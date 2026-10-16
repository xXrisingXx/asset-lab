'use strict';
/* Fills, shapes and text for the Asset Lab editor. This file: the Gradient tool (G), the Paint Bucket (G), Edit > Fill...
   (Shift+F5, Shift+Backspace), Edit > Stroke..., Edit > Define Pattern..., Alt+Backspace / Ctrl+Backspace (foreground /
   background fill; add Shift to keep transparent pixels clear), the Gradient Editor and patterns. It loads
   editor/fill-shapes.js (Rectangle, Ellipse, Polygon, Line: U) and editor/fill-type.js (Type: T, live type layers).
   Every fill goes through Ed.applyToTarget: the selection (soft), Lock Transparent Pixels, layer masks (the gray of the
   paint) and one history state named as in Photoshop ('Paint Bucket', 'Gradient', 'Fill', 'Stroke').

   PAINT BUCKET  The pixels within Tolerance of the clicked one on every channel (premultiplied, so transparency counts),
     4-connected when Contiguous (a one pixel diagonal line stops it), from the active layer or, with All Layers, from the
     visible picture; never outside the selection. Anti-alias cuts each corner of the filled area's pixel staircase
     diagonally through the pixel edges' midpoints (half a quarter pixel gained or lost), so straight edges stay crisp.
     Fills with the foreground color or a pattern; Mode, Anti-alias and All Layers sit under More.
   GRADIENTS (Photoshop's math; tests/browser/test_fill_refs.py has the numpy reference)
     t at each pixel centre p, with the drag from a to b, d = b - a, L = |d|, u = (p - a) . d / L^2:
       linear u · radial |p - a| / L · angle (atan2(d) - atan2(p - a)) mod 2pi / 2pi (a counterclockwise sweep from the
       drag direction) · reflected |u| · diamond (|(p - a) . d| + |(p - a) x d|) / L^2 (the end point is a corner),
     clamped to 0..1 (Reverse: 1 - t). Color and opacity stops blend in sRGB like Photoshop's Classic method; between two
     stops the weight is P ^ (ln .5 / ln mid) with P the position inside the segment and the midpoint limited to 5..95%.
     Values round to the nearest level; Dither adds hash noise below one level instead. Transparency off ignores the
     opacity stops.

   SHARED CONTRACT (other modules may rely on it; feature-detect it, this file may be missing)
   Ed.gradients = {
     sample(g, t) -> [r, g, b, a] floats 0..255 at t (0..1),
     image(g, kind, x0, y0, x1, y1, {rect, reverse, dither, transparency=true}) -> ImageData of rect (pixel centres sampled),
     fill(ctx, g, kind, x0, y0, x1, y1, opts) draws it into a canvas (canvas pixels; the context's alpha, mode and clip apply),
     presets (getter: list of named gradients, fg/bg resolved now), edit(g) -> Promise<g|null> (the Gradient Editor),
     normalize(g), kinds: ['linear', 'radial', 'angle', 'reflected', 'diamond']}
   g = {name?, stops: [{at: 0..1, color: '#rrggbb', mid: 0..1 (midpoint to the next stop, default .5), kind?: 'fg'|'bg' (follows
     the current colors; color holds a snapshot)}], alpha: [{at, a: 0..1, mid}]}.
   Ed.patterns = {list() -> [{id, name, width, height, canvas}], get(id), add(name, canvas) -> id, remove(id)}; patterns tile
     from the document's top left corner, as Photoshop aligns them.
   Field types: 'gradient' (value g), 'pattern' (value: a pattern id).
   Type layers keep everything in layer.data.text (fill-type.js); a fill on one asks to rasterize it first.
   Ed.fillKit holds helpers shared by the fill-*.js files (internal). */
(()=>{
const Ed=window.Ed;if(!Ed||!Ed.util)return;const U=Ed.util,{clamp}=U;
const base=document.currentScript&&document.currentScript.src;

// ----- small helpers ---------------------------------------------------------------------------------------------------
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const isHex=v=>/^#[0-9a-f]{6}$/i.test(String(v||''));
const typing=e=>{const t=e.target;return !!(t&&t.closest&&t.closest('input,textarea,select,[contenteditable=""],[contenteditable="true"]'));};
const modalOpen=()=>!!document.querySelector('dialog[open]');
const busy=()=>typeof S!=='undefined'&&!!S.busy;
const clone=v=>JSON.parse(JSON.stringify(v));
function hash01(x,y){let h=Math.imul(x|0,374761393)+Math.imul(y|0,668265263)|0;h=Math.imul(h^(h>>>13),1274126177);h^=h>>>16;return (h>>>0)/4294967296;}
function checker(g,w,h,s=5){g.fillStyle='#ffffff';g.fillRect(0,0,w,h);g.fillStyle='#c4cbd4';for(let y=0;y<h;y+=s)for(let x=(y/s)%2?s:0;x<w;x+=2*s)g.fillRect(x,y,s,s);}
const svgCursor=(svg,x,y,fb)=>`url("data:image/svg+xml,${encodeURIComponent(svg)}") ${x} ${y}, ${fb}`;
const ICON={gradient:['M4 5h16v14H4z','M8.5 5v14','M12 5v14','M15 5v14','M17.5 5v14'],
  bucket:['M4.5 11.5l6.5-6.5 7.5 7.5-6.5 6.5a1.8 1.8 0 0 1-2.5 0l-5-5a1.8 1.8 0 0 1 0-2.5z','M4.5 11.5h14','M11 5L8.5 2.5','M20.2 15.5c0 1.6 1.3 2.5 1.3 3.6a1.3 1.3 0 0 1-2.6 0c0-1.1 1.3-2 1.3-3.6z'],
  linear:['M3 4h14v12H3z','M7 4v12','M10 4v12','M12.5 4v12','M14.5 4v12'],radial:['M10 3a7 7 0 1 0 0 14a7 7 0 1 0 0-14z','M10 7a3 3 0 1 0 0 6a3 3 0 1 0 0-6z'],
  angle:['M10 3a7 7 0 1 0 0 14a7 7 0 1 0 0-14z','M10 10h7','M10 10l-4.5-5.3'],reflected:['M3 4h14v12H3z','M10 4v12','M6.5 4v12','M13.5 4v12'],
  diamond:['M10 2.5l7.5 7.5-7.5 7.5-7.5-7.5z','M10 6.5l3.5 3.5-3.5 3.5-3.5-3.5z']};
const BUCKET_CURSOR=svgCursor('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="M4.5 11.5l6.5-6.5 7.5 7.5-6.5 6.5a1.8 1.8 0 0 1-2.5 0l-5-5a1.8 1.8 0 0 1 0-2.5z" fill="#fff" stroke="#000" stroke-width="1.3"/><path d="M4.5 11.5h14" stroke="#000" stroke-width="1.2"/><path d="M20.2 15.5c0 1.6 1.3 2.5 1.3 3.6a1.3 1.3 0 0 1-2.6 0c0-1.1 1.3-2 1.3-3.6z" fill="#000" stroke="#fff" stroke-width=".8"/></svg>',20,21,'crosshair');

// Custom fields re-sync themselves when their tool's options change (the shell only syncs its own field types).
const syncs=new Set();
function showIf(s,values){if(!s.field.showIf)return;const w=s.node.closest('.ed-field')||s.node,[k,v]=s.field.showIf;w.hidden=![].concat(v).includes(values[k]);}
function track(field,node,sync){if(!field||!field.tool)return;const s={tool:field.tool,key:field.key,field,node,sync};syncs.add(s);if(field.showIf)queueMicrotask(()=>showIf(s,Ed.toolOptions(field.tool)));}
Ed.on('options',({id,values})=>{for(const s of [...syncs]){if(!s.node.isConnected){syncs.delete(s);continue;}if(s.tool===id)try{s.sync(values[s.key]);showIf(s,values);}catch(e){}}});
const swatchRedraws=new Set();   // gradient swatches that follow the foreground / background colors
Ed.on('color',()=>{for(const f of [...swatchRedraws]){if(!f.node.isConnected){swatchRedraws.delete(f);continue;}f.draw();}});

// A small panel under a control (inside a dialog when the control is in one, so it stays usable).
let pop=null;
function popover(anchor,content,label){closePopover();const host=anchor.closest('dialog')||document.body,p=el('div','fill-pop');p.setAttribute('role','dialog');p.setAttribute('aria-label',label||'Options');p.append(content);host.append(p);
  const r=anchor.getBoundingClientRect(),w=p.offsetWidth,h=p.offsetHeight;p.style.left=Math.max(6,Math.min(r.left,innerWidth-w-8))+'px';p.style.top=(r.bottom+h+8>innerHeight&&r.top-h-4>0?r.top-h-4:r.bottom+4)+'px';
  const off=e=>{if(!p.contains(e.target)&&!anchor.contains(e.target))closePopover();};
  p.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closePopover();anchor.focus();}});
  document.addEventListener('pointerdown',off,true);pop={p,anchor,close(){p.remove();document.removeEventListener('pointerdown',off,true);}};
  const f=p.querySelector('button,input,select');if(f)f.focus({preventScroll:true});return pop;}
function closePopover(){if(pop){const o=pop;pop=null;o.close();}}
window.addEventListener('resize',closePopover);

