'use strict';
/* =====================================================================================================================
   LAYER STYLES  (editor/styles.js)  ·  Photoshop's layer effects and the Layer Style dialog. Built on window.Ed only
   (editor/core.js documents it); the editor works without this file.

   EFFECTS (Ed.registerEffect types, stored in layer.effects, saved with the document)
     'stroke'            Stroke            size 1..100 px, position outside|inside|center, blend, opacity %, color
     'inner-shadow'      Inner Shadow      blend, color, opacity, angle, distance, choke %, size
     'inner-glow'        Inner Glow        blend, color, opacity, choke %, size (glows in from the edges)
     'color-overlay'     Color Overlay     blend, color, opacity
     'gradient-overlay'  Gradient Overlay  blend, opacity, gradient {stops:[{at, color, mid}], alpha:[{at, a, mid}]} (the
                                           fill.js format), reverse, style linear|radial, align (with the layer's bounds,
                                           else the canvas), angle, scale %
     'outer-glow'        Outer Glow        blend, color, opacity, spread %, size
     'drop-shadow'       Drop Shadow       blend, color, opacity, angle, distance, spread %, size
     Every params object may carry enabled:false (kept but not drawn). Missing or damaged values fall back to defaults.

   THE MATH (Photoshop's model; all on the CPU so a saved style draws the same on every PC and reopens exactly)
     Shape = the layer's masked pixels (Fill not applied: effects ignore Fill). a = its alpha.
     Distance to the outline: an exact Euclidean distance transform (Felzenszwalb) to the nearest pixel on the other side
       of the 50% outline, corrected by that pixel's alpha so anti-aliased edges keep their sub-pixel position:
       outside d = |p - q| - (a(q) - .5) over pixels q with a >= 50%; inside d = |p - q| - (.5 - a(q)) over the others and
       everything beyond the canvas. Pixels under 50% alpha therefore count as outside: a layer that is all under 50%
       (faint glass) gets no outside stroke, spread or choke.
     Dilation by r (outside stroke, spread): max(a, clamp(r + .5 - d_out)), i.e. the outline pushed out by a disc of
       radius r with an anti-aliased rim. Erosion by r (inside stroke, choke): min(a, clamp(d_in - r + .5)).
     Size = blur: a Gaussian with sigma = (size - spread px) / 2 (CSS box-shadow's convention, which Photoshop's CSS
       export uses), done as three extended box passes (Gwosdek et al.) that match its variance exactly; within 3/255 of
       a true Gaussian. Spread / choke % = the part of Size used to dilate / erode the shape before the blur.
     Angle (light direction, 120 = from the upper left) moves shadows away from the light by Distance (whole pixels).
     Compositing: Drop Shadow and Outer Glow go under the layer, knocked out by the shape (alpha m (1 - a) / (1 - a Fill)
       so the uncovered part of an edge pixel shows them and Fill 0 leaves them outside only). Overlays, inner effects and
       the Stroke are composited inside the layer before it meets what is below (Photoshop's order: gradient overlay,
       color overlay, inner glow, inner shadow, stroke): an inner effect with coverage k gives the layer k a X + (1 - k) L,
       so a 100% Color Overlay recolors anti-aliased edges cleanly; an outside stroke covers the part of each pixel the
       shape leaves free. Blend modes blend the effect with what is under it. For a clipping-group base or a layer whose
       own blend mode is not Normal the inner effects and the stroke are drawn over the layer instead (alpha k a).
     Gradient Overlay: linear t = ((p - c) . u) / L + .5 with u at Angle (90 = bottom to top), c the centre and
       L = (|w cos| + |h sin|) x Scale of the layer's bounds (or the canvas); radial t = |p - c| / (L / 2).

   UI  Layer > Layer Style > Blending Options... (blend mode, opacity, fill opacity) and one item per effect open the Layer
     Style dialog: the effects with checkboxes on the left, the selected one's settings on the right, live preview on
     the canvas; OK = one history state 'Layer Style', Cancel puts everything back. Also Copy / Paste / Clear Layer Style
     and Hide / Show All Effects (one state each), and an 'fx' badge on styled layers in the Layers panel (layers.js opens
     Blending Options when a row is double-clicked).

   PUBLIC API (feature-detect it)
     Ed.layerStyle = {open(layerRef?, effectType?) -> Promise<bool> (OK), copy() -> bool, paste(refs?) -> bool,
       clear(refs?) -> bool, hideAll() / showAll() -> bool, types() -> [{type, label}], defaults(type) -> params,
       has(layerRef?) -> bool (any effect), dialog (the open Layer Style <dialog> or null)}
     Menu commands run with params skip the dialog: Ed.menu.run('Layer/Layer Style/Stroke...', {size: 2}) merges them into
     the active layer's Stroke (switched on); 'Blending Options...' takes {blend, opacity, fill} (opacity / fill in %).
   ===================================================================================================================== */
(()=>{
const Ed=window.Ed;if(!Ed||!Ed.registerEffect||!Ed.layers||!Ed.util)return;
const U=Ed.util,clamp=U.clamp;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const btn=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title)b.title=title;return b;};
const isHex=v=>/^#[0-9a-f]{6}$/i.test(String(v||''));
const clone=v=>v==null?v:JSON.parse(JSON.stringify(v));
const rgbOf=h=>U.rgbOf(h);
const num=(v,d)=>{const n=Number(v);return Number.isFinite(n)?n:d;};

