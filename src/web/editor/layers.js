'use strict';
/* =====================================================================================================================
   LAYERS  (editor/layers.js)  ·  the Photoshop-style Layers panel, the Layer menu commands, layer and clipping masks,
   merging and the extra blend modes. Built only on window.Ed (editor/core.js documents it); the editor works without it.

   BLEND MODES  Dissolve (a pixel shows fully or not at all: a fixed per-pixel threshold against its alpha, so the pattern
     stays put while painting), Linear Burn, Darker Color, Lighter Color (by luminosity .3/.59/.11), Vivid Light, Linear
     Light, Pin Light, Hard Mix, Subtract, Divide: B(Cb, Cs) through the core's W3C general formula.
   PANEL  replaces the core's 'layers' panel (same ids and classes): blend mode + Opacity + the panel menu, locks + Fill,
     the layer list (eye, clip arrow, thumbnail, mask thumbnail, name, badges) and the footer (fx when styles.js offers
     Layer Style commands, mask, fill/adjustment layer when adjust.js offers them, new, duplicate, delete).
     Ctrl-click a row adds or removes it from the selection, Shift-click selects a range; Opacity, Fill, blend mode, locks,
     Duplicate, Delete, Merge and Arrange act on every selected layer. Drag rows to reorder (a clipping base takes its
     clipped layers along), onto New to duplicate them, onto the bin to delete them. Alt-click an eye shows only that
     layer (again restores the others); drag down the eyes to show or hide many. Alt-click the line between two rows
     makes or releases a clipping mask. Ctrl-click a thumbnail loads its pixels (or the mask) as a selection (Shift adds,
     Alt subtracts, Shift+Alt intersects). Shift-click a mask thumbnail disables it, Alt-click shows the mask alone,
     Shift+Alt-click or \ shows it as a red overlay. Double-click the name renames; the rest of the row opens Blending
     Options (when styles.js registers 'Layer/Layer Style/Blending Options...') or, for an adjustment layer, the
     Properties panel (adjust.js). Right-click a row, eye, thumbnail or mask for the menus Photoshop has there.
   LAYER MENU  New Layer (Shift+Ctrl+N), New ▸ Layer... (dialog), Layer via Copy (Ctrl+J), Layer via Cut (Shift+Ctrl+J);
     Duplicate, Delete, Delete Hidden Layers, Rename Layer...; Layer Mask ▸ From Transparency, Enable/Disable;
     Create/Release Clipping Mask (Alt+Ctrl+G); Hide Layers (no key: Ctrl+, opens the lab's Settings); Arrange ▸ Bring to Front (Shift+Ctrl+]), Bring
     Forward (Ctrl+]), Send Backward (Ctrl+[), Send to Back (Shift+Ctrl+[), Reverse; Lock Layers... (Ctrl+/); Merge Down /
     Merge Layers / Merge Clipping Mask (Ctrl+E), Merge Visible (Shift+Ctrl+E), Stamp Visible (Alt+Shift+Ctrl+E), Flatten
     Image. Select ▸ All Layers (Alt+Ctrl+A), Deselect Layers. Keys: Alt+[ / Alt+] the layer below / above (Shift adds it
     to the selection), Alt+, / Alt+. the bottom / top layer, Shift+Alt+letter a blend mode (N normal, I dissolve, K
     darken, M multiply, B color burn, A linear burn, G lighten, S screen, D color dodge, W linear dodge, O overlay, F soft
     light, H hard light, V vivid light, J linear light, Z pin light, L hard mix, E difference, X exclusion, U hue, T
     saturation, C color, Y luminosity), Shift+plus / Shift+minus the next / previous mode, Shift+digits Fill (when the
     tool has no opacity or flow), \ the mask overlay.
   MERGES keep the picture: the merged pixels are solved against what lies below them, so blend modes, masks, opacity,
     Fill, effects and clipping give the same composite; a clipping group merged into its base keeps the base's blend mode
     and opacity, and clipped layers merged together stay clipped. Merging an unclipped adjustment layer down applies it to
     the layer below only, as Photoshop does. Flatten keeps transparency (game art needs it).
   PUBLIC API (feature-detect it)  Ed.layersPanel = {selected() -> the selected layers bottom-to-top (the active one
     included), select(refs, activeRef), isSelected(ref), showMask(ref, 'alone'|'ruby'|null), maskView,
     merge(refs, {label}) -> the merged layer}; Ed.emit('layer-selection', layers) when the selection changes.
   ===================================================================================================================== */
