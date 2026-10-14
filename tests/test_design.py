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

    def test_fill_sizes_by_the_dense_core_and_ignores_sparse_extras(self):
        art = Image.new('RGBA', (400, 200), (0, 0, 0, 0))
        art.paste((240, 240, 240, 255), (40, 80, 360, 140))           # the letters: a wide dense band
        art.paste((20, 20, 20, 255), (190, 10, 210, 20))              # a small bat far above
        plain, filled = fit(art, (200, 40)), fit(art, (200, 40), fill=True)
        width = lambda im: (lambda b: b[2] - b[0])(im.getchannel('A').point(lambda v: 255 if v > 10 else 0).getbbox())
        self.assertLess(width(plain), 150)                               # the bat makes the plain fit shrink the letters
        self.assertGreaterEqual(width(filled), 195)                       # filled: the letters span the frame
        self.assertEqual(design.core_box(art)[1], 80)                     # the bat's rows are not part of the core

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
        # Redraw verbs and new wording are drawing even when every word is known.
        self.assertTrue(wants_design('turn the coin into a diamond', []))
        self.assertTrue(wants_design('make the title say MITOSIS 2', []))
        self.assertFalse(wants_design('make the buttons look like glass', []))           # glass is a shader look
        self.assertEqual(design.subject_part('turn the coin into a diamond').strip(), 'turn the coin')
        self.assertEqual(design.wording('make the title say MITOSIS 2'), 'MITOSIS 2')
        self.assertEqual(design.wording('change the title text to "Cell Wars" with a glow'), 'Cell Wars')
        self.assertEqual(design.wording('make the title gold'), '')


class ShapeAndQualityTests(Base):
    def test_keep_shape_trims_to_the_original_outline(self):
        original = Image.new('RGBA', (40, 40), (0, 0, 0, 0)); original.paste((255, 255, 255, 255), (10, 10, 30, 30))
        drawn_art = Image.new('RGBA', (40, 40), (200, 30, 30, 255))          # the model filled the whole frame
        out = np.asarray(design.keep_shape(drawn_art, original))
        self.assertEqual(out[20, 20, 3], 255)                                 # inside the outline: kept
        self.assertEqual(out[2, 2, 3], 0)                                     # far outside: trimmed
        self.assertEqual(tuple(out[20, 20, :3]), (200, 30, 30))               # colors untouched

    def test_a_background_that_did_not_cut_out_is_flagged(self):
        clean = unkey(drawn(), 'green')
        self.assertEqual(design.leftover(clean), '')
        busy = Image.new('RGB', (200, 100), (90, 60, 40))                      # the model painted scenery instead of green
        self.assertIn('did not cut out', design.leftover(unkey(busy, 'green')))


