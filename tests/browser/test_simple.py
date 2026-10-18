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
            self.assertEqual(titles, ['Welcome to Asset Lab', 'Pick what to change', 'Right-click for more', 'Describe it', 'Bring your own picture', 'Put it in the game',
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
        self.assertEqual(labels, ['Colors', 'Whole theme', 'Background', 'Redraw something', 'Text & fonts', 'Presets'])
        b.click_selector('#themeStarters button')
        self.assertEqual(b.eval("document.getElementById('themeInput').value"), 'Make the buttons and panels ')
        self.assertFalse(b.eval("!!document.getElementById('gameColorsButton')"))
        self.assertTrue(b.eval("!!document.querySelector('#themeDrawer .theme-head #themeChats')"))   # the chats on the title line
        for folded in ('themeAuto', 'themeAccelerate', 'themePictureButton', 'themeModel'):          # under More
            self.assertFalse(b.eval(f"document.getElementById('{folded}').checkVisibility()"), folded)
        b.click_selector('#themeMore summary')
        for folded in ('themeAuto', 'themeAccelerate', 'themePictureButton', 'themeModel'):
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

    def test_a_preset_is_seen_big_then_used_recolored_and_put_in_the_game(self):
        b = self.browser
        self.open_presets()
        groups = b.eval("[...document.querySelectorAll('#presetsDialog .pv-groups button')].map(x => x.dataset.group)")
        self.assertEqual(groups[0], 'Backgrounds')
        self.assertIn('Viruses', groups)
        b.eval("[...document.querySelectorAll('#presetsDialog .pv-groups button')].find(x => x.dataset.group === 'Viruses').click(); true")
        b.eval("[...document.querySelectorAll('#presetsDialog .pv-card')].find(x => x.textContent === 'Purple virus').click(); true")
        b.wait_for("!!document.getElementById('presetUse')")                               # seen big first: two game floors
        self.assertEqual(b.eval("document.querySelectorAll('#presetsDialog .pv-floor').length"), 2)
        b.click_selector('#presetUse')                                                       # Use picks it: back to the list
        b.wait_for("!!document.getElementById('presetsUseAll')")
        self.assertEqual(b.eval("document.querySelector('#presetsDialog .pv-card.picked').textContent"), 'Purple virus')
        b.click_selector('#presetsUseAll')
        b.wait_for("!document.getElementById('presetsDialog').open && !!document.querySelector('#themeLog .picture-card')", timeout=20)
        card = "[...document.querySelectorAll('#themeLog .picture-card')].at(-1)"
        self.assertNotIn('color=', b.eval(f"{card}.querySelector('.picture-view').src"))     # a preset starts in its own colors
        b.eval(f"[...{card}.querySelectorAll('.picture-chip')].find(x => x.title === 'Dark blue').click(); true")
        b.wait_for(f"{card}.querySelector('.picture-view').src.includes('color=%231e3a8a')")
        b.wait_for(f"{card}.querySelector('.picture-view').complete && {card}.querySelector('.picture-view').naturalWidth === 512")
        b.eval(f"{card}.querySelector('.picture-view').click(); true")                      # the picture big
        b.wait_for("document.getElementById('pictureViewer').open")
        b.eval("document.getElementById('pictureViewer').close(); true")
        if b.eval(f"{card}.querySelectorAll('.picture-target option').length"):
            b.eval(f"{card}.querySelector('.theme-actions .primary').click(); true")
            b.wait_for(f"{card}.querySelector('.theme-actions .primary').textContent === 'Applied ✓'", timeout=20)
        b.eval("LabThemeChat.reset(); true")
        b.click_selector('#themeClose')

    def test_right_click_exports_a_preset_and_a_picture_in_the_chat(self):
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
            b.eval("[...document.querySelectorAll('#presetsDialog .pv-card')].find(x => x.textContent === 'Purple virus').click(); true")
            b.wait_for("!!document.getElementById('presetUse')")
            b.click_selector('#presetUse')
            b.wait_for("!!document.getElementById('presetsUseAll')")
            b.click_selector('#presetsUseAll')
            b.wait_for("!document.getElementById('presetsDialog').open && !!document.querySelector('#themeLog .picture-card')", timeout=20)
            card = "[...document.querySelectorAll('#themeLog .picture-card')].at(-1)"
            b.eval(f"[...{card}.querySelectorAll('.picture-chip')].find(x => x.title === 'Dark blue').click(); true")
            b.wait_for(f"{card}.querySelector('.picture-view').src.includes('color=%231e3a8a')")
            b.eval(f"""{card}.querySelector('.picture-view').dispatchEvent(new MouseEvent('contextmenu',{{bubbles:true,cancelable:true,clientX:40,clientY:40}}));true""")
            b.wait_for("!document.querySelector('.preset-menu').hidden")
            self.assertTrue(b.eval("document.getElementById('contextMenu').hidden"))   # only the Export menu
            b.eval("document.querySelector('.preset-menu button').click(); true")
            href, name = b.eval("__saved.at(-1)")
            self.assertEqual(name, 'Purple-virus-1e3a8a.png')
            self.assertIn('color=%231e3a8a', href)
            self.assertNotIn('small', href)
            b.eval("LabThemeChat.reset(); true")
            b.click_selector('#themeClose')
        finally:
            b.eval("delete HTMLAnchorElement.prototype.click; true")

    def test_picks_from_each_kind_are_used_all_at_once(self):
        # Use picks (one of each kind); Use all puts every pick in the chat and on the game, one after the other.
        b = self.browser
        self.open_presets()
        b.eval("""window.__asked=[];window.__realAsk=LabThemeChat.ask;LabThemeChat.ask=(t,o)=>{__asked.push([t,o]);return Promise.resolve();};true""")
        try:
            groups = b.eval("[...document.querySelectorAll('#presetsDialog .pv-groups button')].map(x => x.dataset.group)")
            for g in groups[:3]:
                b.eval(f"[...document.querySelectorAll('#presetsDialog .pv-groups button')].find(x => x.dataset.group === {g!r}).click(); true")
                b.eval("document.querySelector('#presetsDialog .pv-card').click(); true")
                b.wait_for("!!document.getElementById('presetUse')")
                b.click_selector('#presetUse')
                b.wait_for("!!document.getElementById('presetsTray')")
            self.assertEqual(b.eval("document.querySelectorAll('#presetsTray .pv-pick').length"), len(groups[:3]))
            b.eval("document.querySelector('#presetsDialog .pv-card').click(); true")      # the same one again: unpicked
            b.wait_for("document.getElementById('presetUse').textContent === 'Picked ✓'")
            b.click_selector('#presetUse')
            b.wait_for("document.querySelectorAll('#presetsTray .pv-pick').length === %d" % (len(groups[:3]) - 1))
            b.click_selector('#presetsUseAll')
            b.wait_for("window.__asked.length === %d" % (len(groups[:3]) - 1))
            self.assertTrue(all(o['apply'] and o['picture'].startswith('preset:') for _, o in b.eval("__asked")))
            self.assertFalse(b.eval("document.getElementById('presetsDialog').open"))
        finally:
            b.eval("LabThemeChat.ask=window.__realAsk; document.getElementById('presetsDialog').close(); true")
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
