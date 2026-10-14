import unittest
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


def font_game(root):
    game = stale_game(root)
    page = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
    page.paste((255, 255, 255, 255), (6, 6, 26, 36)); page.paste((255, 255, 255, 255), (42, 6, 60, 36))
    page.save(game / 'assets' / 'nicktext_0.png')
    (game / 'assets' / 'nicktext.fnt').write_text(FNT, encoding='utf-8')
    return game


class FontRenderTests(unittest.TestCase):
    def setUp(self):
        self.page = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
        self.page.paste((255, 255, 255, 255), (6, 6, 26, 36)); self.page.paste((255, 255, 255, 255), (42, 6, 60, 36))

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
        s = fonts.clean_style(dict(size=9, glow='red', outline='#ABCDEF', caps='yes', italic=-3), [])
        self.assertEqual((s['size'], s['glow'], s['outline'], s['caps'], s['italic']), (2.0, '', '#abcdef', False, -0.4))
        grown = fonts.round_grow(Image.new('L', (11, 11), 0).point(lambda v: v), 3)
        self.assertEqual(grown.getbbox(), None)


class FontLabTests(Base):
    maker = staticmethod(font_game)

    def test_a_font_is_applied_built_installed_and_restored(self):
        catalog = self.lab.font_catalog()
        self.assertEqual([g['id'] for g in catalog['game']], ['nicktext'])
        self.assertTrue(self.lab.font_preview('nicktext', dict(fill='#ff0000'), 'AB').startswith(b'\x89PNG'))
        fnt, page = self.game / 'assets' / 'nicktext.fnt', self.game / 'assets' / 'nicktext_0.png'
        before = (file_hash(fnt), file_hash(page))
        result = self.lab.font_apply('nicktext', dict(fill='#000000', glow='#ffffff', glow_size=4))
        self.assertEqual(result['characters'], 3)
        build = self.lab.build()
        self.assertEqual(sorted(f['path'] for f in build['files']), ['assets/nicktext.fnt', 'assets/nicktext_0.png'])
        self.lab.install(build['id'])
        self.assertNotEqual(file_hash(fnt), before[0]); self.assertNotEqual(file_hash(page), before[1])
        installed = fonts.parse_fnt(fnt.read_text())
        self.assertEqual((installed['common']['base'], installed['pages'][0]['file']), ('38', 'nicktext_0.png'))   # the game still finds its page
        self.assertFalse(self.lab.pending())
        self.assertEqual(self.lab.mod()['fonts'][0]['installed'], True)
        self.lab.font_remove('nicktext')
        self.assertTrue(self.lab.pending())                                              # back to the game font at the next install
        self.lab.restore()
        self.assertEqual((file_hash(fnt), file_hash(page)), before)

    def test_theme_ai_turns_text_requests_into_a_font_style(self):
        faces = [dict(id='impact.ttf', family='Impact', style='Regular')]
        s = theme.font_style('make the player names black with a white glow in impact, all caps', faces)
        self.assertEqual((s['fill'], s['glow'], s['outline'], s['font'], s['caps']), ('#000000', '#ffffff', '#ffffff', 'Impact', True))
        gold = theme.font_style('gold names with a drop shadow', faces)
        self.assertEqual((gold['fill'], gold['fill2'], gold['shadow']), ('#fff3b0', '#c98a12', '#000000'))   # the Gold look, not a flat gold
        grad = theme.font_style('names with a red to yellow gradient and a thick black outline', faces)
        self.assertEqual((grad['fill'], grad['fill2'], grad['outline'], grad['outline_width']), (theme.COLORS['red'], theme.COLORS['yellow'], '#000000', 4))
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