(()=>{
const Ed=window.Ed;if(!Ed||!Ed.registerPanel||!Ed.layers)return;
const U=Ed.util;

// ----- small helpers ---------------------------------------------------------------------------------------------------
const $=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const ICON={
  eye:['M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12s-3.5 6.5-9.5 6.5S2.5 12 2.5 12z','M12 9.2a2.8 2.8 0 1 0 0 5.6a2.8 2.8 0 1 0 0-5.6z'],
  mask:['M4 5h16v14H4z','M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z'],
  adjust:['M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z','M12 4v16a8 8 0 0 0 0-16z'],
  plus:['M5 4h10l4 4v12H5z','M12 10v6M9 13h6'],dup:['M8 8h11v11H8z','M5 16V5h11'],
  trash:['M5 7h14','M10 7V5h4v2','M7 7l1 12h8l1-12','M10.5 10.5v5.5M13.5 10.5v5.5'],
  lockAlpha:['M4 4h7v7H4zM13 13h7v7h-7z','M13 4h7v7h-7zM4 13h7v7H4z'],lockPixels:'M16.5 4.5l3 3L9 18l-4 1 1-4z',
  lockMove:['M12 3v18M3 12h18','M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5'],
  lockAll:['M6.5 11h11v9h-11z','M9 11V8a3 3 0 0 1 6 0v3'],menu:'M4 7h16M4 12h16M4 17h16'};
function mkButton(cls,title,ic,size=16){const b=$('button',cls);b.type='button';if(title){b.title=title;b.setAttribute('aria-label',title.replace(/\s*\([^)]*\)\s*$/,''));}
  if(Array.isArray(ic)||(typeof ic==='string'&&ic.length>3))b.append(Ed.icon(ic,{size}));else if(ic)b.append($('span','ed-lp-glyph',ic));return b;}
const layers=()=>Ed.doc?Ed.doc.layers:[];
const act=()=>Ed.layers.active();
const ix=L=>Ed.doc?Ed.doc.layers.indexOf(L):-1;
const FULL=()=>({x:0,y:0,w:Ed.doc.width,h:Ed.doc.height});
const tell=(text,error=false)=>{Ed.message(text,error);return false;};
const refresh=()=>Ed.panels.refresh('layers');
const typing=e=>{const t=e.target;return !!(t&&t.closest&&t.closest('input,textarea,select,[contenteditable=""],[contenteditable="true"]'));};
const modalOpen=()=>!!document.querySelector('dialog[open]');
function isEmpty(c,R){const a=Ed.util.read(c,R).data;for(let i=3;i<a.length;i+=4)if(a[i])return false;return true;}

// ----- extra blend modes (Photoshop's formulas, per pixel through the W3C general formula) ---------------------------------
const sep=f=>(cb,cs,o)=>{o[0]=f(cb[0],cs[0]);o[1]=f(cb[1],cs[1]);o[2]=f(cb[2],cs[2]);};
const lum=c=>.3*c[0]+.59*c[1]+.11*c[2];
const burn=(b,s)=>b>=1?1:s<=0?0:1-Math.min(1,(1-b)/s);
const dodge=(b,s)=>b<=0?0:s>=1?1:Math.min(1,b/(1-s));
const pickColor=better=>(cb,cs,o)=>{const c=better(lum(cs),lum(cb))?cs:cb;o[0]=c[0];o[1]=c[1];o[2]=c[2];};
// Dissolve: a fixed threshold per document pixel (0..254), so a pixel shows when its alpha is above it and the pattern does
// not crawl while painting; a lower opacity shows a subset of the same pixels.
function dissolveNoise(x,y){let h=Math.imul(x|0,0x27d4eb2d)^Math.imul((y|0)+0x3c6ef372,0x165667b1);h=Math.imul(h^(h>>>15),0x85ebca6b);h=Math.imul(h^(h>>>13),0xc2b2ae35);return ((h^(h>>>16))>>>0)%255;}
function dissolve(dst,src,info){const R=info.region,d=dst.data,s=src.data;
  for(let y=0,i=0;y<R.h;y++)for(let x=0;x<R.w;x++,i+=4){const a=s[i+3];if(!a||dissolveNoise(R.x+x,R.y+y)>=a)continue;d[i]=s[i];d[i+1]=s[i+1];d[i+2]=s[i+2];d[i+3]=255;}}
const BLENDS=[['dissolve','Dissolve','normal',{composite:dissolve}],
  ['linear-burn','Linear Burn','darken',{fn:sep((b,s)=>Math.max(0,b+s-1))}],
  ['darker-color','Darker Color','darken',{fn:pickColor((s,b)=>s<b)}],
  ['lighter-color','Lighter Color','lighten',{fn:pickColor((s,b)=>s>b)}],
  ['vivid-light','Vivid Light','contrast',{fn:sep((b,s)=>s<=.5?burn(b,2*s):dodge(b,2*s-1))}],
  ['linear-light','Linear Light','contrast',{fn:sep((b,s)=>Math.min(1,Math.max(0,b+2*s-1)))}],
  ['pin-light','Pin Light','contrast',{fn:sep((b,s)=>s<=.5?Math.min(b,2*s):Math.max(b,2*s-1))}],
  ['hard-mix','Hard Mix','contrast',{fn:sep((b,s)=>b+s>=1-1e-9?1:0)}],
  ['subtract','Subtract','inversion',{fn:sep((b,s)=>Math.max(0,b-s))}],
  ['divide','Divide','inversion',{fn:sep((b,s)=>s<=0?(b<=0?0:1):Math.min(1,b/s))}]];
for(const [id,label,group,def] of BLENDS)Ed.registerBlend(id,{label,group,...def});
// Photoshop's "neutral" color per mode (New Layer's "Fill with neutral color")
const NEUTRAL={multiply:'#ffffff','color-burn':'#ffffff','linear-burn':'#ffffff',darken:'#ffffff','darker-color':'#ffffff',divide:'#ffffff',
  screen:'#000000','color-dodge':'#000000','linear-dodge':'#000000',lighten:'#000000','lighter-color':'#000000',difference:'#000000',exclusion:'#000000',subtract:'#000000',
  overlay:'#808080','soft-light':'#808080','hard-light':'#808080','vivid-light':'#808080','linear-light':'#808080','pin-light':'#808080'};

// ----- panel state: the selected layers (several with Ctrl/Shift-click) ----------------------------------------------------
const THUMB={none:0,small:22,medium:34,large:52};
const P={sel:new Set(),anchor:null,maskView:null,solo:null,thumbs:'medium'};
{const t=U.store.get('ed-layers-thumbs','medium');if(t in THUMB)P.thumbs=t;}
function syncSel(){const d=Ed.doc;if(!d){P.sel.clear();P.anchor=null;return;}const ids=new Set(d.layers.map(L=>L.id));for(const id of [...P.sel])if(!ids.has(id))P.sel.delete(id);
  const A=act();if(A&&!P.sel.has(A.id)){P.sel.clear();P.sel.add(A.id);P.anchor=A.id;}if(P.anchor&&!ids.has(P.anchor))P.anchor=A?A.id:null;}
const selected=()=>{syncSel();return layers().filter(L=>P.sel.has(L.id));};
const announce=()=>{refresh();Ed.emit('layer-selection',selected());};
function select(list,activeRef){if(!Ed.doc)return;list=list.map(r=>Ed.layers.get(r)).filter(Boolean);const A=activeRef!=null?Ed.layers.get(activeRef):list[list.length-1];
  P.sel=new Set(list.map(L=>L.id));if(A){P.sel.add(A.id);if(act()!==A)Ed.layers.setActive(A);}announce();}
function setThumbs(k){if(!(k in THUMB))return;P.thumbs=k;U.store.set('ed-layers-thumbs',k);refresh();}

// ----- clipping structure: a unit is a layer plus the clipped layers right above it (the core's stack() rule) --------------
function units(list=layers()){const out=[];for(let i=0;i<list.length;){const L=list[i];let j=i+1;if(L.kind!=='adjustment')while(j<list.length&&list[j].clip)j++;out.push([i,j]);i=j;}return out;}
function unitAt(k,list=layers()){for(const u of units(list))if(k>=u[0]&&k<u[1])return u;return[k,k+1];}
const isMember=(k,list=layers())=>k>unitAt(k,list)[0];
const hasMembers=(k,list=layers())=>{const u=unitAt(k,list);return u[0]===k&&u[1]>k+1;};
// A layer put at index `at` (the layer there moves up) lands inside a clipping group when that layer is clipped; it must
// then be clipped too or the group above it would clip to it. A layer added above a clipped active layer joins its group.
function clipFor(at,list=layers(),aboveActive=false){if(at<=0)return false;const up=list[at],down=list[at-1];
  if(up&&up.clip&&isMember(at,list))return true;return !!(aboveActive&&down&&down.clip&&isMember(at-1,list));}
const canClipAt=(k,list=layers())=>{const below=list[k-1];return k>0&&!!below&&!(below.kind==='adjustment'&&!below.clip);};

// ----- new layers, Layer via Copy / Cut, duplicate, delete ---------------------------------------------------------------
function newLayer(p={},below=false){const d=Ed.doc;if(!d)return null;Ed.preview.clear();const at=below?d.activeIndex:d.activeIndex+1;
  const clip=p.clip?canClipAt(at):clipFor(at,d.layers,!below);let made=null;
  Ed.history.record('New Layer',()=>{const L=Ed.layers.add({name:p.name?String(p.name).slice(0,255):undefined,at,history:false,clip,
      blend:p.mode&&Ed.blends.get(p.mode)?p.mode:'normal',opacity:p.opacity!=null&&isFinite(+p.opacity)?U.clamp(+p.opacity/100,0,1):1});
    const n=p.neutral&&NEUTRAL[L.blend];if(n){const g=L.canvas.getContext('2d');g.fillStyle=n;g.fillRect(0,0,d.width,d.height);L.version++;}
    made=L;},{op:{tool:'layer',action:'new'}});
  if(made)select([made],made);return made;}
function viaCopy(cut){const d=Ed.doc,L=act();if(!d||!L)return false;const sel=Ed.selection;
  if(!sel){if(cut)return tell('Select an area first: Layer via Cut moves the selected pixels to a new layer.');return !!duplicateLayers([L],'Layer via Copy');}
  if(L.kind!=='pixel')return tell(`“${L.name}” is an adjustment layer and has no pixels to copy.`);
  if(cut&&!L.visible)return tell(`“${L.name}” is hidden. Show it to cut pixels out of it.`,true);
  if(cut&&(L.locks.all||L.locks.pixels))return tell(`“${L.name}” is locked. Unlock it in the Layers panel to cut pixels out of it.`,true);
  Ed.preview.clear();const R=Ed.select.bounds()||FULL(),c=U.canvas(d.width,d.height),g=c.getContext('2d');
  g.drawImage(L.canvas,0,0);g.globalCompositeOperation='destination-in';g.drawImage(sel,0,0);
  if(isEmpty(c,R))return tell(`The selected area of “${L.name}” is empty.`);
  const at=ix(L)+1,clip=clipFor(at,d.layers,true);let out=null;
  Ed.history.record(cut?'Layer via Cut':'Layer via Copy',tx=>{
    if(cut){tx.touch(L,'pixels',R);const lg=L.canvas.getContext('2d');lg.save();lg.setTransform(1,0,0,1,0,0);lg.globalAlpha=1;lg.filter='none';
      if(L.locks.alpha){const t=U.canvas(d.width,d.height),tg=t.getContext('2d');tg.fillStyle=Ed.bg;tg.fillRect(R.x,R.y,R.w,R.h);tg.globalCompositeOperation='destination-in';tg.drawImage(sel,0,0);
        lg.globalCompositeOperation='source-atop';lg.drawImage(t,0,0);}   // locked transparency: the cut area takes the background color, as Clear does
      else{lg.globalCompositeOperation='destination-out';lg.drawImage(sel,0,0);}lg.restore();L.version++;Ed.update(R);}
    out=Ed.layers.add({canvas:c,at,history:false,clip,blend:L.blend,opacity:L.opacity,fill:L.fill,effects:L.effects});},{op:{tool:'layer',action:cut?'via-cut':'via-copy'}});
  if(out)select([out],out);return out;}
function copyName(name,taken){const root=String(name).replace(/ copy(?: \d+)?$/,'');for(let k=1;;k++){const n=root+' copy'+(k>1?' '+k:'');if(!taken.has(n))return n.slice(0,255);}}
function runsOf(list){const idx=list.map(ix).filter(i=>i>=0).sort((a,b)=>a-b),out=[];for(const i of idx){const r=out[out.length-1];if(r&&r[1]===i-1)r[1]=i;else out.push([i,i]);}return out;}
// Each run of neighbouring layers is copied as one block right above itself, so a copied clipping group stays a group;
// a block put inside a clipping group joins it (Photoshop clips a copy of a clipped layer to the same base).
function duplicateLayers(list,label){const d=Ed.doc;if(!d||!list.length)return null;Ed.preview.clear();const A=act(),runs=runsOf(list);let made=[],newActive=null;
  Ed.history.record(label||(list.length>1?'Duplicate Layers':'Duplicate Layer'),()=>{const taken=new Set(d.layers.map(L=>L.name));
    for(const [s,e] of runs.slice().reverse()){const src=d.layers.slice(s,e+1),inside=clipFor(e+1,d.layers),copies=[];
      for(const S of src){const name=copyName(S.name,taken);taken.add(name);const C=Ed.layers.duplicate(S,{history:false,name});copies.push(C);if(S===A)newActive=C;}
      const rest=d.layers.filter(L=>!copies.includes(L));rest.splice(rest.indexOf(src[src.length-1])+1,0,...copies);d.layers=rest;
      if(inside)for(const C of copies)C.clip=true;made=copies.concat(made);}
    Ed.layers.setActive(newActive||made[made.length-1]);},{op:{tool:'layer',action:'duplicate'}});
  if(made.length)select(made,newActive||made[made.length-1]);return made;}
function deleteLayers(list){const d=Ed.doc;if(!d||!list.length)return false;if(list.length>=d.layers.length)return tell('A document needs at least one layer.');
  Ed.preview.clear();Ed.history.record(list.length>1?'Delete Layers':'Delete Layer',()=>{for(const L of list.slice().sort((a,b)=>ix(b)-ix(a)))Ed.layers.remove(L,{history:false});},{op:{tool:'layer',action:'delete'}});return true;}
function deleteHidden(){const all=layers(),hidden=all.filter(L=>!L.visible);if(!hidden.length)return tell('There are no hidden layers.');
  if(hidden.length>=all.length)return tell('Every layer is hidden. Show one first: a document keeps at least one layer.');return deleteLayers(hidden);}
function deleteFromButton(){const d=Ed.doc,L=act();if(!d||!L)return false;
  if(d.target==='mask'&&L.mask)return Ed.layers.removeMask(L,false);   // the bin removes the targeted mask, as in Photoshop
  return deleteLayers(selected());}
function rename(L,name){name=String(name??'').trim().slice(0,255);if(!L||!name||name===L.name)return false;return Ed.layers.set(L,{name});}

// ----- Arrange ------------------------------------------------------------------------------------------------------------
// Whole units move (a base takes its clipped layers along and steps over whole groups); a clipped layer whose base is
// not selected moves inside its group and leaves it at either end (it is then released).
function arrange(how){const d=Ed.doc;if(!d)return false;const sel=new Set(selected());if(!sel.size)return false;Ed.preview.clear();
  const A=act(),list=d.layers.slice();let out=units(list).map(([s,e])=>list.slice(s,e));const clip=new Map();
  const unitSel=out.map(u=>sel.has(u[0]));
  if(unitSel.some(Boolean)){let items=out.map((u,k)=>({u,s:unitSel[k]}));const swap=(i,j)=>{const t=items[i];items[i]=items[j];items[j]=t;};
    if(how==='forward'){for(let k=items.length-2;k>=0;k--)if(items[k].s&&!items[k+1].s)swap(k,k+1);}
    else if(how==='backward'){for(let k=1;k<items.length;k++)if(items[k].s&&!items[k-1].s)swap(k,k-1);}
    else if(how==='front')items=[...items.filter(x=>!x.s),...items.filter(x=>x.s)];
    else if(how==='back')items=[...items.filter(x=>x.s),...items.filter(x=>!x.s)];
    else if(how==='reverse'){const pos=items.map((x,k)=>x.s?k:-1).filter(k=>k>=0),picked=pos.map(k=>items[k]).reverse();pos.forEach((k,j)=>{items[k]=picked[j];});}
    for(const x of items)if(x.s&&x.u[0].clip)clip.set(x.u[0],false);   // a moved unit starts a group of its own
    out=items.map(x=>x.u);}
  else{const extra={before:[],after:[]};
    for(let ui=0;ui<out.length;ui++){const u=out[ui];const ms=u.slice(1).filter(L=>sel.has(L));if(!ms.length)continue;const k=u.length-1;
      if(how==='forward'){if(sel.has(u[k])){const L=u.pop();clip.set(L,false);out.splice(ui+1,0,[L]);ui++;}
        for(let p=u.length-2;p>=1;p--)if(sel.has(u[p])&&!sel.has(u[p+1])){const t=u[p];u[p]=u[p+1];u[p+1]=t;}}
      else if(how==='backward'){if(sel.has(u[1])){const L=u.splice(1,1)[0];clip.set(L,false);out.splice(ui,0,[L]);ui++;}
        for(let p=2;p<u.length;p++)if(sel.has(u[p])&&!sel.has(u[p-1])){const t=u[p];u[p]=u[p-1];u[p-1]=t;}}
      else if(how==='front'||how==='back'){const rest=u.slice(1).filter(L=>!sel.has(L)),atEnd=how==='front'?u.slice(u.length-ms.length).every(L=>sel.has(L)):u.slice(1,1+ms.length).every(L=>sel.has(L));
        if(atEnd){u.splice(1,u.length-1,...rest);for(const L of ms){clip.set(L,false);(how==='front'?extra.after:extra.before).push([L]);}}
        else u.splice(1,u.length-1,...(how==='front'?[...rest,...ms]:[...ms,...rest]));}
      else if(how==='reverse'){const pos=[];for(let p=1;p<u.length;p++)if(sel.has(u[p]))pos.push(p);const picked=pos.map(p=>u[p]).reverse();pos.forEach((p,j)=>{u[p]=picked[j];});}}
    out=[...extra.before,...out,...extra.after];}
  const order=out.flat();if(order.length!==list.length)return false;
  if(order.every((L,i)=>L===list[i])&&[...clip].every(([L,c])=>L.clip===c))return false;   // already there: Photoshop does nothing
  Ed.history.record('Layer Order',()=>{d.layers=order;for(const [L,c] of clip)L.clip=c;if(d.layers[0].clip)d.layers[0].clip=false;Ed.layers.setActive(A,d.target);},{op:{tool:'layer',action:'arrange-'+how}});
  refresh();return true;}
// Dragging rows: the block (with the clipped layers of any base in it) goes right above `below` (null = the bottom).
function moveBlock(block,below){const d=Ed.doc;if(!d||!block.length)return false;Ed.preview.clear();const list=d.layers.slice(),A=act();
  const moving=new Set(block);for(const L of block){const k=list.indexOf(L);if(k<0)continue;const u=unitAt(k,list);if(u[0]===k)for(let j=k+1;j<u[1];j++)moving.add(list[j]);}
  const carried=new Set([...moving].filter(L=>{const k=list.indexOf(L),u=unitAt(k,list);return k>u[0]&&moving.has(list[u[0]]);}));
  const mv=list.filter(L=>moving.has(L)),rest=list.filter(L=>!moving.has(L));let at=0;
  if(below&&!moving.has(below))at=rest.indexOf(below)+1;else if(below){const k=list.indexOf(below);let j=k-1;while(j>=0&&moving.has(list[j]))j--;at=j>=0?rest.indexOf(list[j])+1:0;}
  const order=[...rest.slice(0,at),...mv,...rest.slice(at)],clip=new Map(),top=at+mv.length,above=order[top];
  const inside=!!(above&&above.clip&&isMember(top,order));
  for(const L of mv){if(carried.has(L))continue;const k=order.indexOf(L);
    if(inside){clip.set(L,true);continue;}
    if(!L.clip)continue;
    // a clipped layer stays clipped while it sits on its own group (its base or one of its clipped layers)
    const was=list.indexOf(L),u=unitAt(was,list),group=new Set(list.slice(u[0],u[1]));const under=order[k-1];clip.set(L,!!under&&group.has(under)&&under!==L);}
  if(order.every((L,i)=>L===list[i])&&[...clip].every(([L,c])=>L.clip===c))return false;
  Ed.history.record('Layer Order',()=>{d.layers=order;for(const [L,c] of clip)L.clip=c;if(d.layers[0].clip)d.layers[0].clip=false;Ed.layers.setActive(A,d.target);},{op:{tool:'layer',action:'move'}});
  return true;}

// ----- clipping masks ----------------------------------------------------------------------------------------------------
function createClip(list){const ok=list.filter(L=>{const k=ix(L);return k>0&&!L.clip&&canClipAt(k);});
  if(!ok.length){const L=list[0];if(!L)return false;const k=ix(L);if(k<=0)return tell('The bottom layer has nothing below it to clip to.');
    const below=layers()[k-1];if(below&&below.kind==='adjustment'&&!below.clip)return tell(`“${below.name}” is an adjustment layer. A clipping mask needs a pixel layer below it.`);return false;}
  Ed.history.record('Create Clipping Mask',()=>{for(const L of ok)Ed.layers.set(L,{clip:true},{history:false});},{op:{tool:'layer',action:'clip'}});return true;}
// Release takes the layer and every clipped layer above it out of the group (Photoshop's rule).
function releaseClip(list){const d=Ed.doc,out=new Set();for(const L of list){if(!L.clip)continue;out.add(L);for(let j=ix(L)+1;j<d.layers.length&&d.layers[j].clip;j++)out.add(d.layers[j]);}
  if(!out.size)return false;Ed.history.record('Release Clipping Mask',()=>{for(const L of out)Ed.layers.set(L,{clip:false},{history:false});},{op:{tool:'layer',action:'unclip'}});return true;}
// With several layers selected, the ones above the lowest clip to it (Photoshop).
function clipToggle(){const L=act();if(!L)return false;const s=selected();
  if(s.length>1){const rest=s.slice(1);return rest.every(x=>x.clip)?releaseClip(rest):createClip(rest);}
  return L.clip?releaseClip([L]):createClip([L]);}
const clipLabel=()=>{const L=act(),s=selected();if(!L)return'Create Clipping Mask';if(s.length>1)return s.slice(1).every(x=>x.clip)?'Release Clipping Mask':'Create Clipping Mask';return L.clip?'Release Clipping Mask':'Create Clipping Mask';};
const clipEnabled=()=>{const L=act();if(!L)return false;const s=selected();if(s.length>1)return true;return !!L.clip||canClipAt(ix(L));};

// ----- visibility and locks ----------------------------------------------------------------------------------------------
function setVisible(list,vis,label){const ch=list.filter(L=>L.visible!==vis);if(!ch.length)return false;
  Ed.history.record(label||(ch.length>1?(vis?'Show Layers':'Hide Layers'):(vis?'Show Layer':'Hide Layer')),()=>{for(const L of ch)Ed.layers.set(L,{visible:vis},{history:false});},{op:{tool:'layer',action:'visible'}});return true;}
const hideLayers=()=>{const s=selected();return s.length?setVisible(s,!s.some(L=>L.visible)):false;};
// Alt-click the eye: only that layer shows; Alt-click it again brings the others back as they were.
function solo(L){const d=Ed.doc,others=d.layers.filter(x=>x!==L);
  if(P.solo&&P.solo.id===L.id&&others.every(x=>!x.visible)){const was=P.solo.vis;P.solo=null;
    Ed.history.record('Show Layers',()=>{for(const x of others)if(was.get(x.id))Ed.layers.set(x,{visible:true},{history:false});if(!L.visible)Ed.layers.set(L,{visible:true},{history:false});},{op:{tool:'layer',action:'visible'}});return;}
  P.solo={id:L.id,vis:new Map(d.layers.map(x=>[x.id,x.visible]))};
  Ed.history.record('Hide Layers',()=>{for(const x of others)if(x.visible)Ed.layers.set(x,{visible:false},{history:false});if(!L.visible)Ed.layers.set(L,{visible:true},{history:false});},{op:{tool:'layer',action:'solo'}});}
function toggleLock(k){const A=act();if(!A)return false;const v=!A.locks[k],s=selected().filter(L=>!(k==='alpha'&&L.kind==='adjustment'));if(!s.length)return false;
  Ed.history.record('Lock Change',()=>{for(const L of s)Ed.layers.set(L,{locks:{[k]:v}},{history:false});},{op:{tool:'layer',action:'lock'}});return true;}
function lockLayers(p){const s=selected(),A=act();if(!A)return false;
  const apply=q=>{Ed.history.record('Lock Change',()=>{for(const L of s)Ed.layers.set(L,{locks:{alpha:!!q.alpha&&L.kind!=='adjustment',pixels:!!q.pixels,position:!!q.position,all:!!q.all}},{history:false});},{op:{tool:'layer',action:'lock'}});};
  if(p&&typeof p==='object'){apply(p);return true;}
  Ed.dialog({title:'Lock Layers',text:s.length>1?`For the ${s.length} selected layers.`:`For “${A.name}”.`,fields:[
    {key:'all',label:'All: no painting, moving or blending changes',type:'checkbox',default:!!A.locks.all},
    {key:'alpha',label:'Transparency: paint only where there are pixels',type:'checkbox',default:!!A.locks.alpha},
    {key:'pixels',label:'Image: no painting or filters',type:'checkbox',default:!!A.locks.pixels},
    {key:'position',label:'Position: the layer cannot be moved',type:'checkbox',default:!!A.locks.position}],ok:apply});return true;}

// ----- masks -------------------------------------------------------------------------------------------------------------
function addMask(how){const L=act();if(!L)return false;if(L.mask)return tell('This layer already has a mask.');
  if(!Ed.selection&&(how==='selection'||how==='hide-selection'))how=how==='selection'?'reveal':'hide';return Ed.layers.addMask(L,how);}
// From Transparency: the layer's alpha moves into a new mask and its pixels become opaque; the picture stays the same.
function maskFromTransparency(){const L=act();if(!L||L.kind!=='pixel')return false;if(L.mask)return tell('This layer already has a mask.');
  if(L.locks.all||L.locks.pixels)return tell(`“${L.name}” is locked. Unlock it in the Layers panel first.`,true);
  Ed.preview.clear();const F=FULL(),px=Ed.util.read(L.canvas,F),m=new ImageData(F.w,F.h),a=px.data,md=m.data;
  for(let i=0;i<a.length;i+=4){md[i]=md[i+1]=md[i+2]=255;md[i+3]=a[i+3];a[i+3]=255;}
  const mask=U.canvas(F.w,F.h);Ed.util.write(mask,m,F);
  Ed.history.record('From Transparency',tx=>{tx.touch(L,'pixels',F);Ed.util.write(L.canvas,px,F);L.version++;Ed.layers.set(L,{mask,maskEnabled:true},{history:false});Ed.layers.setActive(L,'mask');},{op:{tool:'mask',action:'transparency'}});return true;}
const toggleMask=L=>Ed.layers.set(L,{maskEnabled:!L.maskEnabled});
function setMaskView(v){const L=v&&Ed.layers.get(v.id);P.maskView=L&&L.mask?{id:L.id,mode:v.mode}:null;
  if(P.maskView&&(act()!==L||Ed.doc.target!=='mask'))Ed.layers.setActive(L,'mask');Ed.view.redrawOverlay();refresh();}
// Ctrl-click a thumbnail: its alpha becomes the selection (Shift adds, Alt subtracts, Shift+Alt intersects).
const SEL_LABEL={replace:'Load Selection',add:'Add to Selection',subtract:'Subtract from Selection',intersect:'Intersect Selection'};
function loadSelection(L,part,mode='replace'){if(!L)return false;const src=part==='mask'||L.kind==='adjustment'?L.mask:L.canvas;
  if(!src)return mode==='replace'||mode==='add'?Ed.select.all():false;
  if((mode==='replace'||mode==='add')&&isEmpty(src,FULL()))return tell(src===L.mask?'This mask hides everything, so there is nothing to select.':`“${L.name}” is empty, so there is nothing to select.`);
  return Ed.select.set(src,mode,{label:SEL_LABEL[mode]});}
const modeOf=e=>e.shiftKey&&e.altKey?'intersect':e.shiftKey?'add':e.altKey?'subtract':'replace';

// ----- merging -----------------------------------------------------------------------------------------------------------
// Premultiplied bytes of a canvas (what it stores); Ed.util.read gives straight colors that multiply back exactly.
function pm(canvas){const d=Ed.util.read(canvas,FULL()).data;for(let i=0;i<d.length;i+=4){const a=d[i+3];if(a===255)continue;if(!a){d[i]=d[i+1]=d[i+2]=0;continue;}
  d[i]=Math.round(d[i]*a/255);d[i+1]=Math.round(d[i+1]*a/255);d[i+2]=Math.round(d[i+2]*a/255);}return d;}
// The normal-mode pixels M (straight 8-bit) that, drawn over B, give T. T, B and I are premultiplied. Alpha is fixed by T and
// B where B is see-through; over opaque pixels it is the merged layers' own coverage (I), raised only when that cannot
// reach T. Where the layers' own pixels (I) already give T they are kept exactly.
function solve(T,B,I){const n=T.length,M=new Uint8ClampedArray(n);
  for(let i=0;i<n;i+=4){const ta=T[i+3],ba=B[i+3];let A;
    if(ba<255)A=ta<=ba?0:Math.min(255,Math.round((ta-ba)*255/(255-ba)));
    else{let need=0;for(let c=0;c<3;c++){const t=T[i+c],b=B[i+c];if(b>t)need=Math.max(need,(b-t)*255/b);else if(t>b)need=Math.max(need,(t-b)*255/(255-b));}
      A=Math.min(255,Math.max(I[i+3],Math.ceil(need-1e-9)));}
    if(A<=0)continue;const k=(255-A)/255;
    if(A===I[i+3]){let ok=true;for(let c=0;c<3;c++)if(Math.abs(I[i+c]+B[i+c]*k-T[i+c])>.5+1e-9){ok=false;break;}
      if(ok){for(let c=0;c<3;c++)M[i+c]=Math.min(255,Math.round(I[i+c]*255/A));M[i+3]=A;continue;}}
    for(let c=0;c<3;c++){const m=Math.min(A,Math.max(0,Math.round(T[i+c]-B[i+c]*k)));M[i+c]=Math.min(255,Math.round(m*255/A));}M[i+3]=A;}
  return M;}
const opaque=(d,i)=>{const a=d[i+3];if(!a)return;if(a<255){d[i]=Math.min(255,Math.round(d[i]*255/a));d[i+1]=Math.min(255,Math.round(d[i+1]*255/a));d[i+2]=Math.min(255,Math.round(d[i+2]*255/a));}d[i+3]=255;};
const fxStage=t=>Ed.effects.get(t)?.stage;
const liveFx=(L,stage)=>Object.entries(L.effects||{}).some(([t,p])=>p&&p.enabled!==false&&fxStage(t)===stage);
const innerFx=L=>Object.fromEntries(Object.entries(L.effects||{}).filter(([t])=>fxStage(t)==='inner'));
const outerFx=L=>Object.fromEntries(Object.entries(L.effects||{}).filter(([t])=>fxStage(t)!=='inner'));
// Render with a few layer fields changed for a moment (synchronously, inside the merge; nothing else sees them).
function withTemp(pairs,fn){const old=pairs.map(([L,k])=>[L,k,L[k]]);try{for(const [L,k,v] of pairs)L[k]=v;return fn();}finally{for(let i=old.length-1;i>=0;i--){const [L,k,v]=old[i];L[k]=v;}}}
const render=list=>Ed.render.composite({layers:list});
// How a contiguous range a..b merges: 'group' = a clipping base and some of its clipped layers (the result stays the base,
// keeping its blend mode and opacity), 'inside' = clipped layers of one group (the result stays clipped), else 'flat'.
function planMerge(a,b){const list=layers(),us=units(list),uOf=k=>us.find(u=>k>=u[0]&&k<u[1]),ua=uOf(a),ub=uOf(b),dependents=ub[1]>b+1;
  if(ua===ub&&a>ua[0])return{kind:'inside',base:ua[0],dependents};
  if(ua===ub&&a===ua[0]&&b>a&&list[a].kind!=='adjustment'){const B=list[a];
    const exact=(B.fill>=1||!(liveFx(B,'behind')||liveFx(B,'over')))&&(B.blend==='normal'||!liveFx(B,'over'));
    if(exact||dependents)return{kind:'group',base:a,exact,dependents};}
  return{kind:'flat',dependents};}
function mergeResult(a,b){const list=layers(),range=list.slice(a,b+1),plan=planMerge(a,b);
  if(plan.kind==='group'){const base=list[a];
    const canvas=withTemp([[base,'blend','normal'],[base,'opacity',1],[base,'effects',innerFx(base)]],()=>render(range));
    return{plan,canvas,props:{blend:base.blend,opacity:base.opacity,effects:outerFx(base),clip:false}};}
  if(plan.kind==='inside'){const base=list[plan.base],temp=[[base,'blend','normal'],[base,'opacity',1],[base,'effects',innerFx(base)]];
    const [gb,ga]=withTemp(temp,()=>[pm(render(list.slice(plan.base,a))),pm(render(list.slice(plan.base,b+1)))]);
    const iso=withTemp(range.map(L=>[L,'clip',false]),()=>pm(render(range))),shape=new Uint8Array(gb.length>>2);
    for(let i=0,p=0;i<gb.length;i+=4,p++){shape[p]=gb[i+3];opaque(gb,i);opaque(ga,i);}
    const data=solve(ga,gb,iso);
    for(let i=0,p=0;i<data.length;i+=4,p++)if(!shape[p]){const a=iso[i+3];data[i+3]=a;for(let c=0;c<3;c++)data[i+c]=a?Math.min(255,Math.round(iso[i+c]*255/a)):0;}   // outside the base: the layers as they are
    return{plan,data,props:{blend:'normal',opacity:1,effects:{},clip:true}};}
  const T=pm(render(list.slice(0,b+1))),B=pm(Ed.render.below(a)),I=pm(render(range));
  return{plan,data:solve(T,B,I),props:{blend:'normal',opacity:1,effects:{},clip:false}};}
function putCanvas(L,cv){const g=L.canvas.getContext('2d');g.save();g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.filter='none';g.globalCompositeOperation='copy';g.drawImage(cv,0,0);g.restore();L.version++;}
// Replace the contiguous range by one layer holding `res`, inside the open transaction tx.
function applyMerge(range,res,o,tx){const F=FULL(),a=ix(range[0]),above=layers().slice(ix(range[range.length-1])+1),clips=above.map(L=>[L,L.clip]);
  let keep=range[0].kind==='pixel'?range[0]:null;if(!keep)keep=Ed.layers.add({name:o.name||range[0].name,at:a,history:false});
  tx.touch(keep,'pixels',F);if(res.canvas)putCanvas(keep,res.canvas);else{Ed.util.write(keep.canvas,new ImageData(res.data,F.w,F.h),F);keep.version++;}
  for(const L of range.slice().reverse())if(L!==keep)Ed.layers.remove(L,{history:false});
  for(const [L,c] of clips)if(L.clip!==c)L.clip=c;   // layers clipped above the range stay clipped to the result
  Ed.layers.set(keep,{name:o.name||keep.name,blend:res.props.blend,opacity:res.props.opacity,fill:1,effects:res.props.effects,mask:null,maskEnabled:true,
    clip:res.props.clip&&ix(keep)>0,visible:true,data:{}},{history:false});
  Ed.layers.setActive(keep,'pixels');Ed.update();return keep;}
function mergeRange(range,label,o={}){if(!range.length)return null;Ed.preview.clear();const res=mergeResult(ix(range[0]),ix(range[range.length-1]));let out=null;
  Ed.history.record(label,tx=>{out=applyMerge(range,res,o,tx);},{op:o.op||{tool:'layer',action:'merge'}});if(out)select([out],out);return out;}
// Visible layers that really draw: a hidden base hides its clipped layers.
function contributors(){const list=layers(),out=[];for(const [s,e] of units(list)){if(!list[s].visible)continue;out.push(list[s]);for(let k=s+1;k<e;k++)if(list[k].visible)out.push(list[k]);}return out;}
// Several layers become one plain layer holding `cv` (the composite they made), at the lowest one's place.
function replaceWith(list,cv,name,tx){const lowest=list.slice().sort((a,b)=>ix(a)-ix(b))[0];
  const keep=lowest.kind==='pixel'?lowest:Ed.layers.add({name,at:ix(lowest),history:false});
  tx.touch(keep,'pixels',FULL());putCanvas(keep,cv);
  for(const L of list.slice().sort((a,b)=>ix(b)-ix(a)))if(L!==keep)Ed.layers.remove(L,{history:false});
  Ed.layers.set(keep,{name,blend:'normal',opacity:1,fill:1,effects:{},mask:null,maskEnabled:true,clip:false,visible:true,data:{}},{history:false});
  Ed.layers.setActive(keep,'pixels');Ed.update();return keep;}
const lockedWhy=(L,into)=>L.locks.all||L.locks.pixels?(into?`“${L.name}” is locked. Unlock it in the Layers panel to merge into it.`:`“${L.name}” is locked. Unlock it in the Layers panel to merge it.`):'';
function mergeDown(){const d=Ed.doc,L=act();if(!d||!L)return false;const s=selected();if(s.length>1)return mergeSelected(s);
  const k=ix(L);if(hasMembers(k)){const u=unitAt(k);if(!L.visible)return tell(`“${L.name}” is hidden. Show it to merge its clipping mask.`);
    const why=lockedWhy(L,true);if(why)return tell(why,true);return mergeRange(d.layers.slice(u[0],u[1]),'Merge Clipping Mask',{op:{tool:'layer',action:'merge-clip'}});}
  if(k<=0)return tell('There is no layer below to merge into.');const lower=d.layers[k-1];
  if(!L.visible)return tell(`“${L.name}” is hidden. Show it to merge it down.`);
  if(!lower.visible)return tell(`“${lower.name}” is hidden. Show it to merge into it.`);
  if(lower.kind==='adjustment')return tell(`“${lower.name}” is an adjustment layer, so nothing can be merged into it.`);
  const why=lockedWhy(lower,true);if(why)return tell(why,true);
  if(L.kind==='adjustment'&&!L.clip)return mergeAdjustmentDown(L,lower);
  return mergeRange([lower,L],'Merge Down',{op:{tool:'layer',action:'merge-down'}});}
// An unclipped adjustment layer merged down is applied to the layer below only (Photoshop); that layer keeps its blend
// mode, opacity, Fill, mask and style.
function mergeAdjustmentDown(adj,lower){Ed.preview.clear();
  const cv=withTemp([[lower,'blend','normal'],[lower,'opacity',1],[lower,'fill',1],[lower,'maskEnabled',false],[lower,'effects',{}],[lower,'clip',false],[lower,'visible',true]],()=>render([lower,adj]));
  Ed.history.record('Merge Down',tx=>{tx.touch(lower,'pixels',FULL());putCanvas(lower,cv);Ed.layers.remove(adj,{history:false});Ed.layers.setActive(lower,'pixels');Ed.update();},{op:{tool:'layer',action:'merge-down'}});
  select([lower],lower);return lower;}
function mergeSelected(s){const shown=s.filter(L=>L.visible);if(!shown.length)return tell('The selected layers are hidden. Show the ones to merge.');
  const locked=s.find(L=>L.locks.all||L.locks.pixels);if(locked)return tell(lockedWhy(locked,false),true);
  const idx=s.map(ix).sort((a,b)=>a-b),top=s[s.length-1];
  if(idx.every((v,i)=>!i||v===idx[i-1]+1))return mergeRange(s,'Merge Layers',{name:top.name,op:{tool:'layer',action:'merge-layers'}});
  // Not side by side: Photoshop's way (the selected layers composited on their own, at the top one's place)
  Ed.preview.clear();const cv=render(shown);let out=null;
  Ed.history.record('Merge Layers',tx=>{const keep=top.kind==='pixel'?top:Ed.layers.add({name:top.name,at:ix(top)+1,history:false});tx.touch(keep,'pixels',FULL());putCanvas(keep,cv);
    for(const L of s.slice().sort((a,b)=>ix(b)-ix(a)))if(L!==keep)Ed.layers.remove(L,{history:false});
    Ed.layers.set(keep,{name:top.name,blend:'normal',opacity:1,fill:1,effects:{},mask:null,maskEnabled:true,clip:false,visible:true,data:{}},{history:false});
    Ed.layers.setActive(keep,'pixels');Ed.update();out=keep;},{op:{tool:'layer',action:'merge-layers'}});if(out)select([out],out);return out;}
const mergeLabel=()=>{const s=selected(),L=act();if(s.length>1)return'Merge Layers';if(L&&hasMembers(ix(L)))return'Merge Clipping Mask';return'Merge Down';};
const mergeEnabled=()=>{const s=selected(),L=act();if(!L)return false;if(s.length>1)return true;const k=ix(L);return hasMembers(k)||(k>0&&layers()[k-1].kind!=='adjustment');};
function mergeVisible(){const d=Ed.doc;if(!d)return false;const c=contributors();if(c.length<2)return tell(c.length?'Only one layer is visible: there is nothing to merge.':'No layer is visible.');
  const locked=c.find(L=>L.locks.all||L.locks.pixels);if(locked)return tell(lockedWhy(locked,false),true);
  Ed.preview.clear();const T=Ed.render.composite(),A=act(),name=(c.includes(A)?A:c[c.length-1]).name;let out=null;
  Ed.history.record('Merge Visible',tx=>{out=replaceWith(c,T,name,tx);},{op:{tool:'layer',action:'merge-visible'}});if(out)select([out],out);return out;}
function flatten(p){const d=Ed.doc;if(!d||d.layers.length<2)return false;const hidden=d.layers.filter(L=>!L.visible);
  const go=()=>{Ed.preview.clear();const T=Ed.render.composite();let out=null;Ed.history.record('Flatten Image',tx=>{out=replaceWith(d.layers.slice(),T,d.layers[0].name,tx);},{op:{tool:'layer',action:'flatten'}});if(out)select([out],out);};
  if(hidden.length&&!p){Ed.dialog({title:'Flatten Image',text:hidden.length===1?'Discard the hidden layer? Hidden layers are not part of the picture.':`Discard the ${hidden.length} hidden layers? Hidden layers are not part of the picture.`,okLabel:'Discard',ok:go});return true;}
  go();return true;}
// Stamp Visible: a new layer with the picture as it looks, above the active layer's group.
function stampVisible(){const d=Ed.doc;if(!d)return false;Ed.preview.clear();const T=Ed.render.composite(),at=unitAt(d.activeIndex)[1];let L=null;
  Ed.history.record('Stamp Visible',()=>{L=Ed.layers.add({canvas:T,at,history:false});},{op:{tool:'layer',action:'stamp'}});if(L)select([L],L);return L;}

// ----- dialogs -----------------------------------------------------------------------------------------------------------
const modeOptions=()=>{const out=[];let g=null;for(const b of Ed.blends.list()){if(g&&g!==b.group)out.push('-');g=b.group;out.push({value:b.id,label:b.label});}return out;};
function newLayerDialog(below=false){const d=Ed.doc;if(!d)return false;const def='Layer '+(d.counter||d.layers.length);
  Ed.dialog({title:'New Layer',fields:[{key:'name',label:'Name',type:'text',default:def},
    {key:'clip',label:'Use Previous Layer to Create Clipping Mask',type:'checkbox',default:false},
    {key:'mode',label:'Mode',type:'select',options:modeOptions(),default:'normal'},
    {key:'opacity',label:'Opacity',type:'range',min:0,max:100,step:1,unit:'%',default:100},
    {key:'neutral',label:'Fill with the mode’s neutral color',type:'checkbox',default:false}],
    ok:p=>{const name=String(p.name||'').trim();newLayer({...p,name:name&&name!==def?name:undefined},below);}});return true;}

// ----- menus -------------------------------------------------------------------------------------------------------------
const hasDoc=()=>!!Ed.doc,one=()=>!!act();
const coreDef=path=>({...(Ed.menu.get(path)?.def||{})});
const reg=(path,def)=>Ed.registerMenu(path,def);
const regNew=(path,def)=>{if(!Ed.menu.get(path))Ed.registerMenu(path,def);};
reg('Layer/New Layer',{...coreDef('Layer/New Layer'),order:10,shortcut:'Shift+Ctrl+N',enabled:hasDoc,action:p=>{newLayer(p&&typeof p==='object'?p:{});}});
reg('Layer/New/Layer...',{order:11,enabled:hasDoc,action:p=>p&&typeof p==='object'?newLayer(p):newLayerDialog()});
reg('Layer/New/Layer via Copy',{order:12,shortcut:'Ctrl+J',enabled:one,action:()=>viaCopy(false)});
reg('Layer/New/Layer via Cut',{order:13,shortcut:'Shift+Ctrl+J',enabled:()=>one()&&!!Ed.selection&&act().kind==='pixel',action:()=>viaCopy(true)});
reg('Layer/Duplicate Layer',{...coreDef('Layer/Duplicate Layer'),order:20,shortcut:undefined,enabled:one,label:()=>selected().length>1?'Duplicate Layers':'Duplicate Layer',action:()=>{duplicateLayers(selected());}});
reg('Layer/Delete Layer',{...coreDef('Layer/Delete Layer'),order:30,enabled:()=>hasDoc()&&Ed.layers.count()>selected().length,label:()=>selected().length>1?'Delete Layers':'Delete Layer',action:()=>deleteLayers(selected())});
reg('Layer/Delete Hidden Layers',{order:31,enabled:()=>layers().some(L=>!L.visible)&&layers().some(L=>L.visible),action:deleteHidden});
reg('Layer/Rename Layer...',{order:32,enabled:one,action:p=>p&&p.name!=null?rename(act(),p.name):startRename(act())});
regNew('Layer/Layer Mask/From Transparency',{order:43.5,enabled:()=>one()&&act().kind==='pixel'&&!act().mask,action:maskFromTransparency});
reg('Layer/Layer Mask/Disable',{...coreDef('Layer/Layer Mask/Disable'),order:44,separatorBefore:true,checked:undefined,label:()=>act()?.mask&&!act().maskEnabled?'Enable':'Disable',
  enabled:()=>!!act()?.mask,action:()=>{const L=act();if(L&&L.mask)toggleMask(L);}});
reg('Layer/Create Clipping Mask',{...coreDef('Layer/Create Clipping Mask'),order:50,separatorBefore:true,shortcut:'Alt+Ctrl+G',checked:undefined,label:clipLabel,enabled:clipEnabled,action:clipToggle});
reg('Layer/Hide Layers',{order:60,separatorBefore:true,enabled:one,label:()=>selected().some(L=>L.visible)?'Hide Layers':'Show Layers',action:hideLayers});
reg('Layer/Arrange/Bring to Front',{order:61,shortcut:['Shift+Ctrl+]','Shift+Ctrl+}'],enabled:one,action:()=>arrange('front')});
reg('Layer/Arrange/Bring Forward',{order:62,shortcut:'Ctrl+]',enabled:one,action:()=>arrange('forward')});
reg('Layer/Arrange/Send Backward',{order:63,shortcut:'Ctrl+[',enabled:one,action:()=>arrange('backward')});
reg('Layer/Arrange/Send to Back',{order:64,shortcut:['Shift+Ctrl+[','Shift+Ctrl+{'],enabled:one,action:()=>arrange('back')});
reg('Layer/Arrange/Reverse',{order:65,separatorBefore:true,enabled:()=>selected().length>1,action:()=>arrange('reverse')});
reg('Layer/Lock Layers...',{order:66,shortcut:'Ctrl+/',enabled:one,action:p=>lockLayers(p)});
reg('Layer/Merge Down',{order:70,separatorBefore:true,shortcut:'Ctrl+E',label:mergeLabel,enabled:mergeEnabled,action:()=>mergeDown()});
reg('Layer/Merge Visible',{order:71,shortcut:'Shift+Ctrl+E',enabled:()=>contributors().length>1,action:()=>mergeVisible()});
reg('Layer/Stamp Visible',{order:72,shortcut:'Alt+Shift+Ctrl+E',enabled:()=>contributors().length>0,action:()=>stampVisible()});
reg('Layer/Flatten Image',{order:73,enabled:()=>layers().length>1,action:p=>flatten(p)});
regNew('Select/All Layers',{order:45,separatorBefore:true,shortcut:'Alt+Ctrl+A',enabled:hasDoc,action:()=>select(layers().slice(),act())});
regNew('Select/Deselect Layers',{order:46,enabled:()=>selected().length>1,action:()=>select([act()],act())});

// ----- popup menus (right-click, the panel menu, fx and adjustment buttons) -------------------------------------------------
let popup=null;
function closePopup(){if(!popup)return;const p=popup;popup=null;p.el.remove();}
function place(m,x,y){m.style.left=Math.max(4,Math.min(x,innerWidth-m.offsetWidth-6))+'px';m.style.top=Math.max(4,Math.min(y,innerHeight-m.offsetHeight-6))+'px';}
function openPopup(items,x,y,o={}){closePopup();const m=$('div','ed-menu ed-lp-popup');m.setAttribute('role','menu');m.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();};if(o.label)m.setAttribute('aria-label',o.label);
  for(const it of items){if(it.sep){if(m.lastChild&&!m.lastChild.classList.contains('ed-menu-sep'))m.append($('div','ed-menu-sep'));continue;}
    const b=$('button','ed-menu-item');b.type='button';b.setAttribute('role',it.checked!=null?'menuitemcheckbox':'menuitem');if(it.checked!=null)b.setAttribute('aria-checked',String(!!it.checked));
    b.disabled=it.enabled===false;if(it.id)b.dataset.item=it.id;
    b.append($('span','ed-menu-check',it.checked?'✓':''),$('span','ed-menu-label',it.label),$('span','ed-menu-key',it.shortcut||''));
    b.onclick=ev=>{ev.stopPropagation();closePopup();try{const r=it.run&&it.run(ev);if(r&&typeof r.catch==='function')r.catch(e=>Ed.message(e.message,true));}catch(e){Ed.message(e.message,true);console.error('[layers] menu:',e);}};
    m.append(b);}
  while(m.lastChild&&m.lastChild.classList.contains('ed-menu-sep'))m.lastChild.remove();while(m.firstChild&&m.firstChild.classList.contains('ed-menu-sep'))m.firstChild.remove();
  if(!m.children.length)return null;
  m.onkeydown=e=>{const all=[...m.querySelectorAll('.ed-menu-item:not(:disabled)')],i=all.indexOf(document.activeElement);
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();e.stopPropagation();const n=all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length];if(n)n.focus();}
    else if(e.key==='Home'||e.key==='End'){e.preventDefault();e.stopPropagation();(e.key==='Home'?all[0]:all[all.length-1])?.focus();}
    else if(e.key==='Escape'||e.key==='Tab'){e.preventDefault();e.stopPropagation();const back=o.anchor;closePopup();if(back&&back.focus)back.focus();}};
  document.body.append(m);place(m,x,y);popup={el:m,anchor:o.anchor||null};if(o.focus!==false)m.querySelector('.ed-menu-item:not(:disabled)')?.focus({preventScroll:true});return m;}
