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
    a._paused = threading.Event(); a._chunk_prog = {}; a._chunk_lock = threading.Lock()
    a.profile = {'name': 't', 'game_path': '', 'mods': [], 'enabled': [], 'variants': {}}
    a._save_profile = lambda: None; a._emit = lambda *x, **k: None; a.log = lambda *x: None
    a._camps_idx = None; a._descs = {}; a._names = {}
    a.config = {'mod_meta': {}}
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

# 8) отзыв игрока: «выбираю одну сборку, а по умолчанию моды с меткой другой». На ЧИСТОЙ
#    игре установленной базы нет → холодный фолбэк падал на глобальный default_source
#    (тут huk = redux). Теперь сборку задаёт НАБОР игрока.
def prof_api(mods):
    a = fresh()
    a.profile['mods'] = list(mods)
    a._packs_cache = dict(a._packs_cache)
    a._packs_cache['universe/universe_base'] = {'camp': 'universe', 'name': 'universe_base',
                                                'tier': 'base'}
    return a

k_uni_g = f"{mid}#universe/universe_prochee"
k_huk_g = f"{mid}#redux/huk_mods"
check('8 без набора и без диска → как раньше, default_source (huk/redux)',
      prof_api([])._chosen_variant(mid) == k_huk_g, prof_api([])._chosen_variant(mid))
a_camp = prof_api([{'type': 'camp', 'camp': 'universe', 'part': 'all', 'name': 'X'}])
check('8 в наборе движок universe → вариант universe, а не default_source',
      a_camp._chosen_variant(mid) == k_uni_g, a_camp._chosen_variant(mid))
check('8 подсказка сборки = universe', a_camp._profile_camp_hint() == 'universe',
      str(a_camp._profile_camp_hint()))
# только моды сборки (движок игрок ставит свой) — сборку всё равно видно по набору
a_mods = prof_api([{'type': 'camp', 'camp': 'universe', 'part': 'mods', 'name': 'X'}])
check('8 в наборе только моды universe → тоже universe',
      a_mods._chosen_variant(mid) == k_uni_g, a_mods._chosen_variant(mid))
# две разных сборки в наборе — гадать нельзя, поведение прежнее
a_mix = prof_api([{'type': 'camp', 'camp': 'universe', 'part': 'mods', 'name': 'X'},
                  {'type': 'unit', 'camp': 'redux', 'unit': 'huk_mods', 'mod': '', 'name': 'Y'}])
check('8 сборок в наборе несколько → подсказки нет', a_mix._profile_camp_hint() is None,
      str(a_mix._profile_camp_hint()))
check('8 …и вариант остаётся по default_source', a_mix._chosen_variant(mid) == k_huk_g,
      a_mix._chosen_variant(mid))
# установленная база важнее набора (её файлы уже на диске)
a_inst = prof_api([{'type': 'camp', 'camp': 'universe', 'part': 'mods', 'name': 'X'}])
a_inst._inst_base_camp = 'redux'
check('8 установленный движок сильнее набора', a_inst._chosen_variant(mid) == k_huk_g,
      a_inst._chosen_variant(mid))
# явный выбор игрока сильнее всего
a_pick = prof_api([{'type': 'camp', 'camp': 'universe', 'part': 'all', 'name': 'X'}])
a_pick.profile['variants'] = {mid: k_sol}
check('8 явный выбор игрока сильнее набора', a_pick._chosen_variant(mid) == k_sol,
      a_pick._chosen_variant(mid))

# 9) массовое «версии модов — одной сборкой» (отзыв «бегать по списку и везде тыкать redux»)
def mass_api():
    a = fresh()
    a._catalog_cache = dict(base_catalog())
    # второй versions_differ-мод: есть и в redux, и в universe
    a._catalog_cache['Tw/Two'] = {
        'name': 'Two', 'default_source': 'redux/huk_mods', 'versions_differ': True,
        'variants': [{'source': 'redux/huk_mods', 'version': 'a', 'name': 'Two'},
                     {'source': 'universe/universe_prochee', 'version': 'b', 'name': 'Two'}]}
    # мод, которого в universe нет вовсе — трогать его нельзя
    a._catalog_cache['Tw/OnlyRedux'] = {
        'name': 'OnlyRedux', 'default_source': 'redux/huk_mods', 'versions_differ': True,
        'variants': [{'source': 'redux/huk_mods', 'version': 'a', 'name': 'OnlyRedux'},
                     {'source': 'redux/solyanka_main', 'version': 'b', 'name': 'OnlyRedux'}]}
    a._catalog_entry = lambda m: a._catalog_cache.get(m)
    return a

