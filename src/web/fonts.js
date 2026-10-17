'use strict';
// Text & fonts: re-draw the game's own text (player names, menu text) in any font on this PC, with gradients, outlines,
// glow, shadow and more. The lab renders each preview exactly the way the game sets the text. Previews are kept, and every
// look is fetched ahead in the background, so clicking through the looks is instant.
// Uses app.js globals: api, message, status, run, S. Shares window.LabFonts (looks, previews, a look picker) with Theme AI.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(text,cls,title)=>{const b=el('button',cls||'',text);b.type='button';if(title)b.title=title;return b;};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const clamp=(v,lo,hi)=>Math.min(hi,Math.max(lo,v));
const dlg=el('dialog','fonts');dlg.id='fontsDialog';dlg.setAttribute('aria-label','Text and fonts');document.body.append(dlg);
const F={data:null,font:'nicktext',style:{},text:'Cellmate',look:'',last:0};
const U={syncs:[]};   // the open dialog's controls

// ----- looks and previews, shared with Theme AI ------------------------------------------------------------------
let loading=null;
function load(fresh){
  if(fresh||!loading)loading=api('/api/fonts').then(d=>{
    if(!Array.isArray(d.presets))d.presets=[{group:'Looks',items:Object.entries(d.presets||{}).map(([name,style])=>({name,style}))}];   // a lab not restarted since the update
    d.looks=d.presets.flatMap(g=>g.items.map(l=>({...l,group:g.group})));return F.data=d;}).catch(e=>{loading=null;throw e;});
  return loading;
}
const stopsOf=s=>(s||[]).map(([at,c])=>[at,c]);
function clean(style){const s={stretch:1,fill_opacity:1,angle:90,...F.data.defaults,...(style||{})};s.stops=stopsOf(s.stops);return s;}
// A preview is known by everything that changes it: the game font, the example text and the whole style.
const keyOf=(font,style,text)=>`${font}\n${text}\n${JSON.stringify(Object.keys(style).sort().map(k=>[k,style[k]]))}`;
const same=(a,b)=>keyOf('',clean(a),'')===keyOf('',clean(b),'');
const cache=new Map(),flying=new Map();
function remember(k,url){cache.delete(k);cache.set(k,url);if(cache.size>400)cache.delete(cache.keys().next().value);}
function preview(font,style,text){
  const k=keyOf(font,style,text);if(cache.has(k))return Promise.resolve(cache.get(k));
  if(!flying.has(k))flying.set(k,api('/api/font-preview',{font,style,text}).then(r=>{remember(k,r.png);return r.png;}).finally(()=>flying.delete(k)));
  return flying.get(k);
}
// Fetch ahead, one at a time, starting next to the current look; whatever the player is looking at goes first.
const P={gen:0,user:0};
async function prefetch(font,text,styles){
  if(window.LabSettings&&LabSettings.get('font_prefetch')===false)return;   // Settings › Text & fonts › Preload look previews
  const gen=++P.gen;
  for(const style of styles){
    while(P.user&&gen===P.gen)await sleep(25);
    if(gen!==P.gen)return;
    try{await preview(font,style,text);}catch{return;}
  }
}
const ring=(n,i)=>{const out=[i];for(let d=1;out.length<n;d++){out.push((i+d)%n);if(out.length<n)out.push(((i-d)%n+n)%n);}return out;};
function lookSelect(first){
  const s=el('select','fonts-look-select');
  if(first!==undefined){const o=el('option','',first);o.value='';s.append(o);}
  for(const g of F.data.presets){const og=el('optgroup');og.label=g.group;for(const l of g.items){const o=el('option','',l.name);o.value=l.name;og.append(o);}s.append(og);}
  return s;
}

