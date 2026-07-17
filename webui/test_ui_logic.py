"""Уровень-1 ассерты: гоняем реальные методы Api как кнопки, сверяем ОЖИДАНИЕ vs ФАКТ.
Песочница: профиль/конфиг НЕ пишем на диск (стабим _save_*), сеть не трогаем (стабим
worker'ы и inject-им фейковый каталог/packs). Запуск: python test_ui_logic.py"""
import sys, types, threading
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher\webui')
import app

PASS = []; FAIL = []
def check(name, expect, actual):
    ok = expect == actual
    (PASS if ok else FAIL).append(name)
    mark = 'OK ' if ok else 'FAIL'
    print(f'[{mark}] {name}\n      ожидание: {expect!r}\n      факт:     {actual!r}')

def fresh_api(base='redux', extra=None):
    a = app.Api.__new__(app.Api)               # без __init__ (не читаем диск)
    a.busy = False
    a._cancel = threading.Event()
    a._updates = {}
    a.profile = {'name': 't', 'game_path': '', 'mods': [], 'enabled': [],
                 'base': base, 'update_extra': list(extra or [])}
    a._save_profile = lambda: None
    a._save_config = lambda: None
    a.log = lambda *x: None
    a._emit = lambda *x, **k: None
    # фейковый каталог: ModA(redux+universe), ModB(universe), ModC(original)
    a._catalog_cache = {
        'Cat/ModA': {'name': 'ModA', 'default_source': 'redux/redux_base_installer',
                     'variants': [{'source': 'redux/redux_base_installer'},
                                  {'source': 'universe/universe_community'}]},
        'Cat/ModB': {'name': 'ModB', 'default_source': 'universe/universe_community',
                     'variants': [{'source': 'universe/universe_community'}]},
        'Cat/ModC': {'name': 'ModC', 'default_source': 'original/original_installer',
                     'variants': [{'source': 'original/original_installer'}]},
    }
    a._packs_cache = {
        'redux/redux_base_installer': {'camp': 'redux', 'name': 'redux_base_installer', 'tier': 'base'},
        'universe/universe_community': {'camp': 'universe', 'name': 'universe_community', 'tier': 'base'},
        'original/original_installer': {'camp': 'original', 'name': 'original_installer', 'tier': 'base'},
    }
    a._token = lambda: None
    return a

print('=== _update_order (строка1=база, дедуп, фильтр невалидных) ===')
a = fresh_api('redux', ['original', 'redux', 'bogus', 'universe'])
check('order: база+валидные, без дублей/мусора', ['redux', 'original', 'universe'], a._update_order())
check('order: пустая база → []', [], fresh_api('', ['original'])._update_order())

print('\n=== _target_camp (первый лагерь порядка, где мод есть) ===')
tc = app.Api._target_camp
check('ModA (redux+uni), order[redux,uni] → redux', 'redux',
      tc(['redux', 'universe'], ['redux/redux_base_installer', 'universe/universe_community']))
check('ModB (только uni), order[redux,uni] → universe', 'universe',
      tc(['redux', 'universe'], ['universe/universe_community']))
check('ModB (только uni), order[redux] → None (не проверяем)', None,
      tc(['redux'], ['universe/universe_community']))
check('ModC (только original), order[uni,original] → original', 'original',
      tc(['universe', 'original'], ['original/original_installer']))

print('\n=== set_update_extra (строку базы не берём, дедуп, порядок) ===')
a = fresh_api('redux')
r = a.set_update_extra(['original', 'universe', 'redux'])
check('extra сохранён без базы', ['original', 'universe'], a.profile['update_extra'])
check('order из set_update_extra', ['redux', 'original', 'universe'], r['order'])
# защитный кейс: строка вместо списка (сериализация) не должна «раскрошиться» в буквы
a2 = fresh_api('redux')
a2.set_update_extra('original')
check('строка вместо списка → пусто (буквы не лагеря)', [], a2.profile['update_extra'])

print('\n=== _detect_base_camp (по sha base-паков) ===')
umaps = [('redux/redux_base_installer', {'a': 'H1', 'b': 'H2'}),
         ('universe/universe_community', {'a': 'H1', 'c': 'H9'})]
