import base64
import io
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import pictures
import theme
from test_theme import Base


def ball(color=(190, 60, 255), size=64):
    """A colored ball with a white highlight and a dark core: light and shade a recolor must keep."""
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - size / 2, yy - size / 2) / (size / 2)
    a = np.zeros((size, size, 4), np.float32)
    a[..., :3] = np.array(color, np.float32)
    a[..., :3] = a[..., :3] + (255 - a[..., :3]) * np.clip(1 - r * 2.2, 0, 1)[..., None]   # white middle
    a[r < 0.08, :3] = (60, 0, 90)                                                         # dark core
    a[..., 3] = np.where(r <= 1, 255, 0)
    return Image.fromarray(a.astype(np.uint8), 'RGBA')


def hue_of(rgb):
    lab = pictures.oklab(np.array([rgb], np.float32) / 255)[0]
    return np.arctan2(lab[2], lab[1]), float(lab[0])


class Recolor(unittest.TestCase):
    def test_the_main_color_becomes_the_new_one_with_its_light_and_shade(self):
        art = ball()
        out = np.asarray(pictures.recolor(art, '#1e3a8a')).astype(int)   # dark blue
        edge, middle, core = out[32, 60], out[32, 35], out[32, 32]
        target_h, target_l = hue_of((0x1e, 0x3a, 0x8a))
        h, l = hue_of(edge[:3])
        self.assertLess(abs(np.angle(np.exp(1j * (h - target_h)))), 0.15)            # the color is the new one
        self.assertLess(abs(l - target_l), 0.06)                                     # and as dark as asked
        self.assertGreater(hue_of(middle[:3])[1], target_l + 0.35)                    # the near-white highlight stays far lighter
        self.assertLess(hue_of(core[:3])[1], hue_of(middle[:3])[1])                  # the core stays darker than the middle
        self.assertTrue((out[..., 3] == np.asarray(art)[..., 3]).all())              # the shape is untouched

    def test_other_colors_and_gray_art(self):
        art = ball()
        art.paste((40, 200, 60, 255), (0, 0, 6, 6))                                  # a green accent far from the purple
        out = np.asarray(pictures.recolor(art, '#e53935'))
        self.assertEqual(tuple(out[2, 2]), (40, 200, 60, 255))                       # left alone
        gray = Image.new('RGBA', (8, 8), (128, 128, 128, 255))
        tinted = np.asarray(pictures.recolor(gray, '#3b82f6')).astype(int)[4, 4]
        self.assertGreater(tinted[2], tinted[0] + 20)                                 # gray art is tinted

    def test_requests_name_the_color(self):
        art = ball()
        self.assertEqual(pictures.wanted_color('can it make a dark blue version of this', art), theme.text_color('dark blue'))
        self.assertEqual(pictures.wanted_color('make it #FF8800', art), '#ff8800')
        darker = pictures.wanted_color('make it darker', art)
        self.assertLess(pictures.hex_lab(darker)[0], pictures.main_color(art)[0])
        gray = pictures.hex_lab(pictures.wanted_color('black and white please', art))
        self.assertLess(abs(gray[1]) + abs(gray[2]), 0.01)
        self.assertEqual(pictures.wanted_color('make it cooler', art), '')


