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
VERBS = (r'\b(?:add|put|draw|stick|wear(?:ing)?|redesign|re-?draw|restyle|reimagine|turn (?:it|the \w+(?: \w+)?) into|make (?:it|the \w+(?: \w+)?) look like|'
         # "make the food little tires", "make the coins into tiny donuts": new things, when the shader has no word for them
         r'make (?:it|them|the \w+(?: \w+)?) (?:(?:into|become|be) )?(?:(?:a|an|little|tiny|small|mini|big|giant|huge) )+\w+)\b')
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
# A whole-game redesign: the menu buttons (named on the game's screens: "Play button (Main menu)", "Back arrow (Menus)"), the
# in-game buttons ("Split button (in a match, phones)") and the phone joystick; the title and the coin are not among them.
WHOLE_GAME_BUTTONS = r'\((?:Main menu|Menus)\)$|\(in a match\b'
INGAME_CONTROLS = {'joystick', 'joystick-bg'}
SPAWNER_RED = '#e0262f'   # the game paints the spawner red in maps (so players call it the red virus)
# Store badges and sign-in buttons show other companies' logos: never redrawn with the rest of the game.
KEEP_AS_IS = {'icon-iphone', 'icon-android', 'icon-facebook', 'icon-gamecenter'}
ARENA_SUBJECT = 'the pale floor of the match arena, seen from above, repeating as a seamless tile'   # theme.py: ARENA_DIRECTION
SCENE_SUBJECT = 'a seamless repeating background picture for the game menu'   # theme.py words its art direction for scenery by this
# A theme restyles the game's pieces; it does not swap them for other things. Told to the art director for every piece
# a look is asked for ("make the game look like rainbows and unicorns"), so an arrow stays an arrow and a check box a
# check box, instead of each piece turning into a unicorn. Not for parts the request turns into something on purpose.
RESTYLE_NOTE = ('\nThis is a restyle of an existing game sprite, not a new picture: it must stay the same object with the same '
                'silhouette, pose and direction (an arrow stays an arrow pointing the same way, a check box stays a check box, a '
                'triangle stays a triangle, a chest stays a chest). Bring the look in through colors, materials, patterns, lighting '
                'and small accents on it. Never turn it into a character, an animal, a logo or a different object.')


def restyles(job):
    """A drawing that restyles a sprite rather than replacing it: not the title (its words have their own rules), a
    background (a new picture), the food (new things on purpose) or a part the request turns into something else."""
    if job.get('title') or job.get('scene') or job.get('food') or job.get('base'): return False
    request = own_words(job.get('prompt') or '')
    change = becomes(request)
    if change and re.fullmatch(WHOLE_PART, change[0]): return True   # "make the game look like rainbows": a look for everything
    return not (change or re.search(REDRAW, request.lower()) or re.search(NEW_ART, request.lower()))


# The whole game as "the part" ("make the game / it / everything look like..."): a look, not one part turned into another.
WHOLE_PART = r'(?:it|them|everything|all|the (?:whole |entire )?(?:game|menus?|ui|look|art))'


# Told to the art director when a reference board (inspire.pictures) goes with the drawing, so the ideas use the real thing.
PICTURES_NOTE = ('\nPictures of it go to the image model too: what the request asks for comes first, '
                 'styled with its real emblem or logo, signature colors and shapes (as fan art), not invented symbols.')
# The game's foods are different things worth different mass (the bigger, the more): smallest first.
# The game itself shows the more valuable foods bigger, so each drawing fills its square; worth shows in its detail.
FOOD_TIERS = (('least mass', 'the first of the three foods, worth the least mass: the simplest, plainest one'),
              ('more mass', 'the second of the three foods, worth more mass: a richer, more detailed one'),
              ('most mass', 'the third of the three foods, worth the most mass: the most impressive, valuable-looking one'))
FOOD_MAX = 40   # every food sprite the game uses fits in one set (about 19 today)


def theme_colors(prompt):
    """The exact colors a request names ("rainbow", "red and gold"), as hex: every piece of a theme is told the same ones."""
    import theme as shading   # imported here: theme imports this module
    return list(dict.fromkeys(shading.named_colors(own_words(prompt))))[:10]


def _hsv(rgb):
    """RGB in 0..1 (..., 3) -> hue, saturation, value, each (...)."""
    mx, mn = rgb.max(-1), rgb.min(-1)
    delta = mx - mn
    safe = np.where(delta > 1e-6, delta, 1.0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.where(mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)) / 6
    return np.where(delta > 1e-6, h, 0.0), np.where(mx > 1e-6, delta / np.maximum(mx, 1e-6), 0.0), mx


def _rgb(h, s, v):
    i = np.floor(h * 6) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    choose = lambda *parts: np.select([i == k for k in range(6)], parts)
    return np.stack([choose(v, q, p, p, t, v), choose(t, v, v, q, p, p), choose(p, p, t, v, v, q)], -1)


def harmonize(image, palette, strength=0.85):
    """Every color that is a shade of one of the palette's colors becomes that color (its hue, and most of its
    saturation), keeping its light and shade: the red of one drawing is the red of the next, a rainbow has the same seven
    bands everywhere. Colors far from all of them (a tire's black, a skin tone in a blue theme) are left as they are."""
    import colorsys
    chroma = [colorsys.rgb_to_hsv(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))) for c in palette]
    chroma = [c for c in chroma if c[1] >= 0.25 and c[2] >= 0.2]
    if not chroma: return image
    a = np.asarray(image.convert('RGBA'), np.float32) / 255
    h, s, v = _hsv(a[..., :3])
    ph, ps = np.array([c[0] for c in chroma], np.float32), np.array([c[1] for c in chroma], np.float32)
    gap = (h[..., None] - ph + 0.5) % 1 - 0.5                       # signed hue distance to each palette color
    near = np.abs(gap).argmin(-1)
    d = np.take_along_axis(gap, near[..., None], -1)[..., 0]
    # Fully that color within about 11 degrees, fading out by about 54; grays and near-grays keep their own.
    w = strength * np.clip(1 - (np.abs(d) - 0.03) / 0.12, 0, 1) * np.clip((s - 0.12) / 0.2, 0, 1)
    vivid = w * 0.6 * np.clip((s - 0.35) / 0.3, 0, 1)                # only strong colors take the palette's strength
    a[..., :3] = np.where(w[..., None] > 0, _rgb((h - w * d) % 1, np.clip(s + vivid * (ps[near] - s), 0, 1), v), a[..., :3])
    return Image.fromarray(np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8), 'RGBA')


