'use strict';
// Theme AI: describe a look, review the plan and samples, apply it, then install it into Mitosis from the same panel.
// Uses app.js globals: $, api, message, status, library, select, run, confirmAction, S.
(()=>{
const T={open:false,info:null,history:[],busy:false,job:null,plan:null,prompt:'',passing:false,game:null};
// Chat history: the conversation, what was applied and any drawing still being made come back after a reload.
const STORE='lab-theme-chat',J={entries:[]};
function keepChat(){try{let e=J.entries.slice(-40);for(;;){try{localStorage.setItem(STORE,JSON.stringify({entries:e,history:T.history}));return;}
  catch(x){if(e.length<=2){localStorage.removeItem(STORE);return;}e=e.slice(Math.ceil(e.length/4));}}}catch(x){}}
function record(entry){J.entries.push(entry);keepChat();return entry;}
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const button=el('button','theme-open','✦ Theme AI');button.id='themeButton';button.title='Change the whole look from a prompt';
document.querySelector('.topbar').insertBefore(button,$('launchButton'));
const drawer=el('aside','theme-drawer');drawer.id='themeDrawer';drawer.hidden=true;drawer.setAttribute('aria-label','Theme AI');

// Header: title, model and close on one line.
const head=el('div','theme-head');const title=el('div','theme-title');title.append(el('span','section-label','THEME AI'));
const model=el('select');model.id='themeModel';model.setAttribute('aria-label','Local AI model');const state=el('span','theme-state','Checking…');
const close=el('button','quiet','✕');close.id='themeClose';close.title='Close Theme AI';head.append(title,model,state,close);

// With assets checked in the library, a request changes only those.
const scopeLine=el('div','theme-scope');scopeLine.hidden=true;const scopeText=el('span');const scopeAll=el('button','', 'Use everything');scopeLine.append(scopeText,scopeAll);
let scopeOff=false;
function only(){return !scopeOff&&window.LabSelection?.size()?window.LabSelection.keys():null;}
function showScope(){const n=window.LabSelection?.size()||0;if(!n)scopeOff=false;scopeLine.hidden=!n;scopeText.textContent=scopeOff?`${n} checked, but this request covers everything.`:`Only the ${n} checked asset${n===1?'':'s'} will change.`;scopeAll.textContent=scopeOff?'Use checked only':'Use everything';}
scopeAll.onclick=()=>{scopeOff=!scopeOff;showScope();};document.addEventListener('lab-selection',showScope);

const log=el('div','theme-log');log.id='themeLog';log.setAttribute('role','log');

// Composer: request, ideas, auto-apply and send.
const form=el('form','theme-form');const input=el('textarea');input.id='themeInput';input.rows=2;input.placeholder='Describe a look… e.g. “rainbow outline on the buttons with a subtle glow”';input.setAttribute('aria-label','Theme request');
const row=el('div','theme-send');
const ideas=el('details','theme-ideas');ideas.append(el('summary','', 'Ideas'));const ideaList=el('div','theme-idea-list');ideas.append(ideaList);
for(const idea of ['Theme it rainbow with proper glows','Synthwave with a chrome logo and neon outlines','Fire gradient buttons with a strong orange glow','Holographic glass panels','Glossy gold everything','Retro 8-bit pixel look','Deep ocean blues, keep the logo','Hide the main menu pattern','Make the MITOSIS title spooky with dripping blood and bats','Put a party hat on the MITOSIS title','Christmas theme and put a santa hat on the title']){
  const b=el('button','', idea);b.type='button';b.onclick=()=>{input.value=idea;ideas.open=false;input.focus();};ideaList.append(b);}
const tags=el('details','theme-ideas');tags.append(el('summary','', 'Words'));const tagList=el('div','theme-idea-list theme-tags');tags.append(tagList);
for(const [label,words] of [['Parts','buttons · panels · icons · logo · cells · food · effects · fonts · menu background · arena — or a sprite or your own name for it: “the white virus”, “the speed potion”'],
  ['Edge','outline · border · rim · ring (thin / thick)'],['Inside','inside · fill · body — recolor, gradient, darker, brighter'],
  ['Light','glow (subtle / strong / inner) · flare · sparkle · glossy'],
  ['Gradients','rainbow · sunset · synthwave · fire · lava · ocean · ice · aurora · galaxy · toxic · candy · gold · chrome · pastel · holographic · “from purple to cyan” · vertical · radial · striped'],
  ['Moods','spooky · christmas · winter · forest · royal · cyberpunk · bloody · midnight — a matching palette for everything or the parts you name'],
  ['Redraw','“make the title spooky” · “put a top hat on the logo” · “add bats and dripping blood” · “turn the logo into graffiti” — drawn by the local image model; you pick a variant and adjust it'],
  ['Text','player names · menu text — a look (gold · neon sign · candy cane · galaxy…) · a font (“in impact”) · “red, orange and yellow gradient” · left to right · diagonal · 45 degrees · hollow · wide / narrow · white glow · black outline'],
  ['Styles','neon · glass · pastel · greyscale · retro 8-bit · cartoon · scanlines · grainy · hide · more / less transparent']]){const p=el('p');p.append(el('b','',label+' '),document.createTextNode(words));tagList.append(p);}
const auto=el('label','compact');const autoBox=el('input');autoBox.type='checkbox';autoBox.id='themeAuto';auto.title='Apply the plan as soon as it is ready';auto.append(autoBox,document.createTextNode(' Auto-apply'));
const send=el('button','primary','Plan');send.id='themeSend';send.type='submit';row.append(ideas,tags,auto,send);form.append(input,row);

// Dock: what is waiting to be installed, whether Mitosis is open, and the install / restore actions.
const dock=el('div','theme-dock');
const modLine=el('div','theme-dock-line');const modText=el('b','', 'Your mod is empty');const gameText=el('small','', '');modLine.append(modText,gameText);
const actions=el('div','theme-dock-actions');
const review=el('button','', 'Review');review.title='List the game files the install would change';
const install=el('button','primary','Install in Mitosis');install.id='themeInstall';
const restore=el('button','', 'Restore original');const launch=el('button','', 'Open Mitosis');
const undo=el('button','quiet','Undo theme');undo.id='themeUndo';undo.title='Take the last theme back off';
const gameButton=el('button','', 'Game colors…');gameButton.id='gameColorsButton';
const fontsButton=el('button','', 'Fonts…');fontsButton.id='fontsButton';fontsButton.title='Draw the game’s text (player names, menu text) in any font, with outline, glow and shadow';
fontsButton.onclick=()=>window.openFonts&&window.openFonts();
actions.append(review,install,restore,launch,gameButton,fontsButton,undo);dock.append(modLine,actions);

// Game colors & menu: the colors Mitosis.exe sets in code, by hand, and the main-menu promos it can leave out.
const game=el('div','theme-game');game.id='gamePanel';game.hidden=true;
drawer.append(head,scopeLine,log,game,form,dock);document.body.append(drawer);
const hex=v=>String(v||'').toUpperCase();
function renderGame(){
  const info=T.info?.game;if(!info||game.hidden)return;
  const palette=S.status?.palette||{},hidden=new Set(S.status?.hidden||[]),locked=false;
  game.replaceChildren();
  const blocked=S.status?.game_colors?.blocked;
  if(blocked){game.append(el('b','','Game colors & menu'),el('p','hint',blocked),el('p','hint','Recolor the menu through its art instead: ask Theme AI for “menu background navy” (the pattern) or redesign art with the image model.'),el('p','hint','To hide the App Store, Google Play or Discord badges or the OG tag, ask Theme AI: “hide the discord button”. It makes their art transparent; the empty spot can still be clicked. Worm.is and Strike.is are not drawn from editable art.'));return;}
  const top=el('div','theme-game-head');top.append(el('b','','Game colors & menu'));
  const reset=el('button','quiet','Reset all');reset.disabled=locked||!(Object.keys(palette).length||hidden.size);
  reset.onclick=run(async()=>{await api('/api/palette',{colors:{}});await api('/api/hide',{groups:[]});await status();renderGame();});top.append(reset);
  game.append(top,el('p','hint',locked?'Restore the installed mod to change these.':'Set in Mitosis.exe and installed with your mod. One color replaces the game’s teal, purple and gray palettes. Theme AI can set these too: “header bars purple”, “hide the discord button”.'));
  const colors=el('div','theme-game-list');
  for(const c of info.colors){
    const rowEl=el('label','theme-game-row');const pick=el('input');pick.type='color';pick.value=(palette[c.field]||c.was).toLowerCase();pick.disabled=locked;
    const changed=!!palette[c.field]&&hex(palette[c.field])!==hex(c.was);rowEl.classList.toggle('changed',changed);
    const text=el('span');text.append(el('b','',c.name.replace(/_/g,' ')),el('small','',c.what));
    const back=el('button','link','↺');back.type='button';back.title=`Back to the game's ${c.was}`;back.hidden=!changed;back.disabled=locked;
    const save=async value=>{const next={...palette};if(!value||hex(value)===hex(c.was))delete next[c.field];else next[c.field]=hex(value);await api('/api/palette',{colors:next});await status();renderGame();};
    pick.onchange=run(()=>save(pick.value));back.onclick=run(e=>{e.preventDefault();return save('');});
    rowEl.title=c.field;rowEl.append(pick,text,back);colors.append(rowEl);}
  game.append(colors,el('div','section-label','HIDE IN THE MAIN MENU'));
  const list=el('div','theme-game-list');
  for(const h of info.hide){const rowEl=el('label','theme-game-row');const box=el('input');box.type='checkbox';box.checked=hidden.has(h.id);box.disabled=locked;
    box.onchange=run(async()=>{const next=new Set(hidden);box.checked?next.add(h.id):next.delete(h.id);await api('/api/hide',{groups:[...next]});await status();renderGame();});
    rowEl.append(box,el('span','',h.label));list.append(rowEl);}
  game.append(list,el('p','hint','Hiding skips creating these controls and their links. Byte-verified against your Mitosis.exe; restore puts the original back.'));
}
function showGame(open){game.hidden=!open;log.hidden=open;form.hidden=open;gameButton.textContent=open?'Back to chat':'Game colors…';gameButton.classList.toggle('active',open);if(open)renderGame();}
gameButton.onclick=run(async()=>{if(game.hidden&&!T.info)await refresh();showGame(game.hidden);});

function scroll(){log.scrollTop=log.scrollHeight;}
function bubble(kind,text){const b=el('div','theme-bubble '+kind);if(text)b.append(el('p','',text));log.append(b);scroll();return b;}
function targetText(line){let t=line.targets.join(', ').replace(/_/g,' ');if(line.match.length)t+=' · only '+line.match.join(', ');if(line.exclude.length)t+=' · except '+line.exclude.join(', ');return t;}
function showPlan(box,result,prompt,keys,entry){
  const {plan,preview}=result;
  if(result.note)box.append(el('p','theme-note',result.note));
  if(preview?.forced)box.append(el('p','theme-note','None of the checked assets belong to the parts named, so every step applies to all of the checked assets.'));
  const code=(preview?.ui_colors?.length||0)+(preview?.hide?.length||0);
  if(!plan.operations.length&&!code)return;
  for(const ui of preview.ui_colors||[]){const line=el('div','theme-ui');const sw=el('span','theme-swatch');sw.style.backgroundColor=ui.color;
    line.append(sw,el('b','',`Game ${ui.name.replace(/_/g,' ')} ${ui.color}`),document.createTextNode(` — ${ui.what} (now ${ui.was}).`));box.append(line);}
  for(const h of preview.hide||[]){const line=el('div','theme-ui');line.append(el('b','','Hide '),document.createTextNode(h.label+' in the main menu.'));box.append(line);}
  const list=el('ul','theme-lines');
  for(const line of preview.lines){const li=el('li');if(line.gradient?.length){const sw=el('span','theme-swatch wide');sw.style.backgroundImage=`linear-gradient(90deg,${line.gradient.join(',')})`;li.append(sw);}else if(line.color){const sw=el('span','theme-swatch');sw.style.backgroundColor=line.color;li.append(sw);}
    li.append(el('b','',targetText(line)),document.createTextNode(' — '+line.effect));
    li.append(el('small','',line.sheets?`${line.sheets} sheet${line.sheets===1?'':'s'}${line.regions?` · ${line.regions} sprites`:''}`:'matches nothing'));list.append(li);}
  box.append(list);
  if(preview.samples.length){const grid=el('div','theme-samples');for(const s of preview.samples){const fig=el('button','theme-sample');fig.title='Open '+s.name;const a=new Image(),b=new Image();a.src=s.before;b.src=s.after;a.alt='before';b.alt='after';fig.append(a,el('span','','→'),b,el('small','',(s.label||s.name)+(s.tint?' · in game':'')));if(s.tint)fig.title+=` · shown as the game draws it (× ${s.tint})`;fig.onclick=run(()=>select(s.key));grid.append(fig);}box.append(grid);}
  const colorsOnly=!preview.sheets&&code;
  const bar=el('div','theme-actions');const apply=el('button','primary',colorsOnly?(preview.ui_colors?.length?'Apply game colors':'Apply'):'Apply');apply.disabled=!preview.sheets&&!colorsOnly;
  const scope=[preview.sheets?`up to ${preview.sheets} sheet${preview.sheets===1?'':'s'}`:'',preview.ui_colors?.length?'game colors':'',preview.hide?.length?'menu':'',keys?`${keys.length} checked only`:''].filter(Boolean).join(' + ');
  apply.onclick=run(async()=>{const job=await applyPlan(plan,prompt,apply,keys);if(entry&&job?.state==='complete'){entry.applied=true;keepChat();}});
  if(entry?.applied){apply.textContent='Saved ✓';apply.disabled=true;}
  bar.append(apply,el('small','hint',`${scope} · ${({rules:'keyword planner',tags:'exact, from your words'}[result.source]||result.source)}`));box.append(bar);
  T.plan=plan;T.prompt=prompt;
}
async function refresh(){
  T.info=await api('/api/theme-status');
  // The player's pick in this drawer wins; until they pick, Settings' model is used (it may load after the drawer).
  const before=model.value,chosen=T.picked?before:'';model.replaceChildren();
  for(const m of T.info.models){const o=el('option','',m.name);o.value=m.name;model.append(o);}
  if(!T.info.models.length){const o=el('option','','Keyword planner');o.value='';model.append(o);}
  // The player's pick in this drawer, else Settings › Theme AI & drawing › Planner model when it is installed.
  const wanted=chosen||setting('planner_model')||before;if(wanted&&[...model.options].some(o=>o.value===wanted))model.value=wanted;
  state.textContent=T.info.ollama?'local AI':'keywords only';state.title=T.info.ollama?`Local model via Ollama · ${T.info.sheets} sheets`:'Ollama is not running, so requests are planned from keywords.';
  state.classList.toggle('offline',!T.info.ollama);
  undo.disabled=!T.info.last||T.busy;undo.title=T.info.last?`Take “${T.info.last.prompt.slice(0,60)}” back off (${T.info.last.sheets} sheets)`:'No theme applied yet';
  if(T.info.job&&!T.job)follow(T.info.job.id,bubble('bot','A theme is being applied…'));
  dockState();renderGame();
}
function setBusy(value){T.busy=value;send.disabled=value;input.disabled=value;undo.disabled=value||!T.info?.last;send.textContent=value?'Working…':'Plan';dockState();}

// ----- the dock ---------------------------------------------------------------------------------
function dockState(){
  const st=S.status||{},edits=(st.staged||[]).length,colors=Object.keys(st.palette||{}).length+(st.hidden||[]).length,active=st.active;
  const broken=active&&active.state!=='installed',pending=!!st.pending;
  modText.textContent=broken?'An install was interrupted':active?(pending?'Changes since your install · Update Mitosis to see them':'Your mod is installed in Mitosis'):edits||colors?`${edits} saved sheet${edits===1?'':'s'}${colors?' + game colors/menu':''} ready to install`:'Nothing to install yet';
  gameButton.title='Set game colors by hand and hide menu promos';
  gameText.textContent=T.game===null?'':T.game?' · Mitosis is open':' · Mitosis is closed';gameText.classList.toggle('warn',!!T.game);
  install.textContent=active?'Update Mitosis':'Install in Mitosis';install.hidden=!!broken;restore.hidden=!active;launch.hidden=!active||!!T.game;
  install.disabled=T.busy||(active?!pending:!(edits||colors));review.disabled=T.busy||!(edits||colors||active);
}
async function checkGame(){try{T.game=(await api('/api/game')).running;}catch{T.game=null;}dockState();return T.game;}
new MutationObserver(dockState).observe($('modTitle'),{childList:true,characterData:true,subtree:true});
setInterval(()=>{if(T.open)checkGame();},5000);
// Installing or restoring replaces game files, so Mitosis has to be closed first: offer to close it (or, with Settings ›
// General › If Mitosis is open set to "Close it for me", close it without asking).
const setting=k=>window.LabSettings?LabSettings.get(k):undefined;
async function closedFirst(what){
  if(!await checkGame())return true;
  if(setting('game_open')!=='close'&&!await confirmAction('Mitosis is open',`${what} replaces game files, so Mitosis has to be closed first. Close it now? Any match in progress ends.`,'Close Mitosis and continue'))return false;
  const r=await api('/api/close-game',{});await checkGame();
  if(!r.closed){message('Mitosis did not close. Close it yourself, then try again.',true);return false;}
  return true;}
// Settings › General › Ask before installing or restoring asks first (LabSettings.beforeGameChange).
async function pass(target,what){
  if(window.LabSettings&&LabSettings.beforeGameChange&&!await LabSettings.beforeGameChange(target.id))return;
  if(!await closedFirst(what))return;T.passing=true;try{target.click();}finally{T.passing=false;}}
// The same check guards the editor's own Install and Restore buttons.
for(const [id,what] of [['installButton','Installing the mod'],['restoreButton','Restoring the original files']]){
  $(id).addEventListener('click',e=>{if(T.passing)return;e.stopImmediatePropagation();e.preventDefault();run(()=>pass($(id),what))();},true);}
review.onclick=()=>$('reviewButton').click();
install.onclick=run(()=>pass($('installButton'),'Installing the mod'));
restore.onclick=run(()=>pass($('restoreButton'),'Restoring the original files'));
launch.onclick=()=>$('launchButton').click();

// ----- asking and applying -----------------------------------------------------------------------
async function ask(text){
  if(T.busy||!text.trim())return;
  bubble('user',text);record({t:'user',text});input.value='';setBusy(true);
  const wait=bubble('bot','Thinking…');const started=Date.now();const tick=setInterval(()=>{wait.firstChild.textContent=`Thinking… ${Math.round((Date.now()-started)/1000)}s`+(Date.now()-started>12000?' (the first request loads the model)':'');},1000);
  try{
    const keys=only();
    const result=await api('/api/theme-chat',{message:text,model:model.value,history:T.history,only:keys,language:window.LabI18n?.lang||'en'});   // the model replies in the lab's language
    clearInterval(tick);wait.firstChild.textContent=result.plan.reply||'Here is the plan.';
    if(result.plan.operations.length||Object.keys(result.plan.ui_colors||{}).length||result.plan.hide?.length){T.history.push({role:'user',content:text},{role:'assistant',content:JSON.stringify(result.plan)});T.history=T.history.slice(-8);}
    const entry=record({t:'bot',text:wait.firstChild.textContent,result,prompt:text,keys});
    if(result.undone){S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);await refresh();}
    renderResult(wait,entry,false);scroll();
    if(autoBox.checked&&result.preview&&(result.preview.sheets||result.preview.ui_colors?.length||result.preview.hide?.length)){setBusy(false);
      const job=await applyPlan(result.plan,text,wait.querySelector('.theme-actions button'),keys);if(job?.state==='complete'){entry.applied=true;keepChat();}}
  }catch(e){clearInterval(tick);wait.classList.add('error');wait.firstChild.textContent=e.message;record({t:'bot',error:e.message});}
  finally{setBusy(false);}
}
// A reply's plan, drawings and text card: drawn when it arrives and again from the history after a reload (restoring:
// nothing starts by itself, applied items stay applied, and drawings reconnect to their jobs).
function renderResult(wait,entry,restoring){
  const {result,prompt:text,keys}=entry;
  const planned=result.plan.operations.length||Object.keys(result.plan.ui_colors||{}).length||result.plan.hide?.length;
  if(result.font&&!planned&&!result.design){showFont(wait,result.font,result.note);return;}
  const addFont=()=>{if(!result.font)return;const offer=el('div','design-offer');
    offer.append(el('p','',result.font.offer?`Want the player names to match? Here they are in the ${result.font.look} look; flip through the others below.`:`Text · ${result.font.label}`));
    wait.append(offer);showFont(offer,result.font);};
  if(result.design&&!planned&&!result.design.optional){showDesign(wait,result.design,keys,null,false,entry,restoring);addFont();return;}
  showPlan(wait,result,text,keys,entry);
  if(result.design){const offer=el('div','design-offer');offer.append(el('p',''));wait.append(offer);showDesign(offer,result.design,keys,null,true,entry,restoring);}
  addFont();
}
// ----- design: redraw art with the local image model, then pick, adjust and apply ---------------------
const GAME_BG='#0D2829';
const drawings=()=>{const n=+setting('drawings');return n>=1&&n<=4?n:2;};   // Settings › Theme AI & drawing › Drawings per request
function showDesign(box,d,keys,base,offer,entry,restoring){
  // One drawing per distinct sprite; the title's sizes share one drawing.
  const groups=[],title=d.targets.filter(t=>t.name==='logo');if(title.length)groups.push(title);
  for(const t of d.targets)if(t.name!=='logo')groups.push([t]);
  const label=g=>g[0].name==='logo'?'the MITOSIS title':g[0].name;
  const what=groups.map(label).join(', ')+(title.length>1?' (every size the game uses)':'');
  box.firstChild.textContent=!d.available?(offer?'':`This needs the image model, which is not installed.`):
    d.optional?`Want ${what} redrawn to match? The local image model can draw it.`:
    offer?`The rest needs drawing: I'll redraw ${what} with the local image model.`:`I'll redraw ${what} with the local image model. Pick a variant, adjust it, then apply.`;
  if(!d.available)return;
  const bar=el('div','theme-actions');const count=el('select');count.className='design-count';for(const n of [1,2,3,4]){const o=el('option','',`${n} variant${n>1?'s':''}`);o.value=n;count.append(o);}count.value=String(drawings());
  const go=el('button','primary',groups.length>1?`Draw all ${groups.length}`:'Draw it');
  bar.append(go,count,el('small','hint',`about ${35*groups.length} s per variant`));box.append(bar);
  go.onclick=run(async()=>{go.disabled=true;count.disabled=true;
    let direction=null;   // the first drawing's art direction is reused, so a set comes out in one style
    for(const g of groups){const part=el('div','design-part');if(groups.length>1)part.append(el('p','design-part-title',label(g)));box.append(part);
      const done=await drawDesign(part,d.prompt,g.map(t=>t.key),+count.value,base,direction,entry,groups.length>1?label(g):'');if(done&&done.direction&&!direction)direction=done.direction;}});
  if(d.optional)go.textContent='Redraw it';
  if(restoring&&entry?.jobs?.length){bar.hidden=true;(async()=>{for(const info of entry.jobs)await resumeDesign(box,info,entry);})();return;}
  if(!restoring&&autoBox.checked&&!d.optional)go.click();
}
async function drawDesign(box,prompt,keys,count,base,direction,entry,label){
  let job=await api('/api/design-start',{prompt,only:keys||null,count,base:base||null,direction:direction||null});
  const info={id:job.id,keys:keys||null,label:label||''};if(entry){(entry.jobs||(entry.jobs=[])).push(info);keepChat();}
  return watchDesign(box,job,keys,entry,info);
}
// A drawing being made: progress until it is done (also after a reload, while the lab keeps running), then its card.
async function watchDesign(box,job,keys,entry,info){
  const text=el('small','hint',job.message||'Starting…');const barEl=el('div','theme-progress');const fill=el('span');barEl.append(fill);box.append(barEl,text);scroll();
  const started=Date.now();
  if(job.state==='running'){setBusy(true);const stop=el('button','quiet','Cancel');stop.onclick=run(async()=>{await api('/api/design-cancel',{job:job.id});});text.after(stop);
    try{while(job.state==='running'){await new Promise(r=>setTimeout(r,1000));job=await api('/api/design-job?job='+job.id);
        const m=job.message.match(/step (\d+) of (\d+)/);fill.style.width=(m?20+70*m[1]/m[2]:job.message.startsWith('Finishing')?92:8)+'%';
        text.textContent=`${job.message} · ${Math.round((Date.now()-started)/1000)} s`;}}
    finally{setBusy(false);stop.remove();}}
  barEl.classList.add('done');text.textContent=job.message;
  if(job.state!=='complete'){box.classList.add('error');return job;}
  designCard(box,job,keys,entry,info);scroll();return job;
}
async function resumeDesign(box,info,entry){
  const part=el('div','design-part');if(info.label)part.append(el('p','design-part-title',info.label));box.append(part);
  let job;try{job=await api('/api/design-job?job='+info.id);}
  catch(e){part.append(el('p','theme-note','This drawing is no longer available because the lab was restarted since it was made. Ask again to redraw it.'));return;}
  return watchDesign(part,job,info.keys,entry,info);
}
function showFont(box,font,note){
  // A text style, asked for or offered with a whole-game theme: the game font in that look, previewed. The looks menu
  // switches the preview instantly; Apply puts the look shown in the mod (never by itself), Fine-tune opens every setting.
  if(note)box.append(el('p','theme-note',note));
  const img=el('img','font-offer');img.src=font.image;img.alt=font.label+' preview';
  let style=font.style;
  const actions=el('div','theme-actions');const apply=el('button','primary','Apply');const tune=el('button','','Fine-tune…');
  const pick=window.LabFonts&&window.LabFonts.picker({font:font.id,text:font.text,style:font.style,img,first:font.offer?undefined:'Your request',
    onPick:s=>{style=s;apply.disabled=false;apply.textContent='Apply';}});
  apply.onclick=run(async()=>{apply.disabled=true;
    try{const r=await api('/api/font-apply',{font:font.id,style});apply.textContent='Applied ✓';message(`${r.label} saved in ${r.face}. Install in Mitosis to see it.`);}
    catch(e){apply.disabled=false;throw e;}
    window.LabFonts&&window.LabFonts.reload().catch(()=>{});S.build=null;await status();await refresh();});
  tune.onclick=()=>window.openFonts&&window.openFonts(font.id,style,font.text);
  actions.append(apply,tune);if(font.offer)actions.append(el('small','hint','Nothing changes until you click Apply.'));
  box.append(img);if(pick)box.append(pick);box.append(actions);
}
function designCard(box,job,keys,entry,info){
  const card=el('div','design-card');box.append(card);
  // Settings › Theme AI & drawing › Keep game-colored icons white ticks "Keep it white" for icons the game colors.
  const S2={variant:0,settings:{scale:1,dx:0,dy:0,tolerance:1,shape:job.stretched?1:0,fill:job.title?1:0,white:(job.tint||job.tinted)&&setting('keep_white')?1:0},applied:null};
  if(job.tint)card.append(el('p','theme-note',`The game colors this white icon ${job.tint} in the menu, so “In game” shows each variant the way the game will draw it. Tick “Keep it white” to get the game’s color with the drawing’s detail.`));
  else if(job.tinted)card.append(el('p','theme-note','This icon is plain white because the game colors it (teal in the menu), so a colorful drawing will come out tinted. Light, mostly white designs keep their look.'));
  if(job.direction){const dir=el('p','design-direction');dir.append(el('b','','Art direction: '),document.createTextNode(job.direction));card.append(dir);}
  const warn=el('p','theme-note');warn.hidden=true;card.append(warn);
  const showWarning=()=>{const w=(job.variants[S2.variant]||{}).warning;warn.textContent=w||'';warn.hidden=!w;};
  const url=(target,small)=>`/api/design-image?job=${job.id}&variant=${S2.variant}&target=${target}&${new URLSearchParams(Object.entries(S2.settings).map(([k,v])=>[k,String(v)]))}${small?'&small=1':''}&t=${Date.now()}`;
  const picks=el('div','design-variants');
  job.variants.forEach((v,i)=>{const b=el('button','design-variant'+(i===0?' on':''));b.type='button';b.title='Use variant '+(i+1);const im=new Image();im.src=`/api/design-image?job=${job.id}&variant=${i}&target=0&small=1`;b.append(im);
    if(v.warning)b.title+=' · '+v.warning;
    b.onclick=()=>{S2.variant=i;picks.querySelectorAll('.design-variant').forEach((x,j)=>x.classList.toggle('on',j===i));showWarning();refresh();};picks.append(b);});
  showWarning();
  const views=el('div','design-views');
  const frames=job.frames.map((f,t)=>{const fig=el('figure','design-view');const im=new Image();im.alt=job.names[t];fig.append(im,el('figcaption','',`${job.names[t]} · ${f[0]}×${f[1]} frame in the game`));views.append(fig);return im;});
  const before=el('figure','design-view before');const old=new Image();old.src=`/api/thumb?key=${encodeURIComponent(job.targets[0])}&big=1`;before.append(old,el('figcaption','','Now'));views.prepend(before);
  if(job.tint){
    // The game multiplies this icon by one of its colors: show the drawing that way too (white turns into that color).
    const fig=el('figure','design-view ingame');const cv=document.createElement('canvas');fig.append(cv,el('figcaption','',`In game (× ${job.tint})`));views.append(fig);
    frames[0].addEventListener('load',()=>{const im=frames[0];cv.width=im.naturalWidth;cv.height=im.naturalHeight;const c=cv.getContext('2d');
      c.clearRect(0,0,cv.width,cv.height);c.drawImage(im,0,0);c.globalCompositeOperation='multiply';c.fillStyle=job.tint;c.fillRect(0,0,cv.width,cv.height);
      c.globalCompositeOperation='destination-in';c.drawImage(im,0,0);c.globalCompositeOperation='source-over';});
  }
  let timer;function refresh(){clearTimeout(timer);timer=setTimeout(()=>frames.forEach((im,t)=>im.src=url(t)),150);}
  const sliders=el('div','design-sliders');
  // A new background fills its frame: only zoom and position apply (nothing is cut out or kept from the old shape).
  const knobs=job.scene?[['scale','Zoom',1,2.5,0.02],['dx','Left / right',-1,1,0.02],['dy','Up / down',-1,1,0.02]]:[['scale','Size',0.5,1.8,0.02],['dx','Left / right',-1,1,0.02],['dy','Up / down',-1,1,0.02],['tolerance','Background cut-out',0.6,1.6,0.05]];
  for(const [k,label,min,max,step] of knobs){
    const row=el('label');const r=el('input');r.type='range';r.min=min;r.max=max;r.step=step;r.value=S2.settings[k];r.oninput=()=>{S2.settings[k]=+r.value;refresh();};row.append(el('span','',label),r);sliders.append(row);}
  const shapeRow=el('label','design-shape');const shape=el('input');shape.type='checkbox';shape.checked=!!S2.settings.shape;
  shape.onchange=()=>{S2.settings.shape=shape.checked?1:0;refresh();};shapeRow.title='Trim the drawing to the original outline, so the game places and stretches it the same way';
  shapeRow.append(shape,document.createTextNode(' Keep the original shape'));if(!job.scene)sliders.append(shapeRow);
  const fillRow=el('label','design-shape');const fill=el('input');fill.type='checkbox';fill.checked=!!S2.settings.fill;
  fill.onchange=()=>{S2.settings.fill=fill.checked?1:0;refresh();};fillRow.title='Size the drawing by its main shape so it fills the frame like the original; sparse extras at the edges may be trimmed';
  fillRow.append(fill,document.createTextNode(' Fill the frame'));if(!job.scene)sliders.append(fillRow);
  if(job.tint||job.tinted){const whiteRow=el('label','design-shape');const white=el('input');white.type='checkbox';white.checked=!!S2.settings.white;
    white.onchange=()=>{S2.settings.white=white.checked?1:0;refresh();};whiteRow.title='Turn the drawing into white and gray: the game then colors it like the original, and the detail shows as shades of that color';
    whiteRow.append(white,document.createTextNode(' Keep it white (the game colors it)'));sliders.append(whiteRow);}
  const actions=el('div','theme-actions');const apply=el('button','primary','Apply to the game art');const more=el('button','','More variants');
  const refine=el('input');refine.placeholder='Refine this one… e.g. “bigger bats, less blood”';refine.className='design-refine';const again=el('button','','Refine');
  apply.onclick=run(async()=>{apply.disabled=true;const r=await api('/api/design-apply',{job:job.id,variant:S2.variant,settings:S2.settings});S2.applied=r;apply.textContent='Applied ✓';
    if(info){info.applied=true;keepChat();}
    const undoD=el('button','quiet','Undo design');undoD.onclick=run(async()=>{await api('/api/design-undo',{job:job.id});undoD.remove();apply.disabled=false;apply.textContent='Apply to the game art';if(info){info.applied=false;keepChat();}S.build=null;await status();await library();});
    const inst=el('button','primary',S.status?.active?'Update Mitosis':'Install in Mitosis');inst.onclick=install.onclick;actions.append(undoD,inst);
    S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);});
  if(info?.applied){apply.disabled=true;apply.textContent='Applied ✓';}
  more.onclick=run(()=>drawDesign(box,job.prompt,job.targets,drawings(),null,job.direction,entry));   // same art direction: the new variants match
  again.onclick=run(()=>{const extra=refine.value.trim();if(!extra)return refine.focus();return drawDesign(box,extra,null,drawings(),[job.id,S2.variant],null,entry);});
  refine.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();again.click();}};
  const refineRow=el('div','design-refine-row');refineRow.append(refine,again);
  actions.append(apply,more);card.append(picks,views,sliders,refineRow,actions);refresh();
}
function follow(id,box,control){
  T.job=id;setBusy(true);const bar=el('div','theme-progress');const fill=el('span');bar.append(fill);const text=el('small','hint','Starting…');box.append(bar,text);scroll();
  return new Promise(resolve=>{const poll=async()=>{let job;try{job=await api('/api/theme-job?job='+id);}catch(e){job={state:'failed',message:e.message,errors:[]};}
    fill.style.width=(job.total?Math.round(job.progress/job.total*100):4)+'%';text.textContent=job.state==='running'?`Shading ${job.progress} of ${job.total} sheets…`:job.message;
    if(job.state==='running'){setTimeout(poll,600);return;}
    T.job=null;setBusy(false);bar.classList.toggle('done',job.state==='complete');box.classList.toggle('error',job.state!=='complete');
    if(job.errors?.length)box.append(el('p','theme-note',`${job.errors.length} sheet(s) skipped: ${job.errors.slice(0,3).join(' · ')}`));
    if(job.state==='complete'){if(control){control.textContent='Saved ✓';control.disabled=true;}
      const next=el('div','theme-actions');const go=el('button','primary','Install in Mitosis');go.onclick=install.onclick;next.append(go,el('small','hint','or keep refining first'));box.append(next);}
    else if(control)control.disabled=false;
    S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);await refresh();scroll();
    resolve(job);};poll();});
}
async function applyPlan(plan,prompt,control,keys){
  if(T.busy)return;
  if(S.dirty&&!await confirmAction('Apply the theme?','You have an unsaved draft open. The theme saves new revisions of whole sheets; your draft stays in the editor.','Apply theme'))return;
  if(control)control.disabled=true;
  try{const job=await api('/api/theme-apply',{plan,prompt,only:keys||null});return await follow(job.id,control?control.closest('.theme-bubble'):bubble('bot','Applying…'),control);}
  catch(e){if(control)control.disabled=false;throw e;}
}
form.onsubmit=e=>{e.preventDefault();run(()=>ask(input.value))();};
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit();}});
undo.onclick=run(async()=>{if(!await confirmAction('Undo the last theme?','Every sheet it changed goes back to how it was just before. Sheets you edited by hand afterwards are left alone.','Undo theme'))return;
  setBusy(true);try{const r=await api('/api/theme-undo',{});const said=`Undid “${r.prompt.slice(0,80)}”: ${r.reverted} sheet(s) restored${r.skipped?`, ${r.skipped} left alone`:''}.`;bubble('bot',said);T.history=[];record({t:'note',text:said});S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);}finally{setBusy(false);await refresh();}});
