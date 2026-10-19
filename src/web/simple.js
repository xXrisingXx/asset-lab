'use strict';
// Simple and Advanced. Simple is the library and Theme AI only, for players who want a new look without an image
// editor: click pictures to check what changes, describe it, apply it to the game. Advanced is the full editor. Everyone
// starts in Simple; a switch to Advanced is remembered in this browser.
// Simple's library is its own: the parts of the game a player recognizes (/api/simple-assets), by friendly name, in
// big cards, one per thing (checking one checks every copy of it), with what is checked always in sight.
// Uses app.js globals: $, api, run, message.
(()=>{
const KEY='lab-layout';   // only a switch the player made (1.0.51 saved 'lab-view' for everyone by itself)
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(text,cls,title)=>{const b=el('button',cls||'',text);b.type='button';if(title)b.title=title;return b;};
let view='advanced';
try{view=localStorage.getItem(KEY)||'simple';}catch(e){view='simple';}
if(view!=='simple')view='advanced';
const seg=el('div','segmented view-switch');seg.id='viewSwitch';seg.setAttribute('role','group');seg.setAttribute('aria-label','Layout');
for(const [id,label,tip] of [['simple','Simple','Check what to change and describe it: Theme AI does the rest'],['advanced','Advanced','The full image editor: layers, brushes, filters and more']]){
  const b=el('button','',label);b.type='button';b.dataset.view=id;b.title=tip;b.onclick=()=>use(id,true);seg.append(b);}
document.querySelector('.topbar').insertBefore(seg,$('themeButton')||$('launchButton'));

// ----- Simple's library ------------------------------------------------------------------------------------------
const L={category:'',q:'',offset:0,total:0,seq:0,groups:new Map()};   // groups: key -> {name, key (shown), keys}
try{for(const g of JSON.parse(sessionStorage.getItem('lab-simple-groups')||'[]'))L.groups.set(g.key,g);}catch(e){}
const box=el('div','simple-library');box.id='simpleLibrary';
const title=el('h1','','Check what to change');
const hint=el('p','simple-hint','Click a picture to check it, again to uncheck. Check nothing to change the whole game.');
const search=el('input');search.type='search';search.id='simpleSearch';search.placeholder='Search: play button, coin, virus…';search.setAttribute('aria-label','Search the game’s pictures');
const chips=el('div','simple-cats');chips.id='simpleCategories';
const checked=el('div','simple-checked');checked.id='simpleChecked';checked.hidden=true;
const grid=el('div','simple-grid');grid.id='simpleGrid';
const more=btn('Show more','simple-more');more.hidden=true;
box.append(title,hint,search,chips,checked,grid,more);
document.querySelector('.library-head')?.after(box);
const thumb=key=>`/api/thumb?key=${encodeURIComponent(key)}&big=1&v=${L.stamp}`;   // a new address after changes: never the old picture
L.stamp=Date.now();
const isChecked=g=>g.keys.length&&g.keys.every(k=>window.LabSelection?.has(k));
function remember(g){L.groups.set(g.key,g);try{sessionStorage.setItem('lab-simple-groups',JSON.stringify([...L.groups.values()].slice(-400)));}catch(e){}}
function toggle(g,on){window.LabSelection?.set(g.keys,on??!isChecked(g));}
function card(g){
  remember(g);
  const c=btn('','simple-card');c.dataset.key=g.key;c.title=g.name+'\nClick to check it · right-click for more';
  const pic=el('span','simple-thumb'+(g.category==='backgrounds'?' tile':''));const im=el('img');im.loading='lazy';im.alt='';im.src=thumb(g.key);pic.append(im);
  const mark=el('span','simple-mark','✓');mark.setAttribute('aria-hidden','true');
  c.append(pic,el('span','simple-name',g.name),mark);
  if(g.saved){const dot=el('span','simple-saved');dot.title='Changed';c.append(dot);}
  c.setAttribute('aria-pressed',String(isChecked(g)));c.classList.toggle('checked',isChecked(g));
  c.onclick=()=>toggle(g);
  c.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();menu(g,e.clientX,e.clientY);};
  return c;}
async function load(append){
  if(view!=='simple')return;
  const seq=++L.seq;
  const r=await api('/api/simple-assets?'+new URLSearchParams({category:L.category,q:L.q,offset:L.offset,limit:60}));
  if(seq!==L.seq)return;
  if(!append)grid.replaceChildren();
  for(const g of r.items)grid.append(card(g));
  L.total=r.total;more.hidden=L.offset+r.items.length>=r.total;
  if(!append)chipsFor(r.categories);
  if(!r.items.length&&!append)grid.append(el('p','simple-empty',L.q?'Nothing with that name. Try another word, or All.':'Nothing here.'));}
// While the lab is still starting (or restarting) it does not answer yet: try again in a moment, quietly.
let retry;function reload(){clearTimeout(retry);L.offset=0;load(false).catch(()=>{retry=setTimeout(reload,1500);});}
function chipsFor(cats){chips.replaceChildren();
  for(const [id,name] of [['','All'],...cats.map(c=>[c.id,c.name])]){const b=btn(name,'picture-chip');b.dataset.cat=id;b.classList.toggle('active',id===L.category);
    b.onclick=()=>{L.category=id;L.offset=0;run(()=>load(false))();};chips.append(b);}}
let typing;search.oninput=()=>{clearTimeout(typing);typing=setTimeout(()=>{L.q=search.value.trim();L.offset=0;run(()=>load(false))();},250);};
more.onclick=run(async()=>{L.offset+=60;await load(true);});
// What is checked, always in sight: each picture with an ✕, and Clear.
function showChecked(){
  const keys=window.LabSelection?.keys()||[];checked.replaceChildren();checked.hidden=!keys.length;if(!keys.length)return;
  const shown=[...L.groups.values()].filter(isChecked);const covered=new Set(shown.flatMap(g=>g.keys));const others=keys.filter(k=>!covered.has(k)).length;
  checked.append(el('span','simple-checked-title',`Checked (${shown.length+others})`));
  for(const g of shown){const chip=el('span','simple-chip');const im=el('img');im.alt='';im.src=thumb(g.key);const x=btn('✕','quiet','Uncheck '+g.name);
    x.onclick=()=>toggle(g,false);chip.append(im,el('span','',g.name),x);checked.append(chip);}
  if(others)checked.append(el('span','simple-chip more',`+${others} more`));
  const clear=btn('Clear','quiet simple-clear','Uncheck everything');clear.onclick=()=>window.LabSelection?.clear();checked.append(clear);}
document.addEventListener('lab-selection',()=>{
  for(const c of grid.querySelectorAll('.simple-card')){const g=L.groups.get(c.dataset.key);if(!g)continue;const on=isChecked(g);c.classList.toggle('checked',on);c.setAttribute('aria-pressed',String(on));}
  showChecked();});
// Right-click a picture: see it big, check it, or redraw it with Image AI.
const pmenu=el('div','context simple-menu');pmenu.hidden=true;document.body.append(pmenu);
function menu(g,x,y){pmenu.replaceChildren();
  const item=(text,fn)=>{const b=btn(text);b.onclick=()=>{pmenu.hidden=true;fn();};pmenu.append(b);};
  item('See it big',()=>window.LabViewer?.open({src:`/api/image?key=${encodeURIComponent(g.key)}`,kind:g.category==='backgrounds'?'background':'sprite',name:g.name}));
  item(isChecked(g)?'Uncheck it':'Check it',()=>toggle(g));
  item('Redraw it with Image AI…',()=>{toggle(g,true);const input=$('themeInput');if(input){input.value=`Redraw ${g.name} as `;input.focus();input.setSelectionRange(input.value.length,input.value.length);}});
  pmenu.hidden=false;pmenu.style.left=Math.min(innerWidth-240,x)+'px';pmenu.style.top=Math.min(innerHeight-130,y)+'px';}
addEventListener('pointerdown',e=>{if(!pmenu.hidden&&!pmenu.contains(e.target))pmenu.hidden=true;},true);
addEventListener('keydown',e=>{if(e.key==='Escape')pmenu.hidden=true;});

function openAI(){const d=$('themeDrawer');if(view==='simple'&&d&&d.hidden)$('themeButton')?.click();}
function use(next,save){
  view=next==='simple'?'simple':'advanced';document.body.classList.toggle('lab-simple',view==='simple');
  seg.querySelectorAll('button').forEach(b=>{const on=b.dataset.view===view;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});
  if(save)try{localStorage.setItem(KEY,view);}catch(e){}
  // Simple: Image AI is always on (no mode to pick), and Accelerate sits beside Show me; Advanced keeps both in More.
  const accel=$('themeAccelerate'),send=$('themeSend'),extras=document.querySelector('#themeMore .theme-more-row');
  if(accel&&send&&extras){if(view==='simple')send.before(accel);else extras.prepend(accel);}
  openAI();dispatchEvent(new Event('resize'));
  if(view==='simple'){reload();showChecked();}
  document.dispatchEvent(new CustomEvent('lab-selection',{detail:{count:window.LabSelection?.size()||0}}));}   // Theme AI says again what a request changes
// In Simple, Theme AI is the page, so it stays open.
new MutationObserver(()=>setTimeout(openAI,0)).observe($('themeDrawer'),{attributes:true,attributeFilter:['hidden']});
use(view,false);
// When the lab's saved art changes (a theme applied, a preset used), the lab lists its library again: so do the cards.
let again;new MutationObserver(()=>{if(view!=='simple')return;clearTimeout(again);again=setTimeout(()=>{L.stamp=Date.now();reload();showChecked();},400);})
  .observe($('assetList'),{childList:true});
window.LabView={get:()=>view,set:v=>use(v,true)};
// How many pictures are checked, as Simple counts them (a card, with all its copies, is one).
function count(){const keys=window.LabSelection?.keys()||[];const shown=[...L.groups.values()].filter(isChecked);
  const covered=new Set(shown.flatMap(g=>g.keys));return shown.length+keys.filter(k=>!covered.has(k)).length;}
window.LabSimpleLibrary={reload,count};
})();
