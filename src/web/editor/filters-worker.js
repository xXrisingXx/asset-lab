'use strict';
/* =====================================================================================================================
   FILTER KERNELS  ·  editor/filters-worker.js
   The pixel work behind editor/filters.js (Photoshop's Filter menu). It runs in same-origin Web Workers (a big job is
   split into horizontal bands, one per worker) and, when workers are unavailable, in the page (window.EdFilterKernels).
   No DOM and no window.Ed here.

   MESSAGES  {type:'source', id, w, h, data (straight RGBA bytes of the whole layer or mask), mask, sel (selection alpha,
     one byte per pixel, or null)} keeps a picture for later jobs · {type:'drop', id} · {type:'run', job, source, filter,
     params, out {x, y, w, h} (the document pixels to compute), ctx {geo {x, y, w, h} (the selection bounds or the whole
     document: what Offset wraps in and what Twirl, Pinch, Spherize are centred on), fg, bg ([r, g, b]), seed, lock
     (lock transparency: keep alpha)}} -> {type:'progress', job, value 0..1}... then {type:'done', job, data (straight
     RGBA bytes of `out`), ms} or {type:'error', job, message}.
   DETERMINISM  Every random choice is a hash of document coordinates and the seed, so bands and previews agree.
   TRANSPARENCY  'premult' filters work on colours multiplied by alpha (blurs, sharpening, median, maximum, distortions,
     mosaic...): soft edges never pick up a dark fringe and alpha is filtered along with the colour, as Photoshop does.
     'color' filters change the colours and keep alpha (Add Noise, Emboss, Find Edges, Solarize, High Pass).
     A layer mask is one gray channel. Lock transparency keeps the old alpha with the new (un-premultiplied) colour.
   EDGES  Pixels beyond the document repeat the edge pixel (Photoshop's filters do not darken the border).
   ===================================================================================================================== */
