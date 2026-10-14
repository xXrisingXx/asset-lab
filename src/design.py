"""Design: redraw game art with a local image model (FLUX.2 klein via stable-diffusion.cpp) from a prompt.

The current art is placed on a flat chroma background and edited by the model, which keeps its shape and layout. The
background is then keyed out with proper unmixing (so glows and drips keep soft edges), and the result is fitted into
each target sprite's fixed atlas frame. Variants are kept, so the user can pick, nudge, refine and only then apply.
"""
import json
import re
import subprocess
import threading
import time
import uuid
import numpy as np
from PIL import Image
import runtime

KEYS = {'green': (0, 255, 0), 'magenta': (255, 0, 255), 'blue': (0, 64, 255)}
TITLE = r'\b(?:title|logo|wordmark|game name|mitosis (?:text|name|title|logo)|main menu (?:name|text|title)|name)\b'
# Objects, materials and styles that need drawing; plain colors, gradients and glows stay with the theme shader.
VERBS = r'\b(?:add|put|draw|stick|wear(?:ing)?|redesign|re-?draw|restyle|reimagine|turn (?:it|the \w+(?: \w+)?) into|make (?:it|the \w+(?: \w+)?) look like)\b'
DRAWN = (r'\b(?:hats?|horns?|crown|halo|blood(?:y)?|drips?|dripping|bats?|cobwebs?|webs?|skulls?|pumpkins?|ghosts?|eyes|teeth|fangs|cracked|cracks|'
         r'spooky|creepy|scary|horror|halloween|zombie|funny|silly|goofy|cute|evil|christmas|xmas|santa|snow(?:y|ed)?|icicles?|frozen|'
         r'slime|slimy|graffiti|steampunk|cyberpunk|3d|stone|wooden|bone|rusty|melting|burning|on fire)\b')


# Things that only exist once drawn (unlike a color or a mood).
OBJECTS = (r'\b(?:hats?|horns?|crowns?|halos?|wings?|bats?|cobwebs?|skulls?|pumpkins?|ghosts?|eyes|teeth|fangs|mustaches?|moustaches?|'
           r'glasses|sunglasses|bows?|ribbons?|flowers?|antlers?|beards?|scarf|scarves|tiaras?|masks?)\b')


def wants_design(text, unknown=()):
    """A request to redraw art rather than recolor or shade it: something only drawing can do ("spooky", "a hat"), or a
    drawing verb ("add", "redesign") with words the shader does not understand. "Add a rainbow outline" stays a theme."""
    text = text.lower()
    return bool(re.search(DRAWN, text) or (re.search(VERBS, text) and unknown))


def key_for(prompt):
    """A background color the request is unlikely to paint the art with."""
    text = prompt.lower()
    if re.search(r'green|toxic|slime|zombie|lime|grass|leaf|leaves|forest|emerald|mint', text): return 'magenta'
    return 'green'


def canvas_size(w, h):
    """A model canvas (multiples of 16, about 0.45 megapixels) with room around the art for drips, hats and glow."""
    aspect = max(0.4, min(4.0, w / max(1, h)))
    width = 1024 if aspect >= 1 else max(512, round(1024 * aspect / 16) * 16)
    height = max(384, min(1024, round(width / aspect * 1.75 / 16) * 16))
    return width, height


