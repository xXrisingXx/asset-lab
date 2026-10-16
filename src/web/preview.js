'use strict';
// Game preview: the main menu and a match, rebuilt by the lab from the game's own sprites with your saved edits, so a
// change can be checked without restarting Mitosis. It refreshes by itself while it is open.
// Uses app.js globals: $, run.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const dlg=el('dialog','game-preview');dlg.id='previewDialog';dlg.setAttribute('aria-label','Game preview');document.body.append(dlg);
// The match view also shows the game's light or dark mode and the PC or phone controls (remembered between visits).
let kept={};try{kept=JSON.parse(localStorage.getItem('lab-preview')||'{}')||{};}catch(e){}
const P={screen:'menu',timer:0,stamp:'',mode:kept.mode==='dark'?'dark':'light',controls:kept.controls==='phone'?'phone':'pc'};
const head=el('div','game-preview-head');const title=el('h2','','Game preview');const tabs=el('div','segmented game-preview-tabs');
for(const [id,label] of [['menu','Main menu'],['match','In a match']]){const b=el('button','',label);b.type='button';b.dataset.screen=id;b.onclick=()=>{P.screen=id;show(true);};tabs.append(b);}
const options=el('div','game-preview-options');
function choice(key,pairs,hint){const g=el('div','segmented');g.title=hint;for(const [id,label] of pairs){const b=el('button','',label);b.type='button';b.dataset[key]=id;
  b.onclick=()=>{P[key]=id;try{localStorage.setItem('lab-preview',JSON.stringify({mode:P.mode,controls:P.controls}));}catch(e){}show(true);};g.append(b);}options.append(g);return g;}
const modes=choice('mode',[['light','Light mode'],['dark','Dark mode']],'The game’s light or dark mode: the arena and the virus change');
const controls=choice('controls',[['pc','PC'],['phone','Phone']],'Split and eject buttons: the guided ones on PC, the touch ones on phones');
const close=el('button','quiet','✕');close.type='button';close.title='Close';close.onclick=()=>dlg.close();head.append(title,tabs,options,close);
const img=el('img','game-preview-image');img.alt='Preview of the game with your edits';
const note=el('p','hint','Your saved edits, drawn the way the game places and colors them. A close preview: menu text uses a stand-in font, and animated or server-loaded parts are left out. In a match, the split and eject spots and the dark arena are close stand-ins. Open Mitosis for the final check.');
dlg.append(head,img,note);
// A new picture only when something changed: the mod list and the saved revisions are the fingerprint.
const stamp=()=>JSON.stringify([S.status?.staged||[],S.asset?.revision||'']);
function show(force){
  tabs.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.screen===P.screen));
  options.hidden=P.screen!=='match';
  modes.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.mode===P.mode));
  controls.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.controls===P.controls));
  const now=stamp();if(!force&&now===P.stamp)return;P.stamp=now;
  // Sized from the dialog: before its first picture loads the image itself has no width yet.
  const w=Math.min(1600,Math.max(640,Math.round((dlg.clientWidth||1280)*(devicePixelRatio||1))));
  img.src=`/api/preview?screen=${P.screen}&w=${w}&mode=${P.mode}&controls=${P.controls}&t=${Date.now()}`;}
function open(){dlg.showModal();P.stamp='';show(true);clearInterval(P.timer);P.timer=setInterval(()=>{if(dlg.open)show(false);},1500);}
dlg.addEventListener('close',()=>clearInterval(P.timer));
const button=el('button','','Game preview');button.id='previewButton';button.title='See your edits the way the game shows them, without opening Mitosis';button.onclick=open;
$('launchButton').before(button);
window.LabPreview={open,refresh:()=>show(true)};
})();
