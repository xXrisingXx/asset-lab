"""Asset Lab's settings: what players can change, grouped the way the Settings window shows them.

They live in one small JSON file, settings.json in the data home (beside the workspace), which the launcher reads too
(open_browser and check_updates). Only the values a player changed are stored, so better defaults in a newer version
still reach everyone else. Every value is checked against SCHEMA (its type, its choices or its range): a value that does
not fit is ignored. Keys this version does not know are kept as they are, so an older lab never drops a newer one's
settings. A damaged file is copied to settings.json.bad and the defaults are used until the player changes something.

The rest of this module does what some settings and buttons stand for: keeping only the last mod builds, clearing
caches, removing the link helper, measuring disk use, and the privacy note (checked against the code by the tests).
"""
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid

import suggest

CATEGORIES = [
    dict(id='general', label='General', intro='Starting Asset Lab, installing your mod and opening the game.'),
    dict(id='appearance', label='Appearance', intro='How the lab looks on your screen.'),
    dict(id='editor', label='Editor', intro='Undo, the canvas and the tools.'),
    dict(id='theme', label='Theme AI & drawing', intro='The local AI that plans themes and the image model that redraws art.'),
    dict(id='text', label='Text & fonts', intro='Restyling the game’s own text.'),
    dict(id='sound', label='Sound', intro='Replacing the game’s music and sound effects.'),
    dict(id='library', label='Library', intro='Browsing the game’s art.'),
    dict(id='storage', label='Storage & privacy', intro='Disk space, caches, and everything the lab connects to.'),
    dict(id='about', label='About', intro='Version, updates, licences and where your files are.'),
]


def _s(key, category, label, description, kind, default=None, **more):
    return dict(key=key, category=category, label=label, description=description, type=kind, default=default, **more)


def _pick(*pairs): return [dict(value=v, label=l) for v, l in pairs]


