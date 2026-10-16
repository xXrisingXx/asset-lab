'use strict';
// Editor UI shell: the menubar, the options bar, the tool strip (flyouts, color chips), the panel dock with the basic Layers
// and History panels, dialogs, the view overlay (marching ants, pixel grid, brush outline, tool overlays), pointer and
// keyboard handling, Focus mode, and the basic tools (Hand, Zoom, Eyedropper, Brush, Eraser) and menus.
// The engine and the whole API are documented in editor/core.js. Uses app.js globals at run time: S, save, exportPNG,
// revertOriginal, fit, draw, dragMoved, confirmAction.
(()=>{
const Ed=window.Ed,U=Ed.util;
const byId=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const iconOf=t=>{try{const i=typeof t.icon==='function'?t.icon():t.icon;if(i instanceof Element)return t.icon instanceof Element?i.cloneNode(true):i;return el('span','ed-glyph',i||String(t.label||'?').slice(0,1));}catch(e){return el('span','ed-glyph','?');}};
const tipOf=t=>t.label+(t.key?` (${t.key.toUpperCase()})`:'');
const typing=e=>{const t=e.target;return !!(t&&t.closest&&t.closest('input,textarea,select,[contenteditable=""],[contenteditable="true"]'));};
const modalOpen=()=>!!document.querySelector('dialog[open]');
const I={hand:'M8 12.5V6a1.5 1.5 0 0 1 3 0v5.5M11 6V4.5a1.5 1.5 0 0 1 3 0V11m0-5a1.5 1.5 0 0 1 3 0v5.5m0-3a1.5 1.5 0 0 1 3 0v6.5a6 6 0 0 1-6 6h-1.2a6 6 0 0 1-4.5-2l-3.6-4.2a1.6 1.6 0 0 1 2.4-2.1L8 15',
  zoom:['M10.5 4a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13z','M15.3 15.3L21 21','M8 10.5h5M10.5 8v5'],
  eyedropper:['M14.5 5.5l4 4','M17 3a2.1 2.1 0 0 1 3 3l-2.5 2.5-3-3z','M15.5 8.5L7 17l-3 1 1-3 8.5-8.5'],
  brush:['M20 4c-4 2.5-8.5 7-10.5 10l1.5 1.5c3-2 7.5-6.5 10-10.5z','M9.5 14c-2.2 0-3.5 1.6-3.5 3.4 0 1.4-.9 2.2-2 2.6 3.6 1.2 7.6.1 7-4.5'],
  eraser:['M8.5 19.5l-4.2-4.2a1.5 1.5 0 0 1 0-2.1l8.9-8.9a1.5 1.5 0 0 1 2.1 0l4.2 4.2a1.5 1.5 0 0 1 0 2.1l-8.1 8.9z','M8.5 19.5H20','M9 9.5l5.5 5.5'],
  eye:['M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12s-3.5 6.5-9.5 6.5S2.5 12 2.5 12z','M12 9.2a2.8 2.8 0 1 0 0 5.6a2.8 2.8 0 1 0 0-5.6z'],
  plus:['M5 4h10l4 4v12H5z','M12 10v6M9 13h6'],dup:['M8 8h11v11H8z','M5 16V5h11'],mask:['M4 5h16v14H4z','M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z'],
  trash:['M5 7h14','M10 7V5h4v2','M7 7l1 12h8l1-12','M10.5 10.5v5.5M13.5 10.5v5.5'],
  lockAlpha:['M4 4h7v7H4zM13 13h7v7h-7z','M13 4h7v7h-7zM4 13h7v7H4z'],lockPixels:'M16.5 4.5l3 3L9 18l-4 1 1-4z',lockMove:['M12 3v18M3 12h18','M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5'],
  lockAll:['M6.5 11h11v9h-11z','M9 11V8a3 3 0 0 1 6 0v3'],swap:['M7 4.5h8.5a3 3 0 0 1 3 3V15','M16 12.5l2.5 2.5L21 12.5','M4.5 7L7 4.5 9.5 7'],
  adjust:['M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z','M12 4v16a8 8 0 0 0 0-16z']};
const EYEDROPPER=`url("data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="M3.5 20.5l1-3.2 9-9 2.2 2.2-9 9z" fill="#fff" stroke="#000" stroke-width="1.2"/><path d="M13.6 8.2l2.6-2.6a2 2 0 0 1 2.9 2.9l-2.6 2.6-1-1-.9-.9z" fill="#222" stroke="#000" stroke-width="1.2"/></svg>')}") 3 21, crosshair`;
const view=byId('view'),overlay=byId('viewOverlay'),viewport=byId('viewport');

// ----- menubar ---------------------------------------------------------------------------------------------------------
const bar=byId('edMenubar');let menuOpen=null;   // {name, button, stack:[elements]}
// Chrome sends pointerenter to whatever a menu opens under a resting pointer; only a pointer that really moved picks items.
let pointerAt=null,still=null;document.addEventListener('pointermove',e=>{pointerAt={x:e.clientX,y:e.clientY};},true);
const moved=e=>{if(still&&e.clientX===still.x&&e.clientY===still.y)return false;still=null;return true;};
function renderMenubar(){bar.replaceChildren();for(const top of Ed.menu.tree()){const b=btn('ed-menu-top',top.name);b.setAttribute('role','menuitem');b.setAttribute('aria-haspopup','true');b.setAttribute('aria-expanded','false');b.dataset.menu=top.name;
    b.onclick=e=>{e.stopPropagation();menuOpen&&menuOpen.name===top.name?closeMenus():openTop(top.name,b,e.detail===0);};
    b.onpointerenter=e=>{if(menuOpen&&menuOpen.name!==top.name&&moved(e))openTop(top.name,b,false);};
    b.onkeydown=e=>{if(e.key==='ArrowDown'){e.preventDefault();openTop(top.name,b,true);}else if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();stepTop(b,e.key==='ArrowRight'?1:-1,!!menuOpen);}};
    bar.append(b);}}
function stepTop(b,d,open){const all=[...bar.children],n=all[(all.indexOf(b)+d+all.length)%all.length];n.focus();if(open)openTop(n.dataset.menu,n,true);}
function closeMenus(){if(!menuOpen)return;for(const m of menuOpen.stack)m.remove();menuOpen.button.setAttribute('aria-expanded','false');menuOpen.button.classList.remove('open');menuOpen=null;}
function openTop(name,b,focus){closeMenus();const node=Ed.menu.tree().find(t=>t.name===name);if(!node)return;still=pointerAt&&{...pointerAt};const m=buildMenu(node.children,0);document.body.append(m);
  const r=b.getBoundingClientRect();place(m,r.left,r.bottom+2);menuOpen={name,button:b,stack:[m]};b.setAttribute('aria-expanded','true');b.classList.add('open');if(focus)focusFirst(m);}
function place(m,x,y){m.style.left=Math.max(4,Math.min(x,innerWidth-m.offsetWidth-6))+'px';m.style.top=Math.max(4,Math.min(y,innerHeight-m.offsetHeight-6))+'px';}
const items=m=>[...m.querySelectorAll(':scope > .ed-menu-item:not(:disabled)')];
function focusFirst(m){const i=items(m)[0];if(i)i.focus();}
function closeSubs(level){if(!menuOpen)return;for(const m of menuOpen.stack.splice(level+1))m.remove();}
function openSub(b,node,level,focus){if(!menuOpen)return;closeSubs(level);const m=buildMenu(node.children,level+1);document.body.append(m);const r=b.getBoundingClientRect();
  place(m,r.right-2,r.top-4);if(m.getBoundingClientRect().left<r.right-10)place(m,r.left-m.offsetWidth+2,r.top-4);menuOpen.stack.push(m);if(focus)focusFirst(m);}
function buildMenu(list,level){const m=el('div','ed-menu');m.setAttribute('role','menu');m.dataset.level=level;
  if(!list.length)m.append(el('div','ed-menu-empty','Nothing here yet'));
  for(const it of list){if(it.separatorBefore&&m.children.length)m.append(el('div','ed-menu-sep'));const b=btn('ed-menu-item');
    if(it.children){b.setAttribute('role','menuitem');b.setAttribute('aria-haspopup','true');b.append(el('span','ed-menu-check'),el('span','ed-menu-label',it.name),el('span','ed-menu-arrow','›'));
      b.onpointerenter=e=>{if(moved(e))openSub(b,it,level);};b.onclick=e=>{e.stopPropagation();openSub(b,it,level,true);};b._sub=it;}
    else{b.setAttribute('role',it.checkable?'menuitemcheckbox':'menuitem');if(it.checkable)b.setAttribute('aria-checked',String(it.checked));b.disabled=!it.enabled;b.dataset.path=it.path;
      b.append(el('span','ed-menu-check',it.checked?'✓':''),el('span','ed-menu-label',it.label),el('span','ed-menu-key',it.shortcut));
      b.onpointerenter=e=>{if(!moved(e))return;closeSubs(level);b.focus({preventScroll:true});};b.onclick=e=>{e.stopPropagation();closeMenus();Ed.menu.run(it.path);};}
    m.append(b);}
  m.onkeydown=e=>{const all=items(m),i=all.indexOf(document.activeElement);
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const n=all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length];if(n)n.focus();}
    else if(e.key==='Home'||e.key==='End'){e.preventDefault();(e.key==='Home'?all[0]:all[all.length-1])?.focus();}
    else if(e.key==='ArrowRight'){e.preventDefault();const c=document.activeElement;if(c&&c._sub)openSub(c,c._sub,level,true);else if(menuOpen)stepTop(menuOpen.button,1,true);}
    else if(e.key==='ArrowLeft'){e.preventDefault();if(level>0){closeSubs(level-1);const p=menuOpen.stack[level-1];(items(p).find(x=>x.getAttribute('aria-haspopup'))||items(p)[0])?.focus();}else if(menuOpen)stepTop(menuOpen.button,-1,true);}
    else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();const b=menuOpen&&menuOpen.button;closeMenus();if(b)b.focus();}
    else if(e.key==='Tab')closeMenus();};
  return m;}
