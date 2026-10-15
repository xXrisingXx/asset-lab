"""Restyle the game's bitmap fonts: every character re-rendered with any installed font and effects.

Mitosis draws its text from BMFont pairs: a page image with every character, and an XML .fnt that says where each one
is and how far to move after it. A style here (typeface, size, width, fill or gradient, outlines, glow, shadow and more)
is rendered character by character at the game's own resolution, packed into a new page, and written with a new .fnt in
the same format, so the game reads it exactly like its own font. The font's nominal size stays the game's, so the game
scales the text the way it always did; the "size" setting makes the letters themselves bigger or smaller.

The lab's server is threaded and FreeType objects are not thread-safe, so every render holds LOCK; loaded typefaces,
parsed .fnt files and recent renderers (with the characters they drew) are kept, so a preview costs only what changed.
"""
from collections import OrderedDict
import json
import math
import os
import re
import threading
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# The game's fonts, by file stem: what each draws. The game colors some text itself (menu text), so a fill there
# mixes with its color, like the menu icons.
GAME_FONTS = {
    'nicktext': ('Player names', 'Names on the cells and in lists. Drawn as it is, so any colors work.'),
    'geosans_80': ('Menu text', 'Most text in the menus. The game colors it, so light fills keep its colors.'),
    'geosans_stroke_80': ('Outlined menu text', 'Menu text drawn with an outline. The game colors it too.'),
    'expinfo': ('Small numbers', 'A small numbers font.'),
}
# stops: 2 to 8 [position 0..1, color] pairs for a gradient fill ([] = the flat fill color); two stops at one position make
# a hard edge. angle: the gradient's direction in degrees, 0 = left to right, 90 = top to bottom. fill_opacity 0 = hollow.
# edge: the game's own letters keep the dark edge some of its fonts draw around them, and the fill colors the rest.
DEFAULT = dict(font='', size=1.0, stretch=1.0, spacing=0.0, bold=0.0, italic=0.0, caps=False, edge=True,
               fill='#ffffff', fill_opacity=1.0, stops=[], angle=90.0,
               outline='', outline_width=0.0, outline2='', outline2_width=0.0,
               glow='', glow_size=0.0, glow_strength=1.0, shadow='', shadow_x=0.0, shadow_y=0.0, shadow_blur=0.0, shadow_opacity=0.6)
LIMITS = dict(size=(0.5, 2.0), stretch=(0.5, 2.0), spacing=(-20, 40), bold=(0, 8), italic=(-0.4, 0.4), fill_opacity=(0, 1),
              outline_width=(0, 16), outline2_width=(0, 16), glow_size=(0, 32), glow_strength=(0, 4),
              shadow_x=(-24, 24), shadow_y=(-24, 24), shadow_blur=(0, 24), shadow_opacity=(0, 1))
MAX_STOPS = 8
COLOR = re.compile(r'#[0-9a-fA-F]{6}')
SUPER = 4          # letters drawn wider or narrower are rendered this many times larger, then scaled down: crisp edges
LOCK = threading.RLock()


def even(*colors):
    """Evenly spaced gradient stops."""
    return [[round(i / (len(colors) - 1), 4), c] for i, c in enumerate(colors)]


# Ready-made looks, grouped as the menu shows them. A font is a list of choices, the first one installed wins: Office fonts
# (Chiller, Showcard Gothic...) are only on some PCs, so every list ends with fonts that ship with Windows 10 and 11.
HEAVY = 'Segoe UI|Black, Arial|Black'
BLACK = 'Arial|Black, Segoe UI|Black'
CONDENSED = 'Bahnschrift|Bold Condensed, Impact|Regular'
SCRIPT = 'Segoe Script|Bold, Gabriola|Regular'
HAND = 'Segoe Print|Bold, Ink Free|Regular, Comic Sans MS|Bold'
ROUND = 'Arial Rounded MT Bold|Regular, Segoe UI|Black, Arial|Black'
SERIF = 'Georgia|Bold, Palatino Linotype|Bold, Sitka|Banner Bold'
SHADE = dict(shadow='#000000', shadow_y=3, shadow_blur=3, shadow_opacity=0.6)


def metal(light, mid, dark, rim, edge, font=HEAVY):
    """A polished metal: bright top, a horizon line across the middle, and light bouncing back up from below."""
    return dict(font=font, stops=[[0, light], [0.46, mid], [0.54, dark], [1, rim]], outline=edge, outline_width=3, **SHADE)


def gem(light, mid, dark, edge, glow):
    return dict(font=HEAVY, stops=even(light, mid, dark), outline=edge, outline_width=3, glow=glow, glow_size=8, glow_strength=0.9)


def neon(tube, core='#ffffff', size=12, strength=1.8, font='Segoe UI|Bold, Arial|Bold'):
    return dict(font=font, fill=core, outline=tube, outline_width=3, glow=tube, glow_size=size, glow_strength=strength)


