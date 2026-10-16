"""Painting tools (src/web/editor/paint.js) end to end in headless Edge with real mouse, pen and keyboard input: the brush
engine (size, hardness, opacity, flow, spacing, smoothing, pen pressure for size and opacity, painting modes) against an
independent numpy model of Photoshop's dab stroke, the Pencil (aliased tips, Pixel Perfect, Auto Erase), the Eraser
(Brush / Pencil / Block), the Clone Stamp, the History Brush, and for every tool: one history state with Photoshop's
label, exact undo, the soft selection, lock transparency and the mask target, no browser errors, options bars that fit at
1366 px, and stroke frame times on a 2048 x 2048 sheet.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_paint*.py" -v
"""
import json
import statistics
import time
import numpy as np
from harness import ALT, SHIFT, EditorTestCase, GPU, distance_to_path, image_array, premultiplied, rgba
from fakegame import SPRITE

TOOLS = ['brush', 'pencil', 'eraser', 'clone-stamp', 'history-brush']
LABELS = {'brush': 'Brush Tool', 'pencil': 'Pencil', 'eraser': 'Eraser Tool', 'clone-stamp': 'Clone Stamp', 'history-brush': 'History Brush'}


# ----- an independent model of a dab stroke ------------------------------------------------------------------------------
def profile(u, h):
    """Photoshop-style round tip: 1 inside the hardness radius, then a smoothstep down to 0 at the edge."""
    u = np.abs(u)
    if h >= 1: return (u <= 1).astype(np.float64)
    t = np.clip((u - h) / (1 - h), 0, 1)
    return np.where(u <= h, 1.0, np.where(u >= 1, 0.0, 1 - t * t * (3 - 2 * t)))


def dab_coverage(shape, cx, cy, size, hardness, ss=12):
    """The fraction of each pixel the tip covers (supersampled ss x ss), as a full (H, W) array."""
    H, W = shape
    R = size / 2
    out = np.zeros(shape)
    x0, x1 = max(0, int(np.floor(cx - R - 1))), min(W, int(np.ceil(cx + R + 1)))
    y0, y1 = max(0, int(np.floor(cy - R - 1))), min(H, int(np.ceil(cy + R + 1)))
    if x1 <= x0 or y1 <= y0: return out
    o = (np.arange(ss) + .5) / ss
    xs = (np.arange(x0, x1)[:, None] + o[None, :]).ravel()
    ys = (np.arange(y0, y1)[:, None] + o[None, :]).ravel()
    X, Y = np.meshgrid(xs, ys)
    p = profile(np.hypot(X - cx, Y - cy) / R, hardness)
    out[y0:y1, x0:x1] = p.reshape(y1 - y0, ss, x1 - x0, ss).mean(axis=(1, 3))
    return out


def dab_positions(points, step, size_limit=np.inf):
    """Dabs every `step` of arc length along the polyline, the first one at its start, plus one at its end when the
    step is at most size_limit (Spacing <= 100 %)."""
    pts = [np.array(p, float) for p in points]
    out, carry = [pts[0]], step
    for a, b in zip(pts, pts[1:]):
        L = float(np.hypot(*(b - a)))
        while carry <= L:
            out.append(a + (b - a) * carry / L)
            carry += step
        carry -= L
    if step <= size_limit and np.hypot(*(pts[-1] - out[-1])) > 1e-3: out.append(pts[-1])   # and one where the stroke ends
    return out


def stroke_coverage(shape, points, size, hardness=100, flow=100, spacing=25, cap=1.0):
    """'Alpha darken': every dab moves the coverage toward the cap by tip x flow and never past it."""
    c = np.zeros(shape)
    for x, y in dab_positions(points, max(.1, spacing / 100 * size), size):
        a = dab_coverage(shape, x, y, size, hardness / 100) * flow / 100
        c = np.where(c < cap, c + (cap - c) * a, c)
    return c


def paint_over(base, color, alpha):
    """Normal paint of `color` with coverage alpha (H, W) over an opaque base; uint8 out."""
    a = alpha[..., None]
    rgb = base[..., :3] * (1 - a) + np.array(color[:3], float) * a
    out = base.astype(np.float64).copy()
    out[..., :3] = rgb
    return np.round(out).astype(np.uint8)


def l_triples(on):
    """How many 2 x 2 squares hold exactly 3 painted pixels (the L-shaped corners Pixel Perfect removes)."""
    s = on[:-1, :-1].astype(int) + on[1:, :-1] + on[:-1, 1:] + on[1:, 1:]
    return int((s == 3).sum())