document.addEventListener('pointerdown',e=>{if(menuOpen&&!e.target.closest('.ed-menu,.ed-menubar'))closeMenus();if(flyout&&!e.target.closest('.ed-flyout'))closeFlyout();},true);
window.addEventListener('resize',()=>{closeMenus();closeFlyout();});

// ----- fields: tool options and dialog fields share one renderer -------------------------------------------------------
// Returns {node, sync(value)}. set(value) is called on every user change.
function field(o,value,set){const wrap=el(o.type==='checkbox'?'label':'div','ed-field ed-'+(o.type||'text'));if(o.title)wrap.title=o.title;wrap.dataset.key=o.key||'';
  const label=o.label&&o.type!=='checkbox'&&o.type!=='button'?el('label','ed-field-label',o.label):null;let sync=()=>{};const id='edf'+Math.random().toString(36).slice(2,8);
  const blurOnEnter=i=>i.addEventListener('keydown',e=>{if(e.key==='Enter'&&!i.closest('dialog')){e.preventDefault();i.dispatchEvent(new Event('change',{bubbles:true}));i.blur();}else if(e.key==='Escape'&&!i.closest('dialog'))i.blur();});
  const num=(v,d)=>{const n=Number(v);return isFinite(n)?n:d;};
  if(label)wrap.append(label);
  if(o.type==='range'||o.type==='number'){const n=el('input');n.type='number';n.name=o.key||'';n.id=o.id||id;if(label)label.htmlFor=n.id;for(const k of ['min','max','step'])if(o[k]!=null)n[k]=o[k];n.value=value;blurOnEnter(n);
    let r=null;if(o.type==='range'){r=el('input');r.type='range';r.tabIndex=-1;r.setAttribute('aria-hidden','true');for(const k of ['min','max','step'])if(o[k]!=null)r[k]=o[k];r.value=value;
      r.oninput=()=>{n.value=r.value;set(num(r.value,value));};r.onchange=()=>set(num(r.value,value),true);}
    n.oninput=()=>{const v=num(n.value,null);if(v!=null&&n.value!==''){if(r)r.value=v;set(v);}};n.onchange=()=>{const v=num(n.value,null);if(v!=null&&n.value!==''){if(r)r.value=v;set(v,true);}};
    if(label&&o.type==='range')scrub(label,n,o,v=>{n.value=v;if(r)r.value=v;set(v);},()=>set(num(n.value,value),true));
    wrap.append(...(r?[r]:[]),n);if(o.unit)wrap.append(el('span','ed-unit',o.unit));sync=v=>{if(document.activeElement!==n)n.value=v;if(r&&document.activeElement!==r)r.value=v;};}
  else if(o.type==='select'){const s=el('select');s.name=o.key||'';s.id=o.id||id;if(o.translate===false)s.setAttribute('translate','no');/* names (fonts) stay as they are */if(label)label.htmlFor=s.id;for(const x of o.options||[]){if(x==='-'){s.append(el('hr'));continue;}const op=el('option','',x.label??String(x.value??x));op.value=String(x.value??x);s.append(op);}
    s.value=String(value);s.onchange=()=>{const x=(o.options||[]).find(x=>String(x.value??x)===s.value);set(x?(x.value??x):s.value,true);};wrap.append(s);sync=v=>{s.value=String(v);};}
  else if(o.type==='checkbox'){const c=el('input');c.type='checkbox';c.name=o.key||'';c.id=o.id||id;c.checked=!!value;c.onchange=()=>set(c.checked,true);wrap.append(c,el('span','',o.label||''));sync=v=>{c.checked=!!v;};}
  else if(o.type==='color'){const c=el('input');c.type='color';c.name=o.key||'';c.id=o.id||id;if(label)label.htmlFor=c.id;c.value=value||'#000000';c.oninput=()=>set(c.value);c.onchange=()=>set(c.value,true);wrap.append(c);sync=v=>{c.value=v;};}
  else if(o.type==='segmented'){const g=el('div','segmented');g.setAttribute('role','radiogroup');if(o.label)g.setAttribute('aria-label',o.label);
    for(const x of o.options||[]){const v=x.value??x,l=el('label','ed-seg'),r=el('input');r.type='radio';r.name=o.key||id;r.value=String(v);r.checked=v===value;l.title=x.title||'';
      r.onchange=()=>{if(r.checked)set(v,true);};l.append(r,el('span','',x.label??String(v)));g.append(l);}
    wrap.append(g);sync=v=>{for(const r of g.querySelectorAll('input'))r.checked=r.value===String(v);};}
  else if(o.type==='button'){const b=btn('ed-option-button',o.label||o.key);b.name=o.key||'';if(o.id)b.id=o.id;b.onclick=()=>{try{o.action&&o.action();}catch(e){Ed.message(e.message,true);}};wrap.append(b);}
  else if(o.type==='text'||!o.type){const t=el('input');t.type='text';t.name=o.key||'';t.id=o.id||id;if(label)label.htmlFor=t.id;t.value=value??'';blurOnEnter(t);t.oninput=()=>set(t.value);t.onchange=()=>set(t.value,true);wrap.append(t);sync=v=>{if(document.activeElement!==t)t.value=v??'';};}
  else{const r=Ed.fields.get(o.type);if(r){try{const n=r(o,value,v=>set(v,true));if(n)wrap.append(n);}catch(e){wrap.append(el('span','hint','This control could not be drawn.'));}}
    else wrap.append(el('span','ed-missing',`This control needs a module that is not loaded (${o.type}).`));}
  return{node:wrap,sync};}
// Drag on a label to change its number, like Photoshop's scrubby sliders.
function scrub(label,input,o,live,done){label.classList.add('ed-scrub');label.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();const x0=e.clientX,v0=Number(input.value)||0,step=o.step||1;let moved=false;
  label.setPointerCapture(e.pointerId);const move=ev=>{const dx=ev.clientX-x0;if(Math.abs(dx)>2)moved=true;if(!moved)return;let v=v0+Math.round(dx/2)*step*(ev.shiftKey?10:1);v=U.clamp(v,o.min??-Infinity,o.max??Infinity);live(+v.toFixed(4));};
  const up=()=>{label.removeEventListener('pointermove',move);label.removeEventListener('pointerup',up);label.removeEventListener('pointercancel',up);if(moved)done();else input.focus();};
  label.addEventListener('pointermove',move);label.addEventListener('pointerup',up);label.addEventListener('pointercancel',up);});}
Ed.ui={field};

// ----- options bar -----------------------------------------------------------------------------------------------------
const optBox=byId('edOptions');let optSync={};
function renderOptions(){optBox.replaceChildren();optSync={};const t=Ed.tools.get(Ed.tool);if(!t)return;const head=el('span','ed-opt-tool');head.append(iconOf(t),el('span','',t.label));optBox.append(head);
  const vals=Ed.toolOptions(t.id);for(const o of t.options||[]){const f=field(o,vals[o.key],v=>Ed.setToolOptions(t.id,{[o.key]:v}));optSync[o.key]=f.sync;optBox.append(f.node);}}
Ed.on('options',({id,values})=>{if(id!==Ed.tool)return;for(const [k,f] of Object.entries(optSync))f(values[k]);Ed.view.redrawOverlay();});

// ----- tool strip, flyouts and color chips ------------------------------------------------------------------------------
const strip=byId('edTools'),grid=el('div','ed-tool-grid'),chips=el('div','ed-colors');strip.append(grid,chips);let flyout=null,holdTimer=0;
function renderStrip(){grid.replaceChildren();for(const s of Ed.toolSlots()){const t=Ed.tools.get(s.current),b=btn('ed-tool');b.dataset.tool=t.id;b.dataset.group=s.group;
    b.title=tipOf(t)+(s.tools.length>1?' · right-click or hold for more':'');b.setAttribute('aria-label',t.label);b.setAttribute('aria-pressed',String(Ed.tool===t.id));b.classList.toggle('active',Ed.tool===t.id);
    b.append(iconOf(t));if(s.tools.length>1)b.append(el('span','ed-tool-more'));
    b.onclick=()=>{if(!flyout)Ed.setTool(t.id);};
    b.ondblclick=()=>{if(t.id==='hand')Ed.view.fit();if(t.id==='zoom')Ed.view.actual();};
    b.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();if(s.tools.length>1)openFlyout(b,s);};
    b.onpointerdown=e=>{if(e.button!==0||s.tools.length<2)return;clearTimeout(holdTimer);holdTimer=setTimeout(()=>openFlyout(b,s),450);};
    b.onpointerup=b.onpointerleave=()=>clearTimeout(holdTimer);
    b.onkeydown=e=>{if(e.key==='ArrowRight'&&s.tools.length>1){e.preventDefault();openFlyout(b,s,true);}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const all=[...grid.children],i=all.indexOf(b);all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length].focus();}};
    grid.append(b);}layoutStrip();}
