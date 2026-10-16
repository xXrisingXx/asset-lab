'use strict';
// Sounds & music: replace a game sound with a file from this PC or with a link (YouTube, SoundCloud, Bandcamp, or a direct
// .mp3/.ogg/.wav link). Trim it with the handles on the waveform, fade it in and out, set its volume or match the game's
// loudness, compare it with the original (A/B) and save it into the mod. The lab renders the preview exactly the way it
// will be installed. Opens when a sound is selected: the editor's 'lab:asset' event, or (older app.js) the #audio
// player's src and #nonvisual's hidden attribute. Uses app.js's message, status, library and confirmAction when present.
(()=>{
const $=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(text,cls,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const clock=s=>{s=Math.max(0,+s||0);const m=Math.floor(s/60),d=s<1?2:1;return `${m}:${(s-m*60).toFixed(d).padStart(d+3,'0')}`;};   // 0:00.15 under a second
const size=n=>n>=1e6?(n/1e6).toFixed(1)+' MB':Math.max(1,Math.round(n/1e3))+' KB';
const kHz=r=>(r%1000?(r/1000).toFixed(2).replace(/0+$/,''):String(r/1000))+' kHz';
const chans=c=>c===1?'mono':c===2?'stereo':c+' channels';
const db=v=>(v>0?'+':v<0?'−':'±')+Math.abs(+v).toFixed(1)+' dB';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const say=(text,error=false)=>{if(typeof message==='function')message(text,error);};
const HOSTS={'github.com':'GitHub','files.pythonhosted.org':'PyPI'};
const A={key:'',info:null,src:null,set:null,saved:'',prev:null,report:null,job:null,mode:'new',playing:null,playStart:0,drag:null,kept:{},names:{},seen:false,token:'',raf:0,timers:{},origKey:'',origUrl:''};
const orig=new Audio(),play=new Audio();orig.preload=play.preload='auto';

// ----- the panel ---------------------------------------------------------------------------------------------------
const root=el('section','audio-editor');root.hidden=true;root.setAttribute('aria-label','Sound editor');
// One row for the game's sound (play, name, waveform, state) and one for "Replace it" (file or link), so the editor and
// Save to mod fit on a laptop screen; the actions row stays at the bottom of the panel while it scrolls (audio.css).
const game=el('div','ae-card ae-game'),gameRow=el('div','ae-row'),gamePlay=btn('▶','ae-round','Play the game’s sound');gamePlay.id='aeGamePlay';
const gameTitles=el('div','ae-titles'),gameName=el('b'),gameInfo=el('small'),badge=el('span','ae-badge','Original');badge.id='aeBadge';
const mini=el('canvas','ae-mini');mini.id='aeOrigWave';mini.setAttribute('aria-label','Waveform of the game’s sound');
gameTitles.append(el('div','section-label','THE GAME’S SOUND'),gameName,gameInfo);gameRow.append(gamePlay,gameTitles,mini,badge);game.append(gameRow);

const replace=el('div','ae-card ae-replace'),fileRow=el('div','ae-row'),choose=btn('Choose a file…','','Pick a sound file on this PC');choose.id='aeChoose';
const file=el('input');file.type='file';file.id='audioFile';file.hidden=true;file.accept='audio/*,video/*,.ogg,.oga,.opus,.mp3,.wav,.flac,.aif,.aiff,.m4a,.aac,.wma,.webm,.mp4';
const linkForm=el('form','ae-link'),link=el('input');link.type='url';link.id='aeLink';link.placeholder='or paste a link: YouTube, SoundCloud, Bandcamp, or a .mp3 / .ogg / .wav file';
link.setAttribute('aria-label','Link to a sound or a page with one');link.autocomplete='off';link.spellcheck=false;linkForm.noValidate=true;   // our own check explains itself
const get=el('button','primary','Get');get.type='submit';get.id='aeGet';linkForm.append(link,get);
fileRow.append(choose,linkForm,file);
const prog=el('div','ae-progress'),bar=el('div','ae-bar'),barFill=el('i'),progText=el('span'),cancel=btn('Cancel','quiet');prog.hidden=true;prog.id='aeProgress';
bar.append(barFill);prog.append(bar,progText,cancel);
const consentBox=el('div','ae-consent');consentBox.hidden=true;consentBox.id='aeConsent';
const problemBox=el('div','ae-problem');problemBox.hidden=true;problemBox.id='aeProblem';problemBox.setAttribute('role','alert');
const helperRow=el('div','ae-helper');helperRow.id='aeHelper';
replace.append(el('div','section-label','REPLACE IT'),fileRow,prog,consentBox,problemBox,el('p','hint ae-rights','You can also drop a sound file here: MP3, OGG, WAV, FLAC and more. Only use music you have the right to use.'),helperRow);

const edit=el('div','ae-card ae-edit');edit.hidden=true;
const srcTitles=el('div','ae-titles'),srcName=el('b'),srcInfo=el('small');srcName.id='aeSrcName';srcInfo.id='aeSrcInfo';srcTitles.append(el('div','section-label','YOUR SOUND'),srcName,srcInfo);
const wave=el('canvas','ae-wave');wave.id='aeWave';wave.tabIndex=0;wave.setAttribute('aria-label','Waveform: drag the yellow handles to choose the part to use, click inside to play from there');
const times=el('div','ae-times'),start=el('input'),end=el('input'),length=el('span','ae-length'),now=el('span','ae-now');
for(const [i,id] of [[start,'aeStart'],[end,'aeEnd']]){i.type='number';i.id=id;i.step='0.01';i.min='0';}
times.append('Start',start,'End',end,length,now);
const transport=el('div','ae-transport'),playBtn=btn('▶ Play','ae-play');playBtn.id='aePlay';
const ab=el('div','segmented ae-ab'),abGame=btn('Game’s sound','','Listen to the game’s own sound'),abNew=btn('Yours','active','Listen to your new sound');
abGame.dataset.mode='orig';abNew.dataset.mode='new';ab.append(abGame,abNew);ab.setAttribute('aria-label','Compare: the game’s sound or yours');
// Loop: both players (the game's sound and yours) start over at the end until stopped. Remembered between visits.
const loopBtn=btn('⟲ Loop','ae-loop','Play the sound again and again until you stop it');loopBtn.id='aeLoop';
function setLoop(on){A.loop=!!on;orig.loop=play.loop=A.loop;loopBtn.classList.toggle('active',A.loop);loopBtn.setAttribute('aria-pressed',String(A.loop));
  try{localStorage.setItem('lab-audio-loop',A.loop?'1':'');}catch(e){}}
loopBtn.onclick=()=>setLoop(!A.loop);badge.before(loopBtn);   // beside the game's sound, so it is there before you pick your own
const working=el('span','ae-note');working.id='aeWorking';transport.append(playBtn,ab,working);
const controls=el('div','ae-controls');
function slider(label,id,min,max,step){const l=el('label'),head=el('span','',label),out=el('output'),r=el('input');r.type='range';r.id=id;r.min=min;r.max=max;r.step=step;head.append(out);l.append(head,r);controls.append(l);return [r,out,head.firstChild];}
const [gain,gainOut,gainLabel]=slider('Volume ','aeGain',-24,12,0.5),[fadeIn,inOut]=slider('Fade in ','aeIn',0,10,0.05),[fadeOut,outOut]=slider('Fade out ','aeOut',0,10,0.05);
const matchRow=el('label','ae-check'),match=el('input');match.type='checkbox';match.id='aeMatch';match.checked=true;matchRow.append(match,el('span','','Match the game’s loudness'));controls.append(matchRow);
const warnings=el('ul','ae-warnings');warnings.id='aeWarnings';const result=el('p','ae-info');result.id='aeResult';
edit.append(srcTitles,wave,times,transport,controls,warnings,result);
const actions=el('div','ae-actions'),save=el('button','primary','Save to mod');save.type='button';save.id='aeSave';
const original=btn('Use the original sound','quiet','Leave this sound out of your mod: the game plays its own sound');original.id='aeOriginal';
const note=el('p','ae-note');note.id='aeNote';actions.append(save,original,note);
root.append(game,replace,edit,actions);

// In the studio's #nonvisual area; over the viewport itself if a future layout has no such area.
function mount(){const host=$('nonvisual')||$('viewport')||document.querySelector('.viewport');if(!host)return false;
  root.classList.toggle('ae-float',host.id!=='nonvisual');if(root.parentNode!==host)host.prepend(root);return true;}
function show(on){root.hidden=!on;const host=root.parentNode;if(host&&host.id==='nonvisual')host.classList.toggle('ae-on',on);
  const view=host&&(host.closest('.viewport')||host);if(view&&view.classList)view.classList.toggle('ae-open',on);if(!on)stop(false);}

// ----- talking to the lab ------------------------------------------------------------------------------------------
async function token(){if(typeof S!=='undefined'&&S&&S.token)return S.token;if(!A.token)A.token=(await (await fetch('/api/status')).json()).token;return A.token;}
async function call(path,data){
  const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Token':await token()},body:JSON.stringify(data)};
  const r=await fetch(path,options);let j;try{j=await r.json();}catch(e){j={error:'The lab did not answer. Is it still running?'};}
  if(!r.ok){const e=Error(j.error||'Something went wrong.');e.code=j.code||'';throw e;}return j;}
async function refresh(){try{if(typeof S!=='undefined'&&S)S.build=null;if(typeof status==='function')await status();if(typeof library==='function')await library();}catch(e){}}
const keyOf=()=>A.src?A.src.id+'|'+JSON.stringify(A.set):'';
// A new sound starts with Settings › Sound: match the game's loudness, fade in, fade out (fades that would not fit the
// sound are shortened together, so a short effect gets a shorter fade).
const pref=(k,d)=>{const v=window.LabSettings?LabSettings.get(k):undefined;return v===undefined||v===null?d:v;};
const defaults=src=>{const end=+(+src.duration).toFixed(4),s={start:0,end,gain:0,fade_in:Math.max(0,+pref('sound_fade_in',0)||0),fade_out:Math.max(0,+pref('sound_fade_out',0)||0),match:!!pref('sound_match',true),quality:0.5};
  const k=s.fade_in+s.fade_out>end?end/(s.fade_in+s.fade_out):1;s.fade_in=+(s.fade_in*k).toFixed(4);s.fade_out=+(s.fade_out*k).toFixed(4);return s;};

async function open(key){
  if(!mount())return;
  if(key!==A.key){stop(false);keep();Object.assign(A,{key,info:null,src:null,set:null,prev:null,report:null,saved:'',mode:'new'});problem('');notice('');consentBox.hidden=true;render();}
  show(true);
  const info=await call('/api/audio?key='+encodeURIComponent(key));if(key!==A.key)return;
  A.info=info;A.names[key]=info.name;const kept=A.kept[key];
  A.saved=info.edit&&info.chosen?info.chosen.id+'|'+JSON.stringify(info.edit.settings):'';
  if(kept){A.src=kept.src;A.set=kept.set;}else if(!A.src&&info.edit&&info.chosen){A.src=info.chosen;A.set={...info.edit.settings};}
  if(!A.src)A.mode='orig';
  loadOriginal(key);render();if(A.src)changed(false);}
function keep(){if(A.key&&A.src&&keyOf()!==A.saved)A.kept[A.key]={src:A.src,set:A.set};else if(A.key)delete A.kept[A.key];}
async function loadOriginal(key){if(A.origKey===key)return;A.origKey=key;
  try{const blob=await (await fetch('/api/raw?key='+encodeURIComponent(key))).blob();if(A.origKey!==key)return;if(A.origUrl)URL.revokeObjectURL(A.origUrl);A.origUrl=URL.createObjectURL(blob);orig.src=A.origUrl;}catch(e){A.origKey='';}}

// ----- what the panel shows ----------------------------------------------------------------------------------------
function render(){
  const info=A.info,o=info&&info.original;
  gameName.textContent=info?(info.label?`${info.label} (${info.name})`:info.name):'Loading the sound…';
  gameInfo.textContent=o?[clock(o.duration),chans(o.channels),kHz(o.rate),size(o.bytes)].join(' · '):'';
  const unsaved=!!A.src&&keyOf()!==A.saved,edit0=info&&info.edit;
  badge.className='ae-badge'+(unsaved?' unsaved':edit0?' saved':'');
  badge.textContent=unsaved?'Not saved yet':edit0?(edit0.staged?(edit0.installed?'Your sound · installed':'Your sound · in your mod'):'Your sound · left out of the mod'):'The game’s sound';
  const doc=$('dirtyBadge');if(doc&&!root.hidden)doc.textContent=unsaved?'Unsaved sound':edit0?'Saved sound':'Original';
  edit.hidden=!A.src;
  if(A.src){const s=A.src;srcName.textContent=s.name||s.title||'Your sound';
    srcInfo.textContent=[clock(s.duration),chans(s.channels),kHz(s.rate),s.via==='helper'&&s.license?s.license:'',s.cut?`only the first ${clock(s.duration)} of ${clock(s.cut)} is used`:''].filter(Boolean).join(' · ');
    const len=A.set.end-A.set.start,fine=len<2;
    start.max=end.max=(+s.duration).toFixed(2);if(document.activeElement!==start)start.value=A.set.start.toFixed(2);if(document.activeElement!==end)end.value=A.set.end.toFixed(2);
    length.textContent='Length '+clock(len);
    for(const [r,v] of [[fadeIn,A.set.fade_in],[fadeOut,A.set.fade_out]]){r.max=Math.max(0.01,Math.min(10,len)).toFixed(2);r.step=fine?'0.01':'0.05';r.value=v;}
    inOut.textContent=A.set.fade_in.toFixed(fine?2:1)+' s';outOut.textContent=A.set.fade_out.toFixed(fine?2:1)+' s';
    gain.value=A.set.gain;gainOut.textContent=db(A.set.gain);gainLabel.textContent=A.set.match?'Volume after matching ':'Volume ';gain.title=A.set.match?'0 dB: as loud as the game’s sound':'0 dB: as loud as your file';
    const silent=o&&o.loudness==null;match.disabled=!!silent;match.checked=A.set.match&&!silent;matchRow.title=silent?'The game’s sound is silent, so there is no loudness to match.':'Sets your sound as loud as the game’s, then the volume on top';}
  else{A.mode='orig';}
  for(const b of [abGame,abNew]){const on=b.dataset.mode===A.mode;b.classList.toggle('active',on);b.setAttribute('aria-pressed',on);}
  abNew.disabled=!A.src;
  gamePlay.textContent=A.playing==='orig'?'■':'▶';gamePlay.title=A.playing==='orig'?'Stop':'Play the game’s sound';
  playBtn.textContent=A.playing?'■ Stop':'▶ Play';playBtn.title=A.mode==='orig'?'Play the game’s sound':'Play your sound as it will be in the game';
  save.disabled=!A.src||(!unsaved&&!!edit0&&edit0.staged);save.textContent=A.src&&!unsaved&&edit0&&!edit0.staged?'Put back in the mod':'Save to mod';
  original.disabled=!A.src&&!edit0;
  helper();draw();}
function helper(){helperRow.replaceChildren();const h=A.info&&A.info.helper;if(!h||!h.ready)return;
  const up=btn('Update the link helper','link','Get the newest yt-dlp: websites change, and new versions keep up');up.onclick=()=>updateHelper();
  helperRow.append(el('span','hint','Link helper ready. '),up);}
function showReport(){const r=A.report;warnings.replaceChildren();if(!r||!A.src){result.textContent='';return;}
  for(const w of r.warnings||[]){const li=el('li','',w);
    if(/Trim it shorter/.test(w)&&A.info){const d=Math.max(0.05,A.info.original.duration),b=btn(`Use just ${clock(d)}`,'link');b.onclick=()=>{A.set.end=Math.min(A.src.duration,A.set.start+d);fit();changed();};li.append(' ',b);}
    warnings.append(li);}
  const parts=[`In the game: ${clock(r.duration)}`,chans(r.channels),kHz(r.rate),'about '+size(r.estimate)];
  if(r.match_db!=null)parts.push(`as loud as the game’s sound${r.settings&&r.settings.gain?', then '+db(r.settings.gain):''} (${db(r.gain_db)} in all)`);
  else if(r.gain_db)parts.push(db(r.gain_db));
  result.textContent=parts.join(' · ');}
function problem(text,code){problemBox.replaceChildren();problemBox.hidden=!text;if(!text)return;problemBox.append(el('span','',text));
  if(code==='outdated'&&A.info&&A.info.helper&&A.info.helper.ready){const b=btn('Update the link helper','link');b.onclick=()=>updateHelper();problemBox.append(' ',b);}}
function notice(text){note.textContent=text||'';}
function progress(fraction,text,cancellable){prog.hidden=false;progText.textContent=text||'';bar.classList.toggle('busy',fraction==null);barFill.style.width=fraction==null?'':Math.round(fraction*100)+'%';cancel.hidden=!cancellable;}
function idle(){prog.hidden=true;}

// ----- the waveforms -----------------------------------------------------------------------------------------------
function fit(){const d=A.src.duration,s=A.set;s.start=Math.max(0,Math.min(s.start,d-0.02));s.end=Math.max(s.start+0.02,Math.min(s.end,d));
  const len=s.end-s.start;if(s.fade_in+s.fade_out>len){const k=len/(s.fade_in+s.fade_out);s.fade_in*=k;s.fade_out*=k;}}
function canvasSize(c){const dpr=Math.min(devicePixelRatio||1,2),w=c.clientWidth,h=c.clientHeight;if(!w||!h)return null;
  if(c.width!==Math.round(w*dpr)||c.height!==Math.round(h*dpr)){c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);}const g=c.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,w,h);return [g,w,h];}
function bars(g,peaks,w,h,color,from=0,to=w,out='#33404f'){const mid=h/2,n=peaks.length;
  for(let x=0;x<w;x++){let p=0;const i0=Math.floor(x/w*n),i1=Math.max(i0+1,Math.floor((x+1)/w*n));for(let i=i0;i<i1&&i<n;i++)if(peaks[i]>p)p=peaks[i];
    const y=Math.max(0.5,Math.sqrt(p)*(mid-4));g.fillStyle=x>=from&&x<=to?color:out;g.fillRect(x,mid-y,1,y*2);}}
function draw(){
  const m=canvasSize(mini);if(m&&A.info){const [g,w,h]=m,o=A.info.original;g.fillStyle='#0f161f';g.fillRect(0,0,w,h);bars(g,o.peaks||[],w,h,'#6b8aa3');
    if(A.playing==='orig'){const x=orig.currentTime/o.duration*w;g.fillStyle='#fff';g.fillRect(x,0,1.5,h);}}
  const c=A.src&&canvasSize(wave);if(!c){now.textContent='';return;}
  const [g,w,h]=c,d=A.src.duration,s=A.set,x0=s.start/d*w,x1=s.end/d*w,mid=h/2;
  g.fillStyle='#0f161f';g.fillRect(0,0,w,h);g.fillStyle='#132c35';g.fillRect(x0,0,x1-x0,h);
  bars(g,A.src.peaks||[],w,h,'#70e5d4',x0,x1);
  // the volume: fades as a line over the part that is used
  const fi=s.fade_in/d*w,fo=s.fade_out/d*w,top=6;g.strokeStyle='#e4edabcc';g.lineWidth=1.5;g.beginPath();
  const curve=(t)=>0.5-0.5*Math.cos(Math.PI*t);g.moveTo(x0,mid);
  for(let i=0;i<=24;i++)g.lineTo(x0+fi*i/24,mid-(mid-top)*curve(i/24));g.lineTo(x1-fo,top);for(let i=0;i<=24;i++)g.lineTo(x1-fo+fo*i/24,mid-(mid-top)*curve(1-i/24));g.stroke();
  for(const x of [x0,x1]){g.fillStyle='#e4edab';g.fillRect(x-1,0,2,h);g.beginPath();g.roundRect(x-5,mid-14,10,28,4);g.fill();g.fillStyle='#0f161f';g.fillRect(x-1.5,mid-6,1,12);g.fillRect(x+0.5,mid-6,1,12);}
  let t=null;if(A.playing==='new')t=A.playStart+play.currentTime;else if(A.playing==='orig'&&orig.currentTime<=s.end-s.start)t=s.start+orig.currentTime;
  if(t!=null){const x=t/d*w;g.fillStyle='#fff';g.fillRect(x-0.75,0,1.5,h);}
  now.textContent=A.playing?'▶ '+clock(A.playing==='new'?play.currentTime:orig.currentTime):'';}
function tick(){cancelAnimationFrame(A.raf);const step=()=>{draw();if(A.playing)A.raf=requestAnimationFrame(step);};A.raf=requestAnimationFrame(step);}
new ResizeObserver(()=>{if(!root.hidden)draw();}).observe(root);

// Trim handles: drag them; click outside the part to move the nearest handle there; click inside to play from there.
const at=e=>{const r=wave.getBoundingClientRect();return {x:e.clientX-r.left,w:r.width,t:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*A.src.duration};};
function nearHandle(p){const d=A.src.duration,x0=A.set.start/d*p.w,x1=A.set.end/d*p.w,a=Math.abs(p.x-x0),b=Math.abs(p.x-x1);
  if(Math.min(a,b)>10)return null;return a<b||(a===b&&p.x<x0)?'start':'end';}
wave.addEventListener('pointermove',e=>{if(!A.src)return;const p=at(e);if(A.drag){A.drag.moved=true;setEdge(A.drag.edge,p.t);return;}wave.style.cursor=nearHandle(p)?'ew-resize':'pointer';});
wave.addEventListener('pointerdown',e=>{if(!A.src||e.button!==0)return;const p=at(e),edge=nearHandle(p);e.preventDefault();wave.focus();
  if(edge){A.drag={edge,moved:false};wave.setPointerCapture(e.pointerId);return;}
  if(p.t>A.set.start&&p.t<A.set.end){playSide('new',p.t-A.set.start).catch(err=>problem(err.message));return;}
  A.drag={edge:p.t<=A.set.start?'start':'end',moved:true};setEdge(A.drag.edge,p.t);wave.setPointerCapture(e.pointerId);});
for(const ev of ['pointerup','pointercancel'])wave.addEventListener(ev,()=>{if(!A.drag)return;const moved=A.drag.moved;A.drag=null;if(moved)changed();});
wave.addEventListener('keydown',e=>{if(!A.src||!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const step=(e.key==='ArrowLeft'?-1:1)*(A.src.duration<20?0.01:0.1);
  setEdge(e.shiftKey?'end':'start',(e.shiftKey?A.set.end:A.set.start)+step);changed();});
function setEdge(edge,t){const s=A.set,d=A.src.duration;if(edge==='start')s.start=Math.max(0,Math.min(t,s.end-0.02));else s.end=Math.min(d,Math.max(t,s.start+0.02));fit();render();}

// ----- settings ----------------------------------------------------------------------------------------------------
function changed(fetchReport=true){if(!A.src)return;keep();render();if(fetchReport)later('report',refreshReport,450);if(A.playing==='new')later('swap',swap,350);}
function later(name,fn,ms){clearTimeout(A.timers[name]);A.timers[name]=setTimeout(()=>fn().catch(e=>problem(e.message,e.code)),ms);}
start.addEventListener('change',()=>{const v=parseFloat(start.value);if(A.src&&isFinite(v)){A.set.start=v;fit();changed();}});
end.addEventListener('change',()=>{const v=parseFloat(end.value);if(A.src&&isFinite(v)){A.set.end=v;fit();changed();}});
gain.addEventListener('input',()=>{A.set.gain=+gain.value;changed();});
fadeIn.addEventListener('input',()=>{A.set.fade_in=+fadeIn.value;fit();changed();});
fadeOut.addEventListener('input',()=>{A.set.fade_out=+fadeOut.value;fit();changed();});
match.addEventListener('change',()=>{A.set.match=match.checked;changed();});
let reporting=false,again=false;   // one report at a time: measuring a 20-minute song takes seconds, so changes meanwhile wait for it
async function refreshReport(){if(!A.src)return;if(reporting){again=true;return;}reporting=true;
  try{const key=keyOf();
    const r=await call('/api/audio-preview?'+new URLSearchParams({key:A.key,source:A.src.id,settings:JSON.stringify(A.set),info:'1'}));
    if(key===keyOf()){A.report=r;problem('');showReport();}}
  finally{reporting=false;if(again){again=false;later('report',refreshReport,0);}}}

// ----- listening ---------------------------------------------------------------------------------------------------
async function preview(){
  for(let tries=0;tries<3;tries++){const key=keyOf();if(A.prev&&A.prev.key===key)return A.prev;
    working.textContent='Preparing the preview…';
    try{const r=await fetch('/api/audio-preview?'+new URLSearchParams({key:A.key,source:A.src.id,settings:JSON.stringify(A.set)}));
      if(!r.ok){let j={};try{j=await r.json();}catch(e){}throw Error(j.error||'The preview could not be made.');}
      const report=JSON.parse(r.headers.get('X-Lab-Audio')||'null'),blob=await r.blob();
      if(key!==keyOf())continue;   // changed while it was being made
      const old=A.prev;A.prev={key,url:URL.createObjectURL(blob)};if(old)setTimeout(()=>URL.revokeObjectURL(old.url),20000);
      if(report){A.report=report;showReport();}return A.prev;}
    finally{working.textContent='';}}
  return null;}
async function playSide(side,from=0){
  stop(false);problem('');
  if(side==='orig'){if(!orig.src)await loadOriginal(A.key);orig.currentTime=Math.max(0,Math.min(from,(A.info?.original?.duration||0)-0.05));await orig.play();}
  else{const p=await preview();if(!p)return;if(play.src!==p.url)play.src=p.url;play.currentTime=Math.max(0,Math.min(from,A.set.end-A.set.start-0.02));A.playStart=A.set.start;await play.play();}
  A.playing=side;A.mode=side;tick();render();}
function stop(redraw=true){orig.pause();play.pause();A.playing=null;cancelAnimationFrame(A.raf);if(redraw)render();}
async function swap(){   // settings changed while your sound plays: keep playing the new version from the same moment
  if(A.playing!=='new')return;const moment=A.playStart+play.currentTime,p=await preview();if(!p||A.playing!=='new')return;
  const from=moment-A.set.start,len=A.set.end-A.set.start;play.src=p.url;play.currentTime=from>=0&&from<len-0.05?from:0;A.playStart=A.set.start;await play.play().catch(()=>{});}
orig.onended=play.onended=()=>{A.playing=null;render();};
try{setLoop(!!localStorage.getItem('lab-audio-loop'));}catch(e){setLoop(false);}
gamePlay.onclick=()=>{if(A.playing==='orig'){stop();return;}playSide('orig').catch(e=>problem(e.message));};
// Right-click on a sound: play or stop it, and loop. (The other entries, such as Name…, come from their own modules.)
{const menu=$('contextMenu'),entry=(action,text)=>{const b=el('button','',text);b.dataset.action=action;b.type='button';menu.prepend(b);return b;};
 const loopItem=entry('audio-loop','Loop'),playItem=entry('audio-play','Play the game’s sound');
 new MutationObserver(()=>{if(menu.hidden)return;const sound=S.asset?.kind==='audio';playItem.hidden=loopItem.hidden=!sound;
   playItem.textContent=A.playing?'Stop':'Play the game’s sound';loopItem.textContent=A.loop?'✓ Loop':'Loop';}).observe(menu,{attributes:true,attributeFilter:['hidden']});
 menu.addEventListener('click',e=>{const a=e.target.dataset.action;if(a==='audio-loop')setLoop(!A.loop);
   if(a==='audio-play'){if(A.playing)stop();else playSide('orig').catch(err=>problem(err.message));}});}
playBtn.onclick=()=>{if(A.playing){stop();return;}playSide(A.mode).catch(e=>problem(e.message));};
ab.onclick=e=>{const b=e.target.closest('[data-mode]');if(!b||b.disabled)return;const mode=b.dataset.mode;
  if(A.playing&&A.playing!==mode){const t=A.playing==='orig'?orig.currentTime:play.currentTime;playSide(mode,t).catch(err=>problem(err.message));return;}
  A.mode=mode;render();};
root.addEventListener('keydown',e=>{if(e.key!==' '||e.target.closest('input,button,textarea,select,a'))return;e.preventDefault();playBtn.click();});

// ----- choosing a sound ----------------------------------------------------------------------------------------------
function loaded(meta,key){if(!meta||!meta.id)return;const set=defaults(meta);
  if(key!==A.key){A.kept[key]={src:meta,set};say(`“${meta.name}” is ready: open that sound again to use it.`);return;}
  stop(false);Object.assign(A,{src:meta,set,prev:null,report:null,mode:'new'});link.value='';problem('');
  notice(meta.via==='helper'&&meta.license?`License on the page: ${meta.license}`:'');render();changed();}
async function upload(f){
  problem('');if(!f)return;const max=A.info?.limits?.bytes||157286400;
  if(f.size>max){problem(`“${f.name}” is ${size(f.size)}. Use a sound file of at most ${size(max)}.`);return;}
  const key=A.key;progress(0,`Sending ${f.name}…`,false);
  try{const t=await token();
    const meta=await new Promise((ok,fail)=>{const x=new XMLHttpRequest();x.open('POST','/api/audio-source');x.setRequestHeader('X-Lab-Token',t);
      x.setRequestHeader('Content-Type','application/octet-stream');x.setRequestHeader('X-Lab-Name',encodeURIComponent(f.name));
      x.upload.onprogress=e=>{if(e.lengthComputable)progress(e.loaded/e.total,`Sending ${f.name}… ${size(e.loaded)} of ${size(e.total)}`,false);};
      x.upload.onload=()=>progress(null,'Converting…',false);
      x.onload=()=>{let j={};try{j=JSON.parse(x.responseText);}catch(e){}if(x.status===200)ok(j);else{const e=Error(j.error||'This file could not be used.');e.code=j.code||'';fail(e);}};
      x.onerror=()=>fail(Error('The lab did not answer. Is it still running?'));x.send(f);});
    idle();loaded(meta,key);}
  catch(e){idle();if(e.code==='needs_helper')consent(()=>upload(f));else problem(e.message,e.code);}}
async function getLink(){
  problem('');let url=link.value.trim().replace(/\s/g,'%20');if(!url){link.focus();return;}   // a space in a pasted link is sent as %20, as browsers do
  if(!/^[a-z][a-z0-9+.-]*:/i.test(url)&&/^[\w-]+(\.[\w-]+)+(:\d+)?(\/|$)/.test(url))url='https://'+url;   // www.youtube.com/watch… works too
  if(A.job){problem('Wait for the download that is running, or press Cancel.');return;}   // one at a time: the panel follows one job
  if(!/^https?:\/\/\S+$/i.test(url)){problem('Paste a link that starts with http:// or https://');return;}
  const key=A.key;
  try{const job=await call('/api/audio-link',{url,key});await follow(job,j=>loaded(j.result,key),()=>getLink(),key);}
  catch(e){problem(e.message,e.code);}}
async function follow(job,done,retry,key=A.key){
  A.job=job;get.disabled=true;const show=j=>progress(j.progress,(key!==A.key&&A.info?`For ${A.names[key]||'another sound'}: `:'')+j.message,true);show(job);
  try{while(A.job&&A.job.id===job.id){await sleep(400);let j;
    try{j=await call('/api/audio-job?id='+job.id);}catch(e){A.job=null;idle();problem(e.message);return;}
    if(j.state==='running'){show(j);continue;}
    A.job=null;idle();
    if(j.state==='done')done(j);else if(j.code==='needs_helper')consent(retry);else if(j.state==='cancelled')notice('Stopped.');else problem(j.error,j.code);
    return j;}}
  finally{if(!A.job)get.disabled=false;}}
cancel.onclick=()=>{if(A.job)call('/api/audio-cancel',{id:A.job.id}).catch(e=>problem(e.message));};
async function consent(then){
  const s=await call('/api/audio-tools');if(s.ready){then();return;}
  if(s.job){await follow(s.job,()=>then(),()=>consent(then));return;}
  if(!s.available){problem('This copy of Asset Lab has no link helper list. Update Asset Lab to get sound from web pages.');return;}
  const mb=Math.max(1,Math.round(s.download/1e6)),missing=s.items.filter(i=>!i.installed);
  consentBox.replaceChildren();consentBox.hidden=false;
  const list=el('ul');for(const i of missing)list.append(el('li','',`${i.name} · ${size(i.size)} · from ${HOSTS[i.source]||i.source}`));
  const yes=btn(`Download (about ${mb} MB)`,'primary'),no=btn('Not now','quiet');yes.id='aeConsentYes';
  yes.onclick=async()=>{consentBox.hidden=true;try{const job=await call('/api/audio-tools-install',{});await follow(job,()=>{notice('The link helper is ready.');refreshHelper();then();},()=>consent(then));}catch(e){problem(e.message,e.code);}};
  no.onclick=()=>{consentBox.hidden=true;notice('No problem: you can still choose sound files from this PC.');};
  const row=el('div','ae-row');row.append(yes,no);
  consentBox.append(el('b','','Get the link helper?'),el('p','',`To get sound from web pages like YouTube, SoundCloud and Bandcamp (and to open M4A, AAC, WebM and MP4 files), Asset Lab needs three free tools. They are downloaded once, about ${mb} MB in all, from their official sources, and every file is checked before it is used:`),list,row);
  yes.focus();}
async function refreshHelper(){try{const h=await call('/api/audio-tools');if(A.info)A.info.helper=h;helper();}catch(e){}}
async function updateHelper(){problem('');try{const job=await call('/api/audio-tools-update',{});await follow(job,j=>{notice(j.result&&j.result.message||'The link helper is up to date.');refreshHelper();},()=>{});}catch(e){problem(e.message,e.code);}}
choose.onclick=()=>file.click();
file.addEventListener('change',()=>{const f=file.files[0];file.value='';if(f)upload(f);});
linkForm.addEventListener('submit',e=>{e.preventDefault();getLink();});
root.addEventListener('dragover',e=>{const types=[...(e.dataTransfer?.types||[])];if(types.includes('Files')||types.includes('text/uri-list')){e.preventDefault();root.classList.add('dropping');}});
root.addEventListener('dragleave',e=>{if(!root.contains(e.relatedTarget))root.classList.remove('dropping');});
root.addEventListener('drop',e=>{root.classList.remove('dropping');const f=e.dataTransfer.files[0];if(f){e.preventDefault();upload(f);return;}
  const u=(e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain')||'').split(/\r?\n/).map(l=>l.trim()).find(l=>/^https?:\/\//i.test(l));
  if(u){e.preventDefault();if(A.job){problem('Wait for the download that is running, or press Cancel.');return;}link.value=u;getLink();}});

// ----- saving --------------------------------------------------------------------------------------------------------
save.onclick=async()=>{
  if(!A.src)return;save.disabled=true;
  if(A.info&&A.info.edit&&!A.info.edit.staged&&keyOf()===A.saved){   // saved but left out of the mod: just put it back
    try{await call('/api/audio-stage',{key:A.key,enabled:true});A.info=await call('/api/audio?key='+encodeURIComponent(A.key));notice('Back in your mod.');await refresh();}
    catch(e){problem(e.message,e.code);}finally{render();}return;}
  notice('Saving: converting to the game’s format (OGG Vorbis)…');
  try{const info=await call('/api/audio-apply',{key:A.key,source:A.src.id,settings:A.set});
    A.info=info;if(info.chosen)A.src=info.chosen;A.set={...info.edit.settings};A.saved=keyOf();delete A.kept[A.key];render();
    const active=typeof S!=='undefined'&&S&&S.status&&S.status.active;
    notice(`Saved: ${size(info.edit.info.bytes)} of OGG Vorbis in your mod.`);say(`Saved your sound for ${info.name}. ${active?'Update':'Install'} Mitosis to hear it in the game.`);
    await refresh();}
  catch(e){notice('');problem(e.message,e.code);}finally{render();}};
original.onclick=async()=>{
  const saved=!!(A.info&&A.info.edit);
  if(saved){const text='Your saved sound leaves the mod. The game plays its own sound again after the next install or update. You can choose a sound again at any time.';
    if(!(typeof confirmAction==='function'?await confirmAction('Use the game’s own sound?',text,'Use the original sound'):confirm(window.LabI18n?LabI18n.t(text):text)))return;}
  stop(false);
  try{if(saved){A.info=await call('/api/audio-remove',{key:A.key});await refresh();}
    Object.assign(A,{src:null,set:null,prev:null,report:null,saved:'',mode:'orig'});delete A.kept[A.key];notice(saved?'The game’s own sound is back in your mod.':'');}
  catch(e){problem(e.message,e.code);}render();showReport();};

// ----- when a sound is selected ----------------------------------------------------------------------------------------
function opened(key){open(key).catch(e=>{problem(e.message,e.code);render();});}
document.addEventListener('lab:asset',e=>{A.seen=true;const d=e.detail||{};if(d.kind==='audio'&&d.key)opened(d.key);else{keep();show(false);}});
function sync(){if(A.seen)return;const a=$('audio'),host=$('nonvisual');if(!a||!host)return;const src=a.getAttribute('src')||'';
  const key=!host.hidden&&!a.hidden&&src.includes('key=')?new URL(src,location.href).searchParams.get('key'):'';if(key)opened(key);else{keep();show(false);}}
const watch=new MutationObserver(sync);for(const id of ['audio','nonvisual']){const n=$(id);if(n)watch.observe(n,{attributes:true,attributeFilter:['src','hidden']});}
// For tests and debugging: the editor's state, read-only.
window.LabAudio={state:()=>({key:A.key,source:A.src&&A.src.id,set:A.set&&{...A.set},saved:!!A.src&&keyOf()===A.saved,playing:A.playing,mode:A.mode,preview:!!(A.prev&&A.prev.key===keyOf()),
  report:A.report,job:A.job&&A.job.id,loop:!!A.loop,time:A.playing==='new'?play.currentTime:A.playing==='orig'?orig.currentTime:0,visible:!root.hidden,edit:A.info&&A.info.edit})};
})();
