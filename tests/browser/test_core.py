"""The layered editor core (src/web/editor/core.js + shell.js) end to end, in headless Edge with real mouse and keyboard input:
layers and the Layers panel, the compositor against the W3C compositing formulas, history, selection, locks, masks, saving and
reopening layers, import, the tools and shortcuts, menus, dialogs, the extension points, the old features, a stroke frame-time
measurement on a 2048 x 2048 sheet, and screenshots at 1366 x 768, 1600 x 1000 and 1920 x 1080 (tests/browser/output/).

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p test_core.py -v
"""
import json
import statistics
import time
import numpy as np
from harness import EditorTestCase, GPU, OUTPUT, distance_to_path, image_array, premultiplied, rgba, write_png
from fakegame import PHOTO, SPRITE

NATIVE = ['normal', 'darken', 'multiply', 'color-burn', 'lighten', 'screen', 'color-dodge', 'linear-dodge', 'overlay', 'soft-light', 'hard-light',
          'difference', 'exclusion', 'hue', 'saturation', 'color', 'luminosity']


# ----- the W3C compositing formulas (Compositing and Blending Level 1), as numpy -------------------------------------------
def _lum(c): return 0.3 * c[..., 0] + 0.59 * c[..., 1] + 0.11 * c[..., 2]


def _clip_color(c):
    l, n, x = _lum(c)[..., None], c.min(-1, keepdims=True), c.max(-1, keepdims=True)
    with np.errstate(divide='ignore', invalid='ignore'):
        c = np.where(n < 0, l + (c - l) * l / np.where(l - n == 0, 1, l - n), c)
        c = np.where(x > 1, l + (c - l) * (1 - l) / np.where(x - l == 0, 1, x - l), c)
    return c


def _set_lum(c, l): return _clip_color(c + (l - _lum(c))[..., None])


def _set_sat(c, s):
    mn, mx = c.min(-1, keepdims=True), c.max(-1, keepdims=True)
    rng = mx - mn
    return np.where(rng > 0, (c - mn) * s[..., None] / np.where(rng > 0, rng, 1), 0.0)


def _sat(c): return c.max(-1) - c.min(-1)


def blend(mode, b, s):
    """B(Cb, Cs) for unpremultiplied colors in 0..1."""
    screen = lambda b, s: b + s - b * s
    hard = lambda b, s: np.where(s <= .5, b * 2 * s, screen(b, 2 * s - 1))
    with np.errstate(divide='ignore', invalid='ignore'):
        return {'normal': lambda: s, 'multiply': lambda: b * s, 'screen': lambda: screen(b, s), 'overlay': lambda: hard(s, b),
                'darken': lambda: np.minimum(b, s), 'lighten': lambda: np.maximum(b, s),
                'color-dodge': lambda: np.where(b <= 0, 0, np.where(s >= 1, 1, np.minimum(1, b / np.where(s >= 1, 1, 1 - s)))),
                'color-burn': lambda: np.where(b >= 1, 1, np.where(s <= 0, 0, 1 - np.minimum(1, (1 - b) / np.where(s <= 0, 1, s)))),
                'hard-light': lambda: hard(b, s),
                'soft-light': lambda: np.where(s <= .5, b - (1 - 2 * s) * b * (1 - b), b + (2 * s - 1) * (np.where(b <= .25, ((16 * b - 12) * b + 4) * b, np.sqrt(b)) - b)),
                'difference': lambda: np.abs(b - s), 'exclusion': lambda: b + s - 2 * b * s, 'linear-dodge': lambda: np.minimum(1, b + s),
                'hue': lambda: _set_lum(_set_sat(s, _sat(b)), _lum(b)), 'saturation': lambda: _set_lum(_set_sat(b, _sat(s)), _lum(b)),
                'color': lambda: _set_lum(s, _lum(b)), 'luminosity': lambda: _set_lum(b, _lum(s))}[mode]()


def composite(backdrop, source, mode='normal', alpha=1.0):
    """source (with its alpha times `alpha`) composited onto backdrop; uint8 RGBA in, uint8 RGBA out."""
    b, s = backdrop.astype(np.float64) / 255, source.astype(np.float64) / 255
    cb, ab, cs, a_s = b[..., :3], b[..., 3:], s[..., :3], s[..., 3:] * alpha
    ao = a_s + ab * (1 - a_s)   # linear-dodge too: Photoshop's Linear Dodge (Add) is B = min(1, Cb + Cs) in the general formula
    co = a_s * (1 - ab) * cs + a_s * ab * np.clip(blend(mode, cb, cs), 0, 1) + (1 - a_s) * ab * cb
    with np.errstate(divide='ignore', invalid='ignore'):
        rgb = np.where(ao > 0, co / np.where(ao > 0, ao, 1), 0)
    return np.round(np.concatenate([rgb, ao], -1) * 255).astype(np.uint8)


ADD_LAYER = """const im={expr};const c=document.createElement('canvas');c.width=im.width;c.height=im.height;c.getContext('2d').putImageData(im,0,0);
  const L=Ed.layers.add({{name:{name},canvas:c,history:{history}}});Ed.layers.set(L,{props},{{history:false}});L.id"""


