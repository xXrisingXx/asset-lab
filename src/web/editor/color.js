'use strict';
/* =====================================================================================================================
   COLOR AND HISTORY (editor/color.js), built on window.Ed only (the engine API is documented in core.js).

   COLOR PICKER  Photoshop's Color Picker: the hue strip beside the saturation/brightness square, H S B, R G B, L a b and
     hex fields, the new color over the current one (click the current one to go back to it), Add to Swatches, and a click
     or drag on the picture samples a color (with the Eyedropper's Sample Size and Sample). Drag the title to move it.
     It is the picker of the tool-strip chips (Ed.registerColorPicker) and of every color field the shell draws
     (.ed-field input[type=color] in dialogs, the options bar and panels): a click opens it instead of the browser's
     picker; the input gets 'input' events while you pick (live previews) and 'change' on OK; Cancel puts the old value
     back. Ed.colorPicker({color, title, onInput(hex)}) -> Promise<'#rrggbb'|null> opens it from code.
     Ed.colorMath = {rgb2hsb, hsb2rgb, rgb2lab, lab2rgb, parseHex}: HSB as Photoshop's (H 0-360, S and B 0-100); Lab is
     CIE L*a*b* (D50) through the ICC sRGB profile's Bradford-adapted colorants, which is what Photoshop shows for sRGB.
   COLOR PANEL  The foreground and background chips (click: Color Picker) with the foreground's hex, and the swatches:
     Recent colors, My Swatches (yours: + adds the foreground, double-click renames, Alt+click or the trash deletes), This
     asset (the picture's most used colors) and Photoshop's default sets. Click = foreground, Ctrl+click = background.
     Kept in localStorage. Ed.swatches = {list(), add(hex, name), remove(index), rename(index, name), recent()}.
   EYEDROPPER  (I; replaces the core one) Sample Size (Point Sample, 3 by 3 ... 101 by 101 Average) and Sample (Current
     Layer, All Layers) through Ed.sample (alpha-weighted, so transparent pixels do not count; all transparent leaves the
     color alone). Alt-click picks the background color. The painting tools' Alt-click (shell) reads the same options.
   HISTORY  Replaces the shell's panel (same id 'history'). Snapshots sit above the states: the document as it was opened,
     then Snapshot 1... (camera button; double-click renames). Clicking a state goes back to it; clicking a snapshot puts
     its layers, pixels, masks and settings back as one new state named after it, which Undo takes back. The trash deletes
     the selected snapshot, or the current state and every state after it (Ed.history.truncate).
     HISTORY BRUSH SOURCE (contract with paint.js): Ed.historySource = {label, kind: 'snapshot', snapshot: id,
       layers: Map(layer id -> canvas), masks: Map(layer id -> canvas|null)} or null. The canvases are document-sized
       copies that never change: read them, never draw into them. It is the opened-document snapshot after each open (and
       after saved layers are restored); the box left of a snapshot picks another. The 'history-source' event fires with
       it whenever it changes.
   ===================================================================================================================== */
