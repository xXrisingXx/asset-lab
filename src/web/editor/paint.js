'use strict';
/* =====================================================================================================================
   PAINT  ·  editor/paint.js: Photoshop's painting tools for the Asset Lab editor (built on window.Ed only).
   TOOLS (strip slot, key): Brush, Pencil (B) · Clone Stamp (S) · History Brush (Y) · Eraser (E). 'brush' and 'eraser'
     replace the shell's basic tools (same ids, #brushSize / #brushOpacity kept on the Brush).
   BRUSH ENGINE  A stroke is a row of round dabs placed every Spacing % of the current diameter along the pointer path
     (smoothed by Smoothing: the paint follows the pointer on a string and catches up when the stroke ends), plus one
     at the end point (unless Spacing is over 100 %, which paints separate marks). A dab's coverage of a pixel is its
     profile (hardness h, u = distance / radius: 1 for u <= h, then a smoothstep down to 0 at u = 1) averaged over the
     pixel's square, so hard edges are anti-aliased like an exact area coverage. Dabs gather in a float coverage buffer
     with "alpha darken": coverage moves toward the dab's cap by tip x Flow and never past it, so Flow builds up inside one stroke and
     crossing your own stroke never darkens it; Opacity is applied to the whole stroke (Photoshop's model). Pen pressure
     can scale the size (option 'pressure', on by default; a mouse always has full pressure) and the cap (Opacity).
     Shift-drag keeps the stroke horizontal or vertical; Shift-click draws a straight line from the last stroke.
   TWO PATHS  Brush, Pencil and Eraser fill Ed.strokeBuffer() and let the core composite it (Ed.preview.stroke with a
     canvas blend mode, the soft selection, lock transparency and the mask target; Ed.commitStroke). Clone Stamp and
     History Brush work on a CPU copy of the target taken when the stroke starts and show it with Ed.preview.layer; the
     soft selection, lock transparency and masks are applied here and the stroke is one Ed.history state.
   PENCIL  Aliased: a pixel is on when its centre is inside the tip; the tip walks pixel by pixel (Bresenham) between
     the pointer samples, so lines have no gaps. Pixel Perfect (1 px tips) removes the corner pixel of every L-shaped step
     of a freehand line, so diagonal lines stay one pixel thin, as in pixel-art editors.
   ===================================================================================================================== */
