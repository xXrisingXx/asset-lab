'use strict';
// Settings: the gear at the right of the top bar (or Ctrl+,) opens the lab's settings, in categories, with a search across
// all of them. Every change is saved at once in settings.json (shared with the launcher; see src/settings.py) and applied
// right away where it can be: the look at once, editor settings through window.Ed (feature-detected), the rest the next
// time it is used. Other scripts read a value with LabSettings.get(key).
// This file runs in <head>, so the look remembered from the last visit (interface size, accent...) is on the page before
// it first draws. The window is built once the page has loaded; from then on it uses app.js's globals ($, api,
// confirmAction, draw, filter, library, select, S) and app.js's canvasBackground().
(()=>{
const html=document.documentElement,CACHE='lab-settings-look',LAST='lab-last-asset',REOPEN='lab-settings-reopen',LOOK=['ui_size','accent','reduce_motion','compact_cards'];
const store={get(k,d){try{const v=localStorage.getItem(k);return v==null?d:JSON.parse(v);}catch(e){return d;}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch(e){}}};
function look(v){
  const set=(name,on,value='')=>{if(on)html.setAttribute(name,value);else html.removeAttribute(name);};
  set('data-ui-size',!!v.ui_size&&+v.ui_size!==100,String(v.ui_size));set('data-accent',!!v.accent&&v.accent!=='teal',String(v.accent));
  set('data-reduce-motion',!!v.reduce_motion);set('data-compact-cards',!!v.compact_cards);}
const lookOf=v=>Object.fromEntries(LOOK.map(k=>[k,v[k]]));
look(store.get(CACHE,{})||{});

// What some choices look like here (the CSS in settings.css uses the same accent colors).
const ACCENT={teal:'#70e5d4',blue:'#6cb6ff',violet:'#b79cff',pink:'#ff8fcd',orange:'#ffb565',lime:'#b7e66a'};
const BACKDROP={dark:['#25313f','#2b3948'],light:['#ffffff','#d6dbe1'],teal:['#0d2829'],gray:['#808080']};
// Words that find the panels that are not settings.
const EXTRAS={storage:{usage:'disk space usage size workspace builds local ai runtime models link helper',privacy:'privacy internet connect connection network online offline github python pypi hugging face huggingface tracking telemetry statistics accounts'},
  about:{about:'about version update updates release releases licence license licenses notices third party credits creator created Novu folders paths files location data where'}};

const L={values:null,changed:[],schema:[],categories:[],about:null,privacy:[],note:'',file:'',damaged:false,old:false,category:'general',query:'',usage:null,usageAt:0,models:null,started:false};
let readyDone;const ready=new Promise(r=>{readyDone=r;});
function get(k){if(L.values&&k in L.values)return L.values[k];const s=typeof S==='object'&&S?S.settings:null;return s&&k in s?s[k]:undefined;}
window.LabSettings={get,ready,values:()=>({...(L.values||{})}),open:c=>openSettings(c),beforeGameChange,merge:res=>merge(res)};   // merge: a change made outside this window

const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(text,cls,title)=>{const b=el('button',cls||'',text);b.type='button';if(title)b.title=title;return b;};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const size=n=>n>=1e9?(n/1e9).toFixed(1)+' GB':n>=1e5?(n/1e6).toFixed(1)+' MB':Math.max(0,Math.round(n/1e3))+' KB';
const byId=id=>document.getElementById(id);
function svg(paths,px){const ns='http://www.w3.org/2000/svg',s=document.createElementNS(ns,'svg');for(const [k,v] of Object.entries({viewBox:'0 0 24 24',width:px,height:px,fill:'none',stroke:'currentColor','stroke-width':1.8,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))s.setAttribute(k,v);
  for(const d of paths){const p=document.createElementNS(ns,'path');p.setAttribute('d',d);s.append(p);}return s;}
// A gear (eight teeth around a ring) and a magnifier.
const GEAR=(()=>{let d='';const n=32,step=Math.PI*2/n;for(let i=0;i<n;i++){const a=i*step-Math.PI/2+step/2,r=i%4===1||i%4===2?9.6:7.3;d+=(i?'L':'M')+(12+r*Math.cos(a)).toFixed(2)+' '+(12+r*Math.sin(a)).toFixed(2);}return[d+'Z','M15 12a3 3 0 1 1-6 0a3 3 0 1 1 6 0Z'];})();
const LENS=['M10.5 17a6.5 6.5 0 1 1 0-13a6.5 6.5 0 0 1 0 13Z','M15.5 15.5L20 20'];

// ----- the window ----------------------------------------------------------------------------------------------------
let dlg,nav,panel,search,status,footNote,resetCat,resetAll,statusTimer,noteTimer;
function build(){
  const gear=btn('','settings-open','Settings (Ctrl+,)');gear.id='settingsButton';gear.setAttribute('aria-label','Settings');gear.setAttribute('aria-haspopup','dialog');gear.append(svg(GEAR,19));
  const bar=document.querySelector('.topbar');if(bar)bar.append(gear);gear.onclick=()=>openSettings();
  dlg=el('dialog','settings');dlg.id='settingsDialog';dlg.setAttribute('aria-labelledby','settingsTitle');
  const head=el('div','settings-head'),title=el('h2','','Settings');title.id='settingsTitle';
  const box=el('div','settings-search');search=el('input');search.type='search';search.id='settingsSearch';search.placeholder='Search settings';search.autocomplete='off';search.spellcheck=false;
  search.setAttribute('aria-label','Search settings');search.setAttribute('aria-controls','settingsPanel');box.append(svg(LENS,15),search);
  status=el('span','settings-status');status.id='settingsStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const close=btn('✕','quiet','Close (Esc)');close.id='settingsClose';close.setAttribute('aria-label','Close settings');close.onclick=()=>dlg.close();
  head.append(title,box,status,close);
  const body=el('div','settings-body');nav=el('div','settings-nav');nav.id='settingsNav';nav.setAttribute('role','tablist');nav.setAttribute('aria-orientation','vertical');nav.setAttribute('aria-label','Settings categories');
  panel=el('div','settings-panel');panel.id='settingsPanel';panel.setAttribute('role','tabpanel');panel.tabIndex=-1;body.append(nav,panel);
  const foot=el('div','settings-foot');footNote=el('p','settings-foot-note');footNote.id='settingsNote';footNote.setAttribute('aria-live','polite');
  resetCat=btn('Reset this category','');resetCat.id='settingsReset';resetAll=btn('Reset everything…','quiet');resetAll.id='settingsResetAll';
  const done=btn('Done','primary');done.id='settingsDone';done.onclick=()=>dlg.close();foot.append(footNote,resetCat,resetAll,done);
  dlg.append(head,body,foot);document.body.append(dlg);
  search.addEventListener('input',()=>{L.query=search.value;render();});
  search.addEventListener('keydown',e=>{if(e.key==='Escape'&&search.value){e.preventDefault();search.value='';L.query='';render();}});   // Esc clears first, then closes
  nav.addEventListener('keydown',e=>{const tabs=[...nav.querySelectorAll('[role=tab]')],i=tabs.indexOf(document.activeElement);if(i<0)return;
    const j={ArrowDown:i+1,ArrowRight:i+1,ArrowUp:i-1,ArrowLeft:i-1,Home:0,End:tabs.length-1}[e.key];if(j===undefined)return;
    e.preventDefault();const t=tabs[(j+tabs.length)%tabs.length];t.focus();show(t.dataset.category);});
  // Keys stay in this window: Escape closes only it, and the editor's shortcuts and Theme AI's Escape do not fire behind it.
  dlg.addEventListener('keydown',e=>{e.stopPropagation();if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='f'){e.preventDefault();search.focus();search.select();}});
  resetCat.onclick=()=>resetCategory().catch(e=>say(e.message,true));resetAll.onclick=()=>resetEverything().catch(e=>say(e.message,true));
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key===','&&!document.querySelector('dialog[open]')){e.preventDefault();openSettings();}});
}
function buildNav(){
  nav.replaceChildren();
  for(const c of L.categories){const t=btn('','');t.setAttribute('role','tab');t.id='settingsTab-'+c.id;t.dataset.category=c.id;t.setAttribute('aria-controls','settingsPanel');
    t.append(el('span','',c.label),el('span','settings-hits'));t.onclick=()=>show(c.id);nav.append(t);}
}
async function openSettings(category){
  if(!dlg)return;
  if(category&&L.categories.some(c=>c.id===category)){L.category=category;L.query='';search.value='';}
  if(!dlg.open){render();dlg.showModal();search.focus();}
  if((!L.values||!L.categories.length)&&!L.old){try{await load();}catch(e){}render();}
}
function show(category){L.category=category;if(L.query){L.query='';search.value='';}render();panel.scrollTop=0;}

