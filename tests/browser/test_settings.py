"""The Settings window (src/web/settings.js) in headless Edge against an isolated lab: the gear, categories, search, live
saving, resets, "Next start" tags, its look at 1366 x 768 and 1920 x 1080, persistence and a damaged settings.json, and
the effect of every setting the page applies, each changed with its own control in the window (real clicks on switches
and swatches). Settings the lab's server applies are proven in tests/test_settings.py.

Nothing here can reach Steam or the real game: the lab is isolated (fake game, temp home), "Open Mitosis after
installing" is checked with /api/launch-game answered inside the page AND blocked in the browser, and "If Mitosis is
open" with /api/game and /api/close-game answered inside the page. Buttons that open File Explorer are never clicked."""
import base64
import io
import json
import math
import time
from pathlib import Path
import zipfile
import numpy as np
import soundfile as sf
from PIL import Image
from harness import EditorTestCase
from fakegame import make_editor_game
from lab_core import png
from test_fonts import FNT, page as font_page

SAVED = "document.querySelector('#settingsStatus.on') && document.getElementById('settingsStatus').textContent === 'Saved'"


def until(fn, timeout=10, message='timed out'):
    end = time.monotonic() + timeout
    while True:
        value = fn()
        if value: return value
        if time.monotonic() > end: raise AssertionError(message)
        time.sleep(0.05)


def tone(freq, seconds, rate=44100, amp=0.25, channels=2):
    t = np.arange(int(seconds * rate)) / rate
    return np.repeat((amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)[:, None], channels, axis=1)


def ogg(samples, rate=44100):
    out = io.BytesIO()
    with sf.SoundFile(out, 'w', rate, samples.shape[1], format='OGG', subtype='VORBIS') as f: f.write(samples)
    return out.getvalue()


def sound_game(root):
    game = make_editor_game(root)
    with zipfile.ZipFile(game / 'assets' / 'assets.zip', 'a') as z:
        z.writestr(zipfile.ZipInfo('st1.ogg', (2020, 1, 2, 3, 4, 6)), ogg(tone(220, 8.0)))
        z.writestr(zipfile.ZipInfo('click.ogg', (2020, 1, 2, 3, 4, 6)), ogg(tone(1800, 0.2, channels=1)))
    return game


def font_game(root):
    """The editor game plus the player names' font (nicktext), so the Fonts window draws real previews."""
    game = make_editor_game(root)
    font_page().save(game / 'assets' / 'nicktext_0.png')
    (game / 'assets' / 'nicktext.fnt').write_text(FNT, encoding='utf-8')
    return game


def tile_game(root):
    """The editor game plus 130 small loose tiles, so the library has several pages."""
    game = make_editor_game(root)
    for i in range(130): (game / 'assets' / f'tile-{i:03d}.png').write_bytes(png(Image.new('RGBA', (8, 8), (i, 90, 160, 255))))
    return game


class SettingsCase(EditorTestCase):
    """Helpers: open the window, change a setting with its own control, read what the lab saved."""
    _schema = None

    def spec(self, key):
        if SettingsCase._schema is None: SettingsCase._schema = {s['key']: s for s in self.server.get('/api/settings')['schema']}
        return SettingsCase._schema[key]

    def saved(self, key=None):
        values = self.server.get('/api/settings')['values']
        return values if key is None else values[key]

    def stored(self):
        f = Path(self.server.home) / 'settings.json'
        return {k: v for k, v in json.loads(f.read_text(encoding='utf-8')).items() if k != '//'} if f.exists() else {}

    def put(self, **values): return self.server.post('/api/settings', {'values': values})['values']

    def reset_all(self): return self.server.post('/api/settings', {'reset': 'all'})['values']

    def is_open(self): return self.browser.eval("!!document.getElementById('settingsDialog')?.open")

    def setUp(self):
        super().setUp()
        # A test that failed half-way may have left a window open; its backdrop would cover the page.
        self.browser.eval("for(const d of document.querySelectorAll('dialog[open]'))d.close(); true")

    def open_settings(self, category='general'):
        b = self.browser
        if not self.is_open(): b.click_selector('#settingsButton')
        b.wait_for("document.getElementById('settingsDialog').open && !!document.querySelector('#settingsPanel .settings-section')", message='Settings did not open')
        if b.eval("document.getElementById('settingsSearch').value"):
            b.eval("const s=document.getElementById('settingsSearch');s.value='';s.dispatchEvent(new Event('input'))")
        b.click_selector(f'#settingsTab-{category}')
        b.wait_for(f"document.querySelector('#settingsPanel .settings-section')?.dataset.category === {json.dumps(category)}")

    def close_settings(self):
        if self.is_open():
            self.browser.click_selector('#settingsDone')
            self.browser.wait_for("!document.getElementById('settingsDialog').open")

    def change(self, key, value):
        """Change a setting with its control in the window, as a player does, and wait until the lab saved it."""
        b, spec = self.browser, self.spec(key)
        self.open_settings(spec['category'])
        sel = f'#set-{key}'
        if spec['type'] == 'bool':
            if b.eval(f"document.querySelector('{sel}').checked") != value: b.click_selector(sel)   # a real click on the switch
        elif key == 'accent': b.click_selector(f"#settingsPanel input[name='set-accent'][value={json.dumps(value)}]")
        elif spec['type'] == 'choice': b.set_value(sel, [c['value'] for c in spec['choices']].index(value))
        else: b.set_value(sel, value)
        until(lambda: self.saved(key) == value, message=f'{key} was not saved as {value!r}')
        b.wait_for(f"LabSettings.get({json.dumps(key)}) === {json.dumps(value)}")
        return value

    def stub(self, routes, record=()):
        """Answer some /api/... requests inside the page (routes: path -> JSON, or a JS function (url, body) -> JSON);
        requests to paths in record are counted in window.__calls and their bodies kept in window.__bodies."""
        js = 'const routes={' + ','.join(f'{json.dumps(k)}:{v if isinstance(v, str) and v.startswith("(") else json.dumps(v)}' for k, v in routes.items()) + '};'
        self.browser.eval(js + "const record=" + json.dumps(list(record)) + ";window.__calls=window.__calls||{};window.__bodies=window.__bodies||{};"
                          "if(!window.__realFetch)window.__realFetch=window.fetch;"
                          "window.fetch=(u,o)=>{const p=new URL(String(u),location.href).pathname,body=o&&o.body?JSON.parse(o.body):null;"
                          "if(record.includes(p)||p in routes){__calls[p]=(__calls[p]||0)+1;(__bodies[p]=__bodies[p]||[]).push(body);}"
                          "if(p in routes){const r=routes[p],v=typeof r==='function'?r(u,body):r;return Promise.resolve(new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}}));}"
                          "return window.__realFetch(u,o);};true")

    def calls(self, path): return self.browser.eval(f"(window.__calls||{{}})[{json.dumps(path)}]||0")

    def bodies(self, path): return self.browser.eval(f"(window.__bodies||{{}})[{json.dumps(path)}]||[]")

    def reload(self):
        self.close_settings()
        self.browser.reload()