(()=>{
const Ed=window.Ed;if(!Ed||!Ed.util||!Ed.registerPanel)return;
const U=Ed.util,clamp=U.clamp,store=U.store;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const btn=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const iconBtn=(cls,paths,title)=>{const b=btn(cls,'',title);b.setAttribute('aria-label',title);b.append(Ed.icon(paths,{size:16}));return b;};
const viewEl=document.getElementById('view');
function report(e,where){if(Ed.test)Ed.test.errors.push({where,message:String(e&&e.message||e)});console.error('[editor] '+where+':',e);}
const shownPanel=()=>{const t=document.querySelector('#edDock .ed-tab[aria-selected="true"]');return t?t.dataset.panel:null;};
const IC={eyedropper:['M14.5 5.5l4 4','M17 3a2.1 2.1 0 0 1 3 3l-2.5 2.5-3-3z','M15.5 8.5L7 17l-3 1 1-3 8.5-8.5'],
  camera:['M4 8.5h3.2L8.8 6h6.4l1.6 2.5H20V19H4z','M12 10.5a3.3 3.3 0 1 0 0 6.6a3.3 3.3 0 1 0 0-6.6z'],
  trash:['M5 7h14','M10 7V5h4v2','M7 7l1 12h8l1-12','M10.5 10.5v5.5M13.5 10.5v5.5'],plus:['M5 5h14v14H5z','M12 9v6M9 12h6'],
  histBrush:['M20 4c-4 2.5-8.5 7-10.5 10l1.5 1.5c3-2 7.5-6.5 10-10.5z','M9.5 14c-2.2 0-3.5 1.6-3.5 3.4 0 1.4-.9 2.2-2 2.6 3.6 1.2 7.6.1 7-4.5']};

// ----- color math ------------------------------------------------------------------------------------------------------
const lin=c=>{c/=255;return c<=0.04045?c/12.92:Math.pow((c+0.055)/1.055,2.4);};
const gam=v=>255*(v<=0.0031308?12.92*v:1.055*Math.pow(v,1/2.4)-0.055);
// sRGB -> XYZ (D50): the colorants of the ICC sRGB IEC61966-2.1 profile (Bradford-adapted to D50); white = their sum.
const M=[[0.4360747,0.3850649,0.1430804],[0.2225045,0.7168786,0.0606169],[0.0139322,0.0971045,0.7141733]];
const MI=(m=>{const[a,b,c]=m[0],[d,e,f]=m[1],[g,h,i]=m[2],A=e*i-f*h,B=f*g-d*i,C=d*h-e*g,det=a*A+b*B+c*C;
  return[[A/det,(c*h-b*i)/det,(b*f-c*e)/det],[B/det,(a*i-c*g)/det,(c*d-a*f)/det],[C/det,(b*g-a*h)/det,(a*e-b*d)/det]];})(M);
const WHITE=M.map(r=>r[0]+r[1]+r[2]),EPS=216/24389,KAP=24389/27;
const fLab=t=>t>EPS?Math.cbrt(t):(KAP*t+16)/116;
function rgb2lab(rgb){const v=rgb.map(lin),X=M.map(r=>r[0]*v[0]+r[1]*v[1]+r[2]*v[2]);
  const fx=fLab(X[0]/WHITE[0]),fy=fLab(X[1]/WHITE[1]),fz=fLab(X[2]/WHITE[2]);return[116*fy-16,500*(fx-fy),200*(fy-fz)];}
// Lab -> sRGB 0..255; out-of-gamut colors come back clamped.
function lab2rgb(lab){const fy=(lab[0]+16)/116,fx=fy+lab[1]/500,fz=fy-lab[2]/200,inv=t=>{const t3=t*t*t;return t3>EPS?t3:(116*t-16)/KAP;};
  const X=[WHITE[0]*inv(fx),WHITE[1]*(lab[0]>KAP*EPS?fy*fy*fy:lab[0]/KAP),WHITE[2]*inv(fz)];
  return MI.map(r=>clamp(gam(clamp(r[0]*X[0]+r[1]*X[1]+r[2]*X[2],0,1)),0,255));}
function rgb2hsb(rgb){const r=rgb[0],g=rgb[1],b=rgb[2],mx=Math.max(r,g,b),mn=Math.min(r,g,b),d=mx-mn;let h=0;
  if(d>0){h=mx===r?(g-b)/d:mx===g?(b-r)/d+2:(r-g)/d+4;h*=60;if(h<0)h+=360;}return[h,mx>0?d/mx*100:0,mx/255*100];}
function hsb2rgb(hsb){const h=((hsb[0]%360)+360)%360/60,s=clamp(hsb[1],0,100)/100,v=clamp(hsb[2],0,100)/100*255,i=Math.floor(h),f=h-i,p=v*(1-s),q=v*(1-s*f),t=v*(1-s*(1-f));
  return[[v,t,p],[q,v,p],[p,v,t],[p,q,v],[t,p,v],[v,p,q]][i%6];}
const parseHex=t=>{t=String(t==null?'':t).trim().replace(/^#/,'');if(/^[0-9a-f]{3}$/i.test(t))t=[...t].map(c=>c+c).join('');return /^[0-9a-f]{6}$/i.test(t)?'#'+t.toLowerCase():null;};
const hexOf=rgb=>U.toHex(rgb);
Ed.colorMath={rgb2hsb,hsb2rgb,rgb2lab,lab2rgb,parseHex};

// ----- Eyedropper --------------------------------------------------------------------------------------------------------
const SIZES=[[1,'Point Sample'],[3,'3 by 3 Average'],[5,'5 by 5 Average'],[11,'11 by 11 Average'],[31,'31 by 31 Average'],[51,'51 by 51 Average'],[101,'101 by 101 Average']];
function sample(x,y){const o=Ed.toolOptions('eyedropper')||{};return Ed.sample(x,y,{size:+o.sample||1,from:o.from==='layer'?'layer':'composite'});}
Ed.registerTool('eyedropper',{label:'Eyedropper Tool',group:'eyedropper',key:'i',cursor:'eyedropper',icon:()=>Ed.icon(IC.eyedropper),
  options:[{key:'sample',label:'Sample Size',type:'select',options:SIZES.map(([value,label])=>({value,label})),default:1,title:'Read the pixel under the cursor or the average of a square around it'},
    {key:'from',label:'Sample',type:'select',options:[{value:'layer',label:'Current Layer'},{value:'composite',label:'All Layers'}],default:'composite',title:'Read the selected layer only, or everything you see'}],
  onDown(p,e){this.which=e.altKey?'bg':'fg';const c=sample(p.x,p.y);if(c)Ed.setColor(this.which,c);},
  onMove(p){const c=sample(p.x,p.y);if(c)Ed.setColor(this.which||'fg',c);},onUp(){}});

// ----- Color Picker ----------------------------------------------------------------------------------------------------
// [key, label, unit, min, max, accessible name]
const FIELDS=[['h','H','°',0,360,'Hue'],['s','S','%',0,100,'Saturation'],['v','B','%',0,100,'Brightness'],['r','R','',0,255,'Red'],['g','G','',0,255,'Green'],['b','B','',0,255,'Blue'],
  ['L','L','',0,100,'Lightness'],['la','a','',-128,127,'a (green to red)'],['lb','b','',-128,127,'b (blue to yellow)']];
let picker=null;
function openPicker(o={}){if(picker)picker.close(null);
  return new Promise(resolve=>{
    const start=parseHex(o.color)||'#000000',m={rgb:U.rgbOf(start)};m.hsb=rgb2hsb(m.rgb);m.lab=rgb2lab(m.rgb);let closed=false,sampling=null;
    const title=o.title||'Color Picker',SQ=innerWidth>=1500&&innerHeight>=900?256:200;
    const d=el('dialog','ed-picker');d.dataset.edDialog=title;d.setAttribute('aria-label',title);const form=el('form');form.method='dialog';d.append(form);
    const head=el('div','ed-pk-head');head.append(el('h2','',title));head.title='Drag to move';
    const sqBox=el('div','ed-pk-square'),sq=el('canvas'),mark=el('span','ed-pk-mark');sq.width=sq.height=SQ;sqBox.style.width=sqBox.style.height=SQ+'px';
    sqBox.setAttribute('aria-label','Saturation and brightness: click or drag');sqBox.append(sq,mark);
    const stBox=el('div','ed-pk-strip'),st=el('canvas'),arrows=el('span','ed-pk-arrows');st.width=1;st.height=SQ;stBox.style.height=SQ+'px';
    stBox.setAttribute('aria-label','Hue: click or drag');stBox.append(st,arrows);
    const newSw=el('div','ed-pk-new'),curSw=btn('ed-pk-cur','','Current color · click to go back to it');curSw.style.backgroundColor=start;newSw.title='New color';
    const compare=el('div','ed-pk-compare');compare.append(el('span','ed-pk-cap','new'),newSw,curSw,el('span','ed-pk-cap','current'));
    const okB=el('button','primary','OK');okB.type='submit';const cancelB=btn('','Cancel'),addB=btn('ed-pk-add','Add to Swatches','Add the new color to My Swatches');
    const acts=el('div','ed-pk-actions');acts.append(okB,cancelB,addB);
    const inputs={},cols=[el('div','ed-pk-col'),el('div','ed-pk-col')];
    FIELDS.forEach(([key,label,unit,lo,hi,name],k)=>{const row=el('label','ed-pk-row');row.dataset.key=key;const i=el('input');i.type='number';i.name=key;i.min=lo;i.max=hi;i.step=1;i.setAttribute('aria-label',name);
      i.oninput=()=>{const v=Number(i.value);if(i.value===''||!isFinite(v))return;setField(key,clamp(v,lo,hi));update(i);};i.onchange=()=>update(null);
      row.append(el('span','ed-pk-label notranslate',label),i,el('span','ed-pk-unit notranslate',unit));inputs[key]=i;cols[k<6?0:1].append(row);});
    const hexRow=el('label','ed-pk-row ed-pk-hexrow'),hexIn=el('input');hexIn.type='text';hexIn.name='hex';hexIn.maxLength=7;hexIn.spellcheck=false;hexIn.setAttribute('aria-label','Hex color');
    hexIn.oninput=()=>{const t=hexIn.value.trim();if(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.test(t)){fromRGB(U.rgbOf(parseHex(t)));update(hexIn);}};hexIn.onchange=()=>update(null);
    hexRow.append(el('span','ed-pk-label notranslate','#'),hexIn);cols[1].append(hexRow);
    const fields=el('div','ed-pk-fields');fields.append(...cols);
    const side=el('div','ed-pk-side'),top=el('div','ed-pk-top');top.append(compare,acts);side.append(top,fields);
    const main=el('div','ed-pk-main');main.append(sqBox,stBox,side);
    form.append(head,main,el('p','ed-pk-hint','Click the picture to sample a color'));
    const hex=()=>hexOf(m.rgb);
    // From RGB: HSB and Lab follow; a gray keeps its hue (and black its saturation), so the strip does not jump.
    function fromRGB(rgb){const old=m.hsb;m.rgb=rgb.map(c=>clamp(Math.round(c),0,255));const h=rgb2hsb(m.rgb);if(h[1]===0||h[2]===0){h[0]=old[0];if(h[2]===0)h[1]=old[1];}m.hsb=h;m.lab=rgb2lab(m.rgb);}
    function fromHSB(h){m.hsb=[clamp(h[0],0,360),clamp(h[1],0,100),clamp(h[2],0,100)];m.rgb=hsb2rgb(m.hsb).map(c=>clamp(Math.round(c),0,255));m.lab=rgb2lab(m.rgb);}
    function setField(key,v){const H=m.hsb,R=m.rgb,L=m.lab;
      switch(key){case 'h':fromHSB([v,H[1],H[2]]);break;case 's':fromHSB([H[0],v,H[2]]);break;case 'v':fromHSB([H[0],H[1],v]);break;
        case 'r':fromRGB([v,R[1],R[2]]);break;case 'g':fromRGB([R[0],v,R[2]]);break;case 'b':fromRGB([R[0],R[1],v]);break;
        default:{const lab=[...L];lab[{L:0,la:1,lb:2}[key]]=v;fromRGB(lab2rgb(lab));m.lab=lab;}}}
    const value=key=>{switch(key){case 'h':return Math.round(m.hsb[0])%360;case 's':return Math.round(m.hsb[1]);case 'v':return Math.round(m.hsb[2]);
      case 'r':return m.rgb[0];case 'g':return m.rgb[1];case 'b':return m.rgb[2];case 'L':return Math.round(m.lab[0]);case 'la':return Math.round(m.lab[1]);default:return Math.round(m.lab[2]);}};
    let sqHue=null;
    function drawSquare(){const h=m.hsb[0];if(h===sqHue)return;sqHue=h;const g=sq.getContext('2d'),img=g.createImageData(SQ,SQ),px=img.data;
      for(let j=0;j<SQ;j++){const v=100-(j+.5)/SQ*100;for(let i=0;i<SQ;i++){const c=hsb2rgb([h,(i+.5)/SQ*100,v]),k=(j*SQ+i)*4;px[k]=c[0];px[k+1]=c[1];px[k+2]=c[2];px[k+3]=255;}}g.putImageData(img,0,0);}
    {const g=st.getContext('2d'),img=g.createImageData(1,SQ),px=img.data;for(let j=0;j<SQ;j++){const c=hsb2rgb([360-(j+.5)/SQ*360,100,100]);px[j*4]=c[0];px[j*4+1]=c[1];px[j*4+2]=c[2];px[j*4+3]=255;}g.putImageData(img,0,0);}
    function update(skip){const h=hex();for(const [key,i] of Object.entries(inputs))if(i!==skip)i.value=String(value(key));if(skip!==hexIn)hexIn.value=h.slice(1);
      newSw.style.backgroundColor=h;drawSquare();
      mark.style.left=m.hsb[1]/100*SQ+'px';mark.style.top=(1-m.hsb[2]/100)*SQ+'px';mark.classList.toggle('dark',U.luma(h)>150);arrows.style.top=(1-m.hsb[0]/360)*SQ+'px';
      if(o.onInput){try{o.onInput(h);}catch(e){report(e,'color picker input');}}}
    const dragOn=(box,fn)=>box.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();box.setPointerCapture(e.pointerId);
      const go=ev=>{const r=box.getBoundingClientRect();fn(clamp((ev.clientX-r.left)/r.width,0,1),clamp((ev.clientY-r.top)/r.height,0,1));update(null);};go(e);
      const up=()=>{box.removeEventListener('pointermove',go);box.removeEventListener('pointerup',up);box.removeEventListener('pointercancel',up);};
      box.addEventListener('pointermove',go);box.addEventListener('pointerup',up);box.addEventListener('pointercancel',up);});
    dragOn(sqBox,(u,v)=>fromHSB([m.hsb[0],u*100,(1-v)*100]));dragOn(stBox,(u,v)=>fromHSB([(1-v)*360,m.hsb[1],m.hsb[2]]));
    curSw.onclick=()=>{fromRGB(U.rgbOf(start));update(null);};
    addB.onclick=()=>{if(swAdd(hex()))Ed.message(`Added ${hex()} to My Swatches.`);};
    // The picture under the (clear) backdrop: click or drag to sample, as Photoshop's picker turns the cursor into an Eyedropper.
    const inside=(r,e)=>e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom;
    function overImage(e,loose){if(!Ed.doc||!viewEl||viewEl.hidden)return null;if(!loose&&!inside(viewEl.getBoundingClientRect(),e))return null;
      const q=Ed.view.toImage(e.clientX,e.clientY),W=Ed.doc.width,H=Ed.doc.height;if(q.x>=0&&q.y>=0&&q.x<W&&q.y<H)return q;return loose?{x:clamp(q.x,0,W-.001),y:clamp(q.y,0,H-.001)}:null;}
    function sampleInto(q){const c=sample(q.x,q.y);if(!c)return;fromRGB(U.rgbOf(c));update(null);}
    d.addEventListener('pointerdown',e=>{if(e.target!==d||e.button!==0||inside(d.getBoundingClientRect(),e))return;const q=overImage(e);if(!q)return;e.preventDefault();sampling=e.pointerId;try{d.setPointerCapture(e.pointerId);}catch(x){}sampleInto(q);});
    d.addEventListener('pointermove',e=>{if(sampling===e.pointerId){const q=overImage(e,true);if(q)sampleInto(q);return;}d.classList.toggle('sampling',e.target===d&&!inside(d.getBoundingClientRect(),e)&&!!overImage(e));});
    const endSample=e=>{if(sampling===e.pointerId)sampling=null;};d.addEventListener('pointerup',endSample);d.addEventListener('pointercancel',endSample);
    // Drag the title to move the picker; the place is remembered.
    head.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();const r=d.getBoundingClientRect(),dx=e.clientX-r.left,dy=e.clientY-r.top;head.setPointerCapture(e.pointerId);
      const mv=ev=>moveTo(ev.clientX-dx,ev.clientY-dy),up=()=>{head.removeEventListener('pointermove',mv);head.removeEventListener('pointerup',up);head.removeEventListener('pointercancel',up);const b=d.getBoundingClientRect();store.set('ed-picker-pos',{x:b.left,y:b.top});};
      head.addEventListener('pointermove',mv);head.addEventListener('pointerup',up);head.addEventListener('pointercancel',up);});
    function moveTo(x,y){const r=d.getBoundingClientRect();d.style.right='auto';d.style.left=clamp(x,4,Math.max(4,innerWidth-r.width-4))+'px';d.style.top=clamp(y,4,Math.max(4,innerHeight-r.height-4))+'px';}
    function close(v){if(closed)return;closed=true;picker=null;if(d.open)d.close();d.remove();resolve(v);}
    form.onsubmit=e=>{e.preventDefault();close(hex());};cancelB.onclick=()=>close(null);d.addEventListener('cancel',e=>{e.preventDefault();close(null);});
    picker={close,element:d};
    document.body.append(d);d.showModal();const pos=store.get('ed-picker-pos',null);if(pos&&isFinite(pos.x)&&isFinite(pos.y))moveTo(pos.x,pos.y);
    update(null);hexIn.focus();hexIn.select();
  });}
