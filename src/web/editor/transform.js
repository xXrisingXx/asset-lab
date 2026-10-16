'use strict';
// Moving and transforming for the Asset Lab editor, built on window.Ed alone (the whole API is at the top of editor/core.js):
// - Move Tool (V): drag moves the active layer and its mask, or lifts the selected pixels and moves them (the hole becomes
//   transparent, the selection moves with them); Alt-drag copies; arrows nudge 1 px, Shift+arrows 10 px; Shift-drag keeps
//   45° directions; Auto-Select (Ctrl-click does it once); Align buttons (to the selection, or the canvas). Locked
//   position and hidden layers refuse. Consecutive moves keep what went past the canvas edge, as long as nothing else
//   changed in between (Photoshop keeps it for good; a game sprite has no room outside its frame).
// - Free Transform (Ctrl+T, a mode of the Move tool) and Edit > Transform (Again, Scale, Rotate, Skew, Distort,
//   Perspective, Rotate 180° / 90°, Flip): a box with 8 handles; corners scale proportionally (Shift: freely), Alt from the
//   reference point, Ctrl distorts, Ctrl+Shift skews, Ctrl+Alt+Shift makes perspective; outside rotates (Shift: 15° steps);
//   inside moves; arrows nudge. The options bar has the reference point, X, Y, W, H (linked), the angle and the
//   interpolation (Nearest Neighbor keeps pixel art crisp, Bilinear, Bicubic). Enter applies, Esc cancels, Ctrl+Z steps
//   back inside the transform. Results come from editor/transform-resample.js (premultiplied alpha, Pillow's filtering for
//   reductions) in a worker; while a handle moves, the GPU draws a quick preview. With a selection only the selected pixels
//   change (and the selection with them). Select > Transform Selection changes the outline only.
// - Image > Image Rotation (180°, 90° on square images, Flip Canvas) for every layer, mask and the selection.
// - Edit > Cut, Copy, Copy Merged, Paste, Paste Special (in Place, Into, Outside): an exact internal clipboard; Copy also
//   puts a PNG on the system clipboard and Ctrl+V takes pictures copied in other apps (the browser's paste event; no
//   permission prompt). Pictures bigger than the canvas are fitted and open in Free Transform. Image files dropped on the
//   picture become layers.
// - File > Import PSD as Layers and Export Layered PSD (editor/transform-psd.js).
// Ed.transform = {start(o), commit(), cancel(), active, move(dx, dy, o), flip(axis), rotate(deg), align(edge), copy(o), cut(),
//   paste(mode), pasteBlob(blob, mode), clipboard, exportPSD(o), importPSD(file, o), canvasRotation(how), setQuad(Q),
//   again()} for other modules and tests.
(()=>{
const Ed=window.Ed;if(!Ed||Ed.transform)return;
const U=Ed.util,g2=c=>c.getContext('2d'),byId=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const button=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const say=(t,err=false)=>Ed.message(t,err);
const docRect=()=>({x:0,y:0,w:Ed.doc.width,h:Ed.doc.height});
const q=L=>`“${L.name}”`;
const round=(v,n=2)=>Math.round(v*10**n)/10**n;
const area=r=>U.area(r);
function plain(g){g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.globalCompositeOperation='source-over';g.filter='none';g.imageSmoothingEnabled=true;}

// ----- helper scripts: the resampler (page copy + worker) and the PSD reader/writer ----------------------------------------
const HERE=(document.currentScript&&document.currentScript.src)||new URL('editor/transform.js',location.href).href;
const fileURL=name=>new URL(name,HERE).href;
const loading={};
function load(name){return loading[name]||(loading[name]=new Promise((ok,fail)=>{const s=document.createElement('script');s.src=fileURL(name);s.async=true;
  s.onload=()=>ok(true);s.onerror=()=>{delete loading[name];fail(Error('The editor file '+name+' could not be loaded. Reload the page.'));};document.head.append(s);}));}
load('transform-resample.js').catch(()=>{});
let worker=null,workerDead=false,jobSeq=0;const jobs=new Map();
function getWorker(){if(worker||workerDead)return worker;
  try{worker=new Worker(fileURL('transform-resample.js'));
    worker.onmessage=e=>{const m=e.data||{},j=jobs.get(m.id);if(!j)return;jobs.delete(m.id);if(m.error)j.fail(Error(m.error));else j.ok(m.out===undefined?true:m.out);};
    worker.onerror=e=>{e.preventDefault();workerDead=true;worker=null;for(const j of jobs.values())j.fail(Error('The transform worker stopped.'));jobs.clear();};}
  catch(e){workerDead=true;worker=null;}
  return worker;}
function job(msg,transfer){const w=getWorker();if(!w)return Promise.reject(Error('no worker'));const id=++jobSeq;
  return new Promise((ok,fail)=>{jobs.set(id,{ok,fail});try{w.postMessage({...msg,id},transfer||[]);}catch(e){jobs.delete(id);fail(e);}});}

// ----- geometry: 3 x 3 projective matrices (row-major) and quads [[x, y] x 4] (top-left, top-right, bottom-right, bottom-left)
const M3={
  inv(m){const[a,b,c,d,e,f,g,h,i]=m,A=e*i-f*h,B=f*g-d*i,C=d*h-e*g,det=a*A+b*B+c*C;if(!det||!isFinite(det))return null;
    return[A/det,(c*h-b*i)/det,(b*f-c*e)/det,B/det,(a*i-c*g)/det,(c*d-a*f)/det,C/det,(b*g-a*h)/det,(a*e-b*d)/det];},
  mul(p,r){const o=new Array(9);for(let i=0;i<3;i++)for(let j=0;j<3;j++)o[i*3+j]=p[i*3]*r[j]+p[i*3+1]*r[3+j]+p[i*3+2]*r[6+j];return o;},
  apply(m,x,y){const w=m[6]*x+m[7]*y+m[8];return[(m[0]*x+m[1]*y+m[2])/w,(m[3]*x+m[4]*y+m[5])/w];},
  T:(x,y)=>[1,0,x,0,1,y,0,0,1],S:(x,y)=>[x,0,0,0,y,0,0,0,1]};
// cos and sin of an angle in degrees, exact for multiples of 90 (so quarter turns stay pixel-exact)
function cs(deg){const k=deg/90;if(Math.abs(k-Math.round(k))<1e-12)return[[1,0],[0,1],[-1,0],[0,-1]][((Math.round(k)%4)+4)%4];const r=deg*Math.PI/180;return[Math.cos(r),Math.sin(r)];}
const R3=(deg,cx=0,cy=0)=>{const[c,s]=cs(deg);return M3.mul(M3.T(cx,cy),M3.mul([c,-s,0,s,c,0,0,0,1],M3.T(-cx,-cy)));};
function squareToQuad(Q){const[[x0,y0],[x1,y1],[x2,y2],[x3,y3]]=Q,sx=x0-x1+x2-x3,sy=y0-y1+y2-y3;let g=0,h=0;
  if(Math.abs(sx)>1e-9||Math.abs(sy)>1e-9){const dx1=x1-x2,dx2=x3-x2,dy1=y1-y2,dy2=y3-y2,det=dx1*dy2-dx2*dy1;if(Math.abs(det)<1e-12)return null;g=(sx*dy2-dx2*sy)/det;h=(dx1*sy-sx*dy1)/det;}
  return[x1-x0+g*x1,x3-x0+h*x3,x0,y1-y0+g*y1,y3-y0+h*y3,y0,g,h,1];}
function rectToQuad(r,Q){const s=squareToQuad(Q);return s&&M3.mul(s,[1/r.w,0,-r.x/r.w,0,1/r.h,-r.y/r.h,0,0,1]);}
const cornersOf=r=>[[r.x,r.y],[r.x+r.w,r.y],[r.x+r.w,r.y+r.h],[r.x,r.y+r.h]];
const quadOf=(M,r)=>cornersOf(r).map(([x,y])=>M3.apply(M,x,y));
const isAffine=M=>Math.abs(M[6])<1e-12&&Math.abs(M[7])<1e-12;
function bboxOf(Q,pad=0){const xs=Q.map(p=>p[0]),ys=Q.map(p=>p[1]);const x=Math.floor(Math.min(...xs))-pad,y=Math.floor(Math.min(...ys))-pad;
  return{x,y,w:Math.ceil(Math.max(...xs))+pad-x,h:Math.ceil(Math.max(...ys))+pad-y};}
function convex(Q){let sign=0;for(let i=0;i<4;i++){const[a,b,c]=[Q[i],Q[(i+1)%4],Q[(i+2)%4]],z=(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);
    if(Math.abs(z)<1e-9)return false;const s=Math.sign(z);if(sign&&s!==sign)return false;sign=s;}return true;}
function jac(M,x,y){const w=M[6]*x+M[7]*y+M[8],u=(M[0]*x+M[1]*y+M[2])/w,v=(M[3]*x+M[4]*y+M[5])/w;return[(M[0]-M[6]*u)/w,(M[1]-M[7]*u)/w,(M[3]-M[6]*v)/w,(M[4]-M[7]*v)/w];}
// What Photoshop's options bar shows for a map: W and H in %, the angle (clockwise on screen) and the horizontal skew.
function decompose(M,r){const[a,b,c,d]=jac(M,r.x+r.w/2,r.y+r.h/2),det=a*d-b*c;let sx=Math.hypot(a,c),th=Math.atan2(c,a);
  if(det<0&&Math.abs(th)>Math.PI/2+1e-9){sx=-sx;th+=th>0?-Math.PI:Math.PI;}   // a mirror shows as a negative W, not a 180° turn
  const co=Math.cos(th),si=Math.sin(th),sy=sx?det/sx:0,t=sy?(co*b+si*d)/sy:0;
  return{w:sx*100,h:sy*100,angle:th*180/Math.PI,hskew:Math.atan(t)*180/Math.PI};}
// The linear part Photoshop builds from those fields: R(angle) . [[1, tan h], [tan v, 1]] . diag(W, H)

// ----- what is on a canvas: bounds of alpha > 0, or of values unlike a mask's default (its most common border value) ------
const scans=new WeakMap();
function scan(canvas,version,mask=false){const hit=scans.get(canvas);if(hit&&version!=null&&hit.version===version&&hit.mask===mask)return hit;
  const W=canvas.width,H=canvas.height,d=U.read(canvas).data;let edge=0;
  if(mask){const n=new Uint32Array(256);for(let x=0;x<W;x++){n[d[x*4+3]]++;n[d[((H-1)*W+x)*4+3]]++;}for(let y=0;y<H;y++){n[d[y*W*4+3]]++;n[d[(y*W+W-1)*4+3]]++;}
    for(let v=0;v<256;v++)if(n[v]>n[edge])edge=v;}
  let x0=W,y0=H,x1=-1,y1=-1;
  for(let y=0;y<H;y++){const r=y*W*4;let a=-1,b=-1;for(let x=0;x<W;x++){const v=d[r+x*4+3];if(mask?v!==edge:v){if(a<0)a=x;b=x;}}
    if(a>=0){if(a<x0)x0=a;if(b>x1)x1=b;if(y<y0)y0=y;y1=y;}}
  const out={version,mask,edge,bounds:x1<0?null:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1}};if(version!=null)scans.set(canvas,out);return out;}
const layerScan=L=>scan(L.canvas,L.version,false);
const maskScan=L=>L.mask?scan(L.mask,L.version,true):null;
const linkedMask=L=>!!L.mask&&L.kind==='pixel';   // a pixel layer's mask moves with it
function clone(c){const n=U.canvas(c.width,c.height);g2(n).drawImage(c,0,0);return n;}
function through(src,sel,op){const c=clone(src),g=g2(c);g.globalCompositeOperation=op;g.drawImage(sel,0,0);g.globalCompositeOperation='source-over';return c;}
const lift=(src,sel)=>[through(src,sel,'destination-in'),through(src,sel,'destination-out')];   // [what moves, what stays]
function fillMask(c,v,R){const g=g2(c);g.save();plain(g);if(R){g.beginPath();g.rect(R.x,R.y,R.w,R.h);g.clip();}g.clearRect(0,0,c.width,c.height);if(v>0){g.fillStyle=`rgba(255,255,255,${v/255})`;g.fillRect(0,0,c.width,c.height);}g.restore();}
const imageData=(data,w,h)=>new ImageData(data instanceof Uint8ClampedArray?data:new Uint8ClampedArray(data.buffer,data.byteOffset,data.byteLength),w,h);
const bgValue=()=>Math.round(U.luma(Ed.bg));
// The selection without a history state: inside an open transaction it becomes part of that state (core hook).
function quietSelect(mask){Ed.select.set(mask,'replace',{history:false});}

// Can the active layer move / change its pixels? '' or a plain reason (whole sentences, so they can be translated).
const WHY={move:{none:'There is no layer to move.',hidden:'is hidden. Show it to move it.',position:'is locked in place. Unlock its position in the Layers panel to move it.',
    pixels:'has locked pixels. Unlock them in the Layers panel to move them.',adjustment:'is an adjustment layer and has nothing to move.'},
  transform:{none:'There is no layer to transform.',hidden:'is hidden. Show it to transform it.',position:'is locked in place. Unlock its position in the Layers panel to transform it.',
    pixels:'has locked pixels. Unlock them in the Layers panel to transform them.',adjustment:'is an adjustment layer and has nothing to transform.'}};
function refuse(L,{pixels=false,verb='move'}={}){const w=WHY[verb];
  if(!Ed.doc)return 'Open an image first.';const v=Ed.view.blocked;if(v)return v;if(!L)return w.none;
  if(!L.visible)return `${q(L)} ${w.hidden}`;
  if(L.locks.all||L.locks.position)return `${q(L)} ${w.position}`;
  if(pixels&&L.locks.pixels&&Ed.doc.target==='pixels')return `${q(L)} ${w.pixels}`;
  if(pixels&&Ed.doc.target==='pixels'&&L.kind==='adjustment'&&!L.mask)return `${q(L)} ${w.adjustment}`;
  return '';}

// =========================================================================================================================
// MOVE TOOL
// =========================================================================================================================
// A move "memo" remembers what the last move lifted, so the next drag or nudge continues from the original pixels: what
// went past the canvas edge comes back, and a floating selection does not eat the pixels it passes over.
let memo=null;
function memoValid(L,kind){const m=memo;return !!m&&m.layer===L&&m.kind===kind&&L.version===m.version&&Ed.history.index===m.hist&&Ed.selection===m.selAfter&&Ed.doc.target===m.target&&!!L.mask===!!m.hadMask;}
// Build what a move works on. kind 'layer' (the whole layer and its linked mask) or 'selection' (lifted from the target).
function moveSource(L,copy){const sel=Ed.selection,target=Ed.doc.target;
  if(!sel){if(memoValid(L,'layer')&&!copy)return memo;
    const sc=L.kind==='pixel'?layerScan(L):null,ms=L.mask&&(linkedMask(L)||L.kind==='adjustment'||target==='mask')?maskScan(L):null;
    return{kind:'layer',layer:L,target,copy,pixels:L.kind==='pixel'&&!(target==='mask'&&!linkedMask(L))?clone(L.canvas):null,pbounds:sc&&sc.bounds,
      mask:ms?clone(L.mask):null,edge:ms?ms.edge:0,mbounds:ms&&ms.bounds,dx:0,dy:0,hadMask:!!L.mask};}
  if(memoValid(L,'selection')&&!copy)return memo;
  const sb=Ed.select.bounds(),src=target==='mask'?L.mask:L.canvas;
  if(target==='mask'){ // mask values move with the selection's coverage; the hole takes the background color's gray
    const V=clone(src),C=clone(sel),base=U.read(src),cov=U.read(sel).data,bg=bgValue(),d=base.data;
    if(!copy)for(let i=3;i<d.length;i+=4){const c=cov[i]/255;if(c)d[i]=Math.round(d[i]*(1-c)+bg*c);}
    return{kind:'selection',layer:L,target,copy,values:V,cover:C,base:d,sel,sbounds:sb,dx:0,dy:0,hadMask:!!L.mask};}
  const[inside,outside]=lift(src,sel);
  return{kind:'selection',layer:L,target,copy,pixels:inside,base:copy?clone(src):outside,sel,sbounds:sb,dx:0,dy:0,lockAlpha:!!L.locks.alpha,hadMask:!!L.mask};}
// Draw the moved result of m at offset (dx, dy) into ctx (document-sized), only inside R.
function drawMoved(g,m,dx,dy,R,which){g.save();plain(g);g.beginPath();g.rect(R.x,R.y,R.w,R.h);g.clip();g.clearRect(R.x,R.y,R.w,R.h);
  if(which==='mask'){fillMaskCtx(g,m.edge,R);if(m.mask){g.clearRect(dx,dy,m.mask.width,m.mask.height);g.drawImage(m.mask,dx,dy);}}
  else{if(m.base&&m.kind==='selection'&&m.target==='pixels')g.drawImage(m.base,0,0);if(m.copy&&m.kind==='layer')g.drawImage(m.layer.canvas,0,0);if(m.pixels)g.drawImage(m.pixels,dx,dy);}
  g.restore();}
function fillMaskCtx(g,v,R){if(v>0){g.fillStyle=`rgba(255,255,255,${v/255})`;g.fillRect(R.x,R.y,R.w,R.h);}}
// Mask-target selection moves: values lerp over the base by the moved coverage (CPU, only inside R).
function moveMaskValues(m,dx,dy,R){const W=Ed.doc.width,V=U.read(m.values).data,C=U.read(m.cover).data,out=new ImageData(R.w,R.h),o=out.data;
  for(let y=0;y<R.h;y++)for(let x=0;x<R.w;x++){const X=R.x+x,Y=R.y+y,i=(y*R.w+x)*4,b=m.base[(Y*W+X)*4+3],sx=X-dx,sy=Y-dy;let v=b;
    if(sx>=0&&sy>=0&&sx<W&&sy<Ed.doc.height){const j=(sy*W+sx)*4,c=C[j+3]/255;if(c)v=b*(1-c)+V[j+3]*c;}o[i]=o[i+1]=o[i+2]=255;o[i+3]=Math.round(v);}
  return out;}
const shift=(r,dx,dy)=>r&&{x:r.x+dx,y:r.y+dy,w:r.w,h:r.h};
// The document area a move from offset a to offset b changes.
function moveArea(m,a,b){const W=Ed.doc.width,H=Ed.doc.height;let R=null;
  const add=r=>{R=U.union(R,r&&area(r));};
  if(m.kind==='layer'){if(m.pixels&&m.pbounds){add(shift(m.pbounds,a.x,a.y));add(shift(m.pbounds,b.x,b.y));}
    if(m.mask){if(m.mbounds){add(shift(m.mbounds,a.x,a.y));add(shift(m.mbounds,b.x,b.y));}
      if(m.edge&&(a.x!==b.x||a.y!==b.y))R=area({x:0,y:0,w:W,h:H});}}   // the uncovered strip takes the default value
  else{add(m.sbounds);add(shift(m.sbounds,a.x,a.y));add(shift(m.sbounds,b.x,b.y));}
  return R;}
let preview=null;   // {px, mask, m}: the move preview's canvases
function showMove(m,dx,dy,prev){const d=Ed.doc,L=m.layer;
  if(!preview||preview.m!==m){preview={m,px:null,mask:null,at:{x:m.dx,y:m.dy}};
    if(m.kind==='layer'){if(m.pixels||m.copy)preview.px=clone(L.canvas);if(m.mask)preview.mask=clone(L.mask);}
    else if(m.target==='mask')preview.mask=clone(L.mask);else preview.px=clone(L.canvas);
    if(m.kind==='layer'&&m.target==='mask'&&!preview.px)Ed.preview.layer({layer:L,canvas:preview.mask,target:'mask'});
    else if(m.kind==='selection'&&m.target==='mask')Ed.preview.layer({layer:L,canvas:preview.mask,target:'mask'});
    else Ed.preview.layer({layer:L,canvas:preview.px||L.canvas,mask:preview.mask||undefined,target:'pixels'});}
  const R=moveArea(m,prev||preview.at,{x:dx,y:dy});preview.at={x:dx,y:dy};if(!R)return;
  if(m.kind==='selection'&&m.target==='mask')U.write(preview.mask,moveMaskValues(m,dx,dy,R),R);
  else{if(preview.px&&(m.pixels||m.copy))drawMoved(g2(preview.px),m,dx,dy,R,'pixels');if(preview.mask&&m.mask)drawMoved(g2(preview.mask),m,dx,dy,R,'mask');}
  Ed.preview.update(R);Ed.view.redrawOverlay();}
function endPreview(){if(preview){preview=null;Ed.preview.clear();}}
// Apply a move as one history state. m from moveSource; (dx, dy) is the total offset from m's original pixels.
function commitMove(m,dx,dy,label){const L=m.layer,d=Ed.doc,before={x:m.dx,y:m.dy};endPreview();
  if(m.kind==='layer'&&m.copy){ // Alt-drag without a selection: a moved copy of the layer above it
    Ed.history.record('Duplicate Layer',tx=>{const C=Ed.layers.duplicate(L,{history:false});if(!C)return;
      const W=d.width,H=d.height,g=g2(C.canvas);g.save();plain(g);g.clearRect(0,0,W,H);if(m.pixels&&!(m.target==='mask'&&!linkedMask(L)))g.drawImage(m.pixels,dx,dy);g.restore();
      if(C.mask&&m.mask){const gm=g2(C.mask);gm.save();plain(gm);gm.clearRect(0,0,W,H);fillMaskCtx(gm,m.edge,{x:0,y:0,w:W,h:H});gm.clearRect(dx,dy,W,H);gm.drawImage(m.mask,dx,dy);gm.restore();}
      C.version++;},{op:{tool:'move',copy:true,dx,dy}});
    const C=Ed.layers.active();memo=C!==L?{...moveSource(C,false),dx:0,dy:0}:null;
    if(memo){memo.pixels=clone(C.canvas);memo.mask=C.mask?clone(C.mask):null;memo.pbounds=shift(m.pbounds,dx,dy);memo.mbounds=m.mbounds&&shift(m.mbounds,dx,dy);seal(memo);}
    return true;}
  const R=moveArea(m,before,{x:dx,y:dy});if(!R){memo=null;return false;}
  Ed.history.record(label,tx=>{
    if(m.kind==='layer'){if(m.pixels&&!(m.target==='mask'&&!linkedMask(L))){tx.touch(L,'pixels',R);drawMoved(g2(L.canvas),m,dx,dy,R,'pixels');}
      if(m.mask&&L.mask){tx.touch(L,'mask',R);drawMoved(g2(L.mask),m,dx,dy,R,'mask');}}
    else if(m.target==='mask'){tx.touch(L,'mask',R);U.write(L.mask,moveMaskValues(m,dx,dy,R),R);}
    else{const old=m.lockAlpha?U.read(L.canvas,R):null;tx.touch(L,'pixels',R);drawMoved(g2(L.canvas),m,dx,dy,R,'pixels');
      if(old){const now=U.read(L.canvas,R);for(let i=3;i<now.data.length;i+=4)now.data[i]=old.data[i];U.write(L.canvas,now,R);}}
    if(m.kind==='selection'&&m.sel){const s=U.canvas(d.width,d.height);g2(s).drawImage(m.sel,dx,dy);quietSelect(s);}},
    {op:{tool:'move',dx,dy,selection:m.kind==='selection'}});
  m.dx=dx;m.dy=dy;memo=m;seal(m);return true;}
function seal(m){m.version=m.layer.version;m.hist=Ed.history.index;m.selAfter=Ed.selection;m.target=Ed.doc.target;m.hadMask=!!m.layer.mask;
  if(m.kind==='selection'&&m.copy){m.copy=false;}}   // a copied float keeps its base (the pixels it was copied from)
// Move the active layer (or the selected pixels) by (dx, dy) as one state, e.g. a nudge.
function moveBy(dx,dy,o={}){const L=Ed.layers.active();const why=refuse(L,{pixels:!!Ed.selection,verb:'move'});if(why){say(why);return false;}
  if(session){session.nudge(dx,dy);return true;}
  const m=moveSource(L,!!o.copy);if(m.kind==='layer'&&!m.pixels&&!m.mask){say(`There is nothing on ${q(L)} to move.`);return false;}
  if(m.kind==='selection'&&!Ed.selection){return false;}
  return commitMove(m,m.dx+dx,m.dy+dy,o.label||'Nudge');}
// Auto-Select: the topmost visible layer with a pixel at (x, y), masks and clipping bases included.
function layerAt(x,y){const d=Ed.doc,X=Math.floor(x),Y=Math.floor(y);if(X<0||Y<0||X>=d.width||Y>=d.height)return null;const list=Ed.layers.list(),R={x:X,y:Y,w:1,h:1};
  const alpha=(L)=>{let a=U.read(L.canvas,R).data[3];if(a&&L.mask&&L.maskEnabled)a=a*U.read(L.mask,R).data[3]/255;return a;};
  for(let i=list.length-1;i>=0;i--){const L=list[i];if(!L.visible||L.kind!=='pixel'||L.opacity<=0)continue;if(alpha(L)<1)continue;
    if(L.clip){let b=i-1;while(b>0&&list[b].clip)b--;const B=list[b];if(!B.visible||B.kind!=='pixel'||alpha(B)<1)continue;}return L;}
  return null;}
const MOVE_ICON=['M4.5 3.5l5 12.5 1.8-5.3 5.2-1.8z','M17.5 13v8M13.5 17h8','M17.5 13l-1.5 1.5M17.5 13l1.5 1.5M17.5 21l-1.5-1.5M17.5 21l1.5-1.5M13.5 17l1.5-1.5M13.5 17l1.5 1.5M21.5 17l-1.5-1.5M21.5 17l-1.5 1.5'];
const ALIGN=[['left','Align left edges','M4 3v18|M7 6h11v4H7z|M7 14h7v4H7z'],['hcenter','Align horizontal centers','M12 3v18|M6 6h12v4H6z|M8.5 14h7v4h-7z'],['right','Align right edges','M20 3v18|M6 6h11v4H6z|M10 14h7v4h-7z'],
  ['top','Align top edges','M3 4h18|M6 7h4v11H6z|M14 7h4v7h-4z'],['vcenter','Align vertical centers','M3 12h18|M6 6h4v12H6z|M14 8.5h4v7h-4z'],['bottom','Align bottom edges','M3 20h18|M6 6h4v11H6z|M14 10h4v7h-4z']];
let drag=null;   // the Move tool's drag
const moveTool={label:'Move Tool',group:'move',key:'v',cursor:'move',edits:false,icon:()=>Ed.icon(MOVE_ICON),
  options:[{key:'autoSelect',label:'Auto-Select',type:'checkbox',default:false,title:'Auto-Select Layer: a click picks the topmost layer with a visible pixel under the pointer (Ctrl-click does it once)'},
    {key:'align',type:'ed-tf-align',title:'Align the layer to the selection, or to the canvas when nothing is selected'}],
  onDown(p,e){drag=null;const d=Ed.doc;if(!d)return false;const blocked=Ed.view.blocked;if(blocked){say(blocked);return false;}
    const o=Ed.toolOptions('move');
    if(!!o.autoSelect!==!!p.ctrlKey){const L=layerAt(p.x,p.y);if(L&&L!==Ed.layers.active())Ed.layers.setActive(L);}
    const L=Ed.layers.active(),why=refuse(L,{pixels:!!Ed.selection,verb:'move'});if(why){say(why);return false;}
    const m=moveSource(L,!!p.altKey);if(m.kind==='layer'&&!m.pixels&&!m.mask){say(`There is nothing on ${q(L)} to move.`);return false;}
    drag={m,x:p.x,y:p.y,dx:m.dx,dy:m.dy,moved:false};this.cursor=p.altKey?'copy':'move';},
  onMove(p){const D=drag;if(!D)return;let dx=Math.round(p.x-D.x),dy=Math.round(p.y-D.y);
    if(p.shiftKey){const a=Math.atan2(dy,dx),s=Math.round(a/(Math.PI/4))*Math.PI/4,len=Math.hypot(dx,dy)*Math.cos(a-s);dx=Math.round(len*Math.cos(s));dy=Math.round(len*Math.sin(s));}
    const X=D.m.dx+dx,Y=D.m.dy+dy;if(X===D.dx&&Y===D.dy&&D.moved)return;const prev={x:D.dx,y:D.dy};D.dx=X;D.dy=Y;
    if(!D.moved){if(!dx&&!dy)return;D.moved=true;showMove(D.m,X,Y,{x:D.m.dx,y:D.m.dy});return;}showMove(D.m,X,Y,prev);},
  onUp(){const D=drag;drag=null;if(!D)return;if(!D.moved){endPreview();return;}
    if(D.dx===D.m.dx&&D.dy===D.m.dy&&!D.m.copy){endPreview();return;}
    commitMove(D.m,D.dx,D.dy,'Move');Ed.view.redrawOverlay();},
  onHover(p){this.cursor=p.altKey?'copy':'move';},
  onKey(e){if(e.defaultPrevented||!/^Arrow/.test(e.key)||e.ctrlKey||e.metaKey)return false;
    const n=e.shiftKey?10:1,dx=e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0,dy=e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0;
    if(drag)return true;moveBy(dx,dy,{copy:e.altKey});return true;},
  onCancel(){if(drag){drag=null;endPreview();}},
  onDeactivate(){if(drag){drag=null;endPreview();}},
  onOptions(){Ed.view.redrawOverlay();},
  drawOverlay(ctx,view){if(drag&&drag.moved&&drag.m.kind==='selection')ghostOutline(ctx,view,M3.T(drag.dx-drag.m.dx,drag.dy-drag.m.dy));}};
Ed.registerTool('move',moveTool);

// =========================================================================================================================
// FREE TRANSFORM
// =========================================================================================================================
let session=null,lastTransform=null;
const MODES={free:'Free Transform',scale:'Scale',rotate:'Rotate',skew:'Skew',distort:'Distort',perspective:'Perspective'};
// The box a transform starts from: the layer's content, the selected part of it, or its mask's painted part.
function transformBox(L){if(!L||!Ed.doc)return null;const sel=Ed.selection,target=Ed.doc.target;
  if(sel){const src=target==='mask'?L.mask:L.canvas;if(!src)return null;
    if(target==='mask')return{kind:'selection',rect:Ed.select.bounds()};
    const inside=through(src,sel,'destination-in'),b=scan(inside,null).bounds;return b?{kind:'selection',rect:b,inside}:null;}
  if(L.kind==='adjustment'||target==='mask'){if(!L.mask)return null;const s=maskScan(L);return{kind:'mask',rect:s.bounds||docRect(),edge:s.edge};}
  const s=layerScan(L);if(s.bounds)return{kind:'layer',rect:s.bounds};
  if(linkedMask(L)){const m=maskScan(L);if(m.bounds)return{kind:'layer',rect:m.bounds};}
  return null;}
// o = {mode, kind: 'outline' (the selection's outline only), paste: {layer, canvas, rect (in the image), quad}, quad,
//      label, interp, apply: true (commit at once, no handles)}
function startTransform(o={}){
  if(session){if(o.mode&&o.mode!==session.mode){session.mode=o.mode;session.label=MODES[o.mode];Ed.view.redrawOverlay();}return session;}
  const d=Ed.doc;if(!d){say('Open an image first.');return null;}const blocked=Ed.view.blocked;if(blocked){say(blocked);return null;}
  const s={mode:o.mode||'free',label:o.label||MODES[o.mode||'free'],interp:o.interp||Ed.toolOptions('transform').interp||'bicubic',planes:[],rev:0,undo:[],redo:[],ref:{u:.5,v:.5},prevTool:Ed.tool};
  const W=d.width,H=d.height;
  if(o.paste){const P=o.paste;Object.assign(s,{kind:'paste',layer:P.layer,rect:P.rect,quad:P.quad.map(p=>p.slice()),oldArea:area(bboxOf(P.quad,2))});
    s.planes.push({id:'px',src:P.canvas,region:P.rect});}
  else if(o.kind==='outline'){if(!Ed.selection){say('Select an area first.');return null;}
    const b=Ed.select.bounds();Object.assign(s,{kind:'outline',layer:null,rect:b,quad:cornersOf(b),label:o.label||'Transform Selection'});s.planes.push({id:'sel',src:clone(Ed.selection),region:b,mask:true,edge:0,doc:true});}
  else{const L=Ed.layers.active(),why=refuse(L,{pixels:true,verb:'transform'});if(why){say(why);return null;}
    const box=transformBox(L);
    if(!box){say(Ed.selection?'The selected area is empty. Select something on this layer to transform it.':`There is nothing on ${q(L)} to transform.`);return null;}
    Object.assign(s,{kind:box.kind,layer:L,target:d.target,rect:box.rect,quad:cornersOf(box.rect)});
    if(box.kind==='layer'){s.planes.push({id:'px',src:L.canvas,region:box.rect});s.oldArea=box.rect;
      if(linkedMask(L)){const m=maskScan(L);s.planes.push({id:'mask',src:L.mask,region:docRect(),mask:true,edge:m.edge,doc:true});}}
    else if(box.kind==='mask'){s.planes.push({id:'mask',src:L.mask,region:docRect(),mask:true,edge:box.edge,doc:true});}
    else if(d.target==='mask'){const sb=Ed.select.bounds(),cov=U.read(Ed.selection).data,base=U.read(L.mask).data,bg=bgValue();
      for(let i=3;i<base.length;i+=4){const c=cov[i]/255;if(c)base[i]=Math.round(base[i]*(1-c)+bg*c);}
      s.maskBase=base;s.planes.push({id:'mv',src:L.mask,region:area(U.grow(sb,2)),mask:true,edge:0,doc:true},{id:'sel',src:clone(Ed.selection),region:sb,mask:true,edge:0,doc:true});}
    else{const[inside,outside]=[box.inside,through(L.canvas,Ed.selection,'destination-out')];s.base=outside;s.lockAlpha=!!L.locks.alpha;
      s.oldArea=U.union(Ed.select.bounds(),box.rect);s.planes.push({id:'px',src:inside,region:box.rect},{id:'sel',src:clone(Ed.selection),region:Ed.select.bounds(),mask:true,edge:0,doc:true});}}
  if(o.quad)s.quad=o.quad.map(p=>p.slice());
  s.M=rectToQuad(s.rect,s.quad);if(!s.M){say('That transform is not possible.');return null;}
  s.M0=s.M.slice();
  for(const p of s.planes){p.data=U.read(p.src,p.region).data;p.key='tf'+(++jobSeq);}
  session=s;memo=null;
  if(o.apply){s.apply=true;return s;}
  // previews: what stays plus the quick GPU drawing; the worker's exact result replaces it moments later
  const L=s.layer;
  if(L){if(s.planes.some(p=>p.id==='px'))s.pc=U.canvas(W,H);if(s.planes.some(p=>p.id==='mask'||p.id==='mv'))s.mc=clone(L.mask);
    if(s.pc)Ed.preview.layer({layer:L,canvas:s.pc,mask:s.mc||undefined,target:'pixels'});else if(s.mc)Ed.preview.layer({layer:L,canvas:s.mc,target:'mask'});
    s.preview=Ed.preview.active;}
  for(const p of s.planes){const copy=p.data.slice();job({cmd:'source',key:p.key,src:copy,sw:p.region.w,sh:p.region.h},[copy.buffer]).catch(()=>{});}
  s.nudge=(dx,dy)=>{pushUndo(s);setQuad(s,s.quad.map(([x,y])=>[x+dx,y+dy]));};
  if(Ed.tool!=='transform'){s.switching=true;Ed.setTool('transform');s.switching=false;}
  setTimeout(()=>{if(session===s)Ed.preview.update();},0);
  changed(s,true);
  return s;}
const refPoint=s=>{const r=s.rect;return M3.apply(s.M,r.x+r.w*s.ref.u,r.y+r.h*s.ref.v);};
function setQuad(s,Q,keepRef=true){const M=rectToQuad(s.rect,Q);if(!M||!M.every(isFinite))return false;
  if(s.mode!=='distort'&&s.mode!=='perspective'&&!convex(Q))return false;if(!convex(Q))return false;s.quad=Q;s.M=M;changed(s);return true;}
function pushUndo(s){s.undo.push({quad:s.quad.map(p=>p.slice()),ref:{...s.ref}});if(s.undo.length>100)s.undo.shift();s.redo=[];}
// The document rectangle a plane's result covers.
function dstRect(s,p,M){if(p.doc)return docRect();const b=bboxOf(quadOf(M,s.rect),3);return area(b);}
function planeJob(s,p,M,interp){const dst=dstRect(s,p,M);if(!dst)return null;const Mp=M3.mul(M,M3.T(p.region.x,p.region.y));
  return{M:Mp,dx:dst.x,dy:dst.y,dw:dst.w,dh:dst.h,interp:p.id==='sel'&&interp==='bicubic'?'bilinear':interp,mask:!!p.mask,edge:p.edge||0,dst};}
async function compute(s,M,interp,sync){const outs={};
  for(const p of s.planes){const j=planeJob(s,p,M,interp);if(!j){outs[p.id]=null;continue;}
    let data=null;
    if(!sync&&!workerDead){try{data=await job({cmd:'transform',key:p.key,...j,dst:undefined});}catch(e){data=null;}}
    if(!data){await (sync?Promise.resolve():load('transform-resample.js').catch(()=>{}));
      if(!window.EdResample)throw Error('The transform helper is not loaded. Reload the page.');
      data=window.EdResample.transform({src:p.data,sw:p.region.w,sh:p.region.h,...j});}
    outs[p.id]={rect:j.dst,data};}
  return outs;}
function scratchOf(out){const c=U.canvas(out.rect.w,out.rect.h);U.write(c,imageData(out.data,out.rect.w,out.rect.h),{x:0,y:0,w:out.rect.w,h:out.rect.h});return c;}
// Mask values over the base, by the moved coverage (mask-target selections): whole document.
function mixMask(s,outs){const W=Ed.doc.width,H=Ed.doc.height,o=new Uint8ClampedArray(W*H*4),b=s.maskBase,v=outs.mv&&outs.mv.data,c=outs.sel&&outs.sel.data;
  for(let i=0;i<W*H*4;i+=4){const k=c?c[i+3]/255:0;o[i]=o[i+1]=o[i+2]=255;o[i+3]=k?b[i+3]*(1-k)+v[i+3]*k:b[i+3];}return imageData(o,W,H);}
// The quick preview: the GPU draws the source through the map (affine directly, perspective as small triangles).
function drawApprox(g,src,r,M,interp){g.save();plain(g);g.imageSmoothingEnabled=interp!=='nearest';g.imageSmoothingQuality=interp==='bicubic'?'high':'low';
  if(isAffine(M)){g.setTransform(M[0],M[3],M[1],M[4],M[2],M[5]);g.drawImage(src,r.x,r.y,r.w,r.h,r.x,r.y,r.w,r.h);}
  else{const n=Math.max(4,Math.min(24,Math.ceil(Math.max(r.w,r.h)/24)));
    for(let j=0;j<n;j++)for(let i=0;i<n;i++){const x0=r.x+r.w*i/n,x1=r.x+r.w*(i+1)/n,y0=r.y+r.h*j/n,y1=r.y+r.h*(j+1)/n;
      for(const tri of [[[x0,y0],[x1,y0],[x1,y1]],[[x0,y0],[x1,y1],[x0,y1]]]){const D=tri.map(([x,y])=>M3.apply(M,x,y));
        const A=affineFrom(tri,D);if(!A)continue;const cx=(D[0][0]+D[1][0]+D[2][0])/3,cy=(D[0][1]+D[1][1]+D[2][1])/3;
        g.save();g.beginPath();for(let k=0;k<3;k++){const[x,y]=D[k],dx=x-cx,dy=y-cy,l=Math.hypot(dx,dy)||1;g[k?'lineTo':'moveTo'](x+dx/l*.6,y+dy/l*.6);}g.closePath();g.clip();
        g.setTransform(A[0],A[3],A[1],A[4],A[2],A[5]);const bx=Math.floor(Math.min(x0,x1))-1,by=Math.floor(y0)-1;g.drawImage(src,Math.max(r.x,bx),Math.max(r.y,by),Math.min(r.x+r.w,Math.ceil(x1)+1)-Math.max(r.x,bx),Math.min(r.y+r.h,Math.ceil(y1)+1)-Math.max(r.y,by),Math.max(r.x,bx),Math.max(r.y,by),Math.min(r.x+r.w,Math.ceil(x1)+1)-Math.max(r.x,bx),Math.min(r.y+r.h,Math.ceil(y1)+1)-Math.max(r.y,by));g.restore();}}}
  g.restore();}
function affineFrom(S,D){const[[x0,y0],[x1,y1],[x2,y2]]=S,det=(x1-x0)*(y2-y0)-(x2-x0)*(y1-y0);if(Math.abs(det)<1e-12)return null;
  const inv=[(y2-y0)/det,-(x2-x0)/det,-(y1-y0)/det,(x1-x0)/det],ux=D[1][0]-D[0][0],uy=D[1][1]-D[0][1],vx=D[2][0]-D[0][0],vy=D[2][1]-D[0][1];
  const a=ux*inv[0]+vx*inv[2],b=ux*inv[1]+vx*inv[3],c=uy*inv[0]+vy*inv[2],d=uy*inv[1]+vy*inv[3];return[a,b,D[0][0]-a*x0-b*y0,c,d,D[0][1]-c*x0-d*y0,0,0,1];}
function fastPreview(s){if(!s.preview||Ed.preview.active!==s.preview)return;const W=Ed.doc.width,H=Ed.doc.height;let R=null;
  for(const p of s.planes){
    if(p.id==='px'){const dst=dstRect(s,p,s.M),Rp=U.union(s.lastPx,dst)||dst;s.lastPx=dst;if(!Rp)continue;const g=g2(s.pc);g.save();plain(g);g.beginPath();g.rect(Rp.x,Rp.y,Rp.w,Rp.h);g.clip();g.clearRect(Rp.x,Rp.y,Rp.w,Rp.h);
      if(s.base)g.drawImage(s.base,0,0);g.restore();drawApprox(g,p.src,p.region,s.M,s.interp);R=U.union(R,Rp);}
    else if(p.id==='mask'){const g=g2(s.mc);g.save();plain(g);g.clearRect(0,0,W,H);fillMaskCtx(g,p.edge,{x:0,y:0,w:W,h:H});
      const Q=quadOf(s.M,docRect());g.beginPath();Q.forEach(([x,y],k)=>g[k?'lineTo':'moveTo'](x,y));g.closePath();g.globalCompositeOperation='destination-out';g.fill();g.restore();
      drawApprox(g,p.src,p.region,s.M,s.interp);R=docRect();}}
  if(R)Ed.preview.update(R);}
function showOutputs(s,outs){if(!s.preview||Ed.preview.active!==s.preview)return;const W=Ed.doc.width,H=Ed.doc.height;let R=null;
  if(outs.px!==undefined&&s.pc){const out=outs.px,Rp=U.union(s.lastPx,out&&out.rect);s.lastPx=out&&out.rect;
    if(Rp){const g=g2(s.pc);g.save();plain(g);g.beginPath();g.rect(Rp.x,Rp.y,Rp.w,Rp.h);g.clip();g.clearRect(Rp.x,Rp.y,Rp.w,Rp.h);if(s.base)g.drawImage(s.base,0,0);
      if(out)g.drawImage(scratchOf(out),out.rect.x,out.rect.y);g.restore();R=U.union(R,Rp);}}
  if(outs.mask&&s.mc){U.write(s.mc,imageData(outs.mask.data,W,H));R=docRect();}
  if(outs.mv!==undefined&&s.mc&&s.maskBase){U.write(s.mc,mixMask(s,outs));R=docRect();}
  if(R)Ed.preview.update(R);}
function scheduleRefine(s,now){clearTimeout(s.timer);const px=s.planes.reduce((n,p)=>n+p.region.w*p.region.h,0);
  s.timer=setTimeout(()=>refine(s),now||px<300000?0:s.dragging?140:40);}
async function refine(s){if(session!==s||s.apply)return;const rev=s.rev,M=s.M.slice(),interp=s.interp;
  try{const outs=await compute(s,M,interp);if(session!==s||s.rev!==rev)return;s.cpu={rev,interp,outs};showOutputs(s,outs);}catch(e){}}
function changed(s,first){s.rev++;if(!s.apply){if(s.preview)fastPreview(s);scheduleRefine(s,first);syncOptions(s);Ed.view.redrawOverlay();}}
function busy(on,text){const b=byId('busy');if(!b)return;if(on){b.hidden=false;const t=byId('busyText');if(t)t.textContent=text||'';if(typeof S==='object')S.busy=true;}else{b.hidden=true;if(typeof S==='object')S.busy=false;}}
// Apply: the computed planes go into the layer (and the selection) as one history state.
async function commit(){const s=session;if(!s||s.busy)return false;s.busy=true;let outs=s.cpu&&s.cpu.rev===s.rev&&s.cpu.interp===s.interp?s.cpu.outs:null;
  if(!outs){const slow=setTimeout(()=>busy(true,'Applying the transform…'),200);
    try{outs=await compute(s,s.M,s.interp);}catch(e){s.busy=false;say(e.message,true);return false;}finally{clearTimeout(slow);busy(false);}}
  if(session!==s)return false;apply(s,outs);end(s);return true;}
function commitSync(){const s=session;if(!s||s.busy)return false;s.busy=true;let outs=s.cpu&&s.cpu.rev===s.rev&&s.cpu.interp===s.interp?s.cpu.outs:null;
  if(!outs){if(!window.EdResample){end(s,true);say('The transform could not be applied (its helper did not load).',true);return false;}
    outs={};for(const p of s.planes){const j=planeJob(s,p,s.M,s.interp);outs[p.id]=j&&{rect:j.dst,data:window.EdResample.transform({src:p.data,sw:p.region.w,sh:p.region.h,...j})};}}
  apply(s,outs);end(s);return true;}
function apply(s,outs){const L=s.layer,d=Ed.doc,W=d.width,H=d.height,M=s.M.slice();
  if(s.preview&&Ed.preview.active===s.preview)Ed.preview.clear(false);
  const identity=M.every((v,i)=>Math.abs(v-s.M0[i])<1e-9)&&s.kind!=='paste';
  if(identity){Ed.update();return;}
  const op={tool:'transform',mode:s.mode,interp:s.interp,m:M.map(v=>round(v,5))};
  if(s.kind==='outline'){const m=U.canvas(W,H);if(outs.sel)U.write(m,imageData(outs.sel.data,W,H));Ed.select.set(m,'replace',{label:s.label});return;}
  Ed.history.record(s.label,tx=>{
    if(outs.px!==undefined&&s.planes.some(p=>p.id==='px')){const out=outs.px,R=area(U.union(s.oldArea,out&&out.rect));
      if(R){const old=s.lockAlpha?U.read(L.canvas,R):null;tx.touch(L,'pixels',R);const g=g2(L.canvas);g.save();plain(g);g.beginPath();g.rect(R.x,R.y,R.w,R.h);g.clip();g.clearRect(R.x,R.y,R.w,R.h);
        if(s.base)g.drawImage(s.base,0,0);if(out)g.drawImage(scratchOf(out),out.rect.x,out.rect.y);g.restore();
        if(old){const now=U.read(L.canvas,R);for(let i=3;i<now.data.length;i+=4)now.data[i]=old.data[i];U.write(L.canvas,now,R);}}}
    if(outs.mask&&L.mask){tx.touch(L,'mask',docRect());U.write(L.mask,imageData(outs.mask.data,W,H));}
    if(s.maskBase&&L.mask){tx.touch(L,'mask',docRect());U.write(L.mask,mixMask(s,outs));}
    if(outs.sel){const m=U.canvas(W,H);U.write(m,imageData(outs.sel.data,W,H));quietSelect(m);}},{op});
  if(s.kind!=='paste')lastTransform={M,interp:s.interp,mode:s.mode};}
function end(s,cancelled){if(session!==s)return;clearTimeout(s.timer);s.ending=true;session=null;
  if(s.preview&&Ed.preview.active===s.preview)Ed.preview.clear();
  for(const p of s.planes)job({cmd:'drop',key:p.key}).catch(()=>{});
  if(Ed.tool==='transform'){const back=s.prevTool&&s.prevTool!=='transform'&&Ed.tools.get(s.prevTool)?s.prevTool:'move';Ed.setTool(back);}
  Ed.view.redrawOverlay();if(cancelled)Ed.update();}
function cancel(){const s=session;if(!s||s.busy)return;end(s,true);}
// Edit > Transform > Flip / Rotate: on the open transform's box, or at once on the layer (exact pixels: Nearest Neighbor
// with integer corners, so nothing is resampled).
function quick(kind){const label={h:'Flip Horizontal',v:'Flip Vertical','180':'Rotate 180°',cw:'Rotate 90° Clockwise',ccw:'Rotate 90° Counter Clockwise'}[kind];
  if(session&&!session.apply){const s=session,c=refPoint(s);pushUndo(s);
    const F=kind==='h'||kind==='v'?(()=>{const r=s.rect,cx=r.x+r.w*s.ref.u,cy=r.y+r.h*s.ref.v;return M3.mul(s.M,M3.mul(M3.T(cx,cy),M3.mul(M3.S(kind==='h'?-1:1,kind==='v'?-1:1),M3.T(-cx,-cy))));})():M3.mul(R3(kind==='180'?180:kind==='cw'?90:-90,c[0],c[1]),s.M);
    setQuad(s,quadOf(F,s.rect));return true;}
  const s=startTransform({apply:true,label,interp:'nearest'});if(!s)return false;const r=s.rect,cx=r.x+r.w/2,cy=r.y+r.h/2;let Q;
  if(kind==='h')Q=[[r.x+r.w,r.y],[r.x,r.y],[r.x,r.y+r.h],[r.x+r.w,r.y+r.h]];
  else if(kind==='v')Q=[[r.x,r.y+r.h],[r.x+r.w,r.y+r.h],[r.x+r.w,r.y],[r.x,r.y]];
  else if(kind==='180')Q=[[r.x+r.w,r.y+r.h],[r.x,r.y+r.h],[r.x,r.y],[r.x+r.w,r.y]];
  else{const x=Math.round(cx-r.h/2),y=Math.round(cy-r.w/2);Q=kind==='cw'?[[x+r.h,y],[x+r.h,y+r.w],[x,y+r.w],[x,y]]:[[x,y+r.w],[x,y],[x+r.h,y],[x+r.h,y+r.w]];}
  s.quad=Q;s.M=rectToQuad(s.rect,Q);s.rev++;
  commitSync();return true;}
// Edit > Transform > Again: the last transform's map, applied to the layer (or selection) as it is now.
function again(){if(!lastTransform){say('Transform something first; Again repeats it.');return false;}
  const s=startTransform({apply:true,label:'Transform Again',interp:lastTransform.interp});if(!s)return false;
  s.M=lastTransform.M.slice();s.quad=quadOf(s.M,s.rect);s.rev++;commitSync();return true;}

// ----- the box on screen: drawing, hit testing, cursors -----------------------------------------------------------------
const HANDLE=4;   // half size in screen px
const ROTATE_CURSOR=`url("data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 14.5a7 7 0 0 1 12-6.5M18.5 4.5v4h-4" stroke="#fff" stroke-width="4"/><path d="M5.5 14.5a7 7 0 0 1 12-6.5M18.5 4.5v4h-4" stroke="#000" stroke-width="1.6"/></g></svg>')}") 12 12, alias`;
function screenQuad(Q,view){return Q.map(([x,y])=>{const p=view.toScreen(x,y);return[p.x,p.y];});}
function sideMids(Q){return[0,1,2,3].map(i=>{const a=Q[i],b=Q[(i+1)%4];return[(a[0]+b[0])/2,(a[1]+b[1])/2];});}
function sideMidsM(M,r){return[[r.x+r.w/2,r.y],[r.x+r.w,r.y+r.h/2],[r.x+r.w/2,r.y+r.h],[r.x,r.y+r.h/2]].map(([x,y])=>M3.apply(M,x,y));}
function drawBox(ctx,view,Q,o={}){const S=screenQuad(Q,view),mids=o.M&&o.rect?screenQuad(sideMidsM(o.M,o.rect),view):sideMids(S);
  ctx.save();ctx.lineWidth=1;ctx.beginPath();S.forEach(([x,y],k)=>ctx[k?'lineTo':'moveTo'](Math.round(x)+.5,Math.round(y)+.5));ctx.closePath();
  ctx.strokeStyle='rgba(0,0,0,.75)';ctx.lineWidth=3;ctx.stroke();ctx.strokeStyle='#38c3ff';ctx.lineWidth=1;ctx.stroke();
  for(const[x,y] of [...S,...mids]){const X=Math.round(x)-HANDLE+.5,Y=Math.round(y)-HANDLE+.5;ctx.fillStyle='#fff';ctx.fillRect(X,Y,HANDLE*2,HANDLE*2);ctx.strokeStyle='#1b2733';ctx.strokeRect(X,Y,HANDLE*2,HANDLE*2);}
  if(o.ref){const p=view.toScreen(o.ref[0],o.ref[1]);ctx.beginPath();ctx.arc(p.x,p.y,4.5,0,Math.PI*2);ctx.fillStyle='rgba(255,255,255,.85)';ctx.fill();ctx.strokeStyle='#1b2733';ctx.stroke();
    ctx.beginPath();ctx.moveTo(p.x-8,p.y+.5);ctx.lineTo(p.x+8,p.y+.5);ctx.moveTo(p.x+.5,p.y-8);ctx.lineTo(p.x+.5,p.y+8);ctx.strokeStyle='rgba(27,39,51,.9)';ctx.stroke();}
  ctx.restore();return true;}
// The selection's outline mapped through M, dashed (shows where a moved or transformed selection goes).
function ghostOutline(ctx,view,M){const o=Ed.select.outline();if(!o||o.h.length+o.v.length>60000)return;ctx.save();ctx.beginPath();
  const seg=(x0,y0,x1,y1)=>{const a=M3.apply(M,x0,y0),b=M3.apply(M,x1,y1),A=view.toScreen(a[0],a[1]),B=view.toScreen(b[0],b[1]);ctx.moveTo(A.x,A.y);ctx.lineTo(B.x,B.y);};
  for(let i=0;i<o.h.length;i+=3)seg(o.h[i+1],o.h[i],o.h[i+2],o.h[i]);for(let i=0;i<o.v.length;i+=3)seg(o.v[i],o.v[i+1],o.v[i],o.v[i+2]);
  ctx.lineWidth=1;ctx.strokeStyle='rgba(255,255,255,.9)';ctx.setLineDash([3,3]);ctx.stroke();ctx.strokeStyle='rgba(0,0,0,.8)';ctx.lineDashOffset=3;ctx.stroke();ctx.restore();}
function inside(S,x,y){let c=false;for(let i=0,j=3;i<4;j=i++){const[xi,yi]=S[i],[xj,yj]=S[j];if((yi>y)!==(yj>y)&&x<(xj-xi)*(y-yi)/(yj-yi)+xi)c=!c;}return c;}
function segDist(px,py,[ax,ay],[bx,by]){const dx=bx-ax,dy=by-ay,t=Math.max(0,Math.min(1,((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy||1)));return Math.hypot(px-ax-t*dx,py-ay-t*dy);}
// What the pointer is over: {kind: 'ref'|'corner'|'side'|'inside'|'outside', index}
function hitTest(Q,s,cx,cy){const t=Ed.view.transform,view={toScreen:(x,y)=>({x:t.left+t.x+x*t.scale,y:t.top+t.y+y*t.scale})};
  const S=screenQuad(Q,view),mids=s?screenQuad(sideMidsM(s.M,s.rect),view):sideMids(S),near=(p,r)=>Math.hypot(p[0]-cx,p[1]-cy)<=r;
  if(s&&s.mode!=='rotate'){const r=refPoint(s),p=view.toScreen(r[0],r[1]);if(near([p.x,p.y],6))return{kind:'ref'};}
  for(let i=0;i<4;i++)if(near(S[i],HANDLE+3))return{kind:'corner',index:i};
  for(let i=0;i<4;i++)if(near(mids[i],HANDLE+3))return{kind:'side',index:i};
  for(let i=0;i<4;i++)if(segDist(cx,cy,S[i],S[(i+1)%4])<=3)return{kind:'side',index:i};
  return inside(S,cx,cy)?{kind:'inside'}:{kind:'outside'};}
const RESIZE=['ew-resize','nwse-resize','ns-resize','nesw-resize'];
function handleCursor(hit,Q,p,s){if(hit.kind==='inside')return 'move';if(hit.kind==='ref')return 'crosshair';if(hit.kind==='outside')return s&&s.mode!=='free'&&s.mode!=='rotate'?'default':ROTATE_CURSOR;
  if(p&&p.ctrlKey||s&&(s.mode==='distort'||s.mode==='perspective'||s.mode==='skew'))return 'default';
  const c=[(Q[0][0]+Q[2][0])/2,(Q[0][1]+Q[2][1])/2],h=hit.kind==='corner'?Q[hit.index]:sideMids(Q)[hit.index];
  let a=Math.atan2(h[1]-c[1],h[0]-c[0])*180/Math.PI;a=((a%180)+180)%180;return RESIZE[Math.round(a/45)%4];}

// ----- dragging the box ------------------------------------------------------------------------------------------------
let tdrag=null;
function beginDrag(s,hit,p){const r=s.rect;tdrag={s,hit,start:[p.x,p.y],last:p,Q0:s.quad.map(q=>q.slice()),M0:s.M.slice(),ref0:refPoint(s),refUV:{...s.ref},
    angle0:decompose(s.M,s.rect).angle,undo:{quad:s.quad.map(q=>q.slice()),ref:{...s.ref}},changed:false};s.dragging=true;
  const up=e=>{window.removeEventListener('pointerup',up,true);window.removeEventListener('pointercancel',up,true);window.removeEventListener('keydown',mods,true);window.removeEventListener('keyup',mods,true);finishDrag();};
  const mods=e=>{if(tdrag&&['Shift','Alt','Control','Meta'].includes(e.key)){const l=tdrag.last;dragTo({...l,shiftKey:e.shiftKey,altKey:e.altKey,ctrlKey:e.ctrlKey||e.metaKey});}};
  window.addEventListener('pointerup',up,true);window.addEventListener('pointercancel',up,true);window.addEventListener('keydown',mods,true);window.addEventListener('keyup',mods,true);}
function finishDrag(){const D=tdrag;tdrag=null;if(!D)return;D.s.dragging=false;if(D.changed&&session===D.s){D.s.undo.push(D.undo);D.s.redo=[];if(D.s.undo.length>100)D.s.undo.shift();scheduleRefine(D.s,true);}}
function toSource(M,x,y){const I=M3.inv(M);return I?M3.apply(I,x,y):[x,y];}
function dragTo(p){const D=tdrag;if(!D||session!==D.s)return;D.last=p;const s=D.s,Q0=D.Q0,r=s.rect,hit=D.hit,mode=s.mode;
  const shift=!!p.shiftKey,alt=!!p.altKey,ctrl=!!p.ctrlKey,dx=p.x-D.start[0],dy=p.y-D.start[1];let Q=null;
  if(hit.kind==='ref'){const[u,v]=toSource(D.M0,p.x,p.y);s.ref={u:(u-r.x)/r.w,v:(v-r.y)/r.h};D.changed=true;syncOptions(s);Ed.view.redrawOverlay();return;}
  if(hit.kind==='inside'&&mode!=='rotate'){let mx=dx,my=dy;if(shift){const a=Math.atan2(my,mx),k=Math.round(a/(Math.PI/4))*Math.PI/4,l=Math.hypot(mx,my)*Math.cos(a-k);mx=l*Math.cos(k);my=l*Math.sin(k);}
    Q=Q0.map(([x,y])=>[x+mx,y+my]);}
  else if(hit.kind==='outside'||mode==='rotate'){if(mode!=='free'&&mode!=='rotate')return;const c=D.ref0;
    let a=(Math.atan2(p.y-c[1],p.x-c[0])-Math.atan2(D.start[1]-c[1],D.start[0]-c[0]))*180/Math.PI;
    if(shift){const total=Math.round((D.angle0+a)/15)*15;a=total-D.angle0;}
    Q=Q0.map(([x,y])=>M3.apply(R3(a,c[0],c[1]),x,y));}
  else{const corner=hit.kind==='corner',i=hit.index;
    const persp=corner&&(mode==='perspective'||(mode==='free'&&ctrl&&alt&&shift));
    const skew=!persp&&(mode==='skew'||(mode==='free'&&ctrl&&shift));
    const distort=!persp&&!skew&&(mode==='distort'||(mode==='free'&&ctrl));
    if(persp||(skew&&corner)){ // slide the corner along its dominant edge; perspective mirrors the edge's other corner
      const a=Q0[i],nb=[Q0[(i+1)%4],Q0[(i+3)%4]],dirs=nb.map(b=>{const vx=b[0]-a[0],vy=b[1]-a[1],l=Math.hypot(vx,vy)||1;return[vx/l,vy/l];});
      const t=dirs.map(([ux,uy])=>dx*ux+dy*uy),k=Math.abs(t[0])>=Math.abs(t[1])?0:1,[ux,uy]=dirs[k],m=t[k];
      Q=Q0.map(c=>c.slice());Q[i]=[a[0]+ux*m,a[1]+uy*m];if(persp){const j=k===0?(i+1)%4:(i+3)%4;Q[j]=[Q0[j][0]-ux*m,Q0[j][1]-uy*m];}}
    else if(skew){const a=Q0[i],b=Q0[(i+1)%4],vx=b[0]-a[0],vy=b[1]-a[1],l=Math.hypot(vx,vy)||1,m=(dx*vx+dy*vy)/l,ox=vx/l*m,oy=vy/l*m;
      Q=Q0.map(c=>c.slice());for(const j of [i,(i+1)%4])Q[j]=[Q0[j][0]+ox,Q0[j][1]+oy];if(alt)for(const j of [(i+2)%4,(i+3)%4])Q[j]=[Q0[j][0]-ox,Q0[j][1]-oy];}
    else if(distort){Q=Q0.map(c=>c.slice());const idx=corner?[i]:[i,(i+1)%4],opp=corner?[(i+2)%4]:[(i+2)%4,(i+3)%4];
      for(const j of idx)Q[j]=[Q0[j][0]+dx,Q0[j][1]+dy];if(alt)for(const j of opp)Q[j]=[Q0[j][0]-dx,Q0[j][1]-dy];}
    else{ // scale in the box's own axes (source space), about the opposite handle or (Alt) the reference point
      if(mode==='rotate')return;
      const link=Ed.toolOptions('transform').link!==false,prop=corner?(link!==shift):shift;
      const C=corner?cornersOf(r)[i]:[[r.x+r.w/2,r.y],[r.x+r.w,r.y+r.h/2],[r.x+r.w/2,r.y+r.h],[r.x,r.y+r.h/2]][i];
      const A=alt?[r.x+r.w*D.refUV.u,r.y+r.h*D.refUV.v]:corner?cornersOf(r)[(i+2)%4]:[[r.x+r.w/2,r.y+r.h],[r.x,r.y+r.h/2],[r.x+r.w/2,r.y],[r.x+r.w,r.y+r.h/2]][i];
      const P=toSource(D.M0,p.x,p.y),S0=toSource(D.M0,D.start[0],D.start[1]),Px=P[0]-S0[0]+C[0],Py=P[1]-S0[1]+C[1];
      const ex=C[0]-A[0],ey=C[1]-A[1];let kx=1,ky=1;
      if(corner){kx=ex?(Px-A[0])/ex:1;ky=ey?(Py-A[1])/ey:1;if(prop){const k=(ex*ex+ey*ey)?((Px-A[0])*ex+(Py-A[1])*ey)/(ex*ex+ey*ey):1;kx=ky=k;}}
      else if(i===1||i===3){kx=ex?(Px-A[0])/ex:1;if(prop)ky=kx;}else{ky=ey?(Py-A[1])/ey:1;if(prop)kx=ky;}
      if(!isFinite(kx)||!isFinite(ky)||Math.abs(kx)<1e-4||Math.abs(ky)<1e-4)return;
      const Sm=M3.mul(M3.T(A[0],A[1]),M3.mul(M3.S(kx,ky),M3.T(-A[0],-A[1])));Q=quadOf(M3.mul(D.M0,Sm),r);}}
  if(Q&&setQuad(s,Q))D.changed=true;}

// ----- the Free Transform "tool": the box, its handles and the options bar while a transform is open -------------------
const TF_ICON=['M6 6h12v12H6z','M3.5 3.5h4v4h-4zM16.5 3.5h4v4h-4zM3.5 16.5h4v4h-4zM16.5 16.5h4v4h-4z'];
let synced={},syncing=false,lastDown=null;
function syncOptions(s){if(Ed.tool!=='transform'||!s)return;const v=decompose(s.M,s.rect),r=refPoint(s);
  const vals={x:round(r[0]),y:round(r[1]),w:round(v.w),h:round(v.h),angle:round(v.angle),interp:s.interp};
  syncing=true;try{Ed.setToolOptions('transform',vals);}finally{syncing=false;}synced=vals;refField.sync&&refField.sync(s.ref);}
function fromOptions(s,vals){if(!s||s.apply)return;const r=s.rect;let changedAny=false;const was=synced;
  if(vals.interp&&vals.interp!==s.interp){s.interp=vals.interp;s.cpu=null;changed(s);changedAny=true;}
  const num=k=>vals[k]!==undefined&&isFinite(vals[k])&&Math.abs(vals[k]-(was[k]??vals[k]))>1e-9;
  if(!['x','y','w','h','angle'].some(num))return changedAny;
  pushUndo(s);const ref=refPoint(s),ru=[r.x+r.w*s.ref.u,r.y+r.h*s.ref.v];let M=s.M;
  if(num('x')||num('y'))M=M3.mul(M3.T((num('x')?vals.x:ref[0])-ref[0],(num('y')?vals.y:ref[1])-ref[1]),M);
  const cur=decompose(M,r),link=vals.link!==false;
  if(num('w')||num('h')){let kx=num('w')&&cur.w?vals.w/cur.w:1,ky=num('h')&&cur.h?vals.h/cur.h:1;if(link){if(num('w')&&!num('h'))ky=kx;else if(num('h')&&!num('w'))kx=ky;}
    if(isFinite(kx)&&isFinite(ky)&&kx&&ky)M=M3.mul(M,M3.mul(M3.T(ru[0],ru[1]),M3.mul(M3.S(kx,ky),M3.T(-ru[0],-ru[1]))));}
  if(num('angle')){const c=M3.apply(M,ru[0],ru[1]);M=M3.mul(R3(vals.angle-decompose(M,r).angle,c[0],c[1]),M);}
  if(!setQuad(s,quadOf(M,r)))syncOptions(s);return true;}
const refField={sync:null};
Ed.registerField('ed-tf-ref',(f,value,set)=>{const g=el('div','ed-tf-ref');g.setAttribute('role','radiogroup');g.setAttribute('aria-label','Reference point');g.title='Reference point: what the box turns and scales around (Alt-drag a handle scales from it)';
  const cells=[];for(let j=0;j<3;j++)for(let i=0;i<3;i++){const b=button('ed-tf-refcell','');b.setAttribute('role','radio');b.setAttribute('aria-label',['Top','Middle','Bottom'][j]+' '+['left','center','right'][i]);
    b.onclick=()=>{const s=session;if(!s)return;pushUndo(s);s.ref={u:i/2,v:j/2};syncOptions(s);Ed.view.redrawOverlay();};cells.push(b);g.append(b);}
  refField.sync=ref=>{cells.forEach((b,k)=>{const on=!!ref&&Math.abs(ref.u-(k%3)/2)<1e-6&&Math.abs(ref.v-Math.floor(k/3)/2)<1e-6;b.classList.toggle('on',on);b.setAttribute('aria-checked',String(on));});};
  refField.sync(session&&session.ref);return g;});
Ed.registerField('ed-tf-link',(f,value,set)=>{const b=button('ed-tf-link','', 'Keep proportions: corner handles and W/H keep the shape (Shift does the opposite while dragging)');b.setAttribute('aria-label','Keep proportions');
  b.append(Ed.icon(['M9.5 14.5l5-5','M8 11l-2 2a3 3 0 0 0 4.2 4.2l2-2','M16 13l2-2a3 3 0 0 0-4.2-4.2l-2 2'],{size:15}));let on=value!==false;
  const show=()=>{b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on));};show();b.onclick=()=>{on=!on;show();set(on);};return b;});
