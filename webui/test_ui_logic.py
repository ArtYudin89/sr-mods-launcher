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

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
if FAIL:
    print('ПРОВАЛЫ:', FAIL); sys.exit(1)
