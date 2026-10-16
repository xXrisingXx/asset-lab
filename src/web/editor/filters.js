'use strict';
// Filters for the Asset Lab editor: Photoshop's Filter menu (Blur, Distort, Noise, Pixelate, Render, Sharpen, Stylize,
// Other), Filter > Last Filter (Alt+Ctrl+F, and Ctrl+F as in older Photoshop) and Edit > Fade (Shift+Ctrl+F).
// A filter with options opens a dialog with a zoomable preview box (drag to look around, hold the mouse down to see the
// original, the - and + buttons or Ctrl+- / Ctrl++ to zoom) and, with Preview on, the result on the canvas. The pixel work
// runs in editor/filters-worker.js: same-origin Web Workers (a big picture is split into bands that run in parallel, with
// a progress bar for slow filters); when workers cannot start, the same file runs in the page. Results go through the
// selection (feathering included) with Ed.applyToTarget, so locks, masks and undo behave as everywhere else; lock
// transparency keeps alpha.
// Public: Ed.filters = {list(), get(id), defaults(id), run(id, params) -> Promise<bool> (no dialog), open(id, params) ->
//   Promise<bool>, last ({id, params} | null), busy, fade (what Edit > Fade would fade, or null), timing ({id, ms, workers}),
//   workers() -> {size, broken, alive}, resetWorkers() (tests)}.
(()=>{
const Ed=window.Ed;if(!Ed)return;const U=Ed.util;
const SCRIPT=document.currentScript;
const WORKER_URL=new URL('filters-worker.js',SCRIPT&&SCRIPT.src||location.href).href;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));

// ----- the filters: Photoshop's names, menus, options, ranges and defaults ------------------------------------------
// A range's min, max and default may be functions of the document (Offset). curve > 1 stretches the slider so small
// values get most of its length, as Photoshop's radius sliders do.
const R=(key,label,min,max,def,o={})=>({key,label,type:'range',min,max,step:o.step??1,unit:o.unit??'',default:def,curve:o.curve||1,title:o.title||''});
const SEG=(key,label,opts,def)=>({key,label,type:'segmented',options:opts.map(([value,label])=>({value,label})),default:def});
const CHK=(key,label,def,o={})=>({key,label,type:'checkbox',default:def,title:o.title||''});
const ANG=(key,label,def)=>({key,label,type:'angle',min:-360,max:360,step:1,default:def});
const LIST=[
  // Blur
  {id:'average',name:'Average',menu:'Blur',order:110},
  {id:'blur',name:'Blur',menu:'Blur',order:111},
  {id:'blur-more',name:'Blur More',menu:'Blur',order:112},
  {id:'box-blur',name:'Box Blur',menu:'Blur',order:113,fields:[R('radius','Radius',1,999,10,{unit:'px',curve:3})],help:'Averages every pixel with the square around it.'},
  {id:'gaussian-blur',name:'Gaussian Blur',menu:'Blur',order:114,fields:[R('radius','Radius',.1,1000,1,{unit:'px',step:.1,curve:3.2})],help:'Softens everything evenly. Radius is how far the blur spreads.'},
  {id:'motion-blur',name:'Motion Blur',menu:'Blur',order:116,fields:[ANG('angle','Angle',0),R('distance','Distance',1,2000,10,{unit:'px',curve:3})],help:'Streaks the picture in one direction, like something moving fast.'},
  // Distort
  {id:'pinch',name:'Pinch',menu:'Distort',order:201,fields:[R('amount','Amount',-100,100,50,{unit:'%'})],help:'Squeezes the middle inward (or pushes it out with a negative amount).'},
  {id:'ripple',name:'Ripple',menu:'Distort',order:203,fields:[R('amount','Amount',-999,999,100,{unit:'%'}),SEG('size','Size',[['small','Small'],['medium','Medium'],['large','Large']],'medium')],help:'Wavy ripples, like the surface of a pond.'},
  {id:'spherize',name:'Spherize',menu:'Distort',order:204,fields:[R('amount','Amount',-100,100,100,{unit:'%'}),SEG('mode','Mode',[['normal','Normal'],['horizontal','Horizontal only'],['vertical','Vertical only']],'normal')],help:'Wraps the picture around a ball (or into a bowl with a negative amount).'},
  {id:'twirl',name:'Twirl',menu:'Distort',order:205,fields:[R('angle','Angle',-999,999,50,{unit:'°'})],help:'Spins the middle more than the edges, like water down a drain.'},
  // Noise
  {id:'add-noise',name:'Add Noise',menu:'Noise',order:300,random:true,fields:[R('amount','Amount',.1,400,12.5,{unit:'%',step:.1,curve:2}),SEG('distribution','Distribution',[['uniform','Uniform'],['gaussian','Gaussian']],'uniform'),CHK('mono','Monochromatic',false,{title:'The same noise in red, green and blue: gray grain instead of colored'})],help:'Sprinkles random grain over the picture.'},
  {id:'dust-scratches',name:'Dust & Scratches',menu:'Noise',order:302,fields:[R('radius','Radius',1,100,1,{unit:'px',curve:2}),R('threshold','Threshold',0,255,0,{unit:'levels'})],help:'Replaces pixels that stand out from their neighbors by the neighbors’ median.'},
  {id:'median',name:'Median',menu:'Noise',order:303,fields:[R('radius','Radius',1,100,1,{unit:'px',curve:2})],help:'Each pixel becomes the middle value of the square around it: removes noise, rounds off details.'},
  // Pixelate
  {id:'mosaic',name:'Mosaic',menu:'Pixelate',order:404,fields:[R('size','Cell Size',2,200,8,{unit:'px',curve:2})],help:'Big square pixels, each the average color of its square. The squares start at the image’s top-left corner.'},
  // Render
  {id:'clouds',name:'Clouds',menu:'Render',order:500,random:true},
  {id:'difference-clouds',name:'Difference Clouds',menu:'Render',order:501,random:true},
  // Sharpen
  {id:'sharpen',name:'Sharpen',menu:'Sharpen',order:600},
  {id:'sharpen-edges',name:'Sharpen Edges',menu:'Sharpen',order:601},
  {id:'sharpen-more',name:'Sharpen More',menu:'Sharpen',order:602},
  {id:'unsharp-mask',name:'Unsharp Mask',menu:'Sharpen',order:604,help:'Classic sharpening: edges get a light and a dark side. Threshold leaves areas this smooth alone.',
    fields:[R('amount','Amount',1,500,50,{unit:'%',curve:1.6}),R('radius','Radius',.1,1000,1,{unit:'px',step:.1,curve:3.2}),R('threshold','Threshold',0,255,0,{unit:'levels'})]},
  // Stylize
  {id:'emboss',name:'Emboss',menu:'Stylize',order:701,fields:[ANG('angle','Angle',135),R('height','Height',1,10,3,{unit:'px'}),R('amount','Amount',1,500,100,{unit:'%'})],help:'Turns the picture gray and raises its edges, lit from the angle you choose.'},
  {id:'find-edges',name:'Find Edges',menu:'Stylize',order:703},
  {id:'solarize',name:'Solarize',menu:'Stylize',order:706},
  // Other
  {id:'high-pass',name:'High Pass',menu:'Other',order:901,fields:[R('radius','Radius',.1,1000,10,{unit:'px',step:.1,curve:3.2})],help:'Keeps only the fine detail on mid gray. Set the layer to Overlay to sharpen with it.'},
  {id:'maximum',name:'Maximum',menu:'Other',order:902,fields:[R('radius','Radius',1,100,1,{unit:'px',curve:2}),SEG('preserve','Preserve',[['squareness','Squareness'],['roundness','Roundness']],'squareness')],help:'Spreads the light areas and grows what is opaque.'},
  {id:'minimum',name:'Minimum',menu:'Other',order:903,fields:[R('radius','Radius',1,100,1,{unit:'px',curve:2}),SEG('preserve','Preserve',[['squareness','Squareness'],['roundness','Roundness']],'squareness')],help:'Spreads the dark areas and shrinks what is opaque.'},
  {id:'offset',name:'Offset',menu:'Other',order:904,help:'Slides the picture. With Wrap Around, what leaves one side comes back on the other: handy for checking that a texture tiles.',
    fields:[R('horizontal','Horizontal',()=>-docW(),()=>docW(),()=>Math.round(docW()/2),{unit:'px right'}),R('vertical','Vertical',()=>-docH(),()=>docH(),()=>Math.round(docH()/2),{unit:'px down'}),
      SEG('undefined','Undefined Areas',[['transparent','Set to Transparent'],['repeat','Repeat Edge Pixels'],['wrap','Wrap Around']],'wrap')]}];
