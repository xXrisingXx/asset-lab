'use strict';
/* =====================================================================================================================
   ADJUSTMENTS  ·  editor/adjust.js (built on window.Ed only; the engine API is at the top of editor/core.js)
   Photoshop's Image > Adjustments as one-off commands with a live-preview dialog, Image > Auto Tone / Auto Contrast /
   Auto Color, Layer > New Adjustment Layer (live, editable later in the Properties panel, painted through its mask) and
   the Properties panel ('properties'). One kernel per adjustment serves the dialog preview, the command and the
   adjustment layer, so a layer and the same command give identical pixels. Commands go through Ed.applyToTarget (soft
   selection, lock transparency, one undo state named like Photoshop's); with a layer mask targeted the tonal ones work
   on the mask (Levels, Curves, Invert...) and the colour ones are refused, as in Photoshop.

   HOW CLOSE EACH ONE IS TO PHOTOSHOP (tests/browser/test_adjust.py checks every kernel against its own numpy reference)
   exact   Levels (input black/white, gamma, output black/white; each colour channel first, then RGB, as GIMP reads
           Photoshop's files), Curves (natural cubic spline through the points, flat beyond the end points, clipped;
           channel then RGB; up to 14 points), Channel Mixer, Invert, Desaturate ((max + min) / 2), Black & White
           (min + (max - mid) x primary + (mid - min) x secondary: a pure colour becomes its slider's % of white),
           Posterize (equal-width bins), Threshold (Rec. 601 luminance >= level is white).
   model   Adobe has not published these, so each follows a documented model (see the comment at each kernel):
           Brightness/Contrast (modern: smooth curves through black and white, never clips; Use Legacy: linear contrast
           around the middle, then + brightness), Exposure (linear light: x 2^stops + offset, power gamma), Vibrance,
           Hue/Saturation (the widely published reverse-engineered saturation and lightness steps, HSL hue rotation, six
           hue ranges with linear fall-off, Colorize from the HSL lightness), Color Balance (GIMP's model), Photo Filter
           (multiply at Density, then the old luminosity back with the W3C SetLum), Selective Color (FFmpeg's
           selectivecolor), Gradient Map (Rec. 601 luminance; stops with midpoints), Equalize (one table from the
           cumulative R+G+B histogram), Auto Tone / Auto Contrast / Auto Color (0.1 % clipping; Auto Color snaps the
           near-gray midtones to neutral).
   missing Color Lookup, HDR Toning, Match Color, Shadows/Highlights, Replace Color (no menu items).
   The eyedroppers (Levels, Curves; in the dialog and in Properties) stay on for more clicks, as in Photoshop, until their
   button is clicked again, Escape, OK or Cancel; meanwhile the dialog is non-modal so the picture can be clicked.

   API  Ed.adjust = {types(), layerTypes(), label(type), defaults(type), normalize(type, params),
     apply(type, params, imageData, region) (in place: unpremultiplied RGBA, alpha kept), command(type, params?) (no
     params: the dialog; returns a Promise), newLayer(type, params?), auto(kind 'tone'|'contrast'|'color', data,
     selectionData?) -> Levels params, autoCommand(kind), histogram(data, selectionData?), spline(points) -> f(x),
     ui(type, params, change(params, done), ctx) -> {node, set(params), params}}
   Field for Ed.dialog: 'adjustment' (a whole adjustment's controls, field.adjType).
   ===================================================================================================================== */
