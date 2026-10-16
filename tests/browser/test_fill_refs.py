"""Independent numpy references and helpers shared by the test_fill*.py files (src/web/editor/fill.js, fill-shapes.js,
fill-type.js). No test cases here: the classes that use these live in test_fill.py, test_fill_bucket.py,
test_fill_shapes.py and test_fill_type.py.

Every reference below is written from Photoshop's documented behaviour, not from the module's code:
- gradients: t from the five styles at pixel centres, stops blended in sRGB, midpoint weight P ** (ln .5 / ln m);
- Paint Bucket: the pixels within Tolerance of the clicked one on every channel (premultiplied, so transparency counts),
  4-connected when Contiguous; anti-aliasing softens corners of the outline only;
- compositing: the W3C source-over formula with separable blend modes;
- shapes: exact area coverage by supersampling the geometry.
"""
import json
import math
from collections import deque

import numpy as np
from harness import rgba, premultiplied

KINDS = ['linear', 'radial', 'angle', 'reflected', 'diamond']


# ----- gradients ----------------------------------------------------------------------------------------------------------
def gradient_t(kind, w, h, x0, y0, x1, y1, ox=0, oy=0):
    """Photoshop's five gradient styles at pixel centres: t in 0..1."""
    X, Y = np.meshgrid(np.arange(w) + ox + .5 - x0, np.arange(h) + oy + .5 - y0)
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    if kind == 'linear': t = (X * dx + Y * dy) / l2
    elif kind == 'radial': t = np.sqrt(X * X + Y * Y) / math.sqrt(l2)
    elif kind == 'angle': t = ((math.atan2(dy, dx) - np.arctan2(Y, X)) % (2 * math.pi)) / (2 * math.pi)   # counterclockwise sweep
    elif kind == 'reflected': t = np.abs(X * dx + Y * dy) / l2
    else: t = (np.abs(X * dx + Y * dy) + np.abs(Y * dx - X * dy)) / l2                                     # end point = a corner
    return np.clip(t, 0, 1)


def eval_stops(at, vals, mids, t):
    """Stops blended in sRGB; a midpoint m bends a segment with weight P ** (ln .5 / ln m), m kept in 5..95%."""
    at, vals, mids = np.asarray(at, float), np.asarray(vals, float), np.asarray(mids, float)
    if vals.ndim == 1: vals = vals[:, None]
    n = len(at)
    if n == 1: return np.broadcast_to(vals[0], t.shape + vals.shape[1:]).copy()
    i = np.clip(np.searchsorted(at, t, side='right') - 1, 0, n - 2)
    a, b = at[i], at[i + 1]
    span = b - a
    p = np.where(span > 0, (t - a) / np.where(span > 0, span, 1), 0)
    m = np.clip(mids[i], .05, .95)
    e = np.where(np.abs(m - .5) < 1e-9, 1.0, np.log(.5) / np.log(m))
    w = np.where(e == 1, p, np.power(np.maximum(p, 0), e))
    v = vals[i] + (vals[i + 1] - vals[i]) * w[..., None]
    v = np.where((t <= at[0])[..., None], vals[0], v)
    return np.where((t >= at[-1])[..., None], vals[-1], v)


def gradient_rgba(g, t, reverse=False, transparency=True):
    """uint8 RGBA of gradient g (the shared format) at t, rounded to the nearest level."""
    if reverse: t = 1 - t
    stops = sorted(g['stops'], key=lambda s: s['at'])
    alpha = sorted(g.get('alpha') or [{'at': 0, 'a': 1}, {'at': 1, 'a': 1}], key=lambda s: s['at'])
    rgb = eval_stops([s['at'] for s in stops], [rgba(s['color'])[:3] for s in stops], [s.get('mid', .5) for s in stops], t)
    a = eval_stops([s['at'] for s in alpha], [s['a'] * 255 for s in alpha], [s.get('mid', .5) for s in alpha], t)
    out = np.concatenate([rgb, a if transparency else np.full_like(a, 255)], -1)
    return np.floor(out + .5).astype(np.uint8)


BW = {'stops': [{'at': 0, 'color': '#000000'}, {'at': 1, 'color': '#ffffff'}], 'alpha': [{'at': 0, 'a': 1}, {'at': 1, 'a': 1}]}
MULTI = {'stops': [{'at': 0, 'color': '#ff0000', 'mid': .25}, {'at': .4, 'color': '#00ff00', 'mid': .8}, {'at': .4, 'color': '#2040ff'},
                   {'at': .75, 'color': '#ffff00', 'mid': .02}, {'at': 1, 'color': '#101010'}],
         'alpha': [{'at': .1, 'a': 1}, {'at': .6, 'a': .3, 'mid': .7}, {'at': .9, 'a': 1}]}


