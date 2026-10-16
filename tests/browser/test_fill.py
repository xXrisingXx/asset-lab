"""Fills (src/web/editor/fill.js) end to end in headless Edge with real mouse and keyboard input: the Gradient tool (all five
styles against numpy formulas, multi-stop, opacity stops, midpoints, reverse, dither, Shift, modes, the selection), the
Gradient Editor, the Paint Bucket (tolerance, contiguous, 4-connectivity, anti-alias, all layers, patterns, modes), Edit > Fill
(Shift+F5) with its shortcuts, Edit > Stroke (inside / center / outside), speed on 2048 x 2048 sheets and screenshots.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_fill*.py" -v
"""
import json
import numpy as np
from harness import EditorTestCase, SHIFT
from test_fill_refs import KINDS, BW, MULTI, FillHelpers, gradient_t, eval_stops, gradient_rgba, over, solid, noise_image, through_selection, dialog_box_js


class FillTests(FillHelpers, EditorTestCase):
    viewport = (1600, 1000)

    def overlay_pixels(self):
        b = self.browser
        if b.eval("document.getElementById('viewOverlay').hidden"): return 0
        return int(b.pixels("document.getElementById('viewOverlay')")[..., 3].astype(bool).sum())

    # ----- gradients: the shared engine ----------------------------------------------------------------------------------
    def test_01_gradient_sample_matches_the_reference(self):
        b = self.fresh(solid(32, 32))
        t = np.linspace(0, 1, 401)
        got = np.array(b.eval(f'[...Array(401).keys()].map(i => Ed.gradients.sample({json.dumps(MULTI)}, i / 400))'))
        ref = gradient_rgba(MULTI, t)
        self.assertLess(np.abs(np.floor(got + .5) - ref).max(), 1.0001, 'multi-stop, midpoints (incl. clamped 2%), duplicate position, opacity stops')
        # a midpoint at 25% puts the 50% mix a quarter of the way: weight = P ** (ln .5 / ln .25) = sqrt(P)
        half = b.eval("Ed.gradients.sample({stops:[{at:0,color:'#000000',mid:.25},{at:1,color:'#ffffff'}]}, .25)")
        self.assertAlmostEqual(half[0], 127.5, 6)
        # stops that follow the foreground and background colors
        b.eval("Ed.setColor('fg', '#102030'); Ed.setColor('bg', '#f0e0d0')")
        fgbg = b.eval("Ed.gradients.presets[0]")
        self.assertEqual(fgbg['name'], 'Foreground to Background')
        self.assertEqual(b.eval("Ed.gradients.sample(Ed.gradients.presets[0], 0).map(Math.round)"), [16, 32, 48, 255])
        self.assertEqual(b.eval("Ed.gradients.sample(Ed.gradients.presets[0], 1).map(Math.round)"), [240, 224, 208, 255])
        b.eval("Ed.setColor('fg', '#000000'); Ed.setColor('bg', '#ffffff')")
        # image(): the five styles, pixel centres, reverse
        for kind in KINDS:
            img = b.pixels(f"Ed.gradients.image({json.dumps(MULTI)}, '{kind}', 10.5, 12.25, 50, 30, {{rect: {{x: 0, y: 0, w: 64, h: 48}}, reverse: true}})")
            ref = gradient_rgba(MULTI, gradient_t(kind, 64, 48, 10.5, 12.25, 50, 30), reverse=True)
            self.assertLessEqual(int(np.abs(img.astype(int) - ref).max()), 1, kind)
        names = [p['name'] for p in b.eval('Ed.gradients.presets')]
        for n in ('Foreground to Background', 'Foreground to Transparent', 'Black, White', 'Spectrum', 'Chrome'):
            self.assertIn(n, names)
        self.assertEqual(b.eval("Ed.fields.get('gradient') ? 'yes' : 'no'"), 'yes')
        self.assertNoBrowserErrors()

    def test_02_gradient_tool_draws_all_five_styles_with_a_real_drag(self):
        b = self.fresh(solid(128, 96))
        b.key('g')
        if b.eval('Ed.tool') != 'gradient': b.key('G')   # Shift+G cycles Gradient / Paint Bucket
        self.assertEqual(b.eval('Ed.tool'), 'gradient')
        for i, kind in enumerate(KINDS):
            b = self.fresh(solid(128, 96))
            self.tool('gradient', gradient=BW, mode='normal', opacity=100, reverse=False, dither=False, transparency=True)
            b.click_selector(f'.fill-kind[data-kind="{kind}"]')   # the style buttons in the options bar
            self.assertEqual(b.eval("Ed.toolOptions('gradient').kind"), kind)
            x0, y0, x1, y1 = 40.25, 50.5, 90.75, 20.0
            p0, p1 = b.image_to_client(x0, y0), b.image_to_client(x1, y1)
            b.mouse_move(*p0)
            b.mouse_down(*p0)
            for k in range(1, 6): b.mouse_move(p0[0] + (p1[0] - p0[0]) * k / 5, p0[1] + (p1[1] - p0[1]) * k / 5)
            b.settle()
            self.assertGreater(self.overlay_pixels(), 20, 'the drag shows a line')
            self.assertEqual(self.labels(), ['Open'], 'nothing is drawn until the button is released')
            b.mouse_up(*p1)
            self.wait_history(2)
            self.assertEqual(self.labels(), ['Open', 'Gradient'])
            ref = gradient_rgba(BW, gradient_t(kind, 128, 96, x0, y0, x1, y1))
            self.assertPixelsClose(b.editor_pixels(), ref, max_abs=1, msg=kind)
        b.key('Ctrl+Z')
        b.wait_for("Ed.history.index === 0")
        self.assertPixelsClose(b.editor_pixels(), solid(128, 96))
        self.assertNoBrowserErrors()

    def test_03_gradient_multi_stop_reverse_opacity_mode_shift_selection_dither(self):
        bg = noise_image(120, 80)
        b = self.fresh(bg)
        # multi-stop with opacity stops and midpoints, reversed, on a noisy background
        self.tool('gradient', gradient=MULTI, kind='linear', mode='normal', opacity=100, reverse=True, dither=False, transparency=True)
        b.drag_image([(10, 40), (110, 40)], center=False)
        self.wait_history(2)
        src = gradient_rgba(MULTI, gradient_t('linear', 120, 80, 10, 40, 110, 40), reverse=True)
        self.assertPixelsClose(b.editor_pixels(), over(bg, src), max_abs=2)
        # Transparency off ignores the opacity stops
        b = self.fresh(bg)
        self.tool('gradient', reverse=False, transparency=False)
        b.drag_image([(10, 40), (110, 40)], center=False)
        self.wait_history(2)
        self.assertPixelsClose(b.editor_pixels(), gradient_rgba(MULTI, gradient_t('linear', 120, 80, 10, 40, 110, 40), transparency=False), max_abs=1)
        # opacity 50% with Multiply (a canvas mode) and Linear Dodge (a per-pixel mode)
        for mode in ('multiply', 'linear-dodge'):
            b = self.fresh(bg)
            self.tool('gradient', gradient=BW, kind='radial', mode=mode, opacity=50, transparency=True)
            b.drag_image([(60, 40), (100, 40)], center=False)
            self.wait_history(2)
            src = gradient_rgba(BW, gradient_t('radial', 120, 80, 60, 40, 100, 40))
            self.assertPixelsClose(b.editor_pixels(), over(bg, src, mode, .5), max_abs=2, msg=mode)
        # Shift constrains the drag to 45 degree steps (here: horizontal)
        b = self.fresh(bg)
        self.tool('gradient', gradient=BW, kind='linear', mode='normal', opacity=100)
        b.drag_image([(20, 30), (60, 34), (100, 36)], center=False, modifiers=SHIFT)
        self.wait_history(2)
        self.assertPixelsClose(b.editor_pixels(), gradient_rgba(BW, gradient_t('linear', 120, 80, 20, 30, 100, 30)), max_abs=1)
        # the selection limits it
        b = self.fresh(bg)
        b.eval('Ed.select.rect(30, 20, 40, 30)')
        b.drag_image([(0, 0), (120, 0)], center=False)
        self.wait_history(3)
        out = b.editor_pixels()
        inside = np.zeros((80, 120), bool)
        inside[20:50, 30:70] = True
        self.assertPixelsClose(out, bg, mask=~inside)
        self.assertPixelsClose(out, gradient_rgba(BW, gradient_t('linear', 120, 80, 0, 0, 120, 0)), max_abs=1, mask=inside)
        # dither: within one level of the exact blend, about zero on average, and not the plain rounding
        b = self.fresh(bg)
        self.tool('gradient', dither=True)
        b.drag_image([(0, 0), (120, 0)], center=False)
        self.wait_history(2)
        exact = eval_stops([0, 1], [[0, 0, 0], [255, 255, 255]], [.5, .5], gradient_t('linear', 120, 80, 0, 0, 120, 0))
        got = b.editor_pixels()[..., :3].astype(float)
        self.assertLess(np.abs(got - exact).max(), 1.0)
        self.assertLess(abs((got - exact).mean()), .08)
        self.assertGreater((got != np.floor(exact + .5)).mean(), .2)
        self.assertNoBrowserErrors()

    def test_04_gradient_editor_stops_midpoints_presets_and_new(self):
        b = self.fresh(solid(64, 32))
        b.eval("localStorage.removeItem('ed-gradient-presets')")
        self.tool('gradient', gradient=BW, kind='linear')
        b.click_selector('.fill-grad-swatch')
        b.wait_for("!!document.querySelector('dialog[data-ed-dialog=\"Gradient Editor\"]')")
        dlg = 'dialog[data-ed-dialog="Gradient Editor"]'
        b.click_selector(f'{dlg} .fill-swatch[data-preset="Black, White"]')
        self.assertEqual(b.eval(f"document.querySelectorAll('{dlg} .fill-ge-bottom .fill-ge-stop').length"), 2)
        # click under the bar at about 45% (50% is the midpoint diamond): a new color stop with the gradient's color there
        x0, y0 = b.center_of(f'{dlg} .fill-ge-bottom')
        r = b.eval(f"(() => {{const r = document.querySelector('{dlg} .fill-ge-bottom').getBoundingClientRect(); return [r.left, r.width];}})()")
        b.click(r[0] + r[1] * .45, y0)
        self.assertEqual(b.eval(f"document.querySelectorAll('{dlg} .fill-ge-bottom .fill-ge-stop').length"), 3)
        b.set_value(f'{dlg} input[name="stop-color"]', '#ff0000')
        b.set_value(f'{dlg} input[name="stop-location"]', 25)
        # drag that stop to about 75%
        sx, sy = b.center_of(f'{dlg} .fill-ge-bottom .fill-ge-stop.on')
        b.drag([(sx, sy), (r[0] + r[1] * .75, sy)])
        # the midpoint between the black stop and the red stop: drag it to about 25% of the way
        mids = b.eval(f"[...document.querySelectorAll('{dlg} .fill-ge-bottom .fill-ge-mid')].map(m => {{const q = m.getBoundingClientRect(); return [m.dataset.index, q.left + q.width / 2, q.top + q.height / 2];}})")
        first = [m for m in mids if m[0] == '0'][0]
        b.drag([(first[1], first[2]), (r[0] + r[1] * .75 * .25, first[2])])
        self.assertEqual(b.eval(f"document.querySelectorAll('{dlg} .fill-ge-bottom .fill-ge-stop').length"), 3)
        # an opacity stop at about 50%, set to 40%
        tx, ty = b.center_of(f'{dlg} .fill-ge-top')
        b.click(r[0] + r[1] * .5, ty)
        self.assertEqual(b.eval(f"document.querySelectorAll('{dlg} .fill-ge-top .fill-ge-stop').length"), 3)
        b.set_value(f'{dlg} input[name="stop-opacity"]', 40)
        # add a fourth color stop and delete it by dragging it off the bar
        b.click(r[0] + r[1] * .9, y0)
        self.assertEqual(b.eval(f"document.querySelectorAll('{dlg} .fill-ge-bottom .fill-ge-stop').length"), 4)
        sx2, sy2 = b.center_of(f'{dlg} .fill-ge-bottom .fill-ge-stop.on')
        b.drag([(sx2, sy2), (sx2, sy2 + 60)])
        self.assertEqual(b.eval(f"document.querySelectorAll('{dlg} .fill-ge-bottom .fill-ge-stop').length"), 3)
        # and one more removed with the Delete key
        b.click(r[0] + r[1] * .95, y0)
        b.key('Delete')
        self.assertEqual(b.eval(f"document.querySelectorAll('{dlg} .fill-ge-bottom .fill-ge-stop').length"), 3)
        # save it as a preset
        b.set_value(f'{dlg} input[name="gradient-name"]', 'Lava test')
        b.click_selector(f'{dlg} .fill-ge-new')
        self.assertEqual(b.eval("JSON.parse(localStorage.getItem('ed-gradient-presets')).map(p => p.name)"), ['Lava test'])
        self.assertTrue(b.eval(f"!!document.querySelector('{dlg} .fill-swatch[data-preset=\"Lava test\"]')"))
        b.click_selector(f'{dlg} button.primary')
        b.wait_for("!document.querySelector('dialog[open]')")
        g = b.eval("Ed.toolOptions('gradient').gradient")
        self.assertEqual(g['name'], 'Lava test')
        stops = g['stops']
        self.assertEqual([s['color'] for s in stops], ['#000000', '#ff0000', '#ffffff'])
        self.assertAlmostEqual(stops[1]['at'], .75, delta=.02)
        self.assertAlmostEqual(stops[0]['mid'], .25, delta=.03)
        self.assertEqual([round(a['a'], 2) for a in g['alpha']], [1, .4, 1])
        # the tool draws exactly what the editor made
        b.drag_image([(0, 0), (64, 0)], center=False)
        self.wait_history(2)
        self.assertPixelsClose(b.editor_pixels(), over(solid(64, 32), gradient_rgba(g, gradient_t('linear', 64, 32, 0, 0, 64, 0))), max_abs=2)
        # the presets menu next to the swatch picks a preset
        b.click_selector('.fill-grad-more')
        b.click_selector('.fill-pop .fill-swatch[data-preset="Spectrum"]')
        self.assertEqual(b.eval("Ed.toolOptions('gradient').gradient.name"), 'Spectrum')
        # Cancel keeps the gradient
        b.click_selector('.fill-grad-swatch')
        b.click_selector(f'{dlg} .fill-swatch[data-preset="Black, White"]')
        b.key('Escape')
        b.wait_for("!document.querySelector('dialog[open]')")
        self.assertEqual(b.eval("Ed.toolOptions('gradient').gradient.name"), 'Spectrum')
        b.eval("localStorage.removeItem('ed-gradient-presets')")
        self.assertNoBrowserErrors()

    def test_05_gradient_lock_transparency_mask_and_soft_selection(self):
        # Lock Transparent Pixels: the colors change, the alpha stays
        Y, X = np.mgrid[:48, :64] + .5
        spr = np.zeros((48, 64, 4), np.uint8)
        spr[..., :3] = (230, 60, 40)
        spr[..., 3] = np.clip((14 - np.hypot(X - 32, Y - 24)) * 100 + 128, 0, 255).astype(np.uint8)
        b = self.fresh(spr)
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}, {history: false}); true")
        self.tool('gradient', gradient=BW, kind='linear', mode='normal', opacity=100, reverse=False, dither=False, transparency=True)
        b.drag_image([(10, 0), (54, 0)], center=False)
        self.wait_history(2)
        out = b.editor_pixels()
        g = gradient_rgba(BW, gradient_t('linear', 64, 48, 10, 0, 54, 0))
        ref = spr.copy(); ref[..., :3] = g[..., :3]
        self.assertPremulClose(out, ref)
        self.assertTrue((out[..., 3] == spr[..., 3]).all(), 'the alpha is kept')
        self.undo_all()
        self.assertPremulClose(b.editor_pixels(), spr, max_abs=0)
        # on a layer mask the gradient's gray becomes the mask
        b = self.fresh(solid(64, 48, '#3070c0'))
        b.eval("Ed.layers.addMask(0, 'reveal'); Ed.setTarget('mask'); true")
        b.drag_image([(0, 0), (64, 0)], center=False)
        self.wait_history(3)
        self.assertEqual(self.labels()[-1], 'Gradient')
        mask = b.editor_pixels('mask')[..., 3].astype(int)
        ref = gradient_rgba(BW, gradient_t('linear', 64, 48, 0, 0, 64, 0))[..., 0].astype(int)
        self.assertLessEqual(np.abs(mask - ref).max(), 1)
        # a feathered selection blends the gradient in
        bg = noise_image(64, 48, seed=2)
        b = self.fresh(bg)
        sel = np.zeros((48, 64, 4), np.uint8); sel[..., :3] = 255
        sel[..., 3] = np.clip(np.hypot(X - 32, Y - 24) * -12 + 200, 0, 255).astype(np.uint8)
        b.eval(f"(() => {{ const c = Ed.util.canvas(64, 48); Ed.util.write(c, {b.push_pixels(sel)}); Ed.select.set(c); return true; }})()")
        b.drag_image([(0, 0), (64, 48)], center=False)
        self.wait_history(3)
        g = gradient_rgba(BW, gradient_t('linear', 64, 48, 0, 0, 64, 48))
        self.assertPixelsClose(b.editor_pixels(), through_selection(bg, g, sel[..., 3]), max_abs=2)
        self.assertNoBrowserErrors()

    def test_06_options_bar_gradient_editor_and_presets_fit(self):
        b = self.fresh(noise_image(64, 64))
        self.tool('gradient', gradient=BW)
        self.assertOptionsFit(['gradient'], shot='fill-gradient')
        b.set_viewport(1366, 768)
        try:
            b.settle(3)
            b.click_selector('.fill-grad-more')
            b.wait_for("!!document.querySelector('.fill-pop')")
            r = b.eval("(() => { const r = document.querySelector('.fill-pop').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
            self.assertTrue(r[0] >= 0 and r[1] >= 0 and r[2] <= 1366 and r[3] <= 768, f'the presets fit: {r}')
            b.screenshot('fill-gradient-presets-1366x768.png')
            b.key('Escape')
            b.click_selector('.fill-grad-swatch')
            dlg = 'dialog[data-ed-dialog="Gradient Editor"]'
            b.wait_for(f"!!document.querySelector('{dlg}')")
            b.settle(3)
            box = b.eval(dialog_box_js('Gradient Editor'))
            self.assertTrue(box['left'] >= 0 and box['top'] >= 0 and box['right'] <= 1366 and box['bottom'] <= 768, f'the Gradient Editor fits: {box}')
            self.assertEqual(box['wide'], [])
            self.assertEqual(box['fieldsScroll'], 0, 'no scrolling inside the Gradient Editor at 1366 x 768')
            b.screenshot('fill-gradient-editor-1366x768.png')
            b.key('Escape')
            b.wait_for("!document.querySelector('dialog[open]')")
        finally:
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()
