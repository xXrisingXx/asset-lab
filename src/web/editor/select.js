'use strict';
/* =====================================================================================================================
   SELECTION TOOLS  ·  editor/select.js  (a feature module built on window.Ed; the engine API is in editor/core.js)
   Photoshop's selection tools and Select menu, with Photoshop's names, keys, defaults and history labels.
   (Full behaviour notes are at the end of this header; the tests are tests/browser/test_select*.py.)

   TOOLS (strip slot · key; Shift+key cycles the slot's tools)
     Marquee M: Rectangular Marquee, Elliptical Marquee (M / Shift+M), Single Row Marquee, Single Column Marquee
       Options: mode, Feather, Anti-alias (ellipse), Style Normal / Fixed Ratio / Fixed Size (rectangle and ellipse).
       Shift = square / circle, Alt = from the centre, Space (held while dragging) moves the box being drawn.
     Lasso   L: Lasso (freehand; Alt + release switches to straight segments), Polygonal Lasso (click corners; double-click,
       Enter, Ctrl+click or a click on the start point closes; Shift = 45 degree steps; Backspace removes a corner)
     Wand    W: Magic Wand (Sample Size, Tolerance, Anti-alias, Contiguous, Sample All Layers)
     Dragging inside the selection (New mode, no modifier) moves the outline; a click deselects; arrow keys nudge it
     1 px (Shift: 10 px). Escape cancels the shape being drawn.
   SELECT MENU  Modify > Border / Smooth / Expand / Contract / Feather (Shift+F6), Grow, Similar.
     (All, Deselect, Reselect and Inverse come from the core.)

   MODES  The options bar has New / Add / Subtract / Intersect. At the start of a drag Shift adds, Alt subtracts and
     Shift+Alt intersects; with no selection those keys are drag modifiers instead (Shift = square/circle, Alt = from the
     centre). A held mode key only becomes a drag modifier after it is released and pressed again, as in Photoshop.
   MASKS  Selections are worked out exactly on the CPU (Uint8Array, alpha 0..255) and handed to Ed.select.set as one
     history state named after the tool ("Rectangular Marquee", "Magic Wand", "Move Selection", "Feather"...).
     Feather = Gaussian blur with sigma = radius. Expand / Contract = Euclidean distance from the 50% edge (round
     corners). Magic Wand Tolerance = every premultiplied channel (R, G, B, alpha) within Tolerance levels of the sample;
     contiguous regions are 4-connected.
   ===================================================================================================================== */