ALL = [mid, 'Tw/Two', 'Tw/OnlyRedux', 'Cat/Solo']
m = mass_api()
pv = m.set_variants_camp('universe', ALL, True)
check('9 preview считает только переключаемые', pv['count'] == 2, str(pv))
check('9 preview ничего не записал', m.profile.get('variants') in (None, {}), str(m.profile.get('variants')))
check('9 мод без universe-версии в список не попал',
      all(x['mid'] != 'Tw/OnlyRedux' for x in pv['items']), str(pv['items']))
check('9 обычный мод (без вариантов) не попал',
      all(x['mid'] != 'Cat/Solo' for x in pv['items']), str(pv['items']))
r = m.set_variants_camp('universe', ALL, False)
check('9 применение переключило два мода', r['count'] == 2, str(r))
check('9 выбор записан в профиль', m.profile['variants'].get(mid) == k_uni_g,
      str(m.profile.get('variants')))
check('9 второй мод тоже на universe',
      m.profile['variants'].get('Tw/Two') == 'Tw/Two#universe/universe_prochee',
      str(m.profile.get('variants')))
check('9 мод без universe-версии не тронут', 'Tw/OnlyRedux' not in m.profile['variants'],
      str(m.profile.get('variants')))
check('9 смена сборки помечена перекачкой', r['redownload'] == 2, str(r))
check('9 повтор той же сборки → менять нечего',
      m.set_variants_camp('universe', ALL, False)['count'] == 0, '')
check('9 без сборки — отказ', m.set_variants_camp('', ALL, True).get('ok') is False, '')
m2 = mass_api(); m2._catalog_cache = None
check('9 каталог не прогрет → внятная ошибка, а не падение',
      m2.set_variants_camp('universe', ALL, True).get('ok') is False, '')

# ─────────────────────────────────────────────────────────────────────────────
# 10) ГИБРИД: @-вариант (Pol/Shu), который сам раздаётся в НЕСКОЛЬКИХ сборках.
# Реальная топология каталога (28 модов: вся ShusRangers + OtherMods/SR1Equipment):
# ShuDomiks = original + universe (versions_differ), а redux даёт @PolDomiks.
# Раньше @-сиблинг выключал схему источников целиком: выбрать orig↔uni было нельзя,
# дата бралась у default_source (=original), пин источника не работал.
# ─────────────────────────────────────────────────────────────────────────────
print('\n--- 10: гибрид (Pol/Shu + версии сборок у одного варианта) ---')
HYB = 'ShusRangers/ShuDomiks'
POL = 'ShusRangers/ShuDomiks@PolDomiks'
K_ORIG, K_UNI = f'{HYB}#original/original_installer', f'{HYB}#universe/universe_community'

def hyb_api(camp_in_profile=None):
    a = fresh()
    a._catalog_cache = {
        HYB: {'name': 'ShuDomiks', 'versions_differ': True, 'mtime': 1664140628,
              'default_source': 'original/original_installer',
              'variants': [
                  {'source': 'original/original_installer', 'version': 'o',
                   'name': 'ShuDomiks', 'mtime': 1664140628, 'depends': [], 'conflicts': []},
                  {'source': 'universe/universe_community', 'version': 'u',
                   'name': 'ShuDomiks', 'mtime': 1780136596, 'depends': [], 'conflicts': []}]},
        POL: {'name': 'PolDomiks', 'default_source': 'redux/redux_base_installer',
              'mtime': 1781347879,
              'variants': [{'source': 'redux/redux_base_installer', 'version': 'p',
                            'name': 'PolDomiks', 'mtime': 1781347879,
                            'depends': [], 'conflicts': []}]},
    }
    a._packs_cache = {
        'original/original_installer': {'name': 'original_installer',
                                        'display_name': 'Original', 'tier': 'base'},
        'universe/universe_community': {'name': 'universe_community',
                                        'display_name': 'Universe Community', 'tier': 'base'},
        'redux/redux_base_installer': {'name': 'redux_base_installer',
                                       'display_name': 'ПБ Свободная Бухта', 'tier': 'base'},
    }
    a._fixparent = {}
    a._camps_idx = None
    a._get_packs = lambda tok=None: a._packs_cache
    if camp_in_profile:
        a.profile['mods'] = [{'type': 'camp', 'camp': camp_in_profile, 'part': 'mods'}]
    return a

h = hyb_api()
vs = h._variants_of(HYB)
check('10 кнопок три: Pol(redux) + Shu(orig) + Shu(uni)', len(vs) == 3, str(vs))
check('10 у Shu-кнопок метка сборки', {v['camps'][0] for v in vs} == {'redux', 'original', 'universe'},
      str(vs))
