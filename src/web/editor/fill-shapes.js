'use strict';
/* Shape tools for the Asset Lab editor (U), loaded by editor/fill.js: Rectangle (corner radius), Ellipse, Polygon (sides)
   and Line (weight). Drag from corner to corner; Shift keeps squares, circles and 45 degree lines, Alt draws from the
   centre, Escape cancels. Tool mode Shape puts each shape on a new layer above the active one (a vector shape ignores the
   selection, as in Photoshop); Pixels draws into the active layer or mask through the selection, keeping locked
   transparency. Fill and Stroke are colors or none; the stroke lies inside the outline. Box corners snap to whole pixels
   (Photoshop's "snap to pixel grid") so straight edges stay crisp; Anti-alias off makes every pixel fully in or out.
   Pixels come from exact area coverage computed here (see "rasterizing"), not from the browser's path filling, so a shape
   gives the same pixels on every graphics card and on the software renderer. */
(()=>{
const Ed=window.Ed,K=Ed&&Ed.fillKit;if(!K)return;const U=Ed.util,{clamp}=U,{el,btn,isHex}=K;

const ICON={rectangle:['M4 6h16v12H4z'],ellipse:['M12 5c4.4 0 8 3.1 8 7s-3.6 7-8 7-8-3.1-8-7 3.6-7 8-7z'],
  polygon:['M12 3.5l8.1 5.9-3.1 9.6H7l-3.1-9.6z'],line:['M5 19L19 5']};
const LABEL={rectangle:'Rectangle',ellipse:'Ellipse',polygon:'Polygon',line:'Line'};
const SHARED=['mode','fill','stroke','width','antialias'];

// ----- the Fill / Stroke color control: a chip that opens a small picker (a color, the foreground color, or none) --------
function paintField(f,value,set){let cur=isHex(value)?value.toLowerCase():'';let last=cur||(f.key==='stroke'?'#000000':'#ffffff');
  const chip=btn('fill-paint-chip'+(f.key==='stroke'?' stroke-chip':''),'');chip.setAttribute('aria-haspopup','dialog');
  const draw=()=>{chip.classList.toggle('none',!cur);chip.style.backgroundColor=cur||'transparent';const what=f.key==='stroke'?'Stroke':'Fill';
    chip.title=`${what}: ${cur||'none'} · click to change`;chip.setAttribute('aria-label',`${what} color`);};
  const pick=v=>{cur=isHex(v)?v.toLowerCase():'';if(cur)last=cur;draw();set(cur);};
  chip.onclick=()=>{
    const box=el('div','fill-paint-pop'),row=el('div','fill-paint-types'),none=btn(cur?'':'on','No Color','No '+(f.key==='stroke'?'stroke':'fill')),
      fg=btn('','Foreground','Use the foreground color'),c=el('input');c.type='color';c.name=f.key+'-color';c.value=cur||last;c.setAttribute('aria-label','Color');
    none.dataset.paint='none';fg.dataset.paint='fg';none.onclick=()=>{pick('');K.closePopover();chip.focus();};fg.onclick=()=>{pick(Ed.fg);c.value=Ed.fg;K.closePopover();chip.focus();};
    c.oninput=c.onchange=()=>{pick(c.value);none.classList.remove('on');};row.append(none,fg,c);box.append(row);K.popover(chip,box,f.key==='stroke'?'Stroke color':'Fill color');};
  K.track(f,chip,v=>{cur=isHex(v)?v.toLowerCase():'';if(cur)last=cur;draw();});draw();return chip;}
Ed.registerField('fill-paint',paintField);

// ----- geometry -----------------------------------------------------------------------------------------------------------
// The box (or the line) of a drag, in image pixels, snapped and constrained.
function geometry(kind,a,b,o,shift,alt){
  if(kind==='line'){const w=Math.max(1,o.weight|0),snap=v=>w%2?Math.floor(v)+.5:Math.round(v);let x1=b.x,y1=b.y;
    if(shift){const dx=b.x-a.x,dy=b.y-a.y,ang=Math.round(Math.atan2(dy,dx)/(Math.PI/4))*(Math.PI/4),d=Math.hypot(dx,dy);x1=a.x+Math.cos(ang)*d;y1=a.y+Math.sin(ang)*d;}
    return{kind,x0:snap(a.x),y0:snap(a.y),x1:snap(x1),y1:snap(y1),weight:w};}
  let dx=b.x-a.x,dy=b.y-a.y;if(shift){const s=Math.max(Math.abs(dx),Math.abs(dy));dx=(dx<0?-1:1)*s;dy=(dy<0?-1:1)*s;}
  let x0,y0,x1,y1;if(alt){x0=a.x-dx;x1=a.x+dx;y0=a.y-dy;y1=a.y+dy;}else{x0=a.x;y0=a.y;x1=a.x+dx;y1=a.y+dy;}
  const r={kind,x0:Math.round(Math.min(x0,x1)),y0:Math.round(Math.min(y0,y1)),x1:Math.round(Math.max(x0,x1)),y1:Math.round(Math.max(y0,y1))};
  if(kind==='rectangle')r.radius=Math.max(0,+o.radius||0);if(kind==='polygon')r.sides=clamp(Math.round(+o.sides||5),3,100);return r;}
function polygonPoints(g){const n=g.sides,pts=[];for(let i=0;i<n;i++){const a=-Math.PI/2+i*2*Math.PI/n;pts.push([Math.cos(a),Math.sin(a)]);}
  const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]),mx=Math.min(...xs),Mx=Math.max(...xs),my=Math.min(...ys),My=Math.max(...ys);
  return pts.map(([x,y])=>[g.x0+(x-mx)/(Mx-mx)*(g.x1-g.x0),g.y0+(y-my)/(My-my)*(g.y1-g.y0)]);}
