"""Adversarial verification of the layered editor core (src/web/editor/core.js, shell.js and the editor parts of app.js).

Independent of test_core.py: its own numpy reference compositor (W3C Compositing and Blending Level 1, computed on
premultiplied floats, compared in the premultiplied 8-bit space a canvas really stores), long random history sequences
checked state by state, real mouse / pen / keyboard input for painting and shortcuts, save -> reload round trips, hostile
documents sent straight to the server, the old features, layout checks at three window sizes and stroke frame times.
Error tables and screenshots go to tests/browser/output/ (verify-*.txt, verify-*.png).

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p test_core_verify.py -v
"""
import base64
import io
import json
import random
import statistics
import time
import urllib.request
import numpy as np
from PIL import Image
from harness import ALT, EditorTestCase, GPU, OUTPUT, distance_to_path, image_array, premultiplied, rgba, write_png
from fakegame import PHOTO, SPRITE

NATIVE = ['normal', 'darken', 'multiply', 'color-burn', 'lighten', 'screen', 'color-dodge', 'linear-dodge', 'overlay', 'soft-light', 'hard-light',
          'difference', 'exclusion', 'hue', 'saturation', 'color', 'luminosity']


# ===== an independent reference compositor ============================================================================
# Everything is premultiplied float RGBA in 0..1 (what a canvas computes with), quantised to 8 bits only for comparison.
def lum(c): return c[..., 0] * 0.3 + c[..., 1] * 0.59 + c[..., 2] * 0.11


def clip_color(c):
    """W3C ClipColor: n and x come from the colour before either correction."""
    l, n, x = lum(c)[..., None], c.min(-1, keepdims=True), c.max(-1, keepdims=True)
    out = c.copy()
    low = np.broadcast_to(n < 0, c.shape)
    with np.errstate(divide='ignore', invalid='ignore'):
        out = np.where(low, l + (out - l) * l / (l - n), out)
        out = np.where(np.broadcast_to(x > 1, c.shape), l + (out - l) * (1 - l) / (x - l), out)
    return out


def set_lum(c, l): return clip_color(c + (l - lum(c))[..., None])


def set_sat(c, s):
    """W3C SetSat with the max / mid / min channels found by a stable sort."""
    order = np.argsort(c, axis=-1, kind='stable')
    lo, mid, hi = (np.take_along_axis(c, order[..., i:i + 1], -1) for i in range(3))
    span = hi - lo
    with np.errstate(divide='ignore', invalid='ignore'):
        new_mid = np.where(span > 0, (mid - lo) * s[..., None] / span, 0.0)
    new_hi = np.where(span > 0, s[..., None], 0.0)
    out = np.empty_like(c)
    np.put_along_axis(out, order[..., 0:1], np.zeros_like(lo), -1)
    np.put_along_axis(out, order[..., 1:2], new_mid, -1)
    np.put_along_axis(out, order[..., 2:3], new_hi, -1)
    return out


def sat(c): return c.max(-1) - c.min(-1)


def mix(mode, cb, cs):
    """B(Cb, Cs) on unpremultiplied colours. linear-dodge is Photoshop's Linear Dodge (Add): min(1, Cb + Cs) through the
    general formula (the canvas 'lighter' operator the core uses differs for half-transparent pixels)."""
    def hard(b, s): return np.where(s <= 0.5, b * 2 * s, b + (2 * s - 1) - b * (2 * s - 1))
    with np.errstate(divide='ignore', invalid='ignore'):
        if mode == 'normal': return cs
        if mode == 'multiply': return cb * cs
        if mode == 'screen': return cb + cs - cb * cs
        if mode == 'overlay': return hard(cs, cb)
        if mode == 'darken': return np.minimum(cb, cs)
        if mode == 'lighten': return np.maximum(cb, cs)
        if mode == 'color-dodge': return np.where(cb == 0, 0.0, np.where(cs >= 1, 1.0, np.minimum(1, cb / (1 - cs))))
        if mode == 'color-burn': return np.where(cb >= 1, 1.0, np.where(cs <= 0, 0.0, 1 - np.minimum(1, (1 - cb) / cs)))
        if mode == 'hard-light': return hard(cb, cs)
        if mode == 'soft-light':
            d = np.where(cb <= 0.25, ((16 * cb - 12) * cb + 4) * cb, np.sqrt(cb))
            return np.where(cs <= 0.5, cb - (1 - 2 * cs) * cb * (1 - cb), cb + (2 * cs - 1) * (d - cb))
        if mode == 'difference': return np.abs(cb - cs)
        if mode == 'exclusion': return cb + cs - 2 * cb * cs
        if mode == 'linear-dodge': return np.minimum(1, cb + cs)
        if mode == 'hue': return set_lum(set_sat(cs, sat(cb)), lum(cb))
        if mode == 'saturation': return set_lum(set_sat(cb, sat(cs)), lum(cb))
        if mode == 'color': return set_lum(cs, lum(cb))
        if mode == 'luminosity': return set_lum(cb, lum(cs))
    raise KeyError(mode)


def to_float(px):
    """getImageData bytes (unpremultiplied 8-bit) -> the premultiplied floats the canvas holds. getImageData divides the
    stored premultiplied bytes by alpha and rounds; multiplying back and rounding recovers the stored bytes exactly."""
    p = premultiplied(px).astype(np.float64) / 255
    return p


def unpremul(p):
    a = p[..., 3:4]
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(a > 0, p[..., :3] / np.where(a > 0, a, 1), 0.0)


def over(dst, src, mode='normal', k=1.0):
    """The W3C general formula: src (premultiplied, its alpha scaled by k) blended with mode and composited source-over dst."""
    k = np.asarray(k, np.float64)
    if k.ndim == 2: k = k[..., None]
    a_s, a_b = src[..., 3:4] * k, dst[..., 3:4]
    cs, cb = unpremul(src), unpremul(dst)
    b = np.clip(mix(mode, cb, cs), 0, 1)
    co = a_s * (1 - a_b) * cs + a_s * a_b * b + (1 - a_s) * a_b * cb
    return np.concatenate([co, a_s + a_b * (1 - a_s)], -1)


def lerp_toward(dst, result, w):
    """Premultiplied interpolation from dst toward result by w (how a fade by opacity acts on a whole group)."""
    w = np.asarray(w, np.float64)
    if w.ndim == 2: w = w[..., None]
    return dst * (1 - w) + result * w


def q8(p): return np.round(np.clip(p, 0, 1) * 255).astype(np.int64)


def stored(p):
    """What an 8-bit premultiplied canvas keeps of a premultiplied float picture (an intermediate group or masked copy)."""
    return np.round(np.clip(p, 0, 1) * 255) / 255


def errors(got_px, want_p, mask=None):
    """Max and mean absolute difference in the premultiplied 8-bit space (all four channels)."""
    d = np.abs(premultiplied(got_px).astype(np.int64) - q8(want_p))
    if mask is not None: d = d[np.asarray(mask, bool)]
    return (int(d.max()) if d.size else 0), (float(d.mean()) if d.size else 0.0)


def random_layer(rng, h, w, alphas=(0, 1, 3, 9, 40, 64, 128, 180, 230, 254, 255)):
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    a[..., 3] = np.array(alphas, np.uint8)[rng.integers(0, len(alphas), (h, w))]
    return a


def decode_png(data): return np.array(Image.open(io.BytesIO(data)).convert('RGBA'))


# In-page helpers: a layer from pushed pixels, and a fingerprint of the whole document for history checks.
ADD = """const im={expr};const c=document.createElement('canvas');c.width=im.width;c.height=im.height;c.getContext('2d').putImageData(im,0,0);
  const L=Ed.layers.add({{name:{name},canvas:c,history:false}});Ed.layers.set(L,{props},{{history:false}});Ed.layers.index(L)"""
# Read through Ed.util.read: getImageData on a layer canvas itself moves it to software rendering, after which Chrome was seen
# to hand a just-duplicated layer a stale copy of its source (the core never reads its canvases directly).
FINGERPRINT = """(() => { const h = c => { if (!c) return null; const d = new Uint32Array(Ed.util.read(c).data.buffer);
    let x = 2166136261; for (let i = 0; i < d.length; i++) x = Math.imul(x ^ d[i], 16777619); return x >>> 0; };
  return {layers: Ed.doc.layers.map(L => [L.id, L.name, L.kind, L.visible, L.opacity, L.fill, L.blend, L.clip, JSON.stringify(L.locks), L.maskEnabled,
      h(L.canvas), h(L.mask), JSON.stringify(L.effects), JSON.stringify(L.adjustment), JSON.stringify(L.data)]), selection: h(Ed.selection)}; })()"""