const docW=()=>Ed.doc?Ed.doc.width:64,docH=()=>Ed.doc?Ed.doc.height:64;
const BY=new Map(LIST.map(f=>[f.id,f]));
const pathOf=f=>'Filter/'+f.menu+'/'+f.name+(f.fields?'...':'');
const val=v=>typeof v==='function'?v():v;
const resolved=x=>x.type==='range'?{...x,min:val(x.min),max:val(x.max),default:val(x.default)}:x;
const fieldsOf=f=>(f.fields||[]).map(resolved);
const defaultsOf=f=>{const o={};for(const x of fieldsOf(f))o[x.key]=x.default;return o;};
// Values check: numbers clamped to their range, choices from their list; anything else falls back to the default.
function clean(f,p){const o=defaultsOf(f);for(const x of fieldsOf(f)){const v=p?p[x.key]:undefined;if(v===undefined)continue;
    if(x.type==='range'||x.type==='angle'){const n=Number(v);if(isFinite(n))o[x.key]=clamp(n,x.min,x.max);}
    else if(x.type==='checkbox')o[x.key]=!!v;else if(x.options.some(c=>c.value===v))o[x.key]=v;}
  if(p&&typeof p.seed==='number'&&isFinite(p.seed))o.seed=p.seed|0;return o;}
const stored=f=>clean(f,U.store.get('ed-filter:'+f.id,null));

// ----- the worker pool --------------------------------------------------------------------------------------------------
// Workers keep a copy of the layer for the whole dialog (sent once), so each preview only sends the options. A newer
// preview stops the older one at once (its workers are replaced). The preview box has a worker of its own (index size).
// A worker that fails before it ever answered (blocked, missing file) switches to running the filters in the page.
const Pool={size:Math.max(1,Math.min(4,(navigator.hardwareConcurrency||4)-1)),list:[],jobs:new Map(),seq:0,inline:null,broken:false};
function worker(i){let w=Pool.list[i];if(w)return w;
  try{w=new Worker(WORKER_URL);}catch(e){Pool.broken=true;return null;}
  w.has=new Set();w.pending=new Set();w.answered=false;w.onmessage=onMessage;
  w.onerror=e=>{if(e.preventDefault)e.preventDefault();if(Pool.list[i]===w)Pool.list[i]=null;
    if(!w.answered){Pool.broken=true;for(const id of w.pending){const j=Pool.jobs.get(id);Pool.jobs.delete(id);if(j)j.retry();}w.pending.clear();return;}
    fail(w,new Error('the filter worker stopped ('+(e.message||'unknown error')+')'));};
  Pool.list[i]=w;return w;}
