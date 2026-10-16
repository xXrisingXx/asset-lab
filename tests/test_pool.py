import json
import unittest
from theme import clean_plan
from lab_core import Lab
from test_theme import Base


class PoolTests(Base):
    def key(self, name): return next(r['key'] for r in self.rows(name) if r.get('primary', True))

    def names(self, **kw): return {r['name'] for r in self.lab.search(limit=120, **kw)['items']}

    def test_removed_sprite_disappears_with_its_copies_and_can_come_back(self):
        result = self.lab.pool.remove(self.key('icon-a'))
        self.assertEqual(result['records'], 2)                       # the loose copy and the one inside assets.zip
        self.assertNotIn('icon-a', self.names())
        self.assertNotIn('icon-a', self.names(tidy=True))
        self.assertEqual(self.names(removed=True), {'icon-a'})
        self.assertIn('icon-b', self.names(tidy=True))
        self.lab.pool.restore(self.key('icon-a'))
        self.assertIn('icon-a', self.names(tidy=True))
        self.assertEqual(self.names(removed=True), set())

    def test_removal_is_a_json_file_shared_by_later_workspaces(self):
        self.lab.pool.remove(self.key('icon-b'))
        file = self.root / 'removed-assets.json'
        saved = json.loads(file.read_text(encoding='utf-8'))
        self.assertEqual({e['id'] for e in saved['removed']}, {'sprite|theme4.png|icon-b'})
        self.assertEqual(saved['removed'][0]['name'], 'icon-b')
        other = Lab(self.game, self.root / 'workspace-2', self.root / 'ledgers', process_check=lambda: [])
        self.assertTrue(all(r['removed'] for r in other.catalog.rows.values() if r['name'] == 'icon-b'))
        self.assertEqual(other.pool.listing()['hidden'], 2)
        file.write_text('not json', encoding='utf-8')                # a damaged file must not stop the lab
        self.assertEqual(Lab(self.game, self.root / 'workspace-3', self.root / 'ledgers', process_check=lambda: []).pool.listing()['total'], 0)

    def test_removing_a_sheet_takes_its_sprites_and_restoring_one_brings_the_sheet_back(self):
        result = self.lab.pool.remove(self.key('item-a'), sheet=True)
        self.assertEqual(result['name'], 'items.png')
        self.assertEqual({'items.png', 'item-a', 'item-b'} & self.names(), set())
        self.assertEqual(self.names(removed=True), {'items.png', 'item-a', 'item-b'})
        self.assertEqual(len(self.lab.pool.restore(self.key('item-b'))['restored']), 1)
        self.assertEqual(self.names(removed=True), set())

    def test_themes_skip_removed_art(self):
        self.lab.pool.remove(self.key('icon-a'))
        self.lab.pool.remove(self.key('items.png'))
        self.themes.reindex()
        plan = clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)]))
        self.assertNotIn('icon-a', [s['name'] for s in self.themes.preview(plan)['samples']])
        self.assertEqual(self.run_plan(plan)['state'], 'complete')
        themed = self.lab.image(next(k for k, s in self.themes.sheets.items() if s['row']['name'] == 'theme4.png'))
        self.assertEqual(themed.getpixel((5, 5)), (230, 60, 40, 255))     # icon-a keeps its original pixels
        self.assertEqual(themed.getpixel((50, 10)), (128, 128, 128, 255))  # icon-b is themed
        self.assertNotIn('items.png', [s['row']['name'] for s in self.themes.sheets.values()])
        self.assertEqual(self.lab.revision(self.key('items.png')), 'original')


