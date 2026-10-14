'use strict';
// Theme AI: describe a look, review the plan and samples, apply it, then install it into Mitosis from the same panel.
// Uses app.js globals: $, api, message, status, library, select, run, confirmAction, S.
(()=>{
const T={open:false,info:null,history:[],busy:false,job:null,plan:null,prompt:'',passing:false,game:null};
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const button=el('button','theme-open','✦ Theme AI');button.id='themeButton';button.title='Change the whole look from a prompt';
document.querySelector('.topbar').insertBefore(button,$('workspaceButton'));
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
for(const [label,words] of [['Parts','buttons · panels · icons · logo · cells · food · equipment · potions · effects · fonts · menu background · arena — or a sprite or your own name for it: “the white virus”, “the speed potion”'],
  ['Edge','outline · border · rim · ring (thin / thick)'],['Inside','inside · fill · body — recolor, gradient, darker, brighter'],
  ['Light','glow (subtle / strong / inner) · flare · sparkle · glossy'],
  ['Gradients','rainbow · sunset · synthwave · fire · lava · ocean · ice · aurora · galaxy · toxic · candy · gold · chrome · pastel · holographic · “from purple to cyan” · vertical · radial · striped'],
  ['Moods','spooky · christmas · winter · forest · royal · cyberpunk · bloody · midnight — a matching palette for everything or the parts you name'],
  ['Redraw','“make the title spooky” · “put a top hat on the logo” · “add bats and dripping blood” · “turn the logo into graffiti” — drawn by the local image model; you pick a variant and adjust it'],
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
actions.append(review,install,restore,launch,gameButton,undo);dock.append(modLine,actions);

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
function showPlan(box,result,prompt,keys){
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
  if(preview.samples.length){const grid=el('div','theme-samples');for(const s of preview.samples){const fig=el('button','theme-sample');fig.title='Open '+s.name;const a=new Image(),b=new Image();a.src=s.before;b.src=s.after;a.alt='before';b.alt='after';fig.append(a,el('span','','→'),b,el('small','',s.name));fig.onclick=run(()=>select(s.key));grid.append(fig);}box.append(grid);}
  const colorsOnly=!preview.sheets&&code;
  const bar=el('div','theme-actions');const apply=el('button','primary',colorsOnly?(preview.ui_colors?.length?'Apply game colors':'Apply'):'Apply');apply.disabled=!preview.sheets&&!colorsOnly;
  const scope=[preview.sheets?`up to ${preview.sheets} sheet${preview.sheets===1?'':'s'}`:'',preview.ui_colors?.length?'game colors':'',preview.hide?.length?'menu':'',keys?`${keys.length} checked only`:''].filter(Boolean).join(' + ');
  apply.onclick=run(()=>applyPlan(plan,prompt,apply,keys));bar.append(apply,el('small','hint',`${scope} · ${({rules:'keyword planner',tags:'exact, from your words'}[result.source]||result.source)}`));box.append(bar);
  T.plan=plan;T.prompt=prompt;
}
async function refresh(){
  T.info=await api('/api/theme-status');
  const chosen=model.value;model.replaceChildren();
  for(const m of T.info.models){const o=el('option','',m.name);o.value=m.name;model.append(o);}
  if(!T.info.models.length){const o=el('option','','Keyword planner');o.value='';model.append(o);}
  if(chosen&&[...model.options].some(o=>o.value===chosen))model.value=chosen;
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
// Installing or restoring replaces game files, so Mitosis has to be closed first: offer to close it.
async function closedFirst(what){
  if(!await checkGame())return true;
  if(!await confirmAction('Mitosis is open',`${what} replaces game files, so Mitosis has to be closed first. Close it now? Any match in progress ends.`,'Close Mitosis and continue'))return false;
  const r=await api('/api/close-game',{});await checkGame();
  if(!r.closed){message('Mitosis did not close. Close it yourself, then try again.',true);return false;}
  return true;}
async function pass(target,what){if(!await closedFirst(what))return;T.passing=true;try{target.click();}finally{T.passing=false;}}
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
  bubble('user',text);input.value='';setBusy(true);
  const wait=bubble('bot','Thinking…');const started=Date.now();const tick=setInterval(()=>{wait.firstChild.textContent=`Thinking… ${Math.round((Date.now()-started)/1000)}s`+(Date.now()-started>12000?' (the first request loads the model)':'');},1000);
  try{
    const keys=only();
    const result=await api('/api/theme-chat',{message:text,model:model.value,history:T.history,only:keys});
    clearInterval(tick);wait.firstChild.textContent=result.plan.reply||'Here is the plan.';
    if(result.plan.operations.length||Object.keys(result.plan.ui_colors||{}).length||result.plan.hide?.length){T.history.push({role:'user',content:text},{role:'assistant',content:JSON.stringify(result.plan)});T.history=T.history.slice(-8);}
    if(result.undone){S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);await refresh();}
    const planned=result.plan.operations.length||Object.keys(result.plan.ui_colors||{}).length||result.plan.hide?.length;
    if(result.design&&!planned&&!result.design.optional){showDesign(wait,result.design,keys);scroll();return;}
    showPlan(wait,result,text,keys);
    if(result.design){const offer=el('div','design-offer');offer.append(el('p',''));wait.append(offer);showDesign(offer,result.design,keys,null,true);}
    scroll();
    if(autoBox.checked&&result.preview&&(result.preview.sheets||result.preview.ui_colors?.length||result.preview.hide?.length)){setBusy(false);await applyPlan(result.plan,text,wait.querySelector('.theme-actions button'),keys);}
  }catch(e){clearInterval(tick);wait.classList.add('error');wait.firstChild.textContent=e.message;}
  finally{setBusy(false);}
}
// ----- design: redraw art with the local image model, then pick, adjust and apply ---------------------
const GAME_BG='#0D2829';
function showDesign(box,d,keys,base,offer){
  // One drawing per distinct sprite; the title's sizes share one drawing.
  const groups=[],title=d.targets.filter(t=>t.name==='logo');if(title.length)groups.push(title);
  for(const t of d.targets)if(t.name!=='logo')groups.push([t]);
  const label=g=>g[0].name==='logo'?'the MITOSIS title':g[0].name;
  const what=groups.map(label).join(', ')+(title.length>1?' (every size the game uses)':'');
  box.firstChild.textContent=!d.available?(offer?'':`This needs the image model, which is not installed.`):
    d.optional?`Want ${what} redrawn to match? The local image model can draw it.`:
    offer?`The rest needs drawing: I'll redraw ${what} with the local image model.`:`I'll redraw ${what} with the local image model. Pick a variant, adjust it, then apply.`;
  if(!d.available)return;
  const bar=el('div','theme-actions');const count=el('select');for(const n of [1,2,3,4]){const o=el('option','',`${n} variant${n>1?'s':''}`);o.value=n;count.append(o);}count.value=2;
  const go=el('button','primary',groups.length>1?`Draw all ${groups.length}`:'Draw it');
  bar.append(go,count,el('small','hint',`about ${35*groups.length} s per variant`));box.append(bar);
  go.onclick=run(async()=>{go.disabled=true;count.disabled=true;
    let direction=null;   // the first drawing's art direction is reused, so a set comes out in one style
    for(const g of groups){const part=el('div','design-part');if(groups.length>1)part.append(el('p','design-part-title',label(g)));box.append(part);
      const done=await drawDesign(part,d.prompt,g.map(t=>t.key),+count.value,base,direction);if(done&&done.direction&&!direction)direction=done.direction;}});
  if(d.optional)go.textContent='Redraw it';
  if(autoBox.checked&&!d.optional)go.click();
}
async function drawDesign(box,prompt,keys,count,base,direction){
  setBusy(true);const text=el('small','hint','Starting…');const barEl=el('div','theme-progress');const fill=el('span');barEl.append(fill);box.append(barEl,text);scroll();
  let job=null;const stop=el('button','quiet','Cancel');stop.onclick=run(async()=>{if(job)await api('/api/design-cancel',{job:job.id});});text.after(stop);
  job=await api('/api/design-start',{prompt,only:keys||null,count,base:base||null,direction:direction||null});const started=Date.now();
  try{while(job.state==='running'){await new Promise(r=>setTimeout(r,1000));job=await api('/api/design-job?job='+job.id);
      const m=job.message.match(/step (\d+) of (\d+)/);fill.style.width=(m?20+70*m[1]/m[2]:job.message.startsWith('Finishing')?92:8)+'%';
      text.textContent=`${job.message} · ${Math.round((Date.now()-started)/1000)} s`;}}
  finally{setBusy(false);stop.remove();}
  barEl.classList.add('done');text.textContent=job.message;
  if(job.state!=='complete'){box.classList.add('error');return job;}
  designCard(box,job,keys);scroll();return job;
}
function designCard(box,job,keys){
  const card=el('div','design-card');box.append(card);
  const S2={variant:0,settings:{scale:1,dx:0,dy:0,tolerance:1,shape:job.stretched?1:0,fill:job.title?1:0},applied:null};
  if(job.tinted)card.append(el('p','theme-note','This icon is plain white because the game colors it (teal in the menu), so a colorful drawing will come out tinted. Light, mostly white designs keep their look.'));
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
  let timer;function refresh(){clearTimeout(timer);timer=setTimeout(()=>frames.forEach((im,t)=>im.src=url(t)),150);}
  const sliders=el('div','design-sliders');
  for(const [k,label,min,max,step] of [['scale','Size',0.5,1.8,0.02],['dx','Left / right',-1,1,0.02],['dy','Up / down',-1,1,0.02],['tolerance','Background cut-out',0.6,1.6,0.05]]){
    const row=el('label');const r=el('input');r.type='range';r.min=min;r.max=max;r.step=step;r.value=S2.settings[k];r.oninput=()=>{S2.settings[k]=+r.value;refresh();};row.append(el('span','',label),r);sliders.append(row);}
  const shapeRow=el('label','design-shape');const shape=el('input');shape.type='checkbox';shape.checked=!!S2.settings.shape;
  shape.onchange=()=>{S2.settings.shape=shape.checked?1:0;refresh();};shapeRow.title='Trim the drawing to the original outline, so the game places and stretches it the same way';
  shapeRow.append(shape,document.createTextNode(' Keep the original shape'));sliders.append(shapeRow);
  const fillRow=el('label','design-shape');const fill=el('input');fill.type='checkbox';fill.checked=!!S2.settings.fill;
  fill.onchange=()=>{S2.settings.fill=fill.checked?1:0;refresh();};fillRow.title='Size the drawing by its main shape so it fills the frame like the original; sparse extras at the edges may be trimmed';
  fillRow.append(fill,document.createTextNode(' Fill the frame'));sliders.append(fillRow);
  const actions=el('div','theme-actions');const apply=el('button','primary','Apply to the game art');const more=el('button','','More variants');
  const refine=el('input');refine.placeholder='Refine this one… e.g. “bigger bats, less blood”';refine.className='design-refine';const again=el('button','','Refine');
  apply.onclick=run(async()=>{apply.disabled=true;const r=await api('/api/design-apply',{job:job.id,variant:S2.variant,settings:S2.settings});S2.applied=r;apply.textContent='Applied ✓';
    const undoD=el('button','quiet','Undo design');undoD.onclick=run(async()=>{await api('/api/design-undo',{job:job.id});undoD.remove();apply.disabled=false;apply.textContent='Apply to the game art';S.build=null;await status();await library();});
    const inst=el('button','primary',S.status?.active?'Update Mitosis':'Install in Mitosis');inst.onclick=install.onclick;actions.append(undoD,inst);
    S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);});
  more.onclick=run(()=>drawDesign(box,job.prompt,job.targets,2,null,job.direction));   // same art direction: the new variants match
  again.onclick=run(()=>{const extra=refine.value.trim();if(!extra)return refine.focus();return drawDesign(box,extra,null,2,[job.id,S2.variant]);});
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
  try{const job=await api('/api/theme-apply',{plan,prompt,only:keys||null});await follow(job.id,control?control.closest('.theme-bubble'):bubble('bot','Applying…'),control);}
  catch(e){if(control)control.disabled=false;throw e;}
}
form.onsubmit=e=>{e.preventDefault();run(()=>ask(input.value))();};
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit();}});
undo.onclick=run(async()=>{if(!await confirmAction('Undo the last theme?','Every sheet it changed goes back to how it was just before. Sheets you edited by hand afterwards are left alone.','Undo theme'))return;
  setBusy(true);try{const r=await api('/api/theme-undo',{});bubble('bot',`Undid “${r.prompt.slice(0,80)}”: ${r.reverted} sheet(s) restored${r.skipped?`, ${r.skipped} left alone`:''}.`);T.history=[];S.build=null;await status();await library();if(S.asset&&!S.dirty)await select(S.asset.key,true);}finally{setBusy(false);await refresh();}});