(()=>{
const Ed=window.Ed;if(!Ed||Ed.adjust)return;
const U=Ed.util,clamp=U.clamp;
const R8=v=>v<=0?0:v>=255?255:Math.round(v);
const rint=v=>{const f=Math.floor(v),d=v-f;return d>.5?f+1:d<.5?f:f%2?f+1:f;};   // C's lrint: halves go to the even neighbour
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const btn=(cls,text,title)=>{const b=el('button',cls,text);b.type='button';if(title){b.title=title;b.setAttribute('aria-label',title.split(':')[0]);}return b;};
const clone=v=>v==null?v:JSON.parse(JSON.stringify(v));
const RGB3=['red','green','blue'];
const deg=v=>Math.round(v)+'°';

// ----- colour math ---------------------------------------------------------------------------------------------------
const LIN=new Float64Array(256);for(let i=0;i<256;i++){const c=i/255;LIN[i]=c<=.04045?c/12.92:Math.pow((c+.055)/1.055,2.4);}
const toSRGB=l=>l<=.0031308?12.92*l:1.055*Math.pow(l,1/2.4)-.055;
const luma=(r,g,b)=>.299*r+.587*g+.114*b;   // Rec. 601, Photoshop's gray for Threshold and Gradient Map
function hueOf(r,g,b,mx,mn){const d=mx-mn;if(d<=0)return 0;const h=mx===r?(g-b)/d+(g<b?6:0):mx===g?(b-r)/d+2:(r-g)/d+4;return h*60;}
function toHsl(r,g,b,o){const mx=Math.max(r,g,b),mn=Math.min(r,g,b),l=(mx+mn)/2,d=mx-mn;o[0]=hueOf(r,g,b,mx,mn);o[1]=d<=0?0:l<.5?d/(mx+mn):d/(2-mx-mn);o[2]=l;return o;}
function hue2(p,q,t){if(t<0)t+=1;else if(t>1)t-=1;return t<1/6?p+(q-p)*6*t:t<.5?q:t<2/3?p+(q-p)*(2/3-t)*6:p;}
function fromHsl(h,s,l,o){if(s<=0){o[0]=o[1]=o[2]=l;return o;}const q=l<.5?l*(1+s):l+s-l*s,p=2*l-q,t=(((h%360)+360)%360)/360;
  o[0]=hue2(p,q,t+1/3);o[1]=hue2(p,q,t);o[2]=hue2(p,q,t-1/3);return o;}
// The W3C Luminosity helpers (Photoshop's non-separable blend math), used to keep the old luminosity.
const lumW=c=>.3*c[0]+.59*c[1]+.11*c[2];
function setLum(c,l){const d=l-lumW(c);c[0]+=d;c[1]+=d;c[2]+=d;const L=lumW(c),n=Math.min(c[0],c[1],c[2]),x=Math.max(c[0],c[1],c[2]);
  if(n<0)for(let k=0;k<3;k++)c[k]=L+(c[k]-L)*L/(L-n);
  if(x>1)for(let k=0;k<3;k++)c[k]=L+(c[k]-L)*(1-L)/(x-L);return c;}
// Scale a pixel's colourfulness around its luminance by k, never past the gamut (the hue stays put).
function scaleChroma(d,i,k){const r=d[i],g=d[i+1],b=d[i+2],Y=luma(r,g,b);
  if(k>1){let e=r-Y;if(e>0)k=Math.min(k,(255-Y)/e);else if(e<0)k=Math.min(k,-Y/e);e=g-Y;if(e>0)k=Math.min(k,(255-Y)/e);else if(e<0)k=Math.min(k,-Y/e);
    e=b-Y;if(e>0)k=Math.min(k,(255-Y)/e);else if(e<0)k=Math.min(k,-Y/e);}
  if(k<0)k=0;d[i]=R8(Y+(r-Y)*k);d[i+1]=R8(Y+(g-Y)*k);d[i+2]=R8(Y+(b-Y)*k);}

// ----- curves: Photoshop's natural cubic spline ----------------------------------------------------------------------
// Through the points with zero second derivative at both ends; flat beyond the first and last point (moving the end
// point of a curve clips everything past it). Steep neighbours overshoot and get clipped to 0..255, as in Photoshop.
function spline(points){
  const P=(points||[]).map(p=>[+p[0],+p[1]]).filter(p=>isFinite(p[0])&&isFinite(p[1])).sort((a,b)=>a[0]-b[0]);
  if(!P.length)return v=>v;const n=P.length,x=P.map(p=>p[0]),y=P.map(p=>p[1]);if(n===1)return()=>y[0];
  const h=[],M=new Float64Array(n);for(let i=0;i<n-1;i++)h.push(Math.max(1e-9,x[i+1]-x[i]));
  if(n>2){const m=n-2,a=new Float64Array(m),b=new Float64Array(m),c=new Float64Array(m),d=new Float64Array(m);
    for(let i=1;i<=m;i++){a[i-1]=h[i-1];b[i-1]=2*(h[i-1]+h[i]);c[i-1]=h[i];d[i-1]=6*((y[i+1]-y[i])/h[i]-(y[i]-y[i-1])/h[i-1]);}
    for(let i=1;i<m;i++){const w=a[i]/b[i-1];b[i]-=w*c[i-1];d[i]-=w*d[i-1];}
    M[m]=d[m-1]/b[m-1];for(let i=m-2;i>=0;i--)M[i+1]=(d[i]-c[i]*M[i+2])/b[i];}
  return v=>{if(v<=x[0])return y[0];if(v>=x[n-1])return y[n-1];let i=0;while(i<n-2&&v>x[i+1])i++;
    const t=v-x[i],hi=h[i],s=(y[i+1]-y[i])/hi-hi*(2*M[i]+M[i+1])/6;return y[i]+s*t+M[i]/2*t*t+(M[i+1]-M[i])/(6*hi)*t*t*t;};}
const table=f=>{const l=new Uint8Array(256);for(let v=0;v<256;v++)l[v]=R8(f(v));return l;};
const isIdentity=l=>{for(let v=0;v<256;v++)if(l[v]!==v)return false;return true;};

// ----- levels ----------------------------------------------------------------------------------------------------------
// Photoshop's Levels: t = (v - inBlack) / (inWhite - inBlack) clipped to 0..1, t^(1/gamma), outBlack + t (outWhite - outBlack).
const LV=()=>({inBlack:0,inWhite:255,gamma:1,outBlack:0,outWhite:255});
function levelsFn(p){const ib=+p.inBlack||0,iw=Math.max(ib+1e-3,+p.inWhite),g=+p.gamma>0?+p.gamma:1,ob=+p.outBlack||0,ow=+p.outWhite;
  return v=>{let t=(v-ib)/(iw-ib);t=t<0?0:t>1?1:t;if(g!==1&&t>0)t=Math.pow(t,1/g);return ob+t*(ow-ob);};}

// ----- histograms and the Auto commands ------------------------------------------------------------------------------
function histogram(d,sel){const r=new Float64Array(256),g=new Float64Array(256),b=new Float64Array(256),l=new Float64Array(256);let n=0;
  for(let i=0;i<d.length;i+=4){if(!d[i+3])continue;let w=1;if(sel){w=sel[i+3]/255;if(w<=0)continue;}
    r[d[i]]+=w;g[d[i+1]]+=w;b[d[i+2]]+=w;l[R8(luma(d[i],d[i+1],d[i+2]))]+=w;n+=w;}
  const rgb=new Float64Array(256);for(let v=0;v<256;v++)rgb[v]=r[v]+g[v]+b[v];return{red:r,green:g,blue:b,rgb,luma:l,n};}
// The values where the darkest and the lightest `clip` fraction of the counts end.
function clipBounds(h,n,clip){const lim=n*clip;let lo=0,acc=0;for(;lo<255;lo++){acc+=h[lo];if(acc>lim)break;}let hi=255;acc=0;for(;hi>0;hi--){acc+=h[hi];if(acc>lim)break;}return[lo,hi];}
const CLIP=.001;   // Photoshop's default: 0.1 % of the shadows and highlights are clipped
// Auto Tone (Enhance Per Channel Contrast), Auto Contrast (Enhance Monochromatic Contrast) and Auto Color (Find Dark &
// Light Colors, then Snap Neutral Midtones) as Levels settings.
function autoLevels(kind,d,sel){const H=histogram(d,sel),p={rgb:LV(),red:LV(),green:LV(),blue:LV()};if(!H.n)return p;
  if(kind==='tone'){for(const c of RGB3){const [lo,hi]=clipBounds(H[c],H.n,CLIP);if(hi-lo>=2){p[c].inBlack=lo;p[c].inWhite=hi;}}return p;}
  if(kind==='contrast'){const [lo,hi]=clipBounds(H.rgb,H.n*3,CLIP);if(hi-lo>=2){p.rgb.inBlack=lo;p.rgb.inWhite=hi;}return p;}
  const [tlo,thi]=clipBounds(H.luma,H.n,CLIP),dk=[0,0,0,0],lt=[0,0,0,0];
  for(let i=0;i<d.length;i+=4){if(!d[i+3])continue;let w=1;if(sel){w=sel[i+3]/255;if(w<=0)continue;}const y=R8(luma(d[i],d[i+1],d[i+2]));
    if(y<=tlo){dk[0]+=d[i]*w;dk[1]+=d[i+1]*w;dk[2]+=d[i+2]*w;dk[3]+=w;}if(y>=thi){lt[0]+=d[i]*w;lt[1]+=d[i+1]*w;lt[2]+=d[i+2]*w;lt[3]+=w;}}
  RGB3.forEach((c,k)=>{const lo=dk[3]?Math.round(dk[k]/dk[3]):0,hi=lt[3]?Math.round(lt[k]/lt[3]):255;if(hi-lo>=2){p[c].inBlack=lo;p[c].inWhite=hi;}});
  // Snap Neutral Midtones: the average nearly gray midtone (after the stretch) becomes exactly gray.
  const f=RGB3.map(c=>levelsFn(p[c])),m=[0,0,0];let mw=0;
  for(let i=0;i<d.length;i+=4){if(!d[i+3])continue;let w=1;if(sel){w=sel[i+3]/255;if(w<=0)continue;}
    const a=f[0](d[i]),b=f[1](d[i+1]),c=f[2](d[i+2]),mx=Math.max(a,b,c),mn=Math.min(a,b,c),y=luma(a,b,c);
    if(mx-mn<26&&y>=48&&y<=208){m[0]+=d[i]*w;m[1]+=d[i+1]*w;m[2]+=d[i+2]*w;mw+=w;}}
  if(mw>=Math.max(1,H.n*.002)){const u=RGB3.map((c,k)=>clamp((m[k]/mw-p[c].inBlack)/Math.max(1,p[c].inWhite-p[c].inBlack),.02,.98)),T=(u[0]+u[1]+u[2])/3;
    RGB3.forEach((c,k)=>{p[c].gamma=+clamp(Math.log(u[k])/Math.log(T),.1,9.99).toFixed(2);});}
  return p;}
function equalizeTable(H){const h=H.rgb;let N=0;for(let v=0;v<256;v++)N+=h[v];let cmin=0,acc=0;for(let v=0;v<256;v++)if(h[v]){cmin=h[v];break;}
  const l=new Uint8Array(256);if(N-cmin<=0){for(let v=0;v<256;v++)l[v]=v;return l;}for(let v=0;v<256;v++){acc+=h[v];l[v]=R8((acc-cmin)/(N-cmin)*255);}return l;}

// ----- the adjustments -----------------------------------------------------------------------------------------------
// def: {label, title (dialog), menu (menu text), key (shortcut), gray (works on a layer mask), layer (an adjustment layer
// type), dialog (false: runs at once), defaults (object or function), fix(p) (repair), lut(p) -> [r, g, b] tables, or
// px(p) -> kernel(data, width, height, region), or whole(imageData, p, selectionData, region), ui(st, emit, ctx), icon}
const DEF={};
const lutKernel=([a,b,c])=>d=>{for(let i=0;i<d.length;i+=4){d[i]=a[d[i]];d[i+1]=b[d[i+1]];d[i+2]=c[d[i+2]];}};
const same=l=>[l,l,l];

// Brightness/Contrast. Use Legacy is Adobe's description: brightness adds n to every channel, contrast stretches or
// squeezes linearly around the middle (+100 leaves only 0 and 255, -100 makes everything 127; -100 contrast with +100
// brightness gives 227). The modern mode is curve based (it came from Camera Raw's first process version) and never
// clips: brightness bends the midtones on a smooth curve through black and white, contrast is an S-curve around the
// middle. Adobe has not published that curve; this one behaves the same way (approximation).
function bcFn(p){
  if(p.legacy){const b=clamp(p.brightness,-100,100),c=clamp(p.contrast,-100,100);
    return v=>{const x=c>=100?(v>=128?255:0):(v-127.5)*(c>=0?100/(100-c):(100+c)/100)+127.5;return clamp(Math.floor(x+b+1e-7),0,255);};}
  const b=clamp(p.brightness,-150,150),c=clamp(p.contrast,-50,100),k=c*.32;
  const fb=b?spline([[0,0],[128,128+b*.5],[255,255]]):null,fc=c?spline([[0,0],[64,64-k],[192,192+k],[255,255]]):null;
  return v=>{let x=v;if(fb)x=clamp(fb(x),0,255);if(fc)x=clamp(fc(x),0,255);return x;};}
DEF['brightness-contrast']={label:'Brightness/Contrast',gray:true,layer:true,defaults:{brightness:0,contrast:0,legacy:false},
  lut:p=>same(table(bcFn(p))),ui:(st,emit)=>bcUI(st,emit)};
DEF.levels={label:'Levels',key:'Ctrl+L',gray:true,layer:true,defaults:()=>({rgb:LV(),red:LV(),green:LV(),blue:LV()}),fix:fixLevels,
  lut(p){const m=levelsFn(p.rgb);return RGB3.map(c=>{const f=levelsFn(p[c]);return table(v=>m(f(v)));});},ui:(st,emit,ctx)=>levelsUI(st,emit,ctx)};
function fixLevels(p){for(const c of ['rgb',...RGB3]){const q=p[c];q.inBlack=clamp(Math.round(q.inBlack),0,253);q.inWhite=clamp(Math.round(q.inWhite),q.inBlack+2,255);
  q.gamma=clamp(q.gamma,.1,9.99);q.outBlack=clamp(Math.round(q.outBlack),0,255);q.outWhite=clamp(Math.round(q.outWhite),0,255);}return p;}
const LINE=()=>[[0,0],[255,255]];
function fixCurve(P){let a=Array.isArray(P)?P.filter(p=>Array.isArray(p)&&isFinite(+p[0])&&isFinite(+p[1])).map(p=>[clamp(Math.round(+p[0]),0,255),clamp(Math.round(+p[1]),0,255)]):[];
  a.sort((p,q)=>p[0]-q[0]);a=a.filter((p,i)=>!i||p[0]!==a[i-1][0]);if(a.length<2)return LINE();return a.slice(0,14);}
DEF.curves={label:'Curves',key:'Ctrl+M',gray:true,layer:true,defaults:()=>({rgb:LINE(),red:LINE(),green:LINE(),blue:LINE()}),
  fix(p){for(const c of ['rgb',...RGB3])p[c]=fixCurve(p[c]);return p;},
  lut(p){const m=spline(p.rgb);return RGB3.map(c=>{const f=spline(p[c]);return table(v=>m(clamp(f(v),0,255)));});},ui:(st,emit,ctx)=>curvesUI(st,emit,ctx)};
// Exposure works in linear light (Adobe: "calculations in a linear color space (gamma 1.0)"): x 2^exposure, + offset,
// then gamma correction as a power, back to sRGB.
DEF.exposure={label:'Exposure',gray:true,layer:true,defaults:{exposure:0,offset:0,gamma:1},
  lut(p){const k=Math.pow(2,clamp(p.exposure,-20,20)),o=clamp(p.offset,-.5,.5),g=clamp(p.gamma,.01,9.99);
    return same(table(v=>{let x=LIN[v]*k+o;x=x<=0?0:Math.pow(x,1/g);return toSRGB(Math.min(1,x))*255;}));},ui:(st,emit)=>exposureUI(st,emit)};
// Vibrance (model): boosts dull colours much more than vivid ones and holds back skin tones; Saturation moves every
// colour the same way. Both scale colourfulness around the pixel's luminance and never clip (the hue is kept).
DEF.vibrance={label:'Vibrance',layer:true,defaults:{vibrance:0,saturation:0},
  px(p){const v=clamp(p.vibrance,-100,100)/100,s=clamp(p.saturation,-100,100)/100;if(!v&&!s)return null;
    return d=>{for(let i=0;i<d.length;i+=4){const r=d[i],g=d[i+1],b=d[i+2],mx=Math.max(r,g,b),mn=Math.min(r,g,b);if(mx===mn)continue;
      const sat=(mx-mn)/255;let k=1;
      if(v>0){const dh=((hueOf(r,g,b,mx,mn)-25)%360+540)%360-180,skin=Math.exp(-dh*dh/800);k=1+v*(1-sat)*(1-sat)*(1-.5*skin);}
      else if(v<0)k=1+v*(1-.5*sat);
      scaleChroma(d,i,k*(1+s));}};},ui:(st,emit)=>form(st,emit,[
      {key:'vibrance',label:'Vibrance',type:'range',min:-100,max:100,step:1,title:'Boosts dull colors much more than strong ones and protects skin tones'},
      {key:'saturation',label:'Saturation',type:'range',min:-100,max:100,step:1,title:'Changes every color by the same amount (-100 is gray)'}])};
// Hue/Saturation. Master plus six colour ranges (Reds 315°/345° \ 15°/45° ...): a pixel's original hue weighs each range
// (1 inside, linear fall-off at the sides). Hue turns the colour around the wheel keeping its HSL lightness and
// saturation; Saturation is the reverse-engineered Photoshop formula (towards the HSL lightness when negative, -100 =
// Desaturate); Lightness mixes towards white (+) or black (-). Colorize: the image's HSL lightness with one hue.
const HS_KEYS=['reds','yellows','greens','cyans','blues','magentas'];
const HS_NAMES={master:'Master',reds:'Reds',yellows:'Yellows',greens:'Greens',cyans:'Cyans',blues:'Blues',magentas:'Magentas'};
const HS_RANGE={reds:[-45,-15,15,45],yellows:[15,45,75,105],greens:[75,105,135,165],cyans:[135,165,195,225],blues:[195,225,255,285],magentas:[255,285,315,345]};
function rangeWeight(h,R){const mid=(R[1]+R[2])/2,dh=((h-mid)%360+540)%360-180,a=R[0]-mid,b=R[1]-mid,c=R[2]-mid,e=R[3]-mid;
  if(dh<a||dh>e)return 0;if(dh<b)return(dh-a)/(b-a);if(dh>c)return(e-dh)/(e-c);return 1;}
function psSaturate(c,inc){const mx=Math.max(c[0],c[1],c[2]),mn=Math.min(c[0],c[1],c[2]),delta=(mx-mn)/255;if(delta<=0||!inc)return;
  const value=(mx+mn)/255,L=value/2,S=L<.5?delta/value:delta/(2-value),L255=L*255;
  if(inc>0){let a=inc+S>=1?S:1-inc;a=1/a-1;for(let k=0;k<3;k++)c[k]+=(c[k]-L255)*a;}else for(let k=0;k<3;k++)c[k]=L255+(c[k]-L255)*(1+inc);
  for(let k=0;k<3;k++)c[k]=c[k]<0?0:c[k]>255?255:c[k];}
function psLight(c,l){if(l>0)for(let k=0;k<3;k++)c[k]+=(255-c[k])*l;else if(l<0)for(let k=0;k<3;k++)c[k]+=c[k]*l;}
const HSL0=()=>({hue:0,saturation:0,lightness:0});
DEF['hue-saturation']={label:'Hue/Saturation',key:'Ctrl+U',layer:true,
  defaults:()=>{const o={master:HSL0()};for(const k of HS_KEYS)o[k]={...HSL0(),range:HS_RANGE[k].slice()};o.colorize={enabled:false,hue:0,saturation:25,lightness:0};return o;},
  fix(p){for(const k of ['master',...HS_KEYS]){const q=p[k];q.hue=clamp(Math.round(q.hue),-180,180);q.saturation=clamp(Math.round(q.saturation),-100,100);q.lightness=clamp(Math.round(q.lightness),-100,100);}
    for(const k of HS_KEYS){const R=p[k].range;if(!Array.isArray(R)||R.length!==4||!R.every(isFinite)||!(R[0]<=R[1]&&R[1]<=R[2]&&R[2]<=R[3])||R[3]-R[0]>360)p[k].range=HS_RANGE[k].slice();}
    const z=p.colorize;z.hue=clamp(Math.round(z.hue),0,360);z.saturation=clamp(Math.round(z.saturation),0,100);z.lightness=clamp(Math.round(z.lightness),-100,100);return p;},
  px(p){
    if(p.colorize.enabled){const H=p.colorize.hue,S=p.colorize.saturation/100,Lt=p.colorize.lightness/100,tab=new Uint8Array(511*3),o=[0,0,0];
      for(let s=0;s<511;s++){let l=s/510;l=Lt>0?l+(1-l)*Lt:l+l*Lt;fromHsl(H,S,l,o);tab[s*3]=R8(o[0]*255);tab[s*3+1]=R8(o[1]*255);tab[s*3+2]=R8(o[2]*255);}
      return d=>{for(let i=0;i<d.length;i+=4){const r=d[i],g=d[i+1],b=d[i+2],s=(r>g?(r>b?r:b):(g>b?g:b))+(r<g?(r<b?r:b):(g<b?g:b));d[i]=tab[s*3];d[i+1]=tab[s*3+1];d[i+2]=tab[s*3+2];}};}
    const M=p.master,act=HS_KEYS.filter(k=>p[k].hue||p[k].saturation||p[k].lightness).map(k=>[p[k].range,p[k].hue,p[k].saturation,p[k].lightness]);
    if(!M.hue&&!M.saturation&&!M.lightness&&!act.length)return null;
    return d=>{const c=[0,0,0],o=[0,0,0];for(let i=0;i<d.length;i+=4){const r=d[i],g=d[i+1],b=d[i+2],mx=Math.max(r,g,b),mn=Math.min(r,g,b),h=mx>mn?hueOf(r,g,b,mx,mn):-1;
      let dh=M.hue,ds=M.saturation,dl=M.lightness;
      if(h>=0)for(let k=0;k<act.length;k++){const a=act[k],w=rangeWeight(h,a[0]);if(w>0){dh+=w*a[1];ds+=w*a[2];dl+=w*a[3];}}
      if(!dh&&!ds&&!dl)continue;
      c[0]=r;c[1]=g;c[2]=b;
      if(dh&&h>=0){const l=(mx+mn)/510,dd=(mx-mn)/255,s=l<.5?dd/(2*l):dd/(2-2*l);fromHsl(h+dh,s,l,o);c[0]=o[0]*255;c[1]=o[1]*255;c[2]=o[2]*255;}
      if(ds)psSaturate(c,clamp(ds,-100,100)/100);
      if(dl)psLight(c,clamp(dl,-100,100)/100);
      d[i]=R8(c[0]);d[i+1]=R8(c[1]);d[i+2]=R8(c[2]);}};},
  ui:(st,emit,ctx)=>hueSatUI(st,emit,ctx)};
// Color Balance (model: GIMP's version of it). Each tone range has a soft mask over the pixel's HSL lightness (the three
// masks add up to one, so the same move in all three is a plain shift); a full slider moves its channel by 0.7.
// Preserve Luminosity puts the old HSL lightness back.
DEF['color-balance']={label:'Color Balance',key:'Ctrl+B',layer:true,defaults:{shadows:[0,0,0],midtones:[0,0,0],highlights:[0,0,0],preserveLuminosity:true},
  fix(p){for(const k of ['shadows','midtones','highlights'])p[k]=[0,1,2].map(i=>clamp(Math.round(+(p[k]||[])[i]||0),-100,100));return p;},
  px(p){const S=p.shadows.map(v=>v/100),M=p.midtones.map(v=>v/100),H=p.highlights.map(v=>v/100),keep=p.preserveLuminosity;
    if(![...S,...M,...H].some(v=>v))return null;
    return d=>{const t=[0,0,0],o=[0,0,0];for(let i=0;i<d.length;i+=4){const r=d[i]/255,g=d[i+1]/255,b=d[i+2]/255,l=(Math.max(r,g,b)+Math.min(r,g,b))/2;
      const ws=clamp((l-.333)/-.25+.5,0,1)*.7,wm=clamp((l-.333)/.25+.5,0,1)*clamp((l+.333-1)/-.25+.5,0,1)*.7,wh=clamp((l+.333-1)/.25+.5,0,1)*.7;
      let rn=clamp(r+S[0]*ws+M[0]*wm+H[0]*wh,0,1),gn=clamp(g+S[1]*ws+M[1]*wm+H[1]*wh,0,1),bn=clamp(b+S[2]*ws+M[2]*wm+H[2]*wh,0,1);
      if(keep){toHsl(rn,gn,bn,t);fromHsl(t[0],t[1],l,o);rn=o[0];gn=o[1];bn=o[2];}
      d[i]=R8(rn*255);d[i+1]=R8(gn*255);d[i+2]=R8(bn*255);}};},ui:(st,emit)=>colorBalanceUI(st,emit)};
// Black & White: gray = min + (max - mid) x primary + (mid - min) x secondary, where the primary is the strongest
// channel's colour (Reds/Greens/Blues) and the secondary the mix of the two strongest (Yellows/Cyans/Magentas). A pure
// colour becomes exactly its slider's percentage of white. Tint colours the gray (its value is the HSL lightness).
DEF['black-white']={label:'Black & White',key:'Alt+Shift+Ctrl+B',layer:true,
  defaults:{reds:40,yellows:60,greens:40,cyans:60,blues:20,magentas:80,tint:false,tintHue:42,tintSaturation:20},
  px(p){const wr=p.reds/100,wy=p.yellows/100,wg=p.greens/100,wc=p.cyans/100,wb=p.blues/100,wm=p.magentas/100;
    let tl=null;if(p.tint){tl=new Uint8Array(768);const o=[0,0,0],s=clamp(p.tintSaturation,0,100)/100;for(let v=0;v<256;v++){fromHsl(p.tintHue,s,v/255,o);tl[v*3]=R8(o[0]*255);tl[v*3+1]=R8(o[1]*255);tl[v*3+2]=R8(o[2]*255);}}
    return d=>{for(let i=0;i<d.length;i+=4){const r=d[i],g=d[i+1],b=d[i+2];let y;
      if(r>=g){if(g>=b)y=b+(r-g)*wr+(g-b)*wy;else if(r>=b)y=g+(r-b)*wr+(b-g)*wm;else y=g+(b-r)*wb+(r-g)*wm;}
      else{if(r>=b)y=b+(g-r)*wg+(r-b)*wy;else if(g>=b)y=r+(g-b)*wg+(b-r)*wc;else y=r+(b-g)*wb+(g-r)*wc;}
      const v=R8(y);if(tl){d[i]=tl[v*3];d[i+1]=tl[v*3+1];d[i+2]=tl[v*3+2];}else d[i]=d[i+1]=d[i+2]=v;}};},ui:(st,emit)=>blackWhiteUI(st,emit)};
// Photo Filter (model): the colour multiplies the image at Density (a coloured lens: c x (1 - d + d x filter)); Preserve
// Luminosity then puts the old luminosity back with the Luminosity blend math.
const FILTERS=[['warming-85','Warming Filter (85)','#ec8a00'],['warming-lba','Warming Filter (LBA)','#fa9600'],['warming-81','Warming Filter (81)','#ebb113'],
  ['cooling-80','Cooling Filter (80)','#006dff'],['cooling-lbb','Cooling Filter (LBB)','#005dff'],['cooling-82','Cooling Filter (82)','#00b5ff'],['red','Red','#ea1a1a'],
  ['orange','Orange','#f38417'],['yellow','Yellow','#f9e31c'],['green','Green','#19c919'],['cyan','Cyan','#1dcbea'],['blue','Blue','#1d35ea'],['violet','Violet','#9b1dea'],
  ['magenta','Magenta','#e318e3'],['sepia','Sepia','#ac7a33'],['deep-red','Deep Red','#ff0000'],['deep-blue','Deep Blue','#0022cd'],['deep-emerald','Deep Emerald','#008c00'],
  ['deep-yellow','Deep Yellow','#ffd500'],['underwater','Underwater','#00c1b1']];
DEF['photo-filter']={label:'Photo Filter',layer:true,defaults:{filter:'warming-85',color:'#ec8a00',density:25,preserveLuminosity:true},
  fix(p){const f=FILTERS.find(x=>x[0]===p.filter);if(f)p.color=f[2];else p.filter='custom';if(!/^#[0-9a-f]{6}$/i.test(p.color))p.color='#ec8a00';p.color=p.color.toLowerCase();return p;},
  px(p){const f=U.rgbOf(p.color).map(v=>v/255),dn=clamp(p.density,0,100)/100,keep=p.preserveLuminosity;if(!dn)return null;const m=f.map(v=>1-dn+dn*v);
    return d=>{const c=[0,0,0];for(let i=0;i<d.length;i+=4){const r=d[i]/255,g=d[i+1]/255,b=d[i+2]/255;c[0]=r*m[0];c[1]=g*m[1];c[2]=b*m[2];
      if(keep)setLum(c,.3*r+.59*g+.11*b);d[i]=R8(c[0]*255);d[i+1]=R8(c[1]*255);d[i+2]=R8(c[2]*255);}};},ui:(st,emit)=>photoFilterUI(st,emit)};
// Channel Mixer (exact): output = R x red% + G x green% + B x blue% + constant% x 255, per output channel.
const MIX=(r,g,b)=>({r,g,b,c:0});
DEF['channel-mixer']={label:'Channel Mixer',layer:true,defaults:()=>({red:MIX(100,0,0),green:MIX(0,100,0),blue:MIX(0,0,100),monochrome:false,gray:MIX(40,40,20)}),
  px(p){const rows=p.monochrome?[p.gray,p.gray,p.gray]:[p.red,p.green,p.blue],m=rows.map(o=>[o.r/100,o.g/100,o.b/100,o.c/100*255]);
    if(!p.monochrome&&m.every((q,k)=>q[3]===0&&q.slice(0,3).every((v,j)=>v===(j===k?1:0))))return null;
    return d=>{for(let i=0;i<d.length;i+=4){const r=d[i],g=d[i+1],b=d[i+2];for(let k=0;k<3;k++){const q=m[k];d[i+k]=R8(r*q[0]+g*q[1]+b*q[2]+q[3]);}}};},ui:(st,emit)=>mixerUI(st,emit)};
DEF.invert={label:'Invert',key:'Ctrl+I',gray:true,layer:true,dialog:false,defaults:{},lut:()=>same(table(v=>255-v)),ui:()=>({node:el('p','hint adj-none','Invert has no settings: every color becomes its opposite.'),sync(){}})};
// Posterize: equal-width bins, bin k of n shows k x 255 / (n - 1).
DEF.posterize={label:'Posterize',gray:true,layer:true,defaults:{levels:4},fix(p){p.levels=clamp(Math.round(p.levels),2,255);return p;},
  lut:p=>same(table(v=>Math.floor(v*p.levels/256)*255/(p.levels-1))),ui:(st,emit)=>form(st,emit,[{key:'levels',label:'Levels',type:'range',min:2,max:255,step:1,title:'How many tones each channel keeps'}])};
DEF.threshold={label:'Threshold',gray:true,layer:true,defaults:{level:128},fix(p){p.level=clamp(Math.round(p.level),1,255);return p;},
  px(p){const t=p.level;return d=>{for(let i=0;i<d.length;i+=4){const v=R8(luma(d[i],d[i+1],d[i+2]))>=t?255:0;d[i]=d[i+1]=d[i+2]=v;}};},ui:(st,emit,ctx)=>thresholdUI(st,emit,ctx)};
// Gradient Map: each pixel's luminance picks a colour along the gradient (black end at the left).
DEF['gradient-map']={label:'Gradient Map',layer:true,defaults:()=>({gradient:twoStops(Ed.fg,Ed.bg),reverse:false,dither:false}),staticDefaults:()=>({gradient:twoStops('#000000','#ffffff'),reverse:false,dither:false}),
  fix(p){if(!p.gradient||!Array.isArray(p.gradient.stops)||!p.gradient.stops.length)p.gradient=twoStops('#000000','#ffffff');return p;},
  px(p){const lut=gradientTable(p.gradient,p.reverse);
    return(d,w,h,region)=>{const x0=region?region.x:0,y0=region?region.y:0;for(let i=0,j=0;i<d.length;i+=4,j++){let y=luma(d[i],d[i+1],d[i+2]);
      if(p.dither){const x=x0+j%w,yy=y0+(j/w|0);y+=(((x*2654435761^yy*2246822519)>>>0)%1000/1000-.5)*.98;}
      const v=R8(y)*3;d[i]=lut[v];d[i+1]=lut[v+1];d[i+2]=lut[v+2];}};},ui:(st,emit)=>gradientUI(st,emit)};
// Selective Color (model: FFmpeg's reverse-engineered selectivecolor). A pixel belongs to Reds/Greens/Blues when that
// channel is the largest (weight max - mid), to Cyans/Magentas/Yellows when the opposite channel is the smallest (mid -
// min), Whites/Neutrals/Blacks by brightness. Cyan moves red, Magenta green, Yellow blue; Black darkens all three.
// Relative scales the move by how much of that ink the colour already has.
const SC_KEYS=['reds','yellows','greens','cyans','blues','magentas','whites','neutrals','blacks'];
DEF['selective-color']={label:'Selective Color',layer:true,defaults:()=>{const o={method:'relative'};for(const k of SC_KEYS)o[k]=[0,0,0,0];return o;},
  fix(p){for(const k of SC_KEYS)p[k]=[0,1,2,3].map(i=>clamp(Math.round(+(p[k]||[])[i]||0),-100,100));if(p.method!=='absolute')p.method='relative';return p;},
  px(p){const rel=p.method!=='absolute',act=SC_KEYS.map((k,n)=>[n,p[k].map(v=>v/100)]).filter(a=>a[1].some(v=>v));if(!act.length)return null;
    const adj=(sc,v,a,k)=>{let r=(-1-a)*k-a;if(rel)r*=1-v;return rint(clamp(r,-v,1-v)*sc);};
    return d=>{for(let i=0;i<d.length;i+=4){const r=d[i],g=d[i+1],b=d[i+2],mx=Math.max(r,g,b),mn=Math.min(r,g,b),md=r+g+b-mx-mn,rn=r/255,gn=g/255,bn=b/255;let ar=0,ag=0,ab=0;
      for(let q=0;q<act.length;q++){const n=act[q][0],a=act[q][1];let sc=0;
        switch(n){case 0:if(r===mx)sc=mx-md;break;case 1:if(b===mn)sc=md-mn;break;case 2:if(g===mx)sc=mx-md;break;case 3:if(r===mn)sc=md-mn;break;case 4:if(b===mx)sc=mx-md;break;
          case 5:if(g===mn)sc=md-mn;break;case 6:if(r>128&&g>128&&b>128)sc=2*mn-255;break;case 7:if((r||g||b)&&!(r===255&&g===255&&b===255))sc=(510-(Math.abs(2*mx-255)+Math.abs(2*mn-255))+1)>>1;break;
          case 8:if(r<128&&g<128&&b<128)sc=255-2*mx;break;}
        if(sc<=0)continue;ar+=adj(sc,rn,a[0],a[3]);ag+=adj(sc,gn,a[1],a[3]);ab+=adj(sc,bn,a[2],a[3]);}
      if(ar||ag||ab){d[i]=R8(r+ar);d[i+1]=R8(g+ag);d[i+2]=R8(b+ab);}}};},ui:(st,emit)=>selectiveUI(st,emit)};
// Commands only (as in Photoshop: no adjustment layer).
DEF.desaturate={label:'Desaturate',key:'Shift+Ctrl+U',dialog:false,defaults:{},   // Photoshop: the HSL lightness, (max + min) / 2
  px:()=>d=>{for(let i=0;i<d.length;i+=4){const r=d[i],g=d[i+1],b=d[i+2],v=R8((Math.max(r,g,b)+Math.min(r,g,b))/2);d[i]=d[i+1]=d[i+2]=v;}}};
// Equalize: the cumulative histogram of all three channels together maps every channel (one table, so colours keep
// their balance); the darkest value present becomes 0 and the lightest 255.
DEF.equalize={label:'Equalize',gray:true,dialog:false,defaults:{},whole(D,p,sel){const t=equalizeTable(histogram(D.data,sel));lutKernel(same(t))(D.data);}};

function twoStops(a,b){return{stops:[{at:0,color:a||'#000000',mid:50},{at:100,color:b||'#ffffff',mid:50}]};}
// A gradient as 256 RGB entries from stops {at (percent), color, mid (percent)}, with Photoshop's midpoints: between two
// stops the mix u is bent so that it is one half at the midpoint (u^(log .5 / log mid)).
function gradientTable(gr,reverse){const out=new Uint8Array(768);
  const raw=(gr&&gr.stops||[]).filter(s=>s&&isFinite(+s.at));const frac=raw.length&&raw.every(s=>+s.at<=1);
  const S=raw.map(s=>({at:clamp(+s.at/(frac?1:100),0,1),rgb:U.rgbOf(s.color),mid:s.mid==null?.5:clamp(+s.mid/(+s.mid>1?100:1),.01,.99)})).sort((a,b)=>a.at-b.at);
  if(!S.length)S.push({at:0,rgb:[0,0,0],mid:.5},{at:1,rgb:[255,255,255],mid:.5});
  for(let v=0;v<256;v++){const t=reverse?1-v/255:v/255;let c;
    if(t<=S[0].at)c=S[0].rgb;else if(t>=S[S.length-1].at)c=S[S.length-1].rgb;
    else{let i=0;while(i<S.length-2&&t>S[i+1].at)i++;const a=S[i],b=S[i+1];let u=b.at>a.at?(t-a.at)/(b.at-a.at):1;if(a.mid!==.5)u=Math.pow(u,Math.log(.5)/Math.log(a.mid));c=[0,1,2].map(k=>a.rgb[k]+(b.rgb[k]-a.rgb[k])*u);}
    out[v*3]=R8(c[0]);out[v*3+1]=R8(c[1]);out[v*3+2]=R8(c[2]);}
  return out;}

// ----- parameters and kernels ----------------------------------------------------------------------------------------
function rawDefaults(type,dynamic){const d=DEF[type];if(!d)return{};const v=!dynamic&&d.staticDefaults?d.staticDefaults:d.defaults;return clone(typeof v==='function'?v():v);}
const defaultsOf=type=>normalize(type,rawDefaults(type,true));
// Defaults deep-merged with what was given (numbers checked, arrays taken whole), then repaired by def.fix.
function merge(def,v){if(Array.isArray(def))return Array.isArray(v)?clone(v):clone(def);
  if(def&&typeof def==='object'){const o={};for(const k of Object.keys(def))o[k]=merge(def[k],v&&typeof v==='object'?v[k]:undefined);return o;}
  if(typeof def==='number'){const n=typeof v==='string'&&v.trim()===''?NaN:Number(v);return v!=null&&typeof v!=='boolean'&&isFinite(n)?n:def;}
  if(typeof def==='boolean')return typeof v==='boolean'?v:def;
  if(typeof def==='string')return typeof v==='string'?v:def;return v===undefined?def:clone(v);}
function normalize(type,p){const d=DEF[type];if(!d)return clone(p||{});const o=merge(rawDefaults(type,false),p||{});
  if(type==='gradient-map'&&p&&p.gradient&&typeof p.gradient==='object')o.gradient=clone(p.gradient);
  return d.fix?d.fix(o):o;}
const kcache=new Map();
function kernel(type,p){const key=type+'|'+JSON.stringify(p);if(kcache.has(key))return kcache.get(key);const d=DEF[type];let k=null;
  if(d.lut){const t=d.lut(p);k=t.every(isIdentity)?null:lutKernel(t);}else if(d.px)k=d.px(p)||null;
  kcache.set(key,k);if(kcache.size>48)kcache.delete(kcache.keys().next().value);return k;}
// Apply an adjustment to ImageData in place (p already normalized). sel = the selection's RGBA in the same rectangle.
function process(type,p,D,region,sel){const d=DEF[type];if(d.whole){d.whole(D,p,sel,region);return D;}const k=kernel(type,p);if(k)k(D.data,D.width,D.height,region);return D;}
// A layer mask as a gray picture and back (the mask value is its alpha, RGB white).
function toGray(D){const d=D.data;for(let i=0;i<d.length;i+=4){const a=d[i+3];d[i]=d[i+1]=d[i+2]=a;d[i+3]=255;}return D;}
function fromGray(D){const d=D.data;for(let i=0;i<d.length;i+=4){const v=R8(luma(d[i],d[i+1],d[i+2]));d[i]=d[i+1]=d[i+2]=255;d[i+3]=v;}return D;}
// The soft selection's share: out = before + (after - before) x selection.
function mixSelection(out,before,sel){for(let i=0;i<out.length;i+=4){const s=sel[i+3];if(s===255)continue;if(!s){out[i]=before[i];out[i+1]=before[i+1];out[i+2]=before[i+2];out[i+3]=before[i+3];continue;}
  const k=s/255;for(let c=0;c<3;c++)out[i+c]=Math.round(before[i+c]+(out[i+c]-before[i+c])*k);}}

for(const [type,d] of Object.entries(DEF))if(d.layer)Ed.registerAdjustment(type,{label:d.label,defaults:rawDefaults(type,false),
  apply(img,params,info){process(type,normalize(type,params),img,info&&info.region,null);}});

// ----- picking colours from the picture (Levels, Curves and Replace Color eyedroppers) -------------------------------
// While an eyedropper is on, a left click on the picture samples instead of using the tool. A dialog becomes non-modal
// for as long (so the picture can be clicked) and everything else stays blocked as if it were modal.
const Sampler=(()=>{let cur=null;
  const view=()=>document.getElementById('view');
  function down(e){if(!cur)return;if(cur.dialog&&!cur.dialog.open){stop();return;}const t=e.target,v=view();if(cur.button&&cur.button.contains(t))return;if(cur.dialog&&cur.dialog.contains(t))return;
    if(v&&t===v&&e.button===0){e.preventDefault();e.stopImmediatePropagation();const q=Ed.view.toImage(e.clientX,e.clientY);try{cur.pick(q.x,q.y);}catch(err){console.error('[adjust] eyedropper:',err);}return;}
    if(cur.dialog){if(!(v&&t===v)){e.preventDefault();e.stopImmediatePropagation();}return;}   // middle/right drags on the picture still pan
    stop();}
  function key(e){if(cur&&e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();stop();}}
  function start(o){stop();cur=o;if(o.dialog&&o.dialog.open&&o.dialog.matches(':modal')){o.dialog.close();o.dialog.classList.add('adj-nonmodal');o.dialog.show();o.modal=true;}
    document.addEventListener('pointerdown',down,true);document.addEventListener('keydown',key,true);document.body.classList.add('adj-sampling');if(o.button)o.button.setAttribute('aria-pressed','true');}
  function stop(){const o=cur;if(!o)return;cur=null;document.removeEventListener('pointerdown',down,true);document.removeEventListener('keydown',key,true);document.body.classList.remove('adj-sampling');
    if(o.button)o.button.setAttribute('aria-pressed','false');if(o.dialog)o.dialog.classList.remove('adj-nonmodal');if(o.modal&&o.dialog.isConnected&&o.dialog.open){o.dialog.close();o.dialog.showModal();if(o.button)o.button.focus();}}
  return{start,stop,get active(){return cur;}};})();
const sampleSize=()=>{const o=Ed.tools.get('eyedropper')?Ed.toolOptions('eyedropper'):{};return clamp(+(o.sample||1)|0,1,101);};
// Average an n x n square of an ImageData covering rect R (null when outside or transparent).
function averageAt(D,R,x,y,n){const h=(n-1)>>1,x0=Math.floor(x)-h,y0=Math.floor(y)-h;let r=0,g=0,b=0,a=0;
  for(let yy=y0;yy<y0+n;yy++)for(let xx=x0;xx<x0+n;xx++){if(xx<R.x||yy<R.y||xx>=R.x+R.w||yy>=R.y+R.h)continue;const i=((yy-R.y)*R.w+(xx-R.x))*4,al=D.data[i+3];r+=D.data[i]*al;g+=D.data[i+1]*al;b+=D.data[i+2]*al;a+=al;}
  return a?[r/a,g/a,b/a]:null;}

// ----- controls ------------------------------------------------------------------------------------------------------
const getP=(o,path)=>path.split('.').reduce((a,k)=>a==null?a:a[k],o);
const setP=(o,path,v)=>{const ks=path.split('.'),last=ks.pop();ks.reduce((a,k)=>a[k],o)[last]=v;};
// Rows of standard controls (Ed.ui.field) bound to paths in the state. Each row: a field descriptor with key = path, and
// optional show(st), track (CSS background of a range), rtl, after(st).
function form(st,emit,rows,cls){const box=el('div','adj-ui adj-form'+(cls?' '+cls:'')),list=[];
  for(const r of rows){if(r.section){const h=el('div','adj-section',r.section);box.append(h);list.push({r,node:h});continue;}
    const f=Ed.ui.field({...r,key:r.key.replace(/\./g,'-')},getP(st,r.key),(v,done)=>{let val=v;if(r.type==='range'||r.type==='number'){val=Number(v);if(!isFinite(val))return;val=clamp(val,r.min??-Infinity,r.max??Infinity);}
      setP(st,r.key,val);if(r.after)r.after(st);emit(!!done);if(r.after||rows.some(x=>x.show))sync();});
    if(r.track||r.rtl){const rg=f.node.querySelector('input[type=range]');if(rg){if(r.track){f.node.classList.add('adj-tracked');rg.style.setProperty('--track',r.track);}if(r.rtl)rg.dir='rtl';}}
    box.append(f.node);list.push({r,f,node:f.node});}
  function sync(){for(const x of list){if(x.f)x.f.sync(getP(st,x.r.key));if(x.r.show)x.node.hidden=!x.r.show(st);}}
  sync();return{node:box,sync};}
// A slider with a word at each end (Color Balance's Cyan ... Red), its number at the right.
function twoSided(key,left,right,min,max,get,set,track){const row=el('div','adj-two');row.dataset.key=key;
  const rg=el('input');rg.type='range';rg.min=min;rg.max=max;rg.step=1;rg.setAttribute('aria-label',left+' – '+right);rg.tabIndex=-1;if(track){row.classList.add('adj-tracked');rg.style.setProperty('--track',track);}
  const n=el('input');n.type='number';n.name=key;n.min=min;n.max=max;n.step=1;n.setAttribute('aria-label',left+' – '+right);
  const val=v=>{const x=Number(v);return v!==''&&isFinite(x)?clamp(Math.round(x),min,max):null;};
  rg.oninput=()=>{n.value=rg.value;set(+rg.value,false);};rg.onchange=()=>set(+rg.value,true);
  n.oninput=()=>{const v=val(n.value);if(v!=null){rg.value=v;set(v,false);}};n.onchange=()=>{const v=val(n.value);if(v!=null){rg.value=v;n.value=v;set(v,true);}else n.value=get();};
  row.append(el('span','adj-two-l',left),rg,el('span','adj-two-r',right),n);
  return{node:row,sync(){const v=get();if(document.activeElement!==n)n.value=v;if(document.activeElement!==rg)rg.value=v;}};}
const select=(key,label,options,value,on,title)=>Ed.ui.field({key,label,type:'select',options,title},value,v=>on(v));
function histCanvas(h=96){const c=el('canvas','adj-hist');c.width=256;c.height=h;return c;}
const HIST_COLOR={rgb:'#aab9c9',red:'#ff6b6b',green:'#5bd875',blue:'#68a2ff',luma:'#aab9c9'};
// Bars scaled like Photoshop's: one huge spike (a flat background) does not flatten the rest.
function drawHist(c,counts,color,bg){const g=c.getContext('2d'),W=c.width,H=c.height;g.clearRect(0,0,W,H);if(bg){g.fillStyle=bg;g.fillRect(0,0,W,H);}if(!counts)return;
  const s=Array.from(counts).sort((a,b)=>b-a);let mx=s[0];if(s[1]>0&&s[0]>s[1]*2.5)mx=s[1]*1.4;if(mx<=0)return;g.fillStyle=color||HIST_COLOR.rgb;
  for(let x=0;x<256;x++){const v=counts[x];if(!v)continue;const hh=Math.max(1,Math.min(1,v/mx)*(H-1));g.fillRect(x*W/256,H-hh,Math.max(1,W/256),hh);}}
const DROP=['M14.5 5.5l4 4','M17 3a2.1 2.1 0 0 1 3 3l-2.5 2.5-3-3z','M15.5 8.5L7 17l-3 1 1-3 8.5-8.5'];
function dropper(kind,title,ctx,onPick){const b=btn('adj-drop adj-drop-'+kind,'',title);b.dataset.drop=kind;b.setAttribute('aria-pressed','false');b.append(Ed.icon(DROP,{size:15}));
  b.onclick=()=>{if(b.getAttribute('aria-pressed')==='true'){Sampler.stop();return;}ctx.sample(onPick,b);};return b;}
// Triangles under a track (Levels, Threshold): drag them, or focus one and use the arrow keys (Shift = 10).
function knob(trackEl,kind,title,onDrag,onStep){const k=el('span','adj-knob adj-knob-'+kind);k.tabIndex=0;k.setAttribute('role','slider');k.setAttribute('aria-label',title.split(':')[0]);k.title=title;k.dataset.knob=kind;trackEl.append(k);
  k.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();k.focus({preventScroll:true});k.setPointerCapture(e.pointerId);const r=trackEl.getBoundingClientRect();
    const mv=ev=>onDrag(clamp((ev.clientX-r.left)/r.width,0,1)*255,false);
    const up=()=>{k.removeEventListener('pointermove',mv);k.removeEventListener('pointerup',up);k.removeEventListener('pointercancel',up);onDrag(null,true);};
    k.addEventListener('pointermove',mv);k.addEventListener('pointerup',up);k.addEventListener('pointercancel',up);});
  k.addEventListener('keydown',e=>{const s=e.key==='ArrowLeft'||e.key==='ArrowDown'?-1:e.key==='ArrowRight'||e.key==='ArrowUp'?1:0;if(!s)return;e.preventDefault();e.stopPropagation();onStep(s*(e.shiftKey?10:1));});
  return k;}
const place=(k,v)=>{k.style.left=(clamp(v,0,255)/255*100)+'%';k.setAttribute('aria-valuenow',String(Math.round(v*100)/100));};
const chanOptions=ctx=>ctx.gray?[{value:'rgb',label:'Gray'}]:[{value:'rgb',label:'RGB'},{value:'red',label:'Red'},{value:'green',label:'Green'},{value:'blue',label:'Blue'}];

function bcUI(st,emit){const lim=()=>st.legacy?[-100,100,-100,100]:[-150,150,-50,100];
  const f=form(st,emit,[{key:'brightness',label:'Brightness',type:'range',min:-150,max:150,step:1,title:'Lightens or darkens the midtones (black and white stay put unless Use Legacy is on)'},
    {key:'contrast',label:'Contrast',type:'range',min:-50,max:100,step:1,title:'Spreads the tones apart (+) or brings them together (-)'},
    {key:'legacy',label:'Use Legacy',type:'checkbox',title:'The old Photoshop math: shifts every value and stretches in a straight line (it can clip)',after:s=>{const[a,b,c,d]=lim();s.brightness=clamp(s.brightness,a,b);s.contrast=clamp(s.contrast,c,d);ranges();}}]);
  function ranges(){const[a,b,c,d]=lim();for(const [k,lo,hi] of [['brightness',a,b],['contrast',c,d]])for(const i of f.node.querySelectorAll(`[data-key="${k}"] input`)){i.min=lo;i.max=hi;}}
  ranges();return{node:f.node,sync(){ranges();f.sync();}};}
function exposureUI(st,emit){return form(st,emit,[{key:'exposure',label:'Exposure',type:'range',min:-20,max:20,step:.01,title:'Brightens or darkens the highlights the most, like a longer or shorter photo exposure (in stops)'},
  {key:'offset',label:'Offset',type:'range',min:-.5,max:.5,step:.0001,title:'Lifts or lowers the shadows and midtones'},
  {key:'gamma',label:'Gamma',type:'range',min:.01,max:9.99,step:.01,rtl:true,title:'Gamma Correction: a power curve; larger values (left) brighten'}]);}

// Levels: histogram, input triangles (black, gray = gamma, white), output triangles, the numbers, Auto and eyedroppers.
function levelsUI(st,emit,ctx){let ch='rgb';const root=el('div','adj-ui adj-levels');const cur=()=>st[ch];
  const chF=select('channel','Channel',chanOptions(ctx),ch,v=>{ch=v;sync();},'RGB changes all three colors; Red, Green or Blue changes one');
  const auto=btn('adj-btn','Auto','Auto: makes the darkest 0.1% of each channel black and the lightest 0.1% white');auto.dataset.action='auto';
  auto.onclick=()=>{const p=ctx.auto&&ctx.auto('tone');if(!p)return;for(const c of ['rgb',...RGB3])Object.assign(st[c],p[c]);emit(true);sync();};
  const top=el('div','adj-row');top.append(chF.node,auto);
  const hc=histCanvas(),inT=el('div','adj-track'),outT=el('div','adj-track adj-out');
  const gx=p=>p.inBlack+(p.inWhite-p.inBlack)*Math.pow(.5,p.gamma);
  const setIB=v=>{const p=cur();p.inBlack=clamp(Math.round(v),0,p.inWhite-2);},setIW=v=>{const p=cur();p.inWhite=clamp(Math.round(v),p.inBlack+2,255);};
  const setG=v=>{const p=cur(),t=clamp((v-p.inBlack)/(p.inWhite-p.inBlack),.001,.999);p.gamma=+clamp(Math.log(t)/Math.log(.5),.1,9.99).toFixed(2);};
  const setOB=v=>{cur().outBlack=clamp(Math.round(v),0,255);},setOW=v=>{cur().outWhite=clamp(Math.round(v),0,255);};
  const drag=set=>(v,done)=>{if(v!=null){set(v);emit(false);}else emit(true);sync();};
  const step=(set,get)=>s=>{set(get()+s);emit(true);sync();};
  const kb=knob(inT,'black','Shadows: input values at or below this become black',drag(setIB),step(setIB,()=>cur().inBlack));
  const kg=knob(inT,'gray','Midtones (gamma): drag left to brighten, right to darken',drag(setG),step(setG,()=>gx(cur())));
  const kw=knob(inT,'white','Highlights: input values at or above this become white',drag(setIW),step(setIW,()=>cur().inWhite));
  const kob=knob(outT,'black','Output black: the darkest the result gets',drag(setOB),step(setOB,()=>cur().outBlack));
  const kow=knob(outT,'white','Output white: the lightest the result gets',drag(setOW),step(setOW,()=>cur().outWhite));
  const num=(key,title,min,max,stp,set)=>{const f=Ed.ui.field({key,type:'number',min,max,step:stp,title},0,(v,done)=>{const x=Number(v);if(!isFinite(x))return;set(x);emit(!!done);sync();});return f;};
  const nIB=num('inBlack','Input black (shadows)',0,253,1,setIB),nG=num('gamma','Gamma (midtones)',.1,9.99,.01,v=>{cur().gamma=clamp(v,.1,9.99);}),nIW=num('inWhite','Input white (highlights)',2,255,1,setIW);
  const nOB=num('outBlack','Output black',0,255,1,setOB),nOW=num('outWhite','Output white',0,255,1,setOW);
  const inRow=el('div','adj-nums');inRow.append(nIB.node,nG.node,nIW.node);const outRow=el('div','adj-nums');outRow.append(nOB.node,nOW.node);
  const drops=el('div','adj-drops');
  const pick=kind=>rgb=>{pickLevels(st,kind,rgb,ctx.gray);emit(true);sync();};
  drops.append(dropper('black','Set Black Point: click it, then click something in the picture that should be black',ctx,pick('black')),
    dropper('gray','Set Gray Point: click it, then click something that should be neutral gray',ctx,pick('gray')),
    dropper('white','Set White Point: click it, then click something that should be white',ctx,pick('white')));
  const hw=el('div','adj-histbox');hw.append(hc,inT);
  root.append(top,el('div','adj-label','Input Levels'),hw,inRow,el('div','adj-label','Output Levels'),outT,outRow,drops);
  function sync(){const p=cur(),h=ctx.hist&&ctx.hist();drawHist(hc,h&&h[ch],HIST_COLOR[ch],'#0d131b');chF.sync(ch);
    place(kb,p.inBlack);place(kg,gx(p));place(kw,p.inWhite);place(kob,p.outBlack);place(kow,p.outWhite);
    nIB.sync(p.inBlack);nG.sync(p.gamma.toFixed(2));nIW.sync(p.inWhite);nOB.sync(p.outBlack);nOW.sync(p.outWhite);}
  sync();return{node:root,sync};}
// Eyedroppers: black/white set each channel's input black/white to the picked colour; gray sets each channel's gamma so
// the picked colour comes out neutral (at its average).
function pickLevels(st,kind,rgb,gray){const chans=gray?['rgb']:RGB3,vals=gray?[rgb[0]]:rgb,t=(rgb[0]+rgb[1]+rgb[2])/3;
  chans.forEach((c,k)=>{const p=st[c],v=Math.round(vals[k]);
    if(kind==='black')p.inBlack=clamp(v,0,p.inWhite-2);else if(kind==='white')p.inWhite=clamp(v,p.inBlack+2,255);
    else{const u=clamp((vals[k]-p.inBlack)/(p.inWhite-p.inBlack),.004,.996),w=clamp((t-p.outBlack)/((p.outWhite-p.outBlack)||1),.004,.996);p.gamma=+clamp(Math.log(u)/Math.log(w),.1,9.99).toFixed(2);}});}

// Curves: a graph with the histogram behind, points to add (click), drag, delete (drag off the graph or Delete key),
// Input/Output for the selected point, presets, Auto and eyedroppers.
const CURVE_PRESETS={'Darker (RGB)':{rgb:[[0,0],[140,105],[255,255]]},'Increase Contrast (RGB)':{rgb:[[0,0],[64,54],[192,202],[255,255]]},
  'Lighter (RGB)':{rgb:[[0,0],[115,150],[255,255]]},'Linear Contrast (RGB)':{rgb:[[0,0],[64,50],[192,206],[255,255]]},
  'Medium Contrast (RGB)':{rgb:[[0,0],[64,47],[192,209],[255,255]]},'Negative (RGB)':{rgb:[[0,255],[255,0]]},'Strong Contrast (RGB)':{rgb:[[0,0],[64,37],[192,219],[255,255]]}};
const CURVE_COLOR={rgb:'#e8eef6',red:'#ff6b6b',green:'#5bd875',blue:'#68a2ff'};
function curvesUI(st,emit,ctx){let ch='rgb',sel=0,preset='Default';const root=el('div','adj-ui adj-curves');const pts=()=>st[ch];
  const presetF=select('preset','Preset',[{value:'Default',label:'Default'},...Object.keys(CURVE_PRESETS).map(k=>({value:k,label:k})),{value:'Custom',label:'Custom'}],preset,v=>{
      preset=v;if(v==='Custom')return;for(const c of ['rgb',...RGB3])st[c]=clone((CURVE_PRESETS[v]||{})[c]||LINE());sel=0;emit(true);sync();},'Ready-made curves');
  const custom=()=>{if(preset!=='Custom'){preset='Custom';presetF.sync(preset);}};
  const chF=select('channel','Channel',chanOptions(ctx),ch,v=>{ch=v;sel=0;sync();},'RGB changes all three colors; Red, Green or Blue changes one');
  const auto=btn('adj-btn','Auto','Auto: makes the darkest 0.1% of each channel black and the lightest 0.1% white');auto.dataset.action='auto';
  auto.onclick=()=>{const p=ctx.auto&&ctx.auto('tone');if(!p)return;for(const c of ['rgb',...RGB3])st[c]=[[p[c].inBlack,p[c].outBlack],[p[c].inWhite,p[c].outWhite]];custom();sel=0;emit(true);sync();};
  const top=el('div','adj-row');top.append(chF.node,auto);
  const wrap=el('div','adj-graphwrap'),G=el('canvas','adj-graph');G.tabIndex=0;G.setAttribute('role','application');
  G.setAttribute('aria-label','Curve graph: click to add a point and drag it; Delete removes the selected point, arrow keys move it, + and - pick the next or previous point');
  G.title='Click to add a point, drag to move it, drag it off the graph (or press Delete) to remove it';wrap.append(G);
  let size=256,dpr=1;const maxSize=ctx.mode==='panel'?320:256;
  function resize(){const w=Math.floor(wrap.clientWidth)||maxSize;const s=Math.max(120,Math.min(maxSize,w-2));dpr=Math.min(2,window.devicePixelRatio||1);
    if(s===size&&G.width===Math.round(s*dpr))return;size=s;G.style.width=G.style.height=size+'px';G.width=G.height=Math.round(size*dpr);draw();}
  G.style.width=G.style.height=size+'px';G.width=G.height=size;
  if(window.ResizeObserver)new ResizeObserver(resize).observe(wrap);
  const X=v=>v/255*size,Y=v=>(1-v/255)*size;
  function draw(){const g=G.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,size,size);g.fillStyle='#0d131b';g.fillRect(0,0,size,size);
    const h=ctx.hist&&ctx.hist();if(h&&h[ch]){const c=h[ch],s=Array.from(c).sort((a,b)=>b-a);let mx=s[0];if(s[1]>0&&s[0]>s[1]*2.5)mx=s[1]*1.4;
      if(mx>0){g.fillStyle='#253242';for(let v=0;v<256;v++){if(!c[v])continue;const hh=Math.min(1,c[v]/mx)*size*.92;g.fillRect(X(v),size-hh,Math.max(1,size/256)+.4,hh);}}}
    g.lineWidth=1;g.strokeStyle='#2c394a';g.beginPath();for(let i=1;i<4;i++){const q=Math.round(i*size/4)+.5;g.moveTo(q,0);g.lineTo(q,size);g.moveTo(0,q);g.lineTo(size,q);}g.stroke();
    g.strokeStyle='#3d4d60';g.beginPath();g.moveTo(0,size);g.lineTo(size,0);g.stroke();
    if(ch==='rgb')for(const c of RGB3){const P=st[c];if(P.length===2&&P[0][0]===0&&P[0][1]===0&&P[1][0]===255&&P[1][1]===255)continue;curve(g,P,CURVE_COLOR[c],.55,1);}
    curve(g,pts(),CURVE_COLOR[ch],1,1.6);
    pts().forEach((p,i)=>{const x=X(p[0]),y=Y(p[1]);g.lineWidth=1.2;if(i===sel){g.fillStyle=CURVE_COLOR[ch];g.fillRect(x-4,y-4,8,8);g.strokeStyle='#0d131b';g.strokeRect(x-4.5,y-4.5,9,9);}
      else{g.fillStyle='#0d131b';g.fillRect(x-3.5,y-3.5,7,7);g.strokeStyle=CURVE_COLOR[ch];g.strokeRect(x-3.5,y-3.5,7,7);}});}
  function curve(g,P,color,alpha,width){const f=spline(P);g.save();g.globalAlpha=alpha;g.strokeStyle=color;g.lineWidth=width;g.beginPath();
    for(let x=0;x<=size;x++){const v=clamp(f(x/size*255),0,255),y=Y(v);x?g.lineTo(x,y):g.moveTo(x,y);}g.stroke();g.restore();}
  const range=i=>{const P=pts();return[i>0?P[i-1][0]+1:0,i<P.length-1?P[i+1][0]-1:255];};
  G.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();G.focus({preventScroll:true});const r=G.getBoundingClientRect(),sx=size/r.width;
    const lx=(e.clientX-r.left)*sx,ly=(e.clientY-r.top)*sx;let P=pts(),i=P.findIndex(p=>Math.abs(X(p[0])-lx)<=6&&Math.abs(Y(p[1])-ly)<=6);
    if(i<0){const x=clamp(Math.round(lx/size*255),0,255),y=clamp(Math.round((1-ly/size)*255),0,255);i=P.findIndex(p=>p[0]===x);
      if(i<0){if(P.length>=14){Ed.message('A curve can have up to 14 points.');return;}P.push([x,y]);P.sort((a,b)=>a[0]-b[0]);i=P.findIndex(p=>p[0]===x);}else P[i][1]=y;custom();emit(false);}
    sel=i;sync();const pt=P[i];let removed=false;G.setPointerCapture(e.pointerId);
    const mv=ev=>{const qx=(ev.clientX-r.left)*sx,qy=(ev.clientY-r.top)*sx,far=qx<-24||qy<-24||qx>size+24||qy>size+24;let P=pts();
      if(far&&!removed){const k=P.indexOf(pt);if(k>0&&k<P.length-1){P.splice(k,1);removed=true;sel=Math.max(0,k-1);custom();emit(false);sync();}return;}
      if(far)return;if(removed){if(P.length>=14)return;removed=false;P.push(pt);P.sort((a,b)=>a[0]-b[0]);}
      const k=P.indexOf(pt);const[lo,hi]=range(k);pt[0]=clamp(Math.round(qx/size*255),lo,hi);pt[1]=clamp(Math.round((1-qy/size)*255),0,255);sel=k;custom();emit(false);sync();};
    const up=()=>{G.removeEventListener('pointermove',mv);G.removeEventListener('pointerup',up);G.removeEventListener('pointercancel',up);emit(true);sync();};
    G.addEventListener('pointermove',mv);G.addEventListener('pointerup',up);G.addEventListener('pointercancel',up);});
  G.addEventListener('keydown',e=>{const P=pts(),p=P[sel],s=e.shiftKey?10:1;let used=true,change=true;
    if((e.key==='ArrowLeft'||e.key==='ArrowRight')&&p){const[lo,hi]=range(sel);p[0]=clamp(p[0]+(e.key==='ArrowRight'?s:-s),lo,hi);}
    else if((e.key==='ArrowUp'||e.key==='ArrowDown')&&p)p[1]=clamp(p[1]+(e.key==='ArrowUp'?s:-s),0,255);
    else if(e.key==='Delete'||e.key==='Backspace'){if(sel>0&&sel<P.length-1){P.splice(sel,1);sel--;}else change=false;}
    else if(e.key==='+'||e.key==='='){sel=(sel+1)%P.length;change=false;}else if(e.key==='-'||e.key==='_'){sel=(sel-1+P.length)%P.length;change=false;}
    else used=false;
    if(!used)return;e.preventDefault();e.stopPropagation();if(change){custom();emit(true);}sync();});
  const ioF=(key,label,title,k)=>Ed.ui.field({key,label,type:'number',min:0,max:255,step:1,title},0,(v,done)=>{const P=pts(),p=P[sel],x=Number(v);if(!p||!isFinite(x))return;
    if(k===0){const[lo,hi]=range(sel);p[0]=clamp(Math.round(x),lo,hi);}else p[1]=clamp(Math.round(x),0,255);custom();emit(!!done);sync();});
  const inF=ioF('input','Input','Input value of the selected point',0),outF=ioF('output','Output','Output value of the selected point',1);
  const io=el('div','adj-io');io.append(inF.node,outF.node);
  const drops=el('div','adj-drops');const pick=kind=>rgb=>{pickCurves(st,kind,rgb,ctx.gray);custom();emit(true);sync();};
  drops.append(dropper('black','Set Black Point: click it, then click something in the picture that should be black',ctx,pick('black')),
    dropper('gray','Set Gray Point: click it, then click something that should be neutral gray',ctx,pick('gray')),
    dropper('white','Set White Point: click it, then click something that should be white',ctx,pick('white')));
  const bottom=el('div','adj-row adj-spread');bottom.append(io,drops);
  root.append(presetF.node,top,wrap,bottom);
  function sync(){const P=pts();if(sel>=P.length)sel=P.length-1;chF.sync(ch);presetF.sync(preset);const p=P[sel];inF.sync(p?p[0]:'');outF.sync(p?p[1]:'');draw();}
  sync();return{node:root,sync};}