# ----- compositing ----------------------------------------------------------------------------------------------------------
def over(dst, src, mode='normal', k=1.0):
    """W3C source-over with a separable blend mode (uint8 RGBA in and out). src alpha may be a float coverage array."""
    b, s = dst.astype(np.float64) / 255, src.astype(np.float64) / 255
    cb, ab, cs, a_s = b[..., :3], b[..., 3:], s[..., :3], s[..., 3:] * k
    mix = {'normal': lambda: cs, 'multiply': lambda: cb * cs, 'screen': lambda: cb + cs - cb * cs, 'linear-dodge': lambda: np.minimum(1, cb + cs),
           'difference': lambda: np.abs(cb - cs), 'darken': lambda: np.minimum(cb, cs), 'lighten': lambda: np.maximum(cb, cs)}[mode]()
    ao = a_s + ab * (1 - a_s)
    co = a_s * (1 - ab) * cs + a_s * ab * np.clip(mix, 0, 1) + (1 - a_s) * ab * cb
    with np.errstate(divide='ignore', invalid='ignore'):
        rgb = np.where(ao > 0, co / np.where(ao > 0, ao, 1), 0)
    return np.floor(np.concatenate([rgb, ao], -1) * 255 + .5).astype(np.uint8)


def paint(dst, color, cover, k=1.0, mode='normal', lock_alpha=False):
    """dst painted with a solid color at coverage `cover` (0..1 float, H x W) and opacity k. lock_alpha keeps dst's alpha
    (Photoshop's Lock Transparent Pixels: the color moves toward the result)."""
    src = np.zeros(dst.shape, np.float64)
    src[..., :3] = rgba(color)[:3]
    src[..., 3] = np.asarray(cover, np.float64) * 255
    out = over(dst, src, mode, k)
    if lock_alpha:
        a = cover * k
        mixed = dst[..., :3] * (1 - a[..., None]) + np.asarray(rgba(color)[:3], float) * a[..., None]
        out = dst.copy()
        out[..., :3] = np.where(dst[..., 3:] > 0, np.floor(mixed + .5), dst[..., :3])
    return out


def through_selection(before, after, sel):
    """Photoshop merges an edit back through a soft selection: before + (after - before) * s."""
    s = np.asarray(sel, np.float64)[..., None] / 255
    return np.floor(before * (1 - s) + after * s + .5).astype(np.uint8)


def solid(w, h, color=(255, 255, 255, 255)):
    return np.broadcast_to(np.array(rgba(color) if isinstance(color, str) else color, np.uint8), (h, w, 4)).copy()


def noise_image(w, h, seed=7, opaque=True):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    if opaque: a[..., 3] = 255
    return a


# ----- Paint Bucket ---------------------------------------------------------------------------------------------------------
def bucket_region(img, x, y, tol, contiguous=True, sel=None):
    """Photoshop's Paint Bucket area: every channel (premultiplied) within tol of the clicked pixel, inside the selection,
    4-connected to the click when contiguous."""
    a = img[..., 3].astype(np.int64)
    pm = img[..., :3].astype(np.int64) * a[..., None]
    sa = int(img[y, x, 3])
    spm = img[y, x, :3].astype(np.int64) * sa
    ok = (np.abs(a - sa) <= tol) & (np.abs(pm - spm) <= tol * 255).all(-1)
    if sel is not None: ok &= sel > 0
    if not contiguous: return ok
    h, w = ok.shape
    out = np.zeros_like(ok)
    if not ok[y, x]: return out
    q = deque([(x, y)])
    out[y, x] = True
    while q:
        cx, cy = q.popleft()
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if 0 <= nx < w and 0 <= ny < h and ok[ny, nx] and not out[ny, nx]:
                out[ny, nx] = True
                q.append((nx, ny))
    return out


def bucket_coverage(region, antialias):
    """0..1 coverage of a filled region. Anti-aliased: the outline is cut diagonally across every corner of the pixel
    staircase (through the midpoints of the pixel edges), so a pixel quarter at a convex corner keeps half of itself and an
    outside quarter in a concave corner gains half. Straight edges stay crisp. Outside the image counts as 'same as here'."""
    r = region.astype(np.float64)
    if not antialias: return r
    h, w = r.shape
    pad = np.pad(region, 1, mode='edge')
    cov = np.zeros((h, w))
    for dx in (-1, 1):
        for dy in (-1, 1):
            hz = pad[1:h + 1, 1 + dx:w + 1 + dx]
            vt = pad[1 + dy:h + 1 + dy, 1:w + 1]
            dg = pad[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx]
            inside_q = np.where(~hz & ~vt, .125, .25)
            outside_q = np.where(hz & vt & dg, .125, 0)
            cov += np.where(region, inside_q, outside_q)
    return cov


