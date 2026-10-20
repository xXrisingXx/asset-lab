"""Country flags drawn exactly, from their shapes (stripes, crosses, discs, stars...), so "make the virus the Norway flag"
gets the real flag every time; Image AI tends to get a flag's details wrong. Each flag is a list of shapes on a unit
canvas (x 0..1 left to right, y 0..1 top to bottom) with its own width:height. Image AI gets the flag a request names as
a reference picture (design.Designer.start), so it copies the real thing.
"""
import math
import re

from PIL import Image, ImageDraw

SS = 4   # drawn this many times larger, then scaled down: clean edges


def _star(cx, cy, r, points=5, inner=0.382, rot=-90):
    pts = []
    for i in range(points * 2):
        a = math.radians(rot + i * 180 / points); rr = r if i % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


# A flag: (width / height, [shapes]). Shapes: ('rect', x0, y0, x1, y1, color), ('poly', [(x, y)...], color),
# ('circle', cx, cy, r, color) (r in heights), ('star', cx, cy, r, color[, points, rotation]), ('cross', cx, cy, w, color)
# (bars through the whole flag; cx, cy, w in widths and heights), ('saltire', w, color).
def h(*colors, weights=None):
    w = weights or [1] * len(colors); t = sum(w); y = 0; out = []
    for c, k in zip(colors, w): out.append(('rect', 0, y / t, 1, (y + k) / t, c)); y += k
    return out


def v(*colors, weights=None):
    w = weights or [1] * len(colors); t = sum(w); x = 0; out = []
    for c, k in zip(colors, w): out.append(('rect', x / t, 0, (x + k) / t, 1, c)); x += k
    return out


def nordic(field, cross, border=None, ratio=(22, 16), cross_w=2, border_w=1, at=8):
    """A Nordic cross: its upright toward the hoist (at: its middle, in flag units from the left)."""
    W, H = ratio; out = [('rect', 0, 0, 1, 1, field)]
    if border: out += [('cross', at / W, 0.5, (cross_w + 2 * border_w) / H, border)]
    return out + [('cross', at / W, 0.5, cross_w / H, cross)]


RED, WHITE, BLUE, GREEN, YELLOW, BLACK = '#ce1126', '#ffffff', '#002868', '#009246', '#fcd116', '#000000'
UNION = [('rect', 0, 0, 1, 1, '#012169'), ('saltire', 0.2, WHITE), ('saltire', 0.067, '#c8102e'),
         ('cross', 0.5, 0.5, 1 / 3, WHITE), ('cross', 0.5, 0.5, 0.2, '#c8102e')]


def canton(shapes, x1, y1):
    """Shapes drawn into the top-left corner (x1 by y1 of the flag), like the Union Jack on Australia's flag."""
    out = []
    for s in shapes:
        k = s[0]
        if k == 'rect': out.append(('rect', s[1] * x1, s[2] * y1, s[3] * x1, s[4] * y1, s[5]))
        elif k == 'cross': out.append(('ccross', s[1] * x1, s[2] * y1, s[3] * y1, s[4], x1, y1))
        elif k == 'saltire': out.append(('csaltire', s[1], s[2], x1, y1))
        else: out.append(s)
    return out