(()=>{
const Ed=window.Ed;if(!Ed||typeof Ed.registerTool!=='function')return;
const U=Ed.util,clamp=U.clamp;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const num=(v,d)=>{const n=Number(v);return isFinite(n)?n:d;};
const DW=()=>Ed.doc.width,DH=()=>Ed.doc.height;
const redraw=()=>Ed.view.redrawOverlay();
const report=(e,where)=>{try{Ed.test.errors.push({where:'select '+where,message:String(e&&e.message||e),stack:String(e&&e.stack||'')});}catch(x){}console.error('[select] '+where+':',e);};

// ----- masks: Uint8Array (W*H, 0..255) <-> canvases (mask in alpha, RGB white) --------------------------------------------
function bboxOf(m,W,H){let x0=W,y0=H,x1=-1,y1=-1;
  for(let y=0;y<H;y++){const row=y*W;let a=-1;for(let x=0;x<W;x++)if(m[row+x]){a=x;break;}if(a<0)continue;
    if(a<x0)x0=a;for(let x=W-1;x>=a;x--)if(m[row+x]){if(x>x1)x1=x;break;}if(y<y0)y0=y;y1=y;}
  return x1<0?null:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};}
function toCanvas(m,R){const W=DW(),H=DH(),c=U.canvas(W,H);if(R===undefined)R=bboxOf(m,W,H);if(!R)return c;
  const D=new ImageData(R.w,R.h),d=new Uint32Array(D.data.buffer);
  for(let y=0;y<R.h;y++){let i=(R.y+y)*W+R.x,j=y*R.w;for(let x=0;x<R.w;x++,i++,j++){const a=m[i];if(a)d[j]=a<<24|0xffffff;}}
  U.write(c,D,R);return c;}
function readAlpha(c,R){const W=DW(),H=DH(),m=new Uint8Array(W*H);if(!c)return m;R=R||{x:0,y:0,w:W,h:H};if(R.w<=0||R.h<=0)return m;
  const d=U.read(c,R).data;for(let y=0;y<R.h;y++){let i=(R.y+y)*W+R.x,j=y*R.w*4+3;for(let x=0;x<R.w;x++,i++,j+=4)m[i]=d[j];}return m;}
function selectionMask(){const s=Ed.selection;if(!s)return null;return readAlpha(s,Ed.select.bounds()||undefined);}
// The selection under the pointer, read once per selection (never per frame).
let selCache=null;
function selAt(p){const s=Ed.selection;if(!s||!Ed.doc)return false;const W=DW(),H=DH(),x=Math.floor(p.x),y=Math.floor(p.y);if(x<0||y<0||x>=W||y>=H)return false;
  if(!selCache||selCache.sel!==s||selCache.W!==W||selCache.H!==H)selCache={sel:s,W,H,m:readAlpha(s,Ed.select.bounds()||undefined)};return selCache.m[y*W+x]>=128;}
// Layer or composite pixels, un-premultiplied RGBA as canvases give them. A targeted layer mask is sampled as gray.
function sourcePixels(all){Ed.flush();const L=Ed.layers.active();
  if(!all&&L&&L.mask&&Ed.doc.target==='mask'){const d=U.read(L.mask).data;for(let i=0;i<d.length;i+=4){const v=d[i+3];d[i]=d[i+1]=d[i+2]=v;d[i+3]=255;}return d;}
  const c=all||!L||L.kind!=='pixel'?Ed.composite():L.canvas;return U.read(c).data;}
// Premultiplied copy: transparent pixels compare as transparent whatever colour they hide.
function premul(d){const n=d.length,p=new Uint8Array(n);for(let i=0;i<n;i+=4){const a=d[i+3];p[i+3]=a;if(a===255){p[i]=d[i];p[i+1]=d[i+1];p[i+2]=d[i+2];}
  else if(a){p[i]=Math.round(d[i]*a/255);p[i+1]=Math.round(d[i+1]*a/255);p[i+2]=Math.round(d[i+2]*a/255);}}return p;}
// The result of a tool goes in as one history state; an empty New selection deselects.
function applySel(m,mode,label,R){if(!Ed.doc)return false;const W=DW(),H=DH();
  if(mode!=='add'&&!Ed.select.hasSelection())mode='new';   // nothing to subtract from or intersect with: a new selection
  if(R===undefined)R=bboxOf(m,W,H);
  if(!R){if((mode==='new'||mode==='intersect')&&Ed.select.hasSelection())Ed.select.none();return false;}
  Ed.select.set(toCanvas(m,R),mode==='new'?'replace':mode,{label});warnInvisible();return true;}
function warnInvisible(){const o=Ed.select.outline();if(Ed.select.hasSelection()&&o&&!o.h.length&&!o.v.length)Ed.message('Warning: No pixels are more than 50% selected. The selection edges will not be visible.');}

// ----- rasterizers --------------------------------------------------------------------------------------------------------
function fillRect(m,W,H,x0,y0,x1,y1){const a=Math.max(0,x0),b=Math.min(W,x1),c=Math.max(0,y0),d=Math.min(H,y1);if(b<=a||d<=c)return null;for(let y=c;y<d;y++)m.fill(255,y*W+a,y*W+b);return{x:a,y:c,w:b-a,h:d-c};}
// Ellipse inscribed in (x0,y0)-(x1,y1). Anti-aliased: each pixel gets its covered area (exact across rows, 32 strips
// across columns); aliased: a pixel is in when its centre is.
function fillEllipse(m,W,H,x0,y0,x1,y1,aa){const cx=(x0+x1)/2,cy=(y0+y1)/2,a=(x1-x0)/2,b=(y1-y0)/2;if(!(a>0&&b>0))return null;
  const px0=Math.max(0,Math.floor(x0)),px1=Math.min(W,Math.ceil(x1)),py0=Math.max(0,Math.floor(y0)),py1=Math.min(H,Math.ceil(y1));if(px1<=px0||py1<=py0)return null;
  if(!aa){for(let y=py0;y<py1;y++){const t=(y+.5-cy)/b,q=1-t*t;if(q<0)continue;const hw=a*Math.sqrt(q),xs=Math.max(px0,Math.ceil(cx-hw-.5)),xe=Math.min(px1-1,Math.floor(cx+hw-.5));if(xe>=xs)m.fill(255,y*W+xs,y*W+xe+1);}
    return{x:px0,y:py0,w:px1-px0,h:py1-py0};}
  const N=32,lo=new Float64Array(N),hi=new Float64Array(N);
  for(let x=px0;x<px1;x++){let minLo=Infinity,maxLo=-Infinity,minHi=Infinity,maxHi=-Infinity;
    for(let k=0;k<N;k++){const t=(x+(k+.5)/N-cx)/a,q=1-t*t,h=q>0?b*Math.sqrt(q):0,l=cy-h,u=cy+h;lo[k]=l;hi[k]=u;if(l<minLo)minLo=l;if(l>maxLo)maxLo=l;if(u<minHi)minHi=u;if(u>maxHi)maxHi=u;}
    const r0=Math.max(py0,Math.floor(minLo)),r1=Math.min(py1,Math.ceil(maxHi)),f0=Math.ceil(maxLo),f1=Math.floor(minHi);
    for(let y=r0;y<r1;y++){let v=255;if(!(y>=f0&&y+1<=f1)){let s=0;for(let k=0;k<N;k++){const o=Math.min(y+1,hi[k])-Math.max(y,lo[k]);if(o>0)s+=o;}v=Math.round(s/N*255);}if(v)m[y*W+x]=v;}}
  return{x:px0,y:py0,w:px1-px0,h:py1-py0};}
// Polygon (closed, non-zero winding). Anti-aliased: exact area coverage (signed-area accumulation); aliased: pixel centres.
function fillPolygon(m,W,H,pts,aa){if(pts.length<3)return null;let minx=Infinity,miny=Infinity,maxx=-Infinity,maxy=-Infinity;
  for(const p of pts){if(p.x<minx)minx=p.x;if(p.x>maxx)maxx=p.x;if(p.y<miny)miny=p.y;if(p.y>maxy)maxy=p.y;}
  const bx0=Math.max(0,Math.floor(minx)),bx1=Math.min(W,Math.ceil(maxx)),by0=Math.max(0,Math.floor(miny)),by1=Math.min(H,Math.ceil(maxy));if(bx1<=bx0||by1<=by0)return null;
  const bw=bx1-bx0,bh=by1-by0,n=pts.length;
  if(aa){const acc=new Float64Array(bw*bh+bw+2);
    for(let i=0;i<n;i++){const p=pts[i],q=pts[(i+1)%n];clipLine(acc,bw,bh,p.x-bx0,p.y-by0,q.x-bx0,q.y-by0);}
    let s=0,any=false;for(let y=0;y<bh;y++){const o=(by0+y)*W+bx0,r=y*bw;for(let x=0;x<bw;x++){s+=acc[r+x];const v=Math.round(Math.min(1,Math.abs(s))*255);if(v){m[o+x]=v;any=true;}}}
    return any?{x:bx0,y:by0,w:bw,h:bh}:null;}
  const rows=Array.from({length:bh},()=>[]);
  for(let i=0;i<n;i++){const p=pts[i],q=pts[(i+1)%n];if(p.y===q.y)continue;const dir=q.y>p.y?1:-1,ya=Math.min(p.y,q.y),yb=Math.max(p.y,q.y);
    const r0=Math.max(by0,Math.ceil(ya-.5)),r1=Math.min(by1,Math.ceil(yb-.5));for(let y=r0;y<r1;y++){const yc=y+.5;rows[y-by0].push(p.x+(yc-p.y)*(q.x-p.x)/(q.y-p.y),dir);}}
  let any=false;
  for(let r=0;r<bh;r++){const xs=rows[r];if(!xs.length)continue;const idx=[];for(let k=0;k<xs.length;k+=2)idx.push(k);idx.sort((a,b)=>xs[a]-xs[b]);
    let wind=0,prev=0;const o=(by0+r)*W;for(const k of idx){const x=xs[k];if(wind!==0){const a=Math.max(0,Math.ceil(prev-.5)),b=Math.min(W,Math.ceil(x-.5));if(b>a){m.fill(255,o+a,o+b);any=true;}}wind+=xs[k+1];prev=x;}}
  return any?{x:bx0,y:by0,w:bw,h:bh}:null;}
// One polygon edge into the accumulation buffer, split where it leaves 0..w (the outside parts run along the border).
function clipLine(a,w,h,x0,y0,x1,y1){const ts=[0,1];if(x0!==x1)for(const X of [0,w]){const t=(X-x0)/(x1-x0);if(t>0&&t<1)ts.push(t);}ts.sort((p,q)=>p-q);
  for(let i=0;i+1<ts.length;i++){const ta=ts[i],tb=ts[i+1];accLine(a,w,h,clamp(x0+(x1-x0)*ta,0,w),y0+(y1-y0)*ta,clamp(x0+(x1-x0)*tb,0,w),y0+(y1-y0)*tb);}}
function accLine(a,w,h,x0,y0,x1,y1){if(y0===y1)return;let dir=1;if(y0>y1){dir=-1;let t=x0;x0=x1;x1=t;t=y0;y0=y1;y1=t;}
  const dxdy=(x1-x0)/(y1-y0);let x=x0;if(y0<0)x-=y0*dxdy;const ys=Math.max(0,Math.floor(y0)),ye=Math.min(h,Math.ceil(y1));
  for(let y=ys;y<ye;y++){const ls=y*w,dy=Math.min(y+1,y1)-Math.max(y,y0),xn=x+dxdy*dy,d=dy*dir;const xa=Math.min(x,xn),xb=Math.max(x,xn);
    const xaf=Math.floor(xa),xai=xaf|0,xbc=Math.ceil(xb),xbi=xbc|0;
    if(xbi<=xai+1){const xmf=.5*(x+xn)-xaf;a[ls+xai]+=d-d*xmf;a[ls+xai+1]+=d*xmf;}
    else{const s=1/(xb-xa),x0f=xa-xaf,a0=.5*s*(1-x0f)*(1-x0f),x1f=xb-xbc+1,am=.5*s*x1f*x1f;a[ls+xai]+=d*a0;
      if(xbi===xai+2)a[ls+xai+1]+=d*(1-a0-am);
      else{const a1=s*(1.5-x0f);a[ls+xai+1]+=d*(a1-a0);for(let xi=xai+2;xi<xbi-1;xi++)a[ls+xi]+=d*s;const a2=a1+(xbi-xai-3)*s;a[ls+xbi-1]+=d*(1-a2-am);}
      a[ls+xbi]+=d*am;}
    x=xn;}}

// ----- feather: Gaussian blur, sigma = radius --------------------------------------------------------------------------------
// Exact separable kernel to 4 sigma (constant runs are copied, so the cost follows the edges) up to sigma 10; above that
// 16 box passes of matched variance on lines extended by their reach (within 1 level of a Gaussian). zeroEdges: beyond
// the canvas counts as unselected ("Apply effect at canvas bounds"); otherwise the canvas edge repeats outward, so a
// selection touching the edge stays solid there.
function boxSizes(sigma,n){const wi=Math.sqrt(12*sigma*sigma/n+1);let wl=Math.floor(wi);if(wl%2===0)wl--;const wu=wl+2;const m=Math.round((12*sigma*sigma-n*wl*wl-4*n*wl-3*n)/(-4*wl-4));return Array.from({length:n},(_,i)=>i<m?wl:wu);}
function gaussMask(src,W,H,sigma,zeroEdges){if(!(sigma>0.05))return src.slice();const R=bboxOf(src,W,H);if(!R)return src.slice();
  const big=sigma>10,boxes=big?boxSizes(sigma,16):null,reach=big?boxes.reduce((s,w)=>s+(w>>1),0):Math.ceil(sigma*4);
  let kernel=null;if(!big){kernel=new Float64Array(2*reach+1);let s=0;for(let i=-reach;i<=reach;i++){const v=Math.exp(-i*i/(2*sigma*sigma));kernel[i+reach]=v;s+=v;}for(let i=0;i<kernel.length;i++)kernel[i]/=s;}
  const x0=Math.max(0,R.x-reach-1),x1=Math.min(W,R.x+R.w+reach+1),y0=Math.max(0,R.y-reach-1),y1=Math.min(H,R.y+R.h+reach+1),w=x1-x0,h=y1-y0;
  const buf=new Float64Array(w*h);for(let y=0;y<h;y++){const s=(y0+y)*W+x0;for(let x=0;x<w;x++)buf[y*w+x]=src[s+x];}
  const n=Math.max(w,h),line=new Float64Array(n),out=new Float64Array(n),pad=new Float64Array(n+2*reach+4),pad2=big?new Float64Array(n+2*reach+4):null;
  const lc=x0===0&&!zeroEdges,rc=x1===W&&!zeroEdges,tc=y0===0&&!zeroEdges,bc=y1===H&&!zeroEdges;
  const run=(len,cl,cr)=>{if(big)boxRun(line,len,out,boxes,reach,cl,cr,pad,pad2);else firRun(line,len,out,kernel,reach,cl,cr,pad);};
  for(let y=0;y<h;y++){const o=y*w;for(let x=0;x<w;x++)line[x]=buf[o+x];run(w,lc,rc);for(let x=0;x<w;x++)buf[o+x]=out[x];}
  for(let x=0;x<w;x++){for(let y=0;y<h;y++)line[y]=buf[y*w+x];run(h,tc,bc);for(let y=0;y<h;y++)buf[y*w+x]=out[y];}
  const dst=src.slice();for(let y=0;y<h;y++){const s=(y0+y)*W+x0;for(let x=0;x<w;x++)dst[s+x]=Math.round(clamp(buf[y*w+x],0,255));}return dst;}
function firRun(a,n,out,k,r,cl,cr,pad){const L=cl?a[0]:0,Rv=cr?a[n-1]:0;for(let i=0;i<n;i++)out[i]=a[i];
  const bounds=[];if(a[0]!==L)bounds.push(0);for(let i=1;i<n;i++)if(a[i]!==a[i-1])bounds.push(i);if(a[n-1]!==Rv)bounds.push(n);if(!bounds.length)return;
  for(let i=0;i<r;i++){pad[i]=L;pad[n+r+i]=Rv;}for(let i=0;i<n;i++)pad[r+i]=a[i];
  let done=-1;for(const b of bounds){const s=Math.max(done+1,b-r),e=Math.min(n-1,b+r-1);for(let x=s;x<=e;x++){let v=0;for(let j=0;j<=2*r;j++)v+=k[j]*pad[x+j];out[x]=v;}if(e>done)done=e;}}
// The line is extended by T (the passes' total reach) with its outside value, so no pass loses what flows past an end.
function boxRun(a,n,out,boxes,T,cl,cr,e1,e2){const L=cl?a[0]:0,Rv=cr?a[n-1]:0,len=n+2*T;
  for(let i=0;i<T;i++){e1[i]=L;e1[T+n+i]=Rv;}for(let i=0;i<n;i++)e1[T+i]=a[i];let src=e1,dst=e2;
  for(const w of boxes){const r=w>>1;let s=0;for(let j=-r;j<=r;j++)s+=src[clamp(j,0,len-1)];
    for(let x=0;x<len;x++){dst[x]=s/w;s+=src[Math.min(len-1,x+r+1)]-src[Math.max(0,x-r)];}const t=src;src=dst;dst=t;}
  for(let i=0;i<n;i++)out[i]=src[T+i];}

// ----- distance-based Modify commands -------------------------------------------------------------------------------------
// Exact Euclidean distance transform (Felzenszwalb & Huttenlocher): squared distance from each pixel centre to the nearest
// feature pixel centre.
function edt(f,W,H){const INF=1e20,n=Math.max(W,H),g=new Float32Array(W*H),line=new Float64Array(n),d=new Float64Array(n),v=new Int32Array(n),z=new Float64Array(n+1);
  for(let x=0;x<W;x++){for(let y=0;y<H;y++)line[y]=f[y*W+x]?0:INF;edt1(line,H,d,v,z);for(let y=0;y<H;y++)g[y*W+x]=d[y];}
  for(let y=0;y<H;y++){const o=y*W;for(let x=0;x<W;x++)line[x]=g[o+x];edt1(line,W,d,v,z);for(let x=0;x<W;x++)g[o+x]=d[x];}return g;}
function edt1(f,n,d,v,z){let k=0;v[0]=0;z[0]=-Infinity;z[1]=Infinity;
  for(let q=1;q<n;q++){let p=v[k],s=((f[q]+q*q)-(f[p]+p*p))/(2*q-2*p);while(s<=z[k]&&k>0){k--;p=v[k];s=((f[q]+q*q)-(f[p]+p*p))/(2*q-2*p);}k++;v[k]=q;z[k]=s;z[k+1]=Infinity;}
  k=0;for(let q=0;q<n;q++){while(z[k+1]<q)k++;const t=q-v[k];d[q]=t*t+f[v[k]];}}
// Selection edges are taken at 50%, as Photoshop's Expand and Contract do (the result is hard-edged).
function expandMask(m,W,H,n){const f=new Uint8Array(W*H);for(let i=0;i<f.length;i++)f[i]=m[i]>=128?1:0;const g=edt(f,W,H),out=new Uint8Array(W*H),t=n*n;for(let i=0;i<out.length;i++)out[i]=g[i]<=t?255:0;return out;}
function contractMask(m,W,H,n,atBounds){const P=atBounds?1:0,Wp=W+2*P,Hp=H+2*P,f=new Uint8Array(Wp*Hp);if(atBounds)f.fill(1);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++)f[(y+P)*Wp+x+P]=m[y*W+x]>=128?0:1;const g=edt(f,Wp,Hp),out=new Uint8Array(W*H),t=n*n;
  for(let y=0;y<H;y++)for(let x=0;x<W;x++)out[y*W+x]=g[(y+P)*Wp+x+P]>t?255:0;return out;}