# ----- the window --------------------------------------------------------------------------------------------------------
class WindowTests(SettingsCase):
    viewport = (1366, 768)

    def test_1_gear_categories_search_and_keys(self):
        b = self.browser
        self.assertTrue(b.eval("!!document.querySelector('.topbar > #settingsButton')"))
        self.assertEqual(b.eval("document.getElementById('settingsButton').getAttribute('aria-label')"), 'Settings')
        b.click_selector('#settingsButton')
        b.wait_for("document.getElementById('settingsDialog').open && !!document.querySelector('#settingsPanel .settings-row')")
        tabs = b.eval("[...document.querySelectorAll('#settingsNav [role=tab]')].map(t=>t.textContent)")
        self.assertEqual(tabs, ['General', 'Appearance', 'Editor', 'Theme AI & drawing', 'Text & fonts', 'Sound', 'Library', 'Storage & privacy', 'About'])
        self.assertEqual(b.eval("document.querySelector('#settingsNav [aria-selected=true]').textContent"), 'General')
        self.assertEqual(b.eval("document.querySelector('#settingsPanel .settings-row').dataset.key"), 'language')
        self.assertEqual(b.eval("[...document.getElementById('set-language').options].map(o=>o.textContent)"),
                         ['Automatic (Windows language)', 'English', 'Español', 'Português', 'Français', 'Deutsch'])
        # Arrow keys move between categories.
        b.eval("document.getElementById('settingsTab-general').focus()")
        b.key('ArrowDown')
        b.wait_for("document.querySelector('#settingsPanel .settings-section').dataset.category === 'appearance'")
        # Search across every category, with the number of hits beside each.
        b.click_selector('#settingsSearch')
        b.type_text('undo')
        b.wait_for("document.querySelectorAll('#settingsPanel .settings-row').length === 2")
        self.assertEqual(b.eval("[...document.querySelectorAll('#settingsPanel .settings-row')].map(r=>r.dataset.key)"), ['history_states', 'history_memory'])
        self.assertEqual(b.text('#settingsTab-editor .settings-hits'), '2')
        self.assertTrue(b.eval("document.getElementById('settingsTab-general').classList.contains('settings-nohits')"))
        b.eval("const s=document.getElementById('settingsSearch');s.select()")
        b.type_text('privacy github')
        b.wait_for("!!document.getElementById('settingsPrivacy')")
        b.eval("document.getElementById('settingsSearch').select()")
        b.type_text('zzzz')
        b.wait_for("document.querySelector('#settingsPanel .settings-empty')?.textContent.includes('No settings match')")
        b.key('Escape')                                                    # Esc clears the search first...
        b.wait_for("document.getElementById('settingsSearch').value === '' && !!document.querySelector('#settingsPanel .settings-row')")
        self.assertTrue(self.is_open())
        b.key('Escape')                                                    # ...then closes the window
        b.wait_for("!document.getElementById('settingsDialog').open")
        b.eval("document.activeElement.blur()")
        b.key('Ctrl+,')                                                    # the shortcut opens it again
        b.wait_for("document.getElementById('settingsDialog').open")
        self.close_settings()

    def look_clean(self, width, height):
        b = self.browser
        b.set_viewport(width, height)
        self.open_settings('general')
        for cat in ('general', 'appearance', 'editor', 'theme', 'text', 'sound', 'library', 'storage', 'about'):
            self.open_settings(cat)
            b.settle(3)
            b.screenshot(f'settings-{width}x{height}-{cat}.png')
            box = b.eval("(()=>{const d=document.getElementById('settingsDialog').getBoundingClientRect(),f=document.getElementById('settingsDone').getBoundingClientRect(),"
                         "p=document.getElementById('settingsPanel');return {left:d.left,top:d.top,right:d.right,bottom:d.bottom,done:f.bottom,doneRight:f.right,"
                         "over:p.scrollWidth-p.clientWidth,rows:[...p.querySelectorAll('.settings-control')].map(c=>c.getBoundingClientRect().right-p.getBoundingClientRect().right)}})()")
            self.assertTrue(box['left'] >= 0 and box['top'] >= 0 and box['right'] <= width and box['bottom'] <= height, (cat, box))
            self.assertLessEqual(box['done'], box['bottom'], cat)                                   # the footer is in view
            self.assertLessEqual(box['over'], 1, f'{cat}: the panel scrolls sideways')
            self.assertTrue(all(r <= 0 for r in box['rows']), f'{cat}: a control sticks out')
        self.assertLessEqual(b.eval("document.querySelector('.topbar').scrollWidth - document.querySelector('.topbar').clientWidth"), 0)
        self.close_settings()

    def test_2_looks_clean_at_1366_and_1920(self):
        self.look_clean(1366, 768)
        self.look_clean(1920, 1080)
        self.browser.set_viewport(*self.viewport)

    def test_3_live_saving_resets_and_next_start_tags(self):
        b = self.browser
        self.reset_all()
        self.reload()
        tagged = {}
        for cat in ('general', 'editor', 'library'):
            self.open_settings(cat)
            tagged.update(b.eval("Object.fromEntries([...document.querySelectorAll('#settingsPanel .settings-row')].filter(r=>r.querySelector('.settings-tag')).map(r=>[r.dataset.key,r.querySelector('.settings-tag').title]))"))
        self.assertEqual(sorted(tagged), ['check_updates', 'open_browser', 'open_last', 'remember_tools', 'start_focus'])
        self.assertIn('next time you start Asset Lab', tagged['open_browser'])
        self.assertIn('reload this page', tagged['start_focus'])
        # A real click on a switch saves at once.
        self.open_settings('general')
        self.assertTrue(b.eval("document.getElementById('settingsReset').disabled"))
        b.click_selector('#set-confirm_install')
        until(lambda: self.saved('confirm_install') is True)
        b.wait_for(SAVED)
        b.wait_for("document.querySelector('.settings-row[data-key=confirm_install] .settings-saved').classList.contains('on')")
        self.assertEqual(self.stored(), {'confirm_install': True})
        self.change('game_open', 'close')
        self.assertEqual(b.text('#settingsReset'), 'Reset General')
        b.wait_for("!document.getElementById('settingsReset').disabled")
        b.click_selector('#settingsReset')
        b.wait_for("document.getElementById('dialog').open")
        self.assertEqual(b.text('#dialogTitle'), 'Reset General?')
        b.click_selector('#dialogAccept')
        until(lambda: self.saved('confirm_install') is False and self.saved('game_open') == 'ask')
        b.wait_for("!document.getElementById('set-confirm_install').checked && document.getElementById('settingsReset').disabled")
        # Reset everything, from another category.
        self.change('compact_cards', True)
        self.change('history_states', 100)
        self.open_settings('sound')
        b.click_selector('#settingsResetAll')
        b.wait_for("document.getElementById('dialog').open")
        b.click_selector('#dialogAccept')
        until(lambda: self.server.get('/api/settings')['changed'] == [])
        self.assertEqual(self.stored(), {})
        b.wait_for("!document.documentElement.hasAttribute('data-compact-cards')")
        self.close_settings()

    def test_4_values_that_do_not_fit_never_reach_the_file(self):
        b = self.browser
        self.open_settings('sound')
        b.set_value('#set-sound_fade_in', 99)                                # the box keeps it in range
        until(lambda: self.saved('sound_fade_in') == 10.0)
        b.set_value('#set-sound_fade_in', 'soon')                            # not a number: back to what is saved
        b.wait_for("document.getElementById('set-sound_fade_in').value === '10'")
        # The API refuses what the window never sends.
        answers = b.eval("const t=S.token,send=v=>fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Token':t},body:JSON.stringify(v)}).then(async r=>[r.status,(await r.json()).error]);"
                         "await Promise.all([send({values:{sound_fade_in:11}}),send({values:{ui_size:130}}),send({values:{language:'xx'}}),send({values:{open_browser:'yes'}}),send({values:{nope:1}})])")
        self.assertEqual([a[0] for a in answers], [400] * 5)
        self.assertEqual(answers[0][1], 'Fade in: use 0 to 10 s.')
        self.assertIn('Interface size: choose', answers[1][1])
        self.assertEqual(self.stored(), {'sound_fade_in': 10.0})
        # A refusal is shown on the setting's row and the control goes back (45 characters put in past the box's limit).
        self.open_settings('text')
        b.set_value('#set-font_sample', 'x' * 45)
        b.wait_for("document.querySelector('.settings-row[data-key=font_sample] .settings-error')?.textContent === 'Example text: use at most 40 characters.'")
        b.wait_for("document.getElementById('settingsStatus').textContent === 'Not saved' && document.getElementById('set-font_sample').value === 'Cellmate'")
        self.assertEqual(self.saved('font_sample'), 'Cellmate')
        self.assertNoBrowserErrors(allow=['/api/settings'])                   # the refused requests are logged as failed loads
        self.reset_all()
        self.close_settings()

    def test_5_saved_values_survive_a_reload(self):
        b = self.browser
        self.reset_all()
        self.change('accent', 'pink')
        self.change('ui_size', 110)
        self.change('sound_fade_out', 1.5)
        self.reload()
        self.assertEqual(b.eval("document.documentElement.dataset.accent + '/' + document.documentElement.dataset.uiSize"), 'pink/110')
        self.open_settings('appearance')
        self.assertTrue(b.eval("document.querySelector(\"input[name='set-accent'][value=pink]\").checked"))
        self.assertEqual(b.eval("document.getElementById('set-ui_size').selectedOptions[0].textContent"), '110%')
        self.open_settings('sound')
        self.assertEqual(b.eval("document.getElementById('set-sound_fade_out').value"), '1.5')
        self.close_settings()

    def test_6_a_damaged_settings_file(self):
        b = self.browser
        self.put(accent='blue')
        f = Path(self.server.home) / 'settings.json'
        f.write_text('{"accent": "violet", oops', encoding='utf-8')
        self.reload()
        self.open_settings('about')
        b.wait_for("document.querySelector('#settingsPanel .settings-warn')?.textContent.includes('could not be read')")
        self.assertFalse(b.eval("document.documentElement.hasAttribute('data-accent')"))   # the defaults are in use
        self.assertEqual(self.saved('accent'), 'teal')
        self.assertEqual((f.parent / 'settings.json.bad').read_text(encoding='utf-8'), '{"accent": "violet", oops')
        self.change('accent', 'lime')                                          # the next change writes a good file
        self.assertEqual(self.stored(), {'accent': 'lime'})
        self.reload()
        self.open_settings('about')
        self.assertFalse(b.eval("!!document.querySelector('#settingsPanel .settings-warn')"))
        self.reset_all()
        self.reload()


