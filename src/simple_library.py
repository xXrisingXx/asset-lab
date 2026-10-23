"""The library in Simple mode: the parts of the game a player recognizes, by friendly name, one card each.

The full library lists every file (file names, archives, cache copies, data files). Simple lists only what players
change: the backgrounds, the menu buttons, what is pressed in a match, the viruses, the foods and the title, each under
the name a player knows ("Play button", "Game floor 2 (light mode)"). Identical copies (the coin the game also keeps in
its download cache, the title on three sheets) are one card: checking it checks all of them.
"""
import re

import button_sets
import design
from lab_core import LOCKED

CATEGORIES = [('backgrounds', 'Backgrounds'), ('buttons', 'Menu buttons'), ('ingame', 'In game'), ('viruses', 'Viruses'),
              ('food', 'Food'), ('title', 'Title')]
FOOD_SIZES = ('Food: the smallest', 'Food: the middle one', 'Food: the biggest')
# Names for art the UI map has no name for (or shares one: the star and rate buttons are both a star).
NAMES = {'icon-ingame-potion': 'Potion button', 'power-container': 'Potion slot', 'joystick-bg': 'Joystick ring', 'joystick': 'Joystick',
         'icon-ingame-potion-desktop': 'Potion button (P key)', 'icon-ingame-chat': 'In-game chat button',
         'icon-ingame-chat-desktop': 'In-game chat button (T key)', 'icon-report-abuse': 'Block player button',
         'icon-pause-button': 'Pause button',
         'icon-sell': 'SELL tag', 'icon-chat': 'Chat button', 'icon-remove': 'Remove X', 'icon-rate': 'Rate button (star)',
         'icon-star': 'Star button'}


def usable(r):
    return r and r.get('editable') and r.get('kind') in ('image', 'sprite') and not r.get('legacy') and not r.get('removed') \
        and not r.get('server_only') and r.get('name') not in LOCKED


def friendly(row):
    """The name a player knows: the UI map's name without where it shows ("Play button"), else a tidy file name."""
    if row['name'] in NAMES: return NAMES[row['name']]
    label = re.sub(r'\s*\((?:Main menu|Menus|in a match[^)]*|Ranks|Friends list|Player profile|Achievements|My profile)\)\s*$', '', row.get('label') or '')
    if label: return label
    name = re.sub(r'\.(png|jpe?g|webp)$', '', row['name'])
    name = re.sub(r'^(icon|button)[-_]', '', name).replace('-', ' ').replace('_', ' ').strip()
    return name[:1].upper() + name[1:] if name else row['name']


def groups(designer):
    """[(category, name, rows)] for the whole game: one entry per thing a player sees."""
    rows = designer.lab.catalog.rows
    out = []
    menu = sorted(designer.menu_backgrounds(), key=lambda r: r['name'])
    named = {}
    for r in menu:
        named.setdefault(friendly(r) if r.get('label') else None, []).append(r)
    unnamed = named.pop(None, [])
    out += [('backgrounds', name, rs) for name, rs in named.items()]
    out += [('backgrounds', f'Menu background {i}', [r]) for i, r in enumerate(unnamed, start=len(named) + 1)]
    floor = lambda r: (int(re.sub(r'\D', '', r['name'].replace('_w', '')) or 0), r['name'].lower().endswith('_w.png'))
    for r in sorted(designer.arena_backgrounds(), key=floor):   # 1 dark, 1 light, 2 dark...
        number, light = floor(r)
        out.append(('backgrounds', f'Game floor {number + 1} ({"light" if light else "dark"} mode)', [r]))
    menu_names = set(button_sets.MENU)
    ingame_names = set(button_sets.PHONE + button_sets.PC + button_sets.HUD)
    buttons, ingame = {}, {}
    for r in rows.values():
        if not usable(r): continue
        label = r.get('label') or ''
        if r['name'] in ingame_names or re.search(r'\(in a match', label): ingame.setdefault(friendly(r), []).append(r)
        elif r['name'] in menu_names or re.search(r'\((?:Main menu|Menus)\)$', label):
            if r['name'] in design.KEEP_AS_IS or r['name'].startswith('logo'): continue
            buttons.setdefault(friendly(r), []).append(r)
    out += [('buttons', name, rs) for name, rs in sorted(buttons.items())]
    out += [('ingame', name, rs) for name, rs in sorted(ingame.items())]
    viruses = {}
    for r in rows.values():
        if r['name'] in design.VIRUSES and usable(r): viruses.setdefault(friendly(r), []).append(r)
    out += [('viruses', name, rs) for name, rs in sorted(viruses.items())]
    for i, kind in enumerate(list(designer.food_kinds())[:3]):
        kept = [r for r in kind if usable(r)]
        if kept: out.append(('food', FOOD_SIZES[i], kept))
    title = [r for r in rows.values() if r['name'] == 'logo' and usable(r)]
    if title: out.append(('title', 'The MITOSIS title', title))
    return [(c, n, rs) for c, n, rs in out if any(usable(r) for r in rs)]


def listing(designer, category='', query='', offset=0, limit=60):
    """One page of cards: {items: [{key (shown), keys (all copies), name, category, width, height, saved}], total, categories}."""
    lab = designer.lab
    with lab.lock:
        found = groups(designer)
        counts = {c: 0 for c, _ in CATEGORIES}
        for c, _, _ in found: counts[c] += 1
        words = query.casefold().split()
        if category: found = [g for g in found if g[0] == category]
        if words:
            found = [g for g in found if all(w in (g[1] + ' ' + ' '.join(r['name'] for r in g[2])).casefold() for w in words)]
        page = found[offset:offset + limit]
        items = []
        for c, name, rs in page:
            shown = max((r for r in rs if usable(r)), key=lambda r: (r.get('primary', True), r['width'] * r['height']))
            items.append(dict(key=shown['key'], keys=[r['key'] for r in rs if usable(r)], name=name, category=c,
                              width=shown['width'], height=shown['height'], saved=any(bool(lab.current(r['key'])) for r in rs)))
    return dict(items=items, total=len(found), offset=offset, limit=limit,
                categories=[dict(id=c, name=n, count=counts[c]) for c, n in CATEGORIES if counts[c]])
