"""Mitos.is Asset Lab: versioned pixel edits and reversible visual-only game installs."""
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import numpy as np
from catalog import Catalog, GROUPS, find_game
from packages import replace_zip
import atlas_audit
from pool import Pool, Favorites, Labels
import exe_palette
import similar

EXE = 'Mitosis.exe'
from asset_notes import AssetNotes

ROOT = Path(__file__).resolve().parent
# The release number (the launcher passes it; a source checkout reads the VERSION file beside src).
VERSION = os.environ.get('ASSET_LAB_VERSION') or ((ROOT.parent / 'VERSION').read_text().strip() if (ROOT.parent / 'VERSION').is_file() else 'dev')
# The Mitosis UI map that ships with Asset Lab: names for interface sprites and art the game never shows (see merge_defaults).
DEFAULTS = ROOT / 'data' / 'ui-defaults.json'
MAX_IMAGE = 4096 * 4096


def digest(data): return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Antivirus, OneDrive or Steam can hold a file open for a moment right after it was written; retry briefly
        # instead of failing an install halfway.
        for attempt in range(20):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 19: raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temp.exists(): temp.unlink()


def atomic_json(path, data): atomic_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8'))


def read_json(path, fallback=None):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else fallback


def png(image):
    out = io.BytesIO()
    image.save(out, format='PNG', compress_level=6)
    return out.getvalue()


def decode_png(data):
    with Image.open(io.BytesIO(data)) as im:
        if im.format != 'PNG' or im.width * im.height > MAX_IMAGE:
            raise ValueError('Use a PNG of at most 4096 × 4096 pixels.')
        im.load()
        return im.convert('RGBA')


def overlap(a, b):
    x, y, w, h = a
    X, Y, W, H = b
    return x < X + W and X < x + w and y < Y + H and Y < y + h


def game_processes(game=None):
    if os.name != 'nt': return []
    run = subprocess.run(['tasklist.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True,
                         encoding='utf-8', errors='replace', creationflags=0x08000000, timeout=15)
    if run.returncode: raise ValueError('Could not check running games. Close Mitosis and try again.')
    matches = [row for row in csv.reader(io.StringIO(run.stdout)) if row and row[0].lower() == 'mitosis.exe']
    if game is None: return [r[0] for r in matches]
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    found = []
    expected = str((Path(game) / 'Mitosis.exe').resolve()).casefold()
    for row in matches:
        handle = kernel.OpenProcess(0x1000, False, int(row[1]))
        if not handle:
            found.append(row[0])  # Unknown identity must not weaken the live-game guard.
            continue
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) or str(Path(buf.value).resolve()).casefold() == expected:
                found.append(row[0])
        finally: kernel.CloseHandle(handle)
    return found


def adjust(data, settings):
    """Local color work. Tint preserves luminance detail and the original alpha channel."""
    im = decode_png(data)
    alpha = im.getchannel('A')
    rgb = im.convert('RGB')
    def number(name, default, low, high):
        v = float(settings.get(name, default))
        if not np.isfinite(v) or not low <= v <= high: raise ValueError('Invalid ' + name)
        return v
    rgb = ImageEnhance.Brightness(rgb).enhance(number('brightness', 1, .1, 3))
    rgb = ImageEnhance.Contrast(rgb).enhance(number('contrast', 1, .1, 3))
    rgb = ImageEnhance.Color(rgb).enhance(number('saturation', 1, 0, 3))
    hue = number('hue', 0, -180, 180)
    if hue:
        hsv = np.array(rgb.convert('HSV'))
        hsv[..., 0] = (hsv[..., 0].astype(np.int16) + round(hue / 360 * 255)) % 256
        rgb = Image.fromarray(hsv, 'HSV').convert('RGB')
    tint = settings.get('tint', '#69d7da')
    amount = number('tint_amount', 0, 0, 1)
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', tint): raise ValueError('Invalid tint color')
    if amount:
        values = np.array(rgb, dtype=np.float32)
        # Use source HSV value to retain the skin's border, gradient and specular highlight.
        shade = values.max(axis=2) / 255.0
        color = np.array([int(tint[i:i+2], 16) for i in (1, 3, 5)], dtype=np.float32)
        chroma = values.max(axis=2) - values.min(axis=2)
        # Keep neutral bright highlights and dark outlines; recolor chromatic skin pixels.
        mask = np.clip(chroma / 32.0, 0, 1)[..., None] * amount
        values = values * (1-mask) + (shade[..., None] * color) * mask
        rgb = Image.fromarray(np.clip(values, 0, 255).astype('uint8'))
    strength = number('sharpen', 0, 0, 3)
    if strength: rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.2, percent=round(strength*100), threshold=2))
    noise = number('noise', 0, 0, .25)
    if noise:
        rng = np.random.default_rng(int(number('seed', 7, 0, 1000000)))
        array = np.array(rgb, dtype=np.float32)
        array += rng.normal(0, noise*64, (im.height, im.width, 1))
        rgb = Image.fromarray(np.clip(array, 0, 255).astype('uint8'))
    rgb.putalpha(alpha)
    return png(rgb)


