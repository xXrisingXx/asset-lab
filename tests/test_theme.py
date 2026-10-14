import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import numpy as np
from PIL import Image
from fixtures import make_game
from lab_core import Lab, png, file_hash
import theme
from theme import Themes, clean_plan, rule_plan, shade


def op(**fields): return clean_plan(dict(operations=[dict(targets=['everything'], **fields)]))['operations'][0]


def stale_game(root):
    """A theme atlas with two live icons, nine leftover rectangles, and an identical copy inside assets.zip."""
    game = make_game(root)
    assets = game / 'assets'
    sheet = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
    sheet.paste((230, 60, 40, 255), (2, 2, 30, 30))      # icon-a, with transparent padding
    sheet.paste((255, 255, 255, 255), (42, 2, 70, 30))   # icon-b, white
    sheet.paste((40, 200, 90, 255), (2, 82, 14, 94))     # free-x
    sheet.paste((250, 250, 250, 255), (82, 82, 98, 98))  # virus-pale
    sheet.paste((25, 30, 35, 255), (106, 82, 122, 98))   # virus-deep
    rows = ['<SubTexture name="icon-a" x="0" y="0" width="32" height="32" />', '<SubTexture name="icon-b" x="40" y="0" width="32" height="32" />',
            '<SubTexture name="free-x" x="0" y="80" width="16" height="16" />', '<SubTexture name="virus-pale" x="80" y="80" width="20" height="20" />',
            '<SubTexture name="virus-deep" x="104" y="80" width="20" height="20" />']
    rows += [f'<SubTexture name="old-{i}" x="{10 + i * 3}" y="{12 + i}" width="36" height="30" />' for i in range(9)]
    atlas = '<TextureAtlas imagePath="theme.png">' + ''.join(rows) + '</TextureAtlas>'
    (assets / 'theme4.png').write_bytes(png(sheet))
    (assets / 'theme4.xml').write_text(atlas, encoding='utf-8')
    with zipfile.ZipFile(assets / 'assets.zip', 'w') as z:
        z.writestr('theme4.png', png(sheet))
        z.writestr('theme4.xml', atlas)
    (game / 'Mitosis.exe').write_bytes(b'QA fixture\0icon-a\0icon-b\0button-up-skin\0')
    return game


class Base(unittest.TestCase):
    maker = staticmethod(stale_game)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.maker(self.root)
        self.lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.themes = Themes(self.lab)

    def tearDown(self): self.temp.cleanup()

    def rows(self, name): return [r for r in self.lab.catalog.rows.values() if r['name'] == name]

    def run_plan(self, plan, prompt='test'):
        job = self.themes.apply(plan, prompt)
        for _ in range(400):
            state = self.themes.job(job['id'])
            if state['state'] != 'running': return state
            time.sleep(0.02)
        self.fail('theme job did not finish')


class AuditTests(Base):
    def test_leftover_rectangles_flagged_and_live_ones_kept(self):
        self.assertTrue(all(r['legacy'] for i in range(9) for r in self.rows(f'old-{i}')))
        for name in ('icon-a', 'icon-b', 'free-x'): self.assertFalse(any(r['legacy'] for r in self.rows(name)), name)
        # A tight, correctly packed atlas is never treated as stale, whatever the executable says.
        self.assertFalse(any(r.get('legacy') for r in self.rows('item-a') + self.rows('button-up-skin')))

    def test_identical_copies_collapse_to_the_loose_file(self):
        copies = self.rows('icon-a')
        self.assertEqual([r['copies'] for r in copies], [2, 2])
        self.assertEqual([r['source'] for r in copies if r['primary']], ['assets/theme4.png'])
        tidy = self.lab.search('icon-a', tidy=True)
        self.assertEqual(tidy['total'], 1)
        self.assertEqual(self.lab.search('icon-a')['total'], 2)
        self.assertEqual(self.lab.search('old-', tidy=True)['total'], 0)
        self.assertEqual(self.lab.search('old-')['total'], 18)

    def test_audit_cache_is_reused_and_invalidated(self):
        cache = self.root / 'workspace' / 'audit-cache.json'
        first = json.loads(cache.read_text())['signature']
        Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.assertEqual(json.loads(cache.read_text())['signature'], first)
        (self.game / 'Mitosis.exe').write_bytes(b'different build')
        fresh = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.assertNotEqual(json.loads(cache.read_text())['signature'], first)
        self.assertFalse(any(r.get('legacy') for r in fresh.catalog.rows.values() if r['name'] in ('icon-a', 'icon-b')))

    def test_sprite_edit_is_installed_into_every_copy(self):
        key = next(r['key'] for r in self.rows('icon-a') if r['primary'])
        im = self.lab.image(key)
        im.paste((10, 20, 200, 255), (0, 0, 32, 32))
        self.lab.save(key, png(im), 'original')
        build = self.lab.build()
        self.assertEqual(sorted(f['path'] for f in build['files']), ['assets/assets.zip', 'assets/theme4.png'])
        self.lab.install(build['id'])
        with zipfile.ZipFile(self.game / 'assets' / 'assets.zip') as z: packed = Image.open(io.BytesIO(z.read('theme4.png'))).convert('RGBA')
        loose = Image.open(self.game / 'assets' / 'theme4.png').convert('RGBA')
        for sheet in (packed, loose):
            self.assertEqual(sheet.getpixel((5, 5)), (10, 20, 200, 255))
            self.assertEqual(sheet.getpixel((50, 10)), (255, 255, 255, 255))
        self.lab.restore()
        self.assertEqual(Image.open(self.game / 'assets' / 'theme4.png').convert('RGBA').getpixel((5, 5)), (230, 60, 40, 255))


