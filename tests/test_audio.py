"""The sound editor: decoding, the edit (trim, fades, gain, loudness match, peak safety, resampling), OGG output, the
mod build/install/restore of sounds inside assets.zip and loose, link safety, direct downloads, the link helper
(a stand-in yt-dlp) and its install with checksums and resumed downloads. Everything runs offline on this PC."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave
import zipfile
import numpy as np
from PIL import Image
import soundfile as sf
import audio
from audio import AudioError
from fixtures import make_game
from lab_core import Lab, file_hash, png
from lab_server import server_for


def tone(freq=440.0, seconds=1.0, rate=44100, amp=0.25, channels=1):
    t = np.arange(int(round(seconds * rate))) / rate
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(x[:, None], channels, axis=1)


def encode(samples, rate, fmt='OGG', subtype=None):
    out = io.BytesIO()
    with sf.SoundFile(out, 'w', rate, samples.shape[1], format=fmt, subtype=subtype or {'OGG': 'VORBIS', 'WAV': 'PCM_16', 'FLAC': 'PCM_16', 'MP3': 'MPEG_LAYER_III'}[fmt]) as f:
        for i in range(0, len(samples), 16384): f.write(samples[i:i + 16384])
    return out.getvalue()


def lufs(samples, rate, block=None):
    return audio.loudness(audio.kweighted(samples, rate), rate, block or audio.block_for(len(samples) / rate))


def audio_game(root):
    """The QA game plus an assets.zip like the real one (stored OGG music and effects, a PNG and another file) and the
    loose logo.ogg."""
    game = make_game(root)
    assets = game / 'assets'
    with zipfile.ZipFile(assets / 'assets.zip', 'w') as z:
        z.writestr(zipfile.ZipInfo('st1.ogg', (2020, 1, 2, 3, 4, 6)), encode(tone(220, 6.0, 44100, 0.3, 2), 44100))
        z.writestr(zipfile.ZipInfo('click.ogg', (2020, 1, 2, 3, 4, 6)), encode(np.concatenate([tone(1800, 0.05, 44100, 0.6), np.zeros((4410, 1), np.float32)]), 44100))
        z.writestr('snd-icon.png', png(Image.new('RGBA', (16, 16), '#3366cc')))
        z.writestr('readme.txt', b'keep me exactly' * 50, compress_type=zipfile.ZIP_DEFLATED)
    (assets / 'logo.ogg').write_bytes(encode(tone(330, 1.5, 48000, 0.2, 2), 48000))
    return game


class Serve:
    """A small local web server for the link tests: routes map paths to (status, headers, body)."""
    def __init__(self, routes):
        self.routes, self.seen = routes, []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                owner.seen.append((self.path, dict(self.headers)))
                status, headers, body = owner.routes.get(self.path, (404, {}, b'missing'))
                if callable(body): body = body(self)
                if body is None: return
                rng = self.headers.get('Range')
                if rng and headers.get('X-Ranges') == 'yes':
                    start = int(rng.split('=')[1].split('-')[0])
                    status, body = 206, body[start:]
                self.send_response(status)
                for k, v in headers.items():
                    if k != 'X-Ranges': self.send_header(k, v)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except OSError: pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_port
        self.url = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


FAKE_YTDLP = textwrap.dedent('''
    import json, os, sys, time
    args = sys.argv[1:]
    mode = os.environ.get('FAKE_YTDLP_MODE', 'ok')
    log = os.environ.get('FAKE_YTDLP_LOG')
    if log:
        with open(log, 'w', encoding='utf-8') as f: json.dump(args, f)
    if args == ['-U']:
        print('Latest version: stable@2026.09.01 from yt-dlp/yt-dlp')
        print('Updated yt-dlp to stable@2026.09.01 from yt-dlp/yt-dlp')
        sys.exit(0)
    out = args[args.index('--output') + 1]
    if mode == 'slow':
        time.sleep(60)
    if mode == 'outdated':
        print('ERROR: [youtube] abc123: Unable to extract nsig function; please report this issue on https://github.com/yt-dlp/yt-dlp/issues, confirm you are on the latest version using yt-dlp -U', file=sys.stderr)
        sys.exit(1)
    if mode == 'signin':
        print("ERROR: [youtube] abc123: Sign in to confirm your age. This video may be inappropriate for some users.", file=sys.stderr)
        sys.exit(1)
    if mode == 'unsupported':
        print('ERROR: Unsupported URL: https://example.org/nothing', file=sys.stderr)
        sys.exit(1)
    if mode == 'filter':
        print('[download] Long mix does not pass filter (!is_live & duration <=? 1800), skipping ..')
        sys.exit(0)
    if mode == 'web':   # like yt-dlp's generic extractor: fetch the page through --proxy, follow its redirects and its <audio src>
        import re, urllib.parse, urllib.request
        proxy = args[args.index('--proxy') + 1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        try:
            page = opener.open(args[-1], timeout=10)
            body = page.read()
            if not body.startswith(b'OggS'): body = opener.open(urllib.parse.urljoin(page.geturl(), re.search(rb'src="([^"]+)"', body).group(1).decode()), timeout=10).read()
        except Exception as exc:
            print('ERROR: [generic] Unable to download webpage: ' + str(exc), file=sys.stderr)
            sys.exit(1)
        print('LABINFO ' + json.dumps(dict(id='page1', title='AC/DC - Back in Black' + chr(0x202e), license='CC BY 4.0 / ' + 'x' * 5000, webpage_url='javascript:alert(1)')))
        path = out.replace('%(id)s', 'page1').replace('%(ext)s', 'ogg')
        with open(path, 'wb') as f: f.write(body)
        print('LABFILE ' + path)
        sys.exit(0)
    print('LABINFO ' + json.dumps(dict(id='abc123', title='Calm Loop', duration=2.0, license='Creative Commons Attribution license (reuse allowed)', webpage_url='https://www.youtube.com/watch?v=abc123')))
    total = 1000
    for done in (0, 250, 500, 1000):
        print(f'LABPROGRESS {done} {total} NA', flush=True)
    path = out.replace('%(id)s', 'abc123').replace('%(ext)s', 'ogg')
    with open(path, 'wb') as f, open(os.environ['FAKE_YTDLP_SOUND'], 'rb') as src: f.write(src.read())
    print('LABFILE ' + path)
''')


class Dsp(unittest.TestCase):
    def test_round_trip_through_ogg_and_other_formats(self):
        x = tone(440, 1.0, 44100, 0.3, 2)
        data = audio.encode_ogg(x, 44100, 0.5)
        self.assertEqual(data[:4], b'OggS')
        self.assertEqual(audio.probe(data), (44100, 2, 44100))
        y, rate = audio.decode(data)
        self.assertEqual((y.shape, rate, y.dtype), ((44100, 2), 44100, np.float32))
        self.assertAlmostEqual(lufs(y, rate), lufs(x, 44100), delta=0.3)
        self.assertGreater(np.corrcoef(x[1000:-1000, 0], y[1000:-1000, 0])[0, 1], 0.99)
        for fmt in ('WAV', 'FLAC', 'MP3'):
            y, rate = audio.decode(encode(x, 44100, fmt))
            self.assertEqual((y.shape[1], rate), (2, 44100))
            self.assertAlmostEqual(len(y) / 44100, 1.0, delta=0.06)   # MP3 pads a little
        with self.assertRaises(AudioError) as caught: audio.decode(b'<!doctype html><html></html>')
        self.assertEqual(caught.exception.code, 'not_audio')
        with self.assertRaises(AudioError) as caught: audio.decode(b'\x00\x00\x00\x18ftypM4A ' + bytes(200), tools=tempfile.gettempdir() + '/no-helper-here')
        self.assertEqual(caught.exception.code, 'needs_helper')       # an M4A without the link helper
        with self.assertRaises(AudioError): audio.decode(b'')

    def test_only_the_first_minutes_are_kept(self):
        info = {}
        y, rate = audio.decode(encode(tone(440, 5.0, 22050), 22050, 'WAV'), limit=2, info=info)
        self.assertEqual(len(y), 2 * 22050)
        self.assertAlmostEqual(info['cut'], 5.0, delta=0.1)

    def test_trim_is_sample_exact(self):
        x = tone(440, 3.0, 44100, 0.2, 2)
        y, report = audio.process(x, 44100, dict(start=0.5, end=1.25, match=False))
        self.assertEqual(len(y), int(round(1.25 * 44100)) - int(round(0.5 * 44100)))
        self.assertEqual(report['frames'], len(y))
        np.testing.assert_array_equal(y, x[22050:55125])    # nothing else changed

    def test_fades_are_smooth_and_reach_silence(self):
        x = np.full((44100, 1), 0.5, np.float32)
        y, _ = audio.process(x, 44100, dict(fade_in=0.25, fade_out=0.4, match=False))
        up, down = y[:11025, 0], y[-17640:, 0]
        self.assertEqual((y[0, 0], y[-1, 0]), (0.0, 0.0))
        self.assertTrue(np.all(np.diff(up) >= 0) and np.all(np.diff(down) <= 0))
        self.assertAlmostEqual(float(up[-1]), 0.5, places=5)
        self.assertTrue(np.all(y[11025:-17640, 0] == 0.5))
        # fades longer than the sound meet in the middle instead of overlapping
        s = audio.clean_settings(dict(fade_in=3, fade_out=1), 2.0)
        self.assertAlmostEqual(s['fade_in'] + s['fade_out'], 2.0, places=3)

    def test_gain_is_exact_in_db(self):
        x = tone(440, 1.0, 44100, 0.05)
        for db in (6.0, -12.5, 0.0):
            y, report = audio.process(x, 44100, dict(gain=db, match=False))
            ratio = float(np.abs(y).max() / np.abs(x).max())
            self.assertAlmostEqual(20 * math.log10(ratio), db, places=4)
            self.assertEqual((report['limited_db'], report['short_db']), (0.0, 0.0))

    def test_bs1770_reference_levels(self):
        self.assertAlmostEqual(lufs(tone(1000, 5, 48000, 1.0), 48000), -3.01, delta=0.05)          # full-scale 1 kHz, one channel
        self.assertAlmostEqual(lufs(tone(1000, 5, 48000, 0.1, 2), 48000), -20.0, delta=0.05)       # -20 dBFS in both channels
        quiet = np.concatenate([tone(1000, 5, 48000, 0.1), np.zeros((48000 * 10, 1), np.float32)])
        self.assertAlmostEqual(lufs(quiet, 48000), lufs(tone(1000, 5, 48000, 0.1), 48000), delta=0.2)   # silence is gated out
        self.assertEqual(audio.loudness(np.zeros(4800, np.float32), 48000), -math.inf)

    def test_loudness_match_lands_within_half_a_db(self):
        x = tone(440, 8.0, 44100, 0.02, 2)
        for target, block, settings in ((-16.0, 0.4, {}), (-20.0, 0.4, dict(fade_in=1.0, fade_out=2.0)), (-12.0, 0.1, dict(start=1, end=1.3))):
            y, report = audio.process(x, 44100, dict(settings, match=True), reference=dict(loudness=target, block=block), channels=2)
            self.assertAlmostEqual(lufs(y, 44100, block), target, delta=0.5)
            self.assertAlmostEqual(report['loudness'], target, delta=0.5)
        # volume on top of the match
        y, _ = audio.process(x, 44100, dict(match=True, gain=-6), reference=dict(loudness=-16.0, block=0.4), channels=2)
        self.assertAlmostEqual(lufs(y, 44100), -22.0, delta=0.5)
        # a peaky sound pushed to a loud target: the limiter softens the peaks, then the lost loudness is made up
        drum = tone(80, 4.0, 44100, 0.05)
        drum[::11025] = 0.2
        y, report = audio.process(drum, 44100, dict(match=True), reference=dict(loudness=-14.0, block=0.4), channels=1)
        self.assertLessEqual(float(np.abs(y).max()), audio.CEILING + 1e-6)
        self.assertEqual(report['short_db'], 0)
        self.assertGreater(report['limited_db'], 1)
        self.assertAlmostEqual(lufs(y, 44100), -14.0, delta=0.5)
        # far beyond what the limiter may do: turned down as a whole, and the report says how much
        drum[::11025] = 0.9
        y, report = audio.process(drum, 44100, dict(match=True), reference=dict(loudness=-14.0, block=0.4), channels=1)
        self.assertLessEqual(float(np.abs(y).max()), audio.CEILING + 1e-6)
        self.assertGreater(report['short_db'], 3)
        self.assertAlmostEqual(lufs(y, 44100), report['loudness'], delta=0.05)          # the report tells the truth
        self.assertAlmostEqual(report['loudness'], -14.0 - report['short_db'], delta=0.05)

    def test_peaks_never_cross_minus_one_dbfs(self):
        x = tone(220, 2.0, 44100, 0.8, 2)
        x[30000:30100] *= 1.2
        for gain in (0, 6, 24):
            y, report = audio.process(x, 44100, dict(gain=gain, match=False))
            self.assertLessEqual(float(np.abs(y).max()), audio.CEILING + 1e-6)
            self.assertAlmostEqual(report['peak_db'], -1.0, delta=0.05)
        _, report = audio.process(x, 44100, dict(gain=24, match=False))
        self.assertGreater(report['short_db'], 10)                    # far too loud: turned down as a whole
        self.assertLessEqual(report['limited_db'], 6.1)               # and the limiter stays gentle
        warn = audio.warnings(dict(duration=2.0, loudness=-20), report)
        self.assertTrue(any('quieter' in w for w in warn))

    def test_resampling_keeps_the_sound_and_drops_what_cannot_fit(self):
        for rate, new in ((96000, 48000), (88200, 44100), (32000, 44100), (11025, 22050), (50000, 48000)):
            x = tone(1000, 1.0, rate, 0.5)
            y = audio.resample(x, rate, new)
            self.assertEqual(len(y), new)
            ideal = tone(1000, 1.0, new, 0.5)
            self.assertLess(float(np.abs(y[2000:-2000] - ideal[2000:-2000]).max()), 1e-3, (rate, new))
        alias = audio.resample(tone(30000, 0.5, 96000, 0.5), 96000, 48000)
        self.assertLess(20 * math.log10(float(np.abs(alias[500:-500]).max()) + 1e-12), -60)
        self.assertEqual([audio.target_rate(r) for r in (8000, 22050, 32000, 44100, 48000, 88200, 96000, 192000)], [22050, 22050, 44100, 44100, 48000, 44100, 48000, 48000])
        y, rate = audio.normalize(np.repeat(tone(500, 0.5, 96000, 0.4), 6, axis=1), 96000)   # 5.1 at 96 kHz
        self.assertEqual((y.shape[1], rate, len(y)), (2, 48000, 24000))

    def test_channels_follow_the_game_sound(self):
        stereo = np.stack([tone(440, 0.5)[:, 0], np.zeros(22050, np.float32)], axis=1)
        mono = audio.to_channels(stereo, 1)
        self.assertEqual(mono.shape, (22050, 1))
        np.testing.assert_allclose(mono[:, 0], stereo[:, 0] / 2, atol=1e-7)
        self.assertEqual(audio.to_channels(tone(440, 0.5), 2).shape, (22050, 2))
        y, report = audio.process(stereo, 44100, dict(match=False), channels=1)
        self.assertEqual((y.shape[1], report['channels']), (1, 1))

    def test_waveform_and_preview_formats(self):
        x = tone(440, 2.0, 44100, 0.5, 2)
        p = audio.peaks(x, 300)
        self.assertEqual(len(p), 300)
        self.assertTrue(all(0 <= v <= 0.51 for v in p) and max(p) > 0.49)
        with wave.open(io.BytesIO(audio.wav(x, 44100))) as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes()), (2, 44100, 2, 88200))

    def test_settings_are_checked(self):
        self.assertEqual(audio.clean_settings(dict(start=-5, end=99, gain=99, match='false'), 10.0), dict(start=0.0, end=10.0, gain=24.0, fade_in=0.0, fade_out=0.0, match=False, quality=0.5))
        for bad in (dict(start=5, end=4), dict(gain=float('nan')), dict(gain='loud')):
            with self.assertRaises(AudioError): audio.clean_settings(bad, 10.0)

    def test_warnings_speak_plainly(self):
        report = dict(duration=40.0, short_db=0, limited_db=0, peak_db=-3, settings=dict(match=True))
        self.assertTrue(any('pile up' in w for w in audio.warnings(dict(duration=0.2, loudness=-12), report)))
        report = dict(duration=4.0, short_db=0, limited_db=0, peak_db=-3, settings=dict(match=True))
        self.assertTrue(any('loops a lot' in w for w in audio.warnings(dict(duration=200.0, loudness=-12), report)))
        self.assertEqual(audio.warnings(dict(duration=3.0, loudness=-12), report), [])


class SoundLab(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = audio_game(self.root)
        self.lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.lab.audio_tools = self.root / 'no-tools'

    def tearDown(self): self.temp.cleanup()

    def key(self, name): return next(r['key'] for r in self.lab.catalog.rows.values() if r['name'] == name)

    def choose(self, samples=None, rate=44100, name='song.wav'):
        return self.lab.audio_source_add(name, encode(tone(523, 3.0, rate, 0.1, 2) if samples is None else samples, rate, 'WAV'))

    def test_the_game_sounds_are_listed_and_measured(self):
        rows = {r['name']: r for r in self.lab.catalog.rows.values() if r['kind'] == 'audio'}
        self.assertEqual(sorted(rows), ['click.ogg', 'logo.ogg', 'st1.ogg'])
        self.assertTrue(all(r['group'] == 'Audio' for r in rows.values()))
        music = self.lab.audio_original(self.key('st1.ogg'))
        self.assertEqual((music['channels'], music['rate'], music['block']), (2, 44100, 0.4))
        self.assertAlmostEqual(music['duration'], 6.0, delta=0.01)
        self.assertAlmostEqual(music['loudness'], lufs(tone(220, 6.0, 44100, 0.3, 2), 44100), delta=0.5)
        click = self.lab.audio_original(self.key('click.ogg'))
        self.assertEqual((click['channels'], click['block']), (1, 0.1))
        self.assertTrue((self.lab.root / 'audio-originals.cache').is_file())
        with self.assertRaises(ValueError): self.lab.audio_original(self.key('bg2.png'))

    def test_a_sound_is_saved_built_into_assets_zip_installed_and_restored(self):
        files = sorted((self.game / 'assets').glob('*'))
        before = {p.name: file_hash(p) for p in files}
        zip_before = (self.game / 'assets' / 'assets.zip').read_bytes()
        k = self.key('st1.ogg')
        chosen = self.choose()
        self.assertEqual((chosen['channels'], chosen['rate']), (2, 44100))
        wav_bytes, report = self.lab.audio_preview(k, chosen['id'], dict(start=0.5, end=2.5, fade_out=0.5))
        self.assertEqual(wav_bytes[:4], b'RIFF')
        self.assertAlmostEqual(report['duration'], 2.0, places=3)
        self.assertAlmostEqual(report['loudness'], self.lab.audio_original(k)['loudness'], delta=0.5)
        info = self.lab.audio_apply(k, chosen['id'], dict(start=0.5, end=2.5, fade_out=0.5))
        self.assertTrue(info['edit']['staged'])
        self.assertEqual((info['edit']['info']['channels'], info['edit']['info']['rate']), (2, 44100))
        self.assertEqual(info['edit']['label'], 'song.wav')
        # the image editor's lists and filters know about it
        self.assertTrue(next(r for r in self.lab.search(kind='audio')['items'] if r['key'] == k)['saved'])
        self.assertEqual([r['key'] for r in self.lab.search(kind='audio', saved=True)['items']], [k])
        self.assertIn(k, [c['key'] for c in self.lab.status()['staged']])
        self.assertEqual(self.lab.mod()['audio'][0]['key'], k)
        # an image inside the same assets.zip goes into the same rebuilt file
        icon = self.key('snd-icon.png')
        self.lab.save(icon, png(Image.new('RGBA', (16, 16), '#ff0000')), 'original')
        build = self.lab.build()
        self.assertEqual([f['path'] for f in build['files']], ['assets/assets.zip'])
        self.assertEqual(sorted(build['files'][0]['entries']), ['snd-icon.png', 'st1.ogg'])
        rebuilt = (self.lab.root / 'builds' / build['id'] / 'files' / 'assets' / 'assets.zip').read_bytes()
        with zipfile.ZipFile(io.BytesIO(zip_before)) as a, zipfile.ZipFile(io.BytesIO(rebuilt)) as b:
            self.assertEqual([i.filename for i in a.infolist()], [i.filename for i in b.infolist()])
            for x, y in zip(a.infolist(), b.infolist()):
                self.assertEqual((x.date_time, x.compress_type, x.external_attr), (y.date_time, y.compress_type, y.external_attr))
                if x.filename in ('st1.ogg', 'snd-icon.png'): continue
                n = 30 + len(x.filename.encode()) + len(x.extra) + x.compress_size
                self.assertEqual(zip_before[x.header_offset:x.header_offset + n], rebuilt[y.header_offset:y.header_offset + n], x.filename)   # byte-identical record
            new = b.read('st1.ogg')
        self.assertEqual(new, self.lab.audio_file(k))
        y, rate = audio.decode(new)
        self.assertEqual((y.shape[1], rate), (2, 44100))
        self.assertAlmostEqual(len(y) / rate, 2.0, delta=0.01)
        self.lab.install(build['id'])
        self.assertFalse(self.lab.pending())
        self.assertTrue(self.lab.mod()['audio'][0]['installed'])
        self.assertTrue(self.lab.audio_info(k)['edit']['installed'])
        with zipfile.ZipFile(self.game / 'assets' / 'assets.zip') as z: self.assertEqual(z.read('st1.ogg'), new)
        # the game's sound is still the original in the editor while the mod is installed
        self.assertAlmostEqual(self.lab.audio_original(k)['duration'], 6.0, delta=0.01)
        # a new version is waiting until the next update
        self.lab.audio_apply(k, chosen['id'], dict(start=0, end=1.0))
        self.assertTrue(self.lab.pending())
        self.assertFalse(self.lab.mod()['audio'][0]['installed'])
        self.lab.restore()
        self.assertIsNone(self.lab.active())
        self.assertEqual(before, {p.name: file_hash(p) for p in files})        # the exact original files are back

    def test_the_loose_logo_sound_is_replaced_directly(self):
        files = sorted((self.game / 'assets').glob('*'))
        before = {p.name: file_hash(p) for p in files}
        k = self.key('logo.ogg')
        chosen = self.choose(tone(600, 2.0, 22050, 0.2), 22050, 'jingle.flac')
        self.assertEqual((chosen['channels'], chosen['rate']), (1, 22050))
        self.lab.audio_apply(k, chosen['id'], dict(match=True))
        build = self.lab.build()
        self.assertEqual(build['files'][0]['path'], 'assets/logo.ogg')
        self.assertEqual(build['files'][0]['entries'], ['Sound: logo.ogg'])
        self.lab.install(build['id'])
        y, rate = audio.decode(self.game / 'assets' / 'logo.ogg')
        self.assertEqual((y.shape[1], rate), (2, 22050))                     # stereo like the game's logo sound
        self.lab.restore()
        self.assertEqual(before, {p.name: file_hash(p) for p in files})

    def test_staging_removing_and_cleanup(self):
        k, click = self.key('st1.ogg'), self.key('click.ogg')
        chosen = self.choose()
        self.lab.audio_apply(k, chosen['id'], {})
        self.lab.install(self.lab.build()['id'])
        self.lab.audio_stage(k, False)                                         # left out of the mod, not deleted
        self.assertTrue(self.lab.pending())
        self.assertEqual((self.lab.mod()['audio'][0]['staged'], self.lab.staged_audio()), (False, []))
        b = self.lab.build()
        self.assertEqual(b['files'], [])
        self.lab.install(b['id'])                                              # nothing left: the originals go back
        self.assertIsNone(self.lab.active())
        self.lab.audio_stage(k, True)
        self.assertEqual(len(self.lab.staged_audio()), 1)
        info = self.lab.audio_remove(k)
        self.assertIsNone(info['edit'])
        with self.assertRaises(ValueError): self.lab.audio_stage(k, True)
        with self.assertRaises(ValueError): self.lab.build()                   # an empty mod
        # a long sound in a 0.15 s effect slot is flagged
        long = self.choose(tone(523, 4.0, 44100, 0.1, 2), name='long.wav')
        _, report = self.lab.audio_preview(click, long['id'], {}, info_only=True)
        self.assertTrue(any('pile up' in w for w in report['warnings']))
        self.assertEqual(report['channels'], 1)
        # chosen sounds nobody uses are forgotten after a few; saved ones stay
        kept = self.choose(name='keep.wav')
        self.lab.audio_apply(k, kept['id'], {})
        for i in range(8): self.choose(name=f'extra{i}.wav')
        names = sorted(p.stem for p in (self.lab.root / 'audio-sources').glob('*.json'))
        self.assertIn(kept['id'], names)
        self.assertEqual(len(names), 7)
        self.assertEqual(len(list((self.lab.root / 'audio').glob('*.ogg'))), 1)

    def test_the_http_endpoints(self):
        server = server_for(self.lab, 0)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        url = f'http://127.0.0.1:{server.server_port}'
        def call(path, data=None, headers=None, raw=None):
            body = raw if raw is not None else None if data is None else json.dumps(data).encode()
            req = urllib.request.Request(url + path, body, headers={**({'Content-Type': 'application/json'} if data is not None else {}), **(headers or {})})
            if body is not None: req.add_header('X-Lab-Token', self.lab.session)
            return urllib.request.urlopen(req)
        try:
            k = self.key('st1.ogg')
            with call('/api/audio?key=' + k) as r: info = json.load(r)
            self.assertEqual((info['name'], info['edit'], info['helper']['ready']), ('st1.ogg', None, False))
            with call('/api/audio-source', raw=encode(tone(440, 2.0, 48000, 0.2, 2), 48000, 'FLAC'),
                      headers={'Content-Type': 'application/octet-stream', 'X-Lab-Name': 'My%20song.flac'}) as r: chosen = json.load(r)
            self.assertEqual((chosen['name'], chosen['rate']), ('My song.flac', 48000))
            with call('/api/audio-source', dict(name='b64.wav', data=base64.b64encode(encode(tone(440, 1.0), 44100, 'WAV')).decode())) as r:
                self.assertEqual(json.load(r)['name'], 'b64.wav')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                call('/api/audio-source', raw=b'\x00\x00\x00\x18ftypM4A ' + bytes(100), headers={'Content-Type': 'application/octet-stream'})
            self.assertEqual(json.load(caught.exception)['code'], 'needs_helper')
            settings = json.dumps(dict(start=0.25, end=1.25))
            with call('/api/audio-preview?' + urllib.parse.urlencode(dict(key=k, source=chosen['id'], settings=settings))) as r:
                self.assertEqual(r.headers['Content-Type'], 'audio/wav')
                report = json.loads(r.headers['X-Lab-Audio'])
                with wave.open(io.BytesIO(r.read())) as w: self.assertEqual((w.getnframes(), w.getnchannels()), (48000, 2))
            self.assertAlmostEqual(report['duration'], 1.0, places=3)
            with call('/api/audio-apply', dict(key=k, source=chosen['id'], settings=dict(start=0.25, end=1.25))) as r: self.assertTrue(json.load(r)['edit']['staged'])
            with call('/api/mod') as r: self.assertEqual(json.load(r)['audio'][0]['key'], k)
            with call('/api/audio-file?key=' + k) as r: self.assertEqual(r.read()[:4], b'OggS')
            with call('/api/audio-stage', dict(key=k, enabled=False)) as r: self.assertFalse(json.load(r)['staged'])
            with call('/api/audio-tools') as r: self.assertFalse(json.load(r)['ready'])
            with self.assertRaises(urllib.error.HTTPError) as caught: call('/api/audio-link', dict(url='file:///C:/Windows/win.ini'))
            self.assertEqual(json.load(caught.exception)['code'], 'bad_link')
            with self.assertRaises(urllib.error.HTTPError):                       # writes need the session token
                urllib.request.urlopen(urllib.request.Request(url + '/api/audio-remove', json.dumps(dict(key=k)).encode()))
        finally:
            server.shutdown(); server.server_close(); t.join(5)


class Links(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ogg = encode(tone(440, 1.5, 44100, 0.3, 2), 44100)

    def tearDown(self):
        audio._TEST_ALLOW = frozenset()
        self.temp.cleanup()

    def serve(self, routes):
        s = Serve(routes)
        self.addCleanup(s.close)
        audio._TEST_ALLOW = frozenset({f'127.0.0.1:{s.port}'})
        return s

    def test_only_public_web_links_are_opened(self):
        for url in ('ftp://example.com/a.mp3', 'javascript:alert(1)', 'file:///C:/Windows/win.ini', 'http://user:pw@example.com/a.mp3', 'http:///nohost', '', 'http://a:99999/'):
            with self.assertRaises(AudioError, msg=url) as caught: audio.check_url(url)
            self.assertEqual(caught.exception.code, 'bad_link', url)
        for url in ('http://127.0.0.1/', 'http://localhost:8753/api/shutdown', 'http://10.0.0.8/a.ogg', 'http://192.168.1.1/', 'http://172.16.5.4/',
                    'http://169.254.169.254/latest/meta-data/', 'http://[::1]/', 'http://224.0.0.251/', 'http://[::ffff:127.0.0.1]/', 'http://0.0.0.0/',
                    'http://100.64.0.1/', 'http://[fe80::1]/', 'http://[fd00::5]/'):
            with self.assertRaises(AudioError, msg=url) as caught: audio.check_url(url)
            self.assertEqual(caught.exception.code, 'blocked', url)
        self.assertEqual(audio.check_url('https://8.8.8.8/a.mp3')[2], '8.8.8.8')     # a public address (no DNS needed)
        self.assertEqual(audio.check_url(' https://example.com/x ', resolve=False)[0], 'https://example.com/x')
        # odd spellings of this PC and the home network: refused, or not even understood by this PC's resolver
        for url in ('http://2130706433/', 'http://0x7f000001/', 'http://0177.0.0.1/', 'http://127.1/', 'http://①②⑦.0.0.1/', 'http://[::127.0.0.1]/',
                    'http://[::ffff:0:7f00:1]/', 'http://[64:ff9b::c0a8:101]/', 'http://[2002:c0a8:101::]/', 'http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/',
                    'http://255.255.255.255/', 'http://198.18.0.1/'):
            with self.assertRaises(AudioError, msg=url) as caught: audio.check_url(url)
            self.assertIn(caught.exception.code, ('blocked', 'network'), url)
        with patch.object(audio.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.215.14', 80)), (2, 1, 6, '', ('192.168.1.20', 80))]):
            with self.assertRaises(AudioError) as caught: audio.check_url('http://rebind.example/a.mp3')   # one home address among public ones
        self.assertEqual(caught.exception.code, 'blocked')

    def test_slow_servers_are_cancelled_and_time_out(self):
        """A server that sends a byte now and then never times out on its own: Cancel and the time limit still end it
        at once (on Windows only closing the socket wakes a waiting read)."""
        def drip(h):
            h.send_response(200); h.send_header('Content-Type', 'audio/ogg'); h.send_header('Content-Length', '100000'); h.end_headers()
            h.wfile.write(b'OggS' + bytes(60)); h.wfile.flush()
            for _ in range(20):
                time.sleep(0.5)
                try: h.wfile.write(b'x'); h.wfile.flush()
                except OSError: return None
        s = self.serve({'/drip.ogg': (200, {}, drip), '/silent.ogg': (200, {}, lambda h: time.sleep(6))})
        for path in ('/drip.ogg', '/silent.ogg'):
            cancel = threading.Event()
            threading.Timer(0.5, cancel.set).start()
            started = time.monotonic()
            with self.assertRaises(AudioError, msg=path) as caught: audio.fetch(s.url + path, self.root / 'dl', cancel=cancel)
            self.assertEqual(caught.exception.code, 'cancelled', path)
            self.assertLess(time.monotonic() - started, 2.5, path)
        started = time.monotonic()
        with self.assertRaises(AudioError) as caught: audio.fetch(s.url + '/drip.ogg', self.root / 'dl', deadline=1.0)
        self.assertEqual((caught.exception.code, str(caught.exception)), ('network', 'The download took too long.'))
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(list((self.root / 'dl').iterdir()), [])                # nothing half-downloaded is left

    def test_links_with_spaces_and_redirects_in_utf8(self):
        s = self.serve({'/my%20song.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg), '/caf%C3%A9.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg),
                        '/hop': (302, {'Location': '/café.ogg'.encode('utf-8').decode('latin-1')}, b'')})   # raw UTF-8 in the header
        self.assertEqual(audio.fetch(s.url + '/my song.ogg', self.root / 'dl')[2]['title'], 'my song.ogg')
        self.assertEqual(audio.fetch(s.url + '/hop', self.root / 'dl')[2]['title'], 'café.ogg')

    def test_a_page_that_turns_away_plain_downloads_goes_to_the_helper(self):
        s = self.serve({'/watch': (403, {'Content-Type': 'text/html'}, b'<html>no robots</html>'), '/gone': (404, {}, b'')})
        command, env = self.fake_helper()
        with env: self.assertEqual(audio.fetch(s.url + '/watch', self.root / 'dl', tools=self.root / 'tools', command=command)[2]['via'], 'helper')
        with self.assertRaises(AudioError) as caught: audio.fetch(s.url + '/watch', self.root / 'dl', tools=self.root / 'tools')
        self.assertEqual(caught.exception.code, 'needs_helper')
        with self.assertRaises(AudioError) as caught: audio.fetch(s.url + '/gone', self.root / 'dl', tools=self.root / 'tools')
        self.assertEqual(caught.exception.code, 'unavailable')

    def test_the_helper_can_only_reach_public_addresses(self):
        """yt-dlp fetches pages itself and follows their redirects and links. Its only way out is the lab's gate (a proxy
        on this PC) that opens public addresses only, so a page can't send it to this PC or the home network."""
        home = Serve({'/secret.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg)})   # stands for this PC / the home network
        self.addCleanup(home.close)
        s = self.serve({'/page': (200, {'Content-Type': 'text/html'}, b'<audio src="/song.ogg"></audio>'), '/song.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg),
                        '/evil': (200, {'Content-Type': 'text/html'}, f'<audio src="{home.url}/secret.ogg"></audio>'.encode()),
                        '/hop': (302, {'Location': home.url + '/secret.ogg'}, b'')})
        command, env = self.fake_helper('web')
        run = lambda path: audio._helper_fetch(s.url + path, self.root / 'dl', lambda f=None, m=None: None, threading.Event(), self.root / 'tools', command)
        with env: y, rate, info = run('/page')
        self.assertEqual((y.shape[1], rate), (2, 44100))
        self.assertEqual(info['title'], 'AC/DC - Back in Black')          # a title keeps its slash; the text-direction trick is gone
        self.assertEqual((len(info['license']), info['url']), (200, s.url + '/page'))   # long text is cut, and only a web link is kept
        args = json.loads((self.root / 'args.json').read_text(encoding='utf-8'))
        self.assertTrue(args[args.index('--proxy') + 1].startswith('http://127.0.0.1:'))
        for path in ('/evil', '/hop'):
            with env, self.assertRaises(AudioError, msg=path) as caught: run(path)
            self.assertEqual(caught.exception.code, 'blocked', path)
        self.assertEqual(home.seen, [])                                          # the home server never heard from it

    def test_the_helper_cannot_fill_the_disk(self):
        """yt-dlp's --max-filesize can't stop a stream of unknown length: past its limit the gate closes and the run ends."""
        s = self.serve({'/page': (200, {'Content-Type': 'text/html'}, b'<audio src="/huge.ogg"></audio>'), '/huge.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg + bytes(3_000_000))})
        command, env = self.fake_helper('web')
        with env, patch.object(audio._Gate, 'LIMIT', 1_000_000), self.assertRaises(AudioError) as caught:
            audio._helper_fetch(s.url + '/page', self.root / 'dl', lambda f=None, m=None: None, threading.Event(), self.root / 'tools', command)
        self.assertEqual(caught.exception.code, 'too_big')
        self.assertEqual(list((self.root / 'dl').iterdir()), [])

    def test_a_small_file_cannot_fill_the_memory(self):
        """Silence compresses to almost nothing: 10 s of 8 channels at 192 kHz is a few KB of FLAC and would be 61 MB of
        samples. Channels are mixed down while reading and at most MAX_SAMPLES samples are kept."""
        out, block = io.BytesIO(), np.zeros((65536, 8), np.float32)
        with sf.SoundFile(out, 'w', 192000, 8, format='FLAC', subtype='PCM_16') as f:
            for _ in range(192000 * 10 // 65536): f.write(block)
        self.assertLess(len(out.getvalue()), 200000)
        info = {}
        with patch.object(audio, 'MAX_SAMPLES', 48000 * 2 * 2):                # 2 s of 48 kHz stereo
            y, rate = audio.decode(out.getvalue(), info=info)
        self.assertEqual((y.shape, rate), ((96000, 2), 192000))                 # 0.5 s at 192 kHz, stereo
        self.assertAlmostEqual(info['cut'], 9.9, delta=0.2)
        y, rate = audio.normalize(y, rate)
        warn = audio.warnings(dict(duration=3.0, loudness=-20), dict(duration=0.5, short_db=0, limited_db=0, peak_db=None, settings=dict(match=False)), dict(cut=info['cut'], duration=len(y) / rate))
        self.assertTrue(any('only its first 0:00.50 can be used' in w for w in warn), warn)
        self.assertEqual([audio.clock(s) for s in (0.15, 0.5, 6.0, 75.25)], ['0:00.15', '0:00.50', '0:06.0', '1:15.2'])

    def test_a_direct_link_is_downloaded_and_decoded(self):
        s = self.serve({'/song.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg),
                        '/mystery': (200, {'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="Night Drive.ogg"'}, self.ogg),
                        '/hop': (302, {'Location': '/song.ogg'}, b'')})
        seen = []
        y, rate, info = audio.fetch(s.url + '/hop', self.root / 'dl', lambda f=None, m=None: seen.append((f, m)), threading.Event())
        self.assertEqual((y.shape, rate, info['via'], info['title']), ((66150, 2), 44100, 'direct', 'song.ogg'))
        self.assertTrue(any(f == 1.0 for f, _ in seen))
        y, rate, info = audio.fetch(s.url + '/mystery', self.root / 'dl')
        self.assertEqual(info['title'], 'Night Drive.ogg')                     # found by its first bytes and named by the server
        self.assertEqual(list((self.root / 'dl').iterdir()), [])                # no download is left behind

    def test_redirects_are_checked_again(self):
        other = Serve({'/x.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg)})   # a second server the test does not allow
        self.addCleanup(other.close)
        s = self.serve({'/private': (302, {'Location': other.url + '/x.ogg'}, b''), '/lan': (301, {'Location': 'http://192.168.0.10/x.ogg'}, b''),
                        '/loop': (302, {'Location': '/loop'}, b'')})
        for path, code in (('/private', 'blocked'), ('/lan', 'blocked'), ('/loop', 'network')):
            with self.assertRaises(AudioError, msg=path) as caught: audio.fetch(s.url + path, self.root / 'dl')
            self.assertEqual(caught.exception.code, code, path)
        self.assertEqual(sum(1 for p, _ in s.seen if p == '/loop'), 6)          # five redirects followed, then it stops

    def test_size_limit_and_errors(self):
        s = self.serve({'/big.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg), '/gone.ogg': (404, {}, b'no')})
        with patch.object(audio, 'MAX_BYTES', 1000):
            with self.assertRaises(AudioError) as caught: audio.fetch(s.url + '/big.ogg', self.root / 'dl')
        self.assertEqual(caught.exception.code, 'too_big')
        with self.assertRaises(AudioError) as caught: audio.fetch(s.url + '/gone.ogg', self.root / 'dl')
        self.assertIn('404', str(caught.exception))
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(AudioError) as caught: audio.fetch(s.url + '/big.ogg', self.root / 'dl', cancel=cancel)
        self.assertEqual(caught.exception.code, 'cancelled')

    def fake_helper(self, mode='ok'):
        script = self.root / 'fake_ytdlp.py'
        script.write_text(FAKE_YTDLP, encoding='utf-8')
        sound = self.root / 'fake-sound.ogg'
        sound.write_bytes(self.ogg)
        env = {'FAKE_YTDLP_MODE': mode, 'FAKE_YTDLP_SOUND': str(sound), 'FAKE_YTDLP_LOG': str(self.root / 'args.json')}
        return [sys.executable, str(script)], patch.dict('os.environ', env)

    def test_a_page_goes_through_the_link_helper(self):
        s = self.serve({'/watch': (200, {'Content-Type': 'text/html; charset=utf-8'}, b'<!doctype html><title>video</title>')})
        command, env = self.fake_helper()
        seen = []
        with env: y, rate, info = audio.fetch(s.url + '/watch', self.root / 'dl', lambda f=None, m=None: seen.append((f, m)), threading.Event(), self.root / 'tools', command)
        self.assertEqual((y.shape[1], rate, info['via'], info['title']), (2, 44100, 'helper', 'Calm Loop'))
        self.assertEqual(info['license'], 'Creative Commons Attribution license (reuse allowed)')
        self.assertEqual([f for f, m in seen if m and m.startswith('Downloading')], [0.0, 0.25, 0.5, 1.0])
        args = json.loads((self.root / 'args.json').read_text(encoding='utf-8'))
        for flag in ('--no-playlist', '--ignore-config', '--no-part', '--no-mtime', '--newline', '--progress'): self.assertIn(flag, args)
        self.assertEqual(args[args.index('--format') + 1], 'bestaudio/best')
        self.assertEqual(args[args.index('--max-filesize') + 1], '150M')
        self.assertEqual(args[args.index('--match-filters') + 1], '!is_live & duration <=? 1800')
        self.assertEqual(args[args.index('--js-runtimes') + 1], 'deno:' + str(self.root / 'tools' / 'deno' / 'deno.exe'))
        self.assertEqual(args[-2:], ['--', s.url + '/watch'])
        self.assertEqual(list((self.root / 'dl').iterdir()), [])                # the helper's folder is cleaned up
        # without the helper installed, a page asks for it
        with self.assertRaises(AudioError) as caught: audio.fetch(s.url + '/watch', self.root / 'dl', tools=self.root / 'tools')
        self.assertEqual(caught.exception.code, 'needs_helper')

    def test_link_helper_errors_become_plain_advice(self):
        for mode, code in (('outdated', 'outdated'), ('signin', 'account'), ('unsupported', 'unsupported'), ('filter', 'too_long')):
            command, env = self.fake_helper(mode)
            with env, self.assertRaises(AudioError, msg=mode) as caught:
                audio._helper_fetch('https://www.youtube.com/watch?v=abc123', self.root / 'dl', lambda f=None, m=None: None, threading.Event(), self.root / 'tools', command)
            self.assertEqual(caught.exception.code, code, mode)
        command, env = self.fake_helper('slow')
        cancel = threading.Event()
        threading.Timer(1.0, cancel.set).start()
        started = time.monotonic()
        with env, self.assertRaises(AudioError) as caught:
            audio._helper_fetch('https://www.youtube.com/watch?v=abc123', self.root / 'dl', lambda f=None, m=None: None, cancel, self.root / 'tools', command)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertLess(time.monotonic() - started, 20)
        command, env = self.fake_helper()
        with env: self.assertIn('Updated yt-dlp', audio.update_tools(self.root, command=command)['message'])

    def test_the_link_job_in_the_lab(self):
        game = audio_game(self.root / 'g')
        lab = Lab(game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        s = self.serve({'/tune.ogg': (200, {'Content-Type': 'audio/ogg'}, self.ogg)})
        job = lab.audio_link(s.url + '/tune.ogg', 'k')
        for _ in range(200):
            job = lab.audio_job(job['id'])
            if job['state'] != 'running': break
            time.sleep(0.05)
        self.assertEqual(job['state'], 'done', job)
        self.assertEqual((job['result']['title'], job['result']['duration']), ('tune.ogg', 1.5))
        self.assertIsNotNone(lab.audio_source(job['result']['id']))
        with self.assertRaises(AudioError): lab.audio_link('ftp://example.com/x.mp3')
        bad = lab.audio_link('http://127.0.0.1:9/never.ogg')                     # this PC: refused inside the job
        for _ in range(100):
            bad = lab.audio_job(bad['id'])
            if bad['state'] != 'running': break
            time.sleep(0.05)
        self.assertEqual((bad['state'], bad['code']), ('error', 'blocked'))


class HelperInstall(unittest.TestCase):
    def test_unpack_retries_windows_access_denied(self):
        self.tools.mkdir()
        wheel = self.root / 'helper.whl'
        wheel.write_bytes(self.files['/av-19.0.1-cp312-abi3-win_amd64.whl'])
        replace = audio.os.replace
        calls = []
        def locked(source, target):
            calls.append((source, target))
            if len(calls) < 4: raise PermissionError(5, 'Access is denied')
            return replace(source, target)
        with patch.object(audio.os, 'replace', side_effect=locked), patch.object(audio.time, 'sleep'):
            audio.unpack(wheel, self.tools, 'pylib')
        self.assertEqual(len(calls), 4)
        self.assertTrue((self.tools / 'pylib' / 'av' / '__init__.py').is_file())

    def test_unpack_persistent_lock_preserves_old_install_and_download(self):
        target = self.tools / 'pylib'
        target.mkdir(parents=True)
        (target / 'old.txt').write_text('keep')
        wheel = self.root / 'helper.whl'
        wheel.write_bytes(self.files['/av-19.0.1-cp312-abi3-win_amd64.whl'])
        replace = audio.os.replace
        def locked(source, dest):
            if str(source).endswith('.new'): raise PermissionError(5, 'Access is denied')
            return replace(source, dest)
        with patch.object(audio.os, 'replace', side_effect=locked), patch.object(audio.time, 'sleep'):
            with self.assertRaises(AudioError) as error: audio.unpack(wheel, self.tools, 'pylib')
        self.assertEqual(error.exception.code, 'helper_locked')
        self.assertEqual((target / 'old.txt').read_text(), 'keep')
        self.assertTrue(wheel.is_file())
        self.assertFalse(list(self.tools.glob('*.new')))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.exe = b'MZ fake yt-dlp ' * 4000
        deno, wheel = io.BytesIO(), io.BytesIO()
        with zipfile.ZipFile(deno, 'w') as z: z.writestr('deno.exe', b'MZ fake deno ' * 3000)
        with zipfile.ZipFile(wheel, 'w') as z:
            z.writestr('av/__init__.py', '__version__ = "19.0.1"\n')
            z.writestr('av-19.0.1.dist-info/METADATA', 'Name: av\n')
        self.files = {'/yt-dlp.exe': self.exe, '/deno-x86_64-pc-windows-msvc.zip': deno.getvalue(), '/av-19.0.1-cp312-abi3-win_amd64.whl': wheel.getvalue()}
        self.server = Serve({path: (200, {'Content-Type': 'application/octet-stream', 'X-Ranges': 'yes'}, body) for path, body in self.files.items()})
        self.tools = self.root / 'tools'

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def items(self, **wrong):
        into = {'/yt-dlp.exe': 'yt-dlp.exe', '/deno-x86_64-pc-windows-msvc.zip': 'deno', '/av-19.0.1-cp312-abi3-win_amd64.whl': 'pylib'}
        return [dict(name=into[p], url=self.server.url + p, size=len(b), sha256=wrong.get(into[p]) or hashlib.sha256(b).hexdigest(), into=into[p]) for p, b in self.files.items()]

    def test_install_checks_every_file_and_unpacks_it(self):
        seen = []
        status = audio.install_tools(self.tools, self.items(), lambda f=None, m=None: seen.append(f), check=False)
        self.assertTrue(status['ready'])
        self.assertEqual((self.tools / 'yt-dlp.exe').read_bytes(), self.exe)
        self.assertTrue((self.tools / 'deno' / 'deno.exe').is_file() and (self.tools / 'pylib' / 'av' / '__init__.py').is_file())
        self.assertEqual(status['download'], 0)
        self.assertAlmostEqual(max(f for f in seen if f is not None), 1.0)
        self.assertEqual(list((self.tools / 'downloads').iterdir()), [])        # downloads are removed once unpacked
        self.assertTrue(audio.helper_ready(self.tools))
        before = len(self.server.seen)
        audio.install_tools(self.tools, self.items(), check=False)              # nothing to do the second time
        self.assertEqual(len(self.server.seen), before)

    def test_a_wrong_checksum_is_refused(self):
        with self.assertRaises(AudioError) as caught: audio.install_tools(self.tools, self.items(deno='0' * 64), check=False)
        self.assertEqual(caught.exception.code, 'checksum')
        self.assertFalse((self.tools / 'deno').exists())
        self.assertFalse(list((self.tools / 'downloads').glob('*.part')))        # the bad file is gone
        self.assertFalse(audio.tools_status(self.tools, self.items())['ready'])

    def test_a_partial_download_continues(self):
        body = self.files['/deno-x86_64-pc-windows-msvc.zip']
        part = self.tools / 'downloads' / 'deno-x86_64-pc-windows-msvc.zip.part'
        part.parent.mkdir(parents=True)
        part.write_bytes(body[:len(body) // 2])
        audio.install_tools(self.tools, self.items(), check=False)
        ranges = [h.get('Range') for p, h in self.server.seen if p == '/deno-x86_64-pc-windows-msvc.zip']
        self.assertEqual(ranges, [f'bytes={len(body) // 2}-'])
        self.assertTrue((self.tools / 'deno' / 'deno.exe').is_file())
        # a server that ignores the range sends the whole file again: that works too
        self.server.routes['/yt-dlp.exe'] = (200, {'Content-Type': 'application/octet-stream'}, self.exe)
        (self.tools / 'yt-dlp.exe').unlink()
        part = self.tools / 'downloads' / 'yt-dlp.exe.part'
        part.write_bytes(self.exe[:1000])
        audio.install_tools(self.tools, self.items(), check=False)
        self.assertEqual((self.tools / 'yt-dlp.exe').read_bytes(), self.exe)

    def test_a_file_bigger_than_its_pin_is_refused(self):
        items = self.items()
        items[0]['size'] -= 10                                                   # the server sends 10 bytes more than pinned
        with self.assertRaises(AudioError) as caught: audio.install_tools(self.tools, items, check=False)
        self.assertEqual(caught.exception.code, 'checksum')
        self.assertFalse((self.tools / 'yt-dlp.exe').exists() or list((self.tools / 'downloads').glob('yt-dlp*')))

    def test_cancel_ends_a_stalled_download_at_once(self):
        def stall(h):
            h.send_response(200); h.send_header('Content-Length', str(len(self.exe))); h.end_headers()
            h.wfile.write(self.exe[:5000]); h.wfile.flush()
            time.sleep(8)
        self.server.routes['/yt-dlp.exe'] = (200, {}, stall)
        cancel = threading.Event()
        threading.Timer(0.7, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(AudioError) as caught: audio.install_tools(self.tools, self.items(), cancel=cancel, check=False)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertLess(time.monotonic() - started, 3)                           # not the 30 s socket time-out
        self.assertTrue(list((self.tools / 'downloads').glob('*.part')))         # kept to continue next time

    def test_cancel_keeps_the_part_for_next_time(self):
        cancel = threading.Event()
        def stop(f=None, m=None):
            if f and f > 0.1: cancel.set()
        with self.assertRaises(AudioError) as caught: audio.install_tools(self.tools, self.items(), stop, cancel, check=False)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertTrue(list((self.tools / 'downloads').glob('*.part')))
        audio.install_tools(self.tools, self.items(), check=False)
        self.assertTrue(audio.helper_ready(self.tools))

    def test_the_manifest_lists_the_pinned_helper(self):
        items = audio.tool_list()
        self.assertEqual([t['into'] for t in items], ['yt-dlp.exe', 'deno', 'pylib'])
        self.assertTrue(all(len(t['sha256']) == 64 and t['url'].startswith('https://') for t in items))
        self.assertAlmostEqual(sum(t['size'] for t in items) / 1e6, 89, delta=1)   # "about 89 MB" in the consent prompt
        manifest = json.loads(audio.MANIFEST.read_text(encoding='utf-8'))
        names = [w['name'] for w in manifest['wheels']]
        self.assertTrue({'soundfile 0.14.0', 'cffi 2.1.1', 'pycparser 3.11'} <= set(names), names)


if __name__ == '__main__': unittest.main()