// One column of slots while they fit; a second column appears when the window is too short (like Photoshop's toolbar).
function layoutStrip(){const n=grid.children.length,h=grid.clientHeight;if(!n||!h)return;const rows=Math.max(1,Math.min(n,Math.floor((h+2)/34))),v=`repeat(${rows},32px)`;if(grid.style.gridTemplateRows!==v)grid.style.gridTemplateRows=v;}
new ResizeObserver(layoutStrip).observe(strip);
function openFlyout(b,s,focus){closeFlyout();clearTimeout(holdTimer);const f=el('div','ed-flyout');f.setAttribute('role','menu');f.setAttribute('aria-label',s.label+' tools');
  for(const t of s.tools){const i=btn('ed-flyout-item');i.setAttribute('role','menuitemradio');i.setAttribute('aria-checked',String(Ed.tool===t.id));i.dataset.tool=t.id;
    i.append(el('span','ed-menu-check',Ed.tool===t.id?'■':''),iconOf(t),el('span','ed-menu-label',t.label),el('span','ed-menu-key',t.key?t.key.toUpperCase():''));
    i.onclick=e=>{e.stopPropagation();closeFlyout();Ed.setTool(t.id);b.focus();};f.append(i);}
  f.onkeydown=e=>{const all=[...f.children],i=all.indexOf(document.activeElement);if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length].focus();}
    else if(e.key==='Escape'||e.key==='ArrowLeft'){e.preventDefault();e.stopPropagation();closeFlyout();b.focus();}};
  document.body.append(f);const r=b.getBoundingClientRect();place(f,r.right+4,r.top);flyout=f;if(focus)f.firstChild.focus();}
function closeFlyout(){if(flyout){flyout.remove();flyout=null;}}
const fgChip=btn('ed-chip ed-fg','', 'Foreground color · click to pick (X swaps, D resets)'),bgChip=btn('ed-chip ed-bg','', 'Background color · click to pick');
fgChip.setAttribute('aria-label','Foreground color');bgChip.setAttribute('aria-label','Background color');
const swapB=btn('ed-swap','', 'Swap colors (X)');swapB.append(Ed.icon(I.swap,{size:12}));const resetB=btn('ed-reset','', 'Default colors (D)');resetB.append(el('i'),el('i'));
const nativeFg=el('input'),nativeBg=el('input');for(const [n,id,label] of [[nativeFg,'paintColor','Foreground color'],[nativeBg,'paintBackground','Background color']]){n.type='color';n.id=id;n.className='ed-native-color';n.tabIndex=-1;n.setAttribute('aria-label',label);}
nativeFg.oninput=nativeFg.onchange=()=>Ed.setColor('fg',nativeFg.value);nativeBg.oninput=nativeBg.onchange=()=>Ed.setColor('bg',nativeBg.value);
const chipBox=el('div','ed-chipbox');chipBox.append(bgChip,fgChip,swapB,resetB);chips.append(chipBox,nativeFg,nativeBg);
fgChip.onclick=()=>Ed.pickColor('fg',fgChip);bgChip.onclick=()=>Ed.pickColor('bg',bgChip);swapB.onclick=()=>Ed.swapColors();resetB.onclick=()=>Ed.defaultColors();
function syncColors(){fgChip.style.backgroundColor=Ed.fg;bgChip.style.backgroundColor=Ed.bg;nativeFg.value=Ed.fg;nativeBg.value=Ed.bg;fgChip.title=`Foreground color ${Ed.fg} · click to pick (X swaps, D resets)`;bgChip.title=`Background color ${Ed.bg} · click to pick`;}
Ed.on('color',syncColors);Ed.on('pick-native',({which})=>{const i=which==='bg'?nativeBg:nativeFg;i.value=Ed[which];try{i.showPicker();}catch(e){i.click();}});

// ----- panel dock -------------------------------------------------------------------------------------------------------
const dock=byId('edDock'),tabs=el('div','ed-tabs'),panelBody=el('div','ed-panel-body');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Editor panels');dock.append(tabs,panelBody);
const boxes=new Map();let shown=U.store.get('ed-panel','layers');
function renderTabs(){tabs.replaceChildren();const list=Ed.panels.list();if(!list.some(p=>p.id===shown)&&list[0])shown=list[0].id;
  for(const p of list){const t=btn('ed-tab',p.title,p.title);t.setAttribute('role','tab');t.id='edTab-'+p.id;t.dataset.panel=p.id;t.setAttribute('aria-selected',String(p.id===shown));t.tabIndex=p.id===shown?0:-1;
    t.onclick=()=>showPanel(p.id);t.onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();const all=[...tabs.children],i=all.indexOf(t),n=all[(i+(e.key==='ArrowRight'?1:-1)+all.length)%all.length];showPanel(n.dataset.panel);n.focus();}};tabs.append(t);}
  for(const [id,b] of boxes)b.hidden=id!==shown;}
function box(id){if(!boxes.has(id)){const b=el('div','ed-panel');b.id='edPanel-'+id;b.dataset.panel=id;b.setAttribute('role','tabpanel');b.setAttribute('aria-labelledby','edTab-'+id);panelBody.append(b);boxes.set(id,b);}return boxes.get(id);}
function showPanel(id){if(!Ed.panels.get(id))return;shown=id;U.store.set('ed-panel',id);box(id);renderTabs();renderPanel(id);}
function renderPanel(id){const p=Ed.panels.get(id);if(!p||id!==shown)return;try{p.render(box(id));}catch(e){Ed.test.errors.push({where:'panel '+id,message:e.message});console.error('[editor] panel '+id+':',e);}}
let panelFrame=0;const refreshPanels=()=>{if(panelFrame)return;panelFrame=requestAnimationFrame(()=>{panelFrame=0;renderPanel(shown);});};
Ed.on('panels',()=>{renderTabs();refreshPanels();});Ed.on('panel-show',showPanel);Ed.on('panel-refresh',id=>{if(!id||id===shown)refreshPanels();});
for(const n of ['layers','active','history','document','open','selection'])Ed.on(n,refreshPanels);

// ----- Layers panel (basic; editor/layers.js may replace it) -------------------------------------------------------------
const thumbs=new Map();
function thumb(src,key,cache,cls,title){const size=34;let t=cache.get(key);if(!t){t=el('canvas',cls);t.width=t.height=size;cache.set(key,t);t.dataset.v='';}
  t.className=cls;t.title=title||'';const ver=src?src._v:'none';if(t.dataset.v!==String(ver)){t.dataset.v=String(ver);const g=t.getContext('2d');g.clearRect(0,0,size,size);
    for(let y=0;y<size;y+=6)for(let x=0;x<size;x+=6){g.fillStyle=((x+y)/6)%2?'#3b4a5a':'#2a3644';g.fillRect(x,y,6,6);}
    if(src&&src.canvas&&Ed.doc){const W=Ed.doc.width,H=Ed.doc.height,s=Math.min(size/W,size/H),w=Math.max(1,W*s),h=Math.max(1,H*s);g.imageSmoothingQuality='high';
      if(src.mask){g.fillStyle='#000';g.fillRect((size-w)/2,(size-h)/2,w,h);}g.drawImage(src.canvas,(size-w)/2,(size-h)/2,w,h);}}
  return t;}