(()=>{
const Ed=window.Ed;if(!Ed||typeof Ed.registerTool!=='function')return;
const U=Ed.util,clamp=U.clamp;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const tr=s=>window.LabI18n&&typeof LabI18n.t==='function'?LabI18n.t(s):s;
function clip(r,W,H){if(!r)return null;const x=Math.max(0,Math.floor(r.x)),y=Math.max(0,Math.floor(r.y)),x2=Math.min(W,Math.ceil(r.x+r.w)),y2=Math.min(H,Math.ceil(r.y+r.h));
  return x2>x&&y2>y?{x,y,w:x2-x,h:y2-y}:null;}
const smooth=t=>t<=0?0:t>=1?1:t*t*(3-2*t);

// ----- the round tip ---------------------------------------------------------------------------------------------------
// profile(t, h) at t = distance / radius. A pixel's coverage is the profile averaged over the pixel's square: small tips
// are supersampled; bigger ones average the radial profile over the square's projection on the radius (a trapezoid
// |cos| + |sin| wide) through the profile's second integral K, which is exact for straight edges and very close on round ones.
const profile=(t,h)=>t<=h?1:t>=1?0:1-smooth((t-h)/(1-h));
function tipK(h){const w=1-h;if(w<1e-4)return t=>t<=1?t*t/2:t-.5;
  const k1=h*h/2+h*w+.35*w*w,half=h+w/2;
  return t=>{if(t<=h)return t*t/2;if(t>=1)return k1+half*(t-1);const T=(t-h)/w,T2=T*T;return h*h/2+h*(t-h)+w*w*(T2/2-T2*T2/4+T2*T2*T/10);};}
function superSample(px,py,x,y,R,h,n){let s=0;for(let j=0;j<n;j++){const dy=py+(j+.5)/n-y;for(let i=0;i<n;i++){const dx=px+(i+.5)/n-x;s+=profile(Math.sqrt(dx*dx+dy*dy)/R,h);}}return s/(n*n);}
// one dab's coverage over its box (clipped to the document): {m, x0, y0, w, h}; the array is reused
const DAB={m:new Float32Array(1<<14),x0:0,y0:0,w:0,h:0};
function rasterDab(x,y,size,h,W,H){const R=Math.max(.01,size/2),ext=R+1.5;h=clamp(h,0,1);
  const x0=Math.max(0,Math.floor(x-ext)),y0=Math.max(0,Math.floor(y-ext)),x1=Math.min(W,Math.ceil(x+ext)),y1=Math.min(H,Math.ceil(y+ext));
  DAB.x0=x0;DAB.y0=y0;DAB.w=Math.max(0,x1-x0);DAB.h=Math.max(0,y1-y0);const n=DAB.w*DAB.h;if(!n)return DAB;
  if(DAB.m.length<n)DAB.m=new Float32Array(Math.ceil(n*1.25));const m=DAB.m;
  if(R<3){for(let py=y0,k=0;py<y1;py++)for(let px=x0;px<x1;px++,k++)m[k]=superSample(px,py,x,y,R,h,8);return DAB;}
  const K=tipK(h),R2=R*R,inner=h*R-.75,outer=R+.75,Hf=r=>R2*K(Math.abs(r)/R);
  for(let py=y0,k=0;py<y1;py++){const dy=py+.5-y,dy2=dy*dy;for(let px=x0;px<x1;px++,k++){const dx=px+.5-x,d=Math.sqrt(dx*dx+dy2);
    if(d>=outer){m[k]=0;continue;}if(d<=inner){m[k]=1;continue;}if(d<1){m[k]=superSample(px,py,x,y,R,h,4);continue;}
    let c=Math.abs(dx)/d,s=Math.abs(dy)/d;if(c<s){const t=c;c=s;s=t;}if(s<1e-3)s=1e-3;const a=(c+s)/2,b=(c-s)/2;
    m[k]=clamp((Hf(d+a)-Hf(d+b)-Hf(d-b)+Hf(d-a))/(c*s),0,1);}}
  return DAB;}

// ----- pencil tips: a pixel is on when its centre is inside the tip (odd sizes sit on a pixel, even ones on a corner) ------
const pencilTips=new Map();
function pencilTip(size,square){const n=Math.max(1,Math.round(size)),key=n+(square?'s':'r');let t=pencilTips.get(key);if(t)return t;
  const odd=n&1,R=n/2,ext=Math.ceil(R)+1,dx=[],dy=[];
  for(let j=-ext;j<=ext;j++)for(let i=-ext;i<=ext;i++){const px=odd?i:i+.5,py=odd?j:j+.5;
    if(square?Math.abs(px)<=R+1e-9&&Math.abs(py)<=R+1e-9:px*px+py*py<=R*R+1e-9){dx.push(i);dy.push(j);}}
  t={n,odd,dx:Int16Array.from(dx),dy:Int16Array.from(dy)};t.edges=tipEdges(t);if(pencilTips.size>64)pencilTips.clear();pencilTips.set(key,t);return t;}
// the outline of a pencil tip (for the cursor): unit edges between on and off pixels, relative to the tip's grid point
function tipEdges(t){const on=new Set();for(let k=0;k<t.dx.length;k++)on.add(t.dx[k]+','+t.dy[k]);const e=[];
  for(let k=0;k<t.dx.length;k++){const x=t.dx[k],y=t.dy[k];if(!on.has(x+','+(y-1)))e.push(x,y,x+1,y);if(!on.has(x+','+(y+1)))e.push(x,y+1,x+1,y+1);
    if(!on.has((x-1)+','+y))e.push(x,y,x,y+1);if(!on.has((x+1)+','+y))e.push(x+1,y,x+1,y+1);}
  return Int16Array.from(e);}
const snapGrid=(v,odd)=>odd?Math.floor(v):Math.round(v);
// Bresenham: every pixel of an 8-connected line, one per step along the major axis
function bresenham(x0,y0,x1,y1,fn){const dx=Math.abs(x1-x0),sx=x0<x1?1:-1,dy=-Math.abs(y1-y0),sy=y0<y1?1:-1;let err=dx+dy;
  for(let guard=0;guard<100000;guard++){fn(x0,y0);if(x0===x1&&y0===y1)return;const e2=2*err;if(e2>=dy){err+=dy;x0+=sx;}if(e2<=dx){err+=dx;y0+=sy;}}}

// ----- options -----------------------------------------------------------------------------------------------------------
const num=(v,lo,hi,d)=>{v=Number(v);return isFinite(v)?clamp(v,lo,hi):d;};
function norm(o){return{...o,size:num(o.size,1,5000,12),hardness:num(o.hardness,0,100,100),opacity:num(o.opacity,1,100,100),flow:num(o.flow,1,100,100),
  spacing:num(o.spacing,1,1000,25),smoothing:num(o.smoothing,0,100,0)};}
const opts=id=>norm(Ed.toolOptions(id));

// ----- shared state ------------------------------------------------------------------------------------------------------
let hover=null,lastPaint=null,cloneSource=null,openSnapshot=null;
const track=p=>{hover={x:p.x,y:p.y};};
let covBuf=null;
function coverage(W,H){const n=W*H;if(!covBuf||covBuf.length!==n)covBuf=new Float32Array(n);return covBuf;}
// the target's pixels on the CPU (a mask as gray with alpha 255), read when a stroke starts
function readTarget(L,target){const d=U.read(target==='mask'?L.mask:L.canvas).data;
  if(target==='mask')for(let i=0;i<d.length;i+=4){const v=d[i+3];d[i]=d[i+1]=d[i+2]=v;d[i+3]=255;}return d;}
function selectionAlpha(){if(!Ed.selection)return null;const D=U.read(Ed.selection).data,n=D.length>>2,a=new Uint8Array(n);for(let i=0;i<n;i++)a[i]=D[i*4+3];return a;}

// ----- the stroke engine ---------------------------------------------------------------------------------------------------
const MAX_DABS=400000,SMOOTH_SCREEN=50;
const P=p=>{const pen=p.pointerType==='pen'||p.pointerType==='touch';return{x:+p.x||0,y:+p.y||0,pressure:pen?clamp(+p.pressure||0,0,1):1,pen};};
const lerpP=(a,b,t)=>({x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t,pressure:a.pressure+(b.pressure-a.pressure)*t,pen:b.pen});
// the diameter at a sample: pen pressure scales it when the tool's 'pressure' option is on
const sizeAt=(S,pt)=>S.o.pressure&&pt.pen?Math.max(1,S.o.size*pt.pressure):S.o.size;
const capAt=(S,pt)=>S.o.pressureOpacity&&pt.pen?pt.pressure:1;
const spacingAt=(S,pt)=>Math.max(.1,S.o.spacing/100*sizeAt(S,pt));
function walkTo(S,pt){if(S.pencil){pencilTo(S,pt);S.pos=pt;return;}const A=S.pos;
  if(!A){S.pos=pt;dab(S,pt);S.carry=spacingAt(S,pt);return;}
  const len=Math.hypot(pt.x-A.x,pt.y-A.y);
  while(S.carry<=len&&S.count<MAX_DABS){const q=lerpP(A,pt,len?S.carry/len:1);dab(S,q);S.carry+=spacingAt(S,q);}
  S.carry-=len;S.pos=pt;}
// "alpha darken": coverage moves toward the cap by tip x flow and never past it
function dab(S,pt){S.count++;S.lastDab=pt;if(S.impl.dab){S.impl.dab(S,pt);return;}
  const M=rasterDab(pt.x,pt.y,sizeAt(S,pt),S.o.hardness/100,S.W,S.H);if(!M.w)return;const {x0,y0,w,h,m}=M,cov=S.cov,W=S.W,cap=capAt(S,pt),f=S.flow;
  for(let y=0,k=0;y<h;y++){let i=(y0+y)*W+x0;for(let x=0;x<w;x++,k++,i++){const a=m[k];if(a<=0)continue;const c=cov[i];if(c<cap)cov[i]=c+(cap-c)*a*f;}}
  touch(S,{x:x0,y:y0,w,h});}
// The pencil walks pixel by pixel between grid points, so its lines are exact and gap-free.
function pencilTo(S,pt){const sz=S.square?S.o.size:Math.max(1,Math.round(sizeAt(S,pt))),odd=Math.round(sz)&1,gx=snapGrid(pt.x,odd),gy=snapGrid(pt.y,odd);
  if(!S.gp){S.gp={x:gx,y:gy};pencilStamp(S,gx,gy,pt,sz);return;}
  if(gx===S.gp.x&&gy===S.gp.y)return;const from=S.gp;
  bresenham(from.x,from.y,gx,gy,(x,y)=>{if(x!==from.x||y!==from.y)pencilStamp(S,x,y,pt,sz);});
  S.gp={x:gx,y:gy};}
function pencilStamp(S,gx,gy,pt,sz){const W=S.W,H=S.H,cov=S.cov,cap=capAt(S,pt),t=pencilTip(sz,S.square);S.count++;
  if(S.pp&&t.n===1){   // Pixel Perfect: the last three pixels a, b, c; when a and c touch diagonally, b is the corner of an L
    if(gx<0||gy<0||gx>=W||gy>=H){S.ppl.length=0;return;}const i=gy*W+gx;S.ppl.push({x:gx,y:gy,i,prev:cov[i]});if(cov[i]<cap)cov[i]=cap;
    const l=S.ppl;if(l.length>=3){const a=l[l.length-3],b=l[l.length-2],c=l[l.length-1];
      if(Math.abs(a.x-c.x)===1&&Math.abs(a.y-c.y)===1&&((b.x===a.x&&b.y===c.y)||(b.y===a.y&&b.x===c.x))){cov[b.i]=b.prev;l.splice(l.length-2,1);touch(S,{x:b.x,y:b.y,w:1,h:1});}}
    if(l.length>3)l.shift();touch(S,{x:gx,y:gy,w:1,h:1});return;}
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(let k=0;k<t.dx.length;k++){const x=gx+t.dx[k],y=gy+t.dy[k];if(x<0||y<0||x>=W||y>=H)continue;const i=y*W+x;if(cov[i]<cap)cov[i]=cap;
    if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}
  if(x1>=x0)touch(S,{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1});}
function touch(S,R){S.dirty=U.union(S.dirty,R);S.bounds=U.union(S.bounds,R);
  if(!S.pending&&S.alive){S.pending=true;queueMicrotask(()=>{S.pending=false;if(S.alive)flush(S);});}}
function flush(S){const R=clip(S.dirty,S.W,S.H);S.dirty=null;if(R)S.impl.frame(S,R);}
// Smoothing: the painted point follows the pointer on a string (shorter when zoomed in, as Photoshop's Adjust for Zoom)
const smoothRadius=S=>S.pencil||!S.o.smoothing?0:S.o.smoothing/100*SMOOTH_SCREEN/Math.max(.01,Ed.view.scale||1);
function feed(S,raw){S.raw=raw;if(raw.pressure>0)S.lastPressure=raw.pressure;const L=smoothRadius(S);if(!L){walkTo(S,raw);return;}
  const z=S.lazy,d=Math.hypot(raw.x-z.x,raw.y-z.y);z.pressure=raw.pressure;z.pen=raw.pen;
  if(d>L){const k=1-L/d;z.x+=(raw.x-z.x)*k;z.y+=(raw.y-z.y)*k;walkTo(S,{...z});}}
// Shift while dragging: horizontal or vertical, whichever way the stroke first went
function constrain(S,pt,shift){if(!shift){S.axis=null;return pt;}const A=S.anchor;
  if(!S.axis){const dx=pt.x-A.x,dy=pt.y-A.y;if(Math.hypot(dx,dy)*(Ed.view.scale||1)<3)return{...pt,x:A.x,y:A.y};S.axis=Math.abs(dx)>=Math.abs(dy)?'h':'v';}
  return S.axis==='h'?{...pt,y:A.y}:{...pt,x:A.x};}
function clearCov(S){const b=clip(S.bounds,S.W,S.H);if(!b||S.cov!==covBuf)return;for(let y=b.y;y<b.y+b.h;y++)S.cov.fill(0,y*S.W+b.x,y*S.W+b.x+b.w);}

// Start, continue, finish or cancel a stroke. spec.make(S, p) returns the implementation:
// {start(S) -> false to refuse, dab(S, pt) (optional), frame(S, rect), commit(S), cancel(S)}.
function strokeDown(tool,spec,p){const doc=Ed.doc;if(!doc)return false;
  const S={tool,o:opts(tool.id),doc,W:doc.width,H:doc.height,cov:coverage(doc.width,doc.height),bounds:null,dirty:null,carry:0,count:0,flow:1,
    alive:true,pending:false,lastPressure:1,pos:null,axis:null,ppl:[]};
  S.flow=S.o.flow/100;const impl=spec.make.call(tool,S,p);if(!impl){S.alive=false;return false;}S.impl=impl;
  if(impl.start&&impl.start(S)===false){S.alive=false;clearCov(S);return false;}
  tool.stroke=S;const pt=P(p);S.anchor=pt;if(pt.pressure>0)S.lastPressure=pt.pressure;
  if(p.shiftKey&&lastPaint&&lastPaint.doc===doc){walkTo(S,{...lastPaint.pt,pressure:pt.pressure,pen:pt.pen});walkTo(S,pt);}else walkTo(S,pt);
  S.lazy={...pt};S.raw=pt;return true;}
function strokeMove(tool,p){const S=tool.stroke;if(!S||!S.alive)return;feed(S,constrain(S,P(p),p.shiftKey));}
function strokeUp(tool,p){const S=tool.stroke;if(!S||!S.alive)return;tool.stroke=null;
  if(p){let pt=P(p);pt.pressure=S.lastPressure;pt=constrain(S,pt,p.shiftKey);if(Math.hypot(pt.x-S.raw.x,pt.y-S.raw.y)>1e-6)feed(S,pt);}
  if(smoothRadius(S))walkTo(S,{...S.raw,pressure:S.lastPressure});   // catch up with the pointer at the end of the stroke
  // a stroke ends exactly where it was let go (unless the dabs are spaced apart as separate marks)
  if(!S.pencil&&S.lastDab&&S.pos&&S.o.spacing<=100&&Math.hypot(S.pos.x-S.lastDab.x,S.pos.y-S.lastDab.y)>1e-3)dab(S,S.pos);
  flush(S);const end=S.pos||S.raw;
  try{S.impl.commit(S);}finally{S.alive=false;clearCov(S);}
  if(end)lastPaint={doc:S.doc,pt:{...end}};}
function abort(tool){const S=tool.stroke;tool.stroke=null;if(!S)return;S.alive=false;try{S.impl.cancel(S);}finally{clearCov(S);}}

// ----- the color path: Ed.strokeBuffer + Ed.preview.stroke ----------------------------------------------------------------
// cfg = {color, mode (canvas operation), opacity 0..1, label, record (the recipe entry)}
function colorImpl(cfg){return{
  start(S){S.buf=Ed.strokeBuffer();if(!S.buf)return false;S.rgb=U.rgbOf(cfg.color);
    S.pv=Ed.preview.stroke({canvas:S.buf,opacity:cfg.opacity,mode:cfg.mode,color:cfg.color});return !!S.pv;},
  frame(S,R){const img=new ImageData(R.w,R.h),D=img.data,cov=S.cov,W=S.W,[r,g,b]=S.rgb;
    for(let y=0;y<R.h;y++){let i=(R.y+y)*W+R.x,k=y*R.w*4;for(let x=0;x<R.w;x++,i++,k+=4){const c=cov[i];if(c<=0)continue;D[k]=r;D[k+1]=g;D[k+2]=b;D[k+3]=Math.round(c*255);}}
    U.write(S.buf,img,R);if(Ed.preview.active===S.pv)Ed.preview.update(R);},
  commit(S){if(Ed.preview.active!==S.pv)return;Ed.commitStroke(cfg.label,{rect:clip(S.bounds,S.W,S.H),op:cfg.record});},
  cancel(S){if(Ed.preview.active===S.pv)Ed.preview.clear();}};}

// ----- the pixel path: a CPU copy of the target and a whole-layer preview ---------------------------------------------------
// cfg = {label, record, start(S) -> false to refuse, pixel(S, i, out): out = the premultiplied 0..1 RGBA result at pixel i}
function pixelImpl(cfg){return{
  start(S){const L=Ed.layers.active(),target=Ed.doc.target;if(!L)return false;const ref=target==='mask'?L.mask:L.canvas;if(!ref)return false;
    S.L=L;S.target=target;S.mask=target==='mask';S.base=readTarget(L,target);S.sel=selectionAlpha();S.lock=!S.mask&&!!L.locks.alpha;S.out4=new Float64Array(4);
    if(cfg.start&&cfg.start(S)===false)return false;
    S.canvas=U.canvas(S.W,S.H);S.canvas.getContext('2d').drawImage(ref,0,0);
    S.pv=Ed.preview.layer({layer:L,canvas:S.canvas,target});return !!S.pv;},
  frame(S,R){writeOut(S,R,cfg.pixel);if(Ed.preview.active===S.pv)Ed.preview.update(R);},
  commit(S){const R=clip(S.bounds,S.W,S.H);if(Ed.preview.active===S.pv)Ed.preview.clear(false);
    const L=S.L,t=S.target,dst=t==='mask'?L.mask:L.canvas;if(!R||!dst||Ed.doc!==S.doc||!S.doc.layers.includes(L)){Ed.update();return;}
    Ed.history.record(cfg.label,tx=>{tx.touch(L,t,R);const g=dst.getContext('2d');g.save();g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.globalCompositeOperation='source-over';
      g.clearRect(R.x,R.y,R.w,R.h);g.drawImage(S.canvas,R.x,R.y,R.w,R.h,R.x,R.y,R.w,R.h);g.restore();Ed.update(R);},{rect:R,op:cfg.record});},
  cancel(S){if(Ed.preview.active===S.pv)Ed.preview.clear();}};}
// the preview's pixels in R: the tool's result through the soft selection, with lock transparency, as gray on masks
function writeOut(S,R,pixel){const W=S.W,img=new ImageData(R.w,R.h),D=img.data,B=S.base,sel=S.sel,o=S.out4;
  for(let y=0;y<R.h;y++){let i=(R.y+y)*W+R.x,k=y*R.w*4;for(let x=0;x<R.w;x++,i++,k+=4){const j=i*4,ba=B[j+3]/255;
    if(S.cov[i]<=0||(sel&&!sel[i])){D[k]=B[j];D[k+1]=B[j+1];D[k+2]=B[j+2];D[k+3]=B[j+3];if(S.mask){D[k]=D[k+1]=D[k+2]=255;D[k+3]=B[j];}continue;}
    pixel(S,i,o);let r=o[0],g=o[1],b=o[2],a=o[3];
    if(sel&&sel[i]<255){const s=sel[i]/255;r=B[j]/255*ba+(r-B[j]/255*ba)*s;g=B[j+1]/255*ba+(g-B[j+1]/255*ba)*s;b=B[j+2]/255*ba+(b-B[j+2]/255*ba)*s;a=ba+(a-ba)*s;}
    if(S.mask){D[k]=D[k+1]=D[k+2]=255;D[k+3]=Math.round(clamp(r,0,1)*255);continue;}
    if(S.lock){D[k+3]=B[j+3];if(a>1e-6){D[k]=Math.round(clamp(r/a,0,1)*255);D[k+1]=Math.round(clamp(g/a,0,1)*255);D[k+2]=Math.round(clamp(b/a,0,1)*255);}else{D[k]=B[j];D[k+1]=B[j+1];D[k+2]=B[j+2];}continue;}
    if(a<=1/510)continue;D[k]=Math.round(clamp(r/a,0,1)*255);D[k+1]=Math.round(clamp(g/a,0,1)*255);D[k+2]=Math.round(clamp(b/a,0,1)*255);D[k+3]=Math.round(clamp(a,0,1)*255);}}
  U.write(S.canvas,img,R);}

// ----- option descriptors ------------------------------------------------------------------------------------------------
const ICON={
  brush:['M20 4c-4 2.5-8.5 7-10.5 10l1.5 1.5c3-2 7.5-6.5 10-10.5z','M9.5 14c-2.2 0-3.5 1.6-3.5 3.4 0 1.4-.9 2.2-2 2.6 3.6 1.2 7.6.1 7-4.5'],
  pencil:['M4.5 19.5l1.2-4.6L16.2 4.4a2.1 2.1 0 0 1 3 3L8.7 17.9z','M14.3 6.3l3.4 3.4','M5.7 14.9l3 3'],
  eraser:['M8.5 19.5l-4.2-4.2a1.5 1.5 0 0 1 0-2.1l8.9-8.9a1.5 1.5 0 0 1 2.1 0l4.2 4.2a1.5 1.5 0 0 1 0 2.1l-8.1 8.9z','M8.5 19.5H20','M9 9.5l5.5 5.5'],
  stamp:['M9.5 4h5v4c0 1.3 4 1.8 4 5H5.5c0-3.2 4-3.7 4-5z','M4.5 15.5h15v2.5h-15z','M7 20.5h10'],
  history:['M19.5 4c-3.4 2.2-6.8 5.5-8.5 8l1.4 1.4c2.5-1.6 5.9-5 8-8.4z','M10.6 13.5c-1.9 0-3 1.4-3 2.9 0 1.2-.8 1.9-1.7 2.2 3.1 1 6.5.1 6-3.9','M3.4 10.5A6.5 6.5 0 0 1 9 4.1','M2.8 7.4l.6 3.1 3-.7'],
  pressSize:['M4 18.5c4.5 0 6-13 9-13s3.5 8 7 8','M18 4l2 2-9 9-3 1 1-3z'],
  pressOpacity:['M12 3.5c3 4 6 7.2 6 10.5a6 6 0 0 1-12 0c0-3.3 3-6.5 6-10.5z','M8.5 14.5a3.5 3.5 0 0 0 3.5 3.5']};
const icon=name=>()=>Ed.icon(ICON[name]);
const O={
  tip:()=>({key:'hardness',type:'paint-tip',min:0,max:100,default:100,title:'Brush tip: size, hardness, spacing and smoothing'}),
  size:(def,id)=>({key:'size',label:'Size',type:'range',min:1,max:5000,step:1,unit:'px',default:def,id,title:'Brush size in image pixels ([ and ] change it)'}),
  mode:()=>({key:'mode',label:'Mode',type:'paint-mode',default:'normal',title:'Painting mode: how the paint mixes with the pixels already on the layer'}),
  opacity:id=>({key:'opacity',label:'Opacity',type:'range',min:1,max:100,step:1,unit:'%',default:100,id,title:'The most one stroke can cover: going over your own stroke never makes it stronger (digit keys: 5 = 50%)'}),
  flow:()=>({key:'flow',label:'Flow',type:'range',min:1,max:100,step:1,unit:'%',default:100,title:'How fast paint builds up within one stroke, up to the Opacity (Shift + digit keys)'}),
  pressureOpacity:()=>({key:'pressureOpacity',type:'paint-toggle',icon:'pressOpacity',default:false,title:'Pen pressure controls opacity (drawing tablets)'}),
  pressure:()=>({key:'pressure',type:'paint-toggle',icon:'pressSize',default:true,title:'Pen pressure controls size (drawing tablets)'}),
  hidden:(key,def)=>({key,type:'paint-hidden',default:def})};
const brushOptions=(size,{ids=false,mode=true}={})=>[O.tip(),O.size(size,ids?'brushSize':undefined),...(mode?[O.mode()]:[]),O.opacity(ids?'brushOpacity':undefined),O.pressureOpacity(),O.flow(),O.pressure(),
  O.hidden('spacing',25),O.hidden('smoothing',0)];

// ----- registering a painting tool -----------------------------------------------------------------------------------------
// spec: {label, group, key, order, icon, options, cursor, make(S, p) -> impl, down(p, o) (returns true/false to handle the
// press itself, undefined to paint), altEyedropper, pencilCursor(o) -> [size, square] for a pixel outline cursor or null for a round outline, overlay(ctx, view)}
const TOOLS=new Set();
function paintTool(id,spec){TOOLS.add(id);
  Ed.registerTool(id,{label:spec.label,group:spec.group,key:spec.key,order:spec.order,icon:spec.icon,cursor:spec.pencilCursor?'none':'brush',edits:true,
    altEyedropper:spec.altEyedropper!==false,options:spec.options,
    onActivate(){this.stroke=null;},
    onDeactivate(){abort(this);closePop();},
    onDown(p){track(p);if(spec.down){const r=spec.down.call(this,p);if(r!==undefined)return r;}return strokeDown(this,spec,p);},
    onMove(p){track(p);strokeMove(this,p);if(spec.pencilCursor||spec.overlay)Ed.view.redrawOverlay();},
    onUp(p){track(p);strokeUp(this,p);Ed.view.redrawOverlay();},
    onHover(p){track(p);if(spec.pencilCursor||spec.overlay)Ed.view.redrawOverlay();},
    onCancel(){abort(this);Ed.view.redrawOverlay();},
    onKey(e){return flowKey(this,e);},
    onOptions(){refreshWidgets();Ed.view.redrawOverlay();},
    drawOverlay(ctx,view){if(spec.overlay)spec.overlay.call(this,ctx,view);if(spec.pencilCursor){const o=opts(id);pencilCursor(spec.pencilCursor(o),o,ctx,view);}}});}
// Shift + digits set the Flow, as the digits set the Opacity (two quick digits = an exact value)
let lastFlowDigit=null;
function flowKey(tool,e){if(e.ctrlKey||e.metaKey||e.altKey||!e.shiftKey||!/^Digit\d$/.test(e.code))return false;
  if(!(Ed.tools.get(tool.id).options||[]).some(o=>o.key==='flow'))return false;
  const d=+e.code.slice(5),t=performance.now();let v=d===0?100:d*10;if(lastFlowDigit&&t-lastFlowDigit.t<700){v=lastFlowDigit.d*10+d||100;lastFlowDigit=null;}else lastFlowDigit={d,t};
  Ed.setToolOptions(tool.id,{flow:clamp(v,1,100)});return true;}
// painting modes the canvas does exactly: Normal, Behind, Clear and every blend mode with a canvas operation
function modeList(){const out=[{value:'normal',label:'Normal'},{value:'behind',label:'Behind'},{value:'clear',label:'Clear'}];let g='normal';
  for(const b of Ed.blends.list()){const def=Ed.blends.get(b.id);if(b.id==='normal'||!def||!def.op||def.op==='lighter')continue;if(b.group!==g){out.push('-');g=b.group;}out.push({value:b.id,label:b.label});}return out;}
const modeOp=mode=>mode==='behind'?'destination-over':mode==='clear'?'destination-out':(mode!=='normal'&&Ed.blends.get(mode)&&Ed.blends.get(mode).op)||'source-over';

// ----- cursors and overlays -----------------------------------------------------------------------------------------------
function strokeTwice(ctx){ctx.lineWidth=2.5;ctx.strokeStyle='rgba(0,0,0,.55)';ctx.stroke();ctx.lineWidth=1;ctx.strokeStyle='rgba(255,255,255,.92)';ctx.stroke();}
function crosshair(ctx,x,y,r=5){ctx.beginPath();ctx.moveTo(x-r,y+.5);ctx.lineTo(x+r+1,y+.5);ctx.moveTo(x+.5,y-r);ctx.lineTo(x+.5,y+r+1);strokeTwice(ctx);}
// the pencil's cursor: the outline of the pixels it will set (a crosshair while that is too small to see)
function pencilCursor(tip,o,ctx,view){if(!hover)return;
  if(!tip){const p=view.toScreen(hover.x,hover.y),r=o.size*view.scale/2;if(r<3){crosshair(ctx,Math.round(p.x),Math.round(p.y));return;}ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);strokeTwice(ctx);return;}
  const [size,square]=tip,t=pencilTip(size,square),gx=snapGrid(hover.x,t.odd),gy=snapGrid(hover.y,t.odd);
  if(t.n*view.scale<4){const p=view.toScreen(hover.x,hover.y);crosshair(ctx,Math.round(p.x),Math.round(p.y));return;}
  ctx.beginPath();for(let k=0;k<t.edges.length;k+=4){const a=view.toScreen(gx+t.edges[k],gy+t.edges[k+1]),b=view.toScreen(gx+t.edges[k+2],gy+t.edges[k+3]);
    ctx.moveTo(Math.round(a.x)+.5,Math.round(a.y)+.5);ctx.lineTo(Math.round(b.x)+.5,Math.round(b.y)+.5);}strokeTwice(ctx);}

// ====================================================================================================================
// THE TOOLS
// ====================================================================================================================
// cfg for the color path from a tool's options; null (with a message) when the mode cannot work here
function paintCfg(label,tool,color,o){const L=Ed.layers.active(),mask=Ed.doc.target==='mask',mode=mask?'normal':o.mode||'normal';
  if(!mask&&L&&L.locks.alpha&&(mode==='behind'||mode==='clear')){Ed.message('Behind and Clear cannot be used while the layer’s transparent pixels are locked.');return null;}
  const record={tool,color,size:Math.round(o.size*10)/10};if(mode!=='normal')record.mode=mode;
  return colorImpl({color,mode:modeOp(mode),opacity:o.opacity/100,label,record});}

// ----- Brush (B) ------------------------------------------------------------------------------------------------------
paintTool('brush',{label:'Brush Tool',group:'brush',key:'b',order:1,icon:icon('brush'),options:brushOptions(12,{ids:true}),
  make(S){return paintCfg('Brush Tool','brush',Ed.fg,S.o);}});

// ----- Pencil (B) -----------------------------------------------------------------------------------------------------
paintTool('pencil',{label:'Pencil Tool',group:'brush',key:'b',order:2,icon:icon('pencil'),pencilCursor:o=>[o.size,false],
  options:[O.size(1),O.mode(),O.opacity(),
    {key:'autoErase',label:'Auto Erase',type:'checkbox',default:false,title:'Starting on the foreground color paints with the background color instead'},
    {key:'pixelPerfect',label:'Pixel Perfect',type:'checkbox',default:true,title:'For 1 px pixel-art lines: removes the extra corner pixel of every L-shaped step, so freehand diagonals stay one pixel thin'}],
  make(S,p){S.pencil=true;S.pp=!!S.o.pixelPerfect;S.flow=1;let color=Ed.fg;
    if(S.o.autoErase&&Ed.doc.target==='pixels'){const c=Ed.sample(p.x,p.y,{from:'layer'});if(c&&c.toLowerCase()===Ed.fg.toLowerCase())color=Ed.bg;}
    return paintCfg('Pencil','pencil',color,S.o);}});

// ----- Eraser (E) -------------------------------------------------------------------------------------------------------
// Brush and Pencil erase to transparency (to the background color under lock transparency, and on a mask the background
// color's gray); Block is a square of 16 screen pixels, always at full strength.
const blockSize=()=>Math.max(1,Math.round(16/(Ed.view.scale||1)));
paintTool('eraser',{label:'Eraser Tool',group:'eraser',key:'e',order:1,icon:icon('eraser'),
  pencilCursor:o=>o.eraseMode==='pencil'?[o.size,false]:o.eraseMode==='block'?[blockSize(),true]:null,
  options:[{key:'eraseMode',label:'Mode',type:'select',options:[{value:'brush',label:'Brush'},{value:'pencil',label:'Pencil'},{value:'block',label:'Block'}],default:'brush',
      title:'Brush: soft or hard edges · Pencil: whole pixels · Block: a 16 px square on screen at full strength'},
    O.tip(),O.size(20),O.opacity(),O.pressureOpacity(),O.flow(),O.pressure(),O.hidden('spacing',25),O.hidden('smoothing',0)],
  make(S){const mode=S.o.eraseMode;
    if(mode==='pencil'){S.pencil=true;S.flow=1;}
    if(mode==='block'){S.pencil=true;S.square=true;S.flow=1;S.o={...S.o,size:blockSize(),opacity:100,pressure:false,pressureOpacity:false};}
    const L=Ed.layers.active(),lock=Ed.doc.target==='pixels'&&L&&L.locks.alpha,record={tool:'eraser',size:Math.round(S.o.size*10)/10};if(mode!=='brush')record.mode=mode;
    return colorImpl({color:Ed.bg,mode:lock?'source-over':'destination-out',opacity:S.o.opacity/100,label:'Eraser Tool',record});}});

// ----- Clone Stamp (S) --------------------------------------------------------------------------------------------------
// Alt-click sets the source. The offset between the source and where the first stroke starts is kept for later strokes
// while Aligned is on; off, every stroke starts copying from the source point again. Pixels are copied exactly (whole-
// pixel offsets) from the layer as it was when the stroke started (Current Layer), the layers up to it, or all layers.
function cloneDown(p){if(p.altKey){const x=Math.floor(p.x),y=Math.floor(p.y);cloneSource={doc:Ed.doc,x,y,offset:null};Ed.view.redrawOverlay();return false;}
  if(!cloneSource||cloneSource.doc!==Ed.doc){Ed.message('Alt-click where to copy from first (that sets the source).');return false;}}
function cloneOffset(p,aligned){const cs=cloneSource,x=Math.floor(p.x),y=Math.floor(p.y);if(!aligned||!cs.offset)cs.offset={x:x-cs.x,y:y-cs.y};return cs.offset;}
function cloneSampled(kind){const doc=Ed.doc;let c;if(kind==='all')c=Ed.composite();else c=Ed.render.composite({layers:doc.layers.slice(0,doc.activeIndex+1)});return U.read(c).data;}
// the source point: a crosshair where the copy comes from (it follows the brush while painting)
function cloneOverlay(ctx,view){const cs=cloneSource;if(!cs||cs.doc!==Ed.doc)return;let sx=cs.x+.5,sy=cs.y+.5;
  const S=this.stroke;if(S&&S.off&&hover){sx=hover.x-S.off.x;sy=hover.y-S.off.y;}else if(cs.offset&&opts('clone-stamp').aligned&&hover){sx=hover.x-cs.offset.x;sy=hover.y-cs.offset.y;}
  const q=view.toScreen(sx,sy);ctx.beginPath();ctx.arc(q.x,q.y,7,0,Math.PI*2);strokeTwice(ctx);crosshair(ctx,Math.round(q.x),Math.round(q.y),10);}
paintTool('clone-stamp',{label:'Clone Stamp Tool',group:'clone',key:'s',order:1,icon:icon('stamp'),altEyedropper:false,down:cloneDown,overlay:cloneOverlay,
  options:[O.tip(),O.size(30),O.opacity(),
    {key:'aligned',label:'Aligned',type:'checkbox',default:true,title:'Keep the same offset for every stroke; off starts each stroke from the source point again'},
    {key:'sampleFrom',label:'Sample',type:'select',options:[{value:'layer',label:'Current Layer'},{value:'below',label:'Current & Below'},{value:'all',label:'All Layers'}],default:'layer',title:'Which layers the copy is taken from'},
    O.hidden('flow',100),O.hidden('spacing',25),O.hidden('smoothing',0)],
  make(S,p){const off=cloneOffset(p,S.o.aligned);S.off=off;const k=S.o.opacity/100,W=S.W,H=S.H;let src=null;
    return pixelImpl({label:'Clone Stamp',record:{tool:'clone-stamp',dx:off.x,dy:off.y},
      start(S){src=S.mask||S.o.sampleFrom==='layer'?S.base:cloneSampled(S.o.sampleFrom);},
      pixel(S,i,out){const B=S.base,j=i*4,ab=B[j+3]/255,x=i%W,y=(i-x)/W,sx=x-off.x,sy=y-off.y;
        if(sx<0||sy<0||sx>=W||sy>=H){out[0]=B[j]/255*ab;out[1]=B[j+1]/255*ab;out[2]=B[j+2]/255*ab;out[3]=ab;return;}
        const q=(sy*W+sx)*4,sa=src[q+3]/255*S.cov[i]*k,keep=1-sa;
        out[0]=src[q]/255*sa+B[j]/255*ab*keep;out[1]=src[q+1]/255*sa+B[j+1]/255*ab*keep;out[2]=src[q+2]/255*sa+B[j+2]/255*ab*keep;out[3]=sa+ab*keep;}});}});

// ----- History Brush (Y) ------------------------------------------------------------------------------------------------
// Paints back the History Brush source: the snapshot or state picked in the History panel when that panel's module is
// loaded, otherwise the document as it was opened. Transparency comes back too (the pixels move toward the source).
function snapshotOpen(){const doc=Ed.doc;if(!doc){openSnapshot=null;return;}const layers=new Map(),masks=new Map(),copy=c=>{const o=U.canvas(doc.width,doc.height);o.getContext('2d').drawImage(c,0,0);return o;};
  for(const L of doc.layers){if(L.kind!=='pixel')continue;layers.set(L.id,copy(L.canvas));masks.set(L.id,L.mask?copy(L.mask):null);}openSnapshot={doc,label:'Open',layers,masks};}
function historySource(){if('historySource' in Ed)return Ed.historySource&&Ed.historySource.layers?Ed.historySource:null;return openSnapshot&&openSnapshot.doc===Ed.doc?openSnapshot:null;}
paintTool('history-brush',{label:'History Brush Tool',group:'history',key:'y',order:1,icon:icon('history'),options:brushOptions(30,{mode:false}),
  make(S){const L=Ed.layers.active(),hs=historySource(),mask=Ed.doc.target==='mask';
    if(!hs){Ed.message('Pick a History Brush source in the History panel first.');return null;}
    const c=L&&(mask?hs.masks&&hs.masks.get(L.id):hs.layers.get(L.id));
    if(!c){Ed.message(mask?'The History Brush source has no mask for this layer.':'The History Brush source has no copy of this layer.');return null;}
    const k=S.o.opacity/100;let src=null;
    return pixelImpl({label:'History Brush',record:{tool:'history-brush'},
      start(S){src=U.read(c).data;if(S.mask)for(let i=0;i<src.length;i+=4){const v=src[i+3];src[i]=src[i+1]=src[i+2]=v;src[i+3]=255;}},
      pixel(S,i,out){const B=S.base,j=i*4,ab=B[j+3]/255,sa=src[j+3]/255,c=S.cov[i]*k,keep=1-c;
        out[0]=B[j]/255*ab*keep+src[j]/255*sa*c;out[1]=B[j+1]/255*ab*keep+src[j+1]/255*sa*c;out[2]=B[j+2]/255*ab*keep+src[j+2]/255*sa*c;out[3]=ab*keep+sa*c;}});}});

// ====================================================================================================================
// OPTIONS BAR WIDGETS AND THE BRUSH POPOVER
// ====================================================================================================================
let pop=null;
function closePop(){if(!pop)return;pop.el.remove();pop.anchor.setAttribute('aria-expanded','false');pop=null;}
function openPop(anchor,title,build){if(pop&&pop.anchor===anchor){closePop();return;}closePop();
  const p=el('div','paint-pop');p.setAttribute('role','dialog');p.setAttribute('aria-label',title);p.append(el('div','paint-pop-title',title));
  const body=el('div','paint-pop-body');p.append(body);pop={el:p,anchor,syncs:[],tool:Ed.tool};build(body,pop);document.body.append(p);anchor.setAttribute('aria-expanded','true');
  const r=anchor.getBoundingClientRect();p.style.left=Math.max(6,Math.min(r.left,innerWidth-p.offsetWidth-8))+'px';p.style.top=Math.max(6,Math.min(r.bottom+6,innerHeight-p.offsetHeight-8))+'px';
  p.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closePop();anchor.focus();}});}
