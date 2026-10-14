'use strict';
// Review: everything the next install puts in Mitosis, before → after, with Remove / Put back on every item.
// Uses app.js globals: $, api, message, status, library, select, run, build, buildText, S.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const dlg=el('dialog','review');dlg.id='reviewDialog';dlg.setAttribute('aria-label','Review your mod');document.body.append(dlg);
const R={data:null,out:new Set(),colorsOut:{},hideOut:new Set(),changed:false};

function thumb(key,original,alt){const im=new Image();im.loading='lazy';im.decoding='async';im.alt=alt;im.src=`/api/thumb?key=${encodeURIComponent(key)}${original?'&original=1':''}&big=1`;return im;}
function toggle(label,out,onRemove,onBack){
  const b=el('button','review-toggle',out?'Put back':'Remove');b.type='button';b.title=out?'Include this in the mod again':'Leave this out of the mod';
  b.onclick=run(async()=>{b.disabled=true;try{await (out?onBack():onRemove());R.changed=true;render();}finally{b.disabled=false;}});return b;}
function swatch(color){const s=el('span','review-swatch');s.style.backgroundColor=color;s.title=color;return s;}

async function open(){R.out=new Set();R.colorsOut={};R.hideOut=new Set();R.changed=false;R.data=await api('/api/mod');render();dlg.showModal();}
async function savePalette(){const colors={...R.data.palette};for(const f of Object.keys(R.colorsOut))delete colors[f];await api('/api/palette',{colors});}
async function saveHide(){await api('/api/hide',{groups:R.data.hide.filter(h=>!R.hideOut.has(h))});}

