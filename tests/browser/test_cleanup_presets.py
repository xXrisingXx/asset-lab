from harness import EditorTestCase
from fakegame import make_editor_game
from hide_fixture import add_hide_controls


def hide_game(root):
    return add_hide_controls(make_editor_game(root))


class HidePresetsBrowser(EditorTestCase):
    viewport = (1366, 900)
    game_maker = staticmethod(hide_game)

    def test_both_hide_options_can_be_picked_and_applied_together(self):
        b = self.browser
        b.eval("localStorage.setItem('lab-presets-tour-done','1'); LabPresets.open('Menu buttons')")
        b.wait_for("!!document.querySelector('[data-cat=\"Clean up\"]')")
        b.click_selector('[data-cat="Clean up"]')
        for pid in ['hide-menu-circle', 'hide-grey-panels']:
            b.click_selector(f'[data-id="preset:{pid}"]')
            b.wait_for("!!document.querySelector('#presetUse')")
            note = b.eval("document.querySelector('.pv-description').textContent")
            self.assertIn('share', note)
            if pid == 'hide-grey-panels':
                self.assertIn('MASS', note)
                b.wait_for("document.querySelector('.pv-stage img')?.complete === true")
                b.screenshot('hide-grey-panels-detail.png')
            b.click_selector('#presetUse')
        b.wait_for("document.querySelectorAll('.pv-pick').length === 2")
        self.assertEqual(b.eval("document.querySelector('#presetsUseAll').textContent"), 'Use all (2)')
        b.screenshot('hide-presets-picked.png')
        # Unpick/re-pick one without losing the other.
        b.click_selector('[data-id="preset:hide-menu-circle"]')
        b.click_selector('#presetUse')
        self.assertEqual(b.eval("document.querySelectorAll('.pv-pick').length"), 1)
        b.click_selector('[data-id="preset:hide-menu-circle"]')
        b.click_selector('#presetUse')
        b.click_selector('#presetsUseAll')
        b.wait_for("!document.querySelector('#presetsDialog').open")
        b.wait_for("document.querySelector('#toast')?.textContent.includes('2 presets')", timeout=30)
        status = self.server.get('/api/status')
        self.assertEqual(len(status['staged']), 6)
        self.assertFalse(status['active'])
        expected = {r['key'] for name in ('button-circle', 'rounded-bg') for r in self.server.assets(name, all='1') if r['name'] == name}
        self.assertEqual({r['key'] for r in status['staged']}, expected)
