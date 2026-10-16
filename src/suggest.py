"""Suggestions from players to the creator (Settings > About > Suggestions): sent to the creator's Google Form.

Only what the player types and the Asset Lab version go out, and only when they press Send: no name, no account,
nothing about their PC. The page cannot reach the internet itself (its Content-Security-Policy), so the lab posts it.
"""
import threading
import time
import urllib.parse
import urllib.request

# The creator's form: its public link and the ids of its questions (entry.<n>, read from the form's page).
FORM = 'https://docs.google.com/forms/d/e/1FAIpQLSeeYoLKm3tafbXkTupZBhpKPx0lKXoYfUiunVQow79L-wmkJw/viewform'
ENTRY_TEXT = 'entry.1243839786'   # the "Suggestion" question (paragraph)
ENTRY_VERSION = ''        # a "Version" question, if the form gets one; until then the version ends the text
HOST = 'https://docs.google.com/forms/'
MIN_GAP, PER_HOUR, MAX_LENGTH = 20, 6, 2000   # a few suggestions an hour is plenty; more is a stuck key or a script
_sent, _lock = [], threading.Lock()


def ready():
    return FORM.startswith(HOST) and bool(ENTRY_TEXT)


def info():
    return dict(ready=ready(), max_length=MAX_LENGTH)


def send(text, version, opener=urllib.request.urlopen):
    """Post one suggestion to the form. Raises ValueError with a message the player can read."""
    text = ' '.join(str(text or '').split()) if '\n' not in str(text or '') else str(text).strip()
    if not ready(): raise ValueError('Suggestions are not open in this version yet.')
    if len(text) < 3: raise ValueError('Write your suggestion first.')
    if len(text) > MAX_LENGTH: raise ValueError(f'Keep it under {MAX_LENGTH} characters, please.')
    now = time.time()
    with _lock:
        recent = [t for t in _sent if now - t < 3600]
        if recent and now - recent[-1] < MIN_GAP: raise ValueError('Thanks! Wait a few seconds before sending another one.')
        if len(recent) >= PER_HOUR: raise ValueError('That is a lot of suggestions for one hour. Thanks! Try again a bit later.')
        _sent[:] = recent + [now]
    stamp = str(version or '(unknown version)')
    fields = {ENTRY_TEXT: text, ENTRY_VERSION: stamp} if ENTRY_VERSION else {ENTRY_TEXT: f'{text}\n\n— Asset Lab {stamp}'}
    request = urllib.request.Request(FORM.replace('/viewform', '/formResponse'), data=urllib.parse.urlencode(fields).encode(),
                                     headers={'User-Agent': 'AssetLab', 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with opener(request, timeout=15) as response:
            if response.status != 200: raise ValueError
    except Exception:
        with _lock:
            if now in _sent: _sent.remove(now)   # it did not go: the player may try again at once
        raise ValueError('Your suggestion could not be sent (no internet?). It is still in the box: try again later.')
    return dict(sent=True)
