import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

import button_sets
import atlas_audit
import community_themes
import design
from fixtures import make_game
from hide_fixture import add_toolbar_controls, TOOLBAR_NAMES
from lab_core import Lab, file_hash
from pictures import Pictures
import simple_library


class ToolbarControls(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game = add_toolbar_controls(make_game(self.root), ('theme', 'theme2', 'theme4'))
        self.lab = self.open_lab()
        self.pics = Pictures(self.lab)

    def open_lab(self):
        return Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])

    def test_all_screenshot_controls_are_available_in_button_sets_library_and_export(self):
        for key in ('set:ingame-buttons', 'set:ingame-phone', 'set:ingame-pc'):
            names = {r['name'] for r in self.pics.button_rows(key)}
            self.assertEqual(names, set(TOOLBAR_NAMES))
        self.assertTrue(set(TOOLBAR_NAMES) <= design.INGAME_CONTROLS)
        designer = design.Designer(self.lab, None)
        cards = simple_library.listing(designer, category='ingame')['items']
        self.assertEqual({n for card in cards for n in [self.lab.row(card['key'])['name']]}, set(TOOLBAR_NAMES))
        drawn = self.pics.drawn('preset:delta-tron-ingame')
        self.assertTrue(set(TOOLBAR_NAMES) <= drawn.keys())
        self.assertEqual(len({drawn[n].tobytes() for n in TOOLBAR_NAMES}), len(TOOLBAR_NAMES))
        with zipfile.ZipFile(io.BytesIO(community_themes.export('tron-delta', self.pics))) as z:
            for name in TOOLBAR_NAMES:
                self.assertTrue(any(p.endswith('/' + name + '.png') for p in z.namelist()), name)

    def test_old_default_exclusions_are_repaired_but_player_removals_stick(self):
        names = ('icon-pause-button', 'icon-ingame-chat', 'icon-ingame-chat-desktop')
        for row in self.lab.catalog.rows.values():
            if row['name'] in names:
                ident = self.lab.pool.ident(row)
                item = dict(id=ident, name=row['name'], kind=row['kind'], sheet=self.lab.pool.sheet_name(row))
                if row['name'] != 'icon-ingame-chat': item['by'] = 'Asset Lab UI map'
                self.lab.pool.entries[ident] = item
        self.lab.pool.store()
        record = self.root / 'defaults-applied.json'
        doc = json.loads(record.read_text())
        doc['applied'] = [s for s in doc['applied'] if not s.startswith('restored:')]
        record.write_text(json.dumps(doc))
        lab = self.open_lab()
        for row in lab.catalog.rows.values():
            if row['name'] in names:
                self.assertEqual(row['removed'], row['name'] == 'icon-ingame-chat')
        # A subsequent intentional removal is not reversed next time the app opens.
        pause = next(r for r in lab.catalog.rows.values() if r['name'] == 'icon-pause-button')
        lab.pool.add([pause['key']])
        self.assertTrue(self.open_lab().row(pause['key'])['removed'])

    def test_encoded_toolbar_names_survive_the_stale_atlas_heuristic(self):
        # Reproduce a stale atlas with many overlapping obsolete rectangles and an encoded desktop icon name.
        from test_theme import stale_game
        from catalog import Catalog
        game = stale_game(self.root / 'stale')
        xml = game / 'assets/theme4.xml'
        xml.write_text(xml.read_text().replace('name="old-0"', 'name="icon-ingame-potion-desktop"'))
        catalog = Catalog(game, lambda source: game / source)
        atlas_audit.annotate(catalog, game)
        desktop = [r for r in catalog.rows.values() if r['name'] == 'icon-ingame-potion-desktop']
        self.assertTrue(desktop)
        self.assertTrue(all(not r['legacy'] for r in desktop))
        self.assertTrue(any(r.get('legacy') for r in catalog.rows.values() if r['name'].startswith('old-')))

    def test_each_remove_option_changes_only_its_control(self):
        options = {'potion': {'icon-ingame-potion', 'icon-ingame-potion-desktop'},
                   'block': {'icon-report-abuse'}, 'chat': {'icon-ingame-chat', 'icon-ingame-chat-desktop'},
                   'pause': {'icon-pause-button'}, 'toolbar': set(TOOLBAR_NAMES)}
        for name, expected in options.items():
            with self.subTest(name=name), patch.object(self.lab, 'commit') as commit:
                result = self.pics.apply('preset:hide-hud-' + name, 'set:cleanup')
                self.assertEqual(result['count'], 3 * len(expected))
                self.assertEqual({self.lab.row(c.args[0])['name'] for c in commit.call_args_list}, expected)
                self.assertTrue(all(c.args[1].getchannel('A').getbbox() is None for c in commit.call_args_list))

    def test_tron_plus_black_override_installs_and_restores_without_touching_other_pixels(self):
        before = {p: file_hash(p) for p in self.game.rglob('*') if p.is_file()}
        originals = {p: np.array(Image.open(p).convert('RGBA')) for p in (self.game / 'assets').glob('theme*.png')}
        self.assertEqual(self.pics.apply('preset:delta-tron-ingame', 'set:ingame-buttons')['count'], 18)
        before_black = {r['key']: self.lab.image(r['key']).tobytes() for r in self.pics.button_rows('set:ingame-buttons')}
        self.assertEqual(self.pics.apply('preset:black-block-player', '')['count'], 3)
        for row in self.pics.button_rows('set:ingame-buttons'):
            im = self.lab.image(row['key'])
            if row['name'] == 'icon-report-abuse':
                pixels = np.array(im)
                self.assertTrue(pixels[..., 3].any())
                opaque = pixels[..., 3] > 200
                self.assertGreater(((pixels[..., :3].max(axis=2) < 10) & opaque).sum(), opaque.sum() * 0.5)
                self.assertGreater(((pixels[..., :3].min(axis=2) > 200) & opaque).sum(), opaque.sum() * 0.04)
            else: self.assertEqual(im.tobytes(), before_black[row['key']])
        self.assertEqual(before, {p: file_hash(p) for p in before})
        self.lab.install(self.lab.build()['id'])
        for p, old in originals.items():
            now = np.array(Image.open(p).convert('RGBA'))
            np.testing.assert_array_equal(now[:, 384:], old[:, 384:])
        self.assertEqual(before[self.game / 'Mitosis.exe'], file_hash(self.game / 'Mitosis.exe'))
        self.lab.restore()
        self.assertEqual(before, {p: file_hash(p) for p in before})
