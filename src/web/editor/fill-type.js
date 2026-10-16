'use strict';
/* The Type tool for the Asset Lab editor (T), loaded by editor/fill.js. Click to start a text: the click is the baseline
   of the first line, alignment is around that point. Type (a hidden text field takes the keys, so IME, paste and the
   arrow keys work); Shift+Enter starts a new line; Enter, Ctrl+Enter, clicking away or another tool commits; Escape
   cancels. Clicking an existing type layer edits it (the caret goes where you click); changing the font, style, size,
   color, alignment or anti-aliasing while editing applies to the whole text, as Photoshop's options bar does.
   A type layer keeps everything in layer.data.text = {text, font, style, size, color, align, antialias, x, y, sig}, saved
   with the document, so it stays editable after a reload. sig fingerprints the drawn pixels: a layer only moved (Move
   tool) follows the move; a layer other tools painted on asks before its text is drawn again. Layer > Rasterize > Type
   (and layers.js' Rasterize > Layer through Ed.registerRasterizer) turns it into plain pixels; the fill tools ask first.
   Fonts: the common fonts installed on this PC (nothing is downloaded). Leading is 120% of the size (Photoshop's Auto). */
(()=>{
const Ed=window.Ed,K=Ed&&Ed.fillKit;if(!Ed||!K)return;const U=Ed.util,{clamp}=U,{el,btn,isHex}=K;

// ----- fonts --------------------------------------------------------------------------------------------------------------
const CANDIDATES=['Arial','Arial Black','Bahnschrift','Calibri','Cambria','Candara','Comic Sans MS','Consolas','Constantia','Corbel','Courier New',
  'Franklin Gothic Medium','Gabriola','Georgia','Impact','Lucida Console','Palatino Linotype','Segoe Print','Segoe Script','Segoe UI','Tahoma',
  'Times New Roman','Trebuchet MS','Verdana','Helvetica','DejaVu Sans','Liberation Sans','Noto Sans','Ubuntu'];
const mctx=U.canvas(8,8).getContext('2d');
function installed(f){const s='mmmmmmmmmlliWW@#1';for(const base of ['monospace','serif','sans-serif']){mctx.font='72px '+base;const w=mctx.measureText(s).width;mctx.font=`72px "${f}", ${base}`;if(mctx.measureText(s).width!==w)return true;}return false;}
let FONTS=CANDIDATES.filter(installed);if(!FONTS.length)FONTS=['sans-serif'];
const DEFAULT_FONT=FONTS.includes('Arial')?'Arial':FONTS[0];
const STYLES=[['regular','Regular'],['bold','Bold'],['italic','Italic'],['bold italic','Bold Italic']];
const ALIGN=['left','center','right'];
const stack=f=>/^(serif|sans-serif|monospace)$/.test(f)?f:`"${String(f).replace(/["\\]/g,'')}", sans-serif`;
const fontCss=t=>`${t.style.includes('italic')?'italic ':''}${t.style.includes('bold')?'bold ':''}${t.size}px ${stack(t.font)}`;

// ----- layout and drawing -------------------------------------------------------------------------------------------------
function clean(t){t=t&&typeof t==='object'?t:{};return{text:String(t.text??'').replace(/\r\n?/g,'\n').slice(0,4000),font:String(t.font||DEFAULT_FONT).slice(0,80),
  style:STYLES.some(s=>s[0]===t.style)?t.style:'regular',size:clamp(Math.round(+t.size||24),1,1000),color:isHex(t.color)?t.color.toLowerCase():'#ffffff',
  align:ALIGN.includes(t.align)?t.align:'left',antialias:t.antialias!==false,x:Math.round(+t.x||0),y:Math.round(+t.y||0)};}
const lead=t=>Math.round(t.size*1.2);
// Lines with their left edges and widths, and the box around them (ascent and descent of the font, not of the glyphs).
function layout(t){mctx.font=fontCss(t);const m=mctx.measureText('Mg'),asc=m.fontBoundingBoxAscent||t.size*.8,desc=m.fontBoundingBoxDescent||t.size*.2;
  const lines=t.text.split('\n').map((s,i)=>{const w=mctx.measureText(s).width,x=t.align==='center'?t.x-w/2:t.align==='right'?t.x-w:t.x;return{s,w,x,y:t.y+i*lead(t)};});
  const x0=Math.min(...lines.map(l=>l.x)),x1=Math.max(...lines.map(l=>l.x+l.w));
  return{lines,asc,desc,box:{x:x0,y:t.y-asc,w:Math.max(x1-x0,1),h:asc+desc+(lines.length-1)*lead(t)}};}
function render(t,into){const doc=Ed.doc,c=into||U.canvas(doc.width,doc.height),g=c.getContext('2d');g.setTransform(1,0,0,1,0,0);g.globalCompositeOperation='source-over';g.globalAlpha=1;
  g.clearRect(0,0,c.width,c.height);g.font=fontCss(t);g.fillStyle=t.color;g.textAlign=t.align;g.textBaseline='alphabetic';
  t.text.split('\n').forEach((s,i)=>{if(s)g.fillText(s,t.x,t.y+i*lead(t));});
  if(!t.antialias){const R=U.area(U.grow(boxRect(t),2));if(R){const D=U.read(c,R),d=D.data;for(let i=3;i<d.length;i+=4)d[i]=d[i]>=128?255:0;U.write(c,D,R);}}
  return c;}
function boxRect(t){const b=layout(t).box,x=Math.floor(b.x),y=Math.floor(b.y);return{x,y,w:Math.ceil(b.x+b.w)-x,h:Math.ceil(b.y+b.h)-y};}
const nameOf=text=>(text.split('\n').map(s=>s.trim()).find(Boolean)||'Text').slice(0,40);
const STYLE_KEYS=['font','style','size','color','align','antialias'];

// ----- the editing session --------------------------------------------------------------------------------------------------
let sess=null;const ta=el('textarea','fill-type-input');ta.setAttribute('aria-label','Text');ta.spellcheck=false;ta.setAttribute('autocomplete','off');ta.tabIndex=-1;
document.body.append(ta);
function redraw(){if(!sess)return;render(sess.t,sess.canvas);if(sess.layer)Ed.preview.update();placeInput();Ed.view.redrawOverlay();}
function placeInput(){if(!sess)return;const c=caretPos();const P=Ed.view.toClient(c.x,c.y);ta.style.left=Math.round(clamp(P.x,0,innerWidth-4))+'px';ta.style.top=Math.round(clamp(P.y,0,innerHeight-24))+'px';}
function focusInput(){if(sess&&document.activeElement!==ta)ta.focus({preventScroll:true});}
function start(layer,t,index){if(sess)commit();const doc=Ed.doc;if(!doc)return;
  sess={layer,t,orig:layer?JSON.stringify(clean(layer.data.text)):'',canvas:U.canvas(doc.width,doc.height),moved:false};
  ta.value=t.text;const i=index==null?t.text.length:index;ta.setSelectionRange(i,i);
  syncOptions(t);render(t,sess.canvas);if(layer)Ed.preview.layer({layer,canvas:sess.canvas,target:'pixels'});
  focusInput();setTimeout(focusInput,0);placeInput();Ed.view.redrawOverlay();}
function syncOptions(t){const o=Ed.toolOptions('type'),diff={};for(const k of STYLE_KEYS)if(o[k]!==t[k])diff[k]=t[k];if(Object.keys(diff).length){applying=true;try{Ed.setToolOptions('type',diff);}finally{applying=false;}}}
function finish(){const s=sess;sess=null;if(document.activeElement===ta)ta.blur();if(s&&s.layer)Ed.preview.clear();Ed.view.redrawOverlay();return s;}
function cancel(){finish();}
function commit(){const s=finish();if(!s)return false;const t=clean(s.t),doc=Ed.doc;if(!doc)return false;
  if(!s.layer){if(!t.text.trim())return false;const c=render(t);t.sig=K.signature(c);
    Ed.layers.add({name:nameOf(t.text),canvas:c,data:{text:t},history:'Type Tool',op:{tool:'type'}});return true;}
  const L=s.layer;if(Ed.layers.get(L)!==L)return false;
  if(!t.text.trim()){if(Ed.layers.count()>1){Ed.layers.remove(L);return true;}return false;}
  if(JSON.stringify(t)===s.orig&&!s.moved)return false;
  const c=render(t);t.sig=K.signature(c);const old=clean(L.data.text),name=L.name===nameOf(old.text)?nameOf(t.text):L.name;
  Ed.history.record('Edit Type Layer',tx=>{tx.touch(L,'pixels',{x:0,y:0,w:doc.width,h:doc.height});const g=L.canvas.getContext('2d');g.save();g.setTransform(1,0,0,1,0,0);
    g.globalCompositeOperation='copy';g.drawImage(c,0,0);g.restore();Ed.layers.set(L,{data:{...L.data,text:t},name},{history:false});Ed.update();},{op:{tool:'type'}});return true;}
ta.addEventListener('input',()=>{if(!sess)return;sess.t.text=ta.value.replace(/\r\n?/g,'\n');redraw();});
ta.addEventListener('keydown',e=>{if(!sess)return;
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();cancel();return;}
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();e.stopPropagation();commit();return;}
  e.stopPropagation();});
