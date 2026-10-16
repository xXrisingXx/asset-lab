"""Settings (src/settings.py): the file and its checks, the HTTP API, and what every setting the lab itself acts on
does. Settings the page applies (the look, the editor, the library on screen, the sound editor's defaults...) are
proven in tests/browser/test_settings.py; the launcher's (open_browser, check_updates, language) are read by
launcher/AssetLab.cs class LabSettings. Everything runs offline in temp folders."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import numpy as np
from PIL import Image
import audio
import design
from design import Designer
import fonts
from fixtures import make_game
import lab_core
from lab_core import Lab, png
import lab_server
from lab_server import server_for
import runtime
import settings as labsettings
from settings import Settings, SCHEMA, BY_KEY, CATEGORIES
import theme
from test_audio import audio_game, encode, tone, Serve
from test_fonts import font_game
from test_theme import Base

ROOT = Path(__file__).resolve().parents[1]


def wait(predicate, timeout=20, message='timed out'):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value: return value
        time.sleep(0.02)
    raise AssertionError(message)


class Http:
    """server_for(lab) on a free port: get(path) and post(path, data) return (status, json)."""
    def __init__(self, lab):
        self.lab, self.server = lab, server_for(lab, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def call(self, path, data=None, token=True):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(self.url + path, body, headers={'Content-Type': 'application/json'} if body is not None else {})
        if body is not None and token: req.add_header('X-Lab-Token', self.lab.session)
        try:
            with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def get(self, path): return self.call(path)

    def post(self, path, data, token=True): return self.call(path, data, token)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)


class Temp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / 'settings.json'

    def tearDown(self): self.temp.cleanup()

    def stored(self): return json.loads(self.file.read_text(encoding='utf-8'))


# ----- the schema ------------------------------------------------------------------------------------------------------
class SchemaTests(unittest.TestCase):
    def test_every_setting_is_described_and_its_default_fits(self):
        keys = [s['key'] for s in SCHEMA]
        self.assertEqual(len(keys), len(set(keys)))
        for s in SCHEMA:
            self.assertIn(s['category'], [c['id'] for c in CATEGORIES], s['key'])
            self.assertTrue(s['label'] and s['description'], s['key'])
            self.assertIn(s.get('restart'), (None, 'app', 'page'), s['key'])
            if s['type'] == 'action': continue
            self.assertEqual(labsettings.clean(s, s['default']), s['default'], s['key'])
            if s['type'] == 'choice':
                self.assertIn(s['default'], [c['value'] for c in s['choices']], s['key'])
            if s['type'] == 'number': self.assertTrue(s['min'] <= s['default'] <= s['max'], s['key'])
        self.assertEqual(json.loads(json.dumps(labsettings.public_schema())), labsettings.public_schema())   # plain JSON

    def test_language_comes_first_with_the_shared_codes(self):
        general = [s for s in SCHEMA if s['category'] == 'general']
        self.assertEqual(general[0]['key'], 'language')
        self.assertEqual(general[0]['label'], 'Language')
        self.assertEqual([c['value'] for c in general[0]['choices']], ['', 'en', 'es', 'pt', 'fr', 'de'])   # the launcher's class L codes
        self.assertEqual([c['label'] for c in general[0]['choices']], ['Automatic (Windows language)', 'English', 'Español', 'Português', 'Français', 'Deutsch'])
        self.assertEqual(general[0]['default'], '')
        self.assertIn('start window', general[0]['description'])
        cs = (ROOT / 'launcher' / 'AssetLab.cs').read_text(encoding='utf-8')
        self.assertIn('{ "en", "es", "pt", "fr", "de" }', cs)

    def test_the_settings_the_launcher_reads_are_marked_and_named_the_same(self):
        cs = (ROOT / 'launcher' / 'AssetLab.cs').read_text(encoding='utf-8')
        for key in ('open_browser', 'check_updates'):
            self.assertEqual(BY_KEY[key]['restart'], 'app')
            self.assertIn(f'Flag("{key}", true)', cs)
        self.assertIn('Text("language")', cs)


# ----- the file --------------------------------------------------------------------------------------------------------
class FileTests(Temp):
    def test_defaults_without_a_file_and_only_changes_are_stored(self):
        s = Settings(self.file)
        self.assertEqual(s.values(), {k: spec['default'] for k, spec in BY_KEY.items()})
        self.assertEqual((s.changed(), s.damaged, self.file.exists()), ([], False, False))
        s.update({'ui_size': 125, 'accent': 'pink', 'language': 'de'})
        data = self.stored()
        self.assertEqual({k: v for k, v in data.items() if k != '//'}, {'ui_size': 125, 'accent': 'pink', 'language': 'de'})
        self.assertIn('Asset Lab settings', data['//'])
        self.assertEqual(s.changed(), ['accent', 'language', 'ui_size'])
        s.update({'accent': 'teal', 'language': ''})           # back to the default: out of the file
        self.assertEqual({k for k in self.stored() if k != '//'}, {'ui_size'})
        self.assertEqual(s.changed(), ['ui_size'])

    def test_values_survive_a_new_lab_and_edits_by_hand_are_read(self):
        Settings(self.file).update({'page_size': 120, 'sound_fade_in': 1.25, 'font_sample': '  Big   Cell  '})
        again = Settings(self.file)                             # the lab started again
        self.assertEqual((again.get('page_size'), again.get('sound_fade_in'), again.get('font_sample')), (120, 1.25, 'Big Cell'))
        data = self.stored(); data['page_size'] = 48
        self.file.write_text(json.dumps(data), encoding='utf-8')
        os.utime(self.file, ns=(time.time_ns(), time.time_ns() + 10_000_000))   # a different stamp, like a later edit
        self.assertEqual(again.get('page_size'), 48)            # read again when the file changes on disk

    def test_values_that_do_not_fit_are_refused_and_nothing_is_stored(self):
        s = Settings(self.file)
        s.update({'ui_size': 110})
        bad = [({'ui_size': 130}, 'Interface size: choose 90%, 100%, 110% or 125%.'),
               ({'ui_size': True}, 'Interface size: choose'), ({'open_browser': 'yes'}, 'turn it on or off'),
               ({'open_browser': 1}, 'turn it on or off'), ({'sound_fade_in': 10.5}, 'Fade in: use 0 to 10 s.'),
               ({'sound_fade_in': -1}, 'use 0 to 10 s'), ({'sound_fade_in': float('nan')}, 'enter a number'),
               ({'sound_fade_in': 'soon'}, 'enter a number'), ({'sound_fade_in': [1]}, 'enter a number'),
               ({'font_sample': 'x' * 41}, 'at most 40 characters'), ({'font_sample': 5}, 'enter some text'),
               ({'planner_model': 'qwen3; rm -rf'}, 'characters a model name never has'), ({'language': 'xx'}, 'Language: choose'),
               ({'language': None}, 'Language: choose'), ({'nope': 1}, 'no setting called'), ({'clear_caches': True}, 'no setting called'),
               ({}, 'Send the settings'), ([('ui_size', 90)], 'Send the settings'), ({'accent': 'pink', 'page_size': 50}, 'Assets per page: choose 48, 72 or 120.')]
        for change, words in bad:
            with self.assertRaises(ValueError, msg=change) as caught: s.update(change)
            self.assertIn(words, str(caught.exception), change)
        self.assertEqual(s.values()['accent'], 'teal')         # the good half of a bad request is not stored either
        self.assertEqual({k: v for k, v in self.stored().items() if k != '//'}, {'ui_size': 110})

    def test_values_are_cleaned_to_their_own_type(self):
        s = Settings(self.file)
        v = s.update({'page_size': '120', 'sound_fade_out': '2.53', 'sound_fade_in': 3})
        self.assertEqual((v['page_size'], v['sound_fade_out'], v['sound_fade_in']), (120, 2.55, 3.0))   # a <select> sends "120"; steps of 0.05
        self.assertIsInstance(v['sound_fade_in'], float)
        self.assertEqual(s.update({'font_sample': 'a\u202eb\tc'})['font_sample'], 'a b c')                # no text-direction or control characters
        self.assertEqual(s.update({'font_sample': '   '})['font_sample'], 'Cellmate')                    # empty text is the default

    def test_a_damaged_file_is_kept_and_the_defaults_are_used(self):
        for text in ('{"ui_size": 125,', '[1, 2]', '"text"', '', 'null', b'\xff\xfe\x00garbage'):
            with self.subTest(text=text):
                (self.file.write_bytes if isinstance(text, bytes) else lambda t: self.file.write_text(t, encoding='utf-8'))(text)
                bad = self.file.with_name('settings.json.bad'); bad.unlink(missing_ok=True)
                s = Settings(self.file)
                self.assertEqual(s.values()['ui_size'], 100)
                self.assertTrue(s.damaged)
                self.assertEqual(bad.read_bytes(), self.file.read_bytes())   # kept for the player to look at
                s.update({'accent': 'lime'})                               # the next change writes a good file
                self.assertFalse(s.damaged)
                self.assertEqual(Settings(self.file).values()['accent'], 'lime')
                self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['accent'], 'lime')

    def test_wrong_values_in_the_file_fall_back_one_by_one_and_unknown_keys_stay(self):
        self.file.write_text('\ufeff' + json.dumps({'ui_size': 'huge', 'accent': 'pink', 'page_size': 120, 'sound_fade_in': 99, 'open_browser': 'no',
                                                    'newer_setting': {'from': 'a newer lab'}, 'language': 'es'}), encoding='utf-8')
        s = Settings(self.file)
        v = s.values()
        self.assertEqual((v['ui_size'], v['accent'], v['page_size'], v['sound_fade_in'], v['open_browser'], v['language']), (100, 'pink', 120, 0.0, True, 'es'))
        self.assertFalse(s.damaged)
        self.assertEqual(s.changed(), ['accent', 'language', 'page_size'])
        s.update({'compact_cards': True})
        data = self.stored()
        self.assertEqual(data['newer_setting'], {'from': 'a newer lab'})   # an older lab never drops a newer one's settings
        self.assertNotIn('ui_size', data); self.assertNotIn('sound_fade_in', data)   # values that never fit are dropped
        self.assertEqual((data['accent'], data['language'], data['compact_cards']), ('pink', 'es', True))

    def test_reset_a_category_or_everything(self):
        s = Settings(self.file)
        s.update({'ui_size': 90, 'accent': 'blue', 'history_states': 200, 'language': 'fr'})
        appearance = [k for k, spec in BY_KEY.items() if spec['category'] == 'appearance']
        v = s.reset(appearance)
        self.assertEqual((v['ui_size'], v['accent'], v['history_states'], v['language']), (100, 'teal', 200, 'fr'))
        with self.assertRaises(ValueError): s.reset(['nope'])
        self.file.write_text(json.dumps(dict(self.stored(), extra=1)), encoding='utf-8')
        v = s.reset()
        self.assertEqual(v, {k: spec['default'] for k, spec in BY_KEY.items()})
        self.assertEqual({k for k in self.stored() if k != '//'}, {'extra'})

    def test_the_launcher_writing_the_language_keeps_the_rest(self):
        """The launcher's language menu rewrites the file with its own JSON writer (one line, no note)."""
        s = Settings(self.file)
        s.update({'open_browser': False, 'sound_fade_in': 0.5})
        data = self.stored(); data['language'] = 'pt'
        self.file.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        os.utime(self.file, ns=(time.time_ns(), time.time_ns() + 10_000_000))
        self.assertEqual((s.get('language'), s.get('open_browser'), s.get('sound_fade_in')), ('pt', False, 0.5))

    def test_a_lab_without_settings_uses_the_defaults(self):
        class Bare: pass
        self.assertEqual(labsettings.value(Bare(), 'drawings'), 2)


