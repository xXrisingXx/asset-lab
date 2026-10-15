"""The sound editor in a real (headless) Edge with real mouse input: pick a game sound, load a sound file from this PC,
trim it with the waveform handles, hear the preview, save it into the mod and find it in the Review dialog."""
import io
import json
from pathlib import Path
import zipfile
import numpy as np
import soundfile as sf
from harness import EditorTestCase
from fakegame import make_editor_game


def tone(freq, seconds, rate=44100, amp=0.25, channels=2):
    t = np.arange(int(seconds * rate)) / rate
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(x[:, None], channels, axis=1)


def ogg(samples, rate=44100):
    out = io.BytesIO()
    with sf.SoundFile(out, 'w', rate, samples.shape[1], format='OGG', subtype='VORBIS') as f:
        for i in range(0, len(samples), 16384): f.write(samples[i:i + 16384])
    return out.getvalue()


def sound_game(root):
    """The editor test game with a music track and a short click in assets.zip, stored like the real game's."""
    game = make_editor_game(root)
    with zipfile.ZipFile(game / 'assets' / 'assets.zip', 'a') as z:
        z.writestr(zipfile.ZipInfo('st1.ogg', (2020, 1, 2, 3, 4, 6)), ogg(tone(220, 8.0)))
        z.writestr(zipfile.ZipInfo('click.ogg', (2020, 1, 2, 3, 4, 6)), ogg(tone(1800, 0.2, channels=1)))
    return game


