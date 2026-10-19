"""Mitos.is Asset Lab: versioned pixel edits and reversible visual-only game installs."""
from collections import Counter
from contextlib import contextmanager
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
import fonts as fontlab
import audio as soundlab   # game sounds and music (see the "sounds" section of Lab)
import settings as labsettings   # the player's settings (settings.json beside the workspace)

ROOT = Path(__file__).resolve().parent
# The release number (the launcher passes it; a source checkout reads the VERSION file beside src).
# Art the game takes back: Mitosis downloads a fresh copy of the file it lives in before a match whenever that file
# was changed, so an edit never shows in game (seen with the red virus on 2026-10-16). Shown as a tag in the library.
LOCKED = {'spawner.png': 'The red virus comes from the game’s server: Mitosis downloads a fresh copy before a match, so edits to it never show in game.'}
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


def atomic_json(path, data, keep=False):
    """keep: the last good version stays beside it (<name>.bak), for read_json to fall back on if this one is damaged."""
    path = Path(path)
    if keep and path.is_file():
        try:
            old = path.read_bytes(); json.loads(old)
            atomic_bytes(path.with_name(path.name + '.bak'), old)
        except (OSError, ValueError): pass   # a damaged one is not kept as the good one
    atomic_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8'))


def read_json(path, fallback=None, notes=None, what=''):
    """notes: a list to recover into instead of failing: a damaged file (antivirus, OneDrive, a failing disk) gives way
    to its last good version (<name>.bak), else it is set aside and the fallback is used, with a note saying so."""
    path = Path(path)
    if not path.is_file(): return fallback
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError):
        if notes is None: raise
    aside = path.with_name(f'{path.name}.damaged-{int(time.time())}')
    try: path.replace(aside)
    except OSError: pass
    backup = path.with_name(path.name + '.bak')
    try:
        data = json.loads(backup.read_text(encoding='utf-8'))
        atomic_bytes(path, backup.read_bytes())
        notes.append(f'{what or path.name} was damaged; its last good copy was used (the damaged file is kept as {aside.name}).')
        return data
    except (OSError, ValueError, UnicodeDecodeError):
        notes.append(f'{what or path.name} was damaged and had no good copy; it was set aside as {aside.name}.')
        return fallback


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


