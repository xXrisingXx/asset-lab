'use strict';
// Presets: ready-made backgrounds, viruses and food, in a window like Game preview. Pick a group and a category, click a
// picture to see it big (a background as the tiled floor it becomes, a virus or food on the game's light and dark floor),
// then Use puts it in Theme AI's chat to recolor and put on the game. The same big view shows any picture card's picture.
// Uses app.js globals: $, api, run, message. Talks to Theme AI through window.LabThemeChat.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(text,cls,title)=>{const b=el('button',cls||'',text);b.type='button';if(title)b.title=title;return b;};
// A preset's picture carries the presets' version: the browser keeps it, so switching categories shows it at once.
const url=(id,color,small)=>`/api/picture?id=${encodeURIComponent(id)}${color?'&color='+encodeURIComponent(color):''}${small?'&small=1':''}${String(id).startsWith('preset:')&&P.version?'&v='+encodeURIComponent(P.version):''}`;
const GROUPS=['Backgrounds','Viruses','Food','Menu buttons','In-game buttons','Title'];
const P={list:null,group:'Backgrounds',category:'',scroll:0,picks:{}};

// ----- the big view (also for a picture card's picture) -----------------------------------------------------------
/* A background is shown as the tiled floor it becomes; anything else on the game's light and dark floor. */
function stage(src,kind,name){
  const box=el('div','pv-stage'+(kind==='background'?' tiled':''));
  if(kind==='background'){box.setAttribute('role','img');box.setAttribute('aria-label',name||'');box.show=s=>{box.style.backgroundImage=`url("${s}")`;};}
  else{const ims=[];for(const mode of kind==='menu'?['menu']:['light','dark']){/* menu buttons: on the menu's own color */const pane=el('div','pv-floor '+mode);const im=el('img');im.alt=name||'';ims.push(im);pane.append(im);box.append(pane);}
    box.show=s=>{for(const im of ims)im.src=s;};}
  box.show(src);return box;}
const viewer=el('dialog','pv-dialog');viewer.id='pictureViewer';document.body.append(viewer);
let closing=null;   // gives borrowed controls back when the big view closes
const giveBack=()=>{const f=closing;closing=null;f?.();};
viewer.addEventListener('close',giveBack);viewer.addEventListener('cancel',giveBack);   // Escape, and any other way it closes
const shut=()=>{giveBack();viewer.close();};   // Back and ✕ give the controls back at once, before the close event
/* A picture big, with Back. o: {src, kind, name, actions: [buttons], controls: an element shown beside it (a picture
   card's own color and placement controls: borrowed while open, given back on close), onclose}. Returns {show(src)}
   to follow changes made with those controls. */
function view(o){
  closing?.();closing=null;
  viewer.replaceChildren();const head=el('div','pv-head');const x=btn('✕','quiet','Close');x.onclick=shut;head.append(el('h2','',o.name||'Picture'),x);
  const back=btn('Back','', 'Back');back.onclick=shut;const bar=el('div','pv-actions');bar.append(back,...(o.actions||[]));
  const pic=stage(o.src,o.kind,o.name);if(o.exportName)pic.dataset.exportName=o.exportName;
  if(o.controls){const side=el('div','pv-controls');const home=o.controls.parentNode,next=o.controls.nextSibling;side.append(o.controls);
    const body=el('div','pv-body');body.append(pic,side);viewer.append(head,body,bar);
    closing=()=>{if(home)home.insertBefore(o.controls,next);o.onclose?.();};}
  else viewer.append(head,pic,bar);
  if(!viewer.open)viewer.showModal();
  return {show:s=>pic.show(s),close:shut};}
window.LabViewer={open:view,close:shut};

