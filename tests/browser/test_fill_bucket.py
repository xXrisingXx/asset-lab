"""The Paint Bucket, Edit > Fill (Shift+F5, Alt/Ctrl+Backspace), Edit > Stroke and Edit > Define Pattern of
src/web/editor/fill.js, driven with real mouse and keyboard input and checked against the independent numpy references in
test_fill_refs.py (flood fill, anti-aliased coverage, W3C compositing, distances).

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_fill_bucket.py" -v
"""
import json
import numpy as np
from harness import EditorTestCase, rgba
from test_fill_refs import (FillHelpers, bucket_region, bucket_coverage, paint, over, through_selection, solid, noise_image,
                            distance_to, dialog_box_js)

A = (200, 120, 40, 255)
FG, BG = '#2050e0', '#f0d030'


def test_picture():
    """96 x 64, opaque: color A, a square close to A (within 32 per channel), an island of another color, a one pixel
    diagonal line that splits the picture for 4-connectivity, and color A again on the other side of the line."""
    img = solid(96, 64, A)
    img[10:30, 10:30] = (220, 110, 55, 255)
    img[40:50, 20:32] = (0, 200, 255, 255)
    img[22:26, 34:52] = (30, 30, 30, 255)          # a bar with concave corners where it meets nothing
    for y in range(64): img[y, 50 + y if 50 + y < 96 else 95] = (20, 20, 20, 255)
    return img


def sprite_picture():
    """64 x 64 with a transparent background, an opaque disc with a soft edge and a half-transparent bar."""
    Y, X = np.mgrid[:64, :64] + .5
    d = np.hypot(X - 30, Y - 30)
    img = np.zeros((64, 64, 4), np.uint8)
    img[..., :3] = (230, 60, 40)
    img[..., 3] = np.clip((16 - d) * 128 + 128, 0, 255).astype(np.uint8)
    img[50:58, 6:40] = (60, 200, 90, 128)
    return img


