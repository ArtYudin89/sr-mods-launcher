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


def _user_documents():
    """Реальная папка «Документы» пользователя (учитывает локализацию и перенос в
    OneDrive — так же, как их видит сама игра). Фолбэк — ~/Documents."""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as k:
            val, _ = winreg.QueryValueEx(k, 'Personal')
            p = Path(os.path.expandvars(val))
            if str(p):
                return p
    except Exception:
        pass
    return Path(os.path.expanduser('~')) / 'Documents'


# Данные лаунчера (конфиг, профили) — в Документы\SpaceRangersHD\Launcher, рядом с
# сейвами/логами игры (а не возле .exe, который может лежать в Program Files без прав
# на запись). DATA_DIR создаётся; при первом запуске переносим старые файлы из папки exe.
DATA_DIR = _user_documents() / 'SpaceRangersHD' / 'Launcher'
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = ROOT                                   # крайний фолбэк — рядом с exe
CONFIG_FILE = DATA_DIR / 'launcher_config.json'
PROFILES_DIR = DATA_DIR / 'profiles'


def _migrate_legacy_data():
    """Один раз перенести старые launcher_config.json/profiles из папки рядом с exe
    (ROOT) в новый DATA_DIR, если в новом месте их ещё нет."""
    if DATA_DIR == ROOT:
        return
    try:
        import shutil
        old_cfg = ROOT / 'launcher_config.json'
        if old_cfg.exists() and not CONFIG_FILE.exists():
            shutil.copy2(old_cfg, CONFIG_FILE)
        old_prof = ROOT / 'profiles'
        if old_prof.is_dir() and not PROFILES_DIR.exists():
            shutil.copytree(old_prof, PROFILES_DIR)
    except Exception as e:
        print('migrate legacy data error:', e)


_migrate_legacy_data()

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

# Версия лаунчера. Проверка самообновления сравнивает её с state/launcher_release.json
# из репозитория ({version, url?, notes?}). url можно оставить пустым — тогда показ без
# ссылки на скачивание (просто «доступна новая версия»).
# ВНИМАНИЕ: при релизе выставить реальный следующий номер (текущий публичный > 0.13.1).
LAUNCHER_VERSION = '0.15.0'
RELEASE_REF = 'state/launcher_release.json'


def _ver_tuple(v):
    out = []
    for part in str(v or '').split('.'):
        num = ''.join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)

