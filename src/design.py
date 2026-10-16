"""Design: redraw game art with a local image model (FLUX.2 klein via stable-diffusion.cpp) from a prompt.

The current art is placed on a flat chroma background and edited by the model, which keeps its shape and layout. The
background is then keyed out with proper unmixing (so glows and drips keep soft edges), and the result is fitted into
each target sprite's fixed atlas frame. Variants are kept, so the user can pick, nudge, refine and only then apply.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
import numpy as np
from PIL import Image
import runtime
import settings as labsettings

KEYS = {'green': (0, 255, 0), 'magenta': (255, 0, 255), 'blue': (0, 64, 255)}
TITLE = r'\b(?:title|logo|wordmark|game name|mitosis (?:text|name|title|logo)|main menu (?:name|text|title)|name)\b'
# Objects, materials and styles that need drawing; plain colors, gradients and glows stay with the theme shader.
VERBS = r'\b(?:add|put|draw|stick|wear(?:ing)?|redesign|re-?draw|restyle|reimagine|turn (?:it|the \w+(?: \w+)?) into|make (?:it|the \w+(?: \w+)?) look like)\b'
DRAWN = (r'\b(?:hats?|horns?|crown|halo|blood(?:y)?|drips?|dripping|bats?|cobwebs?|webs?|skulls?|pumpkins?|ghosts?|eyes|teeth|fangs|cracked|cracks|'
         r'spooky|creepy|scary|horror|halloween|zombie|funny|silly|goofy|cute|evil|christmas|xmas|santa|snow(?:y|ed)?|icicles?|frozen|'
         r'slime|slimy|graffiti|steampunk|cyberpunk|3d|stone|wooden|bone|rusty|melting|burning|on fire)\b')


# Things that only exist once drawn (unlike a color or a mood).
OBJECTS = (r'\b(?:hats?|horns?|crowns?|halos?|wings?|bats?|cobwebs?|skulls?|pumpkins?|ghosts?|eyes|teeth|fangs|mustaches?|moustaches?|'
           r'glasses|sunglasses|bows?|ribbons?|flowers?|antlers?|beards?|scarf|scarves|tiaras?|masks?)\b')


# Verbs that always mean drawing, whatever follows ("turn the coin into a diamond", even though diamonds exist as sprites).
REDRAW = r'\b(?:redesign|re-?draw|reimagine|turn (?:it|them|the \w+(?: \w+)?) into|replace (?:it|them|the \w+(?: \w+)?) with)\b'
# New art rather than a restyle: "something entirely different", "a whole new background", "generate a city skyline".
NEW_ART = (r'\b(?:(?:entirely|completely|totally|really) (?:different|new)|something (?:new|different|else)|brand[- ]new|whole new|'
           r'(?:new|different) (?:\w+ )?(?:image|picture|art|artwork|design|background|backdrop|version|scene|texture|wallpaper)s?|'
           r'(?:entire|whole|full) (?:look|design|reskin|redesign)|reskin|generate|create (?:a|an|me|new)|draw (?:me|a|an)|'
           r'skylines?|city ?scapes?|cities|landscapes?|mountains?|jungle|desert|underwater|beach|castle|dungeon|graveyard|cemetery|'
           r'starfield|nebula|outer space|tv static|static noise)\b')
# Backgrounds are replaced by a whole new picture that tiles, not restyled around a cut-out.
SCENE_GROUPS = ('Backgrounds',)
DEFAULT_SECONDS = 45.0   # one drawing on an 8 GB card, until this PC's own time is known (Designer.learn_time)
THEME_SHEETS = ('theme.png', 'theme4.png')   # the sheets the game shows all the time: every copy of a sprite gets the drawing
VIRUSES = ('virus.png', 'light_virus.png', 'spawner.png')
SPAWNER_RED = '#e0262f'   # the game paints the spawner red in maps (so players call it the red virus)
# Store badges and sign-in buttons show other companies' logos: never redrawn with the rest of the game.
KEEP_AS_IS = {'icon-iphone', 'icon-android', 'icon-facebook', 'icon-gamecenter'}
SCENE_SUBJECT = 'a seamless repeating background picture for the game menu'   # theme.py words its art direction for scenery by this
FOOD_MAX = 40   # every food sprite the game uses fits in one set (about 19 today)


def is_menu_background(row):
    """The main menu background of each game theme (bg2.png, bg7.png, bg9.png): drawn as one picture for all of them."""
    return row.get('group') == 'Backgrounds' and bool(re.fullmatch(r'bg\d+\.png', str(row.get('name', '')), re.I))


def is_food(row):
    """The pellets players eat (food, food1-3, foodc/f/m/r/s...), on the theme sheets."""
    return row.get('kind') == 'sprite' and row.get('group') == 'Cells & gameplay' and str(row.get('name', '')).startswith('food')
GAME_BG = (13, 40, 41)          # the menu's base color compiled into Mitosis.exe (#0D2829); bg2.png is an overlay on it
SCENE_ZOOM, SCENE_CALM = 2.5, 0.5   # defaults: big shapes, half calmed (the game shows the tile at about a quarter size)
# New wording for the title: "make the title say MITOSIS 2", "change the title text to Cell Wars".
WORDING = r'\b(?:say|says|read|reads|spell|spells)\s+(.+)$|\b(?:text|wording|word|name)\s+(?:to|into)\s+(.+)$'


def wants_design(text, unknown=()):
    """A request to redraw art rather than recolor or shade it: something only drawing can do ("spooky", "a hat"), a
    redraw verb ("turn it into"), new wording for the title, or a drawing verb ("add") with words the shader does not
    understand. "Add a rainbow outline" stays a theme."""
    text = text.lower()
    return bool(re.search(DRAWN, text) or re.search(REDRAW, text) or re.search(NEW_ART, text) or (re.search(TITLE, text) and re.search(WORDING, text))
                or (re.search(VERBS, text) and unknown))


def seamless(img):
    """A picture that tiles: its center blended over a half-turned copy whose borders already meet across the edges."""
    a = np.asarray(img.convert('RGB'), np.float32)
    h, w = a.shape[:2]
    b = np.roll(np.roll(a, h // 2, axis=0), w // 2, axis=1)
    my, mx = np.sin(np.pi * (np.arange(h) + 0.5) / h), np.sin(np.pi * (np.arange(w) + 0.5) / w)
    m = np.sqrt(np.outer(my, mx))[..., None]   # 1 in the middle, 0 at every edge
    return Image.fromarray(np.clip(a * m + b * (1 - m), 0, 255).astype(np.uint8)).convert('RGBA')


def cover(art, frame, scale=1.0, dx=0.0, dy=0.0):
    """Fill a frame with a picture (cropping what does not fit); scale zooms in, dx/dy (-1..1) move the crop."""
    W, H = frame
    s = max(W / art.width, H / art.height) * max(1.0, float(scale))
    big = art.resize((max(W, round(art.width * s)), max(H, round(art.height * s))), Image.Resampling.LANCZOS)
    x = round((big.width - W) * (0.5 + 0.5 * max(-1, min(1, dx)))); y = round((big.height - H) * (0.5 + 0.5 * max(-1, min(1, dy))))
    return big.crop((x, y, x + W, y + H))


def wording(prompt):
    """The new text a request wants on the title, as written ("MITOSIS 2"), or ''."""
    found = re.search(WORDING, prompt, re.I)
    if not found: return ''
    text = re.split(r'\s+(?:and|with|in|but|instead)\s+|[.!?;]', (found.group(1) or found.group(2)).strip())[0]
    return text.strip(' "“”\'').strip()[:32]


def subject_part(prompt):
    """The thing to redraw: what comes before "into" / "look like" / "with" in "turn the coin into a diamond"."""
    if re.search(r'\b(?:turn|make|replace|change)\b.*\b(?:into|look like|looks like|with)\b', prompt.lower()):
        return re.split(r'\b(?:into|look like|looks like|with)\b', prompt, maxsplit=1, flags=re.I)[0]
    return prompt


def key_for(prompt):
    """A background color the request is unlikely to paint the art with."""
    text = prompt.lower()
    if re.search(r'green|toxic|slime|zombie|lime|grass|leaf|leaves|forest|emerald|mint', text): return 'magenta'
    return 'green'


def canvas_size(w, h):
    """A model canvas (multiples of 16, about 0.45 megapixels) with room around the art for drips, hats and glow."""
    aspect = max(0.4, min(4.0, w / max(1, h)))
    width = 1024 if aspect >= 1 else max(512, round(1024 * aspect / 16) * 16)
    height = max(384, min(1024, round(width / aspect * 1.75 / 16) * 16))
    return width, height


def render_progress(line, which, steps):
    """Only sampling bars are drawing steps; tensor-loading bars can also be measured in GB/s."""
    if 'encode_first_stage completed' in line: return 'Reading the art direction…'
    picture = re.search(r'generating image: (\d+)/(\d+)', line)
    if picture: return f'Drawing {which}: image {picture.group(1)} of {picture.group(2)}'
    step = re.search(r'\|\s*(\d+)/(\d+)\s*-\s*[\d.]+(?:s/it|it/s)', line)
    if step and int(step.group(2)) == steps: return f'Drawing {which}: step {step.group(1)} of {step.group(2)}'
    if re.search(r'\bdecoding \d+ latents', line): return 'Finishing the images…'
    return None


def reference(art, key, size):
    W, H = size
    canvas = Image.new('RGBA', (W, H), (*KEYS[key], 255))
    s = min(W * 0.8 / art.width, H * 0.62 / art.height)
    scaled = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.Resampling.LANCZOS)
    canvas.alpha_composite(scaled, ((W - scaled.width) // 2, (H - scaled.height) // 2))
    return canvas.convert('RGB')


def unkey(image, key, tolerance=1.0):
    """Remove the flat key background. Alpha comes from how far each pixel is from the key color, and partly covered
    pixels are unmixed from it, so a red glow over green becomes a translucent red glow, not a brown fringe."""
    rgb = np.asarray(image.convert('RGB')).astype(np.float32)
    k = np.array(KEYS[key], np.float32)
    # The model's "flat" background drifts a little: measure it from the border, where only background should be.
    border = np.concatenate([rgb[:6].reshape(-1, 3), rgb[-6:].reshape(-1, 3), rgb[:, :6].reshape(-1, 3), rgb[:, -6:].reshape(-1, 3)])
    near = border[np.linalg.norm(border - k, axis=1) < 140]
    if len(near) > 50: k = np.median(near, axis=0)
    distance = np.linalg.norm(rgb - k, axis=2)
    low, high = 38 * tolerance, 150 * tolerance
    alpha = np.clip((distance - low) / max(1.0, high - low), 0, 1)
    # A glow blended over the key is far from the key color yet partly see-through. Assuming the art itself has no key
    # color in it, its coverage lies between max(other channels) / 255 and 1 - spill / 255; a pixel with spill takes the
    # middle of that range, which turns a red-over-green halo back into a translucent red one instead of olive.
    keyed = [i for i in range(3) if KEYS[key][i] > 128]
    other = [i for i in range(3) if KEYS[key][i] <= 128]
    spill = rgb[..., keyed].min(axis=2) - rgb[..., other].max(axis=2)
    upper = 1 - np.clip(spill / 255, 0, 1)
    lower = np.clip(rgb[..., other].max(axis=2) / 255, 0, 1)
    alpha = np.minimum(alpha, np.where(spill > 12 * tolerance, np.minimum(upper, (lower + upper) / 2), 1))
    a = np.maximum(alpha, 1e-3)[..., None]
    color = np.clip((rgb - (1 - a) * k) / a, 0, 255)
    # Spill: a key-colored cast left on edges and glows. The key is chosen to be a color the request does not use, so
    # the key channel may never exceed the others (green key: G <= max(R, B); magenta key: min(R, B) <= G).
    if key == 'magenta':
        cap = np.minimum(color[..., 0], color[..., 2])
        excess = np.maximum(0, cap - color[..., 1])
        color[..., 0] -= excess; color[..., 2] -= excess
    else:
        dominant = int(np.argmax(KEYS[key]))
        others = [i for i in range(3) if i != dominant]
        color[..., dominant] = np.minimum(color[..., dominant], color[..., others].max(axis=2))
    out = np.dstack([color, alpha * 255]).astype(np.uint8)
    out[alpha < 0.02] = 0
    return Image.fromarray(out, 'RGBA')


def core_box(art, share=0.3):
    """The dense part of a drawing (the letters), ignoring sparse extras such as bats or drip tails at its edges."""
    a = np.asarray(art.getchannel('A')) > 40
    rows, cols = a.sum(axis=1), a.sum(axis=0)
    if not rows.any(): return None
    ys, xs = np.nonzero(rows >= rows.max() * share)[0], np.nonzero(cols >= cols.max() * 0.03)[0]
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def content_box(art, threshold=40, border=3, margin=0.04):
    """Where the drawing is. The model sometimes leaves a faint line along the canvas edge, which made every drawing look
    small once fitted into its frame: specks and the outermost pixels are ignored, and a small margin keeps soft glows."""
    a = np.asarray(art.getchannel('A')) > threshold
    if a.shape[0] > 4 * border and a.shape[1] > 4 * border:
        a[:border] = False; a[-border:] = False; a[:, :border] = False; a[:, -border:] = False
    ys, xs = np.nonzero(a)
    if not len(xs): return None
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    mx, my = round((x1 - x0) * margin), round((y1 - y0) * margin)
    return max(border, x0 - mx), max(border, y0 - my), min(art.width - border, x1 + mx), min(art.height - border, y1 + my)


def fit(art, frame, scale=1.0, dx=0.0, dy=0.0, fill=False):
    """Crop to the drawn content and fit it into a sprite frame (w, h), keeping its proportions.
    fill: size by the dense core instead, so the main shape fills the frame and sparse edges may be trimmed."""
    box = content_box(art)
    if not box: return Image.new('RGBA', frame, (0, 0, 0, 0))
    if fill:
        core = core_box(art)
        if core:
            w, h = frame
            s = min(w / (core[2] - core[0]), h / (core[3] - core[1])) * max(0.2, min(2.0, scale))
            big = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.Resampling.LANCZOS)
            cx, cy = (core[0] + core[2]) / 2 * s, (core[1] + core[3]) / 2 * s
            out = Image.new('RGBA', frame, (0, 0, 0, 0))
            out.paste(big, (round(w / 2 - cx + dx * w / 2), round(h / 2 - cy + dy * h / 2)), big)
            return out
    art = art.crop(box)
    w, h = frame
    s = min(w / art.width, h / art.height) * max(0.2, min(2.0, scale))
    art = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.Resampling.LANCZOS)
    out = Image.new('RGBA', frame, (0, 0, 0, 0))
    out.alpha_composite(art, (round((w - art.width) / 2 + dx * w / 2), round((h - art.height) / 2 + dy * h / 2))) if art.width <= w and art.height <= h else \
        out.paste(art, (round((w - art.width) / 2 + dx * w / 2), round((h - art.height) / 2 + dy * h / 2)), art)
    return out


def keep_shape(art, original, grow=2):
    """Trim drawn art to the original sprite's silhouette (grown a little so soft edges survive)."""
    from PIL import ImageFilter
    mask = original.getchannel('A').point(lambda v: 255 if v > 24 else 0).filter(ImageFilter.MaxFilter(grow * 2 + 1)).filter(ImageFilter.GaussianBlur(1))
    alpha = Image.fromarray((np.asarray(art.getchannel('A'), np.float32) * np.asarray(mask, np.float32) / 255).astype(np.uint8))
    out = art.copy(); out.putalpha(alpha)
    return out

