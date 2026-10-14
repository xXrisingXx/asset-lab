"""Restyle the game's bitmap fonts: every character re-rendered with any installed font and effects.

Mitosis draws its text from BMFont pairs: a page image with every character, and an XML .fnt that says where each one
is and how far to move after it. A style here (typeface, size, fill, outlines, glow, shadow and more) is rendered
character by character at the game's own resolution, packed into a new page, and written with a new .fnt in the same
format, so the game reads it exactly like its own font. The font's nominal size stays the game's, so the game scales
the text the way it always did; the "size" setting makes the letters themselves bigger or smaller.
"""
import json
import math
import os
import re
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
DEFAULT = dict(font='', size=1.0, spacing=0.0, bold=0.0, italic=0.0, caps=False,
               fill='#ffffff', fill2='', outline='', outline_width=0.0, outline2='', outline2_width=0.0,
               glow='', glow_size=0.0, glow_strength=1.0, shadow='', shadow_x=0.0, shadow_y=0.0, shadow_blur=0.0, shadow_opacity=0.6)
LIMITS = dict(size=(0.5, 2.0), spacing=(-20, 40), bold=(0, 8), italic=(-0.4, 0.4), outline_width=(0, 16), outline2_width=(0, 16),
              glow_size=(0, 32), glow_strength=(0, 4), shadow_x=(-24, 24), shadow_y=(-24, 24), shadow_blur=(0, 24), shadow_opacity=(0, 1))
COLOR = re.compile(r'#[0-9a-fA-F]{6}')
PRESETS = {
    'Game font': {},
    'Black with a white glow': dict(font='Bahnschrift|Bold Condensed', fill='#000000', outline='#ffffff', outline_width=2, glow='#ffffff', glow_size=8, glow_strength=1.4, caps=True),
    'Gold': dict(fill='#fff3b0', fill2='#c98a12', outline='#4a2a00', outline_width=3, shadow='#000000', shadow_y=3, shadow_blur=3),
    'Neon pink': dict(fill='#ffffff', outline='#ff2ec4', outline_width=3, glow='#ff2ec4', glow_size=12, glow_strength=1.8),
    'Toxic': dict(fill='#d6ff3d', fill2='#3fae1a', outline='#0b2a05', outline_width=3, glow='#7dff2e', glow_size=8, glow_strength=1.2),
    'Comic': dict(font='Comic Sans MS|Bold', fill='#ffe14d', outline='#000000', outline_width=4, shadow='#000000', shadow_x=3, shadow_y=4, shadow_blur=0, shadow_opacity=1.0),
    'Ice': dict(fill='#ffffff', fill2='#7fd8ff', outline='#0b3a5c', outline_width=3, glow='#bfefff', glow_size=10, glow_strength=1.2),
    'Spooky': dict(font='Chiller|Regular', size=1.15, fill='#ff8a1a', fill2='#b31b00', outline='#1a0000', outline_width=3, glow='#ff5a00', glow_size=10, glow_strength=1.3, caps=True),
}


# ----- the game's .fnt files ------------------------------------------------------------------------------------
def attrs(tag):
    return dict(re.findall(r'(\w+)="([^"]*)"', tag))


def parse_fnt(text):
    info, common = attrs(re.search(r'<info[^>]*>', text).group(0)), attrs(re.search(r'<common[^>]*>', text).group(0))
    pages = [attrs(t) for t in re.findall(r'<page [^>]*>', text)]
    chars = {int(a['id']): {k: (int(v) if re.fullmatch(r'-?\d+', v) else v) for k, v in a.items()} for a in (attrs(t) for t in re.findall(r'<char [^>]*>', text))}
    kernings = [(int(a['first']), int(a['second']), int(a['amount'])) for a in (attrs(t) for t in re.findall(r'<kerning [^>]*>', text))]
    return dict(info=info, common=common, pages=pages, chars=chars, kernings=kernings)


def game_fonts(game):
    """The game's fonts that can be restyled: {id: dict(label, note, fnt, page, face, chars)}."""
    out = {}
    for stem, (label, note) in GAME_FONTS.items():
        fnt = Path(game) / 'assets' / f'{stem}.fnt'
        if not fnt.is_file(): continue
        parsed = parse_fnt(fnt.read_text(encoding='utf-8', errors='replace'))
        if len(parsed['pages']) != 1: continue   # the game's fonts have one page; anything else is left alone
        out[stem] = dict(id=stem, label=label, note=note, fnt=f'assets/{stem}.fnt', page='assets/' + parsed['pages'][0]['file'],
                         face=parsed['info'].get('face', ''), chars=len(parsed['chars']))
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


def load_font(entry, px):
    path, _, instance = entry['id'].partition('#')
    font = ImageFont.truetype(path, max(4, int(round(px))))
    if instance:
        try: font.set_variation_by_name(instance)
        except (OSError, ValueError): pass
    return font