// Cancel and Apply as icon buttons (Photoshop's), so the options bar stays one line at 1366 px.
Ed.registerField('ed-tf-act',f=>{const b=button('ed-tf-act ed-tf-'+f.key,'',f.title);b.setAttribute('aria-label',f.text);b.dataset.act=f.key;
  b.append(Ed.icon(f.key==='commit'?['M5 12.5l4.5 4.5L19 7.5']:['M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 1 0 0-17z','M6 18L18 6'],{size:16}));
  b.onclick=()=>{if(f.key==='commit')commit();else cancel();};return b;});
const ALLOWED=['View/','Edit/Transform/'];
Ed.registerTool('transform',{label:'Free Transform',group:'transform',cursor:'default',edits:false,icon:()=>Ed.icon(TF_ICON),
  options:[{key:'ref',type:'ed-tf-ref',default:'c'},
    {key:'x',label:'X',type:'number',step:1,unit:'px',default:0,title:'Where the reference point is, in pixels from the left'},
    {key:'y',label:'Y',type:'number',step:1,unit:'px',default:0,title:'Where the reference point is, in pixels from the top'},
    {key:'w',label:'W',type:'number',step:1,unit:'%',default:100,title:'Width in percent of the original'},
    {key:'link',type:'ed-tf-link',default:true},
    {key:'h',label:'H',type:'number',step:1,unit:'%',default:100,title:'Height in percent of the original'},
    {key:'angle',label:'Angle',type:'number',min:-180,max:180,step:.1,unit:'°',default:0,title:'Rotation in degrees (clockwise); Shift-drag outside the box turns in 15° steps'},
    {key:'interp',label:'Interpolation',type:'select',options:[{value:'nearest',label:'Nearest Neighbor'},{value:'bilinear',label:'Bilinear'},{value:'bicubic',label:'Bicubic'}],default:'bicubic',
      title:'How new pixels are made: Nearest Neighbor keeps pixel art crisp, Bicubic is smoothest for pictures'},
    {key:'cancel',text:'Cancel',type:'ed-tf-act',title:'Cancel the transform (Esc)'},
    {key:'commit',text:'Apply',type:'ed-tf-act',title:'Apply the transform (Enter)'}],
  onActivate(){if(!session)setTimeout(()=>{if(!session&&Ed.tool==='transform')Ed.setTool('move');},0);else syncOptions(session);},
  onDeactivate(){const s=session;if(s&&!s.ending&&!s.switching&&!s.busy)commitSync();},
  onDown(p){const s=session;if(!s||s.busy)return false;const hit=hitTest(s.quad,s,p.clientX,p.clientY),now=performance.now();
    if(hit.kind==='inside'&&lastDown&&now-lastDown.t<450&&Math.hypot(p.clientX-lastDown.x,p.clientY-lastDown.y)<5){lastDown=null;commit();return false;}
    lastDown={t:now,x:p.clientX,y:p.clientY};beginDrag(s,hit,p);},
  onMove(p){if(tdrag)dragTo(p);},
  onUp(){finishDrag();},
  onHover(p){const s=session;if(!s){this.cursor='default';return;}this.cursor=handleCursor(hitTest(s.quad,s,p.clientX,p.clientY),s.quad,p,s);},
  onKey(e){const s=session;if(!s)return false;if(s.busy)return true;
    if(/^Arrow/.test(e.key)&&!e.ctrlKey&&!e.metaKey&&!e.defaultPrevented){const n=e.shiftKey?10:1;s.nudge(e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0,e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0);return true;}
    if((e.ctrlKey||e.metaKey)&&!e.altKey&&e.key.toLowerCase()==='z'){const st=e.shiftKey?s.redo:s.undo,other=e.shiftKey?s.undo:s.redo,last=st.pop();
      if(last){other.push({quad:s.quad.map(p=>p.slice()),ref:{...s.ref}});s.ref=last.ref;setQuad(s,last.quad);}return true;}
    if(e.ctrlKey||e.metaKey){const path=Ed.menu.forEvent(e);if(path&&!ALLOWED.some(a=>path.startsWith(a))){say('Press Enter to apply the transform or Esc to cancel it first.');return true;}}
    return false;},
  onCommit(){commit();},onCancel(){if(tdrag){tdrag=null;}cancel();},
  onOptions(v){if(syncing||!session)return;fromOptions(session,v);},
  drawOverlay(ctx,view){const s=session;if(!s)return;if(s.planes.some(p=>p.id==='sel'))ghostOutline(ctx,view,s.M);drawBox(ctx,view,s.quad,{ref:s.mode!=='rotate'?refPoint(s):null,M:s.M,rect:s.rect});}});
