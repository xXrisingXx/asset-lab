"""End-to-end browser tests for Asset Lab: an isolated lab server (fake game, temp workspace) and headless Microsoft Edge
driven over the DevTools protocol with real mouse, pen and keyboard input, plus pixel tools. Standard library, numpy and
Pillow only. See README.md beside this file.

    class BrushTests(EditorTestCase):
        def test_stroke(self):
            b = self.browser
            b.select_asset('edit-sprite.png')
            b.click_selector('[data-tool="brush"]')
            before = b.editor_pixels()
            b.drag_image([(8, 10), (20, 10)])
            self.assertTrue((b.editor_pixels() != before).any())
"""
import atexit
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / 'src'
OUTPUT = HERE / 'output'
for _p in (HERE, ROOT / 'tests', SRC):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))
from fakegame import make_editor_game, image_array, EDITOR_IMAGES  # noqa: E402,F401  (re-exported for tests)

KEEP = os.environ.get('ASSET_LAB_TEST_KEEP') == '1'   # keep temp folders, screenshot failures into output/
GPU = os.environ.get('ASSET_LAB_TEST_GPU') == '1'     # use the real graphics card instead of SwiftShader
# Time limits in tests are for a normal PC; GitHub's build machines (CI=true) are several times slower, so speed checks
# and long waits scale by this. ASSET_LAB_TEST_SLOW overrides it.
SLOW = float(os.environ.get('ASSET_LAB_TEST_SLOW') or (3 if os.environ.get('CI') == 'true' else 1))
LIVE_PORT = 8753                                       # the user's own lab: never touched
EDGE_PATHS = (os.environ.get('ASSET_LAB_EDGE'), r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe')
ALT, CTRL, META, SHIFT = 1, 2, 4, 8                    # DevTools modifier bits; combine with |
CHUNK = 6 << 20                                        # bytes of pixels per DevTools message
QUIET = 0x08000000 if os.name == 'nt' else 0           # CREATE_NO_WINDOW
# The editor's feature modules (src/web/editor/<name>.js); EditorTestCase.feature_modules = False serves them empty.
FEATURE_MODULES = ('select', 'paint', 'fill', 'transform', 'adjust', 'filters', 'styles', 'layers', 'color')


class JSError(RuntimeError):
    """A JavaScript exception from Browser.eval; the message is the JavaScript error text with its stack."""


class ProtocolError(RuntimeError):
    """The DevTools protocol refused a command."""


# ----- small helpers ---------------------------------------------------------------------------------------------------
def free_port():
    """A free loopback port that is not the live lab's."""
    while True:
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        if port != LIVE_PORT: return port


def refuse_live(url):
    if urllib.parse.urlsplit(url).port == LIVE_PORT:
        raise RuntimeError('Browser tests never drive the live lab on port 8753. Start an isolated LabServer() instead.')


def remove_tree(path, attempts=8):
    """Delete a folder, retrying while Windows still holds files open (Edge and the server let go a moment after exit)."""
    path = Path(path)
    def unlock(func, p, *_):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError: pass
    for i in range(attempts):
        if not path.exists(): return True
        if sys.version_info >= (3, 12): shutil.rmtree(path, onexc=unlock)
        else: shutil.rmtree(path, onerror=unlock)
        if not path.exists(): return True
        time.sleep(0.15 * (i + 1))
    print(f'[browser tests] could not remove {path} (a file is still in use); delete it later', file=sys.stderr)
    return False


_LIVE, _KERNEL = set(), []


def _kernel():
    """kernel32 with the job-object calls set up, or None off Windows."""
    if os.name != 'nt': return None
    if not _KERNEL:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', wintypes.DWORD),
                        ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('Io', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        info = Extended()
        info.Basic.LimitFlags = 0x2000 | 0x800   # kill everything when the job closes; children may break away (Edge's sandbox)
        _KERNEL.append((k32, ctypes.byref(info), ctypes.sizeof(info)))
    return _KERNEL[0]


def adopt(process):
    """Tie a helper process (Edge, the lab server) and everything it starts to this test run. On Windows it gets a job
    object that closes with this Python process, so even a killed test run leaves nothing behind, and kill_tree ends the
    whole tree at once; elsewhere an exit hook kills it."""
    _LIVE.add(process)
    with contextlib.suppress(OSError, AttributeError, ValueError):
        k = _kernel()
        if k:
            k32, info, size = k
            job = k32.CreateJobObjectW(None, None)
            if job and k32.SetInformationJobObject(job, 9, info, size) and k32.AssignProcessToJobObject(job, int(process._handle)): process.lab_job = job
            elif job: k32.CloseHandle(job)
    return process


@atexit.register
def _kill_leftovers():
    for p in list(_LIVE): kill_tree(p)


def kill_tree(process):
    """Kill a process and everything it started (Edge runs as a tree of processes)."""
    _LIVE.discard(process)
    if process is None: return
    job, process.lab_job = getattr(process, 'lab_job', None), None
    if job:
        k32 = _kernel()[0]
        k32.TerminateJobObject(job, 1)
        k32.CloseHandle(job)
        with contextlib.suppress(subprocess.TimeoutExpired): process.wait(5)   # termination finishes a moment later
    if process.poll() is None:
        if os.name == 'nt': subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True, creationflags=QUIET)
        else: process.kill()
    try: process.wait(10)
    except subprocess.TimeoutExpired: process.kill()


def output_path(name):
    """A path for a test artifact: relative names go into tests/browser/output/ (git-ignored)."""
    p = Path(name)
    if not p.is_absolute(): p = OUTPUT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def find_edge():
    for p in EDGE_PATHS:
        if p and Path(p).is_file(): return p
    if os.environ.get('ASSET_LAB_REQUIRE_BROWSER') == '1':
        raise RuntimeError('Release tests require Microsoft Edge; set ASSET_LAB_EDGE to msedge.exe')
    raise unittest.SkipTest('Microsoft Edge was not found; set ASSET_LAB_EDGE to the path of msedge.exe')


def http_json(url, data=None, headers=None, timeout=10):
    req = urllib.request.Request(url, None if data is None else json.dumps(data).encode(), dict(headers or {}, **({'Content-Type': 'application/json'} if data is not None else {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try: detail = json.loads(e.read().decode('utf-8')).get('error', '')
        except ValueError: detail = ''
        raise RuntimeError(f'{url} answered {e.code}: {detail}') from None


# ----- pixels --------------------------------------------------------------------------------------------------------
def rgba(color):
    """'#rrggbb', '#rrggbbaa' or a tuple -> uint8 array [r, g, b, a]."""
    if isinstance(color, str):
        h = color.lstrip('#')
        color = [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
    c = list(color) + [255] * (4 - len(color))
    return np.array(c[:4], np.uint8)


def read_png(path):
    """A PNG file as uint8 (H, W, 4) RGBA."""
    with Image.open(path) as im: return np.array(im.convert('RGBA'))


def write_png(array, path):
    """Save an (H, W, 4), (H, W, 3) or (H, W) uint8 array; a relative path goes into tests/browser/output/."""
    path = output_path(path)
    Image.fromarray(np.ascontiguousarray(array, np.uint8)).save(path)
    return path


def rgba_b64(array):
    """An (H, W, 4) uint8 array as base64 of its raw RGBA bytes (row by row), the format window.Ed.test.load takes."""
    a = np.ascontiguousarray(array, np.uint8)
    if a.ndim != 3 or a.shape[2] != 4: raise ValueError(f'expected (H, W, 4) RGBA, got {a.shape}')
    return base64.b64encode(a.tobytes()).decode()


def from_b64(data, width, height, channels=4):
    a = np.frombuffer(base64.b64decode(data), np.uint8)
    return a.reshape((height, width, channels) if channels > 1 else (height, width)).copy()


def premultiplied(array):
    """RGB multiplied by alpha (rounded), alpha kept: compares half-transparent pixels the way a canvas stores them."""
    a = np.asarray(array).astype(np.float64)
    out = a.copy()
    out[..., :3] = np.round(a[..., :3] * a[..., 3:4] / 255)
    return out.astype(np.uint8)


def distance_to_path(width, height, points):
    """(H, W) distance from every pixel center (x + 0.5, y + 0.5) to the polyline through points (image coordinates).
    Handy for brush footprints: pixels with distance < radius - 1 are fully painted, > radius + 1 untouched."""
    y, x = np.mgrid[0:height, 0:width].astype(np.float64)
    x, y = x + .5, y + .5
    pts = [tuple(map(float, p)) for p in points]
    if len(pts) == 1: return np.hypot(x - pts[0][0], y - pts[0][1])
    best = np.full((height, width), np.inf)
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        t = np.clip(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy or 1), 0, 1)
        best = np.minimum(best, np.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def _rgba_view(a):
    a = np.asarray(a)
    if a.ndim == 2: a = np.repeat(a[..., None], 3, axis=2)
    if a.shape[2] == 3: a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, a.dtype)], axis=2)
    return np.ascontiguousarray(a.astype(np.uint8))


def save_diff(actual, expected, path):
    """Write  actual | expected | difference  side by side (difference: red, x4 brighter; black where equal) into one PNG.
    Transparent pixels show over a checkerboard; small images are enlarged. Returns the path."""
    a, e = _rgba_view(actual), _rgba_view(expected)
    h, w = max(a.shape[0], e.shape[0]), max(a.shape[1], e.shape[1])
    pad = lambda m: np.pad(m, ((0, h - m.shape[0]), (0, w - m.shape[1]), (0, 0)))
    a, e = pad(a), pad(e)
    d = np.abs(a.astype(np.int16) - e.astype(np.int16)).max(axis=2)
    heat = np.zeros((h, w, 4), np.uint8)
    heat[..., 0] = np.clip(d * 4 + (d > 0) * 60, 0, 255)
    heat[..., 3] = 255
    scale = max(1, 256 // max(h, w))
    check = ((np.indices((h, w)).sum(axis=0) // max(1, 8 // scale)) % 2 * 40 + 160).astype(np.uint8)
    def panel(m):
        bg = Image.fromarray(np.dstack([check] * 3 + [np.full((h, w), 255, np.uint8)]), 'RGBA')
        bg.alpha_composite(Image.fromarray(m, 'RGBA'))
        return bg.resize((w * scale, h * scale), Image.Resampling.NEAREST)
    gap = 8
    out = Image.new('RGBA', (w * scale * 3 + gap * 2, h * scale), (30, 30, 30, 255))
    for i, m in enumerate((a, e, heat)): out.paste(panel(m), (i * (w * scale + gap), 0))
    path = output_path(path)
    out.save(path)
    return path


def psnr(a, b):
    """Peak signal-to-noise ratio in dB over all channels (inf when identical). Above ~40 dB looks identical."""
    mse = np.mean((np.asarray(a).astype(np.float64) - np.asarray(b).astype(np.float64)) ** 2)
    return float('inf') if mse == 0 else float(10 * np.log10(255.0 ** 2 / mse))


def assert_pixels_close(actual, expected, max_abs=0, mean_abs=None, mask=None, msg='', diff_path=None):
    """Fail unless every channel of every compared pixel differs by at most max_abs, and (if given) the mean absolute
    difference is at most mean_abs. mask: optional (H, W) bool array of the pixels to compare. On failure the message
    gives the count, the worst and first differing pixels, and a visual diff written by save_diff."""
    a, e = np.asarray(actual), np.asarray(expected)
    prefix = msg + ': ' if msg else ''
    if a.shape != e.shape: raise AssertionError(f'{prefix}shape {a.shape} differs from expected {e.shape}')
    d = np.abs(a.astype(np.int16) - e.astype(np.int16))
    per = d.max(axis=2) if d.ndim == 3 else d
    if mask is not None: per = np.where(np.asarray(mask, bool), per, 0)
    picked = d[np.asarray(mask, bool)] if mask is not None else d
    worst = int(picked.max()) if picked.size else 0
    mean = float(picked.mean()) if picked.size else 0.0
    if worst <= max_abs and (mean_abs is None or mean <= mean_abs): return
    bad = np.argwhere(per > max_abs)
    where = ''
    if len(bad):
        y, x = bad[0]
        wy, wx = np.unravel_index(int(per.argmax()), per.shape)
        where = (f'; first at (x={x}, y={y}): got {a[y, x].tolist()} expected {e[y, x].tolist()}'
                 f'; worst at (x={wx}, y={wy}): got {a[wy, wx].tolist()} expected {e[wy, wx].tolist()}')
    diff = save_diff(a, e, diff_path or f'diff-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}.png')
    raise AssertionError(f'{prefix}{len(bad)} pixel(s) differ by more than {max_abs} (worst {worst}, mean {mean:.3f}'
                         f'{"" if mean_abs is None else f" vs allowed {mean_abs}"}){where}. Visual diff: {diff}')


# ----- the isolated lab server ---------------------------------------------------------------------------------------
class LabServer:
    """src/lab_server.py on a free port against a fake game, with its own workspace, ledgers, ASSET_LAB_HOME and an empty
    ASSET_LAB_RUNTIME (so no AI runtime starts). Everything lives in one temp folder that stop() deletes."""

    def __init__(self, maker=make_editor_game, keep=KEEP, timeout=60):
        self.keep, self.process = keep, None
        self.temp = Path(tempfile.mkdtemp(prefix='assetlab-e2e-'))
        self.home, self.runtime = self.temp / 'home', self.temp / 'runtime'
        self.workspace, self.ledgers, self.log = self.home / 'workspace', self.home / 'install-ledgers', self.temp / 'server.log'
        self.home.mkdir()
        self.runtime.mkdir()
        try:
            self.game = Path(maker(self.temp / 'game')).resolve()
            self._start(timeout)
        except BaseException:
            self.stop()
            raise

    def _start(self, timeout):
        env = dict(os.environ, ASSET_LAB_HOME=str(self.home), ASSET_LAB_RUNTIME=str(self.runtime), PYTHONDONTWRITEBYTECODE='1')
        env.pop('ASSET_LAB_LAUNCHER', None)
        deadline = time.monotonic() + timeout
        while True:
            self.port = free_port()
            self.url = f'http://127.0.0.1:{self.port}'
            with open(self.log, 'ab') as log:
                self.process = adopt(subprocess.Popen([sys.executable, '-B', '-X', 'utf8', str(SRC / 'lab_server.py'), '--game', str(self.game),
                                                       '--workspace', str(self.workspace), '--ledger-dir', str(self.ledgers), '--port', str(self.port),
                                                       '--no-browser'], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=log,
                                                      stderr=subprocess.STDOUT, creationflags=QUIET))
            while time.monotonic() < deadline and self.process.poll() is None:
                try:
                    if self.get('/api/health', timeout=2).get('app') == 'Asset Lab':
                        # Make sure this port is ours: the server must be serving the fake game in our temp folder.
                        game = self.get('/api/status', timeout=max(2.0, deadline - time.monotonic()))['game']
                        if Path(game).resolve() != self.game: raise RuntimeError(f'Port {self.port} serves another game: {game}')
                        return
                except (OSError, ValueError): pass
                time.sleep(0.1)
            exited, text = self.process.poll(), self.log.read_text('utf-8', 'replace')
            kill_tree(self.process)
            # Only a port taken between choosing and binding it is worth another try; anything else is in the log.
            if exited is None or time.monotonic() >= deadline or not any(s in text for s in ('10048', 'already in use', 'Only one usage')):
                raise RuntimeError(f'The isolated lab server did not start ({"it stopped with code " + str(exited) if exited is not None else f"no answer within {timeout} s"}). '
                                   'Its log (a half-saved edit in src/ can cause this too):\n' + text[-3000:])

    def get(self, path, timeout=10, **query):
        """GET JSON from this server, e.g. get('/api/assets', q='edit', kind='visual')."""
        return http_json(self.url + path + ('?' + urllib.parse.urlencode(query) if query else ''), timeout=timeout)

    def post(self, path, data=None, timeout=60):
        """POST JSON with the session token (allowed: this server only touches its fake game and temp workspace)."""
        return http_json(self.url + path, data or {}, {'X-Lab-Token': self.get('/api/status')['token']}, timeout)

    def assets(self, q='', **params):
        """Catalog rows from /api/assets (params as the page sends them: group, kind, all='1', ...)."""
        return self.get('/api/assets', q=q, **params)['items']

    def asset_key(self, name):
        """The catalog key of the asset named name (the loose file in assets/ when several copies exist)."""
        rows = [r for r in self.assets(name, kind='visual', all='1') if r['name'] == name] or [r for r in self.assets(name, all='1') if r['name'] == name]
        if not rows: raise LookupError(f'No asset named {name!r} in the fake game')
        rows.sort(key=lambda r: r['source'] != 'assets/' + name)
        return rows[0]['key']

    def asset_path(self, name):
        return self.game / 'assets' / name

    def stop(self):
        kill_tree(self.process)
        if self.keep: print(f'[browser tests] kept lab files in {self.temp}', file=sys.stderr)
        else: remove_tree(self.temp)

    def __enter__(self): return self

    def __exit__(self, *exc): self.stop()


# ----- DevTools websocket --------------------------------------------------------------------------------------------
class DevToolsSocket:
    """Just enough RFC 6455 for the DevTools protocol: masked text frames out, (fragmented) frames in, with timeouts."""

    def __init__(self, url, timeout=30):
        host, path = url[len('ws://'):].split('/', 1)
        h, p = host.rsplit(':', 1)
        self.sock = socket.create_connection((h, int(p)), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(f'GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                          f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
        self.buf, self.parts = bytearray(), []
        while b'\r\n\r\n' not in self.buf: self._more(timeout)
        head, _, rest = bytes(self.buf).partition(b'\r\n\r\n')
        if b' 101 ' not in head.split(b'\r\n')[0]: raise ConnectionError(head[:200].decode('latin-1'))
        self.buf = bytearray(rest)

    def _more(self, timeout):
        self.sock.settimeout(timeout)
        chunk = self.sock.recv(1 << 20)
        if not chunk: raise ConnectionError('Edge closed the DevTools connection')
        self.buf += chunk

    def send(self, text, opcode=0x1):
        data = text.encode('utf-8') if isinstance(text, str) else text
        n, mask = len(data), os.urandom(4)
        size = bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, 'big') if n < 65536 else bytes([0x80 | 127]) + n.to_bytes(8, 'big')
        masked = (int.from_bytes(data, 'little') ^ int.from_bytes((mask * (n // 4 + 1))[:n], 'little')).to_bytes(n, 'little') if n else b''
        self.sock.settimeout(60)
        self.sock.sendall(bytes([0x80 | opcode]) + size + mask + masked)

    def _frame(self):
        b = self.buf
        if len(b) < 2: return None
        n, i = b[1] & 0x7f, 2
        if n == 126:
            if len(b) < 4: return None
            n, i = int.from_bytes(b[2:4], 'big'), 4
        elif n == 127:
            if len(b) < 10: return None
            n, i = int.from_bytes(b[2:10], 'big'), 10
        if len(b) < i + n: return None
        fin, op, payload = b[0] & 0x80, b[0] & 0x0f, bytes(b[i:i + n])
        del b[:i + n]
        return fin, op, payload

    def recv(self, timeout=None):
        """The next whole text message, or None when none completes within timeout seconds (0: only what already arrived)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            frame = self._frame()
            if frame:
                fin, op, payload = frame
                if op == 0x8: raise ConnectionError('Edge closed the DevTools connection')
                if op == 0x9: self.send(payload, 0xA)
                if op in (0x0, 0x1, 0x2):
                    self.parts.append(payload)
                    if fin:
                        data, self.parts = b''.join(self.parts), []
                        return data.decode('utf-8')
                continue
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            try: self._more(wait)
            except (socket.timeout, BlockingIOError): return None

    def close(self):
        with contextlib.suppress(OSError): self.sock.close()


# ----- the page-side helper (installed in every document before the page's own scripts) ----------------------------
HELPER_JS = r"""(()=>{'use strict';if(window.__labTest)return;
const store=new Map(),images={};let next=1;const INLINE=%d;
const b64=u8=>{if(u8.toBase64)return u8.toBase64();let s='';for(let i=0;i<u8.length;i+=0x8000)s+=String.fromCharCode.apply(null,u8.subarray(i,i+0x8000));return btoa(s);};
const unb64=s=>{if(Uint8Array.fromBase64)return Uint8Array.fromBase64(s);const t=atob(s),u=new Uint8Array(t.length);for(let i=0;i<t.length;i++)u[i]=t.charCodeAt(i);return u;};
const bytes=d=>d instanceof Uint8Array?d:ArrayBuffer.isView(d)?new Uint8Array(d.buffer,d.byteOffset,d.byteLength):typeof d==='string'?unb64(d):Uint8Array.from(d);
const is=(v,name)=>typeof window[name]==='function'&&v instanceof window[name];
function crop(data,W,H,c,region){if(!region)return{w:W,h:H,data};const[x,y,w,h]=region;if(x<0||y<0||x+w>W||y+h>H)throw RangeError(`Region ${region} is outside ${W} x ${H}`);
  const out=new Uint8Array(w*h*c);for(let r=0;r<h;r++)out.set(data.subarray(((y+r)*W+x)*c,((y+r)*W+x+w)*c),r*w*c);return{w,h,data:out};}
function read(v,region){
  if(is(v,'ImageData'))return crop(bytes(v.data),v.width,v.height,4,region);
  if(['HTMLCanvasElement','OffscreenCanvas','ImageBitmap','HTMLImageElement','HTMLVideoElement','SVGImageElement','VideoFrame'].some(n=>is(v,n))){
    const W=v.naturalWidth||v.videoWidth||v.displayWidth||v.width,H=v.naturalHeight||v.videoHeight||v.displayHeight||v.height;const[x,y,w,h]=region||[0,0,W,H];
    // Copied into a canvas made for reading: getImageData on the page's own canvas would move it to software rendering.
    const c=new OffscreenCanvas(w,h),cg=c.getContext('2d',{willReadFrequently:true});cg.drawImage(v,x,y,w,h,0,0,w,h);return{w,h,data:bytes(cg.getImageData(0,0,w,h).data)};}
  if(v&&typeof v==='object'&&'width' in v&&'height' in v&&'data' in v){const d=bytes(v.data);return crop(d,v.width,v.height,d.length/(v.width*v.height),region);}
  throw TypeError('Cannot read pixels from '+Object.prototype.toString.call(v)+'; give a canvas, ImageData, image, ImageBitmap or {width, height, data}');}
window.__labTest={
  async grab(v,region){v=await v;const r=read(v,region||null),c=r.data.length/(r.w*r.h);if(c!==1&&c!==4)throw Error(`${r.data.length} bytes do not fit ${r.w} x ${r.h} pixels`);
    const out={w:r.w,h:r.h,c,n:r.data.length};if(r.data.length<=INLINE)out.b64=b64(r.data);else{out.id=next++;store.set(out.id,r.data);}return out;},
  chunk:(id,s,e)=>b64(store.get(id).subarray(s,e)),free:id=>{store.delete(id);images[id]=undefined;return true;},
  put(id,text){if(!store.has(id))store.set(id,[]);store.get(id).push(text);return true;},
  take(id){const t=(store.get(id)||[]).join('');store.delete(id);return t;},
  image(id,w,h){const d=unb64(this.take(id));images[id]=new ImageData(new Uint8ClampedArray(d.buffer,d.byteOffset,d.byteLength),w,h);return id;},
  images,b64,unb64,
  toClient(points){const v=window.Ed&&window.Ed.view;if(v&&typeof v.toClient==='function')return points.map(([x,y])=>{const p=v.toClient(x,y);return Array.isArray(p)?p:[p.x,p.y];});
    const r=document.getElementById('view').getBoundingClientRect(),t=transform;return points.map(([x,y])=>[r.left+t.x+x*t.scale,r.top+t.y+y*t.scale]);},
  toImage(points){const v=window.Ed&&window.Ed.view;if(v&&typeof v.toImage==='function')return points.map(([x,y])=>{const p=v.toImage(x,y);return Array.isArray(p)?p:[p.x,p.y];});
    const r=document.getElementById('view').getBoundingClientRect(),t=transform;return points.map(([x,y])=>[(x-r.left-t.x)/t.scale,(y-r.top-t.y)/t.scale]);},
  frames(n=2){return new Promise(done=>{const t=setTimeout(()=>done(false),2000);const step=()=>{if(--n<=0){clearTimeout(t);done(true);}else requestAnimationFrame(step);};requestAnimationFrame(step);});},
};})();""" % CHUNK

# ----- keyboard ------------------------------------------------------------------------------------------------------
# key -> (code, windowsVirtualKeyCode, shifted key or None, text when typed). US layout, as Windows reports it.
_KEYS = {'Enter': ('Enter', 13, None, '\r'), 'Tab': ('Tab', 9, None, ''), 'Escape': ('Escape', 27, None, ''), 'Backspace': ('Backspace', 8, None, ''),
         'Delete': ('Delete', 46, None, ''), ' ': ('Space', 32, None, ' '), 'ArrowLeft': ('ArrowLeft', 37, None, ''), 'ArrowUp': ('ArrowUp', 38, None, ''),
         'ArrowRight': ('ArrowRight', 39, None, ''), 'ArrowDown': ('ArrowDown', 40, None, ''), 'Home': ('Home', 36, None, ''), 'End': ('End', 35, None, ''),
         'PageUp': ('PageUp', 33, None, ''), 'PageDown': ('PageDown', 34, None, ''), 'Insert': ('Insert', 45, None, ''), 'CapsLock': ('CapsLock', 20, None, ''),
         'Shift': ('ShiftLeft', 16, None, ''), 'Control': ('ControlLeft', 17, None, ''), 'Alt': ('AltLeft', 18, None, ''), 'Meta': ('MetaLeft', 91, None, '')}
_KEYS.update({f'F{i}': (f'F{i}', 111 + i, None, '') for i in range(1, 13)})
_KEYS.update({c: ('Key' + c.upper(), ord(c.upper()), c.upper(), c) for c in 'abcdefghijklmnopqrstuvwxyz'})
_KEYS.update({d: ('Digit' + d, ord(d), s, d) for d, s in zip('0123456789', ')!@#$%^&*(')})
_KEYS.update({k: (code, vk, s, k) for k, code, vk, s in (('[', 'BracketLeft', 219, '{'), (']', 'BracketRight', 221, '}'), ('-', 'Minus', 189, '_'),
              ('=', 'Equal', 187, '+'), (',', 'Comma', 188, '<'), ('.', 'Period', 190, '>'), ('/', 'Slash', 191, '?'), (';', 'Semicolon', 186, ':'),
              ("'", 'Quote', 222, '"'), ('\\', 'Backslash', 220, '|'), ('`', 'Backquote', 192, '~'))})
_SHIFTED = {v[2]: k for k, v in _KEYS.items() if v[2] and len(k) == 1}   # 'A' -> 'a', '{' -> '['
_ALIASES = {'space': ' ', 'spacebar': ' ', 'esc': 'Escape', 'return': 'Enter', 'del': 'Delete', 'left': 'ArrowLeft', 'right': 'ArrowRight',
            'up': 'ArrowUp', 'down': 'ArrowDown', 'ctrl': 'Control', 'control': 'Control', 'shift': 'Shift', 'alt': 'Alt', 'option': 'Alt',
            'meta': 'Meta', 'cmd': 'Meta', 'command': 'Meta', 'win': 'Meta', 'pgup': 'PageUp', 'pgdn': 'PageDown', 'ins': 'Insert'}
_ALIASES.update({k.lower(): k for k in _KEYS if len(k) > 1})
_MODIFIER_BITS = {'Alt': ALT, 'Control': CTRL, 'Meta': META, 'Shift': SHIFT}
_MOUSE_BUTTONS = {'left': 1, 'right': 2, 'middle': 4, 'back': 8, 'forward': 16}


def _key_name(name):
    if name in _KEYS or name in _SHIFTED: return name
    if name.lower() in _ALIASES: return _ALIASES[name.lower()]
    raise ValueError(f'Unknown key {name!r}; use a character, a key name like "Enter", "ArrowLeft", "F2", or a combo like "Ctrl+Shift+Z"')


def parse_keys(spec, modifiers=0):
    """'Ctrl+Shift+Z' -> ('z', CTRL | SHIFT). A letter after modifiers names the key (as in menus), so Shift only when listed;
    on its own an upper-case letter or shifted symbol ('A', '{', '?') means Shift + that key, as when typing it."""
    parts = [spec] if len(spec) == 1 else spec[:-2].split('+') + ['+'] if spec.endswith('++') else spec.split('+')
    *mods, name = parts
    for m in mods:
        bit = _MODIFIER_BITS.get(_key_name(m.strip()))
        if not bit: raise ValueError(f'{m!r} in {spec!r} is not a modifier (Ctrl, Shift, Alt, Meta)')
        modifiers |= bit
    name = _key_name(name)
    if name in _SHIFTED and not _KEYS.get(name):
        if mods and name.isalpha(): name = _SHIFTED[name]
        else: name, modifiers = _SHIFTED[name], modifiers | SHIFT
    return name, modifiers


# ----- the browser ---------------------------------------------------------------------------------------------------
class Browser:
    """Headless Edge with one page, driven over the DevTools protocol. See README.md for the full API."""

    def __init__(self, width=1600, height=1000, keep=KEEP, gpu=GPU, timeout=30):
        self.keep, self.process, self.ws, self.lab_url = keep, None, None, None
        self.errors, self.dialogs, self.canceled, self.seq = [], [], [], 0
        self.accept_dialogs = False   # alert/confirm/prompt: dismissed unless True (beforeunload is always accepted)
        self._id, self._urls, self._buttons, self._held, self._down, self._pos = 0, {}, 0, 0, set(), (0.0, 0.0)
        edge = find_edge()
        self.profile = Path(tempfile.mkdtemp(prefix='assetlab-edge-'))
        self.startup_log = tempfile.TemporaryFile()
        args = [edge, '--headless=new', '--remote-debugging-port=0', f'--user-data-dir={self.profile}', '--no-first-run',
                '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars', '--force-device-scale-factor=1',
                f'--window-size={width},{height}', '--disable-background-networking', '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding', '--disable-component-update',
                '--disable-default-apps', '--disable-sync', '--mute-audio', '--disable-features=Translate,msEdgeTranslate,AutofillServerCommunication']
        if not gpu: args += ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']  # the same pixels on every PC
        try:
            self.process = adopt(subprocess.Popen(args + ['--enable-logging=stderr', 'about:blank'], stdin=subprocess.DEVNULL,
                                                  stdout=self.startup_log, stderr=self.startup_log, creationflags=QUIET))
            self.port = self._devtools_port(timeout)
            pages = [t for t in http_json(f'http://127.0.0.1:{self.port}/json/list') if t.get('type') == 'page']
            self.ws = DevToolsSocket(pages[0]['webSocketDebuggerUrl'], timeout)
            for domain in ('Page', 'Runtime', 'Log', 'Network', 'Inspector'): self.call(domain + '.enable')
            self.call('Page.addScriptToEvaluateOnNewDocument', source=HELPER_JS)
            # The first-start tour would cover the page; tests that want it call LabTour.start() or clear this mark.
            self.call('Page.addScriptToEvaluateOnNewDocument', source="try{if(location.port&&!sessionStorage.getItem('tour-test'))localStorage.setItem('lab-tour-done','1')}catch(e){}")
            self.call('Runtime.evaluate', expression=HELPER_JS)
            self.call('Emulation.setFocusEmulationEnabled', enabled=True)
            # Hosted Windows runners may disable OS animations. Tests explicitly opt into reduced motion.
            self.call('Emulation.setEmulatedMedia', features=[{'name':'prefers-reduced-motion','value':'no-preference'}])
            self.set_viewport(width, height)
        except BaseException:
            self.close()
            raise

    def _devtools_port(self, timeout):
        """Edge picks a free port itself (--remote-debugging-port=0) and writes it into DevToolsActivePort."""
        marker, deadline = self.profile / 'DevToolsActivePort', time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Edge's update/launcher process can exit successfully before its child
            # browser writes DevToolsActivePort. Only a nonzero exit is a failure.
            if self.process.poll() not in (None, 0):
                raise RuntimeError(f'Edge exited at start (code {self.process.returncode}). {self.startup_diagnostics()}')
            with contextlib.suppress(OSError, ValueError, IndexError):
                port = int(marker.read_text().split()[0])
                http_json(f'http://127.0.0.1:{port}/json/version', timeout=2)
                return port
            time.sleep(0.05)
        raise RuntimeError('Edge did not open its DevTools port in time. ' + self.startup_diagnostics())

    def startup_diagnostics(self):
        self.startup_log.seek(0)
        text = self.startup_log.read().decode('utf-8', 'replace')[-8000:]
        path = output_path('edge-startup.log')
        path.write_text(text or 'Edge produced no startup diagnostics.', encoding='utf-8')
        return f'Log: {path}\n{text}'

    # ----- protocol ------------------------------------------------------------------------------------------------
    def call(self, method, _timeout=60, **params):
        """Send one DevTools command and return its result; events that arrive meanwhile are handled (errors, dialogs)."""
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))
        deadline = time.monotonic() + _timeout
        while True:
            text = self.ws.recv(max(0.0, deadline - time.monotonic()))
            if text is None: raise TimeoutError(f'{method} got no answer within {_timeout} s')
            msg = json.loads(text)
            if 'method' in msg: self._event(msg['method'], msg.get('params', {}))
            elif msg.get('id') == mid:
                if 'error' in msg: raise ProtocolError(f"{method}: {msg['error'].get('message')} {msg['error'].get('data', '')}".strip())
                return msg.get('result', {})

    def _send(self, method, **params):
        self._id += 1
        self.ws.send(json.dumps({'id': self._id, 'method': method, 'params': params}))

    def pump(self):
        """Handle the events that already arrived (console errors, failed requests) without sending anything."""
        while True:
            text = self.ws.recv(0)
            if text is None: return
            msg = json.loads(text)
            if 'method' in msg: self._event(msg['method'], msg.get('params', {}))

    def _error(self, kind, text, url='', line=None, ident=None):
        self.seq += 1
        self.errors.append(dict(kind=kind, text=text, url=url or '', line=line, time=time.time(), id=ident, seq=self.seq))

    def errors_since(self, seq):
        """Errors recorded after browser.seq had the value seq (see EditorTestCase.assertNoBrowserErrors)."""
        self.pump()
        return [e for e in self.errors if e['seq'] > seq]

    def _event(self, method, p):
        if method == 'Runtime.consoleAPICalled' and p.get('type') in ('error', 'assert'):
            text = ' '.join(str(a.get('value', a.get('description', a.get('unserializableValue', a.get('type', ''))))) for a in p.get('args', []))
            frame = ((p.get('stackTrace') or {}).get('callFrames') or [{}])[0]
            self._error('console', text, frame.get('url'), frame.get('lineNumber'))
        elif method == 'Runtime.exceptionThrown':
            d = p.get('exceptionDetails', {})
            self._error('exception', (d.get('exception') or {}).get('description') or d.get('text', ''), d.get('url'), d.get('lineNumber'), d.get('exceptionId'))
        elif method == 'Runtime.exceptionRevoked':   # a promise rejection reported as unhandled got its handler after all
            self.errors[:] = [e for e in self.errors if not (e['kind'] == 'exception' and e['id'] == p.get('exceptionId'))]
        elif method == 'Log.entryAdded' and p.get('entry', {}).get('level') == 'error':
            e = p['entry']
            self._error('log', e.get('text', ''), e.get('url'), e.get('lineNumber'))
        elif method == 'Network.requestWillBeSent': self._urls[p['requestId']] = p['request']['url']
        elif method == 'Network.loadingFinished': self._urls.pop(p.get('requestId'), None)
        elif method == 'Network.loadingFailed':
            url = self._urls.pop(p.get('requestId'), '')
            if p.get('canceled'): self.canceled.append(url)
            else: self._error('network', p.get('errorText', 'failed') + (f" ({p['blockedReason']})" if p.get('blockedReason') else ''), url)
        elif method == 'Inspector.targetCrashed': self._error('crash', 'The page crashed (renderer process died)')
        elif method == 'Page.javascriptDialogOpening':
            accept = p.get('type') == 'beforeunload' or self.accept_dialogs
            self.dialogs.append(dict(type=p.get('type'), message=p.get('message', ''), accepted=accept))
            self._send('Page.handleJavaScriptDialog', accept=accept)

        elif method == 'Fetch.requestPaused':
            body = base64.b64encode(b"'use strict';// stubbed by the test harness: the editor core is tested alone\n").decode()
            self._send('Fetch.fulfillRequest', requestId=p['requestId'], responseCode=200, body=body,
                       responseHeaders=[{'name': 'Content-Type', 'value': 'text/javascript; charset=utf-8'}, {'name': 'Cache-Control', 'value': 'no-store'}])

    def stub_editor_modules(self, names=FEATURE_MODULES):
        """Serve editor/<name>.js of the feature modules as empty scripts from now on (reloads included), so a test of the
        editor core sees the engine and shell alone, as they were written to work without any module."""
        self.call('Fetch.enable', patterns=[{'urlPattern': f'*/editor/{n}.js', 'requestStage': 'Request'} for n in names])

    def clear_errors(self):
        self.pump()
        self.errors.clear()

    def error_report(self, errors=None):
        return '\n'.join(f"  [{e['kind']}] {e['text']}" + (f" ({e['url']}:{e['line']})" if e['url'] else '') for e in (self.errors if errors is None else errors))

    # ----- JavaScript ----------------------------------------------------------------------------------------------
    def _value(self, r, what):
        if r.get('exceptionDetails'):
            d = r['exceptionDetails']
            raise JSError(((d.get('exception') or {}).get('description') or d.get('text') or 'JavaScript error') + f'\n  while running: {what[:300]}')
        res = r.get('result', {})
        if res.get('type') == 'undefined': return None
        if 'unserializableValue' in res: return float(res['unserializableValue']) if res['unserializableValue'] != '-0' else -0.0
        return res.get('value')

    def eval(self, expression, timeout=30):
        """Run JavaScript in the page like the DevTools console: statements are fine (their declarations stay local to this
        call), top-level await works, and the value of the last expression comes back as JSON; a promise is awaited.
        Raises JSError with the JavaScript error text."""
        deadline = time.monotonic() + timeout
        # REPL mode gives statements, await and the completion value; the block keeps const/let from leaking into the page.
        r = self.call('Runtime.evaluate', _timeout=timeout, expression='{\n' + expression + '\n}', replMode=True, awaitPromise=True, objectGroup='labtest')
        res = r.get('result', {})
        if r.get('exceptionDetails') or 'objectId' not in res: return self._value(r, expression)
        try:
            left = max(1.0, deadline - time.monotonic())
            if res.get('subtype') == 'promise':
                return self._value(self.call('Runtime.awaitPromise', _timeout=left, promiseObjectId=res['objectId'], returnByValue=True), expression)
            try: return self._value(self.call('Runtime.callFunctionOn', _timeout=left, functionDeclaration='function(){return this}', objectId=res['objectId'], returnByValue=True), expression)
            except ProtocolError: return res.get('description')   # not serializable (cycles): its description
        finally: self._send('Runtime.releaseObjectGroup', objectGroup='labtest')

    def _js(self, expression, timeout=60):
        """Evaluate one expression (no REPL wrapping); used by the harness itself."""
        return self._value(self.call('Runtime.evaluate', _timeout=timeout, expression=expression, awaitPromise=True, returnByValue=True), expression)

    def wait_for(self, condition, timeout=10, interval=0.025, message=''):
        """Poll a JavaScript condition until it is truthy and return its value. Errors while polling (S.asset is still null...)
        count as not yet; on timeout the last one is in the message. The timeout scales with SLOW (GitHub's slower machines)."""
        timeout *= SLOW
        deadline, last = time.monotonic() + timeout, None
        while True:
            try:
                value = self.eval(condition, timeout=max(1.0, deadline - time.monotonic()))
                if value: return value
                last = None
            except (JSError, ProtocolError) as e: last = e
            if time.monotonic() > deadline:
                raise TimeoutError(f'Waited {timeout} s for: {condition}' + (f'\n  last error: {last}' if last else '') + (f'\n  {message}' if message else ''))
            time.sleep(interval)

    def settle(self, frames=2):
        """Wait for the page to draw frames animation frames (the editor renders in requestAnimationFrame)."""
        return self._js(f'window.__labTest ? __labTest.frames({int(frames)}) : true', timeout=10)

    # ----- pages ---------------------------------------------------------------------------------------------------
    def set_viewport(self, width, height):
        """Resize the page (CSS pixels, device scale 1)."""
        self.call('Emulation.setDeviceMetricsOverride', width=int(width), height=int(height), deviceScaleFactor=1, mobile=False)
        self.viewport = (int(width), int(height))
        with contextlib.suppress(JSError, ProtocolError, TimeoutError): self.settle(3)   # layout, ResizeObserver, then the redraw

    def goto(self, url, timeout=30):
        """Load a URL and wait for its load event."""
        refuse_live(url)
        def start():
            failed = self.call('Page.navigate', url=url).get('errorText')
            if failed: raise RuntimeError(f'Could not load {url}: {failed}')
        self._navigate(start, timeout)

    def _navigate(self, start, timeout):
        with contextlib.suppress(JSError, ProtocolError): self._js('window.__labOldPage = true')
        start()
        self.wait_for("!window.__labOldPage && document.readyState === 'complete'", timeout)

    def open_lab(self, url=None, timeout=30):
        """Load the lab (a LabServer, its URL, or the last one opened) and wait until the asset list has loaded."""
        url = getattr(url, 'url', url) or self.lab_url
        if not url: raise ValueError('open_lab needs a LabServer or its URL')
        self.lab_url = url.rstrip('/') + '/'
        self.goto(self.lab_url, timeout)
        self.wait_lab(timeout)

    def wait_lab(self, timeout=30):
        self.wait_for("typeof S === 'object' && !!S.token && !S.busy && document.querySelectorAll('#assetList .asset-card').length > 0", timeout,
                      message='The lab page did not finish loading its asset list.')
        self._js('document.fonts.ready.then(() => true)')
        self.settle()

    def reload(self, timeout=30):
        """Reload the page (a beforeunload prompt is accepted) and, on the lab, wait for its asset list again."""
        lab = bool(self.lab_url) and self._js('location.href').startswith(self.lab_url)
        self._navigate(lambda: self.call('Page.reload', ignoreCache=True), timeout)
        if lab: self.wait_lab(timeout)

    def select_asset(self, name, discard=True, timeout=20):
        """Open the asset named name (e.g. 'edit-sprite.png') in the editor like a click on its card, and wait until it is
        drawn. discard=True drops an unsaved draft without asking. With discard=False app.js first asks "Leave this draft?"
        when there are unsaved changes: then this returns at once with that dialog open, for the test to answer.
        Returns the asset key."""
        q = json.dumps(name)
        key = self.eval(f"const rows=(await (await fetch('/api/assets?'+new URLSearchParams({{q:{q},kind:'visual',all:'1'}}))).json()).items.filter(r=>r.name==={q});"
                        f"rows.sort((a,b)=>(b.source==='assets/'+{q})-(a.source==='assets/'+{q}));rows.length?rows[0].key:null")
        if not key: raise LookupError(f'No editable asset named {name!r} in this lab')
        k = json.dumps(key)
        if discard: self.eval(f'await select({k}, true); true', timeout)
        else:
            self._js(f'void select({k}, false)')
            if self.wait_for(f"S.asset && S.asset.key === {k} ? 'open' : document.getElementById('dialog').open ? 'asked' : ''", timeout) == 'asked': return key
        self.wait_for(f"S.asset && S.asset.key === {k}", timeout)
        self.eval("if (window.Ed && Ed.test && typeof Ed.test.ready === 'function') await Ed.test.ready(); true", timeout)
        self.settle()
        return key

    # ----- coordinates ---------------------------------------------------------------------------------------------
    def image_points_to_client(self, points):
        """Image pixel coordinates -> page (client CSS pixel) coordinates, through window.Ed.view.toClient when the editor
        core defines it, else today's app.js mapping (transform and the #view canvas)."""
        return [tuple(p) for p in self._js(f'__labTest.toClient({json.dumps([[float(x), float(y)] for x, y in points])})')]

    def image_to_client(self, x, y):
        return self.image_points_to_client([(x, y)])[0]

    def pixel_center(self, x, y):
        """Page coordinates of the center of image pixel (x, y)."""
        return self.image_to_client(x + .5, y + .5)

    def client_to_image(self, x, y):
        """Page coordinates -> image coordinates (window.Ed.view.toImage, else today's mapping)."""
        return tuple(self._js(f'__labTest.toImage([[{float(x)}, {float(y)}]])')[0])

    # ----- mouse and pen -------------------------------------------------------------------------------------------
    def _mouse(self, kind, x, y, button, modifiers, clicks, pointer, pressure, tilt, wait=True):
        if pointer not in ('mouse', 'pen'): raise ValueError("pointer is 'mouse' or 'pen'")
        down = self._buttons != 0
        p = dict(type=kind, x=float(x), y=float(y), modifiers=modifiers | self._held, button=button, buttons=self._buttons,
                 clickCount=clicks, pointerType=pointer)
        # A pressed mouse reports pressure 0.5 like real hardware; a pen reports its own pressure; nothing pressed is 0.
        p['force'] = (0.5 if pressure is None or pointer == 'mouse' else float(pressure)) if down else 0.0
        if tilt: p['tiltX'], p['tiltY'] = tilt
        (self.call if wait else self._send)('Input.dispatchMouseEvent', **p)
        self._pos = (float(x), float(y))

    def _pressed(self):
        return next((b for b, bit in _MOUSE_BUTTONS.items() if self._buttons & bit), 'none')

    def mouse_move(self, x, y, modifiers=0, pointer='mouse', pressure=None, tilt=None):
        """Move to (x, y) in page coordinates; buttons already down stay down (their mask goes with the move)."""
        self._mouse('mouseMoved', x, y, self._pressed(), modifiers, 0, pointer, pressure, tilt)

    def mouse_down(self, x, y, button='left', modifiers=0, click_count=1, pointer='mouse', pressure=None, tilt=None):
        self._buttons |= _MOUSE_BUTTONS[button]
        self._mouse('mousePressed', x, y, button, modifiers, click_count, pointer, pressure, tilt)

    def mouse_up(self, x, y, button='left', modifiers=0, click_count=1, pointer='mouse', pressure=None, tilt=None):
        self._buttons &= ~_MOUSE_BUTTONS[button]
        self._mouse('mouseReleased', x, y, button, modifiers, click_count, pointer, pressure, tilt)

    def click(self, x, y, button='left', modifiers=0, count=1):
        """Move there and press/release count times (count=2 is a double-click)."""
        self.mouse_move(x, y, modifiers)
        for i in range(1, count + 1):
            self.mouse_down(x, y, button, modifiers, i)
            self.mouse_up(x, y, button, modifiers, i)

    def double_click(self, x, y, button='left', modifiers=0):
        self.click(x, y, button, modifiers, 2)

    def wheel(self, x, y, delta_y, delta_x=0, modifiers=0):
        """Scroll the wheel over (x, y); positive delta_y scrolls down (zooms out in the editor)."""
        self.call('Input.dispatchMouseEvent', type='mouseWheel', x=float(x), y=float(y), deltaX=float(delta_x), deltaY=float(delta_y),
                  modifiers=modifiers | self._held, pointerType='mouse')

    def drag(self, points, button='left', modifiers=0, steps_between=4, pointer='mouse', pressures=None, tilt=None, burst=False):
        """Press at points[0], move through every point and release at the last one (page coordinates). steps_between extra
        moves are sent along each segment, with the buttons mask held down. pointer='pen' sends a pen stroke: pressures is
        one value or one per point (0..1, interpolated along the way; default 0.5). burst=True sends the moves without waiting
        for each one, like a fast tablet: Chrome then merges several into one pointermove (see getCoalescedEvents())."""
        pts = [(float(x), float(y)) for x, y in points]
        if not pts: raise ValueError('drag needs at least one point')
        if pressures is not None and pointer != 'pen': raise ValueError("pressures need pointer='pen'")
        pres = [0.5] * len(pts) if pressures is None else [float(pressures)] * len(pts) if np.isscalar(pressures) else [float(p) for p in pressures]
        if len(pres) != len(pts): raise ValueError('give one pressure per point')
        self.mouse_move(*pts[0], modifiers, pointer, tilt=tilt)   # arrive with nothing pressed, as a hand or hovering pen does
        self.mouse_down(*pts[0], button, modifiers, 1, pointer, pres[0], tilt)
        try:
            for (a, pa), (b, pb) in zip(zip(pts, pres), zip(pts[1:], pres[1:])):
                for i in range(1, steps_between + 2):
                    t = i / (steps_between + 1)
                    self._mouse('mouseMoved', a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, button, modifiers, 0, pointer,
                                pa + (pb - pa) * t, tilt, wait=not burst)
        finally:
            self.mouse_up(*pts[-1], button, modifiers, 1, pointer, pres[-1], tilt)

    def drag_image(self, points, center=True, **kwargs):
        """drag() with points in image pixel coordinates; center=True aims at pixel centers (x + 0.5, y + 0.5)."""
        off = .5 if center else 0.0
        return self.drag(self.image_points_to_client([(x + off, y + off) for x, y in points]), **kwargs)

    # ----- keyboard ------------------------------------------------------------------------------------------------
    def _key(self, kind, name, modifiers, repeat=False):
        code, vk, shifted, text = _KEYS[name]
        key = shifted if modifiers & SHIFT and shifted else name
        if modifiers & (CTRL | ALT | META) or kind == 'keyUp': text = ''
        elif shifted and modifiers & SHIFT: text = shifted
        p = dict(type=kind if kind == 'keyUp' else 'keyDown' if text else 'rawKeyDown', key=key, code=code, windowsVirtualKeyCode=vk,
                 modifiers=modifiers, location=1 if name in _MODIFIER_BITS else 0)
        if text: p['text'] = p['unmodifiedText'] = text
        if repeat: p['autoRepeat'] = True
        self.call('Input.dispatchKeyEvent', **p)

    def key_down(self, key, modifiers=0, repeat=0):
        """Press a key and keep it down (e.g. ' ' to hold Space for panning, 'Alt' to hold Alt). repeat sends that many extra
        auto-repeat keydowns. Held Shift/Ctrl/Alt/Meta also apply to later mouse and key events, as on a real keyboard."""
        name, modifiers = parse_keys(key, modifiers)
        self._held |= _MODIFIER_BITS.get(name, 0)
        if name not in _MODIFIER_BITS: self._down.add(name)
        for i in range(repeat + 1): self._key('keyDown', name, modifiers | self._held, i > 0)

    def key_up(self, key, modifiers=0):
        name, modifiers = parse_keys(key, modifiers)
        self._held &= ~_MODIFIER_BITS.get(name, 0)
        self._down.discard(name)
        self._key('keyUp', name, modifiers | self._held)

    @contextlib.contextmanager
    def hold(self, *keys):
        """with browser.hold(' '): ... keeps keys down for the block (released even if it fails)."""
        done = []
        try:
            for k in keys:
                self.key_down(k)
                done.append(k)
            yield self
        finally:
            for k in reversed(done): self.key_up(k)

    def key(self, key, modifiers=0):
        """Press and release a key or combo: key('e'), key('Enter'), key('['), key('Ctrl+Z'), key('z', CTRL | SHIFT).
        Modifier keys are pressed first and released last with the right masks, like fingers on a keyboard."""
        name, modifiers = parse_keys(key, modifiers)
        mask, pressed, own = self._held, [], _MODIFIER_BITS.get(name, 0)
        for mod, bit in (('Control', CTRL), ('Shift', SHIFT), ('Alt', ALT), ('Meta', META)):
            if modifiers & bit and not mask & bit and name != mod:
                mask |= bit
                self._key('keyDown', mod, mask)
                pressed.append((mod, bit))
        self._key('keyDown', name, mask | modifiers | own)   # a modifier key reports itself as held while it is down
        self._key('keyUp', name, (mask | modifiers) & ~own)
        for mod, bit in reversed(pressed):
            mask &= ~bit
            self._key('keyUp', mod, mask)

    def reset_input(self):
        """Release every mouse button and held key (EditorTestCase does this after each test)."""
        for button, bit in _MOUSE_BUTTONS.items():
            if self._buttons & bit: self.mouse_up(*self._pos, button)
        for name, bit in _MODIFIER_BITS.items():
            if self._held & bit: self.key_up(name)
        for name in list(self._down): self.key_up(name)

    def type_text(self, text):
        """Insert text into the focused field as an input method would (no key events)."""
        self.call('Input.insertText', text=text)

    # ----- page elements -------------------------------------------------------------------------------------------
    def center_of(self, selector):
        """Page coordinates of the middle of the first element matching a CSS selector (scrolled into view if needed)."""
        r = self._js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)throw Error('Nothing matches '+{json.dumps(selector)});"
                     "e.scrollIntoView({block:'nearest',inline:'nearest'});const r=e.getBoundingClientRect();return[r.left+r.width/2,r.top+r.height/2,r.width,r.height];})()")
        if r[2] <= 0 or r[3] <= 0: raise AssertionError(f'{selector} is hidden or has no size')
        return r[0], r[1]

    def click_selector(self, selector, button='left', modifiers=0, count=1):
        """A real mouse click on the middle of an element. Fails if another element covers that point."""
        x, y = self.center_of(selector)
        hit = self._js(f"(()=>{{const e=document.elementFromPoint({x},{y}),t=document.querySelector({json.dumps(selector)});return !!e&&(e===t||t.contains(e))||(e?e.outerHTML.slice(0,120):'nothing');}})()")
        if hit is not True: raise AssertionError(f'Clicking {selector} would hit {hit} instead')
        self.click(x, y, button, modifiers, count)

    def set_value(self, selector, value):
        """Set an input's value (or a checkbox's checked state with True/False) and fire input and change, as a user edit does."""
        self._js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)throw Error('Nothing matches '+{json.dumps(selector)});const v={json.dumps(value)};"
                 "if(typeof v==='boolean')e.checked=v;else e.value=String(v);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true;})()")

    def text(self, selector):
        return self._js(f"(document.querySelector({json.dumps(selector)})||{{}}).textContent")

    def set_files(self, selector, paths):
        """Pick files in an <input type="file"> as the file chooser would (fires input and change), e.g. Import image."""
        root = self.call('DOM.getDocument', depth=0)['root']['nodeId']
        node = self.call('DOM.querySelector', nodeId=root, selector=selector).get('nodeId')
        if not node: raise LookupError(f'Nothing matches {selector}')
        self.call('DOM.setFileInputFiles', files=[str(Path(p).resolve()) for p in paths], nodeId=node)

    # ----- pixels --------------------------------------------------------------------------------------------------
    def pixels(self, expression, region=None):
        """Pixels as uint8 (H, W, 4) of what a JavaScript expression gives: a canvas, OffscreenCanvas, ImageData, image,
        ImageBitmap or {width, height, data} (data may be base64; one byte per pixel gives (H, W)). region=(x, y, w, h)
        reads a part. Canvas pixels are what getImageData returns: un-premultiplied sRGB."""
        info = self._js(f'__labTest.grab(({expression}), {json.dumps([int(v) for v in region]) if region else "null"})')
        w, h, c, n = info['w'], info['h'], info['c'], info['n']
        if 'b64' in info: raw = base64.b64decode(info['b64'])
        else:
            try: raw = b''.join(base64.b64decode(self._js(f"__labTest.chunk({info['id']}, {s}, {min(n, s + CHUNK)})")) for s in range(0, n, CHUNK))
            finally: self._js(f"__labTest.free({info['id']})")
        a = np.frombuffer(raw, np.uint8).copy()
        return a.reshape((h, w, c) if c > 1 else (h, w))

    def editor_pixels(self, target='composite', region=None):
        """The editor's pixels: window.Ed.test.pixels(target) when the editor core defines it ('composite', a layer index or
        'selection'); else today's draft canvas S.draft (target 'composite' or 0)."""
        if self._js("!!(window.Ed && Ed.test && typeof Ed.test.pixels === 'function')"):
            return self.pixels(f'window.Ed.test.pixels({json.dumps(target)})', region)
        if target not in ('composite', 0, None):
            raise NotImplementedError(f'Today\'s editor has a single draft canvas; {target!r} needs window.Ed.test.pixels from the editor core')
        return self.pixels('S.draft', region)

    def wait_for_pixels(self, predicate, target='composite', timeout=10, interval=0.05):
        """Poll editor_pixels(target) until predicate(pixels) is true and return those pixels: for edits the editor finishes
        later (undo decodes an image, a filter may run in steps)."""
        deadline = time.monotonic() + timeout
        while True:
            px = self.editor_pixels(target)
            if predicate(px): return px
            if time.monotonic() > deadline: raise TimeoutError(f'The editor pixels ({target}) did not reach the expected state within {timeout} s')
            time.sleep(interval)

    def _upload(self, text):
        uid = self._js('Date.now() * 1000 + Math.floor(Math.random() * 1000)')
        for s in range(0, len(text), CHUNK): self._js(f'__labTest.put({uid}, {json.dumps(text[s:s + CHUNK])})')
        if not text: self._js(f'__labTest.put({uid}, "")')
        return uid

    def push_pixels(self, array):
        """Send an (H, W, 4) uint8 array into the page as an ImageData; returns a JavaScript expression that names it."""
        a = np.ascontiguousarray(array, np.uint8)
        uid = self._upload(rgba_b64(a))
        self._js(f'__labTest.image({uid}, {a.shape[1]}, {a.shape[0]})')
        return f'__labTest.images[{uid}]'

    def load_editor_image(self, array):
        """Replace the editor's document with these pixels through window.Ed.test.load(width, height, base64 RGBA)."""
        if not self._js("!!(window.Ed && Ed.test && typeof Ed.test.load === 'function')"):
            raise NotImplementedError('window.Ed.test.load is not defined; the editor core provides it')
        a = np.ascontiguousarray(array, np.uint8)
        uid = self._upload(rgba_b64(a))
        self._js(f'Promise.resolve(window.Ed.test.load({a.shape[1]}, {a.shape[0]}, __labTest.take({uid}))).then(() => true)')
        self.settle()

    # ----- screenshots ---------------------------------------------------------------------------------------------
    def _capture(self, clip=None):
        params = dict(format='png')
        if clip: params['clip'] = dict(x=float(clip[0]), y=float(clip[1]), width=float(clip[2]), height=float(clip[3]), scale=1)
        return base64.b64decode(self.call('Page.captureScreenshot', **params)['data'])

    def screenshot(self, path=None, clip=None):
        """Save a PNG of the page, or of clip=(x, y, width, height) in page coordinates. A relative path (or none) goes into
        tests/browser/output/. Returns the path."""
        path = output_path(path or f'screenshot-{time.strftime("%Y%m%d-%H%M%S")}.png')
        data = self._capture(clip)
        for attempt in range(5):   # OneDrive or a viewer may hold the previous file for a moment
            try:
                path.write_bytes(data)
                break
            except PermissionError:
                if attempt == 4: raise
                time.sleep(0.2)
        return path

    def screenshot_array(self, clip=None):
        """What is on screen as uint8 (H, W, 4)."""
        with Image.open(io.BytesIO(self._capture(clip))) as im: return np.array(im.convert('RGBA'))

    def close(self):
        if self.ws: self.ws.close()
        kill_tree(self.process)
        if getattr(self, 'startup_log', None): self.startup_log.close()
        if self.keep: print(f'[browser tests] kept the Edge profile in {self.profile}', file=sys.stderr)
        else: remove_tree(self.profile)
        self.ws = None

    def __enter__(self): return self

    def __exit__(self, *exc): self.close()


# ----- test case base ------------------------------------------------------------------------------------------------
class EditorTestCase(unittest.TestCase):
    """One isolated LabServer and one headless Edge for the whole class, with the lab already open.
    self.server and self.browser are shared by the tests in the class, which run in name order."""
    viewport = (1600, 1000)
    game_maker = staticmethod(make_editor_game)
    open_on_start = True       # open the lab page in setUpClass
    fresh_page = False         # True: reload the lab before every test (slower, fully isolated)
    check_errors = True        # tearDown fails a test that caused browser errors it did not check itself
    allowed_errors = ()        # substrings of errors every check in this class ignores
    feature_modules = True     # False: the editor's feature modules are served empty (tests of the core alone)
    server = browser = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.server = LabServer(cls.game_maker)
            cls.browser = Browser(*cls.viewport)
            if not cls.feature_modules: cls.browser.stub_editor_modules()
            if cls.open_on_start: cls.browser.open_lab(cls.server)
        except BaseException:
            cls._shutdown()
            raise

    @classmethod
    def _shutdown(cls):
        if cls.browser: cls.browser.close()
        if cls.server: cls.server.stop()
        cls.browser = cls.server = None

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Theme AI keeps its chat across reloads; each test starts with none (a test that wants one writes it itself).
        try: self.browser.eval("localStorage.removeItem('lab-theme-chat'); localStorage.removeItem('lab-theme-open')")
        except Exception: pass
        # Chats are saved per build on the server (builds.py): those of earlier tests go too.
        try:
            self.browser.eval("window.LabThemeChat && LabThemeChat.reset(); true")   # the open page must not save it again
            for chat in self.server.get('/api/chats')['chats']: self.server.post('/api/chat-delete', {'id': chat['id']})
        except Exception: pass
        if self.fresh_page: self.browser.open_lab(self.server)
        self.browser.pump()
        self._error_mark = self.browser.seq

    def tearDown(self):
        if self.browser:
            self.browser.reset_input()
            if self.check_errors: self.assertNoBrowserErrors()
        super().tearDown()

    def run(self, result=None):
        if result is None: return super().run(result)
        before = len(result.failures) + len(result.errors)
        out = super().run(result)
        if len(result.failures) + len(result.errors) > before and self.browser:
            with contextlib.suppress(Exception):
                print(f'[browser tests] failure screenshot: {self.browser.screenshot(f"failure-{self.id()}.png")}', file=sys.stderr)
            with contextlib.suppress(Exception):
                state = self.browser.eval("JSON.stringify({visibility:document.visibilityState,audio:window.LabAudio?LabAudio.state():null,focus:document.activeElement?.outerHTML?.slice(0,500)})")
                print(f'[browser tests] failure state: {state}', file=sys.stderr)
        return out

    def assertNoBrowserErrors(self, allow=(), everything=False):
        """Fail if the page logged console errors, threw uncaught exceptions or had failed requests during this test
        (everything=True: since the browser started, page load included). allow: substrings to ignore (plus allowed_errors).
        Checked errors are not reported again by later checks."""
        errors = self.browser.errors_since(0 if everything else self._error_mark)
        self._error_mark = self.browser.seq
        patterns = tuple(allow) + tuple(self.allowed_errors)
        bad = [e for e in errors if not any(p in e['text'] or p in e['url'] for p in patterns)]
        if bad: self.fail(f'{len(bad)} browser error(s):\n' + self.browser.error_report(bad))

    def assertPixelsClose(self, actual, expected, max_abs=0, mean_abs=None, mask=None, msg=''):
        """assert_pixels_close with the visual diff named after this test."""
        assert_pixels_close(actual, expected, max_abs, mean_abs, mask, msg, f'diff-{self.id().split(".")[-1]}.png')

    def original(self, name):
        """The pixels of assets/<name> in the fake game, as uint8 (H, W, 4)."""
        return read_png(self.server.asset_path(name))