// ----- the Presets window -----------------------------------------------------------------------------------------
const dlg=el('dialog','pv-dialog presets-dialog');dlg.id='presetsDialog';dlg.setAttribute('aria-label','Presets');document.body.append(dlg);
// Clean up presets hide clutter in one step (no color to pick): straight into the build.
async function hideNow(p){const r=await api('/api/picture-apply',{id:p.id,key:'set:cleanup'});message(`${p.name}: hidden in your build (${r.count} picture${r.count===1?'':'s'} made see-through). Build to Mitosis to see it.`);}
function use(p){dlg.close();if(p.kind==='hide')return run(()=>hideNow(p))();window.LabThemeChat?.ask(`Use the ${p.name} preset`,{picture:p.id});}
function whole(p){dlg.close();window.LabThemeChat?.ask(`Make a whole theme from ${p.name}`,{theme_from:p.id});}
const themeable=p=>p.kind==='background'||p.kind==='virus';
const sets=p=>p.kind==='menu'||p.kind==='ingame';
const onMenu=p=>p.kind==='menu'||p.kind==='logo'||p.kind==='hide';   // shown on the menu's own color   // button sets: many buttons, exported as a zip of them all
const zipUrl=q=>'/api/presets-export?'+new URLSearchParams(q);
function exportSet(q,name){const a=document.createElement('a');a.href=zipUrl(q);a.download=fileName(name).replace(/\.png$/,'.zip');document.body.append(a);a.click();a.remove();
  message(`Saved ${a.download} in your Downloads folder, ready to send.`);}
async function exportFolder(q){const r=await api('/api/presets-folder',q);message(`Saved the folder “${r.name}” in your Downloads folder, ready to send.`);}
/* Right-click a kind or a category: everything in it, as one zip or as a folder in Downloads. */
const exportItems=(q,label,name)=>[[`Export ${label} as a zip`,()=>exportSet(q,name)],[`Export ${label} as a folder`,run(()=>exportFolder(q))]];
const exportOne=p=>sets(p)?exportSet({id:p.id},p.name):save(url(p.id),p.name);
/* Game files: a background (or virus) as the very files it changes in the game, named as there, to drop in as they are.
   Where it goes picks which files: every menu background, the in-game floors, light or dark mode... */
const kindOf=group=>({Backgrounds:'background',Viruses:'virus'})[group];
const gameTargets=kind=>P.gameFiles?.[kind]||[];
function gameFiles(q,label,name,kind,x,y){const t=gameTargets(kind);
  return t.length?[[`Export ${label} as game files ▸`,()=>popup([...t.map(([key,where])=>[`${where} (zip)`,()=>exportSet({...q,target:key},name+' game files')])],x,y)]]:[];}
/* Picks: Use picks a preset (one of each kind: a background, a virus, a food, menu buttons, in-game buttons), then Use all
   puts every pick in the game at once. */
// One pick of each kind; a Clean up pick goes with a button set, not instead of it.
const slot=p=>p.kind==='hide'?'Clean up':p.group;
const SLOTS=[...GROUPS,'Clean up'];
const isPicked=p=>P.picks[slot(p)]?.id===p.id;
function pick(p){if(isPicked(p))delete P.picks[slot(p)];else P.picks[slot(p)]=p;}
async function useAll(){const list=SLOTS.map(g=>P.picks[g]).filter(Boolean);if(!list.length)return;P.picks={};dlg.close();
  for(const p of list){if(p.kind==='hide')await hideNow(p);else await window.LabThemeChat?.ask(`Use the ${p.name} preset`,{picture:p.id,apply:true});}}
function tray(){
  const list=SLOTS.map(g=>P.picks[g]).filter(Boolean);const box=el('div','pv-tray');box.id='presetsTray';
  if(!list.length){box.append(el('span','pv-tray-empty','Use picks a preset. Pick a background, a virus, food and buttons, then use them all at once.'));return box;}
  const chips=el('div','pv-tray-picks');
  for(const p of list){const c=el('span','pv-pick');const im=el('img');im.src=url(p.id,'',true);im.alt='';const x=btn('✕','quiet','Unpick '+p.name);
    x.onclick=()=>{delete P.picks[slot(p)];render();};c.append(im,el('span','',`${slot(p)}: ${p.name}`),x);chips.append(c);}
  const clear=btn('Clear');clear.onclick=()=>{P.picks={};render();};
  const go=btn(`Use all (${list.length})`,'primary','Put every pick in the game');go.id='presetsUseAll';go.onclick=run(useAll);
  box.append(chips,clear,go);return box;}
