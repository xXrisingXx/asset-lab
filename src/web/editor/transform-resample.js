'use strict';
// Resampling for the Asset Lab editor's transforms (editor/transform.js): affine and perspective (projective) maps of
// RGBA pixels or of a mask, with Nearest Neighbor, Bilinear or Bicubic (Keys, a = -0.5) interpolation.
// - Colors are interpolated premultiplied by alpha, so transparent pixels never bleed dark fringes into edges.
// - Reductions are filtered like Pillow's resize (and Photoshop): the scale part of the map is applied first with a
//   separable convolution whose support grows with the reduction, then the rest (rotation, skew, perspective) is
//   point-sampled. Without that, a sheet shrunk to an icon would sparkle with aliasing.
// - Outside the source is transparent (pixels) or the mask's own default value (masks: white outside a "reveal all").
// The same file runs as a same-origin Web Worker (transform.js posts {id, cmd, ...} messages) and as a page script,
// where it defines window.EdResample for synchronous use.
(function(root){
const KERNELS={
  bilinear:{support:1,f(x){x=x<0?-x:x;return x<1?1-x:0;}},
  bicubic:{support:2,f(x){x=x<0?-x:x;return x<1?(1.5*x-2.5)*x*x+1:x<2?((-.5*x+2.5)*x-4)*x+2:0;}}};

// ----- 3 x 3 matrices, row-major: x' = (m0 x + m1 y + m2) / (m6 x + m7 y + m8), y' = (m3 x + m4 y + m5) / (...) -----------
function inv3(m){const[a,b,c,d,e,f,g,h,i]=m,A=e*i-f*h,B=f*g-d*i,C=d*h-e*g,det=a*A+b*B+c*C;if(!det||!isFinite(det))return null;
  return[A/det,(c*h-b*i)/det,(b*f-c*e)/det,B/det,(a*i-c*g)/det,(c*d-a*f)/det,C/det,(b*g-a*h)/det,(a*e-b*d)/det];}
function mul3(p,q){const r=new Array(9);for(let i=0;i<3;i++)for(let j=0;j<3;j++)r[i*3+j]=p[i*3]*q[j]+p[i*3+1]*q[3+j]+p[i*3+2]*q[6+j];return r;}
const affine=m=>Math.abs(m[6])<1e-12&&Math.abs(m[7])<1e-12;
function normalize(m){const s=m[8];return s&&s!==1?m.map(v=>v/s):m.slice();}
// The local linear part of the forward map at (x, y): [[dx'/dx, dx'/dy], [dy'/dx, dy'/dy]].
function jacobian(m,x,y){const w=m[6]*x+m[7]*y+m[8],u=(m[0]*x+m[1]*y+m[2])/w,v=(m[3]*x+m[4]*y+m[5])/w;
  return[(m[0]-m[6]*u)/w,(m[1]-m[7]*u)/w,(m[3]-m[6]*v)/w,(m[4]-m[7]*v)/w];}
// How much the map shrinks the source along its own x and y axes (1 = not reduced): the scale part of the canonical
// decomposition J = R(angle) . [[sx, k sy], [0, sy]] (rotation, horizontal skew, scale), as Photoshop reports W and H.
function reduction(m,w,h){const J=jacobian(m,w/2,h/2),sx=Math.hypot(J[0],J[2]),det=Math.abs(J[0]*J[3]-J[1]*J[2]),sy=sx?det/sx:0;
  return[Math.min(1,sx||1),Math.min(1,sy||1)];}

// ----- pixels in and out -----------------------------------------------------------------------------------------------
// RGBA bytes (straight alpha, as getImageData gives them) -> premultiplied floats; a mask keeps only its alpha.
function planeOf(src,n,mask){if(mask){const P=new Float32Array(n);for(let i=0;i<n;i++)P[i]=src[i*4+3];return P;}
  const P=new Float32Array(n*4);for(let i=0,j=0;i<n;i++,j+=4){const a=src[j+3];if(!a)continue;const k=a/255;P[j]=src[j]*k;P[j+1]=src[j+1]*k;P[j+2]=src[j+2]*k;P[j+3]=a;}return P;}
function store(out,o,r,g,b,a){ // premultiplied floats -> straight RGBA bytes
  if(a<=.5){out[o]=out[o+1]=out[o+2]=out[o+3]=0;return;}if(a>255)a=255;const k=255/a;
  out[o]=r*k;out[o+1]=g*k;out[o+2]=b*k;out[o+3]=a;}   // Uint8ClampedArray rounds half to even and clamps

// ----- separable reduction (Pillow's resize coefficients; outside the source counts as `edge`) ----------------------------
function coefficients(inSize,outSize,scale,K){const fs=1/scale,support=K.support*fs,ss=scale,list=[];
  for(let o=0;o<outSize;o++){const center=(o+.5)*fs,lo=Math.floor(center-support+.5),hi=Math.floor(center+support+.5);
    const w=new Float64Array(Math.max(0,hi-lo));let total=0;for(let x=lo;x<hi;x++){const v=K.f((x-center+.5)*ss);w[x-lo]=v;total+=v;}
    if(total)for(let k=0;k<w.length;k++)w[k]/=total;list.push({lo,w});}
  return list;}
function reduce(P,w,h,ch,sx,sy,K,edge){
  let cur=P,cw=w,ch2=h;
  if(sx<1){const ow=Math.max(1,Math.ceil(w*sx-1e-9)),cs=coefficients(w,ow,sx,K),out=new Float32Array(ow*ch2*ch);
    for(let y=0;y<ch2;y++){const row=y*cw*ch,orow=y*ow*ch;
      for(let ox=0;ox<ow;ox++){const {lo,w:wt}=cs[ox];let r=0,g=0,b=0,a=0,e=0;
        for(let k=0;k<wt.length;k++){const x=lo+k,v=wt[k];if(!v)continue;if(x<0||x>=cw){e+=v;continue;}const i=row+x*ch;
          if(ch===4){r+=cur[i]*v;g+=cur[i+1]*v;b+=cur[i+2]*v;a+=cur[i+3]*v;}else a+=cur[i]*v;}
        const o=orow+ox*ch;if(ch===4){out[o]=r;out[o+1]=g;out[o+2]=b;out[o+3]=a;}else out[o]=a+e*edge;}}
    cur=out;cw=ow;}
  if(sy<1){const oh=Math.max(1,Math.ceil(h*sy-1e-9)),cs=coefficients(h,oh,sy,K),out=new Float32Array(cw*oh*ch),stride=cw*ch;
    for(let oy=0;oy<oh;oy++){const {lo,w:wt}=cs[oy],orow=oy*stride;
      for(let k=0;k<wt.length;k++){const y=lo+k,v=wt[k];if(!v)continue;
        if(y<0||y>=ch2){if(ch===1&&edge)for(let x=0;x<cw;x++)out[orow+x]+=v*edge;continue;}
        const row=y*stride;for(let i=0;i<stride;i++)out[orow+i]+=cur[row+i]*v;}}
    cur=out;ch2=oh;}
  return{P:cur,w:cw,h:ch2};}

// ----- point sampling of the inverse map over the destination rectangle ------------------------------------------------
function sample(P,w,h,ch,edge,inv,dx,dy,dw,dh,interp){
  const out=new Uint8ClampedArray(dw*dh*4),K=KERNELS[interp],aff=affine(inv);const[a,b,c,d,e,f,g,hh,ii]=inv;
  const wx=new Float64Array(4),wy=new Float64Array(4),mask=ch===1;
  if(mask)for(let i=0;i<out.length;i+=4){out[i]=out[i+1]=out[i+2]=255;}
  for(let y=0;y<dh;y++){const py=dy+y+.5;
    for(let x=0;x<dw;x++){const px=dx+x+.5,o=(y*dw+x)*4;let qx,qy;
      if(aff){qx=a*px+b*py+c;qy=d*px+e*py+f;}else{const t=g*px+hh*py+ii;if(t<=1e-12){if(mask)out[o+3]=edge;continue;}qx=(a*px+b*py+c)/t;qy=(d*px+e*py+f)/t;}
      if(!K){ // nearest neighbor: the pixel that contains the point (a 1e-9 nudge keeps exact ties exact after float error)
        const ix=Math.floor(qx+1e-9),iy=Math.floor(qy+1e-9);
        if(ix<0||iy<0||ix>=w||iy>=h){if(mask)out[o+3]=edge;continue;}const i=(iy*w+ix)*ch;
        if(mask)out[o+3]=P[i];else store(out,o,P[i],P[i+1],P[i+2],P[i+3]);continue;}
      const u=qx-.5,v=qy-.5,s=K.support;
      if(u<-s-1||v<-s-1||u>w+s||v>h+s){if(mask)out[o+3]=edge;continue;}
      const ix=Math.floor(u),iy=Math.floor(v),fx=u-ix,fy=v-iy,n=2*s,x0=ix-s+1,y0=iy-s+1;
      for(let k=0;k<n;k++){wx[k]=K.f(fx+s-1-k);wy[k]=K.f(fy+s-1-k);}
      let r=0,gg=0,bb=0,al=0;
      for(let ky=0;ky<n;ky++){const yy=y0+ky,vy=wy[ky];if(!vy)continue;
        if(yy<0||yy>=h){if(mask)al+=vy*edge;continue;}const row=yy*w;
        for(let kx=0;kx<n;kx++){const xx=x0+kx,vv=wx[kx]*vy;if(!vv)continue;
          if(xx<0||xx>=w){if(mask)al+=vv*edge;continue;}const i=(row+xx)*ch;
          if(mask)al+=P[i]*vv;else{r+=P[i]*vv;gg+=P[i+1]*vv;bb+=P[i+2]*vv;al+=P[i+3]*vv;}}}
      if(mask)out[o+3]=al;else store(out,o,r,gg,bb,al);}}
  return out;}

// ----- the whole transform -----------------------------------------------------------------------------------------------
// o = {src: RGBA bytes of the source region, sw, sh, M: forward 3x3 map from source-region pixels to document pixels,
//      dx, dy, dw, dh: the document rectangle to compute, interp: 'nearest'|'bilinear'|'bicubic', mask: alpha only,
//      edge: the mask value outside the source (0..255)} -> RGBA bytes of dw x dh (masks: RGB white, value in alpha).
function transform(o){
  const mask=!!o.mask,ch=mask?1:4,edge=mask?+o.edge||0:0,interp=KERNELS[o.interp]?o.interp:'nearest';
  let P=planeOf(o.src,o.sw*o.sh,mask),w=o.sw,h=o.sh,M=normalize(o.M);
  if(interp!=='nearest'){let[sx,sy]=reduction(M,w,h);if(sx>=.999)sx=1;if(sy>=.999)sy=1;
    // The reduced image's pixel i covers source [i / s, (i + 1) / s): its coordinates are the source's times s.
    if(sx<1||sy<1){const r=reduce(P,w,h,ch,sx,sy,KERNELS[interp],edge);M=mul3(M,[1/sx,0,0,0,1/sy,0,0,0,1]);P=r.P;w=r.w;h=r.h;}}
  const inv=inv3(M);
  if(!inv){const out=new Uint8ClampedArray(o.dw*o.dh*4);if(mask)for(let i=0;i<out.length;i+=4){out[i]=out[i+1]=out[i+2]=255;out[i+3]=edge;}return out;}
  return sample(P,w,h,ch,edge,inv,o.dx,o.dy,o.dw,o.dh,interp);}

const api={transform,inv3,mul3,affine,jacobian,reduction,KERNELS};
const worker=typeof WorkerGlobalScope!=='undefined'&&root instanceof WorkerGlobalScope;
if(worker){
  const sources=new Map();   // key -> {src, sw, sh}: the session's source stays here between previews
  root.onmessage=ev=>{const m=ev.data||{};try{
      if(m.cmd==='source'){sources.set(m.key,{src:m.src,sw:m.sw,sh:m.sh});root.postMessage({id:m.id,ok:true});return;}
      if(m.cmd==='drop'){sources.delete(m.key);root.postMessage({id:m.id,ok:true});return;}
      if(m.cmd==='transform'){const s=m.key?sources.get(m.key):{src:m.src,sw:m.sw,sh:m.sh};if(!s)throw Error('The transform source is gone.');
        const out=transform({...m,src:s.src,sw:s.sw,sh:s.sh});root.postMessage({id:m.id,out},[out.buffer]);return;}
      throw Error('Unknown command '+m.cmd);}
    catch(e){root.postMessage({id:m.id,error:String(e&&e.message||e)});}};}
else root.EdResample=api;
})(self);
