import base64
import io
import unittest
import unittest.mock
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

    def test_the_build_review_sees_each_new_picture(self):
        # The review asks for each edit's picture by its revision: one address per picture, so a new one is never the old one.
        row = next(r for r in self.lab.catalog.rows.values() if r['editable'] and r['kind'] == 'sprite' and r['width'] >= 16)
        self.pics.apply('preset:purple-virus', row['key'], '#1e3a8a')
        first = next(e for e in self.lab.mod()['edits'] if e['key'] == row['key'])['revision']
        self.pics.apply('preset:purple-virus', row['key'], '#c8102e')
        second = next(e for e in self.lab.mod()['edits'] if e['key'] == row['key'])['revision']
        self.assertNotEqual(first, second)

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
        self.assertEqual([t['key'] for t in self.pics.targets(bg['id'])], ['set:menu'])     # in-game sets are offered only when the game has floors
        floors = [dict(r, name=n) for r, n in zip([r for r in self.lab.catalog.rows.values() if r['editable'] and r['kind'] == 'image'][2:4], ('worldbg.png', 'worldbg_w.png'))]
        self.pics.sets = {'menu': lambda: rows, 'arena': lambda: floors}
        self.assertEqual([t['key'] for t in self.pics.targets(bg['id'])][:5], pictures.BACKGROUND_SETS)   # everywhere first
        self.assertEqual([r['name'] for r in self.pics.background_rows('set:arena-light')], ['worldbg_w.png'])
        self.assertEqual(len(self.pics.background_rows('set:everywhere')), 4)
        self.pics.sets = {'menu': lambda: rows, 'arena': lambda: []}
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
        virus = next(p for p in listed if p['id'] == 'preset:purple-virus')
        self.assertEqual((virus['group'], virus['category']), ('Viruses', 'Classic'))   # groups, then categories in them
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


class VirusAndFoodPresets(Base):
    def test_a_virus_goes_on_both_viruses_and_a_food_on_one_food(self):
        pics = pictures.Pictures(self.lab)
        rows = [r for r in self.lab.catalog.rows.values() if r['editable'] and r['kind'] == 'sprite' and r['width'] >= 16][:3]
        pics.sets = {'food': lambda: [[rows[0]], [rows[1], rows[2]]]}                      # two foods here, the second with a small copy
        foods = [t['key'] for t in pics.targets('preset:purple-virus') if t['key'].startswith('set:')]
        self.assertEqual(foods, [])                                                        # no viruses in this test game
        food = next((p for p in pictures.presets() if p['kind'] == 'food'), None)
        if food is None: self.skipTest('no food presets drawn yet')
        self.assertEqual([t['key'] for t in pics.targets(food['id'])][:2], ['set:food:0', 'set:food:1'])
        result = pics.apply(food['id'], 'set:food:1', '#e53935')
        self.assertEqual(result['count'], 2)
        for r in rows[1:]: self.assertEqual(self.lab.image(r['key']).size, (r['width'], r['height']))   # fitted to each size
        self.assertEqual(food['group'], 'Food')