// A looks menu for any preview image (Theme AI's text card): ‹ menu ›, instant switching, the rest fetched ahead.
function picker(o){
  const row=el('div','fonts-look');const prev=btn('‹','','Previous look'),next=btn('›','','Next look');
  let sel=el('select','fonts-look-select');sel.append(el('option','','Loading looks…'));sel.disabled=prev.disabled=next.disabled=true;
  row.append(prev,sel,next);
  let gen=0;
  const choose=()=>{
    const look=F.data.looks.find(l=>l.name===sel.value),style=look?clean(look.style):o.style,g=++gen;
    o.onPick(style,look?look.name:'');
    preview(o.font,style,o.text).then(url=>{if(g===gen)o.img.src=url;}).catch(e=>message(e.message,true));
  };
  const step=d=>{const opts=[...sel.options].filter(x=>!x.disabled),n=opts.length,i=opts.findIndex(x=>x.value===sel.value);
    sel.value=opts[i<0?(d>0?0:n-1):((i+d)%n+n)%n].value;choose();};
  prev.onclick=()=>step(-1);next.onclick=()=>step(1);
  load().then(d=>{
    const ready=lookSelect(o.first);ready.setAttribute('aria-label','Look');ready.onchange=choose;
    ready.onkeydown=e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();step(e.key==='ArrowRight'?1:-1);}};
    sel.replaceWith(ready);sel=ready;prev.disabled=next.disabled=false;
    const i=d.looks.findIndex(l=>same(l.style,o.style));sel.value=i>=0?d.looks[i].name:'';
    remember(keyOf(o.font,clean(o.style),o.text),o.img.src);   // the card's own first picture
    if(!dlg.open)prefetch(o.font,o.text,ring(d.looks.length,Math.max(0,i)).map(j=>clean(d.looks[j].style)));
  }).catch(()=>{row.hidden=true;});
  return row;
}
window.LabFonts={catalog:()=>load(),reload:()=>load(true),preview,picker};

// ----- the dialog -------------------------------------------------------------------------------------------------
async function open(font,style,text){
  /* Opens at once: the font list can take a moment the first time (it reads every font on this PC), so the window shows
     that it is loading instead of the button seeming to do nothing. */
  let waited=false;   // the loading window was shown: if the player closed it before the fonts came, it stays closed
  if(!F.data&&!dlg.open){const head=el('div','fonts-head'),x=btn('✕','quiet','Close');x.onclick=()=>dlg.close();head.append(el('h2','','Text & fonts'),x);
    dlg.replaceChildren(head,el('p','fonts-sub fonts-loading','Loading fonts…'));dlg.showModal();waited=true;}
  try{await load();}catch(e){if(!F.data&&dlg.open)dlg.close();throw e;}
  /* Asked of the dialog itself: its close event comes a moment after it closes, too late to tell. */
  if(waited&&!dlg.open)return;
  F.font=font||F.font;if(!F.data.game.some(g=>g.id===F.font)&&F.data.game.length)F.font=F.data.game[0].id;
  const saved=F.data.game.find(g=>g.id===F.font);
  setStyle(style||(saved&&saved.style)||{});
  // The example text: what was asked for, else what the player typed here, else Settings › Text & fonts › Example text.
  if(text)F.text=text;else if(!F.typed&&window.LabSettings&&LabSettings.get('font_sample'))F.text=LabSettings.get('font_sample');
  render();if(!dlg.open)dlg.showModal();show();ahead();
}
window.openFonts=run(open);
dlg.addEventListener('close',()=>{P.gen++;});
function setStyle(style){F.style=clean(style);const i=F.data.looks.findIndex(l=>same(l.style,F.style));F.look=i<0?'':F.data.looks[i].name;if(i>=0)F.last=i;}
function sync(){for(const f of U.syncs)f();after();}
function after(){   // what depends on several settings at once
  const s=F.style;if(U.warn)U.warn.hidden=s.fill_opacity>0||!!(s.outline&&s.outline_width||s.outline2&&s.outline2_width||s.glow&&s.glow_size||s.shadow);
  if(U.shot){const grad=s.stops.length>0;U.shot.classList.toggle('can-drag',grad);U.shot.title=grad?'Drag across the preview to point the gradient (Shift snaps to 15°)':'';
    U.meta.textContent='On the menu, on a red cell and on a light cell, at the size the game stores it.'+(grad?' Drag across it to point the gradient.':'');}
}
function edited(){F.look='';if(U.look)U.look.value='';after();show();}
function useLook(i){const n=F.data.looks.length;F.last=((i%n)+n)%n;const l=F.data.looks[F.last];F.look=l.name;F.style=clean(l.style);sync();show();ahead();}
function ahead(){if(dlg.open&&F.data)prefetch(F.font,F.text,ring(F.data.looks.length,F.last).map(j=>clean(F.data.looks[j].style)));}