// ----- painting modes: Photoshop's list (Normal, Dissolve, Behind, Clear, then the blend modes) --------------------------
const SPECIAL={behind:'destination-over',clear:'destination-out'};
const modeIds=()=>modeOptions().filter(o=>o!=='-').map(o=>o.value);
const modeOf=v=>modeIds().includes(v)?v:'normal';
function modeOptions(){const list=Ed.blends.list(),normal=list.filter(b=>b.group==='normal'),out=[];
  if(!normal.some(b=>b.id==='dissolve'))normal.splice(1,0,{id:'dissolve',label:'Dissolve'});
  for(const b of normal)out.push({value:b.id,label:b.label});out.push({value:'behind',label:'Behind'},{value:'clear',label:'Clear'});
  let g='normal';for(const b of list){if(b.group==='normal')continue;if(b.group!==g){out.push('-');g=b.group;}out.push({value:b.id,label:b.label});}return out;}
const modeField=title=>({key:'mode',label:'Mode',type:'select',options:modeOptions(),default:'normal',title:title||'Blending mode: how the new paint mixes with the pixels already there'});
// Tool options use a compact select that lists the blend modes registered when it is drawn (modules add modes later).
const MODE=(tool,title)=>({key:'mode',label:'Mode',type:'fill-mode',default:'normal',tool,title:title||'Blending mode: how the new paint mixes with the pixels already there'});
Ed.registerField('fill-mode',(f,value,set)=>{const s=el('select','fill-mode-select');s.name=f.key||'mode';s.setAttribute('aria-label',f.label||'Mode');
  for(const o of modeOptions()){if(o==='-'){s.append(el('hr'));continue;}const x=el('option','',o.label);x.value=o.value;s.append(x);}
  s.value=modeOf(value);s.onchange=()=>{set(s.value);s.blur();};track(f,s,v=>{s.value=modeOf(v);});return s;});
let modeFrame=0;Ed.on('blends',()=>{if(modeFrame)return;modeFrame=requestAnimationFrame(()=>{modeFrame=0;Ed.emit('tools');});});
// "More": options that do not fit the options bar open in a popover. They stay real tool options (saved, read with
// Ed.toolOptions); in the bar they are 'fill-hidden' fields.
Ed.registerField('fill-hidden',()=>{const s=el('span');s.hidden=true;return s;});
Ed.registerField('fill-more',f=>{const b=btn('fill-more',(f.label||'More')+' \u25be',f.title||'More options');b.setAttribute('aria-haspopup','dialog');b.setAttribute('aria-label',f.title||'More options');
  b.onclick=()=>{if(pop&&pop.anchor===b){closePopover();return;}const box=el('div','fill-more-box'),vals=Ed.toolOptions(f.tool);if(f.heading)box.append(el('p','fill-pop-title',f.heading));
    for(const d of f.items){const x=Ed.ui.field(d,vals[d.key],v=>Ed.setToolOptions(f.tool,{[d.key]:v}));box.append(x.node);}popover(b,box,f.title||'More options');};return b;});
const hidden=(key,def)=>({key,type:'fill-hidden',default:def});

// Paint `buf` (a canvas covering R; its alpha is the coverage) onto `dst` inside R with a painting mode and opacity, as
// Photoshop's fill tools do: lock / preserve transparency keeps the old alpha (the color moves toward the result), a mask
// target takes the gray of the paint.
function paintInto(dst,buf,R,o={}){const mode=o.mode||'normal',k=clamp(o.opacity??1,0,1);if(k<=0||!R||R.w<=0||R.h<=0)return;
  if(o.target==='mask'){maskPaint(dst,buf,R,k,mode);return;}
  const lock=!!o.lockAlpha;if(lock&&SPECIAL[mode])return;   // Photoshop greys out Behind and Clear with locked transparency
  const b=Ed.blends.get(mode);let op=SPECIAL[mode]||(mode==='normal'?(lock?'source-atop':'source-over'):(!lock&&b&&b.op&&mode!=='dissolve'?b.op:null));
  if(op){const g=dst.getContext('2d');g.save();g.setTransform(1,0,0,1,0,0);g.globalAlpha=k;g.globalCompositeOperation=op;g.filter='none';g.beginPath();g.rect(R.x,R.y,R.w,R.h);g.clip();g.drawImage(buf,R.x,R.y);g.restore();return;}
  const D=U.read(dst,R),Sd=U.read(buf,{x:0,y:0,w:R.w,h:R.h}),d=D.data,s=Sd.data,fn=mode==='dissolve'||!b?null:b.fn,cb=[0,0,0],cs=[0,0,0],out=[0,0,0];
  for(let y=0,i=0;y<R.h;y++)for(let x=0;x<R.w;x++,i+=4){let as=s[i+3]/255*k;if(as<=0)continue;if(mode==='dissolve'){if(hash01(R.x+x,R.y+y)>=as)continue;as=1;}
    const ab=d[i+3]/255;for(let c=0;c<3;c++){cs[c]=s[i+c]/255;cb[c]=d[i+c]/255;}
    if(fn&&ab>0)fn(cb,cs,out);else{out[0]=cs[0];out[1]=cs[1];out[2]=cs[2];}
    if(lock){if(ab<=0)continue;for(let c=0;c<3;c++)d[i+c]=Math.round(255*(cb[c]+(clamp(out[c],0,1)-cb[c])*as));continue;}
    const ao=as+ab*(1-as);for(let c=0;c<3;c++)d[i+c]=Math.round(255*(as*(1-ab)*cs[c]+as*ab*clamp(out[c],0,1)+(1-as)*ab*cb[c])/ao);d[i+3]=Math.round(255*ao);}
  U.write(dst,D,R);}
function maskPaint(dst,buf,R,k,mode){const D=U.read(dst,R),Sd=U.read(buf,{x:0,y:0,w:R.w,h:R.h}),d=D.data,s=Sd.data;
  for(let y=0,i=0;y<R.h;y++)for(let x=0;x<R.w;x++,i+=4){let a=s[i+3]/255*k;if(a<=0)continue;if(mode==='dissolve'){if(hash01(R.x+x,R.y+y)>=a)continue;a=1;}
    const v=mode==='clear'?0:.299*s[i]+.587*s[i+1]+.114*s[i+2],m=d[i+3];d[i]=d[i+1]=d[i+2]=255;d[i+3]=Math.round(m+(v-m)*a);}
  U.write(dst,D,R);}
// Fill the active target through the selection: make(info) -> {canvas, rect} (or null) is painted with the mode.
async function applyPaint(label,make,o={}){const L=Ed.layers.active();if(!L)return false;if(!await ensureRaster(L))return false;
  return Ed.applyToTarget(label,async(g,info)=>{const r=await make(info);if(!r)return;
    paintInto(info.canvas,r.canvas,r.rect,{mode:o.mode,opacity:o.opacity,target:info.target,lockAlpha:info.target==='pixels'&&(!!info.layer.locks.alpha||!!o.preserve)});},{rect:o.rect,op:o.op});}

// ----- gradients --------------------------------------------------------------------------------------------------------
const KINDS=['linear','radial','angle','reflected','diamond'];
function normalize(g){g=g&&typeof g==='object'?g:{};const num=(v,d)=>{v=Number(v);return isFinite(v)?v:d;};
  let stops=(Array.isArray(g.stops)?g.stops:[]).filter(s=>s&&typeof s==='object').map(s=>{const o={at:clamp(num(s.at,0),0,1),color:isHex(s.color)?String(s.color).toLowerCase():'#000000',mid:clamp(num(s.mid,.5),0,1)};if(s.kind==='fg'||s.kind==='bg')o.kind=s.kind;return o;});
  if(!stops.length)stops=[{at:0,color:'#000000',mid:.5},{at:1,color:'#ffffff',mid:.5}];
  let alpha=(Array.isArray(g.alpha)?g.alpha:[]).filter(s=>s&&typeof s==='object').map(s=>({at:clamp(num(s.at,0),0,1),a:clamp(num(s.a,1),0,1),mid:clamp(num(s.mid,.5),0,1)}));
  if(!alpha.length)alpha=[{at:0,a:1,mid:.5},{at:1,a:1,mid:.5}];
  stops=stops.map((s,i)=>[s,i]).sort((a,b)=>a[0].at-b[0].at||a[1]-b[1]).map(x=>x[0]);alpha=alpha.map((s,i)=>[s,i]).sort((a,b)=>a[0].at-b[0].at||a[1]-b[1]).map(x=>x[0]);
  const out={stops,alpha};if(g.name)out.name=String(g.name).slice(0,60);return out;}
const stopColor=s=>s.kind==='fg'?Ed.fg:s.kind==='bg'?Ed.bg:s.color;
function compile(g){g=normalize(g);const seg=s=>{const m=clamp(s.mid,.05,.95);return Math.abs(m-.5)<1e-9?1:Math.log(.5)/Math.log(m);};
  return{c:g.stops.map(s=>({at:s.at,v:U.rgbOf(stopColor(s)),e:seg(s)})),a:g.alpha.map(s=>({at:s.at,v:s.a*255,e:seg(s)}))};}
// [index, weight]: the value is list[i] blended toward list[i + 1] by weight (Photoshop's midpoint curve).
function locate(list,t){const n=list.length;if(t<=list[0].at)return[0,0];if(t>=list[n-1].at)return[n-1,0];let i=0;while(t>=list[i+1].at)i++;
  const a=list[i],P=(t-a.at)/(list[i+1].at-a.at);return[i,a.e===1?P:Math.pow(P,a.e)];}