const LayersPanel=(()=>{let root=null,list,blend,opacity,fillF,locks={},footer,live=null,editing=null;
  const act=()=>Ed.layers.active();
  function liveSet(key,v,end){const L=act();if(!L)return;if(!live)live=Ed.history.begin(key==='opacity'?'Opacity Change':'Fill Opacity Change');Ed.layers.set(L,{[key]:U.clamp(v/100,0,1)},{history:false});if(end){const t=live;live=null;t.commit();}}
  function build(c){root=c;c.classList.add('ed-layers');c.replaceChildren();
    const row1=el('div','ed-layers-row');blend=el('select','ed-blend');blend.name='blend';blend.setAttribute('aria-label','Blend mode');blend.title='Blend mode: how this layer mixes with the layers below';
    blend.onchange=()=>{const L=act();if(L)Ed.layers.set(L,{blend:blend.value});blend.blur();};
    opacity=field({key:'opacity',label:'Opacity',type:'range',min:0,max:100,step:1,unit:'%',title:'Layer opacity: how strongly the whole layer shows, effects included. Digit keys set it when the tool has no opacity.'},100,(v,end)=>liveSet('opacity',v,end));
    opacity.node.classList.add('ed-compact');row1.append(blend,opacity.node);
    const row2=el('div','ed-layers-row');const lockBox=el('div','ed-locks');lockBox.append(el('span','ed-field-label','Lock'));
    for(const [k,icon,title] of [['alpha',I.lockAlpha,'Lock transparent pixels: paint only where the layer already has pixels'],['pixels',I.lockPixels,'Lock image pixels: no painting or filters on this layer'],['position',I.lockMove,'Lock position: the layer cannot be moved'],['all',I.lockAll,'Lock all']]){
      const b=btn('ed-lock','',title);b.dataset.lock=k;b.setAttribute('aria-pressed','false');b.setAttribute('aria-label',title.split(':')[0]);b.append(Ed.icon(icon,{size:14}));
      b.onclick=()=>{const L=act();if(L)Ed.layers.set(L,{locks:{[k]:!L.locks[k]}});};locks[k]=b;lockBox.append(b);}
    fillF=field({key:'fill',label:'Fill',type:'range',min:0,max:100,step:1,unit:'%',title:'Fill: how strongly the layer’s own pixels show. Layer effects keep their strength.'},100,(v,end)=>liveSet('fill',v,end));
    fillF.node.classList.add('ed-compact');row2.append(lockBox,fillF.node);
    list=el('div','ed-layer-list');list.setAttribute('role','listbox');list.setAttribute('aria-label','Layers, top first');list.tabIndex=0;
    // Keyboard: Up and Down pick the layer above or below, F2 or Enter renames it.
    list.onkeydown=e=>{const d=Ed.doc;if(!d||e.target!==list)return;const i=d.activeIndex;
      if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();e.stopPropagation();Ed.layers.setActive(U.clamp(i+(e.key==='ArrowUp'?1:-1),0,d.layers.length-1));}
      else if(e.key==='F2'||e.key==='Enter'){e.preventDefault();e.stopPropagation();const r=rowsById.get(d.layers[i]?.id);if(r)rename(d.layers[i],r._ref.name);}};
    footer=el('div','ed-layers-foot');
    for(const [id,icon,title,fn] of [['new',I.plus,'New layer (Shift+Ctrl+N)',()=>Ed.menu.run('Layer/New Layer')],['duplicate',I.dup,'Duplicate layer (Ctrl+J)',()=>Ed.menu.run('Layer/Duplicate Layer')],
      ['mask',I.mask,'Add a layer mask (it reveals the selection when there is one)',()=>Ed.menu.run(Ed.selection?'Layer/Layer Mask/Reveal Selection':'Layer/Layer Mask/Reveal All')],['delete',I.trash,'Delete layer',()=>Ed.menu.run('Layer/Delete Layer')]]){
      const b=btn('ed-layer-action','',title);b.dataset.action=id;b.setAttribute('aria-label',title.replace(/ \(.*\)$/,''));b.append(Ed.icon(icon,{size:16}));b.onclick=fn;footer.append(b);}
    c.append(row1,row2,list,footer);}
  // Rows are kept per layer id and patched in place, so a refresh never swaps an element out from under the pointer.
  const rowsById=new Map(),layerOf=r=>Ed.layers.get(r.dataset.id);
  function makeRow(id){const r=el('div','ed-layer');r.dataset.id=id;r.id='edLayerRow-'+id;r.setAttribute('role','option');
    const eye=btn('ed-eye');eye.append(Ed.icon(I.eye,{size:15}));eye.onclick=e=>{e.stopPropagation();const L=layerOf(r);if(L)Ed.layers.set(L,{visible:!L.visible});};
    const clip=el('span','ed-clip-arrow','↳'),pics=el('span','ed-pics'),name=el('span','ed-layer-name'),badgeBox=el('span','ed-badges');
    name.ondblclick=e=>{e.stopPropagation();const L=layerOf(r);if(L)rename(L,name);};
    r.onpointerdown=e=>{const L=layerOf(r);if(L)dragStart(e,r,L);};r.append(eye,clip,pics,name,badgeBox);r._ref={eye,clip,pics,name,badgeBox};return r;}
  function patchRow(r,L,i){const active=i===Ed.doc.activeIndex,{eye,clip,pics,name,badgeBox}=r._ref;r.dataset.index=i;r.setAttribute('aria-selected',String(active));
    r.classList.toggle('active',active);r.classList.toggle('off',!L.visible);r.classList.toggle('clipped',L.clip);clip.hidden=!L.clip;
    eye.title=L.visible?'Hide this layer':'Show this layer';eye.setAttribute('aria-pressed',String(L.visible));eye.setAttribute('aria-label',(L.visible?'Hide ':'Show ')+L.name);
    const tgt=active?Ed.doc.target:'',want=[];
    if(L.kind==='adjustment'){let s=thumbs.get(L.id+'|adj');if(!s){s=el('span','ed-thumb ed-adjust-thumb');s.append(Ed.icon(I.adjust,{size:18}));thumbs.set(L.id+'|adj',s);}s.dataset.part='pixels';s.title=(Ed.adjustments.get(L.adjustment?.type)?.label||'Adjustment')+' layer';want.push(s);}
    else{const t=thumb({canvas:L.canvas,_v:L.version+'|'+Ed.doc.width},L.id,thumbs,'ed-thumb'+(tgt==='pixels'?' target':''),'Layer pixels · click to paint on them');t.dataset.part='pixels';want.push(t);}
    if(L.mask){const m=thumb({canvas:L.mask,mask:true,_v:L.version+'|m|'+L.maskEnabled},L.id+'|mask',thumbs,'ed-thumb ed-mask-thumb'+(tgt==='mask'?' target':'')+(L.maskEnabled?'':' disabled'),'Layer mask · click to paint on it (black hides, white shows)');m.dataset.part='mask';want.push(m);}
    if(want.length!==pics.children.length||want.some((x,k)=>pics.children[k]!==x))pics.replaceChildren(...want);
    if(!r.contains(editing)&&name.textContent!==L.name)name.textContent=L.name;name.title=L.name+' · double-click to rename';
    const bs=[];for(const fn of Ed.layerBadges()){try{const b=fn(L,r);if(b)bs.push(typeof b==='string'?el('span','ed-badge',b):b);}catch(e){}}
    if(L.locks.all||L.locks.pixels||L.locks.alpha||L.locks.position){const k=el('span','ed-badge ed-lock-badge');k.title='Locked: '+Object.entries(L.locks).filter(([,v])=>v).map(([k])=>({alpha:'transparency',pixels:'pixels',position:'position',all:'everything'})[k]).join(', ');k.append(Ed.icon(I.lockAll,{size:12}));bs.push(k);}
    badgeBox.replaceChildren(...bs);return r;}
  function rename(L,name){const i=el('input','ed-rename');i.type='text';i.value=L.name;i.setAttribute('aria-label','Layer name');editing=i;name.hidden=true;name.after(i);i.focus();i.select();let done=false;
    const end=ok=>{if(done)return;done=true;editing=null;const v=i.value.trim();i.remove();name.hidden=false;if(ok&&v&&v!==L.name)Ed.layers.set(L,{name:v.slice(0,255)});else update();};
    i.onkeydown=e=>{e.stopPropagation();if(e.key==='Enter')end(true);if(e.key==='Escape')end(false);};i.onblur=()=>end(true);i.onpointerdown=e=>e.stopPropagation();i.ondblclick=e=>e.stopPropagation();}
  // A click selects the layer (or its mask); dragging 5 px starts a reorder. The pointer is only captured once dragging, so a
  // double-click still reaches the name.
  function dragStart(e,r,L){if(e.button!==0||e.target.closest('button,input'))return;const id=e.pointerId,i=Ed.layers.index(L),n=Ed.layers.count(),y0=e.clientY,part=e.target.closest('[data-part]')?.dataset.part;let moving=false,line=null;
    const slot=y=>{const rows=[...list.querySelectorAll('.ed-layer')];let k=0;for(const x of rows){const b=x.getBoundingClientRect();if(y>b.top+b.height/2)k++;}return{k,rows};};
    const move=ev=>{if(ev.pointerId!==id||(!moving&&Math.abs(ev.clientY-y0)<5))return;if(!moving){moving=true;r.classList.add('dragging');line=el('div','ed-drop-line');list.append(line);try{r.setPointerCapture(id);}catch(x){}}
      const {k,rows}=slot(ev.clientY),ref=rows[Math.min(k,rows.length-1)],b=ref.getBoundingClientRect(),lb=list.getBoundingClientRect();line.style.top=((k<rows.length?b.top:b.bottom)-lb.top+list.scrollTop-1)+'px';};
    const up=ev=>{if(ev.pointerId!==id)return;window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);if(line)line.remove();r.classList.remove('dragging');
      if(moving&&ev.type==='pointerup'){const {k}=slot(ev.clientY),from=n-1-i,pos=k>from?k-1:k;Ed.layers.move(i,n-1-pos);}
      else if(!moving&&ev.type==='pointerup')Ed.layers.setActive(L,part==='mask'?'mask':'pixels');};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);}
  function update(){const d=Ed.doc,L=act();root.classList.toggle('empty',!d);
    const modes=Ed.blends.list(),sig=modes.map(b=>b.id+b.label).join();if(blend.dataset.sig!==sig){blend.dataset.sig=sig;blend.replaceChildren();let g=null;for(const b of modes){if(g&&g!==b.group)blend.append(el('hr'));g=b.group;const o=el('option','',b.label);o.value=b.id;blend.append(o);}}
    for(const x of [blend,...root.querySelectorAll('input,.ed-lock,.ed-layer-action')])x.disabled=!L;
    if(L){if(document.activeElement!==blend)blend.value=L.blend;opacity.sync(Math.round(L.opacity*100));fillF.sync(Math.round(L.fill*100));for(const [k,b] of Object.entries(locks)){b.setAttribute('aria-pressed',String(!!L.locks[k]));b.classList.toggle('on',!!L.locks[k]);}}
    const rows=[];if(d){for(let i=d.layers.length-1;i>=0;i--){const L=d.layers[i];let r=rowsById.get(L.id);if(!r){r=makeRow(L.id);rowsById.set(L.id,r);}rows.push(patchRow(r,L,i));}}
    else rows.push(el('p','hint','Open an image to see its layers.'));
    if(rows.length!==list.children.length||rows.some((r,k)=>list.children[k]!==r))list.replaceChildren(...rows);
    const cur=L&&rowsById.get(L.id);if(cur){list.setAttribute('aria-activedescendant',cur.id);if(document.activeElement===list){const a=cur.offsetTop,b=a+cur.offsetHeight;if(a<list.scrollTop)list.scrollTop=a;else if(b>list.scrollTop+list.clientHeight)list.scrollTop=b-list.clientHeight;}}
    const live=new Set(d?d.layers.map(L=>L.id):[]);for(const k of rowsById.keys())if(!live.has(k))rowsById.delete(k);
    for(const k of thumbs.keys())if(!live.has(k.split('|')[0]))thumbs.delete(k);}
  return{title:'Layers',order:10,render(c){if(root!==c)build(c);update();}};})();
