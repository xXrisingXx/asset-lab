"""One error log for the whole lab: failed requests, background work, the page itself and a failed start.

Every error that is caught somewhere (so it does not stop the lab) is still written here, with its details, to
<data home>/logs/errors.log, so a player can send it and nothing goes unseen. Writing the log never raises: a log that
breaks would be the worst error of all. The file stays small (the older half moves to errors.old.log).
"""
import datetime
import os
import sys
import threading
import traceback
from pathlib import Path

LIMIT = 512 * 1024
_lock = threading.Lock()
_folder = None   # tests point it elsewhere


def folder():
    """<data home>/logs (found as lab_core.data_home does, without importing it: the log must work when nothing else does)."""
    if _folder is not None: return Path(_folder)
    if os.environ.get('ASSET_LAB_HOME'): return Path(os.environ['ASSET_LAB_HOME']) / 'logs'
    older = Path.home() / 'MitosisAssetLab'
    return (older if older.is_dir() else Path.home() / 'AssetLab') / 'logs'


def details(error):
    """An error's full report (its traceback), or the text given."""
    if isinstance(error, BaseException): return ''.join(traceback.format_exception(error)).rstrip()
    return str(error or '').rstrip()


def record(where, error=None, text=''):
    """Write one error to the log: where it happened, a line about it and its details."""
    try:
        with _lock:
            path = folder() / 'errors.log'
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file() and path.stat().st_size > LIMIT: path.replace(path.with_name('errors.old.log'))
            stamp = datetime.datetime.now().isoformat(timespec='seconds')
            line = text or (f'{type(error).__name__}: {error}' if isinstance(error, BaseException) else '')
            with path.open('a', encoding='utf-8') as f:
                f.write(f'[{stamp}] {where}: {line}\n')
                body = details(error)
                if body and body != line: f.write(body + '\n')
                f.write('\n')
    except Exception: pass


def recent(limit=20000):
    """The end of the log, newest last (for a report)."""
    try: return (folder() / 'errors.log').read_text(encoding='utf-8')[-limit:]
    except OSError: return ''


def install():
    """Errors nobody caught (in the lab or in its background work) are written to the log too, then shown as before."""
    shown = sys.excepthook
    def unexpected(kind, value, tb):
        record('unexpected error', value)
        shown(kind, value, tb)
    sys.excepthook = unexpected
    shown_thread = threading.excepthook
    def background(args):
        if args.exc_type is not SystemExit: record(f'background work ({args.thread.name if args.thread else "?"})', args.exc_value)
        shown_thread(args)
    threading.excepthook = background