(function(){
const G=typeof self!=='undefined'?self:this;
const DEF=Object.create(null),sources=new Map();
const PI=Math.PI,TAU=2*Math.PI;
const ci=(v,a,b)=>v<a?a:v>b?b:v;
const isWorker=typeof WorkerGlobalScope!=='undefined'&&G instanceof WorkerGlobalScope;

// ----- deterministic randomness ---------------------------------------------------------------------------------------
function hash(x,y,s){let h=Math.imul(x|0,0x27d4eb2d)+Math.imul(y|0,0x165667b1)+Math.imul(s|0,0x9e3779b9);
  h^=h>>>15;h=Math.imul(h,0x2c1b3c6d);h^=h>>>12;h=Math.imul(h,0x297a2d39);h^=h>>>15;return h>>>0;}
const rnd=(x,y,s)=>hash(x,y,s)*2.3283064365386963e-10;   // [0, 1)
function gaussRnd(x,y,s){const u=Math.max(1e-12,rnd(x,y,s)),v=rnd(x,y,s+0x51ed27);return Math.sqrt(-2*Math.log(u))*Math.cos(TAU*v);}

// ----- rectangles, loading and storing --------------------------------------------------------------------------------
function clip(R,W,H){const x=Math.max(0,Math.floor(R.x)),y=Math.max(0,Math.floor(R.y)),x2=Math.min(W,Math.ceil(R.x+R.w)),y2=Math.min(H,Math.ceil(R.y+R.h));
  return{x,y,w:Math.max(0,x2-x),h:Math.max(0,y2-y)};}
const grow=(R,k)=>({x:R.x-k,y:R.y-k,w:R.w+2*k,h:R.h+2*k});
const union=(a,b)=>{const x=Math.min(a.x,b.x),y=Math.min(a.y,b.y);return{x,y,w:Math.max(a.x+a.w,b.x+b.w)-x,h:Math.max(a.y+a.h,b.y+b.h)-y};};
// A float image of rectangle R: c = channels (premult: R*a, G*a, B*a, A · color: R, G, B with `a` = alpha · gray: the mask).
function load(src,R,mode){const n=R.w*R.h,S=src.data,W=src.w;let c,a=null;
  if(src.mask){const v=new Float32Array(n);for(let y=0,i=0;y<R.h;y++){let j=((R.y+y)*W+R.x)*4+3;for(let x=0;x<R.w;x++,i++,j+=4)v[i]=S[j];}c=[v];mode='gray';}
  else if(mode==='premult'){const r=new Float32Array(n),g=new Float32Array(n),b=new Float32Array(n),A=new Float32Array(n);
    for(let y=0,i=0;y<R.h;y++){let j=((R.y+y)*W+R.x)*4;for(let x=0;x<R.w;x++,i++,j+=4){const al=S[j+3],k=al/255;r[i]=S[j]*k;g[i]=S[j+1]*k;b[i]=S[j+2]*k;A[i]=al;}}c=[r,g,b,A];}
  else{mode='color';const r=new Float32Array(n),g=new Float32Array(n),b=new Float32Array(n);a=new Float32Array(n);
    for(let y=0,i=0;y<R.h;y++){let j=((R.y+y)*W+R.x)*4;for(let x=0;x<R.w;x++,i++,j+=4){r[i]=S[j];g[i]=S[j+1];b[i]=S[j+2];a[i]=S[j+3];}}c=[r,g,b];}
  return{x:R.x,y:R.y,w:R.w,h:R.h,c,a,mode};}
function store(src,O,ch,mode,lock){const n=O.w*O.h,out=new Uint8ClampedArray(n*4),S=src.data,W=src.w;
  if(mode==='gray'){const v=ch[0];for(let i=0;i<n;i++){const k=i*4;out[k]=out[k+1]=out[k+2]=255;out[k+3]=v[i];}return out;}
  const r=ch[0],g=ch[1],b=ch[2],A=ch[3];
  for(let y=0,i=0;y<O.h;y++){let j=((O.y+y)*W+O.x)*4;for(let x=0;x<O.w;x++,i++,j+=4){const k=i*4;
    if(mode==='color'){out[k]=r[i];out[k+1]=g[i];out[k+2]=b[i];out[k+3]=S[j+3];continue;}
    const al=A[i];
    if(al>1e-3){const f=255/al;out[k]=r[i]*f;out[k+1]=g[i]*f;out[k+2]=b[i]*f;}
    else if(lock){out[k]=S[j];out[k+1]=S[j+1];out[k+2]=S[j+2];}
    out[k+3]=lock?S[j+3]:al;}}
  return out;}
function crop(a,img,O){if(O.x===img.x&&O.y===img.y&&O.w===img.w&&O.h===img.h)return a;const out=new Float32Array(O.w*O.h);
  for(let y=0;y<O.h;y++){const s=(O.y-img.y+y)*img.w+(O.x-img.x);out.set(a.subarray(s,s+O.w),y*O.w);}return out;}
// Run fn(channel, index) -> a channel of img's size for every channel, keep O.
function each(img,O,ctx,fn){const n=img.c.length,res=new Array(n);for(let i=0;i<n;i++){res[i]=crop(fn(img.c[i],i),img,O);ctx.progress((i+1)/n);}return res;}
function fill(O,n,v){return Array.from({length:n},(_,k)=>new Float32Array(O.w*O.h).fill(Array.isArray(v)?v[k]:v));}

// ----- separable building blocks -----------------------------------------------------------------------------------
function kernelGauss(s){const r=Math.max(1,Math.ceil(3*s)),k=new Float32Array(2*r+1);let t=0;for(let i=-r;i<=r;i++){const v=Math.exp(-i*i/(2*s*s));k[i+r]=v;t+=v;}for(let i=0;i<k.length;i++)k[i]/=t;return k;}
function convRows(src,w,h,k){const r=(k.length-1)>>1,n=k.length,out=new Float32Array(w*h),line=new Float32Array(w+2*r);
  for(let y=0;y<h;y++){const o=y*w,f=src[o],e=src[o+w-1];for(let i=0;i<r;i++){line[i]=f;line[r+w+i]=e;}line.set(src.subarray(o,o+w),r);
    for(let x=0;x<w;x++){let s=0;for(let j=0;j<n;j++)s+=k[j]*line[x+j];out[o+x]=s;}}return out;}
function convCols(src,w,h,k){const r=(k.length-1)>>1,n=k.length,out=new Float32Array(w*h);
  for(let y=0;y<h;y++){const o=y*w;for(let j=0;j<n;j++){let yy=y+j-r;yy=yy<0?0:yy>=h?h-1:yy;const q=yy*w,kj=k[j];for(let x=0;x<w;x++)out[o+x]+=kj*src[q+x];}}return out;}
// A box of 2l+1 pixels plus a fractional weight a at both ends (extended box blur), edges repeated.
function boxRows(src,w,h,l,a,out){const pad=l+1,line=new Float32Array(w+2*pad),norm=1/(2*l+1+2*a);
  for(let y=0;y<h;y++){const o=y*w,f=src[o],e=src[o+w-1];for(let i=0;i<pad;i++){line[i]=f;line[pad+w+i]=e;}line.set(src.subarray(o,o+w),pad);
    let s=0;for(let i=pad-l;i<=pad+l;i++)s+=line[i];
    if(a)for(let x=0;x<w;x++){const c=pad+x;out[o+x]=(s+a*(line[c-l-1]+line[c+l+1]))*norm;s+=line[c+l+1]-line[c-l];}
    else for(let x=0;x<w;x++){const c=pad+x;out[o+x]=s*norm;s+=line[c+l+1]-line[c-l];}}
  return out;}
function boxCols(src,w,h,l,a,out){const norm=1/(2*l+1+2*a),S=new Float64Array(w),row=y=>(y<0?0:y>=h?h-1:y)*w;
  for(let i=-l;i<=l;i++){const r=row(i);for(let x=0;x<w;x++)S[x]+=src[r+x];}
  for(let y=0;y<h;y++){const o=y*w,rm=row(y-l-1),rp=row(y+l+1),rs=row(y-l);
    for(let x=0;x<w;x++){out[o+x]=(S[x]+a*(src[rm+x]+src[rp+x]))*norm;S[x]+=src[rp+x]-src[rs+x];}}
  return out;}
// Gaussian blur with standard deviation s (Photoshop's Radius): the exact kernel (3 sigma) up to 5 px, five extended box
// passes above (Gwosdek et al.; within 1.7 levels of the exact blur on a hard black-to-white edge).
const EXACT=5;
function boxParams(s,d){const s2=s*s/d,L=Math.sqrt(12*s2+1),l=Math.floor((L-1)/2),a=(2*l+1)*(l*(l+1)-3*s2)/(6*(s2-(l+1)*(l+1)));return{l,a};}
// d extended box passes along every row. Each row is padded once with its edge pixels, far enough that the passes give
// exactly what they would give on an endlessly repeated edge (each pass only spoils l + 1 more pixels of the padding).
function boxPassesRows(src,w,h,l,a,d){const P=d*(l+1)+1,n=w+2*P,norm=1/(2*l+1+2*a),out=new Float32Array(w*h);let X=new Float64Array(n),Y=new Float64Array(n);
  for(let y=0;y<h;y++){const o=y*w,f=src[o],e=src[o+w-1];for(let i=0;i<P;i++){X[i]=f;X[P+w+i]=e;}for(let x=0;x<w;x++)X[P+x]=src[o+x];
    for(let p=0;p<d;p++){let s=0;for(let i=1;i<=2*l+1;i++)s+=X[i];
      for(let c=l+1;c<n-l-1;c++){Y[c]=(s+a*(X[c-l-1]+X[c+l+1]))*norm;s+=X[c+l+1]-X[c-l];}
      const t=X;X=Y;Y=t;}
    for(let x=0;x<w;x++)out[o+x]=X[P+x];}
  return out;}
function gauss(ch,w,h,s){if(!(s>.02))return Float32Array.from(ch);
  if(s<=EXACT){const k=kernelGauss(s);return convCols(convRows(ch,w,h,k),w,h,k);}
  const d=5,{l,a}=boxParams(s,d),R=boxPassesRows(ch,w,h,l,a,d);
  return transpose(boxPassesRows(transpose(R,w,h),h,w,l,a,d),h,w);}
const gaussPad=s=>s<=EXACT?Math.max(1,Math.ceil(3*s))+1:Math.ceil(s*Math.sqrt(15))+8;
function box(ch,w,h,r){const out=new Float32Array(w*h);boxRows(ch,w,h,r,0,out);const t=new Float32Array(w*h);boxCols(out,w,h,r,0,t);return t;}
function transpose(a,w,h){const t=new Float32Array(w*h);for(let y=0;y<h;y++){const o=y*w;for(let x=0;x<w;x++)t[x*h+y]=a[o+x];}return t;}
// Inverse mapping for distortions: map(docX, docY, pt) writes the source point (document coordinates; pixel centres are
// at x + .5) into pt, or returns false to leave the pixel as it is. Bilinear sampling, edges repeated.
function remap(img,O,ctx,map){const n=img.c.length,w=img.w,h=img.h,res=img.c.map(()=>new Float32Array(O.w*O.h)),pt=[0,0],C=img.c;
  for(let y=0;y<O.h;y++){for(let x=0;x<O.w;x++){const i=y*O.w+x;pt[0]=O.x+x+.5;pt[1]=O.y+y+.5;map(pt[0],pt[1],pt);
      const u=pt[0]-.5-img.x,v=pt[1]-.5-img.y;let x0=Math.floor(u),y0=Math.floor(v);const fx=u-x0,fy=v-y0;let x1=x0+1,y1=y0+1;
      const w00=(1-fx)*(1-fy),w10=fx*(1-fy),w01=(1-fx)*fy,w11=fx*fy;
      x0=ci(x0,0,w-1);x1=ci(x1,0,w-1);y0=ci(y0,0,h-1);y1=ci(y1,0,h-1);
      const a=y0*w+x0,b=y0*w+x1,c=y1*w+x0,d=y1*w+x1;
      for(let k=0;k<n;k++){const ch=C[k];res[k][i]=ch[a]*w00+ch[b]*w10+ch[c]*w01+ch[d]*w11;}}
    if((y&31)===31)ctx.progress(y/O.h);}
  return res;}

// ----- rank filters (median, maximum, minimum) ------------------------------------------------------------------------
// Median of a (2r+1)^2 square per channel (Huang's sliding histogram); values are rounded to whole levels for the histogram.
function median(c,img,O,r){const w=img.w,h=img.h,ox=O.x-img.x,oy=O.y-img.y,out=new Float32Array(O.w*O.h),q=new Uint8Array(w*h);
  for(let i=0;i<q.length;i++){const v=c[i];q[i]=v<=0?0:v>=255?255:(v+.5)|0;}
  const H=new Int32Array(256),half=((2*r+1)*(2*r+1)+1)>>1,cx=x=>x<0?0:x>=w?w-1:x,rows=new Int32Array(2*r+1);
  for(let y=0;y<O.h;y++){H.fill(0);for(let d=-r;d<=r;d++){const yy=oy+y+d;rows[d+r]=(yy<0?0:yy>=h?h-1:yy)*w;}
    for(let d=0;d<rows.length;d++){const rr=rows[d];for(let dx=-r;dx<=r;dx++)H[q[rr+cx(ox+dx)]]++;}
    let m=0,lt=0;while(lt+H[m]<half){lt+=H[m];m++;}
    for(let x=0;x<O.w;x++){out[y*O.w+x]=m;if(x+1===O.w)break;const xl=cx(ox+x-r),xr=cx(ox+x+r+1);
      for(let d=0;d<rows.length;d++){const rr=rows[d],a=q[rr+xl],b=q[rr+xr];H[a]--;if(a<m)lt--;H[b]++;if(b<m)lt++;}
      while(lt>=half){m--;lt-=H[m];}while(lt+H[m]<half){lt+=H[m];m++;}}}
  return out;}
// Running max/min of every row over a window of 2r+1 (van Herk / Gil-Werman), edges repeated.
function rankRows(src,w,h,r,max,out){if(r<=0){out.set(src);return out;}const k=2*r+1,n=w+2*r,line=new Float32Array(n),g=new Float32Array(n),hh=new Float32Array(n);
  for(let y=0;y<h;y++){const o=y*w;const f=src[o],e=src[o+w-1];for(let i=0;i<r;i++){line[i]=f;line[r+w+i]=e;}line.set(src.subarray(o,o+w),r);
    if(max){for(let i=0;i<n;i++){const v=line[i];g[i]=i%k===0?v:(g[i-1]>v?g[i-1]:v);}for(let i=n-1;i>=0;i--){const v=line[i];hh[i]=(i===n-1||(i+1)%k===0)?v:(hh[i+1]>v?hh[i+1]:v);}
      for(let x=0;x<w;x++){const a=hh[x],b=g[x+k-1];out[o+x]=a>b?a:b;}}
    else{for(let i=0;i<n;i++){const v=line[i];g[i]=i%k===0?v:(g[i-1]<v?g[i-1]:v);}for(let i=n-1;i>=0;i--){const v=line[i];hh[i]=(i===n-1||(i+1)%k===0)?v:(hh[i+1]<v?hh[i+1]:v);}
      for(let x=0;x<w;x++){const a=hh[x],b=g[x+k-1];out[o+x]=a<b?a:b;}}}
  return out;}
function rankSquare(c,w,h,r,max){const a=rankRows(c,w,h,r,max,new Float32Array(w*h));const t=transpose(a,w,h);const b=rankRows(t,h,w,r,max,new Float32Array(w*h));return transpose(b,h,w);}
// A round neighbourhood (dx^2 + dy^2 <= r^2): rows of the half-widths of the disk combined.
function rankDisk(c,w,h,r,max,ctx){const out=new Float32Array(w*h).fill(max?-Infinity:Infinity),H=new Float32Array(w*h);
  for(let d=0;d<=r;d++){const wd=Math.floor(Math.sqrt(r*r-d*d)+1e-9);rankRows(c,w,h,wd,max,H);
    for(let y=0;y<h;y++){const o=y*w,a=Math.max(0,y-d)*w,b=Math.min(h-1,y+d)*w;
      if(max)for(let x=0;x<w;x++){let v=H[a+x];const u=H[b+x];if(u>v)v=u;if(v>out[o+x])out[o+x]=v;}
      else for(let x=0;x<w;x++){let v=H[a+x];const u=H[b+x];if(u<v)v=u;if(v<out[o+x])out[o+x]=v;}}
    if(ctx&&(d&7)===7)ctx.progress(d/(r+1));}
  return out;}

// ----- noise for Clouds: tileable gradient noise (the period is the document size) -------------------------------------
function gradTable(px,py,seed){const gx=new Float32Array(px*py),gy=new Float32Array(px*py);for(let j=0;j<py;j++)for(let i=0;i<px;i++){const a=rnd(i,j,seed)*TAU;gx[j*px+i]=Math.cos(a);gy[j*px+i]=Math.sin(a);}return{px,py,gx,gy};}
const fade=t=>t*t*t*(t*(t*6-15)+10);
function perlin(T,x,y){const xi=Math.floor(x),yi=Math.floor(y),fx=x-xi,fy=y-yi,px=T.px,py=T.py;
  const i0=((xi%px)+px)%px,j0=((yi%py)+py)%py,i1=(i0+1)%px,j1=(j0+1)%py;
  const d=(i,j,dx,dy)=>T.gx[j*px+i]*dx+T.gy[j*px+i]*dy;
  const n00=d(i0,j0,fx,fy),n10=d(i1,j0,fx-1,fy),n01=d(i0,j1,fx,fy-1),n11=d(i1,j1,fx-1,fy-1),u=fade(fx),v=fade(fy);
  return n00+(n10-n00)*u+(n01-n00)*v+(n00-n10-n01+n11)*u*v;}
// Fractal clouds in 0..1 for document pixels: about 256 px between the biggest puffs, finer octaves down to 2 px. Every
// octave has a whole number of cells across the document, so the pattern tiles seamlessly.
function cloudField(O,W,H,seed){const nx=Math.max(1,Math.round(W/256)),ny=Math.max(1,Math.round(H/256)),oct=[];
  for(let o=0;o<12;o++){const px=nx<<o,py=ny<<o;if(W/px<1.5&&H/py<1.5)break;oct.push({T:gradTable(px,py,seed+o*7919),px,py,amp:Math.pow(.5,o)});}
  const out=new Float32Array(O.w*O.h);let norm=0;for(const o of oct)norm+=o.amp;norm=1/Math.sqrt(norm*.6);
  for(let y=0;y<O.h;y++)for(let x=0;x<O.w;x++){const X=(O.x+x+.5)/W,Y=(O.y+y+.5)/H;let s=0;for(const o of oct)s+=o.amp*perlin(o.T,X*o.px,Y*o.py);
    out[y*O.w+x]=ci(.5+s*norm*.62,0,1);}
  return out;}

// ----- the filters ----------------------------------------------------------------------------------------------------
// def(id, {mode: 'premult' (default) | 'color', pad(params, ctx) -> px (null: the whole geometry rectangle),
//   input(O, params, ctx) -> rect, run(img, O, params, ctx) -> channels of O's size})
const def=(id,o)=>{DEF[id]=o;};

// Blur
// Average: the selection's (or the layer's) average, weighted by the soft selection; premultiplied, so transparent pixels
// add no colour.
def('average',{pad:null,run(img,O,p,ctx){const G=ctx.geo,sel=ctx.src.sel,W=ctx.W,n=img.c.length,s=new Float64Array(n);let t=0;
  for(let y=G.y;y<G.y+G.h;y++)for(let x=G.x;x<G.x+G.w;x++){const wgt=sel?sel[y*W+x]/255:1;if(!wgt)continue;const i=(y-img.y)*img.w+(x-img.x);t+=wgt;for(let k=0;k<n;k++)s[k]+=wgt*img.c[k][i];}
  return fill(O,n,Array.from(s,v=>t?v/t:0));}});
// Blur and Blur More: binomial kernels [1 2 1]/4 and [1 6 15 20 15 6 1]/64 in both directions.
const B3=Float32Array.of(.25,.5,.25),B7=Float32Array.from([1,6,15,20,15,6,1],v=>v/64);
const sep=(c,w,h,k)=>convCols(convRows(c,w,h,k),w,h,k);
def('blur',{pad:()=>2,run:(img,O,p,ctx)=>each(img,O,ctx,c=>sep(c,img.w,img.h,B3))});
def('blur-more',{pad:()=>4,run:(img,O,p,ctx)=>each(img,O,ctx,c=>sep(c,img.w,img.h,B7))});
def('box-blur',{pad:p=>Math.round(p.radius)+2,run:(img,O,p,ctx)=>each(img,O,ctx,c=>box(c,img.w,img.h,Math.max(0,Math.round(p.radius))))});
def('gaussian-blur',{pad:p=>gaussPad(p.radius),run:(img,O,p,ctx)=>each(img,O,ctx,c=>gauss(c,img.w,img.h,p.radius))});
// Motion Blur: a box of `distance` pixels along `angle`, centred. The rows are sheared so the line becomes a row, boxed with
// a running sum, and sheared back (one linear interpolation each way).
function shearBlur(c,w,h,t,hx){const lo=Math.min(0,-(w-1)*t),hi=Math.max(0,-(w-1)*t),j0=Math.floor(lo)-1,j1=Math.ceil(h-1+hi)+1,rows=j1-j0+1,S=new Float32Array(rows*w);
  for(let J=0;J<rows;J++){const j=J+j0,o=J*w;for(let x=0;x<w;x++){const yy=j+x*t;if(yy<=0){S[o+x]=c[x];continue;}if(yy>=h-1){S[o+x]=c[(h-1)*w+x];continue;}const y0=yy|0,f=yy-y0,a=c[y0*w+x];S[o+x]=a+(c[(y0+1)*w+x]-a)*f;}}
  const n=Math.floor(hx),B=boxRows(S,w,rows,n,hx-n,new Float32Array(rows*w)),out=new Float32Array(w*h);
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){const J=y-x*t-j0,J0=Math.floor(J),f=J-J0,a=B[J0*w+x];out[y*w+x]=f?a+(B[(J0+1)*w+x]-a)*f:a;}
  return out;}
// Along the main axis the box is `distance` x |cos| pixels wide: whole taps plus a fraction at both ends.
function motion(c,w,h,ang,dist){const th=ang*PI/180,vx=Math.cos(th),vy=-Math.sin(th),hw=L=>Math.max(0,(dist*L-1)/2);
  if(Math.abs(vx)>=Math.abs(vy))return shearBlur(c,w,h,vy/vx,hw(Math.abs(vx)));
  return transpose(shearBlur(transpose(c,w,h),h,w,vx/vy,hw(Math.abs(vy))),h,w);}
def('motion-blur',{pad:p=>Math.ceil(p.distance/2)+3,run:(img,O,p,ctx)=>each(img,O,ctx,c=>motion(c,img.w,img.h,p.angle,p.distance))});

// Sharpen: c + k (c - blur(c)); Sharpen and Sharpen More use the 3 x 3 binomial blur, Unsharp Mask a Gaussian of Radius
// and changes only where |c - blur| reaches Threshold.
function usm(c,w,h,s,amount,T){const b=gauss(c,w,h,s),out=new Float32Array(w*h),t=T-.5;for(let i=0;i<out.length;i++){const v=c[i],d=v-b[i];out[i]=(d>t||-d>t)?v+amount*d:v;}return out;}
const lift=k=>(img,O,p,ctx)=>each(img,O,ctx,c=>{const b=sep(c,img.w,img.h,B3),o=new Float32Array(c.length);for(let i=0;i<c.length;i++)o[i]=c[i]+k*(c[i]-b[i]);return o;});
def('unsharp-mask',{pad:p=>gaussPad(p.radius),run:(img,O,p,ctx)=>each(img,O,ctx,c=>usm(c,img.w,img.h,p.radius,p.amount/100,p.threshold))});
def('sharpen',{pad:()=>2,run:lift(.6)});
def('sharpen-more',{pad:()=>2,run:lift(1.8)});
def('sharpen-edges',{pad:()=>gaussPad(1),run:(img,O,p,ctx)=>each(img,O,ctx,c=>usm(c,img.w,img.h,1,1.2,10))});

// Noise
// Add Noise: uniform noise in [-A, A] or Gaussian noise of standard deviation 0.6 A, A = Amount% of 128 levels, added to
// the colours (alpha kept); Monochromatic adds the same value to red, green and blue.
def('add-noise',{mode:'color',pad:()=>0,run(img,O,p,ctx){const A=p.amount/100*128,gaussian=p.distribution==='gaussian',mono=!!p.mono,n=img.c.length,s=ctx.seed|0;
  const out=img.c.map(c=>crop(c,img,O).slice());
  for(let y=0;y<O.h;y++)for(let x=0;x<O.w;x++){const X=O.x+x,Y=O.y+y,i=y*O.w+x;
    for(let k=0;k<n;k++){const seed=mono?s:s+k*1013;const g=gaussian?gaussRnd(X,Y,seed)*A*.6:(rnd(X,Y,seed)*2-1)*A;out[k][i]+=g;}}
  return out;}});
def('median',{pad:p=>Math.round(p.radius)+1,run:(img,O,p,ctx)=>{const r=Math.max(1,Math.round(p.radius));const res=[];for(let k=0;k<img.c.length;k++){res.push(median(img.c[k],img,O,r));ctx.progress((k+1)/img.c.length);}return res;}});
def('dust-scratches',{pad:p=>Math.round(p.radius)+1,run(img,O,p,ctx){const r=Math.max(1,Math.round(p.radius)),T=p.threshold;return img.c.map((c,k)=>{const m=median(c,img,O,r),o=crop(c,img,O);
  const out=new Float32Array(O.w*O.h);for(let i=0;i<out.length;i++){const v=o[i],d=Math.abs(v-m[i]);out[i]=d>T?m[i]:v;}ctx.progress((k+1)/img.c.length);return out;});}});

// Stylize
def('solarize',{mode:'color',pad:()=>0,run:(img,O,p,ctx)=>each(img,O,ctx,c=>c.map(v=>v>127.5?255-v:v))});
// Emboss: 128 + amount * (I(p - h/2 v) - I(p + h/2 v)) per channel, v = the light's direction (Angle, counter-clockwise from
// 3 o'clock), sampled bilinearly. A full black-to-white edge reaches white and black at Amount 100%.
function bil(c,w,h,u,v){let x0=Math.floor(u),y0=Math.floor(v);const fx=u-x0,fy=v-y0;let x1=x0+1,y1=y0+1;
  x0=ci(x0,0,w-1);x1=ci(x1,0,w-1);y0=ci(y0,0,h-1);y1=ci(y1,0,h-1);
  const a=c[y0*w+x0],b=c[y0*w+x1],d=c[y1*w+x0],e=c[y1*w+x1];return a+(b-a)*fx+(d-a)*fy+(a-b-d+e)*fx*fy;}
def('emboss',{mode:'color',pad:p=>Math.ceil(p.height/2)+2,run(img,O,p,ctx){const th=p.angle*PI/180,h2=p.height/2,vx=Math.cos(th)*h2,vy=-Math.sin(th)*h2,k=p.amount/100,w=img.w,h=img.h;
  return img.c.map(c=>{const out=new Float32Array(O.w*O.h);for(let y=0;y<O.h;y++)for(let x=0;x<O.w;x++){const u=O.x-img.x+x,v=O.y-img.y+y;out[y*O.w+x]=128+k*(bil(c,w,h,u-vx,v-vy)-bil(c,w,h,u+vx,v+vy));}return out;});}});
// Find Edges: white where the picture is flat, the edge colour (per channel: 255 - Sobel magnitude / 2) along edges.
function sobel(c,w,h,x,y){const X0=x>0?x-1:0,X1=x<w-1?x+1:w-1,Y0=y>0?y-1:0,Y1=y<h-1?y+1:h-1;
  const a=c[Y0*w+X0],b=c[Y0*w+x],d=c[Y0*w+X1],e=c[y*w+X0],f=c[y*w+X1],g=c[Y1*w+X0],hh=c[Y1*w+x],i=c[Y1*w+X1];
  const gx=(d+2*f+i)-(a+2*e+g),gy=(g+2*hh+i)-(a+2*b+d);return Math.sqrt(gx*gx+gy*gy);}
def('find-edges',{mode:'color',pad:()=>2,run(img,O){const w=img.w,h=img.h;return img.c.map(c=>{const out=new Float32Array(O.w*O.h);
  for(let y=0;y<O.h;y++)for(let x=0;x<O.w;x++)out[y*O.w+x]=255-Math.min(255,sobel(c,w,h,O.x-img.x+x,O.y-img.y+y)/2);return out;});}});

// Pixelate
// Mosaic: squares of Cell Size from the document's top-left corner, each the premultiplied average of its pixels.
def('mosaic',{input:(O,p)=>{const s=Math.max(1,Math.round(p.size)),x=Math.floor(O.x/s)*s,y=Math.floor(O.y/s)*s;return{x,y,w:Math.ceil((O.x+O.w)/s)*s-x,h:Math.ceil((O.y+O.h)/s)*s-y};},
  run(img,O,p){const s=Math.max(1,Math.round(p.size)),n=img.c.length,res=img.c.map(()=>new Float32Array(O.w*O.h));
    for(let cy=Math.floor(O.y/s);cy<=Math.floor((O.y+O.h-1)/s);cy++)for(let cx=Math.floor(O.x/s);cx<=Math.floor((O.x+O.w-1)/s);cx++){
      const X0=Math.max(cx*s,img.x),X1=Math.min((cx+1)*s,img.x+img.w),Y0=Math.max(cy*s,img.y),Y1=Math.min((cy+1)*s,img.y+img.h),cnt=(X1-X0)*(Y1-Y0);if(cnt<=0)continue;
      const a0=Math.max(cx*s,O.x),a1=Math.min((cx+1)*s,O.x+O.w),b0=Math.max(cy*s,O.y),b1=Math.min((cy+1)*s,O.y+O.h);
      for(let k=0;k<n;k++){const c=img.c[k];let t=0;for(let y=Y0;y<Y1;y++){const o=(y-img.y)*img.w-img.x;for(let x=X0;x<X1;x++)t+=c[o+x];}const m=t/cnt,r=res[k];
        for(let y=b0;y<b1;y++){const o=(y-O.y)*O.w-O.x;for(let x=a0;x<a1;x++)r[o+x]=m;}}}
    return res;}});

// Distort (all centred on the selection's bounds, or the whole layer)
const ellipse=G=>({cx:G.x+G.w/2,cy:G.y+G.h/2,rx:G.w/2,ry:G.h/2});
// Twirl: rotation by Angle * (1 - r/R)^2 inside the circle that fits the area (positive angles turn clockwise).
def('twirl',{pad:null,run(img,O,p,ctx){const G=ctx.geo,cx=G.x+G.w/2,cy=G.y+G.h/2,R=Math.min(G.w,G.h)/2,A=p.angle*PI/180;
  return remap(img,O,ctx,(X,Y,pt)=>{const dx=X-cx,dy=Y-cy,r=Math.hypot(dx,dy);if(r>=R)return;const t=1-r/R,a=-A*t*t,c=Math.cos(a),s=Math.sin(a);pt[0]=cx+dx*c-dy*s;pt[1]=cy+dx*s+dy*c;});}});
// Pinch: inside the ellipse that fits the area, a point at normalized radius r samples r * g (amount > 0: the middle
// shrinks) or r / g (amount < 0: it swells), g = 1 + 2 |amount| (1 - r)^2. Smooth and one-to-one: at 100% the middle is
// three times smaller (or larger) and the rim does not move.
def('pinch',{pad:null,run(img,O,p,ctx){const E=ellipse(ctx.geo),a=p.amount/100,k=2*Math.abs(a);
  return remap(img,O,ctx,(X,Y,pt)=>{const dx=(X-E.cx)/E.rx,dy=(Y-E.cy)/E.ry,r=Math.hypot(dx,dy);if(r>=1)return;const g=1+k*(1-r)*(1-r),f=a>=0?g:1/g;pt[0]=E.cx+dx*f*E.rx;pt[1]=E.cy+dy*f*E.ry;});}});
// Spherize: inside the ellipse, normalized radius r samples r + (asin(r) 2/pi - r) k (a bulge) for k > 0, or
// r + (sin(r pi/2) - r) |k| (a bowl) for k < 0; Horizontal / Vertical only bend one axis.
def('spherize',{pad:null,run(img,O,p,ctx){const E=ellipse(ctx.geo),k=p.amount/100,mode=p.mode||'normal';
  const m=r=>k>=0?r+(Math.asin(r)*2/PI-r)*k:r+(Math.sin(r*PI/2)-r)*-k;
  return remap(img,O,ctx,(X,Y,pt)=>{let dx=(X-E.cx)/E.rx,dy=(Y-E.cy)/E.ry;
    if(mode==='horizontal'){if(Math.abs(dx)>=1||Math.abs(dy)>=1)return;dx=Math.sign(dx)*m(Math.abs(dx));}
    else if(mode==='vertical'){if(Math.abs(dy)>=1||Math.abs(dx)>=1)return;dy=Math.sign(dy)*m(Math.abs(dy));}
    else{const r=Math.hypot(dx,dy);if(r>=1||r<1e-9)return;const f=m(r)/r;dx*=f;dy*=f;}
    pt[0]=E.cx+dx*E.rx;pt[1]=E.cy+dy*E.ry;});}});
// Ripple: every pixel samples x + A sin(2 pi y / L), y + A sin(2 pi x / L) (plus a slower wave at 2.7 L), with L and the
// height B from Size and A = B * Amount / 100.
const RIPPLE={small:[7,1.2],medium:[13,2.2],large:[26,4.4]};
def('ripple',{pad:null,run(img,O,p,ctx){const [L,B]=RIPPLE[p.size]||RIPPLE.medium,A=B*p.amount/100;
  const wob=t=>Math.sin(TAU*t/L)+.35*Math.sin(TAU*t/(L*2.7));
  return remap(img,O,ctx,(X,Y,pt)=>{pt[0]=X+A*wob(Y);pt[1]=Y+A*wob(X);});}});

// Render
// Clouds: the cloud field between the foreground (0) and background (1) colours, opaque. Difference Clouds: |picture -
// clouds| per channel.
const gray=c=>.299*c[0]+.587*c[1]+.114*c[2];
function blendColors(n,f,a,b,i,res){if(n===1){res[0][i]=gray(a)+(gray(b)-gray(a))*f;return;}for(let k=0;k<3;k++)res[k][i]=a[k]+(b[k]-a[k])*f;if(n===4)res[3][i]=255;}
def('clouds',{pad:()=>0,run(img,O,p,ctx){const f=cloudField(O,ctx.W,ctx.H,ctx.seed|0),n=img.c.length,res=img.c.map(()=>new Float32Array(O.w*O.h));
  for(let i=0;i<f.length;i++)blendColors(n,f[i],ctx.fg,ctx.bg,i,res);return res;}});
def('difference-clouds',{pad:()=>0,run(img,O,p,ctx){const f=cloudField(O,ctx.W,ctx.H,ctx.seed|0),n=img.c.length,res=img.c.map(()=>new Float32Array(O.w*O.h)),src=img.c.map(c=>crop(c,img,O));
  for(let i=0;i<f.length;i++){blendColors(n,f[i],ctx.fg,ctx.bg,i,res);if(n===1)res[0][i]=Math.abs(src[0][i]-res[0][i]);else for(let k=0;k<3;k++)res[k][i]=Math.abs(src[k][i]-res[k][i]);}return res;}});

// Other
// High Pass: 128 + the picture minus its Gaussian blur of `radius` (transparent pixels do not darken the blur).
def('high-pass',{mode:'color',pad:p=>gaussPad(p.radius),run(img,O,p,ctx){const w=img.w,h=img.h,a=img.a,s=p.radius;
  const den=a?gauss(a,w,h,s):null;return img.c.map((c,k)=>{let b;if(a){const num=gauss(c.map((v,i)=>v*a[i]),w,h,s);b=num.map((v,i)=>den[i]>1e-6?v/den[i]:c[i]);}else b=gauss(c,w,h,s);
    const o=new Float32Array(w*h);for(let i=0;i<o.length;i++)o[i]=c[i]-b[i]+128;ctx.progress((k+1)/img.c.length);return crop(o,img,O);});}});
// Maximum / Minimum: the largest / smallest premultiplied value in a square (Squareness) or a disk (Roundness) of Radius.
const rankPad=p=>Math.round(p.radius)+1;
const rank=max=>(img,O,p,ctx)=>each(img,O,ctx,c=>{const r=Math.max(0,Math.round(p.radius));return p.preserve==='roundness'?rankDisk(c,img.w,img.h,r,max,ctx):rankSquare(c,img.w,img.h,r,max);});
def('maximum',{pad:rankPad,run:rank(true)});
def('minimum',{pad:rankPad,run:rank(false)});
// Offset: the area moves right and down; what comes in on the other side wraps around, repeats the edge or is transparent.
def('offset',{pad:null,run(img,O,p,ctx){const G=ctx.geo,dx=Math.round(p.horizontal||0),dy=Math.round(p.vertical||0),mode=p.undefined||'wrap',w=img.w;
  return img.c.map(c=>{const out=new Float32Array(O.w*O.h);for(let y=0;y<O.h;y++){let sy=O.y+y-dy,oky=true;
      if(mode==='wrap')sy=G.y+((sy-G.y)%G.h+G.h)%G.h;else if(mode==='repeat')sy=ci(sy,G.y,G.y+G.h-1);else oky=sy>=G.y&&sy<G.y+G.h;
      for(let x=0;x<O.w;x++){let sx=O.x+x-dx,ok=oky;if(mode==='wrap')sx=G.x+((sx-G.x)%G.w+G.w)%G.w;else if(mode==='repeat')sx=ci(sx,G.x,G.x+G.w-1);else ok=ok&&sx>=G.x&&sx<G.x+G.w;
        out[y*O.w+x]=ok?c[(sy-img.y)*w+(sx-img.x)]:0;}}return out;});}});

// ----- running jobs ---------------------------------------------------------------------------------------------------
let jobId=0,lastPost=0;
function progress(f){if(!isWorker)return;const t=Date.now();if(t-lastPost<90)return;lastPost=t;G.postMessage({type:'progress',job:jobId,value:Math.max(0,Math.min(1,f))});}
function run(m){const src=sources.get(m.source);if(!src)throw Error('the picture for this filter is no longer loaded');
  const d=DEF[m.filter];if(!d)throw Error('unknown filter '+m.filter);
  const O=m.out,p=m.params||{},W=src.w,H=src.h,ctx=Object.assign({},m.ctx||{},{W,H,src,progress});
  ctx.geo=clip(ctx.geo||{x:0,y:0,w:W,h:H},W,H);if(!ctx.geo.w||!ctx.geo.h)ctx.geo={x:0,y:0,w:W,h:H};
  ctx.fg=ctx.fg||[0,0,0];ctx.bg=ctx.bg||[255,255,255];if(src.mask){const f=gray(ctx.fg),b=gray(ctx.bg);ctx.fg=[f,f,f];ctx.bg=[b,b,b];}
  let I=d.input?d.input(O,p,ctx):d.pad==null?union(ctx.geo,O):grow(O,Math.ceil(d.pad(p,ctx)));I=clip(union(I,O),W,H);
  const img=load(src,I,d.mode||'premult');
  const ch=d.run(img,O,p,ctx);
  return store(src,O,ch,img.mode,!!ctx.lock);}
if(isWorker)G.onmessage=e=>{const m=e.data;
  if(m.type==='source'){sources.set(m.id,{w:m.w,h:m.h,data:m.data,mask:!!m.mask,sel:m.sel||null});return;}
  if(m.type==='drop'){if(m.id==null)sources.clear();else sources.delete(m.id);return;}
  if(m.type==='run'){jobId=m.job;lastPost=Date.now();const t0=performance.now();
    try{const out=run(m);G.postMessage({type:'done',job:m.job,data:out.buffer,ms:performance.now()-t0},[out.buffer]);}
    catch(err){G.postMessage({type:'error',job:m.job,message:String(err&&err.message||err)});}}};
else G.EdFilterKernels={sources,run,DEF};
})();