Ed.registerPanel('layers',LayersPanel);
Ed.registerPanel('history',{title:'History',order:20,render(c){c.classList.add('ed-history');let list=c.querySelector('.ed-history-list');
  if(!list){list=el('div','ed-history-list');list.setAttribute('role','listbox');list.setAttribute('aria-label','History states');c.append(list,el('p','hint ed-history-hint','Click a state to go back to it. A new change after going back drops the later states.'));}
  list.replaceChildren();for(const s of Ed.history.list()){const b=btn('ed-history-item'+(s.current?' current':'')+(s.undone?' undone':''),s.label);b.setAttribute('role','option');b.setAttribute('aria-selected',String(s.current));b.dataset.index=s.index;b.onclick=()=>Ed.history.jump(s.index);list.append(b);}
  const cur=list.querySelector('.current');if(cur){const top=cur.offsetTop-list.offsetTop;if(top<list.scrollTop||top>list.scrollTop+list.clientHeight-cur.offsetHeight)list.scrollTop=top-list.clientHeight/2;}}});

// ----- dialogs ----------------------------------------------------------------------------------------------------------
Ed.dialog=(spec={})=>{let resolve;const done=new Promise(r=>{resolve=r;});const d=el('dialog','ed-dialog'),form=el('form');form.method='dialog';d.setAttribute('data-ed-dialog',spec.title||'');d.setAttribute('aria-label',spec.title||'Dialog');
  const fieldsList=spec.fields||[],params={},defaults={};for(const f of fieldsList)if(f.key&&f.type!=='button'){defaults[f.key]=f.default;params[f.key]=spec.values&&f.key in spec.values?spec.values[f.key]:f.default;}
  const head=el('h2','',spec.title||'');const body=el('div','ed-dialog-fields');let timer=0,closed=false;
  const previewBox=el('label','ed-field ed-checkbox ed-preview-toggle'),pv=el('input');pv.type='checkbox';pv.name='preview';pv.checked=spec.previewDefault!==false;previewBox.append(pv,el('span','','Preview'));
  const runPreview=async()=>{if(closed||!spec.preview)return;if(!pv.checked){Ed.preview.clear();return;}try{await spec.preview({...params});}catch(e){Ed.test.errors.push({where:'dialog preview',message:e.message});console.error('[editor] dialog preview:',e);}};
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(runPreview,spec.delay??70);};
  const draw=()=>{body.replaceChildren();for(const f of fieldsList)body.append(field(f,params[f.key],v=>{params[f.key]=v;schedule();}).node);};
  const actions=el('div','ed-dialog-actions'),reset=btn('quiet','Reset','Back to the starting values'),cancel=btn('','Cancel'),ok=el('button','primary',spec.okLabel||'OK');ok.type='submit';ok.value='ok';
  reset.onclick=()=>{Object.assign(params,defaults);draw();schedule();};cancel.onclick=()=>finish(false);
  actions.append(...(spec.preview?[previewBox]:[]),reset,cancel,ok);pv.onchange=runPreview;
  d.append(form);form.append(head,...(spec.text?[el('p','ed-dialog-text',spec.text)]:[]),body,actions);
  async function finish(accept){if(closed)return;closed=true;clearTimeout(timer);if(spec.preview)Ed.preview.clear();if(d.open)d.close();d.remove();
    if(accept){try{if(spec.ok)await spec.ok({...params});}catch(e){Ed.message(e.message,true);console.error('[editor] dialog ok:',e);}resolve({...params});}
    else{try{spec.cancel&&spec.cancel();}catch(e){}resolve(null);}}
  form.onsubmit=e=>{e.preventDefault();finish(true);};d.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
  draw();document.body.append(d);d.showModal();if(spec.preview)runPreview();done.element=d;return done;};

// ----- the view overlay: marching ants, pixel grid, brush outline, tool overlays ----------------------------------------
const octx=overlay.getContext('2d');let hover=null,ants={key:'',path:null},antsOffset=0,antsTimer=0,overlayFrame=0;
function antsPath(t,vw,vh){const o=Ed.select.outline();if(!o)return null;const key=Ed.select.version+'|'+t.x+'|'+t.y+'|'+t.scale+'|'+vw+'|'+vh;if(key===ants.key)return ants.path;
  const p=new Path2D(),s=t.scale,sx=x=>Math.round(t.x+x*s),sy=y=>Math.round(t.y+y*s);
  for(let i=0;i<o.h.length;i+=3){const y=sy(o.h[i]);if(y<-2||y>vh+2)continue;const a=Math.max(-2,sx(o.h[i+1])),b=Math.min(vw+2,sx(o.h[i+2])+1);if(b<a)continue;p.moveTo(a,y+.5);p.lineTo(b,y+.5);}
  for(let i=0;i<o.v.length;i+=3){const x=sx(o.v[i]);if(x<-2||x>vw+2)continue;const a=Math.max(-2,sy(o.v[i+1])),b=Math.min(vh+2,sy(o.v[i+2]));if(b<a)continue;p.moveTo(x+.5,a);p.lineTo(x+.5,b);}
  ants={key,path:p};return p;}
function drawOverlay(){overlayFrame=0;const vw=viewport.clientWidth,vh=viewport.clientHeight,dpr=Math.min(devicePixelRatio,2);
  if(overlay.width!==Math.round(vw*dpr)||overlay.height!==Math.round(vh*dpr)){overlay.width=Math.round(vw*dpr);overlay.height=Math.round(vh*dpr);}
  // Chrome (seen with its software renderer) can show an older frame again after presenting a canvas that was only cleared,
  // so an overlay that ends up with nothing drawn is hidden instead.
  const t=Ed.doc&&!view.hidden&&!Ed.view.blocked?Ed.view.transform:null,s=t?t.scale:1,tool=Ed.tools.get(Ed.tool);
  if(!t){overlay.hidden=true;return;}
  if(octx.reset)octx.reset();else octx.clearRect(0,0,overlay.width,overlay.height);octx.setTransform(dpr,0,0,dpr,0,0);
  const W=Ed.doc.width,H=Ed.doc.height;let drew=false;
  if(Ed.view.pixelGrid&&s>=8){drew=true;octx.beginPath();const x0=Math.max(0,Math.floor(-t.x/s)),x1=Math.min(W,Math.ceil((vw-t.x)/s)),y0=Math.max(0,Math.floor(-t.y/s)),y1=Math.min(H,Math.ceil((vh-t.y)/s));
    const top=Math.max(0,t.y),bottom=Math.min(vh,t.y+H*s),left=Math.max(0,t.x),right=Math.min(vw,t.x+W*s);
    for(let x=x0;x<=x1;x++){const X=Math.round(t.x+x*s)+.5;octx.moveTo(X,top);octx.lineTo(X,bottom);}for(let y=y0;y<=y1;y++){const Y=Math.round(t.y+y*s)+.5;octx.moveTo(left,Y);octx.lineTo(right,Y);}
    octx.strokeStyle='rgba(150,165,180,.28)';octx.lineWidth=1;octx.stroke();}
  const info={x:t.x,y:t.y,scale:s,width:vw,height:vh,toScreen:(x,y)=>({x:t.x+x*s,y:t.y+y*s}),toImage:(x,y)=>({x:(x-t.x)/s,y:(y-t.y)/s})};
  if(Ed.overlays)for(const o of Ed.overlays()){octx.save();try{if(o.draw(watch(octx,()=>{drew=true;}),info))drew=true;}catch(e){Ed.test.errors.push({where:'overlay '+o.id,message:e.message});}octx.restore();}
  const p=antsPath(t,vw,vh);if(p){drew=true;octx.lineWidth=1;octx.setLineDash([]);octx.strokeStyle='#fff';octx.stroke(p);octx.setLineDash([4,4]);octx.lineDashOffset=-antsOffset;octx.strokeStyle='#000';octx.stroke(p);octx.setLineDash([]);}
  if(tool&&tool.drawOverlay){octx.save();try{tool.drawOverlay(watch(octx,()=>{drew=true;}),info);}catch(e){Ed.test.errors.push({where:'tool overlay',message:e.message});}octx.restore();}
  if(hover&&tool&&tool.cursor==='brush'&&!(altHeld&&tool.altEyedropper)&&!spaceHeld){drew=true;const o=Ed.toolOptions(tool.id),r=(o.size||1)*s/2,x=hover.x,y=hover.y;
    if(r>=3){octx.beginPath();octx.arc(x,y,r,0,Math.PI*2);octx.lineWidth=2.5;octx.strokeStyle='rgba(0,0,0,.55)';octx.stroke();octx.lineWidth=1;octx.strokeStyle='rgba(255,255,255,.9)';octx.stroke();}
    else{octx.beginPath();octx.moveTo(x-5,y+.5);octx.lineTo(x+6,y+.5);octx.moveTo(x+.5,y-5);octx.lineTo(x+.5,y+6);octx.lineWidth=1;octx.strokeStyle='#fff';octx.stroke();}}
  overlay.hidden=!drew;}
// The context a tool's drawOverlay gets: the real one, noting whether anything was drawn.
const DRAWS=new Set(['fill','stroke','fillRect','strokeRect','drawImage','fillText','strokeText','putImageData','drawFocusIfNeeded']);
function watch(g,mark){return new Proxy(g,{get(t,k){const v=t[k];return typeof v==='function'?(...a)=>{if(DRAWS.has(k))mark();return v.apply(t,a);}:v;},set(t,k,v){t[k]=v;return true;}});}
const redrawOverlay=()=>{if(!overlayFrame)overlayFrame=requestAnimationFrame(drawOverlay);};
Ed.on('render',drawOverlay);Ed.on('overlay',redrawOverlay);Ed.on('tool',redrawOverlay);
Ed.on('selection',()=>{ants.key='';redrawOverlay();clearInterval(antsTimer);antsTimer=0;if(Ed.selection)antsTimer=setInterval(()=>{if(document.hidden)return;antsOffset=(antsOffset+1)%8;redrawOverlay();},130);});

