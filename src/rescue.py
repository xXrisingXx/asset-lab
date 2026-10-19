"""Safe mode: when Asset Lab cannot start, it opens this page instead of stopping.

The lab's window still opens (this answers on the lab's port like the lab does), and shows what went wrong in words,
its details, and what can still be done: Try again, Put the game's original files back (when a mod is installed: from
the install record and its backups alone, without the rest of the lab) and open the error log. It depends on as
little as possible (no lab_core), since what failed may be anything.
"""
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse

import errors

LANGUAGES = ('en', 'es', 'pt', 'fr', 'de')


def home():
    """Where the lab's data lives (as lab_core.data_home, without importing it)."""
    if os.environ.get('ASSET_LAB_HOME'): return Path(os.environ['ASSET_LAB_HOME'])
    older = Path.home() / 'MitosisAssetLab'
    return older if older.is_dir() else Path.home() / 'AssetLab'


def translator():
    """The page in the player's language: the lab's own translations of these sentences."""
    try: lang = json.loads((home() / 'settings.json').read_text(encoding='utf-8')).get('language') or ''
    except (OSError, ValueError, AttributeError): lang = ''
    if not lang:   # Automatic: Windows' language
        import locale
        lang = (locale.getlocale()[0] or 'en')[:2].lower()
        lang = {'sp': 'es', 'po': 'pt', 'fr': 'fr', 'ge': 'de'}.get(lang, lang)
    if lang not in LANGUAGES or lang == 'en': return lambda text: text
    try: strings = json.loads((Path(__file__).resolve().parent / 'web' / 'i18n' / f'{lang}.json').read_text(encoding='utf-8'))['strings']
    except (OSError, ValueError, KeyError): strings = {}
    return lambda text: strings.get(text, text)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def installed(game):
    """The install record of the mod in this game, if one is installed (else None)."""
    if not game: return None
    ledger = home() / 'install-ledgers' / (hashlib.sha256(str(Path(game).resolve()).casefold().encode()).hexdigest()[:24] + '.json')
    for path in (ledger, ledger.with_name(ledger.name + '.bak')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            return (ledger, record) if record.get('state') != 'restored' else None
        except (OSError, ValueError): continue
    return None


def game_file(game, relative):
    if relative.startswith('cache/'):
        cache = Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / 'Freakinware/Mitosis'
        name = relative[len('cache/'):]
        if not re.fullmatch(r'[a-f0-9]{32}\.dat', name): raise ValueError('Unexpected file in the install record: ' + relative)
        return cache / name
    path = (Path(game) / relative).resolve()
    if not path.is_relative_to((Path(game) / 'assets').resolve()): raise ValueError('Unexpected file in the install record: ' + relative)
    return path


def mitosis_open():
    if os.name != 'nt': return False
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Mitosis.exe', '/NH'], capture_output=True, text=True, creationflags=0x08000000)
    return 'mitosis.exe' in out.stdout.casefold()


def restore(game, running=mitosis_open):
    """Put the game's original files back from the install record and its backups (the same checks as the lab)."""
    found = installed(game)
    if not found: return 'Nothing to put back: no mod from Asset Lab is installed.'
    ledger, record = found
    if running(): raise ValueError('Close every Mitosis window first.')
    files = record.get('files', [])
    for f in files:
        live = sha256(game_file(game, f['path']))
        ours = (f['original_sha256'], f['edited_sha256'], f.get('previous_sha256') or f['original_sha256'])
        if live not in ours: raise ValueError('Something else changed this file since, so it is left alone: ' + f['path'])
        if live != f['original_sha256'] and (not Path(f['backup']).is_file() or sha256(f['backup']) != f['original_sha256']):
            raise ValueError('The backup of the original files is gone. In Steam: right-click Mitos.is > Properties > Installed Files > '
                             'Verify integrity of game files.')
    for f in files:
        target = game_file(game, f['path'])
        if sha256(target) != f['original_sha256']:
            temp = target.with_name(target.name + '.restore.tmp')
            temp.write_bytes(Path(f['backup']).read_bytes()); os.replace(temp, target)
    record.update(state='restored', restored_at=time.time())
    ledger.write_text(json.dumps(record, indent=2), encoding='utf-8')
    return 'The game has its original files back.'


PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>Asset Lab</title><style>
body{{margin:0;font:14px 'Segoe UI',sans-serif;background:#10151e;color:#e8eef6;display:flex;justify-content:center}}
main{{max-width:760px;width:100%;padding:36px 28px}}h1{{font-size:22px;margin:0 0 6px}}.bad{{color:#ff8a8a}}
p{{color:#a9b8c8;line-height:1.6}}.box{{background:#171e29;border:1px solid #303d4e;border-radius:10px;padding:16px 18px;margin:16px 0}}
.reason{{color:#ffd9a8;font-weight:600;white-space:pre-wrap}}form{{display:inline}}button{{font:inherit;border-radius:8px;padding:10px 18px;margin:4px 8px 4px 0;
border:1px solid #46586b;background:#1e2835;color:#e8eef6;cursor:pointer}}button.primary{{background:#70e5d4;color:#102b2a;border-color:#70e5d4;font-weight:700}}
pre{{white-space:pre-wrap;font-size:11px;color:#adbece;background:#0c1017;padding:12px;border-radius:6px;max-height:300px;overflow:auto}}
.done{{color:#9fffd0}}summary{{cursor:pointer;color:#a9b8c8}}</style></head><body><main>
<h1 class="bad">{title}</h1><p>{lead}</p>
<div class="box"><div class="reason">{reason}</div></div>{result}
<form method="post" action="/rescue/retry"><input type="hidden" name="token" value="{token}"><button class="primary">{retry}</button></form>
{restore}<form method="post" action="/rescue/logs"><input type="hidden" name="token" value="{token}"><button>{logs}</button></form>
<p>{send}</p><details><summary>{more}</summary><pre>{details}</pre></details></main></body></html>'''


def serve(port, error, game=None):
    """Answer on the lab's port with the safe-mode page until the player tries again or closes Asset Lab."""
    t = translator()
    token = secrets.token_urlsafe(24)
    state = {'result': ''}
    reason = str(error) if isinstance(error, BaseException) and str(error) else type(error).__name__

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def reply(self, body, status=200, mime='text/html; charset=utf-8'):
            data = body.encode('utf-8')
            self.send_response(status)
            for k, v in {'Content-Type': mime, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                         'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"}.items():
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(data)

        def local(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def page(self):
            mod = installed(game)
            restore_button = (f'<form method="post" action="/rescue/restore"><input type="hidden" name="token" value="{token}">'
                              f'<button>{html.escape(t("Put the game’s original files back"))}</button></form>') if mod else ''
            return PAGE.format(title=html.escape(t('Asset Lab could not start')),
                               lead=html.escape(t('Nothing is lost: your workspace and edits are kept. This is what stopped it:')),
                               reason=html.escape(reason), token=token, retry=html.escape(t('Try again')), restore=restore_button,
                               logs=html.escape(t('Open the error log')), more=html.escape(t('Details')), details=html.escape(errors.details(error)),
                               send=html.escape(t('If it keeps happening, send errors.log (in the logs folder) to the creator, or use Settings › About › Suggestions once it starts.')),
                               result=state['result'])

        def do_GET(self):
            if not self.local(): return self.reply('', 400)
            path = urllib.parse.urlsplit(self.path).path
            if path in ('/api/health', '/api/status'):
                return self.reply(json.dumps({'app': 'Asset Lab', 'rescue': True, 'error': reason}), mime='application/json')
            if path == '/favicon.ico': return self.reply('', 404)
            return self.reply(self.page())

        def do_POST(self):
            if not self.local(): return self.reply('', 400)
            length = int(self.headers.get('Content-Length') or 0)
            form = urllib.parse.parse_qs(self.rfile.read(min(length, 4096)).decode('utf-8', 'replace'))
            if form.get('token', [''])[0] != token: return self.reply('', 403)
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == '/rescue/retry':
                    self.reply('<!doctype html><meta http-equiv="refresh" content="4;url=/"><body style="background:#10151e;color:#e8eef6;'
                               f'font:16px Segoe UI;padding:40px">{html.escape(t("Starting Asset Lab…"))}</body>')
                    self.server.retry = True
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if path == '/rescue/restore':
                    state['result'] = f'<p class="done">{html.escape(t(restore(game)))}</p>'
                elif path == '/rescue/logs':
                    folder = errors.folder(); folder.mkdir(parents=True, exist_ok=True)
                    if os.name == 'nt': os.startfile(str(folder))
                    state['result'] = f'<p class="done">{html.escape(str(folder))}</p>'
            except Exception as exc:
                errors.record('safe mode', exc)
                state['result'] = f'<p class="bad">{html.escape(t(str(exc)))}</p>'
            self.send_response(303); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers()

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        retry = False

    for attempt in range(40):   # the port may be let go a moment after the failed start
        try: server = Server(('127.0.0.1', port), Handler); break
        except OSError:
            if attempt == 39: raise
            time.sleep(0.25)
    print('Asset Lab: http://127.0.0.1:%d' % server.server_port, flush=True)
    print('Asset Lab could not start and is in safe mode: ' + reason, flush=True)
    server.serve_forever(poll_interval=0.3)
    server.server_close()
    if server.retry:
        if os.environ.get('ASSET_LAB_LAUNCHER'): os._exit(75)   # "start me again": the launcher starts it itself
        # Else the same command again, fresh.
        flags = (['-B'] if sys.dont_write_bytecode else []) + (['-X', 'utf8'] if sys.flags.utf8_mode else [])
        subprocess.Popen([sys.executable, *flags, *sys.argv], cwd=os.getcwd(), creationflags=0x08000000 if os.name == 'nt' else 0)
        os._exit(0)
