"""Image > Adjustments, the Auto commands, adjustment layers and the Properties panel (src/web/editor/adjust.js), driven
with real menu clicks, shortcuts, typing and dragging in the dialogs, in headless Edge. Every result is checked against an
independent numpy implementation of the formula written here (colorsys for the HSL conversions, a tridiagonal solve for
the natural cubic spline, FFmpeg's selectivecolor arithmetic, the W3C SetLum/ClipColor math), never against the
module's own code.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_adjust*.py" -v
"""
import colorsys
import json
import math
import numpy as np
from harness import EditorTestCase, image_array

W, H = 128, 96


# ----- the test pictures ----------------------------------------------------------------------------------------------------
def picture(seed=7, alpha=None):
    """Opaque: a gray ramp, pure and mixed colours, then random colours (every hue and tone)."""
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, (H, W, 4)).astype(np.uint8)
    a[0, :, :3] = np.repeat((np.arange(W) * 255 // (W - 1))[:, None], 3, 1)
    pure = [(255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (0, 0, 255), (255, 0, 255), (0, 0, 0), (255, 255, 255),
            (128, 128, 128), (200, 60, 40), (40, 200, 90), (60, 90, 220), (230, 180, 140), (12, 30, 8), (250, 240, 230), (127, 127, 128)]
    for i, c in enumerate(pure): a[1, i * 8:(i + 1) * 8, :3] = c
    a[..., 3] = 255 if alpha is None else alpha
    return a


def dull():
    """A dull, colour-cast picture so the Auto commands have something to do."""
    img = picture(11)
    img[..., 0] = (img[..., 0] * .55 + 60).astype(np.uint8)
    img[..., 1] = (img[..., 1] * .6 + 40).astype(np.uint8)
    img[..., 2] = (img[..., 2] * .5 + 20).astype(np.uint8)
    img[40:60, 30:90, :3] = (150, 140, 110)            # a near-gray block for Snap Neutral Midtones
    return img


def r8(v):
    return np.clip(np.floor(np.asarray(v, np.float64) + .5), 0, 255).astype(np.uint8)


def apply_lut(img, luts):
    out = img.copy()
    for c in range(3): out[..., c] = luts[c][img[..., c]]
    return out


def per_pixel(img, fn):
    """fn(r, g, b) -> (r, g, b) floats or ints, for every pixel (alpha kept)."""
    out = img.copy()
    flat, res = img.reshape(-1, 4), out.reshape(-1, 4)
    cache = {}
    for i in range(flat.shape[0]):
        k = (int(flat[i, 0]), int(flat[i, 1]), int(flat[i, 2]))
        if k not in cache: cache[k] = fn(*k)
        res[i, :3] = cache[k]
    return out


def luma601(r, g, b):
    return .299 * r + .587 * g + .114 * b


# ----- references --------------------------------------------------------------------------------------------------------------
def levels_fn(p):
    ib, iw, g, ob, ow = p['inBlack'], p['inWhite'], p['gamma'], p['outBlack'], p['outWhite']
    def f(v):
        t = np.clip((np.asarray(v, np.float64) - ib) / (iw - ib), 0, 1)
        return ob + t ** (1 / g) * (ow - ob)
    return f


def levels_luts(p):
    v, m = np.arange(256), levels_fn(p['rgb'])
    return [r8(m(levels_fn(p[c])(v))) for c in ('red', 'green', 'blue')]


def natural_spline(points):
    """Natural cubic spline (second derivative 0 at both ends) through the points, flat outside them."""
    pts = sorted((float(x), float(y)) for x, y in points)
    x, y = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    n = len(x)
    M = np.zeros(n)
    if n > 2:
        h = np.diff(x)
        A, rhs = np.zeros((n - 2, n - 2)), np.zeros(n - 2)
        for i in range(1, n - 1):
            if i > 1: A[i - 1, i - 2] = h[i - 1]
            A[i - 1, i - 1] = 2 * (h[i - 1] + h[i])
            if i < n - 2: A[i - 1, i] = h[i]
            rhs[i - 1] = 6 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
        M[1:-1] = np.linalg.solve(A, rhs)
    def f(v):
        v = np.asarray(v, np.float64)
        out = np.empty_like(v)
        for j, q in np.ndenumerate(v):
            if q <= x[0]: out[j] = y[0]; continue
            if q >= x[-1]: out[j] = y[-1]; continue
            i = int(np.searchsorted(x, q, side='left')) - 1
            i = max(0, min(n - 2, i))
            hh = x[i + 1] - x[i]; a = (x[i + 1] - q) / hh; b = (q - x[i]) / hh
            out[j] = a * y[i] + b * y[i + 1] + ((a ** 3 - a) * M[i] + (b ** 3 - b) * M[i + 1]) * hh * hh / 6
        return out
    return f


def curves_luts(p):
    v, m = np.arange(256), natural_spline(p['rgb'])
    return [r8(m(np.clip(natural_spline(p[c])(v), 0, 255))) for c in ('red', 'green', 'blue')]


def bc_lut(p):
    v = np.arange(256, dtype=np.float64)
    b, c = p['brightness'], p['contrast']
    if p['legacy']:
        if c >= 100: x = np.where(v >= 128, 255.0, 0.0)
        else: x = (v - 127.5) * (100 / (100 - c) if c >= 0 else (100 + c) / 100) + 127.5
        return np.clip(np.floor(x + b + 1e-7), 0, 255).astype(np.uint8)
    x = v
    if b: x = np.clip(natural_spline([(0, 0), (128, 128 + b * .5), (255, 255)])(x), 0, 255)
    if c:
        k = c * .32
        x = np.clip(natural_spline([(0, 0), (64, 64 - k), (192, 192 + k), (255, 255)])(x), 0, 255)
    return r8(x)


def srgb_to_linear(c):
    c = np.asarray(c, np.float64) / 255
    return np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)


def linear_to_srgb(l):
    return np.where(l <= .0031308, 12.92 * l, 1.055 * np.power(np.maximum(l, 0), 1 / 2.4) - .055)


def exposure_lut(p):
    x = srgb_to_linear(np.arange(256)) * 2 ** p['exposure'] + p['offset']
    x = np.where(x <= 0, 0, np.power(np.maximum(x, 1e-300), 1 / p['gamma']))
    return r8(linear_to_srgb(np.minimum(1, x)) * 255)


def hue_deg(r, g, b):
    h, _, _ = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return h * 360


def scale_chroma(r, g, b, k):
    Y = luma601(r, g, b)
    if k > 1:
        for c in (r, g, b):
            e = c - Y
            if e > 0: k = min(k, (255 - Y) / e)
            elif e < 0: k = min(k, -Y / e)
    k = max(k, 0)
    return tuple(int(r8(Y + (c - Y) * k)) for c in (r, g, b))


def vibrance_px(p):
    v, s = p['vibrance'] / 100, p['saturation'] / 100
    def f(r, g, b):
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == mn: return (r, g, b)
        sat, k = (mx - mn) / 255, 1
        if v > 0:
            dh = ((hue_deg(r, g, b) - 25) % 360 + 540) % 360 - 180
            k = 1 + v * (1 - sat) ** 2 * (1 - .5 * math.exp(-dh * dh / 800))
        elif v < 0: k = 1 + v * (1 - .5 * sat)
        return scale_chroma(r, g, b, k * (1 + s))
    return f


HS_RANGES = {'reds': (-45, -15, 15, 45), 'yellows': (15, 45, 75, 105), 'greens': (75, 105, 135, 165), 'cyans': (135, 165, 195, 225),
             'blues': (195, 225, 255, 285), 'magentas': (255, 285, 315, 345)}


def range_weight(h, R):
    mid = (R[1] + R[2]) / 2
    d = ((h - mid) % 360 + 540) % 360 - 180
    a, b, c, e = (q - mid for q in R)
    if d < a or d > e: return 0
    if d < b: return (d - a) / (b - a)
    if d > c: return (e - d) / (e - c)
    return 1


def ps_saturate(c, inc):
    """The widely published reverse-engineered Photoshop Hue/Saturation saturation step."""
    mx, mn = max(c), min(c)
    delta = (mx - mn) / 255
    if delta <= 0 or not inc: return c
    value = (mx + mn) / 255
    L = value / 2
    S = delta / value if L < .5 else delta / (2 - value)
    if inc > 0:
        alpha = S if inc + S >= 1 else 1 - inc
        alpha = 1 / alpha - 1
        c = [x + (x - L * 255) * alpha for x in c]
    else:
        c = [L * 255 + (x - L * 255) * (1 + inc) for x in c]
    return [min(255, max(0, x)) for x in c]


def huesat_px(p):
    M = p['master']
    def f(r, g, b):
        mx, mn = max(r, g, b), min(r, g, b)
        h = hue_deg(r, g, b) if mx > mn else -1
        dh, ds, dl = M['hue'], M['saturation'], M['lightness']
        if h >= 0:
            for k, R in HS_RANGES.items():
                q = p.get(k)
                if not q: continue
                w = range_weight(h, q.get('range', R))
                dh += w * q['hue']; ds += w * q['saturation']; dl += w * q['lightness']
        if not dh and not ds and not dl: return (r, g, b)
        c = [float(r), float(g), float(b)]
        if dh and h >= 0:
            l = (mx + mn) / 510
            rr, gg, bb = colorsys.hls_to_rgb(((h + dh) % 360) / 360, l, colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[2])
            c = [rr * 255, gg * 255, bb * 255]
        if ds: c = ps_saturate(c, max(-100, min(100, ds)) / 100)
        if dl:
            l = max(-100, min(100, dl)) / 100
            c = [x + (255 - x) * l for x in c] if l > 0 else [x + x * l for x in c]
        return tuple(int(r8(x)) for x in c)
    return f


def colorize_px(z):
    def f(r, g, b):
        l = (max(r, g, b) + min(r, g, b)) / 510
        Lt = z['lightness'] / 100
        l = l + (1 - l) * Lt if Lt > 0 else l + l * Lt
        rr, gg, bb = colorsys.hls_to_rgb((z['hue'] % 360) / 360, l, z['saturation'] / 100)
        return tuple(int(r8(x * 255)) for x in (rr, gg, bb))
    return f


def color_balance_px(p):
    S, Mi, Hi = ([v / 100 for v in p[k]] for k in ('shadows', 'midtones', 'highlights'))
    cl = lambda v: min(1, max(0, v))
    def f(r, g, b):
        rgb = [r / 255, g / 255, b / 255]
        l = (max(rgb) + min(rgb)) / 2
        ws = cl((l - .333) / -.25 + .5) * .7
        wm = cl((l - .333) / .25 + .5) * cl((l + .333 - 1) / -.25 + .5) * .7
        wh = cl((l + .333 - 1) / .25 + .5) * .7
        n = [cl(rgb[k] + S[k] * ws + Mi[k] * wm + Hi[k] * wh) for k in range(3)]
        if p['preserveLuminosity']:
            hh, _, ss = colorsys.rgb_to_hls(*n)
            n = colorsys.hls_to_rgb(hh, l, ss)
        return tuple(int(r8(x * 255)) for x in n)
    return f


def black_white_px(p):
    w = {k: p[k] / 100 for k in ('reds', 'yellows', 'greens', 'cyans', 'blues', 'magentas')}
    def f(r, g, b):
        # gray = min + (max - mid) x the strongest channel's slider + (mid - min) x the slider of the two strongest's mix
        ch = sorted([(r, 'r'), (g, 'g'), (b, 'b')], key=lambda t: -t[0])
        (mx, a), (md, bname), (mn, _) = ch
        prim = {'r': 'reds', 'g': 'greens', 'b': 'blues'}[a]
        sec = {frozenset('rg'): 'yellows', frozenset('gb'): 'cyans', frozenset('rb'): 'magentas'}[frozenset(a + bname)]
        y = mn + (mx - md) * w[prim] + (md - mn) * w[sec]
        v = int(r8(y))
        if p['tint']:
            rr, gg, bb = colorsys.hls_to_rgb((p['tintHue'] % 360) / 360, v / 255, p['tintSaturation'] / 100)
            return tuple(int(r8(x * 255)) for x in (rr, gg, bb))
        return (v, v, v)
    return f


def set_lum(c, l):
    """W3C compositing: SetLum + ClipColor."""
    lum = lambda q: .3 * q[0] + .59 * q[1] + .11 * q[2]
    d = l - lum(c)
    c = [x + d for x in c]
    L, n, x = lum(c), min(c), max(c)
    if n < 0: c = [L + (q - L) * L / (L - n) for q in c]
    if x > 1: c = [L + (q - L) * (1 - L) / (x - L) for q in c]
    return c


def photo_filter_px(p):
    f_ = [int(p['color'][i:i + 2], 16) / 255 for i in (1, 3, 5)]
    dn = p['density'] / 100
    m = [1 - dn + dn * v for v in f_]
    def f(r, g, b):
        o = [r / 255, g / 255, b / 255]
        c = [o[k] * m[k] for k in range(3)]
        if p['preserveLuminosity']: c = set_lum(c, .3 * o[0] + .59 * o[1] + .11 * o[2])
        return tuple(int(r8(x * 255)) for x in c)
    return f


def mixer_px(p):
    rows = [p['gray']] * 3 if p['monochrome'] else [p['red'], p['green'], p['blue']]
    def f(r, g, b):
        return tuple(int(r8(r * q['r'] / 100 + g * q['g'] / 100 + b * q['b'] / 100 + q['c'] / 100 * 255)) for q in rows)
    return f


def posterize_lut(n):
    v = np.arange(256)
    return r8(np.floor(v * n / 256) * 255 / (n - 1))


def threshold_img(img, t):
    out = img.copy()
    y = r8(luma601(*(img[..., k].astype(np.float64) for k in range(3))))
    out[..., :3] = np.where(y >= t, 255, 0)[..., None]
    return out


def gradient_lut(stops, reverse=False):
    S = sorted((s['at'] / 100, [int(s['color'][i:i + 2], 16) for i in (1, 3, 5)]) for s in stops)
    out = np.zeros((256, 3))
    for v in range(256):
        t = 1 - v / 255 if reverse else v / 255
        if t <= S[0][0]: c = S[0][1]
        elif t >= S[-1][0]: c = S[-1][1]
        else:
            i = max(k for k in range(len(S) - 1) if S[k][0] <= t)
            (a0, ca), (a1, cb) = S[i], S[i + 1]
            u = (t - a0) / (a1 - a0)
            c = [ca[k] + (cb[k] - ca[k]) * u for k in range(3)]
        out[v] = c
    return r8(out)


def gradient_map_img(img, lut):
    y = r8(luma601(*(img[..., k].astype(np.float64) for k in range(3))))
    out = img.copy()
    out[..., :3] = lut[y]
    return out


def desaturate_img(img):
    out = img.copy()
    rgb = img[..., :3].astype(np.int32)
    out[..., :3] = r8((rgb.max(2) + rgb.min(2)) / 2)[..., None]
    return out


SC_KEYS = ['reds', 'yellows', 'greens', 'cyans', 'blues', 'magentas', 'whites', 'neutrals', 'blacks']


def selective_px(p):
    """FFmpeg vf_selectivecolor (8 bit), the reverse-engineered model of Photoshop's Selective Color."""
    rel = p['method'] == 'relative'
    act = [(k, [v / 100 for v in p[k]]) for k in SC_KEYS if any(p[k])]
    def comp(scale, value, adjust, k):
        res = (-1 - adjust) * k - adjust
        if rel: res *= 1 - value
        return int(np.rint(min(1 - value, max(-value, res)) * scale))
    def f(r, g, b):
        mx, mn = max(r, g, b), min(r, g, b)
        mid = r + g + b - mx - mn
        flags = {'reds': r == mx, 'cyans': r == mn, 'greens': g == mx, 'magentas': g == mn, 'blues': b == mx, 'yellows': b == mn,
                 'whites': r > 128 and g > 128 and b > 128, 'neutrals': (r or g or b) and not (r == 255 and g == 255 and b == 255),
                 'blacks': r < 128 and g < 128 and b < 128}
        ar = ag = ab = 0
        for k, (c, m, y, kk) in act:
            if not flags[k]: continue
            if k in ('reds', 'greens', 'blues'): sc = mx - mid
            elif k in ('cyans', 'magentas', 'yellows'): sc = mid - mn
            elif k == 'whites': sc = 2 * mn - 255
            elif k == 'blacks': sc = 255 - 2 * mx
            else: sc = (510 - (abs(2 * mx - 255) + abs(2 * mn - 255)) + 1) >> 1
            if sc <= 0: continue
            ar += comp(sc, r / 255, c, kk); ag += comp(sc, g / 255, m, kk); ab += comp(sc, b / 255, y, kk)
        return (min(255, max(0, r + ar)), min(255, max(0, g + ag)), min(255, max(0, b + ab)))
    return f


def equalize_img(img, weights=None):
    rgb = img[..., :3].astype(np.int64)
    w = (img[..., 3] > 0).astype(np.float64) if weights is None else weights
    h = np.zeros(256)
    for c in range(3): h += np.bincount(rgb[..., c].ravel(), weights=w.ravel(), minlength=256)
    cdf, N = np.cumsum(h), h.sum()
    cmin = h[np.nonzero(h)[0][0]]
    lut = r8((cdf - cmin) / (N - cmin) * 255)
    return apply_lut(img, [lut] * 3)


def clip_bounds(h, n, clip=.001):
    lim = n * clip
    c = np.cumsum(h); lo = int(np.argmax(c > lim)) if (c > lim).any() else 255
    c2 = np.cumsum(h[::-1]); hi = 255 - (int(np.argmax(c2 > lim)) if (c2 > lim).any() else 255)
    return min(lo, 255), max(hi, 0)


def identity_levels():
    return {'inBlack': 0, 'inWhite': 255, 'gamma': 1, 'outBlack': 0, 'outWhite': 255}


def auto_params(kind, img):
    rgb = img[..., :3].reshape(-1, 3).astype(np.int64)
    n = len(rgb)
    p = {c: identity_levels() for c in ('rgb', 'red', 'green', 'blue')}
    if kind == 'tone':
        for k, c in enumerate(('red', 'green', 'blue')):
            lo, hi = clip_bounds(np.bincount(rgb[:, k], minlength=256), n)
            if hi - lo >= 2: p[c].update(inBlack=lo, inWhite=hi)
        return p
    if kind == 'contrast':
        h = sum(np.bincount(rgb[:, k], minlength=256) for k in range(3))
        lo, hi = clip_bounds(h, n * 3)
        if hi - lo >= 2: p['rgb'].update(inBlack=lo, inWhite=hi)
        return p
    # Auto Color: the average of the darkest and the lightest 0.1 % (by luminance) set each channel's black and white,
    # then the average near-gray midtone after that stretch is made neutral with per-channel gamma.
    y = r8(luma601(rgb[:, 0], rgb[:, 1], rgb[:, 2])).astype(np.int64)
    tlo, thi = clip_bounds(np.bincount(y, minlength=256), n)
    dark, light = rgb[y <= tlo].mean(0), rgb[y >= thi].mean(0)
    for k, c in enumerate(('red', 'green', 'blue')):
        lo, hi = int(r8(dark[k])), int(r8(light[k]))
        if hi - lo >= 2: p[c].update(inBlack=lo, inWhite=hi)
    st = np.stack([levels_fn(p[c])(rgb[:, k]) for k, c in enumerate(('red', 'green', 'blue'))], 1)
    yy = luma601(st[:, 0], st[:, 1], st[:, 2])
    sel = (st.max(1) - st.min(1) < 26) & (yy >= 48) & (yy <= 208)
    if sel.sum() >= max(1, n * .002):
        m = rgb[sel].mean(0)
        u = [min(.98, max(.02, (m[k] - p[c]['inBlack']) / max(1, p[c]['inWhite'] - p[c]['inBlack']))) for k, c in enumerate(('red', 'green', 'blue'))]
        T = sum(u) / 3
        for k, c in enumerate(('red', 'green', 'blue')): p[c]['gamma'] = round(min(9.99, max(.1, math.log(u[k]) / math.log(T))), 2)
    return p


# ----- the tests ---------------------------------------------------------------------------------------------------------------
class AdjustTests(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers ---------------------------------------------------------------------------------------------------------
    def load(self, img=None):
        b = self.browser
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true; true")
        img = picture() if img is None else img
        b.load_editor_image(img)
        b.wait_for('Ed.doc && Ed.doc.width === %d' % img.shape[1])
        b.eval("Ed.setTool('hand'); Ed.view.fit(); true"); b.settle(2)
        return img

    def layer(self, i=0):
        return self.browser.editor_pixels(i)

    def labels(self):
        return self.browser.eval('Ed.history.list().map(s => s.label)')

    def last_op(self):
        return self.browser.eval('S.ops.at(-1)')

    def dialog(self, title):
        b = self.browser
        b.wait_for(f"!!document.querySelector('dialog[open][data-ed-dialog={json.dumps(title)}]')")
        b.settle(2)

    def typ(self, name, value):
        """Type a value into a dialog number field (click, select all, type, Tab)."""
        b = self.browser
        sel = f'dialog[open] input[type=number][name="{name}"]'
        b.click_selector(sel)
        b.key('Ctrl+A'); b.type_text(str(value)); b.key('Tab')
        b.settle(1)

    def choose(self, name, value):
        self.browser.set_value(f'dialog[open] select[name="{name}"]', value); self.browser.settle(1)

    def tick(self, key):
        self.browser.click_selector(f'dialog[open] label.ed-checkbox[data-key="{key}"]'); self.browser.settle(1)

    def ok(self):
        b = self.browser
        n = b.eval('Ed.history.list().filter(s => !s.undone).length')
        b.click_selector('dialog[open] button.primary')
        b.wait_for(f"!document.querySelector('dialog[open]') && !Ed.preview.active && Ed.history.list().filter(s => !s.undone).length > {n}")
        b.settle(2)

    def run_menu(self, *labels):
        """Real clicks through the menubar: the top menu, then each submenu item by its text."""
        b = self.browser
        b.click_selector(f'#edMenubar [data-menu="{labels[0]}"]')
        b.settle(1)
        for i, text in enumerate(labels[1:]):
            ref = b.eval(f"""(() => {{ const menus = [...document.querySelectorAll('.ed-menu')]; const m = menus[menus.length - 1];
                const it = [...m.querySelectorAll('.ed-menu-item')].find(e => e.querySelector('.ed-menu-label').textContent === {json.dumps(text)});
                if (!it) return null; const r = it.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
            self.assertIsNotNone(ref, f'menu item {text!r}')
            b.mouse_move(ref[0] + 12, ref[1]); b.mouse_move(*ref); b.settle(1)
            if i == len(labels) - 2: b.click(*ref)
            else: b.click(*ref); b.settle(2)
        b.settle(2)

    def check_undo(self, before, label):
        b = self.browser
        self.assertEqual(self.labels()[-1], label)
        b.key('Ctrl+Z'); b.settle(2)
        self.assertPixelsClose(self.layer(), before, msg=f'undo of {label} restores the exact pixels')
        b.key('Ctrl+Shift+Z'); b.settle(2)

    # ----- menus --------------------------------------------------------------------------------------------------------------
    def test_01_menus_follow_photoshop_and_nothing_unverified_is_listed(self):
        self.load()
        b = self.browser
        tree = b.eval("Ed.menu.list().map(m => [m.path, m.shortcut || ''])")
        adj = [p.split('/')[-1] for p, _ in tree if p.startswith('Image/Adjustments/')]
        self.assertEqual(adj, ['Brightness-Contrast...', 'Levels...', 'Curves...', 'Exposure...', 'Vibrance...', 'Hue-Saturation...',
                               'Color Balance...', 'Black & White...', 'Photo Filter...', 'Channel Mixer...', 'Invert', 'Posterize...', 'Threshold...',
                               'Gradient Map...', 'Selective Color...', 'Desaturate', 'Equalize'])
        layer = [p.split('/')[-1] for p, _ in tree if p.startswith('Layer/New Adjustment Layer/')]
        self.assertEqual(layer, ['Brightness-Contrast...', 'Levels...', 'Curves...', 'Exposure...', 'Vibrance...', 'Hue-Saturation...',
                                 'Color Balance...', 'Black & White...', 'Photo Filter...', 'Channel Mixer...', 'Invert...', 'Posterize...', 'Threshold...',
                                 'Gradient Map...', 'Selective Color...'])
        for gone in ('Shadows', 'Replace Color', 'HDR', 'Match Color', 'Color Lookup'):
            self.assertFalse([p for p, _ in tree if gone in p], f'{gone} is not offered')
        keys = dict(tree)
        want = {'Image/Adjustments/Levels...': 'Ctrl+L', 'Image/Adjustments/Curves...': 'Ctrl+M', 'Image/Adjustments/Hue-Saturation...': 'Ctrl+U',
                'Image/Adjustments/Color Balance...': 'Ctrl+B', 'Image/Adjustments/Invert': 'Ctrl+I', 'Image/Adjustments/Desaturate': 'Ctrl+Shift+U',
                'Image/Adjustments/Black & White...': 'Ctrl+Alt+Shift+B', 'Image/Auto Tone': 'Ctrl+Shift+L', 'Image/Auto Contrast': 'Ctrl+Alt+Shift+L',
                'Image/Auto Color': 'Ctrl+Shift+B'}
        norm = lambda s: '+'.join(sorted(s.split('+')))
        for path, key in want.items(): self.assertEqual(norm(keys.get(path, '')), norm(key), path)
        shortcuts = [norm(s) for _, s in tree if s]
        self.assertEqual(len(shortcuts), len(set(shortcuts)), 'no shortcut is used twice')
        # The labels show the slash: Brightness/Contrast..., Hue/Saturation...
        n = len(self.labels())
        self.run_menu('Image', 'Adjustments', 'Brightness/Contrast...')
        self.dialog('Brightness/Contrast')
        b.key('Escape'); b.settle(2)
        self.assertFalse(b.eval("!!document.querySelector('dialog[open]')"))
        self.assertEqual(len(self.labels()), n, 'Cancel adds no history state')
        self.assertFalse(b.eval('Ed.preview.active'), 'Cancel clears the preview')
        self.assertNoBrowserErrors()

    # ----- the exact ones -------------------------------------------------------------------------------------------------
    def shortcut(self, spec):
        b = self.browser
        b.eval('document.activeElement && document.activeElement.blur(); true')
        b.mouse_move(*b.pixel_center(W // 2, H // 2))
        b.key(spec)

    def params(self, type_):
        op = self.last_op()
        self.assertEqual((op.get('tool'), op.get('type')), ('adjust', type_), op)
        return op['params']

    def test_02_levels_typed_and_dragged_matches_the_formula(self):
        img = self.load()
        b = self.browser
        self.shortcut('Ctrl+L'); self.dialog('Levels')
        self.typ('inBlack', 20); self.typ('gamma', 1.6); self.typ('inWhite', 230); self.typ('outBlack', 10); self.typ('outWhite', 245)
        self.choose('channel', 'red'); self.typ('inBlack', 30); self.typ('gamma', 0.8)
        self.choose('channel', 'blue')
        # drag the input white triangle 60 px to the left
        k = b.center_of('dialog[open] .adj-histbox .adj-knob-white')
        b.drag([k, (k[0] - 60, k[1] + 3)])
        b.settle(2)
        dragged = int(b.eval("document.querySelector('dialog[open] input[name=inWhite]').value"))
        self.assertTrue(150 < dragged < 230, dragged)
        preview = self.browser.editor_pixels()
        self.ok()
        p = self.params('levels')
        self.assertEqual(p['rgb'], {'inBlack': 20, 'inWhite': 230, 'gamma': 1.6, 'outBlack': 10, 'outWhite': 245})
        self.assertEqual((p['red']['inBlack'], p['red']['gamma'], p['blue']['inWhite']), (30, 0.8, dragged))
        ref = apply_lut(img, levels_luts(p))
        got = self.layer()
        self.assertPixelsClose(got, ref, max_abs=1, msg='Levels = the Levels formula (channel, then RGB)')
        self.assertLess(int((got != ref).sum()), 30, 'only rare rounding ties differ')
        self.assertPixelsClose(preview, got, msg='the live preview showed exactly what OK applied')
        self.check_undo(img, 'Levels')
        self.assertNoBrowserErrors()

    def curve_point(self, x, y):
        r = self.browser.eval("(() => { const q = document.querySelector('dialog[open] .adj-graph').getBoundingClientRect(); return [q.left, q.top, q.width, q.height]; })()")
        return (r[0] + x / 255 * r[2], r[1] + (1 - y / 255) * r[3])

    def test_03_curves_points_clicked_dragged_and_typed_match_a_natural_spline(self):
        img = self.load()
        b = self.browser
        self.shortcut('Ctrl+M'); self.dialog('Curves')
        b.click(*self.curve_point(64, 40)); b.settle(1)                                   # a new point
        b.drag([self.curve_point(192, 192), self.curve_point(180, 225)]); b.settle(1)     # a point on the line, dragged up
        self.typ('output', 228)                                                             # typed for the selected point
        self.choose('channel', 'blue')
        b.click(*self.curve_point(128, 100)); b.settle(1)
        self.ok()
        p = self.params('curves')
        rgb = p['rgb']
        self.assertEqual(len(rgb), 4, rgb)
        self.assertTrue(abs(rgb[1][0] - 64) <= 2 and abs(rgb[1][1] - 40) <= 2, rgb)
        self.assertTrue(abs(rgb[2][0] - 180) <= 2 and rgb[2][1] == 228, rgb)
        self.assertTrue(abs(p['blue'][1][0] - 128) <= 2 and abs(p['blue'][1][1] - 100) <= 2, p['blue'])
        self.assertEqual(p['red'], [[0, 0], [255, 255]])
        ref = apply_lut(img, curves_luts(p))
        self.assertPixelsClose(self.layer(), ref, max_abs=1, msg='Curves = a natural cubic spline (channel, then RGB)')
        self.check_undo(img, 'Curves')
        # A preset, and a curve whose end point moved (flat beyond it).
        b.key('Ctrl+Z')
        self.shortcut('Ctrl+M'); self.dialog('Curves')
        self.choose('preset', 'Strong Contrast (RGB)'); self.ok()
        p = self.params('curves')
        self.assertEqual(p['rgb'], [[0, 0], [64, 37], [192, 219], [255, 255]])
        self.assertPixelsClose(self.layer(), apply_lut(img, curves_luts(p)), max_abs=1)
        b.key('Ctrl+Z')
        self.shortcut('Ctrl+M'); self.dialog('Curves')
        b.drag([self.curve_point(1, 1), self.curve_point(40, 30)]); b.settle(1)
        self.ok()
        p = self.params('curves')
        self.assertEqual(len(p['rgb']), 2)
        self.assertGreater(p['rgb'][0][0], 30)
        self.assertPixelsClose(self.layer(), apply_lut(img, curves_luts(p)), max_abs=1)
        self.assertNoBrowserErrors()

    def test_04_channel_mixer_invert_posterize_threshold_desaturate(self):
        img = self.load()
        b = self.browser
        b.eval("Ed.menu.run('Image/Adjustments/Channel Mixer...'); true"); self.dialog('Channel Mixer')
        self.typ('r', 80); self.typ('g', 30); self.typ('b', -10); self.typ('c', 5)
        self.choose('output', 'blue'); self.typ('b', 120)
        self.ok()
        p = self.params('channel-mixer')
        self.assertEqual(p['red'], {'r': 80, 'g': 30, 'b': -10, 'c': 5})
        self.assertEqual(p['blue']['b'], 120)
        self.assertPixelsClose(self.layer(), per_pixel(img, mixer_px(p)), max_abs=1, msg='Channel Mixer')
        self.check_undo(img, 'Channel Mixer')
        b.key('Ctrl+Z')
        b.eval("Ed.menu.run('Image/Adjustments/Channel Mixer...'); true"); self.dialog('Channel Mixer')
        self.tick('monochrome'); self.typ('r', 30); self.typ('g', 60); self.typ('b', 10)
        self.ok()
        p = self.params('channel-mixer')
        self.assertTrue(p['monochrome'])
        got = self.layer()
        self.assertPixelsClose(got, per_pixel(img, mixer_px(p)), max_abs=1, msg='Monochrome mix')
        self.assertTrue((got[..., 0] == got[..., 1]).all() and (got[..., 1] == got[..., 2]).all())
        b.key('Ctrl+Z')
        # Invert (Ctrl+I, no dialog)
        self.shortcut('Ctrl+I'); b.settle(2)
        ref = img.copy(); ref[..., :3] = 255 - img[..., :3]
        self.assertPixelsClose(self.layer(), ref)
        self.check_undo(img, 'Invert')
        b.key('Ctrl+Z')
        # Posterize 5
        b.eval("Ed.menu.run('Image/Adjustments/Posterize...'); true"); self.dialog('Posterize')
        self.typ('levels', 5); self.ok()
        self.assertEqual(self.params('posterize')['levels'], 5)
        self.assertPixelsClose(self.layer(), apply_lut(img, [posterize_lut(5)] * 3))
        self.check_undo(img, 'Posterize')
        b.key('Ctrl+Z')
        # Threshold: the triangle under the histogram follows the number
        b.eval("Ed.menu.run('Image/Adjustments/Threshold...'); true"); self.dialog('Threshold')
        self.typ('level', 100)
        k = b.eval("parseFloat(document.querySelector('dialog[open] .adj-knob-gray').style.left)")
        self.assertAlmostEqual(k, 100 / 255 * 100, places=3)
        self.ok()
        self.assertPixelsClose(self.layer(), threshold_img(img, 100))
        self.check_undo(img, 'Threshold')
        b.key('Ctrl+Z')
        # Desaturate (Shift+Ctrl+U): the HSL lightness (max + min) / 2
        self.shortcut('Ctrl+Shift+U'); b.settle(2)
        self.assertPixelsClose(self.layer(), desaturate_img(img))
        self.check_undo(img, 'Desaturate')
        self.assertNoBrowserErrors()

    def test_05_black_and_white_gives_each_pure_colour_its_slider(self):
        img = self.load()
        b = self.browser
        self.shortcut('Ctrl+Alt+Shift+B'); self.dialog('Black & White')
        self.ok()
        p = self.params('black-white')
        got = self.layer()
        # Photoshop's defaults: a pure colour becomes its slider's percentage of white
        for i, pct in enumerate([40, 60, 40, 60, 20, 80]):
            self.assertEqual(int(got[1, i * 8, 0]), int(r8(255 * pct / 100)), f'pure colour {i}')
        self.assertPixelsClose(got, per_pixel(img, black_white_px(p)), max_abs=1)
        b.key('Ctrl+Z')
        self.shortcut('Ctrl+Alt+Shift+B'); self.dialog('Black & White')
        self.typ('reds', 70); self.typ('blues', -30); self.tick('tint'); self.typ('tintHue', 30); self.typ('tintSaturation', 40)
        self.ok()
        p = self.params('black-white')
        self.assertEqual((p['reds'], p['blues'], p['tint'], p['tintHue'], p['tintSaturation']), (70, -30, True, 30, 40))
        self.assertPixelsClose(self.layer(), per_pixel(img, black_white_px(p)), max_abs=1, msg='Black & White with Tint')
        self.check_undo(img, 'Black & White')
        self.assertNoBrowserErrors()

    def test_06_gradient_map_selective_color_and_equalize(self):
        img = self.load()
        b = self.browser
        b.eval("Ed.setColor('fg', '#000000'); Ed.setColor('bg', '#ffffff'); Ed.menu.run('Image/Adjustments/Gradient Map...'); true"); self.dialog('Gradient Map')
        self.choose('preset', 'blue-red-yellow'); self.tick('reverse'); self.ok()
        p = self.params('gradient-map')
        self.assertEqual([s['color'] for s in p['gradient']['stops']], ['#0a00b2', '#ff0000', '#fffc00'])
        self.assertTrue(p['reverse'])
        lut = gradient_lut(p['gradient']['stops'], True)
        self.assertPixelsClose(self.layer(), gradient_map_img(img, lut), max_abs=1, msg='Gradient Map')
        self.check_undo(img, 'Gradient Map')
        b.key('Ctrl+Z')
        # Foreground to Background, then Dither: every pixel is the plain result or one gradient step from it
        b.eval("Ed.menu.run('Image/Adjustments/Gradient Map...'); true"); self.dialog('Gradient Map')
        self.assertEqual(b.eval("document.querySelector('dialog[open] select[name=preset]').value"), 'fgbg')
        self.tick('dither'); self.ok()
        p = self.params('gradient-map')
        lut = gradient_lut(p['gradient']['stops'])
        got = self.layer()[..., 0].astype(int)
        y = r8(luma601(*(img[..., k].astype(np.float64) for k in range(3)))).astype(int)
        near = np.minimum.reduce([np.abs(got - lut[np.clip(y + d, 0, 255), 0].astype(int)) for d in (-1, 0, 1)])
        self.assertEqual(int(near.max()), 0, 'dither moves a pixel by at most one gradient step')
        self.assertGreater(int((got != lut[y, 0]).sum()), 50, 'dither does change some pixels')
        b.key('Ctrl+Z')
        # Selective Color: Reds and Neutrals, Absolute
        b.eval("Ed.menu.run('Image/Adjustments/Selective Color...'); true"); self.dialog('Selective Color')
        self.typ('cyan', -40); self.typ('magenta', 20); self.typ('black', 10)
        self.choose('colors', 'neutrals'); self.typ('yellow', 30)
        b.click_selector('dialog[open] [data-key=method] .ed-seg:nth-child(2) span'); b.settle(1)
        self.ok()
        p = self.params('selective-color')
        self.assertEqual((p['reds'], p['neutrals'], p['method']), ([-40, 20, 0, 10], [0, 0, 30, 0], 'absolute'))
        self.assertPixelsClose(self.layer(), per_pixel(img, selective_px(p)), max_abs=1, msg='Selective Color (FFmpeg model), absolute')
        b.key('Ctrl+Z')
        b.eval("Ed.menu.run('Image/Adjustments/Selective Color...', {reds: [30, -20, 10, -5], blues: [0, 40, -30, 20], whites: [10, 0, 0, 30], blacks: [0, 0, 20, -40], method: 'relative'}); true")
        b.wait_for("S.ops.at(-1).type === 'selective-color'"); b.settle(2)
        self.assertPixelsClose(self.layer(), per_pixel(img, selective_px(self.params('selective-color'))), max_abs=1, msg='Selective Color, relative')
        b.key('Ctrl+Z')
        # Equalize
        b.eval("Ed.menu.run('Image/Adjustments/Equalize'); true"); b.wait_for("S.ops.at(-1).type === 'equalize'"); b.settle(2)
        self.assertPixelsClose(self.layer(), equalize_img(img), max_abs=1, msg='Equalize')
        self.check_undo(img, 'Equalize')
        self.assertNoBrowserErrors()

    # ----- the modelled ones ----------------------------------------------------------------------------------------------
    def test_07_brightness_contrast_exposure_vibrance(self):
        img = self.load()
        b = self.browser
        self.run_menu('Image', 'Adjustments', 'Brightness/Contrast...'); self.dialog('Brightness/Contrast')
        self.typ('brightness', 40); self.typ('contrast', 30); self.ok()
        p = self.params('brightness-contrast')
        self.assertEqual((p['brightness'], p['contrast'], p['legacy']), (40, 30, False))
        got = self.layer()
        self.assertPixelsClose(got, apply_lut(img, [bc_lut(p)] * 3), max_abs=1, msg='modern Brightness/Contrast (curve model)')
        self.assertEqual((int(got[0, 0, 0]), int(got[0, -1, 0])), (0, 255), 'the modern mode keeps black and white')
        self.check_undo(img, 'Brightness/Contrast')
        b.key('Ctrl+Z')
        self.run_menu('Image', 'Adjustments', 'Brightness/Contrast...'); self.dialog('Brightness/Contrast')
        self.tick('legacy'); self.typ('brightness', -20); self.typ('contrast', 50); self.ok()
        p = self.params('brightness-contrast')
        self.assertEqual((p['brightness'], p['contrast'], p['legacy']), (-20, 50, True))
        self.assertPixelsClose(self.layer(), apply_lut(img, [bc_lut(p)] * 3), max_abs=1, msg='legacy Brightness/Contrast')
        b.key('Ctrl+Z')
        # Exposure
        b.eval("Ed.menu.run('Image/Adjustments/Exposure...'); true"); self.dialog('Exposure')
        self.typ('exposure', 0.75); self.typ('offset', -0.02); self.typ('gamma', 1.3); self.ok()
        p = self.params('exposure')
        self.assertEqual((p['exposure'], p['offset'], p['gamma']), (0.75, -0.02, 1.3))
        self.assertPixelsClose(self.layer(), apply_lut(img, [exposure_lut(p)] * 3), max_abs=1, msg='Exposure in linear light')
        self.check_undo(img, 'Exposure')
        b.key('Ctrl+Z')
        # Vibrance, both directions
        for vib, sat in ((60, -20), (-40, 15)):
            b.eval("Ed.menu.run('Image/Adjustments/Vibrance...'); true"); self.dialog('Vibrance')
            self.typ('vibrance', vib); self.typ('saturation', sat); self.ok()
            p = self.params('vibrance')
            self.assertEqual((p['vibrance'], p['saturation']), (vib, sat))
            self.assertPixelsClose(self.layer(), per_pixel(img, vibrance_px(p)), max_abs=1, msg=f'Vibrance {vib}/{sat}')
            self.check_undo(img, 'Vibrance')
            b.key('Ctrl+Z')
        self.assertNoBrowserErrors()

    def test_08_hue_saturation_master_ranges_and_colorize(self):
        img = self.load()
        b = self.browser
        self.shortcut('Ctrl+U'); self.dialog('Hue/Saturation')
        self.typ('hue', 40); self.typ('saturation', -30); self.typ('lightness', 20)
        self.choose('edit', 'reds'); self.typ('saturation', 50)
        self.assertEqual(b.text('dialog[open] .adj-range-text'), '315°/345° \\ 15°/45°', "the Reds range shows Photoshop's default")
        self.choose('edit', 'blues'); self.typ('hue', -25); self.typ('lightness', -30)
        self.ok()
        p = self.params('hue-saturation')
        self.assertEqual(p['master'], {'hue': 40, 'saturation': -30, 'lightness': 20})
        self.assertEqual((p['reds']['saturation'], p['blues']['hue'], p['blues']['lightness']), (50, -25, -30))
        self.assertPixelsClose(self.layer(), per_pixel(img, huesat_px(p)), max_abs=1, msg='Hue/Saturation (reverse-engineered model)')
        self.check_undo(img, 'Hue/Saturation')
        b.key('Ctrl+Z')
        # Each step on its own, so a wrong one cannot hide behind another
        for master in ({'hue': 0, 'saturation': 60, 'lightness': 0}, {'hue': 0, 'saturation': -100, 'lightness': 0},
                       {'hue': 0, 'saturation': 0, 'lightness': -45}, {'hue': -120, 'saturation': 0, 'lightness': 0}):
            b.eval(f"Ed.menu.run('Image/Adjustments/Hue-Saturation...', {{master: {json.dumps(master)}}}); true")
            b.wait_for("S.ops.at(-1).type === 'hue-saturation'"); b.settle(2)
            self.assertPixelsClose(self.layer(), per_pixel(img, huesat_px(self.params('hue-saturation'))), max_abs=1, msg=str(master))
            b.key('Ctrl+Z'); b.settle(1)
        # -100 saturation is Desaturate exactly
        b.eval("Ed.menu.run('Image/Adjustments/Hue-Saturation...', {master: {saturation: -100}}); true"); b.wait_for("S.ops.at(-1).type === 'hue-saturation'"); b.settle(2)
        self.assertPixelsClose(self.layer(), desaturate_img(img), max_abs=1)
        b.key('Ctrl+Z')
        # Colorize
        self.shortcut('Ctrl+U'); self.dialog('Hue/Saturation')
        self.tick('colorize')
        self.assertTrue(b.eval("document.querySelector('dialog[open] select[name=edit]').disabled"))
        self.typ('hue', 200); self.typ('saturation', 60); self.typ('lightness', -10); self.ok()
        p = self.params('hue-saturation')
        self.assertEqual(p['colorize'], {'enabled': True, 'hue': 200, 'saturation': 60, 'lightness': -10})
        self.assertPixelsClose(self.layer(), per_pixel(img, colorize_px(p['colorize'])), max_abs=1, msg='Colorize')
        self.check_undo(img, 'Hue/Saturation')
        self.assertNoBrowserErrors()

    def test_09_color_balance_and_photo_filter(self):
        img = self.load()
        b = self.browser
        self.shortcut('Ctrl+B'); self.dialog('Color Balance')
        b.click_selector('dialog[open] [data-key=tone] .ed-seg:nth-child(1) span'); b.settle(1)
        self.typ('cyanRed', 30); self.typ('yellowBlue', -20)
        b.click_selector('dialog[open] [data-key=tone] .ed-seg:nth-child(3) span'); b.settle(1)
        self.typ('magentaGreen', 25)
        b.click_selector('dialog[open] [data-key=tone] .ed-seg:nth-child(2) span'); b.settle(1)
        self.typ('cyanRed', -15)
        self.ok()
        p = self.params('color-balance')
        self.assertEqual((p['shadows'], p['midtones'], p['highlights'], p['preserveLuminosity']), ([30, 0, -20], [-15, 0, 0], [0, 25, 0], True))
        self.assertPixelsClose(self.layer(), per_pixel(img, color_balance_px(p)), max_abs=1, msg='Color Balance, Preserve Luminosity')
        self.check_undo(img, 'Color Balance')
        b.key('Ctrl+Z')
        self.shortcut('Ctrl+B'); self.dialog('Color Balance')
        self.typ('cyanRed', 60); self.typ('yellowBlue', 40); self.tick('preserveLuminosity'); self.ok()
        p = self.params('color-balance')
        self.assertFalse(p['preserveLuminosity'])
        self.assertPixelsClose(self.layer(), per_pixel(img, color_balance_px(p)), max_abs=1, msg='Color Balance')
        b.key('Ctrl+Z')
        # Photo Filter
        b.eval("Ed.menu.run('Image/Adjustments/Photo Filter...'); true"); self.dialog('Photo Filter')
        self.choose('filter', 'cooling-80'); self.typ('density', 60); self.ok()
        p = self.params('photo-filter')
        self.assertEqual((p['filter'], p['color'], p['density'], p['preserveLuminosity']), ('cooling-80', '#006dff', 60, True))
        self.assertPixelsClose(self.layer(), per_pixel(img, photo_filter_px(p)), max_abs=1, msg='Photo Filter, Preserve Luminosity')
        self.check_undo(img, 'Photo Filter')
        b.key('Ctrl+Z')
        b.eval("Ed.menu.run('Image/Adjustments/Photo Filter...'); true"); self.dialog('Photo Filter')
        b.set_value('dialog[open] input[type=color][name=color]', '#33cc66'); b.settle(1)
        self.assertEqual(b.eval("document.querySelector('dialog[open] select[name=filter]').value"), 'custom')
        self.tick('preserveLuminosity'); self.ok()
        p = self.params('photo-filter')
        self.assertEqual((p['filter'], p['color'], p['density'], p['preserveLuminosity']), ('custom', '#33cc66', 25, False))
        self.assertPixelsClose(self.layer(), per_pixel(img, photo_filter_px(p)), max_abs=1, msg='Photo Filter')
        self.assertNoBrowserErrors()

    def test_10_auto_tone_contrast_color(self):
        img = self.load(dull())
        b = self.browser
        for key, kind, label in (('Ctrl+Shift+L', 'tone', 'Auto Tone'), ('Ctrl+Alt+Shift+L', 'contrast', 'Auto Contrast'), ('Ctrl+Shift+B', 'color', 'Auto Color')):
            self.shortcut(key); b.wait_for(f"Ed.history.list().filter(s => !s.undone).at(-1).label === {json.dumps(label)}"); b.settle(2)
            p = auto_params(kind, img)
            self.assertPixelsClose(self.layer(), apply_lut(img, levels_luts(p)), max_abs=1, msg=label)
            self.assertFalse(np.array_equal(self.layer(), img), f'{label} changed the picture')
            self.check_undo(img, label)
            b.key('Ctrl+Z')
        self.assertNoBrowserErrors()

    # ----- selection, lock transparency, layer masks ----------------------------------------------------------------------
    def set_selection(self, mask):
        """A soft selection from a (H, W) uint8 array (the test sets it directly; selecting is select.js's job)."""
        b = self.browser
        rgba = np.zeros(mask.shape + (4,), np.uint8); rgba[..., :3] = 255; rgba[..., 3] = mask
        expr = b.push_pixels(rgba)
        b.eval(f"(() => {{ const d = {expr}; const c = document.createElement('canvas'); c.width = d.width; c.height = d.height; c.getContext('2d').putImageData(d, 0, 0); Ed.select.set(c, 'replace'); return true; }})()")
        b.settle(2)
        return b.editor_pixels('selection')[..., 3]

    def test_11_soft_selection_lock_transparency_and_layer_mask(self):
        img = self.load()
        b = self.browser
        yy, xx = np.mgrid[0:H, 0:W] + .5
        soft = r8(np.clip((30 - np.hypot(xx - 64, yy - 48)) / 14, 0, 1) * 255)
        s = self.set_selection(soft).astype(np.float64)[..., None]
        self.assertGreater(int(((s > 0) & (s < 255)).sum()), 300, 'the selection really is soft')
        self.shortcut('Ctrl+L'); self.dialog('Levels')
        self.typ('inBlack', 60); self.typ('gamma', 1.7); self.typ('outWhite', 220)
        preview = b.editor_pixels()
        self.ok()
        full = apply_lut(img, levels_luts(self.params('levels')))
        want = r8(img + (full.astype(np.float64) - img) * s / 255)
        want[..., 3] = 255
        got = self.layer()
        self.assertPixelsClose(got, want, max_abs=1, msg='the change goes through the soft selection')
        self.assertPixelsClose(got, img, mask=s[..., 0] == 0, msg='outside the selection nothing changes')
        self.assertPixelsClose(preview, got, max_abs=1, msg='the preview matched')
        self.check_undo(img, 'Levels')
        b.key('Ctrl+Z'); b.key('Ctrl+D'); b.settle(2)
        # Lock transparency: alpha stays exactly as it was
        alpha = np.random.default_rng(3).integers(0, 256, (H, W)).astype(np.uint8); alpha[:10] = 0; alpha[10:20] = 255
        self.load(picture(alpha=alpha))
        before = self.layer()
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}); true")
        self.shortcut('Ctrl+I'); b.settle(2)
        got = self.layer()
        self.assertTrue(np.array_equal(got[..., 3], before[..., 3]), 'alpha unchanged under Lock Transparency')
        inv = before.copy(); inv[..., :3] = 255 - before[..., :3]
        self.assertPixelsClose(got, inv, mask=before[..., 3] == 255, msg='opaque pixels inverted exactly')
        self.assertPixelsClose(got, inv, max_abs=3, mask=before[..., 3] >= 128, msg='half-transparent pixels inverted')
        # A layer mask: the tonal adjustments work on it as gray, the colour ones are refused
        img = self.load()
        b.eval("Ed.layers.addMask(0, 'reveal'); true"); b.settle(2)
        self.assertEqual(b.eval('Ed.doc.target'), 'mask')
        en = dict(b.eval("Ed.menu.list().filter(m => m.path.startsWith('Image/')).map(m => [m.path, m.enabled])"))
        self.assertTrue(en['Image/Adjustments/Levels...'] and en['Image/Adjustments/Invert'] and en['Image/Auto Tone'])
        self.assertFalse(en['Image/Adjustments/Hue-Saturation...'] or en['Image/Adjustments/Channel Mixer...'] or en['Image/Auto Color'])
        self.shortcut('Ctrl+L'); self.dialog('Levels')
        self.assertEqual(b.eval("[...document.querySelectorAll('dialog[open] select[name=channel] option')].map(o => o.value)"), ['rgb'])
        self.typ('outWhite', 100); self.ok()
        m = b.editor_pixels('mask')[..., 3]
        self.assertEqual((int(m.min()), int(m.max())), (100, 100), 'Levels on the white mask: output white 100')
        self.assertPixelsClose(self.layer(), img, msg='the pixels are untouched')
        self.shortcut('Ctrl+I'); b.settle(2)
        m = b.editor_pixels('mask')[..., 3]
        self.assertEqual((int(m.min()), int(m.max())), (155, 155))
        self.assertEqual(self.labels()[-1], 'Invert')
        self.assertNoBrowserErrors()

    # ----- adjustment layers ----------------------------------------------------------------------------------------------
    LAYER_PARAMS = {
        'brightness-contrast': {'brightness': 35, 'contrast': -20},
        'levels': {'rgb': {'inBlack': 15, 'inWhite': 220, 'gamma': 1.3, 'outBlack': 5, 'outWhite': 250}, 'green': {'inBlack': 30, 'inWhite': 255, 'gamma': .8, 'outBlack': 0, 'outWhite': 255}},
        'curves': {'rgb': [[0, 10], [90, 70], [200, 230], [255, 250]], 'red': [[0, 0], [128, 150], [255, 255]]},
        'exposure': {'exposure': -.6, 'offset': .03, 'gamma': .9},
        'vibrance': {'vibrance': 45, 'saturation': 10},
        'hue-saturation': {'master': {'hue': 30, 'saturation': 20, 'lightness': -10}, 'greens': {'hue': -40, 'saturation': 30, 'lightness': 0}},
        'color-balance': {'shadows': [20, -10, 5], 'highlights': [0, 15, -25]},
        'black-white': {'reds': 55, 'blues': 10, 'tint': True, 'tintHue': 200, 'tintSaturation': 30},
        'photo-filter': {'filter': 'warming-81', 'density': 45},
        'channel-mixer': {'red': {'r': 70, 'g': 20, 'b': 10, 'c': 0}, 'blue': {'r': 0, 'g': 10, 'b': 90, 'c': 3}},
        'invert': {},
        'posterize': {'levels': 6},
        'threshold': {'level': 140},
        'gradient-map': {'gradient': {'stops': [{'at': 0, 'color': '#290a59', 'mid': 50}, {'at': 100, 'color': '#ff7c00', 'mid': 50}]}},
        'selective-color': {'reds': [-30, 10, 0, 5], 'neutrals': [0, -10, 20, 0], 'method': 'relative'},
    }

    def test_12_every_adjustment_layer_gives_the_same_pixels_as_its_command(self):
        img = self.load()
        b = self.browser
        paths = dict(zip(b.eval('Ed.adjust.layerTypes()'), b.eval("Ed.menu.list().filter(m => m.path.startsWith('Layer/New Adjustment Layer/')).map(m => m.path)")))
        self.assertEqual(set(paths), set(self.LAYER_PARAMS))
        for t, params in self.LAYER_PARAMS.items():
            cmd = paths[t].replace('Layer/New Adjustment Layer/', 'Image/Adjustments/')
            if t == 'invert': cmd = 'Image/Adjustments/Invert'
            b.eval(f"Ed.menu.run({json.dumps(cmd)}, {json.dumps(params)}); true"); b.wait_for(f"S.ops.at(-1).type === {json.dumps(t)}"); b.settle(2)
            by_command = self.layer()
            self.assertFalse(np.array_equal(by_command, img), t)
            b.key('Ctrl+Z'); b.settle(2)
            b.eval(f"Ed.menu.run({json.dumps(paths[t])}, {json.dumps(params)}); true"); b.wait_for('Ed.doc.layers.length === 2'); b.settle(2)
            self.assertEqual(self.labels()[-1], f"New {b.eval(f'Ed.adjust.label({json.dumps(t)})')} Layer")
            self.assertPixelsClose(b.editor_pixels(), by_command, msg=f'{t}: adjustment layer = command')
            self.assertPixelsClose(self.layer(0), img, msg='the layer below is untouched')
            b.key('Ctrl+Z'); b.wait_for('Ed.doc.layers.length === 1'); b.settle(1)
        self.assertNoBrowserErrors()

    def prop_type(self, name, value):
        b = self.browser
        sel = f'#edPanel-properties input[type=number][name="{name}"]'
        b.click_selector(sel); b.key('Ctrl+A'); b.type_text(str(value)); b.key('Tab'); b.settle(2)

    def test_13_adjustment_layer_edited_in_properties_and_painted_through_its_mask(self):
        img = self.load()
        b = self.browser
        self.run_menu('Layer', 'New Adjustment Layer', 'Levels...')
        b.wait_for('Ed.doc.layers.length === 2'); b.settle(3)
        L = b.eval("(() => { const L = Ed.layers.active(); return {name: L.name, kind: L.kind, type: L.adjustment.type, target: Ed.doc.target}; })()")
        self.assertEqual(L, {'name': 'Levels 1', 'kind': 'adjustment', 'type': 'levels', 'target': 'mask'})
        self.assertEqual(self.labels()[-1], 'New Levels Layer')
        self.assertEqual(b.eval("document.querySelector('#edDock .ed-tab[data-panel=properties]').getAttribute('aria-selected')"), 'true')
        self.prop_type('inBlack', 50); self.prop_type('gamma', 1.4)
        self.assertEqual(self.labels()[-2:], ['Modify Levels Layer', 'Modify Levels Layer'])
        p = b.eval('Ed.layers.active().adjustment.params')
        self.assertEqual((p['rgb']['inBlack'], p['rgb']['gamma']), (50, 1.4))
        adjusted = apply_lut(img, levels_luts(p))
        self.assertPixelsClose(b.editor_pixels(), adjusted, max_abs=1)
        self.shortcut('Ctrl+Z'); b.settle(3)
        self.assertEqual(b.eval("document.querySelector('#edPanel-properties input[name=gamma]').value"), '1.00', 'undo updates Properties')
        self.shortcut('Ctrl+Shift+Z'); b.settle(3)
        # Paint the mask black with the brush: the adjustment disappears there, partly at the soft edge
        b.eval("Ed.setColor('fg', '#000000'); Ed.setTool('brush'); Ed.setToolOptions('brush', {size: 24, hardness: 50, opacity: 100, flow: 100}); true")
        b.drag_image([(20, 30), (100, 60)])
        b.settle(3)
        m = b.editor_pixels('mask')[..., 3].astype(np.float64)[..., None]
        self.assertGreater(int((m[..., 0] == 0).sum()), 300); self.assertGreater(int(((m > 0) & (m < 255)).sum()), 100)
        want = r8(img + (adjusted.astype(np.float64) - img) * m / 255); want[..., 3] = 255
        self.assertPixelsClose(b.editor_pixels(), want, max_abs=1, msg='the adjustment applies through its mask')
        self.assertPixelsClose(self.layer(0), img, msg='painting the mask never touches the pixels below')
        # Layer opacity mixes the adjustment in
        b.eval("Ed.layers.set(1, {opacity: .5}); true"); b.settle(2)
        want = r8(img + (adjusted.astype(np.float64) - img) * m / 255 * .5); want[..., 3] = 255
        self.assertPixelsClose(b.editor_pixels(), want, max_abs=1, msg='50% opacity')
        b.eval("Ed.layers.set(1, {opacity: 1}); true")
        # The Properties buttons: visibility, clip (to a small square layer), reset, delete
        b.eval("Ed.setTool('hand'); Ed.panels.show('properties'); true"); b.settle(2)
        b.click_selector('#edPanel-properties [data-action=visible]'); b.settle(2)
        self.assertFalse(b.eval('Ed.doc.layers[1].visible'))
        self.assertPixelsClose(b.editor_pixels(), img)
        b.click_selector('#edPanel-properties [data-action=visible]'); b.settle(2)
        b.click_selector('#edPanel-properties [data-action=reset]'); b.settle(2)
        self.assertPixelsClose(b.editor_pixels(), img, msg='Reset: back to the identity Levels')
        self.assertEqual(self.labels()[-1], 'Modify Levels Layer')
        b.key('Ctrl+Z'); b.settle(2)
        square = np.zeros((H, W, 4), np.uint8); square[20:50, 30:70] = (40, 160, 220, 255)
        b.eval(f"(() => {{ const d = {b.push_pixels(square)}; const c = document.createElement('canvas'); c.width = d.width; c.height = d.height; c.getContext('2d').putImageData(d, 0, 0); Ed.layers.add({{name: 'Square', canvas: c, at: 1}}); Ed.layers.setActive(2); Ed.panels.show('properties'); return true; }})()")
        b.settle(3)
        b.click_selector('#edPanel-properties [data-action=clip]'); b.settle(3)
        self.assertTrue(b.eval('Ed.doc.layers[2].clip'))
        self.assertEqual(b.eval("document.querySelector('#edPanel-properties [data-action=clip]').getAttribute('aria-pressed')"), 'true')
        comp = b.editor_pixels()
        base = img.copy(); base[20:50, 30:70] = square[20:50, 30:70]
        self.assertPixelsClose(comp, base, mask=np.pad(np.ones((30, 40), bool), ((20, H - 50), (30, W - 70))) == 0, msg='clipped: nothing outside the square changes')
        sq_adj = apply_lut(base, levels_luts(p))
        mm = b.editor_pixels('mask')[..., 3].astype(np.float64)[..., None]
        want = r8(base + (sq_adj.astype(np.float64) - base) * mm / 255); want[..., 3] = 255
        self.assertPixelsClose(comp[20:50, 30:70], want[20:50, 30:70], max_abs=1, msg='clipped: the square is adjusted')
        b.click_selector('#edPanel-properties [data-action=delete]'); b.settle(2)
        self.assertEqual(b.eval('Ed.doc.layers.map(L => L.kind)'), ['pixel', 'pixel'])
        self.assertNoBrowserErrors()

    def test_14_adjustment_layers_survive_save_and_reopen(self):
        b = self.browser
        b.select_asset('edit-photo.png')
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); Ed.select.none(); true")
        b.eval("Ed.select.rect(40, 30, 120, 90); Ed.menu.run('Layer/New Adjustment Layer/Curves...', {rgb: [[0, 20], [100, 60], [200, 230], [255, 255]], blue: [[0, 0], [128, 90], [255, 255]]}); Ed.select.none(); true")
        b.eval("Ed.menu.run('Layer/New Adjustment Layer/Hue-Saturation...', {master: {hue: 50, saturation: 25, lightness: 0}, reds: {hue: 0, saturation: -60, lightness: 10}}); true")
        b.eval("Ed.menu.run('Layer/New Adjustment Layer/Gradient Map...', {gradient: {stops: [{at: 0, color: '#0a00b2', mid: 50}, {at: 50, color: '#ff0000', mid: 50}, {at: 100, color: '#fffc00', mid: 50}]}}); Ed.layers.set(3, {opacity: .4, blend: 'multiply'}); true")
        b.settle(3)
        before = b.editor_pixels()
        want = b.eval("Ed.doc.layers.map(L => [L.name, L.kind, JSON.stringify(L.adjustment), L.opacity, L.blend])")
        b.key('Ctrl+S')
        b.wait_for("!S.dirty && !S.busy && S.asset.revision !== 'original'", timeout=30)
        b.select_asset('edit-sprite.png')
        b.select_asset('edit-photo.png')
        b.wait_for('Ed.doc && Ed.doc.layers.length === 4', timeout=20); b.settle(3)
        self.assertEqual(b.eval("Ed.doc.layers.map(L => [L.name, L.kind, JSON.stringify(L.adjustment), L.opacity, L.blend])"), want)
        self.assertNotIn('flat', b.text('#toast') if b.eval("!document.getElementById('toast').hidden") else '')
        self.assertPixelsClose(b.editor_pixels(), before, msg='the reopened document composites the same')
        # still editable after the reopen
        b.eval("Ed.layers.setActive(2); Ed.panels.show('properties'); true"); b.settle(3)
        self.assertTrue(b.eval("!!document.querySelector('#edPanel-properties [data-adjust=hue-saturation]')"))
        self.assertNoBrowserErrors()

    # ----- eyedroppers, the Hue/Saturation range handles, Equalize with a selection ---------------------------------------
    def test_15_levels_and_curves_eyedroppers_pick_from_the_picture(self):
        img = self.load()
        b = self.browser
        b.eval("Ed.setToolOptions('eyedropper', {sample: 1}); true")
        self.shortcut('Ctrl+L'); self.dialog('Levels')
        b.click_selector('dialog[open] .adj-drop-white'); b.settle(1)
        self.assertTrue(b.eval("document.body.classList.contains('adj-sampling') && !document.querySelector('dialog[open]').matches(':modal')"))
        self.assertEqual(b.eval('[document.scrollingElement.scrollTop, Math.round(document.querySelector("dialog[open]").getBoundingClientRect().top)]'), [0, 70], 'the dialog stays put')
        x, y = b.image_to_client(74.5, 1.5)
        b.click(x, y); b.settle(2)                   # the (200, 60, 40) swatch
        self.assertEqual(self.labels()[-1], 'Open', 'the click did not reach the tool')
        self.choose('channel', 'red')
        self.assertEqual(b.eval("document.querySelector('dialog[open] input[name=inWhite]').value"), '200')
        # As in Photoshop the eyedropper stays on for more clicks; clicking its button again turns it off
        self.assertTrue(b.eval("document.body.classList.contains('adj-sampling')"))
        b.click_selector('dialog[open] .adj-drop-white'); b.settle(1)
        self.assertTrue(b.eval("!document.body.classList.contains('adj-sampling') && document.querySelector('dialog[open]').matches(':modal')"))
        self.ok()
        p = self.params('levels')
        self.assertEqual((p['red']['inWhite'], p['green']['inWhite'], p['blue']['inWhite']), (200, 60, 40))
        self.assertPixelsClose(self.layer(), apply_lut(img, levels_luts(p)), max_abs=1)
        self.assertEqual(self.layer()[1, 74, :3].tolist(), [255, 255, 255], 'the picked colour became white')
        b.key('Ctrl+Z')
        # Gray point: the picked colour comes out neutral
        self.shortcut('Ctrl+L'); self.dialog('Levels')
        b.click_selector('dialog[open] .adj-drop-gray'); b.click(*b.image_to_client(98.5, 1.5)); b.settle(2)   # (230, 180, 140)
        self.ok()
        px = self.layer()[1, 98, :3].astype(int)
        self.assertLessEqual(int(px.max() - px.min()), 2, f'gray point made it neutral: {px}')
        b.key('Ctrl+Z')
        # Curves black point: a point at the picked value moved to 0 in each channel
        self.shortcut('Ctrl+M'); self.dialog('Curves')
        b.click_selector('dialog[open] .adj-drop-black'); b.click(*b.image_to_client(106.5, 1.5)); b.settle(2)  # (12, 30, 8)
        self.ok()
        p = self.params('curves')
        self.assertEqual((p['red'][0], p['green'][0], p['blue'][0]), ([12, 0], [30, 0], [8, 0]))
        self.assertEqual(self.layer()[1, 106, :3].tolist(), [0, 0, 0])
        b.key('Ctrl+Z')
        # Escape leaves the eyedropper without closing the dialog
        self.shortcut('Ctrl+L'); self.dialog('Levels')
        b.click_selector('dialog[open] .adj-drop-black'); b.key('Escape'); b.settle(1)
        self.assertTrue(b.eval("!!document.querySelector('dialog[open]') && !document.body.classList.contains('adj-sampling')"))
        b.key('Escape'); b.settle(1)
        self.assertFalse(b.eval("!!document.querySelector('dialog[open]')"))
        self.assertNoBrowserErrors()

    def test_16_hue_saturation_range_handles_drag(self):
        img = self.load()
        b = self.browser
        self.shortcut('Ctrl+U'); self.dialog('Hue/Saturation')
        self.choose('edit', 'greens'); b.settle(2)
        r = b.eval("(() => { const q = document.querySelector('dialog[open] .adj-hsbars').getBoundingClientRect(); return [q.left, q.top, q.width, q.height]; })()")
        px = lambda h: r[0] + (h - 120 + 180) / 360 * r[2]                       # Greens are centred at 120 degrees
        y = r[1] + r[3] * .8
        b.drag([(px(165), y), (px(165) + 40 / 360 * r[2], y)]); b.settle(1)    # the outer right handle, +40 degrees
        self.assertEqual(b.text('dialog[open] .adj-range-text'), '75°/105° \\ 135°/205°')
        b.drag([(px(120), y), (px(120) - 20 / 360 * r[2], y)]); b.settle(1)    # the middle band, -20 degrees
        self.assertEqual(b.text('dialog[open] .adj-range-text'), '55°/85° \\ 115°/185°')
        self.typ('saturation', -80)
        self.ok()
        p = self.params('hue-saturation')
        R = p['greens']['range']
        self.assertTrue(all(abs(a - e) <= 1 for a, e in zip(R, [55, 85, 115, 185])), R)
        self.assertPixelsClose(self.layer(), per_pixel(img, huesat_px(p)), max_abs=1, msg='the moved range')
        self.assertNoBrowserErrors()

    def test_17_equalize_with_a_selection_asks_like_photoshop(self):
        img = self.load()
        b = self.browser
        b.eval("Ed.select.rect(20, 10, 50, 40); true"); b.settle(1)
        sel = np.zeros((H, W)); sel[10:50, 20:70] = 1
        b.eval("Ed.menu.run('Image/Adjustments/Equalize'); true"); self.dialog('Equalize')
        self.ok()
        want = img.copy(); want[10:50, 20:70] = equalize_img(img, sel)[10:50, 20:70]
        self.assertPixelsClose(self.layer(), want, max_abs=1, msg='Selected area only')
        b.key('Ctrl+Z')
        b.eval("Ed.menu.run('Image/Adjustments/Equalize'); true"); self.dialog('Equalize')
        b.click_selector('dialog[open] [data-key=scope] .ed-seg:nth-child(2) span'); b.settle(1)
        self.ok()
        self.assertPixelsClose(self.layer(), equalize_img(img, sel), max_abs=1, msg='Entire image from the selected area')
        self.check_undo(img, 'Equalize')
        self.assertNoBrowserErrors()

    def test_18_properties_for_a_pixel_layer_adds_adjustment_layers(self):
        self.load()
        b = self.browser
        b.eval("Ed.panels.show('properties'); true"); b.settle(3)
        self.assertEqual(b.text('#edPanel-properties .adj-props-title b'), 'Pixel Layer')
        b.wait_for("document.querySelector('#edPanel-properties .adj-info').textContent.includes('128 × 96 px at 0, 0')")
        self.assertIn('128 × 96 px', b.text('#edPanel-properties .adj-info'))
        adds = b.eval("[...document.querySelectorAll('#edPanel-properties [data-add]')].map(e => e.dataset.add)")
        self.assertEqual(adds, b.eval('Ed.adjust.layerTypes()'))
        b.click_selector('#edPanel-properties [data-add=curves]'); b.settle(3)
        self.assertEqual(b.eval("[Ed.layers.active().kind, Ed.layers.active().adjustment.type, Ed.layers.active().name]"), ['adjustment', 'curves', 'Curves 1'])
        self.assertEqual(self.labels()[-1], 'New Curves Layer')
        self.assertEqual(b.text('#edPanel-properties .adj-props-title b'), 'Curves')
        self.assertTrue(b.eval("!!document.querySelector('#edPanel-properties .adj-graph')"))
        # Layer > Layer Content Options... opens Properties for an adjustment layer (disabled on a pixel layer)
        b.eval("Ed.panels.show('layers'); Ed.menu.run('Layer/Layer Content Options...'); true"); b.settle(2)
        self.assertEqual(b.eval("document.querySelector('#edDock .ed-tab[data-panel=properties]').getAttribute('aria-selected')"), 'true')
        self.assertFalse(b.eval("Ed.layers.setActive(0); Ed.menu.list().find(m => m.path === 'Layer/Layer Content Options...').enabled"))
        b.eval("Ed.layers.setActive(1); true"); b.settle(2)
        # The badge on the Layers row opens Properties too
        if b.eval("!!document.querySelector('[data-adj-badge]')"):
            b.eval("Ed.panels.show('layers'); true"); b.settle(2)
            b.click_selector('[data-adj-badge]'); b.settle(2)
            self.assertEqual(b.eval("document.querySelector('#edDock .ed-tab[data-panel=properties]').getAttribute('aria-selected')"), 'true')
        self.assertNoBrowserErrors()

    # ----- layout -----------------------------------------------------------------------------------------------------------
    def test_19_dialogs_and_properties_fit_at_1366_and_1920(self):
        img = self.load(picture())
        b = self.browser
        menus = b.eval("Ed.menu.list().filter(m => m.path.startsWith('Image/Adjustments/') && m.path.endsWith('...')).map(m => m.path)")
        problems = []
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h)
                for path in menus:
                    b.eval(f"Ed.menu.run({json.dumps(path)}); true"); b.wait_for("!!document.querySelector('dialog[open]')"); b.settle(4)
                    m = b.eval("""(() => { const d = document.querySelector('dialog[open]'), r = d.getBoundingClientRect(), f = d.querySelector('.ed-dialog-fields');
                        const out = [...d.querySelectorAll('*')].filter(e => { if (!e.getClientRects().length) return false; const q = e.getBoundingClientRect(); return q.width > 0 && (q.right > r.right - 4 || q.left < r.left + 4); }).map(e => e.className || e.tagName);
                        const cut = [...d.querySelectorAll('label, button, .ed-field-label, span')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => e.textContent.trim());
                        return {rect: [r.left, r.top, r.right, r.bottom], scroll: [f.scrollHeight - f.clientHeight, f.scrollWidth - f.clientWidth], out, cut}; })()""")
                    name = path.split('/')[-1]
                    x0, y0, x1, y1 = m['rect']
                    if x0 < 0 or y0 < 0 or x1 > w or y1 > h: problems.append(f'{w}x{h} {name}: outside the window {m["rect"]}')
                    if m['scroll'][0] > 1 or m['scroll'][1] > 1: problems.append(f'{w}x{h} {name}: the fields scroll {m["scroll"]}')
                    if m['out']: problems.append(f'{w}x{h} {name}: spills {m["out"][:4]}')
                    if m['cut']: problems.append(f'{w}x{h} {name}: cut-off text {m["cut"][:4]}')
                    if w == 1366: b.screenshot(f'adjust-dialog-{name.replace("...", "").replace("/", "-").replace(" ", "_").replace("&", "and")}.png')
                    b.key('Escape'); b.settle(2)
                # Properties with every adjustment layer type
                for t in b.eval('Ed.adjust.layerTypes()'):
                    b.eval(f"Ed.adjust.newLayer({json.dumps(t)}); true"); b.settle(4)
                    m = b.eval("""(() => { const p = document.getElementById('edPanel-properties'), dock = document.getElementById('edDock').getBoundingClientRect();
                        const out = [...p.querySelectorAll('*')].filter(e => { if (!e.getClientRects().length) return false; const q = e.getBoundingClientRect(); return q.width > 0 && (q.right > dock.right - 1 || q.left < dock.left + 1); }).map(e => e.className || e.tagName);
                        return {out, w: p.scrollWidth - p.clientWidth}; })()""")
                    if m['out'] or m['w'] > 0: problems.append(f'{w}x{h} Properties {t}: spills {m["out"][:4]} ({m["w"]})')
                    if w == 1366 and t in ('levels', 'curves', 'hue-saturation', 'color-balance', 'gradient-map'):
                        b.eval("document.querySelector('.inspector').scrollTop = document.getElementById('edDock').offsetTop - 20; true"); b.settle(2)
                        b.screenshot(f'adjust-properties-{t}.png')
                    b.key('Ctrl+Z'); b.settle(1)
                tab = b.eval("(() => { const t = document.querySelector('#edDock .ed-tab[data-panel=properties]').getBoundingClientRect(), l = document.querySelector('#edDock .ed-tabs').getBoundingClientRect(); return t.left >= l.left - 1 && t.right <= l.right + 1; })()")
                if not tab: problems.append(f'{w}x{h}: the Properties tab is scrolled out of the dock')
        finally:
            b.set_viewport(*self.viewport)
        self.assertEqual(problems, [])
        self.assertNoBrowserErrors()

    def test_20_properties_auto_eyedropper_and_mask_buttons(self):
        img = self.load(dull())
        b = self.browser
        b.eval("Ed.setToolOptions('eyedropper', {sample: 1}); Ed.menu.run('Layer/New Adjustment Layer/Levels...'); Ed.setTool('hand'); true"); b.settle(3)
        # Auto: Auto Tone of what lies below
        b.click_selector('#edPanel-properties [data-action=auto]'); b.settle(3)
        self.assertEqual(self.labels()[-1], 'Modify Levels Layer')
        p = b.eval('Ed.layers.active().adjustment.params')
        want = auto_params('tone', img)
        for c in ('red', 'green', 'blue'): self.assertEqual((p[c]['inBlack'], p[c]['inWhite']), (want[c]['inBlack'], want[c]['inWhite']), c)
        self.assertPixelsClose(b.editor_pixels(), apply_lut(img, levels_luts(p)), max_abs=1)
        b.click_selector('#edPanel-properties [data-action=reset]'); b.settle(2)
        # White-point eyedropper in the panel: samples what lies below the adjustment layer
        b.click_selector('#edPanel-properties .adj-drop-white'); b.settle(1)
        b.click(*b.image_to_client(74.5, 1.5)); b.settle(3)
        p = b.eval('Ed.layers.active().adjustment.params')
        self.assertEqual((p['red']['inWhite'], p['green']['inWhite'], p['blue']['inWhite']), tuple(int(v) for v in img[1, 74, :3]))
        self.assertEqual(self.labels()[-1], 'Modify Levels Layer')
        self.assertEqual(b.editor_pixels()[1, 74, :3].tolist(), [255, 255, 255])
        b.key('Escape'); b.settle(1)
        self.assertFalse(b.eval("document.body.classList.contains('adj-sampling')"))
        adjusted = b.editor_pixels()
        # The mask row: Disable shows the adjustment everywhere, Invert hides it where it showed
        b.eval("Ed.select.rect(0, 0, 64, 96); Ed.select.invert(); true")
        b.eval("(() => { const L = Ed.layers.active(); Ed.history.record('Test Mask', tx => { tx.touch(L, 'mask'); const g = L.mask.getContext('2d'); g.clearRect(0, 0, 64, 96); Ed.update(); }); Ed.select.none(); return true; })()")
        b.settle(3)
        half = img.copy(); half[:, 64:] = adjusted[:, 64:]
        self.assertPixelsClose(b.editor_pixels(), half, msg='the mask hides the left half')
        b.click_selector('#edPanel-properties .adj-mask-toggle'); b.settle(2)
        self.assertEqual(b.text('#edPanel-properties .adj-mask-toggle'), 'Enable')
        self.assertPixelsClose(b.editor_pixels(), adjusted, msg='a disabled mask shows the adjustment everywhere')
        b.click_selector('#edPanel-properties .adj-mask-toggle'); b.settle(2)
        b.click_selector('#edPanel-properties .adj-props-mask button:not(.adj-mask-toggle)'); b.settle(3)
        self.assertEqual(self.labels()[-1], 'Invert')
        inv = adjusted.copy(); inv[:, 64:] = img[:, 64:]
        self.assertPixelsClose(b.editor_pixels(), inv, msg='Invert flips the mask')
        self.assertNoBrowserErrors()

    def test_21_every_adjustment_layer_in_a_row_with_properties_showing(self):
        """Regression: creating every type through the menu, one after another, with Properties showing, logs nothing."""
        self.load()
        b = self.browser
        b.eval("Ed.panels.show('properties'); Ed.test.errors.length = 0; true"); b.settle(2)
        paths = b.eval("Ed.menu.list().filter(m => m.path.startsWith('Layer/New Adjustment Layer/')).map(m => m.path)")
        types = b.eval('Ed.adjust.layerTypes()')
        for i, (t, path) in enumerate(zip(types, paths)):
            b.eval(f"Ed.menu.run({json.dumps(path)}); true"); b.wait_for(f'Ed.doc.layers.length === {i + 2}'); b.settle(3)
            self.assertTrue(b.eval(f"!!document.querySelector('#edPanel-properties [data-adjust={json.dumps(t)}]')"), t)
            # every select in the panel has its options
            self.assertEqual(b.eval("[...document.querySelectorAll('#edPanel-properties select')].filter(s => !s.options.length).map(s => s.name)"), [], t)
        self.assertEqual(b.eval('Ed.test.errors'), [])
        # and back through them all, so each panel is rebuilt over another
        for i in range(len(types)):
            b.eval(f"Ed.layers.setActive({len(types) - i}); true"); b.settle(2)
        self.assertEqual(b.eval('Ed.test.errors'), [])
        self.assertNoBrowserErrors()