function pickCurves(st,kind,rgb,gray){const chans=gray?['rgb']:RGB3,vals=gray?[rgb[0]]:rgb,t=Math.round((rgb[0]+rgb[1]+rgb[2])/3);
  chans.forEach((c,k)=>{const v=clamp(Math.round(vals[k]),0,255);let P=st[c];
    if(kind==='black'){const last=P[P.length-1];P=P.filter(p=>p[0]>v&&p!==P[0]);if(!P.length||P[P.length-1][0]<=v)P.push(last[0]>v?last:[255,255]);P.unshift([v,0]);}
    else if(kind==='white'){const first=P[0];P=P.filter(p=>p[0]<v&&p!==P[P.length-1]);if(!P.length||P[0][0]>=v)P.unshift(first[0]<v?first:[0,0]);P.push([v,255]);}
    else{P=P.filter(p=>Math.abs(p[0]-v)>2||p===P[0]||p===P[P.length-1]);if(v>P[0][0]&&v<P[P.length-1][0])P.push([v,t]);}
    st[c]=fixCurve(P);});}

// Hue/Saturation: Edit (Master or a colour range), the three sliders, Colorize, and the two colour bars with the range
// handles (drag the four handles or the middle band).
function hueSatUI(st,emit,ctx){let edit='master';const root=el('div','adj-ui adj-huesat');
  const tgt=()=>st.colorize.enabled?st.colorize:st[edit];
  const editF=select('edit','Edit',[{value:'master',label:'Master'},...HS_KEYS.map(k=>({value:k,label:HS_NAMES[k]}))],edit,v=>{edit=v;sync();},'Change every color, or only one color range');
  const sl={};const mk=(k,label,title)=>{const f=Ed.ui.field({key:k,label,type:'range',min:-100,max:100,step:1,title},0,(v,done)=>{const x=Number(v);if(!isFinite(x))return;const t=tgt(),[lo,hi]=lims(k);t[k]=clamp(Math.round(x),lo,hi);emit(!!done);drawBars();});
    f.node.classList.add('adj-tracked');sl[k]=f;return f.node;};
  const lims=k=>st.colorize.enabled?{hue:[0,360],saturation:[0,100],lightness:[-100,100]}[k]:{hue:[-180,180],saturation:[-100,100],lightness:[-100,100]}[k];
  const colF=Ed.ui.field({key:'colorize',label:'Colorize',type:'checkbox',title:'Color the whole picture with one hue; each pixel keeps its lightness'},false,v=>{st.colorize.enabled=!!v;
    if(v){const fg=U.rgbOf(Ed.fg),mx=Math.max(...fg),mn=Math.min(...fg);st.colorize.hue=mx-mn>0?Math.round(hueOf(fg[0],fg[1],fg[2],mx,mn))%360:0;st.colorize.saturation=25;st.colorize.lightness=0;edit='master';}
    emit(true);sync();});
  const text=el('div','adj-range-text');const bars=el('canvas','adj-hsbars');bars.height=44;bars.title='The top bar is before, the bottom bar after. In a color range, drag the handles to widen or move it.';
  root.append(editF.node,mk('hue','Hue','Turns the colors around the color wheel'),mk('saturation','Saturation','More (+) or less (-) colorful; -100 is gray'),mk('lightness','Lightness','Towards white (+) or black (-)'),colF.node,text,bars);
  let W=0,dpr=1;
  const center=()=>{if(st.colorize.enabled||edit==='master')return 180;const R=st[edit].range;return(R[1]+R[2])/2;};
  const hx=h=>((((h-(center()-180))%360)+360)%360)/360*W;
  function drawBars(){const ranged=!st.colorize.enabled&&edit!=='master',BH=ranged?44:26;bars.style.height=BH+'px';W=Math.max(60,Math.floor(bars.clientWidth||root.clientWidth||300));dpr=Math.min(2,window.devicePixelRatio||1);if(bars.width!==Math.round(W*dpr)||bars.height!==Math.round(BH*dpr)){bars.width=Math.round(W*dpr);bars.height=Math.round(BH*dpr);}
    const g=bars.getContext('2d');g.setTransform(1,0,0,1,0,0);g.clearRect(0,0,bars.width,bars.height);const n=bars.width,src=new ImageData(n,1),o=[0,0,0],c0=center()-180;
    for(let x=0;x<n;x++){fromHsl(c0+x/n*360,1,.5,o);src.data[x*4]=R8(o[0]*255);src.data[x*4+1]=R8(o[1]*255);src.data[x*4+2]=R8(o[2]*255);src.data[x*4+3]=255;}
    const after=new ImageData(new Uint8ClampedArray(src.data),n,1);process('hue-saturation',normalize('hue-saturation',st),after,null,null);
    const t1=document.createElement('canvas');t1.width=n;t1.height=1;const tg=t1.getContext('2d');tg.putImageData(src,0,0);g.imageSmoothingEnabled=false;g.drawImage(t1,0,0,n,1,0,0,n,Math.round(12*dpr));
    tg.putImageData(after,0,0);g.drawImage(t1,0,0,n,1,0,Math.round(14*dpr),n,Math.round(12*dpr));
    if(!st.colorize.enabled&&edit!=='master'){const R=st[edit].range,mid=center(),y=Math.round(28*dpr),hh=Math.round(14*dpr),px=h=>(h-mid+180)/360*n;
      g.fillStyle='#55677a';g.beginPath();g.moveTo(px(R[0]),y+hh);g.lineTo(px(R[1]),y+2);g.lineTo(px(R[2]),y+2);g.lineTo(px(R[3]),y+hh);g.closePath();g.fill();
      g.fillStyle='#9fb2c4';g.fillRect(px(R[1]),y+2,Math.max(1,px(R[2])-px(R[1])),hh-2);
      g.fillStyle='#e8eef6';for(const h of R){const x=Math.round(px(h));g.fillRect(x-Math.round(dpr),y,Math.round(2*dpr),hh);}}
    text.textContent=!st.colorize.enabled&&edit!=='master'?(()=>{const R=st[edit].range.map(v=>((v%360)+360)%360);return `${deg(R[0])}/${deg(R[1])} \\ ${deg(R[2])}/${deg(R[3])}`;})():'';}
  bars.addEventListener('pointerdown',e=>{if(e.button!==0||st.colorize.enabled||edit==='master')return;const r=bars.getBoundingClientRect(),R=st[edit].range,mid=center(),px=h=>(h-mid+180)/360*r.width,x=e.clientX-r.left;
    let which=-1,best=7;R.forEach((h,i)=>{const d=Math.abs(px(h)-x);if(d<best){best=d;which=i;}});if(which<0&&x>px(R[1])&&x<px(R[2]))which=4;if(which<0)return;
    e.preventDefault();bars.setPointerCapture(e.pointerId);const start=R.slice(),x0=e.clientX;
    const mv=ev=>{const dh=Math.round((ev.clientX-x0)/r.width*360),N=start.slice();
      if(which===4)for(let i=0;i<4;i++)N[i]=start[i]+dh;else{N[which]=start[which]+dh;for(let i=which+1;i<4;i++)N[i]=Math.max(N[i],N[i-1]);for(let i=which-1;i>=0;i--)N[i]=Math.min(N[i],N[i+1]);
        if(N[3]-N[0]>360){if(which>=2)N[0]=N[3]-360;else N[3]=N[0]+360;}}
      st[edit].range=N;emit(false);drawBars();};
    const up=()=>{bars.removeEventListener('pointermove',mv);bars.removeEventListener('pointerup',up);bars.removeEventListener('pointercancel',up);emit(true);};
    bars.addEventListener('pointermove',mv);bars.addEventListener('pointerup',up);bars.addEventListener('pointercancel',up);});
  if(window.ResizeObserver)new ResizeObserver(()=>drawBars()).observe(bars);
  function sync(){const on=st.colorize.enabled,t=tgt();editF.sync(on?'master':edit);editF.node.querySelector('select').disabled=on;colF.sync(on);
    for(const k of ['hue','saturation','lightness']){const[lo,hi]=lims(k);for(const i of sl[k].node.querySelectorAll('input')){i.min=lo;i.max=hi;}sl[k].sync(t[k]);
      const rg=sl[k].node.querySelector('input[type=range]');if(rg)rg.style.setProperty('--track',k==='hue'?'linear-gradient(90deg,#0ff,#00f,#f0f,#f00,#ff0,#0f0,#0ff)':k==='saturation'?'linear-gradient(90deg,#808080,#f33)':'linear-gradient(90deg,#000,#808080,#fff)');}
    if(on){const rg=sl.hue.node.querySelector('input[type=range]');if(rg)rg.style.setProperty('--track','linear-gradient(90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)');}
    drawBars();}
  sync();return{node:root,sync};}