// ----- what the panel shows ------------------------------------------------------------------------------------------
const specOf=k=>L.schema.find(s=>s.key===k);
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const textOf=s=>[s.label,s.description,s.keywords||'',(s.choices||[]).map(c=>c.label).join(' '),(L.categories.find(c=>c.id===s.category)||{}).label||''].join(' ').toLowerCase();
const hit=(text,q)=>q.split(/\s+/).filter(Boolean).every(w=>text.includes(w));
function render(){
  if(!panel)return;
  const q=L.query.trim().toLowerCase();panel.replaceChildren();
  if(L.old){panel.append(stale());return paintNav({});}
  if(!L.values||!L.categories.length){panel.append(el('p','settings-empty','Loading your settings…'));return paintNav({});}
  if(q){const counts=results(q);paintNav(counts);resetCat.hidden=true;return paint();}
  const cat=L.categories.find(c=>c.id===L.category)||L.categories[0];L.category=cat.id;
  panel.setAttribute('aria-labelledby','settingsTab-'+cat.id);panel.append(section(cat,L.schema.filter(s=>s.category===cat.id),true));
  const own=L.schema.filter(s=>s.category===cat.id&&s.type!=='action');
  resetCat.hidden=!own.length;resetCat.textContent='Reset '+cat.label;resetCat.disabled=!own.some(s=>!same(L.values[s.key],s.default));
  paintNav({});paint();
}
function paintNav(counts){
  const searching=!!L.query.trim();
  for(const t of nav.children){const on=!searching&&t.dataset.category===L.category,n=counts[t.dataset.category]||0;
    t.setAttribute('aria-selected',String(on));t.tabIndex=on||(searching&&t===nav.firstChild)?0:-1;
    t.lastChild.textContent=searching&&n?String(n):'';t.lastChild.hidden=!(searching&&n);t.classList.toggle('settings-nohits',searching&&!n);}
}
function results(q){
  const counts={};let total=0;panel.setAttribute('aria-labelledby','settingsSearch');
  for(const cat of L.categories){
    const specs=L.schema.filter(s=>s.category===cat.id&&hit(textOf(s),q)),extras=Object.entries(EXTRAS[cat.id]||{}).filter(([,t])=>hit(t+' '+cat.label.toLowerCase(),q)).map(([k])=>k);
    counts[cat.id]=specs.length+extras.length;total+=counts[cat.id];if(!counts[cat.id])continue;
    const sec=section(cat,specs,false);
    if(extras.includes('usage'))sec.insertBefore(usageBlock(),sec.children[1]||null);
    if(extras.includes('about'))sec.append(aboutBlock());if(extras.includes('privacy'))sec.append(privacyBlock());
    panel.append(sec);}
  if(!total)panel.append(el('p','settings-empty',`No settings match “${L.query.trim()}”. Try other words, like “undo”, “sound” or “update”.`));
  return counts;
}
function section(cat,specs,whole){
  const sec=el('section','settings-section');sec.dataset.category=cat.id;sec.append(el('h3','',cat.label));
  if(whole){sec.append(el('p','settings-intro',cat.intro));if(cat.id==='storage')sec.append(usageBlock());if(cat.id==='about')sec.append(aboutBlock());}
  for(const s of specs)sec.append(row(s));
  if(whole&&cat.id==='storage')sec.append(privacyBlock());
  return sec;
}
function row(spec){
  const id='set-'+spec.key,r=el('div','settings-row');r.dataset.key=spec.key;
  const text=el('div','settings-text'),line=el('div','settings-title');
  const named=spec.type==='bool'||spec.type==='number'||(spec.type==='choice'&&spec.key!=='accent')||spec.type==='text';
  const label=el(named?'label':'span','settings-label',spec.label);label.id=id+'-label';if(named)label.htmlFor=id;line.append(label);
  if(spec.restart){const t=el('span','settings-tag','Next start');t.title=spec.restart==='app'?'Takes effect the next time you start Asset Lab':'Takes effect the next time the lab opens (or when you reload this page)';line.append(t);}
  const saved=el('span','settings-saved','Saved');saved.setAttribute('aria-hidden','true');line.append(saved);
  const desc=el('p','settings-desc',spec.description);desc.id=id+'-desc';const info=el('p','settings-info');info.hidden=true;
  text.append(line,desc,info);
  const ctl=el('div','settings-control');ctl.append(...control(spec,id));r.append(text,ctl);
  for(const c of ctl.querySelectorAll('input,select,button'))c.setAttribute('aria-describedby',id+'-desc');
  return r;
}
function control(spec,id){
  const v=L.values[spec.key];
  if(spec.type==='bool'){const c=el('input','settings-switch');c.type='checkbox';c.id=id;c.setAttribute('role','switch');c.checked=!!v;c.onchange=()=>save(spec.key,c.checked);return[c];}
  if(spec.type==='choice'&&spec.key==='accent'){
    const g=el('div','settings-swatches');g.setAttribute('role','radiogroup');g.setAttribute('aria-labelledby',id+'-label');
    for(const c of spec.choices){const l=el('label','settings-swatch');l.title=c.label;const i=el('input');i.type='radio';i.name=id;i.value=c.value;i.checked=c.value===v;i.setAttribute('aria-label',c.label);
      const dot=el('span');dot.style.background=ACCENT[c.value]||'#888';i.onchange=()=>{if(i.checked)save(spec.key,c.value);};l.append(i,dot);g.append(l);}
    return[g];}
  if(spec.type==='choice'){const s=el('select');s.id=id;spec.choices.forEach((c,i)=>{const o=el('option','',c.label);o.value=String(i);s.append(o);});
    s.value=String(spec.choices.findIndex(c=>c.value===v));s.onchange=()=>save(spec.key,spec.choices[+s.value].value);return[s];}
  if(spec.type==='number'){const n=el('input');n.type='number';n.id=id;n.min=spec.min;n.max=spec.max;n.step=spec.step||'any';n.value=v;
    n.onchange=()=>{const x=parseFloat(n.value);if(!isFinite(x)){n.value=L.values[spec.key];return;}save(spec.key,Math.min(spec.max,Math.max(spec.min,x)));};
    return spec.unit?[n,el('span','settings-unit',spec.unit)]:[n];}
  if(spec.type==='text'&&spec.options==='models'){const s=el('select');s.id=id;s.onchange=()=>save(spec.key,s.value);fillModels(s);if(!L.models)loadModels();return[s];}
  if(spec.type==='text'){const t=el('input');t.type='text';t.id=id;t.value=v;t.maxLength=spec.max_length||200;t.spellcheck=false;t.autocomplete='off';
    t.onchange=()=>save(spec.key,t.value);t.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();t.dispatchEvent(new Event('change'));}};return[t];}
  if(spec.type==='action'){const b=btn(spec.button||'Run',spec.danger?'danger':'');b.id=id;b.onclick=()=>act(spec,b);return[b];}
  return[];
}
function fillModels(s){
  const v=L.values.planner_model,names=L.models||[];s.replaceChildren();const auto=el('option','','Automatic (best installed)');auto.value='';s.append(auto);
  for(const n of names){const o=el('option','',n);o.value=n;s.append(o);}
  if(v&&!names.includes(v)){const o=el('option','',L.models?v+' (not installed)':v);o.value=v;s.append(o);}
  s.value=v||'';}
