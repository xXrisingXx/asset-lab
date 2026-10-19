'use strict';
// First start: a short guided tour of the lab. Each step lights up one part of the page and says what it is for.
// It shows once (remembered in this browser) and comes back from the ? button in the top bar.
// Uses app.js globals: $.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const KEY='lab-tour-done',ASKED='lab-suggest-asked';
// Examples the tour can put in Theme AI's box with one click, and the right-click menu shown beside a picture.
const EXAMPLES=['gold buttons with a glow','a spooky halloween theme','turn the coin into a diamond','change the font to comic sans','make the player names out of lava'];
const RIGHT_CLICK={at:'#assetList .asset-card',menu:['Redraw with Image AI…','Change this font…','Add to favorites'],title:'Right-click for more',
  text:'Right-click any picture for quick actions: redraw it with Image AI, change a font (on the letter pictures), or add it to your favorites.'};
// Pictures: any picture a player pastes or drops, recolored in one request. Presets: the ready-made art, with how many.
const PICTURES={at:'#themePictureButton',theme:true,title:'Bring your own picture',
  text:'Add (or paste, or drop) a picture and say what to change, like “make this dark blue”.'};
const PRESETS={at:'#themePresetsButton',theme:true,count:true,title:'Presets',
  text:'{n} ready-made looks: backgrounds, viruses, food, menu and in-game buttons, titles and fonts. Click one to see it big, choose its color and where it goes, then Use now. Or pick a few and Use all.'};
// Simple's library: big pictures by name; right-click one for more.
const SIMPLE_RIGHT_CLICK={at:'#simpleGrid .simple-card',menu:['See it big','Check it','Redraw it with Image AI…'],title:'Right-click for more',
  text:'Right-click a picture to see it big, check it, or redraw it with Image AI.'};
const STEPS=[
  {title:'Welcome to Asset Lab',text:'Asset Lab lets you restyle Mitos.is: recolor or redraw its art, change its text and sounds, and install it all as a mod. Your game files are backed up first, and Restore original always puts them back. This quick tour shows you around.'},
  {at:'.library',title:'1. Find the art',text:'The library holds every picture in the game. Search by name (“play button”, “coin”), pick a category, or drop an image here to find art that looks like it. Click a card to open it; double-click to check it.'},
  {at:'#assetList',title:'2. Check assets',text:'Tick the box on a card (or double-click it) to check it. Checked assets can be favorited, removed from the list, or handed to Theme AI so only they change.'},
  RIGHT_CLICK,
  {at:'#viewport',title:'3. Edit on the canvas',text:'The open asset shows here. Paint with the tools on the left, zoom with the wheel, and drag with the right mouse button to move around. The menus above (File, Edit, Layer…) work like an image editor’s.'},
  {at:'.inspector',title:'4. Layers and saving',text:'Here you find the layers, the color controls and the history. When you like your edit, click Save edit: it is stored in your workspace, never straight in the game.'},
  {at:'#themeStarters,#themeButton',theme:true,examples:true,title:'5. Theme AI',text:'Describe a look in your own words (“spooky halloween”, “gold buttons with a glow”, “make the player names neon”) and Theme AI changes many assets at once. You see a preview before anything is saved.'},
  PICTURES,
  PRESETS,
  {at:'.mod-tray',title:'6. Test it in the game',text:'Close Mitosis, then click Apply to Game: it shows what goes in, then puts your saved edits in the game. Once your mod is in, Restore original appears here and puts every file back exactly as it was.'},
  {at:'#launchButton',title:'7. Play',text:'Open Mitosis starts the game through Steam so you can see your mod.'},
  {at:'#settingsButton',title:'8. Settings',text:'Change the language, the look of the lab, the AI and much more. You can open this tour again with the ? button next to it.'},
  {title:'You are ready',text:'Start by searching for something you want to change, or open Theme AI and describe a look. Have fun!'},
];
// Simple (simple.js): the same welcome, then just the four things Simple shows.
const SIMPLE_STEPS=[STEPS[0],
  {at:'#simpleLibrary,.library',title:'Check what to change',text:'Click the pictures you want to change to check them (click again to uncheck). Check nothing and Theme AI changes the whole game. Use the categories, or search by name: “play button”, “coin”, “virus”.'},
  SIMPLE_RIGHT_CLICK,
  {at:'#themeStarters,#themeInput',examples:true,title:'Describe it',text:'Type what you want (“gold buttons with a glow”, “a spooky halloween theme”, “change the font to comic sans”) or click a button above the box to start. You see it before anything is saved.'},
  PRESETS,
  PICTURES,
  {at:'#themeInstall',title:'Put it in the game',text:'When you like a change, close Mitosis and click Apply to Game: it shows what goes in, then puts it in the game. Once your changes are in, Restore original appears here and puts every file back exactly as it was.'},
  {at:'#viewSwitch',title:'Want more control?',text:'Advanced opens the full image editor: layers, brushes, filters and more. You can switch back any time.'},
  {title:'You are ready',text:'Check something you want to change, or just describe a look in the box (or click an idea). Have fun!'}];
