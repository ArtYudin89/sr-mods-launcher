"""Уровень-1 ассерты: гоняем реальные методы Api как кнопки, сверяем ОЖИДАНИЕ vs ФАКТ.
Песочница: профиль/конфиг НЕ пишем на диск (стабим _save_*), сеть не трогаем (стабим
worker'ы и inject-им фейковый каталог/packs). Запуск: python test_ui_logic.py"""
import sys, types, threading, time
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
    a._paused = threading.Event()
    a._chunk_prog = {}; a._chunk_lock = threading.Lock()
    a._updates = {}
    a._camps_idx = None
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
a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'part': 'base'})
a.add_mod({'mode': 'src', 'camp': 'universe', 'pack': None, 'part': 'mods'})
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

print('\n=== 🔒 «не обновлять» (отзыв 1): пометка, детект, массовое обновление ===')
a = fresh_api('redux'); a.config = {'mod_meta': {}}
a._updates = {'Cat/ModA': {'n': 3, 'camp': 'redux'}}
a.set_mod_frozen('Cat/ModA', True)
check('пометка сохранилась в mod_meta', True, a.config['mod_meta']['Cat/ModA']['frozen'])
check('_is_frozen видит пометку', True, a._is_frozen('Cat/ModA'))
check('бейдж «обновление» снят сразу', False, 'Cat/ModA' in a._updates)
check('соседний мод не задет', False, a._is_frozen('Cat/ModB'))
a.set_mod_frozen('Cat/ModA', False)
check('снятие пометки чистит запись', {}, a.config['mod_meta'])

a = fresh_api('redux'); a.config = {'mod_meta': {}}
a.set_mods_frozen(['Cat/ModA', 'Cat/ModB'], True)
check('массовая заморозка', [True, True],
      [a._is_frozen('Cat/ModA'), a._is_frozen('Cat/ModB')])

# скрытый мод: сам по себе обновляется, но настройка «скрытые не обновлять» его замораживает
a = fresh_api('redux'); a.config = {'mod_meta': {'Cat/ModA': {'hidden': True}}}
check('скрытый по умолчанию ОБНОВЛЯЕТСЯ', False, a._is_frozen('Cat/ModA'))
a.set_freeze_hidden(True)
check('с настройкой freeze_hidden скрытый заморожен', True, a._is_frozen('Cat/ModA'))
check('нескрытый мод настройкой не затронут', False, a._is_frozen('Cat/ModB'))

# «Обновить все» по замороженной строке: явный отказ вместо тихого ничего
a = fresh_api('redux'); a.config = {'mod_meta': {'Cat/ModA': {'frozen': True}}}
a._require_game = lambda: None
r = a.start_merge(['d:Cat/ModA'])
check('обновление замороженного отклонено', False, r['ok'])
check('в тексте объяснено про 🔒', True, 'не обновлять' in r['error'])

# окно конфликтов: «Больше не предлагать» = пропустить + пометить 🔒
def _pending(a, mid):
    a._pending_merge = {'target': ('disk', mid), 'desc': {'id': mid},
                        'plan': {'id': mid, 'actions': [], 'summary': {}}, 'index': {}}
    a._merge_next = lambda: None

a = fresh_api('redux'); a.config = {'mod_meta': {}}
_pending(a, 'Cat/ModA'); a.merge_skip()
check('обычный «Пропустить» не морозит мод', False, a._is_frozen('Cat/ModA'))
_pending(a, 'Cat/ModA'); a.merge_skip(True)
check('«Больше не предлагать» ставит 🔒', True, a._is_frozen('Cat/ModA'))