for(const ev of ['keyup','select','click'])ta.addEventListener(ev,()=>{if(sess){placeInput();Ed.view.redrawOverlay();}});
document.addEventListener('selectionchange',()=>{if(sess&&document.activeElement===ta)Ed.view.redrawOverlay();});
// Anything else that changes the document ends the edit (the typed text is dropped, as when Photoshop's edit is cancelled).
// (commit() ends the session before it records its own state.)
Ed.on('history',()=>{if(sess)cancel();});Ed.on('open',()=>{if(sess)finish();});

// The caret: the image position of the text field's caret (top of the line, its height).
function caretPos(){const s=sess,lay=layout(s.t),i=clamp(ta.selectionEnd??s.t.text.length,0,s.t.text.length),before=s.t.text.slice(0,i).split('\n'),n=before.length-1,line=lay.lines[n];
  mctx.font=fontCss(s.t);return{x:line.x+mctx.measureText(before[n]).width,y:line.y-lay.asc,h:lay.asc+lay.desc};}
// The text index nearest an image point.
function indexAt(t,p){const lay=layout(t),n=clamp(Math.floor((p.y-(t.y-lay.asc))/lead(t)),0,lay.lines.length-1);mctx.font=fontCss(t);
  let base=0;for(let k=0;k<n;k++)base+=lay.lines[k].s.length+1;const L=lay.lines[n];let best=0,bd=Infinity;
  for(let c=0;c<=L.s.length;c++){const d=Math.abs(L.x+mctx.measureText(L.s.slice(0,c)).width-p.x);if(d<bd){bd=d;best=c;}}return base+best;}
