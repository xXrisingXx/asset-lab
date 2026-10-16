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
        b.wait_for("localStorage.getItem('lab-theme-chat') === null && document.querySelector('#themeChats .chat-pick-title').textContent === 'make it gold'")
        b.click_selector('#themeNewChat')
        b.wait_for("document.querySelectorAll('#themeLog .theme-bubble').length === 1")
        self.assertEqual(b.eval("document.querySelector('#themeChats .chat-pick-title').textContent"), 'New chat')
        # The old chat is still there: picking it from the list brings the conversation back.
        b.click_selector('#themeChats')
        b.wait_for("!document.getElementById('themeChatMenu').hidden")
        self.assertEqual(b.eval("[...document.querySelectorAll('#themeChatMenu .chat-row b')].map(e => e.textContent)"), ['New chat', 'make it gold'])
        b.eval("[...document.querySelectorAll('#themeChatMenu .chat-row')].find(r => r.textContent.includes('make it gold')).click()")
        b.wait_for("[...document.querySelectorAll('#themeLog .theme-bubble.user')].some(e => e.textContent === 'make it gold')")
        self.assertTrue(b.eval("document.getElementById('themeChatMenu').hidden"))

    def test_the_chat_list_renames_searches_and_deletes(self):
        b = self.browser
        for i, words in enumerate(['make it gold', 'spooky menu', 'neon buttons', 'ice panels', 'lava cells', 'pastel icons']):
            self.server.post('/api/chat-save', {'id': f'{i:016x}', 'title': words, 'entries': [{'t': 'user', 'text': words}, {'t': 'bot', 'text': 'ok'}], 'history': []})
        b.reload(); self.open_theme()
        b.wait_for("document.querySelector('#themeChats .chat-pick-count').textContent === '6 chats'")
        b.click_selector('#themeChats')
        b.wait_for("!document.getElementById('themeChatMenu').hidden")
        self.assertEqual(b.eval("document.querySelectorAll('#themeChatMenu .chat-row').length"), 6)
        self.assertIn('1 message', b.eval("document.querySelector('#themeChatMenu .chat-row small').textContent"))
        # Search: shown once there are a few chats.
        self.assertFalse(b.eval("document.getElementById('themeChatSearch').hidden"))
        b.set_value('#themeChatSearch', 'SPOOKY')
        b.eval("document.getElementById('themeChatSearch').dispatchEvent(new Event('input'))")
        self.assertEqual(b.eval("[...document.querySelectorAll('#themeChatMenu .chat-row b')].map(e => e.textContent)"), ['spooky menu'])
        b.set_value('#themeChatSearch', 'nothing like this')
        b.eval("document.getElementById('themeChatSearch').dispatchEvent(new Event('input'))")
        self.assertFalse(b.eval("document.querySelector('#themeChatMenu .chat-none').hidden"))
        b.set_value('#themeChatSearch', '')
        b.eval("document.getElementById('themeChatSearch').dispatchEvent(new Event('input'))")
        # Rename in place: the name sticks, in the list, on the button and on the lab.
        b.eval("[...document.querySelectorAll('#themeChatMenu .chat-row')].find(r => r.textContent.includes('neon buttons')).querySelector('[data-act=rename]').click()")
        b.wait_for("document.activeElement && document.activeElement.classList.contains('chat-rename')")
        b.eval("(()=>{const box=document.activeElement;box.value='Neon v2';box.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()")
        b.wait_for("[...document.querySelectorAll('#themeChatMenu .chat-row b')].some(e => e.textContent === 'Neon v2')")
        self.assertIn('Neon v2', [c['title'] for c in self.server.get('/api/chats')['chats']])
        # Escape cancels a rename and leaves the list open.
        b.eval("document.querySelector('#themeChatMenu .chat-row [data-act=rename]').click()")
        b.wait_for("document.activeElement && document.activeElement.classList.contains('chat-rename')")
        b.eval("(()=>{const box=document.activeElement;box.value='nope';box.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));})()")
        self.assertNotIn('nope', b.eval("document.getElementById('themeChatMenu').textContent"))
        self.assertFalse(b.eval("document.getElementById('themeChatMenu').hidden"))
        # Double-clicking the open chat's name renames it.
        b.eval("document.getElementById('themeChats').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
        b.wait_for("document.activeElement && document.activeElement.classList.contains('chat-rename')")
        b.eval("(()=>{const box=document.activeElement;box.value='Open one';box.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()")
        b.wait_for("document.querySelector('#themeChats .chat-pick-title').textContent === 'Open one'")
        # Delete from the list (after confirming).
        b.eval("[...document.querySelectorAll('#themeChatMenu .chat-row')].find(r => r.textContent.includes('lava cells')).querySelector('[data-act=delete]').click()")
        b.wait_for("document.getElementById('dialog').open")
        b.click_selector('#dialogAccept')
        b.wait_for("![...document.querySelectorAll('#themeChatMenu .chat-row b')].some(e => e.textContent === 'lava cells')")
        self.assertEqual(len(self.server.get('/api/chats')['chats']), 5)
        # Escape closes the list; Theme AI stays open.
        b.eval("document.getElementById('themeChatMenu').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        self.assertTrue(b.eval("document.getElementById('themeChatMenu').hidden"))
        self.assertFalse(b.eval("document.getElementById('themeDrawer').hidden"))
        self.assertNoBrowserErrors()

    def test_a_drawing_reconnects_to_its_job(self):
        b = self.browser
        # A saved reply with a drawing job the server no longer knows: the chat says so instead of breaking.
        entry = "{t: 'bot', text: 'Redraw', prompt: 'spooky potion', keys: null, jobs: [{id: 'gone123', keys: null, label: ''}], result: {plan: {reply: 'Redraw', operations: [], ui_colors: {}, hide: []}, source: 'design', note: '', preview: null, design: {available: true, prompt: 'spooky potion', targets: [{key: 'x', name: 'potion'}]}}}"
        b.eval(f"localStorage.setItem('lab-theme-chat', JSON.stringify({{entries: [{{t: 'user', text: 'spooky potion'}}, {entry}], history: []}}))")
        b.reload(); self.open_theme()
        b.wait_for("[...document.querySelectorAll('#themeLog .theme-note')].some(e => /no longer available/.test(e.textContent))")
        self.assertTrue(b.eval("[...document.querySelectorAll('#themeLog .theme-actions')].every(e => e.hidden || e.closest('[hidden]'))"))
        self.assertNoBrowserErrors(allow=('design-job',))