// A transform stops when its layer changes underneath (another command), or another image opens.
Ed.on('history',()=>{const s=session;if(!s||s.busy||s.apply)return;if(s.layer&&!Ed.layers.list().includes(s.layer)){end(s,true);return;}});
Ed.on('open',()=>{if(session)end(session,true);memo=null;});

// =========================================================================================================================
// IMAGE > IMAGE ROTATION (every layer, mask and the selection; exact)
// =========================================================================================================================
function permute(c,how){const W=c.width,H=c.height,src=new Uint32Array(U.read(c).data.buffer),out=new ImageData(W,H),o=new Uint32Array(out.data.buffer);
  for(let y=0;y<H;y++){const r=y*W;for(let x=0;x<W;x++){const v=src[r+x];
    switch(how){case 'h':o[r+W-1-x]=v;break;case 'v':o[(H-1-y)*W+x]=v;break;case '180':o[(H-1-y)*W+W-1-x]=v;break;case 'cw':o[x*W+H-1-y]=v;break;default:o[(W-1-x)*W+y]=v;}}}
  U.write(c,out);}
function canvasRotation(how){const d=Ed.doc;if(!d){say('Open an image first.');return false;}const W=d.width,H=d.height;
  if((how==='cw'||how==='ccw')&&W!==H){say('A quarter turn would change the size of this image, and the game needs it as it is. Use 180°, a flip, or Edit > Transform on a layer.');return false;}
  if(session)commitSync();memo=null;
  const label=how==='h'?'Flip Canvas Horizontal':how==='v'?'Flip Canvas Vertical':'Rotate Canvas';
  Ed.history.record(label,tx=>{for(const L of Ed.layers.list()){tx.touch(L,'pixels',docRect());permute(L.canvas,how);if(L.mask){tx.touch(L,'mask',docRect());permute(L.mask,how);}}
    if(Ed.selection){const m=clone(Ed.selection);permute(m,how);quietSelect(m);}},{op:{tool:'image-rotation',how}});
  return true;}