function fail(w,err){for(const id of w.pending){const j=Pool.jobs.get(id);Pool.jobs.delete(id);if(j)j.reject(err);}w.pending.clear();}
function onMessage(e){const m=e.data,j=Pool.jobs.get(m.job);this.answered=true;if(!j)return;
  if(m.type==='progress'){if(j.onProgress)j.onProgress(m.value);return;}
  Pool.jobs.delete(m.job);j.w.pending.delete(m.job);
  if(m.type==='done')j.resolve({data:new Uint8ClampedArray(m.data),ms:m.ms});else j.reject(new Error(m.message||'The filter failed.'));}
// Stop every job of these workers (all but the preview box's by default); they are replaced on the next job.
function cancelWorkers(which='main'){for(let i=0;i<Pool.list.length;i++){const w=Pool.list[i];if(!w||!w.pending.size)continue;if((which==='box')!==(i===Pool.size))continue;
  Pool.list[i]=null;w.terminate();const err=new Error('canceled');err.canceled=true;fail(w,err);}}
function dropSource(id){for(const w of Pool.list)if(w&&w.has.has(id)){w.has.delete(id);w.postMessage({type:'drop',id});}if(window.EdFilterKernels)window.EdFilterKernels.sources.delete(id);}
function inline(){if(Pool.inline)return Pool.inline;
  Pool.inline=new Promise((ok,no)=>{if(window.EdFilterKernels)return ok(window.EdFilterKernels);const s=document.createElement('script');s.src=WORKER_URL;
    s.onload=()=>window.EdFilterKernels?ok(window.EdFilterKernels):no(Error('the filters could not load'));s.onerror=()=>{Pool.inline=null;no(Error('the filters could not load'));};document.head.append(s);});
  return Pool.inline;}
// In the page: one band at a time, with a pause before each so the progress bar and the Cancel button stay alive.
async function runInline(src,msg){const K=await inline();await new Promise(r=>setTimeout(r,16));
  if(src.dropped)throw Object.assign(new Error('canceled'),{canceled:true});
  if(!K.sources.has(src.id))K.sources.set(src.id,{w:src.w,h:src.h,data:src.data,mask:src.mask,sel:src.sel});
  const t0=performance.now();return{data:K.run({...msg,source:src.id}),ms:performance.now()-t0};}
function job(i,src,msg,onProgress){
  const w=Pool.broken?null:worker(i);
  if(!w)return runInline(src,msg).then(r=>{if(onProgress)onProgress(1);return r;});
  if(!w.has.has(src.id)){w.postMessage({type:'source',id:src.id,w:src.w,h:src.h,data:src.data,mask:src.mask,sel:src.sel});w.has.add(src.id);}
  const id=++Pool.seq;
  return new Promise((resolve,reject)=>{Pool.jobs.set(id,{resolve,reject,w,onProgress,retry:()=>runInline(src,msg).then(resolve,reject)});w.pending.add(id);w.postMessage({type:'run',job:id,source:src.id,...msg});});}
// Horizontal bands of R, one per worker (small areas run as one job).
function bands(R,n){if(n<=1||R.w*R.h<120000||R.h<16)return[R];const k=Math.min(n,Math.floor(R.h/8)),out=[];let y=R.y;for(let i=0;i<k;i++){const h=Math.round((R.y+R.h-y)/(k-i));out.push({x:R.x,y,w:R.w,h});y+=h;}return out;}

// ----- a filter session: the layer as it was when the filter started -------------------------------------------------
let busy=null,last=null,fadeInfo=null,srcSeq=0;
const timing={};
function begin(def){
  if(!Ed.doc){Ed.message('Open an image first.',true);return null;}
  if(busy){Ed.message('A filter is still working. Wait for it to finish.',true);return null;}
  const why=Ed.editable({view:false});if(why){Ed.message(why,true);return null;}
  const doc=Ed.doc,L=Ed.layers.active(),target=doc.target,ref=target==='mask'?L.mask:L.canvas,sel=Ed.selection,Rr=U.area(sel?Ed.select.bounds():null);
  if(!Rr||!Rr.w||!Rr.h){Ed.message('The selection is empty.',true);return null;}
  const image=U.read(ref);let selA=null;
  if(sel){const s=U.read(sel).data;selA=new Uint8Array(doc.width*doc.height);for(let i=0;i<selA.length;i++)selA[i]=s[i*4+3];}
  const s={def,doc,L,target,ref,sel,R:Rr,lock:target==='pixels'&&!!L.locks.alpha,version:L.version,image,
    src:{id:++srcSeq,w:doc.width,h:doc.height,data:image.data,mask:target==='mask',sel:selA},seed:(Math.random()*2147483647)|0,pv:null,tmp:null};
  busy=s;return s;}
function end(s){if(busy===s)busy=null;s.src.dropped=true;dropSource(s.src.id);s.pv=s.tmp=null;}
function ctxOf(s,p){return{geo:{...s.R},fg:U.rgbOf(Ed.fg),bg:U.rgbOf(Ed.bg),seed:typeof p.seed==='number'?p.seed:s.seed,lock:s.lock};}
// Compute rectangle Rr with the given options; resolves to straight RGBA bytes of Rr.
async function compute(s,p,Rr,o={}){const parts=o.box?[Rr]:bands(Rr,Pool.size),prog=parts.map(()=>0),t0=performance.now(),msg={filter:s.def.id,params:p,ctx:ctxOf(s,p)};
  const res=await Promise.all(parts.map((P,i)=>job(o.box?Pool.size:i,s.src,{...msg,out:P},v=>{prog[i]=v;if(o.onProgress)o.onProgress(prog.reduce((a,b)=>a+b,0)/parts.length);})));
  if(!o.box){timing.id=s.def.id;timing.ms=Math.round(performance.now()-t0);timing.workers=Pool.broken?0:parts.length;timing.bands=parts.length;timing.work=Math.round(Math.max(...res.map(r=>r.ms)));}
  if(parts.length===1)return res[0].data;
  const out=new Uint8ClampedArray(Rr.w*Rr.h*4);parts.forEach((P,i)=>out.set(res[i].data,(P.y-Rr.y)*Rr.w*4));return out;}