# restart='app': the launcher reads it when Asset Lab starts; restart='page': the lab reads it when its page opens.
LANGUAGES = (('', 'Automatic (Windows language)'), ('en', 'English'), ('es', 'Español'), ('pt', 'Português'), ('fr', 'Français'), ('de', 'Deutsch'))
SCHEMA = [
    # ----- general
    _s('language', 'general', 'Language', 'The language of the lab (the page reloads to switch) and of the Asset Lab start window (from its next start). Automatic follows Windows.',
       'choice', '', choices=_pick(*LANGUAGES), keywords='language translation idioma langue sprache english español português français deutsch'),
    _s('open_browser', 'general', 'Open the desktop editor automatically', 'Off: setup waits for you to click Open Asset Lab. The editor opens in its own desktop window.',
       'bool', True, restart='app', keywords='launch start desktop window'),
    _s('check_updates', 'general', 'Check for updates when Asset Lab starts', 'Looks for a newer Asset Lab on GitHub and asks before installing it. Your work is never touched.',
       'bool', True, restart='app', keywords='update version release github'),
    _s('confirm_install', 'general', 'Show the build first', 'Build to Mitosis and Restore original first show what goes in or comes out, with Apply build or Restore original to go ahead. Off: they go straight ahead.',
       'bool', True, keywords='confirm install restore mod game files build preview review'),
    _s('game_open', 'general', 'If Mitosis is open when you install', 'The game has to be closed to change its files. “Close it for me” closes it without asking; a match in progress ends.',
       'choice', 'ask', choices=_pick(('ask', 'Ask me first'), ('close', 'Close it for me')), keywords='running game close quit'),
    _s('launch_after_install', 'general', 'Open Mitosis after installing', 'Starts the game through Steam as soon as your mod is installed or updated, so you can check it.',
       'bool', False, keywords='launch play steam start game'),
    # ----- appearance
    _s('ui_size', 'appearance', 'Interface size', 'Makes the panels, the top bar and the lab’s windows bigger or smaller. The canvas and the editor’s tools keep their size.',
       'choice', 100, choices=_pick((90, '90%'), (100, '100%'), (110, '110%'), (125, '125%')), keywords='zoom scale text font bigger smaller readable'),
    _s('accent', 'appearance', 'Accent color', 'The color of buttons, highlights and selected items.', 'choice', 'teal',
       choices=_pick(('teal', 'Teal'), ('blue', 'Sky blue'), ('violet', 'Violet'), ('pink', 'Pink'), ('orange', 'Orange'), ('lime', 'Lime')), keywords='colour theme highlight'),
    _s('reduce_motion', 'appearance', 'Reduce motion', 'Stops the lab’s spinners, sliding progress bars and fades. The selection outline in the editor still moves.', 'bool', False, keywords='animation transition motion sickness'),
    _s('canvas_bg', 'appearance', 'Canvas background', 'What shows through transparent pixels in the editor. Game teal is the color behind Mitosis’s menus.',
       'choice', 'auto', choices=_pick(('auto', 'Automatic: white behind faint or dark art'), ('dark', 'Dark checker'), ('light', 'Light checker'), ('teal', 'Game teal'), ('gray', 'Plain gray')), keywords='checkerboard transparent backdrop white automatic'),
    _s('compact_cards', 'appearance', 'Compact library cards', 'Smaller cards, three to a row, so more assets fit on screen.', 'bool', False, keywords='library grid thumbnails small'),
    # ----- editor
    _s('history_states', 'editor', 'Undo steps', 'How many steps Undo can take back. The oldest go first.', 'choice', 50,
       choices=_pick((20, '20'), (50, '50'), (100, '100'), (200, '200')), keywords='history undo redo'),
    _s('history_memory', 'editor', 'Undo memory', 'The most memory Undo may use. Big sheets need more; lower it if your PC is short on memory.', 'choice', 512,
       choices=_pick((256, '256 MB'), (512, '512 MB'), (1024, '1 GB')), keywords='history undo ram'),
    _s('pixel_grid', 'editor', 'Pixel grid when zoomed in', 'Thin lines between pixels from 800% zoom, like View › Pixel Grid.', 'bool', True, keywords='grid zoom view'),
    _s('sharp_pixels', 'editor', 'Crisp pixels', 'Starts with Pixels on, so every zoom level shows sharp, unblurred pixels.', 'bool', False, keywords='pixelated nearest smooth blurry'),
    _s('start_focus', 'editor', 'Start in Focus mode', 'Opens with the library and mod tray hidden (F switches). Off: the lab opens the way you left it.',
       'bool', False, restart='page', keywords='focus hide library tray'),
    _s('remember_tools', 'editor', 'Remember tool settings', 'Keeps each tool’s size, opacity and other options between visits. Off: every tool starts fresh.',
       'bool', True, restart='page', keywords='brush size options defaults'),
    # ----- Theme AI and drawing
    _s('planner_model', 'theme', 'Planner model', 'The local AI model that turns your requests into theme plans. Automatic uses the best one installed.',
       'text', '', options='models', max_length=200, pattern=r'[\w.:/@+-]*', keywords='ollama llm ai model'),
    _s('auto_apply', 'theme', 'Apply plans automatically', 'Applies a theme as soon as its plan is ready (the Auto-apply box), instead of waiting for you.', 'bool', False),
    _s('offer_redraws', 'theme', 'Offer title redraws for moods', 'After a whole-game mood like “make everything spooky”, offers to redraw the title to match. It never starts by itself.',
       'bool', True, keywords='image model design'),
    _s('offer_text_styles', 'theme', 'Offer matching text styles', 'After a mood theme, shows the player names in a matching look to try. Nothing changes until you click Apply.',
       'bool', True, keywords='font names'),
    _s('drawings', 'theme', 'Drawings per request', 'How many variants the image model draws each time. Each takes about half a minute on a good graphics card.', 'choice', 2,
       choices=_pick((1, '1'), (2, '2'), (3, '3'), (4, '4')), keywords='variants image model redraw'),
    _s('drawing_quality', 'theme', 'Drawing quality', 'More steps can add detail but take longer: Balanced takes about 1.5× as long, Detailed about 2×.', 'choice', 'fast',
       choices=_pick(('fast', 'Fast (4 steps)'), ('balanced', 'Balanced (6 steps)'), ('detailed', 'Detailed (8 steps)')), keywords='steps image model speed'),
    _s('keep_white', 'theme', 'Keep game-colored icons white', 'Ticks “Keep it white” for redraws of icons the game colors itself, so they take the game’s color.', 'bool', False,
       keywords='tint icons redraw'),
    _s('web_inspiration', 'theme', 'Look things up for inspiration', 'When a request names someone or something (“a Post Malone theme”), Theme AI reads '
       'its Wikipedia summary and pictures to get the look right. Off: nothing is looked up.', 'bool', True, keywords='wikipedia internet web search online'),
    _s('accelerate', 'theme', 'Accelerate', 'Draws with the whole graphics card and every processor core, at high priority: faster, '
       'but the computer is slow while it draws (games and videos may stutter).', 'bool', False, keywords='speed fast faster gpu cpu turbo performance'),
    _s('free_gpu', 'theme', 'Free the graphics card before drawing', 'Unloads the planner model first, so both don’t fight over graphics memory. Turn it off only with 12 GB or more.',
       'bool', True, keywords='vram gpu memory unload'),
    # ----- text and fonts
    _s('font_sample', 'text', 'Example text', 'The words shown in font previews, such as your player name (up to 40 characters).', 'text', 'Cellmate', max_length=40,
       keywords='preview name sample'),
    _s('font_prefetch', 'text', 'Preload look previews', 'Draws every ready-made look in the background, so flipping through them is instant. Off: less work for slow PCs.',
       'bool', True, keywords='speed slow pc'),
    # ----- sound
    _s('sound_match', 'sound', 'Match the game’s loudness', 'New sounds start as loud as the game’s own sound; the volume slider still works on top.', 'bool', True, keywords='volume loudness'),
    _s('sound_quality', 'sound', 'Sound quality', 'How sounds are saved (OGG Vorbis): about 112, 160, 224 or 320 kbit/s for music. Higher means bigger files.', 'choice', 'standard',
       choices=_pick(('small', 'Smaller files'), ('standard', 'Standard'), ('high', 'High'), ('best', 'Best')), keywords='ogg vorbis bitrate size'),
    _s('sound_fade_in', 'sound', 'Fade in', 'Seconds of fade-in new sounds start with (0 = none). A short effect gets a shorter fade.', 'number', 0.0,
       min=0, max=10, step=0.05, unit='s', keywords='fade'),
    _s('sound_fade_out', 'sound', 'Fade out', 'Seconds of fade-out new sounds start with (0 = none).', 'number', 0.0, min=0, max=10, step=0.05, unit='s', keywords='fade'),
    _s('page_links', 'sound', 'Allow page links', 'Lets the link helper get sound from pages like YouTube, SoundCloud and Bandcamp. Off: only direct links to sound files.',
       'bool', True, keywords='youtube soundcloud bandcamp yt-dlp link helper'),
    _s('sound_max_minutes', 'sound', 'Longest sound', 'Anything longer is cut to this length when you bring it in. Shorter limits use less memory.', 'choice', 20,
       choices=_pick((1, '1 minute'), (2, '2 minutes'), (5, '5 minutes'), (10, '10 minutes'), (20, '20 minutes')), keywords='length duration limit music'),
    _s('update_helper', 'sound', 'Update the link helper', 'Gets the newest yt-dlp. Websites change, and new versions keep up with them.', 'action', button='Update',
       keywords='yt-dlp link helper'),
    _s('remove_helper', 'sound', 'Remove the link helper', 'Deletes the link helper to free its disk space. It is downloaded again, after asking, when a page link needs it.',
       'action', button='Remove…', danger=True, keywords='yt-dlp deno pyav delete disk space'),
    # ----- library
    _s('page_size', 'library', 'Assets per page', 'How many cards each page of the library shows.', 'choice', 72, choices=_pick((48, '48'), (72, '72'), (120, '120'))),
    _s('show_copies', 'library', 'Show copies & leftovers', 'Starts with Copies & leftovers ticked: identical copies in other files and atlas pieces the game no longer uses.',
       'bool', False, keywords='duplicates legacy'),
    _s('open_last', 'library', 'Reopen the last asset', 'Opens the asset you were working on when the lab starts.', 'bool', False, restart='page', keywords='resume remember'),
    # ----- storage and privacy
    _s('keep_builds', 'storage', 'Mod builds to keep', 'Every review and install makes a build of your mod in the workspace; older ones are deleted. The installed one is always kept.',
       'choice', 10, choices=_pick((5, 'Last 5'), (10, 'Last 10'), (25, 'Last 25'), (0, 'All')), keywords='disk space cleanup builds'),
    _s('clear_caches', 'storage', 'Clear caches', 'Frees memory and disk: thumbnails, the list of fonts on this PC and sounds you chose but never saved. They come back when needed.',
       'action', button='Clear', keywords='cache memory disk space thumbnails'),
    _s('open_workspace', 'storage', 'Workspace folder', 'Your edits, mod builds and the backups of the game’s files.', 'action', button='Open', show='workspace',
       keywords='folder explorer files path'),
]
BY_KEY = {s['key']: s for s in SCHEMA if s['type'] != 'action'}
ACTIONS = {s['key']: s for s in SCHEMA if s['type'] == 'action'}
assert {s['category'] for s in SCHEMA} <= {c['id'] for c in CATEGORIES}

