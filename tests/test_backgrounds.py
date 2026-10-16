"""The main menu background: recolors cover the game's teal completely, and pictures are drawn as one seamless tile."""
import unittest
from unittest.mock import patch
import numpy as np
import design
import theme
from test_theme import Base


def pattern(size=64):
    """Like bg2.png: faint black triangles (alpha 0-16) that the game draws over its teal."""
    a = np.zeros((size, size, 4), np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    a[..., 3] = np.where((xx + yy) % 32 < 16, 16, 4)
    return a


def op(**k): return dict(theme.clean_plan({'operations': [dict(targets=['menu_background'], **k)]})['operations'][0], opaque=True)


class OpaqueTile(unittest.TestCase):
    def test_a_color_fills_the_tile_and_keeps_the_pattern(self):
        out = theme.shade(pattern(), op(color='#5a189a', color_mode='fill'))
        self.assertTrue((out[..., 3] == 255).all())                      # nothing of the teal shows through
        rgb = out[..., :3].astype(int)
        self.assertGreater(rgb[..., 2].mean(), rgb[..., 1].mean() + 40)  # purple, not green
        self.assertGreater(np.ptp(rgb[..., 0]), 5)                       # the triangles are still there as a shade

    def test_dark_colors_show_the_pattern_lighter(self):
        out = theme.shade(pattern(), op(color='#000000', color_mode='fill')).astype(int)
        self.assertGreater(out[..., :3].max(), 10)

    def test_a_gradient_tile_repeats_without_a_seam(self):
        out = theme.shade(np.zeros((64, 64, 4), np.uint8), op(gradient=['#000000', '#ff0000'], color_mode='fill', gradient_angle=0)).astype(int)
        self.assertLessEqual(np.abs(out[:, 0] - out[:, -1]).max(), 12)    # left and right edges meet
        self.assertGreater(out[:, 32, 0].mean(), out[:, 0, 0].mean() + 100)   # and the middle is the far color


class ThemesFillTheBackground(unittest.TestCase):
    def test_a_theme_color_fills_a_faint_tile_whatever_the_step_targets(self):
        """'Design a BMW theme' colors everything: on the faint menu tile that must show, not tint invisible pixels."""
        plain = dict(theme.clean_plan({'operations': [dict(targets=['everything'], color='#1c69d4', color_mode='colorize')]})['operations'][0])
        out = theme.shade(pattern(256), plain)
        self.assertTrue((out[..., 3] == 255).all())
        self.assertGreater(out[..., 2].mean(), out[..., 1].mean() + 30)        # blue, not the game's teal
        icon = np.zeros((256, 256, 4), np.uint8); icon[64:192, 64:192] = 255   # ordinary art keeps its transparency
        self.assertEqual(int(theme.shade(icon, plain)[0, 0, 3]), 0)


class BackgroundRequests(Base):
    def setUp(self):
        super().setUp()
        self.themes.designer = design.Designer(self.lab, self.themes)

    def test_scene_or_color(self):
        for text in ('starry night sky background', 'anime background', 'make the background spooky', 'a city at night as the background'):
            self.assertTrue(theme.scene_request(text, self.themes.named), text)
        for text in ('make the background purple', 'make the background darker', 'i hate the green background', 'black background'):
            self.assertFalse(theme.scene_request(text, self.themes.named), text)

    def test_a_background_color_is_planned_as_a_filled_tile(self):
        with patch.object(theme, 'models', return_value=[]):
            r = self.themes.chat('make the main menu background purple', draw='off')
        ops = r['plan']['operations']
        self.assertTrue(ops)
        self.themes.compile(r['plan'])
        self.assertTrue(any(o.get('opaque') for o in ops))