const WELCOME='Describe the look you want: a color, a mood, or parts like buttons, panels, icons, cells, the logo or the menu pattern. I show a plan with samples; Apply saves it, and Install in Mitosis (below) puts it in the game. Say “undo” to take a theme back off, or “help” for more.';
function toggle(open){T.open=open;drawer.hidden=!open;showScope();document.body.classList.toggle('theme-on',open);button.classList.toggle('active',open);
  try{localStorage.setItem('lab-theme-open',open?'1':'');}catch(e){}
  if(open){run(refresh)();checkGame();input.focus();if(!log.childElementCount)bubble('bot',WELCOME);}}
button.onclick=()=>toggle(!T.open);close.onclick=()=>toggle(false);model.addEventListener('change',()=>{T.picked=true;});
// The saved conversation, drawn again; a request cut off by a reload says so.
function restoreChat(){let saved=null;try{saved=JSON.parse(localStorage.getItem(STORE)||'null');}catch(e){}
  if(!saved?.entries?.length)return;J.entries=saved.entries;T.history=saved.history||[];bubble('bot',WELCOME);
  J.entries.forEach((entry,i)=>{
    if(entry.t==='user'){bubble('user',entry.text);if(J.entries[i+1]?.t!=='bot')bubble('bot','This request was interrupted by a reload. Send it again if you still want it.').classList.add('error');return;}
    const b=bubble('bot',entry.error||entry.text);if(entry.error){b.classList.add('error');return;}
    if(entry.result)try{renderResult(b,entry,true);}catch(e){b.append(el('p','theme-note','This part of the chat could not be shown again.'));}});
  scroll();}