const inBox=(t,p,m=3)=>{const b=layout(t).box;return p.x>=b.x-m&&p.x<=b.x+b.w+m&&p.y>=b.y-m&&p.y<=b.y+b.h+m;};

// Where a type layer's text is now: the Move tool shifts the pixels, so a layer whose pixels are the drawn text moved by
// (dx, dy) is edited there; 'edited' = other tools changed the pixels. Cached per layer version (a read per change).
const states=new WeakMap();
function stateOf(L){const c=states.get(L),sig=JSON.stringify(L.data.text.sig||null);if(c&&c.v===L.version&&c.sig===sig)return c.st;const st=K.liveState(L);states.set(L,{v:L.version,sig,st});return st;}
function placed(L){const t=clean(L.data.text),st=stateOf(L);return st.state==='moved'?{t:{...t,x:t.x+st.dx,y:t.y+st.dy},moved:true,edited:false}:{t,moved:false,edited:st.state==='edited'};}
// The type layer under a point, top first.
function hit(p){const list=Ed.layers.list();for(let i=list.length-1;i>=0;i--){const L=list[i];if(!L.visible||K.liveKind(L)!=='text')continue;const h=placed(L);if(inBox(h.t,p))return{L,...h};}return null;}
async function editLayer(h,p){const L=h.L;if(L.locks&&L.locks.all){Ed.message(`“${L.name}” is locked.`,true);return;}
  if(h.edited){const ok=await K.confirmBox('Edit Type Layer',`“${L.name}” was changed by other tools. Editing its text draws it again and drops those changes.`,'Edit Text');
    if(!ok||Ed.layers.get(L)!==L)return;}
  if(Ed.layers.active()!==L)Ed.layers.setActive(L);start(L,{...h.t},indexAt(h.t,p));if(sess)sess.moved=h.moved||h.edited;}

// ----- options bar ------------------------------------------------------------------------------------------------------------
const ALIGN_ICON={left:['M4 6h16','M4 10h10','M4 14h16','M4 18h10'],center:['M4 6h16','M7 10h10','M4 14h16','M7 18h10'],right:['M4 6h16','M10 10h10','M4 14h16','M10 18h10']};
const ALIGN_LABEL={left:'Left align text',center:'Center text',right:'Right align text'};
Ed.registerField('fill-align',(f,value,set)=>{const g=el('span','fill-align');g.setAttribute('role','radiogroup');g.setAttribute('aria-label','Alignment');
  const bs=ALIGN.map(a=>{const b=btn('','',ALIGN_LABEL[a]);b.dataset.align=a;b.setAttribute('role','radio');b.setAttribute('aria-label',ALIGN_LABEL[a]);b.append(Ed.icon(ALIGN_ICON[a],{size:16}));b.onclick=()=>{sync(a);set(a);};return b;});
  const sync=v=>{for(const b of bs){const on=b.dataset.align===v;b.classList.toggle('on',on);b.setAttribute('aria-checked',String(on));}};sync(ALIGN.includes(value)?value:'left');
  g.append(...bs);K.track(f,g,sync);return g;});
