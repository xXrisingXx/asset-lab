'use strict';
// Presets: ready-made backgrounds, viruses and food, in a window like Game preview. Pick a group and a category, click a
// picture to see it big (a background as the tiled floor it becomes, a virus or food on the game's light and dark floor),
// choose its color and where it goes there, and Use now puts it in your changes (or Pick it, and Use all puts every pick
// in at once). Typing something that isn't a color hands it to Theme AI. The same big view shows any picture card's picture.
// Uses app.js globals: $, api, run, message. Talks to Theme AI through window.LabThemeChat.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(text,cls,title)=>{const b=el('button',cls||'',text);b.type='button';if(title)b.title=title;return b;};
// A preset's picture carries the presets' version: the browser keeps it, so switching categories shows it at once.
const url=(id,color,small)=>`/api/picture?id=${encodeURIComponent(id)}${color?'&color='+encodeURIComponent(color):''}${small?'&small=1':''}${String(id).startsWith('preset:')&&P.version?'&v='+encodeURIComponent(P.version):''}`;
const GROUPS=['Backgrounds','Viruses','Food','Menu buttons','In-game buttons','Title','Fonts'];
const P={list:null,group:'Backgrounds',category:'',scroll:0,picks:{},custom:{},places:{}};
const CHIPS=[['Dark blue','#1e3a8a'],['Blue','#3b82f6'],['Red','#e53935'],['Gold','#f5c542'],['Green','#2ecc71'],['Pink','#ff6fb5'],['Purple','#9b5de5'],['Black','#101216'],['White','#ffffff']];
// Where a preset goes, when it can go on more than one place: what each place covers (a pick on a place takes over any
// pick it overlaps), and its short name in the picks.
const COVER={'set:everywhere':['menu','light','dark'],'set:menu':['menu'],'set:arena':['light','dark'],'set:arena-light':['light'],'set:arena-dark':['dark'],
  'set:viruses':['light','dark'],'set:virus-light':['light'],'set:virus-dark':['dark'],
  'set:food:all':[0,1,2],'set:food:0':[0],'set:food:1':[1],'set:food:2':[2],
  'set:ingame-buttons':['phone','pc'],'set:ingame-phone':['phone'],'set:ingame-pc':['pc']};
const SHORT={'set:everywhere':'everywhere','set:menu':'main menu','set:arena':'in game','set:arena-light':'in game, light mode','set:arena-dark':'in game, dark mode',
  'set:viruses':'light and dark mode','set:virus-light':'light mode','set:virus-dark':'dark mode',
  'set:food:all':'all foods','set:food:0':'food 1','set:food:1':'food 2','set:food:2':'food 3',
  'set:ingame-buttons':'phones and PCs','set:ingame-phone':'phones','set:ingame-pc':'PCs'};
// Food: the game's three foods by number, smallest first, each shown with the game's own picture.
const FOOD={'set:food:all':['All foods','every size at once'],'set:food:0':['Food 1','the smallest'],'set:food:1':['Food 2','the middle one'],'set:food:2':['Food 3','the biggest']};
const places=p=>P.places?.[p.kind]||[];
// How a preset is customized in its big view: its color, where it goes, a background's strength, a title's size.
const custom=p=>P.custom[p.id]||(P.custom[p.id]={color:'',where:places(p)[0]?.[0]||'',strength:1,scale:1});
/* A preset into your changes as customized (q: the preset with its color, place, strength and size): no chat needed. */
async function put(q){
  if(q.kind==='hide')return hideNow(q);
  if(q.kind==='font')return api('/api/font-apply',{font:q.where||places(q)[0]?.[0]||'nicktext',style:q.style||{}});
  const key=q.where||places(q)[0]?.[0]||'';   // none: the lab finds the best place for it
  return api('/api/picture-apply',{id:q.id,key,color:q.color||'',strength:q.kind==='background'?q.strength??1:1,scale:q.kind==='logo'?q.scale??1:1});}
async function refreshLab(){S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);}