class BucketTests(FillHelpers, EditorTestCase):
    viewport = (1600, 1000)

    def setUp(self):
        super().setUp()
        self.browser.eval(f"Ed.setColor('fg', '{FG}'); Ed.setColor('bg', '{BG}'); true")

    def bucket(self, **o):
        opts = dict(source='fg', mode='normal', opacity=100, tolerance=0, contiguous=True, antialias=False, all=False)
        opts.update(o)
        self.tool('bucket', **opts)

    def click_px(self, x, y, n=None):
        b = self.browser
        before = b.eval('Ed.history.index')
        b.click(*b.pixel_center(x, y))
        if n is not False: b.wait_for(f'Ed.history.index === {before + 1} && !Ed.history.open', 20); b.settle()

    # ----- Paint Bucket ------------------------------------------------------------------------------------------------
    def test_01_bucket_tolerance_contiguous_antialias_and_undo(self):
        img = test_picture()
        b = self.fresh(img)
        b.key('g')
        if b.eval('Ed.tool') != 'bucket': b.key('G')   # Shift+G cycles the Gradient and the Paint Bucket
        self.assertEqual(b.eval('Ed.tool'), 'bucket')
        cases = [dict(tolerance=0, contiguous=True, antialias=False), dict(tolerance=32, contiguous=True, antialias=False),
                 dict(tolerance=32, contiguous=True, antialias=True), dict(tolerance=0, contiguous=False, antialias=False),
                 dict(tolerance=0, contiguous=False, antialias=True)]
        for c in cases:
            b = self.fresh(img)
            self.bucket(**c)
            self.click_px(5, 40)
            self.assertEqual(self.labels(), ['Open', 'Paint Bucket'])
            reg = bucket_region(img, 5, 40, c['tolerance'], c['contiguous'])
            ref = paint(img, FG, bucket_coverage(reg, c['antialias']))
            self.assertPixelsClose(b.editor_pixels(), ref, max_abs=1, msg=str(c))
            if c['tolerance'] == 0 and c['contiguous']:
                self.assertFalse(reg[5, 80], 'the one pixel diagonal line stops a contiguous fill')
        self.undo_all()
        self.assertPixelsClose(b.editor_pixels(), img)
        self.assertNoBrowserErrors()

    def test_02_bucket_options_from_the_bar_mode_opacity_and_selection(self):
        img = noise_image(80, 60, seed=3)
        img[:, :40] = (90, 140, 200, 255)
        b = self.fresh(img)
        self.bucket(tolerance=10)
        # the options bar: tolerance and contiguous, then Mode and Anti-alias under More
        b.set_value('.ed-optionsbar .ed-field[data-key="tolerance"] input[type=number]', 0)
        b.set_value('.ed-optionsbar .ed-field[data-key="opacity"] input[type=number]', 60)
        b.click_selector('.ed-optionsbar .fill-more')
        b.wait_for("!!document.querySelector('.fill-pop select[name=mode]')")
        b.set_value('.fill-pop select[name=mode]', 'multiply')
        b.click_selector('.fill-pop .ed-field[data-key="antialias"] input')
        b.key('Escape')
        o = b.eval("Ed.toolOptions('bucket')")
        self.assertEqual((o['tolerance'], o['opacity'], o['mode'], o['antialias'], o['contiguous']), (0, 60, 'multiply', True, True))
        self.click_px(10, 10)
        reg = bucket_region(img, 10, 10, 0)
        self.assertPixelsClose(b.editor_pixels(), paint(img, FG, bucket_coverage(reg, True), .6, 'multiply'), max_abs=1)
        # the selection limits the fill; a click outside it does nothing
        b = self.fresh(img)
        self.bucket(tolerance=0, antialias=True)
        b.eval('Ed.select.rect(5, 5, 20, 30)')
        n = len(self.labels())
        self.click_px(30, 50, n=False)
        b.settle(6)
        self.assertEqual(len(self.labels()), n, 'a click outside the selection does nothing')
        self.click_px(10, 10)
        self.assertEqual(self.labels()[-1], 'Paint Bucket')
        sel = np.zeros((60, 80), bool)
        sel[5:35, 5:25] = True
        reg = bucket_region(img, 10, 10, 0, sel=sel)
        self.assertPixelsClose(b.editor_pixels(), paint(img, FG, bucket_coverage(reg, True) * sel), max_abs=1)
        self.assertNoBrowserErrors()

    def test_03_bucket_transparency_lock_all_layers_pattern_and_mask(self):
        img = sprite_picture()
        # a click on transparent pixels fills the transparent area (alpha within tolerance)
        b = self.fresh(img)
        self.bucket(tolerance=32)
        self.click_px(2, 2)
        reg = bucket_region(img, 2, 2, 32)
        self.assertPremulClose(b.editor_pixels(), paint(img, FG, reg.astype(float)))
        # Lock Transparent Pixels: soft edges keep their alpha, transparent pixels stay clear
        b = self.fresh(img)
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}, {history: false}); true")
        self.bucket(tolerance=255, contiguous=False)
        self.click_px(30, 30)
        out = b.editor_pixels()
        ref = paint(img, FG, np.ones((64, 64)), lock_alpha=True)
        self.assertPremulClose(out, ref)
        self.assertTrue((out[..., 3] == img[..., 3]).all(), 'the alpha is kept')
        # All Layers: the area comes from the picture, the paint goes into the empty layer on top
        b = self.fresh(test_picture())
        b.eval("Ed.layers.add({name: 'Paint'}); true")
        self.bucket(tolerance=0, all=True)
        self.click_px(5, 40)
        reg = bucket_region(test_picture(), 5, 40, 0)
        self.assertPixelsClose(b.editor_pixels(1), paint(np.zeros((64, 96, 4), np.uint8), FG, reg.astype(float)), max_abs=1)
        self.assertPixelsClose(b.editor_pixels(0), test_picture())
        # without All Layers the empty layer is one transparent area
        b.key('Ctrl+Z'); b.wait_for("Ed.history.label(Ed.history.index) !== 'Paint Bucket'")
        self.bucket(tolerance=0, all=False)
        self.click_px(5, 40)
        self.assertTrue((b.editor_pixels(1)[..., 3] == 255).all())
        # a pattern, tiled from the document's corner
        pic = test_picture()
        b = self.fresh(pic)
        self.bucket(tolerance=0, source='pattern', pattern='bricks')
        self.click_px(5, 40)
        tile = b.pixels("Ed.patterns.get('bricks').canvas")
        th, tw = tile.shape[:2]
        tiled = np.tile(tile, (64 // th + 1, 96 // tw + 1, 1))[:64, :96]
        reg = bucket_region(pic, 5, 40, 0)
        self.assertPixelsClose(b.editor_pixels(), np.where(reg[..., None], over(pic, tiled), pic), max_abs=1)
        # on a layer mask the gray of the foreground color is painted
        b = self.fresh(pic)
        b.eval("Ed.layers.addMask(0, 'reveal'); Ed.setTarget('mask'); Ed.setColor('fg', '#404040'); true")
        self.bucket(tolerance=0, source='fg')
        self.click_px(5, 40)
        mask = b.editor_pixels('mask')[..., 3]
        self.assertTrue((mask == 64).all(), 'one mask value: the whole mask is the clicked area')
        self.assertNoBrowserErrors()

    # ----- Edit > Fill -------------------------------------------------------------------------------------------------
    def test_04_edit_fill_dialog_and_quick_fills(self):
        img = noise_image(48, 40, seed=5)
        b = self.fresh(img)
        dlg = 'dialog[data-ed-dialog="Fill"]'
        b.key('Shift+F5')
        b.wait_for(f"!!document.querySelector('{dlg}')")
        b.set_value(f'{dlg} select[name=contents]', 'bg')
        b.set_value(f'{dlg} select[name=mode]', 'multiply')
        b.set_value(f'{dlg} .ed-field[data-key="opacity"] input[type=number]', 50)
        b.click_selector(f'{dlg} button.primary')
        self.wait_history(2)
        self.assertEqual(self.labels(), ['Open', 'Fill'])
        self.assertPixelsClose(b.editor_pixels(), paint(img, BG, np.ones((40, 48)), .5, 'multiply'), max_abs=1)
        # the dialog remembers the last settings; Color... uses the picked color; Escape cancels
        b.key('Shift+F5')
        b.wait_for(f"!!document.querySelector('{dlg}')")
        self.assertEqual(b.eval(f"document.querySelector('{dlg} select[name=contents]').value"), 'bg')
        b.set_value(f'{dlg} select[name=contents]', 'color')
        b.set_value(f'{dlg} input[name=contents-color]', '#00ff80')
        b.set_value(f'{dlg} select[name=mode]', 'normal')
        b.set_value(f'{dlg} .ed-field[data-key="opacity"] input[type=number]', 100)
        b.key('Escape')
        b.wait_for(f"!document.querySelector('{dlg}')")
        self.assertEqual(len(self.labels()), 2)
        # every content with a selection (params skip the dialog)
        for use, color in (('fg', FG), ('bg', BG), ('black', '#000000'), ('white', '#ffffff'), ('gray', '#808080'), ('color', '#00ff80')):
            b = self.fresh(img)
            b.eval('Ed.select.rect(4, 6, 20, 10)')
            b.eval(f"Ed.menu.run('Edit/Fill...', {{use: '{use}', color: '#00ff80'}})")
            self.wait_history(3)
            sel = np.zeros((40, 48)); sel[6:16, 4:24] = 1
            self.assertPixelsClose(b.editor_pixels(), paint(img, color, sel), max_abs=0, msg=use)
        # Alt+Backspace (foreground), Ctrl+Backspace (background), with Shift transparent pixels stay clear
        spr = sprite_picture()
        for keys, color, keep in (('Alt+Backspace', FG, False), ('Ctrl+Backspace', BG, False), ('Alt+Shift+Backspace', FG, True), ('Ctrl+Shift+Backspace', BG, True)):
            b = self.fresh(spr)
            b.key(keys)
            self.wait_history(2)
            self.assertEqual(self.labels(), ['Open', 'Fill'], keys)
            self.assertPremulClose(b.editor_pixels(), paint(spr, color, np.ones((64, 64)), lock_alpha=keep), msg=keys)
        self.undo_all()
        self.assertPremulClose(b.editor_pixels(), spr, max_abs=0)
        self.assertNoBrowserErrors()

    def test_05_edit_fill_special_modes_pattern_preserve_and_refusals(self):
        spr = sprite_picture()
        ones = np.ones((64, 64))
        b = self.fresh(spr)
        b.eval("Ed.menu.run('Edit/Fill...', {use: 'fg', mode: 'behind'})"); self.wait_history(2)
        # Behind paints only where the layer is transparent: the old pixels go over the new color
        self.assertPremulClose(b.editor_pixels(), over(paint(np.zeros_like(spr), FG, ones), spr))
        b = self.fresh(spr)
        b.eval("Ed.menu.run('Edit/Fill...', {use: 'fg', mode: 'clear', opacity: 50})"); self.wait_history(2)
        ref = spr.copy(); ref[..., 3] = np.floor(spr[..., 3] * .5 + .5)
        self.assertPremulClose(b.editor_pixels(), ref)
        b = self.fresh(solid(64, 64, '#000000'))
        b.eval("Ed.menu.run('Edit/Fill...', {use: 'white', mode: 'dissolve', opacity: 30})"); self.wait_history(2)
        out = b.editor_pixels()
        white = (out[..., :3] == 255).all(-1)
        self.assertTrue((white | (out[..., :3] == 0).all(-1)).all(), 'Dissolve paints whole pixels or none')
        self.assertAlmostEqual(white.mean(), .3, delta=.05)
        # preserve transparency and a pattern
        b = self.fresh(spr)
        b.eval("Ed.menu.run('Edit/Fill...', {use: 'pattern', pattern: 'checker', preserve: true})"); self.wait_history(2)
        tile = b.pixels("Ed.patterns.get('checker').canvas")
        tiled = np.tile(tile, (64 // tile.shape[0] + 1, 64 // tile.shape[1] + 1, 1))[:64, :64]
        ref = spr.copy(); ref[..., :3] = np.where(spr[..., 3:] > 0, tiled[..., :3], spr[..., :3])
        self.assertPremulClose(b.editor_pixels(), ref)
        # a hidden layer is refused with a message, nothing changes
        b = self.fresh(spr)
        b.eval("Ed.layers.set(0, {visible: false}, {history: false}); true")
        self.assertFalse(b.eval("Ed.menu.run('Edit/Fill...', {use: 'fg'})"))
        b.settle(4)
        self.assertEqual(self.labels(), ['Open'])
        b.eval("Ed.layers.set(0, {visible: true}, {history: false}); true")
        self.assertNoBrowserErrors()

    # ----- Edit > Stroke and Define Pattern ------------------------------------------------------------------------------
    def test_06_edit_stroke_inside_center_outside(self):
        img = solid(64, 48, '#ffffff')
        Y, X = np.mgrid[:48, :64] + .5
        sel = np.zeros((48, 64), bool); sel[10:36, 12:44] = True
        sel |= np.hypot(X - 50, Y - 34) < 9
        for where, width in (('inside', 3), ('center', 4), ('outside', 2), ('center', 1)):
            b = self.fresh(img)
            mask = np.zeros((48, 64, 4), np.uint8); mask[..., :3] = 255; mask[..., 3] = sel * 255
            b.eval(f"(() => {{ const d = {b.push_pixels(mask)}, c = Ed.util.canvas(64, 48); Ed.util.write(c, d); Ed.select.set(c); return true; }})()")
            b.eval(f"Ed.menu.run('Edit/Stroke...', {{width: {width}, location: '{where}', color: '#ff0000'}})")
            self.wait_history(3)
            self.assertEqual(self.labels()[-1], 'Stroke')
            d_in, d_out = distance_to(sel, ~sel), distance_to(~sel, sel)   # centre distances to the other side
            half = width / 2 if where == 'center' else width
            cov = np.zeros((48, 64))
            if where != 'outside': cov = np.where(sel, np.clip(half - d_in + 1, 0, 1), cov)
            if where != 'inside': cov = np.where(~sel, np.clip(half - d_out + 1, 0, 1), cov)
            self.assertPixelsClose(b.editor_pixels(), paint(img, '#ff0000', cov), max_abs=1, msg=f'{where} {width}')
        # the dialog (real input) without a selection follows the layer's edge
        spr = sprite_picture()
        b = self.fresh(spr)
        b.eval("Ed.select.none(); true")
        b.eval("Ed.menu.run('Edit/Stroke...'); true")
        dlg = 'dialog[data-ed-dialog="Stroke"]'
        b.wait_for(f"!!document.querySelector('{dlg}')")
        b.set_value(f'{dlg} .ed-field[data-key="width"] input[type=number]', 2)
        b.click_selector(f'{dlg} input[name=location][value=outside] + span')
        b.set_value(f'{dlg} input[name=color]', '#0000ff')
        b.click_selector(f'{dlg} button.primary')
        self.wait_history(2)
        inside = spr[..., 3] >= 128
        cov = np.where(~inside, np.clip(2 - distance_to(~inside, inside) + 1, 0, 1), 0)
        self.assertPremulClose(b.editor_pixels(), paint(spr, '#0000ff', cov))
        self.undo_all()
        self.assertPremulClose(b.editor_pixels(), spr, max_abs=0)
        self.assertNoBrowserErrors()

    def test_07_define_pattern_then_fill_with_it(self):
        img = noise_image(40, 30, seed=11)
        b = self.fresh(img)
        b.eval("localStorage.removeItem('ed-patterns'); Ed.select.rect(2, 3, 6, 4); true")
        b.eval("Ed.menu.run('Edit/Define Pattern...'); true")
        dlg = 'dialog[data-ed-dialog="Pattern Name"]'
        b.wait_for(f"!!document.querySelector('{dlg}')")
        b.set_value(f'{dlg} input[name=name]', 'Noise bit')
        b.click_selector(f'{dlg} button.primary')
        b.wait_for("Ed.patterns.list().some(p => p.name === 'Noise bit' && p.canvas)")
        pid = b.eval("Ed.patterns.list().find(p => p.name === 'Noise bit').id")
        b.eval('Ed.select.none()')
        # pick it in the Paint Bucket's pattern picker
        self.bucket(source='pattern', tolerance=255, contiguous=False)
        b.click_selector('.ed-optionsbar .fill-pat-swatch')
        b.click_selector(f'.fill-pop .fill-swatch[data-pattern="{pid}"]')
        self.assertEqual(b.eval("Ed.toolOptions('bucket').pattern"), pid)
        self.click_px(20, 20)
        tile = img[3:7, 2:8]
        tiled = np.tile(tile, (30 // 4 + 1, 40 // 6 + 1, 1))[:30, :40]
        self.assertPixelsClose(b.editor_pixels(), tiled, max_abs=0)
        b.eval("localStorage.removeItem('ed-patterns')")
        self.assertNoBrowserErrors()

    # ----- layout ------------------------------------------------------------------------------------------------------
    def test_08_options_bar_and_dialogs_fit(self):
        b = self.fresh(noise_image(64, 64))
        self.tool('bucket', source='pattern')
        self.assertOptionsFit(['bucket'], shot='fill-bucket')
        self.tool('bucket', source='fg')
        b.set_viewport(1366, 768)
        try:
            for title, path in (('Fill', 'Edit/Fill...'), ('Stroke', 'Edit/Stroke...')):
                b.eval(f"Ed.menu.run('{path}'); true")
                b.wait_for(f"!!document.querySelector('dialog[data-ed-dialog=\"{title}\"]')")
                b.settle(3)
                box = b.eval(dialog_box_js(title))
                self.assertTrue(box['left'] >= 0 and box['top'] >= 0 and box['right'] <= box['vw'] and box['bottom'] <= box['vh'], f'{title} fits: {box}')
                self.assertEqual(box['wide'], [], title)
                b.screenshot(f'fill-dialog-{title.lower()}-1366x768.png')
                b.key('Escape')
                b.wait_for("!document.querySelector('dialog[open]')")
        finally:
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()