PRESET_GROUPS = {
    'Classic': {
        'Game font': {},
        'Clean white': dict(font=HEAVY, fill='#ffffff', shadow='#000000', shadow_y=3, shadow_blur=2, shadow_opacity=0.55),
        'Bold outline': dict(font=BLACK, fill='#ffffff', outline='#000000', outline_width=4),
        'Black with a white glow': dict(font=CONDENSED, fill='#000000', outline='#ffffff', outline_width=2, glow='#ffffff', glow_size=8, glow_strength=1.4, caps=True),
        'Soft shadow': dict(font='Segoe UI|Bold, Arial|Bold', fill='#ffffff', shadow='#000000', shadow_x=2, shadow_y=4, shadow_blur=5, shadow_opacity=0.85),
        'Sports jersey': dict(font=CONDENSED, caps=True, spacing=2, fill='#ffffff', outline='#0b1f3a', outline_width=3, outline2='#f5b700', outline2_width=3),
        'Poster': dict(font='Impact|Regular, Arial|Black', caps=True, spacing=3, fill='#f2f2f2', outline='#111111', outline_width=2,
                       shadow='#000000', shadow_x=4, shadow_y=4, shadow_opacity=1.0),
        'Typewriter': dict(font='Consolas|Bold, Cascadia Code|Bold, Courier New|Bold', fill='#f4ecd8', outline='#2a2118', outline_width=2,
                           shadow='#000000', shadow_y=2, shadow_blur=2, shadow_opacity=0.6),
        'Storybook': dict(font=SERIF, fill='#fff4d6', outline='#3b2412', outline_width=3, shadow='#000000', shadow_x=1, shadow_y=3, shadow_blur=2, shadow_opacity=0.6),
        'Hollow': dict(font=BLACK, fill='#ffffff', fill_opacity=0.0, outline='#ffffff', outline_width=3, outline2='#111111', outline2_width=1),
        'Wide & loud': dict(font=BLACK, stretch=1.35, caps=True, fill='#ffe14d', outline='#000000', outline_width=4),
        'Tall & thin': dict(font='Bahnschrift|SemiBold Condensed, Arial|Bold', stretch=0.8, caps=True, spacing=3, fill='#ffffff', outline='#000000', outline_width=2),
    },
    'Metal & gems': {
        'Gold': metal('#fffbe0', '#ffd54a', '#c98a12', '#ffe08a', '#4a2a00'),
        'Silver': metal('#ffffff', '#d5dce4', '#7d8794', '#eef2f6', '#1f252d'),
        'Chrome': dict(font='Bahnschrift|Bold, Arial|Black', caps=True, stops=[[0, '#bfe3ff'], [0.48, '#ffffff'], [0.5, '#2f3742'], [0.66, '#7f8b9b'], [1, '#f2f6fa']],
                       outline='#0f1318', outline_width=2, outline2='#cfd8e3', outline2_width=2),
        'Bronze': metal('#ffe2c0', '#e0975a', '#9a531f', '#f2b680', '#2e1405'),
        'Rose gold': metal('#fff1ec', '#f6c0b3', '#c97b6c', '#ffd9cf', '#4a2420'),
        'Ruby': gem('#ff9fb5', '#e0115f', '#7a0026', '#2b0010', '#ff2a6d'),
        'Emerald': gem('#b4ffdf', '#10b981', '#065f46', '#022c22', '#34d399'),
        'Sapphire': gem('#b3d4ff', '#2563eb', '#1e1b6b', '#0a0f2e', '#3b82f6'),
        'Amethyst': gem('#efd6ff', '#9b5de5', '#4c1d95', '#1e0b3a', '#a855f7'),
        'Diamond': dict(font=HEAVY, stops=[[0, '#ffffff'], [0.45, '#d6f6ff'], [0.55, '#9fe3ff'], [1, '#ffffff']], angle=45.0,
                        outline='#2a6f9a', outline_width=2, glow='#ffffff', glow_size=10, glow_strength=1.2),
    },
    'Neon & glow': {
        'Neon pink': neon('#ff2ec4'),
        'Neon blue': neon('#1fa2ff'),
        'Neon green': neon('#39ff14', core='#f4fff0', strength=1.6),
        'Ultraviolet': neon('#8f3dff', size=14, strength=1.9),
        'Neon sign': neon('#ff3fd2', core='#fff2fc', size=14, strength=2.2, font='Segoe UI|Semilight, Segoe UI|Light, Arial|Regular'),
        'Synthwave': dict(font=HEAVY, stops=even('#fff275', '#ff2ec4', '#6a00f4'), outline='#12003a', outline_width=3, glow='#ff2ec4', glow_size=10, glow_strength=1.2),
        'Cyber': dict(font='Bahnschrift|SemiBold SemiCondensed, Bahnschrift|Bold, Arial|Bold', caps=True, spacing=3, stops=even('#00f0ff', '#ff00a0'), angle=0.0,
                      outline='#0d0221', outline_width=2, glow='#00e5ff', glow_size=8, glow_strength=1.0),
        'Laser red': neon('#ff1f1f', core='#fff0f0', strength=2.0),
        'Electric': neon('#ffd000', core='#ffffff', strength=1.8, font=CONDENSED),
        'Hologram': dict(font=HEAVY, stops=even('#ffb3ec', '#c9b6ff', '#a8d8ff', '#a9fff1', '#f3ffb0'), angle=45.0, fill_opacity=0.9,
                         outline='#ffffff', outline_width=1, glow='#c9b6ff', glow_size=10, glow_strength=1.2),
    },
    'Elements': {
        'Fire': dict(font=HEAVY, stops=[[0, '#c1121f'], [0.4, '#ff5a1f'], [0.75, '#ffb703'], [1, '#fff3b0']],
                     outline='#3b0000', outline_width=2, glow='#ff5a00', glow_size=10, glow_strength=1.4),
        'Ice': dict(font=HEAVY, stops=even('#ffffff', '#7fd8ff'), outline='#0b3a5c', outline_width=3, glow='#bfefff', glow_size=10, glow_strength=1.2),
        'Lava': dict(font=BLACK, stops=even('#ffe066', '#ff6a00', '#b80000'), outline='#1a0500', outline_width=4, glow='#ff3c00', glow_size=12, glow_strength=1.5),
        'Ocean': dict(font=HEAVY, stops=[[0, '#e0fbfc'], [0.45, '#48cae4'], [1, '#0077b6']], outline='#03045e', outline_width=3, **SHADE),
        'Toxic': dict(font=HEAVY, stops=even('#eaff7b', '#3fae1a'), outline='#0b2a05', outline_width=3, glow='#7dff2e', glow_size=8, glow_strength=1.2),
        'Storm': dict(font='Bahnschrift|Bold, Arial|Black', caps=True, italic=0.12, fill='#f5f9ff', outline='#3d5afe', outline_width=2,
                      glow='#8ab4ff', glow_size=12, glow_strength=1.6),
        'Galaxy': dict(font=HEAVY, stops=even('#9be7ff', '#b06cff', '#6a3df0', '#ff6ec7'), angle=45.0, outline='#0b0033', outline_width=3,
                       glow='#a46bff', glow_size=10, glow_strength=1.2),
        'Forest': dict(font='Trebuchet MS|Bold, Georgia|Bold', stops=even('#d8f3a5', '#4f772d'), outline='#1b2a10', outline_width=3, **SHADE),
        'Stone': dict(font='Impact|Regular, Arial|Black', caps=True, spacing=2, stops=even('#e2dfd8', '#7a756c'), outline='#1f1d1a', outline_width=3,
                      shadow='#000000', shadow_x=2, shadow_y=3, shadow_opacity=1.0),
    },
    'Color blends': {
        'Rainbow': dict(font=HEAVY, stops=even('#ff2d55', '#ff9500', '#ffe600', '#34d058', '#00c7ff', '#5e5ce6', '#d83bff'), outline='#1a1a2e', outline_width=3),
        'Sunset': dict(font=HEAVY, stops=even('#ffd166', '#ff6a3d', '#a4148f'), outline='#2b1055', outline_width=3),
        'Cotton candy': dict(font=ROUND, stops=even('#ff9ad5', '#8fd3ff'), angle=0.0, outline='#ffffff', outline_width=3, outline2='#ff6fb5', outline2_width=2),
        'Pastel': dict(font=ROUND, stops=even('#ffc6ff', '#bdb2ff', '#a0c4ff', '#caffbf'), angle=0.0, outline='#5a4b7a', outline_width=2),
        'Aurora': dict(font=HEAVY, stops=even('#00f5d4', '#7bf1a8', '#9b5de5'), angle=45.0, outline='#001d3d', outline_width=3, glow='#00f5d4', glow_size=8, glow_strength=0.9),
        'Vaporwave': dict(font=HEAVY, stops=even('#ff71ce', '#01cdfe', '#05ffa1'), outline='#2d1b4e', outline_width=3),
        'Fire & ice': dict(font=BLACK, stops=[[0, '#ffb703'], [0.5, '#ff3d00'], [0.5, '#4fc3ff'], [1, '#1565c0']], outline='#0d0d1a', outline_width=3),
        'Peach': dict(font=ROUND, stops=even('#ffe5b4', '#ff8c69'), outline='#5a2a1a', outline_width=2),
        'Mint': dict(font=ROUND, stops=even('#e0fff4', '#3ddc97'), outline='#0f3d2e', outline_width=3),
        'Half & half': dict(font=BLACK, stops=[[0, '#ffffff'], [0.5, '#ffffff'], [0.5, '#ffcc00'], [1, '#ffcc00']], angle=45.0, outline='#000000', outline_width=3),
    },
    'Fun & handwritten': {
        'Comic': dict(font='Comic Sans MS|Bold', fill='#ffe14d', outline='#000000', outline_width=4, shadow='#000000', shadow_x=3, shadow_y=4, shadow_blur=0, shadow_opacity=1.0),
        'Bubble': dict(font=ROUND, stops=even('#c9efff', '#3aa0ff'), outline='#ffffff', outline_width=3, outline2='#1d4e89', outline2_width=3),
        'Bubblegum': dict(font='Comic Sans MS|Bold', stops=even('#ffc2e6', '#ff5fb8'), outline='#ffffff', outline_width=3, outline2='#c2185b', outline2_width=2),
        'Marker': dict(font='Ink Free|Regular, Segoe Print|Bold', size=1.1, bold=1, fill='#ffffff', shadow='#000000', shadow_x=2, shadow_y=3, shadow_blur=1, shadow_opacity=0.7),
        'Handwritten': dict(font=HAND, fill='#fff7e6', outline='#2b2b2b', outline_width=2),
        'Signature': dict(font=SCRIPT, size=1.1, fill='#ffffff', glow='#ffd27a', glow_size=8, glow_strength=1.0),
        'Graffiti': dict(font='Jokerman|Regular, Ink Free|Regular, Comic Sans MS|Bold', size=1.05, bold=1, stops=even('#a6ff00', '#00c2ff'), angle=45.0,
                         outline='#000000', outline_width=4, outline2='#ffffff', outline2_width=2),
        'Chalk': dict(font='Segoe Print|Regular, Ink Free|Regular', fill='#f5f5f0', fill_opacity=0.85, glow='#ffffff', glow_size=4, glow_strength=0.5,
                      shadow='#000000', shadow_y=2, shadow_blur=2, shadow_opacity=0.5),
        'Retro arcade': dict(font='Bahnschrift|Bold, Impact|Regular', caps=True, spacing=2, stops=[[0, '#fff200'], [0.5, '#ff8c00'], [0.5, '#ff3d00'], [1, '#ff007b']],
                             outline='#1a0033', outline_width=3, outline2='#00e5ff', outline2_width=2),
        'Royal': dict(font=SERIF, stops=[[0, '#fff6c2'], [0.5, '#ffd54a'], [1, '#c98b00']], outline='#3d0a5c', outline_width=3, outline2='#ffd54a', outline2_width=1),
        'Wild west': dict(font='Rockwell Extra Bold|Regular, Georgia|Bold', caps=True, spacing=1, fill='#f3d9a4', outline='#3b2412', outline_width=3,
                          shadow='#1a0f05', shadow_x=3, shadow_y=3, shadow_opacity=1.0),
    },
    'Spooky & seasonal': {
        'Spooky': dict(font='Chiller|Regular, Ink Free|Regular', size=1.15, bold=1, stops=even('#ff8a1a', '#b31b00'), outline='#1a0000', outline_width=3,
                       glow='#ff5a00', glow_size=10, glow_strength=1.3, caps=True),
        'Pumpkin': dict(font=HEAVY, stops=even('#ffc46b', '#ff6a00'), outline='#2a0a3d', outline_width=3, glow='#b6ff3b', glow_size=8, glow_strength=0.9),
        'Slime': dict(font='Comic Sans MS|Bold', stops=even('#d4ff6b', '#57c21b'), outline='#173300', outline_width=3, glow='#9dff3b', glow_size=10, glow_strength=1.2),
        'Ghost': dict(font=HAND, fill='#ffffff', fill_opacity=0.55, glow='#e8f6ff', glow_size=12, glow_strength=1.6,
                      shadow='#0b1020', shadow_y=5, shadow_blur=4, shadow_opacity=0.45),
        'Blood': dict(font='Chiller|Regular, Ink Free|Regular', size=1.15, bold=1, stops=even('#ff7070', '#c00000', '#6a0000'), outline='#140000', outline_width=3,
                      glow='#ff1a1a', glow_size=6, glow_strength=0.7),
        'Candy cane': dict(font=ROUND, stops=[[0, '#e01e37'], [0.25, '#e01e37'], [0.25, '#ffffff'], [0.5, '#ffffff'], [0.5, '#e01e37'], [0.75, '#e01e37'],
                                              [0.75, '#ffffff'], [1, '#ffffff']], angle=45.0, outline='#7a0010', outline_width=3),
        'Christmas': dict(font=SERIF, stops=even('#ff4d4d', '#a4161a'), outline='#ffffff', outline_width=3, outline2='#1a7f37', outline2_width=3),
        'Frost': dict(font=HEAVY, stops=even('#ffffff', '#cdefff'), outline='#5fb8e8', outline_width=2, glow='#ffffff', glow_size=10, glow_strength=1.3),
        'Valentine': dict(font='Segoe UI|Black, Comic Sans MS|Bold', stops=even('#ffd1e3', '#ff2d6f'), outline='#ffffff', outline_width=2, outline2='#c2185b', outline2_width=2,
                          glow='#ff6fa5', glow_size=8, glow_strength=1.0),
        'Lucky': dict(font=HEAVY, stops=even('#7dff9b', '#0f9d58'), outline='#ffd700', outline_width=2, outline2='#0b3d1a', outline2_width=2),
        'Spring': dict(font=ROUND, stops=even('#fff6a3', '#ffc6e0', '#b5e8ff'), outline='#7a5c99', outline_width=2),
    },
}
PRESETS = {name: style for group in PRESET_GROUPS.values() for name, style in group.items()}