// Previews the player asked for: at most one on its way, the newest wanted one next, a cached one at once.
const V={want:'',shown:'',next:null,busy:false};
function show(){
  if(!dlg.open||!U.img)return;
  const job={font:F.font,style:{...F.style,stops:stopsOf(F.style.stops)},text:F.text};job.k=keyOf(job.font,job.style,job.text);V.want=job.k;
  if(cache.has(job.k)){V.next=null;return paint(cache.get(job.k),job.k);}
  V.next=job;pump();
}
async function pump(){
  if(V.busy)return;V.busy=true;P.user++;
  const slow=setTimeout(()=>U.shot&&U.shot.classList.add('waiting'),160);
  try{while(V.next){const job=V.next;V.next=null;
      try{const url=await preview(job.font,job.style,job.text);if(job.k===V.want||V.shown!==V.want)paint(url,job.k);}
      catch(e){if(U.meta)U.meta.textContent=e.message;}}}
  finally{V.busy=false;P.user--;clearTimeout(slow);if(U.shot)U.shot.classList.remove('waiting');}
}
function paint(url,k){if(U.img.src!==url)U.img.src=url;V.shown=k;}

function slider(label,key,min,max,step,show_,hint){
  const row=el('label','fonts-slider');const r=el('input');r.type='range';r.min=min;r.max=max;r.step=step;const v=el('span','fonts-value');
  const sync_=()=>{r.value=F.style[key];v.textContent=show_(+F.style[key]);};
  r.oninput=()=>{F.style[key]=+r.value;v.textContent=show_(+r.value);edited();};
  r.ondblclick=()=>{F.style[key]=F.data.defaults[key];sync_();edited();};   // double-click: back to normal
  row.title=(hint?hint+' · ':'')+'Double-click to reset';row.append(el('span','',label),r,v);U.syncs.push(sync_);sync_();return row;
}
function check(label,key){
  const row=el('label','fonts-check');const box=el('input');box.type='checkbox';box.onchange=()=>{F.style[key]=box.checked;edited();};
  U.syncs.push(()=>{box.checked=!!F.style[key];});row.append(box,el('span','',label));return row;
}
function colorToggle(label,key,fallback,extras,start){
  const box=el('div','fonts-effect');const head=el('label','fonts-effect-head');const on=el('input');on.type='checkbox';
  const pick=el('input');pick.type='color';pick.setAttribute('aria-label',label+' color');
  const more=el('div','fonts-effect-more');for(const x of extras||[])more.append(x);
  U.syncs.push(()=>{on.checked=!!F.style[key];pick.value=F.style[key]||pick.value||fallback;pick.disabled=!on.checked;more.hidden=!on.checked;});
  pick.value=fallback;
  on.onchange=()=>{F.style[key]=on.checked?pick.value:'';
    if(on.checked&&Object.keys(start).every(k=>!F.style[k])){Object.assign(F.style,start);sync();}   // switched on at size 0 it would not show: start visible
    pick.disabled=!on.checked;more.hidden=!on.checked;edited();};
  pick.oninput=()=>{F.style[key]=pick.value;edited();};
  head.append(on,el('span','',label),pick);box.append(head,more);return box;
}