# ----- the HTTP API ----------------------------------------------------------------------------------------------------
class ApiTests(Temp):
    def setUp(self):
        super().setUp()
        self.game = make_game(self.root)
        self.lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [], settings_file=self.file)
        self.lab.audio_tools = self.root / 'isolated-helper'
        self.http = Http(self.lab)

    def tearDown(self):
        self.http.close()
        super().tearDown()

    def test_shutdown_refuses_an_active_audio_job_and_keeps_the_server_alive(self):
        done = threading.Event()
        jobs = self.lab._sound()['jobs']
        job = jobs.start('tools', lambda progress, cancel: (done.wait(10), {'ok': True})[1])
        try:
            status, result = self.http.post('/api/shutdown', {})
            self.assertEqual(status, 400)
            self.assertIn('Finish or cancel', result['error'])
            self.assertEqual(self.http.get('/api/health')[0], 200)
        finally:
            done.set()
            wait(lambda: jobs.get(job['id'])['state'] != 'running')

    def test_shutdown_refuses_an_active_drawing(self):
        with patch.object(design.Designer, 'status', return_value={'job': {'state': 'running'}}):
            status, result = self.http.post('/api/shutdown', {})
            self.assertEqual(status, 400)
            self.assertIn('finish first', result['error'])
            self.assertEqual(self.http.get('/api/health')[0], 200)

    def test_pending_build_recovery_blocks_writes_but_still_allows_clean_exit(self):
        self.lab._build_recovery_required = True
        self.assertEqual(self.http.post('/api/settings', {'values': {'page_size': 60}})[0], 400)
        status, result = self.http.post('/api/shutdown', {})
        self.assertEqual((status, result), (200, {'stopping': True}))
        self.http.thread.join(5)
        self.assertFalse(self.http.thread.is_alive())

    def test_the_settings_window_gets_everything_in_one_answer(self):
        status, d = self.http.get('/api/settings')
        self.assertEqual(status, 200)
        self.assertEqual([c['id'] for c in d['categories']], ['general', 'appearance', 'editor', 'theme', 'text', 'sound', 'library', 'storage', 'about'])
        self.assertEqual(d['schema'][0]['key'], 'language')
        self.assertEqual(d['values'], self.lab.settings.values())
        self.assertEqual((d['changed'], d['damaged'], d['file']), ([], False, str(self.file)))
        self.assertTrue(d['privacy'] and d['privacy_note'])
        self.assertEqual(d['about']['version'], lab_core.VERSION)
        self.assertEqual(self.http.get('/api/status')[1]['settings'], self.lab.settings.values())   # the page has them from its first request

    def test_changes_are_checked_saved_and_survive_a_restart(self):
        status, d = self.http.post('/api/settings', {'values': {'ui_size': 125, 'language': 'es'}})
        self.assertEqual(status, 200)
        self.assertEqual((d['values']['ui_size'], d['values']['language'], d['changed']), (125, 'es', ['language', 'ui_size']))
        for change, words in (({'values': {'ui_size': 130}}, 'Interface size: choose'), ({'values': {'sound_fade_out': 60}}, 'Fade out: use 0 to 10 s.'),
                              ({'values': {'page_size': -5}}, 'Assets per page'), ({'values': {'nope': 1}}, 'no setting called'),
                              ({'values': ['ui_size']}, 'Send the settings'), ({'values': {}}, 'Send the settings'), ({'reset': 'some'}, 'Send reset as'),
                              ({'reset': [1]}, 'Send reset as'), ({'reset': ['nope']}, 'no setting called')):
            status, answer = self.http.post('/api/settings', change)
            self.assertEqual(status, 400, change)
            self.assertIn(words, answer['error'], change)
        self.assertEqual(self.http.post('/api/settings', [1, 2])[0], 400)
        status, answer = self.http.post('/api/settings', {'values': {'ui_size': 90}}, token=False)
        self.assertEqual((status, answer['error']), (400, 'Invalid session token; reload the editor.'))
        self.assertEqual(self.lab.settings.get('ui_size'), 125)          # nothing above changed it
        # A new lab (the lab started again) on the same file.
        self.http.close()
        lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [], settings_file=self.file)
        self.http = Http(lab)
        values = self.http.get('/api/settings')[1]['values']
        self.assertEqual((values['ui_size'], values['language']), (125, 'es'))
        status, d = self.http.post('/api/settings', {'reset': ['ui_size']})
        self.assertEqual((d['values']['ui_size'], d['values']['language']), (100, 'es'))
        self.assertEqual(self.http.post('/api/settings', {'reset': 'all'})[1]['changed'], [])

    def test_a_damaged_file_is_reported_to_the_window(self):
        self.file.write_text('{oops', encoding='utf-8')
        d = self.http.get('/api/settings')[1]
        self.assertTrue(d['damaged'])
        self.assertEqual(d['values']['ui_size'], 100)
        self.assertTrue(self.file.with_name('settings.json.bad').is_file())
        self.assertFalse(self.http.post('/api/settings', {'values': {'ui_size': 90}})[1].get('damaged'))
        self.assertFalse(self.http.get('/api/settings')[1]['damaged'])

    def test_unknown_actions_and_folders_are_refused(self):
        self.assertEqual(self.http.post('/api/settings-action', {'id': 'format_c'})[1]['error'], 'Unknown settings action.')
        self.assertEqual(self.http.post('/api/settings-action', {'id': 'open_folder', 'folder': '..'})[1]['error'], 'Unknown folder.')

    def test_usage_notices_and_about(self):
        status, u = self.http.get('/api/settings-usage')
        self.assertEqual(status, 200)
        self.assertEqual(u['workspace']['path'], str(self.lab.root))
        self.assertEqual(set(u['workspace']['parts']), {'revisions', 'originals', 'builds', 'designs', 'audio', 'audio-sources', 'fonts'})
        self.assertEqual((u['builds']['count'], u['builds']['keep']), (0, 10))
        self.assertFalse(u['helper']['installed'])
        self.assertEqual(u['data']['path'], str(self.root))
        about = self.http.get('/api/settings')[1]['about']
        self.assertEqual({f['id'] for f in about['folders']} >= {'data', 'workspace', 'ledgers', 'runtime', 'app'}, True)
        if about['notices']:
            files = self.http.get('/api/notices')[1]['files']
            self.assertIn('THIRD-PARTY-NOTICES.md', [f['name'] for f in files])