document.addEventListener('pointerdown',e=>{if(pop&&!pop.el.contains(e.target)&&!pop.anchor.contains(e.target))closePop();},true);
window.addEventListener('resize',closePop);
// the tip preview: the dab drawn light on dark with the current hardness
function drawTip(cv,o){const n=cv.width,g=cv.getContext('2d'),img=g.createImageData(n,n),D=img.data,M=rasterDab(n/2,n/2,n-4,o.hardness/100,n,n);
  for(let y=0;y<M.h;y++)for(let x=0;x<M.w;x++){const k=((M.y0+y)*n+M.x0+x)*4;D[k]=D[k+1]=D[k+2]=235;D[k+3]=Math.round(M.m[y*M.w+x]*255);}g.putImageData(img,0,0);}
const POP_FIELDS=[{key:'size',label:'Size',type:'range',min:1,max:5000,step:1,unit:'px'},{key:'hardness',label:'Hardness',type:'range',min:0,max:100,step:1,unit:'%'},
  {key:'spacing',label:'Spacing',type:'range',min:1,max:1000,step:1,unit:'%',title:'Distance between dabs, in % of the diameter'},
  {key:'smoothing',label:'Smoothing',type:'range',min:0,max:100,step:1,unit:'%',title:'Steadies the line: the paint follows the pointer on a string and catches up at the end of the stroke'}];
