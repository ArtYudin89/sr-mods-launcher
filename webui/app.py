#!/usr/bin/env python3
"""SR Mods Launcher — новый интерфейс (pywebview).

Тонкая обёртка: весь движок остаётся в launcher_core (сеть/HF/манифесты/merge/
роутинг). Здесь — мост между HTML-фронтом (web/) и ядром: класс Api, методы
которого вызываются из JavaScript, плюс перенесённая из старого GUI «склейка»
(конфиг, профили, пути, сборка дерева, установка, ModCFG).

Запуск:  python webui/app.py
Старый tkinter-лаунчер (launcher.py) остаётся рабочим до полной готовности нового.
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import webview

# Пути: в dev — относительно репозитория; в frozen (.exe) — ресурсы из бандла
# (_MEIPASS), а конфиг/профили/Mods-фолбэк рядом с .exe (туда можно писать).
FROZEN = getattr(sys, 'frozen', False)
if FROZEN:
    BUNDLE = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    ROOT = Path(sys.executable).resolve().parent          # рядом с exe (запись)
else:
    BUNDLE = Path(__file__).resolve().parent
    ROOT = Path(__file__).resolve().parent.parent         # корень репозитория
    sys.path.insert(0, str(ROOT))                         # чтобы найти launcher_core
import launcher_core as core  # noqa: E402

WEB_DIR = BUNDLE / 'web'
CONFIG_FILE = ROOT / 'launcher_config.json'
PROFILES_DIR = ROOT / 'profiles'

# Встроенный токен/репо для «тестовых» сборок RWT (release with tests). Файл
# embedded_secrets.py НЕ коммитится (.gitignore) и кладётся только при RWT-сборке
# для тестеров; если его нет/пуст — обычная сборка (токен вводится вручную).
try:
    import embedded_secrets as _sec  # type: ignore
    EMBEDDED_TOKEN = (getattr(_sec, 'GITHUB_TOKEN', '') or '').strip()
    EMBEDDED_REPO = (getattr(_sec, 'REPO', '') or '').strip()
except Exception:
    EMBEDDED_TOKEN = EMBEDDED_REPO = ''
IS_RWT = bool(EMBEDDED_TOKEN)

DEFAULT_CONFIG = {
    'last_profile': 'default', 'profiles': ['default'], 'github_token': '',
    'repo': EMBEDDED_REPO or 'ArtYudin89/sr-mods-aggregator',
    'tree_mode': 'folder', 'theme': 'dark', 'log_verbose': False,
}


# Фаза 4: подписи статусов плана обновления + опции решений конфликтов.
STATUS_LABELS = {
    'add': '➕ добавить (новый файл)',
    'update': '⬆ обновить (вы не меняли)',
    'automerge': '🔀 авто-слить ваши правки',
    'player_only': '✋ оставить вашу правку (мод не менял)',
    'unchanged': '· без изменений',
    'deleted_clean': '🗑 удалить (нет в новой версии)',
    'conflict_text': '⚠ КОНФЛИКТ текста',
    'conflict_binary': '⚠ КОНФЛИКТ бинарного файла',
    'conflict_deleted': '⚠ КОНФЛИКТ: удалён в новой версии, вы правили',
}
CONFLICT_OPTIONS = {
    'conflict_text': [('оставить мой', 'mine'), ('взять новый', 'theirs'),
                      ('сохранить оба (.srnew)', 'both')],
    'conflict_binary': [('оставить мой', 'mine'), ('взять новый', 'theirs'),
                        ('сохранить оба (.srnew)', 'both')],
    'conflict_deleted': [('оставить мой', 'keep'), ('удалить', 'delete')],
}
ALL_CAMP = '★ весь лагерь'
ALL_PACK = '★ весь пак'

# Ссылку на нативное окно держим в МОДУЛЬНОЙ глобальной, НЕ как атрибут Api:
# pywebview сериализует js_api-объект, и .NET-окно в нём уводит в бесконечную
# рекурсию (...AccessibilityObject.Bounds.Empty.Empty...) → зависание во frozen.
_WINDOW = None


def fmt_date(iso):
    if not iso:
        return ''
    try:
        return datetime.fromisoformat(iso).strftime('%d.%m.%Y %H:%M')
    except Exception:
        return iso


def load_json(path, default):
    try:
        if Path(path).exists():
            d = json.loads(Path(path).read_text(encoding='utf-8'))
            return {**default, **d} if isinstance(default, dict) else d
    except Exception as e:
        print('load error', path, e)
    return dict(default) if isinstance(default, dict) else default


class Api:
    """Мост JS → Python. Каждый публичный метод доступен из window.pywebview.api."""

    def __init__(self):
        self.config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        # синхронизировать флаг подробного лога ядра с конфигом (по умолч. краткий);
        # без этого core.LOG_VERBOSE=True по умолчанию давал построчный лог при снятой галочке
        core.LOG_VERBOSE = bool(self.config.get('log_verbose', False))
        PROFILES_DIR.mkdir(exist_ok=True)
        self.current_profile = self.config.get('last_profile', 'default')
        self.profile = self._load_profile(self.current_profile)
        self.profile['mods'] = []          # очередь — сессионная (как в старом GUI)
        # рантайм-состояние
        self.busy = False
        self._cancel = threading.Event()
        self._catalog_cache = None
        self._packs_cache = None
        self._fixparent = {}
        self._sections = {}
        self._descs = {}
        self._disk_index = None
        self._dl_bytes = 0
        self._dl_lock = threading.Lock()
        self._parts_done = self._parts_total = 0
        self._pack_ctx = ''
        try:
            self._disk_index = core.load_disk_index(self._mods_dir())
        except Exception:
            self._disk_index = None

    # ───────── инфраструктура ─────────
    def _emit(self, event, payload=None):
        """Отправить событие во фронт (window.__emit(event, data))."""
        if not _WINDOW:
            return
        try:
            _WINDOW.evaluate_js(
                f'window.__emit({json.dumps(event)}, {json.dumps(payload)})')
        except Exception:
            pass

    def log(self, msg):
        self._emit('log', str(msg))

    def _save_config(self):
        CONFIG_FILE.write_text(json.dumps(self.config, ensure_ascii=False, indent=2),
                               encoding='utf-8')

    def _load_profile(self, name):
        p = PROFILES_DIR / f'{name}.json'
        default = {'name': name, 'game_path': '', 'mods': [], 'enabled': [],
                   'base': '', 'fork': '', 'created': datetime.now().isoformat()}
        return load_json(p, default)

    def _save_profile(self):
        self.profile['updated'] = datetime.now().isoformat()
        (PROFILES_DIR / f'{self.profile["name"]}.json').write_text(
            json.dumps(self.profile, ensure_ascii=False, indent=2), encoding='utf-8')
        if self.profile['name'] not in self.config['profiles']:
            self.config['profiles'].append(self.profile['name'])
        self._save_config()

    def _token(self):
        return (self.config.get('github_token', '').strip()
                or os.environ.get('GH_TOKEN', '') or EMBEDDED_TOKEN)

    def _repo(self):
        return (self.config.get('repo', '').strip() or EMBEDDED_REPO
                or 'ArtYudin89/sr-mods-aggregator')

    def _game_root(self):
        gp = (self.profile.get('game_path') or '').strip()
        if not gp:
            return None
        p = Path(gp)
        if p.is_file():
            return p.parent
        if p.is_dir():
            return p
        return None

    def _mods_dir(self):
        # Папку Mods создаём ТОЛЬКО когда задан реальный путь игры — иначе вернём
        # путь-заглушку рядом с exe, НО НЕ создаём её (чтобы не плодить пустую Mods
        # около лаунчера до выбора игры). Читающие вызовы переживают отсутствие папки.
        root = self._game_root()
        if root is None:
            return ROOT / 'Mods'
        d = root / 'Mods'
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return d

    def _require_game(self):
        """None, если путь игры задан; иначе текст ошибки для UI."""
        if self._game_root() is None:
            return 'Сначала укажите папку игры (где лежит Rangers.exe).'
        return None

    def _autofind_game(self):
        for drive in ('C:', 'D:', 'E:', 'F:'):
            for c in (
                Path(f'{drive}/Program Files (x86)/Steam/steamapps/common/Space Rangers HD A War Apart'),
                Path(f'{drive}/Program Files/Steam/steamapps/common/Space Rangers HD A War Apart'),
                Path(f'{drive}/SteamLibrary/steamapps/common/Space Rangers HD A War Apart'),
                Path(f'{drive}/Games/Space Rangers HD A War Apart'),
            ):
                if c.exists():
                    return str(c)
        return ''

    def should_cancel(self):
        return self._cancel.is_set()

    # ───────── состояние для фронта ─────────
    def get_state(self):
        """Полное начальное состояние: настройки, профили, тема."""
        if not self.profile.get('game_path'):
            self.profile['game_path'] = self._autofind_game()
        return {
            'theme': self.config.get('theme', 'dark'),
            'tree_mode': self.config.get('tree_mode', 'folder'),
            'log_verbose': self.config.get('log_verbose', False),
            'profiles': self.config.get('profiles', ['default']),
            'current_profile': self.current_profile,
            'game_path': self.profile.get('game_path', ''),
            'base': self.profile.get('base', ''),
            'repo': self._repo(),
            'has_token': bool(self._token()),
            'is_rwt': IS_RWT,
            'busy': self.busy,
        }

    # ───────── сборка дерева ─────────
    def _tree_mode(self):
        return self.config.get('tree_mode', 'folder')

    def _section_of(self, mid):
        if mid not in self._sections:
            p = self._mods_dir() / mid.replace('/', os.sep) / 'ModuleInfo.txt'
            self._sections[mid] = core.read_module_section(p) if p.exists() else ''
        return self._sections[mid]

    def _mi_path(self, mid):
        return self._mods_dir() / mid.replace('/', os.sep) / 'ModuleInfo.txt'

    def _catalog_entry(self, mid):
        """Запись каталога по mid (точно или по короткому имени). None если нет."""
        cat = self._catalog_cache or {}
        if mid in cat:
            return cat[mid]
        leaf = mid.split('/')[-1]
        return next((v for k, v in cat.items() if k.split('/')[-1] == leaf), None)

    def _desc_of(self, mid):
        """Краткое описание мода: с диска (ModuleInfo, кэш), фолбэк на каталог
        (для модов сборки, ещё не установленных). '' если нигде нет."""
        if mid not in self._descs:
            p = self._mi_path(mid)
            card = core.module_card(p) if p.exists() else {}
            self._descs[mid] = card.get('small', '')
        if self._descs[mid]:
            return self._descs[mid]
        ce = self._catalog_entry(mid)               # живой фолбэк (каталог грузится лениво)
        return (ce.get('description') or '') if ce else ''

    def get_mod_info(self, mid):
        """Полная карточка мода для окна (i): из локального ModuleInfo, фолбэк на
        каталог (description/full_description теперь есть и там)."""
        info, mi_ok = {}, False
        if mid:
            p = self._mi_path(mid)
            if p.exists():
                info = core.module_card(p) or {}
                mi_ok = bool(info)
        if not mi_ok and mid:                       # фолбэк из каталога
            ce = self._catalog_entry(mid)
            if ce:
                var = (ce.get('variants') or [{}])[0]
                info = {'name': ce.get('name', ''), 'authors': ce.get('author', ''),
                        'small': ce.get('description', ''),
                        'full': ce.get('full_description', ''),
                        'requires': var.get('depends', []),
                        'conflicts': var.get('conflicts', []),
                        'section': ce.get('section', ''), 'priority': ''}
        if not info:
            return {'ok': False}
        info.setdefault('name', mid.split('/')[-1] if mid else '')
        info['id'] = mid or ''
        info['location'] = (mid or '').replace('/', '\\')   # как в игре: Категория\Имя
        info['installed'] = mi_ok
        return {'ok': True, 'info': info}

    def _mod_group(self, mid):
        folder = mid.split('/')[0] if '/' in mid else None
        if self._tree_mode() == 'section':
            return self._section_of(mid) or folder or 'Не указан'
        return folder

    def _pack_group(self, unit):
        return (self._fixparent or {}).get(unit, unit)

    def _mod_available(self, mid, catalog):
        if not catalog or mid in catalog:
            return mid in catalog
        leaf = mid.split('/')[-1]
        return any(k.split('/')[-1] == leaf for k in catalog)

    def _disk_mods(self):
        mods_dir = self._mods_dir()
        out = {}
        try:
            for mid in core.scan_installed_mods(mods_dir):
                try:
                    p = mods_dir / mid.replace('/', os.sep)
                    out[mid] = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
                except Exception:
                    out[mid] = ''
        except Exception:
            pass
        return out

    def get_tree(self, only_attention=False):
        """Собрать дерево Лагерь→Пак→Мод (диск + набор профиля) как вложенный JSON."""
        try:
            inst_base = core.detect_installed_base(self._mods_dir())
        except Exception:
            inst_base = None
        disk = self._disk_mods()
        try:
            modcfg = set(core.read_modcfg(self._mods_dir()))
        except Exception:
            modcfg = set()
        enabled = set(self.profile.get('enabled', []))
        catalog = self._catalog_cache or {}
        idx_mods = (self._disk_index or {}).get('mods', {})

        def status_of(m, on_disk):
            if m.get('update_available'):
                return ('upd', '⬆ обновление')
            return ('ok', '✅ установлен') if on_disk else ('queued', '➕ добавлен')

        # (camp, pack, kind, label, status_class, status_text, date, iid, mid)
        rows, seen, desc_iids = [], set(), set()
        for idx, m in enumerate(self.profile.get('mods', [])):
            typ, iid = m.get('type'), f'p{idx}'
            if typ == 'desc':
                desc_iids.add(iid)
            if typ == 'unit' and m.get('mod'):
                mid = m['mod']; seen.add(mid)
                on = (mid in disk) or bool(m.get('last_downloaded'))
                sc, st = status_of(m, on)
                rows.append((m.get('camp', 'прочее'), self._mod_group(mid), 'мод',
                             mid.split('/')[-1], sc, st,
                             m.get('last_downloaded') or disk.get(mid, '') if on else '',
                             iid, mid))
            elif typ == 'unit':
                on = bool(m.get('last_downloaded')); sc, st = status_of(m, on)
                rows.append((m.get('camp', 'прочее'), self._pack_group(m.get('unit')),
                             'пак', m.get('name', m.get('unit')), sc, st,
                             m.get('last_downloaded', '') if on else '', iid, ''))
            elif typ == 'camp':
                on = bool(m.get('last_downloaded')); sc, st = status_of(m, on)
                rows.append((m.get('camp', 'прочее'), None, 'лагерь', '★ весь лагерь',
                             sc, st, m.get('last_downloaded', '') if on else '', iid, ''))
            else:
                on = bool(m.get('last_downloaded')); sc, st = status_of(m, on)
                rows.append(('прочее', None, 'форк' if typ == 'desc' else 'zip',
                             m.get('name') or m.get('id') or m.get('url', ''),
                             sc, st, m.get('last_downloaded', '') if on else '', iid, ''))

        camp_disk = inst_base['camp'] if inst_base else 'прочее'
        for mid, ts in sorted(disk.items()):
            if mid in seen:
                continue
            seen.add(mid)
            unknown = (idx_mods.get(mid) or {}).get('status') == 'unknown'
            sc, st = ('unknown', '❓ не в каталоге') if unknown else ('ok', '✅ установлен')
            rows.append((camp_disk, self._mod_group(mid), 'мод',
                         mid.split('/')[-1], sc, st, ts, 'd:' + mid, mid))

        prof_camp = self.profile.get('base') or 'набор профиля'
        for mid in sorted(enabled):
            if mid in seen:
                continue
            avail = self._mod_available(mid, catalog)
            sc, st = (('avail', '📥 доступен') if avail
                      else ('miss', '⚠ недоступен') if catalog
                      else ('load', '… каталог грузится'))
            rows.append((prof_camp, self._mod_group(mid), 'мод', mid.split('/')[-1],
                         sc, st, '', 'e:' + mid, mid))

        # вложенная структура camps → (mods | packs → mods)
        camps = {}

        def camp_obj(c):
            c = c or 'прочее'
            if c not in camps:
                camps[c] = {'label': c, 'kind': 'camp', 'mods': [], 'packs': {}}
            return camps[c]

        for camp, pack, kind, label, sc, st, date, iid, mid in sorted(
                rows, key=lambda x: (x[0] or '', x[1] or '', x[3])):
            if only_attention and sc == 'ok':
                continue
            node = {
                'iid': iid, 'label': label, 'kind': kind,
                'status_class': sc, 'status': st,
                'in_game': bool(mid and mid in modcfg),
                'in_profile': bool(mid and mid in enabled),
                'date': fmt_date(date), 'mid': mid,
                'selectable': bool(iid),
                'folder': (mid.split('/')[0] if mid and '/' in mid else ''),
                'section': (self._section_of(mid) if mid else ''),
                'desc': (self._desc_of(mid) if mid else ''),
                'has_info': bool(mid),
                'mergeable': bool(iid and (iid.startswith('d:') or iid in desc_iids)),
            }
            co = camp_obj(camp)
            if pack and kind == 'мод':
                pk = co['packs'].setdefault(pack, {'label': pack, 'kind': 'pack', 'mods': []})
                pk['mods'].append(node)
            else:
                co['mods'].append(node)

        out = []
        for cname in sorted(camps):
            co = camps[cname]
            packs = [co['packs'][p] for p in sorted(co['packs'])]
            out.append({'label': co['label'], 'kind': 'camp',
                        'mods': co['mods'], 'packs': packs})

        self._lazy_load_catalog()
        return {
            'camps': out,
            'base': ({'camp': inst_base['camp'], 'name': inst_base.get('name', '')}
                     if inst_base else None),
            'queue': self._queue_summary(),
            'tree_mode': self._tree_mode(),
        }

    def _queue_summary(self):
        mods = self.profile.get('mods', [])
        total = sum(self._item_bytes(m) for m in mods)
        return {'count': len(mods), 'size': self._fmt_size(total)}

    def _fmt_size(self, b):
        if not b:
            return ''
        gb = b / (1 << 30)
        return f'{gb:.2f} ГБ' if gb >= 1 else f'{b / (1 << 20):.0f} МБ'

    def _item_bytes(self, m):
        packs = self._packs_cache or {}
        t = m.get('type')
        if t == 'camp':
            return sum(p.get('bytes', 0) for p in packs.values()
                       if p.get('camp') == m.get('camp'))
        if t == 'unit' and not m.get('mod'):
            return packs.get(f"{m.get('camp')}/{m.get('unit')}", {}).get('bytes', 0)
        return 0

    def _lazy_load_catalog(self):
        if self._catalog_cache is not None or getattr(self, '_cat_loading', False):
            return
        self._cat_loading = True

        def bg():
            try:
                self._catalog_cache = core.load_catalog(
                    'descriptors/catalog.json', self._repo(), self._token()) or {}
            except Exception:
                self._catalog_cache = {}
            try:
                pk = core.load_packs('state/packs.json', self._repo(), self._token())
                self._packs_cache = pk
                self._fixparent = {p['name']: p['fix_parent']
                                   for p in pk.values() if p.get('fix_parent')}
            except Exception:
                self._fixparent = {}
            self._cat_loading = False
            self._emit('tree_dirty')        # фронт перезапросит дерево
        threading.Thread(target=bg, daemon=True).start()

    # ───────── настройки/профили ─────────
    def set_theme(self, theme):
        self.config['theme'] = theme
        self._save_config()
        return True

    def set_tree_mode(self, mode):
        self.config['tree_mode'] = mode
        self._save_config()
        return True

    def set_verbose(self, v):
        self.config['log_verbose'] = bool(v)
        core.LOG_VERBOSE = bool(v)
        self._save_config()
        return True

    def save_settings(self, game_path, repo, token, base):
        self.profile['game_path'] = (game_path or '').strip()
        self.profile['base'] = (base or '').strip()
        self.config['repo'] = (repo or '').strip()
        if token is not None:
            self.config['github_token'] = (token or '').strip()
        self._invalidate_remote_cache()
        self._save_profile()
        return self.get_state()

    def _invalidate_remote_cache(self):
        self._catalog_cache = None
        self._packs_cache = None
        self._cat_loading = False

    def switch_profile(self, name):
        if name == self.current_profile:
            return self.get_state()
        self._save_profile()
        self.current_profile = name
        self.config['last_profile'] = name
        self.profile = self._load_profile(name)
        self.profile['mods'] = []
        self._save_config()
        return self.get_state()

    def new_profile(self, name):
        name = (name or '').strip()
        if not name or name in self.config['profiles']:
            return {'ok': False, 'error': 'Профиль с таким именем уже есть.'}
        self.config['profiles'].append(name)
        self.current_profile = name
        self.config['last_profile'] = name
        self.profile = self._load_profile(name)
        self._save_profile()
        return {'ok': True, 'state': self.get_state()}

    def delete_profile(self, name):
        if name == 'default':
            return {'ok': False, 'error': 'Профиль «default» удалить нельзя.'}
        try:
            (PROFILES_DIR / f'{name}.json').unlink(missing_ok=True)
        except Exception:
            pass
        if name in self.config['profiles']:
            self.config['profiles'].remove(name)
        self.current_profile = 'default'
        self.config['last_profile'] = 'default'
        self.profile = self._load_profile('default')
        self._save_config()
        return {'ok': True, 'state': self.get_state()}

    def toggle_enabled(self, mid, on):
        en = self.profile.setdefault('enabled', [])
        if on and mid not in en:
            en.append(mid)
        elif not on and mid in en:
            en.remove(mid)
        self._save_profile()
        return True

    # ───────── ModCFG синхронизация ─────────
    def modcfg_to_profile(self):
        try:
            mods = core.read_modcfg(self._mods_dir())
        except Exception as e:
            return {'ok': False, 'error': str(e)}
        self.profile['enabled'] = mods
        self._save_profile()
        self.log(f'Считано из игры (ModCFG): {len(mods)} подключённых модов.')
        return {'ok': True, 'count': len(mods)}

    def profile_to_modcfg(self):
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        en = list(self.profile.get('enabled', []))
        disk = set(self._disk_mods())
        missing = [m for m in en if m not in disk]
        try:
            core.write_modcfg(self._mods_dir(), en)
        except Exception as e:
            return {'ok': False, 'error': str(e)}
        self.log(f'Записано в игру (ModCFG): {len(en)} модов.'
                 + (f' Не на диске: {len(missing)}.' if missing else ''))
        return {'ok': True, 'count': len(en), 'missing': missing}

    # ───────── действия ─────────
    def launch_game(self):
        root = self._game_root()
        if not root:
            return {'ok': False, 'error': 'Сначала укажите папку игры в настройках.'}
        exe = root / 'Rangers.exe'
        if not exe.exists():
            return {'ok': False, 'error': f'Не найден Rangers.exe в {root}.'}
        try:
            subprocess.Popen([str(exe)], cwd=str(root))
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def open_mods_folder(self):
        try:
            os.startfile(str(self._mods_dir()))  # noqa
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def open_mod_folder(self, mid):
        try:
            p = self._mods_dir() / mid.replace('/', os.sep)
            os.startfile(str(p if p.exists() else self._mods_dir()))  # noqa
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def browse_game(self):
        if not _WINDOW:
            return None
        res = _WINDOW.create_file_dialog(webview.FOLDER_DIALOG)
        if res:
            path = res[0] if isinstance(res, (list, tuple)) else res
            self.profile['game_path'] = path
            self._save_profile()
            return path
        return None

    def remove_pidx(self, indices):
        mods = self.profile.get('mods', [])
        for i in sorted(set(indices), reverse=True):
            if 0 <= i < len(mods):
                mods.pop(i)
        self._save_profile()
        return True

    def add_to_profile(self, mid):
        """Добавить мод из набора профиля в очередь установки (desc-запись)."""
        self.profile.setdefault('mods', []).append(
            {'type': 'desc', 'id': mid, 'name': mid.split('/')[-1], 'repo': self._repo()})
        self._save_profile()
        return True

    def cancel(self):
        self._cancel.set()
        self.log('Отмена запрошена…')
        return True

    # ───────── установка (фоновый поток) ─────────
    def install(self, indices):
        if self.busy:
            return {'ok': False, 'error': 'Уже идёт операция.'}
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        indices = sorted(set(int(i) for i in indices))
        if not indices:
            return {'ok': False, 'error': 'Ничего не выбрано.'}
        self.busy = True
        self._cancel.clear()
        self._emit('op_begin', {'name': 'Установка'})
        threading.Thread(target=self._install_worker, args=(indices,), daemon=True).start()
        return {'ok': True}

    # колбэки прогресса → фронт
    def _progress(self, done, total):
        if total:
            self._emit('progress', {'pct': round(done / total * 100), 'mode': 'pct'})

    def _part_progress(self, done, total):
        self._parts_done, self._parts_total = done, total
        self._emit('progress', {'pct': round(done / total * 100) if total else 0,
                                 'parts': f'{done}/{total}', 'mode': 'parts',
                                 'ctx': self._pack_ctx,
                                 'gb': f'{self._dl_bytes / (1 << 30):.2f}'})

    def _byte_progress(self, delta):
        with self._dl_lock:
            self._dl_bytes += delta

    def _sha_sink(self, rel, target, data):
        pass  # индексация при установке — упрощённо опускаем в первой версии

    def _get_packs(self, tok):
        if self._packs_cache is None:
            self._packs_cache = core.load_packs('state/packs.json', self._repo(), tok) or {}
            self._fixparent = {p['name']: p['fix_parent']
                               for p in self._packs_cache.values() if p.get('fix_parent')}
        return self._packs_cache or {}

    @staticmethod
    def _unit_install_rank(p, packs):
        tier = p.get('tier')
        if tier == 'base':
            return 0
        if tier == 'fixes':
            parent = packs.get(f"{p['camp']}/{p.get('fix_parent', '')}")
            return 3 if (parent and parent.get('tier') == 'base') else 2
        return 1

    def _install_worker(self, indices):
        tok = self._token()
        mods_dir = self._mods_dir()
        self._dl_bytes = 0
        done = []
        try:
            for i in indices:
                if i >= len(self.profile['mods']):
                    continue
                m = self.profile['mods'][i]
                self.log(f'=== {m.get("name", m.get("id", "?"))} ===')
                try:
                    self._install_one(m, mods_dir, tok)
                    m['last_downloaded'] = datetime.now().isoformat()
                    m['update_available'] = False
                    done.append(m)
                    self._save_profile()
                    self._emit('tree_dirty')
                except core.OperationCancelled:
                    raise
                except Exception as e:
                    self.log(f'ОШИБКА {m.get("name", "?")}: {e}')
            self.log('Готово')
            self._finish_install(done, 'Готово')
        except core.OperationCancelled:
            self.log('Установка отменена.')
            self._finish_install(done, 'Отменено')
        except Exception as e:
            self.log(f'ОШИБКА установки: {e}')
            self._finish_install(done, 'Ошибка')

    def _finish_install(self, done, status):
        # убрать успешно установленные из очереди (по id записи)
        done_ids = {id(m) for m in done}
        self.profile['mods'] = [m for m in self.profile.get('mods', [])
                                if id(m) not in done_ids]
        self._save_profile()
        self.busy = False
        self._emit('op_end', {'status': status})
        self._emit('tree_dirty')

    def _install_one(self, m, mods_dir, tok):
        """Установить ОДНУ запись сборки (camp / unit / desc / zip)."""
        if m.get('type') == 'camp':
            packs = self._get_packs(tok)
            units = sorted([p for p in packs.values() if p['camp'] == m['camp']],
                           key=lambda p: (self._unit_install_rank(p, packs),
                                          p.get('load_order', 999)))
            ntot = len(units)
            for k, p in enumerate(units, 1):
                self._pack_ctx = f'пак {k}/{ntot} · {p["name"]}'
                self.log(f'--- {p["name"]} ({p["tier"]}) ---')
                core.reconstruct_unit(
                    m['repo'], p['camp'], p['name'], mods_dir, tok,
                    self._progress, self.log, tmp_dir=ROOT, should_cancel=self.should_cancel,
                    part_cb=self._part_progress, byte_cb=self._byte_progress)
                if p.get('tier') == 'base':
                    self.profile['installed_base'] = p['name']
        elif m.get('type') == 'unit':
            self._pack_ctx = m.get('name', m.get('unit', ''))
            core.reconstruct_unit(
                m['repo'], m['camp'], m['unit'], mods_dir, tok,
                self._progress, self.log, tmp_dir=ROOT, mod=m.get('mod') or None,
                should_cancel=self.should_cancel,
                part_cb=self._part_progress, byte_cb=self._byte_progress)
        elif m.get('type') == 'desc':
            idx = core.load_chunk_index(repo=self._repo(), token=tok)
            desc = core.descriptor_for({'id': m['id']} if not m.get('url') else {'url': m['url']},
                                       self._catalog_cache, self._repo(), tok)
            if desc:
                core.install_descriptor(
                    desc, mods_dir, idx, tok, self._progress, self.log,
                    tmp_dir=ROOT, should_cancel=self.should_cancel,
                    part_cb=self._part_progress, byte_cb=self._byte_progress)
            else:
                self.log('  дескриптор не найден в каталоге')
        else:
            core.install_zip(m['url'], mods_dir, tok, self._progress, self.log,
                             tmp_dir=ROOT, should_cancel=self.should_cancel)

    # ───────── установить всю сборку С ЗАВИСИМОСТЯМИ ─────────
    def install_set_with_deps(self):
        """Установить всю сборку: bulk-записи (лагерь/пак/zip) обычной логикой +
        каталожные моды (desc/unit-мод) через resolve_set (подтянуть Dependence)."""
        if self.busy:
            return {'ok': False, 'error': 'Уже идёт операция.'}
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        if not self.profile.get('mods'):
            return {'ok': False, 'error': 'В сборке пока нет позиций. Нажмите «➕ Добавить мод».'}
        self.busy = True
        self._cancel.clear()
        self._emit('op_begin', {'name': 'Установка сборки'})
        threading.Thread(target=self._resolve_set_worker, daemon=True).start()
        return {'ok': True}

    def _resolve_set_worker(self):
        repo, tok = self._repo(), self._token()
        sels, bulk = [], []
        for m in self.profile.get('mods', []):
            t = m.get('type')
            if t == 'desc':
                sels.append(self._desc_sel(m))
            elif t == 'unit' and m.get('mod'):
                sel = {'id': m['mod']}
                if m.get('camp') and m.get('unit'):
                    sel['source'] = f"{m['camp']}/{m['unit']}"
                sels.append(sel)
            else:
                bulk.append(m)                    # лагерь / весь пак / zip
        plan = None
        if sels:
            self.log('Разрешаю зависимости модов…')
            try:
                cat = core.load_catalog('descriptors/catalog.json', repo, tok)
                plan = core.resolve_set(sels, cat, repo, tok)
            except core.OperationCancelled:
                self.log('Отменено.'); self._finish_set('Отменено'); return
            except Exception as e:
                self.log(f'ОШИБКА резолва зависимостей: {e}'); self._finish_set('Ошибка'); return
            if plan['added_deps']:
                self.log('➕ зависимости (Dependence): ' + ', '.join(plan['added_deps']))
            if plan['missing_deps']:
                self.log('⚠ зависимости не в каталоге: ' + ', '.join(plan['missing_deps']))
            for a, b in plan['conflicts']:
                self.log(f'⚠ КОНФЛИКТ: {a} ⟷ {b}')
        self._pending_set = {'plan': plan, 'bulk': bulk, 'repo': repo, 'tok': tok}
        order = (plan or {}).get('order', [])
        self._emit('deps_confirm', {
            'count': len(order), 'bulk': len(bulk),
            'added_deps': (plan or {}).get('added_deps', []),
            'missing_deps': (plan or {}).get('missing_deps', []),
            'conflicts': [list(p) for p in (plan or {}).get('conflicts', [])],
            'order': list(order)[:60],
        })

    def confirm_install_deps(self):
        ps = getattr(self, '_pending_set', None)
        if not ps:
            return {'ok': False}
        self._pending_set = None
        threading.Thread(target=self._install_set_worker, args=(ps,), daemon=True).start()
        return {'ok': True}

    def cancel_deps(self):
        self._pending_set = None
        self.log('Установка сборки отменена.')
        self._finish_set('Отменено')
        return {'ok': True}

    def _install_set_worker(self, ps):
        repo, tok = ps['repo'], ps['tok']
        mods_dir = self._mods_dir()
        self._dl_bytes = 0
        plan, bulk = ps['plan'], ps['bulk']
        done = []
        try:
            for m in bulk:                        # лагерь/пак/zip — обычной логикой
                if self.should_cancel():
                    raise core.OperationCancelled()
                self.log(f'=== {m.get("name", "?")} ===')
                try:
                    self._install_one(m, mods_dir, tok)
                    m['last_downloaded'] = datetime.now().isoformat()
                    m['update_available'] = False
                    done.append(m)
                    self._save_profile()
                    self._emit('tree_dirty')
                except core.OperationCancelled:
                    raise
                except Exception as e:
                    self.log(f'ОШИБКА {m.get("name", "?")}: {e}')
            if plan and plan.get('order'):        # каталожные моды + зависимости
                idx_url = next((d.get('chunk_index_url') for d in plan['mods'].values()
                                if d.get('chunk_index_url')), None)
                index = core.load_chunk_index(url=idx_url, repo=repo, token=tok)
                self._pack_ctx = 'набор модов'
                core.install_set(plan, mods_dir, index, token=tok, log=self.log,
                                 tmp_dir=ROOT, should_cancel=self.should_cancel,
                                 part_cb=self._part_progress, byte_cb=self._byte_progress)
                now = datetime.now().isoformat()
                for m in self.profile.get('mods', []):
                    mid = m.get('id') or m.get('mod')
                    if mid and mid in plan['mods']:
                        m['installed_version'] = plan['mods'][mid].get('version')
                        m['last_downloaded'] = now
                        m['update_available'] = False
                        done.append(m)
                self.log(f'Набор установлен: {len(plan["order"])} мод(ов) (с зависимостями).')
            self.log('Готово')
            self._finish_set('Готово', done)
        except core.OperationCancelled:
            self.log('Установка отменена.')
            self._finish_set('Отменено', done)
        except Exception as e:
            self.log(f'ОШИБКА установки: {e}')
            self._finish_set('Ошибка', done)

    def _finish_set(self, status, done=None):
        if done:
            done_ids = {id(m) for m in done}
            self.profile['mods'] = [m for m in self.profile.get('mods', [])
                                    if id(m) not in done_ids]
            self._save_profile()
        self.busy = False
        self._emit('op_end', {'status': status})
        self._emit('tree_dirty')

    # ───────── добавление мода в сборку ─────────
    def get_camp_packs(self):
        """{лагерь: [{key,camp,unit,name,tier,load_order}]} для каскада добавления."""
        try:
            packs = self._get_packs(self._token())
            return {'ok': True, 'camps': core.camp_packs(packs)}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def get_unit_mods(self, camp, unit):
        """Список модов пака (для выбора конкретного мода)."""
        try:
            mods = core.list_unit_mods(self._repo(), camp, unit, self._token())
            return {'ok': True, 'mods': mods}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def add_mod(self, payload):
        """Добавить запись в сборку. payload:
        {mode:'src'|'fork', camp, pack:{camp,unit,name}|None, mod:'', url:''}"""
        repo = self._repo()
        if payload.get('mode') == 'fork':
            url = (payload.get('url') or '').strip()
            if not url:
                return {'ok': False, 'error': 'Укажите ссылку на дескриптор форка.'}
            nm = url.rsplit('/', 1)[-1].replace('.json', '') or 'форк'
            self.profile.setdefault('mods', []).append({
                'type': 'desc', 'name': nm, 'repo': repo,
                'catalog': 'descriptors/catalog.json', 'id': '', 'source': '',
                'url': url})
            self._save_profile()
            return {'ok': True}
        camp = (payload.get('camp') or '').strip()
        if not camp:
            return {'ok': False, 'error': 'Выберите лагерь.'}
        pack = payload.get('pack')
        if not pack:                                   # весь лагерь
            self.profile.setdefault('mods', []).append({
                'type': 'camp', 'camp': camp, 'repo': repo,
                'name': f'{camp} — весь лагерь'})
            self._save_profile()
            return {'ok': True}
        mod = {'type': 'unit', 'repo': repo, 'camp': pack['camp'], 'unit': pack['unit'],
               'name': pack['name'], 'mod': ''}
        msel = (payload.get('mod') or '').strip()
        if msel and msel != ALL_PACK:
            mod['mod'] = msel
            mod['name'] = msel
        self.profile.setdefault('mods', []).append(mod)
        self._save_profile()
        return {'ok': True}

    # ───────── обновление с сохранением правок (Фаза 4) ─────────
    def _desc_sel(self, m):
        if m.get('url'):
            return {'url': m['url']}
        sel = {'id': m['id']}
        if m.get('source'):
            sel['source'] = m['source']
        return sel

    def start_merge(self, iids):
        if self.busy:
            return {'ok': False, 'error': 'Уже идёт операция.'}
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        targets, skipped = [], 0
        for iid in iids:
            if iid.startswith('d:'):
                targets.append(('disk', iid[2:]))
            elif iid[1:].isdigit() and self.profile['mods'][int(iid[1:])].get('type') == 'desc':
                targets.append(('profile', int(iid[1:])))
            else:
                skipped += 1
        if not targets:
            return {'ok': False, 'error':
                    'Выберите мод из сборки (добавленный из каталога/по ссылке) '
                    'или мод из «Установлено в игре». Паки/лагеря обновляются кнопкой «Установить».'}
        self.busy = True
        self._cancel.clear()
        self._merge_queue = targets
        self._emit('op_begin', {'name': 'Обновление'})
        if skipped:
            self.log(f'Пропущено {skipped} (паки/лагеря — через «Установить»).')
        threading.Thread(target=self._merge_next, daemon=True).start()
        return {'ok': True}

    def _merge_next(self):
        if self.should_cancel():
            self._merge_queue = []
        if not getattr(self, '_merge_queue', None):
            self.busy = False
            self._emit('op_end', {'status': 'Готово'})
            self._emit('tree_dirty')
            return
        kind, ref = self._merge_queue.pop(0)
        if kind == 'profile':
            self._plan_merge_profile(ref)
        else:
            self._plan_merge_disk(ref)

    def _emit_plan_or_skip(self, target, desc, plan, index):
        s = plan['summary']
        actionable = (sum(s.get(k, 0) for k in
                          ('add', 'update', 'automerge', 'deleted_clean'))
                      + s.get('conflicts', 0))
        if actionable == 0:
            self.log(f'{plan.get("id")}: обновлять нечего (всё актуально / только ваши правки).')
            self._merge_next()
            return
        self._pending_merge = {'target': target, 'desc': desc, 'plan': plan, 'index': index}
        self._emit('merge_plan', self._serialize_plan(plan))

    def _serialize_plan(self, plan):
        groups = {}
        for r in plan['actions']:
            groups.setdefault(r['status'], []).append(core.install_relpath(r['relpath']))
        conflicts = []
        for r in plan['actions']:
            if r['status'] in CONFLICT_OPTIONS:
                conflicts.append({
                    'relpath': r['relpath'],
                    'display': core.install_relpath(r['relpath']),
                    'status': r['status'],
                    'options': [{'label': l, 'code': c} for l, c in CONFLICT_OPTIONS[r['status']]],
                    'default': core.DECISION_DEFAULTS.get(r['status']),
                })
        return {
            'id': plan.get('id'), 'version_old': plan.get('version_old'),
            'version_new': plan.get('version_new'), 'summary': plan['summary'],
            'groups': groups, 'labels': STATUS_LABELS, 'conflicts': conflicts,
        }

    def _plan_merge_profile(self, i):
        m = self.profile['mods'][i]
        repo, tok = self._repo(), self._token()
        mods_dir = self._mods_dir()
        self.log(f'=== Обновление: {m.get("name", m.get("id", "?"))} ===')
        try:
            cat = {}
            if not m.get('url'):
                cat = core.load_catalog(m.get('catalog', 'descriptors/catalog.json'), repo, tok)
            desc = core.descriptor_for(self._desc_sel(m), cat, repo, tok)
            if not desc:
                self.log('Не удалось получить дескриптор мода (нет в каталоге?).')
                self._merge_next(); return
            snap = core.load_install_snapshot(mods_dir, desc.get('id'))
            if snap is None:
                self.log('Нет снимка прошлой установки — сначала установите мод лаунчером один раз.')
                self._merge_next(); return
            index = core.load_chunk_index(desc=desc, repo=repo, token=tok)
            plan = core.plan_update_merge(desc, mods_dir, index, token=tok, log=self.log,
                                          snapshot=snap, tmp_dir=ROOT,
                                          progress_cb=self._progress,
                                          should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Планирование отменено.'); self._merge_next(); return
        except Exception as e:
            self.log(f'ОШИБКА планирования: {e}'); self._merge_next(); return
        self._emit_plan_or_skip(('profile', i), desc, plan, index)

    def _plan_merge_disk(self, mid):
        repo, tok = self._repo(), self._token()
        mods_dir = self._mods_dir()
        self.log(f'=== Обновление дискового мода: {mid} ===')
        try:
            cat = core.load_catalog('descriptors/catalog.json', repo, tok)
            try:
                ib = core.detect_installed_base(mods_dir)
            except Exception:
                ib = None
            prefer = ib['camp'] if ib else None
            self.log('Подбираю вариант мода по файлам на диске…')
            desc, info = core.pick_disk_variant(cat, mid, mods_dir, repo, tok,
                                                prefer_camp=prefer, log=self.log,
                                                should_cancel=self.should_cancel)
            if not desc:
                self.log(f'Мод {mid} не найден в каталоге — обновить нечем.')
                self._merge_next(); return
            self.log(f'Вариант: {info["source"]} (совпало {info["match"]}/{info["cover"]} '
                     f'из {info["total"]})')
            snap = core.load_install_snapshot(mods_dir, desc.get('id'))
            index = core.load_chunk_index(desc=desc, repo=repo, token=tok)
            plan = core.plan_update_merge(desc, mods_dir, index, token=tok, log=self.log,
                                          snapshot=snap, tmp_dir=ROOT,
                                          progress_cb=self._progress,
                                          should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Планирование отменено.'); self._merge_next(); return
        except Exception as e:
            self.log(f'ОШИБКА планирования: {e}'); self._merge_next(); return
        self._emit_plan_or_skip(('disk', mid), desc, plan, index)

    def apply_merge(self, decisions):
        pm = getattr(self, '_pending_merge', None)
        if not pm:
            return {'ok': False, 'error': 'Нет плана для применения.'}
        self._pending_merge = None
        threading.Thread(target=self._apply_merge_worker, args=(pm, decisions or {}),
                         daemon=True).start()
        return {'ok': True}

    def merge_skip(self):
        pm = getattr(self, '_pending_merge', None)
        self._pending_merge = None
        if pm:
            self.log(f'{pm["plan"].get("id")}: пропущено (изменения не применялись).')
        self._merge_next()
        return {'ok': True}

    def _apply_merge_worker(self, pm, decisions):
        mods_dir = self._mods_dir()
        desc, plan, index, target = pm['desc'], pm['plan'], pm['index'], pm['target']
        try:
            stats = core.apply_update_plan(desc, plan, decisions, mods_dir, index,
                                           token=self._token(), log=self.log,
                                           tmp_dir=ROOT, progress_cb=self._progress,
                                           should_cancel=self.should_cancel)
            self.log(f'Применено: {stats}')
            if target[0] == 'profile':
                m = self.profile['mods'][target[1]]
                m['installed_version'] = plan.get('version_new')
                m['update_available'] = False
                self._save_profile()
        except core.OperationCancelled:
            self.log('Применение отменено (часть файлов могла записаться).')
        except Exception as e:
            self.log(f'ОШИБКА применения: {e}')
        self._merge_next()

    # ───────── индексация дисковых модов ─────────
    def reindex(self):
        if self.busy:
            return {'ok': False, 'error': 'Уже идёт операция.'}
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        self.busy = True
        self._cancel.clear()
        self._emit('op_begin', {'name': 'Индексация'})
        threading.Thread(target=self._reindex_worker, daemon=True).start()
        return {'ok': True}

    def _reindex_worker(self):
        mods_dir = self._mods_dir()
        try:
            cat = self._catalog_cache
            if not cat:
                self.log('Загрузка каталога для классификации…')
                try:
                    cat = core.load_catalog('descriptors/catalog.json', self._repo(),
                                            self._token()) or {}
                    self._catalog_cache = cat
                except core.OperationCancelled:
                    raise
                except Exception as e:
                    self.log(f'[!] каталог не загружен ({e}) — моды будут «не в каталоге»')
                    cat = {}
            self.log('Индексирую моды на диске…')
            idx = core.index_disk_mods(mods_dir, cat, prev_index=self._disk_index,
                                       log=self.log, progress_cb=self._progress,
                                       should_cancel=self.should_cancel)
            core.save_disk_index(mods_dir, idx)
            self._disk_index = idx
            self.config['skip_index_offer'] = True
            self._save_config()
            base = idx.get('base') or {}
            self.log(f'Индексация готова: всего {idx["count"]}, знакомых каталогу '
                     f'{idx["known"]}, не в каталоге {idx["unknown"]}. '
                     f'База: {base.get("camp", "?")}.')
            self.busy = False
            self._emit('op_end', {'status': 'Индексация готова'})
            self._emit('tree_dirty')
        except core.OperationCancelled:
            self.log('Индексация отменена.')
            self.busy = False
            self._emit('op_end', {'status': 'Отменено'})
        except Exception as e:
            self.log(f'ОШИБКА индексации: {e}')
            self.busy = False
            self._emit('op_end', {'status': 'Ошибка'})

    # ───────── очистка папки Mods ─────────
    def mods_info(self):
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        d = self._mods_dir()
        n = sum(1 for p in d.rglob('*') if p.is_file()) if d.exists() else 0
        return {'ok': True, 'count': n, 'path': str(d)}

    def clear_mods(self):
        if self.busy:
            return {'ok': False, 'error': 'Уже идёт операция.'}
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        import shutil
        d = self._mods_dir()
        if not d.exists():
            return {'ok': True, 'removed': 0}
        n = sum(1 for p in d.rglob('*') if p.is_file())
        for it in d.iterdir():
            if it.is_dir():
                shutil.rmtree(it, ignore_errors=True)
            else:
                try:
                    it.unlink()
                except Exception:
                    pass
        for m in self.profile.get('mods', []):
            m['last_downloaded'] = None
        self._save_profile()
        self.log(f'Очищено: {n} файлов из {d}')
        self._emit('tree_dirty')
        return {'ok': True, 'removed': n}

    # ───────── совместимость (показ) ─────────
    def check_compat(self):
        """Отчёт о совместимости для модалки: список {level, text}. level —
        ok|warn|info. Собирается из структурной проверки паков (check_pack_
        compatibility возвращает СЛОВАРЬ, не список!) + проверки зависимостей с диска."""
        tok = self._token()
        try:
            packs = self._get_packs(tok)
        except Exception:
            packs = {}
        units = [f'{m["camp"]}/{m["unit"]}' for m in self.profile.get('mods', [])
                 if m.get('type') == 'unit' and m.get('unit')]
        try:
            base = core.detect_installed_base(self._mods_dir())
        except Exception:
            base = None
        base_name = base.get('base') if base else None      # detect_* отдаёт ключ 'base'
        try:
            rep = core.check_pack_compatibility(units, packs, installed_base=base_name) or {}
        except Exception as e:
            rep = {}
            self.log(f'Совместимость: ошибка проверки паков ({e})')
        try:
            deps = core.check_disk_dependencies(self._mods_dir()) or []
        except Exception:
            deps = []
        items = []
        nm = lambda u: (packs.get(u) or {}).get('name', u)
        if base_name:
            items.append({'level': 'ok', 'text': f'База установлена: {base_name}'})
        if rep.get('missing_base'):
            items.append({'level': 'warn',
                          'text': 'Не выбрана база (нужна для модов/фиксов) — добавьте базовый пак.'})
        if rep.get('base_conflict'):
            names = ', '.join(nm(b) for b in rep.get('bases', []))
            items.append({'level': 'warn', 'text': f'В сборке несколько баз ({names}) — оставьте одну.'})
        if rep.get('save_warning'):
            items.append({'level': 'warn', 'text': rep['save_warning']})
        for fix, parent in rep.get('fix_orphans', []):
            items.append({'level': 'warn',
                          'text': f'Фикс «{nm(fix)}» требует родительский пак «{parent}», которого нет в сборке.'})
        if rep.get('mandatory'):
            names = ', '.join(nm(u) for u in rep['mandatory'])
            items.append({'level': 'info', 'text': f'Обязательны к обновлению (база/фикс): {names}'})
        for d in deps:
            items.append({'level': 'warn',
                          'text': f'Моду «{d.get("name", d.get("mod"))}» не хватает: {", ".join(d.get("missing", []))}'})
        if not items:
            items.append({'level': 'ok', 'text': 'Проблем не найдено.'})
        return {'ok': True, 'items': items, 'base': base_name}

    def refresh_remote(self):
        """Сбросить кэши каталога/packs/описаний и перезагрузить с GitHub (кнопка ⟳).
        Каталог тянется заново → видны свежие данные после пуша агрегатора."""
        self._catalog_cache = None
        self._packs_cache = None
        self._descs = {}
        self._sections = {}
        self._lazy_load_catalog()
        return {'ok': True}


def main():
    global _WINDOW
    api = Api()
    index = WEB_DIR / 'index.html'
    _WINDOW = webview.create_window(
        'SR Mods Launcher', str(index), js_api=api,
        width=1180, height=780, min_size=(960, 640))
    webview.start(debug=('--debug' in sys.argv))


if __name__ == '__main__':
    main()
