"""Suggestions to the creator (src/suggest.py): only the text and the version go to the form, and only when sent."""
import unittest
import urllib.parse
from unittest.mock import patch
import suggest


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Suggestions(unittest.TestCase):
    def setUp(self):
        patcher = patch.multiple(suggest, FORM='https://docs.google.com/forms/d/e/abc/viewform', ENTRY_TEXT='entry.11', ENTRY_VERSION='entry.22')
        patcher.start(); self.addCleanup(patcher.stop)
        suggest._sent.clear()
        self.sent = []

    def opener(self, request, timeout=0):
        self.sent.append((request.full_url, dict(urllib.parse.parse_qsl(request.data.decode())))); return Response()

    def test_the_text_and_version_go_to_the_form(self):
        self.assertTrue(suggest.send('Add a  blur tool\nplease', '1.0.33', opener=self.opener)['sent'])
        url, fields = self.sent[0]
        self.assertEqual(url, 'https://docs.google.com/forms/d/e/abc/formResponse')
        self.assertEqual(fields, {'entry.11': 'Add a  blur tool\nplease', 'entry.22': '1.0.33'})   # nothing else
        with patch.object(suggest, 'ENTRY_VERSION', ''):
            suggest._sent.clear(); suggest.send('dark mode', '1.0.33', opener=self.opener)
        self.assertEqual(self.sent[-1][1], {'entry.11': 'dark mode\n\n— Asset Lab 1.0.33'})   # no version question: it ends the text

    def test_empty_long_too_fast_and_offline(self):
        with self.assertRaisesRegex(ValueError, 'Write your suggestion'): suggest.send('  ', '1', opener=self.opener)
        with self.assertRaisesRegex(ValueError, 'under 2000'): suggest.send('x' * 2001, '1', opener=self.opener)
        suggest.send('first idea', '1', opener=self.opener)
        with self.assertRaisesRegex(ValueError, 'Wait a few seconds'): suggest.send('second idea', '1', opener=self.opener)
        suggest._sent.clear()
        def offline(request, timeout=0): raise OSError('no network')
        with self.assertRaisesRegex(ValueError, 'could not be sent'): suggest.send('an idea', '1', opener=offline)
        suggest.send('an idea', '1', opener=self.opener)       # a failed send does not count against the player

    def test_closed_until_the_form_is_set(self):
        with patch.object(suggest, 'ENTRY_TEXT', ''):
            self.assertFalse(suggest.info()['ready'])
            with self.assertRaisesRegex(ValueError, 'not open'): suggest.send('idea', '1', opener=self.opener)


if __name__ == '__main__': unittest.main()