// Gradient: a bar with a marker per color (2 to 8), a direction dial, and the preview to drag across.
function gradientEditor(){
  const box=el('div','fonts-grad');
  const bar=el('div','fonts-grad-bar');bar.title='Click the bar to add a color · drag a marker to move it · drag it off the bar to remove it';
  const color=el('input');color.type='color';color.setAttribute('aria-label','Color of the chosen marker');
  const at=el('input','fonts-num');at.type='number';at.min=0;at.max=100;at.step=1;at.setAttribute('aria-label','Where the chosen color sits, in percent');
  at.title='Where the chosen color sits: 0% is the start, 100% the end. Two colors at the same spot make a hard edge.';
  const remove=btn('Remove','quiet','Remove the chosen color (Delete)'),flip=btn('⇄ Reverse','quiet','Turn the gradient end for end');
  const tools=el('div','fonts-grad-tools');tools.append(color,el('span','fonts-small','at'),at,el('span','fonts-small','%'),remove,flip);
  const dial=el('div','fonts-dial');dial.tabIndex=0;dial.setAttribute('role','slider');dial.setAttribute('aria-label','Gradient direction in degrees');
  dial.setAttribute('aria-valuemin','0');dial.setAttribute('aria-valuemax','359');dial.title='Drag around to turn the gradient (Shift snaps to 15°)';
  const needle=el('i');dial.append(needle);
  const deg=el('input','fonts-num');deg.type='number';deg.min=0;deg.max=359;deg.step=1;deg.setAttribute('aria-label','Gradient direction in degrees');
  deg.title='0° runs left to right, 90° top to bottom';
  const quick=el('div','fonts-quick');
  for(const [label,a,title] of [['↓',90,'Top to bottom'],['→',0,'Left to right'],['↘',45,'Diagonal, down'],['↗',315,'Diagonal, up']]){const b=btn(label,'',title);b.onclick=()=>setAngle(a);quick.append(b);}
  const dir=el('div','fonts-dir');dir.append(dial,deg,el('span','fonts-small','°'),quick);
  const hint=el('p','fonts-hint','Every letter is drawn on its own, so the gradient covers each letter’s box: its width, and from the top of the capitals down to the baseline. Top-to-bottom colors line up along a whole name; sideways ones repeat in every letter.');
  box.append(bar,tools,dir,hint);
  const G={list:[],sel:0,id:0,drag:null,turning:false};
  const marks=new Map();
  const sorted=()=>G.list.slice().sort((a,b)=>a.at-b.at);   // stable: two colors at one spot keep their order
  const chosen=()=>G.list.find(s=>s.id===G.sel)||G.list[0];
  const rgb=c=>[1,3,5].map(i=>parseInt(c.slice(i,i+2),16));
  function colorAt(a){
    const l=sorted();if(a<=l[0].at)return l[0].color;
    for(let i=1;i<l.length;i++)if(a<=l[i].at){const p=l[i-1],q=l[i],t=q.at>p.at?(a-p.at)/(q.at-p.at):0,A=rgb(p.color),B=rgb(q.color);
      return '#'+A.map((v,k)=>Math.round(v+(B[k]-v)*t).toString(16).padStart(2,'0')).join('');}
    return l[l.length-1].color;
  }
  function draw(){
    const l=sorted();
    if(l.length)bar.style.backgroundImage=`linear-gradient(90deg,${l.map(s=>`${s.color} ${(s.at*100).toFixed(2)}%`).join(',')})`;
    for(const [id,m] of marks)if(!G.list.some(s=>s.id===id)){m.remove();marks.delete(id);}
    for(const s of l){
      let m=marks.get(s.id);
      if(!m){m=btn('','fonts-stop');m.append(el('i'));m.dataset.id=s.id;marks.set(s.id,m);bar.append(m);
        m.onfocus=()=>{if(G.sel!==s.id){G.sel=s.id;draw();}};m.onkeydown=e=>key(e,s);}
      const twins=l.filter(o=>o.at===s.at),k=twins.indexOf(s);   // colors at one spot (a hard edge) sit side by side, all visible
      m.style.left=(s.at*100)+'%';m.style.marginLeft=(-8+(k-(twins.length-1)/2)*12)+'px';
      m.firstChild.style.backgroundColor=s.color;m.classList.toggle('on',s.id===G.sel);
      m.setAttribute('aria-label',`Gradient color ${s.color} at ${Math.round(s.at*100)}%`);
    }
    const c=chosen();if(c){color.value=c.color;if(document.activeElement!==at)at.value=Math.round(c.at*100);}
    remove.disabled=G.list.length<=2;needle.style.transform=`rotate(${F.style.angle}deg)`;
    if(document.activeElement!==deg)deg.value=F.style.angle;dial.setAttribute('aria-valuenow',F.style.angle);
  }
  const changed=()=>{F.style.stops=sorted().map(s=>[Math.round(s.at*1000)/1000,s.color]);draw();edited();};
  const setAngle=a=>{const v=((Math.round(a)%360)+360)%360;if(v===F.style.angle)return;F.style.angle=v;draw();edited();};
  U.setAngle=setAngle;
  const drop=s=>{G.list=G.list.filter(x=>x!==s);G.sel=G.list[0].id;changed();};
  function key(e,s){
    if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();if(G.list.length>2){drop(s);marks.get(G.sel).focus();}return;}
    const d={ArrowLeft:-1,ArrowRight:1}[e.key];if(!d)return;
    e.preventDefault();s.at=clamp(Math.round(s.at*100+d*(e.shiftKey?10:1))/100,0,1);changed();
  }
  const pos=e=>{const r=bar.getBoundingClientRect();return clamp((e.clientX-r.left)/r.width,0,1);};
  bar.onpointerdown=e=>{
    if(e.button!==0)return;e.preventDefault();
    const hit=e.target.closest('.fonts-stop');let s=hit&&G.list.find(x=>x.id===+hit.dataset.id);const added=!s;
    if(!s){if(G.list.length>=8){U.meta.textContent='A gradient holds 8 colors at most.';return;}const a=pos(e);s={at:a,color:colorAt(a),id:++G.id};G.list.push(s);}
    G.sel=s.id;G.drag={s,off:false,grab:added?0:pos(e)-s.at};bar.setPointerCapture(e.pointerId);if(added)changed();else draw();marks.get(s.id).focus();   // picking a color is not a change
  };
  bar.onpointermove=e=>{
    const d=G.drag;if(!d)return;
    let a=clamp(pos(e)-d.grab,0,1);for(const o of G.list)if(o!==d.s&&Math.abs(o.at-a)<0.015)a=o.at;   // held where it was grabbed; snaps onto another color: a hard edge
    const r=bar.getBoundingClientRect();d.off=G.list.length>2&&Math.abs(e.clientY-(r.top+r.height/2))>44;
    marks.get(d.s.id).classList.toggle('leaving',d.off);if(d.s.at!==a){d.s.at=a;changed();}
  };
  bar.onpointerup=bar.onpointercancel=()=>{const d=G.drag;G.drag=null;if(d&&d.off)drop(d.s);};
  color.oninput=()=>{const c=chosen();if(c){c.color=color.value;changed();}};
  at.oninput=()=>{const c=chosen(),v=parseFloat(at.value);if(c&&Number.isFinite(v)){c.at=clamp(v,0,100)/100;changed();}};
  remove.onclick=()=>{if(G.list.length>2)drop(chosen());};
  flip.onclick=()=>{for(const s of G.list)s.at=Math.round((1-s.at)*1000)/1000;G.list.reverse();changed();};
  const turn=e=>{const r=dial.getBoundingClientRect();let a=Math.atan2(e.clientY-(r.top+r.height/2),e.clientX-(r.left+r.width/2))*180/Math.PI;if(e.shiftKey)a=Math.round(a/15)*15;setAngle(a);};
  dial.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();dial.focus();dial.setPointerCapture(e.pointerId);G.turning=true;turn(e);};
  dial.onpointermove=e=>{if(G.turning)turn(e);};
  dial.onpointerup=dial.onpointercancel=()=>{G.turning=false;};
  dial.onkeydown=e=>{const d={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1}[e.key];if(!d)return;e.preventDefault();setAngle(F.style.angle+d*(e.shiftKey?15:1));};
  deg.oninput=()=>{const v=parseFloat(deg.value);if(Number.isFinite(v))setAngle(v);};
  deg.onblur=()=>{deg.value=F.style.angle;};
  U.gradient=()=>{G.list=F.style.stops.map(([a,c])=>({at:a,color:c,id:++G.id}));G.sel=G.list.length?G.list[0].id:0;box.hidden=!G.list.length;draw();};
  U.syncs.push(U.gradient);
  return box;
}
function fillBox(){
  const box=el('div','fonts-effect fonts-fill');const head=el('div','fonts-effect-head');
  const color=el('input');color.type='color';color.setAttribute('aria-label','Letter color');
  const gradRow=el('label','fonts-check');const grad=el('input');grad.type='checkbox';gradRow.append(grad,el('span','','Gradient'));
  gradRow.title='Blend 2 to 8 colors across every letter, in any direction';
  const what=el('span','','Color');head.append(what,color,gradRow);
  const solid=slider('Fill','fill_opacity',0,1,0.05,v=>v<=0?'hollow':v>=1?'solid':Math.round(v*100)+'%','How solid the letters are. At 0 only the outline, glow and shadow are drawn: hollow letters');
  U.warn=el('p','fonts-warn','Hollow letters need an outline, glow or shadow to show.');
  const editor=gradientEditor();
  let memory=null;
  color.oninput=()=>{F.style.fill=color.value;edited();};
  grad.onchange=()=>{if(grad.checked)F.style.stops=memory||[[0,F.style.fill],[1,F.style.fill==='#ff8a1a'?'#7a2cff':'#ff8a1a']];else{memory=F.style.stops;F.style.stops=[];}
    color.hidden=grad.checked;what.textContent=grad.checked?'Colors':'Color';U.gradient();edited();};
  U.syncs.push(()=>{color.value=F.style.fill;grad.checked=F.style.stops.length>0;color.hidden=grad.checked;what.textContent=grad.checked?'Colors':'Color';});
  box.append(head,editor,solid,U.warn);return box;
}

