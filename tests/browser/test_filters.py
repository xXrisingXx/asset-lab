"""Filters (src/web/editor/filters.js and filters-worker.js) end to end in headless Edge with real mouse and keyboard input:
every filter of the Filter menu run through its menu and dialog (typed values, dragged sliders, segmented buttons, OK) and
checked against an independent numpy reference written from the filter's definition (Gaussian Blur with radius = sigma on
premultiplied colors, Box Blur, Blur / Blur More, the Sharpen family, Unsharp Mask with threshold, Median, Dust & Scratches,
Add Noise statistics, Mosaic cell averages, Maximum / Minimum morphology, High Pass, Offset, Emboss, Find Edges, Solarize,
Average, Motion Blur, the distortions, Clouds), undo, the soft selection, lock transparency, layer masks, the image edges,
the preview box (zoom, pan, original), the canvas preview, Last Filter, Edit > Fade, the workers under the page CSP and their
in-page fallback, a 2048 x 2048 sheet with its progress bar, and the dialogs at 1366 x 768.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_filters*.py" -v
"""
import json
import math
import time
import numpy as np
from harness import EditorTestCase, premultiplied
from fakegame import image_array, SPRITE


# ----- references: numpy, from the definitions ----------------------------------------------------------------------------
def premul(img):
    """Float RGBA with RGB multiplied by alpha / 255."""
    f = np.asarray(img, np.float64).copy()
    f[..., :3] *= f[..., 3:4] / 255
    return f


def to8(p):
    """Premultiplied float RGBA back to straight uint8 (fully transparent pixels get black)."""
    a = p[..., 3:4]
    rgb = np.where(a > 1e-3, p[..., :3] * 255 / np.where(a > 1e-3, a, 1), 0)
    return np.clip(np.round(np.concatenate([rgb, a], -1)), 0, 255).astype(np.uint8)


def color8(rgb, alpha):
    """Straight float RGB with the given alpha channel, as uint8."""
    return np.clip(np.round(np.concatenate([rgb, np.asarray(alpha, np.float64)[..., None]], -1)), 0, 255).astype(np.uint8)


def shifted(a, dx, dy):
    """a[y + dy, x + dx] with the edge pixels repeated beyond the image."""
    h, w = a.shape[:2]
    ys = np.clip(np.arange(h) + dy, 0, h - 1)
    xs = np.clip(np.arange(w) + dx, 0, w - 1)
    return a[ys][:, xs]


def conv1(a, k, axis):
    """Correlation with kernel k along one axis, edge pixels repeated."""
    r = len(k) // 2
    out = np.zeros(a.shape, np.float64)
    for i, wgt in enumerate(k):
        out += wgt * (shifted(a, i - r, 0) if axis == 1 else shifted(a, 0, i - r))
    return out


def separable(a, k): return conv1(conv1(a, k, 1), k, 0)


def gauss_kernel(s):
    r = max(1, int(math.ceil(4 * s)))
    x = np.arange(-r, r + 1)
    k = np.exp(-x * x / (2 * s * s))
    return k / k.sum()


def gaussian(a, s): return separable(np.asarray(a, np.float64), gauss_kernel(s))


def box(a, r): return separable(np.asarray(a, np.float64), np.ones(2 * r + 1) / (2 * r + 1))


