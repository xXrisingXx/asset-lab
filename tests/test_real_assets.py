"""Read the user's assets, test replacements on copies only, and report evidence."""
import io
import json
from pathlib import Path
import sys
import struct
import time
import zipfile
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lab_core import Lab, png, digest, file_hash, adjust, decode_png, game_processes
from packages import replace_zip

def main():
    root=Path.home()/'MitosisAssetLab'
    lab=Lab(workspace=root/'qa/real-asset-checks/workspace', ledger_dir=root/'qa/real-asset-checks/ledgers')
    evidence=Path(__file__).resolve().parents[1]/'tests/evidence'
    before={source:file_hash(lab.game_file(source)) for source in lab.state['baseline']}
    report={'visuals':lab.catalog.stats,'warnings':lab.catalog.warnings,'archives':[],'shading':{},'sources_unchanged':False}
    # Decode/crop every indexed visual, not only a synthetic atlas sample.
    start=time.perf_counter()
    for r in lab.catalog.rows.values():
        if r['editable']:
            im=lab.image(r['key'],False)
            assert im.size==(r['width'],r['height'])
            assert im.mode=='RGBA'
    report['all_visuals_decoded']=lab.catalog.stats['visuals']
    report['decode_seconds']=round(time.perf_counter()-start,2)
    for source in before:
        if lab.catalog.containers.get(source)!='zip':continue
        original=lab.game_file(source).read_bytes()
        assert replace_zip(original,{})==original,'No-op round trip must be byte-identical'
        with zipfile.ZipFile(io.BytesIO(original)) as src:
            target='worldbg.png' if source.endswith('assets.zip') else 'items.png'
            edited=adjust(src.read(target),{'hue':22,'brightness':1.05})
            rebuilt=replace_zip(original,{target:edited})
            with zipfile.ZipFile(io.BytesIO(rebuilt)) as dst:
                assert src.namelist()==dst.namelist()
                preserved=0
                for a,b in zip(src.infolist(),dst.infolist()):
                    if a.filename==target:
                        assert dst.read(b)==edited
                        continue
                    assert src.read(a)==dst.read(b),a.filename
                    name_len,extra_len=struct.unpack_from('<HH',original,a.header_offset+26)
                    end=a.header_offset+30+name_len+extra_len+a.compress_size
                    length=end-a.header_offset
                    assert original[a.header_offset:end]==rebuilt[b.header_offset:b.header_offset+length],a.filename
                    preserved+=1
                assert dst.testzip() is None
        report['archives'].append({'path':source,'edited_member':target,'original_bytes':len(original),'edited_bytes':len(rebuilt),'untouched_records_identical':preserved,'no_op_identical':True,'crc_valid':True})
    metal=next(r for r in lab.catalog.rows.values() if r['name']=='button-up-skin' and r['source'].endswith('metalworks_mobile.png'))
    im=lab.image(metal['key'],False)
    edited=decode_png(adjust(png(im),{'tint':'#27cec0','tint_amount':1}))
    assert im.getchannel('A').tobytes()==edited.getchannel('A').tobytes()
    report['shading']={'asset':metal['name'],'size':im.size,'original_shades':lab.detail(metal['key'])['shades'],'alpha_preserved':True}
    # Keep demonstration art in QA only. No real workspace revisions or installs.
    demo=root/'qa/real-art';demo.mkdir(parents=True,exist_ok=True)
    (demo/'button-up-original.png').write_bytes(png(im));(demo/'button-up-teal.png').write_bytes(png(edited))
    report['sources_unchanged']=before=={source:file_hash(lab.game_file(source)) for source in before}
    assert report['sources_unchanged']
    report['live_game_guard']=bool(game_processes(lab.game))
    clean={k:v for k,v in lab.status().items() if k!='token'}
    (evidence/'inventory.json').write_text(json.dumps(clean,indent=2),encoding='utf-8')
    (evidence/'real-asset-tests.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