// ----- pointer and keyboard ----------------------------------------------------------------------------------------------
let drag=null,spaceHeld=false,altHeld=false;
function point(e){const q=Ed.view.toImage(e.clientX,e.clientY),pen=e.pointerType==='pen'||e.pointerType==='touch';
  return{x:q.x,y:q.y,pressure:pen?e.pressure:(e.buttons?1:0),tiltX:e.tiltX||0,tiltY:e.tiltY||0,twist:e.twist||0,buttons:e.buttons,button:e.button,shiftKey:e.shiftKey,altKey:e.altKey,ctrlKey:e.ctrlKey||e.metaKey,
    pointerType:e.pointerType||'mouse',clientX:e.clientX,clientY:e.clientY,time:e.timeStamp};}
function cursor(){const t=Ed.tools.get(Ed.tool);let c='default';
  if(drag&&(drag.kind==='pan'||drag.tool&&drag.tool.id==='hand'))c='grabbing';else if(spaceHeld||Ed.tool==='hand')c='grab';else if(t){if(altHeld&&t.altEyedropper)c=EYEDROPPER;else if(t.cursor==='brush'){const r=(Ed.toolOptions(t.id).size||1)*Ed.view.scale/2;c=r>=3?'none':'crosshair';}
    else if(t.cursor==='eyedropper')c=EYEDROPPER;else if(t.cursor==='zoom-in'||t.cursor==='zoom')c=(altHeld!==(Ed.toolOptions(t.id).direction==='out'))?'zoom-out':'zoom-in';else c=t.cursor;}
  if(Ed.view.blocked&&t&&t.edits&&c!=='grab'&&c!=='grabbing')c='not-allowed';if(view.style.cursor!==c)view.style.cursor=c;}
function startPan(e){drag={kind:'pan',id:e.pointerId,x:e.clientX,y:e.clientY,v:Ed.view.getState()};cursor();}
function movePan(e){const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.hypot(dx,dy)>4)dragMoved=true;Ed.view.setState({px:drag.v.px+dx,py:drag.v.py+dy});}
function sampleAt(p,which){const c=Ed.sample(p.x,p.y,{size:+(Ed.toolOptions('eyedropper').sample||1),from:Ed.toolOptions('eyedropper').from==='layer'?'layer':'composite'});if(c)Ed.setColor(which,c);}
view.addEventListener('pointerdown',e=>{if(!Ed.doc||view.hidden)return;byId('contextMenu').hidden=true;dragMoved=false;closeMenus();closeFlyout();
  const a=document.activeElement;if(a&&a!==document.body&&!a.closest('dialog'))a.blur();
  if(drag)return;const t=Ed.tools.get(Ed.tool);
  if(e.button===1||e.button===2||(e.button===0&&spaceHeld)){if(e.button===1)e.preventDefault();startPan(e);view.setPointerCapture(e.pointerId);return;}
  if(e.button!==0||!t)return;const p=point(e);
  if(e.altKey&&t.altEyedropper){drag={kind:'sample',id:e.pointerId};view.setPointerCapture(e.pointerId);sampleAt(p,'fg');return;}
  if(t.edits){const why=Ed.editable();if(why){Ed.message(why);return;}}
  drag={kind:'tool',id:e.pointerId,tool:t};view.setPointerCapture(e.pointerId);const errs=Ed.test.errors.length;
  const r=Ed.callTool('onDown',p,e);if(r===false||Ed.test.errors.length>errs){drag=null;try{view.releasePointerCapture(e.pointerId);}catch(x){}}});
view.addEventListener('pointermove',e=>{hover={x:e.clientX-viewport.getBoundingClientRect().left,y:e.clientY-viewport.getBoundingClientRect().top};
  if(drag&&drag.id===e.pointerId){if(drag.kind==='pan')movePan(e);else if(drag.kind==='sample')sampleAt(point(e),'fg');
    else{const evs=typeof e.getCoalescedEvents==='function'?e.getCoalescedEvents():[];for(const ce of evs.length?evs:[e])Ed.callTool('onMove',point(ce),ce);}}
  else if(!drag)Ed.callTool('onHover',point(e),e);
  cursor();redrawOverlay();});
function endDrag(e){if(!drag||drag.id!==e.pointerId)return;const d=drag;drag=null;if(d.kind==='tool'&&Ed.tools.get(Ed.tool)===d.tool)Ed.callTool('onUp',point(e),e);try{view.releasePointerCapture(e.pointerId);}catch(x){}cursor();redrawOverlay();}
view.addEventListener('pointerup',endDrag);view.addEventListener('pointercancel',endDrag);view.addEventListener('lostpointercapture',endDrag);
view.addEventListener('pointerleave',()=>{hover=null;redrawOverlay();});
view.addEventListener('wheel',e=>{if(!Ed.doc||view.hidden)return;e.preventDefault();const k=e.deltaMode===1?33:e.deltaMode===2?400:1;Ed.view.setZoom(Ed.view.scale*Math.exp(-e.deltaY*k*.001),e.clientX,e.clientY);},{passive:false});
let lastDigit=null;
function digit(d){const now=performance.now();let v=d===0?100:d*10;if(lastDigit&&now-lastDigit.t<700){v=lastDigit.d*10+d||100;lastDigit=null;}else lastDigit={d,t:now};
  const t=Ed.tools.get(Ed.tool);if(t&&(t.options||[]).some(o=>o.key==='opacity')){const o=t.options.find(o=>o.key==='opacity');Ed.setToolOptions(t.id,{opacity:U.clamp(v,o.min??0,o.max??100)});}
  else if(Ed.doc&&Ed.layers.active())Ed.layers.set(null,{opacity:v/100});}
function bracket(up,shift){const t=Ed.tools.get(Ed.tool);if(!t)return;const v=Ed.toolOptions(t.id),opts=t.options||[];
  if(shift){const h=opts.find(o=>o.key==='hardness');if(h)Ed.setToolOptions(t.id,{hardness:U.clamp((v.hardness??100)+(up?25:-25),h.min??0,h.max??100)});return;}
  const o=opts.find(o=>o.key==='size');if(!o)return;const s=Number(v.size)||1,next=up?SIZES.find(x=>x>s):[...SIZES].reverse().find(x=>x<s);
  Ed.setToolOptions(t.id,{size:U.clamp(next??(up?s+100:1),o.min??1,o.max??5000)});}
// [ and ] walk this ladder, as Photoshop does: single pixels while small, bigger steps for big brushes.
const SIZES=[...Array.from({length:10},(_,i)=>i+1),...Array.from({length:18},(_,i)=>15+i*5),...Array.from({length:8},(_,i)=>125+i*25),...Array.from({length:14},(_,i)=>350+i*50),1200,1500,2000,2500,3000,4000,5000];
function toolKey(k,shift){const cands=Ed.tools.list().filter(t=>t.key===k);if(!cands.length)return false;const cur=Ed.tools.get(Ed.tool);
  if(cur&&cur.key===k){if(shift){const same=cands.filter(t=>t.group===cur.group),i=same.indexOf(cur);Ed.setTool(same[(i+1)%same.length].id);}return true;}
  for(const s of Ed.toolSlots()){const inGroup=cands.filter(t=>t.group===s.group);if(inGroup.length){Ed.setTool((inGroup.find(t=>t.id===s.current)||inGroup[0]).id);return true;}}return false;}
document.addEventListener('keydown',e=>{
  if(e.key==='Alt'){altHeld=true;cursor();redrawOverlay();if(!typing(e))e.preventDefault();}
  if(typing(e)||modalOpen()||(typeof S!=='undefined'&&S.busy))return;
  if(e.key==='Escape'&&(menuOpen||flyout)){closeMenus();closeFlyout();return;}
  if(menuOpen&&e.target.closest&&e.target.closest('.ed-menu'))return;
  if(Ed.callTool('onKey',e)===true){e.preventDefault();return;}
  if(e.key==='Escape'){if(drag&&drag.kind==='tool'){const d=drag;drag=null;try{view.releasePointerCapture(d.id);}catch(x){}}Ed.callTool('onCancel');if(Ed.preview.active)Ed.preview.clear();return;}
  const path=Ed.menu.forEvent(e);if(path){e.preventDefault();if(!drag)Ed.menu.run(path);return;}
  if(e.ctrlKey||e.metaKey||e.altKey)return;
  // Space is the Hand, also while a button has focus: over the picture that button loses focus instead of being pressed
  // again (e.g. "New layer"); elsewhere it keeps Space for keyboard use, and a press on the picture still pans (and blurs it).
  if(e.key===' '){const b=e.target.closest&&e.target.closest('button,a,summary');if(!b||hover||drag){e.preventDefault();if(b)b.blur();}if(!spaceHeld){spaceHeld=true;cursor();redrawOverlay();}return;}
  if(drag)return;
  if(e.key==='Enter'){if(e.target.closest&&e.target.closest('button,a,summary'))return;Ed.callTool('onCommit');return;}
  if(e.code==='BracketLeft'||e.code==='BracketRight'){e.preventDefault();bracket(e.code==='BracketRight',e.shiftKey);return;}
  if(/^Digit\d$/.test(e.code)&&!e.shiftKey){e.preventDefault();digit(+e.code.slice(5));return;}
  const k=(e.key||'').toLowerCase();
  if(k==='x'&&!e.shiftKey){Ed.swapColors();return;}if(k==='d'&&!e.shiftKey){Ed.defaultColors();return;}
  if(/^[a-z]$/.test(k)&&toolKey(k,e.shiftKey))e.preventDefault();});