class SoundEditorTests(EditorTestCase):
    game_maker = staticmethod(sound_game)

    def state(self): return self.browser.eval('LabAudio.state()')

    def open_sound(self, name):
        key = self.server.asset_key(name)
        b = self.browser
        b.eval(f'await select({json.dumps(key)}, true); true')
        b.wait_for(f"LabAudio.state().visible && LabAudio.state().key === {json.dumps(key)} && document.getElementById('aeGamePlay').offsetWidth > 0")
        b.wait_for("document.querySelector('.ae-game b').textContent.includes('.ogg')")
        return key

    def song(self, seconds=6.0, name='my-song.wav'):
        path = Path(self.server.temp) / name
        sf.write(str(path), tone(523, seconds, 48000, 0.1), 48000, subtype='PCM_16')
        return path

    def test_1_load_trim_preview_save_and_review(self):
        b = self.browser
        key = self.open_sound('st1.ogg')
        self.assertEqual(b.text('#aeBadge'), 'The game’s sound')
        self.assertTrue(b.eval("getComputedStyle(document.getElementById('audio')).display === 'none'"))   # the plain player gives way
        self.assertIn('Only use music you have the right to use.', b.text('.ae-replace'))
        b.set_files('#audioFile', [self.song()])
        b.wait_for('LabAudio.state().source && LabAudio.state().report', 20, message='the chosen file did not load')
        s = self.state()
        self.assertEqual((s['set']['start'], s['set']['end'], s['saved']), (0, 6.0, False))
        self.assertEqual(b.text('#aeBadge'), 'Not saved yet')
        self.assertIn('my-song.wav', b.text('#aeSrcName'))
        # Trim with the handles: real drags on the waveform.
        r = b.eval("(()=>{const r=document.getElementById('aeWave').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
        left, top, width, height = r
        y = top + height / 2
        b.drag([(left + 1, y), (left + width * 0.25, y)])                  # start handle to 1.5 s
        b.wait_for('LabAudio.state().set.start > 1.3')
        b.drag([(left + width - 1, y), (left + width * 0.75, y)])          # end handle to 4.5 s
        b.wait_for('LabAudio.state().set.end < 4.7')
        s = self.state()
        step = 6.0 / width                                                   # one pixel of the waveform in seconds
        self.assertAlmostEqual(s['set']['start'], 1.5, delta=3 * step)
        self.assertAlmostEqual(s['set']['end'], 4.5, delta=3 * step)
        self.assertEqual(b.eval("+document.getElementById('aeStart').value"), round(s['set']['start'], 2))
        # Fades and volume from the sliders; the lab reports the result.
        b.set_value('#aeOut', 0.5)
        b.set_value('#aeGain', -3)
        b.wait_for("LabAudio.state().report && Math.abs(LabAudio.state().report.duration - (LabAudio.state().set.end - LabAudio.state().set.start)) < 0.001 && LabAudio.state().report.settings.fade_out === 0.5", 15)
        self.assertIn('as loud as the game’s sound', b.text('#aeResult'))
        # Preview: the lab renders it, the page plays it and the playhead moves.
        b.click_selector('#aePlay')
        b.wait_for("LabAudio.state().playing === 'new' && LabAudio.state().preview", 20)
        b.wait_for('LabAudio.state().time > 0.15', 10, message='the preview did not play')
        b.click_selector('#aePlay')
        b.wait_for('!LabAudio.state().playing')
        # A/B: the game's own sound plays from the same toggle.
        b.click_selector('.ae-ab [data-mode="orig"]')
        b.click_selector('#aePlay')
        b.wait_for("LabAudio.state().playing === 'orig' && LabAudio.state().time > 0.1", 10)
        b.click_selector('#aePlay')
        # Save into the mod.
        b.click_selector('#aeSave')
        b.wait_for('LabAudio.state().saved && LabAudio.state().edit && LabAudio.state().edit.staged', 30, message='saving did not finish')
        self.assertEqual(b.text('#aeBadge'), 'Your sound · in your mod')
        mod = self.server.get('/api/mod')
        self.assertEqual([(a['key'], a['staged'], a['label']) for a in mod['audio']], [(key, True, 'my-song.wav')])
        self.assertAlmostEqual(mod['audio'][0]['duration'], s['set']['end'] - s['set']['start'], delta=0.01)
        b.wait_for("[...document.querySelectorAll('#modEntries .mod-chip')].some(c => c.textContent.includes('st1.ogg'))")
        # The Review dialog lists it with before / after and Remove.
        b.click_selector('#reviewButton')
        b.wait_for("document.getElementById('reviewDialog').open && !!document.querySelector('#reviewDialog .review-sound')", 15)
        text = b.text('#reviewDialog')
        for part in ('Sounds · 1', 'st1.ogg', 'my-song.wav', '1 sound', '▶ Before', '▶ After'): self.assertIn(part, text)
        b.click_selector('#reviewDialog .review-sound .review-toggle')     # Remove: left out, not deleted
        b.wait_for("!!document.querySelector('#reviewDialog .review-sound.out')")
        self.assertFalse(self.server.get('/api/mod')['audio'][0]['staged'])
        b.click_selector('#reviewDialog .review-sound .review-toggle')     # Put back
        b.wait_for("!document.querySelector('#reviewDialog .review-sound.out')")
        self.assertTrue(self.server.get('/api/mod')['audio'][0]['staged'])
        b.key('Escape')
        b.wait_for("!document.getElementById('reviewDialog').open")

    def test_2_a_long_sound_in_a_short_effect_is_flagged_and_trimmed(self):
        b = self.browser
        self.open_sound('click.ogg')
        b.set_files('#audioFile', [self.song(5.0, 'long.wav')])
        b.wait_for("!!LabAudio.state().source && !!document.querySelector('#aeWarnings li')", 20)
        self.assertIn('pile up', b.text('#aeWarnings'))
        self.assertIn('Use just 0:00.20', b.text('#aeWarnings'))             # hundredths under a second
        b.click_selector('#aeWarnings .link')
        b.wait_for('LabAudio.state().set.end < 0.3 && !document.querySelector("#aeWarnings li")', 15)
        self.assertIn('mono', b.text('#aeResult'))                           # mixed down like the game's click
        # Going to another sound and back keeps the unsaved choice.
        self.open_sound('st1.ogg')
        self.open_sound('click.ogg')
        self.assertLess(self.state()['set']['end'], 0.3)
        b.click_selector('#aeOriginal')                                      # nothing saved yet: just drops the choice
        b.wait_for('!LabAudio.state().source')
        self.assertTrue(b.eval("document.querySelector('.ae-edit').hidden"))

    def test_3_links_are_checked_before_anything_is_fetched(self):
        b = self.browser
        self.open_sound('click.ogg')
        b.set_value('#aeLink', 'file:///C:/Windows/win.ini')
        b.click_selector('#aeGet')
        b.wait_for("!document.getElementById('aeProblem').hidden")
        self.assertIn('http', b.text('#aeProblem'))
        b.set_value('#aeLink', 'http://127.0.0.1:9/sound.ogg')            # this PC: refused for safety
        b.click_selector('#aeGet')
        b.wait_for("document.getElementById('aeProblem').textContent.includes('blocked')", 15)
        self.assertIn('home network', b.text('#aeProblem'))
        b.eval("document.getElementById('aeProblem').textContent = ''; true")
        b.set_value('#aeLink', '127.0.0.1:9/sound.ogg')                     # no http://: taken as https://, still this PC
        b.click_selector('#aeLink'); b.key('Enter')                          # Enter sends the link too
        b.wait_for("document.getElementById('aeProblem').textContent.includes('blocked') && !LabAudio.state().job", 15)

    def test_4_save_stays_in_sight_on_a_laptop_screen(self):
        """At 1366 x 768 the panel scrolls, but Save to mod and Use the original sound stay at its bottom."""
        b = self.browser
        b.set_viewport(1366, 768)
        try:
            self.open_sound('st1.ogg')
            b.set_files('#audioFile', [self.song(4.0, 'laptop.wav')])
            b.wait_for('LabAudio.state().source && LabAudio.state().report', 20)
            seen = "(id=>{const r=document.getElementById(id).getBoundingClientRect(),v=document.getElementById('nonvisual').getBoundingClientRect();return r.height>0&&r.top>=v.top-0.5&&r.bottom<=v.bottom+0.5})"
            for where in ('0', 'document.getElementById("nonvisual").scrollHeight'):
                b.eval(f'document.getElementById("nonvisual").scrollTop = {where}; true'); b.settle()
                self.assertTrue(b.eval(f"{seen}('aeSave') && {seen}('aeOriginal')"), f'scrolled to {where}')
            b.click_selector('#aeSave')                                          # a real click where it shows
            b.wait_for('LabAudio.state().saved && LabAudio.state().edit && LabAudio.state().edit.staged', 30)
        finally: b.set_viewport(*self.viewport)
