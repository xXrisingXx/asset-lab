"""Kill real, isolated launcher processes at update boundaries and verify restart/data integrity.

Uses a loopback GitHub-shaped release feed, fixture game, and copied portable runtime.
Never updates the development launcher, user's installation, or real game.
Run after build_release.ps1; --python-home is a previously verified portable runtime.
"""
import argparse
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from fixtures import make_game
from lab_core import Lab
from builds import Builds
from PIL import Image


def digest(data):
    return hashlib.sha256(data).hexdigest()


def wait_until(fn, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError('Timed out waiting for isolated launcher')


def wait_pid(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 30000) == 0, 'Launcher did not exit'
        finally:
            kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python-home', type=Path, required=True)
    args = parser.parse_args()
    qa = ROOT / 'local-only' / ('update-recovery-' + uuid.uuid4().hex)
    qa.mkdir(parents=True)
    home, data = qa / 'install', qa / 'player'
    home.mkdir(); data.mkdir()
    game = make_game(qa)
    os.environ['ASSET_LAB_HOME'] = str(data)
    workspace = data / 'workspace'
    settings = data / 'settings.json'
    settings.write_text(json.dumps({'language': 'en', 'check_updates': False, 'page_size': 120}))
    lab = Lab(game=game, workspace=workspace, process_check=lambda: False, settings_file=settings)
    key = next(r['key'] for r in lab.catalog.rows.values() if r['name'] == 'button-up-skin')
    other = next(r['key'] for r in lab.catalog.rows.values() if r['name'] == 'rotated')
    lab.commit(key, Image.new('RGBA', lab.image(key).size, '#345678'), {'qa': 'saved edit'})
    lab.favorites.add([key]); lab.labels.name([key], 'Saved QA button'); lab.pool.add([other])
    builds = Builds(lab)
    builds.create('QA second build', copy_of=builds.index['active'])
    shutil.copytree(args.python_home, home / 'python')
    executable = qa / 'AssetLab.exe'
    original_exe = (ROOT / 'dist/AssetLab.exe').read_bytes()
    new_exe = original_exe + b'\nAsset Lab isolated update test PE overlay\n'
    app_zip = (ROOT / 'dist/asset-lab-app.zip').read_bytes()
    incomplete = io.BytesIO()
    with zipfile.ZipFile(incomplete, 'w') as package:
        package.writestr('README.md', 'Intentionally missing application code')
    incomplete_zip = incomplete.getvalue()
    current_version = (ROOT / 'VERSION').read_text().strip()
    next_version = current_version + '.1'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    state = {'mode': 'normal', 'requests': [], 'ranges': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state['requests'].append(self.path)
            offered_app = incomplete_zip if state['mode'] == 'incomplete-app' else app_zip
            if self.path == '/release':
                body = json.dumps({'tag_name': 'v' + next_version, 'body': 'Isolated QA update', 'assets': [
                    {'name': 'asset-lab-app.zip', 'browser_download_url': feed + '/app.zip',
                     'digest': 'sha256:' + ('0' * 64 if state['mode'] == 'bad-app' else digest(offered_app)), 'size': len(offered_app)},
                    {'name': 'AssetLab.exe', 'browser_download_url': feed + '/AssetLab.exe',
                     'digest': 'sha256:' + ('0' * 64 if state['mode'] == 'bad-exe' else digest(new_exe)), 'size': len(new_exe)}]}).encode()
            elif self.path == '/app.zip':
                body = offered_app
            elif self.path == '/AssetLab.exe':
                body = new_exe
            else:
                self.send_error(404); return
            start = 0
            if self.headers.get('Range'):
                start = int(self.headers['Range'].split('=')[1].split('-')[0])
                state['ranges'].append({'path': self.path, 'start': start})
            self.send_response(206 if start else 200)
            self.send_header('Content-Length', str(len(body) - start))
            if start:
                self.send_header('Content-Range', f'bytes {start}-{len(body)-1}/{len(body)}')
            self.end_headers()
            try:
                # Small chunks make the mid-download kill deterministic and exercise HTTP range resume.
                for offset in range(start, len(body), 16384):
                    self.wfile.write(body[offset:offset + 16384]); self.wfile.flush()
                    if self.path != '/release': time.sleep(.005)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    feed = f'http://127.0.0.1:{server.server_port}'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, ASSET_LAB_AUTO_UPDATE='1', ASSET_LAB_RELEASES=feed + '/release')
    results = []

    def launch(name, update=False, point=None):
        report = qa / (name + '.json')
        report.unlink(missing_ok=True)
        process_env = env.copy()
        if not update:
            process_env.pop('ASSET_LAB_RELEASES', None)
            process_env.pop('ASSET_LAB_AUTO_UPDATE', None)
        if point:
            process_env['ASSET_LAB_TEST_UPDATE_POINT'] = point
        command = [str(executable), '--offline', '--port', str(port), '--home', str(home),
                   '--workspace', str(workspace), '--game', str(game), '--smoke-test', str(report)]
        p = subprocess.Popen(command, env=process_env, creationflags=subprocess.CREATE_NO_WINDOW)
        if point:
            try:
                marker = home / 'update-checkpoint.json'
                wait_until(lambda: marker.exists() or (p.poll() is not None and report.exists()))
                assert marker.exists(), report.read_text() if report.exists() else 'No checkpoint'
                assert json.loads(marker.read_text())['point'] == point
                p.kill(); p.wait(10)
                return None
            finally:
                if p.poll() is None: p.kill(); p.wait(10)
        try:
            wait_until(report.exists)
            result = json.loads(report.read_text())
            p.wait(30)
            if 'launcherPid' in result: wait_pid(result['launcherPid'])
            return result
        finally:
            if p.poll() is None: p.kill(); p.wait(10)

    executable.write_bytes(original_exe)
    assert launch('baseline')['passed']
    protected = [p for p in data.rglob('*') if p.is_file() and not any(x in str(p) for x in ('audit-cache', 'similar', '__pycache__'))]
    before = {str(p.relative_to(data)): digest(p.read_bytes()) for p in protected}
    original_game = {str(p.relative_to(game)): digest(p.read_bytes()) for p in game.rglob('*') if p.is_file()}

    def check_data():
        after = {str(p.relative_to(data)): digest(p.read_bytes()) for p in protected if p.exists()}
        assert before == after, 'Player data changed: ' + str([k for k in before if before[k] != after.get(k)])
        assert original_game == {str(p.relative_to(game)): digest(p.read_bytes()) for p in game.rglob('*') if p.is_file()}, 'Game fixture changed'

    def reset():
        executable.write_bytes(original_exe)
        (home / 'app/current.txt').write_text(current_version)
        for p in [home / 'pending-update.json', home / 'update-checkpoint.json', home / 'last-update-recovery.json',
                  Path(str(executable) + '.new'), Path(str(executable) + '.old')]:
            p.unlink(missing_ok=True)
        # Download files are owned exclusively by this test installation.
        for p in (home / 'downloads').glob('*'):
            if p.is_file(): p.unlink()
        state['mode'] = 'normal'
        state['ranges'].clear()

    try:
        for point in ['download', 'extract', 'before-pointer', 'after-pointer', 'after-exe']:
            reset()
            launch('kill-' + point, update=True, point=point)
            assert executable.exists(), 'Crash removed the launchable EXE'
            recovered = launch('recover-' + point)
            assert recovered['passed'], recovered
            expected = next_version if point == 'after-exe' else current_version
            assert recovered['version'] == expected, recovered
            check_data()
            retried = launch('retry-' + point, update=True)
            assert retried['passed'] and retried['version'] == next_version, retried
            assert digest(executable.read_bytes()) == digest(new_exe)
            check_data()
            if point == 'download': assert state['ranges'], 'Interrupted download was not resumed'
            results.append({'case': point, 'passed': True, 'recoveredVersion': expected, 'retryVersion': retried['version'], 'ranges': list(state['ranges'])})
            print('PASS: killed at', point, '-> recovered', expected, '-> updated', next_version, flush=True)
        for corruption in ['bad-app', 'bad-exe', 'incomplete-app']:
            reset(); state['mode'] = corruption
            rejected = launch(corruption, update=True)
            expected_error = 'incomplete' if corruption == 'incomplete-app' else 'checksum'
            assert not rejected['passed'] and expected_error in rejected['error'], rejected
            assert (home / 'app/current.txt').read_text().strip() == current_version
            assert digest(executable.read_bytes()) == digest(original_exe)
            recovered = launch('recover-' + corruption)
            assert recovered['passed'], recovered
            check_data()
            results.append({'case': corruption, 'passed': True, 'error': rejected['error']})
            print('PASS:', corruption, 'rejected; existing installation still boots', flush=True)
        reset()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        # Permit execution/reading but deny replacement, as a scanner or another app can do.
        locked = kernel.CreateFileW(str(executable), 0x80000000, 1, None, 3, 0x80, None)
        assert locked not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
        try:
            rejected = launch('locked-exe', update=True)
            assert not rejected['passed'], rejected
            assert (home / 'app/current.txt').read_text().strip() == current_version
            assert digest(executable.read_bytes()) == digest(original_exe)
        finally:
            kernel.CloseHandle(locked)
        recovered = launch('recover-locked-exe')
        assert recovered['passed'], recovered
        check_data()
        results.append({'case': 'locked-exe', 'passed': True, 'error': rejected['error']})
        print('PASS: locked EXE fails safely and restores the previous app pointer', flush=True)
        reset()
        successful = launch('normal-update', update=True)
        assert successful['passed'] and successful['version'] == next_version, successful
        check_data()
        assert any((home / 'backups').rglob('favorite-assets.json')), 'Settings backup missing'
        assert (home / 'app' / current_version / 'src/lab_server.py').exists(), 'Previous app not retained'
        results.append({'case': 'normal-update', 'passed': True, 'version': successful['version']})
        print('PASS: normal app + EXE update, handoff, backups, and player data preservation', flush=True)
    finally:
        server.shutdown(); server.server_close()
        (qa / 'summary.json').write_text(json.dumps({'cases': results, 'protectedFiles': before, 'fixtureGame': original_game}, indent=2))
        print('Evidence:', qa, flush=True)
    assert len(results) == 10


if __name__ == '__main__':
    main()