// =========================================================================================================================
// CLIPBOARD: an exact internal copy, plus PNG on the system clipboard both ways
// =========================================================================================================================
let clip=null,pasteWanted=null,pasteTimer=0,fresh=true;
window.addEventListener('blur',()=>{fresh=false;});
async function encodePNG(c){return await new Promise((ok,fail)=>c.toBlob(b=>b?ok(b):fail(Error('The picture could not be encoded.')),'image/png'));}
function writeSystem(c){c.system='pending';
  try{if(!navigator.clipboard||!navigator.clipboard.write||typeof ClipboardItem!=='function'){c.system='failed';return;}
    navigator.clipboard.write([new ClipboardItem({'image/png':encodePNG(c.canvas)})]).then(()=>{c.system='ok';},()=>{c.system='failed';});}
  catch(e){c.system='failed';}}
// Copy (merged: what all visible layers show). Returns the clip or null.
function copy(o={}){const d=Ed.doc;if(!d){say('Open an image first.');return null;}if(session)commitSync();
  const L=Ed.layers.active(),W=d.width,H=d.height,sel=Ed.selection;let src;
  if(o.merged)src=Ed.composite();
  else if(d.target==='mask'&&L.mask){const v=U.read(L.mask),k=v.data;for(let i=0;i<k.length;i+=4){k[i]=k[i+1]=k[i+2]=k[i+3];k[i+3]=255;}src=U.canvas(W,H);U.write(src,v);}
  else if(L.kind!=='pixel'){say(`${q(L)} is an adjustment layer and has no pixels to copy. Use Copy Merged to copy what you see.`);return null;}
  else src=L.canvas;
  const work=sel?through(src,sel,'destination-in'):clone(src),b=scan(work,null).bounds;
  if(!b){say(sel?'The selected area is empty: there are no pixels to copy there.':'There are no pixels to copy on this layer.');return null;}
  const c=U.canvas(b.w,b.h);g2(c).drawImage(work,b.x,b.y,b.w,b.h,0,0,b.w,b.h);
  clip={canvas:c,x:b.x,y:b.y,w:b.w,h:b.h,docW:W,docH:H,doc:d,merged:!!o.merged};fresh=true;writeSystem(clip);return clip;}