const FLOW_FIELD={key:'flow',label:'Flow',type:'range',min:1,max:100,step:1,unit:'%',title:'How fast paint builds up within one stroke, up to the Opacity (Shift + digit keys)'};
Ed.registerField('paint-hidden',()=>null);
Ed.registerField('paint-tip',()=>{const b=el('button','paint-tipbtn');b.type='button';b.title='Brush tip: size, hardness, spacing and smoothing';b.setAttribute('aria-label','Brush tip settings');
  b.setAttribute('aria-haspopup','dialog');b.setAttribute('aria-expanded','false');const cv=el('canvas','paint-tipcv');cv.width=cv.height=22;b.append(cv,el('span','paint-tipcaret','▾'));
  b._draw=()=>{if(TOOLS.has(Ed.tool))drawTip(cv,opts(Ed.tool));};b._draw();
  b.onclick=()=>openPop(b,tr('Brush tip'),(box,ps)=>{const id=ps.tool,prev=el('canvas','paint-tip-preview');prev.width=prev.height=64;ps.redraw=()=>drawTip(prev,opts(id));ps.redraw();box.append(prev);
    const fields=el('div','paint-pop-fields');box.append(fields);
    const hidden=new Set((Ed.tools.get(id).options||[]).filter(d=>d.type==='paint-hidden').map(d=>d.key));
    for(const d of [...POP_FIELDS.slice(0,2),...(hidden.has('flow')?[FLOW_FIELD]:[]),...POP_FIELDS.slice(2)]){const f=Ed.ui.field(d,opts(id)[d.key],v=>Ed.setToolOptions(id,{[d.key]:v}));ps.syncs.push([d.key,f.sync]);fields.append(f.node);}});
  return b;});
