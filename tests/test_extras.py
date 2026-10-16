import base64
import io
import json
import time
import unittest
from unittest.mock import patch
from PIL import Image
import numpy as np
import lab_core
from lab_core import Lab, png, file_hash
import theme
from theme import clean_plan, rule_plan
from test_theme import Base


def image_bytes(color, size=(24, 24), inner=(4, 4, 20, 20)):
    im = Image.new('RGBA', size, (0, 0, 0, 0)); im.paste(color, inner)
    out = io.BytesIO(); im.save(out, format='PNG'); return out.getvalue()


class NameTests(Base):
    def key(self, name): return next(r['key'] for r in self.rows(name) if r.get('primary', True))

    def test_the_shipped_ui_map_is_added_once_and_user_changes_stick(self):
        ident = lambda name: self.lab.pool.ident(next(r for r in self.rows(name) if r.get('primary', True)))
        defaults = self.root / 'ui-defaults.json'
        defaults.write_text(json.dumps(dict(labels=[dict(id=ident('icon-a'), label='Ranks button icon (Main menu)'), dict(id='sprite|nope.png|missing', label='Gone')],
                                            removed=[dict(id=ident('free-x'), why='never shown')])))
        with patch.object(lab_core, 'DEFAULTS', defaults):
            lab = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
            self.assertEqual(next(r for r in lab.catalog.rows.values() if r['name'] == 'icon-a')['label'], 'Ranks button icon (Main menu)')
            self.assertTrue(all(r['removed'] for r in lab.catalog.rows.values() if r['name'] == 'free-x'))
            applied = json.loads((self.root / 'defaults-applied.json').read_text())['applied']
            self.assertNotIn('label:sprite|nope.png|missing', applied)                               # only sprites this game has
            # The player renames one and restores the other; the next start leaves both alone.
            key = next(r['key'] for r in lab.catalog.rows.values() if r['name'] == 'free-x' and r.get('primary', True))
            lab.pool.restore(key)
            lab.labels.name([next(r['key'] for r in lab.catalog.rows.values() if r['name'] == 'icon-a' and r.get('primary', True))], 'My crown')
            again = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.assertEqual(next(r for r in again.catalog.rows.values() if r['name'] == 'icon-a')['label'], 'My crown')
        self.assertFalse(any(r['removed'] for r in again.catalog.rows.values() if r['name'] == 'free-x'))

    def test_names_are_searchable_shared_by_copies_and_kept(self):
        result = self.lab.labels.name([self.key('virus-pale'), self.key('virus-deep')], '  Speed   potion ')
        self.assertEqual((result['label'], result['count']), ('Speed potion', 2))
        self.assertEqual({r['name'] for r in self.lab.search('speed', tidy=True)['items']}, {'virus-pale', 'virus-deep'})
        self.assertTrue(all(r['label'] == 'Speed potion' for r in self.rows('virus-pale')))   # both copies
        again = Lab(self.game, self.root / 'workspace-2', self.root / 'ledgers', process_check=lambda: [])
        self.assertEqual(again.search('potion', tidy=True)['total'], 2)
        self.lab.labels.name([self.key('virus-deep')], '')
        self.assertEqual({r['name'] for r in self.lab.search('speed', tidy=True)['items']}, {'virus-pale'})
        saved = {e['name']: e['label'] for e in json.loads((self.root / 'asset-names.json').read_text())['label']}
        self.assertEqual(saved.get('virus-pale'), 'Speed potion'); self.assertNotIn('virus-deep', saved)

    def test_a_name_with_a_screen_in_brackets_is_found_without_it(self):
        self.lab.labels.name([self.key('icon-a')], 'Square flag icon (Ranks)')
        self.themes.reindex()
        self.assertEqual(self.themes.named('make the square flag icon gold')[0], {'square flag icon': ['icon-a']})
        self.assertEqual(self.themes.named('the square flag icon (ranks) in gold')[0], {'square flag icon (ranks)': ['icon-a']})

    def test_the_screen_in_brackets_is_not_a_search_word(self):
        self.lab.labels.name([self.key('icon-a')], 'Ranks button icon (Main menu)')
        self.lab.labels.name([self.key('icon-b')], 'Server region flag (Ranks)')
        self.themes.reindex()
        self.assertEqual(rule_plan('make the ranks icon gold', self.themes.named)['operations'][0]['match'], ['icon-a'])

    def test_common_words_in_names_do_not_pick_a_sprite_but_given_names_win(self):
        self.lab.labels.name([self.key('icon-a')], 'New Players mode icon (Main menu)')
        self.lab.labels.name([self.key('virus-pale')], 'Virus button (Main menu)')
        self.themes.reindex()
        dark = rule_plan('make everything dark mode', self.themes.named)['operations'][0]
        self.assertEqual((dark['targets'], dark['match'], dark['brightness'] < 1), (['everything'], [], True))
        self.assertEqual(rule_plan('make the virus gold', self.themes.named)['operations'][0]['match'], ['virus-pale'])   # not virus-deep

    def test_size_and_motion_alone_are_answered_not_planned(self):
        self.lab.labels.name([self.key('icon-a')], 'Quests icon (Main menu)')
        self.themes.reindex()
        with patch.object(theme, 'models', return_value=[]):
            bigger, flash = self.themes.chat('make the buttons bigger'), self.themes.chat('make the quests icon flash')
            mixed = self.themes.chat('make the buttons red and bigger')
        self.assertEqual((bigger['plan']['operations'], flash['plan']['operations']), ([], []))
        self.assertIn('Sizes and positions', bigger['plan']['reply'])
        self.assertIn('still pictures', flash['plan']['reply'])
        self.assertTrue(mixed['plan']['operations'])
        self.assertIn('Sizes and positions', mixed['note'])

    def test_one_store_badge_can_be_hidden_alone(self):
        plan = clean_plan(dict(operations=[], hide=['mobile_store_badges']))
        apple, note = theme.art_hide(plan, {'icon-iphone', 'icon-android'}, 'remove the app store badge')
        self.assertEqual(apple['operations'][0]['match'], ['icon-iphone'])
        self.assertIn('The App Store badge will be hidden', note)
        both, _ = theme.art_hide(plan, {'icon-iphone', 'icon-android'}, 'hide the store badges')
        self.assertEqual(both['operations'][0]['match'], ['icon-iphone', 'icon-android'])

    def test_theme_ai_understands_a_given_name_as_one_phrase(self):
        self.lab.labels.name([self.key('virus-pale')], 'Speed potion')
        self.themes.reindex()
        found, _ = self.themes.named('give the speed potion a rainbow outline')
        self.assertEqual(found, {'speed potion': ['virus-pale']})
        self.assertIn('"speed potion" (your name for these sprites): virus-pale', self.themes.hints('the speed potion'))
        with patch.object(theme, 'models', return_value=[]):
            plan = self.themes.chat('give the speed potion a rainbow outline with a strong glow')['plan']
        self.assertEqual((plan['operations'][0]['match'], plan['operations'][0]['glow_size']), (['virus-pale'], 16.0))


