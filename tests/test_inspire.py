from unittest.mock import patch
import inspire
import theme
from test_theme import Base


class Names(Base):
    def test_names_are_found_and_looks_are_not(self):
        cases = {'give the whole game a post malone theme': ['post malone'], 'make it look like minecraft': ['minecraft'],
                 'inspired by the matrix': ['the matrix'], 'a cyberpunk 2077 style menu': ['cyberpunk 2077'],
                 'dark gold vibes': [], 'full spooky reskin': [], 'make the buttons red': [], 'help': [], 'undo': []}
        for text, want in cases.items(): self.assertEqual(inspire.candidates(text), want, text)

    def test_a_named_theme_is_carried_into_the_drawings_and_can_be_switched_off(self):
        found = dict(title='Post Malone', summary='American rapper known for face tattoos and a laid-back style.')
        with patch.object(inspire, 'lookup', return_value=found) as lookup, patch.object(theme, 'models', return_value=[]):
            result = self.themes.chat('give the whole game a post malone theme')
            self.assertIn('Inspired by Post Malone', result['note'])
            self.lab.settings.update({'web_inspiration': False})
            off = self.themes.chat('give the whole game a post malone theme')
        self.assertEqual(lookup.call_count, 1)                      # nothing is looked up when it is off
        self.assertNotIn('Inspired by', off.get('note') or '')
        if result.get('design'): self.assertIn('Post Malone', result['design']['prompt'])