Ed.colorPicker=openPicker;
Ed.registerColorPicker(({which,color})=>openPicker({color,title:`Color Picker (${which==='bg'?'Background Color':'Foreground Color'})`}));
// Every color field the shell draws (dialogs, the options bar, panels) opens the picker instead of the browser's.
document.addEventListener('click',e=>{const i=e.target;if(!(i instanceof HTMLInputElement)||i.type!=='color'||i.disabled||!i.closest('.ed-field'))return;
  e.preventDefault();e.stopPropagation();const before=i.value,lab=i.closest('.ed-field').querySelector('.ed-field-label'),name=lab?lab.textContent.trim():'';
  const fire=n=>i.dispatchEvent(new Event(n,{bubbles:true}));
  openPicker({color:i.value,title:name?`Color Picker (${name})`:'Color Picker',onInput:hex=>{if(i.value!==hex){i.value=hex;fire('input');}}})
    .then(hex=>{if(hex){i.value=hex;fire('input');fire('change');}else if(i.value!==before){i.value=before;fire('input');fire('change');}});},true);

// ----- swatches ----------------------------------------------------------------------------------------------------------
// Photoshop's default swatch sets (2020 and later), by hex.
const PS_SWATCHES=[['Basics','ff0000 ffff00 00ff00 00ffff 0000ff ff00ff ffffff ebebeb d6d6d6 c2c2c2 adadad 999999 858585 707070 5c5c5c 474747 000000'],
  ['Pastel Colors','f7977a f9ad81 fdc68a fff79a c4df9b a2d39c 82ca9d 7bcdc8 6ecff6 7ea7d8 8493ca 8882be a187be bc8dbf f49ac2 f6989d'],
  ['Light Colors','f26c4f f68e55 fbaf5c fff467 acd372 7cc576 3bb878 1abbb4 00bff3 438cca 5574b9 605ca8 855fa8 a763a8 f06ea9 f26d7d'],
  ['Pure Colors','ed1c24 f26522 f7941d fff200 8dc73f 39b54a 00a651 00a99d 00aeef 0072bc 0054a6 2e3192 662d91 92278f ec008c ed145b'],
  ['Dark Colors','9e0b0f a0410d a36209 aba000 598527 1a7b30 007236 00746b 0076a3 004b80 003471 1b1464 440e62 630460 9e005d 9e0039'],
  ['Darker Colors','790000 7b2e00 7d4900 827b00 406618 005e20 005826 005952 005b7f 003663 002157 0d004c 32004b 4b0049 7b0046 7a0026'],
  ['Browns','c7b299 998675 736357 534741 37302d c69c6d a67c52 8c6239 754c24 603913']];