document.addEventListener('keyup',e=>{if(e.key===' '&&spaceHeld){spaceHeld=false;cursor();redrawOverlay();}if(e.key==='Alt'){altHeld=false;cursor();redrawOverlay();}});
window.addEventListener('blur',()=>{spaceHeld=altHeld=false;cursor();});

// ----- the basic tools --------------------------------------------------------------------------------------------------
Ed.registerTool('hand',{label:'Hand',group:'hand',key:'h',cursor:'grab',icon:()=>Ed.icon(I.hand),
  options:[{key:'fit',label:'Fit on screen',type:'button',action:()=>Ed.view.fit()},{key:'actual',label:'100%',type:'button',action:()=>Ed.view.actual()}],
  onDown(p){this.from={x:p.clientX,y:p.clientY,v:Ed.view.getState()};},onMove(p){const f=this.from;if(f)Ed.view.setState({px:f.v.px+p.clientX-f.x,py:f.v.py+p.clientY-f.y});},onUp(){this.from=null;}});
Ed.registerTool('zoom',{label:'Zoom',group:'zoom',key:'z',cursor:'zoom-in',icon:()=>Ed.icon(I.zoom),
  options:[{key:'direction',label:'Click to',type:'segmented',options:[{value:'in',label:'Zoom in'},{value:'out',label:'Zoom out'}],default:'in',title:'Alt-click does the opposite'},
    {key:'actual',label:'100%',type:'button',action:()=>Ed.view.actual()},{key:'fit',label:'Fit on screen',type:'button',action:()=>Ed.view.fit()}],
  onDown(p,e){this.box={x0:p.clientX,y0:p.clientY,x1:p.clientX,y1:p.clientY};},onMove(p){if(this.box){this.box.x1=p.clientX;this.box.y1=p.clientY;Ed.view.redrawOverlay();}},
  onUp(p,e){const b=this.box;this.box=null;if(!b)return;const out=(Ed.toolOptions('zoom').direction==='out')!==!!e.altKey;
    if(!out&&Math.abs(b.x1-b.x0)>6&&Math.abs(b.y1-b.y0)>6){const a=Ed.view.toImage(Math.min(b.x0,b.x1),Math.min(b.y0,b.y1)),c=Ed.view.toImage(Math.max(b.x0,b.x1),Math.max(b.y0,b.y1)),t=Ed.view.transform;
      const z=Math.min(t.width/(c.x-a.x),t.height/(c.y-a.y)),mid={x:(a.x+c.x)/2,y:(a.y+c.y)/2};Ed.view.setZoom(z);const q=Ed.view.toClient(mid.x,mid.y);Ed.view.panBy(t.left+t.width/2-q.x,t.top+t.height/2-q.y);}
    else if(out)Ed.view.zoomOut(p.clientX,p.clientY);else Ed.view.zoomIn(p.clientX,p.clientY);Ed.view.redrawOverlay();},
  onCancel(){this.box=null;Ed.view.redrawOverlay();},
  drawOverlay(ctx){const b=this.box;if(!b)return;const r=viewport.getBoundingClientRect();ctx.setLineDash([4,3]);ctx.strokeStyle='#fff';ctx.lineWidth=1;
    ctx.strokeRect(Math.round(Math.min(b.x0,b.x1)-r.left)+.5,Math.round(Math.min(b.y0,b.y1)-r.top)+.5,Math.round(Math.abs(b.x1-b.x0)),Math.round(Math.abs(b.y1-b.y0)));ctx.setLineDash([]);}});
Ed.registerTool('eyedropper',{label:'Eyedropper',group:'eyedropper',key:'i',cursor:'eyedropper',icon:()=>Ed.icon(I.eyedropper),
  options:[{key:'sample',label:'Sample size',type:'select',options:[{value:1,label:'Point sample'},{value:3,label:'3 by 3 average'},{value:5,label:'5 by 5 average'}],default:1},
    {key:'from',label:'Sample',type:'select',options:[{value:'composite',label:'All layers'},{value:'layer',label:'Current layer'}],default:'composite'}],
  onDown(p,e){this.which=e.altKey?'bg':'fg';sampleAt(p,this.which);},onMove(p){sampleAt(p,this.which);},onUp(){}});
function softDab(s,w){const D=Math.max(2,Math.ceil(s.o.size)),key=D+'|'+s.o.hardness+'|'+s.color;if(s.dab&&s.dabKey===key)return s.dab;
  const c=U.canvas(D,D),g=c.getContext('2d'),[r,gg,b]=U.rgbOf(s.color),gr=g.createRadialGradient(D/2,D/2,D/2*U.clamp(s.o.hardness/100,0,.99),D/2,D/2,D/2);
  gr.addColorStop(0,`rgba(${r},${gg},${b},1)`);gr.addColorStop(1,`rgba(${r},${gg},${b},0)`);g.fillStyle=gr;g.fillRect(0,0,D,D);s.dab=c;s.dabKey=key;return c;}
function paint(s,p,prev){const g=s.g,o=s.o;let w=Number(o.size)||1;if(o.pressure&&p.pointerType==='pen')w=Math.max(1,w*p.pressure);const r=w/2;
  if(s.hard){g.save();g.fillStyle=g.strokeStyle=s.color;g.lineCap=g.lineJoin='round';g.lineWidth=w;g.beginPath();if(prev){g.moveTo(prev.x,prev.y);g.lineTo(p.x,p.y);g.stroke();}else{g.arc(p.x,p.y,r,0,Math.PI*2);g.fill();}g.restore();}
  else{const dab=softDab(s,w),step=Math.max(.5,w*.12);if(!prev){g.drawImage(dab,p.x-r,p.y-r,w,w);s.carry=step;}
    else{const dx=p.x-prev.x,dy=p.y-prev.y,len=Math.hypot(dx,dy);let t=s.carry??step;while(t<=len){g.drawImage(dab,prev.x+dx*t/len-r,prev.y+dy*t/len-r,w,w);t+=step;}s.carry=t-len;}}
  const a=prev||p,R={x:Math.min(a.x,p.x)-r-2,y:Math.min(a.y,p.y)-r-2,w:Math.abs(p.x-a.x)+w+4,h:Math.abs(p.y-a.y)+w+4};s.bounds=U.union(s.bounds,R);Ed.preview.update(R);}
function strokeTool(kind){return{
  onDown(p){const L=Ed.layers.active(),target=Ed.doc.target,o={...Ed.toolOptions(this.id)};
    let color=kind==='brush'?Ed.fg:'#ffffff',mode=kind==='brush'?'source-over':'destination-out';
    if(kind==='eraser'&&target==='pixels'&&L.locks.alpha){color=Ed.bg;mode='source-over';}   // as in Photoshop: locked transparency erases to the background color
    const buf=Ed.strokeBuffer();this.s={buf,g:buf.getContext('2d'),color,o,last:p,bounds:null,hard:(o.hardness==null?100:Number(o.hardness))>=99.5};
    Ed.preview.stroke({canvas:buf,opacity:U.clamp((Number(o.opacity)||100)/100,0,1),mode,color:kind==='brush'?Ed.fg:Ed.bg});paint(this.s,p,null);},
  onMove(p){const s=this.s;if(!s)return;paint(s,p,s.last);s.last=p;},
  onUp(){const s=this.s;this.s=null;if(!s)return;Ed.commitStroke(kind==='brush'?'Brush Tool':'Eraser Tool',{rect:s.bounds,op:kind==='brush'?{tool:'brush',color:Ed.fg,size:Number(s.o.size)}:{tool:'eraser',size:Number(s.o.size)}});},
  onCancel(){if(this.s){this.s=null;Ed.preview.clear();}},
  onDeactivate(){if(this.s){this.s=null;Ed.preview.clear();}}};}
const strokeOptions=ids=>[{key:'size',label:'Size',type:'range',min:1,max:1000,step:1,unit:'px',default:12,id:ids&&'brushSize',title:'Brush size in image pixels ([ and ] change it)'},
  {key:'hardness',label:'Hardness',type:'range',min:0,max:100,step:1,unit:'%',default:100,title:'Hard or soft edge (Shift+[ and Shift+] change it)'},
  {key:'opacity',label:'Opacity',type:'range',min:1,max:100,step:1,unit:'%',default:100,id:ids&&'brushOpacity',title:'How strongly one stroke covers (the digit keys set it: 5 = 50%)'},
  {key:'pressure',label:'Pen pressure',type:'checkbox',default:false,title:'With a drawing tablet, pressing harder paints a bigger line'}];
Ed.registerTool('brush',{label:'Brush',group:'brush',key:'b',cursor:'brush',edits:true,altEyedropper:true,icon:()=>Ed.icon(I.brush),options:strokeOptions(true),...strokeTool('brush')});
Ed.registerTool('eraser',{label:'Eraser',group:'eraser',key:'e',cursor:'brush',edits:true,altEyedropper:true,icon:()=>Ed.icon(I.eraser),options:strokeOptions(false),...strokeTool('eraser')});

