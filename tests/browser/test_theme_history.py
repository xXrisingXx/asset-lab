import time
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
        deadline = time.time() + 5   # the chat is saved to this build a moment after the answer
        while time.time() < deadline and not any('help' in c['title'] or c['count'] >= 2 for c in self.server.get('/api/chats')['chats']): time.sleep(0.1)
        before =b.eval("[...document.querySelectorAll('#themeLog .theme-bubble')].map(e => e.className.includes('user') ? 'U:' + e.textContent : 'B:' + e.textContent.slice(0, 40))")
        self.assertIn('U:help', before)
        b.reload()
        b.wait_for("!document.getElementById('themeDrawer').hidden")   # Theme AI reopens by itself
        b.wait_for(f"document.querySelectorAll('#themeLog .theme-bubble').length === {len(before)}")   # the saved chat loads from this build
        after =b.eval("[...document.querySelectorAll('#themeLog .theme-bubble')].map(e => e.className.includes('user') ? 'U:' + e.textContent : 'B:' + e.textContent.slice(0, 40))")
        self.assertEqual(after, before)

    def test_a_request_cut_off_by_a_reload_says_so_and_new_chat_keeps_it(self):
        b = self.browser
        # A chat from before chats were saved per build moves into this build's chats.
        b.eval("localStorage.setItem('lab-theme-chat', JSON.stringify({entries: [{t: 'user', text: 'make it gold'}], history: []}))")
        b.reload(); self.open_theme()
        b.wait_for("[...document.querySelectorAll('#themeLog .theme-bubble.error')].some(e => /interrupted by a reload/.test(e.textContent))")
        b.wait_for("localStorage.getItem('lab-theme-chat') === null && [...document.querySelectorAll('#themeChats option')].some(o => o.textContent === 'make it gold')")
        gold = b.eval("document.getElementById('themeChats').value")
        b.click_selector('#themeNewChat')
        b.wait_for("document.querySelectorAll('#themeLog .theme-bubble').length === 1")
        self.assertNotEqual(b.eval("document.getElementById('themeChats').value"), gold)
        # The old chat is still there: picking it brings the conversation back.
        b.eval(f"(()=>{{const s=document.getElementById('themeChats');s.value={gold!r};s.dispatchEvent(new Event('change'));}})()")
        b.wait_for("[...document.querySelectorAll('#themeLog .theme-bubble.user')].some(e => e.textContent === 'make it gold')")

    def test_a_drawing_reconnects_to_its_job(self):
        b = self.browser
        # A saved reply with a drawing job the server no longer knows: the chat says so instead of breaking.
        entry = "{t: 'bot', text: 'Redraw', prompt: 'spooky potion', keys: null, jobs: [{id: 'gone123', keys: null, label: ''}], result: {plan: {reply: 'Redraw', operations: [], ui_colors: {}, hide: []}, source: 'design', note: '', preview: null, design: {available: true, prompt: 'spooky potion', targets: [{key: 'x', name: 'potion'}]}}}"
        b.eval(f"localStorage.setItem('lab-theme-chat', JSON.stringify({{entries: [{{t: 'user', text: 'spooky potion'}}, {entry}], history: []}}))")
        b.reload(); self.open_theme()
        b.wait_for("[...document.querySelectorAll('#themeLog .theme-note')].some(e => /no longer available/.test(e.textContent))")
        self.assertTrue(b.eval("[...document.querySelectorAll('#themeLog .theme-actions')].every(e => e.hidden || e.closest('[hidden]'))"))
        self.assertNoBrowserErrors(allow=('design-job',))