const okSwatch=c=>c&&parseHex(c.hex)&&typeof c.name==='string';
const sw={mine:(()=>{const s=store.get('ed-my-swatches',null);return Array.isArray(s)?s.filter(okSwatch).map(c=>({hex:parseHex(c.hex),name:c.name.slice(0,80)})).slice(0,500):[];})(),
  recent:(store.get('ed-recent-colors',[])||[]).filter(h=>parseHex(h)).slice(0,12),closed:new Set(store.get('ed-swatch-closed',['pastel','light','pure','dark','darker','browns'])),
  sel:-1,root:null,sig:'',asset:{doc:null,version:-1,colors:[]},assetAt:0,assetTimer:0};
function swSave(){store.set('ed-my-swatches',sw.mine);swRender(true);Ed.emit('swatches');}
function swAdd(hex,name){hex=parseHex(hex);if(!hex)return null;sw.mine.push({hex,name:String(name||'Swatch '+(sw.mine.length+1)).slice(0,80)});sw.sel=sw.mine.length-1;swSave();return sw.mine[sw.sel];}
function swRemove(i){if(!sw.mine[i])return false;sw.mine.splice(i,1);sw.sel=-1;swSave();return true;}
function swSetName(i,name){name=String(name||'').trim().slice(0,80);if(!sw.mine[i]||!name)return false;sw.mine[i].name=name;swSave();return true;}
async function swRename(i){const c=sw.mine[i];if(!c)return;const r=await Ed.dialog({title:'Swatch Name',fields:[{key:'name',label:'Name',type:'text',default:c.name}],okLabel:'OK'});if(r)swSetName(i,r.name);}
Ed.swatches={list:()=>sw.mine.map(c=>({...c})),add:(hex,name)=>!!swAdd(hex,name),remove:swRemove,rename:swSetName,recent:()=>sw.recent.slice()};
// Recent colors: the foreground once it has stayed for half a second (a drag through the picker is one color).
let recentTimer=0;
Ed.on('color',()=>{clearTimeout(recentTimer);recentTimer=setTimeout(()=>{const h=Ed.fg;if(sw.recent[0]===h)return;sw.recent=[h,...sw.recent.filter(x=>x!==h)].slice(0,12);store.set('ed-recent-colors',sw.recent);swRender();},500);
  swTop();});