function linePoints(g){const dx=g.x1-g.x0,dy=g.y1-g.y0,L=Math.hypot(dx,dy)||1,nx=-dy/L*g.weight/2,ny=dx/L*g.weight/2;
  return[[g.x0+nx,g.y0+ny],[g.x1+nx,g.y1+ny],[g.x1-nx,g.y1-ny],[g.x0-nx,g.y0-ny]];}
function pathOf(g){const p=new Path2D();
  if(g.kind==='rectangle'){const w=g.x1-g.x0,h=g.y1-g.y0,r=Math.min(g.radius||0,w/2,h/2);if(r>0)p.roundRect(g.x0,g.y0,w,h,r);else p.rect(g.x0,g.y0,w,h);}
  else if(g.kind==='ellipse')p.ellipse((g.x0+g.x1)/2,(g.y0+g.y1)/2,(g.x1-g.x0)/2,(g.y1-g.y0)/2,0,0,Math.PI*2);
  else{const pts=g.kind==='polygon'?polygonPoints(g):linePoints(g);pts.forEach(([x,y],i)=>i?p.lineTo(x,y):p.moveTo(x,y));p.closePath();}return p;}
function boundsOf(g){if(g.kind!=='line')return{x:g.x0,y:g.y0,w:g.x1-g.x0,h:g.y1-g.y0};const pts=linePoints(g),xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
  const x=Math.floor(Math.min(...xs)),y=Math.floor(Math.min(...ys));return{x,y,w:Math.ceil(Math.max(...xs))-x,h:Math.ceil(Math.max(...ys))-y};}
const tooSmall=g=>g.kind==='line'?Math.hypot(g.x1-g.x0,g.y1-g.y0)<1:(g.x1-g.x0<1||g.y1-g.y0<1);

