"""Loopback-only application server for Asset Lab."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import traceback
import urllib.parse
import webbrowser
import subprocess
import sys
import time
# The portable (embedded) Python ignores PYTHONPATH and does not add the script's folder to the path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_core import Lab, VERSION, ROOT, adjust, png, data_home
from theme import Themes, clean_plan, UI_COLORS, HIDE
from design import Designer
from builds import Builds
import activity
import runtime
import preview
import audio as soundlab
import settings as labsettings
AUDIO_REQUEST = 210 * 1024 * 1024   # a 150 MB sound file, even sent as base64 in JSON (only /api/audio-source)
DESIGN_SETTINGS = ('scale', 'dx', 'dy', 'tolerance', 'shape', 'fill', 'white', 'calm')
from PIL import Image


def code_stamp():
    """Newest modification time of the lab's Python code. Pages are served fresh from disk, but Python
    changes only take effect after a restart, so a newer stamp than at startup means the server is stale."""
    return max(p.stat().st_mtime for p in ROOT.glob('*.py'))


STARTED_CODE = code_stamp()
LANGUAGES = ('en', 'es', 'pt', 'fr', 'de')   # the lab's languages (web/i18n/<code>.json), as the launcher offers them


def ui_language(lab):
    """The language the player picked: "language" in settings.json ('' = follow the browser), as the launcher writes it."""
    store = getattr(lab, 'settings', None)
    if store is None: return ''
    try: value = store.get('language')
    except KeyError:   # a settings schema without the Language setting: the launcher's value as it is in the file
        with store.lock: value = store._load().get('language', '')
    return value if value in LANGUAGES else ''


def page_language(page, language):
    """index.html with the chosen language on <html>, so web/i18n.js translates before the first paint."""
    return page.replace(b'<html lang="en">', f'<html lang="{language or "en"}" data-language="{language}">'.encode(), 1)


def server_for(lab, port):
    builds = Builds(lab)  # recover interrupted switches before indexing art or reading theme history
    themes = Themes(lab)
    designer = Designer(lab, themes)
    themes.designer = designer
    designer.director = themes.art_direction
    designer.concepts = themes.art_concepts   # and a different idea for each variant   # the planner model writes the art direction for each redraw

    def quiet():
        """Builds change only while nothing is being applied or drawn into the current one."""
        if any(job['state'] == 'running' for job in themes.jobs.values()) or designer.status().get('job'):
            raise ValueError('Wait for the theme or drawing to finish first.')

    class Handler(BaseHTTPRequestHandler):
        timeout = 30  # a browser's idle pre-opened connection must not keep the server from stopping

        def log_message(self, *args): pass

        def send(self, data, mime='application/json', status=200, filename=None):
            if not isinstance(data, bytes): data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            for name, value in {'Content-Type': mime, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
                                'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                                'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"}.items():
                self.send_header(name, value)
            if filename: self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                raise ValueError('Invalid local Host')
            url = urllib.parse.urlsplit(self.path)
            return url.path, {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}

        def fail(self, exc):
            """Every failed request gets a JSON answer, never a dropped connection. Problems the lab explains (ValueError...)
            and values of the wrong kind in a request (a list where a name belongs) are 400; anything else is the lab's own
            fault (500). Those two kinds also print their traceback to the log (the Asset Lab window), so no bug is hidden."""
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)): return
            if isinstance(exc, (TypeError, AttributeError)): status, text = 400, f'The request was not understood: a value has the wrong type ({exc}).'
            elif isinstance(exc, (ValueError, OSError, KeyError, RuntimeError)): status, text = 400, str(exc)
            else: status, text = 500, f'Something went wrong in the lab ({type(exc).__name__}: {exc}). The details are in the Asset Lab window.'
            if status == 500 or isinstance(exc, (TypeError, AttributeError)):
                print(f'Asset Lab: {self.command} {self.path.split("?")[0]} failed:', file=sys.stderr)
                traceback.print_exception(exc, file=sys.stderr)
            try: self.send({'error': text}, status=status)
            except OSError: pass

        def do_GET(self):
            try:
                path, q = self.route()
                key = q.get('key', '')
                if path == '/api/health': return self.send(dict(app='Asset Lab', version=VERSION, pid=os.getpid(), shutdown=True))
                if path == '/api/status': return self.send(dict(lab.status(), stale=code_stamp() > STARTED_CODE))
                if path == '/api/code-stamp': return self.send(dict(stale=code_stamp() > STARTED_CODE, pid=os.getpid()))
                if path == '/api/game': return self.send(lab.game_state())
                if path == '/api/assets':
                    starred = q.get('group') == 'favorites'  # the Favorites entry in the category list
                    limit = max(1, min(120, int(q.get('limit') or lab.settings.get('page_size'))))   # Settings > Library > Assets per page
                    found = lab.search(q.get('q', ''), '' if starred else q.get('group', ''), q.get('kind', ''), q.get('parent', ''), int(q.get('offset', 0)), saved=q.get('saved') == '1',
                                       origin=q.get('origin', ''), tidy=q.get('all') != '1', removed=q.get('removed') == '1', favorite=starred, like=q.get('similar', ''), limit=limit)
                    return self.send(dict(found, limit=limit))   # the page turns by this many
                if path == '/api/settings': return self.send(labsettings.payload(lab, VERSION))
                if path == '/api/language': return self.send(dict(language=ui_language(lab), languages=list(LANGUAGES)))
                if path == '/api/settings-usage': return self.send(labsettings.usage(lab))
                if path == '/api/notices': return self.send(labsettings.notices())
                if path == '/api/pool': return self.send(lab.pool.listing())
                if path == '/api/marks': return self.send(dict(favorites=lab.favorites.keys(), removed=lab.pool.listing()['hidden'], labels=lab.labels.mapping()))
                if path == '/api/theme-status': return self.send(dict(themes.status(), design=designer.status(), planner_note=runtime.OLLAMA.note))
                if path == '/api/design-job': return self.send(designer.job(q.get('job', '')))
                if path == '/api/design-image':
                    settings = {k: float(q[k]) for k in DESIGN_SETTINGS if k in q}
                    image = designer.render(q.get('job', ''), int(q.get('variant', 0)), int(q.get('target', 0)), settings, raw=q.get('raw') == '1')
                    if q.get('small'): image.thumbnail((640, 320), Image.Resampling.LANCZOS)
                    return self.send(png(image), 'image/png')
                if path == '/api/theme-job': return self.send(themes.job(q.get('job', '')))
                if path == '/api/ai-activity': return self.send(activity.snapshot(designer, themes))   # the activity strip in Theme AI
                if path == '/api/builds': return self.send(builds.listing())
                if path == '/api/chats': return self.send(builds.chats())
                if path == '/api/chat': return self.send(builds.chat(q.get('id', '')))
                if path == '/api/asset': return self.send(lab.detail(key))
                if path == '/api/fonts': return self.send(lab.font_catalog())
                if path == '/api/asset-notes':
                    if q.get('download'):
                        return self.send(lab.asset_notes(key, text=True).encode('utf-8'), 'text/markdown; charset=utf-8', filename='mitosis-asset-notes.md')
                    return self.send(lab.asset_notes(key))
                if path == '/api/asset-notes-all':
                    return self.send(lab.export_asset_notes(), 'application/zip', filename='mitosis-all-visual-locations.zip')
                if path == '/api/preview':   # the menu or a match, rebuilt from the game's sprites with your edits
                    with lab.lock: image = preview.render(lab, q.get('screen', 'menu'), int(q.get('w', 1280)), q.get('mode', 'light'), q.get('controls', 'pc'))
                    return self.send(png(image), 'image/png')
                if path == '/api/thumb': return self.send(lab.thumbnail(key, q.get('original') == '1', (320, 240) if q.get('big') else (144, 112)), 'image/png')
                if path == '/api/mod':
                    # What the next install contains, with the friendly name of each game color for the review.
                    names = {field: dict(name=name.replace('_', ' '), was=was, what=what) for name, (field, was, what) in UI_COLORS.items()}
                    return self.send(dict(lab.mod(), colors=names, hide_labels={g: label for g, (label, _) in HIDE.items()}))
                if path == '/api/image': return self.send(png(lab.image(key, q.get('original') != '1')), 'image/png', filename='mitosis-texture.png' if q.get('download') else None)
                if path == '/api/document': return self.send(b'{"document":' + (lab.layers_file(key, q.get('revision') or None) or b'null') + b'}')   # the editor's saved layers
                if path == '/api/recipe': return self.send(lab.export_recipe(key), 'application/zip', filename='mitosis-asset-recipe.zip')
                if path == '/api/raw':
                    row = lab.row(key)
                    mime = 'audio/ogg' if row['kind'] == 'audio' else 'application/octet-stream'
                    return self.send(lab.catalog.raw(row), mime, filename='mitosis-original' + Path(row['entry'] or row['source']).suffix if q.get('download') else None)
                if path == '/api/text':
                    row = lab.row(key)
                    data = lab.catalog.raw(row)
                    return self.send({'text': data[:250000].decode('utf-8', 'replace'), 'truncated': len(data) > 250000})
                if path.startswith('/api/audio'): return self.audio_get(path, q)   # the sound editor (see audio_get)
                static = ROOT / 'web'
                file = (static / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
                if not file.is_relative_to(static) or not file.is_file(): return self.send({'error': 'Not found'}, status=404)
                body = file.read_bytes()
                if file == (static / 'index.html').resolve(): body = page_language(body, ui_language(lab))
                return self.send(body, mimetypes.guess_type(file)[0] or 'application/octet-stream')
            except Exception as exc: self.fail(exc)

        def do_POST(self):
            try:
                path, q = self.route()
                if self.headers.get('X-Lab-Token') != lab.session: raise ValueError('Invalid session token; reload the editor.')
                origin = self.headers.get('Origin')
                if origin and origin != 'http://' + self.headers['Host']: raise ValueError('Cross-origin writes are blocked.')
                if getattr(lab, '_build_recovery_required', False) and path != '/api/shutdown':
                    raise ValueError('Build recovery is pending. Restart Asset Lab after checking disk space and file access.')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 <= length <= (AUDIO_REQUEST if path == '/api/audio-source' else 96 * 1024 * 1024): raise ValueError('Request too large')
                raw = self.rfile.read(length)
                if path.startswith('/api/audio'): return self.audio_post(path, q, raw)   # the sound editor (see audio_post)
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict): raise ValueError('Send the request as a JSON object, like {"key": "…"}.')
                key = data.get('key', '')
                if not isinstance(key, str): raise ValueError('Send the asset’s key as text.')
                if path == '/api/settings':
                    # {values: {name: value}} changes settings; {reset: "all" | [names]} puts them back to their defaults.
                    before = lab.settings.get('keep_builds')
                    if 'reset' in data:
                        reset = data['reset']
                        if reset != 'all' and not (isinstance(reset, list) and all(isinstance(k, str) for k in reset)): raise ValueError('Send reset as "all" or a list of setting names.')
                        values = lab.settings.reset(None if reset == 'all' else reset)
                    else: values = lab.settings.update(data.get('values'))
                    out = dict(values=values, changed=lab.settings.changed())
                    if values['keep_builds'] != before: out['pruned'] = labsettings.prune_builds(lab)   # fewer builds kept: the old ones go now
                    return self.send(out)
                if path == '/api/settings-action': return self.send(labsettings.run_action(lab, str(data.get('id', '')), data))
                if path == '/api/save':
                    return self.send(lab.save(key, base64.b64decode(data['png'], validate=True), data['revision'], data.get('recipe'), data.get('document')))
                if path == '/api/adjust':
                    output = adjust(base64.b64decode(data['png'], validate=True), data.get('settings', {}))
                    return self.send({'png': base64.b64encode(output).decode()})
                if path == '/api/undo': return self.send(lab.step(key, data['revision'], -1))
                if path == '/api/redo': return self.send(lab.step(key, data['revision'], 1))
                if path == '/api/stage': return self.send(lab.stage(key, bool(data['enabled'])))
                if path == '/api/stage-many':
                    keys = [k for k in data.get('keys') or [] if isinstance(k, str)][:5000]
                    return self.send(lab.stage_many(keys, bool(data['enabled'])))
                if path == '/api/build': return self.send(lab.build())
                if path == '/api/install':
                    result = lab.install(data['build'])
                    builds.installed(True)
                    return self.send(result)
                if path == '/api/restore':
                    result = lab.restore()
                    builds.installed(False)
                    return self.send(result)
                if path == '/api/abandon-install':
                    result = lab.abandon()
                    builds.installed(False)
                    return self.send(result)
                if path == '/api/revert':   # back to the game's original art, history kept
                    keys = [k for k in data.get('keys') or [] if isinstance(k, str)][:5000]
                    quiet()
                    result = lab.revert(keys)
                    themes.reindex()
                    return self.send(result)
                if path in ('/api/build-switch', '/api/build-new', '/api/build-rename', '/api/build-delete'):
                    bid = str(data.get('id') or '')
                    if path != '/api/build-rename': quiet()
                    if path == '/api/build-switch': result = builds.switch(bid)
                    elif path == '/api/build-new': result = builds.create(str(data.get('name') or ''), str(data.get('copy') or ''))
                    elif path == '/api/build-rename': result = builds.rename(bid, data.get('name'))
                    else: result = builds.delete(bid)
                    themes.reindex()   # its art is what Theme AI samples now
                    return self.send(result)
                if path == '/api/chat-save': return self.send(builds.save_chat(str(data.get('id') or ''), data.get('entries'), data.get('history'), str(data.get('title') or '')))
                if path == '/api/chat-delete': return self.send(builds.delete_chat(str(data.get('id') or '')))
                if path in ('/api/pool-remove', '/api/pool-restore'):
                    with lab.lock:
                        lab.row(key)
                        result = lab.pool.remove(key, bool(data.get('sheet'))) if path.endswith('remove') else lab.pool.restore(key)
                    themes.reindex()  # removed art is no longer themed
                    return self.send(result)
                if path == '/api/mark':
                    # One action for a whole selection: favorite, unfavorite, remove (from the pool) or restore.
                    keys = [k for k in data.get('keys') or [] if isinstance(k, str)][:5000]
                    action = data.get('action')
                    if action not in ('favorite', 'unfavorite', 'remove', 'restore'): raise ValueError('Unknown mark')
                    with lab.lock:
                        for k in keys: lab.row(k)
                        marks = lab.favorites if 'favorite' in action else lab.pool
                        result = marks.add(keys) if action in ('favorite', 'remove') else marks.discard(keys)
                    if marks is lab.pool: themes.reindex()
                    return self.send(dict(result, action=action, count=len(keys)))
                if path == '/api/label':
                    keys = [k for k in data.get('keys') or [] if isinstance(k, str)][:5000]
                    with lab.lock:
                        for k in keys: lab.row(k)
                        result = lab.labels.name(keys, data.get('name', ''))
                    themes.reindex()  # Theme AI can now be asked about "the speed potion"
                    return self.send(result)
                if path == '/api/close-game': return self.send(lab.close_game())
                if path == '/api/similar-image': return self.send({'id': lab.similar.upload(data.get('image', ''))})
                if path == '/api/palette': return self.send({'palette': lab.set_palette(data.get('colors') or {})})
                if path == '/api/hide': return self.send({'hidden': lab.set_hidden(data.get('groups') or [])})
                if path == '/api/restart':
                    if any(job['state'] == 'running' for job in themes.jobs.values()): raise ValueError('Wait for the theme to finish applying, then restart.')
                    self.send({'restarting': True})
                    self.server.restart = True
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                only = data.get('only') if isinstance(data.get('only'), list) else None
                if path == '/api/design-start':
                    base = data.get('base') if isinstance(data.get('base'), list) and len(data['base']) == 2 else None
                    return self.send(designer.start(data.get('prompt', ''), only, int(data.get('count') or labsettings.value(lab, 'drawings')), base, data.get('direction') or None))
                if path == '/api/design-apply':
                    result = designer.apply(data.get('job', ''), int(data.get('variant', 0)), {k: float(v) for k, v in (data.get('settings') or {}).items() if k in DESIGN_SETTINGS})
                    return self.send(result)
                if path == '/api/design-undo': return self.send(designer.undo(data.get('job', '')))
                if path == '/api/design-cancel': return self.send(designer.cancel(data.get('job', '')))
                if path == '/api/font-preview':
                    image = lab.font_preview(str(data.get('font', '')), data.get('style') or {}, str(data.get('text', ''))[:40])
                    return self.send({'png': 'data:image/png;base64,' + base64.b64encode(image).decode()})
                if path == '/api/font-apply': return self.send(lab.font_apply(str(data.get('font', '')), data.get('style') or {}))
                if path == '/api/font-remove': return self.send(lab.font_remove(str(data.get('font', ''))))
                if path == '/api/theme-chat': return self.send(themes.chat(data.get('message'), data.get('model', ''), data.get('history'), only, language=str(data.get('language') or ''), draw=str(data.get('draw') or 'auto')))
                if path == '/api/theme-preview': return self.send(themes.preview(clean_plan(data.get('plan')), themes.scope(only)))
                if path == '/api/theme-apply': return self.send(themes.apply(data.get('plan'), data.get('prompt', ''), only))
                if path == '/api/theme-undo': return self.send(themes.undo())
                if path == '/api/open-workspace':
                    if os.name == 'nt': os.startfile(str(lab.root))
                    return self.send({'path': str(lab.root)})
                if path == '/api/launch-game':
                    if lab.active() and lab.active()['state'] != 'installed': raise ValueError('Restore the incomplete install before launching.')
                    if lab.game_state().get('running'): return self.send({'opened': False, 'running': True})
                    # Straight from Mitosis.exe: no Steam round trip, and Steam can't be left thinking the game still
                    # runs after the lab closed it. Steam's own launch is the fallback.
                    exe = lab.game / 'Mitosis.exe'
                    if os.name == 'nt':
                        try: subprocess.Popen([str(exe)], cwd=str(lab.game), creationflags=0x00000008 | 0x00000200, close_fds=True)
                        except OSError: os.startfile('steam://rungameid/389570')
                    return self.send({'opened': True})
                if path == '/api/shutdown':
                    # These jobs run outside request threads; server_close alone cannot wait for them.
                    quiet()
                    sounds = getattr(lab, '_sounds', None)
                    if sounds:
                        with sounds['jobs'].lock:
                            if any(job['state'] == 'running' for job, _ in sounds['jobs'].items.values()):
                                raise ValueError('Finish or cancel the sound download or helper update before closing Asset Lab.')
                    self.send({'stopping': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self.send({'error': 'Unknown action'}, status=404)
            except Exception as exc: self.fail(exc)

        # ----- sounds: the sound editor's endpoints (Lab.audio_* and audio.py) ---------------------------------------
        # GET  /api/audio?key=            the game's sound and the saved replacement     /api/audio-tools   link helper state
        # GET  /api/audio-preview?key=&source=&settings=JSON[&info=1]   WAV of the edit (report in X-Lab-Audio) or the report
        # GET  /api/audio-file?key=       the saved replacement (OGG)                    /api/audio-job?id= a job's progress
        # POST /api/audio-source          a sound file: raw bytes (name in X-Lab-Name) or JSON {name, data: base64}
        # POST /api/audio-link {url, key} -> job      /api/audio-cancel {id}      /api/audio-apply {key, source, settings}
        # POST /api/audio-remove {key}   /api/audio-stage {key, enabled}   /api/audio-tools-install   /api/audio-tools-update
        def end_headers(self):
            for name, value in self.__dict__.pop('extra_headers', {}).items(): self.send_header(name, value)
            super().end_headers()

        def audio_get(self, path, q):
            key = q.get('key', '')
            try:
                if path == '/api/audio': return self.send(lab.audio_info(key))
                if path == '/api/audio-preview':
                    settings = json.loads(q.get('settings') or '{}')
                    data, report = lab.audio_preview(key, q.get('source', ''), settings if isinstance(settings, dict) else {}, info_only=q.get('info') == '1')
                    if data is None: return self.send(report)
                    self.extra_headers = {'X-Lab-Audio': json.dumps(report, ensure_ascii=True)}
                    return self.send(data, 'audio/wav')
                if path == '/api/audio-file': return self.send(lab.audio_file(key), 'audio/ogg')
                if path == '/api/audio-job': return self.send(lab.audio_job(q.get('id', '')))
                if path == '/api/audio-tools': return self.send(lab.audio_tools_status())
                return self.send({'error': 'Not found'}, status=404)
            except soundlab.AudioError as exc: return self.send({'error': str(exc), 'code': exc.code}, status=400)

        def audio_post(self, path, q, raw):
            try:
                if path == '/api/audio-source':
                    if (self.headers.get('Content-Type') or '').startswith('application/octet-stream'):
                        return self.send(lab.audio_source_add(urllib.parse.unquote(self.headers.get('X-Lab-Name') or ''), raw))
                    data = json.loads(raw) if raw else {}
                    if not isinstance(data, dict): raise ValueError('Send the sound as a file, or as a JSON object {name, data}.')
                    return self.send(lab.audio_source_add(str(data.get('name', '')), base64.b64decode(str(data.get('data', '')), validate=True)))
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict): raise ValueError('Send the request as a JSON object, like {"key": "…"}.')
                if path == '/api/audio-link': return self.send(lab.audio_link(str(data.get('url', '')), str(data.get('key', ''))))
                if path == '/api/audio-cancel': return self.send(lab.audio_cancel(str(data.get('id', ''))))
                if path == '/api/audio-apply':
                    settings = data.get('settings') if isinstance(data.get('settings'), dict) else {}
                    return self.send(lab.audio_apply(str(data.get('key', '')), str(data.get('source', '')), settings))
                if path == '/api/audio-remove': return self.send(lab.audio_remove(str(data.get('key', ''))))
                if path == '/api/audio-stage': return self.send(lab.audio_stage(str(data.get('key', '')), bool(data.get('enabled'))))
                if path == '/api/audio-tools-install': return self.send(lab.audio_tools_install())
                if path == '/api/audio-tools-update': return self.send(lab.audio_tools_update())
                return self.send({'error': 'Unknown action'}, status=404)
            except soundlab.AudioError as exc: return self.send({'error': str(exc), 'code': exc.code}, status=400)

    class Server(ThreadingHTTPServer):
        daemon_threads = False
        block_on_close = True
        restart = False
        request_queue_size = 64   # a page load asks for ~45 files at once; the default backlog of 5 refused some (a missing editor script)
    # A restarted server can start before the old one has released the port; give it a moment.
    for attempt in range(40):
        try: return Server(('127.0.0.1', port), Handler)
        except OSError:
            if attempt == 39: raise
            time.sleep(0.25)


def open_page(lab, address, no_browser=False, opener=webbrowser.open):
    """Open the lab in the browser unless --no-browser was given or Settings > General > Open the lab in your browser is
    off. (The launcher always passes --no-browser and reads that setting itself.) Returns whether it opened."""
    if no_browser or not lab.settings.get('open_browser'): return False
    opener(address)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game')
    parser.add_argument('--workspace')
    parser.add_argument('--ledger-dir')
    parser.add_argument('--port', type=int, default=8753)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    # The settings file is the one the launcher reads, whichever workspace this lab uses.
    lab = Lab(args.game, args.workspace, args.ledger_dir, settings_file=data_home() / 'settings.json')
    server = server_for(lab, args.port)
    address = f'http://127.0.0.1:{server.server_port}'
    print('Asset Lab: ' + address, flush=True)
    print('Game: ' + str(lab.game), flush=True)
    print('Workspace: ' + str(lab.root), flush=True)
    lab.settings.values()   # reading them also checks settings.json
    if lab.settings.damaged: print('Settings: settings.json could not be read; using the defaults (the file is kept as settings.json.bad).', flush=True)
    open_page(lab, address, args.no_browser)
    threading.Thread(target=lab.similar.build, daemon=True).start()  # warm the image-search index
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        if server.restart and os.environ.get('ASSET_LAB_LAUNCHER'):
            # The launcher owns the server process: exit with "restart me" and it starts the new code itself.
            server.socket.close()
            runtime.OLLAMA.stop()
            os._exit(75)
        if not server.restart: runtime.OLLAMA.stop()
        if server.restart:
            # Same arguments, fresh code. The port is released first; the new process inherits this one's
            # console and job, so the launcher still owns it. Idle browser connections are not waited for.
            server.socket.close()
            flags = (['-B'] if sys.dont_write_bytecode else []) + (['-X', 'utf8'] if sys.flags.utf8_mode else [])
            subprocess.Popen([sys.executable, *flags, *sys.argv], cwd=os.getcwd(), creationflags=0x08000000 if os.name == 'nt' else 0)
            os._exit(0)
        server.server_close()


if __name__ == '__main__': main()