# ----- General -------------------------------------------------------------------------------------------------------------
class GeneralTests(SettingsCase):
    def edit_and_reload(self):
        """A saved edit in the mod, so Install has something to put in the (fake) game."""
        key = self.server.asset_key('edit-sprite.png')
        art = np.zeros((64, 64, 4), np.uint8); art[..., 0] = 200; art[..., 3] = 255
        buf = io.BytesIO(); Image.fromarray(art, 'RGBA').save(buf, 'PNG')
        detail = self.server.get('/api/asset', key=key)
        self.server.post('/api/save', {'key': key, 'png': base64.b64encode(buf.getvalue()).decode(), 'revision': detail['revision']})
        self.reload()

    def restore(self):
        if self.server.get('/api/status')['active']: self.server.post('/api/restore', {})
        self.browser.eval('await status(); true')

    def install_click(self):
        self.browser.eval("document.activeElement && document.activeElement.blur()")
        self.browser.click_selector('#installButton')

    def installed(self): return self.browser.wait_for("!S.busy && !!S.status.active && S.status.active.state === 'installed'", 30)

    def test_1_language_is_saved_and_the_page_reloads(self):
        b = self.browser
        self.reset_all()
        self.reload()
        b.eval('window.__before = 1')
        self.change('language', 'es')
        b.wait_for('!window.__before && typeof S === "object" && !!S.token', 20, message='the page did not reload')
        b.wait_lab()
        b.wait_for("document.getElementById('settingsDialog').open && document.querySelector('#settingsPanel .settings-section')?.dataset.category === 'general'")
        self.assertEqual(self.stored(), {'language': 'es'})                    # what the launcher's start window reads
        self.assertEqual(b.eval("document.getElementById('set-language').selectedOptions[0].textContent"), 'Español')
        self.assertEqual(self.server.get('/api/settings')['values']['language'], 'es')
        if b.eval('!!window.LabI18n'):   # the translation (src/web/i18n.js) gets the new language with the reloaded page
            self.assertEqual(b.eval("document.documentElement.getAttribute('data-language')"), 'es')
        b.eval('window.__before = 1')
        self.change('language', '')                                            # Automatic: out of the file, Windows decides
        b.wait_for('!window.__before && typeof S === "object" && !!S.token', 20)
        b.wait_lab()
        self.assertEqual(self.stored(), {})
        self.close_settings()
        # A language whose dictionary (src/web/i18n/<code>.json) is not there yet stays English; that missing file is not
        # a Settings problem.
        self.assertNoBrowserErrors(allow=['/i18n/'])

    def test_2_ask_before_installing_or_restoring(self):
        b = self.browser
        self.reset_all()
        self.edit_and_reload()
        self.change('confirm_install', True)
        self.close_settings()
        self.install_click()
        b.wait_for("document.getElementById('dialog').open")
        self.assertEqual(b.text('#dialogTitle'), 'Install your mod in Mitosis?')
        b.click_selector('#dialog button[value=cancel]')
        b.settle(5)
        self.assertIsNone(self.server.get('/api/status')['active'])         # cancelled: nothing installed
        self.install_click()
        b.wait_for("document.getElementById('dialog').open")
        b.click_selector('#dialogAccept')
        self.installed()
        b.eval("document.activeElement && document.activeElement.blur()")
        b.click_selector('#restoreButton')
        b.wait_for("document.getElementById('dialog').open")
        self.assertEqual(b.text('#dialogTitle'), 'Restore the original game files?')
        b.click_selector('#dialogAccept')
        b.wait_for("!S.busy && !S.status.active", 30)
        # Off: Install goes ahead without asking.
        self.change('confirm_install', False)
        self.close_settings()
        self.install_click()
        self.installed()
        self.assertFalse(b.eval("document.getElementById('dialog').open"))
        self.restore()

    def test_3_if_mitosis_is_open(self):
        b = self.browser
        self.reset_all()
        self.edit_and_reload()
        # The fake game "runs" until the lab asks to close it (both answered in the page: nothing is ever closed for real).
        self.stub({'/api/game': '(()=>({running:!window.__closed}))', '/api/close-game': '(()=>{window.__closed=true;return {closed:true,forced:false};})'})
        self.install_click()
        b.wait_for("document.getElementById('dialog').open")
        self.assertEqual(b.text('#dialogTitle'), 'Mitosis is open')            # Ask me first
        b.click_selector('#dialog button[value=cancel]')
        b.settle(5)
        self.assertEqual((self.calls('/api/close-game'), self.server.get('/api/status')['active']), (0, None))
        self.change('game_open', 'close')
        self.close_settings()
        self.install_click()
        self.installed()                                                       # closed without asking, then installed
        self.assertEqual(self.calls('/api/close-game'), 1)
        self.assertFalse(b.eval("document.getElementById('dialog').open"))
        self.restore()
        self.reset_all()
        self.reload()

    def test_4_open_mitosis_after_installing(self):
        b = self.browser
        self.reset_all()
        self.edit_and_reload()
        # Two locks, so a test can never start Steam: the page answers /api/launch-game itself, and the browser blocks it.
        b.call('Network.setBlockedURLs', urls=['*/api/launch-game*'])
        self.stub({'/api/launch-game': {'opened': True}})
        self.install_click()
        self.installed()
        b.settle(5)
        self.assertEqual(self.calls('/api/launch-game'), 0)                    # off by default
        self.restore()
        self.change('launch_after_install', True)
        self.close_settings()
        self.install_click()
        self.installed()
        b.wait_for("(window.__calls||{})['/api/launch-game'] === 1", 10, message='Mitosis was not opened after installing')
        b.wait_for("document.getElementById('toast').textContent.includes('Opening Mitosis through Steam')")
        self.restore()
        self.reset_all()
        self.reload()
        b.call('Network.setBlockedURLs', urls=[])


