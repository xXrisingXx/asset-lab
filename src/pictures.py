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
import re
from pathlib import Path
import numpy as np
from PIL import Image

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
    C2 = C * (1 + (tC / max(dC, 0.02) - 1) * m) if (tC > 0.02 and dC > 0.02) else (C * (1 - m) + tC * m * np.clip(L / max(dL, 1e-3), 0, 1) if tC > 0.02 else C * (1 - m))
    up = tL + (L - dL) * (1 - tL) / max(1e-3, 1 - dL)
    down = L * tL / max(1e-3, dL)
    L2 = L + (np.where(L >= dL, up, down) - L) * m
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
SETS = {'set:menu': 'All menu backgrounds', 'set:arena': 'Arena floor (light and dark mode)'}


def presets():
    """The ready-made pictures: [dict(id, name, category, kind, for)] (for: the game art each is made for; a background's
    is its sets, 'menu' and 'arena')."""
    try: data = json.loads((PRESETS / 'presets.json').read_text(encoding='utf-8')).get('presets') or []
    except (OSError, ValueError): return []
    return [dict(id='preset:' + p['id'], name=p['name'], category=p.get('category') or 'Other', kind=p.get('kind') or 'sprite', **{'for': list(p.get('for') or [])}) for p in data
            if isinstance(p, dict) and re.fullmatch(r'[a-z0-9-]{1,40}', str(p.get('id', ''))) and (PRESETS / str(p.get('file', ''))).is_file()]


class Pictures:
    """Pictures pasted into Theme AI (kept in workspace/pictures by content) and the presets shipped with the lab."""

    def __init__(self, lab):
        self.lab, self.folder = lab, lab.root / 'pictures'
        self.sets = {}   # 'menu' / 'arena' -> a function giving those rows (the lab server hands in the Designer's)

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
            entry = next((p for p in json.loads((PRESETS / 'presets.json').read_text(encoding='utf-8'))['presets'] if p['id'] == pid[7:]), None)
            path = PRESETS / entry['file'] if entry else None
        else: path = self.folder / f'{pid}.png'
        if not path or not path.is_file(): raise ValueError('That picture is no longer here. Paste it again.')
        with Image.open(path) as im: return im.convert('RGBA')

    def render(self, pid, color=''):
        """The picture, recolored when a color is given."""
        image = self.image(pid)
        return recolor(image, color) if re.fullmatch(r'#[0-9a-f]{6}', str(color or '').lower()) else image

    def targets(self, pid, limit=6, only=None):
        """The game art the picture is most likely for, best first: the checked pictures it can go on, a preset's own,
        then the look-alikes (image search)."""
        from lab_core import LOCKED   # art the game puts back by itself (the red virus): never offered
        rows, out = self.lab.catalog.rows, []
        usable = lambda r: r and r['editable'] and r['kind'] in ('image', 'sprite') and r.get('group') != 'Fonts' and not r.get('server_only') \
            and not r.get('legacy') and r.get('primary', True) and not r.get('removed')
        out = [rows[k] for k in only or [] if usable(rows.get(k)) and rows[k]['name'] not in LOCKED]
        preset = next((p for p in presets() if p['id'] == pid), None)
        if preset and preset['kind'] == 'background':   # a background goes on a whole set, menu or arena
            sets = [dict(key=f'set:{s}', name=SETS[f'set:{s}'], file='', size=[]) for s in preset['for'] if f'set:{s}' in SETS and s in self.sets and self.sets[s]()]
            return sets + [dict(key=r['key'], name=r.get('label') or r['name'], file=r['name'], size=[r['width'], r['height']]) for r in out][:limit]
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

    def apply(self, pid, key, color='', strength=1.0):
        """Put the picture (recolored) on a game sprite: fitted to its frame, centered, its shape kept. On a set of
        backgrounds ('set:menu', 'set:arena'): tiled to every one of them, so the copies the game lays side by side meet."""
        import design
        if key in SETS:
            rows = list(self.sets.get(key[4:], lambda: [])())
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