# ----- the game's .fnt files ------------------------------------------------------------------------------------
def attrs(tag):
    return dict(re.findall(r'(\w+)="([^"]*)"', tag))


def parse_fnt(text):
    info, common = attrs(re.search(r'<info[^>]*>', text).group(0)), attrs(re.search(r'<common[^>]*>', text).group(0))
    pages = [attrs(t) for t in re.findall(r'<page [^>]*>', text)]
    chars = {int(a['id']): {k: (int(v) if re.fullmatch(r'-?\d+', v) else v) for k, v in a.items()} for a in (attrs(t) for t in re.findall(r'<char [^>]*>', text))}
    kernings = [(int(a['first']), int(a['second']), int(a['amount'])) for a in (attrs(t) for t in re.findall(r'<kerning [^>]*>', text))]
    return dict(info=info, common=common, pages=pages, chars=chars, kernings=kernings)


_PARSED = OrderedDict()


def parsed(text):
    """parse_fnt, remembered: a preview should not re-read 700 characters' metrics."""
    with LOCK:
        if text not in _PARSED:
            _PARSED[text] = parse_fnt(text)
            while len(_PARSED) > 8: _PARSED.popitem(last=False)
        return _PARSED[text]


_GAME = {}


def game_fonts(game):
    """The game's fonts that can be restyled: {id: dict(label, note, fnt, page, face, chars)}."""
    out = {}
    for stem, (label, note) in GAME_FONTS.items():
        fnt = Path(game) / 'assets' / f'{stem}.fnt'
        try: stat = fnt.stat()
        except OSError: continue
        known = _GAME.get(str(fnt))
        if not known or known[0] != (stat.st_mtime_ns, stat.st_size):
            known = _GAME[str(fnt)] = ((stat.st_mtime_ns, stat.st_size), parse_fnt(fnt.read_text(encoding='utf-8', errors='replace')))
        info = known[1]
        if len(info['pages']) != 1: continue   # the game's fonts have one page; anything else is left alone
        out[stem] = dict(id=stem, label=label, note=note, fnt=f'assets/{stem}.fnt', page='assets/' + info['pages'][0]['file'],
                         face=info['info'].get('face', ''), chars=len(info['chars']))
    return out


