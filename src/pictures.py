"""Pictures players bring to Theme AI, and the ready-made presets: recolored with one request ("make this dark blue") and
put on the game art they belong to.

A recolor finds the picture's main color and moves that color family to the new one in OKLab, a space where lightness
is what the eye sees: the main color lands exactly on the new one, lighter parts keep their place between it and white
and darker parts between black and it, so highlights, glows and shading survive. Colors far from the main one (a
second accent, black outlines) are left as they are.
"""
import base64
import hashlib
import io
import json
import os
import re
import zlib
from pathlib import Path
import numpy as np
from PIL import Image

import button_sets

PRESETS = Path(__file__).resolve().parent / 'presets'
PICTURE_ID = re.compile(r'(?:[0-9a-f]{20}|preset:[a-z0-9-]{1,40})')
MAX_BYTES, MAX_SIDE = 16 * 1024 * 1024, 4096
FAMILY = np.pi / 3   # hues within about 60 degrees of the main color change with it


# ----- OKLab --------------------------------------------------------------------------------------------------------
_M1 = np.array([[0.4122214708, 0.2119034982, 0.0883024619], [0.5363325363, 0.6806995451, 0.2817188376], [0.0514459929, 0.1073970566, 0.6299787005]], np.float32)
_M2 = np.array([[0.2104542553, 1.9779984951, 0.0259040371], [0.7936177850, -2.4285922050, 0.7827717662], [-0.0040720468, 0.4505937099, -0.8086757660]], np.float32)
_I2 = np.array([[1, 1, 1], [0.3963377774, -0.1055613458, -0.0894841775], [0.2158037573, -0.0638541728, -1.2914855480]], np.float32)
_I1 = np.array([[4.0767416621, -1.2684380046, -0.0041960863], [-3.3077115913, 2.6097574011, -0.7034186147], [0.2309699292, -0.3413193965, 1.7076127010]], np.float32)


def oklab(rgb):
    """sRGB 0..1 (..., 3) -> OKLab (..., 3)."""
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return np.cbrt(lin @ _M1) @ _M2


def srgb(lab):
    lin = np.clip(((lab @ _I2) ** 3) @ _I1, 0, 1)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


def hex_lab(color): return oklab(np.array([[int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]], np.float32))[0]


def lab_hex(lab): return '#' + ''.join(f'{int(round(float(c) * 255)):02x}' for c in srgb(np.array([lab], np.float32))[0])


def main_color(image):
    """(lightness, chroma, hue in radians) of the picture's main color, or None when it has no color (gray art)."""
    a = np.asarray(image.convert('RGBA'), np.float32) / 255
    lab = oklab(a[..., :3])
    L, C, H = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2]), np.arctan2(lab[..., 2], lab[..., 1])
    w = a[..., 3] * C * (C > 0.03)
    if w.sum() < 1e-3: return None
    hue = float(np.angle((w * np.exp(1j * H)).sum()))
    core = (np.abs(np.angle(np.exp(1j * (H - hue)))) < 0.6) & (w > 0)
    return float((L[core] * w[core]).sum() / w[core].sum()), float((C[core] * w[core]).sum() / w[core].sum()), hue


def recolor(image, color):
    """The picture with its main color family moved to color ('#rrggbb'), light and shade kept. Gray art is tinted."""
    a = np.asarray(image.convert('RGBA'), np.float32) / 255
    lab = oklab(a[..., :3])
    L, C, H = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2]), np.arctan2(lab[..., 2], lab[..., 1])
    main = main_color(image)
    t = hex_lab(color)
    tL, tC, tH = float(t[0]), float(np.hypot(t[1], t[2])), float(np.arctan2(t[2], t[1]))
    if main is None:   # gray art: tinted, its lightness kept
        dL, dC, dH = float((L * a[..., 3]).sum() / max(1e-3, a[..., 3].sum())), 0.0, tH
        m = np.ones_like(L)
    else:
        dL, dC, dH = main
        off = np.abs(np.angle(np.exp(1j * (H - dH))))
        m = np.maximum(np.clip(1 - (off - FAMILY) / 0.5, 0, 1), np.clip(1 - C / 0.04, 0, 1))   # its family, and the grays with it
    H2 = H + np.angle(np.exp(1j * (tH - dH))) * m if main is not None else np.where(m > 0, tH, H)
    up = tL + (L - dL) * (1 - tL) / max(1e-3, 1 - dL)
    down = L * tL / max(1e-3, dL)
    L2 = L + (np.where(L >= dL, up, down) - L) * m
    if main is None and dL < 0.35:
        # Dark gray art (black with stars): its light and dark stay where they are, and only what is light enough takes
        # the color. Lifting or coloring the near-black would turn the picture's faint compression blocks into squares.
        L2 = L
    lit = np.clip((L2 - 0.06) / 0.12, 0, 1)   # near-black stays neutral black
    if tC > 0.02 and dC > 0.02: C2 = C * (1 + (tC / max(dC, 0.02) - 1) * m)
    elif tC > 0.02: C2 = C * (1 - m) + tC * m * np.clip(L2 / max(tL, 0.2), 0, 1) * lit
    else: C2 = C * (1 - m)
    a[..., :3] = srgb(np.stack([L2, C2 * np.cos(H2), C2 * np.sin(H2)], -1))
    return Image.fromarray(np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8), 'RGBA')