DEFAULT_CONFIG = {
    'last_profile': 'default', 'profiles': ['default'], 'github_token': '',
    'repo': EMBEDDED_REPO or 'ArtYudin89/sr-mods-aggregator',
    # Дополнительные репозитории-форки, накладываются ПОВЕРХ основного по приоритету
    # (первый = высший). Каждый: {'repo':'owner/name', 'token':''}. Форк не добавляет
    # новые моды (мод обязан быть в основном), а переопределяет/дополняет ФАЙЛЫ модов.
    'forks': [],
    # по умолчанию (при первом запуске) — группировка «По разделам» и имена «как в игре»;
    # сохранённый конфиг пользователя накладывается поверх и эти значения не трогает.
    'tree_mode': 'section', 'name_mode': 'module', 'theme': 'dark', 'log_verbose': False,
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

# Базовые моды, поставляемые с игрой (инсталлятор кладёт их в Mods и сохраняет при
# переустановке через TempStorageFolder). При «Очистить Mods» их НЕ удаляем. Пути —
# относительно папки Mods, в нижнем регистре (сравнение без учёта регистра, posix).
BASE_GAME_KEEP = {
    'modcfg.txt',
    'tweaks/german', 'tweaks/spanish',
    'tweaks/leodomikshipsupdate15', 'tweaks/leodomikshipsupdate30',
    'tweaks/sr2loadingscreen', 'tweaks/sr2pqueststyle',
}


def _is_base_game_path(rel):
    """rel — путь относительно Mods (posix). True, если это базовый мод игры, его
    содержимое или родительская папка базового мода (напр. сам каталог Tweaks)."""
    rel = rel.replace('\\', '/').lower()
    for prot in BASE_GAME_KEEP:
        if rel == prot or rel.startswith(prot + '/'):
            return True                       # сам базовый мод или его файлы
        if prot.startswith(rel + '/'):
            return True                       # родительская папка (Tweaks) — не сносим
    return False

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
        self._camps_idx = None             # (by_base, by_leaf) -> множества лагерей мода
        self._cat_by_repo = {}             # repo -> catalog (кэш каталогов форков)
        self._idx_by_repo = {}             # repo -> chunk-index (кэш индексов форков)
        self._fork_man_cache = {}          # (repo,camp,unit,which) -> манифест форка
        self._pub_cache = {}               # camp -> {install_rel: sha} (опубликованные файлы)
        self._pub_cache_all = None         # [(source, {install_rel: sha})] по ВСЕМ лагерям
        self._updates = {}                 # mid -> {changed, added} (найденные обновления)
        self._fixparent = {}
        self._sections = {}
        self._names = {}
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

    # ───────── форки (дополнительные репозитории) ─────────
    def _forks(self):
        """Список форков из конфига: [{'repo','token'}], в порядке приоритета."""
        out = []
        for f in self.config.get('forks', []) or []:
            repo = (f.get('repo') or '').strip()
            if repo:
                out.append({'repo': repo, 'token': (f.get('token') or '').strip() or None})
        return out

    def _sources(self):
        """Источники в порядке приоритета (ВЫСШИЙ первым): форки + основной последним.
        Основной задаёт, какие моды вообще существуют; форки переопределяют файлы."""
        return self._forks() + [{'repo': self._repo(), 'token': self._token()}]

    def _catalog_for(self, repo, token):
        """Каталог конкретного репо (с кэшем). Для основного — общий self._catalog_cache."""
        if repo == self._repo():
            if self._catalog_cache is None:
                self._catalog_cache = core.load_catalog(
                    'descriptors/catalog.json', repo, token) or {}
            return self._catalog_cache
        if repo not in self._cat_by_repo:
            try:
                self._cat_by_repo[repo] = core.load_catalog(
                    'descriptors/catalog.json', repo, token) or {}
            except Exception as e:
                self.log(f'⚠ форк {repo}: каталог не загружен ({e})')
                self._cat_by_repo[repo] = {}
        return self._cat_by_repo[repo]

    def _index_for(self, repo, token, desc=None):
        """chunk-index конкретного репо (с кэшем)."""
        if repo not in self._idx_by_repo:
            self._idx_by_repo[repo] = core.load_chunk_index(
                desc=desc, repo=repo, token=token)
        return self._idx_by_repo[repo]

    def _overlay(self, mod_id, source=None):
        """Эффективный дескриптор мода с наложением форков + объединённый индекс.
        Мод обязан быть в ОСНОВНОМ каталоге (форк не добавляет новые моды) — иначе
        (None, None). Без форков эквивалентно обычной установке по дескриптору."""
        sources = self._sources()
        main = sources[-1]
        main_cat = self._catalog_for(main['repo'], main['token'])
        if mod_id not in main_cat:
            return None, None              # нет в основном -> не ставим
        descs, indexes = [], []
        for src in sources:
            repo, tok = src['repo'], src['token']
            cat = main_cat if src is main else self._catalog_for(repo, tok)
            if mod_id not in cat:
                continue                   # этот репо не содержит мод -> пропустить
            sel = {'id': mod_id}
            if src is main and source:
                sel['source'] = source
            try:
                d = core.descriptor_for(sel, cat, repo, tok)
            except Exception as e:
                self.log(f'⚠ {repo}: дескриптор {mod_id} не загружен ({e})')
                d = None
            if not d:
                continue
            descs.append(d)
            try:
                indexes.append(self._index_for(repo, tok, desc=d))
            except Exception as e:
                self.log(f'⚠ {repo}: индекс частей не загружен ({e})')
        if not descs:
            return None, None
        return core.merge_descriptors(descs), core.merge_chunk_indexes(indexes)

    def _overlay_plan(self, plan):
        """Наложить форки на каждый мод плана набора (resolve_set). Мутирует
        plan['mods'] эффективными дескрипторами, возвращает объединённый chunk-index.
        Если форков нет — None (вызывающий грузит индекс как раньше)."""
        if not self._forks():
            return None
        indexes = []
        for mid, d in list(plan['mods'].items()):
            odesc, oidx = self._overlay(mid, d.get('source'))
            if odesc:
                plan['mods'][mid] = odesc
                if oidx:
                    indexes.append(oidx)
        return core.merge_chunk_indexes(indexes) if indexes else None

    def _overlay_theirs(self, desc, source, allow=True):
        """Для merge: «новая версия» мода с наложением форков. -> (desc, index).
        Без форков (или allow=False, напр. форк-по-URL) — исходный desc + его индекс."""
        if allow and self._forks() and desc.get('id'):
            o_desc, o_index = self._overlay(desc.get('id'), source)
            if o_desc:
                return o_desc, o_index
        return desc, core.load_chunk_index(desc=desc, repo=self._repo(), token=self._token())

    def _fork_manifest(self, repo, tok, camp, unit, which):
        """Манифест форка для пака camp/unit (which='code'|'assets'). {} если нет."""
        key = (repo, camp, unit, which)
        if key not in self._fork_man_cache:
            path = f'mods/{camp}/{unit}/{which}.manifest.json'
            try:
                self._fork_man_cache[key] = core._load_manifest(repo, path, tok) or {}
            except Exception:
                self._fork_man_cache[key] = {}
        return self._fork_man_cache[key]

    def _fork_unit_overlay(self, camp, unit):
        """Наложение форков на пак camp/unit для bulk-установки. Форк должен держать
        файлы под тем же путём пака (mods/camp/unit/*.manifest.json). Возвращает
        (fork_files {relpath:(sha,kind)}, fork_index) или (None, None) если форков/
        совпадений нет. Приоритет: высший форк выигрывает по совпавшему пути."""
        forks = self._forks()
        if not forks:
            return None, None
        merged = {}
        indexes = []
        for src in forks:                          # высший приоритет первым
            repo, tok = src['repo'], src['token']
            cm = self._fork_manifest(repo, tok, camp, unit, 'code')
            am = self._fork_manifest(repo, tok, camp, unit, 'assets')
            if not cm and not am:
                continue
            for kind, man in (('code', cm), ('asset', am)):
                for rp, meta in (man or {}).items():
                    if rp not in merged:           # высший приоритет уже занял путь
                        merged[rp] = (meta['sha256'], kind)
            try:
                indexes.append(self._index_for(repo, tok))
            except Exception as e:
                self.log(f'⚠ форк {repo}: индекс частей не загружен ({e})')
        if not merged:
            return None, None
        return merged, core.merge_chunk_indexes(indexes)

    def _unit_maps(self, camp):
        """Список (метка_юнита, {install_rel: sha}) для лагеря (его юниты + shared,
        с наложением форков). Кэш на сессию. По юнитам (а не плоско) — чтобы детект
        мог подобрать лучший вариант мода, как делает обновление (pick_disk_variant)."""
        if camp in self._pub_cache:
            return self._pub_cache[camp]
        repo, tok = self._repo(), self._token()
        packs = self._get_packs(tok)
        units = [p for p in packs.values()
                 if p.get('camp') == camp or p.get('camp') == 'shared']
        out = []
        for p in units:
            if self.should_cancel():
                raise core.OperationCancelled()
            c, u = p.get('camp'), p.get('name')
            try:
                cm = core._load_manifest(repo, f'mods/{c}/{u}/code.manifest.json', tok)
            except Exception:
                cm = {}
            try:
                am = core._load_manifest(repo, f'mods/{c}/{u}/assets.manifest.json', tok)
            except Exception:
                am = {}
            ff, _fidx = self._fork_unit_overlay(c, u)
            fmap = core.published_files(cm, am, ff)
            if fmap:
                out.append((f'{c}/{u}', fmap))
        self._pub_cache[camp] = out
        return out

    def _all_unit_maps(self):
        """Список (source='camp/unit', {install_rel: sha}) по ВСЕМ лагерям (+форки).
        Кэш на сессию. Детект ОБЯЗАН выбирать вариант среди ВСЕХ источников — как
        обновление (pick_disk_variant). Иначе кросс-лагерные моды вечно «нуждаются в
        обновлении»: genuine Shu* живут в universe/original, а redux_base несёт Pol*-
        контент под теми же путями ShusRangers/* → сверка с базовым лагерем всегда
        расходится, а обновление пишет другой вариант → они никогда не сходятся."""
        if self._pub_cache_all is not None:
            return self._pub_cache_all
        repo, tok = self._repo(), self._token()
        packs = self._get_packs(tok)
        out = []
        for p in packs.values():
            if self.should_cancel():
                raise core.OperationCancelled()
            c, u = p.get('camp'), p.get('name')
            try:
                cm = core._load_manifest(repo, f'mods/{c}/{u}/code.manifest.json', tok)
            except Exception:
                cm = {}
            try:
                am = core._load_manifest(repo, f'mods/{c}/{u}/assets.manifest.json', tok)
            except Exception:
                am = {}
            ff, _fidx = self._fork_unit_overlay(c, u)
            fmap = core.published_files(cm, am, ff)
            if fmap:
                out.append((f'{c}/{u}', fmap))
        self._pub_cache_all = out
        return out

    @staticmethod
    def _best_unit_files(mid, unit_maps, disk, allowed_sources=None, prefer_camp=None):
        """Файлы мода mid из источника, лучше всего совпадающего с диском. Критерий
        ИДЕНТИЧЕН pick_disk_variant (путь обновления): (cover, match, same_camp) — иначе
        детект и обновление выберут РАЗНЫЕ варианты и цикл «обновление» не сойдётся (Pol/
        Shu). same_camp — лишь тайбрейкер (при равных cover+match предпочесть базу).
        allowed_sources (множество 'camp/unit' = логические варианты мода) ограничивает
        выбор: иначе Pol*-контент под путём Shu*/ в redux_base посчитался бы тем же модом.
        {rel:sha}|None."""
        best, best_score = None, (-1, -1, -1)
        for label, fmap in unit_maps:
            if allowed_sources is not None and label not in allowed_sources:
                continue
            sub = {r: s for r, s in fmap.items() if r == mid or r.startswith(mid + '/')}
            if not sub:
                continue
            cover = sum(1 for r in sub if r in disk)
            match = sum(1 for r, s in sub.items() if disk.get(r) == s)
            same_camp = bool(prefer_camp and label.split('/')[0] == prefer_camp)
            score = (cover, match, same_camp)
            if score > best_score:
                best_score, best = score, sub
        return best

    def check_updates(self):
        """Проверить обновления ПОФАЙЛОВОЙ сверкой: хеши файлов на диске (из индекса)
        против опубликованных манифестов (+форки). Запускается фоном."""
        if self.busy:
            return {'ok': False, 'error': 'Уже идёт операция.'}
        if self._game_root() is None:
            return {'ok': False, 'error': 'Сначала укажите папку игры.'}
        self.busy = True
        self._cancel.clear()
        self._emit('op_begin', {'name': 'Проверка обновлений'})
        threading.Thread(target=self._check_updates_worker, daemon=True).start()
        return {'ok': True}

    def _check_updates_worker(self):
        try:
            mods_dir = self._mods_dir()
            cat = self._catalog_for(self._repo(), self._token())
            self.log('Хеширую файлы на диске (инкрементально)…')
            idx = core.index_disk_mods(mods_dir, cat, prev_index=self._disk_index,
                                       log=self.log, progress_cb=self._progress,
                                       should_cancel=self.should_cancel)
            core.save_disk_index(mods_dir, idx)
            self._disk_index = idx
            base = idx.get('base')
            camp = base.get('camp') if base else None
            if not camp:
                self.log('Не удалось определить базу в Mods — сверять не с чем.')
                self._finish_check(); return
            self.log(f'Сверяю с опубликованными вариантами (база: {camp})…')
            unit_maps = self._all_unit_maps()
            # источники по логическому id + его Pol/Shu-сиблингам (id@<Name>): дисковая
            # папка ShusRangers/X может нести genuine X ИЛИ X@PolX (redux_base кладёт
            # Pol-контент под тем же путём) — кандидаты ОБЯЗАНЫ включать сиблингов, иначе
            # на redux-сборке Pol-папка вечно «обновляется» к чужому genuine-варианту.
            src_by_base = {}
            for k, e in cat.items():
                bid = k.split('@', 1)[0]
                src_by_base.setdefault(bid, set()).update(
                    v['source'] for v in e.get('variants', []))
            # доступные на сервере блобы: файлы, которых нет на HF, скачать нельзя →
            # не считаем их обновлением (иначе вечный ложный «⬆ обновление»)
            avail = set()
            try:
                cidx = core.load_chunk_index(repo=self._repo(), token=self._token())
                avail |= set(cidx.get('blobs', {}))
            except Exception as e:
                self.log(f'⚠ индекс частей не загружен ({e})')
            for fidx in self._idx_by_repo.values():
                avail |= set((fidx or {}).get('blobs', {}))
            ups = {}
            unavail = 0
            for mid, m in idx.get('mods', {}).items():
                if self.should_cancel():
                    raise core.OperationCancelled()
                disk = {rel: f['sha'] for rel, f in (m.get('files') or {}).items()}
                allowed = src_by_base.get(mid)
                if not allowed:                    # фолбэк по короткому имени (как в index_disk_mods)
                    leaf = mid.split('/')[-1]
                    cands = [k for k in cat if k.split('/')[-1] == leaf]
                    if len(cands) == 1:
                        allowed = src_by_base.get(cands[0].split('@', 1)[0])
                if not allowed:
                    continue                       # мода нет в каталоге → сверять не с чем
                # выбор варианта игроком (переключатель Pol/Shu): нацеливаемся ИМЕННО на
                # выбранный вариант — если диск ему не соответствует, будет «обновление»
                choice = (self.profile.get('variants') or {}).get(mid)
                theirs = None
                if choice and choice in cat:
                    theirs = self._variant_files(
                        mid, unit_maps, cat[choice].get('default_source'))
                if not theirs:
                    theirs = self._best_unit_files(mid, unit_maps, disk, allowed, prefer_camp=camp)
                if not theirs:
                    continue                       # ни один логический вариант не лёг на диск
                unavail += sum(1 for s in theirs.values() if s not in avail)
                theirs = {r: s for r, s in theirs.items() if s in avail}
                if not theirs:
                    continue                       # нечего скачать с сервера
                # снимок (если ставили лаунчером) — чтобы НЕ считать правки игрока обновлением
                base = {}
                snap = core.load_install_snapshot(mods_dir, mid)
                if snap:
                    base = {core.install_relpath(rp): sha
                            for rp, sha in (snap.get('files') or {}).items()}
                n = core.plan_actionable_sha(theirs, base, disk)
                if n:
                    ups[mid] = {'n': n}
            self._updates = ups
            if unavail:
                self.log(f'(на сервере пока нет {unavail} файлов — они не учитываются)')
            for pm in self.profile.get('mods', []):
                if pm.get('type') == 'desc':
                    pm['update_available'] = bool(pm.get('id') in ups)
            self.log(f'Готово. Модов с обновлением: {len(ups)}'
                     + (': ' + ', '.join(sorted(ups)[:20]) if ups else '.'))
            self._emit('tree_dirty')
        except core.OperationCancelled:
            self.log('Проверка обновлений отменена.')
        except Exception as e:
            self.log(f'ОШИБКА проверки обновлений: {e}')
        self._finish_check()

    def _finish_check(self):
        self.busy = False
        self._emit('op_end', {'status': 'Готово'})

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
            'name_mode': self.config.get('name_mode', 'folder'),
            'log_verbose': self.config.get('log_verbose', False),
            'profiles': self.config.get('profiles', ['default']),
            'current_profile': self.current_profile,
            'game_path': self.profile.get('game_path', ''),
            'base': self.profile.get('base', ''),
            'repo': self._repo(),
            'has_token': bool(self._token()),
            'forks': self._forks_public(),
            'is_rwt': IS_RWT,
            'version': LAUNCHER_VERSION,
            'busy': self.busy,
        }

    def check_self_update(self):
        """Проверить, вышла ли новая версия самого лаунчера (state/launcher_release.json
        в репозитории). Возвращает {'ok','update','version','current','url','notes'}."""
        try:
            info = core._fetch_json(RELEASE_REF, self._repo(), self._token())
        except Exception as e:
            return {'ok': False, 'error': str(e)}
        latest = str(info.get('version', '')).strip()
        upd = bool(latest) and _ver_tuple(latest) > _ver_tuple(LAUNCHER_VERSION)
        return {'ok': True, 'update': upd, 'version': latest,
                'current': LAUNCHER_VERSION, 'url': info.get('url', ''),
                'notes': info.get('notes', '')}

    # ───────── сборка дерева ─────────
    def _tree_mode(self):
        return self.config.get('tree_mode', 'folder')

    def _section_of(self, mid):
        if mid not in self._sections:
            p = self._mods_dir() / mid.replace('/', os.sep) / 'ModuleInfo.txt'
            self._sections[mid] = core.read_module_section(p) if p.exists() else ''
        sec = self._sections[mid]
        if not sec:                         # не установлен — раздел из каталога (чтобы мод,
            ce = self._catalog_entry(mid)   # добавленный «по названию», попал в свою группу,
            sec = (ce.get('section') if ce else '') or ''   # а не в «прочее» вверху списка
        return sec

    def _name_of(self, mid):
        """Имя мода как в игре — поле Name из ModuleInfo (диск, кэш). Фолбэк:
        имя варианта из каталога, затем имя папки. Для режима «Имя из ModuleInfo»."""
        if mid not in self._names:
            p = self._mi_path(mid)
            nm = ''
            if p.exists():
                nm = (core.module_card(p) or {}).get('name', '')
            if not nm:
                ce = self._catalog_entry(mid)
                nm = (ce.get('name') if ce else '') or ''
            self._names[mid] = core._strip_color(nm) if nm else mid.split('/')[-1]
        return self._names[mid]

    def _mi_path(self, mid):
        return self._mods_dir() / mid.replace('/', os.sep) / 'ModuleInfo.txt'

    def _catalog_entry(self, mid):
        """Запись каталога по mid (точно или по короткому имени). None если нет."""
        cat = self._catalog_cache or {}
        if mid in cat:
            return cat[mid]
        leaf = mid.split('/')[-1]
        return next((v for k, v in cat.items() if k.split('/')[-1] == leaf), None)

    def _variant_index(self):
        """base-id → [ключи каталога вариантов] и короткое-имя → [ключи] (фолбэк). Кэш.
        Пустой каталог (ещё не загружен) НЕ кэшируем — иначе бейджи навсегда пустые."""
        cat = self._catalog_cache
        if not cat:
            return {}, {}
        if self._camps_idx is None:
            by_base, by_leaf = {}, {}
            for k in cat:
                b = k.split('@', 1)[0]
                by_base.setdefault(b, []).append(k)
                by_leaf.setdefault(b.split('/')[-1], []).append(k)
            self._camps_idx = (by_base, by_leaf)
        return self._camps_idx

    def _variant_keys(self, mid):
        """Ключи каталога всех вариантов папки mid (base + @-сиблинги). [] если нет."""
        by_base, by_leaf = self._variant_index()
        base = mid.split('@', 1)[0]
        return by_base.get(base) or by_leaf.get(base.split('/')[-1]) or []

    @staticmethod
    def _entry_camps(e):
        return {v['source'].split('/')[0] for v in e.get('variants', [])
                if '/' in (v.get('source') or '')}

    def _camp_variant_entry(self, mid, camp):
        """Запись каталога варианта папки mid, релевантного лагерю camp: у папки Pol/Shu
        (напр. ShusRangers/ShuNukes) под redux канон — @PolNukes, под original/universe —
        базовый ShuNukes. Возвращает (ключ, запись) или None. Для показа в диалоге
        добавления «Лагерь→Пак→Мод»: имя/описание берём у варианта ЭТОГО лагеря, а не у
        первого попавшегося (Shu-)варианта."""
        cat = self._catalog_cache or {}
        keys = self._variant_keys(mid)
        if not keys:
            e = self._catalog_entry(mid)
            return (mid, e) if e else None
        match = [k for k in keys if camp in self._entry_camps(cat.get(k) or {})]
        pick = match[0] if match else (mid if mid in cat else keys[0])
        return (pick, cat.get(pick) or {})

    def _installed_variant_key(self, mid):
        """Ключ каталога ИМЕННО установленного варианта папки mid — по ModuleInfo Name
        (Pol/Shu-папка общая, на диске один вариант). None, если не определить."""
        cat = self._catalog_cache or {}
        if '@' in mid and mid in cat:
            return mid                               # явный вариант
        cands = self._variant_keys(mid)
        if len(cands) == 1:
            return cands[0]
        if not cands:
            return None
        nm = self._name_of(mid)                      # ModuleInfo Name с диска (кэш)
        for k in cands:
            if (cat[k].get('name') or '') == nm:
                return k
        return None

    def _camps_of(self, mid):
        """Метки лагерей ИМЕННО установленного варианта mid. Папка Pol/Shu общая, но на
        диске один вариант → по ModuleInfo Name берём камп(ы) его записи (Pol→redux,
        Shu→uni/orig). Если вариант не определён — объединение всех вариантов папки.

        Если игрок ЯВНО выбрал вариант в переключателе (profile['variants']) — метка
        следует ЗА выбором сразу, ещё до перекачки: «я выбрал мод с другой меткой —
        метка должна обновиться». Иначе — по установленному на диске варианту."""
        cat = self._catalog_cache or {}
        if not cat or not mid:
            return []
        chosen = (self.profile.get('variants') or {}).get(mid.split('@', 1)[0])
        key = chosen if (chosen and chosen in cat) else self._installed_variant_key(mid)
        if key and key in cat:
            return sorted(self._entry_camps(cat[key]))
        camps = set()
        for k in self._variant_keys(mid):            # фолбэк: объединение
            camps |= self._entry_camps(cat[k])
        return sorted(camps)

    # ───────── выбор варианта Pol/Shu в общей папке ─────────
    def _variants_of(self, mid):
        """Список вариантов папки mid для переключателя: [{key,name,camps}]. Пусто, если
        вариант один (переключать нечего)."""
        cat = self._catalog_cache or {}
        keys = self._variant_keys(mid)
        if len(keys) < 2:
            return []
        out = []
        for k in keys:
            e = cat.get(k) or {}
            out.append({'key': k, 'name': e.get('name') or k.split('/')[-1],
                        'camps': sorted(self._entry_camps(e))})
        out.sort(key=lambda v: v['name'])
        return out

    def _chosen_variant(self, mid):
        """Выбранный игроком вариант папки mid (profile['variants']) или, если не
        выбирал, — установленный на диске. '' если не определить."""
        base = mid.split('@', 1)[0]
        ch = (self.profile.get('variants') or {}).get(base)
        if ch and ch in (self._catalog_cache or {}):
            return ch
        return self._installed_variant_key(mid) or ''

    def set_variant(self, mid, key):
        """Игрок выбрал вариант key для папки mid. Если он отличается от установленного —
        помечаем мод как требующий обновления (перекачки); иначе снимаем пометку."""
        cat = self._catalog_cache or {}
        base = mid.split('@', 1)[0]
        if key not in cat:
            return {'ok': False, 'error': 'Неизвестный вариант.'}
        self.profile.setdefault('variants', {})[base] = key
        self._save_profile()
        installed = self._installed_variant_key(mid)
        want_name = (cat.get(key) or {}).get('name')
        inst_name = (cat.get(installed) or {}).get('name') if installed else None
        if key != installed and want_name != inst_name:
            self._updates[base] = {'n': 1, 'variant': key}   # нужно перекачать вариант
        else:
            self._updates.pop(base, None)
        self._emit('tree_dirty')
        return {'ok': True}

    @staticmethod
    def _variant_files(mid, unit_maps, source):
        """Файлы мода mid из КОНКРЕТНОГО источника source (для нацеливания на выбранный
        вариант). {rel:sha}|None."""
        for label, fmap in unit_maps:
            if label == source:
                return {r: s for r, s in fmap.items() if r == mid or r.startswith(mid + '/')}
        return None

    def get_all_mods(self):
        """Плоский список ВСЕХ модов каталога для поиска по названию (режим добавления
        «По названию»): [{id, name, camps, desc}]. id — ключ каталога (с @-вариантом),
        camps — лагеря этого конкретного варианта."""
        cat = self._catalog_cache
        if cat is None:
            return {'ok': False, 'error': 'Каталог ещё загружается — повторите через секунду.'}
        out = []
        for k, e in cat.items():
            camps = sorted({v['source'].split('/')[0] for v in e.get('variants', [])
                            if '/' in (v.get('source') or '')})
            out.append({'id': k, 'base': k.split('@', 1)[0],
                        'name': e.get('name') or k.split('/')[-1],
                        'camps': camps, 'desc': e.get('description', ''),
                        'source': e.get('default_source', '')})
        out.sort(key=lambda m: m['name'].lower())
        return {'ok': True, 'mods': out, 'count': len(out)}

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

    @staticmethod
    def _card_from_entry(ce):
        """Карточка мода из записи каталога (для варианта, которого нет на диске)."""
        var = (ce.get('variants') or [{}])[0]
        return {'name': ce.get('name', ''), 'authors': ce.get('author', ''),
                'small': ce.get('description', ''),
                'full': core._strip_color(ce.get('full_description', '')),
                'full_raw': ce.get('full_description', ''),
                'requires': var.get('depends', []),
                'conflicts': var.get('conflicts', []),
                'section': ce.get('section', ''), 'priority': ''}

    def get_mod_info(self, mid, variant=None):
        """Полная карточка мода для окна (i): из локального ModuleInfo, фолбэк на каталог.

        variant — ключ каталога конкретного варианта папки (Pol/Shu). Если задан, карточку
        строим ИМЕННО по нему (из каталога), чтобы можно было посмотреть описание обоих
        вариантов, даже если на диске стоит другой. Ответ также содержит `variants`
        (переключатель в окне) и `variant_key` (какой вариант сейчас показан)."""
        cat = self._catalog_cache or {}
        variants = self._variants_of(mid) if mid else []       # [{key,name,camps}]
        info, mi_ok, shown_key = {}, False, ''
        if variant and variant in cat:                          # явно запрошенный вариант
            info = self._card_from_entry(cat[variant])
            shown_key = variant
            mi_ok = (variant == self._installed_variant_key(mid))
        else:
            if mid:
                p = self._mi_path(mid)
                if p.exists():
                    info = core.module_card(p) or {}
                    mi_ok = bool(info)
            if not mi_ok and mid:                               # фолбэк из каталога
                ce = self._catalog_entry(mid)
                if ce:
                    info = self._card_from_entry(ce)
            shown_key = self._chosen_variant(mid) if mid else ''
        if not info:
            return {'ok': False}
        info.setdefault('name', mid.split('/')[-1] if mid else '')
        info['id'] = mid or ''
        info['location'] = (mid or '').replace('/', '\\')   # как в игре: Категория\Имя
        info['installed'] = mi_ok
        info['variants'] = variants                          # [] если вариант один
        info['variant_key'] = shown_key
        # полное описание с цветовой разметкой → безопасный HTML для окна (#12/#13)
        info['full_html'] = core.color_to_html(info.get('full_raw') or info.get('full') or '')
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
            elif typ == 'desc' and m.get('id') and not m.get('url'):
                # мод из каталога, добавленный «по названию»: показываем как обычный мод
                # в ЕГО разделе/папке (а не как «форк» в «прочее» вверху списка)
                mid = m['id']; seen.add(mid)
                on = (mid in disk) or bool(m.get('last_downloaded'))
                sc, st = status_of(m, on)
                rows.append((m.get('camp') or 'набор профиля', self._mod_group(mid), 'мод',
                             mid.split('/')[-1], sc, st,
                             m.get('last_downloaded') or disk.get(mid, '') if on else '',
                             iid, mid))
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
            if mid in self._updates:
                sc, st = ('upd', '⬆ обновление')
            elif unknown:
                sc, st = ('unknown', '❓ не в каталоге')
            else:
                sc, st = ('ok', '✅ установлен')
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
                'name': (self._name_of(mid) if mid else label),
                'status_class': sc, 'status': st,
                'in_game': bool(mid and mid in modcfg),
                'in_profile': bool(mid and mid in enabled),
                'date': fmt_date(date), 'mid': mid,
                'selectable': bool(iid),
                'folder': (mid.split('/')[0] if mid and '/' in mid else ''),
                'section': (self._section_of(mid) if mid else ''),
                'desc': (self._desc_of(mid) if mid else ''),
                'has_info': bool(mid),
                'labels': (self._camps_of(mid) if mid else []),   # лагеря-метки (бейджи)
                'variants': (self._variants_of(mid) if mid else []),  # Pol/Shu-переключатель
                'chosen': (self._chosen_variant(mid) if mid else ''),
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
            'name_mode': self.config.get('name_mode', 'folder'),
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
            self._camps_idx = None          # перестроить индекс вариантов на свежем каталоге
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

    def set_name_mode(self, mode):
        """Что показывать как имя мода в списке: 'folder' (имя папки) либо
        'module' (поле Name из ModuleInfo — как в самой игре)."""
        self.config['name_mode'] = 'module' if mode == 'module' else 'folder'
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
        self._camps_idx = None
        self._cat_by_repo = {}
        self._idx_by_repo = {}
        self._fork_man_cache = {}
        self._pub_cache = {}
        self._pub_cache_all = None
        self._updates = {}
        self._cat_loading = False

    def set_forks(self, forks):
        """Сохранить список форков (доп. репозиториев). forks: [{'repo','token'}].
        Приоритет = порядок в списке (первый — высший)."""
        prev = {f.get('repo'): (f.get('token') or '')
                for f in self.config.get('forks', []) or []}
        clean = []
        for f in (forks or []):
            repo = (f.get('repo') or '').strip()
            if not repo:
                continue
            tok = (f.get('token') or '').strip()
            if not tok and repo in prev:
                tok = prev[repo]          # токен не вводили заново -> сохранить прежний
            clean.append({'repo': repo, 'token': tok})
        self.config['forks'] = clean
        self._invalidate_remote_cache()
        self._save_config()
        return {'ok': True, 'forks': self._forks_public()}

    def _forks_public(self):
        """Форки для UI: токен не отдаём целиком, только признак наличия."""
        return [{'repo': f['repo'], 'has_token': bool(f['token'])}
                for f in self._forks()]

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

    def plan_enable(self, mid):
        """Каскад при включении мода mid (как подтверждение в игре): какие
        зависимости подключатся, какие конфликтующие отключатся, чего не хватает.
        Возвращает {'ok', 'cascade': {add[], disable[], missing[], block} | None}.
        cascade=None — каталог ещё не загружен (тогда фронт просто переключит)."""
        cat = self._catalog_cache
        if not cat:
            return {'ok': True, 'cascade': None}
        enabled = set(self.profile.get('enabled', []))
        disk = set(self._disk_mods())
        try:
            c = core.plan_enable(mid, enabled, disk, cat, self._mods_dir(),
                                 name_of=self._name_of)
        except Exception as e:
            return {'ok': True, 'cascade': None, 'error': str(e)}
        # без изменений (нет зависимостей/конфликтов/недостающего) — каскад не нужен
        if not (c['add'] or c['disable'] or c['missing']):
            return {'ok': True, 'cascade': None}
        nm = lambda m: self._name_of(m)
        c['add_names'] = [nm(m) for m in c['add']]
        c['disable_names'] = [nm(m) for m in c['disable']]
        return {'ok': True, 'cascade': c}

    def plan_disable(self, mid):
        """Каскад при выключении мода mid: какие включённые моды зависят от него
        (транзитивно) и отключатся следом. {'ok','cascade':{disable[],disable_names[]}|None}.
        cascade=None — отключать нечего (никто не зависит)."""
        enabled = set(self.profile.get('enabled', []))
        try:
            c = core.plan_disable(mid, enabled, self._catalog_cache or {},
                                  self._mods_dir(), name_of=self._name_of)
        except Exception as e:
            return {'ok': True, 'cascade': None, 'error': str(e)}
        if not c['disable']:
            return {'ok': True, 'cascade': None}
        c['disable_names'] = [self._name_of(m) for m in c['disable']]
        return {'ok': True, 'cascade': c}

    def toggle_enabled(self, mid, on, add=None, disable=None):
        """Включить/выключить мод в сборке. При включении: add — зависимости к
        подключению, disable — конфликтующие к отключению (plan_enable). При
        выключении: disable — зависящие моды, отключаемые следом (plan_disable)."""
        en = self.profile.setdefault('enabled', [])
        if on:
            for m in [mid] + list(add or []):
                if m not in en:
                    en.append(m)
            for m in (disable or []):
                if m in en:
                    en.remove(m)
        else:
            for m in [mid] + list(disable or []):
                if m in en:
                    en.remove(m)
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
                ff, fidx = self._fork_unit_overlay(p['camp'], p['name'])
                core.reconstruct_unit(
                    m['repo'], p['camp'], p['name'], mods_dir, tok,
                    self._progress, self.log, tmp_dir=ROOT, should_cancel=self.should_cancel,
                    part_cb=self._part_progress, byte_cb=self._byte_progress,
                    skip_present=True,         # докачивать только недостающее/изменённое
                    fork_files=ff, fork_index=fidx)
                if p.get('tier') == 'base':
                    self.profile['installed_base'] = p['name']
        elif m.get('type') == 'unit':
            self._pack_ctx = m.get('name', m.get('unit', ''))
            ff, fidx = self._fork_unit_overlay(m['camp'], m['unit'])
            core.reconstruct_unit(
                m['repo'], m['camp'], m['unit'], mods_dir, tok,
                self._progress, self.log, tmp_dir=ROOT, mod=m.get('mod') or None,
                should_cancel=self.should_cancel,
                part_cb=self._part_progress, byte_cb=self._byte_progress,
                skip_present=True,             # «починка»: сверка хешей, качаем только отличия
                fork_files=ff, fork_index=fidx)
        elif m.get('type') == 'desc':
            if m.get('url'):               # явный форк одного мода по URL (старое поведение)
                idx = core.load_chunk_index(repo=self._repo(), token=tok)
                desc = core.descriptor_for({'url': m['url']}, self._catalog_cache,
                                           self._repo(), tok)
            else:                          # наложение форков поверх основного (по файлам)
                desc, idx = self._overlay(m['id'], m.get('source'))
            if desc:
                if desc.get('overlaid'):
                    self.log('  (с наложением форков)')
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
                ov_index = self._overlay_plan(plan)   # наложить форки на каждый мод плана
                if ov_index is not None:
                    index = ov_index
                else:
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
        """Список модов пака (для выбора конкретного мода). Каждый мод — объект
        {key, name, camps, desc}: key — папка (мод-ключ для установки), а имя/описание/
        метки берём у варианта ИМЕННО этого лагеря (redux→Pol*, orig/uni→Shu*), чтобы в
        диалоге не показывались Shu*-названия для redux-пака."""
        try:
            keys = core.list_unit_mods(self._repo(), camp, unit, self._token())
        except Exception as e:
            return {'ok': False, 'error': str(e)}
        out = []
        for key in keys:
            if key == '_base':
                out.append({'key': key, 'name': '_base', 'camps': [],
                            'desc': 'Общие файлы игры'})
                continue
            ent = self._camp_variant_entry(key, camp)
            if ent and ent[1]:
                e = ent[1]
                out.append({'key': key, 'name': e.get('name') or key.split('/')[-1],
                            'camps': sorted(self._entry_camps(e)),
                            'desc': e.get('description', '')})
            else:
                out.append({'key': key, 'name': key.split('/')[-1], 'camps': [], 'desc': ''})
        return {'ok': True, 'mods': out}

    def add_mod(self, payload):
        """Добавить запись в сборку. payload:
        {mode:'src'|'fork', camp, pack:{camp,unit,name}|None, mod:'', url:''}"""
        repo = self._repo()
        if payload.get('mode') == 'search':
            mid = (payload.get('id') or '').strip()
            if not mid:
                return {'ok': False, 'error': 'Не выбран мод.'}
            self._dedup_folder(mid)
            entry = {'type': 'desc', 'id': mid, 'repo': repo,
                     'name': (payload.get('name') or mid.split('/')[-1])}
            src = (payload.get('source') or '').strip()
            if src:
                entry['source'] = src
            self.profile.setdefault('mods', []).append(entry)
            self._save_profile()
            return {'ok': True}
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
            self._dedup_folder(msel)               # один вариант на папку (#2.2)
            mod['mod'] = msel
            mod['name'] = msel
        self.profile.setdefault('mods', []).append(mod)
        self._save_profile()
        return {'ok': True}

    def _dedup_folder(self, mid):
        """Убрать из набора прежние записи ТОГО ЖЕ мода (та же дисковая папка/база), чтобы
        не оказалось двух вариантов одной папки с разными метками (правило #2.2). База —
        id без @-варианта; затрагивает только записи-моды (unit с mod / desc с id)."""
        base = (mid or '').split('@', 1)[0]
        if not base:
            return
        kept, dropped = [], 0
        for m in self.profile.get('mods', []):
            emid = m.get('mod') or m.get('id') or ''
            if emid and emid.split('@', 1)[0] == base and m.get('type') in ('unit', 'desc'):
                dropped += 1
                continue
            kept.append(m)
        if dropped:
            self.profile['mods'] = kept
            self.log(f'Заменён вариант мода «{base.split("/")[-1]}» в наборе.')

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
        self._merge_remember = {}          # запомненные решения конфликтов (status -> code)
        self._merge_remember_on = False    # «больше не спрашивать» до конца серии
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
            # согласованность с детектом: раз обновлять нечего — снять бейдж «обновление»
            upd_mid = target[1] if target[0] == 'disk' else (desc.get('id') if desc else None)
            if upd_mid:
                self._updates.pop(upd_mid, None)
                self._emit('tree_dirty')
            self._merge_next()
            return
        self._pending_merge = {'target': target, 'desc': desc, 'plan': plan, 'index': index}
        # «Запомнить решения до конца обновления»: если все конфликты этого мода
        # покрыты ранее запомненными решениями (по типу конфликта) — применяем без
        # диалога, чтобы не спрашивать по каждому моду.
        auto = self._remembered_decisions(plan)
        if auto is not None:
            self.log(f'{plan.get("id")}: применяю запомненные решения конфликтов.')
            pm = self._pending_merge
            self._pending_merge = None
            threading.Thread(target=self._apply_merge_worker, args=(pm, auto),
                             daemon=True).start()
            return
        self._emit('merge_plan', self._serialize_plan(plan))

    def _remembered_decisions(self, plan):
        """Если игрок выбрал «Запомнить» (self._merge_remember_on) — применяем все
        следующие моды БЕЗ диалога: конфликты решаются запомненным выбором по типу,
        а не покрытые типы — дефолтом; add/update и так авто. Иначе (None) — диалог."""
        if not getattr(self, '_merge_remember_on', False):
            return None
        rem = getattr(self, '_merge_remember', {})
        has_snap = plan.get('has_snapshot', True)
        dec = {}
        for r in plan['actions']:
            if r['status'] in CONFLICT_OPTIONS:
                dec[r['relpath']] = rem.get(r['status']) or core.default_decision(r['status'], has_snap)
        return dec

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
                    'default': core.default_decision(r['status'], plan.get('has_snapshot', True)),
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
            desc, index = self._overlay_theirs(desc, desc.get('source'), allow=not m.get('url'))
            plan = core.plan_update_merge(desc, mods_dir, index, token=tok, log=self.log,
                                          snapshot=snap, tmp_dir=ROOT,
                                          progress_cb=self._progress,
                                          should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Планирование отменено.'); self._merge_next(); return
        except Exception as e:
            self.log(f'ОШИБКА планирования: {e}'); self._merge_next(); return
        self._emit_plan_or_skip(('profile', i), desc, plan, index)

    def _full_variant_descriptor(self, key, cat, repo, tok, source=None):
        """Полный дескриптор варианта: НАБОР ФАЙЛОВ = слияние ВСЕХ источников его лагеря
        (base-installer + *_fixes), fixes поверх base. Иначе default_source, указывающий
        на куцый *_fixes-пак (напр. 5 файлов из 54), даёт неполный «theirs» — и апдейт
        сносит остальные файлы мода: «при смене Pol/Shu мод полностью удаляется».
        source задаёт лагерь (по умолчанию default_source записи). Все дескрипторы
        адресуют один общий HF-индекс, поэтому блобы из разных паков резолвятся."""
        ent = cat.get(key) or {}
        src = source or ent.get('default_source') or ''
        camp = src.split('/')[0]
        srcs = [v['source'] for v in ent.get('variants', [])
                if (v.get('source') or '').split('/')[0] == camp] if camp else []
        if len(srcs) <= 1:                       # один источник — сливать нечего
            return core.descriptor_for({'id': key, 'source': src or (srcs[0] if srcs else None)},
                                       cat, repo, tok)
        try:
            packs = self._get_packs(tok)
        except Exception:
            packs = {}
        # порядок применения по load_order пака: выше = позже = приоритетнее (fixes бьёт base)
        srcs.sort(key=lambda s: (packs.get(s) or {}).get('load_order', 999), reverse=True)
        descs = [core.descriptor_for({'id': key, 'source': s}, cat, repo, tok) for s in srcs]
        descs = [d for d in descs if d]
        if not descs:
            return None
        merged = core.merge_descriptors(descs)   # fixes-первым → перекрывает base
        if merged is not None and src:
            merged = dict(merged); merged['source'] = src
        return merged

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
            desc = info = None
            # выбор варианта игроком (переключатель Pol/Shu) — ставим ИМЕННО его
            choice = (self.profile.get('variants') or {}).get(mid.split('@', 1)[0])
            if choice and choice in cat:
                src = cat[choice].get('default_source')
                self.log(f'Выбранный вариант: {cat[choice].get("name")} ({src}).')
                d = self._full_variant_descriptor(choice, cat, repo, tok, source=src)
                if d:
                    desc, info = d, {'source': src}
                else:
                    self.log('Дескриптор выбранного варианта не найден — подбираю по диску.')
            if not desc:
                self.log('Подбираю вариант мода по файлам на диске…')
                desc, info = core.pick_disk_variant(cat, mid, mods_dir, repo, tok,
                                                    prefer_camp=prefer, log=self.log,
                                                    should_cancel=self.should_cancel)
                if desc:
                    # подобран лучший ОДИН источник по совпадению; расширяем до полного
                    # набора лагеря (base+fixes), иначе *_fixes-выбор снёс бы файлы
                    full = self._full_variant_descriptor(desc.get('id'), cat, repo, tok,
                                                         source=info.get('source'))
                    if full and full.get('files'):
                        full = dict(full); full['id'] = desc.get('id')
                        desc = full
                    note = ''
                    if prefer and not str(info["source"]).startswith(prefer + '/'):
                        note = (f' — выбран по совпадению файлов на диске, не по базе ({prefer}); '
                                f'это нормально: один мод может совпадать с версией из другого источника')
                    self.log(f'Вариант: {info["source"]} (совпало {info["match"]}/{info["cover"]} '
                             f'из {info["total"]}){note}')
            if not desc:
                self.log(f'Мод {mid} не найден в каталоге — обновить нечем.')
                self._merge_next(); return
            # дисковая папка mid — стабильная идентичность мода. Если подобран Pol/Shu-
            # сиблинг (id вида ShusRangers/X@PolX), ставим его контент, но снимок и детект
            # ключуются по mid (папке), а не по id варианта — иначе снимок не находится.
            if desc.get('id') != mid:
                desc = dict(desc); desc['id'] = mid
            snap = core.load_install_snapshot(mods_dir, mid)
            desc, index = self._overlay_theirs(desc, info.get('source'))
            plan = core.plan_update_merge(desc, mods_dir, index, token=tok, log=self.log,
                                          snapshot=snap, tmp_dir=ROOT,
                                          progress_cb=self._progress,
                                          should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Планирование отменено.'); self._merge_next(); return
        except Exception as e:
            self.log(f'ОШИБКА планирования: {e}'); self._merge_next(); return
        self._emit_plan_or_skip(('disk', mid), desc, plan, index)

    def apply_merge(self, decisions, remember=False):
        pm = getattr(self, '_pending_merge', None)
        if not pm:
            return {'ok': False, 'error': 'Нет плана для применения.'}
        self._pending_merge = None
        decisions = decisions or {}
        if remember:
            # «больше не спрашивать до конца серии»: дальше моды применяются авто.
            # Запоминаем ВЫБОР по типу конфликта (текст/бинарь/удалён); для не покрытых
            # типов в следующих модах применится дефолт. Добавления/обновления и так авто.
            self._merge_remember_on = True
            self._merge_remember = getattr(self, '_merge_remember', {})
            has_snap = pm['plan'].get('has_snapshot', True)
            for r in pm['plan']['actions']:
                if r['status'] in CONFLICT_OPTIONS:
                    code = decisions.get(r['relpath']) or core.default_decision(r['status'], has_snap)
                    if code:
                        self._merge_remember[r['status']] = code
        threading.Thread(target=self._apply_merge_worker, args=(pm, decisions),
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
        self._pack_ctx = f'обновление: {(desc or {}).get("id", "")}'
        self._dl_bytes = 0
        try:
            stats = core.apply_update_plan(desc, plan, decisions, mods_dir, index,
                                           token=self._token(), log=self.log,
                                           tmp_dir=ROOT, progress_cb=self._progress,
                                           should_cancel=self.should_cancel,
                                           byte_cb=self._byte_progress,
                                           part_cb=self._part_progress)
            self.log(f'Применено: {stats}')
            # мод обновлён → снять пометку «⬆ обновление» (строка станет «✅ установлен»)
            upd_mid = target[1] if target[0] == 'disk' else (desc.get('id') if desc else None)
            if upd_mid:
                self._updates.pop(upd_mid, None)
            if target[0] == 'profile':
                m = self.profile['mods'][target[1]]
                m['installed_version'] = plan.get('version_new')
                m['update_available'] = False
                self._save_profile()
            self._emit('tree_dirty')
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
        removable = kept = 0
        if d.exists():
            for p in d.rglob('*'):
                if not p.is_file():
                    continue
                if _is_base_game_path(p.relative_to(d).as_posix()):
                    kept += 1
                else:
                    removable += 1
        # count = что реально удалится (базовые моды игры исключены)
        return {'ok': True, 'count': removable, 'kept': kept, 'path': str(d)}

    def clear_mods(self):
        if self.busy:
            return {'ok': False, 'error': 'Уже идёт операция.'}
        err = self._require_game()
        if err:
            return {'ok': False, 'error': err}
        d = self._mods_dir()
        if not d.exists():
            return {'ok': True, 'removed': 0, 'kept': 0}
        removed = kept = 0
        # удаляем снизу вверх (сначала файлы, потом опустевшие папки); базовые моды
        # игры (BASE_GAME_KEEP) и их родительские папки пропускаем
        for p in sorted(d.rglob('*'), key=lambda x: len(x.parts), reverse=True):
            rel = p.relative_to(d).as_posix()
            if _is_base_game_path(rel):
                if p.is_file():
                    kept += 1
                continue
            try:
                if p.is_file():
                    p.unlink(); removed += 1
                elif p.is_dir():
                    p.rmdir()                 # удалится только если опустела
            except Exception:
                pass
        for m in self.profile.get('mods', []):
            m['last_downloaded'] = None
        self._save_profile()
        self.log(f'Очищено: {removed} файлов из {d}'
                 + (f' (сохранено базовых модов игры: {kept})' if kept else ''))
        self._emit('tree_dirty')
        return {'ok': True, 'removed': removed, 'kept': kept}

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
        # проверка набора «в сборке» по зависимостям/конфликтам (Name-aware)
        enabled = set(self.profile.get('enabled', []))
        try:
            disk = set(self._disk_mods())
        except Exception:
            disk = set()
        try:
            setrep = core.check_enabled_compat(enabled, disk, self._catalog_cache or {},
                                               self._mods_dir(), name_of=self._name_of) or {}
        except Exception as e:
            setrep = {}
            self.log(f'Совместимость: ошибка проверки набора ({e})')
        items = []
        nm = lambda u: (packs.get(u) or {}).get('name', u)
        mn = lambda m: (self._name_of(m) if m else m)   # имя мода как в игре
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
        # направление 1/2: у включённого мода есть зависимость не в сборке
        for d in setrep.get('dep_issues', []):
            if d.get('available'):
                items.append({'level': 'warn',
                              'text': f'Моду «{d.get("name")}» нужен «{d.get("dep")}», но он не в сборке — подключите его (или включение мода подтянет его само).'})
            else:
                items.append({'level': 'warn',
                              'text': f'Моду «{d.get("name")}» нужен «{d.get("dep")}», которого нет ни в сборке, ни в каталоге, ни на диске.'})
        # направление 2/2: два конфликтующих мода оба в сборке
        for a, b in setrep.get('conflicts', []):
            items.append({'level': 'warn',
                          'text': f'Конфликт: «{mn(a)}» и «{mn(b)}» оба в сборке — оставьте один.'})
        # зависимости подключённых в игре модов по ИМЕНИ (ModuleInfo Name, как делает
        # сама игра): ловит «PolMercsHQ требует PolMercs, а на диске лежит ShuMercs»
        try:
            dc = self._disk_compat_issues()
            for it in dc['deps']:
                items.append({'level': 'warn',
                              'text': f'Подключённому в игре моду «{it["name"]}» не хватает '
                                      f'(нет на диске): {", ".join(it["missing"])}.'})
            for a, b in dc['conflicts']:
                items.append({'level': 'warn',
                              'text': f'Конфликт подключённых в игре: «{a}» и «{b}» несовместимы — оставьте один.'})
        except Exception as e:
            self.log(f'Совместимость: ошибка проверки зависимостей на диске ({e})')
        if not items:
            items.append({'level': 'ok', 'text': 'Проблем не найдено.'})
        return {'ok': True, 'items': items, 'base': base_name}

    def _disk_compat_issues(self):
        """Совместимость подключённых в игре модов ПО ИМЕНИ (ModuleInfo Name): нехватка
        зависимостей и конфликты между подключёнными. Игра резолвит Dependence/Conflict
        по полю Name, а не по имени папки — поэтому Pol/Shu-варианты (одна папка
        ShusRangers/ShuDomiks, но Name=ShuDomiks либо PolDomiks — выбор игрока, вместе
        нельзя) сверяются по Name. Пример: ShuDomiks + PolDomiksPlus → PolDomiksPlus
        требует PolDomiks (нет на диске, стоит ShuDomiks) — обе стороны видны.
        {'deps': [{'name','missing':[Name,...]}], 'conflicts': [(a,b),...]}."""
        mods_dir = self._mods_dir()
        try:
            mids = core.scan_installed_mods(mods_dir)
        except Exception:
            return {'deps': [], 'conflicts': []}
        try:
            connected = set(core.read_modcfg(mods_dir))
        except Exception:
            connected = set()
        present_names, info = set(), {}
        for mid in mids:
            mi = core.read_module_info(mods_dir / mid.replace('/', os.sep) / 'ModuleInfo.txt')
            nm = core._strip_color(mi.get('Name', '')) or mid.split('/')[-1]
            deps = core._split_modlist(mi.get('Dependence', ''))
            cons = core._split_modlist(mi.get('Conflict', ''))
            present_names.add(nm)
            present_names.add(mid.split('/')[-1])   # фолбэк по имени папки
            info[mid] = (nm, deps, cons)
        # проверяем подключённые в игре (если ModCFG пуст — все установленные)
        check = [m for m in info if not connected or m in connected]
        # для конфликтов сверяем ТОЛЬКО по фактическим ModuleInfo Name (НЕ по имени папки:
        # папка Pol/Shu общая — лист 'ShuText' совпал бы с собственным Conflict Pol-варианта
        # и давал ложный «PolText ⟷ ShuText», хотя стоит один вариант)
        conn_names = {info[m][0] for m in check}
        # Объявленный Conflict — ВСЕГДА конфликт, даже если встречная сторона объявляет
        # этот мод зависимостью. Кейс Cat_Nuke↔PolNukes: Cat_Nuke.Dependence=PolNukes, но
        # PolNukes.Conflict=Cat_Nuke — это противоречивая пара (нужен, но несовместим),
        # и её НАДО показать конфликтом, чтобы игрок оставил один. Зависимость не гасит.
        deps_out, conflicts, seen = [], [], set()
        for mid in check:
            nm, deps, cons = info[mid]
            missing = [d for d in deps if d not in present_names]
            if missing:
                deps_out.append({'name': nm, 'missing': missing})
            for c in cons:                          # конфликт: оба реально подключены (по Name)
                if c in conn_names and c != nm:
                    pair = tuple(sorted((nm, c)))
                    if pair not in seen:
                        seen.add(pair)
                        conflicts.append(pair)
        return {'deps': deps_out, 'conflicts': conflicts}

    def refresh_remote(self):
        """Кнопка ⟳: сбросить кэши, перетянуть каталог с GitHub И запустить пофайловую
        проверку обновлений (хеши диска ↔ опубликованные манифесты, фоном)."""
        self._invalidate_remote_cache()    # каталог/packs/форки/опубликованные/обновления
        self._descs = {}
        self._sections = {}
        self._names = {}
        self._lazy_load_catalog()
        self.check_updates()               # фоновая сверка → бейджи «⬆ обновление»
        return {'ok': True, 'checking': True}


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
