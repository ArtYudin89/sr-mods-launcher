"""Language switching, safe substitutions and translated user workflows."""
import json
import re
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import app
import test_playwright as fixtures
from playwright.sync_api import sync_playwright


class HtmlStructure(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.tags = []
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        self.tags.append(('start', tag, sorted((k, v) for k, v in attrs
                         if k not in ('title', 'placeholder', 'aria-label'))))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.tags.append(('end', tag))


class CatalogTest(unittest.TestCase):
    def test_translation_keeps_placeholders_and_html_controls(self):
        source = (Path(__file__).parent / 'web/en.js').read_text(encoding='utf-8')
        catalog = json.loads(source[source.index('{'):source.rindex('}') + 1])
        for original, translated in catalog.items():
            with self.subTest(original=original):
                self.assertTrue(translated.strip())
                self.assertEqual(Counter(re.findall(r'\{\d+\}', original)),
                                 Counter(re.findall(r'\{\d+\}', translated)))
                self.assertEqual(HtmlStructure(original).tags, HtmlStructure(translated).tags)
                self.assertIsNone(re.search('[А-Яа-яЁё]', translated))

    def test_language_is_saved_and_native_file_filters_follow_it(self):
        api = app.Api.__new__(app.Api)
        api.config = {}
        saved = []
        api._save_config = lambda: saved.append(dict(api.config))
        self.assertFalse(api.set_language('unknown')['ok'])
        self.assertEqual(saved, [])
        self.assertTrue(api.set_language('en')['ok'])
        self.assertEqual(saved[-1]['language'], 'en')
        self.assertEqual(api._file_types('Профиль (*.json)', 'Все файлы (*.*)'),
                         ('Profile (*.json)', 'All files (*.*)'))
        api.set_language('ru')
        self.assertEqual(api._file_types('Текстовый файл (*.txt)'), ('Текстовый файл (*.txt)',))


class EnglishUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, _ = fixtures.start_server(0)
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        previous = dict(fixtures.MOCK_DISPATCH)
        self.addCleanup(lambda: (fixtures.MOCK_DISPATCH.clear(), fixtures.MOCK_DISPATCH.update(previous)))
        self.state = fixtures._mock_state()
        fixtures.MOCK_DISPATCH['get_state'] = lambda args: self.state
        fixtures.MOCK_DISPATCH['set_language'] = lambda args: (self.state.update(language=args[0]) or {'ok': True})
        self.page = self.browser.new_page(viewport={'width': 1024, 'height': 768})
        self.addCleanup(self.page.close)
        fixtures.init_page(self.page, self.url)

    def switch(self, language):
        self.page.evaluate('openSettings()')
        self.page.locator('#setLanguage').select_option(language)
        self.page.wait_for_function('language => document.documentElement.lang === language', arg=language)
        self.page.wait_for_function('!document.getElementById("settingsOverlay").classList.contains("hidden")')

    def test_switch_persists_and_restores_russian(self):
        self.switch('en')
        self.assertIn('Add mod', self.page.locator('#addBtn').inner_text())
        self.assertIn('GB', self.page.locator('#queueLbl').inner_text())
        self.assertIsNone(re.search('[А-Яа-яЁё]', self.page.locator('#settingsOverlay').inner_text().replace('Русский', '')))
        self.page.reload()
        self.page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        fixtures.wait_tree(self.page)
        self.assertEqual(self.page.locator('html').get_attribute('lang'), 'en')
        self.assertIn('Add mod', self.page.locator('#addBtn').inner_text())
        self.switch('ru')
        self.assertIn('Добавить мод', self.page.locator('#addBtn').inner_text())

    def test_status_logs_and_values_have_separate_languages(self):
        self.switch('en')
        row = self.page.locator('.leaf[data-mid="FairansVision/FairansBalance"]')
        self.assertIn('update', row.locator('.badge').inner_text())
        self.assertIn('Любимое', row.inner_text())
        self.page.evaluate("appendLog('Установка закончена. Обработано пунктов набора: 12.', 'ok'); onOpBegin(); onOpPaused({paused:true})")
        self.assertIn('Installation complete. Processed 12 profile items.', self.page.locator('#logBox').inner_text())
        self.assertIn('Resume', self.page.locator('#pauseBtn').inner_text())
        cases = [
            ('Моду «Ошибка» нужен «МодДва», которого нет ни в профиле, ни в каталоге, ни на диске.', 'Mod “Ошибка” requires “МодДва”, which is absent from the profile, catalogue and disk.'),
            ('Папка игры недоступна: D:\\Игры\\Ошибка\nПроверьте, что диск подключён и путь верный, затем повторите.', 'Game folder unavailable: D:\\Игры\\Ошибка\nCheck the drive connection and path, then try again.'),
            ('Проверка закончена: обновить можно 2 мода.', 'Check complete: 2 mods can be updated.'),
        ]
        for original, translated in cases:
            with self.subTest(original=original):
                self.assertEqual(self.page.evaluate('value => uiMessage(value)', original), translated)

    def test_full_and_basic_tours_are_english_and_fit_at_large_scale(self):
        self.switch('en')
        self.page.evaluate("hide('settingsOverlay'); STATE.ui_scale=160; STATE.text_scale=170; applyView()")
        for tour in ['basic', 'full']:
            self.page.evaluate('tour => startTour(tour)', tour)
            for _ in range(self.page.evaluate('tourSteps.length')):
                self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                self.assertIsNone(re.search('[А-Яа-яЁё]', self.page.locator('#tourTip').inner_text()))
                box = self.page.locator('#tourNext').bounding_box()
                self.assertGreaterEqual(box['y'], 0)
                self.assertLessEqual(box['y'] + box['height'], 769)
                self.page.locator('#tourNext').click()

    def test_silent_update_summary_translates_counts_and_keeps_mod_name(self):
        self.switch('en')
        self.page.evaluate("onMergeSilent({text:'МойМод обновлён', sub:'обновлено 2 файла · добавлено 1 файл', sub_parts:['обновлено 2 файла','добавлено 1 файл']})")
        text = self.page.locator('.toast').last.inner_text()
        self.assertIn('МойМод updated', text)
        self.assertIn('updated 2 files', text)
        self.assertIn('added 1 file', text)

    def test_update_decisions_and_plan_translate_without_changing_paths(self):
        self.switch('en')
        self.page.evaluate("hide('settingsOverlay'); onMergePlan({id:'МойМод', summary:{}, labels:{add:'➕ добавляется новый файл'}, groups:{add:['Папка/Файл.txt']}, conflicts:[{status:'conflict_text', relpath:'Папка/Файл.txt', display:'Папка/Файл.txt', default:'mine', options:[{code:'mine',label:'оставить мой'},{code:'theirs',label:'взять новый'}]}]})")
        body = self.page.locator('#mergeOverlay').inner_text()
        self.assertIn('keep mine', body)
        self.assertIn('Папка/Файл.txt', body)
        self.assertNotIn('и вы, и мод', body)
        self.page.evaluate("hide('mergeOverlay'); onPreviewPlan({id:'МойМод', files:[{status:'add', path:'Папка/Файл.txt', source:'developer', new_size:2048}], summary:{add:1}}); show('planOverlay')")
        body = self.page.locator('#planOverlay').inner_text()
        self.assertIn('ACTION', body)
        self.assertIn('Папка/Файл.txt', body)
        self.assertNotIn('добавится', body)


if __name__ == '__main__':
    unittest.main()
