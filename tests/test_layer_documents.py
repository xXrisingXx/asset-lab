"""The editor's layered documents: saved beside a revision (revisions/<key>/<version>.layers.json), checked before they are
stored, and served back by GET /api/document. The flattened PNG stays the revision itself."""
import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from PIL import Image
from fixtures import make_game
from lab_core import Lab, png
from lab_server import server_for


def b64png(im): return base64.b64encode(png(im)).decode()


class LayerDocuments(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.lab = Lab(make_game(root), root / 'workspace', root / 'ledgers', process_check=lambda: [])
        self.key = next(r['key'] for r in self.lab.catalog.rows.values() if r['name'] == 'bg2.png')
        self.base = self.lab.image(self.key)

    def tearDown(self): self.temp.cleanup()

    def document(self, layers=2, size=(64, 64), **extra):
        rows = [dict(id=f'L{i + 1}', name=f'Layer {i}', kind='pixel', visible=True, opacity=1, fill=1, blend='normal', locks={'alpha': False},
                     maskEnabled=True, clip=False, effects={}, adjustment=None, data={}, png=b64png(Image.new('RGBA', size, (i * 40, 90, 200, 255))))
                for i in range(layers)]
        return dict(version=1, width=64, height=64, activeIndex=layers - 1, target='pixels', layers=rows, **extra)

    def edited(self):
        im = self.base.copy()
        im.paste((10, 200, 30, 255), (0, 0, 20, 20))
        return png(im)

    def layers_path(self, version): return self.lab.root / 'revisions' / self.key / (version + '.layers.json')

    def test_a_layered_save_keeps_the_document_beside_the_revision(self):
        doc = self.document(3)
        doc['layers'][1].update(blend='multiply', opacity=0.5, mask_png=b64png(Image.new('RGBA', (64, 64), (255, 255, 255, 128))), effects={'drop-shadow': {'distance': 4}})
        detail = self.lab.save(self.key, self.edited(), 'original', {'operations': []}, doc)
        stored = json.loads(self.lab.layers_file(self.key))
        self.assertTrue(self.layers_path(detail['revision']).is_file())
        self.assertEqual([l['id'] for l in stored['layers']], ['L1', 'L2', 'L3'])
        self.assertEqual((stored['layers'][1]['blend'], stored['layers'][1]['opacity'], stored['layers'][1]['effects']), ('multiply', 0.5, {'drop-shadow': {'distance': 4}}))
        self.assertIn('mask_png', stored['layers'][1])
        self.assertNotIn('mask_png', stored['layers'][0])
        self.assertEqual(self.lab.image(self.key).tobytes(), Image.open(__import__('io').BytesIO(self.edited())).convert('RGBA').tobytes(), 'the flat PNG is the revision')

    def test_same_pixels_with_new_layers_update_the_current_revision(self):
        first = self.lab.save(self.key, self.edited(), 'original', {}, self.document(2))
        again = self.lab.save(self.key, self.edited(), first['revision'], {}, self.document(4))
        self.assertEqual(again['revision'], first['revision'], 'no new revision for the same picture')
        self.assertEqual(len(json.loads(self.lab.layers_file(self.key))['layers']), 4)
        # A plain (one-layer) save of the same picture keeps the stored layers rather than throwing them away.
        self.lab.save(self.key, self.edited(), first['revision'], {}, None)
        self.assertEqual(len(json.loads(self.lab.layers_file(self.key))['layers']), 4)

    def test_layers_on_the_unchanged_original_make_a_revision(self):
        detail = self.lab.save(self.key, png(self.base), 'original', {}, self.document(2))
        self.assertNotEqual(detail['revision'], 'original')
        self.assertTrue(self.layers_path(detail['revision']).is_file())
        plain = self.lab.save(self.key, png(self.base), detail['revision'], {}, None)
        self.assertEqual(plain['revision'], detail['revision'])

    def test_a_new_flat_revision_has_no_layers(self):
        first = self.lab.save(self.key, self.edited(), 'original', {}, self.document(2))
        im = self.base.copy()
        im.paste((250, 0, 0, 255), (30, 30, 40, 40))
        second = self.lab.save(self.key, png(im), first['revision'], {}, None)
        self.assertNotEqual(second['revision'], first['revision'])
        self.assertIsNone(self.lab.layers_file(self.key))
        self.assertIsNotNone(self.lab.layers_file(self.key, first['revision']), 'the earlier revision keeps its layers')
        self.assertIsNone(self.lab.layers_file(self.key, '../../escape'))

    def test_bad_documents_are_refused_and_nothing_is_written(self):
        cases = {'version': self.document(1), 'size': self.document(2, size=(32, 64)), 'many': self.document(101)}
        cases['version']['version'] = 2
        cases['wide'] = dict(self.document(1), width=65)
        cases['picture'] = self.document(1)
        cases['picture']['layers'][0]['png'] = base64.b64encode(b'not a png').decode()
        cases['jpeg'] = self.document(1)
        import io
        out = io.BytesIO()
        Image.new('RGB', (64, 64)).save(out, 'JPEG')
        cases['jpeg']['layers'][0]['png'] = base64.b64encode(out.getvalue()).decode()
        cases['effects'] = self.document(1)
        cases['effects']['layers'][0]['effects'] = ['not', 'an', 'object']
        for name, doc in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.lab.save(self.key, self.edited(), 'original', {}, doc)
        self.assertEqual(self.lab.revision(self.key), 'original')
        self.assertFalse((self.lab.root / 'revisions' / self.key).exists() and any((self.lab.root / 'revisions' / self.key).iterdir()))

    def test_values_are_cleaned_before_they_are_stored(self):
        doc = self.document(1)
        doc['layers'][0].update(opacity=7, fill='x', blend='Robert"); drop', name='n' * 999, visible=0, extra='ignored', activeIndex=9)
        doc['activeIndex'] = 42
        detail = self.lab.save(self.key, self.edited(), 'original', {}, doc)
        layer = json.loads(self.lab.layers_file(self.key, detail['revision']))
        self.assertEqual((layer['activeIndex'], layer['layers'][0]['opacity'], layer['layers'][0]['fill'], layer['layers'][0]['blend']), (0, 1.0, 1.0, 'normal'))
        self.assertEqual(len(layer['layers'][0]['name']), 255)
        self.assertNotIn('extra', layer['layers'][0])

    def test_the_server_round_trip(self):
        server = server_for(self.lab, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            def call(path, data=None):
                req = urllib.request.Request(base + path, None if data is None else json.dumps(data).encode(),
                                             {'Content-Type': 'application/json', 'X-Lab-Token': self.lab.session} if data is not None else {})
                with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read())
            self.assertIsNone(call('/api/document?key=' + self.key)['document'])
            saved = call('/api/save', dict(key=self.key, revision='original', png=base64.b64encode(self.edited()).decode(), recipe={}, document=self.document(2)))
            got = call(f'/api/document?key={self.key}&revision={saved["revision"]}')['document']
            self.assertEqual((got['width'], len(got['layers']), got['layers'][1]['name']), (64, 2, 'Layer 1'))
            self.assertIsNone(call(f'/api/document?key={self.key}&revision=nope')['document'])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__': unittest.main()