print('\n=== ⏸ пауза операции (отзыв 3) ===')
a = fresh_api('redux')
check('по умолчанию не на паузе', False, a.is_paused())
check('should_cancel без паузы возвращает сразу', False, a.should_cancel())
a.set_paused(True)
check('is_paused после нажатия', True, a.is_paused())
_res = []
_t = threading.Thread(target=lambda: _res.append(a.should_cancel()), daemon=True)
_t.start(); _t.join(0.6)
check('на паузе should_cancel НЕ возвращает управление', True, _t.is_alive())
a.set_paused(False)
_t.join(1.5)
check('после «Продолжить» операция едет дальше', [False], _res)
a.set_paused(True); a._cancel.set()
_t2 = threading.Thread(target=lambda: _res.append(a.should_cancel()), daemon=True)
_t2.start(); _t2.join(1.0)
check('отмена пробивает паузу', False, _t2.is_alive())

print('\n=== плавный прогресс частей (отзыв 3: «не видно прогресс скачивания») ===')
a = fresh_api('redux')
a._dl_bytes = 0; a._pack_ctx = 'тест'
_ev = []
a._emit = lambda e, d=None: _ev.append((e, d))
a._part_progress(1, 4)                       # 1 часть из 4 готова → 25%
check('после первой части 25%', 25, _ev[-1][1]['pct'])
time.sleep(0.25)                             # обойти троттлинг эмитов
a._chunk_progress('c2', 5, 10)               # вторая часть скачана наполовину
check('доля внутри части учтена (37%)', 38, _ev[-1][1]['pct'])
time.sleep(0.25)
a._chunk_progress('c2', 10, 10)              # часть докачана → доля уходит в счётчик
check('докачанная часть не удваивает прогресс', 25, _ev[-1][1]['pct'])

print('\n=== «Откуда установлен» в карточке (отзыв 5) ===')
a = fresh_api('redux'); a.config = {'mod_meta': {}}
a._packs_cache['redux/redux_base_installer']['display_name'] = 'Universe Redux — установщик'
a._packs_cache['redux/redux_base_installer']['load_order'] = 10
a._packs_cache['redux/redux_fixes'] = {'camp': 'redux', 'name': 'redux_fixes', 'tier': 'fix',
                                       'fix_parent': 'redux_base_installer', 'load_order': 20,
                                       'display_name': 'Universe Redux — фиксы'}
a._fixparent = {'redux_fixes': 'redux_base_installer'}
a._pub_cache_all = [('redux/redux_base_installer', {'Cat/ModA/a.dat': 'A'}),
                    ('redux/redux_fixes', {'Cat/ModA/a.dat': 'B'})]
si = a._source_info('Cat/ModA')
check('сборка определена', 'redux', si['camp'])
check('паки в порядке установки', ['Universe Redux — установщик', 'Universe Redux — фиксы'],
      [p['title'] for p in si['packs']])
check('роли паков подписаны', ['основа', 'фиксы'], [p['role'] for p in si['packs']])
a._pub_cache_all = [('redux/redux_base_installer', {'Cat/ModA/a.dat': 'A'}),
                    ('redux/redux_fixes', {'Cat/ModB/b.dat': 'B'})]
si = a._source_info('Cat/ModA')
check('фикс-пак без файлов этого мода не показывается', ['redux_base_installer'],
      [p['unit'] for p in si['packs']])

print('\n=== база нужна и модам, набранным поштучно (Rangers.exe с ними не едет) ===')
import launcher_core as lc
PK = {'redux/redux_base_installer': {'camp': 'redux', 'name': 'redux_base_installer',
                                     'tier': 'base', 'update_required': True}}
# уровень ядра: extra_playable/extra_base
check('только поштучные моды, базы нет → предупредить', True,
      lc.check_pack_compatibility([], PK, extra_playable=True)['missing_base'])
check('поштучные + база на диске → молчим', False,
      lc.check_pack_compatibility([], PK, installed_base='redux_base_installer',
                                  extra_playable=True)['missing_base'])
check('поштучные + база в наборе иначе (сборка/_base) → молчим', False,
      lc.check_pack_compatibility([], PK, extra_playable=True,
                                  extra_base=True)['missing_base'])