function render(){
  const d=F.data;dlg.replaceChildren();U.syncs=[];
  const head=el('div','fonts-head');const h=el('h2','','Text & fonts');const x=btn('✕','quiet','Close');x.onclick=()=>dlg.close();head.append(h,x);
  const sub=el('p','fonts-sub','Draw the game’s own text in any font on this PC, with gradients, outlines, glow and shadow. Every letter is rendered at the game’s resolution, so it stays crisp.');
  const body=el('div','fonts-body');const side=el('div','fonts-controls');const stage=el('div','fonts-stage');body.append(side,stage);dlg.append(head,sub,body);

  // Which text
  const target=el('select');target.setAttribute('aria-label','Which text in the game');
  for(const g of d.game){const o=el('option','',g.label+(g.staged?' · changed':''));o.value=g.id;target.append(o);}target.value=F.font;
  const note=el('p','hint',(d.game.find(g=>g.id===F.font)||{}).note||'');
  target.onchange=()=>{F.font=target.value;const g=d.game.find(g=>g.id===F.font);setStyle(g&&g.style);render();show();ahead();};
  U.target=target;
  side.append(el('div','section-label','TEXT IN THE GAME'),target,note);

  // Looks
  const looks=el('div','fonts-look');const prev=btn('‹','','Previous look (←)'),next=btn('›','','Next look (→)');
  const pick=lookSelect('Your own look');pick.options[0].disabled=true;pick.setAttribute('aria-label','Ready-made looks');
  pick.onchange=()=>{const i=d.looks.findIndex(l=>l.name===pick.value);if(i>=0)useLook(i);};
  prev.onclick=()=>useLook(F.last-1);next.onclick=()=>useLook(F.last+1);
  U.look=pick;U.syncs.push(()=>{pick.value=F.look;});looks.append(prev,pick,next);
  side.append(el('div','section-label','LOOK'),looks,el('p','fonts-hint',`${d.looks.length} ready-made looks. Flip through them with ‹ › or the arrow keys, then change anything below.`));

  // Typeface
  const search=el('input');search.type='search';search.placeholder=`Search ${d.typefaces.length} fonts on this PC`;search.setAttribute('aria-label','Search fonts');
  const list=el('select','fonts-list');list.size=7;list.setAttribute('aria-label','Font');
  const fill=()=>{const q=search.value.trim().toLowerCase();list.replaceChildren();const own=el('option','',"The game’s own font");own.value='';list.append(own);
    for(const t of d.typefaces){if(q&&!`${t.family} · ${t.style}`.toLowerCase().includes(q))continue;const o=el('option','',`${t.family} · ${t.style}`);o.value=t.id;list.append(o);}
    list.value=F.style.font||'';};
  fill();search.oninput=fill;list.onchange=()=>{F.style.font=list.value;edited();};
  U.syncs.push(()=>{if(![...list.options].some(o=>o.value===(F.style.font||''))){search.value='';fill();}list.value=F.style.font||'';});
  side.append(el('div','section-label','FONT'),search,list);

  // Shape
  side.append(el('div','section-label','SHAPE'),slider('Size','size',0.5,2,0.05,v=>Math.round(v*100)+'%'),
    slider('Letter width','stretch',0.5,2,0.05,v=>v===1?'normal':Math.round(v*100)+'%','Wider or narrower letters; their height stays the same'),
    slider('Letter spacing','spacing',-20,40,1,v=>v+' px'),slider('Bold','bold',0,8,1,v=>v?v+' px':'off'),
    slider('Slant','italic',-0.4,0.4,0.02,v=>v?Math.round(v*100)+'%':'off'),check('ALL CAPS','caps'));

  // Color and effects
  side.append(el('div','section-label','LETTERS'),fillBox(),el('div','section-label','EFFECTS'),
    colorToggle('Outline','outline','#000000',[slider('Width','outline_width',0,16,1,v=>v+' px')],{outline_width:3}),
    colorToggle('Second outline','outline2','#ffffff',[slider('Width','outline2_width',0,16,1,v=>v+' px')],{outline2_width:2}),
    colorToggle('Glow','glow','#ffffff',[slider('Size','glow_size',0,32,1,v=>v+' px'),slider('Strength','glow_strength',0,4,0.1,v=>v.toFixed(1))],{glow_size:8}),
    colorToggle('Shadow','shadow','#000000',[slider('Across','shadow_x',-24,24,1,v=>v+' px'),slider('Down','shadow_y',-24,24,1,v=>v+' px'),slider('Blur','shadow_blur',0,24,1,v=>v+' px'),slider('Strength','shadow_opacity',0,1,0.05,v=>Math.round(v*100)+'%')],
      {shadow_x:2,shadow_y:3,shadow_blur:2}));

  // Preview and actions
  const sample=el('input','fonts-text');sample.value=F.text;sample.placeholder='Example text';sample.maxLength=40;sample.setAttribute('aria-label','Example text');
  sample.oninput=()=>{F.text=sample.value;F.typed=true;P.gen++;show();clearTimeout(U.typing);U.typing=setTimeout(ahead,400);};   // looks for the new text once typing pauses
  const shot=el('div','fonts-shot');const img=el('img','fonts-preview');img.id='fontsPreview';img.alt='Preview';const arrow=el('canvas','fonts-arrow');shot.append(img,arrow);
  const meta=el('p','hint fonts-meta');meta.id='fontsMeta';
  Object.assign(U,{shot,img,meta});V.shown='';dragDirection(shot,arrow);
  const actions=el('div','theme-actions');const apply=el('button','primary','Apply to the game font');const reset=el('button','',"Use the game’s font");
  apply.onclick=run(async()=>{apply.disabled=true;try{const r=await api('/api/font-apply',{font:F.font,style:F.style});
      message(`${r.label} saved in ${r.face}: ${r.drawn} letters${r.missing?`, ${r.missing} taken from the game font`:''}. Install in Mitosis to see it.`);
      await load(true);S.build=null;await status();labels();}finally{apply.disabled=false;}});
  reset.onclick=run(async()=>{await api('/api/font-remove',{font:F.font});await load(true);setStyle({});sync();show();message('Back to the game’s own font at the next install.');S.build=null;await status();labels();});
  actions.append(apply,reset);
  stage.append(el('div','section-label','EXAMPLE TEXT'),sample,shot,meta,actions);
  sync();
}
function labels(){if(!U.target)return;for(const o of U.target.options){const g=F.data.game.find(g=>g.id===o.value);if(g)o.textContent=g.label+(g.staged?' · changed':'');}}

