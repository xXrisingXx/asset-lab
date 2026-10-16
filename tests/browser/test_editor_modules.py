"""The editor with every feature module loaded, as players get it (src/web/editor/*.js): it loads without errors, every
tool's options bar, the menubar, every menu and the panel dock fit at 1366 x 768 and 1920 x 1080, and every menu command
can run on a picture without throwing. Each module's own behaviour is proven in tests/browser/test_<module>*.py; the
core alone in test_core*.py. Screenshots go to tests/browser/output/modules-*.png."""
import json
import re
from harness import EditorTestCase

# From tests/browser/test_i18n.py: names that stay as they are, file names, and the overflow check.
KEEP = {'Asset Lab', 'ASSET LAB', 'Theme AI', 'THEME AI', 'Mitosis', 'MITOSIS', 'Steam', 'Ollama', 'OK', 'Atlas', 'Sprites', 'Mod', 'px',
        'English', 'Español', 'Português', 'Français', 'Deutsch'}
FILE = re.compile(r'\.(png|jpe?g|webp|ogg|wav|mp3|xml|zip|json|fnt|exe|awp|bin|atf)\b|[/\\]|^#[0-9a-f]{6}$', re.I)
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
SIZES = ((1366, 768), (1920, 1080))
MODULES = ('select', 'paint', 'fill', 'transform', 'adjust', 'filters', 'styles', 'layers', 'color')

# Elements of the menu row, the options bar and the dock that spill out of their box, and controls whose text is cut off.
SPILL_JS = r"""[...document.querySelectorAll('#menuRow *, .ed-optionsbar *, #edDock .ed-tabs *, #edDock .ed-layers-row *, #edDock .ed-layers-foot *')].filter(e => {
    if (!e.getClientRects().length || getComputedStyle(e).visibility === 'hidden' || e.closest('[hidden], .ed-popover, dialog')) return false;
    const r = e.getBoundingClientRect(), box = e.closest('#menuRow, .ed-optionsbar, #edDock');
    if (!box) return false; const p = box.getBoundingClientRect();
    return r.width > 0 && (r.right > p.right + 1 || r.left < p.left - 1 || r.bottom > p.bottom + 1 || r.top < p.top - 1); })
  .map(e => (e.id || (typeof e.className === 'string' ? e.className : e.tagName) || e.tagName) + ':' + (e.textContent || '').trim().slice(0, 24))"""
CLIPPED_JS = r"""[...document.querySelectorAll('#edMenubar .ed-menu-top, .ed-optionsbar label, .ed-optionsbar button, .ed-optionsbar select, #edDock .ed-tab, #edDock button')]
  .filter(e => e.getClientRects().length && !e.closest('[hidden]') && e.tagName !== 'SELECT' && e.scrollWidth > e.clientWidth + 1)
  .map(e => (e.id || e.className) + ':' + e.textContent.trim().slice(0, 24))"""
# Commands that leave the editor (files, downloads, the server, the clipboard prompt, the page layout) are not run blind.
SKIP_RUN = ('File/', 'View/Focus Editor', 'Edit/Copy', 'Edit/Cut', 'Edit/Paste', 'Edit/Copy Merged', 'Image/Color & Surface',
            'Help/', 'Window/')


