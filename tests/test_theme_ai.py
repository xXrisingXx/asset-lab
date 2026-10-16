from unittest.mock import patch
import design
import theme
from test_theme import Base


class OpenEndedRequests(Base):
    """'Make it look cool' is a request for a bold look, not for cool color tones; the Image AI switch decides drawing."""

    def setUp(self):
        super().setUp()
        self.themes.designer = design.Designer(self.lab, self.themes)

    def chat(self, text, draw='auto'):
        with patch.object(theme, 'models', return_value=[]):
            return self.themes.chat(text, draw=draw)

    def test_cool_alone_is_a_bold_look_not_a_hue_nudge(self):
        r = self.chat('make the game look cool', draw='off')
        self.assertTrue(theme.changes_anything(r['plan']))
        self.assertIn('You left the look open', r['plan']['reply'])
        # A real look (gradients, outlines, a gold finish...), whichever one was picked, not a hue nudge.
        self.assertTrue(any(op['gradient'] or op['rim_color'] or op['color'] or op['glow_color'] for op in r['plan']['operations']))
        self.assertFalse(all(set(k for k, v in op.items() if v not in ('', [], 0, 0.0, 1.0, False, 'none', 'tint', 'linear')) <= {'targets', 'match', 'exclude', 'hue_shift', 'saturation', 'opaque'}
                             for op in r['plan']['operations']))
        with patch.object(theme.random, 'choice', side_effect=lambda seq: seq[2]):   # "glossy gold buttons…": colors, no gradient
            self.assertTrue(theme.changes_anything(self.chat('make the game look cool', draw='off')['plan']))
        self.assertNotIn('design', r)                                       # Image AI off: no drawing

    def test_cool_colors_still_shift_the_hue(self):
        plan = theme.rule_plan('cool colors on the buttons')
        self.assertTrue(any(op['hue_shift'] > 0 for op in plan['operations']))

    def test_named_parts_keep_the_open_look_on_them(self):
        r = self.chat('make the buttons look sick', draw='off')
        self.assertEqual({t for op in r['plan']['operations'] for t in op['targets']}, {'buttons'})

    def test_asking_for_image_generation_draws_and_is_not_a_sprite_name(self):
        r = self.chat('make the game look cool using image generation.')
        self.assertTrue(r.get('design') and r['design']['targets'])
        self.assertFalse(r['design'].get('optional'))
        names = {n for op in r['plan']['operations'] for n in op.get('match') or []}
        self.assertFalse({n for n in names if 'image' in n})               # "image" was about the tool
        self.assertNotIn('image', self.themes.named('use image generation on the buttons')[0])

    def test_image_ai_on_draws_and_off_never_offers(self):
        on = self.chat('make the game look cool', draw='on')
        self.assertTrue(on.get('design') and on['design']['targets'])
        off = self.chat('spooky theme', draw='off')
        self.assertNotIn('design', off)
        hat = self.chat('put a party hat on the title', draw='off')
        self.assertNotIn('design', hat)
        self.assertIn('Image AI is off', hat['note'])

    def test_glow_up_is_open_ended_not_a_glow_tag(self):
        r = self.chat('give it a glow up', draw='off')
        self.assertIn('You left the look open', r['plan']['reply'])
