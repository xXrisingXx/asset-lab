import base64
import io
from PIL import Image
from harness import EditorTestCase


class Builds(EditorTestCase):
    """The build picker, Start over, switching back, Revert to original, and Focus Editor folding Edit & Inspect."""
    viewport = (1366, 768)

    def paint(self, key, color):
        d = self.server.get('/api/asset', key=key)
        buf = io.BytesIO(); Image.new('RGBA', (d['width'], d['height']), color).save(buf, 'PNG')
        self.server.post('/api/save', {'key': key, 'revision': d['revision'], 'png': base64.b64encode(buf.getvalue()).decode(), 'recipe': {}})

    def reloads(self, action):
        """Something that switches builds: the page reloads itself, then the lab loads again."""
        self.browser._navigate(action, 30)
        self.browser.wait_lab()

    def sprite(self):
        return next(r['key'] for r in self.server.assets('', all='1') if r.get('kind') == 'sprite' and r.get('editable'))

    def test_a_start_over_and_switch_back(self):
        b = self.browser
        key = self.sprite()
        self.paint(key, (200, 30, 30, 255)); b.reload()
        b.wait_for("document.querySelector('#buildButton b').textContent === 'My build'")
        b.click_selector('#buildButton')
        b.wait_for("document.getElementById('buildsDialog').open && document.querySelectorAll('.build-row').length === 1")
        b.screenshot('builds-dialog.png')
        b.click_selector('#startOverButton')
        b.wait_for("document.getElementById('dialog').open")
        self.reloads(lambda: b.click_selector('#dialogAccept'))
        b.wait_for("document.querySelector('#buildButton b').textContent === 'Build 2'")
        self.assertEqual(self.server.get('/api/status')['staged'], [])          # a new build is the original art
        b.click_selector('#buildButton')
        b.wait_for("document.querySelectorAll('.build-row').length === 2")
        self.reloads(lambda: b.eval("document.querySelector('.build-row:not(.active) [data-act=open]').click()"))
        b.wait_for("document.querySelector('#buildButton b').textContent === 'My build'")
        self.assertEqual([s['key'] for s in self.server.get('/api/status')['staged']], [key])

    def test_ab_rename_a_build(self):
        # Rename asks for the name (the browser's own prompt, answered here) and the picker shows it.
        b = self.browser
        b.click_selector('#buildButton')
        b.wait_for("document.getElementById('buildsDialog').open && !!document.querySelector('.build-row.active [data-act=rename]')")
        b.eval("window.prompt = () => 'Spooky test'; true")
        b.click_selector('.build-row.active [data-act=rename]')
        b.wait_for("document.querySelector('#buildButton b').textContent === 'Spooky test'")
        self.assertTrue(b.eval("[...document.querySelectorAll('.build-row')].some(r => r.textContent.includes('Spooky test'))"))
        b.eval("window.prompt = () => 'My build'; true")                           # and back, for the other tests
        b.click_selector('.build-row.active [data-act=rename]')
        b.wait_for("document.querySelector('#buildButton b').textContent === 'My build'")
        b.eval("document.getElementById('buildsDialog').close(); true")

    def test_b_revert_the_open_asset(self):
        b = self.browser
        key = self.sprite()
        b.select_asset(next(r['name'] for r in self.server.assets('', all='1') if r['key'] == key))
        b.wait_for("!document.getElementById('revertButton').disabled")
        b.click_selector('#revertButton')
        b.wait_for("document.getElementById('dialog').open")
        b.click_selector('#dialogAccept')
        b.wait_for("document.getElementById('revertButton').disabled")
        self.assertEqual(self.server.get('/api/status')['staged'], [])
        self.assertFalse(self.server.get('/api/asset', key=key)['changed'])

    def test_c_focus_editor_folds_edit_and_inspect(self):
        b = self.browser
        b.eval("localStorage.removeItem('lab-inspector-min')")
        b.eval("document.body.classList.add('ed-focus-mode')")
        b.wait_for("document.body.classList.contains('inspector-min')")
        b.eval("document.body.classList.remove('ed-focus-mode')")
        b.wait_for("!document.body.classList.contains('inspector-min')")
