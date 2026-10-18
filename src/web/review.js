'use strict';
// Review: everything the next install puts in Mitosis, before → after, with Remove / Put back on every item.
// Uses app.js globals: $, api, message, status, library, select, run, build, buildText, S.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const dlg=el('dialog','review');dlg.id='reviewDialog';dlg.setAttribute('aria-label','Review your mod');document.body.append(dlg);
// mode: 'review' (Review), 'build' (Build to Mitosis: what goes in, then Apply build) or 'restore' (Restore original:
// what comes out of the game, then Restore original).
const R={data:null,out:new Set(),colorsOut:{},hideOut:new Set(),changed:false,mode:'review'};

// The revision is in the address: the browser keeps pictures it already loaded by address, so a new edit showed the old one.
function thumb(key,original,alt,rev){const im=new Image();im.loading='lazy';im.decoding='async';im.alt=alt;im.src=`/api/thumb?key=${encodeURIComponent(key)}${original?'&original=1':`&v=${encodeURIComponent(rev??Date.now())}`}&big=1`;return im;}
// A fully transparent "after" is art hidden on purpose ("hide the discord button"): say so instead of showing nothing.
function markHidden(im,pair){im.addEventListener('load',()=>{try{const c=document.createElement('canvas');c.width=c.height=24;const x=c.getContext('2d');x.drawImage(im,0,0,24,24);
  const a=x.getImageData(0,0,24,24).data;for(let i=3;i<a.length;i+=4)if(a[i])return;pair.classList.add('hidden-art');pair.append(el('span','review-hidden','Hidden in the game'));}catch(e){}});}
function toggle(label,out,onRemove,onBack){
  if(R.mode==='restore')return el('span');   // everything installed comes out: nothing to pick
  const b=el('button','review-toggle',out?'Put back':'Remove');b.type='button';b.title=out?'Include this in the mod again':'Leave this out of the mod';
  b.onclick=run(async()=>{b.disabled=true;try{await (out?onBack():onRemove());R.changed=true;render();}finally{b.disabled=false;}});return b;}
function swatch(color){const s=el('span','review-swatch');s.style.backgroundColor=color;s.title=color;return s;}

async function open(mode){R.mode=['build','restore'].includes(mode)?mode:'review';R.out=new Set();R.colorsOut={};R.hideOut=new Set();R.changed=false;R.data=await api('/api/mod');render();dlg.showModal();}
async function savePalette(){const colors={...R.data.palette};for(const f of Object.keys(R.colorsOut))delete colors[f];await api('/api/palette',{colors});}
async function saveHide(){await api('/api/hide',{groups:R.data.hide.filter(h=>!R.hideOut.has(h))});}

// Everything out of the mod at once (nothing is deleted), or all of it back in.
async function setAll(on){const d=R.data,keys=d.edits.map(e=>e.key);
  if(keys.length)await api('/api/stage-many',{keys,enabled:on});keys.forEach(k=>on?R.out.delete(k):R.out.add(k));
  if(Object.keys(d.palette).length){R.colorsOut=on?{}:{...d.palette};await savePalette();}
  if(d.hide.length){R.hideOut=on?new Set():new Set(d.hide);await saveHide();}
  for(const a of d.audio||[])if(!!a.staged!==on){await api('/api/audio-stage',{key:a.key,enabled:on});a.staged=on;}
  R.changed=true;render();}
