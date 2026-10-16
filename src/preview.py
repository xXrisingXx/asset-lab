"""Game preview: the main menu and a match scene rebuilt from the game's own sprites (with your edits) at the places,
sizes and tints the game uses, so changes can be checked without restarting Mitosis.

Positions are in 2560 x 1600 screen pixels, measured on screen captures of the real game (the UI map). It is a
close preview, not the game itself: menu text uses a stand-in font and a few animated or server-loaded parts are left out.
"""
import math
import random
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H = 2560, 1600
BG = (13, 40, 41)                       # the menu's base color, compiled into Mitosis.exe (#0D2829)
TEAL, PINK = '#40BDC4', '#E52C58'
TILE = 0.25                             # the background tile (bg2.png, 512 px) is drawn at about a quarter size
# (sprite name, x, y, w, h, tint) for the main menu, from the UI map's measured spots.
MENU = [('icon-menu-profile', 49, 34, 62, 62, TEAL), ('icon-crown', 1446, 34, 62, 62, TEAL), ('icon-share', 1626, 34, 62, 62, TEAL),
        ('icon-friends', 1811, 32, 66, 66, TEAL), ('icon-remove', 2176, 36, 58, 58, TEAL),
        ('icon-iphone', 2333, 34, 183, 63, None), ('icon-android', 2332, 113, 183, 64, None), ('icon-discord', 2333, 193, 183, 63, None),
        ('logo', 1140, 535, 284, 62, None), ('icon-goto-previous', 951, 672, 62, 62, PINK), ('icon-goto-next', 1549, 672, 62, 62, PINK),
        ('icon-play', 1195, 794, 171, 171, None), ('icon-menu-invite', 1096, 1103, 66, 66, TEAL), ('icon-coin', 29, 1494, 87, 87, None)]
# (text, center x, top y, size, color)
MENU_TEXT = [('Player', 80, 100, 30, TEAL), ('Ranks', 1477, 104, 32, TEAL), ('Achievements', 1657, 104, 32, TEAL), ('Friends', 1844, 104, 32, TEAL),
             ('Quit game', 2205, 104, 32, TEAL), ('Split Party', 1280, 678, 46, PINK), ('Invite a friend', 1320, 1112, 40, TEAL)]
SCREENS = ('menu', 'match')
ARENA, ARENA_TILE = (236, 238, 240), 0.5   # the arena: worldbg.png's light triangles
ARENA_DARK = (46, 52, 60, 255)              # dark mode: the same pattern, darkened (close, not measured)
MODES = ('light', 'dark')
CONTROLS = {'pc': ('button-explain-split', 'button-explain-eject'), 'phone': ('button-split', 'button-eject')}
CONTROL_SPOTS = ((2320, 1360, 200), (2090, 1400, 160))   # split, eject: (x, y, size) at 2560 x 1600
FOOD = ('food1', 'food2', 'food3', 'food', 'foodc1', 'foodr1')   # the pellet shapes and blobs
FOOD_SIZE = 34
FOOD_COLORS = ('#f6c94c', '#e74c8b', '#3fb6c0', '#8b6cf0', '#7fd35a', '#f08a3c')


def font(size):
    for name in ('bahnschrift.ttf', 'segoeuisb.ttf', 'arial.ttf'):
        try: return ImageFont.truetype(str(Path('C:/Windows/Fonts') / name), size)
        except OSError: continue
    return ImageFont.load_default()


def sprite(lab, name):
    """The art the game shows for a sprite name, with your saved edits: its main copy (theme4.png / the assets.zip theme)."""
    rows = [r for r in lab.catalog.rows.values() if r['name'] == name and r['editable'] and not r.get('legacy')]
    if not rows: return None
    # The menu's title is the plain 500 x 110 one (theme4's copy carries the OG badge).
    rows.sort(key=lambda r: (not r.get('primary', True), (r['width'], r['height']) != (500, 110) if name == 'logo' else r['source'] != 'assets/theme4.png', r['source']))
    try: return lab.image(rows[0]['key']).convert('RGBA')
    except (OSError, ValueError, KeyError): return None