# ----- Appearance --------------------------------------------------------------------------------------------------------
class AppearanceTests(SettingsCase):
    viewport = (1366, 768)

    def zoom(self, sel): return float(self.browser.eval(f"getComputedStyle(document.querySelector({json.dumps(sel)})).zoom"))

    def test_interface_size(self):
        b = self.browser
        self.reset_all()
        self.reload()
        width = b.eval("document.getElementById('settingsButton').getBoundingClientRect().width")
        self.change('ui_size', 125)
        self.assertEqual(b.eval("document.documentElement.dataset.uiSize"), '125')
        self.assertEqual(self.zoom('.topbar > .brand'), 1.25)
        self.assertEqual(self.zoom('#settingsDialog > .settings-body'), 1.25)  # the lab's own windows too
        self.assertEqual(self.zoom('.library > h1'), 1.25)
        self.assertEqual(self.zoom('#view'), 1)                                # the canvas keeps its size
        self.close_settings()
        self.assertAlmostEqual(b.eval("document.getElementById('settingsButton').getBoundingClientRect().width"), width * 1.25, delta=1)
        self.assertLessEqual(b.eval("document.querySelector('.topbar').scrollWidth - document.querySelector('.topbar').clientWidth"), 0)
        b.screenshot('settings-ui-size-125.png')
        self.open_settings('appearance')
        b.screenshot('settings-ui-size-125-window.png')
        self.assertLessEqual(b.eval("document.getElementById('settingsDone').getBoundingClientRect().bottom"), 768)
        self.change('ui_size', 90)
        self.assertEqual(self.zoom('.topbar > .brand'), 0.9)
        self.change('ui_size', 100)
        self.assertFalse(b.eval("document.documentElement.hasAttribute('data-ui-size')"))
        self.close_settings()

    def test_accent_color(self):
        b = self.browser
        self.change('accent', 'pink')                                          # a real click on the swatch
        self.assertEqual(b.eval("getComputedStyle(document.documentElement).getPropertyValue('--cyan').trim()"), '#ff8fcd')
        done = "getComputedStyle(document.getElementById('settingsDone')).backgroundColor"
        b.wait_for(f"{done} === 'rgb(255, 143, 205)'", message='the Done button did not turn pink')   # after its color transition
        self.close_settings()
        self.assertEqual(b.eval("getComputedStyle(document.getElementById('installButton')).backgroundColor"), 'rgb(255, 143, 205)')
        self.change('accent', 'teal')
        self.assertEqual(b.eval("getComputedStyle(document.documentElement).getPropertyValue('--cyan').trim()"), '#70e5d4')
        b.wait_for(f"{done} === 'rgb(112, 229, 212)'")
        self.close_settings()

    def test_reduce_motion(self):
        b = self.browser
        self.open_settings('appearance')
        duration = "getComputedStyle(document.getElementById('set-compact_cards')).transitionDuration"
        spinner = "getComputedStyle(document.querySelector('#busy .spinner')).animationName"
        self.assertNotEqual(b.eval(duration).split(',')[0], '0s')
        self.assertEqual(b.eval(spinner), 'spin')
        self.change('reduce_motion', True)
        self.assertEqual({d.strip() for d in b.eval(duration).split(',')}, {'0s'})
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('.settings-saved')).transitionDuration"), '0s')
        self.assertEqual(b.eval(spinner), 'none')                              # the busy spinner stands still
        self.change('reduce_motion', False)
        self.close_settings()

    def test_canvas_background(self):
        b = self.browser
        b.select_asset('edit-sprite.png')                                     # transparent around its shapes
        def color():
            b.settle(3)
            x, y = b.pixel_center(60, 2)
            return tuple(int(v) for v in b.screenshot_array(clip=(int(x), int(y), 1, 1))[0, 0, :3])
        expect = {'dark': {(0x25, 0x31, 0x3f), (0x2b, 0x39, 0x48)}, 'light': {(255, 255, 255), (0xd6, 0xdb, 0xe1)}, 'teal': {(0x0d, 0x28, 0x29)}, 'gray': {(128, 128, 128)}}
        self.assertIn(color(), expect['dark'])
        for choice in ('teal', 'light', 'gray', 'dark'):
            self.change('canvas_bg', choice)
            self.close_settings()
            self.assertIn(color(), expect[choice], choice)

    def test_compact_library_cards(self):
        b = self.browser
        columns = "new Set([...document.querySelectorAll('#assetList .asset-card')].map(c=>Math.round(c.getBoundingClientRect().left))).size"
        before = b.eval(columns)
        self.change('compact_cards', True)
        self.close_settings()
        self.assertEqual(b.eval(columns), 3)
        self.assertNotEqual(before, 3)
        self.assertTrue(b.eval("getComputedStyle(document.querySelector('#assetList .asset-card small')).display === 'none'"))
        self.change('compact_cards', False)
        self.close_settings()
        self.assertEqual(b.eval(columns), before)


