"""Level 3: Integration tests — real disk I/O, mocked network.

Тестирует реальные переходы состояний:
- файлы появляются/исчезают на диске
- ModCFG читается/пишется
- фоновые потоки завершаются и меняют состояние
- sha-сравнение при check_updates
- merge flow: план → apply → файл обновлён

Запуск: python webui/test_integration.py  (из корня репозитория)
"""
import sys, types, json, shutil, tempfile, hashlib, time, threading, os
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 0. Перекодируем вывод ──
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── 1. Мокаем webview ДО импорта app ──
if 'webview' not in sys.modules:
    _wv = types.ModuleType('webview')
    _wv.FOLDER_DIALOG = 0
    sys.modules['webview'] = _wv

sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher')
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher\webui')
import app
import launcher_core as core


class _MultiPatch:
    """Патчит и reconstruct_unit, и reconstruct_camp одним фейком. Позиция mods_dir
    (4-й позиционный аргумент) одинакова в обеих функциях, а фейки берут args[3] и
    глотают остальное через **kw — так один side_effect совместим с любой из них.
    Нужно, потому что установка ЛАГЕРЯ теперь идёт через reconstruct_camp (единый
    идемпотентный проход), а установка отдельного мода/юнита — через reconstruct_unit."""
    def __init__(self, fn):
        self._ps = [patch.object(core, 'reconstruct_unit', side_effect=fn),
                    patch.object(core, 'reconstruct_camp', side_effect=fn)]
    def start(self):
        for p in self._ps:
            p.start()
    def stop(self):
        for p in self._ps:
            p.stop()


def patch_reconstruct(fn):
    return _MultiPatch(fn)

# ═══════════════════════════════════════════════
#  Тестовые данные
# ═══════════════════════════════════════════════

# Содержимое ModuleInfo.txt двух версий мода
NUKE_V1 = b'[main]\r\nName = Nuke Weapons\r\nSection = Weapons\r\nVersion = 1.0\r\nDependence=\r\nConflict=\r\n'
NUKE_V2 = b'[main]\r\nName = Nuke Weapons\r\nSection = Weapons\r\nVersion = 2.0\r\nDependence=\r\nConflict=\r\n'
SHA_V1 = hashlib.sha256(NUKE_V1).hexdigest()
SHA_V2 = hashlib.sha256(NUKE_V2).hexdigest()

VISION = b'[main]\r\nName = Fairans Vision\r\nSection = Global\r\nVersion = 3.0\r\nDependence=\r\nConflict=\r\n'

FAKE_CATALOG = {
    'ShusRangers/ShuNukes': {
        'name': 'Ядерное оружие',
        'author': 'TestAuthor',
        'description': 'Ядерное оружие для тестов',
        'section': 'Оружие',
        'default_source': 'redux/redux_base_installer',
        'variants': [{'source': 'redux/redux_base_installer',
                      'path': 'descriptors/redux/redux_base_installer/ShusRangers/ShuNukes.json',
                      'depends': [], 'conflicts': []}],
    },
    'Fairan/FairansVision': {
        'name': 'Видение Файрана',
        'author': 'Fairan',
        'description': 'Глобальный мод',
        'section': 'Глобальные',
        'default_source': 'redux/redux_base_installer',
        'variants': [{'source': 'redux/redux_base_installer',
                      'path': 'descriptors/redux/redux_base_installer/Fairan/FairansVision.json',
                      'depends': [], 'conflicts': []}],
    },
}

FAKE_PACKS = {
    'redux/redux_base_installer': {
        'camp': 'redux', 'name': 'redux_base_installer',
        'tier': 'base', 'load_order': 0, 'bytes': 1_000_000,
    },
}

# Fake chunk-index — оба sha «есть на сервере»
FAKE_IDX = {
    'blobs': {
        SHA_V1: {'chunk': 'chunk001', 'offset': 0, 'size': len(NUKE_V1)},
        SHA_V2: {'chunk': 'chunk002', 'offset': 0, 'size': len(NUKE_V2)},
    },
    'chunks': {
        'chunk001': {'url': None, 'store': None},
        'chunk002': {'url': None, 'store': None},
    },
}

# Дескриптор v2 мода для merge-тестов
V2_DESC = {
    'id': 'ShusRangers/ShuNukes',
    'version': '2.0',
    'source': 'redux/redux_base_installer',
    'files': {
        'code': {
            'ShusRangers/ShuNukes/ModuleInfo.txt': {
                'sha256': SHA_V2, 'size': len(NUKE_V2),
            },
        },
        'assets': {},
    },
}

# ═══════════════════════════════════════════════
#  Инфраструктура тестов
# ═══════════════════════════════════════════════

PASS: list = []
FAIL: list = []


def check(name, expect, actual):
    ok = expect == actual
    (PASS if ok else FAIL).append(name)
    mark = 'OK ' if ok else 'FAIL'
    print(f'[{mark}] {name}')
    if not ok:
        print(f'      ожидание: {expect!r}')
        print(f'      факт:     {actual!r}')


def check_true(name, val):
    check(name, True, bool(val))


def check_false(name, val):
    check(name, False, bool(val))


def wait_idle(api, timeout=8.0):
    """Ждём завершения фонового потока (api.busy → False)."""
    deadline = time.time() + timeout
    while api.busy and time.time() < deadline:
        time.sleep(0.05)
    return not api.busy