class SimilarTests(Base):
    def key(self, name): return next(r['key'] for r in self.rows(name) if r.get('primary', True))

    def test_an_asset_or_a_dropped_image_finds_lookalikes_first(self):
        found = self.lab.search(kind='visual', tidy=True, like='key:' + self.key('icon-a'))
        self.assertEqual(found['items'][0]['name'], 'icon-a')
        self.assertGreater(found['items'][0]['similarity'], 0.99)
        # A red square, smaller and with different padding, is still most like icon-a (a red square).
        token = self.lab.similar.upload(base64.b64encode(image_bytes((235, 50, 35, 255), (40, 40), (8, 8, 30, 30))).decode())
        names = [r['name'] for r in self.lab.search(kind='sprite', tidy=True, like='upload:' + token)['items']]
        self.assertEqual(names[0], 'icon-a')
        # A pale square prefers the pale sprites over the red one.
        pale = self.lab.similar.upload('data:image/png;base64,' + base64.b64encode(image_bytes((252, 252, 252, 255))).decode())
        names = [r['name'] for r in self.lab.search(kind='sprite', tidy=True, like='upload:' + pale)['items']]
        self.assertLess(names.index('virus-pale'), names.index('icon-a'))
        with self.assertRaises(ValueError): self.lab.similar.upload(base64.b64encode(b'not an image').decode())
        with self.assertRaises(ValueError): self.lab.similar.upload(base64.b64encode(image_bytes((0, 0, 0, 0))).decode())
        with self.assertRaises(ValueError): self.lab.search(like='upload:missing')