// a popup that opens above its footer button
function popupAbove(btn,items,label,e){if(popup&&popup.anchor===btn){closePopup();return;}const r=btn.getBoundingClientRect(),m=openPopup(items,r.left,r.top,{label,anchor:btn,focus:e.detail===0});if(m)place(m,r.left,r.top-m.offsetHeight-4);}
document.addEventListener('pointerdown',e=>{if(popup&&!(e.target.closest&&(e.target.closest('.ed-lp-popup')||e.target===popup.anchor||popup.anchor?.contains(e.target))))closePopup();},true);
document.addEventListener('keydown',e=>{if(popup&&e.key==='Escape'){e.preventDefault();e.stopPropagation();const back=popup.anchor;closePopup();if(back&&back.focus)back.focus({preventScroll:true});}},true);
window.addEventListener('resize',closePopup);window.addEventListener('blur',closePopup);
// A registered menu command as a popup item (label, shortcut and enabled come from the menu itself).
function cmd(id,path,extra={}){const m=Ed.menu.get(path);if(!m)return null;const row=Ed.menu.list().find(x=>x.path===path)||{};let label=path.split('/').pop();
  try{const l=m.def.label;if(l)label=typeof l==='function'?l():l;}catch(e){}return{id,label,shortcut:row.shortcut||'',enabled:row.enabled!==false,run:()=>Ed.menu.run(path),...extra};}