# ----- installed typefaces -----------------------------------------------------------------------------------------
def font_dirs():
    return [Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts', Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'Windows' / 'Fonts']


def system_fonts(cache_file=None):
    """Installed typefaces as [dict(id, family, style)]; id is 'path' or 'path#named instance' for variable fonts."""
    files = sorted(p for d in font_dirs() if d.is_dir() for p in d.iterdir() if p.suffix.lower() in ('.ttf', '.otf', '.ttc'))
    signature = f'{len(files)}|' + '|'.join(f'{p.name}:{int(p.stat().st_mtime)}' for p in files)
    if cache_file:
        try:
            saved = json.loads(Path(cache_file).read_text(encoding='utf-8'))
            if saved.get('signature') == signature: return saved['fonts']
        except (OSError, ValueError, KeyError): pass
    fonts = []
    for p in files:
        try: f = ImageFont.truetype(str(p), 24)
        except (OSError, ValueError): continue
        family, style = f.getname()
        if not family: continue
        try: instances = [n.decode('utf-8', 'replace') if isinstance(n, bytes) else n for n in f.get_variation_names()]
        except (OSError, ValueError, AttributeError): instances = []
        if instances: fonts += [dict(id=f'{p}#{name}', family=family, style=name) for name in instances]
        else: fonts.append(dict(id=str(p), family=family, style=style or 'Regular'))
    seen, unique = set(), []
    for f in sorted(fonts, key=lambda f: (f['family'].lower(), f['style'].lower())):
        if (f['family'], f['style']) in seen: continue
        seen.add((f['family'], f['style'])); unique.append(f)
    if cache_file:
        Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_file).write_text(json.dumps(dict(signature=signature, fonts=unique)), encoding='utf-8')
    return unique


def find_font(fonts, name):
    """A typeface by id, by 'Family|Style', or by family name alone (its boldest regular-width style)."""
    if not name: return None
    for f in fonts:
        if f['id'] == name or f"{f['family']}|{f['style']}".lower() == name.lower(): return f
    family = [f for f in fonts if f['family'].lower() == name.split('|')[0].strip().lower()]
    if not family: return None
    rank = lambda f: (('condensed' in f['style'].lower()) and 'condensed' not in name.lower(), not re.search(r'bold|black|heavy', f['style'], re.I), f['style'])
    return sorted(family, key=rank)[0]