def tinted(image):
    """True for a plain white glyph: the game multiplies those by a UI color, so a colorful redraw comes out tinted."""
    a = np.asarray(image.convert('RGBA')).astype(np.float32) / 255
    solid = a[..., 3] > 0.5
    if solid.sum() < 16: return False
    rgb = a[..., :3][solid]
    return bool(rgb.min(axis=1).mean() > 0.85 and (rgb.max(axis=1) - rgb.min(axis=1)).mean() < 0.08)


def crisp(art):
    """A light unsharp mask on the colors (not the outline), for drawings shrunk into small sprite frames. Transparent
    pixels first take the color of the art next to them, so the edge does not pick up a dark fringe from them."""
    from PIL import ImageFilter
    a = np.asarray(art.convert('RGBA')).astype(np.float32)
    alpha = a[..., 3:4] / 255
    soft = lambda channels: np.asarray(Image.fromarray(np.clip(channels, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))).astype(np.float32)
    near_color = soft(a[..., :3] * alpha)
    near_weight = soft(alpha[..., 0] * 255)[..., None] / 255
    fill = near_color / np.maximum(near_weight, 0.01)
    rgb = np.where(alpha > 0.02, a[..., :3], fill)
    sharp = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=2))
    return Image.fromarray(np.dstack([np.asarray(sharp), a[..., 3].astype(np.uint8)]), 'RGBA')