# What some choices stand for, in the units the lab works in.
STEPS = dict(fast=4, balanced=6, detailed=8)                       # stable-diffusion.cpp --steps
QUALITY = dict(small=0.3, standard=0.5, high=0.7, best=0.9)         # Vorbis quality (audio.encode_ogg)

# Every place Asset Lab connects to. tests/test_settings.py checks it against every web address in the code and in
# bootstrap.json, so this note stays true.
PRIVACY = [
    dict(what='Updates', hosts=['api.github.com', 'github.com'],
         text='When Asset Lab starts (unless you turn it off under General), the launcher asks GitHub for its newest release, and downloads it from GitHub if you agree.'),
    dict(what='First start', hosts=['www.python.org', 'files.pythonhosted.org'],
         text='The launcher downloads a portable Python from python.org and its libraries (numpy, Pillow, soundfile) from PyPI, once.'),
    dict(what='Local AI, if you agree', hosts=['huggingface.co', 'github.com'],
         text='The image model and the planner model come from Hugging Face, Ollama and stable-diffusion.cpp from GitHub. Once downloaded, they run on this PC: your requests never leave it.'),
    dict(what='Link helper, if you agree', hosts=['github.com', 'files.pythonhosted.org'],
         text='yt-dlp and Deno come from GitHub and PyAV from PyPI, the first time a sound needs them. “Update the link helper” asks GitHub for a newer yt-dlp.'),
    dict(what='Inspiration lookups', hosts=['en.wikipedia.org', 'upload.wikimedia.org', 'thumb.wikimedia.org'],
         text='When a Theme AI request names a person, band, game or film (unless you turn it off under Theme AI & drawing), only that name is looked up on Wikipedia. '
              'A few of its pictures are downloaded and kept on this PC, so the drawings can take their look from them.'),
    dict(what='Suggestions you send', hosts=['docs.google.com'],
         text='What you type under About › Suggestions, and the Asset Lab version, go to the creator’s Google Form when you press Send. No name, account or anything else.'),
    dict(what='Links you paste', hosts=[],
         text='A link you paste in the sound editor is downloaded from that website; web pages are read by the link helper, which may only reach public internet addresses.'),
]
PRIVACY_NOTE = ('Nothing else: no accounts, no ads, no tracking and no usage statistics. Your art, edits and requests stay on this PC (only a '
                'name to look up goes to Wikipedia), and the '
                'lab’s page can only talk to the lab itself.')