class EditorCore(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers -------------------------------------------------------------------------------------------------------
    def state(self): return self.browser.eval('Ed.test.state')

    def labels(self): return self.browser.eval('Ed.history.list().map(s => s.label)')

    def add_layer(self, array, name='Test layer', history='false', **props):
        b = self.browser
        return b.eval(ADD_LAYER.format(expr=b.push_pixels(array), name=json.dumps(name), history=history, props=json.dumps(props)))

    def tool(self, name, **options):
        b = self.browser
        b.eval(f'Ed.setTool({json.dumps(name)})' + (f';Ed.setToolOptions({json.dumps(name)}, {json.dumps(options)})' if options else ''))
        b.settle()

    def fresh(self, name='edit-photo.png'):
        b = self.browser
        b.select_asset(name)
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true")
        return b

    def overlay_pixels(self):
        """Drawn pixels on the view overlay (ants, grid, outlines); a hidden overlay shows nothing."""
        b = self.browser
        if b.eval("document.getElementById('viewOverlay').hidden"): return 0
        return int(b.pixels("document.getElementById('viewOverlay')")[..., 3].astype(bool).sum())

    # ----- loading -------------------------------------------------------------------------------------------------------
    def test_01_editor_loads_without_errors(self):
        b = self.browser
        self.assertTrue(b.eval('await Ed.ready; !!Ed.doc === false && typeof Ed.registerTool === "function"'))
        self.assertEqual(b.eval("[...document.querySelectorAll('#edMenubar .ed-menu-top')].map(e => e.textContent)"),
                         ['File', 'Edit', 'Image', 'Layer', 'Select', 'View'])   # Filter appears once a module adds a filter
        tools = b.eval("[...document.querySelectorAll('#edTools .ed-tool')].map(e => [e.dataset.tool, e.title])")
        self.assertEqual([t[0] for t in tools], ['eyedropper', 'brush', 'eraser', 'hand', 'zoom'], 'Photoshop order')
        self.assertIn(['brush', 'Brush (B)'], tools)
        self.assertEqual(b.eval('Ed.tool'), 'hand')
        self.assertEqual(b.eval("[...document.querySelectorAll('#edDock .ed-tab')].map(e => e.textContent)"), ['Layers', 'History'])
        paths = [m['path'] for m in b.eval('Ed.menu.list()')]
        for p in ('File/Import Image as Layer...', 'File/Export PNG', 'File/Revert to Original', 'Edit/Undo', 'Edit/Redo', 'Edit/Clear', 'Layer/New Layer',
                  'Layer/Duplicate Layer', 'Layer/Delete Layer', 'Select/All', 'Select/Deselect', 'Select/Reselect', 'Select/Inverse', 'View/Zoom In',
                  'View/Zoom Out', 'View/Fit on Screen', 'View/100%', 'View/Pixel Grid', 'View/Show Original', 'View/Focus Editor'):
            self.assertIn(p, paths)
        loaded = b.eval("performance.getEntriesByType('resource').map(r => r.name.split('/').slice(-2).join('/')).filter(n => n.startsWith('editor/'))")
        for m in ('core', 'shell', 'select', 'paint', 'fill', 'transform', 'adjust', 'filters', 'styles', 'layers', 'color'):
            self.assertIn(f'editor/{m}.js', loaded)
        for m in ('editor', 'select', 'paint', 'fill', 'transform', 'adjust', 'filters', 'styles', 'layers', 'color'):
            self.assertIn(f'editor/{m}.css', loaded)
        self.assertNoBrowserErrors(everything=True)

    def test_02_an_opened_asset_is_one_layer_named_after_it(self):
        b = self.browser
        b.eval("window.__assets = []; document.addEventListener('lab:asset', e => __assets.push(e.detail.name)); window.__docs = 0; document.addEventListener('lab:document', () => __docs++)")
        self.fresh('edit-sprite.png')
        st = self.state()
        self.assertEqual([(l['name'], l['kind'], l['blend'], l['opacity']) for l in st['layers']], [('edit-sprite', 'pixel', 'normal', 1)])
        self.assertEqual((b.eval('Ed.doc.width'), b.eval('Ed.doc.height'), st['activeIndex'], st['target']), (64, 64, 0, 'pixels'))
        self.assertEqual(self.labels(), ['Open'])
        self.assertEqual(b.eval('__assets'), ['edit-sprite.png'])
        self.assertGreaterEqual(b.eval('__docs'), 1)
        sprite = image_array('edit-sprite.png')
        for target in ('composite', 0):
            px = b.editor_pixels(target)
            self.assertPixelsClose(px[..., 3], sprite[..., 3], msg=f'{target} alpha')
            self.assertPixelsClose(premultiplied(px), premultiplied(sprite), msg=f'{target} colors')
        self.assertPixelsClose(b.pixels('S.draft'), b.editor_pixels(), msg='S.draft holds the composite')
        self.assertEqual(b.text('#edPanel-layers .ed-layer .ed-layer-name'), 'edit-sprite')
        self.assertFalse(b.eval('S.dirty'))

    # ----- layers ----------------------------------------------------------------------------------------------------------
    def test_03_layers_api_add_duplicate_delete_reorder_visibility_opacity(self):
        b = self.browser
        base = np.zeros((32, 32, 4), np.uint8); base[...] = (40, 80, 120, 255)
        b.load_editor_image(base)
        red = np.zeros((32, 32, 4), np.uint8); red[8:24, 8:24] = (220, 30, 30, 255)
        b.eval(f"const im={b.push_pixels(red)};const c=document.createElement('canvas');c.width=32;c.height=32;c.getContext('2d').putImageData(im,0,0);Ed.layers.add({{name:'Red',canvas:c}})")
        self.assertEqual([l['name'] for l in self.state()['layers']], ['Layer 0', 'Red'])
        self.assertEqual(self.state()['activeIndex'], 1)
        self.assertPixelsClose(b.editor_pixels(), composite(base, red))
        b.eval('Ed.layers.duplicate()')
        self.assertEqual([l['name'] for l in self.state()['layers']], ['Layer 0', 'Red', 'Red copy'])
        self.assertPixelsClose(b.editor_pixels(2), red)
        b.eval("Ed.layers.set(2, {visible: false}); Ed.layers.set(1, {opacity: 0.5})")
        self.assertPixelsClose(b.editor_pixels(), composite(base, red, alpha=0.5), max_abs=1)
        b.eval('Ed.layers.move(0, 2)')   # the base goes on top and covers everything
        self.assertEqual([l['name'] for l in self.state()['layers']], ['Red', 'Red copy', 'Layer 0'])
        self.assertPixelsClose(b.editor_pixels(), base)
        b.eval("Ed.layers.remove(Ed.layers.get(2))")
        self.assertEqual([l['name'] for l in self.state()['layers']], ['Red', 'Red copy'])
        self.assertEqual(self.labels(), ['Open', 'New Layer', 'Duplicate Layer', 'Hide Layer', 'Opacity Change', 'Layer Order', 'Delete Layer'])
        self.assertTrue(b.eval('S.dirty'))
        self.assertEqual(b.eval("S.ops.slice(-2).map(o => o.action)"), ['move', 'delete'])
        self.assertFalse(b.eval("Ed.layers.remove(0) && Ed.layers.remove(0)"), 'the last layer cannot be deleted')
        self.assertNoBrowserErrors()

    def test_04_layers_panel_buttons_eye_opacity_blend_rename_and_drag(self):
        b = self.fresh('edit-photo.png')
        photo = image_array('edit-photo.png')
        b.click_selector('#edPanel-layers [data-action="new"]')
        self.assertEqual([l['name'] for l in self.state()['layers']], ['edit-photo', 'Layer 1'])
        b.click_selector('#edPanel-layers [data-action="duplicate"]')
        self.assertEqual(len(self.state()['layers']), 3)
        b.eval("Ed.layers.setActive(0)"); b.settle()
        self.assertTrue(b.eval("document.querySelector('#edPanel-layers .ed-layer[data-index=\"0\"]').classList.contains('active')"))
        # Opacity typed into the panel: one history state.
        before = len(self.labels())
        b.set_value('#edPanel-layers .ed-field[data-key="opacity"] input[type=number]', 40)
        self.assertAlmostEqual(self.state()['layers'][0]['opacity'], 0.4)
        self.assertEqual(self.labels()[-1], 'Opacity Change')
        self.assertEqual(len(self.labels()), before + 1)
        # Scrubbing the Opacity label is live and makes one state too.
        lx, ly = b.center_of('#edPanel-layers .ed-field[data-key="opacity"] .ed-field-label')
        b.drag([(lx, ly), (lx + 20, ly), (lx + 40, ly)])
        self.assertAlmostEqual(self.state()['layers'][0]['opacity'], 0.6, places=2)
        self.assertEqual(len(self.labels()), before + 2)
        b.set_value('#edPanel-layers select.ed-blend', 'multiply')
        self.assertEqual(self.state()['layers'][0]['blend'], 'multiply')
        blend_list = b.eval("[...document.querySelectorAll('#edPanel-layers select.ed-blend option')].map(o => o.value)")
        self.assertEqual(blend_list, NATIVE, 'the blend list follows Photoshop order')
        self.assertEqual(b.eval("document.querySelectorAll('#edPanel-layers select.ed-blend hr').length"), 5, 'six groups')
        # The eye hides the layer (the photo is the bottom layer; the others are empty).
        b.click_selector('#edPanel-layers .ed-layer[data-index="0"] .ed-eye')
        self.assertFalse(self.state()['layers'][0]['visible'])
        self.assertEqual(int(b.editor_pixels()[..., 3].max()), 0)
        b.click_selector('#edPanel-layers .ed-layer[data-index="0"] .ed-eye')
        b.eval("Ed.layers.set(0, {blend: 'normal', opacity: 1})")
        self.assertPixelsClose(b.editor_pixels(), photo)
        # Rename with a double-click.
        b.double_click(*b.center_of('#edPanel-layers .ed-layer[data-index="2"] .ed-layer-name'))
        b.wait_for("document.querySelector('#edPanel-layers .ed-rename') === document.activeElement")
        b.key('Ctrl+A'); b.type_text('Highlights'); b.key('Enter')
        self.assertEqual(self.state()['layers'][2]['name'], 'Highlights')
        self.assertEqual(self.labels()[-1], 'Rename Layer')
        # Drag the top row below the bottom row: it becomes the bottom layer.
        top = b.center_of('#edPanel-layers .ed-layer[data-index="2"] .ed-layer-name')
        bottom = b.eval("(() => { const r = document.querySelector('#edPanel-layers .ed-layer[data-index=\"0\"]').getBoundingClientRect(); return [r.left + r.width / 2, r.bottom - 3]; })()")
        b.drag([top, (top[0], top[1] + 20), (bottom[0], bottom[1] + 2)], steps_between=6)
        self.assertEqual([l['name'] for l in self.state()['layers']], ['Highlights', 'edit-photo', 'Layer 1'])
        self.assertEqual(self.labels()[-1], 'Layer Order')
        # Clicking a row selects it; the delete button removes it.
        b.click_selector('#edPanel-layers .ed-layer[data-index="2"] .ed-layer-name')
        self.assertEqual(self.state()['activeIndex'], 2)
        b.click_selector('#edPanel-layers [data-action="delete"]')
        self.assertEqual([l['name'] for l in self.state()['layers']], ['Highlights', 'edit-photo'])
        b.wait_for("document.querySelectorAll('#edPanel-layers .ed-layer').length === 2")

    # ----- compositor ------------------------------------------------------------------------------------------------------
    def test_05_native_blend_modes_opacity_and_fill_match_the_w3c_formulas(self):
        b = self.browser
        rng = np.random.default_rng(11)
        base = rng.integers(0, 256, (24, 24, 4), dtype=np.uint8); base[..., 3] = 255
        top = rng.integers(0, 256, (24, 24, 4), dtype=np.uint8)
        top[..., 3] = np.array([255, 255, 230, 180, 128, 64, 0, 255])[np.arange(24 * 24).reshape(24, 24) % 8]
        b.load_editor_image(base)
        self.add_layer(top, 'Blend')
        stored = b.editor_pixels(1)   # what the layer canvas holds (premultiplied storage rounds low alpha)
        self.assertPixelsClose(stored[..., 3], top[..., 3])
        worst = {}
        for mode in NATIVE:
            for opacity, fill in ((1, 1), (0.6, 1), (1, 0.5), (0.7, 0.4)):
                with self.subTest(mode=mode, opacity=opacity, fill=fill):
                    b.eval(f"Ed.layers.set(1, {{blend: '{mode}', opacity: {opacity}, fill: {fill}}}, {{history: false}})")
                    got, want = b.editor_pixels(), composite(base, stored, mode, opacity * fill)
                    d = int(np.abs(got.astype(int) - want.astype(int)).max())
                    worst[mode] = max(worst.get(mode, 0), d)
                    # Burn and dodge divide, and the non-separable modes clip colors: 8-bit storage differs a little more there.
                    self.assertPixelsClose(got, want, max_abs=3 if mode in ('color-burn', 'color-dodge', 'hue', 'saturation', 'color', 'luminosity') else 2, msg=f'{mode} at opacity {opacity}, fill {fill}')
        print('\n[blend modes] worst channel difference vs W3C formulas:', worst)
        # A transparent backdrop: the general formula with alpha on both sides.
        glass = base.copy(); glass[..., 3] = np.array([255, 128, 40, 0])[np.arange(24 * 24).reshape(24, 24) % 4]
        b.load_editor_image(glass)
        self.add_layer(top, 'Blend')
        under, over = b.editor_pixels(0), b.editor_pixels(1)
        for mode in ('normal', 'multiply', 'screen', 'difference', 'luminosity'):
            with self.subTest(mode=mode, backdrop='transparent'):
                b.eval(f"Ed.layers.set(1, {{blend: '{mode}', opacity: 0.8}}, {{history: false}})")
                got, want = b.editor_pixels(), composite(under, over, mode, 0.8)
                self.assertPixelsClose(premultiplied(got), premultiplied(want), max_abs=3, msg=f'{mode} over a transparent backdrop (premultiplied)')

    def test_06_extension_points_custom_blend_adjustment_effects_and_clipping(self):
        b = self.browser
        base = np.zeros((16, 16, 4), np.uint8); base[...] = (200, 100, 50, 255)
        top = np.zeros((16, 16, 4), np.uint8); top[4:12, 4:12] = (20, 60, 220, 255)
        b.load_editor_image(base)
        self.add_layer(top, 'Top')
        # A per-pixel mode registered by a module (the layers agent adds Linear Burn and friends this way).
        b.eval("Ed.registerBlend('test-average', {label: 'Average', group: 'contrast', fn: (cb, cs, o) => { for (let i = 0; i < 3; i++) o[i] = (cb[i] + cs[i]) / 2; }})")
        self.assertIn('test-average', b.eval("Ed.blends.list().map(b => b.id)"))
        b.eval("Ed.layers.set(1, {blend: 'test-average'}, {history: false})")
        want = base.copy(); want[4:12, 4:12, :3] = np.round((base[4:12, 4:12, :3].astype(int) + top[4:12, 4:12, :3]) / 2)
        self.assertPixelsClose(b.editor_pixels(), want, max_abs=1)
        b.eval("Ed.layers.set(1, {blend: 'normal'}, {history: false})")
        # Effects: 'behind' draws under the layer, 'over' above it, and Fill hides only the layer's own pixels.
        b.eval("""Ed.registerEffect('test-shadow', {stage: 'behind', order: 10, pad: p => p.d, render(ctx, i) {
                    const t = i.temp(), g = t.getContext('2d'); g.clearRect(0, 0, t.width, t.height); g.drawImage(i.source, i.params.d, i.params.d);
                    g.globalCompositeOperation = 'source-in'; g.fillStyle = '#000000'; g.fillRect(0, 0, t.width, t.height);
                    ctx.globalAlpha = i.opacity; ctx.drawImage(t, 0, 0); i.free(t); }});
                  Ed.registerEffect('test-frame', {stage: 'over', order: 100, render(ctx, i) { ctx.globalAlpha = i.opacity; ctx.fillStyle = '#ffffff'; ctx.fillRect(4, 4, 8, 1); }});
                  Ed.layers.set(1, {effects: {'test-shadow': {d: 2}, 'test-frame': {}}}, {history: false})""")
        got = b.editor_pixels()
        self.assertEqual(tuple(got[13, 13]), (0, 0, 0, 255), 'the shadow shows below and right of the square')
        self.assertEqual(tuple(got[8, 8]), (20, 60, 220, 255), 'the layer is drawn over its shadow')
        self.assertEqual(tuple(got[4, 8]), (255, 255, 255, 255), 'the frame is drawn over the layer')
        b.eval("Ed.layers.set(1, {fill: 0}, {history: false})")
        got = b.editor_pixels()
        self.assertEqual(tuple(got[8, 8]), (0, 0, 0, 255), 'fill 0 hides the pixels but not the shadow under them')
        self.assertEqual(tuple(got[4, 8]), (255, 255, 255, 255), 'nor the frame')
        b.eval("Ed.layers.set(1, {fill: 1, effects: {}}, {history: false})")
        # An adjustment layer applies to everything below, limited by its mask and opacity.
        b.eval("""Ed.registerAdjustment('test-invert', {label: 'Invert', apply(img) { const d = img.data; for (let i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2]; } }});
                  const A = Ed.layers.add({kind: 'adjustment', name: 'Invert', adjustment: {type: 'test-invert', params: {}}});
                  const g = A.mask.getContext('2d'); g.clearRect(0, 0, 8, 16); Ed.update()""")
        self.assertEqual(self.state()['target'], 'mask', 'an adjustment layer is painted through its mask')
        got = b.editor_pixels()
        self.assertEqual(tuple(got[1, 1]), (200, 100, 50, 255), 'masked out: unchanged')
        self.assertEqual(tuple(got[1, 14]), (55, 155, 205, 255), 'inverted')
        self.assertEqual(tuple(got[6, 10]), (235, 195, 35, 255), 'the layer below is inverted too')
        b.eval("Ed.layers.set(2, {opacity: 0.5}, {history: false})")
        self.assertPixelsClose(b.editor_pixels()[1:2, 14:15], np.array([[[128, 128, 128, 255]]], np.uint8), max_abs=1)
        b.eval("Ed.layers.remove(2, {history: false})")
        # A clipping mask: the clipped layer shows only where the layer below has pixels.
        full = np.zeros((16, 16, 4), np.uint8); full[...] = (250, 250, 0, 255)
        self.add_layer(full, 'Clipped', clip=True)
        got = b.editor_pixels()
        self.assertEqual(tuple(got[8, 8]), (250, 250, 0, 255))
        self.assertEqual(tuple(got[1, 1]), (200, 100, 50, 255), 'outside the base layer: not drawn')
        b.eval("Ed.layers.set(1, {opacity: 0.5}, {history: false})")
        self.assertPixelsClose(b.editor_pixels()[8:9, 8:9], composite(base[8:9, 8:9], full[8:9, 8:9], alpha=0.5), max_abs=1, msg='the base opacity applies to the group')
        b.eval("Ed.layers.set(1, {visible: false}, {history: false})")
        self.assertPixelsClose(b.editor_pixels(), base, msg='hiding the base hides the clipped layer too')
        # The composite helpers later modules build merges on.
        self.assertPixelsClose(b.pixels("Ed.render.composite({layers: [0]})"), base, msg='a subset: just the bottom layer')
        b.eval("Ed.layers.set(1, {visible: true, opacity: 1}, {history: false}); Ed.layers.set(2, {visible: false}, {history: false}); Ed.layers.set(1, {effects: {'test-shadow': {d: 2}}}, {history: false})")
        alone = b.pixels("Ed.render.layer(1)")
        self.assertEqual((tuple(alone[13, 13]), tuple(alone[8, 8]), int(alone[1, 1, 3])), ((0, 0, 0, 255), (20, 60, 220, 255), 0), 'one layer with its effects, on transparency')
        self.assertEqual(int(b.pixels("Ed.render.layer(1, {effects: false})")[13, 13, 3]), 0)
        self.assertPixelsClose(b.pixels("Ed.render.below(1)"), base)
        b.eval("Ed.layers.set(1, {effects: {}}, {history: false}); Ed.layers.set(2, {visible: true}, {history: false})")
        # Unknown modes, adjustments and effects fall back without breaking the composite.
        b.eval("Ed.layers.set(1, {visible: true, opacity: 1, blend: 'not-loaded', effects: {'not-loaded': {}}}, {history: false})")
        self.assertEqual(tuple(b.editor_pixels()[8, 8]), (250, 250, 0, 255))
        self.assertEqual(b.eval("[...Ed.doc.missing].sort()"), ['blend not-loaded', 'effect not-loaded'])

    # ----- history ---------------------------------------------------------------------------------------------------------
    def snap(self):
        """Every layer's pixels (restored exactly by history), the composite and the structure."""
        b = self.browser
        return [b.editor_pixels(i) for i in range(b.eval('Ed.doc.layers.length'))], b.editor_pixels(), self.state()

    def assertSnap(self, snap, msg):
        layers, comp, st = self.snap()
        self.assertEqual([(l['id'], l['name'], l['opacity'], l['blend'], l['visible']) for l in st['layers']],
                         [(l['id'], l['name'], l['opacity'], l['blend'], l['visible']) for l in snap[2]['layers']], msg)
        for i, (got, want) in enumerate(zip(layers, snap[0])): self.assertPixelsClose(got, want, msg=f'{msg}: layer {i} exactly')
        # The canvas may composite a half-transparent pixel 1 level differently once Chrome renders it in software.
        self.assertPixelsClose(comp, snap[1], max_abs=1, msg=f'{msg}: composite')

    def test_07_history_undo_redo_and_jump_restore_exact_pixels_and_structure(self):
        b = self.fresh('edit-photo.png')
        self.tool('brush', size=10, hardness=100, opacity=100)
        b.eval("Ed.setColor('fg', '#ff00ff')")
        snaps = [self.snap()]
        b.drag_image([(30.5, 120.5), (220.5, 120.5)], center=False)
        snaps.append(self.snap())
        b.key('Shift+Ctrl+N')
        b.drag_image([(120.5, 20.5), (120.5, 230.5)], center=False)
        snaps.append(self.snap())
        b.eval("Ed.layers.set(1, {opacity: 0.3, blend: 'screen'})")
        b.key('Ctrl+A')
        snaps.append(self.snap())
        b.eval("Ed.layers.setActive(0)"); b.key('Delete')
        snaps.append(self.snap())
        self.assertEqual(self.labels(), ['Open', 'Brush Tool', 'New Layer', 'Brush Tool', 'Layer Properties', 'Select All', 'Clear'])
        self.assertEqual(int(b.editor_pixels(0)[..., 3].max()), 0, 'Clear with Select All emptied the layer')
        marks = [0, 1, 3, 5, 6]   # history index of each snapshot
        for k in range(len(marks) - 1, 0, -1):
            while b.eval('Ed.history.index') > marks[k - 1]:
                b.key('Ctrl+Z')
            self.assertSnap(snaps[k - 1], f'undo back to state {marks[k - 1]}')
        self.assertEqual(b.eval('S.ops.at(-1).tool'), 'undo')
        b.key('Ctrl+Shift+Z'); b.key('Ctrl+Y')
        self.assertEqual(b.eval('Ed.history.index'), 2)
        self.assertPixelsClose(b.editor_pixels(0), snaps[1][0][0], msg='Ctrl+Shift+Z and Ctrl+Y redo')
        b.eval('Ed.history.jump(6)')
        self.assertSnap(snaps[4], 'jump to the last state')
        self.assertTrue(b.eval('Ed.selection !== null'))
        b.click_selector('#edDock .ed-tab[data-panel="history"]')
        b.wait_for("!!document.querySelector('#edPanel-history .ed-history-item[data-index=\"0\"]')")
        b.click_selector('#edPanel-history .ed-history-item[data-index="0"]')
        b.settle()
        self.assertSnap(snaps[0], 'clicking the first History row shows the opened image')
        self.assertFalse(b.eval('Ed.selection !== null'))
        self.assertEqual(b.eval("document.querySelectorAll('#edPanel-history .ed-history-item.undone').length"), 6)
        b.eval('Ed.history.jump(2)')
        b.drag_image([(10.5, 10.5), (40.5, 10.5)], center=False)
        self.assertEqual(self.labels(), ['Open', 'Brush Tool', 'New Layer', 'Brush Tool'], 'a new action after going back drops the later states')
        b.eval("Ed.panels.show('layers')")
        # The state limit: the oldest states drop, the newest stay.
        b.eval("Ed.history.limits.states = 5; for (let i = 0; i < 8; i++) Ed.layers.set(0, {opacity: 1 - i / 20});")
        self.assertEqual(len(self.labels()), 6)
        b.eval("Ed.history.limits.states = 50")
        self.assertNoBrowserErrors()

    # ----- selection -------------------------------------------------------------------------------------------------------
    def test_08_selection_commands_ants_and_a_brush_clipped_to_a_rectangle(self):
        b = self.fresh('edit-photo.png')
        b.key('Ctrl+A')
        self.assertTrue(b.eval('Ed.select.hasSelection()'))
        self.assertEqual(int(b.editor_pixels('selection')[..., 3].min()), 255)
        self.assertEqual(b.eval('Ed.select.bounds()'), {'x': 0, 'y': 0, 'w': 256, 'h': 256})
        b.key('Ctrl+D')
        self.assertFalse(b.eval('Ed.select.hasSelection()'))
        self.assertEqual(int(b.editor_pixels('selection')[..., 3].max()), 0)
        b.key('Ctrl+Shift+D')
        self.assertTrue(b.eval('Ed.select.hasSelection()'), 'Reselect brings it back')
        b.eval("Ed.select.rect(64, 96, 80, 40)")
        b.key('Ctrl+Shift+I')
        sel = b.editor_pixels('selection')[..., 3]
        self.assertEqual((int(sel[100, 100]), int(sel[10, 10])), (0, 255), 'Inverse')
        b.key('Ctrl+Shift+I')
        # A rectangle set through Ed.select.set clips a real brush stroke.
        b.eval("const m = document.createElement('canvas'); m.width = m.height = 256; m.getContext('2d').fillRect(64, 96, 80, 40); Ed.select.set(m, 'replace')")
        self.assertEqual(b.eval('Ed.select.bounds()'), {'x': 64, 'y': 96, 'w': 80, 'h': 40})
        b.settle(3)
        self.assertGreater(self.overlay_pixels(), 200, 'marching ants are drawn')
        out = b.eval("Ed.select.outline()")
        self.assertTrue(out['h'] and out['v'])
        self.tool('brush', size=12, hardness=100, opacity=100)
        b.eval("Ed.setColor('fg', '#00ff00')")
        before = b.editor_pixels()
        path = [(20.5, 116.5), (230.5, 116.5)]
        b.drag_image(path, center=False)
        after = b.editor_pixels()
        y, x = np.mgrid[0:256, 0:256]
        inside = (x >= 64) & (x < 144) & (y >= 96) & (y < 136)
        d = distance_to_path(256, 256, path)
        self.assertPixelsClose(after, before, mask=~inside, msg='nothing outside the selection changes')
        self.assertPixelsClose(after, np.broadcast_to(rgba('#00ff00'), after.shape), mask=inside & (d <= 4), msg='the stroke inside the selection')
        # Delete clears the selected pixels of the active layer.
        b.key('Delete')
        cleared = b.editor_pixels()
        self.assertEqual(int(cleared[inside][..., 3].max()), 0)
        self.assertPixelsClose(cleared, after, mask=~inside)
        self.assertEqual(self.labels()[-2:], ['Brush Tool', 'Clear'])
        b.key('Ctrl+D')
        b.key('Delete')
        self.assertIn('Select an area first', b.text('#toast'))

    def test_09_lock_transparency_keeps_alpha_for_brush_and_eraser(self):
        b = self.fresh('edit-sprite.png')
        b.click_selector('#edPanel-layers .ed-lock[data-lock="alpha"]')
        self.assertTrue(self.state()['layers'][0]['locks']['alpha'])
        self.assertEqual(self.labels()[-1], 'Lock Change')
        self.tool('brush', size=8, hardness=100, opacity=100)
        b.eval("Ed.setColor('fg', '#ffff00'); Ed.setColor('bg', '#123456')")
        before = b.editor_pixels()
        path = [(2.5, 16.5), (40.5, 16.5)]   # from transparent space across the opaque square and back out
        b.drag_image(path, center=False)
        after = b.editor_pixels()
        self.assertPixelsClose(after[..., 3], before[..., 3], msg='alpha is locked')
        d = distance_to_path(64, 64, path)
        flat = np.zeros((64, 64), bool); flat[4:28, 4:28] = True
        self.assertPixelsClose(after, np.broadcast_to(rgba('#ffff00'), after.shape), mask=flat & (d <= 3))
        self.tool('eraser', size=8, hardness=100, opacity=100)
        b.drag_image([(8.5, 24.5), (24.5, 24.5)], center=False)
        erased = b.editor_pixels()
        self.assertPixelsClose(erased[..., 3], before[..., 3], msg='the eraser keeps locked alpha')
        self.assertEqual(tuple(erased[24, 16]), (0x12, 0x34, 0x56, 255), 'it paints the background color, as in Photoshop')
        b.click_selector('#edPanel-layers .ed-lock[data-lock="alpha"]')
        b.drag_image([(8.5, 24.5), (24.5, 24.5)], center=False)
        self.assertEqual(int(b.editor_pixels()[24, 16, 3]), 0, 'unlocked, the eraser makes pixels transparent')
        # Locked pixels refuse painting with a plain message.
        b.click_selector('#edPanel-layers .ed-lock[data-lock="pixels"]')
        n = len(self.labels())
        self.tool('brush')
        b.drag_image([(10.5, 10.5), (20.5, 10.5)], center=False)
        self.assertEqual(len(self.labels()), n)
        self.assertIn('locked', b.text('#toast'))

    def test_10_painting_on_a_layer_mask(self):
        b = self.fresh('edit-photo.png')
        photo = image_array('edit-photo.png')
        b.eval("Ed.menu.run('Layer/Layer Mask/Reveal All')")
        st = self.state()
        self.assertEqual((st['layers'][0]['mask'], st['target']), (True, 'mask'))
        self.assertEqual(int(b.editor_pixels('mask')[..., 3].min()), 255)
        b.wait_for("!!document.querySelector('#edPanel-layers .ed-mask-thumb.target')")
        self.tool('brush', size=16, hardness=100, opacity=100)
        b.key('d')   # on a mask: white foreground, black background
        self.assertEqual((b.eval('Ed.fg'), b.eval('Ed.bg')), ('#ffffff', '#000000'))
        b.key('x')
        path = [(40.5, 200.5), (200.5, 200.5)]
        b.drag_image(path, center=False)
        d = distance_to_path(256, 256, path)
        mask = b.editor_pixels('mask')[..., 3]
        self.assertEqual(int(mask[d <= 6].max()), 0, 'black hides')
        self.assertEqual(int(mask[d >= 10].min()), 255)
        comp = b.editor_pixels()
        self.assertEqual(int(comp[..., 3][d <= 6].max()), 0)
        self.assertPixelsClose(b.editor_pixels(0), photo, msg='the layer pixels are untouched')
        # Gray paints part way; the eraser paints the background color (black) on a mask too.
        b.eval("Ed.setColor('fg', '#808080')")
        b.drag_image([(40.5, 60.5), (200.5, 60.5)], center=False)
        self.assertAlmostEqual(int(b.editor_pixels('mask')[60, 120, 3]), 128, delta=2)
        b.eval("Ed.layers.set(0, {maskEnabled: false})")
        self.assertPixelsClose(b.editor_pixels(), photo, msg='a disabled mask shows everything')
        b.click_selector('#edPanel-layers .ed-layer[data-index="0"] .ed-thumb:not(.ed-mask-thumb)')
        self.assertEqual(self.state()['target'], 'pixels')
        b.eval("Ed.layers.set(0, {maskEnabled: true}); Ed.menu.run('Layer/Layer Mask/Apply')")
        self.assertFalse(self.state()['layers'][0]['mask'])
        self.assertEqual(int(b.editor_pixels(0)[200, 120, 3]), 0, 'Apply cuts the masked pixels out of the layer')

    # ----- saving --------------------------------------------------------------------------------------------------------
    def test_11_save_and_reopen_restores_the_layers(self):
        b = self.fresh('edit-photo.png')
        key = b.eval('S.asset.key')
        art = np.zeros((256, 256, 4), np.uint8); art[40:200, 60:180] = (30, 200, 240, 200)
        self.add_layer(art, 'Glow', history="'New Layer'", blend='screen', opacity=0.75)
        b.eval("Ed.layers.addMask(1, 'reveal'); const g = Ed.layers.get(1).mask.getContext('2d'); g.clearRect(60, 40, 40, 160); Ed.layers.get(1).version++; Ed.update()")
        b.eval("Ed.layers.set(0, {locks: {alpha: true}})")
        want = b.editor_pixels()
        b.key('Ctrl+S')
        b.wait_for("!S.dirty && !S.busy && S.asset.revision !== 'original'", timeout=30)
        revision = b.eval('S.asset.revision')
        stored = json.loads((self.server.workspace / 'revisions' / key / f'{revision}.layers.json').read_text('utf-8'))
        self.assertEqual([(l['name'], l['blend'], l['opacity'], 'mask_png' in l) for l in stored['layers']], [('edit-photo', 'normal', 1, False), ('Glow', 'screen', 0.75, True)])
        b.select_asset('edit-sprite.png')
        b.select_asset('edit-photo.png')
        st = self.state()
        self.assertEqual([(l['name'], l['blend'], l['opacity'], l['mask'], l['locks']['alpha']) for l in st['layers']],
                         [('edit-photo', 'normal', 1, False, True), ('Glow', 'screen', 0.75, True, False)])
        self.assertPixelsClose(b.editor_pixels(), want, max_abs=2, msg='the reopened layers give the saved picture')
        self.assertEqual(self.labels(), ['Open'])
        self.assertFalse(b.eval('S.dirty'))
        # A document that no longer adds up to the saved picture opens flat, with a plain message.
        path = self.server.workspace / 'revisions' / key / f'{revision}.layers.json'
        damaged = json.loads(path.read_text('utf-8'))
        damaged['layers'][1]['blend'] = 'not-in-this-version'
        path.write_text(json.dumps(damaged), 'utf-8')
        b.select_asset('edit-sprite.png')
        b.select_asset('edit-photo.png')
        self.assertEqual([l['name'] for l in self.state()['layers']], ['edit-photo'])
        self.assertIn('one flat layer', b.text('#toast'))
        saved_png = self.server.get(f'/api/asset?key={key}')['revision']
        self.assertEqual(saved_png, revision)
        self.assertPixelsClose(b.editor_pixels(), want, max_abs=2, msg='the flat picture is the saved PNG')
        self.assertNoBrowserErrors()
        # Leave the fake game's photo as it was for the tests after this one (Saved history: Earlier save).
        self.server.post('/api/undo', {'key': key, 'revision': revision})
        b.select_asset('edit-photo.png')
        self.assertPixelsClose(b.editor_pixels(), image_array('edit-photo.png'))

    def test_12_import_adds_a_layer_fitted_to_the_asset(self):
        b = self.fresh('edit-sprite.png')
        art = np.zeros((64, 64, 4), np.uint8); art[...] = (10, 200, 30, 255); art[20:40, 20:40] = (250, 250, 250, 255)
        b.set_files('#importFile', [write_png(art, 'core-import.png')])
        b.wait_for("S.ops.length > 0 && S.ops.at(-1).tool === 'import'")
        self.assertEqual([l['name'] for l in self.state()['layers']], ['edit-sprite', 'core-import'])
        self.assertEqual(self.labels()[-1], 'Import Image')
        self.assertPixelsClose(b.editor_pixels(), art)
        self.assertPixelsClose(b.editor_pixels(0)[..., 3], image_array('edit-sprite.png')[..., 3], msg='the asset layer is kept under it')
        big = np.zeros((128, 32, 4), np.uint8); big[...] = (200, 40, 40, 255)
        b.set_files('#importFile', [write_png(big, 'core-import-big.png')])
        b.wait_for("document.getElementById('dialog').open")
        self.assertIn('32 × 128', b.text('#dialogText'))   # width × height
        b.click_selector('#dialogAccept')
        b.wait_for("Ed.doc.layers.length === 3")
        self.assertPixelsClose(b.editor_pixels(2), np.broadcast_to(rgba((200, 40, 40)), (64, 64, 4)), msg='resized to 64 x 64')

    # ----- old features -------------------------------------------------------------------------------------------------
    def test_13_existing_features_still_work(self):
        b = self.fresh('edit-sprite.png')
        self.tool('brush', size=6)
        b.drag_image([(8, 8), (20, 8)])
        self.assertTrue(b.eval('S.dirty'))
        b.select_asset('edit-tiny.png', discard=False)
        self.assertTrue(b.eval("document.getElementById('dialog').open"), 'switching with unsaved changes asks')
        self.assertEqual(b.text('#dialogTitle'), 'Leave this draft?')
        b.click_selector('#dialog button[value="cancel"]')
        self.assertEqual(b.eval('S.asset.name'), 'edit-sprite.png')
        b.select_asset('edit-tiny.png', discard=False)
        b.click_selector('#dialogAccept')
        b.wait_for("S.asset.name === 'edit-tiny.png' && Ed.doc.width === 16")
        # View modes and the Original compare.
        b.click_selector('[data-view="tile"]')
        b.wait_for("S.mode === 'tile'")
        self.assertEqual(b.eval('Ed.view.blocked'), 'Use Texture view with Original off to paint.')
        self.tool('brush')
        n = len(self.labels())
        b.drag_image([(2, 2), (6, 2)])
        self.assertEqual(len(self.labels()), n, 'no painting in the Tile view')
        self.assertIn('Texture view', b.text('#toast'))
        b.click_selector('[data-view="texture"]')
        b.wait_for("S.mode === 'texture'")
        b.select_asset('edit-photo.png')
        self.tool('brush', size=30, opacity=100)
        b.eval("Ed.setColor('fg', '#0000ff')")
        b.drag_image([(30, 30), (40, 40)])
        cx, cy = b.pixel_center(35, 35)
        self.assertPixelsClose(b.screenshot_array(clip=(cx - 1, cy - 1, 3, 3))[1:2, 1:2], np.array([[[0, 0, 255, 255]]], np.uint8), max_abs=2)
        b.click_selector('#compare')
        b.settle(2)
        self.assertPixelsClose(b.screenshot_array(clip=(cx - 1, cy - 1, 3, 3))[1:2, 1:2], np.array([[[255, 255, 255, 255]]], np.uint8), max_abs=2, msg='Original shows the white square')
        b.click_selector('#compare')
        # The context menu.
        b.click(*b.pixel_center(128, 128), button='right')
        b.wait_for("!document.getElementById('contextMenu').hidden")
        b.key('Escape')
        self.assertTrue(b.eval("document.getElementById('contextMenu').hidden"))
        # Theme AI, Fonts and Review.
        b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden")
        b.click_selector('#fontsButton')
        b.wait_for("document.getElementById('fontsDialog').open", timeout=20)
        b.eval("document.getElementById('fontsDialog').close()")
        b.click_selector('#themeClose')
        b.wait_for("document.getElementById('themeDrawer').hidden")
        b.eval("document.getElementById('reviewButton').disabled = false")
        b.click_selector('#reviewButton')
        b.wait_for("document.getElementById('reviewDialog').open", timeout=20)
        b.eval("document.getElementById('reviewDialog').close()")
        # select.js multi-select on the cards.
        b.click_selector('#assetList .asset-card .pick')
        b.wait_for("document.getElementById('selectedCount').textContent.includes('1 checked')")
        b.click_selector('#clearSelected')
        # The fake game's font previews are refused by the server (fonts.py, not the editor); everything else is clean.
        self.assertNoBrowserErrors(allow=['/api/font-preview'])

    # ----- tools ----------------------------------------------------------------------------------------------------------
    def test_14_tool_shortcuts_size_opacity_and_colors(self):
        b = self.fresh('edit-photo.png')
        b.eval("document.activeElement && document.activeElement.blur()")
        for key, tool in (('b', 'brush'), ('e', 'eraser'), ('i', 'eyedropper'), ('h', 'hand'), ('z', 'zoom'), ('B', 'brush'), ('E', 'eraser')):
            b.key(key)
            self.assertEqual((b.eval('Ed.tool'), b.eval('S.tool')), (tool, tool), f'after {key}')
            self.assertTrue(b.eval(f"document.querySelector('#edTools [data-tool=\"{tool}\"]').classList.contains('active')"))
        b.key('b')
        b.eval("Ed.setToolOptions('brush', {size: 12, hardness: 100, opacity: 100})")
        b.key(']'); self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 15)
        b.key('['); b.key('['); self.assertEqual(b.eval("Ed.toolOptions('brush').size"), 9)
        self.assertEqual(b.eval("document.getElementById('brushSize').value"), '9', 'the options bar follows')
        b.key('{'); self.assertEqual(b.eval("Ed.toolOptions('brush').hardness"), 75)
        b.key('}'); self.assertEqual(b.eval("Ed.toolOptions('brush').hardness"), 100)
        b.set_value('#brushSize', 33)
        self.assertEqual(b.eval("JSON.parse(localStorage.getItem('ed-tool:brush')).size"), 33, 'options persist per tool')
        time.sleep(.8); b.key('7'); self.assertEqual(b.eval("Ed.toolOptions('brush').opacity"), 70)
        time.sleep(.8); b.key('0'); self.assertEqual(b.eval("Ed.toolOptions('brush').opacity"), 100)
        b.key('5'); self.assertEqual(b.eval("Ed.toolOptions('brush').opacity"), 5, '0 then 5 quickly is 5%')
        time.sleep(.8); b.key('4'); b.key('5'); self.assertEqual(b.eval("Ed.toolOptions('brush').opacity"), 45, 'two quick digits')
        b.eval("Ed.setToolOptions('brush', {opacity: 100})")
        b.key('h'); time.sleep(.8); b.key('3')
        self.assertAlmostEqual(self.state()['layers'][0]['opacity'], 0.3, msg='without a tool opacity the digits set the layer opacity')
        self.assertEqual(self.labels()[-1], 'Opacity Change')
        b.eval("Ed.layers.set(0, {opacity: 1})")
        b.eval("Ed.setColor('fg', '#112233'); Ed.setColor('bg', '#445566')")
        b.key('x'); self.assertEqual((b.eval('Ed.fg'), b.eval('Ed.bg')), ('#445566', '#112233'))
        b.key('d'); self.assertEqual((b.eval('Ed.fg'), b.eval('Ed.bg')), ('#000000', '#ffffff'))
        self.assertEqual(b.eval("document.getElementById('paintColor').value"), '#000000')
        b.set_value('#paintColor', '#ff00ff')
        self.assertEqual(b.eval('Ed.fg'), '#ff00ff', 'the native color input sets the foreground')
        # A module can replace the color picker (the color agent's Photoshop-style picker).
        b.eval("Ed.registerColorPicker(async ({which, color}) => { window.__picked = [which, color]; return which === 'bg' ? '#0a0b0c' : null; })")
        b.click_selector('#edTools .ed-bg')
        b.wait_for("Ed.bg === '#0a0b0c'")
        self.assertEqual(b.eval('__picked'), ['bg', '#ffffff'])
        b.click_selector('#edTools .ed-fg')
        b.wait_for("window.__picked[0] === 'fg'")
        self.assertEqual(b.eval('Ed.fg'), '#ff00ff', 'a picker that returns nothing leaves the color')
        b.eval("Ed.registerColorPicker(null)")   # back to the native picker
        # Typing into a field never triggers shortcuts.
        b.click_selector('#search')
        b.type_text('b'); b.key('e')
        self.assertEqual(b.eval('Ed.tool'), 'hand')
        b.set_value('#search', '')
        b.eval("document.getElementById('search').blur()")
        # Ctrl+0 fits, Ctrl+1 is 100%, Ctrl+plus / minus step through Photoshop's zoom levels.
        b.key('Ctrl+1'); self.assertAlmostEqual(b.eval('S.zoom'), 1)
        b.key('Ctrl+='); self.assertAlmostEqual(b.eval('S.zoom'), 2)
        b.key('Ctrl+-'); b.key('Ctrl+-'); self.assertAlmostEqual(b.eval('S.zoom'), 0.6667, places=3)
        b.key('Ctrl+0'); self.assertAlmostEqual(b.eval('S.zoom'), b.eval("Math.min((document.getElementById('viewport').clientWidth - 100) / 256, (document.getElementById('viewport').clientHeight - 100) / 256, 6)"))

    def test_15_eyedropper_alt_sampling_hand_space_zoom_and_wheel(self):
        b = self.fresh('edit-photo.png')
        b.key('i')
        b.click(*b.pixel_center(40, 220))          # in the red triangle
        self.assertEqual(b.eval('Ed.fg'), '#ff0000')
        b.click(*b.pixel_center(30, 30), modifiers=1)  # Alt-click: background color
        self.assertEqual(b.eval('Ed.bg'), '#ffffff')
        b.eval("Ed.setToolOptions('eyedropper', {sample: 5})")
        b.click(*b.pixel_center(64, 64))           # the white square's corner: averaged
        self.assertNotIn(b.eval('Ed.fg'), ('#ffffff', '#ff0000'))
        b.eval("Ed.setToolOptions('eyedropper', {sample: 1})")
        # Alt with the brush samples instead of painting.
        b.key('b')
        n = len(self.labels())
        with b.hold('Alt'):
            b.click(*b.pixel_center(200, 64))     # the black disk
        self.assertEqual(b.eval('Ed.fg'), '#000000')
        self.assertEqual(len(self.labels()), n, 'nothing painted')
        # Escape in the middle of a stroke cancels it.
        before = b.editor_pixels()
        b.eval("Ed.setColor('fg', '#00ff00')")
        x0, y0 = b.pixel_center(100, 150)
        b.mouse_move(x0, y0); b.mouse_down(x0, y0); b.mouse_move(x0 + 60, y0); b.mouse_move(x0 + 120, y0)
        self.assertTrue(b.eval('!!Ed.preview.active'))
        b.key('Escape')
        b.mouse_up(x0 + 120, y0)
        self.assertEqual(len(self.labels()), n)
        self.assertPixelsClose(b.editor_pixels(), before)
        # A fast zigzag (moves merged by the browser): every coalesced sample reaches the brush, so no corner is cut.
        b.eval("Ed.setToolOptions('brush', {size: 3, hardness: 100, opacity: 100}); Ed.setColor('fg', '#00ffff')")
        zig = [(40 + 6 * i, 96 if i % 2 else 112) for i in range(25)]
        b.drag_image(zig, steps_between=3, burst=True)
        b.wait_for("Ed.history.list().at(-1).label === 'Brush Tool'")
        px = b.editor_pixels()
        for x, y in zig[1:-1]:
            self.assertEqual(tuple(px[y, x][:3]), (0, 255, 255), f'the zigzag reaches ({x}, {y})')
        b.key('Ctrl+Z'); b.key('Escape')
        n = len(self.labels())
        # The Undo button in the options bar is the same history.
        b.eval("Ed.layers.add({history: 'New Layer'})")
        b.click_selector('#undoDraft')
        self.assertEqual(len(self.state()['layers']), 1)
        b.click_selector('#redoDraft')
        self.assertEqual(len(self.state()['layers']), 2)
        b.key('Ctrl+Z')
        # Space held pans with any tool, without painting.
        b.click(*b.pixel_center(5, 5), button='middle')
        px, py = b.eval('[S.px, S.py]')
        x, y = b.pixel_center(128, 128)
        with b.hold(' '):
            b.drag([(x, y), (x + 30, y + 10)])
        for got, want in zip(b.eval('[S.px, S.py]'), (px + 30, py + 10)): self.assertAlmostEqual(got, want, delta=0.01)
        self.assertEqual(len(self.labels()), n)
        # The Hand tool drags too.
        b.key('h')
        b.drag([(x, y), (x - 30, y - 10)])
        for got, want in zip(b.eval('[S.px, S.py]'), (px, py)): self.assertAlmostEqual(got, want, delta=0.01)
        # Zoom tool: click zooms in around the point, Alt-click zooms out, a dragged box fills the view.
        b.key('z')
        z0 = b.eval('S.zoom')
        cx, cy = b.pixel_center(60, 180)
        b.click(cx, cy)
        z1 = b.eval('S.zoom')
        self.assertGreater(z1, z0)
        for got, want in zip(b.client_to_image(cx, cy), (60.5, 180.5)): self.assertAlmostEqual(got, want, delta=0.05)
        b.click(cx, cy, modifiers=1)
        self.assertLess(b.eval('S.zoom'), z1)
        a, c = b.pixel_center(16, 16), b.pixel_center(80, 80)
        b.drag([a, c])
        self.assertGreater(b.eval('S.zoom'), z1)
        mid = b.client_to_image(*b.eval("(() => { const r = document.getElementById('viewport').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()"))
        for got, want in zip(mid, (48, 48)): self.assertAlmostEqual(got, want, delta=1)
        # The wheel zooms around the cursor.
        b.key('Ctrl+0')
        cx, cy = (round(v) for v in b.pixel_center(200, 50))   # wheel events carry whole client pixels
        under, z = b.client_to_image(cx, cy), b.eval('S.zoom')
        b.wheel(cx, cy, -240)
        self.assertGreater(b.eval('S.zoom'), z * 1.2)
        for got, want in zip(b.client_to_image(cx, cy), under): self.assertAlmostEqual(got, want, delta=0.01)

    def test_23_tool_groups_flyout_cycling_and_errors_in_tools(self):   # last: it leaves two test tools registered
        b = self.fresh('edit-photo.png')
        b.eval("""Ed.registerTool('test-pencil', {label: 'Test Pencil', group: 'brush', key: 'b', icon: 'P', edits: true, onDown() {}, onMove() {}, onUp() {}});
                  Ed.registerTool('test-broken', {label: 'Broken', group: 'test', key: '', icon: '!', onDown() { throw Error('boom in a tool'); }})""")
        b.settle(3)
        slot = '#edTools .ed-tool[data-group="brush"]'
        self.assertTrue(b.eval(f"!!document.querySelector('{slot} .ed-tool-more')"), 'a group with more tools shows the corner mark')
        b.click_selector(slot, button='right')
        b.wait_for("!!document.querySelector('.ed-flyout')")
        self.assertEqual(b.eval("[...document.querySelectorAll('.ed-flyout-item')].map(e => e.dataset.tool)"), ['brush', 'test-pencil'])
        self.assertFalse(b.eval("document.getElementById('contextMenu').hidden === false"), 'no asset menu on the tool strip')
        b.click_selector('.ed-flyout-item[data-tool="test-pencil"]')
        self.assertEqual(b.eval('Ed.tool'), 'test-pencil')
        self.assertEqual(b.eval(f"document.querySelector('{slot}').dataset.tool"), 'test-pencil', 'the slot shows the last used tool')
        b.key('B'); self.assertEqual(b.eval('Ed.tool'), 'brush', 'Shift+B cycles the group')
        b.key('B'); self.assertEqual(b.eval('Ed.tool'), 'test-pencil')
        b.key('e'); b.key('b'); self.assertEqual(b.eval('Ed.tool'), 'test-pencil', 'B returns to the group’s current tool')
        # A tool that throws is reported in Ed.test.errors and does not break the editor.
        b.eval("Ed.setTool('test-broken')")
        b.click(*b.pixel_center(50, 50))
        self.assertIn('boom in a tool', b.eval("Ed.test.errors.map(e => e.message).join(' ')"))
        self.assertNoBrowserErrors(allow=['boom in a tool'])
        b.key('b'); b.key('B')
        self.assertEqual(b.eval('Ed.tool'), 'brush')
        b.eval("Ed.registerTool('test-pencil', {label: 'Test Pencil', group: 'test-retired', key: '', icon: 'P'}); Ed.registerTool('test-broken', {label: 'Broken', group: 'test-retired', key: '', icon: '!'})")

    def test_16_color_and_surface_revert_selection_modes_grid_and_layer_keys(self):
        b = self.fresh('edit-photo.png')
        photo = b.editor_pixels()
        # Color & surface runs on the server for the active layer and comes back through the selection.
        b.eval("Ed.select.rect(0, 0, 128, 256)")
        b.set_value('#saturation', 0)
        b.click_selector('#applyAdjust')
        b.wait_for("Ed.history.list().at(-1).label === 'Color & Surface' && !S.busy", timeout=30)
        adjusted = b.editor_pixels()
        left = adjusted[:, :128].astype(int)
        self.assertLessEqual(int(np.abs(left[..., 0] - left[..., 1]).max()), 2, 'gray inside the selection')
        self.assertPixelsClose(adjusted[:, 128:], photo[:, 128:], msg='unchanged outside it')
        self.assertEqual(b.eval("S.ops.at(-1).tool"), 'adjust')
        # Revert to Original replaces the layers with the game's art in one step that undo takes back.
        b.key('Ctrl+D'); b.key('Shift+Ctrl+N')
        b.click_selector('#edMenubar [data-menu="File"]')
        b.click_selector('.ed-menu .ed-menu-item[data-path="File/Revert to Original"]')
        self.assertEqual([l['name'] for l in self.state()['layers']], ['edit-photo'])
        self.assertPixelsClose(b.editor_pixels(), image_array('edit-photo.png'))
        self.assertEqual((self.labels()[-1], b.eval("S.ops.at(-1).tool")), ('Revert to Original', 'original'))
        b.key('Ctrl+Z')
        self.assertEqual(len(self.state()['layers']), 2)
        self.assertPixelsClose(b.editor_pixels(), adjusted)
        # Selection modes: add, subtract, intersect.
        b.eval("Ed.select.rect(10, 10, 40, 40); Ed.select.rect(30, 30, 40, 40, 'add')")
        sel = b.editor_pixels('selection')[..., 3]
        self.assertEqual((int(sel[15, 15]), int(sel[60, 60]), int(sel[15, 60])), (255, 255, 0))
        b.eval("Ed.select.rect(20, 20, 20, 20, 'subtract')")
        sel = b.editor_pixels('selection')[..., 3]
        self.assertEqual((int(sel[25, 25]), int(sel[15, 15])), (0, 255))
        b.eval("Ed.select.rect(0, 0, 45, 45, 'intersect')")
        sel = b.editor_pixels('selection')[..., 3]
        self.assertEqual((int(sel[60, 60]), int(sel[15, 15]), int(sel[42, 42])), (0, 255, 255))
        self.assertEqual(self.labels()[-4:], ['Rectangular Marquee'] * 4, 'one state each, named after the tool as in Photoshop')
        b.eval("const m = document.createElement('canvas'); m.width = m.height = 256; m.getContext('2d').fillRect(0, 0, 9, 9); Ed.select.set(m, 'add')")
        self.assertEqual(self.labels()[-1], 'Add to Selection')
        b.key('Ctrl+Z')
        b.key('Ctrl+Z')
        self.assertEqual(int(b.editor_pixels('selection')[60, 60, 3]), 255, 'selection changes are undoable')
        # The pixel grid shows from 800%.
        b.key('Ctrl+D')
        b.eval("Ed.view.setZoom(16)"); b.settle(3)
        on = self.overlay_pixels()
        b.eval("Ed.menu.run('View/Pixel Grid')"); b.settle(3)
        off = self.overlay_pixels()
        self.assertGreater(on, 5000)
        self.assertEqual(off, 0, 'no grid and no old marching ants (an empty overlay must not show an older frame)')
        self.assertTrue(b.eval("document.getElementById('viewOverlay').hidden"), 'an empty overlay is hidden, so no older frame can show')
        b.eval("Ed.menu.run('View/Pixel Grid')"); b.key('Ctrl+0')
        # The layer list: Up and Down pick layers, F2 renames.
        b.eval("document.querySelector('#edPanel-layers .ed-layer-list').focus()")
        b.key('ArrowDown')
        self.assertEqual(self.state()['activeIndex'], 0)
        b.key('ArrowUp')
        self.assertEqual(self.state()['activeIndex'], 1)
        b.key('F2')
        b.wait_for("document.activeElement && document.activeElement.classList.contains('ed-rename')")
        b.key('Ctrl+A'); b.type_text('Notes'); b.key('Enter')
        self.assertEqual(self.state()['layers'][1]['name'], 'Notes')

    # ----- menus, dialogs, focus ------------------------------------------------------------------------------------------
    def test_17_menubar_keyboard_and_menu_run(self):
        b = self.fresh('edit-photo.png')
        b.click_selector('#edMenubar [data-menu="Select"]')
        b.wait_for("!!document.querySelector('.ed-menu')")
        items = b.eval("[...document.querySelectorAll('.ed-menu .ed-menu-item')].map(e => [e.querySelector('.ed-menu-label').textContent, e.querySelector('.ed-menu-key').textContent, e.disabled])")
        self.assertEqual(items, [['All', 'Ctrl+A', False], ['Deselect', 'Ctrl+D', True], ['Reselect', 'Shift+Ctrl+D', False], ['Inverse', 'Shift+Ctrl+I', True]])
        b.click_selector('.ed-menu .ed-menu-item[data-path="Select/All"]')
        self.assertTrue(b.eval('Ed.select.hasSelection()'))
        self.assertFalse(b.eval("!!document.querySelector('.ed-menu')"))
        # Keyboard: open with Enter, move with the arrows, run with Enter.
        b.eval("document.querySelector('#edMenubar [data-menu=\"Layer\"]').focus()")
        b.key('Enter')
        b.wait_for("document.activeElement && document.activeElement.classList.contains('ed-menu-item')")
        self.assertEqual(b.eval("document.activeElement.dataset.path"), 'Layer/New Layer')
        b.key('Enter')
        self.assertEqual(len(self.state()['layers']), 2)
        b.eval("document.querySelector('#edMenubar [data-menu=\"Layer\"]').focus()")
        b.key('ArrowDown'); b.key('ArrowDown'); b.key('ArrowDown'); b.key('ArrowDown')
        self.assertEqual(b.eval("document.activeElement.querySelector('.ed-menu-label').textContent"), 'Layer Mask')
        b.key('ArrowRight')
        self.assertEqual(b.eval("document.activeElement.dataset.path"), 'Layer/Layer Mask/Reveal All')
        b.key('Escape'); b.key('Escape')
        self.assertFalse(b.eval("!!document.querySelector('.ed-menu')"))
        # The Undo item names the step; Ed.menu.run runs commands from code.
        b.click_selector('#edMenubar [data-menu="Edit"]')
        self.assertEqual(b.eval("document.querySelector('.ed-menu [data-path=\"Edit/Undo\"] .ed-menu-label').textContent"), 'Undo New Layer')
        b.key('Escape')
        self.assertEqual(b.eval("Ed.menu.run('Edit/Undo')"), True)
        self.assertEqual(len(self.state()['layers']), 1)
        self.assertEqual(b.eval("Ed.menu.run('Edit/Redo')"), True)
        self.assertFalse(b.eval("Ed.menu.run('Select/Reselect')"), 'disabled while there is a selection')
        b.eval("Ed.registerMenu('Filter/Test/Make It Red...', {action(p) { window.__ran = p; }, shortcut: 'Ctrl+Shift+F12', order: 5})")
        b.eval("Ed.menu.run('Filter/Test/Make It Red...', {amount: 3})")
        self.assertEqual(b.eval('__ran'), {'amount': 3})
        b.key('Ctrl+Shift+F12')
        self.assertIsNone(b.eval('__ran'), 'the shortcut runs it with no params (it would open its dialog)')
        b.wait_for("!!document.querySelector('#edMenubar [data-menu=\"Filter\"]')")   # the menubar rebuilds when a menu is added
        b.click_selector('#edMenubar [data-menu="Filter"]')
        b.wait_for("!!document.querySelector('.ed-menu [aria-haspopup]')")
        b.eval("document.querySelector('.ed-menu .ed-menu-item[aria-haspopup]').dispatchEvent(new PointerEvent('pointerenter'))")
        b.wait_for("document.querySelectorAll('.ed-menu').length === 2")
        self.assertEqual(b.eval("document.querySelectorAll('.ed-menu')[1].querySelector('.ed-menu-key').textContent"), 'Ctrl+Shift+F12')
        b.key('Escape')

    def test_24_panels_and_layer_badges_from_modules(self):   # late: the test panel stays registered
        b = self.fresh('edit-sprite.png')
        b.eval("""window.__renders = 0; Ed.registerPanel('test-swatches', {title: 'Swatches', order: 30, render(c) { __renders++; c.textContent = 'Swatches for ' + (Ed.doc ? Ed.doc.layers.length : 0) + ' layers'; }});
                  Ed.registerLayerBadge(L => Object.keys(L.effects || {}).length ? 'fx' : null)""")
        b.wait_for("[...document.querySelectorAll('#edDock .ed-tab')].map(e => e.textContent).join() === 'Layers,History,Swatches'")
        b.click_selector('#edDock .ed-tab[data-panel="test-swatches"]')
        self.assertEqual(b.text('#edPanel-test-swatches'), 'Swatches for 1 layers')
        b.key('Shift+Ctrl+N')
        b.wait_for("document.getElementById('edPanel-test-swatches').textContent === 'Swatches for 2 layers'", message='panels refresh after document changes')
        b.click_selector('#edDock .ed-tab[data-panel="layers"]')
        b.eval("Ed.layers.set(0, {effects: {'drop-shadow': {}}})")
        b.wait_for("document.querySelector('#edPanel-layers .ed-layer[data-index=\"0\"] .ed-badges').textContent.includes('fx')")
        self.assertNotIn('fx', b.text('#edPanel-layers .ed-layer[data-index="1"] .ed-badges'))
        self.assertNoBrowserErrors()

    def test_18_dialog_preview_ok_cancel_and_reset(self):
        b = self.fresh('edit-photo.png')
        photo = b.editor_pixels()
        b.eval("""window.__dlg = Ed.dialog({title: 'Test Brighten', fields: [
              {key: 'amount', label: 'Amount', type: 'range', min: 0, max: 100, default: 50, unit: '%'},
              {key: 'mode', label: 'Mode', type: 'select', options: [{value: 'add', label: 'Add'}, {value: 'none', label: 'Nothing'}], default: 'add'},
              {key: 'curve', label: 'Curve', type: 'curve'}],
            preview(p) { const L = Ed.layers.active(), c = Ed.util.canvas(L.canvas.width, L.canvas.height), g = c.getContext('2d'); g.drawImage(L.canvas, 0, 0);
              if (p.mode === 'add') { g.globalCompositeOperation = 'lighter'; g.fillStyle = `rgb(${p.amount},${p.amount},${p.amount})`; g.fillRect(0, 0, c.width, c.height); }
              Ed.preview.layer({canvas: c}); },
            ok(p) { return Ed.applyToTarget('Test Brighten', g => { if (p.mode === 'add') { g.globalCompositeOperation = 'lighter'; g.fillStyle = `rgb(${p.amount},${p.amount},${p.amount})`; g.fillRect(0, 0, 256, 256); } }); }}); true""")
        sel = '[data-ed-dialog="Test Brighten"]'
        b.wait_for(f"document.querySelector('{sel}') && document.querySelector('{sel}').open")
        self.assertTrue(b.eval(f"['amount', 'mode', 'preview'].every(n => !!document.querySelector('{sel} [name=' + n + ']'))"))
        self.assertIn('needs a module', b.text(f'{sel} .ed-field[data-key="curve"]'))
        b.wait_for_pixels(lambda px: int(px[2, 2, 1]) == min(255, int(photo[2, 2, 1]) + 50))
        self.assertEqual(self.labels(), ['Open'], 'a preview is not history')
        b.set_value(f'{sel} [name=amount]', 10)
        b.wait_for_pixels(lambda px: int(px[2, 2, 1]) == int(photo[2, 2, 1]) + 10)
        b.set_value(f'{sel} [name=preview]', False)
        b.wait_for_pixels(lambda px: int(px[2, 2, 1]) == int(photo[2, 2, 1]))
        b.set_value(f'{sel} [name=preview]', True)
        b.click_selector(f'{sel} button.quiet')   # Reset
        self.assertEqual(b.eval(f"document.querySelector('{sel} [name=amount]').value"), '50')
        b.click_selector(f'{sel} button:not(.primary):not(.quiet)')   # Cancel
        self.assertIsNone(b.eval('await __dlg'))
        self.assertPixelsClose(b.editor_pixels(), photo)
        self.assertFalse(b.eval(f"!!document.querySelector('{sel}')"))
        # OK applies once, inside the selection.
        b.eval("Ed.select.rect(0, 0, 128, 256); window.__dlg = Ed.dialog({title: 'Test Brighten', fields: [{key: 'amount', type: 'number', default: 20}], preview() {}, ok(p) { return Ed.applyToTarget('Test Brighten', g => { g.globalCompositeOperation = 'lighter'; g.fillStyle = `rgb(${p.amount},${p.amount},${p.amount})`; g.fillRect(0, 0, 256, 256); }); }}); true")
        b.wait_for(f"!!document.querySelector('{sel}')")
        b.click_selector(f'{sel} button.primary')
        self.assertEqual(b.eval('(await __dlg).amount'), 20)
        px = b.editor_pixels()
        self.assertEqual(int(px[2, 2, 1]), int(photo[2, 2, 1]) + 20)
        self.assertEqual(int(px[2, 200, 1]), int(photo[2, 200, 1]), 'outside the selection: unchanged')
        self.assertEqual(self.labels()[-1], 'Test Brighten')

    def test_19_focus_mode_and_the_folding_mod_tray(self):
        b = self.fresh('edit-photo.png')
        w = b.eval("document.getElementById('viewport').clientWidth")
        b.click_selector('#focusButton')
        self.assertTrue(b.eval("document.body.classList.contains('ed-focus-mode')"))
        self.assertTrue(b.eval("getComputedStyle(document.querySelector('.library')).display === 'none' && getComputedStyle(document.querySelector('.mod-tray')).display === 'none'"))
        self.assertGreater(b.eval("document.getElementById('viewport').clientWidth"), w + 200)
        b.reload()
        self.assertTrue(b.eval("document.body.classList.contains('ed-focus-mode')"), 'remembered')
        b.eval("document.activeElement && document.activeElement.blur()")
        b.key('f')
        self.assertFalse(b.eval("document.body.classList.contains('ed-focus-mode')"))
        h = b.eval("document.querySelector('.mod-tray').offsetHeight")
        b.click_selector('#trayToggle')
        self.assertLess(b.eval("document.querySelector('.mod-tray').offsetHeight"), min(h, 48))
        self.assertEqual(b.eval("document.getElementById('trayToggle').getAttribute('aria-expanded')"), 'false')
        b.click_selector('#trayToggle')
        # Rarely, right after a reload, the lab server (listen backlog 5) refuses one of the burst of library thumbnails.
        self.assertNoBrowserErrors(allow=['ERR_CONNECTION_REFUSED (http://127.0.0.1', 'net::ERR_CONNECTION_REFUSED'])

    def test_20_test_hooks_and_view_mapping(self):
        b = self.fresh('edit-sprite.png')
        for x, y in ((0, 0), (10.5, 20.25), (64, 64)):
            cx, cy = b.image_to_client(x, y)
            for got, want in zip(b.client_to_image(cx, cy), (x, y)): self.assertAlmostEqual(got, want, places=6)
        r = b.eval("(() => { const r = document.getElementById('view').getBoundingClientRect(); return [r.left + transform.x, r.top + transform.y, transform.scale]; })()")
        cx, cy = b.image_to_client(3, 4)
        self.assertAlmostEqual(cx, r[0] + 3 * r[2], places=6)
        self.assertAlmostEqual(cy, r[1] + 4 * r[2], places=6)
        data = b.eval("(async () => { const p = await Ed.test.pixels('composite'); return [p.width, p.height, typeof p.data, p.data.length]; })()")
        self.assertEqual(data[:3], [64, 64, 'string'])
        self.assertEqual(int(b.editor_pixels('mask')[..., 3].min()), 255, 'no mask reads as all visible')
        art = np.random.default_rng(3).integers(0, 256, (12, 20, 4), dtype=np.uint8)
        b.load_editor_image(art)
        self.assertPixelsClose(premultiplied(b.editor_pixels()), premultiplied(art))
        self.assertEqual((self.state()['layers'][0]['name'], b.eval('Ed.doc.width')), ('Layer 0', 20))
        self.assertEqual(b.eval('Ed.test.ready()'), True)

    # ----- speed ------------------------------------------------------------------------------------------------------------
    def test_21_stroke_frame_times_on_a_2048_sheet_with_three_layers(self):
        b = self.fresh('edit-big.png')
        tint = np.zeros((2048, 2048, 4), np.uint8); tint[..., :3] = (255, 128, 0); tint[..., 3] = 60
        self.add_layer(tint, 'Tint', blend='multiply')
        dots = np.zeros((2048, 2048, 4), np.uint8); dots[::64, ::64] = (255, 255, 255, 255)
        self.add_layer(dots, 'Dots')
        b.eval("Ed.layers.setActive(1)")
        self.tool('brush', size=40, hardness=100, opacity=80)
        b.eval("Ed.setColor('fg', '#3355ff')")
        # The Brush tool gets one pointer move per animation frame, as from a tablet that keeps up with the screen. The time
        # between frames is what one frame of painting costs: compositing the changed rectangle and drawing the view. The GPU
        # work is included because the browser holds the next frame back until the previous one is drawn.
        pts = [(200 + i * 20, 1024 + 300 * np.sin(i / 12)) for i in range(80)]
        result = b.eval("""(async pts => { const f = () => new Promise(r => requestAnimationFrame(r)), P = ([x, y]) => ({x, y, pressure: 1, pointerType: 'mouse', buttons: 1});
            const idle = []; let t0 = await f(); for (let i = 0; i < 20; i++) { const t = await f(); idle.push(t - t0); t0 = t; }
            const frames = [], js = []; Ed.callTool('onDown', P(pts[0])); let last = await f();
            for (const p of pts.slice(1)) { const a = performance.now(); Ed.callTool('onMove', P(p)); Ed.flush(); js.push(performance.now() - a); const t = await f(); frames.push(t - last); last = t; }
            const c = performance.now(); Ed.callTool('onUp', P(pts.at(-1))); const commit = performance.now() - c; await f(); await f();
            const u = performance.now(); Ed.history.undo(); Ed.flush(); const undo = performance.now() - u; Ed.history.redo();
            return {idle, frames, js, commit, undo}; })(%s)""" % json.dumps(pts), timeout=120)
        frames, js, idle = result['frames'], result['js'], statistics.median(result['idle'])
        self.assertEqual(self.labels()[-1], 'Brush Tool')
        med, p95, worst = statistics.median(frames), float(np.percentile(frames, 95)), max(frames)
        gpu = 'real GPU (ASSET_LAB_TEST_GPU=1)' if GPU else 'SwiftShader software GPU'
        report = (f'{len(frames)} frames while painting: median {med:.1f} ms, 95th percentile {p95:.1f} ms, worst {worst:.1f} ms '
                  f'(an idle frame is {idle:.1f} ms here; compositing JavaScript per move: median {statistics.median(js):.1f} ms); '
                  f'committing the stroke {result["commit"]:.1f} ms, undoing it {result["undo"]:.1f} ms of JavaScript; {gpu}')
        print('\n[stroke frame times, 2048 x 2048, 3 layers, size 40 brush]', report)
        (OUTPUT / ('stroke-frame-times-gpu.txt' if GPU else 'stroke-frame-times.txt')).write_text(report + '\n' + json.dumps([round(f, 2) for f in frames]) + '\n')
        self.assertLess(med, 150, report)
        for x, y in (pts[0], pts[-1]):
            self.assertTrue((b.editor_pixels(1, region=(int(x) - 4, int(y) - 4, 8, 8))[..., 2] > 200).any(), f'the stroke reached ({x:.0f}, {y:.0f})')
        changed = b.editor_pixels(1, region=(196, 1020, 8, 8))   # where the stroke starts, (200, 1024)
        self.assertTrue((changed[..., 2] > 200).any(), 'the stroke landed on the middle layer')

    # ----- looks ------------------------------------------------------------------------------------------------------------
    def test_22_screenshots_at_common_sizes(self):
        b = self.fresh('edit-photo.png')
        art = np.zeros((256, 256, 4), np.uint8); art[60:140, 120:230] = (255, 210, 60, 230)
        self.add_layer(art, 'Sun', history="'New Layer'", blend='screen')
        b.eval("Ed.layers.addMask(1, 'reveal'); Ed.select.rect(40, 150, 120, 70)")
        self.tool('brush', size=18, hardness=80, opacity=90)
        try:
            for w, h in ((1200, 800), (1600, 1000), (1920, 1080), (1366, 768)):
                b.set_viewport(w, h)
                b.eval("Ed.view.fit()")
                b.settle(4)
                b.mouse_move(*b.pixel_center(100, 100))
                b.settle(2)
                b.screenshot(f'core-{w}x{h}.png')
                self.assertEqual(b.eval('document.documentElement.scrollWidth'), w, f'no horizontal scroll at {w}')
                for sel in ('#edMenubar', '#edOptions', '#edTools', '#edDock', '#viewport'):
                    box = b.eval(f"(() => {{ const r = document.querySelector('{sel}').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }})()")
                    self.assertTrue(box[2] > 20 and box[3] > 20 and box[0] >= 0 and box[0] + box[2] <= w + 1, f'{sel} at {w}x{h}: {box}')
                self.assertGreater(b.eval("document.getElementById('viewport').clientHeight"), 300 if h >= 768 else 200)
                strip = b.eval("(() => { const g = document.querySelector('.ed-tool-grid'), r = g.getBoundingClientRect(); return [r.width, g.scrollHeight <= g.clientHeight + 1]; })()")
                self.assertLess(strip[0], 80, 'the tool strip stays slim')
                self.assertTrue(strip[1], 'every tool slot is visible')
            b.click_selector('#edMenubar [data-menu="Layer"]')
            b.eval("document.querySelector('.ed-menu .ed-menu-item[aria-haspopup]').dispatchEvent(new PointerEvent('pointerenter'))")
            b.settle(2)
            b.screenshot('core-menu-1366x768.png')
            b.key('Escape')
            b.click_selector('#edTools .ed-tool[data-tool="brush"]', button='right')   # one tool in the group: no flyout
            b.click_selector('#focusButton')
            b.eval("Ed.view.fit()"); b.settle(3)
            b.screenshot('core-focus-1366x768.png')
            self.assertEqual(b.eval('document.documentElement.scrollWidth'), 1366)
            b.click_selector('#focusButton')
            # Theme AI's drawer takes 420 px on wide screens; the editor still fits beside it.
            b.set_viewport(1600, 1000)
            b.click_selector('#themeButton')
            b.wait_for("!document.getElementById('themeDrawer').hidden")
            b.eval("Ed.view.fit()"); b.settle(4)
            b.screenshot('core-theme-1600x1000.png')
            self.assertEqual(b.eval('document.documentElement.scrollWidth'), 1600)
            self.assertLessEqual(b.eval("document.querySelector('.inspector').getBoundingClientRect().right"), 1600 - 420 + 1)
            b.click_selector('#themeClose')
        finally:
            b.set_viewport(*self.viewport)


if __name__ == '__main__':
    import unittest
    unittest.main()
