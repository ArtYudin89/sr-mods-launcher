"""Download progress and temporary files as observed by the launcher user."""
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import app


class DownloadFeedbackTest(unittest.TestCase):
    def api(self):
        api = app.Api.__new__(app.Api)
        api._paused = threading.Event()
        api._chunk_lock = threading.Lock()
        api._chunk_prog = {}
        api._dl_bytes = 0
        api._pack_ctx = 'Redux'
        api.events = []
        api._emit = lambda event, payload=None: api.events.append((event, payload))
        api._begin_op()
        return api

    def progress(self, api):
        api._emit_parts(force=True)
        return api.events[-1][1]

    def test_finishing_one_download_keeps_other_download_progress(self):
        api = self.api()
        api._part_progress(0, 2)
        api._chunk_progress('a.zip', 80, 100)
        api._chunk_progress('b.zip', 60, 100)
        before = self.progress(api)['pct']
        api._chunk_progress('a.zip', 100, 100)
        finished = self.progress(api)['pct']
        api._part_progress(1, 2)
        extracted = self.progress(api)['pct']
        self.assertEqual([before, finished, extracted], [70, 80, 80])
        api._chunk_progress('b.zip', 100, 100)
        api._part_progress(2, 2)
        self.assertEqual(self.progress(api)['pct'], 100)

    def test_retry_does_not_move_percentage_backwards(self):
        api = self.api()
        api._part_progress(0, 1)
        api._chunk_progress('a.zip', 80, 100)
        before = self.progress(api)['pct']
        api._chunk_progress('a.zip', 20, 100)
        self.assertGreaterEqual(self.progress(api)['pct'], before)

    def test_cached_parts_and_active_downloads_both_contribute(self):
        api = self.api()
        api._part_progress(0, 4)
        api._part_progress(1, 4)  # a cached part completed without byte callbacks
        api._chunk_progress('b.zip', 50, 100)
        self.assertEqual(self.progress(api)['pct'], 38)
        api._chunk_progress('c.zip', 90, 100)
        before = self.progress(api)['pct']
        api._chunk_progress('c.zip', 100, 100)
        self.assertGreaterEqual(self.progress(api)['pct'], before)
        api._part_progress(2, 4)
        self.assertEqual(self.progress(api)['pct'], 62)
        api._part_progress(1, 4)  # late callback from another worker
        self.assertEqual(self.progress(api)['parts'], '2/4')

    def test_new_pack_has_known_count_and_its_own_progress(self):
        api = self.api()
        api._part_progress(0, 1)
        api._chunk_progress('a.zip', 100, 100)
        api._part_progress(1, 1)
        api._pack_ctx = 'Universe'
        api._part_progress(0, 4)
        result = self.progress(api)
        self.assertEqual((result['parts'], result['pct']), ('0/4', 0))

    def test_progress_names_real_archive_and_shows_its_size(self):
        api = self.api()
        api._part_progress(0, 2)
        api._chunk_progress('assets-base.zip', 1 << 20, 4 << 20)
        result = self.progress(api)
        self.assertEqual(result['file'], 'assets-base.zip')
        self.assertEqual(result['file_total_mb'], '4.0')
        self.assertEqual(result['file_done_mb'], '1.0')

    def test_download_directory_is_private_and_cleaned(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'LOCALAPPDATA': root}):
            first, second = self.api(), self.api()
            one, two = first._download_dir(), second._download_dir()
            self.assertNotEqual(one, two)
            self.assertTrue(one.is_relative_to(Path(root)))
            (one / '_chunk_a.zip').write_bytes(b'partial')
            first._cleanup_downloads()
            second._cleanup_downloads()
            self.assertFalse(one.exists())
            self.assertFalse(two.exists())

    def test_total_is_announced_before_parallel_download_starts(self):
        events = []
        with tempfile.TemporaryDirectory() as root:
            def resolver(chunk, destination, *_args):
                events.append(('fetch', chunk))
                with zipfile.ZipFile(destination, 'w'):
                    pass
            app.core._parallel_fetch_extract(
                {'a.zip': {}, 'b.zip': {}}, Path(root) / 'Mods', root, resolver, lambda *_: None,
                part_cb=lambda done, total: events.append(('parts', done, total)))
        self.assertEqual(events[0], ('parts', 0, 2))


if __name__ == '__main__':
    unittest.main()
