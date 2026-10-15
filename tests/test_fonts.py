import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import numpy as np
import fonts
import lab_core
import theme
from lab_core import Lab, file_hash
from test_theme import Base, stale_game

FNT = '''<?xml version="1.0"?>
<font>
  <info face="Test Rounded" size="-40" bold="0" italic="0" charset="" unicode="1" stretchH="100" smooth="1" aa="1" padding="2,2,2,2" spacing="2,2" outline="0"/>
  <common lineHeight="48" base="38" scaleW="128" scaleH="128" pages="1" packed="0" alphaChnl="0" redChnl="4" greenChnl="4" blueChnl="4"/>
  <pages>
    <page id="0" file="nicktext_0.png" />
  </pages>
  <chars count="3">
    <char id="32" x="0" y="0" width="0" height="0" xoffset="0" yoffset="0" xadvance="10" page="0" chnl="15" />
    <char id="65" x="4" y="4" width="24" height="34" xoffset="-2" yoffset="6" xadvance="22" page="0" chnl="15" />
    <char id="66" x="40" y="4" width="22" height="34" xoffset="-2" yoffset="6" xadvance="20" page="0" chnl="15" />
  </chars>
  <kernings count="1">
    <kerning first="65" second="66" amount="-1" />
  </kernings>
</font>
'''
RED, GREEN, BLUE = '#ff0000', '#00ff00', '#0000ff'
WINDOWS = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
# Typefaces that ship with Windows 10 and 11: every look must end on one of them, so it works on any player's PC.
SHIPPED = {'Arial', 'Bahnschrift', 'Segoe UI', 'Segoe Print', 'Segoe Script', 'Ink Free', 'Impact', 'Georgia', 'Consolas', 'Cascadia Code', 'Comic Sans MS',
           'Gabriola', 'Franklin Gothic Medium', 'Palatino Linotype', 'Trebuchet MS', 'Verdana', 'MV Boli', 'Sitka', 'Courier New'}


def font_game(root):
    game = stale_game(root)
    page = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
    page.paste((255, 255, 255, 255), (6, 6, 26, 36)); page.paste((255, 255, 255, 255), (42, 6, 60, 36))
    page.save(game / 'assets' / 'nicktext_0.png')
    (game / 'assets' / 'nicktext.fnt').write_text(FNT, encoding='utf-8')
    return game


def page():
    im = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
    im.paste((255, 255, 255, 255), (6, 6, 26, 36)); im.paste((255, 255, 255, 255), (42, 6, 60, 36))
    return im


def solid(img):
    """The glyph's pixels and the box where the letter is fully opaque."""
    px = np.asarray(img).astype(int)
    ys, xs = np.where(px[..., 3] == 255)
    return px, ys.min(), ys.max(), xs.min(), xs.max()


def chain_names(chain): return [part.strip().split('|')[0] for part in chain.split(',')]