// Smooth: a pixel is selected when more than half of the (2r+1)^2 square around it is (partial values count by weight).
function smoothMask(m,W,H,r,atBounds){const A=new Int32Array(W*H),line=new Int32Array(Math.max(W,H)+2*r+2);
  for(let y=0;y<H;y++){const o=y*W;for(let i=-r;i<W+r;i++){const x=i<0?(atBounds?-1:0):i>=W?(atBounds?-1:W-1):i;line[i+r]=x<0?0:m[o+x];}let s=0;for(let j=0;j<2*r+1;j++)s+=line[j];for(let x=0;x<W;x++){A[o+x]=s;s+=line[x+2*r+1]-line[x];}}
  const out=new Uint8Array(W*H),full=255*(2*r+1)*(2*r+1);
  for(let x=0;x<W;x++){for(let i=-r;i<H+r;i++){const y=i<0?(atBounds?-1:0):i>=H?(atBounds?-1:H-1):i;line[i+r]=y<0?0:A[y*W+x];}let s=0;for(let j=0;j<2*r+1;j++)s+=line[j];for(let y=0;y<H;y++){out[y*W+x]=2*s>full?255:0;s+=line[y+2*r+1]-line[y];}}
  return out;}
// Border: a soft band `width` wide centred on the 50% edge (width/2 inside, width/2 outside), as Photoshop documents it.
function borderMask(m,W,H,width){const f=new Uint8Array(W*H),g=new Uint8Array((W+2)*(H+2));g.fill(1);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){const s=m[y*W+x]>=128;f[y*W+x]=s?1:0;g[(y+1)*(W+2)+x+1]=s?0:1;}
  const dOut=edt(f,W,H),dIn=edt(g,W+2,H+2),half=width/2,soft=Math.max(1,width/4),out=new Uint8Array(W*H);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){const i=y*W+x,e=Math.sqrt(f[i]?dIn[(y+1)*(W+2)+x+1]:dOut[i])-.5;out[i]=Math.round(255*clamp((half-e)/soft+.5,0,1));}return out;}

// ----- anti-aliasing a hard (0/255) region: coverage of its outline drawn through the midpoints of the pixel edges --------
// Straight edges stay crisp, stair steps get partial coverage (exact for 45 degree steps: 7/8 inside, 1/8 outside), an
// isolated pixel keeps half. Outside the canvas repeats the edge, so a region touching the edge stays solid there.
function antialias(bin,W,H,R){R=R||bboxOf(bin,W,H);const out=bin.slice();if(!R)return out;
  const at=(x,y)=>bin[clamp(y,0,H-1)*W+clamp(x,0,W-1)]?1:0;
  const xa=Math.max(0,R.x-1),xb=Math.min(W,R.x+R.w+1),ya=Math.max(0,R.y-1),yb=Math.min(H,R.y+R.h+1);
  for(let y=ya;y<yb;y++)for(let x=xa;x<xb;x++){const P=at(x,y),N=at(x,y-1),S=at(x,y+1),Wt=at(x-1,y),E=at(x+1,y);
    if(P){if(N&&S&&Wt&&E)continue;const c=((N||Wt)?2:1)+((N||E)?2:1)+((S||Wt)?2:1)+((S||E)?2:1);if(c<8)out[y*W+x]=Math.ceil(c*255/8);}
    else{if(!((N||S)&&(Wt||E)))continue;const c=(N&&Wt&&at(x-1,y-1))+(N&&E&&at(x+1,y-1))+(S&&Wt&&at(x-1,y+1))+(S&&E&&at(x+1,y+1));if(c)out[y*W+x]=Math.floor(c*255/8);}}
  return out;}

