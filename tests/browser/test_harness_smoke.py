"""Proves the browser harness against today's editor (src/web/app.js): page load, asset selection, real mouse brush strokes,
Ctrl+Z, pen pressure, tilt and fast (merged) pen bursts, keyboard shortcuts and key codes, wheel zoom, right/middle-button
panning, screenshots, viewport sizes, pixel reads up to 2048 x 2048, reload, file import and the window.Ed hooks.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -v
"""
import time
import numpy as np
from PIL import Image
from harness import (EditorTestCase, OUTPUT, assert_pixels_close, distance_to_path, image_array, premultiplied, psnr, refuse_live, rgba,
                     write_png)
from fakegame import EDITOR_IMAGES, SPRITE

KEY_LOG = ("window.__keys = []; window.__keyLog = e => __keys.push([e.type, e.key, e.code, e.keyCode, e.ctrlKey, e.shiftKey, e.altKey, e.repeat]);"
           "document.addEventListener('keydown', __keyLog, true); document.addEventListener('keyup', __keyLog, true);")
PEN_LOG = ("window.__pen = []; window.__penLog = e => __pen.push({type: e.type, pointerType: e.pointerType, pressure: e.pressure, buttons: e.buttons, tilt: [e.tiltX, e.tiltY],"
           "merged: e.type === 'pointermove' ? e.getCoalescedEvents().map(c => c.pressure) : []});"
           "for (const t of ['pointerdown', 'pointermove', 'pointerup']) document.getElementById('view').addEventListener(t, __penLog);")


