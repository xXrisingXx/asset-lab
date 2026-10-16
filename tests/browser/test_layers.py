"""The Layers module (src/web/editor/layers.js) with every feature module loaded, as players have it, driven with real mouse
and keyboard input in headless Edge: the Layers panel (the core's selectors and behaviours, multi-select, drag, eyes,
right-click menus, layout at 1366 x 768 and 1920 x 1080), the extra blend modes against Photoshop's formulas computed in
numpy, masks, clipping masks, merges that must keep the composite, Arrange, locks and the layer keys.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_layers*.py" -v
"""
import json
import numpy as np
from harness import ALT, CTRL, SHIFT, EditorTestCase, image_array, premultiplied

ORDER = ['normal', 'dissolve', 'darken', 'multiply', 'color-burn', 'linear-burn', 'darker-color', 'lighten', 'screen', 'color-dodge',
         'linear-dodge', 'lighter-color', 'overlay', 'soft-light', 'hard-light', 'vivid-light', 'linear-light', 'pin-light', 'hard-mix',
         'difference', 'exclusion', 'subtract', 'divide', 'hue', 'saturation', 'color', 'luminosity']   # Photoshop's list
EXTRA = ['linear-burn', 'darker-color', 'lighter-color', 'vivid-light', 'linear-light', 'pin-light', 'hard-mix', 'subtract', 'divide']
PANEL = '#edPanel-layers'


# ----- Photoshop's blend formulas (B(Cb, Cs) on straight colours in 0..1), written independently of layers.js ---------------
def lum(c): return 0.3 * c[..., 0] + 0.59 * c[..., 1] + 0.11 * c[..., 2]


def mix(mode, cb, cs):
    with np.errstate(divide='ignore', invalid='ignore'):
        if mode == 'normal': return cs
        if mode == 'multiply': return cb * cs
        if mode == 'screen': return cb + cs - cb * cs
        if mode == 'linear-burn': return np.maximum(0, cb + cs - 1)
        if mode == 'darker-color': return np.where((lum(cs) < lum(cb))[..., None], cs, cb)
        if mode == 'lighter-color': return np.where((lum(cs) > lum(cb))[..., None], cs, cb)
        if mode in ('vivid-light', 'color-burn', 'color-dodge'):
            def burn(b, s): return np.where(b >= 1, 1.0, np.where(s <= 0, 0.0, 1 - np.minimum(1, (1 - b) / np.where(s <= 0, 1, s))))
            def dodge(b, s): return np.where(b <= 0, 0.0, np.where(s >= 1, 1.0, np.minimum(1, b / np.where(s >= 1, 1, 1 - s))))
            if mode == 'color-burn': return burn(cb, cs)
            if mode == 'color-dodge': return dodge(cb, cs)
            return np.where(cs <= 0.5, burn(cb, 2 * cs), dodge(cb, 2 * cs - 1))   # Vivid Light: burn by 2s, dodge by 2s - 1
        if mode == 'linear-light': return np.clip(cb + 2 * cs - 1, 0, 1)
        if mode == 'pin-light': return np.where(cs <= 0.5, np.minimum(cb, 2 * cs), np.maximum(cb, 2 * cs - 1))
        if mode == 'hard-mix': return np.where(cb + cs >= 1 - 1e-9, 1.0, 0.0)   # per channel: white when the sum reaches 255
        if mode == 'subtract': return np.maximum(0, cb - cs)
        if mode == 'divide': return np.where(cs <= 0, np.where(cb <= 0, 0.0, 1.0), np.minimum(1, cb / np.where(cs <= 0, 1, cs)))
    raise KeyError(mode)


def general(back, src, mode, k=1.0):
    """W3C Compositing general formula on the straight 8-bit pixels the canvas hands the blend (getImageData), source alpha
    scaled by k; returns straight 8-bit RGBA rounded as a canvas stores it."""
    cb, ab = back[..., :3] / 255.0, back[..., 3:] / 255.0
    cs, a_s = src[..., :3] / 255.0, src[..., 3:] / 255.0 * k
    B = np.clip(mix(mode, cb, cs), 0, 1)
    ao = a_s + ab * (1 - a_s)
    co = a_s * (1 - ab) * cs + a_s * ab * B + (1 - a_s) * ab * cb
    with np.errstate(divide='ignore', invalid='ignore'):
        rgb = np.where(ao > 0, co / np.where(ao > 0, ao, 1), 0)
    return np.round(np.concatenate([rgb, ao], -1) * 255).astype(np.uint8)


def dissolve_noise(w, h):
    """The fixed per-pixel threshold Dissolve uses (0..254, a 32-bit integer hash of the pixel position)."""
    M = 0xFFFFFFFF
    def imul(a, b): return (a * b) & M
    y, x = np.mgrid[0:h, 0:w].astype(np.uint64)
    v = imul(x, 0x27d4eb2d) ^ imul((y + 0x3c6ef372) & M, 0x165667b1)
    v = imul(v ^ (v >> 15), 0x85ebca6b)
    v = imul(v ^ (v >> 13), 0xc2b2ae35)
    return ((v ^ (v >> 16)) % 255).astype(np.int64)


def random_layer(rng, h, w, alphas=(0, 1, 3, 9, 40, 64, 128, 180, 230, 254, 255)):
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    a[..., 3] = np.array(alphas, np.uint8)[rng.integers(0, len(alphas), (h, w))]
    return a


def pm_diff(a, b):
    """Max difference in the premultiplied 8-bit space a canvas stores."""
    return int(np.abs(premultiplied(a).astype(int) - premultiplied(b).astype(int)).max())


ADD = """const im={expr};const c=document.createElement('canvas');c.width=im.width;c.height=im.height;c.getContext('2d').putImageData(im,0,0);
  const L=Ed.layers.add({{name:{name},canvas:c,history:false}});Ed.layers.set(L,{props},{{history:false}});Ed.layers.index(L)"""
FINGERPRINT = """(() => { const h = c => { if (!c) return null; const d = new Uint32Array(Ed.util.read(c).data.buffer);
    let x = 2166136261; for (let i = 0; i < d.length; i++) x = Math.imul(x ^ d[i], 16777619); return x >>> 0; };
  return {active: Ed.doc.activeIndex, layers: Ed.doc.layers.map(L => [L.id, L.name, L.kind, L.visible, L.opacity, L.fill, L.blend, L.clip, JSON.stringify(L.locks), L.maskEnabled,
      h(L.canvas), h(L.mask), JSON.stringify(L.effects), JSON.stringify(L.data)])}; })()"""
SPILL = """[...document.querySelectorAll('#edDock .ed-layers-row *, #edDock .ed-layers-foot *, #edDock .ed-layer > *')].filter(e => {
    if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden') return false; const r = e.getBoundingClientRect(), p = e.closest('#edPanel-layers').getBoundingClientRect();
    return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1); }).map(e => (e.id || e.className.baseVal || e.className || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 20))"""


