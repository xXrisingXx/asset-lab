"""Layer styles (src/web/editor/styles.js) end to end in headless Edge: every effect's pixels against independent numpy
references (brute-force distances to the outline for strokes, spread and choke; a true Gaussian for Size; the compositing
model written out per pixel), the Layer Style dialog driven with real mouse and keyboard input (live preview, OK = one
'Layer Style' state, Cancel and undo restore exactly), Copy / Paste / Clear Layer Style, Hide All Effects, the fx badge,
save + reopen of a styled document, speed on a 2048 x 2048 sheet and the dialog's layout at 1366 x 768 and 1920 x 1080.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_styles*.py" -v
"""
import json
import math
import time
import numpy as np
from harness import EditorTestCase, premultiplied, rgba
from fakegame import image_array

PAD = 40   # the references work on a canvas this much larger on every side (effects reach past the edges)
DIALOG = '[data-ed-dialog="Layer Style"]'


# ----- references -------------------------------------------------------------------------------------------------------
def unit(a):
    return a.astype(np.float64) / 255


def grow(a, value=0.0):
    out = np.full((a.shape[0] + 2 * PAD, a.shape[1] + 2 * PAD) + a.shape[2:], value, np.float64)
    out[PAD:-PAD, PAD:-PAD] = a
    return out


def shrink(a):
    return a[PAD:-PAD, PAD:-PAD]


def outline_distance(alpha, inside):
    """Brute force over every pixel q on the other side of the 50% outline: min |p - q| minus how far q's own alpha puts the
    outline from its centre (a pixel at alpha 1 has its edge half a pixel out). alpha: float 0..1 on the padded canvas."""
    h, w = alpha.shape
    feat = alpha < 128 / 255 if inside else alpha >= 128 / 255
    qy, qx = np.nonzero(feat)
    corr = (.5 - alpha[qy, qx]) if inside else (alpha[qy, qx] - .5)
    py, px = np.mgrid[0:h, 0:w]
    best = np.full(h * w, 1e9)
    P = np.stack([py.ravel(), px.ravel()], 1).astype(np.float64)
    for s in range(0, len(qy), 512):
        d = np.sqrt((P[:, None, 0] - qy[None, s:s + 512]) ** 2 + (P[:, None, 1] - qx[None, s:s + 512]) ** 2) - corr[None, s:s + 512]
        best = np.minimum(best, d.min(1))
    return best.reshape(h, w)


def dilate(alpha, r):
    return alpha if r <= 0 else np.maximum(alpha, np.clip(r + .5 - outline_distance(alpha, False), 0, 1))


def erode(alpha, r):
    return alpha if r <= 0 else np.minimum(alpha, np.clip(outline_distance(alpha, True) - r + .5, 0, 1))


def gaussian(a, sigma, edge=0.0):
    """A true Gaussian (separable, kernel to 5 sigma) with `edge` beyond the canvas."""
    if sigma <= .05: return a
    r = int(math.ceil(5 * sigma))
    k = np.exp(-np.arange(-r, r + 1) ** 2 / (2 * sigma * sigma))
    k /= k.sum()
    p = np.pad(a, r, constant_values=edge)
    p = np.apply_along_axis(lambda v: np.convolve(v, k, 'valid'), 1, p)
    return np.apply_along_axis(lambda v: np.convolve(v, k, 'valid'), 0, p)