def pick_font(fonts, name):
    """The first installed typeface of a choice list such as 'Chiller|Regular, Ink Free|Regular' (each an id, a
    'Family|Style' or a family), so a look made on one PC still works on a PC without its first font. Exact matches
    come first; only then does a family stand in for a style it lacks."""
    if not name or fonts is None: return None
    parts = [name] if any(f['id'] == name for f in fonts) else [p.strip() for p in name.split(',') if p.strip()]   # an id may hold a comma
    exact = lambda part: next((f for f in fonts if f['id'] == part or f"{f['family']}|{f['style']}".lower() == part.lower()), None)
    return next((f for f in map(exact, parts) if f), None) or next((f for f in (find_font(fonts, p) for p in parts) if f), None)


def load_font(entry, px):
    path, _, instance = entry['id'].partition('#')
    font = ImageFont.truetype(path, max(4, int(round(px))))
    if instance:
        try: font.set_variation_by_name(instance)
        except (OSError, ValueError): pass
    return font


_TYPEFACES = OrderedDict()


def typeface(entry, px):
    """A typeface at a pixel size, loaded once and kept (callers hold LOCK: FreeType objects are not thread-safe)."""
    key = (entry['id'], max(4, int(round(px))))
    with LOCK:
        font = _TYPEFACES.get(key)
        if font is None:
            font = _TYPEFACES[key] = load_font(entry, key[1])
            while len(_TYPEFACES) > 64: _TYPEFACES.popitem(last=False)
        else: _TYPEFACES.move_to_end(key)
        return font


