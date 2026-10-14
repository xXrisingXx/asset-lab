"""Theme the whole game from a sentence.

A local Ollama model turns the request into a small plan: which art families to touch and
how to shade them. The plan is applied to whole sprite sheets, always from the art as it was
before any theme, so a follow-up request replaces the previous theme instead of stacking on it.
Without Ollama a keyword parser produces the same kind of plan for simple requests.
"""
import base64
import fnmatch
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
import numpy as np
from PIL import Image, ImageFilter
import design
import exe_palette
import runtime

OLLAMA = 'http://127.0.0.1:11434'
PREFERRED = ('qwen3:latest', 'lab-qwen3-4b:latest', 'gamedecomp-qwen:latest', 'qwen2.5-coder:7b', 'llama3.1:latest', 'llama3.2:latest')
# Per-model settings measured on this PC's 8 GB card; unknown models use Ollama's defaults.
MODEL_OPTIONS = {'gamedecomp-qwen:latest': {'num_gpu': 27}}

FAMILIES = {
    'buttons': 'Button skins in every state, tabs, checkboxes, arrows and close buttons.',
    'panels': 'Panels, popups, list rows, headers, tooltips, bars, sliders and containers.',
    'icons': 'Menu and in-game icons (mostly white glyphs) and badges.',
    'logo': 'The MITOSIS logo and its seasonal variants.',
    'menu_background': 'The faint pattern drawn over the main menu: black pixels at 2-15% opacity. Use color_mode "fill" to recolor it and opacity 2-4 to strengthen it; opacity 0 hides it.',
    'world_background': 'The in-game arena floor tiles and grid (very light grey by default).',
    'cells': 'Cell bodies, borders, viruses, balls and other things on the arena.',
    'food': 'Food pellets.',
    'equipment': 'Equipment and item artwork.',
    'potions': 'Potion, vial and alchemy artwork.',
    'effects': 'Glows, particles, lightning and heal effects.',
    'fonts': 'Bitmap font glyphs (white text; use color_mode "colorize" to tint it).',
    'flags': 'Country flags. Left alone unless named.',
    'downloaded_skins': 'Player skins downloaded from the server. Left alone unless named.',
    'other': 'Images that fit no other family.',
}
QUIET = {'flags', 'downloaded_skins'}  # never part of "everything"
BUTTON = re.compile(r'button|btn|ar-bt|tab-|check-|radio-|toggle|arrow|popup-close|callout|picker-|page-indicator|back-icon|forward-icon')
MODES = ('none', 'tint', 'colorize', 'fill')
LIMITS = dict(color_amount=(0, 1, 1), hue_shift=(-180, 180, 0), saturation=(0, 3, 1), brightness=(0.1, 3, 1), contrast=(0.1, 3, 1), opacity=(0, 4, 1),
              gradient_angle=(-360, 360, 0), gradient_repeat=(0.25, 12, 1), glow_size=(1, 48, 8), glow_strength=(0, 4, 1.2),
              outline_width=(0, 8, 0), rim_width=(0, 12, 0), gloss=(0, 1, 0), flare=(0, 1, 0), posterize=(0, 8, 0), pixelate=(0, 32, 0), scanlines=(0, 1, 0), grain=(0, 0.5, 0))
# Named ramps the model (or the keyword planner) can ask for instead of listing colors.
GRADIENTS = {
    'rainbow': ['#ff2d55', '#ff9500', '#ffe600', '#34d058', '#00c7ff', '#5e5ce6', '#d83bff', '#ff2d55'],
    'sunset': ['#2b1055', '#7b2ff7', '#f107a3', '#ff6a3d', '#ffd166'], 'synthwave': ['#12003a', '#6a00f4', '#ff2ec4', '#ff9f1c', '#fff275'],
    'fire': ['#3b0000', '#c1121f', '#ff5a1f', '#ffb703', '#fff3b0'], 'ocean': ['#03045e', '#0077b6', '#00b4d8', '#90e0ef', '#e0fbfc'],
    'galaxy': ['#0b0033', '#3a0ca3', '#7209b7', '#f72585', '#4cc9f0'], 'toxic': ['#0b1f00', '#2b9348', '#aaff00', '#eeff41'],
    'ice': ['#0a2a66', '#3a86ff', '#9bf6ff', '#ffffff'], 'aurora': ['#001d3d', '#00f5d4', '#7bf1a8', '#9b5de5', '#f15bb5'],
    'candy': ['#ff70a6', '#ff9770', '#ffd670', '#e9ff70', '#70d6ff'], 'lava': ['#1a0000', '#7f0000', '#ff3c00', '#ffb300'],
    'gold': ['#5a3a00', '#c98b00', '#ffd54a', '#fff6c2', '#e0a100'], 'chrome': ['#1c1f26', '#8d99ae', '#f8f9fa', '#6c757d', '#e9ecef'],
    'pastel': ['#ffc6ff', '#bdb2ff', '#a0c4ff', '#9bf6ff', '#caffbf', '#fdffb6'],
    'holographic': ['#ffb3ec', '#c9b6ff', '#a8d8ff', '#a9fff1', '#f3ffb0', '#ffd0a8', '#ffb3ec'],  # soap-bubble sheen
    # Seasonal and mood palettes, so "spooky", "xmas" or "royal" can be shaded without the image model.
    'halloween': ['#0b0710', '#3d1a5c', '#ff6a00', '#ffb347', '#b6ff3b'], 'christmas': ['#0b3d1a', '#1a7f37', '#f1faee', '#d62828', '#ffd166'],
    'winter': ['#0b2545', '#13315c', '#8ecae6', '#e0fbfc', '#ffffff'], 'forest': ['#132a13', '#31572c', '#4f772d', '#90a955', '#ecf39e'],
    'royal': ['#240046', '#5a189a', '#9d4edd', '#ffd60a', '#fff3b0'], 'cyberpunk': ['#0d0221', '#ff00a0', '#00f0ff', '#fcee0c'],
    'blood': ['#120000', '#5c0000', '#a4161a', '#e5383b', '#ffccd5'], 'midnight': ['#020617', '#0b1d3a', '#3a0ca3', '#7b8cde']}
# Everyday words for those palettes.
MOOD_WORDS = {'halloween': r'spooky|creepy|scary|horror|haunted|halloween|pumpkins?', 'christmas': r'christmas|xmas|festive|santa',
              'winter': r'winter|wintery|snowy|frozen|frosty|icy', 'forest': r'forest|nature|natural|jungle|woodland|earthy',
              'royal': r'royal|regal|king(?:ly)?|queen|luxury|luxurious', 'cyberpunk': r'cyberpunk|cyber|futuristic|sci-?fi|hacker',
              'blood': r'bloody|blood|gory|vampire', 'midnight': r'midnight|nocturnal|night sky|starry'}
# Words in a request that never name a sprite, even though some sprite's name contains them.
STOP = set('''make give want need theme style look looks like more less with without into onto from that this them they their have has some something
    side keep leave alone only every each whole game mitosis main menu screen color colour colors colours gradient outline inside glow flare neon rainbow
    strong subtle soft dark darker light lighter bright brighter proper nice clean white black grey gray thing things part parts effect effects please
    also then over under around back front when where what which very really just still again same different normal default original
    turn change changed replace swap switch show hide remove delete bigger smaller larger flash blink'''.split())
# Words every name for a control has ("Play button", "New Players mode icon"): too common to pick out one sprite.
GENERIC = {'button', 'buttons', 'icon', 'icons', 'arrow', 'arrows', 'badge', 'badges', 'mode', 'menu', 'main', 'list', 'screen', 'players', 'player'}
GRADIENT_TYPES = ('linear', 'radial', 'conic', 'luminance')
SCHEMA = {'type': 'object', 'required': ['reply', 'operations'], 'properties': {
    'reply': {'type': 'string'},
    'ui_colors': {'type': 'object', 'properties': {}},  # filled in below, one property per game color
    'hide': {'type': 'array', 'items': {'type': 'string'}},
    'operations': {'type': 'array', 'items': {'type': 'object', 'required': ['targets'], 'properties': {
        'targets': {'type': 'array', 'items': {'type': 'string', 'enum': ['everything', *FAMILIES]}},
        'match': {'type': 'array', 'items': {'type': 'string'}},
        'exclude': {'type': 'array', 'items': {'type': 'string'}},
        'color': {'type': 'string'}, 'color_mode': {'type': 'string', 'enum': list(MODES)}, 'color_amount': {'type': 'number'},
        'gradient': {'type': 'array', 'items': {'type': 'string'}}, 'gradient_preset': {'type': 'string', 'enum': ['', *GRADIENTS]},
        'gradient_type': {'type': 'string', 'enum': list(GRADIENT_TYPES)}, 'gradient_angle': {'type': 'number'}, 'gradient_repeat': {'type': 'number'},
        'glow_color': {'type': 'string'}, 'glow_size': {'type': 'number'}, 'glow_strength': {'type': 'number'}, 'glow_inside': {'type': 'boolean'},
        'outline_color': {'type': 'string'}, 'outline_width': {'type': 'number'}, 'rim_color': {'type': 'string'}, 'rim_width': {'type': 'number'},
        'rim_gradient': {'type': 'array', 'items': {'type': 'string'}}, 'rim_preset': {'type': 'string', 'enum': ['', *GRADIENTS]}, 'flare': {'type': 'number'},
        'gloss': {'type': 'number'}, 'posterize': {'type': 'number'}, 'pixelate': {'type': 'number'}, 'scanlines': {'type': 'number'}, 'grain': {'type': 'number'},
        'hue_shift': {'type': 'number'}, 'saturation': {'type': 'number'}, 'brightness': {'type': 'number'},
        'contrast': {'type': 'number'}, 'opacity': {'type': 'number'}, 'invert': {'type': 'boolean'}}}}}}
SPATIAL = ('gradient', 'glow_color', 'outline_color', 'rim_color', 'gloss', 'flare', 'pixelate')  # effects that need each sprite's own frame

COLORS = {'red': '#e53935', 'crimson': '#c2183a', 'scarlet': '#ff2400', 'orange': '#ff8a1e', 'amber': '#ffb300', 'gold': '#f5c542', 'yellow': '#ffe53b',
          'lime': '#a6e22e', 'green': '#2ecc71', 'emerald': '#10b981', 'mint': '#7ee8c1', 'teal': '#14b8a6', 'cyan': '#22d3ee', 'aqua': '#22d3ee',
          'turquoise': '#2dd4bf', 'sky': '#38bdf8', 'blue': '#3b82f6', 'navy': '#1e3a8a', 'indigo': '#5b5bd6', 'purple': '#9b5de5', 'violet': '#8b5cf6',
          'lavender': '#b9a7f5', 'magenta': '#e935c1', 'pink': '#ff6fb5', 'rose': '#f43f5e', 'brown': '#8d5a3b', 'white': '#ffffff', 'silver': '#c0c7d1',
          'grey': '#8a94a3', 'gray': '#8a94a3', 'black': '#101216'}
WORDS = {
    'buttons': r'buttons?|btns?|tabs?|checkbox(?:es)?', 'panels': r'panels?|popups?|windows?|menus?|dialogs?|lists?|headers?|ui|interface|hud',
    'icons': r'icons?|badges?', 'logo': r'logos?|title', 'menu_background': r'(?:main |menu |home )(?:screen )?(?:background|bg|backdrop|pattern)|menu overlay',
    'world_background': r'(?:world|game|arena|map|in-?game)\s*(?:background|bg|floor|grid)|grid|arena|floor', 'cells': r'cells?|blobs?',
    'food': r'food|pellets?', 'equipment': r'equipment|items?|gear|weapons?|armou?r', 'potions': r'potions?|vials?|alchemy', 'effects': r'effects?|particles?',
    'fonts': r'fonts?|text|letters?', 'flags': r'flags?', 'downloaded_skins': r'skins?', 'everything': r'everything|all of it|whole (?:game|theme)|entire|theme|the game'}
MOODS = [
    (r'gr[ae]yscale|black and white|monochrome|desaturate[d]?', dict(saturation=0)),
    (r'neon|vibrant|vivid|saturated|punchy', dict(saturation=1.6, contrast=1.12)),
    (r'pastel|soft|muted|washed', dict(saturation=0.55, brightness=1.12)),
    (r'much darker|very dark|way darker', dict(brightness=0.5)), (r'darker|dark(?:en)?|dim(?:mer)?', dict(brightness=0.7)),
    (r'much (?:brighter|lighter)', dict(brightness=1.5)), (r'brighter|lighter|light(?:en)?', dict(brightness=1.25)),
    (r'high contrast|more contrast|contrasty', dict(contrast=1.35)), (r'low contrast|less contrast|flat(?:ter)?', dict(contrast=0.75)),
    (r'invert(?:ed)?|negative', dict(invert=True)), (r'hide|remove|invisible|get rid of', dict(opacity=0)),
    (r'more transparent|see-?through|fainter|subtle[r]?|less visible', dict(opacity=0.5)),
    (r'less transparent|more (?:visible|opaque|solid)|stronger|bolder', dict(opacity=2.2)),
    (r'warm(?:er)?', dict(hue_shift=-12, saturation=1.1)), (r'cool(?:er)?|cold(?:er)?', dict(hue_shift=14, saturation=1.05))]