# ----- checking values ---------------------------------------------------------------------------------------------
def _number_text(v): return f'{v:g}'


def clean(spec, value):
    """value checked against its setting, in the setting's own type; ValueError with a message for the player."""
    kind, label = spec['type'], spec['label']
    if kind == 'bool':
        if isinstance(value, bool): return value
        raise ValueError(f'{label}: turn it on or off.')
    if kind == 'choice':
        for v in ([] if isinstance(value, bool) else [c['value'] for c in spec['choices']]):
            if value == v and isinstance(value, str) == isinstance(v, str): return v
            if isinstance(value, str) and not isinstance(v, str) and value.strip() == str(v): return v   # a <select> sends "72"
        names = [c['label'] for c in spec['choices']]
        raise ValueError(f'{label}: choose ' + (', '.join(names[:-1]) + ' or ' + names[-1] if len(names) > 1 else names[0]) + '.')
    if kind == 'number':
        if isinstance(value, str):
            try: value = float(value.strip())
            except ValueError: pass
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{label}: enter a number.')
        low, high, unit = spec['min'], spec['max'], spec.get('unit', '')
        if not low <= value <= high: raise ValueError(f'{label}: use {_number_text(low)} to {_number_text(high)}{(" " + unit) if unit else ""}.')
        step = spec.get('step')
        if step: value = min(high, max(low, round(round((value - low) / step) * step + low, 6)))
        return float(value)
    if kind == 'text':
        if not isinstance(value, str): raise ValueError(f'{label}: enter some text.')
        value = ' '.join(re.sub(r'[\x00-\x1f\x7f‎‏‪-‮⁦-⁩]', ' ', value).split())
        if len(value) > spec.get('max_length', 200): raise ValueError(f'{label}: use at most {spec.get("max_length", 200)} characters.')
        if spec.get('pattern') and not re.fullmatch(spec['pattern'], value): raise ValueError(f'{label}: that name has characters a model name never has.')
        return value or spec['default']
    raise ValueError(f'{label} is a button, not a setting.')


