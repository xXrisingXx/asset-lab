"""Move tool, Free Transform, flips and rotations, Image Rotation, the clipboard and layered PSD files
(src/web/editor/transform.js, transform-resample.js, transform-psd.js), driven with real mouse and keyboard input in
headless Edge and checked against independent numpy / Pillow references.

From the repo root, with PYTHONPATH=src;tests:  <python> -B -X utf8 -m unittest discover -s tests/browser -p "test_transform*.py" -v
"""
import io
import json
import math
import struct
import numpy as np
from PIL import Image
from harness import EditorTestCase, ALT, CTRL, SHIFT, SLOW, output_path


# ----- independent references -----------------------------------------------------------------------------------------------
def pattern(w, h, seed=1, alpha='binary'):
    """Random colours; alpha 0/255 ('binary'), all 255 ('opaque')."""
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    if alpha == 'opaque':
        a[..., 3] = 255
    else:
        a[..., 3] = np.where(rng.random((h, w)) < .75, 255, 0)
        a[a[..., 3] == 0] = 0
    return a


def shifted(a, dx, dy):
    """a moved by (dx, dy); what leaves the canvas is gone, the uncovered area is transparent."""
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    xs0, xs1, ys0, ys1 = max(0, -dx), min(w, w - dx), max(0, -dy), min(h, h - dy)
    if xs1 > xs0 and ys1 > ys0:
        out[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx] = a[ys0:ys1, xs0:xs1]
    return out


def paste_over(base, top, x, y):
    """top (binary alpha) laid over base at (x, y), clipped to the canvas: a floating selection dropped onto its layer."""
    out = base.copy()
    h, w = base.shape[:2]
    th, tw = top.shape[:2]
    for j in range(th):
        for i in range(tw):
            X, Y = x + i, y + j
            if 0 <= X < w and 0 <= Y < h and top[j, i, 3] == 255:
                out[Y, X] = top[j, i]
    return out


def nearest_map(src, M, w, h):
    """Nearest-neighbour inverse mapping of a (H, W, 4) image through the forward 2x3 affine map M (pixel centres)."""
    A = np.array([[M[0], M[1], M[2]], [M[3], M[4], M[5]], [0, 0, 1]], float)
    inv = np.linalg.inv(A)
    y, x = np.mgrid[0:h, 0:w] + .5
    sx = inv[0, 0] * x + inv[0, 1] * y + inv[0, 2]
    sy = inv[1, 0] * x + inv[1, 1] * y + inv[1, 2]
    ix, iy = np.floor(sx + 1e-9).astype(int), np.floor(sy + 1e-9).astype(int)
    ok = (ix >= 0) & (iy >= 0) & (ix < src.shape[1]) & (iy < src.shape[0])
    out = np.zeros((h, w, 4), np.uint8)
    out[ok] = src[iy[ok], ix[ok]]
    # pixels whose sample point lies within 1e-6 of a source pixel edge are ties (either answer is right)
    tie = (np.abs(sx - np.round(sx)) < 1e-6) | (np.abs(sy - np.round(sy)) < 1e-6)
    return out, tie


def rot(deg, cx, cy):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [c, -s, cx - c * cx + s * cy, s, c, cy - s * cx - c * cy]


# ----- an independent PSD reader and writer (Adobe's "Photoshop File Formats Specification", 8-bit RGB) -------------------------


def unpackbits(data, n):
    out, i = bytearray(), 0
    while len(out) < n and i < len(data):
        c = data[i]; i += 1
        if c < 128:
            out += data[i:i + c + 1]; i += c + 1
        elif c > 128:
            out += bytes([data[i]]) * (257 - c); i += 1
    return bytes(out[:n])


def read_psd(raw):
    """-> {'width', 'height', 'layers': [{name, rect (x, y, w, h), opacity, clip, visible, lock_alpha, blend, rgba (H, W, 4)
    in the layer's rect, mask: {rect, default, disabled, data (h, w)} or None, blocks}], 'merged' (H, W, 4)}"""
    p = 0
    def take(n):
        nonlocal p
        v = raw[p:p + n]; p += n
        return v
    u8 = lambda: take(1)[0]
    u16 = lambda: struct.unpack('>H', take(2))[0]
    i16 = lambda: struct.unpack('>h', take(2))[0]
    u32 = lambda: struct.unpack('>I', take(4))[0]
    i32 = lambda: struct.unpack('>i', take(4))[0]
    assert take(4) == b'8BPS' and u16() == 1
    take(6)
    nch, H, W, depth, mode = u16(), u32(), u32(), u16(), u16()
    assert depth == 8 and mode == 3
    take(u32()); take(u32())
    lm_len = u32(); lm_end = p + lm_len
    li_len = u32(); count = abs(i16())
    layers = []
    for _ in range(count):
        top, left, bottom, right = i32(), i32(), i32(), i32()
        chans = [(i16(), u32()) for _ in range(u16())]
        assert take(4) == b'8BIM'
        blend = take(4).decode('latin-1'); opacity = u8(); clip = u8(); flags = u8(); take(1)
        ex_end = u32() + p
        mlen = u32(); mask = None
        if mlen:
            mt, ml, mb, mr = i32(), i32(), i32(), i32(); md = u8(); mf = u8(); take(mlen - 18)
            mask = {'rect': (ml, mt, mr - ml, mb - mt), 'default': md, 'disabled': bool(mf & 2)}
        take(u32())
        nl = u8(); name = take(nl).decode('latin-1'); take((4 - (nl + 1) % 4) % 4)
        blocks = {}
        while p + 12 <= ex_end:
            assert take(4) in (b'8BIM', b'8B64')
            key = take(4).decode('latin-1'); n = u32(); blocks[key] = take(n)
        if 'luni' in blocks:
            k = struct.unpack('>I', blocks['luni'][:4])[0]
            name = blocks['luni'][4:4 + 2 * k].decode('utf-16-be')
        p = ex_end
        layers.append({'name': name, 'rect': (left, top, right - left, bottom - top), 'opacity': opacity, 'clip': clip,
                       'visible': not flags & 2, 'lock_alpha': bool(flags & 1), 'blend': blend, 'mask': mask, 'chans': chans,
                       'blocks': blocks})
    for L in layers:
        planes = {}
        for cid, n in L['chans']:
            d = take(n)
            comp = struct.unpack('>H', d[:2])[0]
            x, y, w, h = L['mask']['rect'] if cid == -2 else L['rect']
            if comp == 0:
                plane = d[2:2 + w * h]
            else:
                assert comp == 1
                counts = struct.unpack(f'>{h}H', d[2:2 + 2 * h])
                q, rows = 2 + 2 * h, []
                for c in counts:
                    rows.append(unpackbits(d[q:q + c], w)); q += c
                plane = b''.join(rows)
            planes[cid] = np.frombuffer(plane, np.uint8).reshape(h, w)
        x, y, w, h = L['rect']
        L['rgba'] = np.stack([planes[0], planes[1], planes[2], planes.get(-1, np.full((h, w), 255, np.uint8))], -1) if w and h else None
        if L['mask']:
            L['mask']['data'] = planes[-2]
    p = lm_end
    comp = u16()
    if comp == 1:
        counts = struct.unpack(f'>{nch * H}H', take(2 * nch * H))
        planes = []
        for c in range(nch):
            planes.append(np.frombuffer(b''.join(unpackbits(take(n), W) for n in counts[c * H:(c + 1) * H]), np.uint8).reshape(H, W))
    else:
        planes = [np.frombuffer(take(W * H), np.uint8).reshape(H, W) for _ in range(nch)]
    return {'width': W, 'height': H, 'layers': layers, 'merged': np.stack(planes[:4], -1)}


def write_psd(W, H, layers):
    """layers: [{name, rgba (H, W, 4) document-sized, rect (x, y, w, h) to store, opacity 0..255, visible, blend key,
    mask: {rect, default, data (h, w)} or None}] -> raw bytes (raw channels)."""
    recs, data = b'', b''
    for L in layers:
        x, y, w, h = L['rect']
        px = np.zeros((h, w, 4), np.uint8)
        for j in range(h):
            for i in range(w):
                if 0 <= y + j < H and 0 <= x + i < W:
                    px[j, i] = L['rgba'][y + j, x + i]
        chans = [(c, b'\0\0' + px[..., k].tobytes()) for c, k in ((0, 0), (1, 1), (2, 2), (-1, 3))]
        mrec = b''
        if L.get('mask'):
            m = L['mask']; mx, my, mw, mh = m['rect']
            chans.append((-2, b'\0\0' + m['data'].astype(np.uint8).tobytes()))
            mrec = struct.pack('>iiiiBBH', my, mx, my + mh, mx + mw, m['default'], 0, 0)
        name = L['name'].encode('latin-1', 'replace')[:255]
        pas = bytes([len(name)]) + name
        pas += b'\0' * ((4 - len(pas) % 4) % 4)
        extra = struct.pack('>I', len(mrec)) + mrec + struct.pack('>I', 0) + pas
        if L.get('section'):          # 1 = a group (its record comes after its layers), 3 = the group's end (before them)
            extra += b'8BIMlsct' + struct.pack('>II', 4, L['section'])
        rec = struct.pack('>iiiiH', y, x, y + h, x + w, len(chans))
        rec += b''.join(struct.pack('>hI', c, len(d)) for c, d in chans)
        rec += b'8BIM' + L.get('blend', 'norm').encode() + bytes([L.get('opacity', 255), 0, 0 if L.get('visible', True) else 2, 0])
        rec += struct.pack('>I', len(extra)) + extra
        recs += rec
        data += b''.join(d for _, d in chans)
    li = struct.pack('>h', -len(layers)) + recs + data
    if len(li) % 2: li += b'\0'
    lm = struct.pack('>I', len(li)) + li + struct.pack('>I', 0)
    head = b'8BPS' + struct.pack('>H', 1) + b'\0' * 6 + struct.pack('>HIIHH', 4, H, W, 8, 3)
    merged = struct.pack('>H', 0) + b'\0' * (4 * W * H)
    return head + struct.pack('>I', 0) + struct.pack('>I', 0) + struct.pack('>I', len(lm)) + lm + merged