# ----- Editor ------------------------------------------------------------------------------------------------------------
class EditorTests(SettingsCase):
    def states(self): return self.browser.eval('Ed.history.list().length')

    def test_undo_steps(self):
        b = self.browser
        self.reset_all()
        b.select_asset('edit-sprite.png')
        self.change('history_states', 20)
        self.close_settings()
        self.assertEqual(b.eval('Ed.history.limits.states'), 20)
        b.eval("for(let i=0;i<25;i++)Ed.menu.run('Layer/New Layer'); true")
        self.assertEqual(self.states(), 21)                                   # the opened image + the last 20 steps
        self.change('history_states', 200)
        self.close_settings()
        b.eval("for(let i=0;i<25;i++)Ed.menu.run('Layer/New Layer'); true")
        self.assertEqual(self.states(), 46)

    def test_undo_memory(self):
        b = self.browser
        fills = ("for(let i=0;i<9;i++)await Ed.applyToTarget('Fill '+i,g=>{g.fillStyle='rgb('+(i*20)+',80,160)';g.fillRect(0,0,g.canvas.width,g.canvas.height);}); true")
        self.change('history_memory', 256)
        self.close_settings()
        self.assertEqual(b.eval('Ed.history.limits.bytes'), 256 * 1024 * 1024)
        b.select_asset('edit-big.png')                                        # 2048 x 2048: every full fill keeps 32 MB
        b.eval(fills, timeout=120)
        self.assertLessEqual(b.eval('Ed.history.bytes'), 256 * 1024 * 1024)
        self.assertLess(self.states() - 1, 9)                                 # the oldest went
        self.change('history_memory', 1024)
        self.close_settings()
        b.select_asset('edit-big.png')
        b.eval(fills, timeout=120)
        self.assertEqual(self.states() - 1, 9)
        self.assertGreater(b.eval('Ed.history.bytes'), 256 * 1024 * 1024)
        b.select_asset('edit-sprite.png')                                     # let the big history go
        self.reset_all()
        self.reload()

    def grid_pixels(self):
        b = self.browser
        b.eval("Ed.view.redrawOverlay(); true")
        b.settle(3)
        return int((b.pixels("document.getElementById('viewOverlay')")[..., 3] > 0).sum())

    def test_pixel_grid(self):
        b = self.browser
        b.select_asset('edit-tiny.png')
        b.eval("Ed.setTool('hand'); Ed.view.setZoom(16); true")
        self.change('pixel_grid', True)
        self.close_settings()
        self.assertGreater(self.grid_pixels(), 100)                          # lines between the pixels from 800%
        self.change('pixel_grid', False)
        self.close_settings()
        self.assertFalse(b.eval('Ed.view.pixelGrid'))
        self.assertEqual(self.grid_pixels(), 0)
        b.eval("Ed.menu.run('View/Pixel Grid'); true")                       # View > Pixel Grid is the same choice
        until(lambda: self.saved('pixel_grid') is True)
        self.assertGreater(self.grid_pixels(), 100)

    def test_crisp_pixels(self):
        b = self.browser
        self.reset_all()
        self.reload()
        self.assertFalse(b.eval("document.getElementById('pixelated').checked"))
        self.change('sharp_pixels', True)
        self.assertTrue(b.eval("document.getElementById('pixelated').checked"))   # at once
        self.reload()
        self.assertTrue(b.eval("document.getElementById('pixelated').checked"))   # and from the start
        self.assertTrue(b.eval("Ed.menu.list().find(m=>m.path==='View/Crisp Pixels').checked"))
        self.change('sharp_pixels', False)
        self.reload()
        self.assertFalse(b.eval("document.getElementById('pixelated').checked"))

    def focus_off(self):
        if self.browser.eval("document.body.classList.contains('ed-focus-mode')"): self.browser.eval("Ed.menu.run('View/Focus Editor'); true")

    def test_start_in_focus_mode(self):
        b = self.browser
        self.change('start_focus', True)
        self.close_settings()
        self.focus_off()                                                      # left out of Focus mode...
        self.reload()
        b.wait_for("document.body.classList.contains('ed-focus-mode')", message='did not start in Focus mode')   # ...starts in it
        self.change('start_focus', False)
        self.close_settings()
        self.focus_off()
        self.reload()
        b.settle(10)
        self.assertFalse(b.eval("document.body.classList.contains('ed-focus-mode')"))   # the way it was left

    def test_remember_tool_settings(self):
        b = self.browser
        default = b.eval("Ed.tools.get('brush').options.find(o=>o.key==='size').default")
        b.eval("Ed.setToolOptions('brush', {size: 37}); true")
        self.reload()
        b.settle(10)
        self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 37)          # on: kept between visits
        self.change('remember_tools', False)
        self.close_settings()
        b.eval("Ed.setToolOptions('brush', {size: 37}); true")
        self.reload()
        b.wait_for(f"Ed.toolOptions('brush').size === {default}", message='the brush did not start fresh')
        self.change('remember_tools', True)
        self.close_settings()