async function loadModels(){try{const d=await api('/api/theme-status');L.models=(d.models||[]).map(m=>m.name);}catch(e){L.models=[];}const s=byId('set-planner_model');if(s)fillModels(s);paint();}

// Live facts beside some settings: the link helper, the builds, the workspace folder, the chosen model.
function paint(){
  if(!panel)return;const u=L.usage;let wants=false;
  for(const r of panel.querySelectorAll('.settings-row[data-key]')){
    const k=r.dataset.key,info=r.querySelector('.settings-info'),b=r.querySelector('.settings-control button');let text='';
    if(k==='update_helper'||k==='remove_helper'){wants=true;const h=u&&u.helper;
      if(h){text=k==='remove_helper'?(h.installed?'Installed · '+size(h.bytes):'Not installed.'):h.ready?(h.updated?'Ready · '+h.updated:'Ready.'):'Not installed. It is downloaded, after asking, the first time a page link needs it.';
        if(b&&!r.dataset.busy)b.disabled=!(k==='remove_helper'?h.installed:h.ready);}}
    if(k==='get_ai'){wants=true;const a=u&&u.ai;if(!a)text='Checking…';   // the line is there from the start, so nothing below it jumps
      if(a&&!r.dataset.busy){text=a.installed?'Installed: Theme AI plans with it and Image AI redraws.':a.job?(a.job.message||'Downloading…'):a.known?'Not downloaded yet · '+size(a.missing)+', one time.':'';
        if(b)b.disabled=a.installed||!a.known;if(b)b.textContent=a.installed?'Installed':'Get the local AI';
        if(a.job&&!L.aiFollow){L.aiFollow=1;r.dataset.busy='1';if(b)b.disabled=true;follow(a.job,r).catch(e=>problem(r,e.message)).finally(()=>{L.aiFollow=0;delete r.dataset.busy;refreshUsage();});}}}
    if(k==='keep_builds'){wants=true;if(u)text=`${u.builds.count} build${u.builds.count===1?'':'s'} in your workspace now (${size(u.builds.bytes)}).`;}
    if(k==='open_workspace')text=((L.about&&L.about.folders||[]).find(f=>f.id==='workspace')||{}).path||'';
    if(k==='planner_model'&&L.models)text=!L.models.length?'No local AI model is installed, so Theme AI plans from keywords.':L.values.planner_model&&!L.models.includes(L.values.planner_model)?'That model is not installed now, so Automatic is used.':'';
    if(info){info.textContent=text;info.hidden=!text;info.classList.toggle('settings-path',k==='open_workspace');}}
  for(const box of panel.querySelectorAll('[data-usage]')){wants=true;fillUsage(box);}
  if(wants&&Date.now()-L.usageAt>15000)refreshUsage();
}
async function refreshUsage(){L.usageAt=Date.now();try{L.usage=await api('/api/settings-usage');}catch(e){L.usageAt=0;return;}paint();}
function usageBlock(){const box=el('div','settings-usage');box.dataset.usage='1';fillUsage(box);return box;}
function fillUsage(box){
  const u=L.usage;box.replaceChildren();const tile=(title,value,detail)=>{const d=el('div');d.append(el('small','',title),el('b','',value),el('span','',detail));box.append(d);};
  if(!u){for(const t of ['Workspace','Local AI','Link helper'])tile(t,'…','Measuring…');return;}
  const w=u.workspace,p=w.parts,sounds=(p.audio||0)+(p['audio-sources']||0);
  tile('Workspace',size(w.bytes),['Edits '+size(p.revisions),'game backups '+size(p.originals),'builds '+size(p.builds),p.designs?'drawings '+size(p.designs):'',sounds?'sounds '+size(sounds):''].filter(Boolean).join(' · '));
  tile('Local AI',u.runtime.bytes?size(u.runtime.bytes):'Not downloaded',u.runtime.bytes?'The image model, the planner and their programs':'Asset Lab offers it when it starts');
  tile('Link helper',u.helper.installed?size(u.helper.bytes):'Not installed',u.helper.installed?'yt-dlp, Deno and PyAV: sound from web pages':'Downloaded, after asking, when a page link needs it');
}
function privacyBlock(){
  const box=el('div','settings-privacy-box');box.append(el('div','section-label','WHAT ASSET LAB CONNECTS TO'));
  const list=el('ul','settings-privacy');list.id='settingsPrivacy';
  for(const p of L.privacy){const li=el('li');li.append(el('b','',p.what),document.createTextNode(p.text));if(p.hosts.length){const hs=el('div');for(const h of p.hosts)hs.append(el('span','settings-host',h));li.append(hs);}list.append(li);}
  box.append(list,el('p','settings-note',L.note));return box;
}
// Suggestions: straight to the creator's Google Form. The lab sends them (this page cannot reach the internet), and
// only what is typed and the version go out. An unsent draft is kept in this browser.
function suggestBlock(a){
  const DRAFT='lab-suggest-draft',sec=el('section','settings-suggest');sec.id='settingsSuggest';
  sec.append(el('h3','','Suggestions'),el('p','hint','Have an idea, a wish or a bug? Send it to the creator, who reads every one. Only what you type and your Asset Lab version are sent: no name, no account.'));
  const area=el('textarea','suggest-text');area.id='suggestText';area.rows=4;area.maxLength=a.suggest?.max_length||2000;area.placeholder='I wish Asset Lab could…';
  area.setAttribute('aria-label','Your suggestion');try{area.value=localStorage.getItem(DRAFT)||'';}catch(e){}
  const send=btn('Send suggestion','primary');send.id='suggestSend';const note=el('small','suggest-note','');note.setAttribute('role','status');
  const fit=()=>{send.disabled=!a.suggest?.ready||area.value.trim().length<3;};
  area.oninput=()=>{try{localStorage.setItem(DRAFT,area.value);}catch(e){}fit();};
  send.onclick=async()=>{send.disabled=true;note.textContent='Sending…';
    try{await api('/api/suggest',{text:area.value});area.value='';try{localStorage.removeItem(DRAFT);}catch(e){}note.textContent='Thank you! Your suggestion was sent.';}
    catch(e){note.textContent=e.message;}finally{fit();}};
  if(!a.suggest?.ready){area.disabled=true;note.textContent='Suggestions open in a later version.';}
  fit();const row=el('div','suggest-actions');row.append(send,note);sec.append(area,row);return sec;}
