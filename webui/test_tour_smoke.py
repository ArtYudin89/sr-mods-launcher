"""Смоук обучающего тура (Playwright): тур проходит все шаги без ошибок консоли, а шаги
«Окно профилей / Добавление модов / Настройки» реально ОТКРЫВАЮТ соответствующие окна и
закрывают их при уходе с шага / завершении тура. Запуск: python webui/test_tour_smoke.py"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import test_playwright as T
from playwright.sync_api import sync_playwright

FAIL = []
def ck(name, cond):
    print(('[OK ] ' if cond else '[FAIL] ') + name)
    if not cond:
        FAIL.append(name)

srv, base = T.start_server(17798)
try:
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page()
        errs = []
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)))
        T.init_page(pg, base)
        time.sleep(0.3)
        pg.evaluate('startTour()')
        time.sleep(0.3)
        n = pg.evaluate('TOUR_STEPS.length')
        ck('шагов тура >= 15 (покрытие расширено)', n >= 15)
        OVS = ['profileOverlay', 'addOverlay', 'settingsOverlay']
        opened = {}
        for i in range(n):
            title = pg.evaluate("document.getElementById('tourTipTitle').textContent")
            for ov in OVS:
                if pg.evaluate(f"!document.getElementById('{ov}').classList.contains('hidden')"):
                    opened.setdefault(ov, title)
            # ровно одно окно (или ни одного) открыто на каждом шаге — тур не плодит окна
            vis_now = [ov for ov in OVS
                       if pg.evaluate(f"!document.getElementById('{ov}').classList.contains('hidden')")]
            if len(vis_now) > 1:
                FAIL.append(f'шаг {i}: открыто >1 окна: {vis_now}')
            if i < n - 1:
                pg.evaluate('tourGo(1)'); time.sleep(0.12)
        pg.evaluate('tourGo(1)'); time.sleep(0.2)   # финиш с последнего шага
        ck('шаг «Окно профилей» открыл profileOverlay', opened.get('profileOverlay') is not None)
        ck('шаг «Добавление модов» открыл addOverlay', opened.get('addOverlay') is not None)
        ck('шаг «Настройки» открыл settingsOverlay', opened.get('settingsOverlay') is not None)
        closed = all(pg.evaluate(f"document.getElementById('{ov}').classList.contains('hidden')") for ov in OVS)
        ck('все окна закрыты после завершения тура', closed)
        real = [e for e in errs if 'favicon' not in e.lower()]
        ck('нет ошибок консоли во время тура', not real)
        if real:
            print('   ошибки:', real[:6])
finally:
    srv.shutdown()

print(f'\n===== ИТОГ: FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
