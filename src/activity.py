"""What the local AI is doing right now, for the small activity strip in Theme AI: the stage (planning, loading the image
model, drawing, finishing), memory (RAM and the graphics card's VRAM) and which planner models are loaded.

Read-only and cheap: Windows' own memory counter, nvidia-smi when an NVIDIA card is there (cached for two seconds), and
Ollama's list of loaded models without ever starting Ollama.
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import runtime

_cache, _lock = {}, threading.Lock()


def ram():
    """{'used', 'total'} in bytes, or None off Windows."""
    if os.name != 'nt': return None
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong),
                    ('page_total', ctypes.c_ulonglong), ('page_free', ctypes.c_ulonglong), ('virtual_total', ctypes.c_ulonglong),
                    ('virtual_free', ctypes.c_ulonglong), ('extended', ctypes.c_ulonglong)]
    s = Status(); s.length = ctypes.sizeof(Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)): return None
    return dict(used=s.total - s.free, total=s.total)


def _cached(name, seconds, make):
    with _lock:
        hit = _cache.get(name)
        if hit and time.monotonic() - hit[0] < seconds: return hit[1]
    value = make()
    with _lock: _cache[name] = (time.monotonic(), value)
    return value


def gpu():
    """{'name', 'used', 'total', 'busy'} for the first NVIDIA card (bytes, busy in %), or None without one."""
    def read():
        exe = shutil.which('nvidia-smi')
        if not exe: return None
        try:
            out = subprocess.run([exe, '--query-gpu=name,memory.used,memory.total,utilization.gpu', '--format=csv,noheader,nounits'],
                                 capture_output=True, text=True, timeout=3, creationflags=runtime.QUIET).stdout
            name, used, total, busy = [p.strip() for p in out.splitlines()[0].split(',')]
            return dict(name=name, used=int(used) * 2**20, total=int(total) * 2**20, busy=int(busy))
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired): return None
    return _cached('gpu', 2.0, read)


def loaded():
    """Planner models Ollama has in memory right now: [{'name', 'size', 'vram'}]. Never starts Ollama."""
    def read():
        try:
            with urllib.request.urlopen(runtime.OLLAMA.url + '/api/ps', timeout=1) as r: models = json.load(r).get('models') or []
            return [dict(name=m.get('name', ''), size=m.get('size', 0), vram=m.get('size_vram', 0)) for m in models]
        except (OSError, ValueError): return []
    return _cached('loaded', 2.0, read)


# The image model's progress messages (design.render_progress) by stage.
STAGES = (('text', r'art direction|ideas'), ('loading', r'Loading'), ('drawing', r'Drawing'), ('finishing', r'Finishing'))


def stage(designer, themes):
    """(kind, words): idle, planning (the planner model is writing a plan), text, loading, drawing or finishing."""
    job = (designer.status() or {}).get('job') if designer else None
    if job:
        message = job.get('message') or ''
        return next((kind for kind, words in STAGES if re.search(words, message)), 'drawing'), message
    planning = getattr(themes, 'activity', None)
    if planning: return 'planning', planning
    return 'idle', ''


def snapshot(designer, themes):
    kind, words = stage(designer, themes)
    return dict(stage=kind, detail=words, ram=ram(), gpu=gpu(), loaded=loaded())