check('поштучные + base-пак целиком → молчим', False,
      lc.check_pack_compatibility(['redux/redux_base_installer'], PK,
                                  extra_playable=True)['missing_base'])
check('пустой профиль → не придираемся', False,
      lc.check_pack_compatibility([], PK)['missing_base'])

def compat_texts(mods):
    a = fresh_api('redux')
    a._names = {}
    a.config = {}
    a._mods_dir = lambda: Path(r'C:\nonexistent_test_dir_zzz')   # базы на диске нет
    a._repo = lambda: 'x/y'
    a.profile['mods'] = mods
    return ' | '.join(i['text'] for i in a.check_compat()['items'] if i['level'] == 'warn')

warn = lambda mods: 'Rangers.exe' in compat_texts(mods)
check('профиль из одних каталожных модов → предупреждение есть', True,
      warn([{'type': 'desc', 'id': 'Cat/ModA'}]))
check('профиль из одного мода пака → тоже (пак приехал без базовых файлов)', True,
      warn([{'type': 'unit', 'camp': 'redux', 'unit': 'redux_base_installer',
             'mod': 'Cat/ModA'}]))
check('добавлена вся сборка → предупреждения нет', False,
      warn([{'type': 'desc', 'id': 'Cat/ModA'}, {'type': 'camp', 'camp': 'redux'}]))
check('добавлены базовые файлы игры (_base) → предупреждения нет', False,
      warn([{'type': 'desc', 'id': 'Cat/ModA'},
            {'type': 'unit', 'camp': 'redux', 'unit': 'redux_base_installer',
             'mod': '_base'}]))
check('добавлен base-пак целиком → предупреждения нет', False,
      warn([{'type': 'desc', 'id': 'Cat/ModA'},
            {'type': 'unit', 'camp': 'redux', 'unit': 'redux_base_installer', 'mod': ''}]))

print('\n=== «Базовые файлы игры»: имя в UI и маршрут установки ===')
a = fresh_api('redux'); a._repo = lambda: 'x/y'
app.core.list_unit_mods = lambda repo, camp, unit, tok: ['_base', 'Cat/ModA']
rows = a.get_unit_mods('redux', 'redux_base_installer')['mods']
check('служебный ключ сохранён', '_base', rows[0]['key'])
check('в списке — человеческое имя', app.BASE_MOD_LABEL, rows[0]['name'])
check('есть пояснение, что это вне Mods', True, 'вне папки Mods' in rows[0]['desc'])
a.add_mod({'mode': 'src', 'camp': 'redux',
           'pack': {'camp': 'redux', 'unit': 'redux_base_installer', 'name': 'Redux'},
           'mod': '_base'})
check('в профиле запись названа по-человечески', app.BASE_MOD_LABEL,
      a.profile['mods'][0]['name'])
# маршрут: '_base' НЕ мод каталога — уходит в bulk (reconstruct_unit), а не в resolve_set
a._token = lambda: None
a._resolve_set_worker()
ps = a._pending_set
check('_base не ушёл в резолв каталога (иначе молча терялся)', 0,
      len((ps['plan'] or {}).get('order', [])))
check('_base поедет через bulk-ветку', ['_base'], [m.get('mod') for m in ps['bulk']])
# строка в дереве профиля: служебный ключ не должен утечь в подпись ни в одном режиме
a._names = {}
check('_name_of не лезет за ModuleInfo к псевдо-моду', app.BASE_MOD_SHORT,
      a._name_of('_base'))