async function cut(){const c=copy();if(!c)return false;
  return Ed.applyToTarget('Cut Pixels',(g,i)=>{const r=i.rect;g.save();plain(g);if(i.target==='pixels'&&i.layer.locks.alpha){g.globalCompositeOperation='source-atop';g.fillStyle=Ed.bg;g.fillRect(r.x,r.y,r.w,r.h);}
    else if(i.target==='mask'){g.clearRect(r.x,r.y,r.w,r.h);const v=bgValue();if(v){g.fillStyle=`rgba(255,255,255,${v/255})`;g.fillRect(r.x,r.y,r.w,r.h);}}else g.clearRect(r.x,r.y,r.w,r.h);g.restore();},
    {rect:Ed.selection?undefined:docRect(),op:{tool:'cut'}});}
async function decodeImage(blob){const bmp=await createImageBitmap(blob,{premultiplyAlpha:'default',colorSpaceConversion:'default'});const c=U.canvas(bmp.width,bmp.height);g2(c).drawImage(bmp,0,0);bmp.close&&bmp.close();return c;}
// The same picture as our clip? (A trip through the system clipboard re-encodes it, so allow a level or two.)
function sameAsClip(c){if(!clip||c.width!==clip.w||c.height!==clip.h)return false;const a=U.read(c).data,b=U.read(clip.canvas).data;
  for(let i=0;i<a.length;i+=4){const aa=a[i+3],ab=b[i+3];if(Math.abs(aa-ab)>2)return false;if(aa+ab===0)continue;for(let k=0;k<3;k++)if(Math.abs(a[i+k]*aa-b[i+k]*ab)/255>3)return false;}return true;}