class FavoriteAndSelectionTests(Base):
    def key(self, name): return next(r['key'] for r in self.rows(name) if r.get('primary', True))

    def test_favorite_copies_do_not_replace_other_favorites_or_art(self):
        first, second = self.key('bg2.png'), self.key('icon-a')
        before = {k: self.lab.thumbnail(k) for k in (first, second)}
        self.lab.favorites.add([first])
        self.lab.favorites.add([second])
        cards = self.lab.search(favorite=True, tidy=True)['items']
        self.assertEqual({r['key'] for r in cards}, {first, second})
        self.assertEqual(len(cards), 2)
        self.assertEqual(self.lab.search(favorite=True, tidy=False)['total'], 3)
        self.assertEqual(before, {k: self.lab.thumbnail(k) for k in before})
        self.lab.favorites.discard([second])
        self.assertEqual([r['key'] for r in self.lab.search(favorite=True, tidy=True)['items']], [first])

    def test_favorites_are_a_category_that_survives_a_restart(self):
        keys = [self.key('icon-a'), self.key('bg2.png')]
        self.assertEqual(self.lab.favorites.add(keys)['records'], 3)          # icon-a has a copy in assets.zip
        self.assertEqual({r['name'] for r in self.lab.search(favorite=True)['items']}, {'icon-a', 'bg2.png'})
        self.assertEqual(self.lab.search('icon-b', favorite=True)['total'], 0)
        self.assertIn(self.key('icon-a'), self.lab.favorites.keys())
        self.assertTrue(self.lab.detail(self.key('icon-a'))['favorite'])
        again = Lab(self.game, self.root / 'workspace-2', self.root / 'ledgers', process_check=lambda: [])
        self.assertEqual(again.search(favorite=True, tidy=True)['total'], 2)
        self.assertTrue((self.root / 'favorite-assets.json').is_file())
        self.lab.favorites.discard([self.key('icon-a')])
        self.assertEqual({r['name'] for r in self.lab.search(favorite=True)['items']}, {'bg2.png'})
        # Starring a sheet does not star every sprite on it, and a favorite can still be removed from the pool.
        self.lab.favorites.add([self.key('items.png')])
        self.assertFalse(self.lab.row(self.key('item-a'))['favorite'])

    def test_theme_for_checked_assets_changes_nothing_else(self):
        sheet = next(k for k, s in self.themes.sheets.items() if s['row']['name'] == 'theme4.png')
        everything = clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)]))
        only = [self.key('icon-b')]
        preview = self.themes.preview(everything, self.themes.scope(only))
        self.assertEqual((preview['sheets'], preview['lines'][0]['regions'], preview['scoped'], preview['forced']), (1, 1, True, False))
        self.assertEqual([s['name'] for s in preview['samples']], ['icon-b'])
        job = self.themes.apply(everything, 'dim the checked one', only)
        self.assertEqual(self.wait(job)['changed'], 1)
        themed = self.lab.image(sheet)
        self.assertEqual(themed.getpixel((50, 10)), (128, 128, 128, 255))     # icon-b
        self.assertEqual(themed.getpixel((5, 5)), (230, 60, 40, 255))         # icon-a untouched
        self.assertEqual(self.lab.revision(self.key('bg2.png')), 'original')  # other sheets untouched

    def test_checked_assets_keep_an_existing_theme_around_them(self):
        sheet = next(k for k, s in self.themes.sheets.items() if s['row']['name'] == 'theme4.png')
        self.wait(self.themes.apply(clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)])), 'dim all'))
        red = clean_plan(dict(operations=[dict(targets=['everything'], color='#ff0000', color_mode='fill')]))
        self.wait(self.themes.apply(red, 'red icon-b', [self.key('icon-b')]))
        themed = self.lab.image(sheet)
        self.assertEqual(themed.getpixel((50, 10)), (255, 0, 0, 255))         # rebuilt from the original, not from the dimmed art
        self.assertEqual(themed.getpixel((5, 5)), (115, 30, 20, 255))         # the earlier theme stays on everything else
        self.assertNotEqual(self.lab.revision(self.key('bg2.png')), 'original')
        self.themes.undo()
        self.assertEqual(self.lab.image(sheet).getpixel((50, 10)), (128, 128, 128, 255))

    def test_a_step_aimed_at_other_parts_still_reaches_the_checked_assets(self):
        buttons = clean_plan(dict(operations=[dict(targets=['buttons'], brightness=0.5)]))
        scope = self.themes.scope([self.key('icon-b')])
        self.assertTrue(self.themes.forced(buttons, scope))
        preview = self.themes.preview(buttons, scope)
        self.assertEqual((preview['sheets'], preview['forced']), (1, True))
        self.assertIsNone(self.themes.scope(['not-a-key']))
        with self.assertRaisesRegex(ValueError, 'no longer in the library'): self.themes.apply(buttons, '', ['not-a-key'])

    def wait(self, job):
        import time
        for _ in range(400):
            state = self.themes.job(job['id'])
            if state['state'] != 'running': return state
            time.sleep(0.02)
        self.fail('theme job did not finish')


if __name__ == '__main__': unittest.main()
