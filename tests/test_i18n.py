"""The lab's languages (src/web/i18n.js and src/web/i18n/<lang>.json): the dictionaries are complete and consistent, the
page loads the translation layer first, the server tells the page the chosen language, and the planner model is asked
to reply in it. What the page looks like in another language is proven in tests/browser/test_i18n.py."""
import json
from pathlib import Path
import re
import tempfile
import unittest
import lab_server
from settings import Settings
import theme

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src' / 'web'
HOLE = re.compile(r'\{[a-z]+\d*\}')
KEYS = {'Ctrl', 'Shift', 'Alt', 'Delete', 'Backspace', 'Space', 'Enter', 'Esc', 'Tab', 'Home', 'End'}


def dictionary(code):
    return json.loads((WEB / 'i18n' / f'{code}.json').read_text('utf-8'))


class Dictionaries(unittest.TestCase):
    def test_every_language_of_the_page_has_a_dictionary(self):
        langs = re.search(r"const LANGS=\[([^\]]*)\]", (WEB / 'i18n.js').read_text('utf-8')).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", langs)), lab_server.LANGUAGES)
        for code in lab_server.LANGUAGES[1:]:
            with self.subTest(code):
                data = dictionary(code)
                self.assertEqual(data['language'], code)
                self.assertGreater(len(data['strings']), 500)
                self.assertGreater(len(data['patterns']), 100)

    def test_translations_are_complete_and_keep_their_placeholders(self):
        english = None
        for code in lab_server.LANGUAGES[1:]:
            with self.subTest(code):
                data = dictionary(code)
                for section in ('strings', 'patterns'):
                    for en, text in data[section].items():
                        self.assertIsInstance(text, str, en)
                        self.assertTrue(text.strip(), f'{code}: empty translation of {en!r}')
                        self.assertEqual(text, text.strip() if en == en.strip() else text, f'{code}: stray spaces in {text!r}')
                        self.assertEqual(sorted(HOLE.findall(en)), sorted(HOLE.findall(text)), f'{code}: placeholders of {en!r} -> {text!r}')
                for en in data['patterns']:
                    self.assertTrue(HOLE.search(en), f'{code}: a pattern without placeholders: {en!r}')
                    literal = max(HOLE.split(en), key=len)
                    self.assertGreaterEqual(len(literal.strip()), 3, f'{code}: {en!r} would match almost anything')
                self.assertLessEqual(set(data.get('keys', {})), KEYS)
                # Every language translates the same English, so none falls back to English where the others do not.
                keys = (set(data['strings']), set(data['patterns']))
                if english is None: english = keys
                self.assertEqual(keys[0] ^ english[0], set(), f'{code}: strings differ from the other languages')
                self.assertEqual(keys[1] ^ english[1], set(), f'{code}: patterns differ from the other languages')

    def test_product_names_stay_as_they_are(self):
        for code in lab_server.LANGUAGES[1:]:
            with self.subTest(code):
                data = dictionary(code)
                for en, text in {**data['strings'], **data['patterns']}.items():
                    # A short label may leave the name out ("Check for updates when Asset Lab starts" -> "...at start"), but
                    # a name that is there is never translated. German joins names into compounds: "Theme-AI-Wunsch".
                    for name in ('Theme AI', 'Mitosis', 'Steam'):
                        if name in en: self.assertTrue(name in text or name.replace(' ', '-') in text, f'{code}: {en!r} -> {text!r}')
                    self.assertNotRegex(text, r'(?i)laborat[oó]ri|asset-labor|IA de tema|IA de th[eè]me|Thema-KI|IA de temas')


class Page(unittest.TestCase):
    def test_the_translation_layer_loads_before_anything_else(self):
        page = (WEB / 'index.html').read_text('utf-8')
        head = page[:page.index('</head>')]
        scripts = re.findall(r'<script src="([^"]+)"', page)
        self.assertEqual(scripts[0], 'i18n.js')
        self.assertIn('<script src="i18n.js">', head)
        self.assertLess(head.index('i18n.js'), head.index('<link'))
        self.assertIn('<html lang="en">', page)   # what lab_server.page_language fills in

    def test_the_server_writes_the_chosen_language_on_the_page(self):
        page = b'<!doctype html>\n<html lang="en"><head></head></html>'
        self.assertIn(b'<html lang="es" data-language="es">', lab_server.page_language(page, 'es'))
        self.assertIn(b'<html lang="en" data-language="">', lab_server.page_language(page, ''))

    def test_the_language_setting_is_read_from_settings_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'settings.json'
            lab = type('Lab', (), {'settings': Settings(path)})()
            self.assertEqual(lab_server.ui_language(lab), '')
            for value, expected in (('de', 'de'), ('pt', 'pt'), ('', ''), ('xx', ''), (3, '')):
                path.write_text(json.dumps({'language': value}), 'utf-8')
                lab.settings._stamp = None   # the file changed within the same clock tick
                self.assertEqual(lab_server.ui_language(lab), expected, value)
            self.assertEqual(lab_server.ui_language(type('Lab', (), {})()), '')


class PlannerReply(unittest.TestCase):
    def test_the_model_is_asked_to_reply_in_the_lab_language(self):
        self.assertEqual(theme.reply_language(''), '')
        self.assertEqual(theme.reply_language('en'), '')
        self.assertEqual(theme.reply_language('xx'), '')
        for code, name in (('es', 'Spanish'), ('pt', 'Portuguese'), ('fr', 'French'), ('de', 'German')):
            text = theme.reply_language(code)
            self.assertIn(name, text)
            self.assertIn('"reply"', text)


if __name__ == '__main__':
    unittest.main()
