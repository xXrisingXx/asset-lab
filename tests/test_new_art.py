import re
import unittest
import numpy as np
from PIL import Image
import design


class NewArtRouting(unittest.TestCase):
    """Requests for new art go to the image model; colors, glows and moods of existing art stay with the shader."""
    DRAW = ['change these 2 background images to something entirely different. im thinking something like static',
            'change their entire look', 'generate me a whole new background with city skylines', 'make a brand new background',
            'give the menu a new background of outer space', 'full spooky reskin', 'create a new coin design', 'draw me a castle']
    SHADE = ['make the buttons red', 'gold buttons with a glow', 'rainbow outline on the buttons', 'make everything darker',
             'something like gold for the coin', 'make the background blue', 'synthwave colors', 'more transparent panels']

    def test_new_art_is_drawn(self):
        for text in self.DRAW: self.assertTrue(design.wants_design(text), text)

    def test_colors_stay_shaded(self):
        for text in self.SHADE: self.assertFalse(design.wants_design(text), text)


class Backgrounds(unittest.TestCase):
    def test_a_new_background_tiles_without_seams(self):
        rng = np.random.default_rng(1)
        img = Image.fromarray(rng.integers(0, 255, (128, 160, 3), dtype=np.uint8))
        a = np.asarray(design.seamless(img), np.int16)[..., :3]
        # Across each wrap-around edge the step is as small as between neighbours inside the picture.
        inside = np.abs(np.diff(a, axis=1)).mean()
        across = np.abs(a[:, 0] - a[:, -1]).mean(), np.abs(a[0] - a[-1]).mean()
        self.assertLess(max(across), inside * 1.2)

    def test_cover_fills_the_frame_and_zoom_crops(self):
        art = Image.new('RGBA', (300, 200), (10, 20, 30, 255)); art.paste((200, 0, 0, 255), (0, 0, 30, 200))
        out = design.cover(art, (128, 128))
        self.assertEqual(out.size, (128, 128)); self.assertEqual(out.getchannel('A').getextrema(), (255, 255))
        left = design.cover(art, (128, 128), dx=-1)
        self.assertEqual(left.getpixel((2, 64))[:3], (200, 0, 0))     # moved to the left edge: the red band shows
        self.assertNotEqual(out.getpixel((2, 64))[:3], (200, 0, 0))     # centered: it is cropped away


class Food(unittest.TestCase):
    """Food is redrawn as one set: every pellet shape and size the game uses gets the same new art (crowns, say)."""

    def setUp(self):
        from types import SimpleNamespace
        def row(key, name, size=128, **k):
            return dict(dict(key=key, name=name, kind='sprite', group='Cells & gameplay', editable=True, primary=True, legacy=False, removed=False,
                             dup=key, width=size, height=size, source='assets/theme.png', parent='sheet', label='', live=True), **k)
        rows = [row('a', 'food'), row('b', 'food1'), row('c', 'food2'), row('d', 'foodf1', 64), row('e', 'foods1', 64),
                row('f', 'foode1', 64, legacy=True), row('g', 'food1', dup='b', primary=False), row('h', 'cell-skin')]
        self.lab = SimpleNamespace(catalog=SimpleNamespace(rows={r['key']: r for r in rows}, children={}))
        self.lab.row = lambda k: self.lab.catalog.rows[k]
        self.designer = design.Designer.__new__(design.Designer)
        self.designer.lab, self.designer.themes = self.lab, SimpleNamespace(named=lambda text: ({}, text))

    def test_every_food_sprite_is_one_target_set(self):
        names = [r['name'] for r in self.designer.targets('turn the food into little crowns')]
        self.assertEqual(names, ['food1', 'food2', 'foods1'])   # the shapes Mitosis shows; blobs, dots, leftovers and copies are left out

    def test_one_drawing_covers_them_all(self):
        from unittest.mock import patch
        self.designer.jobs, self.designer.lock = {}, __import__('threading').Lock()
        with patch.object(self.designer, 'status', return_value=dict(missing=[])), patch('threading.Thread'), patch.object(design, 'key_for', return_value='green'), \
             patch.object(design, 'tinted', return_value=True), patch.object(design, 'tints', return_value={}):
            self.lab.catalog.image = lambda k: Image.new('RGBA', (8, 8), (255, 255, 255, 255))
            job = self.designer.start('turn the food into little crowns', count=1)
            self.assertTrue(job['food']); self.assertEqual(len(job['targets']), 3)
            self.assertIn('food pellet', self.designer.instruction(job))
            self.designer.jobs.clear()
            other = self.designer.start('make it spooky', keys=['h', 'a'], count=1)
            self.assertFalse(other['food']); self.assertEqual(other['targets'], ['h'])


if __name__ == '__main__': unittest.main()


class MakingIntoNewThings(unittest.TestCase):
    def test_making_a_part_into_new_things_is_drawn(self):
        import theme
        for text in ('make the food little tires', 'make the coins into tiny donuts', 'make the virus a giant pizza'):
            self.assertTrue(design.wants_design(text, theme.unknown_words(text)), text)
        for text in ('make the buttons a little darker', 'make the panels gold', 'make it a bit brighter'):
            self.assertFalse(design.wants_design(text, theme.unknown_words(text)), text)

    def test_request_words_are_not_sprite_names(self):
        import theme
        for word in ('create', 'design', 'build', 'little'): self.assertIn(word, theme.STOP)   # not the create-guild icon


class PartBecomes(unittest.TestCase):
    """The many ways people say "this part becomes that": the part is what gets redrawn, the new thing is only described."""
    CASES = {'make the food little tires': ('the food', 'little tires'), 'the food should be little burgers': ('the food', 'little burgers'),
             'i want the food to be stars': ('the food', 'stars'), 'can the food be tires': ('the food', 'tires'),
             'food = tiny tires': ('food', 'tiny tires'), 'tires for the food please': ('the food', 'tires'),
             'swap the food for tires': ('the food', 'tires'), 'the virus but as a donut': ('the virus', 'a donut'),
             'make the potion a beer bottle': ('the potion', 'a beer bottle'), 'change the coin into a bitcoin': ('the coin', 'a bitcoin'),
             'put sunglasses on the virus': ('the virus', 'sunglasses'), 'add some stars to the title': ('the title', 'some stars'),
             'draw tiny sharks as the food': ('the food', 'tiny sharks'), 'redraw the crown as a tiara': ('the crown', 'a tiara'),
             'make the friends icon two little cats': ('the friends icon', 'two little cats'),
             'change the arena background to grass': ('the arena background', 'grass'),
             'replace the food with tiny crowns': ('the food', 'tiny crowns')}

    def test_the_part_and_the_new_thing(self):
        for text, want in self.CASES.items(): self.assertEqual(design.becomes(text), want, text)

    def test_not_a_change_of_part(self):
        for text in ('make the buttons red', 'make the friends button icon the same shade as its neighbour', 'gold coin', 'make everything blue'):
            self.assertIn(design.becomes(text) and design.becomes(text)[1], (None, 'red', 'blue'), text)

    def test_nicknames_and_badges(self):
        import theme
        self.assertEqual(theme.ALIASES['red virus'], ['spawner.png'])
        self.assertTrue(re.search(design.BRANDS, 'the app store badge')); self.assertFalse(re.search(design.BRANDS, 'the store icon'))
        for word in ('round', 'circle', 'shadow', 'left', 'next', 'close'): self.assertIn(word, theme.STOP)
