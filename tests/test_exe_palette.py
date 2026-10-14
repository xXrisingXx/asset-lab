import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch as mock_patch
from fixtures import make_game
import exe_palette


class PaletteCopyTests(unittest.TestCase):
    def setUp(self):
        # Synthetic code bytes at the documented addresses expose accidental
        # changes outside the four-byte operands without needing a game install.
        data=bytearray(b'\x90'*(0x5F4D41+16))
        for name,sites in exe_palette.SITES.items():
            for offset,value,_ in sites:
                start=offset-6;va=0x140000000+start+0xC00
                if name in exe_palette.PREFIX:
                    data[start:offset]=exe_palette.PREFIX[name];struct.pack_into('<I',data,offset,value)
                else:
                    data[start:start+2]=b'\xC7\x05'
                    struct.pack_into('<iI',data,start+2,exe_palette.GLOBALS[name]-va-10,value)
        for offset,original,_,_ in exe_palette.HIDE.values():data[offset:offset+6]=original
        self.original=bytes(data)
        self.hash=hashlib.sha256(self.original).hexdigest()

    def changed(self,output):return {i for i,(a,b) in enumerate(zip(self.original,output)) if a!=b}

    def test_arbitrary_rgb_changes_both_pink_assignments_only(self):
        with mock_patch.object(exe_palette,'EXE_SHA256',self.hash):
            output,changes=exe_palette.patch(self.original,{'ACTIVE_ITEM_COLOR':'#12A4FE'})
        self.assertEqual(len(output),len(self.original));self.assertEqual(len(changes),2)
        allowed={i for o,_,_ in exe_palette.SITES['ACTIVE_ITEM_COLOR'] for i in range(o,o+4)}
        self.assertTrue(self.changed(output)<=allowed)
        for offset,_,_ in exe_palette.SITES['ACTIVE_ITEM_COLOR']:
            self.assertEqual(output[offset:offset+4],b'\xfe\xa4\x12\0')

    def test_every_field_and_variant_and_the_expanded_pink(self):
        with mock_patch.object(exe_palette,'EXE_SHA256',self.hash):
            output,changes=exe_palette.patch(self.original,{name:'#010203' for name in exe_palette.SITES})
        self.assertEqual(len(changes),sum(len(s) for s in exe_palette.SITES.values()))   # 72 palette operands + 1
        self.assertEqual(len(changes),73)
        for sites in exe_palette.SITES.values():
            for offset,_,_ in sites:self.assertEqual(output[offset:offset+4],b'\x03\x02\x01\0')
        self.assertEqual({c['phase'] for c in changes if c['field']=='BG_COLOR'},{'startup','theme_0_or_4','theme_1','theme_2'})
        # A wrong instruction in front of the expanded-pink operand is refused.
        broken=bytearray(self.original);broken[0x5F4D41-6]=0x8B
        with mock_patch.object(exe_palette,'EXE_SHA256',hashlib.sha256(broken).hexdigest()):
            with self.assertRaisesRegex(ValueError,'does not match'):exe_palette.patch(bytes(broken),{'MULTIBUTTON_EXPANDED_COLOR':'#00FF00'})

    def test_hiding_menu_controls_swaps_only_the_branch(self):
        with mock_patch.object(exe_palette,'EXE_SHA256',self.hash):
            output,changes=exe_palette.patch(self.original,{},['discord','top_banner'])
            with self.assertRaisesRegex(ValueError,'Unknown menu control'):exe_palette.patch(self.original,{},['ads'])
        offsets={exe_palette.HIDE[g][0] for g in ('discord','top_banner')}
        self.assertEqual(self.changed(output),{o+i for o in offsets for i in range(2)})   # 0F 84/85 -> 90 E9; displacement kept
        for g in ('discord','top_banner'):
            offset,original,replacement,_=exe_palette.HIDE[g]
            self.assertEqual(output[offset:offset+6],replacement);self.assertEqual(replacement[2:],original[2:])   # same jump target
        self.assertEqual([c['hide'] for c in changes],['discord','top_banner'])
        broken=bytearray(self.original);broken[exe_palette.HIDE['discord'][0]+2]^=1
        with mock_patch.object(exe_palette,'EXE_SHA256',hashlib.sha256(broken).hexdigest()):
            with self.assertRaisesRegex(ValueError,'Menu branch'):exe_palette.patch(bytes(broken),{},['discord'])

    def test_unrecognized_exe_and_bad_color_refused(self):
        with self.assertRaisesRegex(ValueError,'Unsupported'):exe_palette.patch(b'wrong',{'ACTIVE_ITEM_COLOR':'#123456'})
        with mock_patch.object(exe_palette,'EXE_SHA256',self.hash):
            for settings in ({'ACTIVE_ITEM_COLOR':'red'},{'unknown':'#123456'},{}):
                with self.assertRaises(ValueError):exe_palette.patch(self.original,settings)

    def test_copy_keeps_source_and_refuses_in_place_or_stacked_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game=root/'game';game.mkdir();source=game/'Mitosis.exe';source.write_bytes(self.original)
            dest=root/'output/Mitosis.exe'
            with mock_patch.object(exe_palette,'EXE_SHA256',self.hash):
                with self.assertRaises(ValueError):exe_palette.build_copy(source,source,{'ACTIVE_ITEM_COLOR':'#123456'})
                report=exe_palette.build_copy(source,dest,{'BG_COLOR':'#101010'})
                with self.assertRaises(ValueError):exe_palette.build_copy(source,dest,{'BG_COLOR':'#151515'})
            self.assertEqual(source.read_bytes(),self.original);self.assertEqual(len(report['changes']),4)
            self.assertFalse(report['installed']);self.assertTrue(dest.with_suffix('.palette.json').is_file())


if __name__=='__main__':unittest.main()