class EditorCoreVerify(EditorTestCase):
    viewport = (1600, 1000)
    feature_modules = False   # the core alone, as it works without any module (the modules have their own tests)
    report = {}   # name -> text, written to output/verify-report.txt at the end

    @classmethod
    def tearDownClass(cls):
        if cls.report:
            (OUTPUT / 'verify-report.txt').write_text('\n\n'.join(f'== {k} ==\n{v}' for k, v in sorted(cls.report.items())) + '\n', 'utf-8')
        super().tearDownClass()

    # ----- helpers -----------------------------------------------------------------------------------------------------
    def state(self): return self.browser.eval('Ed.test.state')

    def labels(self): return self.browser.eval('Ed.history.list().map(s => s.label)')

    def add_layer(self, array, name='Layer', **props):
        b = self.browser
        return b.eval(ADD.format(expr=b.push_pixels(array), name=json.dumps(name), props=json.dumps(props)))

    def set_mask(self, index, alpha):
        """Give layer `index` a mask with these alpha values (H, W uint8), without history."""
        b = self.browser
        m = np.zeros(alpha.shape + (4,), np.uint8); m[..., :3] = 255; m[..., 3] = alpha
        b.eval(f"const im={b.push_pixels(m)};const c=document.createElement('canvas');c.width=im.width;c.height=im.height;"
               f"c.getContext('2d').putImageData(im,0,0);Ed.layers.set({index},{{mask:c}},{{history:false}});true")

    def layer_pm(self, i):
        """Layer i as the premultiplied floats its canvas holds."""
        return to_float(self.browser.editor_pixels(i))

    def mask_of(self, i):
        return self.browser.pixels(f'Ed.layers.get({i}).mask')[..., 3].astype(np.float64) / 255

    def fresh(self, name='edit-photo.png'):
        b = self.browser
        b.select_asset(name)
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true; Ed.select.none(); true")
        return b

    def fingerprint(self): return self.browser.eval(FINGERPRINT)

    def blank_page_focus(self):
        self.browser.eval("document.activeElement && document.activeElement.blur && document.activeElement.blur(); true")

    # ===== 1. the API contract ==========================================================================================
    def test_01_ready_doc_and_test_hooks(self):
        b = self.browser
        self.assertTrue(b.eval('(await Ed.ready) === Ed'), 'Ed.ready resolves with Ed')
        self.assertIsNone(b.eval('Ed.doc'), 'no document before an asset opens')
        self.assertIsNone(b.eval('Ed.layers.active()'))
        self.assertEqual(b.eval('Ed.layers.count()'), 0)
        self.assertEqual(b.eval("Ed.editable()"), 'Open an image first.')
        self.assertEqual(b.eval("Ed.view.blocked"), 'Open an image first.')
        self.assertIsNone(b.eval("Ed.sample(1, 1)"))
        self.assertFalse(b.eval("Ed.history.undo()"))
        art = random_layer(np.random.default_rng(1), 9, 13)
        self.assertTrue(b.eval(f"(async () => {{ const r = Ed.test.load(13, 9, {json.dumps(base64.b64encode(art.tobytes()).decode())}); return r instanceof Promise && (await r) === true; }})()"))
        self.assertEqual((b.eval('Ed.doc.width'), b.eval('Ed.doc.height'), b.eval('Ed.doc.layers.length')), (13, 9, 1))
        got = b.editor_pixels()
        self.assertEqual(got.shape, (9, 13, 4))
        self.assertPixelsClose(premultiplied(got), premultiplied(art), msg='load -> composite (premultiplied storage)')
        self.assertPixelsClose(premultiplied(b.editor_pixels(0)), premultiplied(art), msg="layer 0 by number")
        self.assertPixelsClose(premultiplied(b.editor_pixels('0')), premultiplied(art), msg="layer 0 by a digit string")
        self.assertEqual(int(b.editor_pixels('selection')[..., 3].max()), 0, 'no selection reads all 0')
        self.assertEqual(int(b.editor_pixels('mask')[..., 3].min()), 255, 'no mask reads all 255')
        with self.assertRaises(Exception): b.editor_pixels(5)
        st = self.state()
        self.assertEqual((st['activeIndex'], st['target'], st['history'], st['index'], st['selection']), (0, 'pixels', 1, 0, False))
        self.assertTrue(b.eval('Ed.test.ready()'))
        self.assertIsInstance(b.eval('Ed.test.errors'), list)

    def test_02_tools_and_options_contract(self):
        b = self.browser
        b.load_editor_image(np.full((16, 16, 4), 255, np.uint8))
        self.assertFalse(b.eval("Ed.setTool('no-such-tool')"))
        self.assertTrue(b.eval("Ed.setTool('brush')"))
        self.assertEqual((b.eval('Ed.tool'), b.eval('S.tool')), ('brush', 'brush'))
        opts = b.eval("Ed.toolOptions('brush')")
        self.assertEqual(sorted(opts), ['hardness', 'opacity', 'pressure', 'size'])
        self.assertEqual(b.eval("Ed.toolOptions()"), opts, 'no id = the active tool')
        # Clamped and type-checked.
        b.eval("Ed.setToolOptions('brush', {size: 0, hardness: 250, opacity: -5})")
        self.assertEqual([b.eval(f"Ed.toolOptions('brush').{k}") for k in ('size', 'hardness', 'opacity')], [1, 100, 1])
        b.eval("Ed.setToolOptions('brush', {size: 'abc'})")
        self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 1, 'a non-number is ignored')
        b.eval("Ed.setToolOptions('brush', {size: 1e9})")
        self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 1000)
        b.eval("Ed.setToolOptions('brush', {pressure: 'yes'})")
        self.assertIs(b.eval("Ed.toolOptions('brush').pressure"), True)
        b.eval("Ed.setToolOptions('eyedropper', {sample: 7, from: 'nowhere'})")
        self.assertEqual((b.eval("Ed.toolOptions('eyedropper').sample"), b.eval("Ed.toolOptions('eyedropper').from")), (1, 'composite'),
                         'a select option refuses values that are not in its list')
        b.eval("Ed.setToolOptions('eyedropper', {sample: 3})")
        self.assertEqual(b.eval("Ed.toolOptions('eyedropper').sample"), 3)
        b.eval("Ed.setToolOptions('zoom', {direction: 'sideways'})")
        self.assertEqual(b.eval("Ed.toolOptions('zoom').direction"), 'in', 'a segmented option too')
        # The options event and per-tool persistence.
        b.eval("window.__opt = []; Ed.on('options', e => __opt.push(e.id)); Ed.setToolOptions('brush', {size: 42, pressure: false})")
        self.assertEqual(b.eval('__opt'), ['brush'])
        self.assertEqual(b.eval("JSON.parse(localStorage.getItem('ed-tool:brush')).size"), 42)
        self.assertFalse(b.eval("Ed.setToolOptions('nope', {size: 3})"))
        b.eval("Ed.setToolOptions('eyedropper', {sample: 1}); Ed.setToolOptions('brush', {size: 12, hardness: 100, opacity: 100})")

    def test_03_menu_and_history_contract(self):
        b = self.browser
        b.load_editor_image(np.zeros((8, 8, 4), np.uint8))
        rows = b.eval("Ed.menu.list()")
        self.assertTrue(all(set(r) == {'path', 'shortcut', 'enabled', 'checked'} for r in rows))
        by = {r['path']: r for r in rows}
        self.assertFalse(by['Edit/Undo']['enabled'])
        self.assertFalse(by['Layer/Delete Layer']['enabled'], 'one layer cannot be deleted')
        self.assertEqual(by['Edit/Redo']['shortcut'], 'Ctrl+Shift+Z')
        self.assertTrue(b.eval("Ed.menu.run('Layer/New Layer', {name: 'Named'})"))
        self.assertEqual(self.state()['layers'][-1]['name'], 'Named')
        self.assertFalse(b.eval("Ed.menu.run('Select/Deselect')"), 'disabled: false and nothing runs')
        self.assertIn('There is no menu command', b.eval("(() => { try { Ed.menu.run('Nope/Nothing'); } catch (e) { return e.message; } })()"))
        # A command that throws is reported, not raised.
        b.eval("Ed.registerMenu('Filter/Verify/Throws', {action() { throw Error('menu boom'); }})")
        self.assertFalse(b.eval("Ed.menu.run('Filter/Verify/Throws')"))
        self.assertIn('menu boom', b.eval("Ed.test.errors.at(-1).message"))
        self.assertNoBrowserErrors(allow=['menu boom'])
        b.eval("Ed.registerMenu('Filter/Verify/Throws', {enabled: () => false})")
        # History: list, label, canUndo/canRedo, index, jump clamping, undo/redo return values.
        b.eval("Ed.layers.set(1, {opacity: 0.5}); Ed.layers.set(1, {blend: 'screen'})")
        self.assertEqual(b.eval("Ed.history.list()"), [{'index': 0, 'label': 'Open', 'current': False, 'undone': False},
                                                         {'index': 1, 'label': 'New Layer', 'current': False, 'undone': False},
                                                         {'index': 2, 'label': 'Opacity Change', 'current': False, 'undone': False},
                                                         {'index': 3, 'label': 'Blending Change', 'current': True, 'undone': False}])
        self.assertTrue(b.eval("Ed.history.undo()"))
        self.assertEqual((b.eval('Ed.history.index'), b.eval('Ed.history.canRedo()'), b.eval("Ed.history.label(3)")), (2, True, 'Blending Change'))
        self.assertTrue(b.eval("Ed.history.redo()"))
        self.assertFalse(b.eval("Ed.history.redo()"))
        self.assertTrue(b.eval("Ed.history.jump(-5)"))
        self.assertEqual((b.eval('Ed.history.index'), len(self.state()['layers'])), (0, 1), 'jump clamps to the first state')
        self.assertFalse(b.eval("Ed.history.jump(0)"), 'already there')
        self.assertTrue(b.eval("Ed.history.jump(99)"))
        self.assertEqual(b.eval('Ed.history.index'), 3)
        st = self.state()['layers'][1]
        self.assertEqual((st['opacity'], st['blend']), (0.5, 'screen'))
        # record(): a throw cancels everything the transaction did, an async fn commits when it settles.
        before = self.fingerprint()
        self.assertIn('rec boom', b.eval("(() => { try { Ed.history.record('Bad', tx => { Ed.layers.add({history: false}); Ed.layers.set(0, {opacity: .1}, {history: false}); throw Error('rec boom'); }); } catch (e) { return e.message; } })()"))
        self.assertEqual(self.fingerprint(), before, 'a throwing record leaves no trace')
        self.assertEqual(len(self.labels()), 4)
        self.assertEqual(b.eval("(async () => { await Ed.history.record('Async', async tx => { await new Promise(r => setTimeout(r, 20)); Ed.layers.set(0, {opacity: .25}, {history: false}); }); return Ed.history.label(Ed.history.index); })()"), 'Async')
        self.assertEqual(self.state()['layers'][0]['opacity'], 0.25)

    # ===== 2. view mapping, checked against what is really on screen ======================================================
    def test_04_view_to_client_matches_the_screen_at_several_zooms_and_pans(self):
        b = self.browser
        art = np.zeros((64, 64, 4), np.uint8); art[..., 3] = 255
        art[30, 20] = (255, 255, 255, 255)                 # one white pixel
        art[8:24, 40:56] = (255, 0, 0, 255)                # a red 16 x 16 block
        b.load_editor_image(art)
        b.eval("Ed.setTool('hand'); document.getElementById('compare').checked = false; Ed.view.pixelGrid = false; true")   # the grid draws lines on pixel edges
        self.addCleanup(lambda: b.eval("Ed.view.pixelGrid = true"))
        b.mouse_move(5, 5)   # keep the brush outline and hover effects away from the view
        results = []
        for zoom, px, py in ((8, 0, 0), (8, 37.3, -12.6), (3, -15.5, 22.25), (16, -150, 90.4), (1, 0, 0), (1, 13.5, -7.5), (2, 3.3, 3.7), (0.5, 0, 0)):
            b.eval(f"Ed.view.setState({{zoom: {zoom}, px: {px}, py: {py}}})")
            b.settle(3)
            for (x, y, w, h, color) in ((20, 30, 1, 1, (255, 255, 255)), (40, 8, 16, 16, (255, 0, 0))):
                (x0, y0), (x1, y1) = b.image_to_client(x, y), b.image_to_client(x + w, y + h)
                self.assertAlmostEqual(x1 - x0, w * zoom, places=6)
                pad = 3
                shot = b.screenshot_array(clip=(np.floor(x0) - pad, np.floor(y0) - pad, np.ceil(x1 - x0) + 2 * pad, np.ceil(y1 - y0) + 2 * pad)).astype(int)
                hit = (np.abs(shot[..., :3] - np.array(color)).max(-1) <= 40)
                if w * zoom >= 2:
                    ys, xs = np.nonzero(hit)
                    self.assertTrue(len(xs), f'the {color} shape is on screen at zoom {zoom}, pan ({px}, {py})')
                    left, top = np.floor(x0) - pad + xs.min(), np.floor(y0) - pad + ys.min()
                    right, bottom = np.floor(x0) - pad + xs.max() + 1, np.floor(y0) - pad + ys.max() + 1
                    err = max(abs(left - x0), abs(top - y0), abs(right - x1), abs(bottom - y1))
                    results.append((zoom, px, py, (x, y), round(float(err), 3)))
                    self.assertLessEqual(err, 1.0, f'zoom {zoom}, pan ({px}, {py}): the {w}x{h} shape at image ({x}, {y}) is drawn at '
                                                   f'[{left}, {top}, {right}, {bottom}], Ed.view.toClient says [{x0:.2f}, {y0:.2f}, {x1:.2f}, {y1:.2f}]')
                # Round trip.
                for got, want in zip(b.client_to_image(x0, y0), (x, y)): self.assertAlmostEqual(got, want, places=6)
            # At exactly 100% every image pixel is one screen pixel: crisp, not smeared over its neighbours.
            if zoom == 1:
                x0, y0 = b.image_to_client(20, 30)
                shot = b.screenshot_array(clip=(np.floor(x0) - 2, np.floor(y0) - 2, 5, 5)).astype(int)
                bright = shot[..., :3].max(-1)
                self.assertEqual(int((bright > 200).sum()), 1, f'at 100% (pan {px}, {py}) the single white pixel covers exactly one screen pixel; got\n{bright}')
                self.assertEqual(int(((bright > 30) & (bright <= 200)).sum()), 0, f'and nothing around it is gray (smoothing a half-pixel offset); got\n{bright}')
        self.report['view mapping'] = '\n'.join(f'zoom {z} pan ({px}, {py}) shape at {xy}: worst edge error {e} px' for z, px, py, xy, e in results)
        b.eval("Ed.view.fit()")

    def test_04b_brush_outline_and_marching_ants_line_up_with_the_picture(self):
        b = self.browser
        b.load_editor_image(np.full((64, 64, 4), 90, np.uint8))
        b.eval("Ed.view.pixelGrid = false; Ed.view.setState({zoom: 4, px: 13.3, py: -7.6}); Ed.setTool('brush'); Ed.setToolOptions('brush', {size: 10}); true")
        self.addCleanup(lambda: b.eval("Ed.view.pixelGrid = true"))
        overlay = lambda: b.pixels("document.getElementById('viewOverlay')")[..., 3]
        # The brush outline: a circle of size x zoom / 2 around the pointer.
        x, y = b.image_to_client(30, 30)
        b.mouse_move(x, y); b.settle(3)
        box = lambda s: b.eval(f"(() => {{ const r = document.querySelector('{s}').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }})()")
        self.assertEqual(box('#viewOverlay'), box('#viewport'), 'the overlay (shown now) covers the viewport exactly')
        ox, oy = box('#viewOverlay')[:2]
        ys, xs = np.nonzero(overlay())
        self.assertTrue(len(xs), 'the brush outline is drawn')
        cx, cy, r = (xs.min() + xs.max() + 1) / 2 + ox, (ys.min() + ys.max() + 1) / 2 + oy, (xs.max() + 1 - xs.min()) / 2
        self.assertLessEqual(max(abs(cx - x), abs(cy - y)), 1.5, f'outline centred on the pointer: ({cx}, {cy}) vs ({x}, {y})')
        self.assertAlmostEqual(r, 10 * 4 / 2, delta=2.5)
        # Marching ants: on the selection's edges.
        b.mouse_move(5, 5)
        b.eval("Ed.select.rect(10, 12, 20, 16)"); b.settle(3)
        ys, xs = np.nonzero(overlay())
        (l, t), (rr, bb) = b.image_to_client(10, 12), b.image_to_client(30, 28)
        got = (xs.min() + ox, ys.min() + oy, xs.max() + 1 + ox, ys.max() + 1 + oy)
        self.assertLessEqual(max(abs(got[0] - l), abs(got[1] - t), abs(got[2] - rr), abs(got[3] - bb)), 1.5, f'ants {got} vs selection [{l}, {t}, {rr}, {bb}]')
        b.eval("Ed.select.none()")

    # ===== 3. the compositor against the reference ========================================================================
    def test_05_every_native_mode_with_alpha_on_both_layers_opacity_fill_and_a_soft_mask(self):
        b = self.browser
        rng = np.random.default_rng(2024)
        H, W = 24, 32
        base = random_layer(rng, H, W)
        top = random_layer(rng, H, W)
        b.load_editor_image(base)
        self.add_layer(top, 'Top')
        B, T = self.layer_pm(0), self.layer_pm(1)
        soft = np.tile(np.array([0, 1, 30, 128, 200, 254, 255, 77], np.uint8), (H, W // 8))
        rows, worst = [], {}
        for mode in NATIVE:
            for opacity, fill, masked in ((1, 1, False), (0.6, 1, False), (1, 0.45, False), (0.7, 0.5, False), (1, 1, True), (0.8, 0.6, True)):
                if masked: self.set_mask(1, soft)
                else: b.eval("Ed.layers.set(1, {mask: null}, {history: false})")
                b.eval(f"Ed.layers.set(1, {{blend: '{mode}', opacity: {opacity}, fill: {fill}}}, {{history: false}})")
                m = self.mask_of(1) if masked else 1.0
                got = b.editor_pixels()
                ideal = errors(got, over(B, T, mode, opacity * fill * m))
                # The masked copy of the layer is itself an 8-bit premultiplied canvas: model that storage step too.
                model = errors(got, over(B, stored(T * (m[..., None] if masked else 1)), mode, opacity * fill)) if masked else ideal
                rows.append(f'{mode:13s} opacity {opacity:<4} fill {fill:<4} {"soft mask" if masked else "no mask  "}  ideal max {ideal[0]:3d} mean {ideal[1]:.4f}'
                            f'   with 8-bit masked copy max {model[0]:3d} mean {model[1]:.4f}')
                w = worst.setdefault(mode, [0, 0.0, 0, 0.0])
                w[0], w[1], w[2], w[3] = max(w[0], ideal[0]), max(w[1], ideal[1]), max(w[2], model[0]), max(w[3], model[1])
        b.eval("Ed.layers.set(1, {mask: null}, {history: false})")
        table = ('\n'.join(rows) + '\n\nworst per mode, premultiplied 8-bit levels (ideal float reference | reference that also stores the masked copy in 8 bits):\n'
                 + '\n'.join(f'  {m:13s} max {v[0]:3d} mean {v[1]:.4f} | max {v[2]:3d} mean {v[3]:.4f}' for m, v in worst.items()))
        self.report['blend modes vs W3C'] = table
        print('\n[verify] blend modes, worst per mode (ideal max, mean | 8-bit-mask model max, mean):', {m: (v[0], round(v[1], 3), v[2], round(v[3], 3)) for m, v in worst.items()})
        bad = {m: v for m, v in worst.items() if v[2] > 3 or v[3] > 0.6}
        self.assertFalse(bad, f'modes off by more than 3 levels (or mean > 0.6) from the W3C formula; full table in output/verify-report.txt:\n{table}')

    def test_06_stack_hidden_layers_opacity_and_masks(self):
        b = self.browser
        rng = np.random.default_rng(7)
        H, W = 20, 20
        layers = [random_layer(rng, H, W) for _ in range(4)]
        b.load_editor_image(layers[0])
        for i, a in enumerate(layers[1:], 1): self.add_layer(a, f'L{i}')
        P = [self.layer_pm(i) for i in range(4)]
        b.eval("Ed.layers.set(1, {blend: 'multiply', opacity: 0.8}, {history: false}); Ed.layers.set(2, {visible: false}, {history: false});"
               "Ed.layers.set(3, {blend: 'screen', fill: 0.5}, {history: false})")
        soft = rng.integers(0, 256, (H, W), dtype=np.uint8)
        self.set_mask(3, soft)
        want = over(over(P[0], P[1], 'multiply', 0.8), P[3], 'screen', 0.5 * self.mask_of(3))
        mx, mean = errors(b.editor_pixels(), want)
        self.assertLessEqual(mx, 2, f'a hidden layer is skipped, the rest stack (max {mx}, mean {mean:.3f})')
        b.eval("Ed.layers.set(3, {maskEnabled: false}, {history: false})")
        mx2, _ = errors(b.editor_pixels(), over(over(P[0], P[1], 'multiply', 0.8), P[3], 'screen', 0.5))
        self.assertLessEqual(mx2, 2, 'a disabled mask shows everything')
        b.eval("Ed.layers.set(3, {maskEnabled: true}, {history: false}); Ed.layers.set(0, {visible: false}, {history: false})")
        mx3, _ = errors(b.editor_pixels(), over(over(np.zeros_like(P[0]), P[1], 'multiply', 0.8), P[3], 'screen', 0.5 * self.mask_of(3)))
        self.assertLessEqual(mx3, 2, 'a hidden bottom layer leaves transparency to blend with')
        # A full re-composite and the incremental one agree.
        self.assertPixelsClose(b.pixels("Ed.render.composite()"), b.editor_pixels(), max_abs=1, msg='Ed.render.composite() = the draft')
        self.report['stack with hidden layers and a soft mask'] = f'max {mx} mean {mean:.4f}; mask off max {mx2}; hidden bottom max {mx3}'

    def test_07_clipping_groups(self):
        b = self.browser
        rng = np.random.default_rng(99)
        H, W = 16, 24
        back = random_layer(rng, H, W, alphas=(255,))
        base = np.zeros((H, W, 4), np.uint8); base[..., :3] = (200, 180, 40)
        base[..., 3] = np.tile(np.array([0, 40, 128, 255, 255, 200, 90, 0], np.uint8), (H, W // 8))   # soft edges
        member = random_layer(rng, H, W, alphas=(255, 128, 60))
        b.load_editor_image(back)
        self.add_layer(base, 'Base')
        self.add_layer(member, 'Member', clip=True)
        P0, P1, P2 = self.layer_pm(0), self.layer_pm(1), self.layer_pm(2)
        rows = []

        def clipped(base, members, fill=1.0, opacity=1.0, invert=False):
            """Photoshop's clipping mask: inside the group the base counts as opaque (its colour at Fill) and the clipped
            layers blend with it; the base's own alpha (its shape) then applies to the group as a whole."""
            a = base[..., 3:4]
            inside = np.concatenate([unpremul(base), np.where(a > 0, np.floor(fill * 255 + .5) / 255, 0.0)], -1)
            inside[..., :3] *= inside[..., 3:4]
            for P, mode, k in members: inside = over(inside, P, mode, k)
            if invert: inside[..., :3] = inside[..., 3:4] - inside[..., :3]
            return over(P0, inside * a, 'normal', opacity)

        def check(what, want, limit=2):
            mx, mean = errors(b.editor_pixels(), want)
            rows.append(f'{what}: max {mx} mean {mean:.4f}')
            self.assertLessEqual(mx, limit, f'{what}: max {mx}, mean {mean:.4f}')

        check('normal member on a soft-edged base', clipped(P1, [(P2, 'normal', 1)]))
        b.eval("Ed.layers.set(1, {opacity: 0.5}, {history: false})")
        check('base opacity 0.5 fades the whole group', clipped(P1, [(P2, 'normal', 1)], opacity=0.5))
        b.eval("Ed.layers.set(1, {opacity: 1, fill: 0.3}, {history: false})")
        check('base fill 0.3 fades only the base, the clip shape keeps full strength', clipped(P1, [(P2, 'normal', 1)], fill=0.3))
        b.eval("Ed.layers.set(1, {fill: 0}, {history: false})")
        check('base fill 0: the clipped layer shows in the base shape alone', clipped(P1, [(P2, 'normal', 1)], fill=0))
        b.eval("Ed.layers.set(1, {fill: 1}, {history: false}); Ed.layers.set(2, {opacity: 0.6}, {history: false})")
        check('member opacity 0.6', clipped(P1, [(P2, 'normal', 0.6)]))
        b.eval("Ed.layers.set(2, {opacity: 1}, {history: false})")
        soft = rng.integers(0, 256, (H, W), dtype=np.uint8)
        self.set_mask(1, soft)
        masked = stored(P1 * self.mask_of(1)[..., None])   # the base's masked pixels are an 8-bit canvas
        check('a soft mask on the base clips the members too', clipped(masked, [(P2, 'normal', 1)]))
        b.eval("Ed.layers.set(1, {mask: null}, {history: false}); Ed.layers.set(2, {visible: false}, {history: false})")
        check('a hidden member is skipped', over(P0, P1), 1)
        b.eval("Ed.layers.set(2, {visible: true}, {history: false}); Ed.layers.set(1, {visible: false}, {history: false})")
        check('hiding the base hides the group', P0, 0)
        b.eval("Ed.layers.set(1, {visible: true}, {history: false})")
        for mode in ('multiply', 'screen', 'color-burn', 'luminosity'):
            b.eval(f"Ed.layers.set(2, {{blend: '{mode}'}}, {{history: false}})")
            check(f'{mode} member blends with the base', clipped(P1, [(P2, mode, 1)]))
        b.eval("Ed.layers.set(2, {blend: 'screen'}, {history: false}); Ed.layers.set(1, {fill: 0.5}, {history: false})")
        check('screen member over a base at fill 0.5', clipped(P1, [(P2, 'screen', 1)], fill=0.5))
        b.eval("Ed.layers.set(1, {fill: 1}, {history: false})")
        # An adjustment layer clipped to the base changes only the base (all of it, soft edges included).
        b.eval("Ed.registerAdjustment('verify-invert', {label: 'Invert', apply(img) { const d = img.data; for (let i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2]; } }});"
               "Ed.layers.set(2, {visible: false}, {history: false}); const A = Ed.layers.add({kind: 'adjustment', adjustment: {type: 'verify-invert', params: {}}, history: false}); Ed.layers.set(A, {clip: true}, {history: false}); true")
        check('clipped invert adjustment', clipped(P1, [], invert=True))
        self.report['clipping groups (vs Photoshop clipping masks)'] = '\n'.join(rows)

    def test_08_adjustment_layer_with_soft_mask_and_opacity(self):
        b = self.browser
        rng = np.random.default_rng(5)
        H, W = 16, 16
        base = random_layer(rng, H, W, alphas=(255, 200, 128, 30))
        b.load_editor_image(base)
        P0 = self.layer_pm(0)
        b.eval("Ed.registerAdjustment('verify-invert', {label: 'Invert', apply(img) { const d = img.data; for (let i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2]; } }});"
               "Ed.layers.add({kind: 'adjustment', adjustment: {type: 'verify-invert', params: {}}, history: false}); true")
        soft = rng.integers(0, 256, (H, W), dtype=np.uint8)
        self.set_mask(1, soft)
        b.eval("Ed.layers.set(1, {opacity: 0.7}, {history: false})")
        w = 0.7 * self.mask_of(1)
        inv = P0.copy(); inv[..., :3] = P0[..., 3:4] - P0[..., :3]
        want = lerp_toward(P0, inv, w)
        mx, mean = errors(b.editor_pixels(), want)
        self.report['adjustment layer'] = f'invert through a soft mask at 70%: max {mx} mean {mean:.4f}'
        self.assertLessEqual(mx, 3, f'adjustment through a soft mask and opacity (max {mx}, mean {mean:.4f})')

    def test_09_effects_fade_with_the_layer_and_ignore_fill(self):
        """Photoshop fades a layer and its effects together by the layer opacity (the effect under an opaque layer stays
        hidden), and Fill fades only the layer's own pixels."""
        b = self.browser
        H, W = 16, 16
        back = np.zeros((H, W, 4), np.uint8); back[...] = (40, 90, 200, 255)
        sq = np.zeros((H, W, 4), np.uint8); sq[4:12, 4:12] = (250, 30, 30, 255)
        b.load_editor_image(back)
        self.add_layer(sq, 'Square')
        b.eval("""Ed.registerEffect('verify-shadow', {stage: 'behind', order: 10, pad: p => 2, render(ctx, i) {
                    const t = i.temp(), g = t.getContext('2d'); g.drawImage(i.source, 2, 2); g.globalCompositeOperation = 'source-in';
                    g.fillStyle = '#000000'; g.fillRect(0, 0, t.width, t.height); ctx.globalAlpha = i.opacity; ctx.drawImage(t, 0, 0); i.free(t); }});
                  Ed.layers.set(1, {effects: {'verify-shadow': {}}}, {history: false}); true""")
        P0, P1 = self.layer_pm(0), self.layer_pm(1)
        shadow = np.zeros_like(P1); shadow[6:14, 6:14] = (0, 0, 0, 1)
        whole = over(over(P0, shadow), P1)     # layer + effect at full strength
        for opacity in (1, 0.5):
            b.eval(f"Ed.layers.set(1, {{opacity: {opacity}}}, {{history: false}})")
            mx, mean = errors(b.editor_pixels(), lerp_toward(P0, whole, opacity))
            self.report[f'effects at opacity {opacity}'] = f'max {mx} mean {mean:.4f}'
            self.assertLessEqual(mx, 2, f'opacity {opacity}: the layer and its shadow fade as one (max {mx})')
        b.eval("Ed.layers.set(1, {opacity: 1, fill: 0.25}, {history: false})")
        mx, mean = errors(b.editor_pixels(), over(over(P0, shadow), P1, 'normal', 0.25))
        self.assertLessEqual(mx, 2, f'fill fades only the pixels; the effect keeps its strength (max {mx})')

    def test_10_a_live_property_change_during_a_stroke_preview_shows_at_once(self):
        """A stroke preview caches the layers below; a change to one of them (history:false, e.g. a live slider) must not
        leave a stale cache on screen."""
        b = self.browser
        H, W = 16, 16
        base = np.zeros((H, W, 4), np.uint8); base[...] = (0, 200, 0, 255)
        b.load_editor_image(base)
        self.add_layer(np.zeros((H, W, 4), np.uint8), 'Paint')
        b.eval("const c = Ed.strokeBuffer(); const g = c.getContext('2d'); g.fillStyle = '#ff0000'; g.fillRect(0, 0, 4, 4);"
               "Ed.preview.stroke({layer: 1, canvas: c}); Ed.preview.update({x: 0, y: 0, w: 4, h: 4}); Ed.flush(); true")
        self.assertEqual(tuple(b.editor_pixels()[1, 1]), (255, 0, 0, 255))
        b.eval("Ed.layers.set(0, {opacity: 0.2}, {history: false}); Ed.flush(); true")
        px = b.editor_pixels()
        self.assertEqual(int(px[10, 10, 3]), 51, 'the layer below fades while the preview is live')
        b.eval("Ed.preview.clear(); Ed.layers.set(0, {opacity: 1}, {history: false}); true")

    def test_11_layer_commands_keep_an_adjustment_layer_on_its_mask(self):
        b = self.browser
        b.load_editor_image(np.full((8, 8, 4), 200, np.uint8))
        b.eval("Ed.registerAdjustment('verify-invert', {label: 'Invert', apply(img) { const d = img.data; for (let i = 0; i < d.length; i += 4) d[i] = 255 - d[i]; }});"
               "Ed.layers.add({kind: 'adjustment', adjustment: {type: 'verify-invert', params: {}}}); true")
        self.assertEqual(self.state()['target'], 'mask')
        b.eval("Ed.layers.duplicate()")
        self.assertEqual((self.state()['activeIndex'], self.state()['target']), (2, 'mask'), 'a duplicated adjustment layer is painted through its mask')
        self.assertEqual(b.eval("Ed.editable()"), '')
        b.eval("Ed.layers.add(); Ed.layers.remove(3)")   # deleting a layer selects the adjustment layer below it
        self.assertEqual((self.state()['activeIndex'], self.state()['target']), (2, 'mask'))
        self.assertEqual(b.eval("Ed.editable()"), '')

    # ===== 4. history ===================================================================================================
    OPS = ('paint', 'paint', 'stroke', 'erase', 'add', 'add-content', 'adjust', 'delete', 'dup', 'move', 'prop', 'prop', 'mask', 'unmask', 'mask-on',
           'target', 'sel', 'sel', 'sel-soft', 'clear', 'undo', 'undo', 'redo', 'jump', 'jump')

    def random_op(self, rng, W, H):
        n = self.browser.eval('Ed.doc.layers.length')
        i = rng.randrange(n)
        col = lambda: f"rgba({rng.randrange(256)},{rng.randrange(256)},{rng.randrange(256)},{rng.choice([1, 1, .5, .2, .03])})"
        rect = lambda: (rng.randrange(-4, W), rng.randrange(-4, H), rng.randrange(1, W), rng.randrange(1, H))
        op = rng.choice(self.OPS)
        if op == 'paint':
            x, y, w, h = rect()
            return op, f"await Ed.applyToTarget('Paint', g => {{ g.fillStyle = '{col()}'; g.fillRect({x}, {y}, {w}, {h}); }})"
        if op in ('stroke', 'erase'):
            pts = [(rng.uniform(-3, W + 3), rng.uniform(-3, H + 3)) for _ in range(rng.randrange(1, 6))]
            tool = 'brush' if op == 'stroke' else 'eraser'
            return op, (f"if (!Ed.editable()) {{ Ed.setTool('{tool}'); Ed.setToolOptions('{tool}', {{size: {rng.choice([1, 3, 7, 15])}, hardness: {rng.choice([0, 50, 100])}, opacity: {rng.choice([100, 60, 10])}}});"
                        f"Ed.setColor('fg', '#{rng.randrange(1 << 24):06x}'); const P = ([x, y]) => ({{x, y, pressure: 1, pointerType: 'mouse', buttons: 1}}), pts = {json.dumps(pts)};"
                        "Ed.callTool('onDown', P(pts[0])); for (const p of pts.slice(1)) Ed.callTool('onMove', P(p)); Ed.callTool('onUp', P(pts.at(-1))); }")
        if op == 'add': return op, f"Ed.layers.add({{name: 'R{rng.randrange(999)}'}})"
        if op == 'add-content':
            x, y, w, h = rect()
            return op, (f"const c = Ed.util.canvas({W}, {H}), g = c.getContext('2d'); g.fillStyle = '{col()}'; g.fillRect({x}, {y}, {w}, {h});"
                        f"Ed.layers.add({{name: 'C{rng.randrange(999)}', canvas: c, at: {rng.randrange(n + 1)}}})")
        if op == 'adjust': return op, "Ed.layers.add({kind: 'adjustment', adjustment: {type: 'verify-invert', params: {}}})"
        if op == 'delete': return op, f"Ed.layers.remove({i})"
        if op == 'dup': return op, f"Ed.layers.duplicate({i})"
        if op == 'move': return op, f"Ed.layers.move({i}, {rng.randrange(n)})"
        if op == 'prop':
            p = rng.choice([{'opacity': round(rng.random(), 3)}, {'fill': round(rng.random(), 3)}, {'blend': rng.choice(NATIVE)}, {'visible': rng.random() < .7},
                            {'clip': rng.random() < .5}, {'name': f'N{rng.randrange(99)}'}, {'locks': {'alpha': rng.random() < .5}}, {'locks': {'pixels': rng.random() < .3}},
                            {'opacity': 0.5, 'blend': 'screen', 'visible': True}])
            return op, f"Ed.layers.set({i}, {json.dumps(p)})"
        if op == 'mask': return op, f"Ed.layers.addMask({i}, '{rng.choice(['reveal', 'hide', 'selection', 'hide-selection'])}')"
        if op == 'unmask': return op, f"Ed.layers.removeMask({i}, {json.dumps(rng.random() < .5)})"
        if op == 'mask-on': return op, f"Ed.layers.get({i}).mask && Ed.layers.set({i}, {{maskEnabled: {json.dumps(rng.random() < .5)}}})"
        if op == 'target': return op, f"Ed.layers.setActive({i}, '{rng.choice(['pixels', 'mask'])}')"
        if op == 'sel':
            x, y, w, h = rect()
            k = rng.choice(['rect', 'rect', 'all', 'none', 'invert', 'reselect'])
            return op, (f"Ed.select.rect({x}, {y}, {w}, {h}, '{rng.choice(['replace', 'add', 'subtract', 'intersect'])}')" if k == 'rect' else f"Ed.select.{k}()")
        if op == 'sel-soft':
            x, y, w, h = rect()
            return op, (f"const m = Ed.util.canvas({W}, {H}), g = m.getContext('2d'); g.fillStyle = 'rgba(0,0,0,{rng.choice([.5, .25, .9])})'; g.fillRect({x}, {y}, {w}, {h});"
                        f"Ed.select.set(m, '{rng.choice(['replace', 'add', 'subtract', 'intersect'])}')")
        if op == 'clear': return op, "await Ed.menu.run('Edit/Clear')"
        if op == 'undo': return op, "Ed.history.undo()"
        if op == 'redo': return op, "Ed.history.redo()"
        return op, f"Ed.history.jump({rng.randrange(-1, 60)})"

    def run_ops(self, seed, count, W=40, H=30):
        b = self.browser
        rng = random.Random(seed)
        b.load_editor_image(random_layer(np.random.default_rng(seed), H, W, alphas=(255, 255, 128, 0)))
        b.eval("Ed.registerAdjustment('verify-invert', {label: 'Invert', apply(img) { const d = img.data; for (let i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; } }}); true")
        b.eval("window.__hist = 0; if (window.__histOff) __histOff(); window.__histOff = Ed.on('history', () => __hist++); true")
        fps, cur, hist = [self.fingerprint()], 0, 0
        stats = {'new states': 0, 'refused or no-op': 0, 'undo/redo/jump': 0, 'trimmed': 0}
        for step in range(count):
            op, js = self.random_op(rng, W, H)
            r = b.eval(f"(async () => {{ {js}; Ed.flush(); return {{index: Ed.history.index, length: Ed.history.list().length, hist: __hist, fp: {FINGERPRINT}}}; }})()")
            where = f'seed {seed}, step {step}: {op}: {js[:160]}'
            idx, length, fp = r['index'], r['length'], r['fp']
            recorded, hist = r['hist'] != hist, r['hist']
            if op in ('undo', 'redo', 'jump'):
                stats['undo/redo/jump'] += 1
                self.assertEqual(length, len(fps), where)
                self.assertEqual(fp, fps[idx], f'{where}: state {idx} is not restored exactly')
                cur = idx
                continue
            if not recorded:
                # Nothing recorded (refused, or no change): then nothing may have changed either.
                stats['refused or no-op'] += 1
                self.assertEqual((idx, length), (cur, len(fps)), where)
                self.assertEqual(fp, fps[cur], f'{where}: changed the document without a history state')
                continue
            stats['new states'] += 1
            fps = fps[:cur + 1] + [fp]
            if len(fps) > length:
                stats['trimmed'] += len(fps) - length
                fps = fps[len(fps) - length:]
            self.assertEqual(len(fps), length, f'{where}: a new state drops the later ones ({length} states listed, {len(fps)} expected)')
            self.assertEqual(idx, length - 1, f'{where}: the new state is the current one')
            cur = idx
            if step % 7 == 0:   # premultiplied: the much-read draft may be rendered in software, 1 level apart from a fresh canvas
                comp, full = b.editor_pixels(), b.pixels("Ed.render.composite()")
                self.assertPixelsClose(premultiplied(comp), premultiplied(full), max_abs=1, msg=f'{where}: the incrementally updated draft = a full composite')
        # Visit every state in a random order, then walk the whole list back and forth.
        order = list(range(len(fps))); rng.shuffle(order)
        for k in order + list(range(len(fps) - 1, -1, -1)) + list(range(len(fps))):
            self.assertEqual(b.eval(f"Ed.history.jump({k}); Ed.flush(); {FINGERPRINT}"), fps[k], f'seed {seed}: jumping to state {k}')
        self.assertPixelsClose(premultiplied(b.editor_pixels()), premultiplied(b.pixels("Ed.render.composite()")), max_abs=1, msg=f'seed {seed}: final composite')
        return stats

    def test_12_random_history_sequences_restore_every_state_exactly(self):
        b = self.browser
        rows = []
        for seed in (11, 12, 13, 14):
            stats = self.run_ops(seed, 90)
            rows.append(f'seed {seed}: {stats}, {len(self.labels())} states at the end, {b.eval("Ed.history.bytes")} bytes')
        self.report['random history'] = '\n'.join(rows)
        self.assertNoBrowserErrors()

    def test_13_history_memory_cap_and_state_limit(self):
        b = self.browser
        W, H = 64, 64
        b.load_editor_image(np.zeros((H, W, 4), np.uint8))
        cap = W * H * 4 * 5
        b.eval(f"Ed.history.limits.bytes = {cap}")
        try:
            fps = {}
            for k in range(14):
                b.eval(f"await Ed.applyToTarget('Paint {k}', g => {{ g.fillStyle = '#{(k * 4567) % (1 << 24):06x}'; g.fillRect({k}, 0, {W - k}, {H}); }}); true")
                fps[f'Paint {k}'] = self.fingerprint()
                listed = b.eval('Ed.history.list().length')
                used = b.eval('Ed.history.bytes')
                if listed > 2: self.assertLessEqual(used, cap, f'after paint {k}: {used} bytes kept, cap {cap}')
            labels = self.labels()
            self.assertLess(len(labels), 14, 'the oldest states were dropped')
            self.assertEqual(labels[-1], 'Paint 13')
            for i, label in enumerate(labels):
                if label in fps:
                    self.assertEqual(b.eval(f"Ed.history.jump({i}); {FINGERPRINT}"), fps[label], f'state {label} after trimming')
            # Byte accounting: undoing to the oldest kept state and back changes nothing; a new action after going back
            # frees the dropped states.
            b.eval("Ed.history.jump(1)")
            before = b.eval('Ed.history.bytes')
            b.eval("await Ed.applyToTarget('Small', g => { g.fillStyle = '#fff'; g.fillRect(0, 0, 1, 1); }, {rect: {x: 0, y: 0, w: 1, h: 1}}); true")
            self.assertEqual(self.labels()[-1], 'Small')
            self.assertEqual(len(self.labels()), 3)
            self.assertLess(b.eval('Ed.history.bytes'), before, 'the dropped states no longer count')
        finally:
            b.eval("Ed.history.limits.bytes = 512 * 1024 * 1024; Ed.history.limits.states = 50")
        b.eval("Ed.history.limits.states = 3")
        for k in range(6): b.eval(f"Ed.layers.set(0, {{opacity: {0.9 - k / 10}}})")
        self.assertEqual(len(self.labels()), 4, 'states: 3 plus the oldest kept one')
        self.assertTrue(b.eval("Ed.history.undo() && Ed.history.undo() && Ed.history.undo()"))
        self.assertFalse(b.eval("Ed.history.undo()"))
        self.assertAlmostEqual(self.state()['layers'][0]['opacity'], 0.7)
        b.eval("Ed.history.limits.states = 50")

    # ===== 5. painting with real input =================================================================================
    def paint_doc(self, w=200, h=120, color=(255, 255, 255, 255)):
        b = self.browser
        a = np.zeros((h, w, 4), np.uint8); a[...] = color
        b.load_editor_image(a)
        b.eval("Ed.view.setState({zoom: 3, px: 0, py: 0}); document.getElementById('toast').hidden = true; true")
        b.settle(2)
        return a

    def test_20_fast_drags_leave_no_gaps_hard_soft_and_pen(self):
        b = self.browser
        self.paint_doc()
        self.blank_page_focus()
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 6, hardness: 100, opacity: 100, pressure: false}); Ed.setColor('fg', '#ff00ff')")
        zig = [(10 + 26 * i, 20 if i % 2 else 100) for i in range(8)]
        b.drag_image(zig, center=False, steps_between=0, burst=True)
        b.wait_for("Ed.history.label(Ed.history.index) === 'Brush Tool'")
        px = b.editor_pixels()
        d = distance_to_path(200, 120, zig)
        core = d <= 2
        self.assertTrue(np.all(px[core][:, :3] == (255, 0, 255)), f'{int((~np.all(px[core][:, :3] == (255, 0, 255), -1)).sum())} pixels on the path of a fast hard stroke were left unpainted')
        self.assertTrue(np.all(px[d >= 4.5][:, :3] == 255), 'nothing far from the path changed')
        # A soft brush: the middle of the stroke is painted evenly, no beads with gaps between them.
        b.key('Ctrl+Z')
        b.eval("Ed.setToolOptions('brush', {size: 12, hardness: 0, opacity: 100}); Ed.setColor('fg', '#000000')")
        line = [(10, 60), (190, 60)]
        b.drag_image(line, center=False, steps_between=0, burst=True)
        b.wait_for("Ed.history.label(Ed.history.index) === 'Brush Tool'")
        mid = 255 - b.editor_pixels()[60, 16:184, 0].astype(int)
        self.assertGreater(int(mid.min()), 0.6 * int(mid.max()), f'soft stroke along its middle: min {mid.min()}, max {mid.max()}')
        self.assertLess(int(mid.max()) - int(mid.min()), 40, 'and even (no visible beads)')
        # A pen stroke: pressure changes the width when "Pen pressure" is on, and it stays connected.
        b.key('Ctrl+Z')
        b.eval("Ed.setToolOptions('brush', {size: 16, hardness: 100, opacity: 100, pressure: true}); Ed.setColor('fg', '#0000ff')")
        pen = [(20, 40), (100, 40), (180, 40)]
        b.drag_image(pen, center=False, steps_between=6, pointer='pen', pressures=[0.15, 0.6, 1.0])
        b.wait_for("Ed.history.label(Ed.history.index) === 'Brush Tool'")
        px = b.editor_pixels()
        blue = np.all(px[..., :3] == (0, 0, 255), -1)
        widths = [int(blue[:, x].sum()) for x in (30, 100, 170)]
        self.assertTrue(widths[0] < widths[1] < widths[2], f'width follows pressure: {widths}')
        self.assertTrue(all(blue[40, 20:180]), 'a pen stroke is continuous')
        b.eval("Ed.setToolOptions('brush', {pressure: false})")

    def test_21_selection_soft_values_lock_transparency_and_the_mask_target(self):
        b = self.browser
        a = self.paint_doc(color=(0, 0, 255, 255))
        b.eval("const m = Ed.util.canvas(200, 120), g = m.getContext('2d'); g.fillStyle = '#000'; g.fillRect(0, 0, 100, 120); g.fillStyle = 'rgba(0,0,0,0.5)'; g.fillRect(100, 0, 60, 120); Ed.select.set(m)")
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 20, hardness: 100, opacity: 100}); Ed.setColor('fg', '#ff0000')")
        b.drag_image([(10, 60), (190, 60)], center=False)
        b.wait_for("Ed.history.label(Ed.history.index) === 'Brush Tool'")
        px = b.editor_pixels().astype(int)
        self.assertEqual(tuple(px[60, 50]), (255, 0, 0, 255), 'fully selected: painted')
        self.assertTrue(abs(px[60, 130, 0] - 128) <= 2 and abs(px[60, 130, 2] - 127) <= 2, f'half selected: half painted, got {px[60, 130]}')
        self.assertEqual(tuple(px[60, 180]), (0, 0, 255, 255), 'not selected: untouched')
        b.eval("Ed.select.none()")
        # Lock transparency keeps every alpha value exactly, half-transparent ones included.
        sprite = image_array('edit-sprite.png')
        b.load_editor_image(sprite)
        b.eval("Ed.view.setState({zoom: 6, px: 0, py: 0}); Ed.layers.set(0, {locks: {alpha: true}})")
        before = b.editor_pixels()
        for tool, color in (('brush', '#00ff00'), ('eraser', None)):
            b.key(tool[0])
            b.eval(f"Ed.setToolOptions('{tool}', {{size: 9, hardness: 0, opacity: 70}}); Ed.setColor('fg', '{color or '#000000'}'); Ed.setColor('bg', '#123456')")
            b.drag_image([(2, 55), (40, 44), (60, 30)], center=False)
            b.wait_for(f"Ed.history.label(Ed.history.index) === '{tool.title()} Tool'")
            self.assertPixelsClose(b.editor_pixels()[..., 3], before[..., 3], msg=f'{tool}: alpha is locked exactly')
        b.eval("Ed.layers.set(0, {locks: {alpha: false}})")
        # The mask as the edit target: only the mask changes, and undo restores it.
        b.eval("Ed.layers.addMask(0, 'reveal')")
        self.assertEqual(self.state()['target'], 'mask')
        pixels_before, mask_before = b.editor_pixels(0), b.editor_pixels('mask')
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 10, hardness: 100, opacity: 100}); Ed.setColor('fg', '#000000')")
        b.drag_image([(10, 10), (50, 10)], center=False)
        b.wait_for("Ed.history.label(Ed.history.index) === 'Brush Tool'")
        self.assertPixelsClose(b.editor_pixels(0), pixels_before, msg='painting on the mask leaves the pixels alone')
        mask = b.editor_pixels('mask')[..., 3]
        self.assertEqual(int(mask[10, 30]), 0)
        self.assertEqual(int(b.editor_pixels()[10, 30, 3]), 0, 'the composite hides there')
        b.key('Ctrl+Z')
        self.assertPixelsClose(b.editor_pixels('mask'), mask_before, msg='undo restores the mask')

    def test_22_alt_eyedropper_space_hand_brackets_and_digits(self):
        b = self.browser
        self.paint_doc()
        b.eval("const g = Ed.layers.get(0).canvas.getContext('2d'); g.fillStyle = '#123456'; g.fillRect(0, 0, 100, 120); g.fillStyle = '#abcdef'; g.fillRect(100, 0, 100, 120); Ed.update(); true")
        self.blank_page_focus()
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 6, hardness: 100, opacity: 100}); Ed.setColor('fg', '#ff0000')")
        n = len(self.labels())
        # Alt held: the brush samples, and keeps sampling while dragging.
        with b.hold('Alt'):
            b.drag_image([(20, 20), (150, 20)], center=False)
        self.assertEqual(b.eval('Ed.fg'), '#abcdef', 'Alt-drag samples where it ends')
        self.assertEqual(len(self.labels()), n, 'and paints nothing')
        b.drag_image([(20, 90), (60, 90)], center=False)
        self.assertEqual(len(self.labels()), n + 1, 'after Alt is let go the brush paints again')
        self.assertEqual(tuple(b.editor_pixels()[90, 40][:3]), (0xab, 0xcd, 0xef))
        # Space held pans and paints nothing, even right after a toolbar button was clicked (focus on that button).
        b.click_selector('#edPanel-layers [data-action="new"]')
        layers = len(self.state()['layers'])
        n = len(self.labels())
        v0 = b.eval('[S.px, S.py]')
        x, y = b.pixel_center(100, 60)
        with b.hold(' '):
            b.drag([(x, y), (x + 40, y + 15)])
        self.assertEqual(len(self.state()['layers']), layers, 'Space did not press the focused "New layer" button again')
        self.assertEqual(len(self.labels()), n, 'nothing painted while Space was held')
        v1 = b.eval('[S.px, S.py]')
        self.assertAlmostEqual(v1[0] - v0[0], 40, delta=0.01)
        self.assertAlmostEqual(v1[1] - v0[1], 15, delta=0.01)
        # [ and ] walk the size ladder, digits set the opacity.
        self.blank_page_focus()
        b.eval("Ed.setToolOptions('brush', {size: 10, opacity: 100})")
        b.key(']'); b.key(']')
        self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 20)
        b.key('[')
        self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 15)
        time.sleep(0.8); b.key('3')
        self.assertEqual(b.eval("Ed.toolOptions('brush').opacity"), 30)
        time.sleep(0.8)
        b.eval("Ed.setToolOptions('brush', {size: 6, opacity: 100})")

    def test_23_shortcuts_are_ignored_while_typing(self):
        b = self.browser
        self.paint_doc()
        b.eval("Ed.setTool('brush'); Ed.setToolOptions('brush', {size: 10, opacity: 100})")
        n = len(self.labels())
        # Number boxes take digits, 'e', Backspace and Delete as typing (that edits their own value), so they get the other keys.
        letters, all_keys = ('h', ']', '[', 'x', 'd', ' '), ('e', 'h', ']', '[', '5', 'x', 'd', 'Delete', 'Backspace', ' ')
        b.eval("Ed.setColor('fg', '#102030'); Ed.setColor('bg', '#405060')")
        for sel, what, keys in (('#brushSize', 'the Size box', letters), ('#search', 'the library search', all_keys),
                                ('#edPanel-layers select.ed-blend', 'the blend list', (']', '[', '5', 'x', 'Delete', 'Backspace')),   # no blend mode starts with these
                                ('#buttonInset', 'a number box in the inspector', letters)):
            self.assertTrue(b.eval(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); const h = e.closest('[hidden]'); if (h) h.hidden = false; e.focus(); return document.activeElement === e; }})()"), what)
            for k in keys: b.key(k)
            self.assertEqual(b.eval('Ed.tool'), 'brush', f'typing in {what} does not switch tools')
            self.assertEqual((b.eval("Ed.toolOptions('brush').opacity"), len(self.labels())), (100, n), f'nor change opacity or history ({what})')
            self.assertEqual((b.eval('Ed.fg'), b.eval('Ed.bg')), ('#102030', '#405060'), f'nor swap or reset the colors ({what})')
            if keys is letters: self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 10, f'nor resize the brush ({what})')
            b.eval("document.activeElement.blur()")
        b.eval("Ed.setToolOptions('brush', {size: 10})")
        b.eval("document.getElementById('insetControl').hidden = true")
        b.set_value('#search', '')
        # A textarea (Theme AI) and an editor dialog too.
        b.eval("window.__dlg = Ed.dialog({title: 'Verify Typing', fields: [{key: 'note', label: 'Note', type: 'text', default: ''}]}); true")
        b.wait_for("document.querySelector('[data-ed-dialog=\"Verify Typing\"] [name=note]') === document.activeElement || (document.querySelector('[data-ed-dialog=\"Verify Typing\"] [name=note]').focus(), false)")
        for k in ('e', 'Delete', 'Ctrl+Z', 'Ctrl+A'): b.key(k)
        self.assertEqual((b.eval('Ed.tool'), len(self.labels()), b.eval('Ed.selection === null')), ('brush', n, True))
        b.key('Escape')
        self.assertIsNone(b.eval('await __dlg'))

    # ===== 6. saving and reopening ======================================================================================
    def snapshot_doc(self):
        b = self.browser
        st = self.state()
        n = len(st['layers'])
        props = b.eval("Ed.doc.layers.map(L => ({id: L.id, name: L.name, kind: L.kind, visible: L.visible, opacity: L.opacity, fill: L.fill, blend: L.blend, clip: L.clip, locks: L.locks, maskEnabled: L.maskEnabled, mask: !!L.mask, data: L.data, effects: L.effects}))")
        pixels = [premultiplied(b.editor_pixels(i)) for i in range(n)]
        masks = [b.pixels(f'Ed.layers.get({i}).mask')[..., 3] if props[i]['mask'] else None for i in range(n)]
        return dict(props=props, pixels=pixels, masks=masks, active=st['activeIndex'], target=st['target'], composite=premultiplied(b.editor_pixels()))

    def test_30_save_and_reload_restore_every_layer_exactly(self):
        b = self.fresh('edit-sprite.png')
        key = b.eval('S.asset.key')
        rng = np.random.default_rng(30)
        self.add_layer(random_layer(rng, 64, 64), 'Glass ✓ «1»', blend='multiply', opacity=0.37, fill=0.6)
        self.add_layer(random_layer(rng, 64, 64, alphas=(255, 90, 3)), 'Clipped', clip=True, blend='screen')
        self.add_layer(random_layer(rng, 64, 64, alphas=(255, 128, 0)), 'Hidden', visible=False)
        self.set_mask(1, rng.integers(0, 256, (64, 64), dtype=np.uint8))
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}, {history: false}); Ed.layers.set(2, {data: {verify: [1, 'two', {'3': null}]}}, {history: false});"
               "Ed.layers.set(1, {maskEnabled: true}, {history: false}); Ed.layers.setActive(1, 'mask'); Ed.history.record('Verify Setup', () => {}); true")
        b.eval("Ed.layers.set(3, {name: 'Hidden layer'})")   # one real state so the draft is dirty
        want = self.snapshot_doc()
        self.assertTrue(b.eval('S.dirty'))
        b.key('Ctrl+S')
        b.wait_for("!S.dirty && !S.busy && S.asset.revision !== 'original'", timeout=30)
        revision = b.eval('S.asset.revision')
        saved = decode_png(urllib.request.urlopen(f'{self.server.url}/api/image?key={key}').read())
        self.assertPixelsClose(premultiplied(saved), want['composite'], msg='the saved flat PNG is the composite')
        b.reload()
        b.select_asset('edit-sprite.png')
        got = self.snapshot_doc()
        self.assertEqual(got['props'], want['props'], 'every layer setting comes back')
        self.assertEqual((got['active'], got['target']), (want['active'], want['target']))
        for i, (g, w) in enumerate(zip(got['pixels'], want['pixels'])): self.assertPixelsClose(g, w, msg=f'layer {i} pixels exactly')
        for i, (g, w) in enumerate(zip(got['masks'], want['masks'])):
            if w is not None: self.assertPixelsClose(g, w, msg=f'layer {i} mask exactly')
        self.assertPixelsClose(got['composite'], want['composite'], max_abs=1, msg='composite')
        self.assertEqual(self.labels(), ['Open'])
        self.assertFalse(b.eval('S.dirty'))
        self.assertEqual(b.eval('S.asset.revision'), revision)
        # A new layer named like an existing one after reopening gets a fresh id (ids never collide with restored ones).
        b.eval("Ed.layers.add()")
        ids = [p['id'] for p in self.snapshot_doc()['props']]
        self.assertEqual(len(ids), len(set(ids)))
        b.key('Ctrl+Z')
        type(self).saved_sprite = (key, revision, want['composite'])

    def test_31_the_server_refuses_damaged_and_oversized_documents_and_writes_nothing(self):
        import http.client
        server = self.server
        key = server.asset_key('edit-tiny.png')
        revision = server.get('/api/asset', key=key)['revision']
        folder = server.workspace / 'revisions' / key
        files = lambda: sorted(p.name for p in folder.glob('*')) if folder.exists() else []
        before = files()
        png_of = lambda a, fmt='PNG': (lambda buf: (Image.fromarray(a).convert('RGB' if fmt == 'JPEG' else 'RGBA').save(buf, fmt), base64.b64encode(buf.getvalue()).decode())[1])(io.BytesIO())
        flat = png_of(image_array('edit-tiny.png'))
        good = png_of(np.zeros((16, 16, 4), np.uint8))
        layer = lambda **k: dict(dict(id='L1', name='A', kind='pixel', visible=True, opacity=1, fill=1, blend='normal', locks={}, maskEnabled=True, clip=False,
                                      effects={}, adjustment=None, data={}, png=good), **k)
        doc = lambda layers=None, **k: dict(dict(version=1, width=16, height=16, activeIndex=0, target='pixels', name='x', layers=[layer()] if layers is None else layers), **k)
        cases = {
            'unknown version': doc(version=2), 'wrong size': doc(width=17), 'not an object': 'layers', 'no layers': doc(layers=[]),
            '101 layers': doc(layers=[layer()] * 101), 'a layer that is not an object': doc(layers=['x']),
            'not base64': doc(layers=[layer(png='not base64!!')]), 'garbage bytes': doc(layers=[layer(png=base64.b64encode(b'\x89PNG garbage').decode())]),
            'a JPEG': doc(layers=[layer(png=png_of(np.zeros((16, 16, 4), np.uint8), 'JPEG'))]), 'a 17 x 16 PNG': doc(layers=[layer(png=png_of(np.zeros((16, 17, 4), np.uint8)))]),
            'a small mask': doc(layers=[layer(mask_png=png_of(np.zeros((8, 8, 4), np.uint8)))]), 'huge effects': doc(layers=[layer(effects={'x': 'a' * 300000})]),
            'effects as a list': doc(layers=[layer(effects=[1, 2])]), 'NaN in module data': doc(layers=[layer(data={'x': float('nan')})]),
            'Infinity in effects': doc(layers=[layer(effects={'glow': {'size': float('inf')}})]),
        }
        rows = []
        for name, bad in cases.items():
            with self.subTest(name):
                try:
                    server.post('/api/save', {'key': key, 'revision': revision, 'png': flat, 'recipe': {}, 'document': bad})
                    outcome = 'ACCEPTED'
                except RuntimeError as e: outcome = str(e).split('answered ', 1)[-1]
                except OSError as e: outcome = f'no answer ({type(e).__name__})'
                rows.append(f'{name:28s} -> {outcome}')
                self.assertTrue(outcome.startswith('400: '), f'{name}: {outcome}')
                self.assertEqual(server.get('/api/health')['app'], 'Asset Lab', 'the server is still up')
                self.assertEqual(files(), before, f'{name}: nothing was written')
                self.assertEqual(server.get('/api/asset', key=key)['revision'], revision)
        # Larger than the request limit: refused from the headers alone, before reading the body.
        conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=20)
        conn.putrequest('POST', '/api/save'); conn.putheader('Content-Type', 'application/json')
        conn.putheader('X-Lab-Token', server.get('/api/status')['token']); conn.putheader('Content-Length', str(97 * 1024 * 1024)); conn.endheaders()
        r = conn.getresponse(); body = r.read().decode(); conn.close()
        rows.append(f'{"97 MB request":28s} -> {r.status}: {body}')
        self.assertEqual(r.status, 400); self.assertIn('too large', body)
        # Under the request limit but over the 80 MB document limit (4 incompressible 2048 x 2048 layers).
        big_key = server.asset_key('edit-big.png')
        big_rev = server.get('/api/asset', key=big_key)['revision']
        noise = np.random.default_rng(1).integers(0, 256, (2048, 2048, 4), dtype=np.uint8)
        buf = io.BytesIO(); Image.fromarray(noise).save(buf, 'PNG', compress_level=0); noisy = base64.b64encode(buf.getvalue()).decode()
        buf = io.BytesIO(); Image.new('RGBA', (2048, 2048), (1, 2, 3, 255)).save(buf, 'PNG'); flat_big = base64.b64encode(buf.getvalue()).decode()
        big = dict(version=1, width=2048, height=2048, activeIndex=0, target='pixels', name='big', layers=[layer(png=noisy, id=f'L{i}') for i in range(4)])
        try:
            server.post('/api/save', {'key': big_key, 'revision': big_rev, 'png': flat_big, 'recipe': {}, 'document': big}, timeout=120)
            outcome = 'ACCEPTED'
        except RuntimeError as e: outcome = str(e).split('answered ', 1)[-1]
        rows.append(f'{"an 89 MB document":28s} -> {outcome[:120]}')
        self.assertTrue(outcome.startswith('400: ') and 'too big' in outcome, outcome)
        self.assertEqual(server.get('/api/asset', key=big_key)['revision'], big_rev)
        # Out-of-range numbers are cleaned, not refused; an integer too long for a float once dropped the connection.
        saved = server.post('/api/save', {'key': key, 'revision': revision, 'png': flat, 'recipe': {}, 'document': doc(layers=[layer(opacity=10 ** 400, fill=-3)])})
        stored = json.loads((folder / f"{saved['revision']}.layers.json").read_text('utf-8'))
        rows.append(f'{"a 400-digit opacity":28s} -> saved with opacity {stored["layers"][0]["opacity"]}, fill {stored["layers"][0]["fill"]}')
        self.assertEqual((stored['layers'][0]['opacity'], stored['layers'][0]['fill']), (1.0, 0.0))
        server.post('/api/undo', {'key': key, 'revision': saved['revision']})
        self.report['server refusals'] = '\n'.join(rows)

    def test_32_install_uses_the_flat_png_and_restore_puts_the_original_back(self):
        server = self.server
        key, revision, composite = getattr(type(self), 'saved_sprite', (None, None, None))
        if not key: self.skipTest('needs the layered save from test_30')
        game_file = server.asset_path('edit-sprite.png')
        original = game_file.read_bytes()
        build = server.post('/api/build', {})
        paths = [f['path'] for f in build['files']]
        self.assertIn('assets/edit-sprite.png', paths)
        self.assertFalse([p for p in paths if not p.endswith('.png') or 'revisions' in p], f'only pictures go into the mod, never a layers file: {paths}')
        server.post('/api/install', {'build': build['id']})
        try:
            installed = np.array(Image.open(game_file).convert('RGBA'))
            self.assertPixelsClose(premultiplied(installed), composite, msg='the installed file is the flattened picture')
        finally:
            server.post('/api/restore', {})
        self.assertEqual(game_file.read_bytes(), original, 'restore puts the original back byte for byte')

    def test_33_a_document_using_a_missing_module_opens_flat_with_a_message(self):
        b = self.fresh('edit-tiny.png')
        b.eval("Ed.registerAdjustment('verify-gone', {label: 'Gone', apply(img) { const d = img.data; for (let i = 0; i < d.length; i += 4) d[i] = 255 - d[i]; }});"
               "Ed.layers.add({kind: 'adjustment', adjustment: {type: 'verify-gone', params: {}}}); true")
        want = premultiplied(b.editor_pixels())
        b.key('Ctrl+S')
        b.wait_for("!S.dirty && !S.busy && S.asset.revision !== 'original'", timeout=30)
        b.reload()   # the module is gone after a reload
        b.select_asset('edit-tiny.png')
        self.assertEqual(len(self.state()['layers']), 1, 'opened as one flat layer')
        self.assertIn('one flat layer', b.text('#toast'))
        self.assertIn('verify-gone', b.text('#toast'))
        self.assertPixelsClose(premultiplied(b.editor_pixels()), want, max_abs=1, msg='the saved picture is shown unchanged')
        self.assertFalse(b.eval('S.dirty'))

    def test_34_work_done_while_saved_layers_are_still_loading_is_kept(self):
        b = self.fresh('edit-sprite.png')
        key = b.eval('S.asset.key')
        self.assertGreater(len(self.state()['layers']), 1, 'the sprite reopens with the layers saved in test_30')
        b.select_asset('edit-tiny.png')
        # Open the sprite again and act before its layers are rebuilt: the brush must refuse to paint over a picture that
        # is about to be replaced, and a layer command must not be thrown away by the rebuild.
        b.eval("window.__cib = window.createImageBitmap; window.createImageBitmap = async (...a) => { await new Promise(r => setTimeout(r, 250)); return __cib(...a); };"   # a slow PC
               "Ed.setTool('brush'); window.__lab = select(%s, true); true" % json.dumps(key))
        try:
            b.wait_for(f"S.asset && S.asset.key === {json.dumps(key)} && Ed.doc && Ed.doc.layers.length === 1")
            early = b.eval("(() => { const why = Ed.editable(); Ed.layers.add({name: 'Early'}); return why; })()")
            b.eval("await __lab; await Ed.test.ready(); true", timeout=30)
        finally:
            b.eval("window.createImageBitmap = __cib; true")
        self.assertIn('layers', early, f'painting waits for the saved layers to load (got {early!r})')
        names = [l['name'] for l in self.state()['layers']]
        self.assertIn('Early', names, f'a layer added during loading is kept: {names}')
        self.assertTrue(b.eval('S.dirty'))
        b.select_asset('edit-sprite.png')   # discards the draft; the saved layers come back
        self.assertGreater(len(self.state()['layers']), 1)
        self.assertNotIn('Early', [l['name'] for l in self.state()['layers']])

    # ===== 7. the rest of the lab still works ===========================================================================
    def test_40_view_modes_original_compare_export_and_recipe(self):
        b = self.fresh('edit-photo.png')
        photo = image_array('edit-photo.png')
        # A change to the layers alone (the picture is the same) still counts as unsaved work.
        b.eval("Ed.layers.add({name: 'Empty'})")
        b.select_asset('edit-tiny.png', discard=False)
        self.assertEqual(b.text('#dialogTitle'), 'Leave this draft?')
        b.click_selector('#dialog button[value="cancel"]')
        self.assertEqual((b.eval('S.asset.name'), len(self.state()['layers'])), ('edit-photo.png', 2))
        b.eval("Ed.setTool('brush'); Ed.setToolOptions('brush', {size: 20, hardness: 100, opacity: 100}); Ed.setColor('fg', '#00ff00')")
        b.drag_image([(100, 140), (150, 140)])
        b.wait_for("Ed.history.label(Ed.history.index) === 'Brush Tool'")
        for mode in ('tile', 'cell', 'button', 'atlas', 'texture'):
            b.click_selector(f'#viewModes [data-view="{mode}"]')
            if mode != 'atlas': b.wait_for(f"S.mode === '{mode}'")
            b.settle(3)
            shot = b.screenshot_array(clip=tuple(b.eval("(() => { const r = document.getElementById('viewport').getBoundingClientRect(); return [r.left + 20, r.top + 20, r.width - 40, r.height - 40]; })()")))
            self.assertGreater(len(np.unique(shot.reshape(-1, 4), axis=0)), 50, f'{mode} view draws the picture')
            blocked = b.eval('Ed.view.blocked')
            self.assertEqual(bool(blocked), b.eval("S.mode !== 'texture'"), f'{mode}: painting is blocked outside Texture view')
        n = len(self.labels())
        b.click_selector('#compare')
        self.assertTrue(b.eval('!!Ed.view.blocked'))
        b.drag_image([(30, 30), (60, 30)])
        self.assertEqual(len(self.labels()), n, 'no painting while the original is shown')
        b.click_selector('#compare')
        # Export: the downloaded PNG is the composite.
        b.eval("window.__downloads = []; document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a[download]'); if (a) { __downloads.push([a.download, a.href]); e.preventDefault(); } }, true); true")
        b.click_selector('#exportButton')
        b.wait_for("__downloads.length === 1")
        name, href = b.eval("__downloads[0]")
        self.assertEqual(name, 'edit-photo-edited.png')
        exported = decode_png(base64.b64decode(href.split(',', 1)[1]))
        self.assertPixelsClose(premultiplied(exported), premultiplied(b.editor_pixels()), msg='Export PNG = the composite')
        self.assertFalse(np.array_equal(exported, photo))
        b.eval("Ed.menu.run('File/Export PNG')")
        b.wait_for("__downloads.length === 2")
        # Export recipe saves first, then downloads the recipe zip.
        b.click_selector('#recipeButton')
        b.wait_for("__downloads.length === 3 && !S.busy", timeout=30)
        self.assertFalse(b.eval('S.dirty'), 'the recipe export saved the draft first')
        url = b.eval("__downloads[2][1]")
        data = urllib.request.urlopen(url).read()
        self.assertEqual(data[:2], b'PK', 'a zip')
        self.server.post('/api/undo', {'key': b.eval('S.asset.key'), 'revision': b.eval('S.asset.revision')})   # leave the photo as it was

    def test_41_theme_ai_review_and_fonts_open_without_errors(self):
        b = self.fresh('edit-photo.png')
        b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden && document.getElementById('themeModel').options.length > 0")   # settled: buttons in place
        b.settle(4)
        b.click_selector('#fontsButton')
        b.wait_for("document.getElementById('fontsDialog').open", timeout=20)
        b.settle(10)
        b.eval("document.getElementById('fontsDialog').close()")
        b.click_selector('#themeClose')
        b.wait_for("document.getElementById('themeDrawer').hidden")
        b.eval("document.getElementById('reviewButton').disabled = false")
        b.click_selector('#reviewButton')
        b.wait_for("document.getElementById('reviewDialog').open", timeout=20)
        b.settle(5)
        b.eval("document.getElementById('reviewDialog').close()")
        errors = b.errors_since(self._error_mark)
        self.report['theme / fonts / review errors'] = b.error_report(errors) if errors else 'none'
        self.assertNoBrowserErrors(allow=['/api/font-preview'])

    def test_42_inspector_saved_history_include_in_mod_and_revert(self):
        b = self.fresh('edit-tiny.png')
        key = b.eval('S.asset.key')
        b.eval("Ed.setTool('brush'); Ed.setToolOptions('brush', {size: 2, hardness: 100, opacity: 100}); Ed.setColor('fg', '#0000ff')")
        b.eval("Ed.layers.add({name: 'First'})")
        b.drag_image([(2, 2), (6, 2)])
        b.key('Ctrl+S'); b.wait_for("!S.dirty && !S.busy", timeout=30)
        first = premultiplied(b.editor_pixels())
        b.eval("Ed.layers.add({name: 'Second'})")
        b.drag_image([(2, 9), (12, 9)])
        b.key('Ctrl+S'); b.wait_for("!S.dirty && !S.busy", timeout=30)
        second = premultiplied(b.editor_pixels())
        self.assertEqual([l['name'] for l in self.state()['layers']], ['edit-tiny', 'First', 'Second'])
        b.eval("document.getElementById('undoSaved').closest('details').open = true")   # the Saved history section starts folded
        b.click_selector('#undoSaved')
        b.wait_for("Ed.doc && Ed.doc.layers.length === 2", timeout=20)
        b.eval("Ed.test.ready()")
        self.assertEqual([l['name'] for l in self.state()['layers']], ['edit-tiny', 'First'], 'Earlier save brings back that save’s layers')
        self.assertPixelsClose(premultiplied(b.editor_pixels()), first, max_abs=1)
        b.click_selector('#redoSaved')
        b.wait_for("Ed.doc && Ed.doc.layers.length === 3", timeout=20)
        self.assertPixelsClose(premultiplied(b.editor_pixels()), second, max_abs=1)
        # Include in mod.
        staged = lambda: key in [r['key'] for r in self.server.get('/api/status')['staged']]
        self.assertTrue(staged(), 'a save is included in the mod')
        b.click_selector('#includeMod'); b.wait_for("!document.getElementById('includeMod').checked && S.asset.staged === false")
        self.assertFalse(staged())
        b.click_selector('#includeMod'); b.wait_for("S.asset.staged === true")
        # The inspector's Revert to original button is the same undoable step as File > Revert to Original.
        b.click_selector('#resetOriginal')
        self.assertEqual(len(self.state()['layers']), 1)
        self.assertPixelsClose(b.editor_pixels(), image_array('edit-tiny.png'))
        b.key('Ctrl+Z')
        self.assertEqual(len(self.state()['layers']), 3)
        self.assertTrue(b.text('#assetInfo').startswith('16 × 16'))

    # ===== 8. layout =====================================================================================================
    def test_50_layout_at_three_window_sizes_and_focus_mode(self):
        b = self.fresh('edit-photo.png')
        b.eval("Ed.setTool('brush'); Ed.layers.add({name: 'A layer with quite a long name to see how the row copes'}); Ed.layers.addMask(1, 'reveal'); true")
        regions = {'library': '.library', 'studio': '.studio', 'inspector': '.inspector', 'document bar': '.document-bar', 'menu row': '#menuRow',
                   'options bar': '.ed-optionsbar', 'work area': '.workarea', 'tools': '#edTools', 'viewport': '#viewport', 'mod tray': '#modTray', 'dock': '#edDock'}
        rows = []
        try:
            for w, h in ((1366, 768), (1600, 1000), (1920, 1080)):
                b.set_viewport(w, h)
                b.eval("Ed.view.fit(); document.querySelector('.inspector').scrollTop = 0; Ed.panels.show('layers'); true"); b.settle(4)
                b.mouse_move(*b.pixel_center(128, 128)); b.settle(2)
                path = b.screenshot(f'verify-layout-{w}x{h}.png')
                box = b.eval(f"Object.fromEntries(Object.entries({json.dumps(regions)}).map(([k, s]) => {{ const r = document.querySelector(s).getBoundingClientRect(); return [k, [r.left, r.top, r.right, r.bottom]]; }}))")
                rows.append(f'{w}x{h}: ' + ', '.join(f'{k} {[round(v) for v in r]}' for k, r in box.items()) + f'  ({path.name})')
                self.assertLessEqual(b.eval('document.documentElement.scrollWidth'), w, f'no sideways scrolling at {w}x{h}')
                inside = lambda r, o: r[0] >= o[0] - 1 and r[1] >= o[1] - 1 and r[2] <= o[2] + 1 and r[3] <= o[3] + 1
                overlap = lambda a, c: min(a[2], c[2]) - max(a[0], c[0]) > 1 and min(a[3], c[3]) - max(a[1], c[1]) > 1
                for k in ('library', 'studio', 'inspector'): self.assertTrue(box[k][2] <= w + 1 and box[k][0] >= -1, f'{k} fits the window width at {w}x{h}: {box[k]}')
                self.assertFalse(overlap(box['library'], box['studio']) or overlap(box['studio'], box['inspector']), f'columns overlap at {w}x{h}')
                for k in ('document bar', 'menu row', 'options bar', 'work area', 'mod tray'): self.assertTrue(inside(box[k], box['studio']), f'{k} inside the studio at {w}x{h}: {box[k]} vs {box["studio"]}')
                order = ['document bar', 'menu row', 'options bar', 'work area', 'mod tray']
                for a, c in zip(order, order[1:]): self.assertLessEqual(box[a][3], box[c][1] + 1, f'{a} above {c} without overlap at {w}x{h}')
                self.assertLessEqual(box['mod tray'][3], h + 1, f'the mod tray is on screen at {w}x{h}')
                rows_seen = b.eval("[...document.querySelectorAll('#edPanel-layers .ed-layer')].filter(r => { const q = r.getBoundingClientRect(); return q.top >= 0 && q.bottom <= innerHeight; }).length")
                self.assertTrue(box['dock'][1] >= box['inspector'][1] - 1 and rows_seen == 2, f'the Layers panel and both layer rows are on screen at {w}x{h} ({box["dock"]}, {rows_seen} rows)')
                self.assertTrue(inside(box['tools'], box['work area']) and inside(box['viewport'], box['work area']))
                self.assertFalse(overlap(box['tools'], box['viewport']), 'the tool strip does not cover the picture')
                vw, vh = box['viewport'][2] - box['viewport'][0], box['viewport'][3] - box['viewport'][1]
                self.assertGreater(vw, 0.38 * w, f'the canvas keeps a useful width at {w}x{h}: {vw}')
                self.assertGreater(vh, 0.42 * h, f'the canvas keeps a useful height at {w}x{h}: {vh}')
                # Nothing in the menu row, the options bar or the Layers panel is cut off or spills out of its box.
                spill = b.eval("""[...document.querySelectorAll('#menuRow *, .ed-optionsbar *, #edDock .ed-layers-row *, #edDock .ed-layers-foot *')].filter(e => {
                    if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden') return false; const r = e.getBoundingClientRect(), p = e.closest('#menuRow, .ed-optionsbar, #edDock').getBoundingClientRect();
                    return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); }).map(e => (e.id || e.className || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 20))""")
                self.assertEqual(spill, [], f'elements spilling out of their bar at {w}x{h}')
                clipped = b.eval("""[...document.querySelectorAll('#edMenubar .ed-menu-top, .ed-optionsbar label, .ed-optionsbar button, #edDock .ed-tab, #edDock .ed-layer-action, .view-controls button, .view-controls label')]
                    .filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => (e.id || e.className) + ':' + e.textContent.trim().slice(0, 20))""")
                self.assertEqual(clipped, [], f'controls with cut-off text at {w}x{h}')
            b.set_viewport(1366, 768)
            before = b.eval("document.getElementById('viewport').getBoundingClientRect().width")
            b.click_selector('#focusButton')
            b.eval("Ed.view.fit()"); b.settle(4)
            b.screenshot('verify-layout-focus-1366x768.png')
            after = b.eval("document.getElementById('viewport').getBoundingClientRect().width")
            self.assertGreater(after, before + 150, 'Focus mode gives the canvas the library’s room')
            self.assertEqual(b.eval("[getComputedStyle(document.querySelector('.library')).display, getComputedStyle(document.querySelector('#modTray')).display]"), ['none', 'none'])
            self.assertLessEqual(b.eval('document.documentElement.scrollWidth'), 1366)
            b.click_selector('#focusButton')
        finally:
            b.set_viewport(*self.viewport)
        self.report['layout'] = '\n'.join(rows)

    # ===== 9. speed ======================================================================================================
    def test_60_stroke_frame_times_on_2048_with_six_layers(self):
        b = self.fresh('edit-big.png')
        rng = np.random.default_rng(60)
        tint = np.zeros((2048, 2048, 4), np.uint8); tint[..., :3] = (255, 128, 0); tint[..., 3] = 60
        self.add_layer(tint, 'Tint', blend='multiply')
        self.add_layer(np.zeros((2048, 2048, 4), np.uint8), 'Paint here')
        glow = np.zeros((2048, 2048, 4), np.uint8); glow[::32, :, :] = (40, 40, 200, 255)
        self.add_layer(glow, 'Lines', blend='linear-dodge', opacity=0.7)
        self.add_layer(glow, 'Clipped', clip=True)
        self.add_layer(np.full((2048, 2048, 4), 128, np.uint8), 'Hidden', visible=False)
        self.set_mask(1, (np.indices((2048, 2048)).sum(0) % 256).astype(np.uint8))
        b.eval("Ed.layers.setActive(2); Ed.setTool('brush'); Ed.setToolOptions('brush', {size: 40, hardness: 100, opacity: 80}); Ed.setColor('fg', '#3355ff'); Ed.view.fit(); true")
        b.settle(4)
        pts = [(200 + i * 20, 1024 + 300 * np.sin(i / 12)) for i in range(80)]
        result = b.eval("""(async pts => { const f = () => new Promise(r => requestAnimationFrame(r)), P = ([x, y]) => ({x, y, pressure: 1, pointerType: 'mouse', buttons: 1});
            const idle = []; let t0 = await f(); for (let i = 0; i < 20; i++) { const t = await f(); idle.push(t - t0); t0 = t; }
            const frames = []; Ed.callTool('onDown', P(pts[0])); let last = await f();
            for (const p of pts.slice(1)) { Ed.callTool('onMove', P(p)); const t = await f(); frames.push(t - last); last = t; }
            const c = performance.now(); Ed.callTool('onUp', P(pts.at(-1))); const commit = performance.now() - c; await f();
            const u = performance.now(); Ed.history.undo(); Ed.flush(); const undo = performance.now() - u;
            const r = performance.now(); Ed.history.redo(); Ed.flush(); const redo = performance.now() - r;
            const o = performance.now(); Ed.update(); Ed.flush(); const full = performance.now() - o;
            return {idle, frames, commit, undo, redo, full}; })(%s)""" % json.dumps(pts), timeout=180)
        frames = result['frames']
        # The same stroke with real mouse input through DevTools, timed by the page's own frames.
        b.eval("window.__frames = []; window.__tick = true; (function loop(t) { __frames.push(t); if (__tick) requestAnimationFrame(loop); })(performance.now()); true")
        t0 = time.perf_counter()
        b.drag_image([(x, y + 200) for x, y in pts[::4]], steps_between=3)
        wall = time.perf_counter() - t0
        real = b.eval("__tick = false; __frames.slice(1).map((t, i) => t - __frames[i])")
        b.wait_for("Ed.history.label(Ed.history.index) === 'Brush Tool'")
        gpu = 'real GPU' if GPU else 'SwiftShader software GPU'
        text = (f'{gpu}; 2048 x 2048, 6 layers (multiply tint with a soft mask, the painted layer, a linear-dodge layer at 70% with a clipped layer, a hidden one)\n'
                f'one move per frame: median {statistics.median(frames):.1f} ms, 95th percentile {np.percentile(frames, 95):.1f} ms, worst {max(frames):.1f} ms; '
                f'idle frame {statistics.median(result["idle"]):.1f} ms\ncommit {result["commit"]:.1f} ms, undo {result["undo"]:.1f} ms, redo {result["redo"]:.1f} ms, '
                f'full re-composite {result["full"]:.1f} ms\nreal mouse drag ({len(pts[::4]) * 4} moves): {wall:.2f} s, page frames median {statistics.median(real):.1f} ms, worst {max(real):.1f} ms')
        print('\n[verify speed]', text)
        self.report['speed'] = text
        self.assertLess(statistics.median(frames), 150, text)