# ----- Theme AI & drawing ------------------------------------------------------------------------------------------------
MODELS = {'ollama': True, 'models': [{'name': 'best:latest', 'thinking': False, 'size': 5e9}, {'name': 'small:4b', 'thinking': False, 'size': 2e9}],
          'families': [], 'sheets': 3, 'job': None, 'last': None, 'game': {'colors': [], 'hide': []}, 'design': {'available': True, 'missing': [], 'job': None}, 'planner_note': ''}


class ThemeAiTests(SettingsCase):
    allowed_errors = ('/api/design-image',)   # the stubbed drawing job has no pictures

    def open_drawer(self):
        b = self.browser
        if b.eval("document.getElementById('themeDrawer').hidden"): b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden && document.getElementById('themeModel').options.length > 0")

    def close_drawer(self):
        if not self.browser.eval("document.getElementById('themeDrawer').hidden"): self.browser.click_selector('#themeClose')

    def test_apply_plans_automatically(self):
        b = self.browser
        self.change('auto_apply', True)
        self.assertTrue(b.eval("document.getElementById('themeAuto').checked"))   # the drawer's Auto-apply box follows
        self.close_settings()
        self.open_drawer()
        b.click_selector('#themeAuto')                                          # and the setting follows the box
        until(lambda: self.saved('auto_apply') is False)
        self.close_drawer()

    def test_planner_model(self):
        b = self.browser
        self.reset_all()
        self.reload()
        self.stub({'/api/theme-status': MODELS})
        self.open_settings('theme')
        b.wait_for("document.getElementById('set-planner_model').options.length === 3")
        self.assertEqual(b.eval("[...document.getElementById('set-planner_model').options].map(o=>o.textContent)"), ['Automatic (best installed)', 'best:latest', 'small:4b'])
        self.change('planner_model', 'small:4b')
        self.close_settings()
        self.open_drawer()
        self.assertEqual(b.eval("document.getElementById('themeModel').value"), 'small:4b')   # what Theme AI plans with
        self.change('planner_model', '')
        self.assertEqual(b.eval("document.getElementById('themeModel').value"), 'best:latest')
        self.close_settings()
        self.close_drawer()
        self.put(planner_model='gone:7b')
        self.reload()
        self.stub({'/api/theme-status': MODELS})
        self.open_settings('theme')
        b.wait_for("[...document.getElementById('set-planner_model').options].some(o=>o.textContent==='gone:7b (not installed)')")
        b.wait_for("document.querySelector('.settings-row[data-key=planner_model] .settings-info').textContent.includes('not installed now')")
        self.close_settings()
        self.reset_all()

    def ask_for_a_drawing(self):
        b = self.browser
        key = self.server.asset_key('icon-b')
        job = {'id': 'f' * 32, 'state': 'complete', 'message': 'Ready.', 'prompt': 'a witch hat', 'targets': [key], 'names': ['icon-b'], 'frames': [[32, 32]],
               'variants': [{'index': 0, 'warning': ''}, {'index': 1, 'warning': ''}], 'tinted': True, 'tint': '', 'title': False, 'stretched': False, 'direction': '', 'total': 2}
        chat = {'plan': {'reply': '', 'operations': [], 'ui_colors': {}, 'hide': []}, 'source': 'design', 'note': '', 'preview': None,
                'design': {'prompt': 'a witch hat on icon-b', 'available': True, 'targets': [{'key': key, 'name': 'icon-b', 'size': [32, 32]}]}}
        self.stub({'/api/theme-status': MODELS, '/api/theme-chat': chat, '/api/design-start': dict(job, state='running', message='Preparing…'), '/api/design-job': job})
        self.open_drawer()
        b.click_selector('#themeInput')
        b.type_text('put a witch hat on icon-b')
        b.key('Enter')
        b.wait_for("!!document.querySelector('#themeLog .design-count')")

    def test_drawings_per_request_and_keep_it_white(self):
        b = self.browser
        self.reset_all()
        self.reload()
        self.change('drawings', 3)
        self.change('keep_white', True)
        self.close_settings()
        self.ask_for_a_drawing()
        self.assertEqual(b.eval("document.querySelector('#themeLog .design-count').value"), '3')
        b.eval("[...document.querySelectorAll('#themeLog .theme-actions button')].find(x=>x.textContent==='Draw it').click(); true")
        b.wait_for("!!document.querySelector('#themeLog .design-card')")
        self.assertEqual(self.bodies('/api/design-start')[-1]['count'], 3)
        white = "[...document.querySelectorAll('#themeLog .design-card label')].find(l=>l.textContent.includes('Keep it white')).querySelector('input').checked"
        self.assertTrue(b.eval(white))                                        # ticked for an icon the game colors
        b.wait_for("[...document.querySelectorAll('#themeLog .design-view img')].some(i=>i.src.includes('white=1'))")
        # Off: the box starts unticked.
        self.change('keep_white', False)
        self.close_settings()
        b.eval("document.querySelector('#themeLog .design-card').remove(); [...document.querySelectorAll('#themeLog .theme-actions button')].filter(x=>x.textContent==='Draw it').forEach(x=>x.disabled=false); true")
        b.eval("[...document.querySelectorAll('#themeLog .theme-actions button')].find(x=>x.textContent==='Draw it').click(); true")
        b.wait_for("!!document.querySelector('#themeLog .design-card')")
        self.assertFalse(b.eval(white))
        self.close_drawer()

    def test_auto_apply_starts_a_drawing_by_itself(self):
        b = self.browser
        self.reset_all()
        self.reload()
        self.change('auto_apply', True)
        self.change('drawings', 1)
        self.close_settings()
        self.ask_for_a_drawing()
        b.wait_for("(window.__calls||{})['/api/design-start'] === 1", message='the drawing did not start by itself')
        self.assertEqual(self.bodies('/api/design-start')[-1]['count'], 1)
        self.close_drawer()
        self.reset_all()


