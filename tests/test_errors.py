"""The error log: everything caught somewhere is still written down, it stays small, and writing it never fails."""
import tempfile
import threading
import unittest
from pathlib import Path
import errors


class ErrorLog(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        errors._folder = Path(self.temp.name) / 'logs'

    def tearDown(self):
        errors._folder = None
        self.temp.cleanup()

    def log(self): return (Path(self.temp.name) / 'logs' / 'errors.log').read_text(encoding='utf-8')

    def test_an_error_is_written_with_its_details(self):
        try: {}['missing']
        except KeyError as exc: errors.record('GET /api/thing', exc)
        text = self.log()
        self.assertIn('GET /api/thing: KeyError', text)
        self.assertIn('Traceback', text)

    def test_the_log_stays_small(self):
        for _ in range(40): errors.record('loop', text='x' * 20000)
        self.assertLess(len(self.log()), errors.LIMIT + 30000)
        self.assertTrue((Path(self.temp.name) / 'logs' / 'errors.old.log').is_file())

    def test_writing_it_never_fails(self):
        errors._folder = Path(self.temp.name) / 'a file, not a folder'
        errors._folder.write_text('in the way')
        errors.record('anything', ValueError('still fine'))   # no exception

    def test_errors_in_background_work_are_kept(self):
        import io, sys
        from unittest.mock import patch
        saved = (threading.excepthook, sys.excepthook)
        def fails(): raise RuntimeError('drawing failed')
        try:
            errors.install()
            with patch('sys.stderr', io.StringIO()):   # still shown as before (here: not on the test's screen)
                t = threading.Thread(target=fails, name='previews'); t.start(); t.join()
            self.assertIn('background work (previews): RuntimeError: drawing failed', self.log())
        finally:
            threading.excepthook, sys.excepthook = saved


if __name__ == '__main__':
    unittest.main()