function evalInto(C,t,out){let [i,w]=locate(C.c,t);const u=C.c[i].v;if(w>0){const v=C.c[i+1].v;out[0]=u[0]+(v[0]-u[0])*w;out[1]=u[1]+(v[1]-u[1])*w;out[2]=u[2]+(v[2]-u[2])*w;}else{out[0]=u[0];out[1]=u[1];out[2]=u[2];}
  [i,w]=locate(C.a,t);const a=C.a[i].v;out[3]=w>0?a+(C.a[i+1].v-a)*w:a;return out;}
function gradientImage(g,kind,x0,y0,x1,y1,o={}){const R=o.rect||{x:0,y:0,w:1,h:1},W=Math.max(1,R.w|0),H=Math.max(1,R.h|0),D=new ImageData(W,H),d=D.data,C=compile(g);
  const k=Math.max(0,KINDS.indexOf(kind)),dx=x1-x0,dy=y1-y0,L2=dx*dx+dy*dy,L=Math.sqrt(L2),rev=!!o.reverse,dith=!!o.dither,useA=o.transparency!==false,a0=Math.atan2(dy,dx),TAU=2*Math.PI,c=[0,0,0,0];
  for(let y=0,i=0;y<H;y++){const Y=R.y+y+.5-y0;for(let x=0;x<W;x++,i+=4){const X=R.x+x+.5-x0;let t;
    if(L2<1e-12)t=0;else if(k===0)t=(X*dx+Y*dy)/L2;else if(k===1)t=Math.sqrt(X*X+Y*Y)/L;else if(k===2){let a=(a0-Math.atan2(Y,X))%TAU;if(a<0)a+=TAU;t=a/TAU;}
    else if(k===3)t=Math.abs(X*dx+Y*dy)/L2;else t=(Math.abs(X*dx+Y*dy)+Math.abs(Y*dx-X*dy))/L2;
    t=t<0?0:t>1?1:t;if(rev)t=1-t;evalInto(C,t,c);
    if(dith){const n=hash01(R.x+x,R.y+y);d[i]=Math.floor(c[0]+n);d[i+1]=Math.floor(c[1]+n);d[i+2]=Math.floor(c[2]+n);d[i+3]=useA?Math.floor(c[3]+n):255;}
    else{d[i]=Math.floor(c[0]+.5);d[i+1]=Math.floor(c[1]+.5);d[i+2]=Math.floor(c[2]+.5);d[i+3]=useA?Math.floor(c[3]+.5):255;}}}
  return D;}
function gradientFill(ctx,g,kind,x0,y0,x1,y1,o={}){const c=ctx.canvas,F={x:0,y:0,w:c.width,h:c.height},r=o.rect||F,x=Math.max(0,Math.floor(r.x)),y=Math.max(0,Math.floor(r.y)),
    R={x,y,w:Math.min(c.width,Math.ceil(r.x+r.w))-x,h:Math.min(c.height,Math.ceil(r.y+r.h))-y};if(R.w<=0||R.h<=0)return;
  const t=U.canvas(R.w,R.h);U.write(t,gradientImage(g,kind,x0,y0,x1,y1,{...o,rect:R}));ctx.save();ctx.setTransform(1,0,0,1,0,0);ctx.drawImage(t,R.x,R.y);ctx.restore();}
const st=(at,color,mid)=>({at,color,mid:mid??.5}),op=(at,a)=>({at,a,mid:.5}),OPAQUE=[op(0,1),op(1,1)];
const BUILTIN_GRADIENTS=()=>[
  {name:'Foreground to Background',stops:[{...st(0,Ed.fg),kind:'fg'},{...st(1,Ed.bg),kind:'bg'}],alpha:OPAQUE},
  {name:'Foreground to Transparent',stops:[{...st(0,Ed.fg),kind:'fg'},{...st(1,Ed.fg),kind:'fg'}],alpha:[op(0,1),op(1,0)]},
  {name:'Black, White',stops:[st(0,'#000000'),st(1,'#ffffff')],alpha:OPAQUE},
  {name:'Spectrum',stops:[st(0,'#ff0000'),st(.15,'#ff00ff'),st(.33,'#0000ff'),st(.49,'#00ffff'),st(.67,'#00ff00'),st(.84,'#ffff00'),st(1,'#ff0000')],alpha:OPAQUE},
  {name:'Chrome',stops:[st(0,'#1d5ca8'),st(.45,'#bfe0fa'),st(.5,'#ffffff'),st(.52,'#4a3420'),st(.78,'#a47a4c'),st(1,'#fff3dc')],alpha:OPAQUE},
  {name:'Rainbow',stops:[st(0,'#ff0000'),st(.17,'#ff8000'),st(.33,'#ffff00'),st(.5,'#00c000'),st(.67,'#0080ff'),st(.83,'#4b0082'),st(1,'#8f00ff')],alpha:OPAQUE},
  {name:'Gold',stops:[st(0,'#5c3a00'),st(.3,'#c98b0e'),st(.5,'#ffe27a'),st(.56,'#fff8d6'),st(.8,'#d9a21a'),st(1,'#6e4500')],alpha:OPAQUE},
  {name:'Steel',stops:[st(0,'#3b4452'),st(.45,'#e5e7eb'),st(.5,'#ffffff'),st(.75,'#9ca3af'),st(1,'#2d3440')],alpha:OPAQUE},
  {name:'Copper',stops:[st(0,'#3b1d0e'),st(.4,'#b8643c'),st(.55,'#f3b58a'),st(.8,'#8a4424'),st(1,'#2b1408')],alpha:OPAQUE},
  {name:'Fire',stops:[st(0,'#fff7a8'),st(.25,'#ffc21a'),st(.55,'#ff6a00'),st(.8,'#c41e00'),st(1,'#3d0000')],alpha:OPAQUE},
  {name:'Ice',stops:[st(0,'#ffffff'),st(.3,'#c9f4ff'),st(.65,'#5ec8f2'),st(1,'#1f5fa8')],alpha:OPAQUE},
  {name:'Toxic',stops:[st(0,'#f4ff7a'),st(.4,'#7dff3a'),st(.75,'#18a83a'),st(1,'#0b3d1f')],alpha:OPAQUE},
  {name:'Sunset',stops:[st(0,'#2b1055'),st(.3,'#7b2d8e'),st(.6,'#e8475f'),st(.85,'#ffb347'),st(1,'#ffe29f')],alpha:OPAQUE},
  {name:'Night Sky',stops:[st(0,'#020617'),st(.5,'#1e1b4b'),st(1,'#4338ca')],alpha:OPAQUE},
  {name:'Health Bar',stops:[st(0,'#e53935'),st(.5,'#fdd835'),st(1,'#43a047')],alpha:OPAQUE},
  {name:'Neon',stops:[st(0,'#ff00cc'),st(1,'#3333ff')],alpha:OPAQUE},
  {name:'Soft Shadow',stops:[st(0,'#000000'),st(1,'#000000')],alpha:[op(0,.85),op(1,0)]},
  {name:'Glow',stops:[st(0,'#ffffff'),st(.5,'#fff3b0'),st(1,'#ffcc33')],alpha:[op(0,1),{at:1,a:0,mid:.35}]}].map(normalize);
const PRESET_KEY='ed-gradient-presets';
const customPresets=()=>{const v=U.store.get(PRESET_KEY,[]);return Array.isArray(v)?v.map(normalize):[];};
function addPreset(g){const list=U.store.get(PRESET_KEY,[]);const n=Array.isArray(list)?list:[];n.push(normalize(g));U.store.set(PRESET_KEY,n.slice(-60));}
function removePreset(i){const n=U.store.get(PRESET_KEY,[]);if(Array.isArray(n)){n.splice(i,1);U.store.set(PRESET_KEY,n);}}
function drawRamp(cv,g){const w=cv.width,h=cv.height,c=cv.getContext('2d');checker(c,w,h,Math.max(3,Math.round(h/4)));const t=U.canvas(w,h);U.write(t,gradientImage(g,'linear',0,0,w,0,{rect:{x:0,y:0,w,h}}));c.drawImage(t,0,0);}
async function editGradient(g){const r=await Ed.dialog({title:'Gradient Editor',fields:[{key:'gradient',label:'',type:'fill-gradient-editor',default:normalize(g)}],okLabel:'OK'});return r?normalize(r.gradient):null;}
Ed.gradients={kinds:KINDS,normalize,sample:(g,t)=>evalInto(compile(g),clamp(+t||0,0,1),[0,0,0,0]),image:gradientImage,fill:gradientFill,edit:editGradient,
  get presets(){return [...BUILTIN_GRADIENTS(),...customPresets()];}};

// A grid of gradient presets (the picker and the editor share it).
function presetGrid(onPick,opts={}){const box=el('div','fill-swatches');box.setAttribute('role','listbox');box.setAttribute('aria-label','Gradient presets');
  const draw=()=>{box.replaceChildren();const builtin=BUILTIN_GRADIENTS(),mine=customPresets();
    [...builtin,...mine].forEach((g,i)=>{const own=i>=builtin.length,b=btn('fill-swatch','',g.name+(own?' · right-click to delete':''));b.setAttribute('role','option');b.dataset.preset=g.name;
      const cv=el('canvas');cv.width=40;cv.height=24;drawRamp(cv,g);b.append(cv);b.onclick=()=>onPick(clone(g));
      if(own)b.oncontextmenu=e=>{e.preventDefault();removePreset(i-builtin.length);draw();Ed.message(`Deleted the gradient preset “${g.name}”.`);};box.append(b);});};
  draw();box.redraw=draw;return box;}