// This asset: the most used opaque colors of the picture (exact for pixel art), read at most every 0.7 s while shown.
function assetColors(max=24){const d=Ed.doc,k=Math.min(1,256/Math.max(d.width,d.height)),w=Math.max(1,Math.round(d.width*k)),h=Math.max(1,Math.round(d.height*k));
  let src=Ed.composite();if(k<1){const c=U.canvas(w,h),g=c.getContext('2d');g.imageSmoothingEnabled=false;g.drawImage(src,0,0,w,h);src=c;}
  const D=U.read(src,{x:0,y:0,w,h}).data,bins=new Map();
  for(let i=0;i<D.length;i+=4){if(D[i+3]<128)continue;const key=(D[i]>>3)<<10|(D[i+1]>>3)<<5|(D[i+2]>>3);let b=bins.get(key);if(!b){b={n:0,r:0,g:0,b:0};bins.set(key,b);}b.n++;b.r+=D[i];b.g+=D[i+1];b.b+=D[i+2];}
  const out=[];for(const b of [...bins.values()].sort((a,c)=>c.n-a.n)){if(out.length>=max)break;const rgb=[b.r/b.n,b.g/b.n,b.b/b.n];if(out.some(o=>Math.hypot(o[0]-rgb[0],o[1]-rgb[1],o[2]-rgb[2])<20))continue;out.push(rgb);}
  return out.map(hexOf);}
function refreshAsset(){const d=Ed.doc;if(!d){sw.asset={doc:null,version:-1,colors:[]};return;}if(sw.asset.doc===d&&sw.asset.version===d.version)return;
  const wait=700-(performance.now()-sw.assetAt);if(sw.asset.doc===d&&wait>0){clearTimeout(sw.assetTimer);sw.assetTimer=setTimeout(()=>swRender(),wait+20);return;}
  try{sw.asset={doc:d,version:d.version,colors:assetColors()};}catch(e){report(e,'swatches: this asset');sw.asset={doc:d,version:d.version,colors:[]};}sw.assetAt=performance.now();}
