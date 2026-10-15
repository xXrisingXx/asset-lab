from harness import EditorTestCase


class ThemeChatHistory(EditorTestCase):
    def open_theme(self):
        b = self.browser
        if not b.eval("document.body.classList.contains('theme-on')"): b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden")

    def ask(self, text):
        b = self.browser
        b.set_value('#themeInput', text)
        b.eval("document.querySelector('.theme-form').requestSubmit()")
        b.wait_for("!document.getElementById('themeInput').disabled", timeout=60)

    def test_the_chat_comes_back_after_a_reload(self):
        b = self.browser
        b.eval("localStorage.removeItem('lab-theme-chat')"); b.reload()
        self.open_theme()
        self.ask('help')
        before = b.eval("[...document.querySelectorAll('#themeLog .theme-bubble')].map(e => e.className.includes('user') ? 'U:' + e.textContent : 'B:' + e.textContent.slice(0, 40))")
        self.assertIn('U:help', before)
        b.reload()
        b.wait_for("!document.getElementById('themeDrawer').hidden")   # Theme AI reopens by itself
        after = b.eval("[...document.querySelectorAll('#themeLog .theme-bubble')].map(e => e.className.includes('user') ? 'U:' + e.textContent : 'B:' + e.textContent.slice(0, 40))")
        self.assertEqual(after, before)

    def test_a_request_cut_off_by_a_reload_says_so_and_new_chat_clears(self):
        b = self.browser
        b.eval("localStorage.setItem('lab-theme-chat', JSON.stringify({entries: [{t: 'user', text: 'make it gold'}], history: []}))")
        b.reload(); self.open_theme()
        self.assertTrue(b.eval("[...document.querySelectorAll('#themeLog .theme-bubble.error')].some(e => /interrupted by a reload/.test(e.textContent))"))
        b.click_selector('#themeNewChat')
        self.assertEqual(b.eval("document.querySelectorAll('#themeLog .theme-bubble').length"), 1)
        self.assertEqual(b.eval("JSON.parse(localStorage.getItem('lab-theme-chat')).entries.length"), 0)

    def test_a_drawing_reconnects_to_its_job(self):
        b = self.browser
        # A saved reply with a drawing job the server no longer knows: the chat says so instead of breaking.
        entry = "{t: 'bot', text: 'Redraw', prompt: 'spooky potion', keys: null, jobs: [{id: 'gone123', keys: null, label: ''}], result: {plan: {reply: 'Redraw', operations: [], ui_colors: {}, hide: []}, source: 'design', note: '', preview: null, design: {available: true, prompt: 'spooky potion', targets: [{key: 'x', name: 'potion'}]}}}"
        b.eval(f"localStorage.setItem('lab-theme-chat', JSON.stringify({{entries: [{{t: 'user', text: 'spooky potion'}}, {entry}], history: []}}))")
        b.reload(); self.open_theme()
        b.wait_for("[...document.querySelectorAll('#themeLog .theme-note')].some(e => /no longer available/.test(e.textContent))")
        self.assertTrue(b.eval("[...document.querySelectorAll('#themeLog .theme-actions')].every(e => e.hidden || e.closest('[hidden]'))"))
        self.assertNoBrowserErrors(allow=('design-job',))