# ----- a style ------------------------------------------------------------------------------------------------------------
def number(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clean_style(raw, fonts=None):
    """A complete, safe style from anything (a preset, a request, a style saved by an older version): numbers clamped,
    colors checked, 2 to 8 gradient stops in order, and the typeface resolved to an installed one when the list is given."""
    s = dict(DEFAULT)
    raw = raw if isinstance(raw, dict) else {}
    for k, (lo, hi) in LIMITS.items():
        v = raw.get(k, DEFAULT[k])
        s[k] = float(min(hi, max(lo, v))) if number(v) else float(DEFAULT[k])
    for k in ('fill', 'outline', 'outline2', 'glow', 'shadow'):
        v = raw.get(k, DEFAULT[k])
        s[k] = v.lower() if isinstance(v, str) and COLOR.fullmatch(v) else ('' if k != 'fill' else DEFAULT['fill'])
    s['caps'] = raw.get('caps') is True
    s['edge'] = raw['edge'] is True if 'edge' in raw else 'fill2' not in raw   # v1.0.15 colored that edge too: its styles still do
    s['angle'] = float(round(raw['angle']) % 360) if number(raw.get('angle')) else DEFAULT['angle']
    stops = [[round(min(1.0, max(0.0, float(at))), 4), color.lower()] for at, color in
             (item for item in (raw.get('stops') if isinstance(raw.get('stops'), list) else [])[:32] if isinstance(item, (list, tuple)) and len(item) == 2)
             if number(at) and isinstance(color, str) and COLOR.fullmatch(color)]
    if len(stops) < 2 and isinstance(raw.get('fill2'), str) and COLOR.fullmatch(raw['fill2']):
        stops = [[0.0, s['fill']], [1.0, raw['fill2'].lower()]]   # saved by v1.0.15: the fill fading down into fill2
    stops.sort(key=lambda stop: stop[0])                           # stable: two stops at one spot keep their order (a hard edge)
    s['stops'] = stops[:MAX_STOPS] if len(stops) >= 2 else []
    font = raw.get('font') if isinstance(raw.get('font'), str) else ''
    s['font'] = (pick_font(fonts, font) or {}).get('id', '') if fonts is not None and font else font[:300]
    return s


def style_key(style): return json.dumps(style, sort_keys=True)


def rgb(color): return np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def edge_color(page):
    """The dark edge some game fonts draw around their white letters (RGB 0..1), or None; worked out once per page."""
    if 'lab_edge' not in page.__dict__:
        px = np.asarray(page)[::3, ::3].reshape(-1, 4).astype(np.float32) / 255
        solid = px[px[:, 3] > 0.5, :3]   # player names' black edge is a little see-through
        dark = solid[solid @ LUMA < 0.5]
        page.lab_edge = dark.mean(axis=0) if len(dark) > 0.02 * len(solid) else None
    return page.lab_edge


def ramp(stops, t, px):
    """The gradient's color at every position t (0 at the first stop's end, 1 at the last). Two stops at one spot make a
    hard edge; it gets one pixel (px, in t) of blend so it stays smooth at any angle instead of turning into steps."""
    at = []
    for x, _ in stops: at.append(x if not at else max(x, at[-1] + px))
    colors = np.stack([rgb(c) for _, c in stops])
    return np.stack([np.interp(t, at, colors[:, i]) for i in range(3)], axis=-1).astype(np.float32)


def pad_for(s):
    """Room around each character for its outlines, glow and shadow, so no effect is ever cut off square."""
    ring = s['bold'] + s['outline_width'] + s['outline2_width']
    return int(math.ceil(max(ring + s['glow_size'] * 2.0, ring + max(abs(s['shadow_x']), abs(s['shadow_y'])) + s['shadow_blur'] * 2.2, ring) + 3))


def edge_fade(W, H, p):
    """1 inside a character's picture, easing to 0 over the outer part of its room: the faint end of a big glow or soft
    shadow fades out there instead of stopping in a straight line (a box around each letter in the game)."""
    near = lambda n: np.minimum(np.arange(n), np.arange(n)[::-1]).astype(np.float32) + 0.5
    f = np.clip(np.minimum(near(H)[:, None], near(W)[None, :]) / max(2.0, p * 0.4), 0, 1)
    return f * f * (3 - 2 * f)


class Renderer:
    """Renders characters for one game font in one style; keep=True remembers each character it drew."""

    def __init__(self, fnt_text, page, style, fonts, keep=False):
        self.src = parsed(fnt_text)
        self.source = page   # kept, so a cached renderer's page can never be mistaken for another
        self.page = page if page.mode == 'RGBA' else page.convert('RGBA')
        self.s, self.fonts = style, fonts
        self.em = abs(int(self.src['info'].get('size', '-64'))) * style['size']
        entry = pick_font(fonts, style['font']) if style['font'] else None
        self.faces = []   # (typeface entry, font at the letters' size): the chosen one, then fallbacks for characters it lacks
        if entry:
            try: self.faces.append((entry, typeface(entry, self.em)))
            except OSError: entry = None   # the font file went away: the game's own letters, restyled
        self.face = entry['family'] if entry else self.src['info'].get('face', '')
        self.font = self.faces[0][1] if self.faces else None
        if entry:
            for name in ('Segoe UI|Black', 'Segoe UI|Bold', 'Arial|Bold', 'Segoe UI Symbol|Regular'):
                e = find_font(fonts, name)
                if e:
                    try: self.faces.append((e, typeface(e, self.em)))
                    except OSError: pass
        self.base = int(round(int(self.src['common']['base']) * style['size']))
        self.line = int(round(int(self.src['common']['lineHeight']) * style['size']))
        self.pad = pad_for(style)
        self.edge = edge_color(self.page) if style['edge'] else None
        self.cap = self.cap_height()
        self.missing, self.cache = 0, ({} if keep else None)

    def cap_height(self):
        """How far the capitals reach above the baseline: the height a gradient runs over, the same for every letter."""
        if self.font is not None:
            try:
                top = self.font.getbbox('H', anchor='ls')[1]
                if top < 0: return float(-top)
            except (OSError, ValueError): pass
        c = self.src['chars'].get(72)
        if c and c['width'] > 0 and c['height'] > 0:
            crop = self.page.crop((c['x'], c['y'], c['x'] + c['width'], c['y'] + c['height']))
            part = self.light(crop)
            box = (crop.getchannel('A') if part is None else part).getbbox()
            if box: return max(1.0, (int(self.src['common']['base']) - c['yoffset'] - box[1]) * self.s['size'])
        return self.em * 0.72

    def light(self, crop):
        """The part of a game letter the fill colors when its dark edge is kept ('L'), or None: all of it."""
        if self.edge is None: return None
        px, e = np.asarray(crop, np.float32) / 255, float(self.edge @ LUMA)
        part = np.clip((px[..., :3] @ LUMA - e - 0.03) / max(0.05, 0.93 - e), 0, 1) * px[..., 3]   # near-white is white, near the edge's color is edge
        return Image.fromarray((part * 255 + 0.5).astype(np.uint8))

    def has(self, font, text):
        """Whether a typeface really has this character (a missing one draws as its 'not defined' box)."""
        if text.isspace(): return True
        known = font.__dict__.setdefault('lab_has', {})
        if text not in known:
            try:
                if 'lab_notdef' not in font.__dict__:
                    m = font.getmask('\uffff'); font.lab_notdef = (m.size, bytes(m))
                mask = font.getmask(text)
                known[text] = (mask.size, bytes(mask)) != font.lab_notdef
            except (OSError, ValueError): known[text] = False
        return known[text]

    def shape(self, cid):
        """(mask image, origin x, origin y on the mask = the pen at the baseline, xadvance, light) for one character; light
        is the part the fill colors ('L', same size as the mask) when it is not the whole letter, else None."""
        text = chr(cid)
        if self.s['caps'] and text.islower() and len(text.upper()) == 1: text = text.upper()
        wide = self.s['stretch']
        if self.font is not None:
            entry, font = next(((e, f) for e, f in self.faces if self.has(f, text)), (None, None))
            if font is not None:
                bold = int(round(self.s['bold']))
                advance = font.getlength(text) * wide + bold
                if wide == 1:
                    l, t, r, b = font.getbbox(text, anchor='ls', stroke_width=bold)
                    if r <= l or b <= t: return None, 0, 0, advance, None
                    mask = Image.new('L', (r - l, b - t), 0)
                    ImageDraw.Draw(mask).text((-l, -t), text, font=font, fill=255, anchor='ls', stroke_width=bold, stroke_fill=255)
                    return mask, -l, -t, advance, None
                # Wider or narrower letters: drawn SUPER times larger and scaled down, so their edges stay crisp. The pen
                # lands on whole pixels of the result, so the baseline and spacing match the regular letters.
                big, n = typeface(entry, self.em * SUPER), SUPER
                l, t, r, b = big.getbbox(text, anchor='ls', stroke_width=bold * n)
                if r <= l or b <= t: return None, 0, 0, advance, None
                ox, oy = math.ceil(-l * wide / n), math.ceil(-t / n)
                w, h = max(1, math.ceil((ox * n / wide + r) * wide / n)), max(1, math.ceil((oy * n + b) / n))
                mask = Image.new('L', (max(1, round(w * n / wide)), h * n), 0)
                ImageDraw.Draw(mask).text((ox * n / wide, oy * n), text, font=big, fill=255, anchor='ls', stroke_width=bold * n, stroke_fill=255)
                return mask.resize((w, h), Image.Resampling.BOX), ox, oy, advance, None
            self.missing += 1
        # The game's own character, scaled: the shape is the game font's, every effect is applied to it. Its dark edge, if
        # the font draws one, stays dark (see light), so the default style gives back the game's own letters.
        c = self.src['chars'].get(cid)
        kx, ky = self.s['size'] * wide, self.s['size']
        if not c or c['width'] <= 0 or c['height'] <= 0: return None, 0, 0, (c or {}).get('xadvance', 0) * kx, None
        crop = self.page.crop((c['x'], c['y'], c['x'] + c['width'], c['y'] + c['height']))
        if (kx, ky) != (1, 1): crop = crop.resize((max(1, round(crop.width * kx)), max(1, round(crop.height * ky))), Image.Resampling.LANCZOS)
        mask, light = crop.getchannel('A'), self.light(crop)
        bold = int(round(self.s['bold']))
        if bold: mask, light = thicken(mask, bold), (None if light is None else thicken(light, bold))
        # Pen at the baseline: the original box sits xoffset right of the pen and yoffset below the line top.
        return mask, -c['xoffset'] * kx + bold, (int(self.src['common']['base']) - c['yoffset']) * ky + bold, c['xadvance'] * kx + bold, light

    def fill(self, W, H, x0, y0, width, slant):
        """The letter's color: flat, or the gradient laid over this character's own box, its advance across and the cap
        height down to the baseline, in the style's direction. Every letter is its own picture in the game's font, so an
        up-and-down gradient lines up along a whole name and a left-to-right one runs across each letter."""
        s = self.s
        if not s['stops']: return rgb(s['fill'])
        a = math.radians(s['angle'])
        dx, dy = math.cos(a), math.sin(a)
        w, h = max(1.0, width), max(1.0, self.cap)
        span = max(1.0, abs(w * dx) + abs(h * dy))   # the box's corners land exactly on the first and last stops
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32) + 0.5
        if slant: xs = xs + slant * (ys - y0)        # slanted letters carry their gradient with them
        t = ((xs - x0 - w / 2) * dx + (ys - y0 + h / 2) * dy) / span + 0.5
        return ramp(s['stops'], t, 1 / span)

    def glyph(self, cid):
        """(RGBA image or None, xoffset, yoffset, xadvance) in the new font's terms."""
        if self.cache is not None and cid in self.cache: return self.cache[cid]
        out = self.draw(cid)
        if self.cache is not None: self.cache[cid] = out
        return out

    def draw(self, cid):
        mask, ox, oy, width, light = self.shape(cid)
        advance = int(round(width + self.s['spacing']))
        if mask is None: return None, 0, 0, advance
        s, p = self.s, self.pad
        slant = s['italic']
        extra_l, extra_r = int(math.ceil(max(0, slant) * (mask.height - oy) + max(0, -slant) * oy)), int(math.ceil(max(0, slant) * oy + max(0, -slant) * (mask.height - oy)))
        W, H = mask.width + 2 * p + extra_l + extra_r, mask.height + 2 * p
        body = Image.new('L', (W, H), 0); body.paste(mask, (p + extra_l, p))
        lit = None
        if light is not None: lit = Image.new('L', (W, H), 0); lit.paste(light, (p + extra_l, p))
        if slant:
            by = p + oy   # shear around the baseline: letters lean, the baseline stays put
            lean = lambda im: im.transform((W, H), Image.Transform.AFFINE, (1, slant, -slant * by, 0, 1, 0), resample=Image.Resampling.BICUBIC)
            body, lit = lean(body), (None if lit is None else lean(lit))
        a_body = np.asarray(body, np.float32) / 255
        a_fill = a_body if lit is None else np.minimum(np.asarray(lit, np.float32) / 255, a_body)
        layers = []
        o1 = round_grow(body, s['outline_width']) if s['outline'] and s['outline_width'] else body
        o2 = round_grow(o1, s['outline2_width']) if s['outline2'] and s['outline2_width'] else o1
        outer = np.asarray(o2, np.float32) / 255
        soft = edge_fade(W, H, p) if s['glow_size'] or s['shadow_blur'] else 1
        if s['shadow'] and (s['shadow_x'] or s['shadow_y'] or s['shadow_blur']):
            moved = Image.new('L', (W, H), 0); moved.paste(o2, (int(round(s['shadow_x'])), int(round(s['shadow_y']))))
            if s['shadow_blur']: moved = moved.filter(ImageFilter.GaussianBlur(s['shadow_blur']))
            layers.append((rgb(s['shadow']), np.asarray(moved, np.float32) / 255 * s['shadow_opacity'] * (soft if s['shadow_blur'] else 1)))
        if s['glow'] and s['glow_size'] and s['glow_strength']:
            g = o2.filter(ImageFilter.GaussianBlur(s['glow_size'] * 0.5))
            g2 = o2.filter(ImageFilter.GaussianBlur(s['glow_size'] * 1.1))
            halo = np.clip((np.asarray(g, np.float32) * 0.9 + np.asarray(g2, np.float32) * 0.5) / 255 * s['glow_strength'], 0, 1) * soft
            layers.append((rgb(s['glow']), halo))
        if o2 is not o1: layers.append((rgb(s['outline2']), outer))
        if o1 is not body: layers.append((rgb(s['outline']), np.asarray(o1, np.float32) / 255))
        color, alpha = np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32)
        for c, a in layers:   # back to front, "over", premultiplied
            color = c * a[..., None] + color * (1 - a[..., None])
            alpha = a + alpha * (1 - a)
        # The letter on top. What lies under it is cut out, so a see-through or hollow letter shows the game behind it
        # rather than its own outline and glow; at full fill this is exactly "over". A game letter's own dark edge stays.
        fill, seen = self.fill(W, H, p + extra_l + ox, p + oy, width, slant), a_fill * s['fill_opacity']
        edge = a_body - a_fill
        color = fill * seen[..., None] + (0 if lit is None else self.edge * edge[..., None]) + color * (1 - a_body)[..., None]
        alpha = seen + edge + alpha * (1 - a_body)
        out = np.dstack([np.clip(color / np.maximum(alpha, 1e-6)[..., None], 0, 1) * 255, alpha * 255])
        img = Image.fromarray((out + 0.5).astype(np.uint8), 'RGBA')
        box = img.getchannel('A').point(lambda v: 255 if v > 1 else 0).getbbox()
        if not box: return None, 0, 0, advance
        img = img.crop(box)
        xoff = box[0] - (p + extra_l + ox)
        yoff = self.base - (p + oy) + box[1]
        return img, int(round(xoff)), int(round(yoff)), advance

    def kernings(self, ids):
        wide = self.s['stretch']
        if self.font is None: return [(a, b, int(round(k * self.s['size'] * wide))) for a, b, k in self.src['kernings'] if a in ids and b in ids]
        out, ascii_ids = [], [i for i in ids if 33 <= i < 127]
        caps = lambda c: c.upper() if self.s['caps'] and c.islower() else c
        width = {i: self.font.getlength(caps(chr(i))) for i in ascii_ids}
        for a in ascii_ids:
            for b in ascii_ids:
                k = round((self.font.getlength(caps(chr(a)) + caps(chr(b))) - width[a] - width[b]) * wide)
                if k: out.append((a, b, int(k)))
        return out


