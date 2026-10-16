import base64
from unittest.mock import patch
from PIL import Image
import theme
from lab_core import png
from test_theme import Base


class ServerArtTests(Base):
    """The item sheet the game's server restores (potions, power-ups, equipment) stays out of the lab."""

    def mark_items(self):
        rows = [r for r in self.lab.catalog.rows.values() if r.get('entry', '').endswith('items.png')]
        self.assertTrue(rows, 'the fixture game has an item sheet')
        for r in rows: r['server_only'] = True
        self.lab.pool.apply(); self.themes.reindex()
        return rows

    def test_server_art_is_hidden_unstaged_and_never_themed(self):
        rows = self.mark_items()
        sheet = next(r for r in rows if r['editable'] and not r.get('parent'))
        im = self.lab.image(sheet['key']); im.putpixel((0, 0), (1, 2, 3, 255))
        self.lab.save(sheet['key'], png(im), 'original')                       # an edit saved before it was hidden
        keys = {r['key'] for r in rows}
        found = {r['key'] for r in self.lab.search(limit=500)['items']} | {r['key'] for r in self.lab.search(limit=500, removed=True)['items']}
        self.assertFalse(found & keys, 'neither in the library nor in the Removed list')
        self.assertNotIn(sheet['key'], [s['key'] for s in self.lab.staged()])   # so installs leave the game's file alone
        self.lab.pool.discard([sheet['key']])
        self.assertTrue(self.lab.catalog.rows[sheet['key']]['removed'], 'it cannot be put back')

    def test_theme_ai_explains_instead_of_planning(self):
        self.mark_items()
        with patch.object(theme, 'models', return_value=[]):
            potions = self.themes.chat('make the potions spooky')
            mixed = self.themes.chat('make the buttons red and the potions green')
        self.assertIn("come from the game's server", potions['plan']['reply'])
        self.assertTrue(mixed['plan']['operations'])
        self.assertIn("come from the game's server", mixed['note'])