// ----- colour regions: Magic Wand, Grow, Similar ---------------------------------------------------------------------------
// Tolerance follows Photoshop's "levels": a pixel matches when every channel (premultiplied R, G, B and alpha) is within
// Tolerance of the sample. Contiguous regions are 4-connected (as Photoshop's wand and bucket: a 1 px checkerboard stays
// one square).
function flood(match,W,H,sx,sy,out){if(!match[sy*W+sx])return null;const st=[sx,sy];let x0=sx,x1=sx,y0=sy,y1=sy;
  while(st.length){const y=st.pop(),x=st.pop(),row=y*W;if(out[row+x])continue;let l=x,r=x;while(l>0&&match[row+l-1]&&!out[row+l-1])l--;while(r<W-1&&match[row+r+1]&&!out[row+r+1])r++;
    out.fill(255,row+l,row+r+1);if(l<x0)x0=l;if(r>x1)x1=r;if(y<y0)y0=y;if(y>y1)y1=y;
    for(let ny=y-1;ny<=y+1;ny+=2){if(ny<0||ny>=H)continue;const nr=ny*W;let i=l;while(i<=r){while(i<=r&&!(match[nr+i]&&!out[nr+i]))i++;if(i>r)break;st.push(i,ny);while(i<=r&&match[nr+i]&&!out[nr+i])i++;}}}
  return{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};}
function sampleColor(px,W,H,x,y,size){const h=(size-1)>>1;let r=0,g=0,b=0,a=0,c=0;
  for(let yy=Math.max(0,y-h);yy<=Math.min(H-1,y+h);yy++)for(let xx=Math.max(0,x-h);xx<=Math.min(W-1,x+h);xx++){const i=(yy*W+xx)*4;r+=px[i];g+=px[i+1];b+=px[i+2];a+=px[i+3];c++;}return[r/c,g/c,b/c,a/c];}
function wandMask(px,W,H,x,y,o){const s=sampleColor(px,W,H,x,y,o.sampleSize),t=o.tolerance,n=W*H,match=new Uint8Array(n);
  for(let i=0,j=0;i<n;i++,j+=4)match[i]=Math.abs(px[j]-s[0])<=t&&Math.abs(px[j+1]-s[1])<=t&&Math.abs(px[j+2]-s[2])<=t&&Math.abs(px[j+3]-s[3])<=t?1:0;
  let out,R;if(o.contiguous){out=new Uint8Array(n);R=flood(match,W,H,x,y,out);if(!R)return{m:out,R:null};}
  else{out=match;for(let i=0;i<n;i++)if(out[i])out[i]=255;R=bboxOf(out,W,H);}
  if(o.antialias&&R)out=antialias(out,W,H,R);return{m:out,R:R&&{x:Math.max(0,R.x-1),y:Math.max(0,R.y-1),w:Math.min(W,R.x+R.w+1)-Math.max(0,R.x-1),h:Math.min(H,R.y+R.h+1)-Math.max(0,R.y-1)}};}
// The colours of the selected pixels (50% or more), widened by Tolerance in every channel: a 256^3 table.
function colorSet(px,W,H,sel,tol){const n=W*H;let amin=255,amax=0,any=false;const all=tol>=255,g=all?null:new Uint8Array(1<<24);
  for(let i=0,j=0;i<n;i++,j+=4)if(sel[i]>=128){any=true;const a=px[j+3];if(a<amin)amin=a;if(a>amax)amax=a;if(g)g[px[j]<<16|px[j+1]<<8|px[j+2]]=1;}
  if(!any)return null;if(g&&tol>0){dilateAxis(g,1,tol);dilateAxis(g,256,tol);dilateAxis(g,65536,tol);}
  return{g,amin:amin-tol,amax:amax+tol};}
// Binary dilation by t along one axis of the colour cube (index r<<16 | g<<8 | b): 65536 lines of 256 cells `stride` apart.
function dilateAxis(g,stride,t){const buf=new Uint8Array(256);
  for(let L=0;L<65536;L++){const base=stride===1?L*256:stride===256?(L>>8)*65536+(L&255):L;let any=false;
    for(let i=0;i<256;i++){const v=g[base+i*stride];buf[i]=v;if(v)any=true;}if(!any)continue;
    let last=-1e9;for(let i=0;i<256;i++){if(buf[i])last=i;if(i-last<=t)g[base+i*stride]=1;}
    last=1e9;for(let i=255;i>=0;i--){if(buf[i])last=i;if(last-i<=t)g[base+i*stride]=1;}}}
function inSet(cs,px,j){const a=px[j+3];return a>=cs.amin&&a<=cs.amax&&(!cs.g||cs.g[px[j]<<16|px[j+1]<<8|px[j+2]]===1);}

// ----- marching ants for the shapes being drawn and the outline being moved ------------------------------------------------------
// Same geometry as the shell's marching ants, so the outline being moved replaces them exactly.
function runsPath(o,v,dx=0,dy=0){const p=new Path2D(),s=v.scale,sx=x=>Math.round(v.x+(x+dx)*s),sy=y=>Math.round(v.y+(y+dy)*s),vw=v.width,vh=v.height;
  for(let i=0;i<o.h.length;i+=3){const y=sy(o.h[i]);if(y<-2||y>vh+2)continue;const a=Math.max(-2,sx(o.h[i+1])),b=Math.min(vw+2,sx(o.h[i+2])+1);if(b<a)continue;p.moveTo(a,y+.5);p.lineTo(b,y+.5);}
  for(let i=0;i<o.v.length;i+=3){const x=sx(o.v[i]);if(x<-2||x>vw+2)continue;const a=Math.max(-2,sy(o.v[i+1])),b=Math.min(vh+2,sy(o.v[i+2]));if(b<a)continue;p.moveTo(x+.5,a);p.lineTo(x+.5,b);}
  return p;}
const antsPhase=()=>Math.floor(performance.now()/130)%8;
function drawAnts(ctx,path){ctx.lineWidth=1;ctx.setLineDash([]);ctx.strokeStyle='#fff';ctx.stroke(path);ctx.setLineDash([4,4]);ctx.lineDashOffset=-antsPhase();ctx.strokeStyle='#000';ctx.stroke(path);ctx.setLineDash([]);}
// Hides the core's ants (the outline is being replaced or moved): they are drawn just before the tool's overlay.
function eraseAnts(ctx,v){const o=Ed.select.outline();if(!o)return;ctx.save();ctx.globalCompositeOperation='destination-out';ctx.lineWidth=2;ctx.setLineDash([]);ctx.strokeStyle='#000';ctx.stroke(runsPath(o,v));ctx.restore();}
const scr=(v,x,y)=>({x:v.x+x*v.scale,y:v.y+y*v.scale});
// Ants keep moving while a tool shows a shape of its own.
let antsTimer=0;function animate(on){if(on&&!antsTimer)antsTimer=setInterval(()=>{if(!document.hidden)redraw();},130);if(!on&&antsTimer){clearInterval(antsTimer);antsTimer=0;}}

// ----- selection modes, moving and nudging the outline -----------------------------------------------------------------------
const MODES=['new','add','subtract','intersect'];
const modeOf=id=>{const m=Ed.toolOptions(id).mode;return MODES.includes(m)?m:'new';};
// Mode keys at the start of a drag. With no selection Shift and Alt stay free for the drag (square, from the centre).
function startMode(id,p){const base=modeOf(id),has=Ed.select.hasSelection();
  if(has&&p.shiftKey&&p.altKey)return{mode:'intersect',shift:true,alt:true};if(has&&p.shiftKey)return{mode:'add',shift:true,alt:false};
  if(has&&p.altKey)return{mode:'subtract',shift:false,alt:true};return{mode:base,shift:false,alt:false};}
const plainNew=(id,p)=>modeOf(id)==='new'&&!p.shiftKey&&!p.altKey;
function moveSelection(dx,dy,label='Move Selection'){const s=Ed.selection;if(!s||(!dx&&!dy))return false;const c=U.canvas(DW(),DH());c.getContext('2d').drawImage(s,dx,dy);Ed.select.set(c,'replace',{label});return true;}
const dragged=(a,p)=>Math.hypot(p.clientX-a.clientX,p.clientY-a.clientY)>=3;
function moveStart(p){return{start:p,runs:Ed.select.outline(),dx:0,dy:0,moving:false};}
function moveUpdate(mv,p){if(!mv.moving&&!dragged(mv.start,p))return;mv.moving=true;let dx=p.x-mv.start.x,dy=p.y-mv.start.y;
  if(p.shiftKey){const a=Math.round(Math.atan2(dy,dx)/(Math.PI/4))*(Math.PI/4),l=dx*Math.cos(a)+dy*Math.sin(a);dx=l*Math.cos(a);dy=l*Math.sin(a);}
  mv.dx=Math.round(dx);mv.dy=Math.round(dy);redraw();}
function drawMove(ctx,v,mv){if(!mv.moving||!mv.runs)return;eraseAnts(ctx,v);drawAnts(ctx,runsPath(mv.runs,v,mv.dx,mv.dy));}
// Arrow keys nudge the outline 1 px (Shift: 10 px) while a selection tool is active and the picture has focus.
function nudgeKey(e){if(!/^Arrow(Left|Right|Up|Down)$/.test(e.key)||e.ctrlKey||e.altKey||e.metaKey||!Ed.select.hasSelection())return false;
  const t=e.target;if(t&&t!==document.body&&t.closest&&t.closest('button,input,select,textarea,a,[role=listbox],[role=menu],.ed-dock,.ed-tools,.ed-optionsbar,.ed-menubar,.library,.inspector'))return false;
  const n=e.shiftKey?10:1;moveSelection(e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0,e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0);return true;}