// ----- the big view (also for a picture card's picture) -----------------------------------------------------------
/* A background is shown as the tiled floor it becomes; anything else on the game's light and dark floor. */
function stage(src,kind,name){
  const box=el('div','pv-stage'+(kind==='background'?' tiled':''));
  if(kind==='background'){box.setAttribute('role','img');box.setAttribute('aria-label',name||'');box.show=s=>{box.style.backgroundImage=`url("${s}")`;};}
  else{const ims=[];for(const mode of kind==='menu'?['menu']:['light','dark']){/* menu buttons: on the menu's own color */const pane=el('div','pv-floor '+mode);const im=el('img');im.alt=name||'';ims.push(im);pane.append(im);box.append(pane);}
    box.show=s=>{for(const im of ims)im.src=s;};}
  box.show(src);return box;}
function fontStage(p,c){
  const box=el('div','pv-stage pv-font-stage');const im=el('img');im.alt=p.name;box.append(im);let seq=0;
  box.refresh=async()=>{const n=++seq;try{const r=await api('/api/font-preview',{font:c.where||places(p)[0]?.[0]||'nicktext',style:p.style||{},text:c.text||'Mitosis Player'});if(n===seq)im.src=r.png;}
    catch(e){if(n===seq){box.replaceChildren(el('p','pv-note',e.message));}}};
  box.show=()=>{};box.refresh();return box;}
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
async function hideNow(p){const r=await api('/api/picture-apply',{id:p.id,key:'set:cleanup'});message(`${p.name}: hidden in your build (${r.count} picture${r.count===1?'':'s'} made see-through). Apply to Game to see it.`);}
async function use(p){await put({...p,...custom(p)});await refreshLab();
  message(p.kind==='hide'?`${p.name}: hidden in your changes. Apply to Game to see it.`:`${p.name} is in your changes. Apply to Game to put it in the game.`);}
function whole(p){dlg.close();window.LabThemeChat?.ask(`Make a whole theme from ${p.name}`,{theme_from:p.id});}
const themeable=p=>p.kind==='background'||p.kind==='virus';
const sets=p=>p.kind==='menu'||p.kind==='ingame';
const onMenu=p=>p.kind==='menu'||p.kind==='logo'||p.kind==='hide'||p.kind==='font';   // shown on the menu's own color   // button sets: many buttons, exported as a zip of them all
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
/* Picks: Pick keeps a preset (with its color and place) to use with others, then Use all puts every pick in your changes
   at once. One pick of each kind, or one per place: a background for the menu and others for the light and dark floors,
   a virus for each mode, a food for each size. A Clean up pick goes with a button set, not instead of it. */
const slot=(p,where)=>p.kind==='hide'?'Clean up':COVER[where]||(p.kind==='font'&&where)?`${p.group} · ${where}`:p.group;
const SLOTS=[...GROUPS,'Clean up'];
const rank=q=>SLOTS.indexOf(q.kind==='hide'?'Clean up':q.group)*10+Math.max(0,places(q).findIndex(x=>x[0]===q.where));
const picked=()=>Object.values(P.picks).sort((a,b)=>rank(a)-rank(b));
const isPicked=(p,where)=>Object.values(P.picks).some(q=>q.id===p.id&&(where===undefined||q.where===where));
function pick(p){const c=custom(p),key=slot(p,c.where);
  if(P.picks[key]?.id===p.id){delete P.picks[key];return;}
  if(COVER[c.where])for(const [k,q] of Object.entries(P.picks))if(q.group===p.group&&COVER[q.where]?.some(x=>COVER[c.where].includes(x)))delete P.picks[k];   // that place is taken over
  P.picks[key]={...p,...c};}
async function useAll(){const list=picked();if(!list.length)return;P.picks={};dlg.close();
  const failed=[];let done=0;   // one that cannot go in (this game has nowhere for it) never stops the others
  for(const q of list){try{await put(q);done++;}catch(e){failed.push(`${q.name}: ${e.message}`);}}
  if(done)await refreshLab();
  if(failed.length)message(failed.join(' · '),true);
  else message(done===1?`${list[0].name} is in your changes. Apply to Game to put it in the game.`:`${done} presets are in your changes. Apply to Game to put them in the game.`);}