// ----- effect specifications ---------------------------------------------------------------------------------------------
const BLEND={key:'blend',label:'Blend Mode',type:'select'};
const OPACITY={key:'opacity',label:'Opacity',type:'range',min:0,max:100,step:1,unit:'%'};
const COLOR={key:'color',label:'Color',type:'color'};
const ANGLE={key:'angle',label:'Angle',type:'range',min:-180,max:180,step:1,unit:'°'};
const DISTANCE={key:'distance',label:'Distance',type:'range',min:0,max:200,step:1,unit:'px'};
const SPREAD={key:'spread',label:'Spread',type:'range',min:0,max:100,step:1,unit:'%'};
const CHOKE={key:'choke',label:'Choke',type:'range',min:0,max:100,step:1,unit:'%'};
const SIZE={key:'size',label:'Size',type:'range',min:0,max:150,step:1,unit:'px'};
const BW={stops:[{at:0,color:'#000000',mid:.5},{at:1,color:'#ffffff',mid:.5}],alpha:[{at:0,a:1,mid:.5},{at:1,a:1,mid:.5}]};
// Listed in the dialog in Photoshop's order (top to bottom).
const SPEC={
  'stroke':{label:'Stroke',stage:'over',order:100,defaults:{size:3,position:'outside',blend:'normal',opacity:100,color:'#000000'},
    fields:[{...SIZE,min:1,max:100},{key:'position',label:'Position',type:'select',options:[{value:'outside',label:'Outside'},{value:'inside',label:'Inside'},{value:'center',label:'Center'}]},BLEND,OPACITY,COLOR]},
  'inner-shadow':{label:'Inner Shadow',stage:'inner',order:80,defaults:{blend:'multiply',color:'#000000',opacity:75,angle:120,distance:5,choke:0,size:5},
    fields:[BLEND,COLOR,OPACITY,ANGLE,DISTANCE,CHOKE,SIZE]},
  'inner-glow':{label:'Inner Glow',stage:'inner',order:70,defaults:{blend:'screen',color:'#ffffbe',opacity:75,choke:0,size:5},
    fields:[BLEND,COLOR,OPACITY,CHOKE,SIZE]},
  'color-overlay':{label:'Color Overlay',stage:'inner',order:50,defaults:{blend:'normal',color:'#ff0000',opacity:100},fields:[BLEND,COLOR,OPACITY]},
  'gradient-overlay':{label:'Gradient Overlay',stage:'inner',order:40,defaults:{blend:'normal',opacity:100,gradient:BW,reverse:false,style:'linear',align:true,angle:90,scale:100},
    fields:[BLEND,OPACITY,{key:'gradient',label:'Gradient',type:'gradient'},{key:'reverse',label:'Reverse',type:'checkbox'},
      {key:'style',label:'Style',type:'select',options:[{value:'linear',label:'Linear'},{value:'radial',label:'Radial'}]},{key:'align',label:'Align with Layer',type:'checkbox'},
      ANGLE,{key:'scale',label:'Scale',type:'range',min:10,max:150,step:1,unit:'%'}]},
  'outer-glow':{label:'Outer Glow',stage:'behind',order:20,defaults:{blend:'screen',color:'#ffffbe',opacity:75,spread:0,size:5},fields:[BLEND,COLOR,OPACITY,SPREAD,SIZE]},
  'drop-shadow':{label:'Drop Shadow',stage:'behind',order:10,defaults:{blend:'multiply',color:'#000000',opacity:75,angle:120,distance:5,spread:0,size:5},
    fields:[BLEND,COLOR,OPACITY,ANGLE,DISTANCE,SPREAD,SIZE]}};
const TYPES=Object.keys(SPEC);

function normGradient(g){const d=clone(BW);if(!g||typeof g!=='object')return d;
  const stops=(Array.isArray(g.stops)?g.stops:[]).filter(s=>s&&isHex(s.color)).slice(0,64).map(s=>({at:clamp(num(s.at,0),0,1),color:String(s.color).toLowerCase(),mid:clamp(num(s.mid,.5),0,1)}));
  const alpha=(Array.isArray(g.alpha)?g.alpha:[]).filter(s=>s&&typeof s==='object').slice(0,64).map(s=>({at:clamp(num(s.at,0),0,1),a:clamp(num(s.a,1),0,1),mid:clamp(num(s.mid,.5),0,1)}));
  const out={stops:stops.length?stops.sort((a,b)=>a.at-b.at):d.stops,alpha:alpha.length?alpha.sort((a,b)=>a.at-b.at):d.alpha};if(g.name&&typeof g.name==='string')out.name=g.name.slice(0,80);return out;}
// Clean params for drawing and storing: defaults for anything missing or out of range.
function norm(type,p){const s=SPEC[type],o={...s.defaults};p=p&&typeof p==='object'?p:{};
  for(const f of s.fields){const v=p[f.key];if(v===undefined)continue;
    if(f.type==='range')o[f.key]=clamp(num(v,o[f.key]),f.min,f.max);
    else if(f.type==='select'&&f.key!=='blend'){if(f.options.some(x=>x.value===v))o[f.key]=v;}
    else if(f.key==='blend'){if(typeof v==='string'&&v)o.blend=v;}
    else if(f.type==='color'){if(isHex(v))o[f.key]=String(v).toLowerCase();}
    else if(f.type==='checkbox')o[f.key]=!!v;
    else if(f.type==='gradient')o[f.key]=normGradient(v);}
  o.enabled=p.enabled!==false;if(p.hiddenAll&&p.enabled===false)o.hiddenAll=true;return o;}

// ----- masks: distance fields, dilation, erosion and blur ------------------------------------------------------------------
const FAR=1e20;
// Euclidean distance transform (Felzenszwalb & Huttenlocher): for every cell the distance to the nearest feature cell
// and that cell's index (-1 when there is none).
function edt(feat,w,h){const n=w*h,cd=new Float64Array(n),cf=new Int32Array(n);
  for(let x=0;x<w;x++){let last=-1;for(let y=0,i=x;y<h;y++,i+=w){if(feat[i])last=y;cf[i]=last;}
    let next=-1;for(let y=h-1,i=x+(h-1)*w;y>=0;y--,i-=w){if(feat[i])next=y;const up=cf[i];let best=-1,bd=FAR;if(up>=0){best=up;bd=y-up;}if(next>=0&&next-y<bd){best=next;bd=next-y;}
      cf[i]=best;cd[i]=best<0?FAR:bd*bd;}}
  const dist=new Float32Array(n),f=new Int32Array(n),v=new Int32Array(w),z=new Float64Array(w+1);
  for(let y=0,o=0;y<h;y++,o+=w){let k=-1;
    for(let q=0;q<w;q++){const fq=cd[o+q];if(fq>=FAR)continue;
      if(k<0){k=0;v[0]=q;z[0]=-Infinity;z[1]=Infinity;continue;}
      let s=0;for(;;){const p=v[k];s=((fq+q*q)-(cd[o+p]+p*p))/(2*q-2*p);if(s<=z[k]&&k>0)k--;else break;}
      if(s<=z[k]){v[k]=q;z[k+1]=Infinity;}else{k++;v[k]=q;z[k]=s;z[k+1]=Infinity;}}
    if(k<0){for(let x=0;x<w;x++){dist[o+x]=FAR;f[o+x]=-1;}continue;}
    for(let x=0,j=0;x<w;x++){while(z[j+1]<x)j++;const q=v[j],dx=x-q;dist[o+x]=Math.sqrt(dx*dx+cd[o+q]);f[o+x]=cf[o+q]*w+q;}}
  return{dist,f};}
