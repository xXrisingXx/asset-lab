from unittest.mock import patch
from PIL import Image
import preview
import theme
from lab_core import png
from test_theme import Base


class PreviewTests(Base):
    """The game preview draws the menu and a match from the game's sprites, with your saved edits."""

    def test_screens_and_sizes(self):
        for screen in preview.SCREENS:
            image = preview.render(self.lab, screen, 640)
            self.assertEqual(image.size, (640, 400))
        self.assertEqual(preview.render(self.lab, 'menu', 99999).width, preview.W)
        with self.assertRaises(ValueError): preview.render(self.lab, 'shop')
        with self.assertRaises(ValueError): preview.render(self.lab, 'match', mode='sepia')

    def test_dark_mode_darkens_the_arena(self):
        light = preview.render(self.lab, 'match', 640, 'light', 'pc').convert('L')
        dark = preview.render(self.lab, 'match', 640, 'dark', 'phone').convert('L')
        from PIL import ImageStat
        self.assertLess(ImageStat.Stat(dark).mean[0], ImageStat.Stat(light).mean[0] - 60)

    def test_saved_menu_background_shows(self):
        row = next(r for r in self.lab.catalog.rows.values() if r['name'] == 'bg2.png' and r['editable'])
        before = preview.render(self.lab, 'menu', 640).convert('RGB').getpixel((320, 380))
        red = Image.new('RGBA', self.lab.image(row['key']).size, (220, 20, 20, 255))
        self.lab.save(row['key'], png(red), 'original')
        after = preview.render(self.lab, 'menu', 640).convert('RGB').getpixel((320, 380))
        self.assertNotEqual(before, after)
        self.assertGreater(after[0], 150); self.assertLess(after[1], 60)


class PluralNameTests(Base):
    """A name you gave matches its plural too: "the eject buttons" means the Eject buttons, not every button."""

    def test_plural_of_a_given_name(self):
        rows = [r for r in self.lab.catalog.rows.values() if r['editable'] and r['kind'] == 'sprite'][:2]
        self.lab.labels.name([rows[0]['key']], 'Eject button (in a match, PC)')
        self.lab.labels.name([rows[1]['key']], 'Eject button (in a match, phones)')
        self.themes.reindex()
        with patch.object(theme, 'models', return_value=[]):
            plan = self.themes.chat('make the eject buttons red')['plan']
        names = {n for op in plan['operations'] for n in op.get('match') or []}
        self.assertEqual(names, {r['name'] for r in rows})