function tray(){
  const list=picked();const box=el('div','pv-tray');box.id='presetsTray';
  if(!list.length){box.append(el('span','pv-tray-empty','Pick a few presets (a background for the menu and another for the game, a virus, food, buttons), then Use all puts them in at once.'));return box;}
  const chips=el('div','pv-tray-picks');
  for(const q of list){const c=el('span','pv-pick');const im=el('img');im.src=url(q.id,q.color,true);im.alt='';const x=btn('✕','quiet','Unpick '+q.name);
    x.onclick=()=>{delete P.picks[slot(q,q.where)];render();};
    const what=el('b','',q.kind==='hide'?'Clean up':q.group);const spot=SHORT[q.where]||(q.kind==='font'&&places(q).find(x=>x[0]===q.where)?.[1]);
    if(places(q).length>1&&spot)what.append(el('small','',spot));
    c.append(im,what,el('span','',q.name),x);chips.append(c);}
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
    // A shimmer until the picture is there (a first look at a tab used to show plain white squares).
    pic.classList.add('loading');const done=()=>pic.classList.remove('loading');
    if(p.kind==='background'){const probe=new Image();probe.onload=probe.onerror=done;probe.src=url(p.id,'',true);pic.style.backgroundImage=`url("${probe.src}")`;}
    else{const im=el('img');im.loading='lazy';im.onload=im.onerror=done;im.src=url(p.id,'',true);im.alt=p.name;pic.append(im);}
    card.append(pic,el('span','',p.name));card.onclick=()=>detail(p);
    card.oncontextmenu=e=>{e.preventDefault();menu(p,e.clientX,e.clientY);};grid.append(card);}
  const hint=el('p','pv-hint','Click a picture to see it big. Right-click a picture or a category to export it.');
  dlg.append(head,groups,chips,hint,grid,tray());grid.scrollTop=P.scroll;grid.onscroll=()=>{P.scroll=grid.scrollTop;};
}
// A preset big, customized beside it (color, where it goes, a background's strength, a title's size): Use now puts it in
// your changes, Pick keeps it for Use all. Also a whole theme from it, Export, or Back to the list.
function detail(p){
  dlg.replaceChildren();const c=custom(p);
  const head=el('div','pv-head');const x=btn('✕','quiet','Close');x.onclick=()=>dlg.close();
  head.append(el('h2','',p.name),el('span','pv-sub',`${p.group} · ${p.category}`),x);
  const pic=p.kind==='font'?fontStage(p,c):stage(url(p.id,c.color),onMenu(p)?'menu':p.kind,p.name);pic.dataset.exportName=p.name;
  const side=el('div','pv-custom');side.id='presetCustom';
  const section=(title,...parts)=>{const box=el('div','pv-section');box.append(el('h3','',title),...parts);side.append(box);return box;};
  // A pick follows what is changed here, and the buttons say whether this place already has it.
  const now=btn('Use now','primary','Put it in your changes now, as set up here');now.id='presetUseNow';
  const keep=btn('','', '');keep.id='presetUse';
  const state=()=>{const on=isPicked(p,c.where);keep.textContent=on?'Picked ✓':'Pick';keep.title=on?'Click to unpick it':'Keep it with your other picks, then Use all puts them all in at once';};
  const sync=()=>{const key=slot(p,c.where);if(P.picks[key]?.id===p.id)P.picks[key]={...p,...c};state();};
  if(p.kind==='font'){   // a name to see it on, as it will look on the cells and in lists
    const name=el('input','picture-words');name.id='presetFontText';name.placeholder='Type a name to try it';name.maxLength=40;
    let wait=0;name.oninput=()=>{clearTimeout(wait);wait=setTimeout(()=>{c.text=name.value.trim();pic.refresh();},250);};
    section('Try it',name);}
  if(p.kind!=='hide'&&p.kind!=='font'){
    // Color: one click, or typed. Anything that is not just a color goes to Theme AI with this preset.
    const chips=el('div','pv-colors');
    const paint=()=>{pic.show(url(p.id,c.color));pic.dataset.exportName=[p.name,(c.color||'').replace('#','')].filter(Boolean).join(' ');chips.querySelectorAll('button').forEach(b=>b.classList.toggle('active',(b.dataset.color||'')===(c.color||'')));sync();};
    for(const [label,color] of [['Original',''],...CHIPS]){const b=btn('','picture-chip',label);b.dataset.color=color;
      if(color){const dot=el('i');dot.style.background=color;b.append(dot);}b.append(el('span','',label));b.onclick=()=>{c.color=color;paint();};chips.append(b);}
    const words=el('input','picture-words');words.id='presetWords';words.placeholder='Type a color (“dark purple”, “darker”) or ask Theme AI';
    const change=btn('Change it');change.id='presetChange';
    change.onclick=run(async()=>{const said=words.value.trim();if(!said)return words.focus();
      const r=await api('/api/picture-color',{id:p.id,words:said});
      if(r.color&&said.split(/\s+/).length<=4){c.color=r.color;words.value='';paint();return;}
      dlg.close();window.LabThemeChat?.ask(said,{picture:p.id});});   // "make it a pirate flag", "use it with a glow": Theme AI's job
    words.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();change.click();}};
    const row=el('div','design-refine-row');row.append(words,change);
    section('Color',chips,row);paint();}
  // Where it goes, when there is a choice: the main menu or the game (light or dark mode), a virus mode, which food.
  const spots=places(p);
  if(spots.length>1){
    const list=el('div','pv-places'+(p.kind==='food'?' food':''));list.id='presetPlaces';list.setAttribute('role','radiogroup');list.setAttribute('aria-label','Where it goes');
    const mark=()=>{list.querySelectorAll('button').forEach(b=>{const on=b.dataset.place===c.where;b.classList.toggle('active',on);b.setAttribute('aria-checked',on);});};
    for(const [key,name,look] of spots){const b=btn('','pv-place');b.dataset.place=key;b.setAttribute('role','radio');
      if(p.kind==='food'&&FOOD[key]){const pics=el('span','pv-food-pics');
        for(const k of key==='set:food:all'?spots.filter(s=>s[2]).map(s=>s[2]):[look]){if(!k)continue;const im=el('img');im.alt='';im.src=`/api/thumb?key=${encodeURIComponent(k)}`;pics.append(im);}
        b.append(pics,el('b','',FOOD[key][0]),el('small','',FOOD[key][1]));}
      else b.append(el('span','',name));
      b.onclick=()=>{c.where=key;mark();more();state();pic.refresh?.();};list.append(b);}
    section(p.kind==='food'?'Which food':p.kind==='font'?'Which text':'Where it goes',list);mark();}
  // A background: how much of the game's own color shows through. A title: its size in the title's place.
  const strength=el('input');strength.type='range';strength.min=0.2;strength.max=1;strength.step=0.05;strength.value=c.strength;strength.id='presetStrength';
  strength.oninput=()=>{c.strength=+strength.value;sync();};
  const dark=el('p','pv-note','In dark mode the game lays its own dark color over the in-game floor, so it looks darker there than here.');
  const size=el('input');size.type='range';size.min=0.5;size.max=1.5;size.step=0.05;size.value=c.scale;size.id='presetSize';
  const sizeText=el('small','pv-size-text');const sized=()=>{sizeText.textContent=Math.round(c.scale*100)+'%';};
  size.oninput=()=>{c.scale=+size.value;sized();sync();};sized();
  let strengthBox=null;
  if(p.kind==='background'){strengthBox=section('Strength',strength,el('p','pv-note','Full covers the game’s own color. Lower lets it show through.'),dark);}
  if(p.kind==='logo'){const line=el('div','pv-size');line.append(size,sizeText);
    section('Size',line,el('p','pv-note','100% fits the whole title in its place, nothing cut off. Bigger fills more of the menu, but may cut off what sticks out.'));}
  const more=()=>{dark.hidden=!/^set:(?:everywhere|arena$|arena-dark)/.test(c.where);};more();
  if(p.kind==='hide')side.append(el('p','pv-note','Hidden by making these pictures see-through: the game itself is not changed, and the empty spot can still be clicked.'));
  if(sets(p))side.append(el('p','pv-note',p.kind==='menu'?'Some menu icons are colored by the game itself (the teal and pink ones): they keep that color, with this set’s shine and outline.':'Phone and PC buttons: split, eject and the joystick.'));
  now.onclick=run(async()=>{now.disabled=true;try{await use(p);render();}finally{now.disabled=false;}});
  keep.onclick=()=>{pick(p);render();};state();
  const back=btn('Back');back.onclick=render;
  const bar=el('div','pv-actions');bar.append(back);
  if(themeable(p)){const t=btn('Make a whole theme from this','','Draw the rest of the game in its colors and style');t.onclick=()=>whole(p);bar.append(t);}
  const out=btn('Export','',sets(p)?'Save every button in this set as PNGs (a zip) to send to people':'Save this picture as a PNG to send to people');out.onclick=()=>exportOne(p);
  if(p.kind==='font'){const tune=btn('Fine-tune…','','Open it in Text & fonts to change the typeface, colors, outline and glow');
    tune.onclick=()=>{dlg.close();window.openFonts?.(c.where||places(p)[0]?.[0],p.style);};if(window.openFonts)bar.append(tune);}
  const targets=gameTargets(p.kind);
  if(targets.length){   // what to export: the picture, or the game files it changes in one place (ready to drop in)
    const as=el('select','pv-export-as');as.id='presetExportAs';as.setAttribute('aria-label','What to export');
    as.append(new Option('The picture (PNG)',''),...targets.map(([key,where])=>new Option(`Game files: ${where}`,key)));
    as.title='Game files are the game’s own files this changes, named as in the game: drop them in, no renaming';
    out.onclick=()=>as.value?exportSet({id:p.id,target:as.value},p.name+' game files'):exportOne(p);bar.append(as);}
  if(p.kind==='font')bar.append(keep,now);else bar.append(out,keep,now);
  const body=el('div','pv-body');body.append(pic,side);
  dlg.append(head,body,bar);now.focus();
}
const pmenu=el('div','context preset-menu');pmenu.hidden=true;document.body.append(pmenu);
/* A right-click menu of [text, fn] entries. In the top layer's dialog when one is open, or it would show behind it. */
function popup(items,x,y){pmenu.replaceChildren();
  for(const [text,fn] of items){const b=btn(text);b.onclick=()=>{pmenu.hidden=true;fn();};pmenu.append(b);}
  const home=[...document.querySelectorAll('dialog[open]')].pop()||document.body;if(pmenu.parentNode!==home)home.append(pmenu);
  pmenu.hidden=false;pmenu.style.left=Math.min(innerWidth-260,x)+'px';pmenu.style.top=Math.min(innerHeight-40*items.length-10,y)+'px';}