// Distance from pixel centres to the 50% outline: to the pixels on the other side, less how far each one's alpha puts the
// outline from its centre. With soft edges the nearest centre is not always the nearest outline, so the nearest features
// of the 24 cells around are tried too (only up to `limit`, beyond which no effect looks).
function outline(A,w,h,inside,limit){const n=w*h,feat=new Uint8Array(n);let soft=false;
  for(let i=0;i<n;i++){const a=A[i];feat[i]=inside?a<128:a>=128;if(a&&a<255)soft=true;}
  const{dist,f}=edt(feat,w,h),corr=q=>inside?.5-A[q]/255:A[q]/255-.5;
  if(!soft){for(let i=0;i<n;i++)dist[i]=f[i]<0?FAR:dist[i]-.5;return dist;}
  const out=new Float32Array(n);
  for(let y=0,i=0;y<h;y++)for(let x=0;x<w;x++,i++){if(f[i]<0||dist[i]>limit+2){out[i]=f[i]<0?FAR:dist[i]-corr(f[i]);continue;}let best=FAR;
    for(let dy=-2;dy<=2;dy++){const yy=y+dy;if(yy<0||yy>=h)continue;for(let dx=-2;dx<=2;dx++){const xx=x+dx;if(xx<0||xx>=w)continue;const q=f[yy*w+xx];if(q<0)continue;
      const qx=q%w,qy=(q-qx)/w,d=Math.sqrt((qx-x)*(qx-x)+(qy-y)*(qy-y))-corr(q);if(d<best)best=d;}}
    out[i]=best;}
  return out;}
// Extended box blur: three passes of a box with fractional end weights whose variance adds up to sigma^2.
function boxPlan(sigma){const v=sigma*sigma/3,r=Math.floor((Math.sqrt(12*v+1)-1)/2),a=clamp((r*(r+1)*(2*r+1)/3-v*(2*r+1))/(2*v-2*(r+1)*(r+1)),0,1);return{r,a,k:1/(2*r+1+2*a)};}
const reach=sigma=>sigma>.05?3*(boxPlan(sigma).r+1):0;
// One box pass along rows (hpass) or columns (vpass); `out` stands for everything beyond the grid. The middle of each line
// runs without bounds checks.
function hpass(S,D,w,h,r,a,k,out){const lo=Math.min(w,r+1),hi=Math.max(lo,w-r-1);
  for(let y=0;y<h;y++){const o=y*w;let s=0;for(let i=-r;i<=r;i++)s+=i<0||i>=w?out:S[o+i];
    for(let x=0;x<lo;x++){const l=x-r-1,e=x+r+1,q=x-r,Lv=l<0?out:S[o+l],Rv=e>=w?out:S[o+e];D[o+x]=(s+a*(Lv+Rv))*k;s+=Rv-(q<0?out:S[o+q]);}
    for(let x=lo;x<hi;x++){const Rv=S[o+x+r+1];D[o+x]=(s+a*(S[o+x-r-1]+Rv))*k;s+=Rv-S[o+x-r];}
    for(let x=hi;x<w;x++){const l=x-r-1,e=x+r+1,q=x-r,Lv=l<0?out:S[o+l],Rv=e>=w?out:S[o+e];D[o+x]=(s+a*(Lv+Rv))*k;s+=Rv-(q<0?out:S[o+q]);}}}
function vpass(S,D,w,h,r,a,k,out){const s=new Float64Array(w),T=new Float32Array(w).fill(out);
  for(let i=-r;i<=r;i++){if(i<0||i>=h)for(let x=0;x<w;x++)s[x]+=out;else{const o=i*w;for(let x=0;x<w;x++)s[x]+=S[o+x];}}
  const row=i=>i<0||i>=h?null:i*w;
  for(let y=0;y<h;y++){const ot=row(y-r-1),ob=row(y+r+1),oq=row(y-r),o=y*w;
    if(ot!==null&&ob!==null&&oq!==null){for(let x=0;x<w;x++){const B=S[ob+x];D[o+x]=(s[x]+a*(S[ot+x]+B))*k;s[x]+=B-S[oq+x];}continue;}
    const St=ot===null?T:S,Sb=ob===null?T:S,Sq=oq===null?T:S,bt=ot===null?0:ot,bb=ob===null?0:ob,bq=oq===null?0:oq;
    for(let x=0;x<w;x++){const B=Sb[bb+x];D[o+x]=(s[x]+a*(St[bt+x]+B))*k;s[x]+=B-Sq[bq+x];}}}
function blur(src,w,h,sigma,out=0){if(!(sigma>.05))return src;const{r,a,k}=boxPlan(sigma);let A=Float32Array.from(src),B=new Float32Array(w*h);
  for(let p=0;p<3;p++){hpass(A,B,w,h,r,a,k,out);[A,B]=[B,A];}
  for(let p=0;p<3;p++){vpass(A,B,w,h,r,a,k,out);[A,B]=[B,A];}
  return A;}

// The layer's pixels around a region, shared by the effects of one layer drawn in one pass (behind, content, inner, over).
// A pass ends when another layer or region comes, or an effect type comes again (the same layer drawn a second time).
let pass=null;
function passOf(info,type){const R=info.region;
  if(!pass||pass.layer!==info.layer||pass.done.has(type)||pass.R.x!==R.x||pass.R.y!==R.y||pass.R.w!==R.w||pass.R.h!==R.h){
    if(!pass)queueMicrotask(()=>{pass=null;});pass={layer:info.layer,R:{...R},done:new Set(),fields:[]};}
  pass.done.add(type);return pass;}
