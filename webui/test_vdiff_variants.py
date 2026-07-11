"""versions_differ → переключатель вариантов по (СБОРКА × СЕМЕЙСТВО ПАКОВ).
Мод с одним каталожным ключом, versions_differ:true и несколькими источниками:
- installer+fixes ОДНОЙ дистрибуции (fix_parent) схлопываются в одну кнопку;
- РАЗНЫЕ паки одной сборки от разных авторов (Huk / Солянка) — РАЗНЫЕ кнопки (отзыв 19).
Плюс проверки связей: самоконфликт убран, обратные конфликты показаны.
Запуск: python webui/test_vdiff_variants.py"""
import sys, threading
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher\webui')
import app

PASS = []; FAIL = []
def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('[OK ] ' if cond else '[FAIL] ') + name + (f'  -> {extra}' if extra and not cond else ''))

MID = "Huk'sShit/Mod_Interface"

def base_catalog():
    return {
        MID: {
            'name': 'Mod_Interface', 'author': 'Huk', 'section': 'Твики',
            'description': 'краткое', 'full_description': 'полное',
            'default_source': 'redux/huk_mods', 'versions_differ': True,
            'variants': [
                {'source': 'redux/huk_mods', 'version': 'h', 'name': 'Mod_Interface',
                 'depends': [], 'conflicts': ['DenUIRecolor_Mod_Interface']},
                {'source': 'redux/huk_fixes', 'version': 'hf', 'name': 'Mod_Interface',
                 'depends': [], 'conflicts': ['DenUIRecolor_Mod_Interface']},   # фикс-слой huk
                {'source': 'redux/solyanka_main', 'version': 's', 'name': 'Mod_Interface',
                 'depends': [], 'conflicts': []},
                {'source': 'universe/universe_prochee', 'version': 'u', 'name': 'Mod_Interface',
                 'depends': [], 'conflicts': []},
            ],
        },
        'Cat/Solo': {'name': 'Solo', 'default_source': 'redux/x',
                     'variants': [{'source': 'redux/x'}]},
        # для проверки связей-конфликтов (односторонний конфликт + самоконфликт)
        'Free/FreePlayFromMenu': {'name': 'FreePlayFromMenu', 'default_source': 'redux/x',
            'variants': [{'source': 'redux/x', 'depends': [],
                          'conflicts': ['RefBPNoPtiority']}]},
        'Ref/RefBPNoPtiority': {'name': 'RefBPNoPtiority', 'default_source': 'redux/x',
            'variants': [{'source': 'redux/x', 'depends': [], 'conflicts': []}]},
        'Self/SelfConf': {'name': 'SelfConf', 'default_source': 'redux/x',
            'variants': [{'source': 'redux/x', 'depends': [],
                          'conflicts': ['SelfConf']}]},   # конфликт сам с собой
    }

def fresh():
    a = app.Api.__new__(app.Api)
    a.busy = False; a._cancel = threading.Event(); a._updates = {}
    a.profile = {'name': 't', 'game_path': '', 'mods': [], 'enabled': [], 'variants': {}}
    a._save_profile = lambda: None; a._emit = lambda *x, **k: None; a.log = lambda *x: None
    a._camps_idx = None; a._descs = {}; a._names = {}
    a._catalog_cache = base_catalog()
    # паки: huk_fixes — фикс-слой huk_mods (fix_parent) → схлоп; huk/solyanka — независимы
    a._fixparent = {'huk_fixes': 'huk_mods'}
    a._packs_cache = {
        'redux/huk_mods': {'name': 'huk_mods', 'display_name': 'Huk Mods', 'tier': 'mod'},
        'redux/huk_fixes': {'name': 'huk_fixes', 'display_name': 'Huk Fixes', 'tier': 'fix',
                            'fix_parent': 'huk_mods'},
        'redux/solyanka_main': {'name': 'solyanka_main', 'display_name': 'Солянка сборка',
                                'tier': 'mod'},
        'universe/universe_prochee': {'name': 'universe_prochee',
                                      'display_name': 'Universe прочее', 'tier': 'mod'},
    }
    a._installed_variant_key = lambda m: None
    a._mi_path = lambda m: type('P', (), {'exists': lambda s=None: False})()
    a._catalog_entry = lambda m: a._catalog_cache.get(m)
    return a

a = fresh(); mid = MID

