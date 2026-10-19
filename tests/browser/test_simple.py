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
        # Simple's own library: pictures by friendly name, by category; the full file list is not shown.
        b.wait_for("document.querySelectorAll('#simpleGrid .simple-card').length > 0")
        self.assertFalse(b.eval("document.getElementById('assetList').checkVisibility()"))
        self.assertIn('All', b.eval("document.getElementById('simpleCategories').textContent"))
        self.assertIn('whole game', b.eval("document.querySelector('.theme-scope').textContent"))   # what a request changes, always said
        before = b.eval("S.asset?.key || ''")
        b.click_selector('#simpleGrid .simple-card')
        b.wait_for("LabSelection.size() >= 1")                                          # every copy of that picture
        self.assertEqual(b.eval("S.asset?.key || ''"), before)                        # checked, not opened in the hidden editor
        self.assertIn('checked picture', b.eval("document.querySelector('.theme-scope').textContent"))
        self.assertIn('Checked (1)', b.eval("document.getElementById('simpleChecked').textContent"))   # in sight, with an ✕
        b.click_selector('#simpleChecked .simple-chip .quiet')                           # unchecked from there
        b.wait_for("LabSelection.size() === 0")
        b.click_selector('#simpleGrid .simple-card')
        b.wait_for("LabSelection.size() >= 1")
        b.click_selector('#simpleGrid .simple-card')                                     # again: unchecked
        b.wait_for("LabSelection.size() === 0")
        b.click_selector('#viewSwitch [data-view="advanced"]')
        self.assertEqual(b.eval("getComputedStyle(document.querySelector('.studio')).display"), 'flex')
        self.assertEqual(b.eval("localStorage.getItem('lab-layout')"), 'advanced')

    def test_everyone_starts_in_simple_and_a_switch_is_remembered(self):
        b = self.browser
        self.assertFalse(b.eval("document.body.classList.contains('lab-simple')"))     # this browser chose Advanced
        b.eval("sessionStorage.setItem('tour-test','1'); localStorage.removeItem('lab-tour-done'); localStorage.removeItem('lab-layout'); true")
        b.reload()
        try:
            b.wait_for("document.body.classList.contains('lab-simple')")
            b.wait_for("!!document.querySelector('.tour-box')", timeout=10)          # the short tour of Simple
            titles = []
            for _ in range(10):
                if not b.eval("!!document.querySelector('.tour-box')"): break
                titles.append(b.eval("document.querySelector('.tour-box h3').textContent"))
                b.click_selector('.tour-box .primary')
            self.assertEqual(titles, ['Welcome to Asset Lab', 'Check what to change', 'Right-click for more', 'Describe it', 'Presets', 'Bring your own picture', 'Put it in the game',
                                      'Want more control?', 'You are ready'])
        finally:
            b.eval("sessionStorage.removeItem('tour-test'); localStorage.setItem('lab-tour-done','1'); localStorage.setItem('lab-layout','advanced'); true")
            b.reload()

    def test_the_tour_shows_examples_and_the_right_click_menu(self):
        b = self.browser
        b.eval("LabTour.start(); true")
        for _ in range(12):
            if b.eval("document.querySelector('.tour-box h3').textContent") == 'Right-click for more': break
            b.click_selector('.tour-box .primary')
        b.wait_for("!!document.querySelector('.tour-box .tour-demo-menu')")   # inside the card: it never covers its words
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
        self.assertEqual(labels, ['Colors', 'Whole theme', 'Background', 'Redraw something'])
        tools = b.eval("[...document.querySelectorAll('.theme-inputbar .theme-tools button')].map(x => x.getAttribute('aria-label'))")
        self.assertEqual(tools, ['Your picture', 'Text & fonts'])                  # the tools are icons in the box
        self.assertTrue(b.eval("document.querySelector('.theme-toolbar #themePresetsButton').checkVisibility()"))   # Presets, labelled, on the starters' line
        self.assertEqual(b.eval("[...document.querySelectorAll('.theme-dock-actions button')].slice(-2).map(x => x.textContent)"), ['Undo theme', 'Start fresh'])
        self.assertTrue(b.eval("!!document.querySelector('.theme-inputbar #themeInput') && !!document.querySelector('.theme-inputbar #themeSend')"))
        b.click_selector('#themeStarters button')
        self.assertEqual(b.eval("document.getElementById('themeInput').value"), 'Make the buttons and panels ')
        b.eval("document.getElementById('themeSend').click(); true")                   # the starter's words alone are not sent
        b.wait_for("document.getElementById('toast')?.textContent.includes('Finish the sentence')")
        self.assertEqual(b.eval("document.querySelectorAll('#themeLog .theme-bubble.user').length"), 0)
        self.assertFalse(b.eval("!!document.getElementById('gameColorsButton')"))
        self.assertTrue(b.eval("!!document.querySelector('#themeDrawer .theme-head #themeChats')"))   # the chats on the title line
        for folded in ('themeAuto', 'themeAccelerate', 'themeModel'):          # under More
            self.assertFalse(b.eval(f"document.getElementById('{folded}').checkVisibility()"), folded)
        b.click_selector('#themeMore summary')
        for folded in ('themeAuto', 'themeAccelerate', 'themeModel'):
            self.assertTrue(b.eval(f"document.getElementById('{folded}').checkVisibility()"), folded)
        self.assertTrue(b.eval("!!document.querySelector('#themeMore [data-draw=on]')"))
        b.click_selector('#themeMore summary')
        b.eval("document.getElementById('themeInput').value=''; true")
        b.click_selector('#themeClose')


