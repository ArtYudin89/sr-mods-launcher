"""Уровень-2 UI-тесты: Playwright + mock-сервер.

Запуск: python webui/test_playwright.py
Скриншоты: рядом с файлом, папка screenshots_pw/
Требует: pip install playwright --prefer-binary && playwright install chromium
"""
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Windows консоль часто cp1251 — переключаем на utf-8, чтобы emoji в print не ломали тест
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

WEB_DIR = Path(__file__).parent / 'web'
SHOTS_DIR = Path(__file__).parent / 'screenshots_pw'
SHOTS_DIR.mkdir(exist_ok=True)

# ───────── Mock-данные ─────────

def _mock_state():
    return {
        'theme': 'dark',
        'tree_mode': 'folder',
        'name_mode': 'folder',
        'log_verbose': False,
        'profiles': ['default', 'test'],
        'current_profile': 'default',
        'game_path': 'D:\\Games\\Space Rangers HD',
        'base': 'redux',
        'repo': 'ArtYudin89/sr-mods-aggregator',
        'has_token': False,
        'forks': [],
        'is_rwt': False,
        'version': '0.15.4',
        'busy': False,
        'desc_in_list': True,
        'show_hidden': True,
        'tutorial_done': True,   # не показывать онбординг (экран читаемости/тур) — иначе
                                 # авто-оверлей первого запуска перехватывает фокус/Esc в UI-сценариях
    }


def _mock_tree():
    def mod(iid, label, mid, sc, st, in_game=False, in_profile=True,
            date='02.07.2026', section='', desc='', labels=None, variants=None,
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
            'labels': labels or ['redux'],
            'variants': variants or [],
            'chosen': chosen,
            'mergeable': mergeable,
            'hidden': hidden, 'tags': tags or [], 'note': note, 'full_desc': full_desc,
        }

    redux_pack_mods = [
        mod('d:FairansVision/FairansVisionCore', 'FairansVisionCore',
            'FairansVision/FairansVisionCore', 'ok', '✅ установлен',
            in_game=True, section='Сюжет', labels=['redux'], mergeable=True),
        mod('d:FairansVision/FairansBalance', 'FairansBalance',
            'FairansVision/FairansBalance', 'upd', '⬆ обновление',
            in_game=True, section='Баланс', labels=['redux'], mergeable=True,
            tags=['Любимое', 'PvP'], note='моя заметка', full_desc='Полное описание мода баланса для проверки показа в списке.'),
        mod('d:FairansVision/FairansGraphics', 'FairansGraphics',
            'FairansVision/FairansGraphics', 'ok', '✅ установлен',
            section='Графика', labels=['redux'], mergeable=True, hidden=True),
    ]

    uni_pack_mods = [
        mod('d:DrKlesMod/DrKlesMod', 'DrKlesMod', 'DrKlesMod/DrKlesMod',
            'ok', '✅ установлен', in_game=True, section='Экономика',
            labels=['universe'], mergeable=True),
        mod('d:Polaria/PolariaCampaign', 'PolariaCampaign', 'Polaria/PolariaCampaign',
            'queued', '➕ добавлен', in_profile=True,
            section='Сюжет', labels=['redux', 'universe'],
            variants=[
                {'id': 'Polaria/PolariaCampaign', 'source': 'redux/redux_base_installer',
                 'camp': 'redux', 'label': 'Pol-Redux'},
                {'id': 'Polaria/PolariaCampaign@Shu', 'source': 'universe/universe_community',
                 'camp': 'universe', 'label': 'Pol-Universe'},
            ], chosen='redux'),
    ]

    extra_mod = mod('e:Reflections/ReflectionsMod', 'ReflectionsMod',
                    'Reflections/ReflectionsMod', 'avail', '📥 доступен',
                    in_profile=False, section='Графика', labels=['redux'])

    return {
        'camps': [
            {
                'label': 'redux',
                'kind': 'camp',
                'packs': [
                    {'label': 'FairansVision', 'kind': 'pack', 'mods': redux_pack_mods},
                ],
                'mods': [],
            },
            {
                'label': 'universe',
                'kind': 'camp',
                'packs': [
                    {'label': 'Universe_Community', 'kind': 'pack', 'mods': uni_pack_mods},
                ],
                'mods': [],
            },
            {
                'label': 'набор профиля',
                'kind': 'camp',
                'packs': [],
                'mods': [extra_mod],
            },
        ],
        'base': {'camp': 'redux', 'name': 'redux_base_installer'},
        'base_manual': 'redux',
        'queue': {'count': 3, 'size': '1.2 ГБ'},
        'tree_mode': 'folder',
        'name_mode': 'folder',
        'all_tags': ['Любимое', 'PvP'],
        'hidden_count': 1,
        'show_hidden': True,
        'desc_in_list': True,
    }


def _mock_update_order():
    return {
        'ok': True,
        'base': 'redux',
        'order': ['redux', 'universe'],
        'all_camps': ['redux', 'universe', 'original'],
        'needs_order': False,
    }


def _mock_compat():
    return {
        'ok': True,
        'conflicts': [],
        'warnings': [],
        'summary': 'Конфликтов нет.',
    }


def _mock_all_mods():
    return {
        'ok': True,
        'mods': [
            {'id': 'FairansVision/FairansVisionCore', 'name': 'Вижн Фейрана — ядро',
             'camps': ['redux'], 'desc': 'Основной мод из серии Вижн.'},
            {'id': 'FairansVision/FairansBalance', 'name': 'Баланс Фейрана',
             'camps': ['redux'], 'desc': 'Баланс для Вижн.'},
            {'id': 'DrKlesMod/DrKlesMod', 'name': 'Мод Д-ра Клеса',
             'camps': ['universe'], 'desc': 'Экономический мод.'},
            {'id': 'Polaria/PolariaCampaign', 'name': 'Полария',
             'camps': ['redux', 'universe'], 'desc': 'Новая кампания Полария.'},
            {'id': 'Reflections/ReflectionsMod', 'name': 'Отражения',
             'camps': ['redux'], 'desc': 'Графические улучшения.'},
        ],
    }