// ----- options bar fields: mode buttons, marquee style, hidden values ----------------------------------------------------------
const NS='http://www.w3.org/2000/svg';
function svg(parts,o={}){const s=document.createElementNS(NS,'svg');s.setAttribute('viewBox',o.viewBox||'0 0 24 24');s.setAttribute('width',o.size||18);s.setAttribute('height',o.size||18);s.setAttribute('aria-hidden','true');
  s.setAttribute('fill','none');s.setAttribute('stroke','currentColor');s.setAttribute('stroke-width',o.width||1.7);s.setAttribute('stroke-linecap','round');s.setAttribute('stroke-linejoin','round');
  for(const q of parts){const e=document.createElementNS(NS,'path');e.setAttribute('d',typeof q==='string'?q:q.d);if(q.fill)e.setAttribute('fill',q.fill);if(q.dash)e.setAttribute('stroke-dasharray',q.dash);if(q.none)e.setAttribute('stroke','none');if(q.op)e.setAttribute('opacity',q.op);s.append(e);}return s;}
const MODE_INFO={new:['New selection','M3.5 6.5h11v11h-11z'],add:['Add to selection (Shift)'],subtract:['Subtract from selection (Alt)'],intersect:['Intersect with selection (Shift+Alt)']};
function modeIcon(m){const A='M3 4h11v11H3z',B='M9 9h11v11H9z';
  if(m==='new')return svg([{d:'M5 5h13v13H5z',fill:'currentColor',op:.85}],{size:16,width:1.4});
  if(m==='add')return svg([{d:'M3 4h11v5h6v11H9v-5H3z',fill:'currentColor',op:.85}],{size:16,width:1.4});
  if(m==='subtract')return svg([{d:'M3 4h11v5H9v6H3z',fill:'currentColor',op:.85},B],{size:16,width:1.4});
  return svg([A,B,{d:'M9 9h5v6H9z',fill:'currentColor',none:true}],{size:16,width:1.4});}
Ed.registerField('sel-mode',(o,value)=>{const box=el('div','ed-selmode');box.setAttribute('role','radiogroup');box.setAttribute('aria-label','Selection mode');
  for(const m of o.modes||MODES){const b=btn('ed-selmode-btn','',MODE_INFO[m][0]);b.dataset.mode=m;b.setAttribute('role','radio');b.setAttribute('aria-label',MODE_INFO[m][0]);b.append(modeIcon(m));
    b.onclick=()=>{if(Ed.tool)Ed.setToolOptions(Ed.tool,{[o.key]:m});};box.append(b);}
  syncMode(box,value);return box;});
function syncMode(box,v){for(const b of box.querySelectorAll('.ed-selmode-btn')){const on=b.dataset.mode===v;b.classList.toggle('on',on);b.setAttribute('aria-checked',String(on));}}
Ed.registerField('sel-hidden',()=>null);
// Style: Normal / Fixed Ratio / Fixed Size, with Width and Height only when they matter (as Photoshop greys them out).
Ed.registerField('sel-style',(o,value)=>{const box=el('div','ed-selstyle'),lab=el('label','ed-field-label','Style'),s=el('select');s.name='style';const id='edss'+Math.random().toString(36).slice(2,7);s.id=id;lab.htmlFor=id;
  for(const [v,l] of [['normal','Normal'],['ratio','Fixed Ratio'],['size','Fixed Size']]){const op=el('option','',l);op.value=v;s.append(op);}
  s.title='Normal: drag any size · Fixed Ratio: keep Width : Height · Fixed Size: click to place a box of exactly Width × Height pixels';
  s.onchange=()=>{if(Ed.tool)Ed.setToolOptions(Ed.tool,{style:s.value});s.blur();};
  const wh=el('span','ed-selstyle-wh'),w=el('input'),h=el('input'),swap=btn('ed-selstyle-swap','⇄','Swap Width and Height'),unit=el('span','ed-unit','px');
  for(const [i,k] of [[w,'W'],[h,'H']]){i.type='number';i.min=0;i.step='any';i.name=k==='W'?'styleWidth':'styleHeight';i.setAttribute('aria-label',k==='W'?'Width':'Height');i.title=k==='W'?'Width':'Height';
    i.onchange=()=>{const t=Ed.tool,v=Ed.toolOptions(t),key=(v.style==='size'?'size':'ratio')+k,n=num(i.value,null);if(n!=null&&n>0)Ed.setToolOptions(t,{[key]:v.style==='size'?Math.max(1,Math.round(n)):n});};
    i.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();i.dispatchEvent(new Event('change'));i.blur();}else if(e.key==='Escape')i.blur();};}
  swap.onclick=()=>{const t=Ed.tool,v=Ed.toolOptions(t),p=v.style==='size'?'size':'ratio';Ed.setToolOptions(t,{[p+'W']:v[p+'H'],[p+'H']:v[p+'W']});};
  wh.append(el('span','ed-selstyle-k','W'),w,swap,el('span','ed-selstyle-k','H'),h,unit);box.append(lab,s,wh);box._sync=vals=>{s.value=vals.style||'normal';const p=vals.style==='size'?'size':'ratio';
    wh.hidden=!(vals.style==='size'||vals.style==='ratio');if(document.activeElement!==w)w.value=num(vals[p+'W'],p==='size'?64:1);if(document.activeElement!==h)h.value=num(vals[p+'H'],p==='size'?64:1);unit.hidden=vals.style!=='size';};
  setTimeout(()=>{if(Ed.tool)box._sync(Ed.toolOptions(Ed.tool));});box._sync({style:value});return box;});
function syncOptionWidgets(values){const bar=document.getElementById('edOptions');if(!bar)return;for(const b of bar.querySelectorAll('.ed-selmode'))syncMode(b,MODES.includes(values.mode)?values.mode:'new');
  for(const b of bar.querySelectorAll('.ed-selstyle'))b._sync&&b._sync(values);}
Ed.on('options',({id,values})=>{if(id===Ed.tool)syncOptionWidgets(values);});
Ed.on('tool',()=>requestAnimationFrame(()=>{if(Ed.tool&&Ed.tools.get(Ed.tool))syncOptionWidgets(Ed.toolOptions(Ed.tool));}));

const OPT={
  mode:(modes)=>({key:'mode',type:'sel-mode',default:'new',modes,title:'Selection mode'}),
  feather:{key:'feather',label:'Feather',type:'number',min:0,max:1000,step:.1,unit:'px',default:0,title:'Soften the edge of the next selection by this many pixels'},
  antialias:{key:'antialias',label:'Anti-alias',type:'checkbox',default:true,title:'Smooth the stair-stepped edges of the selection'},
  style:[{key:'style',type:'sel-style',default:'normal'},{key:'ratioW',type:'sel-hidden',default:1},{key:'ratioH',type:'sel-hidden',default:1},{key:'sizeW',type:'sel-hidden',default:64},{key:'sizeH',type:'sel-hidden',default:64}]};
const styleOf=o=>({style:['normal','ratio','size'].includes(o.style)?o.style:'normal',rw:Math.max(.001,num(o.ratioW,1)),rh:Math.max(.001,num(o.ratioH,1)),sw:Math.max(1,Math.round(num(o.sizeW,64))),sh:Math.max(1,Math.round(num(o.sizeH,64)))});
const featherOf=o=>clamp(num(o.feather,0),0,1000);

// ----- tool icons -------------------------------------------------------------------------------------------------------------
const DASH='2.6 2.2';
const ICON={
  rect:()=>svg([{d:'M4.5 5.5h15v13h-15z',dash:DASH}]),
  ellipse:()=>svg([{d:'M12 5.5c4.4 0 7.5 2.9 7.5 6.5s-3.1 6.5-7.5 6.5S4.5 15.6 4.5 12 7.6 5.5 12 5.5z',dash:DASH}]),
  row:()=>svg([{d:'M2.5 12h19',dash:DASH},'M2.5 9v6M21.5 9v6']),
  col:()=>svg([{d:'M12 2.5v19',dash:DASH},'M9 2.5h6M9 21.5h6']),
  lasso:()=>svg(['M8.2 15.6C5.4 14.8 3.5 13 3.5 10.8 3.5 7.6 7.3 5 12 5s8.5 2.6 8.5 5.8-3.8 5.8-8.5 5.8c-1 0-1.9-.1-2.8-.3','M9.6 16.5c-1.2.5-1.9 1.4-1.7 2.4.2 1.2 1.6 1.6 2.6 1.1']),
  poly:()=>svg(['M9.4 16.4L3.5 9.5 9 4.5l11.5 3.2-2.2 7.8z','M9.4 16.4c-1.2.6-1.8 1.5-1.6 2.5.2 1.1 1.5 1.5 2.5 1']),
  wand:()=>svg(['M4 20.5L14.6 9.9','M13.2 8.5l2.3 2.3','M17.5 3v3.4M15.8 4.7h3.4','M20.5 9.3v2.2M19.4 10.4h2.2','M9.6 3.8v2.2M8.5 4.9h2.2'])};