FLAGS = {
    # Nordic crosses
    'norway': (22 / 16, nordic('#ba0c2f', '#00205b', WHITE)),
    'sweden': (16 / 10, nordic('#006aa7', '#fecc02', ratio=(16, 10), cross_w=2, at=6)),
    'denmark': (37 / 28, nordic('#c8102e', WHITE, ratio=(37, 28), cross_w=4, at=14)),
    'finland': (18 / 11, nordic(WHITE, '#002f6c', ratio=(18, 11), cross_w=3, at=6.5)),
    'iceland': (25 / 18, nordic('#02529c', '#dc1e35', WHITE, ratio=(25, 18), cross_w=2, border_w=1, at=9)),
    # Stripes
    'germany': (5 / 3, h('#000000', '#dd0000', '#ffce00')),
    'netherlands': (3 / 2, h('#ae1c28', WHITE, '#21468b')),
    'russia': (3 / 2, h(WHITE, '#0039a6', '#d52b1e')),
    'austria': (3 / 2, h('#ed2939', WHITE, '#ed2939')),
    'hungary': (2, h('#ce2939', WHITE, '#477050')),
    'bulgaria': (5 / 3, h(WHITE, '#00966e', '#d62612')),
    'lithuania': (5 / 3, h('#fdb913', '#006a44', '#c1272d')),
    'estonia': (11 / 7, h('#0072ce', BLACK, WHITE)),
    'latvia': (2, h('#9e3039', WHITE, '#9e3039', weights=[2, 1, 2])),
    'ukraine': (3 / 2, h('#0057b7', '#ffd700')),
    'poland': (8 / 5, h(WHITE, '#dc143c')),
    'indonesia': (3 / 2, h('#ff0000', WHITE)),
    'monaco': (5 / 4, h('#ce1126', WHITE)),
    'luxembourg': (5 / 3, h('#ed2939', WHITE, '#00a1de')),
    'yemen': (3 / 2, h('#ce1126', WHITE, BLACK)),
    'sierra leone': (3 / 2, h('#1eb53a', WHITE, '#0072c6')),
    'gabon': (4 / 3, h('#009e60', '#fcd116', '#3a75c4')),
    'colombia': (3 / 2, h('#fcd116', '#003893', '#ce1126', weights=[2, 1, 1])),
    'armenia': (2, h('#d90012', '#0033a0', '#f2a800')),
    'spain': (3 / 2, h('#aa151b', '#f1bf00', '#aa151b', weights=[1, 2, 1])),
    'thailand': (3 / 2, h('#a51931', '#f4f5f8', '#2d2a4a', '#f4f5f8', '#a51931', weights=[1, 1, 2, 1, 1])),
    'france': (3 / 2, v('#002654', WHITE, '#ce1126')),
    'italy': (3 / 2, v('#009246', WHITE, '#ce2b37')),
    'ireland': (2, v('#169b62', WHITE, '#ff883e')),
    'belgium': (15 / 13, v(BLACK, '#fdda24', '#ef3340')),
    'romania': (3 / 2, v('#002b7f', '#fcd116', '#ce1126')),
    'nigeria': (2, v('#008751', WHITE, '#008751')),
    'chad': (3 / 2, v('#002664', '#fecb00', '#c60c30')),
    'mali': (3 / 2, v('#14b53a', '#fcd116', '#ce1126')),
    'guinea': (3 / 2, v('#ce1126', '#fcd116', '#009460')),
    'ivory coast': (3 / 2, v('#f77f00', WHITE, '#009e60')),
    'peru': (3 / 2, v('#d91023', WHITE, '#d91023')),
    'canada': (2, v('#d52b1e', WHITE, '#d52b1e', weights=[1, 2, 1]) + [('poly', [
        (0.5, 0.14), (0.535, 0.22), (0.575, 0.2), (0.565, 0.36), (0.62, 0.29), (0.635, 0.33), (0.68, 0.31), (0.665, 0.42),
        (0.69, 0.44), (0.6, 0.53), (0.615, 0.58), (0.51, 0.565), (0.513, 0.75), (0.487, 0.75), (0.49, 0.565), (0.385, 0.58),
        (0.4, 0.53), (0.31, 0.44), (0.335, 0.42), (0.32, 0.31), (0.365, 0.33), (0.38, 0.29), (0.435, 0.36), (0.425, 0.2),
        (0.465, 0.22)], '#d52b1e')]),
    # Crosses and saltires
    'switzerland': (1, [('rect', 0, 0, 1, 1, '#da291c'), ('rect', 0.4, 0.1875, 0.6, 0.8125, WHITE), ('rect', 0.1875, 0.4, 0.8125, 0.6, WHITE)]),
    'england': (5 / 3, [('rect', 0, 0, 1, 1, WHITE), ('cross', 0.5, 0.5, 0.2, '#ce1124')]),
    'scotland': (5 / 3, [('rect', 0, 0, 1, 1, '#005eb8'), ('saltire', 0.2, WHITE)]),
    'united kingdom': (2, UNION),
    'georgia': (3 / 2, [('rect', 0, 0, 1, 1, WHITE), ('cross', 0.5, 0.5, 0.2, '#ff0000')] +
                [('rect', cx - 0.03, cy - 0.11, cx + 0.03, cy + 0.11, '#ff0000') for cx, cy in ((0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8))] +
                [('rect', cx - 0.07, cy - 0.04, cx + 0.07, cy + 0.04, '#ff0000') for cx, cy in ((0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8))]),
    'greece': (3 / 2, h(*(['#0d5eaf', WHITE] * 4 + ['#0d5eaf'])) + [('rect', 0, 0, 10 / 27, 5 / 9, '#0d5eaf'),
               ('rect', 4 / 27, 0, 6 / 27, 5 / 9, WHITE), ('rect', 0, 2 / 9, 10 / 27, 3 / 9, WHITE)]),
    'jamaica': (2, [('rect', 0, 0, 1, 1, '#009b3a'), ('poly', [(0, 0), (0.5, 0.5), (0, 1)], BLACK), ('poly', [(1, 0), (0.5, 0.5), (1, 1)], BLACK),
                    ('saltire', 0.15, '#fed100')]),
    # Discs, suns, stars and crescents
    'japan': (3 / 2, [('rect', 0, 0, 1, 1, WHITE), ('circle', 0.5, 0.5, 0.3, '#bc002d')]),
    'bangladesh': (5 / 3, [('rect', 0, 0, 1, 1, '#006a4e'), ('circle', 0.45, 0.5, 0.2, '#f42a41')]),
    'palau': (8 / 5, [('rect', 0, 0, 1, 1, '#4aadd6'), ('circle', 0.45, 0.5, 0.3, '#ffde00')]),
    'china': (3 / 2, [('rect', 0, 0, 1, 1, '#ee1c25'), ('star', 0.167, 0.25, 0.15, '#ffff00')] +
              [('star', x, y, 0.05, '#ffff00', 5, r) for x, y, r in ((0.333, 0.1, -60), (0.4, 0.2, -80), (0.4, 0.35, -100), (0.333, 0.45, -120))]),
    'vietnam': (3 / 2, [('rect', 0, 0, 1, 1, '#da251d'), ('star', 0.5, 0.5, 0.3, '#ffff00')]),
    'somalia': (3 / 2, [('rect', 0, 0, 1, 1, '#4189dd'), ('star', 0.5, 0.5, 0.25, WHITE)]),
    'morocco': (3 / 2, [('rect', 0, 0, 1, 1, '#c1272d'), ('star', 0.5, 0.5, 0.26, '#006233'), ('star', 0.5, 0.5, 0.2, '#c1272d')]),
    'turkey': (3 / 2, [('rect', 0, 0, 1, 1, '#e30a17'), ('crescent', 0.35, 0.5, 0.25, 0.05, 0.0, 0.2, WHITE), ('star', 0.53, 0.5, 0.125, WHITE, 5, 180)]),
    'pakistan': (3 / 2, [('rect', 0, 0, 1, 1, '#01411c'), ('rect', 0, 0, 0.25, 1, WHITE), ('crescent', 0.625, 0.5, 0.3, 0.06, -0.06, 0.27, WHITE),
                         ('star', 0.71, 0.35, 0.1, WHITE, 5, -45)]),
    'algeria': (3 / 2, v('#006233', WHITE) + [('crescent', 0.5, 0.5, 0.25, 0.06, 0.0, 0.2, '#d21034'), ('star', 0.56, 0.5, 0.11, '#d21034', 5, 180)]),
    'israel': (11 / 8, [('rect', 0, 0, 1, 1, WHITE), ('rect', 0, 0.1, 1, 0.25, '#0038b8'), ('rect', 0, 0.75, 1, 0.9, '#0038b8'),
                        ('hexagram', 0.5, 0.5, 0.19, '#0038b8')]),
    'ghana': (3 / 2, h('#ce1126', '#fcd116', '#006b3f') + [('star', 0.5, 0.5, 0.16, BLACK)]),
    'senegal': (3 / 2, v('#00853f', '#fdef42', '#e31b23') + [('star', 0.5, 0.5, 0.16, '#00853f')]),
    'cameroon': (3 / 2, v('#007a5e', '#ce1126', '#fcd116') + [('star', 0.5, 0.5, 0.16, '#fcd116')]),
    'burkina faso': (3 / 2, h('#ef2b2d', '#009e49') + [('star', 0.5, 0.5, 0.16, '#fcd116')]),
    'myanmar': (3 / 2, h('#fecb00', '#34b233', '#ea2839') + [('star', 0.5, 0.53, 0.38, WHITE)]),
    'argentina': (8 / 5, h('#74acdf', WHITE, '#74acdf') + [('circle', 0.5, 0.5, 0.12, '#f6b40e')]),
    'uruguay': (3 / 2, h(*([WHITE, '#0038a8'] * 4 + [WHITE])) + [('rect', 0, 0, 0.5 * 2 / 3, 5 / 9, WHITE), ('circle', 0.167, 0.278, 0.13, '#fcd116')]),
    'india': (3 / 2, h('#ff9933', WHITE, '#138808') + [('circle', 0.5, 0.5, 0.14, '#000080'), ('circle', 0.5, 0.5, 0.115, WHITE),
                                                       ('circle', 0.5, 0.5, 0.03, '#000080')]),
    'brazil': (10 / 7, [('rect', 0, 0, 1, 1, '#009c3b'), ('poly', [(0.085, 0.5), (0.5, 0.06), (0.915, 0.5), (0.5, 0.94)], '#ffdf00'),
                        ('circle', 0.5, 0.5, 0.25, '#002776')]),
    'philippines': (2, h('#0038a8', '#ce1126') + [('poly', [(0, 0), (0.433, 0.5), (0, 1)], WHITE), ('circle', 0.144, 0.5, 0.11, '#fcd116')] +
                    [('star', x, y, 0.06, '#fcd116') for x, y in ((0.05, 0.12), (0.05, 0.88), (0.36, 0.5))]),
    'czechia': (3 / 2, h(WHITE, '#d7141a') + [('poly', [(0, 0), (0.5, 0.5), (0, 1)], '#11457e')]),
    'cuba': (2, h(*(['#002a8f', WHITE] * 2 + ['#002a8f'])) + [('poly', [(0, 0), (0.433, 0.5), (0, 1)], '#cf142b'), ('star', 0.144, 0.5, 0.13, WHITE)]),
    'puerto rico': (3 / 2, h(*(['#ed0000', WHITE] * 2 + ['#ed0000'])) + [('poly', [(0, 0), (0.433, 0.5), (0, 1)], '#0050f0'), ('star', 0.144, 0.5, 0.13, WHITE)]),
    'chile': (3 / 2, h(WHITE, '#d52b1e') + [('rect', 0, 0, 1 / 3, 0.5, '#0039a6'), ('star', 1 / 6, 0.25, 0.1, WHITE)]),
    'texas': (3 / 2, h(WHITE, '#bf0a30') + [('rect', 0, 0, 1 / 3, 1, '#002868'), ('star', 1 / 6, 0.5, 0.16, WHITE)]),
    'palestine': (2, h(BLACK, WHITE, '#007a3d') + [('poly', [(0, 0), (1 / 3, 0.5), (0, 1)], '#ce1126')]),
    'sudan': (2, h('#d21034', WHITE, BLACK) + [('poly', [(0, 0), (1 / 3, 0.5), (0, 1)], '#007229')]),
    'united arab emirates': (2, h('#00732f', WHITE, BLACK) + [('rect', 0, 0, 0.25, 1, '#ff0000')]),
    'south africa': (3 / 2, [('rect', 0, 0, 1, 0.5, '#e03c31'), ('rect', 0, 0.5, 1, 1, '#001489'),
                             ('poly', [(0, 0), (0.12, 0), (0.48, 0.33), (1, 0.33), (1, 0.67), (0.48, 0.67), (0.12, 1), (0, 1)], WHITE),
                             ('poly', [(0, 0.06), (0.43, 0.4), (1, 0.4), (1, 0.6), (0.43, 0.6), (0, 0.94)], '#007749'),
                             ('poly', [(0, 0.17), (0.33, 0.5), (0, 0.83)], '#ffb81c'), ('poly', [(0, 0.24), (0.26, 0.5), (0, 0.76)], BLACK)]),
    'united states': (19 / 10, h(*(['#b22234', WHITE] * 6 + ['#b22234'])) + [('rect', 0, 0, 0.4, 7 / 13, '#3c3b6e')] +
                      [('star', 0.4 * (c + 0.5 + 0.5 * (r % 2)) / 6, (7 / 13) * (r + 0.5) / 9, 0.022, WHITE)
                       for r in range(9) for c in range(6 - r % 2)]),
    'australia': (2, canton(UNION, 0.5, 0.5) + [('rect', 0.5, 0, 1, 1, '#012169'), ('rect', 0, 0.5, 0.5, 1, '#012169'),
                  ('star', 0.25, 0.75, 0.15, WHITE, 7)] + [('star', x, y, 0.07, WHITE, 7) for x, y in ((0.75, 0.83), (0.62, 0.42), (0.75, 0.17), (0.87, 0.36))]),
    'new zealand': (2, canton(UNION, 0.5, 0.5) + [('rect', 0.5, 0, 1, 1, '#012169'), ('rect', 0, 0.5, 0.5, 1, '#012169')] +
                    [('star', x, y, 0.08, WHITE) for x, y in ((0.75, 0.83), (0.66, 0.42), (0.75, 0.17), (0.86, 0.36))] +
                    [('star', x, y, 0.055, '#c8102e') for x, y in ((0.75, 0.83), (0.66, 0.42), (0.75, 0.17), (0.86, 0.36))]),
    'mexico': (7 / 4, v('#006847', WHITE, '#ce1126') + [('circle', 0.5, 0.5, 0.13, '#8c5a2b')]),
    'portugal': (3 / 2, v('#046a38', '#da291c', weights=[2, 3]) + [('circle', 0.4, 0.5, 0.21, '#ffe900'), ('circle', 0.4, 0.5, 0.15, '#da291c'),
                                                                 ('circle', 0.4, 0.5, 0.1, WHITE)]),
    'egypt': (3 / 2, h('#ce1126', WHITE, BLACK) + [('circle', 0.5, 0.5, 0.11, '#c09300')]),
    'kenya': (3 / 2, h(BLACK, WHITE, '#bb0000', WHITE, '#006600', weights=[6, 1, 6, 1, 6]) + [('ellipse', 0.5, 0.5, 0.09, 0.4, '#bb0000')]),
    # Not countries, asked for all the same
    'pride': (3 / 2, h('#e40303', '#ff8c00', '#ffed00', '#008026', '#004dff', '#750787')),
    'rainbow': (3 / 2, h('#e40303', '#ff8c00', '#ffed00', '#008026', '#004dff', '#750787')),
    'transgender': (5 / 3, h('#5bcefa', '#f5a9b8', WHITE, '#f5a9b8', '#5bcefa')),
    'european union': (3 / 2, [('rect', 0, 0, 1, 1, '#003399')] + [('star', 0.5 + 0.22 * math.cos(math.radians(a)) / 1.5, 0.5 + 0.33 * math.sin(math.radians(a)), 0.035, '#ffcc00')
                                                                   for a in range(0, 360, 30)]),
}
NAMES = {   # other words for the same flags
    'norwegian': 'norway', 'swedish': 'sweden', 'danish': 'denmark', 'finnish': 'finland', 'icelandic': 'iceland',
    'german': 'germany', 'dutch': 'netherlands', 'holland': 'netherlands', 'russian': 'russia', 'austrian': 'austria',
    'hungarian': 'hungary', 'bulgarian': 'bulgaria', 'lithuanian': 'lithuania', 'estonian': 'estonia', 'latvian': 'latvia',
    'ukrainian': 'ukraine', 'polish': 'poland', 'indonesian': 'indonesia', 'colombian': 'colombia', 'spanish': 'spain',
    'thai': 'thailand', 'french': 'france', 'italian': 'italy', 'irish': 'ireland', 'belgian': 'belgium', 'romanian': 'romania',
    'nigerian': 'nigeria', 'peruvian': 'peru', 'canadian': 'canada', 'swiss': 'switzerland', 'english': 'england',
    'scottish': 'scotland', 'british': 'united kingdom', 'uk': 'united kingdom', 'great britain': 'united kingdom', 'union jack': 'united kingdom',
    'greek': 'greece', 'jamaican': 'jamaica', 'japanese': 'japan', 'chinese': 'china', 'vietnamese': 'vietnam', 'moroccan': 'morocco',
    'turkish': 'turkey', 'turkiye': 'turkey', 'pakistani': 'pakistan', 'algerian': 'algeria', 'israeli': 'israel', 'ghanaian': 'ghana',
    'argentinian': 'argentina', 'argentine': 'argentina', 'indian': 'india', 'brazilian': 'brazil', 'filipino': 'philippines', 'czech': 'czechia', 'czech republic': 'czechia', 'cuban': 'cuba', 'chilean': 'chile', 'texan': 'texas',
    'palestinian': 'palestine', 'uae': 'united arab emirates', 'emirati': 'united arab emirates', 'south african': 'south africa',
    'american': 'united states', 'usa': 'united states', 'us': 'united states', 'america': 'united states', 'united states of america': 'united states',
    'australian': 'australia', 'kiwi': 'new zealand', 'mexican': 'mexico', 'portuguese': 'portugal', 'egyptian': 'egypt', 'kenyan': 'kenya',
    'lgbt': 'pride', 'lgbtq': 'pride', 'trans': 'transgender', 'eu': 'european union', 'europe': 'european union', 'european': 'european union',
    'uruguayan': 'uruguay', 'senegalese': 'senegal', 'armenian': 'armenia', 'georgian': 'georgia', 'somali': 'somalia', 'sudanese': 'sudan',
    'puerto rican': 'puerto rico', 'bangladeshi': 'bangladesh', 'burmese': 'myanmar', 'malian': 'mali', 'chadian': 'chad',
}
# "the Norway flag", "the flag of Japan", "norwegian flag", "a japan ball", "countryball of brazil"
_WORDS = sorted(set(FLAGS) | set(NAMES), key=len, reverse=True)
_ALT = '|'.join(re.escape(w) for w in _WORDS)
FLAG_REQUEST = re.compile(rf'\b(?:flags?\s+of\s+(?:the\s+)?(?P<a>{_ALT})|(?P<b>{_ALT})(?:\'?s)?\s+(?:flag|country\s?ball|ball))\b', re.I)