# ----- what the settings do on the server ------------------------------------------------------------------------------
class GeneralTests(Temp):
    def test_open_browser(self):
        lab = Lab(make_game(self.root), self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [], settings_file=self.file)
        opened = []
        self.assertTrue(lab_server.open_page(lab, 'http://127.0.0.1:1', opener=opened.append))
        self.assertFalse(lab_server.open_page(lab, 'http://127.0.0.1:2', no_browser=True, opener=opened.append))
        lab.settings.update({'open_browser': False})
        self.assertFalse(lab_server.open_page(lab, 'http://127.0.0.1:3', opener=opened.append))
        self.assertEqual(opened, ['http://127.0.0.1:1'])

    def test_the_server_reads_the_data_home_settings_file(self):
        """lab_server.main() passes <data home>/settings.json, the file the launcher reads, whatever the workspace."""
        src = (ROOT / 'src' / 'lab_server.py').read_text(encoding='utf-8')
        self.assertIn("settings_file=data_home() / 'settings.json'", src)
        with patch.dict(os.environ, ASSET_LAB_HOME=str(self.root / 'home')):
            self.assertEqual(lab_core.data_home() / 'settings.json', self.root / 'home' / 'settings.json')


class LibraryTests(Temp):
    def test_assets_per_page(self):
        game = make_game(self.root)
        for i in range(130): (game / 'assets' / f'tile-{i:03d}.png').write_bytes(png(Image.new('RGBA', (8, 8), (i, 0, 0, 255))))
        lab = Lab(game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [], settings_file=self.file)
        http = Http(lab)
        try:
            page = lambda **q: http.get('/api/assets?' + '&'.join(f'{k}={v}' for k, v in dict(kind='visual', q='tile', **q).items()))[1]
            d = page()
            self.assertEqual((len(d['items']), d['limit'], d['total']), (72, 72, 130))
            lab.settings.update({'page_size': 48})
            d = page()
            self.assertEqual((len(d['items']), d['limit']), (48, 48))
            self.assertEqual(len(page(offset=96)['items']), 130 - 96)
            lab.settings.update({'page_size': 120})
            self.assertEqual(len(page()['items']), 120)
            self.assertEqual(len(page(limit=10)['items']), 10)           # a caller that asks for a size gets it
        finally: http.close()