// The result shown on the canvas exactly as applyToTarget will merge it: through the soft selection.
function blitR(g,src,Rr,op){g.save();g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.globalCompositeOperation=op;g.beginPath();g.rect(Rr.x,Rr.y,Rr.w,Rr.h);g.clip();g.drawImage(src,Rr.x,Rr.y,Rr.w,Rr.h,Rr.x,Rr.y,Rr.w,Rr.h);g.restore();}
function showResult(s,data,Rr){const W=s.doc.width,H=s.doc.height;if(!s.pv){s.pv=U.canvas(W,H);s.pv.getContext('2d').drawImage(s.ref,0,0);}
  const pv=s.pv,g=pv.getContext('2d'),img=new ImageData(data,Rr.w,Rr.h);
  if(!s.sel)U.write(pv,img,Rr);
  else{if(!s.tmp)s.tmp=U.canvas(W,H);U.write(s.tmp,img,Rr);const tg=s.tmp.getContext('2d');
    g.save();g.setTransform(1,0,0,1,0,0);g.clearRect(Rr.x,Rr.y,Rr.w,Rr.h);g.restore();blitR(g,s.ref,Rr,'source-over');
    blitR(tg,s.sel,Rr,'destination-in');blitR(g,s.sel,Rr,'destination-out');blitR(g,s.tmp,Rr,'lighter');}
  const a=Ed.preview.active;if(a&&a.canvas===pv&&a.layer===s.L)Ed.preview.update(Rr);else Ed.preview.layer({layer:s.L,canvas:pv,target:s.target});}
async function apply(s,p,data){
  if(Ed.doc!==s.doc||Ed.layers.active()!==s.L||Ed.doc.target!==s.target||s.L.version!==s.version||(s.target==='mask'?s.L.mask:s.L.canvas)!==s.ref){
    Ed.message(`The layer changed while ${s.def.name} was working, so it was not applied.`,true);return false;}
  const img=new ImageData(data,s.R.w,s.R.h),keep={...p};delete keep.seed;
  const ok=await Ed.applyToTarget(s.def.name,(g,info)=>{U.write(info.canvas,img,s.R);},{rect:s.R,op:{tool:'filter',filter:s.def.id,params:keep}});
  if(!ok)return false;
  // Last Filter repeats the options; a random filter (Add Noise, Clouds) gets a new pattern each time, as in Photoshop.
  last={id:s.def.id,params:keep};if(s.def.fields)U.store.set('ed-filter:'+s.def.id,keep);
  fadeInfo={name:s.def.name,L:s.L,target:s.target,R:{...s.R},before:s.image,version:Ed.doc.version,index:Ed.history.index};
  Ed.emit('menus');return true;}

// ----- running a filter without its dialog (no options, Last Filter, Ed.menu.run with params) -------------------------
async function runDirect(def,params){const s=begin(def);if(!s)return false;let pd=null,timer=0,canceled=false;
  try{const p=clean(def,params);
    timer=setTimeout(()=>{pd=progressDialog(def.name,()=>{canceled=true;s.src.dropped=true;cancelWorkers();});},300);
    const data=await compute(s,p,s.R,{onProgress:v=>pd&&pd.set(v)});
    clearTimeout(timer);if(pd)pd.close();if(canceled)return false;
    return await apply(s,p,data);}
  catch(e){clearTimeout(timer);if(pd)pd.close();if(!e.canceled&&!canceled){Ed.message(`${def.name} failed: ${e.message}`,true);console.error('[filters]',e);}return false;}
  finally{end(s);}}
function progressDialog(name,onCancel){const d=el('dialog','ed-dialog edf-busy');d.dataset.edDialog=name;d.setAttribute('aria-label',name);const f=el('form');f.method='dialog';
  const bar=el('div','edf-bar on'),fillI=el('i');bar.append(fillI);bar.setAttribute('role','progressbar');bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');bar.setAttribute('aria-valuenow','0');
  const c=btn('','Cancel'),acts=el('div','ed-dialog-actions');acts.append(c);
  f.append(el('h2','',name),el('p','ed-dialog-text','Working on it…'),bar,acts);d.append(f);
  const stop=()=>{if(d.open)d.close();d.remove();onCancel();};c.onclick=stop;d.addEventListener('cancel',e=>{e.preventDefault();stop();});
  document.body.append(d);d.showModal();
  return{set(v){const k=Math.round(clamp(v,0,1)*100);fillI.style.width=k+'%';bar.setAttribute('aria-valuenow',String(k));},close(){if(d.open)d.close();d.remove();}};}

// ----- controls for the filter dialogs --------------------------------------------------------------------------------
const fmt=(v,step)=>{const d=String(step).includes('.')?String(step).split('.')[1].length:0;return String(+Number(v).toFixed(d));};
function scrub(label,get,set,f){label.classList.add('ed-scrub');label.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();const x0=e.clientX,v0=get();let moved=false;label.setPointerCapture(e.pointerId);
  const move=ev=>{const dx=ev.clientX-x0;if(Math.abs(dx)>2)moved=true;if(!moved)return;const st=f.step||1;set(clamp(v0+Math.round(dx/2)*st*(ev.shiftKey?10:1),f.min,f.max));};
  const up=()=>{label.removeEventListener('pointermove',move);label.removeEventListener('pointerup',up);label.removeEventListener('pointercancel',up);};
  label.addEventListener('pointermove',move);label.addEventListener('pointerup',up);label.addEventListener('pointercancel',up);});}