function render(){
  dlg.replaceChildren();
  const head=el('div','pv-head');const x=btn('✕','quiet','Close');x.onclick=()=>dlg.close();
  const help=btn('?','quiet pv-help','Show how Presets works');help.onclick=()=>tour(true);
  head.append(el('h2','','Presets'),help,x);
  const groups=el('div','segmented pv-groups');
  for(const g of GROUPS){const n=P.list.filter(p=>p.group===g).length;if(!n)continue;
    const b=btn(`${g} · ${n}`);b.dataset.group=g;b.classList.toggle('active',g===P.group);b.onclick=()=>{P.group=g;P.category='';P.scroll=0;render();};
    b.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();popup([...exportItems({group:g},`all ${n} ${g}`,g),...gameFiles({group:g},`all ${n}`,g,kindOf(g),e.clientX,e.clientY)],e.clientX,e.clientY);};groups.append(b);}
  const mine=P.list.filter(p=>p.group===P.group),cats=[...new Set(mine.map(p=>p.category))];
  const chips=el('div','pv-cats');
  for(const c of ['',...cats]){const b=btn(c||'All','picture-chip');b.dataset.cat=c;b.classList.toggle('active',c===P.category);b.onclick=()=>{P.category=c;P.scroll=0;render();};
    // Right-click a category: all of it in one zip, to send to people.
    const n=mine.filter(p=>!c||p.category===c).length;
    b.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();const q=c?{group:P.group,category:c}:{group:P.group},name=c?`${P.group} ${c}`:P.group;
      popup([...exportItems(q,`all ${n} in ${c||P.group}`,name),...gameFiles(q,`all ${n} in ${c||P.group}`,name,kindOf(P.group),e.clientX,e.clientY)],e.clientX,e.clientY);};
    chips.append(b);}
  const grid=el('div','pv-grid'+(P.group==='Backgrounds'?' tiles':''));
  for(const p of mine.filter(p=>!P.category||p.category===P.category)){
    const card=btn('','pv-card');card.dataset.id=p.id;card.title=`${p.name} · ${p.category}`;card.classList.toggle('picked',isPicked(p));
    const pic=el('div','pv-thumb'+(onMenu(p)||sets(p)?' buttons':''));
    if(p.kind==='background')pic.style.backgroundImage=`url("${url(p.id,'',true)}")`;else{const im=el('img');im.loading='lazy';im.src=url(p.id,'',true);im.alt=p.name;pic.append(im);}
    card.append(pic,el('span','',p.name));card.onclick=()=>detail(p);
    card.oncontextmenu=e=>{e.preventDefault();menu(p,e.clientX,e.clientY);};grid.append(card);}
  const hint=el('p','pv-hint','Click a picture to see it big. Right-click a picture or a category to export it.');
  dlg.append(head,groups,chips,hint,grid,tray());grid.scrollTop=P.scroll;grid.onscroll=()=>{P.scroll=grid.scrollTop;};
}
// A preset big: Use picks it (or unpicks it), a whole theme from it, Export, or Back to the list.
function detail(p){
  dlg.replaceChildren();
  const head=el('div','pv-head');const x=btn('✕','quiet','Close');x.onclick=()=>dlg.close();
  head.append(el('h2','',p.name),el('span','pv-sub',`${p.group} · ${p.category}`),x);
  const on=isPicked(p);
  const go=btn(on?'Picked ✓':'Use','primary',on?'Click to unpick it':'Pick it, then pick more (a background, a virus, food, buttons) and use them all');go.id='presetUse';
  go.onclick=()=>{pick(p);render();};
  const back=btn('Back');back.onclick=render;
  const bar=el('div','pv-actions');bar.append(back);
  if(themeable(p)){const t=btn('Make a whole theme from this','','Draw the rest of the game in its colors and style');t.onclick=()=>whole(p);bar.append(t);}
  const out=btn('Export','',sets(p)?'Save every button in this set as PNGs (a zip) to send to people':'Save this picture as a PNG to send to people');out.onclick=()=>exportOne(p);
  const targets=gameTargets(p.kind);
  if(targets.length){   // what to export: the picture, or the game files it changes in one place (ready to drop in)
    const as=el('select','pv-export-as');as.id='presetExportAs';as.setAttribute('aria-label','What to export');
    as.append(new Option('The picture (PNG)',''),...targets.map(([key,where])=>new Option(`Game files: ${where}`,key)));
    as.title='Game files are the game’s own files this changes, named as in the game: drop them in, no renaming';
    out.onclick=()=>as.value?exportSet({id:p.id,target:as.value},p.name+' game files'):exportOne(p);bar.append(as);}
  bar.append(out);
  const pic=stage(url(p.id),onMenu(p)?'menu':p.kind,p.name);pic.dataset.exportName=p.name;
  if(p.kind==='hide')bar.prepend(el('p','pv-note','Hidden by making these pictures see-through: the game itself is not changed, and the empty spot can still be clicked.'));
  if(sets(p))bar.prepend(el('p','pv-note',p.kind==='menu'?'Some menu icons are colored by the game itself (the teal and pink ones): they keep that color, with this set’s shine and outline.':'Phone and PC buttons: split, eject and the joystick.'));
  bar.append(go);dlg.append(head,pic,bar);go.focus();
}
const pmenu=el('div','context preset-menu');pmenu.hidden=true;document.body.append(pmenu);
/* A right-click menu of [text, fn] entries. In the top layer's dialog when one is open, or it would show behind it. */
function popup(items,x,y){pmenu.replaceChildren();
  for(const [text,fn] of items){const b=btn(text);b.onclick=()=>{pmenu.hidden=true;fn();};pmenu.append(b);}
  const home=[...document.querySelectorAll('dialog[open]')].pop()||document.body;if(pmenu.parentNode!==home)home.append(pmenu);
  pmenu.hidden=false;pmenu.style.left=Math.min(innerWidth-260,x)+'px';pmenu.style.top=Math.min(innerHeight-40*items.length-10,y)+'px';}