class PicturesInTheLab(Base):
    def setUp(self):
        super().setUp()
        self.pics = pictures.Pictures(self.lab)

    def test_a_pasted_picture_is_kept_once_and_put_on_game_art(self):
        buffer = io.BytesIO(); ball().save(buffer, format='PNG')
        url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        pid = self.pics.save(url)
        self.assertEqual(self.pics.save(url), pid)                                   # the same picture: one file
        self.assertEqual(len(list((self.lab.root / 'pictures').iterdir())), 1)
        row = next(r for r in self.lab.catalog.rows.values() if r['editable'] and r['kind'] == 'sprite' and r['width'] >= 16)
        result = self.pics.apply(pid, row['key'], '#1e3a8a')
        self.assertEqual(result['key'], row['key'])
        self.assertEqual(self.lab.image(row['key']).size, (row['width'], row['height']))
        self.assertIn(row['key'], self.lab.state['staged'])
        for bad in ('../../etc', 'preset:../x', ''):
            with self.assertRaises(ValueError): self.pics.image(bad)
        with self.assertRaises(ValueError): self.pics.save(b'not a picture')

    def test_presets_are_listed_and_offered_for_their_own_art(self):
        listed = pictures.presets()
        virus = next(p for p in listed if p['id'] == 'preset:purple-virus')
        self.assertEqual(virus['for'], ['virus.png', 'light_virus.png'])
        self.assertEqual(self.pics.image('preset:purple-virus').size, (512, 512))
        blue = np.asarray(self.pics.render('preset:purple-virus', '#1e3a8a'))
        self.assertEqual(blue.shape, (512, 512, 4))

    def test_a_background_preset_goes_on_a_whole_set_and_tiles(self):
        bg = next((p for p in pictures.presets() if p['kind'] == 'background'), None)
        if bg is None: self.skipTest('no background presets drawn yet')
        rows = [r for r in self.lab.catalog.rows.values() if r['editable'] and r['kind'] == 'image'][:2]
        self.pics.sets = {'menu': lambda: rows, 'arena': lambda: []}
        self.assertEqual([t['key'] for t in self.pics.targets(bg['id'])], ['set:menu'])     # the arena set is offered only when the game has one
        result = self.pics.apply(bg['id'], 'set:menu', '#3b82f6')
        self.assertEqual(result['count'], 2)
        for r in rows:
            art = np.asarray(self.lab.image(r['key']))
            self.assertEqual(art.shape[:2], (r['height'], r['width']))
            self.assertTrue((art[..., 3] == 255).all())                                   # opaque: the game's color never shows through
        with self.assertRaises(ValueError): self.pics.apply(bg['id'], 'set:arena')        # no arena here
        self.pics.apply(bg['id'], 'set:menu', '', strength=0.5)                         # less strength: the game's color shows through
        self.assertEqual(int(np.asarray(self.lab.image(rows[0]['key']))[..., 3].max()), 128)

    def test_presets_have_categories(self):
        listed = pictures.presets()
        self.assertTrue(all(p['category'] and p['kind'] for p in listed))
        self.assertEqual(next(p for p in listed if p['id'] == 'preset:purple-virus')['category'], 'Viruses')
        for p in listed:
            if p['kind'] == 'background': self.assertEqual(p['for'], ['menu', 'arena'])


class AWholeThemeFromAPreset(Base):
    def setUp(self):
        super().setUp()
        import design
        self.designer = self.themes.designer = design.Designer(self.lab, self.themes)

    def test_the_preset_is_offered_and_the_rest_drawn_in_its_colors(self):
        import design
        with patch.object(self.designer, 'status', return_value=dict(available=True, missing=[], seconds_per_drawing=30)), \
                patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('Make a whole theme from Purple virus', theme_from='preset:purple-virus')
        self.assertEqual(result['picture']['id'], 'preset:purple-virus')
        offer = result['design']
        self.assertTrue(offer['inspiration'].endswith('.png') and (self.lab.root / 'inspiration' / offer['inspiration']).is_file())
        self.assertFalse({t['file'] for t in offer['targets']} & set(design.VIRUSES))         # the viruses are the preset itself
        learned = self.designer.palettes()[design.palette_key(offer['prompt'])]['colors']
        self.assertTrue(learned)                                                           # every drawing matches the preset's colors
        with patch.object(self.designer, 'status', return_value=dict(available=False, missing=['flux'])):
            plain = self.themes.chat('Make a whole theme from Purple virus', theme_from='preset:purple-virus')
        self.assertNotIn('design', plain)
        self.assertIn('install the image model', plain['plan']['reply'])