a = fresh_api('redux')
check('диск={a:H1,b:H2} → redux (match 2)', 'redux',
      a._detect_base_camp(umaps, {'a': 'H1', 'b': 'H2'}))
check('диск={a:H1,c:H9} → universe (match 2)', 'universe',
      a._detect_base_camp(umaps, {'a': 'H1', 'c': 'H9'}))
check('пустой диск → None', None, a._detect_base_camp(umaps, {}))

print('\n=== _camp_member_mids (вход для разворота пресета / фолбэка) ===')
a = fresh_api('redux')
check('redux-члены', {'Cat/ModA'}, a._camp_member_mids('redux'))
check('universe-члены', {'Cat/ModA', 'Cat/ModB'}, a._camp_member_mids('universe'))
a._catalog_cache = {}
check('каталог пуст → пусто (тогда build_rows покажет заглушку)', set(), a._camp_member_mids('redux'))

print('\n=== add_mod: дедуп записи лагеря (повторный клик пресета) ===')
a = fresh_api('redux')
a._repo = lambda: 'x/y'
r1 = a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
r2 = a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
check('1-й клик добавил запись camp', 1, sum(1 for m in a.profile['mods'] if m.get('type') == 'camp'))
check('2-й клик → dup, без дубля', True, bool(r2.get('dup')))
check('всего записей camp по-прежнему 1', 1, sum(1 for m in a.profile['mods'] if m.get('type') == 'camp'))

print('\n=== check_updates(extra): персист порядка ДО фона (контракт фикса refresh_remote) ===')
a = fresh_api('redux')
a._game_root = lambda: r'C:\fake'
a._check_updates_worker = lambda: None          # не трогаем сеть/диск
r = a.check_updates(['original'])
check('check_updates(extra) сохранил порядок', ['redux', 'original'], a._update_order())
# затем внутренний вызов refresh_remote → check_updates() БЕЗ extra не должен стирать порядок
a.busy = False
a.check_updates()
check('check_updates() без extra НЕ стёр порядок', ['redux', 'original'], a._update_order())

print('\n=== _needs_order (пропуск окна порядка, если всё в базе) ===')
a = fresh_api('redux')                              # ModA есть в redux, ModB только universe, ModC только original
a._disk_index = {'mods': {'Cat/ModA': {}}}          # на диске только ModA (есть в базе redux)
check('только ModA (в базе) → окно НЕ нужно', False, a._needs_order())
a._disk_index = {'mods': {'Cat/ModA': {}, 'Cat/ModB': {}}}   # ModB только в universe
check('есть ModB (не в базе redux) → окно нужно', True, a._needs_order())
a2 = fresh_api('redux', ['original'])               # уже настроены доп.строки
a2._disk_index = {'mods': {'Cat/ModA': {}}}
check('настроены доп.строки → окно нужно', True, a2._needs_order())
a3 = fresh_api('redux'); a3._disk_index = None
check('нет индекса диска → окно не навязываем', False, a3._needs_order())

print('\n=== clear_queue (отменить ВСЕ добавления) ===')
a = fresh_api('redux'); a._repo = lambda: 'x/y'
a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'mod': ''})
a.add_mod({'mode': 'src', 'camp': 'universe', 'pack': None, 'mod': ''})
r = a.clear_queue()
check('clear_queue вернул число удалённых', 2, r['removed'])
check('очередь пуста', [], a.profile['mods'])

print('\n=== set_ui_scale / set_text_scale / set_contrast: клампинг диапазонов ===')
a = fresh_api('redux'); a.config = {}
check('scale 200 → 160 (макс)', 160, a.set_ui_scale(200))
check('scale 50 → 80 (мин)', 80, a.set_ui_scale(50))
check('scale "x" → 100 (дефолт)', 100, a.set_ui_scale('x'))
check('text 999 → 170 (макс)', 170, a.set_text_scale(999))
check('text 10 → 80 (мин)', 80, a.set_text_scale(10))
check('text "y" → 100 (дефолт)', 100, a.set_text_scale('y'))
check('contrast 999 → 150 (макс)', 150, a.set_contrast(999))
check('contrast 90 → 100 (мин)', 100, a.set_contrast(90))
check('contrast "abc" → 100 (дефолт)', 100, a.set_contrast('abc'))