class StorageTests(Temp):
    def setUp(self):
        super().setUp()
        self.game = make_game(self.root)
        self.lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [], settings_file=self.file)
        self.key = next(r['key'] for r in self.lab.catalog.rows.values() if r['name'] == 'bg2.png')

    def builds(self): return sorted(d.name for d in (self.lab.root / 'builds').iterdir())

    def build(self, n=1):
        made = []
        for i in range(n):
            self.lab.save(self.key, png(Image.new('RGBA', (64, 64), (i * 9 % 255, 40, 90, 255))), self.lab.revision(self.key))
            made.append(self.lab.build()['id'])
            time.sleep(0.01)
        return made

    def test_mod_builds_to_keep(self):
        first = self.build(1)[0]
        self.lab.install(first)                                   # the installed build always stays
        self.lab.settings.update({'keep_builds': 25})
        made = self.build(8)
        self.assertEqual(len(self.builds()), 9)
        http = Http(self.lab)
        try:
            d = http.post('/api/settings', {'values': {'keep_builds': 5}})[1]
            self.assertEqual(d['pruned']['removed'], 3)           # the old ones go as soon as fewer are kept (not the installed one)
            self.assertGreater(d['pruned']['freed'], 0)
        finally: http.close()
        self.assertEqual(self.builds(), sorted(made[-5:] + [first]))
        self.lab.settings.update({'keep_builds': 0})               # All: nothing is deleted any more
        more = self.build(3)
        self.assertEqual(len(self.builds()), 9)
        self.lab.settings.update({'keep_builds': 5})
        last = self.build(1)                                      # every build prunes the rest too
        self.assertEqual(self.builds(), sorted(last + more + made[-1:] + [first]))
        self.lab.restore()

    def test_clear_caches(self):
        self.lab.thumbnail(self.key)
        self.assertTrue(self.lab._thumbs)
        faces = self.lab.root.parent / 'typefaces.json'
        faces.write_text('{"fonts": []}', encoding='utf-8')
        self.lab._typefaces = ['cached']
        sources = self.lab.root / 'audio-sources'
        chosen = self.lab.audio_source_add('loop.wav', encode(tone(440, 1.0), 44100, 'WAV'))
        self.assertTrue((sources / (chosen['id'] + '.json')).is_file())
        done = labsettings.run_action(self.lab, 'clear_caches')
        self.assertEqual(done['thumbnails'], 1)
        self.assertEqual((self.lab._thumbs, self.lab._typefaces, faces.exists()), ({}, None, False))
        self.assertFalse((sources / (chosen['id'] + '.json')).exists())   # a sound chosen but never saved
        self.assertGreater(done['freed'], 0)
        self.assertIn('Caches cleared: 1 thumbnail', done['message'])

    @unittest.skipUnless(os.name == 'nt', 'File Explorer is on Windows')
    def test_open_the_workspace_and_the_folders(self):
        opened = []
        with patch.object(labsettings.os, 'startfile', opened.append):   # nothing really opens
            self.assertEqual(labsettings.run_action(self.lab, 'open_workspace')['path'], str(self.lab.root))
            labsettings.run_action(self.lab, 'open_folder', {'folder': 'data'})
            with self.assertRaises(ValueError): labsettings.run_action(self.lab, 'open_folder', {'folder': 'C:\\Windows'})
        self.assertEqual(opened, [str(self.lab.root), str(self.root)])

    def test_the_link_helper_can_be_removed_and_updated(self):
        tools = self.root / 'tools'
        self.lab.audio_tools = tools
        self.assertIn('not installed', labsettings.run_action(self.lab, 'remove_helper')['message'])
        with self.assertRaisesRegex(ValueError, 'not installed'): labsettings.run_action(self.lab, 'update_helper')
        (tools / 'yt-dlp').mkdir(parents=True)
        (tools / 'yt-dlp' / 'yt-dlp.exe').write_bytes(b'x' * 5000)
        (tools / 'deno.exe').write_bytes(b'y' * 3000)
        self.assertEqual(labsettings.helper_status(self.lab)['bytes'], 8000)
        with patch.object(self.lab._sound()['jobs'], 'running', side_effect=lambda kind: dict(id='j') if kind == 'update' else None):
            with self.assertRaisesRegex(ValueError, 'busy'): labsettings.run_action(self.lab, 'remove_helper')
        with patch.object(audio, 'helper_ready', return_value=True), patch.object(self.lab, 'audio_tools_update', return_value=dict(id='u1', state='running')) as update:
            self.assertEqual(labsettings.run_action(self.lab, 'update_helper')['job']['id'], 'u1')
        update.assert_called_once()
        done = labsettings.run_action(self.lab, 'remove_helper')
        self.assertEqual((done['removed'], done['freed']), (True, 8000))
        self.assertFalse(tools.exists())
        self.assertEqual(list(self.root.glob('tools.removing-*')), [])

    def test_privacy_note_names_every_address_in_the_code(self):
        listed = {h for p in labsettings.PRIVACY for h in p['hosts']}
        local = {'127.0.0.1', 'localhost', 'www.w3.org'}   # this PC, and the SVG namespace (a name, never fetched)
        found = {}
        files = list((ROOT / 'src').rglob('*.py')) + list((ROOT / 'src' / 'web').rglob('*.js')) + list((ROOT / 'src' / 'web').glob('*.html'))
        files += [ROOT / 'launcher' / 'AssetLab.cs', ROOT / 'bootstrap.json']
        for f in files:
            for host in re.findall(r'https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)', f.read_text(encoding='utf-8', errors='replace')):
                if host not in local: found.setdefault(host, f.name)
        missing = {h: f for h, f in found.items() if h not in listed}
        self.assertEqual(missing, {}, 'web addresses the privacy note does not name')
        self.assertTrue({'api.github.com', 'github.com', 'www.python.org', 'files.pythonhosted.org', 'huggingface.co'} <= set(found))


