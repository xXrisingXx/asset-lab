from harness import EditorTestCase
from fakegame import make_editor_game
from hide_fixture import add_toolbar_controls


def toolbar_game(root):
    return add_toolbar_controls(make_editor_game(root))


class ToolbarPresetsBrowser(EditorTestCase):
    viewport = (1366, 900)
    game_maker = staticmethod(toolbar_game)

    def test_toolbar_removals_and_black_override_can_join_a_complete_button_set(self):
        b = self.browser
        b.eval("localStorage.setItem('lab-presets-tour-done','1'); LabPresets.open('Menu buttons')")
        b.wait_for("!!document.querySelector('[data-cat=\"Clean up\"]')")
        b.click_selector('[data-cat="Clean up"]')
        for part in ('toolbar', 'potion', 'block', 'chat', 'pause'):
            self.assertTrue(b.eval(f"!!document.querySelector('[data-id=\"preset:hide-hud-{part}\"]')"))
        b.screenshot('toolbar-remove-options.png')
        b.click_selector('[data-id="preset:hide-hud-chat"]')
        b.click_selector('#presetUse')
        b.eval("LabPresets.open('In-game buttons')")
        b.click_selector('[data-id="preset:delta-tron-ingame"]')
        b.wait_for("[...document.querySelectorAll('.pv-stage img')].every(i=>i.complete&&i.naturalWidth>0)")
        b.screenshot('expanded-tron-ingame-controls.png')
        b.click_selector('#presetUse')
        b.click_selector('[data-id="preset:black-block-player"]')
        b.wait_for("!!document.querySelector('#presetUse')")
        self.assertIn('every active copy', b.eval("document.querySelector('.pv-description').textContent"))
        self.assertFalse(b.eval("!!document.querySelector('#presetWords')"))
        b.screenshot('black-block-preset.png')
        b.click_selector('#presetUse')
        b.wait_for("document.querySelector('#presetsUseAll').textContent==='Use all (3)'")
        b.screenshot('toolbar-preset-combination.png')
        b.click_selector('#presetsUseAll')
        b.wait_for("document.querySelector('#toast')?.textContent.includes('3 presets')", timeout=30)
        status = self.server.get('/api/status')
        expected = {r['key'] for name in ('icon-ingame-potion', 'icon-ingame-potion-desktop', 'icon-report-abuse',
                                         'icon-ingame-chat', 'icon-ingame-chat-desktop', 'icon-pause-button')
                    for r in self.server.assets(name, all='1') if r['name'] == name}
        self.assertTrue(expected <= {r['key'] for r in status['staged']})
        self.assertFalse(status['active'])