def find(text):
    """The flag a request names, or ''."""
    m = FLAG_REQUEST.search(str(text or ''))
    if not m: return ''
    word = (m.group('a') or m.group('b')).lower()
    return NAMES.get(word, word)


def draw(name, height=400):
    """The flag as a picture, height pixels tall (RGBA)."""
    ratio, shapes = FLAGS[name]
    H = height * SS; W = round(H * ratio)
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    X = lambda x: x * W
    Y = lambda y: y * H
    for s in shapes:
        k = s[0]
        if k == 'rect':
            d.rectangle((X(s[1]), Y(s[2]), X(s[3]) - 1, Y(s[4]) - 1), fill=s[5])
        elif k == 'poly': d.polygon([(X(x), Y(y)) for x, y in s[1]], fill=s[2])
        elif k == 'circle': d.ellipse((X(s[1]) - s[3] * H, Y(s[2]) - s[3] * H, X(s[1]) + s[3] * H, Y(s[2]) + s[3] * H), fill=s[4])
        elif k == 'ellipse': d.ellipse((X(s[1]) - s[3] * H, Y(s[2]) - s[4] * H, X(s[1]) + s[3] * H, Y(s[2]) + s[4] * H), fill=s[5])
        elif k == 'crescent':   # a disc with a smaller one cut out of it, over whatever is beneath
            cx, cy, r, ox, oy, ri = X(s[1]), Y(s[2]), s[3] * H, s[4] * H, s[5] * H, s[6] * H
            m = Image.new('L', im.size, 0); dm = ImageDraw.Draw(m)
            dm.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255); dm.ellipse((cx + ox - ri, cy + oy - ri, cx + ox + ri, cy + oy + ri), fill=0)
            im.paste(s[7], mask=m)
        elif k == 'star':
            pts, rot = (s[5] if len(s) > 5 else 5), (s[6] if len(s) > 6 else -90)
            d.polygon(_star(X(s[1]), Y(s[2]), s[3] * H, pts, 0.382 if pts == 5 else 0.5, rot), fill=s[4])
        elif k == 'hexagram':
            for rot in (-90, 90):
                tri = [(X(s[1]) + s[3] * H * math.cos(math.radians(rot + a)), Y(s[2]) + s[3] * H * math.sin(math.radians(rot + a))) for a in (0, 120, 240)]
                d.polygon(tri, outline=s[4], width=max(1, int(0.03 * H)))
        elif k in ('cross', 'ccross'):
            w = s[3] * H; x0, y0 = X(s[1]), Y(s[2])
            x1, y1 = (W, H) if k == 'cross' else (X(s[5]), Y(s[6]))
            d.rectangle((x0 - w / 2, 0, x0 + w / 2, y1), fill=s[4]); d.rectangle((0, y0 - w / 2, x1, y0 + w / 2), fill=s[4])
        elif k in ('saltire', 'csaltire'):
            x1, y1 = (W, H) if k == 'saltire' else (X(s[3]), Y(s[4]))
            w = s[1] * H
            d.line([(0, 0), (x1, y1)], fill=s[2], width=int(w)); d.line([(0, y1), (x1, 0)], fill=s[2], width=int(w))
    return im.resize((W // SS, H // SS), Image.Resampling.LANCZOS)
