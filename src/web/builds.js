'use strict';
// Builds: separate versions of your mod to switch between (each with its own edits, sounds, fonts, game colors and
// Theme AI chats), Start over, and Revert to original for the open asset or the checked ones.
// Uses app.js globals: $, api, message, status, library, select, run, confirmAction, S.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const B={list:null};

// ----- the picker in the top bar ------------------------------------------------------------------------------
const pick=el('button','build-pick');pick.id='buildButton';pick.type='button';pick.title='Your builds: switch, copy, rename or start over';
const pickName=el('b','', '…');pick.append(el('span','build-pick-label','Build'),pickName,el('span','build-pick-caret','▾'));
document.querySelector('.top-status').after(pick);

const dlg=el('dialog','builds');dlg.id='buildsDialog';dlg.setAttribute('aria-label','Your builds');document.body.append(dlg);
const head=el('div','builds-head');const x=el('button','quiet','✕');x.type='button';x.title='Close';x.onclick=()=>dlg.close();
head.append(el('h2','', 'Your builds'),x);
const intro=el('p','hint','Each build is its own version of your mod: edits, sounds, fonts, game colors and Theme AI chats. Switching changes what you edit here; the game keeps what is installed until you press Build to Mitosis.');
const list=el('div','builds-list');list.id='buildsList';
const foot=el('div','builds-foot');
const startOver=el('button','primary','Start over');startOver.id='startOverButton';startOver.type='button';startOver.title='A new, empty build from the game’s original art. Your current build stays in this list.';
const copy=el('button','', 'Copy this build');copy.id='copyBuildButton';copy.type='button';copy.title='Try something new without changing this build';
foot.append(startOver,copy);dlg.append(head,intro,list,foot);

function counts(b){return [`${b.edits} edit${b.edits===1?'':'s'}`,b.sounds&&`${b.sounds} sound${b.sounds===1?'':'s'}`,b.fonts&&`${b.fonts} font${b.fonts===1?'':'s'}`,
  b.colors&&`${b.colors} game color${b.colors===1?'':'s'}`,b.chats&&`${b.chats} chat${b.chats===1?'':'s'}`].filter(Boolean).join(' · ');}
