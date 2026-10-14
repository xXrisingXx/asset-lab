"""Find sprites that look like a given image, so the many versions of one potion (or any art) can be found together.

Each sprite gets a small fingerprint: its visible shape and brightness pattern at 16x16 after trimming
transparent margins, its color layout at 4x4, and its aspect ratio. Resized copies match closely;
recolored copies still match on shape. This is a visual likeness measure, not proof that two sprites
are the same game item.
"""
import base64
import io
import threading
import uuid
import numpy as np
from PIL import Image

SIZE = 16


def fingerprint(image):
    """image: a PIL image or an RGBA array -> (shape vector, color vector, aspect) or None for empty art."""
    image = Image.fromarray(image, 'RGBA') if isinstance(image, np.ndarray) else image.convert('RGBA')
    box = image.getchannel('A').point(lambda v: 255 if v > 24 else 0).getbbox()
    if not box: return None
    image = image.crop(box)
    aspect = float(np.log(image.width / image.height))
    small = np.asarray(image.resize((SIZE, SIZE), Image.Resampling.BILINEAR), dtype=np.float32) / 255
    alpha = small[..., 3]
    light = (small[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)) * alpha
    shape = np.concatenate([alpha.ravel(), light.ravel()])
    shape = shape - shape.mean()
    norm = float(np.linalg.norm(shape))
    shape = shape / norm if norm > 1e-6 else shape
    tiny = np.asarray(image.resize((4, 4), Image.Resampling.BOX), dtype=np.float32) / 255
    color = (tiny[..., :3] * tiny[..., 3:4]).ravel()
    return shape.astype(np.float32), color.astype(np.float32), aspect


class Index:
    def __init__(self, catalog):
        self.catalog, self.lock, self.keys, self.uploads = catalog, threading.Lock(), None, {}

    def build(self):
        """Fingerprint every editable, non-leftover sprite once (a few seconds), then keep it in memory."""
        with self.lock:
            if self.keys is not None: return
            keys, shapes, colors, aspects = [], [], [], []
            for sheet_key, sheet in self.catalog.rows.items():
                if sheet['kind'] != 'image' or not sheet['editable']: continue
                pixels = np.asarray(self.catalog._sheet(sheet_key))
                children = [self.catalog.rows[k] for k in self.catalog.children.get(sheet_key, [])]
                for row in [sheet] + [c for c in children if not c.get('legacy')]:
                    x, y, w, h = row.get('rect') or (0, 0, pixels.shape[1], pixels.shape[0])
                    crop = pixels[y:y+h, x:x+w]
                    if row.get('rotated'): crop = np.rot90(crop, -1)
                    found = fingerprint(np.ascontiguousarray(crop))
                    if found is None: continue
                    keys.append(row['key']); shapes.append(found[0]); colors.append(found[1]); aspects.append(found[2])
            self.position = {key: i for i, key in enumerate(keys)}
            self.shapes, self.colors, self.aspects = np.stack(shapes), np.stack(colors), np.array(aspects, np.float32)
            self.keys = keys

    def upload(self, data):
        """Fingerprint an image the user dropped or pasted; returns an id usable as 'upload:<id>'."""
        if isinstance(data, str): data = base64.b64decode(data.split(',', 1)[-1], validate=False)
        if len(data) > 24 * 1024 * 1024: raise ValueError('Use an image under 24 MB.')
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 4096 * 4096: raise ValueError('Use an image of at most 4096 x 4096.')
                image.load()
                found = fingerprint(image)
        except (OSError, Image.DecompressionBombError) as exc: raise ValueError('That file is not an image the lab can read.') from exc
        if found is None: raise ValueError('The image is completely transparent.')
        token = uuid.uuid4().hex[:12]
        self.uploads[token] = found
        while len(self.uploads) > 20: self.uploads.pop(next(iter(self.uploads)))
        return token

    def query(self, like):
        self.build()
        kind, _, value = like.partition(':')
        if kind == 'upload' and value in self.uploads: return self.uploads[value]
        if kind == 'key' and value in self.position:
            i = self.position[value]
            return self.shapes[i], self.colors[i], float(self.aspects[i])
        raise ValueError('That image is not available for a similarity search. Try another one.')

    def rank(self, target, keys):
        """{key: distance} for the given keys that have a fingerprint; 0 is identical, around 1 is unrelated."""
        self.build()
        shape, color, aspect = target
        index = [self.position[k] for k in keys if k in self.position]
        if not index: return {}
        rows = np.array(index)
        outline = 1 - self.shapes[rows] @ shape                    # 0 same, 1 unrelated, 2 inverted
        tone = np.abs(self.colors[rows] - color).mean(axis=1) * 2  # 0 same colors, about 1 very different
        stretch = np.minimum(np.abs(self.aspects[rows] - aspect), 1.5)
        distance = outline * 0.7 + tone * 0.2 + stretch * 0.25
        return {self.keys[i]: float(d) for i, d in zip(index, distance)}