// The Gradient Editor: presets, name and New, the ramp with opacity stops above and color stops below (click a track to add,
// drag to move, drag away or press Delete to remove), midpoints next to the selected stop, and the selected stop's fields.
function gradientEditor(value,set){let g=normalize(value),sel={type:'c',i:0},mid=null;const root=el('div','fill-ge');
  const emit=()=>set(normalize(clone(g)));   // a copy: the markers and fields keep editing the live stop objects
  const grid=presetGrid(p=>{g=normalize(p);sel={type:'c',i:0};mid=null;name.value=g.name||'Custom';draw();emit();});
  const nameRow=el('div','fill-ge-row'),nameLabel=el('label','fill-ge-label','Name'),name=el('input');name.type='text';name.name='gradient-name';name.value=g.name||'Custom';name.id='fillGeName';nameLabel.htmlFor=name.id;
  name.oninput=()=>{g.name=name.value.trim()||'Custom';emit();};name.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();newB.click();}};
  const newB=btn('fill-ge-new','New','Save this gradient as a new preset');newB.onclick=()=>{g.name=name.value.trim()||'Custom';addPreset(g);grid.redraw();Ed.message(`Saved the gradient “${g.name}” to your presets.`);};
  nameRow.append(nameLabel,name,newB);
  const bar=el('div','fill-ge-bar'),top=el('div','fill-ge-track fill-ge-top'),bottom=el('div','fill-ge-track fill-ge-bottom'),ramp=el('canvas','fill-ge-ramp');ramp.width=320;ramp.height=26;
  top.title='Opacity stops: click to add one, drag to move it, drag it away to delete it';bottom.title='Color stops: click to add one, drag to move it, drag it away to delete it';bar.append(top,ramp,bottom);
  const fields=el('div','fill-ge-fields');
  const list=t=>t==='a'?g.alpha:g.stops;
  const pct=v=>Math.round(v*1000)/10;
  function numField(label,v,min,max,on,key){const w=el('label','fill-ge-num'),i=el('input');i.type='number';i.min=min;i.max=max;i.step=1;i.value=v;i.name=key;w.append(el('span','',label),i,el('span','ed-unit','%'));
    i.onchange=()=>{const n=Number(i.value);if(isFinite(n)&&i.value!==''){on(clamp(n,min,max));}};i.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();i.dispatchEvent(new Event('change'));}};return w;}
  function drawFields(){fields.replaceChildren();
    if(mid){const s=list(mid.type)[mid.i];fields.append(numField('Midpoint',pct(s.mid),5,95,v=>{s.mid=v/100;draw();emit();},'mid'));return;}
    const L=list(sel.type),s=L[sel.i];if(!s)return;
    if(sel.type==='a')fields.append(numField('Opacity',pct(s.a),0,100,v=>{s.a=v/100;draw();emit();},'stop-opacity'));
    else{const w=el('label','fill-ge-num'),c=el('input');c.type='color';c.name='stop-color';c.value=stopColor(s);c.oninput=c.onchange=()=>{s.color=c.value;delete s.kind;drawRamp(ramp,g);for(const m of root.querySelectorAll('.fill-ge-stop'))paintMarker(m);emit();};w.append(el('span','','Color'),c);fields.append(w);
      if(s.kind)fields.append(el('span','fill-ge-note',s.kind==='fg'?'Follows the foreground color':'Follows the background color'));}
    fields.append(numField('Location',pct(s.at),0,100,v=>{s.at=v/100;resort();draw();emit();},'stop-location'));
    const del=btn('fill-ge-delete','Delete','Delete this stop (Delete key)');del.disabled=L.length<=2;del.onclick=()=>removeStop(sel.type,sel.i);fields.append(del);}
  function resort(){const L=list(sel.type),s=L[sel.i];L.sort((a,b)=>a.at-b.at);sel.i=L.indexOf(s);}
  function removeStop(type,i){const L=list(type);if(L.length<=2){Ed.message('A gradient needs at least two stops of each kind.');return;}L.splice(i,1);sel={type,i:Math.min(i,L.length-1)};mid=null;draw();emit();}
  // Stop markers are only rebuilt when no drag is running: the element that holds the pointer capture must stay in place.
  function marker(type,i,s){const m=btn('fill-ge-stop '+(type==='a'?'alpha':'color'),'');m.dataset.type=type;m.dataset.index=i;m.style.left=(s.at*100)+'%';m._stop=s;
    m.setAttribute('aria-label',(type==='a'?'Opacity stop ':'Color stop ')+(i+1));const chip=el('i');m.append(chip);paintMarker(m);
    m.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();sel={type,i:list(type).indexOf(s)};mid=null;m.focus({preventScroll:true});refresh();drag(e,type,s,m);};
    m.onkeydown=e=>{const L=list(type),k=L.indexOf(s);if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();e.stopPropagation();removeStop(type,k);}
      else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();s.at=clamp(s.at+(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?.1:.01),0,1);sel={type,i:k};resort();draw();emit();focusSel();}};
    return m;}
  function paintMarker(m){const s=m._stop,type=m.dataset.type,a=type==='a'?Math.round(255*(1-s.a)):0;m.firstChild.style.background=type==='a'?`rgb(${a},${a},${a})`:stopColor(s);
    m.title=(type==='a'?`Opacity ${pct(s.a)}%`:`Color ${stopColor(s)}`)+` at ${pct(s.at)}% · drag to move, drag it off the bar to delete it`;}
  function midMarker(type,i){const L=list(type),a=L[i],b=L[i+1];if(!b||b.at-a.at<=0)return null;const m=btn('fill-ge-mid','');m.style.left=((a.at+(b.at-a.at)*clamp(a.mid,.05,.95))*100)+'%';
    m.classList.toggle('on',!!mid&&mid.type===type&&mid.i===i);m.title=`Midpoint ${pct(clamp(a.mid,.05,.95))}% · drag to bend the blend`;m.setAttribute('aria-label','Midpoint');m.dataset.type=type;m.dataset.index=i;
    m.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();mid={type,i};for(const x of root.querySelectorAll('.fill-ge-mid'))x.classList.toggle('on',x===m);selClasses();drawFields();
      const r=m.parentElement.getBoundingClientRect();m.setPointerCapture(e.pointerId);
      const move=ev=>{const t=clamp((ev.clientX-r.left)/r.width,0,1);a.mid=clamp((t-a.at)/(b.at-a.at),.05,.95);m.style.left=((a.at+(b.at-a.at)*a.mid)*100)+'%';drawRamp(ramp,g);drawFields();};
      const up=()=>{m.removeEventListener('pointermove',move);m.removeEventListener('pointerup',up);m.removeEventListener('pointercancel',up);draw();emit();};
      m.addEventListener('pointermove',move);m.addEventListener('pointerup',up);m.addEventListener('pointercancel',up);};
    return m;}
  function drag(e,type,s,m){const r=m.parentElement.getBoundingClientRect();let away=false,moved=false;const x0=e.clientX,y0=e.clientY;m.setPointerCapture(e.pointerId);
    const move=ev=>{if(!moved&&Math.hypot(ev.clientX-x0,ev.clientY-y0)<2)return;moved=true;s.at=clamp((ev.clientX-r.left)/r.width,0,1);m.style.left=(s.at*100)+'%';
      away=list(type).length>2&&Math.abs(ev.clientY-(r.top+r.height/2))>28;m.classList.toggle('leaving',away);paintMarker(m);drawRamp(ramp,g);drawMids();};
    const up=()=>{m.removeEventListener('pointermove',move);m.removeEventListener('pointerup',up);m.removeEventListener('pointercancel',up);
      if(away){removeStop(type,list(type).indexOf(s));return;}if(moved){const L=list(type);L.sort((a,b)=>a.at-b.at);sel={type,i:L.indexOf(s)};draw();emit();focusSel();}};
    m.addEventListener('pointermove',move);m.addEventListener('pointerup',up);m.addEventListener('pointercancel',up);}
  function focusSel(){const m=root.querySelector(`.fill-ge-stop[data-type="${sel.type}"][data-index="${sel.i}"]`);if(m)m.focus({preventScroll:true});}
  function addAt(type,track,e){const r=track.getBoundingClientRect(),t=clamp((e.clientX-r.left)/r.width,0,1),c=Ed.gradients.sample(g,t);
    const s=type==='a'?{at:t,a:Math.round(c[3]/255*100)/100,mid:.5}:{at:t,color:U.toHex(c),mid:.5};const L=list(type);L.push(s);L.sort((a,b)=>a.at-b.at);sel={type,i:L.indexOf(s)};mid=null;draw();emit();focusSel();}
  top.onpointerdown=e=>{if(e.button===0&&e.target===top){e.preventDefault();addAt('a',top,e);}};bottom.onpointerdown=e=>{if(e.button===0&&e.target===bottom){e.preventDefault();addAt('c',bottom,e);}};
  function selClasses(){for(const m of root.querySelectorAll('.fill-ge-stop')){const on=!mid&&m.dataset.type===sel.type&&m._stop===list(sel.type)[sel.i];m.classList.toggle('on',on);m.setAttribute('aria-pressed',String(on));}}
  // midpoints beside the selected stop, as Photoshop shows them
  function drawMids(){for(const m of root.querySelectorAll('.fill-ge-mid'))m.remove();const at=mid||sel,L=list(at.type);
    for(const i of [at.i-1,at.i])if(i>=0&&i<L.length-1){const m=midMarker(at.type,i);if(m)(at.type==='a'?top:bottom).append(m);}}
  function refresh(){selClasses();drawMids();drawFields();}
  function draw(keepFields){drawRamp(ramp,g);top.replaceChildren();bottom.replaceChildren();g.alpha.forEach((s,i)=>top.append(marker('a',i,s)));g.stops.forEach((s,i)=>bottom.append(marker('c',i,s)));
    selClasses();drawMids();if(!keepFields)drawFields();}
  root.append(grid,nameRow,bar,fields,el('p','fill-ge-hint','Click above the bar to add an opacity stop, below it to add a color stop. Drag a stop away from the bar to delete it.'));
  draw();return root;}
