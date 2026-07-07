"""versions_differ → переключатель вариантов ПО БАЗОВОЙ СБОРКЕ (не по паку).
Мод с одним каталожным ключом, versions_differ:true и несколькими источниками получает
переключатель с ОДНОЙ кнопкой на сборку: несколько паков одной сборки (installer+fixes)
схлопываются, кнопка именуется сборкой. Выбор/резолв/карточка/детект-апдейта работают.
Запуск: python webui/test_vdiff_variants.py"""
import sys, threading
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher\webui')
import app

PASS = []; FAIL = []
def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('[OK ] ' if cond else '[FAIL] ') + name + (f'  -> {extra}' if extra and not cond else ''))

def fresh():
    a = app.Api.__new__(app.Api)
    a.busy = False; a._cancel = threading.Event(); a._updates = {}
    a.profile = {'name': 't', 'game_path': '', 'mods': [], 'enabled': [], 'variants': {}}
    a._save_profile = lambda: None; a._emit = lambda *x, **k: None; a.log = lambda *x: None
    a._camps_idx = None
    a._descs = {}
    # versions_differ мод: один ключ, 3 источника. ДВА из них — redux (huk+solyanka),
    # один — universe. Переключатель должен показать 2 кнопки: redux и universe.
    mid = "Huk'sShit/Mod_Interface"
    a._catalog_cache = {
        mid: {
            'name': 'Mod_Interface', 'author': 'Huk', 'section': 'Твики',
            'description': 'краткое', 'full_description': 'полное',
            'default_source': 'redux/huk_mods', 'versions_differ': True,
            'variants': [
                {'source': 'redux/huk_mods', 'version': 'h', 'name': 'Mod_Interface',
                 'depends': [], 'conflicts': ['DenUIRecolor_Mod_Interface']},
                {'source': 'redux/solyanka_main', 'version': 's', 'name': 'Mod_Interface',
                 'depends': [], 'conflicts': []},
                {'source': 'universe/universe_prochee', 'version': 'u', 'name': 'Mod_Interface',
                 'depends': [], 'conflicts': []},
            ],
        },
        # обычный мод для контроля (один источник → нет переключателя)
        'Cat/Solo': {'name': 'Solo', 'default_source': 'redux/x',
                     'variants': [{'source': 'redux/x'}]},
    }
    a._installed_variant_key = lambda m: None      # на диске не определяем (нет файлов)
    a._mi_path = lambda m: type('P', (), {'exists': lambda s=None: False})()
    a._catalog_entry = lambda m: a._catalog_cache.get(m)
    return a, mid

a, mid = fresh()

# 1) переключатель: ОДНА кнопка на сборку (2 redux-пака схлопнуты), имена = сборки
vs = a._variants_of(mid)
check('2 варианта (по сборкам, паки схлопнуты)', len(vs) == 2, str(vs))
names = sorted(v['name'] for v in vs)
check('варианты названы по СБОРКЕ', names == ['redux', 'universe'], str(names))
check('ключи синтетические <base>#<source>', all('#' in v['key'] for v in vs), str([v['key'] for v in vs]))
check('вариант помечен by_camp', all(v.get('by_camp') for v in vs), str(vs))
by_camp = {v['camps'][0]: v['key'] for v in vs}
# redux-кнопка ведёт на канонический источник (default_source huk_mods), НЕ solyanka
check('redux → default_source (huk_mods)', by_camp['redux'] == f"{mid}#redux/huk_mods", str(by_camp))
check('universe → его источник', by_camp['universe'] == f"{mid}#universe/universe_prochee", str(by_camp))

# 2) обычный мод — переключателя нет
check('у обычного мода вариантов нет', a._variants_of('Cat/Solo') == [])

# 3) резолв ключа → источник
k_sol = f"{mid}#redux/solyanka_main"
check('_variant_ref синтетического → источник', a._variant_ref(k_sol) == (mid, 'redux/solyanka_main'), str(a._variant_ref(k_sol)))

# 4) дефолтный выбранный вариант = канонический ключ сборки default_source (huk)
ch = a._chosen_variant(mid)
check('дефолтный выбор = redux-канон (huk)', ch == f"{mid}#redux/huk_mods", ch)