function colorBalanceUI(st,emit){let tone='midtones';const root=el('div','adj-ui adj-colorbalance');
  const toneF=Ed.ui.field({key:'tone',label:'Tone',type:'segmented',options:[{value:'shadows',label:'Shadows'},{value:'midtones',label:'Midtones'},{value:'highlights',label:'Highlights'}]},tone,v=>{tone=v;sync();});
  const rows=[['cyanRed','Cyan','Red','linear-gradient(90deg,#0ff,#808080,#f00)'],['magentaGreen','Magenta','Green','linear-gradient(90deg,#f0f,#808080,#0f0)'],['yellowBlue','Yellow','Blue','linear-gradient(90deg,#ff0,#808080,#00f)']]
    .map(([key,l,r,track],k)=>twoSided(key,l,r,-100,100,()=>st[tone][k],(v,done)=>{st[tone][k]=v;emit(done);},track));
  const keep=Ed.ui.field({key:'preserveLuminosity',label:'Preserve Luminosity',type:'checkbox',title:'Keep each pixel as light as it was'},st.preserveLuminosity,v=>{st.preserveLuminosity=!!v;emit(true);});
  root.append(toneF.node,...rows.map(r=>r.node),keep.node);
  function sync(){toneF.sync(tone);rows.forEach(r=>r.sync());keep.sync(st.preserveLuminosity);}sync();return{node:root,sync};}