def whiten(art):
    """White-and-gray version of a drawing, for icons the game multiplies by a color: the game's color then shows
    everywhere, and the drawing's detail survives as lighter and darker shades of it."""
    a = np.asarray(art.convert('RGBA')).astype(np.float32) / 255
    lum = a[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    solid = a[..., 3] > 0.5
    top = float(np.percentile(lum[solid], 98)) if solid.sum() > 16 else 1.0
    shade = np.clip(lum / max(top, 0.05), 0, 1) ** 0.75   # brightest part pure white, mid-tones lifted a little
    out = np.dstack([shade, shade, shade, a[..., 3]])
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), 'RGBA')


def tints():
    """White icons the game multiplies by one of its UI colors, measured on captures of the menus: name -> '#RRGGBB'."""
    try: return json.loads((Path(__file__).parent / 'data' / 'ui-tints.json').read_text(encoding='utf-8')).get('tints', {})
    except (OSError, ValueError, AttributeError): return {}


def leftover(art):
    """A warning when the model ignored the flat background: the cut-out then keeps lots of solid pixels at the canvas edge."""
    a = np.asarray(art.getchannel('A'))
    band = max(2, min(a.shape) // 30)
    edge = np.concatenate([a[:band].ravel(), a[-band:].ravel(), a[:, :band].ravel(), a[:, -band:].ravel()])
    share = float((edge > 128).mean())
    return 'The background did not cut out cleanly; try another variant or a lower cut-out.' if share > 0.2 else ''


class Designer:
    def __init__(self, lab, themes):
        self.lab, self.themes = lab, themes
        self.folder = lab.root / 'designs'
        self.jobs, self.lock = {}, threading.Lock()
        # How long one drawing takes on this PC (seconds, model loading included), learned from finished drawings, so a big
        # set ("make the whole game look cool": a hundred sprites) can show when it will be done.
        self.timing_file = self.folder / 'timing.json'
        try: self.per_drawing = max(5.0, min(900.0, float(json.loads(self.timing_file.read_text())['seconds_per_drawing'])))
        except (OSError, ValueError, KeyError, TypeError): self.per_drawing = DEFAULT_SECONDS

    def learn_time(self, seconds, drawings):
        """One finished job's time per drawing joins the running estimate (recent jobs count most)."""
        if drawings < 1 or seconds <= 0: return
        sample = max(5.0, min(900.0, seconds / drawings))
        self.per_drawing = round(0.6 * self.per_drawing + 0.4 * sample, 1)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.timing_file.write_text(json.dumps(dict(seconds_per_drawing=self.per_drawing)))
        except OSError: pass

    def status(self):
        lacking = runtime.missing('sd', 'diffusion', 'llm', 'vae')
        return dict(available=not lacking, missing=lacking, job=next((dict(j) for j in self.jobs.values() if j['state'] == 'running'), None),
                    seconds_per_drawing=self.per_drawing)

    # ----- what to redraw -----------------------------------------------------------------------
    def title_rows(self):
        """The MITOSIS title as the main menu draws it (not Strike.is's logo in theme2), one per distinct frame."""
        rows = [r for r in self.lab.catalog.rows.values() if r['name'] == 'logo' and r.get('primary', True) and not r.get('legacy')
                and self.lab.catalog.rows.get(r.get('parent', ''), {}).get('name') in ('theme.png', 'theme4.png')]
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: (r['source'].startswith('assets/assets.zip'), r['height'])):
            if r.get('dup') in seen: continue
            seen.add(r.get('dup')); out.append(r)
        return out

    def whole_game_rows(self):
        """Everything players look at all the time, for "make the whole game look cool" with drawing: the MITOSIS title,
        the viruses, every sprite on theme.png (the coin, the in-game potion, buttons, icons, panels...) with all its copies,
        and the food as one set. One drawing per sprite; store badges and specks are left as they are."""
        rows = self.lab.catalog.rows
        sheet_of = lambda r: rows.get(r.get('parent', ''), {}).get('name', '')
        live = lambda r: r['editable'] and not r.get('legacy') and not r.get('removed') and not r.get('server_only')
        badge = lambda r: r['name'] in KEEP_AS_IS or re.search(r'discord|app store|google play|facebook|game ?center', r.get('label') or '', re.I)
        names = {r['name'] for r in rows.values() if live(r) and sheet_of(r) == 'theme.png' and not badge(r)
                 and not r['name'].startswith('logo') and not is_food(r) and min(r['width'], r['height']) >= 16}
        sprites = sorted((r for r in rows.values() if live(r) and r['name'] in names and sheet_of(r) in THEME_SHEETS), key=lambda r: (r['name'], r['source']))
        # The three viruses: light mode, dark mode, and the spawner ("the red virus") that can appear in maps.
        viruses = [r for r in rows.values() if live(r) and r['name'] in VIRUSES]
        return self.title_rows() + viruses + sprites + self.food_rows()

    def food_rows(self):
        """Every food sprite the game uses (pellet shapes, blobs and dots in each size), one per art: drawn as one set."""
        rows, seen = [], set()
        for r in sorted(self.lab.catalog.rows.values(), key=lambda r: (r['name'], r['source'])):
            if not is_food(r) or not r['editable'] or not r.get('primary', True) or r.get('legacy') or r.get('removed') or r.get('dup') in seen: continue
            seen.add(r.get('dup')); rows.append(r)
        return rows

    def targets(self, prompt, only=None):
        if only:
            rows = [r for r in (self.lab.row(k) for k in only if k in self.lab.catalog.rows) if r['editable']]
            return rows[:FOOD_MAX] if rows and all(is_food(r) for r in rows) else rows[:8]   # room for a sprite and its identical copies (the coin has 7)
        subject = subject_part(prompt)
        if re.search(TITLE, subject.lower()): return self.title_rows()
        # "Full spooky reskin" is the whole game (offered as the title, the virus and the food), not the full-screen button.
        if re.search(r'\b(?:full|whole|entire|complete|total)\s+(?:\w+\s+)?(?:reskin|redesign|makeover|overhaul|look)\b|^\s*reskin\b', subject.lower()): return []
        names = {n for found in self.themes.named(subject)[0].values() for n in found}
        if not names:
            rows = self.family_rows(subject)
            # "Turn the food into crowns": every pellet the game shows, so none keeps the old look.
            return self.food_rows() if rows and all(is_food(r) for r in rows) else rows
        rows = [r for r in self.lab.catalog.rows.values() if r['name'] in names and r['editable'] and r.get('primary', True) and not r.get('legacy')]
        seen, out = set(), []
        for r in rows:
            if r.get('dup') in seen: continue
            seen.add(r.get('dup')); out.append(r)
        return out[:8]

    def family_rows(self, prompt, limit=6):
        """A group named in the request ("the cells", "every icon"): its first few sprites the game actually uses, one per art."""
        import theme as shading   # imported here: theme imports this module
        text = prompt.lower()
        wanted = [f for f, words in shading.WORDS.items() if f != 'everything' and re.search(rf'\b(?:{words})\b', text)]
        if not wanted: return []
        rows, seen = [], set()
        for r in self.lab.catalog.rows.values():
            if not r['editable'] or not r.get('primary', True) or r.get('legacy') or r.get('removed') or r.get('dup') in seen: continue
            if r['width'] < 24 or r['height'] < 24 or self.lab.catalog.children.get(r['key']): continue   # whole sheets and specks are not drawn
            if shading.families_of(r, self.lab.catalog.rows.get(r.get('parent', ''), {}).get('name', '')) in wanted:
                seen.add(r.get('dup')); rows.append(r)
        # Parts known by name (found on the game's screens) first, then sprites the game names in its code.
        rows.sort(key=lambda r: (not r.get('label'), not r.get('live'), r['name']))
        return rows[:limit]

    # ----- generating ---------------------------------------------------------------------------
    def start(self, prompt, keys=None, count=None, base=None, direction=None):
        """base: (job id, variant index) to refine a variant instead of the current art. count: variants to draw
        (Settings > Theme AI & drawing > Drawings per request when not given)."""
        prompt = str(prompt or '').strip()[:800]
        if count is None: count = labsettings.value(self.lab, 'drawings')
        if not prompt: raise ValueError('Describe the design you want.')
        lacking = self.status()['missing']
        if lacking: raise ValueError('The image model is not installed: ' + ', '.join(lacking))
        if base:
            parent = self.job(base[0])
            rows = [self.lab.row(k) for k in parent['targets']]
        else:
            rows = self.targets(prompt, keys)
        if not rows: raise ValueError('Name what to redesign, or check it in the library first.')
        # One drawing per sprite: different sprites are drawn one at a time (the title's sizes share one drawing, and so do
        # copies that share a name you gave and a size, such as the coin the game also downloads).
        # Food is the exception: one drawing for every pellet shape and size, the way a player swaps them all for crowns.
        first = rows[0]
        food, menu = is_food(first), is_menu_background(first)   # and one drawing for the menu background of every game theme
        rows = [r for r in rows if (food and is_food(r)) or (menu and is_menu_background(r)) or r['name'] == first['name'] or (first.get('label') and r.get('label') == first['label'] and (r['width'], r['height']) == (first['width'], first['height']))]
        with self.lock:
            if any(j['state'] == 'running' for j in self.jobs.values()): raise ValueError('A design is already being drawn.')
            job = dict(id=uuid.uuid4().hex, state='running', prompt=prompt, targets=[r['key'] for r in rows], names=[r.get('label') or r['name'] for r in rows],
                       frames=[[r['width'], r['height']] for r in rows], variants=[], message='Preparing…', progress=0, total=max(1, min(4, int(count))),
                       key=parent['key'] if base else key_for(prompt), base=base, at=time.time(), title=rows[0]['name'] == 'logo',
                       # Button and panel skins are stretched by the game, so their outline should stay; white icons are tinted by it.
                       stretched=rows[0]['group'] == 'Buttons & panels' and rows[0]['name'] != 'logo', tinted=tinted(self.lab.catalog.image(rows[0]['key'])),
                       tint=tints().get(rows[0]['name'], '') or (SPAWNER_RED if rows[0]['name'] == 'spawner.png' else ''),   # the color the game multiplies this art by
                       spawner=rows[0]['name'] == 'spawner.png',
                       scene=parent.get('scene', False) if base else rows[0]['group'] in SCENE_GROUPS,   # a background: a whole new picture that tiles
                       food=food)
            if direction: job['direction'] = str(direction)[:700]   # shared by a set drawn together, so they match
            if job['title'] and wording(prompt): job['text'] = wording(prompt)
            self.jobs[job['id']] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return dict(job)

    @staticmethod
    def subject(job):
        if job['title']: return 'the game title logo that reads MITOSIS (the O is a round dot)'
        if job.get('food'): return 'a small food pellet that players eat in a top-down cell game, seen from above'
        if job.get('scene'): return SCENE_SUBJECT
        # "Play button (Main menu)" -> "play button"; "icon-crown" -> "icon crown".
        name = re.sub(r'[-_]+', ' ', re.sub(r'\.png$|\s*\([^)]*\)\s*$', '', job['names'][0])).strip()
        return f'a game sprite: {name}'

    def instruction(self, job):
        if job.get('scene'):
            return (f'{job.get("direction") or job["prompt"]}. A brand new full-frame background picture for a 2D game menu, '
                    'filling the whole image edge to edge so it can repeat as a seamless tile. It is shown small and repeated behind '
                    'buttons and text, so use a few large, bold, simple shapes instead of many tiny details, and moderate contrast so '
                    'what is on top stays readable. Use the colors the request names; if it names none, rich colors that are not too '
                    'bright. No text, no logos, no frame, no border, no single big object in the middle. Clean, high quality game art.')
        what = self.subject(job)
        if job.get('text'): keep = f'Change the word MITOSIS so it reads exactly "{job["text"]}", in the same lettering style, on one line, centered.'
        elif job['title']: keep = 'Keep the exact word MITOSIS, spelled the same, in the same layout and size, centered.'
        elif job.get('food'): keep = 'Draw it as one simple object that fills most of the square, centered; its shape may change completely.'
        else: keep = 'Keep its outline, size and position, centered.'
        small = '' if job['title'] or max(job['frames'][0]) > 160 else ' It is shown small in the game, so use bold, simple shapes and strong contrast that read at a glance.'
        color = {'green': 'pure green (#00FF00)', 'magenta': 'pure magenta (#FF00FF)', 'blue': 'pure blue (#0040FF)'}[job['key']]
        return (f'{job.get("direction") or job["prompt"]}. Edit {what}. {keep}{small} Crisp, high quality game UI art. '
                f'The background must stay a perfectly flat solid {color} with nothing else on it: no scenery, no shadow or floor under the art, no frame, no extra text.')

    def _run(self, job):
        folder = self.folder / job['id']
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if job['base']:
                # Refining: the chosen variant, exactly as drawn (same canvas and background), is the new starting point.
                ref = Image.open(self.folder / job['base'][0] / f'raw-{int(job["base"][1])}.png').convert('RGB')
                size = ref.size
            elif job.get('scene'):
                # A new background is drawn from the words alone, at about the model's 0.6 megapixels, in the art's shape.
                source = self.lab.image(job['targets'][0])
                aspect = max(0.5, min(2.0, source.width / max(1, source.height)))
                size = (768, round(768 / aspect / 16) * 16) if aspect >= 1 else (round(768 * aspect / 16) * 16, 768)
                ref = None
            else:
                source = self.lab.image(job['targets'][0])
                size = canvas_size(source.width, source.height)
                ref = reference(source, job['key'], size)
            if ref is not None: ref.save(folder / 'reference.png')
            runs = []   # (art direction, variants drawn with it)
            if not job['base'] and not job.get('direction') and job['total'] > 1 and getattr(self, 'concepts', None):
                # Several variants: a different idea for each (mummy wraps / chains and a graveyard...), not near-copies.
                job['message'] = 'Coming up with ideas…'
                try: ideas = [i.strip()[:700] for i in (self.concepts(job['prompt'], self.subject(job), job['total']) or []) if i.strip()]
                except Exception: ideas = []
                if len(ideas) >= 2:
                    job['directions'] = ideas; job['direction'] = ideas[0]   # "More variants" keeps the first idea's style
                    runs = [(idea, 1) for idea in ideas]
            if not runs and not job['base'] and not job.get('direction') and getattr(self, 'director', None):
                # A short request ("spooky") becomes a concrete art direction the image model can follow.
                job['message'] = 'Writing the art direction…'
                try: job['direction'] = (self.director(job['prompt'], self.subject(job)) or '').strip()[:700]
                except Exception: job['direction'] = ''
            if not runs: runs = [(job.get('direction') or '', job['total'])]
            # The planner and the image model do not both fit on an 8 GB card (Settings: Free the graphics card before drawing).
            if labsettings.value(self.lab, 'free_gpu'): runtime.OLLAMA.unload()
            job['message'] = 'Loading the image model…'
            steps = labsettings.STEPS[labsettings.value(self.lab, 'drawing_quality')]   # Settings: Drawing quality
            log, made, failed = [], 0, 0
            for number, (direction, count) in enumerate(runs):
                if job.get('cancelled'): raise RuntimeError('Cancelled.')
                one = len(runs) > 1
                out = folder / (f'raw-{made}.png' if one else 'raw-%d.png')
                args = [str(runtime.path('sd')), '--diffusion-model', str(runtime.path('diffusion')), '--vae', str(runtime.path('vae')), '--llm', str(runtime.path('llm')),
                        # Leave GPU workspace for diffusion; the text encoder and decoder can run in RAM.
                        '--offload-to-cpu', '--backend', 'te=cpu,vae=cpu',
                        '--threads', str(max(1, min(4, (os.cpu_count() or 2) // 2))),
                        *(['-r', str(folder / 'reference.png')] if (folder / 'reference.png').is_file() else []), '-p', self.instruction(dict(job, direction=direction)),
                        '--cfg-scale', '1.0', '--steps', str(steps), '--sampling-method', 'euler', '-W', str(size[0]), '-H', str(size[1]),
                        '-s', str((int(time.time()) + number * 7919) % 100000), '-b', str(count), '-o', str(out)]
                # Cancel can arrive while preparing the command. Publish the child atomically with that check.
                with self.lock:
                    if job.get('cancelled'): raise RuntimeError('Cancelled.')
                    proc = subprocess.Popen(args, cwd=str(runtime.path('sd').parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            creationflags=runtime.QUIET | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0),
                                            text=True, encoding='utf-8', errors='replace')
                    job['pid'] = proc.pid
                which = f'idea {number + 1} of {len(runs)}' if one else f'{job["total"]} variant{"s" if job["total"] > 1 else ""}'
                job['message'] = 'Loading the image model…'
                try:
                    with (folder / 'render.log').open('a', encoding='utf-8') as diagnostics:
                        for line in proc.stdout:
                            diagnostics.write(line); diagnostics.flush()
                            log.append(line.rstrip()); log[:] = log[-40:]
                            message = render_progress(line, which, steps)
                            if message: job['message'] = message
                    proc.wait(timeout=1200)
                except BaseException:
                    # A full disk or a locked log must not leave the image model running with the graphics card and
                    # nobody reading its output (Cancel finds no process once this job ends).
                    subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, creationflags=runtime.QUIET, timeout=15)
                    raise
                if proc.returncode != 0: failed += 1
                made = len(list(folder.glob('raw-*.png')))
            if job.get('cancelled'): raise RuntimeError('Cancelled.')
            raws = sorted(folder.glob('raw-*.png'), key=lambda p: int(re.search(r'(\d+)', p.stem).group(1)))
            if not raws: raise RuntimeError('The image model failed: ' + ' / '.join(l for l in log[-6:] if l.strip())[:400])
            for index, raw in enumerate(raws):
                if job.get('scene'):   # nothing to cut out: the whole picture is the art, made to tile
                    seamless(Image.open(raw)).save(folder / f'variant-{index}.png')
                    job['variants'].append(dict(index=index, settings=dict(scale=1.0, dx=0.0, dy=0.0, tolerance=1.0, shape=0.0, fill=0.0), warning=''))
                    job['progress'] = index + 1
                    continue
                art = unkey(Image.open(raw), job['key'])
                # The model's "flat" background sometimes drifts: try a stronger cut-out before warning about it.
                for stronger in (1.25, 1.5):
                    if not leftover(art): break
                    again = unkey(Image.open(raw), job['key'], stronger)
                    if not leftover(again): art = again
                art.save(folder / f'variant-{index}.png')
                job['variants'].append(dict(index=index, settings=dict(scale=1.0, dx=0.0, dy=0.0, tolerance=1.0, shape=1.0 if job.get('stretched') else 0.0, fill=1.0 if job['title'] else 0.0),
                                            warning=leftover(art)))
                job['progress'] = index + 1
            job.update(state='complete', message=f'{len(raws)} variant{"s" if len(raws) != 1 else ""} ready. Pick one, adjust it, then apply.')
            self.learn_time(time.time() - job['at'], len(raws))
        except Exception as exc:  # a background job must always finish
            job.update(state='failed', message=str(exc)[:500])
        finally:
            job.pop('pid', None)
            (folder / 'job.json').write_text(json.dumps({k: v for k, v in job.items()}, indent=1), encoding='utf-8') if folder.is_dir() else None

    def cancel(self, job_id):
        job = self.job(job_id)
        with self.lock:
            job['cancelled'] = True
            pid = job.get('pid')
        if pid: subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'], capture_output=True, creationflags=runtime.QUIET, timeout=15)
        return dict(job)

    def job(self, job_id):
        if job_id in self.jobs: return self.jobs[job_id]
        if re.fullmatch(r'[a-f0-9]{32}', str(job_id)) and (self.folder / job_id / 'job.json').is_file():
            self.jobs[job_id] = json.loads((self.folder / job_id / 'job.json').read_text(encoding='utf-8'))
            return self.jobs[job_id]
        raise ValueError('Unknown design')

    # ----- reviewing and applying ---------------------------------------------------------------
    def render(self, job_id, index, target=0, settings=None, raw=False):
        job = self.job(job_id)
        folder = self.folder / job['id']
        if raw: return Image.open(folder / f'raw-{int(index)}.png').convert('RGBA')
        s = {'scale': 1.0, 'dx': 0.0, 'dy': 0.0, 'tolerance': 1.0, 'shape': 1.0 if job.get('stretched') else 0.0, 'fill': 1.0 if job['title'] else 0.0, 'white': 0.0, **(settings or {})}
        if job.get('scene'):
            # A background fills its frame. Zoom picks a part of the drawing (the game shows the tile small and repeated, so
            # zooming in keeps shapes big enough to read), which is then made to tile again; Calmer softens and darkens the
            # picture in its own colors (never toward the game's teal, which players want gone), so the menu's text stays
            # readable on top of it. The result is opaque, so none of that teal shows through.
            s = {'scale': SCENE_ZOOM, 'calm': SCENE_CALM, 'dx': 0.0, 'dy': 0.0, 'white': 0.0, **(settings or {})}
            frame = tuple(job['frames'][int(target)])
            raw = Image.open(folder / f'raw-{int(index)}.png').convert('RGBA')
            out = seamless(cover(raw, frame, float(s['scale']), float(s['dx']), float(s['dy']))).resize(frame, Image.Resampling.LANCZOS)
            calm = max(0.0, min(0.9, float(s['calm'])))
            if calm:
                a = np.asarray(out, np.float32)[..., :3]
                average = a.reshape(-1, 3).mean(axis=0)
                a = (a * (1 - calm * 0.4) + average * calm * 0.4) * (1 - calm * 0.45)   # less contrast, then darker, same colors
                out = Image.fromarray(np.dstack([np.clip(a, 0, 255), np.full(a.shape[:2], 255, np.float32)]).astype(np.uint8), 'RGBA')
            return whiten(out) if float(s['white']) >= 0.5 else out
        art = unkey(Image.open(folder / f'raw-{int(index)}.png'), job['key'], float(s['tolerance'])) if float(s['tolerance']) != 1.0 else \
            Image.open(folder / f'variant-{int(index)}.png').convert('RGBA')
        out = self._place(job, art, s, int(target))
        if max(out.size) <= 96: out = crisp(out)                   # a big drawing shrunk into a small frame goes soft
        return whiten(out) if float(s['white']) >= 0.5 else out   # last, so the brightest pixel of the final art is white

    def _place(self, job, art, s, target):
        frame = tuple(job['frames'][int(target)])
        if float(s['shape']) >= 0.5:
            # Keep the original outline: line the drawing up with the original's content box, then trim gently to its shape.
            original = self.lab.catalog.image(job['targets'][int(target)]).convert('RGBA')
            box = original.getchannel('A').point(lambda v: 255 if v > 24 else 0).getbbox()
            if original.size == frame and box:
                inner = fit(art, (box[2] - box[0], box[3] - box[1]), float(s['scale']), float(s['dx']), float(s['dy']))
                out = Image.new('RGBA', frame, (0, 0, 0, 0)); out.alpha_composite(inner, box[:2])
                return keep_shape(out, original, grow=max(2, round(min(frame) * 0.05)))
        return fit(art, frame, float(s['scale']), float(s['dx']), float(s['dy']), fill=float(s['fill']) >= 0.5)

    def apply(self, job_id, index, settings=None):
        job = self.job(job_id)
        if job['state'] != 'complete': raise ValueError('This design is not finished.')
        items = []
        with self.lab.lock:
            for target, key in enumerate(job['targets']):
                before = self.lab.revision(key)
                self.lab.commit(key, self.render(job_id, index, target, settings), dict(design=job['id'], prompt=job['prompt'], variant=int(index), settings=settings or {}), persist=False)
                items.append(dict(key=key, before=before, after=self.lab.revision(key)))
            self.lab.persist()
        job['applied'] = dict(index=int(index), items=items, at=time.time())
        (self.folder / job['id'] / 'job.json').write_text(json.dumps(job, indent=1), encoding='utf-8')
        return dict(job=job['id'], items=items, names=job['names'])

    def undo(self, job_id):
        job = self.job(job_id)
        applied = job.get('applied')
        if not applied: raise ValueError('This design was not applied.')
        reverted = 0
        with self.lab.lock:
            for item in applied['items']:
                if self.lab.revision(item['key']) == item['after']:
                    self.lab.step(item['key'], item['after'], -1, detail=False); reverted += 1
            self.lab.persist()
        job.pop('applied', None)
        (self.folder / job['id'] / 'job.json').write_text(json.dumps(job, indent=1), encoding='utf-8')
        return dict(reverted=reverted)
