"""Opt-in real-model stress test. Copies game art to a disposable game and never installs a mod.

python tools/stress_image_generation.py --game ".../Mitos.is The Game"
Runs sequentially to avoid competing image models. Requires the already-installed AI runtime.
"""
import argparse
import ctypes
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/browser'), str(ROOT / 'tests'), str(ROOT / 'src')]
from harness import LabServer
from PIL import Image


def memory(pid):
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (n, ctypes.c_size_t) for n in ('peak_working', 'working', 'peak_pool', 'pool', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    proc = kernel.OpenProcess(0x1000, False, pid)
    if not proc: return None
    try:
        info = Counters(); info.cb = ctypes.sizeof(info)
        get = ctypes.WinDLL('psapi').GetProcessMemoryInfo
        get.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if get(proc, ctypes.byref(info), info.cb): return round(info.working / 2**20, 1)
    finally: kernel.CloseHandle(proc)


def gpu():
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,utilization.gpu', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=5, creationflags=0x08000000)
        return [list(map(int, line.split(','))) for line in result.stdout.strip().splitlines()]
    except (OSError, ValueError, subprocess.TimeoutExpired): return None


def hashes(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=ROOT / 'runtime')
    parser.add_argument('--report', type=Path, default=ROOT / 'local-only/image-stress.json')
    parser.add_argument('--cases', default='cancel-load,recover-square,batch-four,repeat-logo,cancel-active,crash-active,recover-background')
    args = parser.parse_args()
    report = {'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'cases': [], 'status': 'running',
              'gpu_measurement': 'Total device use, including other applications; working memory is renderer-only.'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    def save(): args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    def fixture(root):
        game = root / 'StressGame'; assets = game / 'assets'; assets.mkdir(parents=True)
        (game / 'Mitosis.exe').write_bytes(b'Non-executable isolated image-generation test fixture')
        for name in ('theme.png', 'theme.xml', 'bg2.png'):
            shutil.copy2(args.game / 'assets' / name, assets / name)
        return game
    class RealModelServer(LabServer):
        def _start(self, timeout):
            self.runtime = args.runtime.resolve()
            super()._start(timeout)
    with RealModelServer(maker=fixture, keep=True) as server:
        report['workspace'] = str(server.workspace)
        report['url'] = server.url
        original = hashes(server.game)
        keys = {n: server.asset_key(n) for n in ('logo', 'icon-crown', 'bg2.png')}
        server.post('/api/settings', {'values': {'free_gpu': True, 'drawing_quality': 'fast'}})
        cases = {
            'cancel-load': ('logo', 1, 'Chrome MITOSIS lettering with cyan highlights', 'immediate'),
            'recover-square': ('icon-crown', 1, 'A polished ruby crown with bright silver edges, centered on flat green', None),
            'batch-four': ('logo', 4, 'Bold metallic MITOSIS lettering with violet illuminated edges', None),
            'repeat-logo': ('logo', 1, 'Bold metallic MITOSIS lettering with cyan illuminated edges', None),
            'cancel-active': ('icon-crown', 1, 'A polished sapphire crown with bright silver edges', 'active'),
            'crash-active': ('logo', 1, 'Bold metallic MITOSIS lettering with violet illuminated edges', 'crash'),
            'recover-background': ('bg2.png', 1, 'Dark teal cellular hexagon background, soft large shapes and subtle lighting', None),
        }
        for name in args.cases.split(','):
            target, count, direction, cancel = cases[name]
            result = {'case': name, 'target': target, 'requested': count, 'samples': [], 'status': 'running'}
            report['cases'].append(result); save()
            started = time.monotonic()
            job = None
            try:
                job = server.post('/api/design-start', {'prompt': direction, 'direction': direction, 'only': [keys[target]], 'count': count})
                result['id'] = job['id']
                # Concurrent starts must fail rather than creating a second GPU process.
                try:
                    server.post('/api/design-start', {'prompt': direction, 'direction': direction, 'only': [keys[target]], 'count': 1})
                except RuntimeError as exc:
                    assert 'answered 400: A design is already being drawn.' in str(exc), str(exc)
                    result['concurrent_start_rejected'] = True
                else: raise AssertionError('A second job was allowed while one was running')
                cancelled_at = None
                while job['state'] == 'running':
                    elapsed = time.monotonic() - started
                    if elapsed > 1200: raise TimeoutError('Drawing exceeded the 20-minute case limit')
                    job = server.get('/api/design-job', job=job['id'])
                    result['samples'].append({'seconds': round(elapsed, 1), 'message': job['message'], 'pid': job.get('pid'),
                                              'working_mib': memory(job['pid']) if job.get('pid') else None, 'gpu': gpu()})
                    log = server.workspace / 'designs' / job['id'] / 'render.log'
                    text = log.read_text('utf-8', errors='replace') if log.exists() else ''
                    active = bool(re.search(r'\|\s*[1-9]\d*/4\s*-\s*[\d.]+s/it', text))
                    if cancel and cancelled_at is None and (cancel == 'immediate' or active):
                        cancelled_at = time.monotonic()
                        result['cancel_phase'] = job['message']
                        if cancel == 'crash':
                            assert job.get('pid'), 'No owned renderer to terminate'
                            subprocess.run(['taskkill.exe', '/PID', str(job['pid']), '/T', '/F'], capture_output=True, timeout=15, creationflags=0x08000000, check=True)
                        else: server.post('/api/design-cancel', {'job': job['id']})
                    save()
                    if job['state'] == 'running': time.sleep(2)
                result['message'] = job['message']
                if cancel:
                    assert cancelled_at is not None and job['state'] == 'failed', job
                    assert bool(job.get('cancelled')) == (cancel != 'crash'), job
                    result['cancel_seconds'] = round(time.monotonic() - cancelled_at, 2)
                    assert not job['variants'], 'Cancelled render exposed variants'
                else:
                    assert job['state'] == 'complete' and len(job['variants']) == count, job
                    outputs = []
                    for variant in job['variants']:
                        path = server.workspace / 'designs' / job['id'] / f'variant-{variant["index"]}.png'
                        with Image.open(path) as im:
                            im.load()
                            assert im.width > 0 and im.height > 0 and im.convert('RGBA').getchannel('A').getbbox(), 'Blank image'
                            outputs.append({'path': str(path), 'size': list(im.size), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
                    assert len({o['sha256'] for o in outputs}) == count, 'Repeated identical variants'
                    result['outputs'] = outputs
                    applied = server.post('/api/design-apply', {'job': job['id'], 'variant': 0})
                    undone = server.post('/api/design-undo', {'job': job['id']})
                    assert undone['reverted'] == len(applied['items']) > 0
                    result['apply_undo'] = True
                result['status'] = 'passed'
            except Exception as exc:
                result.update(status='failed', error=str(exc))
                if job:
                    server.post('/api/design-cancel', {'job': job['id']})
                    for _ in range(30):
                        if server.get('/api/design-job', job=job['id'])['state'] != 'running': break
                        time.sleep(1)
            result['seconds'] = round(time.monotonic() - started, 2)
            print(name, result['status'], result['seconds'], result.get('error', ''), flush=True)
            save()
        report['game_files_unchanged'] = hashes(server.game) == original
        report['status'] = 'passed' if report['game_files_unchanged'] and all(c['status'] == 'passed' for c in report['cases']) else 'failed'
        save()
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__': raise SystemExit(main())
