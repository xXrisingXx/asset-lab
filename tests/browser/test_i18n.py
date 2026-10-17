"""Asset Lab in another language, in a real (headless) Edge at 1366 x 768: the lab started with "language" set in
settings.json shows its library, editor (menus, tools, panels, context menu), Theme AI dock, Review, Text & fonts,
sound editor and Settings with no English left in the visible chrome, Theme AI's fixed replies come back translated,
and nothing overflows its box. Screenshots of every screen go to tests/browser/output/i18n-<lang>-*.png."""
import json
from pathlib import Path
import re
from harness import EditorTestCase
from test_audio import sound_game

WEB = Path(__file__).resolve().parents[2] / 'src' / 'web'
# Names that stay as they are in every language, and what the fake game calls its art.
KEEP = {'Asset Lab', 'ASSET LAB', 'Theme AI', 'THEME AI', 'Mitosis', 'MITOSIS', 'Steam', 'Ollama', 'OK', 'Atlas', 'Sprites', 'Mod', 'px',
        'English', 'Español', 'Português', 'Français', 'Deutsch'}   # the language picker names each language in itself
FILE = re.compile(r'\.(png|jpe?g|webp|ogg|wav|mp3|xml|zip|json|fnt|exe|awp|bin|atf)\b|[/\\]|^#[0-9a-f]{6}$', re.I)

# Every visible element whose text does not fit: wider than its box (cut off), past the window's right edge, or a
# one-line control (a toolbar button, tab, menu entry) that had to wrap onto a second line.
OVERFLOW_JS = r"""(() => {
  const bad = [], seen = new Set();
  const shown = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' && el.closest('[hidden]') === null; };
  const oneLine = 'header button, .segmented button, .zoom-controls button, .mod-heading button, .pagination button, .ed-tab, .ed-menu-top, .ed-menu-item, .theme-dock-actions button, .select-bar button, .actions button, .history-buttons button, .ed-option-button, .context button, .settings-nav button, .dialog-actions button, .review-foot button, .ae-actions button, .fonts-head button, .theme-actions button, .badge, .section-label';
  for (const el of document.querySelectorAll('button, label, summary, select, .section-label, .badge, h1, h2, h3, a.button, .ed-tab, .ed-menu-item, .settings-nav [role=tab]')) {
    if (!shown(el) || seen.has(el)) continue;
    const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
    if (!/[A-Za-zÀ-ÿ]/.test(text)) continue;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    let why = '';
    if (el.scrollWidth > el.clientWidth + 2 && cs.overflowX !== 'visible' && el.tagName !== 'SELECT') why = 'cut off';
    else if (r.right > innerWidth + 1) why = 'past the right edge';
    else if (el.matches(oneLine)) {
      const line = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.25;
      const inner = r.height - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - parseFloat(cs.borderTopWidth) - parseFloat(cs.borderBottomWidth);
      if (inner > line * 1.6) why = 'wraps';
    }
    if (why) { seen.add(el); bad.push(`${why}: <${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : ''}> "${text.slice(0, 60)}" (${Math.round(r.width)}x${Math.round(r.height)})`); }
  }
  return bad;
})()"""