class PaintTests(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers -----------------------------------------------------------------------------------------------------
    def doc(self, array, zoom=3):
        """A fresh one-layer document with these pixels, zoomed and panned to the top left corner."""
        b = self.browser
        b.load_editor_image(array)
        b.eval(f"document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true;"
               f"Ed.select.none(); Ed.view.setState({{zoom: {zoom}, px: 0, py: 0}}); true")
        b.settle(2)
        return array

    def flat(self, w=200, h=120, color=(255, 255, 255, 255)):
        a = np.zeros((h, w, 4), np.uint8)
        a[...] = color
        return self.doc(a)

    def tool(self, tool, **values):
        """Activate a tool with its default options plus these values."""
        b = self.browser
        b.eval(f"""Ed.setTool({json.dumps(tool)}); const d = {{}}; for (const o of Ed.tools.get({json.dumps(tool)}).options) if (o.key && o.type !== 'button') d[o.key] = o.default;
            Ed.setToolOptions({json.dumps(tool)}, Object.assign(d, {json.dumps(values)})); true""")

    def colors(self, fg='#000000', bg='#ffffff'):
        self.browser.eval(f"Ed.setColor('fg', '{fg}'); Ed.setColor('bg', '{bg}'); true")

    def states(self): return self.browser.eval('Ed.history.list().length')

    def label(self): return self.browser.eval('Ed.history.label(Ed.history.index)')

    def stroke(self, points, label, **kw):
        """A real drag in image coordinates that must add exactly one history state named `label`."""
        b = self.browser
        i, ops = b.eval('Ed.history.index'), b.eval('S.ops.length')
        b.drag_image(points, center=False, **kw)
        b.wait_for(f"Ed.history.index === {i + 1} && S.ops.length === {ops + 1} && Ed.history.label(Ed.history.index) === {json.dumps(label)}",
                   message=f'one "{label}" state after the stroke')
        return b.editor_pixels()

    def undo_restores(self, before, target='composite'):
        b = self.browser
        b.key('Ctrl+Z')
        b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        self.assertPixelsClose(b.editor_pixels(target), before, msg='undo restores every pixel exactly')

    def blank_focus(self):
        self.browser.eval("document.activeElement && document.activeElement.blur && document.activeElement.blur(); true")

    # ===== the brush engine ===============================================================================================
    def test_01_hard_brush_stroke_matches_the_dab_model_and_undo_is_exact(self):
        b = self.browser
        base = self.flat()
        b.click_selector('[data-tool="brush"]')
        self.assertEqual(b.eval('Ed.tool'), 'brush')
        self.tool('brush', size=12, hardness=100)
        b.set_value('#paintColor', '#ff00ff')       # the ids the core's tests and the old tool used still work
        b.set_value('#brushSize', 12)
        b.set_value('#brushOpacity', 100)
        path = [(20.3, 30.6), (110.2, 52.9), (170.7, 90.1)]
        px = self.stroke(path, 'Brush Tool')
        want = paint_over(base, rgba('#ff00ff'), stroke_coverage((120, 200), path, 12))
        d = distance_to_path(200, 120, path)
        self.assertPixelsClose(px, want, mask=d <= 3.5, msg='the stroke body is exactly the color')
        self.assertPixelsClose(px, base, mask=d >= 7, msg='nothing else changed')
        self.assertPixelsClose(px, want, max_abs=10, mean_abs=.2, msg='the anti-aliased edge follows the exact pixel coverage')
        self.assertEqual(b.eval("S.ops.filter(o => o.tool === 'brush').map(o => [o.color, o.size])")[-1], ['#ff00ff', 12])
        self.undo_restores(base)
        self.assertNoBrowserErrors()

    def test_02_soft_brush_flow_and_opacity_follow_photoshops_buildup(self):
        b = self.browser
        base = self.flat()
        self.colors('#000000')
        cases = [dict(size=20, hardness=0, flow=100, opacity=100), dict(size=20, hardness=50, flow=30, opacity=100),
                 dict(size=16, hardness=100, flow=40, opacity=60), dict(size=24, hardness=20, flow=100, opacity=50, spacing=10)]
        path = [(20, 60), (180, 60)]
        for c in cases:
            self.tool('brush', **c)
            px = self.stroke(path, 'Brush Tool')
            cov = stroke_coverage((120, 200), path, c['size'], c['hardness'], c['flow'], c.get('spacing', 25)) * c['opacity'] / 100
            self.assertPixelsClose(px, paint_over(base, (0, 0, 0), cov), max_abs=8, mean_abs=.6, msg=f'{c}')
            b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        # a single click is one dab: Flow is its strength
        self.tool('brush', size=20, hardness=100, flow=30)
        px = self.stroke([(100.5, 60.5)], 'Brush Tool')
        self.assertAlmostEqual(int(px[60, 100, 0]), round(255 * .7), delta=2)

    def test_03_opacity_caps_a_stroke_crossing_itself_and_flow_builds_up_to_it(self):
        b = self.browser
        self.flat()
        self.colors('#000000')
        self.tool('brush', size=10, hardness=100, opacity=50)
        px = self.stroke([(20, 30), (180, 90), (180, 30), (20, 90)], 'Brush Tool')      # crosses itself at (100, 60)
        self.assertEqual(int(px[60, 100, 0]), int(px[45, 60, 0]), 'the crossing is no darker than the rest of the stroke')
        self.assertAlmostEqual(int(px[45, 60, 0]), 128, delta=2)
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        # a second stroke over the first does add up (each stroke is capped on its own)
        self.stroke([(20, 60), (180, 60)], 'Brush Tool')
        px = self.stroke([(20, 60), (180, 60)], 'Brush Tool')
        self.assertAlmostEqual(int(px[60, 100, 0]), 64, delta=2)

    def test_04_spacing_places_dabs_along_the_path(self):
        self.flat()
        self.colors('#000000')
        self.tool('brush', size=8, hardness=100, spacing=250)          # one dab every 20 px
        px = self.stroke([(20.5, 60.5), (180.5, 60.5)], 'Brush Tool')
        row = px[60, :, 0]
        centres = [x for x in range(200) if row[x] == 0 and (x == 0 or row[x - 1] != 0)]
        self.assertEqual(len(centres), 9, f'nine separate dots: {centres}')
        for i, x in enumerate(centres): self.assertAlmostEqual(x + 3.5, 20.5 + 20 * i, delta=1, msg=f'dot {i} at x {x}')
        self.assertTrue((row[[30, 50, 70, 90, 110, 130, 150, 170]] == 255).all(), 'the gaps between dots stay untouched')

    def test_05_pen_pressure_controls_size_by_default_and_opacity_when_asked(self):
        b = self.browser
        self.flat()
        self.tool('brush')
        # the core's own check, with this module loaded: width follows pressure and the line stays connected
        self.blank_focus()
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 16, hardness: 100, opacity: 100, flow: 100, pressure: true, pressureOpacity: false}); Ed.setColor('fg', '#0000ff')")
        px = self.stroke([(20, 40), (100, 40), (180, 40)], 'Brush Tool', steps_between=6, pointer='pen', pressures=[0.15, 0.6, 1.0])
        blue = np.all(px[..., :3] == (0, 0, 255), -1)
        widths = [int(blue[:, x].sum()) for x in (30, 100, 170)]
        self.assertTrue(widths[0] < widths[1] < widths[2], f'width follows pressure: {widths}')
        self.assertTrue(all(blue[40, 20:180]), 'a pen stroke is continuous')
        self.assertAlmostEqual(widths[1], 16 * .6, delta=2)
        self.assertAlmostEqual(widths[2], 16 * 1.0, delta=2.5)
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        # a mouse always paints at full size with the option on
        px = self.stroke([(20, 40), (180, 40)], 'Brush Tool')
        self.assertEqual(int((px[:, 100, 0] < 128).sum()), 16)
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        # pressure for opacity, size fixed
        b.eval("Ed.setToolOptions('brush', {pressure: false, pressureOpacity: true}); Ed.setColor('fg', '#000000')")
        px = self.stroke([(20, 80), (180, 80)], 'Brush Tool', pointer='pen', pressures=0.5)
        self.assertAlmostEqual(int(px[80, 100, 0]), 128, delta=3, msg='half pressure, half the paint')
        self.assertEqual(int((px[:, 100, 0] < 250).sum()), 16, 'full size')
        # the toggles in the options bar show and change the state
        self.assertEqual(b.eval("document.querySelector('#edOptions .paint-toggle[data-key=pressureOpacity]').getAttribute('aria-pressed')"), 'true')
        b.click_selector('#edOptions .paint-toggle[data-key=pressureOpacity]')
        self.assertFalse(b.eval("Ed.toolOptions('brush').pressureOpacity"))

    def test_06_smoothing_steadies_the_line_and_catches_up_at_the_end(self):
        b = self.browser
        self.flat()
        self.colors('#000000')
        self.tool('brush', size=4, hardness=100, smoothing=100)        # a 50 screen px string = 16.7 image px at zoom 3
        spike = [(20, 60), (100, 60), (104, 40), (108, 60), (180, 60)]
        px = self.stroke(spike, 'Brush Tool')
        dark = px[..., 0] < 128
        self.assertFalse(dark[40:50, :].any(), 'the jitter toward y 40 is smoothed away')
        self.assertTrue(dark[58:63, 178:181].any(), 'the stroke still ends where the pointer was let go')
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        self.tool('brush', size=4, hardness=100, smoothing=0)
        px = self.stroke(spike, 'Brush Tool')
        self.assertTrue((px[40:50, :, 0] < 128).any(), 'without smoothing the spike is painted')

    def test_07_shift_click_draws_a_straight_line_and_shift_drag_keeps_an_axis(self):
        b = self.browser
        self.flat()
        self.colors('#000000')
        self.tool('brush', size=6, hardness=100)
        self.stroke([(30.5, 30.5)], 'Brush Tool')
        b.click(*b.image_to_client(150.5, 90.5), modifiers=SHIFT)
        b.wait_for("Ed.history.list().length === 3")
        px = b.editor_pixels()
        d = distance_to_path(200, 120, [(30.5, 30.5), (150.5, 90.5)])
        self.assertTrue((px[d <= 2][:, 0] == 0).all() and (px[d >= 4][:, 0] == 255).all(), 'Shift-click joined the two points')
        self.flat()                                  # a new document: no last point to draw a line from
        px = self.stroke([(20.5, 60.5), (60.5, 64.5), (120.5, 50.5), (180.5, 70.5)], 'Brush Tool', modifiers=SHIFT)
        rows = np.where((px[..., 0] < 100).any(1))[0]
        self.assertEqual((rows.min(), rows.max()), (57, 63), 'Shift-drag stays on the first row (y 60.5, radius 3)')

    def test_08_painting_modes_against_the_w3c_formulas(self):
        b = self.browser
        self.tool('brush')
        b.settle(2)
        modes = b.eval("[...document.querySelectorAll('#edOptions select.paint-mode option')].map(o => o.value)")
        self.assertEqual(modes[:3], ['normal', 'behind', 'clear'])
        for m in ('multiply', 'screen', 'darken', 'lighten', 'difference', 'overlay', 'color'): self.assertIn(m, modes)
        base = self.flat(color=(200, 100, 50, 255))
        self.colors('#80ff40')
        cb, cs = np.array([200, 100, 50]) / 255, np.array([0x80, 0xff, 0x40]) / 255
        expect = {'multiply': cb * cs, 'screen': cb + cs - cb * cs, 'darken': np.minimum(cb, cs), 'lighten': np.maximum(cb, cs), 'difference': np.abs(cb - cs)}
        for mode, want in expect.items():
            self.tool('brush', size=10, hardness=100, mode=mode)
            px = self.stroke([(20, 60), (180, 60)], 'Brush Tool')
            got = px[60, 30:170, :3].astype(int)
            self.assertTrue(np.abs(got - np.round(want * 255)).max() <= 1, f'{mode}: {got[0]} vs {np.round(want * 255)}')
            self.assertPixelsClose(px, base, mask=distance_to_path(200, 120, [(20, 60), (180, 60)]) >= 7)
            b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        self.assertEqual(b.eval("S.ops.filter(o => o.tool === 'brush').at(-1).mode"), 'difference')
        # Behind paints only under transparency, Clear erases
        sprite = self.doc(image_array('edit-sprite.png'), zoom=6)
        self.colors('#ff0000')
        self.tool('brush', size=6, hardness=100, mode='behind')
        px = self.stroke([(2, 14), (40, 14)], 'Brush Tool')
        opaque = sprite[..., 3] == 255
        self.assertPixelsClose(px, sprite, mask=opaque, msg='Behind leaves opaque pixels alone')
        self.assertTrue((px[14, 31:38] == (255, 0, 0, 255)).all(), 'and fills transparent ones')
        self.tool('brush', size=6, hardness=100, mode='clear')
        px = self.stroke([(2, 14), (40, 14)], 'Brush Tool')
        self.assertTrue((px[13:16, 3:38, 3] == 0).all(), 'Clear erases')
        # with locked transparency, Behind and Clear are refused (as Photoshop greys them out)
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}, {history: false}); document.getElementById('toast').hidden = true; true")
        n = self.states()
        b.drag_image([(2, 30), (40, 30)], center=False)
        b.settle(3)
        self.assertEqual(self.states(), n)
        self.assertIn('Behind and Clear', b.text('#toast'))
        b.eval("Ed.layers.set(0, {locks: {alpha: false}}, {history: false}); true")
        self.assertNoBrowserErrors()

    def test_09_selection_lock_transparency_and_mask_target(self):
        """The core's own checks (test_core_verify test_21) with this module's Brush and Eraser."""
        b = self.browser
        self.flat(color=(0, 0, 255, 255))
        self.tool('brush'); self.tool('eraser')
        b.eval("const m = Ed.util.canvas(200, 120), g = m.getContext('2d'); g.fillStyle = '#000'; g.fillRect(0, 0, 100, 120); g.fillStyle = 'rgba(0,0,0,0.5)'; g.fillRect(100, 0, 60, 120); Ed.select.set(m)")
        self.blank_focus()
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 20, hardness: 100, opacity: 100, flow: 100}); Ed.setColor('fg', '#ff0000')")
        px = self.stroke([(10, 60), (190, 60)], 'Brush Tool').astype(int)
        self.assertEqual(tuple(px[60, 50]), (255, 0, 0, 255), 'fully selected: painted')
        self.assertTrue(abs(px[60, 130, 0] - 128) <= 2 and abs(px[60, 130, 2] - 127) <= 2, f'half selected: half painted, got {px[60, 130]}')
        self.assertEqual(tuple(px[60, 180]), (0, 0, 255, 255), 'not selected: untouched')
        b.eval("Ed.select.none()")
        sprite = self.doc(image_array('edit-sprite.png'), zoom=6)
        b.eval("Ed.layers.set(0, {locks: {alpha: true}})")
        before = b.editor_pixels()
        for tool, color in (('brush', '#00ff00'), ('eraser', None)):
            b.key(tool[0])
            self.assertEqual(b.eval('Ed.tool'), tool)
            b.eval(f"Ed.setToolOptions('{tool}', {{size: 9, hardness: 0, opacity: 70}}); Ed.setColor('fg', '{color or '#000000'}'); Ed.setColor('bg', '#123456')")
            prev = b.editor_pixels().astype(float)
            px = self.stroke([(2, 55), (40, 44), (60, 30)], LABELS[tool])
            self.assertPixelsClose(px[..., 3], before[..., 3], msg=f'{tool}: alpha is locked exactly')
        # where it covers fully, the eraser painted the background color over the half-transparent green bar
        a = 0.7 * stroke_coverage((64, 64), [(2, 55), (40, 44), (60, 30)], 9, 0)
        want = np.round(prev[..., :3] * (1 - a[..., None]) + np.array([0x12, 0x34, 0x56]) * a[..., None])
        glass = before[..., 3] == 128
        self.assertTrue(np.abs(px[glass][:, :3] - want[glass]).max() <= 4, 'locked: the eraser paints the background color')
        b.eval("Ed.layers.set(0, {locks: {alpha: false}})")
        b.eval("Ed.layers.addMask(0, 'reveal')")
        self.assertEqual(b.eval('Ed.doc.target'), 'mask')
        pixels_before, mask_before = b.editor_pixels(0), b.editor_pixels('mask')
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 10, hardness: 100, opacity: 100}); Ed.setColor('fg', '#000000')")
        self.stroke([(10, 10), (50, 10)], 'Brush Tool')
        self.assertPixelsClose(b.editor_pixels(0), pixels_before, msg='painting on the mask leaves the pixels alone')
        self.assertEqual(int(b.editor_pixels('mask')[10, 30, 3]), 0)
        self.assertEqual(int(b.editor_pixels()[10, 30, 3]), 0, 'the composite hides there')
        b.eval("Ed.setColor('fg', '#808080')")
        self.stroke([(10, 50), (50, 50)], 'Brush Tool')
        self.assertAlmostEqual(int(b.editor_pixels('mask')[50, 30, 3]), 128, delta=1, msg='gray paints a half mask')
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        self.undo_restores(mask_before, 'mask')
        self.assertNoBrowserErrors()

    def test_10_fast_drags_leave_no_gaps(self):
        """test_core_verify test_20's fast zigzag and soft line, with this module's Brush."""
        b = self.browser
        self.flat()
        self.tool('brush')
        self.blank_focus()
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 6, hardness: 100, opacity: 100, flow: 100, pressure: false}); Ed.setColor('fg', '#ff00ff')")
        zig = [(10 + 26 * i, 20 if i % 2 else 100) for i in range(8)]
        px = self.stroke(zig, 'Brush Tool', steps_between=0, burst=True)
        d = distance_to_path(200, 120, zig)
        self.assertTrue(np.all(px[d <= 2][:, :3] == (255, 0, 255)), 'every pixel on the path of a fast hard stroke is painted')
        self.assertTrue(np.all(px[d >= 4.5][:, :3] == 255), 'nothing far from the path changed')
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        b.eval("Ed.setToolOptions('brush', {size: 12, hardness: 0, opacity: 100}); Ed.setColor('fg', '#000000')")
        mid = 255 - self.stroke([(10, 60), (190, 60)], 'Brush Tool', steps_between=0, burst=True)[60, 16:184, 0].astype(int)
        self.assertGreater(int(mid.min()), 0.6 * int(mid.max()))
        self.assertLess(int(mid.max()) - int(mid.min()), 40, 'even, no beads')
        b.eval("Ed.setToolOptions('brush', {pressure: true})")

    def test_11_keys_brackets_digits_and_shift_digits(self):
        b = self.browser
        self.flat()
        self.tool('brush', size=10, hardness=100, opacity=100, flow=100)
        self.blank_focus()
        b.key(']'); b.key(']')
        self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 20)
        b.key('{')
        self.assertEqual(b.eval("Ed.toolOptions('brush').hardness"), 75)
        time.sleep(.8); b.key('3')
        self.assertEqual(b.eval("Ed.toolOptions('brush').opacity"), 30)
        time.sleep(.8); b.key('4', SHIFT)
        self.assertEqual(b.eval("Ed.toolOptions('brush').flow"), 40, 'Shift + digit sets the Flow')
        time.sleep(.8); b.key('2', SHIFT); b.key('5', SHIFT)
        self.assertEqual(b.eval("Ed.toolOptions('brush').flow"), 25, 'two quick digits: an exact value')
        self.assertEqual(b.eval("+document.querySelector('#edOptions [data-key=flow] input[type=number]').value"), 25)
        # Alt samples the foreground color instead of painting
        b.eval("const g = Ed.layers.get(0).canvas.getContext('2d'); g.fillStyle = '#abcdef'; g.fillRect(100, 0, 100, 120); Ed.update(); true")
        n = self.states()
        with b.hold('Alt'):
            b.drag_image([(20, 20), (150, 20)], center=False)
        self.assertEqual(b.eval('Ed.fg'), '#abcdef')
        self.assertEqual(self.states(), n)

    def test_12_brush_tip_popover_edits_size_hardness_spacing_and_smoothing(self):
        b = self.browser
        self.flat()
        self.tool('brush', size=10, hardness=100)
        b.settle(2)
        b.click_selector('#edOptions .paint-tipbtn')
        b.wait_for("!!document.querySelector('.paint-pop')")
        keys = b.eval("[...document.querySelectorAll('.paint-pop .ed-field')].map(f => f.dataset.key)")
        self.assertEqual(keys, ['size', 'hardness', 'spacing', 'smoothing'])
        b.set_value(".paint-pop [data-key=hardness] input[type=number]", 30)
        b.set_value(".paint-pop [data-key=spacing] input[type=number]", 50)
        b.set_value(".paint-pop [data-key=smoothing] input[type=number]", 20)
        o = b.eval("Ed.toolOptions('brush')")
        self.assertEqual((o['hardness'], o['spacing'], o['smoothing']), (30, 50, 20))
        b.screenshot('paint-popover.png')
        box = b.eval("(() => { const r = document.querySelector('.paint-pop').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
        self.assertTrue(box[0] >= 0 and box[1] >= 0 and box[2] <= 1600 and box[3] <= 1000, f'the popover is on screen: {box}')
        self.blank_focus()
        b.key(']')
        self.assertEqual(b.eval("+document.querySelector('.paint-pop [data-key=size] input[type=number]').value"), 15, 'the popover follows [ and ]')
        b.click(*b.image_to_client(100, 100))      # a click on the picture closes it
        b.wait_for("!document.querySelector('.paint-pop')")
        self.assertNoBrowserErrors()

    # ===== Pencil ======================================================================================================
    def staircase(self, a, b):
        """Right one pixel, then down one pixel, from pixel (a, a) to (b, b): the path of a hand drawing a diagonal."""
        pts = [(a + .5, a + .5)]
        for k in range(a, b):
            pts += [(k + 1.5, k + .5), (k + 1.5, k + 1.5)]
        return pts

    def test_20_pencil_pixel_perfect_removes_l_corners_on_pixel_art(self):
        b = self.browser
        tiny = self.doc(image_array('edit-tiny.png'), zoom=32)
        self.colors('#00ffff')
        cyan = np.array([0, 255, 255, 255], np.uint8)
        stairs = self.staircase(3, 12)
        b.click_selector('[data-tool="brush"]', button='right')            # the Brush slot's flyout lists the Pencil
        b.wait_for("!!document.querySelector('.ed-flyout [data-tool=\"pencil\"]')")
        b.click_selector('.ed-flyout [data-tool="pencil"]')
        self.assertEqual(b.eval('Ed.tool'), 'pencil')
        self.tool('pencil', size=1, pixelPerfect=True)
        px = self.stroke(stairs, 'Pencil', steps_between=2)
        want = tiny.copy()
        for k in range(3, 13): want[k, k] = cyan
        self.assertPixelsClose(px, want, msg='Pixel Perfect: a clean one-pixel diagonal')
        self.undo_restores(tiny)
        self.tool('pencil', size=1, pixelPerfect=False)
        px = self.stroke(stairs, 'Pencil', steps_between=2)
        want = tiny.copy()
        for k in range(3, 12): want[k, k] = want[k, k + 1] = cyan
        want[12, 12] = cyan
        self.assertPixelsClose(px, want, msg='without it every step keeps its corner pixel')
        b.screenshot('paint-pencil-tiny.png')
        self.assertEqual(b.eval("S.ops.filter(o => o.tool === 'pencil').length"), 2)

    def test_21_pencil_freehand_curves_are_gap_free_and_thin(self):
        b = self.browser
        self.doc(np.full((64, 64, 4), 255, np.uint8), zoom=12)
        self.colors('#000000')
        t = np.linspace(0, np.pi * 1.5, 40)
        arc = [(32 + 22 * np.cos(a), 32 + 22 * np.sin(a)) for a in t]
        for pp in (True, False):
            self.tool('pencil', size=1, pixelPerfect=pp)
            px = self.stroke(arc, 'Pencil', steps_between=3)
            on = px[..., 0] == 0
            self.assertTrue(np.isin(px[..., 0], (0, 255)).all(), 'aliased: every pixel is either painted or untouched')
            seen, todo = set(), [tuple(np.argwhere(on)[0])]       # one 8-connected line
            while todo:
                y, x = todo.pop()
                if (y, x) in seen or not (0 <= y < 64 and 0 <= x < 64 and on[y, x]): continue
                seen.add((y, x)); todo += [(y + dy, x + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
            self.assertEqual(len(seen), int(on.sum()), f'pixel perfect {pp}: the line has no gaps')
            if pp: self.assertEqual(l_triples(on), 0, 'no L-shaped corners')
            else: self.assertGreater(l_triples(on), 3, 'a plain freehand line has L corners (what Pixel Perfect removes)')
            b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")

    def test_22_pencil_tips_auto_erase_and_opacity(self):
        white = self.doc(np.full((40, 40, 4), 255, np.uint8), zoom=12)
        self.colors('#000000', '#ffffff')
        self.tool('pencil', size=3)
        px = self.stroke([(10.3, 10.7)], 'Pencil')
        self.assertEqual(np.argwhere(px[..., 0] == 0).tolist(), [[y, x] for y in range(9, 12) for x in range(9, 12)], 'odd sizes: centred on the pixel')
        self.tool('pencil', size=4)
        px = self.stroke([(20.4, 20.6)], 'Pencil')
        got = {(int(y), int(x)) for y, x in np.argwhere(px[..., 0] == 0) if y >= 15}
        want = {(y, x) for y in range(19, 23) for x in range(18, 22)} - {(19, 18), (19, 21), (22, 18), (22, 21)}
        self.assertEqual(got, want, 'even sizes: centred on the pixel corner, a round 4 px tip')
        # Auto Erase: starting on the foreground color paints the background color
        self.doc(white, zoom=12)
        self.tool('pencil', size=1, autoErase=True)
        self.stroke([(5.5, 30.5), (30.5, 30.5)], 'Pencil')               # starts on white: paints black
        px = self.stroke([(10.5, 30.5), (20.5, 30.5)], 'Pencil')         # starts on black: paints white
        self.assertTrue((px[30, 5:10, 0] == 0).all() and (px[30, 10:21, 0] == 255).all() and (px[30, 21:31, 0] == 0).all())
        # Opacity
        self.tool('pencil', size=1, opacity=50)
        px = self.stroke([(5.5, 5.5), (30.5, 5.5)], 'Pencil')
        self.assertTrue((np.abs(px[5, 5:31, 0].astype(int) - 128) <= 1).all())

    # ===== Eraser ======================================================================================================
    def test_30_eraser_brush_mode_matches_the_dab_model(self):
        b = self.browser
        self.doc(image_array('edit-sprite.png'), zoom=8)
        sprite = b.editor_pixels()            # as the canvas stores it (half-transparent colors are rounded)
        self.blank_focus()
        b.key('e')
        self.assertEqual(b.eval('Ed.tool'), 'eraser')
        self.tool('eraser', size=8, hardness=50, opacity=60)
        path = [(6.2, 10.4), (25.3, 22.8)]
        px = self.stroke(path, 'Eraser Tool')
        cov = stroke_coverage((64, 64), path, 8, 50) * .6
        square = np.zeros((64, 64), bool); square[4:28, 4:28] = True
        want_a = np.round(255 * (1 - cov))
        self.assertLessEqual(int(np.abs(px[square][:, 3].astype(int) - want_a[square]).max()), 3, 'the blue square loses alpha by the stroke coverage')
        self.assertPixelsClose(px[..., :3], sprite[..., :3], max_abs=2, mask=square & (px[..., 3] > 20), msg='and keeps its color')
        self.assertPixelsClose(px, sprite, mask=distance_to_path(64, 64, path) >= 6, msg='nothing else changes')
        self.assertEqual(b.eval("S.ops.at(-1)"), {'tool': 'eraser', 'size': 8})
        self.undo_restores(sprite)

    def test_31_eraser_pencil_and_block_modes(self):
        b = self.browser
        self.doc(image_array('edit-sprite.png'), zoom=4)
        sprite = b.editor_pixels()
        self.tool('eraser', eraseMode='pencil', size=3, opacity=100)
        b.settle(2)
        off = b.eval("[...document.querySelectorAll('#edOptions .ed-field.paint-off')].map(f => f.dataset.key)")
        self.assertEqual(sorted(off), ['flow', 'hardness'], 'Pencil mode greys out what it does not use')
        px = self.stroke([(6.5, 6.5), (20.5, 14.5)], 'Eraser Tool')
        changed = (px != sprite).any(-1)
        self.assertTrue((px[changed][:, 3] == 0).all(), 'aliased: erased pixels are fully clear')
        self.assertTrue(changed[6, 5:8].all() and changed[14, 19:22].all())
        self.assertEqual(b.eval("S.ops.at(-1).mode"), 'pencil')
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        self.tool('eraser', eraseMode='block', size=50, opacity=10)
        b.settle(2)
        off = b.eval("[...document.querySelectorAll('#edOptions .ed-field.paint-off')].map(f => f.dataset.key)")
        self.assertEqual(sorted(off), ['flow', 'hardness', 'opacity', 'pressure', 'pressureOpacity', 'size'])
        px = self.stroke([(20.3, 20.3)], 'Eraser Tool')                 # 16 screen px at 400% = a 4 x 4 pixel block
        want = sprite.copy(); want[18:22, 18:22] = 0
        self.assertPixelsClose(premultiplied(px), premultiplied(want), msg='Block: one 4 x 4 square, at full strength')

    def test_32_eraser_on_a_mask_and_with_a_selection(self):
        b = self.browser
        photo = self.doc(image_array('edit-photo.png'), zoom=2)
        b.eval("Ed.layers.addMask(0, 'reveal')")
        mask_before = b.editor_pixels('mask')
        self.colors('#ffffff', '#000000')
        self.tool('eraser', size=10, hardness=100)
        self.stroke([(20, 200), (120, 200)], 'Eraser Tool')
        self.assertEqual(int(b.editor_pixels('mask')[200, 70, 3]), 0, 'on a mask the eraser paints the background color (black hides)')
        self.assertPixelsClose(b.editor_pixels(0), photo, msg='the pixels stay')
        self.undo_restores(mask_before, 'mask')
        b.eval("Ed.layers.removeMask(0)")
        b.eval("Ed.select.rect(0, 0, 70, 256)")
        self.tool('eraser', size=10, hardness=100)
        px = self.stroke([(20, 200), (120, 200)], 'Eraser Tool')
        self.assertEqual((int(px[200, 50, 3]), int(px[200, 100, 3])), (0, 255), 'only the selection is erased')
        b.eval("Ed.select.none()")

    # ===== Clone Stamp =================================================================================================
    def test_40_clone_stamp_copies_exact_pixels_with_alt_click_source(self):
        b = self.browser
        photo = self.doc(image_array('edit-photo.png'), zoom=2)
        self.tool('clone-stamp', size=9, hardness=100, aligned=True)
        b.eval("document.getElementById('toast').hidden = true; true")
        n = self.states()
        b.drag_image([(150, 150)], center=False)
        self.assertEqual(self.states(), n, 'no source yet: nothing painted')
        self.assertIn('Alt-click', b.text('#toast'))
        with b.hold('Alt'):
            b.click(*b.image_to_client(40.5, 40.5))
        self.assertEqual(b.eval('Ed.paint.source'), {'x': 40, 'y': 40, 'offset': None})
        self.assertEqual(self.states(), n, 'setting the source is not an edit')
        path = [(140.5, 140.5), (180.5, 152.5)]
        px = self.stroke(path, 'Clone Stamp')
        src = np.roll(np.roll(photo, 100, 0), 100, 1)                       # src[y, x] = photo[y - 100, x - 100]
        cov = stroke_coverage((256, 256), path, 9)
        want = photo.copy()
        want[..., :3] = np.round(photo[..., :3] * (1 - cov[..., None]) + src[..., :3] * cov[..., None])
        d = distance_to_path(256, 256, path)
        self.assertPixelsClose(px, src, mask=d <= 2.5, msg='the stroke body is an exact copy of the source')
        self.assertPixelsClose(px, want, max_abs=6, mean_abs=.05, msg="with the brush tip's anti-aliased edge")
        self.assertEqual(b.eval("S.ops.at(-1)"), {'tool': 'clone-stamp', 'dx': 100, 'dy': 100})
        # Aligned: the next stroke keeps the offset
        px = self.stroke([(150.5, 220.5), (190.5, 220.5)], 'Clone Stamp')
        self.assertPixelsClose(px[218:223, 152:189], src[218:223, 152:189], msg='aligned: same offset')
        # not aligned: every stroke starts from the source point again
        self.tool('clone-stamp', size=9, hardness=100, aligned=False)
        px = self.stroke([(200.5, 30.5), (230.5, 30.5)], 'Clone Stamp')
        self.assertPixelsClose(px[29:32, 200:231], photo[39:42, 40:71], msg='not aligned: the stroke starts at the source')
        self.undo_restores(self._before_last(px))

    def _before_last(self, px):
        """The composite one state back (read through history, not through this test's own model)."""
        b = self.browser
        b.eval("Ed.history.undo(); true")
        before = b.editor_pixels()
        b.eval("Ed.history.redo(); true")
        self.assertPixelsClose(b.editor_pixels(), px, msg='redo puts the stroke back exactly')
        return before

    def test_41_clone_stamp_samples_layers_and_respects_selection_lock_and_mask(self):
        b = self.browser
        photo = self.doc(image_array('edit-photo.png'), zoom=2)
        top = np.zeros((256, 256, 4), np.uint8); top[20:60, 20:60] = (255, 0, 0, 255)
        b.eval(f"const im={b.push_pixels(top)};const c=document.createElement('canvas');c.width=256;c.height=256;c.getContext('2d').putImageData(im,0,0);"
               "Ed.layers.add({name: 'Top', canvas: c, history: false}); Ed.layers.setActive(0); true")
        self.tool('clone-stamp', size=9, hardness=100, aligned=False, sampleFrom='layer')
        with b.hold('Alt'): b.click(*b.image_to_client(40.5, 40.5))
        self.stroke([(140.5, 140.5), (160.5, 140.5)], 'Clone Stamp')
        self.assertPixelsClose(b.editor_pixels(0)[139:142, 140:161], photo[39:42, 40:61], msg='Current Layer: only the active layer is copied')
        self.tool('clone-stamp', size=9, hardness=100, aligned=False, sampleFrom='all')
        self.stroke([(140.5, 180.5), (160.5, 180.5)], 'Clone Stamp')
        got = b.editor_pixels(0)[179:182, 140:160]          # the red square ends at x 59 of the source
        self.assertTrue((got == (255, 0, 0, 255)).all(), f'All Layers: what you see is copied {got[:, ::5].tolist()}')
        # selection
        b.eval("Ed.select.rect(0, 0, 150, 256)")
        self.tool('clone-stamp', size=9, hardness=100, aligned=False, sampleFrom='layer')
        self.stroke([(140.5, 220.5), (160.5, 220.5)], 'Clone Stamp')
        layer = b.editor_pixels(0)
        self.assertPixelsClose(layer[219:222, 140:150], photo[39:42, 40:50])
        self.assertPixelsClose(layer[214:227, 150:170], photo[214:227, 150:170], msg='outside the selection nothing changes')
        b.eval("Ed.select.none()")
        # lock transparency on the sprite: alpha stays exactly
        sprite = self.doc(image_array('edit-sprite.png'), zoom=8)
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}, {history: false}); true")
        self.tool('clone-stamp', size=12, hardness=0, aligned=False)
        with b.hold('Alt'): b.click(*b.image_to_client(44.5, 44.5))
        px = self.stroke([(10.5, 10.5), (30.5, 30.5), (10.5, 55.5)], 'Clone Stamp')
        self.assertPixelsClose(px[..., 3], sprite[..., 3], msg='lock transparency keeps every alpha value')
        self.assertTrue((np.abs(px[10, 10, :3].astype(int) - (230, 60, 40)) <= 1).all(), f'and the red disk was copied into the square: {px[10, 10]}')
        b.eval("Ed.layers.set(0, {locks: {alpha: false}}, {history: false}); true")
        # a mask target: the mask values are cloned
        self.doc(image_array('edit-photo.png'), zoom=2)
        m = np.zeros((256, 256), np.uint8); m[:, :128] = 255; m[:, 64:128] = 100
        b.eval("Ed.layers.addMask(0, 'reveal')")
        b.eval(f"const im={b.push_pixels(np.dstack([np.full((256, 256, 3), 255, np.uint8), m]))};const c=document.createElement('canvas');c.width=256;c.height=256;c.getContext('2d').putImageData(im,0,0);"
               "Ed.layers.set(0, {mask: c}, {history: false}); Ed.setTarget('mask'); true")
        self.tool('clone-stamp', size=9, hardness=100, aligned=False)
        with b.hold('Alt'): b.click(*b.image_to_client(80.5, 100.5))
        before = b.editor_pixels(0)
        self.stroke([(180.5, 100.5), (200.5, 100.5)], 'Clone Stamp')
        mask = b.editor_pixels('mask')[..., 3]
        self.assertTrue((mask[99:102, 180:201] == 100).all(), 'the gray mask value was copied')
        self.assertTrue((mask[90:96, 180:201] == 0).all(), 'and nothing around it')
        self.assertPixelsClose(b.editor_pixels(0), before, msg='the pixels stay')

    # ===== History Brush ===============================================================================================
    def test_50_history_brush_paints_back_the_opened_image(self):
        b = self.browser
        b.select_asset('edit-photo.png')
        b.eval("document.getElementById('toast').hidden = true; Ed.view.setState({zoom: 2, px: 0, py: 0}); true"); b.settle(2)
        photo = b.editor_pixels()
        self.colors('#ff0000')
        self.tool('brush', size=20, hardness=100)
        self.stroke([(20.5, 100.5), (220.5, 100.5)], 'Brush Tool')
        self.blank_focus()
        b.key('y')
        self.assertEqual(b.eval('Ed.tool'), 'history-brush')
        self.tool('history-brush', size=30, hardness=100)
        px = self.stroke([(10.5, 100.5), (230.5, 100.5)], 'History Brush')
        self.assertPixelsClose(px, photo, msg='the stroke is back to the opened image')
        b.key('Ctrl+Z'); b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        self.assertTrue((b.editor_pixels()[100, 50] == (255, 0, 0, 255)).all(), 'undo brings the red back')
        # transparency comes back too
        b.select_asset('edit-sprite.png')
        sprite = b.editor_pixels()
        b.eval("document.getElementById('toast').hidden = true; Ed.view.setState({zoom: 8, px: 0, py: 0}); true"); b.settle(2)
        self.tool('eraser', size=10, hardness=100)
        self.stroke([(2, 16), (60, 16)], 'Eraser Tool')
        self.tool('history-brush', size=14, hardness=100, opacity=100)
        px = self.stroke([(2, 16), (60, 16)], 'History Brush')
        self.assertPixelsClose(premultiplied(px), premultiplied(sprite), msg='erased pixels come back')
        # half opacity: half way back
        self.tool('brush', size=10, hardness=100)
        self.colors('#000000')
        self.stroke([(6, 10), (26, 10)], 'Brush Tool')
        self.tool('history-brush', size=10, hardness=100, opacity=50)
        px = self.stroke([(6, 10), (26, 10)], 'History Brush')
        self.assertLessEqual(int(np.abs(px[10, 10:22, :3].astype(int) - np.round(np.array([40, 120, 220]) / 2)).max()), 1)

    # ===== layout ======================================================================================================
    SPILL = """[...document.querySelectorAll('.ed-optionsbar *')].filter(e => {
        if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden') return false; const r = e.getBoundingClientRect(), p = e.closest('.ed-optionsbar').getBoundingClientRect();
        return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); }).map(e => (e.id || e.className || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 20))"""
    CLIPPED = """[...document.querySelectorAll('.ed-optionsbar label, .ed-optionsbar button')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1)
        .map(e => (e.id || e.className) + ':' + e.textContent.trim().slice(0, 20))"""

    def test_60_every_paint_tools_options_bar_fits_at_1366_and_1920(self):
        b = self.browser
        b.select_asset('edit-photo.png')
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h)
                b.eval("Ed.view.fit(); true"); b.settle(3)
                for tool in TOOLS + ['eraser:pencil', 'eraser:block']:
                    tool, _, mode = tool.partition(':')
                    self.tool(tool, **({'eraseMode': mode} if mode else {}))
                    b.settle(3)
                    self.assertEqual(b.eval(self.SPILL), [], f'{tool} {mode}: nothing spills out of the options bar at {w}x{h}')
                    self.assertEqual(b.eval(self.CLIPPED), [], f'{tool} {mode}: no cut-off labels at {w}x{h}')
                    self.assertLessEqual(b.eval("document.documentElement.scrollWidth"), w)
                    if w == 1366:
                        bar = b.eval("(() => { const r = document.querySelector('.ed-optionsbar').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")
                        b.screenshot(f'paint-bar-{tool}{"-" + mode if mode else ""}-1366.png', clip=tuple(bar))
                self.tool('brush')
                b.mouse_move(*b.pixel_center(128, 128)); b.settle(2)
                b.click_selector('#edOptions .paint-tipbtn')
                b.wait_for("!!document.querySelector('.paint-pop')")
                box = b.eval("(() => { const r = document.querySelector('.paint-pop').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
                self.assertTrue(box[0] >= 0 and box[1] >= 0 and box[2] <= w and box[3] <= h, f'the brush popover fits at {w}x{h}: {box}')
                clipped = b.eval("[...document.querySelectorAll('.paint-pop *')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1 && e.tagName !== 'INPUT').map(e => e.className + ':' + e.textContent.slice(0, 20))")
                self.assertEqual(clipped, [], 'nothing in the popover is cut off')
                b.screenshot(f'paint-layout-{w}x{h}.png')
                b.click_selector('#edOptions .paint-tipbtn')
                b.wait_for("!document.querySelector('.paint-pop')")
        finally:
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()

    # ===== speed =======================================================================================================
    FRAMES = """(async ([pts, tool]) => { const f = () => new Promise(r => requestAnimationFrame(r)), P = ([x, y]) => ({x, y, pressure: 1, pointerType: 'mouse', buttons: 1});
        const frames = [], work = []; const d = performance.now(); Ed.callTool('onDown', P(pts[0])); const down = performance.now() - d; let last = await f();
        for (const p of pts.slice(1)) { const w = performance.now(); Ed.callTool('onMove', P(p)); await Promise.resolve(); Ed.flush(); work.push(performance.now() - w);
          const t = await f(); frames.push(t - last); last = t; }
        const c = performance.now(); Ed.callTool('onUp', P(pts.at(-1))); const commit = performance.now() - c; await f();
        return {frames, work, down, commit}; })(%s)"""

    def test_70_stroke_frame_times_on_a_2048_sheet(self):
        b = self.browser
        b.select_asset('edit-big.png')
        b.eval("document.getElementById('toast').hidden = true; Ed.view.fit(); true")
        tint = np.zeros((2048, 2048, 4), np.uint8); tint[..., :3] = (255, 128, 0); tint[..., 3] = 60
        b.eval(f"const im={b.push_pixels(tint)};const c=document.createElement('canvas');c.width=2048;c.height=2048;c.getContext('2d').putImageData(im,0,0);"
               "Ed.layers.add({name: 'Tint', canvas: c, blend: 'multiply', history: false}); Ed.layers.add({name: 'Paint here', history: false}); true")
        b.settle(4)
        b.eval("Ed.layers.setActive(0); true")              # the History Brush needs a layer the opened image has
        pts = [(200 + i * 20, 1024 + 300 * np.sin(i / 12)) for i in range(80)]
        rows, worst = [], {}
        for name, tool, values in (('brush 40 px hard', 'brush', dict(size=40, hardness=100, opacity=80)),
                                   ('brush 200 px soft', 'brush', dict(size=200, hardness=0, opacity=80, flow=50)),
                                   ('eraser 60 px', 'eraser', dict(size=60, hardness=50)),
                                   ('pencil 3 px', 'pencil', dict(size=3)),
                                   ('clone stamp 40 px', 'clone-stamp', dict(size=40, hardness=50)),
                                   ('history brush 40 px', 'history-brush', dict(size=40))):
            self.tool(tool, **values)
            if tool == 'clone-stamp':
                with b.hold('Alt'): b.click(*b.image_to_client(300, 300))
            r = b.eval(self.FRAMES % json.dumps([pts, tool]), timeout=180)
            med, p95 = statistics.median(r['frames']), float(np.percentile(r['frames'], 95))
            rows.append(f'{name}: frames median {med:.1f} ms, 95th percentile {p95:.1f} ms; work per move (dabs + composite) median '
                        f'{statistics.median(r["work"]):.1f} ms, worst {max(r["work"]):.1f} ms; press {r["down"]:.1f} ms, commit {r["commit"]:.1f} ms')
            worst[name] = med
        idle = b.eval("(async () => { const f = () => new Promise(r => requestAnimationFrame(r)); let t0 = await f(), s = []; for (let i = 0; i < 20; i++) { const t = await f(); s.push(t - t0); t0 = t; } return s; })()")
        text = f"{'real GPU' if GPU else 'SwiftShader'}; 2048 x 2048, 3 layers; idle frame {statistics.median(idle):.1f} ms\n" + '\n'.join(rows)
        print('\n[paint speed]', text)
        (__import__('harness').OUTPUT / 'paint-speed.txt').write_text(text + '\n', 'utf-8')
        limit = 25 if GPU else 150
        for name, med in worst.items(): self.assertLess(med, limit, f'{name}: {text}')
        self.assertEqual(b.eval("Ed.test.errors.length"), 0)