function menu(p,x,y){const items=[['See it big',()=>detail(p)],[isPicked(p,custom(p).where)?'Unpick it':'Pick it',()=>{pick(p);render();}],['Use this preset now',run(()=>use(p))]];
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
  {at:'.pv-groups',title:'Pick a kind',text:'Backgrounds, viruses, food, menu and in-game buttons, titles and fonts. Each kind has its own categories.'},
  {at:'.pv-cats',title:'Then a category',text:'Narrow it down by category. All shows everything in the kind.'},
  {at:'.pv-card',title:'Click a picture to see it big',text:'There you choose its color and where it goes (the main menu, the game in light or dark mode, which food), then Use now puts it in your changes.'},
  {at:'.pv-card',menu:['See it big','Pick it','Use this preset now','Make a whole theme from this','Export picture (PNG)'],title:'Right-click a picture',
   text:'Export saves it as a PNG to send to people. Make a whole theme draws the rest of the game to match it.'},
  {at:'.pv-cats .picture-chip:nth-child(2),.pv-cats .picture-chip',menu:['Export all in this category as a zip','Export all in this category as a folder'],title:'Right-click a category',
   text:'Export a whole category as one zip or as a folder, ready to send. Right-click a kind at the top to export all of it.'},
  {at:'.pv-groups button[data-group="Food"],.pv-groups',title:'Food 1, 2 and 3',text:'The game has three foods, from the smallest (Food 1) to the biggest (Food 3). In a food’s big view, put it on all of them at once or on just one.'},
  {at:'#presetsTray',title:'Pick a few, then use them all',text:'Pick keeps a preset down here with its color and place: one background for the menu and another for the dark-mode floor, a virus, food, buttons. Use all puts every pick in your changes at once, and Apply to Game puts them in the game.'},
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
function take(r){P.list=r.presets||[];P.gameFiles=r.game_files||{};P.places=r.places||{};P.version=r.version||'';}
let loading=null;
const load=()=>loading||(loading=api('/api/presets').then(take).catch(e=>{loading=null;throw e;}));
async function open(group){
  if(!P.list)await load();
  if(group&&GROUPS.includes(group))P.group=group;
  if(!P.list.some(p=>p.group===P.group))P.group=P.list[0]?.group||'Backgrounds';
  render();if(!dlg.open)dlg.showModal();tour(false);}
window.LabPresets={open:run(open),get total(){return P.list?.length||0;}};
setTimeout(()=>{if(!P.list)load().catch(()=>{});},300);   // ready before the tour and the first click
})();
