from pathlib import Path
import sys
import zipfile
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from lab_core import png


def make_game(root):
    game = Path(root) / 'Mitos.is The Game'
    assets = game / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    (game / 'Mitosis.exe').write_bytes(b'QA fixture, not executable')
    im = Image.new('RGBA', (64, 64), '#fc960d')
    # Distinct corners and semi-transparent interior expose rotation/alpha errors.
    for y in range(64):
        for x in range(64):
            im.putpixel((x,y), (x*4, y*4, 90, 128 if x>20 else 255))
    (assets / 'bg2.png').write_bytes(png(im))
    (assets / 'metalworks_mobile.png').write_bytes(png(im))
    (assets / 'metalworks_mobile.xml').write_text('''<TextureAtlas imagePath="wrong.png">
      <SubTexture name="button-up-skin" x="0" y="0" width="16" height="24" />
      <SubTexture name="button-down-skin" x="20" y="0" width="16" height="24" />
      <SubTexture name="rotated" x="0" y="32" width="24" height="16" rotated="true" frameWidth="20" frameHeight="28" frameX="-2" frameY="-2" />
      <SubTexture name="alias" x="0" y="0" width="8" height="8" />
      <SubTexture name="outside" x="62" y="62" width="20" height="20" />
    </TextureAtlas>''', encoding='utf-8')
    (assets / 'font_0.png').write_bytes(png(im))
    (assets / 'font.fnt').write_text('''<font><pages><page id="0" file="font_0.png"/></pages><chars><char id="65" x="0" y="0" width="12" height="20" page="0" xadvance="13"/></chars></font>''',encoding='utf-8')
    with zipfile.ZipFile(assets / 'items_635w.zip', 'w') as z:
        z.comment=b'keep-archive-comment'
        info=zipfile.ZipInfo('items.png', (2021,2,3,4,5,6))
        info.comment=b'keep-entry-comment'
        info.external_attr=0o644<<16
        z.writestr(info,png(im))
        z.writestr('items.xml','<TextureAtlas imagePath="theme.png"><SubTexture name="item-a" x="0" y="0" width="12" height="12"/><SubTexture name="item-b" x="16" y="0" width="12" height="12"/></TextureAtlas>')
        z.writestr('keep.bin',bytes(range(256))*20,compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('particle.awp','{"doNotChange":true}')
    return game