check('10 Shu-кнопки помечены dual (имя + сборка)',
      sum(1 for v in vs if v.get('dual')) == 2, str(vs))
check('10 ключи Shu-кнопок синтетические',
      {K_ORIG, K_UNI} <= {v['key'] for v in vs}, str([v['key'] for v in vs]))

# набор игрока = universe → и метка, и ДАТА должны быть universe-версии, а не default_source
hu = hyb_api('universe')
check('10 вариант по сборке набора = universe', hu._chosen_variant(HYB) == K_UNI,
      hu._chosen_variant(HYB))
check('10 дата = universe-билд (а не «дата ориг»)', hu._dev_date(HYB) == 1780136596,
      str(hu._dev_date(HYB)))
check('10 метка сборки = universe', hu._camps_of(HYB) == ['universe'], str(hu._camps_of(HYB)))

ho = hyb_api('original')
check('10 набор original → своя версия и дата', ho._chosen_variant(HYB) == K_ORIG
      and ho._dev_date(HYB) == 1664140628, f'{ho._chosen_variant(HYB)} {ho._dev_date(HYB)}')

# профиль «Свободной Бухты»: её версия мода едет под @Pol-ключом → и метка, и дата
# должны быть от него. Раньше имя ПАПКИ выдавалось за «установленный вариант» (Shu), и
# на redux-профиле мод показывался с меткой ORIG/UNI и датой original-билда.
hr = hyb_api('redux')
check('10 профиль redux → метка redux (Pol-вариант)', hr._camps_of(HYB) == ['redux'],
      str(hr._camps_of(HYB)))
check('10 …и дата Pol-версии, а не базовой записи', hr._dev_date(HYB) == 1781347879,
      str(hr._dev_date(HYB)))
hd = hyb_api('redux')
hd._disk_name = lambda m: 'ShuDomiks'            # а вот РЕАЛЬНО установленный вариант
check('10 диск сильнее сборки набора', hd._camps_of(HYB) != ['redux'], str(hd._camps_of(HYB)))
check('10 неустановленный мод не считается установленным',
      hyb_api('redux')._installed_variant_key(HYB) is None, '')

# выбор источника внутри Shu-ключа + пин при установке сборки
hs = hyb_api('universe')
check('10 выбор orig-версии принят', hs.set_variant(HYB, K_ORIG).get('ok') is True, '')
check('10 выбор записан в профиль', hs.profile['variants'].get(HYB) == K_ORIG,
      str(hs.profile.get('variants')))
check('10 пин источника заработал (был невозможен)',
      hs._pinned_sources() == {HYB: {'original/original_installer'}}, str(hs._pinned_sources()))
check('10 после выбора дата следует за выбором', hs._dev_date(HYB) == 1664140628,
      str(hs._dev_date(HYB)))

# выбор Pol-варианта: другой мод → перекачка, источников у него один → пина нет
hp = hyb_api('universe')
hp._installed_variant_key = lambda m: K_UNI
r = hp._set_variant_nosave(HYB, POL)
check('10 смена Shu→Pol помечена перекачкой', r.get('differ') is True, str(r))
check('10 выбор Pol не создаёт пин источника', hp._pinned_sources() == {},
      str(hp._pinned_sources()))
check('10 у Pol-варианта переключателя версий нет', hp._source_variants_key(POL) == [], '')

# массовое «версии одной сборкой»
hm = hyb_api('universe')
hm.set_variant(HYB, K_ORIG)                     # игрок сидит на original-версии
pv = hm.set_variants_camp('universe', [HYB], True)
check('10 массово на universe — гибрид переключается', pv['count'] == 1, str(pv))
hm.set_variants_camp('universe', [HYB], False)
check('10 …и выбор реально записан', hm.profile['variants'].get(HYB) == K_UNI,
      str(hm.profile.get('variants')))
hm2 = hyb_api('universe')
pv2 = hm2.set_variants_camp('redux', [HYB], True)
check('10 массово на redux — не трогаем (это Pol, другой мод)', pv2['count'] == 0, str(pv2))
check('10 …и причина названа явно', pv2['skipped']['variant'] == 1, str(pv2['skipped']))
hm3 = hyb_api('original')
pv3 = hm3.set_variants_camp('original', [HYB], True)
check('10 уже этой сборки → в «already», а не в тишину',
      pv3['count'] == 0 and pv3['skipped']['already'] == 1, str(pv3))

print(f'\n===== ИТОГ: PASS={len(PASS)} FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
