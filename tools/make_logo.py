"""Draw the Asset Lab logo, a sibling of the gamedecomp logo: a rounded gradient tile with a dark outline mark.

gamedecomp uses a violet-to-cyan tile with a diamond; Asset Lab uses cyan-to-pink (Mitosis's colors) with two
overlapping rings, a cell dividing. Writes src/web/icon.svg, src/web/logo.png and launcher/application.ico.
"""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
START, END, INK = (34, 211, 238), (244, 114, 182), (7, 8, 12)  # cyan -> pink, dark mark
# Geometry on a 40-unit tile (as gamedecomp's 40 px logo): radius 13, rings r=6.2 with a 3-unit stroke.
RINGS = ((16.4, 20.0), (23.6, 20.0))
R, STROKE = 5.9, 2.6
SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#f472b6"/></linearGradient></defs>
<rect width="40" height="40" rx="13" fill="url(#g)"/>
<g fill="none" stroke="#07080c" stroke-width="{STROKE}">{''.join(f'<circle cx="{x}" cy="{y}" r="{R}"/>' for x, y in RINGS)}</g>
</svg>
'''


def draw(size):
    s = size * 8  # supersample, then shrink for smooth edges
    u = s / 40
    # 135 degree gradient, as CSS draws it: top left START, bottom right END.
    diagonal = Image.frombytes('L', (s, s), bytes(round(255 * (x + y) / (2 * s - 2)) for y in range(s) for x in range(s)))
    tile = Image.composite(Image.new('RGB', (s, s), END), Image.new('RGB', (s, s), START), diagonal).convert('RGBA')
    mask = Image.new('L', (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=13 * u, fill=255)
    tile.putalpha(mask)
    d = ImageDraw.Draw(tile)
    for x, y in RINGS:
        d.ellipse((u * (x - R - STROKE / 2), u * (y - R - STROKE / 2), u * (x + R + STROKE / 2), u * (y + R + STROKE / 2)),
                  outline=INK, width=round(STROKE * u))
    return tile.resize((size, size), Image.Resampling.LANCZOS)


def main():
    web = ROOT / 'src' / 'web'
    draw(512).save(web / 'logo.png')
    (web / 'icon.svg').write_text(SVG, encoding='utf-8')
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    draw(256).save(ROOT / 'launcher' / 'application.ico', sizes=[(n, n) for n in sizes], append_images=[draw(n) for n in sizes[:-1]])
    print('logo written')


if __name__ == '__main__': main()