function render(){
  // Rebuilt after every Remove / Put back: keep the place in the list.
  const scrolled=dlg.querySelector('.review-body')?.scrollTop||0,page=dlg.scrollTop;
  const d=R.data;dlg.replaceChildren();
  const edits=d.edits.filter(e=>!R.out.has(e.key)).length,colors=Object.keys(d.palette).filter(f=>!(f in R.colorsOut)).length,hidden=d.hide.filter(h=>!R.hideOut.has(h)).length;
  const sounds=(d.audio||[]).filter(a=>a.staged).length;
  const head=el('div','review-head');const h=el('h2','',{build:'Build to Mitosis',restore:'Restore original'}[R.mode]||'Review your mod');const x=el('button','quiet','✕');x.type='button';x.onclick=()=>dlg.close();
  const total=d.edits.length+Object.keys(d.palette).length+d.hide.length+(d.audio||[]).length,anyIn=edits||colors||hidden||sounds;
  const all=el('button','review-all',anyIn?'Remove all':'Put all back');all.type='button';all.id='reviewAll';all.hidden=!total;
  all.title=anyIn?'Leave everything out of the mod (your work stays saved)':'Include everything in the mod again';
  all.onclick=run(async()=>{all.disabled=true;try{await setAll(!anyIn);}finally{all.disabled=false;}});
  if(R.mode==='restore')all.hidden=true;
  head.append(h,all,x);
  const summary=[`${edits} edit${edits===1?'':'s'}`,colors&&`${colors} game color${colors===1?'':'s'}`,hidden&&`${hidden} hidden menu control${hidden===1?'':'s'}`,sounds&&`${sounds} sound${sounds===1?'':'s'}`].filter(Boolean).join(' · ');
  const sub=el('p','review-sub',(d.installed?'Installed in Mitosis now. Items marked New are not in the game yet; Build to Mitosis puts the list below in. ':'Nothing is installed yet. ')+`This mod: ${summary}. Remove leaves an item out without deleting your work; Put back includes it again.`);
  if(R.mode==='build')sub.textContent='This is what goes into Mitosis. Leave anything out with Remove, then click Apply build. '+sub.textContent;
  if(R.mode==='restore')sub.textContent='Restoring takes your mod out of Mitosis and puts every game file back exactly as it was. Your saved work stays in Asset Lab.';
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
      const pair=el('button','review-pair big');pair.type='button';pair.title='Open '+e.name+' in the editor';const after=thumb(e.key,false,'after',e.revision);markHidden(after,pair);pair.append(thumb(e.key,true,'before'),el('span','review-arrow','→'),after);
      pair.onclick=run(async()=>{dlg.close();await select(e.key);});
      const text=el('div','review-text');text.append(el('b','',e.name),el('small','',`${e.size[0]} × ${e.size[1]}${e.sheet?' · whole sheet':''} · ${e.source}`));
      if(d.installed&&!e.installed)text.firstChild.append(el('span','review-new','New'));
      card.append(pair,text,toggle(e.name,out,()=>api('/api/stage',{key:e.key,enabled:false}).then(()=>R.out.add(e.key)),()=>api('/api/stage',{key:e.key,enabled:true}).then(()=>R.out.delete(e.key))));grid.append(card);}
    if(shown.length<g.items.length){const more=el('button','link',`Show all ${g.items.length}`);more.type='button';more.onclick=()=>{g.all=true;render();};sec.append(more);}
  }
  // Fonts: the game's text drawn in another font. Changed or taken out in the Fonts panel.
  if((d.fonts||[]).length){const sec=section(body,`Fonts · ${d.fonts.length}`);
    for(const f of d.fonts){const row=el('div','review-row');const t=el('div','review-text');t.append(el('b','',f.label),el('small','',`Drawn in ${f.face||'a new style'}`));
      if(d.installed&&!f.installed)t.firstChild.append(el('span','review-new','New'));
      const change=el('button','','Change…');change.type='button';change.onclick=()=>{dlg.close();window.openFonts&&window.openFonts(f.id);};row.append(t,change);sec.append(row);}}
  soundSection(d,body);
  if(!fields.length&&!d.hide.length&&!d.edits.length&&!(d.fonts||[]).length&&!(d.audio||[]).length)body.append(el('p','hint',d.installed?'Everything is out of the mod. Build to Mitosis now puts the original files back.':'Your mod is empty. Save an edit, apply a theme, or set game colors first.'));

  // Footer: file check and install.
  const foot=el('div','review-foot');const files=el('details','review-files');files.append(el('summary','', 'Check the game files'));const pre=el('pre');files.append(pre);
  files.ontoggle=run(async()=>{if(!files.open||pre.textContent)return;pre.textContent='Building…';try{const b=await build();pre.textContent=buildText(b)||'No game files change.';}catch(e){pre.textContent=e.message;}});
  const close=el('button','', 'Close');close.type='button';close.onclick=()=>dlg.close();
  const go=el('button','primary',{build:'Apply build',restore:'Restore original'}[R.mode]||'Build to Mitosis');go.type='button';go.id='reviewGo';
  go.disabled=R.mode==='restore'?!d.installed:R.mode==='build'?!d.installed&&!(edits||colors||hidden||sounds):false;
  const target=R.mode==='restore'?'restoreButton':'installButton',now=R.mode!=='review';
  // Apply build / Restore original go ahead (through the usual checks); Build to Mitosis from Review shows the build first.
  go.onclick=()=>{dlg.close();setTimeout(()=>now&&window.LabThemeChat?.proceed?LabThemeChat.proceed(target):$(target).click(),0);};
  foot.append(files,close,go);dlg.append(foot);
  body.scrollTop=scrolled;dlg.scrollTop=page;
}
function section(body,title,action){const sec=el('section','review-section');const h=el('div','review-section-head');h.append(el('span','section-label',title));if(action)h.append(action);sec.append(h);body.append(sec);return sec;}

