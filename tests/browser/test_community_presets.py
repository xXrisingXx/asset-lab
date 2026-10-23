from harness import EditorTestCase
from fakegame import make_editor_game
from PIL import Image, ImageDraw

def community_game(root):
    game=make_editor_game(root)
    # Three differently filled food sprites exercise the real food-kind mapping.
    sheet=Image.new("RGBA",(192,64))
    d=ImageDraw.Draw(sheet)
    for i,r in enumerate((8,16,24)):
        x=i*64+32;d.ellipse((x-r,32-r,x+r,32+r),fill="white")
    sheet.save(game/"assets/sprites.png")
    (game/"assets/sprites.xml").write_text('<TextureAtlas imagePath="sprites.png">'+''.join(f'<SubTexture name="food{i+1}" x="{i*64}" y="0" width="64" height="64" />' for i in range(3))+'</TextureAtlas>')
    return game


class CommunityPresets(EditorTestCase):
    viewport = (1366, 900)
    game_maker = staticmethod(community_game)

    def test_community_theme_credit_food_choices_and_build(self):
        b = self.browser
        b.eval("localStorage.setItem('lab-presets-tour-done','1'); LabPresets.open('Community themes')")
        b.wait_for("!!document.querySelector('[data-theme=\"tron-delta\"]')")
        self.assertTrue(b.eval("document.querySelector('[data-theme=\"tron-delta\"]').textContent.includes('Credit: Delta')"))
        b.screenshot('community-theme-card.png')
        b.click_selector('[data-theme="tron-delta"]')
        b.wait_for("document.querySelectorAll('.pv-community-foods select').length===3")
        self.assertEqual(b.eval("[...document.querySelectorAll('.pv-community-foods select')].map(s=>s.options.length)"), [3, 3, 3])
        b.wait_for("[...document.querySelectorAll('.pv-community-parts img')].every(i=>i.complete&&i.naturalWidth>0)")
        b.screenshot('community-theme-detail.png')
        # The approved foods also retain the normal preset placement controls, including All foods.
        b.eval("document.querySelectorAll('.pv-community-parts .pv-card')[4].click()")
        b.wait_for("!!document.querySelector('#presetCustom')")
        self.assertTrue(b.eval("document.querySelector('#presetCustom').textContent.includes('All foods')"))
        b.screenshot('community-food-placement.png')
        b.eval("LabPresets.open('Community themes')")
        b.click_selector('[data-theme="tron-delta"]')
        b.eval("const s=document.querySelector('.pv-community-foods select');s.selectedIndex=2;s.dispatchEvent(new Event('change'));true")
        old = self.server.get('/api/builds')['active']
        b._navigate(lambda: b.click_selector('#communityThemeUse'), 60)
        b.wait_lab()
        builds = self.server.get('/api/builds')
        self.assertNotEqual(builds['active'], old)
        self.assertTrue(any(x['id'] == old for x in builds['builds']))
        b.wait_for("document.querySelector('#buildButton b').textContent==='TRON - Delta'")
        self.assertTrue(self.server.get('/api/status')['staged'])
        self.assertFalse(self.server.get('/api/status')['active'])

    def test_z_community_tab_fits_narrow_viewport(self):
        b = self.browser
        b.set_viewport(720,850)
        b.eval("LabPresets.open('Community themes')")
        b.wait_for("!!document.querySelector('[data-theme=\"tron-delta\"]')")
        b.click_selector('[data-theme="tron-delta"]')
        self.assertTrue(b.eval("document.querySelector('#presetsDialog').scrollWidth<=document.querySelector('#presetsDialog').clientWidth+2"))
        b.screenshot('community-theme-narrow.png')