function fieldOf(info,pad){const R=info.region,G={x:R.x-pad,y:R.y-pad,w:R.w+2*pad,h:R.h+2*pad},cache=pass&&pass.layer===info.layer?pass.fields:[];
  for(const c of cache)if(c.src===info.source&&c.G.x===G.x&&c.G.y===G.y&&c.G.w===G.w&&c.G.h===G.h)return c;
  const n=G.w*G.h,A=new Uint8Array(n),Ri=U.area(G);let img=null;
  if(Ri){img=U.read(info.source,Ri).data;for(let y=0;y<Ri.h;y++){const s=y*Ri.w*4,d=(Ri.y-G.y+y)*G.w+(Ri.x-G.x);for(let x=0;x<Ri.w;x++)A[d+x]=img[s+x*4+3];}}
  const F={src:info.source,G,A,img,Ri,_out:null,_in:null,
    get dOut(){return this._out||(this._out=outline(A,G.w,G.h,false,pad));},
    get dIn(){return this._in||(this._in=outline(A,G.w,G.h,true,pad));}};
  cache.push(F);return F;}
// Dilation by r (spread) and the inverse of the erosion by r (choke), over the whole grid, 0..1.
function dilated(F,r){const A=F.A,n=A.length,o=new Float32Array(n);if(r<=0){for(let i=0;i<n;i++)o[i]=A[i]/255;return o;}
  const d=F.dOut;for(let i=0;i<n;i++){const a=A[i]/255,v=r+.5-d[i];o[i]=v>=1?1:v>a?v:a;}return o;}
function erodedInverse(F,r){const A=F.A,n=A.length,o=new Float32Array(n);if(r<=0){for(let i=0;i<n;i++)o[i]=1-A[i]/255;return o;}
  const d=F.dIn;for(let i=0;i<n;i++){const a=A[i]/255,v=d[i]-r+.5,e=v<=0?0:v<a?v:a;o[i]=1-e;}return o;}
const offsetOf=p=>{const t=p.angle*Math.PI/180;return[Math.round(-p.distance*Math.cos(t)),Math.round(p.distance*Math.sin(t))];};
const sizePad=(size,part)=>{const k=size*part/100;return Math.ceil(k)+reach((size-k)/2)+2;};

// ----- compositing ----------------------------------------------------------------------------------------------------------
// W3C general formula onto unpremultiplied bytes (as the core does for its per-pixel blend modes).
function blendPixels(dst,src,fn){const cb=[0,0,0],cs=[0,0,0],o=[0,0,0];
  for(let i=0;i<dst.length;i+=4){const as=src[i+3]/255;if(as<=0)continue;const ab=dst[i+3]/255;
    for(let c=0;c<3;c++){cs[c]=src[i+c]/255;cb[c]=dst[i+c]/255;}if(ab>0)fn(cb,cs,o);else{o[0]=cs[0];o[1]=cs[1];o[2]=cs[2];}
    const ao=as+ab*(1-as);for(let c=0;c<3;c++)dst[i+c]=Math.round(255*(as*(1-ab)*cs[c]+as*ab*clamp(o[c],0,1)+(1-as)*ab*cb[c])/ao);dst[i+3]=Math.round(255*ao);}}
const blendDef=id=>Ed.blends.get(id)||Ed.blends.get('normal');
// Draw an R-sized unpremultiplied image (color + coverage in alpha) onto ctx in blend mode `mode`.
function paint(ctx,info,R,img,mode){const b=blendDef(mode);
  if(b.op||b.id==='normal'||(!b.fn&&!b.composite)){const t=info.temp();U.write(t,img,R);ctx.save();ctx.setTransform(1,0,0,1,0,0);ctx.globalAlpha=1;ctx.globalCompositeOperation=b.op||'source-over';
    ctx.drawImage(t,R.x,R.y,R.w,R.h,R.x,R.y,R.w,R.h);ctx.restore();info.free(t);return;}
  const D=U.read(ctx.canvas,R);if(b.fn)blendPixels(D.data,img.data,b.fn);else b.composite(D,img,{region:R,alpha:1,doc:Ed.doc});U.write(ctx.canvas,D,R);}

// ----- the effects -----------------------------------------------------------------------------------------------------------
const mineRender=new Map();
const enabledMine=L=>Object.entries(L.effects||{}).filter(([t,p])=>p&&p.enabled!==false&&SPEC[t]&&Ed.effects.get(t)&&mineRender.get(t)===Ed.effects.get(t).render)
  .map(([t,p])=>[t,norm(t,p)]).sort((a,b)=>SPEC[a[0]].order-SPEC[b[0]].order);
const padOf=(type,p)=>{switch(type){
  case'stroke':return Math.ceil(p.size)+2;
  case'drop-shadow':case'inner-shadow':{const[dx,dy]=offsetOf(p);return Math.max(Math.abs(dx),Math.abs(dy))+sizePad(p.size,type==='drop-shadow'?p.spread:p.choke);}
  case'outer-glow':return sizePad(p.size,p.spread);case'inner-glow':return sizePad(p.size,p.choke);default:return 0;}};
const layerPad=L=>enabledMine(L).reduce((m,[t,p])=>Math.max(m,padOf(t,p)),0);
function isBase(L){const d=Ed.doc,i=d?d.layers.indexOf(L):-1;return i>=0&&L.kind!=='adjustment'&&!!(d.layers[i+1]&&d.layers[i+1].clip);}
// Inner effects and the stroke are built inside the layer when its pixels were drawn plainly (Normal, not a clip base).
const insideOK=L=>L.blend==='normal'&&!isBase(L);
const chainOf=L=>enabledMine(L).filter(([t])=>SPEC[t].stage!=='behind');

// Behind: Drop Shadow and Outer Glow, knocked out by the shape.
function renderBehind(type,ctx,info){passOf(info,type);const p=norm(type,info.params),R=info.region,L=info.layer,F=fieldOf(info,layerPad(L)),G=F.G,A=F.A;
  const sp=p.size*p.spread/100,[dx,dy]=type==='drop-shadow'?offsetOf(p):[0,0],M=blur(dilated(F,sp),G.w,G.h,(p.size-sp)/2,0),f=L.fill,o=p.opacity/100*(info.opacity??1);
  const img=new ImageData(R.w,R.h),d=img.data,[cr,cg,cb]=rgbOf(p.color);let any=false;
  for(let y=0,j=0;y<R.h;y++){const gy=R.y+y-G.y,my=gy-dy,row=my>=0&&my<G.h;for(let x=0;x<R.w;x++,j+=4){const gx=R.x+x-G.x,a=A[gy*G.w+gx]/255,den=1-a*f;if(den<=1e-6||!row)continue;
    const mx=gx-dx;if(mx<0||mx>=G.w)continue;const v=o*M[my*G.w+mx]*(1-a)/den;if(!(v>0))continue;d[j]=cr;d[j+1]=cg;d[j+2]=cb;d[j+3]=Math.round(Math.min(1,v)*255);any=true;}}
  if(any)paint(ctx,info,R,img,p.blend);}

