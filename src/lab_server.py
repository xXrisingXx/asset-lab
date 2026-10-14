"""Loopback-only application server for Asset Lab."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import urllib.parse
import webbrowser
import subprocess
import sys
import time
# The portable (embedded) Python ignores PYTHONPATH and does not add the script's folder to the path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_core import Lab, VERSION, ROOT, adjust, png
from theme import Themes, clean_plan, UI_COLORS, HIDE
from design import Designer
import runtime
from PIL import Image


def code_stamp():
    """Newest modification time of the lab's Python code. Pages are served fresh from disk, but Python
    changes only take effect after a restart, so a newer stamp than at startup means the server is stale."""
    return max(p.stat().st_mtime for p in ROOT.glob('*.py'))


STARTED_CODE = code_stamp()


def server_for(lab, port):
    themes = Themes(lab)
    designer = Designer(lab, themes)
    themes.designer = designer

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
                    return self.send(lab.search(q.get('q', ''), '' if starred else q.get('group', ''), q.get('kind', ''), q.get('parent', ''), int(q.get('offset', 0)), saved=q.get('saved') == '1',
                                                origin=q.get('origin', ''), tidy=q.get('all') != '1' and not starred, removed=q.get('removed') == '1', favorite=starred, like=q.get('similar', '')))
                if path == '/api/pool': return self.send(lab.pool.listing())
                if path == '/api/marks': return self.send(dict(favorites=lab.favorites.keys(), removed=lab.pool.listing()['hidden'], labels=lab.labels.mapping()))
                if path == '/api/theme-status': return self.send(dict(themes.status(), design=designer.status(), planner_note=runtime.OLLAMA.note))
                if path == '/api/design-job': return self.send(designer.job(q.get('job', '')))
                if path == '/api/design-image':
                    settings = {k: float(q[k]) for k in ('scale', 'dx', 'dy', 'tolerance') if k in q}
                    image = designer.render(q.get('job', ''), int(q.get('variant', 0)), int(q.get('target', 0)), settings, raw=q.get('raw') == '1')
                    if q.get('small'): image.thumbnail((640, 320), Image.Resampling.LANCZOS)
                    return self.send(png(image), 'image/png')
                if path == '/api/theme-job': return self.send(themes.job(q.get('job', '')))
                if path == '/api/asset': return self.send(lab.detail(key))
                if path == '/api/asset-notes':
                    if q.get('download'):
                        return self.send(lab.asset_notes(key, text=True).encode('utf-8'), 'text/markdown; charset=utf-8', filename='mitosis-asset-notes.md')
                    return self.send(lab.asset_notes(key))
                if path == '/api/asset-notes-all':
                    return self.send(lab.export_asset_notes(), 'application/zip', filename='mitosis-all-visual-locations.zip')
                if path == '/api/thumb': return self.send(lab.thumbnail(key, q.get('original') == '1', (320, 240) if q.get('big') else (144, 112)), 'image/png')
                if path == '/api/mod':
                    # What the next install contains, with the friendly name of each game color for the review.
                    names = {field: dict(name=name.replace('_', ' '), was=was, what=what) for name, (field, was, what) in UI_COLORS.items()}
                    return self.send(dict(lab.mod(), colors=names, hide_labels={g: label for g, (label, _) in HIDE.items()}))
                if path == '/api/image': return self.send(png(lab.image(key, q.get('original') != '1')), 'image/png', filename='mitosis-texture.png' if q.get('download') else None)
                if path == '/api/recipe': return self.send(lab.export_recipe(key), 'application/zip', filename='mitosis-asset-recipe.zip')
                if path == '/api/raw':
                    row = lab.row(key)
                    mime = 'audio/ogg' if row['kind'] == 'audio' else 'application/octet-stream'
                    return self.send(lab.catalog.raw(row), mime, filename='mitosis-original' + Path(row['entry'] or row['source']).suffix if q.get('download') else None)
                if path == '/api/text':
                    row = lab.row(key)
                    data = lab.catalog.raw(row)
                    return self.send({'text': data[:250000].decode('utf-8', 'replace'), 'truncated': len(data) > 250000})
                static = ROOT / 'web'
                file = (static / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
                if not file.is_relative_to(static) or not file.is_file(): return self.send({'error': 'Not found'}, status=404)
                return self.send(file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream')
            except (BrokenPipeError, ConnectionResetError): pass
            except (ValueError, OSError, KeyError, RuntimeError) as exc: self.send({'error': str(exc)}, status=400)

        def do_POST(self):
            try:
                path, q = self.route()
                if self.headers.get('X-Lab-Token') != lab.session: raise ValueError('Invalid session token; reload the editor.')
                origin = self.headers.get('Origin')
                if origin and origin != 'http://' + self.headers['Host']: raise ValueError('Cross-origin writes are blocked.')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 <= length <= 96 * 1024 * 1024: raise ValueError('Request too large')
                raw = self.rfile.read(length)
                data = json.loads(raw) if raw else {}
                key = data.get('key', '')
                if path == '/api/save':
                    return self.send(lab.save(key, base64.b64decode(data['png'], validate=True), data['revision'], data.get('recipe')))
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
                if path == '/api/install': return self.send(lab.install(data['build']))
                if path == '/api/restore': return self.send(lab.restore())
                if path == '/api/abandon-install': return self.send(lab.abandon())
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
                    return self.send(designer.start(data.get('prompt', ''), only, int(data.get('count') or 2), base))
                if path == '/api/design-apply':
                    result = designer.apply(data.get('job', ''), int(data.get('variant', 0)), {k: float(v) for k, v in (data.get('settings') or {}).items() if k in ('scale', 'dx', 'dy', 'tolerance')})
                    return self.send(result)
                if path == '/api/design-undo': return self.send(designer.undo(data.get('job', '')))
                if path == '/api/design-cancel': return self.send(designer.cancel(data.get('job', '')))
                if path == '/api/theme-chat': return self.send(themes.chat(data.get('message'), data.get('model', ''), data.get('history'), only))
                if path == '/api/theme-preview': return self.send(themes.preview(clean_plan(data.get('plan')), themes.scope(only)))
                if path == '/api/theme-apply': return self.send(themes.apply(data.get('plan'), data.get('prompt', ''), only))
                if path == '/api/theme-undo': return self.send(themes.undo())
                if path == '/api/open-workspace':
                    if os.name == 'nt': os.startfile(str(lab.root))
                    return self.send({'path': str(lab.root)})
                if path == '/api/launch-game':
                    if lab.active() and lab.active()['state'] != 'installed': raise ValueError('Restore the incomplete install before launching.')
                    if os.name == 'nt': os.startfile('steam://rungameid/389570')
                    return self.send({'opened': True})
                if path == '/api/shutdown':
                    self.send({'stopping': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self.send({'error': 'Unknown action'}, status=404)
            except (BrokenPipeError, ConnectionResetError): pass
            except (ValueError, OSError, KeyError, RuntimeError) as exc: self.send({'error': str(exc)}, status=400)

    class Server(ThreadingHTTPServer):
        daemon_threads = False
        block_on_close = True
        restart = False
    # A restarted server can start before the old one has released the port; give it a moment.
    for attempt in range(40):
        try: return Server(('127.0.0.1', port), Handler)
        except OSError:
            if attempt == 39: raise
            time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game')
    parser.add_argument('--workspace')
    parser.add_argument('--ledger-dir')
    parser.add_argument('--port', type=int, default=8753)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    lab = Lab(args.game, args.workspace, args.ledger_dir)
    server = server_for(lab, args.port)
    address = f'http://127.0.0.1:{server.server_port}'
    print('Asset Lab: ' + address, flush=True)
    print('Game: ' + str(lab.game), flush=True)
    print('Workspace: ' + str(lab.root), flush=True)
    if not args.no_browser: webbrowser.open(address)
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
