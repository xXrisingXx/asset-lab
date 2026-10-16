"""Game sounds and music for Asset Lab: decode a sound file, trim it, fade it, set its volume or match the loudness of
the game's own sound, and encode the OGG Vorbis files Mitosis plays.

Sounds can also come from a link. A direct audio file is downloaded here over HTTP(S), to public internet addresses
only. A web page (YouTube, SoundCloud, Bandcamp…) goes through the optional link helper: yt-dlp, the Deno JavaScript
runtime it needs for YouTube, and PyAV to decode what websites serve (AAC, Opus, WebM, MP4). The lab downloads them
once, after the player agrees, from the official sources listed under "tools" in bootstrap.json, each checked against
its size and SHA-256. yt-dlp fetches pages itself, so it runs behind the lab's gate (_Gate, a proxy on this PC): every
connection it makes, after any redirect or DNS answer, goes to a checked public address too, and its downloads are capped.

Loudness follows ITU-R BS.1770: K-weighting, 400 ms blocks with 75 % overlap, an absolute gate at -70 LUFS and a
relative gate 10 LU below the level of the blocks above it. Game sounds shorter than 2 s are measured with 100 ms blocks,
so the gate still finds a click in a mostly silent file; a replacement is always measured the same way as the original.
"""
import collections
import hashlib
import http.client
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import _socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
import numpy as np
import runtime

ROOT = Path(__file__).resolve().parent
TOOLS = Path(runtime.ROOT) / 'tools'          # the link helper: yt-dlp.exe, deno/, pylib/ (PyAV)
MANIFEST = ROOT.parent / 'bootstrap.json'      # its download list, beside the Python and wheel pins
MAX_BYTES = 150 * 1024 * 1024                  # a sound file or a download
MAX_SECONDS = 20 * 60                          # longest sound kept: the first 20 minutes of anything longer
# The most sound kept in memory: 20 minutes of 48 kHz stereo (460 MB of samples). A small file can hold far more (an
# hour of 8-channel silence at 192 kHz compresses to a few MB), so a sound above 48 kHz keeps less than 20 minutes.
MAX_SAMPLES = MAX_SECONDS * 48000 * 2
LINK_SECONDS = 30 * 60                         # longer videos and live streams are refused before downloading
RATES = (22050, 44100, 48000)                  # the sample rates of the game's own sounds
CEILING = 10 ** (-1 / 20)                      # peaks stay below -1 dBFS
QUALITY = 0.5                                  # Vorbis quality 0.1-1.0 (0.5 is about 160 kbit/s for stereo music)
QUIET = 0x08000000 if os.name == 'nt' else 0   # no console window for the helper
AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AssetLab'
# Sites whose links are pages, never sound files: straight to the link helper without fetching the page first.
PAGES = ('youtube.com', 'youtu.be', 'youtube-nocookie.com', 'soundcloud.com', 'bandcamp.com', 'vimeo.com', 'dailymotion.com',
         'twitch.tv', 'tiktok.com', 'mixcloud.com', 'audiomack.com', 'newgrounds.com', 'x.com', 'twitter.com', 'instagram.com', 'facebook.com')
NEEDS_HELPER = ('mp4', 'webm', 'wma', 'aac', 'caf', 'amr', 'video')
_TEST_ALLOW = frozenset()   # tests only: 'ip:port' of a test server on this PC that fetch() may reach. Never set by the lab.


class AudioError(ValueError):
    """A problem the player can act on. code tells the editor what to offer: needs_helper, outdated, cancelled, ..."""
    def __init__(self, message, code=''):
        super().__init__(message)
        self.code = code


def _soundfile():
    try:
        import importlib.util, types, typing
        if 'typing_extensions' not in sys.modules and importlib.util.find_spec('typing_extensions') is None:
            # soundfile 0.14 imports typing_extensions only for Self, which typing has had since Python 3.11. The portable
            # Python the launcher sets up has no typing_extensions, so names are served from typing instead.
            shim = types.ModuleType('typing_extensions')
            shim.__getattr__ = lambda name: getattr(typing, name)
            sys.modules['typing_extensions'] = shim
        import soundfile
        return soundfile
    except (ImportError, OSError) as exc:
        raise AudioError('Sound editing is not set up on this PC yet. Start Asset Lab from its launcher once more to finish the setup.', 'setup') from exc


_DLL = []
def _pyav(tools=None):
    """PyAV from the link helper's pylib folder, put on the import path only when a sound needs it."""
    lib = Path(tools or TOOLS) / 'pylib'
    if not (lib / 'av').is_dir(): raise AudioError('This file type needs the link helper.', 'needs_helper')
    if str(lib) not in sys.path: sys.path.append(str(lib))
    for folder in (lib / 'av.libs', lib / 'av'):   # FFmpeg's DLLs ship beside the module
        if os.name == 'nt' and folder.is_dir() and str(folder) not in [str(d[0]) for d in _DLL]:
            try: _DLL.append((folder, os.add_dll_directory(str(folder))))
            except OSError: pass
    try:
        import av
        return av
    except (ImportError, OSError) as exc:
        raise AudioError('The link helper’s sound decoder did not start (' + str(exc) + '). Install the link helper again.', 'helper_broken') from exc


def mb(n): return f'{n / 1e6:.1f} MB' if n >= 1e5 else f'{max(1, round(n / 1e3))} KB'


def clock(seconds):
    """m:ss.s, or m:ss.ss under a second (a 0.15 s click is not '0:00.1')."""
    seconds = max(0.0, float(seconds))
    return f'{int(seconds // 60)}:{seconds % 60:05.2f}' if seconds < 1 else f'{int(seconds // 60)}:{seconds % 60:04.1f}'


# ----- reading sound files ----------------------------------------------------------------------------------------
def sniff(head):
    """What a file is from its first bytes: ogg, wav, flac, mp3, aiff, mp4, webm, wma, aac, caf, amr, video, html or ''."""
    h = bytes(head[:64])
    if h[:4] == b'OggS': return 'ogg'
    if h[:4] == b'RIFF' and h[8:12] == b'WAVE': return 'wav'
    if h[:4] == b'RIFF' and h[8:12] == b'AVI ': return 'video'
    if h[:4] == b'fLaC': return 'flac'
    if h[:3] == b'ID3': return 'mp3'
    if h[:4] == b'FORM' and h[8:12] in (b'AIFF', b'AIFC'): return 'aiff'
    if h[4:8] == b'ftyp': return 'mp4'
    if h[:4] == b'\x1a\x45\xdf\xa3': return 'webm'
    if h[:16] == bytes.fromhex('3026b2758e66cf11a6d900aa0062ce6c'): return 'wma'
    if h[:4] == b'caff': return 'caf'
    if h[:5] == b'#!AMR': return 'amr'
    if len(h) > 1 and h[0] == 0xFF and h[1] & 0xF6 == 0xF0: return 'aac'           # ADTS frame
    if len(h) > 1 and h[0] == 0xFF and h[1] & 0xE0 == 0xE0 and h[1] & 0x06: return 'mp3'   # MPEG audio frame
    text = h.lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    if text[:1] == b'<' or text.startswith((b'#extm3u', b'{')): return 'html'
    return ''


