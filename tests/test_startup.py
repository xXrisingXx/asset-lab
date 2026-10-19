"""The lab starting the way the launcher starts it, on PCs in every state a player's can be in.

Each case runs src/lab_server.py in its own process (with the launcher's arguments and environment) on a fake game and a
fresh home folder, and the lab must come up and answer, never stop at start: a crash there leaves players with nothing
but a traceback (1.0.54 stopped for players whose game had a mod from another workspace).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from PIL import Image
from fixtures import make_game
from lab_core import Lab, png

SRC = Path(__file__).resolve().parents[1] / 'src'


class Startup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'Usuário José' / 'Asset Lab data'   # spaces and accents, like players' folders
        self.home.mkdir(parents=True)
        self.game = make_game(Path(self.temp.name) / 'Steam library')

    def tearDown(self):
        for _ in range(20):   # the lab's files are let go a moment after it stops
            try: self.temp.cleanup(); return
            except OSError: time.sleep(0.25)

    def lab(self, workspace=None):
        """A lab made here, as the player's earlier sessions left it."""
        return Lab(self.game, workspace or self.home / 'workspace', self.home / 'install-ledgers', process_check=lambda: [],
                   settings_file=self.home / 'settings.json')

    def edit_and_install(self, lab):
        key = next(r['key'] for r in lab.catalog.rows.values() if r['name'] == 'bg2.png')
        im = lab.image(key); im.paste('#28bcb4', (0, 0, im.width, im.height))
        lab.save(key, png(im), lab.revision(key), {'color': '#28bcb4'})
        lab.install(lab.build()['id'])

    def start(self, *extra, game=True):
        """Start the lab like the launcher does; returns (status from /api/status or None, everything it printed)."""
        env = dict(os.environ, ASSET_LAB_HOME=str(self.home), ASSET_LAB_LAUNCHER='1', ASSET_LAB_RUNTIME=str(self.home / 'runtime'),
                   ASSET_LAB_VERSION='test', PYTHONIOENCODING='utf-8')
        args = [sys.executable, '-B', '-X', 'utf8', str(SRC / 'lab_server.py'), '--no-browser', '--port', '0', *extra]
        if game: args += ['--game', str(self.game)]
        proc = subprocess.Popen(args, cwd=str(SRC), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                creationflags=0x08000000 if os.name == 'nt' else 0)
        out, status = [], None
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None: break
                    continue
                out.append(line)
                if line.startswith('Asset Lab: http'):
                    with urllib.request.urlopen(line.split(': ', 1)[1].strip() + '/api/status', timeout=20) as r: status = json.load(r)
                    break
        finally:
            proc.kill(); proc.wait(10)
            out.append(proc.stdout.read() or '')
            proc.stdout.close()
        return status, ''.join(out)

    def assertStarts(self, *extra):
        status, out = self.start(*extra)
        self.assertIsNotNone(status, 'the lab stopped at start:\n' + out[-2500:])
        self.assertFalse(status.get('rescue'), 'the lab opened in safe mode:\n' + out[-2500:])   # the real lab, not its safe mode
        return status

    def test_a_first_start(self):
        self.assertStarts()

    def test_a_damaged_state_file(self):
        self.lab()
        (self.home / 'workspace' / 'state.json').write_text('{ not json', encoding='utf-8')
        self.assertStarts()

    def test_damaged_settings(self):
        (self.home / 'settings.json').write_text('[1, 2', encoding='utf-8')
        self.assertStarts()

    def test_a_damaged_install_record(self):
        lab = self.lab()
        (lab.ledger).write_text('garbage', encoding='utf-8')
        self.assertStarts()

    def test_a_mod_installed_from_another_workspace(self):
        self.edit_and_install(self.lab(self.home / 'an older workspace'))
        status = self.assertStarts()
        self.assertTrue(status['active'])                                   # its mod is known, so it can be restored
        self.assertEqual(Path(status['workspace']).resolve(), (self.home / 'an older workspace').resolve())

    def test_a_mod_from_a_workspace_that_is_gone(self):
        self.edit_and_install(self.lab(self.home / 'deleted workspace'))
        shutil.rmtree(self.home / 'deleted workspace')
        status = self.assertStarts()
        self.assertTrue(any('no longer on this PC' in w for w in status['warnings']))

    def test_an_interrupted_install(self):
        lab = self.lab()
        self.edit_and_install(lab)
        record = json.loads(lab.ledger.read_text(encoding='utf-8')); record['state'] = 'installing'
        lab.ledger.write_text(json.dumps(record), encoding='utf-8')
        self.assertStarts()

    def test_a_damaged_archive_is_left_out(self):
        # A cut-off zip (it starts like one, the rest is missing): the lab opens without it and says so.
        good = (self.game / 'assets' / 'bg2.png').read_bytes()
        (self.game / 'assets' / 'broken.zip').write_bytes(b'PK\x03\x04' + b'\0' * 40)
        status = self.assertStarts()
        self.assertTrue(any('broken.zip could not be read' in w for w in status['warnings']))
        self.assertEqual((self.game / 'assets' / 'bg2.png').read_bytes(), good)

    def test_the_game_updated_since_the_workspace_was_made(self):
        self.lab()
        (self.game / 'assets' / 'bg2.png').write_bytes(png(Image.new('RGBA', (64, 64), 'blue')))   # Steam changed a file
        self.assertStarts()

    def test_a_start_that_fails_opens_safe_mode(self):
        # No game where it was: instead of a lab that is gone, safe mode answers and says why, in words.
        status, out = self.start('--game', str(Path(self.temp.name) / 'nowhere'), game=False)
        self.assertTrue(status and status.get('rescue'), out[-2500:])
        self.assertIn('Mitos.is installation', status['error'])
        self.assertIn('Mitos.is installation', (self.home / 'logs' / 'errors.log').read_text(encoding='utf-8'))   # and in the error log

    def test_safe_mode_puts_the_original_files_back(self):
        import rescue
        lab = self.lab()
        original = (self.game / 'assets' / 'bg2.png').read_bytes()
        self.edit_and_install(lab)
        self.assertNotEqual((self.game / 'assets' / 'bg2.png').read_bytes(), original)
        os.environ['ASSET_LAB_HOME'], saved = str(self.home), os.environ.get('ASSET_LAB_HOME')
        try:
            self.assertTrue(rescue.installed(self.game))
            with self.assertRaisesRegex(ValueError, 'Close every Mitosis'): rescue.restore(self.game, running=lambda: True)
            self.assertIn('original files back', rescue.restore(self.game, running=lambda: False))
            self.assertIsNone(rescue.installed(self.game))
        finally:
            if saved is None: os.environ.pop('ASSET_LAB_HOME')
            else: os.environ['ASSET_LAB_HOME'] = saved
        self.assertEqual((self.game / 'assets' / 'bg2.png').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