class ButtonSets(unittest.TestCase):
    """A button set restyles the game's own buttons in one look: same shapes and sizes, the set's material."""

    @staticmethod
    def play():   # a pink circle with a white triangle, like the game's play button
        size = 128
        yy, xx = np.mgrid[0:size, 0:size]
        a = np.zeros((size, size, 4), np.uint8)
        circle = np.hypot(xx - 64, yy - 64) < 56
        a[circle] = (231, 57, 98, 255)
        tri = (xx > 48) & (xx < 88) & (np.abs(yy - 64) < (88 - xx) * 0.6)
        a[tri & circle] = (255, 255, 255, 255)
        return Image.fromarray(a, 'RGBA')

    def test_a_button_keeps_its_shape_and_takes_the_material(self):
        import button_sets
        gold = Image.new('RGB', (64, 64), (230, 180, 40))
        out = button_sets.style(self.play(), dict(outline='#5c3d00', ink='#ffffff', bevel=0.9), gold)
        self.assertEqual(out.size, (128, 128))
        px = np.asarray(out).astype(int)
        self.assertEqual(px[64, 4, 3], 0)                                   # outside stays clear
        face = px[40, 64]                                                     # the circle's face: gold, not pink
        self.assertGreater(face[0], face[2] + 60)
        self.assertGreater(face[1], face[2] + 30)
        self.assertGreater(px[64, 60, :3].min(), 200)                       # the triangle: the set's white ink
        self.assertEqual(px[64, 60, 3], 255)

    def test_icons_the_game_tints_stay_light_gray(self):
        import button_sets
        icon = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        icon.paste((255, 255, 255, 255), (16, 16, 48, 48))
        out = np.asarray(button_sets.style(icon, dict(outline='#000000', glow='#ff0000', bevel=0.9), Image.new('RGB', (32, 32), (200, 30, 30)), tinted=True)).astype(int)
        solid = out[..., 3] > 200
        self.assertTrue((np.abs(out[solid, 0] - out[solid, 2]) < 3).all())  # gray: the game's color does the coloring
        self.assertGreater(out[32, 32, :3].mean(), 120)

    def test_a_set_goes_on_every_button_of_the_menu(self):
        sets = [p for p in pictures.presets() if p['kind'] == 'menu']
        if not sets: self.skipTest('no button sets drawn yet')
        self.assertTrue(all(p['group'] == 'Menu buttons' and p['look'] for p in sets))
        self.assertEqual(len([p for p in pictures.presets() if p['kind'] == 'ingame']), len(sets))   # every look: menu and in game
        lab = unittest.mock.MagicMock()
        pics = pictures.Pictures(lab)
        rows = [dict(key='a', name='icon-play', width=128, height=128), dict(key='b', name='back-icon', width=128, height=128)]
        lab.catalog.image.return_value = self.play()
        with patch.object(pictures.Pictures, 'button_rows', return_value=rows):
            self.assertEqual([t['key'] for t in pics.targets(sets[0]['id'])], ['set:menu-buttons'])
            done = pics.apply(sets[0]['id'], 'set:menu-buttons')
            self.assertEqual(done['count'], 2)
            self.assertEqual([c.args[0] for c in lab.commit.call_args_list], ['a', 'b'])
            self.assertEqual(lab.commit.call_args_list[0].args[1].size, (128, 128))
            sheet = pics.render(sets[0]['id'], small=True)
            self.assertGreater(sheet.width, 64)
            data = pics.export([sets[0]['id']])
        import zipfile
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertTrue(any(n.endswith('/preview.png') for n in names) and any(n.endswith('/icon-play.png') for n in names))

    def test_a_category_is_chosen_for_export(self):
        listed = pictures.presets()
        bg = next((p for p in listed if p['kind'] == 'background'), None)
        if bg is None: self.skipTest('no backgrounds')
        ids, name = pictures.chosen(dict(group='Backgrounds', category=bg['category']))
        self.assertIn(bg['id'], ids)
        self.assertTrue(all(p['category'] == bg['category'] for p in listed if p['id'] in ids))
        self.assertNotIn(' ', name)
        with self.assertRaises(ValueError): pictures.chosen(dict(id='preset:nope'))

    def test_backgrounds_export_as_the_game_files_they_change(self):
        # Ready to drop in: each file named as in the game, its size; one folder per background when there are several.
        import zipfile
        bgs = [p for p in pictures.presets() if p['kind'] == 'background'][:2]
        if len(bgs) < 2: self.skipTest('no background presets')
        lab = unittest.mock.MagicMock()
        pics = pictures.Pictures(lab)
        menu = [dict(key='m', name='bg2.png', source='assets/bg2.png', entry='', editable=True, width=512, height=512)]
        arena = [dict(key='a', name='worldbg_w.png', source='assets/assets.zip', entry='worldbg_w.png', editable=True, width=256, height=256),
                 dict(key='s', name='sprite', source='assets/theme4.png', entry='', parent='sheet', editable=True, width=64, height=64)]
        pics.sets = {'menu': lambda: menu, 'arena': lambda: arena}
        # the dark-mode floors here are a sheet's sprite, not a file: not offered
        self.assertEqual([k for k, _ in pics.game_targets()['background']], ['set:everywhere', 'set:menu', 'set:arena', 'set:arena-light'])
        one = zipfile.ZipFile(io.BytesIO(pics.export([bgs[0]['id']], 'set:everywhere')))
        self.assertEqual(sorted(one.namelist()), ['READ ME.txt', 'bg2.png', 'inside assets.zip/worldbg_w.png'])
        self.assertEqual(Image.open(io.BytesIO(one.read('inside assets.zip/worldbg_w.png'))).size, (256, 256))
        both = zipfile.ZipFile(io.BytesIO(pics.export([p['id'] for p in bgs], 'set:menu')))
        self.assertEqual(sorted(n.split('/')[-1] for n in both.namelist() if '/' in n), ['bg2.png', 'bg2.png'])
        self.assertEqual(len({n.split('/')[0] for n in both.namelist() if '/' in n}), 2)                 # a folder for each
        with self.assertRaises(ValueError): pics.export([bgs[0]['id']], 'set:nowhere')