def data_home():
    """Where workspaces, install ledgers and marks live: ASSET_LAB_HOME, else the folder an older version already uses."""
    if os.environ.get('ASSET_LAB_HOME'): return Path(os.environ['ASSET_LAB_HOME'])
    older = Path.home() / 'MitosisAssetLab'
    return older if older.is_dir() else Path.home() / 'AssetLab'


class Lab:
    def __init__(self, game=None, workspace=None, ledger_dir=None, process_check=None, cache=None):
        self.game = Path(game).resolve() if game else find_game()
        if not (self.game / 'Mitosis.exe').is_file() or not (self.game / 'assets').is_dir():
            raise ValueError('Select the Mitos.is installation containing Mitosis.exe and assets.')
        self.root = Path(workspace or data_home() / 'workspace').resolve()
        if self.root.is_relative_to(self.game) or self.game.is_relative_to(self.root):
            raise ValueError('Keep the editing workspace separate from the game installation.')
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cache = Path(cache).resolve() if cache else None
        if cache is None:
            try:
                if self.game == find_game():
                    candidate=Path(os.environ.get('APPDATA', str(Path.home()/'AppData/Roaming'))) / 'Freakinware/Mitosis'
                    if candidate.is_dir(): self.cache=candidate.resolve()
            except (ValueError, OSError): pass
        self.process_check = process_check or (lambda: game_processes(None if self.cache else self.game))
        self.session = secrets.token_urlsafe(32)
        self.game_id = digest(str(self.game).casefold().encode())[:24]
        self.ledger_dir = Path(ledger_dir or data_home() / 'install-ledgers').resolve()
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = self.ledger_dir / (self.game_id + '.json')
        self.state_path = self.root / 'state.json'
        self.state = read_json(self.state_path, {'game': str(self.game), 'baseline': {}, 'edits': {}, 'staged': []})
        if Path(self.state['game']).resolve() != self.game:
            raise ValueError('This workspace belongs to another game installation.')
        if self.active() and self.active().get('workspace') != str(self.root):
            raise ValueError('A test is installed from another workspace. Open that workspace to restore it first.')
        self.catalog = Catalog(self.game, self.source_path, self.cache)
        for row in self.catalog.rows.values():
            source = row['source']
            if source not in self.state['baseline']:
                f = self.game_file(source)
                self.state['baseline'][source] = {'sha256': file_hash(f), 'size': f.stat().st_size}
        if exe_palette.BLOCKED and any([self.state.pop('palette', None), self.state.pop('hide', None)]):
            self.catalog.warnings.append('Saved game colors and hidden menu buttons were cleared: ' + exe_palette.BLOCKED)
        self.persist()
        self._thumbs = {}
        self._notes = None
        self.catalog.copies = {}
        try: atlas_audit.annotate(self.catalog, self.game, self.root / 'audit-cache.json')
        except (OSError, ValueError, KeyError) as exc: self.catalog.warnings.append('Sprite audit unavailable: ' + str(exc))
        # Shared by every workspace beside this one, so a removal outlives game updates.
        self.pool = Pool(self.catalog, self.root.parent / 'removed-assets.json')
        self.favorites = Favorites(self.catalog, self.root.parent / 'favorite-assets.json')
        self.labels = Labels(self.catalog, self.root.parent / 'asset-names.json')
        self.merge_defaults()
        self.similar = similar.Index(self.catalog)

    def merge_defaults(self):
        """Add the shipped UI map (names for interface sprites; art the game never shows taken out of the pool), each entry
        once and only for sprites this game has. Whatever you rename, unname or restore afterwards stays as you left it."""
        doc = read_json(DEFAULTS, {}) or {}
        record = self.root.parent / 'defaults-applied.json'
        seen = set((read_json(record, {}) or {}).get('applied') or [])
        rows = {self.pool.ident(row): row for row in self.catalog.rows.values()}
        fresh = [(marks, kind, entry) for marks, kind, field in ((self.labels, 'label', 'labels'), (self.pool, 'removed', 'removed'))
                 for entry in doc.get(field) or []
                 if isinstance(entry, dict) and entry.get('id') in rows and f"{kind}:{entry['id']}" not in seen]
        if not fresh: return
        for marks, kind, entry in fresh:
            if entry['id'] in marks.entries: continue
            row = rows[entry['id']]
            item = dict(id=entry['id'], name=row['name'], kind=row['kind'], sheet=marks.sheet_name(row))
            if kind == 'label': item['label'] = ' '.join(str(entry.get('label') or '').split())[:80]
            else: item.update(group=row['group'], at=time.strftime('%Y-%m-%d %H:%M:%S'), by='Asset Lab UI map')
            if kind == 'removed' or item['label']: marks.entries[entry['id']] = item
        for marks in (self.labels, self.pool): marks.apply(); marks.store()
        atomic_json(record, {'note': 'UI map entries Asset Lab already added once (renames and restores are kept). Delete this file to add them all again.',
                             'applied': sorted(seen | {f"{kind}:{entry['id']}" for _, kind, entry in fresh})})

    def persist(self): atomic_json(self.state_path, self.state)

    def game_file(self, relative):
        if relative.startswith('cache/'):
            name=relative[len('cache/'):]
            if not self.cache or not re.fullmatch(r'[a-f0-9]{32}\.dat', name):
                raise ValueError('Only recognized visual-cache files may be edited.')
            path=(self.cache / name).resolve()
            if not path.is_relative_to(self.cache):raise ValueError('Cache path escapes its directory')
            return path
        if relative == EXE:
            return (self.game / EXE).resolve()  # only for the verified palette operands, never arbitrary code
        p = (self.game / relative).resolve()
        if not p.is_relative_to(self.game / 'assets') or p == self.game / 'assets':
            raise ValueError('Only files inside this game\'s assets directory can be changed.')
        return p

    def source_path(self, source):
        snapshot = self.root / 'originals' / source
        return snapshot if snapshot.is_file() else self.game_file(source)

    def row(self, key):
        if key not in self.catalog.rows: raise ValueError('Unknown asset')
        return self.catalog.rows[key]

    def asset_notes(self, key, text=False):
        with self.lock:
            self.row(key)
            if self._notes is None: self._notes = AssetNotes(self.catalog, self.game)
            return self._notes.text(key) if text else self._notes.get(key)

    def export_asset_notes(self):
        with self.lock:
            if self._notes is None: self._notes = AssetNotes(self.catalog, self.game)
            return self._notes.export_all()

    def active(self):
        entry = read_json(self.ledger)
        return entry if entry and entry.get('state') != 'restored' else None

    @contextmanager
    def install_lock(self):
        # A per-installation OS file lock coordinates different app ports/workspaces.
        path = self.ledger.with_suffix('.lock')
        with path.open('a+b') as f:
            if f.tell() == 0: f.write(b'0'); f.flush()
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as e: raise ValueError('Another editor is changing this installation.') from e
                try: yield
                finally: f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try: yield
                finally: fcntl.flock(f, fcntl.LOCK_UN)

    def status(self):
        with self.lock:
            return dict(app='Asset Lab', version=VERSION, game=str(self.game), cache=str(self.cache) if self.cache else None, workspace=str(self.root), token=self.session,
                        stats=self.catalog.stats, groups=GROUPS, sources=sorted({r['source'] for r in self.catalog.rows.values()}),
                        warnings=self.catalog.warnings, active=self.active(), staged=self.staged(), saved_count=sum(bool(self.current(k)) for k in self.state['edits']),
                        stuck=self.drift(), palette=self.palette(), hidden=self.hidden(), pending=self.pending(),
                        game_colors=self.game_colors(), research=(ROOT / 'data' / 'exe-references.json').is_file())

    # ----- game colors and menu controls set in the executable ----------------------------------
    @staticmethod
    def game_colors():
        """Every editable code color with its original value per palette phase, and the menu controls that can be hidden."""
        return dict(colors={name: [dict(color=f'#{value:06X}', phase=phase) for _, value, phase in sites] for name, sites in exe_palette.SITES.items()},
                    hide={group: label for group, (*_, label) in exe_palette.HIDE.items()}, blocked=exe_palette.BLOCKED)

    def palette(self):
        """Color overrides for the next mod build, as {field: '#RRGGBB'}; empty means the game's own colors."""
        return dict(self.state.get('palette') or {})

    def hidden(self):
        """Main-menu controls the next mod build leaves out (promo links and banners)."""
        return list(self.state.get('hide') or [])

    def set_hidden(self, groups):
        groups = list(dict.fromkeys(g for g in groups or [] if g))
        if groups and exe_palette.BLOCKED: raise ValueError(exe_palette.BLOCKED)
        for group in groups:
            if group not in exe_palette.HIDE: raise ValueError('Unknown menu control: ' + str(group))
        with self.lock:
            if groups: self.state['hide'] = groups
            else: self.state.pop('hide', None)
            self.persist()
            return self.hidden()

    def set_palette(self, overrides):
        overrides = {k: v.upper() for k, v in (overrides or {}).items() if v}
        if overrides and exe_palette.BLOCKED: raise ValueError(exe_palette.BLOCKED)
        for name, value in overrides.items():
            if name not in exe_palette.SITES: raise ValueError('Unknown game color: ' + name)
            exe_palette.parse_color(value)
        with self.lock:
            if overrides: self.state['palette'] = overrides
            else: self.state.pop('palette', None)
            self.persist()
            return self.palette()

    def search(self, query='', group='', kind='', parent='', offset=0, limit=72, saved=False, origin='', tidy=False, removed=False, favorite=False, like=''):
        """like: 'key:<asset key>' or 'upload:<id>' ranks the results by visual similarity to that image."""
        words = query.casefold().split()
        target = self.similar.query(like) if like else None
        with self.lock:
            # Tidy hides leftover atlas rectangles and shows one card per identical copy.
            rows = [r for r in self.catalog.rows.values()
                    if (not group or r['group'] == group) and (not kind or (r['editable'] if kind == 'visual' else r['kind'] == kind))
                    and bool(r.get('removed')) == bool(removed) and (not favorite or r.get('favorite'))
                    and (not tidy or saved or removed or (not r.get('legacy') and (parent or r.get('primary', True))))
                    and (not parent or r.get('parent') == parent)
                    and (not origin or r['source'].startswith('cache/' if origin == 'cache' else 'assets/'))
                    and (not saved or self.current(r['key']))
                    and all(w in (r['name']+' '+r['source']+' '+r.get('source_label','')+' '+r['entry']+' '+r['group']+' '+r.get('label', '')).casefold() for w in words)]
            if target is not None:
                scores = self.similar.rank(target, [r['key'] for r in rows])
                rows = sorted((r for r in rows if r['key'] in scores), key=lambda r: scores[r['key']])[:240]
                return {'total': len(rows), 'items': [{**r, 'similarity': round(1 - scores[r['key']], 3), 'saved': bool(self.current(r['key'])), 'staged': r['key'] in self.state['staged']}
                                                       for r in rows[max(0, offset):max(0, offset)+min(120, limit)]]}
            rows.sort(key=lambda r: (r['group'] == 'Metadata', r['group'] == 'Audio', r['kind'] != 'image', r['name'].casefold(), r['source']))
            return {'total': len(rows), 'items': [{**r, 'saved': bool(self.current(r['key'])), 'staged': r['key'] in self.state['staged']} for r in rows[max(0, offset):max(0, offset)+min(120, limit)]]}

    def history(self, key): return self.state['edits'].get(key, {'versions': [], 'cursor': -1})

    def current(self, key):
        h = self.history(key)
        return h['versions'][h['cursor']] if h['cursor'] >= 0 else None

    def revision(self, key):
        current = self.current(key)
        return current['id'] if current else 'original'

    def assert_revision(self, key, revision):
        if revision != self.revision(key): raise ValueError('This asset changed in another tab. Reload it before saving.')

    def image(self, key, saved=True):
        with self.lock:
            row = self.row(key)
            if not row['editable']: raise ValueError('This item is not an editable image.')
            current = self.current(key) if saved else None
            if current: return decode_png((self.root / 'revisions' / key / (current['id'] + '.png')).read_bytes())
            parent = row.get('parent')
            if saved and parent and ((self.current(parent) or {}).get('recipe') or {}).get('theme'):
                # On a themed sheet, a sprite with no edit of its own shows the themed art it will install.
                x, y, w, h = row['rect']
                crop = self.image(parent).crop((x, y, x+w, y+h))
                return crop.transpose(Image.Transpose.ROTATE_90) if row['rotated'] else crop
            return self.catalog.image(key)

    def thumbnail(self, key, original=False, size=(144, 112)):
        with self.lock:
            cachekey = (key, 'original' if original else self.revision(key), 'original' if original else self.revision(self.row(key).get('parent', key)), size)
            if cachekey not in self._thumbs:
                im = self.catalog.image(key) if original else self.image(key)
                im.thumbnail(size, Image.Resampling.LANCZOS)
                self._thumbs[cachekey] = png(im)
            return self._thumbs[cachekey]

    def detail(self, key):
        with self.lock:
            row = self.row(key)
            h = self.history(key)
            siblings = self.catalog.children.get(row.get('parent', ''), [])
            shared = [self.row(k) for k in siblings if k != key and overlap(row['rect'], self.row(k)['rect'])] if row.get('parent') else []
            prefix = re.sub(r'-(up|down|disabled|focused|selected)(?=-|$)', '', row['name'])
            related = [self.row(k) for k in siblings if re.sub(r'-(up|down|disabled|focused|selected)(?=-|$)', '', self.row(k)['name']) == prefix]
            result = dict(row, revision=self.revision(key), can_undo=h['cursor'] >= 0,
                          can_redo=h['cursor'] + 1 < len(h['versions']), history=h,
                          staged=key in self.state['staged'], shared_regions=[{'key':r['key'],'name':r['name']} for r in shared[:30]],
                          related=[{'key':r['key'],'name':r['name']} for r in related[:24]],
                          children=[self.row(k) for k in self.catalog.children.get(key, [])],
                          copy_list=[dict(key=k, source=self.row(k).get('source_label') or self.row(k)['source'], entry=self.row(k)['entry'], live=self.row(k).get('live', ''))
                                     for k in self.catalog.copies.get(row.get('dup', ''), []) if k != key][:24],
                          source_sha256=self.state['baseline'][row['source']]['sha256'])
            if row['editable']:
                im = self.image(key)
                points = [(im.width//2, max(0, im.height//6)), (im.width//2, im.height//2), (im.width//2, min(im.height-1, im.height*5//6))]
                result['shades'] = ['#' + ''.join(f'{v:02x}' for v in im.getpixel(p)[:3]) for p in points]
                result['alpha'] = im.getchannel('A').getextrema()
            return result

    def snapshot(self, source):
        expected = self.state['baseline'][source]['sha256']
        path = self.source_path(source)
        if file_hash(path) != expected:
            raise ValueError('The source changed (possibly a Steam update). Start a new workspace before editing this version.')
        snapshot = self.root / 'originals' / source
        if not snapshot.is_file():
            data = path.read_bytes()
            if digest(data) != expected: raise ValueError('The source changed while it was being copied.')
            atomic_bytes(snapshot, data)
        return snapshot

    def save(self, key, data, revision, recipe=None):
        with self.lock:
            row = self.row(key)
            self.assert_revision(key, revision)
            if not row['editable']: raise ValueError('Only image assets can be edited.')
            im = decode_png(data)
            if im.size != (row['width'], row['height']):
                raise ValueError(f'Keep the original {row["width"]} × {row["height"]} size. Resize the import to fit in the editor.')
            if len(json.dumps(recipe or {})) > 64000: raise ValueError('Recipe is too large')
            self.snapshot(row['source'])
            current = self.image(key)
            if im.tobytes() == current.tobytes(): return self.detail(key)
            version = uuid.uuid4().hex
            data = png(im)
            atomic_bytes(self.root / 'revisions' / key / (version + '.png'), data)
            h = self.history(key)
            self.state['edits'][key] = {'versions': h['versions'][:h['cursor']+1] + [dict(id=version, at=time.time(), sha256=digest(data), recipe=recipe or {})], 'cursor': h['cursor']+1}
            if key not in self.state['staged']: self.state['staged'].append(key)
            self.persist()
            return self.detail(key)

    def commit(self, key, im, recipe, persist=True):
        """Save a finished RGBA image as the next revision; batch callers skip the inspector detail."""
        with self.lock:
            row = self.row(key)
            if not row['editable'] or im.size != (row['width'], row['height']): raise ValueError('Image does not fit ' + row['name'])
            self.snapshot(row['source'])
            version, data = uuid.uuid4().hex, png(im.convert('RGBA'))
            atomic_bytes(self.root / 'revisions' / key / (version + '.png'), data)
            h = self.history(key)
            self.state['edits'][key] = {'versions': h['versions'][:h['cursor']+1] + [dict(id=version, at=time.time(), sha256=digest(data), recipe=recipe or {})], 'cursor': h['cursor']+1}
            if key not in self.state['staged']: self.state['staged'].append(key)
            if persist: self.persist()
            return version

    def step(self, key, revision, direction, detail=True):
        with self.lock:
            self.assert_revision(key, revision)
            h = self.history(key)
            cursor = h['cursor'] + direction
            if cursor < -1 or cursor >= len(h['versions']): raise ValueError('No further history in that direction.')
            h['cursor'] = cursor
            self.state['edits'][key] = h
            if cursor < 0 and key in self.state['staged']: self.state['staged'].remove(key)
            if not detail: return None  # Batch callers persist once at the end.
            self.persist()
            return self.detail(key)

    def stage(self, key, enabled):
        with self.lock:
            self.row(key)
            if enabled and not self.current(key): raise ValueError('Save an edit first.')
            if enabled and key not in self.state['staged']: self.state['staged'].append(key)
            if not enabled and key in self.state['staged']: self.state['staged'].remove(key)
            self.persist()
            return self.staged()

    def stage_many(self, keys, enabled):
        """Take a whole theme (or any selection) out of the mod, or put it back, in one step."""
        with self.lock:
            for key in keys:
                self.row(key)
                if enabled and self.current(key) and key not in self.state['staged']: self.state['staged'].append(key)
                if not enabled and key in self.state['staged']: self.state['staged'].remove(key)
            self.persist()
            return self.staged()

    def staged(self):
        return [dict(key=k, name=self.row(k)['name'], source=self.row(k)['source'], revision=self.revision(k)) for k in self.state['staged'] if self.current(k)]

    def _compose(self, keys):
        groups = {}
        for k in keys:
            r = self.row(k)
            if not r['editable'] or not self.current(k): raise ValueError('Only saved visual edits can be installed.')
            groups.setdefault(r.get('parent', k), []).append(k)
        def place(image, row, patch):
            if row['rotated']: patch = patch.transpose(Image.Transpose.ROTATE_270)
            x, y, w, h = row['rect']
            if patch.size != (w, h): raise ValueError('Sprite geometry mismatch')
            image.paste(patch, (x, y))  # Replace RGBA exactly; never alpha-blend onto the old skin.
        sheets = {}
        for sheet_key, edits in groups.items():
            sheet = self.row(sheet_key)
            sprites = [k for k in edits if k != sheet_key]
            # A whole-sheet theme is a base layer; hand edits of single sprites sit on top of it.
            themed = sheet_key in edits and bool((self.current(sheet_key).get('recipe') or {}).get('theme'))
            if sheet_key in edits and sprites and not themed:
                raise ValueError('Choose the full sheet OR its individual sprites in the mod list: ' + sheet['name'])
            for i, first in enumerate(sprites):
                for second in sprites[i+1:]:
                    if overlap(self.row(first)['rect'], self.row(second)['rect']):
                        raise ValueError('These edits share atlas pixels. Keep just one: ' + self.row(first)['name'] + ' / ' + self.row(second)['name'])
            sheets[sheet_key] = dict(image=self.image(sheet_key) if sheet_key in edits else self.catalog.image(sheet_key), whole=sheet_key in edits, sprites=sprites)
        # The same art ships in several files and only one is drawn, so copies follow the edit.
        mirrored, self.mirror_skipped = {}, []
        def target(sheet_key):
            if sheet_key in sheets or sheet_key in mirrored: return (sheets.get(sheet_key) or mirrored[sheet_key])
            try: self.snapshot(self.row(sheet_key)['source'])
            except ValueError:
                self.mirror_skipped.append(self.row(sheet_key)['source'])
                return None
            mirrored[sheet_key] = dict(image=self.catalog.image(sheet_key), whole=False, sprites=[])
            return mirrored[sheet_key]
        if getattr(self, 'mirror_copies', True):
            for sheet_key, entry in list(sheets.items()):
                if not entry['whole']: continue
                for copy in self.catalog.copies.get(self.row(sheet_key).get('dup', ''), []):
                    if copy in sheets or self.row(copy)['kind'] != 'image': continue
                    slot = target(copy)
                    if slot: slot.update(image=entry['image'].copy(), whole=True)
            for sheet_key, entry in list(sheets.items()):
                for k in entry['sprites']:
                    for copy in self.catalog.copies.get(self.row(k).get('dup', ''), []):
                        row = self.row(copy)
                        if copy == k or copy in keys or row.get('legacy') or not row.get('parent'): continue
                        if row['parent'] == sheet_key and row['rect'] == self.row(k)['rect']: continue
                        slot = target(row['parent'])
                        if slot: place(slot['image'], row, self.image(k))
        for entry in sheets.values():
            for k in entry['sprites']: place(entry['image'], self.row(k), self.image(k))
        output = {}
        for sheet_key, entry in {**mirrored, **sheets}.items():
            sheet = self.row(sheet_key)
            output.setdefault(sheet['source'], {})[sheet['entry']] = png(entry['image'])
        return output

    def game_state(self):
        try: return dict(running=bool(self.process_check()))
        except ValueError: return dict(running=None)

    def close_game(self, wait=12.0):
        """Close every Mitosis window the way its X button would, then force only a window that ignored that."""
        if not self.process_check(): return dict(closed=True, forced=False)
        if os.name != 'nt': raise ValueError('Close Mitosis yourself, then try again.')
        quiet = dict(capture_output=True, creationflags=0x08000000, timeout=15)
        subprocess.run(['taskkill.exe', '/IM', 'Mitosis.exe'], **quiet)
        deadline = time.time() + wait
        while time.time() < deadline:
            if not self.process_check(): return dict(closed=True, forced=False)
            time.sleep(0.5)
        subprocess.run(['taskkill.exe', '/F', '/IM', 'Mitosis.exe'], **quiet)
        for _ in range(10):
            if not self.process_check(): return dict(closed=True, forced=True)
            time.sleep(0.5)
        return dict(closed=False, forced=True)

    def ensure_closed(self):
        if self.process_check(): raise ValueError('Close every Mitosis window before installing or restoring a mod.')
        # The Steam client may stay open; block actual downloads/updates for this app.
        manifest = self.game.parent.parent / 'appmanifest_389570.acf'
        if manifest.is_file():
            text = manifest.read_text(encoding='utf-8')
            flags = re.search(r'"StateFlags"\s+"(\d+)"', text)
            if flags and int(flags.group(1)) != 4:
                raise ValueError('Steam reports an incomplete or updating installation. Let it finish first.')

    def accepted(self, path):
        """Hashes a game file may have right now: its original, or what the installed mod (or an update to it) wrote."""
        hashes = {self.state['baseline'][path]['sha256']} if path in self.state['baseline'] else set()
        for f in (self.active() or {}).get('files', []):
            if f['path'] == path: hashes |= {f['original_sha256'], f['edited_sha256'], f.get('previous_sha256') or f['original_sha256']}
        return hashes

    def pending(self):
        """True when the mod as it is now differs from what is installed, so an update would change the game."""
        journal = self.active()
        if not journal: return False
        now = (sorted((r['key'], r['revision']) for r in self.staged()), self.palette(), self.hidden())
        then = (sorted((r['key'], r['revision']) for r in journal.get('edits', [])), journal.get('palette') or {}, journal.get('hide') or [])
        return now != then

    def mod(self):
        """Everything the next install would put in the game, item by item, for the review."""
        with self.lock:
            journal = self.active() if (self.active() or {}).get('state') == 'installed' else None
            then = {r['key']: r['revision'] for r in (journal or {}).get('edits', [])}
            edits = []
            for r in self.staged():
                row, recipe = self.row(r['key']), (self.current(r['key']).get('recipe') or {})
                edits.append(dict(key=r['key'], name=row.get('label') or row['name'], source=row['source'], sheet=bool(self.catalog.children.get(r['key'])),
                                  size=[row['width'], row['height']], theme=recipe.get('prompt', '') if recipe.get('theme') else '', theme_id=recipe.get('theme', ''),
                                  redraw=recipe.get('prompt', '') if recipe.get('design') else '', redraw_id=recipe.get('design', ''),
                                  installed=then.get(r['key']) == r['revision']))
            by_name = {}
            for row in self.catalog.rows.values():
                if row.get('editable') and row.get('primary', True) and not row.get('legacy'): by_name.setdefault(row['name'], row['key'])
            sprites = lambda group: [by_name[n] for n in exe_palette.HIDE_SPRITES.get(group, []) if n in by_name]
            return dict(edits=edits, palette=self.palette(), hide=self.hidden(), hide_sprites={g: sprites(g) for g in exe_palette.HIDE},
                        installed=journal and dict(edits=list(then), palette=journal.get('palette') or {}, hide=journal.get('hide') or []),
                        pending=self.pending())

    def build(self):
        with self.lock, self.install_lock():
            journal = self.active()
            if journal and journal['state'] != 'installed': raise ValueError('An install was interrupted. Restore it before building another.')
            keys = [r['key'] for r in self.staged()]
            colors, hide = self.palette(), self.hidden()
            if not keys and not colors and not hide and not journal: raise ValueError('Save and include at least one edit in the mod.')
            for k in keys: self.snapshot(self.row(k)['source'])
            replacements = self._compose(keys) if keys else {}
            build_id = uuid.uuid4().hex
            folder = self.root / 'builds' / build_id
            files = []
            for source, values in replacements.items():
                original = self.snapshot(source).read_bytes()
                baseline = self.state['baseline'][source]['sha256']
                if file_hash(self.game_file(source)) not in self.accepted(source):
                    raise ValueError('The installed source differs from the saved original: ' + source)
                is_zip = self.catalog.containers[source] == 'zip'
                rebuilt = replace_zip(original, values) if is_zip else values['']
                if not is_zip: decode_png(rebuilt)
                atomic_bytes(folder / 'files' / source, rebuilt)
                files.append(dict(path=source, original_sha256=baseline, edited_sha256=digest(rebuilt), original_bytes=len(original),
                                  edited_bytes=len(rebuilt), entries=list(values), backup=str(self.snapshot(source))))
            if colors or hide:
                # The executable joins the mod only for these verified color operands and menu branches; it is backed
                # up and restored exactly like an image file. The patch refuses any build other than the analyzed one.
                if EXE not in self.state['baseline']:
                    live = self.game_file(EXE)
                    self.state['baseline'][EXE] = {'sha256': file_hash(live), 'size': live.stat().st_size}
                    self.persist()
                original = self.snapshot(EXE).read_bytes()
                rebuilt, changes = exe_palette.patch(original, colors, hide)
                if len(rebuilt) != len(original): raise ValueError('Color patch changed the executable size')
                atomic_bytes(folder / 'files' / EXE, rebuilt)
                files.append(dict(path=EXE, original_sha256=self.state['baseline'][EXE]['sha256'], edited_sha256=digest(rebuilt), original_bytes=len(original),
                                  edited_bytes=len(rebuilt), entries=[f"Hide: {c['phase']}" if c['field'] == 'HIDE' else f"{c['field']} {c['color']} ({c['phase']})" for c in changes],
                                  backup=str(self.snapshot(EXE))))
            manifest = dict(id=build_id, game=str(self.game), at=time.time(), files=files, edits=self.staged(), palette=colors, hide=hide, state='ready')
            atomic_json(folder / 'build.json', manifest)
            return manifest

    def _build(self, build_id):
        if not re.fullmatch(r'[a-f0-9]{32}', build_id): raise ValueError('Invalid build ID')
        manifest = read_json(self.root / 'builds' / build_id / 'build.json')
        if not manifest or manifest['game'] != str(self.game): raise ValueError('Build belongs to another game')
        return manifest

    def install(self, build_id):
        """Install a build. With a mod already installed this is an update: each file goes straight from the
        old mod to the new one, and files the new mod no longer changes go back to their originals."""
        with self.lock, self.install_lock():
            self.ensure_closed()
            old = self.active()
            if old and old['state'] != 'installed': raise ValueError('An install was interrupted. Restore it before installing another.')
            if old and self.drift(): raise ValueError('Something outside the lab replaced an installed file. Clear the stale install first.')
            build = self._build(build_id)
            before = {f['path']: f for f in (old or {}).get('files', [])}
            for f in build['files']:
                live = file_hash(self.game_file(f['path']))
                if live != f['original_sha256'] and live != before.get(f['path'], {}).get('edited_sha256'): raise ValueError('Game file changed since build: ' + f['path'])
                if file_hash(Path(f['backup'])) != f['original_sha256']: raise ValueError('Original backup failed verification')
                if file_hash(self.root / 'builds' / build_id / 'files' / f['path']) != f['edited_sha256']: raise ValueError('Build failed verification')
            paths = {f['path'] for f in build['files']}
            dropped = [f for path, f in before.items() if path not in paths]
            for f in dropped:
                if file_hash(Path(f['backup'])) != f['original_sha256']: raise ValueError('Original backup failed verification')
            # Every file this update can touch is in the ledger, with each state it may be in, before the first write;
            # Restore can therefore always put the originals back, even if the update is interrupted.
            files = [dict(f, previous_sha256=before[f['path']]['edited_sha256']) if f['path'] in before else f for f in build['files']]
            journal = dict(build, files=files + dropped, state='installing', workspace=str(self.root), installed=[], updated_from=old and old['id'])
            atomic_json(self.ledger, journal)  # Persist recovery information before the first game write.
            try:
                for f in build['files']:
                    self.ensure_closed()
                    if file_hash(self.game_file(f['path'])) not in (f['original_sha256'], before.get(f['path'], {}).get('edited_sha256')): raise ValueError('Game changed during install')
                    atomic_bytes(self.game_file(f['path']), (self.root / 'builds' / build_id / 'files' / f['path']).read_bytes())
                    if file_hash(self.game_file(f['path'])) != f['edited_sha256']: raise ValueError('Installed file failed verification')
                    journal['installed'].append(f['path'])
                    atomic_json(self.ledger, journal)
                for f in dropped:
                    self.ensure_closed()
                    atomic_bytes(self.game_file(f['path']), Path(f['backup']).read_bytes())
                    if file_hash(self.game_file(f['path'])) != f['original_sha256']: raise ValueError('Restore verification failed')
                journal.update(files=build['files'], state='installed' if build['files'] else 'restored', restored_files=[f['path'] for f in dropped])
                atomic_json(self.ledger, journal)
                return journal
            except Exception:
                # Leave a recoverable ledger, even if an external updater interfered.
                journal['state'] = 'needs_restore'
                atomic_json(self.ledger, journal)
                raise

    def restore(self):
        with self.lock, self.install_lock():
            self.ensure_closed()
            journal = self.active()
            if not journal: return {'state': 'restored', 'files': []}
            ours = lambda f: (f['original_sha256'], f['edited_sha256'], f.get('previous_sha256') or f['original_sha256'])
            for f in journal['files']:
                live = file_hash(self.game_file(f['path']))
                if live not in ours(f):
                    raise ValueError('An external change was detected; restore will not overwrite it: ' + f['path'])
                if file_hash(f['backup']) != f['original_sha256']: raise ValueError('Original backup failed verification')
            for f in journal['files']:
                self.ensure_closed()
                if file_hash(self.game_file(f['path'])) not in ours(f): raise ValueError('Game changed during restore')
                atomic_bytes(self.game_file(f['path']), Path(f['backup']).read_bytes())
                if file_hash(self.game_file(f['path'])) != f['original_sha256']: raise ValueError('Restore verification failed')
            journal['state'] = 'restored'
            journal['restored_at'] = time.time()
            atomic_json(self.ledger, journal)
            return journal

    def drift(self):
        """Files of the active install that something else (Steam, the game's cache) has since rewritten."""
        journal = self.active()
        if not journal: return []
        changed = []
        for f in journal['files']:
            path = self.game_file(f['path'])
            if not path.is_file() or file_hash(path) not in (f['original_sha256'], f['edited_sha256'], f.get('previous_sha256') or f['original_sha256']): changed.append(f['path'])
        return changed

    def abandon(self):
        """Close an install record that can no longer be restored, without writing any game file.

        Files still holding the mod are put back first. Only files that were replaced from
        outside are left as they are; their original backups stay in the workspace.
        """
        with self.lock, self.install_lock():
            self.ensure_closed()
            journal = self.active()
            if not journal: return {'state': 'restored', 'files': []}
            changed = self.drift()
            if not changed: raise ValueError('Nothing blocks a normal restore. Use Restore original.')
            for f in journal['files']:
                if f['path'] in changed: continue
                if file_hash(f['backup']) != f['original_sha256']: raise ValueError('Original backup failed verification')
                atomic_bytes(self.game_file(f['path']), Path(f['backup']).read_bytes())
                if file_hash(self.game_file(f['path'])) != f['original_sha256']: raise ValueError('Restore verification failed')
            journal.update(state='restored', restored_at=time.time(), abandoned=changed)
            atomic_json(self.ledger, journal)
            # The rewritten files are the new starting point for this workspace.
            for path in changed:
                live = self.game_file(path)
                if live.is_file(): self.state['baseline'][path] = {'sha256': file_hash(live), 'size': live.stat().st_size}
                stale = self.root / 'originals' / path
                if stale.is_file(): stale.replace(stale.with_name(stale.name + '.' + journal['id'][:8] + '.bak'))
            self.persist()
            return journal

    def export_recipe(self, key):
        row = self.row(key)
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('texture.png', png(self.image(key)))
            z.writestr('original.png', png(self.image(key, False)))
            z.writestr('recipe.json', json.dumps(dict(asset=row, revision=self.current(key), source_sha256=self.state['baseline'][row['source']]['sha256']), indent=2))
        return out.getvalue()