print('\n=== _serialize_plan_detailed: точный источник (пак/форк) + стороны мой↔обновление ===')
from pathlib import Path
a = fresh_api('redux')
a._mods_dir = lambda: Path(r'C:\nonexistent_test_dir_zzz')     # диск-stat всегда падает → ('', None)
a._dev_date = lambda mid: 1600000000
a._forks = lambda: []                                          # → fork_date '' (атрибуцию не ломает)
a._last_fork_files = {'Mods/ModX/b.dat'}                       # b.dat пришёл из форка → хотфикс
a._last_fork_sources = {'Mods/ModX/b.dat': 'ArtYudin89/sr-mods-hotfixes'}
a._last_variant_sources = {'Mods/ModX/a.dat': 'redux/redux_fixes',
                           'Mods/ModX/c.dat': 'redux/redux_base_installer',
                           'Mods/ModX/e.dat': 'redux/redux_base_installer'}
desc = {'id': 'Cat/ModX', 'source': 'redux/redux_fixes', 'version': '2',
        'files': {'code': {'Mods/ModX/a.dat': {'sha256': 'A', 'size': 100, 'mtime': 1650646800},
                           'Mods/ModX/b.dat': {'sha256': 'B', 'size': 200}},
                  'assets': {}}}
plan = {'source': 'redux/redux_fixes', 'version_old': '1', 'version_new': '2',
        'summary': {'update': 2, 'player_only': 1, 'deleted_clean': 1, 'unchanged': 1, 'conflicts': 0},
        'actions': [
            {'relpath': 'Mods/ModX/a.dat', 'status': 'update'},        # пак redux_fixes, есть моё+новое
            {'relpath': 'Mods/ModX/b.dat', 'status': 'update'},        # хотфикс-репо
            {'relpath': 'Mods/ModX/c.dat', 'status': 'player_only'},   # база redux, только моё
            {'relpath': 'Mods/ModX/e.dat', 'status': 'deleted_clean'}, # удаление, только моё
            {'relpath': 'Mods/ModX/d.dat', 'status': 'unchanged'},     # не в списке, только счётчик
        ]}
det = a._serialize_plan_detailed(('disk', 'Cat/ModX'), desc, plan)
byname = {f['path'].split('/')[-1].split('\\')[-1]: f for f in det['files']}
check('в списке 4 изменяемых файла (unchanged исключён)', 4, len(det['files']))
check('unchanged посчитан отдельно', 1, det['unchanged'])
check('a.dat → kind разработчик', 'developer', byname['a.dat']['source'])
check('a.dat → точный пак redux_fixes', 'redux/redux_fixes', byname['a.dat']['source_detail'])
check('a.dat → новое: размер из манифеста', 100, byname['a.dat']['their']['size'])
check('a.dat → новое: РЕАЛЬНАЯ дата файла (per-file mtime 2022)',
      True, '2022' in byname['a.dat']['their']['date'])
check('b.dat → хотфикс без per-file mtime и без даты форк-репо: правая ячейка НЕ пустая '
      '(фолбэк на dev_date родительского мода — не «—»)',
      True, bool(byname['b.dat']['their']['date']))
check('b.dat → фолбэк-дата = дата родительского мода (dev_date 2020)',
      True, '2020' in byname['b.dat']['their']['date'])
check('a.dat → есть сторона «моё» (update)', True, byname['a.dat']['mine'] is not None)
check('b.dat → kind хотфикс', 'hotfix', byname['b.dat']['source'])
check('b.dat → точный форк-репо', 'ArtYudin89/sr-mods-hotfixes', byname['b.dat']['source_detail'])
check('b.dat → новое: размер 200', 200, byname['b.dat']['their']['size'])
check('c.dat (player_only) → пак-установщик redux', 'redux/redux_base_installer', byname['c.dat']['source_detail'])
check('c.dat → нет стороны «обновление»', None, byname['c.dat']['their'])
check('c.dat → есть сторона «моё»', True, byname['c.dat']['mine'] is not None)
check('e.dat (deleted) → нет стороны «обновление»', None, byname['e.dat']['their'])
check('has_forks=True (были форк-файлы)', True, det['has_forks'])

