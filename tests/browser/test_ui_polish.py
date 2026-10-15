from harness import EditorTestCase


class UiPolish(EditorTestCase):
    def test_inspector_minimizes_and_is_remembered(self):
        b = self.browser
        b.select_asset('edit-sprite.png')
        b.click_selector('#inspectorToggle')
        b.wait_for("document.body.classList.contains('inspector-min')")
        self.assertLess(b.eval("document.querySelector('.inspector').getBoundingClientRect().width"), 60)
        self.assertTrue(b.eval("document.getElementById('saveButton').offsetParent === null"))
        b.screenshot('ui-inspector-min.png')
        b.reload(); b.wait_for("document.body.classList.contains('inspector-min')")
        b.click_selector('#inspectorToggle')
        b.wait_for("!document.body.classList.contains('inspector-min')")
        self.assertGreater(b.eval("document.querySelector('.inspector').getBoundingClientRect().width"), 200)

    def test_library_minimizes_too(self):
        b = self.browser
        b.click_selector('#libraryToggle')
        b.wait_for("document.body.classList.contains('library-min')")
        self.assertLess(b.eval("document.querySelector('.library').getBoundingClientRect().width"), 60)
        if not b.eval("document.body.classList.contains('inspector-min')"): b.click_selector('#inspectorToggle')
        self.assertGreater(b.eval("document.querySelector('.studio').getBoundingClientRect().width"), 1400)
        b.screenshot('ui-both-min.png')
        b.click_selector('#libraryToggle'); b.click_selector('#inspectorToggle')
        b.wait_for("!document.body.classList.contains('library-min') && !document.body.classList.contains('inspector-min')")
        self.assertGreater(b.eval("document.querySelector('.library').getBoundingClientRect().width"), 200)

    def test_double_click_checks_and_checked_come_first(self):
        b = self.browser
        b.eval("LabSelection.clear()")
        last = b.eval("[...document.querySelectorAll('#assetList .asset-card')].at(-1).dataset.key")
        b.eval(f"document.querySelector('#assetList .asset-card[data-key=\"{last}\"]').scrollIntoView()")
        x, y = b.center_of(f'#assetList .asset-card[data-key="{last}"] .asset-label')
        b.double_click(x, y)
        self.assertEqual(b.eval("LabSelection.keys()"), [last])
        b.eval("library()")
        b.wait_for(f"document.querySelector('#assetList .asset-card')?.dataset.key === '{last}'")
        b.double_click(*b.center_of(f'#assetList .asset-card[data-key="{last}"] .asset-label'))
        self.assertEqual(b.eval("LabSelection.keys()"), [])

    def test_ui_text_does_not_highlight(self):
        b = self.browser
        self.assertEqual(b.eval("getComputedStyle(document.body).userSelect"), 'none')
        self.assertEqual(b.eval("getComputedStyle(document.getElementById('search')).userSelect"), 'text')
        self.assertFalse(b.eval("[...document.querySelectorAll('#edMenubar [data-menu]')].some(m => m.textContent.trim() === 'Filter')"))