function menu(p,x,y){const items=[['See it big',()=>detail(p)],[isPicked(p)?'Unpick it':'Pick it',()=>{pick(p);render();}],['Use this preset now',()=>use(p)]];
  if(themeable(p))items.push(['Make a whole theme from this',()=>whole(p)]);
  if(sets(p))items.push(...exportItems({id:p.id},'all its buttons',p.name));else items.push(['Export picture (PNG)',()=>exportOne(p)],...gameFiles({id:p.id},'it',p.name,p.kind,x,y));
  popup(items,x,y);}

// ----- Export: right-click a picture to save it as a PNG and send it to people --------------------------------------
const fileName=n=>(String(n||'picture').replace(/\.(png|webp|jpe?g)$/i,'').replace(/[^\p{L}\p{N} ._-]+/gu,' ').trim().replace(/\s+/g,'-').slice(0,80)||'picture')+'.png';
/* The full-size picture behind a shown one: a thumbnail's edit in full, a picture or drawing without "small". */
function fullSize(src){
  try{const u=new URL(src,location.href);if(u.origin!==location.origin)return src;
    if(u.pathname==='/api/thumb')return `/api/image?key=${encodeURIComponent(u.searchParams.get('key')||'')}${u.searchParams.get('original')?'&original=1':''}`;
    if(u.pathname==='/api/picture'||u.pathname==='/api/design-image'){u.searchParams.delete('small');return u.pathname+u.search;}}catch{}
  return src;}
function save(src,name){const a=document.createElement('a');a.href=fullSize(src);a.download=fileName(name);document.body.append(a);a.click();a.remove();
  message(`Saved ${a.download} in your Downloads folder, ready to send.`);}
/* What to call a picture: its own name, its card's, its caption, then its words. */
function nameOf(n){const named=n.closest('[data-export-name]');if(named)return named.dataset.exportName;
  const alt=n.getAttribute?.('alt')||n.getAttribute?.('aria-label')||'';if(alt&&!/^(before|after|the picture|)$/i.test(alt))return alt;
  return n.closest('figure')?.querySelector('figcaption')?.textContent||n.closest('[title]')?.title||'picture';}
function sourceOf(n){
  if(n.tagName==='CANVAS'){try{return n.toDataURL('image/png');}catch{return '';}}
  if(n.tagName==='IMG')return n.currentSrc||n.src||'';
  return (getComputedStyle(n).backgroundImage.match(/url\("?(.*?)"?\)/)||[])[1]||'';}
// Pictures in Theme AI's chat (edits, drawings, pictures and their cards) and in the big views. Before app.js's own menu.
const EXPORTABLE='#themeLog img, #themeLog canvas, #pictureViewer img, #pictureViewer .pv-stage.tiled, #presetsDialog .pv-stage img, #presetsDialog .pv-stage.tiled';
document.addEventListener('contextmenu',e=>{
  const pic=e.target.closest?.(EXPORTABLE);if(!pic||pic.closest('.preset-menu'))return;const src=sourceOf(pic);if(!src)return;
  e.preventDefault();e.stopPropagation();popup([['Export picture (PNG)',()=>save(src,nameOf(pic))]],e.clientX,e.clientY);},true);