class ExeBlockedTests(Base):
    def test_exe_edits_are_refused_and_explained_while_the_game_rejects_them(self):
        self.assertTrue(lab_core.exe_palette.BLOCKED)
        with self.assertRaisesRegex(ValueError, 'checks its own'): self.lab.set_palette({'BG_COLOR': '#0b1d3a'})
        with self.assertRaisesRegex(ValueError, 'checks its own'): self.lab.set_hidden(['discord'])
        self.assertEqual((self.lab.set_palette({}), self.lab.set_hidden([])), ({}, []))
        self.assertTrue(self.lab.status()['game_colors']['blocked'])
        # The planner turns "menu background navy" into the pattern art, and the chat explains the rest.
        self.assertEqual(rule_plan('menu background navy')['operations'][0]['targets'], ['menu_background'])
        with patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('hide the discord button')
            colors = self.themes.chat('make the header bars purple')
        self.assertEqual((result['plan']['hide'], result['plan']['operations']), ([], []))
        self.assertIn('not drawn from art', result['plan']['reply'])          # this fixture has no Discord art
        # A game color is explained, not painted over all the art (the game multiplies art by its own colors).
        self.assertEqual((colors['plan']['operations'], colors['plan']['ui_colors']), ([], {}))
        self.assertIn('set inside Mitosis.exe', colors['plan']['reply'])
        # A workspace saved before the switch-off loses its exe edits on the next start.
        self.lab.state.update(palette={'BG_COLOR': '#0B1D3A'}, hide=['discord']); self.lab.persist()
        again = Lab(self.game, self.root / 'workspace', self.root / 'ledgers', process_check=lambda: [])
        self.assertEqual((again.palette(), again.hidden()), ({}, []))


    def test_menu_controls_drawn_from_art_are_hidden_through_the_art(self):
        sheet = next(k for k, s in self.themes.sheets.items() if s['row']['name'] == 'theme4.png')
        self.assertEqual(self.lab.image(sheet).getpixel((50, 10))[3], 255)          # inside icon-b
        with patch.dict(lab_core.exe_palette.HIDE_SPRITES, {'discord': ['icon-b']}), patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('hide the discord button and the worm.is promo')
            op = result['plan']['operations'][-1]
            self.assertEqual((op['targets'], op['match'], op['opacity'], result['plan']['hide']), (['everything'], ['icon-b'], 0.0, []))
            self.assertIn('Discord button will be hidden', result['note'])
            self.assertIn('Worm.is and Strike.is promos are not drawn from art', result['note'])
            self.assertEqual(result['preview']['lines'][-1]['effect'], 'hidden (made fully transparent)')
            job = PaletteTests.wait(self, self.themes.apply(result['plan'], 'hide discord'))
        self.assertEqual(job['state'], 'complete')
        hidden = self.lab.image(sheet)
        self.assertEqual(hidden.getpixel((50, 10))[3], 0)
        self.assertEqual(hidden.getpixel((5, 5))[3], 255)                            # icon-a untouched
        self.assertEqual(self.lab.hidden(), [])                                      # Mitosis.exe is left alone