print('\n=== «вся сборка» разделена на движок и моды ===')
def packs_full():
    """Паки трёх сборок: база + фикс к базе + мод + фикс к моду."""
    return {
        'redux/redux_base_installer': {'camp': 'redux', 'name': 'redux_base_installer',
                                       'tier': 'base', 'load_order': 10, 'bytes': 100},
        'redux/redux_fixes': {'camp': 'redux', 'name': 'redux_fixes', 'tier': 'fix',
                              'fix_parent': 'redux_base_installer', 'load_order': 20,
                              'bytes': 10},
        'redux/some_mods': {'camp': 'redux', 'name': 'some_mods', 'tier': 'mod',
                            'load_order': 50, 'bytes': 30},
        'redux/some_mods_fix': {'camp': 'redux', 'name': 'some_mods_fix', 'tier': 'fix',
                                'fix_parent': 'some_mods', 'load_order': 60, 'bytes': 5},
        'universe/universe_community': {'camp': 'universe', 'name': 'universe_community',
                                        'tier': 'base', 'load_order': 10, 'bytes': 100},
    }

a = fresh_api('redux'); a._repo = lambda: 'x/y'; a._packs_cache = packs_full()
pk = a._packs_cache
names = lambda part: sorted(p['name'] for p in a._camp_part_packs('redux', pk, part))
check('часть «движок» = база + фиксы К БАЗЕ',
      ['redux_base_installer', 'redux_fixes'], names(app.PART_BASE))
check('часть «моды» = моды + фиксы К МОДАМ',
      ['some_mods', 'some_mods_fix'], names(app.PART_MODS))
check('часть «all» (старые профили) = все паки сборки',
      ['redux_base_installer', 'redux_fixes', 'some_mods', 'some_mods_fix'],
      names(app.PART_ALL))
# порядок установки: база → моды → фиксы модов → фиксы базы (tier в packs.json = 'fix',
# сравнение только с 'fixes' делало ветку мёртвой и роняло фиксы в ранг обычного мода)
check('фикс базы ставится ПОСЛЕ модов (ранг 3)', 3,
      a._unit_install_rank(pk['redux/redux_fixes'], pk))
check('фикс мода — ранг 2 (после модов, до фиксов базы)', 2,
      a._unit_install_rank(pk['redux/some_mods_fix'], pk))
check('размер части считается по её пакам', 110, a._item_bytes(
    {'type': 'camp', 'camp': 'redux', 'part': app.PART_BASE}))

print('\n=== запрет второй базы (движки разных сборок не смешиваем) ===')
a = fresh_api('redux'); a._repo = lambda: 'x/y'; a._packs_cache = packs_full()
r1 = a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'part': 'base'})
check('движок redux добавлен', True, bool(r1.get('ok')))
r2 = a.add_mod({'mode': 'src', 'camp': 'universe', 'pack': None, 'part': 'base'})
check('движок universe ОТКЛОНЁН', False, bool(r2.get('ok')))
check('в отказе названа занявшая сборка', True, 'Свободная Бухта' in (r2.get('error') or ''))
r3 = a.add_mod({'mode': 'src', 'camp': 'universe', 'pack': None, 'part': 'mods'})
check('моды universe поверх базы redux — можно', True, bool(r3.get('ok')))
r4 = a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'part': 'base'})
check('повторный движок той же сборки → dup, без дубля', True, bool(r4.get('dup')))
check('в профиле ровно 2 записи', 2, len(a.profile['mods']))
# та же защита на пути «пак целиком» и «только базовые файлы пака»
r5 = a.add_mod({'mode': 'src', 'camp': 'universe',
                'pack': {'camp': 'universe', 'unit': 'universe_community', 'name': 'U'},
                'mod': ''})
check('base-пак другой сборки целиком — отказ', False, bool(r5.get('ok')))
r6 = a.add_mod({'mode': 'src', 'camp': 'universe',
                'pack': {'camp': 'universe', 'unit': 'universe_community', 'name': 'U'},
                'mod': '_base'})
check('«базовые файлы» другой сборки — отказ', False, bool(r6.get('ok')))
# профиль без движка: база добавляется свободно
a2 = fresh_api('redux'); a2._repo = lambda: 'x/y'; a2._packs_cache = packs_full()
a2.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'part': 'mods'})
check('моды не занимают базу', None, a2._profile_base_camp())
check('после модов redux движок universe разрешён', True,
      bool(a2.add_mod({'mode': 'src', 'camp': 'universe', 'pack': None,
                       'part': 'base'}).get('ok')))

