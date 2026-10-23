"""Button sets: the game's own buttons restyled in one look, the whole menu (or every in-game button) at once.

A set is a material (a seamless texture drawn by tools/make_button_sets.py) and a few touches: an outline, a glow and
an "ink" for the light parts of two-tone buttons (the play button's triangle). Each button keeps its exact shape, drawn
from the player's own game, so every icon stays the icon it was and all of them match.

The game multiplies some white menu icons by its own colors (data/ui-tints.json: the teal back arrow, the pink arrows)
and that can't be changed without touching the game. Those icons get the set's look in light shades, so the game's
color still shows through with the set's shine and outline.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent
VERSION = 3   # bump when previews or buttons are drawn differently: kept previews are made again
TINT_COLORS = json.loads((ROOT / 'data' / 'ui-tints.json').read_text(encoding='utf-8'))['tints']   # name -> the game's color for it
TINTS = set(TINT_COLORS)
# The main menu's buttons (not the coin, the title or the store badges: they keep their own look) ...
MENU = ['icon-play', 'icon-menu-settings', 'icon-menu-profile', 'icon-friends', 'icon-menu-invite', 'icon-crown', 'icon-share',
        'icon-star', 'icon-rate', 'icon-music-on', 'icon-music-off', 'icon-add', 'button-x', 'back-icon', 'forward-icon',
        'icon-goto-next', 'icon-goto-previous', 'icon-quest', 'vip_icon', 'icon-mitosis', 'icon-flag', 'icon-edit', 'icon-expand',
        'icon-coin', 'popup-close', 'icon-remove', 'icon-sell']
# ... and what is pressed in a match: on phones the split and eject buttons and the joystick, on PC the buttons that
# also show their mouse and keyboard keys.
PHONE = ['button-split', 'button-eject', 'joystick', 'joystick-bg']
PC = ['button-explain-split', 'button-explain-eject']
HUD = ['power-container', 'icon-ingame-potion', 'icon-chat']   # on both: the potion slot, the potion and chat buttons
SETS = {'set:menu-buttons': ('All the main menu buttons', MENU), 'set:ingame-buttons': ('Every in-game button: phone and PC', PHONE + PC + HUD),
        'set:ingame-phone': ('In-game buttons on phones (split, eject, joystick)', PHONE + HUD), 'set:ingame-pc': ('In-game buttons on PC', PC + HUD)}
LIGHT = np.array([-0.45, -0.6, 0.66]) / np.linalg.norm([-0.45, -0.6, 0.66])   # from the top left, like the game's own art


def rgb(color):
    color = str(color or '').lstrip('#')
    return np.array([int(color[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255 if len(color) == 6 else None


def blur(a, radius):
    return np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(radius)), np.float32) / 255


def grow(a, px):
    """The shape made px pixels wider all around (its outline)."""
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), 'L')
    for _ in range(px): im = im.filter(ImageFilter.MaxFilter(3))
    return np.asarray(im, np.float32) / 255


def material(texture, size, period):
    """The texture tiled over size, one tile every `period` pixels: the same scale on every button of the set."""
    period = max(8, int(round(period)))
    tile = np.asarray(texture.convert('RGB').resize((period, period), Image.Resampling.LANCZOS), np.float32) / 255
    w, h = size
    return np.tile(tile, (h // period + 1, w // period + 1, 1))[:h, :w]


def style(image, look, texture, tinted=False):
    """One button in the set's look, the same size as it was, its shape kept."""
    from pictures import oklab
    image = image.convert('RGBA')
    w, h = image.size
    side = min(w, h)
    edge = max(1, round(side / 42)) if look.get('outline') else 0
    glow = side / 18 if look.get('glow') and not tinted else 0   # a tinted icon's glow would only turn the game's color
    # Room around it for the outline and the whole glow: cut by the frame, a glow looked like a square.
    scale = 1 - 2 * (edge + glow * 1.6) / side
    inner = image.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    canvas.paste(inner, ((w - inner.width) // 2, (h - inner.height) // 2))
    px = np.asarray(canvas, np.float32) / 255
    a, color = px[..., 3], px[..., :3]
    solid = a > 0.5
    if not solid.any(): return image
    lab = oklab(color)
    L, C = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2])
    # Two-tone buttons (a white triangle on the pink play button): the light parts take the set's ink.
    light = (L > 0.85) & (C < 0.12) & solid
    share = light.sum() / solid.sum()
    ink = light if 0.04 < share < 0.85 else np.zeros_like(light)
    body = solid & ~ink
    # The material follows the button's own shading: its dark outlines stay darker than its face.
    ref = float(np.median(L[body])) if body.any() else 1.0
    shade_of = np.clip(L / max(ref, 0.05), 0.35, 1.15)
    # Bevel: a height map from the shape, lit from the top left.
    radius = float(np.clip(side / 28, 1.5, 4.5))
    height = blur(a, radius)
    gy, gx = np.gradient(height)
    k = radius * 2.5
    n = np.stack([-gx * k, -gy * k, np.ones_like(gx)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    lit = n @ LIGHT
    bevel = float(look.get('bevel', 0.9))
    shade = 1 + bevel * (lit - LIGHT[2]) * 1.4
    spec = bevel * np.clip((lit - 0.93) / 0.07, 0, 1) ** 2 * 0.55
    tex = material(texture, (w, h), side * 1.15)
    out = tex * (shade * shade_of)[..., None] + spec[..., None]
    ink_rgb = rgb(look.get('ink')) if rgb(look.get('ink')) is not None else np.ones(3, np.float32)
    out = np.where(ink[..., None], ink_rgb * (1 + 0.5 * (shade[..., None] - 1)) + spec[..., None] * 0.6, out)
    out = np.clip(out, 0, 1)
    # Behind it: the outline, and the glow behind that.
    back = np.zeros((h, w, 4), np.float32)
    if glow:
        g = blur(grow(a, max(1, edge)), glow) * 0.9
        back[..., :3], back[..., 3] = rgb(look['glow']), g
    if edge:
        o = grow(a, edge)
        oc = rgb(look['outline'])
        back[..., :3] = back[..., :3] * (1 - o[..., None]) + oc * o[..., None]
        back[..., 3] = o + back[..., 3] * (1 - o)
    alpha = a + back[..., 3] * (1 - a)
    mixed = (out * a[..., None] + back[..., :3] * back[..., 3:] * (1 - a[..., None])) / np.maximum(alpha, 1e-4)[..., None]
    if tinted:   # the game multiplies it by its color: the look in light shades, its own texture and shine kept softly
        # Every look as light as the game's own icon (so its color reads the same), the material only as a soft texture
        # and shine on it, the outline dark around it.
        y = mixed @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        face = y[a > 0.5] if (a > 0.5).any() else y.ravel()
        lo, mid, hi = (float(v) for v in np.percentile(face, [10, 50, 90]))
        texture_hint = np.clip((y - mid) / max(hi - lo, 1e-3), -1, 1)
        gray = a * (0.8 + 0.2 * texture_hint + spec * 0.5) + (1 - a) * 0.35
        mixed = np.repeat(np.clip(gray, 0, 1)[..., None], 3, -1)
    result = np.dstack([mixed, alpha])
    return Image.fromarray(np.clip(result * 255 + 0.5, 0, 255).astype(np.uint8), 'RGBA')


# Symbols the image model can't be trusted to draw (the share sign came out as a row of dots or a dumbbell): a fully
# redrawn set shows the game's own symbol for them, in the set's material.
KEEP_GAME_SYMBOL = {'icon-share', 'icon-sell'}   # (and the SELL tag: the model is not asked to spell)
# Buttons whose symbol points somewhere: a redrawn one is turned to point the same way as the game's own.
DIRECTIONAL = {'icon-play', 'icon-goto-next', 'icon-goto-previous', 'back-icon', 'forward-icon', 'icon-expand'}


def shape(image, n=48):
    """An image's solid parts cut to their edges, centered in an n x n square (to compare shapes)."""
    a = np.asarray(image.convert('RGBA'))[..., 3] > 128
    ys, xs = np.nonzero(a)
    if not len(ys): return np.zeros((n, n), bool)
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = a.shape
    side = max(h, w)
    square = np.zeros((side, side), bool)
    square[(side - h) // 2:(side - h) // 2 + h, (side - w) // 2:(side - w) // 2 + w] = a
    return np.asarray(Image.fromarray(square.astype(np.uint8) * 255).resize((n, n))) > 127


def orient(art, game):
    """The drawing turned (a quarter turn at a time) to overlap the game's symbol best: the same direction."""
    d, g = shape(art), shape(game)
    turns = max(range(4), key=lambda k: (np.rot90(d, k) & g).sum() / max(1, (np.rot90(d, k) | g).sum()))
    return art.rotate(90 * turns, expand=True) if turns else art


INGAME_SYMBOL = 0.62   # an in-game button's symbol is bigger on its face: it is read at a glance in a match
GLYPH_INNER = {'button-split', 'button-eject', 'button-explain-split', 'button-explain-eject', 'icon-ingame-potion'}
RINGS = {'joystick-bg', 'power-container'}   # rings stay rings: the set's material on them
SYMBOL_TEXT = {'icon-coin': '$'}   # the coin on a set's button: its dollar sign


def text_symbol(text, size):
    """A symbol made of a character (the coin's $), white on clear, for a set's button."""
    from PIL import ImageDraw, ImageFont
    w, h = size
    try: font = ImageFont.truetype('arialbd.ttf', int(h * 0.8))
    except OSError: font = ImageFont.load_default()
    out = Image.new('RGBA', size, (0, 0, 0, 0))
    ImageDraw.Draw(out).text((w / 2, h / 2), text, font=font, fill='white', anchor='mm')
    return out


def button(name, picture, look, texture, base=None, drawn=None):
    """One of the game's buttons in a set's look, the same size: a fully redrawn set's own drawing of it (drawn), a whole
    new button when the set has its own drawn button (base), else the game's shape in the set's material."""
    tinted = name in TINTS
    if drawn is not None:
        art = drawn.convert('RGBA').copy()
        if name in DIRECTIONAL and not look.get('exact_symbols'): art = orient(art, glyph(picture))   # an arrow points the way the game's does
        art.thumbnail(picture.size, Image.Resampling.LANCZOS)
        out = Image.new('RGBA', picture.size, (0, 0, 0, 0))
        out.alpha_composite(art, ((picture.width - art.width) // 2, (picture.height - art.height) // 2))
        if tinted:   # drawn light already; kept gray so the game's color is the only color
            px = np.asarray(out, np.float32) / 255
            y = px[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            out = Image.fromarray((np.dstack([np.repeat(y[..., None], 3, -1), px[..., 3]]) * 255 + 0.5).astype(np.uint8), 'RGBA')
        return out
    narrow = not 0.7 <= picture.width / max(1, picture.height) <= 1.4   # a round button would shrink to a dot in it (the quests "!")
    if base is None or name in RINGS or narrow:
        return style(picture, look, texture, tinted=tinted)
    if name in SYMBOL_TEXT: picture = text_symbol(SYMBOL_TEXT[name], picture.size)
    if name == 'joystick': return compose(base, None, look, picture.size)
    inner = name in GLYPH_INNER
    return compose(base, picture, look, picture.size, tinted=tinted, symbol=INGAME_SYMBOL if inner else 0.5, inner=inner)


def sheet(buttons, look, texture, kind, small=False, base=None, drawn=None):
    """A preview of the set: its buttons (name -> the game's picture) laid out the way a player meets them."""
    cell = (64 if small else 112) if kind != 'ingame' else (84 if small else 190)   # in-game buttons are few and big
    if kind == 'ingame':
        names = [n for n in ('button-split', 'button-eject', 'joystick-bg', 'button-explain-split', 'button-explain-eject', 'icon-ingame-potion',
                             'icon-chat', 'power-container') if n in buttons]
        if small: names = names[:3]
    else:
        names = [n for n in MENU if n in buttons and n != 'icon-play']
        if small: names = sorted(names, key=lambda n: n in TINTS)[:6]   # the buttons in the set's own colors first
    if not names and 'icon-play' not in buttons:   # a game without these buttons: the material itself
        return texture.convert('RGB').resize((256, 256) if small else (512, 512)).convert('RGBA')
    big = 'icon-play' in buttons and kind != 'ingame'
    cols = 3 if small or kind == 'ingame' else 6
    rows = max(1, -(-len(names) // cols))
    left = cell * 2 if big else 0
    out = Image.new('RGBA', (left + cols * cell, max(rows, 2 if big else 1) * cell), (0, 0, 0, 0))
    if big:
        play = button('icon-play', buttons['icon-play'], look, texture, base, (drawn or {}).get('icon-play')).resize((cell * 2 - 8, cell * 2 - 8), Image.Resampling.LANCZOS)
        out.alpha_composite(play, (4, (out.height - play.height) // 2))
    for i, name in enumerate(names):
        art = button(name, buttons[name], look, texture, base, (drawn or {}).get(name))
        if name in TINTS:   # shown as the game will draw it: multiplied by its color
            px = np.asarray(art, np.float32)
            px[..., :3] *= rgb(TINT_COLORS[name])
            art = Image.fromarray(px.astype(np.uint8), 'RGBA')
        if name == 'joystick-bg' and 'joystick' in buttons:   # the stick in its ring, as on the phone
            stick = button('joystick', buttons['joystick'], look, texture, base).resize((art.width // 2, art.height // 2), Image.Resampling.LANCZOS)
            art.alpha_composite(stick, ((art.width - stick.width) // 2, (art.height - stick.height) // 2))
        art.thumbnail((cell - 8, cell - 8), Image.Resampling.LANCZOS)
        x, y = left + (i % cols) * cell, (i // cols) * cell
        out.alpha_composite(art, (x + (cell - art.width) // 2, y + (cell - art.height) // 2))
    return out


def glyph(image, inner=False):
    """A button's symbol from the game's own icon: its light parts on a two-tone button (the play button's triangle, the
    Battle Pass crown), else the whole icon (the settings gear), as a white shape cut to its edges. inner: an in-game
    button's symbol without the game's own ring around it."""
    from pictures import oklab
    px = np.asarray(image.convert('RGBA'), np.float32) / 255
    a = px[..., 3].copy()
    if inner:
        h, w = a.shape
        yy, xx = np.mgrid[0:h, 0:w]
        a[np.hypot(xx - w / 2, yy - h / 2) > min(w, h) * 0.39] = 0
    lab = oklab(px[..., :3])
    light = (lab[..., 0] > 0.88) & (np.hypot(lab[..., 1], lab[..., 2]) < 0.15) & (a > 0.5)
    solid = a > 0.5
    share = light.sum() / max(1, solid.sum())
    mask = np.where(light, a, 0) if 0.04 < share < 0.85 else a
    out = Image.fromarray(np.dstack([np.ones(a.shape + (3,), np.float32), mask]).__mul__(255).astype(np.uint8), 'RGBA')
    box = out.getchannel('A').getbbox()
    return out.crop(box) if box else out


def compose(base, icon, look, size, tinted=False, symbol=0.5, inner=False):
    """A whole new button: the set's own drawn button (base) with the game icon's symbol on its face, in the set's ink,
    beveled and outlined so it reads at a glance (no icon: the button alone, like the joystick's knob). size: the
    game's frame for it."""
    w, h = size
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    b = base.convert('RGBA').copy()
    b.thumbnail((w, h), Image.Resampling.LANCZOS)
    out.alpha_composite(b, ((w - b.width) // 2, (h - b.height) // 2))
    if icon is None: return out
    g = glyph(icon, inner)
    side = min(b.width, b.height) * symbol
    g.thumbnail((max(1, int(side)), max(1, int(side))), Image.Resampling.LANCZOS)
    pad = max(4, int(side * 0.12))
    canvas = Image.new('RGBA', (g.width + 2 * pad, g.height + 2 * pad), (0, 0, 0, 0))
    canvas.alpha_composite(g, (pad, pad))
    ink = Image.new('RGB', (8, 8), look.get('ink') or '#ffffff')
    sym = style(canvas, dict(outline=look.get('outline') or '#000000', bevel=0.6), ink)
    out.alpha_composite(sym, ((w - sym.width) // 2, (h - sym.height) // 2))
    if tinted:   # the game multiplies it by its color: the button in light shades
        px = np.asarray(out, np.float32) / 255
        y = px[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        a = px[..., 3]
        mid = float(np.median(y[a > 0.5])) if (a > 0.5).any() else 0.5
        gray = np.clip(0.55 + 0.45 * np.tanh((y - mid) * 4), 0.25, 1)
        out = Image.fromarray((np.dstack([np.repeat(gray[..., None], 3, -1), a]) * 255 + 0.5).astype(np.uint8), 'RGBA')
    return out


def classic_texture(virus):
    """A classic look's material from its virus: the virus's own glow unwrapped, its white middle at the top running out
    to its color at the bottom (past the nucleus), and around the virus from left to right, so letters and rings shade
    like the virus (a rainbow one in all its colors)."""
    px = np.asarray(virus.convert('RGBA'), np.float32)
    h, w = px.shape[:2]
    r = np.linspace(0.09, 0.4, 128)[:, None] * min(w, h)
    t = np.linspace(0, 2 * np.pi, 256, endpoint=False)[None, :]
    ys = np.clip((h / 2 + r * np.sin(t)).astype(int), 0, h - 1)
    xs = np.clip((w / 2 + r * np.cos(t)).astype(int), 0, w - 1)
    return Image.fromarray(np.clip(px[ys, xs, :3], 0, 255).astype(np.uint8), 'RGB')
