from PIL import Image
from builds import Builds
from lab_core import Lab, png
from test_theme import Base


class BuildTests(Base):
    """Builds: versions of the mod to switch between, each with its own edits, mod list and Theme AI chats."""

    def setUp(self):
        super().setUp()
        self.builds = Builds(self.lab)

    def key(self, name, sprite=True):
        return next(r['key'] for r in self.lab.catalog.rows.values() if r['name'] == name and (r['kind'] == 'sprite') == sprite and r['editable'])

    def paint(self, key, color):
        im = Image.new('RGBA', self.lab.image(key).size, color)
        self.lab.save(key, png(im), self.lab.revision(key))

    def test_existing_work_becomes_the_first_build(self):
        self.paint(self.key('icon-a'), (1, 2, 3, 255))
        listing = Builds(self.lab).listing()
        self.assertEqual([b['name'] for b in listing['builds']], ['My build'])
        self.assertEqual(listing['builds'][0]['edits'], 1)

    def test_start_over_switch_back_and_copy(self):
        a = self.key('icon-a')
        self.paint(a, (1, 2, 3, 255))
        first = self.builds.index['active']
        self.builds.create()                                           # Start over: an empty build
        self.assertEqual(self.lab.staged(), []); self.assertIsNone(self.lab.current(a))
        self.paint(a, (9, 9, 9, 255))
        second = self.builds.index['active']
        self.builds.switch(first)
        self.assertEqual(self.lab.image(a).getpixel((0, 0)), (1, 2, 3, 255))
        self.assertEqual([s['key'] for s in self.lab.staged()], [a])
        self.builds.switch(second)
        self.assertEqual(self.lab.image(a).getpixel((0, 0)), (9, 9, 9, 255))
        listing = self.builds.create('Spooky v2', copy_of=first)        # a copy of the first build
        self.assertEqual(self.lab.image(a).getpixel((0, 0)), (1, 2, 3, 255))
        self.assertEqual([b['name'] for b in listing['builds']], ['My build', 'Build 2', 'Spooky v2'])
        # The active build survives a restart of the lab.
        again = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.assertEqual(Builds(again).index['active'], listing['active'])
        self.assertEqual(again.image(a).getpixel((0, 0)), (1, 2, 3, 255))

    def test_a_copied_build_keeps_its_own_layers(self):
        """Build A saves two layers, B (a copy) changes them to four without changing the picture: A still has two."""
        import base64, io, json
        key = self.key('icon-a')
        im = self.lab.image(key); im.paste((0, 0, 255, 255), (0, 0, 4, 4))
        flat = png(im)
        def doc(n):
            buf = io.BytesIO(); im.save(buf, 'PNG'); layer = base64.b64encode(buf.getvalue()).decode()
            return dict(version=1, width=im.width, height=im.height, activeIndex=0, target='pixels',
                        layers=[dict(id=f'L{i}', name=f'Layer {i}', kind='pixel', png=layer) for i in range(n)])
        self.lab.save(key, flat, 'original', None, doc(2))
        a = self.builds.index['active']
        self.builds.create('B', copy_of=a)
        self.lab.save(key, flat, self.lab.revision(key), None, doc(4))       # same picture, more layers
        self.assertEqual(len(json.loads(self.lab.layers_file(key))['layers']), 4)
        self.builds.switch(a)
        self.assertEqual(len(json.loads(self.lab.layers_file(key))['layers']), 2)
        # B's history still holds that revision, so A's next layers-only save gets its own; one only A has is changed in place.
        shared = self.lab.revision(key)
        self.lab.save(key, flat, shared, None, doc(3))
        own = self.lab.revision(key)
        self.assertNotEqual(own, shared)
        self.lab.save(key, flat, own, None, doc(5))
        self.assertEqual(self.lab.revision(key), own)
        self.assertEqual(len(json.loads(self.lab.layers_file(key))['layers']), 5)

    def test_rename_and_delete(self):
        first = self.builds.index['active']
        self.builds.create('Other')
        with self.assertRaises(ValueError): self.builds.delete(self.builds.index['active'])   # not the open one
        self.builds.rename(first, '  Neon   night ')
        listing = self.builds.delete(first)
        self.assertEqual([b['name'] for b in listing['builds']], ['Other'])
        with self.assertRaises(ValueError): self.builds.switch(first)

    def test_theme_undo_list_follows_its_build(self):
        (self.lab.root / 'themes.json').write_text('{"batches": [{"id": "x"}]}')
        first = self.builds.index['active']
        self.builds.create()
        self.assertFalse((self.lab.root / 'themes.json').exists())
        self.builds.switch(first)
        self.assertIn('"x"', (self.lab.root / 'themes.json').read_text())

    def test_chats_are_saved_per_build(self):
        self.builds.save_chat('a' * 16, [{'t': 'user', 'text': 'spooky'}], [], 'spooky')
        self.builds.save_chat('b' * 16, [{'t': 'user', 'text': 'gold'}], [], 'gold')
        self.assertEqual([c['title'] for c in self.builds.chats()['chats']], ['gold', 'spooky'])
        self.assertEqual(self.builds.chat('a' * 16)['entries'][0]['text'], 'spooky')
        self.assertEqual(self.builds.chats()['active'], 'a' * 16)
        first = self.builds.index['active']
        self.builds.create()
        self.assertEqual(self.builds.chats()['chats'], [])              # a new build starts with no chats
        self.builds.switch(first)
        self.assertEqual(len(self.builds.delete_chat('b' * 16)['chats']), 1)
        with self.assertRaises(ValueError): self.builds.chat('../../x')


class RevertTests(Base):
    """Revert to original: an asset, its copies and its spot on a themed sheet go back to the game's art."""

    def test_revert_an_edit_keeps_history(self):
        key = next(r['key'] for r in self.lab.catalog.rows.values() if r['name'] == 'icon-a' and r['kind'] == 'sprite')
        im = self.lab.image(key); im.paste((0, 0, 255, 255), (0, 0, 4, 4))
        self.lab.save(key, png(im), 'original')
        self.assertTrue(self.lab.detail(key)['changed'])
        self.lab.revert([key])
        self.assertIsNone(self.lab.current(key)); self.assertEqual(self.lab.staged(), [])
        self.assertFalse(self.lab.detail(key)['changed'])
        self.lab.step(key, 'original', 1)                                # Redo brings it back
        self.assertEqual(self.lab.image(key).getpixel((0, 0)), (0, 0, 255, 255))

    def test_revert_one_sprite_on_a_themed_sheet(self):
        a, b = (next(r for r in self.lab.catalog.rows.values() if r['name'] == n and r['kind'] == 'sprite') for n in ('icon-a', 'icon-b'))
        sheet = a['parent']
        themed = Image.new('RGBA', self.lab.image(sheet).size, (10, 200, 10, 255))
        self.lab.commit(sheet, themed, {'theme': 'green'})
        self.lab.revert([a['key']])
        self.assertEqual(self.lab.image(a['key']).tobytes(), self.lab.catalog.image(a['key']).tobytes())
        self.assertEqual(self.lab.image(b['key']).getpixel((0, 0)), (10, 200, 10, 255))   # the rest keeps the theme
        self.assertEqual(self.lab.current(sheet)['recipe']['theme'], 'green')
