import io
import json
import zipfile
from unittest.mock import patch
import numpy as np
from PIL import Image
from builds import Builds
from design import Designer
from test_theme import Base
import community_themes as community
import pictures


class CommunityThemes(Base):
    def setUp(self):
        super().setUp()
        self.builds = Builds(self.lab)
        self.designer = Designer(self.lab, self.themes)
        self.pics = self.themes.pictures
        self.pics.sets = dict(menu=self.designer.menu_backgrounds, arena=self.designer.arena_backgrounds, food=self.designer.food_kinds)

    def test_grid_has_exact_edges_and_uniform_repeated_spacing(self):
        a = np.asarray(Image.open(pictures.PRESETS / 'community/tron-delta/grid.png'))
        self.assertTrue(np.array_equal(a[0], a[-1]))
        self.assertTrue(np.array_equal(a[:, 0], a[:, -1]))
        self.assertTrue(np.array_equal(a[:, :128], a[:, 128:256]))
        self.assertTrue(np.array_equal(a[:128], a[128:256]))

    def test_credited_bundle_and_all_foods_are_available(self):
        theme = community.find('tron-delta')
        self.assertEqual(theme['credit'], 'Delta')
        self.assertEqual(len(theme['parts']), 8)
        ids = {p['id']: p for p in pictures.presets()}
        for part in theme['parts']:
            self.assertEqual(part['status'], 'approved')
            self.assertIn(part['preset'], ids)
            with Image.open(pictures.PRESETS / pictures.listed()[0][part['preset'][7:]]['file']) as im:
                im.verify()
        self.assertEqual(sum(ids[p['preset']]['kind'] == 'food' for p in theme['parts']), 3)

    def test_apply_preserves_previous_work_and_never_installs(self):
        old = self.builds.index['active']
        original = json.loads(self.lab.state_path.read_text())
        with patch.object(self.lab, 'install', side_effect=AssertionError('Must not install')):
            result = community.apply('tron-delta', self.pics, self.builds)
        self.assertNotEqual(result['build'], old)
        self.assertGreater(result['count'], 0)
        self.assertTrue(self.lab.staged())
        self.builds.switch(old)
        self.assertEqual(self.lab.state['edits'], original['edits'])
        self.assertEqual(self.lab.state['staged'], original['staged'])

    def test_failed_part_restores_previous_build(self):
        old = self.builds.index['active']
        count = len(self.builds.index['builds'])
        with patch.object(self.pics, 'apply', side_effect=OSError('Disk full')):
            with self.assertRaisesRegex(OSError, 'Disk full'):
                community.apply('tron-delta', self.pics, self.builds)
        self.assertEqual(self.builds.index['active'], old)
        self.assertEqual(len(self.builds.index['builds']), count)
        self.assertFalse(self.lab.staged())

    def test_invalid_theme_and_food_assignment_do_not_change_build(self):
        old = self.builds.index['active']
        for tid, foods in [('missing', None), ('tron-delta', {'set:food:0': 'preset:purple-virus'}), ('tron-delta', {'set:everywhere':'preset:delta-tron-food-1'}), ('tron-delta', [])]:
            with self.assertRaises(ValueError): community.apply(tid, self.pics, self.builds, foods)
        self.assertEqual(old, self.builds.index['active'])

    def test_food_choice_is_sent_to_its_selected_slot(self):
        food = dict(name='Food 1', preset='preset:delta-tron-food-1', target='set:food:0', status='approved')
        other = dict(food, name='Food 2', preset='preset:delta-tron-food-2', target='set:food:1')
        with patch.object(community, 'find', return_value=dict(name='Test', credit='Delta', parts=[food, other])), \
             patch.object(community, 'supported', return_value=[{}]), patch.object(self.pics, 'apply', return_value={'count': 1}) as apply:
            community.apply('tron-delta', self.pics, self.builds, {'set:food:0':'preset:delta-tron-food-2'})
        self.assertEqual(apply.call_args_list[0].args, ('preset:delta-tron-food-2', 'set:food:0'))

    def test_export_keeps_credit_and_manifest(self):
        with zipfile.ZipFile(io.BytesIO(community.export('tron-delta', self.pics))) as z:
            self.assertIsNone(z.testzip())
            self.assertIn('Credit: Delta', z.read('CREDIT.txt').decode())
            self.assertEqual(len(json.loads(z.read('theme.json'))['parts']), 8)

    def test_curated_symbols_cover_all_controls_and_keep_arrow_direction(self):
        import button_sets
        preset = self.pics.button_set('preset:delta-tron-menu')
        drawn = self.pics.drawn(preset['id'])
        self.assertEqual(set(drawn), set(button_sets.MENU + button_sets.PHONE + button_sets.PC + button_sets.HUD))
        # A locally themed original may be a circle: it must not rotate a hand-authored arrow.
        with patch.object(button_sets, 'orient', side_effect=AssertionError('Do not infer this direction')):
            result = button_sets.button('forward-icon', Image.new('RGBA', (128,128), 'white'), preset['look'],
                                        self.pics.material(preset['id']), drawn=drawn['forward-icon'])
        self.assertEqual(result.size, (128,128))
        self.assertGreater(result.getchannel('A').getextrema()[1], 0)