# 1) переключатель: 3 кнопки — huk+huk_fixes схлопнуты, solyanka и universe отдельно
vs = a._variants_of(mid)
check('3 варианта (huk|solyanka|universe; фикс-слой схлопнут)', len(vs) == 3, str(vs))
names = sorted(v['name'] for v in vs)
check('имена по паку/сборке', names == sorted(['Huk', 'Солянка', 'Universe']), str(names))
check('ключи синтетические <base>#<source>', all('#' in v['key'] for v in vs), str([v['key'] for v in vs]))
check('все by_camp', all(v.get('by_camp') for v in vs), str(vs))
by_key = {v['key']: v for v in vs}
check('huk-группа → канон huk_mods (default_source, фикс схлопнут)',
      f"{mid}#redux/huk_mods" in by_key, str(list(by_key)))
check('solyanka — ОТДЕЛЬНАЯ кнопка (не схлопнута с huk)',
      f"{mid}#redux/solyanka_main" in by_key, str(list(by_key)))
check('universe — отдельная кнопка', f"{mid}#universe/universe_prochee" in by_key, str(list(by_key)))
redux_btns = [v for v in vs if v['camps'] == ['redux']]
check('в сборке redux ДВЕ кнопки (huk и solyanka)', len(redux_btns) == 2, str(redux_btns))

# 2) обычный мод — переключателя нет
check('у обычного мода вариантов нет', a._variants_of('Cat/Solo') == [])

# 3) резолв синтетического ключа
k_sol = f"{mid}#redux/solyanka_main"
check('_variant_ref → источник', a._variant_ref(k_sol) == (mid, 'redux/solyanka_main'), str(a._variant_ref(k_sol)))

# 4) дефолтный выбор = группа default_source (huk)
check('дефолт = huk-группа', a._chosen_variant(mid) == f"{mid}#redux/huk_mods", a._chosen_variant(mid))

# 5) выбор solyanka (та же сборка redux, ДРУГОЙ пак) → перекачка нужна (разные группы)
r = a.set_variant(mid, k_sol)
check('set_variant solyanka ok', r.get('ok') is True, str(r))
check('huk→solyanka (один redux, разные паки) → ПЕРЕКАЧКА',
      mid in a._updates and a._updates[mid].get('camp') == 'redux', str(a._updates.get(mid)))
check('чосен следует за выбором (solyanka)', a._chosen_variant(mid) == k_sol, a._chosen_variant(mid))
check('метка сборки = redux', a._camps_of(mid) == ['redux'], str(a._camps_of(mid)))

# 6) выбор universe → перекачка, камп universe
a2 = fresh()
k_uni = f"{mid}#universe/universe_prochee"
a2.set_variant(mid, k_uni)
check('смена сборки → перекачка (universe)',
      mid in a2._updates and a2._updates[mid].get('camp') == 'universe', str(a2._updates.get(mid)))
check('чосен = universe', a2._chosen_variant(mid) == k_uni, a2._chosen_variant(mid))

# 7) карточка конкретного источника: свои конфликты
info_h = a.get_mod_info(mid, f"{mid}#redux/huk_mods")['info']
info_s = a.get_mod_info(mid, k_sol)['info']
check('карточка huk: конфликт есть',
      [x['name'] for x in info_h['conflicts_ref']] == ['DenUIRecolor_Mod_Interface'],
      str(info_h.get('conflicts_ref')))
check('карточка solyanka: конфликтов нет', info_s['conflicts_ref'] == [], str(info_s.get('conflicts_ref')))
check('в карточке — 3 варианта', len(info_s['variants']) == 3, str(info_s['variants']))

# 8) file-match детект установленного источника → чосен = ЕГО группа (solyanka!), не huk
b = fresh()
huk = {f"{mid}/ModuleInfo.txt": 'mi_h', f"{mid}/CFG/Data.dat": 'd_h'}
sol = {f"{mid}/ModuleInfo.txt": 'mi_s', f"{mid}/CFG/Data.dat": 'd_s',
       f"{mid}/CFG/Rus/Lang.dat": 'lang'}
uni = {f"{mid}/ModuleInfo.txt": 'mi_u'}
del b._installed_variant_key                      # используем НАСТОЯЩИЙ детект
b._pub_cache_all = [('redux/huk_mods', huk), ('redux/huk_fixes', huk),
                    ('redux/solyanka_main', sol), ('universe/universe_prochee', uni)]