function blackWhiteUI(st,emit){return form(st,emit,[
  {key:'reds',label:'Reds',type:'range',min:-200,max:300,step:1,unit:'%',track:'linear-gradient(90deg,#000,#f22)',title:'How light reds become'},
  {key:'yellows',label:'Yellows',type:'range',min:-200,max:300,step:1,unit:'%',track:'linear-gradient(90deg,#000,#ee2)',title:'How light yellows become'},
  {key:'greens',label:'Greens',type:'range',min:-200,max:300,step:1,unit:'%',track:'linear-gradient(90deg,#000,#2d2)',title:'How light greens become'},
  {key:'cyans',label:'Cyans',type:'range',min:-200,max:300,step:1,unit:'%',track:'linear-gradient(90deg,#000,#2dd)',title:'How light cyans become'},
  {key:'blues',label:'Blues',type:'range',min:-200,max:300,step:1,unit:'%',track:'linear-gradient(90deg,#000,#36f)',title:'How light blues become'},
  {key:'magentas',label:'Magentas',type:'range',min:-200,max:300,step:1,unit:'%',track:'linear-gradient(90deg,#000,#e2e)',title:'How light magentas become'},
  {key:'tint',label:'Tint',type:'checkbox',title:'Color the gray picture with one hue'},
  {key:'tintHue',label:'Hue',type:'range',min:0,max:360,step:1,unit:'°',track:'linear-gradient(90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)',show:s=>s.tint},
  {key:'tintSaturation',label:'Saturation',type:'range',min:0,max:100,step:1,unit:'%',show:s=>s.tint}],'adj-bw');}
