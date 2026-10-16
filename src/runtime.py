"""The lab's own AI runtime, kept in runtime/ beside the code so the folder works on a PC with nothing installed.

- runtime/ollama: portable Ollama (planning). An Ollama already running on this PC is used instead when present.
- runtime/sd: stable-diffusion.cpp (drawing), run as a short-lived process per request.
- runtime/models: FLUX.2 klein 4B (image generation and editing, Apache-2.0), Qwen3-4B (its text encoder, and the
  bundled planner model), and the FLUX.2 VAE. runtime/ollama-models holds the bundled Ollama's own model store.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import zipfile

# The installed app keeps it in %LOCALAPPDATA%\AssetLab\runtime (set by the launcher); a source checkout beside the code.
ROOT = Path(os.environ.get('ASSET_LAB_RUNTIME') or Path(__file__).resolve().parent.parent / 'runtime')
SYSTEM = 'http://127.0.0.1:11434'
BUNDLED = 'http://127.0.0.1:11439'  # a port of its own, so it never collides with an installed Ollama
PLANNER = 'lab-qwen3-4b:latest'
FILES = dict(sd='sd/sd-cli.exe', ollama='ollama/ollama.exe', diffusion='models/flux-2-klein-4b-Q4_0.gguf',
             llm='models/Qwen3-4B-Q4_K_M.gguf', vae='models/full_encoder_small_decoder.safetensors')
ARCHIVES = {'sd': 'downloads/sd-master-1d0a58a-bin-win-vulkan-x64.zip', 'ollama': 'downloads/ollama-windows-amd64.zip'}
QUIET = 0x08000000 if os.name == 'nt' else 0  # no console window
# Qwen3's chat template (from Ollama's own qwen3 model), so the imported GGUF chats and answers JSON correctly.
TEMPLATE = '''{{- if .Messages }}
{{- if or .System .Tools }}<|im_start|>system
{{- if .System }}
{{ .System }}
{{- end }}<|im_end|>
{{ end }}
{{- range $i, $_ := .Messages }}
{{- $last := eq (len (slice $.Messages $i)) 1 -}}
{{- if eq .Role "user" }}<|im_start|>user
{{ .Content }}<|im_end|>
{{ else if eq .Role "assistant" }}<|im_start|>assistant
{{ if .Content }}{{ .Content }}{{ end }}{{ if not $last }}<|im_end|>
{{ end }}
{{- end }}
{{- if and (ne .Role "assistant") $last }}<|im_start|>assistant
{{ end }}
{{- end }}
{{- else }}
{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ end }}{{ .Response }}{{ if .Response }}<|im_end|>{{ end }}'''


def path(name): return ROOT / FILES[name]


def missing(*names):
    unpack()
    return [FILES[n] for n in names if not path(n).is_file()]


def unpack():
    """Extract a downloaded archive the first time its program is needed."""
    for name, archive in ARCHIVES.items():
        target, source = path(name), ROOT / archive
        if target.is_file() or not source.is_file(): continue
        folder = target.parent
        with zipfile.ZipFile(source) as z:
            for member in z.namelist():
                out = (folder / member).resolve()
                if not out.is_relative_to(folder.resolve()): raise ValueError('Unsafe path in ' + source.name)
            z.extractall(folder)


def call(url, route, payload=None, timeout=5):
    request = urllib.request.Request(url + route, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response: return json.loads(response.read().decode('utf-8'))


class Ollama:
    """Finds a running Ollama, or starts the bundled one, and makes sure the bundled planner model exists in it."""

    def __init__(self):
        self.url, self.process, self.lock, self.note = SYSTEM, None, threading.Lock(), ''
        self.retry_at = 0

    def up(self, url):
        try: call(url, '/api/version', timeout=1.5); return True
        except (OSError, ValueError): return False

    def ensure(self):
        with self.lock:
            if time.monotonic() < self.retry_at: return self.url
            for url in dict.fromkeys((self.url, SYSTEM, BUNDLED)):
                if self.up(url):
                    switched = url != self.url
                    self.url = url
                    if switched and url == BUNDLED: self.import_planner()  # left running by a restarted lab
                    return self.url
            if missing('ollama'):
                # Settings and Theme AI can ask at the same time. Do not queue repeated offline probes.
                self.retry_at = time.monotonic() + 3
                return self.url  # nothing to start; callers see an empty model list
            env = dict(os.environ, OLLAMA_HOST=BUNDLED.split('//')[1], OLLAMA_MODELS=str(ROOT / 'ollama-models'))
            (ROOT / 'ollama-models').mkdir(exist_ok=True)
            self.process = subprocess.Popen([str(path('ollama')), 'serve'], env=env, cwd=str(path('ollama').parent), creationflags=QUIET,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(60):
                if self.up(BUNDLED): break
                time.sleep(0.5)
            self.url = BUNDLED
            self.import_planner()
            return self.url

    def import_planner(self):
        """The bundled store starts empty: create the planner from the Qwen3-4B GGUF that also drives the image model."""
        try:
            if any(m['name'] == PLANNER for m in call(self.url, '/api/tags').get('models', [])): return
        except (OSError, ValueError): return
        if missing('llm', 'ollama'): return
        modelfile = ROOT / 'ollama-models' / 'planner.Modelfile'
        modelfile.write_text(f'FROM {path("llm")}\nTEMPLATE """{TEMPLATE}"""\nPARAMETER stop <|im_end|>\nPARAMETER temperature 0.2\n', encoding='utf-8')
        env = dict(os.environ, OLLAMA_HOST=self.url.split('//')[1])
        self.note = 'Preparing the bundled planner model (first run only)…'
        subprocess.run([str(path('ollama')), 'create', PLANNER.split(':')[0], '-f', str(modelfile)], env=env, creationflags=QUIET,
                       capture_output=True, timeout=900)
        self.note = ''

    def unload(self):
        """Free the graphics card for drawing: models Ollama keeps loaded would otherwise share 8 GB with the image model."""
        try:
            for m in call(self.url, '/api/ps').get('models', []):
                call(self.url, '/api/generate', dict(model=m['name'], keep_alive=0), timeout=20)
        except (OSError, ValueError): pass

    def stop(self):
        if self.process and self.process.poll() is None: self.process.terminate()


OLLAMA = Ollama()