function treeItems(path){let nodes=Ed.menu.tree(),node=null;for(const part of path.split('/')){node=nodes.find(n=>n.name===part&&n.children);if(!node)return[];nodes=node.children;}
  const out=[];const walk=list=>{for(const it of list){if(it.children){out.push({sep:true});walk(it.children);out.push({sep:true});continue;}if(it.separatorBefore)out.push({sep:true});
    out.push({id:it.path,label:it.label,shortcut:it.shortcut,enabled:it.enabled,checked:it.checkable?it.checked:undefined,run:()=>Ed.menu.run(it.path)});}};walk(nodes);return out;}
// Layer styles come from styles.js and fill/adjustment layers from adjust.js / fill.js: offered only when they exist.
const STYLE_MENU='Layer/Layer Style';
const styleItems=()=>treeItems(STYLE_MENU);
const blendingPath=()=>{const it=styleItems().find(x=>!x.sep&&/^Blending Options/.test(String(x.label)));return it?it.id:null;};
const adjustmentItems=()=>{const out=[...treeItems('Layer/New Fill Layer'),{sep:true},...treeItems('Layer/New Adjustment Layer')];return out.some(x=>!x.sep)?out:[];};
const hasProps=()=>!!Ed.panels.get('properties');
function openOptions(L){if(!L)return false;
  if(L.kind==='adjustment'){if(hasProps()){Ed.panels.show('properties');return true;}return false;}
  const path=blendingPath();if(path){Ed.menu.run(path);return true;}return false;}