function photoFilterUI(st,emit){return form(st,emit,[
  {key:'filter',label:'Filter',type:'select',options:[...FILTERS.map(f=>({value:f[0],label:f[1]})),{value:'custom',label:'Custom color'}],title:'A camera lens filter, or your own color',after:s=>{const f=FILTERS.find(x=>x[0]===s.filter);if(f)s.color=f[2];}},
  {key:'color',label:'Color',type:'color',title:'The filter color (picking one makes it a custom filter)',after:s=>{const f=FILTERS.find(x=>x[2]===s.color.toLowerCase());s.filter=f?f[0]:'custom';}},
  {key:'density',label:'Density',type:'range',min:0,max:100,step:1,unit:'%',title:'How strong the filter is'},
  {key:'preserveLuminosity',label:'Preserve Luminosity',type:'checkbox',title:'Keep the picture as light as it was'}]);}
function mixerUI(st,emit){let out='red';const root=el('div','adj-ui adj-mixer');const row=()=>st.monochrome?st.gray:st[out];
  const outF=select('output','Output Channel',[{value:'red',label:'Red'},{value:'green',label:'Green'},{value:'blue',label:'Blue'}],out,v=>{out=v;sync();},'Which channel of the result the sliders build');
  const sl=[['r','Red','#f33'],['g','Green','#3d3'],['b','Blue','#47f']].map(([k,label,c])=>{const f=Ed.ui.field({key:k,label,type:'range',min:-200,max:200,step:1,unit:'%',title:`How much of the source ${label.toLowerCase()} channel goes into the output`},0,(v,done)=>{const x=Number(v);if(!isFinite(x))return;row()[k]=clamp(Math.round(x),-200,200);emit(!!done);total();});
    f.node.classList.add('adj-tracked');f.node.querySelector('input[type=range]').style.setProperty('--track',`linear-gradient(90deg,#000,${c})`);return[k,f];});
  const tot=el('div','adj-total');const cst=Ed.ui.field({key:'c',label:'Constant',type:'range',min:-200,max:200,step:1,unit:'%',title:'Adds black (-) or white (+) to the output channel'},0,(v,done)=>{const x=Number(v);if(!isFinite(x))return;row().c=clamp(Math.round(x),-200,200);emit(!!done);});
  cst.node.classList.add('adj-tracked');cst.node.querySelector('input[type=range]').style.setProperty('--track','linear-gradient(90deg,#000,#fff)');
  const mono=Ed.ui.field({key:'monochrome',label:'Monochrome',type:'checkbox',title:'Make a gray picture from the mix (one output for all three channels)'},st.monochrome,v=>{st.monochrome=!!v;emit(true);sync();});
  root.append(outF.node,el('div','adj-label','Source Channels'),...sl.map(x=>x[1].node),tot,cst.node,mono.node);
  function total(){const r=row(),t=r.r+r.g+r.b;tot.textContent=`Total: ${t>0?'+':''}${t}%`;tot.classList.toggle('warn',t>100);tot.title=t>100?'Over 100%: bright areas may lose detail':'';}
  function sync(){const on=st.monochrome;outF.node.hidden=on;for(const [k,f] of sl)f.sync(row()[k]);cst.sync(row().c);mono.sync(on);total();}sync();return{node:root,sync};}