class ShadeTests(unittest.TestCase):
    pixels = np.array([[[230, 60, 40, 255], [255, 255, 255, 255], [0, 0, 0, 200], [90, 90, 90, 255], [10, 200, 30, 0]]], np.uint8)

    def test_tint_keeps_neutrals_and_alpha(self):
        out = shade(self.pixels, op(color='#0000ff', color_mode='tint'))
        self.assertGreater(out[0, 0, 2], 200); self.assertLess(out[0, 0, 0], 30)
        for i in (1, 2, 3): self.assertEqual(out[0, i].tolist(), self.pixels[0, i].tolist())
        self.assertEqual(out[0, 4].tolist(), self.pixels[0, 4].tolist())  # invisible pixels are never rewritten

    def test_colorize_and_fill(self):
        tinted = shade(self.pixels, op(color='#ff0000', color_mode='colorize'))
        self.assertEqual(tinted[0, 1].tolist(), [255, 0, 0, 255])
        self.assertEqual(tinted[0, 2].tolist(), [0, 0, 0, 200])
        flat = shade(self.pixels, op(color='#102030', color_mode='fill'))
        self.assertEqual(flat[0, 2].tolist(), [16, 32, 48, 200])

    def test_opacity_brightness_saturation_invert(self):
        self.assertEqual(shade(self.pixels, op(opacity=0))[0, 0, 3], 0)
        self.assertEqual(shade(self.pixels, op(opacity=4))[0, 2, 3], 255)
        self.assertEqual(shade(self.pixels, op(brightness=0.5))[0, 1].tolist(), [128, 128, 128, 255])
        grey = shade(self.pixels, op(saturation=0))[0, 0]
        self.assertEqual(len(set(grey[:3].tolist())), 1)
        self.assertEqual(shade(self.pixels, op(invert=True))[0, 1].tolist(), [0, 0, 0, 255])

    def test_plan_cleaning_clamps_and_drops_noise(self):
        plan = clean_plan(dict(reply='ok', operations=[
            dict(targets=['buttons', 'nonsense'], color='teal', brightness=99, opacity=-3, hue_shift='x', match=['a*', 5]),
            dict(targets=['icons']), 'junk', dict(targets=[], color='#12ab', saturation=True)]))
        self.assertEqual(len(plan['operations']), 1)
        only = plan['operations'][0]
        self.assertEqual((only['targets'], only['color'], only['color_mode'], only['brightness'], only['opacity'], only['hue_shift'], only['match']),
                         (['buttons'], '#14b8a6', 'tint', 3.0, 0.0, 0.0, ['a*']))
        with self.assertRaises(ValueError): clean_plan('not a plan')

    def test_keyword_planner(self):
        plan = rule_plan('Make everything purple but keep the buttons orange.')
        self.assertEqual([(o['targets'], o['color']) for o in plan['operations']], [(['everything'], '#9b5de5'), (['buttons'], '#ff8a1e')])
        plan = rule_plan('hide the main menu background pattern, buttons and panels darker')
        self.assertEqual([(o['targets'], o['opacity'], o['brightness']) for o in plan['operations']], [(['menu_background'], 0.0, 1.0), (['buttons', 'panels'], 1.0, 0.7)])
        plan = rule_plan('neon green cells and a dark blue arena')
        self.assertEqual([o['targets'] for o in plan['operations']], [['cells'], ['world_background']])
        self.assertEqual(plan['operations'][1]['brightness'], 1.0)
        self.assertEqual(rule_plan('buttons red and darker')['operations'][0]['brightness'], 0.7)
        self.assertEqual(rule_plan('black and white icons')['operations'][0]['saturation'], 0.0)
        self.assertEqual(rule_plan('what time is it')['operations'], [])