class LayersModule(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers -------------------------------------------------------------------------------------------------------
    def state(self): return self.browser.eval('Ed.test.state')

    def names(self): return [l['name'] for l in self.state()['layers']]

    def labels(self): return self.browser.eval('Ed.history.list().map(s => s.label)')

    def fingerprint(self): return self.browser.eval(FINGERPRINT)

    def add_layer(self, array, name='Layer', **props):
        b = self.browser
        return b.eval(ADD.format(expr=b.push_pixels(array), name=json.dumps(name), props=json.dumps(props)))

    def set_mask(self, index, alpha):
        b = self.browser
        m = np.zeros(alpha.shape + (4,), np.uint8); m[..., :3] = 255; m[..., 3] = alpha
        b.eval(f"const im={b.push_pixels(m)};const c=document.createElement('canvas');c.width=im.width;c.height=im.height;"
               f"c.getContext('2d').putImageData(im,0,0);Ed.layers.set({index},{{mask:c}},{{history:false}});true")

    def load(self, array):
        b = self.browser
        b.load_editor_image(array)
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true; Ed.setTool('hand'); Ed.panels.show('layers'); true")
        b.settle(3)
        return b

    def row(self, index, part='.ed-layer-name'): return f'{PANEL} .ed-layer[data-index="{index}"] {part}'.strip()

    def click_row(self, index, modifiers=0, part='.ed-layer-name'):
        self.browser.settle(2)   # the panel redraws once per frame
        self.browser.click_selector(self.row(index, part), modifiers=modifiers)
        self.browser.settle(2)

    def selected(self): return self.browser.eval('Ed.layersPanel.selected().map(L => Ed.layers.index(L))')

    def key(self, spec):
        b = self.browser
        b.eval("document.activeElement && document.activeElement !== document.body && document.activeElement.blur(); true")
        b.key(spec); b.settle(2)

    def undo_to(self, index):
        b = self.browser
        b.eval('document.activeElement && document.activeElement.blur && document.activeElement.blur(); true')
        while b.eval('Ed.history.index') > index:
            n = b.eval('Ed.history.index')
            b.key('Ctrl+Z'); b.wait_for(f'Ed.history.index === {n - 1}')
        b.settle(2)

    def popup_item(self, item):
        b = self.browser
        b.wait_for(f"!!document.querySelector('.ed-lp-popup [data-item=\"{item}\"]')")
        b.click_selector(f'.ed-lp-popup [data-item="{item}"]'); b.settle(2)

    def assertSameComposite(self, before, msg, limit=2):
        after = self.browser.editor_pixels()
        d = pm_diff(after, before)
        self.assertLessEqual(d, limit, f'{msg}: the composite changed by {d} levels')
        return d

    # ----- 1. the panel: layout and the core's selectors ---------------------------------------------------------------
    def test_01_panel_layout_at_1366_and_1920(self):
        b = self.browser
        self.assertNoBrowserErrors(everything=True)
        b.select_asset('edit-photo.png')
        b.eval("Ed.setTool('brush'); Ed.layers.add({name: 'A layer with quite a long name to see how the row copes'}); Ed.layers.addMask(1, 'reveal'); true")
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h)
                b.eval("Ed.view.fit(); document.querySelector('.inspector').scrollTop = 0; Ed.panels.show('layers'); true"); b.settle(4)
                b.screenshot(f'layers-panel-{w}x{h}.png')
                b.screenshot(f'layers-panel-{w}x{h}-dock.png', clip=tuple(b.eval("(() => { const r = document.getElementById('edDock').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")))
                self.assertEqual(b.eval(SPILL), [], f'nothing spills out of the Layers panel at {w}x{h}')
                rows = b.eval(f"[...document.querySelectorAll('{PANEL} .ed-layer')].filter(r => {{ const q = r.getBoundingClientRect(); return q.top >= 0 && q.bottom <= innerHeight; }}).length")
                self.assertEqual(rows, 2, f'both layer rows are on screen at {w}x{h}')
                cut = b.eval(f"[...document.querySelectorAll('{PANEL} .ed-layer-action, {PANEL} .ed-lock, {PANEL} .ed-lp-pmenu, {PANEL} select.ed-blend')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => e.className)")
                self.assertEqual(cut, [], 'no control has cut-off content')
                menu = b.eval(f"(() => {{ const m = document.querySelector('{PANEL} .ed-lp-pmenu').getBoundingClientRect(), p = document.getElementById('edDock').getBoundingClientRect(); return m.right <= p.right && m.left >= p.left && m.width > 10; }})()")
                self.assertTrue(menu, 'the panel menu button sits inside the dock')
            # the panel menu opens inside the window and lists only working commands
            b.set_viewport(1366, 768); b.settle(3)
            b.click_selector(f'{PANEL} .ed-lp-pmenu'); b.settle(2)
            box = b.eval("(() => { const r = document.querySelector('.ed-lp-popup').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
            self.assertTrue(box[0] >= 0 and box[1] >= 0 and box[2] <= 1366 and box[3] <= 768, f'the panel menu fits the window: {box}')
            items = b.eval("[...document.querySelectorAll('.ed-lp-popup .ed-menu-item')].map(e => e.textContent)")
            self.assertTrue(any('Merge Visible' in i for i in items) and any('Small Thumbnails' in i for i in items), items)
            self.assertFalse(b.eval("!!document.querySelector('.ed-lp-popup .ed-menu-empty')"))
            b.screenshot('layers-panel-menu-1366x768.png')
            b.key('Escape'); b.settle(1)
            self.assertFalse(b.eval("!!document.querySelector('.ed-lp-popup')"))
        finally:
            b.set_viewport(*self.viewport)

    def test_02_core_panel_behaviours_buttons_eye_opacity_blend_rename_drag(self):
        """test_core.py test_04 with the module loaded."""
        b = self.browser
        b.select_asset('edit-photo.png')
        photo = image_array('edit-photo.png')
        b.click_selector(f'{PANEL} [data-action="new"]')
        self.assertEqual(self.names(), ['edit-photo', 'Layer 1'])
        b.click_selector(f'{PANEL} [data-action="duplicate"]')
        self.assertEqual(self.names(), ['edit-photo', 'Layer 1', 'Layer 1 copy'])
        self.assertEqual(self.labels()[-1], 'Duplicate Layer')
        b.eval("Ed.layers.setActive(0)"); b.settle()
        self.assertTrue(b.eval(f"document.querySelector('{PANEL} .ed-layer[data-index=\"0\"]').classList.contains('active')"))
        before = len(self.labels())
        b.set_value(f'{PANEL} .ed-field[data-key="opacity"] input[type=number]', 40)
        self.assertAlmostEqual(self.state()['layers'][0]['opacity'], 0.4)
        self.assertEqual((self.labels()[-1], len(self.labels())), ('Opacity Change', before + 1))
        lx, ly = b.center_of(f'{PANEL} .ed-field[data-key="opacity"] .ed-field-label')
        b.drag([(lx, ly), (lx + 20, ly), (lx + 40, ly)])
        self.assertAlmostEqual(self.state()['layers'][0]['opacity'], 0.6, places=2)
        self.assertEqual(len(self.labels()), before + 2, 'scrubbing is one state')
        b.set_value(f'{PANEL} .ed-field[data-key="fill"] input[type=number]', 70)
        self.assertEqual((self.state()['layers'][0]['fill'], self.labels()[-1]), (0.7, 'Fill Opacity Change'))
        b.set_value(f'{PANEL} select.ed-blend', 'linear-burn')
        self.assertEqual((self.state()['layers'][0]['blend'], self.labels()[-1]), ('linear-burn', 'Blending Change'))
        self.assertEqual(b.eval(f"[...document.querySelectorAll('{PANEL} select.ed-blend option')].map(o => o.value)"), ORDER, 'every mode in Photoshop order')
        self.assertEqual(b.eval(f"document.querySelectorAll('{PANEL} select.ed-blend hr').length"), 5, 'six groups')
        b.click_selector(self.row(0, '.ed-eye'))
        self.assertFalse(self.state()['layers'][0]['visible'])
        self.assertEqual(self.labels()[-1], 'Hide Layer')
        self.assertEqual(int(b.editor_pixels()[..., 3].max()), 0)
        b.click_selector(self.row(0, '.ed-eye'))
        b.eval("Ed.layers.set(0, {blend: 'normal', opacity: 1, fill: 1})")
        self.assertPixelsClose(b.editor_pixels(), photo)
        # the lock buttons
        b.click_selector(f'{PANEL} .ed-lock[data-lock="alpha"]')
        self.assertTrue(self.state()['layers'][0]['locks']['alpha'])
        # The panel redraws on the next frame after the change: wait for it rather than reading the DOM straight away.
        b.wait_for(f"document.querySelector('{PANEL} .ed-lock[data-lock=\"alpha\"]').getAttribute('aria-pressed') === 'true' && !!document.querySelector('{PANEL} .ed-layer[data-index=\"0\"] .ed-badges .ed-lock-badge')",
                   message='the lock button shows pressed and the row gets its lock badge')
        b.click_selector(f'{PANEL} .ed-lock[data-lock="all"]'); b.settle(2)
        self.assertTrue(b.eval(f"document.querySelector('{PANEL} select.ed-blend').disabled"), 'Lock All freezes the blend mode')
        b.click_selector(f'{PANEL} .ed-lock[data-lock="all"]'); b.click_selector(f'{PANEL} .ed-lock[data-lock="alpha"]')
        self.assertEqual(self.state()['layers'][0]['locks'], {'alpha': False, 'pixels': False, 'position': False, 'all': False})
        # rename with a double-click; Escape cancels
        b.double_click(*b.center_of(self.row(2)))
        b.wait_for(f"document.querySelector('{PANEL} .ed-rename') === document.activeElement")
        b.key('Ctrl+A'); b.type_text('Highlights'); b.key('Enter')
        self.assertEqual((self.state()['layers'][2]['name'], self.labels()[-1]), ('Highlights', 'Rename Layer'))
        b.double_click(*b.center_of(self.row(2)))
        b.wait_for(f"document.querySelector('{PANEL} .ed-rename') === document.activeElement")
        b.type_text('Nope'); b.key('Escape'); b.settle()
        self.assertEqual(self.state()['layers'][2]['name'], 'Highlights')
        # drag the top row below the bottom row
        top = b.center_of(self.row(2))
        bottom = b.eval(f"(() => {{ const r = document.querySelector('{PANEL} .ed-layer[data-index=\"0\"]').getBoundingClientRect(); return [r.left + r.width / 2, r.bottom - 3]; }})()")
        b.drag([top, (top[0], top[1] + 20), (bottom[0], bottom[1] + 2)], steps_between=6)
        self.assertEqual(self.names(), ['Highlights', 'edit-photo', 'Layer 1'])
        self.assertEqual(self.labels()[-1], 'Layer Order')
        self.click_row(2)
        self.assertEqual(self.state()['activeIndex'], 2)
        b.click_selector(f'{PANEL} [data-action="delete"]')
        self.assertEqual(self.names(), ['Highlights', 'edit-photo'])
        b.wait_for(f"document.querySelectorAll('{PANEL} .ed-layer').length === 2")
        # badges from other modules still show (core contract)
        b.eval("Ed.registerLayerBadge(L => L.name === 'Highlights' ? 'T1' : null); true"); b.settle(2)
        self.assertIn('T1', b.text(self.row(0, '.ed-badges')))

    # ----- 2. blend modes ----------------------------------------------------------------------------------------------
    def test_03_extra_blend_modes_match_photoshops_formulas(self):
        b = self.browser
        rng = np.random.default_rng(31)
        H, W = 24, 32
        base, top = random_layer(rng, H, W), random_layer(rng, H, W)
        base[:, :8, 3] = 255   # an opaque strip too
        self.load(base)
        self.add_layer(top, 'Top')
        back, src = b.editor_pixels(0), b.editor_pixels(1)   # the straight pixels the canvas hands the blend
        worst = {}
        for mode in EXTRA + ['multiply']:
            for opacity, fill in ((1, 1), (0.6, 1), (0.7, 0.5)):
                b.eval(f"Ed.layers.set(1, {{blend: '{mode}', opacity: {opacity}, fill: {fill}}}, {{history: false}})")
                got, want = b.editor_pixels(), general(back, src, mode, opacity * fill)
                d = pm_diff(got, want)
                worst[mode] = max(worst.get(mode, 0), d)
                self.assertLessEqual(d, 1, f'{mode} at opacity {opacity}, fill {fill}: {d} levels from the formula')
        print('\n[layers] extra blend modes, worst premultiplied difference:', worst)
        # Dissolve: each pixel shows fully opaque or not at all; it shows where its alpha is above a fixed threshold.
        noise = dissolve_noise(W, H)
        for opacity in (1, 0.5):
            b.eval(f"Ed.layers.set(1, {{blend: 'dissolve', opacity: {opacity}, fill: 1}}, {{history: false}})")
            got = b.editor_pixels()
            a = src[..., 3].astype(int) * opacity
            shown = noise < np.round(a)
            close = np.abs(noise - a) <= 1   # rounding of the scaled alpha
            want = np.where(shown[..., None], np.concatenate([src[..., :3], np.full((H, W, 1), 255, np.uint8)], -1), back)
            self.assertPixelsClose(got, want, max_abs=1, mask=~close, msg=f'dissolve at opacity {opacity}')
            self.assertTrue(set(np.unique(got[..., 3][shown & ~close & (back[..., 3] == 0)])) <= {255})
        # The New Layer dialog's neutral fill changes nothing until painted (over opaque pixels, as in Photoshop)
        b.eval("Ed.layers.set(1, {blend: 'normal', opacity: 1}, {history: false})")
        before = b.editor_pixels()
        opaque = before[..., 3] == 255
        self.assertGreater(int(opaque.sum()), 100)
        for mode in ('linear-burn', 'subtract', 'vivid-light', 'linear-light', 'pin-light'):
            b.eval(f"Ed.menu.run('Layer/New/Layer...', {{mode: '{mode}', neutral: true, name: 'N'}}); true"); b.settle(2)
            self.assertEqual(self.state()['layers'][-1]['blend'], mode)
            limit = 2 if mode in ('vivid-light',) else 1
            self.assertLessEqual(pm_diff(b.editor_pixels()[opaque], before[opaque]), limit, f'neutral {mode} layer')
            b.eval("Ed.layers.remove(2, {history: false}); true")
        self.assertNoBrowserErrors()

    def test_04_blend_keys_fill_digits_and_layer_steps(self):
        b = self.browser
        self.load(np.full((16, 16, 4), 128, np.uint8))
        b.eval("for (let i = 0; i < 3; i++) Ed.layers.add({history: false}); Ed.layers.setActive(1); true"); b.settle(2)
        for spec, mode in (('Alt+Shift+M', 'multiply'), ('Alt+Shift+A', 'linear-burn'), ('Alt+Shift+I', 'dissolve'), ('Alt+Shift+L', 'hard-mix'), ('Alt+Shift+N', 'normal')):
            self.key(spec)
            self.assertEqual(self.state()['layers'][1]['blend'], mode, spec)
        self.key('Shift+=')
        self.assertEqual(self.state()['layers'][1]['blend'], 'dissolve', 'Shift+plus: the next mode')
        self.key('Shift+-'); self.key('Shift+-')
        self.assertEqual(self.state()['layers'][1]['blend'], 'luminosity', 'Shift+minus wraps to the last mode')
        self.key('Shift+5')
        self.assertEqual((self.state()['layers'][1]['fill'], self.labels()[-1]), (0.5, 'Fill Opacity Change'))
        self.key('7')
        self.assertAlmostEqual(self.state()['layers'][1]['opacity'], 0.7, msg='digits still set the layer opacity (core)')
        # Alt+] / Alt+[ walk the stack, Alt+. / Alt+, jump to the top / bottom, Shift adds to the selection
        self.key('Alt+]'); self.assertEqual(self.state()['activeIndex'], 2)
        self.key('Alt+.'); self.assertEqual(self.state()['activeIndex'], 3)
        self.key('Alt+,'); self.assertEqual(self.state()['activeIndex'], 0)
        self.key('Alt+Shift+]')
        self.assertEqual((self.state()['activeIndex'], self.selected()), (1, [0, 1]))
        self.key('Alt+['); self.assertEqual(self.selected(), [0])
        # while a painting tool is active, Shift+Alt+letters and Shift+digits belong to the tool
        b.eval("Ed.setTool('brush'); true")
        self.key('Shift+3')
        self.assertEqual(self.state()['layers'][0]['fill'], 1)
        b.eval("Ed.setTool('hand'); true")

    # ----- 3. the selection of layers, Arrange, Hide, Lock ------------------------------------------------------------
    def test_05_multi_select_arrange_hide_lock_duplicate_delete(self):
        b = self.browser
        self.load(np.full((16, 16, 4), 200, np.uint8))
        b.eval("for (const n of ['A', 'B', 'C', 'D']) Ed.layers.add({name: n, history: false}); Ed.layers.setActive(1); true"); b.settle(2)
        self.assertEqual(self.names(), ['Layer 0', 'A', 'B', 'C', 'D'])
        self.click_row(1); self.click_row(3, CTRL)
        self.assertEqual((self.selected(), self.state()['activeIndex']), ([1, 3], 3))
        b.wait_for(f"document.querySelector('{PANEL} .ed-layer[data-index=\"1\"]').getAttribute('aria-selected') === 'true'")   # the panel redraws on the next frame
        self.click_row(3, CTRL); self.assertEqual(self.selected(), [1], 'Ctrl-click again leaves the selection')
        self.click_row(3, SHIFT); self.assertEqual(self.selected(), [1, 2, 3], 'Shift-click: a range')
        n = len(self.labels())
        b.set_value(f'{PANEL} .ed-field[data-key="opacity"] input[type=number]', 50)
        self.assertEqual([l['opacity'] for l in self.state()['layers']], [1, 0.5, 0.5, 0.5, 1])
        self.assertEqual(len(self.labels()), n + 1, 'one state for all of them')
        b.set_value(f'{PANEL} select.ed-blend', 'screen')
        self.assertEqual([l['blend'] for l in self.state()['layers']][1:4], ['screen'] * 3)
        b.click_selector(f'{PANEL} .ed-lock[data-lock="position"]')
        self.assertEqual([l['locks']['position'] for l in self.state()['layers']], [False, True, True, True, False])
        self.key('Ctrl+/')   # Lock Layers... dialog
        b.wait_for("!!document.querySelector('dialog[data-ed-dialog=\"Lock Layers\"]')")
        b.set_value('dialog[data-ed-dialog="Lock Layers"] input[name=position]', False)
        b.click_selector('dialog[data-ed-dialog="Lock Layers"] button.primary'); b.settle(2)
        self.assertEqual([l['locks']['position'] for l in self.state()['layers']], [False] * 5)
        # Arrange
        self.key('Ctrl+]')
        self.assertEqual(self.names(), ['Layer 0', 'D', 'A', 'B', 'C'])
        self.assertEqual(self.labels()[-1], 'Layer Order')
        self.key('Ctrl+[')
        self.assertEqual(self.names(), ['Layer 0', 'A', 'B', 'C', 'D'])
        self.key('Shift+Ctrl+[')
        self.assertEqual(self.names(), ['A', 'B', 'C', 'Layer 0', 'D'])
        self.key('Shift+Ctrl+]')
        self.assertEqual(self.names(), ['Layer 0', 'D', 'A', 'B', 'C'])
        b.eval("Ed.menu.run('Layer/Arrange/Reverse')"); b.settle(2)
        self.assertEqual(self.names(), ['Layer 0', 'D', 'C', 'B', 'A'])
        self.assertEqual(self.selected(), [2, 3, 4])
        # Hide Layers and back (no key: Ctrl+, opens the lab's Settings)
        b.eval("Ed.menu.run('Layer/Hide Layers')"); b.settle(2)
        self.assertEqual([l['visible'] for l in self.state()['layers']], [True, True, False, False, False])
        self.assertEqual(self.labels()[-1], 'Hide Layers')
        b.eval("Ed.menu.run('Layer/Hide Layers')"); b.settle(2)
        self.assertTrue(all(l['visible'] for l in self.state()['layers']))
        # Duplicate and delete the selection
        b.click_selector(f'{PANEL} [data-action="duplicate"]')
        self.assertEqual(self.names(), ['Layer 0', 'D', 'C', 'B', 'A', 'C copy', 'B copy', 'A copy'])
        self.assertEqual((self.labels()[-1], self.selected()), ('Duplicate Layers', [5, 6, 7]))
        b.click_selector(f'{PANEL} [data-action="delete"]')
        self.assertEqual((self.names(), self.labels()[-1]), (['Layer 0', 'D', 'C', 'B', 'A'], 'Delete Layers'))
        # Select All Layers / Deselect Layers
        self.key('Alt+Ctrl+A')
        self.assertEqual(self.selected(), [0, 1, 2, 3, 4])
        self.assertFalse(b.eval("Ed.menu.list().find(m => m.path === 'Layer/Delete Layer').enabled"), 'all layers selected: nothing would be left')
        b.eval("Ed.menu.run('Select/Deselect Layers')"); b.settle(2)
        self.assertEqual(len(self.selected()), 1)
        # keyboard in the list: arrows move, Shift+arrows extend
        b.eval(f"document.querySelector('{PANEL} .ed-layer-list').focus(); Ed.layers.setActive(2); true"); b.settle(2)
        b.key('ArrowUp'); b.settle(2)
        self.assertEqual(self.state()['activeIndex'], 3)
        b.key('Shift+ArrowUp'); b.settle(2)
        self.assertEqual(self.selected(), [3, 4])
        # undo all the way back restores the stack
        self.undo_to(1)
        self.assertEqual(self.names(), ['Layer 0', 'A', 'B', 'C', 'D'])

    def test_06_eyes_solo_drag_rows_onto_buttons_and_context_menus(self):
        b = self.browser
        rng = np.random.default_rng(5)
        base = random_layer(rng, 20, 20, alphas=(255,))
        self.load(base)
        for i in range(3): self.add_layer(random_layer(rng, 20, 20, alphas=(255, 128)), f'L{i + 1}')
        b.eval('Ed.layers.setActive(1); true'); b.settle(2)
        start = b.eval('Ed.history.index')
        # Alt-click an eye: only that layer shows; again: the others come back
        b.click_selector(self.row(2, '.ed-eye'), modifiers=ALT); b.settle(2)
        self.assertEqual([l['visible'] for l in self.state()['layers']], [False, False, True, False])
        b.click_selector(self.row(2, '.ed-eye'), modifiers=ALT); b.settle(2)
        self.assertTrue(all(l['visible'] for l in self.state()['layers']))
        # drag down the eyes: three layers hidden in one state
        n = len(self.labels())
        pts = [b.center_of(self.row(i, '.ed-eye')) for i in (3, 2, 1)]
        b.drag(pts, steps_between=5); b.settle(2)
        self.assertEqual([l['visible'] for l in self.state()['layers']], [True, False, False, False])
        self.assertEqual((len(self.labels()), self.labels()[-1]), (n + 1, 'Hide Layers'))
        self.undo_to(start)
        # drag a row onto New: a copy; onto the bin: gone
        r = b.center_of(self.row(1))
        b.drag([r, (r[0], r[1] + 12), b.center_of(f'{PANEL} [data-action="new"]')], steps_between=6); b.settle(2)
        self.assertEqual(self.names(), ['Layer 0', 'L1', 'L1 copy', 'L2', 'L3'])
        r = b.center_of(self.row(4))
        b.drag([r, (r[0], r[1] + 12), b.center_of(f'{PANEL} [data-action="delete"]')], steps_between=6); b.settle(2)
        self.assertEqual(self.names(), ['Layer 0', 'L1', 'L1 copy', 'L2'])
        self.undo_to(start)
        # right-click a row: Photoshop's layer menu
        x, y = b.center_of(self.row(2))
        b.click(x, y, button='right'); b.settle(2)
        items = b.eval("[...document.querySelectorAll('.ed-lp-popup .ed-menu-item')].map(e => [e.dataset.item, e.textContent, e.disabled])")
        ids = [i[0] for i in items]
        for want in ('duplicate', 'delete', 'rename', 'mask-add', 'clip', 'merge', 'merge-visible', 'stamp', 'flatten'): self.assertIn(want, ids)
        self.assertNotIn('Nothing here', ' '.join(i[1] for i in items))
        self.assertEqual(self.state()['activeIndex'], 2, 'right-click selects the row')
        self.popup_item('rename')
        b.wait_for(f"document.querySelector('{PANEL} .ed-rename') === document.activeElement")
        b.key('Ctrl+A'); b.type_text('Renamed'); b.key('Enter'); b.settle(2)
        self.assertEqual(self.names()[2], 'Renamed')
        b.click(*b.center_of(self.row(2)), button='right'); b.settle(1)
        self.popup_item('mask-add')
        self.assertTrue(b.eval('!!Ed.layers.get(2).mask && Ed.doc.target === "mask"'))
        # right-click the mask thumbnail, the thumbnail and the eye
        b.click(*b.center_of(self.row(2, '.ed-mask-thumb')), button='right'); b.settle(1)
        self.popup_item('mask-toggle')
        self.assertFalse(b.eval('Ed.layers.get(2).maskEnabled'))
        b.click(*b.center_of(self.row(2, '.ed-thumb:not(.ed-mask-thumb)')), button='right'); b.settle(1)
        self.popup_item('thumbs-small')
        self.assertEqual(b.eval(f"Math.round(document.querySelector('{PANEL} .ed-layer[data-index=\"2\"] .ed-thumb').getBoundingClientRect().width)"), 22)
        b.click(*b.center_of(self.row(2, '.ed-thumb:not(.ed-mask-thumb)')), button='right'); b.settle(1)
        self.popup_item('thumbs-none')
        b.wait_for(f"document.querySelectorAll('{PANEL} .ed-thumb').length === 0")   # the panel redraws on the next frame
        self.assertTrue(b.eval(f"!!document.querySelector('{PANEL} .ed-layer[data-index=\"2\"] .ed-lp-masktag')"), 'a mask can still be clicked without thumbnails')
        b.click(*b.center_of(f'{PANEL} .ed-lp-pmenu')); b.settle(1)
        self.popup_item('thumbs-medium')
        self.assertEqual(b.eval(f"Math.round(document.querySelector('{PANEL} .ed-thumb').getBoundingClientRect().width)"), 34)
        b.click(*b.center_of(self.row(3, '.ed-eye')), button='right'); b.settle(1)
        self.popup_item('eye-others')
        self.assertEqual([l['visible'] for l in self.state()['layers']], [False, False, False, True])
        # Alt-click New: the New Layer dialog
        b.click_selector(f'{PANEL} [data-action="new"]', modifiers=ALT)
        b.wait_for("!!document.querySelector('dialog[data-ed-dialog=\"New Layer\"]')")
        b.set_value('dialog[data-ed-dialog="New Layer"] input[name=name]', 'Shading')
        b.set_value('dialog[data-ed-dialog="New Layer"] select[name=mode]', 'multiply')
        b.set_value('dialog[data-ed-dialog="New Layer"] input[name=clip]', True)
        b.click_selector('dialog[data-ed-dialog="New Layer"] button.primary'); b.settle(2)
        L = self.state()['layers'][4]
        self.assertEqual((L['name'], L['blend'], L['clip']), ('Shading', 'multiply', True))
        self.assertEqual(self.labels()[-1], 'New Layer')

    # ----- 4. masks -----------------------------------------------------------------------------------------------------
    def test_07_masks_add_disable_apply_from_transparency_and_selections(self):
        b = self.browser
        rng = np.random.default_rng(8)
        H, W = 24, 24
        back = random_layer(rng, H, W, alphas=(255,))
        top = random_layer(rng, H, W, alphas=(255, 200, 90, 0))
        self.load(back)
        self.add_layer(top, 'Top')
        P0, P1 = b.editor_pixels(0), b.editor_pixels(1)
        plain = b.editor_pixels()
        # with a selection the mask button reveals it
        b.eval("Ed.select.rect(4, 6, 10, 8); true")
        b.click_selector(f'{PANEL} [data-action="mask"]')
        self.assertEqual(self.labels()[-1], 'Add Layer Mask')
        sel = np.zeros((H, W), bool); sel[6:14, 4:14] = True
        m = b.pixels('Ed.layers.get(1).mask')[..., 3]
        self.assertTrue(np.array_equal(m > 0, sel), 'the mask is the selection')
        self.assertPixelsClose(b.editor_pixels(), np.where(sel[..., None], plain, P0), max_abs=1)
        self.assertTrue(b.eval(f"!!document.querySelector('{PANEL} .ed-mask-thumb.target')"))
        # Shift-click the mask thumbnail: disabled (everything shows); again: enabled
        self.click_row(1, SHIFT, '.ed-mask-thumb')
        self.assertEqual((b.eval('Ed.layers.get(1).maskEnabled'), self.labels()[-1]), (False, 'Disable Layer Mask'))
        self.assertPixelsClose(b.editor_pixels(), plain, max_abs=1)
        b.wait_for(f"document.querySelector('{PANEL} .ed-mask-thumb').classList.contains('disabled')")   # the panel redraws on the next frame
        self.click_row(1, SHIFT, '.ed-mask-thumb')
        self.assertTrue(b.eval('Ed.layers.get(1).maskEnabled'))
        # Ctrl-click the mask: it becomes the selection; Ctrl-click the thumbnail: the layer's alpha
        b.eval("Ed.select.none(); true")
        self.click_row(1, CTRL, '.ed-mask-thumb')
        self.assertTrue(np.array_equal(b.editor_pixels('selection')[..., 3] > 0, sel))
        self.click_row(1, CTRL, '.ed-thumb:not(.ed-mask-thumb)')
        self.assertTrue(np.array_equal(b.editor_pixels('selection')[..., 3], P1[..., 3]), 'Ctrl-click the thumbnail: Load Selection from its alpha')
        self.assertEqual(self.labels()[-1], 'Load Selection')
        self.click_row(1, CTRL | ALT, '.ed-mask-thumb')
        want = np.where(sel, 0, P1[..., 3])
        self.assertTrue(np.array_equal(b.editor_pixels('selection')[..., 3], want), 'Alt subtracts the mask')
        b.eval("Ed.select.none(); true")
        # Apply Layer Mask: the pixels keep the masked look, the mask is gone
        b.eval("Ed.menu.run('Layer/Layer Mask/Apply')"); b.settle(2)
        self.assertFalse(self.state()['layers'][1].get('mask'))
        self.assertTrue(b.eval('Ed.layers.get(1).mask === null'))
        self.assertPixelsClose(b.editor_pixels(1)[..., 3], np.where(sel, P1[..., 3], 0))
        self.assertPixelsClose(b.editor_pixels(), np.where(sel[..., None], plain, P0), max_abs=1)
        self.key('Ctrl+Z')
        # Alt-click the mask button: a mask that hides everything; Delete Layer Mask by the bin while the mask is targeted
        b.eval("Ed.layers.removeMask(1, false); true"); b.settle(2)
        b.click_selector(f'{PANEL} [data-action="mask"]', modifiers=ALT)
        self.assertPixelsClose(b.editor_pixels(), P0, max_abs=0)
        b.click_selector(f'{PANEL} [data-action="delete"]')
        self.assertEqual((self.labels()[-1], len(self.names())), ('Delete Layer Mask', 2))
        self.assertPixelsClose(b.editor_pixels(), plain, max_abs=1)
        # From Transparency: alpha moves into the mask, the picture stays
        b.eval("Ed.menu.run('Layer/Layer Mask/From Transparency')"); b.settle(2)
        self.assertEqual(self.labels()[-1], 'From Transparency')
        self.assertTrue(np.all(b.editor_pixels(1)[..., 3] == 255))
        self.assertTrue(np.array_equal(b.pixels('Ed.layers.get(1).mask')[..., 3], P1[..., 3]))
        self.assertLessEqual(pm_diff(b.editor_pixels(), plain), 2)
        b.key('Ctrl+Z'); b.wait_for('!Ed.layers.get(1).mask')
        self.assertPixelsClose(b.editor_pixels(1), P1)
        # the Disable menu item follows the state
        b.eval("Ed.layers.addMask(1, 'reveal'); Ed.layers.set(1, {maskEnabled: false}); true")
        self.assertEqual(b.eval("Ed.menu.get('Layer/Layer Mask/Disable').def.label()"), 'Enable')

    def test_08_mask_view_alone_and_ruby_overlay(self):
        b = self.browser
        H, W = 32, 32
        back = np.zeros((H, W, 4), np.uint8); back[...] = (40, 160, 60, 255)
        self.load(back)
        self.add_layer(np.full((H, W, 4), 255, np.uint8), 'White')
        alpha = np.zeros((H, W), np.uint8); alpha[:, 16:] = 255; alpha[:8, :] = 128
        self.set_mask(1, alpha)
        b.eval("Ed.view.fit(); true"); b.settle(3)
        baseline = self.overlay_count()   # the pixel grid at this zoom
        def overlay_at(x, y):
            cx, cy = b.image_to_client(x + 0.5, y + 0.5)
            ox, oy = b.eval("(() => { const r = document.getElementById('viewOverlay').getBoundingClientRect(); return [r.left, r.top]; })()")
            return b.pixels("document.getElementById('viewOverlay')")[int(cy - oy), int(cx - ox)]
        self.click_row(1, ALT, '.ed-mask-thumb')
        self.assertEqual(b.eval('Ed.layersPanel.maskView'), {'id': b.eval('Ed.layers.get(1).id'), 'mode': 'alone'})
        self.assertEqual(self.state()['target'], 'mask')
        b.settle(3)
        for (x, y), v in (((4, 20), 0), ((24, 20), 255), ((4, 3), 128)):
            px = overlay_at(x, y)
            self.assertTrue(px[3] == 255 and abs(int(px[0]) - v) <= 2 and px[0] == px[1] == px[2], f'mask alone at {x},{y}: {px}, want gray {v}')
        b.screenshot('layers-mask-alone.png')
        self.click_row(1, ALT, '.ed-mask-thumb')
        self.assertIsNone(b.eval('Ed.layersPanel.maskView'))
        # \ shows the mask as a red overlay over the picture
        self.key('\\'); b.settle(3)
        self.assertEqual(b.eval('Ed.layersPanel.maskView.mode'), 'ruby')
        hidden, shown = overlay_at(4, 20), overlay_at(24, 20)
        self.assertTrue(hidden[0] > 200 and hidden[1] < 40 and 100 < hidden[3] < 150, f'hidden area: half red {hidden}')
        self.assertEqual(int(shown[3]), 0, 'shown area: nothing drawn')
        b.screenshot('layers-mask-ruby.png')
        # clicking the pixels thumbnail ends the mask view; so does choosing another layer
        self.click_row(1, 0, '.ed-thumb:not(.ed-mask-thumb)')
        self.assertIsNone(b.eval('Ed.layersPanel.maskView'))
        self.click_row(1, SHIFT | ALT, '.ed-mask-thumb')
        self.assertEqual(b.eval('Ed.layersPanel.maskView.mode'), 'ruby')
        self.click_row(0)
        self.assertIsNone(b.eval('Ed.layersPanel.maskView'))
        b.settle(3)
        self.assertEqual(self.overlay_count(), baseline, 'the mask overlay is gone')

    def overlay_count(self):
        b = self.browser
        if b.eval("document.getElementById('viewOverlay').hidden"): return 0
        return int(b.pixels("document.getElementById('viewOverlay')")[..., 3].astype(bool).sum())

    # ----- 5. clipping masks -----------------------------------------------------------------------------------------------
    def test_09_clipping_masks_by_key_menu_and_alt_click(self):
        b = self.browser
        H, W = 16, 16
        back = np.zeros((H, W, 4), np.uint8); back[...] = (20, 30, 200, 255)
        base = np.zeros((H, W, 4), np.uint8); base[4:12, 4:12] = (200, 200, 0, 255)
        member = np.zeros((H, W, 4), np.uint8); member[...] = (250, 0, 0, 255)
        self.load(back)
        self.add_layer(base, 'Base'); self.add_layer(member, 'Member')
        inside = np.zeros((H, W), bool); inside[4:12, 4:12] = True
        clipped = np.where(inside[..., None], member, back)
        self.key('Alt+Ctrl+G')
        self.assertTrue(self.state()['layers'][2]['clip'])
        self.assertEqual(self.labels()[-1], 'Create Clipping Mask')
        self.assertPixelsClose(b.editor_pixels(), clipped)
        b.wait_for(f"document.querySelector('{PANEL} .ed-layer[data-index=\"2\"]').classList.contains('clipped') && !document.querySelector('{PANEL} .ed-layer[data-index=\"2\"] .ed-clip-arrow').hidden")   # the panel redraws on the next frame
        self.assertTrue(b.eval(f"document.querySelector('{PANEL} .ed-layer[data-index=\"1\"]').classList.contains('base')"))
        self.assertEqual(b.eval("Ed.menu.get('Layer/Create Clipping Mask').def.label()"), 'Release Clipping Mask')
        self.key('Alt+Ctrl+G')
        self.assertFalse(self.state()['layers'][2]['clip'])
        self.assertEqual(self.labels()[-1], 'Release Clipping Mask')
        self.assertPixelsClose(b.editor_pixels(), member)
        # Alt-click the line between the two rows
        y = b.eval(f"document.querySelector('{PANEL} .ed-layer[data-index=\"2\"]').getBoundingClientRect().bottom")
        x = b.center_of(self.row(2))[0]
        b.mouse_move(x, y - 2, modifiers=ALT); b.settle(1)
        self.assertFalse(b.eval(f"document.querySelector('{PANEL} .ed-lp-clipline').hidden"), 'Alt shows where the clipping mask goes')
        b.click(x, y - 2, modifiers=ALT); b.settle(2)
        self.assertTrue(self.state()['layers'][2]['clip'])
        b.click(x, y + 2, modifiers=ALT); b.settle(2)
        self.assertFalse(self.state()['layers'][2]['clip'], 'Alt-click again releases it')
        # Release takes the clipped layers above along; the bottom layer cannot clip
        self.add_layer(member, 'Member 2')
        b.eval("Ed.layers.set(2, {clip: true}, {history: false}); Ed.layers.set(3, {clip: true}, {history: false}); Ed.layers.setActive(2); true"); b.settle(2)
        self.key('Alt+Ctrl+G')
        self.assertEqual([l['clip'] for l in self.state()['layers']], [False, False, False, False])
        b.eval("Ed.layers.setActive(0); true"); b.settle(1)
        self.assertFalse(b.eval("Ed.menu.list().find(m => m.path === 'Layer/Create Clipping Mask').enabled"))
        # a new layer above a clipped one joins its group; one dragged out of the group leaves it
        b.eval("Ed.layers.set(2, {clip: true}, {history: false}); Ed.layers.setActive(2); true"); b.settle(2)
        self.key('Shift+Ctrl+N')
        self.assertEqual([l['clip'] for l in self.state()['layers']], [False, False, True, True, False])
        # dragging a clipping base to the top takes its clipped layers along; the picture of the group is unchanged
        b.eval("Ed.layers.remove(3, {history: false}); Ed.layers.set(3, {name: 'Top', visible: false}, {history: false}); true"); b.settle(2)
        self.assertEqual((self.names(), [l['clip'] for l in self.state()['layers']]), (['Layer 0', 'Base', 'Member', 'Top'], [False, False, True, False]))
        r, top = b.center_of(self.row(1)), b.eval(f"(() => {{ const r = document.querySelector('{PANEL} .ed-layer[data-index=\"3\"]').getBoundingClientRect(); return [r.left + r.width / 2, r.top + 3]; }})()")
        b.drag([r, (r[0], r[1] - 12), (top[0], top[1] - 2)], steps_between=6); b.settle(2)
        self.assertEqual((self.names(), [l['clip'] for l in self.state()['layers']]), (['Layer 0', 'Top', 'Base', 'Member'], [False, False, False, True]))
        self.assertPixelsClose(b.editor_pixels(), clipped, msg='the group still clips Member to Base')
        # Ctrl-click New: the new layer goes below the active one
        b.eval("Ed.layers.setActive(1); true"); b.settle(1)
        old = self.names()
        b.click_selector(f'{PANEL} [data-action="new"]', modifiers=CTRL); b.settle(1)
        self.assertEqual((len(self.names()), self.names()[2:], self.state()['activeIndex']), (5, old[1:], 1))

    # ----- 6. merges keep the picture --------------------------------------------------------------------------------------
    def stack(self, seed=3, H=20, W=24):
        """Opaque backdrop, a soft middle layer and a screen layer with a soft mask, opacity and Fill."""
        b = self.browser
        rng = np.random.default_rng(seed)
        self.load(random_layer(rng, H, W, alphas=(255,)))
        self.add_layer(random_layer(rng, H, W), 'Middle', blend='multiply', opacity=0.8)
        self.add_layer(random_layer(rng, H, W), 'Top', blend='screen', opacity=0.7, fill=0.8)
        self.set_mask(2, rng.integers(0, 256, (H, W), dtype=np.uint8))
        b.eval("Ed.layers.setActive(2); true"); b.settle(2)
        return rng

    def merge_check(self, run, label, names, limit=2):
        b = self.browser
        before, fp, idx = b.editor_pixels(), self.fingerprint(), b.eval('Ed.history.index')
        run()
        b.settle(2)
        self.assertEqual(self.labels()[-1], label)
        self.assertEqual(self.names(), names)
        d = self.assertSameComposite(before, label, limit)
        self.undo_to(idx)
        self.assertEqual(self.fingerprint(), fp, f'{label}: undo restores every layer exactly')
        return d

    def test_10_merge_down_layers_visible_and_flatten_keep_the_composite(self):
        b = self.browser
        rows = []
        self.stack()
        rows.append(('Merge Down', self.merge_check(lambda: self.key('Ctrl+E'), 'Merge Down', ['Layer 0', 'Middle'])))
        self.assertEqual(b.eval("[Ed.layers.get(1).blend, Ed.layers.get(1).opacity, Ed.layers.get(1).mask]"), ['multiply', 0.8, None], 'undone')
        # merge into the bottom layer of a see-through document
        rng = np.random.default_rng(12)
        self.load(random_layer(rng, 16, 16))
        self.add_layer(random_layer(rng, 16, 16), 'Over', blend='hard-light', opacity=0.6)
        rows.append(('Merge Down, transparent', self.merge_check(lambda: self.key('Ctrl+E'), 'Merge Down', ['Layer 0'])))
        # Merge Layers: Shift-click a range, Ctrl+E
        self.stack(4)
        self.click_row(1); self.click_row(2, SHIFT)
        self.assertEqual(self.selected(), [1, 2])
        rows.append(('Merge Layers', self.merge_check(lambda: self.key('Ctrl+E'), 'Merge Layers', ['Layer 0', 'Top'])))
        # Merge Visible with a hidden layer in between (it stays, hidden)
        self.stack(5)
        rng = np.random.default_rng(55)
        self.add_layer(random_layer(rng, 20, 24), 'Hidden', visible=False, blend='difference')
        b.eval("Ed.layers.move(3, 1, {history: false}); Ed.layers.setActive(3); true"); b.settle(2)
        self.assertEqual(self.names(), ['Layer 0', 'Hidden', 'Middle', 'Top'])
        rows.append(('Merge Visible', self.merge_check(lambda: self.key('Shift+Ctrl+E'), 'Merge Visible', ['Top', 'Hidden'])))
        # Flatten Image asks about hidden layers; OK discards them and keeps transparency
        rng = np.random.default_rng(6)
        self.load(random_layer(rng, 20, 24))
        self.add_layer(random_layer(rng, 20, 24), 'Soft', blend='linear-light', opacity=0.9)
        self.add_layer(random_layer(rng, 20, 24), 'Gone', visible=False)
        before, idx = b.editor_pixels(), b.eval('Ed.history.index')
        self.assertLess(int(before[..., 3].min()), 255)
        b.eval("Ed.menu.run('Layer/Flatten Image'); true")
        b.wait_for("!!document.querySelector('dialog[data-ed-dialog=\"Flatten Image\"]')")
        b.click_selector('dialog[data-ed-dialog="Flatten Image"] button.primary'); b.settle(2)
        self.assertEqual((self.names(), self.labels()[-1]), (['Layer 0'], 'Flatten Image'))
        rows.append(('Flatten', self.assertSameComposite(before, 'Flatten Image')))
        self.assertPixelsClose(b.editor_pixels(0)[..., 3], before[..., 3], max_abs=1, msg='flatten keeps transparency')
        self.undo_to(idx)
        self.assertEqual(len(self.names()), 3)
        # Stamp Visible: a new layer holding the picture
        before = b.editor_pixels()
        b.eval("Ed.layers.setActive(0); true"); b.settle(1)
        self.key('Alt+Shift+Ctrl+E')
        self.assertEqual((self.labels()[-1], len(self.names()), self.state()['activeIndex']), ('Stamp Visible', 4, 1))
        self.assertLessEqual(pm_diff(b.editor_pixels(1), before), 1, 'the stamped layer is the composite')
        print('\n[layers] merges, composite change in premultiplied levels:', rows)

    def test_11_merges_with_clipping_groups_adjustments_and_effects(self):
        b = self.browser
        rng = np.random.default_rng(21)
        H, W = 16, 24
        back = random_layer(rng, H, W, alphas=(255,))
        base = np.zeros((H, W, 4), np.uint8); base[..., :3] = (200, 180, 40)
        base[..., 3] = np.tile(np.array([0, 40, 128, 255, 255, 200, 90, 0], np.uint8), (H, W // 8))
        def group():
            self.load(back)
            self.add_layer(base, 'Base', blend='screen', opacity=0.6)
            self.add_layer(random_layer(rng, H, W, alphas=(255, 128, 60)), 'M1', clip=True, blend='multiply')
            self.add_layer(random_layer(rng, H, W, alphas=(255, 128, 60)), 'M2', clip=True, opacity=0.5)
        group()
        b.eval("Ed.layers.setActive(1); true"); b.settle(2)
        self.assertEqual(b.eval("Ed.menu.get('Layer/Merge Down').def.label()"), 'Merge Clipping Mask')
        self.merge_check(lambda: self.key('Ctrl+E'), 'Merge Clipping Mask', ['Layer 0', 'Base'])
        b.eval("Ed.layers.setActive(1); true"); self.key('Ctrl+E')
        self.assertEqual(b.eval("[Ed.layers.get(1).blend, Ed.layers.get(1).opacity, Ed.layers.get(1).clip]"), ['screen', 0.6, False], 'the group keeps the base blend mode and opacity')
        # two clipped layers merged stay clipped
        group()
        self.click_row(2); self.click_row(3, SHIFT)
        self.merge_check(lambda: self.key('Ctrl+E'), 'Merge Layers', ['Layer 0', 'Base', 'M2'])
        self.click_row(2); self.click_row(3, SHIFT); self.key('Ctrl+E')
        self.assertTrue(self.state()['layers'][2]['clip'])
        # merge a clipped layer down into its base
        group()
        b.eval("Ed.layers.setActive(2); true"); b.settle(1)
        self.merge_check(lambda: self.key('Ctrl+E'), 'Merge Down', ['Layer 0', 'Base', 'M2'])
        # an unclipped adjustment layer merged down applies to the layer below only (Photoshop)
        b.eval("""Ed.registerAdjustment('lp-test-invert', {label: 'Invert', apply(img) { const d = img.data; for (let i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2]; } }}); true""")
        self.load(back)
        mid = np.zeros((H, W, 4), np.uint8); mid[2:10, 3:15] = (30, 90, 210, 255)
        self.add_layer(mid, 'Mid')
        b.eval("Ed.layers.add({kind: 'adjustment', name: 'Invert', adjustment: {type: 'lp-test-invert', params: {}}}); true"); b.settle(2)
        before = b.editor_pixels()
        self.key('Ctrl+E')
        self.assertEqual((self.names(), self.labels()[-1]), (['Layer 0', 'Mid'], 'Merge Down'))
        inv = mid.copy(); inv[..., :3] = 255 - inv[..., :3]
        self.assertPixelsClose(b.editor_pixels(1), np.where(mid[..., 3:] > 0, inv, 0))
        got = b.editor_pixels()
        self.assertPixelsClose(got[2:10, 3:15], before[2:10, 3:15], msg='where the layer covers, the picture stays')
        # a layer with effects (an outer and an inner effect registered here) merges down keeping the picture
        b.eval("""Ed.registerEffect('lp-test-shadow', {stage: 'behind', order: 10, pad: p => 2, render(ctx, i) { const t = i.temp(), g = t.getContext('2d'); g.drawImage(i.source, 2, 2);
                    g.globalCompositeOperation = 'source-in'; g.fillStyle = '#000000'; g.fillRect(0, 0, t.width, t.height); ctx.globalAlpha = i.opacity * 0.7; ctx.drawImage(t, 0, 0); i.free(t); }});
                  Ed.registerEffect('lp-test-tint', {stage: 'inner', order: 50, render(ctx, i) { const t = i.temp(), g = t.getContext('2d'); g.drawImage(i.source, 0, 0);
                    g.globalCompositeOperation = 'source-in'; g.fillStyle = '#ff00ff'; g.fillRect(0, 0, t.width, t.height); ctx.globalAlpha = i.opacity * 0.5; ctx.drawImage(t, 0, 0); i.free(t); }}); true""")
        self.load(back)
        self.add_layer(random_layer(rng, H, W, alphas=(0, 255, 128)), 'Mid', opacity=0.8, fill=0.6, effects={'lp-test-shadow': {}, 'lp-test-tint': {}})
        b.settle(2)
        self.assertTrue(b.eval(f"document.querySelector('{PANEL} .ed-layer[data-index=\"1\"] .ed-badges').textContent.includes('fx')"), 'an fx badge')
        self.merge_check(lambda: self.key('Ctrl+E'), 'Merge Down', ['Layer 0'])

    # ----- 7. Layer via Copy / Cut, Delete Hidden, rename by menu ------------------------------------------------------------
    def test_12_layer_via_copy_and_cut_delete_hidden_and_rename(self):
        b = self.browser
        rng = np.random.default_rng(9)
        H, W = 20, 20
        img = random_layer(rng, H, W, alphas=(255,))
        self.load(img)
        P0 = b.editor_pixels(0)
        b.eval("Ed.select.rect(3, 4, 8, 6); true")
        sel = np.zeros((H, W), bool); sel[4:10, 3:11] = True
        self.key('Ctrl+J')
        self.assertEqual((self.names(), self.labels()[-1]), (['Layer 0', 'Layer 1'], 'Layer via Copy'))
        self.assertPixelsClose(b.editor_pixels(1), np.where(sel[..., None], P0, 0))
        self.assertPixelsClose(b.editor_pixels(0), P0)
        b.eval("Ed.layers.setActive(0); Ed.select.rect(3, 4, 8, 6); true"); b.settle(1)
        self.key('Shift+Ctrl+J')
        self.assertEqual(self.labels()[-1], 'Layer via Cut')
        self.assertPixelsClose(b.editor_pixels(0), np.where(sel[..., None], 0, P0))
        self.assertPixelsClose(b.editor_pixels(1), np.where(sel[..., None], P0, 0))
        self.assertEqual(self.state()['activeIndex'], 1)
        b.eval("Ed.select.none(); true")
        self.key('Ctrl+J')
        self.assertEqual(self.labels()[-1], 'Layer via Copy')
        self.assertEqual(len(self.names()), 4, 'without a selection Ctrl+J duplicates the layer')
        b.eval("Ed.layers.set(1, {visible: false}, {history: false}); Ed.layers.set(3, {visible: false}, {history: false}); true")
        b.eval("Ed.menu.run('Layer/Delete Hidden Layers')"); b.settle(2)
        self.assertEqual((len(self.names()), self.labels()[-1]), (2, 'Delete Layers'))
        b.eval("Ed.menu.run('Layer/Rename Layer...', {name: 'Sprite'})"); b.settle(1)
        self.assertEqual(self.names()[self.state()['activeIndex']], 'Sprite')
        b.eval("Ed.menu.run('Layer/Rename Layer...')")
        b.wait_for(f"document.querySelector('{PANEL} .ed-rename') === document.activeElement")
        b.key('Ctrl+A'); b.type_text('Final'); b.key('Enter'); b.settle(1)
        self.assertEqual(self.names()[self.state()['activeIndex']], 'Final')

    # ----- 8. other modules are optional ------------------------------------------------------------------------------------
    def test_13_double_click_opens_blending_options_or_properties_when_present(self):
        b = self.browser
        self.load(np.full((16, 16, 4), 90, np.uint8))
        b.eval("Ed.layers.add({name: 'Paint', history: false}); true"); b.settle(2)
        styles = b.eval("!!Ed.menu.get('Layer/Layer Style/Blending Options...')")
        self.assertEqual(b.eval(f"!document.querySelector('{PANEL} [data-action=\"fx\"]').hidden"),
                         b.eval("Ed.menu.list().some(m => m.path.startsWith('Layer/Layer Style/'))"), 'the fx button follows styles.js')
        if not styles:
            b.eval("window.__blending = 0; Ed.registerMenu('Layer/Layer Style/Blending Options...', {order: 1, enabled: () => true, action: () => { window.__blending++; }}); true")
        else:   # styles.js is loaded: count the calls instead of opening its dialog
            b.eval("window.__blending = 0; Ed.menu.get('Layer/Layer Style/Blending Options...').def.action = () => { window.__blending++; }; true")
        b.settle(2)
        r = b.eval(f"(() => {{ const r = document.querySelector('{PANEL} .ed-layer[data-index=\"1\"]').getBoundingClientRect(); return [r.right - 6, r.top + r.height / 2]; }})()")
        b.double_click(*r); b.settle(2)
        self.assertEqual(b.eval('window.__blending'), 1, 'double-click the row: Blending Options')
        self.assertFalse(b.eval(f"!!document.querySelector('{PANEL} .ed-rename')"))
        self.assertFalse(b.eval(f"document.querySelector('{PANEL} [data-action=\"fx\"]').hidden"))
        b.click(*r, button='right'); b.settle(1)
        self.popup_item('blending')
        self.assertEqual(b.eval('window.__blending'), 2)
        # an adjustment layer opens the Properties panel (adjust.js)
        if b.eval("!!Ed.panels.get('properties') && Ed.adjustments.list().length > 0"):
            b.eval("const a = Ed.adjustments.list()[0]; Ed.layers.add({kind: 'adjustment', name: 'Adj', adjustment: {type: a.type, params: {...(a.defaults || {})}}}); Ed.panels.show('layers'); true"); b.settle(3)
            r = b.eval(f"(() => {{ const r = document.querySelector('{PANEL} .ed-layer[data-index=\"2\"]').getBoundingClientRect(); return [r.right - 6, r.top + r.height / 2]; }})()")
            b.double_click(*r); b.settle(3)
            self.assertEqual(b.eval("document.querySelector('#edDock .ed-tab[aria-selected=true]').dataset.panel"), 'properties')
            b.eval("Ed.panels.show('layers'); true"); b.settle(2)
            self.assertTrue(b.eval(f"!document.querySelector('{PANEL} [data-action=\"adjust\"]').hidden"), 'the adjustment button shows with adjust.js')
            b.click_selector(f'{PANEL} [data-action="adjust"]'); b.settle(1)
            self.assertGreater(b.eval("document.querySelectorAll('.ed-lp-popup .ed-menu-item').length"), 3)
            b.key('Escape')

    def test_14_layer_menu_has_no_dead_items(self):
        b = self.browser
        self.load(np.full((16, 16, 4), 90, np.uint8))
        paths = [m['path'] for m in b.eval('Ed.menu.list()')]
        for gone in ('Layer/Link Layers', 'Layer/Select Linked Layers', 'Select/Find Layers', 'Select/Isolate Layers', 'Layer/Layer Properties...', 'Layer/Layer Mask/Unlink'):
            self.assertNotIn(gone, paths)
        for p in ('Layer/New/Layer...', 'Layer/New/Layer via Copy', 'Layer/New/Layer via Cut', 'Layer/Delete Hidden Layers', 'Layer/Layer Mask/From Transparency',
                  'Layer/Arrange/Bring to Front', 'Layer/Merge Down', 'Layer/Merge Visible', 'Layer/Stamp Visible', 'Layer/Flatten Image', 'Select/All Layers'):
            self.assertIn(p, paths)
        # no shortcut is taken twice, and none clashes with the lab's own (Ctrl+, Settings)
        keys = [m['shortcut'] for m in b.eval('Ed.menu.list()') if m['shortcut']]
        self.assertEqual(sorted(k for k in set(keys) if keys.count(k) > 1), [])
        self.assertNotIn('Ctrl+,', keys)
        # Rasterize stays fill.js's (layers.js must not replace its items)
        if 'Layer/Rasterize/Layer' in paths:
            self.assertNotIn('rasterizer', b.eval("String(Ed.menu.get('Layer/Rasterize/Layer').def.enabled)"))
        b.click_selector('#edMenubar [data-menu="Layer"]'); b.settle(2)
        b.screenshot('layers-menu-layer.png')
        b.key('Escape')