// ----- marquee tools ------------------------------------------------------------------------------------------------------------
// Drag geometry. Corners snap to the pixel grid; Shift squares, Alt draws from the centre; Fixed Ratio keeps Width : Height;
// Fixed Size places a Width x Height box with its corner (Alt: its centre) at the pointer.
function marqueeBox(d,st){const ax=Math.round(d.a.x),ay=Math.round(d.a.y),bx=Math.round(d.b.x),by=Math.round(d.b.y);
  if(st.style==='size'){if(d.center){const x0=Math.round(d.b.x-st.sw/2),y0=Math.round(d.b.y-st.sh/2);return{x0,y0,x1:x0+st.sw,y1:y0+st.sh};}return{x0:bx,y0:by,x1:bx+st.sw,y1:by+st.sh};}
  let dx=bx-ax,dy=by-ay;
  if(st.style==='ratio'){const k=Math.max(Math.abs(dx)/st.rw,Math.abs(dy)/st.rh);dx=Math.sign(dx||1)*Math.round(st.rw*k);dy=Math.sign(dy||1)*Math.round(st.rh*k);}
  else if(d.constrain){const s=Math.max(Math.abs(dx),Math.abs(dy));dx=Math.sign(dx||1)*s;dy=Math.sign(dy||1)*s;}
  if(d.center)return{x0:ax-Math.abs(dx),y0:ay-Math.abs(dy),x1:ax+Math.abs(dx),y1:ay+Math.abs(dy)};
  return{x0:Math.min(ax,ax+dx),y0:Math.min(ay,ay+dy),x1:Math.max(ax,ax+dx),y1:Math.max(ay,ay+dy)};}
// A key held for the mode only turns into a drag modifier after it is released and pressed again.
function dragMods(d,shift,alt){if(!shift)d.usedShift=false;if(!alt)d.usedAlt=false;d.constrain=shift&&!d.usedShift;d.center=alt&&!d.usedAlt;}
function shapeMask(kind,b,o){const W=DW(),H=DH(),m=new Uint8Array(W*H);let R;
  if(kind==='ellipse')R=fillEllipse(m,W,H,b.x0,b.y0,b.x1,b.y1,o.antialias!==false);else R=fillRect(m,W,H,b.x0,b.y0,b.x1,b.y1);
  const f=featherOf(o);return f>0&&R?gaussMask(m,W,H,f,false):m;}
const LABELS={'marquee-rect':'Rectangular Marquee','marquee-ellipse':'Elliptical Marquee','marquee-row':'Single Row Marquee','marquee-col':'Single Column Marquee',
  lasso:'Lasso','lasso-poly':'Polygonal Lasso','magic-wand':'Magic Wand'};
const common={cursor:'crosshair',
  onHover(p){this.cursor=plainNew(this.id,p)&&selAt(p)?'move':(this.baseCursor||'crosshair');},
  onKey(e){return nudgeKey(e);},
  onDeactivate(){this.cancelAll();},onCancel(){this.cancelAll();},
  cancelAll(){this.d=null;this.mv=null;animate(false);redraw();}};
function marquee(kind){return{...common,
  onDown(p){if(!Ed.doc)return false;const sm=startMode(this.id,p);this.mv=null;
    if(sm.mode==='new'&&!p.shiftKey&&!p.altKey&&selAt(p)){this.mv=moveStart(p);return;}
    this.d={start:p,mode:sm.mode,usedShift:sm.shift,usedAlt:sm.alt,a:{x:p.x,y:p.y},b:{x:p.x,y:p.y},space:null};dragMods(this.d,p.shiftKey,p.altKey);animate(true);redraw();},
  onMove(p){if(this.mv){moveUpdate(this.mv,p);return;}const d=this.d;if(!d)return;
    if(d.space){d.a.x+=p.x-d.space.x;d.a.y+=p.y-d.space.y;d.space={x:p.x,y:p.y};}d.b={x:p.x,y:p.y};dragMods(d,p.shiftKey,p.altKey);redraw();},
  onUp(p){if(this.mv){const mv=this.mv;this.mv=null;if(mv.moving){if(mv.dx||mv.dy)moveSelection(mv.dx,mv.dy);}else Ed.select.none();redraw();return;}
    const d=this.d;this.d=null;animate(false);if(!d){redraw();return;}d.b={x:p.x,y:p.y};dragMods(d,p.shiftKey,p.altKey);
    const o=Ed.toolOptions(this.id),b=this.box(d,o);redraw();
    if(!b){if(d.mode==='new')Ed.select.none();return;}
    applySel(shapeMask(kind,b,o),d.mode,LABELS[this.id]);},
  box(d,o){const b=marqueeBox(d,styleOf(o));return b.x1>b.x0&&b.y1>b.y0?b:null;},
  onKey(e){const d=this.d;if(d){if(e.key===' '){if(!d.space)d.space={x:d.b.x,y:d.b.y};return true;}
      if(e.key==='Shift'||e.key==='Alt'){dragMods(d,e.shiftKey,e.altKey);redraw();}return false;}return nudgeKey(e);},
  onKeyUp(e){const d=this.d;if(!d)return;if(e.key===' ')d.space=null;if(e.key==='Shift'||e.key==='Alt'){dragMods(d,e.shiftKey,e.altKey);redraw();}},
  drawOverlay(ctx,v){if(this.mv){drawMove(ctx,v,this.mv);return;}const d=this.d;if(!d)return;const b=this.box(d,Ed.toolOptions(this.id));if(d.mode==='new')eraseAnts(ctx,v);if(!b)return;
    const a=scr(v,b.x0,b.y0),c=scr(v,b.x1,b.y1),path=new Path2D();
    if(kind==='ellipse')path.ellipse((a.x+c.x)/2,(a.y+c.y)/2,Math.max(.5,(c.x-a.x)/2),Math.max(.5,(c.y-a.y)/2),0,0,Math.PI*2);
    else path.rect(Math.round(a.x)+.5,Math.round(a.y)+.5,Math.round(c.x)-Math.round(a.x),Math.round(c.y)-Math.round(a.y));drawAnts(ctx,path);}};}
function lineMarquee(kind){return{...common,
  onDown(p){if(!Ed.doc)return false;const sm=startMode(this.id,p);this.d={mode:sm.mode,p};redraw();},
  onMove(p){if(this.d){this.d.p=p;redraw();}},
  onUp(p){const d=this.d;this.d=null;if(!d)return;d.p=p;const W=DW(),H=DH(),m=new Uint8Array(W*H),o=Ed.toolOptions(this.id);
    if(kind==='row'){const y=Math.floor(p.y);if(y<0||y>=H){redraw();return;}fillRect(m,W,H,0,y,W,y+1);}else{const x=Math.floor(p.x);if(x<0||x>=W){redraw();return;}fillRect(m,W,H,x,0,x+1,H);}
    const f=featherOf(o);redraw();applySel(f>0?gaussMask(m,W,H,f,false):m,d.mode,LABELS[this.id]);},
  onHover(){this.cursor='crosshair';},
  drawOverlay(ctx,v){const d=this.d;if(!d)return;if(d.mode==='new')eraseAnts(ctx,v);const W=DW(),H=DH(),path=new Path2D();
    if(kind==='row'){const y=Math.floor(d.p.y);if(y<0||y>=H)return;const a=scr(v,0,y),b=scr(v,W,y+1);path.rect(Math.round(a.x)+.5,Math.round(a.y)+.5,Math.round(b.x)-Math.round(a.x),Math.max(1,Math.round(b.y)-Math.round(a.y)));}
    else{const x=Math.floor(d.p.x);if(x<0||x>=W)return;const a=scr(v,x,0),b=scr(v,x+1,H);path.rect(Math.round(a.x)+.5,Math.round(a.y)+.5,Math.max(1,Math.round(b.x)-Math.round(a.x)),Math.round(b.y)-Math.round(a.y));}drawAnts(ctx,path);}};}
const marqueeOptions=aa=>[OPT.mode(),OPT.feather,...(aa?[OPT.antialias]:[]),...OPT.style];
Ed.registerTool('marquee-rect',{label:'Rectangular Marquee Tool',group:'marquee',order:1,key:'m',icon:ICON.rect,options:marqueeOptions(false),...marquee('rect')});
Ed.registerTool('marquee-ellipse',{label:'Elliptical Marquee Tool',group:'marquee',order:2,key:'m',icon:ICON.ellipse,options:marqueeOptions(true),...marquee('ellipse')});
Ed.registerTool('marquee-row',{label:'Single Row Marquee Tool',group:'marquee',order:3,icon:ICON.row,options:[OPT.mode(),OPT.feather],...lineMarquee('row')});
Ed.registerTool('marquee-col',{label:'Single Column Marquee Tool',group:'marquee',order:4,icon:ICON.col,options:[OPT.mode(),OPT.feather],...lineMarquee('col')});