class SoundTests(Temp):
    def setUp(self):
        super().setUp()
        self.game = audio_game(self.root)
        self.lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [], settings_file=self.file)
        self.lab.audio_tools = self.root / 'no-tools'
        self.k = next(r['key'] for r in self.lab.catalog.rows.values() if r['name'] == 'st1.ogg')

    def tearDown(self):
        audio._TEST_ALLOW = frozenset()
        super().tearDown()

    def choose(self, seconds=3.0, rate=44100, name='song.wav', noise=False):
        y = np.random.default_rng(3).uniform(-0.3, 0.3, (int(seconds * rate), 2)).astype(np.float32) if noise else tone(523, seconds, rate, 0.2, 2)
        return self.lab.audio_source_add(name, encode(y, rate, 'WAV'))

    def test_sound_quality(self):
        chosen = self.choose(4.0, noise=True)
        sizes = {}
        for q in ('small', 'standard', 'high', 'best'):
            self.lab.settings.update({'sound_quality': q})
            info = self.lab.audio_apply(self.k, chosen['id'], dict(quality=0.1))   # the editor's own value is ignored
            self.assertEqual(info['edit']['settings']['quality'], labsettings.QUALITY[q])
            sizes[q] = len(self.lab.audio_file(self.k))
        self.assertLess(sizes['small'], sizes['standard']); self.assertLess(sizes['standard'], sizes['high']); self.assertLess(sizes['high'], sizes['best'])

    def test_match_and_fades_are_the_defaults_for_what_an_edit_leaves_out(self):
        chosen = self.choose(3.0)
        _, report = self.lab.audio_preview(self.k, chosen['id'], {}, info_only=True)
        self.assertIsNotNone(report['match_db'])                    # on by default
        self.lab.settings.update({'sound_match': False, 'sound_fade_in': 0.5, 'sound_fade_out': 1.0})
        _, report = self.lab.audio_preview(self.k, chosen['id'], {}, info_only=True)
        self.assertIsNone(report['match_db'])
        self.assertEqual((report['settings']['fade_in'], report['settings']['fade_out']), (0.5, 1.0))
        samples, rate, _ = self.lab.audio_render(self.k, chosen['id'], {})
        level = lambda a, b: float(np.sqrt(np.mean(samples[int(a * rate):int(b * rate)] ** 2)))
        self.assertLess(level(0, 0.02), 0.01 * level(1.0, 1.5))      # starts silent
        self.assertLess(level(2.98, 3.0), 0.01 * level(1.0, 1.5))    # ends silent
        _, plain = self.lab.audio_preview(self.k, chosen['id'], dict(match=True, fade_in=0, fade_out=0), info_only=True)
        self.assertIsNotNone(plain['match_db'])                     # what the editor sends wins
        self.assertEqual((plain['settings']['fade_in'], plain['settings']['fade_out']), (0.0, 0.0))

    def test_longest_sound(self):
        self.assertEqual(self.lab.audio_info(self.k)['limits']['seconds'], 20 * 60)
        self.lab.settings.update({'sound_max_minutes': 1})
        self.assertEqual(self.lab.audio_info(self.k)['limits']['seconds'], 60)
        chosen = self.choose(70.0, 8000, 'long.wav')
        self.assertAlmostEqual(chosen['duration'], 60.0, delta=0.01)
        self.assertAlmostEqual(chosen['cut'], 70.0, delta=0.1)       # the editor says only the first minute is used
        self.lab.settings.update({'sound_max_minutes': 2})
        self.assertAlmostEqual(self.choose(70.0, 8000, 'long.wav')['duration'], 70.0, delta=0.01)

    def test_page_links(self):
        s = Serve({'/watch': (200, {'Content-Type': 'text/html'}, b'<html>a video page</html>'), '/song.ogg': (200, {'Content-Type': 'audio/ogg'}, encode(tone(440, 1.0), 44100))})
        self.addCleanup(s.close)
        audio._TEST_ALLOW = frozenset({f'127.0.0.1:{s.port}'})
        def run(path):
            job = self.lab.audio_link(s.url + path)
            return wait(lambda: (lambda j: j if j['state'] != 'running' else None)(self.lab.audio_job(job['id'])))
        self.assertEqual(run('/watch')['code'], 'needs_helper')             # on: pages go to the link helper (not installed here)
        self.lab.settings.update({'page_links': False})
        job = run('/watch')
        self.assertEqual(job['code'], 'pages_off')
        self.assertIn('page links are turned off in Settings', job['error'])
        self.assertEqual(run('/song.ogg')['state'], 'done')                 # direct links to sound files still work


