import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from fixtures import make_game
from hide_fixture import add_hide_controls
from lab_core import Lab, file_hash
from pictures import Pictures, presets


class HidePresets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = add_hide_controls(make_game(self.root))
        self.lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.pictures = Pictures(self.lab)

    def test_both_presets_build_install_and_restore_without_touching_markers(self):
        before = {p: file_hash(p) for p in self.game.rglob('*') if p.is_file()}
        original = {p: np.array(Image.open(p).convert('RGBA')) for p in (self.game / 'assets').glob('cleanup-*.png')}
        for pid, name in [('hide-menu-circle', 'button-circle'), ('hide-grey-panels', 'rounded-bg')]:
            preset = next(p for p in presets() if p['id'] == 'preset:' + pid)
            self.assertIn('share', preset['description'])
            self.assertEqual({r['name'] for r in self.pictures.hide_rows(preset['id'])}, {name})
            self.assertEqual(self.pictures.apply(preset['id'], 'set:cleanup')['count'], 3)
        # Applying presets only stages artwork until the player installs the build.
        self.assertEqual(before, {p: file_hash(p) for p in before})
        self.assertIsNone(self.lab.active())
        build = self.lab.build()
        self.lab.install(build['id'])
        for p, old in original.items():
            now = np.array(Image.open(p).convert('RGBA'))
            self.assertFalse(now[:, :64, 3].any())
            np.testing.assert_array_equal(now[:, 64:], old[:, 64:])
        self.assertEqual({p: h for p, h in before.items() if p not in original},
                         {p: file_hash(p) for p in before if p not in original})
        self.lab.restore()
        self.assertEqual(before, {p: file_hash(p) for p in before})

    def test_legacy_and_removed_copies_are_left_out(self):
        rows = self.pictures.hide_rows('preset:hide-menu-circle')
        rows[0]['legacy'] = True
        rows[1]['removed'] = True
        self.assertEqual(self.pictures.hide_rows('preset:hide-menu-circle'), [rows[2]])
        self.assertEqual(self.pictures.apply('preset:hide-menu-circle', 'set:cleanup')['count'], 1)