// ----- lasso tools -------------------------------------------------------------------------------------------------------------
// A lasso path in image coordinates; closing it rasterizes the polygon (anti-aliased by area), then feathers it.
function closeLasso(id,L){const o=Ed.toolOptions(id),pts=dedupe(L.pts);
  if(pts.length<3||polyArea(pts)<1e-6){if(L.mode==='new'&&Ed.select.hasSelection())Ed.select.none();return;}
  const W=DW(),H=DH(),m=new Uint8Array(W*H),R=fillPolygon(m,W,H,pts,o.antialias!==false),f=featherOf(o);
  applySel(f>0&&R?gaussMask(m,W,H,f,false):m,L.mode,LABELS[id]);}
function dedupe(pts){const out=[];for(const p of pts){const q=out[out.length-1];if(!q||Math.abs(q.x-p.x)>1e-6||Math.abs(q.y-p.y)>1e-6)out.push({x:p.x,y:p.y});}
  while(out.length>1&&Math.abs(out[0].x-out[out.length-1].x)<1e-6&&Math.abs(out[0].y-out[out.length-1].y)<1e-6)out.pop();return out;}
function polyArea(p){let s=0;for(let i=0;i<p.length;i++){const a=p[i],b=p[(i+1)%p.length];s+=a.x*b.y-b.x*a.y;}return Math.abs(s/2);}
// Shift snaps the segment from the last point to 45 degree steps (the point is the pointer projected onto that line).
function snap45(from,p){const dx=p.x-from.x,dy=p.y-from.y,a=Math.round(Math.atan2(dy,dx)/(Math.PI/4))*(Math.PI/4),l=dx*Math.cos(a)+dy*Math.sin(a);return{x:from.x+l*Math.cos(a),y:from.y+l*Math.sin(a)};}
function lassoPath(v,pts,extra){const path=new Path2D();pts.forEach((q,i)=>{const s=scr(v,q.x,q.y);i?path.lineTo(s.x,s.y):path.moveTo(s.x,s.y);});if(extra){const s=scr(v,extra.x,extra.y);path.lineTo(s.x,s.y);}return path;}
const nearStart=(L,p)=>{const s=L.pts[0];if(!s||L.pts.length<3)return false;const a=Ed.view.toClient(s.x,s.y);return Math.hypot(a.x-p.clientX,a.y-p.clientY)<=6;};
function drawLasso(ctx,v,L,closing){if(L.mode==='new')eraseAnts(ctx,v);drawAnts(ctx,lassoPath(v,L.pts,L.hover));
  if(closing){const s=scr(v,L.pts[0].x,L.pts[0].y);ctx.beginPath();ctx.arc(s.x,s.y,5,0,Math.PI*2);ctx.lineWidth=2.5;ctx.strokeStyle='rgba(0,0,0,.6)';ctx.stroke();ctx.lineWidth=1.2;ctx.strokeStyle='#fff';ctx.stroke();}}
const pt=p=>({x:p.x,y:p.y});
// Lasso: drag freehand; release to close with a straight line. Alt (pressed during the drag) and releasing the button
// switches to straight segments: each click adds a corner, dragging with the button down draws freehand again, releasing
// Alt closes. Backspace removes the last corner, Enter closes, Escape cancels.
Ed.registerTool('lasso',{label:'Lasso Tool',group:'lasso',order:1,key:'l',icon:ICON.lasso,options:[OPT.mode(),OPT.feather,OPT.antialias],...common,
  onDown(p){if(!Ed.doc)return false;const L=this.L;
    if(L&&L.open){L.pts.push(pt(p));L.pressed=true;L.hover=null;redraw();return;}
    const sm=startMode(this.id,p);if(sm.mode==='new'&&!p.shiftKey&&!p.altKey&&selAt(p)){this.mv=moveStart(p);return;}
    this.L={mode:sm.mode,usedAlt:sm.alt,pts:[pt(p)],pressed:true,open:false,start:p,moved:false};animate(true);redraw();},
  onMove(p){if(this.mv){moveUpdate(this.mv,p);return;}const L=this.L;if(!L)return;if(!p.altKey)L.usedAlt=false;if(dragged(L.start,p))L.moved=true;
    const q=L.pts[L.pts.length-1];if(!q||Math.hypot(q.x-p.x,q.y-p.y)*Ed.view.scale>=.75)L.pts.push(pt(p));redraw();},
  onUp(p){if(this.mv){const mv=this.mv;this.mv=null;if(mv.moving){if(mv.dx||mv.dy)moveSelection(mv.dx,mv.dy);}else Ed.select.none();redraw();return;}
    const L=this.L;if(!L)return;L.pressed=false;if(!p.altKey)L.usedAlt=false;const q=L.pts[L.pts.length-1];if(q&&(q.x!==p.x||q.y!==p.y))L.pts.push(pt(p));
    if(p.altKey&&!L.usedAlt){L.open=true;L.hover=pt(p);redraw();return;}
    this.finish();},
  finish(){const L=this.L;this.L=null;animate(false);redraw();if(!L)return;if(!L.moved&&!L.open&&L.pts.length<3){if(L.mode==='new')Ed.select.none();return;}closeLasso(this.id,L);},
  onHover(p){const L=this.L;if(L&&L.open){if(!p.altKey)L.usedAlt=false;L.hover=p.shiftKey?snap45(L.pts[L.pts.length-1],p):pt(p);redraw();this.cursor='crosshair';return;}common.onHover.call(this,p);},
  onKey(e){const L=this.L;if(L){if((e.key==='Backspace'||e.key==='Delete')&&L.open){if(L.pts.length>1)L.pts.pop();redraw();return true;}if(e.key==='Enter'){this.finish();return true;}return false;}return nudgeKey(e);},
  onKeyUp(e){const L=this.L;if(L&&e.key==='Alt'){L.usedAlt=false;if(L.open&&!L.pressed)this.finish();}},
  onCommit(){if(this.L)this.finish();},
  cancelAll(){this.L=null;this.mv=null;animate(false);redraw();},
  drawOverlay(ctx,v){if(this.mv){drawMove(ctx,v,this.mv);return;}if(this.L)drawLasso(ctx,v,this.L,false);}});
// Polygonal Lasso: click corners; double-click, Enter, Ctrl+click or a click on the start point closes. Shift = 45 degree
// steps, Alt+drag draws freehand, Backspace removes the last corner, Escape cancels.
Ed.registerTool('lasso-poly',{label:'Polygonal Lasso Tool',group:'lasso',order:2,key:'l',icon:ICON.poly,options:[OPT.mode(),OPT.feather,OPT.antialias],...common,
  onDown(p){if(!Ed.doc)return false;const P=this.P,now=performance.now();
    if(!P){const sm=startMode(this.id,p);if(sm.mode==='new'&&!p.shiftKey&&!p.altKey&&selAt(p)){this.mv=moveStart(p);this.pendingMode=sm;return;}
      this.P={mode:sm.mode,usedAlt:sm.alt,pts:[pt(p)],hover:pt(p),pressed:true,last:{t:now,x:p.clientX,y:p.clientY},down:p};animate(true);redraw();return;}
    P.pressed=true;P.down=p;if(!p.altKey)P.usedAlt=false;
    const dbl=now-P.last.t<500&&Math.hypot(p.clientX-P.last.x,p.clientY-P.last.y)<5;P.last={t:now,x:p.clientX,y:p.clientY};
    if(dbl||p.ctrlKey||nearStart(P,p)){this.finish();return false;}
    P.pts.push(p.shiftKey?snap45(P.pts[P.pts.length-1],p):pt(p));P.hover=null;redraw();},
  onMove(p){if(this.mv){moveUpdate(this.mv,p);return;}const P=this.P;if(!P)return;if(!p.altKey)P.usedAlt=false;
    if(p.altKey&&!P.usedAlt&&dragged(P.down,p)){const q=P.pts[P.pts.length-1];if(Math.hypot(q.x-p.x,q.y-p.y)*Ed.view.scale>=.75)P.pts.push(pt(p));P.hover=null;}
    else P.hover=p.shiftKey?snap45(P.pts[P.pts.length-1],p):pt(p);redraw();},
  onUp(p){if(this.mv){const mv=this.mv;this.mv=null;if(mv.moving){if(mv.dx||mv.dy)moveSelection(mv.dx,mv.dy);redraw();return;}
      // a click inside the selection starts a polygon there
      const sm=this.pendingMode||{mode:'new',alt:false};this.P={mode:sm.mode,usedAlt:sm.alt,pts:[pt(mv.start)],hover:pt(p),pressed:false,last:{t:performance.now(),x:p.clientX,y:p.clientY},down:mv.start};animate(true);redraw();return;}
    const P=this.P;if(P)P.pressed=false;},
  onHover(p){const P=this.P;if(P){if(!p.altKey)P.usedAlt=false;P.hover=p.shiftKey?snap45(P.pts[P.pts.length-1],p):pt(p);P.closing=nearStart(P,p);this.cursor='crosshair';redraw();return;}common.onHover.call(this,p);},
  finish(){const P=this.P;this.P=null;animate(false);redraw();if(P)closeLasso(this.id,P);},
  onKey(e){const P=this.P;if(P){if(e.key==='Backspace'||e.key==='Delete'){if(P.pts.length>1)P.pts.pop();else{this.P=null;animate(false);}redraw();return true;}if(e.key==='Enter'){this.finish();return true;}return false;}return nudgeKey(e);},
  onCommit(){if(this.P)this.finish();},
  cancelAll(){this.P=null;this.mv=null;animate(false);redraw();},
  drawOverlay(ctx,v){if(this.mv){drawMove(ctx,v,this.mv);return;}if(this.P)drawLasso(ctx,v,this.P,this.P.closing);}});