def wanted_color(text, image):
    """The color a request asks for ('#rrggbb'), or ''. A named color ("dark blue", "#ff8800", "gold"), or a change of the
    picture's own: "darker", "lighter", "black and white"."""
    import theme   # here: theme imports design, which this lab module must not pull in at start
    low = str(text or '').lower()
    main = main_color(image)
    if main and re.search(r'\b(?:black and white|gr[ae]y ?scale|monochrome|no colou?rs?|desaturated?)\b', low): return lab_hex([main[0], 0.0, 0.0])
    found = re.search(theme.TEXT_COLOR, low)
    if found: return theme.text_color(found.group(0))
    if main is None: return ''
    L, C, H = main
    if re.search(r'\b(?:darker|darken|dimmer|deeper)\b', low): return lab_hex([L * 0.62, C * np.cos(H), C * np.sin(H)])
    if re.search(r'\b(?:lighter|brighter|lighten|paler)\b', low): return lab_hex([L + (1 - L) * 0.45, C * 0.8 * np.cos(H), C * 0.8 * np.sin(H)])
    return ''


# ----- pictures and presets -----------------------------------------------------------------------------------------
# Where a background goes: every menu background (one picture for each of the game's themes), or the arena floor in
# light and dark mode. A background preset is put on one of these sets, tiled to each frame.
# A background goes everywhere at once (the default), or only on the main menu, or only in game (both modes or one).
# In game, the light-mode floors are the worldbg*_w.png files (the whitest; the game darkens the others for dark mode).
BACKGROUND_SETS = ['set:everywhere', 'set:menu', 'set:arena', 'set:arena-light', 'set:arena-dark']
SETS = {'set:everywhere': 'Everywhere: main menu and in game (light and dark mode)', 'set:menu': 'Main menu',
        'set:arena': 'In game, light and dark mode', 'set:arena-light': 'In game, light mode only', 'set:arena-dark': 'In game, dark mode only',
        'set:viruses': 'Both viruses (light and dark mode)',
        'set:virus-light': 'The virus in light mode only', 'set:virus-dark': 'The virus in dark mode only',
        'set:food:all': 'All foods (every size, at once)',
        'set:food:0': 'The smallest food (least mass)', 'set:food:1': 'The middle food', 'set:food:2': 'The biggest food (most mass)',
        **{k: name for k, (name, _) in button_sets.SETS.items()}}
GROUPS = {'background': 'Backgrounds', 'virus': 'Viruses', 'food': 'Food', 'menu': 'Menu buttons', 'ingame': 'In-game buttons', 'logo': 'Title', 'font': 'Fonts',
          'hide': 'Menu buttons'}   # the Presets window's top level
# The title (the MITOSIS logo, every copy of it) and the menu clutter a Clean up preset hides (its art made fully
# transparent: Mitosis.exe is never touched, and the empty spot can still be clicked).
SETS['set:logo'] = 'The MITOSIS title on the main menu'
SETS['set:cleanup'] = 'Selected buttons and panels'
SETS['set:control'] = 'Every copy of this button'
# A virus goes on both (the default), or on the light-mode one (virus.png) or the dark-mode one (light_virus.png) alone.
VIRUS_SETS = ['set:viruses', 'set:virus-light', 'set:virus-dark']
VIRUS_FILES = {'set:virus-light': 'virus.png', 'set:virus-dark': 'light_virus.png'}
BUTTON_KINDS = {'menu': ['set:menu-buttons'], 'ingame': ['set:ingame-buttons', 'set:ingame-phone', 'set:ingame-pc']}


_LISTED = {'stamp': None, 'raw': {}, 'list': []}   # presets.json read once (again only when it changes): it is long


def listed():
    """presets.json as it is on disk: (its entries by id, the presets), read again only when the file changed."""
    path = PRESETS / 'presets.json'
    try: stamp = path.stat().st_mtime_ns
    except OSError: return {}, []
    if _LISTED['stamp'] != stamp:
        try: data = json.loads(path.read_text(encoding='utf-8')).get('presets') or []
        except (OSError, ValueError): data = []
        _LISTED.update(stamp=stamp, raw={p['id']: p for p in data if isinstance(p, dict) and 'id' in p}, list=_presets(data))
    return _LISTED['raw'], _LISTED['list']