class PicturesAndPresets(EditorTestCase):
    """A preset or a picture the player adds: recolored ("make this dark blue") and put on the game art it goes on."""

    def open_presets(self):
        b = self.browser
        b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden")
        b.click_selector('#themePresetsButton')
        b.wait_for("document.getElementById('presetsDialog').open && !!document.querySelector('#presetsDialog .pv-card')")

    def staged(self): return {x['key'] for x in self.server.get('/api/status')['staged']}

    def big(self, group, name=None):
        """A preset of a group seen big (the first one, or by name)."""
        b = self.browser
        b.eval(f"[...document.querySelectorAll('#presetsDialog .pv-groups button')].find(x => x.dataset.group === {group!r}).click(); true")
        b.eval("[...document.querySelectorAll('#presetsDialog .pv-card')].find(x => %s).click(); true" % (f"x.textContent === {name!r}" if name else 'true'))
        b.wait_for("!!document.getElementById('presetUseNow')")

    def test_a_preset_is_seen_big_then_used_recolored_and_put_in_the_game(self):
        # Seen big, recolored there (a click or typed), then Use now puts it in your changes: no chat in between.
        b = self.browser
        self.open_presets()
        groups = b.eval("[...document.querySelectorAll('#presetsDialog .pv-groups button')].map(x => x.dataset.group)")
        self.assertEqual(groups[0], 'Backgrounds')
        self.assertIn('Viruses', groups)
        self.big('Viruses', 'Purple virus')
        self.assertEqual(b.eval("document.querySelectorAll('#presetsDialog .pv-floor').length"), 2)   # on the game's two floors
        floor = "document.querySelector('#presetsDialog .pv-floor img')"
        self.assertNotIn('color=', b.eval(f"{floor}.src"))                                        # a preset starts in its own colors
        b.eval("[...document.querySelectorAll('#presetCustom .picture-chip')].find(x => x.title === 'Dark blue').click(); true")
        b.wait_for(f"{floor}.src.includes('color=%231e3a8a')")
        b.set_value('#presetWords', 'gold')                                                       # typed: recolored right here
        b.click_selector('#presetChange')
        b.wait_for(f"!{floor}.src.includes('color=%231e3a8a') && {floor}.src.includes('color=')")
        self.assertEqual(b.eval("document.getElementById('presetWords').value"), '')
        b.eval("[...document.querySelectorAll('#presetsDialog .pv-actions button')].find(x => x.textContent === 'Back').click(); true")
        before = self.staged()
        self.big('Backgrounds')                                                                   # (this test game has backgrounds, no virus)
        b.click_selector('#presetUseNow')
        b.wait_for("!!document.querySelector('#presetsDialog .pv-grid')", timeout=20)              # in, and back to the list
        b.wait_for("document.getElementById('toast')?.textContent.includes('is in your changes')")
        self.assertTrue(self.staged() - before)
        b.eval("document.getElementById('presetsDialog').close(); true")
        b.click_selector('#themeClose')

    def test_typing_more_than_a_color_asks_theme_ai(self):
        b = self.browser
        self.open_presets()
        b.eval("""window.__asked=[];window.__realAsk=LabThemeChat.ask;LabThemeChat.ask=(t,o)=>{__asked.push([t,o]);return Promise.resolve();};true""")
        try:
            self.big('Viruses', 'Purple virus')
            b.set_value('#presetWords', 'make it a pirate flag with a skull')
            b.click_selector('#presetChange')
            b.wait_for("window.__asked.length === 1")
            self.assertEqual(b.eval("__asked[0][0]"), 'make it a pirate flag with a skull')
            self.assertEqual(b.eval("__asked[0][1].picture"), 'preset:purple-virus')
            self.assertFalse(b.eval("document.getElementById('presetsDialog').open"))
        finally:
            b.eval("LabThemeChat.ask=window.__realAsk; document.getElementById('presetsDialog').close(); true")
            b.click_selector('#themeClose')

    def test_right_click_exports_a_preset_in_its_colors(self):
        # Requests for assets: right-click, Export, and the PNG is ready to send. Full size, named, in its new colors.
        b = self.browser
        b.eval("""window.__saved=[];HTMLAnchorElement.prototype.click=function(){if(this.download)__saved.push([this.getAttribute('href'),this.download]);};true""")
        try:
            self.open_presets()
            b.eval("[...document.querySelectorAll('#presetsDialog .pv-groups button')].find(x => x.dataset.group === 'Viruses').click(); true")
            b.eval("""const c=[...document.querySelectorAll('#presetsDialog .pv-card')].find(x => x.textContent === 'Purple virus');
                      c.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:40,clientY:40}));true""")
            b.wait_for("!document.querySelector('.preset-menu').hidden")
            self.assertEqual(b.eval("document.querySelector('.preset-menu').parentNode.id"), 'presetsDialog')   # not behind the window
            b.eval("[...document.querySelectorAll('.preset-menu button')].find(x => x.textContent === 'Export picture (PNG)').click(); true")
            href, name = b.eval("__saved.at(-1)")
            self.assertEqual((href.split('&v=')[0], name), ('/api/picture?id=preset%3Apurple-virus', 'Purple-virus.png'))
            self.big('Viruses', 'Purple virus')
            b.eval("[...document.querySelectorAll('#presetCustom .picture-chip')].find(x => x.title === 'Dark blue').click(); true")
            img = "document.querySelector('#presetsDialog .pv-floor img')"
            b.wait_for(f"{img}.src.includes('color=%231e3a8a')")
            b.eval(f"""{img}.dispatchEvent(new MouseEvent('contextmenu',{{bubbles:true,cancelable:true,clientX:40,clientY:40}}));true""")
            b.wait_for("!document.querySelector('.preset-menu').hidden")
            self.assertTrue(b.eval("document.getElementById('contextMenu').hidden"))   # only the Export menu
            b.eval("document.querySelector('.preset-menu button').click(); true")
            href, name = b.eval("__saved.at(-1)")
            self.assertEqual(name, 'Purple-virus-1e3a8a.png')
            self.assertIn('color=%231e3a8a', href)
            self.assertNotIn('small', href)
            b.eval("document.getElementById('presetsDialog').close(); true")
            b.click_selector('#themeClose')
        finally:
            b.eval("delete HTMLAnchorElement.prototype.click; true")

    def test_picks_from_each_kind_are_used_all_at_once(self):
        # Pick keeps a preset (one of each kind); Use all puts every pick in your changes at once.
        b = self.browser
        self.open_presets()
        try:
            groups = b.eval("[...document.querySelectorAll('#presetsDialog .pv-groups button')].map(x => x.dataset.group)")
            for g in groups[:3]:
                self.big(g)
                b.click_selector('#presetUse')
                b.wait_for("!!document.getElementById('presetsTray')")
            self.assertEqual(b.eval("document.querySelectorAll('#presetsTray .pv-pick').length"), len(groups[:3]))
            b.eval("document.querySelector('#presetsDialog .pv-card').click(); true")      # the same one again: unpicked
            b.wait_for("document.getElementById('presetUse').textContent === 'Picked ✓'")
            b.click_selector('#presetUse')
            b.wait_for("document.querySelectorAll('#presetsTray .pv-pick').length === %d" % (len(groups[:3]) - 1))
            names = b.eval("[...document.querySelectorAll('#presetsTray .pv-pick > span')].map(x => x.textContent)")
            b.click_selector('#presetsUseAll')
            b.wait_for("!document.getElementById('presetsDialog').open")
            # This test game has only a menu background: the background goes in, the other picks are named as not.
            b.wait_for("document.getElementById('toast')?.classList.contains('error')", timeout=30)
            toast = b.eval("document.getElementById('toast').textContent")
            self.assertNotIn(names[0], toast)
            self.assertTrue(all(n in toast for n in names[1:]), toast)
            b.clear_errors()   # the 400s for the picks with nowhere to go
        finally:
            b.eval("document.getElementById('presetsDialog').close(); true")
            b.click_selector('#themeClose')

    def test_a_background_for_each_place_and_food_by_number(self):
        # Where it goes: one background on the menu and another on the dark-mode floor; one covering both takes over.
        b = self.browser
        places = self.server.get('/api/presets')['places']
        self.open_presets()
        try:
            spots = [k for k, *_ in places['background']]
            if {'set:menu', 'set:arena-dark', 'set:everywhere'} <= set(spots):
                cards = b.eval("[...document.querySelectorAll('#presetsDialog .pv-card')].slice(0,3).map(x => x.textContent)")
                for name, spot in zip(cards, ('set:menu', 'set:arena-dark', 'set:everywhere')):
                    self.big('Backgrounds', name)
                    b.eval(f"document.querySelector('#presetPlaces [data-place=\"{spot}\"]').click(); true")
                    b.click_selector('#presetUse')
                    b.wait_for("!!document.getElementById('presetsTray')")
                    if spot == 'set:arena-dark':
                        self.assertEqual(b.eval("document.querySelectorAll('#presetsTray .pv-pick').length"), 2)   # both kept
                self.assertEqual(b.eval("document.querySelectorAll('#presetsTray .pv-pick').length"), 1)           # everywhere took over
                self.assertIn('everywhere', b.eval("document.querySelector('#presetsTray .pv-pick b').textContent"))
            food = [k for k, *_ in places['food']]
            if len(food) > 1:
                self.big('Food')
                labels = b.eval("[...document.querySelectorAll('#presetPlaces .pv-place b')].map(x => x.textContent)")
                self.assertEqual(labels[0], 'All foods')
                self.assertEqual(labels[1:], [f'Food {i + 1}' for i in range(len(food) - 1)])
                self.assertEqual(b.eval("document.querySelector('#presetPlaces .active').dataset.place"), 'set:food:all')
        finally:
            b.eval("document.getElementById('presetsDialog').close(); true")
            b.click_selector('#themeClose')

    def test_a_category_exports_as_a_zip_or_a_folder(self):
        b = self.browser
        self.open_presets()
        b.eval("""window.__saved=[];HTMLAnchorElement.prototype.click=function(){if(this.download)__saved.push([this.getAttribute('href'),this.download]);};
                  window.__folders=[];if(!window.__realFetch)window.__realFetch=window.fetch;window.fetch=(u,o)=>{const p=new URL(String(u),location.href).pathname;
                  if(p==='/api/presets-folder'){__folders.push(JSON.parse(o.body));return Promise.resolve(new Response(JSON.stringify({path:'x',name:'Backgrounds-Space'}),{headers:{'Content-Type':'application/json'}}));}
                  return window.__realFetch(u,o);};true""")
        try:
            cat = b.eval("[...document.querySelectorAll('#presetsDialog .pv-cats button')].map(x => x.dataset.cat).filter(Boolean)[0]")
            b.eval(f"""[...document.querySelectorAll('#presetsDialog .pv-cats button')].find(x => x.dataset.cat === {cat!r})
                      .dispatchEvent(new MouseEvent('contextmenu',{{bubbles:true,cancelable:true,clientX:200,clientY:200}}));true""")
            b.wait_for("!document.querySelector('.preset-menu').hidden")
            labels = b.eval("[...document.querySelectorAll('.preset-menu button')].map(x => x.textContent)")
            self.assertTrue(labels[0].endswith('as a zip') and labels[1].endswith('as a folder'))
            self.assertTrue(all(x.endswith('as game files ▸') for x in labels[2:]) and len(labels) <= 3)   # backgrounds: the game's own files too
            b.eval("document.querySelectorAll('.preset-menu button')[0].click(); true")
            href, name = b.eval("__saved.at(-1)")
            self.assertIn('/api/presets-export?', href)
            self.assertIn('category=', href)
            self.assertTrue(name.endswith('.zip'))
            zipped = b.eval(f"fetch({href!r}).then(r => r.headers.get('Content-Type'))")
            self.assertEqual(zipped, 'application/zip')
            b.eval(f"""[...document.querySelectorAll('#presetsDialog .pv-cats button')].find(x => x.dataset.cat === {cat!r})
                      .dispatchEvent(new MouseEvent('contextmenu',{{bubbles:true,cancelable:true,clientX:200,clientY:200}}));true""")
            b.wait_for("!document.querySelector('.preset-menu').hidden")
            b.eval("document.querySelectorAll('.preset-menu button')[1].click(); true")
            b.wait_for("window.__folders.length === 1")
            self.assertEqual(b.eval("__folders[0].category"), cat)
        finally:
            b.eval("delete HTMLAnchorElement.prototype.click; window.fetch=window.__realFetch; document.getElementById('presetsDialog').close(); true")
            b.click_selector('#themeClose')

    def test_presets_shows_how_it_works_the_first_time(self):
        b = self.browser
        b.eval("localStorage.removeItem('lab-presets-tour-done'); true")
        self.open_presets()
        b.wait_for("!!document.getElementById('presetsTour')")
        steps = 0
        while b.eval("!!document.getElementById('presetsTour')") and steps < 12:
            if b.eval("!!document.querySelector('#presetsTour .pv-coach-menu')"):           # the right-click steps show the menu
                self.assertIn('Export', b.eval("document.querySelector('#presetsTour .pv-coach-menu').textContent"))
            b.eval("[...document.querySelectorAll('#presetsTour .pv-coach-actions button')].at(-1).click(); true")
            steps += 1
        self.assertEqual(steps, 8)
        self.assertEqual(b.eval("localStorage.getItem('lab-presets-tour-done')"), '1')
        b.eval("document.getElementById('presetsDialog').close(); true")
        b.click_selector('#themePresetsButton')
        b.wait_for("document.getElementById('presetsDialog').open && !!document.querySelector('#presetsDialog .pv-card')")
        self.assertFalse(b.eval("!!document.getElementById('presetsTour')"))               # once
        b.eval("document.querySelector('#presetsDialog .pv-help').click(); true")           # and again from ?
        self.assertTrue(b.eval("!!document.getElementById('presetsTour')"))
        b.eval("document.querySelector('#presetsTour .pv-coach-actions .quiet').click(); document.getElementById('presetsDialog').close(); true")
        b.click_selector('#themeClose')

    def test_a_dropped_picture_and_make_this_red(self):
        b = self.browser
        b.click_selector('#themeButton')
        b.wait_for("!document.getElementById('themeDrawer').hidden")
        b.eval("""(async()=>{const blob=await (await fetch('/api/picture?id=preset%3Apurple-virus')).blob();
            const dt=new DataTransfer();dt.items.add(new File([blob],'mine.png',{type:'image/png'}));
            document.getElementById('themeDrawer').dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));})()""")
        b.wait_for("!document.getElementById('themeAttached').hidden")
        b.set_value('#themeInput', 'make this red')
        b.click_selector('#themeSend')
        b.wait_for("!!document.querySelector('#themeLog .picture-card')", timeout=20)
        self.assertTrue(b.eval("document.getElementById('themeAttached').hidden"))           # sent with the request
        self.assertTrue(b.eval("!!document.querySelector('#themeLog .theme-bubble.user .theme-thumb')"))
        self.assertIn('color=%23e53935', b.eval("document.querySelector('#themeLog .picture-card .picture-view').src"))
        b.eval("LabThemeChat.reset(); true")
        b.click_selector('#themeClose')

    def test_presets_by_category_and_a_whole_theme_from_one(self):
        b = self.browser
        self.open_presets()
        b.eval("[...document.querySelectorAll('#presetsDialog .pv-groups button')].find(x => x.dataset.group === 'Backgrounds').click(); true")
        cats = b.eval("[...document.querySelectorAll('#presetsDialog .pv-cats button')].map(x => x.dataset.cat).filter(Boolean)")
        if cats:                                                                         # a category shows only its own
            b.eval(f"[...document.querySelectorAll('#presetsDialog .pv-cats button')].find(x => x.dataset.cat === {cats[-1]!r}).click(); true")
            n = b.eval("document.querySelectorAll('#presetsDialog .pv-card').length")
            self.assertTrue(n >= 1)
        self.assertEqual(b.eval("document.querySelectorAll('#themeLog .picture-card').length"), 0)   # opening Presets adds nothing to the chat
        # Right-click: a whole theme from it, asked of Theme AI with the preset.
        b.eval("window.__asked=[];if(!window.__realFetch)window.__realFetch=window.fetch;window.fetch=(u,o)=>{const p=new URL(String(u),location.href).pathname;"
               "if(p==='/api/theme-chat'){__asked.push(JSON.parse(o.body));return Promise.resolve(new Response(JSON.stringify({plan:{reply:'ok',operations:[],ui_colors:{},hide:[]},source:'picture',note:'',preview:null}),{headers:{'Content-Type':'application/json'}}));}"
               "return window.__realFetch(u,o);};true")
        b.eval("(r => r.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:300,clientY:300})))(document.querySelector('#presetsDialog .pv-card')); true")
        b.wait_for("!document.querySelector('.preset-menu').hidden")
        labels = b.eval("[...document.querySelectorAll('.preset-menu button')].map(x => x.textContent)")
        self.assertEqual(labels[:2], ['See it big', 'Pick it'])
        self.assertIn('Make a whole theme from this', labels)
        b.eval("[...document.querySelectorAll('.preset-menu button')].find(x => x.textContent === 'Make a whole theme from this').click(); true")
        b.wait_for("window.__asked.length === 1")
        sent = b.eval("window.__asked[0]")
        self.assertTrue(sent['theme_from'].startswith('preset:'))
        self.assertTrue(b.eval("document.querySelector('.preset-menu').hidden && !document.getElementById('presetsDialog').open"))
        b.eval("window.fetch=window.__realFetch; LabThemeChat.reset(); true")
        b.click_selector('#themeClose')