// A number with a slider whose scale is stretched (curve > 1) so small radii get most of its length, as in Photoshop.
function rangeField(f,value,set){const wrap=el('div','ed-field ed-range edf-range');wrap.dataset.key=f.key;if(f.title)wrap.title=f.title;
  const id='edf-'+f.key+'-'+Math.random().toString(36).slice(2,6),label=el('label','ed-field-label',f.label);label.htmlFor=id;
  const num=el('input');num.type='number';num.id=id;num.name=f.key;num.min=f.min;num.max=f.max;num.step=f.step;
  const sl=el('input');sl.type='range';sl.min=0;sl.max=1000;sl.step=1;sl.tabIndex=-1;sl.setAttribute('aria-hidden','true');
  const k=f.min<0?1:f.curve||1,toPos=v=>Math.round(1000*Math.pow(clamp((v-f.min)/(f.max-f.min),0,1),1/k)),toVal=p=>{const v=f.min+(f.max-f.min)*Math.pow(p/1000,k);return +clamp(Math.round(v/f.step)*f.step,f.min,f.max).toFixed(4);};
  let cur=value;const show=(v,skipNum)=>{cur=v;if(!skipNum)num.value=fmt(v,f.step);sl.value=toPos(v);};show(value);
  sl.oninput=()=>{const v=toVal(+sl.value);num.value=fmt(v,f.step);cur=v;set(v);};
  num.oninput=()=>{if(num.value==='')return;const v=Number(num.value);if(!isFinite(v))return;const c=clamp(v,f.min,f.max);cur=c;sl.value=toPos(c);set(c);};
  num.onchange=()=>{const v=Number(num.value);show(isFinite(v)&&num.value!==''?clamp(v,f.min,f.max):cur);set(cur);};
  scrub(label,()=>cur,v=>{show(v);set(v);},f);
  wrap.append(label,sl,num);if(f.unit)wrap.append(el('span','ed-unit',f.unit));return{node:wrap,sync:v=>show(v)};}
// Photoshop's angle wheel: drag the dial or type the angle (counter-clockwise from 3 o'clock).
function angleField(f,value,set){const wrap=el('div','ed-field edf-angle');wrap.dataset.key=f.key;
  const label=el('label','ed-field-label',f.label),dial=el('span','edf-dial'),hand=el('i');dial.append(hand);dial.title='Drag to set the angle';
  const num=el('input');num.type='number';num.name=f.key;num.min=f.min;num.max=f.max;num.step=1;num.id='edf-'+f.key+'-'+Math.random().toString(36).slice(2,6);label.htmlFor=num.id;
  const show=v=>{num.value=String(Math.round(v));hand.style.transform=`rotate(${-v}deg)`;};show(value);
  num.oninput=()=>{const v=Number(num.value);if(num.value===''||!isFinite(v))return;const c=clamp(Math.round(v),f.min,f.max);hand.style.transform=`rotate(${-c}deg)`;set(c);};
  num.onchange=()=>{const v=clamp(Math.round(Number(num.value)||0),f.min,f.max);show(v);set(v);};
  dial.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();dial.setPointerCapture(e.pointerId);const at=ev=>{const r=dial.getBoundingClientRect();let a=Math.round(Math.atan2(-(ev.clientY-r.top-r.height/2),ev.clientX-r.left-r.width/2)*180/Math.PI);
      if(ev.shiftKey)a=Math.round(a/15)*15;show(a);set(a);};at(e);
    const mv=ev=>at(ev),up=()=>{dial.removeEventListener('pointermove',mv);dial.removeEventListener('pointerup',up);dial.removeEventListener('pointercancel',up);};
    dial.addEventListener('pointermove',mv);dial.addEventListener('pointerup',up);dial.addEventListener('pointercancel',up);});
  wrap.append(label,dial,num,el('span','ed-unit','°'));return{node:wrap,sync:show};}