def round_grow(img, w):
    """The shape grown by w pixels with nearly round corners: one-pixel steps alternating a plus and a square give an
    octagon, which keeps soft edges and costs a few array operations per pixel of width."""
    steps = int(round(w))
    if steps < 1: return img
    out = np.asarray(img, np.uint8)
    for i in range(steps):
        p = np.pad(out, 1)
        near = [p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]]
        if i % 2: near += [p[:-2, :-2], p[:-2, 2:], p[2:, :-2], p[2:, 2:]]
        out = np.maximum.reduce(near)
    return Image.fromarray(out)


def thicken(mask, w):
    """Faux bold for the game's own characters: grow the shape a little, with room around it."""
    bigger = Image.new('L', (mask.width + 2 * w, mask.height + 2 * w), 0); bigger.paste(mask, (w, w))
    return round_grow(bigger, w)


def pack(images, width):
    """Shelf packing, tallest first. Returns {id: (x, y)} and the used height, or None if it does not fit."""
    order = sorted(images, key=lambda i: -images[i].height)
    x = y = shelf = 0
    spots = {}
    for i in order:
        im = images[i]
        if im.width + 2 > width: return None, 0
        if x + im.width + 2 > width: x, y, shelf = 0, y + shelf + 2, 0
        spots[i] = (x + 1, y + 1)
        x += im.width + 2; shelf = max(shelf, im.height)
    return spots, y + shelf + 2