class EffectTests(unittest.TestCase):
    """A 24x24 sprite: an opaque white 12x12 square in the middle of transparent padding."""
    def setUp(self):
        self.sprite = np.zeros((24, 24, 4), np.uint8)
        self.sprite[6:18, 6:18] = (255, 255, 255, 255)

    def test_linear_gradient_runs_edge_to_edge_and_keeps_alpha(self):
        full = np.full((8, 32, 4), 255, np.uint8)
        out = shade(full, op(gradient=['#ff0000', '#0000ff']))
        self.assertGreater(out[4, 0, 0], 240); self.assertLess(out[4, 0, 2], 15)
        self.assertGreater(out[4, 31, 2], 240); self.assertLess(out[4, 31, 0], 15)
        self.assertTrue((out[..., 3] == 255).all())
        down = shade(full.transpose(1, 0, 2).copy(), op(gradient=['#ff0000', '#0000ff'], gradient_angle=90))
        self.assertGreater(down[0, 4, 0], 240); self.assertGreater(down[31, 4, 2], 240)
        self.assertEqual(shade(self.sprite, op(gradient_preset='rainbow'))[0, 0].tolist(), [0, 0, 0, 0])   # padding untouched

    def test_gradient_modes_presets_and_repeat(self):
        plan = clean_plan(dict(operations=[dict(targets=['buttons'], gradient='fire'), dict(targets=['icons'], gradient_preset='nope', gradient=['#fff'])]))
        self.assertEqual(len(plan['operations']), 1)
        self.assertEqual((plan['operations'][0]['gradient'], plan['operations'][0]['color_mode']), (theme.GRADIENTS['fire'], 'colorize'))
        grey = np.zeros((1, 3, 4), np.uint8); grey[0] = [(0, 0, 0, 255), (128, 128, 128, 255), (255, 255, 255, 255)]
        mapped = shade(grey, op(gradient=['#ff0000', '#00ff00', '#0000ff'], gradient_type='luminance'))
        self.assertEqual([int(np.argmax(p[:3])) for p in mapped[0]], [0, 1, 2])
        kept = shade(grey, op(gradient=['#ff0000', '#0000ff'], color_mode='tint'))                          # tint leaves neutrals alone
        self.assertEqual(kept.tolist(), grey.tolist())
        wide = np.full((2, 60, 4), 255, np.uint8)
        stripes = shade(wide, op(gradient=['#000000', '#ffffff'], gradient_repeat=3))[0, :, 0].astype(int)
        self.assertEqual(int((np.diff(stripes) < -100).sum()), 2)                                            # three ramps, two resets
        radial = shade(np.full((21, 21, 4), 255, np.uint8), op(gradient=['#ff0000', '#0000ff'], gradient_type='radial'))
        self.assertGreater(radial[10, 10, 0], radial[0, 0, 0]); self.assertGreater(radial[0, 0, 2], radial[10, 10, 2])

    def test_glow_spreads_into_padding_but_never_past_the_sprite(self):
        out = shade(self.sprite, op(glow_color='#00ffff', glow_size=8, glow_strength=2))
        self.assertEqual(out.shape, self.sprite.shape)
        self.assertEqual(out[12, 12].tolist(), [255, 255, 255, 255])                 # the art itself is unchanged
        halo = out[12, 3]
        self.assertGreater(halo[3], 20); self.assertLess(halo[0], 40); self.assertGreater(halo[1], 200)
        self.assertGreater(out[12, 4, 3], out[12, 1, 3])                             # fades with distance
        red = self.sprite.copy(); red[6:18, 6:18] = (255, 0, 0, 255)
        own = shade(red, op(glow_color='auto', glow_size=8, glow_strength=2))[12, 3]
        self.assertGreater(own[0], 200); self.assertLess(own[1], 40)                 # "auto" glows in the art's color
        inner = shade(red, op(glow_color='#ffffff', glow_size=6, glow_strength=2, glow_inside=True))
        self.assertGreater(inner[12, 6, 1], inner[12, 12, 1])                        # rim is brighter than the middle
        self.assertFalse(clean_plan(dict(operations=[dict(targets=['icons'], glow_color='auto', glow_strength=0)]))['operations'])

    def test_outline_gloss_posterize_pixelate_scanlines_grain(self):
        ring = shade(self.sprite, op(outline_color='#ff0000', outline_width=2))
        self.assertEqual(ring[12, 4].tolist(), [255, 0, 0, 255]); self.assertEqual(ring[12, 3, 3], 0)
        self.assertEqual(ring[12, 12].tolist(), [255, 255, 255, 255])
        dark = np.full((20, 4, 4), (40, 40, 40, 255), np.uint8)
        glossy = shade(dark, op(gloss=1))
        self.assertGreater(glossy[0, 0, 0], 200); self.assertEqual(glossy[19, 0, 0], 40)
        ramp = np.zeros((1, 256, 4), np.uint8); ramp[0, :, :3] = np.arange(256)[:, None]; ramp[..., 3] = 255
        self.assertEqual(len(np.unique(shade(ramp, op(posterize=4))[0, :, 0])), 4)
        noisy = np.zeros((16, 16, 4), np.uint8); noisy[..., 3] = 255; noisy[::2, ::2, :3] = 255
        blocks = shade(noisy, op(pixelate=4))
        self.assertTrue((blocks[0:4, 0:4] == blocks[0, 0]).all())
        lines = shade(np.full((8, 2, 4), 200, np.uint8), op(scanlines=0.5))[:, 0, 0].tolist()
        self.assertEqual(lines, [100, 100, 200, 200, 100, 100, 200, 200])
        flat = np.full((16, 16, 4), 128, np.uint8)
        self.assertEqual(shade(flat, op(grain=0.3)).tolist(), shade(flat, op(grain=0.3)).tolist())           # repeatable
        self.assertGreater(len(np.unique(shade(flat, op(grain=0.3))[..., 0])), 4)

    def test_neon_rim_follows_only_the_outer_edge_and_leaves_the_inside_alone(self):
        # A white ring (radius 13..17) with a white dot in its middle, like a round button with an icon.
        ys, xs = np.mgrid[0:48, 0:48]
        distance = np.hypot(xs - 23.5, ys - 23.5)
        button = np.zeros((48, 48, 4), np.uint8)
        button[(distance >= 13) & (distance <= 17)] = (255, 255, 255, 255)
        button[distance <= 4] = (255, 255, 255, 255)
        plan = clean_plan(dict(operations=[dict(targets=['buttons'], rim_color='gradient', glow_color='auto', glow_size=10, glow_strength=2)]))['operations'][0]
        self.assertEqual((plan['gradient'], plan['gradient_type'], plan['color_mode'], plan['rim_width']), (theme.GRADIENTS['rainbow'], 'conic', 'none', 3.0))
        out = shade(button, plan)
        self.assertEqual(out[23, 23].tolist(), [255, 255, 255, 255])                  # the icon inside stays white
        left, right, top = out[23, 7], out[23, 40], out[7, 23]                         # the ring's outer edge is colored
        for pixel in (left, right, top): self.assertGreater(int(pixel[:3].max()) - int(pixel[:3].min()), 120)
        self.assertGreater(np.abs(left[:3].astype(int) - right[:3].astype(int)).sum(), 150)   # and the color travels around it
        self.assertGreater(out[23, 4, 3], 30)                                          # glow outside the ring
        self.assertGreater(int(out[23, 4, :3].max()) - int(out[23, 4, :3].min()), 100) # in the rim's color, not white
        flat = clean_plan(dict(operations=[dict(targets=['panels'], rim_color='#00ffff', rim_width=2)]))['operations'][0]
        panel = shade(np.full((20, 20, 4), (30, 30, 30, 255), np.uint8), flat)         # a filled tile gets a border
        self.assertEqual(panel[10, 0].tolist(), [0, 255, 255, 255]); self.assertEqual(panel[10, 10].tolist(), [30, 30, 30, 255])
        conic = shade(np.full((21, 21, 4), 255, np.uint8), op(gradient=['#ff0000', '#00ff00', '#0000ff'], gradient_type='conic'))
        self.assertNotEqual(conic[10, 0].tolist(), conic[10, 20].tolist()); self.assertNotEqual(conic[0, 10].tolist(), conic[20, 10].tolist())

    def test_model_plans_are_tamed_to_what_was_asked(self):
        messy = clean_plan(dict(operations=[
            dict(targets=['buttons'], rim_color='gradient', hue_shift=15, contrast=1.3, gloss=0.5, grain=0.2, invert=True),
            dict(targets=['logo'], gradient_preset='rainbow', gloss=0.4, pixelate=4),
            dict(targets=['menu_background'], gradient_preset='rainbow', color_mode='fill', opacity=3),
            dict(targets=['everything'], brightness=0.8), dict(targets=['icons'], brightness=0.8)]))
        plan = theme.tame(messy, 'Theme it rainbow with proper GLOWS')
        rim, logo, menu = plan['operations'][:3]
        self.assertEqual((rim['glow_color'], rim['hue_shift'], rim['contrast'], rim['gloss'], rim['grain'], rim['invert']), ('auto', 0.0, 1.0, 0.0, 0.0, False))
        self.assertEqual((logo['glow_color'], logo['gloss'], logo['pixelate']), ('auto', 0.0, 0.0))
        self.assertEqual((menu['glow_color'], menu['opacity']), ('', 3.0))                    # the flat menu pattern does not glow
        self.assertEqual([o['targets'] for o in plan['operations'][3:]], [['everything']])    # the repeat on icons is dropped
        kept = theme.tame(messy, 'glossy retro pixel look, inverted and grainy')['operations']
        self.assertEqual((kept[1]['gloss'], kept[1]['pixelate'], kept[1]['glow_color']), (0.4, 4.0, ''))
        self.assertEqual((kept[0]['invert'], kept[0]['grain'], kept[0]['gloss']), (True, 0.2, 0.0))   # asked-for effects survive; a rim-only step still gets no gloss

    def test_each_sprite_gets_its_own_gradient_and_glow_stays_in_its_rectangle(self):
        sheet = np.zeros((16, 64, 4), np.uint8)
        sheet[2:14, 2:30] = (255, 255, 255, 255)      # sprite A occupies x 0..31
        sheet[2:14, 34:62] = (255, 255, 255, 255)     # sprite B occupies x 32..63
        rects = [[0, 0, 32, 16], [32, 0, 32, 16]]
        both = Themes.render(sheet, [(op(gradient=['#ff0000', '#0000ff']), None)], rects)
        self.assertGreater(both[8, 3, 0], 200); self.assertGreater(both[8, 28, 2], 200)      # A runs red to blue
        self.assertGreater(both[8, 35, 0], 200); self.assertGreater(both[8, 60, 2], 200)     # so does B
        only_a = Themes.render(sheet, [(op(glow_color='#00ff00', glow_size=12, glow_strength=3), [rects[0]])], rects)
        self.assertGreater(only_a[8, 1, 3], 0)                                               # glow inside A's rectangle
        self.assertGreater(only_a[8, 1, 3], only_a[8, 0, 3])                                 # and it fades out before the edge
        self.assertEqual(only_a[:, 32:].tolist(), sheet[:, 32:].tolist())                    # nothing leaks into B

    def test_keyword_planner_understands_exotic_requests(self):
        rainbow = rule_plan('theme it rainbow with proper glows')['operations']
        self.assertEqual(len(rainbow), 1)
        self.assertEqual((rainbow[0]['targets'], rainbow[0]['gradient'], rainbow[0]['glow_color'], rainbow[0]['color_mode']), (['everything'], theme.GRADIENTS['rainbow'], 'auto', 'none'))
        self.assertEqual((rainbow[0]['rim_color'], rainbow[0]['rim_width'], rainbow[0]['gradient_type']), ('gradient', 4.0, 'conic'))   # neon rims, art inside untouched
        self.assertEqual(rule_plan('rainbow buttons')['operations'][0]['rim_color'], '')
        fade = rule_plan('buttons with a vertical gradient from purple to cyan and a strong pink glow')['operations'][0]
        self.assertEqual((fade['targets'], fade['gradient'], fade['gradient_angle'], fade['glow_color'], fade['glow_size']), (['buttons'], ['#9b5de5', '#22d3ee'], 90.0, '#ff6fb5', 16.0))
        self.assertEqual(fade['color'], '')
        icons = rule_plan('icons with a black outline, glossy gold buttons, retro 8-bit food')['operations']
        self.assertEqual([(o['targets'], o['rim_color'], o['gloss'], o['pixelate'], o['color']) for o in icons],
                         [(['icons'], '#101216', 0.0, 0.0, ''), (['buttons'], '', 0.5, 0.0, '#f5c542'), (['food'], '', 0.0, 4.0, '')])
        glass = rule_plan('holographic glass panels')['operations'][0]
        self.assertEqual((glass['targets'], glass['rim_gradient'], glass['glow_inside'], glass['gloss'], glass['color_mode']), (['panels'], theme.GRADIENTS['holographic'], True, 0.3, 'none'))

    def test_tag_words_name_one_sprite_with_a_separate_outline_and_inside(self):
        lookup = lambda text: ({'virus': ['light_virus.png']}, text.replace('white virus', 'virus'))
        plan = rule_plan('On the white virus give me a rainbow outline with a subtle glow and the inside something on the dark side with flare', lookup)
        self.assertEqual(len(plan['operations']), 1)
        one = plan['operations'][0]
        self.assertEqual((one['targets'], one['match']), (['everything'], ['light_virus.png']))
        self.assertEqual((one['rim_color'], one['rim_gradient'], one['rim_width']), ('gradient', theme.GRADIENTS['rainbow'], 4.0))
        self.assertEqual((one['glow_color'], one['glow_size'], one['glow_strength']), ('auto', 7.0, 0.8))          # "subtle"
        self.assertEqual((one['gradient'], one['gradient_type'], one['color_mode'], one['brightness']), (theme.DARK_SIDE, 'radial', 'colorize', 1.0))
        self.assertEqual((one['flare'], one['color'], one['opacity']), (0.6, '', 1.0))
        # The result: bright rainbow edge, dark body, a highlight in the upper left, soft halo outside.
        ys, xs = np.mgrid[0:96, 0:96]
        virus = np.zeros((96, 96, 4), np.uint8); virus[np.hypot(xs - 47.5, ys - 47.5) <= 36] = (255, 255, 255, 255)
        out = shade(virus, one)
        edge, body, spot = out[48, 13], out[70, 60], out[30, 32]
        self.assertGreater(int(edge[:3].max()), 200); self.assertGreater(int(edge[:3].max()) - int(edge[:3].min()), 120)
        self.assertLess(int(body[:3].max()), 130)
        self.assertGreater(int(spot[:3].min()), int(body[:3].max()) + 60)
        self.assertGreater(out[48, 8, 3], 0); self.assertLess(out[48, 8, 3], 200)
        thick = rule_plan('virus with a thick cyan border and a strong glow', lookup)['operations'][0]
        self.assertEqual((thick['rim_color'], thick['rim_width'], thick['glow_size'], thick['color']), ('#22d3ee', 6.0, 16.0, ''))
        two = rule_plan('coin with a gold outline, virus inside purple', lambda t: ({'coin': ['coin.png'], 'virus': ['virus.png']}, t))['operations']
        self.assertEqual([(o['match'], o['rim_color'], o['color'], o['color_mode']) for o in two], [(['coin.png'], '#f5c542', '', 'none'), (['virus.png'], '', '#9b5de5', 'colorize')])
        self.assertEqual(rule_plan('gold gradient logo')['operations'][0]['gradient'], theme.GRADIENTS['gold'])
        self.assertEqual(rule_plan('striped radial fire cells')['operations'][0]['gradient_type'], 'radial')