window.LabExport={save,fullSize,fileName};
addEventListener('pointerdown',e=>{if(!pmenu.hidden&&!pmenu.contains(e.target))pmenu.hidden=true;},true);
addEventListener('keydown',e=>{if(e.key==='Escape')pmenu.hidden=true;});
dlg.addEventListener('close',()=>{pmenu.hidden=true;});
// ----- First time in Presets: a short tour of what it can do, inside the window (a page tour would sit behind it) -----
const TOUR_KEY='lab-presets-tour-done';
const TOUR=[
  {at:'.pv-groups',title:'Pick a kind',text:'Backgrounds, viruses, food, menu buttons and in-game buttons. Each kind has its own categories.'},
  {at:'.pv-cats',title:'Then a category',text:'Narrow it down by category. All shows everything in the kind.'},
  {at:'.pv-card',title:'Click a picture to see it big',text:'A background shows as the floor it becomes, a virus or food on the game’s light and dark floor. There, Use picks it.'},
  {at:'.pv-card',menu:['See it big','Pick it','Use this preset now','Make a whole theme from this','Export picture (PNG)'],title:'Right-click a picture',
   text:'Export saves it as a PNG to send to people. Make a whole theme draws the rest of the game to match it.'},
  {at:'.pv-cats .picture-chip:nth-child(2),.pv-cats .picture-chip',menu:['Export all in this category as a zip','Export all in this category as a folder'],title:'Right-click a category',
   text:'Export a whole category as one zip or as a folder, ready to send. Right-click a kind at the top to export all of it.'},
  {at:'.pv-groups button[data-group="Food"],.pv-groups',title:'Food: all at once',text:'The game has three foods, from small to big. A food preset goes on all of them at once (All foods), or pick just one in the chat.'},
  {at:'#presetsTray',title:'Pick a few, then use them all',text:'Pick a background, a virus, food and buttons: your picks wait down here. Use all puts every one in the game at once.'},
  {title:'That’s it',text:'See this again any time with the ? button at the top. Have fun!'}];
let coach=null;
function tour(force){
  if(!force){try{if(localStorage.getItem(TOUR_KEY))return;}catch{return;}}
  if(!dlg.open)return;
  if(!dlg.querySelector('.pv-grid'))render();
  coach?.remove();coach=el('div','pv-coach');coach.id='presetsTour';dlg.append(coach);
  let i=0;
  const end=()=>{coach?.remove();coach=null;try{localStorage.setItem(TOUR_KEY,'1');}catch{}};
  const show=()=>{
    const s=TOUR[i];coach.replaceChildren();
    const box=dlg.getBoundingClientRect();
    const target=s.at?s.at.split(',').map(q=>dlg.querySelector(q)).find(Boolean):null;
    const card=el('div','pv-coach-card');card.setAttribute('role','dialog');
    card.append(el('small','',`${i+1} / ${TOUR.length}`),el('h3','',s.title),el('p','',s.text));
    const row=el('div','pv-coach-actions');const skip=btn('Skip','quiet');skip.onclick=end;
    const next=btn(i===TOUR.length-1?'Done':'Next','primary');next.onclick=()=>{if(++i>=TOUR.length)end();else show();};
    if(i>0){const back=btn('Back');back.onclick=()=>{i--;show();};row.append(back);}
    row.append(skip,next);card.append(row);coach.append(card);
    let below=box.height/2;
    if(target){const r=target.getBoundingClientRect();const ring=el('div','pv-coach-ring');
      Object.assign(ring.style,{left:r.left-box.left-4+'px',top:r.top-box.top-4+'px',width:r.width+8+'px',height:r.height+8+'px'});coach.append(ring);
      if(s.menu){const m=el('div','context pv-coach-menu');for(const t of s.menu)m.append(btn(t));
        Object.assign(m.style,{left:Math.min(box.width-250,r.left-box.left+r.width*0.5)+'px',top:r.top-box.top+Math.min(r.height,40)+'px'});coach.append(m);}
      below=r.bottom-box.top+(s.menu?40*s.menu.length+20:14);
      if(below+190>box.height)below=Math.max(10,r.top-box.top-200);}
    card.style.top=Math.max(10,Math.min(box.height-200,below))+'px';
    next.focus();};
  show();}
async function open(group){
  if(!P.list){const r=await api('/api/presets');P.list=r.presets||[];P.gameFiles=r.game_files||{};P.version=r.version||'';}
  if(group&&GROUPS.includes(group))P.group=group;
  if(!P.list.some(p=>p.group===P.group))P.group=P.list[0]?.group||'Backgrounds';
  render();if(!dlg.open)dlg.showModal();tour(false);}
window.LabPresets={open:run(open)};
})();