function render(){
  const d=R.data;dlg.replaceChildren();
  const edits=d.edits.filter(e=>!R.out.has(e.key)).length,colors=Object.keys(d.palette).filter(f=>!(f in R.colorsOut)).length,hidden=d.hide.filter(h=>!R.hideOut.has(h)).length;
  const head=el('div','review-head');const h=el('h2','', 'Review your mod');const x=el('button','quiet','✕');x.type='button';x.onclick=()=>dlg.close();head.append(h,x);
  const summary=[`${edits} edit${edits===1?'':'s'}`,colors&&`${colors} game color${colors===1?'':'s'}`,hidden&&`${hidden} hidden menu control${hidden===1?'':'s'}`].filter(Boolean).join(' · ');
  const sub=el('p','review-sub',(d.installed?'Installed in Mitosis now. Items marked New are not in the game yet; Update Mitosis puts the list below in. ':'Nothing is installed yet. ')+`This mod: ${summary}. Remove leaves an item out without deleting your work; Put back includes it again.`);
  const body=el('div','review-body');dlg.append(head,sub,body);

  // Game colors: the original next to the new one.
  const fields=[...Object.keys(d.palette)];
  if(fields.length){const sec=section(body,'Game colors · Mitosis.exe');
    for(const f of fields){const info=d.colors[f]||{name:f,was:'',what:''},out=f in R.colorsOut;const row=el('div','review-row'+(out?' out':''));
      const pair=el('div','review-pair');pair.append(swatch(info.was),el('span','review-arrow','→'),swatch(d.palette[f]));
      const text=el('div','review-text');text.append(el('b','',info.name),el('small','',`${info.what} · ${info.was} → ${d.palette[f]}`));
      if(d.installed&&d.installed.palette[f]!==d.palette[f])text.firstChild.append(el('span','review-new','New'));
      row.append(pair,text,toggle(info.name,out,async()=>{R.colorsOut[f]=d.palette[f];await savePalette();},async()=>{delete R.colorsOut[f];await savePalette();}));sec.append(row);}}

  // Hidden menu controls: the button that disappears.
  if(d.hide.length){const sec=section(body,'Hidden in the main menu · Mitosis.exe');
    for(const g of d.hide){const out=R.hideOut.has(g);const row=el('div','review-row'+(out?' out':''));const pair=el('div','review-pair');
      const keys=d.hide_sprites[g]||[];if(keys.length){const before=el('span','review-shot');for(const k of keys.slice(0,2))before.append(thumb(k,true,'before'));pair.append(before);}else pair.append(el('span','review-shot empty','banner'));
      pair.append(el('span','review-arrow','→'),el('span','review-shot empty','hidden'));
      const text=el('div','review-text');text.append(el('b','',d.hide_labels[g]||g),el('small','','The game skips creating it, so its link is gone too.'));
      if(d.installed&&!d.installed.hide.includes(g))text.firstChild.append(el('span','review-new','New'));
      row.append(pair,text,toggle(g,out,async()=>{R.hideOut.add(g);await saveHide();},async()=>{R.hideOut.delete(g);await saveHide();}));sec.append(row);}}

  // Art: one group per theme and one for hand edits, each item before → after.
  const groups=new Map();
  for(const e of d.edits){const id=e.theme_id||(e.redraw_id&&'redraw:'+e.redraw_id)||'hand';if(!groups.has(id))groups.set(id,{title:e.theme?`Theme · “${e.theme.slice(0,80)}”`:e.redraw?`Redraw · “${e.redraw.slice(0,80)}”`:'Your edits',items:[]});groups.get(id).items.push(e);}
  for(const [,g] of groups){
    const keys=g.items.map(e=>e.key),allOut=keys.every(k=>R.out.has(k));
    const sec=section(body,`${g.title} · ${g.items.length} item${g.items.length===1?'':'s'}`,keys.length>1&&toggle('group',allOut,async()=>{await api('/api/stage-many',{keys,enabled:false});keys.forEach(k=>R.out.add(k));},async()=>{await api('/api/stage-many',{keys,enabled:true});keys.forEach(k=>R.out.delete(k));}));
    const grid=el('div','review-grid');sec.append(grid);
    const shown=g.items.length>24&&!g.all?g.items.slice(0,24):g.items;
    for(const e of shown){const out=R.out.has(e.key);const card=el('div','review-card'+(out?' out':''));
      const pair=el('button','review-pair big');pair.type='button';pair.title='Open '+e.name+' in the editor';pair.append(thumb(e.key,true,'before'),el('span','review-arrow','→'),thumb(e.key,false,'after'));
      pair.onclick=run(async()=>{dlg.close();await select(e.key);});
      const text=el('div','review-text');text.append(el('b','',e.name),el('small','',`${e.size[0]} × ${e.size[1]}${e.sheet?' · whole sheet':''} · ${e.source}`));
      if(d.installed&&!e.installed)text.firstChild.append(el('span','review-new','New'));
      card.append(pair,text,toggle(e.name,out,()=>api('/api/stage',{key:e.key,enabled:false}).then(()=>R.out.add(e.key)),()=>api('/api/stage',{key:e.key,enabled:true}).then(()=>R.out.delete(e.key))));grid.append(card);}
    if(shown.length<g.items.length){const more=el('button','link',`Show all ${g.items.length}`);more.type='button';more.onclick=()=>{g.all=true;render();};sec.append(more);}
  }
  if(!fields.length&&!d.hide.length&&!d.edits.length)body.append(el('p','hint',d.installed?'Everything is out of the mod. Update Mitosis now puts the original files back.':'Your mod is empty. Save an edit, apply a theme, or set game colors first.'));

  // Footer: file check and install.
  const foot=el('div','review-foot');const files=el('details','review-files');files.append(el('summary','', 'Check the game files'));const pre=el('pre');files.append(pre);
  files.ontoggle=run(async()=>{if(!files.open||pre.textContent)return;pre.textContent='Building…';try{const b=await build();pre.textContent=buildText(b)||'No game files change.';}catch(e){pre.textContent=e.message;}});
  const close=el('button','', 'Close');close.type='button';close.onclick=()=>dlg.close();
  const go=el('button','primary',d.installed?'Update Mitosis':'Install in Mitosis');go.type='button';go.disabled=!d.installed&&!(edits||colors||hidden);
  go.onclick=()=>{dlg.close();setTimeout(()=>$('installButton').click(),0);};
  foot.append(files,close,go);dlg.append(foot);
}
function section(body,title,action){const sec=el('section','review-section');const h=el('div','review-section-head');h.append(el('span','section-label',title));if(action)h.append(action);sec.append(h);body.append(sec);return sec;}

dlg.addEventListener('close',run(async()=>{if(!R.changed)return;S.build=null;await status();await library();}));
$('reviewButton').onclick=run(open);
})();
