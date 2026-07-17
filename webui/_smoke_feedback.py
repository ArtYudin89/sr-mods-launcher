"""Рендер-смоук по фичам из отзывов (кластеры A/B/C): грузим страницу с mock-api,
ловим ошибки консоли и проверяем, что новые элементы отрисованы и кликаются.
Запуск: python webui/_smoke_feedback.py"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import test_playwright as T
from playwright.sync_api import sync_playwright

srv, base = T.start_server(17790)
errs = []
try:
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page()
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)))
        T.init_page(pg, base)
        # закрыть онбординг-тур, если открылся (перехватывает клики)
        pg.evaluate("var t=document.getElementById('tourOverlay'); if(t){t.classList.add('hidden');t.style.display='none';}")
        time.sleep(0.4)

        def ck(name, cond):
            print(('  [OK ] ' if cond else '  [FAIL] ') + name)
            return cond

        ok = True
        # C: чекбоксы выделения в каждой строке
        ok &= ck('чекбоксы строк отрисованы', pg.locator('.row-check').count() >= 3)
        # A: пользовательские теги + иконка заметки
        ok &= ck('тег .utag виден', pg.locator('.utag', has_text='Любимое').count() >= 1)
        ok &= ck('иконка заметки 📝', pg.locator('.note-ic').count() >= 1)
        # A: чипы-фильтры по тегам в рельсе
        ok &= ck('чипы тегов в рельсе', pg.locator('#tagChips .utag-f').count() == 2)
        # A: скрытый мод показан (show_hidden) с пометкой
        ok &= ck('скрытый мод помечен', pg.locator('.row.leaf.hidden-mod').count() == 1)
        # A(5): полное описание в списке
        ok &= ck('полное описание в строке (.mdesc.full)', pg.locator('.mdesc.full').count() >= 1)
        # D: дата в строке
        ok &= ck('дата в строке (.mdate)', pg.locator('.mdate').count() >= 1)

        # #2 ВЫБОР: клик по чекбоксу отмечает строку (галочка → .sel)
        pg.locator('.row-check').first.click()
        ok &= ck('чекбокс → ВЫБРАНА (.sel)', pg.locator('.row.leaf.sel').count() >= 1)

        # #2 ВЫДЕЛЕНИЕ: обычный клик по строке делает её активной (.active), галочки НЕ трогает
        rows = pg.locator('#treeBody .row.leaf')
        sel_before = pg.locator('.row.leaf.sel').count()
        rows.nth(1).click()
        ok &= ck('клик по строке → ВЫДЕЛЕНА (.active, ровно одна)', pg.locator('.row.leaf.active').count() == 1)
        ok &= ck('обычный клик НЕ меняет выбор (галочки)', pg.locator('.row.leaf.sel').count() == sel_before)
        ok &= ck('выделенная строка не стала выбранной', 'sel' not in (rows.nth(1).get_attribute('class') or ''))

        # #2 ВЫБОР диапазоном: клик по строке (якорь) + Shift-клик → отмечает диапазон галочками
        rows.nth(0).click()
        pg.keyboard.down('Shift'); rows.nth(2).click(); pg.keyboard.up('Shift')
        ok &= ck('Shift-диапазон ВЫБРАЛ ≥3 (.sel)', pg.locator('.row.leaf.sel').count() >= 3)

        # D(8)/note: иконка заметки теперь у правого края строки (в ячейке статуса)
        ok &= ck('📝 в ячейке статуса (правый край)', pg.locator('#treeBody .cell .note-ic').count() >= 1)

        # B: список связей выбранного мода (второй список с теми же колонками)
        ok &= ck('панель связей есть (#relWrap)', pg.locator('#relWrap').count() == 1)
        pg.locator('#treeBody .row.leaf').first.click()
        time.sleep(0.35)
        ok &= ck('панель НЕ авторазворачивается при выборе (#1)', 'collapsed' in (pg.locator('#relWrap').get_attribute('class') or ''))
        pg.locator('#relHead').click()  # раскрыть вручную
        time.sleep(0.15)
        ok &= ck('после ручного разворота видно группы связей', pg.locator('#relBody .rel-group').count() >= 1)
        ok &= ck('строка-копия связанного мода', pg.locator('#relBody .row.leaf').count() >= 1)
        # клик по связанной строке → показать связи уже ЕГО (навигация)
        rel_before = pg.locator('#relSub').inner_text()
        pg.locator('#relBody .row.leaf').first.click()
        time.sleep(0.35)
        ok &= ck('навигация по связи (relSub сменился)', pg.locator('#relSub').inner_text() != rel_before)
        # сворачивание панели по заголовку
        pg.locator('#relHead').click()
        time.sleep(0.15)
        ok &= ck('панель связей сворачивается', 'collapsed' in (pg.locator('#relWrap').get_attribute('class') or ''))

        # B: карточка ⓘ теперь только инфо (без связей), плавающая
        pg.locator('#treeBody .info-btn').first.click()
        time.sleep(0.3)
        ok &= ck('карточка ⓘ открылась (.mod-card)', pg.locator('.mod-card').count() == 1)
        ok &= ck('в карточке НЕТ блока связей', pg.locator('.mod-card .mc-rel-toggle').count() == 0)
        pg.locator('.mc-collapse').first.click()
        time.sleep(0.15)
        ok &= ck('карточка сворачивается в шапку', not pg.locator('.mod-card .mc-body').first.is_visible())

        # закрыть карточку (перекрывает дерево), потом ПКМ по моду
        pg.evaluate("document.querySelectorAll('.mod-card').forEach(c=>c.remove())")
        time.sleep(0.2)
        pg.locator('#treeBody .row.leaf').first.click(button='right')
        time.sleep(0.2)
        ok &= ck('ПКМ: пункт «Теги…»', pg.locator('#ctxTags').is_visible())
        ok &= ck('ПКМ: пункт «Заметка…»', pg.locator('#ctxNote').is_visible())
        pg.keyboard.press('Escape')  # закрыть ctx

        pg.screenshot(path=str(T.SHOTS_DIR / 'feedback_features.png'))
        br.close()
    ok &= ck('нет ошибок консоли', not errs)
    if errs:
        print('  console errors:', errs[:8])
    print('\nИТОГ:', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)
finally:
    srv.shutdown()
