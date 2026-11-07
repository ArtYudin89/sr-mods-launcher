"""User feedback scenarios in a browser, including large UI/text scales."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_playwright as fixtures
from playwright.sync_api import sync_playwright


class FeedbackUiTest(unittest.TestCase):
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
        self.page = self.browser.new_page(viewport={'width': 1024, 'height': 768})
        fixtures.init_page(self.page, self.url)

    def tearDown(self):
        self.page.close()

    def layout(self):
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    def assert_navigation_visible(self):
        box = self.page.locator('#tourNext').bounding_box()
        size = self.page.viewport_size
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box['x'], 0)
        self.assertGreaterEqual(box['y'], 0)
        self.assertLessEqual(box['x'] + box['width'], size['width'] + 1)
        self.assertLessEqual(box['y'] + box['height'], size['height'] + 1)

    def test_pause_has_visible_background_in_both_themes(self):
        for theme in ['dark', 'light']:
            self.page.evaluate("theme => { document.documentElement.dataset.theme = theme; onOpBegin(); onOpPaused({paused: true}); }", theme)
            style = self.page.locator('#pauseBtn').evaluate('(button) => { const s = getComputedStyle(button); return {bg:s.backgroundColor, fg:s.color}; }')
            self.assertNotEqual(style['bg'], 'rgba(0, 0, 0, 0)')
            self.assertNotEqual(style['bg'], style['fg'])
            self.assertIn('Продолжить', self.page.locator('#pauseBtn').inner_text())

    def test_short_training_is_available_while_downloading_and_can_repeat(self):
        self.page.evaluate('onOpBegin()')
        for _ in range(2):
            self.page.locator('#tourBtn').click()
            self.page.locator('#trainingBasic').click()
            self.assertEqual(self.page.evaluate('tourSteps.length'), 5)
            for _ in range(5):
                self.layout()
                self.assert_navigation_visible()
                self.page.locator('#tourNext').click()
            self.assertTrue(self.page.locator('#tourOverlay').evaluate("el => el.classList.contains('hidden')"))

    def test_every_full_tour_step_stays_in_window_at_large_scale(self):
        for viewport in [{'width': 1024, 'height': 768}, {'width': 1280, 'height': 800}]:
            self.page.set_viewport_size(viewport)
            self.page.evaluate("STATE.ui_scale = 160; STATE.text_scale = 170; applyView(); startTour('full')")
            for step in range(self.page.evaluate('tourSteps.length')):
                with self.subTest(viewport=viewport, step=step):
                    self.layout()
                    self.assert_navigation_visible()
                    self.page.locator('#tourNext').click()

    def test_progress_names_download_without_zero_of_zero(self):
        self.page.evaluate("onOpBegin(); onProgress({mode:'parts', parts:'0/0', pct:0, ctx:'Redux', file:'assets-base.zip', file_done_mb:'1.0', file_total_mb:'4.0', active_count:2})")
        text = self.page.locator('#progText').inner_text()
        self.assertIn('assets-base.zip', text)
        self.assertIn('1.0/4.0', text)
        self.assertNotIn('0/0', text)

    def test_add_dialog_remembers_source_and_marks_profile_members(self):
        previous = dict(fixtures.MOCK_DISPATCH)
        self.addCleanup(lambda: (fixtures.MOCK_DISPATCH.clear(), fixtures.MOCK_DISPATCH.update(previous)))
        remembered = {}
        fixtures.MOCK_DISPATCH['get_camp_packs'] = lambda args: {'ok': True, 'camps': {
            'redux': [{'camp': 'redux', 'unit': 'pack', 'name': 'Example pack', 'in_profile': True}]}}
        fixtures.MOCK_DISPATCH['get_unit_mods'] = lambda args: {'ok': True, 'mods': [
            {'key': 'Group/A', 'name': 'Mod A', 'in_profile': False},
            {'key': 'Group/B', 'name': 'Mod B', 'in_profile': True}]}
        fixtures.MOCK_DISPATCH['get_all_mods'] = lambda args: {'ok': True, 'mods': [
            {'id': 'Group/B', 'name': 'Mod B', 'camps': ['redux'], 'in_profile': True}]}
        fixtures.MOCK_DISPATCH['set_add_selection'] = lambda args: (remembered.update(args[0]) or {'ok': True})
        fixtures.MOCK_DISPATCH['get_state'] = lambda args: dict(fixtures._mock_state(), add_selection=remembered)
        self.page.evaluate('openAdd()')
        self.page.locator('[data-amode="src"]').click()
        self.page.locator('#addCamp').select_option('redux')
        self.page.locator('#addPack').select_option('pack')
        self.page.wait_for_function("document.getElementById('addMod').options.length === 3")
        self.assertIn('✓', self.page.locator('#addMod option[value="Group/B"]').inner_text())
        self.page.locator('#addMod').select_option('Group/A')
        self.page.evaluate('doAdd()')
        self.page.evaluate('openAdd()')
        self.assertEqual(self.page.locator('#addCamp').input_value(), 'redux')
        self.assertEqual(self.page.locator('#addPack').input_value(), 'pack')
        self.assertEqual(self.page.locator('#addMod').input_value(), 'Group/A')
        self.page.locator('[data-amode="search"]').click()
        self.page.locator('#addSearchList .already-added').wait_for()
        self.assertIn('в профиле', self.page.locator('#addSearchList').inner_text())
        self.page.reload()
        self.page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        fixtures.wait_tree(self.page)
        self.page.evaluate('openAdd()')
        self.assertEqual(self.page.locator('#addPack').input_value(), 'pack')


if __name__ == '__main__':
    unittest.main()