Ed.registerField('fill-gradient-editor',(f,v,set)=>gradientEditor(v,set));

// The compact gradient control for tool options and dialogs: a swatch (click: Gradient Editor) and a presets menu.
function gradientField(f,value,set){let cur=normalize(value);const wrap=el('span','fill-grad-field');
  const sw=btn('fill-grad-swatch','','Click to edit the gradient'),cv=el('canvas');cv.width=66;cv.height=18;sw.append(cv);sw.setAttribute('aria-label','Edit the gradient');
  const more=btn('fill-grad-more','','Gradient presets');more.setAttribute('aria-label','Gradient presets');more.setAttribute('aria-haspopup','dialog');
  const draw=()=>drawRamp(cv,cur);draw();const red={node:wrap,draw};swatchRedraws.add(red);
  const pick=g=>{cur=normalize(g);draw();set(clone(cur));};
  sw.onclick=async()=>{closePopover();const r=await editGradient(cur);if(r)pick(r);};
  more.onclick=()=>{if(pop&&pop.anchor===more){closePopover();return;}popover(more,presetGrid(g=>{closePopover();pick(g);}),'Gradient presets');};
  track(f,wrap,v=>{cur=normalize(v);draw();});wrap.append(sw,more);return wrap;}
Ed.registerField('gradient',gradientField);

// ----- patterns ---------------------------------------------------------------------------------------------------------
const PATTERN_KEY='ed-patterns';
const BUILTIN_PATTERNS=[
  ['checker','Checkerboard',8,8,g=>{g.fillStyle='#d4dae2';g.fillRect(0,0,8,8);g.fillStyle='#8592a3';g.fillRect(0,0,4,4);g.fillRect(4,4,4,4);}],
  ['stripes','Diagonal Stripes',8,8,g=>{g.fillStyle='#e9edf2';g.fillRect(0,0,8,8);g.fillStyle='#6f7f93';for(let i=-8;i<16;i+=4){g.beginPath();g.moveTo(i,8);g.lineTo(i+8,0);g.lineTo(i+10,0);g.lineTo(i+2,8);g.closePath();g.fill();}}],
  ['dots','Dots',8,8,g=>{g.fillStyle='#eef1f5';g.fillRect(0,0,8,8);g.fillStyle='#55657a';g.beginPath();g.arc(4,4,2,0,7);g.fill();}],
  ['grid','Grid',8,8,g=>{g.fillStyle='#eef1f5';g.fillRect(0,0,8,8);g.fillStyle='#7d8b9c';g.fillRect(0,0,8,1);g.fillRect(0,0,1,8);}],
  ['bricks','Bricks',16,8,g=>{g.fillStyle='#b4563b';g.fillRect(0,0,16,8);g.fillStyle='#e7d9c9';g.fillRect(0,3,16,1);g.fillRect(0,7,16,1);g.fillRect(7,0,1,3);g.fillRect(15,4,1,3);}],
  ['diamonds','Diamonds',8,8,g=>{g.fillStyle='#2f3d52';g.fillRect(0,0,8,8);g.fillStyle='#70e5d4';g.beginPath();g.moveTo(4,0);g.lineTo(8,4);g.lineTo(4,8);g.lineTo(0,4);g.closePath();g.fill();}],
  ['scales','Scales',8,8,g=>{g.fillStyle='#1f6f5c';g.fillRect(0,0,8,8);g.strokeStyle='#9ef0d6';g.lineWidth=1;for(const [x,y] of [[4,0],[0,4],[8,4]]){g.beginPath();g.arc(x,y,4,0,Math.PI);g.stroke();}}],
  ['noise','Noise',32,32,g=>{const D=new ImageData(32,32);for(let i=0;i<1024;i++){const v=110+Math.floor(hash01(i%32,i>>5)*120);D.data.set([v,v,v,255],i*4);}const t=U.canvas(32,32);U.write(t,D);g.drawImage(t,0,0);}]];
const patternCache=new Map();
function builtinCanvas(p){let c=patternCache.get(p[0]);if(!c){c=U.canvas(p[2],p[3]);p[4](c.getContext('2d'));patternCache.set(p[0],c);}return c;}
const decoded=new Map();
function customPatterns(){const v=U.store.get(PATTERN_KEY,[]);return Array.isArray(v)?v.filter(p=>p&&p.id&&p.png):[];}
function customCanvas(p){let e=decoded.get(p.id);if(!e){e={canvas:null};decoded.set(p.id,e);const im=new Image();im.onload=()=>{const c=U.canvas(im.naturalWidth,im.naturalHeight);c.getContext('2d').drawImage(im,0,0);e.canvas=c;Ed.emit('patterns');};im.src=p.png;}return e.canvas;}
Ed.patterns={
  list(){return [...BUILTIN_PATTERNS.map(p=>({id:p[0],name:p[1],width:p[2],height:p[3],canvas:builtinCanvas(p)})),...customPatterns().map(p=>({id:p.id,name:p.name,width:p.w,height:p.h,canvas:customCanvas(p)}))];},
  get(id){return Ed.patterns.list().find(p=>p.id===id)||null;},
  add(name,canvas){const w=canvas.width,h=canvas.height;if(w>512||h>512)throw Error('A pattern can be at most 512 x 512 pixels.');const id='p'+Date.now().toString(36)+Math.floor(Math.random()*1e4);
    const list=customPatterns();list.push({id,name:String(name||'Pattern').slice(0,60),w,h,png:canvas.toDataURL('image/png')});while(list.length>40)list.shift();U.store.set(PATTERN_KEY,list);Ed.emit('patterns');return id;},
  remove(id){U.store.set(PATTERN_KEY,customPatterns().filter(p=>p.id!==id));decoded.delete(id);Ed.emit('patterns');}};
const firstPattern=()=>BUILTIN_PATTERNS[0][0];
// The pattern tiled from the document origin (as Photoshop aligns patterns) over rect R, into a canvas the size of R.
function patternCanvas(id,R){const p=Ed.patterns.get(id)||Ed.patterns.get(firstPattern());const c=U.canvas(R.w,R.h),g=c.getContext('2d');if(!p||!p.canvas)return c;
  const pat=g.createPattern(p.canvas,'repeat');pat.setTransform(new DOMMatrix().translate(-R.x,-R.y));g.fillStyle=pat;g.fillRect(0,0,R.w,R.h);return c;}
function patternField(f,value,set){let cur=value||firstPattern();const wrap=el('span','fill-pat-field'),sw=btn('fill-pat-swatch','',(f.title||'Pattern')+' · click to pick'),cv=el('canvas');cv.width=26;cv.height=18;sw.append(cv);sw.setAttribute('aria-label','Pick a pattern');sw.setAttribute('aria-haspopup','dialog');
  const draw=()=>{const g=cv.getContext("2d"),p=Ed.patterns.get(cur)||Ed.patterns.get(firstPattern());g.clearRect(0,0,26,18);if(p&&p.canvas){g.fillStyle=g.createPattern(p.canvas,'repeat');g.fillRect(0,0,26,18);}sw.title=(p?p.name:'Pattern')+' · click to pick a pattern';};draw();
  sw.onclick=()=>{if(pop&&pop.anchor===sw){closePopover();return;}const box=el('div','fill-swatches fill-pattern-grid');box.setAttribute('role','listbox');box.setAttribute('aria-label','Patterns');
    for(const p of Ed.patterns.list()){const b=btn('fill-swatch','',p.name),c=el('canvas');c.width=c.height=30;b.dataset.pattern=p.id;b.setAttribute('role','option');b.setAttribute('aria-selected',String(p.id===cur));
      if(p.canvas){const g=c.getContext('2d');g.fillStyle=g.createPattern(p.canvas,'repeat');g.fillRect(0,0,30,30);}b.append(c);b.onclick=()=>{cur=p.id;draw();closePopover();set(cur);};box.append(b);}
    const hint=el('p','fill-pop-hint','Edit > Define Pattern adds your own from a selection.');const all=el('div');all.append(box,hint);popover(sw,all,'Patterns');};
  track(f,wrap,v=>{cur=v||firstPattern();draw();});wrap.append(sw);return wrap;}