# 5) выбор ДРУГОЙ сборки (universe) — принимается и помечает перекачку
k_uni = f"{mid}#universe/universe_prochee"
r = a.set_variant(mid, k_uni)
check('set_variant universe ok', r.get('ok') is True, str(r))
check('выбор сохранён в профиле', a.profile['variants'][mid] == k_uni)
check('смена сборки → перекачка (universe)', mid in a._updates and a._updates[mid].get('camp') == 'universe', str(a._updates.get(mid)))
check('метка сборки следует за выбором (universe)', a._camps_of(mid) == ['universe'], str(a._camps_of(mid)))
check('чосен = выбранная сборка (universe)', a._chosen_variant(mid) == k_uni, a._chosen_variant(mid))

# 6) карточка конкретного источника: свои конфликты (huk конфликтует, solyanka — нет).
#    Явно запрошенный источник карточки по-прежнему поддержан (внутренний доступ).
info_h = a.get_mod_info(mid, f"{mid}#redux/huk_mods")['info']
info_s = a.get_mod_info(mid, k_sol)['info']
check('карточка huk: есть конфликт', [x['name'] for x in info_h['conflicts_ref']] == ['DenUIRecolor_Mod_Interface'], str(info_h.get('conflicts_ref')))
check('карточка solyanka: конфликтов нет', info_s['conflicts_ref'] == [], str(info_s.get('conflicts_ref')))
check('в карточке — 2 варианта-сборки', len(info_s['variants']) == 2, str(info_s['variants']))

# 7) file-match детект установленного источника, но чосен-кнопка = КАНОН сборки
b = app.Api.__new__(app.Api)
b.busy = False; b._cancel = threading.Event(); b._updates = {}
b.profile = {'name': 't', 'game_path': '', 'mods': [], 'enabled': [], 'variants': {}}
b._save_profile = lambda: None; b._emit = lambda *x, **k: None; b.log = lambda *x: None
b._camps_idx = None; b._descs = {}; b._names = {}
b._catalog_cache = a._catalog_cache
b._catalog_entry = lambda m: b._catalog_cache.get(m)
huk = {f"{mid}/ModuleInfo.txt": 'mi_h', f"{mid}/CFG/Data.dat": 'd_h'}
sol = {f"{mid}/ModuleInfo.txt": 'mi_s', f"{mid}/CFG/Data.dat": 'd_s',
       f"{mid}/CFG/Rus/Lang.dat": 'lang'}      # solyanka несёт Lang.dat, huk — нет
uni = {f"{mid}/ModuleInfo.txt": 'mi_u'}
b._pub_cache_all = [('redux/huk_mods', huk), ('redux/solyanka_main', sol),
                    ('universe/universe_prochee', uni)]
b._disk_index = {'mods': {mid: {'files': {r: {'sha': s} for r, s in sol.items()}}}}
check('детект установленного источника = solyanka (внутр.)',
      b._installed_variant_key(mid) == f"{mid}#redux/solyanka_main", b._installed_variant_key(mid))
# оба redux-пака схлопнуты в кнопку redux → чосен показывает КАНОН redux (huk), не solyanka
check('_chosen_variant = redux-канон при установленной solyanka',
      b._chosen_variant(mid) == f"{mid}#redux/huk_mods", b._chosen_variant(mid))
check('метка сборки установленного = redux', b._camps_of(mid) == ['redux'], str(b._camps_of(mid)))
# выбор кнопки redux, когда на диске уже redux (пусть и другой пак) — БЕЗ перекачки
r = b.set_variant(mid, f"{mid}#redux/huk_mods")
check('выбор той же сборки redux → НЕ помечает перекачку', mid not in b._updates, str(b._updates.get(mid)))
b._disk_index = {'mods': {mid: {'files': {r: {'sha': s} for r, s in huk.items()}}}}
check('детект переключается на huk при смене файлов на диске',
      b._installed_variant_key(mid) == f"{mid}#redux/huk_mods", b._installed_variant_key(mid))
b._updates = {}
b._pub_cache_all = None                          # холодный кэш → без форс-загрузки
check('холодный кэш → детект None', b._installed_source_key(mid) is None)
check('_chosen_variant при холодном кэше = redux-канон',
      b._chosen_variant(mid) == f"{mid}#redux/huk_mods", b._chosen_variant(mid))

print(f'\n===== ИТОГ: PASS={len(PASS)} FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