function selectiveUI(st,emit){let col='reds';const root=el('div','adj-ui adj-selective');
  const colF=select('colors','Colors',SC_KEYS.map(k=>({value:k,label:k[0].toUpperCase()+k.slice(1)})),col,v=>{col=v;sync();},'Which colors the sliders change');
  const sl=[['Cyan','#0dd'],['Magenta','#e2e'],['Yellow','#dd2'],['Black','#000']].map(([label,c],k)=>{const f=Ed.ui.field({key:label.toLowerCase(),label,type:'range',min:-100,max:100,step:1,unit:'%',title:`Add (+) or remove (-) ${label.toLowerCase()} ink in these colors`},0,(v,done)=>{const x=Number(v);if(!isFinite(x))return;st[col][k]=clamp(Math.round(x),-100,100);emit(!!done);});
    f.node.classList.add('adj-tracked');f.node.querySelector('input[type=range]').style.setProperty('--track',`linear-gradient(90deg,#fff,${c})`);return f;});
  const meth=Ed.ui.field({key:'method',label:'Method',type:'segmented',options:[{value:'relative',label:'Relative',title:'Changes the ink a color already has by a share of it'},{value:'absolute',label:'Absolute',title:'Adds or removes a fixed amount of ink'}]},st.method,v=>{st.method=v;emit(true);});
  root.append(colF.node,...sl.map(f=>f.node),meth.node);
  function sync(){colF.sync(col);sl.forEach((f,k)=>f.sync(st[col][k]));meth.sync(st.method);}sync();return{node:root,sync};}
function thresholdUI(st,emit,ctx){const root=el('div','adj-ui adj-threshold'),hc=histCanvas(),tr=el('div','adj-track'),hb=el('div','adj-histbox');hb.append(hc,tr);
  const f=form(st,emit,[{key:'level',label:'Threshold Level',type:'range',min:1,max:255,step:1,title:'Pixels at least this light become white, darker ones black',after:()=>{if(k)place(k,st.level);}}]);
  let k=null;k=knob(tr,'gray','Threshold level: drag it',(v,done)=>{if(v!=null){st.level=clamp(Math.round(v),1,255);emit(false);}else emit(true);sync();},s=>{st.level=clamp(st.level+s,1,255);emit(true);sync();});
  root.append(hb,f.node);function sync(){const h=ctx.hist&&ctx.hist();drawHist(hc,h&&h.luma,HIST_COLOR.luma,'#0d131b');place(k,st.level);f.sync();}
  sync();return{node:root,sync};}
const GRADIENT_PRESETS=[['fgbg','Foreground to Background'],['bw','Black, White',['#000000','#ffffff']],['violet-orange','Violet, Orange',['#290a59','#ff7c00']],
  ['blue-red-yellow','Blue, Red, Yellow',['#0a00b2','#ff0000','#fffc00']],['blue-yellow-blue','Blue, Yellow, Blue',['#0a00b2','#fffc00','#0a00b2']],
  ['spectrum','Spectrum',['#ff0000','#ffff00','#00ff00','#00ffff','#0000ff','#ff00ff','#ff0000']]];
function stopsOf(colors){return{stops:colors.map((c,i)=>({at:Math.round(i/(colors.length-1)*1000)/10,color:c,mid:50}))};}
const presetStops=p=>p[0]==='fgbg'?twoStops(Ed.fg,Ed.bg).stops:stopsOf(p[2]).stops;
function gradientUI(st,emit){const root=el('div','adj-ui adj-gradient'),bar=el('canvas','adj-gradbar');bar.width=256;bar.height=1;bar.title='The gradient: dark pixels take the left color, light pixels the right one';
  const pre=select('preset','Gradient',GRADIENT_PRESETS.map(p=>({value:p[0],label:p[1]})).concat([{value:'',label:'Custom'}]),'',v=>{const p=GRADIENT_PRESETS.find(x=>x[0]===v);if(p){st.gradient={stops:presetStops(p)};emit(true);}sync();},'Ready-made gradients');
  const ends=el('div','adj-row adj-ends');const end=(k,label,title)=>Ed.ui.field({key:k,label,type:'color',title},'#000000',(v,done)=>{const g=st.gradient.stops.slice().sort((a,b)=>a.at-b.at);(k==='start'?g[0]:g[g.length-1]).color=v;st.gradient.stops=g;emit(!!done);sync();});
  const sF=end('start','Shadows','The color the darkest pixels get'),eF=end('end','Highlights','The color the lightest pixels get');ends.append(sF.node,eF.node);
  const opts=form(st,emit,[{key:'dither',label:'Dither',type:'checkbox',title:'Adds a little noise to hide banding'},{key:'reverse',label:'Reverse',type:'checkbox',title:'Flip the gradient',after:()=>drawBar()}],'adj-checks');
  root.append(bar,pre.node,ends,opts.node);
  function drawBar(){const t=gradientTable(st.gradient,st.reverse),im=new ImageData(256,1);for(let v=0;v<256;v++){im.data[v*4]=t[v*3];im.data[v*4+1]=t[v*3+1];im.data[v*4+2]=t[v*3+2];im.data[v*4+3]=255;}bar.getContext('2d').putImageData(im,0,0);}
  function sync(){const g=st.gradient.stops.slice().sort((a,b)=>a.at-b.at),hex=c=>/^#[0-9a-f]{6}$/i.test(c||'')?c:'#000000';if(g.length){sF.sync(hex(g[0].color));eF.sync(hex(g[g.length-1].color));}
    const sig=S=>JSON.stringify(S.map(s=>[+s.at,String(s.color).toLowerCase()]));const key=sig(g);const m=GRADIENT_PRESETS.find(p=>sig(presetStops(p))===key);pre.sync(m?m[0]:'');{const cu=pre.node.querySelector('option[value=""]');if(cu)cu.hidden=!!m;}opts.sync();drawBar();}
  sync();return{node:root,sync};}
// One adjustment's whole set of controls. change(params, done) gets a fresh copy after every user change.
function buildUI(type,params,change,ctx={}){const def=DEF[type];if(!def)throw Error('There is no adjustment '+type);const st=normalize(type,params);
  const emit=done=>change(clone(st),!!done);const ui=(def.ui||(()=>({node:el('p','hint adj-none','No settings.'),sync(){}})))(st,emit,ctx);ui.node.dataset.adjust=type;
  return{node:ui.node,set(p){const n=normalize(type,p);for(const k of Object.keys(st))delete st[k];Object.assign(st,n);ui.sync();},get params(){return clone(st);}};}
Ed.registerField('adjustment',(o,value,set)=>buildUI(o.adjType,value,(p,done)=>{set(p);if(o.onLive)o.onLive(p,done);},o.ctx||{}).node);

// ----- commands ------------------------------------------------------------------------------------------------------
const opOf=(type,p)=>{const j=JSON.stringify(p);return j.length<700?{tool:'adjust',type,params:p}:{tool:'adjust',type};};
const canCommand=type=>{const d=Ed.doc,L=d&&Ed.layers.active();if(!L)return false;return d.target==='mask'?!!DEF[type].gray:L.kind==='pixel';};
function refuse(type){if(!Ed.doc){Ed.message('Open an image first.');return true;}const why=Ed.editable({view:false});if(why){Ed.message(why,true);return true;}
  if(Ed.doc.target==='mask'&&!DEF[type].gray){Ed.message(`${DEF[type].label} changes colors and a layer mask is only gray. Click the layer’s picture thumbnail to change its pixels.`,true);return true;}return false;}
// Image > Adjustments > X. With params it applies at once (Ed.menu.run(path, params)); without, it opens the dialog.
function command(type,params){if(refuse(type))return Promise.resolve(false);if(params!==undefined||DEF[type].dialog===false)return applyNow(type,params||{});return openDialog(type);}
function applyNow(type,params,label){const p=normalize(type,params);const R=Ed.selection?Ed.select.bounds():null;
  return Ed.applyToTarget(label||DEF[type].label,(g,info)=>{const D=U.read(info.canvas,info.rect),gray=info.target==='mask';if(gray)toGray(D);
    const S=info.selection&&DEF[type].whole?U.read(info.selection,info.rect).data:null;process(type,p,D,info.rect,S);if(gray)fromGray(D);U.write(info.canvas,D,info.rect);},{rect:R||undefined,op:opOf(type,p)});}
function autoCommand(kind){const type=kind==='color'?'auto-color':'auto';if(!Ed.doc){Ed.message('Open an image first.');return Promise.resolve(false);}const why=Ed.editable({view:false});if(why){Ed.message(why,true);return Promise.resolve(false);}
  if(kind==='color'&&Ed.doc.target==='mask'){Ed.message('Auto Color needs colors; a layer mask is only gray.',true);return Promise.resolve(false);}
  const label={tone:'Auto Tone',contrast:'Auto Contrast',color:'Auto Color'}[kind],R=Ed.selection?Ed.select.bounds():null;
  return Ed.applyToTarget(label,(g,info)=>{const D=U.read(info.canvas,info.rect),gray=info.target==='mask';if(gray)toGray(D);const S=info.selection?U.read(info.selection,info.rect).data:null;
    const p=autoLevels(kind,D.data,S),k=kernel('levels',normalize('levels',p));if(k)k(D.data);if(gray)fromGray(D);U.write(info.canvas,D,info.rect);},{rect:R||undefined,op:{tool:type,kind}});}
// Equalize with a selection asks Photoshop's question: only the selected area, or the whole layer from the selection's histogram.
async function equalizeCommand(params){if(refuse('equalize'))return false;let whole=params&&params.entire;
  if(Ed.selection&&!params){const r=await Ed.dialog({title:'Equalize',fields:[{key:'scope',label:'Equalize',type:'segmented',options:[{value:'selected',label:'Selected area only'},{value:'entire',label:'Entire image from selected area'}],default:'selected'}],okLabel:'OK'});
    if(!r)return false;whole=r.scope==='entire';}
  if(!whole||!Ed.selection)return applyNow('equalize',{});
  const L=Ed.layers.active(),target=Ed.doc.target,ref=target==='mask'?L.mask:L.canvas,R={x:0,y:0,w:Ed.doc.width,h:Ed.doc.height};
  const D=U.read(ref,R),gray=target==='mask';if(gray)toGray(D);const t=equalizeTable(histogram(D.data,U.read(Ed.selection,R).data));lutKernel(same(t))(D.data);if(gray)fromGray(D);
  Ed.history.record('Equalize',tx=>{tx.touch(L,target,R);U.write(ref,D,R);Ed.update(R);},{op:{tool:'adjust',type:'equalize',entire:true},rect:R});return true;}
// The dialog: the target's pixels (or mask as gray) are read once; each change re-runs the kernel and shows the result
// through the selection with Ed.preview.layer. OK applies it with Ed.applyToTarget.
function openDialog(type){const def=DEF[type],doc=Ed.doc,L=Ed.layers.active(),target=doc.target,ref=target==='mask'?L.mask:L.canvas,gray=target==='mask';
  const sel=Ed.selection,R=sel?Ed.select.bounds():{x:0,y:0,w:doc.width,h:doc.height};
  const src=U.read(ref,R);if(gray)toGray(src);const selD=sel?U.read(sel,R).data:null;
  const pv=U.canvas(doc.width,doc.height);pv.getContext('2d').drawImage(ref,0,0);
  const work=new ImageData(R.w,R.h);let hist=null,closed=false,frame=0,want=null,shownKey='',dlg=null;
  const ctx={mode:'dialog',gray,source:src,rect:R,hist:()=>hist||(hist=histogram(src.data,selD)),auto:kind=>autoLevels(kind,src.data,selD),
    sample:(cb,button)=>Sampler.start({dialog:dlg&&dlg.element,button,pick(x,y){const c=averageAt(src,R,x,y,sampleSize());if(c)cb(c);else Ed.message(sel?'Click a pixel inside the selection that is not transparent.':'Click a pixel of the picture that is not transparent.');}})};
  function show(p){if(closed||!dlg)return;const box=dlg.element.querySelector('input[name=preview]');if(box&&!box.checked)return;
    const key=JSON.stringify(p),a=Ed.preview.active;if(key===shownKey&&a&&a.canvas===pv)return;shownKey=key;
    work.data.set(src.data);process(type,normalize(type,p),work,R,selD);if(selD)mixSelection(work.data,src.data,selD);if(gray)fromGray(work);U.write(pv,work,R);
    if(a&&a.canvas===pv)Ed.preview.update(R);else Ed.preview.layer({layer:L,canvas:pv,target});}
  const live=p=>{want=p;if(!frame)frame=requestAnimationFrame(()=>{frame=0;if(want&&!closed)show(want);});};
  const start=defaultsOf(type);
  dlg=Ed.dialog({title:def.title||def.label,fields:[{key:'params',type:'adjustment',adjType:type,ctx,default:start,onLive:live}],values:{params:start},
    preview:p=>show(p.params),
    ok:async p=>{closed=true;Sampler.stop();await applyNow(type,p.params);},
    cancel:()=>{closed=true;Sampler.stop();}});
  dlg.element.classList.add('adj-dialog','adj-dialog-'+type);show(start);
  return dlg.then(r=>!!r);}

// ----- adjustment layers ---------------------------------------------------------------------------------------------
function nextName(label){let n=1;const esc=label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),re=new RegExp('^'+esc+' (\\d+)$');for(const L of Ed.layers.list()){const m=L.name.match(re);if(m)n=Math.max(n,+m[1]+1);}return label+' '+n;}
// Layer > New Adjustment Layer > X: above the active layer, its mask from the selection (white without one), shown in Properties.
function newLayer(type,params){const d=DEF[type];if(!Ed.doc){Ed.message('Open an image first.');return null;}if(!d||!d.layer)return null;
  const p=params===undefined?defaultsOf(type):normalize(type,params);let mask=null;
  if(Ed.selection){mask=U.canvas(Ed.doc.width,Ed.doc.height);mask.getContext('2d').drawImage(Ed.selection,0,0);}
  const L=Ed.layers.add({kind:'adjustment',name:nextName(d.label),adjustment:{type,params:p},mask,history:'New '+d.label+' Layer',op:{tool:'adjustment layer',type}});
  if(L&&params===undefined)Ed.panels.show('properties');return L;}

// ----- the Properties panel ------------------------------------------------------------------------------------------
const ICONS={'brightness-contrast':['M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z','M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8'],
  levels:['M3.5 20.5h17','M5 20V15','M8 20V9','M11 20V12','M14 20V6','M17 20V11','M20 20V14'],curves:['M4 4v16h16','M4 20C10 19 12 6 20 4'],
  exposure:['M4 4h16v16H4z','M4 20L20 4','M6.5 8.5h4M8.5 6.5v4','M13.5 15.5h4'],vibrance:['M4 5h16l-8 15z','M8.5 9h7'],
  'hue-saturation':['M3 7h18','M3 12h18','M3 17h18','M8 5v4','M15 10v4','M11 15v4'],'color-balance':['M12 4v15','M7 20h10','M4 7h16','M4 7l-2.2 6h4.4z','M20 7l-2.2 6h4.4z'],
  'black-white':['M4 4h16v16H4z','M12 4v16','M14.5 8h3M14.5 12h3M14.5 16h3'],'photo-filter':['M3 8h4l2-3h6l2 3h4v11H3z','M12 10a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z'],
  'channel-mixer':['M9 5a4 4 0 1 0 0 8a4 4 0 1 0 0-8z','M15 5a4 4 0 1 0 0 8a4 4 0 1 0 0-8z','M12 10a4 4 0 1 0 0 8a4 4 0 1 0 0-8z'],
  invert:['M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z','M12 4v16','M12 7h3M12 12h5M12 17h3'],posterize:['M4 20v-4h4v-4h4V8h4V4h4'],threshold:['M4 4h16v16H4z','M4 15c3-4 5 1 8-3s5 1 8-3'],
  'gradient-map':['M4 7h16v10H4z','M8 7v10','M12 7v10','M16 7v10'],'selective-color':['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z','M12 7a5 5 0 1 0 0 10a5 5 0 1 0 0-10z','M12 11a1 1 0 1 0 0 2a1 1 0 1 0 0-2z']};