def version():
    """Changes whenever the presets do: the Presets window asks for pictures with it, so the browser may keep them."""
    listed()
    return f"{_LISTED['stamp'] or 0:x}-{button_sets.VERSION}"


def presets():
    """The ready-made pictures: [dict(id, name, category, kind, for)] (for: the game art each is made for; a background's
    is its sets, 'menu' and 'arena')."""
    return [dict(p) for p in listed()[1]]


def _presets(data):
    return [dict(id='preset:' + p['id'], name=p['name'], category=p.get('category') or 'Other', kind=p.get('kind') or 'sprite',
                 group=p.get('group') or GROUPS.get(p.get('kind'), 'Other'), **{'for': list(p.get('for') or [])},
                 **({'look': dict(p.get('look') or {}), 'base': str(p.get('base') or ''), 'redrawn': str(p.get('redrawn') or '')} if p.get('kind') in BUTTON_KINDS else {}),
                 **({'hide': [str(n) for n in p.get('hide') or []]} if p.get('kind') == 'hide' else {}),
                 **({'description': str(p['description'])} if p.get('description') else {}),
                 **({'style': dict(p.get('style') or {})} if p.get('kind') == 'font' else {})) for p in data
            if isinstance(p, dict) and re.fullmatch(r'[a-z0-9-]{1,40}', str(p.get('id', ''))) and (PRESETS / str(p.get('file', ''))).is_file()]


def chosen(q):
    """The presets one export asks for (one by id, or a group or one of its categories) and a file name for them."""
    listed = presets()
    ids = [q['id']] if q.get('id') else [p['id'] for p in listed if p['group'] == q.get('group') and (not q.get('category') or p['category'] == q.get('category'))]
    if not ids or (q.get('id') and not any(p['id'] == q['id'] for p in listed)): raise ValueError('No such presets.')
    name = next(p['name'] for p in listed if p['id'] == ids[0]) if q.get('id') else ' '.join(filter(None, [q.get('group'), q.get('category')]))
    return ids, re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-') or 'presets'


def downloads():
    """The player's Downloads folder (where Windows keeps it, even when moved), else the one in their home folder."""
    if os.name == 'nt':
        try:
            import ctypes, uuid
            from ctypes import wintypes
            guid = uuid.UUID('374DE290-123F-4565-9164-39C4925E467B').bytes_le
            path = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.c_char_p(guid), 0, None, ctypes.byref(path)) == 0:
                found = Path(path.value); ctypes.windll.ole32.CoTaskMemFree(path)
                if found.is_dir(): return found
        except (OSError, AttributeError, ValueError): pass
    return Path.home() / 'Downloads'


def png_bytes(image):
    out = io.BytesIO(); image.save(out, format='PNG'); return out.getvalue()


