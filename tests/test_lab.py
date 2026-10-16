import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile
from PIL import Image
from fixtures import make_game
from lab_core import Lab, png, file_hash, adjust, decode_png
from lab_server import server_for
from packages import replace_zip, safe_member


class Case(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.game=make_game(self.root)
        self.lab=Lab(self.game,self.root/'workspace',self.root/'ledgers',process_check=lambda:[])

    def tearDown(self): self.temp.cleanup()

    def key(self,name): return next(r['key'] for r in self.lab.catalog.rows.values() if r['name']==name)

    def edit(self,name,color='#28bcb4'):
        k=self.key(name)
        im=self.lab.image(k)
        im.paste(color,(0,0,im.width,im.height))
        return self.lab.save(k,png(im),self.lab.revision(k),{'color':color})

    def test_catalog_covers_visual_types_and_pairing(self):
        self.assertEqual(self.lab.catalog.stats['glyph'],1)
        self.assertTrue(self.lab.row(self.key('items.png'))['pairing_note'])
        self.assertEqual(self.lab.row(self.key('bg2.png'))['group'],'Backgrounds')
        self.assertEqual(self.lab.row(self.key('item-a'))['group'],'Equipment')
        self.assertEqual(self.lab.search(kind='visual')['total'],self.lab.catalog.stats['visuals'])
        self.assertTrue(self.lab.catalog.warnings)

    def test_rotated_sprite_extracted_upright(self):
        im=self.lab.image(self.key('rotated'))
        self.assertEqual(im.size,(16,24))
        source=self.lab.image(self.key('metalworks_mobile.png'))
        self.assertEqual(im.tobytes(),source.crop((0,32,24,48)).transpose(Image.Transpose.ROTATE_90).tobytes())

    def test_font_metadata_preserved(self):
        row=self.lab.row(self.key('U+0041 A'))
        self.assertEqual(row['metadata']['xadvance'],'13')
        self.assertEqual(self.lab.image(row['key']).size,(12,20))

    def test_related_button_states_and_overlap_warning(self):
        d=self.lab.detail(self.key('button-up-skin'))
        self.assertIn('button-down-skin',[r['name'] for r in d['related']])
        self.assertIn('alias',[r['name'] for r in d['shared_regions']])

    def test_wrong_dimensions_rejected(self):
        with self.assertRaisesRegex(ValueError,'original'):
            self.lab.save(self.key('bg2.png'),png(Image.new('RGBA',(32,32))),'original')

    def test_non_image_edit_rejected(self):
        with self.assertRaisesRegex(ValueError,'Only image'):
            self.lab.save(self.key('particle.awp'),png(Image.new('RGBA',(4,4))),'original')

    def test_noop_save_has_no_revision(self):
        k=self.key('bg2.png')
        result=self.lab.save(k,png(self.lab.image(k)),'original')
        self.assertEqual(result['revision'],'original')
        self.assertFalse(self.lab.staged())

    def test_undo_redo_walk_history_and_branch(self):
        a=self.edit('bg2.png','#111111');b=self.edit('bg2.png','#222222');c=self.edit('bg2.png','#333333')
        k=a['key']
        self.assertEqual(self.lab.step(k,c['revision'],-1)['revision'],b['revision'])
        self.assertEqual(self.lab.step(k,b['revision'],-1)['revision'],a['revision'])
        self.assertEqual(self.lab.step(k,a['revision'],1)['revision'],b['revision'])
        d=self.edit('bg2.png','#444444')
        self.assertFalse(d['can_redo'])
        self.assertEqual(len(d['history']['versions']),3)

    def test_stale_revision_rejected(self):
        self.edit('bg2.png')
        with self.assertRaisesRegex(ValueError,'another tab'):
            self.lab.save(self.key('bg2.png'),png(Image.new('RGBA',(64,64))),'original')

    def test_concurrent_saves_only_one_wins(self):
        k=self.key('bg2.png')
        def save(color):
            try: self.lab.save(k,png(Image.new('RGBA',(64,64),color)),'original');return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(save,['red','blue'])),1)

    def test_history_survives_restart(self):
        a=self.edit('bg2.png')
        again=Lab(self.game,self.root/'workspace',self.root/'ledgers',process_check=lambda:[])
        self.assertEqual(again.revision(a['key']),a['revision'])
        self.assertEqual(again.image(a['key']).tobytes(),self.lab.image(a['key']).tobytes())

    def test_adjustments_preserve_alpha(self):
        k=self.key('bg2.png');original=self.lab.image(k)
        out=decode_png(adjust(png(original),{'brightness':1.2,'contrast':1.1,'saturation':1.3,'hue':70,'sharpen':1,'noise':.1,'tint_amount':.6}))
        self.assertEqual(out.getchannel('A').tobytes(),original.getchannel('A').tobytes())
        self.assertNotEqual(out.tobytes(),original.tobytes())

    def test_recolor_preserves_neutral_border_and_highlight(self):
        im=Image.new('RGBA',(3,1));im.putdata([(0,0,0,255),(255,255,255,255),(255,128,0,128)])
        out=decode_png(adjust(png(im),{'tint':'#0088ff','tint_amount':1}))
        self.assertEqual([out.getpixel((x,0)) for x in range(3)],[(0,0,0,255),(255,255,255,255),(0,136,255,128)])

    def test_adjust_invalid_parameters(self):
        for settings in [{'brightness':float('nan')},{'contrast':8},{'tint':'invalid'}]:
            with self.assertRaises(ValueError):adjust(png(self.lab.image(self.key('bg2.png'))),settings)

    def test_two_nonoverlapping_sprite_edits_compose(self):
        self.edit('item-a','red');self.edit('item-b','blue')
        build=self.lab.build();self.assertEqual(len(build['files']),1)
        data=(self.lab.root/'builds'/build['id']/'files/assets/items_635w.zip').read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as z:im=decode_png(z.read('items.png'))
        self.assertEqual(im.getpixel((0,0)),(255,0,0,255));self.assertEqual(im.getpixel((16,0)),(0,0,255,255))
        self.assertEqual(im.getpixel((50,50)),self.lab.image(self.key('items.png')).getpixel((50,50)))

    def test_rotated_sprite_repacked_without_blending(self):
        k=self.key('rotated');im=self.lab.image(k);im.putpixel((2,3),(100,70,9,32))
        self.lab.save(k,png(im),'original');b=self.lab.build()
        out=decode_png((self.lab.root/'builds'/b['id']/'files/assets/metalworks_mobile.png').read_bytes())
        self.assertEqual(out.crop((0,32,24,48)).transpose(Image.Transpose.ROTATE_90).tobytes(),im.tobytes())

    def test_parent_and_child_conflict(self):
        self.edit('items.png');self.edit('item-a')
        with self.assertRaisesRegex(ValueError,'full sheet OR'):self.lab.build()

    def test_overlapping_sprites_conflict(self):
        self.edit('button-up-skin');self.edit('alias')
        with self.assertRaisesRegex(ValueError,'share atlas'):self.lab.build()

    def test_install_restore_multiple_files_exact(self):
        files=list((self.game/'assets').glob('*'));before={str(p):file_hash(p) for p in files}
        self.edit('item-a');self.edit('bg2.png');b=self.lab.build();self.lab.install(b['id'])
        self.assertEqual(self.lab.active()['state'],'installed')
        self.assertNotEqual(file_hash(self.game/'assets/bg2.png'),before[str(self.game/'assets/bg2.png')])
        self.lab.restore();self.assertIsNone(self.lab.active())
        self.assertEqual(before,{str(p):file_hash(p) for p in files})

    def test_installed_mod_updates_in_place_and_restores_exactly(self):
        files=list((self.game/'assets').glob('*'));before={str(p):file_hash(p) for p in files}
        self.edit('item-a');self.edit('bg2.png');b=self.lab.build();self.lab.install(b['id'])
        self.assertFalse(self.lab.pending())
        # Keep working while it is installed: change one edit again and take the other out of the mod.
        self.edit('bg2.png','#ff0000');self.lab.stage(self.key('item-a'),False)
        self.assertTrue(self.lab.pending())
        item_source=self.lab.row(self.key('item-a'))['source'];bg_source=self.lab.row(self.key('bg2.png'))['source']
        b2=self.lab.build();journal=self.lab.install(b2['id'])
        self.assertEqual((journal['state'],[f['path'] for f in journal['files']],journal['restored_files']),('installed',[bg_source],[item_source]))
        self.assertEqual(file_hash(self.lab.game_file(bg_source)),b2['files'][0]['edited_sha256'])
        self.assertEqual(file_hash(self.lab.game_file(item_source)),self.lab.state['baseline'][item_source]['sha256'])
        self.assertFalse(self.lab.pending())
        self.lab.restore();self.assertIsNone(self.lab.active())
        self.assertEqual(before,{str(p):file_hash(p) for p in files})

    def test_update_that_leaves_everything_out_restores_the_originals(self):
        files=list((self.game/'assets').glob('*'));before={str(p):file_hash(p) for p in files}
        self.edit('bg2.png');self.lab.install(self.lab.build()['id'])
        self.lab.stage(self.key('bg2.png'),False)
        b=self.lab.build();self.assertEqual(b['files'],[])
        self.lab.install(b['id']);self.assertIsNone(self.lab.active())
        self.assertEqual(before,{str(p):file_hash(p) for p in files})

    def test_interrupted_update_is_recoverable(self):
        files=list((self.game/'assets').glob('*'));before={str(p):file_hash(p) for p in files}
        self.edit('bg2.png');self.edit('item-a');self.lab.install(self.lab.build()['id'])
        self.edit('bg2.png','#ff0000');self.edit('item-a','#00ff00');b=self.lab.build()
        import lab_core
        real=lab_core.atomic_bytes;writes=0
        def fail_second(path,data):
            nonlocal writes
            if Path(path).resolve().is_relative_to(self.lab.game):  # the lab resolves short (8.3) temp paths
                writes+=1
                if writes==2:raise OSError('simulated disk failure')
            return real(path,data)
        with patch('lab_core.atomic_bytes',side_effect=fail_second):
            with self.assertRaises(OSError):self.lab.install(b['id'])
        self.assertEqual(self.lab.drift(),[])          # one file holds the new mod, one the old: both are known states
        with self.assertRaisesRegex(ValueError,'interrupted'):self.lab.build()
        self.lab.restore()
        self.assertEqual(before,{str(p):file_hash(p) for p in files})

    def test_edits_and_review_listing_while_installed(self):
        self.edit('bg2.png');self.lab.install(self.lab.build()['id'])
        self.edit('item-a')                              # saving while installed works from the original backups
        mod=self.lab.mod()
        self.assertEqual({e['name']:e['installed'] for e in mod['edits']},{'Main menu background':True,'item-a':False})   # bg2.png carries its UI map name
        self.assertEqual(mod['installed']['edits'],[self.key('bg2.png')])
        self.assertTrue(mod['pending'])
        self.lab.stage_many([self.key('item-a')],False);self.assertFalse(self.lab.pending())
        self.assertEqual(self.lab.thumbnail(self.key('bg2.png'),original=True)[:4],b'\x89PNG')

    def test_cross_workspace_cannot_stack(self):
        self.edit('bg2.png');b=self.lab.build();self.lab.install(b['id'])
        with self.assertRaisesRegex(ValueError,'another workspace'):Lab(self.game,self.root/'another',self.root/'ledgers')

    def test_source_change_blocks_save(self):
        p=self.game/'assets/bg2.png';p.write_bytes(png(Image.new('RGBA',(64,64),'blue')))
        with self.assertRaisesRegex(ValueError,'source changed'):self.edit('bg2.png')

    def test_source_change_after_build_blocks_install(self):
        self.edit('bg2.png');b=self.lab.build();p=self.game/'assets/bg2.png';p.write_bytes(p.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'changed since build'):self.lab.install(b['id'])
        self.assertIsNone(self.lab.active())

    def test_unknown_external_change_blocks_restore(self):
        self.edit('bg2.png');b=self.lab.build();self.lab.install(b['id']);p=self.game/'assets/bg2.png';p.write_bytes(p.read_bytes()+b'external')
        with self.assertRaisesRegex(ValueError,'external change'):self.lab.restore()
        self.assertTrue(p.read_bytes().endswith(b'external'))

    def test_tampered_backup_blocks_install(self):
        self.edit('bg2.png');b=self.lab.build();Path(b['files'][0]['backup']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'backup'):self.lab.install(b['id'])

    def test_tampered_build_blocks_install(self):
        self.edit('bg2.png');b=self.lab.build();(self.lab.root/'builds'/b['id']/'files/assets/bg2.png').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'Build failed'):self.lab.install(b['id'])

    def test_process_guard(self):
        self.edit('bg2.png');b=self.lab.build();self.lab.process_check=lambda:['Mitosis.exe']
        with self.assertRaisesRegex(ValueError,'Close every'):self.lab.install(b['id'])

    def test_interrupted_install_is_recoverable(self):
        self.edit('bg2.png');self.edit('item-a');b=self.lab.build()
        import lab_core
        real=lab_core.atomic_bytes
        writes=0
        def fail_second(path,data):
            nonlocal writes
            if Path(path).resolve().is_relative_to(self.lab.game):  # the lab resolves short (8.3) temp paths
                writes+=1
                if writes==2:raise OSError('simulated disk failure')
            return real(path,data)
        with patch('lab_core.atomic_bytes',side_effect=fail_second):
            with self.assertRaises(OSError):self.lab.install(b['id'])
        self.assertEqual(self.lab.active()['state'],'needs_restore')
        self.lab.restore()
        for f in b['files']:self.assertEqual(file_hash(self.lab.game_file(f['path'])),f['original_sha256'])

    def test_export_recipe_contains_original_and_saved(self):
        a=self.edit('bg2.png')
        with zipfile.ZipFile(io.BytesIO(self.lab.export_recipe(a['key']))) as z:
            self.assertEqual(set(z.namelist()),{'texture.png','original.png','recipe.json'})
            self.assertNotEqual(z.read('texture.png'),z.read('original.png'))

    def test_path_escape_rejected(self):
        for p in ['../Mitosis.exe','assets/../Mitosis.exe','./Mitosis.exe','assets/../../bad']:
            with self.assertRaises(ValueError):self.lab.game_file(p)
        # The executable itself is reachable by its exact name only, for the verified game-color operands.
        self.assertEqual(self.lab.game_file('Mitosis.exe'),(self.game/'Mitosis.exe').resolve())

    def test_bad_zip_names_rejected(self):
        for p in ['../bad','C:/bad','x\\bad','/bad']:
            with self.assertRaises(ValueError):safe_member(p)

    def test_zip_preserves_untouched_compressed_records(self):
        before=(self.game/'assets/items_635w.zip').read_bytes();after=replace_zip(before,{'items.png':b'edited bytes'})
        with zipfile.ZipFile(io.BytesIO(before)) as a,zipfile.ZipFile(io.BytesIO(after)) as b:
            self.assertEqual(a.comment,b.comment)
            for x,y in zip(a.infolist(),b.infolist()):
                self.assertEqual((x.filename,x.date_time,x.compress_type,x.comment,x.extra,x.external_attr),(y.filename,y.date_time,y.compress_type,y.comment,y.extra,y.external_attr))
                if x.filename!='items.png':
                    self.assertEqual(a.read(x),b.read(y))
                    n=30+len(x.filename.encode())+len(x.extra)+x.compress_size
                    self.assertEqual(before[x.header_offset:x.header_offset+n],after[y.header_offset:y.header_offset+n])

    def test_deflated_replacement_validates(self):
        before=(self.game/'assets/items_635w.zip').read_bytes();after=replace_zip(before,{'keep.bin':b'new'*2000})
        with zipfile.ZipFile(io.BytesIO(after)) as z:self.assertEqual(z.read('keep.bin'),b'new'*2000)

    def test_unknown_zip_member_cannot_be_added(self):
        with self.assertRaises(ValueError):replace_zip((self.game/'assets/items_635w.zip').read_bytes(),{'new.exe':b'no'})


