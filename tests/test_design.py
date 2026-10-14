import json
from unittest.mock import patch
import numpy as np
from PIL import Image
import design
from design import Designer, unkey, fit, canvas_size, wants_design
import theme
from test_theme import Base


def drawn(key=(0, 255, 0)):
    """What the model returns: flat key background, a solid red disc, and a soft red glow blended over the key."""
    im = Image.new('RGB', (200, 100), key)
    px = im.load()
    for x in range(200):
        for y in range(100):
            d = ((x - 100) ** 2 + (y - 50) ** 2) ** 0.5
            if d < 20: px[x, y] = (220, 20, 30)
            elif d < 30:
                a = (30 - d) / 10   # glow: red over the key color
                px[x, y] = tuple(round(220 * a + k * (1 - a)) if i == 0 else round(20 * a + k * (1 - a)) for i, k in enumerate(key))
    return im


class CutOutTests(Base):
    def test_background_goes_and_glow_keeps_its_color(self):
        art = np.asarray(unkey(drawn(), 'green'))
        self.assertEqual(art[5, 5, 3], 0)                                 # background removed
        self.assertEqual(art[50, 100, 3], 255)                            # solid art kept
        faint = art[50, 128]                                              # the outer, mostly-key end of the glow
        self.assertTrue(0 < faint[3] < 255)
        self.assertGreater(faint[0], faint[1])                            # unmixed: red, not a green fringe
        glow = art[50, 125]
        self.assertLessEqual(glow[1], glow[0])                            # no green cast left anywhere
        self.assertEqual(tuple(art[50, 100][:3]), (220, 20, 30))          # solid art is untouched

    def test_fit_crops_to_the_art_and_keeps_proportions(self):
        art = unkey(drawn(), 'green')
        out = fit(art, (120, 30))
        self.assertEqual(out.size, (120, 30))
        box = out.getchannel('A').point(lambda v: 255 if v > 10 else 0).getbbox()
        self.assertLessEqual(abs((box[2] - box[0]) - (box[3] - box[1])), 2)   # the disc stays round
        self.assertEqual(fit(Image.new('RGBA', (10, 10)), (8, 8)).getbbox(), None)
        self.assertEqual(canvas_size(500, 110)[0] % 16, 0)

    def test_which_requests_are_drawn_rather_than_shaded(self):
        for text in ('make the title spooky', 'put a top hat on the logo', 'give the logo dripping blood and bats'):
            self.assertTrue(wants_design(text, ['x']), text)
        self.assertTrue(wants_design('add a sombrero to the logo', ['sombrero']))      # a verb with something unknown
        for text in ('theme it rainbow with proper glows', 'fire gradient buttons with a strong orange glow'):
            self.assertFalse(wants_design(text, []), text)
        self.assertFalse(wants_design('add a rainbow outline to the logo', []))        # outlines are the shader's job
        self.assertEqual(design.key_for('toxic slime logo'), 'magenta')


class DesignFlowTests(Base):
    def setUp(self):
        super().setUp()
        self.designer = Designer(self.lab, self.themes)
        self.themes.designer = self.designer

    def fake_job(self, names=('virus-pale',)):
        rows = [next(r for r in self.rows(n) if r.get('primary', True)) for n in names]
        job = dict(id='a' * 32, state='complete', prompt='spooky', targets=[r['key'] for r in rows], names=list(names),
                   frames=[[r['width'], r['height']] for r in rows], variants=[dict(index=0)], key='green', base=None, title=False)
        folder = self.designer.folder / job['id']; folder.mkdir(parents=True)
        drawn().save(folder / 'raw-0.png'); unkey(drawn(), 'green').save(folder / 'variant-0.png')
        (folder / 'job.json').write_text(json.dumps(job))
        return job

    def test_apply_saves_each_target_and_undo_takes_it_back(self):
        job = self.fake_job()
        key = job['targets'][0]
        self.assertEqual(self.designer.render(job['id'], 0, 0, dict(scale=0.5)).size, tuple(job['frames'][0]))
        result = self.designer.apply(job['id'], 0, dict(scale=1.2))
        self.assertEqual(self.lab.current(key)['recipe']['design'], job['id'])
        self.assertIn(key, [r['key'] for r in self.lab.staged()])
        self.assertEqual(result['items'][0]['before'], 'original')
        self.designer.undo(job['id'])
        self.assertEqual(self.lab.revision(key), 'original')
        with self.assertRaises(ValueError): self.designer.undo(job['id'])

    def test_chat_hands_drawing_requests_to_the_designer(self):
        with patch.object(design.runtime, 'missing', return_value=[]), patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('give the virus-pale a spooky witch hat')
            self.assertEqual(result['source'], 'design')
            self.assertIn('virus-pale', [t['name'] for t in result['design']['targets']])
            self.assertTrue(result['design']['available'])
            plain = self.themes.chat('virus-pale red')                     # a recolor stays a theme
            self.assertNotIn('design', plain)
        with patch.object(design.runtime, 'missing', return_value=['models/x.gguf']):
            with self.assertRaisesRegex(ValueError, 'not installed'): self.designer.start('spooky', [self.rows('virus-pale')[0]['key']])
