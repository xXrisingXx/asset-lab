"""Discover Mitos.is visual assets, including Starling atlases and BMFont glyphs."""
from collections import Counter
import hashlib
import io
import os
from functools import lru_cache
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from PIL import Image
from packages import safe_member
from cache_index import visual_names
from visual_tags import potion_family

APP_ID = '389570'
GROUPS = ['Backgrounds', 'Buttons & panels', 'Cells & gameplay', 'Downloaded skins', 'Potions & alchemy', 'Equipment', 'Effects', 'Icons & interface', 'Fonts', 'Other visuals', 'Audio', 'Metadata']


def find_game():
    roots = [Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')) / 'Steam']
    if os.name == 'nt':
        import winreg
        for hive, path, name in [(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                                 (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')]:
            try:
                with winreg.OpenKey(hive, path) as k:
                    roots.append(Path(winreg.QueryValueEx(k, name)[0]))
            except OSError:
                pass
    for steam in list(roots):
        libraries = steam / 'steamapps/libraryfolders.vdf'
        if libraries.is_file():
            for value in re.findall(r'"path"\s+"([^"]+)"', libraries.read_text(encoding='utf-8')):
                roots.append(Path(value.replace('\\\\', '\\')))
    for steam in dict.fromkeys(roots):
        manifest = steam / f'steamapps/appmanifest_{APP_ID}.acf'
        if manifest.is_file():
            match = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text(encoding='utf-8'))
            if match:
                game = steam / 'steamapps/common' / match.group(1)
                if (game / 'Mitosis.exe').is_file():
                    return game.resolve()
    raise ValueError('Mitos.is was not found in Steam. Start with --game "path to Mitos.is The Game".')


def asset_key(source, entry='', region=''):
    return hashlib.sha256((source + '\0' + entry + '\0' + region).encode()).hexdigest()[:24]


def category(name, sheet='', font=False):
    n, s = name.lower(), sheet.lower()
    if font: return 'Fonts'
    if potion_family(name, sheet): return 'Potions & alchemy'
    if 'metalworks' in s or 'metalworks' in n: return 'Buttons & panels'
    if s == 'items.png' or n == 'items.png' or n.startswith('box_'): return 'Equipment'
    if s == 'flags.png' or n == 'flags.png': return 'Icons & interface'
    if re.match(r'bg\d|bg-|worldbg', n) or any(v in n for v in ('soccer-field', 'grid', 'map-tile', 'cloud', 'ingame-monts', 'grass', 'pattern', 'home-tiles')): return 'Backgrounds'
    if n.startswith('button') or (not n.startswith('icon') and any(v in n for v in PANEL_WORDS)): return 'Buttons & panels'
    if any(v in n for v in ('particle', 'glow', 'lightning', 'emissive', 'heal', 'noise', 'portal', 'scratch', 'star7', 'slot-glow')): return 'Effects'
    if any(v in n for v in ('icon', 'server_', 'logo', 'vip', 'youwin', 'youlose', 'coin', 'chest', 'diamond', 'palette', 'versus', 'crown')) or n in ('theme.png', 'theme2.png', 'theme4.png', 'misc.png'):
        return 'Icons & interface'
    if s == 'sprites.png' or n == 'sprites.png' or any(v in n for v in CELL_WORDS): return 'Cells & gameplay'
    if s.startswith('theme') or s in ('icons.png', 'misc.png'): return 'Icons & interface'
    return 'Other visuals'


PANEL_WORDS = ('button', 'btn', 'ar-bt', 'background', 'menu-gradient', 'header-bg', 'footer-bg', 'frame', 'panel', 'popup', 'scroll-bar',
               'slider', 'tab-', 'check-', 'list-item', 'list-inset', 'container', 'rounded-bg', 'tooltip', 'separator', 'slot-bg', 'progress',
               'loader', 'selector', 'joystick', 'experience-bar', 'equipment-bar', 'arrow', 'callout', 'radio-', 'picker-', 'page-indicator',
               'toggle', 'shading-', 'shaded-', 'linear-', 'gui-', 'indicator', 'ballon', 'baloon', 'bubble-no', 'standard')
CELL_WORDS = ('base', 'border', 'circle', 'virus', 'snake', 'ball', 'skin', 'cell', 'food', 'flag', 'goal', 'target', 'power', 'rope', 'spawner',
              'rock', 'ice', 'net', 'blade', 'thor', 'fist', 'cart', 'candy', 'egg', 'gift', 'snowman', 'pumpk', 'choco', 'beer', 'bird', 'bubbl',
              'bullet', 'gun', 'wall', 'rail', 'ship', 'medikit', 'needle', 'ray0', 'pow', 'field', 'quad')


def read_xml(data):
    if len(data) > 8 * 1024 * 1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Unsupported XML declarations or excessive size')
    return ET.fromstring(data)


class Catalog:
    def __init__(self, game, source_path, cache=None):
        self.game, self.source_path = Path(game), source_path
        self.cache = Path(cache) if cache else None
        self.rows, self.files, self.children, self.warnings = {}, {}, {}, []
        self.containers, self.cache_names = {}, {}
        self._scan()

    def _add(self, row):
        self.rows[row['key']] = row
        if row.get('parent'): self.children.setdefault(row['parent'], []).append(row['key'])

    def _scan(self):
        sources = [(p, p.relative_to(self.game).as_posix()) for p in sorted((self.game / 'assets').rglob('*')) if p.is_file()]
        if self.cache and self.cache.is_dir():
            try: self.cache_names = visual_names(self.cache)
            except (ValueError, OSError) as exc: self.warnings.append('Cache labels unavailable: ' + str(exc))
            for file in sorted(self.cache.glob('*.dat')):
                if not re.fullmatch(r'[a-f0-9]{32}\.dat', file.name): continue
                with file.open('rb') as stream: magic=stream.read(8)
                if magic.startswith((b'\x89PNG\r\n\x1a\n', b'PK\x03\x04')):
                    sources.append((file, 'cache/' + file.name))
        for file, source in sources:
            allowed = self.cache if source.startswith('cache/') else self.game
            if file.is_symlink() or not file.resolve().is_relative_to(allowed.resolve()):
                continue
            # A damaged or half-written file (a download the game did not finish, a cut-off archive) is left out with a
            # note: one bad file must never stop the lab from opening (1.0.56 stopped on "File is not a zip file").
            before = set(self.rows)
            try: self._read_source(source)
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError, KeyError) as exc:
                for key in set(self.rows) - before: self.rows.pop(key, None)
                for key in list(self.children):
                    if key not in self.rows: self.children.pop(key, None)
                self.containers.pop(source, None)
                for key in [k for k in self.files if k[0] == source]: self.files.pop(key)
                self.warnings.append(f'{source} could not be read ({type(exc).__name__}: {exc}) and was left out. '
                                     + ('It is a download in the game’s cache: Mitosis downloads it again when it needs it.' if source.startswith('cache/')
                                        else 'If its art is missing, verify the game files in Steam (right-click Mitos.is > Properties > Installed Files).'))
        # Loose XMLs can refer to other loose files. Matching basename wins over stale imagePath.
        for file, source in sources:
            if file.suffix.lower() in ('.xml', '.fnt'):
                self._atlas(source, '', self.source_path(source).read_bytes(), lambda n: (PurePosixPath(source).parent / n).as_posix(), '')
        for parent, children in self.children.items():
            self.rows[parent]['sprite_count'] = len(children)
        self.stats = dict(Counter(r['kind'] for r in self.rows.values()))
        self.stats['visuals'] = sum(r['kind'] in ('image', 'sprite', 'glyph') for r in self.rows.values())

    def _read_source(self, source):
        """Every asset in one file of the game: an archive's entries, or the file itself."""
        actual = self.source_path(source)
        with actual.open('rb') as stream: magic=stream.read(8)
        self.containers[source] = 'zip' if magic.startswith(b'PK\x03\x04') else 'image' if magic.startswith(b'\x89PNG\r\n\x1a\n') else 'metadata'
        if self.containers[source] == 'zip':
            with zipfile.ZipFile(actual) as archive:
                infos = archive.infolist()
                if len(infos) > 10000 or len({i.filename for i in infos}) != len(infos):
                    raise ValueError('Unexpected archive inventory')
                entries = {}
                for info in infos:
                    safe_member(info.filename)
                    if not info.is_dir():
                        entries[info.filename] = (info.file_size, lambda n=info.filename: archive.read(n))
                self._container(source, entries)
        else:
            self._container(source, {'': (actual.stat().st_size, actual.read_bytes)}, parse_atlases=False)

    def _container(self, source, entries, parse_atlases=True):
        for entry, (size, read) in entries.items():
            name = PurePosixPath(entry or source).name
            label = self.cache_names.get(PurePosixPath(source).name, '') if source.startswith('cache/') else ''
            if not entry and source.startswith('cache/'):
                name = label or ('cached-' + PurePosixPath(source).stem + '.png')
            ext = PurePosixPath(name).suffix.lower()
            kind = 'image' if ext == '.png' else 'audio' if ext == '.ogg' else 'metadata'
            key = asset_key(source, entry)
            row = dict(key=key, name=name, source=source, entry=entry, kind=kind, size=size, editable=kind == 'image',
                       group=category(name, font=name.endswith('_0.png')) if kind == 'image' else 'Audio' if kind == 'audio' else 'Metadata')
            row['source_label'] = label or source
            if name.startswith('skins/'): row['group'] = 'Downloaded skins'
            if kind == 'image':
                with Image.open(io.BytesIO(read())) as im:
                    if im.width * im.height > 4096 * 4096: raise ValueError('Texture exceeds supported image size')
                    row.update(width=im.width, height=im.height, mode=im.mode)
            self._add(row)
            self.files[(source, entry)] = key
        if parse_atlases:
            for entry, (_, read) in entries.items():
                if entry.lower().endswith(('.xml', '.fnt')):
                    self._atlas(source, entry, read(), lambda n: source, entry)

    def _atlas(self, source, entry, data, loose_source, atlas_entry):
        try:
            root = read_xml(data)
            if root.tag == 'TextureAtlas':
                stem = PurePosixPath(entry or source).stem + '.png'
                declared = root.get('imagePath', stem)
                candidates = [stem, declared]
                parent = None
                for name in candidates:
                    candidate_entry = (PurePosixPath(entry).parent / name).as_posix() if entry else ''
                    parent = self.files.get((loose_source(name), candidate_entry))
                    if parent: break
                if not parent:
                    self.warnings.append(f'No image sheet for {source}:{entry}')
                    return
                sheet = self.rows[parent]
                if PurePosixPath(sheet['entry'] or sheet['source']).name != declared:
                    sheet['pairing_note'] = f'Uses matching sheet; XML imagePath is {declared}.'
                for index, child in enumerate(root.findall('SubTexture')):
                    self._region(parent, child.attrib, 'sprite', source, atlas_entry, index)
            elif root.tag == 'font':
                pages = {c.get('id'): c.get('file') for c in root.findall('pages/page')}
                for index, child in enumerate(root.findall('chars/char')):
                    a = dict(child.attrib)
                    file = pages.get(a.get('page', '0'))
                    if not file: continue
                    parent = self.files.get((loose_source(file), (PurePosixPath(entry).parent / file).as_posix() if entry else ''))
                    if parent:
                        num = int(a['id'])
                        a['name'] = f'U+{num:04X} ' + (chr(num) if num >= 32 and num != 127 else 'space/control')
                        self._region(parent, a, 'glyph', source, atlas_entry, index)
        except (ET.ParseError, ValueError, KeyError) as exc:
            self.warnings.append(f'{source}:{entry}: {exc}')

    def _region(self, parent, a, kind, source, atlas_entry, index):
        sheet = self.rows[parent]
        x, y, w, h = (int(a.get(k, 0)) for k in ('x', 'y', 'width', 'height'))
        if w <= 0 or h <= 0: return
        if min(x, y) < 0 or x + w > sheet['width'] or y + h > sheet['height']:
            self.warnings.append(f'Skipped out-of-bounds region {a.get("name", "?")} in {source}:{atlas_entry}')
            return
        rotated = a.get('rotated', 'false').lower() == 'true'
        name = a['name']
        key = asset_key(sheet['source'], sheet['entry'], f'{atlas_entry}:{index}:{name}')
        self._add(dict(key=key, name=name, source=sheet['source'], entry=sheet['entry'], kind=kind, parent=parent,
                       source_label=sheet.get('source_label', sheet['source']),
                       rect=[x, y, w, h], rotated=rotated, width=h if rotated else w, height=w if rotated else h,
                       group=category(name, sheet['name'], kind == 'glyph'), editable=True,
                       metadata=dict(a), atlas=f'{source}:{atlas_entry}', size=0))

    def raw(self, row):
        path = self.source_path(row['source'])
        if row['entry']:
            with zipfile.ZipFile(path) as archive: return archive.read(row['entry'])
        return path.read_bytes()

    def image(self, key):
        row = self.rows[key]
        image = self._sheet(row.get('parent', key))
        if row.get('parent'):
            x, y, w, h = row['rect']
            image = image.crop((x, y, x+w, y+h))
            if row['rotated']: image = image.transpose(Image.Transpose.ROTATE_90)
        return image.copy()

    @lru_cache(maxsize=12)
    def _sheet(self, key):
        with Image.open(io.BytesIO(self.raw(self.rows[key]))) as image:
            return image.convert('RGBA')
