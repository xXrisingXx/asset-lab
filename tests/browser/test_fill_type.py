"""The Type tool of src/web/editor/fill-type.js (T) with real clicks and typing. The type layer's pixels are compared with a
reference drawn by the test itself (the browser's own fillText with the expected font, color, alignment, baseline and 120%
leading: no module code), plus numpy checks of colors and anti-aliasing. Also: editing a type layer again, cancel,
commit by clicking away, undo, a moved layer, a painted layer, save and reload, Layer > Rasterize and the options bar.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_fill_type.py" -v
"""
import json
import numpy as np
from harness import EditorTestCase, premultiplied
from test_fill_refs import FillHelpers, solid, over


def font_css(size, style='regular', family='Arial'):
    return ('italic ' if 'italic' in style else '') + ('bold ' if 'bold' in style else '') + f'{size}px "{family}", sans-serif'


class TypeTests(FillHelpers, EditorTestCase):
    viewport = (1600, 1000)

    def type_opts(self, **o):
        opts = dict(font='Arial', style='regular', size=20, color='#c02040', align='left', antialias=True)
        opts.update(o)
        self.tool('type', **opts)
        return opts

    def ref(self, w, h, lines, x, y, o):
        """The text drawn straight onto a canvas by the browser: the reference for a type layer."""
        js = (f"(() => {{ const c = document.createElement('canvas'); c.width = {w}; c.height = {h}; const g = c.getContext('2d');"
              f" g.font = {json.dumps(font_css(o['size'], o['style'], o['font']))}; g.fillStyle = {json.dumps(o['color'])}; g.textAlign = {json.dumps(o['align'])};"
              f" g.textBaseline = 'alphabetic'; {json.dumps(lines)}.forEach((s, i) => g.fillText(s, {x}, {y} + i * {round(o['size'] * 1.2)})); return c; }})()")
        a = self.browser.pixels(js)
        if not o.get('antialias', True):
            a = a.copy()
            a[..., 3] = np.where(a[..., 3] >= 128, 255, 0)
        return a

    def click_at(self, x, y):
        b = self.browser
        b.click(*b.image_to_client(x, y))
        b.settle(2)

    def type_new(self, x, y, text_lines, commit='Enter'):
        b = self.browser
        n = b.eval('Ed.history.index')
        self.click_at(x, y)
        b.wait_for('Ed.typeLayers.editing()')
        for i, line in enumerate(text_lines):
            if i: b.key('Shift+Enter')
            b.type_text(line)
        b.settle(2)
        if commit:
            b.key(commit)
            b.wait_for(f'Ed.history.index === {n + 1} && !Ed.typeLayers.editing()')
            b.settle(2)

    def assertText(self, actual, expected, msg=''):
        self.assertPixelsClose(premultiplied(actual), premultiplied(expected), max_abs=1, msg=msg)
        self.assertGreater(int((expected[..., 3] > 0).sum()), 20, 'the reference shows text')

    # --------------------------------------------------------------------------------------------------------------------
    def test_01_click_type_and_enter_make_a_type_layer(self):
        b = self.fresh(solid(160, 80, '#ffffff'))
        b.key('t')
        b.wait_for("Ed.tool === 'type'")
        o = self.type_opts()
        self.type_new(10.4, 40.3, ['Hi there', '2nd line'])
        self.assertEqual(self.labels(), ['Open', 'Type Tool'])
        self.assertEqual(b.eval('Ed.layers.active().name'), 'Hi there')
        t = b.eval('Ed.layers.active().data.text')
        self.assertEqual({k: t[k] for k in ('text', 'font', 'style', 'size', 'color', 'align', 'antialias', 'x', 'y')},
                         {'text': 'Hi there\n2nd line', 'font': 'Arial', 'style': 'regular', 'size': 20, 'color': '#c02040', 'align': 'left', 'antialias': True, 'x': 10, 'y': 40})
        layer = b.editor_pixels(1)
        self.assertText(layer, self.ref(160, 80, ['Hi there', '2nd line'], 10, 40, o))
        solid_px = layer[..., 3] == 255
        self.assertTrue((layer[solid_px][:, :3] == (192, 32, 64)).all(), 'opaque text pixels are the text color')
        rows = np.nonzero(layer[..., 3].any(1))[0]
        self.assertTrue(24 <= rows.min() <= 27 and 61 <= rows.max() <= 70, f'two lines around baselines 40 and 64: rows {rows.min()}..{rows.max()}')
        self.assertPixelsClose(b.editor_pixels(), over(solid(160, 80, '#ffffff'), layer), max_abs=1)
        b.wait_for("[...document.querySelectorAll('#edPanel-layers .fill-badge')].map(e => e.textContent).join() === 'T'", message='a T badge on the type layer')
        self.undo_all()
        self.assertEqual(b.eval('Ed.layers.count()'), 1)
        self.assertNoBrowserErrors()

    def test_02_alignment_style_size_antialias_and_options_while_typing(self):
        W, H = 200, 90
        b = self.fresh(solid(W, H, (0, 0, 0, 0)))
        for align, x in (('center', 100), ('right', 190)):
            o = self.type_opts(align=align, size=24, style='bold italic', color='#2060ff')
            self.type_new(x, 40, ['Mid', 'Wider line'])
            self.assertText(b.editor_pixels(), self.ref(W, H, ['Mid', 'Wider line'], x, 40, o), align)
            b.eval('Ed.history.undo()')
            b.wait_for('Ed.layers.count() === 1')
        # anti-alias off: every pixel fully in or out
        o = self.type_opts(antialias=False, size=30, color='#000000')
        self.type_new(5, 50, ['Pixel'])
        a = b.editor_pixels()
        self.assertTrue(np.isin(a[..., 3], (0, 255)).all())
        self.assertText(a, self.ref(W, H, ['Pixel'], 5, 50, o), 'no anti-aliasing')
        b.eval('Ed.history.undo()'); b.wait_for('Ed.layers.count() === 1')
        # the options bar while typing: color, size and alignment apply to the whole text, typing goes on
        self.type_opts(size=20, color='#000000', align='left')
        self.type_new(10, 40, ['Live'], commit=None)
        b.set_value('.ed-optionsbar .ed-field[data-key="color"] input', '#ff8800')
        b.set_value('.ed-optionsbar .ed-field[data-key="size"] input', 32)
        b.click_selector('.ed-optionsbar .fill-align button[data-align="center"]')
        b.settle(3)
        self.assertTrue(b.eval('Ed.typeLayers.editing()'), 'still typing')
        b.wait_for("document.activeElement.classList.contains('fill-type-input')")
        b.type_text('ly')
        b.key('Enter')
        b.wait_for('Ed.layers.count() === 2 && !Ed.typeLayers.editing()')
        o = dict(font='Arial', style='regular', size=32, color='#ff8800', align='center')
        self.assertText(b.editor_pixels(1), self.ref(W, H, ['Lively'], 10, 40, o), 'options changed while typing')
        self.assertEqual(self.labels()[-1], 'Type Tool')
        self.assertNoBrowserErrors()

    def test_03_edit_again_cancel_click_away_and_undo(self):
        W, H = 160, 80
        b = self.fresh(solid(W, H, '#ffffff'))
        o = self.type_opts()
        self.type_new(20, 40, ['Score'])
        first = b.editor_pixels(1)
        # click inside the text: edit it, the caret where clicked; End, type, then click away commits
        self.click_at(24, 34)
        b.wait_for('Ed.typeLayers.editing()')
        self.assertEqual(b.eval("Ed.layers.active().name"), 'Score')
        b.key('End')
        b.type_text(' 99')
        self.click_at(150, 75)
        b.wait_for('!Ed.typeLayers.editing() && Ed.history.index === 2')
        b.settle(2)
        self.assertEqual(self.labels(), ['Open', 'Type Tool', 'Edit Type Layer'])
        self.assertEqual(b.eval('Ed.layers.count()'), 2, 'clicking away only commits')
        self.assertEqual(b.eval('Ed.layers.active().name'), 'Score 99', 'the layer name follows the text')
        self.assertText(b.editor_pixels(1), self.ref(W, H, ['Score 99'], 20, 40, o))
        # Escape cancels an edit
        self.click_at(30, 34)
        b.wait_for('Ed.typeLayers.editing()')
        b.key('Ctrl+A'); b.type_text('Gone')
        b.key('Escape')
        b.wait_for('!Ed.typeLayers.editing()')
        b.settle(2)
        self.assertText(b.editor_pixels(1), self.ref(W, H, ['Score 99'], 20, 40, o), 'unchanged after Escape')
        self.assertEqual(len(self.labels()), 3)
        # a new text cancelled with Escape adds nothing; an empty text adds nothing
        n = b.eval('Ed.history.index')
        self.click_at(40, 70); b.wait_for('Ed.typeLayers.editing()'); b.type_text('Nope'); b.key('Escape'); b.settle(2)
        self.click_at(40, 70); b.wait_for('Ed.typeLayers.editing()'); b.key('Enter'); b.settle(2)
        self.assertEqual((b.eval('Ed.history.index'), b.eval('Ed.layers.count()')), (n, 2))
        # undo the edit, then the layer
        b.key('Ctrl+Z'); b.wait_for('Ed.history.index === 1')
        self.assertEqual(b.eval('Ed.layers.get(1).data.text.text'), 'Score')
        self.assertEqual(b.eval('Ed.layers.get(1).name'), 'Score')
        self.assertPixelsClose(b.editor_pixels(1), first)
        b.key('Ctrl+Z'); b.wait_for('Ed.history.index === 0')
        self.assertEqual(b.eval('Ed.layers.count()'), 1)
        self.assertNoBrowserErrors()

    def test_04_a_moved_layer_follows_and_a_painted_layer_asks(self):
        W, H = 160, 80
        b = self.fresh(solid(W, H, '#ffffff'))
        o = self.type_opts()
        self.type_new(20, 40, ['Move'])
        # what the Move tool does to the pixels: shift them by (7, 5)
        b.eval("""(() => { const L = Ed.layers.active(), d = Ed.doc; Ed.history.record('Move', tx => { tx.touch(L, 'pixels', {x: 0, y: 0, w: d.width, h: d.height});
            const c = Ed.util.canvas(d.width, d.height); c.getContext('2d').drawImage(L.canvas, 7, 5); const g = L.canvas.getContext('2d'); g.clearRect(0, 0, d.width, d.height); g.drawImage(c, 0, 0); Ed.update(); }); return true; })()""")
        self.click_at(30, 39)     # on the moved text, outside where it was typed
        b.wait_for('Ed.typeLayers.editing()')
        b.key('End'); b.type_text('d'); b.key('Enter')
        b.wait_for('!Ed.typeLayers.editing() && Ed.history.index === 3')
        self.assertText(b.editor_pixels(1), self.ref(W, H, ['Moved'], 27, 45, o), 'the text is drawn where it was moved to')
        self.assertEqual(b.eval('[Ed.layers.active().data.text.x, Ed.layers.active().data.text.y]'), [27, 45])
        # painted on by another tool: editing asks first
        b.eval("""(() => { const L = Ed.layers.active(); Ed.history.record('Brush Tool', tx => { tx.touch(L, 'pixels', {x: 0, y: 0, w: 20, h: 20});
            const g = L.canvas.getContext('2d'); g.fillStyle = '#00ff00'; g.fillRect(2, 2, 6, 6); Ed.update(); }); return true; })()""")
        self.click_at(30, 39)
        b.wait_for("document.getElementById('dialog').open")
        self.assertIn('changed by other tools', b.text('#dialogText'))
        b.click_selector('#dialog button[value=cancel]')
        b.settle(3)
        self.assertFalse(b.eval('Ed.typeLayers.editing()'))
        self.click_at(30, 39)
        b.wait_for("document.getElementById('dialog').open")
        b.click_selector('#dialogAccept')
        b.wait_for('Ed.typeLayers.editing()')
        b.key('End'); b.type_text('!'); b.key('Enter')
        b.wait_for('!Ed.typeLayers.editing() && Ed.history.index === 5')
        self.assertText(b.editor_pixels(1), self.ref(W, H, ['Moved!'], 27, 45, o), 'drawn again: the green paint is gone')
        self.assertNoBrowserErrors()

    def test_05_save_reload_rasterize_and_fill_tools_ask(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        o = self.type_opts(size=14, color='#ffee00')
        self.type_new(6, 20, ['Saved'])
        want = b.editor_pixels(1)
        b.key('Ctrl+S')
        b.wait_for("!S.dirty && !S.busy && S.asset.revision !== 'original'", timeout=30)
        b.reload()
        b.select_asset('edit-sprite.png')
        self.assertEqual(b.eval('Ed.layers.count()'), 2)
        self.assertEqual(b.eval('Ed.layers.get(1).data.text.text'), 'Saved')
        self.assertPixelsClose(b.editor_pixels(1), want)
        # still editable after the reload
        self.tool('type')
        self.click_at(10, 16)
        b.wait_for('Ed.typeLayers.editing()')
        b.key('End'); b.type_text('!'); b.key('Enter')
        b.wait_for('!Ed.typeLayers.editing() && Ed.history.label(Ed.history.index) === "Edit Type Layer"')
        self.assertText(b.editor_pixels(1), self.ref(64, 64, ['Saved!'], 6, 20, o))
        # Layer > Rasterize > Type
        self.assertTrue(b.eval("Ed.menu.list().find(m => m.path === 'Layer/Rasterize/Type').enabled"))
        b.eval("Ed.menu.run('Layer/Rasterize/Type')")
        b.wait_for("Ed.history.label(Ed.history.index) === 'Rasterize Type'")
        self.assertFalse(b.eval('!!Ed.layers.get(1).data.text'))
        b.wait_for("document.querySelectorAll('#edPanel-layers .fill-badge').length === 0", message='the T badge goes away')
        b.key('Ctrl+Z'); b.wait_for('!!Ed.layers.get(1).data.text')
        # layers.js' Rasterize > Layer knows type layers too (when layers.js is loaded)
        if b.eval("!!Ed.registerRasterizer && !!Ed.menu.get('Layer/Rasterize/Layer')"):
            b.eval("Ed.menu.run('Layer/Rasterize/Layer')")
            b.wait_for('!Ed.layers.get(1).data.text')
            b.key('Ctrl+Z'); b.wait_for('!!Ed.layers.get(1).data.text')
        # a fill tool on a type layer asks to rasterize it first
        self.tool('bucket', source='fg', tolerance=0, contiguous=True, mode='normal', opacity=100, antialias=False, all=False)
        b.click(*b.pixel_center(60, 60))
        b.wait_for("document.getElementById('dialog').open")
        self.assertEqual(b.text('#dialogTitle'), 'Rasterize Type')
        b.click_selector('#dialogAccept')
        b.wait_for("Ed.history.label(Ed.history.index) === 'Paint Bucket'")
        self.assertFalse(b.eval('!!Ed.layers.get(1).data.text'))
        self.assertEqual(self.labels()[-2:], ['Rasterize Type', 'Paint Bucket'])
        self.assertNoBrowserErrors()

    def test_06_options_bar_fonts_and_screenshot(self):
        b = self.fresh(solid(200, 100, '#203040'))
        fonts = b.eval('Ed.typeLayers.fonts()')
        self.assertIn('Arial', fonts)
        self.type_opts(size=28, color='#ffffff')
        self.assertOptionsFit(['type'], shot='fill-type')
        b.set_viewport(1366, 768)
        try:
            b.eval('Ed.view.fit()'); b.settle(3)
            self.type_new(20, 60, ['Asset Lab'], commit=None)
            b.screenshot('fill-type-editing-1366x768.png')
            b.key('Enter')
            b.wait_for('!Ed.typeLayers.editing()')
            self.assertEqual(b.eval("Ed.tools.get('type').options.find(o => o.key === 'font').options.map(x => x.value)"), fonts)
            fetched = b.eval("performance.getEntriesByType('resource').map(r => r.name).filter(n => /\\.(woff2?|ttf|otf)(\\?|$)/i.test(n) || !n.startsWith(location.origin))")
            self.assertEqual(fetched, [], 'no fonts or anything else from other places')
        finally:
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()