class Translated(EditorTestCase):
    """Base: the lab started in self.language. Subclasses pick the language; the tests run in name order."""
    language = None
    viewport = (1366, 768)
    game_maker = staticmethod(sound_game)
    open_on_start = False
    allowed_errors = ('/api/font-preview',)   # the fake game has no fonts of its own, so the dialog's previews fail

    @classmethod
    def setUpClass(cls):
        if cls.language is None: raise __import__('unittest').SkipTest('base class')
        super().setUpClass()
        try:
            (cls.server.home / 'settings.json').write_text(json.dumps({'language': cls.language}), 'utf-8')
            cls.words = json.loads((WEB / 'i18n' / f'{cls.language}.json').read_text('utf-8'))
            cls.browser.open_lab(cls.server)
            cls.browser.eval('await LabI18n.ready; true')
        except BaseException:
            cls._shutdown()
            raise

    # ----- helpers -------------------------------------------------------------------------------------------------
    def tr(self, english): return self.words['strings'][english]

    def english_left(self, root='document.body'):
        """Visible English the dictionary does not cover, less names that stay as they are."""
        left = self.browser.eval(f'LabI18n.untranslated({root})')
        def kept(text):
            t = re.sub(r'^\[[\w-]+\] ', '', text).strip('✦ ')
            return t in KEEP or bool(re.fullmatch(r'Asset Lab [\d.]+\S*', t)) or bool(FILE.search(t)) or not re.search(r'[a-z]{3,}', t) or t.startswith('edit-') or t.startswith('qwen')
        return sorted(t for t in left if not kept(t))

    def assertTranslated(self, root='document.body', where=''):
        left = self.english_left(root)
        self.assertEqual(left, [], f'English left in {where or root} ({self.language}):\n  ' + '\n  '.join(left))

    def assertFits(self, where):
        bad = self.browser.eval(OVERFLOW_JS)
        self.assertEqual(bad, [], f'Text that does not fit in {where} ({self.language}):\n  ' + '\n  '.join(bad))

    def shot(self, name): self.browser.screenshot(f'i18n-{self.language}-{name}.png')

    def setUp(self):
        super().setUp()
        # Theme AI's drawer is remembered open across reloads and covers the inspector: each screen starts with it closed.
        if self.browser.eval("!!document.getElementById('themeDrawer') && !document.getElementById('themeDrawer').hidden"):
            self.browser.click_selector('#themeClose')

    # ----- screens -------------------------------------------------------------------------------------------------
    def test_1_page_starts_in_the_language(self):
        b = self.browser
        self.assertEqual(b.eval('document.documentElement.lang'), self.language)
        self.assertEqual(b.eval('LabI18n.lang'), self.language)
        self.assertEqual(b.eval("document.documentElement.style.visibility"), '')
        self.assertEqual(b.text('#stopButton'), self.tr('Stop lab'))
        self.assertEqual(b.eval("document.getElementById('search').placeholder"), self.tr('Search buttons, skins, backgrounds…'))
        self.assertEqual(b.eval("LabI18n.t('Stop lab')"), self.tr('Stop lab'))
        self.assertTranslated(where='the library')
        self.shot('library')

    def test_2_editor_menus_tools_and_panels(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.settle()
        self.assertEqual(b.text('#saveButton'), self.tr('Save edit + recipe'))
        self.assertTranslated(where='the editor')
        self.assertFits('the editor')
        self.shot('editor')
        for name in b.eval("[...document.querySelectorAll('#edMenubar [data-menu]')].map(m => m.dataset.menu)"):
            b.click_selector(f'#edMenubar [data-menu="{name}"]')
            b.wait_for("!!document.querySelector('.ed-menu')")
            self.assertTranslated("document.querySelector('.ed-menu')", f'the {name} menu')
            self.assertFits(f'the {name} menu')
            if name == 'Layer': self.shot('menu-layer')
            b.key('Escape')
            b.wait_for("!document.querySelector('.ed-menu')")
        b.click_selector('#edTab-history')
        self.assertTranslated("document.getElementById('edDock')", 'the History panel')
        b.click_selector('#edTab-layers')
        x, y = b.center_of('#view')
        b.click(x, y, button='right')
        b.wait_for("!document.getElementById('contextMenu').hidden")
        self.assertTranslated("document.getElementById('contextMenu')", 'the context menu')
        self.shot('context-menu')
        b.key('Escape')

    def test_3_theme_ai(self):
        b = self.browser
        b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden && !!document.querySelector('#themeLog .theme-bubble')")
        b.eval("document.querySelectorAll('.theme-ideas').forEach(d => d.open = true); true")
        b.settle()
        self.assertTranslated("document.getElementById('themeDrawer')", 'Theme AI')
        self.assertFits('Theme AI')
        self.shot('theme-ai')
        b.eval("document.querySelectorAll('.theme-ideas').forEach(d => d.open = false); true")
        # A fixed reply from the lab (here the help text) comes back in the language.
        b.set_value('#themeInput', 'help')
        b.click_selector('#themeSend')
        b.wait_for("document.querySelectorAll('#themeLog .theme-bubble.bot').length >= 2 && !document.getElementById('themeSend').disabled", 20)
        reply = b.eval("[...document.querySelectorAll('#themeLog .theme-bubble.bot')].at(-1).innerText.trim()")
        self.assertEqual(reply, self.tr(next(k for k in self.words['strings'] if k.startswith('I change how Mitosis looks.'))))
        self.assertEqual(b.eval("document.querySelector('#themeLog .theme-bubble.user').innerText.trim()"), 'help')   # what the player typed stays
        b.click_selector('#themeMore summary')                                        # the folded extras: Image AI, auto-apply, ideas
        b.wait_for("document.getElementById('themeMore').open")
        self.assertTranslated("document.getElementById('themeDrawer')", 'Theme AI · More')
        self.shot('theme-more')
        b.click_selector('#themeMore summary')
        b.click_selector('#themeClose')

    def test_4_review(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.click_selector('[data-tool="brush"]')
        b.drag_image([(10.5, 10.5), (30.5, 30.5)])
        b.click_selector('#saveButton')
        b.wait_for("!S.dirty && !S.busy && !document.getElementById('toast').hidden", 20)
        self.assertEqual(b.text('#toast'), self.tr('Saved in your workspace. Install saved mod to test in Mitosis.'))
        b.click_selector('#reviewButton')
        b.wait_for("document.getElementById('reviewDialog').open && !!document.querySelector('#reviewDialog .review-card')", 20)
        self.assertTranslated("document.getElementById('reviewDialog')", 'Review')
        self.assertFits('Review')
        self.shot('review')
        b.eval("document.getElementById('reviewDialog').close(); true")

    def test_5_fonts(self):
        b = self.browser
        b.eval('openFonts(); true')
        b.wait_for("document.getElementById('fontsDialog').open && !!document.querySelector('#fontsDialog .fonts-list option')", 30)
        b.settle()
        self.assertTranslated("document.getElementById('fontsDialog')", 'Text & fonts')
        self.assertFits('Text & fonts')
        self.shot('fonts')
        b.eval("document.getElementById('fontsDialog').close(); true")

    def test_6_sound_editor(self):
        b = self.browser
        key = self.server.asset_key('st1.ogg')
        b.eval(f'await select({json.dumps(key)}, true); true')
        b.wait_for(f"LabAudio.state().visible && document.getElementById('aeGamePlay').offsetWidth > 0 && document.querySelector('.ae-game b').textContent.includes('.ogg')", 20)
        b.settle()
        self.assertTranslated("document.querySelector('.audio-editor')", 'the sound editor')
        self.assertTranslated(where='the page with the sound editor')
        self.assertFits('the sound editor')
        self.shot('sound')

    def test_7_settings(self):
        b = self.browser
        if not b.eval('!!window.LabSettings'): self.skipTest('no Settings window in this lab')
        b.eval('LabSettings.open(); true')
        b.wait_for("document.getElementById('settingsDialog')?.open && !!document.querySelector('#settingsPanel .settings-row')", 20)
        for cat in b.eval("[...document.querySelectorAll('#settingsNav [data-category]')].map(t => t.dataset.category)"):
            b.click_selector(f'#settingsTab-{cat}')
            b.settle()
            self.assertTranslated("document.getElementById('settingsDialog')", f'Settings › {cat}')
            self.assertFits(f'Settings › {cat}')
            if cat in ('general', 'storage'): self.shot(f'settings-{cat}')
        b.eval("document.getElementById('settingsDialog').close(); true")


    def test_8_composed_text(self):
        """Text the lab puts together from pieces, numbers and names comes out translated around the names."""
        t = lambda s: self.browser.eval(f'LabI18n.t({json.dumps(s)})')
        p = self.words['patterns']
        for english, gone in (('Edits 1 KB · game backups 30 KB · builds 0 KB', ('Edits', 'game backups')),
                              ('Nothing is installed yet. This mod: 2 edits · 1 sound. Remove leaves an item out without deleting your work; Put back includes it again.',
                               ('installed', 'This mod', 'edits', 'sound', 'Remove leaves')),
                              ('64 × 64 · 3 regions', ('regions',)), ('Undo Brush Tool', ('Undo', 'Brush')),
                              ('rainbow gradient (45°, colorize), neon outline gradient 4px, outer glow auto 12px ×1.6', ('rainbow', 'outline', 'glow')),
                              ('Brush (B) · right-click or hold for more', ('Brush', 'right-click'))):
            out = t(english)
            for word in gone: self.assertNotIn(word, out, f'{english!r} -> {out!r}')
        self.assertEqual(t('Undo something unknown'), 'Undo something unknown')    # short patterns need their phrase translated
        self.assertEqual(t('button-up.png and icon.png'), 'button-up.png and icon.png')
        self.assertEqual(t('12 checked'), p['{n} checked'].replace('{n}', '12'))
        self.assertIn('my-sprite.png', t('my-sprite.png added to Favorites.'))   # names stay as they are
        if self.language == 'de': self.assertEqual(t('Shift+Ctrl+N'), 'Umschalt+Strg+N')


class Spanish(Translated):
    language = 'es'


class German(Translated):
    language = 'de'


class Portuguese(Translated):
    language = 'pt'


class French(Translated):
    language = 'fr'


del Translated   # only the languages run