const fresh=el('button','quiet','New chat');fresh.id='themeNewChat';fresh.title='Start a new conversation (your saved art is not touched)';close.before(fresh);
fresh.onclick=()=>{if(T.busy)return;J.entries=[];T.history=[];keepChat();log.replaceChildren();bubble('bot',WELCOME);input.focus();};
restoreChat();try{if(localStorage.getItem('lab-theme-open'))toggle(true);}catch(e){}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&T.open&&!$('dialog').open&&document.activeElement!==input)toggle(false);});

// Recovery: an install that Steam or the game's cache has overwritten can no longer be restored normally.
const clear=el('button','', 'Clear stale install');clear.id='abandonButton';clear.hidden=true;clear.title='Steam or the game replaced a modded file. Close the install record and keep the current files.';
$('restoreButton').after(clear);
new MutationObserver(()=>{const stuck=S.status?.stuck||[];clear.hidden=!stuck.length;if(stuck.length)$('modStatus').textContent='Something outside the lab replaced '+stuck.join(', ')+'. Restore cannot overwrite it; use Clear stale install to move on.';}).observe($('modTitle'),{childList:true,characterData:true,subtree:true});
clear.onclick=run(async()=>{if(!await confirmAction('Clear this install record?','Files that still hold your mod are restored. Files that Steam or the game already replaced are left exactly as they are and become the new starting point. Your original backups stay in the workspace.','Clear record'))return;await api('/api/abandon-install',{});message('Install record cleared. Restart the lab to rescan the updated files.');await status();});
})();