class TransformTests(EditorTestCase):
    viewport = (1600, 1000)

    # ----- helpers ---------------------------------------------------------------------------------------------------------
    def fresh(self, name='edit-sprite.png', image=None):
        b = self.browser
        if image is not None:
            b.load_editor_image(image)
            b.eval("Ed.view.fit(); true")
        else:
            b.select_asset(name)
        b.eval("document.querySelectorAll('dialog[open]').forEach(d => d.close()); document.getElementById('toast').hidden = true; true")
        b.settle(2)
        return b

    def tool(self, tid, **opts):
        b = self.browser
        b.eval(f"""const t = Ed.tools.get({json.dumps(tid)}); const d = {{}};
            for (const o of t.options || []) if (o.key && o.type !== 'button' && o.default !== undefined) d[o.key] = o.default;
            Ed.setTool({json.dumps(tid)}); Ed.setToolOptions({json.dumps(tid)}, Object.assign(d, {json.dumps(opts)})); true""")
        b.settle()

    def labels(self):
        return self.browser.eval('Ed.history.list().filter(s => !s.undone).map(s => s.label)')

    def toast(self):
        return self.browser.eval("document.getElementById('toast').hidden ? '' : document.getElementById('toast').textContent")

    def layer(self, i):
        return self.browser.editor_pixels(i)

    def add_layer(self, arr, name='Layer'):
        b = self.browser
        expr = b.push_pixels(arr)
        return b.eval(f"(() => {{ const img = {expr}; const c = Ed.util.canvas(img.width, img.height); Ed.util.write(c, img); return Ed.layers.add({{name: {json.dumps(name)}, canvas: c}}).id; }})()")

    def select_rect(self, x, y, w, h):
        self.browser.eval(f"Ed.select.rect({x}, {y}, {w}, {h}); true")

    def undo(self):
        b = self.browser
        b.key('Ctrl+Z')
        b.wait_for("S.ops.at(-1)?.tool === 'undo'")
        b.settle(2)

    def drag(self, pts, **kw):
        self.browser.drag_image(pts, center=False, **kw)
        self.browser.settle(2)

    # =========================================================================================================================
    # MOVE TOOL
    # =========================================================================================================================
    def test_01_move_drags_the_layer_exactly_one_state_and_undo(self):
        img = pattern(48, 40, 1)
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.drag([(20.5, 20.5), (23, 21.5), (25.5, 23.5)])
        self.assertPixelsClose(self.layer(0), shifted(before, 5, 3))
        self.assertEqual(self.labels(), ['Open', 'Move'])
        self.undo()
        self.assertPixelsClose(self.layer(0), before)
        # a second drag after the undo starts from the restored pixels
        self.drag([(20.5, 20.5), (14.5, 18.5)])
        self.assertPixelsClose(self.layer(0), shifted(before, -6, -2))
        self.assertNoBrowserErrors()

    def test_02_move_selected_pixels_leaves_a_transparent_hole_and_moves_the_selection(self):
        img = pattern(48, 40, 2)
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.select_rect(8, 6, 12, 10)
        self.drag([(12.5, 10.5), (20.5, 15.5)])          # +8, +5
        exp = before.copy()
        exp[6:16, 8:20] = 0
        exp = paste_over(exp, before[6:16, 8:20], 16, 11)
        self.assertPixelsClose(self.layer(0), exp)
        sel = b.editor_pixels('selection')[..., 3]
        ref = np.zeros((40, 48), np.uint8); ref[11:21, 16:28] = 255
        self.assertTrue((sel == ref).all(), 'the selection moved with the pixels')
        self.assertEqual(self.labels()[-1], 'Move')
        self.assertEqual(len(self.labels()), 3, 'one state for the move (plus the marquee)')
        self.undo()
        self.assertPixelsClose(self.layer(0), before)
        sel = b.editor_pixels('selection')[..., 3]
        ref = np.zeros((40, 48), np.uint8); ref[6:16, 8:20] = 255
        self.assertTrue((sel == ref).all(), 'undo restores the selection')
        self.assertNoBrowserErrors()

    def test_03_alt_drag_copies_the_selection_and_the_layer(self):
        img = pattern(48, 40, 3)
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.select_rect(4, 4, 10, 8)
        with b.hold('Alt'):
            self.drag([(6.5, 6.5), (26.5, 20.5)])          # +20, +14
        exp = before.copy()
        exp = paste_over(exp, before[4:12, 4:14], 24, 18)
        self.assertPixelsClose(self.layer(0), exp)
        self.assertEqual(b.eval('Ed.layers.count()'), 1)
        # without a selection Alt-drag makes a moved copy of the layer
        b.eval('Ed.select.none(); true')
        n = len(self.labels())
        with b.hold('Alt'):
            self.drag([(30.5, 30.5), (27.5, 31.5)])        # -3, +1
        self.assertEqual(b.eval('Ed.layers.count()'), 2)
        self.assertPixelsClose(self.layer(0), exp)
        self.assertPixelsClose(self.layer(1), shifted(exp, -3, 1))
        self.assertEqual(len(self.labels()), n + 1)
        self.undo()
        self.assertEqual(b.eval('Ed.layers.count()'), 1)
        self.assertNoBrowserErrors()

    def test_04_arrow_keys_nudge_1_and_10_pixels(self):
        img = pattern(48, 40, 4)
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        b.mouse_move(*b.pixel_center(20, 20))
        b.key('ArrowRight'); b.key('ArrowRight'); b.key('ArrowDown')
        b.key('ArrowLeft', SHIFT)
        b.settle(2)
        self.assertPixelsClose(self.layer(0), shifted(before, -8, 1))
        self.assertEqual(self.labels()[1:], ['Nudge'] * 4)
        # nudges continue from the original pixels: what went past the edge comes back
        b.key('ArrowRight', SHIFT); b.settle(2)
        self.assertPixelsClose(self.layer(0), shifted(before, 2, 1))
        for _ in range(5):
            self.undo()
        self.assertPixelsClose(self.layer(0), before)
        # with a selection the selected pixels move
        self.select_rect(10, 10, 6, 6)
        b.key('ArrowUp'); b.settle(2)
        exp = before.copy(); exp[10:16, 10:16] = 0; exp = paste_over(exp, before[10:16, 10:16], 10, 9)
        self.assertPixelsClose(self.layer(0), exp)
        self.assertNoBrowserErrors()

    def test_05_locks_and_hidden_layers_refuse(self):
        img = pattern(32, 32, 5)
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        for locks in ({'position': True}, {'all': True}):
            b.eval(f"Ed.layers.set(0, {{locks: {json.dumps(locks)}}}); true")
            n = len(self.labels())
            self.drag([(10.5, 10.5), (15.5, 12.5)])
            b.key('ArrowLeft'); b.settle(2)
            self.assertPixelsClose(self.layer(0), before)
            self.assertEqual(len(self.labels()), n)
            self.assertIn('locked', self.toast())
            b.eval("Ed.layers.set(0, {locks: {position: false, all: false}}); document.getElementById('toast').hidden = true; true")
        b.eval("Ed.layers.set(0, {visible: false}); true")
        self.drag([(10.5, 10.5), (15.5, 12.5)])
        self.assertIn('hidden', self.toast())
        b.eval("Ed.layers.set(0, {visible: true}); true")
        self.assertPixelsClose(self.layer(0), before)
        self.assertNoBrowserErrors()

    def test_06_the_layer_mask_moves_with_the_layer(self):
        img = pattern(40, 40, 6)
        b = self.fresh(image=img)
        self.tool('move')
        b.eval("Ed.select.rect(5, 5, 20, 12); Ed.layers.addMask(0, 'selection'); Ed.select.none(); true")
        mask0 = b.editor_pixels('mask')[..., 3]
        before = self.layer(0)
        self.drag([(20.5, 20.5), (24.5, 27.5)])            # +4, +7
        self.assertPixelsClose(self.layer(0), shifted(before, 4, 7))
        m = b.editor_pixels('mask')[..., 3]
        ref = np.zeros_like(mask0); ref[12:24, 9:29] = 255   # the uncovered strip takes the mask's outside value (0)
        self.assertTrue((m == ref).all(), 'the linked mask moved too')
        self.undo()
        # the mask targeted: the linked pair still moves together
        b.eval("Ed.setTarget('mask'); true")
        self.drag([(20.5, 20.5), (24.5, 27.5)])
        self.assertPixelsClose(self.layer(0), shifted(before, 4, 7))
        self.assertTrue((b.editor_pixels('mask')[..., 3] == ref).all(), 'the targeted mask moved with its layer')
        self.undo()
        # a "hide selection" mask (white outside): the uncovered strip stays white
        b.eval("Ed.layers.removeMask(0); Ed.select.rect(5, 5, 20, 12); Ed.layers.addMask(0, 'hide-selection'); Ed.select.none(); Ed.setTarget('pixels'); true")
        self.drag([(20.5, 20.5), (17.5, 22.5)])            # -3, +2
        ref = np.full_like(mask0, 255); ref[7:19, 2:22] = 0
        self.assertTrue((b.editor_pixels('mask')[..., 3] == ref).all(), 'a white-outside mask keeps white where it uncovers')
        self.assertNoBrowserErrors()

    def test_07_auto_select_and_ctrl_click_pick_the_layer_under_the_pointer(self):
        base = np.zeros((40, 40, 4), np.uint8); base[:, :] = (200, 30, 30, 255)
        b = self.fresh(image=base)
        top = np.zeros((40, 40, 4), np.uint8); top[20:30, 20:30] = (10, 200, 10, 255)
        self.add_layer(top, 'Top')
        b.eval("Ed.layers.setActive(0); true")
        self.tool('move', autoSelect=True)
        self.drag([(25.5, 25.5), (27.5, 25.5)])
        self.assertEqual(b.eval("Ed.layers.active().name"), 'Top')
        self.assertPixelsClose(self.layer(1), shifted(top, 2, 0))
        self.drag([(5.5, 5.5), (6.5, 5.5)])
        self.assertEqual(b.eval("Ed.layers.index(Ed.layers.active())"), 0)
        # Auto-Select off: Ctrl-click does it once
        self.tool('move', autoSelect=False)
        self.drag([(26.5, 25.5), (26.5, 26.5)], modifiers=CTRL)
        self.assertEqual(b.eval("Ed.layers.active().name"), 'Top')
        self.assertNoBrowserErrors()

    def test_08_align_buttons_to_canvas_and_selection(self):
        img = np.zeros((40, 50, 4), np.uint8); img[10:20, 12:30] = (40, 90, 200, 255)
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        b.click_selector('#edOptions [data-align="left"]'); b.settle(2)
        self.assertPixelsClose(self.layer(0), shifted(before, -12, 0))
        b.click_selector('#edOptions [data-align="bottom"]'); b.settle(2)
        self.assertPixelsClose(self.layer(0), shifted(before, -12, 20))
        self.assertEqual(self.labels()[-2:], ['Align', 'Align'])
        self.select_rect(20, 0, 30, 40)
        b.click_selector('#edOptions [data-align="hcenter"]'); b.settle(2)
        self.assertPixelsClose(self.layer(0), shifted(before, -12 + 26, 20))
        b.click_selector('#edOptions [data-align="vcenter"]'); b.settle(2)
        self.assertPixelsClose(self.layer(0), shifted(before, 14, 5))
        self.assertNoBrowserErrors()

    def test_09_move_options_bar_fits_at_1366(self):
        b = self.fresh()
        self.tool('move')
        self.assertBarFits('move')

    def assertBarFits(self, name):
        b = self.browser
        try:
            for w, h in ((1366, 768), (1920, 1080)):
                b.set_viewport(w, h); b.settle(4)
                b.screenshot(f'transform-bar-{name}-{w}.png')
                spill = b.eval("""[...document.querySelectorAll('.ed-optionsbar *')].filter(e => {
                    if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden') return false; const r = e.getBoundingClientRect(), p = e.closest('.ed-optionsbar').getBoundingClientRect();
                    return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); }).map(e => (e.id || e.className || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 20))""")
                self.assertEqual(spill, [], f'{name} options spill out of the bar at {w}x{h}')
                clipped = b.eval("""[...document.querySelectorAll('.ed-optionsbar label, .ed-optionsbar button')]
                    .filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => (e.id || e.className) + ':' + e.textContent.trim().slice(0, 20))""")
                self.assertEqual(clipped, [], f'cut-off text in the {name} options at {w}x{h}')
        finally:
            b.set_viewport(*self.viewport); b.settle(2)

    # =========================================================================================================================
    # FREE TRANSFORM
    # =========================================================================================================================
    def open_transform(self, interp='nearest'):
        b = self.browser
        b.eval(f"Ed.setToolOptions('transform', {{interp: {json.dumps(interp)}, link: true}}); true")
        b.mouse_move(*b.pixel_center(1, 1))
        b.key('Ctrl+T')
        b.wait_for("Ed.transform.active && Ed.tool === 'transform'")
        b.settle(2)
        return b.eval("Ed.transform.active")

    def type_field(self, key, value):
        b = self.browser
        sel = f'#edOptions .ed-field[data-key="{key}"] input'
        b.click_selector(sel)
        b.key('Ctrl+A')
        b.type_text(str(value))
        b.key('Enter')          # commits the field (and leaves it), as in Photoshop
        b.settle(2)

    def apply_transform(self, label='Free Transform'):
        b = self.browser
        n = len(self.labels())
        b.mouse_move(*b.pixel_center(1, 1))
        b.key('Enter')
        try:
            b.wait_for(f"!Ed.transform.active && Ed.history.index === {n}", timeout=20)
        except TimeoutError:
            raise AssertionError(f"the transform was not applied: labels {self.labels()}, active {b.eval('Ed.transform.active')}, "
                                 f"tool {b.eval('Ed.tool')}, focus {b.eval('document.activeElement.tagName')}, toast {self.toast()!r}, errors {b.error_report()}")
        b.settle(2)
        self.assertEqual(self.labels()[-1], label)

    def handle(self, which):
        """Client position of a box handle: 0..3 corners (TL, TR, BR, BL), 'mid0'..'mid3' side middles."""
        b = self.browser
        q = b.eval("Ed.transform.active.quad")
        if isinstance(which, int):
            return b.image_to_client(*q[which])
        i = int(which[3:])
        a, c = q[i], q[(i + 1) % 4]
        return b.image_to_client((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)

    def test_10_free_transform_options_bar_fits_and_strip_has_no_extra_button(self):
        b = self.fresh(image=pattern(32, 32, 10, 'opaque'))
        self.tool('move')
        self.open_transform()
        self.assertFalse(b.eval("!!document.querySelector('#edTools .ed-tool[data-tool=transform]')?.getClientRects().length"))
        self.assertBarFits('transform')
        b.key('Escape')
        b.wait_for("!Ed.transform.active && Ed.tool === 'move'")
        self.assertNoBrowserErrors()

    def test_11_numeric_scale_nearest_neighbour_is_exact_and_one_state(self):
        img = np.zeros((40, 40, 4), np.uint8); img[12:28, 10:26] = pattern(16, 16, 11, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        st = self.open_transform('nearest')
        self.assertEqual(st['rect'], {'x': 10, 'y': 12, 'w': 16, 'h': 16})
        self.type_field('w', 200)                      # linked: H follows
        self.assertEqual(b.eval("[Ed.toolOptions('transform').w, Ed.toolOptions('transform').h]"), [200, 200])
        self.apply_transform()
        exp, tie = nearest_map(before, [2, 0, -18, 0, 2, -20], 40, 40)   # about the centre (18, 20)
        self.assertFalse(tie.any())
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        self.assertPixelsClose(self.layer(0), before)
        # unlinked: W only
        self.open_transform('nearest')
        b.click_selector('#edOptions .ed-tf-link')
        self.type_field('w', 50)
        self.apply_transform()
        exp, tie = nearest_map(before, [.5, 0, 9, 0, 1, 0], 40, 40)
        self.assertPixelsClose(self.layer(0), exp, mask=~tie)
        b.eval("Ed.setToolOptions('transform', {link: true}); true")
        self.assertNoBrowserErrors()

    def test_12_numeric_rotation_nearest_is_exact_for_90_and_matches_reference_for_30(self):
        img = np.zeros((48, 48, 4), np.uint8); img[14:30, 10:34] = pattern(24, 16, 12, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.open_transform('nearest')
        self.type_field('angle', 90)
        self.apply_transform()
        exp, tie = nearest_map(before, rot(90, 22, 22), 48, 48)
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        self.open_transform('nearest')
        self.type_field('angle', 30)
        self.assertAlmostEqual(b.eval("Ed.toolOptions('transform').angle"), 30)
        self.apply_transform()
        exp, tie = nearest_map(before, rot(30, 22, 22), 48, 48)
        self.assertPixelsClose(self.layer(0), exp, mask=~tie)
        self.assertNoBrowserErrors()

    def test_13_reference_point_and_xy_fields(self):
        img = np.zeros((48, 48, 4), np.uint8); img[10:20, 10:30] = pattern(20, 10, 13, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.open_transform('nearest')
        b.click_selector('#edOptions .ed-tf-ref .ed-tf-refcell:nth-child(1)')     # top left
        self.assertEqual(b.eval("[Ed.toolOptions('transform').x, Ed.toolOptions('transform').y]"), [10, 10])
        self.type_field('angle', 90)                  # about (10, 10)
        self.type_field('x', 30)                      # then move the reference point to x = 30
        self.apply_transform()
        M = rot(90, 10, 10); M[2] += 20
        exp, tie = nearest_map(before, M, 48, 48)
        self.assertPixelsClose(self.layer(0), exp)
        self.assertNoBrowserErrors()

    def test_14_escape_cancels_and_enter_applies(self):
        img = np.zeros((32, 32, 4), np.uint8); img[8:24, 8:24] = pattern(16, 16, 14, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.open_transform('nearest')
        self.type_field('w', 50)
        b.key('Escape')
        b.wait_for("!Ed.transform.active")
        b.settle(3)
        self.assertPixelsClose(self.layer(0), before)
        self.assertEqual(self.labels(), ['Open'])
        self.assertEqual(b.eval('Ed.tool'), 'move')
        self.assertNoBrowserErrors()

    def test_15_drag_corner_scales_proportionally_shift_freely(self):
        img = np.zeros((64, 64, 4), np.uint8); img[16:32, 16:40] = pattern(24, 16, 15, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.open_transform('nearest')
        x0, y0 = self.handle(2)                         # bottom right
        b.drag([(x0, y0), b.image_to_client(52, 40)])
        M = b.eval("Ed.transform.active.M")
        # proportional about the opposite corner (16, 16): one scale factor, the top-left corner stays
        self.assertAlmostEqual(M[0], M[4], places=6)
        self.assertAlmostEqual(M[1], 0, places=6); self.assertAlmostEqual(M[3], 0, places=6)
        self.assertAlmostEqual(16 * M[0] + M[2], 16, places=4); self.assertAlmostEqual(16 * M[4] + M[5], 16, places=4)
        self.assertTrue(1.3 < M[0] < 1.6, M[0])
        self.apply_transform()
        exp, tie = nearest_map(before, M[:6], 64, 64)
        self.assertPixelsClose(self.layer(0), exp, mask=~tie)
        # Shift: free (the corner follows the pointer)
        self.undo()
        self.open_transform('nearest')
        x0, y0 = self.handle(2)
        b.drag([(x0, y0), b.image_to_client(56, 24)], modifiers=SHIFT)
        M = b.eval("Ed.transform.active.M")
        self.assertAlmostEqual(40 * M[0] + M[2], 56, delta=0.3)
        self.assertAlmostEqual(32 * M[4] + M[5], 24, delta=0.3)
        self.assertGreater(abs(M[0] - M[4]), 0.5)
        b.key('Escape')
        self.assertNoBrowserErrors()

    def test_16_drag_outside_rotates_shift_snaps_to_15_degrees(self):
        img = np.zeros((64, 64, 4), np.uint8); img[24:40, 16:48] = pattern(32, 16, 16, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.open_transform('nearest')
        # centre (32, 32); start straight right of the box, end about 40 degrees clockwise
        a = math.radians(40)
        start = b.image_to_client(56, 32)
        end = b.image_to_client(32 + 24 * math.cos(a), 32 + 24 * math.sin(a))
        b.drag([start, end], modifiers=SHIFT)
        self.assertAlmostEqual(b.eval("Ed.toolOptions('transform').angle"), 45, places=4)
        M = b.eval("Ed.transform.active.M")
        np.testing.assert_allclose(M[:6], rot(45, 32, 32), atol=1e-9)
        self.apply_transform()
        exp, tie = nearest_map(before, rot(45, 32, 32), 64, 64)
        self.assertPixelsClose(self.layer(0), exp, mask=~tie)
        self.assertNoBrowserErrors()

    def test_17_inside_drag_moves_and_arrows_nudge_inside_the_transform(self):
        img = np.zeros((40, 40, 4), np.uint8); img[10:20, 10:20] = pattern(10, 10, 17, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.open_transform('nearest')
        self.drag([(12.5, 17.5), (17.5, 20.5)])          # away from the reference point in the middle
        b.key('ArrowLeft'); b.key('ArrowDown', SHIFT)
        self.apply_transform()
        self.assertPixelsClose(self.layer(0), shifted(before, 4, 13))
        self.assertEqual(self.labels(), ['Open', 'Free Transform'])
        self.assertNoBrowserErrors()

    def test_18_bilinear_and_bicubic_match_pillow(self):
        y, x = np.mgrid[0:64, 0:64]
        img = np.zeros((64, 64, 4), np.uint8)
        img[..., 0] = (128 + 100 * np.sin(x / 5.0) * np.cos(y / 7.0)).astype(np.uint8)
        img[..., 1] = (x * 3 + y) % 256
        img[..., 2] = (255 - 3 * y).clip(0, 255)
        img[..., 3] = 255
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        # 50 % bilinear: a reduction, filtered like Pillow's resize
        self.open_transform('bilinear')
        self.type_field('w', 50)
        self.apply_transform()
        ref = np.asarray(Image.fromarray(before).resize((32, 32), Image.BILINEAR))
        got = self.layer(0)
        self.assertPixelsClose(got[17:47, 17:47], ref[1:31, 1:31], max_abs=1)
        self.assertTrue((got[:16] == 0).all() and (got[48:] == 0).all())
        # 200 % bicubic of a 16 x 16 middle: Pillow's bicubic (a = -0.5)
        small = np.zeros((64, 64, 4), np.uint8); small[24:40, 24:40] = before[24:40, 24:40]
        self.fresh(image=small); self.tool('move')
        self.open_transform('bicubic')
        self.type_field('w', 200)
        self.apply_transform()
        ref = np.asarray(Image.fromarray(before[24:40, 24:40]).resize((32, 32), Image.BICUBIC))
        got = self.layer(0)
        self.assertPixelsClose(got[20:44, 20:44], ref[4:28, 4:28], max_abs=1)
        # bilinear rotation against an independent bilinear sampler (opaque inside)
        self.fresh(image=img); self.tool('move')
        self.open_transform('bilinear')
        self.type_field('angle', 20)
        self.apply_transform()
        got = self.layer(0).astype(float)
        inv = np.linalg.inv(np.array(rot(20, 32, 32) + [0, 0, 1]).reshape(3, 3))
        yy, xx = np.mgrid[0:64, 0:64] + .5
        sx = inv[0, 0] * xx + inv[0, 1] * yy + inv[0, 2] - .5
        sy = inv[1, 0] * xx + inv[1, 1] * yy + inv[1, 2] - .5
        x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
        fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
        ok = (x0 >= 0) & (y0 >= 0) & (x0 < 63) & (y0 < 63)
        src = img.astype(float)
        def at(ix, iy): return src[np.clip(iy, 0, 63), np.clip(ix, 0, 63)]
        ref = (at(x0, y0) * (1 - fx) * (1 - fy) + at(x0 + 1, y0) * fx * (1 - fy)
               + at(x0, y0 + 1) * (1 - fx) * fy + at(x0 + 1, y0 + 1) * fx * fy)
        self.assertLessEqual(np.abs(got[ok] - ref[ok]).max(), 1.01)
        self.assertNoBrowserErrors()

    def test_19_transform_selected_pixels_and_the_selection(self):
        img = pattern(32, 32, 19, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.select_rect(8, 8, 8, 8)
        st = self.open_transform('nearest')
        self.assertEqual(st['rect'], {'x': 8, 'y': 8, 'w': 8, 'h': 8})
        self.type_field('w', 200)
        self.apply_transform()
        exp = before.copy(); exp[8:16, 8:16] = 0
        exp[4:20, 4:20] = np.repeat(np.repeat(before[8:16, 8:16], 2, 0), 2, 1)
        self.assertPixelsClose(self.layer(0), exp)
        sel = b.editor_pixels('selection')[..., 3]
        ref = np.zeros((32, 32), np.uint8); ref[4:20, 4:20] = 255
        self.assertTrue((sel == ref).all(), 'the selection is transformed with its pixels')
        self.assertEqual(self.labels(), ['Open', 'Rectangular Marquee', 'Free Transform'])
        self.undo()
        self.assertPixelsClose(self.layer(0), before)
        sel = b.editor_pixels('selection')[..., 3]
        ref = np.zeros((32, 32), np.uint8); ref[8:16, 8:16] = 255
        self.assertTrue((sel == ref).all())
        self.assertNoBrowserErrors()

    def test_20_linked_mask_transforms_with_the_layer_and_locks_refuse(self):
        img = np.zeros((40, 40, 4), np.uint8); img[10:30, 6:34] = pattern(28, 20, 20, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        b.eval("Ed.select.rect(8, 12, 10, 6); Ed.layers.addMask(0, 'selection'); Ed.select.none(); Ed.setTarget('pixels'); true")
        before, mask0 = self.layer(0), b.editor_pixels('mask')
        self.open_transform('nearest')
        self.type_field('angle', 90)
        self.apply_transform()
        exp, _ = nearest_map(before, rot(90, 20, 20), 40, 40)
        self.assertPixelsClose(self.layer(0), exp)
        mexp, _ = nearest_map(mask0, rot(90, 20, 20), 40, 40)
        self.assertTrue((b.editor_pixels('mask')[..., 3] == mexp[..., 3]).all(), 'the linked mask turned with the layer')
        self.undo()
        for locks, word in (({'position': True}, 'locked'), ({'pixels': True}, 'locked')):
            b.eval(f"Ed.layers.set(0, {{locks: {json.dumps(locks)}}}); document.getElementById('toast').hidden = true; true")
            b.mouse_move(*b.pixel_center(1, 1)); b.key('Ctrl+T'); b.settle(2)
            self.assertIsNone(b.eval('Ed.transform.active'))
            self.assertIn(word, self.toast())
            b.eval("Ed.layers.set(0, {locks: {position: false, pixels: false}}); true")
        self.assertNoBrowserErrors()

    def test_21_distort_skew_and_perspective_match_a_numpy_homography(self):
        img = np.zeros((64, 64, 4), np.uint8); img[16:48, 16:48] = pattern(32, 32, 21, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        cases = [('Edit/Transform/Perspective', 1, (54, 16), 0),
                 ('Edit/Transform/Distort', 2, (52, 56), 0),
                 ('Edit/Transform/Skew', 'mid1', (48, 26), 0),
                 (None, 3, (10, 52), CTRL)]                      # Ctrl-drag a corner in Free Transform distorts
        for path, h, to, mods in cases:
            if path:
                b.eval(f"Ed.setToolOptions('transform', {{interp: 'nearest'}}); Ed.menu.run({json.dumps(path)}); true")
                b.wait_for("Ed.transform.active"); b.settle(2)
            else:
                self.open_transform('nearest')
            x0, y0 = self.handle(h)
            b.drag([(x0, y0), b.image_to_client(*to)], modifiers=mods)
            q = b.eval("Ed.transform.active.quad")
            self.assertNotEqual(q, [[16, 16], [48, 16], [48, 48], [16, 48]], path)
            self.apply_transform({'Edit/Transform/Perspective': 'Perspective', 'Edit/Transform/Distort': 'Distort',
                                  'Edit/Transform/Skew': 'Skew', None: 'Free Transform'}[path])
            # homography from the square's corners to the quad, solved here
            src = [(16, 16), (48, 16), (48, 48), (16, 48)]
            A, B = [], []
            for (x, y), (u, v) in zip(src, q):
                A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.append(u)
                A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); B.append(v)
            Hm = np.append(np.linalg.solve(np.array(A, float), np.array(B, float)), 1).reshape(3, 3)
            inv = np.linalg.inv(Hm)
            yy, xx = np.mgrid[0:64, 0:64] + .5
            w = inv[2, 0] * xx + inv[2, 1] * yy + inv[2, 2]
            sx = (inv[0, 0] * xx + inv[0, 1] * yy + inv[0, 2]) / w
            sy = (inv[1, 0] * xx + inv[1, 1] * yy + inv[1, 2]) / w
            ix, iy = np.floor(sx).astype(int), np.floor(sy).astype(int)
            ok = (ix >= 16) & (iy >= 16) & (ix < 48) & (iy < 48)
            exp = np.zeros_like(before); exp[ok] = before[iy[ok], ix[ok]]
            near = (np.abs(sx - np.round(sx)) < 1e-4) | (np.abs(sy - np.round(sy)) < 1e-4)
            got = self.layer(0)
            bad = (np.abs(got.astype(int) - exp).max(axis=2) > 0) & ~near
            self.assertEqual(int(bad.sum()), 0, f'{path}: {int(bad.sum())} pixels differ from the homography')
            self.undo()
        self.assertNoBrowserErrors()

    def test_22_edit_transform_flips_and_quarter_turns_are_exact(self):
        img = np.zeros((40, 48, 4), np.uint8); img[14:20, 10:30] = pattern(20, 6, 22, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        block = before[14:20, 10:30]
        def run(path, label):
            b.eval(f"Ed.menu.run({json.dumps(path)}); true"); b.settle(3)
            self.assertEqual(self.labels()[-1], label)
        run('Edit/Transform/Flip Horizontal', 'Flip Horizontal')
        exp = before.copy(); exp[14:20, 10:30] = block[:, ::-1]
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        run('Edit/Transform/Flip Vertical', 'Flip Vertical')
        exp = before.copy(); exp[14:20, 10:30] = block[::-1]
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        run('Edit/Transform/Rotate 180°', 'Rotate 180°')
        exp = before.copy(); exp[14:20, 10:30] = block[::-1, ::-1]
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        # box 20 x 6 centred on (20, 17): a quarter turn is 6 x 20 at (17, 7)
        run('Edit/Transform/Rotate 90° Clockwise', 'Rotate 90° Clockwise')
        exp = np.zeros_like(before); exp[7:27, 17:23] = np.rot90(block, -1)
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        run('Edit/Transform/Rotate 90° Counter Clockwise', 'Rotate 90° Counter Clockwise')
        exp = np.zeros_like(before); exp[7:27, 17:23] = np.rot90(block, 1)
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        self.assertPixelsClose(self.layer(0), before)
        # with a selection: only the selected part flips, inside its own box
        self.select_rect(10, 14, 8, 6)
        run('Edit/Transform/Flip Horizontal', 'Flip Horizontal')
        exp = before.copy(); exp[14:20, 10:18] = before[14:20, 10:18][:, ::-1]
        self.assertPixelsClose(self.layer(0), exp)
        self.assertNoBrowserErrors()

    def test_23_image_rotation_turns_every_layer_mask_and_the_selection(self):
        base = pattern(40, 40, 23, 'opaque')
        b = self.fresh(image=base)
        top = pattern(40, 40, 24)
        self.add_layer(top, 'Top')
        b.eval("Ed.select.rect(4, 6, 10, 8); Ed.layers.addMask(1, 'selection'); Ed.select.rect(20, 2, 6, 12); Ed.setTarget('pixels'); true")
        l0, l1, m1 = self.layer(0), self.layer(1), b.editor_pixels('mask')[..., 3]
        s0 = b.editor_pixels('selection')[..., 3]
        for path, fn in (('Image/Image Rotation/Flip Canvas Horizontal', lambda a: a[:, ::-1]),
                         ('Image/Image Rotation/Flip Canvas Vertical', lambda a: a[::-1]),
                         ('Image/Image Rotation/180°', lambda a: a[::-1, ::-1]),
                         ('Image/Image Rotation/90° Clockwise', lambda a: np.rot90(a, -1)),
                         ('Image/Image Rotation/90° Counter Clockwise', lambda a: np.rot90(a, 1))):
            b.eval(f"Ed.menu.run({json.dumps(path)}); true"); b.settle(3)
            self.assertPixelsClose(self.layer(0), fn(l0), msg=path)
            self.assertPixelsClose(self.layer(1), fn(l1), msg=path)
            self.assertTrue((b.editor_pixels('mask')[..., 3] == fn(m1)).all(), path)
            self.assertTrue((b.editor_pixels('selection')[..., 3] == fn(s0)).all(), path)
            self.undo()
            self.assertPixelsClose(self.layer(1), l1)
        # a quarter turn of a non-square image would change its size: not offered
        b = self.fresh(image=pattern(48, 32, 25))
        enabled = dict((m['path'], m['enabled']) for m in b.eval("Ed.menu.list()"))
        self.assertFalse(enabled['Image/Image Rotation/90° Clockwise'])
        self.assertTrue(enabled['Image/Image Rotation/180°'])
        self.assertNotIn('Image/Image Size...', enabled)
        self.assertNotIn('Image/Canvas Size...', enabled)
        self.assertNoBrowserErrors()

    def test_24_transform_again_repeats_the_last_transform(self):
        img = np.zeros((44, 44, 4), np.uint8); img[14:30, 10:34] = pattern(24, 16, 26, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        self.open_transform('nearest')
        self.type_field('angle', 90)
        self.apply_transform()
        b.mouse_move(*b.pixel_center(1, 1))
        b.key('Ctrl+Shift+T'); b.settle(3)
        self.assertEqual(self.labels()[-1], 'Transform Again')
        exp, _ = nearest_map(before, rot(180, 22, 22), 44, 44)
        self.assertPixelsClose(self.layer(0), exp)
        self.assertNoBrowserErrors()

    # =========================================================================================================================
    # CLIPBOARD
    # =========================================================================================================================
    def wait_layers(self, n):
        self.browser.wait_for(f"Ed.layers.count() === {n}", timeout=10)
        self.browser.settle(2)

    def test_25_copy_paste_cut_copy_merged_in_place_and_into(self):
        img = pattern(40, 40, 27, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        b.mouse_move(*b.pixel_center(20, 20))
        self.select_rect(5, 5, 10, 8)
        b.key('Ctrl+C'); b.settle(2)
        self.assertEqual(b.eval("(({x, y, w, h}) => [x, y, w, h])(Ed.transform.clipboard)"), [5, 5, 10, 8])
        b.key('Ctrl+V'); self.wait_layers(2)
        self.assertEqual(self.labels()[-1], 'Paste')
        exp = np.zeros_like(before); exp[5:13, 5:15] = before[5:13, 5:15]
        self.assertPixelsClose(self.layer(1), exp, msg='pasted in place in the same image')
        # Cut: the bottom layer's selected pixels go
        b.eval("Ed.layers.setActive(0); true")
        b.key('Ctrl+X'); b.settle(3)
        self.assertEqual(self.labels()[-1], 'Cut Pixels')
        cut = before.copy(); cut[5:13, 5:15] = 0
        self.assertPixelsClose(self.layer(0), cut)
        # Copy Merged: what both layers show
        self.select_rect(0, 0, 40, 40)
        b.key('Ctrl+Shift+C'); b.settle(2)
        self.assertTrue(b.eval("Ed.transform.clipboard.merged"))
        b.eval("Ed.select.none(); true")
        b.key('Ctrl+V'); self.wait_layers(3)
        self.assertEqual(b.eval("Ed.layers.index(Ed.layers.active())"), 1, "pasted above the active layer")
        self.assertPixelsClose(self.layer(1), before)
        # another (smaller) image: Paste centres, Paste in Place keeps the position
        b = self.fresh(image=img)
        self.select_rect(5, 5, 10, 8); b.key('Ctrl+C'); b.settle(2)
        b = self.fresh(image=pattern(30, 30, 28, 'opaque'))
        b.mouse_move(*b.pixel_center(15, 15))
        b.key('Ctrl+V'); self.wait_layers(2)
        exp = np.zeros((30, 30, 4), np.uint8); exp[11:19, 10:20] = before[5:13, 5:15]
        self.assertPixelsClose(self.layer(1), exp, msg='centred in a different-sized image')
        b.key('Ctrl+Shift+V'); self.wait_layers(3)
        exp = np.zeros((30, 30, 4), np.uint8); exp[5:13, 5:15] = before[5:13, 5:15]
        self.assertPixelsClose(self.layer(2), exp, msg='Paste in Place')
        # Paste Into: a layer masked by the selection
        self.select_rect(2, 2, 6, 6)
        b.key('Ctrl+Alt+Shift+V'); self.wait_layers(4)
        self.assertEqual(self.labels()[-1], 'Paste Into')
        exp = np.zeros((30, 30, 4), np.uint8); exp[1:9, 0:10] = before[5:13, 5:15]
        self.assertPixelsClose(self.layer(3), exp)
        m = b.editor_pixels('mask')[..., 3]
        ref = np.zeros((30, 30), np.uint8); ref[2:8, 2:8] = 255
        self.assertTrue((m == ref).all(), 'the mask is the selection')
        b.eval("Ed.menu.run('Edit/Paste Special/Paste Outside'); true"); self.wait_layers(5)
        self.assertEqual(self.labels()[-1], 'Paste Outside')
        self.assertTrue((b.editor_pixels('mask')[..., 3] == 255 - ref).all())
        self.assertNoBrowserErrors()

    def test_26_a_picture_bigger_than_the_canvas_is_fitted_and_opens_in_free_transform(self):
        b = self.fresh(image=pattern(64, 64, 29, 'opaque'))
        y, x = np.mgrid[0:128, 0:128]
        big = np.zeros((128, 128, 4), np.uint8)
        big[..., 0] = (x * 2) % 256; big[..., 1] = (y * 2) % 256; big[..., 2] = 128 + (40 * np.sin(x / 9)).astype(int); big[..., 3] = 255
        expr = b.push_pixels(big)
        b.eval(f"(async () => {{ const img = {expr}; const c = new OffscreenCanvas(img.width, img.height); c.getContext('2d').putImageData(img, 0, 0);"
               f" const blob = await c.convertToBlob({{type: 'image/png'}}); await Ed.menu.run('Edit/Paste', {{blob}}); return true; }})()")
        b.wait_for("Ed.transform.active && Ed.transform.active.kind === 'paste'")
        self.assertEqual(self.labels()[-1], 'Paste')
        self.assertIn('bigger', self.toast())
        b.eval("Ed.setToolOptions('transform', {interp: 'bicubic'}); true")
        self.apply_transform()
        ref = np.asarray(Image.fromarray(big).resize((64, 64), Image.BICUBIC))
        self.assertPixelsClose(self.layer(1)[2:62, 2:62], ref[2:62, 2:62], max_abs=2)
        self.assertNoBrowserErrors()

    def test_27_system_clipboard_round_trip(self):
        b = self.fresh(image=pattern(40, 40, 30, 'opaque'))
        b.mouse_move(*b.pixel_center(20, 20))
        self.select_rect(4, 4, 6, 6)
        b.key('Ctrl+C')
        state = b.wait_for("Ed.transform.clipboard.system !== 'pending' && Ed.transform.clipboard.system")
        print(f'\n[clipboard] system clipboard write: {state}')
        if state != 'ok':
            self.skipTest('the system clipboard cannot be written in this browser without a permission')
        # another app copies a picture: Ctrl+V pastes that one
        other = pattern(12, 9, 31, 'opaque')
        expr = b.push_pixels(other)
        b.eval(f"(async () => {{ const img = {expr}; const c = new OffscreenCanvas(img.width, img.height); c.getContext('2d').putImageData(img, 0, 0);"
               f" await navigator.clipboard.write([new ClipboardItem({{'image/png': c.convertToBlob({{type: 'image/png'}})}})]); return true; }})()")
        b.eval("Ed.select.none(); true")
        b.key('Ctrl+V'); self.wait_layers(2)
        exp = np.zeros((40, 40, 4), np.uint8); exp[16:25, 14:26] = other
        self.assertPixelsClose(self.layer(1), exp)
        self.assertNoBrowserErrors()

    # =========================================================================================================================
    # LAYERED PSD
    # =========================================================================================================================
    def psd_doc(self):
        b = self.fresh(image=pattern(40, 30, 40, 'opaque'))
        hidden = np.zeros((30, 40, 4), np.uint8); hidden[2:12, 20:38] = pattern(18, 10, 41)
        top = np.zeros((30, 40, 4), np.uint8); top[5:20, 7:33] = pattern(26, 15, 42)
        self.add_layer(hidden, 'Hidden')
        b.eval("Ed.layers.set(1, {visible: false, clip: true, locks: {alpha: true}}); true")
        self.add_layer(top, 'Café ✓')
        b.eval("Ed.layers.set(2, {opacity: .6, fill: .4, blend: 'multiply'}); Ed.select.rect(2, 3, 10, 10); Ed.layers.addMask(2, 'selection'); Ed.select.none(); Ed.setTarget('pixels'); true")
        return b

    def export_psd(self):
        import base64
        b = self.browser
        data = b.eval("(async () => Ed.util.b64(await Ed.transform.exportPSD({download: false})))()")
        return base64.b64decode(data)

    def test_28_export_layered_psd_reads_back_with_independent_readers(self):
        b = self.psd_doc()
        layers = [self.layer(i) for i in range(3)]
        mask = b.editor_pixels('mask')[..., 3]
        composite = b.editor_pixels()
        raw = self.export_psd()
        (output_path('transform-export.psd')).write_bytes(raw)
        self.assertIn('Exported', self.toast())
        psd = read_psd(raw)
        self.assertEqual((psd['width'], psd['height']), (40, 30))
        self.assertEqual([L['name'] for L in psd['layers']], ['Layer 0', 'Hidden', 'Café ✓'])
        self.assertEqual([L['visible'] for L in psd['layers']], [True, False, True])
        self.assertEqual([L['blend'] for L in psd['layers']], ['norm', 'norm', 'mul '])
        self.assertEqual([L['opacity'] for L in psd['layers']], [255, 255, 153])
        self.assertEqual([L['lock_alpha'] for L in psd['layers']], [False, True, False])
        self.assertEqual([L['clip'] for L in psd['layers']], [0, 1, 0])
        self.assertEqual([L['blocks'].get('iOpa', bytes([255]))[0] for L in psd['layers']], [255, 255, 102])
        for L, want in zip(psd['layers'], layers):
            x, y, w, h = L['rect']
            doc = np.zeros((30, 40, 4), np.uint8); doc[y:y + h, x:x + w] = L['rgba']
            vis = want[..., 3] > 0
            self.assertTrue((doc[..., 3] == want[..., 3]).all(), L['name'])
            self.assertTrue((doc[vis] == want[vis]).all(), L['name'])
            self.assertTrue(((want[..., 3] > 0) <= np.pad(np.ones((h, w), bool), ((y, 30 - y - h), (x, 40 - x - w)))).all())
        self.assertEqual(psd['layers'][1]['rect'], (20, 2, 18, 10), 'layer bounds are the pixels, not the canvas')
        m = psd['layers'][2]['mask']
        self.assertEqual((m['rect'], m['default'], m['disabled']), ((2, 3, 10, 10), 0, False))
        self.assertTrue((m['data'] == mask[3:13, 2:12]).all())
        # the merged picture: the composite matted with white
        a = composite[..., 3:4].astype(float)
        matte = np.round((composite[..., :3] * a + 255 * (255 - a)) / 255)
        self.assertLessEqual(np.abs(psd['merged'][..., :3].astype(int) - matte).max(), 0)
        self.assertTrue((psd['merged'][..., 3] == composite[..., 3]).all())
        # Pillow reads the merged image and the layers without a mask (it skips masked ones) the same. Pillow 12 keeps the
        # layer tile offsets relative to the layer block, so they are moved to file positions here.
        im = Image.open(io.BytesIO(raw))
        self.assertTrue((np.asarray(im.convert('RGBA')) == psd['merged']).all())
        for i in (2, 1):          # (frame 1 is the merged image until another frame was loaded)
            im.seek(i)
            if getattr(im, "_layers_position", None):
                im.tile = [t._replace(offset=t.offset + im._layers_position) for t in im.tile]
            L = psd['layers'][i - 1]
            self.assertEqual(im.layers[i - 1][0], L['name'])
            x, y, w, h = L['rect']
            self.assertTrue((np.asarray(im.convert('RGBA'))[y:y + h, x:x + w] == L['rgba']).all(), f'Pillow layer {i}')
        self.assertNoBrowserErrors()

    def import_psd(self, raw, how='replace'):
        import base64
        b = self.browser
        b.eval(f"(async () => {{ const f = new File([Ed.util.unb64({json.dumps(base64.b64encode(raw).decode())})], 'test.psd'); return await Ed.transform.importPSD(f, {{how: {json.dumps(how)}}}); }})()")
        b.settle(3)

    def test_29_import_psd_from_an_independent_writer_and_round_trip(self):
        b = self.fresh(image=pattern(40, 30, 43, 'opaque'))
        back = pattern(40, 30, 44, 'opaque')
        sticker = np.zeros((30, 40, 4), np.uint8); sticker[18:30, 0:15] = pattern(15, 12, 45)
        ghost = np.zeros((30, 40, 4), np.uint8); ghost[0:5, 30:40] = (9, 99, 199, 255)
        mdata = np.zeros((5, 10), np.uint8) + 255
        raw = write_psd(40, 30, [
            {'name': 'Back', 'rgba': back, 'rect': (0, 0, 40, 30)},
            {'name': 'Sticker', 'rgba': sticker, 'rect': (-5, 18, 20, 15), 'opacity': 153, 'blend': 'scrn',
             'mask': {'rect': (0, 22, 10, 5), 'default': 0, 'data': mdata}},
            {'name': 'Ghost', 'rgba': ghost, 'rect': (30, 0, 10, 5), 'visible': False}])
        self.import_psd(raw)
        self.assertEqual(b.eval("Ed.layers.list().map(L => [L.name, L.visible, Math.round(L.opacity * 255), L.blend])"),
                         [['Back', True, 255, 'normal'], ['Sticker', True, 153, 'screen'], ['Ghost', False, 255, 'normal']])
        self.assertEqual(self.labels(), ['Open', 'Import PSD'])
        self.assertPixelsClose(self.layer(0), back)
        self.assertPixelsClose(self.layer(1), sticker)
        self.assertPixelsClose(self.layer(2), ghost)
        b.eval("Ed.layers.setActive(1); true")
        m = b.editor_pixels('mask')[..., 3]
        ref = np.zeros((30, 40), np.uint8); ref[22:27, 0:10] = 255
        self.assertTrue((m == ref).all(), 'the layer mask')
        self.undo()
        self.assertEqual(b.eval("Ed.layers.count()"), 1)
        # a hidden group at 50 %: flattened, its layer hidden and at half its opacity; added on top
        none = np.zeros((30, 40, 4), np.uint8)
        raw = write_psd(40, 30, [
            {'name': '</Layer group>', 'rgba': none, 'rect': (0, 0, 0, 0), 'section': 3},
            {'name': 'Inside', 'rgba': ghost, 'rect': (30, 0, 10, 5), 'opacity': 204},
            {'name': 'Group 1', 'rgba': none, 'rect': (0, 0, 0, 0), 'section': 1, 'opacity': 128, 'visible': False}])
        self.import_psd(raw, 'add')
        self.assertEqual(b.eval("Ed.layers.list().map(L => [L.name, L.visible, Math.round(L.opacity * 1000)])"),
                         [['Layer 0', True, 1000], ['Inside', False, round(204 / 255 * 128 / 255 * 1000)]])
        self.assertIn('simplified', self.toast())
        self.assertPixelsClose(self.layer(1), ghost)
        # our own export reads back the same
        b = self.psd_doc()
        layers = [self.layer(i) for i in range(3)]
        props = b.eval("Ed.layers.list().map(L => [L.name, L.visible, Math.round(L.opacity * 255), Math.round(L.fill * 255), L.blend, L.clip, L.locks.alpha, !!L.mask])")
        mask = b.editor_pixels('mask')
        raw = self.export_psd()
        self.import_psd(raw)
        self.assertEqual(b.eval("Ed.layers.list().map(L => [L.name, L.visible, Math.round(L.opacity * 255), Math.round(L.fill * 255), L.blend, L.clip, L.locks.alpha, !!L.mask])"), props)
        for i in range(3):
            got, want = self.layer(i), layers[i]
            self.assertTrue((got[..., 3] == want[..., 3]).all() and (got[want[..., 3] > 0] == want[want[..., 3] > 0]).all(), i)
        b.eval("Ed.layers.setActive(2); true")
        self.assertTrue((b.editor_pixels('mask')[..., 3] == mask[..., 3]).all())
        self.assertNoBrowserErrors()

    def test_30_import_psd_through_the_file_menu(self):
        b = self.fresh(image=pattern(40, 30, 46, 'opaque'))
        art = pattern(40, 30, 47)
        path = output_path('transform-import.psd')
        path.write_bytes(write_psd(40, 30, [{'name': 'Art', 'rgba': art, 'rect': (0, 0, 40, 30)}]))
        b.eval("Ed.menu.run('File/Import PSD as Layers...'); true")
        b.set_files('#edPsdFile', [str(path)])
        try:
            b.wait_for("!!document.querySelector('dialog[open][data-ed-dialog=\"Import PSD\"]')")
        except TimeoutError:
            raise AssertionError(f"no Import PSD dialog: toast {self.toast()!r}, dialogs {b.eval('[...document.querySelectorAll(\"dialog\")].map(d => [d.open, d.dataset.edDialog])')}, errors {b.error_report()}")
        b.click_selector('dialog[open] button[value=ok]')
        b.wait_for("Ed.history.list().length === 2")
        b.settle(2)
        self.assertEqual(b.eval("Ed.layers.list().map(L => L.name)"), ['Art'])
        self.assertPixelsClose(self.layer(0), art)
        self.assertNoBrowserErrors()

    # =========================================================================================================================
    # MORE MOVE / TRANSFORM CASES
    # =========================================================================================================================
    def test_31_move_shift_constrains_alt_arrow_copies_lock_transparency_and_mask_target(self):
        img = pattern(48, 40, 50)
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        # Shift keeps the drag horizontal, vertical or diagonal
        self.drag([(20.5, 20.5), (27.5, 21.5), (29.5, 22.5)], modifiers=SHIFT)     # mostly right: +9, 0
        self.assertPixelsClose(self.layer(0), shifted(before, 9, 0))
        self.undo()
        # Alt+arrow copies the selected pixels once, then nudges the copy
        self.select_rect(6, 6, 8, 8)
        b.mouse_move(*b.pixel_center(30, 30))
        b.key('ArrowRight', ALT); b.settle(2)
        b.key('ArrowRight'); b.settle(2)
        exp = paste_over(before, before[6:14, 6:14], 8, 6)
        self.assertPixelsClose(self.layer(0), exp)
        sel = b.editor_pixels('selection')[..., 3]
        self.assertTrue(sel[6:14, 8:16].min() == 255 and sel.sum() == 64 * 255)
        while len(self.labels()) > 1:
            self.undo()
        # lock transparency: the moved pixels land only where the layer had pixels; the hole keeps its alpha
        b.eval("Ed.layers.set(0, {locks: {alpha: true}}); true")
        self.select_rect(6, 6, 8, 8)
        self.drag([(9.5, 9.5), (19.5, 9.5)])                 # +10, 0
        got = self.layer(0)
        self.assertTrue((got[..., 3] == before[..., 3]).all(), 'lock transparency keeps the alpha')
        b.eval("Ed.layers.set(0, {locks: {alpha: false}}); Ed.select.none(); true")
        while len(self.labels()) > 1:
            self.undo()
        # mask target with a selection: the mask values move, the hole takes the background colour's gray (white)
        b.eval("Ed.layers.addMask(0, 'reveal'); Ed.select.rect(0, 0, 10, 40); Ed.layers.addMask && true; true")
        b.eval("Ed.setColor('fg', '#000000'); Ed.setColor('bg', '#ffffff'); true")
        b.eval("(() => { const L = Ed.layers.get(0); Ed.history.record('t', tx => { tx.touch(L, 'mask', {x: 0, y: 0, w: 48, h: 40}); const g = L.mask.getContext('2d'); g.clearRect(0, 0, 10, 40); }); Ed.select.rect(0, 0, 10, 10); Ed.setTarget('mask'); return true; })()")
        m0 = b.editor_pixels('mask')[..., 3]
        self.drag([(4.5, 4.5), (24.5, 4.5)])                 # +20, 0
        m = b.editor_pixels('mask')[..., 3]
        ref = m0.copy(); ref[0:10, 0:10] = 255; ref[0:10, 20:30] = 0
        self.assertTrue((m == ref).all(), 'mask values moved, the hole is white')
        self.assertPixelsClose(self.layer(0), before)
        self.assertNoBrowserErrors()

    def test_32_transform_undo_inside_tool_return_modes_and_selection_outline(self):
        img = np.zeros((40, 40, 4), np.uint8); img[10:20, 10:30] = pattern(20, 10, 51, 'opaque')
        b = self.fresh(image=img)
        before = self.layer(0)
        self.tool('brush')
        st = self.open_transform('nearest')
        self.type_field('w', 50)
        self.type_field('angle', 90)
        b.mouse_move(*b.pixel_center(1, 1))
        b.key('Ctrl+Z'); b.settle(2)                         # steps back inside the transform: the angle goes
        self.assertAlmostEqual(b.eval("Ed.toolOptions('transform').angle"), 0)
        self.assertAlmostEqual(b.eval("Ed.toolOptions('transform').w"), 50)
        self.assertEqual(self.labels(), ['Open'])
        self.apply_transform()
        self.assertEqual(b.eval('Ed.tool'), 'brush', 'back to the tool that was active')
        exp, tie = nearest_map(before, [.5, 0, 10, 0, .5, 7.5], 40, 40)
        self.assertPixelsClose(self.layer(0), exp, mask=~tie)
        self.undo()
        # Rotate mode: dragging inside turns; Scale mode: dragging outside does nothing
        b.eval("Ed.setToolOptions('transform', {interp: 'nearest'}); Ed.menu.run('Edit/Transform/Rotate'); true")
        b.wait_for("Ed.transform.active && Ed.transform.active.mode === 'rotate'")
        self.assertTrue(dict((m['path'], m['checked']) for m in b.eval('Ed.menu.list()'))['Edit/Transform/Rotate'])
        self.drag([(26.5, 15), (20, 21.5)])                 # from right of the centre (20, 15) to below it: +90
        self.assertAlmostEqual(b.eval("Ed.toolOptions('transform').angle"), 90, delta=2)
        b.key('Escape'); b.wait_for("!Ed.transform.active")
        b.eval("Ed.menu.run('Edit/Transform/Scale'); true")
        b.wait_for("Ed.transform.active && Ed.transform.active.mode === 'scale'")
        self.drag([(2.5, 35.5), (6.5, 37.5)])
        np.testing.assert_allclose(b.eval("Ed.transform.active.M"), [1, 0, 0, 0, 1, 0, 0, 0, 1])
        b.key('Escape'); b.wait_for("!Ed.transform.active")
        # Select > Transform Selection changes the outline only
        self.select_rect(10, 10, 8, 6)
        b.eval("Ed.setToolOptions('transform', {interp: 'nearest'}); Ed.menu.run('Select/Transform Selection'); true")
        b.wait_for("Ed.transform.active && Ed.transform.active.kind === 'outline'")
        self.type_field('w', 200)
        self.apply_transform('Transform Selection')
        sel = b.editor_pixels('selection')[..., 3]
        ref = np.zeros((40, 40), np.uint8); ref[7:19, 6:22] = 255
        self.assertTrue((sel == ref).all(), 'the outline doubled in width and height about its centre')
        self.assertPixelsClose(self.layer(0), before)
        self.assertNoBrowserErrors()

    def test_33_free_transform_on_a_2048_sheet(self):
        import time
        b = self.fresh('edit-big.png')
        self.tool('move')
        before = self.layer(0)
        t0 = time.time()
        self.open_transform('bilinear')
        opened = time.time() - t0
        self.type_field('w', 50)
        t0 = time.time()
        self.apply_transform()
        applied = time.time() - t0
        print(f'\n[transform 2048] open {opened:.2f} s, apply 50 % bilinear {applied:.2f} s')
        self.assertLess(applied, 8 * SLOW)
        ref = np.asarray(Image.fromarray(before).resize((1024, 1024), Image.BILINEAR))
        self.assertPixelsClose(self.layer(0)[514:1534, 514:1534], ref[2:1022, 2:1022], max_abs=1)
        self.undo()
        self.assertPixelsClose(self.layer(0), before)
        self.assertNoBrowserErrors()

    def test_34_dropped_pictures_become_layers(self):
        b = self.fresh(image=pattern(40, 30, 52, 'opaque'))
        pic = pattern(10, 6, 53, 'opaque')
        expr = b.push_pixels(pic)
        b.eval(f"""(async () => {{ const img = {expr}; const c = new OffscreenCanvas(img.width, img.height); c.getContext('2d').putImageData(img, 0, 0);
            const file = new File([await c.convertToBlob({{type: 'image/png'}})], 'sticker.png', {{type: 'image/png'}});
            const dt = new DataTransfer(); dt.items.add(file); const v = document.getElementById('viewport'), r = v.getBoundingClientRect();
            const at = {{bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}};
            v.dispatchEvent(new DragEvent('dragover', at)); v.dispatchEvent(new DragEvent('drop', at)); return true; }})()""")
        self.wait_layers(2)
        self.assertEqual(b.eval("[Ed.layers.active().name, Ed.history.list().at(-1).label]"), ['sticker', 'Place'])
        exp = np.zeros((30, 40, 4), np.uint8); exp[12:18, 15:25] = pic
        self.assertPixelsClose(self.layer(1), exp)
        self.assertNoBrowserErrors()

    def test_35_buttons_double_click_alt_scale_flip_in_session_and_align_menu(self):
        img = np.zeros((40, 40, 4), np.uint8); img[10:20, 10:30] = pattern(20, 10, 60, 'opaque')
        b = self.fresh(image=img)
        self.tool('move')
        before = self.layer(0)
        # the Cancel and Apply buttons
        self.open_transform('nearest')
        self.assertFalse(dict((m['path'], m['enabled']) for m in b.eval('Ed.menu.list()'))['Edit/Free Transform'])
        self.type_field('w', 50)
        b.click_selector('#edOptions .ed-tf-cancel')
        b.wait_for("!Ed.transform.active"); b.settle(2)
        self.assertPixelsClose(self.layer(0), before)
        self.open_transform('nearest')
        self.type_field('h', 200)
        b.click_selector('#edOptions .ed-tf-commit')
        b.wait_for("!Ed.transform.active && Ed.history.index === 1"); b.settle(2)
        exp, tie = nearest_map(before, [2, 0, -20, 0, 2, -15], 40, 40)
        self.assertPixelsClose(self.layer(0), exp, mask=~tie)
        self.undo()
        # Alt-drag a corner scales about the reference point (the centre), then a double-click inside applies
        self.open_transform('nearest')
        x0, y0 = self.handle(2)
        b.drag([(x0, y0), b.image_to_client(35, 22.5)], modifiers=ALT)
        M = b.eval("Ed.transform.active.M")
        self.assertAlmostEqual(20 * M[0] + M[2], 20, places=4)          # the centre (20, 15) stays
        self.assertAlmostEqual(15 * M[4] + M[5], 15, places=4)
        self.assertAlmostEqual(M[0], 1.5, places=4)
        b.double_click(*b.image_to_client(16.5, 16.5))
        b.wait_for("!Ed.transform.active && Ed.history.index === 1"); b.settle(2)
        exp, tie = nearest_map(before, M[:6], 40, 40)
        self.assertPixelsClose(self.layer(0), exp, mask=~tie)
        self.undo()
        # Edit > Transform > Flip Horizontal inside an open transform flips the box about its reference point
        self.open_transform('nearest')
        b.eval("Ed.menu.run('Edit/Transform/Flip Horizontal'); true"); b.settle(2)
        self.assertTrue(b.eval("!!Ed.transform.active"))
        self.apply_transform()
        exp = before.copy(); exp[10:20, 10:30] = before[10:20, 10:30][:, ::-1]
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        # switching tools applies the open transform, as Photoshop asks to
        self.open_transform('nearest')
        self.type_field('angle', 180)
        b.eval("Ed.setTool('brush'); true"); b.settle(3)
        self.assertIsNone(b.eval('Ed.transform.active'))
        self.assertEqual(self.labels()[-1], 'Free Transform')
        exp = before.copy(); exp[10:20, 10:30] = before[10:20, 10:30][::-1, ::-1]
        self.assertPixelsClose(self.layer(0), exp)
        self.undo()
        # Layer > Align from the menu, and a hidden layer refuses Ctrl+T
        b.eval("Ed.menu.run('Layer/Align/Right Edges'); true"); b.settle(2)
        self.assertPixelsClose(self.layer(0), shifted(before, 10, 0))
        b.eval("Ed.layers.set(0, {visible: false}); document.getElementById('toast').hidden = true; true")
        b.mouse_move(*b.pixel_center(1, 1)); b.key('Ctrl+T'); b.settle(2)
        self.assertIsNone(b.eval('Ed.transform.active'))
        self.assertIn('hidden', self.toast())
        self.assertNoBrowserErrors()