class EditorModules(EditorTestCase):
    viewport = (1366, 768)

    def open_photo(self):
        b = self.browser
        b.select_asset('edit-photo.png')
        b.eval("Ed.view.fit(); true"); b.settle(3)
        return b

    def test_1_every_module_loads_without_errors(self):
        b = self.browser
        self.assertTrue(b.eval('await Ed.ready; true'))
        loaded = b.eval("performance.getEntriesByType('resource').map(r => r.name.split('/').slice(-2).join('/')).filter(n => n.startsWith('editor/'))")
        for m in MODULES: self.assertIn(f'editor/{m}.js', loaded)
        self.open_photo()
        self.assertEqual(b.eval('Ed.test.errors'), [])
        self.assertNoBrowserErrors(everything=True)

    def test_2_every_tool_options_bar_fits(self):
        b = self.open_photo()
        tools = b.eval("Ed.tools.list().map(t => t.id)")
        self.assertGreater(len(tools), 5)
        problems = []
        try:
            for w, h in SIZES:
                b.set_viewport(w, h); b.settle(3)
                for t in tools:
                    b.eval(f"Ed.setTool({json.dumps(t)}); true"); b.settle(2)
                    spill, clipped = b.eval(SPILL_JS), b.eval(CLIPPED_JS)
                    if spill or clipped: problems.append(f'{t} at {w}x{h}: spill {spill} clipped {clipped}')
                    if w == 1366: b.screenshot(f'modules-tool-{t}-{w}x{h}.png', clip=(0, 110, w, 130))
                b.eval("Ed.setTool('brush'); true"); b.settle(2)
                b.screenshot(f'modules-editor-{w}x{h}.png')
                self.assertLessEqual(b.eval('document.documentElement.scrollWidth'), w, f'no sideways scrolling at {w}x{h}')
        finally:
            b.set_viewport(*self.viewport)
        self.assertEqual(problems, [], 'options bars that do not fit:\n' + '\n'.join(problems))
        self.assertEqual(b.eval('Ed.test.errors'), [])

    def test_3_menus_and_dock_fit(self):
        b = self.open_photo()
        b.eval("Ed.layers.add({name: 'A layer with quite a long name to see how the row copes'}); Ed.layers.addMask(1, 'reveal'); true")
        problems = []
        try:
            for w, h in SIZES:
                b.set_viewport(w, h); b.settle(3)
                b.eval("Ed.panels.show('layers'); true"); b.settle(2)
                rows = b.eval("[...document.querySelectorAll('#edPanel-layers .ed-layer')].filter(r => { const q = r.getBoundingClientRect(); return q.height > 0 && q.top >= 0 && q.bottom <= innerHeight; }).length")
                if rows < 2: problems.append(f'only {rows} layer rows on screen at {w}x{h}')
                tabs = b.eval("[...document.querySelectorAll('#edDock .ed-tab')].filter(t => t.getClientRects().length).map(t => { const r = t.getBoundingClientRect(), d = document.getElementById('edDock').getBoundingClientRect(); return [t.textContent, r.right <= d.right + 1 && r.left >= d.left - 1 && r.height < 40]; })")
                problems += [f'dock tab {t!r} does not fit at {w}x{h}' for t, ok in tabs if not ok]
                for name in b.eval("[...document.querySelectorAll('#edMenubar [data-menu]')].map(m => m.dataset.menu)"):
                    b.click_selector(f'#edMenubar [data-menu="{name}"]')
                    b.wait_for("!!document.querySelector('.ed-menu')")
                    # Open every submenu in turn and check each menu is on screen (or scrolls).
                    subs = b.eval("[...document.querySelectorAll('.ed-menu')[0].querySelectorAll('.ed-menu-item[aria-haspopup]')].map(e => e.dataset.path)")
                    for sub in [None] + subs:
                        if sub:
                            b.eval(f"document.querySelector('.ed-menu .ed-menu-item[data-path={json.dumps(sub)}]').dispatchEvent(new PointerEvent('pointerenter'))")
                            b.wait_for("document.querySelectorAll('.ed-menu').length >= 2")
                        off = b.eval("""[...document.querySelectorAll('.ed-menu')].filter(m => { const r = m.getBoundingClientRect();
                            return r.left < -1 || r.right > innerWidth + 1 || r.top < -1 || (r.bottom > innerHeight + 1 && m.scrollHeight <= m.clientHeight + 1); }).length""")
                        if off: problems.append(f'menu {sub or name} runs off the window at {w}x{h}')
                        cut = b.eval("[...document.querySelectorAll('.ed-menu .ed-menu-item')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.dataset.path)")
                        problems += [f'cut-off menu item {p} at {w}x{h}' for p in cut]
                    if w == 1366: b.screenshot(f'modules-menu-{name}-{w}x{h}.png')
                    b.key('Escape'); b.key('Escape')
                    b.wait_for("!document.querySelector('.ed-menu')")
                empty = b.eval("Ed.menu.tree ? JSON.stringify(Ed.menu.tree()) : ''")
                self.assertNotIn('"children":[]', empty.replace(' ', ''), 'no empty submenus')
        finally:
            b.set_viewport(*self.viewport)
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_4_every_menu_command_runs_without_errors(self):
        b = self.open_photo()
        b.eval("Ed.layers.add({name: 'Upper'}); Ed.select.rect(40, 40, 120, 100); true")
        paths = [m['path'] for m in b.eval('Ed.menu.list()') if m['enabled'] and not m['path'].startswith(SKIP_RUN)]
        self.assertGreater(len(paths), 20)
        failed = []
        for p in paths:
            before = len(b.eval('Ed.test.errors'))
            mark = b.seq
            try:
                b.eval(f"window.__run = Promise.resolve(Ed.menu.run({json.dumps(p)})).catch(e => {{ console.error('menu run ' + e); }}); true")
                b.settle(3)
                # A command with a dialog: Cancel it the way a player would.
                for _ in range(3):
                    if not b.eval("!!document.querySelector('dialog[open]')"): break
                    b.key('Escape'); b.settle(2)
                b.eval("await Promise.race([window.__run, new Promise(r => setTimeout(r, 3000))]); true", timeout=10)
            except Exception as e:   # noqa: BLE001  (reported below with the command's name)
                failed.append(f'{p}: {e}')
            errs = b.eval('Ed.test.errors').__len__() - before
            if errs: failed.append(f'{p}: {b.eval("Ed.test.errors.slice(-1)[0].message")}')
            browser_errors = [e for e in b.errors_since(mark)]
            if browser_errors: failed.append(f'{p}: ' + b.error_report(browser_errors))
            if not b.eval('!!Ed.doc'): failed.append(f'{p}: closed the document'); self.open_photo()
        self._error_mark = b.seq
        self.assertEqual(failed, [], '\n'.join(failed))