let steps=STEPS;
let index=0,box=null,shade=null,ring=null,target=null,drawerWasOpen=false;
function target_(step){if(!step.at)return null;for(const s of step.at.split(',')){const e=document.querySelector(s.trim());if(e&&e.offsetParent!==null&&e.getBoundingClientRect().width>0)return e;}return null;}
function place(){
  if(!box)return;const r=target?target.getBoundingClientRect():null;
  if(!r){ring.hidden=true;box.classList.add('center');box.style.left=box.style.top='';return;}
  ring.hidden=false;box.classList.remove('center');const pad=6;
  Object.assign(ring.style,{left:r.left-pad+'px',top:r.top-pad+'px',width:r.width+pad*2+'px',height:r.height+pad*2+'px'});
  const bw=box.offsetWidth,bh=box.offsetHeight,vw=innerWidth,vh=innerHeight;
  // Beside the part when there is room (right, left, below, above), else in the middle of it.
  let x=r.right+16,y=r.top;if(x+bw>vw-12){x=r.left-bw-16;}if(x<12){x=Math.min(vw-bw-12,Math.max(12,r.left));y=r.bottom+16;if(y+bh>vh-12)y=r.top-bh-16;}
  if(y<12||y+bh>vh-12)y=Math.min(vh-bh-12,Math.max(12,r.top+r.height/2-bh/2));
  box.style.left=Math.round(x)+'px';box.style.top=Math.round(y)+'px';}
function show(i){
  const forward=i>=index;index=Math.max(0,Math.min(steps.length-1,i));const step=steps[index];
  // Theme AI is open on its own steps and closed on the others, where it would cover the part being shown (the ring
  // around a panel underneath showed through it). Simple keeps it open: there it is the page and covers nothing.
  const drawer=document.getElementById('themeDrawer'),simple=document.body.classList.contains('lab-simple');
  if(drawer&&!simple&&!!step.theme===drawer.hidden)document.getElementById('themeButton')?.click();
  target=target_(step);
  if(step.at&&!target&&index>0&&index<steps.length-1)return show(forward?index+1:index-1);   // a part that is hidden right now (e.g. a folded panel) is skipped
  box.replaceChildren();
  const count=el('div','tour-count',`${index+1} / ${steps.length}`);const h=el('h3','',step.title);const p=el('p','',step.count?(window.LabPresets?.total?step.text.replace('{n}',LabPresets.total):step.text.replace('{n} ready-made','Ready-made')):step.text);
  const actions=el('div','tour-actions');const skip=el('button','quiet',index===steps.length-1?'Close':'Skip the tour');skip.type='button';
  const back=el('button','','Back');back.type='button';back.disabled=index===0;const next=el('button','primary',index===steps.length-1?'Let’s go':'Next');next.type='button';
  skip.onclick=end;back.onclick=()=>show(index-1);next.onclick=()=>index===steps.length-1?end():show(index+1);
  actions.append(skip,back,next);box.append(count,h,p);
  if(step.examples)box.append(examples());
  if(step.menu)box.append(demoMenu(step.menu));
  box.append(actions);
  if(target)target.scrollIntoView({block:'nearest',inline:'nearest'});
  place();requestAnimationFrame(place);next.focus();}   // placed now too: a page in the background may get no frame for a while