function rowItems(L){const s=selected(),many=s.length>1,items=[];const add=it=>{if(it)items.push(it);};
  if(L.kind==='pixel'&&blendingPath())add({id:'blending',label:'Blending Options...',run:()=>openOptions(L)});
  if(L.kind==='adjustment'&&hasProps())add({id:'edit-adjustment',label:'Edit Adjustment...',run:()=>openOptions(L)});
  items.push({sep:true});add(cmd('duplicate','Layer/Duplicate Layer'));add(cmd('delete','Layer/Delete Layer'));
  add({id:'rename',label:'Rename Layer',enabled:!many,run:()=>startRename(L)});
  items.push({sep:true});
  if(L.mask){add({id:'mask-toggle',label:L.maskEnabled?'Disable Layer Mask':'Enable Layer Mask',run:()=>toggleMask(L)});
    add({id:'mask-apply',label:'Apply Layer Mask',enabled:L.kind==='pixel',run:()=>Ed.layers.removeMask(L,true)});add({id:'mask-delete',label:'Delete Layer Mask',run:()=>Ed.layers.removeMask(L,false)});}
  else add({id:'mask-add',label:Ed.selection?'Reveal Selection':'Add Layer Mask',run:()=>addMask(Ed.selection?'selection':'reveal')});
  items.push({sep:true});add(cmd('clip','Layer/Create Clipping Mask'));
  items.push({sep:true});add(cmd('merge','Layer/Merge Down'));add(cmd('merge-visible','Layer/Merge Visible'));add(cmd('stamp','Layer/Stamp Visible'));add(cmd('flatten','Layer/Flatten Image'));
  return items;}
function thumbSizeItems(){return['none','small','medium','large'].map(k=>({id:'thumbs-'+k,label:k==='none'?'No Thumbnails':k==='small'?'Small Thumbnails':k==='medium'?'Medium Thumbnails':'Large Thumbnails',checked:P.thumbs===k,run:()=>setThumbs(k)}));}
function thumbItems(L){return[{id:'select-pixels',label:'Select Pixels',run:()=>loadSelection(L,'pixels')},{id:'add-pixels',label:'Add Pixels to Selection',enabled:!!Ed.selection,run:()=>loadSelection(L,'pixels','add')},
  {id:'subtract-pixels',label:'Subtract Pixels from Selection',enabled:!!Ed.selection,run:()=>loadSelection(L,'pixels','subtract')},{id:'intersect-pixels',label:'Intersect Pixels with Selection',enabled:!!Ed.selection,run:()=>loadSelection(L,'pixels','intersect')},
  {sep:true},...thumbSizeItems()];}