// Where a pasted picture goes: in place (same document or Paste in Place), centered on the selection, or centered.
function placeFor(w,h,mode,fromClip){const d=Ed.doc,W=d.width,H=d.height,sel=Ed.select.bounds();
  if(fromClip&&(mode==='in-place'||(clip.docW===W&&clip.docH===H&&(!sel||mode!=='into'&&mode!=='outside'&&contains(sel,clip)))))return{x:clip.x,y:clip.y};
  const box=sel||{x:0,y:0,w:W,h:H};return{x:Math.round(box.x+(box.w-w)/2),y:Math.round(box.y+(box.h-h)/2)};}
const contains=(a,b)=>b.x>=a.x&&b.y>=a.y&&b.x+b.w<=a.x+a.w&&b.y+b.h<=a.y+a.h;
// Add a picture as a new layer. A picture bigger than the canvas is fitted inside it and opens in Free Transform, so the
// whole picture can still be scaled (a layer cannot hold pixels outside the canvas).
function placeImage(c,mode,o={}){const d=Ed.doc;if(!d){say('Open an image first.');return null;}if(session)commitSync();
  const W=d.width,H=d.height,sel=Ed.selection;if((mode==='into'||mode==='outside')&&!sel){say('Select an area first: Paste Into puts the picture inside the selection.');return null;}
  const label=o.label||(mode==='into'?'Paste Into':mode==='outside'?'Paste Outside':'Paste'),op={tool:o.tool||'paste',mode,size:[c.width,c.height]};
  let mask=null;if(sel){if(mode==='into')mask=clone(sel);else if(mode==='outside'){mask=U.canvas(W,H);const g=g2(mask);g.fillStyle='#fff';g.fillRect(0,0,W,H);g.globalCompositeOperation='destination-out';g.drawImage(sel,0,0);}}
  const extra=mask?{mask}:{};
  if(c.width<=W&&c.height<=H){const at=placeFor(c.width,c.height,mode,!!o.fromClip);
    const L=Ed.layers.add({name:o.name,canvas:c,x:at.x,y:at.y,history:label,op,...extra});return L;}
  const k=Math.min(W/c.width,H/c.height),w=c.width*k,h=c.height*k,box=mask?Ed.select.bounds():{x:0,y:0,w:W,h:H},x=box.x+(box.w-w)/2,y=box.y+(box.h-h)/2;
  const quad=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]],M=rectToQuad({x:0,y:0,w:c.width,h:c.height},quad),fit=U.canvas(W,H);
  const go=()=>{const data=U.read(c).data,out=window.EdResample.transform({src:data,sw:c.width,sh:c.height,M,dx:0,dy:0,dw:W,dh:H,interp:'bicubic'});U.write(fit,imageData(out,W,H));
    const L=Ed.layers.add({name:o.name,canvas:fit,history:label,op,...extra});
    if(L&&!o.noTransform){startTransform({paste:{layer:L,canvas:c,rect:{x:0,y:0,w:c.width,h:c.height},quad},label:'Free Transform'});say('The picture is bigger than this image, so it was fitted inside. Drag the handles to size it, then press Enter.');}
    return L;};
  if(window.EdResample)return go();
  return load('transform-resample.js').then(go,e=>{say(e.message,true);return null;});}