// Examples: one click puts a request in Theme AI's box (opened if needed), ready to send or change. They stay English,
// the words Theme AI reads best.
function examples(){
  const list=el('div','tour-examples');list.append(el('small','','Try one:'));
  for(const text of EXAMPLES){const b=el('button','',text);b.type='button';b.translate=false;
    b.onclick=()=>{if(document.getElementById('themeDrawer')?.hidden)document.getElementById('themeButton')?.click();
      const input=document.getElementById('themeInput');if(input){input.value=text;input.dispatchEvent(new Event('input'));}
      list.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));requestAnimationFrame(place);};
    list.append(b);}
  return list;}
// What the right-click menu looks like, drawn in the tour card (it never covers the card's own words; nothing opens).
function demoMenu(items){
  const menu=el('div','tour-demo-menu');menu.setAttribute('aria-hidden','true');
  menu.append(el('span','tour-demo-pointer','🖱 Right-click (example)'));for(const item of items)menu.append(el('div','',item));
  return menu;}
function start(){
  end(true);index=0;drawerWasOpen=!document.getElementById('themeDrawer')?.hidden;steps=document.body.classList.contains('lab-simple')?SIMPLE_STEPS:STEPS;shade=el('div','tour-shade');ring=el('div','tour-ring');box=el('div','tour-box');box.setAttribute('role','dialog');box.setAttribute('aria-label','Asset Lab tour');
  document.body.append(shade,ring,box);show(0);addEventListener('resize',place);addEventListener('keydown',keys,true);}
function end(quiet){
  const drawer=document.getElementById('themeDrawer');
  if(box&&drawer&&!document.body.classList.contains('lab-simple')&&drawer.hidden===drawerWasOpen)document.getElementById('themeButton')?.click();   // as it was
  for(const n of [shade,ring,box])n?.remove();shade=ring=box=target=null;removeEventListener('resize',place);removeEventListener('keydown',keys,true);
  if(quiet!==true){try{localStorage.setItem(KEY,'1');}catch(e){}askForIdeas();}}
// The first time the tour ends (finished or skipped), About opens at Suggestions: the creator wants to hear from players.
function askForIdeas(){
  let asked=true;try{asked=!!localStorage.getItem(ASKED);localStorage.setItem(ASKED,'1');}catch(e){}
  if(asked||!window.LabSettings)return;
  setTimeout(()=>{LabSettings.open('about');let tries=0;
    const go=()=>{const box=document.getElementById('suggestText');if(!box&&tries++<30)return setTimeout(go,100);
      box?.scrollIntoView({block:'center'});if(box&&!box.disabled)box.focus();};go();},250);}
function keys(e){if(!box)return;
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();end();}
  else if(e.key==='ArrowRight'){e.preventDefault();e.stopPropagation();show(index+1);}
  else if(e.key==='ArrowLeft'){e.preventDefault();e.stopPropagation();show(index-1);}}
// The ? button beside the top bar's last buttons reopens it.
const help=el('button','quiet tour-help','?');help.id='tourButton';help.title='Show the tour of Asset Lab';help.setAttribute('aria-label','Show the tour of Asset Lab');help.onclick=start;
document.querySelector('.topbar')?.append(help);
window.LabTour={start,end};
// First start: after the page (and the library) have settled.
let seen=false;try{seen=!!localStorage.getItem(KEY);}catch(e){seen=true;}
if(!seen)setTimeout(()=>{if(!document.querySelector('dialog[open]'))start();},1200);
})();