function maskItems(L){const view=P.maskView&&P.maskView.id===L.id?P.maskView.mode:null;
  return[{id:'mask-toggle',label:L.maskEnabled?'Disable Layer Mask':'Enable Layer Mask',run:()=>toggleMask(L)},{id:'mask-delete',label:'Delete Layer Mask',run:()=>Ed.layers.removeMask(L,false)},
    {id:'mask-apply',label:'Apply Layer Mask',enabled:L.kind==='pixel',run:()=>Ed.layers.removeMask(L,true)},{sep:true},
    {id:'mask-select',label:'Select Mask',run:()=>loadSelection(L,'mask')},{id:'mask-add-sel',label:'Add Mask to Selection',enabled:!!Ed.selection,run:()=>loadSelection(L,'mask','add')},
    {id:'mask-sub-sel',label:'Subtract Mask from Selection',enabled:!!Ed.selection,run:()=>loadSelection(L,'mask','subtract')},{id:'mask-int-sel',label:'Intersect Mask with Selection',enabled:!!Ed.selection,run:()=>loadSelection(L,'mask','intersect')},
    {sep:true},{id:'mask-alone',label:'Show Mask Alone',checked:view==='alone',run:()=>setMaskView(view==='alone'?null:{id:L.id,mode:'alone'})},
    {id:'mask-ruby',label:'Show Mask as Overlay',shortcut:'\\',checked:view==='ruby',run:()=>setMaskView(view==='ruby'?null:{id:L.id,mode:'ruby'})}];}
function eyeItems(L){const others=layers().filter(x=>x!==L);return[{id:'eye-toggle',label:L.visible?'Hide this layer':'Show this layer',run:()=>setVisible([L],!L.visible)},
  {id:'eye-others',label:others.some(x=>x.visible)?'Hide all other layers':'Show all other layers',enabled:others.length>0,run:()=>setVisible(others,!others.some(x=>x.visible))}];}
function panelMenuItems(){const items=[];const add=it=>{if(it)items.push(it);};
  add(cmd('new','Layer/New/Layer...',{label:'New Layer...'}));add(cmd('duplicate','Layer/Duplicate Layer'));add(cmd('delete','Layer/Delete Layer'));add(cmd('delete-hidden','Layer/Delete Hidden Layers'));
  items.push({sep:true});if(blendingPath())add({id:'blending',label:'Blending Options...',enabled:one()&&act().kind==='pixel',run:()=>openOptions(act())});
  add(cmd('clip','Layer/Create Clipping Mask'));add(cmd('lock','Layer/Lock Layers...'));
  items.push({sep:true});add(cmd('merge','Layer/Merge Down'));add(cmd('merge-visible','Layer/Merge Visible'));add(cmd('stamp','Layer/Stamp Visible'));add(cmd('flatten','Layer/Flatten Image'));
  items.push({sep:true},...thumbSizeItems());return items;}

// ----- the panel ---------------------------------------------------------------------------------------------------------
const UI={root:null,rows:new Map(),thumbs:new Map(),editing:null,live:null,dragging:false};
// Photoshop's clipping cursor (two squares and a bent arrow) for Alt over the line between two rows
const CLIP_CURSOR=`url("data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke-width="3" stroke="#000"><path d="M4 3h9v7H4zM9 11v8h11v-6"/></g><g fill="none" stroke-width="1.4" stroke="#fff"><path d="M4 3h9v7H4zM9 11v8h11v-6"/></g><path d="M15 9l3-3 3 3" fill="none" stroke="#000" stroke-width="3"/><path d="M15 9l3-3 3 3" fill="none" stroke="#fff" stroke-width="1.4"/></svg>')}") 9 11, pointer`;
function liveSet(key,v,end){const s=selected().filter(L=>!L.locks.all);if(!s.length)return;
  if(!UI.live||Ed.history.open!==UI.live)UI.live=Ed.history.begin(key==='opacity'?'Opacity Change':'Fill Opacity Change');
  for(const L of s)Ed.layers.set(L,{[key]:U.clamp(v/100,0,1)},{history:false});if(end){const t=UI.live;UI.live=null;t.commit();}}
function setBlend(id){const s=selected().filter(L=>!L.locks.all&&L.blend!==id);if(!s.length||!Ed.blends.get(id))return false;
  if(s.length===1)return Ed.layers.set(s[0],{blend:id});Ed.history.record('Blending Change',()=>{for(const L of s)Ed.layers.set(L,{blend:id},{history:false});},{op:{tool:'layer',action:'blend'}});return true;}
function build(c){UI.root=c;c.replaceChildren();c.classList.add('ed-layers','ed-lp');UI.rows.clear();
  c.addEventListener('contextmenu',e=>{if(!e.target.closest('input,select'))e.preventDefault();e.stopPropagation();});   // not the page's picture menu
  const r1=$('div','ed-layers-row'),blend=$('select','ed-blend');blend.name='blend';blend.setAttribute('aria-label','Blend mode');
  blend.title='Blend mode: how this layer mixes with the layers below (Shift+Alt+letter picks one, Shift+plus or minus steps)';blend.onchange=()=>{setBlend(blend.value);blend.blur();};
  const opacity=Ed.ui.field({key:'opacity',label:'Opacity',type:'range',min:0,max:100,step:1,unit:'%',title:'Layer opacity: how strongly the whole layer shows, effects included (digit keys set it when the tool has no opacity). Drag the label to change it.'},100,(v,end)=>liveSet('opacity',v,end));
  opacity.node.classList.add('ed-compact');
  const pmenu=mkButton('ed-lp-pmenu','Layers panel menu',ICON.menu,15);pmenu.setAttribute('aria-haspopup','menu');
  pmenu.onclick=e=>{e.stopPropagation();if(popup&&popup.anchor===pmenu){closePopup();return;}const r=pmenu.getBoundingClientRect();openPopup(panelMenuItems(),r.right-230,r.bottom+2,{label:'Layers panel menu',anchor:pmenu,focus:e.detail===0});};
  r1.append(blend,opacity.node,pmenu);
  const r2=$('div','ed-layers-row'),lockBox=$('div','ed-locks'),locks={};lockBox.append($('span','ed-field-label','Lock'));
  for(const [k,ic,title] of [['alpha',ICON.lockAlpha,'Lock transparent pixels: paint only where the layer already has pixels'],['pixels',ICON.lockPixels,'Lock image pixels: no painting or filters on this layer'],
    ['position',ICON.lockMove,'Lock position: the layer cannot be moved'],['all',ICON.lockAll,'Lock all: no painting, moving or blending changes']]){
    const b=mkButton('ed-lock',title,ic,14);b.dataset.lock=k;b.setAttribute('aria-pressed','false');b.setAttribute('aria-label',title.split(':')[0]);b.onclick=()=>toggleLock(k);locks[k]=b;lockBox.append(b);}
  const fill=Ed.ui.field({key:'fill',label:'Fill',type:'range',min:0,max:100,step:1,unit:'%',title:'Fill: how strongly the layer’s own pixels show; layer effects keep their strength (Shift+digits set it). Drag the label to change it.'},100,(v,end)=>liveSet('fill',v,end));
  fill.node.classList.add('ed-compact');r2.append(lockBox,fill.node);
  const list=$('div','ed-layer-list');list.setAttribute('role','listbox');list.setAttribute('aria-label','Layers, top first');list.setAttribute('aria-multiselectable','true');list.tabIndex=0;
  const hint=$('p','hint ed-lp-hint','Open an image to see its layers.');hint.hidden=true;const clipLine=$('div','ed-lp-clipline');clipLine.hidden=true;
  const foot=$('div','ed-layers-foot'),acts={};
  for(const [id,title,ic] of [['fx','Add a layer style','fx'],['mask','Add a layer mask (Alt-click: one that hides)',ICON.mask],
    ['adjust','Create a new fill or adjustment layer',ICON.adjust],['new','Create a new layer (Shift+Ctrl+N; Alt-click for options, Ctrl-click to put it below)',ICON.plus],
    ['duplicate','Duplicate layer (Ctrl+J)',ICON.dup],['delete','Delete layer',ICON.trash]]){
    const b=mkButton('ed-layer-action',title,ic,16);b.dataset.action=id;acts[id]=b;foot.append(b);}
  acts.fx.onclick=e=>popupAbove(acts.fx,styleItems(),'Layer style',e);
  acts.mask.onclick=e=>{addMask(e.altKey?(Ed.selection?'hide-selection':'hide'):(Ed.selection?'selection':'reveal'));};
  acts.adjust.onclick=e=>popupAbove(acts.adjust,adjustmentItems(),'New fill or adjustment layer',e);
  acts.new.onclick=e=>{if(e.altKey)newLayerDialog(e.ctrlKey||e.metaKey);else newLayer({},e.ctrlKey||e.metaKey);};
  acts.duplicate.onclick=()=>duplicateLayers(selected());acts.delete.onclick=()=>deleteFromButton();
  c.append(r1,r2,list,hint,foot);list.append(clipLine);
  Object.assign(UI,{pmenu,blend,opacity,fill,locks,list,hint,foot,acts,clipLine});wireList(list);}

// thumbnails: redrawn only when the layer's version, the size or the document changes
function thumbCanvas(key){let t=UI.thumbs.get(key);if(!t){t=document.createElement('canvas');t._k='';UI.thumbs.set(key,t);}return t;}
function paintThumb(t,src,key,o={}){const S=THUMB[P.thumbs]||34,dpr=Math.min(2,window.devicePixelRatio||1),px=Math.max(1,Math.round(S*dpr)),d=Ed.doc,k=key+'|'+px+'|'+d.width+'x'+d.height;
  if(t._k===k)return;t._k=k;if(t.width!==px||t.height!==px){t.width=px;t.height=px;}const g=t.getContext('2d');g.setTransform(1,0,0,1,0,0);g.clearRect(0,0,px,px);
  const s=Math.min(px/d.width,px/d.height),w=Math.max(1,Math.round(d.width*s)),h=Math.max(1,Math.round(d.height*s)),x=(px-w)>>1,y=(px-h)>>1;
  if(o.mask){g.fillStyle='#000';g.fillRect(x,y,w,h);}else{const c=Math.max(3,Math.round(px/6));for(let yy=0;yy<h;yy+=c)for(let xx=0;xx<w;xx+=c){g.fillStyle=((xx+yy)/c)%2?'#3b4a5a':'#2a3644';g.fillRect(x+xx,y+yy,Math.min(c,w-xx),Math.min(c,h-yy));}}
  g.imageSmoothingEnabled=s<1;g.imageSmoothingQuality='high';if(src)g.drawImage(src,x,y,w,h);
  if(o.disabled){g.strokeStyle='#e0413a';g.lineWidth=Math.max(1.5,px/14);g.lineCap='round';g.beginPath();g.moveTo(x+3,y+3);g.lineTo(x+w-3,y+h-3);g.moveTo(x+w-3,y+3);g.lineTo(x+3,y+h-3);g.stroke();}}
function makeRow(id){const r=$('div','ed-layer');r.dataset.id=id;r.id='edLayerRow-'+id;r.setAttribute('role','option');
  const eye=mkButton('ed-eye','',ICON.eye,15);eye.dataset.part='eye';eye.tabIndex=-1;
  const clip=$('span','ed-clip-arrow','↳'),pics=$('span','ed-pics'),name=$('span','ed-layer-name'),badges=$('span','ed-badges');clip.setAttribute('aria-hidden','true');
  r.append(eye,clip,pics,name,badges);r._ref={eye,clip,pics,name,badges};return r;}