# ----- Text & fonts --------------------------------------------------------------------------------------------------------
class TextTests(SettingsCase):
    game_maker = staticmethod(font_game)

    def open_fonts(self):
        b = self.browser
        self.stub({}, record=['/api/font-preview'])
        b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden")
        b.click_selector('#fontsButton')
        b.wait_for("document.getElementById('fontsDialog').open", 20)
        b.wait_for("(window.__calls||{})['/api/font-preview'] >= 1", 20)

    def close_fonts(self):
        self.browser.eval("document.getElementById('fontsDialog').close(); true")
        self.browser.click_selector('#themeClose')

    def test_example_text(self):
        b = self.browser
        self.change('font_sample', 'ABBA')
        self.close_settings()
        self.open_fonts()
        self.assertEqual(b.eval("document.querySelector('#fontsDialog .fonts-text').value"), 'ABBA')
        self.assertEqual(self.bodies('/api/font-preview')[0]['text'], 'ABBA')   # the previews are drawn with it
        self.close_fonts()

    def test_preload_look_previews(self):
        b = self.browser
        self.reload()                                                         # no previews kept from before
        self.open_fonts()
        b.wait_for("(window.__calls||{})['/api/font-preview'] >= 6", 30, message='the looks were not fetched ahead')
        self.close_fonts()
        self.change('font_prefetch', False)
        self.reload()
        self.open_fonts()
        b.wait_for("!document.querySelector('#fontsDialog .waiting')", 10)
        time.sleep(2.5)                                                       # long enough for a few looks to be fetched if it were on
        self.assertEqual(self.calls('/api/font-preview'), 1)                  # only the look on screen
        b.eval("document.querySelector('#fontsDialog .fonts-look-next, #fontsDialog button[title=\"Next look\"]')?.click(); true")
        self.close_fonts()
        self.reset_all()


# ----- Sound ----------------------------------------------------------------------------------------------------------------
class SoundTests(SettingsCase):
    game_maker = staticmethod(sound_game)

    def open_sound(self, name):
        b = self.browser
        key = self.server.asset_key(name)
        b.eval(f'await select({json.dumps(key)}, true); true')
        b.wait_for(f"LabAudio.state().visible && LabAudio.state().key === {json.dumps(key)}")
        return key

    def song(self, seconds, rate=48000, name='song.wav'):
        path = Path(self.server.temp) / name
        sf.write(str(path), tone(523, seconds, rate, 0.1, 1), rate, subtype='PCM_16')
        return path

    def load(self, path, timeout=30):
        """Choose a sound file in the editor and wait until that one (a new source) is in use and measured."""
        b = self.browser
        before = b.eval('LabAudio.state().source')
        b.set_files('#audioFile', [path])
        b.wait_for(f"LabAudio.state().source && LabAudio.state().source !== {json.dumps(before)} && LabAudio.state().report "
                   f"&& document.getElementById('aeSrcName').textContent.includes({json.dumps(Path(path).name)})", timeout, message=f'{path} did not load')

    def test_new_sounds_start_with_the_sound_settings(self):
        b = self.browser
        self.change('sound_match', False)
        self.change('sound_fade_in', 0.5)
        self.change('sound_fade_out', 1.0)
        self.close_settings()
        self.open_sound('st1.ogg')
        self.load(self.song(6.0))
        s = b.eval('LabAudio.state().set')
        self.assertEqual((s['fade_in'], s['fade_out'], s['match']), (0.5, 1.0, False))
        self.assertEqual((b.eval("document.getElementById('aeIn').value"), b.eval("document.getElementById('aeOut').value")), ('0.5', '1'))
        self.assertFalse(b.eval("document.getElementById('aeMatch').checked"))
        self.assertIsNone(b.eval('LabAudio.state().report.match_db'))         # the lab renders it that way too
        self.open_sound('click.ogg')                                           # a short effect gets shorter fades
        self.load(self.song(0.3, name='blip.wav'))
        s = b.eval('LabAudio.state().set')
        self.assertAlmostEqual(s['fade_in'] + s['fade_out'], 0.3, delta=0.001)
        self.assertAlmostEqual(s['fade_out'] / s['fade_in'], 2.0, delta=0.01)
        self.reset_all()

    def test_page_links(self):
        b = self.browser
        self.open_sound('st1.ogg')
        self.assertIn('YouTube', b.eval("document.getElementById('aeLink').placeholder"))
        self.change('page_links', False)
        self.assertIn('page links are off in Settings', b.eval("document.getElementById('aeLink').placeholder"))
        self.change('page_links', True)
        self.assertIn('YouTube', b.eval("document.getElementById('aeLink').placeholder"))
        self.close_settings()

    def test_longest_sound(self):
        b = self.browser
        self.change('sound_max_minutes', 1)
        self.close_settings()
        self.open_sound('st1.ogg')
        self.load(self.song(70.0, 22050, 'long.wav'), 60)
        self.assertEqual(b.eval('LabAudio.state().set.end'), 60.0)              # the sound is one minute long now
        b.wait_for("document.getElementById('aeSrcInfo').textContent.startsWith('1:00.0') && "
                   "document.getElementById('aeSrcInfo').textContent.includes('only the first 1:00.0 of 1:10.0 is used')")
        self.reset_all()

    def test_link_helper_buttons(self):
        b = self.browser
        self.open_settings('sound')
        b.wait_for("document.querySelector('.settings-row[data-key=remove_helper] .settings-info').textContent === 'Not installed.'")
        self.assertTrue(b.eval("document.getElementById('set-update_helper').disabled && document.getElementById('set-remove_helper').disabled"))
        self.close_settings()


