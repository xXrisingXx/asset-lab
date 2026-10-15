'use strict';
// Three small additions to the editor:
//  1. A banner when the lab's code is newer than the running server, with a one-click restart.
//  2. "Find similar": rank every sprite by how much it looks like an asset or a dropped/pasted image.
//  3. Your own names for assets ("Speed potion"), shown on cards and understood by search and Theme AI.
// Uses app.js globals: $, api, message, library, run, select, S.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const t=text=>window.LabI18n?LabI18n.t(text):text;   // native dialogs are not in the page, so they are translated here

// ----- 1. stale server ------------------------------------------------------------------------
const banner=el('div','update-banner');banner.hidden=true;
const bannerText=el('span','', 'The lab was updated after it started, so some features are not loaded yet.');
const restart=el('button','primary','Restart lab now');banner.append(bannerText,restart);document.body.prepend(banner);
async function checkCode(){try{const r=await fetch('/api/code-stamp').then(x=>x.json());banner.hidden=!r.stale;}catch{}}
restart.onclick=run(async()=>{
  if(S.dirty&&!confirm(t('Restart the lab? Your unsaved draft in the editor will be lost; saved edits are kept.')))return;
  const before=(await fetch('/api/code-stamp').then(x=>x.json())).pid;
  restart.disabled=true;bannerText.textContent='Restarting… the page reloads when the new server answers.';
  await api('/api/restart',{});
  for(let i=0;i<120;i++){await new Promise(r=>setTimeout(r,500));try{const r=await fetch('/api/code-stamp').then(x=>x.json());if(r.pid!==before){location.reload();return;}}catch{}}
  bannerText.textContent='The lab did not come back. Reopen it from the Asset Lab shortcut.';restart.disabled=false;});
checkCode();setInterval(checkCode,30000);

// ----- 2. find similar ------------------------------------------------------------------------
const chip=el('div','similar-chip');chip.hidden=true;const chipText=el('span');const chipClear=el('button','link','Show all ×');chip.append(chipText,chipClear);
const finder=el('div','image-search');const pick=el('button','', 'Search by image…');pick.title='Choose, paste (Ctrl+V) or drop an image to find sprites that look like it';
const file=el('input');file.type='file';file.accept='image/*';file.hidden=true;finder.append(pick,el('small','hint','or paste / drop an image here'),file);
$('search').after(finder);document.querySelector('.count-row').before(chip);
async function like(token,label){window.LabSimilar=token;chipText.textContent='Most alike: '+label;chip.hidden=false;S.offset=0;S.parent='';await library();}
chipClear.onclick=run(async()=>{window.LabSimilar='';chip.hidden=true;S.offset=0;await library();});
async function upload(blob){
  if(!blob||!blob.type.startsWith('image/'))return message('That is not an image.',true);
  const data=await new Promise((ok,fail)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=fail;r.readAsDataURL(blob);});
  const {id}=await api('/api/similar-image',{image:data});await like('upload:'+id,blob.name||'pasted image');}
pick.onclick=()=>file.click();file.onchange=run(async()=>{await upload(file.files[0]);file.value='';});
document.addEventListener('paste',run(async e=>{if(e.target.closest('textarea'))return;const item=[...(e.clipboardData?.items||[])].find(i=>i.type.startsWith('image/'));if(!item)return;e.preventDefault();await upload(item.getAsFile());}));
const aside=document.querySelector('.library');
aside.addEventListener('dragover',e=>{if([...e.dataTransfer.items].some(i=>i.kind==='file')){e.preventDefault();aside.classList.add('dropping');}});
aside.addEventListener('dragleave',e=>{if(!aside.contains(e.relatedTarget))aside.classList.remove('dropping');});
aside.addEventListener('drop',run(async e=>{aside.classList.remove('dropping');const f=e.dataTransfer.files[0];if(!f)return;e.preventDefault();await upload(f);}));

// ----- 3. names ---------------------------------------------------------------------------------
async function nameThese(keys,current=''){
  if(!keys.length)return;
  const text=prompt(t(keys.length===1?'Name this asset (leave empty to remove the name):':`Name all ${keys.length} checked assets (leave empty to remove their names):`),current);
  if(text===null)return;
  const r=await api('/api/label',{keys,name:text});
  message(r.label?`Named ${keys.length===1?'it':keys.length+' assets'} “${r.label}”. Search and Theme AI understand the name now.`:'Name removed.');
  await library();if(S.asset&&keys.includes(S.asset.key))S.asset.label=r.label;}
const bar=$('selectionBar');if(bar){const b=el('button','', 'Name…');b.id='nameSelected';b.title='Give the checked assets one name, for example all versions of one potion';$('clearSelected').before(b);
  b.onclick=run(()=>nameThese(window.LabSelection.keys()));
  const sync=()=>{b.hidden=!window.LabSelection?.size();};document.addEventListener('lab-selection',sync);sync();}

// ----- right-click entries -----------------------------------------------------------------------
const menu=$('contextMenu');
const findItem=el('button','', 'Find similar images');findItem.dataset.action='find-similar';
const nameItem=el('button','', 'Name…');nameItem.dataset.action='name-asset';menu.append(findItem,nameItem);
new MutationObserver(()=>{if(!menu.hidden&&S.asset)nameItem.textContent=S.asset.label?`Rename “${S.asset.label}”…`:'Name…';}).observe(menu,{attributes:true,attributeFilter:['hidden']});
menu.addEventListener('click',run(async e=>{const a=e.target.dataset.action;if(!S.asset)return;
  if(a==='find-similar')await like('key:'+S.asset.key,S.asset.label||S.asset.name);
  if(a==='name-asset')await nameThese([S.asset.key],S.asset.label||'');}));
})();