// Gradient Overlay: Photoshop's sRGB stops with midpoints (the fill.js format).
function compileStops(list,val){const at=list.map(s=>s.at),v=list.map(val),e=list.map(s=>{const m=clamp(s.mid,.05,.95);return Math.abs(m-.5)<1e-9?1:Math.log(.5)/Math.log(m);});return{at,v,e,n:list.length};}
function stopsAt(S,t,out){const n=S.n,at=S.at,v=S.v,c=v[0].length;if(n===1||t<=at[0]){for(let k=0;k<c;k++)out[k]=v[0][k];return;}if(t>=at[n-1]){for(let k=0;k<c;k++)out[k]=v[n-1][k];return;}
  let i=0;while(i<n-2&&at[i+1]<=t)i++;const span=at[i+1]-at[i],q=span>0?(t-at[i])/span:0,e=S.e[i],w=e===1?q:Math.pow(q>0?q:0,e);for(let k=0;k<c;k++)out[k]=v[i][k]+(v[i+1][k]-v[i][k])*w;}
const boundsCache=new Map();   // layer id -> {key, rect}
let boundsRefresh=false;
function boundsOf(L){const pv=Ed.preview.active,live=pv&&pv.layer===L,key=L.version+'|'+L.canvas.width+'x'+L.canvas.height,c=boundsCache.get(L.id);
  if(c&&(c.key===key||live))return c.rect;
  const W=L.canvas.width,H=L.canvas.height,d=U.read(L.canvas).data;let x0=W,y0=H,x1=-1,y1=-1;
  for(let y=0;y<H;y++){const r=y*W*4;for(let x=0;x<W;x++)if(d[r+x*4+3]){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}}
  const rect=x1<0?null:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};
  if(c&&JSON.stringify(c.rect)!==JSON.stringify(rect)&&!boundsRefresh){boundsRefresh=true;setTimeout(()=>{boundsRefresh=false;Ed.update();},0);}   // the whole gradient moved
  boundsCache.set(L.id,{key,rect});return rect;}
// One inside effect, ready to sample: kind 0 color overlay, 1 gradient overlay, 2 inner shadow / glow, 3 stroke.
function prepare(type,p,info,F){const o=p.opacity/100*(info.opacity??1),b=blendDef(p.blend),fn=b.id!=='normal'&&b.fn?b.fn:null,c=rgbOf(p.color||'#000000').map(v=>v/255);
  const e={type,o,fn,blend:p.blend,r:c[0],g:c[1],b:c[2]};
  if(type==='color-overlay')return{...e,kind:0};
  if(type==='gradient-overlay'){const g=normGradient(p.gradient),d=Ed.doc,B=(p.align&&boundsOf(info.layer))||{x:0,y:0,w:d.width,h:d.height},th=p.angle*Math.PI/180;
    const ux=Math.cos(th),uy=-Math.sin(th);
    return{...e,kind:1,col:compileStops(g.stops,s=>rgbOf(s.color).map(v=>v/255)),alp:compileStops(g.alpha,s=>[s.a]),ux,uy,cx:B.x+B.w/2,cy:B.y+B.h/2,
      len:(Math.abs(B.w*ux)+Math.abs(B.h*uy))*p.scale/100,radial:p.style==='radial',reverse:!!p.reverse};}
  if(type==='stroke'){const ro=p.position==='outside'?p.size:p.position==='center'?p.size/2:0,ri=p.position==='inside'?p.size:p.position==='center'?p.size/2:0;
    return{...e,kind:3,ro,ri,dOut:ro>0?F.dOut:null,dIn:ri>0?F.dIn:null};}
  const G=F.G,ck=p.size*p.choke/100,[dx,dy]=type==='inner-shadow'?offsetOf(p):[0,0];
  return{...e,kind:2,M:blur(erodedInverse(F,ck),G.w,G.h,(p.size-ck)/2,1),dx,dy};}
const C4=new Float64Array(4),A1=new Float64Array(1);
// Coverage k of effect e inside the shape at grid cell (gx, gy) / document pixel (X, Y); its color goes to C4.
function sample(e,G,gx,gy,X,Y){
  if(e.kind===0){C4[0]=e.r;C4[1]=e.g;C4[2]=e.b;return e.o;}
  if(e.kind===1){const px=X+.5-e.cx,py=Y+.5-e.cy;let t=e.len<1e-9?.5:e.radial?Math.sqrt(px*px+py*py)/(e.len/2):(px*e.ux+py*e.uy)/e.len+.5;
    t=t<0?0:t>1?1:t;if(e.reverse)t=1-t;stopsAt(e.col,t,C4);stopsAt(e.alp,t,A1);return e.o*clamp(A1[0],0,1);}
  C4[0]=e.r;C4[1]=e.g;C4[2]=e.b;const mx=gx-e.dx,my=gy-e.dy;return e.o*(mx<0||my<0||mx>=G.w||my>=G.h?1:e.M[my*G.w+mx]);}
// Stroke coverage inside (sets ST[0]) and outside (ST[1]) the shape at grid cell i.
const ST=new Float64Array(2);
function strokeAt(e,i,a){let inn=0,out=0;
  if(e.ri>0){const v=e.dIn[i]-e.ri+.5,ero=v<=0?0:v<a?v:a;inn=e.o*(a-ero);}
  if(e.ro>0){const v=e.ro+.5-e.dOut[i],dil=v>=1?1:v>a?v:a;out=e.o*(dil-a);}
  ST[0]=inn>0?inn:0;ST[1]=out>0?out:0;}