function aboutBlock(){
  const a=L.about||{},box=el('div','settings-about'),dl=el('dl','settings-facts');
  const credit=el('div','creator-credit');credit.setAttribute('aria-label','Created by Novu');
  const signature=el('span','creator-signature');signature.setAttribute('aria-hidden','true');
  // Four aligned windows onto the supplied lettering keep its exact shapes and crown,
  // while each letter's light pulses independently. One cached image, no font substitution.
  for(const letter of 'Novu'){
    const part=el('span','creator-letter',letter),art=el('img','creator-art');
    art.src='/novu-wordmark.png';art.alt='';part.append(art);signature.append(part);
  }
  signature.append(el('span','creator-crown'));
  const sheen=el('span','creator-sheen'),sheenArt=el('img','creator-art');
  sheenArt.src='/novu-wordmark.png';sheenArt.alt='';sheen.append(sheenArt);signature.append(sheen);
  for(let i=0;i<7;i++){
    const ember=el('span','creator-ember');ember.style.setProperty('--i',i);signature.append(ember);
  }
  for(const [x,y,delay] of [[54,18,0],[81,26,2.8],[29,38,5.6]]){
    const glint=el('span','creator-glint');glint.style.left=x+'px';glint.style.top=y+'px';
    glint.style.animationDelay=-delay+'s';signature.append(glint);
  }
  const byline=el('span','creator-byline','Created by');byline.setAttribute('aria-hidden','true');
  credit.append(byline,signature);box.append(credit,suggestBlock(a));
  const fact=(k,...parts)=>{dl.append(el('dt','',k));const dd=el('dd');dd.append(...parts);dl.append(dd);return dd;};
  fact('Version','Asset Lab '+(a.version||'(unknown)'));
  if(a.repo){const link=el('a','',`github.com/${a.repo}`);link.href=a.releases;link.target='_blank';link.rel='noopener noreferrer';
    fact('Updates from',link,` · checked when Asset Lab starts${L.values.check_updates?'':' (turned off under General)'}`);}
  else fact('Updates from',a.source?'This copy runs from its source folder, so it updates with it.':'This copy has no update source.');
  const show=btn('Show','');show.id='settingsNotices';show.onclick=()=>notices().catch(e=>say(e.message,true));
  if(a.notices)fact('Licences','The parts Asset Lab downloads, and their licences.',show);
  fact('Settings file',el('span','settings-path',L.file));box.append(dl);
  if(L.damaged)box.append(el('p','settings-warn','Your settings file could not be read, so the defaults are in use. The damaged file was kept beside it as settings.json.bad.'));
  box.append(el('div','section-label','YOUR FILES'));const list=el('div','settings-folders');
  for(const f of a.folders||[]){const r=el('div','settings-folder'),t=el('div');t.append(el('b','',f.label),el('span','settings-path',f.path));
    const o=btn('Open','',`Open this folder in File Explorer`);o.onclick=()=>api('/api/settings-action',{id:'open_folder',folder:f.id}).catch(e=>say(e.message,true));r.append(t,o);list.append(r);}
  box.append(list);return box;
}
function stale(){
  const box=el('div','settings-empty');box.append(el('p','','Settings arrived with an update of Asset Lab while it was running. Restart the lab once to use them.'));
  const banner=document.querySelector('.update-banner button.primary');
  if(banner){const b=btn('Restart lab now','primary');b.onclick=()=>{dlg.close();banner.click();};const p=el('p');p.append(b);box.append(p);}
  else box.append(el('p','','Close Asset Lab and open it again from its shortcut.'));
  return box;
}
async function notices(){
  const d=await api('/api/notices'),box=el('dialog','settings-notices');box.setAttribute('aria-labelledby','settingsNoticesTitle');
  const head=el('div','settings-head'),h=el('h2','','Licences and third-party notices');h.id='settingsNoticesTitle';const x=btn('✕','quiet','Close');x.setAttribute('aria-label','Close');x.onclick=()=>box.close();head.append(h,x);
  const body=el('div','settings-notices-body');
  for(const f of d.files){if(f.name==='LICENSE')body.append(el('h3','','Asset Lab’s own licence'));body.append(...markdown(f.text));}
  box.append(head,body);box.addEventListener('keydown',e=>e.stopPropagation());box.addEventListener('close',()=>box.remove());document.body.append(box);box.showModal();x.focus();
}
// Just enough Markdown for the notices: headings, tables, paragraphs, `code` and **bold**.
function markdown(text){
  const out=[];let table=null,para=[];const flush=()=>{if(para.length)out.push(inline(el('p'),para.join(' ')));para=[];};
  for(const raw of String(text).split(/\r?\n/)){const line=raw.trim();
    if(line.startsWith('|')){flush();const cells=line.replace(/^\||\|$/g,'').split('|').map(c=>c.trim());if(cells.every(c=>/^:?-{2,}:?$/.test(c)))continue;
      const tr=el('tr');for(const c of cells)tr.append(inline(el(table?'td':'th'),c));if(!table){table=el('table');out.push(table);}table.append(tr);continue;}
    table=null;const h=line.match(/^(#{1,3})\s+(.*)$/);
    if(h){flush();out.push(inline(el(h[1].length===1?'h3':'h4'),h[2]));continue;}
    if(!line){flush();continue;}para.push(line);}
  flush();return out;
}
function inline(node,text){for(const part of text.split(/(`[^`]+`|\*\*[^*]+\*\*)/)){if(!part)continue;node.append(part.startsWith('`')?el('code','',part.slice(1,-1)):part.startsWith('**')?el('b','',part.slice(2,-2)):document.createTextNode(part));}return node;}

// ----- saving and applying -------------------------------------------------------------------------------------------
const rowOf=k=>panel&&panel.querySelector(`.settings-row[data-key="${k}"]`);
function say(text,error=false){footNote.textContent=text||'';footNote.classList.toggle('error',!!error);clearTimeout(noteTimer);if(text)noteTimer=setTimeout(()=>{footNote.textContent='';},error?12000:7000);}
function flash(r){status.textContent='Saved';status.classList.remove('error');status.classList.add('on');clearTimeout(statusTimer);statusTimer=setTimeout(()=>status.classList.remove('on'),1600);
  const s=r&&r.querySelector('.settings-saved');if(s){s.classList.add('on');setTimeout(()=>s.classList.remove('on'),1600);}}
function problem(r,text){if(!r)return say(text,true);let p=r.querySelector('.settings-error');if(!text){if(p)p.remove();return;}
  if(!p){p=el('p','settings-error');p.setAttribute('role','alert');r.append(p);}p.textContent=text;}
async function save(key,value){
  const r=rowOf(key),before=L.values[key];problem(r,'');
  L.values={...L.values,[key]:value};if(key!=='page_size')apply(key);
  try{const res=await api('/api/settings',{values:{[key]:value}});merge(res);if(key==='page_size')apply(key);sync(key);flash(rowOf(key));
    if(res.pruned&&res.pruned.removed)say(`${res.pruned.removed} old build${res.pruned.removed===1?'':'s'} deleted (${size(res.pruned.freed)} freed).`);
    if(key==='keep_builds')refreshUsage();}
  catch(e){L.values={...L.values,[key]:before};apply(key);sync(key);problem(rowOf(key),e.message);status.textContent='Not saved';status.classList.add('on','error');}
}
function merge(res){const old=L.values||{};L.values=res.values;L.changed=res.changed||[];store.set(CACHE,lookOf(L.values));
  for(const k of Object.keys(L.values))if(!same(old[k],L.values[k])){apply(k);document.dispatchEvent(new CustomEvent('lab-setting',{detail:k}));}   /* others follow a change (Theme AI's Accelerate) */
  if(L.lang!==undefined&&L.values.language!==L.lang)relanguage();}
// A new language needs the page drawn again: once it is saved, the page reloads and Settings opens where it was.
function relanguage(){L.lang=L.values.language;say('Switching the language…');try{sessionStorage.setItem(REOPEN,L.category||'general');}catch(e){}setTimeout(()=>location.reload(),150);}
function sync(key){   // the control shows what is saved
  const s=specOf(key),c=byId('set-'+key),v=L.values[key];if(!s||!c)return;
  if(s.type==='bool')c.checked=!!v;else if(s.key==='accent'){for(const i of c.querySelectorAll('input'))i.checked=i.value===v;}
  else if(s.type==='choice')c.value=String(s.choices.findIndex(x=>x.value===v));else if(s.options==='models')fillModels(c);else if(document.activeElement!==c)c.value=v;
  if(!L.query&&resetCat){const own=L.schema.filter(x=>x.category===L.category&&x.type!=='action');resetCat.disabled=!own.some(x=>!same(L.values[x.key],x.default));}
}
function apply(key){   // what a setting changes on the page, right away
  const v=L.values[key],hasS=typeof S==='object'&&!!S;
  if(LOOK.includes(key)){look(L.values);store.set(CACHE,lookOf(L.values));}
  if(key==='reduce_motion'||key==='history_states'||key==='history_memory'||key==='pixel_grid')editor();
  if(key==='canvas_bg')backdrop();
  if(key==='sharp_pixels'){const p=byId('pixelated');if(p&&p.checked!==!!v){p.checked=!!v;if(typeof draw==='function')draw();}}
  if(key==='auto_apply'){const a=byId('themeAuto');if(a)a.checked=!!v;}
  if(key==='planner_model'){const m=byId('themeModel'),opts=m?[...m.options].map(o=>o.value):[];if(m&&opts.length)m.value=v&&opts.includes(v)?v:opts[0];}
  if(key==='page_links')linkHint();
  if(key==='page_size'&&hasS&&S.token&&typeof library==='function'){S.offset=0;library().catch(()=>{});}
  if(key==='show_copies'){const box=byId('showAll');if(box&&box.checked!==!!v&&hasS&&S.token&&typeof filter==='function'){box.checked=!!v;S.parent='';filter().catch(()=>{});}}
}
function backdrop(){if(typeof canvasBackground==='function')canvasBackground(L.values.canvas_bg==='auto'?'auto':BACKDROP[L.values.canvas_bg]||BACKDROP.dark);}
function linkHint(){const link=byId('aeLink');if(link)link.placeholder=L.values.page_links?'or paste a link: YouTube, SoundCloud, Bandcamp, or a .mp3 / .ogg / .wav file':'or paste a direct link to a .mp3, .ogg or .wav file (page links are off in Settings)';}
// The editor engine (window.Ed): only what it offers is used.
function editor(){
  const Ed=window.Ed,v=L.values;if(!Ed||!v)return;
  if(Ed.history&&Ed.history.limits){Ed.history.limits.states=+v.history_states;Ed.history.limits.bytes=+v.history_memory*1024*1024;}
  if(Ed.view&&'pixelGrid' in Ed.view&&Ed.view.pixelGrid!==!!v.pixel_grid)Ed.view.pixelGrid=!!v.pixel_grid;
  for(const o of [Ed.view,Ed])if(o&&'reduceMotion' in o)o.reduceMotion=!!v.reduce_motion;   // the marching ants, once the engine offers it
}
async function resetCategory(){
  const cat=L.categories.find(c=>c.id===L.category);if(!cat)return;const keys=L.schema.filter(s=>s.category===cat.id&&s.type!=='action').map(s=>s.key);
  if(!await confirmAction(`Reset ${cat.label}?`,`Every ${cat.label} setting goes back to how Asset Lab comes. Other categories stay as they are.`,'Reset'))return;
  merge(await api('/api/settings',{reset:keys}));render();say(`${cat.label} is back to the defaults.`);
}
async function resetEverything(){
  if(!await confirmAction('Reset every setting?','All settings, in every category, go back to how Asset Lab comes. Your edits, your mod and your library marks are not touched.','Reset everything'))return;
  merge(await api('/api/settings',{reset:'all'}));render();say('Every setting is back to the default.');
}
async function act(spec,b){
  const r=rowOf(spec.key);problem(r,'');
  if(spec.key==='remove_helper'){const h=L.usage&&L.usage.helper;
    if(!await confirmAction('Remove the link helper?',`yt-dlp, Deno and PyAV are deleted from this PC${h&&h.bytes?' ('+size(h.bytes)+')':''}. Sounds you already saved keep working; the helper is downloaded again, after asking, the next time a page link needs it.`,'Remove'))return;}
  if(spec.key==='get_ai'){const a=L.usage&&L.usage.ai;
    if(!await confirmAction('Download the local AI?',`About ${a&&a.missing?size(a.missing):'7 GB'}, one time, from GitHub and Hugging Face. Then the smart Theme AI planner and Image AI redraws run on this PC: your requests never leave it. Works best with 16 GB of memory and a graphics card with 6 GB or more (without one, a drawing takes several minutes). You can keep using Asset Lab while it downloads, and a stopped download continues where it stopped.`,'Download'))return;
    L.aiFollow=1;}
  b.disabled=true;if(r)r.dataset.busy='1';
  try{const res=await api('/api/settings-action',{id:spec.key});
    if(res.job)await follow(res.job,r);else if(res.message)say(res.message);else if(res.path)say((res.opened?'Opened ':'')+res.path);
    await refreshUsage();}
  catch(e){problem(rowOf(spec.key),e.message);}
  finally{b.disabled=false;if(r)delete r.dataset.busy;if(spec.key==='get_ai')L.aiFollow=0;paint();}
}
async function follow(job,r){
  const info=r&&r.querySelector('.settings-info');
  while(job.state==='running'){if(info){info.hidden=false;info.textContent=job.message||'Working…';}await sleep(600);job=await api('/api/audio-job?id='+encodeURIComponent(job.id));}
  if(job.state!=='done')throw Error(job.error||'That did not work.');say((job.result&&job.result.message)||'Done.');
}
// Asked before the lab changes the game's files (theme_chat.js runs it before Install, Update and Restore).
async function beforeGameChange(id){
  if(!get('confirm_install'))return true;
  const hint='\n\n(You can stop this question in Settings › General.)';
  if(id==='restoreButton')return confirmAction('Restore the original game files?','Your mod comes out of Mitosis and the game’s own files go back. Your edits stay in the lab, ready to install again.'+hint,'Restore original');
  const update=!!(typeof S==='object'&&S&&S.status&&S.status.active);
  return confirmAction(update?'Update Mitosis with your changes?':'Install your mod in Mitosis?','Asset Lab replaces game files with your saved changes. The originals are backed up first, and Restore original puts them back at any time.'+hint,update?'Update Mitosis':'Install');
}

// ----- loading, and what happens once when the lab opens --------------------------------------------------------------
let loading=null;
function load(){
  if(!loading)loading=api('/api/settings').then(d=>{
    Object.assign(L,{values:d.values,changed:d.changed||[],schema:d.schema,categories:d.categories,about:d.about,privacy:d.privacy||[],note:d.privacy_note||'',file:d.file||'',damaged:!!d.damaged,old:false});
    if(L.lang===undefined)L.lang=d.values.language;   // the language this page was drawn in
    look(L.values);store.set(CACHE,lookOf(L.values));backdrop();linkHint();buildNav();
    // Theme AI's Auto-apply box and this setting are the same choice: whichever changes, the other follows.
    const a=byId('themeAuto');if(a){a.checked=!!L.values.auto_apply;if(!a.dataset.setting){a.dataset.setting='auto_apply';a.addEventListener('change',()=>{if(L.values&&a.checked!==!!L.values.auto_apply)save('auto_apply',a.checked);});}}
    if(dlg&&dlg.open)render();readyDone(L.values);return L.values;
  }).catch(e=>{loading=null;if(/not found/i.test(e.message)){L.old=true;if(dlg&&dlg.open)render();}readyDone(null);throw e;});
  return loading;
}
async function startup(){
  if(L.started||!L.values)return;L.started=true;const Ed=window.Ed,v=L.values;
  if(Ed&&Ed.ready)try{await Ed.ready;}catch(e){}
  if(Ed){
    editor();
    // View › Pixel Grid and this setting are the same choice: whichever changes, the other follows.
    if(Ed.on&&Ed.view&&'pixelGrid' in Ed.view)Ed.on('overlay',()=>{if(L.values&&Ed.view.pixelGrid!==!!L.values.pixel_grid)save('pixel_grid',Ed.view.pixelGrid);});
    if(v.start_focus&&!document.body.classList.contains('ed-focus-mode')){
      try{if(Ed.menu&&Ed.menu.get&&Ed.menu.get('View/Focus Editor'))Ed.menu.run('View/Focus Editor');else{const f=byId('focusButton');if(f)f.click();}}catch(e){}}
    if(!v.remember_tools&&Ed.tools&&Ed.toolOptions&&Ed.setToolOptions)   // every tool starts with its own defaults
      for(const t of Ed.tools.list()){const cur=Ed.toolOptions(t.id)||{},fresh={};
        for(const o of t.options||[])if(o.key&&o.type!=='button'&&o.default!==undefined&&!same(cur[o.key],o.default))fresh[o.key]=o.default;
        if(Object.keys(fresh).length)Ed.setToolOptions(t.id,fresh);}
  }
  const key=v.open_last&&store.get(LAST,'');
  if(key){for(let i=0;i<300&&!(typeof S==='object'&&S&&S.token&&document.querySelector('#assetList .asset-card'));i++)await sleep(50);
    if(typeof S==='object'&&S&&!S.asset&&!S.dirty&&typeof select==='function')try{await select(key,true);}catch(e){}}
}
document.addEventListener('DOMContentLoaded',()=>{
  build();
  document.addEventListener('lab:asset',e=>{const k=e.detail&&e.detail.key;if(k)store.set(LAST,k);});
  let reopen=null;try{reopen=sessionStorage.getItem(REOPEN);sessionStorage.removeItem(REOPEN);}catch(e){}
  load().then(()=>{if(reopen)openSettings(reopen);return startup();}).catch(()=>{});
});
})();
