"""Inspiration from Wikipedia: when a request names someone or something ("a Post Malone theme", "inspired by Minecraft"),
its short public summary helps the AI get the look right (signature colors, symbols, style).

Only the name goes out, and only when Settings > Theme AI & drawing > Look things up for inspiration is on. Moods, colors
and the game's own parts are never looked up, and a match must really be that name, so "dark gold vibes" stays local.
"""
import hashlib
import io
import json
from pathlib import Path
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
make give turn change redo redesign reskin colors colours color colour mood
design designs create build do apply want need please can could would you i let lets let's something some into to'''.split())
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


IMAGES = ('https://upload.wikimedia.org/', 'https://thumb.wikimedia.org/')   # Wikipedia's own picture servers: the only places a picture is taken from


# Pictures on a page that are Wikipedia's own furniture, not the subject (icons, flags, maps, signatures...).
FURNITURE = re.compile(r'commons-logo|wiki|question_book|ambox|edit-|symbol_|flag_of|padlock|folder|oojs|disambig|portal|nuvola|crystal|'
                       r'map|locator|signature|increase|decrease|steady|star_full|stub|red_pencil|text-x|people_icon|office-|lock-|p_', re.I)
EMBLEM = re.compile(r'logo|emblem|roundel|badge|crest|seal|coat_of_arms|insignia|mark', re.I)


def _download(url, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.geturl() and not r.geturl().startswith(IMAGES): raise ValueError('left Wikimedia')
        data = r.read(6_000_001)
    if len(data) > 6_000_000: raise ValueError('too big')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert('RGBA')
        flat = Image.new('RGB', im.size, (255, 255, 255)); flat.paste(im, mask=im.getchannel('A'))   # transparent logos on white
        return flat


def _page_pictures(title, timeout, limit=2):
    """Up to `limit` more pictures of the page itself, emblems and logos first (BMW's roundel, a band's logo), then photos."""
    listed = _get('/w/api.php?' + urllib.parse.urlencode(dict(action='query', titles=title, prop='images', imlimit=80, format='json')), timeout)
    names = [i['title'] for p in (listed.get('query', {}).get('pages') or {}).values() for i in p.get('images') or []]
    names = [n for n in names if not FURNITURE.search(n) and re.search(r'\.(svg|png|jpe?g)$', n, re.I)]
    names.sort(key=lambda n: (not EMBLEM.search(n), not re.search(re.escape(title.split()[0]), n, re.I), n.lower().endswith('.svg') is False))
    if not names: return []
    info = _get('/w/api.php?' + urllib.parse.urlencode(dict(action='query', titles='|'.join(names[:6]), prop='imageinfo', iiprop='url', iiurlwidth=500, format='json')), timeout)
    urls = {p.get('title'): (p.get('imageinfo') or [{}])[0].get('thumburl', '') for p in (info.get('query', {}).get('pages') or {}).values()}
    return [urls[n] for n in names[:6] if urls.get(n, '').startswith(IMAGES)][:limit]


def pictures(found, folder, timeout=8):
    """A reference board for the image model: the page's lead picture and up to two of its own pictures (emblems first),
    side by side on white, as one PNG (at most 1024 px) in folder. Its file name, or '' when there is nothing or the
    network says no. Kept on this PC; the drawings take their look from it, not from words alone."""
    if not found: return ''
    name = hashlib.sha1(('board:' + found.get('title', '')).encode()).hexdigest()[:20] + '.png'
    path = Path(folder) / name
    if path.is_file(): return name
    try:
        urls = [u for u in [found.get('image') or ''] if u.startswith(IMAGES)]
        file = lambda u: urllib.parse.unquote(u.split('?')[0].split('/thumb/')[-1].split('/')[2] if '/thumb/' in u else u.split('?')[0].rsplit('/', 1)[-1]).lower()
        try:
            for u in _page_pictures(found['title'], timeout, limit=4):
                if file(u) not in {file(x) for x in urls}: urls.append(u)   # the same file at another size is not a new picture
        except (OSError, ValueError, KeyError): pass
        # Emblems first: the lead picture can be a corporate wordmark ("BMW Group") while the page also has the badge.
        urls.sort(key=lambda u: not EMBLEM.search(file(u)) or 'group' in file(u))
        shots = []
        for url in urls[:3]:
            try: shots.append(_download(url, timeout))
            except Exception: continue
        if not shots: return ''
        from PIL import Image
        height = 512
        shots = [s.resize((max(1, round(s.width * height / s.height)), height), Image.Resampling.LANCZOS) for s in shots]
        board = Image.new('RGB', (sum(s.width for s in shots) + 16 * (len(shots) - 1), height), (255, 255, 255))
        x = 0
        for s in shots: board.paste(s, (x, 0)); x += s.width + 16
        board.thumbnail((1024, 1024))
        path.parent.mkdir(parents=True, exist_ok=True)
        board.save(path, 'PNG')
        return name
    except Exception:
        return ''   # never in the way of the request


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
                # The page's lead picture (a logo, a portrait, a poster): what the drawings are inspired by, not just words.
                # Wikimedia serves only its own listed sizes, so the URL is used as given (picture() shrinks it here).
                thumb = (page.get('originalimage') or {}).get('source') or (page.get('thumbnail') or {}).get('source') or ''
                if summary and page.get('type') != 'disambiguation':
                    found = dict(title=page.get('title') or title, summary=summary[:700], image=thumb if thumb.startswith(IMAGES) else '')
        except (OSError, ValueError, KeyError, IndexError):
            return None   # offline or blocked: never in the way of the request
        with _lock: _cache[key] = found
        if found: return found
    return None