const icon=(type,size=16)=>Ed.icon(ICONS[type]||ICONS.levels,{size});
const SLIDERS=['M4 6h9M17 6h3','M4 12h3M11 12h9','M4 18h11M19 18h1','M15 4v4','M9 10v4','M17 16v4'];
const I_RESET=['M4 12a8 8 0 1 0 2.3-5.7','M4 4v4h4'],I_EYE=['M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12s-3.5 6.5-9.5 6.5S2.5 12 2.5 12z','M12 9.2a2.8 2.8 0 1 0 0 5.6a2.8 2.8 0 1 0 0-5.6z'],
  I_CLIP=['M6 4v9a3 3 0 0 0 3 3h9','M15 13l3 3-3 3'],I_TRASH=['M5 7h14','M10 7V5h4v2','M7 7l1 12h8l1-12'],I_MASK=['M4 5h16v14H4z','M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z'];
const LAYER_TYPES=['brightness-contrast','levels','curves','exposure','vibrance','hue-saturation','color-balance','black-white','photo-filter','channel-mixer','invert','posterize','threshold','gradient-map','selective-color'];
const Props=(()=>{let root=null,head,body,maskBox,foot,key='',ui=null,shown='',live=null,uiType=null,uiLayer=null,info=null,boundsKey='',boundsTimer=0,below={key:'',data:null};
  function build(c){root=c;c.classList.add('adj-props');c.replaceChildren();head=el('div','adj-props-head');body=el('div','adj-props-body');maskBox=el('div','adj-props-mask');foot=el('div','adj-props-foot');c.append(head,body,maskBox,foot);key='';}
  function title(ic,text,sub){head.replaceChildren();const i=el('span','adj-props-icon');if(ic)i.append(ic);const t=el('div','adj-props-title');t.append(el('b','',text));if(sub)t.append(el('span','',sub));head.append(i,t);}
  function iconBtn(action,ic,tip,fn){const b=btn('adj-iconbtn','',tip);b.dataset.action=action;b.append(Ed.icon(ic,{size:15}));b.onclick=fn;return b;}
  // What lies under an adjustment layer (its histogram and eyedroppers read this), cached until something below changes.
  function belowData(L){const d=Ed.doc,i=Ed.layers.index(L);if(i<0)return null;const list=d.layers.slice(0,i);
    const k=d.width+'x'+d.height+'|'+list.map(x=>[x.id,x.version,x.visible,x.opacity,x.fill,x.blend,x.clip,x.maskEnabled,x.mask?1:0,JSON.stringify(x.adjustment),JSON.stringify(x.effects)].join(',')).join(';');
    if(below.key!==k){const c=list.length?Ed.render.composite({layers:list}):U.canvas(d.width,d.height);below={key:k,data:U.read(c),hist:null};}return below;}
  function ctxFor(L){return{mode:'panel',gray:false,hist:()=>{const b=belowData(L);if(!b)return null;return b.hist||(b.hist=histogram(b.data.data));},
    auto:kind=>{const b=belowData(L);return b?autoLevels(kind,b.data.data):null;},
    sample:(cb,button)=>Sampler.start({button,pick(x,y){const b=belowData(L);const c=b&&averageAt(b.data,{x:0,y:0,w:b.data.width,h:b.data.height},x,y,sampleSize());if(c)cb(c);else Ed.message('Click a pixel of the picture that is not transparent.');}})};}
  function commitLive(){const t=live;live=null;if(t&&Ed.history.open===t)t.commit();}
  function change(L,type,params,done){if(!Ed.layers.get(L.id))return;
    if(!live||Ed.history.open!==live)live=Ed.history.begin('Modify '+DEF[type].label+' Layer',[],{op:{tool:'adjustment layer',type,action:'modify'}});
    Ed.layers.set(L,{adjustment:{type,params}},{history:false});shown=JSON.stringify(params);if(done)commitLive();}
  function rebuild(L){commitLive();Sampler.stop();ui=null;uiType=null;uiLayer=null;info=null;shown='';body.replaceChildren();foot.replaceChildren();maskBox.replaceChildren();root.classList.remove('adj-props-empty');
    const d=Ed.doc;
    if(!d||!L){title(Ed.icon(SLIDERS,{size:16}),'Properties');body.append(el('p','hint','Open an image to see its properties.'));root.classList.add('adj-props-empty');return;}
    if(L.kind==='adjustment'){const type=L.adjustment&&L.adjustment.type,def=DEF[type];
      if(!def||!def.layer){title(Ed.icon(SLIDERS,{size:16}),Ed.adjustments.get(type)?.label||'Adjustment',L.name);body.append(el('p','hint','This adjustment comes from another module; it has no settings here.'));return;}
      title(icon(type,16),def.label,L.name);uiType=type;uiLayer=L.id;
      ui=buildUI(type,L.adjustment.params,(p,done)=>change(L,type,p,done),ctxFor(L));shown=JSON.stringify(normalize(type,L.adjustment.params));body.append(ui.node);
      foot.append(iconBtn('clip',I_CLIP,'This adjustment clips to the layer below (Alt+Ctrl+G): it changes only that layer',()=>{const X=Ed.layers.get(L.id);if(X)Ed.layers.set(X,{clip:!X.clip});}),
        iconBtn('reset',I_RESET,'Reset to adjustment defaults',()=>{const X=Ed.layers.get(L.id);if(!X)return;commitLive();Ed.layers.set(X,{adjustment:{type,params:defaultsOf(type)}},{history:'Modify '+def.label+' Layer'});}),
        iconBtn('visible',I_EYE,'Show or hide this adjustment',()=>{const X=Ed.layers.get(L.id);if(X)Ed.layers.set(X,{visible:!X.visible});}),
        iconBtn('delete',I_TRASH,'Delete this adjustment layer',()=>{const X=Ed.layers.get(L.id);if(X)Ed.layers.remove(X);}));}
    else{title(Ed.icon(['M4 4h16v16H4z','M4 15l5-5 4 4 3-3 4 4'],{size:16}),'Pixel Layer',L.name);info=el('div','adj-info');body.append(info);
      const grid=el('div','adj-grid');grid.setAttribute('role','group');grid.setAttribute('aria-label','Add an adjustment layer');
      for(const t of LAYER_TYPES){const b=btn('adj-add','',`${DEF[t].label}: add an adjustment layer above this one`);b.dataset.add=t;b.append(icon(t,17));b.onclick=()=>newLayer(t);grid.append(b);}
      body.append(el('div','adj-label','Add an adjustment'),grid);}
    maskRow(L);update(L);}
  function maskRow(L){maskBox.replaceChildren();if(!L.mask)return;const st=el('span','adj-mask-state');
    maskBox.append(Ed.icon(I_MASK,{size:14}),el('span','adj-label','Layer Mask'),st,
      Object.assign(btn('adj-btn','Invert','Invert the mask: hidden parts show and shown parts hide (Ctrl+I with the mask selected)'),{onclick:()=>{const X=Ed.layers.get(L.id);if(!X)return;commitLive();Ed.layers.setActive(X,'mask');command('invert',{});}}),
      Object.assign(btn('adj-btn adj-mask-toggle','',''),{onclick:()=>{const X=Ed.layers.get(L.id);if(X)Ed.layers.set(X,{maskEnabled:!X.maskEnabled});}}));update(L);}
  function bounds(L){const k=L.id+'|'+L.version+'|'+Ed.doc.width;if(k===boundsKey)return;boundsKey=k;clearTimeout(boundsTimer);
    boundsTimer=setTimeout(()=>{const X=Ed.layers.get(L.id);if(!X||!info||!info.isConnected)return;const D=U.read(X.canvas).data,W=Ed.doc.width,H=Ed.doc.height;let x0=W,y0=H,x1=-1,y1=-1;
      for(let y=0;y<H;y++){const row=y*W*4;for(let x=0;x<W;x++)if(D[row+x*4+3]){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}}
      info.dataset.bounds=x1<0?'':`${x0},${y0},${x1-x0+1},${y1-y0+1}`;update(X);},120);}
  function update(L){const d=Ed.doc;if(!d||!L)return;
    if(L.kind==='adjustment'&&ui&&uiLayer===L.id){const p=JSON.stringify(normalize(uiType,L.adjustment&&L.adjustment.params));if(!live&&p!==shown){shown=p;ui.set(JSON.parse(p));}
      head.querySelector('.adj-props-title span')?.replaceChildren(L.name);
      const c=foot.querySelector('[data-action=clip]');if(c)c.setAttribute('aria-pressed',String(!!L.clip));const v=foot.querySelector('[data-action=visible]');if(v)v.setAttribute('aria-pressed',String(!L.visible));}
    if(info){const b=info.dataset.bounds;info.replaceChildren();const line=(k,v)=>{const p=el('div');p.append(el('span','',k),el('b','',v));info.append(p);};
      line('Document',`${d.width} × ${d.height} px`);line('Pixels',b===undefined?'…':b===''?'none (empty layer)':(()=>{const [x,y,w,h]=b.split(',');return `${w} × ${h} px at ${x}, ${y}`;})());
      line('Opacity',`${Math.round(L.opacity*100)}% · Fill ${Math.round(L.fill*100)}%`);bounds(L);head.querySelector('.adj-props-title span')?.replaceChildren(L.name);}
    const st=maskBox.querySelector('.adj-mask-state'),tg=maskBox.querySelector('.adj-mask-toggle');if(st&&L.mask){st.textContent=L.maskEnabled?'':'(off)';tg.textContent=L.maskEnabled?'Disable':'Enable';tg.title=L.maskEnabled?'Turn the mask off for now (Layer > Layer Mask > Disable)':'Turn the mask back on';}}
  function render(c){if(root!==c)build(c);const d=Ed.doc,L=d&&Ed.layers.active();
    const k=!L?'none':L.kind==='adjustment'?`adj|${L.id}|${L.adjustment&&L.adjustment.type}|${L.mask?1:0}`:`px|${L.id}|${L.mask?1:0}`;
    if(k!==key){key=k;rebuild(L);}else update(L);}
  Ed.on('open',()=>{key='';below={key:'',data:null};commitLive();});
  return{title:'Properties',order:15,render};})();
Ed.registerPanel('properties',Props);
Ed.registerLayerBadge(L=>{if(L.kind!=='adjustment')return null;const d=DEF[L.adjustment&&L.adjustment.type];if(!d||!d.layer)return null;
  const b=btn('adj-badge','',`${d.label} settings: open them in Properties`);b.dataset.adjBadge=L.id;b.append(Ed.icon(SLIDERS,{size:12}));
  b.onclick=e=>{e.stopPropagation();Ed.layers.setActive(L);Ed.panels.show('properties');};return b;});

// ----- menus ---------------------------------------------------------------------------------------------------------
const taken=new Set(Ed.menu.list().map(m=>m.shortcut).filter(Boolean));
const keyOf=spec=>{if(!spec)return undefined;if(taken.has(spec)){console.warn('[adjust] shortcut already used:',spec);return undefined;}taken.add(spec);return spec;};
const pathOf=(menu,label)=>menu+'/'+label.replace(/\//g,'-');
const ADJ_MENU=[['brightness-contrast',10],['levels',11],['curves',12],['exposure',13],['vibrance',20,1],['hue-saturation',21],['color-balance',22],['black-white',23],['photo-filter',24],['channel-mixer',25],
  ['invert',30,1],['posterize',31],['threshold',32],['gradient-map',33],['selective-color',34],['desaturate',50,1],['equalize',53]];
for(const [type,order,sep] of ADJ_MENU){const d=DEF[type],text=d.label+(d.dialog===false?'':'...'),path=pathOf('Image/Adjustments',text);if(Ed.menu.get(path))continue;
  Ed.registerMenu(path,{label:text,order,separatorBefore:!!sep,shortcut:keyOf(d.key),enabled:()=>canCommand(type),action:p=>type==='equalize'?equalizeCommand(p):command(type,p)});}
const AUTO=[['tone','Auto Tone','Shift+Ctrl+L',30],['contrast','Auto Contrast','Alt+Shift+Ctrl+L',31],['color','Auto Color','Shift+Ctrl+B',32]];
for(const [kind,label,key,order] of AUTO){const path='Image/'+label;if(Ed.menu.get(path))continue;
  Ed.registerMenu(path,{order,separatorBefore:order===30,shortcut:keyOf(key),enabled:()=>{const d=Ed.doc,L=d&&Ed.layers.active();return !!L&&(d.target==='mask'?kind!=='color':L.kind==='pixel');},action:()=>autoCommand(kind)});}
LAYER_TYPES.forEach((type,i)=>{const d=DEF[type],text=d.label+'...',path=pathOf('Layer/New Adjustment Layer',text);if(Ed.menu.get(path))return;
  Ed.registerMenu(path,{label:text,order:36+i*.01,separatorBefore:type==='vibrance'||type==='invert',enabled:()=>!!Ed.doc,action:p=>{newLayer(type,p);}});});
if(!Ed.menu.get('Layer/Layer Content Options...'))Ed.registerMenu('Layer/Layer Content Options...',{order:37,enabled:()=>{const L=Ed.layers.active();return !!L&&L.kind==='adjustment';},action:()=>Ed.panels.show('properties')});

// ----- for other modules and tests -----------------------------------------------------------------------------------
Ed.adjust={types:()=>Object.keys(DEF),layerTypes:()=>LAYER_TYPES.slice(),label:t=>DEF[t]&&DEF[t].label,defaults:t=>defaultsOf(t),normalize:(t,p)=>normalize(t,p),
  apply(type,params,img,region){if(!DEF[type])throw Error('There is no adjustment '+type);return process(type,normalize(type,params),img,region,null);},
  command:(type,params)=>type==='equalize'?equalizeCommand(params):command(type,params),newLayer,auto:(kind,data,sel)=>autoLevels(kind,data,sel),
  autoCommand,histogram,spline:points=>spline(points),ui:buildUI,sampler:Sampler};
})();