class HarnessSmoke(EditorTestCase):
    def test_01_page_loads_without_errors(self):
        b = self.browser
        self.assertGreater(b.eval("document.querySelectorAll('#assetList .asset-card').length"), 4)
        self.assertIn('visuals', b.text('#inventory'))
        self.assertNoBrowserErrors(everything=True)

    def test_02_catalog_lists_the_editor_images_as_editable(self):
        rows = {r['name']: r for r in self.server.assets(kind='visual', all='1')}
        for name, size in (('edit-photo.png', 256), ('edit-sprite.png', 64), ('edit-big.png', 2048), ('edit-tiny.png', 16)):
            with self.subTest(name):
                self.assertTrue(rows[name]['editable'])
                self.assertEqual((rows[name]['width'], rows[name]['height'], rows[name]['source']), (size, size, 'assets/' + name))
        cards = self.browser.eval("[...document.querySelectorAll('#assetList .asset-card .asset-label')].map(e => e.textContent)")
        for name in EDITOR_IMAGES: self.assertIn(name, cards)

    def test_03_select_asset_shows_the_sprite_on_the_canvas(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        self.assertEqual(b.eval('S.asset.name'), 'edit-sprite.png')
        draft, expected = b.editor_pixels(), image_array('edit-sprite.png')
        # A canvas keeps premultiplied colors: half-transparent edge pixels come back rounded, so compare them premultiplied.
        self.assertPixelsClose(draft[..., 3], expected[..., 3], msg='alpha')
        self.assertPixelsClose(premultiplied(draft), premultiplied(expected), msg='premultiplied colors')
        for (x, y), color in (((15, 15), SPRITE['flat_color']), ((44, 44), SPRITE['disk_color'] + (255,))):
            cx, cy = b.pixel_center(x, y)
            seen = b.screenshot_array(clip=(cx - 1, cy - 1, 3, 3))[1:2, 1:2]
            self.assertPixelsClose(seen, np.array([[color]], np.uint8), max_abs=2, msg=f'screen at image pixel ({x}, {y})')
        ix, iy = b.client_to_image(*b.pixel_center(10, 20))
        self.assertAlmostEqual(ix, 10.5, places=3)
        self.assertAlmostEqual(iy, 20.5, places=3)

    def test_04_brush_drag_paints_only_along_the_stroke_and_ctrl_z_restores(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.click_selector('[data-tool="brush"]')
        self.assertEqual(b.eval('S.tool'), 'brush')
        b.set_value('#paintColor', '#ff00ff')
        b.set_value('#brushSize', 6)
        b.set_value('#brushOpacity', 100)
        before = b.editor_pixels()
        path = [(9.5, 9.5), (22.5, 9.5), (22.5, 18.5)]   # inside the opaque flat square, 5+ pixels from its edges
        b.drag_image(path, center=False)
        after = b.editor_pixels()
        dist = distance_to_path(64, 64, path)            # a 6 px round brush: radius 3, anti-aliased over about a pixel
        self.assertPixelsClose(after, np.broadcast_to(rgba('#ff00ff'), after.shape), mask=dist <= 2, msg='the stroke core is the brush color')
        self.assertPixelsClose(after, before, mask=dist >= 4, msg='pixels away from the stroke are untouched')
        self.assertGreater((after != before).any(axis=2).sum(), 150)
        self.assertEqual(b.eval("S.ops.filter(o => o.tool === 'brush').map(o => [o.color, o.size])"), [['#ff00ff', 6]])
        b.key('Ctrl+Z')
        b.wait_for("S.ops.length > 0 && S.ops[S.ops.length - 1].tool === 'undo'")   # app.js marks the undo once the old image is drawn back
        self.assertPixelsClose(b.editor_pixels(), before, msg='Ctrl+Z restores every pixel exactly')

    def test_05_pen_strokes_arrive_as_pen_pointer_events_with_their_pressure(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.click_selector('[data-tool="brush"]')
        before = b.editor_pixels()
        b.eval(PEN_LOG)
        try:
            b.drag_image([(6, 8), (24, 8)], pointer='pen', pressures=0.1)
            low = b.eval('__pen.splice(0)')
            b.drag_image([(6, 24), (16, 24), (26, 24)], pointer='pen', pressures=[0.2, 0.5, 0.9], steps_between=4, tilt=(30, -15))
            ramp = b.eval('__pen.splice(0)')
            b.drag_image([(6, 16), (26, 16)], pointer='pen', pressures=[0.3, 0.8], steps_between=9, burst=True)
            burst = b.eval('__pen.splice(0)')
        finally:
            b.eval("for (const t of ['pointerdown', 'pointermove', 'pointerup']) document.getElementById('view').removeEventListener(t, __penLog)")
        for events in (low, ramp):
            self.assertEqual({e['pointerType'] for e in events}, {'pen'})
            self.assertEqual([e['type'] for e in events][:2] + [events[-1]['type']], ['pointermove', 'pointerdown', 'pointerup'])
            self.assertEqual((events[-1]['pressure'], events[-1]['buttons']), (0, 0), 'a lifted pen has no pressure')
        pressed = lambda events: [e['pressure'] for e in events if e['buttons'] == 1]
        for p in pressed(low): self.assertAlmostEqual(p, 0.1, places=4)
        ramp_p = pressed(ramp)
        self.assertAlmostEqual(ramp_p[0], 0.2, places=4)
        self.assertAlmostEqual(ramp_p[5], 0.5, places=4)    # the second point, after 4 steps between
        self.assertAlmostEqual(ramp_p[-1], 0.9, places=4)
        self.assertEqual(ramp_p, sorted(ramp_p))
        self.assertEqual({tuple(e['tilt']) for e in ramp}, {(30, -15)})
        # A burst may arrive merged into fewer pointermoves, but every sample is in getCoalescedEvents(), in order.
        merged = [p for e in burst if e['type'] == 'pointermove' and e['buttons'] == 1 for p in e['merged']]
        self.assertEqual(len(merged), 10)
        for got, want in zip(merged, [0.3 + 0.05 * i for i in range(1, 11)]): self.assertAlmostEqual(got, want, places=4)
        changed = (b.editor_pixels() != before).any(axis=2)
        self.assertTrue(changed[8, 6:25].all() and changed[24, 6:27].all() and changed[16, 6:27].all(), 'every pen stroke painted')

    def test_06_keyboard_shortcuts_switch_tools(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.click_selector('[data-tool="brush"]')
        for key, tool in (('e', 'eraser'), ('h', 'hand'), ('b', 'brush'), ('i', 'eyedropper'), ('E', 'eraser')):   # the editor core's Photoshop keys
            b.key(key)
            self.assertEqual(b.eval('S.tool'), tool, f'after pressing {key}')
            self.assertTrue(b.eval(f"document.querySelector('[data-tool=\"{tool}\"]').classList.contains('active')"))

    def test_07_keys_reach_the_page_with_real_codes(self):
        b = self.browser
        b.eval("const t = document.createElement('input'); t.id = 'keyTarget';"
               "Object.assign(t.style, {position: 'fixed', left: '20px', top: '60px', width: '240px', zIndex: '99999'}); document.body.append(t); t.focus();" + KEY_LOG)
        try:
            # spec -> (key, code, keyCode, ctrl, shift, alt) of its keydown, as a US keyboard on Windows reports it
            expected = {'a': ('a', 'KeyA', 65, 0, 0, 0), 'Z': ('Z', 'KeyZ', 90, 0, 1, 0), '7': ('7', 'Digit7', 55, 0, 0, 0),
                        'Enter': ('Enter', 'Enter', 13, 0, 0, 0), 'Escape': ('Escape', 'Escape', 27, 0, 0, 0),
                        'Delete': ('Delete', 'Delete', 46, 0, 0, 0), 'Backspace': ('Backspace', 'Backspace', 8, 0, 0, 0),
                        'ArrowLeft': ('ArrowLeft', 'ArrowLeft', 37, 0, 0, 0), 'ArrowDown': ('ArrowDown', 'ArrowDown', 40, 0, 0, 0),
                        '[': ('[', 'BracketLeft', 219, 0, 0, 0), ']': (']', 'BracketRight', 221, 0, 0, 0),
                        'Shift+[': ('{', 'BracketLeft', 219, 0, 1, 0), 'Ctrl+Z': ('z', 'KeyZ', 90, 1, 0, 0),
                        'Ctrl+Shift+Z': ('Z', 'KeyZ', 90, 1, 1, 0), 'Alt+Backspace': ('Backspace', 'Backspace', 8, 0, 0, 1),
                        'Ctrl+0': ('0', 'Digit0', 48, 1, 0, 0)}
            for spec, want in expected.items():
                b.key(spec)
                events = [e for e in b.eval('__keys.splice(0)') if e[1] not in ('Control', 'Shift', 'Alt', 'Meta')]
                self.assertEqual([e[0] for e in events], ['keydown', 'keyup'], spec)
                self.assertEqual(tuple(events[0][1:4]) + tuple(int(v) for v in events[0][4:7]), want, spec)
            b.key_down(' ', repeat=2)
            b.key_up(' ')
            space = b.eval('__keys.splice(0)')
            self.assertEqual([(e[0], e[1], e[2], e[3], e[7]) for e in space],
                             [('keydown', ' ', 'Space', 32, False), ('keydown', ' ', 'Space', 32, True), ('keydown', ' ', 'Space', 32, True), ('keyup', ' ', 'Space', 32, False)])
            with b.hold('Shift'):
                b.key('x')
            self.assertEqual([(e[0], e[1], e[5]) for e in b.eval('__keys.splice(0)')],
                             [('keydown', 'Shift', True), ('keydown', 'X', True), ('keyup', 'X', True), ('keyup', 'Shift', False)])
            b.eval("document.getElementById('keyTarget').value = ''")
            b.type_text('Héllo wörld ✓')
            self.assertEqual(b.eval("document.getElementById('keyTarget').value"), 'Héllo wörld ✓')
            b.eval("window.__clicks = []; document.getElementById('keyTarget').addEventListener('click', e => __clicks.push(['click', e.detail]));"
                   "document.getElementById('keyTarget').addEventListener('dblclick', e => __clicks.push(['dblclick', e.detail]))")
            b.double_click(*b.center_of('#keyTarget'))
            self.assertEqual(b.eval('__clicks'), [['click', 1], ['click', 2], ['dblclick', 2]])
        finally:
            b.eval("document.removeEventListener('keydown', __keyLog, true); document.removeEventListener('keyup', __keyLog, true); document.getElementById('keyTarget').remove()")

    def test_08_wheel_zooms_and_right_or_middle_drag_pans(self):
        b = self.browser
        b.select_asset('edit-photo.png')
        zoom = b.eval('S.zoom')
        b.wheel(*b.pixel_center(128, 128), delta_y=-300)
        b.wait_for(f'S.zoom > {zoom} * 1.2')
        b.click_selector('[data-tool="brush"]')     # the right and middle buttons pan with any tool
        px, py = b.eval('[S.px, S.py]')
        x, y = b.pixel_center(128, 128)
        b.drag([(x, y), (x + 40, y + 25)], button='right')
        for got, want in zip(b.eval('[S.px, S.py]'), (px + 40, py + 25)): self.assertAlmostEqual(got, want, delta=0.01)
        self.assertTrue(b.eval("document.getElementById('contextMenu').hidden"), 'a right-drag does not open the menu')
        b.drag([(x, y), (x - 40, y - 25)], button='middle')
        for got, want in zip(b.eval('[S.px, S.py]'), (px, py)): self.assertAlmostEqual(got, want, delta=0.01)

    def test_09_screenshots_and_viewport_size(self):
        b = self.browser
        b.select_asset('edit-photo.png')
        path = b.screenshot('smoke-editor.png')
        with Image.open(path) as im: self.assertEqual(im.size, (1600, 1000))
        self.assertEqual(b.screenshot_array(clip=(10, 20, 30, 40)).shape, (40, 30, 4))
        try:
            b.set_viewport(1280, 800)
            self.assertEqual(b.eval('[innerWidth, innerHeight]'), [1280, 800])
            self.assertEqual(b.screenshot_array().shape, (800, 1280, 4))
            cx, cy = b.pixel_center(40, 40)          # inside the white square: coordinates follow the new layout
            self.assertPixelsClose(b.screenshot_array(clip=(cx - 1, cy - 1, 3, 3))[1:2, 1:2], np.full((1, 1, 4), 255, np.uint8), max_abs=2)
        finally:
            b.set_viewport(*self.viewport)

    def test_10_pixel_reads_are_exact_for_tiny_and_big_images(self):
        b = self.browser
        sent = np.random.default_rng(1).integers(0, 256, (33, 47, 4), dtype=np.uint8)
        assert_pixels_close(b.pixels(b.push_pixels(sent)), sent, msg='ImageData round trip')
        b.select_asset('edit-tiny.png')
        self.assertPixelsClose(b.editor_pixels(), image_array('edit-tiny.png'))
        started = time.monotonic()
        b.select_asset('edit-big.png')
        big = b.editor_pixels()
        self.assertLess(time.monotonic() - started, 10, 'opening and reading 2048 x 2048 pixels is quick')
        self.assertPixelsClose(big, image_array('edit-big.png'))
        self.assertPixelsClose(b.editor_pixels(region=(1000, 900, 8, 4)), image_array('edit-big.png')[900:904, 1000:1008])

    def test_11_comparison_tools(self):
        a = np.zeros((8, 8, 4), np.uint8)
        a[..., 3] = 255
        c = a.copy()
        c[2, 3, 0] = 9
        self.assertEqual(psnr(a, a), float('inf'))
        self.assertGreater(psnr(a, c), 40)
        assert_pixels_close(c, a, max_abs=9)
        with self.assertRaises(AssertionError) as caught: assert_pixels_close(c, a, max_abs=2, diff_path='smoke-diff.png')
        self.assertIn('(x=3, y=2)', str(caught.exception))
        self.assertTrue((OUTPUT / 'smoke-diff.png').is_file())
        with self.assertRaises(RuntimeError): refuse_live('http://127.0.0.1:8753/')

    def test_12_reload_drops_an_unsaved_draft(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.click_selector('[data-tool="brush"]')
        b.drag_image([(8, 8), (20, 8)])
        self.assertTrue(b.eval('S.dirty'))
        b.reload()                                   # app.js asks "leave page?"; the harness accepts
        self.assertEqual(b.eval('[S.asset, S.dirty, S.tool]'), [None, False, 'hand'])
        b.select_asset('edit-sprite.png')
        self.assertPixelsClose(premultiplied(b.editor_pixels()), premultiplied(image_array('edit-sprite.png')))

    def test_13_file_input_imports_an_image(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        art = np.zeros((64, 64, 4), np.uint8)
        art[...] = (10, 200, 30, 255)
        art[20:40, 20:40] = (250, 250, 250, 255)
        b.set_files('#importFile', [write_png(art, 'smoke-import.png')])   # what picking a file after "Import image" does
        b.wait_for("S.ops.length > 0 && S.ops[S.ops.length - 1].tool === 'import'")
        self.assertPixelsClose(b.editor_pixels(), art)

    def test_14_editor_core_hooks_are_used_when_present(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        # A stand-in for the new editor core's window.Ed, built on today's draft canvas, logging every call.
        b.eval("""window.Ed = {calls: [],
          view: {toClient(x, y) { Ed.calls.push('toClient'); const r = document.getElementById('view').getBoundingClientRect(); return {x: r.left + transform.x + x * transform.scale, y: r.top + transform.y + y * transform.scale}; },
                 toImage(x, y) { Ed.calls.push('toImage'); const r = document.getElementById('view').getBoundingClientRect(); return [(x - r.left - transform.x) / transform.scale, (y - r.top - transform.y) / transform.scale]; }},
          test: {pixels(target) { Ed.calls.push('pixels:' + target);
                   if (target === 'selection') { const m = new Uint8Array(64 * 64); for (let y = 4; y < 28; y++) m.fill(255, y * 64 + 4, y * 64 + 28); return {width: 64, height: 64, data: m}; }
                   return target === 0 ? S.draft : S.draft.getContext('2d').getImageData(0, 0, S.draft.width, S.draft.height); },
                 load(w, h, data) { Ed.calls.push('load:' + w + 'x' + h); S.draft.width = w; S.draft.height = h;
                   S.draft.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(__labTest.unb64(data)), w, h), 0, 0); return Promise.resolve(); },
                 ready() { Ed.calls.push('ready'); return new Promise(done => setTimeout(done, 20)); }}}""")
        try:
            plain = b.eval("const r = document.getElementById('view').getBoundingClientRect(); [r.left + transform.x + 3 * transform.scale, r.top + transform.y + 4 * transform.scale]")
            for got, want in zip(b.image_to_client(3, 4), plain): self.assertAlmostEqual(got, want, places=6)
            for got, want in zip(b.client_to_image(*plain), (3, 4)): self.assertAlmostEqual(got, want, places=6)
            self.assertPixelsClose(b.editor_pixels(), b.pixels('S.draft'))
            self.assertPixelsClose(b.editor_pixels(0), b.pixels('S.draft'))
            mask = b.editor_pixels('selection')
            self.assertEqual((mask.shape, int(mask[10, 10]), int(mask[40, 40])), ((64, 64), 255, 0))
            art = np.random.default_rng(5).integers(0, 256, (24, 40, 4), dtype=np.uint8)
            art[..., 3] = 255
            b.load_editor_image(art)
            self.assertPixelsClose(b.editor_pixels(), art)
            b.select_asset('edit-sprite.png')
            self.assertEqual(b.eval('Ed.calls'), ['toClient', 'toImage', 'pixels:composite', 'pixels:0', 'pixels:selection', 'load:40x24', 'pixels:composite', 'ready'])
        finally:
            b.eval('delete window.Ed')
