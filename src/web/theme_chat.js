'use strict';
// Theme AI: describe a look, review the plan and samples, apply it, then install it into Mitosis from the same panel.
// Uses app.js globals: $, api, message, status, library, select, run, confirmAction, S.
(()=>{
const T={open:false,info:null,history:[],busy:false,job:null,plan:null,prompt:'',passing:false,game:null};
// Chats: each build keeps its Theme AI conversations (builds.py). The open one is saved as it goes, so the conversation,
// what was applied and any drawing still being made come back after a reload, and older chats can be opened again.
const STORE='lab-theme-chat',J={entries:[],id:'',saveTimer:0,list:[],name:''};   // name: a name given to the open chat before its first save   // STORE: where chats lived before (moved over once)
const newId=()=>[...crypto.getRandomValues(new Uint8Array(8))].map(b=>b.toString(16).padStart(2,'0')).join('');
function chatTitle(){const first=J.entries.find(e=>e.t==='user');return first?first.text.replace(/\s+/g,' ').trim().slice(0,60):'New chat';}
function chatBody(){let entries=J.entries.slice(-60);while(entries.length>2&&JSON.stringify(entries).length>5e6)entries=entries.slice(Math.ceil(entries.length/4));
  return {id:J.id,title:J.name||chatTitle(),named:!!J.name,entries,history:T.history};}
async function saveChat(){clearTimeout(J.saveTimer);J.saveTimer=0;if(!J.entries.length)return false;
  try{const r=await api('/api/chat-save',chatBody()),asked=J.entries.filter(e=>e.t==='user'),was=J.list.find(c=>c.id===r.id);
    /* The chat just saved goes to the top of the list (newest first), with what the list shows about it. */
    J.list=[{id:r.id,title:r.title,updated:r.updated,named:!!J.name||!!was?.named,count:J.entries.length,asked:asked.length,
      last:(asked[asked.length-1]?.text||'').replace(/\s+/g,' ').trim().slice(0,90)},...J.list.filter(c=>c.id!==r.id)];
    J.name='';showChats();return true;}catch(e){return false;}}
function keepChat(){clearTimeout(J.saveTimer);J.saveTimer=setTimeout(saveChat,300);}
// Leaving the page: the last word is sent even while the page closes.
addEventListener('pagehide',()=>{if(!J.saveTimer||!J.entries.length||!S.token)return;clearTimeout(J.saveTimer);
  try{fetch('/api/chat-save',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','X-Lab-Token':S.token},body:JSON.stringify(chatBody())});}catch(e){}});
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
for(const idea of ['Make the menu background solid black','Deep purple to blue gradient background','Starry night sky background','Theme it rainbow with proper glows','Synthwave with a chrome logo and neon outlines','Fire gradient buttons with a strong orange glow','Holographic glass panels','Glossy gold everything','Retro 8-bit pixel look','Deep ocean blues, keep the logo','Hide the main menu pattern','Make the MITOSIS title spooky with dripping blood and bats','Put a party hat on the MITOSIS title','Christmas theme and put a santa hat on the title']){
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
const send=el('button','primary','Plan');send.id='themeSend';send.type='submit';row.append(ideas,tags,auto,send);
// Image AI: Auto draws when a request needs new art ("put a hat on the title"), On always draws new art (the main
// pieces of the game when nothing is named), Off only recolors and shades. Remembered between visits.
const drawRow=el('div','theme-drawmode');drawRow.id='themeDraw';const drawSeg=el('div','segmented');
let drawMode='auto';try{const v=localStorage.getItem('lab-theme-draw');if(v==='on'||v==='off')drawMode=v;}catch(e){}
for(const [id,label,tip] of [['auto','Auto','Draw new art when the request needs it'],['on','On','Always draw new art with the image model'],['off','Off','Only recolor and shade the existing art']]){
  const b=el('button','',label);b.type='button';b.dataset.draw=id;b.title=tip;b.onclick=()=>{drawMode=id;try{localStorage.setItem('lab-theme-draw',id);}catch(e){}showDraw();};drawSeg.append(b);}
function showDraw(){drawSeg.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.draw===drawMode));}
// Accelerate (Settings › Theme AI & drawing): drawings use the whole graphics card and every processor core. Turning
// it on warns first that the computer is slow while it draws.
const accel=el('button','accelerate','⚡ Accelerate');accel.id='themeAccelerate';accel.type='button';
accel.title='Draw faster with the whole graphics card and every processor core (the computer is slow while it draws)';
function showAccel(){const on=!!(window.LabSettings&&LabSettings.get('accelerate'));accel.classList.toggle('active',on);accel.setAttribute('aria-pressed',String(on));}
accel.onclick=run(async()=>{const on=!(window.LabSettings&&LabSettings.get('accelerate'));
  if(on&&!await confirmAction('Turn on Accelerate?','Drawings will use the whole graphics card and every processor core, at high priority. They finish sooner, but the computer will be slow while it draws: games, videos and other apps may stutter or lag.','Turn on Accelerate'))return;
  const res=await api('/api/settings',{values:{accelerate:on}});window.LabSettings?.merge?.(res);showAccel();
  message(on?'Accelerate is on: drawings use the full graphics card and processor.':'Accelerate is off: drawings leave room for other apps.');});
window.LabSettings?.ready?.then?.(showAccel);document.addEventListener('lab-setting',e=>{if(e.detail==='accelerate')showAccel();});
drawRow.append(el('span','section-label','IMAGE AI'),drawSeg,accel);showDraw();showAccel();
form.append(drawRow,input,row);

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
  const info=T.info?.game;if(game.hidden)return;
  const palette=S.status?.palette||{},hidden=new Set(S.status?.hidden||[]),locked=false;
  game.replaceChildren();
  const blocked=S.status?.game_colors?.blocked;
  if(blocked){game.append(el('b','','Game colors & menu'),el('p','hint',blocked),el('p','hint','Recolor the menu through its art instead: ask Theme AI for “menu background navy” (the pattern) or redesign art with the image model.'),el('p','hint','To hide the App Store, Google Play or Discord badges or the OG tag, ask Theme AI: “hide the discord button”. It makes their art transparent; the empty spot can still be clicked. Worm.is and Strike.is are not drawn from editable art.'));return;}
  if(!info)return;
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
gameButton.onclick=run(async()=>{showGame(game.hidden);if(!game.hidden&&!T.info)await refresh();});

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
async function ask(text,opts={}){   // opts: {only: keys, draw: 'on'} for "Redraw with Image AI" from the right-click menu
  if(T.busy||!text.trim())return;
  bubble('user',text);record({t:'user',text});input.value='';setBusy(true);
  const wait=bubble('bot','Thinking…');const started=Date.now();const tick=setInterval(()=>{wait.firstChild.textContent=`Thinking… ${Math.round((Date.now()-started)/1000)}s`+(Date.now()-started>12000?' (the first request loads the model)':'');},1000);
  try{
    const keys=opts.only||only();
    const result=await api('/api/theme-chat',{message:text,model:model.value,history:T.history,only:keys,language:window.LabI18n?.lang||'en',draw:opts.draw||drawMode});   // the model replies in the lab's language
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
  // One drawing per distinct sprite; the title's sizes share one drawing, and each food (big picture and small copy) has one.
  const isTitle=t=>t.set==='title'||t.name==='logo';   // a size can carry a name ("MITOSIS title logo (Main menu)")
  // Sets drawn once for all their sprites: the food, and the main menu background of every game theme. Other sprites are
  // drawn once per name, for all their copies (theme.png, theme4.png and the ones in assets.zip look the same).
  // Groups keep the order the lab sends (for the whole game: the title, background, viruses, play, coin and food first).
  const title=d.targets.filter(isTitle),order=new Map();
  for(const t of d.targets){const id=isTitle(t)?'title':t.set==='food'?'food:'+(t.kind??0):t.set==='background'||t.set==='arena'?t.set:'name:'+t.name;if(!order.has(id))order.set(id,[]);order.get(id).push(t);}
  const groups=[...order.values()];
  const label=g=>isTitle(g[0])?'the MITOSIS title':g[0].set==='food'?(g[0].tier?`the food (${g[0].tier})`:g.length>1?'the food (every pellet shape and size)':'the food'):
    g[0].set==='background'?(g.length>1?'the main menu background (every game theme)':'the main menu background'):
    g[0].set==='arena'?'the arena floor (in a match, light and dark mode)':g[0].name;
  const what=groups.length>6?`${groups.length} pieces of the game (${groups.slice(0,4).map(label).join(', ')}, …)`:groups.map(label).join(', ')+(title.length>1?' (every size the game uses)':'');
  box.firstChild.textContent=!d.available?(offer?'':`This needs the image model, which is not installed.`):
    d.optional?`Want ${what} redrawn to match? The local image model can draw it.`:
    offer?`The rest needs drawing: I'll redraw ${what} with the local image model.`:`I'll redraw ${what} with the local image model. Pick a variant, adjust it, then apply.`;
  if(!d.available)return;
  /* The pictures the look comes from (a Wikipedia lookup): the drawings get them too, not just words. */
  if(d.inspiration){const fig=el('figure','design-inspiration');const img=el('img');img.src='/api/inspiration?name='+encodeURIComponent(d.inspiration);img.alt=d.inspiration_title||'';img.loading='lazy';img.onclick=()=>fig.classList.toggle('big');
    fig.append(img,el('figcaption','',`Inspiration: ${d.inspiration_title||'pictures'} (the drawings take their look from these pictures)`));box.append(fig);}
  const variants=(selected)=>{const s=el('select');s.className='design-count';for(const n of [1,2,3,4]){const o=el('option','',`${n} variant${n>1?'s':''}`);o.value=n;s.append(o);}s.value=String(selected);return s;};
  const bar=el('div','theme-actions');const count=variants(drawings());count.title='Variants for every piece still in the queue (each piece can be set on its own in the queue)';
  // A big set: one variant each, except the pieces worth choosing between (the title and the viruses get two). The queue
  // window sets each piece on its own, leaves pieces out and puts them in the order to draw them.
  const big=groups.length>6,star=g=>isTitle(g[0])||g[0].set==='background'||g[0].set==='arena'||/virus/i.test(g[0].name);
  const Q=newQueue({prompt:d.prompt,base,entry,box,optional:d.optional,each:+d.seconds_per_drawing||45,inspiration:d.inspiration||entry?.batch?.inspiration||'',
    items:groups.map(g=>({keys:g.map(t=>t.key),prompt:g.map(t=>d.prompts?.[t.key]).find(Boolean)||'',rep:showKey(g),pieces:distinctPieces(g),label:groups.length>1?label(g):'',count:big?(star(g)?2:1):drawings(),on:true,state:'queued',job:'',applied:false}))});
  if(big)count.value='1';
  Q.go=el('button','primary','');Q.estimate=el('small','hint');
  bar.append(Q.go,count);
  if(groups.length>1){const open=el('button','', 'Queue…');open.type='button';open.title='See every piece larger, leave pieces out, choose their variants and the order to draw them';open.onclick=()=>openQueue(Q);bar.append(open);}
  bar.append(Q.estimate);box.append(bar);
  count.onchange=()=>{for(const q of Q.items)if(q.state==='queued')q.count=+count.value;refreshQueue(Q);};
  refreshQueue(Q);
  Q.go.onclick=run(async()=>{Q.go.disabled=true;count.disabled=true;
    if(!pending(Q).length)for(const q of Q.items)if(q.on&&q.state!=='drawing')Object.assign(q,{state:'queued',applied:false,job:''});   // Draw again: a new round
    await runQueue(Q);});
  if(restoring&&entry?.jobs?.length){bar.hidden=true;(async()=>{for(const info of entry.jobs)await resumeDesign(box,info,entry);
    // A queue cut off by a reload: what is left can be drawn, in the order it had.
    if(!entry.batch?.items)return;Object.assign(Q,{items:entry.batch.items,direction:entry.batch.direction||null});
    const rest=Q.items.filter(q=>q.on&&q.state!=='done'&&q.state!=='failed').length;
    if(rest>0){for(const q of Q.items)if(q.state==='drawing')q.state='queued';
      const more=el('button','primary',`Continue drawing the rest (${rest} left)`);more.type='button';const look=el('button','', 'Queue…');look.type='button';look.onclick=()=>openQueue(Q);
      more.onclick=run(async()=>{more.remove();look.remove();await runQueue(Q);});box.append(more,look);}})();return;}
  if(!restoring&&autoBox.checked&&!d.optional)Q.go.click();
}
const duration=s=>{s=Math.max(1,Math.round(s));const h=Math.floor(s/3600),m=Math.round((s%3600)/60);return h?`${h} h ${m} min`:m?`${m} min`:`${s} s`;};
const clockIn=s=>new Date(Date.now()+s*1000).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'});
// ----- the drawing queue: a set drawn one piece after another, in one style ---------------------------------------------
// Items: {keys, label, count (variants), on (in the queue), state: queued | drawing | done | failed, job, applied}. The
// next piece is always the first one still queued, so the order (and what is left out) can change while it draws. The
// items are kept with the chat entry, so a reload can continue them.
// The copy a piece is shown by: the one on theme.png (theme4.png's title carries the OG tag), the first food shape for
// the food; and the different pictures a set is made of (the food shapes), each once.
function showKey(g){return (g.find(t=>/^food\d+$/.test(t.file||''))||g.find(t=>t.sheet==='theme.png'&&t.size?.[1]!==172)||g.find(t=>t.sheet==='theme.png')||g[0]).key;}
function distinctPieces(g){const seen=new Set(),out=[];for(const t of g){const id=t.file||t.name;if(seen.has(id))continue;seen.add(id);out.push({key:t.key,name:id});}return out.slice(0,12);}
function newQueue(o){const Q=Object.assign({items:[],direction:null,running:false,stopped:false,spent:0,variantsDone:0,cards:new Map(),dialog:null},o);
  if(Q.items.length>1)remembered(Q);return Q;}
function keepQueue(Q){if(Q.entry){Q.entry.batch={items:Q.items,direction:Q.direction,inspiration:Q.inspiration||''};keepChat();}if(Q.items.length>1)remember(Q);}
// The player's queue choices, kept for next time (on this PC): pieces left out stay unticked but listed, their variants,
// and the order to draw them. Keyed by the piece's name, which stays the same from one request to the next.
const PREFS='lab-draw-queue';
function prefs(){try{return JSON.parse(localStorage.getItem(PREFS)||'{}')||{};}catch(e){return {};}}
function remember(Q){const p=prefs();p.pieces=p.pieces||{};
  for(const q of Q.items)if(q.label)p.pieces[q.label]={on:q.on,count:q.count};
  // Order: this queue's order, with pieces it does not have kept where they were.
  const here=Q.items.map(q=>q.label).filter(Boolean),before=(p.order||[]).filter(l=>!here.includes(l));p.order=[...here,...before].slice(0,600);
  try{localStorage.setItem(PREFS,JSON.stringify(p));}catch(e){}}
function remembered(Q){const p=prefs(),rank=new Map((p.order||[]).map((l,i)=>[l,i]));let used=false;
  for(const q of Q.items){const s=p.pieces?.[q.label];if(s){q.on=s.on!==false;if(s.count>=1&&s.count<=4)q.count=s.count;used=true;}}
  if(rank.size){used=true;const at=new Map(Q.items.map((q,i)=>[q,i]));Q.items.sort((a,b)=>(rank.get(a.label)??1e4+at.get(a))-(rank.get(b.label)??1e4+at.get(b)));}
  Q.remembered=used;}
const pending=Q=>Q.items.filter(q=>q.on&&q.state==='queued');
const pace=Q=>Q.variantsDone?Q.spent/Q.variantsDone:Q.each;   // seconds a variant: this PC's learned time, then this set's real pace
function refreshQueue(Q){
  const left=pending(Q),n=left.reduce((a,q)=>a+q.count,0),s=n*pace(Q),many=Q.items.length>1;
  if(Q.go&&!Q.go.disabled){const on=Q.items.filter(q=>q.on).length;Q.go.textContent=many?`${Q.optional?'Redraw':'Draw'} ${on} of ${Q.items.length}`:Q.optional?'Redraw it':'Draw it';Q.go.disabled=!on;}
  const text=!many?'May take several minutes; speed depends on your PC.':n?`${n} drawing${n===1?'':'s'}: about ${duration(s)} (done around ${clockIn(s)}).`:'Nothing left to draw.';
  if(Q.estimate)Q.estimate.textContent=text;
  if(Q.line&&Q.running){const now=Q.items.find(q=>q.state==='drawing');Q.line.textContent=`${now?`Drawing ${now.label||'it'}`:'Drawing'} · ${left.length} left · about ${duration(s)} left · done around ${clockIn(s)}`;}
  if(Q.ready){const done=Q.items.filter(q=>q.state==='done'&&!q.applied).length;Q.ready.hidden=!done;Q.ready.textContent=`Apply finished drawings (${done})`;}
  if(Q.dialog?.open)renderQueue(Q);
}
async function designerFree(){for(;;){try{const s=await api('/api/theme-status');if(!s.design?.job)return;}catch(e){return;}await new Promise(r=>setTimeout(r,2000));}}
async function runQueue(Q){
  const many=Q.items.length>1;
  if(many&&!Q.head){Q.head=el('div','design-batch-head');Q.line=el('p','design-batch');
    const look=el('button','', 'Queue…');look.type='button';look.title='Change the order, leave pieces out, see them larger';look.onclick=()=>openQueue(Q);
    Q.stop=el('button','quiet','Stop after this drawing');Q.stop.type='button';Q.stop.onclick=()=>{Q.stopped=true;Q.stop.disabled=true;Q.stop.textContent='Stopping after this drawing…';};
    // Roll out what is ready while the rest is still drawing: the first variant of each, with its default settings.
    Q.ready=el('button','primary','');Q.ready.type='button';Q.ready.hidden=true;Q.ready.title='Use the first variant of each finished drawing now, while the rest keep drawing';
    Q.ready.onclick=run(async()=>applyFinished(Q));
    Q.head.append(Q.line,look,Q.ready,Q.stop);Q.box.append(Q.head);}
  Q.running=true;Q.stopped=false;if(Q.stop){Q.stop.disabled=false;Q.stop.textContent='Stop after this drawing';Q.stop.hidden=false;}
  try{
    while(!Q.stopped){
      const item=pending(Q)[0];if(!item)break;
      await designerFree();   // one drawing at a time (one may still be finishing from before a reload)
      if(Q.stopped||!item.on||item.state!=='queued')continue;
      item.state='drawing';refreshQueue(Q);keepQueue(Q);
      /* Each piece folds into one line once the next starts, so a set of 130 pieces stays a list, not a wall; the queue's
         controls stay on top (sticky). Click a line to open that piece again. */
      const part=el('details','design-part');part.open=true;const head=el('summary','design-part-title',item.label||'This piece');head.append(el('small','design-part-state',''));
      part.append(head);Q.box.append(part);for(const [other,p] of Q.cards)if(p!==part&&other.state!=='drawing')p.open=false;Q.cards.set(item,part);
      const t0=Date.now();
      const job=await drawDesign(part,item.prompt||Q.prompt,item.keys,item.count,Q.base,Q.direction,Q.entry,item.label,item.prompt?'':Q.inspiration).catch(e=>{part.append(el('p','theme-note error',e.message));return null;});
      Q.spent+=(Date.now()-t0)/1000;Q.variantsDone+=item.count;
      item.state=job?.state==='complete'?'done':'failed';item.job=job?.id||'';
      head.lastChild.textContent=item.state==='done'?' · drawn':' · not drawn';
      keepQueue(Q);refreshQueue(Q);}
  }finally{Q.running=false;}
  if(!many)return;
  const done=Q.items.filter(q=>q.state==='done').length,left=pending(Q).length;
  Q.stop.hidden=true;Q.line.textContent=left?`Stopped: ${done} drawn, ${left} still in the queue.`:`All drawn: ${done} of ${Q.items.filter(q=>q.on).length}.`;
  if(left){const more=el('button','', 'Continue');more.type='button';more.onclick=run(async()=>{more.remove();await runQueue(Q);});Q.head.append(more);}
  refreshQueue(Q);
}
async function applyFinished(Q){
  const ready=Q.items.filter(q=>q.state==='done'&&!q.applied&&q.job);let n=0;Q.ready.disabled=true;
  try{for(const item of ready){Q.ready.textContent=`Applying ${++n} of ${ready.length}…`;
      const job=await api('/api/design-job?job='+item.job);await api('/api/design-apply',{job:job.id,variant:0,settings:designDefaults(job)});item.applied=true;keepQueue(Q);
      Q.cards.get(item)?.querySelector('.design-card')?.dispatchEvent(new CustomEvent('applied'));
      const line=Q.cards.get(item)?.querySelector('.design-part-state');if(line)line.textContent=' · applied ✓';}}
  finally{Q.ready.disabled=false;}
  message(`Applied ${ready.length} drawing${ready.length===1?'':'s'}. Install in Mitosis to see them.`);S.build=null;await status();await library();refreshQueue(Q);
}
// The queue window: every piece large, with its state; leave pieces out, set their variants, put them in the order to draw.
function openQueue(Q){
  if(!Q.dialog){const d=el('dialog','draw-queue');d.setAttribute('aria-label','Drawing queue');document.body.append(d);Q.dialog=d;d.addEventListener('close',()=>{});}
  renderQueue(Q);if(!Q.dialog.open)Q.dialog.showModal();
}
function renderQueue(Q){
  if(Q.dragging){Q.renderLater=true;return;}   // never rebuilt under a piece being dragged (a drawing finishing would drop it)
  const d=Q.dialog,scroll=d.querySelector('.draw-queue-list')?.scrollTop||0;
  const typing=document.activeElement?.classList.contains('draw-queue-search'),caret=typing?document.activeElement.selectionStart:0;d.replaceChildren();
  // Search by name; the buttons beside it tick or untick every piece the search shows (only pieces still to draw).
  const find=el('div','draw-queue-find');const search=el('input');search.type='search';search.className='draw-queue-search';search.placeholder='Search pieces…';
  search.setAttribute('aria-label','Search the queue');search.value=Q.search||'';
  const shown=()=>Q.items.filter(q=>q.state==='queued'&&(!Q.search||(q.label||'').toLowerCase().includes(Q.search.toLowerCase())));
  const all=(on,text,title)=>{const b=el('button','quiet',text);b.type='button';b.title=title;b.onclick=()=>{for(const q of shown())q.on=on;keepQueue(Q);refreshQueue(Q);};return b;};
  find.append(search,all(true,'Tick all shown','Draw every piece this search shows'),all(false,'Untick all shown','Leave out every piece this search shows'));
  const head=el('div','draw-queue-head');const x=el('button','quiet','✕');x.type='button';x.title='Close';x.onclick=()=>d.close();
  head.append(el('h2','', 'Drawing queue'),x);
  const hint=el('p','hint','Drag pieces (or use the arrows) to draw them sooner. Untick to leave one out. Click a picture to see it larger; once drawn it shows before and after.');
  const memo=el('p','hint draw-queue-memo','Your choices (pieces left out, variants, order) are kept for next time.');
  const forget=el('button','link','Forget my choices');forget.type='button';forget.title='Tick every piece again, back to the usual variants and order (from the next request)';
  forget.onclick=()=>{try{localStorage.removeItem(PREFS);}catch(e){}Q.remembered=false;message('Your queue choices are forgotten. The next request starts with everything ticked.');};
  memo.append(' ',forget);
  const list=el('div','draw-queue-list');
  // Dragging near the top or bottom edge scrolls the list (faster the closer to the edge), so a piece can travel far.
  list.ondragover=e=>{const r=list.getBoundingClientRect(),edge=Math.min(80,r.height/4);
    const up=e.clientY-r.top<edge,down=r.bottom-e.clientY<edge;
    if(up)list.scrollTop-=Math.ceil(40*(1-(e.clientY-r.top)/edge));else if(down)list.scrollTop+=Math.ceil(40*(1-(r.bottom-e.clientY)/edge));};
  const states={queued:'Queued',drawing:'Drawing…',done:'Drawn',failed:'Failed'};
  const queuedNow=pending(Q);
  Q.items.forEach((item,i)=>{
    const row=el('div','draw-queue-row '+item.state+(item.on?'':' off'));const waiting=item.state==='queued';
    row.draggable=waiting;row.dataset.i=i;
    row.ondragstart=e=>{e.dataTransfer.setData('text/plain',String(i));e.dataTransfer.effectAllowed='move';row.classList.add('dragging');Q.dragging=true;};
    row.ondragend=()=>{row.classList.remove('dragging');Q.dragging=false;if(Q.renderLater){Q.renderLater=false;renderQueue(Q);}};
    row.ondragover=e=>{if(waiting){e.preventDefault();row.classList.add('over');}};row.ondragleave=()=>row.classList.remove('over');
    row.ondrop=e=>{e.preventDefault();Q.dragging=false;Q.renderLater=false;const from=+e.dataTransfer.getData('text/plain');moveItem(Q,from,i);};
    const grip=el('span','draw-queue-grip',waiting?'⋮⋮':'');grip.setAttribute('aria-hidden','true');
    const tick=el('input');tick.type='checkbox';tick.checked=item.on;tick.disabled=!waiting;tick.title='Draw this piece';
    tick.onchange=()=>{item.on=tick.checked;keepQueue(Q);refreshQueue(Q);};
    const pic=new Image();pic.loading='lazy';pic.alt='';pic.className='draw-queue-pic';pic.src=`/api/thumb?key=${encodeURIComponent(item.rep||item.keys[0])}&big=1${item.applied?`&t=${item.job}`:''}`;
    pic.title='See it larger';pic.onclick=()=>zoomPiece(Q,item);
    const text=el('div','draw-queue-text');const place=waiting&&item.on?queuedNow.indexOf(item)+1:0;
    text.append(el('b','',item.label||'This piece'),el('small','',`${item.applied?'Applied ✓':states[item.state]}${place?` · #${place}`:''}`));
    const many=el('select');many.className='design-count';for(const n of [1,2,3,4]){const o=el('option','',`${n} variant${n>1?'s':''}`);o.value=n;many.append(o);}many.value=String(item.count);many.disabled=!waiting;
    many.onchange=()=>{item.count=+many.value;keepQueue(Q);refreshQueue(Q);};
    const acts=el('div','draw-queue-acts');
    const btn=(t,title,fn,off)=>{const b=el('button','quiet',t);b.type='button';b.title=title;b.disabled=!!off;b.onclick=fn;acts.append(b);};
    btn('Draw next','Draw this one as soon as the current drawing is done',()=>moveItem(Q,i,Q.items.indexOf(queuedNow[0]??item)),!waiting||place===1);
    btn('↑','Earlier',()=>moveItem(Q,i,i-1),!waiting||i===0);btn('↓','Later',()=>moveItem(Q,i,i+1),!waiting||i===Q.items.length-1);
    if(Q.cards.get(item))btn('Show','Show its drawing in the chat',()=>{d.close();const c=Q.cards.get(item);c.scrollIntoView({block:'center',behavior:'smooth'});c.classList.add('flash');setTimeout(()=>c.classList.remove('flash'),1200);});
    row.append(grip,tick,pic,text,many,acts);list.append(row);});
  const foot=el('div','draw-queue-foot');const est=el('span','hint');const left=pending(Q),n=left.reduce((a,q)=>a+q.count,0),s=n*pace(Q);
  est.textContent=n?`${left.length} piece${left.length===1?'':'s'}, ${n} drawing${n===1?'':'s'} left: about ${duration(s)} (done around ${clockIn(s)}).`:'Nothing left to draw.';
  foot.append(est);
  if(!Q.running&&n){const go=el('button','primary',Q.items.some(q=>q.state!=='queued')?'Continue drawing':'Start drawing');go.type='button';
    go.onclick=run(async()=>{d.close();if(Q.go)Q.go.disabled=true;await runQueue(Q);});foot.append(go);}
  const close=el('button','', 'Close');close.type='button';close.onclick=()=>d.close();foot.append(close);
  // Rows not matching the search are hidden, not removed, so dragging and the order still work on the whole queue.
  const filter=()=>{const t=(Q.search||'').toLowerCase();let n=0;
    for(const row of list.children){const name=row.querySelector('b')?.textContent.toLowerCase()||'';const hit=!t||name.includes(t);row.hidden=!hit;if(hit)n++;}
    none.hidden=!!n;};
  const none=el('p','hint draw-queue-none','No piece matches that search.');
  search.oninput=()=>{Q.search=search.value;filter();list.scrollTop=0;};
  d.append(head,hint,memo,find,list,none,foot);filter();list.scrollTop=scroll;
  if(typing){search.focus();search.setSelectionRange(caret,caret);}   // a drawing finishing while you type keeps the cursor where it was
}
function moveItem(Q,from,to){
  if(from===to||to<0||to>=Q.items.length||Q.items[from]?.state!=='queued')return;
  const [item]=Q.items.splice(from,1);Q.items.splice(to,0,item);keepQueue(Q);refreshQueue(Q);
}
// A piece large: the game's original, what is in your mod now, and the drawing (its first variant) once there is one.
function zoomPiece(Q,item){
  const d=el('dialog','piece-zoom');d.setAttribute('aria-label',item.label||'Piece');document.body.append(d);
  const rep=item.rep||item.keys[0],key=encodeURIComponent(rep),views=el('div','piece-zoom-views');
  const view=(src,caption,into=views)=>{const f=el('figure');const im=new Image();im.src=src;im.alt=caption;f.append(im,el('figcaption','',caption));into.append(f);return f;};
  const pieces=item.pieces||[];
  if(pieces.length>1){
    // A set (the food): every picture it is made of, as the game has them now; one drawing replaces them all.
    const set=el('div','piece-zoom-set');for(const p of pieces)view(`/api/image?key=${encodeURIComponent(p.key)}&original=1`,p.name,set);
    const box=el('figure','piece-zoom-setbox');box.append(set,el('figcaption','',`Original (${pieces.length} pictures, one drawing for all)`));views.append(box);}
  else view(`/api/image?key=${key}&original=1`,'Original');
  if(item.applied)view(`/api/image?key=${key}&t=${Date.now()}`,'In your mod now');
  if(item.job&&item.state==='done')view(`/api/design-image?job=${item.job}&variant=0&target=${Math.max(0,item.keys.indexOf(rep))}`,'Drawn (variant 1)');
  const head=el('div','draw-queue-head');const x=el('button','quiet','✕');x.type='button';x.onclick=()=>d.close();head.append(el('h2','',item.label||'Piece'),x);
  d.append(head,views);d.addEventListener('close',()=>d.remove());d.addEventListener('click',e=>{if(e.target===d)d.close();});d.showModal();
}
// What a drawing starts with (also what "Apply all" uses): zoomed and calmed for a background, the outline kept for a
// stretched button, the frame filled for the title, and kept white where the game colors the art itself.
function designDefaults(job){return {scale:job.scene&&!job.circular?2.5:1,...(job.arena?{pale:0.55}:job.scene?{calm:0.5}:{}),dx:0,dy:0,tolerance:1,shape:job.stretched?1:0,fill:job.title?1:0,white:job.spawner||!job.food&&(job.tint||job.tinted)&&setting('keep_white')?1:0};}   /* food keeps the drawing's colors */
async function drawDesign(box,prompt,keys,count,base,direction,entry,label,inspiration){
  let job=await api('/api/design-start',{prompt,only:keys||null,count,base:base||null,direction:direction||null,inspiration:inspiration||null});
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
  const S2={variant:0,settings:designDefaults(job),applied:null};   // food: the game paints every pellet its own color
  if(job.food)card.append(el('p','theme-note',(job.food_tier?.[1]>1?`This drawing becomes one of the game’s ${job.food_tier[1]} foods (each is worth a different mass), in every size and copy. `:`This drawing becomes every food pellet (${job.targets.length} shapes and sizes). `)+'It keeps the drawing’s colors; the game also tints each pellet, so the colors mix with it. Tick “Keep it white” for the game’s own pellet colors.'));
  else if(job.spawner)card.append(el('p','theme-note','The spawner (the red virus) can appear in maps, and the game paints it red. “Keep it white” is on, so it stays red with the drawing’s detail. It sits in the item files the game downloads: if your drawing does not show in a match, the game has put its own back.'));
  else if(job.tint)card.append(el('p','theme-note',`The game colors this white icon ${job.tint} in the menu, so “In game” shows each variant the way the game will draw it. Tick “Keep it white” to get the game’s color with the drawing’s detail.`));
  else if(job.tinted)card.append(el('p','theme-note','This icon is plain white because the game colors it (teal in the menu), so a colorful drawing will come out tinted. Light, mostly white designs keep their look.'));
  // One idea per variant when the AI came up with several; the line follows the variant picked.
  const ideaOf=i=>(job.directions||[])[i]||job.direction||'';const dir=el('p','design-direction');const dirText=document.createTextNode(ideaOf(0));dir.append(el('b','',job.directions?.length>1?'Idea: ':'Art direction: '),dirText);dir.hidden=!ideaOf(0);card.append(dir);
  const warn=el('p','theme-note');warn.hidden=true;card.append(warn);
  const showWarning=()=>{const w=(job.variants[S2.variant]||{}).warning;warn.textContent=w||'';warn.hidden=!w;};
  const url=(target,small)=>`/api/design-image?job=${job.id}&variant=${S2.variant}&target=${target}&${new URLSearchParams(Object.entries(S2.settings).map(([k,v])=>[k,String(v)]))}${small?'&small=1':''}&t=${Date.now()}`;
  const picks=el('div','design-variants');
  job.variants.forEach((v,i)=>{const b=el('button','design-variant'+(i===0?' on':''));b.type='button';b.title='Use variant '+(i+1);const im=new Image();im.src=`/api/design-image?job=${job.id}&variant=${i}&target=0&small=1`;b.append(im);
    if(v.warning)b.title+=' · '+v.warning;
    b.onclick=()=>{const changed=S2.variant!==i;S2.variant=i;picks.querySelectorAll('.design-variant').forEach((x,j)=>x.classList.toggle('on',j===i));dirText.textContent=ideaOf(i);dir.hidden=!ideaOf(i);showWarning();refresh();
      if(changed&&S2.live)run(applyNow)();};picks.append(b);});   /* already in the game art: the variant picked replaces it there too */
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
  const knobs=job.scene?[['scale','Zoom',1,3,0.02],job.arena?['pale','Paler',0,0.95,0.01]:['calm','Calmer',0,0.9,0.01],['dx','Left / right',-1,1,0.02],['dy','Up / down',-1,1,0.02]]:[['scale','Size',0.5,1.8,0.02],['dx','Left / right',-1,1,0.02],['dy','Up / down',-1,1,0.02],['tolerance','Background cut-out',0.6,1.6,0.05]];
  for(const [k,label,min,max,step] of knobs){
    const row=el('label');const r=el('input');r.type='range';r.min=min;r.max=max;r.step=step;r.value=S2.settings[k];r.oninput=()=>{S2.settings[k]=+r.value;refresh();};row.append(el('span','',label),r);sliders.append(row);
    if(k==='scale'&&job.circular)row.title='At the start the whole tile shows, and it repeats with no seam. Zoom in for bigger shapes: the edges are then blended so it still repeats.';}
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
  /* Applying (again): the picked variant, with its settings, becomes the game art. */
  async function applyNow(){apply.disabled=true;const r=await api('/api/design-apply',{job:job.id,variant:S2.variant,settings:S2.settings});S2.applied=r;S2.live=true;apply.textContent='Applied ✓';
    if(info){info.applied=true;info.variant=S2.variant;keepChat();}S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);return r;}
  apply.onclick=run(async()=>{await applyNow();
    const undoD=el('button','quiet','Undo design');undoD.onclick=run(async()=>{await api('/api/design-undo',{job:job.id});undoD.remove();S2.live=false;apply.disabled=false;apply.textContent='Apply to the game art';if(info){info.applied=false;keepChat();}S.build=null;await status();await library();});
    const inst=el('button','primary',S.status?.active?'Update Mitosis':'Install in Mitosis');inst.onclick=install.onclick;actions.append(undoD,inst);});
  if(info?.applied){apply.disabled=true;apply.textContent='Applied ✓';S2.live=true;}
  card.addEventListener('applied',()=>{S2.live=true;apply.disabled=true;apply.textContent='Applied ✓';if(info){info.applied=true;keepChat();}});   /* by the queue's Apply finished */
  more.onclick=run(()=>drawDesign(box,job.prompt,job.targets,drawings(),null,job.direction,entry,'',job.inspiration));   // same art direction: the new variants match
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
// A saved conversation, drawn again; a request cut off by a reload says so.
function restoreChat(saved){
  log.replaceChildren();J.entries=saved?.entries||[];T.history=saved?.history||[];bubble('bot',WELCOME);if(!J.entries.length)return;
  J.entries.forEach((entry,i)=>{
    if(entry.t==='user'){bubble('user',entry.text);if(J.entries[i+1]?.t!=='bot')bubble('bot','This request was interrupted by a reload. Send it again if you still want it.').classList.add('error');return;}
    const b=bubble('bot',entry.error||entry.text);if(entry.error){b.classList.add('error');return;}
    if(entry.result)try{renderResult(b,entry,true);}catch(e){b.append(el('p','theme-note','This part of the chat could not be shown again.'));}});
  scroll();}
// The chat bar: the open chat's name, which opens this build's chats (search, open, rename, delete), and New chat.
// Chat names are your own words, so they are not translated.
const chatBar=el('div','theme-chats');
const chatPick=el('button','chat-pick');chatPick.id='themeChats';chatPick.type='button';
chatPick.title='Your chats in this build: open, rename or delete one (double-click to rename this one)';
chatPick.setAttribute('aria-haspopup','listbox');chatPick.setAttribute('aria-expanded','false');
const pickTitle=el('b','chat-pick-title','New chat'),pickCount=el('small','chat-pick-count','');
chatPick.append(pickTitle,pickCount,el('span','chat-pick-caret','▾'));
const fresh=el('button','quiet','+ New chat');fresh.id='themeNewChat';fresh.type='button';fresh.title='Start a new conversation. This one stays saved in the list.';
const chatMenu=el('div','chat-menu');chatMenu.id='themeChatMenu';chatMenu.hidden=true;
const chatFind=el('input','chat-find');chatFind.type='search';chatFind.id='themeChatSearch';chatFind.placeholder='Search chats';chatFind.setAttribute('aria-label','Search chats');
const chatRows=el('div','chat-list');chatRows.setAttribute('role','listbox');chatRows.setAttribute('aria-label','Saved chats of this build');
const chatNone=el('p','chat-none','No chats match.');
chatMenu.append(chatFind,chatRows,chatNone);chatBar.append(chatPick,fresh,chatMenu);head.after(chatBar);
const own=(node,title)=>{node.translate=!title||title==='New chat';return node;};   // your own words stay as written
function ago(t){const s=Math.max(0,Date.now()/1000-(+t||0));if(s<60)return 'just now';if(s<3600)return `${Math.floor(s/60)} min ago`;
  if(s<86400)return `${Math.floor(s/3600)} h ago`;if(s<7*86400)return `${Math.floor(s/86400)} d ago`;
  try{return new Date(t*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'});}catch(e){return '';}}
/* The open chat is listed even before its first message (as "New chat", not saved yet). */
function chatList(){return J.list.some(c=>c.id===J.id)?J.list:[{id:J.id,title:J.name||'New chat',named:!!J.name,count:0,unsaved:true},...J.list];}
function chatMeta(c){return c.unsaved?'Not saved yet (say something first)':`${c.asked===1?'1 message':`${c.asked??c.count??0} messages`} · ${ago(c.updated)}`;}
function showChats(){const cur=chatList().find(c=>c.id===J.id),title=cur?.title||'New chat';
  own(pickTitle,title).textContent=title;pickCount.textContent=J.list.length>1?`${J.list.length} chats`:'';
  if(!chatMenu.hidden)renderChats();}
function renderChats(){const q=chatFind.value.trim().toLowerCase();chatFind.hidden=J.list.length<6&&!q;   // search once there are a few
  const rows=chatList().filter(c=>!q||`${c.title} ${c.last||''}`.toLowerCase().includes(q));
  chatRows.replaceChildren(...rows.map(chatRow));chatNone.hidden=rows.length>0;}
function chatRow(c){const row=el('div','chat-row'+(c.id===J.id?' current':''));row.dataset.id=c.id;row.tabIndex=-1;
  row.setAttribute('role','option');row.setAttribute('aria-selected',String(c.id===J.id));
  const text=el('div','chat-row-text');text.append(own(el('b','',c.title||'New chat'),c.title),el('small','',chatMeta(c)));
  if(c.last&&c.last!==c.title)text.append(own(el('span','chat-row-last',`“${c.last}”`),c.last));   // the last request, to tell chats apart
  const ren=el('button','chat-act','✎');ren.type='button';ren.title='Rename this chat (F2)';ren.dataset.act='rename';
  const del=el('button','chat-act','🗑');del.type='button';del.title='Delete this chat (the art it made is not touched)';del.dataset.act='delete';del.disabled=!!c.unsaved;
  row.append(text,ren,del);return row;}
function openChats(){chatMenu.hidden=false;chatPick.setAttribute('aria-expanded','true');chatFind.value='';renderChats();
  (chatFind.hidden?chatRows.querySelector('.current')||chatRows.firstElementChild:chatFind)?.focus();}
function closeChats(focus){chatMenu.hidden=true;chatPick.setAttribute('aria-expanded','false');if(focus)chatPick.focus();}
// Renaming in place: Enter or leaving the box keeps the name, Escape cancels; an empty name goes back to the first request.
function renameChat(row){const c=chatList().find(x=>x.id===row?.dataset.id),name=row?.querySelector('.chat-row-text b');if(!c||!name)return;
  const box=el('input','chat-rename');box.value=c.unsaved?J.name:(c.named?c.title:'');box.placeholder=c.unsaved?'Name this chat':c.title;
  box.maxLength=60;box.setAttribute('aria-label','Chat name');name.replaceWith(box);box.focus();box.select();let done=false;
  const finish=run(async keep=>{if(done)return;done=true;const title=box.value.replace(/\s+/g,' ').trim();
    if(keep&&title!==(c.unsaved?J.name:(c.named?c.title:''))){if(c.unsaved)J.name=title;else J.list=(await api('/api/chat-rename',{id:c.id,title})).chats;}
    showChats();renderChats();chatRows.querySelector(`[data-id="${c.id}"]`)?.focus();});
  box.addEventListener('keydown',e=>{e.stopPropagation();if(e.key==='Enter'){e.preventDefault();finish(true);}else if(e.key==='Escape'){e.preventDefault();finish(false);}});
  box.addEventListener('blur',()=>finish(true));box.addEventListener('click',e=>e.stopPropagation());}
async function deleteChat(id){const c=J.list.find(x=>x.id===id);if(!c||T.busy)return;
  if(!await confirmAction('Delete this chat?','The conversation is removed from this build. Art it applied stays, and Undo still works.','Delete chat'))return;
  if(id===J.id){clearTimeout(J.saveTimer);J.entries=[];}
  J.list=(await api('/api/chat-delete',{id})).chats;
  if(id===J.id){if(J.list.length)await openChat(J.list[0].id);else startChat();}else showChats();
  if(!chatMenu.hidden){renderChats();chatRows.querySelector('.chat-row')?.focus();}}
function startChat(){J.id=newId();J.name='';restoreChat(null);showChats();}
async function openChat(id){await saveChat();try{const doc=await api('/api/chat?'+new URLSearchParams({id}));J.id=id;J.name='';restoreChat(doc);}catch(e){startChat();}showChats();}
fresh.onclick=run(async()=>{if(T.busy)return;closeChats();await saveChat();startChat();input.focus();});
chatPick.onclick=()=>chatMenu.hidden?openChats():closeChats();
chatPick.ondblclick=()=>{if(chatMenu.hidden)openChats();renameChat(chatRows.querySelector('.chat-row.current'));};
chatFind.oninput=renderChats;
chatRows.addEventListener('click',run(async e=>{const row=e.target.closest('.chat-row');if(!row||e.target.closest('.chat-rename'))return;
  const act=e.target.closest('[data-act]')?.dataset.act;if(act==='rename')return renameChat(row);if(act==='delete')return deleteChat(row.dataset.id);
  if(T.busy&&row.dataset.id!==J.id)return message('Wait for the answer, then switch chats.');
  closeChats();if(row.dataset.id!==J.id)await openChat(row.dataset.id);}));
chatMenu.addEventListener('keydown',e=>{const rows=[...chatRows.children],i=rows.indexOf(document.activeElement);
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeChats(true);}
  else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();
    const n=e.key==='ArrowDown'?Math.min(rows.length-1,i+1):i-1;if(n<0&&!chatFind.hidden)chatFind.focus();else rows[Math.max(0,n)]?.focus();}
  else if(e.key==='Enter'&&(i>=0||document.activeElement===chatFind)){e.preventDefault();(rows[i]||rows[0])?.click();}
  else if(e.key==='F2'&&i>=0){e.preventDefault();renameChat(rows[i]);}
  else if(e.key==='Delete'&&i>=0){e.preventDefault();run(deleteChat)(rows[i].dataset.id);}});
document.addEventListener('pointerdown',e=>{if(!chatMenu.hidden&&!chatBar.contains(e.target)&&!$('dialog').contains(e.target))closeChats();});
async function loadChats(){
  let r={active:'',chats:[]};try{r=await api('/api/chats');}catch(e){}J.list=r.chats;
  // A chat from before chats were saved per build becomes this build's first chat.
  let old=null;try{old=JSON.parse(localStorage.getItem(STORE)||'null');}catch(e){}
  if(old?.entries?.length){
    for(let i=0;i<100&&!S.token;i++)await new Promise(r=>setTimeout(r,100));   // saving needs the session from the first status
    J.id=newId();restoreChat(old);if(await saveChat())try{localStorage.removeItem(STORE);}catch(e){}}
  else if(r.active&&J.list.some(c=>c.id===r.active))await openChat(r.active);
  else if(J.list.length)await openChat(J.list[0].id);
  else startChat();
  showChats();}
// AI activity: what the local AI is doing (planning, loading the image model, drawing, finishing) and how much RAM and
// graphics memory (VRAM) are in use, refreshed while Theme AI is open (activity.py).
const act=el('div','theme-activity');act.id='themeActivity';const actDot=el('span','act-dot'),actText=el('span','act-text','');
const meter=(label,tip)=>{const m=el('span','act-meter');m.title=tip;const bar=el('i');m.append(el('b','',label),el('span','act-bar'),el('small'));m.querySelector('.act-bar').append(bar);return m;};
const ramMeter=meter('RAM','Memory in use on this PC'),vramMeter=meter('VRAM','Graphics card memory in use (the image model and the planner both need it)');
act.append(actDot,actText,ramMeter,vramMeter);chatBar.after(act);
const STAGE={idle:'Idle',planning:'Planning',text:'Writing the art direction',loading:'Loading the image model',drawing:'Drawing on the graphics card',finishing:'Finishing the images'};
const gb=b=>(b/2**30).toFixed(1);
function showMeter(m,used,total){m.hidden=!total;if(!total)return;const share=Math.min(1,used/total);m.querySelector('i').style.width=(share*100).toFixed(0)+'%';
  m.classList.toggle('high',share>0.9);m.querySelector('small').textContent=`${gb(used)} / ${gb(total)} GB`;}
async function activity(){if(!T.open||document.hidden)return;let a;try{a=await (await fetch('/api/ai-activity')).json();}catch(e){return;}
  act.dataset.stage=a.stage;actDot.className='act-dot '+a.stage;
  const loaded=(a.loaded||[]).map(m=>m.name.replace(/:latest$/,'')).join(', ');
  actText.textContent=a.stage==='idle'?(loaded?`Idle · ${loaded} loaded`:'Idle'):(a.detail||STAGE[a.stage]||a.stage);
  actDot.translate=false;actDot.title=a.gpu?.name?`${a.gpu.name}${a.gpu.busy!=null?` · ${a.gpu.busy}%`:''}`:'';   // a product name stays as it is
  showMeter(ramMeter,a.ram?.used||0,a.ram?.total||0);showMeter(vramMeter,a.gpu?.used||0,a.gpu?.total||0);}
setInterval(activity,2000);document.addEventListener('visibilitychange',activity);
J.id=newId();showChats();
// Right-click → Redraw with Image AI: the asset (or the checked assets, when it is one of them) drawn anew by the image
// model, by the same rules as Theme AI (a menu background becomes one seamless picture for every game theme).
{const menu=$('contextMenu'),item=el('button','', 'Redraw with Image AI…');item.dataset.action='redraw-ai';item.type='button';menu.prepend(item);
 new MutationObserver(()=>{if(!menu.hidden)item.hidden=!S.asset?.editable;}).observe(menu,{attributes:true,attributeFilter:['hidden']});
 item.onclick=run(async()=>{menu.hidden=true;if(!S.asset?.editable)return;
   const picked=window.LabSelection?.size()?window.LabSelection.keys():[],keys=picked.includes(S.asset.key)?picked:[S.asset.key];
   const name=keys.length>1?`${keys.length} checked assets`:(S.asset.label||S.asset.name);
   const look=prompt((window.LabI18n?LabI18n.t:x=>x)(`Describe the new look for “${name}” (for example “a neon city at night”, “made of gold”):`),'');
   if(!look||!look.trim())return;
   if(!T.open)toggle(true);
   await ask(`Redraw ${name}: ${look.trim()}`,{only:keys,draw:'on'});});}
window.LabThemeChat={reset(){clearTimeout(J.saveTimer);J.saveTimer=0;J.list=[];startChat();}};   // tests: forget the open chat
run(loadChats)();try{if(localStorage.getItem('lab-theme-open'))toggle(true);}catch(e){}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&T.open&&!$('dialog').open&&document.activeElement!==input)toggle(false);});

// Recovery: an install that Steam or the game's cache has overwritten can no longer be restored normally.
const clear=el('button','', 'Clear stale install');clear.id='abandonButton';clear.hidden=true;clear.title='Steam or the game replaced a modded file. Close the install record and keep the current files.';
$('restoreButton').after(clear);
new MutationObserver(()=>{const stuck=S.status?.stuck||[];clear.hidden=!stuck.length;if(stuck.length)$('modStatus').textContent='Something outside the lab replaced '+stuck.join(', ')+'. Restore cannot overwrite it; use Clear stale install to move on.';}).observe($('modTitle'),{childList:true,characterData:true,subtree:true});
clear.onclick=run(async()=>{if(!await confirmAction('Clear this install record?','Files that still hold your mod are restored. Files that Steam or the game already replaced are left exactly as they are and become the new starting point. Your original backups stay in the workspace.','Clear record'))return;await api('/api/abandon-install',{});message('Install record cleared. Restart the lab to rescan the updated files.');await status();});
})();
