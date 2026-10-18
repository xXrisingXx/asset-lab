"""Theme the whole game from a sentence.

A local Ollama model turns the request into a small plan: which art families to touch and
how to shade them. The plan is applied to whole sprite sheets, always from the art as it was
before any theme, so a follow-up request replaces the previous theme instead of stacking on it.
Without Ollama a keyword parser produces the same kind of plan for simple requests.
"""
import base64
import hashlib
import colorsys
import fnmatch
import io
import json
import random
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
import fonts
import inspire
import pictures
import runtime
import settings as labsettings

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
    turn change changed replace swap switch show hide remove delete bigger smaller larger flash blink mode
    create creates design designs build builds apply little tiny mini
    top bottom left right center centre middle shadow shadows circle circles square squares round rounded next close stop battle night day'''.split())
# Names players use for sprites that no file is called: the spawner is "the red virus" (the game paints it red in maps);
# virus.png is shown in light mode and light_virus.png in dark mode.
ALIASES = {'red virus': ['spawner.png'], 'spawner virus': ['spawner.png'], 'dark mode virus': ['light_virus.png'],
           'light mode virus': ['virus.png'], 'normal virus': ['virus.png']}
# Words every name for a control has ("Play button", "New Players mode icon"): too common to pick out one sprite.
GENERIC = {'button', 'buttons', 'icon', 'icons', 'badge', 'badges', 'mode', 'menu', 'main', 'list', 'screen', 'players', 'player'}
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
    # "Cool" alone means awesome ("make it look cool", see VAGUE); only cool colors or tones shift the hue.
    (r'warm(?:er)?', dict(hue_shift=-12, saturation=1.1)), (r'cool[- ](?:colou?rs?|tones?|toned|palette|hues?)|cold(?:er)?', dict(hue_shift=14, saturation=1.05))]
# Praise without a look in it ("make it cool", "something epic", "glow it up"): the model invents a bold theme, the
# image model can draw one, and without either a curated look is picked (CREATIVE_LOOKS) instead of a timid nudge.
VAGUE = (r'\b(?:cool(?:er)?|awesome|epic|sick|dope|fresh|amazing|beautiful|pretty|prettier|badass|sleek(?:er)?|modern|unique|interesting|'
         r'fun(?:ner)?|crazy|insane|wild|lit|stylish|aesthetic|vibes?|better|nicer|best|hype|swag(?:gy)?|surprise me|whatever|anything|'
         r'glow ?up|makeover|upgrade|improve|spice (?:it |this |the game )?up|level (?:it )?up|revamp|refresh(?:ed)?|new look|look good|look great)\b')
CREATIVE_LOOKS = ('synthwave gradient on the buttons and panels with a neon glow and a chrome logo',
                  'cyberpunk theme with neon outlines and a strong glow on the icons',
                  'glossy gold buttons with a subtle glow and dark panels',
                  'ocean gradient on the buttons and panels with a soft glow and glossy icons',
                  'royal theme with gold outlines and a subtle glow',
                  'galaxy gradient on the buttons with a holographic outline on the icons')
# Starting ideas handed to the model with an open-ended request, so "make it cool" twice gives two different looks.
CONCEPTS = ('molten lava: black panels, orange-to-red glowing gradients, ember glow', 'arctic ice: pale blue gradients, white frosty outlines, cold glow',
            'royal gold: deep purple panels, glossy gold buttons and outlines', 'toxic waste: acid green gradients on black, sickly green glow',
            'vaporwave sunset: pink-to-orange-to-purple gradients, soft glow', 'deep ocean: navy to teal gradients, aqua outlines, glossy',
            'galaxy: indigo-to-magenta gradients, starlight sparkle, white outlines', 'candy pop: bright pastel gradients, glossy finish, white outlines',
            'retro arcade: bold primary colors, pixelated icons, scanlines', 'cherry blossom: soft pink gradients, white outlines, gentle glow',
            'gold and black luxury: black panels, gold gradients and gold outlines', 'neon jungle: emerald gradients, lime outlines, strong green glow')
# Words about the tool, not the game ("using image generation", "with the AI"): never a sprite name, and asking for
# the image model this way turns drawing on for that request.
DRAW_ASK = (r'\b(?:using|use|with|via|through|by)\s+(?:the\s+|an?\s+)?(?:ai\s+|local\s+)?(?:image\s*(?:generation|generator|gen|model|ai)|ai\s+(?:art|images?|drawing)|'
            r'drawing|image\s*model)\b|\b(?:image\s*generation|generate\s+(?:new\s+)?(?:art|images?)|draw\s+new\s+art)\b')
TOOL_WORDS = set('image images generation generator generate generated picture pictures art artwork ai drawing draw drawn model using cool awesome epic'.split())


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


# One request can hold several asks: "theme it halloween and make the names spooky".
# Potions, power-ups and equipment: the item sheet the game downloads from its server and restores whenever it changed.
SERVER_ART = r'\b(?:potions?|vials?|super ?glue|power-?ups?|pows?|equipment|equips?|items?|gear|weapons?|armou?r|gems?)\b'
SERVER_NOTE = ('Potions, power-ups and equipment come from the game\'s server: Mitosis downloads a fresh copy whenever its '
               'file was changed, so they can\'t be restyled. Everything else in the menus and the game can.')
# The red virus lives in the same downloaded item file (see lab_core.LOCKED): the game puts its own copy back.
RED_VIRUS = r'\b(?:red virus(?:es)?|spawners?)\b'
RED_VIRUS_NOTE = ('The red virus (the spawner) comes from the game\'s server too: Mitosis downloads a fresh copy before a '
                  'match, so a new look for it won\'t show in the game.')
# A request for a whole look named after something ("a bmw theme", "bmw style"), not a change to one part.
# The whole game, not one part ("make my game look like gta", "everything should be lego").
WHOLE = r'(?:(?:i want |make )?(?:it|everything|all|all of it|(?:the |my |this )?(?:whole |entire )?(?:game|look|ui|menu|menus|theme|style)))'
# What asks for the whole game in a look named after something ("a bmw theme"), unlike "the coin in a bmw style".
GAME_THEME = r'\b(?:theme[ds]?|reskin|makeover|overhaul)\b'
THEMED = r'\b(?:theme[ds]?|style|aesthetic|vibes?|reskin|edition|version|makeover)\b'
CLAUSES = r'[.;\n]+|,?\s+(?:and then|then|also|plus)\s+|,?\s+and\s+(?=(?:make|give|put|add|draw|turn|theme|redesign|redraw|paint|stick|change|set)\b)'
# Text: requests about the game's fonts become a font style with a preview ("player names black with a white glow").
FONT_WORDS = (r'\b(?:fonts?|typefaces?|typography|lettering|player names?|(?:my|your|our|their|his|her) (?:user ?)?names?|nick ?names?|nick ?text\w*|usernames?|names|name text|name tags?|'
              r'menu text|text (?:style|font|look|colou?rs?)|geosans\w*|expinfo\w*)\b')
# "Make the text red", "rainbow letters": the game's text too, when the same words name no other part ("the buttons with
# white text" is about the buttons).
TEXT_ALONE = r'\b(?:text|texts|writing|letters|words|wording)\b'
OTHER_PARTS = rf"\b(?:{'|'.join(v for k, v in WORDS.items() if k != 'fonts')})\b"
TEXT_TARGETS = [('geosans_stroke_80', r'\boutlined (?:menu )?text|\bgeosans_?stroke'), ('geosans_80', r'\b(?:menu|ui|button|hud) (?:text|font|letters|writing)|\bgeosans'),
                ('expinfo', r'\b(?:xp|exp|small) numbers?\b|\bexpinfo'), ('nicktext', r'.')]
# "Make the names say Novu": what text says is not in a font. Player names are typed by players, menu words are in the game.
TEXT_WORDING = r'\b(?:to say|says?|saying|to read|reads?|spell(?:s|ed|ing)?|rename|different words?|other words?)\b'
# A request that names no look ("change the font", "use a different font"): a first look to start from, a different one
# each time, and every other look a click away.
STARTER_LOOKS = ['Gold', 'Neon blue', 'Fire', 'Comic', 'Chrome', 'Galaxy', 'Bold outline', 'Ice', 'Graffiti', 'Rainbow', 'Sports jersey', 'Toxic']


# A new font: what its letters are made of ("a font made of lava", "letters covered in sprinkles", "a carbon fiber texture
# font"). Image AI draws it as a tile and the letters of a real typeface are filled with it (design.start_texture).
TEXTURE_ASK = (r"\b(?:made (?:out )?of|made from|out of|built (?:out )?of|carved (?:out )?(?:of|from)|covered (?:in|with)|filled with|"
               r"textured (?:like|with)|based on|inspired by|in the style of)\s+(?P<what>[^.,;!?]{2,60})")
TEXTURE_WORD = r"\b(?P<what>[a-z][\w' -]{1,40}?\s+(?:texture[sd]?|pattern(?:ed)?|print))\b"
NEW_FONT = (r'\b(?:new|own|custom|unique|original|brand[- ]new|different|fresh)\s+(?:\w+\s+){0,2}(?:fonts?|typefaces?|lettering)\b|'
            r'\b(?:create|design|invent|generate|draw|make)\s+(?:me\s+|us\s+)?(?:a|an|my|our)\s+(?:\w+\s+){0,3}(?:fonts?|typefaces?|lettering)\b')
# Typefaces a new font can start from: shipped with Windows 10 and 11, so it works on every player's PC.
FONT_FAMILIES = ['Arial', 'Arial Black', 'Bahnschrift', 'Segoe UI', 'Segoe UI Black', 'Segoe Print', 'Segoe Script', 'Ink Free', 'Impact', 'Georgia',
                 'Consolas', 'Comic Sans MS', 'Gabriola', 'Franklin Gothic Medium', 'Palatino Linotype', 'Trebuchet MS', 'Verdana', 'MV Boli',
                 'Courier New', 'Chiller', 'Sitka']
# Under a texture the letters need an edge to read on any background: a heavy typeface, a dark outline and a soft shadow.
TEXTURE_BASE = dict(font=fonts.HEAVY, outline='#101216', outline_width=3, shadow='#000000', shadow_x=0, shadow_y=3, shadow_blur=3, shadow_opacity=0.7)


def texture_subject(text):
    """What a request says a font's letters are made of ("lava", "chocolate with sprinkles"), or ''."""
    low = re.sub(TEXT_REFERENCE, ' ', str(text).lower())
    found = re.search(TEXTURE_ASK, low) or re.search(TEXTURE_WORD, low)
    if not found: return ''
    what = re.sub(r"\b(?:textures?|textured|fonts?|typefaces?|letters?|lettering|text|names?|for|the|player|players|my|our|please|pls|thanks?|on|in|it|them)\b", ' ', found.group('what'))
    what = re.sub(r'^\W*(?:an?|some|with)\s+|\s+(?:and|or|with)\s*$', '', re.sub(r'\s+', ' ', what).strip())
    return what if 2 <= len(what) <= 60 and re.search(r'[a-z]{3}', what) else ''


def texty(low):
    """Whether these (lowercase) words are about the game's text."""
    return bool(re.search(FONT_WORDS, low) or (re.search(TEXT_ALONE, low) and not re.search(OTHER_PARTS, low)))


def font_of(row):
    """The game font a library asset belongs to ('nicktext' for nicktext_0.png and each of its letters), or ''."""
    if not row or not (row.get('kind') == 'glyph' or row.get('group') == 'Fonts'): return ''
    found = re.fullmatch(r'(.+?)(?:_\d+)?\.(?:png|fnt)', str(row.get('source') or row.get('name') or '').rsplit('/', 1)[-1], re.I)
    return found.group(1).lower() if found else ''
FACE_WORDS = [(r'\b(?:condensed|tall)\b', 'Bahnschrift|Bold Condensed'), (r'\bcomic\b', 'Comic Sans MS|Bold'),
              (r'\b(?:spooky|horror|creepy|halloween|scary)\b', 'Chiller|Regular, Ink Free|Regular'), (r'\b(?:mono|monospace|typewriter|code)\b', 'Consolas|Bold, Cascadia Code|Bold'),
              (r'\b(?:handwritten|handwriting|script|cursive|signature)\b', 'Segoe Script|Bold, Segoe Print|Bold'), (r'\b(?:blocky|heavy|chunky)\b', 'Arial|Black')]
# The text look a mood calls for: "christmas names" uses it, and a whole-game mood ("halloween theme") offers it for the
# player names next to the theme plan. First match wins, so the more specific moods come first.
MOOD_TEXT = [(r'halloween|spooky|creepy|scary|horror|pumpkins?|witch(?:es|y)?|zombies?', 'Pumpkin'), (r'christmas|xmas|festive|santa|candy canes?', 'Christmas'),
             (r'ghosts?|ghostly|haunted', 'Ghost'), (r'bloody|blood|gory|gore|vampires?', 'Blood'), (r'winter|wintery|snowy?|frost|frosty|frozen|blizzard', 'Frost'),
             (r'ice|icy|glaciers?', 'Ice'), (r'ocean|sea|underwater|aquatic|beach|deep blue', 'Ocean'), (r'synthwave|vaporwave|retrowave|outrun|80s', 'Synthwave'),
             (r'cyberpunk|cyber|hacker|futuristic|sci-?fi', 'Cyber'), (r'neon', 'Neon pink'), (r'gold|golden|luxury|luxurious|bling', 'Gold'),
             (r'royal|regal|kingly|king|queen|majestic', 'Royal'), (r'lava|volcan(?:o|ic)|magma|molten', 'Lava'),
             (r'fire|fiery|flames?|flaming|burning|inferno|hellfire', 'Fire'), (r'galaxy|space|cosmic|cosmos|nebula|starry|universe', 'Galaxy'),
             (r'toxic|radioactive|nuclear|acid|poison(?:ous)?|biohazard', 'Toxic'), (r'slime|slimy|goo|gooey', 'Slime'), (r'valentines?|romantic|romance', 'Valentine'),
             (r'rainbow|pride|colou?rful|lgbtq?', 'Rainbow'), (r'forest|nature|natural|jungle|woodland|earthy', 'Forest'), (r'candy|sweets|bubblegum', 'Bubblegum'),
             (r'pastel|cute|kawaii', 'Pastel'), (r'easter|spring(?:time)?', 'Spring'), (r"st\.? ?patrick'?s?|lucky|clover|shamrock|irish", 'Lucky'),
             (r'chrome|metal(?:lic)?|steel|silver', 'Chrome'), (r'aurora|northern lights', 'Aurora'), (r'sunset|summer|tropical|sunny', 'Sunset'),
             (r'storm(?:y)?|thunder|lightning', 'Storm'), (r'electric', 'Electric'), (r'hologram|holographic|holo|iridescent', 'Hologram'),
             (r'diamonds?|crystal', 'Diamond'), (r'retro|arcade|8-?bit', 'Retro arcade'), (r'western|cowboy|wild west', 'Wild west')]
WHOLE_GAME = r'\b(?:theme[ds]?|everything|whole|entire|all of it|the game|vibes?)\b'
TEXT_COLOR = r'(?:#[0-9a-f]{6}|\b(?:(?:light|pale|dark|deep|bright)\s+)?(?:' + '|'.join(sorted(COLORS, key=len, reverse=True)) + r')\b)'
TEXT_GRADIENT = r'(?:gradients?|fad(?:e|es|ed|ing)|ombre|blend(?:s|ed|ing)?)'
# Colors in a row: "red, white and blue", "red to gold", "red-to-blue", "red white and blue", "blue green gradient"; "sky blue" alone is one color.
TEXT_SEP = rf'(?:\s*,\s*(?:and\s+|then\s+)?|\s+(?:to|into|then|and|through|via)\s+|\s*-\s*(?:to\s*-\s*)?|\s+(?={TEXT_COLOR}\s*(?:,|and\b|{TEXT_GRADIENT})))'
# Which way a gradient runs, in the angle the fonts tool uses: 0 = left to right, 90 = top to bottom.
TEXT_DIRECTIONS = [(r'left[- ]to[- ]right|horizontal(?:ly)?|sideways', 0), (r'right[- ]to[- ]left', 180), (r'top[- ]to[- ]bottom|vertical(?:ly)?|downwards?|up and down', 90),
                   (r'bottom[- ]to[- ]top|upwards?', 270), (r'diagonal(?:ly)?', 45)]


def text_color(name):
    """A color a request names: true black for letters (not the UI palette's soft black); "light" and "pale" mix in white,
    "dark" and "deep" black."""
    shade, _, base = name.strip().rpartition(' ')
    color = '#000000' if base == 'black' else hex_color(base)
    toward = {'light': 'ffffff', 'pale': 'ffffff', 'dark': '000000', 'deep': '000000'}.get(shade)
    return '#' + ''.join(f'{round(int(color[i:i + 2], 16) * 0.55 + int(toward[i - 1:i + 1], 16) * 0.45):02x}' for i in (1, 3, 5)) if toward else color


def same_hue(a, b):
    """Whether two colors are the same kind ("emerald" and "green"): a color named right after a look ("emerald green
    names", "ocean blue") is then the look's own, not a new fill."""
    (h1, s1, _), (h2, s2, _) = (Image.new('RGB', (1, 1), c).convert('HSV').getpixel((0, 0)) for c in (a, b))
    return max(s1, s2) < 40 if min(s1, s2) < 40 else min(abs(h1 - h2), 256 - abs(h1 - h2)) < 32   # grays match grays; hues within 45 degrees


def font_named(low, faces):
    """A typeface the request names, with where: 'arial black' (a family and its style), 'comic sans' (Comic Sans MS),
    'impact'. Its words are then neither colors nor looks."""
    names = {}
    for f in faces:
        for alias in {f['family'].lower(), re.sub(r'\s+(?:ms|mt|ui)$', '', f['family'].lower())}:
            if f['style'].lower() not in ('regular', 'normal', 'roman', 'book'): names.setdefault(f"{alias} {f['style'].lower()}", f"{f['family']}|{f['style']}")
            if len(alias) >= 4: names.setdefault(alias, f['family'])
    for name in sorted(names, key=len, reverse=True):
        found = re.search(rf'(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])', low)
        if found: return names[name], found.span()
    return '', None


def text_look(low):
    """The look a request names ("gold", "neon sign", "fire and ice") or, failing that, the one its mood calls for
    ("christmas", "toxic"): (look name, where it was said), or None. "Hollow" is also how letters are drawn, so in
    "hollow neon names" the mood picks the look and the letters come out hollow."""
    best = None
    for name, preset in fonts.PRESETS.items():
        found = preset and re.search(rf"\b{re.escape(name.lower().replace('&', 'and'))}\b", low)
        if found and (best is None or len(name) > len(best[0])): best = (name, found.span())
    mood = next(((name, found.span()) for pattern, name in MOOD_TEXT for found in [re.search(rf'\b(?:{pattern})\b', low)] if found), None)
    return mood if mood and (not best or best[0] == 'Hollow') else best


def font_style(text, faces):
    """A font style from a request: a look by name or mood, a typeface (by name or by feel), the fill or a gradient of
    up to eight colors in any direction, outline, glow, shadow, hollow or see-through letters, width and more."""
    low = re.sub(r'\boutlined (?:menu )?text\b', ' ', re.sub(r'\s*&\s*', ' and ', text.lower()))   # which font, not an outline
    low = re.sub(r'\b(pink|blue|green)\s+neon\b', r'neon \1', low)   # "green neon" is the Neon green look (same length: places stay)
    any_color, used = f'({TEXT_COLOR})', []
    blank = lambda: ''.join(' ' if any(a <= i < b for a, b in used) else ch for i, ch in enumerate(low))

    def take(pattern):
        """The color named with an effect ("white glow"): '' when the effect has no color, None when it is not asked for.
        A mention with a color wins ("outline only, with a pink outline")."""
        every = list(re.finditer(pattern, low))
        if not every: return None
        found = next((f for f in every if f.group(1)), every[0])
        if found.group(1): used.append(found.span(1))
        return text_color(found.group(1)) if found.group(1) else ''
    # A color named with an effect is the effect's: "a gold outline" is not the Gold look.
    glow = take(rf'{any_color}?\s*(?:glow|halo|glowing)')
    outline = take(rf'{any_color}?\s*(?:outlines?|borders?|strokes?|edges?)')
    shadow = take(rf'{any_color}?\s*(?:drop\s+)?shadows?')
    font, said = font_named(blank(), faces)                    # "arial black" is a font, not black letters
    if said: used.append(said)
    # The letters' colors. Two or more in a row blend: "red, white and blue", "a red-to-gold fade", "a gradient at 30
    # degrees from red to blue" (the row nearest the word gradient). Two joined by "and" without the word are the
    # letters and their outline: "gold and black names".
    word = re.search(rf'\b{TEXT_GRADIENT}\b', low)
    runs = list(re.finditer(rf'{TEXT_COLOR}(?:{TEXT_SEP}{TEXT_COLOR})+', blank()))
    run = min(runs, key=lambda m: abs(m.start() - word.start()) if word else m.start()) if runs else None
    colors = [text_color(c) for c in re.findall(TEXT_COLOR, run.group(0))][:fonts.MAX_STOPS] if run else []
    pair = len(colors) == 2 and not word and outline is None and re.search(r',|\band\b', run.group(0))
    stops = fonts.even(*colors) if colors and not pair else []
    if run: used.append(run.span())
    look = text_look(blank())                                  # "gold names" is the Gold look, not a flat gold fill
    style = dict(fonts.PRESETS[look[0]], look=look[0]) if look else {}   # 'look' only names the starting point; styles ignore it
    if look:
        used.append(look[1])
        tint, own = re.match(rf'\s+({TEXT_COLOR})', low[look[1][1]:]), style['stops'][len(style['stops']) // 2][1] if style.get('stops') else style.get('fill', '#ffffff')
        if tint and same_hue(text_color(tint.group(1)), own): used.append((look[1][1], look[1][1] + tint.end()))   # "ocean blue" is the Ocean look
    rest = blank()
    if font: style['font'] = font
    elif not style.get('font'): style['font'] = next((face for pattern, face in FACE_WORDS if re.search(pattern, low)), '')
    if not colors:
        ramp = re.search(rf"\b({'|'.join(GRADIENTS)})\s+(?:colou?red\s+)?{TEXT_GRADIENT}", rest)   # "sunset gradient"; a look's own colors win
        if ramp: stops = fonts.even(*GRADIENTS[ramp.group(1)][:fonts.MAX_STOPS])
    fill = re.search(any_color, rest)
    neon = style.get('glow') and style.get('glow') == style.get('outline') and sum(int(style.get('fill', '#ffffff')[i:i + 2], 16) for i in (1, 3, 5)) > 600
    if pair: style.update(fill=colors[0], stops=[]); outline = colors[1]
    elif stops: style['stops'] = stops
    elif fill and neon: style.update(outline=text_color(fill.group(1)), glow=text_color(fill.group(1)))   # "red neon": the tube's color
    elif fill and word: style['stops'] = fonts.even(text_color(f'light {fill.group(1)}'), text_color(fill.group(1)))   # "a blue gradient"
    elif fill: style.update(fill=text_color(fill.group(1)), stops=[])
    elif word and not style.get('stops'): style['stops'] = fonts.even('#ffffff', '#7fd8ff')   # "a gradient", no colors named
    direction = next((angle for pattern, angle in TEXT_DIRECTIONS if re.search(rf'\b(?:{pattern})\b', low)), None)
    degrees = re.search(r'\b(\d{1,3})\s*(?:°|-?\s*deg(?:ree)?s?\b)', low)
    if degrees or direction is not None: style['angle'] = int(degrees.group(1)) % 360 if degrees else direction
    main = style['stops'][len(style['stops']) // 2][1] if style.get('stops') else style.get('fill', '#ffffff')
    dark = sum(int(main[i:i + 2], 16) for i in (1, 3, 5)) < 200
    thick = re.search(r'\b(?:thick|big|fat)\s+(?:\w+\s+)?(?:outline|border)', low)
    # An effect named without a color keeps the look's own ("gold names with a glow", "bold outline names").
    if glow is not None and (glow or not style.get('glow')):
        style.update(glow=glow or ('#ffffff' if dark else main), glow_size=8, glow_strength=1.4)
        if dark and outline is None and not style.get('outline'): style.update(outline=style['glow'], outline_width=2)   # dark letters read with a light edge
    if outline is not None and (outline or thick or not style.get('outline')):
        style.update(outline=outline or style.get('outline') or ('#ffffff' if dark else '#000000'), outline_width=4 if thick else max(3, style.get('outline_width') or 0) if outline == '' else 3)
    if shadow is not None and (shadow or not style.get('shadow')): style.update(shadow=shadow or '#000000', shadow_x=2, shadow_y=4, shadow_blur=3, shadow_opacity=0.8)
    if re.search(r'\b(?:hollow|outlines? only|only (?:an? |the )?outlines?|outline-only|empty letters|no fill|unfilled)\b', low):
        style['fill_opacity'] = 0.0                            # only the outline is drawn: it takes the letters' color unless one was named
        keep = style.get('outline') and not (fill or colors) and (style.get('glow') == style.get('outline') or look and look[0] == 'Hollow')   # neon tubes, Hollow
        if outline is None and not keep:
            if style.get('outline') and style.get('stops'): style.update(outline2=style['outline'], outline2_width=2)   # the look's own edge frames it
            style.update(outline=main, outline_width=max(3, style.get('outline_width', 0)))
    elif re.search(r'\b(?:see-?through|translucent|semi-?transparent|half[- ]transparent|transparent|ghostly)\b', low): style['fill_opacity'] = 0.5
    shape = blank()                                            # what the font's and the look's own names say is theirs ("arial bold", "wide & loud")
    if re.search(r'\b(?:extra|very|super|really) (?:wide|stretched|broad)\b', shape): style['stretch'] = 1.6
    elif re.search(r'\b(?:wide|wider|stretched|extended|broad)\b', shape): style['stretch'] = 1.3
    elif re.search(r'\b(?:extra|very|super|really) (?:narrow|skinny|squished|squeezed|thin)\b', shape): style['stretch'] = 0.65
    elif re.search(r'\b(?:narrow|narrower|squished|squeezed|skinny|compressed|slim|thin (?:letters|text|font))\b', shape): style['stretch'] = 0.8
    if re.search(r'\b(?:extra bold|thicker|fatter)\b', shape): style['bold'] = 4
    elif re.search(r'\bbold\b', shape): style['bold'] = 2
    if re.search(r'\b(?:italic|slanted|leaning|tilted)\b', shape): style['italic'] = 0.2
    if re.search(r'\b(?:all caps|caps|capitals?|uppercase|capital letters)\b', shape): style['caps'] = True
    if re.search(r'\bhuge|massive\b', shape): style['size'] = 1.45
    elif re.search(r'\b(?:bigger|larger)\b', shape): style['size'] = 1.2
    elif re.search(r'\b(?:smaller|tiny|little)\b', shape): style['size'] = 0.85
    return style


def color_word(color):
    """The nearest everyday name for a color, for replies ("#fff3b0" reads as yellow)."""
    if color == '#000000': return 'black'
    near = lambda name: sum((int(COLORS[name][i:i + 2], 16) - int(color[i:i + 2], 16)) ** 2 for i in (1, 3, 5))
    return min((n for n in COLORS if n not in ('gray', 'aqua')), key=near)


def describe_text(style, face, look='', based=''):
    """A text style in plain words: "Impact, all caps, black letters, white outline, white glow"; look: the ready-made look
    it is, based: the one it started from."""
    say = color_word
    direction = {0: 'left to right', 45: 'diagonal', 90: 'top to bottom', 180: 'right to left', 270: 'bottom to top'}.get(int(style['angle']), f"at {style['angle']:g}°")
    colors = list(dict.fromkeys(say(c) for _, c in style['stops']))
    letters = (f"{' to '.join(colors)} gradient letters ({direction})" if len(colors) <= 4 else f"{len(style['stops'])}-color gradient letters ({direction})") if style['stops'] else f"{say(style['fill'])} letters"
    if style['fill_opacity'] == 0: letters = 'hollow letters'
    elif style['fill_opacity'] < 1: letters = f"see-through {letters} ({round(style['fill_opacity'] * 100)}%)"
    parts = ['all caps' if style['caps'] else '', 'wide' if style['stretch'] > 1.05 else 'narrow' if style['stretch'] < 0.95 else '', letters,
             f"{say(style['outline'])} outline" if style['outline'] else '', f"{say(style['outline2'])} second outline" if style['outline2'] else '',
             f"{say(style['glow'])} glow" if style['glow'] else '', f"{say(style['shadow'])} shadow" if style['shadow'] else '']
    details, face = ', '.join(p for p in parts if p), face or "the game's own letters"
    return f'the {look} look ({face}): {details}' if look else f'the {based} look with your changes ({face}): {details}' if based else f'{face}, {details}'


# "...to match our other themes like the mitosis menu text": text named only as an example, not as what to change.
TEXT_REFERENCE = r'\b(?:like|match(?:es|ing)?|same as|similar to|as)\b(?:\s+(?:our|the|my|other|mitosis|game|\w+s?)){0,4}\s+' + FONT_WORDS[2:]
THIS = r'\b(?:this|these|it|that|those|selected|checked)\b'


def about_text(clause, only=None):
    """Whether a clause asks to change the game's text (not just mentions it as an example). With assets checked, a clause
    about "this"/"it" is about those assets, whatever text it mentions."""
    low = re.sub(TEXT_REFERENCE, ' ', clause.lower())
    if not texty(low): return False
    return not (only and re.search(THIS, low))


def text_split(clause):
    """A clause about the game's text less the parts that ask for something else: "christmas theme and red names" is a
    theme and a text style. Returns (the text request, [the other requests]); "names in red, white and blue" stays whole."""
    parts = [p.strip() for p in re.split(r'\s*,\s*|\s+and\s+', clause) if p.strip()]
    elsewhere = rf"\b(?:{'|'.join(v for k, v in WORDS.items() if k != 'fonts')})\b|{WHOLE_GAME}"
    others = [p for p in parts if not texty(p.lower()) and re.search(elsewhere, p.lower())]
    text = ', '.join(p for p in parts if p not in others) if others else clause
    return (clause if font_style(text, []) == {'font': ''} else text), others   # "names and buttons gold": the gold is for both


SIZE_WORDS = r'\b(?:bigger|smaller|larger|tinier|resize|enlarge|shrink|move|reposition|relocate)\b'
SIZE_NOTE = ('Sizes and positions are set by the game itself (each sprite has a fixed box in its sheet), '
             'so I can change how things look but not how big they are or where they sit.')
MOTION_WORDS = (r'\b(?:flash(?:es|ing)?|blink(?:s|ing)?|animat(?:e|ed|es|ing|ion)|spin(?:s|ning)?|rotat(?:e|es|ing)|bounc(?:e|es|ing)|'
                r'wiggl(?:e|es|ing)|shak(?:e|es|ing)|puls(?:e|es|ing|ate)|flicker(?:s|ing)?)\b')
MOTION_NOTE = 'Sprites are still pictures, so I can change how they look but not make them flash, spin or move.'
# How the art route words each control: the bar behind the OG tag is drawn by the game, so only the tag goes.
ART_LABELS = {'top_banner': 'the OG tag (the “Mitos.is” bar stays)', 'left_banner_1': 'the Worm.is and Strike.is promos',
              'left_banner_2': 'the Worm.is and Strike.is promos'}


# The languages of the lab's page (web/i18n), as the planner model is asked to write its reply.
REPLY_LANGUAGES = {'es': 'Spanish (neutral Latin American Spanish, informal "tú")', 'pt': 'Brazilian Portuguese (informal "você")',
                   'fr': 'French (informal "tu")', 'de': 'German (informal "du")'}


def reply_language(language):
    """What the system prompt adds so the model's "reply" is in the player's language; nothing for English."""
    name = REPLY_LANGUAGES.get(str(language or '').lower()[:2])
    if not name: return ''
    return (f'\n\nThe player reads {name}: write the "reply" sentence in {name}. Everything else in the JSON (keys, family names, '
            'preset names, color modes, hex colors) stays exactly as specified above, in English.')


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


def named_colors(said):
    """The colors a request names, in order: its gradients' own stops ("rainbow") and plain colors ("red and gold")."""
    low, out = str(said).lower(), []
    for name in sorted(GRADIENTS, key=lambda n: low.find(n) if re.search(rf'\b{re.escape(n)}s?\b', low) else 10 ** 6):
        if re.search(rf'\b{re.escape(name)}s?\b', low): out += [c for c in GRADIENTS[name] if c not in out]
    for found in re.finditer(rf"\b({'|'.join(sorted(COLORS, key=len, reverse=True))})\b", low):
        if COLORS[found.group(1)] not in out: out.append(COLORS[found.group(1)])
    return out


def nearest_named(color, palette):
    """The named color a planned one is a shade of (same hue family, similar lightness), or ''."""
    if not color or not palette: return ''
    h, s, v = colorsys.rgb_to_hsv(*rgb_of(color))
    best, gap = '', 1.0
    for c in palette:
        ph, ps, pv = colorsys.rgb_to_hsv(*rgb_of(c))
        if min(s, ps) < 0.25 or abs(v - pv) > 0.35: continue
        d = min(abs(h - ph), 1 - abs(h - ph))
        if d < gap: best, gap = c, d
    return best if gap <= 0.05 else ''


def same_colors(op, said):
    """The exact colors the request names, every time: "rainbow" is always the same rainbow and "red" the same red, so a
    theme made in pieces (or asked again) does not come out in a different shade each time. A preset gradient a request
    names replaces the model's own version of it; a planned color that is a shade of a named one becomes that color."""
    low = str(said).lower()
    presets = [n for n in GRADIENTS if re.search(rf'\b{re.escape(n)}s?\b', low)]
    palette = named_colors(said)
    if not palette: return op
    op = dict(op)
    for field in ('gradient', 'rim_gradient'):
        ramp = op.get(field) or []
        if len(ramp) >= 2 and len(presets) == 1 and ramp not in GRADIENTS.values():
            op[field] = list(GRADIENTS[presets[0]])
        elif ramp: op[field] = [nearest_named(c, palette) or c for c in ramp]
    for field in ('color', 'glow_color', 'outline_color', 'rim_color'):
        value = op.get(field)
        if isinstance(value, str) and value.startswith('#'): op[field] = nearest_named(value, palette) or value
    return op


def tame(plan, said, bold=False):
    """Small models tend to switch on every field a schema offers. Effects that wreck art are kept only
    when the conversation asked for something like them, and exact repeats of an operation are dropped.
    bold: an open-ended request ("make it cool") left the look to the model, so its gloss and sparkle stay."""
    said, kept, seen = said.lower(), [], set()
    wants_glow = re.search(r'glow|neon|lumin|radiat|halo|light up|lit up', said)
    wants_gloss = re.search(r'gloss|shin[ey]|glass|polish|chrome|metal|wet|candy|bubble|jelly|crystal', said)
    for op in plan['operations']:
        op = dict(op, **{field: (False if field == 'invert' else 0.0) for field, words in RISKY.items() if op[field] and not re.search(words, said)})
        if op['gloss'] and not wants_gloss and not bold: op['gloss'] = 0.0
        if op['flare'] and not bold and not re.search(r'flare|sparkl|glint|highlight|shin[ey]|star|gleam|twinkl', said): op['flare'] = 0.0
        if op['rim_color'] and op['color_mode'] == 'none' and not re.search(r'inside|interior|inner|fill|body|cent(?:er|re)|middle|within|dark|bright|light', said):
            # A rim-only operation promises to leave the art inside alone, so stray tone tweaks are dropped.
            op.update(hue_shift=0.0, saturation=1.0, brightness=1.0, contrast=1.0, gloss=0.0, opacity=1.0)
        # The user asked for glow: every rim or recolor that could carry one gets it, even if the model forgot.
        painted = op['rim_color'] or (op['color_mode'] in ('tint', 'colorize') and op['targets'] != ['menu_background'])
        if wants_glow and painted and not op['glow_color']: op.update(glow_color='auto', glow_size=12.0, glow_strength=1.6)
        if op['glow_color'] and re.search(rf'\b(?:{QUIET_WORDS})\s+(?:\w+\s+)?glow', said): op.update(glow_size=min(op['glow_size'], 8.0), glow_strength=min(op['glow_strength'], 0.9))
        op = same_colors(op, said)
        fingerprint = json.dumps({k: v for k, v in op.items() if k not in ('targets', 'match', 'exclude')}, sort_keys=True)
        whole = json.dumps(op, sort_keys=True)
        if (whole, 'same') in seen: continue   # the very same step again (small models repeat themselves)
        seen.add((whole, 'same'))
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


MENU_BACKGROUND = re.compile(r'bg\d+\.png', re.I)   # bg2.png and the other themes' menu backgrounds
# Art direction for a background picture (design.SCENE_SUBJECT): the whole frame is the art, and it repeats.
# A theme named after a brand, team, artist, film or game: the drawings are made of its visual identity, as fan art.
IDENTITY_DIRECTION = ('If the request names a brand, team, artist, film, game or character, build the design from its recognisable visual '
                      'identity: its emblem or logo shapes, signature colors, patterns and iconic objects (for a car brand: its badge, racing '
                      'stripes, wheels, grille, carbon fiber). ')


def identity(subject):
    """The brand rule for one piece: only the title carries the word MITOSIS (its round O can be the emblem); everything else
    gets no words at all, or the model writes the game's name on food and coins."""
    if 'MITOSIS' in subject: return IDENTITY_DIRECTION + 'Keep the word MITOSIS and make its round O the emblem. '
    return IDENTITY_DIRECTION + 'Never mention the game title, MITOSIS, letters or words: this piece has no text. '
ARENA_DIRECTION = ('This is the floor of the match arena, under the players\' colored cells and the food: describe a very pale, '
                   'quiet, low-contrast pattern or texture in the requested style, with a few large soft shapes, and nothing small, '
                   'round or dotted that could look like food or a cell. No text or logos. ')
SCENE_DIRECTION = ('This is a whole background picture that fills the frame edge to edge and repeats as a tile, like wallpaper: describe '
                   'the scene, its colors and lighting, with a few large shapes rather than tiny detail and nothing that must sit in the '
                   'center. Use the colors the request names. Never mention text, logos, buttons or any interface. ')


def opaque_tile(pixels, op):
    """A recolored main menu background. The menu's teal (#0D2829) is compiled into Mitosis.exe and bg2.png is only a
    faint black pattern over it, so a tinted pattern still shows the teal. This fills the whole tile with the new color
    instead, with the game's pattern kept as a soft shade of it (lighter on a dark color, darker on a light one).
    Gradients run there and back across the tile, so its edges meet and it repeats without seams."""
    h, w = pixels.shape[:2]
    stops = [rgb_of(hex_color(c)) for c in (op.get('gradient') or []) if hex_color(c)] or ([rgb_of(hex_color(op['color']))] if hex_color(op.get('color') or '') else [])
    if not stops: return pixels
    if len(stops) == 1: base = np.broadcast_to(np.asarray(stops[0], np.float32), (h, w, 3)).copy()
    else:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        if op.get('gradient_type') == 'radial': t = np.hypot((xx + .5) / w - .5, (yy + .5) / h - .5) / np.hypot(.5, .5)
        else:
            angle = np.radians(float(op.get('gradient_angle') or 0.0))   # 0 = left to right, 90 = top to bottom
            along = ((xx + .5) / w * np.cos(angle) + (yy + .5) / h * np.sin(angle)) % 1.0
            t = 1 - np.abs(2 * along - 1)   # 0 at both edges, 1 in the middle: seamless when tiled
        pos = np.clip(t, 0, 1) * (len(stops) - 1)
        i = np.minimum(pos.astype(int), len(stops) - 2)
        f = (pos - i)[..., None]
        table = np.asarray(stops, np.float32)
        base = table[i] * (1 - f) + table[i + 1] * f
    base = np.clip(base * float(op.get('brightness') or 1.0), 0, 1)
    alpha = pixels[..., 3:4].astype(np.float32) / 255
    if alpha.min() > 0.99:
        # Already a filled tile (an earlier recolor or a drawn background): its detail is in the brightness instead.
        lum = pixels[..., :3].astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32) / 255
        pattern = np.clip(np.abs(lum - np.median(lum))[..., None] * 3, 0, 0.35)
    else: pattern = np.clip(alpha * 6, 0, 0.35)   # the faint triangles, made readable
    dark = (base @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None] < 0.3
    rgb = np.where(dark, base + (1 - base) * pattern * 0.3, base * (1 - pattern))
    return np.dstack([(np.clip(rgb, 0, 1) * 255 + .5).astype(np.uint8), np.full((h, w), 255, np.uint8)])


def faint_tile(pixels):
    """A full-size, nearly see-through overlay (alpha never above ~10%): what the menu backgrounds are. A theme coloring it
    ("design a BMW theme", "everything gold") fills it instead of tinting pixels nobody can see."""
    return pixels.shape[0] >= 256 and pixels.shape[1] >= 256 and int(pixels[..., 3].max()) <= 26


def shade(pixels, op):
    """Apply one operation to an RGBA uint8 array, using the array's own extent as the frame for gradients and glows.

    Fully transparent pixels stay untouched unless a glow or outline is drawn into them.
    """
    if op.get('opaque') or (faint_tile(pixels) and op['color_mode'] != 'none' and (op.get('gradient') or hex_color(op.get('color') or ''))):
        return opaque_tile(pixels, op)   # a menu background: see opaque_tile
    out = pixels.astype(np.float32)
    rgb, alpha = out[..., :3] / 255.0, out[..., 3]
    if op['color_mode'] != 'none' and op['color_amount'] > 0:
        color = gradient_field(op, rgb) if op.get('gradient') else rgb_of(op['color'])
        value = rgb.max(axis=2)
        mode = op['color_mode']
        if mode == 'tint' and op.get('match'):
            # A tint leaves white and grey alone, so on a plain white icon named outright ("the ranks icon gold") it would
            # change nothing at all; that art is colored through instead. Broad themes keep white icons white.
            visible = (value - rgb.min(axis=2))[alpha > 128]
            if visible.size and np.percentile(visible, 95) < 0.06: mode = 'colorize'
        if mode == 'fill' or op.get('gradient_type') == 'luminance' and op.get('gradient'):
            target, weight = np.broadcast_to(color, rgb.shape), np.full(value.shape, op['color_amount'], np.float32)
            if mode == 'tint': weight = np.clip((value - rgb.min(axis=2)) / 0.125, 0, 1) * op['color_amount']
        else:
            # Shading comes from the art's own brightness, so gradients, outlines and highlights survive.
            target = value[..., None] * color
            chroma = value - rgb.min(axis=2)
            weight = (np.clip(chroma / 0.125, 0, 1) if mode == 'tint' else 1.0) * op['color_amount']
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


BACKGROUND_WORDS = r'\b(?:background|backdrop|wallpaper|bg|scenery)s?\b'
FEELINGS = set('hate hates ugly annoying annoyed love loves bad gross tired sick rid want wish please really so very green teal greenish ugh honestly'.split())


def scene_request(piece, lookup=None):
    """A background asked to show something ("a starry night sky background", "anime background", "spooky background"),
    not just a color or a brightness: the image model draws it as a seamless tile. Feelings about the old one ("I hate
    the green background") are not a picture; colors alone get an opaque recolor instead (see opaque_tile)."""
    low = piece.lower()
    if not re.search(BACKGROUND_WORDS, low): return False
    if any(re.search(rf'\b(?:{words})\b', low) for words in MOOD_WORDS.values()) or re.search(VAGUE, low): return True
    return any(w not in FEELINGS and not re.fullmatch(r'background|backdrop|wallpaper|bg|scenery|backgrounds', w) for w in unknown_words(piece, lookup))


# "...and the virus a pumpkin": a second part that borrows the first one's verb.
SECOND_PART = re.compile(r'(?P<head>.*?\b(?:make|turn|change|redraw|redesign|replace|swap)\s+the\s.+?)\s*,?\s+and\s+(?P<tail>the\s+\w+.*)$', re.I)
VERB = re.compile(r'\b(make|turn|change|redraw|redesign|replace|swap)\b', re.I)


def with_verbs(pieces):
    """Split "make the coin a big diamond and the virus a pumpkin" into two requests, each with its verb, so the
    virus is not lost inside the coin's description. Only when the second part is itself a change ("a pumpkin")."""
    out = []
    for piece in pieces:
        found = SECOND_PART.match(piece)
        tail = found and found.group('tail')
        if found and design.becomes(VERB.search(found.group('head')).group(1) + ' ' + tail):
            out += [found.group('head'), VERB.search(found.group('head')).group(1) + ' ' + tail]
        else: out.append(piece)
    return out


def food_set(designer, row):
    """The queue draws each food on its own (they are different things worth different mass): which one, smallest first."""
    which, of = designer.food_kind(row)
    return dict(set='food', kind=which, tier=design.FOOD_TIERS[min(2, round(which * 2 / max(1, of - 1)))][0] if of > 1 else '')


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
                  r'colou?r(?:s|ed)?|backgrounds?|bg',
                  r'deep(?:er)?|shades?|tones?|tint(?:s|ed)?|hues?|tad|touch|slightly|bit|much|lot|way|rich(?:er)?|pale(?:r)?|vivid|muted|saturated|warm(?:er)?|cool(?:er)?']
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
        names = list(dict.fromkeys(n for word, found in known.items() if re.search(rf'(?<![a-z0-9]){re.escape(word)}(?:e?s)?(?![a-z0-9])', clause) for n in found))
        # With the exe off limits a named game color is still noted (the chat explains it) rather than painted on
        # art: the game multiplies that art by its own color, so gold on teal-tinted white would come out olive.
        # A sprite named outright ("the ranks button icon") is its art, though, and "menu background navy" falls
        # through to the menu pattern art below.
        if len(paints) == 1 and not names and not re.search(r'\bpattern|overlay|triangles?\b', clause):
            named_ui = next((name for name, phrase in UI_PHRASES if re.search(phrase, clause)), '')
            if named_ui:
                ui[named_ui] = paints[0]; continue
            if not exe_palette.BLOCKED and re.search(r'\b(?:background|backdrop|bg)\b', clause) and not re.search(r'\b(?:world|game|arena|map|in-?game)\s*(?:background|bg)\b|\bgrid|arena|floor\b', clause):
                ui['background'] = paints[0]; continue
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
    # An Ollama that is off refuses at once; one that is busy unloading a model can take a few seconds to list them.
    try: listed = ollama('/api/tags', timeout=6).get('models', [])
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
        self.pictures = pictures.Pictures(lab)   # pictures players add to the chat, and the presets
        self.file = lab.root / 'themes.json'
        self.reindex()

    def font_request(self, message, fid=None):
        """A request about the game's text: a font style for one of its fonts, with a preview of example text. fid: the
        font the request is about (a checked font sheet or letter); else it is read from the words (player names by default)."""
        low = message.lower()
        if not fid and not texty(low): return None
        try:
            faces = self.lab.typefaces()
            game = fonts.game_fonts(self.lab.game)
        except (OSError, ValueError, AttributeError): faces, game = [], {}
        if not game:
            # Text is restyled from the game's own font files; without them it must not fall through to recoloring or
            # drawing the font sheets (which turns the letters into a block).
            reply = ("I couldn't find the game's font files (assets/*.fnt in the Mitos.is folder), so I can't restyle text right now. "
                     'In Steam, right-click Mitos.is › Properties › Installed Files › Verify integrity of game files, then restart Asset Lab.')
            return dict(plan=dict(reply=reply, operations=[], ui_colors={}, hide=[]), source='font', note='', preview=None)
        if fid not in game: fid = next((f for f, pattern in TEXT_TARGETS if f in game and re.search(pattern, low)), next(iter(game)))
        quoted = re.search(r'["“]([^"”]{1,24})["”]', message)
        sample = quoted.group(1) if quoted else (self.sample() if fid == 'nicktext' else 'Settings' if fid.startswith('geosans') else '12345')
        raw = font_style(message, faces)
        material = texture_subject(message)
        plain = not raw.get('look') and fonts.clean_style(raw, faces) == fonts.clean_style({}, faces)
        if plain and (material or re.search(NEW_FONT, low)):
            # A new font that the words alone do not describe ("a font made of chocolate", "make me a new font that feels
            # like a horror movie"): the local model picks the typeface and effects for it.
            designed = self.font_design(message, faces)
            if designed: raw, plain = designed, False
        if material:
            return self.texture_font(fid, game, raw, sample, material, plain)
        if plain:
            # Nothing about the look was said ("change the font", "a different font"): a first look to start from.
            self._starter = (getattr(self, '_starter', -1) + 1) % len(STARTER_LOOKS)
            look = STARTER_LOOKS[self._starter]
            raw = dict(fonts.PRESETS[look], look=look)
            card = self.font_card(fid, raw, sample)
            reply = (f'{card["label"]}: here is the {look} look to start from. Flip through more looks below, or tell me what you want: '
                     'a font ("arial black", "comic sans"), colors ("red to gold"), an outline, a glow or a mood ("spooky"). '
                     'Apply puts it in your mod.')
        else:
            card = self.font_card(fid, raw, sample)
            reply = (f"{card['label']}: {describe_text(card['style'], card['face'], card['look'], raw.get('look', ''))}. "
                     'Flip through more looks below; Apply puts it in your mod and Fine-tune has every setting.')
        notes = [] if fid == 'nicktext' else [game[fid]['note']]
        if re.search(TEXT_WORDING, low):
            notes.append("I can change how text looks (font, colors, outline, glow), not what it says: player names are what each "
                         'player types, and the menu words are written in the game itself.')
        return dict(plan=dict(reply=reply, operations=[], ui_colors={}, hide=[]), source='font', note=' '.join(notes), preview=None, font=card)

    def texture_font(self, fid, game, raw, sample, material, plain):
        """A new font whose letters are made of something: the style now (a heavy, outlined typeface unless the words or
        the model chose one), and the card asks Image AI to draw the material as a tile to fill the letters with."""
        style = dict(TEXTURE_BASE, **{k: v for k, v in raw.items() if k != 'look'}) if not plain else dict(TEXTURE_BASE)
        if not style.get('font'): style['font'] = TEXTURE_BASE['font']
        card = self.font_card(fid, style, sample)
        designer = getattr(self, 'designer', None)
        ready = designer.status() if designer else dict(available=False, missing=['the image model'])
        if ready['available']:
            card.update(texture_prompt=material, seconds=round(ready.get('seconds_per_drawing') or design.DEFAULT_SECONDS))
            reply = (f'{card["label"]}: a new font made of {material}. Image AI is drawing what the letters are made of; pick the one you '
                     'like below, flip through looks for the shape and edges, then Apply.')
            note = ''
        else:
            reply = f'{card["label"]}: {describe_text(card["style"], card["face"], card["look"], "")}.'
            note = (f'To fill the letters with {material}, install the image model (Settings › Theme AI & drawing). '
                    'Until then this is the closest look in colors and effects.')
        if fid != 'nicktext': note = (note + ' ' + game[fid]['note']).strip()
        return dict(plan=dict(reply=reply, operations=[], ui_colors={}, hide=[]), source='font', note=note, preview=None, font=card)

    def font_design(self, message, faces):
        """A font style the local model makes up for a request the words do not describe, or None (no model, or no usable
        answer). The typeface is one every Windows PC has; colors are checked by fonts.clean_style."""
        available = models()
        if not available: return None
        model = self.preferred([m['name'] for m in available])
        installed = {f['family'] for f in faces} if faces else set(FONT_FAMILIES)
        families = [f for f in FONT_FAMILIES if f in installed] or FONT_FAMILIES
        system = ('You design the lettering of player names in a 2D game. Answer with one JSON object only: '
                  '{"font": one of [' + ', '.join(families) + '], "fill": "#rrggbb", "gradient": [2 to 5 "#rrggbb", top to bottom, or []], '
                  '"outline": "#rrggbb" or "", "outline_width": 0-6, "glow": "#rrggbb" or "", "glow_size": 0-16, "shadow": "#rrggbb" or "", '
                  '"caps": true or false, "italic": -0.3 to 0.3}. The names must stay easy to read on dark and light backgrounds, '
                  'so light letters get a dark outline or shadow and dark letters a light one.')
        payload = dict(model=model, stream=False, format='json', keep_alive='10m',
                       messages=[dict(role='system', content=system), dict(role='user', content=message[:400])],
                       options=dict(temperature=0.5, num_predict=220, num_ctx=2048, **MODEL_OPTIONS.get(model, {})))
        if next(m for m in available if m['name'] == model)['thinking']: payload['think'] = False
        self.activity = f'Designing the font with {model}…'
        try:
            answer = ollama('/api/chat', payload, timeout=120)
            found = re.search(r'\{.*\}', re.sub(r'<think>.*?</think>', '', answer.get('message', {}).get('content', ''), flags=re.S), re.S)
            got = json.loads(found.group(0)) if found else {}
        except (OSError, ValueError, AttributeError): return None
        finally: self.activity = None
        if not isinstance(got, dict): return None
        family = got.get('font') if got.get('font') in families else 'Arial Black'
        style = dict(font=family.replace('Arial Black', 'Arial|Black').replace('Segoe UI Black', 'Segoe UI|Black'))
        hexes = lambda v: [c for c in (v if isinstance(v, list) else []) if isinstance(c, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', c)]
        ramp = hexes(got.get('gradient'))[:5]
        if len(ramp) >= 2: style['stops'] = fonts.even(*ramp)
        for key in ('fill', 'outline', 'glow', 'shadow'):
            if isinstance(got.get(key), str) and re.fullmatch(r'#[0-9a-fA-F]{6}', got[key]): style[key] = got[key]
        number = lambda v, lo, hi: max(lo, min(hi, float(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if style.get('outline'): style['outline_width'] = number(got.get('outline_width'), 1, 6) or 3
        if style.get('glow'): style.update(glow_size=number(got.get('glow_size'), 2, 16) or 8, glow_strength=1.3)
        if style.get('shadow'): style.update(shadow_y=3, shadow_blur=3, shadow_opacity=0.7)
        if got.get('caps') is True: style['caps'] = True
        if number(got.get('italic'), -0.3, 0.3): style['italic'] = number(got.get('italic'), -0.3, 0.3)
        return style if len(style) > 1 else None

    def checked_font(self, only):
        """The game font the checked assets are, when every one of them is a font sheet or one of its letters."""
        rows = [self.lab.catalog.rows.get(k) for k in only or []]
        found = {font_of(r) for r in rows if r}
        return next(iter(found)) if rows and all(rows) and len(found) == 1 and '' not in found else ''

    def font_card(self, fid, style, sample):
        """A text style ready for the chat: the cleaned style, the look it is (if it is one), and a preview of example text."""
        faces, game = self.lab.typefaces(), fonts.game_fonts(self.lab.game)
        style = fonts.clean_style(style, faces)
        image = 'data:image/png;base64,' + base64.b64encode(self.lab.font_preview(fid, style, sample)).decode()
        look = next((name for name, preset in fonts.PRESETS.items() if preset and fonts.clean_style(preset, faces) == style), '')
        return dict(id=fid, label=game[fid]['label'], style=style, image=image, text=sample, look=look,
                    face=(fonts.find_font(faces, style['font']) or {}).get('family', '') if style['font'] else '')

    def sample(self):
        """The player names' example text (Settings > Text & fonts > Example text)."""
        return labsettings.value(self.lab, 'font_sample') or 'Cellmate'

    def text_offer(self, result, text, only=None):
        """A whole-game mood ("halloween theme", "make everything gold") also offers the player names in the matching look,
        as a card to try: it is never applied by itself. Requests about some parts ("make the buttons red") get none, and
        nobody does when Settings > Theme AI & drawing > Offer matching text styles is off."""
        low = str(text).lower()
        look = None if result.get('font') or re.search(FONT_WORDS, low) else next((name for pattern, name in MOOD_TEXT if re.search(rf'\b(?:{pattern})\b', low)), None)
        whole = re.search(WHOLE_GAME, low) or any(op.get('targets') == ['everything'] and not op.get('match') for op in (result.get('plan') or {}).get('operations') or [])
        if look and whole and not only and labsettings.value(self.lab, 'offer_text_styles'):   # checked assets: the request is about those
            try: result['font'] = dict(self.font_card('nicktext', fonts.PRESETS[look], self.sample()), offer=True)
            except (OSError, ValueError, KeyError, AttributeError): pass
        return result

    @staticmethod
    def tinted_targets(plan):
        """Named sprites a color change lands on although the game multiplies them by one of its colors: name -> color."""
        tints, out = design.tints(), {}
        for op in plan['operations']:
            if op['color_mode'] == 'none' and not any(op[k] for k in ('hue_shift', 'invert', 'rim_color', 'glow_color', 'outline_color')): continue
            for name in op['match']:
                if name in tints: out[name] = tints[name]
        return out

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
        self.tokens, self._tone, self.labelled, self.label_of = {}, {}, {}, {}
        for sheet in sheets.values():
            for row, _ in (sheet['members'] or [(sheet['row'], sheet['family'])]):
                if row.get('label'):
                    label = row['label'].lower()
                    self.labelled.setdefault(label, set()).add(row['name'])
                    self.label_of[row['name']] = re.sub(r'\s*\([^)]*\)\s*$', '', row['label'])
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
        for alias, names in ALIASES.items():
            phrase = rf'\b{alias}(?:es)?\b'
            if re.search(phrase, scan):
                found[alias] = names
                scan, text = re.sub(phrase, ' ', scan), re.sub(phrase, 'virus', text)   # "red" names the sprite, it is not a color here
        for label in sorted(self.labelled, key=len, reverse=True):
            phrase = rf'(?<![a-z0-9]){re.escape(label)}s?(?![a-z0-9])'   # also works for names ending in ")"
            if re.search(phrase, scan):
                found[label] = sorted(self.labelled[label])[:24]
                scan = re.sub(phrase, ' ', scan)
        # "The menu background" is the menu pattern family, not every sprite with "background" in its name.
        scan = re.sub(rf"\b(?:{WORDS['menu_background']}|{WORDS['world_background']})\b", ' ', scan)
        for word in dict.fromkeys(re.findall(r'[a-z]{4,}', scan)):
            stem = next((s for s in (word, word[:-1] if word.endswith('s') else '', word[:-2] if word.endswith('es') else '') if s in self.tokens), '')
            if not stem or stem in STOP or word in STOP or word in TOOL_WORDS or stem in COLORS or stem in GRADIENTS or family_words.fullmatch(word): continue
            # "coins" is also "coin": both spellings of a name, so the plural does not pick a different sprite.
            rows = list({r['key']: (k, r) for s in dict.fromkeys((stem, word, word[:-1] if word.endswith('s') else '')) for k, r in self.tokens.get(s, [])}.values())
            # Store and sign-in badges ("play" is also Google Play) only when the request names them.
            if not re.search(design.BRANDS, text): rows = [(k, r) for k, r in rows if r['name'] not in design.KEEP_AS_IS] or ([] if any(r['name'] in design.KEEP_AS_IS for _, r in rows) else rows)
            if not rows: continue
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
        lines, tints = [], design.tints()
        for word, names in found.items():
            colored = sorted({tints[name] for name in names if name in tints})
            # The game multiplies these white icons by its own color, so the model should know a new color mixes with it.
            extra = f' (white art the game itself colors {", ".join(colored)}: any color you add, glows included, mixes with that; lighter or darker work best)' if colored else ''
            if word in self.labelled:
                lines.append(f'"{word}" (your name for these sprites): ' + ', '.join(names) + extra)
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
                '- The main menu background (menu_background) is a faint pattern over a fixed green-teal that players dislike and that cannot be '
                'changed directly. A color or gradient given to menu_background in its own operation fills the whole tile, so the teal is gone, and '
                'keeps the pattern as a soft shade. Whenever the user asks for a background color, mood or "the background", give menu_background its '
                'own operation with color_mode "fill" and a color or gradient that fits; never only tint it or shift its hue.\n'
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

    _turn = threading.local()   # what one chat request learns on the way (parts it could not find), per server thread

    def chat(self, message, model='', history=None, only=None, language='', draw='auto', picture='', theme_from=''):
        """The reply to a request, with a word about server-only art when the request names some (see ServerArt), and
        inspiration from Wikipedia when it names someone or something ("a Post Malone theme"; Settings can turn it off).
        draw: the Image AI switch in the chat. 'auto' draws when the request needs it, 'on' always draws new art (the
        main pieces of the game when nothing is named), 'off' only recolors and shades. picture: a picture the player
        added (pasted, dropped or a preset): the request is about it ("make this dark blue")."""
        if theme_from: return self.preset_theme(theme_from, only)   # right-click a preset › Make a whole theme from this
        if picture: return self.picture_request(str(message or ''), picture, only)
        draw = draw if draw in ('auto', 'on', 'off') else 'auto'
        self.inspiration = None
        if labsettings.value(self.lab, 'web_inspiration') and inspire.candidates(str(message or '')[:2000]):
            self.inspiration = inspire.lookup(str(message or ''))
        self._turn.lost = []
        try: result = self._chat(message, model, history, only, language, draw)
        finally: found, self.inspiration = self.inspiration, None
        if found: self.inspired(result, found, only, draw, str(message or ''))
        if self._turn.lost and not (result.get('design') or {}).get('targets'):
            # "Make the leaderboard icon a trophy": no sprite here goes by that name, so nothing was guessed.
            parts = ', '.join(f'“{p}”' for p in dict.fromkeys(self._turn.lost))
            result['note'] = ((result.get('note') or '') + f' I could not tell which art {parts} is, so nothing was redrawn for it. '
                              'Check it in the library and ask again, or right-click it › Redraw with Image AI.').strip()
        if re.search(RED_VIRUS, str(message or '').lower()) and result.get('source') != 'command':
            result['note'] = ((result.get('note') or '') + ' ' + RED_VIRUS_NOTE).strip()
        if re.search(SERVER_ART, str(message or '').lower()) and result.get('source') != 'command':
            plan = result.get('plan') or {}
            if plan.get('operations'): plan['operations'] = [op for op in plan['operations'] if not set(op.get('targets') or []) <= {'potions', 'equipment'}]
            drawing = result.get('design') and not result['design'].get('optional')   # an offered title redraw is not a plan
            if not (plan.get('operations') or plan.get('ui_colors') or plan.get('hide') or drawing or result.get('font')):
                plan['reply'] = SERVER_NOTE
            else: result['note'] = ((result.get('note') or '') + ' ' + SERVER_NOTE).strip()
        return result

    def picture_request(self, message, pid, only=None):
        """A picture the player added, recolored as asked ("make this dark blue", "gold", "darker"), with the game art it
        most likely belongs on (checked pictures first). Nothing changes in the game until they pick where it goes and
        click Use this one. "Use the Purple virus preset" opens a preset in its own colors."""
        image = self.pictures.image(pid)
        preset = re.fullmatch(r'(?i)use the .* preset', message.strip())
        color = '' if preset else pictures.wanted_color(message, image)
        targets = self.pictures.targets(pid, only=only)
        if color: reply = 'Here it is in the new color. Check where it goes and click Use this one.'
        elif message.strip() and not preset:
            reply = ('I can change this picture’s colors: pick one below or say one (“dark blue”, “gold”, “darker”). '
                     'Then check where it goes and click Use this one.')
        else: reply = 'Pick a color (or keep it as it is), check where it goes, then click Use this one.'
        note = '' if targets else 'Pick where it goes by checking that picture in the library first.'
        return dict(plan=dict(reply=reply, operations=[], ui_colors={}, hide=[]), source='picture', note=note, preview=None,
                    picture=dict(id=pid, color=color, targets=targets, **next(({'name': p['name'], 'kind': p['kind']} for p in pictures.presets() if p['id'] == pid),
                                                                              {'name': '', 'kind': 'picture'})))

    def preset_theme(self, pid, only=None):
        """A whole theme from a preset (a background or a virus): the preset itself, ready to put on, and the rest of the
        whole-game redesign drawn after it, with the preset as the reference picture and its colors as the theme's colors."""
        preset = next((p for p in pictures.presets() if p['id'] == pid), None)
        if not preset: raise ValueError('Unknown preset.')
        name, kind = preset['name'], preset['kind']
        result = self.picture_request(f'Use the {name} preset', pid, only)
        designer = getattr(self, 'designer', None)
        if not designer or not designer.status()['available']:
            result['plan']['reply'] = f'Put {name} on with Use this one. To draw the rest of the game in its style, install the image model (Settings › Theme AI & drawing).'
            return result
        own = (lambda r: design.is_menu_background(r) or design.is_arena_background(r)) if kind == 'background' else (lambda r: r['name'] in design.VIRUSES)
        rows = [r for r in self.showcase_rows(designer) if not own(r)]
        image = self.pictures.image(pid)
        # The preset is the reference picture of every drawing, and its colors are the theme's.
        board = hashlib.sha256(pid.encode()).hexdigest()[:20] + '.png'
        if not (self.lab.root / 'inspiration' / board).is_file():
            (self.lab.root / 'inspiration').mkdir(parents=True, exist_ok=True)
            image.convert('RGB').save(self.lab.root / 'inspiration' / board)
        prompt = f'a whole game theme in the style and colors of the reference picture ({kind}: {name})'
        designer.seed_palette(design.palette_key(prompt), design.learn_colors(image))
        offer = self.design_offer(designer, prompt, rows)
        offer.update(inspiration=board, inspiration_title=name)
        result['plan']['reply'] = (f'A whole theme from {name}: put {name} on with Use this one, then Draw it to redraw the rest of the game '
                                   'in its colors and style.')
        result['design'] = offer
        return result

    def showcase_rows(self, designer, only=None):
        """What "draw the whole game" redraws: the checked assets, or everything players look at in game (see
        Designer.whole_game_rows: the backgrounds, the viruses, the menu and in-game buttons and the food)."""
        if only: return [r for r in (self.lab.row(k) for k in only if k in self.lab.catalog.rows) if r['editable']][:24]
        # A game without those (another Mitosis version, a test game) draws a few of its buttons and icons instead.
        return designer.whole_game_rows() or designer.family_rows('buttons icons logo cells')

    def inspired(self, result, found, only, draw='auto', message=''):
        """A request that names someone or something: say what was looked up, carry it into the drawings, and offer to redraw
        the main art (the title, the virus, the play button and the coin, or the checked assets), since colors alone can't
        make "a Post Malone theme"."""
        title, summary = found['title'], found['summary']
        # Pictures, not just words: the page's emblem, logo and photos on one board the drawings take their look from.
        board = inspire.pictures(found, self.lab.root / 'inspiration') if draw != 'off' else ''
        result['note'] = ((result.get('note') or '') + f' Inspired by {title} (looked up on Wikipedia{", with its pictures" if board else ""}).').strip()
        context = f' Inspiration ({title}): {summary[:450]}'
        designer = getattr(self, 'designer', None)
        part = result.get('design')
        if part and part.get('optional') and designer and draw != 'off' and not only and labsettings.value(self.lab, 'offer_redraws'):
            # "Make it look like the matrix": the mood's title offer grows into the whole game, still offered (not started).
            rows = self.showcase_rows(designer)
            if rows: result['design'] = part = dict(self.design_offer(designer, f'{title} theme.{context}', rows), optional=True)
        elif part and designer and not only and part.get('targets') and re.search(GAME_THEME, message.lower()) and labsettings.value(self.lab, 'offer_redraws'):
            # "Create me a bmw theme and make the food little tires": the whole game in the theme, and the parts the
            # request describes on their own (the food) drawn from those words, still in the theme.
            rows = self.showcase_rows(designer)
            have = {r['key'] for r in rows}
            rows += [self.lab.row(t['key']) for t in part['targets'] if t['key'] not in have and t['key'] in self.lab.catalog.rows]
            own = f"{part.get('prompt') or ''}. {title} theme.{context}"
            result['design'] = dict(self.design_offer(designer, f'{title} theme.{context}', rows), prompts={t['key']: own for t in part['targets']})
        elif part: part['prompt'] = (part.get('prompt') or '') + context
        elif designer and draw != 'off' and labsettings.value(self.lab, 'offer_redraws'):
            # The whole game in that style (the queue puts the title, background, viruses, play button, coin and food first),
            # or just the checked assets.
            rows = [self.lab.row(k) for k in only or [] if k in self.lab.catalog.rows] or self.showcase_rows(designer)
            if rows:
                plan = result.get('plan') or {}
                planned = plan.get('operations') or plan.get('ui_colors') or plan.get('hide')
                result['design'] = dict(self.design_offer(designer, f'{title} theme.{context}', rows), optional=bool(planned))
        if board and result.get('design'): result['design'].update(inspiration=board, inspiration_title=title)
        return result

    def _chat(self, message, model='', history=None, only=None, language='', draw='auto'):
        """only: catalog keys of checked assets; the plan is then previewed and applied to those alone. language: the
        lab's language ('es', 'pt', 'fr', 'de'; '' or 'en' for English), the one the planner model writes its reply in
        (fixed replies stay English here: the page translates them).

        One request can mix both tools: the parts that need drawing ("put a santa hat on the title") go to the
        image model, the rest ("theme it christmas") to the shader. A whole-game mood also offers a title redraw."""
        scope = self.scope(only)
        message = str(message or '').strip()[:2000]
        if not message: raise ValueError('Describe the look you want.')
        command = self.command(message)
        if command: return command
        # "...using image generation": a word about the tool, not the game. It turns drawing on for this request and is
        # taken out, so "image" is not read as a sprite name.
        if re.search(DRAW_ASK, message.lower()):
            if draw == 'auto': draw = 'on'
            message = re.sub(DRAW_ASK, ' ', message, flags=re.I)
            message = re.sub(r'\s+([.,!?])', r'\1', re.sub(r'\s{2,}', ' ', message)).strip(' .,') or 'give the game a bold new look'
        # A checked font sheet (or its letters) is restyled by the fonts tool, whatever the words: drawn by the image model
        # or recolored as a picture, its letters would be lost.
        checked = self.checked_font(only) if only else ''
        if checked: return self.font_request(message, checked)
        pieces = [p.strip(' ,') for p in re.split(CLAUSES, message) if p and p.strip(' ,')]
        pieces = with_verbs(pieces)
        split = [text_split(p) for p in pieces if about_text(p, only)]   # "christmas theme and red names": both
        texts = [t for t, _ in split]
        font = texts and self.font_request('. '.join(texts))
        if font:                                    # "theme it halloween and make the names spooky": the font card plus the theme
            others = [p for p in pieces if not about_text(p, only)] + [o for _, more in split for o in more]
            if not others: return font
            result = self._chat('. '.join(others), model, history, only, language, draw)
            plan = result.get('plan') or {}
            if not (plan.get('operations') or plan.get('ui_colors') or plan.get('hide') or result.get('design')): return font
            result['font'] = font['font']
            return result
        designer = getattr(self, 'designer', None) if draw != 'off' else None   # Image AI off: colors and effects only
        drawn, rest, lost = [], message, []   # lost: parts asked to change that no sprite here is known by
        self._turn.lost = lost
        if designer:
            for piece in pieces:
                change = design.becomes(piece)
                wants = draw == 'on' or design.wants_design(piece, unknown_words(piece, self.named)) or scene_request(piece, self.named) \
                    or bool(change and unknown_words(change[1]) and not changes_anything(rule_plan(change[1])))
                if wants and designer.targets(piece, only): drawn.append(piece)
                elif wants and change and not only and not re.search(THEMED, piece.lower()) and not re.fullmatch(WHOLE, change[0]): lost.append(change[0])
            rest = '. '.join(p for p in pieces if p not in drawn)
        if draw == 'on' and designer and not drawn and not lost:
            # Image AI on, nothing named ("make the game look cool"): the main art is drawn in the new style, and the rest
            # of the game is shaded to match.
            rows = self.showcase_rows(designer, only)
            if rows:
                result = self.plan_request(message, model, history, scope, language)
                result['design'] = self.design_offer(designer, message, rows)
                return self.text_offer(result, message, only)
        if drawn:
            rows, seen, own = [], set(), {}   # own: each part's own words, when the request changes several parts
            for piece in drawn:
                found = designer.targets(piece, only)
                if (scene_request(piece, self.named) or re.search(BACKGROUND_WORDS, piece.lower())) and not only:
                    # A background picture is for backgrounds: "night sky" must not also redraw a "soccer-field-night" item.
                    arena = re.search(r'\b(?:arena|world|in-?game|match)\b', piece.lower())
                    if arena:   # the arena is worldbg.png, whatever floor textures the words also match
                        found = [r for r in self.lab.catalog.rows.values() if r['name'].lower().startswith('worldbg') and r['editable'] and r.get('primary', True)][:4] or found
                    else: found = [r for r in found if design.is_menu_background(r)] or found
                for r in found:
                    if r['key'] not in seen: seen.add(r['key']); rows.append(r); own[r['key']] = piece
            part = self.design_offer(designer, '. '.join(drawn), rows)
            if len(drawn) > 1: part['prompts'] = own   # "the coin a diamond and the virus a pumpkin": the coin is not told about pumpkins
            if not rest.strip():
                # about drawing chosen art ("make the potion spooky"): no player-name offer
                return dict(plan=dict(reply='', operations=[], ui_colors={}, hide=[]), source='design', note=part.pop('note'), preview=None, design=part)
            result = self.plan_request(rest, model, history, scope, language)
            result['design'] = part
            return self.text_offer(result, rest, only)
        result = self.plan_request(message, model, history, scope, language)
        if draw == 'off' and getattr(self, 'designer', None) and design.wants_design(message, unknown_words(message, self.named)):
            result['note'] = (result['note'] + ' ' if result['note'] else '') + ('Image AI is off, so only colors and effects were planned. '
                                                                                 'Switch it to Auto or On to draw new art.')
        if designer and re.search(design.OBJECTS, message.lower()):
            # New objects need drawing, and drawing needs a target the request did not name.
            result['note'] = (result['note'] + ' ' if result['note'] else '') + ('To draw new things, say what to draw them on (the title, the icons, '
                                                                                 '"the speed potion") or check those assets in the library first.')
        low = message.lower()
        parts = [f for f, words in WORDS.items() if f != 'everything' and re.search(rf'\b(?:{words})\b', low)]
        if designer and labsettings.value(self.lab, 'offer_redraws') and re.search(VAGUE, low) and not parts and not only:
            # "Make the game look cool": the colors are planned now, and redrawing everything players look at is offered
            # (with its time, in the chat); Image AI On would start it straight away.
            rows = self.showcase_rows(designer)
            if rows: result['design'] = dict(self.design_offer(designer, message, rows), optional=True)
        elif designer and labsettings.value(self.lab, 'offer_redraws') and not lost and design.wants_design(message, unknown_words(message, self.named)) and designer.title_rows():
            # "Make the game spooky": the palette is shaded now; redrawing the title is offered, not started (unless the
            # player turned these offers off in Settings).
            rows, prompt = designer.title_rows(), f'{message}. Redesign the game title to match.'
            if re.search(WHOLE_GAME + r'|\breskin', message.lower()):
                # A whole-game look ("full spooky reskin") offers the virus too: the game's most recognisable sprite, and
                # one that shows in matches, and the food (every pellet as one set). Each part is drawn on its own from the same request.
                virus = [r for r in self.lab.catalog.rows.values() if r['name'] == 'virus.png' and r['editable'] and r.get('primary', True) and not r.get('removed')][:1]
                if virus or designer.food_rows(): rows, prompt = rows + virus + designer.food_rows(), message
            result['design'] = dict(self.design_offer(designer, prompt, rows), optional=True)
        return self.text_offer(result, message, only)   # a whole-game mood also offers the player names in a matching look

    HELP = ('I change how Mitosis looks. Ask for colors, moods or effects ("christmas theme", "rainbow outlines with a subtle glow", '
            '"holographic glass panels", "make the buttons darker") and I shade the matching art. Name one thing ("make the play button gold") '
            'to change just that. Ask for new things or a new style ("put a party hat on the title", "make the title say MITOSIS 2", '
            '"turn the coin into a diamond") and the local image model redraws it. "Hide the discord button" hides the App Store, '
            'Google Play and Discord badges or the OG tag. Ask "what can I change?" for every part I know by name. '
            'Check assets in the library to change only those. Say "undo" to take the last theme off. '
            'For the game\'s text, name it: "gold player names", "names in a red, orange and yellow gradient", "hollow neon names".')

    def names_reply(self):
        """Every part known by name, grouped by the screen it is on."""
        screens = {}
        for label in sorted(self.labelled):
            found = re.match(r'^(.*?)\s*\(([^)]*)\)$', label)
            if found: screens.setdefault(found.group(2), []).append(found.group(1))
        if not screens: return 'I do not know any parts by name yet. Name assets in the library (Name…) and I will understand them.'
        return 'Parts I know by name: ' + ' '.join(f'{where.capitalize()}: {", ".join(names)}.' for where, names in sorted(screens.items(), key=lambda kv: (kv[0] != 'main menu', kv[0]))) + \
            ' Everything else works by kind too: buttons, panels, icons, cells, food, the logo, the menu pattern.'

    def command(self, message):
        """A few things that are not looks: help, the names I know, and undo by chat."""
        low = message.lower().strip(' .!?')
        empty = dict(reply='', operations=[], ui_colors={}, hide=[])
        if re.fullmatch(r'help|commands|what can (?:you|i) do(?: here)?|what do you do|how does (?:this|it) work', low):
            return dict(plan=dict(empty, reply=self.HELP), source='command', note='', preview=None)
        if re.fullmatch(r'what (?:can i|could i|do you let me) (?:change|edit|name|theme)(?: here)?|(?:list|show)(?: me)? (?:the |all )?(?:names|named (?:parts|things)|parts|ui parts)|what (?:parts|things|names) do you know', low):
            return dict(plan=dict(empty, reply=self.names_reply()), source='command', note='', preview=None)
        if len(low) < 60 and re.search(r'\b(?:install|download|get|add|set ?up|enable)\b', low) and re.search(r'\b(?:it|that|model|ai|image|drawing|redraws?)\b', low):
            ready = not runtime.missing('sd', 'diffusion', 'llm', 'vae')
            return dict(plan=dict(empty, reply='The image model is installed, so you can ask for redraws now.' if ready else
                                  "I can't download it from in here. Close Asset Lab and open it again: its start window offers to "
                                  'download the local AI (about 5 GB, one time), and has a "Get the local AI" button. Then ask me again.'),
                        source='command', note='', preview=None)
        if re.fullmatch(r'undo(?: (?:that|it|the last (?:theme|change|one)))?|go back|revert(?: that| it)?|take (?:that|it) (?:back )?off', low):
            done = self.undo()
            return dict(plan=dict(empty, reply=f"Undid “{done['prompt'][:80]}”: {done['reverted']} sheet(s) restored" + (f", {done['skipped']} edited since were left alone." if done['skipped'] else '.')),
                        source='command', note='', preview=None, undone=True)
        return None

    def art_direction(self, prompt, subject):
        """Expand a short design request ("spooky") into a concrete visual description for the image model, or None."""
        available = models()
        if not available: return None
        model = self.preferred([m['name'] for m in available])
        system = ('You are the art director of a 2D mobile game. Rewrite the request as one short paragraph (at most 60 words) that says exactly '
                  'what to draw: materials, colors, lighting, and small details or decorations. Keep the subject, and any text in it, exactly as '
                  'it is. ' + identity(subject) + (SCENE_DIRECTION if subject == design.SCENE_SUBJECT else ARENA_DIRECTION if subject == design.ARENA_SUBJECT else 'Do not describe a background, scenery, or a shadow, floor or stand under it (the art is cut out). ') +
                  'No preamble, no lists, no quotes.')
        # Unloaded at once (the image model needs the graphics card next) unless the player keeps it (Settings: Free the graphics card).
        payload = dict(model=model, stream=False, keep_alive=0 if labsettings.value(self.lab, 'free_gpu') else '10m',
                       messages=[dict(role='system', content=system), dict(role='user', content=f'Subject: {subject}\nRequest: {prompt}')],
                       options=dict(temperature=0.7, num_predict=180, num_ctx=2048, **MODEL_OPTIONS.get(model, {})))
        if next(m for m in available if m['name'] == model)['thinking']: payload['think'] = False
        answer = ollama('/api/chat', payload, timeout=120)
        text = re.sub(r'<think>.*?</think>', '', answer.get('message', {}).get('content', ''), flags=re.S).strip().strip('"')
        return text if 20 <= len(text) <= 900 else None

    def art_concepts(self, prompt, subject, count):
        """Several clearly different takes on one request, one per variant ("spooky virus": wrapped like a mummy / chained in a
        graveyard / cracked bone dripping blood), so the player picks between ideas, not between near-copies. The vaguer the
        request, the bolder the ideas. A list of up to `count` directions, or [] when no planner model is installed."""
        available = models()
        if not available or count < 2: return []
        model = self.preferred([m['name'] for m in available])
        system = (f'You are the creative art director of a 2D game. Invent {count} clearly different visual concepts for the request, '
                  'each one a distinct idea (different materials, props, story or style), not variations of one look. If the request is '
                  'short or vague, be bold and surprising. Each concept is one paragraph of at most 55 words saying exactly what to draw: '
                  'materials, colors, lighting, decorations. Keep the subject recognisable and any text in it exactly as it is. '
                  'When the request names what to draw, every concept is exactly that thing, one of it, '
                  'varying only its style. ' + identity(subject) +
                  (SCENE_DIRECTION if subject == design.SCENE_SUBJECT else ARENA_DIRECTION if subject == design.ARENA_SUBJECT else 'Do not describe a background, floor or shadow under it (the art is cut out). ') +
                  'Celebrities, musicians, characters and pop '
                  'culture are fine as fan art (the game\'s own skins use them); swimwear is the limit: never nudity or sexual content, '
                  'and never anyone who could be a minor in anything suggestive. '
                  f'Answer with exactly {count} lines, one concept per line, no numbering, no preamble.')
        payload = dict(model=model, stream=False, keep_alive=0 if labsettings.value(self.lab, 'free_gpu') else '10m',
                       messages=[dict(role='system', content=system), dict(role='user', content=f'Subject: {subject}\nRequest: {prompt}')],
                       options=dict(temperature=0.95, num_predict=120 * count, num_ctx=2048, **MODEL_OPTIONS.get(model, {})))
        if next(m for m in available if m['name'] == model)['thinking']: payload['think'] = False
        try: answer = ollama('/api/chat', payload, timeout=180)
        except (OSError, ValueError): return []
        text = re.sub(r'<think>.*?</think>', '', answer.get('message', {}).get('content', ''), flags=re.S)
        lines = [re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*', '', l).strip().strip('"') for l in text.splitlines()]
        return [l for l in lines if 20 <= len(l) <= 600][:count]

    def preferred(self, names):
        """The planner model to use when the request names none: Settings > Theme AI & drawing > Planner model when it is
        installed, else the best one installed (names come best first)."""
        wanted = labsettings.value(self.lab, 'planner_model')
        return wanted if wanted in names else names[0]

    @staticmethod
    def design_offer(designer, prompt, rows):
        ready = designer.status()
        return dict(prompt=prompt, available=ready['available'], note='' if ready['available'] else 'The image model is not installed (' + ', '.join(ready['missing']) + ').',
                    seconds_per_drawing=ready.get('seconds_per_drawing') or design.DEFAULT_SECONDS,   # for the finish time of a big set
                    targets=[dict(key=r['key'], name=r.get('label') or r['name'], file=r['name'], size=[r['width'], r['height']],
                                  sheet=designer.lab.catalog.rows.get(r.get('parent', ''), {}).get('name', ''),   # the queue shows the theme.png copy
                                  **(food_set(designer, r) if design.is_food(r) else {'set': 'background'} if design.is_menu_background(r) else
                                     {'set': 'arena'} if design.is_arena_background(r) else {'set': 'title'} if r['name'] == 'logo' else {})) for r in rows])

    def plan_request(self, message, model='', history=None, scope=None, language=''):
        """Turn a request into a shading plan: exactly from tag words, else with the local model, else from keywords.
        language: the lab's language, which the model writes its reply in."""
        low = message.lower()
        # Open-ended: praise with no look in it once those words are set aside ("make the game look cool", not "cool red buttons").
        vague = bool(re.search(VAGUE, low)) and not changes_anything(rule_plan(re.sub(VAGUE, ' ', low), self.named))
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
        if not follow_up and not vague and not unknown_words(message, self.named):
            # Written entirely in tag words ("rainbow outline, subtle glow, dark inside"): plan it exactly as
            # said. The model is for requests that need interpreting, and for follow-ups that build on a theme.
            exact = rule_plan(message, self.named)
            if changes_anything(exact): plan, source = dict(exact, reply='Planned exactly from your tag words.'), 'tags'
        if plan is None and available:
            model = model if model in names else self.preferred(names)
            turns = [dict(role='system', content=self.system_prompt() + reply_language(language))]
            for turn in (history or [])[-8:]:
                if isinstance(turn, dict) and turn.get('role') in ('user', 'assistant') and isinstance(turn.get('content'), str):
                    turns.append(dict(role=turn['role'], content=turn['content'][:4000]))
            hints = self.hints(message)
            brief = ('\n\nThis request is open-ended, so the look is up to you: be bold and creative. Pick one clear concept and make '
                     'it obvious across the game with several operations (a palette or gradient on buttons and panels, outlines and '
                     'glows on icons, a finish like gloss), and say the concept in the reply. A small hue shift is not enough. '
                     f'Do not default to cyberpunk; one idea you could build on: {random.choice(CONCEPTS)}.') if vague else ''
            turns.append(dict(role='user', content=message + brief + ('\n\nSprites named in this request: ' + '; '.join(hints) if hints else '')
                              + (f'\n\nInspiration ({self.inspiration["title"]}, from Wikipedia; use its signature colors and mood): {self.inspiration["summary"]}' if getattr(self, 'inspiration', None) else '')))
            payload = dict(model=model, messages=turns, stream=False, format=SCHEMA, keep_alive='10m',
                           options=dict(temperature=0.6 if vague else 0.2, num_ctx=8192, num_predict=1400, **MODEL_OPTIONS.get(model, {})))
            if next(m for m in available if m['name'] == model)['thinking']: payload['think'] = False
            self.activity = f'Planning the look with {model}…'   # shown in the AI activity strip (activity.py)
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
                plan, source = tame(clean_plan(raw), said, bold=vague), model
                parts = {f for f, words in WORDS.items() if f != 'everything' and re.search(rf'\b(?:{words})\b', low)}
                if vague and parts and not re.search(WHOLE_GAME, low):
                    # "Make the buttons look sick": the bold look stays on the buttons, not the whole game.
                    kept = []
                    for op in plan['operations']:
                        aim = parts if op['targets'] == ['everything'] and not op['match'] else parts & set(op['targets'])
                        if aim: kept.append(dict(op, targets=sorted(aim)))
                    plan = dict(plan, operations=kept)
                named = [name for names_ in self.named(message)[0].values() for name in names_]
                if named and not re.search(r'\b(?:everything|all|whole|entire|theme)\b', message.lower()):
                    # The request was about named things; a step that forgot to name them would repaint a whole family.
                    for op in plan['operations']:
                        if not op['match']: op.update(targets=['everything'], match=named[:12])
                    # Steps that only differed in their targets are now the same step: keep one.
                    plan['operations'] = list({json.dumps(op, sort_keys=True): op for op in plan['operations']}.values())
            except (OSError, ValueError, KeyError, AttributeError) as exc:
                note = f'The local model {model} did not return a usable plan ({str(exc)[:120]}); used the keyword planner instead.'
            finally: self.activity = None
        elif plan is None: note = 'Ollama is not running, so the keyword planner was used. Start Ollama for full prompts.'
        if plan is None and not vague: plan = rule_plan(message, self.named)
        if vague and (plan is None or not changes_anything(plan)):
            # "Make it cool" with no model to interpret it: a bold, finished look instead of nothing (or a timid nudge).
            # Parts named ("make the buttons look sick") keep the look on them; otherwise a whole-game look.
            parts = [m.group(0) for f, words in WORDS.items() if f != 'everything' for m in [re.search(rf'\b(?:{words})\b', low)] if m]
            look = (f'{random.choice(("synthwave", "galaxy", "ocean", "sunset", "aurora", "candy", "cyberpunk"))} gradient on the {" and ".join(parts)} with a neon glow'
                    if parts else random.choice(CREATIVE_LOOKS))
            plan = dict(rule_plan(look, self.named), reply=f'You left the look open, so I picked one: “{look}”. Ask again for another, or name a mood, colors or parts to steer it.')
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
        tinted = self.tinted_targets(plan)
        if tinted:
            # The preview shows the art; the game then multiplies these icons by its own color, so say what that does.
            things = listing([self.label_of.get(name, name) for name in tinted][:4])
            color = next(iter(tinted.values())) if len(set(tinted.values())) == 1 else 'its own colors'
            note = (note + ' ' if note else '') + (f'The game colors {things} with {color} itself, so a new color mixes with that in the game '
                                                   '(gold on teal comes out green). Lighter or darker shades work; for a new look, redraw it and tick “Keep it white”.')
        if not changes_anything(plan) and not plan['reply']:
            plan['reply'] = 'I could not turn that into a visual change. Try naming a color, a mood, or a part of the game.'
        preview = self.preview(plan, scope) if changes_anything(plan) else None
        if preview and not plan['ui_colors'] and not plan.get('hide') and not any(line['sheets'] or line['regions'] for line in preview['lines']):
            # The model can name art that does not exist ("title*"); say so instead of claiming a change.
            plan['reply'] = ('That matched no art I can change, so nothing would happen. Try another name for the part '
                             '(ask "what can I change?"), or check it in the library first.')
        return dict(plan=plan, source=source, note=note, preview=preview)

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
        for op in plan['operations']:
            # A new color or gradient for the menu backgrounds alone fills their tiles (see opaque_tile).
            only_menu = (op['targets'] == ['menu_background'] and not op['match']) or (op['match'] and all(MENU_BACKGROUND.fullmatch(m) for m in op['match']))
            op['opaque'] = bool(only_menu and op['color_mode'] != 'none' and (op.get('gradient') or hex_color(op.get('color') or '')))
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
                tint = design.tints().get(row['name'], '')
                if tint:
                    # The game multiplies this white icon by its own color: show both the way the game will draw them.
                    rgb = np.array([int(tint[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255
                    before, after = (np.dstack([(p[..., :3] * rgb + 0.5).astype(np.uint8), p[..., 3]]) for p in (before, after))
                label = re.sub(r'\s*\([^)]*\)\s*$', '', row.get('label') or '')
                samples.append(dict(name=row['name'], label=label, key=row['key'], family=family, before=thumb(before), after=thumb(after), tint=tint))
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