def build(fnt_text, page, style, fonts):
    """A new (page image, .fnt text, summary) for a game font in a style."""
    with LOCK:
        r = Renderer(fnt_text, page, style, fonts)
        ids = sorted(r.src['chars'])
        glyphs, metrics = {}, {}
        for cid in ids:
            img, xoff, yoff, adv = r.glyph(cid)
            metrics[cid] = (xoff, yoff, adv)
            if img is not None: glyphs[cid] = img
        kerns = r.kernings(set(ids))
    for size in (1024, 2048, 4096):
        spots, used = pack(glyphs, size)
        if spots is not None and used <= size: break
    else: raise ValueError('These letters do not fit in one font page; lower the size, glow or outlines.')
    height = 1 << max(8, (used - 1).bit_length())   # power-of-two page, as the game's own
    sheet = Image.new('RGBA', (size, min(size, height)), (0, 0, 0, 0))
    for cid, img in glyphs.items(): sheet.paste(img, spots[cid])
    src = r.src
    info = dict(src['info'], face=r.face, padding=f'{r.pad},{r.pad},{r.pad},{r.pad}', spacing='2,2', outline='0')
    common = dict(src['common'], lineHeight=str(r.line), base=str(r.base), scaleW=str(sheet.width), scaleH=str(sheet.height), pages='1', packed='0',
                  alphaChnl='0', redChnl='0', greenChnl='0', blueChnl='0')
    line = lambda tag, d: '<' + tag + ' ' + ' '.join(f'{k}="{v}"' for k, v in d.items()) + '/>'
    rows = []
    for cid in ids:
        xoff, yoff, adv = metrics[cid]
        if cid in glyphs: x, y = spots[cid]; w, h = glyphs[cid].size
        else: x = y = w = h = 0
        rows.append(f'    <char id="{cid}" x="{x}" y="{y}" width="{w}" height="{h}" xoffset="{xoff}" yoffset="{yoff}" xadvance="{adv}" page="0" chnl="15" />')
    text = '\n'.join(['<?xml version="1.0"?>', '<font>', '  ' + line('info', info), '  ' + line('common', common), '  <pages>',
                      f'    <page id="0" file="{src["pages"][0]["file"]}" />', '  </pages>', f'  <chars count="{len(ids)}">', *rows, '  </chars>',
                      f'  <kernings count="{len(kerns)}">', *[f'    <kerning first="{a}" second="{b}" amount="{k}" />' for a, b, k in kerns], '  </kernings>', '</font>', ''])
    return sheet, text, dict(face=r.face, characters=len(ids), drawn=len(glyphs), page=list(sheet.size), padding=r.pad, missing=r.missing)


_RENDERERS = OrderedDict()


def renderer(fnt_text, page, style, fonts):
    """A renderer for previews, kept with the characters it drew: typing more example text, or going back to a look seen a
    moment ago, only draws what is new. Callers hold LOCK."""
    key = (len(fnt_text), hash(fnt_text), id(page), style_key(style), id(fonts))
    r = _RENDERERS.get(key)
    if r is None:
        r = _RENDERERS[key] = Renderer(fnt_text, page, style, fonts, keep=True)
        while len(_RENDERERS) > 16: _RENDERERS.popitem(last=False)
    else: _RENDERERS.move_to_end(key)
    return r


def preview(fnt_text, page, style, fonts, text, backgrounds=('#0d2829', '#c0392b', '#e9edf2')):
    """The example text set the way the game sets it (offsets, advances, kerning), once per background."""
    text = (text or 'Player').strip()[:40] or 'Player'
    with LOCK:
        r = renderer(fnt_text, page, style, fonts)
        ids = sorted({ord(c) for c in text if ord(c) in r.src['chars']})
        glyphs = {cid: r.glyph(cid) for cid in ids}
        kern = {(a, b): k for a, b, k in r.kernings(set(ids))}
    pen, placed, prev = 0, [], None
    for ch in text:
        cid = ord(ch)
        if cid not in glyphs: continue
        if prev is not None: pen += kern.get((prev, cid), 0)
        img, xoff, yoff, adv = glyphs[cid]
        if img is not None: placed.append((img, pen + xoff, yoff))
        pen += adv; prev = cid
    if not placed: return Image.new('RGBA', (320, 120), (0, 0, 0, 0))
    left = min(x for _, x, _ in placed); right = max(x + im.width for im, x, _ in placed)
    top = min(y for _, _, y in placed); bottom = max(y + im.height for im, _, y in placed)
    margin = 24
    w, h = right - left + 2 * margin, bottom - top + 2 * margin
    out = Image.new('RGBA', (w, h * len(backgrounds)), (0, 0, 0, 0))
    for i, bg in enumerate(backgrounds):
        band = Image.new('RGBA', (w, h), bg)
        for im, x, y in placed: band.alpha_composite(im, (x - left + margin, y - top + margin))
        out.paste(band, (0, i * h))
    return out