def window(a, r, disk=False):
    """Every shifted copy of a over a (2r+1)^2 square (or the disk dx^2 + dy^2 <= r^2), stacked on a new first axis."""
    return np.stack([shifted(a, dx, dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if not disk or dx * dx + dy * dy <= r * r])


def bilinear(ch, u, v):
    """Sample a (H, W) channel at float pixel coordinates (pixel centres at integers), edge pixels repeated."""
    h, w = ch.shape
    x0, y0 = np.floor(u), np.floor(v)
    fx, fy = u - x0, v - y0
    x0, y0 = x0.astype(int), y0.astype(int)
    x1, y1 = np.clip(x0 + 1, 0, w - 1), np.clip(y0 + 1, 0, h - 1)
    x0, y0 = np.clip(x0, 0, w - 1), np.clip(y0, 0, h - 1)
    return ch[y0, x0] * (1 - fx) * (1 - fy) + ch[y0, x1] * fx * (1 - fy) + ch[y1, x0] * (1 - fx) * fy + ch[y1, x1] * fx * fy


def remap(img, fn):
    """Inverse mapping of the premultiplied picture: fn(X, Y) -> source (X, Y) for pixel centres X = x + .5, Y = y + .5."""
    h, w = img.shape[:2]
    Y, X = np.mgrid[0:h, 0:w].astype(np.float64) + .5
    sx, sy = fn(X, Y)
    p = premul(img)
    return to8(np.stack([bilinear(p[..., c], sx - .5, sy - .5) for c in range(4)], -1))


def noise_image(w, h, seed=7, opaque=True):
    a = np.random.default_rng(seed).integers(0, 256, (h, w, 4), dtype=np.uint8)
    if opaque: a[..., 3] = 255
    return a


def solid(w, h, color):
    return np.broadcast_to(np.array(color, np.uint8), (h, w, 4)).copy()


PHOTO = image_array('edit-photo.png')
SPRITE_IMG = image_array('edit-sprite.png')
DIALOG_FILTERS = None   # filled from the page


class FilterTests(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers -------------------------------------------------------------------------------------------------------
    def fresh(self, array):
        b = self.browser
        b.eval("""document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true;
            for (const k of Object.keys(localStorage)) if (k.startsWith('ed-filter')) localStorage.removeItem(k);
            Ed.setColor('fg', '#000000'); Ed.setColor('bg', '#ffffff'); true""")
        b.load_editor_image(array)
        b.eval('Ed.view.fit()')
        b.settle()
        return b

    def labels(self): return self.browser.eval('Ed.history.list().map(s => s.label)')

    def close(self, actual, expected, max_abs, mean_abs=None, mask=None, msg=''):
        """Compared premultiplied, the way the canvas stores the pixels."""
        self.assertPixelsClose(premultiplied(actual), premultiplied(expected), max_abs=max_abs, mean_abs=mean_abs, mask=mask, msg=msg)

    def menu(self, path):
        """Open a menu command with real clicks: the top menu, each submenu, the item."""
        b = self.browser
        parts = path.split('/')
        b.click_selector(f'#edMenubar [data-menu="{parts[0]}"]')
        for sub in parts[1:-1]:
            x, y = b.eval(f"""(() => {{ const e = [...document.querySelectorAll('.ed-menu .ed-menu-item')].find(e => e._sub && e.querySelector('.ed-menu-label').textContent === {json.dumps(sub)});
                const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
            b.click(x, y)
        b.click_selector(f'.ed-menu .ed-menu-item[data-path="{path}"]')

    def field_kind(self, D, key):
        return self.browser.eval(f"""(() => {{ const f = document.querySelector({json.dumps(f'{D} .ed-field[data-key="{key}"]')}); if (!f) return 'missing';
            return f.querySelector('input[type=number]') ? 'number' : f.querySelector('.segmented') ? 'seg' : f.querySelector('input[type=checkbox]') ? 'check' : '?'; }})()""")

    def set_field(self, D, key, v):
        """Change one dialog field the way a player does: type into the number box, click a segment, tick a box."""
        b = self.browser
        kind = self.field_kind(D, key)
        sel = f'{D} .ed-field[data-key="{key}"]'
        if kind == 'number':
            b.click_selector(f'{sel} input[type=number]')
            b.key('Ctrl+A')
            b.type_text(str(v))
            b.key('Tab')
        elif kind == 'seg':
            b.click_selector(f'{sel} .ed-seg:has(input[value="{v}"]) span')
        elif kind == 'check':
            if b.eval(f"document.querySelector({json.dumps(sel + ' input')}).checked") != bool(v): b.click_selector(f'{sel} input[type=checkbox]')
        else:
            self.fail(f'{key} is not a field of {D} ({kind})')

    def open_dialog(self, path, values=None):
        b = self.browser
        self.menu(path)
        fid = b.wait_for("document.querySelector('dialog.edf-dialog[open]')?.dataset.filter")
        D = f'dialog[data-filter="{fid}"]'
        for k, v in (values or {}).items(): self.set_field(D, k, v)
        return D

    def ok(self, D, timeout=60):
        b = self.browser
        n = b.eval('Ed.history.index')
        b.click_selector(f'{D} button.primary')
        b.wait_for(f'Ed.history.index > {n} && !Ed.filters.busy', timeout)
        b.settle()

    def apply(self, path, values=None, timeout=60):
        """Run a filter through the real menu (and its dialog, with the given values typed in) and return the picture."""
        b = self.browser
        n = b.eval('Ed.history.index')
        if path.endswith('...'): self.ok(self.open_dialog(path, values), timeout)
        else:
            self.menu(path)
            b.wait_for(f'Ed.history.index > {n} && !Ed.filters.busy', timeout)
            b.settle()
        return b.editor_pixels()

    def run_filter(self, fid, params=None, timeout=60):
        """Ed.filters.run without a dialog (for extra settings of a filter already driven through its dialog)."""
        b = self.browser
        self.assertTrue(b.eval(f'Ed.filters.run({json.dumps(fid)}, {json.dumps(params or {})})', timeout=timeout), fid)
        b.settle()
        return b.editor_pixels()

    def undo_to(self, array, n=1):
        b = self.browser
        for _ in range(n): b.key('Ctrl+Z')
        b.wait_for('Ed.history.index === 0')
        self.assertPixelsClose(b.editor_pixels(), array, msg='undo restores the exact pixels')

    # ----- the menu ---------------------------------------------------------------------------------------------------------
    def test_00_menu_has_exactly_the_verified_filters(self):
        b = self.fresh(PHOTO)
        paths = sorted(p for p in b.eval("Ed.menu.list().map(m => m.path)") if p.startswith('Filter/'))
        expected = sorted(['Filter/Last Filter',
            'Filter/Blur/Average', 'Filter/Blur/Blur', 'Filter/Blur/Blur More', 'Filter/Blur/Box Blur...', 'Filter/Blur/Gaussian Blur...', 'Filter/Blur/Motion Blur...',
            'Filter/Distort/Pinch...', 'Filter/Distort/Ripple...', 'Filter/Distort/Spherize...', 'Filter/Distort/Twirl...',
            'Filter/Noise/Add Noise...', 'Filter/Noise/Dust & Scratches...', 'Filter/Noise/Median...',
            'Filter/Pixelate/Mosaic...', 'Filter/Render/Clouds', 'Filter/Render/Difference Clouds',
            'Filter/Sharpen/Sharpen', 'Filter/Sharpen/Sharpen Edges', 'Filter/Sharpen/Sharpen More', 'Filter/Sharpen/Unsharp Mask...',
            'Filter/Stylize/Emboss...', 'Filter/Stylize/Find Edges', 'Filter/Stylize/Solarize',
            'Filter/Other/High Pass...', 'Filter/Other/Maximum...', 'Filter/Other/Minimum...', 'Filter/Other/Offset...'])
        self.assertEqual(paths, expected)
        self.assertIn('Edit/Fade...', b.eval("Ed.menu.list().map(m => m.path)"))
        # Photoshop's submenu order, Last Filter on top (disabled until a filter ran), shortcuts shown
        tree = b.eval("Ed.menu.tree().find(t => t.name === 'Filter').children.map(c => c.name)")
        self.assertEqual(tree, ['Last Filter', 'Blur', 'Distort', 'Noise', 'Pixelate', 'Render', 'Sharpen', 'Stylize', 'Other'])
        b.click_selector('#edMenubar [data-menu="Filter"]')
        self.assertEqual(b.eval("document.querySelector('.ed-menu-item[data-path=\"Filter/Last Filter\"]').disabled"), True)
        self.assertEqual(b.text('.ed-menu-item[data-path="Filter/Last Filter"] .ed-menu-key'), 'Alt+Ctrl+F')
        b.key('Escape')
        self.assertNoBrowserErrors()

    # ----- blurs ------------------------------------------------------------------------------------------------------------
    def test_01_gaussian_blur_typed_radius_is_sigma_on_premultiplied_colors_and_undo(self):
        b = self.fresh(PHOTO)
        out = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 2.5})
        self.assertEqual(self.labels(), ['Open', 'Gaussian Blur'])
        self.assertEqual(b.eval('Ed.filters.last'), {'id': 'gaussian-blur', 'params': {'radius': 2.5}})
        ref = to8(gaussian(premul(PHOTO), 2.5))
        self.close(out, ref, max_abs=2, msg='radius 2.5 = sigma 2.5, edges repeated')
        self.undo_to(PHOTO)
        # big radii use the box approximation: still within a few levels of the true Gaussian
        for r in (7, 24):
            out = self.run_filter('gaussian-blur', {'radius': r})
            self.close(out, to8(gaussian(premul(PHOTO), r)), max_abs=4, mean_abs=.6, msg=f'radius {r}')
            self.undo_to(PHOTO)
        self.assertNoBrowserErrors()

    def test_02_gaussian_blur_on_a_sprite_has_no_dark_fringe(self):
        b = self.fresh(SPRITE_IMG)
        out = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 3})
        ref = to8(gaussian(premul(SPRITE_IMG), 3))
        self.close(out, ref, max_abs=2, msg='premultiplied blur of a transparent sprite')
        # the soft edge of the blue square keeps the square's color (a straight-alpha blur would mix in the transparent black)
        y, x = np.mgrid[0:64, 0:64]
        cx, cy = SPRITE['disk']['center']
        far = (np.hypot(x + .5 - cx, y + .5 - cy) > 26) & (y < 44)
        a = out[..., 3]
        blue = np.array(SPRITE['flat_color'][:3])
        for lo, tol in ((64, 3), (16, 10)):
            m = far & (a >= lo) & (a < 255)
            self.assertGreater(m.sum(), 40)
            worst = np.abs(out[m][:, :3].astype(int) - blue).max()
            self.assertLessEqual(worst, tol, f'edge pixels with alpha >= {lo} keep the blue')
        self.assertEqual(int(a[0, 40]), 0, 'far transparent pixels stay transparent')
        self.assertNoBrowserErrors()

    def test_03_box_blur_with_a_dragged_slider_blur_and_blur_more(self):
        b = self.fresh(PHOTO)
        D = self.open_dialog('Filter/Blur/Box Blur...')
        sl = f'{D} .ed-field[data-key="radius"] input[type=range]'
        r = b.eval(f"document.querySelector({json.dumps(sl)}).getBoundingClientRect().toJSON()")
        y = r['y'] + r['height'] / 2
        thumb = r['x'] + r['width'] * float(b.eval(f"document.querySelector({json.dumps(sl)}).value")) / 1000
        b.drag([(thumb, y), (r['x'] + r['width'] * .15, y)])
        radius = float(b.eval(f"document.querySelector({json.dumps(D + ' input[name=radius]')}).value"))
        self.assertTrue(1 <= radius <= 8, f'the slider is stretched: 15% of it is a small radius ({radius})')
        self.assertEqual(radius, round(radius))
        self.ok(D)
        self.close(b.editor_pixels(), to8(box(premul(PHOTO), int(radius))), max_abs=1, msg=f'box radius {radius}')
        self.undo_to(PHOTO)
        # Blur and Blur More: binomial 3 and 7 tap kernels, run straight from the menu
        out = self.apply('Filter/Blur/Blur')
        self.close(out, to8(separable(premul(PHOTO), np.array([1, 2, 1]) / 4)), max_abs=1, msg='Blur')
        out = self.apply('Filter/Blur/Blur More')
        k7 = np.array([1, 6, 15, 20, 15, 6, 1]) / 64
        self.close(out, to8(separable(separable(premul(PHOTO), np.array([1, 2, 1]) / 4), k7)), max_abs=2, msg='Blur More after Blur')
        self.assertEqual(self.labels(), ['Open', 'Blur', 'Blur More'])
        self.undo_to(PHOTO, 2)
        self.assertNoBrowserErrors()

    def test_04_average_and_motion_blur(self):
        b = self.fresh(SPRITE_IMG)
        out = self.apply('Filter/Blur/Average')
        p = premul(SPRITE_IMG).reshape(-1, 4).mean(0)
        self.close(out, to8(np.broadcast_to(p, (64, 64, 4))), max_abs=1, msg='Average: premultiplied mean of the layer')
        # Motion Blur at 0 and 90 degrees: a box of exactly `distance` pixels (7 whole taps and two halves) along the row / column
        img = noise_image(96, 72, seed=3)
        for angle, axis in ((0, 1), (90, 0)):
            self.fresh(img)
            out = self.apply('Filter/Blur/Motion Blur...', {'angle': angle, 'distance': 8})
            ref = to8(conv1(premul(img), np.array([.5, 1, 1, 1, 1, 1, 1, 1, .5]) / 8, axis))
            self.close(out, ref, max_abs=1, msg=f'motion blur at {angle} degrees')
        # at 30 degrees: within a few levels of the mean along the tilted line (both axes step through pixels)
        self.fresh(PHOTO)
        out = self.apply('Filter/Blur/Motion Blur...', {'angle': 30, 'distance': 12})
        th = math.radians(30)
        vx, vy = math.cos(th), -math.sin(th)
        p = premul(PHOTO)
        h, w = 256, 256
        Y, X = np.mgrid[0:h, 0:w].astype(np.float64)
        acc = np.zeros_like(p)
        ts = np.linspace(-6, 6, 97)
        for t in ts: acc += np.stack([bilinear(p[..., c], X + t * vx, Y + t * vy) for c in range(4)], -1)
        ref = to8(acc / len(ts))
        self.close(out, ref, max_abs=20, mean_abs=.3, msg='motion blur at 30 degrees (hard aliased edges differ most)')
        fine = np.zeros((h, w), bool)
        fine[150:234, 150:234] = True   # PHOTO's checker and noise blocks, plus the blur's reach
        fine |= np.abs(np.hypot(X + .5 - 192, Y + .5 - 64) - 32) < 8   # the aliased black disk's rim
        self.close(out, ref, max_abs=6, mask=~fine, msg='motion blur at 30 degrees away from 1 px detail and aliased rims')
        self.assertNoBrowserErrors()

    # ----- sharpening -------------------------------------------------------------------------------------------------------
    def test_05_sharpen_sharpen_more_sharpen_edges(self):
        img = noise_image(80, 64, seed=11)
        img[..., :3] = (img[..., :3] // 3 + 80).astype(np.uint8)   # room above and below for the overshoot
        p = premul(img)
        b3 = separable(p, np.array([1, 2, 1]) / 4)
        for path, k in (('Filter/Sharpen/Sharpen', .6), ('Filter/Sharpen/Sharpen More', 1.8)):
            self.fresh(img)
            self.close(self.apply(path), to8(p + k * (p - b3)), max_abs=1, msg=path)
        self.fresh(img)
        out = self.apply('Filter/Sharpen/Sharpen Edges')
        d = p - gaussian(p, 1)
        ref = to8(np.where(np.abs(d) >= 10, p + 1.2 * d, p))
        ambiguous = (np.abs(np.abs(d) - 10) < .7).any(-1)
        self.close(out, ref, max_abs=1, mask=~ambiguous, msg='Sharpen Edges = Unsharp Mask 120%, 1 px, threshold 10')
        self.assertNoBrowserErrors()

    def test_06_unsharp_mask_amount_radius_threshold(self):
        img = PHOTO.copy()
        img[..., :3] = (img[..., :3].astype(int) * 3 // 5 + 50).astype(np.uint8)
        p = premul(img)
        for amount, radius, threshold in ((150, 1.5, 0), (80, 3, 12)):
            b = self.fresh(img)
            out = self.apply('Filter/Sharpen/Unsharp Mask...', {'amount': amount, 'radius': radius, 'threshold': threshold})
            d = p - gaussian(p, radius)
            ref = to8(np.where(np.abs(d) >= threshold, p + amount / 100 * d, p))
            ambiguous = (np.abs(np.abs(d) - threshold) < .7).any(-1) if threshold else np.zeros((256, 256), bool)
            self.close(out, ref, max_abs=3, mask=~ambiguous, msg=f'unsharp mask {amount}% {radius} px threshold {threshold}')
            if threshold: self.assertGreater(int((np.abs(d) < threshold - 1).all(-1).sum()), 1000, 'the threshold leaves smooth areas alone')
            self.assertEqual(self.labels(), ['Open', 'Unsharp Mask'])
        self.assertNoBrowserErrors()

    # ----- noise ------------------------------------------------------------------------------------------------------------
    def test_07_median_and_dust_and_scratches(self):
        img = noise_image(90, 70, seed=5)
        q = premul(img)
        b = self.fresh(img)
        out = self.apply('Filter/Noise/Median...', {'radius': 2})
        ref = np.median(window(np.round(q), 2), axis=0)
        self.assertPixelsClose(out, to8(ref), msg='median of the 5 x 5 square per channel, edges repeated')
        # Dust & Scratches: the median only where the pixel differs from it by more than the threshold
        b = self.fresh(img)
        out = self.apply('Filter/Noise/Dust & Scratches...', {'radius': 1, 'threshold': 60})
        m = np.median(window(np.round(q), 1), axis=0)
        ref = np.where(np.abs(q - m) > 60, m, q)
        self.assertPixelsClose(out, to8(ref), max_abs=1)
        self.assertNoBrowserErrors()

    def test_08_add_noise_statistics(self):
        gray = solid(200, 160, (128, 128, 128, 255))
        b = self.fresh(gray)
        out = self.apply('Filter/Noise/Add Noise...', {'amount': 25, 'distribution': 'uniform', 'mono': False}).astype(float)
        d = out[..., :3] - 128
        A = 32   # 25% of 128 levels
        self.assertLessEqual(np.abs(d).max(), A + .5, 'uniform noise stays within the amount')
        self.assertAlmostEqual(d.mean(), 0, delta=.4)
        self.assertAlmostEqual(d.std(), A / math.sqrt(3), delta=.6, msg='uniform: std = amount / sqrt(3)')
        self.assertLess(abs(np.corrcoef(d[..., 0].ravel(), d[..., 1].ravel())[0, 1]), .05, 'colored: channels independent')
        hist = np.histogram(d[..., 0], bins=8, range=(-A, A))[0]
        self.assertLess(hist.max() / hist.min(), 1.2, 'flat histogram')
        self.assertTrue((out[..., 3] == 255).all())
        # Gaussian and Monochromatic
        self.fresh(gray)
        out = self.apply('Filter/Noise/Add Noise...', {'amount': 25, 'distribution': 'gaussian', 'mono': True}).astype(float)
        d = out[..., :3] - 128
        self.assertTrue((d[..., 0] == d[..., 1]).all() and (d[..., 1] == d[..., 2]).all(), 'monochromatic: the same value in R, G and B')
        self.assertAlmostEqual(d.std(), .6 * A, delta=.6, msg='gaussian: std = 0.6 x amount')
        self.assertAlmostEqual((np.abs(d[..., 0]) <= .6 * A).mean(), .683, delta=.03, msg='68% within one sigma')
        # a new pattern each time; alpha of a sprite is kept
        self.assertEqual(b.eval('Ed.filters.last'), {'id': 'add-noise', 'params': {'amount': 25, 'distribution': 'gaussian', 'mono': True}})
        b.key('Ctrl+F')
        b.wait_for('Ed.history.list().length === 3')
        again = b.editor_pixels().astype(float)
        self.assertGreater(np.abs(again - out).mean(), 5, 'Last Filter adds new noise')
        self.fresh(SPRITE_IMG)
        out = self.apply('Filter/Noise/Add Noise...', {'amount': 10})
        self.assertPixelsClose(out[..., 3], SPRITE_IMG[..., 3], msg='alpha kept')
        self.assertNoBrowserErrors()

    # ----- pixelate, other ------------------------------------------------------------------------------------------------
    def test_09_mosaic_exact_cell_averages(self):
        img = noise_image(70, 50, seed=9, opaque=False)
        b = self.fresh(img)
        out = self.apply('Filter/Pixelate/Mosaic...', {'size': 16})
        p = premul(img)
        ref = np.zeros_like(p)
        for y0 in range(0, 50, 16):
            for x0 in range(0, 70, 16):
                ref[y0:y0 + 16, x0:x0 + 16] = p[y0:y0 + 16, x0:x0 + 16].reshape(-1, 4).mean(0)   # partial cells at the right and bottom
        self.close(out, to8(ref), max_abs=1, msg='cells from the top-left corner, premultiplied averages')
        self.assertNoBrowserErrors()

    def test_10_maximum_and_minimum_are_exact_morphology(self):
        img = noise_image(60, 48, seed=13)
        img[10:30, 20:26, 3] = 0   # a transparent hole: Maximum fills it, Minimum grows it
        p = premul(img)
        for path, fn in (('Filter/Other/Maximum...', np.max), ('Filter/Other/Minimum...', np.min)):
            for preserve, disk in (('squareness', False), ('roundness', True)):
                self.fresh(img)
                out = self.apply(path, {'radius': 3, 'preserve': preserve})
                self.close(out, to8(fn(window(p, 3, disk), axis=0)), max_abs=1, msg=f'{path} {preserve}')
        self.assertNoBrowserErrors()

    def test_11_high_pass(self):
        b = self.fresh(PHOTO)
        out = self.apply('Filter/Other/High Pass...', {'radius': 4})
        c = PHOTO[..., :3].astype(float)
        ref = color8(c - gaussian(c, 4) + 128, PHOTO[..., 3])
        self.assertPixelsClose(out, ref, max_abs=1)
        c2 = out[..., :3].astype(float)
        out = self.run_filter('high-pass', {'radius': 30})
        ref = color8(c2 - gaussian(c2, 30) + 128, PHOTO[..., 3])
        self.assertPixelsClose(out, ref, max_abs=3, msg='a big radius (box approximation)')
        self.assertNoBrowserErrors()

    def test_12_offset_wrap_repeat_transparent_and_in_a_selection(self):
        img = noise_image(64, 48, seed=17)
        b = self.fresh(img)
        D = self.open_dialog('Filter/Other/Offset...')
        self.assertEqual(b.eval(f"document.querySelector({json.dumps(D + ' input[name=horizontal]')}).value"), '32', 'half the width by default: shows the tile seams')
        self.set_field(D, 'horizontal', 20)
        self.set_field(D, 'vertical', -7)
        self.set_field(D, 'undefined', 'wrap')
        self.ok(D)
        self.assertPixelsClose(b.editor_pixels(), np.roll(img, (-7, 20), (0, 1)), msg='wrap around: exact')
        self.fresh(img)
        out = self.apply('Filter/Other/Offset...', {'horizontal': 5, 'vertical': 3, 'undefined': 'repeat'})
        ys, xs = np.clip(np.arange(48) - 3, 0, 47), np.clip(np.arange(64) - 5, 0, 63)
        self.assertPixelsClose(out, img[ys][:, xs], msg='repeat edge pixels')
        self.fresh(img)
        out = self.apply('Filter/Other/Offset...', {'horizontal': -6, 'vertical': 4, 'undefined': 'transparent'})
        ref = np.zeros_like(img)
        ref[4:, :58] = img[:44, 6:]
        self.assertPixelsClose(out, ref, msg='set to transparent')
        # in a selection the rectangle of the selection wraps
        self.fresh(img)
        b.eval('Ed.select.rect(10, 8, 30, 20)')
        out = self.apply('Filter/Other/Offset...', {'horizontal': 7, 'vertical': 5, 'undefined': 'wrap'})
        ref = img.copy()
        ref[8:28, 10:40] = np.roll(img[8:28, 10:40], (5, 7), (0, 1))
        self.assertPixelsClose(out, ref, msg='wrap inside the selection')
        self.assertNoBrowserErrors()

    # ----- stylize ----------------------------------------------------------------------------------------------------------
    def test_13_emboss_find_edges_solarize(self):
        img = PHOTO
        c = img[..., :3].astype(float)
        b = self.fresh(img)
        out = self.apply('Filter/Stylize/Emboss...', {'angle': 135, 'height': 3, 'amount': 150})
        th = math.radians(135)
        vx, vy = math.cos(th) * 1.5, -math.sin(th) * 1.5
        Y, X = np.mgrid[0:256, 0:256].astype(np.float64)
        ref = np.stack([128 + 1.5 * (bilinear(c[..., k], X - vx, Y - vy) - bilinear(c[..., k], X + vx, Y + vy)) for k in range(3)], -1)
        self.assertPixelsClose(out, color8(ref, img[..., 3]), max_abs=1, msg='emboss lit from the upper left')
        self.assertGreater(int(out[16, 26, 0]), 200, 'the top edge of the white square (16, 16) faces the light')
        self.fresh(img)
        out = self.apply('Filter/Stylize/Find Edges')
        gx = conv1(conv1(c, [-1, 0, 1], 1), [1, 2, 1], 0)
        gy = conv1(conv1(c, [1, 2, 1], 1), [-1, 0, 1], 0)
        self.assertPixelsClose(out, color8(255 - np.minimum(255, np.hypot(gx, gy) / 2), img[..., 3]), max_abs=1, msg='Sobel, edges repeated')
        self.fresh(img)
        out = self.apply('Filter/Stylize/Solarize')
        self.assertPixelsClose(out, color8(np.where(c > 127, 255 - c, c), img[..., 3]), msg='solarize: values above 127 inverted')
        self.assertNoBrowserErrors()

    # ----- distort ----------------------------------------------------------------------------------------------------------
    def test_14_twirl_pinch_spherize_ripple(self):
        img = PHOTO
        W = H = 256
        cx = cy = 128.0
        R = 128.0
        b = self.fresh(img)
        out = self.apply('Filter/Distort/Twirl...', {'angle': 120})

        def twirl(X, Y):
            dx, dy = X - cx, Y - cy
            r = np.hypot(dx, dy)
            a = np.where(r < R, -math.radians(120) * (1 - r / R) ** 2, 0)
            return cx + dx * np.cos(a) - dy * np.sin(a), cy + dx * np.sin(a) + dy * np.cos(a)
        self.close(out, remap(img, twirl), max_abs=2, msg='twirl')
        Y, X = np.mgrid[0:H, 0:W] + .5
        outside = np.hypot(X - cx, Y - cy) > R + 1
        self.assertPixelsClose(out, img, mask=outside, msg='twirl leaves the corners alone')

        for amount in (60, -60):
            self.fresh(img)
            out = self.apply('Filter/Distort/Pinch...', {'amount': amount})

            def pinch(X, Y):
                dx, dy = (X - cx) / R, (Y - cy) / R
                r = np.hypot(dx, dy)
                g = 1 + 2 * abs(amount) / 100 * (1 - r) ** 2
                f = np.where(r < 1, g if amount > 0 else 1 / g, 1)
                return cx + dx * f * R, cy + dy * f * R
            self.close(out, remap(img, pinch), max_abs=2, msg=f'pinch {amount}')

        for amount, mode in ((100, 'normal'), (-50, 'normal'), (70, 'horizontal')):
            self.fresh(img)
            out = self.apply('Filter/Distort/Spherize...', {'amount': amount, 'mode': mode})
            k = amount / 100

            def m(r): return r + (np.arcsin(np.clip(r, 0, 1)) * 2 / np.pi - r) * k if k >= 0 else r + (np.sin(r * np.pi / 2) - r) * -k

            def sph(X, Y):
                dx, dy = (X - cx) / R, (Y - cy) / R
                if mode == 'horizontal':
                    inside = (np.abs(dx) < 1) & (np.abs(dy) < 1)
                    return np.where(inside, cx + np.sign(dx) * m(np.abs(dx)) * R, X), Y
                r = np.hypot(dx, dy)
                f = np.where((r < 1) & (r > 1e-9), m(np.clip(r, 1e-9, .999999)) / np.clip(r, 1e-9, 1), 1)
                return cx + dx * f * R, cy + dy * f * R
            self.close(out, remap(img, sph), max_abs=2, msg=f'spherize {amount} {mode}')

        # direction checks on a small red square right of the centre
        dot = solid(128, 128, (255, 255, 255, 255))
        dot[60:68, 92:100] = (255, 0, 0, 255)

        def red_centre(a):
            m = (a[..., 1] < 128)
            yy, xx = np.nonzero(m)
            return xx.mean() + .5, yy.mean() + .5, m.sum()
        self.fresh(dot)
        _, y1, _ = red_centre(self.apply('Filter/Distort/Twirl...', {'angle': 90}))
        self.assertGreater(y1, 64 + 3, 'a positive twirl turns clockwise (to the right side moves down)')
        dot2 = solid(128, 128, (255, 255, 255, 255))
        dot2[56:72, 56:72] = (255, 0, 0, 255)
        self.fresh(dot2)
        _, _, n_sph = red_centre(self.apply('Filter/Distort/Spherize...', {'amount': 100, 'mode': 'normal'}))
        self.fresh(dot2)
        _, _, n_pinch = red_centre(self.apply('Filter/Distort/Pinch...', {'amount': 100}))
        self.assertGreater(n_sph, 256 * 1.5, 'spherize magnifies the middle')
        self.assertLess(n_pinch, 256 / 1.5, 'pinch shrinks the middle')

        # in a selection: centred on the selection's bounds, nothing changes outside
        self.fresh(img)
        b.eval('Ed.select.rect(40, 30, 120, 90)')
        out = self.apply('Filter/Distort/Twirl...', {'angle': 200})
        sub = img[30:120, 40:160]
        scx, scy, sR = 60.0, 45.0, 45.0

        def twirl_sub(X, Y):
            dx, dy = X - scx, Y - scy
            r = np.hypot(dx, dy)
            a = np.where(r < sR, -math.radians(200) * (1 - r / sR) ** 2, 0)
            return scx + dx * np.cos(a) - dy * np.sin(a), scy + dx * np.sin(a) + dy * np.cos(a)
        ref = img.copy()
        ref[30:120, 40:160] = remap(sub, twirl_sub)
        self.close(out, ref, max_abs=2, msg='twirl in a selection')

        self.fresh(img)
        out = self.apply('Filter/Distort/Ripple...', {'amount': 300, 'size': 'large'})
        L, A = 26, 4.4 * 3

        def wob(t): return np.sin(2 * np.pi * t / L) + .35 * np.sin(2 * np.pi * t / (L * 2.7))
        self.close(out, remap(img, lambda X, Y: (X + A * wob(Y), Y + A * wob(X))), max_abs=2, msg='ripple')
        self.assertNoBrowserErrors()

    # ----- render -----------------------------------------------------------------------------------------------------------
    def test_15_clouds_and_difference_clouds(self):
        img = noise_image(640, 400, seed=21)
        b = self.fresh(img)
        b.eval("Ed.setColor('fg', '#102030'); Ed.setColor('bg', '#f0e0c0')")
        out = self.apply('Filter/Render/Clouds')
        fg, bg = np.array([16, 32, 48.]), np.array([240, 224, 192.])
        t = (out[..., :3] - fg) / (bg - fg)
        self.assertTrue((out[..., 3] == 255).all(), 'opaque')
        self.assertLess(np.abs(t - t.mean(-1, keepdims=True)).max(), .012, 'every pixel lies between the foreground and background colors')
        t = t.mean(-1)
        self.assertGreater(t.std(), .08)
        self.assertGreater(t.max() - t.min(), .4)   # the menu picks a random seed: some clouds span a little less than half
        # seamless tiles: the wrap-around step is like any step inside
        inner = np.abs(np.diff(t, axis=1)).mean()
        self.assertLess(np.abs(t[:, 0] - t[:, -1]).mean(), 2.5 * inner)
        self.assertLess(np.abs(t[0] - t[-1]).mean(), 2.5 * np.abs(np.diff(t, axis=0)).mean())
        # the same seed gives the same clouds whatever the area or the band split; Difference Clouds = |picture - clouds|
        self.fresh(img)
        b.eval("Ed.setColor('fg', '#102030'); Ed.setColor('bg', '#f0e0c0')")
        full = self.run_filter('clouds', {'seed': 4242})
        self.fresh(img)
        b.eval("Ed.setColor('fg', '#102030'); Ed.setColor('bg', '#f0e0c0'); Ed.select.rect(100, 90, 300, 41)")
        part = self.run_filter('clouds', {'seed': 4242})
        self.assertPixelsClose(part[90:131, 100:400], full[90:131, 100:400], msg='a selection shows the same clouds')
        self.assertPixelsClose(part[:90], img[:90])
        self.fresh(img)
        b.eval("Ed.setColor('fg', '#102030'); Ed.setColor('bg', '#f0e0c0')")
        diff = self.run_filter('difference-clouds', {'seed': 4242})
        ref = np.abs(img[..., :3].astype(int) - full[..., :3].astype(int))
        self.assertPixelsClose(diff[..., :3], ref.astype(np.uint8), max_abs=1)
        self.assertEqual(self.labels(), ['Open', 'Difference Clouds'])
        # through the menu on a transparent sprite: fills it; Last Filter makes new clouds
        self.fresh(SPRITE_IMG)
        out = self.apply('Filter/Render/Difference Clouds')
        self.assertTrue((out[..., 3] == 255).all())
        b.key('Ctrl+F')
        b.wait_for('Ed.history.list().length === 3')
        self.assertGreater(np.abs(b.editor_pixels().astype(int) - out).mean(), 3)
        self.assertNoBrowserErrors()

    # ----- selection, locks, masks ------------------------------------------------------------------------------------------
    def test_20_soft_selection_lock_transparency_and_layer_mask(self):
        img = PHOTO
        b = self.fresh(img)
        # a feathered selection: alpha ramps from 0 to 255 across the middle
        b.eval("""(() => { const c = Ed.util.canvas(256, 256), g = c.getContext('2d'), gr = g.createLinearGradient(64, 0, 192, 0);
            gr.addColorStop(0, 'rgba(255,255,255,0)'); gr.addColorStop(1, 'rgba(255,255,255,1)'); g.fillStyle = gr; g.fillRect(40, 30, 200, 190); Ed.select.set(c); return true; })()""")
        sel = b.editor_pixels('selection')
        k = (sel[..., 3] if sel.ndim == 3 else sel).astype(float)[..., None] / 255
        out = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 3})
        p0, p1 = premul(img), gaussian(premul(img), 3)
        self.close(out, to8(p0 * (1 - k) + p1 * k), max_abs=3, msg='the result goes through the soft selection')
        self.assertPixelsClose(out[:30], img[:30], msg='outside the selection nothing changes')
        b.key('Ctrl+Z')
        b.wait_for('Ed.history.list().at(-1).undone')
        self.assertPixelsClose(b.editor_pixels(), img)
        # lock transparency: alpha stays exactly, colors are the premultiplied blur's
        self.fresh(SPRITE_IMG)
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}, {history: false}); true")
        out = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 2})
        self.assertPixelsClose(out[..., 3], SPRITE_IMG[..., 3], msg='lock transparency keeps alpha')
        g = gaussian(premul(SPRITE_IMG), 2)
        rgb = g[..., :3] * 255 / np.maximum(g[..., 3:4], 1e-9)
        ref = color8(rgb, SPRITE_IMG[..., 3])
        self.close(out, ref, max_abs=2, msg='colors of the premultiplied blur under the old alpha')
        b.eval("Ed.layers.set(0, {locks: {alpha: false}}, {history: false}); true")
        # a locked or hidden layer refuses with a message and no history
        for props, words in (("{locks: {all: true}}", 'locked'), ("{visible: false}", 'hidden')):
            self.fresh(SPRITE_IMG)
            b.eval(f"Ed.layers.set(0, {props}, {{history: false}}); true")
            self.menu('Filter/Blur/Gaussian Blur...')
            b.wait_for(f"(document.getElementById('toast')?.textContent || '').includes({json.dumps(words)})")
            self.assertFalse(b.eval("!!document.querySelector('dialog.edf-dialog')"), 'no dialog')
            self.assertEqual(self.labels(), ['Open'])
            self.assertFalse(b.eval('Ed.filters.busy'))
        # a layer mask: one gray channel, blurred with the edges repeated
        self.fresh(PHOTO)
        b.eval("Ed.select.rect(50, 60, 100, 80); Ed.layers.addMask(0, 'selection'); Ed.select.none(); Ed.setTarget('mask'); true")
        before_mask = b.editor_pixels('mask')[..., 3].astype(float)
        before_layer = b.editor_pixels(0)
        out = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 4})
        mask = b.editor_pixels('mask')[..., 3]
        self.assertPixelsClose(mask, np.clip(np.round(gaussian(before_mask, 4)), 0, 255).astype(np.uint8), max_abs=1, msg='the mask is blurred')
        self.assertPixelsClose(b.editor_pixels(0), before_layer, msg='the layer pixels stay')
        self.assertNoBrowserErrors()

    # ----- the dialog: preview box, canvas preview, cancel -----------------------------------------------------------------
    def test_21_preview_box_zoom_pan_original_and_canvas_preview(self):
        img = PHOTO
        b = self.fresh(img)
        b.eval("localStorage.setItem('ed-filter-preview', 'true')")
        D = self.open_dialog('Filter/Blur/Gaussian Blur...', {'radius': 4})
        V = f'{D} .edf-view'
        shown = lambda: b.eval(f"document.querySelector({json.dumps(V)}).dataset.shown || ''")
        b.wait_for(f"document.querySelector({json.dumps(V)}).dataset.shown === JSON.stringify({{radius: 4}})")
        ref = to8(gaussian(premul(img), 4))
        # the canvas shows the result before OK (Preview is on) and the history is untouched
        b.wait_for_pixels(lambda a: np.abs(a.astype(int) - ref).max() <= 2, timeout=10)
        self.assertEqual(self.labels(), ['Open'])
        # zoom in twice with the + button, then compare the box with the result at pixel centres
        self.assertEqual(b.text(f'{D} .edf-zoom-value'), '100%', 'the box starts at 100% like Photoshop')
        b.click_selector(f'{D} .edf-zoom-in')
        b.click_selector(f'{D} .edf-zoom-in')
        self.assertEqual(b.text(f'{D} .edf-zoom-value'), '300%')
        b.wait_for(f"document.querySelector({json.dumps(V)}).dataset.zoom === '3' && !document.querySelector({json.dumps(V)}).classList.contains('working')")
        b.settle(3)

        def box_matches(expected):
            rx, ry, rw, rh, vx, vy = map(float, b.eval(f"document.querySelector({json.dumps(V)}).dataset.region").split(','))
            z = float(b.eval(f"document.querySelector({json.dumps(V)}).dataset.zoom"))
            cv = b.pixels(f"document.querySelector({json.dumps(D + ' .edf-canvas')})")
            xs = np.arange(int(rx) + 2, int(rx + rw) - 2)
            ys = np.arange(int(ry) + 2, int(ry + rh) - 2)
            px = ((xs + .5 - vx) * z).astype(int)
            py = ((ys + .5 - vy) * z).astype(int)
            okx, oky = (px >= 0) & (px < cv.shape[1]), (py >= 0) & (py < cv.shape[0])
            got = cv[py[oky]][:, px[okx]]
            want = expected[ys[oky]][:, xs[okx]]
            return got, want
        got, want = box_matches(ref)
        self.assertGreater(got.shape[0] * got.shape[1], 1000)
        self.close(got, want, max_abs=3, msg='the preview box shows the result')
        # hold the mouse down in the box: the original; drag: pan
        region0 = b.eval(f"document.querySelector({json.dumps(V)}).dataset.region")
        x, y = b.center_of(V)
        b.mouse_move(x - 60, y)
        b.mouse_down(x - 60, y)
        b.wait_for(f"!document.querySelector({json.dumps(V)}).classList.contains('working')")
        b.settle(3)
        got, want = box_matches(img)
        self.close(got, want, max_abs=0, msg='holding the mouse shows the original')
        for k in range(1, 6): b.mouse_move(x - 60 + 12 * k, y + 8 * k)
        b.mouse_up(x, y + 40)
        self.assertNotEqual(b.eval(f"document.querySelector({json.dumps(V)}).dataset.region"), region0, 'dragging pans the box')
        b.wait_for(f"!document.querySelector({json.dumps(V)}).classList.contains('working')")
        b.settle(3)
        got, want = box_matches(ref)
        self.close(got, want, max_abs=3, msg='released: the result again')
        # Ctrl+- zooms out (the editor's own zoom does not react)
        view_zoom = b.eval('Ed.view.scale')
        b.click_selector(f'{D} input[name=radius]')
        b.key('Ctrl+-')
        self.assertEqual(b.text(f'{D} .edf-zoom-value'), '200%')
        self.assertEqual(b.eval('Ed.view.scale'), view_zoom)
        # Cancel: the picture comes back, no history
        b.click_selector(f'{D} button:not(.primary):not(.quiet):not(.edf-zoom-in):not(.edf-zoom-out)')
        b.wait_for("!document.querySelector('dialog.edf-dialog')")
        b.settle(3)
        self.assertPixelsClose(b.editor_pixels(), img, msg='Cancel restores the picture')
        self.assertEqual(self.labels(), ['Open'])
        self.assertFalse(b.eval('Ed.filters.busy'))
        # Escape cancels too; Reset restores the defaults
        D = self.open_dialog('Filter/Blur/Gaussian Blur...', {'radius': 9})
        b.click_selector(f'{D} button.quiet')
        self.assertEqual(b.eval(f"document.querySelector({json.dumps(D + ' input[name=radius]')}).value"), '1')
        b.key('Escape')
        b.wait_for("!document.querySelector('dialog.edf-dialog')")
        self.assertEqual(self.labels(), ['Open'])
        # the dialog remembers the last values used with OK
        D = self.open_dialog('Filter/Blur/Gaussian Blur...', {'radius': 6.5})
        self.ok(D)
        D = self.open_dialog('Filter/Blur/Gaussian Blur...')
        self.assertEqual(b.eval(f"document.querySelector({json.dumps(D + ' input[name=radius]')}).value"), '6.5')
        # with Preview off the canvas keeps the picture until OK
        b.click_selector(f'{D} input[name=preview]')
        self.set_field(D, 'radius', 2)
        b.settle(10)
        self.assertPixelsClose(b.editor_pixels(), to8(gaussian(premul(img), 6.5)), max_abs=3, msg='Preview off: the canvas shows the last state')
        b.key('Escape')
        b.wait_for("!document.querySelector('dialog.edf-dialog')")
        b.eval("localStorage.setItem('ed-filter-preview', 'true')")
        self.assertNoBrowserErrors()

    # ----- Last Filter, Fade -----------------------------------------------------------------------------------------------
    def test_22_last_filter_repeats_with_ctrl_f_and_alt_ctrl_f(self):
        img = PHOTO
        b = self.fresh(img)
        self.apply('Filter/Blur/Gaussian Blur...', {'radius': 1.5})
        b.click_selector('#edMenubar [data-menu="Filter"]')
        self.assertEqual(b.text('.ed-menu-item[data-path="Filter/Last Filter"] .ed-menu-label'), 'Last Filter (Gaussian Blur)')
        b.key('Escape')
        b.key('Ctrl+F')
        b.wait_for('Ed.history.list().length === 3')
        b.key('Alt+Ctrl+F')
        b.wait_for('Ed.history.list().length === 4')
        b.settle()
        self.assertEqual(self.labels(), ['Open', 'Gaussian Blur', 'Gaussian Blur', 'Gaussian Blur'])
        p = premul(img)
        for _ in range(3): p = gaussian(p, 1.5)
        self.close(b.editor_pixels(), to8(p), max_abs=3, msg='three times the same blur')
        # it stays for the next picture (apply the same filter to several sprites)
        self.fresh(SPRITE_IMG)
        b.key('Ctrl+F')
        b.wait_for('Ed.history.index === 1')
        b.settle()
        self.close(b.editor_pixels(), to8(gaussian(premul(SPRITE_IMG), 1.5)), max_abs=2, msg='Last Filter on another picture')
        self.assertNoBrowserErrors()

    def test_23_fade_opacity_and_mode(self):
        img = PHOTO
        b = self.fresh(img)
        out = self.apply('Filter/Stylize/Solarize')
        b.key('Shift+Ctrl+F')
        b.wait_for("!!document.querySelector('dialog[data-ed-dialog=\"Fade\"]')")
        D = 'dialog[data-ed-dialog="Fade"]'
        b.click_selector(f'{D} input[name=opacity][type=number]')
        b.key('Ctrl+A')
        b.type_text('40')
        b.key('Tab')
        b.click_selector(f'{D} button.primary')
        b.wait_for('Ed.history.list().length === 3')
        b.settle()
        self.assertEqual(self.labels(), ['Open', 'Solarize', 'Fade Solarize'])
        ref = np.round(img.astype(float) * .6 + out.astype(float) * .4)
        self.assertPixelsClose(b.editor_pixels(), ref.astype(np.uint8), max_abs=1, msg='Fade 40%: a mix of before and after')
        self.assertFalse(b.eval("Ed.menu.list().find(m => m.path === 'Edit/Fade...').enabled"), 'Fade once')
        b.key('Ctrl+Z')
        b.wait_for('Ed.history.index === 1')
        self.assertPixelsClose(b.editor_pixels(), out)
        # with a blend mode (and on a transparent sprite: alpha mixes too)
        self.fresh(SPRITE_IMG)
        after = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 2})
        b.click_selector('#edMenubar [data-menu="Edit"]')
        self.assertEqual(b.text('.ed-menu-item[data-path="Edit/Fade..."] .ed-menu-label'), 'Fade Gaussian Blur...')
        b.key('Escape')
        self.assertTrue(b.eval("Ed.menu.run('Edit/Fade...', {opacity: 50, mode: 'normal'})"))
        b.settle()
        ref = (premul(SPRITE_IMG) + premul(after)) / 2
        self.close(b.editor_pixels(), to8(ref), max_abs=2, msg='Fade 50% Normal on transparency: premultiplied mix')
        self.fresh(img)
        after = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 2})
        self.assertTrue(b.eval("Ed.menu.run('Edit/Fade...', {opacity: 100, mode: 'multiply'})"))
        b.settle()
        self.assertPixelsClose(b.editor_pixels()[..., :3], np.round(img[..., :3].astype(float) * after[..., :3] / 255).astype(np.uint8), max_abs=1, msg='Fade 100% Multiply')
        # anything else in between makes Fade unavailable
        self.fresh(img)
        self.apply('Filter/Blur/Blur')
        b.eval("Ed.select.rect(0, 0, 10, 10)")
        self.assertFalse(b.eval("Ed.menu.list().find(m => m.path === 'Edit/Fade...').enabled"))
        self.assertNoBrowserErrors()

    # ----- workers ----------------------------------------------------------------------------------------------------------
    def test_24_workers_under_the_page_csp_and_the_in_page_fallback(self):
        img = noise_image(640, 400, seed=23)
        b = self.fresh(img)
        csp = b.eval("fetch(location.href).then(r => r.headers.get('Content-Security-Policy'))")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn('worker-src', csp)
        ref = to8(gaussian(premul(img), 2))
        out = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 2})
        self.close(out, ref, max_abs=2)
        info, timing = b.eval('Ed.filters.workers()'), b.eval('Ed.filters.timing')
        self.assertFalse(info['broken'])
        self.assertGreaterEqual(info['alive'], 1)
        self.assertEqual(timing['workers'], timing['bands'])
        self.assertGreaterEqual(timing['workers'], min(4, info['size']), 'a big area is split over the workers')
        try:
            for stub in ("window.Worker = function () { throw new DOMException('blocked by the page', 'SecurityError'); }",
                         "window.Worker = class { constructor () { setTimeout(() => this.onerror && this.onerror({message: 'blocked', preventDefault () {}}), 5); } postMessage () {} terminate () {} }"):
                self.fresh(img)
                b.eval(f"window.__RealWorker = window.__RealWorker || window.Worker; Ed.filters.resetWorkers(); {stub}; true")
                out = self.apply('Filter/Blur/Gaussian Blur...', {'radius': 2})
                self.close(out, ref, max_abs=2, msg='the same result from the in-page fallback')
                self.assertTrue(b.eval('Ed.filters.workers().broken'))
                self.assertEqual(b.eval('Ed.filters.timing.workers'), 0)
                self.assertEqual(self.labels(), ['Open', 'Gaussian Blur'])
                self.undo_to(img)
        finally:
            b.eval("if (window.__RealWorker) window.Worker = window.__RealWorker; Ed.filters.resetWorkers(); true")
        self.fresh(img)
        self.run_filter('gaussian-blur', {'radius': 2})
        self.assertFalse(b.eval('Ed.filters.workers().broken'))
        self.assertNoBrowserErrors()

    def test_25_big_sheet_gaussian_blur_with_progress(self):
        b = self.browser
        b.select_asset('edit-big.png')
        b.eval("for (const k of Object.keys(localStorage)) if (k.startsWith('ed-filter')) localStorage.removeItem(k); true")
        big = b.editor_pixels()
        n = len(self.labels())
        D = self.open_dialog('Filter/Blur/Gaussian Blur...')
        b.click_selector(f'{D} input[name=preview]')   # Preview off: OK computes the whole sheet
        self.set_field(D, 'radius', 25)
        b.eval(f"""window.__bar = 0; window.__barObs = new MutationObserver(() => {{ const i = document.querySelector({json.dumps(D + ' .edf-view-bar.on i')}); if (i) window.__bar = Math.max(window.__bar, parseFloat(i.style.width) || .01); }});
            window.__barObs.observe(document.querySelector({json.dumps(D)}), {{subtree: true, attributes: true}}); true""")
        t0 = time.monotonic()
        b.click_selector(f'{D} button.primary')
        b.wait_for(f'Ed.history.list().length > {n} && !Ed.filters.busy', 30)
        took = time.monotonic() - t0
        b.eval('window.__barObs.disconnect(); true')
        self.assertLess(took, 8, f'Gaussian Blur 25 px on 2048 x 2048 took {took:.1f} s')
        self.assertGreater(b.eval('window.__bar'), 0, 'the progress bar showed')
        out = b.editor_pixels()
        c = big[:420, :420]
        ref = to8(gaussian(premul(c), 25))
        self.close(out[:300, :300], ref[:300, :300], max_abs=3, msg='the top-left corner (edges repeated)')
        # a slow filter run without its dialog shows a progress dialog that cancels
        # Cancel in the progress event's turn. Several DevTools mouse round-trips can let a fast CI worker
        # finish first, turning this into a machine-speed test instead of testing the Cancel handler.
        b.eval("""window.__busy = null; window.__cancelAt = null; window.__busyObs = new MutationObserver(() => {
            const d = document.querySelector('dialog.edf-busy'); if (!d) return; window.__busy = d.dataset.edDialog;
            const v = Number(d.querySelector('[role=progressbar]').getAttribute('aria-valuenow'));
            if (v > 0 && v < 100 && window.__cancelAt === null) { window.__cancelAt = v; d.querySelector('button').click(); }
        }); window.__busyObs.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-valuenow']}); true""")
        b.eval("Ed.menu.run('Filter/Noise/Median...', {radius: 60}); true")
        b.wait_for("window.__cancelAt !== null && !Ed.filters.busy", 20)
        self.assertEqual(b.eval('window.__busy'), 'Median')
        b.wait_for('!Ed.filters.busy && !document.querySelector("dialog.edf-busy")', 10)
        b.eval('window.__busyObs.disconnect(); true')
        self.assertEqual(len(self.labels()), n + 1, 'canceled: nothing applied')
        self.assertPixelsClose(b.editor_pixels(), out)
        self.assertNoBrowserErrors()

    # ----- layout -----------------------------------------------------------------------------------------------------------
    def test_30_every_dialog_fits_1366x768_and_1920x1080(self):
        b = self.fresh(PHOTO)
        paths = [p for p in b.eval("Ed.filters.list().filter(f => f.dialog).map(f => f.path)")]
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h)
                b.settle(3)
                for path in paths + ['Edit/Fade...']:
                    if path == 'Edit/Fade...':
                        self.run_filter('blur')
                        b.eval("Ed.menu.run('Edit/Fade...'); true")
                        sel = 'dialog[data-ed-dialog="Fade"]'
                    else:
                        b.eval(f"Ed.menu.run({json.dumps(path)}); true")
                        sel = 'dialog.edf-dialog'
                    b.wait_for(f"!!document.querySelector({json.dumps(sel)})")
                    if sel == 'dialog.edf-dialog':
                        b.wait_for("document.querySelector('dialog.edf-dialog .edf-view').dataset.shown")
                    b.settle(3)
                    box = b.eval(f"document.querySelector({json.dumps(sel)}).getBoundingClientRect().toJSON()")
                    name = path.split('/')[-1].replace('...', '').replace(' ', '_').replace('&', 'and')
                    self.assertTrue(box['left'] >= 0 and box['top'] >= 0 and box['right'] <= w and box['bottom'] <= h, f'{path} fits {w}x{h}: {box}')
                    spill = b.eval(f"""(() => {{ const d = document.querySelector({json.dumps(sel)}), r = d.getBoundingClientRect();
                        return [...d.querySelectorAll('*')].filter(e => {{ if (!e.getClientRects().length) return false; const q = e.getBoundingClientRect();
                            return q.width > 0 && (q.right > r.right + 1 || q.left < r.left - 1 || q.bottom > r.bottom + 1 || q.top < r.top - 1); }}).map(e => e.className || e.tagName); }})()""")
                    self.assertEqual(spill, [], f'{path}: nothing spills out of the dialog at {w}x{h}')
                    scroll = b.eval(f"(() => {{ const f = document.querySelector({json.dumps(sel + ' .ed-dialog-fields')}); return f ? f.scrollHeight - f.clientHeight : 0; }})()")
                    self.assertLessEqual(scroll, 1, f'{path}: every field visible without scrolling at {w}x{h}')
                    cut = b.eval(f"[...document.querySelectorAll({json.dumps(sel + ' label, ' + sel + ' button, ' + sel + ' .ed-seg span')})].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => e.textContent)")
                    self.assertEqual(cut, [], f'{path}: no cut-off text at {w}x{h}')
                    if w == 1366: b.screenshot(f'filters-{name}-{w}x{h}.png')
                    b.key('Escape')
                    b.wait_for(f"!document.querySelector({json.dumps(sel)})")
                    if path == 'Edit/Fade...': b.eval('Ed.history.undo(); true')
        finally:
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()