Ed.registerField('paint-toggle',(o,value,set)=>{const b=el('button','paint-toggle');b.type='button';b.title=o.title;b.setAttribute('aria-label',o.title);b.setAttribute('aria-pressed',String(!!value));
  b.dataset.key=o.key;b.append(Ed.icon(ICON[o.icon],{size:16}));b.onclick=()=>set(!Ed.toolOptions(Ed.tool)[o.key]);return b;});
Ed.registerField('paint-mode',(o,value,set)=>{const s=el('select','paint-mode');s.name='mode';s.setAttribute('aria-label','Mode');
  for(const m of modeList()){if(m==='-'){s.append(el('hr'));continue;}const op=el('option','',m.label);op.value=m.value;s.append(op);}
  s.value=String(value);if(s.selectedIndex<0)s.value='normal';s.onchange=()=>{set(s.value);s.blur();};return s;});
// keep the custom widgets in step with option changes ([ and ], Shift+[ ], digits, the popover)
function refreshWidgets(){const id=Ed.tool;if(!TOOLS.has(id))return;const v=opts(id),box=document.getElementById('edOptions');
  if(box){for(const b of box.querySelectorAll('.paint-tipbtn'))b._draw&&b._draw();for(const b of box.querySelectorAll('.paint-toggle[data-key]'))b.setAttribute('aria-pressed',String(!!v[b.dataset.key]));
    for(const s of box.querySelectorAll('select.paint-mode'))if(s.value!==v.mode)s.value=v.mode;
    if(id==='eraser'){const m=v.eraseMode,off={hardness:m!=='brush',flow:m!=='brush',size:m==='block',opacity:m==='block',pressureOpacity:m==='block',pressure:m==='block'};
      for(const f of box.querySelectorAll('.ed-field[data-key]'))if(f.dataset.key in off){f.classList.toggle('paint-off',off[f.dataset.key]);f.inert=off[f.dataset.key];}}}
  if(pop&&pop.tool===id){for(const [k,sync] of pop.syncs)sync(v[k]);pop.redraw&&pop.redraw();}}
Ed.on('options',({id})=>{if(id===Ed.tool)refreshWidgets();});
Ed.on('tool',()=>{closePop();requestAnimationFrame(refreshWidgets);});

// ====================================================================================================================
// EVENTS
// ====================================================================================================================
Ed.on('open',()=>{lastPaint=null;cloneSource=null;for(const id of TOOLS){const t=Ed.tools.get(id);if(t&&t.stroke)abort(t);}
  queueMicrotask(()=>{if(!('historySource' in Ed))snapshotOpen();else openSnapshot=null;});});   // after the History panel's module set its source
Ed.on('document',d=>{if(d&&d.reason==='restore'&&!('historySource' in Ed))snapshotOpen();});
{const v=document.getElementById('view');if(v)v.addEventListener('pointerleave',()=>{hover=null;if(TOOLS.has(Ed.tool))Ed.view.redrawOverlay();});}
// for tests: the clone source
Ed.paint={get source(){return cloneSource&&{x:cloneSource.x,y:cloneSource.y,offset:cloneSource.offset&&{...cloneSource.offset}};}};
})();