class HTTPTests(unittest.TestCase):
    setUp=Case.setUp
    tearDown=Case.tearDown
    key=Case.key
    def test_a_kept_alive_connection_does_not_stop_the_lab_from_closing(self):
        # The page polls over one kept-alive connection; closing the window must still end the server at once.
        import http.client
        server=server_for(self.lab,0);t=threading.Thread(target=server.serve_forever);t.start()
        page=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        for _ in range(3):page.request('GET','/api/status');r=page.getresponse();r.read();self.assertFalse(r.will_close)
        server.shutdown();t.join(5)
        start=time.monotonic();server.end_idle_connections();server.server_close()
        self.assertLess(time.monotonic()-start,3,'the idle kept-alive connection held the server open')
        page.close()

    def test_server_read_write_origin_and_shutdown(self):
        server=server_for(self.lab,0);t=threading.Thread(target=server.serve_forever);t.start()
        url=f'http://127.0.0.1:{server.server_port}'
        def request(path,data=None,token=None,origin=None):
            headers={}
            if token:headers['X-Lab-Token']=token
            if origin:headers['Origin']=origin
            req=urllib.request.Request(url+path,None if data is None else json.dumps(data).encode(),headers=headers)
            return urllib.request.urlopen(req)
        try:
            with request('/api/status') as r:status=json.load(r)
            self.assertEqual(status['app'],'Asset Lab')
            with self.assertRaises(urllib.error.HTTPError):request('/api/build',{})
            with self.assertRaises(urllib.error.HTTPError):request('/api/build',{},status['token'],'https://example.com')
            k=self.key('bg2.png')
            data=dict(key=k,revision='original',png=base64.b64encode(png(Image.new('RGBA',(64,64),'red'))).decode())
            with request('/api/save',data,status['token'],url) as r:self.assertNotEqual(json.load(r)['revision'],'original')
            with request('/api/build',{},status['token']) as r:self.assertTrue(json.load(r)['files'])
            with request('/api/shutdown',{},status['token']) as r:self.assertTrue(json.load(r)['stopping'])
            t.join(5);self.assertFalse(t.is_alive())
        finally:server.shutdown();server.server_close();t.join(5)


if __name__=='__main__':unittest.main()
