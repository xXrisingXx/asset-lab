"""Hand-authored geometric UI glyphs for the TRON disc master, rendered with antialiasing.

These replace ambiguous symbols in the locally installed theme. Geometry is editable here;
the approved AI disc provides the material, while every symbol has a deliberate meaning.
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def symbol(name):
    im = Image.new('RGBA', (256, 256))
    d = ImageDraw.Draw(im)
    ink = '#d7fcff'
    def line(points, w=13): d.line(points, fill=ink, width=w, joint='curve')
    def circle(box, fill=False, w=12): d.ellipse(box, fill=ink if fill else None, outline=ink, width=w)
    def poly(points): d.polygon(points, fill=ink)
    def text(t, y=128, size=120):
        f = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', size)
        d.text((128,y), t, font=f, fill=ink, anchor='mm', stroke_width=1)
    def person(x,y,r=24):
        circle((x-r,y-r,x+r,y+r),True)
        d.rounded_rectangle((x-r*1.6,y+r+10,x+r*1.6,y+r*3),radius=r,fill=ink)
    if name in ('icon-crown','vip_icon'):
        poly([(38,75),(88,112),(128,49),(168,112),(218,75),(196,185),(60,185)])
        line([(63,207),(193,207)],12)
    elif name in ('button-x','popup-close','icon-remove'):
        line([(64,64),(192,192)],22);line([(192,64),(64,192)],22)
    elif name == 'icon-add': line([(128,48),(128,208)],22);line([(48,128),(208,128)],22)
    elif name in ('back-icon','icon-goto-previous','forward-icon','icon-goto-next'):
        right=name in ('forward-icon','icon-goto-next')
        pts=[(156,52),(80,128),(156,204)]
        if right: pts=[(256-x,y) for x,y in pts]
        line(pts,22)
    elif name == 'icon-expand':
        for pts in [[(48,100),(48,48),(100,48)],[(156,48),(208,48),(208,100)],[(48,156),(48,208),(100,208)],[(156,208),(208,208),(208,156)]]:line(pts,15)
    elif name == 'icon-flag':
        line([(65,45),(65,215)],14);poly([(72,50),(205,50),(171,100),(205,145),(72,145)])
    elif name == 'icon-edit':
        poly([(57,163),(158,62),(194,98),(93,199),(48,212)])
        line([(169,51),(205,87)],16)
    elif name in ('icon-star','icon-rate'):
        poly([(128+math.sin(i*math.pi/5)*(91 if i%2==0 else 42),128-math.cos(i*math.pi/5)*(91 if i%2==0 else 42)) for i in range(10)])
    elif name in ('icon-menu-profile','icon-friends','icon-menu-invite'):
        if name=='icon-menu-profile': person(128,77,32)
        else:
            person(87,87,25)
            if name=='icon-friends':person(170,87,25)
            else:line([(179,93),(179,167)],14);line([(143,130),(215,130)],14)
    elif name=='icon-share':
        line([(71,128),(184,62)],13);line([(71,128),(184,194)],13)
        for x,y in [(71,128),(184,62),(184,194)]:circle((x-24,y-24,x+24,y+24),True)
    elif name in ('icon-music-on','icon-music-off'):
        poly([(44,100),(81,100),(126,61),(126,195),(81,156),(44,156)])
        if name.endswith('off'):line([(162,98),(214,154)],13);line([(214,98),(162,154)],13)
        else:
            d.arc((85,74,196,182),-65,65,fill=ink,width=12)
            d.arc((69,46,229,210),-65,65,fill=ink,width=12)
    elif name in ('icon-coin','icon-sell'): text('$' if name=='icon-coin' else 'SELL',size=140 if name=='icon-coin' else 66)
    elif name=='icon-quest': text('!',size=190)
    elif name=='icon-chat':
        d.rounded_rectangle((40,55,216,176),radius=24,outline=ink,width=14)
        line([(75,176),(75,211),(116,176)],13)
        for x in (82,128,174):circle((x-8,106,x+8,122),True)
    elif name=='icon-ingame-potion':
        line([(96,44),(160,44)],14)
        line([(105,49),(105,98),(64,187),(68,208),(188,208),(192,187),(151,98),(151,49)],13)
        poly([(90,156),(166,156),(183,195),(73,195)])
    elif name in ('button-split','button-explain-split','icon-mitosis'):
        circle((38,86,112,160),True);circle((144,86,218,160),True)
        line([(128,62),(128,184)],7)
    elif name in ('button-eject','button-explain-eject'):
        circle((45,113,139,207),True);circle((147,72,192,117),True);circle((200,38,222,60),True)
    return im


def make(folder):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    import button_sets
    base=Image.open(folder/'base.png').convert('RGBA').resize((512,512),Image.Resampling.LANCZOS)
    for name in dict.fromkeys(button_sets.MENU+button_sets.PHONE+button_sets.PC+button_sets.HUD):
        if name in ('icon-play','icon-menu-settings'):continue
        if name in ('joystick','joystick-bg','power-container'):
            if name=='joystick':out=base.copy()
            else:
                out=Image.new('RGBA',(512,512));d=ImageDraw.Draw(out)
                d.ellipse((65,65,447,447),outline='#176678',width=13)
                d.ellipse((75,75,437,437),outline='#8bf4ff',width=5)
        else:
            out=base.copy();g=symbol(name)
            # Keep original keyboard/mouse hints on the desktop variants.
            pc=name.startswith('button-explain-')
            g.thumbnail((200,180) if pc else (200,200),Image.Resampling.LANCZOS)
            out.alpha_composite(g,((512-g.width)//2,135 if pc else (512-g.height)//2))
            if pc:
                d=ImageDraw.Draw(out);font=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf',25)
                d.rounded_rectangle((154,322,184,363),radius=8,outline='#d7fcff',width=2)
                d.line((169,323,169,339),fill='#d7fcff',width=2)
                d.rectangle((156 if name.endswith('split') else 171,325,167 if name.endswith('split') else 182,337),fill='#53e8f5')
                d.text((198,329),'or '+('SPACE' if name.endswith('split') else 'W'),font=font,fill='#d7fcff')
        if name=='icon-sell':
            out=Image.new('RGBA',(512,192));d=ImageDraw.Draw(out)
            d.rounded_rectangle((12,12,500,180),radius=30,fill='#030b10',outline='#53e8f5',width=7)
            font=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf',116);d.text((256,100),'SELL',font=font,fill='#d7fcff',anchor='mm')
        if name=='icon-quest':
            out=Image.new('RGBA',(128,512));d=ImageDraw.Draw(out);font=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf',400)
            d.text((64,256),'!',font=font,fill='#d7fcff',anchor='mm')
        out.save(folder/'drawn'/f'{name}.webp',lossless=True)


if __name__=='__main__':
    make(Path(__file__).resolve().parents[1]/'src/presets/community/tron-delta')
