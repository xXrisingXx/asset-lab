"""A fake Mitos.is install for browser tests: the unit-test fixture game plus images made for editor tests.

make_editor_game(root) writes the usual fixture (atlases, font, items zip, theme atlas and assets.zip) and four loose
PNGs in assets/. image_array(name) returns the exact RGBA pixels written for each one, and the region tables below
say where the known shapes are, so tests can aim at them. Coordinates are image pixels: (x, y, width, height).
"""
from pathlib import Path
import sys
import zipfile
import numpy as np
from PIL import Image

TESTS = Path(__file__).resolve().parents[1]
for _p in (TESTS, TESTS.parent / 'src'):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))
from fixtures import make_game  # noqa: E402  (tests/fixtures.py)
from lab_core import png  # noqa: E402

EDITOR_IMAGES = ('edit-photo.png', 'edit-sprite.png', 'edit-big.png', 'edit-tiny.png')

# edit-photo.png, 256 x 256, opaque: R ramps left to right, G top to bottom, B falls off from the center; hard shapes on top.
PHOTO = dict(white_square=(16, 16, 48, 48), black_disk=dict(center=(192, 64), radius=32), red_triangle=(24, 152, 80, 80),
             gray_bar=(96, 100, 48, 24), checker=(160, 160, 64, 32), noise=(160, 192, 64, 32))
# edit-sprite.png, 64 x 64, transparent background (0, 0, 0, 0).
SPRITE = dict(flat=(4, 4, 24, 24), flat_color=(40, 120, 220, 255),          # opaque flat square: magic wand, fill
              disk=dict(center=(44.0, 44.0), radius=14.5), disk_color=(230, 60, 40),  # one color, anti-aliased alpha edge
              glass=(4, 50, 24, 10), glass_color=(60, 200, 90, 128))         # flat half-transparent bar
# edit-tiny.png, 16 x 16 pixel art; one character per pixel, '.' is transparent.
TINY_PALETTE = {'.': (0, 0, 0, 0), 'k': (20, 20, 30, 255), 'r': (220, 40, 50, 255), 'w': (250, 250, 250, 255),
                'y': (250, 210, 60, 255), 'g': (60, 170, 80, 255)}
TINY_ROWS = ('................', '..kkkk....kkkk..', '.krrrrk..krrrrk.', 'krrwwrrkkrrrrrrk', 'krwwrrrrrrrrrrrk', 'krwrrrrrrrrrrrrk',
             'krrrrrrrrrrrrrrk', '.krrrrrrrrrrrrk.', '..krrrrrrrrrrk..', '...krrrrrrrrk...', '....krrrrrrk....', '.....krrrrk.....',
             '......krrk......', '.......kk.......', 'yyyy........gggg', 'yyyy........gggg')
# edit-big.png, 2048 x 2048, opaque gradients with a white 64 x 64 square every 256 pixels starting at (96, 96).
BIG = dict(size=2048, squares_every=256, square=(96, 96, 64, 64))


def _photo():
    y, x = np.mgrid[0:256, 0:256].astype(np.float64)
    a = np.zeros((256, 256, 4), np.uint8)
    a[..., 0], a[..., 1] = x, y
    a[..., 2] = np.clip(255 * (1 - np.hypot(x - 127.5, y - 127.5) / 181), 0, 255).round()
    a[..., 3] = 255
    def box(name, color):
        bx, by, bw, bh = PHOTO[name]
        a[by:by + bh, bx:bx + bw] = color
    box('white_square', (255, 255, 255, 255))
    box('gray_bar', (128, 128, 128, 255))
    (cx, cy), r = PHOTO['black_disk']['center'], PHOTO['black_disk']['radius']
    a[np.hypot(x + .5 - cx, y + .5 - cy) <= r] = (0, 0, 0, 255)  # aliased: every pixel is in or out
    tx, ty, tw, th = PHOTO['red_triangle']
    a[(x >= tx) & (y < ty + th) & (x - tx <= y - ty)] = (255, 0, 0, 255)
    bx, by, bw, bh = PHOTO['checker']
    a[by:by + bh, bx:bx + bw, :3] = (((x + y) % 2) * 255)[by:by + bh, bx:bx + bw, None]
    bx, by, bw, bh = PHOTO['noise']
    a[by:by + bh, bx:bx + bw, :3] = np.random.default_rng(7).integers(0, 256, (bh, bw, 1), dtype=np.uint8)
    return a