# ----- Library -----------------------------------------------------------------------------------------------------------
class LibraryTests(SettingsCase):
    game_maker = staticmethod(tile_game)

    def cards(self): return self.browser.eval("document.querySelectorAll('#assetList .asset-card').length")

    def test_assets_per_page(self):
        b = self.browser
        total = int(b.eval("parseInt(document.getElementById('resultCount').textContent.replace(/\\D/g,''))"))
        self.assertGreater(total, 120)
        self.assertEqual(self.cards(), 72)
        self.change('page_size', 48)
        self.close_settings()
        b.wait_for("document.querySelectorAll('#assetList .asset-card').length === 48")
        self.assertEqual(b.text('#pageNumber'), f'1 / {math.ceil(total / 48)}')
        b.click_selector('#nextPage')
        b.wait_for(f"document.getElementById('pageNumber').textContent === '2 / {math.ceil(total / 48)}'")
        self.assertEqual(self.cards(), 48)
        self.change('page_size', 120)
        self.close_settings()
        b.wait_for("document.querySelectorAll('#assetList .asset-card').length === 120")
        self.assertEqual(b.text('#pageNumber'), f'1 / {math.ceil(total / 120)}')
        self.reset_all()
        self.reload()
        self.assertEqual(self.cards(), 72)

    def test_show_copies(self):
        b = self.browser
        count = "parseInt(document.getElementById('resultCount').textContent.replace(/\\D/g,''))"
        before = b.eval(count)
        self.change('show_copies', True)
        b.wait_for("document.getElementById('showAll').checked")
        self.close_settings()
        b.wait_for(f"{count} > {before}", message='copies and leftovers were not listed')
        self.reload()
        self.assertTrue(b.eval("document.getElementById('showAll').checked"))   # ticked from the start
        self.assertGreater(b.eval(count), before)
        self.change('show_copies', False)
        self.close_settings()
        b.wait_for(f"!document.getElementById('showAll').checked && {count} === {before}")

    def test_reopen_the_last_asset(self):
        b = self.browser
        self.change('open_last', True)
        self.close_settings()
        key = b.select_asset('tile-005.png')
        self.reload()
        b.wait_for(f"S.asset && S.asset.key === {json.dumps(key)}", 20, message='the last asset was not reopened')
        self.change('open_last', False)
        self.close_settings()
        b.select_asset('edit-photo.png')
        self.reload()
        b.settle(20)
        self.assertIsNone(b.eval('S.asset'))


# ----- Storage & privacy, About --------------------------------------------------------------------------------------------
class StorageTests(SettingsCase):
    def test_storage_privacy_and_about(self):
        b = self.browser
        key = self.server.asset_key('edit-sprite.png')
        art = np.zeros((64, 64, 4), np.uint8); art[..., 1] = 200; art[..., 3] = 255
        buf = io.BytesIO(); Image.fromarray(art, 'RGBA').save(buf, 'PNG')
        self.server.post('/api/save', {'key': key, 'png': base64.b64encode(buf.getvalue()).decode(), 'revision': 'original'})
        for _ in range(7): self.server.post('/api/build', {})
        self.open_settings('storage')
        b.wait_for("[...document.querySelectorAll('.settings-usage b')].length === 3 && document.querySelector('.settings-usage b').textContent !== '…'")
        b.wait_for("document.querySelector('.settings-row[data-key=keep_builds] .settings-info').textContent.startsWith('7 builds in your workspace now')")
        self.assertIn('api.github.com', b.text('#settingsPrivacy'))
        self.change('keep_builds', 5)
        b.wait_for("document.getElementById('settingsNote').textContent.startsWith('2 old builds deleted')")
        self.assertEqual(self.server.get('/api/settings-usage')['builds']['count'], 5)
        b.wait_for("document.querySelector('.settings-row[data-key=keep_builds] .settings-info').textContent.startsWith('5 builds')")
        b.click_selector('#set-clear_caches')
        b.wait_for("document.getElementById('settingsNote').textContent.startsWith('Caches cleared')")
        self.assertTrue(b.eval("!!document.getElementById('set-open_workspace')"))   # (not clicked: it opens File Explorer)
        self.open_settings('about')
        self.assertIn('Asset Lab ', b.text('#settingsPanel .settings-facts'))
        self.assertEqual(b.eval("document.querySelectorAll('#settingsPanel .settings-folder button').length") >= 5, True)
        if b.eval("!!document.getElementById('settingsNotices')"):
            b.click_selector('#settingsNotices')
            b.wait_for("!!document.querySelector('dialog.settings-notices[open] table, dialog.settings-notices[open] h3')")
            b.key('Escape')
            b.wait_for("!document.querySelector('dialog.settings-notices')")
        self.close_settings()
        self.reset_all()