function swBuild(c){sw.root=c;c.replaceChildren();c.classList.add('ed-colorpanel');
  const top=el('div','ed-cp-top'),fg=btn('ed-cp-chip','','Foreground color · click to pick'),bg=btn('ed-cp-chip','','Background color · click to pick');fg.dataset.which='fg';bg.dataset.which='bg';
  fg.setAttribute('aria-label','Foreground color');bg.setAttribute('aria-label','Background color');fg.onclick=()=>Ed.pickColor('fg',fg);bg.onclick=()=>Ed.pickColor('bg',bg);
  const hexBox=el('label','ed-cp-hexbox'),hexIn=el('input');hexIn.type='text';hexIn.name='hex';hexIn.maxLength=7;hexIn.spellcheck=false;hexIn.setAttribute('aria-label','Foreground hex color');hexIn.title='Foreground color in hex (Enter to use it)';
  hexIn.onchange=()=>{const h=parseHex(hexIn.value);if(h)Ed.setColor('fg',h);else hexIn.value=Ed.fg.slice(1);};
  hexIn.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();hexIn.dispatchEvent(new Event('change'));hexIn.blur();}else if(e.key==='Escape'){hexIn.value=Ed.fg.slice(1);hexIn.blur();}};
  hexBox.append(el('span','','#'),hexIn);top.append(fg,bg,hexBox);
  const list=el('div','ed-sw-list');list.setAttribute('aria-label','Swatches');
  const foot=el('div','ed-panel-foot'),hint=el('span','ed-foot-hint','Click: foreground · Ctrl+click: background');
  const addB=iconBtn('ed-foot-btn',IC.plus,'Add the foreground color to My Swatches'),delB=iconBtn('ed-foot-btn',IC.trash,'Delete the selected swatch');addB.dataset.action='add';delB.dataset.action='delete';
  addB.onclick=()=>swAdd(Ed.fg);delB.onclick=()=>{if(sw.sel>=0)swRemove(sw.sel);};
  list.addEventListener('dblclick',e=>{const b=e.target.closest('.ed-swatch[data-group="mine"]');if(b&&!e.altKey&&!e.ctrlKey&&!e.metaKey)swRename(+b.dataset.index);});
  hint.title=hint.textContent;foot.append(hint,addB,delB);c.append(top,list,foot);Object.assign(sw,{list,delB,fg,bg,hexIn});swTop();}
function swTop(){if(!sw.root)return;sw.fg.style.backgroundColor=Ed.fg;sw.bg.style.backgroundColor=Ed.bg;if(document.activeElement!==sw.hexIn)sw.hexIn.value=Ed.fg.slice(1);}
function swatchButton(c,group,index){const b=btn('ed-swatch');b.setAttribute('translate','no');b.style.backgroundColor=c.hex;b.dataset.hex=c.hex;b.dataset.group=group;b.dataset.index=index;const mine=group==='mine';
  b.title=`${c.name?c.name+' · ':''}${c.hex}`;b.setAttribute('aria-label',(c.name||c.hex)+' '+c.hex);
  b.onclick=e=>{if(mine&&e.altKey){swRemove(index);return;}if(e.ctrlKey||e.metaKey){Ed.setColor('bg',c.hex);return;}Ed.setColor('fg',c.hex);if(mine){sw.sel=index;swMarkSel();}};
  return b;}
function swRender(force){if(!sw.root||shownPanel()!=='color')return;refreshAsset();
  const groups=[{id:'recent',name:'Recent colors',colors:sw.recent.map(h=>({hex:h,name:''}))},{id:'mine',name:'My Swatches',colors:sw.mine,empty:'Add colors with + or the Color Picker’s Add to Swatches.'},
    {id:'asset',name:'This asset',colors:sw.asset.colors.map(h=>({hex:h,name:''})),empty:Ed.doc?'No opaque colors here.':'Open an asset to see its colors.'},
    ...PS_SWATCHES.map(([name,list])=>({id:name.split(' ')[0].toLowerCase(),name,colors:list.split(' ').map(h=>({hex:'#'+h,name:''}))}))];
  const sig=JSON.stringify([groups.slice(0,3).map(g=>g.colors.map(c=>c.hex+c.name)),[...sw.closed],!!Ed.doc]);if(!force&&sig===sw.sig)return swMarkSel();sw.sig=sig;
  const scroll=sw.list.scrollTop,rows=[],existing=new Map([...sw.list.children].map(n=>[n.dataset.group,n]));
  for(const g of groups){if(g.id==='recent'&&!g.colors.length)continue;const open=!sw.closed.has(g.id),groupSig=JSON.stringify([open,g.colors,g.empty]);
    const kept=existing.get(g.id);if(kept&&kept.swSignature===groupSig){rows.push(kept);continue;}
    const sec=el('section','ed-sw-group');sec.dataset.group=g.id;sec.swSignature=groupSig;
    const h=btn('ed-sw-head');h.setAttribute('aria-expanded',String(open));h.append(el('span','ed-sw-arrow',open?'▾':'▸'),el('span','ed-sw-name',g.name),el('span','ed-sw-count',String(g.colors.length)));
    h.onclick=()=>{if(sw.closed.has(g.id))sw.closed.delete(g.id);else sw.closed.add(g.id);store.set('ed-swatch-closed',[...sw.closed]);swRender(true);};sec.append(h);
    if(open){const body=el('div','ed-sw-grid');g.colors.forEach((c,i)=>body.append(swatchButton(c,g.id,i)));if(!g.colors.length)body.append(el('span','ed-sw-empty',g.empty||''));sec.append(body);}
    rows.push(sec);}
  // A recent-color timer can fire between mouse-down and mouse-up. Keep unrelated buttons attached,
  // including their identity and focus, so single/double clicks are not silently lost.
  for(const node of [...sw.list.children])if(!rows.includes(node))node.remove();
  rows.forEach((node,i)=>{if(sw.list.children[i]!==node)sw.list.insertBefore(node,sw.list.children[i]||null);});
  sw.list.scrollTop=scroll;swMarkSel();}
// The selected swatch is marked in place, so a double-click lands on the same element as its first click.
function swMarkSel(){if(!sw.list)return;if(!sw.mine[sw.sel])sw.sel=-1;for(const b of sw.list.querySelectorAll('.ed-swatch[data-group="mine"]'))b.classList.toggle('selected',+b.dataset.index===sw.sel);sw.delB.disabled=sw.sel<0;}
Ed.registerPanel('color',{title:'Color',order:30,render(c){if(sw.root!==c)swBuild(c);swTop();swRender();}});
Ed.on('open',()=>{sw.asset={doc:null,version:-1,colors:[]};});