class PaletteTests(Base):
    """The exe patching itself, with the game's self-check block lifted (as if the owner allowed it)."""
    def setUp(self):
        super().setUp()
        blocked = patch.object(lab_core.exe_palette, 'BLOCKED', '')
        blocked.start(); self.addCleanup(blocked.stop)

    def fake_patch(self, data, overrides, hide=()):
        if data != (self.game / 'Mitosis.exe').read_bytes() and not data.startswith(b'QA fixture'): raise ValueError('not the original')
        return data[:-4] + b'EDIT', [dict(field=k, color=v, phase='test') for k, v in overrides.items()] + [dict(field='HIDE', phase=h, hide=h, color='') for h in hide]

    def test_hidden_menu_controls_alone_make_a_mod(self):
        original = (self.game / 'Mitosis.exe').read_bytes()
        with self.assertRaises(ValueError): self.lab.set_hidden(['ads'])
        self.assertEqual(self.lab.set_hidden(['discord', 'discord', 'top_banner']), ['discord', 'top_banner'])
        status = self.lab.status()
        self.assertEqual((status['hidden'], len(status['game_colors']['colors']), len(status['game_colors']['hide'])), (['discord', 'top_banner'], 20, 5))
        with patch.object(lab_core.exe_palette, 'patch', side_effect=self.fake_patch) as patched:
            build = self.lab.build()
        self.assertEqual(patched.call_args.args[2], ['discord', 'top_banner'])
        self.assertEqual(build['hide'], ['discord', 'top_banner'])
        self.assertEqual(next(f for f in build['files'] if f['path'] == 'Mitosis.exe')['entries'], ['Hide: discord', 'Hide: top_banner'])
        self.lab.install(build['id']); self.lab.restore()
        self.assertEqual((self.game / 'Mitosis.exe').read_bytes(), original)
        self.assertEqual(self.lab.set_hidden([]), [])

    def test_theme_ai_knows_every_game_color_and_the_menu_promos(self):
        self.assertEqual(rule_plan('gold menu buttons, purple header bars, and hide the discord button')['ui_colors'], {'menu_buttons': '#f5c542', 'header': '#9b5de5'})
        self.assertEqual(rule_plan('light mode background black')['ui_colors'], {'light_background': '#101216'})
        self.assertEqual(rule_plan('chat background navy and dividers red')['ui_colors'], {'chat_background': '#1e3a8a', 'lines': '#e53935'})
        self.assertEqual(rule_plan('menu text white')['ui_colors'], {'text': '#ffffff'})
        self.assertEqual(rule_plan('expanded circle buttons cyan')['ui_colors'], {'expanded': '#22d3ee'})
        self.assertEqual(rule_plan('the buttons red')['ui_colors'], {})                                   # button art, not code
        self.assertEqual(rule_plan('hide the worm.is and strike.is banners')['hide'], ['left_banner_1', 'left_banner_2'])
        self.assertEqual(rule_plan('hide discord and the app store badges')['hide'], ['discord', 'mobile_store_badges'])
        self.assertEqual(len(rule_plan('no ads')['hide']), 5)
        plan = rule_plan('hide the discord button and make the buttons red')
        self.assertEqual((plan['hide'], plan['operations'][0]['targets']), (['discord'], ['buttons']))
        self.assertEqual(rule_plan('make the buttons darker and hide the menu pattern')['hide'], [])          # the pattern is art
        self.assertEqual(theme.unknown_words('hide the discord button and remove the og banner'), [])
        model = clean_plan(dict(operations=[], ui_colors={'header': '#ff0000', 'guild': '#00ff00'}, hide=['discord', 'bogus']))
        self.assertEqual((model['hide'], theme.tame(model, 'ocean theme')['hide'], theme.tame(model, 'ocean theme')['ui_colors']), (['discord'], [], {}))
        self.assertEqual(theme.tame(model, 'red header bars and no discord button')['ui_colors'], {'header': '#ff0000'})
        self.assertEqual(theme.tame(model, 'hide the discord button')['hide'], ['discord'])
        self.assertEqual(set(theme.SCHEMA['properties']['ui_colors']['properties']), set(theme.UI_COLORS))
        self.assertEqual({field for field, _, _ in theme.UI_COLORS.values()}, set(lab_core.exe_palette.SITES))   # every verified field is reachable
        preview = self.themes.preview(clean_plan(dict(operations=[], hide=['discord'])))
        self.assertEqual(preview['hide'], [dict(id='discord', label='the Discord button')])
        job = PaletteTests.wait(self, self.themes.apply(clean_plan(dict(operations=[], hide=['discord'])), 'hide discord'))
        self.assertEqual((job['state'], self.lab.hidden()), ('complete', ['discord']))
        self.assertIn('1 hidden menu control', job['message'])
        self.themes.undo()
        self.assertEqual(self.lab.hidden(), [])
        with patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('remove the left promos')
        self.assertEqual((result['source'], result['plan']['hide']), ('tags', ['left_banner_1', 'left_banner_2']))

    def test_game_colors_join_the_mod_and_restore_exactly(self):
        original = (self.game / 'Mitosis.exe').read_bytes()
        with self.assertRaises(ValueError): self.lab.set_palette({'NOT_A_COLOR': '#ffffff'})
        with self.assertRaises(ValueError): self.lab.set_palette({'BG_COLOR': 'navy'})
        self.assertEqual(self.lab.set_palette({'BG_COLOR': '#0b1d3a'}), {'BG_COLOR': '#0B1D3A'})
        self.assertEqual(self.lab.status()['palette'], {'BG_COLOR': '#0B1D3A'})
        with patch.object(lab_core.exe_palette, 'patch', side_effect=self.fake_patch):
            build = self.lab.build()                                   # colors alone are a valid mod
        exe = next(f for f in build['files'] if f['path'] == 'Mitosis.exe')
        self.assertEqual((exe['original_sha256'], exe['edited_bytes']), (file_hash(self.game / 'Mitosis.exe'), len(original)))
        self.lab.install(build['id'])
        self.assertTrue((self.game / 'Mitosis.exe').read_bytes().endswith(b'EDIT'))
        self.lab.restore()
        self.assertEqual((self.game / 'Mitosis.exe').read_bytes(), original)
        self.assertEqual(self.lab.set_palette({}), {})
        with self.assertRaisesRegex(ValueError, 'at least one edit'): self.lab.build()

    def test_the_real_patcher_refuses_any_other_executable(self):
        self.lab.set_palette({'ACTIVE_ITEM_COLOR': '#22d3ee'})
        with self.assertRaisesRegex(ValueError, 'Unsupported or already edited'): self.lab.build()

    def test_theme_ai_sets_and_undoes_game_colors_without_touching_the_art(self):
        self.assertEqual(rule_plan('make the menu background navy and the pink cyan')['ui_colors'], {'background': '#1e3a8a', 'accent': '#22d3ee'})
        self.assertEqual(rule_plan('arrows gold')['ui_colors'], {'accent': '#f5c542'})
        self.assertEqual(rule_plan('menu background pattern red')['ui_colors'], {})            # the pattern is art, not the code color
        self.assertEqual(rule_plan('arena background blue')['ui_colors'], {})
        ui_only = clean_plan(dict(operations=[], ui_colors={'background': '#0b1d3a', 'accent': 'teal', 'bogus': '#fff'}))
        self.assertEqual(ui_only['ui_colors'], {'background': '#0b1d3a', 'accent': '#14b8a6'})
        self.assertEqual(theme.tame(dict(ui_only, operations=[]), 'ocean theme')['ui_colors'], {})          # not asked: dropped
        self.assertEqual(theme.tame(dict(ui_only, operations=[]), 'make the menu background navy')['ui_colors'], {'background': '#0b1d3a'})
        preview = self.themes.preview(ui_only)
        self.assertEqual(sorted((u['name'], u['was']) for u in preview['ui_colors']), [('accent', '#E52C58'), ('background', '#0D2829')])
        # An earlier art theme must survive a request that only changes the game colors.
        self.wait(self.themes.apply(clean_plan(dict(operations=[dict(targets=['everything'], brightness=0.5)])), 'dim'))
        sheet = next(k for k, s in self.themes.sheets.items() if s['row']['name'] == 'theme4.png')
        dimmed = self.lab.revision(sheet)
        job = self.wait(self.themes.apply(ui_only, 'navy and teal'))
        self.assertEqual((job['state'], job['changed']), ('complete', 0))
        self.assertEqual(self.lab.palette(), {'BG_COLOR': '#0B1D3A', 'ACTIVE_ITEM_COLOR': '#14B8A6'})
        self.assertEqual(self.lab.revision(sheet), dimmed)
        self.themes.undo()
        self.assertEqual((self.lab.palette(), self.lab.revision(sheet)), ({}, dimmed))
        with patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('make the menu background navy')
        self.assertEqual((result['source'], result['plan']['ui_colors'], result['preview']['sheets']), ('tags', {'background': '#1e3a8a'}, 0))

    def wait(self, job):
        for _ in range(400):
            state = self.themes.job(job['id'])
            if state['state'] != 'running': return state
            time.sleep(0.02)
        self.fail('theme job did not finish')