// Inside the layer: L = f P, each effect L = k a X + (1 - k) L (outside stroke: L += c X), then out = L + (1 - aL) B with
// B = (D - f P) / (1 - a f) recovered from what the core drew (D). Blend modes mix X with what shows there (W3C).
const CB=[0,0,0],CS=[0,0,0],BO=[0,0,0],c01=v=>v<0?0:v>1?1:v;
function renderChain(ctx,info,list){const R=info.region,L=info.layer,F=fieldOf(info,layerPad(L)),G=F.G,A=F.A,f=L.fill,img=F.img,Ri=F.Ri;
  const fx=list.map(([t,p])=>prepare(t,p,info,F)),D=U.read(ctx.canvas,R),d=D.data;
  for(let y=0,j=0;y<R.h;y++){const Y=R.y+y,gy=Y-G.y,srow=(Y-Ri.y)*Ri.w-Ri.x;
    for(let x=0;x<R.w;x++,j+=4){const X=R.x+x,gx=X-G.x,gi=gy*G.w+gx,s=(srow+X)*4,a=A[gi]/255,da=d[j+3]/255,den=1-a*f;
      let lr=img[s]/255*a*f,lg=img[s+1]/255*a*f,lb=img[s+2]/255*a*f,la=a*f,br=0,bg=0,bb=0,ba=0;
      if(den>1/1024){ba=c01((da-la)/den);br=clamp((d[j]/255*da-lr)/den,0,ba);bg=clamp((d[j+1]/255*da-lg)/den,0,ba);bb=clamp((d[j+2]/255*da-lb)/den,0,ba);}
      let touched=false;
      for(let q=0;q<fx.length;q++){const e=fx[q];let k,inner=0,outer=0;
        if(e.kind===3){strokeAt(e,gi,a);inner=ST[0];outer=ST[1];if(!(inner>0)&&!(outer>0))continue;C4[0]=e.r;C4[1]=e.g;C4[2]=e.b;k=a>0?Math.min(1,inner/a):0;}
        else{if(!(a>0))continue;k=sample(e,G,gx,gy,X,Y);if(!(k>0))continue;if(k>1)k=1;}
        let xr=C4[0],xg=C4[1],xb=C4[2];touched=true;
        if(e.fn){const ac=la+(1-la)*ba;if(ac>0){CB[0]=(lr+(1-la)*br)/ac;CB[1]=(lg+(1-la)*bg)/ac;CB[2]=(lb+(1-la)*bb)/ac;CS[0]=xr;CS[1]=xg;CS[2]=xb;e.fn(CB,CS,BO);
          xr=(1-ac)*xr+ac*c01(BO[0]);xg=(1-ac)*xg+ac*c01(BO[1]);xb=(1-ac)*xb+ac*c01(BO[2]);}}
        if(k>0&&a>0){const m=k*a;lr=m*xr+(1-k)*lr;lg=m*xg+(1-k)*lg;lb=m*xb+(1-k)*lb;la=m+(1-k)*la;}
        if(outer>0){lr+=outer*xr;lg+=outer*xg;lb+=outer*xb;la=Math.min(1,la+outer);}}
      if(!touched)continue;
      const ao=la+(1-la)*ba;if(ao<=1/1024){d[j]=d[j+1]=d[j+2]=d[j+3]=0;continue;}
      d[j]=Math.round(255*c01((lr+(1-la)*br)/ao));d[j+1]=Math.round(255*c01((lg+(1-la)*bg)/ao));d[j+2]=Math.round(255*c01((lb+(1-la)*bb)/ao));d[j+3]=Math.round(255*c01(ao));}}
  U.write(ctx.canvas,D,R);}
// Over the layer (clip bases, other blend modes): each effect drawn with coverage k a in its blend mode.
function renderOver(type,ctx,info){const p=norm(type,info.params),R=info.region,F=fieldOf(info,layerPad(info.layer)),G=F.G,A=F.A,e=prepare(type,p,info,F);
  const img=new ImageData(R.w,R.h),d=img.data;let any=false;
  for(let y=0,j=0;y<R.h;y++){const Y=R.y+y,gy=Y-G.y;for(let x=0;x<R.w;x++,j+=4){const X=R.x+x,gx=X-G.x,gi=gy*G.w+gx,a=A[gi]/255;let m;
    if(e.kind===3){strokeAt(e,gi,a);m=ST[0]+ST[1];C4[0]=e.r;C4[1]=e.g;C4[2]=e.b;}else{if(!(a>0))continue;m=clamp(sample(e,G,gx,gy,X,Y),0,1)*a;}
    if(!(m>0))continue;d[j]=Math.round(C4[0]*255);d[j+1]=Math.round(C4[1]*255);d[j+2]=Math.round(C4[2]*255);d[j+3]=Math.round(clamp(m,0,1)*255);any=true;}}
  if(any)paint(ctx,info,R,img,p.blend);}
function renderInside(type,ctx,info){passOf(info,type);const L=info.layer;
  if(!insideOK(L)){renderOver(type,ctx,info);return;}
  const list=chainOf(L);if(!list.length||list[0][0]!==type)return;   // the first one draws them all, in order
  renderChain(ctx,info,list);}

for(const type of TYPES){const s=SPEC[type];
  const render=s.stage==='behind'?(ctx,info)=>renderBehind(type,ctx,info):(ctx,info)=>renderInside(type,ctx,info);mineRender.set(type,render);
  Ed.registerEffect(type,{label:s.label,stage:s.stage,order:s.order,defaults:clone(s.defaults),pad:p=>padOf(type,norm(type,p)),render});}

// ----- commands ---------------------------------------------------------------------------------------------------------------
const tell=(t,err)=>{Ed.message(t,err);return false;};
const pixelLayer=ref=>{const L=Ed.layers.get(ref??null);return L&&L.kind==='pixel'?L:null;};
const hasFx=L=>!!(L&&Object.keys(L.effects||{}).length);
const targets=refs=>{if(refs!=null)return [].concat(refs).map(r=>Ed.layers.get(r)).filter(L=>L&&L.kind==='pixel');
  const s=Ed.layersPanel&&Ed.layersPanel.selected?Ed.layersPanel.selected():[];const list=(s&&s.length?s:[Ed.layers.active()]).filter(L=>L&&L.kind==='pixel');return list;};