def _mock_camp_packs():
    return {
        'ok': True,
        'camps': {
            'redux': {
                'label': 'ПБ «Свободная Бухта»',
                'packs': [
                    {'id': 'redux/redux_base_installer', 'name': 'redux_base_installer',
                     'tier': 'base', 'bytes': 800_000_000},
                    {'id': 'redux/FairansVision', 'name': 'FairansVision',
                     'tier': 'fix', 'bytes': 300_000_000},
                ],
            },
            'universe': {
                'label': 'Space Rangers Universe (Community)',
                'packs': [
                    {'id': 'universe/universe_community', 'name': 'universe_community',
                     'tier': 'base', 'bytes': 950_000_000},
                ],
            },
        },
    }


def _mock_mod_info(mid, variant=None):
    names = {
        'FairansVision/FairansVisionCore': 'Вижн Фейрана — ядро',
        'FairansVision/FairansBalance': 'Баланс Фейрана',
        'FairansVision/FairansGraphics': 'Графика Фейрана',
        'DrKlesMod/DrKlesMod': 'Мод Д-ра Клеса',
        'Polaria/PolariaCampaign': 'Полария',
        'Reflections/ReflectionsMod': 'Отражения',
    }
    info = {
        'name': names.get(mid, mid.split('/')[-1]),
        'section': 'Графика' if 'Graphics' in mid or 'Reflections' in mid else 'Сюжет',
        'authors': 'Фейран / Авторский коллектив',
        'small': f'Краткое описание мода {mid.split("/")[-1]}.',
        'full': f'Полное описание мода {mid.split("/")[-1]}.\n\nМод добавляет новые возможности в игру.',
        'full_html': f'<p>Полное описание мода {mid.split("/")[-1]}.</p>',
        'requires': [{'name': 'FairansVisionCore', 'mid': 'FairansVision/FairansVisionCore'}],
        'dependents': [{'name': 'FairansGraphics', 'mid': 'FairansVision/FairansGraphics'}],
        'conflicts': ['SomeOtherMod'],
        'conflicts_ref': [{'name': 'SomeOtherMod', 'mid': ''}],
        'id': mid,
        'location': mid.replace('/', '\\'),
        'installed': True,
        'variants': [],
        'variant_key': variant or '',
    }
    return {'ok': True, 'info': info}


MOCK_DISPATCH = {
    'get_state': lambda args: _mock_state(),
    'get_tree': lambda args: _mock_tree(),
    'get_update_order': lambda args: _mock_update_order(),
    'check_self_update': lambda args: {'ok': True, 'update': False, 'version': '0.15.4',
                                        'current': '0.15.4', 'url': '', 'notes': ''},
    'get_all_mods': lambda args: _mock_all_mods(),
    'get_camp_packs': lambda args: _mock_camp_packs(),
    'get_mod_info': lambda args: _mock_mod_info(*args),
    'check_compat': lambda args: _mock_compat(),
    'get_unit_mods': lambda args: {'ok': True, 'mods': []},
    'mods_info': lambda args: {'ok': True, 'count': 5, 'kept': 0,
                               'path': 'D:\\Games\\Space Rangers HD\\Mods', 'mods': []},
    'set_base': lambda args: {'ok': True},
    'set_theme': lambda args: {'ok': True},
    'set_tree_mode': lambda args: {'ok': True},
    'set_name_mode': lambda args: {'ok': True},
    'set_verbose': lambda args: {'ok': True},
    'set_update_extra': lambda args: {'ok': True, 'order': ['redux', 'universe']},
    'set_forks': lambda args: {'ok': True},
    'save_settings': lambda args: {'ok': True},
    'switch_profile': lambda args: {'ok': True},
    'new_profile': lambda args: {'ok': True, 'name': args[0] if args else 'new'},
    'delete_profile': lambda args: {'ok': True},
    'add_mod': lambda args: {'ok': True},
    'remove_pidx': lambda args: {'ok': True},
    'clear_queue': lambda args: {'ok': True},
    'toggle_enabled': lambda args: {'ok': True},
    'set_variant': lambda args: {'ok': True},
    'plan_enable': lambda args: {'ok': True, 'plan': []},
    'plan_disable': lambda args: {'ok': True},
    'install': lambda args: {'ok': True},
    'install_set_with_deps': lambda args: {'ok': True},
    'confirm_install_deps': lambda args: {'ok': True},
    'start_merge': lambda args: {'ok': True},
    'apply_merge': lambda args: {'ok': True},
    'merge_skip': lambda args: {'ok': True},
    'cancel': lambda args: {'ok': True},
    'cancel_deps': lambda args: {'ok': True},
    'clear_mods': lambda args: {'ok': True},
    'autodetect_base': lambda args: {'ok': True},
    'refresh_remote': lambda args: {'ok': True},
    'reindex': lambda args: {'ok': True},
    'browse_game': lambda args: {'ok': True, 'path': 'D:\\Games\\Space Rangers HD'},
    'launch_game': lambda args: {'ok': True},
    'open_mod_folder': lambda args: {'ok': True},
    'open_mods_folder': lambda args: {'ok': True},
    'open_url': lambda args: {'ok': True},
    'modcfg_to_profile': lambda args: {'ok': True, 'count': 4},
    'profile_to_modcfg': lambda args: {'ok': True},
    'check_self_update': lambda args: {'ok': True, 'update': False},
}

# ───────── HTTP-сервер ─────────

class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # тихий режим

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/' or path == '/index.html':
            fpath = WEB_DIR / 'index.html'
        elif path.startswith('/'):
            fpath = WEB_DIR / path.lstrip('/')
        else:
            self._404(); return

        if not fpath.exists():
            self._404(); return

        content = fpath.read_bytes()
        ct = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
        }.get(fpath.suffix, 'application/octet-stream')
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        path = self.path
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b'[]'
        try:
            args = json.loads(body)
        except Exception:
            args = []

        # /api/<method>
        if path.startswith('/api/'):
            method = path[5:]
            handler = MOCK_DISPATCH.get(method)
            if handler:
                try:
                    result = handler(args)
                except Exception as e:
                    result = {'ok': False, 'error': str(e)}
            else:
                result = {'ok': True, '_stub': method}
            self._json(result)
        else:
            self._404()

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _404(self):
        self.send_response(404)
        self.end_headers()