def reference(art, key, size):
    W, H = size
    canvas = Image.new('RGBA', (W, H), (*KEYS[key], 255))
    s = min(W * 0.8 / art.width, H * 0.62 / art.height)
    scaled = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.Resampling.LANCZOS)
    canvas.alpha_composite(scaled, ((W - scaled.width) // 2, (H - scaled.height) // 2))
    return canvas.convert('RGB')


def unkey(image, key, tolerance=1.0):
    """Remove the flat key background. Alpha comes from how far each pixel is from the key color, and partly covered
    pixels are unmixed from it, so a red glow over green becomes a translucent red glow, not a brown fringe."""
    rgb = np.asarray(image.convert('RGB')).astype(np.float32)
    k = np.array(KEYS[key], np.float32)
    # The model's "flat" background drifts a little: measure it from the border, where only background should be.
    border = np.concatenate([rgb[:6].reshape(-1, 3), rgb[-6:].reshape(-1, 3), rgb[:, :6].reshape(-1, 3), rgb[:, -6:].reshape(-1, 3)])
    near = border[np.linalg.norm(border - k, axis=1) < 140]
    if len(near) > 50: k = np.median(near, axis=0)
    distance = np.linalg.norm(rgb - k, axis=2)
    low, high = 38 * tolerance, 150 * tolerance
    alpha = np.clip((distance - low) / max(1.0, high - low), 0, 1)
    # A glow blended over the key is far from the key color yet partly see-through. Assuming the art itself has no key
    # color in it, its coverage lies between max(other channels) / 255 and 1 - spill / 255; a pixel with spill takes the
    # middle of that range, which turns a red-over-green halo back into a translucent red one instead of olive.
    keyed = [i for i in range(3) if KEYS[key][i] > 128]
    other = [i for i in range(3) if KEYS[key][i] <= 128]
    spill = rgb[..., keyed].min(axis=2) - rgb[..., other].max(axis=2)
    upper = 1 - np.clip(spill / 255, 0, 1)
    lower = np.clip(rgb[..., other].max(axis=2) / 255, 0, 1)
    alpha = np.minimum(alpha, np.where(spill > 12 * tolerance, np.minimum(upper, (lower + upper) / 2), 1))
    a = np.maximum(alpha, 1e-3)[..., None]
    color = np.clip((rgb - (1 - a) * k) / a, 0, 255)
    # Spill: a key-colored cast left on edges and glows. The key is chosen to be a color the request does not use, so
    # the key channel may never exceed the others (green key: G <= max(R, B); magenta key: min(R, B) <= G).
    if key == 'magenta':
        cap = np.minimum(color[..., 0], color[..., 2])
        excess = np.maximum(0, cap - color[..., 1])
        color[..., 0] -= excess; color[..., 2] -= excess
    else:
        dominant = int(np.argmax(KEYS[key]))
        others = [i for i in range(3) if i != dominant]
        color[..., dominant] = np.minimum(color[..., dominant], color[..., others].max(axis=2))
    out = np.dstack([color, alpha * 255]).astype(np.uint8)
    out[alpha < 0.02] = 0
    return Image.fromarray(out, 'RGBA')


def core_box(art, share=0.3):
    """The dense part of a drawing (the letters), ignoring sparse extras such as bats or drip tails at its edges."""
    a = np.asarray(art.getchannel('A')) > 40
    rows, cols = a.sum(axis=1), a.sum(axis=0)
    if not rows.any(): return None
    ys, xs = np.nonzero(rows >= rows.max() * share)[0], np.nonzero(cols >= cols.max() * 0.03)[0]
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def fit(art, frame, scale=1.0, dx=0.0, dy=0.0, fill=False):
    """Crop to the drawn content and fit it into a sprite frame (w, h), keeping its proportions.
    fill: size by the dense core instead, so the main shape fills the frame and sparse edges may be trimmed."""
    box = art.getchannel('A').point(lambda v: 255 if v > 10 else 0).getbbox()
    if not box: return Image.new('RGBA', frame, (0, 0, 0, 0))
    if fill:
        core = core_box(art)
        if core:
            w, h = frame
            s = min(w / (core[2] - core[0]), h / (core[3] - core[1])) * max(0.2, min(2.0, scale))
            big = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.Resampling.LANCZOS)
            cx, cy = (core[0] + core[2]) / 2 * s, (core[1] + core[3]) / 2 * s
            out = Image.new('RGBA', frame, (0, 0, 0, 0))
            out.paste(big, (round(w / 2 - cx + dx * w / 2), round(h / 2 - cy + dy * h / 2)), big)
            return out
    art = art.crop(box)
    w, h = frame
    s = min(w / art.width, h / art.height) * max(0.2, min(2.0, scale))
    art = art.resize((max(1, round(art.width * s)), max(1, round(art.height * s))), Image.Resampling.LANCZOS)
    out = Image.new('RGBA', frame, (0, 0, 0, 0))
    out.alpha_composite(art, (round((w - art.width) / 2 + dx * w / 2), round((h - art.height) / 2 + dy * h / 2))) if art.width <= w and art.height <= h else \
        out.paste(art, (round((w - art.width) / 2 + dx * w / 2), round((h - art.height) / 2 + dy * h / 2)), art)
    return out


def keep_shape(art, original, grow=2):
    """Trim drawn art to the original sprite's silhouette (grown a little so soft edges survive)."""
    from PIL import ImageFilter
    mask = original.getchannel('A').point(lambda v: 255 if v > 24 else 0).filter(ImageFilter.MaxFilter(grow * 2 + 1)).filter(ImageFilter.GaussianBlur(1))
    alpha = Image.fromarray((np.asarray(art.getchannel('A'), np.float32) * np.asarray(mask, np.float32) / 255).astype(np.uint8))
    out = art.copy(); out.putalpha(alpha)
    return out

def tinted(image):
    """True for a plain white glyph: the game multiplies those by a UI color, so a colorful redraw comes out tinted."""
    a = np.asarray(image.convert('RGBA')).astype(np.float32) / 255
    solid = a[..., 3] > 0.5
    if solid.sum() < 16: return False
    rgb = a[..., :3][solid]
    return bool(rgb.min(axis=1).mean() > 0.85 and (rgb.max(axis=1) - rgb.min(axis=1)).mean() < 0.08)


def leftover(art):
    """A warning when the model ignored the flat background: the cut-out then keeps lots of solid pixels at the canvas edge."""
    a = np.asarray(art.getchannel('A'))
    band = max(2, min(a.shape) // 30)
    edge = np.concatenate([a[:band].ravel(), a[-band:].ravel(), a[:, :band].ravel(), a[:, -band:].ravel()])
    share = float((edge > 128).mean())
    return 'The background did not cut out cleanly; try another variant or a lower cut-out.' if share > 0.2 else ''


class Designer:
    def __init__(self, lab, themes):
        self.lab, self.themes = lab, themes
        self.folder = lab.root / 'designs'
        self.jobs, self.lock = {}, threading.Lock()

    def status(self):
        lacking = runtime.missing('sd', 'diffusion', 'llm', 'vae')
        return dict(available=not lacking, missing=lacking, job=next((dict(j) for j in self.jobs.values() if j['state'] == 'running'), None))

    # ----- what to redraw -----------------------------------------------------------------------
    def title_rows(self):
        """The MITOSIS title as the main menu draws it (not Strike.is's logo in theme2), one per distinct frame."""
        rows = [r for r in self.lab.catalog.rows.values() if r['name'] == 'logo' and r.get('primary', True) and not r.get('legacy')
                and self.lab.catalog.rows.get(r.get('parent', ''), {}).get('name') in ('theme.png', 'theme4.png')]
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: (r['source'].startswith('assets/assets.zip'), r['height'])):
            if r.get('dup') in seen: continue
            seen.add(r.get('dup')); out.append(r)
        return out

    def targets(self, prompt, only=None):
        if only:
            rows = [self.lab.row(k) for k in only if k in self.lab.catalog.rows]
            return [r for r in rows if r['editable']][:6]
        if re.search(TITLE, prompt.lower()): return self.title_rows()
        names = {n for found in self.themes.named(prompt)[0].values() for n in found}
        if not names: return self.family_rows(prompt)
        rows = [r for r in self.lab.catalog.rows.values() if r['name'] in names and r['editable'] and r.get('primary', True) and not r.get('legacy')]
        seen, out = set(), []
        for r in rows:
            if r.get('dup') in seen: continue
            seen.add(r.get('dup')); out.append(r)
        return out[:6]

    def family_rows(self, prompt, limit=6):
        """A group named in the request ("the cells", "every icon"): its first few sprites the game actually uses, one per art."""
        import theme as shading   # imported here: theme imports this module
        text = prompt.lower()
        wanted = [f for f, words in shading.WORDS.items() if f != 'everything' and re.search(rf'\b(?:{words})\b', text)]
        if not wanted: return []
        rows, seen = [], set()
        for r in self.lab.catalog.rows.values():
            if not r['editable'] or not r.get('primary', True) or r.get('legacy') or r.get('removed') or r.get('dup') in seen: continue
            if r['width'] < 24 or r['height'] < 24 or self.lab.catalog.children.get(r['key']): continue   # whole sheets and specks are not drawn
            if shading.families_of(r, self.lab.catalog.rows.get(r.get('parent', ''), {}).get('name', '')) in wanted:
                seen.add(r.get('dup')); rows.append(r)
        rows.sort(key=lambda r: (not r.get('live'), r['name']))   # sprites the game names in its code first
        return rows[:limit]

    # ----- generating ---------------------------------------------------------------------------
    def start(self, prompt, keys=None, count=2, base=None, direction=None):
        """base: (job id, variant index) to refine a variant instead of the current art."""
        prompt = str(prompt or '').strip()[:800]
        if not prompt: raise ValueError('Describe the design you want.')
        lacking = self.status()['missing']
        if lacking: raise ValueError('The image model is not installed: ' + ', '.join(lacking))
        if base:
            parent = self.job(base[0])
            rows = [self.lab.row(k) for k in parent['targets']]
        else:
            rows = self.targets(prompt, keys)
        if not rows: raise ValueError('Name what to redesign, or check it in the library first.')
        # One drawing per sprite: different sprites are drawn one at a time (the title's sizes share one drawing).
        rows = [r for r in rows if r['name'] == rows[0]['name']]
        with self.lock:
            if any(j['state'] == 'running' for j in self.jobs.values()): raise ValueError('A design is already being drawn.')
            job = dict(id=uuid.uuid4().hex, state='running', prompt=prompt, targets=[r['key'] for r in rows], names=[r.get('label') or r['name'] for r in rows],
                       frames=[[r['width'], r['height']] for r in rows], variants=[], message='Preparing…', progress=0, total=max(1, min(4, int(count))),
                       key=parent['key'] if base else key_for(prompt), base=base, at=time.time(), title=rows[0]['name'] == 'logo',
                       # Button and panel skins are stretched by the game, so their outline should stay; white icons are tinted by it.
                       stretched=rows[0]['group'] == 'Buttons & panels' and rows[0]['name'] != 'logo', tinted=tinted(self.lab.catalog.image(rows[0]['key'])))
            if direction: job['direction'] = str(direction)[:700]   # shared by a set drawn together, so they match
            self.jobs[job['id']] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return dict(job)

    @staticmethod
    def subject(job):
        return 'the game title logo that reads MITOSIS (the O is a round dot)' if job['title'] else f'a game sprite ({job["names"][0]})'

    def instruction(self, job):
        what = self.subject(job)
        keep = 'Keep the exact word MITOSIS, spelled the same, in the same layout and size, centered.' if job['title'] else 'Keep its outline, size and position, centered.'
        small = '' if job['title'] or max(job['frames'][0]) > 160 else ' It is shown small in the game, so use bold, simple shapes and strong contrast that read at a glance.'
        color = {'green': 'pure green (#00FF00)', 'magenta': 'pure magenta (#FF00FF)', 'blue': 'pure blue (#0040FF)'}[job['key']]
        return (f'{job.get("direction") or job["prompt"]}. Edit {what}. {keep}{small} Crisp, high quality game UI art. '
                f'The background must stay a perfectly flat solid {color} with nothing else on it: no scenery, no shadow, no frame, no extra text.')

    def _run(self, job):
        folder = self.folder / job['id']
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if job['base']:
                # Refining: the chosen variant, exactly as drawn (same canvas and background), is the new starting point.
                ref = Image.open(self.folder / job['base'][0] / f'raw-{int(job["base"][1])}.png').convert('RGB')
                size = ref.size
            else:
                source = self.lab.image(job['targets'][0])
                size = canvas_size(source.width, source.height)
                ref = reference(source, job['key'], size)
            ref.save(folder / 'reference.png')
            if not job['base'] and not job.get('direction') and getattr(self, 'director', None):
                # A short request ("spooky") becomes a concrete art direction the image model can follow.
                job['message'] = 'Writing the art direction…'
                try: job['direction'] = (self.director(job['prompt'], self.subject(job)) or '').strip()[:700]
                except Exception: job['direction'] = ''
            runtime.OLLAMA.unload()  # the planner and the image model do not both fit on an 8 GB card
            job['message'] = 'Loading the image model…'
            args = [str(runtime.path('sd')), '--diffusion-model', str(runtime.path('diffusion')), '--vae', str(runtime.path('vae')), '--llm', str(runtime.path('llm')),
                    '-r', str(folder / 'reference.png'), '-p', self.instruction(job), '--cfg-scale', '1.0', '--steps', '4', '--sampling-method', 'euler',
                    '-W', str(size[0]), '-H', str(size[1]), '-s', str(int(time.time()) % 100000), '-b', str(job['total']), '-o', str(folder / 'raw-%d.png')]
            proc = subprocess.Popen(args, cwd=str(runtime.path('sd').parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=runtime.QUIET,
                                    text=True, encoding='utf-8', errors='replace')
            job['pid'] = proc.pid
            log = []
            for line in proc.stdout:
                log.append(line.rstrip()); log[:] = log[-40:]
                if 'loading tensors' in line: job['message'] = 'Loading the image model…'
                step = re.search(r'\|\s*(\d+)/(\d+)\s*-', line)
                if step and 'MB/s' not in line: job['message'] = f'Drawing {job["total"]} variant{"s" if job["total"] > 1 else ""}: step {step.group(1)} of {step.group(2)}'
                if 'decoding' in line: job['message'] = 'Finishing the images…'
            proc.wait(timeout=1200)
            if job.get('cancelled'): raise RuntimeError('Cancelled.')
            raws = sorted(folder.glob('raw-*.png'), key=lambda p: int(re.search(r'(\d+)', p.stem).group(1)))
            if proc.returncode != 0 or not raws: raise RuntimeError('The image model failed: ' + ' / '.join(l for l in log[-6:] if l.strip())[:400])
            for index, raw in enumerate(raws):
                art = unkey(Image.open(raw), job['key'])
                art.save(folder / f'variant-{index}.png')
                job['variants'].append(dict(index=index, settings=dict(scale=1.0, dx=0.0, dy=0.0, tolerance=1.0, shape=1.0 if job.get('stretched') else 0.0, fill=1.0 if job['title'] else 0.0),
                                            warning=leftover(art)))
                job['progress'] = index + 1
            job.update(state='complete', message=f'{len(raws)} variant{"s" if len(raws) != 1 else ""} ready. Pick one, adjust it, then apply.')
        except Exception as exc:  # a background job must always finish
            job.update(state='failed', message=str(exc)[:500])
        finally:
            job.pop('pid', None)
            (folder / 'job.json').write_text(json.dumps({k: v for k, v in job.items()}, indent=1), encoding='utf-8') if folder.is_dir() else None

    def cancel(self, job_id):
        job = self.job(job_id)
        job['cancelled'] = True
        if job.get('pid'): subprocess.run(['taskkill.exe', '/PID', str(job['pid']), '/T', '/F'], capture_output=True, creationflags=runtime.QUIET)
        return dict(job)

    def job(self, job_id):
        if job_id in self.jobs: return self.jobs[job_id]
        if re.fullmatch(r'[a-f0-9]{32}', str(job_id)) and (self.folder / job_id / 'job.json').is_file():
            self.jobs[job_id] = json.loads((self.folder / job_id / 'job.json').read_text(encoding='utf-8'))
            return self.jobs[job_id]
        raise ValueError('Unknown design')

    # ----- reviewing and applying ---------------------------------------------------------------
    def render(self, job_id, index, target=0, settings=None, raw=False):
        job = self.job(job_id)
        folder = self.folder / job['id']
        if raw: return Image.open(folder / f'raw-{int(index)}.png').convert('RGBA')
        s = {'scale': 1.0, 'dx': 0.0, 'dy': 0.0, 'tolerance': 1.0, 'shape': 1.0 if job.get('stretched') else 0.0, 'fill': 1.0 if job['title'] else 0.0, **(settings or {})}
        art = unkey(Image.open(folder / f'raw-{int(index)}.png'), job['key'], float(s['tolerance'])) if float(s['tolerance']) != 1.0 else \
            Image.open(folder / f'variant-{int(index)}.png').convert('RGBA')
        frame = tuple(job['frames'][int(target)])
        if float(s['shape']) >= 0.5:
            # Keep the original outline: line the drawing up with the original's content box, then trim gently to its shape.
            original = self.lab.catalog.image(job['targets'][int(target)]).convert('RGBA')
            box = original.getchannel('A').point(lambda v: 255 if v > 24 else 0).getbbox()
            if original.size == frame and box:
                inner = fit(art, (box[2] - box[0], box[3] - box[1]), float(s['scale']), float(s['dx']), float(s['dy']))
                out = Image.new('RGBA', frame, (0, 0, 0, 0)); out.alpha_composite(inner, box[:2])
                return keep_shape(out, original, grow=max(2, round(min(frame) * 0.05)))
        return fit(art, frame, float(s['scale']), float(s['dx']), float(s['dy']), fill=float(s['fill']) >= 0.5)

    def apply(self, job_id, index, settings=None):
        job = self.job(job_id)
        if job['state'] != 'complete': raise ValueError('This design is not finished.')
        items = []
        with self.lab.lock:
            for target, key in enumerate(job['targets']):
                before = self.lab.revision(key)
                self.lab.commit(key, self.render(job_id, index, target, settings), dict(design=job['id'], prompt=job['prompt'], variant=int(index), settings=settings or {}), persist=False)
                items.append(dict(key=key, before=before, after=self.lab.revision(key)))
            self.lab.persist()
        job['applied'] = dict(index=int(index), items=items, at=time.time())
        (self.folder / job['id'] / 'job.json').write_text(json.dumps(job, indent=1), encoding='utf-8')
        return dict(job=job['id'], items=items, names=job['names'])

    def undo(self, job_id):
        job = self.job(job_id)
        applied = job.get('applied')
        if not applied: raise ValueError('This design was not applied.')
        reverted = 0
        with self.lab.lock:
            for item in applied['items']:
                if self.lab.revision(item['key']) == item['after']:
                    self.lab.step(item['key'], item['after'], -1, detail=False); reverted += 1
            self.lab.persist()
        job.pop('applied', None)
        (self.folder / job['id'] / 'job.json').write_text(json.dumps(job, indent=1), encoding='utf-8')
        return dict(reverted=reverted)