let clip=null;   // the copied style
function copyStyle(){const L=pixelLayer();if(!L||!hasFx(L))return tell('This layer has no layer style to copy.');
  clip={effects:clone(L.effects),blend:L.blend,opacity:L.opacity,fill:L.fill};Ed.emit('menus');return true;}
function pasteStyle(refs){if(!clip)return tell('Copy a layer style first.');const list=targets(refs).filter(L=>!L.locks.all);if(!list.length)return tell('Select a pixel layer that is not locked to paste the style on.');
  Ed.history.record('Paste Layer Style',()=>{for(const L of list)Ed.layers.set(L,{effects:clone(clip.effects),blend:clip.blend,opacity:clip.opacity,fill:clip.fill},{history:false});},{op:{tool:'layer',action:'paste-style'}});return true;}
function clearStyle(refs){const list=targets(refs).filter(L=>hasFx(L)&&!L.locks.all);if(!list.length)return tell('This layer has no layer style.');
  Ed.history.record('Clear Layer Style',()=>{for(const L of list)Ed.layers.set(L,{effects:{}},{history:false});},{op:{tool:'layer',action:'clear-style'}});return true;}
const anyShown=()=>Ed.layers.list().some(L=>Object.values(L.effects||{}).some(p=>p&&p.enabled!==false));
const anyHidden=()=>Ed.layers.list().some(L=>Object.values(L.effects||{}).some(p=>p&&p.hiddenAll));
function hideAll(){if(!anyShown())return false;Ed.history.record('Hide All Effects',()=>{for(const L of Ed.layers.list()){const e=clone(L.effects||{});let ch=false;
    for(const [t,p] of Object.entries(e))if(p&&typeof p==='object'&&p.enabled!==false){p.enabled=false;p.hiddenAll=true;ch=true;}if(ch)Ed.layers.set(L,{effects:e},{history:false});}},{op:{tool:'layer',action:'hide-effects'}});return true;}
function showAll(){if(!anyHidden())return false;Ed.history.record('Show All Effects',()=>{for(const L of Ed.layers.list()){const e=clone(L.effects||{});let ch=false;
    for(const [t,p] of Object.entries(e))if(p&&p.hiddenAll){p.enabled=true;delete p.hiddenAll;ch=true;}if(ch)Ed.layers.set(L,{effects:e},{history:false});}},{op:{tool:'layer',action:'show-effects'}});return true;}

// ----- the Layer Style dialog -------------------------------------------------------------------------------------------------
let openDialog=null;
function blendOptions(){const out=[];let g=null;for(const b of Ed.blends.list()){if(g&&g!==b.group)out.push('-');g=b.group;out.push({value:b.id,label:b.label});}return out;}
function fieldsFor(type){return SPEC[type].fields.map(f=>f.key==='blend'?{...f,options:blendOptions()}:f.type==='gradient'&&!Ed.fields.get('gradient')?{...f,type:'fx-gradient'}:f);}
// Without fill.js: a two-color gradient (start and end) for Gradient Overlay.
Ed.registerField('fx-gradient',(f,value,set)=>{const g=normGradient(value),box=el('span','ed-fx-grad2');
  const pick=(i,label)=>{const c=el('input');c.type='color';c.setAttribute('aria-label',label);c.name='gradient-'+(i?'end':'start');c.value=i?g.stops[g.stops.length-1].color:g.stops[0].color;
    c.oninput=c.onchange=()=>{const s=g.stops;if(i)s[s.length-1].color=c.value;else s[0].color=c.value;set(clone(g));};return c;};
  box.append(pick(0,'Start color'),el('span','ed-fx-grad2-arrow','→'),pick(1,'End color'));return box;});
