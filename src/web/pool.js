'use strict';
// Right-click > Remove from pool: hide art you never want to see or theme (for example Worm.is and Strike.is leftovers).
// Removals are remembered in removed-assets.json beside the workspace. Uses app.js globals: $, api, message, library, run, S.
(()=>{
const menu=$('contextMenu');
const item=(action,text)=>{const b=document.createElement('button');b.dataset.action=action;b.textContent=text;menu.append(b);return b;};
const remove=item('pool-remove','Remove from pool'),removeSheet=item('pool-remove-sheet','Remove its whole sheet'),restore=item('pool-restore','Put back in pool');
// The menu opens after the right-clicked card has been selected, so decide the entries at that moment.
new MutationObserver(()=>{if(menu.hidden)return;const a=S.asset,gone=!!a?.removed;remove.hidden=!a||gone;restore.hidden=!a||!gone;
  removeSheet.hidden=!a||gone||(!a.parent&&!a.sprite_count);removeSheet.textContent=a?.parent?'Remove its whole sheet':'Remove sheet and all its sprites';
  menu.style.top=Math.min(parseFloat(menu.style.top)||0,innerHeight-menu.offsetHeight-8)+'px';}).observe(menu,{attributes:true,attributeFilter:['hidden']});
async function count(){const p=await api('/api/pool');$('removedCount').textContent=p.total?` (${p.hidden.toLocaleString()})`:'';$('showRemoved').parentElement.title=p.total?`${p.total} removal(s) hide ${p.hidden} records. Saved in ${p.file}`:'Nothing has been removed. Right-click an asset to remove it.';}
menu.addEventListener('click',run(async e=>{const action=e.target.dataset.action;if(!action?.startsWith('pool-')||!S.asset)return;
  const key=S.asset.key,name=S.asset.name;
  if(action==='pool-restore'){const r=await api('/api/pool-restore',{key});S.asset.removed=false;message(`${name} is back in the pool.`+(r.restored.length>1?' Its sheet was put back with it.':''));}
  else{const r=await api('/api/pool-remove',{key,sheet:action==='pool-remove-sheet'});S.asset.removed=true;message(`Removed ${r.name} from the pool (${r.records} record${r.records===1?'':'s'} hidden). Tick “Removed” to see or restore it.`);}
  await library();await count();}));
document.addEventListener('lab-pool',run(count));  // a mass removal from the selection bar
run(count)();
})();