class ChatRoutingTests(Base):
    def setUp(self):
        super().setUp()
        self.designer = Designer(self.lab, self.themes)
        self.themes.designer = self.designer

    def test_help_and_undo_are_chat_commands(self):
        self.assertIn('image model', self.themes.chat('what can you do?')['plan']['reply'])
        with self.assertRaisesRegex(ValueError, 'no theme to undo'): self.themes.chat('undo')
        self.themes.apply(theme.clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)])), 'dim')
        for _ in range(400):
            if all(j['state'] != 'running' for j in self.themes.jobs.values()): break
            import time; time.sleep(0.02)
        result = self.themes.chat('undo that')
        self.assertTrue(result['undone']); self.assertIn('Undid', result['plan']['reply'])

    def test_one_request_can_shade_and_draw(self):
        with patch.object(design.runtime, 'missing', return_value=[]), patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('theme it christmas and put a santa hat on the virus-pale')
        self.assertTrue(result['plan']['operations'])                                         # the palette is shaded
        self.assertEqual(result['plan']['operations'][0]['gradient'], theme.GRADIENTS['christmas'])
        self.assertIn('virus-pale', [t['name'] for t in result['design']['targets']])          # the hat is drawn
        self.assertIn('santa hat', result['design']['prompt'])
        self.assertNotIn('christmas', result['design']['prompt'])

    def test_broken_model_json_gets_one_repair_round(self):
        answers = iter(['{"reply":"ok","operations":[{"targets":["cells"],"color":"#ff0000"', '{"reply":"fixed","operations":[{"targets":["cells"],"color":"#ff0000"}]}'])
        calls = []
        def fake(path, payload=None, timeout=0):
            calls.append(payload['messages'][-1]['content']); return {'message': {'content': next(answers)}}
        with patch.object(theme, 'models', return_value=[dict(name='m', thinking=False, size=5e9)]), patch.object(theme, 'ollama', side_effect=fake):
            result = self.themes.chat('molten crimson cells')
        self.assertEqual((result['source'], result['plan']['reply'], len(calls)), ('m', 'fixed', 2))
        self.assertIn('not valid JSON', calls[1])

    def test_a_model_step_that_forgets_the_named_thing_is_kept_to_it(self):
        self.lab.labels.name([next(r['key'] for r in self.rows('virus-pale') if r.get('primary', True))], 'Friends button icon (Main menu)')
        self.themes.reindex()
        answer = '{"reply":"same color","operations":[{"targets":["everything"],"color":"#8b8995"},{"targets":["icons"],"match":["virus-pale"],"color":"#8b8995"}]}'
        with patch.object(theme, 'models', return_value=[dict(name='m', thinking=False, size=5e9)]), \
                patch.object(theme, 'ollama', return_value={'message': {'content': answer}}):
            result = self.themes.chat('make the friends button icon the same shade as its neighbour')
        self.assertEqual([op['match'] for op in result['plan']['operations']], [['virus-pale'], ['virus-pale']])   # nothing repaints everything

    def test_names_are_listed_and_a_plan_that_matches_nothing_says_so(self):
        self.lab.labels.name([next(r['key'] for r in self.rows('virus-pale') if r.get('primary', True))], 'Play button (Main menu)')
        self.themes.reindex()
        listing = self.themes.chat('what can I change?')
        self.assertEqual(listing['source'], 'command')
        self.assertIn('Main menu: play button.', listing['plan']['reply'])
        answer = '{"reply":"The title now reads MITOSIS 2.","operations":[{"targets":["everything"],"match":["title*"],"color":"#ff0000"}]}'
        with patch.object(theme, 'models', return_value=[dict(name='m', thinking=False, size=5e9)]), \
                patch.object(theme, 'ollama', return_value={'message': {'content': answer}}):
            result = self.themes.chat('make the wordmark thingy shimmer')
        self.assertIn('matched no art', result['plan']['reply'])

    def test_the_menu_background_phrase_beats_sprite_names(self):
        self.lab.labels.name([next(r['key'] for r in self.rows('free-x') if r.get('primary', True))], 'Background panel')
        self.themes.reindex()
        op = theme.rule_plan('change the menu background to a galaxy', self.themes.named)['operations'][0]
        self.assertEqual((op['targets'], op['match']), (['menu_background'], []))

    def test_recoloring_an_icon_the_game_colors_says_what_happens(self):
        self.lab.labels.name([next(r['key'] for r in self.rows('virus-pale') if r.get('primary', True))], 'Ranks button icon (Main menu)')
        self.themes.reindex()
        with patch.object(design, 'tints', return_value={'virus-pale': '#40BDC4'}), patch.object(theme, 'models', return_value=[]):
            gold, darker = self.themes.chat('make the ranks button icon gold'), self.themes.chat('make the ranks button icon darker')
        self.assertIn('The game colors Ranks button icon with #40BDC4', gold['note'])
        self.assertNotIn('The game colors', darker['note'])                                            # shades are fine
        sample = next(s for s in gold['preview']['samples'] if s['name'] == 'virus-pale')
        self.assertEqual((sample['tint'], sample['label']), ('#40BDC4', 'Ranks button icon'))        # shown as the game draws it

    def test_a_group_can_be_redrawn_one_sprite_at_a_time(self):
        rows = self.designer.family_rows('put a tiny santa hat on every icon')
        self.assertTrue(rows)
        self.assertTrue(all(theme.families_of(r, self.lab.catalog.rows.get(r.get('parent', ''), {}).get('name', '')) == 'icons' for r in rows))
        self.assertEqual(len({r.get('dup') for r in rows}), len(rows))                         # one per distinct art
        self.lab.pool.remove(rows[0]['key'])
        self.assertNotIn(rows[0]['key'], [r['key'] for r in self.designer.family_rows('every icon')])   # removed art is skipped
        with patch.object(design.runtime, 'missing', return_value=[]), patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('put a santa hat on the icons')
        self.assertEqual(result['source'], 'design')

    def test_complete_requests_skip_the_model_even_mid_conversation(self):
        history = [dict(role='user', content='ocean theme'), dict(role='assistant', content='{}')]
        called = []
        with patch.object(theme, 'models', return_value=[dict(name='m', thinking=False, size=5e9)]), \
             patch.object(theme, 'ollama', side_effect=lambda *a, **k: called.append(1) or {'message': {'content': '{"reply":"","operations":[]}'}}):
            self.assertEqual(self.themes.chat('christmas theme', history=history)['source'], 'tags')
            self.assertEqual(called, [])
            self.themes.chat('make it darker', history=history)                     # leans on the last plan: the model is asked
        self.assertEqual(called, [1])

    def test_moods_have_palettes(self):
        for words, preset in (('make everything spooky', 'halloween'), ('frozen panels', 'winter'), ('royal theme', 'royal')):
            op = theme.rule_plan(words)['operations'][0]
            self.assertEqual(op['gradient'], theme.GRADIENTS[preset], words)
        self.assertEqual(theme.unknown_words('make everything spooky'), [])

    def test_the_art_direction_leads_the_instruction(self):
        job = dict(title=False, names=['icon-a'], frames=[[64, 64]], key='green', prompt='spooky', direction='Cracked bone, dripping red ink.')
        text = self.designer.instruction(job)
        self.assertTrue(text.startswith('Cracked bone'))
        self.assertIn('bold, simple shapes', text)                                            # small sprites read at a glance

    def test_new_title_wording_and_clean_subjects(self):
        title = dict(title=True, names=['logo'], frames=[[500, 110]], key='green', prompt='make the title say MITOSIS 2', text='MITOSIS 2')
        text = self.designer.instruction(title)
        self.assertIn('reads exactly "MITOSIS 2"', text)
        self.assertNotIn('Keep the exact word MITOSIS', text)
        self.assertEqual(Designer.subject(dict(title=False, names=['Play button (Main menu)'])), 'a game sprite: Play button')
        self.assertEqual(Designer.subject(dict(title=False, names=['icon-crown'])), 'a game sprite: icon crown')
        self.assertEqual(design.tints().get('icon-crown'), '#40BDC4')                         # measured: the menu's teal

    def test_turn_one_thing_into_another_redraws_only_the_first(self):
        self.lab.labels.name([next(r['key'] for r in self.rows('virus-pale') if r.get('primary', True))], 'Speed potion')
        self.themes.reindex()
        self.assertEqual([r['name'] for r in self.designer.targets('turn the speed potion into a free-x')], ['virus-pale'])


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

    def test_keep_it_white_turns_a_drawing_into_shades_for_tinted_icons(self):
        job = self.fake_job()
        colorful = np.asarray(self.designer.render(job['id'], 0, 0, {})).astype(int)
        white = np.asarray(self.designer.render(job['id'], 0, 0, dict(white=1))).astype(int)
        solid = white[..., 3] > 128
        self.assertTrue(solid.any())
        self.assertTrue((white[..., 0][solid] == white[..., 1][solid]).all() and (white[..., 1][solid] == white[..., 2][solid]).all())   # gray
        self.assertEqual(int(white[..., 0][solid].max()), 255)                                        # the brightest part is white
        self.assertTrue((white[..., 3] == colorful[..., 3]).all())                                    # same shape

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