def shift(a, dx, dy, edge=0.0):
    out = np.full_like(a, edge)
    h, w = a.shape
    out[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = a[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
    return out


def offset(angle, distance):
    t = math.radians(angle)
    return round(-distance * math.cos(t)), round(distance * math.sin(t))


def color(hexcolor):
    return np.array(rgba(hexcolor)[:3], np.float64) / 255


def blend(mode, cb, cs):
    return {'normal': lambda: cs, 'multiply': lambda: cb * cs, 'screen': lambda: cb + cs - cb * cs,
            'overlay': lambda: np.where(cb <= .5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))}[mode]()


def over_premul(dst, rgb, m, mode='normal'):
    """W3C source-over (with a separable blend) of color rgb at coverage m onto premultiplied dst (H, W, 4 floats)."""
    ab = dst[..., 3:]
    with np.errstate(divide='ignore', invalid='ignore'):
        cb = np.where(ab > 0, dst[..., :3] / np.where(ab > 0, ab, 1), 0)
    cs = np.broadcast_to(rgb, dst[..., :3].shape)
    m = m[..., None]
    mixed = (1 - ab) * cs + ab * np.clip(blend(mode, cb, cs), 0, 1)
    out = np.empty_like(dst)
    out[..., :3] = m * mixed + (1 - m) * dst[..., :3]
    out[..., 3:] = m + (1 - m) * ab
    return out


def premul(a):
    f = unit(a)
    out = f.copy()
    out[..., :3] *= f[..., 3:]
    return out


def to_bytes(p):
    """Premultiplied floats -> the uint8 premultiplied values harness.premultiplied() gives for the page's pixels."""
    return np.floor(np.clip(p, 0, 1) * 255 + .5).astype(np.uint8)


def styled(layer, effects, below=None, fill=1.0):
    """The model in styles.js's header, per pixel. layer / below: uint8 RGBA (below None = transparent). Returns premultiplied
    floats of the document."""
    P = grow(premul(layer))
    a = P[..., 3]
    B = grow(premul(below)) if below is not None else np.zeros_like(P)
    eff = {t: p for t, p in effects.items() if p.get('enabled', True)}
    for t in ('drop-shadow', 'outer-glow'):
        if t not in eff: continue
        p = eff[t]
        sp = p['size'] * p['spread'] / 100
        M = gaussian(dilate(a, sp), (p['size'] - sp) / 2)
        if t == 'drop-shadow': M = shift(M, *offset(p['angle'], p['distance']))
        den = 1 - a * fill
        m = np.where(den > 1e-6, p['opacity'] / 100 * M * (1 - a) / np.where(den > 1e-6, den, 1), 0)
        B = over_premul(B, color(p['color']), m, p['blend'])
    Liso = P * fill
    for t in ('gradient-overlay', 'color-overlay', 'inner-glow', 'inner-shadow'):
        if t not in eff: continue
        p = eff[t]
        o = p['opacity'] / 100
        if t == 'color-overlay':
            k, X = np.full(a.shape, o), np.broadcast_to(color(p['color']), P[..., :3].shape)
        elif t == 'gradient-overlay':
            k, X = p['_reference'](P.shape[1], P.shape[0])
            k = k * o
        else:
            ck = p['size'] * p['choke'] / 100
            M = gaussian(1 - erode(a, ck), (p['size'] - ck) / 2, edge=1.0)
            if t == 'inner-shadow': M = shift(M, *offset(p['angle'], p['distance']), edge=1.0)
            k, X = o * M, np.broadcast_to(color(p['color']), P[..., :3].shape)
        Liso = cover(Liso, B, k, a, X, p['blend'])
    if 'stroke' in eff:
        p = eff['stroke']
        o, r = p['opacity'] / 100, p['size']
        ro = r if p['position'] == 'outside' else r / 2 if p['position'] == 'center' else 0
        ri = r if p['position'] == 'inside' else r / 2 if p['position'] == 'center' else 0
        X = np.broadcast_to(color(p['color']), P[..., :3].shape)
        inner = o * np.maximum(0, a - erode(a, ri)) if ri else np.zeros_like(a)
        outer = o * np.maximum(0, dilate(a, ro) - a) if ro else np.zeros_like(a)
        with np.errstate(divide='ignore', invalid='ignore'):
            Liso = cover(Liso, B, np.where(a > 0, np.minimum(1, inner / np.where(a > 0, a, 1)), 0), a, X, p['blend'])
        Xc = composite_color(Liso, B, X, p['blend'])
        Liso = Liso.copy()
        Liso[..., :3] += outer[..., None] * Xc
        Liso[..., 3] = np.minimum(1, Liso[..., 3] + outer)
    out = Liso + (1 - Liso[..., 3:]) * B
    return shrink(out)


def composite_color(Liso, B, X, mode):
    if mode == 'normal': return X
    al = Liso[..., 3:]
    ac = al + (1 - al) * B[..., 3:]
    with np.errstate(divide='ignore', invalid='ignore'):
        cb = np.where(ac > 0, (Liso[..., :3] + (1 - al) * B[..., :3]) / np.where(ac > 0, ac, 1), 0)
    return (1 - ac) * X + ac * np.clip(blend(mode, cb, X), 0, 1)


def cover(Liso, B, k, a, X, mode):
    """An inner effect with coverage k inside the shape: L = k a X + (1 - k) L."""
    X = composite_color(Liso, B, X, mode)
    m = (k * a)[..., None]
    out = np.empty_like(Liso)
    out[..., :3] = m * X + (1 - k[..., None]) * Liso[..., :3]
    out[..., 3:] = m + (1 - k[..., None]) * Liso[..., 3:]
    return np.where((a > 0)[..., None], out, Liso)


def eval_stops(at, vals, mids, t):
    """Stops blended in sRGB; a midpoint m bends a segment with weight P ** (ln .5 / ln m), m kept in 5..95%."""
    at, vals, mids = np.asarray(at, float), np.asarray(vals, float), np.asarray(mids, float)
    if vals.ndim == 1: vals = vals[:, None]
    n = len(at)
    if n == 1: return np.broadcast_to(vals[0], t.shape + vals.shape[1:]).copy()
    i = np.clip(np.searchsorted(at, t, side='right') - 1, 0, n - 2)
    a, b = at[i], at[i + 1]
    span = b - a
    q = np.where(span > 0, (t - a) / np.where(span > 0, span, 1), 0)
    m = np.clip(mids[i], .05, .95)
    e = np.where(np.abs(m - .5) < 1e-9, 1.0, np.log(.5) / np.log(m))
    w = np.where(e == 1, q, np.power(np.maximum(q, 0), e))
    v = vals[i] + (vals[i + 1] - vals[i]) * w[..., None]
    v = np.where((t <= at[0])[..., None], vals[0], v)
    return np.where((t >= at[-1])[..., None], vals[-1], v)


def gradient_reference(p, layer):
    """Gradient Overlay's colors and alpha on the padded canvas: t along the angle through the layer's (or canvas's) bounds."""
    h, w = layer.shape[:2]
    if p['align'] and (layer[..., 3] > 0).any():
        ys, xs = np.nonzero(layer[..., 3] > 0)
        bx, by, bw, bh = xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    else: bx, by, bw, bh = 0, 0, w, h
    th = math.radians(p['angle'])
    ux, uy = math.cos(th), -math.sin(th)
    length = (abs(bw * ux) + abs(bh * uy)) * p['scale'] / 100

    def make(W, H):
        Y, X = np.mgrid[0:H, 0:W].astype(np.float64)
        X, Y = X - PAD + .5 - (bx + bw / 2), Y - PAD + .5 - (by + bh / 2)
        t = np.sqrt(X * X + Y * Y) / (length / 2) if p['style'] == 'radial' else (X * ux + Y * uy) / length + .5
        t = np.clip(t, 0, 1)
        if p['reverse']: t = 1 - t
        g = p['gradient']
        rgb = eval_stops([s['at'] for s in g['stops']], [rgba(s['color'])[:3] for s in g['stops']], [s['mid'] for s in g['stops']], t) / 255
        a = eval_stops([s['at'] for s in g['alpha']], [s['a'] for s in g['alpha']], [s['mid'] for s in g['alpha']], t)[..., 0]
        return a, rgb
    return make


# ----- test shapes ------------------------------------------------------------------------------------------------------
def crisp_shape(w=64, h=64):
    """Hard-edged pixel art: a rectangle, a disc and a one-pixel diagonal, in three colors, on transparency."""
    a = np.zeros((h, w, 4), np.uint8)
    a[12:28, 10:30] = (40, 120, 220, 255)
    yy, xx = np.mgrid[0:h, 0:w]
    a[(xx + .5 - 44) ** 2 + (yy + .5 - 40) ** 2 <= 10.5 ** 2] = (230, 60, 40, 255)
    for i in range(14): a[44 + i, 8 + i] = (250, 220, 60, 255)
    return a


def soft_shape(w=64, h=64):
    """The sprite's anti-aliased red disc alone (alpha has every level along its edge)."""
    s = image_array('edit-sprite.png').copy()
    s[:30] = 0
    s[:, :30] = 0
    return s


class StyleTests(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers --------------------------------------------------------------------------------------------------------
    def load(self, layer, below=None):
        b = self.browser
        if not b.eval("!!(window.S && S.asset)"): b.select_asset('edit-sprite.png')
        b.load_editor_image(layer)
        if below is not None:
            src = b.push_pixels(below)
            b.eval(f"(() => {{ const c = Ed.util.canvas({below.shape[1]}, {below.shape[0]}); Ed.util.write(c, {src}); Ed.layers.add({{canvas: c, at: 0, name: 'Below', history: false}}); Ed.layers.setActive(1); return true; }})()")
        b.settle()

    def set_fx(self, effects, index=None, fill=None):
        idx = 'Ed.doc.activeIndex' if index is None else index
        props = {'effects': effects}
        if fill is not None: props['fill'] = fill
        self.browser.eval(f"Ed.layers.set({idx}, {json.dumps(props)}, {{history: false}}); Ed.flush(); true")

    def full(self, t, **p):
        d = self.browser.eval(f"Ed.layerStyle.defaults({json.dumps(t)})")
        d.update(p)
        return d

    def check(self, effects, layer, below=None, fill=1.0, max_abs=2, mean_abs=None, msg=''):
        got = premultiplied(self.browser.editor_pixels())
        want = to_bytes(styled(layer, effects, below, fill))
        diff = np.abs(got.astype(int) - want.astype(int))
        self.report.append(f'{msg}: max {diff.max()} mean {diff.mean():.4f}')
        self.assertPixelsClose(got, want, max_abs=max_abs, mean_abs=mean_abs, msg=msg)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = []

    @classmethod
    def tearDownClass(cls):
        from harness import output_path
        output_path('styles-report.txt').write_text('\n'.join(cls.report), encoding='utf-8')
        super().tearDownClass()

    # ----- effects against the references ---------------------------------------------------------------------------------
    def test_01_stroke_outside_inside_center_on_pixel_art(self):
        shape = crisp_shape()
        self.load(shape)
        for pos, size in (('outside', 3), ('inside', 4), ('center', 5), ('outside', 1), ('outside', 2.5)):
            fx = {'stroke': self.full('stroke', size=size, position=pos, color='#10e070')}
            self.set_fx(fx)
            self.check(fx, shape, msg=f'stroke {pos} {size}')
        self.assertNoBrowserErrors()

    def test_02_stroke_on_an_antialiased_shape_over_a_background(self):
        shape, below = soft_shape(), np.broadcast_to(np.array((30, 50, 80, 255), np.uint8), (64, 64, 4)).copy()
        below[:, 48:] = (240, 240, 230, 255)
        self.load(shape, below)
        for pos, size, mode, fill in (('outside', 3, 'normal', 1.0), ('inside', 2, 'normal', 1.0), ('center', 4, 'normal', 0.4), ('outside', 2, 'multiply', 1.0),
                                      ('outside', 3, 'normal', 0.0)):
            fx = {'stroke': self.full('stroke', size=size, position=pos, color='#f0d020', blend=mode, opacity=90)}
            self.set_fx(fx, fill=fill)
            self.check(fx, shape, below, fill=fill, msg=f'soft stroke {pos} {size} {mode} fill {fill}')
        self.set_fx({}, fill=1)
        self.assertNoBrowserErrors()

    def test_03_outer_glow_and_drop_shadow(self):
        below = np.broadcast_to(np.array((200, 210, 220, 255), np.uint8), (64, 64, 4)).copy()
        for shape, name in ((crisp_shape(), 'crisp'), (soft_shape(), 'soft')):
            self.load(shape, below)
            cases = [({'outer-glow': self.full('outer-glow', size=8, color='#ff8000', blend='normal')}, 1.0),
                     ({'outer-glow': self.full('outer-glow', size=10, spread=40)}, 1.0),
                     ({'drop-shadow': self.full('drop-shadow')}, 1.0),
                     ({'drop-shadow': self.full('drop-shadow', angle=30, distance=7, spread=25, size=9, opacity=100, blend='normal')}, 0.0),
                     ({'drop-shadow': self.full('drop-shadow', size=0, distance=3, angle=-90)}, 0.5),
                     ({'drop-shadow': self.full('drop-shadow', size=4), 'outer-glow': self.full('outer-glow', size=6, color='#40ff40', blend='screen')}, 1.0)]
            for fx, fill in cases:
                self.set_fx(fx, fill=fill)
                # the Size blur is three extended box passes: measured within 2.5/255 of a true Gaussian on a step edge
                self.check(fx, shape, below, fill=fill, max_abs=4, mean_abs=0.5, msg=f'{name} {sorted(fx)} fill {fill}')
            self.set_fx({}, fill=1)
        self.assertNoBrowserErrors()

    def test_04_color_and_gradient_overlay(self):
        below = np.broadcast_to(np.array((20, 30, 40, 255), np.uint8), (64, 64, 4)).copy()
        below[20:44] = (250, 250, 250, 255)
        g3 = {'stops': [{'at': 0, 'color': '#ff0000', 'mid': .5}, {'at': .4, 'color': '#00ff80', 'mid': .3}, {'at': 1, 'color': '#2020ff', 'mid': .5}],
              'alpha': [{'at': 0, 'a': 1, 'mid': .5}, {'at': .7, 'a': .8, 'mid': .6}, {'at': 1, 'a': .1, 'mid': .5}]}
        for shape, name in ((crisp_shape(), 'crisp'), (soft_shape(), 'soft')):
            self.load(shape, below)
            cases = [({'color-overlay': self.full('color-overlay')}, 1.0),
                     ({'color-overlay': self.full('color-overlay', color='#30c0ff', opacity=60, blend='multiply')}, 1.0),
                     ({'color-overlay': self.full('color-overlay', color='#ffe040')}, 0.0),
                     ({'gradient-overlay': self.full('gradient-overlay')}, 1.0),
                     ({'gradient-overlay': self.full('gradient-overlay', gradient=g3, angle=30, scale=80, opacity=90)}, 1.0),
                     ({'gradient-overlay': self.full('gradient-overlay', gradient=g3, style='radial', reverse=True, align=False, scale=120, blend='screen')}, 0.5),
                     ({'gradient-overlay': self.full('gradient-overlay', gradient=g3, angle=-135), 'color-overlay': self.full('color-overlay', opacity=50, blend='overlay')}, 1.0)]
            for fx, fill in cases:
                self.set_fx(fx, fill=fill)
                ref = {t: dict(p, _reference=gradient_reference(p, shape)) if t == 'gradient-overlay' else p for t, p in fx.items()}
                self.check(ref, shape, below, fill=fill, msg=f'{name} {sorted(fx)} fill {fill}')
            self.set_fx({}, fill=1)
        self.assertNoBrowserErrors()

    def test_05_inner_shadow_and_inner_glow(self):
        below = np.broadcast_to(np.array((90, 90, 90, 255), np.uint8), (64, 64, 4)).copy()
        for shape, name in ((crisp_shape(), 'crisp'), (soft_shape(), 'soft')):
            self.load(shape, below)
            cases = [({'inner-shadow': self.full('inner-shadow')}, 1.0),
                     ({'inner-shadow': self.full('inner-shadow', angle=-60, distance=4, choke=30, size=7, color='#200040', blend='normal', opacity=100)}, 0.3),
                     ({'inner-glow': self.full('inner-glow')}, 1.0),
                     ({'inner-glow': self.full('inner-glow', size=9, choke=50, color='#00ffff', blend='normal')}, 0.0),
                     ({'inner-glow': self.full('inner-glow', size=3), 'inner-shadow': self.full('inner-shadow', size=2, distance=2)}, 1.0)]
            for fx, fill in cases:
                self.set_fx(fx, fill=fill)
                self.check(fx, shape, below, fill=fill, max_abs=4, mean_abs=0.5, msg=f'{name} {sorted(fx)} fill {fill}')
            self.set_fx({}, fill=1)
        self.assertNoBrowserErrors()

    def test_06_every_effect_at_once_on_a_masked_layer(self):
        shape, below = soft_shape(), np.broadcast_to(np.array((60, 40, 30, 255), np.uint8), (64, 64, 4)).copy()
        shape = np.maximum(shape, crisp_shape())
        self.load(shape, below)
        mask = np.zeros((64, 64, 4), np.uint8)
        mask[..., :3] = 255
        mask[..., 3] = np.clip(np.arange(64)[None, :] * 6, 0, 255)   # a ramp: the left edge hidden, the right shown
        src = self.browser.push_pixels(mask)
        self.browser.eval(f"(() => {{ const m = Ed.util.canvas(64, 64); Ed.util.write(m, {src}); Ed.layers.set(1, {{mask: m}}, {{history: false}}); return true; }})()")
        masked = shape.copy()
        masked[..., 3] = np.floor(shape[..., 3].astype(np.float64) * mask[..., 3] / 255 + .5).astype(np.uint8)
        fx = {'drop-shadow': self.full('drop-shadow', distance=4, size=5), 'outer-glow': self.full('outer-glow', size=4, opacity=60),
              'gradient-overlay': self.full('gradient-overlay', opacity=40), 'color-overlay': self.full('color-overlay', opacity=30, color='#0040ff'),
              'inner-glow': self.full('inner-glow', size=3), 'inner-shadow': self.full('inner-shadow', size=3, distance=3),
              'stroke': self.full('stroke', size=2, position='center', color='#ffffff')}
        self.set_fx(fx, fill=0.8)
        ref = {t: dict(p, _reference=gradient_reference(p, masked)) if t == 'gradient-overlay' else p for t, p in fx.items()}
        self.check(ref, masked, below, fill=0.8, max_abs=5, mean_abs=0.5, msg='all effects on a masked layer')
        # switched-off effects are kept but not drawn
        fx['inner-glow']['enabled'] = False
        fx['stroke']['enabled'] = False
        self.set_fx(fx, fill=0.8)
        self.check(ref | {'inner-glow': fx['inner-glow'], 'stroke': fx['stroke']}, masked, below, fill=0.8, max_abs=5, mean_abs=0.5, msg='two effects switched off')
        self.assertNoBrowserErrors()

    def test_07_layers_in_other_blend_modes_and_clipping_bases_get_the_effects_on_top(self):
        shape = crisp_shape()
        below = np.broadcast_to(np.array((200, 180, 60, 255), np.uint8), (64, 64, 4)).copy()
        self.load(shape, below)
        b = self.browser
        fx = {'color-overlay': self.full('color-overlay', color='#40a0ff', opacity=70), 'stroke': self.full('stroke', size=2, color='#000000')}
        b.eval("Ed.layers.set(1, {blend: 'multiply'}, {history: false}); true")
        self.set_fx(fx)
        B = premul(below)
        a = unit(shape)[..., 3]
        D = over_premul(B, unit(shape)[..., :3], a, 'multiply')
        D = over_premul(D, color('#40a0ff'), 0.7 * a)
        D = shrink(over_premul(grow(D), color('#000000'), np.maximum(0, dilate(grow(a), 2) - grow(a))))
        got = premultiplied(b.editor_pixels())
        self.assertPixelsClose(got, to_bytes(D), max_abs=2, msg='a Multiply layer: its effects are drawn over it')
        # A clipping base keeps its outside stroke around the whole group and its overlay under the clipped layer.
        b.eval("Ed.layers.set(1, {blend: 'normal'}, {history: false}); const c = Ed.util.canvas(64, 64), g = c.getContext('2d'); g.fillStyle = '#ffffff'; g.fillRect(0, 0, 20, 64);"
               "Ed.layers.add({canvas: c, clip: true, name: 'Clipped', history: false}); Ed.flush(); true")
        got = premultiplied(b.editor_pixels())
        self.assertEqual(tuple(got[20, 15]), (255, 255, 255, 255), 'the clipped layer shows inside the base')
        want = tuple(int(v) for v in np.floor(np.array(rgba('#40a0ff')[:3]) * .7 + np.array((40, 120, 220)) * .3 + .5))
        self.assertLessEqual(max(abs(int(g) - w) for g, w in zip(got[20, 25][:3], want)), 1, 'the overlay shows where the clipped layer does not reach')
        self.assertEqual(tuple(got[20, 31]), (0, 0, 0, 255), 'the outside stroke surrounds the group')
        self.assertEqual(tuple(got[5, 5]), (200, 180, 60, 255))
        self.assertNoBrowserErrors()

    # ----- the dialog, menus and commands with real input ------------------------------------------------------------------
    def labels(self):
        return [s['label'] for s in self.browser.eval('Ed.history.list()')]

    def menu(self, path):
        """Click through the menubar: Layer > Layer Style > item."""
        b = self.browser
        parts = path.split('/')
        b.click_selector(f'#edMenubar [data-menu="{parts[0]}"]')
        for i in range(1, len(parts) - 1):
            sub = parts[i]
            b.wait_for(f"[...document.querySelectorAll('.ed-menu .ed-menu-item[aria-haspopup]')].some(e => e.textContent.includes({json.dumps(sub)}))")
            x, y = b.eval(f"(() => {{ const e = [...document.querySelectorAll('.ed-menu .ed-menu-item[aria-haspopup]')].find(e => e.textContent.includes({json.dumps(sub)})); const r = e.getBoundingClientRect(); return [r.left + 30, r.top + r.height / 2]; }})()")
            b.click(x, y)
        sel = f'.ed-menu .ed-menu-item[data-path="{path}"]'
        b.wait_for(f"!!document.querySelector({json.dumps(sel)})")
        b.click_selector(sel)

    def wait_dialog(self, open_=True):
        self.browser.wait_for(f"!!document.querySelector('{DIALOG}') === {'true' if open_ else 'false'}")

    def type_into(self, name, value):
        b = self.browser
        b.click_selector(f'{DIALOG} input[name="{name}"][type=number]')
        b.key('Ctrl+A')
        b.type_text(str(value))
        b.key('Tab')

    def wait_close(self, want, max_abs=2, timeout=10):
        """Wait until the composite matches want (premultiplied bytes)."""
        def ok(px): return int(np.abs(premultiplied(px).astype(int) - want.astype(int)).max()) <= max_abs
        self.browser.wait_for_pixels(ok, timeout=timeout)

    def test_10_dialog_live_preview_ok_undo_and_cancel(self):
        shape = soft_shape()
        below = np.broadcast_to(np.array((30, 50, 80, 255), np.uint8), (64, 64, 4)).copy()
        self.load(shape, below)
        b = self.browser
        b.eval("Ed.panels.show('layers'); true")
        b.settle(3)
        plain = premultiplied(b.editor_pixels())
        # double-click the styled layer's row (its thumbnail) opens Blending Options
        b.wait_for("!!document.querySelector('#edPanel-layers .ed-layer[data-index=\"1\"] .ed-thumb')")
        x, y = b.center_of('#edPanel-layers .ed-layer[data-index="1"] .ed-thumb')
        b.double_click(x, y)
        self.wait_dialog()
        self.assertEqual(b.eval(f"document.querySelector('{DIALOG} .ed-fx-item.selected').dataset.fx"), 'blending')
        # clicking an effect's name switches it on and shows its settings
        b.click_selector(f'{DIALOG} .ed-fx-item[data-fx="stroke"] .ed-fx-name')
        b.wait_for(f"document.querySelector('{DIALOG} input[name=\"fx-stroke\"]').checked && !!document.querySelector('{DIALOG} input[name=\"size\"]')")
        self.type_into('size', 4)
        b.set_value(f'{DIALOG} select[name="position"]', 'center')
        b.set_value(f'{DIALOG} input[name="color"]', '#ffd040')
        stroke = self.full('stroke', size=4, position='center', color='#ffd040')
        self.wait_close(to_bytes(styled(shape, {'stroke': stroke}, below)))
        self.assertEqual(self.labels(), ['Open'], 'the live preview is not history')
        # the Preview box shows the layer without the new style and back
        b.click_selector(f'{DIALOG} input[name="preview"]')
        self.wait_close(plain, 0)
        b.click_selector(f'{DIALOG} input[name="preview"]')
        # a checkbox switches Drop Shadow on with Photoshop's defaults
        b.click_selector(f'{DIALOG} input[name="fx-drop-shadow"]')
        both = {'stroke': stroke, 'drop-shadow': self.full('drop-shadow')}
        self.wait_close(to_bytes(styled(shape, both, below)), max_abs=4)
        self.assertEqual(b.eval(f"document.querySelector('{DIALOG} .ed-fx-item.selected').dataset.fx"), 'drop-shadow')
        b.click_selector(f'{DIALOG} button.primary')
        self.wait_dialog(False)
        self.assertEqual(self.labels(), ['Open', 'Layer Style'])
        saved = b.eval("Ed.doc.layers[1].effects")
        self.assertEqual(sorted(saved), ['drop-shadow', 'stroke'])
        self.assertEqual((saved['stroke']['size'], saved['stroke']['position'], saved['stroke']['color'], saved['stroke']['enabled']), (4, 'center', '#ffd040', True))
        styled_px = premultiplied(b.editor_pixels())
        self.assertPixelsClose(styled_px, to_bytes(styled(shape, both, below)), max_abs=4, msg='after OK')
        b.key('Ctrl+Z')
        self.wait_close(plain, 0)
        self.assertEqual(b.eval("Ed.doc.layers[1].effects"), {})
        b.key('Ctrl+Shift+Z')
        self.wait_close(styled_px, 0)
        # From the menu: Outer Glow... opens with Outer Glow on and selected; Cancel (Escape) puts everything back.
        self.menu('Layer/Layer Style/Outer Glow...')
        self.wait_dialog()
        self.assertEqual(b.eval(f"document.querySelector('{DIALOG} .ed-fx-item.selected').dataset.fx"), 'outer-glow')
        self.type_into('size', 12)
        b.click_selector(f'{DIALOG} .ed-fx-item[data-fx="blending"] .ed-fx-name')
        self.type_into('fill', 30)
        b.wait_for_pixels(lambda px: int(np.abs(premultiplied(px).astype(int) - styled_px.astype(int)).max()) > 20)
        b.key('Escape')
        self.wait_dialog(False)
        self.wait_close(styled_px, 0)
        self.assertEqual(self.labels(), ['Open', 'Layer Style'], 'Cancel adds no state')
        self.assertEqual(b.eval("Ed.doc.layers[1].fill"), 1)
        self.assertEqual(sorted(b.eval("Ed.doc.layers[1].effects")), ['drop-shadow', 'stroke'])
        # Unchecking an effect keeps its settings (switched off) and Reset to Default puts the defaults back.
        self.menu('Layer/Layer Style/Stroke...')
        self.wait_dialog()
        b.click_selector(f'{DIALOG} button.ed-fx-reset')
        b.wait_for(f"document.querySelector('{DIALOG} input[name=\"size\"][type=number]').value === '3'")
        b.click_selector(f'{DIALOG} input[name="fx-drop-shadow"]')
        b.click_selector(f'{DIALOG} button.primary')
        self.wait_dialog(False)
        fx = b.eval("Ed.doc.layers[1].effects")
        self.assertEqual((fx['drop-shadow']['enabled'], fx['stroke']['size'], fx['stroke']['position']), (False, 3, 'outside'))
        self.assertEqual(self.labels(), ['Open', 'Layer Style', 'Layer Style'])
        self.wait_close(to_bytes(styled(shape, {'stroke': self.full('stroke')}, below)))
        self.assertNoBrowserErrors()

    def test_11_copy_paste_clear_and_hide_all_from_the_menu(self):
        shape = crisp_shape()
        other = np.zeros_like(shape)
        other[40:60, 30:60] = (120, 200, 90, 255)
        self.load(shape, other)
        b = self.browser
        plain = premultiplied(b.editor_pixels())
        fx = {'stroke': self.full('stroke', size=2, color='#ffffff'), 'color-overlay': self.full('color-overlay', opacity=50)}
        b.eval(f"Ed.layers.set(1, {{effects: {json.dumps(fx)}, fill: 0.5}}, {{history: 'Layer Style'}}); true")
        b.wait_for("!!document.querySelector('#edPanel-layers .ed-layer[data-index=\"1\"] .ed-fx-badge')")
        self.assertEqual(b.eval("document.querySelector('#edPanel-layers .ed-layer[data-index=\"1\"] .ed-fx-badge').dataset.effects"), 'stroke color-overlay')
        self.assertFalse(b.eval("!!document.querySelector('#edPanel-layers .ed-layer[data-index=\"0\"] .ed-fx-badge')"))
        self.menu('Layer/Layer Style/Copy Layer Style')
        b.click_selector('#edPanel-layers .ed-layer[data-index="0"] .ed-thumb')
        b.wait_for("Ed.doc.activeIndex === 0")
        self.menu('Layer/Layer Style/Paste Layer Style')
        b.wait_for("Object.keys(Ed.doc.layers[0].effects).length === 2")
        self.assertEqual(b.eval("Ed.doc.layers[0].fill"), 0.5)
        self.assertEqual(self.labels(), ['Open', 'Layer Style', 'Paste Layer Style'])
        both = premultiplied(b.editor_pixels())
        # Hide All Effects / Show All Effects
        self.menu('Layer/Layer Style/Hide All Effects')
        b.wait_for("Ed.history.list().length === 4")
        self.assertEqual(self.labels()[-1], 'Hide All Effects')
        self.assertFalse(b.eval("!!document.querySelector('#edPanel-layers .ed-fx-badge')"), 'no fx badge while hidden')
        self.assertEqual(b.eval("Ed.menu.tree().find(m => m.name === 'Layer').children.find(c => c.name === 'Layer Style').children.find(c => c.path.endsWith('Hide All Effects')).label"), 'Show All Effects')
        self.menu('Layer/Layer Style/Hide All Effects')   # now labelled Show All Effects
        b.wait_for("Ed.history.list().length === 5")
        self.wait_close(both, 0)
        # Clear Layer Style on the active layer, then undo everything back
        self.menu('Layer/Layer Style/Clear Layer Style')
        b.wait_for("Object.keys(Ed.doc.layers[0].effects).length === 0")
        self.assertEqual(self.labels()[-1], 'Clear Layer Style')
        for _ in range(4): b.key('Ctrl+Z')
        b.wait_for("Ed.history.index === 1")
        self.assertEqual(b.eval("Object.keys(Ed.doc.layers[0].effects).length"), 0)
        b.key('Ctrl+Z')
        self.wait_close(plain, 0)
        self.assertNoBrowserErrors()

    def test_12_painting_on_a_styled_layer_redraws_its_effects(self):
        shape = crisp_shape()
        below = np.broadcast_to(np.array((240, 240, 240, 255), np.uint8), (64, 64, 4)).copy()
        self.load(shape, below)
        b = self.browser
        fx = {'stroke': self.full('stroke', size=2, color='#000000'), 'drop-shadow': self.full('drop-shadow', size=3, distance=3)}
        b.eval(f"Ed.layers.set(1, {{effects: {json.dumps(fx)}}}); true")
        b.click_selector('[data-tool="brush"]')
        b.set_value('#paintColor', '#ff00ff')
        b.set_value('#brushSize', 5)
        b.set_value('#brushOpacity', 100)
        b.drag_image([(36.5, 8.5), (56.5, 8.5), (56.5, 20.5)], center=False)
        b.wait_for("Ed.history.list().length === 3")
        layer = b.editor_pixels(1)
        self.check(fx, layer, below, max_abs=4, mean_abs=0.5, msg='after a brush stroke')
        b.key('Ctrl+Z')
        b.wait_for("Ed.history.index === 1")
        self.check(fx, shape, below, max_abs=4, mean_abs=0.5, msg='after undo')
        self.assertNoBrowserErrors()

    def test_13_a_styled_document_is_saved_and_reopens_with_its_layers(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.wait_for("Ed.doc && Ed.doc.layers.length === 1")
        fx = {'stroke': self.full('stroke', size=2, color='#101010'), 'outer-glow': self.full('outer-glow', size=6),
              'gradient-overlay': self.full('gradient-overlay', opacity=50), 'inner-shadow': self.full('inner-shadow', size=3)}
        b.eval(f"Ed.layers.set(0, {{effects: {json.dumps(fx)}}}); true")
        b.wait_for("S.dirty")
        want = premultiplied(b.editor_pixels())
        b.key('Ctrl+S')
        b.wait_for("!S.dirty && !S.busy && S.asset.revision !== 'original'", timeout=30)
        b.reload()
        b.select_asset('edit-sprite.png')
        got_fx = b.eval("Ed.doc.layers[0].effects")
        self.assertEqual(sorted(got_fx), sorted(fx), 'the effects come back (not a flat image)')
        self.assertEqual(b.eval("Ed.doc.layers.length"), 1)
        self.assertNotEqual(premultiplied(b.editor_pixels(0)).tolist(), want.tolist(), 'the layer pixels are the plain sprite, the effects are live')
        self.assertPixelsClose(premultiplied(b.editor_pixels()), want, max_abs=1, msg='the reopened composite')
        self.assertEqual(self.labels(), ['Open'])
        self.assertNoBrowserErrors()

    def test_14_speed_on_a_2048_sheet(self):
        """A 2048 x 2048 transparent sheet of icons with five effects: a full redraw and the frames of a brush stroke."""
        b = self.browser
        sheet = np.zeros((2048, 2048, 4), np.uint8)
        yy, xx = np.mgrid[0:128, 0:128]
        disc = ((xx - 64) ** 2 + (yy - 64) ** 2 <= 50 ** 2)
        for i in range(0, 2048, 256):
            for j in range(0, 2048, 256):
                sheet[i + 40:i + 168, j + 40:j + 168][disc] = (200, 120, 40, 255)
        self.load(sheet)
        fx = {'stroke': self.full('stroke', size=4), 'outer-glow': self.full('outer-glow', size=10, spread=20), 'drop-shadow': self.full('drop-shadow', size=8, distance=6),
              'color-overlay': self.full('color-overlay', opacity=40), 'gradient-overlay': self.full('gradient-overlay', opacity=30)}
        b.eval(f"Ed.layers.set(0, {{effects: {json.dumps(fx)}}}); true")
        full = b.eval("(() => { Ed.flush(); const t = []; for (let k = 0; k < 3; k++) { const a = performance.now(); Ed.update(); Ed.flush(); t.push(performance.now() - a); } return t; })()")
        b.eval("window.__frames = []; window.__flush = Ed.flush; Ed.flush = () => { const a = performance.now(); __flush(); __frames.push(performance.now() - a); }; true")
        try:
            b.click_selector('[data-tool="brush"]')
            b.set_value('#brushSize', 20)
            b.eval("Ed.view.fit(); true")
            b.settle(3)
            b.eval("__frames.length = 0; true")
            start = time.perf_counter()
            b.drag_image([(100.5, 100.5), (900.5, 300.5), (1500.5, 1200.5)], center=False, steps_between=12)
            b.wait_for("Ed.history.list().length === 3", timeout=30)
            elapsed = time.perf_counter() - start
            frames = b.eval("__frames.slice()")
        finally:
            b.eval("Ed.flush = __flush; true")
        stroke = [f for f in frames if f > 0.5]
        median = sorted(stroke)[len(stroke) // 2] if stroke else 0
        self.report.append(f'2048 sheet, 5 effects: full redraw {[round(t) for t in full]} ms; brush frames {len(stroke)}, median {median:.1f} ms, max {max(stroke or [0]):.1f} ms, drag {elapsed:.2f} s')
        self.assertLess(min(full), 2500, f'a full redraw of the styled 2048 sheet: {full}')
        self.assertLess(median, 150, f'brush frames on the styled sheet: median {median} ms')
        self.assertNoBrowserErrors()

    def test_20_dialog_layout_at_1366_and_1920(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h)
                b.settle(3)
                before = b.eval("Ed.doc.layers[0].effects")
                self.menu('Layer/Layer Style/Blending Options...')
                self.wait_dialog()
                for fx in ['blending', 'stroke', 'inner-shadow', 'inner-glow', 'color-overlay', 'gradient-overlay', 'outer-glow', 'drop-shadow']:
                    b.click_selector(f'{DIALOG} .ed-fx-item[data-fx="{fx}"] .ed-fx-name')
                    b.wait_for(f"document.querySelector('{DIALOG} .ed-fx-item.selected').dataset.fx === '{fx}'")
                    box = b.eval(f"""(() => {{ const d = document.querySelector('{DIALOG}'), r = d.getBoundingClientRect(), bad = [];
                        for (const e of d.querySelectorAll('.ed-field, .ed-fx-item, button, select, input')) {{ const q = e.getBoundingClientRect();
                          if (q.width && (q.left < r.left - 1 || q.right > r.right + 1 || q.bottom > r.bottom + 1)) bad.push(e.className || e.tagName); }}
                        const p = d.querySelector('.ed-fx-pane');
                        return {{rect: [r.left, r.top, r.right, r.bottom], bad, spill: p.scrollWidth > p.clientWidth + 1 || d.scrollWidth > d.clientWidth + 1 || d.scrollHeight > d.clientHeight + 1}}; }})()""")
                    l, t, r, btm = box['rect']
                    self.assertTrue(l >= 0 and t >= 0 and r <= w and btm <= h, f'the dialog fits {w}x{h} on {fx}: {box["rect"]}')
                    self.assertEqual(box['bad'], [], f'controls inside the dialog at {w}x{h} on {fx}')
                    self.assertFalse(box['spill'], f'nothing scrolls sideways or spills at {w}x{h} on {fx}')
                    if fx in ('blending', 'drop-shadow', 'gradient-overlay', 'stroke'): b.screenshot(f'styles-dialog-{fx}-{w}x{h}.png')
                b.key('Escape')
                self.wait_dialog(False)
                self.assertEqual(b.eval("Ed.doc.layers[0].effects"), before, 'Cancel puts the style back as it was')
        finally:
            b.set_viewport(*self.viewport)
        self.assertNoBrowserErrors()

    def test_08_layer_opacity_fades_the_layer_and_its_effects_as_one(self):
        shape = soft_shape()
        below = np.broadcast_to(np.array((240, 200, 60, 255), np.uint8), (64, 64, 4)).copy()
        self.load(shape, below)
        fx = {'drop-shadow': self.full('drop-shadow', size=4, distance=4, opacity=100), 'stroke': self.full('stroke', size=3, color='#000080'),
              'color-overlay': self.full('color-overlay', opacity=50)}
        self.set_fx(fx)
        self.browser.eval("Ed.layers.set(1, {opacity: 0.5}, {history: false}); Ed.flush(); true")
        full_strength, B = styled(shape, fx, below), premul(below)
        got = premultiplied(self.browser.editor_pixels())
        self.assertPixelsClose(got, to_bytes(B + .5 * (full_strength - B)), max_abs=4, mean_abs=0.5, msg='opacity 50%')
        self.browser.eval("Ed.layers.set(1, {opacity: 1, visible: false}, {history: false}); Ed.flush(); true")
        self.assertPixelsClose(premultiplied(self.browser.editor_pixels()), to_bytes(B), msg='a hidden layer hides its effects')
        self.assertNoBrowserErrors()

    def test_15_duplicate_rasterize_and_merge_keep_the_picture(self):
        shape = crisp_shape()
        below = np.broadcast_to(np.array((30, 30, 30, 255), np.uint8), (64, 64, 4)).copy()
        self.load(shape, below)
        b = self.browser
        fx = {'outer-glow': self.full('outer-glow', size=6, opacity=100, color='#ffff00', blend='normal'), 'stroke': self.full('stroke', size=2, color='#ffffff'),
              'gradient-overlay': self.full('gradient-overlay', opacity=60)}
        b.eval(f"Ed.layers.set(1, {{effects: {json.dumps(fx)}}}, {{history: 'Layer Style'}}); true")
        want = premultiplied(b.editor_pixels())
        b.eval("Ed.layers.duplicate(1); Ed.layers.set(2, {visible: false}); true")
        self.assertEqual(sorted(b.eval("Ed.doc.layers[2].effects")), sorted(fx), 'a duplicate keeps the style')
        self.assertPixelsClose(premultiplied(b.editor_pixels()), want)
        ran = []
        if b.eval("!!Ed.menu.get('Layer/Rasterize/Layer Style')"):   # layers.js
            b.eval("Ed.layers.setActive(1); true")
            self.assertTrue(b.eval("Ed.menu.run('Layer/Rasterize/Layer Style')"))
            b.wait_for("Object.keys(Ed.doc.layers[1].effects).length === 0")
            self.assertPixelsClose(premultiplied(b.editor_pixels()), want, max_abs=2, msg='Rasterize Layer Style keeps the picture')
            b.key('Ctrl+Z')
            b.wait_for("Object.keys(Ed.doc.layers[1].effects).length === 3")
            self.assertPixelsClose(premultiplied(b.editor_pixels()), want, msg='undo brings the live style back')
            ran.append('rasterize')
        if not b.eval("!!Ed.menu.get('Layer/Merge Down')"): self.skipTest(f'layers.js has no Merge Down (ran {ran})')
        self.assertTrue(b.eval("Ed.layers.setActive(1); Ed.menu.run('Layer/Merge Down')"))
        b.wait_for("Ed.doc.layers.length === 2")
        self.assertPixelsClose(premultiplied(b.editor_pixels()), want, max_abs=2, msg='Merge Down keeps the picture')
        self.assertNoBrowserErrors()

    def test_16_commands_with_params_skip_the_dialog(self):
        shape = crisp_shape()
        self.load(shape)
        b = self.browser
        self.assertTrue(b.eval("Ed.menu.run('Layer/Layer Style/Stroke...', {size: 2, color: '#ff0000'})"))
        self.assertFalse(b.eval(f"!!document.querySelector('{DIALOG}')"))
        self.assertEqual(self.labels(), ['Open', 'Layer Style'])
        self.check({'stroke': self.full('stroke', size=2, color='#ff0000')}, shape, msg='Stroke... with params')
        self.assertTrue(b.eval("Ed.menu.run('Layer/Layer Style/Blending Options...', {fill: 0})"))
        self.assertEqual(b.eval("Ed.doc.layers[0].fill"), 0)
        self.check({'stroke': self.full('stroke', size=2, color='#ff0000')}, shape, fill=0.0, msg='Fill 0 keeps the stroke')
        # the API other modules use
        self.assertEqual([t['type'] for t in b.eval("Ed.layerStyle.types()")], ['stroke', 'inner-shadow', 'inner-glow', 'color-overlay', 'gradient-overlay', 'outer-glow', 'drop-shadow'])
        self.assertTrue(b.eval("Ed.layerStyle.has(0)"))
        self.assertTrue(b.eval("Ed.layerStyle.copy() && Ed.layerStyle.clear()"))
        self.assertFalse(b.eval("Ed.layerStyle.has(0)"))
        self.assertTrue(b.eval("Ed.layerStyle.paste([0])"))
        self.assertEqual(self.labels()[-2:], ['Clear Layer Style', 'Paste Layer Style'])
        b.eval("window.__open = Ed.layerStyle.open(0, 'drop-shadow'); true")
        self.wait_dialog()
        self.assertEqual(b.eval(f"document.querySelector('{DIALOG} .ed-fx-item.selected').dataset.fx"), 'drop-shadow')
        b.click_selector(f'{DIALOG} button.primary')
        self.assertTrue(b.eval("__open"))
        self.assertTrue(b.eval("Ed.doc.layers[0].effects['drop-shadow'].enabled"))
        # a locked layer refuses, an adjustment layer has no style
        b.eval("Ed.layers.set(0, {locks: {all: true}}, {history: false}); true")
        self.assertFalse(b.eval("Ed.menu.list().find(m => m.path === 'Layer/Layer Style/Blending Options...').enabled"))
        self.assertFalse(b.eval("Ed.layerStyle.open(0)"))
        self.assertNoBrowserErrors()

    def test_21_gradient_field_in_the_dialog(self):
        shape = crisp_shape()
        self.load(shape)
        b = self.browser
        if b.eval("!!Ed.fields.get('gradient')"):
            self.menu('Layer/Layer Style/Gradient Overlay...')
            self.wait_dialog()
            b.click_selector(f'{DIALOG} .fill-grad-more')
            b.wait_for("!!document.querySelector('.fill-pop .fill-swatch[data-preset=\"Neon\"]')")
            b.click_selector('.fill-pop .fill-swatch[data-preset="Neon"]')
            neon = {'stops': [{'at': 0, 'color': '#ff00cc', 'mid': .5}, {'at': 1, 'color': '#3333ff', 'mid': .5}], 'alpha': [{'at': 0, 'a': 1, 'mid': .5}, {'at': 1, 'a': 1, 'mid': .5}]}
            p = self.full('gradient-overlay', gradient=neon)
            self.wait_close(to_bytes(styled(shape, {'gradient-overlay': dict(p, _reference=gradient_reference(p, shape))})))
            b.click_selector(f'{DIALOG} input[name="reverse"]')
            p = dict(p, reverse=True)
            self.wait_close(to_bytes(styled(shape, {'gradient-overlay': dict(p, _reference=gradient_reference(p, shape))})))
            b.click_selector(f'{DIALOG} button.primary')
            self.wait_dialog(False)
            g = b.eval("Ed.doc.layers[0].effects['gradient-overlay'].gradient")
            self.assertEqual([s['color'] for s in g['stops']], ['#ff00cc', '#3333ff'])
            b.key('Ctrl+Z')
        # without fill.js: two colors
        b.eval("window.__getField = Ed.fields.get; Ed.fields.get = t => t === 'gradient' ? undefined : __getField(t); true")
        try:
            self.menu('Layer/Layer Style/Gradient Overlay...')
            self.wait_dialog()
            b.set_value(f'{DIALOG} input[name="gradient-start"]', '#ff0000')
            b.set_value(f'{DIALOG} input[name="gradient-end"]', '#00ff00')
            rg = {'stops': [{'at': 0, 'color': '#ff0000', 'mid': .5}, {'at': 1, 'color': '#00ff00', 'mid': .5}], 'alpha': [{'at': 0, 'a': 1, 'mid': .5}, {'at': 1, 'a': 1, 'mid': .5}]}
            p = self.full('gradient-overlay', gradient=rg)
            self.wait_close(to_bytes(styled(shape, {'gradient-overlay': dict(p, _reference=gradient_reference(p, shape))})))
            b.key('Escape')
            self.wait_dialog(False)
        finally:
            b.eval("Ed.fields.get = __getField; true")
        self.assertNoBrowserErrors()