// ----- rasterizing: exact area coverage, computed here rather than by the browser's path filling ---------------------------
// The canvas fills paths with the graphics driver's anti-aliasing, which differs between GPUs and the software renderer
// (SwiftShader's coverage is coarse: with Anti-alias off whole pixels landed on the wrong side of the outline). So each
// shape is described by its horizontal spans: for SUB sub-rows per pixel row, spans(y) gives the [x0, x1] intervals inside
// the shape, and each interval adds its exact horizontal coverage to the pixels it crosses. Same pixels on every PC.
const SUB=16;
function spansOf(s){ // s = {kind:'rrect', x0, y0, x1, y1, r} | {kind:'ellipse', cx, cy, rx, ry} | {kind:'poly', pts}
  if(s.kind==='rrect'){const{x0,y0,x1,y1}=s,r=Math.max(0,Math.min(s.r||0,(x1-x0)/2,(y1-y0)/2));
    return y=>{if(y<y0||y>y1||x1<=x0)return[];let d=0;const dy=y<y0+r?y0+r-y:y>y1-r?y-(y1-r):0;if(dy>0)d=r-Math.sqrt(Math.max(0,r*r-dy*dy));return[[x0+d,x1-d]];};}
  if(s.kind==='ellipse'){const{cx,cy,rx,ry}=s;return y=>{if(rx<=0||ry<=0)return[];const t=(y-cy)/ry;if(t*t>=1)return[];const h=rx*Math.sqrt(1-t*t);return[[cx-h,cx+h]];};}
  const P=s.pts,n=P.length;return y=>{const xs=[];for(let i=0;i<n;i++){const[xa,ya]=P[i],[xb,yb]=P[(i+1)%n];if((ya>y)!==(yb>y))xs.push(xa+(y-ya)*(xb-xa)/(yb-ya));}
    xs.sort((a,b)=>a-b);const out=[];for(let i=0;i+1<xs.length;i+=2)out.push([xs[i],xs[i+1]]);return out;};}
// Coverage 0..1 of every pixel of R by the shape.
function coverage(s,R){const W=R.w,cov=new Float32Array(W*R.h),spans=spansOf(s),acc=new Float64Array(W+1),k=1/SUB;
  for(let j=0;j<R.h;j++){acc.fill(0);const row=new Float64Array(W);
    for(let q=0;q<SUB;q++){const y=R.y+j+(q+.5)/SUB;
      for(const[a0,b0]of spans(y)){const a=Math.max(0,a0-R.x),b=Math.min(W,b0-R.x);if(!(b>a))continue;const ia=Math.floor(a),ib=Math.floor(b);
        if(ia===ib){row[ia]+=(b-a)*k;continue;}row[ia]+=(ia+1-a)*k;acc[ia+1]+=k;acc[ib]-=k;if(ib<W)row[ib]+=(b-ib)*k;}}
    let run=0;for(let i=0;i<W;i++){run+=acc[i];cov[j*W+i]=Math.min(1,row[i]+run);}}
  return cov;}
// The shape itself and its inside part farther than w from the outline (where the fill shows inside an inner stroke).
function convexInset(pts,w){ // each edge moved inward by w, neighbours intersected; null when it collapses
  const n=pts.length;let area=0;for(let i=0;i<n;i++){const[a,b]=pts[i],[c,d]=pts[(i+1)%n];area+=a*d-b*c;}const sgn=area>0?1:-1;
  const lines=pts.map((p,i)=>{const q=pts[(i+1)%n],dx=q[0]-p[0],dy=q[1]-p[1],L=Math.hypot(dx,dy)||1,nx=-dy/L*sgn,ny=dx/L*sgn;return{px:p[0]+nx*w,py:p[1]+ny*w,dx,dy};});
  const out=[];for(let i=0;i<n;i++){const A=lines[(i+n-1)%n],B=lines[i],den=A.dx*B.dy-A.dy*B.dx;if(Math.abs(den)<1e-12)return null;const t=((B.px-A.px)*B.dy-(B.py-A.py)*B.dx)/den;out.push([A.px+A.dx*t,A.py+A.dy*t]);}
  let a2=0;for(let i=0;i<n;i++){const[a,b]=out[i],[c,d]=out[(i+1)%n];a2+=a*d-b*c;}return a2*area>0&&Math.abs(a2)<Math.abs(area)?out:null;}
function shapeOf(g){if(g.kind==='rectangle')return{kind:'rrect',x0:g.x0,y0:g.y0,x1:g.x1,y1:g.y1,r:g.radius||0};
  if(g.kind==='ellipse')return{kind:'ellipse',cx:(g.x0+g.x1)/2,cy:(g.y0+g.y1)/2,rx:(g.x1-g.x0)/2,ry:(g.y1-g.y0)/2};
  return{kind:'poly',pts:g.kind==='polygon'?polygonPoints(g):linePoints(g)};}
