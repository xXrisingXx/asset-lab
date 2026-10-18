"""The asset library's filters in headless Edge: the category and type selects work together (Audio lists sounds, a
picture category lists pictures, and no change leaves an empty list by surprise), every type option lists what its name
says, and "Show all sprites on this sheet" lists one sheet's sprites behind a chip with a ✕ back to the whole library."""
from harness import EditorTestCase
from test_audio import sound_game


class LibraryFilters(EditorTestCase):
    game_maker = staticmethod(sound_game)

    def setUp(self):
        super().setUp()
        b = self.browser
        b.eval("window.LabView && LabView.set('advanced'); window.LabTour && LabTour.end(true); S.parent=''; true")
        b.eval("const d=document.getElementById('themeDrawer'); if (d && !d.hidden) document.getElementById('themeClose')?.click(); true")   # it would cover Edit & Inspect
        b.eval("document.getElementById('search').value=''; document.getElementById('origin').value=''; true")
        self.choose('#group', '')
        self.choose('#kind', 'visual')

    def choose(self, selector, value):
        """Pick an option as the player does (change event) and wait for the list to load."""
        b = self.browser
        seq = b.eval('S.query')
        b.set_value(selector, value)
        b.wait_for(f"S.query > {seq} && !document.querySelector('#busy:not([hidden])')")
        b.eval("await new Promise(r => setTimeout(r, 300)); true")

    def state(self):
        return self.browser.eval("({group: $('group').value, kind: $('kind').value, total: parseInt($('resultCount').textContent.replace(/\\D/g, '')) || 0,"
                                 " disabled: [...$('kind').options].filter(o => o.disabled).map(o => o.value),"
                                 " cards: [...document.querySelectorAll('#assetList .asset-card')].length})")

    def test_type_options_are_plain_words_and_list_what_they_say(self):
        b = self.browser
        names = b.eval("Object.fromEntries([...$('kind').options].map(o => [o.value, o.textContent]))")
        self.assertEqual(names, {'visual': 'All pictures', 'image': 'Whole pictures', 'sprite': 'Sprites (parts of sheets)',
                                 'glyph': 'Font letters', 'audio': 'Sounds', 'metadata': 'Data files'})
        for kind, wanted in (('image', {'image'}), ('sprite', {'sprite'}), ('glyph', {'glyph'}), ('audio', {'audio'}), ('metadata', {'metadata'}),
                             ('visual', {'image', 'sprite', 'glyph'})):
            found = self.server.get('/api/assets', kind=kind, limit=120)
            self.assertGreater(found['total'], 0, kind)
            self.assertEqual({r['kind'] for r in found['items']} - wanted, set(), kind)
            self.assertEqual(found['kinds'][kind], found['total'], kind)
        # "All pictures" lists whole pictures and sprites side by side by name, not every sheet first.
        first = self.server.get('/api/assets', kind='visual', limit=12)['items']
        self.assertEqual({'image', 'sprite'} - {r['kind'] for r in first}, set())
        for kind in ('image', 'sprite'):
            self.choose('#kind', kind)
            s = self.state()
            self.assertEqual(s['kind'], kind)
            self.assertGreater(s['cards'], 0, kind)

    def test_audio_category_lists_sounds_and_a_picture_category_lists_pictures(self):
        self.choose('#group', 'Audio')
        s = self.state()
        self.assertEqual((s['kind'], s['total'] > 0), ('audio', True))
        self.assertTrue(self.browser.eval("[...document.querySelectorAll('#assetList .asset-card .thumb')].every(t => t.textContent === '♫')"))
        self.assertIn('visual', s['disabled'])                       # no pictures in Audio: the option says so
        self.choose('#group', '')                                    # Sounds was chosen for the category: All categories goes back
        self.assertEqual(self.state()['kind'], 'visual')
        self.choose('#kind', 'audio')                                # the player's own choice stays with All categories...
        self.assertEqual(self.state()['kind'], 'audio')
        self.choose('#group', 'Backgrounds')                         # ...but a picture category lists pictures
        s = self.state()
        self.assertEqual(s['kind'], 'visual')
        self.assertGreater(s['total'], 0)

    def test_an_empty_combination_moves_the_type_to_one_with_assets(self):
        self.choose('#kind', 'sprite')
        self.assertGreater(self.state()['total'], 0)
        self.choose('#group', 'Backgrounds')                         # whole pictures only: no sprites there
        s = self.state()
        self.assertEqual(s['kind'], 'visual')
        self.assertGreater(s['total'], 0)
        self.assertIn('sprite', s['disabled'])
        self.assertNotIn('image', s['disabled'])

    def test_browsing_a_sheet_shows_a_chip_and_keeps_search_and_paging(self):
        b = self.browser
        b.select_asset('button-up-skin')
        b.wait_for("!document.getElementById('sheetButton').hidden")
        self.assertEqual(b.text('#sheetButton'), 'Show all sprites on this sheet →')
        b.eval("document.getElementById('sheetButton').scrollIntoView(); true")
        b.click_selector('#sheetButton')
        b.wait_for("!document.getElementById('sheetChip').hidden")
        self.assertEqual(b.text('#sheetChipText'), 'Sprites in metalworks_mobile.png')
        sheet = self.server.asset_key('metalworks_mobile.png')
        names = b.eval("[...document.querySelectorAll('#assetList .asset-card')].map(c => c.dataset.key)")
        rows = {r['key']: r for r in self.server.assets(parent=sheet)}
        self.assertTrue(names)
        self.assertEqual(set(names), set(rows))
        seq = b.eval('S.query')
        b.set_value('#search', 'down')                               # search stays inside the sheet
        b.wait_for(f"S.query > {seq} && document.querySelectorAll('#assetList .asset-card').length === 1")
        self.assertFalse(b.eval("document.getElementById('sheetChip').hidden"))
        self.assertEqual(b.eval("document.querySelector('#assetList .asset-card .asset-label').textContent"), 'button-down-skin')
        b.set_value('#search', '')
        b.wait_for("document.querySelectorAll('#assetList .asset-card').length > 1")
        b.click_selector('#clearParent')
        b.wait_for("document.getElementById('sheetChip').hidden && !S.parent")
        self.assertGreater(self.state()['total'], len(rows))

    def test_a_picture_without_sprites_offers_no_sheet_to_browse(self):
        b = self.browser
        b.select_asset('bg2.png')
        self.assertTrue(b.eval("document.getElementById('sheetButton').hidden"))
        b.click(*b.pixel_center(32, 32), button='right')
        b.wait_for("!document.getElementById('contextMenu').hidden")
        self.assertTrue(b.eval("document.querySelector('#contextMenu [data-action=sheet]').hidden"))
        b.key('Escape')
        b.select_asset('metalworks_mobile.png')
        b.wait_for("!document.getElementById('sheetButton').hidden")
        self.assertEqual(b.text('#sheetButton'), 'Show this sheet’s sprites →')
