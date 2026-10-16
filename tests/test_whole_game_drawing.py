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