function insetOf(s,w){
  if(s.kind==='rrect'){const r=Math.max(0,Math.min(s.r||0,(s.x1-s.x0)/2,(s.y1-s.y0)/2));const x0=s.x0+w,y0=s.y0+w,x1=s.x1-w,y1=s.y1-w;return x1>x0&&y1>y0?{kind:'rrect',x0,y0,x1,y1,r:Math.max(0,r-w)}:null;}
  if(s.kind==='ellipse')return s.rx>w&&s.ry>w?{kind:'ellipse',cx:s.cx,cy:s.cy,rx:s.rx-w,ry:s.ry-w}:null;   // exact for circles, close for ellipses
  const pts=convexInset(s.pts,w);return pts?{kind:'poly',pts}:null;}

// The shape's pixels over R (a canvas the size of R): the stroke color in the band of the stroke width just inside the
// outline, the fill color inside that band (so the stroke sits inside and no fill color fringes the outer edge).
function render(g,o,R){const fill=isHex(o.fill)?U.rgbOf(o.fill):null,stroke=isHex(o.stroke)?U.rgbOf(o.stroke):null,w=Math.max(0,+o.width||0);
  if(!fill&&!(stroke&&w>0))return null;
  const s=shapeOf(g),outer=coverage(s,R),inner=stroke&&w>0?(()=>{const t=insetOf(s,w);return t?coverage(t,R):new Float32Array(outer.length);})():null;
  const D=new ImageData(R.w,R.h),d=D.data,hard=o.antialias===false;
  for(let i=0;i<outer.length;i++){let cov=outer[i],ic=inner?Math.min(cov,inner[i]):cov;
    if(hard){cov=cov>=.5?1:0;ic=ic>=.5&&cov?1:0;}
    const band=inner?cov-ic:0,top=fill?ic:0,a=band+top;if(a<=0)continue;const k=i*4;
    for(let ch=0;ch<3;ch++)d[k+ch]=Math.round(((band?band*stroke[ch]:0)+(top?top*fill[ch]:0))/a);d[k+3]=Math.round(a*255);}
  const c=U.canvas(R.w,R.h);U.write(c,D);return c;}

async function draw(kind,g,o){const doc=Ed.doc;if(!doc)return false;const label=LABEL[kind]+' Tool',b=boundsOf(g),R=U.area(U.grow(b,2));if(!R)return false;
  const op={tool:kind,mode:o.mode};
  if(o.mode==='pixels')return K.applyPaint(label,()=>{const c=render(g,o,R);return c?{canvas:c,rect:R}:null;},{rect:R,op});
  const c=render(g,o,R);if(!c)return false;
  const names=new Set(Ed.layers.list().map(L=>L.name));let n=1;while(names.has(`${LABEL[kind]} ${n}`))n++;
  Ed.layers.add({name:`${LABEL[kind]} ${n}`,canvas:c,x:R.x,y:R.y,history:label,op});return true;}

// ----- the tools ----------------------------------------------------------------------------------------------------------
function options(kind){const list=[
  {key:'mode',label:'',type:'segmented',options:[{value:'shape',label:'Shape',title:'Draw each shape on a new layer'},{value:'pixels',label:'Pixels',title:'Draw into the current layer'}],default:'shape'},
  {key:'fill',label:'Fill',type:'fill-paint',default:'#ffffff',tool:kind,title:'The fill color of the shape'},
  {key:'stroke',label:'Stroke',type:'fill-paint',default:'',tool:kind,title:'The color of the line along the inside of the outline'},
  {key:'width',label:'',type:'number',min:0,max:100,step:1,unit:'px',default:2,title:'Stroke width'}];
  if(kind==='rectangle')list.push({key:'radius',label:'Radius',type:'number',min:0,max:1000,step:1,unit:'px',default:0,title:'Rounds the corners'});
  if(kind==='polygon')list.push({key:'sides',label:'Sides',type:'number',min:3,max:100,step:1,default:5,title:'Number of sides'});
  if(kind==='line')list.push({key:'weight',label:'Weight',type:'number',min:1,max:1000,step:1,unit:'px',default:4,title:'How thick the line is'});
  list.push({key:'antialias',label:'Anti-alias',type:'checkbox',default:true,title:'Smooth edges; off keeps every pixel fully in or out (pixel art)'});return list;}
