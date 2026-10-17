'use strict';
// Simple and Advanced. Simple is the library and Theme AI only, for players who want a new look without an image
// editor: click pictures to pick what changes, describe it, install. Advanced is the full editor. New players (no tour
// seen yet) start in Simple and everyone else keeps Advanced; the choice is remembered in this browser.
// Uses app.js globals: $.
(()=>{
const KEY='lab-view';
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
let view='advanced';
try{view=localStorage.getItem(KEY)||(localStorage.getItem('lab-tour-done')?'advanced':'simple');localStorage.setItem(KEY,view);}catch(e){}
if(view!=='simple')view='advanced';
const seg=el('div','segmented view-switch');seg.id='viewSwitch';seg.setAttribute('role','group');seg.setAttribute('aria-label','Layout');
for(const [id,label,tip] of [['simple','Simple','Pick what to change and describe it: Theme AI does the rest'],['advanced','Advanced','The full image editor: layers, brushes, filters and more']]){
  const b=el('button','',label);b.type='button';b.dataset.view=id;b.title=tip;b.onclick=()=>use(id,true);seg.append(b);}
document.querySelector('.topbar').insertBefore(seg,$('themeButton')||$('launchButton'));
const hint=el('p','simple-hint','Click pictures to choose what to change (click again to unpick), or pick nothing to change the whole game.');
document.querySelector('.library h1')?.after(hint);
function openAI(){const d=$('themeDrawer');if(view==='simple'&&d&&d.hidden)$('themeButton')?.click();}
function use(next,save){
  view=next==='simple'?'simple':'advanced';document.body.classList.toggle('lab-simple',view==='simple');
  seg.querySelectorAll('button').forEach(b=>{const on=b.dataset.view===view;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});
  if(save)try{localStorage.setItem(KEY,view);}catch(e){}
  openAI();dispatchEvent(new Event('resize'));}
// In Simple, Theme AI is the page, so it stays open; and a click on a picture picks it (for Theme AI) instead of
// opening it in the editor, which Simple does not show.
new MutationObserver(()=>setTimeout(openAI,0)).observe($('themeDrawer'),{attributes:true,attributeFilter:['hidden']});
const list=$('assetList');
list.addEventListener('click',e=>{if(view!=='simple')return;const card=e.target.closest('.asset-card');if(!card||e.target.closest('.pick,.fav'))return;
  e.preventDefault();e.stopImmediatePropagation();card.querySelector('.pick')?.click();},true);
list.addEventListener('dblclick',e=>{if(view==='simple'&&e.target.closest('.asset-card')){e.preventDefault();e.stopImmediatePropagation();}},true);
use(view,false);
window.LabView={get:()=>view,set:v=>use(v,true)};
})();