def windows_processes():
    """Read Windows' process snapshot directly; tasklist can stall for seconds on a busy PC."""
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('name', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for fn in (kernel.Process32FirstW, kernel.Process32NextW):
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        fn.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if handle == ctypes.c_void_p(-1).value:
        raise ValueError('Could not check running games. Close Mitosis and try again.')
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
        rows = []
        found = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while found:
            rows.append((entry.name, entry.pid))
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES; other errors must not mean "game closed".
            raise ValueError('Could not finish checking running games. Close Mitosis and try again.')
        return rows
    finally: kernel.CloseHandle(handle)


def game_processes(game=None):
    if os.name != 'nt': return []
    matches = [row for row in windows_processes() if row[0].casefold() == 'mitosis.exe']
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


def locked(row):
    """{'locked': why} for art an edit cannot change in game (LOCKED), else {}."""
    return {'locked': LOCKED[row['name']]} if row.get('name') in LOCKED else {}


class Lab:
    def __init__(self, game=None, workspace=None, ledger_dir=None, process_check=None, cache=None, settings_file=None):
        self.game = Path(game).resolve() if game else find_game()
        if not (self.game / 'Mitosis.exe').is_file() or not (self.game / 'assets').is_dir():
            raise ValueError('Select the Mitos.is installation containing Mitosis.exe and assets.')
        self.game_id = digest(str(self.game).casefold().encode())[:24]
        self.ledger_dir = Path(ledger_dir or data_home() / 'install-ledgers').resolve()
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = self.ledger_dir / (self.game_id + '.json')
        self.root = Path(workspace or data_home() / 'workspace').resolve()
        # A mod in the game installed from another workspace on this PC: that workspace opens instead (only it knows the
        # game's real originals; a new one here would take the modded files for them). When it is gone, this one opens
        # with Restore original only, so the game can still be put back. Never a lab that cannot start.
        notes, self.foreign = [], None
        other = (self.active() or {}).get('workspace')
        if other and Path(other).resolve() != self.root:   # the same folder written differently is the same folder
            if (Path(other) / 'state.json').is_file():
                notes.append(f'Opened the workspace your installed mod came from ({other}), so Restore original and updates work.')
                self.root = Path(other).resolve()
            else:
                self.foreign = other
                notes.append('The mod in the game was installed from a workspace that is no longer on this PC. '
                             'Use Restore original before installing anything new.')
        if self.root.is_relative_to(self.game) or self.game.is_relative_to(self.root):
            raise ValueError('Keep the editing workspace separate from the game installation.')
        self.root.mkdir(parents=True, exist_ok=True)
        # The server passes <data home>/settings.json (the file the launcher reads); a lab made elsewhere keeps its own.
        self.settings = labsettings.Settings(settings_file or self.root.parent / 'settings.json')
        self.lock = threading.RLock()
        self._typefaces_lock = threading.Lock()   # one reading of this PC's fonts at a time (typefaces)
        fontlab.TEXTURES = self.font_texture   # where a font's texture fill is (Theme AI draws them)
        self.cache = Path(cache).resolve() if cache else None
        if cache is None:
            try:
                if self.game == find_game():
                    candidate=Path(os.environ.get('APPDATA', str(Path.home()/'AppData/Roaming'))) / 'Freakinware/Mitosis'
                    if candidate.is_dir(): self.cache=candidate.resolve()
            except (ValueError, OSError): pass
        self.process_check = process_check or (lambda: game_processes(None if self.cache else self.game))
        self.session = secrets.token_urlsafe(32)
        self.state_path = self.root / 'state.json'
        self.state = read_json(self.state_path, {'game': str(self.game), 'baseline': {}, 'edits': {}, 'staged': []}, notes,
                               'Your workspace record (state.json)')
        if Path(self.state['game']).resolve() != self.game:
            raise ValueError('This workspace belongs to another game installation.')
        self.catalog = Catalog(self.game, self.source_path, self.cache)
        self.catalog.warnings.extend(notes + self.__dict__.pop('early_notes', []))
        # The installed mod's files are not the game's originals: their baseline is the original the mod replaced.
        modded = {f['path']: f for f in (self.active() or {}).get('files', [])} if self.foreign else {}
        try: real = self.game == find_game()   # the Steam install, whose item sheet the game's server keeps restoring
        except (ValueError, OSError): real = False
        for row in self.catalog.rows.values():
            # The item sheet (potions, power-ups, equipment) comes from the game's server, which puts a fresh copy back
            # at start-up whenever it was changed: edits there never show, so it stays out of the library, themes and mods.
            row['server_only'] = real and (row.get('entry', '').rsplit('/', 1)[-1] == 'items.png' or row['name'] == 'items.png')
            source = row['source']
            if source not in self.state['baseline']:
                f = self.game_file(source)
                original = modded.get(source)
                self.state['baseline'][source] = ({'sha256': original['original_sha256'], 'size': original.get('original_bytes') or f.stat().st_size}
                                                  if original else {'sha256': file_hash(f), 'size': f.stat().st_size})
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

    def persist(self):
        if getattr(self, '_build_recovery_required', False):
            raise ValueError('Build recovery could not finish. Restart Asset Lab after checking free disk space and file access.')
        atomic_json(self.state_path, self.state, keep=True)

    # ----- fonts: the game's text re-rendered in any installed typeface ---------------------------------------------
    def typefaces(self):
        """This PC's typefaces, read once (fonts.system_fonts, kept in typefaces.json). One reading at a time: whoever asks
        while it runs (the Fonts window, Theme AI, the warm-up at start) waits for that reading instead of starting another."""
        faces = getattr(self, '_typefaces', None)
        if faces is not None: return faces
        with self._typefaces_lock:
            faces = getattr(self, '_typefaces', None)
            if faces is None: faces = self._typefaces = fontlab.system_fonts(self.root.parent / 'typefaces.json')
            return faces

    def warm_typefaces(self):
        """Reads the typefaces ahead (the server runs this in the background at start), so the Fonts window opens at once.
        A failure here is only a missed head start: the window asks again and reports it."""
        try: self.typefaces()
        except Exception: pass

    def font_source(self, fid):
        """(font info, original .fnt text, original page image) of one of the game's fonts, read once."""
        game = fontlab.game_fonts(self.game)
        if fid not in game: raise ValueError('Unknown game font.')
        f = game[fid]
        known = self.__dict__.setdefault('_font_sources', {}).get(fid)
        if known and known[0] == f: return known
        for path in (f['fnt'], f['page']):
            if path not in self.state['baseline']:
                live = self.game_file(path)   # first use: the game's own file, before any font of ours was installed
                self.state['baseline'][path] = {'sha256': file_hash(live), 'size': live.stat().st_size}
                self.persist()
        text = self.snapshot(f['fnt']).read_text(encoding='utf-8', errors='replace')
        with Image.open(self.snapshot(f['page'])) as page: page = page.convert('RGBA')
        self._font_sources[fid] = (f, text, page)
        return f, text, page

    def font_catalog(self):
        faces = self.typefaces()
        # Only a quick read of the saved styles: an install, a build or a drawing being applied holds the lab's lock for a
        # while, and the Fonts window must not wait for them (it sat on "Loading fonts…").
        locked = self.lock.acquire(timeout=1.5)
        try: saved = json.loads(json.dumps(self.state.get('fonts') or {}))
        finally:
            if locked: self.lock.release()
        game = [dict(f, style=fontlab.clean_style(saved[fid]['style'], faces) if (saved.get(fid) or {}).get('style') else None,
                     staged=bool((saved.get(fid) or {}).get('staged'))) for fid, f in fontlab.game_fonts(self.game).items()]
        # Every look with its font list resolved to a typeface installed on this PC, so the menu and the previews agree.
        presets = [dict(group=group, items=[dict(name=name, style=fontlab.clean_style(p, faces)) for name, p in items.items()])
                   for group, items in fontlab.PRESET_GROUPS.items()]
        return dict(game=game, typefaces=faces, presets=presets, defaults=fontlab.DEFAULT, limits=fontlab.LIMITS)

    def font_preview(self, fid, style, text):
        """PNG of example text in a style. Recent ones are kept, so going back to a look, or a player clicking through the
        looks, costs nothing."""
        with self.lock: f, fnt, page = self.font_source(fid)
        faces = self.typefaces()
        style = fontlab.clean_style(style, faces)
        key = (fid, fontlab.style_key(style), text)
        with fontlab.LOCK:
            kept = self.__dict__.setdefault('_font_previews', {})
            if key in kept:
                data = kept[key] = kept.pop(key)
                return data
        out = io.BytesIO()
        fontlab.preview(fnt, page, style, faces, text).save(out, format='PNG', compress_level=1)   # local and short-lived: speed over size
        data = out.getvalue()
        with fontlab.LOCK:
            kept[key] = data
            while len(kept) > 300: kept.pop(next(iter(kept)))
        return data

    def font_texture(self, name):
        """The picture of a font texture: kept in the workspace once a font uses it, else the drawing it came from."""
        if not fontlab.TEXTURE.fullmatch(str(name or '')): return None
        kept = self.root / 'font-textures' / f'{name}.png'
        if kept.is_file(): return kept
        job, index = name.split('-')
        drawn = self.root / 'designs' / job / f'variant-{int(index)}.png'
        if drawn.is_file(): return drawn
        made = ROOT / 'presets' / 'font-textures' / f'{name}.webp'   # a Fonts preset's: drawn by Image AI once, ships with the app
        return made if made.is_file() else None

    def font_apply(self, fid, style):
        """Render the whole font in a style, keep it in the workspace and put it in the mod."""
        with self.lock:
            f, fnt, page = self.font_source(fid)
            faces = self.typefaces()
            style = fontlab.clean_style(style, faces)
            if style['texture']:
                # The drawing it came from can be cleared away with old designs: the font keeps its own copy.
                source = self.font_texture(style['texture'])
                if source is None: raise ValueError('The texture for these letters is gone. Pick another one or draw it again.')
                kept = self.root / 'font-textures' / f'{style["texture"]}.png'
                if source != kept: atomic_bytes(kept, source.read_bytes())
            sheet, text, info = fontlab.build(fnt, page, style, faces)
            version = uuid.uuid4().hex
            atomic_bytes(self.root / 'fonts' / version / 'page.png', png(sheet))
            atomic_bytes(self.root / 'fonts' / version / 'font.fnt', text.encode('utf-8'))
            self.state.setdefault('fonts', {})[fid] = dict(style=style, version=version, at=time.time(), staged=True, info=info)
            self.persist()
            return dict(id=fid, label=f['label'], **info)

    def font_remove(self, fid):
        """Back to the game's own font (at the next install)."""
        with self.lock:
            (self.state.get('fonts') or {}).pop(fid, None)
            self.persist()
            return dict(id=fid, removed=True)

    def staged_fonts(self):
        game = fontlab.game_fonts(self.game)
        return [dict(game[fid], version=e['version'], style=e['style'], info=e.get('info') or {})
                for fid, e in sorted((self.state.get('fonts') or {}).items()) if e.get('staged') and fid in game]

    # ----- sounds: the game's music and effects, replaced from a file on this PC or from a link --------------------
    # Kept like a font: workspace/audio/<version>.ogg and state['audio'][key] = {version, at, label, settings, staged,
    # info, source, sha256}. A chosen sound (a "source") waits in workspace/audio-sources as FLAC plus its JSON.
    audio_tools = soundlab.TOOLS   # where the link helper lives (tests point it elsewhere)

    def _sound(self):
        """The sound editor's caches (decoded sources and their K-weighted power), its background jobs, and 'render':
        at most two renders at a time (a 20-minute song takes about a gigabyte while it is rendered)."""
        with self.lock:
            if '_sounds' not in self.__dict__: self._sounds = dict(lock=threading.RLock(), samples={}, power={}, originals=None, jobs=soundlab.Jobs(), render=threading.BoundedSemaphore(2))
            return self._sounds

    def audio_row(self, key):
        row = self.row(key)
        if row['kind'] != 'audio': raise ValueError('This item is not a sound.')
        return row

    def audio_original(self, key):
        """The game's own sound: length, rate, channels, loudness and waveform, decoded once from the saved original of
        its file (inside assets.zip, or the loose file) and remembered in audio-originals.cache (JSON; not a .json name,
        so the launcher's settings backup leaves this rebuildable cache out)."""
        row, cache = self.audio_row(key), self._sound()
        sha, path = self.state['baseline'][row['source']]['sha256'], self.root / 'audio-originals.cache'
        with cache['lock']:
            if cache['originals'] is None: cache['originals'] = read_json(path, {}) or {}
            hit = cache['originals'].get(key)
            if hit and hit.get('sha256') == sha and hit.get('v') == 1: return hit
        with self.lock: original = self.snapshot(row['source'])
        if row['entry']:
            with zipfile.ZipFile(original) as z: data = z.read(row['entry'])
        else: data = original.read_bytes()
        samples, rate = soundlab.decode(data)
        info = dict(soundlab.describe(samples, rate), **soundlab.measure(samples, rate), bytes=len(data), sha256=sha, v=1, peaks=soundlab.peaks(samples, 800))
        with cache['lock']:
            cache['originals'][key] = info
            atomic_json(path, cache['originals'])
        return info

    def audio_info(self, key):
        """What the sound editor shows: the game's sound, the saved replacement (if any) and the link helper's state."""
        row = self.audio_row(key)
        original = self.audio_original(key)
        with self.lock:
            edit = dict((self.state.get('audio') or {}).get(key) or {}) or None
            journal = self.active()
        if edit: edit['installed'] = (journal or {}).get('state') == 'installed' and any(a['key'] == key and a['version'] == edit['version'] for a in journal.get('audio') or [])
        return dict(key=key, name=row['name'], label=row.get('label', ''), file=row['source'] + (' → ' + row['entry'] if row['entry'] else ''), original=original, edit=edit,
                    chosen=self.audio_source(edit['source']) if edit and edit.get('source') else None, helper=self.audio_tools_status(),
                    limits=dict(seconds=min(soundlab.MAX_SECONDS, self.settings.get('sound_max_minutes') * 60), bytes=soundlab.MAX_BYTES))

    def audio_source(self, sid):
        """A chosen sound's details (name, length, rate, waveform), or None once it has been cleaned up."""
        if not re.fullmatch(r'[a-f0-9]{32}', str(sid or '')): return None
        meta = read_json(self.root / 'audio-sources' / (sid + '.json'))
        return meta if meta and (self.root / 'audio-sources' / (sid + '.flac')).is_file() else None

    def audio_source_add(self, name, data):
        """A sound file from this PC: decoded (by PyAV for formats libsndfile lacks), resampled if needed, and kept."""
        if not data: raise ValueError('The file is empty.')
        if len(data) > soundlab.MAX_BYTES: raise ValueError(f'Use a sound file of at most {soundlab.mb(soundlab.MAX_BYTES)}.')
        info = {}
        samples, rate = soundlab.decode(data, self.audio_tools, info=info)
        return self._audio_keep(samples, rate, dict(info, name=soundlab.clean_name(name) or 'Your sound', via='file'))

    def _audio_keep(self, samples, rate, meta):
        samples, rate = soundlab.normalize(samples, rate)
        limit = int(self.settings.get('sound_max_minutes') * 60 * rate)   # Settings > Sound > Longest sound
        if len(samples) > limit: meta, samples = dict(meta, cut=meta.get('cut') or round(len(samples) / rate, 1)), samples[:limit].copy()
        sid, folder = uuid.uuid4().hex, self.root / 'audio-sources'
        meta = dict(meta, id=sid, at=time.time(), **soundlab.describe(samples, rate), peak_db=soundlab.peak_db(samples), peaks=soundlab.peaks(samples, 1200))
        atomic_json(folder / (sid + '.json'), meta)   # before the sound itself: a cleanup never sees a sound without its details
        soundlab.write_flac(folder / (sid + '.flac'), samples, rate)
        cache = self._sound()
        with cache['lock']: cache['samples'] = {**dict(list(cache['samples'].items())[-1:]), sid: (samples, rate)}
        self.audio_cleanup()
        return meta

    def _audio_samples(self, sid):
        cache = self._sound()
        with cache['lock']:
            if sid in cache['samples']: return cache['samples'][sid]
        if not self.audio_source(sid): raise ValueError('This sound is no longer kept. Choose it again.')
        samples, rate = soundlab.decode(self.root / 'audio-sources' / (sid + '.flac'))
        with cache['lock']: cache['samples'] = {**dict(list(cache['samples'].items())[-1:]), sid: (samples, rate)}
        return samples, rate

    def _audio_power(self, sid, samples, rate, channels):
        cache = self._sound()
        with cache['lock']:
            if (sid, channels) in cache['power']: return cache['power'][(sid, channels)]
        power = soundlab.kweighted(soundlab.to_channels(samples, channels), rate)
        with cache['lock']: cache['power'] = {**dict(list(cache['power'].items())[-1:]), (sid, channels): power}
        return power

    def sound_settings(self, settings):
        """An edit's settings, with the player's defaults (Settings > Sound) for what it leaves out. The quality always
        comes from Settings: the sound editor has no control for it."""
        s = dict(settings) if isinstance(settings, dict) else {}
        for key, name in (('match', 'sound_match'), ('fade_in', 'sound_fade_in'), ('fade_out', 'sound_fade_out')):
            if s.get(key) is None or s.get(key) == '': s[key] = self.settings.get(name)
        s['quality'] = labsettings.QUALITY[self.settings.get('sound_quality')]
        return s

    def audio_render(self, key, sid, settings):
        """The replacement as the game will play it: (float32 samples, rate, report with warnings). It takes the game
        sound's channel count, and its loudness when settings['match'] is on."""
        original = self.audio_original(key)
        samples, rate = self._audio_samples(sid)
        clean = soundlab.clean_settings(self.sound_settings(settings), len(samples) / rate)
        channels = original['channels'] if original['channels'] in (1, 2) else None
        power = self._audio_power(sid, samples, rate, channels) if clean['match'] and original.get('loudness') is not None else None
        out, report = soundlab.process(samples, rate, clean, dict(loudness=original.get('loudness'), block=original.get('block')), power, channels)
        report['warnings'] = soundlab.warnings(original, report, self.audio_source(sid))
        return out, rate, report

    def audio_preview(self, key, sid, settings, info_only=False):
        """(WAV bytes or None, report): the editor's preview, rendered exactly as it will be saved."""
        with self._sound()['render']:
            out, rate, report = self.audio_render(key, sid, settings)
            return (None if info_only else soundlab.wav(out, rate)), report

    def audio_apply(self, key, sid, settings):
        """Convert the replacement to OGG Vorbis, keep it in the workspace and put it in the mod."""
        row = self.audio_row(key)
        with self._sound()['render']:
            out, rate, report = self.audio_render(key, sid, settings)
            data = soundlab.encode_ogg(out, rate, report['settings']['quality'])
            del out
        got_rate, got_channels, frames = soundlab.probe(data)
        chosen = self.audio_source(sid) or {}
        with self.lock:
            self.snapshot(row['source'])   # the original file is kept safe before anything can be installed
            version = uuid.uuid4().hex
            atomic_bytes(self.root / 'audio' / (version + '.ogg'), data)
            info = dict(duration=report['duration'], rate=got_rate, channels=got_channels, frames=frames, bytes=len(data), gain_db=report['gain_db'], match_db=report['match_db'],
                        limited_db=report['limited_db'], short_db=report['short_db'], loudness=report['loudness'], warnings=report['warnings'])
            self.state.setdefault('audio', {})[key] = dict(version=version, at=time.time(), label=str(chosen.get('title') or chosen.get('name') or 'Your sound')[:120],
                                                          settings=report['settings'], staged=True, info=info, source=sid, sha256=digest(data),
                                                          url=chosen.get('url', ''), license=chosen.get('license', ''))
            self.persist()
        self.audio_cleanup()
        return self.audio_info(key)

    def audio_remove(self, key):
        """Back to the game's own sound (at the next install)."""
        self.audio_row(key)
        with self.lock:
            (self.state.get('audio') or {}).pop(key, None)
            self.persist()
        self.audio_cleanup()
        return self.audio_info(key)

    def audio_stage(self, key, enabled):
        """Leave a saved sound out of the mod, or put it back, without deleting it."""
        self.audio_row(key)
        with self.lock:
            entry = (self.state.get('audio') or {}).get(key)
            if not entry: raise ValueError('Save a sound for this first.')
            entry['staged'] = bool(enabled)
            self.persist()
            return dict(key=key, staged=entry['staged'])

    def audio_file(self, key):
        """The saved replacement itself (OGG), for the review's play button."""
        self.audio_row(key)
        with self.lock: entry = (self.state.get('audio') or {}).get(key)
        if not entry: raise ValueError('No sound is saved for this.')
        return (self.root / 'audio' / (entry['version'] + '.ogg')).read_bytes()

    def staged_audio(self):
        return [dict(key=k, version=e['version'], sha256=e.get('sha256', ''), name=self.row(k)['name'], source=self.row(k)['source'], entry=self.row(k)['entry'],
                     label=e.get('label', ''), info=e.get('info') or {}) for k, e in sorted((self.state.get('audio') or {}).items()) if e.get('staged') and k in self.catalog.rows]

    def audio_chips(self):
        """Staged sounds as entries of the editor's mod list, beside the image edits."""
        return [dict(key=a['key'], name='♫ ' + (self.row(a['key']).get('label') or a['name']), source=a['source'], revision=a['version'], kind='sound') for a in self.staged_audio()]

    def audio_mod(self, journal=None):
        """Every saved sound for the review, in the mod or left out, and whether that version is installed."""
        installed = {a['key']: a['version'] for a in (journal or {}).get('audio') or []}
        return [dict(key=k, name=self.row(k).get('label') or self.row(k)['name'], file=self.row(k)['name'], label=e.get('label', ''), version=e['version'],
                     staged=bool(e.get('staged')), duration=(e.get('info') or {}).get('duration'), installed=installed.get(k) == e['version'])
                for k, e in sorted((self.state.get('audio') or {}).items()) if k in self.catalog.rows]

    def _audio_build(self, sounds, replacements, folder, files):
        """Put the staged sounds in a build. Entries of a ZIP join that file's other replacements, so images and sounds
        in assets.zip go in together; a loose sound file (logo.ogg) is written here. Each saved file is checked first."""
        for a in sounds:
            data = (self.root / 'audio' / (a['version'] + '.ogg')).read_bytes()
            if digest(data) != a['sha256']: raise ValueError('A saved sound changed on disk. Open it and save it again: ' + a['name'])
            if self.catalog.containers.get(a['source']) == 'zip':
                replacements.setdefault(a['source'], {})[a['entry']] = data
                continue
            path = a['source']
            original = self.snapshot(path).read_bytes()
            if file_hash(self.game_file(path)) not in self.accepted(path): raise ValueError('The installed source differs from the saved original: ' + path)
            atomic_bytes(folder / 'files' / path, data)
            files.append(dict(path=path, original_sha256=self.state['baseline'][path]['sha256'], edited_sha256=digest(data), original_bytes=len(original),
                              edited_bytes=len(data), entries=['Sound: ' + a['name']], backup=str(self.snapshot(path))))

    def audio_link(self, url, key=''):
        """Start getting a sound from a link: a background job the editor follows (see audio.fetch)."""
        url = soundlab.check_url(url, resolve=False)[0]
        folder, pages = self.root / 'audio-downloads', self.settings.get('page_links')   # Settings > Sound > Allow page links
        def work(update, cancel):
            samples, rate, meta = soundlab.fetch(url, folder, update, cancel, self.audio_tools, pages=pages)
            update(None, 'Getting it ready…')
            return self._audio_keep(samples, rate, dict(meta, name=meta.get('title') or 'Sound from a link'))
        return self._sound()['jobs'].start('link', work, key=str(key)[:64], url=url[:500])

    def audio_job(self, jid): return self._sound()['jobs'].get(str(jid))

    def audio_cancel(self, jid): return self._sound()['jobs'].cancel(str(jid))

    def audio_tools_status(self): return dict(soundlab.tools_status(self.audio_tools), job=self._sound()['jobs'].running('tools'))

    def audio_tools_install(self):
        """Download the link helper (after the player agreed in the editor). One install at a time."""
        jobs = self._sound()['jobs']
        return jobs.running('tools') or jobs.start('tools', lambda update, cancel: soundlab.install_tools(self.audio_tools, None, update, cancel))

    def audio_tools_update(self):
        jobs = self._sound()['jobs']
        return jobs.running('update') or jobs.start('update', lambda update, cancel: soundlab.update_tools(self.audio_tools, update, cancel))

    def audio_cleanup(self, keep=6):
        """Forget chosen sounds no saved edit uses (apart from the newest few), saved versions nothing points to, and
        leftovers of stopped downloads."""
        with self.lock:
            # Sounds in your other saved builds (builds.py) are kept as well.
            entries = list((self.state.get('audio') or {}).values())
            for saved in (self.root / 'saved-builds').glob('*/state.json'):
                entries += list(((read_json(saved, {}) or {}).get('audio') or {}).values())
            used = {e.get('source') for e in entries}
            versions = {e.get('version') for e in entries} | {a.get('version') for a in (self.active() or {}).get('audio') or []}
            folder = self.root / 'audio-sources'
            if folder.is_dir():
                metas = sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
                for p in [p for p in metas if p.stem not in used][keep:]:
                    for f in (p, p.with_suffix('.flac')): f.unlink(missing_ok=True)
                for f in folder.glob('*.flac'):
                    if not f.with_suffix('.json').is_file(): f.unlink(missing_ok=True)
            if (self.root / 'audio').is_dir():
                for f in (self.root / 'audio').glob('*.ogg'):
                    if f.stem not in versions: f.unlink(missing_ok=True)
            downloads = self.root / 'audio-downloads'
            for d in downloads.iterdir() if downloads.is_dir() else []:
                try:
                    if time.time() - d.stat().st_mtime > 6 * 3600: shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink()
                except OSError: pass

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
        notes = []
        entry = read_json(self.ledger, None, notes, 'The record of the mod installed in the game')
        if notes:   # damaged with no good copy: what Asset Lab installed is unknown, Steam can put the game back
            if entry is None: notes.append('If the game still looks modded, verify its files in Steam (right-click Mitos.is > '
                                           'Properties > Installed Files > Verify integrity of game files).')
            warnings = getattr(getattr(self, 'catalog', None), 'warnings', None)
            (warnings if warnings is not None else self.__dict__.setdefault('early_notes', [])).extend(notes)
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
                        warnings=self.catalog.warnings, active=self.active(), staged=self.staged() + self.audio_chips(), saved_count=sum(bool(self.current(k)) for k in self.state['edits']),
                        stuck=self.drift(), palette=self.palette(), hidden=self.hidden(), pending=self.pending(),
                        fonts=[dict(id=f['id'], label=f['label']) for f in self.staged_fonts()], settings=self.settings.values(),
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
            sounds = self.state.get('audio') or {}   # sounds with a saved replacement count as saved edits
            # Tidy hides leftover atlas rectangles and shows one card per identical copy.
            # The kind filter comes last, so the page can see what every other kind would show (the "kinds" counts) and
            # never leave the player on an empty kind by surprise.
            rows = [r for r in self.catalog.rows.values()
                    if (not group or r['group'] == group)
                    and bool(r.get('removed')) == bool(removed) and not r.get('server_only') and (not favorite or r.get('favorite'))
                    and (not tidy or favorite or saved or removed or (not r.get('legacy') and (parent or r.get('primary', True))))
                    and (not parent or r.get('parent') == parent)
                    and (not origin or r['source'].startswith('cache/' if origin == 'cache' else 'assets/'))
                    and (not saved or self.current(r['key']) or r['key'] in sounds)
                    and all(w in (r['name']+' '+r['source']+' '+r.get('source_label','')+' '+r['entry']+' '+r['group']+' '+r.get('label', '')).casefold() for w in words)]
            if favorite and tidy:
                # Favorites mark identical copies together, but show one card, not every cached archive.
                # Choose among the filtered rows so a cache-only search still has a representative.
                unique = {}
                for row in rows:
                    identity = row.get('dup') or row['key']
                    if identity not in unique or row.get('primary', False): unique[identity] = row
                rows = list(unique.values())
            kinds = dict(Counter(r['kind'] for r in rows), visual=sum(1 for r in rows if r['editable']))
            if kind: rows = [r for r in rows if (r['editable'] if kind == 'visual' else r['kind'] == kind)]
            if target is not None:
                scores = self.similar.rank(target, [r['key'] for r in rows])
                rows = sorted((r for r in rows if r['key'] in scores), key=lambda r: scores[r['key']])[:240]
                return {'total': len(rows), 'kinds': kinds, 'items': [{**r, 'similarity': round(1 - scores[r['key']], 3), 'saved': bool(self.current(r['key'])), 'staged': r['key'] in self.state['staged'], **locked(r)}
                                                                      for r in rows[max(0, offset):max(0, offset)+min(120, limit)]]}
            # By name: whole pictures and sprites side by side (sheets first hid every sprite behind hundreds of pages).
            rows.sort(key=lambda r: (r['group'] == 'Metadata', r['group'] == 'Audio', r['name'].casefold(), r['kind'] != 'image', r['source']))
            return {'total': len(rows), 'kinds': kinds, 'items': [{**r, 'saved': bool(self.current(r['key'])) or r['key'] in sounds, 'staged': r['key'] in self.state['staged'] or bool((sounds.get(r['key']) or {}).get('staged')), **locked(r)}
                                                                  for r in rows[max(0, offset):max(0, offset)+min(120, limit)]]}

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
                # Browsing every page of a big library must not grow memory for ever: the oldest thumbnails go first.
                while len(self._thumbs) > 4000: self._thumbs.pop(next(iter(self._thumbs)))
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
                          source_sha256=self.state['baseline'][row['source']]['sha256'], changed=bool(row['editable'] and self.changed(key)), **locked(row))
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

    def save(self, key, data, revision, recipe=None, document=None):
        with self.lock:
            row = self.row(key)
            self.assert_revision(key, revision)
            if not row['editable']: raise ValueError('Only image assets can be edited.')
            im = decode_png(data)
            if im.size != (row['width'], row['height']):
                raise ValueError(f'Keep the original {row["width"]} × {row["height"]} size. Resize the import to fit in the editor.')
            if len(json.dumps(recipe or {})) > 64000: raise ValueError('Recipe is too large')
            layers = self.layer_document(document, row['width'], row['height']) if document is not None else None
            self.snapshot(row['source'])
            current = self.image(key)
            if im.tobytes() == current.tobytes():
                # Same picture: only the layers changed. They join the current revision (the original gets a revision),
                # unless another saved build shares that revision: then it gets its own, so that build keeps its layers.
                if self.current(key):
                    if not layers: return self.detail(key)
                    if not self.shared_revision(key, self.current(key)['id']):
                        atomic_json(self.root / 'revisions' / key / (self.current(key)['id'] + '.layers.json'), layers)
                        return self.detail(key)
                elif not layers: return self.detail(key)
            version = uuid.uuid4().hex
            data = png(im)
            atomic_bytes(self.root / 'revisions' / key / (version + '.png'), data)
            if layers: atomic_json(self.root / 'revisions' / key / (version + '.layers.json'), layers)
            h = self.history(key)
            self.state['edits'][key] = {'versions': h['versions'][:h['cursor']+1] + [dict(id=version, at=time.time(), sha256=digest(data), recipe=recipe or {})], 'cursor': h['cursor']+1}
            if key not in self.state['staged']: self.state['staged'].append(key)
            self.persist()
            return self.detail(key)

    def shared_revision(self, key, version):
        """Whether another saved build (builds.py) uses this revision of the asset too. Builds share revision files, so a
        revision they share is never changed in place."""
        folder = self.root / 'saved-builds'
        active = (read_json(folder / 'index.json', {}) or {}).get('active')
        for saved in folder.glob('*/state.json'):
            if saved.parent.name == active: continue   # the open build's own copy is out of date; it lives in self.state
            history = ((read_json(saved, {}) or {}).get('edits') or {}).get(key) or {}
            if any(v.get('id') == version for v in history.get('versions') or []): return True
        return False

    # ----- layered documents: the editor's layers, kept beside a revision as revisions/<key>/<version>.layers.json.
    # The flattened PNG stays what installs, exports and themes use; the layers only reopen the editor as it was.
    MAX_LAYERS, MAX_DOCUMENT = 100, 80 * 1024 * 1024

    @classmethod
    def layer_document(cls, doc, width, height):
        """Check an editor document (layer settings plus each layer and mask as base64 PNG) before it is stored."""
        import base64
        if not isinstance(doc, dict) or doc.get('version') != 1: raise ValueError('The layers could not be saved: unknown layer format.')
        if doc.get('width') != width or doc.get('height') != height: raise ValueError(f'The layers must be {width} × {height}, like the asset.')
        layers = doc.get('layers')
        if not isinstance(layers, list) or not 1 <= len(layers) <= cls.MAX_LAYERS: raise ValueError(f'Save between 1 and {cls.MAX_LAYERS} layers.')
        def text(v, n, default=''): return v[:n] if isinstance(v, str) else default
        def unit(v, default=1.0):
            if not isinstance(v, (int, float)) or isinstance(v, bool): return default
            try: v = float(v)
            except OverflowError: v = 1.0 if v > 0 else 0.0   # an integer too long for a float
            return min(1.0, max(0.0, v)) if v == v else default   # NaN gets the default
        def blob(v, what):
            # NaN and Infinity are refused: Python would store them, but the editor's JSON.parse could not read the file back.
            try: size = len(json.dumps(v or {}, allow_nan=False)) if isinstance(v, (dict, type(None))) else -1
            except ValueError: size = -1
            if not 0 <= size <= 256 * 1024: raise ValueError(f'A layer’s {what} settings are damaged or too large.')
            return v
        out = []
        for layer in layers:
            if not isinstance(layer, dict): raise ValueError('A layer is damaged.')
            clean = dict(id=text(layer.get('id'), 32), name=text(layer.get('name'), 255, 'Layer'), kind='adjustment' if layer.get('kind') == 'adjustment' else 'pixel',
                         visible=layer.get('visible') is not False, opacity=unit(layer.get('opacity')), fill=unit(layer.get('fill')),
                         blend=layer.get('blend') if isinstance(layer.get('blend'), str) and re.fullmatch(r'[a-z][a-z0-9-]{0,39}', layer['blend']) else 'normal',
                         locks={k: bool((layer.get('locks') or {}).get(k)) for k in ('alpha', 'pixels', 'position', 'all')} if isinstance(layer.get('locks') or {}, dict) else {},
                         maskEnabled=layer.get('maskEnabled') is not False, clip=bool(layer.get('clip')),
                         effects=blob(layer.get('effects'), 'effect') or {}, adjustment=blob(layer.get('adjustment'), 'adjustment'), data=blob(layer.get('data'), 'module') or {})
            for field in ('png', 'mask_png'):
                if field == 'mask_png' and layer.get(field) is None: continue
                try:
                    with Image.open(io.BytesIO(base64.b64decode(layer.get(field) or '', validate=True))) as im: size, kind = im.size, im.format
                except Exception: raise ValueError('A layer’s picture is damaged.') from None
                if kind != 'PNG' or size != (width, height): raise ValueError(f'Every layer must be a {width} × {height} PNG.')
                clean[field] = layer[field]
            out.append(clean)
        active = doc.get('activeIndex')
        result = dict(version=1, width=width, height=height, activeIndex=active if isinstance(active, int) and 0 <= active < len(out) else len(out) - 1,
                      target='mask' if doc.get('target') == 'mask' else 'pixels', layers=out)
        if len(json.dumps(result)) > cls.MAX_DOCUMENT: raise ValueError('These layers are too big to save. Merge or delete some layers first.')
        return result

    def layers_file(self, key, revision=None):
        """The stored layered document of a revision (default: the current one) as JSON bytes, or None."""
        with self.lock:
            self.row(key)
            revision = revision or self.revision(key)
            if revision not in {v['id'] for v in self.history(key)['versions']}: return None
            path = self.root / 'revisions' / key / (revision + '.layers.json')
            return path.read_bytes() if path.is_file() else None

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

    def revert(self, keys):
        """Put assets back to the game's original art: each one, its identical copies elsewhere, and its spot on a sheet a
        theme changed (the rest of that sheet keeps the theme). History is kept, so Redo in the editor brings an edit back."""
        with self.lock:
            targets = set()
            for key in keys:
                row = self.row(key)
                if not row['editable']: continue
                targets.add(key)
                targets.update(k for k in self.catalog.copies.get(row.get('dup', ''), []) if k in self.catalog.rows and self.row(k)['editable'])
            reverted, sheets = 0, {}
            for key in sorted(targets):
                if self.current(key):
                    h = self.history(key)
                    h['cursor'] = -1
                    self.state['edits'][key] = h
                    if key in self.state['staged']: self.state['staged'].remove(key)
                    reverted += 1
                parent = self.row(key).get('parent')
                if parent and parent not in targets and self.current(parent): sheets.setdefault(parent, []).append(key)
            for parent, children in sheets.items():
                now, original = self.image(parent), self.catalog.image(parent)
                out = now.copy()
                for k in children:
                    x, y, w, h = self.row(k)['rect']
                    out.paste(original.crop((x, y, x + w, y + h)), (x, y))
                if out.tobytes() != now.tobytes():
                    # The sheet keeps its recipe (a themed sheet still shows its theme on the other sprites).
                    recipe = dict((self.current(parent) or {}).get('recipe') or {}, reverted=sorted({self.row(k)['name'] for k in children})[:40])
                    self.commit(parent, out, recipe, persist=False)
                    reverted += 1
            self.persist()
            self._thumbs.clear()
            return dict(reverted=reverted, assets=len(targets))

    def changed(self, key):
        """Whether an asset shows anything but the game's original art: its own edit, a copy's, or a changed sheet."""
        row = self.row(key)
        keys = [key, *self.catalog.copies.get(row.get('dup', ''), [])]
        return any(self.current(k) or (self.catalog.rows.get(k, {}).get('parent') and self.current(self.catalog.rows[k]['parent'])) for k in keys if k in self.catalog.rows)

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
        return [dict(key=k, name=self.row(k)['name'], source=self.row(k)['source'], revision=self.revision(k)) for k in self.state['staged']
                if self.current(k) and not self.row(k).get('server_only')]   # server art never reaches the game (see __init__)

    def _compose(self, keys):
        groups = {}
        for k in keys:
            r = self.row(k)
            if not r['editable'] or not self.current(k): raise ValueError('Only saved visual edits can be installed.')
            groups.setdefault(r.get('parent', k), []).append(k)
        def place(image, row, patch, neighbours=()):
            if row['rotated']: patch = patch.transpose(Image.Transpose.ROTATE_270)
            x, y, w, h = row['rect']
            if patch.size != (w, h): raise ValueError('Sprite geometry mismatch')
            shared = [o['rect'] for o in neighbours if o is not row and o['rect'] != row['rect'] and overlap(row['rect'], o['rect'])]
            if not shared:
                image.paste(patch, (x, y))  # Replace RGBA exactly; never alpha-blend onto the old skin.
                return
            # The game packs some sprites so they share a strip of pixels ("check-on" / "check-off"). There, an edit writes
            # only the pixels it changed from the game's art, so it does not wipe out its edited neighbour's.
            new = np.asarray(patch.convert('RGBA'))
            was = np.asarray(self.catalog.image(row['parent']).convert('RGBA').crop((x, y, x + w, y + h)))
            now = np.asarray(image.convert('RGBA').crop((x, y, x + w, y + h)))
            write = np.ones((h, w), bool)
            for ox, oy, ow, oh in shared:
                left, top, right, bottom = max(x, ox) - x, max(y, oy) - y, min(x + w, ox + ow) - x, min(y + h, oy + oh) - y
                write[top:bottom, left:right] = (new[top:bottom, left:right] != was[top:bottom, left:right]).any(-1)
            image.paste(Image.fromarray(np.where(write[..., None], new, now).astype(np.uint8), 'RGBA'), (x, y))
        sheets = {}
        for sheet_key, edits in groups.items():
            sheet = self.row(sheet_key)
            sprites = [k for k in edits if k != sheet_key]
            # A whole-sheet theme is a base layer; hand edits of single sprites sit on top of it.
            themed = sheet_key in edits and bool((self.current(sheet_key).get('recipe') or {}).get('theme'))
            if sheet_key in edits and sprites and not themed:
                raise ValueError('Choose the full sheet OR its individual sprites in the mod list: ' + sheet['name'])
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
        pastes = {}   # sheet slot id -> [(slot, row placed, edited key)]: placed together, so shared strips are known
        if getattr(self, 'mirror_copies', True):
            for sheet_key, entry in list(sheets.items()):
                for k in entry['sprites']:
                    for copy in self.catalog.copies.get(self.row(k).get('dup', ''), []):
                        row = self.row(copy)
                        if copy == k or copy in keys or row.get('legacy') or not row.get('parent'): continue
                        if row['parent'] == sheet_key and row['rect'] == self.row(k)['rect']: continue
                        slot = target(row['parent'])
                        if slot: pastes.setdefault(id(slot), []).append((slot, row, k))
        for entry in sheets.values():
            for k in entry['sprites']: pastes.setdefault(id(entry), []).append((entry, self.row(k), k))
        for jobs in pastes.values():
            neighbours = [row for _, row, _ in jobs]
            for slot, row, k in jobs: place(slot['image'], row, self.image(k), neighbours)
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
        now = (sorted((r['key'], r['revision']) for r in self.staged()), self.palette(), self.hidden(), sorted((f['id'], f['version']) for f in self.staged_fonts()))
        then = (sorted((r['key'], r['revision']) for r in journal.get('edits', [])), journal.get('palette') or {}, journal.get('hide') or [],
                sorted((f['id'], f['version']) for f in journal.get('fonts') or []))
        sounds = sorted((a['key'], a['version']) for a in self.staged_audio()) != sorted((a['key'], a['version']) for a in journal.get('audio') or [])
        return now != then or sounds

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
                                  revision=r['revision'], installed=then.get(r['key']) == r['revision']))
            by_name = {}
            for row in self.catalog.rows.values():
                if row.get('editable') and row.get('primary', True) and not row.get('legacy'): by_name.setdefault(row['name'], row['key'])
            sprites = lambda group: [by_name[n] for n in exe_palette.HIDE_SPRITES.get(group, []) if n in by_name]
            installed_fonts = {f['id']: f['version'] for f in (journal or {}).get('fonts') or []}
            fonts = [dict(id=f['id'], label=f['label'], face=f['info'].get('face', ''), installed=installed_fonts.get(f['id']) == f['version']) for f in self.staged_fonts()]
            return dict(edits=edits, palette=self.palette(), hide=self.hidden(), hide_sprites={g: sprites(g) for g in exe_palette.HIDE}, fonts=fonts,
                        installed=journal and dict(edits=list(then), palette=journal.get('palette') or {}, hide=journal.get('hide') or []),
                        pending=self.pending(), audio=self.audio_mod(journal))

    def build(self):
        with self.lock, self.install_lock():
            journal = self.active()
            if journal and journal['state'] != 'installed': raise ValueError('An install was interrupted. Restore it before building another.')
            keys = [r['key'] for r in self.staged()]
            colors, hide, fonts = self.palette(), self.hidden(), self.staged_fonts()
            sounds = self.staged_audio()
            if not keys and not colors and not hide and not fonts and not sounds and not journal: raise ValueError('Save and include at least one edit in the mod.')
            for k in keys: self.snapshot(self.row(k)['source'])
            replacements = self._compose(keys) if keys else {}
            build_id = uuid.uuid4().hex
            folder = self.root / 'builds' / build_id
            files = []
            self._audio_build(sounds, replacements, folder, files)   # sounds join the same rebuilt files as the images
            for f in fonts:
                # A new font replaces its page outright, so recolors of the old page give way to it.
                replacements.pop(f['page'], None)
                for path, name in ((f['page'], 'page.png'), (f['fnt'], 'font.fnt')):
                    original = self.snapshot(path).read_bytes()
                    if file_hash(self.game_file(path)) not in self.accepted(path): raise ValueError('The installed source differs from the saved original: ' + path)
                    rebuilt = (self.root / 'fonts' / f['version'] / name).read_bytes()
                    atomic_bytes(folder / 'files' / path, rebuilt)
                    files.append(dict(path=path, original_sha256=self.state['baseline'][path]['sha256'], edited_sha256=digest(rebuilt), original_bytes=len(original),
                                      edited_bytes=len(rebuilt), entries=[f"Font: {f['label']} in {f['info'].get('face', 'a new style')}"], backup=str(self.snapshot(path))))
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
            manifest = dict(id=build_id, game=str(self.game), at=time.time(), files=files, edits=self.staged(), palette=colors, hide=hide,
                            fonts=[dict(id=f['id'], version=f['version']) for f in fonts], audio=[dict(key=a['key'], version=a['version']) for a in sounds], state='ready')
            atomic_json(folder / 'build.json', manifest)
            # Settings > Storage > Mod builds to keep: older builds go (this one and the installed one always stay).
            try: labsettings.prune_builds(self)
            except OSError: pass
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
            if old and self.foreign: raise ValueError('The mod in the game came from a workspace that is no longer on this PC. Use Restore original first.')
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
            atomic_json(self.ledger, journal, keep=True)  # Persist recovery information before the first game write.
            try:
                for f in build['files']:
                    self.ensure_closed()
                    if file_hash(self.game_file(f['path'])) not in (f['original_sha256'], before.get(f['path'], {}).get('edited_sha256')): raise ValueError('Game changed during install')
                    atomic_bytes(self.game_file(f['path']), (self.root / 'builds' / build_id / 'files' / f['path']).read_bytes())
                    if file_hash(self.game_file(f['path'])) != f['edited_sha256']: raise ValueError('Installed file failed verification')
                    journal['installed'].append(f['path'])
                    atomic_json(self.ledger, journal, keep=True)
                for f in dropped:
                    self.ensure_closed()
                    atomic_bytes(self.game_file(f['path']), Path(f['backup']).read_bytes())
                    if file_hash(self.game_file(f['path'])) != f['original_sha256']: raise ValueError('Restore verification failed')
                journal.update(files=build['files'], state='installed' if build['files'] else 'restored', restored_files=[f['path'] for f in dropped])
                atomic_json(self.ledger, journal, keep=True)
                return journal
            except Exception:
                # Leave a recoverable ledger, even if an external updater interfered.
                journal['state'] = 'needs_restore'
                atomic_json(self.ledger, journal, keep=True)
                raise

    def restore(self):
        with self.lock, self.install_lock():
            self.ensure_closed()
            journal = self.active()
            if not journal: return {'state': 'restored', 'files': []}
            if all(file_hash(self.game_file(f['path'])) == f['original_sha256'] for f in journal['files']):
                # Already the originals (Steam put them back): nothing to copy, the mod is just marked as gone.
                journal.update(state='restored', restored_at=time.time())
                atomic_json(self.ledger, journal, keep=True)
                return journal
            ours = lambda f: (f['original_sha256'], f['edited_sha256'], f.get('previous_sha256') or f['original_sha256'])
            for f in journal['files']:
                live = file_hash(self.game_file(f['path']))
                if live not in ours(f):
                    raise ValueError('An external change was detected; restore will not overwrite it: ' + f['path'])
                if not Path(f['backup']).is_file():
                    raise ValueError('The backup of the original game files is gone, so Asset Lab cannot put them back. In Steam, right-click '
                                     'Mitos.is > Properties > Installed Files > Verify integrity of game files, then open Asset Lab again.')
                if file_hash(f['backup']) != f['original_sha256']: raise ValueError('Original backup failed verification')
            for f in journal['files']:
                self.ensure_closed()
                if file_hash(self.game_file(f['path'])) not in ours(f): raise ValueError('Game changed during restore')
                atomic_bytes(self.game_file(f['path']), Path(f['backup']).read_bytes())
                if file_hash(self.game_file(f['path'])) != f['original_sha256']: raise ValueError('Restore verification failed')
            journal['state'] = 'restored'
            journal['restored_at'] = time.time()
            atomic_json(self.ledger, journal, keep=True)
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
            atomic_json(self.ledger, journal, keep=True)
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