// ----- the filter dialog ----------------------------------------------------------------------------------------------
const ZOOMS=[.0625,.125,.25,.3333,.5,.6667,1,2,3,4,6,8,12,16];
function openDialog(def,start){const s=begin(def);if(!s)return Promise.resolve(false);
  const fields=fieldsOf(def),p=clean(def,{...stored(def),...(start||{})}),defaults=defaultsOf(def);
  let closed=false,pvGen=0,pvTimer=0,boxTimer=0,lastResult=null,boxBusy=false,boxAgain=false,original=false,resolveFn;
  const done=new Promise(r=>{resolveFn=r;});
  const d=el('dialog','ed-dialog edf-dialog');d.dataset.edDialog=def.name;d.dataset.filter=def.id;d.setAttribute('aria-label',def.name);
  const form=el('form','edf-form');form.method='dialog';const head=el('h2','',def.name);
  // the preview box
  const view=el('div','edf-view'),cv=el('canvas','edf-canvas'),bar=el('div','edf-bar edf-view-bar'),barI=el('i');bar.append(barI);
  const zoomBox=el('div','edf-zoom'),zo=btn('edf-zoom-out','−','Zoom out'),zl=el('span','edf-zoom-value'),zi=btn('edf-zoom-in','+','Zoom in');zoomBox.append(zo,zl,zi);
  zo.setAttribute('aria-label','Zoom out');zi.setAttribute('aria-label','Zoom in');
  view.append(cv,bar,zoomBox);view.title='Drag to look around · hold the mouse down to see the original';cv.setAttribute('aria-label','Preview');
  const text=def.help?el('p','ed-dialog-text edf-help',def.help):null,fieldsBox=el('div','ed-dialog-fields'),actions=el('div','ed-dialog-actions');
  const pvLabel=el('label','ed-field ed-checkbox ed-preview-toggle'),pvBox=el('input');pvBox.type='checkbox';pvBox.name='preview';pvBox.checked=U.store.get('ed-filter-preview',true)!==false;pvLabel.append(pvBox,el('span','','Preview'));pvLabel.title='Show the result on the picture';
  const reset=btn('quiet','Reset','Back to the starting values'),cancel=btn('','Cancel'),ok=el('button','primary','OK');ok.type='submit';ok.value='ok';
  actions.append(pvLabel,reset,cancel,ok);form.append(head,view,...(text?[text]:[]),fieldsBox,actions);d.append(form);
  // fields
  const syncs={};
  const changed=()=>schedule();
  for(const f of fields){const set=v=>{p[f.key]=v;changed();};let r;
    if(f.type==='range')r=rangeField(f,p[f.key],set);else if(f.type==='angle')r=angleField(f,p[f.key],set);else r=Ed.ui.field(f,p[f.key],set);
    syncs[f.key]=r.sync;fieldsBox.append(r.node);}
  const first=fieldsBox.querySelector('input:not([type=range]),select');if(first)first.autofocus=true;else ok.autofocus=true;
  // the preview box: what part of the layer it shows
  const W=s.doc.width,H=s.doc.height,b0=s.R;let cx=b0.x+b0.w/2,cy=b0.y+b0.h/2,z=1;
  const boxSize=()=>({w:Math.max(80,view.clientWidth||340),h:Math.max(60,view.clientHeight||160)});
  {const bs=boxSize(),fit=Math.min(bs.w/Math.min(W,b0.w*1.15),bs.h/Math.min(H,b0.h*1.15));z=fit<1?1:Math.min(8,ZOOMS.filter(v=>v<=fit).pop()||1);}
  const region=()=>{const bs=boxSize(),vw=bs.w/z,vh=bs.h/z;cx=clamp(cx,Math.min(vw/2,W/2),Math.max(W-vw/2,W/2));cy=clamp(cy,Math.min(vh/2,H/2),Math.max(H-vh/2,H/2));
    const vis={x:cx-vw/2,y:cy-vh/2,w:vw,h:vh},x0=Math.max(0,Math.floor(vis.x)),y0=Math.max(0,Math.floor(vis.y)),x1=Math.min(W,Math.ceil(vis.x+vis.w)),y1=Math.min(H,Math.ceil(vis.y+vis.h));
    return{vis,R:{x:x0,y:y0,w:Math.max(1,x1-x0),h:Math.max(1,y1-y0)}};};
  const boxCanvas=U.canvas(1,1);
  function paintBox(bytes,Rb,vis){const dpr=Math.min(2,devicePixelRatio||1),bs=boxSize();cv.width=Math.round(bs.w*dpr);cv.height=Math.round(bs.h*dpr);const g=cv.getContext('2d');g.clearRect(0,0,cv.width,cv.height);
    if(boxCanvas.width!==Rb.w||boxCanvas.height!==Rb.h){boxCanvas.width=Rb.w;boxCanvas.height=Rb.h;}boxCanvas.getContext('2d').putImageData(new ImageData(bytes,Rb.w,Rb.h),0,0);
    g.imageSmoothingEnabled=z<1;g.imageSmoothingQuality='high';g.drawImage(boxCanvas,(Rb.x-vis.x)*z*dpr,(Rb.y-vis.y)*z*dpr,Rb.w*z*dpr,Rb.h*z*dpr);
    showZoom();view.dataset.zoom=String(z);view.dataset.region=[Rb.x,Rb.y,Rb.w,Rb.h,vis.x,vis.y].join(',');}
  function showZoom(){zl.textContent=(z>=1?Math.round(z*100):+(z*100).toFixed(1))+'%';zo.disabled=z<=ZOOMS[0];zi.disabled=z>=ZOOMS[ZOOMS.length-1];}
  const sourceBytes=Rb=>{const out=new Uint8ClampedArray(Rb.w*Rb.h*4),S=s.src.data;for(let y=0;y<Rb.h;y++)out.set(S.subarray(((Rb.y+y)*W+Rb.x)*4,((Rb.y+y)*W+Rb.x+Rb.w)*4),y*Rb.w*4);return out;};
  // The selection's soft edge, merged like the canvas: premultiplied mix of the result and the original.
  function throughSelection(res,Rb){const sel=s.src.sel;if(!sel)return res;const S=s.src.data,out=new Uint8ClampedArray(res.length);
    for(let y=0;y<Rb.h;y++)for(let x=0;x<Rb.w;x++){const i=(y*Rb.w+x)*4,j=(Rb.y+y)*W+Rb.x+x,o=j*4,k=sel[j]/255,a1=res[i+3]/255*k,a0=S[o+3]/255*(1-k),A=a1+a0;
      if(A<=0){out[i+3]=0;continue;}for(let c=0;c<3;c++)out[i+c]=(res[i+c]*a1+S[o+c]*a0)/A;out[i+3]=A*255;}
    return out;}
  async function renderBox(){if(closed)return;if(boxBusy){boxAgain=true;return;}const {vis,R:Rb}=region();
    if(original){paintBox(sourceBytes(Rb),Rb,vis);return;}
    boxBusy=true;boxAgain=false;view.classList.add('working');
    try{const res=await compute(s,{...p},Rb,{box:true});if(!closed&&!boxAgain&&!original){paintBox(throughSelection(res,Rb),Rb,vis);view.dataset.shown=JSON.stringify(p);}}
    catch(e){if(!e.canceled&&!closed){console.error('[filters] preview',e);Ed.message(`${def.name} failed: ${e.message}`,true);}}
    finally{boxBusy=false;view.classList.remove('working');if(boxAgain&&!closed)renderBox();}}
  // the canvas preview (Preview checkbox): the whole area, in bands, newest options only
  const big=s.R.w*s.R.h>400000;
  function schedule(){if(closed)return;clearTimeout(boxTimer);boxTimer=setTimeout(renderBox,12);clearTimeout(pvTimer);lastResult=null;
    if(pvBox.checked)pvTimer=setTimeout(runPreview,big?150:30);else progress(null);}
  async function runPreview(){const g=++pvGen,q={...p};cancelWorkers();progress(0);
    try{const data=await compute(s,q,s.R,{onProgress:v=>{if(g===pvGen)progress(v);}});if(g!==pvGen||closed)return;lastResult={key:JSON.stringify(q),data};if(pvBox.checked)showResult(s,data,s.R);progress(null);}
    catch(e){if(e.canceled||g!==pvGen||closed)return;progress(null);Ed.message(`${def.name} failed: ${e.message}`,true);console.error('[filters]',e);}}
  function progress(v){if(v==null){bar.classList.remove('on');barI.style.width='0';return;}bar.classList.add('on');barI.style.width=Math.round(v*100)+'%';}
  pvBox.onchange=()=>{U.store.set('ed-filter-preview',pvBox.checked);if(pvBox.checked){if(lastResult&&lastResult.key===JSON.stringify(p))showResult(s,lastResult.data,s.R);else{clearTimeout(pvTimer);runPreview();}}else{++pvGen;cancelWorkers();progress(null);Ed.preview.clear();}};
  // box interactions: drag to pan (showing the original while held, as Photoshop does), zoom buttons and keys
  view.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.edf-zoom'))return;e.preventDefault();view.setPointerCapture(e.pointerId);view.classList.add('dragging');const x0=e.clientX,y0=e.clientY,c0=[cx,cy];
    original=true;renderBox();
    const mv=ev=>{cx=c0[0]-(ev.clientX-x0)/z;cy=c0[1]-(ev.clientY-y0)/z;const {vis,R:Rb}=region();paintBox(sourceBytes(Rb),Rb,vis);};
    const up=()=>{view.removeEventListener('pointermove',mv);view.removeEventListener('pointerup',up);view.removeEventListener('pointercancel',up);view.classList.remove('dragging');original=false;renderBox();};
    view.addEventListener('pointermove',mv);view.addEventListener('pointerup',up);view.addEventListener('pointercancel',up);});
  const zoomBy=dir=>{const i=ZOOMS.indexOf(z),n=ZOOMS[clamp((i<0?ZOOMS.findIndex(v=>v>=z):i)+dir,0,ZOOMS.length-1)];if(n!==z){z=n;showZoom();renderBox();}};
  zo.onclick=()=>zoomBy(-1);zi.onclick=()=>zoomBy(1);
  d.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&!e.altKey&&(e.key==='+'||e.key==='='||e.key==='-'||e.key==='_')){e.preventDefault();e.stopPropagation();zoomBy(e.key==='-'||e.key==='_'?-1:1);}});
  reset.onclick=()=>{Object.assign(p,defaults);for(const f of fields)syncs[f.key](p[f.key]);changed();};
  cancel.onclick=()=>finish(false);
  d.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
  d.addEventListener('close',()=>{if(!closed)finish(false);});
  form.onsubmit=e=>{e.preventDefault();finish(true);};
  async function finish(accept){if(closed||(ok.disabled&&accept))return;
    if(!accept){closed=true;++pvGen;clearTimeout(pvTimer);clearTimeout(boxTimer);cancelWorkers();cancelWorkers('box');Ed.preview.clear();if(d.open)d.close();d.remove();end(s);resolveFn(false);return;}
    const q={...p},key=JSON.stringify(q);let data=lastResult&&lastResult.key===key?lastResult.data:null;
    if(!data){ok.disabled=true;for(const i of fieldsBox.querySelectorAll('input,select,button'))i.disabled=true;const g=++pvGen;clearTimeout(pvTimer);cancelWorkers();progress(0);
      try{data=await compute(s,q,s.R,{onProgress:v=>{if(g===pvGen)progress(v);}});}
      catch(e){if(e.canceled||closed)return;progress(null);Ed.message(`${def.name} failed: ${e.message}`,true);console.error('[filters]',e);ok.disabled=false;for(const i of fieldsBox.querySelectorAll('input,select,button'))i.disabled=false;return;}
      if(g!==pvGen||closed)return;}
    closed=true;clearTimeout(boxTimer);cancelWorkers('box');if(d.open)d.close();d.remove();Ed.preview.clear();
    let applied=false;try{applied=await apply(s,q,data);}finally{end(s);}resolveFn(applied);}
  document.body.append(d);d.showModal();
  requestAnimationFrame(()=>{if(!closed){renderBox();if(pvBox.checked)runPreview();}});
  done.element=d;return done;}

