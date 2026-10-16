"""Selection tools and the Select menu (src/web/editor/select.js) against Photoshop's behaviour, driven with real mouse and
keyboard input in headless Edge. Every mask is checked against an independent numpy reference: rectangles, the ellipse's
anti-aliasing against analytic coverage, lasso polygons against exact polygon/pixel intersection areas (or pixel centres),
Feather against a Gaussian of sigma = radius, Expand / Contract against brute-force Euclidean distances, Smooth against box
counts, Magic Wand / Grow / Similar against a per-channel tolerance flood fill. Also: one history state per change with
Photoshop's label and undo, the marching ants on the view overlay, options bars at 1366 px in every language, the Modify
dialogs at 1366 x 768, and speed on a 2048 x 2048 sheet.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_select*.py" -v
"""
import json
import math
import time
import numpy as np
from harness import EditorTestCase, CTRL, SHIFT


# ----- independent references -----------------------------------------------------------------------------------------------
def rect_mask(w, h, x0, y0, x1, y1):
    m = np.zeros((h, w), np.uint8)
    m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 255
    return m


def ellipse_coverage(w, h, x0, y0, x1, y1, strips=512):
    """Exact-in-y, 512 strips in x: the area of each pixel inside the ellipse inscribed in (x0, y0)-(x1, y1)."""
    cx, cy, a, b = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    xs = (np.arange(w)[:, None] + (np.arange(strips)[None, :] + .5) / strips)            # (w, strips)
    t = (xs - cx) / a
    hh = np.where(1 - t * t > 0, b * np.sqrt(np.clip(1 - t * t, 0, None)), 0.0)
    lo, hi = cy - hh, cy + hh                                                             # (w, strips)
    ys = np.arange(h)[:, None, None]
    cover = np.clip(np.minimum(ys + 1, hi[None]) - np.maximum(ys, lo[None]), 0, 1).mean(axis=2)  # (h, w)
    return np.round(cover * 255).astype(np.uint8)


def ellipse_centres(w, h, x0, y0, x1, y1):
    cx, cy, a, b = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    y, x = np.mgrid[0:h, 0:w] + .5
    return np.where(((x - cx) / a) ** 2 + ((y - cy) / b) ** 2 <= 1, 255, 0).astype(np.uint8)


def gaussian_blur(m, sigma, edge='edge'):
    """Separable Gaussian (kernel to 5 sigma); outside the canvas repeats the edge ('edge') or is 0 ('constant')."""
    r = int(math.ceil(5 * sigma))
    k = np.exp(-np.arange(-r, r + 1) ** 2 / (2 * sigma * sigma))
    k /= k.sum()
    a = np.pad(m.astype(np.float64), r, mode=edge)
    a = np.apply_along_axis(lambda v: np.convolve(v, k, mode='valid'), 1, a)
    a = np.apply_along_axis(lambda v: np.convolve(v, k, mode='valid'), 0, a)
    return a


def polygon_coverage(w, h, pts):
    """Exact area of a simple polygon inside each pixel (Sutherland-Hodgman clipping against every pixel square)."""
    pts = [(float(x), float(y)) for x, y in pts]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    out = np.zeros((h, w))
    def clip(poly, inside, cross):
        res = []
        for i in range(len(poly)):
            a, b = poly[i - 1], poly[i]
            ia, ib = inside(a), inside(b)
            if ib:
                if not ia: res.append(cross(a, b))
                res.append(b)
            elif ia: res.append(cross(a, b))
        return res
    def area(poly):
        return abs(sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))) / 2
    for py in range(max(0, int(min(ys))), min(h, int(math.ceil(max(ys))))):
        for px in range(max(0, int(min(xs))), min(w, int(math.ceil(max(xs))))):
            poly = pts
            for inside, cross in (
                (lambda p: p[0] >= px, lambda a, b: (px, a[1] + (b[1] - a[1]) * (px - a[0]) / (b[0] - a[0]))),
                (lambda p: p[0] <= px + 1, lambda a, b: (px + 1, a[1] + (b[1] - a[1]) * (px + 1 - a[0]) / (b[0] - a[0]))),
                (lambda p: p[1] >= py, lambda a, b: (a[0] + (b[0] - a[0]) * (py - a[1]) / (b[1] - a[1]), py)),
                (lambda p: p[1] <= py + 1, lambda a, b: (a[0] + (b[0] - a[0]) * (py + 1 - a[1]) / (b[1] - a[1]), py + 1))):
                poly = clip(poly, inside, cross)
                if not poly: break
            if len(poly) >= 3: out[py, px] = area(poly)
    return np.round(np.clip(out, 0, 1) * 255).astype(np.uint8)


def polygon_centres(w, h, pts):
    """Pixels whose centre is inside (even-odd equals non-zero for simple polygons)."""
    y, x = np.mgrid[0:h, 0:w] + .5
    inside = np.zeros((h, w), bool)
    n = len(pts)
    for i in range(n):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
        if ay == by: continue
        cond = ((ay <= y) & (y < by)) | ((by <= y) & (y < ay))
        xc = ax + (y - ay) * (bx - ax) / (by - ay)
        inside ^= cond & (x < xc)
    return np.where(inside, 255, 0).astype(np.uint8)


def dist_to(mask_bool):
    """Exact Euclidean distance from every pixel centre to the nearest True pixel centre (brute force on small images)."""
    h, w = mask_bool.shape
    # Only pixels with an unselected 4-neighbour can be the nearest one (a step towards the target from any other is closer).
    p = np.pad(mask_bool, 1, mode='edge')
    edge = mask_bool & ~(p[1:-1, 2:] & p[1:-1, :-2] & p[2:, 1:-1] & p[:-2, 1:-1])
    ys, xs = np.nonzero(edge)
    if not mask_bool.any(): return np.full((h, w), np.inf)
    gy, gx = np.mgrid[0:h, 0:w]
    best = np.full((h, w), np.inf)
    for i in range(0, len(ys), 512):
        d = (gy[..., None] - ys[None, None, i:i + 512]) ** 2 + (gx[..., None] - xs[None, None, i:i + 512]) ** 2
        best = np.minimum(best, d.min(axis=2))
    return np.where(mask_bool, 0, np.sqrt(best))


def premul(img):
    """Premultiplied RGBA as floats rounded half up (alpha kept)."""
    a = img.astype(np.float64)
    out = a.copy()
    out[..., :3] = np.floor(a[..., :3] * a[..., 3:4] / 255 + .5)
    return out


def flood4(match, x, y):
    """4-connected region of `match` containing (x, y) (plain breadth-first search)."""
    h, w = match.shape
    out = np.zeros((h, w), bool)
    if not match[y, x]: return out
    todo = [(x, y)]
    out[y, x] = True
    while todo:
        cx, cy = todo.pop()
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if 0 <= nx < w and 0 <= ny < h and match[ny, nx] and not out[ny, nx]:
                out[ny, nx] = True
                todo.append((nx, ny))
    return out


