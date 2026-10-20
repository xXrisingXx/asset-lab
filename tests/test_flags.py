"""Flags drawn exactly, for Image AI to copy: "make the virus the Norway flag"."""
import unittest

import numpy as np

import flags


class Flags(unittest.TestCase):
    def test_every_flag_draws_in_its_own_proportions(self):
        for name, (ratio, _) in flags.FLAGS.items():
            im = flags.draw(name, 60)
            self.assertEqual(im.height, 60, name)
            self.assertLessEqual(abs(im.width / im.height - ratio), 0.05, name)
            self.assertGreater(len(set(im.convert('RGB').getdata())), 1, name)

    def test_norway_is_red_with_a_blue_cross_left_of_the_middle(self):
        im = np.asarray(flags.draw('norway', 160).convert('RGB')).astype(int)
        h, w = im.shape[:2]
        self.assertGreater(im[10, w - 10, 0], 150)                                  # red field
        blue_cols = [x for x in range(w) if im[10, x, 2] > 80 and im[10, x, 0] < 60]
        self.assertTrue(blue_cols)
        self.assertLess(np.mean(blue_cols), w * 0.45)                                # the upright toward the hoist
        self.assertGreater(im[h // 2, w - 5, 2], 80)                                 # the bar right across

    def test_requests_name_flags_in_many_ways(self):
        self.assertEqual(flags.find('make the virus the Norway flag'), 'norway')
        self.assertEqual(flags.find('put the flag of Japan on the coin'), 'japan')
        self.assertEqual(flags.find('a brazilian flag ball'), 'brazil')
        self.assertEqual(flags.find('USA flag'), 'united states')
        self.assertEqual(flags.find('make everything brazil colors'), '')           # a color theme, not a flag
        self.assertEqual(flags.find('make the play button gold'), '')