def learn_colors(image, most=6):
    """The main colors of a finished piece (hue families that cover a fair share of it), as hex: the colors the rest
    of its theme is matched to when the request named none ("a bmw theme")."""
    a = np.asarray(image.convert('RGBA'), np.float32) / 255
    h, s, v = _hsv(a[..., :3])
    keep = (a[..., 3] > 0.5) & (s > 0.3) & (v > 0.25)
    if keep.sum() < 50: return []
    h, s, v = h[keep], s[keep], v[keep]
    bins = 24
    weight = np.bincount((h * bins).astype(int) % bins, weights=s, minlength=bins)
    smooth = weight + 0.5 * (np.roll(weight, 1) + np.roll(weight, -1))
    colors = []
    for b in np.argsort(-smooth):
        if len(colors) >= most or smooth[b] < 0.08 * smooth.sum(): break
        if smooth[b] < max(smooth[(b - 1) % bins], smooth[(b + 1) % bins]): continue   # peaks only
        near = np.abs((h - (b + 0.5) / bins + 0.5) % 1 - 0.5) <= 1.5 / bins
        if near.sum() < 20: continue
        angle = np.angle(np.exp(2j * np.pi * h[near]).mean()) / (2 * np.pi) % 1
        rgb = _rgb(np.float32(angle), np.median(s[near]), np.median(v[near]))
        colors.append('#' + ''.join(f'{int(round(float(c) * 255)):02x}' for c in rgb))
    return colors


def palette_key(prompt):
    """Pieces of one theme share their colors: the request's own words, ignoring case and punctuation."""
    return re.sub(r'\W+', ' ', own_words(prompt).lower()).strip()[:200]


# A new font (theme.font_request): Image AI draws what the letters are made of as a seamless tile, and the fonts tool fills
# the letters of a real typeface with it.
TEXTURE_SIZE = 512
TEXTURE_SUBJECT = 'a seamless texture that fills the letters of a game font'
TEXTURE_RULES = ('A seamless tileable close-up texture of that material or pattern, filling the whole square edge to edge and evenly '
                 'detailed everywhere, with no single object in the middle and no empty background. It fills the letters of player '
                 'names in a game, so use bold, clear detail, rich color and strong light and shade that read at a small size. '
                 'No text, no letters, no frame, no border, no vignette.')


def is_font(row):
    """A font sheet or one of its letters: restyled by the fonts tool (theme.font_request), never drawn as a picture."""
    return row.get('kind') == 'glyph' or row.get('group') == 'Fonts'


def is_arena_background(row):
    """The floor of a match (worldbg.png and its variants): near-white tiles the game itself darkens in dark mode, so one
    pale drawing serves light and dark mode alike. Drawn as one picture for all of them, like the menu backgrounds."""
    return row.get('group') == 'Backgrounds' and bool(re.fullmatch(r'worldbg\d*(?:_w)?\.png', str(row.get('name', '')), re.I))


def is_menu_background(row):
    """The main menu background of each game theme (bg2.png, bg7.png, bg9.png): drawn as one picture for all of them."""
    return row.get('group') == 'Backgrounds' and bool(re.fullmatch(r'bg\d+\.png', str(row.get('name', '')), re.I))


def is_food(row):
    """The pellets players eat: the shapes food1-3 and their small copies foods1-3, on the theme sheets. (The round
    blobs and dots also named food… are not what Mitosis shows, so they are not redrawn.)"""
    return row.get('kind') == 'sprite' and row.get('group') == 'Cells & gameplay' and bool(re.fullmatch(r'foods?\d+', str(row.get('name', ''))))
GAME_BG = (13, 40, 41)          # the menu's base color compiled into Mitosis.exe (#0D2829); bg2.png is an overlay on it
# The match floor: the cells and the food are drawn on top of it, and the game darkens it in dark mode. Food is the
# game's own colors on the drawing (see FOOD_TIERS), so the floor stays pale and quiet and never looks like food.
# Said as what to draw, never as what to avoid: an image model that reads "no food" tends to draw food.
ARENA_RULES = ('A brand new floor for the arena of a top-down cell game, seen from above, filling the whole image edge to edge so '
               'it repeats as a seamless tile. Players\' colorful pieces move on top of it, so it is a calm, empty floor: very pale, '
               'mostly near-white with soft, low-contrast light greys or very pale tints, made only of a few large soft shapes or a '
               'gentle smooth texture, with open space everywhere. Clean and simple, without any text, frame or border.')
ARENA_PALE = 0.55   # the Paler slider's start: how far an arena drawing is lifted toward white (the game darkens it for dark mode)
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