# ----- distances (Edit > Stroke) --------------------------------------------------------------------------------------------
def distance_to(mask_from, mask_to):
    """For each pixel of mask_from: the distance between its centre and the nearest centre of a mask_to pixel (inf elsewhere)."""
    h, w = mask_from.shape
    ty, tx = np.nonzero(mask_to)
    out = np.full((h, w), np.inf)
    fy, fx = np.nonzero(mask_from)
    if not len(ty) or not len(fy): return out
    best = np.full(len(fy), np.inf)
    for i in range(0, len(ty), 256):
        d = np.hypot(fx[:, None] - tx[None, i:i + 256], fy[:, None] - ty[None, i:i + 256]).min(1)
        best = np.minimum(best, d)
    out[fy, fx] = best
    return out


# ----- shapes ---------------------------------------------------------------------------------------------------------------
def supersample(w, h, inside, ss=8):
    """Area coverage (0..1) of each pixel by the set inside(X, Y) -> bool, sampled ss x ss times per pixel."""
    o = (np.arange(ss) + .5) / ss
    X = (np.arange(w)[:, None] + o[None, :]).ravel()
    Y = (np.arange(h)[:, None] + o[None, :]).ravel()
    XX, YY = np.meshgrid(X, Y)
    return inside(XX, YY).reshape(h, ss, w, ss).mean((1, 3))


