"""Profile membership and remembered choices when adding several mods."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import app


class AddFeedbackTest(unittest.TestCase):
    def api(self):
        api = app.Api.__new__(app.Api)
        api.config = {}
        api.profile = {'mods': [], 'enabled': []}
        api._save_config = lambda: None
        api._catalog_cache = {
            'Group/A': {'name': 'A', 'variants': [{'source': 'redux/base'}]},
            'Group/B': {'name': 'B', 'variants': [{'source': 'redux/pack'}]},
            'Group/C': {'name': 'C', 'variants': [{'source': 'redux/addon'}]},
        }
        api._packs_cache = {
            'redux/base': {'camp': 'redux', 'name': 'base', 'tier': 'base'},
            'redux/pack': {'camp': 'redux', 'name': 'pack', 'tier': 'pack'},
            'redux/addon': {'camp': 'redux', 'name': 'addon', 'tier': 'pack', 'addon': True},
        }
        return api

    def test_search_marks_direct_and_already_installed_profile_mods(self):
        api = self.api()
        api.profile['mods'] = [{'type': 'desc', 'id': 'Group/A@variant'}]
        api.profile['enabled'] = ['Group/B']
        mods = {m['id']: m for m in api.get_all_mods()['mods']}
        self.assertTrue(mods['Group/A']['in_profile'])
        self.assertTrue(mods['Group/B']['in_profile'])
        self.assertFalse(mods['Group/C']['in_profile'])

    def test_whole_camp_excludes_removed_mods_and_optional_packs(self):
        api = self.api()
        api.profile['mods'] = [{'type': 'camp', 'camp': 'redux', 'skip': ['Group/A']}]
        mods = {m['id']: m for m in api.get_all_mods()['mods']}
        self.assertFalse(mods['Group/A']['in_profile'])
        self.assertTrue(mods['Group/B']['in_profile'])
        self.assertFalse(mods['Group/C']['in_profile'])

    def test_engine_only_does_not_mark_mods_added(self):
        api = self.api()
        api.profile['mods'] = [{'type': 'camp', 'camp': 'redux', 'part': 'base'}]
        self.assertFalse(any(m['in_profile'] for m in api.get_all_mods()['mods']))

    def test_selected_pack_covers_its_mods(self):
        api = self.api()
        api.profile['mods'] = [{'type': 'unit', 'camp': 'redux', 'unit': 'pack', 'mod': ''}]
        mods = {m['id']: m for m in api.get_all_mods()['mods']}
        self.assertTrue(mods['Group/B']['in_profile'])
        self.assertFalse(mods['Group/A']['in_profile'])

    def test_add_selection_is_saved_and_rejects_non_object(self):
        api = self.api()
        selection = {'mode': 'src', 'camp': 'redux', 'pack': 'pack', 'mod': 'Group/B'}
        self.assertTrue(api.set_add_selection(selection)['ok'])
        self.assertEqual(api.config['add_selection'], selection)
        self.assertFalse(api.set_add_selection('redux')['ok'])
        self.assertEqual(api.config['add_selection'], selection)


if __name__ == '__main__':
    unittest.main()
