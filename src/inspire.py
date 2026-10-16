"""Inspiration from Wikipedia: when a request names someone or something ("a Post Malone theme", "inspired by Minecraft"),
its short public summary helps the AI get the look right (signature colors, symbols, style).

Only the name goes out, and only when Settings > Theme AI & drawing > Look things up for inspiration is on. Moods, colors
and the game's own parts are never looked up, and a match must really be that name, so "dark gold vibes" stays local.
"""
import json
import re
import threading
import urllib.parse
import urllib.request

API = 'https://en.wikipedia.org'
AGENT = 'AssetLab/1.0 (Mitos.is mod tool; https://github.com/xXrisingXx/asset-lab)'
# "like X", "inspired by X", "X theme", "X themed", "X style", "X vibes", "a X reskin".
PATTERNS = [r'\b(?:like|inspired by|based on|in the style of|themed (?:after|on|like|around)|theme(?:d)? (?:after|of|around)|look like)\s+([^,.;!?\n]{2,60})',
            r'(?:^|\b(?:a|an|the|my|full|whole|complete)\s+)([^,.;!?\n]{2,40}?)\s+(?:theme[ds]?|style|vibes?|aesthetic|reskin|edition|version)\b']
# Words that describe a look rather than name something.
GENERIC = set('''a an the my our your this that these those it them me us full whole complete entire new different cool nice awesome epic
dark light bright neon gold golden silver chrome rainbow pastel spooky scary creepy halloween christmas xmas winter summer spring autumn fall
ocean sea water fire lava ice snow frozen toxic slime galaxy space cosmic retro 8-bit pixel cyberpunk synthwave vaporwave steampunk royal
blood bloody evil cute funny goofy minimal clean modern old vintage glow glowing glossy holographic metal metallic wood wooden stone
red orange yellow green blue purple violet pink black white grey gray brown teal cyan magenta game ui menu buttons button icons icon
virus cells cell background backgrounds title logo text font fonts everything same like look theme style vibe vibes and or with of
make give turn change redo redesign reskin colors colours color colour mood'''.split())
_cache, _lock = {}, threading.Lock()


def candidates(text):
    """Names a request may refer to, best first ("post malone" from "give the game a post malone theme")."""
    out = []
    for pattern in PATTERNS:
        for found in re.finditer(pattern, text, re.I):
            name = re.split(r'\s+(?:and|but|with|for|to|on|in|so)\s+', found.group(1).strip(), 1, flags=re.I)[0].strip(' "\'')
            # Drop the request's own words in front ("give the whole game a post malone" -> "post malone"); a "the" right
            # before the name stays ("the matrix" is a film, "matrix" is maths).
            parts = name.split()
            while parts and parts[0].lower() in GENERIC and not (parts[0].lower() == 'the' and len(parts) > 1 and parts[1].lower() not in GENERIC):
                if len(parts) > 1 and all(p.isdigit() for p in parts[1:]): break   # "cyberpunk 2077": the style word is part of the name
                parts.pop(0)
            name = ' '.join(parts)
            words = [w for w in re.findall(r"[\w'&.-]+", name.lower())]
            if words and not all(w in GENERIC for w in words) and name.lower() not in (c.lower() for c in out): out.append(name)
    return out[:2]


def _get(path, timeout):
    req = urllib.request.Request(API + path, headers={'User-Agent': AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read(400_000).decode('utf-8', 'replace'))


def _close(name, title):
    plain = re.sub(r'\(.*?\)', '', title)
    if re.sub(r'\W', '', name.lower()) == re.sub(r'\W', '', plain.lower()): return True   # "spiderman" is "Spider-Man"
    a, b = set(re.findall(r'\w+', name.lower())), set(re.findall(r'\w+', plain.lower()))
    return bool(a) and len(a & b) / len(a) >= 0.6


def lookup(text, timeout=6):
    """{'title', 'summary'} for the first name in the request that Wikipedia knows, or None (offline, no match, or no name)."""
    for name in candidates(text):
        key = name.lower()
        with _lock:
            if key in _cache:
                if _cache[key]: return _cache[key]
                continue
        found = None
        try:
            hits = _get('/w/api.php?' + urllib.parse.urlencode(dict(action='opensearch', search=name, limit=3, namespace=0, format='json')), timeout)
            title = next((t for t in (hits[1] if len(hits) > 1 else []) if _close(name, t)), None)
            if title:
                page = _get('/api/rest_v1/page/summary/' + urllib.parse.quote(title.replace(' ', '_')), timeout)
                summary = re.sub(r'\s+', ' ', page.get('extract') or '').strip()
                if summary and page.get('type') != 'disambiguation': found = dict(title=page.get('title') or title, summary=summary[:700])
        except (OSError, ValueError, KeyError, IndexError):
            return None   # offline or blocked: never in the way of the request
        with _lock: _cache[key] = found
        if found: return found
    return None
