"""Дата мода в списке = дата изменения РАЗРАБОТЧИКОМ (catalog[...].mtime), а не дата
установки на диск. Для versions_differ берётся mtime ВЫБРАННОГО варианта (фикс-пак с более
новым файлом → свежее). Нет mtime в каталоге (старые манифесты) → откат на дату диска.
Запуск: python webui/test_dev_date.py"""
import sys
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher\webui')
import app
from datetime import datetime

FAIL = []
def ck(n, c, extra=''):
    print(('[OK ] ' if c else '[FAIL] ') + n + (f'  -> {extra}' if extra and not c else ''))
    if not c:
        FAIL.append(n)

DEV = 1600000000  # 2020-09-13

def mk():
    a = app.Api.__new__(app.Api)
    a._camps_idx = None; a.profile = {'variants': {}}; a._updates = {}
    a._save_profile = lambda: None; a._emit = lambda *x, **k: None
    return a

# 1) обычный мод — dev-дата из top-level каталога, а не с диска
a = mk()
a._catalog_cache = {'Cat/Mod': {'name': 'Mod', 'mtime': DEV, 'variants': [{'source': 'redux/x'}]}}
ck('обычный мод: _dev_date из каталога', a._dev_date('Cat/Mod') == DEV, str(a._dev_date('Cat/Mod')))
ck('display = dev-дата (не диск)',
   a._display_date('Cat/Mod', '2026-07-07T10:00:00') == app.fmt_date(datetime.fromtimestamp(DEV).isoformat()))

# 2) каталог без mtime (старые данные) → откат на дату файлов на диске
a2 = mk(); a2._catalog_cache = {'Cat/Mod': {'name': 'Mod', 'variants': [{'source': 'redux/x'}]}}
ck('нет mtime → _dev_date None', a2._dev_date('Cat/Mod') is None)
ck('нет mtime → display = дата диска',
   a2._display_date('Cat/Mod', '2026-07-07T10:00:00') == app.fmt_date('2026-07-07T10:00:00'))

# 3) versions_differ: дата ВЫБРАННОГО варианта; фикс-пак новее установщика
mid = 'Huk/Mi'; FIX = DEV + 900000
a3 = mk()
a3._catalog_cache = {mid: {
    'name': 'Mi', 'default_source': 'redux/fixes', 'versions_differ': True, 'mtime': FIX,
    'variants': [
        {'source': 'redux/fixes', 'version': 'f', 'mtime': FIX, 'name': 'Mi'},
        {'source': 'redux/installer', 'version': 'i', 'mtime': DEV, 'name': 'Mi'},
        {'source': 'universe/community', 'version': 'u', 'mtime': DEV + 100, 'name': 'Mi'}]}}
a3._installed_variant_key = lambda m: None
ck('vd: дефолт (redux-канон) берёт дату фикс-пака', a3._dev_date(mid) == FIX, str(a3._dev_date(mid)))
a3.profile['variants'][mid] = f'{mid}#universe/community'
ck('vd: выбор universe → его дата', a3._dev_date(mid) == DEV + 100, str(a3._dev_date(mid)))

# 4) отзыв 3: холодный кэш (установленный источник не определён) → дата берётся из
#    варианта СБОРКИ игрока, а не из глобального default_source (у AMod_Merchant
#    default_source=universe, но redux-игрок должен видеть дату redux-билда).
mid4 = 'An/AMod'; RED = DEV + 500000; UNI = DEV + 200000
def mk_amod(camp):
    a = mk()
    a._catalog_cache = {mid4: {
        'name': 'AMod', 'default_source': 'universe/uni_pack', 'versions_differ': True, 'mtime': UNI,
        'variants': [
            {'source': 'redux/red_pack', 'version': 'r', 'mtime': RED, 'name': 'AMod'},
            {'source': 'universe/uni_pack', 'version': 'u', 'mtime': UNI, 'name': 'AMod'}]}}
    a._installed_variant_key = lambda m: None       # кэш file-match не прогрет
    a._packs_cache = {'redux/red_pack': {'name': 'red_pack', 'display_name': 'Red'},
                      'universe/uni_pack': {'name': 'uni_pack', 'display_name': 'Uni'}}
    a._fixparent = {}
    a._inst_base_camp = camp
    return a
ck('vd холодный: redux-игрок → дата redux-билда (не universe-дефолт)',
   mk_amod('redux')._dev_date(mid4) == RED, str(mk_amod('redux')._dev_date(mid4)))
ck('vd холодный: universe-игрок → дата universe-билда',
   mk_amod('universe')._dev_date(mid4) == UNI, str(mk_amod('universe')._dev_date(mid4)))
ck('vd холодный: база неизвестна → global default_source (universe)',
   mk_amod(None)._dev_date(mid4) == UNI, str(mk_amod(None)._dev_date(mid4)))

print(f'\n===== ИТОГ: FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