async function pasteBlob(blob,mode){let c;try{c=await decodeImage(blob);}catch(e){say('That picture could not be read.',true);return null;}
  if(sameAsClip(c))return placeImage(clip.canvas,mode,{fromClip:true});return placeImage(c,mode);}
function pasteInternal(mode){if(!clip){say('Copy something first (Ctrl+C), or copy a picture in another app and press Ctrl+V.');return null;}return placeImage(clip.canvas,mode,{fromClip:true});}
// Paste from the menu: our own clipboard (reading the system clipboard from a click would ask for a permission; Ctrl+V
// brings what other apps copied through the browser's paste event below).
function pasteCommand(mode){return pasteInternal(mode);}
// Keyboard paste: let the browser fire its paste event (it carries what other apps copied) instead of the menu shortcut.
// It belongs to the editor when the player last worked in the workshop or the inspector; in the asset library a pasted
// picture is a search (lab_extras.js).
let inEditor=true;
document.addEventListener('pointerdown',e=>{const t=e.target;inEditor=!(t&&t.closest&&t.closest('.library,#themeDrawer'));},true);
const takesPaste=e=>{const t=e.target;if(!Ed.doc||!inEditor||byId('view')?.hidden||document.querySelector('dialog[open]'))return false;if(typeof S==='object'&&S.busy)return false;
  if(t&&t.closest&&t.closest('input,textarea,select,[contenteditable=""],[contenteditable="true"],.library,#themeDrawer'))return false;return true;};
window.addEventListener('keydown',e=>{if(!(e.ctrlKey||e.metaKey)||(e.key||'').toLowerCase()!=='v')return;
  if(Ed.doc&&!inEditor){e.stopImmediatePropagation();return;}   // the library's paste (a search), not the editor's
  if(!takesPaste(e))return;
  const mode=e.altKey&&e.shiftKey?'into':e.shiftKey?'in-place':e.altKey?null:'paste';if(!mode)return;
  e.stopImmediatePropagation();pasteWanted={mode,t:performance.now()};clearTimeout(pasteTimer);
  pasteTimer=setTimeout(()=>{const w=pasteWanted;pasteWanted=null;if(w)pasteCommand(w.mode);},200);},true);
window.addEventListener('paste',e=>{if(!takesPaste(e))return;const w=pasteWanted;pasteWanted=null;clearTimeout(pasteTimer);
  const items=[...(e.clipboardData&&e.clipboardData.items||[])],img=items.find(i=>i.kind==='file'&&i.type.startsWith('image/'));
  if(!img&&!w)return;   // nothing for the editor: the page may use it
  e.preventDefault();e.stopImmediatePropagation();const mode=w?w.mode:'paste';
  // the system's picture, unless our own copy is newer (it could not reach the system clipboard and the window never lost focus)
  const f=img&&img.getAsFile();
  if(f&&(!clip||clip.system==='ok'||!fresh)){pasteBlob(f,mode);return;}
  pasteInternal(mode);},true);
// Image files dropped on the picture become layers (a PSD adds its layers).
const viewport=byId('viewport');
if(viewport){const files=e=>[...(e.dataTransfer&&e.dataTransfer.types||[])].includes('Files');
  viewport.addEventListener('dragover',e=>{if(!files(e))return;e.preventDefault();e.dataTransfer.dropEffect=Ed.doc?'copy':'none';viewport.classList.toggle('ed-tf-drop',!!Ed.doc);});
  viewport.addEventListener('dragleave',e=>{if(!viewport.contains(e.relatedTarget))viewport.classList.remove('ed-tf-drop');});
  viewport.addEventListener('drop',e=>{if(!files(e))return;e.preventDefault();viewport.classList.remove('ed-tf-drop');if(!Ed.doc){say('Open an asset first, then drop pictures on it to add them as layers.');return;}
    placeFiles([...e.dataTransfer.files]);});}
async function placeFiles(list){const pics=list.filter(f=>/\.psd$/i.test(f.name)||f.type==='image/vnd.adobe.photoshop'||f.type.startsWith('image/'));
  if(!pics.length){say('Drop PNG, JPEG, WebP, GIF or PSD files to add them as layers.');return;}
  for(const f of pics){if(/\.psd$/i.test(f.name)||f.type==='image/vnd.adobe.photoshop'){await importPSD(f,{how:'add'});continue;}
    let c;try{c=await decodeImage(f);}catch(e){say(`${f.name} could not be read as a picture.`,true);continue;}
    await placeImage(c,'paste',{name:f.name.replace(/\.[^.]+$/,'').slice(0,80)||'Placed image',label:'Place',tool:'place',noTransform:pics.length>1});}}