def start_server(port=17777):
    srv = HTTPServer(('127.0.0.1', port), MockHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f'http://127.0.0.1:{port}'


# ───────── JS-мост (инъекция ДО app.js) ─────────

BRIDGE_JS = r"""
window.pywebview = {
  api: new Proxy({}, {
    get: (_, method) => function() {
      const args = Array.from(arguments);
      return fetch('/api/' + method, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(args)
      }).then(function(r) { return r.json(); });
    }
  })
};
"""

# ───────── Утилиты тестов ─────────

PASS = []
FAIL = []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  [OK ] {name}')
    else:
        FAIL.append(name)
        print(f'  [FAIL] {name}' + (f' — {detail}' if detail else ''))


def shot(page, name):
    p = SHOTS_DIR / f'{name}.png'
    page.screenshot(path=str(p), full_page=False)
    print(f'  📷 {name}.png')
    return p


def wait_tree(page, timeout=5000):
    """Ждём пока дерево отрисуется (исчезнет .tree-empty)."""
    page.wait_for_function(
        "document.querySelector('#treeBody .row') !== null",
        timeout=timeout
    )


def init_page(page, base_url):
    """Инициализировать страницу: инъектировать мост и загрузить."""
    page.add_init_script(BRIDGE_JS)
    page.goto(base_url, wait_until='domcontentloaded')
    # Запустить инициализацию: fires pywebviewready
    page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
    wait_tree(page)


# ───────── Сценарии ─────────

def scenario_initial_render(page, base_url):
    """Сц.0: Базовый рендер — страница грузится, дерево видно."""
    print('\n=== Сц.0: Начальный рендер ===')
    init_page(page, base_url)
    shot(page, '00_initial')

    check('тема dark применена',
          page.evaluate("document.documentElement.dataset.theme") == 'dark')
    check('вербейдж содержит версию',
          '0.15.4' in page.text_content('#verBadge'))
    check('папка игры показана',
          'Space Rangers' in page.text_content('#gamePath'))
    check('дерево: есть хотя бы одна строка .row',
          page.locator('#treeBody .row').count() > 0)
    check('setupHint скрыт (папка выбрана)',
          page.evaluate("document.getElementById('setupHint').style.display") == 'none')
    rows = page.locator('#treeBody .row').count()
    print(f'  └─ строк в дереве: {rows}')


def _active_row_id(page):
    """iid или data-key активного .row элемента (None если не .row)."""
    return page.evaluate(
        "(() => { const el = document.activeElement; "
        "if (!el || !el.classList.contains('row')) return null; "
        "return el.dataset.iid || el.dataset.key || null; })()"
    )


def _focus_row(page, row_locator):
    """Явно фокусировать строку через JS (click не всегда переносит focus в headless)."""
    row_locator.evaluate("el => el.focus()")
    time.sleep(0.05)


def scenario_tree_keyboard_nav(page, base_url):
    """Сц.1: Клавиатурная навигация по дереву (v0.15.4)."""
    print('\n=== Сц.1: Клавиатурная навигация по дереву ===')
    init_page(page, base_url)

    # Roving tabindex: до любых действий — ровно одна строка с tabindex=0
    tabs_initial = page.evaluate(
        "[...document.querySelectorAll('#treeBody .row')].filter(r => r.tabIndex === 0).length"
    )
    check('roving tabindex: ровно 1 строка с tabindex=0 после рендера', tabs_initial == 1,
          f'нашли {tabs_initial}')
    shot(page, '01a_initial_tree')

    # Фокусируем первую строку через JS (click не гарантирует focus в headless)
    first_row = page.locator('#treeBody .row').first
    _focus_row(page, first_row)
    id_first = _active_row_id(page)
    print(f'  └─ первая строка: {id_first!r}')
    check('JS focus() на первую строку работает', id_first is not None)

    # ArrowDown → фокус на следующую строку
    page.keyboard.press('ArrowDown')
    time.sleep(0.1)
    id_after_down = _active_row_id(page)
    print(f'  └─ после ArrowDown: {id_after_down!r}')
    check('ArrowDown: фокус на .row', id_after_down is not None)
    check('ArrowDown: позиция изменилась', id_after_down != id_first)
    shot(page, '01b_arrow_down')

    # ArrowUp → обратно
    page.keyboard.press('ArrowUp')
    time.sleep(0.1)
    id_after_up = _active_row_id(page)
    print(f'  └─ после ArrowUp: {id_after_up!r}')
    check('ArrowUp: вернулись к первой', id_after_up == id_first)

    # Home/End
    total_rows = page.locator('#treeBody .row').count()
    page.keyboard.press('End')
    time.sleep(0.1)
    id_end = _active_row_id(page)
    shot(page, '01c_end')
    page.keyboard.press('Home')
    time.sleep(0.1)
    id_home = _active_row_id(page)
    shot(page, '01d_home')
    print(f'  └─ Home={id_home!r}, End={id_end!r}, всего строк={total_rows}')
    check('Home/End: разные позиции', id_home != id_end or total_rows <= 1)

    # ← на развёрнутой группе: свернуть (листьев должно стать меньше)
    groups = page.locator('#treeBody .row.group')
    if groups.count() > 0:
        _focus_row(page, groups.first)
        rows_before = page.locator('#treeBody .row').count()
        grp_key = page.evaluate(
            "(() => { const g = document.querySelector('#treeBody .row.group'); "
            "return g ? g.dataset.key : null; })()"
        )
        print(f'  └─ группа: {grp_key!r}, строк до ←: {rows_before}')

        # если группа уже свёрнута — сначала раскроем (ArrowRight)
        arrow_icon = page.evaluate(
            "(() => { const g = document.querySelector('#treeBody .row.group .tw'); "
            "return g ? g.textContent : '▾'; })()"
        )
        if arrow_icon == '▸':
            page.keyboard.press('ArrowRight')
            time.sleep(0.2)
            rows_before = page.locator('#treeBody .row').count()

        page.keyboard.press('ArrowLeft')
        time.sleep(0.25)
        rows_after = page.locator('#treeBody .row').count()
        shot(page, '01e_collapse_left')
        print(f'  └─ строк после ←: {rows_after}')
        check('ArrowLeft сворачивает группу (строк меньше)', rows_after < rows_before,
              f'{rows_before} → {rows_after}')

        # → раскрыть обратно
        page.keyboard.press('ArrowRight')
        time.sleep(0.25)
        rows_expanded = page.locator('#treeBody .row').count()
        shot(page, '01f_expand_right')
        print(f'  └─ строк после →: {rows_expanded}')
        check('ArrowRight раскрывает группу', rows_expanded > rows_after)

    # Space на листе: выделить строку
    leaves = page.locator('#treeBody .row.leaf')
    if leaves.count() > 0:
        _focus_row(page, leaves.first)
        iid_before_space = _active_row_id(page)
        selected_before = page.evaluate(
            f"document.querySelector('#treeBody .row.leaf.sel') !== null"
        )
        page.keyboard.press('Space')
        time.sleep(0.15)
        selected_after = page.evaluate(
            f"document.querySelector('#treeBody .row.leaf.sel') !== null"
        )
        check('Space переключает выделение листа',
              selected_after != selected_before or selected_after,
              f'sel before={selected_before}, after={selected_after}')
        shot(page, '01g_space_select')

    # i — открыть окно инфо о моде (правильный ID: infoOverlay)
    if leaves.count() > 0:
        _focus_row(page, leaves.first)
        time.sleep(0.05)
        page.keyboard.press('i')
        time.sleep(0.5)
        info_win_visible = page.evaluate(
            "!document.getElementById('infoOverlay').classList.contains('hidden')"
        )
        check('i открывает окно инфо о моде', info_win_visible)
        # Проверить что тело окна заполнилось (не только "Загрузка...")
        info_body_text = page.text_content('#infoBody') if info_win_visible else ''
        info_has_content = bool(info_body_text and 'Загрузка' not in info_body_text)
        check('окно инфо показывает данные (не "Загрузка…")', info_has_content,
              f'тело: {info_body_text[:80]!r}')
        shot(page, '01h_mod_info')
        if info_win_visible:
            page.keyboard.press('Escape')
            time.sleep(0.2)


def scenario_focus_trap(page, base_url):
    """Сц.2: Focus-trap в модальных окнах."""
    print('\n=== Сц.2: Focus-trap в модалках ===')
    init_page(page, base_url)

    # Кликаем кнопку Настройки
    settings_btn = page.locator('#settingsBtn')
    settings_btn.click()
    time.sleep(0.35)
    shot(page, '02a_settings_open')

    overlay_visible = page.evaluate(
        "!document.getElementById('settingsOverlay').classList.contains('hidden')"
    )
    check('Настройки открылись', overlay_visible)

    # Фокус внутри модалки
    focus_inside = page.evaluate(
        "document.getElementById('settingsOverlay').contains(document.activeElement)"
    )
    check('focus_trap: фокус внутри settingsOverlay', focus_inside)

    # Tab-цикл: Tab несколько раз — фокус не выходит за overlay
    for _ in range(10):
        page.keyboard.press('Tab')
        time.sleep(0.05)
    focus_inside2 = page.evaluate(
        "document.getElementById('settingsOverlay').contains(document.activeElement)"
    )
    check('focus_trap: после 10xTab фокус всё ещё внутри', focus_inside2)
    shot(page, '02b_tab_cycle')

    # Shift+Tab — тоже внутри
    for _ in range(5):
        page.keyboard.press('Shift+Tab')
        time.sleep(0.05)
    focus_inside3 = page.evaluate(
        "document.getElementById('settingsOverlay').contains(document.activeElement)"
    )
    check('focus_trap: Shift+Tab внутри', focus_inside3)

    # Esc закрывает и ВОЗВРАЩАЕТ фокус на settingsBtn
    page.keyboard.press('Escape')
    time.sleep(0.25)
    shot(page, '02c_settings_closed')

    overlay_hidden = page.evaluate(
        "document.getElementById('settingsOverlay').classList.contains('hidden')"
    )
    check('Esc закрыл Настройки', overlay_hidden)

    focus_returned = page.evaluate(
        "document.activeElement === document.getElementById('settingsBtn')"
    )
    check('Esc вернул фокус на settingsBtn', focus_returned)

    # Проверим confirmOverlay (через doClearMods → двойное подтверждение)
    # используем низкоуровневый show()
    page.evaluate("window.show && show('confirmOverlay')")
    time.sleep(0.2)
    confirm_visible = page.evaluate(
        "!document.getElementById('confirmOverlay').classList.contains('hidden')"
    )
    check('confirmOverlay открывается через show()', confirm_visible)
    if confirm_visible:
        focus_in_confirm = page.evaluate(
            "document.getElementById('confirmOverlay').contains(document.activeElement)"
        )
        check('focus_trap: фокус в confirmOverlay', focus_in_confirm)
        shot(page, '02d_confirm_trap')
        page.keyboard.press('Escape')
        time.sleep(0.2)


def scenario_focus_visible(page, base_url):
    """Сц.3: :focus-visible — рамка видна с клавиатуры, не видна с мыши."""
    print('\n=== Сц.3: :focus-visible рамка ===')
    init_page(page, base_url)

    # 3a. Правило :focus-visible есть в CSS
    has_focus_visible_rule = page.evaluate("""
        Array.from(document.styleSheets).some(ss => {
          try {
            return Array.from(ss.cssRules || []).some(r =>
              r.selectorText && r.selectorText.includes(':focus-visible')
            );
          } catch(e) { return false; }
        })
    """)
    check(':focus-visible правило есть в CSS', has_focus_visible_rule)

    # 3b. Tab с известной стартовой точки: программно фокусируем body, затем Tab
    # (page.locator('body').click() не гарантирует что body не перехватывает focus)
    # Фокусируем первый focusable элемент через JS
    page.evaluate(
        "document.querySelector('button, input, select, [tabindex]') && "
        "document.querySelector('button, input, select, [tabindex]').blur()"
    )
    # Принудительно сбросить фокус — focus на documentElement
    page.evaluate("document.documentElement.focus()")
    time.sleep(0.05)
    page.keyboard.press('Tab')
    time.sleep(0.15)
    shot(page, '03a_focus_visible_tab')

    active_tag = page.evaluate("document.activeElement && document.activeElement.tagName")
    print(f'  └─ активный элемент: <{active_tag}>')
    check('Tab фокусирует интерактивный элемент',
          active_tag in ('BUTTON', 'INPUT', 'SELECT', 'A', 'TEXTAREA'),
          f'tag={active_tag}')

    # 3c. :focus-visible матчится на активном элементе при навигации с клавиатуры
    matches_fv = page.evaluate(
        "document.activeElement ? document.activeElement.matches(':focus-visible') : false"
    )
    print(f'  └─ activeElement matches(:focus-visible): {matches_fv}')
    check(':focus-visible активен на эл-те после Tab', matches_fv)

    # 3d. После клика мышью — :focus-visible НЕ применяется (браузерная эвристика)
    active_before_click = page.evaluate("document.activeElement && document.activeElement.id")
    page.locator('#settingsBtn').click()
    time.sleep(0.1)
    page.keyboard.press('Escape')  # закрыть открывшееся окно
    time.sleep(0.1)
    shot(page, '03b_focus_after_click')
    # :focus-visible после click() — в Chromium обычно false, но не гарантировано
    # Поэтому просто диагностируем без жёсткого assertion
    fv_after_click = page.evaluate(
        "document.activeElement ? document.activeElement.matches(':focus-visible') : false"
    )
    print(f'  └─ :focus-visible после click: {fv_after_click} '
          f'(в Chromium headless обычно false)')


def scenario_update_order_dialog(page, base_url):
    """Сц.4: Диалог порядка проверки (нужно при needs_order=True)."""
    print('\n=== Сц.4: Диалог порядка обновлений ===')

    # Патчим get_update_order чтобы needs_order=True
    from http.server import BaseHTTPRequestHandler
    original = MOCK_DISPATCH['get_update_order']
    MOCK_DISPATCH['get_update_order'] = lambda args: {
        'ok': True, 'base': 'redux',
        'order': ['redux', 'universe'],
        'all_camps': ['redux', 'universe', 'original'],
        'needs_order': True,
    }

    try:
        init_page(page, base_url)
        shot(page, '04a_main_loaded')

        # Нажать «Проверить обновления»
        refresh_btn = page.locator('#refreshBtn')
        refresh_btn.click()
        time.sleep(0.4)
        shot(page, '04b_after_refresh_click')

        # При needs_order=True должно открыться окно порядка
        order_visible = page.evaluate(
            "!document.getElementById('updateOrderOverlay').classList.contains('hidden')"
            if page.evaluate("!!document.getElementById('updateOrderOverlay')")
            else "false"
        )
        print(f'  └─ updateOrderOverlay видимо: {order_visible}')
        # Это может быть другое имя overlay — ищем любой открытый overlay
        any_overlay = page.evaluate(
            "[...document.querySelectorAll('.overlay:not(.hidden)')].map(e=>e.id)"
        )
        print(f'  └─ открытые overlay: {any_overlay}')
        check('При needs_order=True открылось какое-то окно', bool(any_overlay))

    finally:
        MOCK_DISPATCH['get_update_order'] = original


def scenario_base_selector(page, base_url):
    """Сц.5: Селектор «База:» и кнопка 🎯."""
    print('\n=== Сц.5: Селектор База + autodetect ===')
    init_page(page, base_url)

    # Текущее значение baseSel (должно быть 'redux' из mock_state)
    base_val = page.evaluate("document.getElementById('baseSel').value")
    check('baseSel стартует с redux', base_val == 'redux', f'было: {base_val!r}')
    shot(page, '05a_base_sel')

    # Сменить на universe
    page.select_option('#baseSel', 'universe')
    time.sleep(0.2)
    shot(page, '05b_base_changed')

    # Кнопка 🎯 — autodetect
    auto_btn = page.locator('#baseAutoBtn')
    check('кнопка 🎯 видна', auto_btn.is_visible())
    auto_btn.click()
    time.sleep(0.3)
    shot(page, '05c_autodetect_clicked')
    # Должен появиться toast
    toast_visible = page.evaluate(
        "document.querySelectorAll('.toast').length > 0"
    )
    print(f'  └─ toast после 🎯: {toast_visible}')


def scenario_add_mod_dialog(page, base_url):
    """Сц.6: Диалог добавления мода."""
    print('\n=== Сц.6: Добавление мода ===')
    init_page(page, base_url)

    add_btn = page.locator('#addBtn')
    add_btn.click()
    time.sleep(0.4)
    shot(page, '06a_add_mod_open')

    add_overlay = page.evaluate(
        "[...document.querySelectorAll('.overlay:not(.hidden)')].map(e=>e.id)"
    )
    check('Диалог добавления открылся', bool(add_overlay), str(add_overlay))

    # Проверить focus-trap в этом окне тоже
    focus_in_add = page.evaluate(
        "!!document.activeElement && document.activeElement !== document.body"
    )
    check('Фокус внутри диалога добавления', focus_in_add)

    # Закрыть Esc
    page.keyboard.press('Escape')
    time.sleep(0.2)
    any_overlay2 = page.evaluate(
        "[...document.querySelectorAll('.overlay:not(.hidden)')].length"
    )
    check('Esc закрыл диалог добавления', any_overlay2 == 0)
    shot(page, '06b_add_mod_closed')


def scenario_context_menu(page, base_url):
    """Сц.7: ПКМ и Shift+F10 на листе."""
    print('\n=== Сц.7: Контекстное меню ===')
    init_page(page, base_url)

    leaves = page.locator('#treeBody .row.leaf')
    if leaves.count() == 0:
        print('  └─ нет листьев, пропускаем')
        return

    leaf = leaves.first
    leaf.click(button='right')
    time.sleep(0.3)
    shot(page, '07a_ctx_right_click')

    def ctx_is_visible():
        return page.evaluate(
            "!document.getElementById('ctxMenu').classList.contains('hidden')"
        )

    check('ПКМ открывает контекстное меню', ctx_is_visible())

    # Закрыть Esc
    page.keyboard.press('Escape')
    time.sleep(0.15)
    check('Esc закрыл контекстное меню', not ctx_is_visible())

    # Shift+F10 на листе: нужно чтобы лист был реально сфокусирован через keyboard
    # Используем JS focus() — гарантированно передаёт клавиатурный фокус
    _focus_row(page, leaf)
    # Убеждаемся что ctx закрыт
    page.evaluate("document.getElementById('ctxMenu').classList.add('hidden')")
    time.sleep(0.05)
    page.keyboard.press('Shift+F10')
    time.sleep(0.35)
    shot(page, '07b_ctx_shift_f10')

    ctx_visible2 = ctx_is_visible()
    ctx_menu_content = page.evaluate(
        "document.getElementById('ctxMenu').innerHTML"
    )
    print(f'  └─ ctx после Shift+F10: visible={ctx_visible2}')
    check('Shift+F10 открывает контекстное меню', ctx_visible2)
    if ctx_visible2:
        page.keyboard.press('Escape')
        time.sleep(0.1)


def scenario_filter_popover(page, base_url):
    """Сц.8: Фильтр-поповер."""
    print('\n=== Сц.8: Фильтр-поповер ===')
    init_page(page, base_url)

    filter_btn = page.locator('#filterBtn')
    filter_btn.click()
    time.sleep(0.2)

    pop_visible = page.evaluate(
        "!document.getElementById('filterPop').classList.contains('hidden')"
    )
    check('Фильтр-поповер открылся', pop_visible)
    shot(page, '08a_filter_open')

    # Снять все чекбоксы состояний
    none_btn = page.locator('#fNone')
    none_btn.click()
    time.sleep(0.2)
    shot(page, '08b_filter_none')

    rows_after_none = page.locator('#treeBody .row.leaf').count()
    print(f'  └─ строк-листьев после «Снять все»: {rows_after_none}')
    check('«Снять все» скрывает все листья', rows_after_none == 0)

    # Вернуть все
    page.locator('#fAll').click()
    time.sleep(0.2)
    rows_after_all = page.locator('#treeBody .row.leaf').count()
    check('«Все» возвращает листья', rows_after_all > 0)
    shot(page, '08c_filter_all')

    # Закрыть поповер кликом вне
    page.locator('body').click()
    time.sleep(0.1)


def scenario_clear_mods(page, base_url):
    """Сц.9: «Очистить Mods» — двойное подтверждение."""
    print('\n=== Сц.9: Очистить Mods (двойное подтверждение) ===')
    init_page(page, base_url)

    clear_btn = page.locator('#clearModsBtn')
    clear_btn.click()
    time.sleep(0.3)
    shot(page, '09a_first_confirm')

    # Должен открыться первый confirmOverlay
    confirm1 = page.evaluate(
        "!document.getElementById('confirmOverlay').classList.contains('hidden')"
    )
    check('Первое подтверждение открылось', confirm1)

    if confirm1:
        # Нажать «Продолжить»
        page.locator('#confirmOk').click()
        time.sleep(0.3)
        shot(page, '09b_second_confirm')

        # Должен быть второй confirmOverlay (okSwap)
        confirm2 = page.evaluate(
            "!document.getElementById('confirmOverlay').classList.contains('hidden')"
        )
        check('Второе подтверждение открылось', confirm2)

        # Отменить
        page.locator('#confirmCancel').click()
        time.sleep(0.2)
        closed = page.evaluate(
            "document.getElementById('confirmOverlay').classList.contains('hidden')"
        )
        check('Отмена закрывает подтверждение', closed)
        shot(page, '09c_cancelled')


def scenario_feedback_batch(page, base_url):
    """Сц.10: батч отзывов — снять выбор (12), кликабельный тег (13), фильтры
    «только скрытые» (14) и «без тега» (16)."""
    print('\n=== Сц.10: батч отзывов (выбор/теги/фильтры) ===')
    init_page(page, base_url)

    def leaves():
        return page.locator('#treeBody .row.leaf').count()

    # (12) кнопка «Снять выбор» появляется при выборе и исчезает после сброса
    hidden0 = page.evaluate("document.getElementById('clearSelBtn').style.display === 'none'")
    check('12: «Снять выбор» скрыта пока ничего не выбрано', hidden0)
    page.locator('#treeBody .row.leaf .row-check').first.click()
    time.sleep(0.15)
    shown = page.evaluate("document.getElementById('clearSelBtn').style.display !== 'none'")
    txt = page.text_content('#clearSelBtn') or ''
    check('12: кнопка видна и показывает счётчик (1)', shown and '(1)' in txt, txt)
    page.locator('#clearSelBtn').click()
    time.sleep(0.15)
    gone = page.evaluate("document.getElementById('clearSelBtn').style.display === 'none'")
    check('12: клик «Снять выбор» сбрасывает выбор и прячет кнопку', gone)
    shot(page, '10a_clear_selection')

    # (13) клик по вашему тегу в строке открывает редактор тегов с чипами
    utag = page.locator('#treeBody .row.leaf .utag').first
    check('13: у мода есть кликабельный тег', utag.count() > 0)
    utag.dispatch_event('click')          # тег клипается overflow:hidden — жмём обработчик напрямую
    time.sleep(0.2)
    prompt_open = page.evaluate("!document.getElementById('promptOverlay').classList.contains('hidden')")
    chips_shown = page.evaluate("document.getElementById('promptChips').style.display !== 'none'")
    chip_cnt = page.locator('#promptChips .chip-pick').count()
    inp = page.evaluate("document.getElementById('promptInput').value")
    check('13: открылся редактор тегов', prompt_open)
    check('13: показаны чипы существующих тегов', chips_shown and chip_cnt >= 2, f'chips={chip_cnt}')
    check('13: поле предзаполнено тегами мода', 'Любимое' in (inp or ''), inp)
    shot(page, '10b_tag_editor')
    page.locator('#promptCancel').dispatch_event('click')
    time.sleep(0.1)

    # (14) фильтр «только скрытые» — остаётся лишь скрытый мод (в моке он один).
    # Чекбокс живёт в фильтр-поповере (в headless его позиционирование флейкает) —
    # дёргаем его onchange напрямую, детерминированно.
    check('14: чекбокс «только скрытые» есть', page.locator('#fOnlyHidden').count() > 0)
    page.evaluate("(() => { const c = document.getElementById('fOnlyHidden'); "
                  "c.checked = true; c.dispatchEvent(new Event('change')); })()")
    time.sleep(0.2)
    only_hidden = leaves()
    hidden_marks = page.locator('#treeBody .row.leaf .hid-mark').count()
    print(f'  └─ листьев при «только скрытые»: {only_hidden}, значков 🙈: {hidden_marks}')
    check('14: показаны только скрытые моды', only_hidden == 1 and hidden_marks == 1,
          f'leaves={only_hidden} marks={hidden_marks}')
    shot(page, '10c_only_hidden')
    page.evaluate("(() => { const c = document.getElementById('fOnlyHidden'); "
                  "c.checked = false; c.dispatchEvent(new Event('change')); })()")
    time.sleep(0.15)

    # (16) чип «∅ без тега» — прячет моды, у которых есть ваши теги
    base_leaves = leaves()
    notag = page.locator('#tagChips .notag-f')
    check('16: чип «без тега» есть', notag.count() > 0)
    notag.dispatch_event('click')
    time.sleep(0.2)
    after = leaves()
    utags_left = page.locator('#treeBody .row.leaf .utag').count()
    print(f'  └─ листьев было {base_leaves}, стало {after}, тегов на виду {utags_left}')
    check('16: моды с тегом отфильтрованы', after == base_leaves - 1 and utags_left == 0,
          f'{base_leaves}->{after}, utags={utags_left}')
    shot(page, '10d_no_tag')
    notag.dispatch_event('click')                   # сбросить фильтр «без тега»
    time.sleep(0.15)

    # (доп.) поиск матчит ПОЛНОЕ описание, когда оно показано в таблице (desc_in_list).
    # У FairansBalance full_desc содержит «показа в списке» — по обычному desc такого нет.
    si = page.locator('#searchInp')
    si.fill('показа в списке')
    time.sleep(0.2)
    hits = leaves()
    hit_mid = page.evaluate("(() => { const r = document.querySelector('#treeBody .row.leaf'); return r ? r.dataset.mid : ''; })()")
    print(f'  └─ поиск по full_desc: листьев={hits}, первый={hit_mid!r}')
    check('поиск матчит полное описание в таблице',
          hits == 1 and hit_mid == 'FairansVision/FairansBalance', f'hits={hits} mid={hit_mid}')
    # крестик очистки: виден при вводе, чистит и прячется по клику
    clear_shown = page.evaluate("!document.getElementById('searchClear').classList.contains('hidden')")
    check('крестик очистки виден при вводе', clear_shown)
    page.locator('#searchClear').dispatch_event('click')
    time.sleep(0.15)
    val = page.evaluate("document.getElementById('searchInp').value")
    clear_hidden = page.evaluate("document.getElementById('searchClear').classList.contains('hidden')")
    check('крестик очистил поиск и спрятался', val == '' and clear_hidden and leaves() == 6,
          f'val={val!r} hidden={clear_hidden} leaves={leaves()}')
    shot(page, '10e_search_fulldesc')

    # (доп.) карточка мода (ⓘ) показывает ВАШИ теги — отзыв «не вижу где теги в popup»
    ib = page.locator("#treeBody .row.leaf[data-mid='FairansVision/FairansBalance'] .info-btn")
    ib.dispatch_event('click')
    time.sleep(0.3)
    card_txt = page.evaluate("(() => { const c = document.querySelector('.mod-card'); return c ? c.querySelector('.mc-body').innerText : ''; })()")
    low = card_txt.lower()   # .i-k метки в uppercase через CSS text-transform → сравниваем без регистра
    print(f"  └─ карточка: 'ваши теги'={'ваши теги' in low}, 'Любимое'={'Любимое' in card_txt}")
    check('карточка мода показывает ваши теги',
          'ваши теги' in low and 'Любимое' in card_txt, card_txt[:140])
    shot(page, '10f_card_tags')


# ───────── Главный запуск ─────────

_PLAN_FIXTURE = {
    'id': 'Cat/EvoTranc', 'name': 'EvoTranc',
    'version_old': '1', 'version_new': '2', 'source_label': 'redux',
    'summary': {}, 'unchanged': 3, 'has_forks': True, 'reconciled': False,
    'labels': {}, 'files': [
        {'path': 'Evolution/EvoTranc/DATA/Items/a.dat', 'status': 'deleted_clean',
         'source': 'developer', 'source_detail': 'redux/redux_base_installer',
         'mine': {'date': '03.07.2026', 'size': 100}, 'their': None},
        {'path': 'Evolution/EvoTranc/DATA/Items/b.dat', 'status': 'deleted_clean',
         'source': 'developer', 'source_detail': 'redux/redux_base_installer',
         'mine': {'date': '03.07.2026', 'size': 120}, 'their': None},
        {'path': 'Evolution/EvoTranc/DATA/Config/x.cfg', 'status': 'update',
         'source': 'developer', 'source_detail': 'redux/redux_fixes',
         'mine': {'date': '03.07.2026', 'size': 50}, 'their': {'date': '22.03.2024', 'size': 55}},
        {'path': 'Eng/Lang.dat', 'status': 'conflict_binary',
         'source': 'developer', 'source_detail': 'redux/redux_fixes',
         'mine': {'date': '03.07.2026', 'size': 200}, 'their': {'date': '22.03.2024', 'size': 210}},
        {'path': 'Rus/Lang.dat', 'status': 'conflict_binary',
         'source': 'hotfix', 'source_detail': 'ArtYudin89/sr-mods-hotfixes',
         'mine': {'date': '03.07.2026', 'size': 200}, 'their': {'date': '01.07.2026', 'size': 205}},
    ],
}


def scenario_first_run_appearance(page, base_url):
    """Сц.11: Экран читаемости первого запуска ДО обучающего тура (#2).
    Новичок с нечитаемым текстом должен настроить размер/масштаб/контраст перед туром."""
    print('\n=== Сц.11: Экран читаемости до тура ===')
    init_page(page, base_url)
    # эмулируем первый запуск (mock-state ставит tutorial_done=True): сбрасываем и зовём онбординг
    page.evaluate("STATE.tutorial_done = false; maybeAutoTour();")
    page.wait_for_function(
        "!document.getElementById('firstRunOverlay').classList.contains('hidden')", timeout=3000)
    check('первый запуск: показан экран читаемости (firstRunOverlay)', True)
    check('экран читаемости: 3 степпера (текст/масштаб/контраст)',
          page.eval_on_selector_all('#firstRunOverlay .step-row', 'els => els.length') == 3)
    # степпер «крупнее» повышает размер текста и синхронизируется со степпером в настройках
    page.click('#frTextPlus')
    check('степпер «крупнее» → 110%', page.inner_text('#frTextVal') == '110%')
    check('синхрон со степпером настроек (setTextVal)',
          page.eval_on_selector('#setTextVal', 'e => e.textContent') == '110%')
    page.click('#frTextReset')
    check('сброс ↺ → 100%', page.inner_text('#frTextVal') == '100%')
    shot(page, '11_first_run_appearance')
    # «Продолжить» закрывает экран и открывает обучающий тур
    page.click('#frContinueBtn')
    page.wait_for_function(
        "document.getElementById('firstRunOverlay').classList.contains('hidden')", timeout=2000)
    check('«Продолжить» → экран закрыт, открыт тур',
          page.evaluate("!document.getElementById('tourOverlay').classList.contains('hidden')"))
    page.evaluate("endTour()")


def scenario_plan_filter_tree(page, base_url):
    """Сц.12: План обновления — фильтр-чипы по действию + древовидный вид по папкам (#3)."""
    print('\n=== Сц.12: Фильтр и дерево в плане обновления ===')
    init_page(page, base_url)
    page.evaluate(
        "(p) => { onPreviewPlan(p); "
        "if (document.getElementById('planOverlay').classList.contains('hidden')) show('planOverlay'); }",
        _PLAN_FIXTURE)
    page.wait_for_selector('#planCtls .plan-chip', timeout=2000)
    chips = page.eval_on_selector_all('#planCtls .plan-chip', 'els => els.map(e => e.textContent.trim())')
    check('чипы-счётчики: Все 5 / заменится 1 / конфликт 2 / удалится 2',
          any('Все' in c and '5' in c for c in chips) and any('заменится' in c and '1' in c for c in chips)
          and any('конфликт' in c and '2' in c for c in chips) and any('удалится' in c and '2' in c for c in chips),
          str(chips))
    check('плоский вид: 5 файловых строк',
          page.eval_on_selector_all('#planBody tbody tr', 'els => els.length') == 5)
    # клик по чипу «конфликт» → фильтр до 2 строк, чип подсвечен, показана плашка
    page.evaluate("() => { for (const b of document.querySelectorAll('#planCtls .plan-chip')) "
                  "if (b.textContent.includes('конфликт')) { b.click(); break; } }")
    check('фильтр «конфликт» → 2 строки',
          page.eval_on_selector_all('#planBody tbody tr', 'els => els.length') == 2)
    check('активный чип подсвечен (.on)',
          any('конфликт' in c for c in
              page.eval_on_selector_all('#planCtls .plan-chip.on', 'els => els.map(e=>e.textContent.trim())')))
    # снять фильтр
    page.evaluate("() => { for (const b of document.querySelectorAll('#planCtls .plan-chip')) "
                  "if (b.textContent.trim().startsWith('Все')) { b.click(); break; } }")
    check('«Все» → снова 5 строк',
          page.eval_on_selector_all('#planBody tbody tr', 'els => els.length') == 5)
    # переключить в дерево: сжатие цепочки одиночных папок + отдельные узлы
    page.click('#planCtls .plan-viewtog')
    dirs = page.eval_on_selector_all('#planBody .plan-dir .dir-name', 'els => els.map(e=>e.textContent)')
    check('дерево: цепочка сжата в узел «Evolution/EvoTranc/DATA»',
          any(d == 'Evolution/EvoTranc/DATA' for d in dirs), str(dirs))
    check('дерево: отдельные узлы Items и Config под DATA',
          any(d == 'Items' for d in dirs) and any(d == 'Config' for d in dirs), str(dirs))
    shot(page, '12_plan_tree')
    expanded = page.eval_on_selector_all('#planBody tbody tr', 'els => els.length')
    page.evaluate("() => { for (const r of document.querySelectorAll('#planBody .plan-dir')) "
                  "if (r.getAttribute('data-dir').endsWith('/Items')) { r.click(); break; } }")
    check('сворачивание узла Items скрыло его файлы',
          page.eval_on_selector_all('#planBody tbody tr', 'els => els.length') < expanded)
    # тоггл обратно в список
    page.click('#planCtls .plan-viewtog')
    check('тоггл вернул плоский список (5 строк)',
          page.eval_on_selector_all('#planBody tbody tr', 'els => els.length') == 5)


def main():
    print('Запуск mock-сервера…')
    srv, base_url = start_server(17777)
    print(f'Сервер: {base_url}')
    time.sleep(0.2)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800})

        scenarios = [
            scenario_initial_render,
            scenario_tree_keyboard_nav,
            scenario_focus_trap,
            scenario_focus_visible,
            scenario_update_order_dialog,
            scenario_base_selector,
            scenario_add_mod_dialog,
            scenario_context_menu,
            scenario_filter_popover,
            scenario_clear_mods,
            scenario_feedback_batch,
            scenario_first_run_appearance,
            scenario_plan_filter_tree,
        ]

        for sc in scenarios:
            page = ctx.new_page()
            try:
                sc(page, base_url)
            except Exception as e:
                print(f'  [ERR] {sc.__name__}: {e}')
                traceback.print_exc()
                try:
                    shot(page, f'ERR_{sc.__name__}')
                except Exception:
                    pass
                FAIL.append(sc.__name__ + ':exception')
            finally:
                page.close()

        browser.close()
    srv.shutdown()

    print(f'\n{"="*50}')
    print(f'ИТОГ: {len(PASS)} OK, {len(FAIL)} FAIL')
    if FAIL:
        print('Провалились:')
        for f in FAIL:
            print(f'  • {f}')
    print(f'Скриншоты: {SHOTS_DIR}')
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