// ----- the basic menus ---------------------------------------------------------------------------------------------------
const hasDoc=()=>!!Ed.doc,hasSel=()=>!!Ed.selection,layer=()=>Ed.layers.active();
const m=(path,def)=>Ed.registerMenu(path,def);
m('File/Import Image as Layer...',{order:10,enabled:hasDoc,action:p=>{if(p&&p.file)return importImage(p.file);byId('importFile').click();}});
m('File/Save Edit',{order:20,shortcut:'Ctrl+S',enabled:()=>!!S.asset?.editable,action:()=>run(save)()});
m('File/Export PNG',{order:30,enabled:hasDoc,action:()=>exportPNG()});
m('File/Export Recipe',{order:40,enabled:()=>!!S.asset?.editable,action:()=>byId('recipeButton').click()});
m('File/Revert to Original',{order:50,separatorBefore:true,enabled:()=>hasDoc()&&!!S.original,action:()=>revertOriginal()});
m('Edit/Undo',{order:10,shortcut:'Ctrl+Z',enabled:()=>Ed.history.canUndo(),label:()=>Ed.history.canUndo()?'Undo '+Ed.history.label(Ed.history.index):'Undo',action:()=>Ed.history.undo()});
m('Edit/Redo',{order:20,shortcut:['Ctrl+Shift+Z','Ctrl+Y'],enabled:()=>Ed.history.canRedo(),label:()=>Ed.history.canRedo()?'Redo '+Ed.history.label(Ed.history.index+1):'Redo',action:()=>Ed.history.redo()});
m('Edit/Clear',{order:50,separatorBefore:true,shortcut:['Delete','Backspace'],enabled:hasDoc,action:()=>clearSelection()});
m('Image/Color & Surface...',{order:10,enabled:()=>hasDoc()&&!!S.asset?.editable,action:()=>{const d=byId('applyAdjust')?.closest('details');if(d){d.open=true;d.scrollIntoView({block:'nearest',behavior:'smooth'});byId('brightness')?.focus();}}});
m('Layer/New Layer',{order:10,shortcut:'Shift+Ctrl+N',enabled:hasDoc,action:p=>{Ed.layers.add({name:p&&p.name});}});
m('Layer/Duplicate Layer',{order:20,shortcut:'Ctrl+J',enabled:()=>!!layer(),action:()=>{Ed.layers.duplicate();}});
m('Layer/Delete Layer',{order:30,enabled:()=>hasDoc()&&Ed.layers.count()>1,action:()=>{Ed.layers.remove();}});
m('Layer/Layer Mask/Reveal All',{order:40,separatorBefore:true,enabled:()=>!!layer()&&!layer().mask,action:()=>Ed.layers.addMask(null,'reveal')});
m('Layer/Layer Mask/Hide All',{order:41,enabled:()=>!!layer()&&!layer().mask,action:()=>Ed.layers.addMask(null,'hide')});
m('Layer/Layer Mask/Reveal Selection',{order:42,enabled:()=>!!layer()&&!layer().mask&&hasSel(),action:()=>Ed.layers.addMask(null,'selection')});
m('Layer/Layer Mask/Hide Selection',{order:43,enabled:()=>!!layer()&&!layer().mask&&hasSel(),action:()=>Ed.layers.addMask(null,'hide-selection')});
m('Layer/Layer Mask/Disable',{order:44,separatorBefore:true,enabled:()=>!!layer()?.mask,checked:()=>!!layer()?.mask&&!layer().maskEnabled,action:()=>Ed.layers.set(null,{maskEnabled:!layer().maskEnabled})});
m('Layer/Layer Mask/Apply',{order:45,enabled:()=>!!layer()?.mask&&layer().kind==='pixel',action:()=>Ed.layers.removeMask(null,true)});
m('Layer/Layer Mask/Delete',{order:46,enabled:()=>!!layer()?.mask,action:()=>Ed.layers.removeMask(null,false)});
m('Layer/Create Clipping Mask',{order:50,separatorBefore:true,shortcut:'Alt+Ctrl+G',enabled:()=>hasDoc()&&Ed.doc.activeIndex>0,checked:()=>!!layer()?.clip,action:()=>Ed.layers.set(null,{clip:!layer().clip})});
m('Select/All',{order:10,shortcut:'Ctrl+A',enabled:hasDoc,action:()=>Ed.select.all()});
m('Select/Deselect',{order:20,shortcut:'Ctrl+D',enabled:hasSel,action:()=>Ed.select.none()});
m('Select/Reselect',{order:30,shortcut:'Shift+Ctrl+D',enabled:()=>hasDoc()&&!hasSel(),action:()=>{if(!Ed.select.reselect())Ed.message('There is no earlier selection to bring back.');}});
m('Select/Inverse',{order:40,shortcut:'Shift+Ctrl+I',enabled:hasSel,action:()=>Ed.select.invert()});
m('View/Zoom In',{order:10,shortcut:['Ctrl++','Ctrl+='],enabled:hasDoc,action:()=>Ed.view.zoomIn()});
m('View/Zoom Out',{order:20,shortcut:'Ctrl+-',enabled:hasDoc,action:()=>Ed.view.zoomOut()});
m('View/Fit on Screen',{order:30,shortcut:'Ctrl+0',enabled:hasDoc,action:()=>Ed.view.fit()});
m('View/100%',{order:40,shortcut:'Ctrl+1',enabled:hasDoc,action:()=>Ed.view.actual()});
m('View/Pixel Grid',{order:50,separatorBefore:true,checked:()=>Ed.view.pixelGrid,action:()=>{Ed.view.pixelGrid=!Ed.view.pixelGrid;}});
m('View/Crisp Pixels',{order:51,checked:()=>byId('pixelated').checked,action:()=>{byId('pixelated').checked=!byId('pixelated').checked;draw();}});
m('View/Show Original',{order:52,enabled:hasDoc,checked:()=>byId('compare').checked,action:()=>{byId('compare').checked=!byId('compare').checked;byId('compare').dispatchEvent(new Event('change'));}});
for(const [i,[mode,label]] of [['texture','Texture'],['tile','Tile'],['cell','Cell'],['button','Button'],['atlas','Atlas']].entries())
  m('View/Preview As/'+label,{order:60+i,separatorBefore:i===0,enabled:hasDoc,checked:()=>S.mode===mode,action:()=>byId('viewModes').querySelector(`[data-view="${mode}"]`).click()});
m('View/Focus Editor',{order:80,separatorBefore:true,shortcut:'F',checked:()=>document.body.classList.contains('ed-focus-mode'),action:()=>setFocus(!document.body.classList.contains('ed-focus-mode'))});
async function clearSelection(){if(!Ed.selection){Ed.message('Select an area first (Ctrl+A selects everything).');return;}
  await Ed.applyToTarget('Clear',(g,i)=>{const r=i.rect;if(i.target==='pixels'&&i.layer.locks.alpha){g.save();g.globalCompositeOperation='source-atop';g.fillStyle=Ed.bg;g.fillRect(r.x,r.y,r.w,r.h);g.restore();}else g.clearRect(r.x,r.y,r.w,r.h);},{op:{tool:'clear'}});}

// ----- focus mode, the mod tray and the undo buttons ---------------------------------------------------------------------
const focusB=byId('focusButton'),tray=byId('modTray'),trayB=byId('trayToggle');
function setFocus(on){document.body.classList.toggle('ed-focus-mode',!!on);focusB.setAttribute('aria-pressed',String(!!on));focusB.title=on?'Show the library and mod tray again (F)':'Focus editor: hide the library and mod tray (F)';U.store.set('ed-focus',!!on);closeMenus();}
focusB.onclick=()=>setFocus(!document.body.classList.contains('ed-focus-mode'));
function setTray(open){tray.classList.toggle('collapsed',!open);trayB.setAttribute('aria-expanded',String(!!open));trayB.title=open?'Fold the mod tray to one line':'Show the whole mod tray';U.store.set('ed-tray',!!open);}
trayB.onclick=()=>setTray(tray.classList.contains('collapsed'));
const undoB=byId('undoDraft'),redoB=byId('redoDraft');
function syncHistory(){undoB.disabled=!Ed.history.canUndo();redoB.disabled=!Ed.history.canRedo();undoB.title=Ed.history.canUndo()?`Undo ${Ed.history.label(Ed.history.index)} (Ctrl+Z)`:'Undo (Ctrl+Z)';redoB.title=Ed.history.canRedo()?`Redo ${Ed.history.label(Ed.history.index+1)} (Ctrl+Shift+Z)`:'Redo (Ctrl+Shift+Z)';}
Ed.on('history',syncHistory);

// ----- start -----------------------------------------------------------------------------------------------------------
let uiFrame=0;const later=()=>{if(!uiFrame)uiFrame=requestAnimationFrame(()=>{uiFrame=0;renderMenubar();renderStrip();renderOptions();cursor();});};
Ed.on('menus',()=>{if(!menuOpen)later();});Ed.on('tools',()=>{if(Ed.tool&&!Ed.tools.get(Ed.tool))Ed.setTool('hand');later();});
Ed.on('tool',()=>{renderStrip();renderOptions();cursor();redrawOverlay();});Ed.on('view',()=>{cursor();redrawOverlay();});
Ed.on('document',d=>{if(d&&(d.reason==='open'||d.reason==='restore'))thumbs.clear();});Ed.on('open',()=>{thumbs.clear();cursor();});
renderMenubar();renderTabs();showPanel(shown);syncColors();syncHistory();setFocus(U.store.get('ed-focus',false));setTray(U.store.get('ed-tray',true));
document.addEventListener('DOMContentLoaded',()=>{if(!Ed.tool||!Ed.tools.get(Ed.tool))Ed.setTool('hand');renderStrip();renderOptions();renderMenubar();renderTabs();cursor();Ed._ready();});
})();