class Pictures:
    """Pictures pasted into Theme AI (kept in workspace/pictures by content) and the presets shipped with the lab."""

    def __init__(self, lab):
        self.lab, self.folder = lab, lab.root / 'pictures'
        self.sets = {}   # 'menu' / 'arena' -> a function giving those rows (the lab server hands in the Designer's)
        self._sheets = {}   # a button set's preview, by (id, color, small): drawn from the game's own buttons
        self._buttons = {}  # kind -> the game's own buttons (name -> picture)
        self.previews = lab.root / 'preset-previews'   # drawn previews kept between runs: the window opens at once

    def save(self, data):
        """A pasted or dropped picture (bytes or a data URL) -> its id. The same picture twice is kept once."""
        if isinstance(data, str): data = base64.b64decode(data.split(',', 1)[-1], validate=False)
        if not data or len(data) > MAX_BYTES: raise ValueError('Use a picture under 16 MB.')
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_SIDE * MAX_SIDE: raise ValueError('Use a picture of at most 4096 × 4096.')
                image = image.convert('RGBA')
        except (OSError, Image.DecompressionBombError) as exc: raise ValueError('That file is not a picture the lab can read.') from exc
        if not image.getchannel('A').getbbox(): raise ValueError('The picture is completely transparent.')
        out = io.BytesIO(); image.save(out, format='PNG')
        pid = hashlib.sha256(out.getvalue()).hexdigest()[:20]
        path = self.folder / f'{pid}.png'
        if not path.is_file():
            self.folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(out.getvalue())
        return pid

    def image(self, pid):
        if not PICTURE_ID.fullmatch(str(pid or '')): raise ValueError('Unknown picture.')
        if pid.startswith('preset:'):
            entry = listed()[0].get(pid[7:])
            path = PRESETS / entry['file'] if entry else None
        else: path = self.folder / f'{pid}.png'
        if not path or not path.is_file(): raise ValueError('That picture is no longer here. Paste it again.')
        with Image.open(path) as im: return im.convert('RGBA')

    def render(self, pid, color='', small=False):
        """The picture, recolored when a color is given. A button set: a preview of the game's buttons in its look."""
        color = str(color or '').lower() if re.fullmatch(r'#[0-9a-f]{6}', str(color or '').lower()) else ''
        preset = self.button_set(pid)
        if preset:
            key = (pid, color, bool(small))
            if key not in self._sheets:
                with self.lab.lock: buttons = self.buttons(preset['kind'])
                stamp = zlib.crc32(json.dumps([listed()[0].get(pid[7:]), sorted((n, b.size) for n, b in buttons.items()), button_sets.VERSION]).encode())
                kept = self.previews / f"{pid[7:]}-{color[1:] or 'own'}-{'small' if small else 'big'}-{stamp:08x}.png"
                if kept.is_file():
                    with Image.open(kept) as im: sheet = im.convert('RGBA')
                else:
                    sheet = button_sets.sheet(buttons, preset['look'], self.material(pid, color), preset['kind'], small=small,
                                              base=self.base(pid, color), drawn=self.drawn(pid, color))
                    try:
                        self.previews.mkdir(parents=True, exist_ok=True)
                        sheet.save(kept, format='PNG')
                    except OSError: pass
                self._sheets[key] = sheet
                while len(self._sheets) > 400: self._sheets.pop(next(iter(self._sheets)))
            return self._sheets[key].copy()

        hide = next((p for p in presets() if p['id'] == pid and p['kind'] == 'hide'), None)
        if hide: return self.hide_preview(pid, small)
        image = self.image(pid)
        return recolor(image, color) if color else image

    def prewarm(self, stop=None):
        """Draw every button set's small preview ahead (in the background when the lab starts), so switching
        categories in the Presets window never waits for them. stop: an Event that ends it (the lab closing)."""
        import time
        for p in presets():
            if stop is not None and stop.is_set(): return
            if p['kind'] not in BUTTON_KINDS: continue
            started = time.monotonic()
            try: self.render(p['id'], small=True)
            except Exception: pass   # a game without these buttons, a preset still being drawn: shown when asked
            # Paced: at most about a third of one processor core, so a slow PC stays quick while they are drawn.
            if stop is not None and stop.wait(max(0.02, 2 * (time.monotonic() - started))): return

    def hide_preview(self, pid, small=False):
        """What a Clean up preset takes away: the game's own pictures of it, faded and crossed out."""
        from PIL import ImageDraw
        with self.lab.lock:
            pics = []
            for row in self.hide_rows(pid):
                if row['name'] in {n for n, _ in pics}: continue
                try: pics.append((row['name'], self.lab.catalog.image(row['key']).convert('RGBA')))
                except (OSError, ValueError, KeyError): pass
        if not pics: return self.image(pid)
        h = 120 if small else 200
        tiles = []
        for _, im in pics:
            im = im.copy(); im.thumbnail((h * 3, h), Image.Resampling.LANCZOS)
            im.putalpha(im.getchannel('A').point(lambda v: v * 45 // 100))
            tiles.append(im)
        out = Image.new('RGBA', (sum(t.width for t in tiles) + 24 * (len(tiles) + 1), h + 48), (0, 0, 0, 0))
        x = 24
        d = ImageDraw.Draw(out)
        for t in tiles:
            y = (out.height - t.height) // 2
            out.alpha_composite(t, (x, y))
            w = max(6, h // 14)
            d.line((x, y, x + t.width, y + t.height), fill=(255, 70, 90, 255), width=w)
            d.line((x, y + t.height, x + t.width, y), fill=(255, 70, 90, 255), width=w)
            x += t.width + 24
        return out

    def button_set(self, pid):
        return next((p for p in presets() if p['id'] == pid and p['kind'] in BUTTON_KINDS), None)

    def material(self, pid, color=''):
        """A button set's material (its texture), recolored when a color is given. A classic set's: the middle of its
        classic virus (the white glow out to its color)."""
        texture = self.image(pid)
        preset = self.button_set(pid) or {}
        if preset.get('look', {}).get('classic'): texture = button_sets.classic_texture(texture)
        if preset.get('redrawn') and (PRESETS / preset['redrawn'] / 'icon-star.webp').is_file():
            # a fully redrawn set's material: the middle of its own star (solid, in the set's look)
            with Image.open(PRESETS / preset['redrawn'] / 'icon-star.webp') as im: star = im.convert('RGB')
            w, h = star.size
            texture = star.crop((w * 2 // 5, h * 2 // 5, w * 3 // 5, h * 3 // 5))
        return recolor(texture, color) if color else texture

    def drawn(self, pid, color=''):
        """A fully redrawn set's own drawing of each button (name -> picture), recolored when a color is given; None else."""
        preset = self.button_set(pid)
        folder = PRESETS / preset['redrawn'] if preset and preset.get('redrawn') else None
        if not folder or not folder.is_dir(): return None
        out = {}
        for path in folder.glob('*.webp'):
            if path.stem in button_sets.KEEP_GAME_SYMBOL and not preset.get('look', {}).get('exact_symbols'): continue
            with Image.open(path) as im: art = im.convert('RGBA')
            out[path.stem] = recolor(art, color) if color else art
        return out

    def base(self, pid, color=''):
        """A button set's own drawn button (an exotic shape the symbols go on), recolored when a color is given; None for a
        set that restyles the game's own shapes."""
        preset = self.button_set(pid)
        path = PRESETS / preset['base'] if preset and preset.get('base') else None
        if not path or not path.is_file(): return None
        with Image.open(path) as im: art = im.convert('RGBA')
        return recolor(art, color) if color else art

    def logo_rows(self):
        """The MITOSIS title, every copy (the game draws one of them, depending on its version)."""
        return [r for r in self.lab.catalog.rows.values() if r['name'] == 'logo' and r['editable'] and r['kind'] in ('image', 'sprite')
                and not r.get('legacy') and not r.get('removed')]

    def hide_rows(self, pid):
        """Every active copy of the buttons or shared panels a Clean up preset hides."""
        names = set(next((p.get('hide') or [] for p in presets() if p['id'] == pid), []))
        return [r for r in self.lab.catalog.rows.values() if r['name'] in names and r['editable'] and r['kind'] in ('image', 'sprite')
                and not r.get('legacy') and not r.get('removed')]

    def button_rows(self, key):
        """The game's buttons a button set covers (every copy of each, so whichever the game draws gets the look)."""
        from lab_core import LOCKED
        names = set(button_sets.SETS[key][1]) if key in button_sets.SETS else set()
        return [r for r in self.lab.catalog.rows.values() if r['name'] in names and r['editable'] and r['kind'] in ('image', 'sprite')
                and not r.get('legacy') and not r.get('removed') and r['name'] not in LOCKED]

    def control_rows(self, pid):
        """A single-control preset replaces all active atlas copies of its named control."""
        names = set(next((p['for'] for p in presets() if p['id'] == pid and p['kind'] == 'control'), []))
        from lab_core import LOCKED
        return [r for r in self.lab.catalog.rows.values() if r['name'] in names and r['name'] not in LOCKED
                and r.get('editable') and r['kind'] == 'sprite' and not r.get('legacy') and not r.get('removed')
                and not r.get('server_only')]

    def buttons(self, kind):
        """name -> the game's own picture of each button in a set (the biggest copy), for the preview. Read once: the
        game's own pictures don't change while the lab runs."""
        if kind in self._buttons: return self._buttons[kind]
        out = self._buttons[kind] = {}
        for row in sorted(self.button_rows(BUTTON_KINDS[kind][0]), key=lambda r: -r['width'] * r['height']):
            if row['name'] not in out:
                try: out[row['name']] = self.lab.catalog.image(row['key']).convert('RGBA')
                except (OSError, ValueError, KeyError): pass
        return out

    def targets(self, pid, limit=6, only=None):
        """The game art the picture is most likely for, best first: the checked pictures it can go on, a preset's own,
        then the look-alikes (image search)."""
        from lab_core import LOCKED   # art the game puts back by itself (the red virus): never offered
        rows, out = self.lab.catalog.rows, []
        usable = lambda r: r and r['editable'] and r['kind'] in ('image', 'sprite') and r.get('group') != 'Fonts' and not r.get('server_only') \
            and not r.get('legacy') and r.get('primary', True) and not r.get('removed')
        out = [rows[k] for k in only or [] if usable(rows.get(k)) and rows[k]['name'] not in LOCKED]
        preset = next((p for p in presets() if p['id'] == pid), None)
        if preset and preset['kind'] == 'logo': return [dict(key='set:logo', name=SETS['set:logo'], file='', size=[])] if self.logo_rows() else []
        if preset and preset['kind'] == 'hide': return [dict(key='set:cleanup', name=SETS['set:cleanup'], file='', size=[])] if self.hide_rows(pid) else []
        if preset and preset['kind'] == 'control': return [dict(key='set:control', name=SETS['set:control'], file='', size=[])] if self.control_rows(pid) else []
        if preset and preset['kind'] in BUTTON_KINDS:   # a button set: the whole menu, or the in-game buttons (all, phone, PC)
            return [dict(key=k, name=SETS[k], file='', size=[]) for k in BUTTON_KINDS[preset['kind']] if self.button_rows(k)]
        if preset and preset['kind'] in ('background', 'virus', 'food'):
            # A background goes on a whole set (menu or arena), a virus on both viruses or one, a food on one of the
            # game's three foods (each with its small copies).
            if preset['kind'] in ('background', 'virus'): sets = [k for k, *_ in self.places()[preset['kind']]]
            else:   # all the game's foods at once first (what most players want), then each one
                sets = [f'set:food:{i}' for i in range(3) if self.set_rows(f'set:food:{i}')]
                if len(sets) > 1: sets.insert(0, 'set:food:all')
            own = [r for name in preset['for'] for r in rows.values() if r['name'] == name and usable(r) and name not in LOCKED and r not in out] if preset['kind'] == 'virus' else []
            return [dict(key=k, name=SETS[k], file='', size=[]) for k in sets] + \
                [dict(key=r['key'], name=r.get('label') or r['name'], file=r['name'], size=[r['width'], r['height']]) for r in out + own][:limit]
        if str(pid).startswith('preset:'):
            made_for = next((p['for'] for p in presets() if p['id'] == pid), [])
            out += [r for name in made_for for r in rows.values() if r['name'] == name and usable(r) and name not in LOCKED and r not in out]
        try:
            buffer = io.BytesIO(); self.image(pid).save(buffer, format='PNG')
            token = self.lab.similar.upload(buffer.getvalue())
            found = self.lab.search(like=f'upload:{token}', kind='visual', tidy=True, limit=limit * 3)['items']
        except (OSError, ValueError): found = []
        seen = {r['key'] for r in out}
        for item in found:
            row = rows.get(item['key'])
            if usable(row) and row['key'] not in seen and row['name'] not in LOCKED: out.append(row); seen.add(row['key'])
        return [dict(key=r['key'], name=r.get('label') or r['name'], file=r['name'], size=[r['width'], r['height']]) for r in out[:limit]]

    def game_targets(self):
        """Where a preset can be exported as ready game files (kind -> [[set, name]]): only sets whose files stand alone in
        the game folder (a menu background is its own PNG), so they can be dropped in as they are."""
        with self.lab.lock:
            return {'background': [[k, SETS[k]] for k in BACKGROUND_SETS if self.loose(self.background_rows(k))],
                    'virus': [[k, SETS[k]] for k in VIRUS_SETS if self.loose(self.set_rows(k))]}

    def places(self):
        """Where each kind of preset can go in this game (kind -> [[set, name, picture key]], the usual place first): the
        Presets window offers them, so one background can go on the menu and others on the light and dark in-game floors,
        a food on one of the game's three foods (shown with the game's own picture), buttons on phones or PCs only."""
        with self.lab.lock:
            background = [k for k in BACKGROUND_SETS if self.background_rows(k)]
            if background == ['set:everywhere', 'set:menu']: background = ['set:menu']   # no in-game floor here: just the menu
            virus = [k for k in VIRUS_SETS if self.set_rows(k)]
            if virus[1:] and len(self.set_rows('set:viruses')) == len(self.set_rows(virus[1])): virus = virus[:1]   # one virus only: no modes to tell apart
            food = [f'set:food:{i}' for i in range(3) if self.set_rows(f'set:food:{i}')]
            if len(food) > 1: food.insert(0, 'set:food:all')
            look = lambda k: (self.set_rows(k) or [{}])[0].get('key', '') if k.startswith('set:food:') and k != 'set:food:all' else ''
            out = {'background': background, 'virus': virus, 'food': food,
                   **{kind: [k for k in sets if self.button_rows(k)] for kind, sets in BUTTON_KINDS.items()},
                   'logo': ['set:logo'] if self.logo_rows() else []}
            out = {kind: [[k, SETS[k], look(k)] for k in keys] for kind, keys in out.items()}
            # A font preset goes on one of the game's fonts: the player names, the menu text...
            import fonts as fontlab
            try: out['font'] = [[fid, f['label'], ''] for fid, f in fontlab.game_fonts(self.lab.game).items()]
            except (TypeError, OSError): out['font'] = []
            return out

    @staticmethod
    def loose(rows):
        """The rows that are whole files: a PNG in the assets folder, or one inside an archive there (assets.zip), not a
        piece of a sprite sheet."""
        return [r for r in rows if r.get('editable') and not r.get('parent') and str(r.get('source', '')).startswith('assets/')
                and (not r.get('entry') or str(r['source']).lower().endswith('.zip'))]

    @staticmethod
    def game_path(row):
        """Where the file goes, from the assets folder: its own name, or inside the archive it lives in."""
        source = str(row['source'])[len('assets/'):]
        return f"inside {source}/{row['entry']}" if row.get('entry') else source

    def game_files(self, pid, key, color=''):
        """The game's files a preset changes when put on a set, made as the lab would put them in: [(file name, picture)].
        Named exactly as in the game's assets folder, so they can be dropped in without renaming anything."""
        import design
        with self.lab.lock:
            rows = self.loose(self.background_rows(key) if key in BACKGROUND_SETS else self.set_rows(key))
        picture = self.render(pid, color)
        out = []
        for row in rows:
            size = (row['width'], row['height'])
            art = design.tile_resize(picture.convert('RGBA'), size) if key in BACKGROUND_SETS else design.fit(picture, size, 1.0, 0.0, 0.0)
            out.append((self.game_path(row), art))
        return out

    GAME_FILES_NOTE = ("These are Mitos.is game files, named exactly as in the game. To use them, open the game's assets folder "
                       '(Steam: right-click Mitos.is > Manage > Browse local files > assets) and replace the files there with these.\r\n'
                       'Files in a folder named "inside assets.zip" go inside that archive in the assets folder: open assets.zip and '
                       'replace the files of the same name in it.\r\n'
                       'Keep a copy of the originals first. Easier: Asset Lab puts them in for you (Apply to Game) and Restore original '
                       'takes them back out.\r\n')

    def export(self, ids, target=''):
        """Presets to send to people, as one zip: each picture as a PNG in its category's folder; a button set as its
        preview and every button in its look, drawn from this game's own buttons. With a target (a set, like the main
        menu): each preset as the game files it changes there, one folder per preset, ready to drop in the game."""
        import zipfile
        safe = lambda text: re.sub(r'[^\w .&()-]+', ' ', str(text)).strip() or 'preset'
        listed = {p['id']: p for p in presets()}
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            if target:
                targets = {k for kind in self.game_targets().values() for k, _ in kind}
                if target not in targets: raise ValueError("These presets can't be made into game files for that.")
                chosen = [listed[pid] for pid in ids if pid in listed and listed[pid]['kind'] == ('background' if target in BACKGROUND_SETS else 'virus')]
                if not chosen: raise ValueError('No presets to export there.')
                folders = set()
                for p in chosen:
                    folder = safe(p['name'])
                    while folder in folders: folder += ' 2'   # two presets of the same name: a folder each still
                    folders.add(folder)
                    for name, art in self.game_files(p['id'], target):
                        z.writestr((folder + '/' if len(chosen) > 1 else '') + name, png_bytes(art))
                z.writestr('READ ME.txt', self.GAME_FILES_NOTE)
            for pid in [] if target else ids:
                p = listed.get(pid)
                if not p: continue
                folder = f"{safe(p['group'])}/{safe(p['category'])}"
                if p['kind'] in BUTTON_KINDS:
                    texture, base, drawn = self.material(pid), self.base(pid), self.drawn(pid) or {}
                    z.writestr(f"{folder}/{safe(p['name'])}/preview.png", png_bytes(self.render(pid)))
                    with self.lab.lock: buttons = self.buttons(p['kind'])
                    for name, art in buttons.items():
                        z.writestr(f"{folder}/{safe(p['name'])}/{name}.png", png_bytes(button_sets.button(name, art, p['look'], texture, base, drawn.get(name))))
                else: z.writestr(f"{folder}/{safe(p['name'])}.png", png_bytes(self.image(pid)))
        return out.getvalue()

    def export_folder(self, ids, name, target=''):
        """The export as a folder in Downloads (a new one: name, name (2)...), the zip's folders and pictures in it."""
        import zipfile
        base = downloads(); base.mkdir(parents=True, exist_ok=True)
        folder, n = base / name, 1
        while folder.exists(): n += 1; folder = base / f'{name} ({n})'
        with zipfile.ZipFile(io.BytesIO(self.export(ids, target))) as z:
            for item in z.infolist():
                target = (folder / item.filename).resolve()
                if folder.resolve() not in target.parents: continue   # names are made here, but never outside the folder
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(item))
        return folder

    def background_rows(self, key):
        """The backgrounds a background set covers: the menu's (one per game theme), the in-game floors, or both."""
        menu = list(self.sets['menu']()) if 'menu' in self.sets else []
        arena = list(self.sets['arena']()) if 'arena' in self.sets else []
        light = lambda r: r['name'].lower().endswith('_w.png')
        return {'set:everywhere': menu + arena, 'set:menu': menu, 'set:arena': arena, 'set:arena-light': [r for r in arena if light(r)],
                'set:arena-dark': [r for r in arena if not light(r)]}.get(key, [])

    def set_rows(self, key):
        """The game art a set of sprites is: both viruses (the red one the game puts back itself is left out), or one food."""
        from lab_core import LOCKED
        if key in VIRUS_SETS:
            import design
            names = {VIRUS_FILES[key]} if key in VIRUS_FILES else set(design.VIRUSES)
            return [r for r in self.lab.catalog.rows.values() if r['name'] in names and r['name'] not in LOCKED and r['editable']
                    and not r.get('legacy') and not r.get('removed')]
        if key.startswith('set:food:') and 'food' in self.sets:
            kinds = list(self.sets['food']())
            if key == 'set:food:all': return [r for kind in kinds for r in kind]
            i = int(key.rsplit(':', 1)[1])
            return list(kinds[i]) if i < len(kinds) else []
        return []

    def apply(self, pid, key, color='', strength=1.0, scale=1.0):
        """Put the picture (recolored) on a game sprite: fitted to its frame, centered, its shape kept. On a set of
        backgrounds (BACKGROUND_SETS): tiled to every one of them, so the copies the game lays side by side meet.
        No key: the best place for it (Use now in Presets, when this game has none of the usual places)."""
        import design
        if not key:
            found = self.targets(pid, limit=1)
            if not found: raise ValueError('This game has nowhere to put that picture.')
            key = found[0]['key']
        if key == 'set:logo':   # every copy of the title, the new one fitted to each, centered
            rows = self.logo_rows()
            if not rows: raise ValueError('This game has no title to change.')
            picture = self.render(pid, color)
            with self.lab.lock:
                # Size: 1 fits all of it in the title's place; bigger may cut off what stands out, smaller leaves room around it.
                scale = max(0.4, min(2.0, float(scale or 1)))
                for row in rows: self.lab.commit(row['key'], design.fit(picture, (row['width'], row['height']), scale, 0.0, 0.0), dict(picture=pid, color=color or '', scale=round(scale, 2)), persist=False)
                self.lab.persist()
            return dict(key=key, name=SETS[key], count=len(rows))
        if key == 'set:control':
            rows = self.control_rows(pid)
            if not rows: raise ValueError('This game has no matching button for this preset.')
            picture = self.render(pid)
            with self.lab.lock:
                for row in rows:
                    self.lab.commit(row['key'], design.fit(picture, (row['width'], row['height']), 1.0, 0.0, 0.0), dict(picture=pid), persist=False)
                self.lab.persist()
            return dict(key=key, name=SETS[key], count=len(rows))
        if key == 'set:cleanup':   # buttons and panels: the art made fully transparent
            rows = self.hide_rows(pid)
            if not rows: raise ValueError('This game has none of these buttons or panels.')
            with self.lab.lock:
                for row in rows: self.lab.commit(row['key'], Image.new('RGBA', (row['width'], row['height']), (0, 0, 0, 0)), dict(picture=pid, hidden=True), persist=False)
                self.lab.persist()
            return dict(key=key, name=SETS[key], count=len(rows))
        if key in button_sets.SETS:   # every button of the set restyled from the game's own, in the set's look
            preset = self.button_set(pid)
            if not preset: raise ValueError('Pick a button set from Presets for the buttons.')
            rows = self.button_rows(key)
            if not rows: raise ValueError('This game has no such buttons.')
            color = str(color or '').lower() if re.fullmatch(r'#[0-9a-f]{6}', str(color or '').lower()) else ''
            texture, base, drawn = self.material(pid, color), self.base(pid, color), self.drawn(pid, color) or {}
            with self.lab.lock:
                for row in rows:
                    art = button_sets.button(row['name'], self.lab.catalog.image(row['key']).convert('RGBA'), preset['look'], texture, base, drawn.get(row['name']))
                    self.lab.commit(row['key'], art, dict(picture=pid, color=color or ''), persist=False)
                self.lab.persist()
            return dict(key=key, name=SETS[key], count=len(rows))
        if key in SETS and key not in BACKGROUND_SETS:   # sprites: fitted to each frame, their own shape
            rows = self.set_rows(key)
            if not rows: raise ValueError('This game has no such art.')
            picture = self.render(pid, color)
            with self.lab.lock:
                for row in rows:
                    self.lab.commit(row['key'], design.fit(picture, (row['width'], row['height']), 1.0, 0.0, 0.0), dict(picture=pid, color=color or ''), persist=False)
                self.lab.persist()
            return dict(key=key, name=SETS[key], count=len(rows))
        if key in SETS:
            rows = self.background_rows(key)
            if not rows: raise ValueError('This game has no such backgrounds.')
            picture = self.render(pid, color)
            with self.lab.lock:
                for row in rows:
                    tile = design.tile_resize(picture.convert('RGBA'), (row['width'], row['height']))
                    # Strength: 1 covers the game's own color; less lets it show through (the game's base color and its
                    # dark-mode shade mix in, the way its own patterns are drawn).
                    tile.putalpha(int(round(255 * max(0.1, min(1.0, float(strength))))))
                    self.lab.commit(row['key'], tile, dict(picture=pid, color=color or '', strength=round(float(strength), 2)), persist=False)
                self.lab.persist()
            return dict(key=key, name=SETS[key], count=len(rows))
        row = self.lab.row(key)
        if not row['editable'] or row.get('group') == 'Fonts': raise ValueError('Pick a picture from the game to put it on.')
        art = design.fit(self.render(pid, color), (row['width'], row['height']), 1.0, 0.0, 0.0)
        version = self.lab.commit(key, art, dict(picture=pid, color=color or ''))
        return dict(key=key, name=row.get('label') or row['name'], version=version)