def _sprite():
    a = np.zeros((64, 64, 4), np.uint8)
    x, y, w, h = SPRITE['flat']
    a[y:y + h, x:x + w] = SPRITE['flat_color']
    x, y, w, h = SPRITE['glass']
    a[y:y + h, x:x + w] = SPRITE['glass_color']
    (cx, cy), r = SPRITE['disk']['center'], SPRITE['disk']['radius']
    sub = (np.arange(8) + .5) / 8  # 8 x 8 samples per pixel give the coverage of the anti-aliased edge
    py, px = np.mgrid[0:64, 0:64]
    sx = px[..., None, None] + sub[None, None, None, :]
    sy = py[..., None, None] + sub[None, None, :, None]
    cover = ((sx - cx) ** 2 + (sy - cy) ** 2 <= r * r).mean(axis=(2, 3))
    alpha = np.round(cover * 255).astype(np.uint8)
    on = alpha > 0
    a[on, :3] = SPRITE['disk_color']
    a[on, 3] = alpha[on]
    return a


def _tiny():
    return np.array([[TINY_PALETTE[c] for c in row] for row in TINY_ROWS], np.uint8)


def _big():
    n = BIG['size']
    y, x = np.mgrid[0:n, 0:n].astype(np.float32)
    a = np.empty((n, n, 4), np.uint8)
    a[..., 0] = (x * 255 / (n - 1)).round()
    a[..., 1] = (y * 255 / (n - 1)).round()
    a[..., 2] = (127.5 + 127.5 * np.sin((x + y) * np.pi / 512)).round()
    a[..., 3] = 255
    step, (sx, sy, sw, sh) = BIG['squares_every'], BIG['square']
    for oy in range(0, n, step):
        for ox in range(0, n, step):
            a[oy + sy:oy + sy + sh, ox + sx:ox + sx + sw, :3] = 255
    return a


_MAKERS = {'edit-photo.png': _photo, 'edit-sprite.png': _sprite, 'edit-big.png': _big, 'edit-tiny.png': _tiny}
_cache = {}


def image_array(name):
    """The exact RGBA pixels (H, W, 4 uint8) written to assets/<name>. A copy, so tests may change it."""
    if name not in _cache:
        if name not in _MAKERS: raise KeyError(f'{name} is not an editor test image; use one of {EDITOR_IMAGES}')
        _cache[name] = _MAKERS[name]()
        _cache[name].setflags(write=False)
    return _cache[name].copy()


_png = {}


def image_png(name):
    if name not in _png: _png[name] = png(Image.fromarray(image_array(name), 'RGBA'))
    return _png[name]


def theme_atlas(game):
    """Theme atlas with two live icons and leftover rectangles, plus an identical copy in assets.zip (as stale_game in tests/test_theme.py)."""
    assets = game / 'assets'
    sheet = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
    sheet.paste((230, 60, 40, 255), (2, 2, 30, 30))
    sheet.paste((255, 255, 255, 255), (42, 2, 70, 30))
    sheet.paste((40, 200, 90, 255), (2, 82, 14, 94))
    sheet.paste((250, 250, 250, 255), (82, 82, 98, 98))
    sheet.paste((25, 30, 35, 255), (106, 82, 122, 98))
    rows = ['<SubTexture name="icon-a" x="0" y="0" width="32" height="32" />', '<SubTexture name="icon-b" x="40" y="0" width="32" height="32" />',
            '<SubTexture name="free-x" x="0" y="80" width="16" height="16" />', '<SubTexture name="virus-pale" x="80" y="80" width="20" height="20" />',
            '<SubTexture name="virus-deep" x="104" y="80" width="20" height="20" />']
    rows += [f'<SubTexture name="old-{i}" x="{10 + i * 3}" y="{12 + i}" width="36" height="30" />' for i in range(9)]
    atlas = '<TextureAtlas imagePath="theme.png">' + ''.join(rows) + '</TextureAtlas>'
    (assets / 'theme4.png').write_bytes(png(sheet))
    (assets / 'theme4.xml').write_text(atlas, encoding='utf-8')
    with zipfile.ZipFile(assets / 'assets.zip', 'w') as z:
        z.writestr('theme4.png', png(sheet))
        z.writestr('theme4.xml', atlas)
    (game / 'Mitosis.exe').write_bytes(b'QA fixture\0icon-a\0icon-b\0button-up-skin\0')


def make_editor_game(root):
    """Write the fake game under root and return its folder (the one holding Mitosis.exe and assets/)."""
    game = make_game(root)
    theme_atlas(game)
    for name in EDITOR_IMAGES: (game / 'assets' / name).write_bytes(image_png(name))
    return game