def fits(spec, value):
    try: clean(spec, value)
    except ValueError: return False
    return True


def public_schema():
    """The schema as the Settings window gets it (plain JSON)."""
    return [dict(s) for s in SCHEMA]


# ----- the file ----------------------------------------------------------------------------------------------------
def write_text(path, text):
    """Write a file in one piece: a temporary file beside it, then a rename (retried while antivirus holds the file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temp.open('x', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 19: raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temp.exists(): temp.unlink()


class Settings:
    """settings.json: read again whenever it changes on disk, written whole and in one piece."""
    NOTE = 'Asset Lab settings (the gear in the lab). Only changed values are kept; delete this file to go back to every default.'

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.damaged = False
        self._stamp, self._raw = None, {}

    def _load(self):
        try: stat = self.path.stat()
        except OSError:
            self._stamp, self._raw = None, {}
            return self._raw
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp: return self._raw
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict): raise ValueError('not a JSON object')
            self._raw, self.damaged = data, False
        except (OSError, ValueError):
            # Kept for the player to look at; the lab carries on with the defaults and writes a fresh file on the next change.
            try: shutil.copyfile(self.path, self.path.with_name(self.path.name + '.bad'))
            except OSError: pass
            self._raw, self.damaged = {}, True
        self._stamp = stamp
        return self._raw

    def get(self, key):
        spec = BY_KEY[key]
        with self.lock: raw = self._load()
        if key in raw:
            try: return clean(spec, raw[key])
            except ValueError: pass
        return spec['default']

    def values(self):
        with self.lock: raw = dict(self._load())
        out = {}
        for key, spec in BY_KEY.items():
            out[key] = spec['default']
            if key in raw:
                try: out[key] = clean(spec, raw[key])
                except ValueError: pass
        return out

    def changed(self):
        """The keys a player set to something other than the default (with valid values)."""
        values = self.values()
        return sorted(k for k, spec in BY_KEY.items() if values[k] != spec['default'])

    def update(self, changes):
        """Check and store {key: value}; nothing is stored when any value does not fit. A value set back to its default
        is taken out of the file. Returns the effective values."""
        if not isinstance(changes, dict) or not changes: raise ValueError('Send the settings to change as {"key": value}.')
        checked = {}
        for key, value in changes.items():
            if key not in BY_KEY: raise ValueError(f'There is no setting called “{str(key)[:60]}”.')
            checked[key] = clean(BY_KEY[key], value)
        with self.lock:
            raw = dict(self._load())
            raw.update(checked)
            self._write(raw)
        return self.values()

    def reset(self, keys=None):
        """Back to the defaults: the given keys, or every setting (unknown keys are kept)."""
        keys = list(BY_KEY) if keys is None else list(keys)
        for key in keys:
            if key not in BY_KEY: raise ValueError(f'There is no setting called “{str(key)[:60]}”.')
        with self.lock:
            raw = dict(self._load())
            for key in keys: raw.pop(key, None)
            self._write(raw)
        return self.values()

    def _write(self, raw):
        # Known keys are stored checked, and only when they differ from the default (a value that does not fit is
        # dropped); anything else, such as a newer version's settings, stays exactly as it was.
        data = {'//': self.NOTE}
        for k, v in raw.items():
            if k == '//': continue
            if k not in BY_KEY: data[k] = v
            elif fits(BY_KEY[k], v) and clean(BY_KEY[k], v) != BY_KEY[k]['default']: data[k] = clean(BY_KEY[k], v)
        write_text(self.path, json.dumps(data, indent=2, ensure_ascii=False) + '\n')
        self.damaged, self._stamp = False, None


def value(lab, key):
    """A setting of this lab, or its default for a lab object without settings (some tests build their own)."""
    store = getattr(lab, 'settings', None)
    return store.get(key) if store is not None else BY_KEY[key]['default']


# ----- what the settings do ----------------------------------------------------------------------------------------
def folder_size(path):
    """(bytes, files) under a folder (or of one file); links are not followed."""
    path, total, count = Path(path), 0, 0
    try:
        if path.is_file(): return path.stat().st_size, 1
    except OSError: return 0, 0
    stack = [path]
    while stack:
        try: entries = list(os.scandir(stack.pop()))
        except OSError: continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False): stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False): total += entry.stat(follow_symlinks=False).st_size; count += 1
            except OSError: continue
    return total, count


def mb(n): return f'{n / 1e9:.1f} GB' if n >= 1e9 else f'{n / 1e6:.1f} MB' if n >= 1e5 else f'{max(0, round(n / 1e3))} KB'


BUILD_ID = re.compile(r'[a-f0-9]{32}')


def needed_builds(lab):
    """Builds an install record still points to: the one installed now, and any install that was interrupted."""
    keep = set()
    records = [lab.ledger] + sorted(Path(lab.ledger_dir).glob('*.json'))
    for path in records:
        try: journal = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError): continue
        if isinstance(journal, dict) and journal.get('state') != 'restored' and journal.get('workspace', str(lab.root)) == str(lab.root):
            keep.update(str(journal.get(k)) for k in ('id', 'updated_from') if journal.get(k))
    return keep


def prune_builds(lab, keep=None):
    """Keep only the newest `keep` builds (0 keeps all) in workspace/builds; never one an install record needs."""
    keep = value(lab, 'keep_builds') if keep is None else int(keep)
    folder = Path(lab.root) / 'builds'
    if not keep or not folder.is_dir(): return dict(removed=0, freed=0)
    with lab.lock:
        builds = [d for d in folder.iterdir() if d.is_dir() and BUILD_ID.fullmatch(d.name)]
        def made(d):
            try: return float(json.loads((d / 'build.json').read_text(encoding='utf-8')).get('at') or 0) or d.stat().st_mtime
            except (OSError, ValueError, TypeError, AttributeError):
                try: return d.stat().st_mtime
                except OSError: return 0.0
        builds.sort(key=made, reverse=True)
        needed, removed, freed = needed_builds(lab), 0, 0
        for d in builds[keep:]:
            if d.name in needed: continue
            size = folder_size(d)[0]
            shutil.rmtree(d, ignore_errors=True)
            if not d.exists(): removed, freed = removed + 1, freed + size
        return dict(removed=removed, freed=freed)


def clear_caches(lab):
    """Thumbnails and font previews in memory, the list of this PC's fonts, and chosen sounds no saved edit uses."""
    import fonts as fontlab
    with lab.lock:
        thumbs = len(getattr(lab, '_thumbs', {}) or {})
        getattr(lab, '_thumbs', {}).clear()
        lab._typefaces = None
    with fontlab.LOCK: (lab.__dict__.get('_font_previews') or {}).clear()
    faces = Path(lab.root).parent / 'typefaces.json'
    freed = folder_size(faces)[0]
    faces.unlink(missing_ok=True)
    sources = Path(lab.root) / 'audio-sources'
    before = folder_size(sources)[0]
    if hasattr(lab, '_sound'):
        cache = lab._sound()
        with cache['lock']: cache.update(samples={}, power={})
        lab.audio_cleanup(keep=0)
    freed += max(0, before - folder_size(sources)[0])
    return dict(thumbnails=thumbs, freed=freed, message=f'Caches cleared: {thumbs} thumbnail{"s" if thumbs != 1 else ""} and {mb(freed)} on disk. They come back when needed.')