class ThemeTests(Base):
    def sheet(self, name): return next(k for k, s in self.themes.sheets.items() if s['row']['name'] == name)

    def test_targets_touch_only_matching_sprites(self):
        plan = clean_plan(dict(operations=[dict(targets=['icons'], match=['icon-a'], color='#0000ff', color_mode='tint')]))
        preview = self.themes.preview(plan)
        self.assertEqual((preview['sheets'], preview['lines'][0]['regions']), (1, 1))
        self.assertEqual(preview['samples'][0]['name'], 'icon-a')
        self.assertEqual(self.run_plan(plan)['state'], 'complete')
        themed = self.lab.image(self.sheet('theme4.png'))
        self.assertGreater(themed.getpixel((5, 5))[2], 200)
        self.assertEqual(themed.getpixel((5, 85)), (40, 200, 90, 255))   # free-x was not targeted
        self.assertEqual(themed.getpixel((50, 10)), (255, 255, 255, 255))

    def test_follow_up_replaces_the_theme_and_undo_walks_back(self):
        key = self.sheet('theme4.png')
        blue = clean_plan(dict(operations=[dict(targets=['everything'], color='#0000ff', color_mode='tint')]))
        self.run_plan(blue, 'blue')
        first = self.lab.revision(key)
        darker = clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)]))
        self.run_plan(darker, 'darker')
        # Rebuilt from the original art, not stacked on the blue theme.
        self.assertEqual(self.lab.image(key).getpixel((5, 5)), (115, 30, 20, 255))
        self.assertEqual(self.themes.undo()['reverted'] > 0, True)
        self.assertEqual(self.lab.revision(key), first)
        self.themes.undo()
        self.assertEqual(self.lab.revision(key), 'original')
        self.assertEqual(self.lab.staged(), [])
        with self.assertRaisesRegex(ValueError, 'no theme'): self.themes.undo()

    def test_sheet_dropped_by_the_next_plan_returns_to_its_base(self):
        self.run_plan(clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)])))
        self.assertNotEqual(self.lab.revision(self.sheet('bg2.png')), 'original')
        self.run_plan(clean_plan(dict(operations=[dict(targets=['icons'], brightness=0.5)])))
        self.assertEqual(self.lab.revision(self.sheet('bg2.png')), 'original')
        self.assertNotEqual(self.lab.revision(self.sheet('theme4.png')), 'original')

    def test_hand_edit_sits_on_top_of_a_theme_and_installs(self):
        self.run_plan(clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)])))
        key = next(r['key'] for r in self.rows('icon-b') if r['primary'])
        im = self.lab.image(key)
        self.assertEqual(im.getpixel((5, 5)), (128, 128, 128, 255))   # the sprite shows its themed sheet
        im.paste((255, 0, 255, 255), (4, 4, 12, 12))
        self.lab.save(key, png(im), 'original')
        build = self.lab.build()
        self.lab.install(build['id'])
        loose = Image.open(self.game / 'assets' / 'theme4.png').convert('RGBA')
        self.assertEqual(loose.getpixel((46, 6)), (255, 0, 255, 255))
        self.assertEqual(loose.getpixel((5, 5)), (115, 30, 20, 255))
        with zipfile.ZipFile(self.game / 'assets' / 'assets.zip') as z: packed = Image.open(io.BytesIO(z.read('theme4.png'))).convert('RGBA')
        self.assertEqual(packed.getpixel((46, 6)), (255, 0, 255, 255))
        self.assertEqual(packed.getpixel((5, 5)), (115, 30, 20, 255))
        self.lab.restore()

    def test_a_new_theme_goes_on_while_one_is_installed(self):
        with self.assertRaisesRegex(ValueError, 'changes nothing'): self.themes.apply(dict(operations=[]))
        originals = {p: file_hash(p) for p in (self.game / 'assets').rglob('*') if p.is_file()}
        self.run_plan(clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)])))
        self.lab.install(self.lab.build()['id'])
        installed = {p: file_hash(p) for p in originals}
        # No restore first: the next theme is shaded from the original art, then swapped in as an update.
        self.run_plan(clean_plan(dict(operations=[dict(targets=['everything'], color='#ff0000', color_mode='tint')])))
        self.assertTrue(self.lab.pending())
        self.lab.install(self.lab.build()['id'])
        self.assertNotEqual({p: file_hash(p) for p in originals}, installed)
        self.themes.undo()                                   # back to the first theme, still installable as an update
        self.lab.install(self.lab.build()['id'])
        self.assertEqual({p: file_hash(p) for p in originals}, installed)
        self.lab.restore()
        self.assertEqual({p: file_hash(p) for p in originals}, originals)

    def test_a_request_can_name_one_sprite_and_describe_it_by_tone(self):
        found, text = self.themes.named('give the viruses a glow')
        self.assertEqual(found, {'viruses': ['virus-deep', 'virus-pale']})            # no adjective: both
        found, text = self.themes.named('on the white virus give me a red outline')
        self.assertEqual((found, text), ({'virus': ['virus-pale']}, 'on the virus give me a red outline'))
        self.assertEqual(self.themes.named('the dark virus')[0], {'virus': ['virus-deep']})
        self.assertEqual(self.themes.named('make the buttons and icons darker')[0], {})   # family words are not sprite names
        self.assertEqual(self.themes.hints('the white virus'), ['virus: virus-pale (light)'])
        with patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('on the white virus give me a rainbow outline with a subtle glow')
        only = result['plan']['operations'][0]
        self.assertEqual((only['match'], only['rim_gradient'], only['glow_strength']), (['virus-pale'], theme.GRADIENTS['rainbow'], 0.8))
        self.assertEqual((result['preview']['sheets'], result['preview']['lines'][0]['regions']), (1, 1))
        fake = [dict(name='m', thinking=False, size=5e9)]
        sent = {}
        def answer(path, payload=None, timeout=0):
            sent.update(payload)
            return {'message': {'content': '{"reply":"ok","operations":[{"targets":["cells"],"rim_color":"#ff0000"}]}'}}
        with patch.object(theme, 'models', return_value=fake), patch.object(theme, 'ollama', side_effect=answer):
            result = self.themes.chat('eerie red outline on the white virus')
        self.assertIn('Sprites named in this request: virus: virus-pale (light)', sent['messages'][-1]['content'])
        self.assertEqual((result['plan']['operations'][0]['targets'], result['plan']['operations'][0]['match']), (['everything'], ['virus-pale']))  # the model forgot to name it

    def test_requests_written_in_tag_words_are_planned_exactly_without_the_model(self):
        self.assertEqual(theme.unknown_words('On the white virus give me a rainbow outline with a subtle glow and the inside something on the dark side with flare', self.themes.named), [])
        self.assertEqual(theme.unknown_words('theme it rainbow with proper glows'), [])
        self.assertEqual(theme.unknown_words('cyberpunk neon theme with a hot pink logo'), ['hot'])   # cyberpunk is a palette
        self.assertEqual(theme.unknown_words('molten buttons, charcoal panels'), ['molten', 'charcoal'])
        fake = [dict(name='m', thinking=False, size=5e9)]
        def never(*args, **kwargs): raise AssertionError('the model must not be asked')
        with patch.object(theme, 'models', return_value=fake), patch.object(theme, 'ollama', side_effect=never):
            result = self.themes.chat('buttons with a thick cyan outline and a strong glow')
        only = result['plan']['operations'][0]
        self.assertEqual((result['source'], only['targets'], only['rim_color'], only['rim_width'], only['glow_size']), ('tags', ['buttons'], '#22d3ee', 6.0, 16.0))
        called = []
        def answer(path, payload=None, timeout=0):
            called.append(payload)
            return {'message': {'content': '{"reply":"ok","operations":[{"targets":["buttons"],"brightness":0.5}]}'}}
        with patch.object(theme, 'models', return_value=fake), patch.object(theme, 'ollama', side_effect=answer):
            self.themes.chat('make them darker', history=[dict(role='user', content='ocean buttons'), dict(role='assistant', content='{}')])   # a follow-up needs the conversation
            subtle = theme.tame(clean_plan(dict(operations=[dict(targets=['icons'], rim_color='#ff0000', glow_color='auto', glow_size=14, glow_strength=2)])), 'red outline with a subtle glow')
        self.assertEqual(len(called), 1)
        self.assertEqual((subtle['operations'][0]['glow_size'], subtle['operations'][0]['glow_strength']), (8.0, 0.9))

    def test_chat_uses_keywords_without_ollama_and_survives_bad_model_output(self):
        with patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('make the buttons teal like a peacock')
        self.assertEqual((result['source'], result['plan']['operations'][0]['targets']), ('rules', ['buttons']))
        self.assertIn('Ollama is not running', result['note'])
        fake = [dict(name='m', thinking=True, size=5e9)]
        with patch.object(theme, 'models', return_value=fake), patch.object(theme, 'ollama', return_value={'message': {'content': 'sorry, no JSON'}}):
            result = self.themes.chat('make the buttons teal like a peacock')
        self.assertEqual(result['source'], 'rules')
        self.assertIn('did not return a usable plan', result['note'])
        sent = {}
        def answer(path, payload=None, timeout=0):
            sent.update(payload)
            return {'message': {'content': '{"reply":"Teal.","operations":[{"targets":["buttons"],"color":"#008080","color_mode":"tint"}]}'}}
        with patch.object(theme, 'models', return_value=fake), patch.object(theme, 'ollama', side_effect=answer):
            result = self.themes.chat('make the buttons teal like a peacock', history=[dict(role='user', content='earlier'), dict(role='tool', content='x')])
        self.assertEqual((result['source'], result['plan']['reply'], sent['think']), ('m', 'Teal.', False))
        self.assertEqual([m['role'] for m in sent['messages']], ['system', 'user', 'user'])
        self.assertEqual(result['preview']['lines'][0]['regions'], 2)
        with self.assertRaises(ValueError): self.themes.chat('   ')