// ----- sounds: game music and effects replaced in the sound editor (audio.js), with before / after play buttons ------
const ear=new Audio();let earButton=null;
const clock=s=>{s=Math.max(0,+s||0);const m=Math.floor(s/60),d=s<1?2:1;return `${m}:${(s-m*60).toFixed(d).padStart(d+3,'0')}`;};   // as audio.js
function listen(url,label,what){
  const b=el('button','review-listen','▶ '+label);b.type='button';b.title='Play '+what;
  const reset=()=>{b.textContent='▶ '+label;if(earButton===b)earButton=null;};
  b.onclick=()=>{if(earButton===b){ear.pause();reset();return;}if(earButton)earButton.dispatchEvent(new Event('stopped'));
    ear.src=url;ear.onended=reset;earButton=b;b.textContent='■ '+label;ear.play().catch(e=>{reset();message(e.message,true);});};
  b.addEventListener('stopped',reset);return b;}
function soundSection(d,body){
  const sounds=d.audio||[];if(!sounds.length)return;
  const sec=section(body,`Sounds · ${sounds.length}`);
  for(const a of sounds){const out=!a.staged;const row=el('div','review-row review-sound'+(out?' out':''));const pair=el('div','review-pair');
    pair.append(listen('/api/raw?key='+encodeURIComponent(a.key),'Before','the game’s sound'),el('span','review-arrow','→'),listen(`/api/audio-file?key=${encodeURIComponent(a.key)}&v=${a.version}`,'After','your sound'));
    const text=el('div','review-text');text.append(el('b','',a.name),el('small','',[a.label,a.duration?clock(a.duration):''].filter(Boolean).join(' · ')));
    if(d.installed&&a.staged&&!a.installed)text.firstChild.append(el('span','review-new','New'));
    row.append(pair,text,toggle(a.name,out,async()=>{await api('/api/audio-stage',{key:a.key,enabled:false});a.staged=false;},async()=>{await api('/api/audio-stage',{key:a.key,enabled:true});a.staged=true;}));
    sec.append(row);}}
dlg.addEventListener('close',()=>{ear.pause();if(earButton)earButton.dispatchEvent(new Event('stopped'));});

dlg.addEventListener('close',run(async()=>{if(!R.changed)return;S.build=null;await status();await library();}));
$('reviewButton').onclick=run(()=>open('review'));
window.LabReview={open:run(open)};
})();
