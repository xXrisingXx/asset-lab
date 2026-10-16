import json
from harness import EditorTestCase
from test_audio import sound_game


class SoundMenu(EditorTestCase):
    """Right-click on a sound: the entries that fit a sound (Name…, Loop, Play…), not the image ones."""
    game_maker = staticmethod(sound_game)

    def test_right_click_a_sound_and_loop_it(self):
        b = self.browser
        key = self.server.asset_key('click.ogg')
        b.eval(f'await select({json.dumps(key)}, true); true')
        b.wait_for(f"LabAudio.state().visible && LabAudio.state().key === {json.dumps(key)}")
        x, y = b.center_of('#viewport')[:2]
        b.click(x, y, button='right')
        b.wait_for("!document.getElementById('contextMenu').hidden")
        shown = b.eval("[...document.querySelectorAll('#contextMenu button')].filter(x => !x.hidden).map(x => x.dataset.action)")
        for action in ('audio-play', 'audio-loop', 'name-asset', 'pool-remove'): self.assertIn(action, shown)
        for action in ('save', 'export', 'sheet', 'find-similar'): self.assertNotIn(action, shown)
        loop = b.eval("LabAudio.state().loop")
        b.eval("document.querySelector('#contextMenu [data-action=audio-loop]').click()")
        self.assertEqual(b.eval("LabAudio.state().loop"), not loop)
        self.assertEqual(b.eval("document.getElementById('aeLoop').getAttribute('aria-pressed')"), str(not loop).lower())
        b.click_selector('#aeLoop')                                            # the button in the editor turns it back
        self.assertEqual(b.eval("LabAudio.state().loop"), loop)
        # An image gets the image entries back.
        b.select_asset('edit-sprite.png')
        x, y = b.center_of('#viewport')[:2]
        b.click(x, y, button='right')
        b.wait_for("!document.getElementById('contextMenu').hidden")
        shown = b.eval("[...document.querySelectorAll('#contextMenu button')].filter(x => !x.hidden).map(x => x.dataset.action)")
        self.assertIn('save', shown); self.assertNotIn('audio-loop', shown)
        b.key('Escape')