def helper_status(lab):
    import audio as soundlab
    tools = Path(lab.audio_tools)
    status = soundlab.tools_status(tools)
    return dict(path=str(tools), ready=status['ready'], installed=tools.exists(), bytes=folder_size(tools)[0] if tools.exists() else 0,
                updated=status.get('updated', ''), installed_at=status.get('installed_at'), available=status['available'])


def remove_helper(lab):
    """Delete the link helper (yt-dlp, Deno, PyAV): first moved aside in one step, so it is either all there or all gone."""
    tools = Path(lab.audio_tools)
    jobs = lab._sound()['jobs']
    if any(jobs.running(kind) for kind in ('link', 'tools', 'update')):
        raise ValueError('The link helper is busy. Wait for the download or update to finish, then remove it.')
    for old in tools.parent.glob(tools.name + '.removing-*'): shutil.rmtree(old, ignore_errors=True)   # an earlier attempt's leftovers
    if not tools.exists(): return dict(removed=False, freed=0, message='The link helper is not installed.')
    av = sys.modules.get('av')
    if av is not None and Path(getattr(av, '__file__', '') or '.').resolve().is_relative_to(tools.resolve()):
        raise ValueError('The link helper’s sound decoder is in use. Restart the lab, then remove it.')
    size = folder_size(tools)[0]
    trash = tools.with_name(f'{tools.name}.removing-{uuid.uuid4().hex[:8]}')
    try: os.replace(tools, trash)
    except OSError: raise ValueError('Windows says the link helper is in use. Restart the lab, then remove it.') from None
    shutil.rmtree(trash, ignore_errors=True)
    return dict(removed=True, freed=size, message=f'Link helper removed: {mb(size)} freed. It is downloaded again, after asking, when a page link needs it.')