def wand_reference(img, x, y, tol, contiguous=True, size=1):
    """Photoshop's Magic Wand: every channel (premultiplied R, G, B and alpha) within `tol` levels of the sample (the
    average of a size x size square, clipped to the image); contiguous = 4-connected to the clicked pixel."""
    p = premul(img)
    h, w = p.shape[:2]
    r = (size - 1) // 2
    s = p[max(0, y - r):min(h, y + r + 1), max(0, x - r):min(w, x + r + 1)].reshape(-1, 4).mean(axis=0)
    match = np.all(np.abs(p - s) <= tol, axis=-1)
    region = flood4(match, x, y) if contiguous else match
    return np.where(region, 255, 0).astype(np.uint8)


def box_sum(a, r, mode):
    """Sum over the (2r+1)^2 square around every pixel; outside the image repeats the edge ('edge') or is 0 ('constant')."""
    p = np.pad(a.astype(np.int64), r, mode=mode)
    c = np.cumsum(np.cumsum(np.pad(p, ((1, 0), (1, 0))), 0), 1)
    k = 2 * r + 1
    return c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]


def grow_reference(img, sel, tol, contiguous):
    """Grow / Similar: the colours of the pixels selected 50% or more, each widened by `tol` per premultiplied channel (R, G, B
    as a cube, alpha as the range of the selected alphas); Grow keeps the matching pixels 4-connected to the selection,
    Similar takes all of them. The old (soft) selection stays."""
    p = premul(img)
    inside = sel >= 128
    cols = np.unique(p[inside][:, :3], axis=0)
    amin, amax = p[inside][:, 3].min() - tol, p[inside][:, 3].max() + tol
    flat = p.reshape(-1, 4)
    ok = np.zeros(len(flat), bool)
    for i in range(0, len(cols), 64):
        d = np.abs(flat[:, None, :3] - cols[None, i:i + 64]).max(axis=2)
        ok |= (d <= tol).any(axis=1)
    ok &= (flat[:, 3] >= amin) & (flat[:, 3] <= amax)
    match = ok.reshape(sel.shape) | inside
    if contiguous:
        region = np.zeros(sel.shape, bool)
        for y, x in zip(*np.nonzero(inside)):
            if not region[y, x]: region |= flood4(match, x, y)
    else:
        region = match
    return np.maximum(np.where(region, 255, 0), sel).astype(np.uint8)