Ed.registerField('fill-pattern',patternField);Ed.registerField('pattern',patternField);
Ed.on('patterns',()=>{for(const s of syncs)if(s.node.isConnected&&s.node.classList.contains('fill-pat-field'))s.sync(Ed.toolOptions(s.tool)[s.key]);});

// ----- live layers (a type layer keeps its settings in layer.data.text) -------------------------------------------------
const liveKind=L=>L&&L.data&&L.data.text?'text':null;
// A fingerprint of a layer's pixels: the bounds, a hash of the alpha inside them and the mean color. It tells whether other
// tools changed a live layer (painted on it) or only moved it (Move tool), so the layer can follow the move.
function signature(c){const W=c.width,H=c.height,d=U.read(c).data;let x0=W,y0=H,x1=-1,y1=-1;
  for(let y=0;y<H;y++){const r=y*W*4;for(let x=0;x<W;x++)if(d[r+x*4+3]){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}}
  if(x1<0)return{empty:true};let h=2166136261,r=0,g=0,b=0,a=0;
  for(let y=y0;y<=y1;y++)for(let x=x0;x<=x1;x++){const i=(y*W+x)*4,al=d[i+3];h=Math.imul(h^al,16777619);if(al){r+=d[i]*al;g+=d[i+1]*al;b+=d[i+2]*al;a+=al;}}
  return{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1,hash:h>>>0,c:[r/a,g/a,b/a].map(v=>Math.round(v*10)/10)};}
function liveState(L){const k=liveKind(L);if(!k)return{state:'none'};const r=L.data[k].sig;if(!r)return{state:'ok',dx:0,dy:0};const s=signature(L.canvas);
  if(s.empty||r.empty)return s.empty&&r.empty?{state:'ok',dx:0,dy:0}:{state:'edited'};
  if(s.w===r.w&&s.h===r.h&&s.hash===r.hash&&[0,1,2].every(i=>Math.abs(s.c[i]-r.c[i])<=2))return{state:s.x===r.x&&s.y===r.y?'ok':'moved',dx:s.x-r.x,dy:s.y-r.y};
  return{state:'edited'};}
async function confirmBox(title,text,accept){if(typeof confirmAction==='function')return confirmAction(title,text,accept);
  return !!(await Ed.dialog({title,text,fields:[],okLabel:accept}));}
function rasterize(L,label){if(!liveKind(L))return false;const data={...L.data};delete data.text;return Ed.layers.set(L,{data},{history:label||'Rasterize Type'});}
// Photoshop asks before a pixel tool changes a type layer; so do we.
async function ensureRaster(L){if(!liveKind(L))return true;
  const ok=await confirmBox('Rasterize Type',`“${L.name}” is a type layer. It must be rasterized before proceeding, and its text will no longer be editable.`,'Rasterize');
  if(!ok||Ed.layers.get(L)!==L)return false;rasterize(L);return true;}

// ----- Paint Bucket -----------------------------------------------------------------------------------------------------
// Photoshop: pixels within Tolerance of the clicked color on every channel (here premultiplied RGBA, so transparency counts),
// Contiguous = 4-connected (a 1 px diagonal line stops the fill), never outside the selection; Anti-alias smooths the
// stair steps of the filled area's outline (straight edges stay crisp).
function bucketRegion(D,W,H,sx,sy,o){const n=W*H,ok=new Uint8Array(n),tol=o.tol,sel=o.sel,i0=(sy*W+sx)*4;
  if(o.alphaOnly){const sa=D[i0+3];for(let p=0,i=3;p<n;p++,i+=4)if(Math.abs(D[i]-sa)<=tol&&(!sel||sel[i]))ok[p]=1;}
  else{const sa=D[i0+3],sr=D[i0]*sa,sg=D[i0+1]*sa,sb=D[i0+2]*sa,t=tol*255;
    for(let p=0,i=0;p<n;p++,i+=4){const a=D[i+3];if(Math.abs(a-sa)>tol||Math.abs(D[i]*a-sr)>t||Math.abs(D[i+1]*a-sg)>t||Math.abs(D[i+2]*a-sb)>t||(sel&&!sel[i+3]))continue;ok[p]=1;}}
  if(!o.contiguous)return ok;const out=new Uint8Array(n),stack=[sx,sy];
  while(stack.length){const y=stack.pop(),x=stack.pop(),row=y*W;if(out[row+x]||!ok[row+x])continue;let l=x,r=x;
    while(l>0&&ok[row+l-1]&&!out[row+l-1])l--;while(r<W-1&&ok[row+r+1]&&!out[row+r+1])r++;out.fill(1,row+l,row+r+1);
    for(const ny of [y-1,y+1]){if(ny<0||ny>=H)continue;const nr=ny*W;let k=l;while(k<=r){while(k<=r&&!(ok[nr+k]&&!out[nr+k]))k++;if(k>r)break;stack.push(k,ny);while(k<=r&&ok[nr+k]&&!out[nr+k])k++;}}}
  return out;}
function boundsOf(reg,W,H){let x0=W,y0=H,x1=-1,y1=-1;for(let y=0;y<H;y++){const r=y*W;let a=-1,b=-1;for(let x=0;x<W;x++)if(reg[r+x]){if(a<0)a=x;b=x;}if(a>=0){if(a<x0)x0=a;if(b>x1)x1=b;if(y<y0)y0=y;y1=y;}}
  return x1<0?null:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};}
// Coverage 0..255 over R: with anti-aliasing each pixel quarter at a convex corner of the outline keeps half of itself and
// each quarter at a concave corner gains half (the outline is cut diagonally through the pixel centres' midpoints).
function coverageOf(reg,W,H,R,aa){const A=new Uint8ClampedArray(R.w*R.h);
  for(let y=0;y<R.h;y++){const Y=R.y+y;for(let x=0;x<R.w;x++){const X=R.x+x,v=reg[Y*W+X];if(!aa){A[y*R.w+x]=v?255:0;continue;}let c=0;
    for(let q=0;q<4;q++){const xx=clamp(X+(q&1?1:-1),0,W-1),yy=clamp(Y+(q&2?1:-1),0,H-1),h=reg[Y*W+xx],vv=reg[yy*W+X];
      if(v)c+=!h&&!vv?1:2;else if(h&&vv&&reg[yy*W+xx])c+=1;}
    A[y*R.w+x]=Math.round(c*255/8);}}
  return A;}
async function bucket(p){const doc=Ed.doc,L=Ed.layers.active();if(!doc||!L)return;const x=Math.floor(p.x),y=Math.floor(p.y),W=doc.width,H=doc.height;
  if(x<0||y<0||x>=W||y>=H)return;const o=Ed.toolOptions('bucket');if(!await ensureRaster(L))return;const target=doc.target;
  const src=o.all?Ed.render.composite():(target==='mask'?L.mask:L.canvas);if(!src)return;
  const D=U.read(src).data,sel=Ed.selection?U.read(Ed.selection).data:null;if(sel&&!sel[(y*W+x)*4+3])return;   // outside the selection: nothing, as in Photoshop
  const reg=bucketRegion(D,W,H,x,y,{tol:clamp(Math.round(+o.tolerance||0),0,255),contiguous:o.contiguous!==false,alphaOnly:target==='mask'&&!o.all,sel});
  const box=boundsOf(reg,W,H);if(!box)return;const R=U.area(o.antialias!==false?U.grow(box,1):box),A=coverageOf(reg,W,H,R,o.antialias!==false);
  let buf;if(o.source==='pattern'){buf=patternCanvas(o.pattern,R);const m=new ImageData(R.w,R.h);for(let i=0;i<A.length;i++){m.data[i*4]=m.data[i*4+1]=m.data[i*4+2]=255;m.data[i*4+3]=A[i];}
      const mc=U.canvas(R.w,R.h);U.write(mc,m);const g=buf.getContext('2d');g.globalCompositeOperation='destination-in';g.drawImage(mc,0,0);}
  else{const [r,g,b]=U.rgbOf(Ed.fg),m=new ImageData(R.w,R.h);for(let i=0;i<A.length;i++){m.data[i*4]=r;m.data[i*4+1]=g;m.data[i*4+2]=b;m.data[i*4+3]=A[i];}buf=U.canvas(R.w,R.h);U.write(buf,m);}
  await Ed.applyToTarget('Paint Bucket',(g,info)=>{paintInto(info.canvas,buf,R,{mode:modeOf(o.mode),opacity:clamp((+o.opacity||100)/100,0,1),target:info.target,lockAlpha:info.target==='pixels'&&!!info.layer.locks.alpha});},
    {rect:R,op:{tool:'bucket',x,y,tolerance:o.tolerance,source:o.source}});}