print('\n=== preview_update_plan: защита от занятости/паков ===')
a = fresh_api('redux'); a.busy = True
r = a.preview_update_plan('d:Cat/ModX')
check('busy → отказ', False, r['ok'])
a = fresh_api('redux'); a.busy = False; a._previewing = False
a._require_game = lambda: 'нет игры'
r = a.preview_update_plan('d:Cat/ModX')
check('нет папки игры → отказ', False, r['ok'])

print('\n=== _overlay_theirs: форк по install-rel + base-id fallback (баг ShuDomiks) ===')
# Pol/Shu @-вариант: форк-хотфикс записан под base-id (Cat/Mod), desc['id'] несёт @-суффикс
# (Cat/Mod@Pol), а raw-корень форка ('Fork_unpacked/Mods/…') ≠ корню основного
# ('{app}/Mods/…'). Форк ОБЯЗАН перекрыть файл по install-rel (не плодить фантом-дубль) и
# найтись по base-id — иначе хотфикс не доставляется, а детект форсит его → вечный бейдж.
import launcher_core as _core
a = fresh_api('redux')
a._repo = lambda: 'main/repo'; a._token = lambda: None
a._forks = lambda: [{'repo': 'fork/repo', 'token': None}]
_fork_cat = {'Cat/Mod': {'default_source': 'redux/redux_fixes'}}   # ТОЛЬКО base-id, без @
a._catalog_for = lambda repo, tok: _fork_cat
a._index_for = lambda repo, tok, desc=None: {
    'blobs': {'BBB': {'chunk': 'c'}, 'CCC': {'chunk': 'c'}}, 'chunks': {'c': {'url': 'x'}}}
_fork_desc = {'id': 'Cat/Mod', 'files': {
    'code': {'Fork_unpacked/Mods/Cat/Mod/x.scr': {'sha256': 'BBB', 'size': 9}},
    'assets': {'Fork_unpacked/Mods/Cat/Mod/new.dat': {'sha256': 'CCC', 'size': 3}}}}
_odf, _olci = _core.descriptor_for, _core.load_chunk_index
_core.descriptor_for = lambda sel, catalog=None, repo=None, token=None: _fork_desc
_core.load_chunk_index = lambda desc=None, url=None, repo=None, token=None: {'blobs': {}, 'chunks': {}}
_main_desc = {'id': 'Cat/Mod@Pol', 'source': 'redux/redux_base_installer', 'files': {
    'code': {'{app}/Mods/Cat/Mod/x.scr': {'sha256': 'AAA', 'size': 9}},
    'assets': {'{app}/Mods/Cat/Mod/keep.dat': {'sha256': 'KEEP', 'size': 1}}}}
_merged, _idx = a._overlay_theirs(_main_desc, 'redux/redux_base_installer')
_core.descriptor_for, _core.load_chunk_index = _odf, _olci
_flat = {r: m['sha256'] for k in ('code', 'assets') for r, m in _merged['files'][k].items()}
check('форк найден по base-id для @-варианта (overlaid)', True, _merged.get('overlaid') is True)
check('x.scr перекрыт форком по install-rel (sha BBB)', 'BBB', _flat.get('{app}/Mods/Cat/Mod/x.scr'))
check('нет фантом-дубля по raw-пути форка', False, 'Fork_unpacked/Mods/Cat/Mod/x.scr' in _flat)
check('новый файл форка добавлен (new.dat=CCC)', 'CCC', _flat.get('Fork_unpacked/Mods/Cat/Mod/new.dat'))
check('несвязанный файл основного цел (keep.dat)', 'KEEP', _flat.get('{app}/Mods/Cat/Mod/keep.dat'))
check('идентичность мода сохранена (@-ключ)', 'Cat/Mod@Pol', _merged.get('id'))
check('preview пометит перекрытый файл hotfix (raw основного в _last_fork_files)',
      True, '{app}/Mods/Cat/Mod/x.scr' in a._last_fork_files)

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
if FAIL:
    print('ПРОВАЛЫ:', FAIL); sys.exit(1)