class _Sweep:
    """Every tool's options bar, every menu and submenu, every dialog a menu command opens and every panel of the editor
    in another language: nothing visible left in English and nothing that does not fit. German has the longest words,
    French is second. A mixin (not a TestCase, so it does not run itself): the classes below add the language."""
    viewport = (1366, 768)
    open_on_start = False
    language = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            (cls.server.home / 'settings.json').write_text(json.dumps({'language': cls.language}), 'utf-8')
            cls.browser.open_lab(cls.server)
            cls.browser.eval('await LabI18n.ready; true')
        except BaseException:
            cls._shutdown()
            raise

    def setUp(self):
        super().setUp()
        if self.browser.eval("!!document.getElementById('themeDrawer') && !document.getElementById('themeDrawer').hidden"):
            self.browser.click_selector('#themeClose')

    def shot(self, name): self.browser.screenshot(f'modules-{self.language}-{name}.png')

    def english_left(self, root):
        """Visible English the dictionary does not cover, less names that stay as they are (test_i18n.py's rule)."""
        def kept(text):
            t = re.sub(r'^\[[\w-]+\] ', '', text).strip('✦ ')
            return t in KEEP or bool(re.fullmatch(r'Asset Lab [\d.]+\S*', t)) or bool(FILE.search(t)) or not re.search(r'[a-z]{3,}', t) or t.startswith('edit-')
        return sorted(t for t in self.browser.eval(f'LabI18n.untranslated({root})') if not kept(t))

    def assertFits(self, where):
        bad = self.browser.eval(OVERFLOW_JS)
        self.assertEqual(bad, [], f'Text that does not fit in {where} ({self.language}):\n  ' + '\n  '.join(bad))

    def left(self, root):
        return self.english_left(root)

    def open_editor(self):
        b = self.browser
        b.select_asset('edit-photo.png')
        b.eval("Ed.view.fit(); true"); b.settle(3)
        return b

    def test_a_tools(self):
        b = self.open_editor()
        problems = []
        for t in b.eval("Ed.tools.list().map(t => t.id)"):
            b.eval(f"Ed.setTool({json.dumps(t)}); true"); b.settle(2)
            left = self.left("document.querySelector('.ed-optionsbar')") + self.left("document.getElementById('edTools')")
            spill, clipped = b.eval(SPILL_JS), b.eval(CLIPPED_JS)
            if left or spill or clipped: problems.append(f'{t}: English {left} spill {spill} clipped {clipped}')
            if t in ('brush', 'move', 'gradient', 'magic-wand', 'type'): self.shot(f'tool-{t}')
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_b_menus(self):
        b = self.open_editor()
        b.eval("Ed.layers.add({name: 'Upper'}); Ed.select.rect(40, 40, 120, 100); true")
        problems = []
        for name in b.eval("[...document.querySelectorAll('#edMenubar [data-menu]')].map(m => m.dataset.menu)"):
            b.click_selector(f'#edMenubar [data-menu="{name}"]')
            b.wait_for("!!document.querySelector('.ed-menu')")
            subs = b.eval("[...document.querySelectorAll('.ed-menu')[0].querySelectorAll('.ed-menu-item[aria-haspopup]')].map(e => e.dataset.path)")
            for sub in [None] + subs:
                if sub:
                    b.eval(f"document.querySelector('.ed-menu .ed-menu-item[data-path={json.dumps(sub)}]').dispatchEvent(new PointerEvent('pointerenter'))")
                    b.wait_for("document.querySelectorAll('.ed-menu').length >= 2")
                for i in range(b.eval("document.querySelectorAll('.ed-menu').length")):
                    left = self.left(f"document.querySelectorAll('.ed-menu')[{i}]")
                    if left: problems.append(f'{sub or name}: {left}')
                cut = b.eval("[...document.querySelectorAll('.ed-menu .ed-menu-item')].filter(e => e.scrollWidth > e.clientWidth + 1 || e.getBoundingClientRect().right > innerWidth + 1).map(e => e.dataset.path)")
                problems += [f'cut off: {p}' for p in cut]
            b.key('Escape'); b.key('Escape')
            b.wait_for("!document.querySelector('.ed-menu')")
        self.assertEqual(sorted(set(problems)), [], '\n'.join(sorted(set(problems))))

    def test_c_dialogs(self):
        b = self.open_editor()
        b.eval("Ed.layers.add({name: 'Upper', kind: 'pixel'}); Ed.select.rect(40, 40, 120, 100); true")
        paths = [m['path'] for m in b.eval('Ed.menu.list()') if m['enabled'] and m['path'].endswith('...') and not m['path'].startswith(SKIP_RUN)]
        self.assertGreater(len(paths), 10)
        problems = []
        quiet = []
        for p in paths:
            # Every command starts from the same picture: the empty pixel layer 'Upper' active, a selection.
            b.eval("const i = Ed.doc.layers.findIndex(l => l.name === 'Upper'); if (i >= 0) Ed.layers.setActive(i, 'pixels'); if (!Ed.select.hasSelection()) Ed.select.rect(40, 40, 120, 100); true")
            b.eval(f"window.__run = Promise.resolve(Ed.menu.run({json.dumps(p)})).catch(() => {{}}); true")
            try: b.wait_for("!!document.querySelector('dialog[open]')", timeout=3)
            except TimeoutError:   # a command that asks nothing here (or only a native confirm)
                quiet.append(p); continue
            b.settle(3)
            left = self.left("document.querySelector('dialog[open]')")
            box = b.eval("(() => { const r = document.querySelector('dialog[open]').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
            over = b.eval(OVERFLOW_JS)
            if left: problems.append(f'{p}: English {left}')
            if box[0] < -1 or box[1] < -1 or box[2] > 1367 or box[3] > 769: problems.append(f'{p}: dialog {box} outside 1366x768')
            if over: problems.append(f'{p}: {over}')
            if p in ('Layer/Layer Style/Blending Options...', 'Image/Adjustments/Curves...', 'Filter/Blur/Gaussian Blur...', 'Edit/Fill...'): self.shot('dialog-' + p.split('/')[-1].strip('.').replace(' ', '-'))
            for _ in range(3):
                if not b.eval("!!document.querySelector('dialog[open]')"): break
                b.key('Escape'); b.settle(2)
            b.eval("await Promise.race([window.__run, new Promise(r => setTimeout(r, 3000))]); true", timeout=10)
        print(f'\n[{self.language}] commands with "..." that opened no dialog here: {quiet}')
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_d_panels(self):
        b = self.open_editor()
        problems = []
        for panel in b.eval("[...document.querySelectorAll('#edDock .ed-tab')].map(t => t.dataset.panel)"):
            b.click_selector(f'#edDock .ed-tab[data-panel="{panel}"]'); b.settle(3)
            left = self.left("document.getElementById('edDock')")
            if left: problems.append(f'{panel}: {left}')
            self.shot(f'panel-{panel}')
        b.click_selector('#edDock .ed-tab[data-panel="layers"]')
        self.assertFits('the dock')
        self.assertEqual(problems, [], '\n'.join(problems))



class EditorGerman(_Sweep, EditorTestCase):
    language = 'de'


class EditorFrench(_Sweep, EditorTestCase):
    language = 'fr'