Ed.registerTool('bucket',{label:'Paint Bucket',group:'fill',order:2,key:'g',cursor:BUCKET_CURSOR,edits:true,altEyedropper:true,icon:()=>Ed.icon(ICON.bucket),
  options:[{key:'source',label:'',type:'select',options:[{value:'fg',label:'Foreground'},{value:'pattern',label:'Pattern'}],default:'fg',title:'Fill with the foreground color or a pattern'},
    {key:'pattern',label:'',type:'fill-pattern',default:firstPattern(),tool:'bucket',showIf:['source','pattern'],title:'The pattern'},
    {key:'opacity',label:'Opacity',type:'range',min:1,max:100,step:1,unit:'%',default:100,title:'How strongly the fill covers (the digit keys set it: 5 = 50%)'},
    {key:'tolerance',label:'Tolerance',type:'range',min:0,max:255,step:1,default:32,title:'How close a color must be to the clicked one to be filled (0 to 255 on each channel)'},
    {key:'contiguous',label:'Contiguous',type:'checkbox',default:true,title:'Fill only the connected area you click; off fills every matching pixel'},
    hidden('mode','normal'),hidden('antialias',true),hidden('all',false),
    {key:'more',type:'fill-more',tool:'bucket',title:'More Paint Bucket options',items:[
      MODE('bucket'),
      {key:'antialias',label:'Anti-alias',type:'checkbox',title:'Smooth the stair steps along the edge of the filled area'},
      {key:'all',label:'All Layers',type:'checkbox',title:'Find the area from all visible layers, but fill the current layer'}]}],
  onDown(p){bucket(p).catch(e=>{Ed.message('Paint Bucket: '+e.message,true);Ed.test.errors.push({where:'bucket',message:e.message});});return false;}});

// ----- Gradient tool ----------------------------------------------------------------------------------------------------
const KIND_LABEL={linear:'Linear Gradient',radial:'Radial Gradient',angle:'Angle Gradient',reflected:'Reflected Gradient',diamond:'Diamond Gradient'};
Ed.registerField('fill-kinds',(f,value,set)=>{let cur=KINDS.includes(value)?value:'linear';const g=el('span','fill-kinds');g.setAttribute('role','radiogroup');g.setAttribute('aria-label','Gradient style');
  const buttons=KINDS.map(k=>{const b=btn('fill-kind','',KIND_LABEL[k]);b.dataset.kind=k;b.setAttribute('role','radio');b.append(Ed.icon(ICON[k],{size:16,viewBox:'0 0 20 20'}));b.onclick=()=>{cur=k;sync(k);set(k);};
    b.onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();const n=KINDS[(KINDS.indexOf(cur)+(e.key==='ArrowRight'?1:-1)+KINDS.length)%KINDS.length];cur=n;sync(n);set(n);g.querySelector(`[data-kind=${n}]`).focus();}};return b;});
  const sync=v=>{cur=KINDS.includes(v)?v:'linear';for(const b of buttons){const on=b.dataset.kind===cur;b.classList.toggle('on',on);b.setAttribute('aria-checked',String(on));b.tabIndex=on?0:-1;}};
  sync(cur);g.append(...buttons);track(f,g,sync);return g;});
function constrain(a,p,shift){if(!shift)return{x:p.x,y:p.y};const dx=p.x-a.x,dy=p.y-a.y,ang=Math.round(Math.atan2(dy,dx)/(Math.PI/4))*(Math.PI/4),ux=Math.cos(ang),uy=Math.sin(ang),d=dx*ux+dy*uy;
  return{x:a.x+ux*d,y:a.y+uy*d};}
async function runGradient(a,b){const o=Ed.toolOptions('gradient'),kind=KINDS.includes(o.kind)?o.kind:'linear',g=normalize(o.gradient);
  return applyPaint('Gradient',info=>{const R=info.rect,c=U.canvas(R.w,R.h);U.write(c,gradientImage(g,kind,a.x,a.y,b.x,b.y,{rect:R,reverse:!!o.reverse,dither:o.dither!==false,transparency:o.transparency!==false}));return{canvas:c,rect:R};},
    {mode:modeOf(o.mode),opacity:clamp((+o.opacity||100)/100,0,1),op:{tool:'gradient',kind}});}