function styleDialog(L,first){
  if(openDialog)return Promise.resolve(false);
  const orig={effects:clone(L.effects||{}),blend:L.blend,opacity:L.opacity,fill:L.fill};
  const st={blend:L.blend,opacity:Math.round(L.opacity*100),fill:Math.round(L.fill*100),fx:{}};
  for(const t of TYPES){const p=orig.effects[t];st.fx[t]=norm(t,p);st.fx[t].enabled=!!p&&p.enabled!==false;delete st.fx[t].hiddenAll;}
  if(first&&SPEC[first])st.fx[first].enabled=true;
  let sel=first&&SPEC[first]?first:'blending',previewOn=true,frame=0,resolve;const done=new Promise(r=>{resolve=r;});
  const result=()=>{const effects={};for(const [t,p] of Object.entries(orig.effects))if(!SPEC[t])effects[t]=clone(p);
    for(const t of TYPES){const p=st.fx[t];if(p.enabled||orig.effects[t]){const o=clone(p);o.enabled=!!p.enabled;effects[t]=o;}}
    return{effects,blend:st.blend,opacity:clamp(st.opacity/100,0,1),fill:clamp(st.fill/100,0,1)};};
  const live=()=>{if(frame)return;frame=requestAnimationFrame(()=>{frame=0;if(!openDialog)return;Ed.layers.set(L,previewOn?result():orig,{history:false});});};
  const d=el('dialog','ed-dialog ed-fx-dialog'),form=el('form');form.method='dialog';d.setAttribute('data-ed-dialog','Layer Style');d.setAttribute('aria-label','Layer Style');
  const list=el('div','ed-fx-list'),pane=el('div','ed-fx-pane');list.setAttribute('role','listbox');list.setAttribute('aria-label','Effects');
  const rows={};
  const row=(id,label)=>{const r=el('div','ed-fx-item');r.dataset.fx=id;r.setAttribute('role','option');
    if(id!=='blending'){const c=el('input');c.type='checkbox';c.name='fx-'+id;c.setAttribute('aria-label',label);c.checked=st.fx[id].enabled;
      c.onchange=()=>{st.fx[id].enabled=c.checked;if(c.checked)select(id);live();sync();};r.append(c);}
    const b=btn('ed-fx-name',label);b.onclick=()=>{if(id!=='blending'&&!st.fx[id].enabled){st.fx[id].enabled=true;live();}select(id);sync();};r.append(b);rows[id]=r;list.append(r);};
  row('blending','Blending Options');for(const t of TYPES)row(t,SPEC[t].label);
  const sync=()=>{for(const [id,r] of Object.entries(rows)){r.classList.toggle('selected',id===sel);r.setAttribute('aria-selected',String(id===sel));const c=r.querySelector('input');if(c)c.checked=st.fx[id].enabled;}};
  function select(id){sel=id;pane.replaceChildren();const head=el('div','ed-fx-head'),title=el('h3','',id==='blending'?'Blending Options':SPEC[id].label);head.append(title);
    const box=el('div','ed-fx-fields');
    if(id==='blending'){for(const f of [{key:'blend',label:'Blend Mode',type:'select',options:blendOptions()},{...OPACITY},{key:'fill',label:'Fill Opacity',type:'range',min:0,max:100,step:1,unit:'%',
        title:'Fill Opacity: how strongly the layer’s own pixels show; its effects keep their strength'}])box.append(Ed.ui.field(f,st[f.key],v=>{st[f.key]=v;live();}).node);}
    else{const p=st.fx[id];for(const f of fieldsFor(id))box.append(Ed.ui.field(f,p[f.key],v=>{p[f.key]=f.type==='gradient'||f.type==='fx-gradient'?normGradient(v):v;if(!p.enabled){p.enabled=true;sync();}live();}).node);
      const reset=btn('quiet ed-fx-reset','Reset to Default','Put this effect’s settings back to their defaults');reset.onclick=()=>{st.fx[id]={...clone(SPEC[id].defaults),enabled:true};select(id);sync();live();};head.append(reset);}
    pane.append(head,box);}
  const actions=el('div','ed-dialog-actions'),pvBox=el('label','ed-field ed-checkbox ed-preview-toggle'),pv=el('input');pv.type='checkbox';pv.name='preview';pv.checked=true;pvBox.append(pv,el('span','','Preview'));
  pv.onchange=()=>{previewOn=pv.checked;live();};
  const cancel=btn('','Cancel'),ok=el('button','primary','OK');ok.type='submit';ok.value='ok';actions.append(pvBox,cancel,ok);
  const body=el('div','ed-fx-body');body.append(list,pane);form.append(el('h2','','Layer Style'),body,actions);d.append(form);
  function finish(accept){if(!openDialog)return;openDialog=null;cancelAnimationFrame(frame);frame=0;const want=result();if(d.open)d.close();d.remove();
    if(!Ed.layers.get(L)){resolve(false);return;}
    Ed.layers.set(L,orig,{history:false});
    if(accept&&JSON.stringify([want.effects,want.blend,want.opacity,want.fill])!==JSON.stringify([orig.effects,orig.blend,orig.opacity,orig.fill]))Ed.layers.set(L,want,{history:'Layer Style'});
    resolve(!!accept);}
  form.onsubmit=e=>{e.preventDefault();finish(true);};cancel.onclick=()=>finish(false);d.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
  select(sel);sync();document.body.append(d);openDialog={dialog:d,layer:L,finish};d.showModal();live();
  const focus=rows[sel]&&rows[sel].querySelector('.ed-fx-name');if(focus)focus.focus({preventScroll:true});
  done.element=d;return done;}
function open(ref,type){const L=Ed.layers.get(ref??null);if(!L)return Promise.resolve(tell('Open an image first.'));
  if(L.kind!=='pixel')return Promise.resolve(tell('Adjustment layers have no layer style.'));
  if(L.locks.all)return Promise.resolve(tell(`“${L.name}” is locked. Unlock it in the Layers panel to change its style.`));
  return styleDialog(L,type);}
Ed.on('open',()=>{if(openDialog)openDialog.finish(false);boundsCache.clear();});

// ----- menus, badge, API --------------------------------------------------------------------------------------------------
const canStyle=()=>{const L=pixelLayer();return !!(L&&!L.locks.all)&&!openDialog;};
const M='Layer/Layer Style/';
Ed.registerMenu(M+'Blending Options...',{order:33,enabled:canStyle,action:p=>{
  if(p&&typeof p==='object'){const L=pixelLayer(),o={};if(!L)return false;if(typeof p.blend==='string')o.blend=p.blend;for(const k of ['opacity','fill'])if(p[k]!=null)o[k]=clamp(num(p[k],100)/100,0,1);
    return Ed.layers.set(L,o,{history:'Layer Style'});}
  open(null);}});
TYPES.forEach((t,i)=>Ed.registerMenu(M+SPEC[t].label+'...',{order:33.01+i/100,separatorBefore:i===0,enabled:canStyle,action:p=>{
  if(p&&typeof p==='object'){const L=pixelLayer();if(!L)return false;const e=clone(L.effects||{});e[t]={...norm(t,{...e[t],...p}),enabled:true};delete e[t].hiddenAll;Ed.layers.set(L,{effects:e},{history:'Layer Style'});return true;}
  open(null,t);}}));
Ed.registerMenu(M+'Copy Layer Style',{order:33.2,separatorBefore:true,enabled:()=>hasFx(pixelLayer()),action:()=>copyStyle()});
Ed.registerMenu(M+'Paste Layer Style',{order:33.21,enabled:()=>!!clip&&targets().some(L=>!L.locks.all),action:()=>pasteStyle()});
Ed.registerMenu(M+'Clear Layer Style',{order:33.22,enabled:()=>targets().some(L=>hasFx(L)&&!L.locks.all),action:()=>clearStyle()});
Ed.registerMenu(M+'Hide All Effects',{order:33.3,separatorBefore:true,enabled:()=>anyShown()||anyHidden(),label:()=>!anyShown()&&anyHidden()?'Show All Effects':'Hide All Effects',
  action:()=>anyShown()?hideAll():showAll()});
Ed.registerLayerBadge(L=>{const on=Object.entries(L.effects||{}).filter(([,p])=>p&&p.enabled!==false);if(!on.length)return null;
  const b=el('span','ed-badge ed-fx-badge','fx');b.title='Layer style · double-click the layer to edit it';b.dataset.effects=on.map(([t])=>t).join(' ');return b;});
Ed.layerStyle={open:(ref,type)=>open(ref,type),copy:copyStyle,paste:pasteStyle,clear:clearStyle,hideAll,showAll,
  types:()=>TYPES.map(type=>({type,label:SPEC[type].label})),defaults:t=>SPEC[t]?clone(SPEC[t].defaults):null,has:ref=>hasFx(Ed.layers.get(ref??null)),
  get dialog(){return openDialog?openDialog.dialog:null;}};
})();