// ----- snapshots and the History panel -------------------------------------------------------------------------------------
const hist={rows:new Map(),snaps:[],seq:0,count:0,selected:null,source:null,root:null,list:null,sig:''};
const copyCanvas=c=>{if(!c)return null;const k=U.canvas(c.width,c.height);k.getContext('2d').drawImage(c,0,0);return k;};
function putAll(dst,src){const g=dst.getContext('2d');g.save();g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.globalCompositeOperation='copy';g.filter='none';g.shadowColor='transparent';g.drawImage(src,0,0);g.restore();}
function snapThumb(){const d=Ed.doc,n=28,k=Math.min(n/d.width,n/d.height),c=U.canvas(n,n),g=c.getContext('2d'),w=Math.max(1,d.width*k),h=Math.max(1,d.height*k);
  g.fillStyle='#2a3644';g.fillRect(0,0,n,n);g.imageSmoothingQuality='high';g.drawImage(Ed.composite(),(n-w)/2,(n-h)/2,w,h);c.className='ed-hist-thumb';return c;}
const mark=()=>({index:Ed.history.index,len:Ed.history.list().length,version:Ed.doc?Ed.doc.version:-1});
const sameMark=(a,b)=>!!a&&a.index===b.index&&a.len===b.len&&a.version===b.version;
function takeSnapshot(opened){const d=Ed.doc;if(!d)return null;if(!opened)hist.count++;
  const s={id:++hist.seq,name:opened?d.name:'Snapshot '+hist.count,opened:!!opened,thumb:snapThumb(),activeIndex:d.activeIndex,target:d.target,width:d.width,height:d.height,
    layers:d.layers.map(L=>({ref:L,id:L.id,name:L.name,kind:L.kind,visible:L.visible,opacity:L.opacity,fill:L.fill,blend:L.blend,clip:L.clip,locks:{...L.locks},maskEnabled:L.maskEnabled,
      effects:U.clone(L.effects),adjustment:U.clone(L.adjustment),data:U.clone(L.data),version:L.version,maskRef:L.mask,canvas:copyCanvas(L.canvas),mask:copyCanvas(L.mask)}))};
  hist.snaps.push(s);if(!opened)hist.selected={id:s.id,...mark()};histRender(true);return s;}
const snapSelected=s=>!!(hist.selected&&hist.selected.id===s.id&&sameMark(hist.selected,mark()));
// Back to a snapshot: its layers, pixels, masks and settings come back as one new state, which Undo takes back. Nothing is
// added when the document already is the snapshot.
function restoreSnapshot(s){const d=Ed.doc;if(!d||s.width!==d.width||s.height!==d.height)return false;
  const why=Ed.view.blocked;if(why&&/opening/.test(why)){Ed.message(why);return false;}
  Ed.history.record(s.name,tx=>{const want=s.layers;
    for(const w of want)if(!Ed.layers.get(w.id))Ed.layers.add({id:w.id,name:w.name,kind:w.kind,canvas:w.canvas,mask:copyCanvas(w.mask),withMask:false,at:Ed.layers.count(),history:false});
    for(const L of Ed.layers.list())if(!want.some(w=>w.id===L.id))Ed.layers.remove(L,{history:false});
    for(const w of want){const L=Ed.layers.get(w.id);if(!L)continue;const same=L===w.ref&&L.version===w.version;
      if(!same){putAll(L.canvas,w.canvas);}
      if(w.mask&&L.mask&&!(same&&L.mask===w.maskRef)){putAll(L.mask,w.mask);}
      if(w.mask&&!L.mask)Ed.layers.set(L,{mask:copyCanvas(w.mask)},{history:false});else if(!w.mask&&L.mask)Ed.layers.set(L,{mask:null},{history:false});
      Ed.layers.set(L,{name:w.name,visible:w.visible,opacity:w.opacity,fill:w.fill,blend:w.blend,locks:w.locks,maskEnabled:w.maskEnabled,effects:w.effects,adjustment:w.adjustment,data:w.data},{history:false});}
    want.forEach((w,i)=>{const j=Ed.layers.index(w.id);if(j>=0&&j!==i)Ed.layers.move(j,i,{history:false});});
    for(const w of want)Ed.layers.set(w.id,{clip:w.clip},{history:false});
    Ed.layers.setActive(clamp(s.activeIndex,0,want.length-1),s.target);Ed.update();},{layers:d.layers.map(L=>L.id),mask:true,op:{tool:'history',action:'snapshot'}});
  Ed.flush();hist.selected={id:s.id,...mark()};histRender(true);return true;}
function deleteSnapshot(s){hist.snaps=hist.snaps.filter(x=>x!==s);if(hist.selected&&hist.selected.id===s.id)hist.selected=null;
  if(hist.source&&hist.source.snapshot===s.id){const o=hist.snaps.find(x=>x.opened);setSource(o?sourceOf(o):null);}histRender(true);}
const sourceOf=s=>({label:s.name,kind:'snapshot',snapshot:s.id,layers:new Map(s.layers.map(w=>[w.id,w.canvas])),masks:new Map(s.layers.map(w=>[w.id,w.mask]))});
function setSource(src){hist.source=src;Ed.historySource=src;Ed.emit('history-source',src);histRender(true);}
function deleteCurrent(){const s=hist.snaps.find(snapSelected);if(s){deleteSnapshot(s);return;}if(!Ed.history.truncate)return;const i=Ed.history.index;
  if(i<=0){Ed.message('The first state is the picture as it was opened; it cannot be deleted.');return;}
  hist.selected=null;Ed.history.jump(i-1);Ed.history.truncate();histRender(true);}
function renameSnapshot(s,b){const name=b.querySelector('.ed-hist-name'),i=el('input','ed-rename');i.type='text';i.value=s.name;i.setAttribute('aria-label','Snapshot name');name.hidden=true;name.after(i);i.focus();i.select();let done=false;
  const end=ok=>{if(done)return;done=true;const v=i.value.trim();i.remove();name.hidden=false;if(ok&&v){s.name=v.slice(0,80);s.renamed=true;if(hist.source&&hist.source.snapshot===s.id){hist.source.label=s.name;Ed.emit('history-source',hist.source);}}histRender(true);};
  i.onkeydown=e=>{e.stopPropagation();if(e.key==='Enter'){e.preventDefault();end(true);}if(e.key==='Escape'){e.preventDefault();end(false);}};i.onblur=()=>end(true);i.onclick=e=>e.stopPropagation();i.ondblclick=e=>e.stopPropagation();}