def tinted(im, color):
    """White icons are multiplied by one of the game's colors, as the game does."""
    if not color: return im
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    rgb = Image.merge('RGB', [c.point(lambda v, k=k: v * k // 255) for c, k in zip(im.convert('RGB').split(), (r, g, b))])
    out = rgb.convert('RGBA'); out.putalpha(im.getchannel('A')); return out


def place(canvas, im, x, y, w, h):
    s = min(w / im.width, h / im.height)
    small = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)
    canvas.alpha_composite(small, (round(x + (w - small.width) / 2), round(y + (h - small.height) / 2)))


def tiled(canvas, tile, scale):
    t = tile.resize((max(1, round(tile.width * scale)), max(1, round(tile.height * scale))), Image.Resampling.LANCZOS)
    for y in range(0, canvas.height, t.height):
        for x in range(0, canvas.width, t.width): canvas.alpha_composite(t, (x, y))


def menu(lab):
    canvas = Image.new('RGBA', (W, H), (*BG, 255))
    bg = sprite(lab, 'bg2.png')
    if bg: tiled(canvas, bg, TILE)
    for name, x, y, w, h, tint in MENU:
        im = sprite(lab, name)
        if im: place(canvas, tinted(im, tint), x, y, w, h)
    draw = ImageDraw.Draw(canvas)
    for text, cx, top, size, color in MENU_TEXT:
        f = font(size); width = draw.textlength(text, font=f)
        draw.text((cx - width / 2, top), text, font=f, fill=color)
    return canvas


def match(lab, mode='light', controls='pc'):
    """A match moment: the virus among cells and food on the arena's triangle pattern (worldbg.png; cell skins are left
    plain), with the split and eject buttons. Food is the game's white pellet art, each one painted its own color as the
    game does. mode: the game's light or dark mode (dark: the arena darkened, light_virus.png). controls: 'pc' shows the
    guided buttons (button-explain-*), 'phone' the touch ones (button-split / button-eject)."""
    canvas = Image.new('RGBA', (W, H), (*ARENA, 255))
    bg = sprite(lab, 'worldbg.png')
    if bg: tiled(canvas, bg, ARENA_TILE)
    if mode == 'dark':
        dark = Image.new('RGBA', canvas.size, ARENA_DARK)
        canvas = Image.merge('RGBA', [*ImageChops.multiply(canvas.convert('RGB'), dark.convert('RGB')).split(), canvas.getchannel('A')])
    draw = ImageDraw.Draw(canvas)
    rnd = random.Random(7)
    pellets = [(name, im) for name in FOOD for im in [sprite(lab, name)] if im]
    looks = {}
    for _ in range(220):
        x, y, color = rnd.randrange(W), rnd.randrange(H), rnd.choice(FOOD_COLORS)
        if not pellets:
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color); continue
        name, im = rnd.choice(pellets)
        if (name, color) not in looks:
            small = im.resize((FOOD_SIZE, FOOD_SIZE), Image.Resampling.LANCZOS)
            looks[name, color] = tinted(small, color)
        canvas.alpha_composite(looks[name, color], (x - FOOD_SIZE // 2, y - FOOD_SIZE // 2))
    for (x, y, r, color) in ((640, 900, 150, '#3fb6c0'), (1900, 520, 210, '#e5507a'), (1500, 1250, 90, '#f3b33d')):   # cells
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color, outline='#ffffff', width=6)
    virus = sprite(lab, 'light_virus.png' if mode == 'dark' else 'virus.png') or sprite(lab, 'virus.png')
    for (x, y, size) in ((1180, 640, 360), (1950, 1180, 300)):
        if virus: place(canvas, virus, x - size / 2, y - size / 2, size, size)
    # Split and eject, bottom right (where the game keeps its action buttons; a close placement, not measured in a match).
    # The art is white; the game shades it to read on the arena (dark on light, white on dark: a stand-in, not measured).
    for name, (x, y, size) in zip(CONTROLS[controls], CONTROL_SPOTS):
        im = sprite(lab, name)
        if not im: continue
        im = tinted(im, '#3a4450') if mode == 'light' else im
        im.putalpha(im.getchannel('A').point(lambda a: a * 3 // 4))
        place(canvas, im, x, y, size, size)
    return canvas


def render(lab, screen='menu', width=1280, mode='light', controls='pc'):
    if screen not in SCREENS: raise ValueError('Unknown preview screen.')
    if mode not in MODES or controls not in CONTROLS: raise ValueError('Unknown preview option.')
    image = menu(lab) if screen == 'menu' else match(lab, mode, controls)
    width = max(320, min(W, int(width)))
    return image.resize((width, round(H * width / W)), Image.Resampling.LANCZOS)
