"""The Color / History module (src/web/editor/color.js): the Color Picker dialog, the Color panel with its swatches, the
Eyedropper's Sample Size and Sample, and the History panel with snapshots and the History Brush source. Driven with real
mouse and keyboard input in headless Edge; colors are checked against independent numpy / colorsys references (sRGB -> Lab
D50 built from the sRGB primaries with a Bradford adaptation, alpha-weighted averages of the pixels the editor stores) and
history against the pixels captured at each step.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_color*.py" -v
"""
import colorsys
import json
import math
import numpy as np
from harness import EditorTestCase, ALT, CTRL, distance_to_path
from fakegame import TINY_PALETTE


# ----- independent references -----------------------------------------------------------------------------------------------
def rnd(v):
    return int(math.floor(v + 0.5))


def _xyz(x, y):
    return np.array([x / y, 1.0, (1 - x - y) / y])


def _srgb_to_xyz_d50():
    """sRGB -> XYZ (D50) from the sRGB primaries and D65 white, adapted to D50 with the Bradford transform (as ICC v4)."""
    P = np.stack([_xyz(.64, .33), _xyz(.30, .60), _xyz(.15, .06)], axis=1)
    w65 = _xyz(.3127, .3290)
    m65 = P * np.linalg.solve(P, w65)
    BF = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    w50 = np.array([0.9642, 1.0, 0.8249])
    adapt = np.linalg.inv(BF) @ np.diag((BF @ w50) / (BF @ w65)) @ BF
    return adapt @ m65, w50


M50, W50 = _srgb_to_xyz_d50()


