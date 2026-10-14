import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from fixtures import make_game
from catalog import Catalog, category
from asset_notes import AssetNotes, reference_terms
from visual_tags import potion_family


class AssetLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.game=make_game(self.root)
        self.catalog=Catalog(self.game,lambda source:self.game/source)
        self.notes=AssetNotes(self.catalog,self.game,self.root/'missing.json')

    def tearDown(self): self.temp.cleanup()

    def test_all_visuals_have_exact_storage_notes(self):
        for key,row in self.catalog.rows.items():
            if not row['editable']:continue
            note=self.notes.get(key);loc=note['pixel_location']
            self.assertTrue(Path(loc['container']).is_file())
            self.assertEqual(loc['region'],row.get('rect'))
            self.assertEqual(loc['member'],row['entry'])
            if row['entry']:
                raw=Path(loc['container']).read_bytes()
                self.assertEqual(raw[int(loc['local_header_offset'],16):][:4],b'PK\x03\x04')
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    info=archive.getinfo(row['entry'])
                    self.assertEqual(loc['encoded_bytes'],info.compress_size)
                    header=raw[info.header_offset:info.header_offset+30]
                    n,e=struct.unpack_from('<HH',header,26)
                    self.assertEqual(int(loc['encoded_data_offset'],16),info.header_offset+30+n+e)

    def test_wrong_executable_hash_hides_native_offsets(self):
        row=next(r for r in self.catalog.rows.values() if r['editable'])
        path=self.root/'refs.json';path.write_text(json.dumps({'exe_sha256':'wrong','references':{row['name']:[{'string_va':'0x1'}]}}))
        note=AssetNotes(self.catalog,self.game,path).get(row['key'])
        self.assertEqual(note['exe']['references'],[])
        self.assertIn('differs',note['exe']['status'])

    def test_verified_native_reference_and_family_confidence(self):
        row=next(r for r in self.catalog.rows.values() if r['editable'])
        ref={'string_va':'0x140001234','string_file_offset':'0x634','uses':[{'function':'Example','function_va':'0x140002000','instruction_va':'0x140002010','file_offset':'0x1410'}]}
        path=self.root/'refs.json';path.write_text(json.dumps({'exe_sha256':hashlib.sha256((self.game/'Mitosis.exe').read_bytes()).hexdigest(),'references':{row['name']:[ref]}}))
        notes=AssetNotes(self.catalog,self.game,path)
        self.assertEqual(notes.get(row['key'])['exe']['references'][0]['uses'],ref['uses'])
        self.assertIn('0x1410',notes.text(row['key']))
        terms=reference_terms(dict(name='tile7_2074',kind='sprite',entry='items.png',source='assets/items.zip'))
        self.assertIn('individual member is not proven',terms['tile7_'])

    def test_bulk_export_covers_every_visual_once(self):
        with zipfile.ZipFile(io.BytesIO(self.notes.export_all())) as archive:
            records=[json.loads(line) for line in archive.read('all-visual-locations.jsonl').splitlines()]
        expected={k for k,r in self.catalog.rows.items() if r['editable']}
        self.assertEqual({r['asset_key'] for r in records},expected)
        self.assertEqual(len(records),len(expected))

    def test_verified_color_findings_are_version_locked(self):
        row=next(r for r in self.catalog.rows.values() if r['editable'])
        digest=hashlib.sha256((self.game/'Mitosis.exe').read_bytes()).hexdigest()
        findings=self.root/'visual-findings.json'
        findings.write_text(json.dumps({'exe_sha256':digest,'findings':{row['name']:['ACTIVE_ITEM_COLOR at 0x3978A5']}}))
        with patch('asset_notes.DATA',self.root):
            notes=AssetNotes(self.catalog,self.game)
            self.assertIn('0x3978A5',notes.text(row['key']))
            self.assertEqual(len(notes.get(row['key'])['verified_usage']),1)
            (self.game/'Mitosis.exe').write_bytes(b'new game version')
            self.assertEqual(notes.get(row['key'])['verified_usage'],[])

    def test_decoded_path_notes_keep_encoded_operand_evidence(self):
        row=next(r for r in self.catalog.rows.values() if r['editable'])
        ref=dict(kind='decoded_path',description='Encoded filename; external pixels.',
                 decoded_buffer_va='0x1421617E8',encoded_sites=[dict(file_offset='0x471450',bytes='12 34 56 78')],
                 uses=[dict(function='filename constructor',function_va='0x140472050',instruction_va='0x140472050',file_offset='0x471450')])
        path=self.root/'refs.json'
        path.write_text(json.dumps({'exe_sha256':hashlib.sha256((self.game/'Mitosis.exe').read_bytes()).hexdigest(),'references':{row['name']:[ref]}}))
        text=AssetNotes(self.catalog,self.game,path).text(row['key'])
        self.assertIn('Runtime decoded buffer VA: 0x1421617E8',text)
        self.assertIn('12 34 56 78',text)
        self.assertIn('0x471450',text)

    def test_potions_are_curated_without_depot_or_medal_false_positives(self):
        for name in ('tile_42','tile_53_b','tile7_2003','tile7_2074','tile7_2102','bottle'):
            self.assertTrue(potion_family(name,'items.png'),name)
            self.assertEqual(category(name,'items.png'),'Potions & alchemy')
        for name in ('tile7_1552','tile7_3464','icon-human-depot','weapon_a1'):
            self.assertFalse(potion_family(name,'items.png'),name)
        self.assertFalse(potion_family('tile7_2074','unrelated.png'))


if __name__=='__main__':unittest.main()
