"""'Make the game look cool' with drawing: everything players look at is redrawn, with a time estimate the lab learns."""
from unittest.mock import patch
import design
import theme
from test_theme import Base


class WholeGame(Base):
    def setUp(self):
        super().setUp()
        self.designer = self.themes.designer = design.Designer(self.lab, self.themes)

    def test_learned_drawing_time_is_kept(self):
        self.assertEqual(self.designer.status()['seconds_per_drawing'], design.DEFAULT_SECONDS)
        self.designer.learn_time(200, 2)                       # 100 s a drawing on this PC
        again = design.Designer(self.lab, self.themes)
        self.assertGreater(again.per_drawing, design.DEFAULT_SECONDS)
        self.assertLess(again.per_drawing, 100)                # a running estimate, not the last job alone

    def test_cool_with_image_ai_on_offers_everything_with_its_time(self):
        with patch.object(theme, 'models', return_value=[]):
            r = self.themes.chat('make the game look cool', draw='on')
        self.assertTrue(r['design']['targets'])
        self.assertFalse(r['design'].get('optional'))
        self.assertEqual(r['design']['seconds_per_drawing'], self.designer.per_drawing)
        with patch.object(theme, 'models', return_value=[]):
            auto = self.themes.chat('make the game look cool')
        self.assertTrue(auto['design'].get('optional'))       # Auto: colors now, the redraw is offered

    def test_badges_are_never_redrawn(self):
        names = {r['name'] for r in self.designer.whole_game_rows()}
        self.assertFalse(names & design.KEEP_AS_IS)

    def test_a_whole_game_redesign_is_only_what_players_care_about(self):
        rows = {}
        def add(name, label='', sheet='theme.png'):
            parent = 'sheet:' + sheet
            rows.setdefault(parent, dict(key=parent, name=sheet, editable=True, source='assets/' + sheet))
            key = f'{sheet}:{name}'
            rows[key] = dict(key=key, name=name, label=label, parent=parent, editable=True, source='assets/' + sheet, width=64, height=64, rect=[0, 0, 64, 64])
        for name, label in (('icon-play', 'Play button (Main menu)'), ('icon-menu-settings', 'Settings button icon (Main menu)'), ('back-icon', 'Back arrow (Menus)'),
                            ('button-split', 'Split button (in a match, phones)'), ('button-explain-eject', 'Eject button (in a match, PC)'), ('joystick', ''),
                            ('logo', 'MITOSIS title logo (Main menu)'), ('icon-coin', 'Coin icon (Main menu)'), ('icon-guild', ''), ('loader', ''), ('icon-iphone', 'App Store (Main menu)')):
            add(name, label)
        add('icon-play', sheet='theme4.png')                                    # the other copy of play gets the drawing too
        rows['virus'] = dict(key='virus', name='virus.png', editable=True, source='assets/virus.png', width=64, height=64)   # a loose file
        with patch.object(self.lab.catalog, 'rows', rows), patch.object(self.designer, 'menu_backgrounds', return_value=[{'name': 'bg2.png'}]), \
                patch.object(self.designer, 'arena_backgrounds', return_value=[{'name': 'worldbg.png'}]), patch.object(self.designer, 'food_rows', return_value=[{'name': 'food1'}]):
            names = [r['name'] for r in self.designer.whole_game_rows()]
        self.assertEqual(names, ['bg2.png', 'worldbg.png', 'virus.png', 'icon-play', 'icon-play', 'back-icon', 'icon-coin', 'icon-menu-settings',
                                 'button-explain-eject', 'button-split', 'joystick', 'food1'])   # the coin too, players asked