def paint_original(original, art):
    """The original sprite's own shape and shading, colored by the drawing behind it: each pixel takes the drawing's color
    there (softened, so thin lines get a clear color, not noise), lit by the original's own brightness."""
    from PIL import ImageFilter
    o = np.asarray(original, np.float32)
    # Colors spread from where the drawing has some into where it is see-through (a blur weighted by its alpha), so the
    # parts of the outline outside the drawing get its nearby colors, not black.
    rgba = art.convert('RGBA'); a = np.asarray(rgba, np.float32)[..., 3:] / 255
    weighted = Image.fromarray((np.asarray(rgba, np.float32)[..., :3] * a).astype(np.uint8))
    radius = max(2, min(art.size) / 12)
    num = np.asarray(weighted.filter(ImageFilter.GaussianBlur(radius)), np.float32)
    den = np.asarray(Image.fromarray((a[..., 0] * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius)), np.float32)[..., None] / 255
    mean = (np.asarray(rgba, np.float32)[..., :3] * a).sum((0, 1)) / max(1e-3, a.sum())
    colors = np.where(den > 0.05, num / np.maximum(den, 1e-3), mean)
    light = (o[..., :3].mean(-1, keepdims=True) / 255) * 0.45 + 0.55   # the original's highlights and shades, kept gently
    rgb = np.clip(colors * light + (255 - colors) * 0.12 * (light > 0.97), 0, 255)
    return Image.fromarray(np.dstack([rgb, o[..., 3]]).astype(np.uint8), 'RGBA')


def wrap_shift(img, dx=0.0, dy=0.0):
    """Move a tile by up to half its size (dx/dy -1..1), wrapping around: what leaves one edge comes in at the other."""
    a = np.asarray(img.convert('RGBA'))
    a = np.roll(np.roll(a, int(round(dy * a.shape[0] / 2)), axis=0), int(round(dx * a.shape[1] / 2)), axis=1)
    return Image.fromarray(a, 'RGBA')


def tile_resize(img, frame):
    """Resize a tile so it still tiles: resized among its own copies, so its edges are filtered with what comes next to
    them in the game, not with nothing."""
    w, h = img.size
    big = Image.new('RGBA', (w * 3, h * 3))
    for x in range(3):
        for y in range(3): big.paste(img, (x * w, y * h))
    big = big.resize((frame[0] * 3, frame[1] * 3), Image.Resampling.LANCZOS)
    return big.crop((frame[0], frame[1], frame[0] * 2, frame[1] * 2))


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


def own_words(prompt):
    """The player's request without the Wikipedia summary theme.inspired() adds after "Inspiration (…):". The summary is
    context for the look, never a request ("changed its name to Bayerische Motoren Werke" must not rename the title)."""
    return re.split(r'\s*\bInspiration \(', prompt, maxsplit=1)[0] or prompt


# How people say "this part becomes that": (the part, the new thing). The part is what gets redrawn; the new thing is
# only described, so "make the potion a beer bottle" redraws the potion, not the beer sprite.
_THE = r'(?:the|my|our|all|every|each|this|that|these|those|it|them)\b'
_SIZES = r'(?:a|an|some|little|tiny|small|mini|big|giant|huge|cute|two|three|four|five|few|lots of|many|several)'
BECOMES = (
    # "put sunglasses on the virus", "add some stars to the title", "draw tiny sharks as the food"
    rf'\b(?:put|add|draw|stick|place|paint|give)\s+(?P<new>.+?)\s+(?:on|onto|to|as|for|in|over|around)\s+(?P<subject>{_THE}.*)$',
    # "replace the food with tiny crowns", "swap the food for tires"
    r'\b(?:replace|swap|switch|exchange|trade)\s+(?P<subject>.+?)\s+(?:with|for|by)\s+(?P<new>.+)$',
    # "turn the coin into a donut", "change the arena background to grass", "redraw the crown as a tiara"
    r'\b(?:turn|change|convert|transform|morph|make|redraw|re-draw|redesign|remake|reimagine|restyle|draw|render)\s+(?P<subject>.+?)\s+'
    # ("as a tiara", not "the same shade as its neighbour")
    rf'(?:into|in to|to look like|look like|looks like|as(?=\s+{_SIZES}\b)|to)\s+(?P<new>.+)$',
    # "make the potion a beer bottle", "make the friends icon two little cats", "make the food little tires"
    rf'\bmake\s+(?P<subject>it|them|the\s+\w+(?:\s+\w+){{0,2}}?)\s+(?:(?:be|become)\s+)?(?P<new>(?:{_SIZES}\s+)+.+)$',
    # "the food should be little burgers", "i want the food to be stars", "the virus should be a creeper"
    r'^(?:(?:i want|i\'d like|id like|i would like|let)\s+)?(?P<subject>.+?)\s+(?:should|could|would|will|must|can|to)\s+(?:be|become|look like|turn into)\s+(?P<new>.+)$',
    # "can the food be tires"
    rf'\b(?:can|could|should|will|would)\s+(?P<subject>{_THE}.*?)\s+(?:be|become|look like)\s+(?P<new>.+)$',
    # "food = tiny tires", "the virus but as a donut"
    r'^(?P<subject>.+?)\s*(?:=|->|→|\bbut (?:as|like)\b)\s*(?P<new>.+)$',
    # "tires for the food please"
    rf'^(?P<new>.+?)\s+(?:for|as|on)\s+(?P<subject>{_THE}.+?)(?:\s+please)?$',
)


def becomes(prompt):
    """(the part, the new thing) when a request says one part becomes something else, else None."""
    low = ' '.join(str(prompt).lower().split()).strip(' .!?')
    for pattern in BECOMES:
        found = re.search(pattern, low)
        if found and found.group('subject').strip(' ,') and found.group('new').strip(' ,'):
            return found.group('subject').strip(' ,'), found.group('new').strip(' ,')
    return None


def subject_part(prompt):
    """The thing to redraw: "the coin" in "turn the coin into a diamond" (see BECOMES), else the whole request."""
    found = becomes(prompt)
    return found[0] if found else prompt


# Words in front of "icon" or "button" that do not pick one ("the main buttons", "the little icons").
VAGUE_PARTS = set('main menu game small little big tiny round square other same new old whole top bottom side'.split())