class RecoveryTests(Base):
    maker = staticmethod(make_game)

    def test_abandon_closes_an_install_that_cannot_be_restored(self):
        key = next(r['key'] for r in self.rows('bg2.png'))
        im = self.lab.image(key); im.paste((1, 2, 3, 255), (0, 0, 8, 8))
        self.lab.save(key, png(im), 'original')
        k2 = next(r['key'] for r in self.rows('button-up-skin'))
        im = self.lab.image(k2); im.paste((9, 9, 9, 255), (0, 0, 4, 4))
        self.lab.save(k2, png(im), 'original')
        originals = {n: (self.game / 'assets' / n).read_bytes() for n in ('bg2.png', 'metalworks_mobile.png')}
        self.lab.install(self.lab.build()['id'])
        self.assertEqual(self.lab.drift(), [])
        with self.assertRaisesRegex(ValueError, 'normal restore'): self.lab.abandon()
        update = png(Image.new('RGBA', (64, 64), '#123456'))
        (self.game / 'assets' / 'bg2.png').write_bytes(update)             # Steam replaced one file
        self.assertEqual(self.lab.drift(), ['assets/bg2.png'])
        self.assertEqual(self.lab.status()['stuck'], ['assets/bg2.png'])
        with self.assertRaises(ValueError): self.lab.restore()
        journal = self.lab.abandon()
        self.assertEqual((journal['state'], journal['abandoned']), ('restored', ['assets/bg2.png']))
        self.assertEqual((self.game / 'assets' / 'bg2.png').read_bytes(), update)                      # left alone
        self.assertEqual((self.game / 'assets' / 'metalworks_mobile.png').read_bytes(), originals['metalworks_mobile.png'])
        self.assertIsNone(self.lab.active())
        self.assertEqual(self.lab.state['baseline']['assets/bg2.png']['sha256'], file_hash(self.game / 'assets' / 'bg2.png'))
        self.lab.build()                                                                               # work can continue


if __name__ == '__main__': unittest.main()