class GameWindowTests(Base):
    def test_closing_mitosis_asks_first_and_forces_only_if_it_will_not_close(self):
        running = [['Mitosis.exe']]
        self.lab.process_check = lambda: running[0]
        calls = []
        def taskkill(args, **kw):
            calls.append(args)
            if '/F' not in args: running[0] = []          # the window closes when asked
            return None
        with patch.object(lab_core.os, 'name', 'nt'), patch.object(lab_core.subprocess, 'run', side_effect=taskkill):
            self.assertEqual(self.lab.game_state(), {'running': True})
            self.assertEqual(self.lab.close_game(wait=1), {'closed': True, 'forced': False})
        self.assertEqual(calls, [['taskkill.exe', '/IM', 'Mitosis.exe']])
        running[0], calls[:] = ['Mitosis.exe'], []
        def stubborn(args, **kw):
            calls.append(args)
            if '/F' in args: running[0] = []                  # only a forced close works
        with patch.object(lab_core.os, 'name', 'nt'), patch.object(lab_core.subprocess, 'run', side_effect=stubborn), patch.object(lab_core.time, 'sleep'):
            self.assertEqual(self.lab.close_game(wait=0.01), {'closed': True, 'forced': True})
        self.assertEqual([c[1] for c in calls], ['/IM', '/F'])
        self.assertEqual(self.lab.game_state(), {'running': False})
        self.assertEqual(self.lab.close_game(), {'closed': True, 'forced': False})   # nothing to close

    def test_theme_result_counts_what_changed_and_what_already_matched(self):
        job = self.themes.apply(clean_plan(dict(operations=[dict(targets=['everything'], color='#ff0000', color_mode='tint')])), 'red')
        for _ in range(400):
            state = self.themes.job(job['id'])
            if state['state'] != 'running': break
            time.sleep(0.02)
        self.assertEqual(state['state'], 'complete')
        self.assertEqual(state['saved'] + state['unchanged'], state['total'])
        self.assertTrue(state['message'].startswith(f"Saved {state['saved']} sheet"))
        self.assertEqual(('already looked right' in state['message']), state['unchanged'] > 0)


class RestartTests(unittest.TestCase):
    def test_code_stamp_moves_when_python_changes(self):
        import lab_server
        self.assertGreaterEqual(lab_server.code_stamp(), lab_server.STARTED_CODE)
        with patch.object(lab_server, 'code_stamp', return_value=lab_server.STARTED_CODE + 5):
            self.assertTrue(lab_server.code_stamp() > lab_server.STARTED_CODE)


if __name__ == '__main__': unittest.main()