function histBuild(c){hist.root=c;c.replaceChildren();c.classList.add('ed-history','ed-history-full');
  const list=el('div','ed-history-list ed-hist-list');list.setAttribute('role','listbox');list.setAttribute('aria-label','Snapshots and history states');
  const foot=el('div','ed-panel-foot'),src=el('span','ed-foot-hint ed-hist-src');
  const snapB=iconBtn('ed-foot-btn',IC.camera,'Create new snapshot'),delB=iconBtn('ed-foot-btn',IC.trash,'Delete the current state and the ones after it, or the selected snapshot');
  snapB.dataset.action='snapshot';delB.dataset.action='delete';snapB.onclick=()=>{if(Ed.doc)takeSnapshot(false);};delB.onclick=()=>deleteCurrent();
  list.addEventListener('dblclick',e=>{const b=e.target.closest('.ed-history-snap'),s=b&&hist.snaps.find(x=>String(x.id)===b.dataset.snapshot);if(s)renameSnapshot(s,b);});
  const snapBox=el('div','ed-hist-snaps'),sep=el('div','ed-hist-sep'),stateBox=el('div','ed-hist-states');list.append(snapBox,sep,stateBox);hist.rows=new Map();
  foot.append(src,snapB,delB);c.append(list,foot);Object.assign(hist,{list,srcLabel:src,snapB,delB,snapBox,sep,stateBox});}
function snapRow(s){let r=hist.rows.get(s.id);if(r)return r;
  const row=el('div','ed-hist-row'),pick=btn('ed-hsrc'),b=btn('ed-history-snap'),name=el('span','ed-hist-name');b.dataset.snapshot=s.id;b.setAttribute('role','option');
  pick.onclick=e=>{e.stopPropagation();setSource(sourceOf(s));};b.onclick=()=>{if(!b.querySelector('.ed-rename'))restoreSnapshot(s);};b.append(s.thumb,name);row.append(pick,b);
  r={row,pick,b,name};hist.rows.set(s.id,r);return r;}
function histRender(force){if(!hist.root||!hist.list)return;const states=Ed.history.list(),src=hist.source,mk=mark();
  const sig=JSON.stringify([states,hist.snaps.map(s=>[s.id,s.name]),hist.selected,mk,src&&[src.snapshot,src.label],!!Ed.doc]);if(!force&&sig===hist.sig)return;hist.sig=sig;
  const snapOn=hist.snaps.some(snapSelected),snapRows=[];
  for(const s of hist.snaps){const on=snapSelected(s),isSrc=!!(src&&src.snapshot===s.id),r=snapRow(s);snapRows.push(r.row);
    r.row.classList.toggle('current',on);r.b.classList.toggle('current',on);r.b.setAttribute('aria-selected',String(on));r.b.title=s.name+' · click to go back to this snapshot · double-click to rename';
    if(!r.b.querySelector('.ed-rename')&&r.name.textContent!==s.name)r.name.textContent=s.name;r.name.setAttribute('translate',s.opened||s.renamed?'no':'yes');
    r.pick.classList.toggle('on',isSrc);r.pick.setAttribute('aria-pressed',String(isSrc));r.pick.title=`Use “${s.name}” as the History Brush source`;r.pick.setAttribute('aria-label',r.pick.title);
    if(isSrc!==!!r.pick.firstChild)r.pick.replaceChildren(...(isSrc?[Ed.icon(IC.histBrush,{size:13})]:[]));}
  for(const id of [...hist.rows.keys()])if(!hist.snaps.some(s=>s.id===id))hist.rows.delete(id);
  if(snapRows.length!==hist.snapBox.children.length||snapRows.some((r,i)=>hist.snapBox.children[i]!==r))hist.snapBox.replaceChildren(...snapRows);
  hist.sep.hidden=!snapRows.length;
  const rows=[];
  for(const st of states){const row=el('div','ed-hist-row');const b=btn('ed-history-item'+(st.current&&!snapOn?' current':'')+(st.undone?' undone':''),st.label);
    b.setAttribute('role','option');b.setAttribute('aria-selected',String(st.current&&!snapOn));b.dataset.index=st.index;b.title=st.undone?'Click to redo up to here':'Click to go back to this state';
    b.onclick=()=>{hist.selected=null;Ed.history.jump(st.index);histRender(true);};row.append(el('span','ed-hsrc-gap'),b);rows.push(row);}
  hist.stateBox.replaceChildren(...rows);
  const cur=hist.list.querySelector('.ed-history-snap.current')||hist.stateBox.querySelector('.ed-history-item.current');
  if(cur){const L=hist.list.getBoundingClientRect(),r=cur.getBoundingClientRect();if(r.top<L.top||r.bottom>L.bottom)hist.list.scrollTop+=r.top-L.top-L.height/2;}
  hist.srcLabel.textContent=src?'Source: '+src.label:'No History Brush source';hist.srcLabel.title=src?'The History Brush paints from “'+src.label+'”':'Click the box beside a snapshot to paint from it with the History Brush';
  hist.snapB.disabled=!Ed.doc;hist.delB.hidden=!Ed.history.truncate;hist.delB.disabled=!Ed.doc||!(snapOn||Ed.history.index>0);}
Ed.registerPanel('history',{title:'History',order:20,render(c){if(hist.root!==c)histBuild(c);histRender();}});
function resetSnapshots(){hist.snaps=[];hist.selected=null;hist.count=0;let src=null;if(Ed.doc){const s=takeSnapshot(true);if(s)src=sourceOf(s);}setSource(src);}
Ed.on('open',()=>resetSnapshots());
Ed.on('document',d=>{if(d&&d.reason==='restore')resetSnapshots();});
Ed.on('history',()=>{if(shownPanel()==='history')histRender();});
})();