class FontRenderTests(unittest.TestCase):
    def setUp(self): self.page = page()

    def glyph(self, faces=(), cid=65, **style):
        return fonts.Renderer(FNT, self.page, fonts.clean_style(style, list(faces)), list(faces)).glyph(cid)

    def test_the_game_font_is_rebuilt_with_effects_and_its_metrics(self):
        style = fonts.clean_style(dict(fill='#000000', outline='#ffffff', outline_width=2, glow='#ffffff', glow_size=6), [])
        sheet, text, info = fonts.build(FNT, self.page, style, [])
        parsed = fonts.parse_fnt(text)
        self.assertEqual(sorted(parsed['chars']), [32, 65, 66])                       # every character is still there
        self.assertEqual(parsed['common']['base'], '38'); self.assertEqual(parsed['chars'][65]['xadvance'], 22)
        self.assertEqual(parsed['kernings'], [(65, 66, -1)])                           # the game's kerning stays for its own letters
        a = parsed['chars'][65]
        self.assertGreater(a['width'], 24)                                             # room for the outline and glow
        self.assertLessEqual(a['x'] + a['width'], sheet.width); self.assertLessEqual(a['y'] + a['height'], sheet.height)
        glyph = np.asarray(sheet.crop((a['x'], a['y'], a['x'] + a['width'], a['y'] + a['height'])))
        middle = glyph[glyph.shape[0] // 2, glyph.shape[1] // 2]
        self.assertTrue(middle[3] == 255 and middle[:3].max() < 20)                    # black letters
        self.assertGreater(glyph[..., :3][glyph[..., 3] > 30].max(), 200)              # with a light edge around them
        self.assertEqual(glyph[0, 0, 3], 0)                                            # the glow fades before the box edge
        self.assertEqual(info['missing'], 0)

    def test_a_system_typeface_and_the_preview(self):
        faces = fonts.system_fonts()
        face = fonts.find_font(faces, 'Arial|Bold') or (faces[0] if faces else None)
        if not face: self.skipTest('no fonts installed')
        style = fonts.clean_style(dict(font=face['id'], caps=True, spacing=4, size=1.2), faces)
        sheet, text, info = fonts.build(FNT, self.page, style, faces)
        self.assertEqual(info['face'], face['family'])
        parsed = fonts.parse_fnt(text)
        self.assertEqual(parsed['common']['lineHeight'], str(round(48 * 1.2)))
        preview = fonts.preview(FNT, self.page, style, faces, 'AB A')
        self.assertEqual(preview.height % 3, 0)                                        # three backgrounds
        self.assertGreater(preview.width, 40)

    def test_style_values_are_clamped_and_colors_checked(self):
        s = fonts.clean_style(dict(size=9, glow='red', outline='#ABCDEF', caps='yes', italic=-3, stretch=7, fill_opacity=-1, angle=-90), [])
        self.assertEqual((s['size'], s['glow'], s['outline'], s['caps'], s['italic'], s['stretch'], s['fill_opacity'], s['angle']), (2.0, '', '#abcdef', False, -0.4, 2.0, 0.0, 270.0))
        self.assertEqual(fonts.clean_style(dict(angle=float('inf'), stretch=float('nan')), [])['angle'], 90.0)
        grown = fonts.round_grow(Image.new('L', (11, 11), 0).point(lambda v: v), 3)
        self.assertEqual(grown.getbbox(), None)

    def test_gradient_stops_are_checked_sorted_and_limited(self):
        s = fonts.clean_style(dict(stops=[[0.9, BLUE], [2, '#FF0000'], [0.5, 'red'], ['x', GREEN], [0.1, GREEN], 'junk']), [])
        self.assertEqual(s['stops'], [[0.1, GREEN], [0.9, BLUE], [1.0, RED]])         # sorted, clamped, bad ones dropped
        self.assertEqual(fonts.clean_style(dict(stops=[[0, RED]]), [])['stops'], [])  # one color is no gradient
        many = fonts.clean_style(dict(stops=[[i / 11, RED] for i in range(12)]), [])['stops']
        self.assertEqual(len(many), fonts.MAX_STOPS)
        hard = fonts.clean_style(dict(stops=[[0.5, BLUE], [0, RED], [0.5, RED], [1, BLUE]]), [])['stops']
        self.assertEqual(hard, [[0.0, RED], [0.5, BLUE], [0.5, RED], [1.0, BLUE]])     # two stops at one spot keep their order

    def test_styles_saved_with_fill2_still_load_as_two_stops(self):
        old = fonts.clean_style(dict(fill='#FFF3B0', fill2='#C98A12', outline='#4a2a00', outline_width=3), [])
        self.assertEqual((old['stops'], old['angle']), ([[0.0, '#fff3b0'], [1.0, '#c98a12']], 90.0))   # it ran top to bottom
        self.assertNotIn('fill2', old)
        new = fonts.clean_style(dict(fill='#fff3b0', stops=[[0, '#fff3b0'], [1, '#c98a12']], outline='#4a2a00', outline_width=3), [])
        a, b = (np.asarray(self.glyph(**style)[0]) for style in (old, new))
        self.assertTrue(np.array_equal(a, b))                                          # and renders exactly like the new form

    def test_multi_stop_ramp_and_hard_stops(self):
        t = np.array([0, 0.25, 0.5, 0.75, 1], np.float32)
        colors = (fonts.ramp([[0, RED], [0.5, GREEN], [1, BLUE]], t, 0.001) * 255).round().astype(int).tolist()
        self.assertEqual(colors, [[255, 0, 0], [128, 128, 0], [0, 255, 0], [0, 128, 128], [0, 0, 255]])
        uneven = fonts.ramp([[0, RED], [0.8, GREEN], [1, BLUE]], np.array([0.4], np.float32), 0.001)[0]
        self.assertAlmostEqual(float(uneven[0]), 0.5, places=2)                       # positions are honoured, not spaced evenly
        hard = fonts.ramp([[0, RED], [0.5, RED], [0.5, BLUE], [1, BLUE]], np.array([0.49, 0.52], np.float32), 0.01)
        self.assertEqual((hard * 255).round().astype(int).tolist(), [[255, 0, 0], [0, 0, 255]])

    def test_gradient_angles_inside_a_glyph(self):
        across, _, _, _ = self.glyph(stops=[[0, RED], [1, BLUE]], angle=0)
        px, top, bottom, left, right = solid(across)
        row = px[(top + bottom) // 2]
        self.assertGreater(row[left + 1, 0], row[right - 1, 0] + 120)                 # 0 degrees: red on the left...
        self.assertLess(row[left + 1, 2], row[right - 1, 2] - 120)                    # ...blue on the right
        self.assertTrue(np.array_equal(px[top + 2, left:right + 1, :3], px[bottom - 2, left:right + 1, :3]))   # the same all the way down
        down, _, _, _ = self.glyph(stops=[[0, RED], [1, BLUE]], angle=90)
        px, top, bottom, left, right = solid(down)
        self.assertGreater(px[top + 1, (left + right) // 2, 0], px[bottom - 1, (left + right) // 2, 0] + 100)   # 90 degrees: red at the top
        self.assertTrue(np.array_equal(px[top:bottom + 1, left + 2, :3], px[top:bottom + 1, right - 2, :3]))     # the same all the way across
        diagonal, _, _, _ = self.glyph(stops=[[0, RED], [1, BLUE]], angle=45)
        px, top, bottom, left, right = solid(diagonal)
        self.assertGreater(px[top + 1, left + 1, 0], px[bottom - 1, right - 1, 0] + 150)   # 45 degrees: top left to bottom right
        self.assertLess(px[top + 1, left + 1, 0] - px[top + 1, right - 1, 0], px[top + 1, left + 1, 0] - px[bottom - 1, right - 1, 0])

    def test_vertical_gradients_line_up_across_letters(self):
        short = page(); short.paste((0, 0, 0, 0), (42, 6, 60, 16))                    # B is shorter than A: its ink starts lower
        r = fonts.Renderer(FNT, short, fonts.clean_style(dict(stops=[[0, RED], [1, BLUE]], angle=90), []), [])
        (a, _, ya, _), (b, _, yb, _) = r.glyph(65), r.glyph(66)
        self.assertGreater(yb, ya + 5)
        line = yb + 3                                                                  # one line of the text, inside both letters
        pa, pb = np.asarray(a), np.asarray(b)
        self.assertEqual(pa[line - ya, pa.shape[1] // 2, :3].tolist(), pb[line - yb, pb.shape[1] // 2, :3].tolist())
        self.assertLess(pb[3, pb.shape[1] // 2, 0], 230)                               # so B's top is not the gradient's first color

    def test_hard_stop_splits_a_letter_cleanly(self):
        img, _, _, _ = self.glyph(stops=[[0, RED], [0.5, RED], [0.5, BLUE], [1, BLUE]], angle=0)
        px, top, bottom, left, right = solid(img)
        row = px[(top + bottom) // 2, left:right + 1]
        reds, blues = (row[:, 0] > 240) & (row[:, 2] < 15), (row[:, 2] > 240) & (row[:, 0] < 15)
        self.assertGreater(reds.sum(), 5); self.assertGreater(blues.sum(), 5)
        self.assertLessEqual(len(row) - reds.sum() - blues.sum(), 2)                   # at most a pixel or two of blend at the edge
        self.assertTrue(reds[:reds.sum()].all())                                       # all red first, then all blue

    def test_letter_width_stretches_the_shape_and_its_advance(self):
        ink = lambda img: [int(v) for v in (np.ptp(np.where(np.asarray(img)[..., 3].max(axis=a) > 128)[0]) + 1 for a in (0, 1))]   # solid width, height
        normal, _, _, adv = self.glyph()
        wide, _, _, wide_adv = self.glyph(stretch=1.5)
        narrow, _, _, narrow_adv = self.glyph(stretch=0.5)
        self.assertEqual((adv, wide_adv, narrow_adv), (22, 33, 11))
        self.assertEqual((ink(normal), ink(wide), ink(narrow)), ([20, 30], [30, 30], [10, 30]))   # wider or narrower, never taller
        faces = fonts.system_fonts()
        arial = fonts.find_font(faces, 'Arial|Bold')
        if not arial: self.skipTest('Arial is not installed')
        r1 = fonts.Renderer(FNT, self.page, fonts.clean_style(dict(font=arial['id']), faces), faces)
        r2 = fonts.Renderer(FNT, self.page, fonts.clean_style(dict(font=arial['id'], stretch=1.4), faces), faces)
        (g1, x1, y1, a1), (g2, x2, y2, a2) = r1.glyph(65), r2.glyph(65)
        self.assertEqual((y1, g1.height), (y2, g2.height))                             # same baseline and height
        self.assertAlmostEqual(g2.width / g1.width, 1.4, delta=0.1); self.assertAlmostEqual(a2 / a1, 1.4, delta=0.06)
        edge = np.asarray(g2)[g2.height // 2, :, 3]
        self.assertGreater(int((edge == 255).sum()), 8)                                # crisp: solid inside, not a blur
        self.assertLessEqual(int(((edge > 20) & (edge < 235)).sum()), 6)

    def test_the_game_letters_keep_their_dark_edge(self):
        edged = page(); edged.paste((0, 0, 0, 220), (6, 6, 26, 36)); edged.paste((255, 255, 255, 255), (9, 9, 23, 33))   # A: white with a black edge, as nicktext
        draw = lambda **style: np.asarray(fonts.Renderer(FNT, edged, fonts.clean_style(style, []), []).glyph(65)[0]).astype(int)
        ring, core = (lambda px: px[px.shape[0] // 2, 1]), (lambda px: px[px.shape[0] // 2, px.shape[1] // 2])   # a pixel of the edge, one of the middle
        plain = draw()
        self.assertEqual((ring(plain)[:3].max(), ring(plain)[3], core(plain).tolist()), (0, 220, [255, 255, 255, 255]))   # the game's own letter, unchanged
        red = draw(fill=RED)
        self.assertEqual((ring(red)[:3].max(), ring(red)[3], core(red).tolist()), (0, 220, [255, 0, 0, 255]))             # the fill colors the light part only
        hollow = draw(fill=RED, fill_opacity=0)
        self.assertEqual((ring(hollow)[3], core(hollow)[3]), (220, 0))                                                    # hollow: see-through, the edge stays
        legacy = draw(fill=RED, fill2='')                                                                                 # saved by v1.0.15: as it drew then
        self.assertEqual((ring(legacy).tolist(), core(legacy).tolist()), ([255, 0, 0, 220], [255, 0, 0, 255]))
        self.assertEqual([fonts.clean_style(s, [])['edge'] for s in ({}, dict(fill2=''), dict(edge=False), fonts.clean_style(dict(fill2=''), []))], [True, False, False, False])
        self.assertIsNone(fonts.edge_color(self.page))                                                                    # plain white letters have no edge

    def test_big_glows_and_soft_shadows_fade_out_inside_each_letters_picture(self):
        for style in (dict(glow=BLUE, glow_size=32, glow_strength=4), dict(glow=BLUE, glow_size=12, glow_strength=2.2), dict(shadow='#000000', shadow_x=24, shadow_blur=24, shadow_opacity=1)):
            a = np.asarray(self.glyph(**style)[0])[..., 3].astype(int)
            border = max(a[0].max(), a[-1].max(), a[:, 0].max(), a[:, -1].max())
            self.assertLessEqual(border, 3, style)                                         # no straight edge where the picture ends (a box around letters in the game)

    def test_hollow_letters_cut_the_outline_and_glow_out_underneath(self):
        hollow, _, _, _ = self.glyph(fill='#ffffff', fill_opacity=0, outline=RED, outline_width=3, glow=BLUE, glow_size=6)
        px = np.asarray(hollow).astype(int)
        h, w = px.shape[:2]
        self.assertEqual(px[h // 2, w // 2, 3], 0)                                     # see right through the letter
        ring = px[px[..., 3] == 255]
        self.assertTrue(len(ring) and (ring[:, 0] > 240).all() and (ring[:, 1] < 15).all())   # the outline is still drawn, solid red
        half, _, _, _ = self.glyph(fill='#ffffff', fill_opacity=0.5, outline=RED, outline_width=3)
        middle = np.asarray(half)[np.asarray(half).shape[0] // 2, np.asarray(half).shape[1] // 2]
        self.assertEqual((middle[:3].tolist(), abs(int(middle[3]) - 128) <= 1), ([255, 255, 255], True))   # half see-through, no red under it
        full, _, _, _ = self.glyph(fill='#ffffff', outline=RED, outline_width=3)
        row = np.asarray(full)[full.height // 2, :, 3].astype(int)
        opaque = np.where(row == 255)[0]
        self.assertTrue((row[opaque[0]:opaque[-1] + 1] == 255).all())                  # a solid fill: no see-through seam along the outline

    def test_presets_render_with_and_without_their_fonts(self):
        self.assertGreaterEqual(len(fonts.PRESETS), 50)
        self.assertGreaterEqual(len(fonts.PRESET_GROUPS), 7)
        for group, looks in fonts.PRESET_GROUPS.items():
            for name, look in looks.items():
                if look.get('font'): self.assertIn(chain_names(look['font'])[-1], SHIPPED, f'{name}: the last font must ship with Windows')
                for faces in ([], None):
                    preview = fonts.preview(FNT, self.page, fonts.clean_style(look, faces), faces or [], 'AB')
                    self.assertGreater(preview.width, 20, name)
        arial = WINDOWS / 'arial.ttf'
        if not arial.is_file(): self.skipTest('no Windows fonts to stand in for the others')
        wanted = {part.strip() for look in fonts.PRESETS.values() if look.get('font') for part in look['font'].split(',')}
        everything = [dict(id=str(arial), family=part.split('|')[0], style=part.split('|')[1]) for part in sorted(wanted)]
        shipped = [f for f in everything if f['family'] in SHIPPED]                    # a PC without Office fonts
        for faces in (everything, shipped):
            for name, look in fonts.PRESETS.items():
                style = fonts.clean_style(look, faces)
                if look.get('font'): self.assertEqual(style['font'], str(arial), name)   # some font in its list was found
                self.assertGreater(fonts.preview(FNT, self.page, style, faces, 'AB').width, 20, name)

    def test_font_lists_fall_back_in_order(self):
        faces = [dict(id='ink.ttf', family='Ink Free', style='Regular'), dict(id='black.ttf', family='Segoe UI', style='Black'),
                 dict(id='light.ttf', family='Segoe UI', style='Light'), dict(id='a,b.ttf', family='Odd', style='Regular')]
        self.assertEqual(fonts.pick_font(faces, 'Chiller|Regular, Ink Free|Regular')['id'], 'ink.ttf')
        self.assertEqual(fonts.pick_font(faces, 'Segoe UI|Semilight, Segoe UI|Light')['id'], 'light.ttf')   # an exact style before a stand-in
        self.assertEqual(fonts.pick_font(faces, 'Segoe UI|Semilight')['id'], 'black.ttf')                  # the family stands in when nothing fits
        self.assertEqual(fonts.pick_font(faces, 'a,b.ttf')['id'], 'a,b.ttf')                                # an id with a comma
        self.assertIsNone(fonts.pick_font(faces, 'Chiller|Regular, Jokerman|Regular'))
        self.assertEqual(fonts.clean_style(dict(font='Chiller|Regular, Ink Free|Regular'), faces)['font'], 'ink.ttf')

    def test_renders_from_many_threads_at_once(self):
        faces = fonts.system_fonts()
        looks = list(fonts.PRESETS.values())
        errors = []

        def work(i):
            try:
                for look in looks[i::6]: fonts.preview(FNT, self.page, fonts.clean_style(look, faces), faces, 'AB A')
            except Exception as exc: errors.append(exc)
        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])


class FontLabTests(Base):
    maker = staticmethod(font_game)

    def test_a_font_is_applied_built_installed_and_restored(self):
        catalog = self.lab.font_catalog()
        self.assertEqual([g['id'] for g in catalog['game']], ['nicktext'])
        self.assertTrue(self.lab.font_preview('nicktext', dict(fill='#ff0000'), 'AB').startswith(b'\x89PNG'))
        fnt, page = self.game / 'assets' / 'nicktext.fnt', self.game / 'assets' / 'nicktext_0.png'
        before = (file_hash(fnt), file_hash(page))
        result = self.lab.font_apply('nicktext', dict(fill='#000000', glow='#ffffff', glow_size=4, stops=[[0, RED], [1, BLUE]], angle=30, stretch=1.2))
        self.assertEqual(result['characters'], 3)
        build = self.lab.build()
        self.assertEqual(sorted(f['path'] for f in build['files']), ['assets/nicktext.fnt', 'assets/nicktext_0.png'])
        self.lab.install(build['id'])
        self.assertNotEqual(file_hash(fnt), before[0]); self.assertNotEqual(file_hash(page), before[1])
        installed = fonts.parse_fnt(fnt.read_text())
        self.assertEqual((installed['common']['base'], installed['pages'][0]['file']), ('38', 'nicktext_0.png'))   # the game still finds its page
        self.assertEqual(installed['chars'][65]['xadvance'], round(22 * 1.2))
        self.assertFalse(self.lab.pending())
        self.assertEqual(self.lab.mod()['fonts'][0]['installed'], True)
        self.assertEqual(self.lab.font_catalog()['game'][0]['style']['angle'], 30.0)   # the saved style comes back for fine-tuning
        self.lab.font_remove('nicktext')
        self.assertTrue(self.lab.pending())                                              # back to the game font at the next install
        self.lab.restore()
        self.assertEqual((file_hash(fnt), file_hash(page)), before)

    def test_the_catalog_groups_looks_and_old_styles_load(self):
        self.lab.state['fonts'] = dict(nicktext=dict(style=dict(fill='#fff3b0', fill2='#c98a12'), version='x', staged=False))
        catalog = self.lab.font_catalog()
        self.assertEqual([g['group'] for g in catalog['presets']], list(fonts.PRESET_GROUPS))
        self.assertEqual(sum(len(g['items']) for g in catalog['presets']), len(fonts.PRESETS))
        self.assertEqual(set(catalog['presets'][0]['items'][1]['style']), set(fonts.DEFAULT))   # complete styles, ready to preview
        self.assertEqual(catalog['game'][0]['style']['stops'], [[0.0, '#fff3b0'], [1.0, '#c98a12']])

    def test_previews_are_kept_and_reused(self):
        style = dict(stops=[[0, RED], [1, BLUE]], angle=45)
        first = self.lab.font_preview('nicktext', style, 'AB')
        with patch.object(fonts, 'preview', side_effect=AssertionError('rendered again')):
            self.assertEqual(self.lab.font_preview('nicktext', dict(style, outline=''), 'AB'), first)   # the same look after cleaning
        self.assertNotEqual(self.lab.font_preview('nicktext', dict(style, angle=46), 'AB'), first)
        with patch.object(lab_core, 'file_hash', side_effect=AssertionError('read again')):
            self.lab.font_preview('nicktext', dict(style, angle=90), 'AB')            # the game's font files are read once

    def test_theme_ai_turns_text_requests_into_a_font_style(self):
        faces = [dict(id='impact.ttf', family='Impact', style='Regular')]
        s = theme.font_style('make the player names black with a white glow in impact, all caps', faces)
        self.assertEqual((s['fill'], s['glow'], s['outline'], s['font'], s['caps']), ('#000000', '#ffffff', '#ffffff', 'Impact', True))
        gold = theme.font_style('gold names with a drop shadow', faces)
        self.assertEqual((gold['stops'], gold['shadow']), (fonts.PRESETS['Gold']['stops'], '#000000'))   # the Gold look, not a flat gold
        grad = theme.font_style('names with a red to yellow gradient and a thick black outline', faces)
        self.assertEqual((grad['stops'], grad['outline'], grad['outline_width']), ([[0.0, theme.COLORS['red']], [1.0, theme.COLORS['yellow']]], '#000000', 4))
        with patch.object(self.lab, 'typefaces', return_value=[]), patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('make the player names black with a white glow')
            plain = self.themes.chat('make the buttons red')
            mixed = self.themes.chat('make the buttons red and make the player names black with a white glow')
        self.assertEqual((result['source'], result['font']['id']), ('font', 'nicktext'))
        self.assertTrue(result['font']['image'].startswith('data:image/png;base64,'))
        self.assertIn('black letters, white outline, white glow', result['plan']['reply'])
        self.assertNotIn('font', plain)
        self.assertTrue(mixed['plan']['operations'])                                     # both halves of the request are kept
        self.assertEqual(mixed['font']['style']['fill'], '#000000')

    def test_theme_ai_understands_gradients_directions_and_shapes(self):
        style = lambda text: fonts.clean_style(theme.font_style(text, []), [])
        c = theme.COLORS
        three = style('player names in a red, orange and yellow gradient')
        self.assertEqual(three['stops'], [[0.0, c['red']], [0.5, c['orange']], [1.0, c['yellow']]])
        chain = style('names with a gradient from red to blue to green, left to right')
        self.assertEqual(([s[1] for s in chain['stops']], chain['angle']), ([c['red'], c['blue'], c['green']], 0.0))
        self.assertEqual(style('names that fade from purple to pink')['stops'], [[0.0, c['purple']], [1.0, c['pink']]])
        self.assertEqual(style('red-to-blue gradient names')['stops'], [[0.0, c['red']], [1.0, c['blue']]])
        self.assertEqual(style('names with a diagonal purple to cyan gradient')['angle'], 45.0)
        self.assertEqual(style('names with a 30 degree red to blue gradient')['angle'], 30.0)
        self.assertEqual(style('names with a red to blue gradient bottom to top')['angle'], 270.0)
        self.assertEqual(style('names with a red to blue gradient, vertical')['angle'], 90.0)
        self.assertEqual(len(style('names with a midnight gradient')['stops']), len(theme.GRADIENTS['midnight']))
        hollow = style('hollow red names')
        self.assertEqual((hollow['fill_opacity'], hollow['outline'], hollow['outline_width'] >= 3), (0.0, c['red'], True))
        self.assertEqual(style('outline only names with a pink outline')['outline'], c['pink'])
        neon = style('hollow neon names')                                              # the mood picks the look, the letters come out hollow
        self.assertEqual((neon['fill_opacity'], neon['outline'], neon['glow']), (0.0, fonts.PRESETS['Neon pink']['outline'], fonts.PRESETS['Neon pink']['glow']))
        self.assertEqual(style('hollow names')['outline2'], fonts.PRESETS['Hollow']['outline2'])
        self.assertEqual(style('see-through blue names')['fill_opacity'], 0.5)
        self.assertEqual((style('wide names')['stretch'], style('extra wide names')['stretch'], style('narrow names')['stretch']), (1.3, 1.6, 0.8))
        self.assertEqual(style('neon sign names')['outline'], fonts.PRESETS['Neon sign']['outline'])   # looks by name
        self.assertEqual(style('candy cane names')['stops'], fonts.clean_style(fonts.PRESETS['Candy cane'])['stops'])
        self.assertEqual(style('fire & ice player names')['stops'], fonts.clean_style(fonts.PRESETS['Fire & ice'])['stops'])
        self.assertEqual(style('christmas names')['outline2'], fonts.PRESETS['Christmas']['outline2'])    # looks by mood
        self.assertEqual(style('gold outline on the names')['stops'], [])                              # a gold outline, not the Gold look
        self.assertEqual(style('white and gold gradient names')['stops'], [[0.0, '#ffffff'], [1.0, c['gold']]])

    def test_theme_ai_reads_tricky_text_requests(self):
        faces = [dict(id='ariblk.ttf', family='Arial', style='Black'), dict(id='arial.ttf', family='Arial', style='Regular'), dict(id='comic.ttf', family='Comic Sans MS', style='Bold')]
        style = lambda text: fonts.clean_style(theme.font_style(text, faces), faces)
        raw = lambda text: theme.font_style(text, faces)
        c, names = theme.COLORS, lambda s: [color for _, color in s['stops']]
        late = style('names with a gradient at 120 degrees from red to blue')                     # words between "gradient" and its colors
        self.assertEqual((names(late), late['angle']), ([c['red'], c['blue']], 120.0))
        self.assertEqual(names(style('red white and blue gradient names')), [c['red'], '#ffffff', c['blue']])
        self.assertEqual(names(style('make the names red, yellow and green')), [c['red'], c['yellow'], c['green']])   # three colors blend
        pair = style('gold and black names')                                                          # two: the letters and their outline
        self.assertEqual((pair['fill'], pair['stops'], pair['outline']), (c['gold'], [], '#000000'))
        self.assertEqual(raw('green neon names')['look'], 'Neon green')
        tube = style('neon names in red')                                                             # a neon look's color is its tube
        self.assertEqual((tube['outline'], tube['glow'], tube['fill']), (c['red'], c['red'], '#ffffff'))
        hollow = style('hollow gold names')                                                           # the outline takes the gold, the look's edge frames it
        self.assertEqual((hollow['fill_opacity'], hollow['outline'], hollow['outline2']), (0.0, fonts.PRESETS['Gold']['stops'][2][1], fonts.PRESETS['Gold']['outline']))
        black = style('names in arial black')                                                         # a font, not black letters
        self.assertEqual((black['font'], black['fill']), ('ariblk.ttf', '#ffffff'))
        comic = raw('names in comic sans')                                                            # the font, not the Comic look
        self.assertEqual((comic.get('look'), fonts.clean_style(comic, faces)['font']), (None, 'comic.ttf'))
        self.assertGreater(int(style('names with a light blue glow')['glow'][1:3], 16), int(c['blue'][1:3], 16))
        self.assertEqual(names(style('names with a blue gradient'))[-1], c['blue'])                   # one color and the word gradient: light to full
        loud = style('bold outline names')                                                            # the look's own outline and no added bold
        self.assertEqual((loud['outline_width'], loud['bold']), (4.0, 0.0))
        self.assertEqual([raw(t).get('look') for t in ('rose gold names', 'ocean blue names', 'emerald green names')], ['Rose gold', 'Ocean', 'Emerald'])
        self.assertEqual(style('ocean blue names')['stops'], fonts.clean_style(fonts.PRESETS['Ocean'])['stops'])   # "ocean blue" is the look's own color
        self.assertEqual(style('spooky green names')['fill'], c['green'])                            # another color is a new fill
        self.assertEqual((style('sky blue names')['fill'], names(style('blue green gradient names'))), (c['sky'], [c['blue'], c['green']]))

    def test_a_theme_and_a_text_style_in_one_sentence(self):
        self.assertEqual(theme.text_split('christmas theme and red names'), ('red names', ['christmas theme']))
        self.assertEqual(theme.text_split('names in red, white and blue'), ('names in red, white and blue', []))
        with patch.object(self.lab, 'typefaces', return_value=[]), patch.object(theme, 'models', return_value=[]):
            both = self.themes.chat('christmas theme and red names')
        self.assertTrue(both['plan']['operations'])                                                  # the theme is planned...
        self.assertEqual((both['font']['style']['fill'], both['font'].get('offer')), (theme.COLORS['red'], None))   # ...and the names are red

    def test_a_whole_game_mood_offers_matching_text(self):
        with patch.object(self.lab, 'typefaces', return_value=[]), patch.object(theme, 'models', return_value=[]):
            spooky = self.themes.chat('halloween theme')
            gold = self.themes.chat('make everything gold')
            ocean = self.themes.chat('theme it ocean')
            plain = self.themes.chat('make the buttons red')
            parts = self.themes.chat('make the buttons gold')
            asked = self.themes.chat('halloween theme and make the names neon green')
        self.assertEqual((spooky['font']['offer'], spooky['font']['look'], spooky['font']['id']), (True, 'Pumpkin', 'nicktext'))
        self.assertTrue(spooky['font']['image'].startswith('data:image/png;base64,'))
        self.assertTrue(spooky['plan']['operations'])                                  # the theme itself is planned as before
        self.assertEqual((gold['font']['look'], ocean['font']['look']), ('Gold', 'Ocean'))
        self.assertNotIn('font', plain); self.assertNotIn('font', parts)               # a request about some parts gets no offer
        self.assertNotIn('offer', asked['font'])                                       # asking for text yourself beats the offer
        self.assertEqual(asked['font']['look'], 'Neon green')
        for mood, look in (('christmas', 'Christmas'), ('valentines day', 'Valentine'), ('galaxy', 'Galaxy'), ('toxic', 'Toxic'), ('rainbow', 'Rainbow'),
                           ('winter', 'Frost'), ('fire', 'Fire'), ('neon', 'Neon pink'), ('ice', 'Ice')):
            self.assertEqual(next(name for pattern, name in theme.MOOD_TEXT if theme.re.search(rf'\b(?:{pattern})\b', f'{mood} theme')), look, mood)
            self.assertIn(look, fonts.PRESETS)