// =========================================================================================================================
// PSD: File > Export Layered PSD, File > Import PSD as Layers
// =========================================================================================================================
function download(bytes,name,type){const url=URL.createObjectURL(new Blob([bytes],{type}));const a=el('a');a.href=url;a.download=name;a.hidden=true;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
const baseName=()=>String((typeof S==='object'&&S.asset&&S.asset.name)||Ed.doc&&Ed.doc.name||'image').replace(/\.[^.]+$/,'').replace(/[^a-zA-Z0-9._ -]/g,'_')||'image';
async function exportPSD(o={}){const d=Ed.doc;if(!d){say('Open an image first.');return null;}if(session)commitSync();
  await load('transform-psd.js');const W=d.width,H=d.height,layers=[];let active=0;
  const skipped=Ed.layers.list().filter(L=>L.kind!=='pixel'),styled=Ed.layers.list().filter(L=>L.kind==='pixel'&&Object.values(L.effects||{}).some(p=>p&&p.enabled!==false));
  for(const L of Ed.layers.list()){if(L.kind!=='pixel')continue;if(L===Ed.layers.active())active=layers.length;
    const m=L.mask?{data:alphaOf(U.read(L.mask).data),default:maskScan(L).edge>=128?255:0,enabled:L.maskEnabled}:null;
    layers.push({name:L.name,visible:L.visible,opacity:L.opacity,fill:L.fill,blend:L.blend,clip:L.clip,locks:L.locks,rgba:U.read(L.canvas).data,mask:m});}
  if(!layers.length){say('There are no pixel layers to export.');return null;}
  const bytes=window.EdPSD.write({width:W,height:H,layers,composite:U.read(Ed.composite()).data,active});
  const name=(o.name||baseName())+'.psd';if(o.download!==false)download(bytes,name,'image/vnd.adobe.photoshop');
  const lossy=skipped.length||styled.length||(bytes.notes||[]).length;
  say(lossy?`Exported ${name} (layers: ${layers.length}). Adjustment layers, layer styles and blend modes Photoshop does not have are left out; the flattened picture inside the file still shows them.`
    :`Exported ${name} (layers: ${layers.length}).`);
  return bytes;}
function alphaOf(rgba){const a=new Uint8Array(rgba.length/4);for(let i=0;i<a.length;i++)a[i]=rgba[i*4+3];return a;}
const psdInput=el('input');psdInput.type='file';psdInput.accept='.psd,image/vnd.adobe.photoshop';psdInput.hidden=true;psdInput.id='edPsdFile';
(document.body||document.documentElement).append(psdInput);
psdInput.addEventListener('change',()=>{const f=psdInput.files&&psdInput.files[0];psdInput.value='';if(f)importPSD(f);});
// o.how: 'replace' (the PSD's layers replace the current ones) or 'add' (on top); asked when not given.
async function importPSD(file,o={}){const d=Ed.doc;if(!d){say('Open an asset first.');return false;}if(session)commitSync();
  let psd;try{await load('transform-psd.js');await load('transform-resample.js');psd=await window.EdPSD.read(await file.arrayBuffer());}catch(e){say(e.message,true);return false;}
  if(Ed.doc!==d)return false;
  const W=d.width,H=d.height,keep=[];let simplified=psd.warnings.length>0;
  // groups: flatten them; a hidden group hides its layers and its opacity multiplies theirs
  const stack=[[]];for(const L of psd.layers){if(L.kind==='group-end'){stack.push([]);continue;}
    if(L.kind==='group'){const members=stack.length>1?stack.pop():[];for(const m of members){if(!L.visible)m.visible=false;m.opacity*=L.opacity;}stack[stack.length-1].push(...members);simplified=true;continue;}
    if(L.kind!=='pixel'||L.broken){simplified=true;continue;}
    if(L.notes.length)simplified=true;
    if(!L.blend){simplified=true;L.blend='normal';}
    stack[stack.length-1].push(L);}
  keep.push(...stack.flat());
  let layers=keep;if(!layers.length&&psd.composite)layers=[{name:file.name.replace(/\.[^.]+$/,''),rect:{x:0,y:0,w:psd.width,h:psd.height},rgba:psd.composite,visible:true,opacity:1,fill:1,blend:'normal',clip:false,locks:{},mask:null}];
  if(!layers.length){say('This PSD has no picture layers this editor can use.',true);return false;}
  // size: the same size lands 1:1; a different size is centered, scaled down to fit when bigger
  const k=Math.min(1,W/psd.width,H/psd.height),ox=(W-psd.width*k)/2,oy=(H-psd.height*k)/2,resized=psd.width!==W||psd.height!==H;if(resized)simplified=true;
  let how=o.how;
  if(!how){const res=await Ed.dialog({title:'Import PSD',text:resized?`Layers in ${file.name}: ${layers.length}. The PSD has a different size, so its layers will be centered (scaled down when bigger).`:`Layers in ${file.name}: ${layers.length}.`,
      fields:[{key:'how',label:'Layers',type:'select',options:[{value:'replace',label:'Replace this image’s layers'},{value:'add',label:'Add them on top'}],default:resized?'add':'replace'}],okLabel:'Import'});
    if(!res||Ed.doc!==d)return false;how=res.how;}
  const place=(rgba,rect,def)=>{const c=U.canvas(W,H);if(!rect.w||!rect.h)return c;
    if(k===1&&Number.isInteger(ox)&&Number.isInteger(oy)){U.write(c,imageData(rgba,rect.w,rect.h),{x:rect.x+ox,y:rect.y+oy,w:rect.w,h:rect.h});if(def!==undefined)return c;return c;}
    const M=M3.mul(M3.T(ox,oy),M3.mul(M3.S(k,k),M3.T(rect.x,rect.y))),out=window.EdResample.transform({src:rgba,sw:rect.w,sh:rect.h,M,dx:0,dy:0,dw:W,dh:H,interp:'bicubic'});U.write(c,imageData(out,W,H));return c;};
  const built=layers.map(L=>{const canvas=place(L.rgba,L.rect);let mask=null;
    if(L.mask&&L.mask.data){const m=U.canvas(W,H);fillMask(m,L.mask.default);const r=L.mask.rect,rgba=new Uint8ClampedArray(r.w*r.h*4);for(let i=0;i<r.w*r.h;i++){rgba[i*4]=rgba[i*4+1]=rgba[i*4+2]=255;rgba[i*4+3]=L.mask.data[i];}
      if(r.w&&r.h){if(k===1&&Number.isInteger(ox)&&Number.isInteger(oy))U.write(m,imageData(rgba,r.w,r.h),area({x:r.x+ox,y:r.y+oy,w:r.w,h:r.h})&&{x:r.x+ox,y:r.y+oy,w:r.w,h:r.h});
        else{const M=M3.mul(M3.T(ox,oy),M3.mul(M3.S(k,k),M3.T(r.x,r.y))),out=window.EdResample.transform({src:rgba,sw:r.w,sh:r.h,M,dx:0,dy:0,dw:W,dh:H,interp:'bicubic',mask:true,edge:L.mask.default});U.write(m,imageData(out,W,H));}}
      mask=m;}
    return{name:L.name||'Layer',canvas,mask,maskEnabled:L.mask?L.mask.enabled!==false:true,visible:L.visible!==false,opacity:L.opacity,fill:L.fill??1,blend:Ed.blends.get(L.blend)?L.blend:(L.blend||'normal'),clip:!!L.clip,
      locks:{alpha:!!(L.locks&&L.locks.alpha),pixels:!!(L.locks&&L.locks.pixels),position:!!(L.locks&&L.locks.position),all:!!(L.locks&&L.locks.all)}};});
  const old=Ed.layers.list(),added=[],targetAt=psd.target!=null?layers.indexOf(psd.layers[psd.target]):-1;
  Ed.history.record('Import PSD',()=>{let at=how==='replace'?old.length:Ed.doc.activeIndex+1;
    for(const b of built){const L=Ed.layers.add({...b,at,history:false});added.push(L);if(L){at=Ed.layers.index(L)+1;Ed.layers.set(L,{maskEnabled:b.maskEnabled},{history:false});}}
    if(how==='replace')for(const L of old)Ed.layers.remove(L,{history:false});
    const t=added[targetAt]||added.filter(Boolean).at(-1);if(t)Ed.layers.setActive(t);},
    {op:{tool:'import-psd',file:file.name.slice(0,80),layers:built.length,how}});
  say(simplified?`Imported from ${file.name} (layers: ${built.length}). Some Photoshop features were simplified or left out (groups, layer styles, adjustment and text layers, smart objects, another canvas size).`
    :`Imported from ${file.name} (layers: ${built.length}).`);
  return true;}

// =========================================================================================================================
// ALIGN (Layer > Align; the Move tool's buttons): the layer's content to the selection, or to the canvas
// =========================================================================================================================
function align(edge){const L=Ed.layers.active(),why=refuse(L,{verb:'move'});if(why){say(why);return false;}if(session)commitSync();
  const s=L.kind==='pixel'?layerScan(L):maskScan(L),b=s&&s.bounds;if(!b){say(`There is nothing on ${q(L)} to align.`);return false;}
  const t=Ed.select.bounds()||docRect();let dx=0,dy=0;
  if(edge==='left')dx=t.x-b.x;else if(edge==='right')dx=t.x+t.w-(b.x+b.w);else if(edge==='hcenter')dx=Math.round(t.x+t.w/2-(b.x+b.w/2));
  else if(edge==='top')dy=t.y-b.y;else if(edge==='bottom')dy=t.y+t.h-(b.y+b.h);else dy=Math.round(t.y+t.h/2-(b.y+b.h/2));
  if(!dx&&!dy)return true;const sel=Ed.selection;
  // align moves the whole layer even with a selection (the selection is only the target)
  const m={kind:'layer',layer:L,target:Ed.doc.target,copy:false,pixels:L.kind==='pixel'?clone(L.canvas):null,pbounds:b,mask:L.mask&&(linkedMask(L)||L.kind!=='pixel')?clone(L.mask):null,dx:0,dy:0};
  if(m.mask){const ms=maskScan(L);m.edge=ms.edge;m.mbounds=ms.bounds;}
  const keep=sel;const ok=commitMoveKeepSelection(m,dx,dy,keep);return ok;}
function commitMoveKeepSelection(m,dx,dy){const L=m.layer,R=moveArea(m,{x:0,y:0},{x:dx,y:dy});if(!R)return false;
  Ed.history.record('Align',tx=>{if(m.pixels){tx.touch(L,'pixels',R);drawMoved(g2(L.canvas),m,dx,dy,R,'pixels');}if(m.mask&&L.mask){tx.touch(L,'mask',R);drawMoved(g2(L.mask),m,dx,dy,R,'mask');}},{op:{tool:'align',dx,dy}});
  memo=null;return true;}
Ed.registerField('ed-tf-align',()=>{const g=el('div','ed-tf-align');g.setAttribute('role','group');g.setAttribute('aria-label','Align');
  for(const[edge,title,paths] of ALIGN){const b=button('ed-tf-alignbtn','',title);b.dataset.align=edge;b.setAttribute('aria-label',title);b.append(Ed.icon(paths.split('|'),{size:16}));b.onclick=()=>align(edge);g.append(b);}
  return g;});

// =========================================================================================================================
// MENUS
// =========================================================================================================================
const has=()=>!!Ed.doc,hasSel=()=>!!Ed.selection,menu=(p,d)=>Ed.registerMenu(p,d);
menu('Edit/Cut',{order:30,separatorBefore:true,shortcut:'Ctrl+X',enabled:has,action:()=>cut()});
menu('Edit/Copy',{order:31,shortcut:'Ctrl+C',enabled:has,action:()=>{copy();}});
menu('Edit/Copy Merged',{order:32,shortcut:'Shift+Ctrl+C',enabled:has,action:()=>{copy({merged:true});}});
menu('Edit/Paste',{order:33,shortcut:'Ctrl+V',enabled:has,action:p=>p&&p.blob?pasteBlob(p.blob,'paste'):pasteCommand('paste')});
menu('Edit/Paste Special/Paste in Place',{order:34,shortcut:'Shift+Ctrl+V',enabled:has,action:p=>p&&p.blob?pasteBlob(p.blob,'in-place'):pasteCommand('in-place')});
menu('Edit/Paste Special/Paste Into',{order:35,shortcut:'Alt+Shift+Ctrl+V',enabled:()=>has()&&hasSel(),action:p=>p&&p.blob?pasteBlob(p.blob,'into'):pasteCommand('into')});
menu('Edit/Paste Special/Paste Outside',{order:36,enabled:()=>has()&&hasSel(),action:p=>p&&p.blob?pasteBlob(p.blob,'outside'):pasteCommand('outside')});
menu('Edit/Free Transform',{order:70,separatorBefore:true,shortcut:'Ctrl+T',enabled:()=>has()&&!session,action:()=>{startTransform({mode:'free'});}});
menu('Edit/Transform/Again',{order:71,shortcut:'Shift+Ctrl+T',enabled:()=>has()&&!!lastTransform&&!session,action:()=>again()});
[['Scale','scale',72],['Rotate','rotate',73],['Skew','skew',74],['Distort','distort',75],['Perspective','perspective',76]].forEach(([n,mode,order],i)=>
  menu('Edit/Transform/'+n,{order,separatorBefore:i===0,enabled:has,checked:()=>!!session&&session.mode===mode,action:()=>{startTransform({mode});}}));
[['Rotate 180°','180',80],['Rotate 90° Clockwise','cw',81],['Rotate 90° Counter Clockwise','ccw',82],['Flip Horizontal','h',83],['Flip Vertical','v',84]].forEach(([n,k,order])=>
  menu('Edit/Transform/'+n,{order,separatorBefore:order===80||order===83,enabled:has,action:()=>quick(k)}));
const square=()=>has()&&Ed.doc.width===Ed.doc.height;
menu('Image/Image Rotation/180°',{order:50,enabled:has,action:()=>canvasRotation('180')});
menu('Image/Image Rotation/90° Clockwise',{order:51,enabled:square,action:()=>canvasRotation('cw')});
menu('Image/Image Rotation/90° Counter Clockwise',{order:52,enabled:square,action:()=>canvasRotation('ccw')});
menu('Image/Image Rotation/Flip Canvas Horizontal',{order:53,separatorBefore:true,enabled:has,action:()=>canvasRotation('h')});
menu('Image/Image Rotation/Flip Canvas Vertical',{order:54,enabled:has,action:()=>canvasRotation('v')});
for(const[edge,n,order] of [['top','Top Edges',60],['vcenter','Vertical Centers',61],['bottom','Bottom Edges',62],['left','Left Edges',63],['hcenter','Horizontal Centers',64],['right','Right Edges',65]])
  menu('Layer/Align/'+n,{order,separatorBefore:order===63,enabled:()=>has()&&!!Ed.layers.active(),action:()=>align(edge)});
menu('File/Import PSD as Layers...',{order:11,enabled:has,action:p=>{if(p&&p.file)return importPSD(p.file,p);psdInput.click();}});
menu('File/Export Layered PSD...',{order:31,enabled:has,action:p=>exportPSD(p||{})});
Ed.ready.then(()=>{if(!Ed.menu.get('Select/Transform Selection'))menu('Select/Transform Selection',{order:60,separatorBefore:true,enabled:hasSel,action:()=>{startTransform({kind:'outline',mode:'free'});}});});

Ed.transform={start:o=>!!startTransform(o||{}),commit,cancel,get active(){return session?{kind:session.kind,mode:session.mode,quad:session.quad.map(p=>p.slice()),rect:{...session.rect},M:session.M.slice(),ref:{...session.ref},interp:session.interp,rev:session.rev,ready:!!(session.cpu&&session.cpu.rev===session.rev)}:null;},
  move:(dx,dy,o)=>moveBy(dx,dy,o||{}),flip:axis=>quick(axis==='v'?'v':'h'),rotate:deg=>quick(deg===180?'180':deg===90?'cw':'ccw'),align,copy,cut,paste:mode=>pasteCommand(mode||'paste'),
  pasteBlob,get clipboard(){return clip?{x:clip.x,y:clip.y,w:clip.w,h:clip.h,system:clip.system,merged:clip.merged}:null;},exportPSD,importPSD,canvasRotation,
  setQuad:Q=>!!session&&setQuad(session,Q.map(p=>p.slice())),again};
})();