function toggle(open){T.open=open;drawer.hidden=!open;showScope();document.body.classList.toggle('theme-on',open);button.classList.toggle('active',open);
  if(open){run(refresh)();checkGame();input.focus();if(!log.childElementCount)bubble('bot','Describe the look you want: a color, a mood, or parts like buttons, panels, icons, cells, the logo or the menu pattern. I show a plan with samples; Apply saves it, and Install in Mitosis (below) puts it in the game. Say “undo” to take a theme back off, or “help” for more.');}}
button.onclick=()=>toggle(!T.open);close.onclick=()=>toggle(false);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&T.open&&!$('dialog').open&&document.activeElement!==input)toggle(false);});

// Recovery: an install that Steam or the game's cache has overwritten can no longer be restored normally.
const clear=el('button','', 'Clear stale install');clear.id='abandonButton';clear.hidden=true;clear.title='Steam or the game replaced a modded file. Close the install record and keep the current files.';
$('restoreButton').after(clear);
new MutationObserver(()=>{const stuck=S.status?.stuck||[];clear.hidden=!stuck.length;if(stuck.length)$('modStatus').textContent='Something outside the lab replaced '+stuck.join(', ')+'. Restore cannot overwrite it; use Clear stale install to move on.';}).observe($('modTitle'),{childList:true,characterData:true,subtree:true});
clear.onclick=run(async()=>{if(!await confirmAction('Clear this install record?','Files that still hold your mod are restored. Files that Steam or the game already replaced are left exactly as they are and become the new starting point. Your original backups stay in the workspace.','Clear record'))return;await api('/api/abandon-install',{});message('Install record cleared. Restart the lab to rescan the updated files.');await status();});
})();
