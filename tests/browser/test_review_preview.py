import base64
import io
from PIL import Image
from harness import EditorTestCase


class ReviewAndPreview(EditorTestCase):
    viewport = (1100, 640)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A dozen saved edits, so the Review list scrolls.
        rows = [r for r in cls.server.assets('', all='1') if r.get('kind') == 'sprite' and r.get('editable')][:12]
        for r in rows:
            d = cls.server.get('/api/asset', key=r['key'])
            buf = io.BytesIO(); Image.new('RGBA', (d['width'], d['height']), (200, 30, 30, 255)).save(buf, 'PNG')
            cls.server.post('/api/save', {'key': r['key'], 'revision': d['revision'], 'png': base64.b64encode(buf.getvalue()).decode(), 'recipe': {}})
        cls.edited = len(rows)
        cls.browser.open_lab(cls.server)

    def open_review(self):
        b = self.browser
        b.click_selector('#reviewButton')
        b.wait_for("document.getElementById('reviewDialog').open && document.querySelectorAll('#reviewDialog .review-card').length > 0")

    def test_a_remove_keeps_the_place_in_the_list(self):
        b = self.browser
        self.assertGreaterEqual(self.edited, 8)
        self.open_review()
        b.eval("(()=>{const s=document.querySelector('#reviewDialog .review-body');s.scrollTop=s.scrollHeight;})()")
        before = b.eval("document.querySelector('#reviewDialog .review-body').scrollTop")
        self.assertGreater(before, 50, 'the list scrolls')
        b.eval("[...document.querySelectorAll('#reviewDialog .review-card .review-toggle')].pop().click()")
        b.wait_for("[...document.querySelectorAll('#reviewDialog .review-card')].pop().classList.contains('out')")
        after = b.eval("document.querySelector('#reviewDialog .review-body').scrollTop")
        self.assertLessEqual(abs(after - before), 2, 'Remove did not jump back to the top')
        b.eval("[...document.querySelectorAll('#reviewDialog .review-card .review-toggle')].pop().click()")   # put it back
        b.wait_for("![...document.querySelectorAll('#reviewDialog .review-card')].pop().classList.contains('out')")
        b.key('Escape')

    def test_b_remove_all_and_put_all_back(self):
        b = self.browser
        self.open_review()
        self.assertEqual(b.text('#reviewAll'), 'Remove all')
        b.click_selector('#reviewAll')
        b.wait_for("document.getElementById('reviewAll').textContent === 'Put all back'")
        self.assertTrue(b.eval("[...document.querySelectorAll('#reviewDialog .review-card')].every(c => c.classList.contains('out'))"))
        self.assertEqual(self.server.get('/api/status')['staged'], [])
        b.click_selector('#reviewAll')
        b.wait_for("document.getElementById('reviewAll').textContent === 'Remove all'")
        self.assertFalse(b.eval("[...document.querySelectorAll('#reviewDialog .review-card')].some(c => c.classList.contains('out'))"))
        self.assertEqual(len(self.server.get('/api/status')['staged']), self.edited)
        b.key('Escape')

    def test_c_game_preview_shows_both_screens(self):
        b = self.browser
        b.click_selector('#previewButton')
        b.wait_for("(()=>{const i=document.querySelector('#previewDialog img');return document.getElementById('previewDialog').open && i.complete && i.naturalWidth > 0;})()")
        self.assertIn('screen=menu', b.eval("document.querySelector('#previewDialog img').src"))
        b.eval("document.querySelector('#previewDialog [data-screen=match]').click()")
        b.wait_for("(()=>{const i=document.querySelector('#previewDialog img');return i.src.includes('screen=match') && i.complete && i.naturalWidth > 0;})()")
        self.assertTrue(b.eval("!document.querySelector('#previewDialog .game-preview-options').hidden"))
        b.eval("document.querySelector('#previewDialog [data-mode=dark]').click(); document.querySelector('#previewDialog [data-controls=phone]').click()")
        b.wait_for("(()=>{const i=document.querySelector('#previewDialog img');return /mode=dark/.test(i.src) && /controls=phone/.test(i.src) && i.complete && i.naturalWidth > 0;})()")
        b.screenshot('game-preview.png')
        b.eval("document.querySelector('#previewDialog [data-screen=menu]').click()")
        self.assertTrue(b.eval("document.querySelector('#previewDialog .game-preview-options').hidden"))   # only a match has these
        b.key('Escape')

    def test_d_automatic_canvas_background(self):
        b = self.browser
        # Faint black triangles (like the menu pattern) get a white checker; a white icon keeps the dark one.
        pick = """(async (rgba) => {const c=document.createElement('canvas');c.width=c.height=16;const g=c.getContext('2d');g.fillStyle=rgba;g.fillRect(0,0,16,16);
                   const im=new Image();im.src=c.toDataURL();await im.decode();canvasBackground('auto');autoBackdrop(im);return checkerColors[0];})"""
        self.assertEqual(b.eval(f"{pick}('rgba(0,0,0,0.05)')"), '#ffffff')
        self.assertEqual(b.eval(f"{pick}('rgba(255,255,255,1)')"), '#25313f')
        self.assertFalse(b.eval("document.getElementById('previewDialog').open"))
