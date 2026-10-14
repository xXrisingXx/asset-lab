"""Art families verified against the installed item sheets, not item gameplay data."""
import re

POTION_TILES = {
    64, 65, 66, 67, 69, 960, 2003, 2008, 2009, 2010, 2011, 2012,
    2074, 3458, 3459, 3460, 3461, 3471,
    *range(928, 942), *range(961, 966), *range(970, 978),
    *range(2024, 2032), *range(2075, 2109), *range(2130, 2138),
    *range(2160, 2171), *range(3716, 3721),
}


def potion_family(name, sheet=''):
    """Exact curated memberships: a vague 'pot' substring also matches 'depot'."""
    if name in ('icon-ingame-potion', 'icon-ingame-potion-desktop'):
        return 'Potion interface icon'
    if sheet != 'items.png': return ''
    if name in ('bottle', 'speed_gravity'): return 'Potion bottle'
    match = re.fullmatch(r'tile7_(\d+)', name)
    if match:
        number = int(match[1])
        if number in POTION_TILES: return 'Potion / vial artwork'
        if number in (2005, 2006, 2007, 2015): return 'Alchemy artwork'
    if re.fullmatch(r'tile_(?:30|31|32|33|34|37|38|39|40|41|42|51|52|53|54|55)(?:_b)?', name):
        return 'Potion / vial artwork'
    return ''