// ----- Magic Wand ---------------------------------------------------------------------------------------------------------------
const SAMPLE_SIZES=[[1,'Point Sample'],[3,'3 by 3 Average'],[5,'5 by 5 Average'],[11,'11 by 11 Average'],[31,'31 by 31 Average'],[51,'51 by 51 Average'],[101,'101 by 101 Average']];
const wandOpts=o=>({sampleSize:SAMPLE_SIZES.some(s=>s[0]===+o.sampleSize)?+o.sampleSize:1,tolerance:clamp(Math.round(num(o.tolerance,32)),0,255),antialias:o.antialias!==false,contiguous:o.contiguous!==false,sampleAll:!!o.sampleAll});
function magicWand(x,y,mode,o){const W=DW(),H=DH();x=Math.floor(x);y=Math.floor(y);if(x<0||y<0||x>=W||y>=H)return false;
  const px=premul(sourcePixels(o.sampleAll)),r=wandMask(px,W,H,x,y,o);return applySel(r.m,mode,'Magic Wand',r.R);}
Ed.registerTool('magic-wand',{label:'Magic Wand Tool',group:'wand',order:2,key:'w',icon:ICON.wand,
  options:[OPT.mode(),{key:'sampleSize',label:'Sample Size',type:'select',options:SAMPLE_SIZES.map(([value,label])=>({value,label})),default:1,title:'Compare with one pixel or the average of a square around it'},
    {key:'tolerance',label:'Tolerance',type:'number',min:0,max:255,step:1,default:32,title:'How different (0-255 levels per channel) a color can be and still be selected'},
    OPT.antialias,{key:'contiguous',label:'Contiguous',type:'checkbox',default:true,title:'Only touching pixels (off: every matching pixel in the image)'},
    {key:'sampleAll',label:'Sample All Layers',type:'checkbox',default:false,title:'Look at all visible layers instead of the current one'}],
  ...common,
  onDown(p){if(!Ed.doc)return false;const sm=startMode(this.id,p);this.w={p,mode:sm.mode,inside:sm.mode==='new'&&!p.shiftKey&&!p.altKey&&selAt(p)};this.mv=null;},
  onMove(p){const w=this.w;if(!w)return;if(!this.mv&&w.inside&&dragged(w.p,p))this.mv=moveStart(w.p);if(this.mv)moveUpdate(this.mv,p);},
  onUp(p){const w=this.w;this.w=null;if(this.mv){const mv=this.mv;this.mv=null;if(mv.moving&&(mv.dx||mv.dy))moveSelection(mv.dx,mv.dy);redraw();return;}if(!w)return;
    magicWand(w.p.x,w.p.y,w.mode,wandOpts(Ed.toolOptions(this.id)));},
  cancelAll(){this.w=null;this.mv=null;redraw();},
  drawOverlay(ctx,v){if(this.mv)drawMove(ctx,v,this.mv);}});

// ----- Select menu: Modify, Grow, Similar ---------------------------------------------------------------------------------------
function needSel(){if(Ed.select.hasSelection())return true;Ed.message('Select an area first (Ctrl+A selects everything).');return false;}
function modify(label,fn){if(!Ed.doc||!needSel())return false;const W=DW(),H=DH(),m=selectionMask(),out=fn(m,W,H);return applySel(out,'new',label);}
// A command with a dialog: Ed.menu.run(path, params) skips it; the dialog remembers the last values, as Photoshop does.
function modifyDialog(title,fields,run){return p=>{if(p&&typeof p==='object'&&Object.keys(p).length)return run(p);if(!needSel())return false;
  const key='ed-sel-dlg:'+title,values=U.store.get(key,{});return Ed.dialog({title,fields,values,ok:q=>{U.store.set(key,q);return run(q);}});};}
const boundsField={key:'atBounds',label:'Apply effect at canvas bounds',type:'checkbox',default:false,title:'Also treat the edge of the canvas as the edge of the selection'};
const M=(path,def)=>Ed.registerMenu('Select/'+path,def);
const hasSel=()=>!!Ed.doc&&Ed.select.hasSelection();
M('Modify/Border...',{order:71,enabled:hasSel,action:modifyDialog('Border Selection',[{key:'width',label:'Width',type:'number',min:1,max:200,step:1,unit:'px',default:10}],
  p=>modify('Border',(m,W,H)=>borderMask(m,W,H,clamp(Math.round(num(p.width,10)),1,200))))});
M('Modify/Smooth...',{order:72,enabled:hasSel,action:modifyDialog('Smooth Selection',[{key:'radius',label:'Sample Radius',type:'number',min:1,max:500,step:1,unit:'px',default:2},boundsField],
  p=>modify('Smooth',(m,W,H)=>smoothMask(m,W,H,clamp(Math.round(num(p.radius,2)),1,500),!!p.atBounds)))});
M('Modify/Expand...',{order:73,enabled:hasSel,action:modifyDialog('Expand Selection',[{key:'by',label:'Expand By',type:'number',min:1,max:500,step:1,unit:'px',default:1}],
  p=>modify('Expand',(m,W,H)=>expandMask(m,W,H,clamp(Math.round(num(p.by,1)),1,500))))});
M('Modify/Contract...',{order:74,enabled:hasSel,action:modifyDialog('Contract Selection',[{key:'by',label:'Contract By',type:'number',min:1,max:500,step:1,unit:'px',default:1},boundsField],
  p=>modify('Contract',(m,W,H)=>contractMask(m,W,H,clamp(Math.round(num(p.by,1)),1,500),!!p.atBounds)))});
M('Modify/Feather...',{order:75,shortcut:'Shift+F6',enabled:hasSel,action:modifyDialog('Feather Selection',[{key:'radius',label:'Feather Radius',type:'number',min:.1,max:1000,step:.1,unit:'px',default:2},boundsField],
  p=>modify('Feather',(m,W,H)=>gaussMask(m,W,H,clamp(num(p.radius,2),.1,1000),!!p.atBounds)))});
// Grow and Similar use the Magic Wand's Tolerance, Anti-alias and Sample All Layers.
function growSimilar(contiguous){if(!Ed.doc||!needSel())return false;const W=DW(),H=DH(),o=wandOpts(Ed.toolOptions('magic-wand')),sel=selectionMask(),px=premul(sourcePixels(o.sampleAll));
  const cs=colorSet(px,W,H,sel,o.tolerance);if(!cs)return false;const n=W*H,out=new Uint8Array(n);
  if(contiguous){const match=new Uint8Array(n);for(let i=0,j=0;i<n;i++,j+=4)match[i]=sel[i]>=128||inSet(cs,px,j)?1:0;
    for(let i=0;i<n;i++)if(sel[i]>=128&&!out[i])flood(match,W,H,i%W,(i/W)|0,out);}
  else for(let i=0,j=0;i<n;i++,j+=4)if(sel[i]>=128||inSet(cs,px,j))out[i]=255;
  let m=o.antialias?antialias(out,W,H):out;for(let i=0;i<n;i++)if(sel[i]>m[i])m[i]=sel[i];
  return applySel(m,'new',contiguous?'Grow':'Similar');}
M('Grow',{order:80,separatorBefore:true,enabled:hasSel,action:()=>growSimilar(true)});
M('Similar',{order:81,enabled:hasSel,action:()=>growSimilar(false)});

// ----- module plumbing ------------------------------------------------------------------------------------------------------------
// Key releases reach the active selection tool (Space, Shift and Alt during drags and lassos).
document.addEventListener('keyup',e=>{const t=Ed.tools.get(Ed.tool);if(t&&typeof t.onKeyUp==='function'){try{t.onKeyUp(e);}catch(x){report(x,'keyup');}}});
Ed.on('open',()=>{selCache=null;for(const id of Object.keys(LABELS)){const t=Ed.tools.get(id);if(t&&t.cancelAll)try{t.cancelAll();}catch(e){}}});
Ed.on('selection',()=>{selCache=null;});
})();