function overlay(ctx,v,g){const p=new Path2D(),pts=g.kind==='rectangle'||g.kind==='ellipse'?null:(g.kind==='polygon'?polygonPoints(g):linePoints(g));
  if(g.kind==='rectangle'){const A=v.toScreen(g.x0,g.y0),B=v.toScreen(g.x1,g.y1),r=Math.min(g.radius||0,(g.x1-g.x0)/2,(g.y1-g.y0)/2)*v.scale;p.roundRect(A.x,A.y,B.x-A.x,B.y-A.y,Math.max(0,r));}
  else if(g.kind==='ellipse'){const A=v.toScreen(g.x0,g.y0),B=v.toScreen(g.x1,g.y1);p.ellipse((A.x+B.x)/2,(A.y+B.y)/2,Math.abs(B.x-A.x)/2,Math.abs(B.y-A.y)/2,0,0,Math.PI*2);}
  else pts.forEach(([x,y],i)=>{const P=v.toScreen(x,y);i?p.lineTo(P.x,P.y):p.moveTo(P.x,P.y);});if(pts)p.closePath();
  ctx.fillStyle='rgba(102,221,210,.18)';ctx.fill(p);ctx.lineWidth=3;ctx.strokeStyle='rgba(0,0,0,.65)';ctx.stroke(p);ctx.lineWidth=1;ctx.strokeStyle='#ffffff';ctx.stroke(p);}
let syncing=false;
function tool(kind,order){return{label:LABEL[kind],group:'shape',order,key:'u',cursor:'crosshair',icon:()=>Ed.icon(ICON[kind]),options:options(kind),
  onOptions(values){if(syncing)return;syncing=true;try{for(const k of Object.keys(LABEL))if(k!==kind){const cur=Ed.toolOptions(k),diff={};for(const s of SHARED)if(cur[s]!==values[s])diff[s]=values[s];
    if(Object.keys(diff).length)Ed.setToolOptions(k,diff);}}finally{syncing=false;}},
  onDown(p){const o=Ed.toolOptions(kind),why=o.mode==='pixels'?Ed.editable():Ed.view.blocked;if(why){Ed.message(why,true);return false;}
    if(!isHex(o.fill)&&!(isHex(o.stroke)&&+o.width>0)){Ed.message('Pick a fill or a stroke color first.',true);return false;}
    this.d={a:{x:p.x,y:p.y},b:{x:p.x,y:p.y},shift:p.shiftKey,alt:p.altKey};Ed.view.redrawOverlay();},
  onMove(p){if(!this.d)return;Object.assign(this.d,{b:{x:p.x,y:p.y},shift:p.shiftKey,alt:p.altKey});Ed.view.redrawOverlay();},
  onUp(p){const d=this.d;this.d=null;Ed.view.redrawOverlay();if(!d)return;const o=Ed.toolOptions(kind),g=geometry(kind,d.a,{x:p.x,y:p.y},o,p.shiftKey,p.altKey);if(tooSmall(g))return;
    draw(kind,g,o).catch(e=>{Ed.message(LABEL[kind]+': '+e.message,true);Ed.test.errors.push({where:kind,message:e.message});});},
  onCancel(){this.d=null;Ed.view.redrawOverlay();},onDeactivate(){this.d=null;},
  drawOverlay(ctx,v){const d=this.d;if(!d)return;const g=geometry(kind,d.a,d.b,Ed.toolOptions(kind),d.shift,d.alt);if(!tooSmall(g))overlay(ctx,v,g);}};}
Ed.registerTool('rectangle',tool('rectangle',1));Ed.registerTool('ellipse',tool('ellipse',2));Ed.registerTool('polygon',tool('polygon',3));Ed.registerTool('line',tool('line',4));
Ed.shapes={geometry,render,pathOf};
})();