// ----- Edit > Fade ------------------------------------------------------------------------------------------------------
// Photoshop's Fade blends the last filter's result with the picture before it, with an opacity and a blend mode. Here the
// result's colors are first blended with the picture before (where that was opaque), then mixed with it, premultiplied, by
// the opacity: 0% gives the picture before the filter back exactly, 100% Normal the filter's result.
function fadeAvailable(){const f=fadeInfo;if(!f||!Ed.doc||busy)return false;
  return Ed.doc.version===f.version&&Ed.history.index===f.index&&Ed.layers.active()===f.L&&Ed.doc.target===f.target&&(f.target==='mask'?!!f.L.mask:true);}
function fadeMix(B,A,o,mode){const out=new Uint8ClampedArray(B.length),cb=[0,0,0],cs=[0,0,0];
  for(let i=0;i<B.length;i+=4){const ab=B[i+3]/255,aa=A[i+3]/255;let m0=A[i]/255,m1=A[i+1]/255,m2=A[i+2]/255;
    if(mode!=='normal'&&ab>0){cb[0]=B[i]/255;cb[1]=B[i+1]/255;cb[2]=B[i+2]/255;cs[0]=m0;cs[1]=m1;cs[2]=m2;const r=Ed.blends.mix(mode,cb,cs);
      m0=(1-ab)*m0+ab*clamp(r[0],0,1);m1=(1-ab)*m1+ab*clamp(r[1],0,1);m2=(1-ab)*m2+ab*clamp(r[2],0,1);}
    const ao=ab*(1-o)+aa*o;out[i+3]=ao*255;if(ao<=0)continue;const kb=ab*(1-o)/ao,ka=aa*o/ao;
    out[i]=B[i]*kb+255*m0*ka;out[i+1]=B[i+1]*kb+255*m1*ka;out[i+2]=B[i+2]*kb+255*m2*ka;}
  return out;}