def in_round_rect(x0, y0, x1, y1, r):
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    def f(X, Y):
        ins = (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
        if r <= 0: return ins
        cx = np.clip(X, x0 + r, x1 - r)
        cy = np.clip(Y, y0 + r, y1 - r)
        return ins & ((X - cx) ** 2 + (Y - cy) ** 2 <= r * r)
    return f


def in_ellipse(cx, cy, rx, ry):
    return lambda X, Y: ((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2 <= 1


def in_polygon(pts):
    pts = np.asarray(pts, float)
    def f(X, Y):
        ins = np.zeros(X.shape, bool)
        n = len(pts)
        for i in range(n):
            (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
            cross = (ya > Y) != (yb > Y)
            with np.errstate(divide='ignore', invalid='ignore'):
                xi = xa + (Y - ya) * (xb - xa) / (yb - ya)
            ins ^= cross & (X < xi)
        return ins
    return f


def polygon_points(x0, y0, x1, y1, sides):
    """A regular polygon with a corner at the top, scaled to fill the box (x0, y0)-(x1, y1)."""
    a = -math.pi / 2 + np.arange(sides) * 2 * math.pi / sides
    px, py = np.cos(a), np.sin(a)
    px = x0 + (px - px.min()) / (px.max() - px.min()) * (x1 - x0)
    py = y0 + (py - py.min()) / (py.max() - py.min()) * (y1 - y0)
    return list(zip(px, py))


def line_points(xa, ya, xb, yb, weight):
    dx, dy = xb - xa, yb - ya
    L = math.hypot(dx, dy)
    nx, ny = -dy / L * weight / 2, dx / L * weight / 2
    return [(xa + nx, ya + ny), (xb + nx, yb + ny), (xb - nx, yb - ny), (xa - nx, ya - ny)]


def dist_to_segments(pts):
    pts = np.asarray(pts, float)
    def d(X, Y):
        best = np.full(X.shape, np.inf)
        n = len(pts)
        for i in range(n):
            (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
            vx, vy = xb - xa, yb - ya
            t = np.clip(((X - xa) * vx + (Y - ya) * vy) / (vx * vx + vy * vy), 0, 1)
            best = np.minimum(best, np.hypot(X - xa - t * vx, Y - ya - t * vy))
        return best
    return d


def shape_layer(w, h, inside, fill=None, stroke=None, width=0, inner=None, ss=8):
    """RGBA of a shape: `inside` covers the shape, `inner` the part farther than the stroke width from its edge (the stroke
    sits inside the outline). fill / stroke are '#rrggbb' or None."""
    cov = supersample(w, h, inside, ss)
    out = np.zeros((h, w, 4), np.float64)
    if stroke and width > 0:
        icov = supersample(w, h, inner, ss) if inner else np.zeros((h, w))
        band = np.clip(cov - icov, 0, 1)
        top = icov if fill else np.zeros((h, w))
        a = band + top
        col = np.zeros((h, w, 3))
        col += band[..., None] * np.asarray(rgba(stroke)[:3], float)
        if fill: col += top[..., None] * np.asarray(rgba(fill)[:3], float)
        with np.errstate(divide='ignore', invalid='ignore'):
            out[..., :3] = np.where(a[..., None] > 0, col / np.where(a > 0, a, 1)[..., None], 0)
        out[..., 3] = a * 255
    elif fill:
        out[..., :3] = np.where(cov[..., None] > 0, np.asarray(rgba(fill)[:3], float), 0)
        out[..., 3] = cov * 255
    return np.floor(out + .5).astype(np.uint8)


# ----- page helpers ---------------------------------------------------------------------------------------------------------
# Every element of the options bar must sit inside it, and the scrolling tool options must not need to scroll.
SPILL_JS = """(() => { const bar = document.querySelector('.ed-optionsbar'), opts = document.querySelector('.ed-options'), p = bar.getBoundingClientRect(), q = opts.getBoundingClientRect();
  const out = [...bar.querySelectorAll('*')].filter(e => { if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden') return false;
    const r = e.getBoundingClientRect(), box = opts.contains(e) ? q : p;
    return r.width > 0 && (r.right > box.right + 1 || r.left < box.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); })
    .map(e => (e.id || e.className || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 20));
  if (opts.scrollWidth > opts.clientWidth + 1) out.push('options scroll ' + opts.scrollWidth + ' > ' + opts.clientWidth);
  const cut = [...bar.querySelectorAll('label, button, select')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible')
    .map(e => 'cut:' + (e.id || e.className) + ':' + e.textContent.trim().slice(0, 20));
  return out.concat(cut); })()"""


def dialog_box_js(title):
    return f"""(() => {{ const d = document.querySelector('dialog[data-ed-dialog={json.dumps(title)}]'); if (!d) return null; const r = d.getBoundingClientRect();
      const f = d.querySelector('.ed-dialog-fields'); return {{left: r.left, top: r.top, right: r.right, bottom: r.bottom, vw: innerWidth, vh: innerHeight,
      fieldsScroll: f ? f.scrollHeight - f.clientHeight : 0, wide: [...d.querySelectorAll('*')].filter(e => e.getClientRects().length && e.getBoundingClientRect().right > r.right + 1).map(e => e.className || e.tagName) }}; }})()"""


class FillHelpers:
    """Mixed into the EditorTestCase classes of the test_fill*.py files."""

    def fresh(self, array):
        b = self.browser
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); const t = document.getElementById('toast'); if (t) t.hidden = true; Ed.select.none && Ed.doc && Ed.selection && Ed.select.none(); true")
        b.load_editor_image(array)
        b.eval('Ed.view.fit()')
        b.settle()
        return b

    def tool(self, name, **options):
        b = self.browser
        b.eval(f'Ed.setTool({json.dumps(name)})' + (f';Ed.setToolOptions({json.dumps(name)}, {json.dumps(options)})' if options else ''))
        b.settle()

    def labels(self):
        return self.browser.eval('Ed.history.list().map(s => s.label)')

    def wait_history(self, n, timeout=20):
        self.browser.wait_for(f'Ed.history.list().length >= {n} && !Ed.history.open', timeout)
        self.browser.settle()

    def undo_all(self):
        """Ctrl+Z once per history state, back to the opened picture."""
        b = self.browser
        for _ in range(b.eval('Ed.history.index')):
            n = b.eval('Ed.history.index')
            b.key('Ctrl+Z')
            b.wait_for(f'Ed.history.index === {n - 1}')
        b.settle()

    def assertOptionsFit(self, tools, sizes=((1366, 768), (1920, 1080)), shot=None):
        b = self.browser
        try:
            for w, h in sizes:
                b.set_viewport(w, h)
                for t in tools:
                    b.eval(f'Ed.setTool({json.dumps(t)})')
                    b.settle(3)
                    self.assertEqual(b.eval(SPILL_JS), [], f'the {t} options fit the options bar at {w}x{h}')
                    if shot: b.screenshot(f'{shot}-{t}-{w}x{h}.png')
        finally:
            b.set_viewport(*self.viewport)
            b.settle(3)

    def assertPremulClose(self, actual, expected, max_abs=1, **kw):
        """Compare the way a canvas stores pixels (RGB times alpha): half-transparent colors round, transparent ones vanish."""
        self.assertPixelsClose(premultiplied(actual), premultiplied(expected), max_abs=max_abs, **kw)