function patchRow(r,L,i,info){const d=Ed.doc,A=act(),isActive=L===A,isSel=P.sel.has(L.id),{eye,clip,pics,name,badges}=r._ref;
  r.dataset.index=i;r.setAttribute('aria-selected',String(isSel));r.classList.toggle('active',isActive);r.classList.toggle('selected',isSel&&!isActive);r.classList.toggle('off',!L.visible);
  r.classList.toggle('clipped',info.member[i]);r.classList.toggle('base',info.base[i]);clip.hidden=!info.member[i];
  eye.title=L.visible?'Hide this layer (Alt-click: show only this one; drag down the eyes for more)':'Show this layer (Alt-click: show only this one; drag down the eyes for more)';eye.setAttribute('aria-pressed',String(L.visible));eye.setAttribute('aria-label',(L.visible?'Hide ':'Show ')+L.name);
  const want=[],tgt=isActive?d.target:'',view=P.maskView&&P.maskView.id===L.id;
  if(P.thumbs!=='none'){
    if(L.kind==='adjustment'){let s=UI.thumbs.get(L.id+'|adj');if(!s){s=$('span','ed-thumb ed-adjust-thumb');s.append(Ed.icon(ICON.adjust,{size:18}));UI.thumbs.set(L.id+'|adj',s);}
      s.dataset.part='pixels';s.title='Adjustment layer · double-click to edit it';want.push(s);}
    else{const t=thumbCanvas(L.id);t.className='ed-thumb'+(tgt==='pixels'?' target':'');t.dataset.part='pixels';t.title='Layer pixels · click to paint on them, Ctrl-click to select them';
      paintThumb(t,L.canvas,'p'+L.version);want.push(t);}
    if(L.mask){const m=thumbCanvas(L.id+'|mask');m.className='ed-thumb ed-mask-thumb'+(tgt==='mask'?' target':'')+(L.maskEnabled?'':' disabled')+(view?' viewing':'');m.dataset.part='mask';
      m.title=L.maskEnabled?'Layer mask · black hides, white shows. Shift-click: disable, Alt-click: show it alone, Ctrl-click: select it':'Layer mask (disabled) · Shift-click: enable, Alt-click: show it alone, Ctrl-click: select it';
      paintThumb(m,L.mask,'m'+L.version+'|'+L.maskEnabled,{mask:true,disabled:!L.maskEnabled});want.push(m);}}
  else if(L.mask){   // without thumbnails a mask still needs something to click
    let m=UI.thumbs.get(L.id+'|mtag');if(!m){m=$('span','ed-lp-masktag');m.append(Ed.icon(ICON.mask,{size:13}));UI.thumbs.set(L.id+'|mtag',m);}
    m.dataset.part='mask';m.classList.toggle('target',tgt==='mask');m.classList.toggle('disabled',!L.maskEnabled);m.title='Layer mask';want.push(m);}
  if(want.length!==pics.children.length||want.some((x,k)=>pics.children[k]!==x))pics.replaceChildren(...want);
  if(!r.contains(UI.editing)&&name.textContent!==L.name)name.textContent=L.name;name.title=L.name+' · double-click to rename';
  const bs=[];let fx=false;for(const fn of Ed.layerBadges()){try{const b=fn(L,r);if(b){const n=typeof b==='string'?$('span','ed-badge',b):b;if(/\bfx\b/.test(n.textContent||''))fx=true;bs.push(n);}}catch(e){}}
  if(!fx&&Object.values(L.effects||{}).some(p=>p&&p.enabled!==false)){const n=$('span','ed-badge ed-lp-fx','fx');n.title='Layer style';bs.push(n);}
  if(Object.values(L.locks).some(Boolean)){const k=$('span','ed-badge ed-lock-badge');k.title=L.locks.all?'Locked: everything':'Locked';
    k.append(Ed.icon(ICON.lockAll,{size:12}));if(L.locks.all)k.classList.add('all');bs.push(k);}
  badges.replaceChildren(...bs);return r;}
function update(){const d=Ed.doc,L=act(),root=UI.root;syncSel();root.classList.toggle('empty',!d);
  for(const k of Object.keys(THUMB))root.classList.toggle('thumbs-'+k,P.thumbs===k);
  const modes=Ed.blends.list(),sig=modes.map(b=>b.id+b.label).join();if(UI.blend.dataset.sig!==sig){UI.blend.dataset.sig=sig;UI.blend.replaceChildren();let g=null;
    for(const b of modes){if(g&&g!==b.group)UI.blend.append($('hr'));g=b.group;const o=$('option','',b.label);o.value=b.id;UI.blend.append(o);}}
  const allLocked=!!(L&&L.locks.all);UI.blend.disabled=!L||allLocked;for(const f of [UI.opacity,UI.fill])for(const x of f.node.querySelectorAll('input,button'))x.disabled=!L||allLocked;
  if(L){if(document.activeElement!==UI.blend)UI.blend.value=L.blend;UI.opacity.sync(Math.round(L.opacity*100));UI.fill.sync(Math.round(L.fill*100));}
  for(const [k,b] of Object.entries(UI.locks)){const on=!!(L&&L.locks[k]),implied=!!(L&&k!=='all'&&L.locks.all);b.disabled=!L||implied||(k==='alpha'&&L.kind==='adjustment');b.setAttribute('aria-pressed',String(on||implied));b.classList.toggle('on',on||implied);}
  UI.pmenu.disabled=!d;
  // rows, top first
  const rows=[],info={member:[],base:[]};
  if(d){const n=d.layers.length;for(const [s,e] of units(d.layers)){info.base[s]=e>s+1;info.member[s]=false;for(let k=s+1;k<e;k++){info.member[k]=true;info.base[k]=false;}}
    for(let i=n-1;i>=0;i--){const Ly=d.layers[i];let r=UI.rows.get(Ly.id);if(!r){r=makeRow(Ly.id);UI.rows.set(Ly.id,r);}rows.push(patchRow(r,Ly,i,info));}}
  const list=UI.list,cur=[...list.children].filter(x=>x.classList.contains('ed-layer'));
  if(rows.length!==cur.length||rows.some((r,k)=>cur[k]!==r)){for(const x of cur)if(!rows.includes(x))x.remove();for(const r of rows)list.append(r);list.append(UI.clipLine);}
  UI.hint.hidden=!!d;
  const live=new Set(d?d.layers.map(x=>x.id):[]);for(const k of UI.rows.keys())if(!live.has(k))UI.rows.delete(k);for(const k of UI.thumbs.keys())if(!live.has(k.split('|')[0]))UI.thumbs.delete(k);
  const curRow=L&&UI.rows.get(L.id);if(curRow){list.setAttribute('aria-activedescendant',curRow.id);if(document.activeElement===list){const a=curRow.offsetTop,b=a+curRow.offsetHeight;if(a<list.scrollTop)list.scrollTop=a;else if(b>list.scrollTop+list.clientHeight)list.scrollTop=b-list.clientHeight;}}
  // footer
  const a=UI.acts,s=d?selected():[];
  a.fx.hidden=!styleItems().some(x=>!x.sep);a.fx.disabled=!L||L.kind!=='pixel';a.adjust.hidden=!adjustmentItems().length;a.adjust.disabled=!d;
  a.mask.disabled=!L||!!L.mask;a.new.disabled=!d;a.duplicate.disabled=!L;a.delete.disabled=!d||(!(d.target==='mask'&&L&&L.mask)&&d.layers.length<=s.length);
  a.delete.title=d&&d.target==='mask'&&L&&L.mask?'Delete the layer mask':s.length>1?`Delete the ${s.length} selected layers`:'Delete layer';}
Ed.registerPanel('layers',{title:'Layers',order:10,render(c){if(UI.root!==c)build(c);update();}});

// ----- pointer and keyboard in the list ---------------------------------------------------------------------------------
const rowOf=t=>t&&t.closest?t.closest('#edPanel-layers .ed-layer'):null;
const layerOfRow=r=>r?Ed.layers.get(r.dataset.id):null;
const partOf=t=>t&&t.closest&&t.closest('[data-part]')?t.closest('[data-part]').dataset.part:'row';
const visRows=()=>[...UI.list.querySelectorAll('.ed-layer')];
function clickRow(L,part){P.sel=new Set([L.id]);P.anchor=L.id;const t=part==='mask'&&L.mask?'mask':part==='pixels'?(L.kind==='adjustment'&&L.mask?'mask':'pixels'):L===act()?Ed.doc.target:undefined;
  if(P.maskView&&P.maskView.id===L.id&&part==='pixels')P.maskView=null;Ed.layers.setActive(L,t);Ed.view.redrawOverlay();announce();}
function toggleSelect(L){if(P.sel.has(L.id)&&P.sel.size>1){P.sel.delete(L.id);if(act()===L){const rest=layers().filter(x=>P.sel.has(x.id));if(rest.length)Ed.layers.setActive(rest[rest.length-1]);}P.anchor=act().id;}
  else{P.sel.add(L.id);Ed.layers.setActive(L);P.anchor=L.id;}announce();}
function rangeSelect(L){const anchor=Ed.layers.get(P.anchor)||act(),a=ix(anchor),b=ix(L),lo=Math.min(a,b),hi=Math.max(a,b);
  P.sel=new Set(layers().slice(lo,hi+1).map(x=>x.id));Ed.layers.setActive(L);announce();}
// Alt over the line between two rows that are neighbours in the stack: the upper one clips to (or is released from) the lower one.
function boundaryAt(e,r){const rows=visRows(),k=rows.indexOf(r),b=r.getBoundingClientRect();let up=null,low=null;
  if(e.clientY-b.top<=6&&k>0){up=layerOfRow(rows[k-1]);low=layerOfRow(r);}else if(b.bottom-e.clientY<=6&&k<rows.length-1){up=layerOfRow(r);low=layerOfRow(rows[k+1]);}
  if(!up||!low||ix(up)!==ix(low)+1)return null;return{up,low,y:e.clientY-b.top<=6?b.top:b.bottom};}
function showClipHint(b){const l=UI.clipLine;if(!l||!UI.list)return;if(!b){l.hidden=true;UI.list.style.cursor='';return;}const lb=UI.list.getBoundingClientRect();
  l.hidden=false;l.style.top=(b.y-lb.top+UI.list.scrollTop-1)+'px';UI.list.style.cursor=CLIP_CURSOR;}
function eyeDown(e,L){e.preventDefault();e.stopPropagation();if(e.altKey){solo(L);return;}
  const vis=!L.visible,changed=new Map(),flip=x=>{if(x&&x.visible!==vis&&!changed.has(x)){changed.set(x,x.visible);Ed.layers.set(x,{visible:vis},{history:false});}};flip(L);
  const id=e.pointerId,move=ev=>{if(ev.pointerId!==id)return;const t=document.elementFromPoint(ev.clientX,ev.clientY),r=rowOf(t);if(r&&t.closest('.ed-eye'))flip(layerOfRow(r));};
  const up=ev=>{if(ev.pointerId!==id)return;window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);
    for(const [x,was] of changed)if(Ed.layers.get(x.id))Ed.layers.set(x,{visible:was},{history:false});   // put back, then record the whole drag as one state
    setVisible([...changed.keys()].filter(x=>Ed.layers.get(x.id)),vis);};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);}