function fadeData(f){const ref=f.target==='mask'?f.L.mask:f.L.canvas,R=f.R,W=f.before.width,after=U.read(ref,R).data,before=new Uint8ClampedArray(R.w*R.h*4);
  for(let y=0;y<R.h;y++)before.set(f.before.data.subarray(((R.y+y)*W+R.x)*4,((R.y+y)*W+R.x+R.w)*4),y*R.w*4);return{ref,before,after};}
const fadeModes=()=>Ed.blends.list().filter(b=>{const x=Ed.blends.get(b.id);return x&&(x.fn||b.id==='normal');});
function fadeParams(p){const o=clamp(Number(p&&p.opacity),0,100);return{opacity:isFinite(o)?o:100,mode:fadeModes().some(b=>b.id===(p&&p.mode))?p.mode:'normal'};}
function applyFade(f,dat,p){const q=fadeParams(p),img=new ImageData(fadeMix(dat.before,dat.after,q.opacity/100,q.mode),f.R.w,f.R.h);
  Ed.history.record('Fade '+f.name,tx=>{tx.touch(f.L,f.target,f.R);U.write(dat.ref,img,f.R);Ed.update(f.R);},{rect:f.R,op:{tool:'fade',filter:f.name,opacity:Math.round(q.opacity),mode:q.mode}});
  fadeInfo=null;Ed.emit('menus');return true;}
async function openFade(params){if(!fadeAvailable()){Ed.message('Fade works right after a filter, before anything else changes.',true);return false;}
  const why=Ed.editable({view:false});if(why){Ed.message(why,true);return false;}
  const f=fadeInfo,dat=fadeData(f);
  if(params&&typeof params==='object')return applyFade(f,dat,{opacity:100,mode:'normal',...params});
  const opts=[];let g=null;for(const b of fadeModes()){if(g&&g!==b.group)opts.push('-');g=b.group;opts.push({value:b.id,label:b.label});}
  let pv=null;
  const r=await Ed.dialog({title:'Fade',fields:[{key:'opacity',label:'Opacity',type:'range',min:0,max:100,step:1,unit:'%',default:100},{key:'mode',label:'Mode',type:'select',options:opts,default:'normal'}],
    preview(p){if(!fadeAvailable())return;const q=fadeParams(p),img=new ImageData(fadeMix(dat.before,dat.after,q.opacity/100,q.mode),f.R.w,f.R.h);
      if(!pv){pv=U.canvas(Ed.doc.width,Ed.doc.height);pv.getContext('2d').drawImage(dat.ref,0,0);}U.write(pv,img,f.R);
      const a=Ed.preview.active;if(a&&a.canvas===pv)Ed.preview.update(f.R);else Ed.preview.layer({layer:f.L,canvas:pv,target:f.target});},
    ok(p){if(!fadeAvailable()){Ed.message('The picture changed, so there is nothing to fade any more.',true);return;}applyFade(f,dat,p);}});
  return !!r;}

// ----- menus ------------------------------------------------------------------------------------------------------------
for(const f of LIST)Ed.registerMenu(pathOf(f),{order:f.order,enabled:()=>!!Ed.doc,action:p=>p&&typeof p==='object'?runDirect(f,p):f.fields?openDialog(f):runDirect(f,{})});
Ed.registerMenu('Filter/Last Filter',{order:1,shortcut:['Alt+Ctrl+F','Ctrl+F'],enabled:()=>!!Ed.doc&&!!last,
  label:()=>last?`Last Filter (${BY.get(last.id).name})`:'Last Filter',action:()=>last?runDirect(BY.get(last.id),last.params):false});
// Edit > Fade sits under Undo/Redo as in Photoshop.
Ed.registerMenu('Edit/Fade...',{order:25,separatorBefore:true,shortcut:'Shift+Ctrl+F',enabled:fadeAvailable,
  label:()=>fadeAvailable()?`Fade ${fadeInfo.name}...`:'Fade...',action:p=>openFade(p)});
Ed.on('open',()=>{fadeInfo=null;});   // Last Filter stays for the next picture, as in Photoshop
Ed.on('history',()=>{if(fadeInfo&&!fadeAvailable())fadeInfo=null;});

Ed.filters={list:()=>LIST.map(f=>({id:f.id,name:f.name,menu:f.menu,path:pathOf(f),dialog:!!f.fields,fields:fieldsOf(f).map(x=>x.key)})),
  get:id=>BY.get(id)||null,defaults:id=>BY.has(id)?defaultsOf(BY.get(id)):null,
  run:(id,params)=>{const f=BY.get(id);if(!f)return Promise.reject(Error('No filter '+id));return runDirect(f,params||{});},
  open:(id,params)=>{const f=BY.get(id);if(!f)return Promise.reject(Error('No filter '+id));return f.fields?openDialog(f,params):runDirect(f,params||{});},
  get last(){return last?{id:last.id,params:{...last.params}}:null;},get busy(){return !!busy;},get fade(){return fadeAvailable()?{name:fadeInfo.name,rect:{...fadeInfo.R}}:null;},
  timing,workers:()=>({size:Pool.size,broken:Pool.broken,alive:Pool.list.filter(Boolean).length}),
  resetWorkers(){for(const w of Pool.list)if(w)w.terminate();Pool.list=[];Pool.broken=false;for(const [id,j] of Pool.jobs){Pool.jobs.delete(id);j.reject(Object.assign(new Error('canceled'),{canceled:true}));}}};
})();