# ----- a style ------------------------------------------------------------------------------------------------------------
def clean_style(raw, fonts=None):
    s = dict(DEFAULT)
    raw = raw if isinstance(raw, dict) else {}
    for k, (lo, hi) in LIMITS.items():
        v = raw.get(k, DEFAULT[k])
        s[k] = float(min(hi, max(lo, v))) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else float(DEFAULT[k])
    for k in ('fill', 'fill2', 'outline', 'outline2', 'glow', 'shadow'):
        v = raw.get(k, DEFAULT[k])
        s[k] = v.lower() if isinstance(v, str) and COLOR.fullmatch(v) else ('' if k != 'fill' else DEFAULT['fill'])
    s['caps'] = raw.get('caps') is True
    font = raw.get('font') if isinstance(raw.get('font'), str) else ''
    s['font'] = (find_font(fonts, font) or {}).get('id', '') if fonts is not None and font else font[:300]
    return s


def rgb(color): return np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def pad_for(s):
    """Room around each character for its outlines, glow and shadow, so no effect is ever cut off square."""
    ring = s['bold'] + s['outline_width'] + s['outline2_width']
    return int(math.ceil(max(ring + s['glow_size'] * 1.6, ring + max(abs(s['shadow_x']), abs(s['shadow_y'])) + s['shadow_blur'] * 2.2, ring) + 3))