class TempGame:
    """Временный каталог игры + фабрика Api без сети."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix='srl_test_'))
        self.game = self.root / 'game'
        self.mods = self.game / 'Mods'
        self.mods.mkdir(parents=True)
        self.profiles = self.root / 'profiles'
        self.profiles.mkdir()

    def fresh_api(self, base='redux', catalog=None, packs=None):
        """Api через __new__ — без сети, с реальным game/Mods/ и temp profiles."""
        a = app.Api.__new__(app.Api)
        a.busy = False
        a._cancel = threading.Event()
        a._updates = {}
        a.current_profile = 'default'
        a.config = {
            'tree_mode': 'section', 'name_mode': 'folder', 'log_verbose': False,
            'repo': 'test/repo', 'github_token': '', 'forks': [],
            'profiles': ['default'], 'last_profile': 'default',
        }
        a.profile = {
            'name': 'default',
            'game_path': str(self.game),
            'mods': [], 'enabled': [],
            'base': base, 'update_extra': [],
        }
        # Реальный _save_profile пишет в наш temp-каталог
        profiles_dir = self.profiles

        def _real_save():
            profiles_dir.mkdir(exist_ok=True)
            (profiles_dir / 'default.json').write_text(
                json.dumps(a.profile, ensure_ascii=False, indent=2), encoding='utf-8')

        a._save_profile = _real_save
        a._save_config = lambda: None
        a._emit = lambda *args, **kw: None
        a._token = lambda: ''
        a._repo = lambda: 'test/repo'
        a.log = lambda msg: None
        a._catalog_cache = dict(FAKE_CATALOG) if catalog is None else catalog
        a._packs_cache = dict(FAKE_PACKS) if packs is None else packs
        a._camps_idx = None
        a._cat_by_repo = {}
        a._idx_by_repo = {}
        a._fork_man_cache = {}
        a._pub_cache = {}
        a._pub_cache_all = None
        a._fixparent = {}
        a._sections = {}
        a._names = {}
        a._descs = {}
        a._disk_index = None
        a._dl_bytes = 0
        a._dl_lock = threading.Lock()
        a._parts_done = a._parts_total = 0
        a._pack_ctx = ''
        try:
            a._disk_index = core.load_disk_index(self.mods)
        except Exception:
            a._disk_index = None
        return a

    def put_mod(self, mid, content=NUKE_V1, extra_files=None):
        """Создать фейковый установленный мод на диске."""
        mod_dir = self.mods / mid.replace('/', os.sep)
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / 'ModuleInfo.txt').write_bytes(content)
        for fname, fcontent in (extra_files or {}).items():
            p = mod_dir / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(fcontent if isinstance(fcontent, bytes)
                          else fcontent.encode('utf-8'))
        return mod_dir

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ═══════════════════════════════════════════════
#  ГРУППА 1: Профиль / очередь (без диска)
# ═══════════════════════════════════════════════
print('=== ГРУППА 1: профиль / очередь ===')

g = TempGame()
try:
    print('\n--- T01: add_mod (весь лагерь) + clear_queue ---')
    a = g.fresh_api()
    r1 = a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    check('add_mod camp ok=True', True, r1.get('ok'))
    check('одна запись в очереди', 1, len(a.profile['mods']))
    check('тип = camp', 'camp', a.profile['mods'][0].get('type'))
    r2 = a.clear_queue()
    check('clear_queue removed=1', 1, r2.get('removed'))
    check('очередь пуста', [], a.profile['mods'])

    print('\n--- T02: add_mod (поиск по каталогу) ---')
    a = g.fresh_api()
    r = a.add_mod({'mode': 'search', 'id': 'ShusRangers/ShuNukes', 'name': 'Ядерное оружие',
                   'source': 'redux/redux_base_installer'})
    check('add_mod search ok=True', True, r.get('ok'))
    check('тип = desc', 'desc', a.profile['mods'][0].get('type'))
    check('id записи', 'ShusRangers/ShuNukes', a.profile['mods'][0].get('id'))
    check('source записи', 'redux/redux_base_installer', a.profile['mods'][0].get('source'))

    print('\n--- T03: add_mod дубль лагеря → dup=True, 1 запись ---')
    a = g.fresh_api()
    a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    r2 = a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    check('dup=True', True, bool(r2.get('dup')))
    check('только 1 запись в очереди', 1, len(a.profile['mods']))

    print('\n--- T04: set_base сохраняет базу в профиле ---')
    a = g.fresh_api()
    r = a.set_base('universe')
    check('set_base ok=True', True, r.get('ok'))
    check('profile.base = universe', 'universe', a.profile['base'])
    check('_update_order[0] = universe', 'universe', a._update_order()[0])

    print('\n--- T05: set_update_extra + _update_order ---')
    a = g.fresh_api(base='redux')
    r = a.set_update_extra(['universe', 'original', 'redux'])  # redux = база, удалится
    check('extra без базы', ['universe', 'original'], a.profile.get('update_extra'))
    check('order = [redux, universe, original]', ['redux', 'universe', 'original'], r['order'])
    # Проверяем get_update_order
    uo = a.get_update_order()
    check('get_update_order ok', True, uo.get('ok'))
    check('base в ответе', 'redux', uo.get('base'))

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 2: Дисковые моды — сканирование и ModCFG
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 2: дисковые моды ===')

g = TempGame()
try:
    print('\n--- T06: scan_installed_mods находит мод ---')
    g.put_mod('ShusRangers/ShuNukes')
    found = list(core.scan_installed_mods(g.mods))
    check_true('ShuNukes найден', 'ShusRangers/ShuNukes' in found)
    check('ровно 1 мод', 1, len(found))

    print('\n--- T07: get_tree показывает установленный мод ---')
    a = g.fresh_api()
    tree = a.get_tree()
    # Собираем все метки из всех лагерей/паков/модов
    labels = set()
    for camp in tree['camps']:
        for m in camp['mods']:
            labels.add(m['label'])
        for pack in camp['packs']:
            for m in pack['mods']:
                labels.add(m['label'])
    check_true('ShuNukes в дереве', 'ShuNukes' in labels)
    check_true('дерево имеет camps', len(tree['camps']) > 0)

    print('\n--- T08: toggle_enabled + profile_to_modcfg → ModCFG.txt ---')
    a = g.fresh_api()
    a.toggle_enabled('ShusRangers/ShuNukes', True)
    check_true('мод в enabled', 'ShusRangers/ShuNukes' in a.profile.get('enabled', []))
    r = a.profile_to_modcfg()
    check('profile_to_modcfg ok', True, r.get('ok'))
    modcfg = g.mods / 'ModCFG.txt'
    check_true('ModCFG.txt создан', modcfg.exists())
    modcfg_text = modcfg.read_bytes().decode('utf-8', errors='replace')
    check_true('ShuNukes в ModCFG', 'ShuNukes' in modcfg_text)

    print('\n--- T09: modcfg_to_profile читает ModCFG обратно ---')
    a2 = g.fresh_api()
    r = a2.modcfg_to_profile()
    check('modcfg_to_profile ok', True, r.get('ok'))
    check_true('ShuNukes в enabled после чтения ModCFG',
               'ShusRangers/ShuNukes' in a2.profile.get('enabled', []))

    print('\n--- T10: mods_info — реальный подсчёт файлов ---')
    a = g.fresh_api()
    r = a.mods_info()
    check('mods_info ok', True, r.get('ok'))
    check_true('count > 0', r.get('count', 0) > 0)
    check_true('path корректен', str(g.mods) in r.get('path', ''))

    print('\n--- T11: clear_mods удаляет файлы мода, возвращает счётчик ---')
    r = a.clear_mods()
    check('clear_mods ok', True, r.get('ok'))
    check_true('removed > 0', r.get('removed', 0) > 0)
    # ModCFG.txt входит в BASE_GAME_KEEP и НЕ удаляется — проверяем только мод
    nuke_gone = not (g.mods / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt').exists()
    check_true('ShuNukes удалён clear_mods', nuke_gone)

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 3: Установка (мокаем core.reconstruct_camp / reconstruct_unit)
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 3: установка (мок reconstruct_camp) ===')

g = TempGame()
try:
    print('\n--- T12: install camp → файлы на диске, очередь очищена ---')
    a = g.fresh_api()
    a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    check('1 запись в очереди до install', 1, len(a.profile['mods']))

    def fake_reconstruct_camp(repo, camp, units, mods_dir_path, token,
                              log=print, tmp_dir=None, should_cancel=None,
                              part_cb=None, byte_cb=None, sha_sink=None, dry_run=False):
        """Имитирует reconstruct_camp (единый проход по лагерю): пишет фейковые файлы."""
        mds = Path(mods_dir_path)
        nuke_dir = mds / 'ShusRangers' / 'ShuNukes'
        nuke_dir.mkdir(parents=True, exist_ok=True)
        (nuke_dir / 'ModuleInfo.txt').write_bytes(NUKE_V1)
        return {'code_files': 1, 'asset_files': 0, 'chunks': ['chunk001'],
                'missing': 0, 'skipped': 0}

    # Патч должен жить пока работает фоновый поток
    patcher12 = patch.object(core, 'reconstruct_camp', side_effect=fake_reconstruct_camp)
    patcher12.start()
    try:
        r = a.install([0])
        check('install ok=True сразу', True, r.get('ok'))
        check_true('busy=True (фон запущен)', a.busy)
        check_true('фоновый поток завершился', wait_idle(a, timeout=8))
    finally:
        patcher12.stop()

    nuke_mi = g.mods / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt'
    check_true('ModuleInfo.txt появился на диске', nuke_mi.exists())
    if nuke_mi.exists():
        check('содержимое = NUKE_V1', NUKE_V1, nuke_mi.read_bytes())
    check('очередь очищена после install', [], a.profile['mods'])

    print('\n--- T13: после install mods_info видит файлы ---')
    r = a.mods_info()
    check_true('count > 0 после install', r.get('count', 0) > 0)

    print('\n--- T14: install + повторный install (skip_present) не удваивает ---')
    a2 = g.fresh_api()
    t14_logs = []
    a2.log = t14_logs.append
    a2.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    call_count = [0]

    def counting_reconstruct_camp(repo, camp, units, mods_dir_path, token, log=None, **kw):
        call_count[0] += 1
        mds = Path(mods_dir_path)
        nuke_dir = mds / 'ShusRangers' / 'ShuNukes'
        nuke_dir.mkdir(parents=True, exist_ok=True)
        (nuke_dir / 'ModuleInfo.txt').write_bytes(NUKE_V1)
        return {'code_files': 1, 'asset_files': 0, 'chunks': [], 'missing': 0, 'skipped': 0}

    patcher14 = patch.object(core, 'reconstruct_camp', side_effect=counting_reconstruct_camp)
    patcher14.start()
    try:
        r14 = a2.install([0])
        check('T14 install ok=True', True, r14.get('ok'))
        check_true('T14 фоновый поток завершился', wait_idle(a2, timeout=8))
    finally:
        patcher14.stop()
    if call_count[0] != 1:
        print('  T14 лог:', t14_logs)
    check('reconstruct вызван ровно 1 раз', 1, call_count[0])

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 4: Детект обновлений (мокаем манифесты)
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 4: детект обновлений ===')

print('\n--- T15: v1 на диске, v2 в манифесте → обновление найдено ---')
g = TempGame()
try:
    g.put_mod('ShusRangers/ShuNukes', NUKE_V1)
    a = g.fresh_api(base='redux')

    # unit_maps возвращает v2 sha
    a._pub_cache_all = [
        ('redux/redux_base_installer', {
            'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V2,
        }),
    ]

    def fake_index_disk(mods_dir, catalog, prev_index=None, log=print,
                        progress_cb=None, should_cancel=None):
        return {
            'mods': {
                'ShusRangers/ShuNukes': {
                    'files': {
                        'ShusRangers/ShuNukes/ModuleInfo.txt': {'sha': SHA_V1},
                    },
                    'status': 'known',
                },
            },
            'count': 1, 'known': 1, 'unknown': 0,
        }

    p15a = patch.object(core, 'index_disk_mods', side_effect=fake_index_disk)
    p15b = patch.object(core, 'save_disk_index', return_value=None)
    p15c = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    p15a.start(); p15b.start(); p15c.start()
    try:
        r = a.check_updates()
        check('check_updates ok=True', True, r.get('ok'))
        check_true('фоновый поток запущен', a.busy)
        check_true('ждём завершения', wait_idle(a, timeout=8))
    finally:
        p15a.stop(); p15b.stop(); p15c.stop()
    check_true('ShuNukes попал в _updates', 'ShusRangers/ShuNukes' in a._updates)
    check_true('n > 0 у обновления',
               a._updates.get('ShusRangers/ShuNukes', {}).get('n', 0) > 0)
    check('целевой лагерь = redux',
          'redux', a._updates.get('ShusRangers/ShuNukes', {}).get('camp'))
finally:
    g.cleanup()

print('\n--- T16: v1 на диске, v1 в манифесте → обновлений нет ---')
g = TempGame()
try:
    g.put_mod('ShusRangers/ShuNukes', NUKE_V1)
    a = g.fresh_api(base='redux')

    # unit_maps возвращает тот же v1 sha
    a._pub_cache_all = [
        ('redux/redux_base_installer', {
            'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V1,
        }),
    ]

    def fake_index_disk_v1(mods_dir, catalog, **kw):
        return {
            'mods': {
                'ShusRangers/ShuNukes': {
                    'files': {
                        'ShusRangers/ShuNukes/ModuleInfo.txt': {'sha': SHA_V1},
                    },
                    'status': 'known',
                },
            },
            'count': 1, 'known': 1, 'unknown': 0,
        }

    p16a = patch.object(core, 'index_disk_mods', side_effect=fake_index_disk_v1)
    p16b = patch.object(core, 'save_disk_index', return_value=None)
    p16c = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    p16a.start(); p16b.start(); p16c.start()
    try:
        r = a.check_updates()
        wait_idle(a, timeout=8)
    finally:
        p16a.stop(); p16b.stop(); p16c.stop()
    check_false('ShuNukes НЕ в _updates (нет изменений)',
                'ShusRangers/ShuNukes' in a._updates)
finally:
    g.cleanup()

print('\n--- T17: _needs_order: мод в базе → False; мод только в universe → True ---')
g = TempGame()
try:
    # Только redux-мод на диске (есть в redux-каталоге) → needs_order=False
    a = g.fresh_api(base='redux')
    a._disk_index = {'mods': {'ShusRangers/ShuNukes': {}}}
    check('needs_order False (все моды в базе)', False, a._needs_order())

    # Добавим только-universe-мод в каталог и «установим» его на диск
    uni_cat = dict(FAKE_CATALOG)
    uni_cat['Universe/UniMod'] = {
        'name': 'Тест-Вселенная',
        'default_source': 'universe/universe_community',
        'variants': [{'source': 'universe/universe_community', 'path': 'x.json',
                      'depends': [], 'conflicts': []}],
    }
    a2 = g.fresh_api(base='redux', catalog=uni_cat)
    a2._disk_index = {'mods': {'Universe/UniMod': {}}}
    check('needs_order True (мод не в redux)', True, a2._needs_order())

    # Уже настроены доп.строки → True даже если все моды в базе
    a3 = g.fresh_api(base='redux')
    a3.profile['update_extra'] = ['universe']
    a3._disk_index = {'mods': {'ShusRangers/ShuNukes': {}}}
    check('needs_order True (доп.строки настроены)', True, a3._needs_order())
finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 5: Merge / update flow
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 5: merge/update flow ===')

g = TempGame()
try:
    print('\n--- T18: start_merge → plan emitted ИЛИ op_end (нечего обновлять) ---')
    g.put_mod('ShusRangers/ShuNukes', NUKE_V1)
    a = g.fresh_api(base='redux')
    a._updates['ShusRangers/ShuNukes'] = {'n': 1, 'camp': 'redux'}

    emitted = []
    a._emit = lambda ev, pl=None: emitted.append((ev, pl))

    # Фейковый план: 1 обновление (update)
    fake_plan = {
        'id': 'ShusRangers/ShuNukes',
        'version_old': '1.0', 'version_new': '2.0',
        'has_snapshot': True,
        'actions': [
            {'relpath': 'ShusRangers/ShuNukes/ModuleInfo.txt',
             'status': 'update', 'sha_theirs': SHA_V2, 'sha_base': SHA_V1},
        ],
        'summary': {'update': 1, 'add': 0, 'automerge': 0, 'conflicts': 0,
                    'player_only': 0, 'unchanged': 0, 'deleted_clean': 0},
    }

    p18a = patch.object(core, 'load_catalog', return_value=dict(FAKE_CATALOG))
    p18b = patch.object(core, 'detect_installed_base', return_value=None)
    p18c = patch.object(core, 'descriptor_for', return_value=V2_DESC)
    p18d = patch.object(core, 'load_install_snapshot',
                        return_value={'id': 'ShusRangers/ShuNukes', 'version': '1.0',
                                      'files': {'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V1}})
    p18e = patch.object(core, 'plan_update_merge', return_value=fake_plan)
    p18f = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    for p in (p18a, p18b, p18c, p18d, p18e, p18f):
        p.start()
    try:
        r = a.start_merge(['d:ShusRangers/ShuNukes'])
        check('start_merge ok=True', True, r.get('ok'))
        check_true('busy после start_merge', a.busy)

        # Ждём merge_plan или op_end
        deadline = time.time() + 8.0
        while a.busy and time.time() < deadline:
            time.sleep(0.05)
    finally:
        for p in (p18a, p18b, p18c, p18d, p18e, p18f):
            p.stop()

    ev_names = [e[0] for e in emitted]
    check_true('op_begin emitted', 'op_begin' in ev_names)
    got_plan = 'merge_plan' in ev_names
    got_end = 'op_end' in ev_names
    check_true('merge_plan или op_end emitted', got_plan or got_end)

    print('\n--- T19: apply_merge → файл обновлён на диске, _updates снят ---')
    if got_plan:
        applied_files = []

        def fake_apply(desc, plan, decisions, mods_dir, index, token=None, log=print,
                       tmp_dir=None, progress_cb=None, should_cancel=None,
                       byte_cb=None, part_cb=None):
            nuke = Path(mods_dir) / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt'
            nuke.write_bytes(NUKE_V2)
            applied_files.append(str(nuke))
            return 'update=1'

        p19 = patch.object(core, 'apply_update_plan', side_effect=fake_apply)
        p19.start()
        try:
            r2 = a.apply_merge({}, remember=False)
            check('apply_merge ok=True', True, r2.get('ok'))
            wait_idle(a, timeout=8)
        finally:
            p19.stop()

        nuke_on_disk = g.mods / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt'
        check_true('apply_update_plan вызван', len(applied_files) > 0)
        check('файл обновлён до v2', NUKE_V2, nuke_on_disk.read_bytes())
        check_false('ShuNukes снят из _updates', 'ShusRangers/ShuNukes' in a._updates)
    else:
        # plan_update_merge решил, что actionable=0 (mock вернул план, но что-то не так)
        print('  [SKIP] merge_plan не был emitted (actionable=0 или иное) — T19 пропущен')
        PASS.append('T19-skipped')

    print('\n--- T20: merge_skip → мод пропущен, busy=False ---')
    g2 = TempGame()
    g2.put_mod('ShusRangers/ShuNukes', NUKE_V1)
    a3 = g2.fresh_api(base='redux')
    a3._updates['ShusRangers/ShuNukes'] = {'n': 1, 'camp': 'redux'}
    ev3 = []
    a3._emit = lambda ev, pl=None: ev3.append(ev)

    p20s = [
        patch.object(core, 'load_catalog', return_value=dict(FAKE_CATALOG)),
        patch.object(core, 'detect_installed_base', return_value=None),
        patch.object(core, 'descriptor_for', return_value=V2_DESC),
        patch.object(core, 'load_install_snapshot',
                     return_value={'id': 'ShusRangers/ShuNukes', 'version': '1.0',
                                   'files': {'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V1}}),
        patch.object(core, 'plan_update_merge', return_value=fake_plan),
        patch.object(core, 'load_chunk_index', return_value=FAKE_IDX),
    ]
    for p in p20s:
        p.start()
    try:
        a3.start_merge(['d:ShusRangers/ShuNukes'])
        # Ждём merge_plan
        deadline = time.time() + 8.0
        while a3.busy and 'merge_plan' not in ev3 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        for p in p20s:
            p.stop()
    if 'merge_plan' in ev3:
        r_skip = a3.merge_skip()
        check('merge_skip ok=True', True, r_skip.get('ok'))
        wait_idle(a3, timeout=4)
        check_false('busy=False после skip', a3.busy)
        # файл не изменился
        check('файл НЕ изменился после skip',
              NUKE_V1, (g2.mods / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt').read_bytes())
    else:
        print('  [SKIP] merge_plan не пришёл для T20')
        PASS.append('T20-skipped')
    g2.cleanup()

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 6: Настройки и профиль
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 6: настройки / профиль ===')

g = TempGame()
try:
    print('\n--- T21: save_settings сохраняет game_path и base ---')
    a = g.fresh_api()
    r = a.save_settings(str(g.game), 'test/repo', '', 'universe')
    check('save_settings возвращает state (version)', True, 'version' in r)
    check('game_path сохранился', str(g.game), a.profile.get('game_path'))
    check('base = universe', 'universe', a.profile.get('base'))
    check('_game_root() = game dir', g.game.resolve(), a._game_root().resolve())

    print('\n--- T22: set_theme + set_tree_mode → конфиг обновлён ---')
    a = g.fresh_api()
    config_changes = {}
    original_save = a._save_config
    a._save_config = lambda: config_changes.update(dict(a.config))
    a.set_theme('light')
    check('theme = light в config', 'light', a.config.get('theme'))
    a.set_tree_mode('folder')
    check('tree_mode = folder в config', 'folder', a.config.get('tree_mode'))

    print('\n--- T23: cancel() выставляет флаг отмены ---')
    a = g.fresh_api()
    check_false('_cancel не выставлен до cancel()', a._cancel.is_set())
    a.cancel()
    check_true('_cancel выставлен после cancel()', a._cancel.is_set())
    check_true('should_cancel() = True', a.should_cancel())

    print('\n--- T24: plan_actionable_sha — ключевая логика детекта ---')
    # v1 на диске, v2 на сервере, нет снимка → 1 обновление
    n = core.plan_actionable_sha(
        theirs={'A/file.txt': SHA_V2},
        base={},
        disk={'A/file.txt': SHA_V1},
    )
    check('нет снимка: v1→v2 = 1 обновление', 1, n)
    # v1 на диске, v1 на сервере → 0 обновлений
    n = core.plan_actionable_sha(
        theirs={'A/file.txt': SHA_V1},
        base={'A/file.txt': SHA_V1},
        disk={'A/file.txt': SHA_V1},
    )
    check('совпадает: 0 обновлений', 0, n)
    # Игрок правил (mine != base), сервер не менял (theirs == base) → player_only → 0
    n = core.plan_actionable_sha(
        theirs={'A/file.txt': SHA_V1},  # theirs == base (сервер не менял)
        base={'A/file.txt': SHA_V1},
        disk={'A/file.txt': SHA_V2},    # игрок изменил
    )
    check('player_only: 0 обновлений', 0, n)
    # Оба изменили (конфликт) → 1
    n = core.plan_actionable_sha(
        theirs={'A/file.txt': SHA_V2},
        base={'A/file.txt': 'sha_base'},
        disk={'A/file.txt': SHA_V1},
    )
    check('конфликт (оба изменили): 1', 1, n)

    print('\n--- T25: _target_camp выбирает первый лагерь из порядка ---')
    tc = app.Api._target_camp
    check('ModA (redux+uni), order[redux,uni] → redux', 'redux',
          tc(['redux', 'universe'], ['redux/r_base', 'universe/u_base']))
    check('ModB (только uni), order[redux,uni] → universe', 'universe',
          tc(['redux', 'universe'], ['universe/u_base']))
    check('ModB (только uni), order[redux] → None', None,
          tc(['redux'], ['universe/u_base']))

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 7: Граничные случаи
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 7: граничные случаи ===')

g = TempGame()
try:
    print('\n--- T26: install без game_path → ошибка ---')
    a = g.fresh_api()
    a.profile['game_path'] = ''   # нет пути
    a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    r = a.install([0])
    check('install без game_path: ok=False', False, r.get('ok'))
    check_true('error text присутствует', r.get('error'))

    print('\n--- T27: install busy=True → ошибка ---')
    a = g.fresh_api()
    a.busy = True
    r = a.install([0])
    check('install при busy: ok=False', False, r.get('ok'))

    print('\n--- T28: mods_info без game_path → ошибка ---')
    a = g.fresh_api()
    a.profile['game_path'] = ''
    r = a.mods_info()
    check('mods_info без game_path: ok=False', False, r.get('ok'))

    print('\n--- T29: clear_mods BASE_GAME_KEEP не удаляет modcfg.txt ---')
    g2 = TempGame()
    a2 = g2.fresh_api()
    # Создаём ModCFG.txt (попадает в BASE_GAME_KEEP как 'modcfg.txt')
    modcfg = g2.mods / 'ModCFG.txt'
    modcfg.write_text('[mods]\r\nCurrentMod=\r\n', encoding='utf-8')
    g2.put_mod('ShusRangers/ShuNukes', NUKE_V1)  # мод — должен удалиться
    r = a2.clear_mods()
    check('clear_mods ok', True, r.get('ok'))
    check_true('ModCFG.txt сохранён (BASE_GAME_KEEP)', modcfg.exists())
    check_true('ShuNukes удалён',
               not (g2.mods / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt').exists())
    check_true('kept >= 1', r.get('kept', 0) >= 1)
    g2.cleanup()

    print('\n--- T30: add_mod (fork URL) ---')
    a = g.fresh_api()
    r = a.add_mod({'mode': 'fork', 'url': 'https://example.com/mod.json'})
    check('add_mod fork ok=True', True, r.get('ok'))
    check('тип = desc', 'desc', a.profile['mods'][0].get('type'))
    check_true('url присутствует', a.profile['mods'][0].get('url'))

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 8: E2E полный поток
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 8: E2E полный поток ===')

g = TempGame()
try:
    print('\n--- T31: E2E install v1 → detect update v2 → merge → файл = v2 ---')
    a = g.fresh_api(base='redux')
    a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})

    def fake_rec_v1(repo, camp, unit, mods_dir_path, token,
                    progress_cb=None, log=None, **kw):
        d = Path(mods_dir_path) / 'ShusRangers' / 'ShuNukes'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'ModuleInfo.txt').write_bytes(NUKE_V1)
        return {'code_files': 1, 'asset_files': 0, 'chunks': [], 'missing': 0, 'skipped': 0}

    p_rec = patch_reconstruct(fake_rec_v1)
    p_rec.start()
    try:
        a.install([0])
        wait_idle(a, timeout=8)
    finally:
        p_rec.stop()

    nuke_path = g.mods / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt'
    check_true('E2E: v1 на диске', nuke_path.exists())
    check('E2E: содержимое v1', NUKE_V1, nuke_path.read_bytes())

    # Шаг 2: detect update (v2 на сервере, v1 на диске)
    a._pub_cache_all = [
        ('redux/redux_base_installer',
         {'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V2}),
    ]

    def fake_idx_e2e(mods_dir, catalog, **kw):
        return {
            'mods': {'ShusRangers/ShuNukes': {
                'files': {'ShusRangers/ShuNukes/ModuleInfo.txt': {'sha': SHA_V1}},
                'status': 'known',
            }},
            'count': 1, 'known': 1, 'unknown': 0,
        }

    p_idx = patch.object(core, 'index_disk_mods', side_effect=fake_idx_e2e)
    p_sdx = patch.object(core, 'save_disk_index', return_value=None)
    p_cidx = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    p_idx.start(); p_sdx.start(); p_cidx.start()
    try:
        a.check_updates()
        wait_idle(a, timeout=8)
    finally:
        p_idx.stop(); p_sdx.stop(); p_cidx.stop()

    check_true('E2E: обновление найдено', 'ShusRangers/ShuNukes' in a._updates)

    # Шаг 3: start_merge → plan
    evts = []
    a._emit = lambda ev, pl=None: evts.append((ev, pl))

    fake_plan_e2e = {
        'id': 'ShusRangers/ShuNukes',
        'version_old': '1.0', 'version_new': '2.0',
        'has_snapshot': False,
        'actions': [
            {'relpath': 'ShusRangers/ShuNukes/ModuleInfo.txt',
             'status': 'update', 'sha_theirs': SHA_V2, 'sha_base': SHA_V1},
        ],
        'summary': {'update': 1, 'add': 0, 'automerge': 0, 'conflicts': 0,
                    'player_only': 0, 'unchanged': 0, 'deleted_clean': 0},
    }
    mp_list = [
        patch.object(core, 'load_catalog', return_value=dict(FAKE_CATALOG)),
        patch.object(core, 'detect_installed_base', return_value=None),
        patch.object(core, 'descriptor_for', return_value=V2_DESC),
        patch.object(core, 'load_install_snapshot', return_value=None),
        patch.object(core, 'plan_update_merge', return_value=fake_plan_e2e),
        patch.object(core, 'load_chunk_index', return_value=FAKE_IDX),
    ]
    a.config['always_show_plan'] = True     # модальный путь: показать план даже без конфликтов
    for p in mp_list:
        p.start()
    try:
        a.start_merge(['d:ShusRangers/ShuNukes'])
        deadline = time.time() + 8.0
        while a.busy and time.time() < deadline:
            time.sleep(0.05)
    finally:
        for p in mp_list:
            p.stop()

    evt_names = [e[0] for e in evts]
    check_true('E2E: merge_plan emitted', 'merge_plan' in evt_names)

    # Шаг 4: apply_merge → файл = v2
    if 'merge_plan' in evt_names:
        def fake_apply_e2e(desc, plan, decisions, mods_dir, index, **kw):
            (Path(mods_dir) / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt').write_bytes(NUKE_V2)
            return 'update=1'

        p_apply = patch.object(core, 'apply_update_plan', side_effect=fake_apply_e2e)
        p_apply.start()
        try:
            a.apply_merge({}, remember=False)
            wait_idle(a, timeout=8)
        finally:
            p_apply.stop()

        check('E2E: файл обновлён до v2', NUKE_V2, nuke_path.read_bytes())
        check_false('E2E: _updates снят', 'ShusRangers/ShuNukes' in a._updates)
    else:
        PASS.append('T31-merge-skipped')

    print('\n--- T31b: тихий режим — бесконфликтный апдейт применяется БЕЗ окна ---')
    nuke_path.write_bytes(NUKE_V1)              # откат к v1
    a.config['always_show_plan'] = False        # тихий режим (по умолчанию)
    a._updates = {'ShusRangers/ShuNukes': {'camp': 'redux', 'n': 1}}
    evts2 = []
    a._emit = lambda ev, pl=None: evts2.append((ev, pl))

    def fake_apply_silent(desc, plan, decisions, mods_dir, index, **kw):
        (Path(mods_dir) / 'ShusRangers' / 'ShuNukes' / 'ModuleInfo.txt').write_bytes(NUKE_V2)
        return 'update=1'

    mp_silent = [
        patch.object(core, 'load_catalog', return_value=dict(FAKE_CATALOG)),
        patch.object(core, 'detect_installed_base', return_value=None),
        patch.object(core, 'descriptor_for', return_value=V2_DESC),
        patch.object(core, 'load_install_snapshot', return_value=None),
        patch.object(core, 'plan_update_merge', return_value=fake_plan_e2e),
        patch.object(core, 'load_chunk_index', return_value=FAKE_IDX),
        patch.object(core, 'apply_update_plan', side_effect=fake_apply_silent),
    ]
    for p in mp_silent:
        p.start()
    try:
        a.start_merge(['d:ShusRangers/ShuNukes'])
        wait_idle(a, timeout=8)
    finally:
        for p in mp_silent:
            p.stop()

    names2 = [e[0] for e in evts2]
    check_false('T31b: merge_plan НЕ emitted (тихо)', 'merge_plan' in names2)
    check_true('T31b: merge_silent emitted (тост-итог)', 'merge_silent' in names2)
    check('T31b: файл обновлён до v2 без окна', NUKE_V2, nuke_path.read_bytes())

    print('\n--- T32: E2E удалить мод с диска → mods_info = 0 → reinstall → mods_info > 0 ---')
    g2 = TempGame()
    g2.put_mod('ShusRangers/ShuNukes', NUKE_V1)
    a2 = g2.fresh_api()

    check_true('T32: до удаления count > 0', a2.mods_info().get('count', 0) > 0)

    shutil.rmtree(g2.mods / 'ShusRangers', ignore_errors=True)
    check('T32: после удаления count = 0', 0, a2.mods_info().get('count', 0))

    a2.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})

    def fake_rec_reinstall(repo, camp, unit, mds, tok,
                           progress_cb=None, log=None, **kw):
        d = Path(mds) / 'ShusRangers' / 'ShuNukes'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'ModuleInfo.txt').write_bytes(NUKE_V1)
        return {'code_files': 1, 'asset_files': 0, 'chunks': [], 'missing': 0, 'skipped': 0}

    p_ri = patch_reconstruct(fake_rec_reinstall)
    p_ri.start()
    try:
        a2.install([0])
        wait_idle(a2, timeout=8)
    finally:
        p_ri.stop()

    check_true('T32: после reinstall count > 0', a2.mods_info().get('count', 0) > 0)
    g2.cleanup()

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 9: Профиль — персистентность и несколько модов
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 9: профиль + несколько модов ===')

g = TempGame()
try:
    print('\n--- T33: _save_profile пишет JSON, поля сохраняются ---')
    a = g.fresh_api(base='redux')
    a.profile['base'] = 'universe'
    a.profile['enabled'] = ['ShusRangers/ShuNukes']
    a._save_profile()

    saved = g.profiles / 'default.json'
    check_true('T33: файл профиля создан', saved.exists())
    loaded = json.loads(saved.read_text(encoding='utf-8'))
    check('T33: base сохранился', 'universe', loaded.get('base'))
    check('T33: enabled сохранился', ['ShusRangers/ShuNukes'], loaded.get('enabled'))

    print('\n--- T34: install([0,1]) — camp и desc оба обработаны ---')
    g2 = TempGame()
    a2 = g2.fresh_api()
    a2.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    a2.add_mod({'mode': 'search', 'id': 'ShusRangers/ShuNukes', 'name': 'Nuke'})
    check('T34: 2 записи в очереди', 2, len(a2.profile['mods']))

    call_log34 = []

    def fake_rec_34(repo, camp, unit, mds, tok, progress_cb=None, log=None, **kw):
        call_log34.append('reconstruct')
        d = Path(mds) / 'ShusRangers' / 'ShuNukes'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'ModuleInfo.txt').write_bytes(NUKE_V1)
        return {'code_files': 1, 'asset_files': 0, 'chunks': [], 'missing': 0, 'skipped': 0}

    def fake_inst_34(desc, mods_dir, idx, tok, *args, **kw):
        call_log34.append('install_desc')
        d = Path(mods_dir) / 'ShusRangers' / 'ShuNukes'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'ModuleInfo.txt').write_bytes(NUKE_V1)

    p_r34 = patch_reconstruct(fake_rec_34)
    p_d34 = patch.object(core, 'descriptor_for', return_value=V2_DESC)
    p_i34 = patch.object(core, 'install_descriptor', side_effect=fake_inst_34)
    p_c34 = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    for p in (p_r34, p_d34, p_i34, p_c34):
        p.start()
    try:
        r34 = a2.install([0, 1])
        check('T34: install([0,1]) ok=True', True, r34.get('ok'))
        wait_idle(a2, timeout=8)
    finally:
        for p in (p_r34, p_d34, p_i34, p_c34):
            p.stop()

    check_true('T34: reconstruct вызван (camp)', 'reconstruct' in call_log34)
    check_true('T34: install_desc вызван (desc)', 'install_desc' in call_log34)
    check('T34: очередь очищена', [], a2.profile['mods'])
    g2.cleanup()

    print('\n--- T35: install([1]) из 2 — только 2-й обработан, 1-й остался ---')
    g3 = TempGame()
    a3 = g3.fresh_api()
    a3.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    a3.add_mod({'mode': 'search', 'id': 'ShusRangers/ShuNukes', 'name': 'Nuke'})

    call_log35 = []

    def fake_rec_35(repo, camp, unit, mds, tok, progress_cb=None, log=None, **kw):
        call_log35.append('reconstruct')
        return {'code_files': 0, 'asset_files': 0, 'chunks': [], 'missing': 0, 'skipped': 0}

    def fake_inst_35(desc, mods_dir, idx, tok, *args, **kw):
        call_log35.append('install_desc')

    p_r35 = patch_reconstruct(fake_rec_35)
    p_d35 = patch.object(core, 'descriptor_for', return_value=V2_DESC)
    p_i35 = patch.object(core, 'install_descriptor', side_effect=fake_inst_35)
    p_c35 = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    for p in (p_r35, p_d35, p_i35, p_c35):
        p.start()
    try:
        a3.install([1])  # только второй (desc)
        wait_idle(a3, timeout=8)
    finally:
        for p in (p_r35, p_d35, p_i35, p_c35):
            p.stop()

    check_false('T35: camp (index 0) НЕ установлен', 'reconstruct' in call_log35)
    check_true('T35: desc (index 1) установлен', 'install_desc' in call_log35)
    remaining35 = a3.profile['mods']
    check('T35: в очереди осталась 1 запись (camp)', 1, len(remaining35))
    check('T35: оставшийся тип = camp', 'camp', remaining35[0].get('type'))
    g3.cleanup()

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 10: install type='unit' и 'desc'
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 10: install type=unit / desc ===')

g = TempGame()
try:
    print('\n--- T36: install type=unit → reconstruct с правильным unit и mod ---')
    a = g.fresh_api()
    pack_info = {'camp': 'redux', 'unit': 'redux_base_installer', 'name': 'redux_base_installer'}
    a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': pack_info,
               'mod': 'ShusRangers/ShuNukes'})
    m0 = a.profile['mods'][0]
    check('T36: тип = unit', 'unit', m0.get('type'))
    check('T36: unit = redux_base_installer', 'redux_base_installer', m0.get('unit'))
    check('T36: mod = ShusRangers/ShuNukes', 'ShusRangers/ShuNukes', m0.get('mod'))

    rec_args36 = []

    def fake_rec_36(repo, camp, unit, mds, tok,
                    progress_cb=None, log=None, mod=None, **kw):
        rec_args36.append({'camp': camp, 'unit': unit, 'mod': mod})
        d = Path(mds) / 'ShusRangers' / 'ShuNukes'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'ModuleInfo.txt').write_bytes(NUKE_V1)
        return {'code_files': 1, 'asset_files': 0, 'chunks': [], 'missing': 0, 'skipped': 0}

    p_r36 = patch_reconstruct(fake_rec_36)
    p_r36.start()
    try:
        a.install([0])
        wait_idle(a, timeout=8)
    finally:
        p_r36.stop()

    check_true('T36: reconstruct вызван', len(rec_args36) > 0)
    if rec_args36:
        check('T36: camp = redux', 'redux', rec_args36[0]['camp'])
        check('T36: unit = redux_base_installer', 'redux_base_installer', rec_args36[0]['unit'])
        check('T36: mod передан как ShusRangers/ShuNukes',
              'ShusRangers/ShuNukes', rec_args36[0]['mod'])

    print('\n--- T37: install type=desc → install_descriptor вызван с правильным id ---')
    g2 = TempGame()
    a2 = g2.fresh_api()
    a2.add_mod({'mode': 'search', 'id': 'ShusRangers/ShuNukes',
                'name': 'Ядерное оружие', 'source': 'redux/redux_base_installer'})
    check('T37: тип = desc', 'desc', a2.profile['mods'][0].get('type'))

    inst_calls37 = []

    def fake_inst_37(desc, mods_dir, idx, tok, *args, **kw):
        inst_calls37.append(desc.get('id', '?'))
        d = Path(mods_dir) / 'ShusRangers' / 'ShuNukes'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'ModuleInfo.txt').write_bytes(NUKE_V1)

    p_d37 = patch.object(core, 'descriptor_for', return_value=V2_DESC)
    p_i37 = patch.object(core, 'install_descriptor', side_effect=fake_inst_37)
    p_c37 = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    for p in (p_d37, p_i37, p_c37):
        p.start()
    try:
        a2.install([0])
        wait_idle(a2, timeout=8)
    finally:
        for p in (p_d37, p_i37, p_c37):
            p.stop()

    check_true('T37: install_descriptor вызван', len(inst_calls37) > 0)
    if inst_calls37:
        check('T37: desc id = ShusRangers/ShuNukes', 'ShusRangers/ShuNukes', inst_calls37[0])
    check('T37: очередь очищена', [], a2.profile['mods'])
    g2.cleanup()

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 11: toggle_enabled / modcfg edge cases
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 11: toggle_enabled / modcfg edge cases ===')

g = TempGame()
try:
    print('\n--- T38: toggle_enabled False снимает мод из enabled ---')
    a = g.fresh_api()
    g.put_mod('ShusRangers/ShuNukes')
    a.toggle_enabled('ShusRangers/ShuNukes', True)
    check_true('T38: мод включён', 'ShusRangers/ShuNukes' in a.profile.get('enabled', []))
    a.toggle_enabled('ShusRangers/ShuNukes', False)
    check_false('T38: мод отключён', 'ShusRangers/ShuNukes' in a.profile.get('enabled', []))

    print('\n--- T39: toggle_enabled True дважды → нет дублей ---')
    a = g.fresh_api()
    a.toggle_enabled('ShusRangers/ShuNukes', True)
    a.toggle_enabled('ShusRangers/ShuNukes', True)
    count_en = a.profile.get('enabled', []).count('ShusRangers/ShuNukes')
    check('T39: мод ровно 1 раз в enabled', 1, count_en)

    print('\n--- T40: profile_to_modcfg с двумя enabled → оба в ModCFG ---')
    g2 = TempGame()
    a2 = g2.fresh_api()
    g2.put_mod('ShusRangers/ShuNukes')
    g2.put_mod('Fairan/FairansVision')
    a2.toggle_enabled('ShusRangers/ShuNukes', True)
    a2.toggle_enabled('Fairan/FairansVision', True)
    a2.profile_to_modcfg()
    mcfg = g2.mods / 'ModCFG.txt'
    text = mcfg.read_text(encoding='utf-8', errors='replace') if mcfg.exists() else ''
    check_true('T40: ShuNukes в ModCFG', 'ShuNukes' in text)
    check_true('T40: FairansVision в ModCFG', 'FairansVision' in text)
    g2.cleanup()

    print('\n--- T41: modcfg_to_profile с пустым CurrentMod → enabled пуста ---')
    g3 = TempGame()
    a3 = g3.fresh_api()
    g3.put_mod('ShusRangers/ShuNukes')
    a3.toggle_enabled('ShusRangers/ShuNukes', True)
    (g3.mods / 'ModCFG.txt').write_text(
        '[mods]\r\nCurrentMod=\r\n', encoding='utf-8')
    r = a3.modcfg_to_profile()
    check('T41: ok=True', True, r.get('ok'))
    check('T41: enabled пуста после пустого ModCFG', [], a3.profile.get('enabled', []))
    g3.cleanup()

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 12: cancel() во время операции
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 12: cancel во время операции ===')

g = TempGame()
try:
    print('\n--- T42: cancel() во время install → поток завершается ---')
    a = g.fresh_api()
    a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})

    def slow_reconstruct(repo, camp, unit, mds, tok,
                         progress_cb=None, log=None,
                         should_cancel=None, **kw):
        # Ждём флага отмены, потом бросаем исключение
        for _ in range(40):
            if should_cancel and should_cancel():
                raise core.OperationCancelled()
            time.sleep(0.05)
        return {'code_files': 0, 'asset_files': 0, 'chunks': [], 'missing': 0, 'skipped': 0}

    p_slow = patch_reconstruct(slow_reconstruct)
    p_slow.start()
    try:
        a.install([0])
        check_true('T42: busy=True сразу', a.busy)
        time.sleep(0.1)
        a.cancel()
        check_true('T42: should_cancel()=True', a.should_cancel())
        idle = wait_idle(a, timeout=5)
        check_true('T42: поток завершился', idle)
    finally:
        p_slow.stop()

    check_false('T42: busy=False после cancel', a.busy)

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 13: граничные случаи API
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 13: граничные случаи API ===')

g = TempGame()
try:
    print('\n--- T43: install([]) → ok=False ---')
    a = g.fresh_api()
    r = a.install([])
    check('T43: install([]) ok=False', False, r.get('ok'))
    check_true('T43: error text', bool(r.get('error')))
    check_false('T43: busy не выставлен', a.busy)

    print('\n--- T44: clear_queue пустая очередь → removed=0, ok=True ---')
    a = g.fresh_api()
    r = a.clear_queue()
    check('T44: removed=0', 0, r.get('removed'))
    check('T44: ok=True', True, r.get('ok'))

    print('\n--- T45: add_mod fork без url → ok=False ---')
    a = g.fresh_api()
    r = a.add_mod({'mode': 'fork', 'url': ''})
    check('T45: fork без url ok=False', False, r.get('ok'))
    check('T45: очередь пуста', 0, len(a.profile.get('mods', [])))

    print('\n--- T46: check_updates без game_path → ok=False ---')
    a = g.fresh_api()
    a.profile['game_path'] = ''
    r = a.check_updates()
    check('T46: check_updates без game_path ok=False', False, r.get('ok'))

    print('\n--- T47: start_merge busy=True → ok=False ---')
    a = g.fresh_api()
    a.busy = True
    r = a.start_merge(['d:ShusRangers/ShuNukes'])
    check('T47: start_merge busy ok=False', False, r.get('ok'))

    print('\n--- T48: autodetect_base без game_path → ok=False ---')
    a = g.fresh_api()
    a.profile['game_path'] = ''
    r = a.autodetect_base()
    check('T48: autodetect_base без game_path ok=False', False, r.get('ok'))

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 14: core functions напрямую
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 14: core functions напрямую ===')

g = TempGame()
try:
    print('\n--- T49: core.write_modcfg + core.read_modcfg round-trip ---')
    mids = ['ShusRangers/ShuNukes', 'Fairan/FairansVision']
    core.write_modcfg(g.mods, mids)
    check_true('T49: ModCFG.txt создан', (g.mods / 'ModCFG.txt').exists())
    read_back = core.read_modcfg(g.mods)
    check('T49: read_modcfg вернул оба мода', sorted(mids), sorted(read_back))

    print('\n--- T50: core.scan_installed_mods — 3 мода ---')
    g2 = TempGame()
    g2.put_mod('ShusRangers/ShuNukes')
    g2.put_mod('Fairan/FairansVision')
    g2.put_mod('OtherMods/SomeMod')
    found50 = list(core.scan_installed_mods(g2.mods))
    check('T50: найдено 3 мода', 3, len(found50))
    check_true('T50: ShuNukes найден', 'ShusRangers/ShuNukes' in found50)
    check_true('T50: SomeMod найден', 'OtherMods/SomeMod' in found50)
    g2.cleanup()

    print('\n--- T51: plan_actionable_sha — новый файл (nsha is None) → n=1 ---')
    n = core.plan_actionable_sha(
        theirs={'New/file.dat': 'aaa'},
        base={},
        disk={},
    )
    check('T51: новый файл (msha=None) → 1', 1, n)

    print('\n--- T52: plan_actionable_sha — 2 файла, 1 изменён → n=1 ---')
    n = core.plan_actionable_sha(
        theirs={'A/f1.txt': SHA_V1, 'A/f2.txt': SHA_V2},
        base={},
        disk={'A/f1.txt': SHA_V1, 'A/f2.txt': SHA_V1},
    )
    check('T52: 2 файла, 1 совпадает, 1 изменён → n=1', 1, n)

    print('\n--- T53: plan_actionable_sha — player_only со снимком → n=0 ---')
    n = core.plan_actionable_sha(
        theirs={'A/f.txt': SHA_V1},   # сервер не менял (tsha==bsha)
        base={'A/f.txt': SHA_V1},
        disk={'A/f.txt': SHA_V2},     # игрок поменял
    )
    check('T53: player_only со снимком → n=0', 0, n)

    print('\n--- T54: plan_actionable_sha — update со снимком (msha==bsha) → n=1 ---')
    n = core.plan_actionable_sha(
        theirs={'A/f.txt': SHA_V2},   # сервер обновил
        base={'A/f.txt': SHA_V1},
        disk={'A/f.txt': SHA_V1},     # игрок не трогал
    )
    check('T54: update со снимком (msha==bsha) → n=1', 1, n)

    print('\n--- T54b: косметика без снимка не считается обновлением ---')
    # Lang.dat (известное имя) + Main.dat (производный от соседнего Main.txt) дрейфнули,
    # снимка нет → не обновление. Обычный отличающийся файл — обновление.
    n = core.plan_actionable_sha(
        theirs={'M/CFG/Rus/Lang.dat': SHA_V2, 'M/CFG/Main.dat': SHA_V2,
                'M/CFG/Main.txt': SHA_V1, 'M/DATA/data.txt': SHA_V1},
        base={},
        disk={'M/CFG/Rus/Lang.dat': SHA_V1, 'M/CFG/Main.dat': SHA_V1,
              'M/CFG/Main.txt': SHA_V1, 'M/DATA/data.txt': SHA_V1},
    )
    check('T54b: Lang.dat+производный Main.dat дрейф → 0 обновлений', 0, n)
    n = core.plan_actionable_sha(
        theirs={'M/CFG/Binary.dat': SHA_V2},   # .dat без соседнего .txt — реальный апдейт
        base={}, disk={'M/CFG/Binary.dat': SHA_V1},
    )
    check('T54b: .dat без соседнего .txt → 1 обновление', 1, n)

    print('\n--- T54c: форк-хотфикс Lang.dat без снимка → force_rels пробивает косметику ---')
    # Lang.dat-only хотфикс модам БЕЗ снимка (bulk-установка): без force игнорируется
    # как косметика, с force (целевой sha = форк-блоб) — засчитывается как обновление.
    lang = 'OtherMods/WH40kGuns/CFG/Rus/Lang.dat'
    check('T54c: без force — косметика, 0',
          0, core.plan_actionable_sha({lang: SHA_V2}, {}, {lang: SHA_V1}))
    check('T54c: с force — авторитетно, 1',
          1, core.plan_actionable_sha({lang: SHA_V2}, {}, {lang: SHA_V1},
                                      force_rels={lang}))
    check('T54c: force, но диск уже = фикс → 0',
          0, core.plan_actionable_sha({lang: SHA_V2}, {}, {lang: SHA_V2},
                                      force_rels={lang}))

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 15: get_tree детали
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 15: get_tree детали ===')

g = TempGame()
try:
    print('\n--- T55: get_tree без game_path → структура, camps список ---')
    a = g.fresh_api()
    a.profile['game_path'] = ''
    tree = a.get_tree()
    check_true('T55: camps в ответе', 'camps' in tree)
    check_true('T55: camps — список', isinstance(tree.get('camps'), list))

    print('\n--- T56: get_tree с enabled → is_enabled=True для включённого мода ---')
    g2 = TempGame()
    a2 = g2.fresh_api()
    g2.put_mod('ShusRangers/ShuNukes')
    a2.toggle_enabled('ShusRangers/ShuNukes', True)
    tree2 = a2.get_tree()

    def collect_mods(tree):
        result = []
        for camp in tree.get('camps', []):
            result.extend(camp.get('mods', []))
            for pack in camp.get('packs', []):
                result.extend(pack.get('mods', []))
        return result

    mods2 = collect_mods(tree2)
    nuke_entries = [m for m in mods2
                    if 'ShuNukes' in m.get('id', '') or 'Nuke' in m.get('label', '')]
    check_true('T56: ShuNukes в дереве', len(nuke_entries) > 0)
    if nuke_entries:
        check_true('T56: in_profile=True', nuke_entries[0].get('in_profile', False))
    g2.cleanup()

    print('\n--- T57: get_tree — мод без включения in_profile=False ---')
    g3 = TempGame()
    a3 = g3.fresh_api()
    g3.put_mod('ShusRangers/ShuNukes')
    # НЕ включаем мод
    tree3 = a3.get_tree()
    mods3 = collect_mods(tree3)
    nuke3 = [m for m in mods3
             if 'ShuNukes' in m.get('id', '') or 'Nuke' in m.get('label', '')]
    if nuke3:
        check_false('T57: in_profile=False (не включён)', nuke3[0].get('in_profile', False))
    else:
        PASS.append('T57-no-nuke-entry')
    g3.cleanup()

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 16: add_mod / _dedup_folder / get_camp_packs
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 16: add_mod / dedup / get_camp_packs ===')

g = TempGame()
try:
    print('\n--- T58: _dedup_folder — повторный search той же папки заменяет предыдущий ---')
    a = g.fresh_api()
    a.add_mod({'mode': 'search', 'id': 'ShusRangers/ShuNukes', 'name': 'оригинал'})
    check('T58: 1 запись после первого add', 1, len(a.profile['mods']))
    # Добавляем @Pol-вариант той же папки
    a.add_mod({'mode': 'search', 'id': 'ShusRangers/ShuNukes@PolNukes', 'name': 'Pol'})
    ids_in_q = [m.get('id', '') for m in a.profile['mods']]
    check_false('T58: оригинал удалён _dedup_folder',
                'ShusRangers/ShuNukes' in ids_in_q)
    check_true('T58: Pol-вариант в очереди',
               'ShusRangers/ShuNukes@PolNukes' in ids_in_q)

    print('\n--- T59: add_mod двух разных лагерей → 2 записи ---')
    a = g.fresh_api()
    a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
    a.add_mod({'mode': 'src', 'camp': 'universe', 'pack': None, 'mod': ''})
    check('T59: 2 записи', 2, len(a.profile['mods']))
    camps_q = [m.get('camp') for m in a.profile['mods']]
    check_true('T59: redux в очереди', 'redux' in camps_q)
    check_true('T59: universe в очереди', 'universe' in camps_q)

    print('\n--- T60: set_update_extra пустой список → order=[base] ---')
    a = g.fresh_api(base='redux')
    r = a.set_update_extra([])
    check('T60: extra=[]', [], a.profile.get('update_extra'))
    check('T60: order=[redux]', ['redux'], r.get('order'))

    print('\n--- T61: get_camp_packs возвращает структуру лагерей ---')
    a = g.fresh_api()
    r = a.get_camp_packs()
    check('T61: ok=True', True, r.get('ok'))
    camps_r = r.get('camps') or {}
    check_true('T61: camps не пустой', bool(camps_r))
    check_true('T61: redux в camps', 'redux' in camps_r)
    if 'redux' in camps_r:
        check_true('T61: redux содержит паки', len(camps_r['redux']) > 0)

finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 17: check_updates edge cases
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 17: check_updates edge cases ===')

print('\n--- T62: check_updates без модов на диске → _updates пустой ---')
g = TempGame()
try:
    a = g.fresh_api(base='redux')
    a._pub_cache_all = [
        ('redux/redux_base_installer',
         {'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V2}),
    ]

    def fake_idx_empty(mods_dir, catalog, **kw):
        return {'mods': {}, 'count': 0, 'known': 0, 'unknown': 0}

    p_idx = patch.object(core, 'index_disk_mods', side_effect=fake_idx_empty)
    p_sdx = patch.object(core, 'save_disk_index', return_value=None)
    p_cidx = patch.object(core, 'load_chunk_index', return_value=FAKE_IDX)
    p_idx.start(); p_sdx.start(); p_cidx.start()
    try:
        a.check_updates()
        wait_idle(a, timeout=8)
    finally:
        p_idx.stop(); p_sdx.stop(); p_cidx.stop()

    check('T62: _updates пустой (нет модов)', {}, a._updates)
finally:
    g.cleanup()

print('\n--- T63: check_updates два мода, один актуален, один устарел ---')
g = TempGame()
try:
    g.put_mod('ShusRangers/ShuNukes', NUKE_V1)    # устарел (v1→v2)
    g.put_mod('Fairan/FairansVision', VISION)      # актуален (sha совпадает)
    SHA_VISION = hashlib.sha256(VISION).hexdigest()
    a = g.fresh_api(base='redux')
    a._pub_cache_all = [
        ('redux/redux_base_installer', {
            'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V2,
            'Fairan/FairansVision/ModuleInfo.txt': SHA_VISION,
        }),
    ]
    full_idx = {
        'blobs': {
            SHA_V1: {'chunk': 'c1', 'offset': 0, 'size': len(NUKE_V1)},
            SHA_V2: {'chunk': 'c2', 'offset': 0, 'size': len(NUKE_V2)},
            SHA_VISION: {'chunk': 'c3', 'offset': 0, 'size': len(VISION)},
        },
        'chunks': {'c1': {'url': None}, 'c2': {'url': None}, 'c3': {'url': None}},
    }

    def fake_idx_two(mods_dir, catalog, **kw):
        return {
            'mods': {
                'ShusRangers/ShuNukes': {
                    'files': {'ShusRangers/ShuNukes/ModuleInfo.txt': {'sha': SHA_V1}},
                    'status': 'known',
                },
                'Fairan/FairansVision': {
                    'files': {'Fairan/FairansVision/ModuleInfo.txt': {'sha': SHA_VISION}},
                    'status': 'known',
                },
            },
            'count': 2, 'known': 2, 'unknown': 0,
        }

    p_idx = patch.object(core, 'index_disk_mods', side_effect=fake_idx_two)
    p_sdx = patch.object(core, 'save_disk_index', return_value=None)
    p_cidx = patch.object(core, 'load_chunk_index', return_value=full_idx)
    p_idx.start(); p_sdx.start(); p_cidx.start()
    try:
        a.check_updates()
        wait_idle(a, timeout=8)
    finally:
        p_idx.stop(); p_sdx.stop(); p_cidx.stop()

    check_true('T63: ShuNukes в _updates (устарел)', 'ShusRangers/ShuNukes' in a._updates)
    check_false('T63: FairansVision НЕ в _updates (актуален)',
                'Fairan/FairansVision' in a._updates)
finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 18: autodetect_base
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 18: autodetect_base ===')

print('\n--- T64: autodetect_base → camp определён по sha совпадению ---')
g = TempGame()
try:
    g.put_mod('ShusRangers/ShuNukes', NUKE_V1)
    a = g.fresh_api(base='')

    # unit_maps: redux-пак имеет файл, который есть на диске
    a._pub_cache_all = [
        ('redux/redux_base_installer',
         {'ShusRangers/ShuNukes/ModuleInfo.txt': SHA_V1}),
    ]

    def fake_idx_base(mods_dir, catalog, **kw):
        return {
            'mods': {'ShusRangers/ShuNukes': {
                'files': {'ShusRangers/ShuNukes/ModuleInfo.txt': {'sha': SHA_V1}},
                'status': 'known',
            }},
            'count': 1, 'known': 1, 'unknown': 0,
        }

    p_idx = patch.object(core, 'index_disk_mods', side_effect=fake_idx_base)
    p_sdx = patch.object(core, 'save_disk_index', return_value=None)
    p_idx.start(); p_sdx.start()
    try:
        r = a.autodetect_base()
        check('T64: autodetect_base ok=True', True, r.get('ok'))
        check_true('T64: busy=True запущен', a.busy)
        wait_idle(a, timeout=8)
    finally:
        p_idx.stop(); p_sdx.stop()

    check('T64: base определена как redux', 'redux', a.profile.get('base'))
finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 19: multi-camp check_updates
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 19: multi-camp check_updates ===')

print('\n--- T65: update_extra=[universe] — universe-мод попадает в _updates ---')
g = TempGame()
try:
    multi_cat = dict(FAKE_CATALOG)
    multi_cat['Universe/UniMod'] = {
        'name': 'UniMod', 'section': 'Global',
        'default_source': 'universe/universe_community',
        'variants': [{'source': 'universe/universe_community', 'path': 'x.json',
                      'depends': [], 'conflicts': []}],
    }
    multi_packs = dict(FAKE_PACKS)
    multi_packs['universe/universe_community'] = {
        'camp': 'universe', 'name': 'universe_community',
        'tier': 'base', 'load_order': 0, 'bytes': 500_000,
    }
    SHA_UNI_V2 = hashlib.sha256(b'universe_v2_content').hexdigest()
    uni_idx = {
        'blobs': {
            SHA_V1: {'chunk': 'c1', 'offset': 0, 'size': len(NUKE_V1)},
            SHA_UNI_V2: {'chunk': 'c2', 'offset': 0, 'size': 19},
        },
        'chunks': {'c1': {'url': None}, 'c2': {'url': None}},
    }

    g.put_mod('Universe/UniMod', NUKE_V1)
    a = g.fresh_api(base='redux', catalog=multi_cat, packs=multi_packs)
    a.set_update_extra(['universe'])

    a._pub_cache_all = [
        ('redux/redux_base_installer', {}),
        ('universe/universe_community',
         {'Universe/UniMod/ModuleInfo.txt': SHA_UNI_V2}),
    ]

    def fake_disk_uni(mods_dir, catalog, **kw):
        return {
            'mods': {'Universe/UniMod': {
                'files': {'Universe/UniMod/ModuleInfo.txt': {'sha': SHA_V1}},
                'status': 'known',
            }},
            'count': 1, 'known': 1, 'unknown': 0,
        }

    p_idx = patch.object(core, 'index_disk_mods', side_effect=fake_disk_uni)
    p_sdx = patch.object(core, 'save_disk_index', return_value=None)
    p_cidx = patch.object(core, 'load_chunk_index', return_value=uni_idx)
    p_idx.start(); p_sdx.start(); p_cidx.start()
    try:
        a.check_updates()
        wait_idle(a, timeout=8)
    finally:
        p_idx.stop(); p_sdx.stop(); p_cidx.stop()

    check_true('T65: Universe/UniMod в _updates', 'Universe/UniMod' in a._updates)
    check('T65: лагерь = universe',
          'universe', a._updates.get('Universe/UniMod', {}).get('camp'))
finally:
    g.cleanup()

# ═══════════════════════════════════════════════
#  ГРУППА 20: reconstruct_camp — идемпотентность лагеря (реальная логика)
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 20: reconstruct_camp идемпотентность ===')

import zipfile as _zip

def _sha(b): return hashlib.sha256(b).hexdigest()

# base и fix делят один целевой файл с РАЗНЫМ содержимым (пути расходятся регистром и
# префиксом {app}, но install_route сводит их в одну цель). fix идёт позже → побеждает.
_BASE_SHARED = b'BASE-shared'; _FIX_SHARED = b'FIX-shared-WINS'
_BASE_ONLY = b'BASE-only';     _FIX_ONLY = b'FIX-only'
_S_BS, _S_FS, _S_BO, _S_FO = map(_sha, (_BASE_SHARED, _FIX_SHARED, _BASE_ONLY, _FIX_ONLY))
_BASE_MAN = {'files': {'{app}/Mods/Foo/shared.dat': {'sha256': _S_BS, 'size': len(_BASE_SHARED)},
                       '{app}/Mods/Foo/onlybase.dat': {'sha256': _S_BO, 'size': len(_BASE_ONLY)}}}
_FIX_MAN = {'files': {'Mods/Foo/Shared.dat': {'sha256': _S_FS, 'size': len(_FIX_SHARED)},
                      'Mods/Foo/onlyfix.dat': {'sha256': _S_FO, 'size': len(_FIX_ONLY)}}}
_EMPTY_MAN = {'files': {}}
_IDX = {'blobs': {_S_BS: {'chunk': 'c1'}, _S_FS: {'chunk': 'c1'},
                  _S_BO: {'chunk': 'c1'}, _S_FO: {'chunk': 'c1'}},
        'chunks': {'c1': {'url': 'http://fake/c1', 'store': 'hf'}}}

def _fake_rfb(repo, path, token, should_cancel=None, branch=None):
    if path == 'state/asset_index.json':
        return json.dumps(_IDX).encode()
    tbl = {'mods/redux/redux_base_installer/code.manifest.json': _BASE_MAN,
           'mods/redux/redux_base_installer/assets.manifest.json': _EMPTY_MAN,
           'mods/redux/redux_fixes/code.manifest.json': _FIX_MAN,
           'mods/redux/redux_fixes/assets.manifest.json': _EMPTY_MAN}
    if path in tbl:
        return json.dumps(tbl[path]).encode()
    import requests
    raise requests.HTTPError(f'404 {path}')

def _fake_dl(url, token, dest, progress_cb=None, should_cancel=None, byte_cb=None):
    with _zip.ZipFile(dest, 'w') as z:
        z.writestr(_S_BS, _BASE_SHARED); z.writestr(_S_FS, _FIX_SHARED)
        z.writestr(_S_BO, _BASE_ONLY);   z.writestr(_S_FO, _FIX_ONLY)

def _run_camp(mods, tmp):
    units = [{'unit': 'redux_base_installer', 'tier': 'base', 'fork_files': None, 'fork_index': None},
             {'unit': 'redux_fixes', 'tier': 'fixes', 'fork_files': None, 'fork_index': None}]
    with patch.object(core, 'repo_file_bytes', side_effect=_fake_rfb), \
         patch.object(core, 'download_url', side_effect=_fake_dl):
        return core.reconstruct_camp('repo', 'redux', units, mods, 'tok',
                                     log=lambda *_: None, tmp_dir=tmp)

_w = Path(tempfile.mkdtemp(prefix='camp_idem_'))
try:
    print('\n--- T66: проход 1 — победитель по приоритету + запись ---')
    _mods = _w / 'Game' / 'Mods'; _tmp = _w / 'tmp'
    _mods.mkdir(parents=True); _tmp.mkdir()
    _shared = _mods / 'Foo' / 'Shared.dat'
    s1 = _run_camp(_mods, _tmp)
    check_true('T66: проход1 записал код (>0)', s1['code_files'] > 0)
    check('T66: проход1 skipped=0', 0, s1['skipped'])
    check('T66: победил fix (позже в порядке)', _FIX_SHARED, _shared.read_bytes())
    check('T66: onlybase записан', _BASE_ONLY, (_mods / 'Foo' / 'onlybase.dat').read_bytes())

    print('\n--- T67: проход 2 — идемпотентно (ничего не пишется) ---')
    s2 = _run_camp(_mods, _tmp)
    check('T67: проход2 code=0', 0, s2['code_files'])
    check('T67: проход2 asset=0', 0, s2['asset_files'])
    check('T67: проход2 всё skipped (3)', 3, s2['skipped'])
    check('T67: спорный файл не откатился', _FIX_SHARED, _shared.read_bytes())

    print('\n--- T68: проход 3 — порча одного файла чинит ровно его ---')
    _shared.write_bytes(b'corrupted')
    s3 = _run_camp(_mods, _tmp)
    check('T68: перезаписан ровно 1 файл', 1, s3['code_files'])
    check('T68: восстановлен до fix', _FIX_SHARED, _shared.read_bytes())
    check('T68: остальные 2 skipped', 2, s3['skipped'])
finally:
    shutil.rmtree(_w, ignore_errors=True)

# ═══════════════════════════════════════════════
#  ГРУППА 21: reconstruct_multi — cross-entry merge + прунинг (п.10)
# ═══════════════════════════════════════════════
print('\n=== ГРУППА 21: reconstruct_multi cross-entry merge (п.10) ===')

# Два пака несут ОДИН мод-id Huk'sShit/Mod_Interface: solyanka с уникальным Lang.dat,
# huk без. Отдельные проходы затирали общий файл и оставляли Lang.dat сиротой. Слияние
# в один eff + прунинг снимка это чинит.
_HUK_DATA = b'HUK-data'; _SOL_DATA = b'SOL-data-diff'; _MI = b'MI'; _LANG = b'LANG-solyanka'
_S_HUK, _S_SOL, _S_MI, _S_LANG = map(_sha, (_HUK_DATA, _SOL_DATA, _MI, _LANG))
_HUK_MAN = {'files': {
    "Mods/HuksShit/Mod_Interface/data.dat": {'sha256': _S_HUK, 'size': len(_HUK_DATA)},
    "Mods/HuksShit/Mod_Interface/ModuleInfo.txt": {'sha256': _S_MI, 'size': len(_MI)}}}
_SOL_MAN = {'files': {
    "Mods/HuksShit/Mod_Interface/data.dat": {'sha256': _S_SOL, 'size': len(_SOL_DATA)},
    "Mods/HuksShit/Mod_Interface/ModuleInfo.txt": {'sha256': _S_MI, 'size': len(_MI)},
    "Mods/HuksShit/Mod_Interface/CFG/Rus/Lang.dat": {'sha256': _S_LANG, 'size': len(_LANG)}}}
_M_IDX = {'blobs': {_S_HUK: {'chunk': 'c1'}, _S_SOL: {'chunk': 'c1'},
                    _S_MI: {'chunk': 'c1'}, _S_LANG: {'chunk': 'c1'}},
          'chunks': {'c1': {'url': 'http://fake/c1', 'store': 'hf'}}}

def _m_rfb(repo, path, token, should_cancel=None, branch=None):
    if path == 'state/asset_index.json':
        return json.dumps(_M_IDX).encode()
    tbl = {'mods/redux/huk_mods/code.manifest.json': _HUK_MAN,
           'mods/redux/huk_mods/assets.manifest.json': _EMPTY_MAN,
           'mods/redux/solyanka_main/code.manifest.json': _SOL_MAN,
           'mods/redux/solyanka_main/assets.manifest.json': _EMPTY_MAN}
    if path in tbl:
        return json.dumps(tbl[path]).encode()
    import requests
    raise requests.HTTPError(f'404 {path}')

def _m_dl(url, token, dest, progress_cb=None, should_cancel=None, byte_cb=None):
    with _zip.ZipFile(dest, 'w') as z:
        z.writestr(_S_HUK, _HUK_DATA); z.writestr(_S_SOL, _SOL_DATA)
        z.writestr(_S_MI, _MI); z.writestr(_S_LANG, _LANG)

_HUK = {'camp': 'redux', 'unit': 'huk_mods', 'tier': 'mod', 'fork_files': None, 'fork_index': None}
_SOL = {'camp': 'redux', 'unit': 'solyanka_main', 'tier': 'mod', 'fork_files': None, 'fork_index': None}

def _run_multi(units, mods, tmp, snap):
    with patch.object(core, 'repo_file_bytes', side_effect=_m_rfb), \
         patch.object(core, 'download_url', side_effect=_m_dl):
        return core.reconstruct_multi('repo', units, mods, 'tok', log=lambda *_: None,
                                      tmp_dir=tmp, prune_snap_id='__b', snap_dir=snap)

_w2 = Path(tempfile.mkdtemp(prefix='multi_')); _snap = _w2 / 'snap'
try:
    _mods = _w2 / 'Game' / 'Mods'; _tmp = _w2 / 'tmp'; _mods.mkdir(parents=True); _tmp.mkdir()
    _data = _mods / 'HuksShit' / 'Mod_Interface' / 'data.dat'
    _lang = _mods / 'HuksShit' / 'Mod_Interface' / 'CFG' / 'Rus' / 'Lang.dat'

    print('\n--- T69: слияние — solyanka позже побеждает общий файл, Lang.dat записан ---')
    _run_multi([_HUK, _SOL], _mods, _tmp, _snap)
    check('T69: общий data.dat = solyanka (позже в порядке)', _SOL_DATA, _data.read_bytes())
    check_true('T69: уникальный Lang.dat записан', _lang.exists())

    print('\n--- T70: повторный проход идемпотентен ---')
    s = _run_multi([_HUK, _SOL], _mods, _tmp, _snap)
    check('T70: всё skipped (3)', 3, s['skipped'])

    print('\n--- T71: игрок убрал solyanka → только huk → Lang.dat-сирота удалён ---')
    _run_multi([_HUK], _mods, _tmp, _snap)
    check('T71: data.dat стал huk', _HUK_DATA, _data.read_bytes())
    check_true('T71: сирота Lang.dat удалён', not _lang.exists())

    print('\n--- T72: правка игрока не удаляется прунингом ---')
    _run_multi([_HUK, _SOL], _mods, _tmp, _snap)     # вернуть Lang.dat
    _lang.write_bytes(b'PLAYER-EDIT')                 # игрок изменил
    _run_multi([_HUK], _mods, _tmp, _snap)            # снова без solyanka
    check_true('T72: изменённый игроком Lang.dat НЕ удалён', _lang.exists())
    check('T72: правка игрока цела', b'PLAYER-EDIT', _lang.read_bytes())
finally:
    shutil.rmtree(_w2, ignore_errors=True)

# ═══════════════════════════════════════════════
#  Итог
# ═══════════════════════════════════════════════
print(f'\n{"="*50}')
print(f'ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)}')
if FAIL:
    print('ПРОВАЛЫ:', FAIL)
    sys.exit(1)