function wireList(list){
  list.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('input'))return;const r=rowOf(e.target),L=layerOfRow(r);if(!L)return;const part=partOf(e.target),ctrl=e.ctrlKey||e.metaKey;
    if(UI.editing&&!r.contains(UI.editing))UI.editing.blur();
    if(part==='eye'){eyeDown(e,L);return;}
    if(e.altKey&&!ctrl&&part!=='mask'){const b=boundaryAt(e,r);if(b){e.preventDefault();showClipHint(null);if(b.up.clip)releaseClip([b.up]);else createClip([b.up]);return;}}
    if(ctrl&&(part==='pixels'||part==='mask')){e.preventDefault();loadSelection(L,part,modeOf(e));return;}
    if(part==='mask'&&e.shiftKey&&!e.altKey){e.preventDefault();toggleMask(L);return;}
    if(part==='mask'&&e.altKey){e.preventDefault();const mode=e.shiftKey?'ruby':'alone';setMaskView(P.maskView&&P.maskView.id===L.id&&P.maskView.mode===mode?null:{id:L.id,mode});return;}
    if(ctrl){e.preventDefault();toggleSelect(L);return;}
    if(e.shiftKey){e.preventDefault();rangeSelect(L);return;}
    dragRow(e,r,L,part);});
  list.addEventListener('pointermove',e=>{if(UI.dragging)return;const r=rowOf(e.target);showClipHint(r&&e.altKey&&!(e.ctrlKey||e.metaKey)&&partOf(e.target)!=='mask'?boundaryAt(e,r):null);});
  list.addEventListener('pointerleave',()=>showClipHint(null));
  list.addEventListener('dblclick',e=>{const r=rowOf(e.target),L=layerOfRow(r);if(!L||e.target.closest('input')||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;const part=partOf(e.target);
    if(e.target.closest('.ed-layer-name')){e.stopPropagation();startRename(L);return;}
    if(part==='eye')return;if(part==='mask'){if(hasProps())Ed.panels.show('properties');return;}openOptions(L);});
  // the panel has its own menus: the page's context menu (app.js) must not open over them
  list.addEventListener('contextmenu',e=>{e.preventDefault();e.stopPropagation();const r=rowOf(e.target),L=layerOfRow(r);if(!L){if(Ed.doc)openPopup(panelMenuItems(),e.clientX,e.clientY,{label:'Layers panel menu',focus:false});return;}
    const part=partOf(e.target);if(!P.sel.has(L.id))clickRow(L,part==='mask'?'mask':undefined);
    const items=part==='eye'?eyeItems(L):part==='mask'&&L.mask?maskItems(L):part==='pixels'&&L.kind==='pixel'?thumbItems(L):rowItems(L);openPopup(items,e.clientX,e.clientY,{label:'Layer menu',focus:false});});
  list.addEventListener('click',e=>{const t=e.target.closest('.ed-eye');if(t&&e.detail===0){const L=layerOfRow(rowOf(t));if(L)setVisible([L],!L.visible);}});   // keyboard activation of the eye
  list.addEventListener('keydown',e=>{if(e.target!==list||!Ed.doc)return;const L=act();if(!L)return;
    if(e.key==='ArrowUp'||e.key==='ArrowDown'||e.key==='Home'||e.key==='End'){e.preventDefault();e.stopPropagation();const rows=visRows(),k=rows.indexOf(UI.rows.get(L.id));if(!rows.length)return;
      const j=e.key==='Home'?0:e.key==='End'?rows.length-1:U.clamp(k<0?0:k+(e.key==='ArrowUp'?-1:1),0,rows.length-1),N=layerOfRow(rows[j]);if(!N)return;
      if(e.shiftKey){P.sel.add(N.id);Ed.layers.setActive(N);announce();}else clickRow(N,undefined);}
    else if(e.key==='F2'||e.key==='Enter'){e.preventDefault();e.stopPropagation();startRename(L);}
    else if((e.key==='Delete'||e.key==='Backspace')&&!Ed.selection){e.preventDefault();e.stopPropagation();deleteLayers(selected());}});}
// A press on a row: a click selects it (or its mask / pixels), a 5 px drag reorders the selected rows. Dropped on New it
// duplicates them, on the bin it deletes them.
function dragRow(e,r,L,part){const id=e.pointerId,x0=e.clientX,y0=e.clientY,list=UI.list;let moving=false,line=null,hot=null,scroll=0;
  const block=P.sel.has(L.id)&&P.sel.size>1?selected():[L];
  const slot=y=>{const rows=visRows();let k=0;for(const x of rows){const b=x.getBoundingClientRect();if(y>b.top+b.height/2)k++;}return{k,rows};};
  const setHot=b=>{if(hot===b)return;if(hot)hot.classList.remove('drop');hot=b;if(hot)hot.classList.add('drop');};
  const autoscroll=y=>{const b=list.getBoundingClientRect();scroll=y<b.top+16?-6:y>b.bottom-16?6:0;};
  const timer=setInterval(()=>{if(moving&&scroll)list.scrollTop+=scroll;},30);
  const move=ev=>{if(ev.pointerId!==id)return;if(!moving&&Math.hypot(ev.clientX-x0,ev.clientY-y0)<5)return;
    if(!moving){moving=true;UI.dragging=true;r.classList.add('dragging');line=$('div','ed-drop-line');list.append(line);try{r.setPointerCapture(id);}catch(x){}}
    const over=document.elementFromPoint(ev.clientX,ev.clientY),btn=over&&over.closest?over.closest('#edPanel-layers .ed-layers-foot [data-action]'):null;
    setHot(btn&&(btn.dataset.action==='delete'||btn.dataset.action==='new')?btn:null);autoscroll(ev.clientY);
    const {k,rows}=slot(ev.clientY);if(!rows.length||hot){line.hidden=true;return;}line.hidden=false;const ref=rows[Math.min(k,rows.length-1)],b=ref.getBoundingClientRect(),lb=list.getBoundingClientRect();
    line.style.top=((k<rows.length?b.top:b.bottom)-lb.top+list.scrollTop-1)+'px';};
  const up=ev=>{if(ev.pointerId!==id)return;window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);clearInterval(timer);
    if(line)line.remove();r.classList.remove('dragging');UI.dragging=false;const h=hot;setHot(null);
    if(!moving){if(ev.type==='pointerup')clickRow(L,part);return;}if(ev.type!=='pointerup')return;
    if(h){if(h.dataset.action==='new')duplicateLayers(block);else if(h.dataset.action==='delete')deleteLayers(block);return;}
    const {k,rows}=slot(ev.clientY);let below=null;for(let j=k;j<rows.length;j++){const x=layerOfRow(rows[j]);if(x&&!block.includes(x)){below=x;break;}}
    moveBlock(block,below);};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);}
function startRename(L){if(!L||!UI.root)return false;if(!UI.root.isConnected||UI.root.hidden)Ed.panels.show('layers');const r=UI.rows.get(L.id);if(!r)return false;const name=r._ref.name;
  if(UI.editing)UI.editing.blur();const i=$('input','ed-rename');i.type='text';i.value=L.name;i.setAttribute('aria-label','Layer name');UI.editing=i;name.hidden=true;name.after(i);i.focus();i.select();let done=false;
  const end=ok=>{if(done)return;done=true;UI.editing=null;const v=i.value;i.remove();name.hidden=false;if(!(ok&&Ed.layers.get(L.id)&&rename(L,v)))refresh();};
  i.onkeydown=e=>{e.stopPropagation();if(e.key==='Enter'){e.preventDefault();end(true);}else if(e.key==='Escape'){e.preventDefault();end(false);}};
  i.onblur=()=>end(true);i.onpointerdown=e=>e.stopPropagation();i.ondblclick=e=>e.stopPropagation();return true;}

// ----- the mask on the canvas: alone (grayscale) or as a red overlay -----------------------------------------------------
let ruby=null;
function rubyOf(m,W,H){if(!ruby||ruby.width!==W||ruby.height!==H)ruby=U.canvas(W,H);const g=ruby.getContext('2d');g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;
  g.globalCompositeOperation='copy';g.fillStyle='#ff0000';g.fillRect(0,0,W,H);g.globalCompositeOperation='destination-out';g.drawImage(m,0,0);g.globalCompositeOperation='source-over';return ruby;}
if(Ed.registerOverlay)Ed.registerOverlay('layers-mask',{order:5,draw(ctx,v){const mv=P.maskView,d=Ed.doc;if(!mv||!d)return false;const L=Ed.layers.get(mv.id);if(!L||!L.mask)return false;
  const pv=Ed.preview.active;let m=L.mask;if(pv&&pv.layer===L&&pv.target==='mask')m=(pv.kind==='stroke'?pv.scratch:pv.canvas)||m;   // a stroke on the mask shows as it is painted
  ctx.save();ctx.translate(v.x,v.y);ctx.scale(v.scale,v.scale);ctx.imageSmoothingEnabled=v.scale<1;
  if(mv.mode==='alone'){ctx.fillStyle='#000';ctx.fillRect(0,0,d.width,d.height);ctx.drawImage(m,0,0);}else{ctx.globalAlpha=.5;ctx.drawImage(rubyOf(m,d.width,d.height),0,0);}
  ctx.restore();return true;}});

// ----- keys Photoshop has for layers ------------------------------------------------------------------------------------
const BLEND_KEYS={KeyN:'normal',KeyI:'dissolve',KeyK:'darken',KeyM:'multiply',KeyB:'color-burn',KeyA:'linear-burn',KeyG:'lighten',KeyS:'screen',KeyD:'color-dodge',KeyW:'linear-dodge',
  KeyO:'overlay',KeyF:'soft-light',KeyH:'hard-light',KeyV:'vivid-light',KeyJ:'linear-light',KeyZ:'pin-light',KeyL:'hard-mix',KeyE:'difference',KeyX:'exclusion',KeyU:'hue',KeyT:'saturation',KeyC:'color',KeyY:'luminosity'};
const toolHas=k=>(Ed.tools.get(Ed.tool)?.options||[]).some(o=>o.key===k);
function cycleBlend(dir){const L=act();if(!L)return;const list=Ed.blends.list().map(b=>b.id),k=list.indexOf(L.blend);setBlend(list[(k+dir+list.length)%list.length]);}
let lastFill=null;
function fillDigit(n){const now=performance.now();let v=n===0?100:n*10;if(lastFill&&now-lastFill.t<700){v=lastFill.n*10+n||100;lastFill=null;}else lastFill={n,t:now};
  const s=selected().filter(L=>!L.locks.all);if(!s.length)return;Ed.history.record('Fill Opacity Change',()=>{for(const L of s)Ed.layers.set(L,{fill:v/100},{history:false});},{op:{tool:'layer',action:'fill'}});}
function stepLayer(code,shift){const list=layers(),L=act();if(!L)return;const k=ix(L);
  const j=code==='BracketRight'?Math.min(list.length-1,k+1):code==='BracketLeft'?Math.max(0,k-1):code==='Period'?list.length-1:0;const N=list[j];
  if(shift){const lo=Math.min(k,j),hi=Math.max(k,j);for(let x=lo;x<=hi;x++)P.sel.add(list[x].id);Ed.layers.setActive(N);announce();}
  else if(N!==L)clickRow(N,undefined);}
document.addEventListener('keydown',e=>{if(e.defaultPrevented||!Ed.doc||typing(e)||modalOpen()||(typeof S!=='undefined'&&S.busy))return;const ctrl=e.ctrlKey||e.metaKey;
  if(e.altKey&&!ctrl&&['BracketLeft','BracketRight','Comma','Period'].includes(e.code)){e.preventDefault();stepLayer(e.code,e.shiftKey);return;}
  if(e.altKey&&e.shiftKey&&!ctrl&&BLEND_KEYS[e.code]&&!toolHas('mode')&&!toolHas('blend')){e.preventDefault();setBlend(BLEND_KEYS[e.code]);return;}
  if(!e.altKey&&!ctrl&&e.shiftKey&&(e.code==='Equal'||e.code==='Minus')&&!toolHas('mode')&&!toolHas('blend')){e.preventDefault();cycleBlend(e.code==='Equal'?1:-1);return;}
  if(!e.altKey&&!ctrl&&e.shiftKey&&/^Digit\d$/.test(e.code)&&!toolHas('flow')&&!toolHas('opacity')){e.preventDefault();fillDigit(+e.code.slice(5));return;}
  if(!e.altKey&&!ctrl&&!e.shiftKey&&e.code==='Backslash'){const L=act();if(L&&L.mask){e.preventDefault();setMaskView(P.maskView&&P.maskView.id===L.id&&P.maskView.mode==='ruby'?null:{id:L.id,mode:'ruby'});}}});
document.addEventListener('keyup',e=>{if(e.key==='Alt')showClipHint(null);});

// ----- keeping the state in step ----------------------------------------------------------------------------------------
Ed.on('open',()=>{P.sel.clear();P.anchor=null;P.solo=null;P.maskView=null;UI.thumbs.clear();closePopup();});
Ed.on('active',()=>{syncSel();if(P.maskView&&(!act()||act().id!==P.maskView.id)){P.maskView=null;Ed.view.redrawOverlay();}});
Ed.on('layers',()=>{syncSel();if(P.maskView){const L=Ed.layers.get(P.maskView.id);if(!L||!L.mask){P.maskView=null;Ed.view.redrawOverlay();}}});
for(const n of ['blends','registry','menus'])Ed.on(n,refresh);

// ----- public API --------------------------------------------------------------------------------------------------------
Ed.layersPanel={selected:()=>selected(),select:(refs,activeRef)=>select([].concat(refs??[]),activeRef),isSelected:r=>{const L=Ed.layers.get(r);return !!L&&P.sel.has(L.id);},
  showMask:(r,mode)=>{const L=Ed.layers.get(r);setMaskView(L&&mode?{id:L.id,mode}:null);},get maskView(){return P.maskView?{...P.maskView}:null;},
  merge:(refs,o={})=>{const list=[].concat(refs||[]).map(r=>Ed.layers.get(r)).filter(Boolean).sort((a,b)=>ix(a)-ix(b));return list.length?mergeRange(list,o.label||'Merge Layers',o):null;}};
})();