class Renderer:
    """Renders characters for one game font in one style."""

    def __init__(self, fnt_text, page, style, fonts):
        self.src = parse_fnt(fnt_text)
        self.page = page.convert('RGBA')
        self.s = style
        self.em = abs(int(self.src['info'].get('size', '-64'))) * style['size']
        entry = find_font(fonts, style['font']) if style['font'] else None
        self.face = entry['family'] if entry else self.src['info'].get('face', '')
        self.font = load_font(entry, self.em) if entry else None
        self.fallbacks = []
        for name in ('Segoe UI|Black', 'Segoe UI|Bold', 'Arial|Bold', 'Segoe UI Symbol|Regular'):
            e = find_font(fonts, name)
            if e:
                try: self.fallbacks.append(load_font(e, self.em))
                except OSError: pass
        self.base = int(round(int(self.src['common']['base']) * style['size']))
        self.line = int(round(int(self.src['common']['lineHeight']) * style['size']))
        self.pad = pad_for(style)
        self.missing, self._notdef = 0, {}

    def has(self, font, text):
        """Whether a typeface really has this character (a missing one draws as its 'not defined' box)."""
        if text.isspace(): return True
        try:
            if id(font) not in self._notdef:
                m = font.getmask('\uffff'); self._notdef[id(font)] = (m.size, bytes(m))
            mask = font.getmask(text)
        except (OSError, ValueError): return False
        return (mask.size, bytes(mask)) != self._notdef[id(font)]

    def shape(self, cid):
        """(mask image, origin x, origin y on the mask = the pen at the baseline, xadvance) for one character."""
        text = chr(cid)
        if self.s['caps'] and text.islower() and len(text.upper()) == 1: text = text.upper()
        if self.font is not None:
            fonts = [self.font] + self.fallbacks
            font = next((f for f in fonts if self.has(f, text)), None)
            if font is not None:
                bold = int(round(self.s['bold']))
                l, t, r, b = font.getbbox(text, anchor='ls', stroke_width=bold)
                advance = font.getlength(text) + bold
                if r <= l or b <= t: return None, 0, 0, advance
                mask = Image.new('L', (r - l, b - t), 0)
                ImageDraw.Draw(mask).text((-l, -t), text, font=font, fill=255, anchor='ls', stroke_width=bold, stroke_fill=255)
                return mask, -l, -t, advance
            self.missing += 1
        # The game's own character, scaled: the shape is the game font's, every effect is applied to it.
        c = self.src['chars'].get(cid)
        if not c or c['width'] <= 0 or c['height'] <= 0: return None, 0, 0, (c or {}).get('xadvance', 0) * self.s['size']
        crop = self.page.crop((c['x'], c['y'], c['x'] + c['width'], c['y'] + c['height'])).getchannel('A')
        k = self.s['size']
        if k != 1: crop = crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))), Image.Resampling.LANCZOS)
        bold = int(round(self.s['bold']))
        if bold: crop = thicken(crop, bold)
        # Pen at the baseline: the original box sits xoffset right of the pen and yoffset below the line top.
        return crop, -c['xoffset'] * k + bold, (int(self.src['common']['base']) - c['yoffset']) * k + bold, c['xadvance'] * k + bold

    def glyph(self, cid):
        """(RGBA image or None, xoffset, yoffset, xadvance) in the new font's terms."""
        mask, ox, oy, advance = self.shape(cid)
        advance = int(round(advance + self.s['spacing']))
        if mask is None: return None, 0, 0, advance
        s, p = self.s, self.pad
        slant = s['italic']
        extra_l, extra_r = int(math.ceil(max(0, slant) * (mask.height - oy) + max(0, -slant) * oy)), int(math.ceil(max(0, slant) * oy + max(0, -slant) * (mask.height - oy)))
        W, H = mask.width + 2 * p + extra_l + extra_r, mask.height + 2 * p
        body = Image.new('L', (W, H), 0); body.paste(mask, (p + extra_l, p))
        if slant:
            by = p + oy   # shear around the baseline: letters lean, the baseline stays put
            body = body.transform((W, H), Image.Transform.AFFINE, (1, slant, -slant * by, 0, 1, 0), resample=Image.Resampling.BICUBIC)
        a_body = np.asarray(body, np.float32) / 255
        layers = []
        o1 = round_grow(body, s['outline_width']) if s['outline'] and s['outline_width'] else body
        o2 = round_grow(o1, s['outline2_width']) if s['outline2'] and s['outline2_width'] else o1
        outer = np.asarray(o2, np.float32) / 255
        if s['shadow'] and (s['shadow_x'] or s['shadow_y'] or s['shadow_blur']):
            moved = Image.new('L', (W, H), 0); moved.paste(o2, (int(round(s['shadow_x'])), int(round(s['shadow_y']))))
            if s['shadow_blur']: moved = moved.filter(ImageFilter.GaussianBlur(s['shadow_blur']))
            layers.append((rgb(s['shadow']), np.asarray(moved, np.float32) / 255 * s['shadow_opacity']))
        if s['glow'] and s['glow_size'] and s['glow_strength']:
            g = o2.filter(ImageFilter.GaussianBlur(s['glow_size'] * 0.5))
            g2 = o2.filter(ImageFilter.GaussianBlur(s['glow_size'] * 1.1))
            halo = np.clip((np.asarray(g, np.float32) * 0.9 + np.asarray(g2, np.float32) * 0.5) / 255 * s['glow_strength'], 0, 1)
            layers.append((rgb(s['glow']), halo))
        if o2 is not o1: layers.append((rgb(s['outline2']), outer))
        if o1 is not body: layers.append((rgb(s['outline']), np.asarray(o1, np.float32) / 255))
        if s['fill2']:
            # A fill gradient runs over the letter height (cap height to baseline), the same for every character.
            ys = (np.arange(H, dtype=np.float32) - (p + oy - self.em * 0.75)) / max(1.0, self.em * 0.75)
            t = np.clip(ys, 0, 1)[:, None, None]
            fill = rgb(s['fill'])[None, None, :] * (1 - t) + rgb(s['fill2'])[None, None, :] * t
            fill = np.broadcast_to(fill, (H, W, 3))
        else: fill = rgb(s['fill'])
        layers.append((fill, a_body))
        color, alpha = np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32)
        for c, a in layers:   # back to front, "over"
            color = c * a[..., None] + color * (1 - a[..., None])
            alpha = a + alpha * (1 - a)
        out = np.dstack([np.clip(color / np.maximum(alpha, 1e-6)[..., None], 0, 1) * 255, alpha * 255])
        img = Image.fromarray((out + 0.5).astype(np.uint8), 'RGBA')
        box = img.getchannel('A').point(lambda v: 255 if v > 1 else 0).getbbox()
        if not box: return None, 0, 0, advance
        img = img.crop(box)
        xoff = box[0] - (p + extra_l + ox)
        yoff = self.base - (p + oy) + box[1]
        return img, int(xoff), int(yoff), advance

    def kernings(self, ids):
        if self.font is None: return [(a, b, int(round(k * self.s['size']))) for a, b, k in self.src['kernings'] if a in ids and b in ids]
        out, ascii_ids = [], [i for i in ids if 33 <= i < 127]
        caps = lambda c: c.upper() if self.s['caps'] and c.islower() else c
        width = {i: self.font.getlength(caps(chr(i))) for i in ascii_ids}
        for a in ascii_ids:
            for b in ascii_ids:
                k = round(self.font.getlength(caps(chr(a)) + caps(chr(b))) - width[a] - width[b])
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
    r = Renderer(fnt_text, page, style, fonts)
    ids = sorted(r.src['chars'])
    glyphs, metrics = {}, {}
    for cid in ids:
        img, xoff, yoff, adv = r.glyph(cid)
        metrics[cid] = (xoff, yoff, adv)
        if img is not None: glyphs[cid] = img
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
    kerns = r.kernings(set(ids))
    text = '\n'.join(['<?xml version="1.0"?>', '<font>', '  ' + line('info', info), '  ' + line('common', common), '  <pages>',
                      f'    <page id="0" file="{src["pages"][0]["file"]}" />', '  </pages>', f'  <chars count="{len(ids)}">', *rows, '  </chars>',
                      f'  <kernings count="{len(kerns)}">', *[f'    <kerning first="{a}" second="{b}" amount="{k}" />' for a, b, k in kerns], '  </kernings>', '</font>', ''])
    return sheet, text, dict(face=r.face, characters=len(ids), drawn=len(glyphs), page=list(sheet.size), padding=r.pad, missing=r.missing)


def preview(fnt_text, page, style, fonts, text, backgrounds=('#0d2829', '#c0392b', '#e9edf2')):
    """The example text set the way the game sets it (offsets, advances, kerning), once per background."""
    text = (text or 'Player').strip()[:40] or 'Player'
    r = Renderer(fnt_text, page, style, fonts)
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
