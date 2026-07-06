"""versions_differ → переключатель вариантов по ИСТОЧНИКУ (п.10, «как Pol/Shu»).
Проверяем: мод с одним каталожным ключом, versions_differ:true и 3 источниками
получает 3 варианта переключателя; выбор/резолв/карточка/детект-апдейта работают
по источнику. Запуск: python webui/test_vdiff_variants.py"""
import sys, threading
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher\webui')
import app

PASS = []; FAIL = []
def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('[OK ] ' if cond else '[FAIL] ') + name + (f'  → {extra}' if extra and not cond else ''))

def fresh():
    a = app.Api.__new__(app.Api)
    a.busy = False; a._cancel = threading.Event(); a._updates = {}
    a.profile = {'name': 't', 'game_path': '', 'mods': [], 'enabled': [], 'variants': {}}
    a._save_profile = lambda: None; a._emit = lambda *x, **k: None; a.log = lambda *x: None
    a._camps_idx = None
    a._descs = {}
    # versions_differ мод: один ключ, 3 источника (solyanka+huk=redux, universe)
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

# 1) переключатель: 3 варианта по источнику, имена = юниты, каждый со своим кампом
vs = a._variants_of(mid)
check('3 варианта у versions_differ-мода', len(vs) == 3, str(vs))
names = sorted(v['name'] for v in vs)
check('варианты названы по юниту', names == ['huk_mods', 'solyanka_main', 'universe_prochee'], str(names))
check('ключи синтетические <base>#<source>', all('#' in v['key'] for v in vs), str([v['key'] for v in vs]))
camp_of = {v['name']: v['camps'] for v in vs}
check('камп solyanka=redux', camp_of['solyanka_main'] == ['redux'], str(camp_of))
check('камп universe=universe', camp_of['universe_prochee'] == ['universe'], str(camp_of))

# 2) обычный мод — переключателя нет
check('у обычного мода вариантов нет', a._variants_of('Cat/Solo') == [])

# 3) резолв ключа → источник
k_sol = f"{mid}#redux/solyanka_main"
check('_variant_ref синтетического → источник', a._variant_ref(k_sol) == (mid, 'redux/solyanka_main'), str(a._variant_ref(k_sol)))

# 4) дефолтный выбранный вариант = default_source (huk)
ch = a._chosen_variant(mid)
check('дефолтный выбор = default_source huk', ch == f"{mid}#redux/huk_mods", ch)

# 5) выбор варианта игроком принимается + помечает апдейт (другой источник)
r = a.set_variant(mid, k_sol)
check('set_variant синтетического ключа ok', r.get('ok') is True, str(r))
check('выбор сохранён в профиле', a.profile['variants'][mid] == k_sol)
check('помечен на перекачку (другой источник)', mid in a._updates and a._updates[mid].get('camp') == 'redux', str(a._updates.get(mid)))
check('метка сборки следует за выбором (redux)', a._camps_of(mid) == ['redux'], str(a._camps_of(mid)))

# 6) карточка конкретного источника: свои конфликты (huk конфликтует, solyanka — нет)
info_h = a.get_mod_info(mid, f"{mid}#redux/huk_mods")['info']
info_s = a.get_mod_info(mid, k_sol)['info']
check('карточка huk: есть конфликт', [x['name'] for x in info_h['conflicts_ref']] == ['DenUIRecolor_Mod_Interface'], str(info_h.get('conflicts_ref')))
check('карточка solyanka: конфликтов нет', info_s['conflicts_ref'] == [], str(info_s.get('conflicts_ref')))
check('в карточке показан выбранный вариант', info_s['variant_key'] == k_sol)

print(f'\n===== ИТОГ: PASS={len(PASS)} FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
