import json
from harness import EditorTestCase


class RedrawMenu(EditorTestCase):
    """Right-click on a picture → Redraw with Image AI…: asks for the look, then Theme AI draws that asset (image AI on)."""

    def test_right_click_redraw_asks_theme_ai_to_draw_it(self):
        b = self.browser
        key = self.server.asset_key('edit-sprite.png')
        b.select_asset('edit-sprite.png')
        # Theme AI's answer is stubbed; the request it gets is kept.
        b.eval("window.__chat=[];if(!window.__realFetch)window.__realFetch=window.fetch;"
               "window.fetch=(u,o)=>{const p=new URL(String(u),location.href).pathname;"
               "if(p==='/api/theme-chat'){__chat.push(JSON.parse(o.body));return Promise.resolve(new Response(JSON.stringify("
               "{plan:{reply:'Done.',operations:[],ui_colors:{},hide:[]},source:'design',note:'',preview:null}),{headers:{'Content-Type':'application/json'}}));}"
               "return window.__realFetch(u,o);};window.prompt=()=>'made of gold';true")
        x, y = b.center_of('#viewport')[:2]
        b.click(x, y, button='right')
        b.wait_for("!document.getElementById('contextMenu').hidden")
        self.assertFalse(b.eval("document.querySelector('#contextMenu [data-action=redraw-ai]').hidden"))
        b.eval("document.querySelector('#contextMenu [data-action=redraw-ai]').click(); true")
        b.wait_for("window.__chat.length === 1", message='Theme AI was not asked')
        sent = b.eval("window.__chat[0]")
        self.assertTrue(sent['message'].startswith('Redraw ') and sent['message'].endswith(': made of gold'), sent['message'])
        self.assertEqual(sent['only'], [key])
        self.assertEqual(sent['draw'], 'on')
        self.assertTrue(b.eval("document.getElementById('contextMenu').hidden"))
        b.eval("window.fetch=window.__realFetch; true")
        self.assertNoBrowserErrors()
