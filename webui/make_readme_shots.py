# -*- coding: utf-8 -*-
"""Генератор скриншотов для README (руководство игрока).

Запуск:  python webui/make_readme_shots.py
Результат: PNG в  docs/img/  (в корне репозитория).

Данные — синтетические и НЕЙТРАЛЬНЫЕ: базовая сборка стоит на «— авто —»
(рекомендация самого лаунчера), моды поровну распределены между сборками
REDUX / UNIVERSE / ORIGINAL, названия модов — обобщённые примеры и не
повторяют реальные паки конкретных авторов. Ни одна сборка не показана как
«основная» или предпочтительная.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

WEB_DIR = Path(__file__).parent / 'web'
OUT_DIR = Path(__file__).parent.parent / 'docs' / 'img'
OUT_DIR.mkdir(parents=True, exist_ok=True)

VERSION = '0.22.0'

# ───────── Нейтральные mock-данные ─────────

def _state(game_path='D:\\Games\\Space Rangers HD', tutorial_done=True, theme='dark'):
    return {
        'theme': theme,
        'tree_mode': 'folder',
        'name_mode': 'module',
        'log_verbose': False,
        'profiles': ['Основной', 'Хардкор'],
        'current_profile': 'Основной',
        'game_path': game_path,
        'base': '',                 # «— авто —»: нейтрально, рекомендация лаунчера
        'repo': 'ArtYudin89/sr-mods-aggregator',
        'has_token': False,
        'forks': [],
        'is_rwt': False,
        'version': VERSION,
        'busy': False,
        'desc_in_list': True,
        'show_hidden': True,
        'tutorial_done': tutorial_done,
    }


def _mod(iid, label, mid, sc, st, in_game=False, in_profile=True,
         date='10.07.2026', section='', desc='', labels=None, variants=None,
         chosen='', mergeable=False, hidden=False, tags=None, note='', full_desc=''):
    return {
        'iid': iid, 'label': label, 'kind': 'мод',
        'name': label, 'status_class': sc, 'status': st,
        'in_game': in_game, 'in_profile': in_profile,
        'date': date, 'mid': mid,
        'selectable': True,
        'folder': mid.split('/')[0] if '/' in mid else '',
        'section': section, 'desc': desc,
        'has_info': True,
        'labels': labels or [],
        'variants': variants or [],
        'chosen': chosen,
        'mergeable': mergeable,
        'hidden': hidden, 'tags': tags or [], 'note': note, 'full_desc': full_desc,
    }


def _tree():
    # По одной паре модов на каждую сборку — сбалансированно, без выделения одной.
    redux = [
        _mod('d:OrionQuest/OrionQuestPack', 'Квесты Ориона',
             'OrionQuest/OrionQuestPack', 'ok', '✅ установлен',
             in_game=True, section='Сюжет', labels=['redux'], mergeable=True,
             full_desc='Пример сюжетного мода: добавляет цепочку квестов. '
                       'Данные демонстрационные.'),
        _mod('d:TradeBalance/TradeBalance', 'Торговый баланс',
             'TradeBalance/TradeBalance', 'upd', '⬆ обновление',
             in_game=True, section='Баланс', labels=['redux'], mergeable=True,
             tags=['Любимое']),
    ]
    universe = [
        _mod('d:NebulaShips/NebulaShips', 'Корабли Туманности',
             'NebulaShips/NebulaShips', 'ok', '✅ установлен',
             in_game=True, section='Корабли', labels=['universe'], mergeable=True),
        _mod('d:EconomyPlus/EconomyPlus', 'Экономика+',
             'EconomyPlus/EconomyPlus', 'queued', '➕ добавлен',
             in_profile=True, section='Экономика', labels=['universe'], mergeable=True),
    ]
    original = [
        _mod('d:ClassicHUD/ClassicHUD', 'Классический интерфейс',
             'ClassicHUD/ClassicHUD', 'ok', '✅ установлен',
             in_game=True, section='Интерфейс', labels=['original'], mergeable=True),
        _mod('d:RetroTextures/RetroTextures', 'Ретро-текстуры',
             'RetroTextures/RetroTextures', 'ok', '✅ установлен',
             section='Графика', labels=['original'], mergeable=True),
    ]
    # Мод, доступный в двух сборках сразу — выбор варианта делает игрок.
    multi = [
        _mod('d:StarMap/StarMapExpanded', 'Расширенная карта',
             'StarMap/StarMapExpanded', 'queued', '➕ добавлен',
             in_profile=True, section='Галактика', labels=['redux', 'universe'],
             variants=[
                 {'id': 'StarMap/StarMapExpanded', 'source': 'redux/redux_pack',
                  'camp': 'redux', 'label': 'REDUX'},
                 {'id': 'StarMap/StarMapExpanded@Alt', 'source': 'universe/universe_pack',
                  'camp': 'universe', 'label': 'UNIVERSE'},
             ], chosen='redux', mergeable=True),
    ]
    available = _mod('e:ExtraQuests/ExtraQuests', 'Дополнительные квесты',
                     'ExtraQuests/ExtraQuests', 'avail', '📥 доступен',
                     in_profile=False, section='Сюжет', labels=['original'])

    return {
        'camps': [
            {'label': 'REDUX', 'kind': 'camp', 'packs': [
                {'label': 'Сборка REDUX', 'kind': 'pack', 'mods': redux}], 'mods': []},
            {'label': 'UNIVERSE', 'kind': 'camp', 'packs': [
                {'label': 'Сборка UNIVERSE', 'kind': 'pack', 'mods': universe}], 'mods': []},
            {'label': 'ORIGINAL', 'kind': 'camp', 'packs': [
                {'label': 'Сборка ORIGINAL', 'kind': 'pack', 'mods': original}], 'mods': []},
            {'label': 'в нескольких сборках', 'kind': 'camp', 'packs': [
                {'label': 'Общие моды', 'kind': 'pack', 'mods': multi}], 'mods': []},
            {'label': 'набор профиля', 'kind': 'camp', 'packs': [], 'mods': [available]},
        ],
        'base': {'camp': '', 'name': ''},
        'base_manual': '',
        'queue': {'count': 3, 'size': '0.9 ГБ'},
        'tree_mode': 'folder',
        'name_mode': 'module',
        'all_tags': ['Любимое'],
        'hidden_count': 0,
        'show_hidden': True,
        'desc_in_list': True,
    }


def _all_mods():
    return {'ok': True, 'mods': [
        {'id': 'OrionQuest/OrionQuestPack', 'name': 'Квесты Ориона',
         'camps': ['redux'], 'desc': 'Сюжетная цепочка квестов (пример).'},
        {'id': 'NebulaShips/NebulaShips', 'name': 'Корабли Туманности',
         'camps': ['universe'], 'desc': 'Новые модели кораблей (пример).'},
        {'id': 'ClassicHUD/ClassicHUD', 'name': 'Классический интерфейс',
         'camps': ['original'], 'desc': 'Интерфейс в классическом стиле (пример).'},
        {'id': 'StarMap/StarMapExpanded', 'name': 'Расширенная карта',
         'camps': ['redux', 'universe'], 'desc': 'Больше систем на карте галактики (пример).'},
        {'id': 'ExtraQuests/ExtraQuests', 'name': 'Дополнительные квесты',
         'camps': ['original'], 'desc': 'Ещё квесты (пример).'},
    ]}


def _mod_info(mid, variant=None):
    names = {
        'OrionQuest/OrionQuestPack': 'Квесты Ориона',
        'TradeBalance/TradeBalance': 'Торговый баланс',
        'NebulaShips/NebulaShips': 'Корабли Туманности',
        'EconomyPlus/EconomyPlus': 'Экономика+',
        'ClassicHUD/ClassicHUD': 'Классический интерфейс',
        'RetroTextures/RetroTextures': 'Ретро-текстуры',
        'StarMap/StarMapExpanded': 'Расширенная карта',
        'ExtraQuests/ExtraQuests': 'Дополнительные квесты',
    }
    is_multi = mid == 'StarMap/StarMapExpanded'
    info = {
        'name': names.get(mid, mid.split('/')[-1]),
        'section': 'Галактика' if is_multi else 'Сюжет',
        'authors': 'Демонстрационные данные',
        'small': 'Краткое описание мода (пример для руководства).',
        'full': 'Полное описание мода. Здесь автор рассказывает, что мод меняет '
                'в игре, какие требования и с чем он несовместим.\n\nВсе данные на '
                'скриншоте — демонстрационные.',
        'full_html': '<p>Полное описание мода (пример). Мод добавляет новые '
                     'возможности в игру.</p>',
        'requires': [{'name': 'Квесты Ориона', 'mid': 'OrionQuest/OrionQuestPack'}],
        'dependents': [{'name': 'Экономика+', 'mid': 'EconomyPlus/EconomyPlus'}],
        'conflicts': ['Ретро-текстуры'],
        'conflicts_ref': [{'name': 'Ретро-текстуры', 'mid': 'RetroTextures/RetroTextures'}],
        'id': mid,
        'location': mid.replace('/', '\\'),
        'installed': True,
        'variants': [
            {'id': 'StarMap/StarMapExpanded', 'camp': 'redux', 'label': 'REDUX'},
            {'id': 'StarMap/StarMapExpanded@Alt', 'camp': 'universe', 'label': 'UNIVERSE'},
        ] if is_multi else [],
        'variant_key': variant or '',
    }
    return {'ok': True, 'info': info}


def _update_order(needs=False):
    return {
        'ok': True, 'base': '',
        'order': ['redux', 'universe', 'original'],
        'all_camps': ['redux', 'universe', 'original'],
        'needs_order': needs,
    }


MOCK = {
    'get_state': lambda a: _state(),
    'get_tree': lambda a: _tree(),
    'get_update_order': lambda a: _update_order(False),
    'check_self_update': lambda a: {'ok': True, 'update': False, 'version': VERSION,
                                    'current': VERSION, 'url': '', 'notes': ''},
    'get_all_mods': lambda a: _all_mods(),
    'get_camp_packs': lambda a: {'ok': True, 'camps': {}},
    'get_mod_info': lambda a: _mod_info(*a),
    'check_compat': lambda a: {'ok': True, 'conflicts': [], 'warnings': [],
                               'summary': 'Конфликтов нет.'},
    'get_unit_mods': lambda a: {'ok': True, 'mods': []},
    'mods_info': lambda a: {'ok': True, 'count': 6, 'kept': 0,
                            'path': 'D:\\Games\\Space Rangers HD\\Mods', 'mods': []},
}
# всё, что меняет состояние, — no-op заглушки
for _m in ('set_base', 'set_theme', 'set_tree_mode', 'set_name_mode', 'set_verbose',
           'set_update_extra', 'set_forks', 'save_settings', 'switch_profile',
           'new_profile', 'delete_profile', 'add_mod', 'remove_pidx', 'clear_queue',
           'toggle_enabled', 'set_variant', 'plan_enable', 'plan_disable', 'install',
           'install_set_with_deps', 'confirm_install_deps', 'start_merge', 'apply_merge',
           'merge_skip', 'cancel', 'cancel_deps', 'clear_mods', 'autodetect_base',
           'refresh_remote', 'reindex', 'launch_game', 'open_mod_folder',
           'open_mods_folder', 'open_url', 'modcfg_to_profile', 'profile_to_modcfg'):
    MOCK[_m] = (lambda a: {'ok': True})
MOCK['new_profile'] = lambda a: {'ok': True, 'name': a[0] if a else 'new'}
MOCK['browse_game'] = lambda a: {'ok': True, 'path': 'D:\\Games\\Space Rangers HD'}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split('?')[0]
        fpath = WEB_DIR / ('index.html' if path in ('/', '/index.html') else path.lstrip('/'))
        if not fpath.exists():
            self.send_response(404); self.end_headers(); return
        content = fpath.read_bytes()
        ct = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
              '.css': 'text/css; charset=utf-8'}.get(fpath.suffix, 'application/octet-stream')
        self.send_response(200); self.send_header('Content-Type', ct)
        self.send_header('Content-Length', len(content)); self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b'[]'
        try:
            args = json.loads(body)
        except Exception:
            args = []
        method = self.path[5:] if self.path.startswith('/api/') else ''
        handler = MOCK.get(method)
        try:
            result = handler(args) if handler else {'ok': True, '_stub': method}
        except Exception as e:
            result = {'ok': False, 'error': str(e)}
        data = json.dumps(result, ensure_ascii=False).encode('utf-8')
        self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(data)); self.end_headers()
        self.wfile.write(data)


BRIDGE = r"""
window.pywebview = { api: new Proxy({}, { get: (_, m) => function() {
  const args = Array.from(arguments);
  return fetch('/api/' + m, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(args)}).then(r => r.json());
}})};
"""


def run():
    from playwright.sync_api import sync_playwright
    srv = HTTPServer(('127.0.0.1', 17788), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:17788'

    def init(page, state_override=None):
        if state_override is not None:
            MOCK['get_state'] = lambda a, s=state_override: s
        else:
            MOCK['get_state'] = lambda a: _state()
        page.add_init_script(BRIDGE)
        page.goto(url, wait_until='domcontentloaded')
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        try:
            page.wait_for_function("document.querySelector('#treeBody .row') !== null", timeout=4000)
        except Exception:
            pass
        time.sleep(0.4)

    def snap(page, name):
        p = OUT_DIR / name
        page.screenshot(path=str(p), full_page=False)
        print(f'  saved {p}')

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1280, 'height': 760},
                                  device_scale_factor=2)
        page = ctx.new_page()

        # 1. Главное окно (тёмная тема, база = авто)
        init(page)
        snap(page, '01-main.png')

        # 2. Первый запуск — «Шаг 1: папка игры» (папка не выбрана)
        init(page, _state(game_path=''))
        snap(page, '02-game-folder.png')

        # 3. Добавление мода
        init(page)
        page.locator('#addBtn').click(); time.sleep(0.5)
        snap(page, '03-add-mod.png')

        # 4. Карточка мода (ⓘ) с выбором варианта сборки
        init(page)
        page.locator("#treeBody .row.leaf .info-btn").first.click()
        time.sleep(0.6)
        snap(page, '04-mod-info.png')

        # 5. Выбор порядка сборок при проверке обновлений
        MOCK['get_update_order'] = lambda a: _update_order(True)
        init(page)
        page.locator('#refreshBtn').click(); time.sleep(0.6)
        snap(page, '05-update-order.png')
        MOCK['get_update_order'] = lambda a: _update_order(False)

        # 6. Профили — меню создания/управления
        init(page)
        page.locator('#profileMenuBtn').click(); time.sleep(0.4)
        snap(page, '06-profiles.png')

        # 7. Настройки
        init(page)
        page.locator('#settingsBtn').click(); time.sleep(0.5)
        snap(page, '07-settings.png')

        # 8. Обозначения (легенда состояний)
        init(page)
        page.locator('#legendBtn').click(); time.sleep(0.4)
        snap(page, '08-legend.png')

        # 9. Фильтр
        init(page)
        page.locator('#filterBtn').click(); time.sleep(0.3)
        snap(page, '09-filter.png')

        # 10. Светлая тема
        init(page, _state(theme='light'))
        page.evaluate("document.documentElement.dataset.theme = 'light'")
        time.sleep(0.3)
        snap(page, '10-light.png')

        browser.close()
    srv.shutdown()
    print('DONE')


if __name__ == '__main__':
    run()