# Store and sign-in badges are someone else's logo: only redrawn when the request names them ("the app store badge").
BRANDS = r'\b(?:app ?store|apple|iphone|ios|google play|play store|android|facebook|game ?center|badges?)\b'


def key_for(prompt):
    """A background color the request is unlikely to paint the art with."""
    text = prompt.lower()
    if re.search(r'green|toxic|slime|zombie|lime|grass|leaf|leaves|forest|emerald|mint', text): return 'magenta'
    return 'green'


CANVAS_BUDGET = 768 * 768


def canvas_size(w, h):
    """A model canvas (multiples of 16, about 0.45 megapixels) with room around the art for drips, hats and glow."""
    aspect = max(0.4, min(4.0, w / max(1, h)))
    width = 1024 if aspect >= 1 else max(512, round(1024 * aspect / 16) * 16)
    height = max(384, min(1024, round(width / aspect * 1.75 / 16) * 16))
    # At most 768 x 768 worth of pixels: a square 1024 canvas (food, coins, icons) no longer fits beside the reference
    # pictures on an 8 GB card and draws about 5 times slower, for detail the game never shows (sprites are 64-256 px).
    if width * height > CANVAS_BUDGET:
        scale = (CANVAS_BUDGET / (width * height)) ** 0.5
        width, height = max(384, int(width * scale) // 16 * 16), max(384, int(height * scale) // 16 * 16)
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

    def menu_backgrounds(self):
        """bg2.png and the other game themes' menu backgrounds (one drawing for all of them)."""
        return [r for r in self.lab.catalog.rows.values() if is_menu_background(r) and r['editable'] and not r.get('removed') and not r.get('legacy')]

    def arena_backgrounds(self):
        """worldbg.png and its variants: the floor of every match (one drawing for all of them)."""
        return [r for r in self.lab.catalog.rows.values() if is_arena_background(r) and r['editable'] and not r.get('removed') and not r.get('legacy')]

    def whole_game_rows(self):
        """What a whole-game redesign redraws ("make the whole game look cool" with drawing): only what players care about,
        the backgrounds (menu and arena), the viruses, the menu buttons, the in-game buttons and the food, with all their
        copies. One drawing per sprite. The title, the coin, icons and the rest stay as they are (ask for them by name)."""
        rows = self.lab.catalog.rows
        sheet_of = lambda r: rows.get(r.get('parent', ''), {}).get('name', '')
        live = lambda r: r['editable'] and not r.get('legacy') and not r.get('removed') and not r.get('server_only')
        main = [r for r in rows.values() if live(r) and sheet_of(r) == 'theme.png']
        names = {r['name'] for r in main if re.search(WHOLE_GAME_BUTTONS, r.get('label') or '') or r['name'] in INGAME_CONTROLS}
        names = {n for n in names - KEEP_AS_IS - {'icon-coin'} if not n.startswith('logo')}   # not the title, the coin or store badges
        buttons = sorted((r for r in rows.values() if live(r) and r['name'] in names and sheet_of(r) in THEME_SHEETS),
                         # play first, then the rest of the menu, then what is pressed in a match
                         key=lambda r: (r['name'] != 'icon-play', bool(re.search(r'in a match', r.get('label') or '')) or r['name'] in INGAME_CONTROLS, r['name'], r['source']))
        viruses = [r for r in rows.values() if live(r) and r['name'] in VIRUSES]
        return self.menu_backgrounds() + self.arena_backgrounds() + viruses + buttons + self.food_rows()

    def food_kinds(self):
        """The game's different foods, each worth a different mass, smallest first: one list of sprites per food (its big
        picture and its small copy). The small copies are matched by shape: their numbers differ (foods1 is the small
        food3). The more of its square a food fills, the more it is worth (the molecule, the "<", then the X)."""
        if getattr(self, '_kinds', None) is None:
            rows = self.food_rows()
            big = [r for r in rows if re.fullmatch(r'food\d+', r['name'])]
            mask = lambda r: np.asarray(self.lab.catalog.image(r['key']).convert('RGBA').getchannel('A').resize((32, 32))) > 100
            masks = {r['key']: mask(r) for r in rows}
            kinds = {r['key']: [r] for r in big}
            for r in rows:
                if r['key'] in kinds or not big: continue
                twin = max(big, key=lambda b: (masks[b['key']] & masks[r['key']]).sum() / max(1, (masks[b['key']] | masks[r['key']]).sum()))
                kinds[twin['key']].append(r)
            order = sorted(big, key=lambda b: masks[b['key']].mean())
            self._kinds = [kinds[b['key']] for b in order] or ([rows] if rows else [])
        return self._kinds

    def food_kind(self, row):
        """(which food, how many foods) for a food sprite, by its name (every sheet's copy is the same food)."""
        for i, kind in enumerate(self.food_kinds()):
            if row.get('name') in {r['name'] for r in kind}: return i, len(self.food_kinds())
        return 0, 1

    def food_rows(self):
        """Every food sprite the game uses (pellet shapes, blobs and dots in each size), one per art: drawn as one set."""
        rows, seen = [], set()
        for r in sorted(self.lab.catalog.rows.values(), key=lambda r: (r['name'], r['source'])):
            if not is_food(r) or not r['editable'] or not r.get('primary', True) or r.get('legacy') or r.get('removed') or r.get('dup') in seen: continue
            seen.add(r.get('dup')); rows.append(r)
        return rows

    def targets(self, prompt, only=None):
        if only:
            rows = [r for r in (self.lab.row(k) for k in only if k in self.lab.catalog.rows) if r['editable'] and not is_font(r)]
            if any(is_arena_background(r) for r in rows):   # the floor of every match, light and dark mode: together
                rows += [r for r in self.arena_backgrounds() if r['key'] not in {x['key'] for x in rows}]
            if any(is_menu_background(r) for r in rows):
                # A menu background is one picture for every game theme: redraw them together.
                rows += [r for r in self.menu_backgrounds() if r['key'] not in {x['key'] for x in rows}]
            return rows[:FOOD_MAX] if rows and all(is_food(r) for r in rows) else rows[:8]   # room for a sprite and its identical copies (the coin has 7)
        subject = subject_part(prompt)
        if re.search(TITLE, subject.lower()): return self.title_rows()
        # "Full spooky reskin" is the whole game (offered as the title, the virus and the food), not the full-screen button.
        if re.search(r'\b(?:full|whole|entire|complete|total)\s+(?:\w+\s+)?(?:reskin|redesign|makeover|overhaul|look)\b|^\s*reskin\b', subject.lower()): return []
        names = {n for found in self.themes.named(subject)[0].values() for n in found}
        if not re.search(BRANDS, subject.lower()): names -= KEEP_AS_IS
        if not names:
            # "The leaderboard icon": one part this lab has no name for. Guessing would redraw other icons, so it is left
            # to the player to check it in the library (or right-click it) instead. "The icons" and "every button" are fine.
            named_one = re.search(r'\b(?:the|my|that|this)\s+(?P<which>[a-z][\w-]*)\s+(?:icon|button|badge|panel|sign|symbol|logo)\b', subject.lower())
            if named_one and named_one.group('which') not in VAGUE_PARTS: return []
            rows = self.family_rows(subject)
            # "Turn the food into crowns": every pellet the game shows, so none keeps the old look.
            return self.food_rows() if rows and any(is_food(r) for r in rows) and all(r['name'].startswith('food') for r in rows) else rows
        rows = [r for r in self.lab.catalog.rows.values() if r['name'] in names and r['editable'] and r.get('primary', True) and not r.get('legacy') and not is_font(r)]
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
            if not r['editable'] or not r.get('primary', True) or r.get('legacy') or r.get('removed') or r.get('dup') in seen or is_font(r): continue
            if r['width'] < 24 or r['height'] < 24 or self.lab.catalog.children.get(r['key']): continue   # whole sheets and specks are not drawn
            if shading.families_of(r, self.lab.catalog.rows.get(r.get('parent', ''), {}).get('name', '')) in wanted:
                seen.add(r.get('dup')); rows.append(r)
        # Parts known by name (found on the game's screens) first, then sprites the game names in its code.
        rows.sort(key=lambda r: (not r.get('label'), not r.get('live'), r['name']))
        return rows[:limit]

    # ----- generating ---------------------------------------------------------------------------
    def start(self, prompt, keys=None, count=None, base=None, direction=None, inspiration=None):
        """base: (job id, variant index) to refine a variant instead of the current art. count: variants to draw
        (Settings > Theme AI & drawing > Drawings per request when not given)."""
        prompt = str(prompt or '').strip()[:800]
        if count is None: count = labsettings.value(self.lab, 'drawings')
        if not prompt: raise ValueError('Describe the design you want.')
        request = own_words(prompt)
        lacking = self.status()['missing']
        if lacking: raise ValueError('The image model is not installed: ' + ', '.join(lacking))
        if base:
            parent = self.job(base[0])
            rows = [self.lab.row(k) for k in parent['targets']]
        else:
            rows = self.targets(request, keys)
        if not rows: raise ValueError('Name what to redesign, or check it in the library first.')
        # One drawing per sprite: different sprites are drawn one at a time (the title's sizes share one drawing, and so do
        # copies that share a name you gave and a size, such as the coin the game also downloads).
        # Food is the exception: one drawing for every pellet shape and size, the way a player swaps them all for crowns.
        first = rows[0]
        food, menu = is_food(first), is_menu_background(first)   # and one drawing for the menu background of every game theme
        # A food is drawn once for its big picture and its small copy; the other foods are different things (more mass).
        tier = self.food_kind(first) if food else None
        arena = is_arena_background(first)
        rows = [r for r in rows if (food and is_food(r) and self.food_kind(r) == tier) or (menu and is_menu_background(r)) or (arena and is_arena_background(r)) or r['name'] == first['name'] or (first.get('label') and r.get('label') == first['label'] and (r['width'], r['height']) == (first['width'], first['height']))]
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
                       circular=parent.get('circular', False) if base else rows[0]['group'] in SCENE_GROUPS,   # drawn wrapping around its edges (--circular)
                       food=food, **({'food_tier': list(tier)} if food else {}), **({'arena': True} if arena else {}))
            if direction: job['direction'] = str(direction)[:700]   # shared by a set drawn together, so they match
            # A reference board from the inspiration lookup (inspire.pictures): its file in workspace/inspiration.
            if inspiration and re.fullmatch(r'[0-9a-f]{20}\.png', str(inspiration)) and (self.lab.root / 'inspiration' / inspiration).is_file():
                job['inspiration'] = str(inspiration)
            if job['title'] and wording(request): job['text'] = wording(request)
            if restyles(job): job['restyle'] = True   # a theme on the game's own sprite: its outline is kept (render)
            # One theme, one set of colors: the ones the request names, or else those of the first piece applied (render).
            job['palette_key'] = parent.get('palette_key', '') if base else palette_key(prompt)
            colors = parent.get('colors', []) if base else theme_colors(request)
            if colors: job['colors'] = colors
            else:
                learned = self.palettes().get(job['palette_key'])
                if learned and learned.get('colors'): job['learned'] = learned['colors']
            self.jobs[job['id']] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return dict(job)

    def start_texture(self, prompt, count=2):
        """A new font: a seamless texture its letters are filled with ("a font made of lava", "letters of candy"), drawn
        from the words alone as a tile. The letters keep a real font's shapes (fonts.py), so names stay readable."""
        prompt = re.sub(r'\s+', ' ', str(prompt or '')).strip()[:400]
        if not prompt: raise ValueError('Say what the letters are made of.')
        lacking = self.status()['missing']
        if lacking: raise ValueError('The image model is not installed: ' + ', '.join(lacking))
        with self.lock:
            if any(j['state'] == 'running' for j in self.jobs.values()): raise ValueError('A design is already being drawn.')
            job = dict(id=uuid.uuid4().hex, state='running', prompt=prompt, targets=[], names=['Letter texture'], frames=[[TEXTURE_SIZE, TEXTURE_SIZE]],
                       variants=[], message='Preparing…', progress=0, total=max(1, min(4, int(count))), key='green', base=None, at=time.time(),
                       title=False, stretched=False, tinted=False, tint='', spawner=False, scene=True, circular=True, texture=True, food=False, palette_key='')
            colors = theme_colors(prompt)
            if colors: job['colors'] = colors
            self.jobs[job['id']] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return dict(job)

    @staticmethod
    def subject(job):
        if job.get('texture'): return TEXTURE_SUBJECT
        if job['title']: return 'the game title logo that reads MITOSIS (the O is a round dot)'
        if job.get('food'):
            which, of = job.get('food_tier') or (0, 1)
            what = 'a food that players eat in a top-down cell game, seen from above'
            return f'{what}: {FOOD_TIERS[min(2, round(which * 2 / max(1, of - 1)))][1]}' if of > 1 else what
        if job.get('arena'): return ARENA_SUBJECT
        if job.get('scene'): return SCENE_SUBJECT
        # "Play button (Main menu)" -> "play button"; "icon-crown" -> "icon crown".
        name = re.sub(r'[-_]+', ' ', re.sub(r'\.png$|\s*\([^)]*\)\s*$', '', job['names'][0])).strip()
        return f'a game sprite: {name}'

    def instruction(self, job):
        text = self._instruction(job)
        if job.get('colors'):   # the same exact colors in every piece of the theme ("rainbow" is always these seven)
            text += ' Use exactly these colors: ' + ', '.join(job['colors']) + '.'
        return text

    # ----- one set of colors per theme -------------------------------------------------------------------------------
    def palettes(self):
        """palette key -> dict(colors, job): the colors of each theme whose request named none, from its first applied piece."""
        if getattr(self, '_palettes', None) is None:
            if getattr(self, 'folder', None) is None: return {}   # no designs folder (a bare Designer): nothing learned yet
            try: self._palettes = json.loads((self.folder / 'palettes.json').read_text(encoding='utf-8'))
            except (OSError, ValueError): self._palettes = {}
            if not isinstance(self._palettes, dict): self._palettes = {}
        return self._palettes

    def theme_palette(self, job):
        """The colors this piece is matched to: the ones its request names, or the ones learned from another piece."""
        if job.get('colors'): return job['colors']
        learned = self.palettes().get(job.get('palette_key', ''))
        return learned['colors'] if learned and learned.get('job') != job['id'] and learned.get('colors') else []

    def learn_palette(self, job, image):
        """The first piece applied of a theme that named no colors sets them for the rest of it."""
        key = job.get('palette_key', '')
        if not key or job.get('colors') or key in self.palettes(): return
        colors = learn_colors(image)
        if not colors: return
        self.palettes()[key] = dict(colors=colors, job=job['id'], at=time.time())
        if len(self._palettes) > 200:   # the newest themes only
            for old in sorted(self._palettes, key=lambda k: self._palettes[k].get('at', 0))[:-200]: self._palettes.pop(old)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / 'palettes.json').write_text(json.dumps(self._palettes), encoding='utf-8')
        for other in list(self.jobs.values()):
            if other.get('palette_key') == key and other['id'] != job['id']: other['learned'] = colors

    def _instruction(self, job):
        if job.get('texture'):
            return (f'{job.get("direction") or job["prompt"]}. {TEXTURE_RULES}')
        if job.get('arena'):
            return (f'{job.get("direction") or job["prompt"]}. {ARENA_RULES}'
                    + (' Take the look from the reference picture: its colors and patterns, kept very pale.' if job.get('inspiration') else ''))
        if job.get('scene'):
            return (f'{job.get("direction") or job["prompt"]}. A brand new full-frame background picture for a 2D game menu, '
                    'filling the whole image edge to edge so it can repeat as a seamless tile. It is shown small and repeated behind '
                    'buttons and text, so use a few large, bold, simple shapes instead of many tiny details, and moderate contrast so '
                    'what is on top stays readable. Use the colors the request names; if it names none, rich colors that are not too '
                    'bright. No text, no frame, no border, no single big object in the middle. Clean, high quality game art.'
                    + (' Take the look from the reference picture: its emblem, colors, patterns and style, repeated as a background motif.' if job.get('inspiration') else ''))
        what = self.subject(job)
        if job.get('text'): keep = f'Change the word MITOSIS so it reads exactly "{job["text"]}", in the same lettering style, on one line, centered.'
        elif job['title']: keep = 'Keep the exact word MITOSIS, spelled the same, in the same layout and size, centered.'
        elif job.get('food'):
            keep = ('Draw it as one simple object that fills most of the square, centered; its shape may change completely. '
                    'Give it a bold, solid silhouette with a clear dark outline and no fine detail, so it stands out on the arena floor, '
                    'light or dark, and is never mistaken for part of the background.')
            if (job.get('food_tier') or (0, 1))[1] > 1:
                keep += (' The game has three foods, each worth a different mass, so this one must look clearly different from the other two '
                         'and fit its worth: plainer for less mass, richer and more impressive for more, still filling most of the square.')
        elif restyles(job):
            keep = ('Restyle it, do not replace it: it must still clearly be the same thing as in the first reference picture, with the '
                    'same outline, pose and direction, size and position, centered. The look comes in through its colors, materials, '
                    'patterns and small accents only.')
        else: keep = 'Keep its outline, size and position, centered.'
        small = '' if job['title'] or max(job['frames'][0]) > 160 else ' It is shown small in the game, so use bold, simple shapes and strong contrast that read at a glance.'
        if job.get('inspiration'):   # the reference board (inspire.pictures): emblems and photos of what the theme is about
            small += (' Style it after the second reference picture: its emblem, colors and finish, as fan art'
                      + (' (the round O of MITOSIS can become its emblem).' if job['title'] else
                         '. Draw what is asked above, not a copy of the reference picture.'))
        color = {'green': 'pure green (#00FF00)', 'magenta': 'pure magenta (#FF00FF)', 'blue': 'pure blue (#0040FF)'}[job['key']]
        return (f'{job.get("direction") or job["prompt"]}. Edit {what}. {keep}{small} Crisp, high quality game UI art. '
                f'The background must stay a perfectly flat solid {color} with nothing else on it: no scenery, no shadow or floor under the art, no frame, no extra text.'
                + ('' if job['title'] or job.get('text') else ' Do not write any words or letters on it.'))

    def _run(self, job):
        folder = self.folder / job['id']
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if job['base']:
                # Refining: the chosen variant, exactly as drawn (same canvas and background), is the new starting point.
                ref = Image.open(self.folder / job['base'][0] / f'raw-{int(job["base"][1])}.png').convert('RGB')
                size = ref.size
            elif job.get('texture'):   # a font's texture: a square tile, from the words alone
                size, ref = (TEXTURE_SIZE, TEXTURE_SIZE), None
            elif job.get('scene'):
                # A new background is drawn from the words alone, at about the model's 0.6 megapixels, in the art's shape.
                source = self.lab.image(job['targets'][0])
                aspect = max(0.5, min(2.0, source.width / max(1, source.height)))
                size = (768, round(768 / aspect / 16) * 16) if aspect >= 1 else (round(768 * aspect / 16) * 16, 768)
                ref = None
            else:
                # A theme restyles the game's own art: the drawing starts from the original sprite, never from an earlier
                # drawing (redrawing a piece that had become a unicorn must give back the arrow, not another unicorn).
                # Other requests build on what is there now ("make the crown I drew bigger").
                source = self.lab.catalog.image(job['targets'][0]) if restyles(job) else self.lab.image(job['targets'][0])
                size = canvas_size(source.width, source.height)
                ref = reference(source, job['key'], size)
            if ref is not None: ref.save(folder / 'reference.png')
            runs = []   # (art direction, variants drawn with it)
            brief = job['prompt'] + (PICTURES_NOTE if job.get('inspiration') else '') + (RESTYLE_NOTE if restyles(job) else '')
            if not job['base'] and not job.get('direction') and job['total'] > 1 and getattr(self, 'concepts', None):
                # Several variants: a different idea for each (mummy wraps / chains and a graveyard...), not near-copies.
                job['message'] = 'Coming up with ideas…'
                try: ideas = [i.strip()[:700] for i in (self.concepts(brief, self.subject(job), job['total']) or []) if i.strip()]
                except Exception: ideas = []
                if len(ideas) >= 2:
                    job['directions'] = ideas; job['direction'] = ideas[0]   # "More variants" keeps the first idea's style
                    runs = [(idea, 1) for idea in ideas]
            if not runs and not job['base'] and not job.get('direction') and getattr(self, 'director', None):
                # A short request ("spooky") becomes a concrete art direction the image model can follow.
                job['message'] = 'Writing the art direction…'
                try: job['direction'] = (self.director(brief, self.subject(job)) or '').strip()[:700]
                except Exception: job['direction'] = ''
            if not runs: runs = [(job.get('direction') or '', job['total'])]
            # Settings › Accelerate: everything on the graphics card, every core, high priority (the PC is slow meanwhile).
            fast = bool(labsettings.value(self.lab, 'accelerate'))
            # The planner and the image model do not both fit on an 8 GB card (Settings: Free the graphics card before drawing).
            if fast or labsettings.value(self.lab, 'free_gpu'): runtime.OLLAMA.unload()
            job['message'] = 'Loading the image model…'
            steps = labsettings.STEPS[labsettings.value(self.lab, 'drawing_quality')]   # Settings: Drawing quality
            log, made, failed = [], 0, 0
            for number, (direction, count) in enumerate(runs):
                if job.get('cancelled'): raise RuntimeError('Cancelled.')
                one = len(runs) > 1
                out = folder / (f'raw-{made}.png' if one else 'raw-%d.png')
                args = [str(runtime.path('sd')), '--diffusion-model', str(runtime.path('diffusion')), '--vae', str(runtime.path('vae')), '--llm', str(runtime.path('llm')),
                        # Weights wait in RAM and visit the graphics card one part at a time, so 8 GB is enough. The picture
                        # encoder and decoder (small, but slow on the processor: about 3 minutes of every drawing) run on the
                        # graphics card, with flash attention: the same picture, about ten times sooner. The text encoder stays
                        # on the processor unless Accelerate is on.
                        '--offload-to-cpu', '--flash-attn', 'on', *([] if fast else ['--backend', 'te=cpu']),
                        '--threads', str(os.cpu_count() or 4) if fast else str(max(1, min(6, (os.cpu_count() or 2) // 2))),
                        *(['-r', str(folder / 'reference.png')] if (folder / 'reference.png').is_file() else []),
                        *(['-r', str(self.lab.root / 'inspiration' / job['inspiration'])] if job.get('inspiration') else []),   # the look, as pictures
                        # A background is drawn as a true tile: the model wraps its picture around the edges while it draws
                        # (circular padding), so the copies the game lays side by side meet with no seam, at no extra cost.
                        *(['--circular'] if job.get('circular') else []),
                        '-p', self.instruction(dict(job, direction=direction)),
                        '--cfg-scale', '1.0', '--steps', str(steps), '--sampling-method', 'euler', '-W', str(size[0]), '-H', str(size[1]),
                        '-s', str((int(time.time()) + number * 7919) % 100000), '-b', str(count), '-o', str(out)]
                # Cancel can arrive while preparing the command. Publish the child atomically with that check.
                with self.lock:
                    if job.get('cancelled'): raise RuntimeError('Cancelled.')
                    proc = subprocess.Popen(args, cwd=str(runtime.path('sd').parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            creationflags=runtime.QUIET | getattr(subprocess, 'HIGH_PRIORITY_CLASS' if fast else 'BELOW_NORMAL_PRIORITY_CLASS', 0),
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
                    (Image.open(raw).convert('RGBA') if job.get('circular') else seamless(Image.open(raw))).save(folder / f'variant-{index}.png')
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
        # A restyle keeps the sprite's outline by default (the drawing is trimmed to it): the shape is the game's, the look is new.
        s = {'scale': 1.0, 'dx': 0.0, 'dy': 0.0, 'tolerance': 1.0, 'shape': 1.0 if job.get('stretched') or job.get('restyle') else 0.0, 'fill': 1.0 if job['title'] else 0.0, 'white': 0.0, **(settings or {})}
        if job.get('scene'):
            # A background fills its frame. Zoom picks a part of the drawing (the game shows the tile small and repeated, so
            # zooming in keeps shapes big enough to read), which is then made to tile again; Calmer softens and darkens the
            # picture in its own colors (never toward the game's teal, which players want gone), so the menu's text stays
            # readable on top of it. The result is opaque, so none of that teal shows through.
            s = {'scale': 1.0 if job.get('circular') else SCENE_ZOOM, 'calm': SCENE_CALM, 'dx': 0.0, 'dy': 0.0, 'white': 0.0, **(settings or {})}
            frame = tuple(job['frames'][int(target)])
            raw = Image.open(folder / f'raw-{int(index)}.png').convert('RGBA')
            if job.get('circular') and float(s['scale']) <= 1.0:
                # A true tile, shown whole: moving it wraps around, and it is shrunk as a tile, so it still meets itself.
                out = tile_resize(wrap_shift(raw, float(s['dx']), float(s['dy'])), frame)
            else:   # zoomed in (or drawn before tiles were): a crop, blended so its edges meet again
                out = seamless(cover(raw, frame, float(s['scale']), float(s['dx']), float(s['dy']))).resize(frame, Image.Resampling.LANCZOS)
            out = self.matched(job, out, s)
            if job.get('arena'):
                pale = max(0.0, min(0.95, float(s.get('pale', ARENA_PALE))))
                a = np.asarray(out, np.float32)[..., :3]
                a = 255 - (255 - a) * (1 - pale)   # toward white, same colors: food and cells keep standing out on it
                out = Image.fromarray(np.dstack([np.clip(a, 0, 255), np.full(a.shape[:2], 255, np.float32)]).astype(np.uint8), 'RGBA')
                return out
            calm = max(0.0, min(0.9, float(s['calm'])))
            if calm:
                a = np.asarray(out, np.float32)[..., :3]
                average = a.reshape(-1, 3).mean(axis=0)
                a = (a * (1 - calm * 0.4) + average * calm * 0.4) * (1 - calm * 0.45)   # less contrast, then darker, same colors
                out = Image.fromarray(np.dstack([np.clip(a, 0, 255), np.full(a.shape[:2], 255, np.float32)]).astype(np.uint8), 'RGBA')
            return whiten(out) if float(s['white']) >= 0.5 else out
        art = unkey(Image.open(folder / f'raw-{int(index)}.png'), job['key'], float(s['tolerance'])) if float(s['tolerance']) != 1.0 else \
            Image.open(folder / f'variant-{int(index)}.png').convert('RGBA')
        out = self.matched(job, self._place(job, art, s, int(target)), s)
        if max(out.size) <= 96: out = crisp(out)                   # a big drawing shrunk into a small frame goes soft
        return whiten(out) if float(s['white']) >= 0.5 else out   # last, so the brightest pixel of the final art is white

    def matched(self, job, image, s):
        """"Same colors as the theme" (on unless unticked): the piece in its theme's exact colors (theme_palette)."""
        colors = self.theme_palette(job) if float(s.get('match', 1)) >= 0.5 else []
        return harmonize(image, colors) if colors else image

    def _place(self, job, art, s, target):
        frame = tuple(job['frames'][int(target)])
        if float(s.get('paint', 0)) >= 0.5:
            # Paint the original: the game's sprite exactly (outline and alpha), in the drawing's colors. For thin outline
            # icons (an empty box, a ring) that a drawing does not hold on to.
            original = self.lab.catalog.image(job['targets'][int(target)]).convert('RGBA')
            if original.size == frame: return paint_original(original, fit(art, frame, float(s['scale']), float(s['dx']), float(s['dy']), fill=True))
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
        if job.get('texture'): raise ValueError('This is a texture for letters: pick it in the font card instead.')
        items, first = [], None
        with self.lab.lock:
            for target, key in enumerate(job['targets']):
                before = self.lab.revision(key)
                image = self.render(job_id, index, target, settings)
                first = first or image
                self.lab.commit(key, image, dict(design=job['id'], prompt=job['prompt'], variant=int(index), settings=settings or {}), persist=False)
                items.append(dict(key=key, before=before, after=self.lab.revision(key)))
            self.lab.persist()
        if first is not None and float((settings or {}).get('match', 1)) >= 0.5:
            try: self.learn_palette(job, first)
            except (OSError, ValueError): pass
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