function when(t){try{return new Date(t*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'});}catch(e){return '';}}
// A name you gave stays as you wrote it; the first build's default name is translated.
const own=(node,name)=>{node.translate=name==='My build';return node;};
async function load(){B.list=await api('/api/builds');const a=B.list.builds.find(b=>b.active);pickName.textContent=a?a.name:'';own(pickName,a?.name);
  pick.classList.toggle('installed',!!a?.installed);return B.list;}
function render(){
  list.replaceChildren();
  for(const b of B.list.builds){const row=el('div','build-row'+(b.active?' active':''));row.dataset.id=b.id;
    const text=el('div','build-text');const name=el('b');name.append(own(el('span','',b.name),b.name));text.append(name);
    if(b.active)name.append(el('span','build-tag open','Open'));
    if(b.installed)name.append(el('span','build-tag game','In the game'));
    text.append(el('small','',`${counts(b)} · ${when(b.updated)}`));
    const acts=el('div','build-actions');
    const btn=(label,title,fn,cls='')=>{const n=el('button',cls,label);n.type='button';n.title=title;n.onclick=run(fn);acts.append(n);return n;};
    if(!b.active)btn('Open','Switch to this build',()=>switchTo(b),'primary').dataset.act='open';
    btn('Rename','Give this build a new name',()=>rename(b)).dataset.act='rename';
    btn('Copy','A new build that starts as a copy of this one',()=>make('',b.id)).dataset.act='copy';
    if(!b.active)btn('Delete','Delete this build (the game is not touched)',()=>remove(b),'quiet').dataset.act='delete';
    row.append(text,acts);list.append(row);}}
async function open(){await load();render();dlg.showModal();}

// Switching rebuilds the whole page from the other build, so every panel shows it.
async function leaveDraft(){return !S.dirty||await confirmAction('Leave this draft?','Your latest unsaved changes will be discarded. Saved revisions stay in the build.','Discard draft');}
async function done(r,said){B.list=r;S.dirty=false;try{sessionStorage.setItem('lab-build-note',said);}catch(e){}location.reload();}
async function switchTo(b){if(!await leaveDraft())return;await done(await api('/api/build-switch',{id:b.id}),`Opened the build “${b.name}”.`);}
async function make(name,copyOf){if(!await leaveDraft())return;const r=await api('/api/build-new',{name,copy:copyOf||''});
  await done(r,copyOf?`Made a copy: “${r.builds.find(b=>b.active).name}”.`:`New build “${r.builds.find(b=>b.active).name}”: everything is the game’s original art.`);}
async function rename(b){const name=prompt(t('Name this build:'),b.name);if(name===null||!name.trim())return;B.list=await api('/api/build-rename',{id:b.id,name});await load();render();}
async function remove(b){if(!await confirmAction(`Delete “${b.name}”?`,'Its edits, sounds and Theme AI chats are removed from the list. The game and your other builds are not touched.','Delete build'))return;
  B.list=await api('/api/build-delete',{id:b.id});await load();render();}

startOver.onclick=run(async()=>{
  const a=B.list.builds.find(b=>b.active);
  const installed=!!S.status?.active;
  if(!await confirmAction('Start over?',`A new, empty build starts from the game’s original art. “${a.name}” stays in your builds, so you can switch back any time.`+
    (installed?' Your mod is still installed in Mitosis: Restore original puts the game back to normal too.':''),'Start over'))return;
  await make('','');});
copy.onclick=run(()=>make('',B.list.active));
pick.onclick=run(open);
run(load)();
try{const note=sessionStorage.getItem('lab-build-note');if(note){sessionStorage.removeItem('lab-build-note');setTimeout(()=>message(note),400);}}catch(e){}

// ----- Revert to original ----------------------------------------------------------------------------------------
async function revert(keys,title){
  if(!await confirmAction(title,'The game’s own art comes back, including identical copies and its spot on a sheet a theme changed (the rest of that theme stays). Your edits stay in the saved history, so you can bring them back.','Revert'))return;
  const r=await api('/api/revert',{keys});S.build=null;
  message(r.reverted?`Back to the original art (${r.assets} asset${r.assets===1?'':'s'} checked).`:'That was already the original art.');
  await status();await library();if(S.asset&&(keys.includes(S.asset.key)||r.reverted))await select(S.asset.key,true);}
const back=el('button','', 'Revert to original');back.id='revertButton';back.type='button';back.disabled=true;
back.title='Put the game’s original art back for this asset (history is kept)';$('recipeButton').after(back);
back.onclick=run(async()=>{if(!S.asset)return;if(!await leaveDraft())return;S.dirty=false;await revert([S.asset.key],`Revert “${S.asset.label||S.asset.name}” to the original?`);});
// Opening, saving and undoing all rewrite the Original / Saved edit badge, so it says when to check again.
const sync=()=>{back.disabled=!S.asset?.changed;};document.addEventListener('lab:asset',sync);
new MutationObserver(sync).observe($('dirtyBadge'),{childList:true,characterData:true,subtree:true});
// The older button only swaps the open layers for the original (then Save): say so.
const layers=$('resetOriginal');if(layers){layers.textContent='Replace layers with the original';}
const bar=$('selectionBar');if(bar){const b=el('button','', 'Revert…');b.id='revertSelected';b.type='button';b.title='Put the game’s original art back for every checked asset';$('clearSelected').before(b);
  b.onclick=run(async()=>{const keys=window.LabSelection.keys();await revert(keys,keys.length===1?'Revert this asset to the original?':`Revert ${keys.length} checked assets to the original?`);});
  const sync=()=>{b.hidden=!window.LabSelection?.size();};document.addEventListener('lab-selection',sync);sync();}
window.LabBuilds={open,load};
})();
