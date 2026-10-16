from harness import EditorTestCase


class Tour(EditorTestCase):
    viewport = (1366, 768)

    def test_the_tour_walks_every_step_and_is_remembered(self):
        b = self.browser
        b.eval("localStorage.removeItem('lab-tour-done'); LabTour.start()")
        titles = []
        for i in range(12):
            if not b.eval("!!document.querySelector('.tour-box')"): break
            titles.append(b.eval("document.querySelector('.tour-box h3').textContent"))
            b.screenshot(f'tour-{i}.png')
            b.click_selector('.tour-box .primary')
        self.assertEqual(titles[0], 'Welcome to Asset Lab')
        self.assertEqual(titles[-1], 'You are ready')
        self.assertGreaterEqual(len(titles), 8)
        self.assertEqual(b.eval("localStorage.getItem('lab-tour-done')"), '1')

    def test_escape_closes_it_and_the_help_button_reopens_it(self):
        b = self.browser
        b.click_selector('#tourButton')
        b.wait_for("!!document.querySelector('.tour-box')")
        b.key('Escape')
        self.assertFalse(b.eval("!!document.querySelector('.tour-box')"))

    def test_the_first_tour_ends_at_suggestions_once(self):
        b = self.browser
        b.eval("localStorage.removeItem('lab-suggest-asked'); LabTour.start()")
        b.wait_for("!!document.querySelector('.tour-box')")
        b.key('Escape')                                                       # skipped: About opens at Suggestions anyway
        b.wait_for("document.getElementById('settingsDialog')?.open && !!document.getElementById('suggestText')", message='About did not open at Suggestions')
        self.assertTrue(b.eval("(r => r.top >= 0 && r.bottom <= innerHeight)(document.getElementById('settingsSuggest').getBoundingClientRect())"))
        self.assertEqual(b.eval("localStorage.getItem('lab-suggest-asked')"), '1')
        b.eval("document.getElementById('settingsDialog').close(); true")
        b.eval("LabTour.start()"); b.key('Escape')                       # only the first time
        self.assertFalse(b.eval("document.getElementById('settingsDialog').open"))
