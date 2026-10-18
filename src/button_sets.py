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
TINT_COLORS = json.loads((ROOT / 'data' / 'ui-tints.json').read_text(encoding='utf-8'))['tints']   # name -> the game's color for it
TINTS = set(TINT_COLORS)
# The main menu's buttons (not the coin, the title or the store badges: they keep their own look) ...
MENU = ['icon-play', 'icon-menu-settings', 'icon-menu-profile', 'icon-friends', 'icon-menu-invite', 'icon-crown', 'icon-share',
        'icon-star', 'icon-rate', 'icon-music-on', 'icon-music-off', 'icon-add', 'button-x', 'back-icon', 'forward-icon',
        'icon-goto-next', 'icon-goto-previous', 'icon-quest', 'vip_icon', 'icon-mitosis', 'icon-flag', 'icon-edit', 'icon-expand']
# ... and what is pressed in a match: on phones the split and eject buttons and the joystick, on PC the buttons that
# also show their mouse and keyboard keys.
PHONE = ['button-split', 'button-eject', 'joystick', 'joystick-bg']
PC = ['button-explain-split', 'button-explain-eject']
SETS = {'set:menu-buttons': ('All the main menu buttons', MENU), 'set:ingame-buttons': ('Every in-game button: phone and PC', PHONE + PC),
        'set:ingame-phone': ('In-game buttons on phones (split, eject, joystick)', PHONE), 'set:ingame-pc': ('In-game buttons on PC', PC)}
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


def sheet(buttons, look, texture, kind, small=False):
    """A preview of the set: its buttons (name -> the game's picture) laid out the way a player meets them."""
    cell = (64 if small else 112) if kind != 'ingame' else (84 if small else 190)   # in-game buttons are few and big
    if kind == 'ingame':
        names = [n for n in ('button-split', 'button-eject', 'joystick-bg', 'button-explain-split', 'button-explain-eject') if n in buttons]
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
        play = style(buttons['icon-play'], look, texture).resize((cell * 2 - 8, cell * 2 - 8), Image.Resampling.LANCZOS)
        out.alpha_composite(play, (4, (out.height - play.height) // 2))
    for i, name in enumerate(names):
        art = style(buttons[name], look, texture, tinted=name in TINTS)
        if name in TINTS:   # shown as the game will draw it: multiplied by its color
            px = np.asarray(art, np.float32)
            px[..., :3] *= rgb(TINT_COLORS[name])
            art = Image.fromarray(px.astype(np.uint8), 'RGBA')
        if name == 'joystick-bg' and 'joystick' in buttons:   # the stick in its ring, as on the phone
            stick = style(buttons['joystick'], look, texture).resize((art.width // 2, art.height // 2), Image.Resampling.LANCZOS)
            art.alpha_composite(stick, ((art.width - stick.width) // 2, (art.height - stick.height) // 2))
        art.thumbnail((cell - 8, cell - 8), Image.Resampling.LANCZOS)
        x, y = left + (i % cols) * cell, (i // cols) * cell
        out.alpha_composite(art, (x + (cell - art.width) // 2, y + (cell - art.height) // 2))
    return out