class ThemeAiTests(Base):
    """Theme AI & drawing: the planner model, the offers after a mood, the image model's runs."""
    def setUp(self):
        super().setUp()
        self.designer = Designer(self.lab, self.themes)
        self.themes.designer = self.designer
        self.key = next(r['key'] for r in self.rows('virus-pale') if r.get('primary', True))

    def test_planner_model(self):
        seen = []
        def fake(path, payload=None, timeout=0):
            seen.append(payload['model']); return {'message': {'content': '{"reply":"ok","operations":[{"targets":["cells"],"color":"#ff0000"}]}'}}
        installed = [dict(name='best:latest', thinking=False, size=5e9), dict(name='small:4b', thinking=False, size=2e9)]
        with patch.object(theme, 'models', return_value=installed), patch.object(theme, 'ollama', side_effect=fake):
            self.themes.plan_request('molten crimson cells')
            self.lab.settings.update({'planner_model': 'small:4b'})
            self.themes.plan_request('molten crimson cells')
            self.themes.plan_request('molten crimson cells', model='best:latest')   # the drawer's own pick wins
            self.lab.settings.update({'planner_model': 'gone:7b'})              # not installed: the best one installed
            self.themes.plan_request('molten crimson cells')
        self.assertEqual(seen, ['best:latest', 'small:4b', 'best:latest', 'best:latest'])

    def test_offer_title_redraws(self):
        with patch.object(design.runtime, 'missing', return_value=[]), patch.object(theme, 'models', return_value=[]), \
                patch.object(self.designer, 'title_rows', return_value=[self.lab.row(self.key)]):
            on = self.themes.chat('make everything spooky')
            self.lab.settings.update({'offer_redraws': False})
            off = self.themes.chat('make everything spooky')
        self.assertTrue(on['design']['optional'])
        self.assertIn('Redesign the game title', on['design']['prompt'])
        self.assertTrue(off['plan']['operations'])                    # the palette is still shaded
        self.assertNotIn('design', off)

    def fake_sd(self, calls):
        class FakeSD:
            def __init__(self, args, **kw):
                calls.append(args)
                out, n = args[args.index('-o') + 1], int(args[args.index('-b') + 1])
                w, h = int(args[args.index('-W') + 1]), int(args[args.index('-H') + 1])
                for i in range(n):
                    im = Image.new('RGB', (w, h), (255, 0, 255)); im.paste((220, 20, 30), (w // 4, h // 4, 3 * w // 4, 3 * h // 4)); im.save(out % i)
                self.stdout, self.returncode, self.pid = iter(['sampling |  1/4 - 1.2s/it\n']), 0, 4242
            def wait(self, timeout=None): return 0
        return FakeSD

    def draw(self, calls, unloads, count=None):
        tool = self.root / 'sd' / 'sd-cli.exe'
        with patch.object(design.runtime, 'missing', return_value=[]), patch.object(design.runtime, 'path', lambda name: tool), \
                patch.object(design.subprocess, 'Popen', self.fake_sd(calls)), patch.object(design.runtime.OLLAMA, 'unload', lambda: unloads.append(1)):
            job = self.designer.start('make it spooky', [self.key], count)
            return wait(lambda: self.designer.jobs[job['id']] if self.designer.jobs[job['id']]['state'] != 'running' else None)

    def test_drawings_quality_and_freeing_the_graphics_card(self):
        calls, unloads = [], []
        job = self.draw(calls, unloads)
        self.assertEqual((job['state'], job['total'], len(job['variants'])), ('complete', 2, 2))   # the default: 2 drawings
        arg = lambda name: calls[-1][calls[-1].index(name) + 1]
        self.assertEqual((arg('--steps'), arg('-b'), len(unloads)), ('4', '2', 1))
        self.lab.settings.update({'drawings': 3, 'drawing_quality': 'detailed', 'free_gpu': False})
        job = self.draw(calls, unloads)
        self.assertEqual((job['total'], len(job['variants']), arg('--steps'), arg('-b'), len(unloads)), (3, 3, '8', '3', 1))   # no unload this time
        self.lab.settings.update({'drawing_quality': 'balanced'})
        job = self.draw(calls, unloads, count=1)                       # a request that names a count keeps it
        self.assertEqual((job['total'], arg('--steps')), (1, '6'))

    def test_the_http_endpoint_draws_the_setting_s_count(self):
        self.lab.settings.update({'drawings': 4})
        calls = []
        tool = self.root / 'sd' / 'sd-cli.exe'
        with patch.object(design.runtime, 'missing', return_value=[]), patch.object(design.runtime, 'path', lambda name: tool), \
                patch.object(design.subprocess, 'Popen', self.fake_sd(calls)), patch.object(design.runtime.OLLAMA, 'unload', lambda: None), \
                patch.object(theme, 'models', return_value=[]):
            http = Http(self.lab)
            try:
                status, job = http.post('/api/design-start', {'prompt': 'make it spooky', 'only': [self.key]})
                self.assertEqual((status, job['total']), (200, 4))
                wait(lambda: http.get('/api/design-job?job=' + job['id'])[1]['state'] != 'running')
            finally: http.close()

    def test_the_art_direction_frees_the_graphics_card_too(self):
        seen = []
        def fake(path, payload=None, timeout=0):
            seen.append(payload['keep_alive']); return {'message': {'content': 'Cracked bone and dripping red ink over a pale glow, sharp edges.'}}
        with patch.object(theme, 'models', return_value=[dict(name='m', thinking=False, size=5e9)]), patch.object(theme, 'ollama', side_effect=fake):
            self.themes.art_direction('spooky', 'a game sprite: virus')
            self.lab.settings.update({'free_gpu': False})
            self.themes.art_direction('spooky', 'a game sprite: virus')
        self.assertEqual(seen, [0, '10m'])


class TextTests(Base):
    maker = staticmethod(font_game)

    def test_offer_matching_text_styles_and_the_example_text(self):
        with patch.object(self.lab, 'typefaces', return_value=[]), patch.object(theme, 'models', return_value=[]):
            on = self.themes.chat('halloween theme')
            self.lab.settings.update({'font_sample': 'ABBA'})
            named = self.themes.chat('halloween theme')
            asked = self.themes.chat('gold player names')
            self.lab.settings.update({'offer_text_styles': False})
            off = self.themes.chat('halloween theme')
            still = self.themes.chat('gold player names')               # asking for text yourself still works
        self.assertEqual((on['font']['offer'], on['font']['text']), (True, 'Cellmate'))
        self.assertEqual(named['font']['text'], 'ABBA')
        self.assertEqual(asked['font']['text'], 'ABBA')
        self.assertTrue(off['plan']['operations'])
        self.assertNotIn('font', off)
        self.assertEqual(still['font']['look'], 'Gold')


if __name__ == '__main__':
    unittest.main()