def update_helper(lab):
    import audio as soundlab
    if not soundlab.helper_ready(lab.audio_tools): raise ValueError('The link helper is not installed. It is downloaded the first time a page link needs it.')
    return dict(job=lab.audio_tools_update())


def usage(lab, runtime_root=None):
    """Disk use of the workspace (by part), the local AI runtime and the link helper."""
    import runtime
    root = Path(lab.root)
    parts = {}
    for name in ('revisions', 'originals', 'builds', 'designs', 'audio', 'audio-sources', 'fonts'):
        parts[name] = folder_size(root / name)[0]
    total = folder_size(root)[0]
    builds = [d for d in (root / 'builds').iterdir() if d.is_dir() and BUILD_ID.fullmatch(d.name)] if (root / 'builds').is_dir() else []
    helper = helper_status(lab)
    rt = Path(runtime_root or runtime.ROOT)
    rt_bytes = folder_size(rt)[0] if rt.is_dir() else 0
    if Path(helper['path']).resolve().is_relative_to(rt.resolve()): rt_bytes = max(0, rt_bytes - helper['bytes'])
    return dict(workspace=dict(path=str(root), bytes=total, parts=parts), builds=dict(count=len(builds), bytes=parts['builds'], keep=value(lab, 'keep_builds')),
                runtime=dict(path=str(rt), bytes=rt_bytes), helper=helper, data=dict(path=str(Path(lab.settings.path).parent) if getattr(lab, 'settings', None) else str(root.parent)))


