"""Simple's library: what players change, by the name they know, one card per thing (all its copies checked together)."""
from unittest.mock import patch
import design
import simple_library
from test_theme import Base


class SimpleLibrary(Base):
    def setUp(self):
        super().setUp()
        self.designer = self.themes.designer = design.Designer(self.lab, self.themes)

    def rows(self):
        rows = {}
        def add(name, label='', sheet='theme4.png', key=None, size=128):
            key = key or f'{sheet}:{name}'
            rows[key] = dict(key=key, name=name, label=label, editable=True, kind='sprite', source='assets/' + sheet, width=size, height=size)
        add('icon-play', 'Play button (Main menu)')
        add('icon-coin', 'Coin icon (Main menu)')
        add('cached-1.png', 'Coin icon (Main menu)', key='cache:1')                 # the coin the game also downloads: one card
        add('icon-ingame-potion', '')                                              # no name in the UI map: a friendly one
        add('button-split', 'Split button (in a match, phones)', size=188)
        add('logo', 'MITOSIS title logo (Main menu)', sheet='theme.png')
        add('logo', '', key='theme4.png:logo')
        add('icon-iphone', 'App Store badge (Main menu)')                          # store badges are not changed
        add('ae.png', '', sheet='flags.png')                                       # a file no player looks for
        rows['virus'] = dict(key='virus', name='virus.png', label='Virus (light mode)', editable=True, kind='image', source='assets/virus.png', width=512, height=512)
        rows['red'] = dict(key='red', name='spawner.png', label='', editable=True, kind='image', source='assets/spawner.png', width=512, height=512)
        return rows

    def listing(self, **kw):
        with patch.object(self.lab.catalog, 'rows', self.rows()), patch.object(self.designer, 'menu_backgrounds', return_value=[]), \
                patch.object(self.designer, 'arena_backgrounds', return_value=[]), patch.object(self.designer, 'food_kinds', return_value=[]):
            return simple_library.listing(self.designer, **kw)

    def test_names_players_know_one_card_each(self):
        cards = {c['name']: c for c in self.listing()['items']}
        self.assertEqual(sorted(cards), ['Coin icon', 'Play button', 'Potion button', 'Split button', 'The MITOSIS title', 'Virus (light mode)'])
        self.assertEqual(sorted(cards['Coin icon']['keys']), ['cache:1', 'theme4.png:icon-coin'])   # every copy, checked together
        self.assertEqual(len(cards['The MITOSIS title']['keys']), 2)
        self.assertEqual(cards['Split button']['category'], 'ingame')

    def test_categories_and_search(self):
        found = self.listing()
        self.assertEqual([c['id'] for c in found['categories']], ['buttons', 'ingame', 'viruses', 'title'])
        self.assertEqual([c['name'] for c in self.listing(category='ingame')['items']], ['Potion button', 'Split button'])
        self.assertEqual([c['name'] for c in self.listing(query='coin')['items']], ['Coin icon'])