class SelectTools(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers ---------------------------------------------------------------------------------------------------------
    def fresh(self, name='edit-photo.png'):
        b = self.browser
        b.select_asset(name)
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true")
        return b

    def tool(self, tid, **opts):
        """Activate a tool with its default options plus `opts` (options persist between tests otherwise)."""
        b = self.browser
        b.eval(f"""const t = Ed.tools.get({json.dumps(tid)}); const d = {{}};
            for (const o of t.options || []) if (o.key && o.type !== 'button') d[o.key] = o.default;
            Ed.setTool({json.dumps(tid)}); Ed.setToolOptions({json.dumps(tid)}, Object.assign(d, {json.dumps(opts)})); true""")
        b.settle()

    def sel(self):
        return self.browser.editor_pixels('selection')[..., 3]

    def labels(self):
        return self.browser.eval('Ed.history.list().map(s => s.label)')

    def drag(self, pts, **kw):
        self.browser.drag_image(pts, center=False, **kw)

    def click_at(self, x, y, **kw):
        b = self.browser
        b.click(*b.image_to_client(x, y), **kw)

    def assertMask(self, actual, expected, max_abs=0, msg=''):
        self.assertPixelsClose(np.repeat(actual[..., None], 4, 2), np.repeat(expected[..., None], 4, 2), max_abs=max_abs, msg=msg)

    # ----- tools, keys, options --------------------------------------------------------------------------------------------
    def test_01_tools_keys_and_options_bar(self):
        b = self.fresh()
        slots = b.eval("Ed.toolSlots().map(s => [s.group, s.tools.map(t => t.id)])")
        groups = dict((g, ids) for g, ids in slots)
        self.assertEqual(groups['marquee'], ['marquee-rect', 'marquee-ellipse', 'marquee-row', 'marquee-col'])
        self.assertEqual(groups['lasso'][:2], ['lasso', 'lasso-poly'])
        self.assertIn('magic-wand', groups['wand'])
        order = [g for g, _ in slots]
        self.assertLess(order.index('marquee'), order.index('lasso'))
        self.assertLess(order.index('lasso'), order.index('wand'))
        # M picks the marquee slot, Shift+M cycles Rectangular <-> Elliptical (the single row/column have no key).
        b.eval("Ed.setTool('hand')"); b.mouse_move(*b.pixel_center(128, 128))
        b.key('m'); self.assertEqual(b.eval('Ed.tool'), 'marquee-rect')
        b.key('M'); self.assertEqual(b.eval('Ed.tool'), 'marquee-ellipse')
        b.key('M'); self.assertEqual(b.eval('Ed.tool'), 'marquee-rect')
        b.key('l'); self.assertEqual(b.eval('Ed.tool'), 'lasso')
        b.key('L'); self.assertEqual(b.eval('Ed.tool'), 'lasso-poly')
        b.key('w'); self.assertIn(b.eval('Ed.tool'), ('magic-wand', 'quick-select'))
        tips = dict(b.eval("[...document.querySelectorAll('#edTools .ed-tool')].map(e => [e.dataset.tool, e.title])"))
        self.assertTrue(any(t.startswith('Rectangular Marquee Tool (M)') or t.startswith('Elliptical Marquee Tool (M)') for t in tips.values()))
        # The options bar: four mode buttons, Feather, Style, Select and Mask.
        self.tool('marquee-rect')
        modes = b.eval("[...document.querySelectorAll('#edOptions .ed-selmode-btn')].map(e => [e.dataset.mode, e.getAttribute('aria-checked')])")
        self.assertEqual(modes, [['new', 'true'], ['add', 'false'], ['subtract', 'false'], ['intersect', 'false']])
        b.click_selector('#edOptions .ed-selmode-btn[data-mode="add"]')
        self.assertEqual(b.eval("Ed.toolOptions('marquee-rect').mode"), 'add')
        self.assertEqual(b.eval("document.querySelector('#edOptions .ed-selmode-btn[data-mode=\"add\"]').getAttribute('aria-checked')"), 'true')
        self.assertTrue(b.eval("!!document.querySelector('#edOptions .ed-field[data-key=feather] input')"))
        self.assertTrue(b.eval("document.querySelector('#edOptions .ed-selstyle-wh').hidden"), 'W/H only for Fixed Ratio / Fixed Size')
        b.set_value('#edOptions .ed-selstyle select', 'ratio')
        self.assertEqual(b.eval("Ed.toolOptions('marquee-rect').style"), 'ratio')
        self.assertFalse(b.eval("document.querySelector('#edOptions .ed-selstyle-wh').hidden"))
        self.assertNoBrowserErrors()

    # ----- rectangular marquee ---------------------------------------------------------------------------------------------
    def test_02_rectangular_marquee_is_exact_and_one_history_state(self):
        b = self.fresh()
        self.tool('marquee-rect')
        self.drag([(40.2, 30.1), (90.3, 70.4)])
        self.assertMask(self.sel(), rect_mask(256, 256, 40, 30, 90, 70))
        self.assertEqual(self.labels()[-1], 'Rectangular Marquee')
        # Dragging backwards gives the same rectangle; the new one replaces the old.
        self.drag([(120.4, 150.2), (100.1, 110.3)])
        self.assertMask(self.sel(), rect_mask(256, 256, 100, 110, 120, 150))
        # Off the canvas: clipped to it.
        self.drag([(-20, -10), (30.2, 20.1)])
        self.assertMask(self.sel(), rect_mask(256, 256, 0, 0, 30, 20))
        b.key('Ctrl+Z')
        self.assertMask(self.sel(), rect_mask(256, 256, 100, 110, 120, 150), msg='undo brings the previous selection back')
        self.assertNoBrowserErrors()

    def test_03_marquee_modifier_keys_held_on_the_keyboard(self):
        b = self.fresh()
        self.tool('marquee-rect')
        # No selection: Shift squares, Alt draws from the centre, both = a square from the centre.
        with b.hold('Shift'): self.drag([(20.2, 20.2), (60.1, 40.3)])
        self.assertMask(self.sel(), rect_mask(256, 256, 20, 20, 60, 60), msg='Shift: square')
        b.key('Ctrl+D')
        with b.hold('Alt'): self.drag([(100.2, 100.1), (120.3, 110.4)])
        self.assertMask(self.sel(), rect_mask(256, 256, 80, 90, 120, 110), msg='Alt: from the centre')
        b.key('Ctrl+D')
        with b.hold('Shift', 'Alt'): self.drag([(100.2, 100.1), (120.3, 110.4)])
        self.assertMask(self.sel(), rect_mask(256, 256, 80, 80, 120, 120), msg='Shift+Alt: square from the centre')
        # With a selection: Shift adds, Alt subtracts, Shift+Alt intersects (held before the drag starts).
        b.key('Ctrl+D')
        self.drag([(10.2, 10.2), (50.1, 50.1)])
        with b.hold('Shift'): self.drag([(40.2, 40.2), (80.1, 60.1)])
        ref = np.maximum(rect_mask(256, 256, 10, 10, 50, 50), rect_mask(256, 256, 40, 40, 80, 60))
        self.assertMask(self.sel(), ref, msg='Shift: add (not a square)')
        with b.hold('Alt'): self.drag([(20.2, 20.2), (30.1, 70.3)])
        ref[20:70, 20:30] = 0
        self.assertMask(self.sel(), ref, msg='Alt: subtract (not from the centre)')
        with b.hold('Shift', 'Alt'): self.drag([(0.2, 0.2), (45.1, 45.1)])
        ref = np.minimum(ref, rect_mask(256, 256, 0, 0, 45, 45))
        self.assertMask(self.sel(), ref, msg='Shift+Alt: intersect')
        self.assertEqual(self.labels()[-3:], ['Rectangular Marquee'] * 3)
        # Adding while constraining: start with Shift (add), release it and press it again during the drag.
        b.key('Ctrl+D'); self.drag([(150.2, 150.2), (160.1, 160.1)])
        b.key_down('Shift')
        b.mouse_move(*b.image_to_client(10.2, 150.2)); b.mouse_down(*b.image_to_client(10.2, 150.2))
        b.mouse_move(*b.image_to_client(40.1, 170.1))
        b.key_up('Shift'); b.mouse_move(*b.image_to_client(40.2, 170.2))
        b.key_down('Shift'); b.mouse_move(*b.image_to_client(40.3, 170.3))
        b.mouse_up(*b.image_to_client(40.3, 170.3)); b.key_up('Shift')
        ref = np.maximum(rect_mask(256, 256, 150, 150, 160, 160), rect_mask(256, 256, 10, 150, 40, 180))
        self.assertMask(self.sel(), ref, msg='Shift pressed again during an add drag squares the box')
        self.assertNoBrowserErrors()

    def test_04_space_moves_the_marquee_while_drawing_and_escape_cancels(self):
        b = self.fresh()
        self.tool('marquee-rect')
        p = lambda x, y: b.image_to_client(x, y)
        b.mouse_move(*p(20.2, 20.2)); b.mouse_down(*p(20.2, 20.2))
        b.mouse_move(*p(40.1, 30.1))                   # a 20 x 10 box
        b.key_down(' ')
        b.mouse_move(*p(70.1, 80.1))                   # Space held: the box moves by (+30, +50)
        b.key_up(' ')
        b.mouse_move(*p(80.1, 90.1))                   # resizing again from the moved corner
        b.mouse_up(*p(80.1, 90.1))
        self.assertMask(self.sel(), rect_mask(256, 256, 50, 70, 80, 90))
        self.assertEqual(b.eval('Ed.view.getState().px !== undefined'), True)
        # Escape during the drag cancels it: the selection stays.
        before = self.sel()
        b.mouse_move(*p(100.2, 100.2)); b.mouse_down(*p(100.2, 100.2)); b.mouse_move(*p(140.2, 140.2))
        b.key('Escape'); b.mouse_up(*p(140.2, 140.2))
        self.assertMask(self.sel(), before)
        self.assertNoBrowserErrors()

    def test_05_click_deselects_drag_inside_moves_the_outline_and_arrows_nudge(self):
        b = self.fresh()
        self.tool('marquee-rect')
        self.drag([(40.2, 40.2), (80.2, 70.2)])
        photo = b.editor_pixels()
        # Dragging inside the selection moves the outline, not the pixels.
        self.drag([(50.4, 50.4), (70.4, 45.4)])
        self.assertMask(self.sel(), rect_mask(256, 256, 60, 35, 100, 65))
        self.assertEqual(self.labels()[-1], 'Move Selection')
        self.assertPixelsClose(b.editor_pixels(), photo, msg='pixels stay where they are')
        # Shift pressed after the move started: 45 degree steps (Shift held before the press would add instead).
        p = lambda x, y: b.image_to_client(x, y)
        b.mouse_move(*p(70.4, 50.4)); b.mouse_down(*p(70.4, 50.4)); b.mouse_move(*p(80.4, 52.4))
        b.key_down('Shift'); b.mouse_move(*p(90.4, 54.4)); b.mouse_up(*p(90.4, 54.4)); b.key_up('Shift')
        self.assertMask(self.sel(), rect_mask(256, 256, 80, 35, 120, 65))
        # Arrow keys nudge by 1, Shift+arrow by 10.
        b.click(*b.image_to_client(200.5, 200.5))   # a click outside deselects ...
        self.assertFalse(b.eval('Ed.select.hasSelection()'))
        b.key('Ctrl+Z')                              # ... and is undoable
        self.assertMask(self.sel(), rect_mask(256, 256, 80, 35, 120, 65))
        b.key('ArrowRight'); b.key('ArrowDown'); b.key('ArrowDown')
        self.assertMask(self.sel(), rect_mask(256, 256, 81, 37, 121, 67))
        b.key('ArrowLeft', SHIFT)
        self.assertMask(self.sel(), rect_mask(256, 256, 71, 37, 111, 67))
        # A click inside the selection deselects too (New mode).
        self.click_at(90.5, 50.5)
        self.assertFalse(b.eval('Ed.select.hasSelection()'))
        self.assertNoBrowserErrors()

    # ----- elliptical marquee and feather ----------------------------------------------------------------------------------
    def test_06_elliptical_marquee_antialias_matches_analytic_coverage(self):
        b = self.fresh()
        self.tool('marquee-ellipse')
        self.drag([(30.2, 40.1), (131.3, 101.2)])     # 101 x 61 box: odd sizes put the centre mid-pixel
        ref = ellipse_coverage(256, 256, 30, 40, 131, 101)
        got = self.sel()
        self.assertMask(got, ref, max_abs=1, msg='anti-aliased ellipse = covered area')
        self.assertEqual(self.labels()[-1], 'Elliptical Marquee')
        self.assertGreater(int(((got > 0) & (got < 255)).sum()), 200, 'the edge really is soft')
        # Anti-alias off: pixel centres.
        self.tool('marquee-ellipse', antialias=False)
        self.drag([(150.2, 20.1), (221.3, 90.2)])
        self.assertMask(self.sel(), ellipse_centres(256, 256, 150, 20, 221, 90))
        # Shift: circle; Alt: from the centre.
        self.tool('marquee-ellipse')
        b.key('Ctrl+D')
        with b.hold('Shift', 'Alt'): self.drag([(128.2, 128.2), (148.1, 138.1)])
        self.assertMask(self.sel(), ellipse_coverage(256, 256, 108, 108, 148, 148), max_abs=1)
        self.assertNoBrowserErrors()

    def test_07_feather_is_a_gaussian_with_sigma_equal_to_the_radius(self):
        b = self.fresh()
        for radius in (2.5, 6, 14, 30, 120):
            self.tool('marquee-rect', feather=radius)
            self.drag([(80.2, 70.2), (170.1, 160.1)])
            ref = gaussian_blur(rect_mask(256, 256, 80, 70, 170, 160), radius)
            got = self.sel().astype(float)
            err = np.abs(got - ref)
            self.assertLessEqual(err.max(), 1.5, f'feather {radius}: max error {err.max():.2f}')
            self.assertLess(err.mean(), .3, f'feather {radius}')
            self.assertEqual(self.labels()[-1], 'Rectangular Marquee')
        # Touching the canvas edge: the edge repeats outward (the selection stays solid along the border).
        self.tool('marquee-rect', feather=8)
        self.drag([(-30, 100.2), (60.1, 200.1)])
        ref = gaussian_blur(rect_mask(256, 256, 0, 100, 60, 200), 8)
        self.assertLessEqual(np.abs(self.sel() - ref).max(), 1)
        self.assertEqual(int(self.sel()[150, 0]), 255)
        # Select > Modify > Feather with "Apply effect at canvas bounds": outside counts as unselected.
        b.key('Ctrl+A')
        b.eval("Ed.menu.run('Select/Modify/Feather...', {radius: 5, atBounds: true})")
        ref = gaussian_blur(np.full((256, 256), 255, np.uint8), 5, edge='constant')
        self.assertLessEqual(np.abs(self.sel() - ref).max(), 1)
        self.assertEqual(self.labels()[-1], 'Feather')
        b.eval("Ed.menu.run('Select/All')")
        b.eval("Ed.menu.run('Select/Modify/Feather...', {radius: 5, atBounds: false})")
        self.assertEqual(int(self.sel().min()), 255, 'without it Select All stays whole')
        self.assertNoBrowserErrors()

    def test_08_fixed_ratio_fixed_size_and_single_row_column(self):
        b = self.fresh()
        self.tool('marquee-rect', style='ratio', ratioW=2, ratioH=1)
        self.drag([(20.2, 20.2), (60.1, 30.3)])        # wider than 2:1 -> width 40, height 20
        self.assertMask(self.sel(), rect_mask(256, 256, 20, 20, 60, 40))
        self.tool('marquee-rect', style='size', sizeW=33, sizeH=17)
        self.click_at(100.2, 100.2)                    # a click places the box
        self.assertMask(self.sel(), rect_mask(256, 256, 100, 100, 133, 117))
        with b.hold('Alt'): self.click_at(110.2, 105.2)   # with a selection Alt subtracts the box placed at the click
        ref = rect_mask(256, 256, 100, 100, 133, 117); ref[105:117, 110:133] = 0
        self.assertMask(self.sel(), ref)
        b.key('Ctrl+D')
        with b.hold('Alt'): self.click_at(50.1, 200.1)  # no selection: Alt centres the box on the click
        self.assertMask(self.sel(), rect_mask(256, 256, 34, 192, 67, 209))
        # The W / H fields and the swap button in the options bar.
        self.assertEqual(b.eval("[document.querySelector('#edOptions [name=styleWidth]').value, document.querySelector('#edOptions [name=styleHeight]').value]"), ['33', '17'])
        b.click_selector('#edOptions .ed-selstyle-swap')
        self.assertEqual(b.eval("[Ed.toolOptions('marquee-rect').sizeW, Ed.toolOptions('marquee-rect').sizeH]"), [17, 33])
        b.set_value('#edOptions [name=styleWidth]', 20)
        self.assertEqual(b.eval("Ed.toolOptions('marquee-rect').sizeW"), 20)
        b.key('Ctrl+D')
        self.click_at(5.4, 6.6)
        self.assertMask(self.sel(), rect_mask(256, 256, 5, 7, 25, 40))
        self.tool('marquee-row')
        self.click_at(77.3, 90.6)
        self.assertMask(self.sel(), rect_mask(256, 256, 0, 90, 256, 91))
        self.assertEqual(self.labels()[-1], 'Single Row Marquee')
        self.tool('marquee-col')
        with b.hold('Shift'): self.click_at(12.7, 3.3)
        self.assertMask(self.sel(), np.maximum(rect_mask(256, 256, 0, 90, 256, 91), rect_mask(256, 256, 12, 0, 13, 256)))
        self.assertNoBrowserErrors()

    # ----- lasso tools -----------------------------------------------------------------------------------------------------
    def test_09_lasso_freehand_matches_the_polygon_area(self):
        b = self.fresh()
        self.tool('lasso')
        pts = [(30.3, 40.2), (120.7, 30.1), (90.2, 90.6), (140.4, 150.3), (40.1, 130.8)]
        self.drag(pts)                                  # released at the last point: closed with a straight line
        self.assertMask(self.sel(), polygon_coverage(256, 256, pts), max_abs=2, msg='anti-aliased lasso = covered area')
        self.assertEqual(self.labels()[-1], 'Lasso')
        # Anti-alias off: pixel centres. Alt held from the start subtracts from the selection.
        self.tool('lasso', antialias=False)
        b.key('Ctrl+D')
        self.drag(pts)
        self.assertMask(self.sel(), polygon_centres(256, 256, pts))
        cut = [(60.2, 20.3), (100.1, 20.3), (100.1, 200.4), (60.2, 200.4)]
        with b.hold('Alt'): self.drag(cut)
        self.assertMask(self.sel(), np.minimum(polygon_centres(256, 256, pts), 255 - polygon_centres(256, 256, cut)))
        self.assertEqual(self.labels()[-2:], ['Lasso', 'Lasso'])
        # Alt pressed during a drag, then the button released: straight segments; each click adds a corner; releasing Alt closes.
        b.key('Ctrl+D')
        p = lambda x, y: b.image_to_client(x, y)
        b.mouse_move(*p(20.2, 20.2)); b.mouse_down(*p(20.2, 20.2)); b.mouse_move(*p(40.2, 20.2)); b.mouse_move(*p(60.3, 20.2))
        b.key_down('Alt'); b.mouse_up(*p(60.3, 20.2))
        b.click(*p(60.3, 70.1)); b.click(*p(20.2, 70.1))
        b.key_up('Alt')
        self.assertMask(self.sel(), polygon_centres(256, 256, [(20.2, 20.2), (60.3, 20.2), (60.3, 70.1), (20.2, 70.1)]))
        # Escape cancels a lasso in progress; a plain click deselects.
        before = self.sel()
        b.mouse_move(*p(150.2, 150.2)); b.mouse_down(*p(150.2, 150.2)); b.mouse_move(*p(200.2, 160.2)); b.mouse_move(*p(180.2, 210.2))
        b.key('Escape'); b.mouse_up(*p(180.2, 210.2))
        self.assertMask(self.sel(), before)
        self.click_at(200.5, 30.5)
        self.assertFalse(b.eval('Ed.select.hasSelection()'))
        self.assertEqual(self.labels()[-1], 'Deselect')
        self.assertNoBrowserErrors()

    def test_10_polygonal_lasso_clicks_closes_and_keys(self):
        b = self.fresh()
        self.tool('lasso-poly')
        pts = [(50.3, 30.6), (180.2, 60.4), (120.6, 140.1), (160.5, 200.3), (30.4, 170.8)]
        for x, y in pts[:-1]: self.click_at(x, y)
        b.double_click(*b.image_to_client(*pts[-1]))     # a double-click closes
        self.assertMask(self.sel(), polygon_coverage(256, 256, pts), max_abs=2)
        self.assertEqual(self.labels()[-1], 'Polygonal Lasso')
        # A click on the start point closes; Backspace removes the last corner first.
        self.tool('lasso-poly', antialias=False)
        b.key('Ctrl+D')
        n = len(self.labels())
        tri = [(20.2, 20.2), (90.4, 20.2), (20.2, 110.6)]
        for x, y in tri: self.click_at(x, y)
        self.click_at(200.3, 200.3); b.key('Backspace')
        self.click_at(*tri[0])
        self.assertMask(self.sel(), polygon_centres(256, 256, tri))
        # Enter closes; Shift snaps the segment to 45 degrees (the corner lands on the snapped line).
        b.key('Ctrl+D')
        self.click_at(100.2, 100.2)
        with b.hold('Shift'): self.click_at(150.2, 104.7)  # snapped to horizontal: (150.2, 100.2)
        self.click_at(150.2, 160.2)
        b.key('Enter')
        self.assertMask(self.sel(), polygon_centres(256, 256, [(100.2, 100.2), (150.2, 100.2), (150.2, 160.2)]))
        # Escape cancels; Ctrl+click closes; Shift at the first click adds.
        before = self.sel()
        self.click_at(10.2, 200.2); self.click_at(60.2, 200.2); self.click_at(60.2, 250.2)
        b.key('Escape')
        self.assertMask(self.sel(), before, msg='Escape keeps the old selection')
        with b.hold('Shift'): self.click_at(10.2, 200.2)
        self.click_at(60.2, 200.2); self.click_at(60.2, 247.3)
        self.click_at(30.3, 240.3, modifiers=CTRL)
        ref = np.maximum(before, polygon_centres(256, 256, [(10.2, 200.2), (60.2, 200.2), (60.2, 247.3)]))
        self.assertMask(self.sel(), ref)
        self.assertEqual(self.labels()[n:], ['Polygonal Lasso', 'Deselect', 'Polygonal Lasso', 'Polygonal Lasso'])
        self.assertNoBrowserErrors()

    # ----- Magic Wand ------------------------------------------------------------------------------------------------------
    def wand_image(self):
        rng = np.random.default_rng(3)
        h = w = 128
        y, x = np.mgrid[0:h, 0:w]
        img = np.zeros((h, w, 4), np.uint8)
        img[..., 0] = np.clip(60 + x + rng.integers(-25, 26, (h, w)), 0, 255)
        img[..., 1] = np.clip(200 - y + rng.integers(-25, 26, (h, w)), 0, 255)
        img[..., 2] = np.clip(120 + rng.integers(-25, 26, (h, w)), 0, 255)
        img[..., 3] = 255
        # Exact tolerance boundaries (sample (200, 200, 200)): 32 levels in, 33 out, in any one channel.
        img[0:40, 0:40] = (200, 200, 200, 255)
        img[5:10, 5:10] = (232, 200, 200, 255)
        img[5:10, 15:20] = (233, 200, 200, 255)
        img[15:20, 5:10] = (200, 168, 200, 255)
        img[15:20, 15:20] = (200, 200, 167, 255)
        img[25:30, 25:30] = (190, 210, 220, 255)       # all channels within 20
        # A gray island cut off by a ring of red (left out when contiguous); two squares touching only at a corner.
        img[2:12, 26:38] = (255, 0, 0, 255)
        img[4:10, 28:36] = (200, 200, 200, 255)
        img[40:50, 40:50] = (200, 200, 200, 255)
        img[40:50, 30:40] = (0, 0, 0, 255); img[30:40, 40:50] = (0, 0, 0, 255)
        return img

    def test_11_magic_wand_tolerance_contiguous_sample_size(self):
        b = self.fresh()
        img = self.wand_image()
        b.load_editor_image(img)
        self.tool('magic-wand', antialias=False)
        self.click_at(2.5, 30.5)
        ref = wand_reference(img, 2, 30, 32)
        self.assertMask(self.sel(), ref)
        self.assertTrue(ref[7, 7] and not ref[7, 17] and ref[17, 7] and not ref[17, 17] and ref[27, 27] and not ref[6, 30] and not ref[45, 45])
        self.assertEqual(self.labels()[-1], 'Magic Wand')
        self.tool('magic-wand', antialias=False, contiguous=False)
        self.click_at(2.5, 30.5)
        ref = wand_reference(img, 2, 30, 32, contiguous=False)
        self.assertMask(self.sel(), ref)
        self.assertTrue(ref[6, 30] and ref[45, 45])
        # Noisy gradient: several tolerances and sample sizes, against the reference flood fill.
        for tol, size, x, y in ((0, 1, 80, 80), (10, 1, 70, 90), (40, 1, 100, 60), (25, 5, 90, 100), (60, 11, 64, 64)):
            self.tool('magic-wand', antialias=False, tolerance=tol, sampleSize=size)
            self.click_at(x + .5, y + .5)
            self.assertMask(self.sel(), wand_reference(img, x, y, tol, size=size), msg=f'tolerance {tol}, sample {size}')
        # Shift adds, Alt subtracts.
        self.tool('magic-wand', antialias=False)
        self.click_at(2.5, 30.5)
        a = wand_reference(img, 2, 30, 32)
        with b.hold('Shift'): self.click_at(45.5, 45.5)
        a = np.maximum(a, wand_reference(img, 45, 45, 32))
        self.assertMask(self.sel(), a)
        with b.hold('Alt'): self.click_at(27.5, 27.5)
        a = np.minimum(a, 255 - wand_reference(img, 27, 27, 32))
        self.assertMask(self.sel(), a)
        self.assertNoBrowserErrors()

    def test_12_magic_wand_antialias_transparency_and_sample_all_layers(self):
        b = self.fresh()
        img = self.wand_image()
        b.load_editor_image(img)
        self.tool('magic-wand', tolerance=40)              # Anti-alias on (the default); a ragged region in the noise
        self.click_at(100.5, 60.5)
        hard = wand_reference(img, 100, 60, 40) > 0
        got = self.sel()
        self.assertMask(np.where(got >= 128, 255, 0).astype(np.uint8), np.where(hard, 255, 0).astype(np.uint8), msg='the 50% outline is the hard region')
        padded = np.pad(hard, 1, mode='edge')
        n4 = [padded[1:-1, 2:], padded[1:-1, :-2], padded[2:, 1:-1], padded[:-2, 1:-1]]
        self.assertTrue(np.all(got[hard & np.all(n4, axis=0)] == 255), 'the inside stays fully selected')
        self.assertTrue(np.all(got[~hard & ~np.any(n4, axis=0)] == 0), 'nothing away from the edge outside')
        self.assertGreater(int(((got > 0) & (got < 255)).sum()), 50, 'stair steps are softened')
        # Transparency counts: on the sprite a click on empty pixels takes every empty pixel connected to it.
        b.select_asset('edit-sprite.png')
        sprite = self.original('edit-sprite.png')
        self.tool('magic-wand', antialias=False, tolerance=0)
        self.click_at(60.5, 2.5)
        self.assertMask(self.sel(), wand_reference(sprite, 60, 2, 0))
        # Sample All Layers: a layer on top with a red square; the wand reads the active layer or the composite.
        b.load_editor_image(img)
        top = np.zeros_like(img); top[60:100, 60:100] = (255, 0, 0, 255)
        b.eval(f"const c = document.createElement('canvas'); c.width = 128; c.height = 128; c.getContext('2d').putImageData({b.push_pixels(top)}, 0, 0); Ed.layers.add({{name: 'Top', canvas: c}}); true")
        self.tool('magic-wand', antialias=False)
        self.click_at(2.5, 30.5)
        self.assertMask(self.sel(), wand_reference(top, 2, 30, 32), msg='active layer: its transparent area')
        self.tool('magic-wand', antialias=False, sampleAll=True)
        self.click_at(2.5, 30.5)
        comp = img.copy(); comp[60:100, 60:100] = (255, 0, 0, 255)
        self.assertMask(self.sel(), wand_reference(comp, 2, 30, 32), msg='all layers: the composite')
        # A targeted layer mask is sampled as gray.
        mask = np.full((128, 128), 255, np.uint8); mask[:, 64:] = 0
        mimg = np.dstack([np.full((128, 128, 3), 255, np.uint8), mask])
        b.eval(f"const c = document.createElement('canvas'); c.width = 128; c.height = 128; c.getContext('2d').putImageData({b.push_pixels(mimg)}, 0, 0); Ed.layers.set(1, {{mask: c}}); Ed.layers.setActive(1, 'mask'); true")
        self.tool('magic-wand', antialias=False, sampleAll=False)
        self.click_at(10.5, 10.5)
        self.assertMask(self.sel(), np.where(mask == 255, 255, 0).astype(np.uint8))
        self.assertNoBrowserErrors()

    # ----- Select menu -----------------------------------------------------------------------------------------------------
    def base_selection(self):
        """A rectangle plus an overlapping ellipse plus a box on the canvas edge, made with the marquees."""
        b = self.browser
        self.tool('marquee-rect')
        self.drag([(40.2, 60.2), (120.1, 150.1)])
        self.tool('marquee-ellipse')
        with b.hold('Shift'): self.drag([(100.2, 30.2), (210.1, 110.1)])
        self.tool('marquee-rect')
        with b.hold('Shift'): self.drag([(-10, 200.2), (60.1, 300)])
        return self.sel()

    def run_dialog(self, path, title, values):
        """Open a Select menu command from the menubar, fill its dialog, press OK."""
        b = self.browser
        b.click_selector('#edMenubar [data-menu="Select"]')
        for name in path.split('/')[1:-1]:          # submenus by their label
            x, y = b.eval(f"(() => {{ const e = [...document.querySelectorAll('.ed-menu .ed-menu-item')].find(e => e.querySelector('.ed-menu-label')?.textContent === {json.dumps(name)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
            b.click(x, y)
        b.click_selector(f'.ed-menu .ed-menu-item[data-path="{path}"]')
        dlg = f'dialog[data-ed-dialog="{title}"]'
        b.wait_for(f"!!document.querySelector('{dlg}')")
        for k, v in values.items(): b.set_value(f'{dlg} [name="{k}"]', v)
        b.click_selector(f'{dlg} button[type=submit]')
        b.wait_for(f"!document.querySelector('{dlg}')")
        b.settle()

    def test_13_modify_expand_contract_smooth_border(self):
        b = self.fresh()
        sel0 = self.base_selection()
        inside = sel0 >= 128
        # Expand by 5 (through the menubar and the dialog): every pixel within 5 px of the selection.
        self.run_dialog('Select/Modify/Expand...', 'Expand Selection', {'by': 5})
        self.assertMask(self.sel(), np.where(dist_to(inside) <= 5, 255, 0).astype(np.uint8))
        self.assertEqual(self.labels()[-1], 'Expand')
        b.key('Ctrl+Z'); self.assertMask(self.sel(), sel0, msg='undo restores the selection')
        # Contract by 4: the canvas edge is not an edge of the selection unless "Apply effect at canvas bounds" is on.
        self.run_dialog('Select/Modify/Contract...', 'Contract Selection', {'by': 4, 'atBounds': False})
        self.assertMask(self.sel(), np.where(dist_to(~inside) > 4, 255, 0).astype(np.uint8))
        self.assertEqual(self.labels()[-1], 'Contract')
        b.key('Ctrl+Z')
        b.eval("Ed.menu.run('Select/Modify/Contract...', {by: 4, atBounds: true})")
        out = np.pad(~inside, 1, constant_values=True)
        self.assertMask(self.sel(), np.where(dist_to(out)[1:-1, 1:-1] > 4, 255, 0).astype(np.uint8))
        b.key('Ctrl+Z')
        # Smooth (sample radius 6): a pixel is selected when more than half of the 13 x 13 square around it is.
        for at, mode in ((False, 'edge'), (True, 'constant')):
            b.eval(f"Ed.menu.run('Select/Modify/Smooth...', {{radius: 6, atBounds: {json.dumps(at)}}})")
            ref = np.where(2 * box_sum(sel0, 6, mode) > 255 * 13 * 13, 255, 0).astype(np.uint8)
            self.assertMask(self.sel(), ref, msg=f'smooth, at bounds {at}')
            self.assertEqual(self.labels()[-1], 'Smooth')
            b.key('Ctrl+Z')
        # Border 8: a soft band 8 px wide centred on the edge (4 in, 4 out, a 2 px ramp each side).
        b.eval("Ed.menu.run('Select/Modify/Border...', {width: 8})")
        d_out = dist_to(inside)
        d_in = dist_to(np.pad(~inside, 1, constant_values=True))[1:-1, 1:-1]
        e = np.where(inside, d_in, d_out) - .5
        ref = np.round(255 * np.clip((4 - e) / 2 + .5, 0, 1)).astype(np.uint8)
        self.assertMask(self.sel(), ref, max_abs=1)
        self.assertEqual(self.labels()[-1], 'Border')
        b.key('Ctrl+Z')
        self.assertMask(self.sel(), sel0)
        self.assertNoBrowserErrors()

    def test_14_grow_and_similar_use_the_wand_options(self):
        b = self.fresh()
        img = self.wand_image()
        b.load_editor_image(img)
        self.tool('magic-wand', antialias=False, tolerance=20)
        self.tool('marquee-rect')
        self.drag([(70.2, 70.2), (74.1, 73.1)])
        sel0 = self.sel()
        b.eval("Ed.menu.run('Select/Grow')")
        self.assertMask(self.sel(), grow_reference(img, sel0, 20, True))
        self.assertEqual(self.labels()[-1], 'Grow')
        b.key('Ctrl+Z')
        b.eval("Ed.menu.run('Select/Similar')")
        self.assertMask(self.sel(), grow_reference(img, sel0, 20, False))
        self.assertEqual(self.labels()[-1], 'Similar')
        # A soft selection keeps its soft values; the colours of pixels under 50% are not used.
        b.key('Ctrl+Z')
        self.tool('marquee-ellipse', feather=2)
        self.drag([(4.2, 4.2), (36.1, 36.1)])
        sel1 = self.sel()
        self.tool('magic-wand', antialias=False, tolerance=8)
        b.eval("Ed.menu.run('Select/Grow')")
        self.assertMask(self.sel(), grow_reference(img, sel1, 8, True))
        self.assertNoBrowserErrors()

    # ----- marching ants, layout, speed ------------------------------------------------------------------------------------
    def overlay_alpha(self):
        b = self.browser
        b.settle(2)
        if b.eval("document.getElementById('viewOverlay').hidden"): return None
        return b.pixels("document.getElementById('viewOverlay')")

    def outline_band(self, x0, y0, x1, y1, pad=2):
        """Overlay pixels within `pad` px of the outline of image rectangle (x0, y0)-(x1, y1), and the outline itself."""
        b = self.browser
        dpr, left, top = b.eval("[Math.min(devicePixelRatio, 2), document.getElementById('viewport').getBoundingClientRect().left, document.getElementById('viewport').getBoundingClientRect().top]")
        (ax, ay), (cx, cy) = b.image_to_client(x0, y0), b.image_to_client(x1, y1)
        ax, ay, cx, cy = [(v - o) * dpr for v, o in ((ax, left), (ay, top), (cx, left), (cy, top))]
        h, w = b.eval("[document.getElementById('viewOverlay').height, document.getElementById('viewOverlay').width]")
        yy, xx = np.mgrid[0:h, 0:w] + .5
        inside_outer = (xx >= ax - pad * dpr) & (xx <= cx + pad * dpr) & (yy >= ay - pad * dpr) & (yy <= cy + pad * dpr)
        inside_inner = (xx > ax + pad * dpr) & (xx < cx - pad * dpr) & (yy > ay + pad * dpr) & (yy < cy - pad * dpr)
        return inside_outer & ~inside_inner, 2 * ((cx - ax) + (cy - ay))

    def test_15_marching_ants_show_while_drawing_and_after(self):
        b = self.fresh()
        b.eval("Ed.view.fit(); true")
        self.tool('marquee-rect')
        b.eval("Ed.select.none(); true")
        p = lambda x, y: b.image_to_client(x, y)
        # While dragging: the box being drawn.
        b.mouse_move(*p(40.2, 40.2)); b.mouse_down(*p(40.2, 40.2)); b.mouse_move(*p(60.2, 50.2)); b.mouse_move(*p(100.2, 90.2))
        ov = self.overlay_alpha()
        self.assertIsNotNone(ov, 'the overlay shows the box being drawn')
        band, perim = self.outline_band(40, 40, 100, 90)
        drawn = ov[..., 3] > 0
        self.assertEqual(int((drawn & ~band).sum()), 0, 'nothing drawn away from the box')
        self.assertGreater(int((drawn & band).sum()), perim * .9, 'the whole box outline is drawn')
        b.mouse_up(*p(100.2, 90.2))
        # After: the shell's marching ants along the selection, black and white dashes that move.
        ov = self.overlay_alpha()
        drawn = ov[..., 3] > 0
        self.assertEqual(int((drawn & ~band).sum()), 0)
        self.assertGreater(int((drawn & band).sum()), perim * .9)
        on = ov[drawn][:, :3].astype(int).sum(axis=1)
        self.assertTrue((on < 60).sum() > perim * .3 and (on > 700).sum() > perim * .3, 'black and white dashes')
        time.sleep(.4)
        ov2 = self.overlay_alpha()
        self.assertFalse(np.array_equal(ov[..., :3], ov2[..., :3]), 'the ants march')
        # Moving the outline: the moved outline replaces the old ants during the drag.
        b.mouse_move(*p(70.4, 60.4)); b.mouse_down(*p(70.4, 60.4)); b.mouse_move(*p(80.4, 60.4)); b.mouse_move(*p(90.4, 70.4))
        ov = self.overlay_alpha()
        band2, perim2 = self.outline_band(60, 50, 120, 100)
        drawn = ov[..., 3] > 0
        self.assertGreater(int((drawn & band2).sum()), perim2 * .9, 'the outline follows the pointer')
        self.assertLess(int((drawn & band & ~band2).sum()), perim * .05, 'the old ants are hidden while moving')
        b.mouse_up(*p(90.4, 70.4))
        self.assertMask(self.sel(), rect_mask(256, 256, 60, 50, 120, 100))
        # Lasso and polygonal lasso draw their path; Escape clears it.
        self.tool('lasso-poly')
        self.click_at(150.2, 150.2); self.click_at(200.2, 150.2); b.mouse_move(*p(200.2, 200.2))
        ov = self.overlay_alpha()
        self.assertGreater(int((ov[..., 3] > 0).sum()), 50)
        b.key('Escape')
        self.assertMask(self.sel(), rect_mask(256, 256, 60, 50, 120, 100))
        self.assertNoBrowserErrors()

    def test_16_options_bars_fit_at_1366_and_screenshots(self):
        b = self.fresh()
        spill_js = """[...document.querySelectorAll('.ed-optionsbar *')].filter(e => {
            if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden') return false;
            const r = e.getBoundingClientRect(), p = e.closest('.ed-optionsbar').getBoundingClientRect();
            return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); })
            .map(e => (e.id || e.className || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 20))"""
        clip_js = """[...document.querySelectorAll('.ed-optionsbar label, .ed-optionsbar button, .ed-optionsbar select')]
            .filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => (e.id || e.className) + ':' + e.textContent.trim().slice(0, 20))"""
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h)
                b.eval("Ed.view.fit(); true"); b.settle(3)
                for tid, opts in (('marquee-rect', {'style': 'size'}), ('marquee-ellipse', {'style': 'ratio'}), ('marquee-row', {}), ('marquee-col', {}),
                                  ('lasso', {}), ('lasso-poly', {}), ('magic-wand', {'sampleSize': 101})):
                    self.tool(tid, **opts)
                    b.settle(2)
                    self.assertEqual(b.eval(spill_js), [], f'{tid}: options spill out of the bar at {w}x{h}')
                    self.assertEqual(b.eval(clip_js), [], f'{tid}: cut-off controls at {w}x{h}')
                    self.assertLessEqual(b.eval('document.documentElement.scrollWidth'), w)
                self.tool('marquee-ellipse', feather=0)
                self.drag([(60.2, 50.2), (190.1, 170.1)])
                self.tool('marquee-rect', style='size')
                b.mouse_move(*b.pixel_center(128, 128)); b.settle(3)
                b.screenshot(f'select-marquee-{w}x{h}.png')
                self.tool('magic-wand')
                b.settle(2)
                b.screenshot(f'select-wand-{w}x{h}.png')
                # Every Modify dialog fits in the window.
                for path, title in (('Border...', 'Border Selection'), ('Smooth...', 'Smooth Selection'), ('Expand...', 'Expand Selection'),
                                    ('Contract...', 'Contract Selection'), ('Feather...', 'Feather Selection')):
                    b.eval(f"Ed.menu.run('Select/Modify/{path}'); true")
                    dlg = f'dialog[data-ed-dialog="{title}"]'
                    b.wait_for(f"!!document.querySelector('{dlg}')"); b.settle(2)
                    r = b.eval(f"(() => {{ const r = document.querySelector('{dlg}').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }})()")
                    self.assertTrue(r[0] >= 0 and r[1] >= 0 and r[2] <= w and r[3] <= h, f'{title} fits at {w}x{h}: {r}')
                    self.assertEqual(b.eval(f"[...document.querySelectorAll('{dlg} *')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1 && !['INPUT','SELECT'].includes(e.tagName)).length"), 0)
                    if title == 'Feather Selection': b.screenshot(f'select-feather-dialog-{w}x{h}.png')
                    b.key('Escape'); b.wait_for(f"!document.querySelector('{dlg}')")
        finally:
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()

    def test_17_speed_on_a_2048_sheet(self):
        b = self.fresh('edit-big.png')
        timings = {}
        def timed(name, fn, limit=3.0):
            t = time.time(); fn(); b.settle(1); timings[name] = time.time() - t
            self.assertLess(timings[name], limit, f'{name} took {timings[name]:.2f} s')
        self.tool('magic-wand', tolerance=10)
        timed('magic wand', lambda: self.click_at(10.5, 10.5))
        self.assertTrue(b.eval('Ed.select.hasSelection()'))
        self.tool('magic-wand', tolerance=10, contiguous=False)
        timed('magic wand (not contiguous)', lambda: self.click_at(100.5, 100.5))
        b.key('Ctrl+D')
        self.tool('marquee-ellipse')
        timed('ellipse', lambda: self.drag([(100.2, 100.2), (1900.1, 1800.1)]))
        timed('feather 40', lambda: b.eval("Ed.menu.run('Select/Modify/Feather...', {radius: 40, atBounds: false})"))
        timed('feather 8', lambda: b.eval("Ed.menu.run('Select/Modify/Feather...', {radius: 8, atBounds: false})"))
        timed('expand 30', lambda: b.eval("Ed.menu.run('Select/Modify/Expand...', {by: 30})"))
        timed('contract 30', lambda: b.eval("Ed.menu.run('Select/Modify/Contract...', {by: 30, atBounds: false})"))
        timed('smooth 20', lambda: b.eval("Ed.menu.run('Select/Modify/Smooth...', {radius: 20, atBounds: false})"))
        timed('border 20', lambda: b.eval("Ed.menu.run('Select/Modify/Border...', {width: 20})"))
        self.tool('magic-wand', tolerance=10)
        timed('similar', lambda: b.eval("Ed.menu.run('Select/Similar')"))
        timed('grow', lambda: b.eval("Ed.menu.run('Select/Grow')"))
        self.tool('lasso')
        timed('lasso', lambda: self.drag([(100, 100), (1800, 300), (1000, 1900), (200, 1500)]))
        print('\n  selection speed on 2048 x 2048: ' + ', '.join(f'{k} {v:.2f} s' for k, v in timings.items()))
        self.assertNoBrowserErrors()

    def test_18_options_bars_fit_in_every_language(self):
        """The lab is translated (es, pt, fr, de): the longest labels must still fit the options bar at 1366 px."""
        b = self.fresh()
        spill_js = """[...document.querySelectorAll('.ed-optionsbar *')].filter(e => {
            if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden') return false;
            const r = e.getBoundingClientRect(), p = e.closest('.ed-optionsbar').getBoundingClientRect();
            return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); })
            .map(e => (e.id || e.className || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 20))"""
        try:
            b.set_viewport(1366, 768)
            for lang in ('es', 'pt', 'fr', 'de'):
                b.eval(f"LabI18n.set('{lang}')"); b.wait_for(f"LabI18n.lang === '{lang}'"); b.settle(3)
                for tid, opts in (('marquee-rect', {'style': 'size'}), ('marquee-ellipse', {'style': 'ratio'}), ('marquee-row', {}),
                                  ('lasso', {}), ('lasso-poly', {}), ('magic-wand', {'sampleSize': 101})):
                    self.tool(tid, **opts)
                    b.settle(3)
                    self.assertEqual(b.eval(spill_js), [], f'{tid}: options spill out of the bar in {lang}')
                    self.assertLessEqual(b.eval('document.documentElement.scrollWidth'), 1366)
        finally:
            b.eval("LabI18n.set('en')"); b.wait_for("LabI18n.lang === 'en'")
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()