let applying=false;
Ed.registerTool('type',{label:'Type',group:'type',key:'t',cursor:'text',icon:()=>Ed.icon(['M5 5h14','M12 5v14','M9 19h6']),
  options:[{key:'font',label:'',type:'select',options:FONTS.map(f=>({value:f,label:f})),default:DEFAULT_FONT,title:'Font family',translate:false},
    {key:'style',label:'',type:'select',options:STYLES.map(([value,label])=>({value,label})),default:'regular',title:'Font style'},
    {key:'size',label:'',type:'number',min:1,max:1000,step:1,unit:'px',default:24,title:'Font size'},
    {key:'antialias',label:'Anti-alias',type:'checkbox',default:true,title:'Smooth edges; off keeps every pixel fully in or out (pixel art)'},
    {key:'align',label:'',type:'fill-align',default:'left',tool:'type',title:'Alignment'},
    {key:'color',label:'',type:'color',default:'#ffffff',title:'Text color'}],
  onOptions(values){if(applying||!sess)return;for(const k of STYLE_KEYS)sess.t[k]=values[k];sess.t=clean(sess.t);redraw();setTimeout(focusInput,0);},
  // The shell blurs the focused element on pointerdown and the mouse's default action would focus the page: keep the
  // text field focused (no compatibility mousedown) and focus it again after the event.
  onDown(p,e){if(!Ed.doc)return false;if(e&&e.preventDefault)e.preventDefault();setTimeout(focusInput,0);
    if(sess){if(inBox(sess.t,p,6)){const i=indexAt(sess.t,p);ta.setSelectionRange(i,i);focusInput();placeInput();Ed.view.redrawOverlay();return false;}commit();return false;}
    const why=Ed.view.blocked;if(why){Ed.message(why,true);return false;}
    const h=hit(p);if(h){editLayer(h,p).catch(e=>Ed.message(e.message,true));return false;}
    const o=Ed.toolOptions('type');start(null,clean({...o,text:'',x:Math.round(p.x),y:Math.round(p.y)}));return false;},
  onCommit(){commit();},onCancel(){cancel();},onDeactivate(){commit();},
  drawOverlay(ctx,v){const s=sess;if(!s)return;const lay=layout(s.t),b=lay.box;
    if(!s.layer){const R=U.area(U.grow(boxRect(s.t),2));if(R){const A=v.toScreen(R.x,R.y);ctx.save();ctx.imageSmoothingEnabled=v.scale<1;ctx.drawImage(s.canvas,R.x,R.y,R.w,R.h,A.x,A.y,R.w*v.scale,R.h*v.scale);ctx.restore();}}
    const A=v.toScreen(b.x,b.y),B=v.toScreen(b.x+b.w,b.y+b.h);ctx.save();ctx.setLineDash([4,3]);ctx.lineWidth=1;ctx.strokeStyle='rgba(0,0,0,.7)';ctx.strokeRect(Math.round(A.x)-1.5,Math.round(A.y)-1.5,Math.round(B.x-A.x)+3,Math.round(B.y-A.y)+3);
    ctx.lineDashOffset=3.5;ctx.strokeStyle='#ffffff';ctx.strokeRect(Math.round(A.x)-1.5,Math.round(A.y)-1.5,Math.round(B.x-A.x)+3,Math.round(B.y-A.y)+3);ctx.restore();
    if(document.activeElement===ta){const c=caretPos(),P=v.toScreen(c.x,c.y),Q=v.toScreen(c.x,c.y+c.h);ctx.fillStyle='#000';ctx.fillRect(Math.round(P.x)-1,P.y,3,Q.y-P.y);ctx.fillStyle='#fff';ctx.fillRect(Math.round(P.x),P.y,1,Q.y-P.y);}}});

// ----- rasterize, badge -------------------------------------------------------------------------------------------------------
const rasterDef={label:'Type',test:L=>K.liveKind(L)==='text',rasterize(L){const data={...L.data};delete data.text;Ed.layers.set(L,{data},{history:false});}};
if(Ed.registerRasterizer)Ed.registerRasterizer('type',rasterDef);else Ed.ready.then(()=>{if(Ed.registerRasterizer)Ed.registerRasterizer('type',rasterDef);});
Ed.registerMenu('Layer/Rasterize/Type',{order:54,enabled:()=>K.liveKind(Ed.layers.active())==='text',action:()=>{if(sess)commit();return K.rasterize(Ed.layers.active(),'Rasterize Type');}});
if(Ed.registerLayerBadge)Ed.registerLayerBadge(L=>{if(K.liveKind(L)!=='text')return null;const b=el('span','fill-badge','T');b.title='Type layer: click its text with the Type tool to edit it';return b;});
Ed.typeLayers={render:t=>render(clean(t)),layout:t=>layout(clean(t)),fonts:()=>FONTS.slice(),editing:()=>!!sess};
})();