b._disk_index = {'mods': {mid: {'files': {r: {'sha': s} for r, s in sol.items()}}}}
check('детект установленного источника = solyanka',
      b._installed_variant_key(mid) == f"{mid}#redux/solyanka_main", b._installed_variant_key(mid))
check('_chosen_variant = solyanka (его группа, НЕ huk-канон)',
      b._chosen_variant(mid) == f"{mid}#redux/solyanka_main", b._chosen_variant(mid))
check('метка установленного = redux', b._camps_of(mid) == ['redux'], str(b._camps_of(mid)))
# выбор solyanka, когда solyanka на диске → без перекачки (та же группа)
b.set_variant(mid, f"{mid}#redux/solyanka_main")
check('выбор solyanka при установленной solyanka → без перекачки', mid not in b._updates, str(b._updates.get(mid)))
# выбор huk, когда на диске solyanka → перекачка (разные группы в одной сборке)
b._updates = {}
b.set_variant(mid, f"{mid}#redux/huk_mods")
check('solyanka→huk (один redux, разные паки) → ПЕРЕКАЧКА', mid in b._updates, str(b._updates.get(mid)))

# 9) связи: самоконфликт убран, обратный конфликт показан (отзывы «PolText сам с собой»,
#    «FreePlayFromMenu ← RefBPNoPtiority»)
c = fresh()
inf_self = c.get_mod_info('Self/SelfConf')['info']
check('самоконфликт убран (мод не конфликтует с собой)',
      inf_self['conflicts_ref'] == [], str(inf_self.get('conflicts_ref')))
inf_free = c.get_mod_info('Free/FreePlayFromMenu')['info']
check('прямой конфликт виден у FreePlayFromMenu',
      [x['name'] for x in inf_free['conflicts_ref']] == ['RefBPNoPtiority'],
      str(inf_free.get('conflicts_ref')))
inf_ref = c.get_mod_info('Ref/RefBPNoPtiority')['info']
check('ОБРАТНЫЙ конфликт виден у RefBPNoPtiority (← FreePlayFromMenu)',
      [x['name'] for x in inf_ref['conflicts_ref']] == ['FreePlayFromMenu'],
      str(inf_ref.get('conflicts_ref')))
check('обратный конфликт указывает на верный mid',
      [x['mid'] for x in inf_ref['conflicts_ref']] == ['Free/FreePlayFromMenu'],
      str(inf_ref.get('conflicts_ref')))

# 5) отзыв 4.1 — ИДЕНТИЧНОЕ содержимое в двух паках ОДНОЙ сборки (совпал хэш-версии)
#    схлопывается в ОДНУ кнопку (напр. PBFairanGraphics одинаков в fairans_vision_redux
#    и redux_base_installer). Каноничным остаётся default_source.
def dedup_api():
    a = fresh()
    a._catalog_cache = {
        'PB/FairanGraphics': {
            'name': 'FairanGraphics', 'default_source': 'redux/fairans_redux',
            'versions_differ': True,
            'variants': [
                {'source': 'redux/fairans_redux', 'version': 'X', 'name': 'FairanGraphics'},
                {'source': 'universe/fairans_uni', 'version': 'Y', 'name': 'FairanGraphics'},
                {'source': 'redux/redux_base', 'version': 'X', 'name': 'FairanGraphics'},  # == fairans_redux
            ],
        },
    }
    a._fixparent = {}
    a._packs_cache = {
        'redux/fairans_redux': {'name': 'fairans_redux', 'display_name': "Fairan's Vision", 'tier': 'mod'},
        'universe/fairans_uni': {'name': 'fairans_uni', 'display_name': "Fairan's Universe", 'tier': 'mod'},
        'redux/redux_base': {'name': 'redux_base', 'display_name': 'Universe Redux', 'tier': 'base'},
    }
    a._catalog_entry = lambda m: a._catalog_cache.get(m)
    return a

ad = dedup_api()
vd = ad._variants_of('PB/FairanGraphics')
check('4.1 идентичный билд в двух redux-паках → ОДНА redux-кнопка',
      len([v for v in vd if v['camps'] == ['redux']]) == 1, str(vd))
check('4.1 всего 2 кнопки (redux+universe)', len(vd) == 2, str(vd))
check('4.1 каноничным остался default_source (fairans_redux)',
      any(v['key'].endswith('redux/fairans_redux') for v in vd), str([v['key'] for v in vd]))
