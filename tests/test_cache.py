import io
from pathlib import Path
import struct
import tempfile
import unittest
import urllib.parse
import zipfile
from PIL import Image
from fixtures import make_game
from lab_core import Lab, png, file_hash
from cache_index import read_table, visual_names, HaxeData


def string(v):
    encoded=urllib.parse.quote(v,safe='')
    return 'y'+str(len(encoded))+':'+encoded


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.game=make_game(self.root);self.cache=self.root/'cache';self.cache.mkdir()
        self.name='1'*32+'.dat';self.zipname='2'*32+'.dat'
        (self.cache/self.name).write_bytes(png(Image.new('RGBA',(32,32),'orange')))
        with zipfile.ZipFile(self.cache/self.zipname,'w') as z:
            z.writestr('items.png',png(Image.new('RGBA',(32,32),'blue')))
            z.writestr('items.xml','<TextureAtlas imagePath="wrong.png"><SubTexture name="cached-item" x="0" y="0" width="16" height="16"/></TextureAtlas>')
        data=('o'+string('rows')+'q:0o'+string('FilePath')+string('storage:/'+self.name)+string('Url')+string('http://art.invalid/skins/a/1/2.png')+'g:1o'+string('FilePath')+string('storage:/'+self.zipname)+string('Url')+string('http://art.invalid/files/items_new.zip')+'ghg').encode()
        (self.cache/'StaticFilesTbl_0.mtdb').write_bytes(b'\x09\x00MITOSISDB'+struct.pack('<I',len(data))+b'\x04'+data)
        (self.cache/'account.sol').write_bytes(b'private-data-must-not-be-indexed')
        (self.cache/('3'*32+'.dat')).write_bytes(b'not-a-visual')
        self.lab=Lab(self.game,self.root/'work',self.root/'ledger',process_check=lambda:[],cache=self.cache)

    def tearDown(self):self.temp.cleanup()

    def test_names_from_static_table(self):
        names=visual_names(self.cache)
        self.assertEqual(names[self.name],'skins/a/1/2.png')
        row=next(r for r in self.lab.catalog.rows.values() if r['source']=='cache/'+self.name)
        self.assertEqual(row['group'],'Downloaded skins')
        self.assertEqual(row['width'],32)

    def test_only_recognized_visual_cache_indexed(self):
        sources={r['source'] for r in self.lab.catalog.rows.values()}
        self.assertFalse(any('.sol' in s or '.mtdb' in s for s in sources))
        self.assertNotIn('cache/'+'3'*32+'.dat',sources)

    def test_cached_zip_gets_named_regions(self):
        row=next(r for r in self.lab.catalog.rows.values() if r['name']=='cached-item')
        self.assertEqual(row['source_label'],'files/items_new.zip')
        self.assertEqual(self.lab.image(row['key']).size,(16,16))

    def test_install_and_restore_png_and_zip_cache_exactly(self):
        before={p.name:file_hash(p) for p in self.cache.iterdir()}
        for name in ['skins/a/1/2.png','cached-item']:
            row=next(r for r in self.lab.catalog.rows.values() if r['name']==name)
            im=self.lab.image(row['key']);im.paste('red',(0,0,im.width,im.height))
            self.lab.save(row['key'],png(im),'original')
        build=self.lab.build();self.assertEqual(len(build['files']),2)
        self.lab.install(build['id']);self.assertEqual(self.lab.active()['state'],'installed')
        self.lab.restore();self.assertEqual(before,{p.name:file_hash(p) for p in self.cache.iterdir()})

    def test_cache_path_escape_and_account_file_blocked(self):
        for p in ['cache/../account.sol','cache/account.sol','cache/StaticFilesTbl_0.mtdb']:
            with self.assertRaises(ValueError):self.lab.game_file(p)

    def test_haxe_string_reference(self):
        value=HaxeData(b'oy1:ay5:helloy1:bR1g').value()
        self.assertEqual(value,{'a':'hello','b':'hello'})

    def test_haxe_class_instantiation_rejected(self):
        with self.assertRaises(ValueError):HaxeData(b'cy8:BadClassg').value()


if __name__=='__main__':unittest.main()