def srgb_to_lab(rgb):
    c = np.asarray(rgb, np.float64) / 255
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    t = (M50 @ lin) / W50
    e, k = 216 / 24389, 24389 / 27
    f = np.where(t > e, np.cbrt(t), (k * t + 16) / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def lab_to_srgb(lab):
    L, a, b = lab
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    e, k = 216 / 24389, 24389 / 27
    inv = lambda t: t ** 3 if t ** 3 > e else (116 * t - 16) / k
    xyz = np.array([inv(fx), fy ** 3 if L > k * e else L / k, inv(fz)]) * W50
    lin = np.clip(np.linalg.solve(M50, xyz), 0, 1)
    c = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.clip(c * 255, 0, 255)


def hsb_to_rgb(h, s, v):
    return [c * 255 for c in colorsys.hsv_to_rgb((h % 360) / 360, s / 100, v / 100)]


def rgb_to_hsb(rgb):
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return h * 360, s * 100, v * 100


def hexs(rgb):
    return '#' + ''.join(f'{min(255, max(0, rnd(c))):02x}' for c in rgb)


def rgb_of(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def weighted_average(px, x, y, n):
    """Photoshop-style n x n average around pixel (x, y), clipped to the picture, weighted by alpha; None when transparent."""
    h = (n - 1) // 2
    H, W = px.shape[:2]
    a = px[max(0, y - h):min(H, y - h + n), max(0, x - h):min(W, x - h + n)].reshape(-1, 4).astype(np.float64)
    s = a[:, 3].sum()
    if s == 0: return None
    return hexs((a[:, :3] * a[:, 3:4]).sum(0) / s)


class ColorTests(EditorTestCase):

    # ----- helpers ------------------------------------------------------------------------------------------------------
    def setUp(self):
        super().setUp()
        self.browser.eval("Ed.test.errors.length = 0; document.querySelectorAll('dialog[open]').forEach(d => d.close()); true")

    def tearDown(self):
        if self.browser:
            errs = self.browser.eval("Ed.test.errors.slice()")
            if errs: self.fail(f'editor errors: {errs}')
        super().tearDown()

    def fresh(self, name='edit-sprite.png'):
        b = self.browser
        b.set_viewport(*self.viewport)
        b.select_asset(name)
        b.eval("Ed.view.fit(); true")
        b.settle(3)
        return b

    def picker_values(self):
        return self.browser.eval("Object.fromEntries([...document.querySelectorAll('.ed-picker input')].map(i => [i.name, i.value]))")

    def field(self, name, text):
        """Select a picker field's text with the mouse and keyboard and type a new value."""
        b = self.browser
        b.click_selector(f'.ed-picker input[name="{name}"]')
        b.key('Ctrl+A')
        b.type_text(text)
        b.settle(1)

    def open_fg_picker(self):
        b = self.browser
        b.click_selector('#edTools .ed-chip.ed-fg')
        b.wait_for("!!document.querySelector('dialog.ed-picker[open]')")
        b.settle(2)

    def fill(self, x, y, w, h, color):
        self.browser.eval(f"Ed.applyToTarget('Fill', g => {{ g.fillStyle = '{color}'; g.fillRect({x}, {y}, {w}, {h}); }})")
        self.browser.settle(2)

    def show_panel(self, pid):
        b = self.browser
        b.click_selector(f'#edDock .ed-tab[data-panel="{pid}"]')
        b.settle(3)

    # ----- color math -----------------------------------------------------------------------------------------------------
    def test_01_hsb_and_lab_math_match_independent_references(self):
        b = self.fresh()
        rng = np.random.default_rng(1)
        colors = [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255], [0, 0, 0], [128, 128, 128], [1, 2, 3], [254, 1, 128]] + rng.integers(0, 256, (300, 3)).tolist()
        out = b.eval(f"{json.dumps(colors)}.map(c => [Ed.colorMath.rgb2hsb(c), Ed.colorMath.rgb2lab(c)])")
        for c, (hsb, lab) in zip(colors, out):
            ref_h = rgb_to_hsb(c)
            if ref_h[1] > 0: self.assertAlmostEqual((hsb[0] - ref_h[0] + 180) % 360 - 180, 0, delta=1e-6, msg=f'H of {c}')
            self.assertAlmostEqual(hsb[1], ref_h[1], delta=1e-6, msg=f'S of {c}')
            self.assertAlmostEqual(hsb[2], ref_h[2], delta=1e-6, msg=f'B of {c}')
            ref = srgb_to_lab(c)
            self.assertTrue(np.allclose(lab, ref, atol=0.06), f'Lab of {c}: {lab} vs {ref}')
        # Photoshop shows pure sRGB red as L 54 a 81 b 70, white as 100 0 0, black as 0 0 0.
        self.assertEqual([rnd(v) for v in out[0][1]], [54, 81, 70])
        self.assertEqual([rnd(v) for v in out[3][1]], [100, 0, 0])
        self.assertEqual([rnd(v) for v in out[4][1]], [0, 0, 0])
        labs = [[50, 20, -30], [70, -40, 50], [20, 5, 5], [90, 0, 0], [60, 90, -100]]   # the last one is outside sRGB
        back = b.eval(f"{json.dumps(labs)}.map(l => Ed.colorMath.lab2rgb(l))")
        for l, rgb in zip(labs, back):
            self.assertTrue(np.allclose(rgb, lab_to_srgb(l), atol=0.15), f'RGB of Lab {l}: {rgb} vs {lab_to_srgb(l)}')
        hs = [[0, 100, 100], [37.5, 40, 80], [200, 70, 30], [359, 5, 99], [120, 0, 50]]
        back = b.eval(f"{json.dumps(hs)}.map(h => Ed.colorMath.hsb2rgb(h))")
        for h, rgb in zip(hs, back): self.assertTrue(np.allclose(rgb, hsb_to_rgb(*h), atol=1e-6), f'RGB of HSB {h}')

    # ----- Color Picker ----------------------------------------------------------------------------------------------------
    def test_10_picker_from_the_chip_shows_every_field_and_the_square(self):
        b = self.fresh()
        b.eval("Ed.setColor('fg', '#3a7bd5'); Ed.setColor('bg', '#ffffff'); true")
        self.open_fg_picker()
        self.assertEqual(b.eval("document.querySelector('dialog.ed-picker').dataset.edDialog"), 'Color Picker (Foreground Color)')
        v = self.picker_values()
        c = rgb_of('#3a7bd5')
        h, s, br = rgb_to_hsb(c)
        L, A, B = srgb_to_lab(c)
        self.assertEqual([int(v[k]) for k in 'rgb'], c)
        self.assertEqual([int(v['h']), int(v['s']), int(v['v'])], [rnd(h), rnd(s), rnd(br)])
        for k, ref in (('L', L), ('la', A), ('lb', B)): self.assertLessEqual(abs(int(v[k]) - ref), 0.5 + 0.06, f'{k} {v[k]} vs {ref}')
        self.assertEqual(v['hex'], '3a7bd5')
        # The square is S (left to right) by B (top to bottom) at the color's hue; the strip is the hue, 360 at the top.
        sq = b.pixels("document.querySelector('.ed-pk-square canvas')")
        n = sq.shape[0]
        i = (np.arange(n) + .5) / n
        S, V = np.meshgrid(i, 1 - i)
        hh = np.full_like(S, h / 360)
        ref = np.stack(np.vectorize(colorsys.hsv_to_rgb)(hh, S, V), -1) * 255
        self.assertLessEqual(np.abs(sq[..., :3].astype(np.float64) - ref).max(), 1.0)
        strip = b.pixels("document.querySelector('.ed-pk-strip canvas')")[:, 0, :3].astype(np.float64)
        ref = np.array([colorsys.hsv_to_rgb((1 - t) % 1.0, 1, 1) for t in i]) * 255
        self.assertLessEqual(np.abs(strip - ref).max(), 1.0)
        # A click in the square sets S and B from where it lands, keeping the hue.
        r = b.eval("(() => { const r = document.querySelector('.ed-pk-square').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")
        cx, cy = r[0] + r[2] * 0.3 + 0.25, r[1] + r[3] * 0.6 + 0.25
        b.click(cx, cy)
        b.settle(2)
        u, w = (cx - r[0]) / r[2], (cy - r[1]) / r[3]
        want = hexs(hsb_to_rgb(h, u * 100, (1 - w) * 100))
        v = self.picker_values()
        self.assertEqual('#' + v['hex'], want)
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('.ed-pk-new')).backgroundColor"), 'rgb(%d, %d, %d)' % tuple(rgb_of(want)))
        self.assertEqual(b.eval("Ed.fg"), '#3a7bd5', 'the foreground only changes on OK')
        # A drag on the strip changes the hue, keeping S and B.
        s0, b0 = int(v['s']), int(v['v'])
        st = b.eval("(() => { const r = document.querySelector('.ed-pk-strip').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")
        y0, y1 = st[1] + 5, st[1] + st[3] * 0.75 + 0.25
        b.drag([(st[0] + st[2] / 2, y0), (st[0] + st[2] / 2, y1)])
        b.settle(2)
        hue = (1 - (y1 - st[1]) / st[3]) * 360
        v = self.picker_values()
        self.assertEqual(int(v['h']), rnd(hue) % 360)
        self.assertEqual('#' + v['hex'], hexs(hsb_to_rgb(hue, u * 100, (1 - w) * 100)))
        self.assertLessEqual(abs(int(v['s']) - s0), 1)
        self.assertLessEqual(abs(int(v['v']) - b0), 1)
        # Typed values: R G B, then H S B, then Lab, then hex; every other field follows.
        self.field('r', '200'); self.field('g', '30'); self.field('b', '90')
        v = self.picker_values()
        self.assertEqual(v['hex'], 'c81e5a')
        h, s, br = rgb_to_hsb([200, 30, 90])
        self.assertEqual([int(v['h']), int(v['s']), int(v['v'])], [rnd(h), rnd(s), rnd(br)])
        self.field('h', '120'); self.field('s', '50'); self.field('v', '40')
        self.assertEqual('#' + self.picker_values()['hex'], hexs(hsb_to_rgb(120, 50, 40)))
        self.field('L', '50'); self.field('la', '20'); self.field('lb', '-30')
        v = self.picker_values()
        self.assertEqual([v['L'], v['la'], v['lb']], ['50', '20', '-30'], 'typed Lab values stay as typed')
        ref = lab_to_srgb([50, 20, -30])
        self.assertTrue(all(abs(int(v[k]) - ref[i]) <= 0.51 for i, k in enumerate('rgb')), f'{v} vs {ref}')
        self.field('hex', '#12ab9c')
        v = self.picker_values()
        self.assertEqual([int(v[k]) for k in 'rgb'], [0x12, 0xab, 0x9c])
        # The current color goes back with a click; Escape cancels, Enter (OK) applies.
        b.click_selector('.ed-pk-cur')
        b.settle(1)
        self.assertEqual(self.picker_values()['hex'], '3a7bd5')
        self.field('hex', 'ff8000')
        b.key('Escape')
        b.wait_for("!document.querySelector('dialog.ed-picker')")
        self.assertEqual(b.eval("Ed.fg"), '#3a7bd5')
        self.open_fg_picker()
        self.field('hex', 'ff8000')
        b.key('Enter')
        b.wait_for("!document.querySelector('dialog.ed-picker')")
        self.assertEqual(b.eval("Ed.fg"), '#ff8000')
        b.click_selector('#edTools .ed-chip.ed-bg')
        b.wait_for("!!document.querySelector('dialog.ed-picker[open]')")
        self.assertEqual(b.eval("document.querySelector('dialog.ed-picker').dataset.edDialog"), 'Color Picker (Background Color)')
        self.field('hex', '102030')
        b.click_selector('.ed-picker button.primary')
        b.wait_for("!document.querySelector('dialog.ed-picker')")
        self.assertEqual(b.eval("[Ed.fg, Ed.bg]"), ['#ff8000', '#102030'])

    def test_11_picker_samples_the_picture_with_the_eyedropper_settings(self):
        b = self.fresh('edit-photo.png')
        px = b.editor_pixels()
        b.eval("Ed.setToolOptions('eyedropper', {sample: 1, from: 'composite'}); Ed.setColor('fg', '#000000'); true")
        self.open_fg_picker()
        box = b.eval("(() => { const r = document.querySelector('dialog.ed-picker').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
        for (x, y) in ((30, 200), (60, 140), (20, 30)):
            cx, cy = b.pixel_center(x, y)
            self.assertFalse(box[0] <= cx <= box[2] and box[1] <= cy <= box[3], 'the test pixel is not under the picker')
            b.click(cx, cy)
            b.settle(2)
            self.assertEqual('#' + self.picker_values()['hex'], hexs(px[y, x, :3]), f'sampled ({x}, {y})')
        # A drag keeps sampling; with 5 by 5 Average it reads the average.
        b.eval("Ed.setToolOptions('eyedropper', {sample: 5}); true")
        p0, p1 = b.pixel_center(40, 100), b.pixel_center(70, 120)
        b.drag([p0, p1])
        b.settle(2)
        self.assertEqual('#' + self.picker_values()['hex'], weighted_average(px, 70, 120, 5))
        b.click_selector('.ed-picker button.primary')
        b.wait_for("!document.querySelector('dialog.ed-picker')")
        self.assertEqual(b.eval("Ed.fg"), weighted_average(px, 70, 120, 5))
        self.assertEqual(b.eval("Ed.history.list().length"), 1, 'sampling changes no pixels')
        b.eval("Ed.setToolOptions('eyedropper', {sample: 1}); true")

    def test_12_color_fields_in_dialogs_open_the_picker_live(self):
        b = self.fresh()
        b.eval("""window.__seen = []; window.__dlg = Ed.dialog({title: 'Tint Test', fields: [{key: 'tint', label: 'Tint', type: 'color', default: '#112233'}],
            preview(p) { __seen.push(p.tint); }}); window.__res = null; __dlg.then(r => { window.__res = r; }); true""")
        b.settle(2)
        b.click_selector('dialog[data-ed-dialog="Tint Test"] input[type=color]')
        b.wait_for("!!document.querySelector('dialog.ed-picker[open]')")
        self.assertEqual(b.eval("document.querySelector('dialog.ed-picker').dataset.edDialog"), 'Color Picker (Tint)')
        self.assertEqual(self.picker_values()['hex'], '112233')
        self.field('hex', 'aa5500')
        b.wait_for("__seen.includes('#aa5500')", message='the dialog preview sees the color while picking')
        self.assertEqual(b.eval("document.querySelector('dialog[data-ed-dialog=\"Tint Test\"] input[type=color]').value"), '#aa5500')
        b.key('Escape')
        b.wait_for("!document.querySelector('dialog.ed-picker')")
        self.assertEqual(b.eval("document.querySelector('dialog[data-ed-dialog=\"Tint Test\"] input[type=color]').value"), '#112233', 'Cancel puts the old color back')
        self.assertTrue(b.eval("!!document.querySelector('dialog[data-ed-dialog=\"Tint Test\"][open]')"), 'the dialog underneath stays open')
        b.click_selector('dialog[data-ed-dialog="Tint Test"] input[type=color]')
        b.wait_for("!!document.querySelector('dialog.ed-picker[open]')")
        self.field('hex', '00ff7f')
        b.click_selector('.ed-picker button.primary')
        b.wait_for("!document.querySelector('dialog.ed-picker')")
        b.click_selector('dialog[data-ed-dialog="Tint Test"] button.primary')
        b.wait_for("window.__res")
        self.assertEqual(b.eval("__res.tint"), '#00ff7f')

    def test_13_add_to_swatches_and_moving_the_picker(self):
        b = self.fresh()
        b.eval("localStorage.removeItem('ed-picker-pos'); true")
        n = b.eval("Ed.swatches.list().length")
        self.open_fg_picker()
        self.field('hex', '4b0082')
        b.click_selector('.ed-pk-add')
        b.settle(1)
        self.assertEqual(b.eval("Ed.swatches.list().at(-1).hex"), '#4b0082')
        self.assertEqual(b.eval("Ed.swatches.list().length"), n + 1)
        r0 = b.eval("(() => { const r = document.querySelector('dialog.ed-picker').getBoundingClientRect(); return [r.left, r.top]; })()")
        hx, hy = b.center_of('.ed-pk-head')
        b.drag([(hx, hy), (hx - 300, hy + 40)])
        r1 = b.eval("(() => { const r = document.querySelector('dialog.ed-picker').getBoundingClientRect(); return [r.left, r.top]; })()")
        self.assertAlmostEqual(r1[0], r0[0] - 300, delta=2)
        self.assertAlmostEqual(r1[1], r0[1] + 40, delta=2)
        b.key('Escape')
        b.wait_for("!document.querySelector('dialog.ed-picker')")
        self.open_fg_picker()
        r2 = b.eval("(() => { const r = document.querySelector('dialog.ed-picker').getBoundingClientRect(); return [r.left, r.top]; })()")
        self.assertAlmostEqual(r2[0], r1[0], delta=2, msg='the place is remembered')
        b.key('Escape')
        b.eval("localStorage.removeItem('ed-picker-pos'); true")

    # ----- Eyedropper ------------------------------------------------------------------------------------------------------
    def test_20_eyedropper_sample_size_and_sample_against_numpy(self):
        b = self.fresh()
        rng = np.random.default_rng(20)
        base = rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)
        base[..., 3] = 255
        b.load_editor_image(base)
        top = rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)
        top[..., 3] = rng.choice([0, 0, 40, 128, 200, 255], (64, 64))
        top[20:40, 20:40, 3] = 0                       # a transparent hole on the top layer
        img = b.push_pixels(top)
        b.eval(f"(() => {{ const c = document.createElement('canvas'); c.width = c.height = 64; c.getContext('2d').putImageData({img}, 0, 0); Ed.layers.add({{name: 'Top', canvas: c}}); return true; }})()")
        b.settle(3)
        comp, layer = b.editor_pixels(), b.editor_pixels(1)
        b.click_selector('#edTools .ed-tool[data-group="eyedropper"]')
        b.settle(1)
        if b.eval("Ed.tool") != 'eyedropper':
            b.eval("Ed.setTool('eyedropper'); true"); b.settle(1)
        self.assertEqual(b.eval("Ed.tools.get('eyedropper').label"), 'Eyedropper Tool')
        cases = [(10, 10, 1), (0, 0, 3), (63, 5, 5), (30, 30, 11), (2, 60, 31), (32, 32, 51), (32, 32, 101)]
        for src, ref in (('composite', comp), ('layer', layer)):
            b.set_value('.ed-optionsbar select[name="from"]', src)
            for x, y, n in cases:
                b.set_value('.ed-optionsbar select[name="sample"]', str(n))
                b.eval("Ed.setColor('fg', '#010203'); true")
                b.click(*b.pixel_center(x, y))
                b.settle(1)
                want = weighted_average(ref, x, y, n) or '#010203'
                self.assertEqual(b.eval("Ed.fg"), want, f'{src} {n}x{n} at ({x}, {y})')
        # Alt-click picks the background color; a transparent spot leaves it alone.
        b.set_value('.ed-optionsbar select[name="sample"]', '3')
        b.eval("Ed.setColor('bg', '#010203'); true")
        b.click(*b.pixel_center(30, 30), modifiers=ALT)
        b.settle(1)
        self.assertEqual(b.eval("Ed.bg"), '#010203', 'a fully transparent spot of the current layer changes nothing')
        b.click(*b.pixel_center(5, 50), modifiers=ALT)
        b.settle(1)
        self.assertEqual(b.eval("Ed.bg"), weighted_average(layer, 5, 50, 3))
        # The brush's Alt-click reads the same settings.
        b.eval("Ed.setTool('brush'); Ed.setColor('fg', '#010203'); true")
        b.settle(1)
        b.click(*b.pixel_center(50, 12), modifiers=ALT)
        b.settle(1)
        self.assertEqual(b.eval("Ed.fg"), weighted_average(layer, 50, 12, 3))
        self.assertEqual(b.eval("Ed.history.list().length"), 2, 'sampling adds no history state')
        b.eval("Ed.setToolOptions('eyedropper', {sample: 1, from: 'composite'}); Ed.setTool('hand'); true")

    # ----- Color panel and swatches ------------------------------------------------------------------------------------------
    def test_30_swatches_click_ctrl_click_add_rename_delete_and_keep(self):
        b = self.fresh()
        b.eval("localStorage.removeItem('ed-my-swatches'); localStorage.removeItem('ed-recent-colors'); localStorage.removeItem('ed-swatch-closed'); true")
        b.reload()
        b.select_asset('edit-sprite.png')
        self.show_panel('color')
        self.assertTrue(b.eval("!!document.querySelector('#edPanel-color .ed-sw-group[data-group=\"basics\"] .ed-swatch')"))
        b.click_selector('#edPanel-color .ed-sw-group[data-group="basics"] .ed-swatch[data-hex="#ff0000"]')
        self.assertEqual(b.eval("Ed.fg"), '#ff0000')
        b.click_selector('#edPanel-color .ed-sw-group[data-group="basics"] .ed-swatch[data-hex="#0000ff"]', modifiers=CTRL)
        self.assertEqual(b.eval("[Ed.fg, Ed.bg]"), ['#ff0000', '#0000ff'])
        # The chips and the hex field at the top follow the colors; the hex field sets the foreground.
        self.assertEqual(b.eval("document.querySelector('#edPanel-color .ed-cp-hexbox input').value"), 'ff0000')
        b.click_selector('#edPanel-color .ed-cp-hexbox input')
        b.key('Ctrl+A'); b.type_text('00c0a0'); b.key('Enter')
        b.settle(1)
        self.assertEqual(b.eval("Ed.fg"), '#00c0a0')
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('#edPanel-color .ed-cp-chip[data-which=fg]')).backgroundColor"), 'rgb(0, 192, 160)')
        # + adds the foreground to My Swatches; a click on it selects it.
        b.click_selector('#edPanel-color [data-action="add"]')
        b.settle(2)
        sel = '#edPanel-color .ed-sw-group[data-group="mine"] .ed-swatch'
        self.assertEqual(b.eval(f"[...document.querySelectorAll('{sel}')].map(s => s.dataset.hex)"), ['#00c0a0'])
        b.eval("Ed.setColor('fg', '#123456'); true")
        b.click_selector('#edPanel-color [data-action="add"]')
        b.settle(2)
        self.assertEqual(b.eval(f"[...document.querySelectorAll('{sel}')].map(s => s.dataset.hex)"), ['#00c0a0', '#123456'])
        # Double-click renames.
        b.double_click(*b.center_of(f'{sel}[data-index="0"]'))
        b.wait_for("!!document.querySelector('dialog[data-ed-dialog=\"Swatch Name\"][open]')")
        b.click_selector('dialog[data-ed-dialog="Swatch Name"] input[name=name]')
        b.key('Ctrl+A'); b.type_text('Sea green')
        b.click_selector('dialog[data-ed-dialog="Swatch Name"] button.primary')
        b.settle(2)
        self.assertEqual(b.eval("Ed.swatches.list()[0].name"), 'Sea green')
        self.assertIn('Sea green', b.eval(f"document.querySelector('{sel}[data-index=\"0\"]').title"))
        # Recent colors collect the foreground once it stays.
        b.wait_for("!!document.querySelector('#edPanel-color .ed-sw-group[data-group=\"recent\"] .ed-swatch[data-hex=\"#00c0a0\"]')", timeout=5)
        # Kept across a reload.
        b.reload()
        b.select_asset('edit-sprite.png')
        self.show_panel('color')
        self.assertEqual(b.eval(f"[...document.querySelectorAll('{sel}')].map(s => s.dataset.hex)"), ['#00c0a0', '#123456'])
        self.assertEqual(b.eval("Ed.swatches.list()[0].name"), 'Sea green')
        self.assertIn("#00c0a0", b.eval("Ed.swatches.recent()"))
        # Select + trash deletes; Alt+click deletes.
        self.assertTrue(b.eval("document.querySelector('#edPanel-color [data-action=\"delete\"]').disabled"))
        b.click_selector(f'{sel}[data-index="1"]')
        self.assertEqual(b.eval("Ed.fg"), '#123456')
        b.click_selector('#edPanel-color [data-action="delete"]')
        b.settle(2)
        self.assertEqual(b.eval(f"[...document.querySelectorAll('{sel}')].map(s => s.dataset.hex)"), ['#00c0a0'])
        b.click_selector(f'{sel}[data-index="0"]', modifiers=ALT)
        b.settle(2)
        self.assertEqual(b.eval("Ed.swatches.list().length"), 0)
        self.assertTrue(b.eval("!!document.querySelector('#edPanel-color .ed-sw-group[data-group=\"mine\"] .ed-sw-empty')"))
        # A collapsed group opens with its header and stays open.
        b.click_selector('#edPanel-color .ed-sw-group[data-group="pastel"] .ed-sw-head')
        b.settle(1)
        self.assertGreater(b.eval("document.querySelectorAll('#edPanel-color .ed-sw-group[data-group=\"pastel\"] .ed-swatch').length"), 10)
        self.assertNotIn('pastel', b.eval("JSON.parse(localStorage.getItem('ed-swatch-closed'))"))
        # The chips open the Color Picker.
        b.click_selector('#edPanel-color .ed-cp-chip[data-which=bg]')
        b.wait_for("!!document.querySelector('dialog.ed-picker[open]')")
        self.assertEqual(b.eval("document.querySelector('dialog.ed-picker').dataset.edDialog"), 'Color Picker (Background Color)')
        b.key('Escape')
        b.wait_for("!document.querySelector('dialog.ed-picker')")

    def test_31_this_asset_lists_the_pixel_art_palette_exactly(self):
        b = self.fresh('edit-tiny.png')
        self.show_panel('color')
        b.wait_for("document.querySelectorAll('#edPanel-color .ed-sw-group[data-group=\"asset\"] .ed-swatch').length")
        got = b.eval("[...document.querySelectorAll('#edPanel-color .ed-sw-group[data-group=\"asset\"] .ed-swatch')].map(s => s.dataset.hex)")
        want = {hexs(c[:3]) for c in TINY_PALETTE.values() if c[3] == 255}
        self.assertEqual(set(got), want)
        self.assertEqual(got[0], hexs(TINY_PALETTE['r'][:3]), 'the most used color comes first')
        # It follows an edit.
        b.eval("Ed.select.rect(0, 0, 16, 16); true")
        self.fill(0, 0, 16, 16, '#3060f0')
        b.eval("Ed.select.none(); true")
        b.wait_for("[...document.querySelectorAll('#edPanel-color .ed-sw-group[data-group=\"asset\"] .ed-swatch')].map(s => s.dataset.hex).join() === '#3060f0'", timeout=5)

    # ----- History -------------------------------------------------------------------------------------------------------------
    def test_40_history_click_a_state_and_delete_states(self):
        b = self.fresh()
        self.show_panel('history')
        shots = [b.editor_pixels()]
        for i, color in enumerate(('#ff0000', '#00ff00', '#0000ff')):
            self.fill(4 + 8 * i, 4, 10, 10, color)
            shots.append(b.editor_pixels())
        items = '#edPanel-history .ed-history-item'
        self.assertEqual(b.eval(f"[...document.querySelectorAll('{items}')].map(e => e.textContent)"), ['Open', 'Fill', 'Fill', 'Fill'])
        b.click_selector(f'{items}[data-index="1"]')
        b.settle(2)
        self.assertPixelsClose(b.editor_pixels(), shots[1])
        self.assertEqual(b.eval("Ed.history.index"), 1)
        self.assertTrue(b.eval(f"document.querySelector('{items}[data-index=\"3\"]').classList.contains('undone')"))
        b.click_selector(f'{items}[data-index="3"]')
        b.settle(2)
        self.assertPixelsClose(b.editor_pixels(), shots[3])
        # Delete removes the current state and every later one.
        b.click_selector(f'{items}[data-index="2"]')
        b.click_selector('#edPanel-history [data-action="delete"]')
        b.settle(2)
        self.assertEqual(b.eval("Ed.history.list().map(s => s.label)"), ['Open', 'Fill'])
        self.assertEqual(b.eval("Ed.history.index"), 1)
        self.assertPixelsClose(b.editor_pixels(), shots[1])
        self.assertFalse(b.eval("Ed.history.canRedo()"))
        b.click_selector(f'{items}[data-index="0"]')
        b.settle(1)
        self.assertTrue(b.eval("document.querySelector('#edPanel-history [data-action=\"delete\"]').disabled"), 'the opened state cannot be deleted')
        self.assertPixelsClose(b.editor_pixels(), shots[0])

    def test_41_snapshots_restore_exactly_undo_and_feed_the_history_brush(self):
        b = self.fresh()
        self.show_panel('history')
        opened = b.editor_pixels()
        L1 = b.eval("Ed.layers.list()[0].id")
        self.assertEqual(b.eval("Ed.historySource && Ed.historySource.label"), 'edit-sprite.png')
        self.assertPixelsClose(b.pixels(f"Ed.historySource.layers.get('{L1}')"), opened)
        # Build a document: a fill, a second layer with pixels, opacity, blend and a mask.
        self.fill(2, 2, 20, 12, '#ff0000')
        b.eval("Ed.layers.add({name: 'B'}); true")
        self.fill(10, 10, 30, 30, '#20c040')
        b.eval("Ed.layers.set(null, {opacity: 0.5, blend: 'multiply'}); Ed.layers.addMask(null, 'reveal'); true")
        b.eval("Ed.applyToTarget('Hide', g => g.clearRect(10, 10, 8, 30))")   # hides part of B through its mask (the target is the mask now)
        LB = b.eval("Ed.layers.active().id")
        b.settle(2)
        snap = b.editor_pixels()
        snap_layers = [b.editor_pixels(0), b.editor_pixels(1), b.editor_pixels('mask')]
        props = b.eval("Ed.layers.list().map(L => [L.id, L.name, L.opacity, L.blend, !!L.mask])")
        b.click_selector('#edPanel-history [data-action="snapshot"]')
        b.settle(2)
        self.assertEqual(b.eval("[...document.querySelectorAll('#edPanel-history .ed-history-snap .ed-hist-name')].map(e => e.textContent)"), ['edit-sprite.png', 'Snapshot 1'])
        # Change a lot: pixels on both layers, settings, the mask, a new layer, then delete B.
        b.eval("Ed.layers.setActive(1, 'pixels'); true")
        self.fill(0, 30, 64, 10, '#ffff00')
        b.eval("Ed.layers.set(1, {opacity: 1, blend: 'normal', name: 'B2'}); Ed.layers.setActive(0); true")
        self.fill(40, 40, 20, 20, '#ff00ff')
        b.eval("Ed.layers.add({name: 'C'}); true")
        self.fill(0, 0, 64, 4, '#00ffff')
        b.eval(f"Ed.layers.remove('{LB}'); true")
        b.settle(2)
        before = b.editor_pixels()
        n_states = b.eval("Ed.history.list().length")
        b.click_selector('#edPanel-history .ed-hist-row:nth-child(2) .ed-history-snap')
        b.settle(3)
        self.assertPixelsClose(b.editor_pixels(), snap)
        self.assertEqual(b.eval("Ed.layers.list().map(L => [L.id, L.name, L.opacity, L.blend, !!L.mask])"), props)
        self.assertPixelsClose(b.editor_pixels(0), snap_layers[0])
        self.assertPixelsClose(b.editor_pixels(1), snap_layers[1])
        b.eval(f"Ed.layers.setActive('{LB}', 'mask'); true")
        self.assertPixelsClose(b.editor_pixels('mask'), snap_layers[2])
        self.assertEqual(b.eval("Ed.history.list().length"), n_states + 1)
        self.assertEqual(b.eval("Ed.history.list().at(-1).label"), 'Snapshot 1')
        self.assertTrue(b.eval("document.querySelector('#edPanel-history .ed-hist-row:nth-child(2) .ed-history-snap').classList.contains('current')"))
        # Undo takes the restore back exactly; redo brings it again.
        b.key('Ctrl+Z')
        b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        self.assertPixelsClose(b.editor_pixels(), before)
        self.assertEqual(b.eval("Ed.layers.list().map(L => L.name).slice(1)"), ['C'])
        b.key('Ctrl+Shift+Z')
        b.wait_for("S.ops.at(-1)?.tool === 'redo'")
        self.assertPixelsClose(b.editor_pixels(), snap)
        # Clicking it again when nothing changed adds nothing.
        b.click_selector('#edPanel-history .ed-hist-row:nth-child(2) .ed-history-snap')
        b.settle(2)
        self.assertEqual(b.eval("Ed.history.list().length"), n_states + 1)
        # The opened snapshot gives back the picture as opened.
        b.click_selector('#edPanel-history .ed-hist-row:nth-child(1) .ed-history-snap')
        b.settle(3)
        self.assertPixelsClose(b.editor_pixels(), opened)
        self.assertEqual(b.eval("Ed.layers.count()"), 1)
        # The History Brush source: the box beside Snapshot 1.
        b.click_selector('#edPanel-history .ed-hist-row:nth-child(2) .ed-hsrc')
        b.settle(1)
        self.assertEqual(b.eval("[Ed.historySource.label, Ed.historySource.kind]"), ['Snapshot 1', 'snapshot'])
        self.assertPixelsClose(b.pixels(f"Ed.historySource.layers.get('{LB}')"), snap_layers[1])
        self.assertPixelsClose(b.pixels(f"Ed.historySource.layers.get('{L1}')"), snap_layers[0])
        self.assertIn('Snapshot 1', b.text('#edPanel-history .ed-hist-src'))
        # Rename by double-click; the source label follows.
        b.double_click(*b.center_of('#edPanel-history .ed-hist-row:nth-child(2) .ed-hist-name'))
        b.wait_for("!!document.querySelector('#edPanel-history input.ed-rename')")
        b.key('Ctrl+A'); b.type_text('Green box'); b.key('Enter')
        b.settle(2)
        self.assertEqual(b.eval("Ed.historySource.label"), 'Green box')
        # Delete the selected snapshot: the source falls back to the opened one.
        b.click_selector('#edPanel-history .ed-hist-row:nth-child(2) .ed-history-snap')
        b.settle(2)
        n = b.eval("Ed.history.list().length")
        b.click_selector('#edPanel-history [data-action="delete"]')
        b.settle(2)
        self.assertEqual(b.eval("document.querySelectorAll('#edPanel-history .ed-history-snap').length"), 1)
        self.assertEqual(b.eval("Ed.history.list().length"), n, 'deleting a snapshot keeps the states')
        self.assertEqual(b.eval("Ed.historySource.label"), 'edit-sprite.png')
        # Opening another asset starts over with its own opened snapshot.
        b.select_asset('edit-tiny.png')
        self.assertEqual(b.eval("Ed.historySource.label"), 'edit-tiny.png')
        self.assertEqual(b.eval("document.querySelectorAll('#edPanel-history .ed-history-snap').length"), 1)

    def test_42_the_history_brush_paints_from_the_chosen_source(self):
        b = self.fresh()
        if not b.eval("!!Ed.tools.get('history-brush')"): self.skipTest('paint.js provides no History Brush')
        self.show_panel('history')
        opened = b.editor_pixels()
        red = np.broadcast_to(np.array([255, 0, 0, 255], np.uint8), opened.shape)
        blue = np.broadcast_to(np.array([0, 0, 255, 255], np.uint8), opened.shape)
        b.eval("Ed.select.all(); true")
        self.fill(0, 0, 64, 64, '#ff0000')
        b.eval("Ed.select.none(); Ed.setTool('history-brush'); Ed.setToolOptions('history-brush', {size: 16, hardness: 100, opacity: 100}); true")
        b.settle(2)
        path = [(4.5, 20.5), (59.5, 20.5)]
        b.drag_image(path, center=False)
        b.settle(3)
        d = distance_to_path(64, 64, path)
        px = b.editor_pixels()
        self.assertPixelsClose(px, opened, mask=d <= 6, msg='the opened picture comes back under the stroke')
        self.assertPixelsClose(px, red, mask=d >= 10, msg='the rest stays red')
        # With Snapshot 1 (all red) as the source, a stroke over blue paints red.
        b.eval("Ed.select.all(); true")
        self.fill(0, 0, 64, 64, '#ff0000')
        b.click_selector('#edPanel-history [data-action="snapshot"]')
        self.fill(0, 0, 64, 64, '#0000ff')
        b.eval("Ed.select.none(); true")
        b.settle(3)
        b.click_selector('#edPanel-history .ed-hist-row:nth-child(2) .ed-hsrc')
        self.assertEqual(b.eval("Ed.historySource.label"), 'Snapshot 1')
        b.drag_image(path, center=False)
        b.settle(3)
        px = b.editor_pixels()
        self.assertPixelsClose(px, red, mask=d <= 6)
        self.assertPixelsClose(px, blue, mask=d >= 10)
        b.eval("Ed.setTool('hand'); true")

    # ----- layout --------------------------------------------------------------------------------------------------------------
    def test_90_layout_at_1366_and_1920(self):
        b = self.fresh('edit-photo.png')
        b.eval("Ed.layers.add({name: 'Second'}); true")
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h)
                b.eval("document.querySelector('.inspector').scrollTop = 0; Ed.view.fit(); true")
                for pid in ('layers', 'history', 'color'):
                    self.show_panel(pid)
                    b.screenshot(f'color-{pid}-{w}x{h}.png')
                    tabs = b.eval("[...document.querySelectorAll('#edDock .ed-tab')].map(t => { const r = t.getBoundingClientRect(), d = document.getElementById('edDock').getBoundingClientRect(); return [t.dataset.panel, r.top, r.right <= d.right + 1 && r.left >= d.left - 1, t.scrollWidth <= t.clientWidth + 1]; })")
                    self.assertEqual(len({t[1] for t in tabs}), 1, f'the tabs sit on one row at {w}x{h}')
                    self.assertTrue(all(t[2] and t[3] for t in tabs), f'no tab is cut off at {w}x{h}: {tabs}')
                    spill = b.eval(f"""[...document.querySelectorAll('#edPanel-{pid} *')].filter(e => {{ if (!e.getClientRects().length) return false;
                        const r = e.getBoundingClientRect(), p = document.getElementById('edDock').getBoundingClientRect(), s = e.closest('.ed-sw-list, .ed-history-list');
                        if (s && s !== e) {{ const q = s.getBoundingClientRect(); if (r.bottom <= q.top || r.top >= q.bottom) return false; }}
                        return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1); }}).map(e => e.className || e.tagName)""")
                    if pid != 'layers': self.assertEqual(spill, [], f'{pid} panel spills out of the dock at {w}x{h}')
                rows = b.eval("Ed.panels.show('layers'); [...document.querySelectorAll('#edPanel-layers .ed-layer')].filter(r => { const q = r.getBoundingClientRect(); return q.top >= 0 && q.bottom <= innerHeight; }).length")
                b.settle(2)
                rows = b.eval("[...document.querySelectorAll('#edPanel-layers .ed-layer')].filter(r => { const q = r.getBoundingClientRect(); return q.height && q.top >= 0 && q.bottom <= innerHeight; }).length")
                self.assertEqual(rows, 2, f'both layer rows are on screen at {w}x{h}')
                # The Eyedropper's options fit in the options bar.
                b.eval("Ed.setTool('eyedropper'); true"); b.settle(2)
                spill = b.eval("""[...document.querySelectorAll('.ed-optionsbar *')].filter(e => { if (!e.getClientRects().length) return false;
                    const r = e.getBoundingClientRect(), p = e.closest('.ed-optionsbar').getBoundingClientRect(); return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); }).map(e => e.className || e.tagName)""")
                self.assertEqual(spill, [], f'the Eyedropper options spill at {w}x{h}')
                # The picker fits in the window.
                self.open_fg_picker()
                b.screenshot(f'color-picker-{w}x{h}.png')
                r = b.eval("(() => { const r = document.querySelector('dialog.ed-picker').getBoundingClientRect(), d = document.querySelector('dialog.ed-picker'); return [r.left, r.top, r.right, r.bottom, d.scrollHeight <= d.clientHeight + 1, d.scrollWidth <= d.clientWidth + 1]; })()")
                self.assertTrue(r[0] >= 0 and r[1] >= 0 and r[2] <= w and r[3] <= h and r[4] and r[5], f'the picker fits at {w}x{h}: {r}')
                b.key('Escape')
                b.wait_for("!document.querySelector('dialog.ed-picker')")
                b.eval("Ed.setTool('hand'); true")
        finally:
            b.set_viewport(*self.viewport)
