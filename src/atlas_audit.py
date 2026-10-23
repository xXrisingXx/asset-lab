"""Find leftover atlas regions and identical copies so the library shows each sprite once.

The theme atlases still list hundreds of names from an older packing. Their rectangles
now cut through unrelated art. A region is treated as a leftover only when the atlas is
visibly stale, the executable never names it, and its rectangle is cut or collides with a
region the executable does name. Nothing is deleted: leftovers and copies stay reachable.
"""
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import numpy as np

VERSION = 4
CUT = 0.05  # share of a rectangle's outline where opaque pixels continue outside it
# Matched to the current in-game toolbar. Some of these names are assembled/encoded in native code;
# absence from a plain-string scan must not classify their valid rectangles as leftovers.
SCREEN_VERIFIED = {'icon-ingame-potion', 'icon-ingame-potion-desktop', 'icon-report-abuse',
                   'icon-ingame-chat', 'icon-ingame-chat-desktop', 'icon-pause-button'}


def exe_names(game):
    exe = Path(game) / 'Mitosis.exe'
    if not exe.is_file() or exe.stat().st_size > 512 * 1024 * 1024: return set()
    return {m.decode('ascii') for m in re.findall(rb'[\x21-\x7e][\x20-\x7e]{1,62}[\x21-\x7e]', exe.read_bytes())}


def referenced(name, names):
    stem = re.sub(r'\.png$', '', name)
    if name in SCREEN_VERIFIED or name in names or stem in names: return True
    # Numbered families are assembled at run time: "food" + 2, "icon_resources_" + 1 + "_small".
    m = re.match(r'^(.*?[-_a-zA-Z])(\d+)(.*)$', stem)
    return bool(m and m.group(1) in names and (not m.group(3) or m.group(3) in names or m.group(3).lstrip('-_') in names))


def manifest_files(game):
    manifest = Path(game) / 'manifest' / 'default.json'
    try: text = json.loads(manifest.read_text(encoding='utf-8')).get('assets', '')
    except (OSError, ValueError, AttributeError): return set()
    # Haxe serialization: y<length>:<url-encoded string>
    text = text if isinstance(text, str) else ''
    found = {urllib.parse.unquote(text[m.end():m.end() + int(m.group(1))]) for m in re.finditer(r'y(\d{1,4}):', text)}
    return {name for name in found if name.startswith('assets/')}


def rank(row, listed):
    """Lower is the copy most likely to be the one the game draws."""
    source = row['source']
    if source.startswith('cache/'): return 3
    if row['entry']: return 1
    return 0 if not listed or source in listed else 2


def _signature(catalog, game):
    h = hashlib.sha1(str(VERSION).encode())
    for source in sorted({r['source'] for r in catalog.rows.values()}):
        stat = catalog.source_path(source).stat()
        h.update(f'{source}|{stat.st_size}|{stat.st_mtime_ns}\n'.encode())
    for extra in (Path(game) / 'Mitosis.exe', Path(game) / 'manifest' / 'default.json'):
        if extra.is_file(): h.update(f'{extra.name}|{extra.stat().st_size}|{extra.stat().st_mtime_ns}\n'.encode())
    h.update('|'.join(sorted(catalog.rows)).encode())
    return h.hexdigest()