function guideLine(ctx,v,a,b){const A=v.toScreen(a.x,a.y),B=v.toScreen(b.x,b.y);ctx.lineCap='round';ctx.beginPath();ctx.moveTo(A.x,A.y);ctx.lineTo(B.x,B.y);ctx.lineWidth=3;ctx.strokeStyle='rgba(0,0,0,.6)';ctx.stroke();
  ctx.lineWidth=1;ctx.strokeStyle='#fff';ctx.stroke();for(const P of [A,B]){ctx.beginPath();ctx.arc(P.x,P.y,3.5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.lineWidth=1;ctx.strokeStyle='#000';ctx.stroke();}}
Ed.registerTool('gradient',{label:'Gradient',group:'fill',order:1,key:'g',cursor:'crosshair',edits:true,icon:()=>Ed.icon(ICON.gradient),
  options:[{key:'gradient',label:'',type:'gradient',default:BUILTIN_GRADIENTS()[0],tool:'gradient',title:'The gradient'},{key:'kind',label:'',type:'fill-kinds',default:'linear',tool:'gradient'},
    MODE('gradient'),{key:'opacity',label:'Opacity',type:'range',min:1,max:100,step:1,unit:'%',default:100,title:'How strongly the gradient covers (the digit keys set it: 5 = 50%)'},
    hidden('reverse',false),hidden('dither',true),hidden('transparency',true),
    {key:'more',type:'fill-more',tool:'gradient',title:'More Gradient options',items:[
      {key:'reverse',label:'Reverse',type:'checkbox',title:'Swap the start and end of the gradient'},
      {key:'dither',label:'Dither',type:'checkbox',title:'Add a faint noise so smooth blends show no bands'},
      {key:'transparency',label:'Transparency',type:'checkbox',title:'Use the gradient’s opacity stops'}]}],
  onDown(p){this.line={a:{x:p.x,y:p.y},b:{x:p.x,y:p.y}};Ed.view.redrawOverlay();},
  onMove(p){if(!this.line)return;this.line.b=constrain(this.line.a,p,p.shiftKey);Ed.view.redrawOverlay();},
  onUp(p){const s=this.line;this.line=null;Ed.view.redrawOverlay();if(!s)return;s.b=constrain(s.a,p,p.shiftKey);if(Math.hypot(s.b.x-s.a.x,s.b.y-s.a.y)<.5)return;
    runGradient(s.a,s.b).catch(e=>{Ed.message('Gradient: '+e.message,true);Ed.test.errors.push({where:'gradient',message:e.message});});},
  onCancel(){this.line=null;Ed.view.redrawOverlay();},onDeactivate(){this.line=null;},
  drawOverlay(ctx,v){if(this.line)guideLine(ctx,v,this.line.a,this.line.b);}});

// ----- Edit > Fill, Edit > Stroke and the quick fills -------------------------------------------------------------------
const CONTENTS=[['fg','Foreground Color'],['bg','Background Color'],['color','Color...'],['pattern','Pattern'],['-'],['black','Black'],['gray','50% Gray'],['white','White']];
const contentColor=c=>({fg:Ed.fg,bg:Ed.bg,color:isHex(c.color)?c.color:Ed.fg,black:'#000000',gray:'#808080',white:'#ffffff'})[c.use]||Ed.fg;
Ed.registerField('fill-contents',(f,value,set)=>{let v={use:'fg',color:Ed.fg,pattern:firstPattern(),...(value||{})};const wrap=el('span','fill-contents'),s=el('select');s.name='contents';s.setAttribute('aria-label','Contents');
  for(const [val,label] of CONTENTS){if(val==='-'){s.append(el('hr'));continue;}const o=el('option','',label);o.value=val;s.append(o);}s.value=v.use;
  const c=el('input');c.type='color';c.name='contents-color';c.value=contentColor({...v,use:'color'});c.title='The fill color';
  const pat=patternField({title:'Pattern'},v.pattern,id=>{v={...v,pattern:id};set({...v});});
  const show=()=>{c.hidden=v.use!=='color';pat.hidden=v.use!=='pattern';};show();
  s.onchange=()=>{v={...v,use:s.value};show();set({...v});};c.oninput=c.onchange=()=>{v={...v,color:c.value};set({...v});};
  wrap.append(s,c,pat);return wrap;});
function fillParams(p={}){const c=typeof p.contents==='object'&&p.contents?p.contents:{use:p.use||p.contents||'fg',color:p.color,pattern:p.pattern};
  return{use:CONTENTS.some(x=>x[0]===c.use)?c.use:'fg',color:c.color,pattern:c.pattern||firstPattern(),mode:modeOf(p.mode),opacity:clamp(p.opacity==null?100:+p.opacity,0,100),preserve:!!p.preserve};}
async function doFill(p,label='Fill'){const doc=Ed.doc;if(!doc)return false;const why=Ed.editable({view:false});if(why){Ed.message(why,true);return false;}
  return applyPaint(label,info=>{const R=info.rect;if(p.use==='pattern')return{canvas:patternCanvas(p.pattern,R),rect:R};const c=U.canvas(R.w,R.h),g=c.getContext('2d');g.fillStyle=contentColor(p);g.fillRect(0,0,R.w,R.h);return{canvas:c,rect:R};},
    {mode:p.mode,opacity:p.opacity/100,preserve:p.preserve,op:{tool:'fill',use:p.use,mode:p.mode,opacity:p.opacity}});}
async function fillCommand(params){if(!Ed.doc)return false;if(params)return doFill(fillParams(params));
  const why=Ed.editable({view:false});if(why){Ed.message(why,true);return false;}const last=U.store.get('ed-fill-last',{})||{};
  const r=await Ed.dialog({title:'Fill',text:'Tip: Alt+Backspace fills with the foreground color and Ctrl+Backspace with the background color; add Shift to keep transparent pixels clear.',
    fields:[{key:'contents',label:'Contents',type:'fill-contents',default:{use:'fg',color:Ed.fg,pattern:firstPattern()}},modeField(),
      {key:'opacity',label:'Opacity',type:'range',min:1,max:100,step:1,unit:'%',default:100},{key:'preserve',label:'Preserve Transparency',type:'checkbox',default:false,title:'Fill only where the layer already has pixels'}],
    values:{contents:{use:'fg',color:Ed.fg,pattern:firstPattern(),...(last.contents||{})},mode:last.mode||'normal',opacity:last.opacity??100,preserve:!!last.preserve}});
  if(!r)return false;U.store.set('ed-fill-last',r);return doFill(fillParams(r));}
// Euclidean distance transform (Felzenszwalb): squared distance from each cell to the nearest cell where f is 0.
function edt(f,W,H){const INF=1e10,n=Math.max(W,H),v=new Int32Array(n),z=new Float64Array(n+1),fx=new Float64Array(n),dd=new Float64Array(n);
  const one=len=>{let k=0;v[0]=0;z[0]=-INF;z[1]=INF;for(let q=1;q<len;q++){let s=((fx[q]+q*q)-(fx[v[k]]+v[k]*v[k]))/(2*q-2*v[k]);while(s<=z[k]){k--;s=((fx[q]+q*q)-(fx[v[k]]+v[k]*v[k]))/(2*q-2*v[k]);}k++;v[k]=q;z[k]=s;z[k+1]=INF;}
    k=0;for(let q=0;q<len;q++){while(z[k+1]<q)k++;dd[q]=(q-v[k])*(q-v[k])+fx[v[k]];}};
  for(let x=0;x<W;x++){for(let y=0;y<H;y++)fx[y]=f[y*W+x];one(H);for(let y=0;y<H;y++)f[y*W+x]=dd[y];}
  for(let y=0;y<H;y++){const r=y*W;for(let x=0;x<W;x++)fx[x]=f[r+x];one(W);for(let x=0;x<W;x++)f[r+x]=dd[x];}return f;}
// Coverage of a stroke along the 50% edge of `alpha` (document-sized RGBA bytes, alpha used) in a band Inside / Center /
// Outside of `width`. Returns {R, A} or null when there is nothing inside.
function strokeCoverage(alpha,W,H,width,where){const inside=new Uint8Array(W*H);for(let i=0,p=3;i<inside.length;i++,p+=4)inside[i]=alpha[p]>=128?1:0;const box=boundsOf(inside,W,H);if(!box)return null;
  const pad=Math.ceil(where==='inside'?1:where==='center'?width/2+1:width+1)+1,G=U.area(U.grow(box,pad)),gw=G.w+2,gh=G.h+2,fin=new Float64Array(gw*gh),fout=new Float64Array(gw*gh);
  for(let y=0;y<gh;y++)for(let x=0;x<gw;x++){const X=G.x+x-1,Y=G.y+y-1,inn=X>=0&&Y>=0&&X<W&&Y<H&&inside[Y*W+X];fin[y*gw+x]=inn?1e10:0;fout[y*gw+x]=inn?0:1e10;}
  edt(fin,gw,gh);if(where!=='inside')edt(fout,gw,gh);const A=new Uint8ClampedArray(G.w*G.h),half=where==='center'?width/2:width;
  for(let y=0;y<G.h;y++)for(let x=0;x<G.w;x++){const j=(y+1)*gw+x+1,X=G.x+x,Y=G.y+y,inn=inside[Y*W+X];let c=0;
    if(inn){if(where!=='outside')c=clamp(half-Math.sqrt(fin[j])+1,0,1);}else if(where!=='inside')c=clamp(half-Math.sqrt(fout[j])+1,0,1);A[y*G.w+x]=Math.round(c*255);}
  return{R:G,A};}
async function doStroke(p){const doc=Ed.doc,L=Ed.layers.active();if(!doc||!L)return false;const why=Ed.editable({view:false});if(why){Ed.message(why,true);return false;}
  if(!await ensureRaster(L))return false;const W=doc.width,H=doc.height,target=doc.target,width=clamp(+p.width||1,1,250),where=['inside','center','outside'].includes(p.location)?p.location:'center';
  const src=Ed.selection||L.canvas,cov=strokeCoverage(U.read(src).data,W,H,width,where);if(!cov){Ed.message(Ed.selection?'The selection is too faint to stroke.':'This layer has no pixels to stroke. Select an area first.');return false;}
  const {R,A}=cov,[r,g,b]=U.rgbOf(isHex(p.color)?p.color:Ed.fg),m=new ImageData(R.w,R.h);for(let i=0;i<A.length;i++){m.data[i*4]=r;m.data[i*4+1]=g;m.data[i*4+2]=b;m.data[i*4+3]=A[i];}
  const buf=U.canvas(R.w,R.h);U.write(buf,m);const ref=target==='mask'?L.mask:L.canvas;
  Ed.history.record('Stroke',tx=>{tx.touch(L,target,R);paintInto(ref,buf,R,{mode:modeOf(p.mode),opacity:clamp((p.opacity==null?100:+p.opacity)/100,0,1),target,lockAlpha:target==='pixels'&&(!!L.locks.alpha||!!p.preserve)});},
    {rect:R,op:{tool:'stroke',width,location:where}});return true;}
async function strokeCommand(params){if(!Ed.doc)return false;if(params)return doStroke(params);const why=Ed.editable({view:false});if(why){Ed.message(why,true);return false;}
  const last=U.store.get('ed-stroke-last',{})||{};
  const r=await Ed.dialog({title:'Stroke',text:Ed.selection?'Draws a line along the edge of the selection.':'There is no selection, so the line follows the edge of this layer’s pixels.',
    fields:[{key:'width',label:'Width',type:'range',min:1,max:250,step:1,unit:'px',default:1},{key:'color',label:'Color',type:'color',default:Ed.fg},
      {key:'location',label:'Location',type:'segmented',options:[{value:'inside',label:'Inside'},{value:'center',label:'Center'},{value:'outside',label:'Outside'}],default:'center'},
      modeField(),{key:'opacity',label:'Opacity',type:'range',min:1,max:100,step:1,unit:'%',default:100},{key:'preserve',label:'Preserve Transparency',type:'checkbox',default:false,title:'Draw only where the layer already has pixels'}],
    values:{width:last.width??1,color:Ed.fg,location:last.location||'center',mode:last.mode||'normal',opacity:last.opacity??100,preserve:!!last.preserve}});
  if(!r)return false;U.store.set('ed-stroke-last',{width:r.width,location:r.location,mode:r.mode,opacity:r.opacity,preserve:r.preserve});return doStroke(r);}
Ed.registerMenu('Edit/Fill...',{order:60,separatorBefore:true,shortcut:['Shift+F5','Shift+Backspace'],enabled:()=>!!Ed.doc,action:p=>fillCommand(p)});
Ed.registerMenu('Edit/Stroke...',{order:61,enabled:()=>!!Ed.doc,action:p=>strokeCommand(p)});
async function definePattern(params){const doc=Ed.doc;if(!doc)return false;const b=Ed.select.bounds()||{x:0,y:0,w:doc.width,h:doc.height};
  if(b.w>512||b.h>512){Ed.message('A pattern can be at most 512 x 512 pixels. Select a smaller area first.',true);return false;}
  const name=params&&params.name?params.name:(await Ed.dialog({title:'Pattern Name',fields:[{key:'name',label:'Name',type:'text',default:'Pattern '+(customPatterns().length+1)}],okLabel:'OK'})||{}).name;if(!name)return false;
  const all=Ed.render.composite({region:b}),c=U.canvas(b.w,b.h);c.getContext('2d').drawImage(all,b.x,b.y,b.w,b.h,0,0,b.w,b.h);const id=Ed.patterns.add(name,c);Ed.message(`Saved the pattern “${name}”. Pick it in the Paint Bucket or Edit > Fill.`);return id;}
Ed.registerMenu('Edit/Define Pattern...',{order:90,separatorBefore:true,enabled:()=>!!Ed.doc,action:p=>definePattern(p)});
// Alt+Backspace (or Alt+Delete) fills with the foreground color, Ctrl+Backspace with the background color; with Shift the
// layer's transparent pixels stay clear (Photoshop's Preserve Transparency).
document.addEventListener('keydown',e=>{if(e.defaultPrevented||(e.key!=='Backspace'&&e.key!=='Delete'))return;const alt=e.altKey,ctrl=e.ctrlKey||e.metaKey;if(alt===ctrl||!Ed.doc)return;
  if(typing(e)||modalOpen()||busy())return;e.preventDefault();doFill({use:alt?'fg':'bg',mode:'normal',opacity:100,preserve:e.shiftKey}).catch(x=>Ed.message(x.message,true));});

// ----- shared with fill-shapes.js and fill-type.js ----------------------------------------------------------------------
Ed.fillKit={el,btn,isHex,popover,closePopover,track,applyPaint,liveKind,signature,liveState,confirmBox,rasterize};
if(base)for(const f of ['fill-shapes.js','fill-type.js']){const s=document.createElement('script');s.src=new URL(f,base).href;s.async=false;document.head.append(s);}
})();