def _finite(samples):
    np.nan_to_num(samples, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return samples


def decode(source, tools=None, limit=MAX_SECONDS, info=None):
    """(float32 samples [frames, channels], sample rate) of a sound file given as bytes or a path.

    soundfile (libsndfile) reads WAV, FLAC, OGG Vorbis/Opus, MP3 and AIFF. Anything else (AAC, M4A, WebM, MP4…) is
    decoded by PyAV when the link helper is installed. Only the first `limit` seconds are kept; `info` (a dict) gets
    'cut' (the full length in seconds) when something was left out, and 'codec'."""
    info = {} if info is None else info
    data = bytes(source) if isinstance(source, (bytes, bytearray, memoryview)) else None
    path = None if data is not None else Path(source)
    if data is None:
        with path.open('rb') as f: head = f.read(64)
    else: head = data[:64]
    if not head: raise AudioError('This file is empty.', 'not_audio')
    kind = sniff(head)
    if kind == 'html': raise AudioError('Not a sound file.', 'not_audio')
    sf = _soundfile()
    if kind not in NEEDS_HELPER:
        try:
            with sf.SoundFile(io.BytesIO(data) if data is not None else str(path)) as f:
                return _read_soundfile(f, limit, info)
        except RuntimeError: pass   # libsndfile does not know this format: PyAV may
    try: av = _pyav(tools)
    except AudioError as exc:
        if exc.code == 'needs_helper' and kind in NEEDS_HELPER: raise
        if exc.code == 'needs_helper': raise AudioError('Not a sound file the lab can open.' + (' It may be damaged.' if kind else ''), 'not_audio') from None
        raise
    return _read_pyav(av, data, path, limit, info)


def _downmix(x):
    """Stereo from more channels (left from the even ones, right from the odd ones), as the sound is kept."""
    return np.stack([x[:, 0::2].mean(axis=1), x[:, 1::2].mean(axis=1)], axis=1).astype(np.float32)


def _read_soundfile(f, limit, info):
    rate, channels = f.samplerate, f.channels
    if not (1 <= channels <= 64 and 1000 <= rate <= 768000): raise AudioError('Not a sound file the lab can open.', 'not_audio')
    # More than two channels are mixed down block by block, and the memory budget (MAX_SAMPLES) bounds what is read.
    keep, blocks, total = int(min(limit * rate, MAX_SAMPLES // min(channels, 2), 4 * MAX_SAMPLES // channels)), [], 0
    step = max(4096, (1 << 22) // channels)
    while total < keep:
        block = f.read(min(step, keep - total), dtype='float32', always_2d=True)
        if not len(block): break
        blocks.append(_downmix(block) if channels > 2 else block)
        total += len(block)
    if total >= keep and len(f.read(1, dtype='float32', always_2d=True)): info['cut'] = round(max(f.frames, keep + 1) / rate, 1)
    info.setdefault('codec', f.format)
    if not blocks: raise AudioError('This sound is empty.', 'not_audio')
    return _finite(np.concatenate(blocks) if len(blocks) > 1 else blocks[0]), rate


def _read_pyav(av, data, path, limit, info):
    try: container = av.open(io.BytesIO(data) if data is not None else str(path), 'r')
    except Exception as exc: raise AudioError('Not a sound file the lab can open.', 'not_audio') from exc   # PyAV raises its own error types
    with container:
        if not container.streams.audio: raise AudioError('This file has no sound in it.', 'not_audio')
        stream = container.streams.audio[0]
        ctx = stream.codec_context
        rate_in = int(ctx.sample_rate or stream.rate or 0)
        channels = int(getattr(ctx, 'channels', 0) or len(getattr(getattr(ctx, 'layout', None), 'channels', ())) or 2)
        if not 1000 <= rate_in <= 768000: raise AudioError('Not a sound file the lab can open.', 'not_audio')
        rate = target_rate(rate_in)
        resampler = av.AudioResampler(format='fltp', layout='mono' if channels == 1 else 'stereo', rate=rate)
        keep, blocks, total, more = int(limit * rate), [], 0, False
        def take(frames):
            nonlocal total
            for frame in frames:
                a = frame.to_ndarray()
                a = a.reshape(1, -1) if a.ndim == 1 else a
                blocks.append(np.ascontiguousarray(a.T, np.float32))
                total += a.shape[1]
        try:
            for frame in container.decode(stream):
                take(resampler.resample(frame))
                if total >= keep: more = True; break
            if not more: take(resampler.resample(None))
        except Exception as exc:   # a damaged file: keep what decoded, if anything
            if not total: raise AudioError('This sound file is damaged.', 'not_audio') from exc
            info['damaged'] = True
        if more and container.duration: info['cut'] = round(container.duration / 1e6, 1)
        info.setdefault('codec', (ctx.name or 'audio').upper())
    if not blocks: raise AudioError('This sound is empty.', 'not_audio')
    return _finite(np.concatenate(blocks)[:keep]), rate


def describe(samples, rate):
    return dict(duration=round(len(samples) / rate, 3), rate=int(rate), channels=int(samples.shape[1]), frames=int(len(samples)))


def _peak(samples):
    return max(float(samples.max()), -float(samples.min())) if samples.size else 0.0


def peak_db(samples):
    peak = _peak(samples)
    return round(20 * math.log10(peak), 2) if peak > 0 else None


def peaks(samples, n=1000):
    """Highest level (0..1) in each of n columns, for drawing a waveform."""
    if not len(samples): return []
    level = np.abs(samples[:, 0])
    for c in range(1, samples.shape[1]): np.maximum(level, np.abs(samples[:, c]), out=level)
    edges = np.linspace(0, len(level), min(n, len(level)) + 1).astype(np.int64)[:-1]
    return [round(float(v), 4) for v in np.maximum.reduceat(level, edges)]


# ----- channels, sample rate --------------------------------------------------------------------------------------
def target_rate(rate):
    """22.05, 44.1 or 48 kHz like the game's sounds: those are kept, higher rates come down, odd ones go up."""
    if rate in RATES: return int(rate)
    if rate > 48000: return 44100 if rate % 44100 == 0 else 48000
    return next(r for r in RATES if r >= rate)


def to_channels(samples, channels):
    """A copy with `channels` channels: stereo is mixed down for a mono game sound, mono doubled for a stereo one."""
    if not channels or samples.shape[1] == channels: return np.array(samples, np.float32)
    if channels == 1: return samples.mean(axis=1, keepdims=True, dtype=np.float32)
    if samples.shape[1] == 1: return np.repeat(samples, channels, axis=1).astype(np.float32, copy=False)
    return np.array(samples[:, :channels], np.float32)


def resample(samples, rate, new_rate, half=32):
    """Band-limited resampling with a Kaiser-windowed sinc (64 taps at the lower rate, about -80 dB stopband),
    computed block by block so a long song stays within memory."""
    if rate == new_rate or not len(samples): return np.asarray(samples, np.float32)
    g = math.gcd(int(rate), int(new_rate))
    up, down = int(new_rate) // g, int(rate) // g          # output m sits at input position m * down / up
    scale = min(1.0, new_rate / rate)                       # going down: a wider kernel and a lower cutoff
    width = int(math.ceil(half / scale))
    cutoff, beta = 0.5 * scale * 0.95, 8.6                  # cycles per input sample, just under the lower Nyquist
    offsets = np.arange(-width + 1, width + 1)
    def weights(frac):
        d = frac[:, None] - offsets[None, :]
        w = 2 * cutoff * np.sinc(2 * cutoff * d) * np.i0(beta * np.sqrt(np.clip(1 - (d / width) ** 2, 0, 1))) / np.i0(beta)
        return (w / w.sum(axis=1, keepdims=True)).astype(np.float32)   # every phase passes DC unchanged
    table = weights(np.arange(up) / up) if up <= 4096 else None
    x = np.pad(np.asarray(samples, np.float32), ((width + 1, width + 1), (0, 0)))
    n_out = len(samples) * up // down
    out = np.empty((n_out, samples.shape[1]), np.float32)
    if up == 1:   # a whole-number step down (96 → 48 kHz, 88.2 → 44.1 kHz): one filter, every down-th window
        for c in range(samples.shape[1]):
            windows = np.lib.stride_tricks.sliding_window_view(x[:, c], len(offsets))[2::down]
            for m0 in range(0, n_out, 1 << 16): out[m0:m0 + (1 << 16), c] = windows[m0:min(n_out, m0 + (1 << 16))] @ table[0]
        return out
    for m0 in range(0, n_out, 4096):
        m = np.arange(m0, min(n_out, m0 + 4096), dtype=np.int64)
        base, phase = np.divmod(m * down, up)
        w = table[phase] if table is not None else weights(phase / up)
        out[m0:m0 + len(m)] = np.einsum('nt,ntc->nc', w, x[base[:, None] + offsets[None, :] + width + 1])
    return out


def normalize(samples, rate):
    """How a chosen sound is kept: at most two channels, at a rate the game plays, finite, no louder than full scale."""
    samples = np.asarray(samples, np.float32)
    if samples.ndim == 1: samples = samples[:, None]
    if samples.shape[1] > 2: samples = _downmix(samples)
    rate, new = int(rate), target_rate(int(rate))
    if new != rate: samples = resample(samples, rate, new)
    samples = np.ascontiguousarray(samples, np.float32)
    samples = _finite(samples if samples.flags.writeable else samples.copy())
    peak = _peak(samples)
    if peak > 1: samples *= np.float32(1 / peak)
    return samples, new


# ----- loudness ---------------------------------------------------------------------------------------------------
def _kfilter(rate):
    """The BS.1770 K-weighting (a high shelf, then a high pass) as two biquads designed for any rate, as libebur128."""
    k, q = math.tan(math.pi * 1681.974450955533 / rate), 0.7071752369554196
    vh = 10 ** (3.999843853973347 / 20)
    vb, a0 = vh ** 0.4996667741545416, 1 + k / q + k * k
    shelf = ([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0], [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    k, q = math.tan(math.pi * 38.13547087602444 / rate), 0.5003270373238773
    a0 = 1 + k / q + k * k
    return shelf, ([1, -2, 1], [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])


def _fast(n):
    """The smallest 2^a·3^b·5^c at or above n: a size numpy's FFT is quick at."""
    best, p5 = 1 << max(0, (n - 1).bit_length()), 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            p = p35
            while p < n: p *= 2
            best, p35 = min(best, p), p35 * 3
        p5 *= 5
    return best


def kweighted(samples, rate):
    """K-weighted power of each frame, summed over the channels (BS.1770 weighs left and right 1.0).
    The filter runs as its impulse response (8192 taps, long after it has died away) by FFT, block by block."""
    n = len(samples)
    out = np.zeros(n, np.float32)
    if not n: return out
    size = 1 << 15
    w = 2 * np.pi * np.arange(size // 2 + 1) / size
    z = np.exp(-1j * w)
    h = np.ones_like(z)
    for b, a in _kfilter(rate): h *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    ir = np.fft.irfft(h, size)[:8192].astype(np.float32)
    chunk = 1 << 17
    fft = _fast(chunk + len(ir) - 1)
    spectrum = np.fft.rfft(ir, fft)
    for c in range(samples.shape[1]):
        y = np.zeros(n + len(ir) - 1, np.float32)
        for i in range(0, n, chunk):
            seg = np.fft.irfft(np.fft.rfft(samples[i:i + chunk, c], fft) * spectrum, fft)[:min(chunk, n - i) + len(ir) - 1]
            y[i:i + len(seg)] += seg
        y = y[:n]
        out += y * y
    return out


def loudness(kw2, rate, block=0.4):
    """Integrated loudness in LUFS (gated BS.1770) of a kweighted() signal; -inf for silence."""
    n, size = len(kw2), max(1, int(round(block * rate)))
    if not n: return -math.inf
    if n < size: means = np.array([float(np.mean(kw2, dtype=np.float64))])   # shorter than one block: the whole sound
    else:
        c = np.concatenate([[0.0], np.cumsum(kw2, dtype=np.float64)])
        starts = np.arange(0, n - size + 1, max(1, size // 4))
        means = (c[starts + size] - c[starts]) / size
    means = np.maximum(means, 0)
    with np.errstate(divide='ignore'): blocks = -0.691 + 10 * np.log10(means)
    gated = means[blocks > -70]
    if not len(gated): return -math.inf
    relative = -0.691 + 10 * math.log10(float(gated.mean())) - 10
    gated = means[(blocks > -70) & (blocks > relative)]
    return -0.691 + 10 * math.log10(float(gated.mean()))


def block_for(duration): return 0.4 if duration >= 2 else 0.1


def measure(samples, rate, block=None):
    """Loudness (LUFS, None for silence), the block length it was measured with, and the peak in dBFS."""
    block = block or block_for(len(samples) / rate)
    level = loudness(kweighted(samples, rate), rate, block)
    return dict(loudness=round(level, 2) if math.isfinite(level) else None, block=block, peak_db=peak_db(samples))


# ----- the edit ---------------------------------------------------------------------------------------------------
def clean_settings(settings, duration):
    """Settings checked and clamped to the sound: start/end (s), gain (dB), fade_in/fade_out (s), match, quality."""
    s = settings if isinstance(settings, dict) else {}
    def number(name, default, low, high):
        v = s.get(name)
        if v is None or v == '': v = default
        try: v = float(v)
        except (TypeError, ValueError): raise AudioError('Invalid ' + name) from None
        if not math.isfinite(v): raise AudioError('Invalid ' + name)
        return min(high, max(low, v))
    duration = max(0.0, float(duration))
    start, end = number('start', 0, 0, duration), number('end', duration, 0, duration)
    if end - start < 0.02: raise AudioError('Choose a longer part of the sound (at least 0.02 s).')
    fade_in, fade_out = number('fade_in', 0, 0, 60), number('fade_out', 0, 0, 60)
    if fade_in + fade_out > end - start:   # the fades meet in the middle instead of overlapping
        scale = (end - start) / (fade_in + fade_out)
        fade_in, fade_out = fade_in * scale, fade_out * scale
    match = s.get('match', True)
    match = match if isinstance(match, bool) else str(match).strip().lower() in ('1', 'true', 'yes', 'on')
    return dict(start=round(start, 4), end=round(end, 4), gain=round(number('gain', 0, -40, 24), 2), fade_in=round(fade_in, 4),
                fade_out=round(fade_out, 4), match=match, quality=round(number('quality', QUALITY, 0.1, 1), 2))


def fade(n, rising=True):
    """A smooth S-curve (raised cosine) from silence to full volume over n samples, or back down."""
    if n <= 1: return np.zeros(max(0, n), np.float32)
    curve = (0.5 - 0.5 * np.cos(np.pi * np.arange(n) / (n - 1))).astype(np.float32)
    return curve if rising else curve[::-1].copy()


def limit(y, rate, ceiling=CEILING, attack=0.005, release=0.08):
    """A gentle look-ahead peak limiter, in place. Around each peak that would cross the ceiling the volume dips in a
    smooth valley: down over 5 ms before the peak, back up over 80 ms after it, so nothing clips and nothing pumps.
    Works on 32-frame blocks; returns the deepest dip in dB."""
    n, step = len(y), 32
    if not n: return 0.0
    level = np.abs(y[:, 0])
    for c in range(1, y.shape[1]): np.maximum(level, np.abs(y[:, c]), out=level)
    blocks = -(-n // step)
    need = np.ones(blocks * step, np.float32)
    need[:n] = np.minimum(1, ceiling / np.maximum(level, 1e-9))
    need = need.reshape(blocks, step).min(axis=1)
    if need.min() >= 1: return 0.0
    # A block also honours its neighbours, so interpolating the gain between block centres cannot overshoot.
    need = np.minimum(need, np.minimum(np.concatenate([[1.0], need[:-1]]), np.concatenate([need[1:], [1.0]])))
    depth = (-20 * np.log10(need)).astype(np.float32)          # the dip each block needs, in dB
    a, r = max(1, round(attack * rate / step)), max(1, round(release * rate / step))
    dips = depth.copy()                                        # each valley is the peak's dip times this shape; the deepest wins
    for t in range(-a, r + 1):
        if not t: continue
        s = np.float32(0.5 - 0.5 * math.cos(math.pi * (a + 1 + t) / (a + 1)) if t < 0 else 0.5 + 0.5 * math.cos(math.pi * t / (r + 1)))
        if t > 0: np.maximum(dips[t:], depth[:-t] * s, out=dips[t:])
        elif -t < blocks: np.maximum(dips[:t], depth[-t:] * s, out=dips[:t])
    gain = 10 ** (-dips.astype(np.float64) / 20)
    centres = np.arange(blocks) * step + (step - 1) / 2
    for i in range(0, n, 1 << 20):
        frames = np.arange(i, min(n, i + (1 << 20)))
        y[i:i + len(frames)] *= np.interp(frames, centres, gain).astype(np.float32)[:, None]
    over = _peak(y)
    if over > ceiling: y *= np.float32(ceiling / over)   # rounding only
    return round(float(dips.max()), 2)


def process(samples, rate, settings, reference=None, kw2=None, channels=None):
    """The edit as the game will play it: (float32 samples, report).

    settings (see clean_settings): the start/end trim, gain in dB, fade_in/fade_out in seconds (S-curves), match =
    first set the loudness to the reference's (the game's sound: dict(loudness=LUFS, block=seconds)), with the gain
    on top. channels: 1 or 2 to match the game's sound. Peaks stay below -1 dBFS: a gentle limiter takes off up to
    6 dB; a sound that would need more is turned down as a whole (report['short_db']). kw2: kweighted() of the samples
    already converted to `channels`, when the caller keeps it."""
    s = clean_settings(settings, len(samples) / rate)
    a = int(round(s['start'] * rate))
    b = max(a + 1, min(len(samples), int(round(s['end'] * rate))))
    n = b - a
    fi = min(n, int(round(s['fade_in'] * rate)))
    fo = min(n - fi, int(round(s['fade_out'] * rate)))
    up, down = fade(fi, True), fade(fo, False)
    report = dict(duration=round(n / rate, 4), frames=n, rate=int(rate), channels=int(channels or samples.shape[1]), gain_db=s['gain'],
                  match_db=None, limited_db=0.0, short_db=0.0, loudness=None, target=None, settings=s)
    block, target = (reference or {}).get('block') or block_for(n / rate), None
    if s['match'] and reference and reference.get('loudness') is not None:
        power = np.array(kw2[a:b], np.float32) if kw2 is not None else kweighted(to_channels(samples[a:b], channels), rate)
        if fi: power[:fi] *= up * up                        # K(fade·x) ≈ fade·K(x): fades change slowly
        if fo: power[n - fo:] *= down * down
        level = loudness(power, rate, block)
        del power
        if math.isfinite(level):
            report['match_db'] = round(reference['loudness'] - level, 2)
            target = reference['loudness'] + s['gain']
    def render(gain_db):
        y = to_channels(samples[a:b], channels)
        y *= np.float32(10 ** (gain_db / 20))
        if fi: y[:fi] *= up[:, None]
        if fo: y[n - fo:] *= down[:, None]
        peak, short = _peak(y), 0.0
        if peak > CEILING * 2:   # more than 6 dB over: turn the whole sound down so the limiter stays gentle
            short = 20 * math.log10(peak / (CEILING * 2))
            y *= np.float32(CEILING * 2 / peak)
        return y, short, limit(y, rate) if _peak(y) > CEILING else 0.0
    total = s['gain'] + (report['match_db'] or 0)
    y, short, limited = render(total)
    level = None
    if target is not None and limited > 0.5:
        # The limiter took some loudness off the peaks: measure what is left, make up for it once, measure again.
        level = loudness(kweighted(y, rate), rate, block)
        if not short and math.isfinite(level) and target - level > 0.1:
            total += target - level
            y, short, limited = render(total)
            level = loudness(kweighted(y, rate), rate, block)
    report.update(gain_db=round(total, 2), limited_db=limited, short_db=round(short, 2), target=None if target is None else round(target, 2),
                  peak_db=peak_db(y), estimate=estimate(n / rate, y.shape[1], s['quality']))
    if target is not None:
        final = level if level is not None and math.isfinite(level) else target - short
        report.update(loudness=round(final, 2), short_db=round(target - final, 2) if target - final > 0.25 else 0.0)   # a quarter dB is a match
    return y, report


def estimate(seconds, channels, quality=QUALITY):
    """About how big the OGG file will be: libvorbis's nominal stereo bitrates per quality step (it is variable
    bitrate, so this is a guide), with mono at about 60 % of stereo."""
    kbps = float(np.interp(quality * 10, range(11), (64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 500)))
    return int(seconds * kbps * 1000 / 8 * (0.6 if channels == 1 else 1)) + 4000


def warnings(original, report, chosen=None):
    """Plain advice about the edit: length against the game's sound, peaks, silence, a cut source."""
    out, o, n = [], float(original.get('duration') or 0), float(report['duration'])
    if o and o < 3 and n > max(4 * o, 3):
        out.append(f'The game’s sound is {clock(o)} long and yours is {clock(n)}. Short effects can play many times at once, so a long one may pile up. Trim it shorter.')
    if o >= 30 and n < 10: out.append(f'The game’s music is {clock(o)} long and yours is {clock(n)}. Music repeats, so a short clip loops a lot.')
    if report.get('short_db', 0) > 0.5: out.append(f'Your sound stays {report["short_db"]:.1f} dB quieter than asked so it doesn’t distort.')
    elif report.get('limited_db', 0) > 3: out.append(f'Loud peaks are softened by up to {report["limited_db"]:.1f} dB so they don’t distort.')
    if (report.get('peak_db') or -200) < -50: out.append('This part is almost silent.')
    if report['settings']['match'] and original.get('loudness') is None: out.append('The game’s sound is silent, so its loudness can’t be matched.')
    if chosen and chosen.get('cut'): out.append(f'Your file is {clock(chosen["cut"])} long; only its first {clock(chosen.get("duration") or 0)} can be used.')
    if chosen and chosen.get('damaged'): out.append('Part of this file was damaged; the rest is used.')
    return out


# ----- writing -----------------------------------------------------------------------------------------------------
def encode_ogg(samples, rate, quality=QUALITY):
    """OGG Vorbis bytes, the format the game plays. Written in blocks: libsndfile overflows its stack on huge writes."""
    if not len(samples): raise AudioError('There is nothing to save.')
    sf = _soundfile()
    q = min(1.0, max(0.1, float(quality)))
    out = io.BytesIO()
    with sf.SoundFile(out, 'w', int(rate), samples.shape[1], format='OGG', subtype='VORBIS', compression_level=1 - q) as f:
        for i in range(0, len(samples), 16384): f.write(samples[i:i + 16384])
    return out.getvalue()


def probe(data):
    """(rate, channels, frames) of an encoded sound, checked by opening it again."""
    sf = _soundfile()
    try:
        with sf.SoundFile(io.BytesIO(data)) as f:
            block = f.read(4096, dtype='float32', always_2d=True)
            if not len(block): raise AudioError('The converted sound came out empty.')
            return f.samplerate, f.channels, f.frames
    except RuntimeError as exc: raise AudioError('The converted sound could not be read back: ' + str(exc)) from exc


def write_flac(path, samples, rate):
    """Keep a chosen sound losslessly at 16 bits (atomically: a temporary file, then a rename)."""
    sf, path = _soundfile(), Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with sf.SoundFile(str(temp), 'w', int(rate), samples.shape[1], format='FLAC', subtype='PCM_16') as f:
            for i in range(0, len(samples), 65536): f.write(samples[i:i + 65536])
        os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()


def wav(samples, rate):
    """16-bit PCM WAV bytes: the editor's quick preview."""
    pcm = np.empty(samples.shape, '<i2')
    for i in range(0, len(samples), 1 << 20): pcm[i:i + (1 << 20)] = np.round(np.clip(samples[i:i + (1 << 20)], -1, 1) * 32767)
    ch = samples.shape[1]
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + pcm.nbytes, b'WAVE', b'fmt ', 16, 1, ch, int(rate), int(rate) * ch * 2, ch * 2, 16, b'data', pcm.nbytes) + pcm.tobytes()


# ----- links -------------------------------------------------------------------------------------------------------
def _public(ip):
    if ip.version == 6 and ip.ipv4_mapped: ip = ip.ipv4_mapped
    if ip.version == 6 and ip.sixtofour: ip = ip.sixtofour
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _addresses(host, port):
    """Every address of host, each one on the public internet (or a test server in _TEST_ALLOW). Links to this PC, the
    home network, link-local or multicast addresses are refused, however the address is spelled or looked up."""
    try: infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError): raise AudioError('That website could not be found. Check the link and your internet connection.', 'network') from None
    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if not _public(ip) and f'{ip}:{port}' not in _TEST_ALLOW:
            raise AudioError('Links to this computer or your home network are blocked for safety.', 'blocked')
        if str(ip) not in addresses: addresses.append(str(ip))
    if not addresses: raise AudioError('That website could not be found.', 'network')
    return addresses


def check_url(url, resolve=True):
    """(url, parts, address) of a link the lab may open: http or https, no user name, and (when resolve) a host whose
    every address is on the public internet. Links to this PC, the home network, link-local or multicast are refused."""
    url = url.strip() if isinstance(url, str) else ''
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme.lower() == 'https' else 80)
    except ValueError: raise AudioError('That link doesn’t look right.', 'bad_link') from None
    if len(url) > 4096 or parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        raise AudioError('Paste a link that starts with http:// or https://.', 'bad_link')
    if parts.username or parts.password: raise AudioError('Links with a user name or password can’t be used.', 'bad_link')
    return url, parts, _addresses(parts.hostname, port)[0] if resolve else None


def _dial(addresses, port, timeout):
    """A connection to the first of the checked addresses that answers."""
    error = OSError('No address to connect to')
    for address in addresses:
        try: return socket.create_connection((address, port), timeout)
        except OSError as exc: error = exc
    raise error


def _connect(parts, addresses, timeout):
    """A connection to the checked addresses (not a second DNS answer), with the host name kept for TLS and Host."""
    https = parts.scheme.lower() == 'https'
    port = parts.port or (443 if https else 80)
    conn = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout, context=ssl.create_default_context()) if https else http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    conn._create_connection = lambda where, timeout=None, source=None: _dial(addresses, where[1], timeout)
    return conn


class _Guard:
    """Cancel and a time limit for a download. A read can wait a long time on a slow or stalled server (a byte every
    few seconds never times out), so when either one trips, the connections it watches are closed from a thread of
    its own: the waiting read returns at once and check() says why. (On Windows only closing the socket itself wakes
    a waiting read: shutdown() does not, and socket.close() waits for the response's file object to let go.)"""

    def __init__(self, cancel=None, seconds=None, message='The download took too long.'):
        self.cancel, self.message, self.items, self.done = cancel, message, [], threading.Event()
        self.until = None if seconds is None else time.monotonic() + seconds
        threading.Thread(target=self._watch, daemon=True, name='audio-guard').start()

    def __enter__(self): return self

    def __exit__(self, *exc): self.done.set()

    def check(self):
        if self.cancel is not None and self.cancel.is_set(): raise AudioError('Cancelled.', 'cancelled')
        if self.until is not None and time.monotonic() > self.until: raise AudioError(self.message, 'network')

    def watch(self, item):
        """An http.client connection or response to shut down when the guard trips."""
        self.items.append(item)
        return item

    def _watch(self):
        while not self.done.wait(0.1):
            try: self.check()
            except AudioError:
                for item in list(self.items):
                    sock = getattr(item, 'sock', None) or getattr(getattr(getattr(item, 'fp', None), 'raw', None), '_sock', None)
                    if isinstance(sock, socket.socket):
                        try: socket.socket.shutdown(sock, socket.SHUT_RDWR)
                        except OSError: pass
                        _socket.socket.close(sock)


def _chunk(response, guard, n, whole=False):
    """Up to n bytes of a response body (read1: as soon as any arrive; whole: n unless it ends), b'' at its end."""
    try: data = response.read(n) if whole else response.read1(n)
    except (OSError, http.client.HTTPException) as exc:
        guard.check()
        raise AudioError('The download stopped: ' + str(exc), 'network') from None
    if not data: guard.check()   # a connection the guard shut down reads as the end of the file
    return data


def _target(parts):
    """The request target, with spaces and other characters a server would refuse percent-encoded."""
    safe = "/%:@!$&'()*+,;=-._~"
    return urllib.parse.quote(parts.path or '/', safe=safe) + ('?' + urllib.parse.quote(parts.query, safe=safe + '?') if parts.query else '')


def _open(url, guard, timeout=30):
    """GET a link, following at most 5 redirects, each one checked again. Returns (connection, response, final url).
    Answers that websites give programs they don't know (403, 429, 503…) are 'refused': the page may still work in
    the link helper."""
    for _ in range(6):
        guard.check()
        url, parts, _ = check_url(url, resolve=False)
        conn = guard.watch(_connect(parts, _addresses(parts.hostname, parts.port or (443 if parts.scheme.lower() == 'https' else 80)), timeout))
        try:
            conn.request('GET', _target(parts), headers={'User-Agent': AGENT, 'Accept': '*/*', 'Accept-Encoding': 'identity'})
            response = guard.watch(conn.getresponse())
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            guard.check()
            raise AudioError('The website did not answer: ' + str(exc), 'network') from None
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader('Location')
            response.close(); conn.close()
            if not location: raise AudioError('The link points nowhere.', 'network')
            try: location = location.encode('latin-1').decode('utf-8')   # raw UTF-8 in the header, read the way browsers do
            except UnicodeError: pass
            url = urllib.parse.urljoin(url, location.strip())
            continue
        if response.status >= 400:
            response.close(); conn.close()
            if response.status in (404, 410): raise AudioError(f'Nothing was found at this link ({response.status}). Check the link.', 'unavailable')
            raise AudioError(f'The website answered with an error ({response.status}). Check the link.', 'refused' if response.status in (401, 403, 405, 406, 429, 451, 503) else 'network')
        return conn, response, url
    raise AudioError('The link redirected too many times.', 'network')


def _page_host(host):
    host = (host or '').lower().rstrip('.')
    return any(host == p or host.endswith('.' + p) for p in PAGES)


def _filename(response, url):
    name = ''
    disposition = response.getheader('Content-Disposition') or ''
    found = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.I)
    if found: name = urllib.parse.unquote(found.group(1))
    if not name: name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1])
    return clean_name(name) or 'Sound from a link'


# Control and text-direction characters (a right-to-left override makes "song<RLO>3pm.exe" read "songexe.mp3").
_INVISIBLE = re.compile('[\x00-\x1f\x7f' + ''.join(map(chr, (0x200e, 0x200f, *range(0x202a, 0x202f), *range(0x2066, 0x206a)))) + ']')


def clean_text(text, limit=200):
    """One line of plain text from a page or a file: no control or text-direction characters, at most limit long."""
    return ' '.join(_INVISIBLE.sub(' ', str(text or '')).split())[:limit]


def clean_name(name):
    """A file's name without any folder in front of it."""
    return clean_text(re.split(r'[\\/]', str(name or ''))[-1], 120)


def fetch(url, folder, progress=None, cancel=None, tools=None, command=None, deadline=900, pages=True):
    """Sound from a link: (samples, rate, info). A direct audio link is downloaded and decoded here; any other link is
    treated as a page for the link helper. progress(fraction or None, message); cancel: a threading.Event.
    command: the helper's command line before its arguments (tests use a stand-in for yt-dlp). deadline: seconds a
    direct download may take in all. pages=False (Settings > Sound) refuses pages: only direct links work."""
    report = progress or (lambda fraction=None, message=None: None)
    url, parts, _ = check_url(url)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if not _page_host(parts.hostname):
        report(None, 'Connecting…')
        with _Guard(cancel, deadline) as guard:
            try: conn, response, final = _open(url, guard)
            except AudioError as exc:
                if exc.code != 'refused': raise
                conn = response = None   # a page that turns away a plain download may still work in the link helper
            if response is not None:
                try:
                    head = _chunk(response, guard, 64, whole=True)
                    kind, ctype = sniff(head), (response.getheader('Content-Type') or '').split(';')[0].strip().lower()
                    if kind not in ('', 'html') or (kind == '' and (ctype.startswith(('audio/', 'video/')) or ctype in ('application/ogg', 'application/x-flac'))):
                        path = folder / ('direct-' + uuid.uuid4().hex)
                        try:
                            _save(response, head, path, report, guard)
                            report(None, 'Converting…')
                            info = {}
                            samples, rate = decode(path, tools, info=info)
                        finally: path.unlink(missing_ok=True)
                        return samples, rate, dict(info, title=_filename(response, final), url=final, via='direct')
                finally:
                    response.close(); conn.close()
    if not pages: raise AudioError('This link is a web page, not a sound file, and page links are turned off in Settings › Sound. '
                                   'Paste a direct link to a sound file, or turn page links back on.', 'pages_off')
    return _helper_fetch(url, folder, report, cancel, tools, command)


def _save(response, head, path, report, guard):
    total = len(head) + response.length if response.length is not None else 0
    if total > MAX_BYTES: raise AudioError(f'This file is {mb(total)}; the limit is {mb(MAX_BYTES)}.', 'too_big')
    done = len(head)
    with path.open('wb') as f:
        f.write(head)
        while True:
            guard.check()
            chunk = _chunk(response, guard, 1 << 16)
            if not chunk: break
            done += len(chunk)
            if done > MAX_BYTES: raise AudioError(f'This file is bigger than {mb(MAX_BYTES)}.', 'too_big')
            f.write(chunk)
            report(done / total if total else None, f'Downloading {mb(done)}' + (f' of {mb(total)}' if total else ''))
    if total and done < total: raise AudioError('The download stopped before the end. Try again.', 'network')


# ----- the link helper ---------------------------------------------------------------------------------------------
def helper_ready(tools=None):
    tools = Path(tools or TOOLS)
    return (tools / 'yt-dlp.exe').is_file() and (tools / 'deno' / 'deno.exe').is_file() and (tools / 'pylib' / 'av').is_dir()


def helper_args(url, work, tools, proxy=None):
    """yt-dlp's options (checked against yt-dlp 2026.08.19 --help): one item, audio only, size and length limits, no
    config files, progress and results on lines of their own, and every connection through the lab's gate (proxy)."""
    tools = Path(tools)
    return ['--ignore-config', '--no-playlist', '--playlist-items', '1', '--format', 'bestaudio/best', '--max-filesize', '150M',
            '--match-filters', f'!is_live & duration <=? {LINK_SECONDS}', '--no-part', '--no-mtime', '--fixup', 'never',
            '--js-runtimes', 'deno:' + str(tools / 'deno' / 'deno.exe'), '--cache-dir', str(tools / 'cache'),
            '--socket-timeout', '30', '--retries', '3', '--newline', '--progress', '--color', 'never', '--encoding', 'utf-8',
            *(['--proxy', proxy] if proxy else []),
            '--progress-template', 'download:LABPROGRESS %(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.total_bytes_estimate)s',
            '--print', 'before_dl:LABINFO %(.{id,title,duration,license,uploader,webpage_url,extractor_key})j',
            '--print', 'after_move:LABFILE %(filepath)s', '--output', str(Path(work) / '%(id)s.%(ext)s'), '--', url]


class _Gate:
    """The link helper's only way out: a small web proxy on this PC (127.0.0.1, a free port, only while yt-dlp runs)
    that connects to public internet addresses only, each checked like a direct link. So neither a page, nor a redirect,
    nor a DNS answer that changes between two lookups can send yt-dlp to this PC or the home network. HTTPS passes
    through as a tunnel (CONNECT); a plain HTTP request is passed on, one per connection."""
    LIMIT = 2 * MAX_BYTES   # all one run may download: the sound and the pages and scripts around it

    def __init__(self):
        self.server = socket.create_server(('127.0.0.1', 0))
        self.url = 'http://127.0.0.1:%d' % self.server.getsockname()[1]
        self.socks, self.lock, self.closed, self.blocked, self.received, self.too_much = set(), threading.Lock(), False, [], 0, False
        threading.Thread(target=self._accept, daemon=True, name='audio-gate').start()

    def __enter__(self): return self

    def __exit__(self, *exc): self.close()

    def close(self):
        with self.lock: self.closed, socks, self.socks = True, [self.server, *self.socks], set()
        for s in socks:
            try: socket.socket.shutdown(s, socket.SHUT_RDWR)
            except OSError: pass
            s.close()

    def _keep(self, s):
        with self.lock:
            if self.closed: s.close(); raise OSError('The link helper has finished.')
            self.socks.add(s)
        return s

    def _accept(self):
        while True:
            try: client, _ = self.server.accept()
            except OSError: return
            threading.Thread(target=self._serve, args=(client,), daemon=True, name='audio-gate-link').start()

    def _serve(self, client):
        upstream = None
        try:
            self._keep(client).settimeout(60)
            data = b''
            while b'\r\n\r\n' not in data:
                chunk = client.recv(65536)
                if not chunk or len(data) > 65536: return
                data += chunk
            head, rest = data.split(b'\r\n\r\n', 1)
            lines = head.decode('latin-1').split('\r\n')
            method, target, version = lines[0].split(' ')
            if method.upper() == 'CONNECT':
                host, _, port = target.rpartition(':')
                host, port = host.strip('[]'), int(port)
            else:
                parts = urllib.parse.urlsplit(target)
                if parts.scheme.lower() != 'http' or not parts.hostname: raise ValueError('Not a proxy request')
                host, port = parts.hostname, parts.port or 80
            try: addresses = _addresses(host, port)
            except AudioError as exc:
                if exc.code == 'blocked': self.blocked.append(f'{host}:{port}')
                client.sendall(b'HTTP/1.1 403 Blocked by Asset Lab\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                return
            upstream = self._keep(_dial(addresses, port, 20))
            upstream.settimeout(120)
            client.settimeout(120)
            if method.upper() == 'CONNECT': client.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
            else:
                keep = [l for l in lines[1:] if l.split(':', 1)[0].strip().lower() not in ('connection', 'proxy-connection', 'keep-alive', 'proxy-authorization')]
                origin = (parts.path or '/') + ('?' + parts.query if parts.query else '')
                upstream.sendall((f'{method} {origin} {version}\r\n' + ''.join(l + '\r\n' for l in keep) + 'Connection: close\r\n\r\n').encode('latin-1'))
            if rest: upstream.sendall(rest)
            self._relay(client, upstream)
        except (OSError, ValueError):
            if upstream is None:
                try: client.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                except OSError: pass
        finally:
            for s in (client, upstream):
                if s is None: continue
                with self.lock: self.socks.discard(s)
                s.close()

    def _relay(self, client, upstream):
        def pump(src, dst, count):
            try:
                while True:
                    data = src.recv(1 << 16)
                    if not data or (count and not self._count(len(data))): break
                    dst.sendall(data)
            except OSError: pass
            try: dst.shutdown(socket.SHUT_WR)
            except OSError: pass
        other = threading.Thread(target=pump, args=(client, upstream, False), daemon=True, name='audio-gate-pump')
        other.start()
        pump(upstream, client, True)
        other.join(130)

    def _count(self, n):
        """Bytes in from the internet. yt-dlp's --max-filesize can't stop a stream of unknown length, so past LIMIT the
        gate closes and the run ends as too big instead of filling the disk."""
        with self.lock:
            self.received += n
            over = self.received > self.LIMIT and not self.closed
            if over: self.too_much = True
        if over: self.close()
        return not self.too_much


def _kill(proc):
    if proc.poll() is not None: return
    if os.name == 'nt':   # yt-dlp.exe is a one-file bundle that runs itself as a child: end the whole tree
        try: subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True, creationflags=QUIET, timeout=30)
        except (OSError, subprocess.TimeoutExpired): pass
    if proc.poll() is None: proc.kill()
    try: proc.wait(10)
    except subprocess.TimeoutExpired: pass


def run_helper(command, url, work, tools, report, cancel, timeout=1500):
    """Run yt-dlp for one link: (downloaded file, info from the page). Progress is passed on as it comes. Every
    connection it makes goes through a _Gate; the proxy variables also send anything it starts there."""
    with _Gate() as gate:
        env = dict(os.environ, HTTP_PROXY=gate.url, HTTPS_PROXY=gate.url, ALL_PROXY=gate.url, NO_PROXY='')
        proc = subprocess.Popen([*command, *helper_args(url, work, tools, gate.url)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, cwd=str(work), env=env, creationflags=QUIET)
        lines, errors, found = collections.deque(maxlen=100), collections.deque(maxlen=100), dict(meta={}, path=None)
        def read_out():
            for raw in iter(lambda: proc.stdout.readline(1 << 20), b''):   # a line of at most 1 MB at a time
                line = raw.decode('utf-8', 'replace').strip()
                if line.startswith('LABPROGRESS '):
                    values = ([int(v) if v.isdigit() else int(float(v)) if re.fullmatch(r'\d+\.\d+', v) else 0 for v in line.split()[1:4]] + [0, 0, 0])[:3]
                    done, total = values[0], values[1] or values[2]
                    report(min(1.0, done / total) if total else None, f'Downloading {mb(done)}' + (f' of {mb(total)}' if total else ''))
                elif line.startswith('LABINFO '):
                    try: meta = json.loads(line[8:])
                    except ValueError: meta = {}
                    found['meta'] = meta if isinstance(meta, dict) else {}
                    report(None, 'Found “' + (clean_text(found['meta'].get('title'), 120) or 'the sound') + '”')
                elif line.startswith('LABFILE '): found['path'] = line[8:].strip()
                elif line: lines.append(line[:500])
        def read_err():
            for raw in iter(lambda: proc.stderr.readline(1 << 20), b''): errors.append(raw.decode('utf-8', 'replace').strip()[:500])
        readers = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
        for t in readers: t.start()
        until = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set(): raise AudioError('Cancelled.', 'cancelled')
                if time.monotonic() > until: raise AudioError('The link helper took too long.', 'network')
                time.sleep(0.15)
        finally:
            _kill(proc)
            for t in readers: t.join(5)
            for pipe in (proc.stdout, proc.stderr): pipe.close()
    path = Path(found['path']).resolve() if found['path'] else None
    if gate.too_much: raise AudioError(f'This sound is bigger than {mb(MAX_BYTES)}.', 'too_big')
    if proc.returncode or not path:
        if gate.blocked: raise AudioError('This page tried to reach this computer or your home network. That is blocked for safety.', 'blocked')
        raise _helper_error(list(errors)[-40:] + list(lines)[-20:], found['meta'])
    if not path.is_relative_to(Path(work).resolve()) or not path.is_file(): raise AudioError('The link helper did not save a sound file.')
    if path.stat().st_size > MAX_BYTES: raise AudioError(f'This sound is bigger than {mb(MAX_BYTES)}.', 'too_big')
    return path, found['meta']


def _helper_error(lines, meta=None):
    text = '\n'.join(lines).lower()
    last = next((l for l in reversed(lines) if 'ERROR' in l), lines[-1] if lines else '')
    last = re.sub(r'^ERROR:\s*', '', last)
    if 'does not pass filter' in text or 'is_live' in text: return AudioError(f'Live streams and videos longer than {LINK_SECONDS // 60} minutes can’t be used.', 'too_long')
    if 'max-filesize' in text or 'larger than max' in text: return AudioError(f'This sound is bigger than {mb(MAX_BYTES)}.', 'too_big')
    if 'unsupported url' in text: return AudioError('The link helper doesn’t know how to get sound from this page.', 'unsupported')
    if 'not a bot' in text: return AudioError('The website asked to confirm you are not a bot. Try again later, or update the link helper.', 'outdated')
    if any(k in text for k in ('private video', 'sign in', 'login required', 'members-only', 'confirm your age', 'age-restricted')):
        return AudioError('This needs a signed-in account, so the link helper can’t get it.', 'account')
    if any(k in text for k in ('yt-dlp -u', 'latest version', 'unable to extract', 'nsig', 'signature', 'requested format is not available',
                               'http error 403', 'please report this issue', 'js runtime', 'challenge', 'outdated')):
        return AudioError('The link helper could not read this page. Websites change often: try “Update the link helper”.', 'outdated')
    if any(k in text for k in ('video unavailable', 'not available', 'has been removed', 'http error 404', 'does not exist')): return AudioError('This video or sound is not available.', 'unavailable')
    if 'unable to download' in text or 'timed out' in text or 'getaddrinfo' in text: return AudioError('The website did not answer. Check your internet connection.', 'network')
    return AudioError('The link helper could not get the sound' + (': ' + last[:240] if last else '.'), 'helper')


def _helper_fetch(url, folder, report, cancel, tools, command):
    tools = Path(tools or TOOLS)
    if command is None:
        if not helper_ready(tools): raise AudioError('To get sound from a web page, Asset Lab needs its link helper.', 'needs_helper')
        command = [str(tools / 'yt-dlp.exe')]
    report(None, 'Looking for the sound on the page…')
    work = Path(folder) / ('helper-' + uuid.uuid4().hex)
    work.mkdir(parents=True)
    try:
        path, meta = run_helper(command, url, work, tools, report, cancel)
        report(None, 'Converting…')
        info = {}
        samples, rate = decode(path, tools, info=info)
        result = dict(info, **{k: clean_text(meta[k]) for k in ('license', 'uploader', 'extractor_key') if isinstance(meta.get(k), str) and clean_text(meta[k])})
        page = meta.get('webpage_url')
        result.update(title=clean_text(meta.get('title'), 120) or 'Sound from a link', url=page[:2000] if isinstance(page, str) and re.match(r'https?://', page, re.I) else url, via='helper')
        return samples, rate, result
    finally: shutil.rmtree(work, ignore_errors=True)


def tool_list(manifest=None):
    """The link helper's downloads from bootstrap.json: name, url, sha256, size, into (its place in the tools folder)."""
    path = Path(manifest or MANIFEST)
    try: doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): return []
    return [t for t in doc.get('tools') or [] if isinstance(t, dict) and {'name', 'url', 'sha256', 'size', 'into'} <= set(t)]


def tools_status(tools=None, items=None):
    tools = Path(tools or TOOLS)
    items = tool_list() if items is None else items
    have = [dict(name=t['name'], size=int(t['size']), installed=(tools / t['into']).exists(), source=urllib.parse.urlsplit(t['url']).hostname or '') for t in items]
    try: record = json.loads((tools / 'tools.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): record = {}
    return dict(available=bool(items), ready=bool(items) and all(h['installed'] for h in have) and helper_ready(tools), items=have,
                download=sum(h['size'] for h in have if not h['installed']), installed_at=record.get('at'), updated=record.get('updated', ''))


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def download(url, dest, size, sha256, progress=None, cancel=None, opener=None):
    """Download into dest, resuming a partial dest.part, then check the size and SHA-256 (a mismatch is deleted)."""
    dest, size = Path(dest), int(size)
    part = dest.with_name(dest.name + '.part')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == size and file_sha(dest) == sha256: return dest
    for attempt in range(4):
        have = part.stat().st_size if part.is_file() else 0
        if have > size: part.unlink(); have = 0
        try:
            if have < size:
                request = urllib.request.Request(url, headers={'User-Agent': AGENT, **({'Range': f'bytes={have}-'} if have else {})})
                # read1 returns whatever has arrived, so Cancel is noticed on a slow line too; the guard ends a stalled read.
                with (opener or urllib.request.urlopen)(request, timeout=30) as response, _Guard(cancel) as guard:
                    guard.watch(response)
                    if have and response.status != 206: have = 0    # the server sent the whole file again
                    with part.open('ab' if have else 'wb') as f:
                        while True:
                            if cancel is not None and cancel.is_set(): raise AudioError('Cancelled. The download continues where it stopped next time.', 'cancelled')
                            chunk = (getattr(response, 'read1', None) or response.read)(1 << 16)
                            if not chunk: break
                            have += len(chunk)
                            f.write(chunk)
                            if have > size: break   # more than the pinned size: the check below deletes it
                            if progress: progress(have)
            got = part.stat().st_size if part.is_file() else 0
            if got > size or (got == size and file_sha(part) != sha256):
                part.unlink(missing_ok=True)
                raise AudioError(f'The download of {dest.name} did not match its checksum, so it was deleted. Try again.', 'checksum')
            if got < size: raise ConnectionError(f'{mb(got)} of {mb(size)} arrived')
            os.replace(part, dest)
            return dest
        except (OSError, http.client.HTTPException) as exc:
            if cancel is not None and cancel.is_set(): raise AudioError('Cancelled. The download continues where it stopped next time.', 'cancelled') from None
            if attempt == 3: raise AudioError(f'The download of {dest.name} stopped ({exc}). Try again: it continues where it stopped.', 'network') from None
            for _ in range(int(15 * (attempt + 1))):
                if cancel is not None and cancel.is_set(): raise AudioError('Cancelled. The download continues where it stopped next time.', 'cancelled')
                time.sleep(0.1)


def _publish_helper(source, target):
    # Defender and OneDrive can briefly hold freshly extracted DLLs/directories.
    # Keep the verified download and the old installation intact on failure.
    for attempt in range(20):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            if attempt == 19:
                raise AudioError('Windows is still holding the sound helper files open. '
                                 'Close other Asset Lab windows and try Get again. '
                                 'The checked download has been kept; it will not download again.', 'helper_locked') from exc
            time.sleep(0.3)


def unpack(file, tools, into):
    """Put a checked download in place: an .exe is copied, a .zip or wheel extracted (to a new folder, then renamed)."""
    file, tools = Path(file), Path(tools)
    target = tools / into
    if not target.resolve().is_relative_to(tools.resolve()) or target.resolve() == tools.resolve():
        raise AudioError('Unsafe helper destination.')
    tools.mkdir(parents=True, exist_ok=True)
    if file.suffix.lower() == '.exe':
        staged = target.with_name(target.name + '.' + uuid.uuid4().hex + '.new')
        shutil.copyfile(file, staged)
        try: _publish_helper(staged, target)
        finally: staged.unlink(missing_ok=True)
        return target
    staging = tools / (into + '.' + uuid.uuid4().hex + '.new')
    with zipfile.ZipFile(file) as z:
        root = staging.resolve()
        for member in z.namelist():
            if not (staging / member).resolve().is_relative_to(root): raise AudioError('Unsafe path in ' + file.name)
        z.extractall(staging)
    backup = tools / (into + '.' + uuid.uuid4().hex + '.old')
    try:
        if target.exists(): _publish_helper(target, backup)
        try: _publish_helper(staging, target)
        except Exception:
            if backup.exists(): _publish_helper(backup, target)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    return target


def _check_tools(tools):
    """Each part of the helper must start: yt-dlp and Deno report their versions, PyAV imports in a separate Python."""
    tools = Path(tools)
    checks = [('yt-dlp', [str(tools / 'yt-dlp.exe'), '--version']), ('Deno', [str(tools / 'deno' / 'deno.exe'), '--version']),
              ('PyAV', [sys.executable, '-I', '-c', 'import sys; sys.path.append(sys.argv[1]); import os; [os.add_dll_directory(p) for p in '
                                                    '(os.path.join(sys.argv[1], "av.libs"),) if hasattr(os, "add_dll_directory") and os.path.isdir(p)]; '
                                                    'import av; print(av.__version__)', str(tools / 'pylib')])]
    versions = {}
    for name, command in checks:
        try: run = subprocess.run(command, capture_output=True, timeout=120, creationflags=QUIET, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AudioError(f'{name} was downloaded but does not start ({exc}). Your antivirus may have blocked it.', 'helper_broken') from None
        if run.returncode: raise AudioError(f'{name} was downloaded but does not start: ' + (run.stderr or run.stdout).decode('utf-8', 'replace').strip()[-300:], 'helper_broken')
        versions[name] = run.stdout.decode('utf-8', 'replace').strip().splitlines()[0][:80] if run.stdout.strip() else ''
    return versions


def install_tools(tools=None, items=None, progress=None, cancel=None, opener=None, check=True):
    """Download the link helper from its official sources (a partial download continues), check each file's size and
    SHA-256, and unpack it into the tools folder: yt-dlp.exe, deno/ and pylib/ (PyAV)."""
    tools = Path(tools or TOOLS)
    items = tool_list() if items is None else items
    report = progress or (lambda fraction=None, message=None: None)
    if not items: raise AudioError('This copy of Asset Lab has no link helper list. Update Asset Lab.')
    todo = [t for t in items if not (tools / t['into']).exists()]
    total, before = sum(int(t['size']) for t in todo) or 1, 0
    for t in todo:
        name = Path(urllib.parse.urlsplit(t['url']).path).name or t['into']
        file = download(t['url'], tools / 'downloads' / name, int(t['size']), t['sha256'],
                        lambda done, t=t: report((before + done) / total, f"Downloading {t['name']} · {mb(before + done)} of {mb(total)}"), cancel, opener)
        report((before + int(t['size'])) / total, 'Unpacking ' + t['name'] + '…')
        unpack(file, tools, t['into'])
        file.unlink(missing_ok=True)
        before += int(t['size'])
    versions = _check_tools(tools) if check else {}
    record = dict(at=time.time(), items=[t['name'] for t in items], versions=versions)
    (tools / 'tools.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    return tools_status(tools, items)


def update_tools(tools=None, progress=None, cancel=None, command=None):
    """yt-dlp's own updater (yt-dlp -U checks the new release's signature itself). Returns what it said."""
    tools = Path(tools or TOOLS)
    if command is None:
        if not (tools / 'yt-dlp.exe').is_file(): raise AudioError('The link helper is not installed.', 'needs_helper')
        command = [str(tools / 'yt-dlp.exe')]
    (progress or (lambda fraction=None, message=None: None))(None, 'Checking for a newer yt-dlp…')
    proc = subprocess.Popen([*command, '-U'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(tools), creationflags=QUIET)
    until, text = time.monotonic() + 300, b''
    try:
        while True:
            if cancel is not None and cancel.is_set(): raise AudioError('Cancelled.', 'cancelled')
            if time.monotonic() > until: raise AudioError('The update took too long.', 'network')
            try:
                text = proc.communicate(timeout=0.2)[0] or b''
                break
            except subprocess.TimeoutExpired: continue   # communicate() keeps what it has read so far
    finally: _kill(proc)
    lines = [l.strip() for l in text.decode('utf-8', 'replace').splitlines() if l.strip()]
    said = next((l for l in reversed(lines) if re.search(r'up to date|updated yt-dlp|latest', l, re.I)), lines[-1] if lines else '')
    if proc.returncode: raise AudioError('The update did not work: ' + (said or 'no answer')[:240], 'network')
    try:
        record = json.loads((tools / 'tools.json').read_text(encoding='utf-8'))
        record['updated'] = said[:200]
        (tools / 'tools.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    except (OSError, ValueError): pass
    return dict(message=said or 'yt-dlp is up to date.')


# ----- background jobs ---------------------------------------------------------------------------------------------
class Jobs:
    """Work the editor follows while it runs: link downloads and the link helper install. One thread per job."""

    def __init__(self):
        self.items, self.lock = {}, threading.Lock()

    def start(self, kind, work, **extra):
        job = dict(id=uuid.uuid4().hex, kind=kind, state='running', progress=None, message='Starting…', result=None, error='', code='', started=time.time(), **extra)
        cancel = threading.Event()
        def update(fraction=None, message=None):
            with self.lock:
                job['progress'] = None if fraction is None else round(min(1.0, max(0.0, float(fraction))), 4)
                if message: job['message'] = message
        def run():
            try: outcome = dict(state='done', result=work(update, cancel), progress=1.0, message='Done')
            except AudioError as exc: outcome = dict(state='cancelled' if exc.code == 'cancelled' else 'error', error=str(exc), code=exc.code)
            except Exception as exc:   # anything else must still end the job, or the editor would wait for it forever
                outcome = dict(state='error', error='Something went wrong: ' + (str(exc) or type(exc).__name__), code='')
            with self.lock: job.update(outcome, finished=time.time())
        with self.lock:
            self.items[job['id']] = (job, cancel)
            done = sorted((j for j, _ in self.items.values() if j['state'] != 'running'), key=lambda j: j['started'])
            for j in done[:-30]: self.items.pop(j['id'], None)
        threading.Thread(target=run, daemon=True, name='audio-' + kind).start()
        return self.get(job['id'])

    def get(self, jid):
        with self.lock:
            entry = self.items.get(jid)
            if not entry: raise AudioError('This download is no longer known. Start it again.')
            return dict(entry[0])

    def cancel(self, jid):
        with self.lock:
            entry = self.items.get(jid)
            if entry: entry[1].set()
        return self.get(jid)

    def running(self, kind):
        with self.lock: return next((dict(j) for j, _ in self.items.values() if j['kind'] == kind and j['state'] == 'running'), None)