print('\n=== строки профиля: видно, что именно добавлено ===')
a = fresh_api('redux'); a._repo = lambda: 'x/y'; a._packs_cache = packs_full()
a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'part': 'base'})
check('имя записи движка содержит сборку', True,
      'Свободная Бухта' in a.profile['mods'][0]['name'])
a.add_mod({'mode': 'src', 'camp': 'redux', 'pack': None, 'part': 'mods'})
check('имя записи модов отличается от записи движка', 2,
      len({m['name'] for m in a.profile['mods']}))
# заглушка (каталог не загрузился) больше не безымянная
a._catalog_cache = {}
a.config = {'mod_meta': {}}
a._names = {}
a._mods_dir = lambda: Path(r'C:\nonexistent_test_dir_zzz')
a._disk_index = {'mods': {}}
a._pub_cache_all = None
a._warm_variant_labels = lambda: None      # прогрев лезет в сеть/диск
a._lazy_load_catalog = lambda: None
tree = a.get_tree()
labels = [n['label'] for c in tree['camps'] for n in c['mods']]
check('движок — отдельная строка с понятным именем', True,
      any(l == app.PART_TITLES[app.PART_BASE] for l in labels))
check('заглушка модов названа со сборкой', True,
      any('Свободная Бухта' in l for l in labels))
check('безымянного «★ вся сборка» больше нет', False, any(l == '★ вся сборка' for l in labels))

print('\n=== установленный движок виден в списке (после установки очередь пустеет) ===')
def tree_rows(prof_extra, base_files=None):
    a = fresh_api('redux'); a._repo = lambda: 'x/y'; a._packs_cache = packs_full()
    a.config = {'mod_meta': {}}; a._names = {}; a._pub_cache_all = None
    a._catalog_cache = {}; a._disk_index = {'mods': {}}
    a._mods_dir = lambda: Path(r'C:\nonexistent_test_dir_zzz')
    a._warm_variant_labels = lambda: None
    a._lazy_load_catalog = lambda: None
    a.profile['mods'] = list(prof_extra)
    if base_files:
        a.profile['base_files'] = base_files
    t = a.get_tree()
    return [(n['label'], n['status']) for c in t['camps'] for n in c['mods']]

bf = {'camp': 'redux', 'unit': 'redux_base_installer', 'date': '2026-07-29T22:40:01'}
rows_after = tree_rows([], bf)                       # как после установки: очередь пуста
line = [r for r in rows_after if r[0] == app.PART_TITLES[app.PART_BASE]]
check('строка движка есть и после установки', 1, len(line))
check('и помечена установленной', True, bool(line and 'установлен' in line[0][1]))
# пока запись движка ещё в очереди — дубля быть не должно
rows_q = tree_rows([{'type': 'camp', 'camp': 'redux', 'part': 'base', 'name': 'X'}], bf)
check('в очереди — ровно одна строка движка', 1,
      len([r for r in rows_q if r[0] == app.PART_TITLES[app.PART_BASE]]))
check('и она в статусе очереди', True,
      any('добавлен' in r[1] for r in rows_q if r[0] == app.PART_TITLES[app.PART_BASE]))
# движок не ставили — строки нет
check('без установленной базы строки нет', 0,
      len([r for r in tree_rows([]) if r[0] == app.PART_TITLES[app.PART_BASE]]))
# факт установки записывается при установке base-пака
a = fresh_api('redux'); a._packs_cache = packs_full()
a._mark_base_installed('redux', 'redux_base_installer')
check('запомнены сборка и юнит движка', ('redux', 'redux_base_installer'),
      (a.profile['base_files']['camp'], a.profile['base_files']['unit']))
check('installed_base тоже проставлен', 'redux_base_installer',
      a.profile.get('installed_base'))

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
if FAIL:
    print('ПРОВАЛЫ:', FAIL); sys.exit(1)
