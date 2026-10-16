from unittest.mock import patch
import design
import inspire
import theme
from test_theme import Base


class Names(Base):
    def test_names_are_found_and_looks_are_not(self):
        cases = {'give the whole game a post malone theme': ['post malone'], 'make it look like minecraft': ['minecraft'],
                 'inspired by the matrix': ['the matrix'], 'a cyberpunk 2077 style menu': ['cyberpunk 2077'],
                 'design a bmw theme': ['bmw'], 'dark gold vibes': [], 'full spooky reskin': [], 'make the buttons red': [], 'help': [], 'undo': []}
        for text, want in cases.items(): self.assertEqual(inspire.candidates(text), want, text)

    def test_a_named_theme_is_carried_into_the_drawings_and_can_be_switched_off(self):
        found = dict(title='Post Malone', summary='American rapper known for face tattoos and a laid-back style.')
        with patch.object(inspire, 'lookup', return_value=found) as lookup, patch.object(theme, 'models', return_value=[]), \
             patch.object(inspire, 'pictures', return_value='') as pictures:
            result = self.themes.chat('give the whole game a post malone theme')
            self.assertIn('Inspired by Post Malone', result['note'])
            self.lab.settings.update({'web_inspiration': False})
            off = self.themes.chat('give the whole game a post malone theme')
        self.assertEqual(lookup.call_count, 1)                      # nothing is looked up when it is off
        self.assertEqual(pictures.call_count, 1)
        self.assertNotIn('Inspired by', off.get('note') or '')
        if result.get('design'): self.assertIn('Post Malone', result['design']['prompt'])


class Pictures(Base):
    """The drawings take their look from pictures of the thing named, not only from its summary."""
    def setUp(self):
        super().setUp()
        self.designer = design.Designer(self.lab, self.themes)

    def test_a_board_is_made_from_the_pages_pictures_emblems_first(self):
        from PIL import Image
        lead = 'https://upload.wikimedia.org/wikipedia/commons/a/ab/BMW_Group.svg'
        badge = 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/44/BMW_logo_%28gray%29.svg/500px-BMW_logo_%28gray%29.svg.png'
        shots = {lead: Image.new('RGB', (300, 100), 'black'), badge: Image.new('RGB', (200, 200), 'blue')}
        got = []
        with patch.object(inspire, '_page_pictures', return_value=[badge, lead.replace('/a/ab/', '/thumb/a/ab/') + '/500px-BMW_Group.svg.png']), \
             patch.object(inspire, '_download', side_effect=lambda u, t: got.append(u) or shots[u]):
            name = inspire.pictures(dict(title='BMW', image=lead), self.lab.root / 'inspiration')
        self.assertRegex(name, r'^[0-9a-f]{20}\.png$')
        self.assertEqual(got, [badge, lead])                         # the roundel first, the same wordmark only once
        with Image.open(self.lab.root / 'inspiration' / name) as board:
            self.assertLessEqual(max(board.size), 1024)
            self.assertEqual(board.getpixel((5, 5))[:3], (0, 0, 255))
        with patch.object(inspire, '_download') as again:            # kept on this PC: not fetched twice
            self.assertEqual(inspire.pictures(dict(title='BMW', image=lead), self.lab.root / 'inspiration'), name)
        again.assert_not_called()

    def test_nothing_from_other_hosts_and_no_board_offline(self):
        with patch.object(inspire, '_page_pictures', side_effect=OSError('offline')), patch.object(inspire, '_download') as download:
            self.assertEqual(inspire.pictures(dict(title='X', image='https://example.com/x.png'), self.lab.root / 'inspiration'), '')
        download.assert_not_called()
        self.assertEqual(inspire.pictures(None, self.lab.root / 'inspiration'), '')

    def test_the_drawings_are_told_to_use_the_board(self):
        job = dict(title=True, names=['logo'], frames=[[500, 110]], key='green', prompt='bmw', inspiration='0' * 20 + '.png')
        self.assertIn('second reference picture', self.designer.instruction(job))
        self.assertIn('round O of MITOSIS', self.designer.instruction(job))
        self.assertIn('Take the look from the reference picture', self.designer.instruction(dict(job, scene=True)))
        self.assertNotIn('reference picture', self.designer.instruction(dict(job, inspiration=None)))

    def test_the_summary_never_renames_the_title(self):
        prompt = ('BMW theme. Inspiration (BMW): The moniker "BMW" first came into use when the German firm Rapp Motorenwerke '
                  'changed its name to Bayerische Motoren Werke GmbH in 1917.')
        self.assertEqual(design.own_words(prompt), 'BMW theme.')
        self.assertEqual(design.wording(design.own_words(prompt)), '')
        self.assertEqual(design.own_words('rename the title to MITOSIS 2'), 'rename the title to MITOSIS 2')

    def test_a_named_theme_with_one_part_described_draws_the_whole_game(self):
        self.themes.designer = self.designer
        key = next(k for k, r in self.lab.catalog.rows.items() if r['editable'])
        found = dict(title='BMW', summary='German maker of luxury cars.')
        part = lambda: {'design': {'prompt': 'make the food little tires', 'targets': [{'key': key, 'name': 'x'}]}, 'note': ''}
        with patch.object(inspire, 'pictures', return_value=''):
            result = part()
            self.themes.inspired(result, found, None, 'auto', 'create me a bmw theme and make the food little tires')
            own = result['design']['prompts'][key]
            self.assertTrue(own.startswith('make the food little tires. BMW theme.'), own)
            self.assertIn(key, [t['key'] for t in result['design']['targets']])
            self.assertTrue(result['design']['prompt'].startswith('BMW theme.'))
            # No theme asked for: only that part, with the inspiration added to its words.
            alone = part()
            self.themes.inspired(alone, found, None, 'auto', 'make the food little bmw tires like bmw')
            self.assertNotIn('prompts', alone['design'])
            self.assertTrue(alone['design']['prompt'].startswith('make the food little tires Inspiration (BMW)'))