def _measure(catalog, game):
    names = exe_names(game)
    facts = {}
    for sheet_key, sheet in catalog.rows.items():
        if sheet['kind'] != 'image': continue
        pixels = np.asarray(catalog._sheet(sheet_key))
        facts[sheet_key] = dict(pix=hashlib.sha1(pixels.tobytes()).hexdigest(), cut=0.0, legacy=False)
        children = [catalog.rows[k] for k in catalog.children.get(sheet_key, [])]
        if not children: continue
        solid = pixels[..., 3] > 40
        height, width = solid.shape
        for row in children:
            x, y, w, h = row['rect']
            crossing = edge = 0
            if y > 0: crossing += int((solid[y, x:x+w] & solid[y-1, x:x+w]).sum()); edge += w
            if y + h < height: crossing += int((solid[y+h-1, x:x+w] & solid[y+h, x:x+w]).sum()); edge += w
            if x > 0: crossing += int((solid[y:y+h, x] & solid[y:y+h, x-1]).sum()); edge += h
            if x + w < width: crossing += int((solid[y:y+h, x+w-1] & solid[y:y+h, x+w]).sum()); edge += h
            crop = np.ascontiguousarray(pixels[y:y+h, x:x+w])
            facts[row['key']] = dict(pix=hashlib.sha1(crop.tobytes() + (b'r' if row['rotated'] else b'')).hexdigest(),
                                     cut=round(crossing / edge, 4) if edge else 0.0, legacy=False)
        atlases = {}
        for row in children:
            if row['kind'] == 'sprite': atlases.setdefault(row['atlas'], []).append(row)
        for rows in atlases.values():
            r = np.array([row['rect'] for row in rows], dtype=np.int64)
            x0, y0, x1, y1 = r[:, 0], r[:, 1], r[:, 0] + r[:, 2], r[:, 1] + r[:, 3]
            touch = (x0[:, None] < x1[None]) & (x0[None] < x1[:, None]) & (y0[:, None] < y1[None]) & (y0[None] < y1[:, None])
            inside = (x0[:, None] >= x0[None]) & (y0[:, None] >= y0[None]) & (x1[:, None] <= x1[None]) & (y1[:, None] <= y1[None])
            partial = touch & ~inside & ~inside.T
            # A correctly packed atlas has no partially overlapping rectangles at all.
            if partial.sum() // 2 < max(8, len(rows) // 10) or not names: continue
            known = np.array([referenced(row['name'], names) for row in rows])
            same = inside & inside.T
            collides = (touch & ~same & known[None]).any(axis=1)
            for row, is_known, hit in zip(rows, known, collides):
                if not is_known and (hit or facts[row['key']]['cut'] > CUT): facts[row['key']]['legacy'] = True
    return facts


def annotate(catalog, game, cache_file=None):
    """Add legacy, live, dup, copies and primary to every editable catalog row."""
    signature, facts = _signature(catalog, game), None
    if cache_file and Path(cache_file).is_file():
        try:
            saved = json.loads(Path(cache_file).read_text(encoding='utf-8'))
            if saved.get('signature') == signature: facts = saved['facts']
        except (OSError, ValueError, KeyError): facts = None
    if facts is None:
        facts = _measure(catalog, game)
        if cache_file:
            Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_file).write_text(json.dumps(dict(signature=signature, facts=facts)), encoding='utf-8')
    listed = manifest_files(game)
    groups = {}
    for key, row in catalog.rows.items():
        fact = facts.get(key)
        if not fact: continue
        row['legacy'], row['cut'] = bool(fact['legacy']), fact['cut']
        row['live'] = ('cache', 'archive' if row['entry'] else 'loose')[not row['source'].startswith('cache/')]
        if row['live'] == 'loose' and listed and row['source'] not in listed: row['live'] = 'unlisted'
        row['dup'] = hashlib.sha1(f"{row['kind'] != 'image'}|{row['name']}|{row['width']}x{row['height']}|{fact['pix']}".encode()).hexdigest()[:20]
        groups.setdefault(row['dup'], []).append(key)
    for keys in groups.values():
        keys.sort(key=lambda k: (catalog.rows[k]['legacy'], rank(catalog.rows[k], listed), catalog.rows[k]['source'], k))
        for index, key in enumerate(keys):
            catalog.rows[key]['primary'], catalog.rows[key]['copies'] = index == 0, len(keys)
    catalog.copies = groups
    catalog.stats['legacy'] = sum(bool(r.get('legacy')) for r in catalog.rows.values())
    catalog.stats['unique_visuals'] = sum(1 for r in catalog.rows.values() if r.get('primary') and not r.get('legacy'))
    return facts
