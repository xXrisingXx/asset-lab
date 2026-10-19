'use strict';
// Checkboxes on every asset card for mass selection, plus favorites.
// A selection can be starred, removed from the pool in one go, or handed to Theme AI so only the checked art changes.
// Uses app.js globals: $, api, message, library, run, confirmAction, S.
(()=>{
const picked=new Set(JSON.parse(sessionStorage.getItem('lab-picked')||'[]'));
const names=new Map();let favorites=new Set(),anchor=null;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const list=$('assetList');
const bar=el('div','select-bar');bar.id='selectionBar';
const page=el('button','link','Select page');page.id='selectPage';page.title='Check every asset on this page. Shift-click a checkbox to check a range.';
const count=el('span','select-count');count.id='selectedCount';
const star=el('button','', '★ Favorite');star.id='favoriteSelected';
const remove=el('button','', 'Remove');remove.id='removeSelected';remove.title='Remove the checked assets from the pool';
const clear=el('button','link','Clear');clear.id='clearSelected';
bar.append(page,count,star,remove,clear);document.querySelector('.count-row').after(bar);
const option=el('option','', '★ Favorites');option.value='favorites';$('group').options[0].after(option);

function changed(){sessionStorage.setItem('lab-picked',JSON.stringify([...picked]));const n=picked.size;
  count.textContent=n?`${n.toLocaleString()} checked`:'';for(const b of [star,remove,clear])b.hidden=!n;
  star.textContent=n&&[...picked].every(k=>favorites.has(k))?'☆ Unfavorite':'★ Favorite';
  for(const card of list.querySelectorAll('.asset-card')){const on=picked.has(card.dataset.key);card.classList.toggle('picked',on);card.querySelector('.pick')?.setAttribute('aria-checked',on);}
  document.dispatchEvent(new CustomEvent('lab-selection',{detail:{count:n}}));}
function decorate(){
  // Checked assets come first on the page (only when a page loads, so cards don't jump while you check them).
  const all=[...list.querySelectorAll('.asset-card')],first=all.filter(c=>picked.has(c.dataset.key));
  if(first.length&&first.length<all.length&&first.some((c,i)=>all[i]!==c)){observer.disconnect();list.prepend(...first);observer.observe(list,{childList:true});}
  for(const card of list.querySelectorAll('.asset-card')){
    names.set(card.dataset.key,card.querySelector('.asset-label')?.textContent||'');
    if(!card.querySelector('.pick')){const box=el('span','pick');box.setAttribute('role','checkbox');box.title='Check to include in a mass action or a Theme AI request';card.prepend(box);
      const fav=el('span','fav','★');fav.setAttribute('role','button');fav.title='Favorite';card.append(fav);}
    card.classList.toggle('favorite',favorites.has(card.dataset.key));card.classList.toggle('picked',picked.has(card.dataset.key));
    card.querySelector('.pick').setAttribute('aria-checked',picked.has(card.dataset.key));
  }}
const observer=new MutationObserver(decorate);observer.observe(list,{childList:true});
// Double-click a card to check or uncheck it.
list.addEventListener('dblclick',e=>{const card=e.target.closest('.asset-card');if(!card||e.target.closest('.pick,.fav'))return;
  e.preventDefault();const key=card.dataset.key;picked.has(key)?picked.delete(key):picked.add(key);anchor=key;changed();});
// Capture phase: the card itself is a button that opens the asset, so its own click must not fire for these two controls.
list.addEventListener('click',run(async e=>{
  const box=e.target.closest('.pick'),fav=e.target.closest('.fav');if(!box&&!fav)return;
  e.stopPropagation();e.preventDefault();const card=e.target.closest('.asset-card'),key=card.dataset.key;
  if(fav){await mark([key],favorites.has(key)?'unfavorite':'favorite');return;}
  const cards=[...list.querySelectorAll('.asset-card')],on=!picked.has(key);
  const from=anchor?cards.findIndex(c=>c.dataset.key===anchor):-1,to=cards.indexOf(card);
  const span=e.shiftKey&&from>=0?cards.slice(Math.min(from,to),Math.max(from,to)+1):[card];
  for(const c of span)on?picked.add(c.dataset.key):picked.delete(c.dataset.key);
  anchor=key;changed();}),true);
async function loadMarks(){favorites=new Set((await api('/api/marks')).favorites);decorate();changed();}
async function mark(keys,action){
  const r=await api('/api/mark',{keys,action});
  if(action==='remove'||action==='restore'){for(const k of keys)picked.delete(k);}
  await loadMarks();
  const what=keys.length===1?(names.get(keys[0])||'Asset'):`${keys.length} assets`;
  message({favorite:`${what} added to Favorites.`,unfavorite:`${what} removed from Favorites.`,remove:`${what} removed from the pool (${r.records} records hidden).`,restore:`${what} back in the pool.`}[action]);
  if(action!=='favorite'||$('group').value==='favorites')await library();
  if(action==='remove'||action==='restore')document.dispatchEvent(new Event('lab-pool'));
  if(S.asset&&keys.includes(S.asset.key)){if(action.includes('favorite'))S.asset.favorite=action==='favorite';else S.asset.removed=action==='remove';}}
page.onclick=()=>{const cards=[...list.querySelectorAll('.asset-card')],all=cards.every(c=>picked.has(c.dataset.key));for(const c of cards)all?picked.delete(c.dataset.key):picked.add(c.dataset.key);changed();page.textContent=all?'Select page':'Unselect page';};
new MutationObserver(()=>{page.textContent='Select page';}).observe(list,{childList:true});
clear.onclick=()=>{picked.clear();anchor=null;changed();};
star.onclick=run(()=>mark([...picked],[...picked].every(k=>favorites.has(k))?'unfavorite':'favorite'));
remove.onclick=run(async()=>{const showing=$('showRemoved').checked;
  if(!showing&&!await confirmAction(`Remove ${picked.size} assets from the pool?`,'They disappear from the library and from themes, together with their identical copies. Nothing is deleted; tick “Removed” to bring them back.','Remove from pool'))return;
  await mark([...picked],showing?'restore':'remove');});
$('showRemoved').addEventListener('change',()=>{remove.textContent=$('showRemoved').checked?'Put back':'Remove';});
// Right-click: favorite the asset under the cursor.
const menu=$('contextMenu'),item=el('button','', 'Add to favorites');item.dataset.action='favorite-toggle';menu.append(item);
new MutationObserver(()=>{if(!menu.hidden&&S.asset)item.textContent=favorites.has(S.asset.key)?'Remove from favorites':'Add to favorites';}).observe(menu,{attributes:true,attributeFilter:['hidden']});
menu.addEventListener('click',run(async e=>{if(e.target.dataset.action==='favorite-toggle'&&S.asset)await mark([S.asset.key],favorites.has(S.asset.key)?'unfavorite':'favorite');}));
window.LabSelection={keys:()=>[...picked],size:()=>picked.size,clear:()=>{picked.clear();changed();},
  has:key=>picked.has(key),
  // Simple's cards: one card is every copy of a picture, checked or unchecked together.
  set:(keys,on)=>{for(const k of keys)on?picked.add(k):picked.delete(k);changed();}};
run(loadMarks)();
})();