// Drag across the preview to point the gradient; an arrow shows the direction while dragging.
function dragDirection(shot,canvas){
  let from=null;
  const draw=(x1,y1)=>{
    const r=shot.getBoundingClientRect(),k=window.devicePixelRatio||1;canvas.width=Math.round(r.width*k);canvas.height=Math.round(r.height*k);
    const c=canvas.getContext('2d');c.setTransform(k,0,0,k,0,0);if(!from||x1===undefined)return;
    const {x,y}=from,a=Math.atan2(y1-y,x1-x);if(Math.hypot(x1-x,y1-y)<4)return;
    c.lineCap=c.lineJoin='round';
    for(const [w,col] of [[7,'rgba(0,0,0,.6)'],[3,'#70e5d4']]){c.strokeStyle=col;c.lineWidth=w;c.beginPath();c.moveTo(x,y);c.lineTo(x1,y1);
      c.moveTo(x1-14*Math.cos(a-0.5),y1-14*Math.sin(a-0.5));c.lineTo(x1,y1);c.lineTo(x1-14*Math.cos(a+0.5),y1-14*Math.sin(a+0.5));c.stroke();}
    c.fillStyle='#70e5d4';c.beginPath();c.arc(x,y,4.5,0,Math.PI*2);c.fill();
  };
  const at=e=>{const r=shot.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};};
  shot.onpointerdown=e=>{if(e.button!==0||!F.style.stops.length)return;e.preventDefault();shot.setPointerCapture(e.pointerId);from=at(e);};
  shot.onpointermove=e=>{if(!from)return;const p=at(e);draw(p.x,p.y);
    if(Math.hypot(p.x-from.x,p.y-from.y)>8){let a=Math.atan2(p.y-from.y,p.x-from.x)*180/Math.PI;if(e.shiftKey)a=Math.round(a/15)*15;U.setAngle(a);}};
  shot.onpointerup=shot.onpointercancel=()=>{from=null;draw();};
}

// ← → flip through the looks unless a control needs the arrows itself. Keys stay in this dialog: Escape closes only it,
// and the editor's shortcuts behind it (Ctrl+S, Ctrl+Z, tool letters) do not fire.
dlg.addEventListener('keydown',e=>{
  e.stopPropagation();if(e.key==='Escape')return;
  if((e.key!=='ArrowLeft'&&e.key!=='ArrowRight')||e.altKey||e.ctrlKey||e.metaKey||!F.data)return;
  const t=e.target;if(t.matches('input,textarea,.fonts-list,.fonts-stop,.fonts-dial')||(t.tagName==='SELECT'&&!t.classList.contains('fonts-look-select')))return;
  e.preventDefault();useLook(F.last+(e.key==='ArrowRight'?1:-1));
});
})();
