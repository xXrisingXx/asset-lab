'use strict';
// First start: a short guided tour of the lab. Each step lights up one part of the page and says what it is for.
// It shows once (remembered in this browser) and comes back from the ? button in the top bar.
// Uses app.js globals: $.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const KEY='lab-tour-done';
const STEPS=[
  {title:'Welcome to Asset Lab',text:'Asset Lab lets you restyle Mitos.is: recolor or redraw its art, change its text and sounds, and install it all as a mod. Your game files are backed up first, and Restore original always puts them back. This quick tour shows you around.'},
  {at:'.library',title:'1. Find the art',text:'The library holds every picture in the game. Search by name (“play button”, “coin”), pick a category, or drop an image here to find art that looks like it. Click a card to open it; double-click to check it.'},
  {at:'#assetList',title:'2. Check assets',text:'Tick the box on a card (or double-click it) to check it. Checked assets can be favorited, removed from the list, or handed to Theme AI so only they change.'},
  {at:'#viewport',title:'3. Edit on the canvas',text:'The open asset shows here. Paint with the tools on the left, zoom with the wheel, and drag with the right mouse button to move around. The menus above (File, Edit, Layer…) work like an image editor’s.'},
  {at:'.inspector',title:'4. Layers and saving',text:'Here you find the layers, the color controls and the history. When you like your edit, click Save edit: it is stored in your workspace, never straight in the game.'},
  {at:'#themeButton',title:'5. Theme AI',text:'Describe a look in your own words (“spooky halloween”, “gold buttons with a glow”, “make the player names neon”) and Theme AI changes many assets at once. You see a preview before anything is saved.'},
  {at:'.mod-tray',title:'6. Test it in the game',text:'Close Mitosis, then click Install (or Update Mitosis) to put your saved edits in the game. Restore original takes the mod back out and returns every file exactly as it was.'},
  {at:'#launchButton',title:'7. Play',text:'Open Mitosis starts the game through Steam so you can see your mod.'},
  {at:'#settingsButton',title:'8. Settings',text:'Change the language, the look of the lab, the AI and much more. You can open this tour again with the ? button next to it.'},
  {title:'You are ready',text:'Start by searching for something you want to change, or open Theme AI and describe a look. Have fun!'},
];
let index=0,box=null,shade=null,ring=null,target=null;
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
  const forward=i>=index;index=Math.max(0,Math.min(STEPS.length-1,i));const step=STEPS[index];target=target_(step);
  if(step.at&&!target&&index>0&&index<STEPS.length-1)return show(forward?index+1:index-1);   // a part that is hidden right now (e.g. a folded panel) is skipped
  box.replaceChildren();
  const count=el('div','tour-count',`${index+1} / ${STEPS.length}`);const h=el('h3','',step.title);const p=el('p','',step.text);
  const actions=el('div','tour-actions');const skip=el('button','quiet',index===STEPS.length-1?'Close':'Skip the tour');skip.type='button';
  const back=el('button','','Back');back.type='button';back.disabled=index===0;const next=el('button','primary',index===STEPS.length-1?'Let’s go':'Next');next.type='button';
  skip.onclick=end;back.onclick=()=>show(index-1);next.onclick=()=>index===STEPS.length-1?end():show(index+1);
  actions.append(skip,back,next);box.append(count,h,p,actions);
  if(target)target.scrollIntoView({block:'nearest',inline:'nearest'});
  requestAnimationFrame(place);next.focus();}
function start(){
  end(true);index=0;shade=el('div','tour-shade');ring=el('div','tour-ring');box=el('div','tour-box');box.setAttribute('role','dialog');box.setAttribute('aria-label','Asset Lab tour');
  document.body.append(shade,ring,box);show(0);addEventListener('resize',place);addEventListener('keydown',keys,true);}
function end(quiet){
  for(const n of [shade,ring,box])n?.remove();shade=ring=box=target=null;removeEventListener('resize',place);removeEventListener('keydown',keys,true);
  if(quiet!==true)try{localStorage.setItem(KEY,'1');}catch(e){}}
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