def about(lab, version):
    """Version, where updates come from, and the folders the lab uses."""
    import runtime
    app = Path(__file__).resolve().parent.parent
    try: repo = str(json.loads((app / 'bootstrap.json').read_text(encoding='utf-8')).get('repo') or '')
    except (OSError, ValueError, AttributeError): repo = ''
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or repo.startswith('OWNER/'): repo = ''
    data = Path(lab.settings.path).parent if getattr(lab, 'settings', None) else Path(lab.root).parent
    local = os.environ.get('LOCALAPPDATA')
    folders = [dict(id='data', label='Your settings and marks', path=str(data)),
               dict(id='workspace', label='Workspace: edits, mod builds, game file backups', path=str(lab.root)),
               dict(id='ledgers', label='Install records', path=str(lab.ledger_dir)),
               dict(id='runtime', label='Local AI and link helper', path=str(runtime.ROOT)),
               dict(id='app', label='Asset Lab’s program files', path=str(app))]
    if local and (Path(local) / 'AssetLab').is_dir(): folders.append(dict(id='launcher', label='Launcher: Python, downloads, update backups', path=str(Path(local) / 'AssetLab')))
    return dict(version=version, repo=repo, releases=f'https://github.com/{repo}/releases' if repo else '', launcher=bool(os.environ.get('ASSET_LAB_LAUNCHER')),
                source=(app / '.git').exists(), folders=folders, notices=(app / 'THIRD-PARTY-NOTICES.md').is_file(), suggest=suggest.info())


def notices():
    app = Path(__file__).resolve().parent.parent
    parts = []
    for name in ('THIRD-PARTY-NOTICES.md', 'LICENSE'):
        try: parts.append((name, (app / name).read_text(encoding='utf-8')))
        except OSError: pass
    if not parts: raise ValueError('The licence notices are missing from this copy of Asset Lab.')
    return dict(files=[dict(name=n, text=t) for n, t in parts])


def payload(lab, version):
    """Everything the Settings window needs, in one answer."""
    return dict(categories=CATEGORIES, schema=public_schema(), values=lab.settings.values(), changed=lab.settings.changed(), damaged=lab.settings.damaged,
                file=str(lab.settings.path), privacy=PRIVACY, privacy_note=PRIVACY_NOTE, about=about(lab, version))


def run_action(lab, name, data=None):
    """A button in the Settings window."""
    if name == 'clear_caches': return clear_caches(lab)
    if name == 'remove_helper': return remove_helper(lab)
    if name == 'update_helper': return update_helper(lab)
    if name == 'prune_builds':
        done = prune_builds(lab)
        return dict(done, message=f'{done["removed"]} old build{"s" if done["removed"] != 1 else ""} deleted ({mb(done["freed"])} freed).')
    if name in ('open_workspace', 'open_folder'):
        folders = {f['id']: f['path'] for f in about(lab, '')['folders']}
        which = 'workspace' if name == 'open_workspace' else str((data or {}).get('folder', ''))
        if which not in folders: raise ValueError('Unknown folder.')
        target = Path(folders[which])
        if not target.exists(): raise ValueError('This folder does not exist yet: ' + str(target))
        if os.name == 'nt': os.startfile(str(target))
        return dict(path=str(target), opened=os.name == 'nt')
    raise ValueError('Unknown settings action.')