def families_of(row, sheet=''):
    name, group = row['name'].lower(), row['group']
    if group == 'Downloaded skins': return 'downloaded_skins'
    if group in ('Fonts', 'Equipment', 'Effects'): return group.lower()
    if group == 'Potions & alchemy': return 'potions'
    if group == 'Buttons & panels': return 'buttons' if BUTTON.search(name) else 'panels'
    if group == 'Backgrounds': return 'menu_background' if re.fullmatch(r'bg\d+\.png', name) else 'world_background'
    if group == 'Icons & interface':
        if 'logo' in name: return 'logo'
        return 'flags' if sheet.lower() == 'flags.png' or name == 'flags.png' else 'icons'
    if group == 'Cells & gameplay': return 'food' if name.startswith('food') else 'cells'
    return 'other'


def clean_plan(raw):
    """Clamp and normalize whatever the model or parser produced into a safe plan."""
    if not isinstance(raw, dict): raise ValueError('The theme plan was not an object.')
    operations = []
    for item in (raw.get('operations') or [])[:16]:
        if not isinstance(item, dict): continue
        targets = [t for t in item.get('targets') or [] if isinstance(t, str) and (t == 'everything' or t in FAMILIES)]
        op = dict(targets=['everything'] if 'everything' in targets or not targets else list(dict.fromkeys(targets)))
        for field in ('match', 'exclude'):
            op[field] = [str(v)[:80] for v in (item.get(field) or []) if isinstance(v, str) and v.strip()][:12]
        color = hex_color(item.get('color'))
        raw_gradient = item.get('gradient')
        if isinstance(raw_gradient, str): raw_gradient = GRADIENTS.get(raw_gradient.strip().lower(), [])
        gradient = [c for c in (hex_color(v) for v in (raw_gradient if isinstance(raw_gradient, list) else [])[:10]) if c]
        if len(gradient) < 2: gradient = list(GRADIENTS.get(str(item.get('gradient_preset') or '').strip().lower(), []))
        kind = item.get('gradient_type') if item.get('gradient_type') in GRADIENT_TYPES else 'linear'
        rim = str(item.get('rim_color') or '').strip().lower()
        rim = 'gradient' if rim in ('gradient', 'rainbow', 'auto') else hex_color(rim)
        # The edge can have its own ramp, so "rainbow outline, dark inside" needs no second operation.
        raw_rim = item.get('rim_gradient')
        if isinstance(raw_rim, str): raw_rim = GRADIENTS.get(raw_rim.strip().lower(), [])
        rim_gradient = [c for c in (hex_color(v) for v in (raw_rim if isinstance(raw_rim, list) else [])[:10]) if c]
        if len(rim_gradient) < 2: rim_gradient = list(GRADIENTS.get(str(item.get('rim_preset') or '').strip().lower(), []))
        if rim_gradient: rim = 'gradient'
        shared = rim == 'gradient' and not rim_gradient  # the rim borrows the operation's main gradient
        if shared and not gradient: gradient = list(GRADIENTS['rainbow'])
        # Around a ring or a frame, colors that travel along the edge read as one continuous neon tube.
        if shared and item.get('gradient_type') not in GRADIENT_TYPES: kind = 'conic'
        # A gradient paints white art too unless the plan says otherwise; a flat color defaults to a gentle tint.
        mode = item.get('color_mode') if item.get('color_mode') in MODES and item.get('color_mode') != 'none' else ('colorize' if gradient else 'tint' if color else 'none')
        # A gradient meant for the rim leaves the art inside alone unless the plan asks to paint it too.
        if shared and item.get('color_mode') in (None, '', 'none'): mode = 'none'
        if not color and not gradient: mode = 'none'
        op.update(rim_color=rim, rim_gradient=rim_gradient)
        if gradient: color = ''
        op.update(color=color, color_mode=mode, gradient=gradient, gradient_type=kind if gradient else 'linear')
        glow = str(item.get('glow_color') or '').strip().lower()
        op['glow_color'] = 'auto' if glow in ('auto', 'self', 'same', 'match') else hex_color(glow)
        op['glow_inside'] = item.get('glow_inside') is True
        op['outline_color'] = hex_color(item.get('outline_color'))
        for field, (low, high, default) in LIMITS.items():
            value = item.get(field, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value: value = default
            op[field] = round(float(min(high, max(low, value))), 3)
        if mode != 'none' and 'color_amount' not in item: op['color_amount'] = 1.0
        for whole in ('outline_width', 'rim_width', 'posterize', 'pixelate', 'glow_size'): op[whole] = float(round(op[whole]))
        if op['rim_color'] and not op['rim_width']: op['rim_width'] = 3.0
        if not op['rim_color']: op['rim_width'] = 0.0
        if op['posterize'] == 1: op['posterize'] = 2.0
        if op['pixelate'] == 1: op['pixelate'] = 0.0
        if op['outline_color'] and not op['outline_width']: op['outline_width'] = 2.0
        if not op['outline_color']: op['outline_width'] = 0.0
        if not op['glow_color'] or not op['glow_strength']: op.update(glow_color='', glow_inside=False)
        op['invert'] = item.get('invert') is True
        if changes(op): operations.append(op)
    reply = raw.get('reply') if isinstance(raw.get('reply'), str) else ''
    colors = raw.get('ui_colors') if isinstance(raw.get('ui_colors'), dict) else {}
    ui = {name: hex_color(colors.get(name)) for name in UI_COLORS if hex_color(colors.get(name))}
    hide = [g for g in dict.fromkeys(raw.get('hide') or []) if isinstance(g, str) and g in HIDE] if isinstance(raw.get('hide'), list) else []
    return dict(reply=reply.strip()[:600], operations=operations, ui_colors=ui, hide=hide)


# Colors the game sets in its code (Mitosis.exe), by plan name -> palette field, its original (teal palette)
# value and what it paints. Verified in docs/NATIVE-UI-COLOR-INDEX.md; one value replaces every palette variant.
UI_COLORS = {
    'accent': ('ACTIVE_ITEM_COLOR', '#E52C58', 'the pink of the menu arrows and other active controls'),
    'background': ('BG_COLOR', '#0D2829', 'the solid dark-mode menu and game background behind the pattern'),
    'light_background': ('BG_COLOR_LIGHT', '#FCFCFC', 'the background in light mode (night mode off)'),
    'menu_buttons': ('BUTTON_CLEAR_COLOR', '#40BDC4', 'the bright teal of the main menu buttons, text and white icon together (profile, ranks, achievements, friends, quit)'),
    'button_border': ('ROUNDED_BUTTON_BORDER_COLOR', '#40BDC4', 'the outline of rounded buttons'),
    'selected': ('CHOOSEN_ITEM_BG', '#40BDC4', 'the background of the chosen item in lists and pickers'),
    'expanded': ('MULTIBUTTON_EXPANDED_COLOR', '#E5365F', 'the pink of a circular button group while it is opened out'),
    'text': ('NORMAL_TEXT_COLOR', '#247276', 'ordinary menu text (a darker teal)'),
    'dark_text': ('DARKER_TEXT_COLOR', '#194C4D', 'dimmer, secondary menu text'),
    'header': ('HEADER_COLOR', '#113638', 'the header bars of panels'),
    'bottom_bars': ('BOTTOM_BARS_COLOR', '#061515', 'the bars along the bottom of menus'),
    'lines': ('LINE_DARK_COLOR', '#0A2022', 'thin divider lines'),
    'chat_background': ('MESSENGER_BG_COLOR', '#030B0B', 'the chat / messenger background'),
    'loader': ('LOADER_COLOR', '#2E8F94', 'the loading spinner'),
    'ingame_text': ('INGAME_TEXT_COLOR', '#859798', 'text during a match'),
    'ingame_dark_text': ('INGAME_DARK_TEXT_COLOR', '#113537', 'dark text during a match and in light mode'),
    'ingame_border': ('INGAME_BORDER_COLOR', '#566F70', 'borders during a match'),
    'leaderboard_self': ('LEADERBOARD_SELF_COLOR', '#DDFB83', 'your own row on the leaderboard'),
    'guild': ('GUILD_BACKGROUND_COLOR', '#18A3E8', 'the guild (clan) background'),
    'guild_dark': ('DARK_GUILD_BACKGROUND_COLOR', '#091E1F', 'the darker guild background'),
}
# Phrases that name one game color in a request, most specific first ("light mode background" before "background").
UI_PHRASES = [
    ('expanded', r'\b(?:expanded|opened|circular|circle) (?:buttons?|group|menu)|\bmulti.?buttons?'),
    ('light_background', r'\blight[- ]?mode|\blight (?:background|bg)\b|\bday ?mode'),
    ('chat_background', r'\b(?:chat|messenger|messages?)\b'),
    ('guild_dark', r'\bdark(?:er)? (?:guild|clan)'),
    ('guild', r'\b(?:guild|clan)s?\b'),
    ('leaderboard_self', r'\b(?:leaderboard|scoreboard)\b|\bmy (?:row|name|score)\b'),
    ('ingame_border', r'\bin-?game (?:borders?|outlines?)'),
    ('ingame_dark_text', r'\bin-?game dark text'),
    ('ingame_text', r'\bin-?game text|\bhud text|\bmatch text'),
    ('loader', r'\bloader|\bloading|\bspinner'),
    ('button_border', r'\bbutton (?:borders?|outlines?|edges?|rims?)|\brounded buttons?'),
    ('menu_buttons', r'\bmenu (?:buttons?|icons?)|\bbutton (?:text|labels?|icons?)|\bthe teal\b|\bmain buttons?'),
    ('selected', r'\b(?:selected|chosen|selection|picked) (?:items?|backgrounds?|rows?|entry|entries)|\bselection colou?r'),
    ('header', r'\bheader (?:bars?|colou?r)|\btitle bars?'),
    ('bottom_bars', r'\bbottom bars?|\bfooter'),
    ('lines', r'\b(?:divider|separator)s?(?: lines?)?\b'),
    ('dark_text', r'\b(?:dark(?:er)?|secondary|dim(?:mer)?) (?:menu )?text'),
    ('text', r'\b(?:menu|ui|normal|main) text\b|\btext colou?r'),
    ('accent', r'\b(?:accent|arrows?|highlights?|active)\b|\bthe pink\b'),
]
# Words that keep a model's game color in the plan (tame): the request has to be about that color.
UI_ALL = r'game colou?rs?|ui colou?rs?|code colou?rs?|whole (?:ui|menu)|entire (?:ui|menu)'
UI_WORDS = {name: pattern for name, pattern in UI_PHRASES}
UI_WORDS['accent'] = r'accent|arrows?|pink|highlight|active|ui colou?r|game colou?rs?'
UI_WORDS['background'] = r'background colou?r|backdrop|menu colou?r|behind the pattern|base colou?r|background (?:to|into|be|should)|(?:make|turn|change|paint|set) the (?:main )?(?:menu )?background|dark[- ]?mode'
# Main-menu controls the game can be told to leave out (Mitosis.exe branches; see exe_palette.HIDE).
HIDE = {'mobile_store_badges': ('the App Store and Google Play badges', r'app ?store|google play|play store|mobile|phone|store badges?|iphone|android'),
        'discord': ('the Discord button', r'discord'),
        'top_banner': ('the top banner with the Mitosis OG tag', r'top banner|og\b|mitosis og|og banner|banner at the top'),
        'left_banner_1': ('the first left promo (Worm.is / Strike.is)', r'left (?:banners?|promos?)|worm\.?is|strike\.?is|other games|promo games'),
        'left_banner_2': ('the second left promo (Worm.is / Strike.is)', r'left (?:banners?|promos?)|worm\.?is|strike\.?is|other games|promo games')}
HIDE_ALL = r'promos?|promotions?|ads|adverts?|advertising|all (?:the |of the )?banners|link buttons|links|social buttons|clutter'
HIDE_VERB = r'\b(?:hide|remove|get rid of|disable|delete|turn off|take out|take away|without|no)\b'


def palette_for(plan):
    return {UI_COLORS[name][0]: value.upper() for name, value in (plan.get('ui_colors') or {}).items()}


def hide_targets(text):
    named = [group for group, (_, words) in HIDE.items() if re.search(rf'\b(?:{words})', text)]
    return named or (list(HIDE) if re.search(rf'\b(?:{HIDE_ALL})\b', text) else [])


def hide_in(text):
    """Menu controls a request asks to leave out: "hide the discord button", "remove all the promos"."""
    return hide_targets(text) if re.search(HIDE_VERB, text) else []


SIZE_WORDS = r'\b(?:bigger|smaller|larger|tinier|resize|enlarge|shrink|move|reposition|relocate)\b'
SIZE_NOTE = ('Sizes and positions are set by the game itself (each sprite has a fixed box in its sheet), '
             'so I can change how things look but not how big they are or where they sit.')
MOTION_WORDS = (r'\b(?:flash(?:es|ing)?|blink(?:s|ing)?|animat(?:e|ed|es|ing|ion)|spin(?:s|ning)?|rotat(?:e|es|ing)|bounc(?:e|es|ing)|'
                r'wiggl(?:e|es|ing)|shak(?:e|es|ing)|puls(?:e|es|ing|ate)|flicker(?:s|ing)?)\b')
MOTION_NOTE = 'Sprites are still pictures, so I can change how they look but not make them flash, spin or move.'
# How the art route words each control: the bar behind the OG tag is drawn by the game, so only the tag goes.
ART_LABELS = {'top_banner': 'the OG tag (the “Mitos.is” bar stays)', 'left_banner_1': 'the Worm.is and Strike.is promos',
              'left_banner_2': 'the Worm.is and Strike.is promos'}


def listing(items):
    items = list(dict.fromkeys(items))
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def art_hide(plan, present, text=''):
    """With Mitosis.exe off limits, hide menu controls by making the art they are drawn from fully transparent.

    present: sprite names in the library; text: the request, so "the app store badge" leaves Google Play alone (the
    game hides the two badges together, but each is its own picture). Returns the plan with the hiding as an
    operation, and a note.
    """
    groups = plan.get('hide') or []
    if not groups: return plan, ''
    text = text.lower()
    apple, google = re.search(r'app ?store|iphone|apple|\bios\b', text), re.search(r'google|android|play ?store', text)
    names, done, left = [], [], []
    for group in groups:
        mine = [name for name in exe_palette.HIDE_SPRITES.get(group, []) if name in present]
        label = ART_LABELS.get(group, HIDE[group][0])
        if group == 'mobile_store_badges' and bool(apple) != bool(google):
            mine = [name for name in mine if name == ('icon-iphone' if apple else 'icon-android')]
            label = 'the App Store badge' if apple else 'the Google Play badge'
        (done if mine else left).append(label)
        names += mine
    ops = clean_plan(dict(operations=[dict(targets=['everything'], match=list(dict.fromkeys(names)), opacity=0)]))['operations'] if names else []
    notes = []
    if done:
        text = listing(done)
        notes.append(f'{text[0].upper() + text[1:]} will be hidden by making the art fully transparent (the empty spot can still be clicked).')
    if left:
        one = len(dict.fromkeys(left)) == 1 and not listing(left).endswith('promos')
        notes.append(f"{listing(left)[0].upper() + listing(left)[1:]} {'is' if one else 'are'} not drawn from art Asset Lab can change, so {'it stays' if one else 'they stay'}.")
    return dict(plan, operations=plan['operations'] + ops, hide=[]), ' '.join(notes)


SCHEMA['properties']['ui_colors']['properties'] = {name: {'type': 'string'} for name in UI_COLORS}
SCHEMA['properties']['hide']['items']['enum'] = list(HIDE)


RISKY = {'invert': r'invert|negative|opposite|flip the colou?rs', 'pixelate': r'pixel|8-?bit|retro|blocky|chunky|mosaic|arcade|minecraft',
         'posterize': r'poster|cartoon|cel.?shad|comic|toon|flat colou?r', 'scanlines': r'scan.?line|crt|vhs|\btv\b|retro|arcade|glitch',
         'grain': r'grain|nois[ey]|gritty|film|dust|rough|vintage|glitch|static'}


def tame(plan, said):
    """Small models tend to switch on every field a schema offers. Effects that wreck art are kept only
    when the conversation asked for something like them, and exact repeats of an operation are dropped."""
    said, kept, seen = said.lower(), [], set()
    wants_glow = re.search(r'glow|neon|lumin|radiat|halo|light up|lit up', said)
    wants_gloss = re.search(r'gloss|shin[ey]|glass|polish|chrome|metal|wet|candy|bubble|jelly|crystal', said)
    for op in plan['operations']:
        op = dict(op, **{field: (False if field == 'invert' else 0.0) for field, words in RISKY.items() if op[field] and not re.search(words, said)})
        if op['gloss'] and not wants_gloss: op['gloss'] = 0.0
        if op['flare'] and not re.search(r'flare|sparkl|glint|highlight|shin[ey]|star|gleam|twinkl', said): op['flare'] = 0.0
        if op['rim_color'] and op['color_mode'] == 'none' and not re.search(r'inside|interior|inner|fill|body|cent(?:er|re)|middle|within|dark|bright|light', said):
            # A rim-only operation promises to leave the art inside alone, so stray tone tweaks are dropped.
            op.update(hue_shift=0.0, saturation=1.0, brightness=1.0, contrast=1.0, gloss=0.0, opacity=1.0)
        # The user asked for glow: every rim or recolor that could carry one gets it, even if the model forgot.
        painted = op['rim_color'] or (op['color_mode'] in ('tint', 'colorize') and op['targets'] != ['menu_background'])
        if wants_glow and painted and not op['glow_color']: op.update(glow_color='auto', glow_size=12.0, glow_strength=1.6)
        if op['glow_color'] and re.search(rf'\b(?:{QUIET_WORDS})\s+(?:\w+\s+)?glow', said): op.update(glow_size=min(op['glow_size'], 8.0), glow_strength=min(op['glow_strength'], 0.9))
        fingerprint = json.dumps({k: v for k, v in op.items() if k not in ('targets', 'match', 'exclude')}, sort_keys=True)
        # The same treatment again on a single family, after "everything" already applied it, only costs time.
        if changes(op) and not ((fingerprint, 'everything') in seen and not op['match'] and op['targets'] != ['everything']):
            kept.append(op)
            seen.add((fingerprint, 'everything' if op['targets'] == ['everything'] and not op['match'] and not op['exclude'] else 'part'))
    # Game colors and hidden menu controls change Mitosis.exe, so they are kept only when the request is about them.
    ui = {name: value for name, value in (plan.get('ui_colors') or {}).items() if re.search(f'{UI_WORDS[name]}|{UI_ALL}', said)}
    asked = hide_in(said)
    return dict(plan, operations=kept[:10], ui_colors=ui, hide=[g for g in plan.get('hide') or [] if g in asked])


def hex_color(value):
    value = COLORS.get(value.strip().lower(), value.strip()) if isinstance(value, str) else ''
    return '#' + value.lstrip('#').lower() if re.fullmatch(r'#?[0-9a-fA-F]{6}', value) else ''


def rgb_of(color): return np.array([int(color[i:i+2], 16) for i in (1, 3, 5)], dtype=np.float32) / 255.0


def changes(op):
    return bool((op['color_mode'] != 'none' and op['color_amount'] > 0) or op['hue_shift'] or op['invert']
                or any(op[k] != 1 for k in ('saturation', 'brightness', 'contrast', 'opacity'))
                or op['glow_color'] or op['outline_color'] or op['rim_color'] or any(op[k] for k in ('gloss', 'flare', 'posterize', 'pixelate', 'scanlines', 'grain')))


def spatial(op): return any(op.get(field) for field in SPATIAL)


def describe(op):
    if op['opacity'] == 0: return 'hidden (made fully transparent)'
    parts = []
    verb = {'tint': 'recolor', 'colorize': 'colorize', 'fill': 'fill', 'none': ''}[op['color_mode']]
    strength = '' if op['color_amount'] == 1 else f" at {round(op['color_amount']*100)}%"
    preset = next((name for name, stops in GRADIENTS.items() if stops == op['gradient']), f"{len(op['gradient'])}-color")
    if op['gradient'] and op['color_mode'] != 'none':
        shape = {'linear': f"{op['gradient_angle']:g}°", 'radial': 'radial', 'conic': 'around the center', 'luminance': 'mapped by brightness'}[op['gradient_type']]
        parts.append(f"{preset} gradient ({shape}{', ×' + format(op['gradient_repeat'], 'g') if op['gradient_repeat'] != 1 else ''}, {verb}){strength}")
    elif op['color_mode'] != 'none': parts.append(f"{verb} {op['color']}{strength}")
    edge = next((name for name, stops in GRADIENTS.items() if stops == op['rim_gradient']), f"{len(op['rim_gradient'])}-color") if op['rim_gradient'] else preset
    if op['rim_color']: parts.append(f"neon outline {edge if op['rim_color'] == 'gradient' else op['rim_color']} {op['rim_width']:g}px")
    if op['hue_shift']: parts.append(f"hue {op['hue_shift']:+g}°")
    for field, label in (('saturation', 'saturation'), ('brightness', 'brightness'), ('contrast', 'contrast'), ('opacity', 'opacity')):
        if op[field] != 1: parts.append(f'{label} ×{op[field]:g}')
    if op['invert']: parts.append('invert')
    if op['glow_color']: parts.append(f"{'inner + outer' if op['glow_inside'] else 'outer'} glow {op['glow_color']} {op['glow_size']:g}px ×{op['glow_strength']:g}")
    if op['outline_color']: parts.append(f"outline {op['outline_color']} {op['outline_width']:g}px")
    for field, label in (('gloss', 'gloss'), ('flare', 'flare'), ('posterize', 'posterize'), ('pixelate', 'pixelate'), ('scanlines', 'scanlines'), ('grain', 'grain')):
        if op[field]: parts.append(f'{label} {op[field]:g}')
    return ', '.join(parts)


def ramp(stops, t):
    """Color for each position t in 0..1 along evenly spaced stops."""
    colors = np.stack([rgb_of(c) for c in stops])
    at = np.linspace(0, 1, len(stops))
    return np.stack([np.interp(t, at, colors[:, channel]) for channel in range(3)], axis=-1).astype(np.float32)


def gradient_field(op, rgb):
    height, width = rgb.shape[:2]
    stops = op['gradient']
    if op['gradient_type'] == 'luminance': t = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    else:
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
        u, v = (xs + 0.5) / width - 0.5, (ys + 0.5) / height - 0.5
        if op['gradient_type'] == 'conic':
            # Sweep around the center; closing the loop on the first stop leaves no seam.
            sweep = (np.arctan2(v, u) / (2 * np.pi) + op['gradient_angle'] / 360.0) * op['gradient_repeat']
            return ramp(stops if stops[0] == stops[-1] else [*stops, stops[0]], sweep % 1.0)
        if op['gradient_type'] == 'radial': t = np.sqrt(u * u + v * v) / 0.7071
        else:
            angle = np.deg2rad(op['gradient_angle'])
            dx, dy = np.cos(angle), np.sin(angle)
            t = (u * dx + v * dy) / (abs(dx) + abs(dy)) + 0.5  # 0 and 1 land exactly on opposite edges
    t = np.clip(t, 0, 1) * op['gradient_repeat']
    return ramp(stops, np.where(t >= op['gradient_repeat'], 1.0, t % 1.0) if op['gradient_repeat'] != 1 else t)


def blur(channel, radius):
    """Gaussian blur of a float array in 0..1 without edge wrap."""
    image = Image.fromarray(np.uint8(np.clip(channel, 0, 1) * 255 + 0.5), 'L')
    return np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0


def silhouette(solid):
    """The shape with its holes filled in: everything that open space cannot reach from the frame's edge.

    A ring becomes a disc, so an edge effect follows only the outside of the ring and leaves an icon drawn inside it alone.
    """
    if solid.size > 700_000: return solid  # a whole sheet: too large to walk, and it has no single outline anyway
    outside = np.zeros_like(solid)
    outside[0], outside[-1], outside[:, 0], outside[:, -1] = ~solid[0], ~solid[-1], ~solid[:, 0], ~solid[:, -1]
    for _ in range(sum(solid.shape)):
        grown = outside.copy()
        grown[1:] |= outside[:-1]; grown[:-1] |= outside[1:]; grown[:, 1:] |= outside[:, :-1]; grown[:, :-1] |= outside[:, 1:]
        grown &= ~solid
        if np.array_equal(grown, outside): break
        outside = grown
    return ~outside


def shade(pixels, op):
    """Apply one operation to an RGBA uint8 array, using the array's own extent as the frame for gradients and glows.

    Fully transparent pixels stay untouched unless a glow or outline is drawn into them.
    """
    out = pixels.astype(np.float32)
    rgb, alpha = out[..., :3] / 255.0, out[..., 3]
    if op['color_mode'] != 'none' and op['color_amount'] > 0:
        color = gradient_field(op, rgb) if op.get('gradient') else rgb_of(op['color'])
        value = rgb.max(axis=2)
        if op['color_mode'] == 'fill' or op.get('gradient_type') == 'luminance' and op.get('gradient'):
            target, weight = np.broadcast_to(color, rgb.shape), np.full(value.shape, op['color_amount'], np.float32)
            if op['color_mode'] == 'tint': weight = np.clip((value - rgb.min(axis=2)) / 0.125, 0, 1) * op['color_amount']
        else:
            # Shading comes from the art's own brightness, so gradients, outlines and highlights survive.
            target = value[..., None] * color
            chroma = value - rgb.min(axis=2)
            weight = (np.clip(chroma / 0.125, 0, 1) if op['color_mode'] == 'tint' else 1.0) * op['color_amount']
            weight = np.broadcast_to(np.asarray(weight, np.float32), value.shape)
        rgb = rgb * (1 - weight[..., None]) + target * weight[..., None]
    if op['invert']: rgb = 1.0 - rgb
    if op['hue_shift']:
        hsv = np.array(Image.fromarray(np.uint8(np.clip(rgb, 0, 1) * 255 + 0.5), 'RGB').convert('HSV'))
        hsv[..., 0] = (hsv[..., 0].astype(np.int16) + round(op['hue_shift'] / 360 * 255)) % 256
        rgb = np.asarray(Image.fromarray(hsv, 'HSV').convert('RGB'), dtype=np.float32) / 255.0
    if op['saturation'] != 1:
        luma = (rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
        rgb = luma + (rgb - luma) * op['saturation']
    if op['contrast'] != 1: rgb = (rgb - 0.5) * op['contrast'] + 0.5
    if op['brightness'] != 1: rgb = rgb * op['brightness']
    rgb = np.clip(rgb, 0, 1)
    height, width = rgb.shape[:2]
    a = alpha / 255.0
    if op.get('posterize'): rgb = np.round(rgb * (op['posterize'] - 1)) / (op['posterize'] - 1)
    if op.get('pixelate') and min(height, width) >= op['pixelate'] * 2:
        block = int(op['pixelate'])
        small = Image.fromarray(np.uint8(np.dstack([rgb, a]) * 255 + 0.5), 'RGBA').resize((max(1, width // block), max(1, height // block)), Image.Resampling.BOX)
        chunky = np.asarray(small.resize((width, height), Image.Resampling.NEAREST), dtype=np.float32) / 255.0
        rgb, a = chunky[..., :3], chunky[..., 3]
    if op.get('gloss'):
        # A soft highlight over the upper half, like light on a glass button.
        rows = np.clip(1 - (np.arange(height, dtype=np.float32) + 0.5) / max(1.0, height * 0.55), 0, 1) ** 1.5
        rgb = rgb + (1 - rgb) * (rows[:, None, None] * op['gloss'])
    if op.get('flare'):
        # A light catching the upper left: a soft bloom, a hot core and two thin streaks, screened over the art.
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
        u, v = (xs + 0.5) / width - 0.34, (ys + 0.5) / height - 0.32
        far = u * u + v * v
        light = (np.exp(-far / (2 * 0.17 ** 2)) * 0.7 + np.exp(-far / (2 * 0.045 ** 2))
                 + np.exp(-(v / 0.014) ** 2) * np.exp(-np.abs(u) / 0.2) * 0.55 + np.exp(-(u / 0.014) ** 2) * np.exp(-np.abs(v) / 0.13) * 0.35)
        rgb = rgb + (1 - rgb) * np.clip(light * op['flare'], 0, 1)[..., None]
    if op.get('scanlines'): rgb = rgb * (1 - op['scanlines'] * ((np.arange(height) % 4) < 2).astype(np.float32))[:, None, None]
    if op.get('grain'): rgb = np.clip(rgb + np.random.default_rng(height * 8191 + width).normal(0, op['grain'] * 0.25, (height, width, 1)).astype(np.float32), 0, 1)
    drawn, rim_light, band = False, None, None
    # Sizes are given for a sprite about 128 px across and grow with larger art, so a 512 px cell gets the
    # same proportions as a small button. A whole sheet used as a frame keeps the plain pixel sizes.
    scale = 1.0 if height * width > 700_000 else float(np.clip(min(height, width) / 128.0, 1, 6))
    if op.get('rim_color') and op.get('rim_width') and a.any():
        # A neon line just inside the outer edge of the shape; the art within it is not repainted.
        shape, reach = silhouette(a > 0.2), max(1, round(op['rim_width'] * scale))
        core = np.asarray(Image.fromarray(np.uint8(np.pad(shape, reach)) * 255, 'L').filter(ImageFilter.MinFilter(reach * 2 + 1)))[reach:-reach, reach:-reach] > 0
        # The art's own alpha keeps the outer edge smooth; a slight blur does the same for the inner edge.
        band = np.clip(blur((shape & ~core).astype(np.float32), 0.7) * 1.25, 0, 1) * a
        if op['rim_color'] == 'gradient':
            own = dict(op, gradient=op['rim_gradient'], gradient_type='conic', gradient_repeat=1.0) if op.get('rim_gradient') else dict(op, gradient_type='conic' if op['gradient_type'] == 'luminance' else op['gradient_type'])
            rim_light = gradient_field(own, rgb)
            rim_light = rim_light / np.maximum(rim_light.max(axis=2, keepdims=True), 0.35)  # a neon tube is never dim: dark stops keep their hue
        else: rim_light = np.broadcast_to(rgb_of(op['rim_color']), rgb.shape)
        rgb = rgb * (1 - band[..., None]) + rim_light * band[..., None]
    if op.get('outline_color') and op.get('outline_width') and a.any():
        size = max(1, round(op['outline_width'] * scale)) * 2 + 1
        spread = np.asarray(Image.fromarray(np.uint8(a * 255 + 0.5), 'L').filter(ImageFilter.MaxFilter(size)), dtype=np.float32) / 255.0
        ring = np.clip(spread - a, 0, 1)
        total = a + ring * (1 - a)
        rgb = (rgb * a[..., None] + rgb_of(op['outline_color']) * (ring * (1 - a))[..., None]) / np.maximum(total, 1e-6)[..., None]
        a, drawn = total, True
    if op.get('glow_color') and op.get('glow_strength') and a.any():
        radius = max(1.0, float(op['glow_size']) * scale) / 2
        if op['glow_color'] == 'auto' and rim_light is not None: light = rim_light  # a neon rim glows in its own colors
        elif op['glow_color'] == 'auto':
            # Each shape glows in its own color: blur the art's premultiplied color along with its alpha.
            spread = np.maximum(blur(a, radius), 1e-4)
            light = np.clip(np.dstack([blur(rgb[..., c] * a, radius) for c in range(3)]) / spread[..., None], 0, 1)
            light = light / np.maximum(light.max(axis=2, keepdims=True), 0.35)  # full brightness, same hue; near-black art stays dim
        else: light = np.broadcast_to(rgb_of(op['glow_color']), rgb.shape)
        # Bloom: a tight bright layer plus a wide faint one, the way a real light falls off. With a rim, the
        # rim is the light source, so it also spills a little onto the art just inside it.
        source = band if band is not None else a
        halo = np.clip((blur(source, radius * 0.6) * 0.95 + blur(source, radius * 1.7) * 0.55) * op['glow_strength'], 0, 1)
        if band is not None: rgb = np.clip(rgb + light * (halo * a * (1 - band) * 0.5)[..., None], 0, 1)
        # Fade the halo out well before the frame's edge; a glow cut off square by a tightly packed rectangle looks like a box.
        across, down = np.arange(width, dtype=np.float32) + 0.5, np.arange(height, dtype=np.float32) + 0.5
        fade = np.clip(np.minimum(np.minimum(across, width - across)[None, :], np.minimum(down, height - down)[:, None]) / max(3.0, radius * 2.2), 0, 1)
        halo = halo * fade * fade * (3 - 2 * fade)
        if op.get('glow_inside'):
            pad = int(radius * 3) + 1
            edge = blur(np.pad(1 - a, pad, constant_values=1.0), radius)[pad:-pad, pad:-pad]
            rgb = np.clip(rgb + light * (np.clip(edge * op['glow_strength'] * 1.4, 0, 1) * a)[..., None], 0, 1)
        behind = halo * (1 - a)
        total = a + behind
        rgb = (rgb * a[..., None] + light * behind[..., None]) / np.maximum(total, 1e-6)[..., None]
        a, drawn = np.clip(total, 0, 1), True
    out[..., :3] = np.clip(rgb, 0, 1) * 255.0
    out[..., 3] = np.clip(a * 255.0 * op['opacity'], 0, 255)
    result = np.uint8(out + 0.5)
    if not drawn and not op.get('pixelate'):
        hidden = pixels[..., 3] == 0
        result[hidden] = pixels[hidden]
    return result


EDGE = r'outlines?|outlined|borders?|edges?|rims?|strokes?|rings?|contours?|trim'
INSIDE = r'inside|interior|insides|fill(?:ing)?|body|cent(?:er|re)|middle|core'
DARK_SIDE = ['#6a4fc4', '#2a1656', '#0b0619']  # radial: a violet heart falling away to near-black
QUIET_WORDS = r'subtle|soft|faint|slight|gentle|light|thin|fine'
LOUD_WORDS = r'strong|big|huge|intense|heavy|crazy|bold|thick|bright'
GLASS_WORDS = r'holographic|hologram|holo|iridescent|soap bubbles?|bubbles?|bubbly|glass(?:y)?|frosted|pearl(?:y|escent)?|opal(?:escent)?'


SMALL_WORDS = set('''a an the on in of to it its me my i is be are and with plus but for at by as or so do does can could would should will give gives make makes
    turn turns set put add adds use get let have has please want wants like only just all every each any also too then now there here this that these
    those them they their into onto from up out off over under around around while when where what which very really still again same one ones
    thing things something anything bit lot kind sort way side look looks style themed theme it's im i'm'''.split())


def unknown_words(prompt, lookup=None):
    """Content words the keyword planner has no meaning for. An empty list means the request was written
    entirely in tag words, so it can be planned exactly instead of being interpreted by the model."""
    text = re.sub(r'\b(worm|strike|mitos)\.is\b', r'\1is', prompt.lower().replace('black and white', 'monochrome'))  # a dot in a name is not a sentence end
    for preset, words in MOOD_WORDS.items(): text = re.sub(rf'\b(?:{words})\b', preset, text)   # "spooky" is the halloween palette
    known, text = lookup(text) if lookup else ({}, text)
    vocabulary = [*(phrase.replace(r'\b', '') for _, phrase in UI_PHRASES), *(words for _, words in HIDE.values()), HIDE_ALL, HIDE_VERB.replace(r'\b', ''), r'banners?|promos?',
                  *WORDS.values(), *(pattern for pattern, _ in MOODS), EDGE, INSIDE, QUIET_WORDS, LOUD_WORDS, GLASS_WORDS, *COLORS, *GRADIENTS, *map(re.escape, known),
                  r'#[0-9a-f]{6}', r'glow(?:s|ing|y)?|halos?|luminous|neon|outer|inner|proper|nice|lens|flares?|sparkl\w*|glints?|gleam\w*|twinkl\w*',
                  r'gloss(?:y)?|shiny|shine|polished|pixelated?|pixel art|8-?bit|retro|posteri[sz]ed?|cel.?shaded|cartoon(?:y)?|scanlines?|crt|vhs|grain(?:y)?|film grain|noisy|gritty',
                  r'gradients?|fad(?:e|es|ing)|ombre|blend(?:s|ed|ing)?|vertical|top to bottom|downward|diagonal|radial|circular|from the cent(?:er|re)|strip(?:e|es|ed)|bands?|duotone|tritone|gradient map|mapped',
                  r'colou?r(?:s|ed)?|backgrounds?|bg']
    rest = re.sub(r'(?<![a-z0-9#])(?:' + '|'.join(f'(?:{v})' for v in vocabulary) + r')(?![a-z0-9])', ' ', text)
    return [word for word in re.findall(r"[a-z0-9#][a-z0-9'-]*", rest) if word not in SMALL_WORDS and word not in STOP and not word.isdigit()]


def rule_plan(prompt, lookup=None):
    """Keyword planner: one operation per thing named, built from tag words.

    lookup(text) -> ({word: [sprite names]}, text) resolves words that name a specific sprite ("the white virus").
    """
    text = re.sub(r'\b(worm|strike|mitos)\.is\b', r'\1is', prompt.lower().replace('black and white', 'monochrome'))  # a dot in a name is not a sentence end
    for preset, words in MOOD_WORDS.items(): text = re.sub(rf'\b(?:{words})\b', preset, text)   # "spooky" is the halloween palette
    known, text = lookup(text) if lookup else ({}, text)
    operations = []
    parts = [(index, part.strip()) for index, sentence in enumerate(re.split(r'[.;\n,]| but ', text))
             for part in re.split(r'\s+(?:and|with|plus)\s+', sentence) if part.strip()]
    carried, carried_names, previous, sentence = [], [], None, -1  # "buttons and panels darker" / "buttons red and darker"
    any_color = r'(#[0-9a-f]{6}|\b(?:' + '|'.join(COLORS) + r')\b)'
    qualifiers = rf'(?:(?:outer|inner|neon|proper|nice|{QUIET_WORDS}|{LOUD_WORDS}) )*'

    def ramp_in(piece):
        """(preset stops or listed colors, single color) named in a piece of text."""
        preset = next((name for name in GRADIENTS if re.search(rf'\b{name}\b', piece) and (name not in COLORS or re.search(r'\bgradient\b', piece))), '')
        listed = [hex_color(c) for c in re.findall(any_color, piece)]
        return (GRADIENTS[preset] if preset else listed if len(listed) >= 2 else []), (listed[0] if len(listed) == 1 and not preset else '')

    ui, hide, hiding = {}, [], False
    for index, clause in parts:
        if index != sentence: carried, carried_names, previous, sentence, hiding = [], [], None, index, False
        op = {}
        # Menu controls the game can leave out: "hide the discord button", "remove the promos", and the
        # rest of "hide discord and the app store badges" without repeating the verb.
        if hide_targets(clause) and (hiding or re.search(HIDE_VERB, clause)):
            hide += hide_targets(clause); hiding = True; continue
        # Game colors from the code: "make the pink red", "arrows cyan", "menu background navy" (not the pattern),
        # "light mode background white", "header bars purple", "menu buttons gold".
        paints = [hex_color(c) for c in re.findall(any_color, re.sub(r'\bthe pink\b|\bthe teal\b', ' ', clause))]
        # With the exe off limits a named game color is still noted (the chat explains it) rather than painted on
        # art: the game multiplies that art by its own color, so gold on teal-tinted white would come out olive.
        # "menu background navy" falls through to the menu pattern art below instead.
        if len(paints) == 1 and not re.search(r'\bpattern|overlay|triangles?\b', clause):
            named_ui = next((name for name, phrase in UI_PHRASES if re.search(phrase, clause)), '')
            if named_ui:
                ui[named_ui] = paints[0]; continue
            if not exe_palette.BLOCKED and re.search(r'\b(?:background|backdrop|bg)\b', clause) and not re.search(r'\b(?:world|game|arena|map|in-?game)\s*(?:background|bg)\b|\bgrid|arena|floor\b', clause):
                ui['background'] = paints[0]; continue
        names = list(dict.fromkeys(n for word, found in known.items() if re.search(rf'\b{re.escape(word)}\b', clause) for n in found))
        if re.search(rf'\b(?:{GLASS_WORDS})\b', clause):
            # Soap-bubble glass: a pale iridescent edge, a soft glow that also blooms inward, and a glassy highlight.
            op.update(rim_color='gradient', rim_gradient=GRADIENTS['holographic'], rim_width=3, glow_color='auto', glow_size=10, glow_strength=0.9, glow_inside=True, gloss=0.3)
            clause = re.sub(rf'\b(?:{GLASS_WORDS})\b', ' ', clause)
        # "a cyan glow": that color belongs to the glow, not to the art.
        glow = re.search(qualifiers + any_color + r'?\s*' + qualifiers + r'\bglow(?:s|ing|y)?\b|\bhalos?\b|\bluminous\b', clause)
        if glow:
            quiet, loud = re.search(rf'\b(?:{QUIET_WORDS})\b', glow.group(0)), re.search(rf'\b(?:{LOUD_WORDS})\b', glow.group(0))
            op.update(glow_color=hex_color(glow.group(1) or '') or 'auto', glow_size=16 if loud else 7 if quiet else 10, glow_strength=2.2 if loud else 0.8 if quiet else 1.4,
                      glow_inside=bool(re.search(r'\binner\b', glow.group(0))) or op.get('glow_inside', False))
            clause = clause.replace(glow.group(0), ' ')
        flare = re.search(qualifiers + r'\b(?:lens )?(?:flares?|sparkl\w*|glints?|gleam\w*|twinkl\w*)\b', clause)
        if flare:
            op['flare'] = 0.35 if re.search(rf'\b(?:{QUIET_WORDS})\b', flare.group(0)) else 0.9 if re.search(rf'\b(?:{LOUD_WORDS})\b', flare.group(0)) else 0.6
            clause = clause.replace(flare.group(0), ' ')
        # "rainbow outline" / "thick cyan border": a neon line along the outer edge, with its own colors.
        if re.search(rf'\b(?:{EDGE})\b', clause):
            stops, single = ramp_in(clause)
            op.update(rim_width=6 if re.search(rf'\b(?:{LOUD_WORDS})\b', clause) else 2 if re.search(rf'\b(?:{QUIET_WORDS})\b', clause) else 4)
            op.update(dict(rim_color='gradient', rim_gradient=stops) if stops else dict(rim_color=single or op.get('rim_color') or '#ffffff'))
            clause = re.sub(any_color + '|' + '|'.join(rf'\b{name}\b' for name in GRADIENTS) + rf'|\b(?:{EDGE}|{QUIET_WORDS}|{LOUD_WORDS})\b', ' ', clause)
        for pattern, settings in ((r'gloss(?:y)?|shiny|shine|polished', dict(gloss=0.5)), (r'pixelated?|pixel art|8-?bit|retro', dict(pixelate=4)),
                                  (r'posteri[sz]ed?|cel.?shaded|cartoon(?:y)?', dict(posterize=4)), (r'scanlines?|crt|vhs', dict(scanlines=0.35)),
                                  (r'grain(?:y)?|film grain|noisy|gritty', dict(grain=0.12))):
            if re.search(rf'\b(?:{pattern})\b', clause): op.update(settings)
        stops, _ = ramp_in(clause)
        inside = re.search(rf'\b(?:{INSIDE})\b', clause)
        if stops and (inside or re.search(r'\bgradient|\bfad(?:e|ing)\b|\bombre\b|\bblend', clause) or stops in GRADIENTS.values()):
            op.update(gradient=stops, gradient_angle=90 if re.search(r'\bvertical|top to bottom|downward', clause) else 45 if re.search(r'\bdiagonal', clause) else 0,
                      gradient_type='radial' if re.search(r'\bradial|circular|from the cent', clause) else 'luminance' if re.search(r'\bduotone|tritone|gradient map|mapped\b', clause) else 'linear',
                      gradient_repeat=3 if re.search(r'\bstrip(?:e|es|ed)\b|\bbands?\b', clause) else 1)
            clause = re.sub(any_color, ' ', clause)
        elif inside and re.search(r'\bdark(?:er)?\b|\bblack\b|\bshadow\w*|\bnight\b', clause) and not re.search(any_color.replace('|black', ''), clause):
            # "the inside something on the dark side": a deep radial fill rather than plain dimming.
            op.update(gradient=DARK_SIDE, gradient_type='radial', gradient_angle=0, gradient_repeat=1)
            clause = re.sub(r'\bdark(?:er)?\b|\bblack\b', ' ', clause)
        hexes = re.findall(r'#[0-9a-f]{6}\b', clause)
        named = [c for c in COLORS if re.search(rf'\b{c}\b', clause)]
        for pattern, settings in MOODS:
            if re.search(rf'\b(?:{pattern})\b', clause):
                for key, value in settings.items(): op.setdefault(key, value)
        # "dark blue" / "light pink" describe the color, not a second instruction.
        if (hexes or named) and re.search(r'\b(?:dark|light|bright|deep|pale)\s+(?:' + '|'.join(COLORS) + r')\b', clause): op.pop('brightness', None)
        targets = [family for family, pattern in WORDS.items() if re.search(rf'\b(?:{pattern})\b', clause)]
        if 'menu_background' in targets: targets = [t for t in targets if t not in ('panels', 'world_background')]
        if re.search(r'\bbackgrounds?\b|\bbg\b', clause) and not {'menu_background', 'world_background'} & set(targets): targets += ['menu_background', 'world_background']
        if hexes or named:
            color = hexes[0] if hexes else COLORS[named[0]]
            if re.search(r'\b(?:dark|deep)\s+' + (named[0] if named else '#'), clause): color = '#' + ''.join(f'{int(int(color[i:i+2], 16) * 0.6):02x}' for i in (1, 3, 5))
            if re.search(r'\b(?:light|pale)\s+' + (named[0] if named else '#'), clause): color = '#' + ''.join(f'{int(255 - (255 - int(color[i:i+2], 16)) * 0.55):02x}' for i in (1, 3, 5))
            only_flat = set(targets) and set(targets) <= {'menu_background'}
            neutral = inside or (set(targets) and set(targets) <= {'fonts', 'icons', 'world_background'})
            op.update(color=color, color_mode='fill' if only_flat else 'colorize' if neutral else 'tint')
        if op.get('gradient'): op['color_mode'] = 'fill' if set(targets) and set(targets) <= {'menu_background'} else 'colorize'
        if not op:
            carried += targets  # named now, described by the next part
            carried_names += names
            continue
        if not targets and not names and not carried and not carried_names and previous is not None:
            for key, value in op.items(): previous.setdefault(key, value)  # another trait of the same thing
            continue
        # A named sprite is matched by name across every family; a family word alone selects the family.
        subject = list(dict.fromkeys(carried_names + names))
        previous = dict(op, targets=['everything'] if subject else list(dict.fromkeys(carried + targets)) or ['everything'], match=subject)
        operations.append(previous)
        carried, carried_names = [], []
    for op in operations:
        # A gradient asked for together with a glow is the neon-sign look: glowing colored edges, art inside left alone.
        if op.get('glow_color') == 'auto' and op.get('gradient') and op.get('color_mode') != 'fill' and not op.get('rim_color') and op['gradient'] is not DARK_SIDE:
            plain = op.get('gradient_type') == 'linear' and not op.get('gradient_angle')
            op.update(rim_color='gradient', rim_width=4, color_mode='none', gradient_type='conic' if plain else op['gradient_type'])
    plan = clean_plan(dict(operations=operations, ui_colors=ui, hide=hide))
    plan['reply'] = 'Planned from keywords (the local AI model was not used).' if changes_anything(plan) else ''
    return plan


def changes_anything(plan):
    return bool(plan['operations'] or plan['ui_colors'] or plan.get('hide'))


def ollama(path, payload=None, timeout=6):
    # The installed Ollama when it runs, otherwise the portable one in runtime/ (started on first use).
    request = urllib.request.Request((runtime.OLLAMA.ensure() if OLLAMA == runtime.SYSTEM else OLLAMA) + path, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def models():
    try: listed = ollama('/api/tags').get('models', [])
    except (OSError, ValueError): return []
    usable = [m for m in listed if 'completion' in (m.get('capabilities') or ['completion']) and 'embed' not in m['name']
              and 'vision' not in (m.get('capabilities') or []) and m.get('size', 0) > 1.5e9]
    order = {name: i for i, name in enumerate(PREFERRED)}
    usable.sort(key=lambda m: (order.get(m['name'], len(order)), -m.get('size', 0)))
    return [dict(name=m['name'], thinking='thinking' in (m.get('capabilities') or []), size=m.get('size', 0)) for m in usable]


def thumb(pixels, size=84):
    image = Image.fromarray(pixels, 'RGBA')
    if max(image.size) < size // 2: image = image.resize((image.width * 3, image.height * 3), Image.Resampling.NEAREST)
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()


class Themes:
    def __init__(self, lab):
        self.lab, self.lock, self.jobs = lab, threading.RLock(), {}
        self.file = lab.root / 'themes.json'
        self.reindex()

    def present(self):
        """Names of the sprites in the library (not removed from the pool)."""
        with self.lab.lock: return {row['name'] for row in self.lab.catalog.rows.values() if not row.get('removed')}

    def reindex(self):
        """Rebuild the sheet index; art removed from the pool is never planned, sampled or shaded."""
        lab, sheets, counts = self.lab, {}, {family: 0 for family in FAMILIES}
        for key, row in lab.catalog.rows.items():
            if row['kind'] != 'image' or not row.get('primary', True) or row.get('removed'): continue
            every = [lab.catalog.rows[k] for k in lab.catalog.children.get(key, []) if not lab.catalog.rows[k].get('legacy')]
            children = [child for child in every if not child.get('removed')]
            if every and not children: continue
            members = [(child, families_of(child, row['name'])) for child in children]
            # With some sprites removed, "the whole sheet" must not sweep them back in.
            sheets[key] = dict(row=row, family=families_of(row), members=members, partial=len(children) < len(every))
            for _, family in (members or [(row, families_of(row))]): counts[family] += 1
        self.sheets, self.counts, self._look = sheets, counts, None
        # Words that appear in sprite names, so a request can name one thing: "the virus", "the crown".
        self.tokens, self._tone, self.labelled = {}, {}, {}
        for sheet in sheets.values():
            for row, _ in (sheet['members'] or [(sheet['row'], sheet['family'])]):
                if row.get('label'):
                    label = row['label'].lower()
                    self.labelled.setdefault(label, set()).add(row['name'])
                    # "Square flag icon (Ranks)" is also asked for as just "the square flag icon".
                    short = re.sub(r'\s*\([^)]*\)\s*$', '', label).strip()
                    if short and short != label: self.labelled.setdefault(short, set()).add(row['name'])
        for sheet_key, sheet in sheets.items():
            for row, _ in (sheet['members'] or [(sheet['row'], sheet['family'])]):
                if sheet['family'] in QUIET or row['kind'] == 'glyph': continue
                # The screen in brackets says where a thing is, not what it is: "Server region flag (Ranks)" is no "ranks icon".
                label = {word for word in re.findall(r'[a-z]{4,}', re.sub(r'\([^)]*\)', ' ', row.get('label', '').lower())) if word not in GENERIC}
                for token in set(re.findall(r'[a-z]{4,}', re.sub(r'\.png$', '', row['name'].lower()))) | label:
                    self.tokens.setdefault(token, []).append((sheet_key, row))

    def tone(self, sheet_key, row):
        """Average brightness of a sprite's visible pixels, 0 (black) to 1 (white)."""
        if row['key'] not in self._tone:
            pixels = np.asarray(self.lab.catalog._sheet(sheet_key))
            x, y, w, h = row.get('rect') or (0, 0, pixels.shape[1], pixels.shape[0])
            crop = pixels[y:y+h:max(1, h // 64), x:x+w:max(1, w // 64)].reshape(-1, 4).astype(np.float32)
            solid = crop[crop[:, 3] > 128][:, :3]
            self._tone[row['key']] = float((solid @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean() / 255) if len(solid) else 0.5
        return self._tone[row['key']]

    def named(self, text):
        """Sprites a request names -> ({word: [sprite names]}, text).

        "the white virus" picks the light virus sprite; the adjective is then removed from the text
        so it is not mistaken for a color to paint with.
        """
        found, text = {}, text.lower()
        family_words = re.compile('|'.join(f'(?:{pattern})' for pattern in WORDS.values()))
        # Names you gave ("speed potion") are matched as whole phrases before single words.
        scan = text
        for label in sorted(self.labelled, key=len, reverse=True):
            phrase = rf'(?<![a-z0-9]){re.escape(label)}s?(?![a-z0-9])'   # also works for names ending in ")"
            if re.search(phrase, scan):
                found[label] = sorted(self.labelled[label])[:24]
                scan = re.sub(phrase, ' ', scan)
        for word in dict.fromkeys(re.findall(r'[a-z]{4,}', scan)):
            stem = next((s for s in (word, word[:-1] if word.endswith('s') else '', word[:-2] if word.endswith('es') else '') if s in self.tokens), '')
            if not stem or stem in STOP or word in STOP or stem in COLORS or stem in GRADIENTS or family_words.fullmatch(word): continue
            rows = self.tokens[stem]
            # A word that is part of a name you gave means those sprites: "the star icon" is the Star button, not every star effect.
            given = [(k, r) for k, r in rows if re.search(rf'\b{stem}', re.sub(r'\([^)]*\)', ' ', r.get('label', '').lower()))]
            if given: rows = given
            described = re.search(rf'\b(white|light|bright|pale|dark|black)\s+{word}\b', text)
            if described:
                light = described.group(1) in ('white', 'light', 'bright', 'pale')
                fits = [(k, r) for k, r in rows if (self.tone(k, r) > 0.55) == light]
                if fits and len(fits) < len(rows) or fits and all((self.tone(k, r) > 0.55) == light for k, r in rows):
                    rows, text = fits, text.replace(described.group(0), word)
            found[word] = sorted({row['name'] for _, row in rows})[:12]
        return found, text

    def hints(self, message):
        """One line per named thing, telling the model the real sprite names and whether they are light or dark."""
        found, _ = self.named(message)
        lines = []
        for word, names in found.items():
            if word in self.labelled:
                lines.append(f'"{word}" (your name for these sprites): ' + ', '.join(names))
                continue
            rows = {row['name']: (key, row) for key, row in self.tokens.get(word, self.tokens.get(word.rstrip('s'), [])) if row['name'] in names}
            lines.append(f'{word}: ' + ', '.join(f"{name} ({'light' if self.tone(*rows[name]) > 0.55 else 'dark'})" if name in rows else name for name in names))
        return lines

    # ----- state on disk -------------------------------------------------
    def load(self):
        try: return json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError, ValueError): return {'batches': []}

    def store(self, data):
        data['batches'] = data['batches'][-30:]
        temp = self.file.with_name(self.file.name + '.tmp')
        temp.write_text(json.dumps(data, indent=1), encoding='utf-8')
        temp.replace(self.file)

    def last(self):
        active = [b for b in self.load()['batches'] if not b.get('undone')]
        return active[-1] if active else None

    # ----- what the model is told ---------------------------------------
    def look(self):
        """Typical color of each family today, so "match the buttons" has something to match."""
        if self._look is None:
            look = {}
            for sheet_key, sheet in self.sheets.items():
                todo = [(row, family) for row, family in (sheet['members'] or [(sheet['row'], sheet['family'])]) if len(look.setdefault(family, [])) < 14]
                if not todo: continue
                pixels = np.asarray(self.lab.catalog._sheet(sheet_key))
                for row, family in todo[:14]:
                    x, y, w, h = row.get('rect') or (0, 0, pixels.shape[1], pixels.shape[0])
                    crop = pixels[y:y+h:max(1, h // 48), x:x+w:max(1, w // 48)].reshape(-1, 4).astype(np.int32)
                    solid = crop[crop[:, 3] > 128][:, :3]
                    vivid = solid[(solid.max(axis=1) - solid.min(axis=1)) > 40]
                    pick = vivid if len(vivid) > max(8, len(solid) // 12) else solid
                    if len(pick) and len(look[family]) < 14: look[family].append(pick.mean(axis=0))
            self._look = {family: '#' + ''.join(f'{int(v):02x}' for v in np.mean(values, axis=0)) for family, values in look.items() if values}
        return self._look

    def system_prompt(self):
        look, lines = self.look(), []
        for family, hint in FAMILIES.items():
            if self.counts[family]: lines.append(f'- {family} ({self.counts[family]} sprites, typical color {look.get(family, "n/a")}): {hint}')
        return ('You design visual themes for the game Mitos.is by planning recolor operations on its sprite art. '
                'Reply with JSON only: {"reply": one or two friendly sentences describing the theme, "operations": [...]}.\n\n'
                'Art families you can target:\n' + '\n'.join(lines) + '\n- everything: every family except flags and downloaded_skins.\n\n'
                'Each operation has:\n'
                '- targets: list of families. Later operations are applied after earlier ones, so start broad and then refine.\n'
                '- match / exclude: optional sprite-name wildcards such as "button-danger*" or "*crown*" to narrow the targets.\n'
                '- color: "#rrggbb", with color_mode: "tint" recolors only colored pixels and keeps white, grey and black as they are (best for buttons, cells, logo); '
                '"colorize" recolors every pixel by its brightness, so white becomes the color (use for white icons, fonts, light backgrounds); '
                '"fill" paints a flat color and keeps only the shape (use for menu_background). color_amount 0-1 is the strength.\n'
                '- hue_shift (-180..180 degrees), saturation (0 = greyscale, 1 = unchanged, up to 3), brightness (0.1-3), contrast (0.1-3), '
                'opacity (0 hides, 1 unchanged, up to 4 strengthens faint art), invert (true/false).\n'
                '- gradient: a list of 2-8 "#rrggbb" stops painted across EACH sprite instead of one flat color, or gradient_preset: one of ' + ', '.join(GRADIENTS) + '. '
                'For a seasonal or mood theme start from its palette: ' + '; '.join(f'{preset} for {words.replace("|", ", ").replace("(?:", "").replace(")?", "").replace("?", "")}' for preset, words in MOOD_WORDS.items()) + '. '
                'gradient_type "linear" (with gradient_angle in degrees: 0 = left to right, 90 = top to bottom, 45 = diagonal), "radial" (center outwards), "conic" (colors travel around the center, ideal for rings and frames) or "luminance" (dark pixels take the first stop and bright pixels the last: duotone and heat-map looks). '
                'gradient_repeat 2-6 makes stripes. A gradient uses color_mode like a color does: "colorize" (default, keeps shading), "tint" (only colored pixels) or "fill" (flat paint).\n'
                '- glow_color: "#rrggbb", or "auto" so each shape glows in its own color; glow_size 4-24 pixels, glow_strength 0.5-3, glow_inside true adds a bright rim inside the shape. '
                'outline_color with outline_width 1-6 draws a border around shapes.\n'
                '- rim_color: "#rrggbb" or "gradient" (uses this operation\'s gradient, conic by default) with rim_width 2-6: a neon line along only the OUTER edge of each sprite; whatever is inside (icons, text, fills) is left exactly as it is. '
                'rim_color plus glow_color "auto" is the neon-sign look and is the right answer whenever the user asks for glow, neon or glowing gradients: glowing colored rings and frames with clean white icons inside. With a rim, set color_mode "none" unless the inside should be repainted too.\n'
                'The rim can have its own colors, separate from the fill: rim_preset (a preset name) or rim_gradient (a list of stops). Then color / gradient / brightness describe the INSIDE and the rim is drawn over it at full brightness. '
                'So "rainbow outline, dark inside" is ONE operation: rim_preset "rainbow" plus a dark radial gradient with color_mode "colorize".\n'
                '- gloss 0-1 (glassy highlight), flare 0-1 (a light catching the upper left: bloom, hot core and thin streaks), posterize 2-8 (flat cartoon color bands), pixelate 2-16 (chunky retro pixels), scanlines 0-1 (CRT lines), grain 0-0.5 (film noise).\n'
                'Words the user may use and what they mean: outline / border / edge / rim / ring = rim_color; glow = glow_color (subtle: size 7 strength 0.8; strong: size 16 strength 2.2); inside / fill / body = color or gradient on the art itself; '
                'flare / sparkle = flare; glossy / shiny = gloss; glass / bubble / holographic / iridescent = rim_preset "holographic" with rim_width 3, glow_color "auto", glow_strength 0.9, glow_inside true and gloss 0.3.\n'
                'Naming one thing: when the request names a specific sprite, a line "Sprites named in this request" follows it with the real sprite names. Target them with "targets":["everything"] and "match":[those exact names]; do not theme anything else unless asked.\n\n'
                'Rules: 1 means unchanged, so omit fields you do not need. Every part the user names gets its own operation with exactly the color or change they asked for, placed after any "everything" operation. '
                'When the user says to leave something alone, put it in no operation and exclude it from "everything" by listing its sprite-name wildcards in exclude, or simply target the other families one by one instead of "everything". Pick concrete hex colors yourself for moods like "sunset" or "cyberpunk" and use two or three related colors across families so the theme feels designed. '
                'Keep text and icons readable against panels. Do not touch flags or downloaded_skins unless asked. '
                'Always return the complete theme, including earlier requests in this conversation that still apply, because each plan replaces the previous one. '
                'If the request is not about how the game looks, return an empty operations list and say so in reply.\n\n'
                'Example request: "ocean theme, keep the logo"\n'
                '{"reply":"Deep ocean blues with seafoam buttons; the logo is untouched.","operations":['
                '{"targets":["everything"],"exclude":["logo*"],"color":"#1f6fb2","color_mode":"tint","color_amount":0.85},'
                '{"targets":["buttons"],"color":"#2ec4b6","color_mode":"tint"},'
                '{"targets":["menu_background"],"color":"#0b3c5d","color_mode":"fill","opacity":2.5},'
                '{"targets":["world_background"],"color":"#cfe8f5","color_mode":"colorize","color_amount":0.6}]}\n'
                'Game colors set in the code (they change Mitosis.exe, so use them only when the user asks about these colors): '
                + '; '.join(f'ui_colors.{name} (now {original}) is {what}' for name, (_, original, what) in UI_COLORS.items()) +
                '. The main menu buttons are white art tinted by ui_colors.menu_buttons in the game, so to recolor them set that color rather than tinting the button art. '
                'When the user wants a whole new look for the menus, choose matching game colors too (background, menu_buttons, accent, header, text). '
                'Menu controls the game can leave out (also in Mitosis.exe), listed in "hide" only when the user asks to hide or remove them: '
                + '; '.join(f'{group} = {label}' for group, (label, _) in HIDE.items()) + '. "Remove the promos/ads" means all of them.\n'
                'Example request: "make the menu background navy and the pink cyan"\n'
                '{"reply":"Navy behind the menu and cyan instead of pink; the art files are unchanged.","operations":[],"ui_colors":{"background":"#0b1d3a","accent":"#22d3ee"}}\n'
                'Example request: "gold menu buttons, purple headers, and hide the discord button"\n'
                '{"reply":"Gold menu buttons and purple header bars; the Discord button is left out.","operations":[],"ui_colors":{"menu_buttons":"#f5c542","header":"#3b1f5c"},"hide":["discord"]}\n'
                'Example request: "theme it rainbow with proper glows"\n'
                '{"reply":"Rainbow neon: glowing rainbow rims around buttons, panels and cells, a rainbow logo, and clean white icons.","operations":['
                '{"targets":["buttons","panels","cells","icons"],"gradient_preset":"rainbow","gradient_type":"conic","color_mode":"none","rim_color":"gradient","rim_width":4,"glow_color":"auto","glow_size":14,"glow_strength":1.8},'
                '{"targets":["logo"],"gradient_preset":"rainbow","gradient_angle":0,"color_mode":"colorize","glow_color":"auto","glow_size":12,"glow_strength":1.5},'
                '{"targets":["menu_background"],"gradient_preset":"rainbow","gradient_angle":45,"color_mode":"fill","opacity":3}]}\n'
                'Example request: "synthwave: chrome logo, hot pink neon outlines on icons, retro pixel food"\n'
                '{"reply":"Synthwave night: chrome logo, neon-pink icons and chunky pixel food.","operations":['
                '{"targets":["everything"],"gradient_preset":"synthwave","gradient_type":"luminance","color_mode":"colorize","color_amount":0.8},'
                '{"targets":["logo"],"gradient_preset":"chrome","gradient_angle":90,"color_mode":"colorize","gloss":0.5},'
                '{"targets":["icons","buttons","panels"],"rim_color":"#ff2ec4","rim_width":3,"glow_color":"auto","glow_size":12,"glow_strength":1.8},'
                '{"targets":["food"],"pixelate":4,"saturation":1.4}]}\n'
                'Example request: "on the white virus give me a rainbow outline with a subtle glow and the inside something on the dark side with flare" (Sprites named in this request: virus: light_virus.png (light), virus.png (dark))\n'
                '{"reply":"The white virus gets a rainbow neon outline with a soft glow, a deep violet-black body and a light flare.","operations":['
                '{"targets":["everything"],"match":["light_virus.png"],"rim_preset":"rainbow","rim_width":4,"glow_color":"auto","glow_size":7,"glow_strength":0.8,'
                '"gradient":["#6a4fc4","#2a1656","#0b0619"],"gradient_type":"radial","color_mode":"colorize","flare":0.6}]}\n'
                'Be bold when the user asks for something wild: combine gradients, glows, outlines and gloss, and vary angles between families. '
                'Use at most six operations. After an "everything" operation, add a family only when it should look different from the rest; never list every family one by one with the same effect. '
                'Leave out invert, pixelate, posterize, scanlines and grain unless the user asked for that kind of look, and leave opacity at 1 except for menu_background.\n'
                'Example request: "make the buttons darker and hide the menu pattern"\n'
                '{"reply":"Darker buttons, and the menu pattern is hidden.","operations":[{"targets":["buttons"],"brightness":0.7},{"targets":["menu_background"],"opacity":0}]}')

    # ----- planning ------------------------------------------------------
    def status(self):
        available, last = models(), self.last()
        running = next((dict(j) for j in self.jobs.values() if j['state'] == 'running'), None)
        return dict(ollama=bool(available), models=available, families=[dict(id=f, sprites=self.counts[f], color=self.look().get(f), hint=h) for f, h in FAMILIES.items() if self.counts[f]],
                    sheets=len(self.sheets), job=running, last=last and dict(id=last['id'], prompt=last['prompt'], sheets=len(last['items']), at=last['at'], plan=last['plan']),
                    game=dict(colors=[dict(name=name, field=field, was=was, what=what) for name, (field, was, what) in UI_COLORS.items()],
                              hide=[dict(id=group, label=label) for group, (label, _) in HIDE.items()]))

    def chat(self, message, model='', history=None, only=None):
        """only: catalog keys of checked assets; the plan is then previewed and applied to those alone.

        One request can mix both tools: the parts that need drawing ("put a santa hat on the title") go to the
        image model, the rest ("theme it christmas") to the shader. A whole-game mood also offers a title redraw."""
        scope = self.scope(only)
        message = str(message or '').strip()[:2000]
        if not message: raise ValueError('Describe the look you want.')
        command = self.command(message)
        if command: return command
        designer = getattr(self, 'designer', None)
        drawn, rest = [], message
        if designer:
            pieces = [p.strip(' ,') for p in re.split(r'[.;\n]+|,?\s+(?:and then|then|also|plus)\s+|,?\s+and\s+(?=(?:make|give|put|add|draw|turn|theme|redesign|redraw|paint|stick)\b)', message) if p and p.strip(' ,')]
            for piece in pieces:
                if design.wants_design(piece, unknown_words(piece, self.named)) and designer.targets(piece, only): drawn.append(piece)
            rest = '. '.join(p for p in pieces if p not in drawn)
        if drawn:
            rows, seen = [], set()
            for piece in drawn:
                for r in designer.targets(piece, only):
                    if r['key'] not in seen: seen.add(r['key']); rows.append(r)
            part = self.design_offer(designer, '. '.join(drawn), rows)
            if not rest.strip():
                return dict(plan=dict(reply='', operations=[], ui_colors={}, hide=[]), source='design', note=part.pop('note'), preview=None, design=part)
            result = self.plan_request(rest, model, history, scope)
            result['design'] = part
            return result
        result = self.plan_request(message, model, history, scope)
        if designer and re.search(design.OBJECTS, message.lower()):
            # New objects need drawing, and drawing needs a target the request did not name.
            result['note'] = (result['note'] + ' ' if result['note'] else '') + ('To draw new things, say what to draw them on (the title, the icons, '
                                                                                 '"the speed potion") or check those assets in the library first.')
        if designer and design.wants_design(message, unknown_words(message, self.named)) and designer.title_rows():
            # "Make the game spooky": the palette is shaded now; redrawing the title is offered, not started.
            result['design'] = dict(self.design_offer(designer, f'{message}. Redesign the game title to match.', designer.title_rows()), optional=True)
        return result

    HELP = ('I change how Mitosis looks. Ask for colors, moods or effects ("christmas theme", "rainbow outlines with a subtle glow", '
            '"holographic glass panels", "make the buttons darker") and I shade the matching art. Ask for new things or a new style '
            '("put a party hat on the title", "make the title spooky with dripping blood") and the local image model redraws it. '
            'Check assets in the library to change only those. Say "undo" to take the last theme off.')

    def command(self, message):
        """A few things that are not looks: help, and undo by chat."""
        low = message.lower().strip(' .!?')
        empty = dict(reply='', operations=[], ui_colors={}, hide=[])
        if re.fullmatch(r'help|commands|what can (?:you|i) do(?: here)?|what do you do|how does (?:this|it) work', low):
            return dict(plan=dict(empty, reply=self.HELP), source='command', note='', preview=None)
        if re.fullmatch(r'undo(?: (?:that|it|the last (?:theme|change|one)))?|go back|revert(?: that| it)?|take (?:that|it) (?:back )?off', low):
            done = self.undo()
            return dict(plan=dict(empty, reply=f"Undid “{done['prompt'][:80]}”: {done['reverted']} sheet(s) restored" + (f", {done['skipped']} edited since were left alone." if done['skipped'] else '.')),
                        source='command', note='', preview=None, undone=True)
        return None

    def art_direction(self, prompt, subject):
        """Expand a short design request ("spooky") into a concrete visual description for the image model, or None."""
        available = models()
        if not available: return None
        model = available[0]['name']
        system = ('You are the art director of a 2D mobile game. Rewrite the request as one short paragraph (at most 60 words) that says exactly '
                  'what to draw: materials, colors, lighting, and small details or decorations. Keep the subject, and any text in it, exactly as '
                  'it is. Do not describe a background, scenery, or a shadow, floor or stand under it (the art is cut out). '
                  'No preamble, no lists, no quotes.')
        payload = dict(model=model, stream=False, keep_alive=0,   # unload at once: the image model needs the graphics card next
                       messages=[dict(role='system', content=system), dict(role='user', content=f'Subject: {subject}\nRequest: {prompt}')],
                       options=dict(temperature=0.7, num_predict=180, num_ctx=2048, **MODEL_OPTIONS.get(model, {})))
        if available[0]['thinking']: payload['think'] = False
        answer = ollama('/api/chat', payload, timeout=120)
        text = re.sub(r'<think>.*?</think>', '', answer.get('message', {}).get('content', ''), flags=re.S).strip().strip('"')
        return text if 20 <= len(text) <= 900 else None

    @staticmethod
    def design_offer(designer, prompt, rows):
        ready = designer.status()
        return dict(prompt=prompt, available=ready['available'], note='' if ready['available'] else 'The image model is not installed (' + ', '.join(ready['missing']) + ').',
                    targets=[dict(key=r['key'], name=r.get('label') or r['name'], size=[r['width'], r['height']]) for r in rows])

    def plan_request(self, message, model='', history=None, scope=None):
        """Turn a request into a shading plan: exactly from tag words, else with the local model, else from keywords."""
        low = message.lower()
        size, motion = re.search(SIZE_WORDS, low), re.search(MOTION_WORDS, low)
        if size or motion:
            # "Make the font bigger", "make the quests icon flash": nothing else asked, so say what is possible instead of
            # handing the model a request it would answer with an unrelated tweak.
            rest = re.sub(f'{SIZE_WORDS}|{MOTION_WORDS}', ' ', low)
            if not changes_anything(rule_plan(rest, self.named)) and not unknown_words(rest, self.named):
                reply = ' '.join(note for note in (SIZE_NOTE if size else '', MOTION_NOTE if motion else '') if note)
                return dict(plan=dict(reply=reply, operations=[], ui_colors={}, hide=[]), source='rules', note='', preview=None)
        available = models()
        names = [m['name'] for m in available]
        plan, source, note = None, 'rules', ''
        # A follow-up that leans on the last plan ("make it darker", "more glow") needs the model's memory of it;
        # a complete request in tag words ("christmas theme", "gold buttons") does not, even mid-conversation.
        follow_up = history and re.search(r'\b(?:it|that|this|them|those|more|less|again|instead|too|also|rest|back)\b', message.lower())
        if not follow_up and not unknown_words(message, self.named):
            # Written entirely in tag words ("rainbow outline, subtle glow, dark inside"): plan it exactly as
            # said. The model is for requests that need interpreting, and for follow-ups that build on a theme.
            exact = rule_plan(message, self.named)
            if changes_anything(exact): plan, source = dict(exact, reply='Planned exactly from your tag words.'), 'tags'
        if plan is None and available:
            model = model if model in names else names[0]
            turns = [dict(role='system', content=self.system_prompt())]
            for turn in (history or [])[-8:]:
                if isinstance(turn, dict) and turn.get('role') in ('user', 'assistant') and isinstance(turn.get('content'), str):
                    turns.append(dict(role=turn['role'], content=turn['content'][:4000]))
            hints = self.hints(message)
            turns.append(dict(role='user', content=message + ('\n\nSprites named in this request: ' + '; '.join(hints) if hints else '')))
            payload = dict(model=model, messages=turns, stream=False, format=SCHEMA, keep_alive='10m',
                           options=dict(temperature=0.2, num_ctx=8192, num_predict=1400, **MODEL_OPTIONS.get(model, {})))
            if next(m for m in available if m['name'] == model)['thinking']: payload['think'] = False
            try:
                answer = ollama('/api/chat', payload, timeout=300)
                content = answer.get('message', {}).get('content', '')
                found = re.search(r'\{.*\}', content, re.S)
                said = ' '.join([message] + [t['content'] for t in turns[1:-1] if t['role'] == 'user'])
                try: raw = json.loads(found.group(0) if found else content)
                except ValueError:
                    # One repair round: small models sometimes stop mid-object or add prose around it.
                    payload['messages'] = turns + [dict(role='assistant', content=content[:3000]), dict(role='user', content='That was not valid JSON for the schema. Reply with only the corrected JSON object.')]
                    content = ollama('/api/chat', payload, timeout=300).get('message', {}).get('content', '')
                    found = re.search(r'\{.*\}', content, re.S)
                    raw = json.loads(found.group(0) if found else content)
                plan, source = tame(clean_plan(raw), said), model
                named = [name for names_ in self.named(message)[0].values() for name in names_]
                if named and plan['operations'] and not any(op['match'] for op in plan['operations']) and not re.search(r'\b(?:everything|all|whole|entire|theme)\b', message.lower()):
                    # The request was about one named thing; a plan that forgot to name it would repaint a whole family.
                    for op in plan['operations']: op.update(targets=['everything'], match=named[:12])
            except (OSError, ValueError, KeyError, AttributeError) as exc:
                note = f'The local model {model} did not return a usable plan ({str(exc)[:120]}); used the keyword planner instead.'
        elif plan is None: note = 'Ollama is not running, so the keyword planner was used. Start Ollama for full prompts.'
        if plan is None: plan = rule_plan(message, self.named)
        if exe_palette.BLOCKED and (plan['ui_colors'] or plan['hide']):
            # Mitosis.exe refuses to run once edited: menu controls drawn from art are hidden through that art instead,
            # and game colors (set in its code) are explained rather than planned.
            plan, hidden = art_hide(plan, self.present(), message)
            notes = [hidden] if hidden else []
            if plan['ui_colors']: notes.append(exe_palette.COLORS_BLOCKED)
            plan = dict(plan, ui_colors={})
            if not plan['operations']: plan['reply'], notes = ' '.join(notes), []   # nothing left to do: the explanation is the answer
            note = ' '.join(([note] if note else []) + notes)
        if size: note = (note + ' ' if note else '') + SIZE_NOTE
        if motion: note = (note + ' ' if note else '') + MOTION_NOTE
        if not changes_anything(plan) and not plan['reply']:
            plan['reply'] = 'I could not turn that into a visual change. Try naming a color, a mood, or a part of the game.'
        return dict(plan=plan, source=source, note=note, preview=self.preview(plan, scope) if changes_anything(plan) else None)

    def matches(self, op, row, family):
        if op['targets'] != ['everything']:
            if family not in op['targets']: return False
        elif family in QUIET: return False
        name = row['name'].lower()
        if op['match'] and not any(fnmatch.fnmatch(name, pattern.lower()) for pattern in op['match']): return False
        return not any(fnmatch.fnmatch(name, pattern.lower()) for pattern in op['exclude'])

    def scope(self, keys):
        """The checked assets as catalog keys, widened to their identical copies and, for a sheet, its sprites. None = no limit."""
        rows, chosen = self.lab.catalog.rows, set()
        for key in keys or []:
            if key not in rows: continue
            for twin in self.lab.catalog.copies.get(rows[key].get('dup', ''), []) or [key]:
                chosen.add(twin)
                chosen.update(self.lab.catalog.children.get(twin, []))
        return chosen or None

    def hit(self, op, row, family, scope=None, force=False):
        return (scope is None or row['key'] in scope) and (force or self.matches(op, row, family))

    def forced(self, plan, scope):
        """With a selection, "make the buttons red" on checked icons would do nothing; the checked art then takes every step."""
        return bool(scope) and not self.compile(plan, scope)

    def compile(self, plan, scope=None, force=False):
        """sheet key -> [(operation, rectangles or None for the whole sheet)]"""
        work = {}
        for sheet_key, sheet in self.sheets.items():
            steps = []
            for op in plan['operations']:
                if not sheet['members']:
                    if self.hit(op, sheet['row'], sheet['family'], scope, force): steps.append((op, None))
                    continue
                hits = [row for row, family in sheet['members'] if self.hit(op, row, family, scope, force)]
                named = scope is None and any(fnmatch.fnmatch(sheet['row']['name'].lower(), pattern.lower()) for pattern in op['match'])
                if (named or len(hits) == len(sheet['members'])) and not sheet['partial'] and (scope is None or sheet_key in scope): steps.append((op, None))
                elif hits: steps.append((op, [row['rect'] for row in hits]))
            if steps: work[sheet_key] = steps
        return work

    @staticmethod
    def render(pixels, steps, frames=()):
        """frames: every sprite rectangle on the sheet, so a whole-sheet gradient or glow still fits each sprite."""
        out = pixels.copy()
        for op, rects in steps:
            if spatial(op):
                # Each sprite is shaded inside its own rectangle, always from the art as it was before this
                # operation, so rectangles that share pixels agree and a glow never leaks into a neighbour.
                before = out.copy()
                if rects is None: out = shade(before, op)  # art outside any named rectangle uses the sheet as its frame
                for x, y, w, h in dict.fromkeys(tuple(r) for r in (frames if rects is None else rects)):
                    out[y:y+h, x:x+w] = shade(before[y:y+h, x:x+w], op)
                continue
            if rects is None:
                out = shade(out, op)
                continue
            mask = np.zeros(out.shape[:2], bool)
            for x, y, w, h in rects: mask[y:y+h, x:x+w] = True
            ys, xs = np.where(mask.any(axis=1))[0], np.where(mask.any(axis=0))[0]
            box = (slice(ys[0], ys[-1] + 1), slice(xs[0], xs[-1] + 1))
            region = out[box]
            # One pass over the union: sprites that share atlas pixels are not shaded twice.
            region[mask[box]] = shade(region, op)[mask[box]]
        return out

    def preview(self, plan, scope=None):
        force = self.forced(plan, scope)
        work = self.compile(plan, scope, force)
        lines, samples, seen = [], [], set()
        for index, op in enumerate(plan['operations']):
            sheets = regions = 0
            for steps in work.values():
                for step_op, rects in steps:
                    if step_op is op: sheets += 1; regions += len(rects) if rects else 0
            lines.append(dict(targets=op['targets'], match=op['match'], exclude=op['exclude'], effect=describe(op), sheets=sheets, regions=regions,
                              color=op['color'] or (op['glow_color'] if op['glow_color'] != 'auto' else '') or op['outline_color'], gradient=op['gradient']))
        showcase = ['buttons', 'logo', 'icons', 'cells', 'panels', 'menu_background', 'world_background', 'food', 'equipment', 'effects', 'fonts']
        for op in plan['operations']:
            # Show the most recognisable art each operation touches, one per family.
            candidates = {}
            for sheet_key, steps in work.items():
                if not any(step_op is op for step_op, _ in steps): continue
                sheet = self.sheets[sheet_key]
                for row, family in (sheet['members'] or [(sheet['row'], sheet['family'])]):
                    if row['key'] in seen or not self.hit(op, row, family, scope, force) or max(row['width'], row['height']) < 12: continue
                    # Colored art shows a recolor; plain white or grey art often looks the same afterwards.
                    plain = op['color_mode'] == 'tint' and family in ('icons', 'fonts', 'food', 'other', 'effects')
                    score = (sheet['row']['source'].startswith('cache/'), plain, abs(max(row['width'], row['height']) - 128))
                    slot = (family, row['name']) if scope else (family, '')  # a selection shows the checked sprites themselves
                    if slot not in candidates or score < candidates[slot][0]: candidates[slot] = (score, sheet_key, row)
            ranked = sorted(candidates.items(), key=lambda item: (showcase.index(item[0][0]) if item[0][0] in showcase else 99, item[1][0]))
            for (family, _), (_, sheet_key, row) in ranked[:3 if len(plan['operations']) > 1 else 6]:
                if len(samples) >= 12: break
                sheet, steps = self.sheets[sheet_key], work[sheet_key]
                seen.add(row['key'])
                pixels = np.asarray(self.lab.catalog._sheet(sheet_key))
                x, y, w, h = row.get('rect') or (0, 0, pixels.shape[1], pixels.shape[0])
                if w * h > 1024 * 1024: x, y, w, h = x, y, min(w, 512), min(h, 512)
                before = np.ascontiguousarray(pixels[y:y+h, x:x+w])
                if not (before[..., 3] > 0).any(): continue
                after = before
                for step_op, _ in steps:
                    if not sheet['members'] or self.hit(step_op, row, families_of(row, sheet['row']['name']), scope, force) or row is sheet['row']: after = shade(after, step_op)
                if np.array_equal(before, after): continue
                samples.append(dict(name=row['name'], key=row['key'], family=family, before=thumb(before), after=thumb(after)))
        ui = [dict(name=name, color=value, was=UI_COLORS[name][1], what=UI_COLORS[name][2]) for name, value in (plan.get('ui_colors') or {}).items()]
        hide = [dict(id=group, label=HIDE[group][0]) for group in plan.get('hide') or []]
        return dict(sheets=len(work), lines=lines, samples=samples, scoped=bool(scope), forced=force, ui_colors=ui, hide=hide)

    # ----- applying ------------------------------------------------------
    def base(self, key):
        """Index of the newest saved revision that is not a theme, or -1 for the original art."""
        history = self.lab.history(key)
        cursor = history['cursor']
        while cursor >= 0 and (history['versions'][cursor].get('recipe') or {}).get('theme'): cursor -= 1
        return cursor

    def base_pixels(self, key):
        cursor = self.base(key)
        if cursor < 0: return np.asarray(self.lab.catalog.image(key))
        version = self.lab.history(key)['versions'][cursor]
        with Image.open(self.lab.root / 'revisions' / key / (version['id'] + '.png')) as image: return np.asarray(image.convert('RGBA'))

    def rewind(self, key):
        """Step a sheet back out of its theme revisions. Returns True when something changed."""
        target, changed = self.base(key), False
        while self.lab.history(key)['cursor'] > target:
            self.lab.step(key, self.lab.revision(key), -1, detail=False)
            changed = True
        return changed

    def apply(self, plan, prompt='', only=None):
        """only: catalog keys of checked assets; when given, nothing else is changed."""
        plan, scope = clean_plan(plan), self.scope(only)
        if exe_palette.BLOCKED: plan = dict(art_hide(plan, self.present(), prompt)[0], ui_colors={})
        if only and not scope: raise ValueError('The selected assets are no longer in the library.')
        if not changes_anything(plan): raise ValueError('This plan changes nothing.')
        with self.lock:
            if any(j['state'] == 'running' for j in self.jobs.values()): raise ValueError('A theme is already being applied.')
            job = dict(id=uuid.uuid4().hex, state='running', progress=0, total=0, message='Planning…', changed=0, errors=[])
            self.jobs[job['id']] = job
        threading.Thread(target=self._run, args=(job, plan, str(prompt)[:2000], scope), daemon=True).start()
        return dict(job)

    def _run(self, job, plan, prompt, scope=None):
        try:
            work = self.compile(plan, scope, self.forced(plan, scope)) if plan['operations'] else {}
            # A whole theme replaces the previous one everywhere; a theme for checked assets, or a request that only
            # sets the game colors, leaves every other sheet as it is.
            themed = [] if scope or not plan['operations'] else [key for key in self.sheets if (self.lab.current(key) or {}).get('recipe', {}).get('theme')]
            order = list(dict.fromkeys(list(work) + themed))
            job.update(total=len(order), message='Shading sprite sheets…')
            batch = dict(id=job['id'], prompt=prompt, plan=plan, at=time.time(), items=[])
            if plan['ui_colors']:
                before = self.lab.palette()
                after = self.lab.set_palette(dict(before, **palette_for(plan)))
                batch['palette'] = dict(before=before, after=after)
            if plan['hide']:
                before = self.lab.hidden()
                after = self.lab.set_hidden(before + plan['hide'])
                batch['hide'] = dict(before=before, after=after)
            for index, key in enumerate(order):
                name = self.sheets[key]['row']['name']
                try:
                    with self.lab.lock:
                        before = self.lab.revision(key)
                        if key not in work:
                            if self.rewind(key): batch['items'].append(dict(key=key, before=before, after=self.lab.revision(key)))
                            continue
                        source = self.base_pixels(key)
                        result = self.render(source, work[key], [row['rect'] for row, _ in self.sheets[key]['members']])
                        if scope:
                            # Only the checked rectangles are taken from the new render; the rest of the sheet
                            # keeps its current look, including any theme already on it.
                            current = np.asarray(self.lab.image(key))
                            if not any(rects is None for _, rects in work[key]):
                                merged = current.copy()
                                for _, rects in work[key]:
                                    for x, y, w, h in rects: merged[y:y+h, x:x+w] = result[y:y+h, x:x+w]
                                result = merged
                            source = current
                        # The earlier theme stays in history beneath this one, so Undo returns to it.
                        if np.array_equal(result, source):
                            if not scope: self.rewind(key)
                        else:
                            self.lab.commit(key, Image.fromarray(result, 'RGBA'), dict(theme=job['id'], prompt=prompt, operations=[op for op, _ in work[key]]), persist=False)
                        if self.lab.revision(key) != before:
                            batch['items'].append(dict(key=key, before=before, after=self.lab.revision(key)))
                            job['changed'] += 1
                except (ValueError, OSError) as exc:
                    job['errors'].append(f'{name}: {exc}')
                job.update(progress=index + 1, message=f'{index + 1} / {len(order)} · {name}')
                if index % 40 == 39:
                    with self.lab.lock: self.lab.persist()
            with self.lab.lock: self.lab.persist()
            data = self.load()
            data['batches'].append(batch)
            self.store(data)
            same = len(order) - job['changed'] - len(job['errors'])
            note = f" ({same} already looked right)" if same > 0 else ''
            saved = ([f"{job['changed']} sheet{'s' if job['changed'] != 1 else ''}{note}"] if plan['operations'] or order else []) + \
                    (['the game colors'] if plan['ui_colors'] else []) + \
                    ([f"{len(plan['hide'])} hidden menu control{'s' if len(plan['hide']) != 1 else ''}"] if plan['hide'] else [])
            job.update(state='complete', saved=job['changed'], unchanged=max(0, same), colors=bool(plan['ui_colors'] or plan['hide']),
                       message=f"Saved {', '.join(saved[:-1]) + ' and ' + saved[-1] if len(saved) > 1 else saved[0]}. Install it to see it in Mitosis.")
        except Exception as exc:  # A background job must always reach a final state.
            with self.lab.lock: self.lab.persist()
            job.update(state='failed', message=str(exc))

    def job(self, job_id):
        if job_id not in self.jobs: raise ValueError('Unknown theme job')
        return dict(self.jobs[job_id])

    def undo(self):
        """Take the latest theme back off: every sheet returns to the revision it had before."""
        with self.lock, self.lab.lock:
            if any(j['state'] == 'running' for j in self.jobs.values()): raise ValueError('Wait for the theme to finish applying.')
            data = self.load()
            batch = next((b for b in reversed(data['batches']) if not b.get('undone')), None)
            if not batch: raise ValueError('There is no theme to undo.')
            reverted = skipped = 0
            for item in batch['items']:
                if self.lab.revision(item['key']) != item['after']:
                    skipped += 1  # Edited by hand since; leave the newer work alone.
                    continue
                history = self.lab.history(item['key'])
                ids = ['original'] + [v['id'] for v in history['versions']]
                if item['before'] not in ids: skipped += 1; continue
                wanted = ids.index(item['before']) - 1
                while history['cursor'] != wanted:
                    self.lab.step(item['key'], self.lab.revision(item['key']), 1 if history['cursor'] < wanted else -1, detail=False)
                    history = self.lab.history(item['key'])
                if wanted >= 0 and item['key'] not in self.lab.state['staged']: self.lab.state['staged'].append(item['key'])
                reverted += 1
            colors = batch.get('palette')
            if colors and self.lab.palette() == colors['after']: self.lab.set_palette(colors['before'])
            hidden = batch.get('hide')
            if hidden and self.lab.hidden() == hidden['after']: self.lab.set_hidden(hidden['before'])
            batch['undone'] = time.time()
            self.lab.persist()
            self.store(data)
            return dict(reverted=reverted, skipped=skipped, prompt=batch['prompt'])