gsrc = ad._group_of_source(ad._variant_groups('PB/FairanGraphics'), 'redux/redux_base')
check('4.1 источник схлопнутого redux_base мапится на объединённую группу',
      bool(gsrc) and gsrc['key'].endswith('redux/fairans_redux'), str(gsrc))

# 6) отзыв 4 — РАЗНЫЕ билды (разный хэш) в двух паках-однофамильцах одной сборки
#    («Солянка» / «Солянка») НЕ схлопываются, но метки разводятся полным именем пака.
def leo_api():
    a = fresh()
    a._catalog_cache = {
        'Sol/LEO': {
            'name': 'LEO', 'default_source': 'redux/sol_graphpak', 'versions_differ': True,
            'variants': [
                {'source': 'redux/sol_graphpak', 'version': 'A', 'name': 'LEO'},
                {'source': 'redux/sol_main', 'version': 'B', 'name': 'LEO'},   # ДРУГОЙ билд
                {'source': 'universe/sol_uni', 'version': 'A', 'name': 'LEO'},
            ],
        },
    }
    a._fixparent = {}
    a._packs_cache = {
        'redux/sol_graphpak': {'name': 'sol_graphpak', 'display_name': 'Солянка графпак', 'tier': 'assets'},
        'redux/sol_main': {'name': 'sol_main', 'display_name': 'Солянка основная', 'tier': 'mod'},
        'universe/sol_uni': {'name': 'sol_uni', 'display_name': 'Солянка Universe', 'tier': 'mod'},
    }
    a._catalog_entry = lambda m: a._catalog_cache.get(m)
    return a

al = leo_api()
vl = al._variants_of('Sol/LEO')
redux_leo = [v for v in vl if v['camps'] == ['redux']]
check('4 разные билды «Солянка» → ДВЕ redux-кнопки (не схлопнуты)', len(redux_leo) == 2, str(vl))
check('4 метки redux-кнопок РАЗЛИЧАЮТСЯ (полное имя пака)',
      len({v['name'] for v in redux_leo}) == 2, str([v['name'] for v in redux_leo]))
check('4 обе метки не пустые', all(v['name'] for v in redux_leo), str(redux_leo))

# 7) конфликт от варианта ЧУЖОЙ сборки не «протекает» на сиблинг-вариант той же папки
#    (EndlessGame/PolKlissan: конфликт объявил ShuKlissan в original/universe, а у redux-игрока
#    в папке PolKlissan — конфликта быть не должно). Обратный индекс фильтруется по сборке.
def kli_api(camp):
    a = fresh()
    a._catalog_cache = {
        'Tw/EndlessX': {'name': 'EndlessX', 'default_source': 'redux/base',
                        'variants': [{'source': 'redux/base', 'conflicts': [], 'depends': []}]},
        # база папки = вариант ShuKli (только original/universe), объявляет конфликт с EndlessX
        'Shu/Kli': {'name': 'ShuKli', 'default_source': 'universe/comm',
                    'variants': [{'source': 'universe/comm', 'conflicts': ['EndlessX'], 'depends': []},
                                 {'source': 'original/orig', 'conflicts': ['EndlessX'], 'depends': []}]},
        # @-вариант той же папки = PolKli (redux), БЕЗ конфликта с EndlessX
        'Shu/Kli@PolKli': {'name': 'PolKli', 'default_source': 'redux/base',
                           'variants': [{'source': 'redux/base', 'conflicts': [], 'depends': []}]},
    }
    a._catalog_entry = lambda m: a._catalog_cache.get(m)
    a._inst_base_camp = camp
    return a

mids = lambda camp: [x['mid'] for x in kli_api(camp).get_mod_info('Tw/EndlessX')['info']['conflicts_ref']]
check('7 redux-игрок: конфликт ShuKli (original/universe) НЕ виснет на EndlessX',
      'Shu/Kli' not in mids('redux'), str(mids('redux')))
check('7 universe-игрок: конфликт ShuKli показан (там он реально есть)',
      'Shu/Kli' in mids('universe'), str(mids('universe')))
check('7 сборка неизвестна (None): поведение как раньше — конфликт виден',
      'Shu/Kli' in mids(None), str(mids(None)))

print(f'\n===== ИТОГ: PASS={len(PASS)} FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
