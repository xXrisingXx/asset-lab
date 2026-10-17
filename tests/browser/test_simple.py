from harness import EditorTestCase


class SimpleLayout(EditorTestCase):
    """Simple (simple.js): the library and Theme AI only. A click on a picture picks it for Theme AI; Advanced is the editor."""

    def tearDown(self):
        self.browser.eval("LabView.set('advanced'); window.LabSelection?.clear(); LabTour.end(true); true")
        super().tearDown()

    def test_simple_shows_the_library_and_theme_ai_and_a_click_picks(self):
        b = self.browser
        b.eval("LabView.set('simple'); true")
        b.wait_for("!document.getElementById('themeDrawer').hidden")
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('.studio')).display"), 'none')
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('.inspector')).display"), 'none')
        self.assertEqual(b.eval("getComputedStyle(document.getElementById('themeClose')).display"), 'none')
        b.key('Escape')                                                                # Theme AI is the page: it stays open
        b.wait_for("!document.getElementById('themeDrawer').hidden")
        before = b.eval("S.asset?.key || ''")
        b.click_selector('#assetList .asset-card .asset-label')
        b.wait_for("LabSelection.size() === 1")
        self.assertEqual(b.eval("S.asset?.key || ''"), before)                        # picked, not opened in the hidden editor
        self.assertIn('1', b.eval("document.querySelector('.theme-scope').textContent"))
        b.click_selector('#assetList .asset-card .asset-label')                        # again: unpicked
        b.wait_for("LabSelection.size() === 0")
        b.click_selector('#viewSwitch [data-view="advanced"]')
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('.studio')).display"), 'flex')
        self.assertEqual(b.eval("localStorage.getItem('lab-view')"), 'advanced')

    def test_a_new_player_starts_in_simple_and_everyone_else_keeps_advanced(self):
        b = self.browser
        self.assertFalse(b.eval("document.body.classList.contains('lab-simple')"))     # this browser has seen the tour
        b.eval("sessionStorage.setItem('tour-test','1'); localStorage.removeItem('lab-tour-done'); localStorage.removeItem('lab-view'); true")
        b.reload()
        try:
            b.wait_for("document.body.classList.contains('lab-simple')")
            b.wait_for("!!document.querySelector('.tour-box')", timeout=10)          # the short tour of Simple
            titles = []
            for _ in range(10):
                if not b.eval("!!document.querySelector('.tour-box')"): break
                titles.append(b.eval("document.querySelector('.tour-box h3').textContent"))
                b.click_selector('.tour-box .primary')
            self.assertEqual(titles, ['Welcome to Asset Lab', 'Pick what to change', 'Right-click for more', 'Describe it', 'Put it in the game',
                                      'Want more control?', 'You are ready'])
        finally:
            b.eval("sessionStorage.removeItem('tour-test'); localStorage.setItem('lab-tour-done','1'); localStorage.setItem('lab-view','advanced'); true")
            b.reload()

    def test_the_tour_shows_examples_and_the_right_click_menu(self):
        b = self.browser
        b.eval("LabTour.start(); true")
        for _ in range(12):
            if b.eval("document.querySelector('.tour-box h3').textContent") == 'Right-click for more': break
            b.click_selector('.tour-box .primary')
        b.wait_for("!!document.querySelector('.tour-demo-menu')")
        self.assertIn('Redraw with Image AI…', b.eval("document.querySelector('.tour-demo-menu').textContent"))
        b.click_selector('.tour-box .primary')
        self.assertFalse(b.eval("!!document.querySelector('.tour-demo-menu')"))       # gone with its step
        for _ in range(8):
            if b.eval("!!document.querySelector('.tour-examples')"): break
            b.click_selector('.tour-box .primary')
        b.wait_for("!document.getElementById('themeDrawer').hidden")                 # Theme AI opened for its step
        b.click_selector('.tour-examples button')
        self.assertEqual(b.eval("document.getElementById('themeInput').value"), 'gold buttons with a glow')
        b.key('Escape')
        self.assertFalse(b.eval("!!document.querySelector('.tour-box') || !!document.querySelector('.tour-demo-menu')"))
        b.eval("document.getElementById('themeInput').value=''; true")


class ThemeAiComposer(EditorTestCase):
    """Theme AI starts with one-click starters; extras sit under More; Game colors is gone."""

    def test_starters_fill_the_box_and_more_holds_the_extras(self):
        b = self.browser
        b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden")
        labels = b.eval("[...document.querySelectorAll('#themeStarters button')].map(x => x.textContent)")
        self.assertEqual(labels, ['Colors', 'Whole theme', 'Background', 'Redraw something', 'Text & fonts'])
        b.click_selector('#themeStarters button')
        self.assertEqual(b.eval("document.getElementById('themeInput').value"), 'Make the buttons and panels ')
        self.assertFalse(b.eval("!!document.getElementById('gameColorsButton')"))
        self.assertFalse(b.eval("document.getElementById('themeAuto').checkVisibility()"))   # folded under More
        b.click_selector('#themeMore summary')
        self.assertTrue(b.eval("document.getElementById('themeAuto').checkVisibility()"))
        self.assertTrue(b.eval("!!document.querySelector('#themeMore [data-draw=\"on\"]')"))
        b.click_selector('#themeMore summary')
        b.eval("document.getElementById('themeInput').value=''; true")
        b.click_selector('#themeClose')
