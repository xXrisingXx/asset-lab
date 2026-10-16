"""The shape tools of src/web/editor/fill-shapes.js (U: Rectangle, Ellipse, Polygon, Line) with real drags, checked
against exact area coverage of the geometry computed with numpy (supersampled, test_fill_refs.py): new shape layers and
Pixels mode, fill / stroke / width, corner radius, sides, weight, Shift / Alt, anti-alias off, the selection, lock
transparency, undo and the options bar.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_fill_shapes.py" -v
"""
import numpy as np
from harness import EditorTestCase, ALT, SHIFT, premultiplied
from test_fill_refs import (FillHelpers, solid, noise_image, over, through_selection, shape_layer, supersample, in_round_rect,
                            in_ellipse, in_polygon, polygon_points, line_points)

TOOLS = ['rectangle', 'ellipse', 'polygon', 'line']


class ShapeTests(FillHelpers, EditorTestCase):
    viewport = (1600, 1000)

    def shape(self, name, **o):
        opts = dict(mode='shape', fill='#30c060', stroke='', width=2, antialias=True)
        opts.update(o)
        self.tool(name, **opts)

    def drag(self, a, b, modifiers=0, mid=None):
        n = self.browser.eval('Ed.history.index')
        pts = [a] + ([mid] if mid else []) + [((a[0] + b[0]) / 2 + .3, (a[1] + b[1]) / 2 + .2), b]
        self.browser.drag_image(pts, center=False, modifiers=modifiers)
        self.browser.wait_for(f'Ed.history.index === {n + 1} && !Ed.history.open', 20)
        self.browser.settle()

    def assertShape(self, actual, expected, msg='', edge=None):
        """Anti-aliased edges (pixels that are partly covered, or where two colors meet) may differ a little from exact area
        coverage; on average they agree closely and everywhere else the pixels are exact."""
        a, e = premultiplied(actual).astype(int), premultiplied(expected).astype(int)
        d = np.abs(a - e).max(-1)
        self.assertLessEqual(int(d.max()), 96, f'{msg}: worst {d.max()} at {np.unravel_index(d.argmax(), d.shape)}')
        self.assertLess(float(d.mean()), .8, msg)
        if edge is None:
            ex = expected.astype(int)
            edge = ((expected[..., 3] > 0) & (expected[..., 3] < 255)) | (np.abs(ex - np.roll(ex, 1, 0)).max(-1) > 0) | (np.abs(ex - np.roll(ex, 1, 1)).max(-1) > 0)
        grown = edge.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1): grown |= np.roll(np.roll(edge, dy, 0), dx, 1)
        self.assertLessEqual(int(d[~grown].max(initial=0)), 1, f'{msg}: away from the edges the pixels are exact')

    # ----- shape layers ------------------------------------------------------------------------------------------------
    def test_01_rectangle_new_layer_radius_stroke_and_undo(self):
        bg = noise_image(64, 48, seed=4)
        b = self.fresh(bg)
        b.key('u')
        b.wait_for("Ed.tool === 'rectangle' || Ed.tools.get(Ed.tool)?.group === 'shape'")
        self.shape('rectangle', radius=0)
        self.drag((10.3, 8.7), (40.6, 30.2))
        self.assertEqual(self.labels(), ['Open', 'Rectangle Tool'])
        self.assertEqual(b.eval('Ed.layers.list().map(L => L.name)')[1:], ['Rectangle 1'])
        self.assertEqual(b.eval('Ed.layers.active().name'), 'Rectangle 1')
        ref = shape_layer(64, 48, in_round_rect(10, 9, 41, 30, 0), fill='#30c060')
        self.assertPixelsClose(b.editor_pixels(1), ref, msg='corners snap to whole pixels: an exact rectangle')
        self.assertPixelsClose(b.editor_pixels(0), bg, msg='the picture below is untouched')
        self.assertPixelsClose(b.editor_pixels(), over(bg, ref))
        # rounded corners and a stroke inside the outline, on a second layer named Rectangle 2
        self.shape('rectangle', radius=9, stroke='#e02020', width=3)
        self.drag((4, 4), (60, 44))
        self.assertEqual(b.eval('Ed.layers.active().name'), 'Rectangle 2')
        ref2 = shape_layer(64, 48, in_round_rect(4, 4, 60, 44, 9), fill='#30c060', stroke='#e02020', width=3, inner=in_round_rect(7, 7, 57, 41, 6))
        self.assertShape(b.editor_pixels(2), ref2, 'rounded rectangle with a 3 px stroke')
        # stroke only (no fill)
        self.shape('rectangle', radius=0, fill='', stroke='#2040ff', width=4)
        self.drag((20, 10), (50, 40))
        ref3 = shape_layer(64, 48, in_round_rect(20, 10, 50, 40, 0), stroke='#2040ff', width=4, inner=in_round_rect(24, 14, 46, 36, 0))
        self.assertPixelsClose(b.editor_pixels(3), ref3, msg='a 4 px frame')
        self.assertEqual(self.labels(), ['Open', 'Rectangle Tool', 'Rectangle Tool', 'Rectangle Tool'])
        self.undo_all()
        self.assertEqual(b.eval('Ed.layers.count()'), 1)
        self.assertPixelsClose(b.editor_pixels(), bg)
        self.assertNoBrowserErrors()

    def test_02_ellipse_polygon_line_with_shift_and_alt(self):
        b = self.fresh(solid(64, 64, '#ffffff'))
        # Alt+Shift: a circle around the first point
        self.shape('ellipse', stroke='#000000', width=2)
        self.drag((32, 30), (44.2, 38.4), modifiers=ALT | SHIFT)
        ref = shape_layer(64, 64, in_ellipse(32, 30, 12, 12), fill='#30c060', stroke='#000000', width=2, inner=in_ellipse(32, 30, 10, 10))
        self.assertShape(b.editor_pixels(1), ref, 'circle with a stroke')
        self.assertEqual(b.eval('Ed.layers.active().name'), 'Ellipse 1')
        # an ellipse from corner to corner
        self.shape('ellipse', stroke='')
        self.drag((3, 40), (60, 61))
        self.assertShape(b.editor_pixels(2), shape_layer(64, 64, in_ellipse(31.5, 50.5, 28.5, 10.5), fill='#30c060'), 'ellipse')
        # a hexagon filling its box
        b = self.fresh(solid(64, 64, '#ffffff'))
        self.shape('polygon', sides=6, fill='#c03080')
        self.drag((8, 6), (56, 50))
        self.assertShape(b.editor_pixels(1), shape_layer(64, 64, in_polygon(polygon_points(8, 6, 56, 50, 6)), fill='#c03080'), 'hexagon')
        self.assertEqual(self.labels()[-1], 'Polygon Tool')
        # a triangle with Shift: an equal box
        self.shape('polygon', sides=3, fill='#c03080')
        self.drag((10, 10), (40, 24), modifiers=SHIFT)
        self.assertShape(b.editor_pixels(2), shape_layer(64, 64, in_polygon(polygon_points(10, 10, 40, 40, 3)), fill='#c03080'), 'triangle')
        # lines: odd weights sit on pixel centres, even weights on pixel corners; Shift snaps to 45 degrees
        b = self.fresh(solid(64, 64, '#ffffff'))
        self.shape('line', weight=3, fill='#000000')
        self.drag((5.2, 5.9), (50.7, 30.1))
        self.assertShape(b.editor_pixels(1), shape_layer(64, 64, in_polygon(line_points(5.5, 5.5, 50.5, 30.5, 3)), fill='#000000'), 'line')
        self.shape('line', weight=4, fill='#0000ff')
        self.drag((10.2, 50.3), (55.9, 52.4), modifiers=SHIFT)
        ref = shape_layer(64, 64, in_round_rect(10, 48, 56, 52, 0), fill='#0000ff')
        self.assertPixelsClose(b.editor_pixels(2), ref, msg='a horizontal 4 px line is a crisp bar')
        self.assertEqual(self.labels()[-1], 'Line Tool')
        self.assertNoBrowserErrors()

    # ----- Pixels mode -------------------------------------------------------------------------------------------------
    def test_03_pixels_mode_selection_lock_transparency_and_no_antialias(self):
        bg = noise_image(64, 48, seed=9)
        b = self.fresh(bg)
        self.shape('rectangle', mode='pixels', radius=0, fill='#ffff00')
        self.drag((5, 5), (50, 40))
        self.assertEqual(b.eval('Ed.layers.count()'), 1, 'Pixels mode draws into the layer')
        self.assertEqual(self.labels(), ['Open', 'Rectangle Tool'])
        self.assertPixelsClose(b.editor_pixels(), over(bg, shape_layer(64, 48, in_round_rect(5, 5, 50, 40, 0), fill='#ffff00')))
        # the selection clips it
        b = self.fresh(bg)
        b.eval('Ed.select.rect(20, 0, 30, 20)')
        self.shape('ellipse', mode='pixels', fill='#ff00ff')
        self.drag((2, 2), (62, 46))
        sel = np.zeros((48, 64)); sel[0:20, 20:50] = 255
        full = over(bg, shape_layer(64, 48, in_ellipse(32, 24, 30, 22), fill='#ff00ff'))
        cov = supersample(64, 48, in_ellipse(32, 24, 30, 22))
        self.assertShape(b.editor_pixels(), through_selection(bg, full, sel), 'ellipse through the selection', edge=(cov > 0) & (cov < 1))
        # lock transparency: only the existing pixels change color
        Y, X = np.mgrid[:48, :64] + .5
        spr = np.zeros((48, 64, 4), np.uint8); spr[..., :3] = (40, 120, 220); spr[10:30, 8:56, 3] = 255
        b = self.fresh(spr)
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}, {history: false}); true")
        self.shape('rectangle', mode='pixels', fill='#ff0000', radius=0)
        self.drag((30, 0), (64, 48))
        out = b.editor_pixels()
        ref = spr.copy(); ref[10:30, 30:56, :3] = (255, 0, 0)
        self.assertPremulClose(out, ref, max_abs=0)
        # Anti-alias off: every pixel is fully in or out, as exact coverage >= 50% says
        b = self.fresh(solid(64, 48, (0, 0, 0, 0)))
        self.shape('ellipse', mode='pixels', fill='#ffffff', antialias=False)
        self.drag((3, 3), (60, 41))
        a = b.editor_pixels()[..., 3]
        self.assertTrue(np.isin(a, (0, 255)).all(), 'no half-transparent pixels')
        cov = supersample(64, 48, in_ellipse(31.5, 22, 28.5, 19), 16)
        wrong = (a == 255) != (cov >= .5)
        self.assertLessEqual(int(wrong.sum()), 3, f'pixels on the wrong side: {np.argwhere(wrong).tolist()}')
        self.assertTrue(((a == 255) == (cov >= .5))[np.abs(cov - .5) > .1].all())
        self.assertNoBrowserErrors()

    # ----- the options bar ---------------------------------------------------------------------------------------------
    def test_04_fill_and_stroke_pickers_shared_options_cancel_and_layout(self):
        b = self.fresh(solid(64, 48, '#ffffff'))
        b.eval("Ed.setColor('fg', '#123456'); true")
        self.shape('rectangle', fill='#30c060', stroke='', radius=0)
        # the Fill chip: a color, the foreground color, or none
        b.click_selector('.ed-optionsbar .ed-field[data-key="fill"] .fill-paint-chip')
        b.wait_for("!!document.querySelector('.fill-pop .fill-paint-pop')")
        b.set_value('.fill-pop input[name="fill-color"]', '#ff8000')
        self.assertEqual(b.eval("Ed.toolOptions('rectangle').fill"), '#ff8000')
        b.click_selector('.fill-pop [data-paint="fg"]')
        self.assertEqual(b.eval("Ed.toolOptions('rectangle').fill"), '#123456')
        b.click_selector('.ed-optionsbar .ed-field[data-key="stroke"] .fill-paint-chip')
        b.set_value('.fill-pop input[name="stroke-color"]', '#00ffff')
        b.key('Escape')
        b.set_value('.ed-optionsbar .ed-field[data-key="width"] input', 1)
        b.click_selector('.ed-optionsbar .ed-field[data-key="mode"] input[value="pixels"] + span')
        o = b.eval("Ed.toolOptions('rectangle')")
        self.assertEqual((o['fill'], o['stroke'], o['width'], o['mode']), ('#123456', '#00ffff', 1, 'pixels'))
        # the other shape tools share fill, stroke, width and mode
        for t in ('ellipse', 'polygon', 'line'):
            e = b.eval(f"Ed.toolOptions('{t}')")
            self.assertEqual((e['fill'], e['stroke'], e['width'], e['mode']), ('#123456', '#00ffff', 1, 'pixels'), t)
        self.drag((10, 10), (30, 20))
        ref = over(solid(64, 48, '#ffffff'), shape_layer(64, 48, in_round_rect(10, 10, 30, 20, 0), fill='#123456', stroke='#00ffff', width=1, inner=in_round_rect(11, 11, 29, 19, 0)))
        self.assertPixelsClose(b.editor_pixels(), ref)
        # the stroke chip's No Color
        b.click_selector('.ed-optionsbar .ed-field[data-key="stroke"] .fill-paint-chip')
        b.click_selector('.fill-pop [data-paint="none"]')
        self.assertEqual(b.eval("Ed.toolOptions('rectangle').stroke"), '')
        # Escape during a drag cancels it; a click without a drag draws nothing
        n = len(self.labels())
        p0, p1 = b.image_to_client(5, 5), b.image_to_client(40, 40)
        b.mouse_move(*p0); b.mouse_down(*p0); b.mouse_move(*p1); b.settle(2)
        b.key('Escape')
        b.mouse_up(*p1); b.settle(4)
        b.click(*b.pixel_center(20, 20)); b.settle(4)
        self.assertEqual(len(self.labels()), n)
        # nothing to draw: no fill and no stroke
        self.shape('ellipse', mode='shape', fill='', stroke='')
        b.drag_image([(5, 5), (30, 30)], center=False); b.settle(4)
        self.assertEqual(len(self.labels()), n)
        self.shape('rectangle', mode='shape', fill='#30c060', stroke='')
        self.assertOptionsFit(TOOLS, shot='fill-shapes')
        self.assertNoBrowserErrors(allow=('Pick a fill or a stroke color first.',))
