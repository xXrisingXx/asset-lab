'use strict';
// Layered PSD files for the Asset Lab editor (editor/transform.js uses it for File > Export Layered PSD and File > Import
// PSD as Layers). Written from Adobe's "Photoshop File Formats Specification" for 8-bit RGB documents (version 1 PSD):
// - Writer: one layer record per pixel layer, bottom to top, with its name (Pascal + 'luni' Unicode), opacity, fill
//   ('iOpa'), visibility, blend mode key, clipping, lock transparency (flags) and the other locks ('lspf'), and its layer
//   mask (channel -2 with its rectangle, default color and disabled flag). Channels are PackBits (RLE) compressed, or raw
//   when that is smaller. The merged image (RGB + transparency) follows, matted with white as Photoshop stores it.
//   Channel records are in R, G, B, A, mask order and every channel's data has an even length, which also keeps
//   Pillow's PsdImagePlugin (it reads channels in that order and skips a byte after odd lengths) reading it right.
// - Reader: 8-bit RGB or Grayscale PSD with raw, RLE, ZIP or ZIP-with-prediction channels; layer groups, adjustment
//   and fill layers, styles, text and smart objects are reported so the importer can say what it kept.
// Defines window.EdPSD = {write(doc) -> Uint8Array, read(arrayBuffer) -> Promise<psd>, BLEND, KEYS}.
(function(root){
// Our blend mode ids <-> Photoshop's 4-character keys.
const BLEND={'normal':'norm','dissolve':'diss','darken':'dark','multiply':'mul ','color-burn':'idiv','linear-burn':'lbrn','darker-color':'dkCl','lighten':'lite',
  'screen':'scrn','color-dodge':'div ','linear-dodge':'lddg','lighter-color':'lgCl','overlay':'over','soft-light':'sLit','hard-light':'hLit','vivid-light':'vLit',
  'linear-light':'lLit','pin-light':'pLit','hard-mix':'hMix','difference':'diff','exclusion':'smud','subtract':'fsub','divide':'fdiv','hue':'hue ',
  'saturation':'sat ','color':'colr','luminosity':'lum '};
const KEYS=Object.fromEntries(Object.entries(BLEND).map(([k,v])=>[v,k]));
const ADJUSTMENTS={levl:'Levels',curv:'Curves',brit:'Brightness/Contrast','hue ':'Hue/Saturation',hue2:'Hue/Saturation',blnc:'Color Balance',selc:'Selective Color',
  mixr:'Channel Mixer',grdm:'Gradient Map',thrs:'Threshold',post:'Posterize',nvrt:'Invert',vibA:'Vibrance',expA:'Exposure',phfl:'Photo Filter',clrL:'Color Lookup',
  blwh:'Black & White',CgEd:'Brightness/Contrast'};
const FILLS={SoCo:'Solid Color',GdFl:'Gradient',PtFl:'Pattern'};

// ----- writing ---------------------------------------------------------------------------------------------------------
class Out{
  constructor(n){this.b=new Uint8Array(Math.max(1024,n||0));this.n=0;}
  need(k){if(this.n+k<=this.b.length)return;let m=this.b.length*2;while(m<this.n+k)m*=2;const nb=new Uint8Array(m);nb.set(this.b.subarray(0,this.n));this.b=nb;}
  u8(v){this.need(1);this.b[this.n++]=v&255;}
  u16(v){this.need(2);const b=this.b;b[this.n++]=v>>>8&255;b[this.n++]=v&255;}
  u32(v){this.need(4);const b=this.b;v>>>=0;b[this.n++]=v>>>24;b[this.n++]=v>>>16&255;b[this.n++]=v>>>8&255;b[this.n++]=v&255;}
  bytes(a){this.need(a.length);this.b.set(a,this.n);this.n+=a.length;}
  sig(s){for(let i=0;i<4;i++)this.u8(s.charCodeAt(i));}
  pad(m,start=0){while((this.n-start)%m)this.u8(0);}
  mark(){const at=this.n;this.u32(0);return at;}
  close(at){const v=this.n-at-4,b=this.b;b[at]=v>>>24;b[at+1]=v>>>16&255;b[at+2]=v>>>8&255;b[at+3]=v&255;return v;}
  done(){return this.b.slice(0,this.n);}}

// PackBits runs of one row: [start, length, repeat?]. Repeats need 2+ equal bytes; literals hold up to 128.
function runsOf(src,off,w){const runs=[];let i=0;
  while(i<w){let r=1;const v=src[off+i];while(i+r<w&&r<128&&src[off+i+r]===v)r++;
    if(r>=2){runs.push([i,r,true]);i+=r;continue;}
    let l=1;while(i+l<w&&l<128&&!(i+l+1<w&&src[off+i+l]===src[off+i+l+1]))l++;runs.push([i,l,false]);i+=l;}
  return runs;}
const runBytes=runs=>runs.reduce((n,[,l,rep])=>n+(rep?2:l+1),0);
// Re-encode one run so the row grows by an odd number of bytes (every channel's data must have an even length).
function oddTweak(runs){for(let k=0;k<runs.length;k++){const[s,l,rep]=runs[k];
    if(!rep&&l>=2){runs.splice(k,1,[s,l-1,false],[s+l-1,1,false]);return true;}            // split a literal: +1
    if(rep&&l===2){runs[k]=[s,2,false];return true;}                                        // a pair as a literal: +1
    if(rep&&l===3){runs.splice(k,1,[s,1,false],[s+1,2,false]);return true;}                 // +3
    if(rep&&l>=4){runs.splice(k,1,[s,l-2,true],[s+l-2,2,false]);return true;}}             // +3
  return false;}
function writeRuns(o,src,off,runs){for(const[s,l,rep]of runs){if(rep){o.u8(257-l);o.u8(src[off+s]);}else{o.u8(l-1);o.bytes(src.subarray(off+s,off+s+l));}}}
// One channel (plane = w*h bytes): compression code and data, RLE or raw, with an even total length.
function channel(plane,w,h){const o=new Out(plane.length/2+16);
  if(!w||!h){o.u16(0);return o.done();}
  const rows=[];let total=0;for(let y=0;y<h;y++){const r=runsOf(plane,y*w,w);rows.push(r);total+=runBytes(r);}
  if(total+2*h>=w*h&&(w*h)%2===0){o.u16(0);o.bytes(plane);return o.done();}   // raw is smaller
  if(total%2){for(const r of rows)if(oddTweak(r))break;}
  o.u16(1);for(const r of rows)o.u16(runBytes(r));for(let y=0;y<h;y++)writeRuns(o,plane,y*w,rows[y]);return o.done();}
function planeOf(rgba,W,rect,c){const{x,y,w,h}=rect,p=new Uint8Array(w*h);for(let j=0;j<h;j++){let s=((y+j)*W+x)*4+c,d=j*w;for(let i=0;i<w;i++,s+=4)p[d+i]=rgba[s];}return p;}
function boundsOf(rgba,W,H){let x0=W,y0=H,x1=-1,y1=-1;for(let y=0;y<H;y++){const r=y*W*4;for(let x=0;x<W;x++)if(rgba[r+x*4+3]){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}}
  return x1<0?{x:0,y:0,w:0,h:0}:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};}
function maskBounds(m,W,H,def){let x0=W,y0=H,x1=-1,y1=-1;for(let y=0;y<H;y++){const r=y*W;for(let x=0;x<W;x++)if(m[r+x]!==def){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}}
  return x1<0?null:{x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};}
function pascal(o,name,mult){const bytes=[];for(const ch of name){const c=ch.codePointAt(0);bytes.push(c<128?c:63);if(bytes.length>=255)break;}
  const start=o.n;o.u8(bytes.length);o.bytes(Uint8Array.from(bytes));o.pad(mult,start);}
function block(o,key,fn){o.sig('8BIM');o.sig(key);const at=o.mark(),start=o.n;fn();o.pad(4,start);o.close(at);}
function unicode(o,s){const units=[];for(let i=0;i<s.length;i++)units.push(s.charCodeAt(i));o.u32(units.length);for(const u of units)o.u16(u);}

// doc = {width, height, layers: [{name, visible, opacity, fill, blend, clip, locks, rgba (W*H*4, straight alpha),
//        mask: {data (W*H bytes), default (0|255), enabled}|null}], composite (W*H*4), active (layer index)}
function write(doc){const W=doc.width,H=doc.height,o=new Out(W*H*2+4096);const notes=[];
  o.sig('8BPS');o.u16(1);o.bytes(new Uint8Array(6));o.u16(4);o.u32(H);o.u32(W);o.u16(8);o.u16(3);
  o.u32(0);   // color mode data
  // image resources: resolution (72 ppi), the target layer, version info (the merged image is real)
  const res=o.mark();
  const resource=(id,fn)=>{o.sig('8BIM');o.u16(id);o.u16(0);const at=o.mark(),start=o.n;fn();o.close(at);o.pad(2,start);};
  resource(1005,()=>{o.u32(72<<16);o.u16(1);o.u16(1);o.u32(72<<16);o.u16(1);o.u16(1);});
  resource(1024,()=>o.u16(Math.max(0,Math.min(doc.layers.length-1,doc.active|0))));
  resource(1057,()=>{o.u32(1);o.u8(1);unicode(o,'Mitosis Asset Lab');unicode(o,'Adobe Photoshop');o.u32(1);});
  o.close(res);
  // layer and mask information
  const lmi=o.mark(),li=o.mark(),liStart=o.n;
  o.u16((-doc.layers.length)&0xffff);   // negative: the merged image's first alpha channel is its transparency
  const data=[];
  doc.layers.forEach((L,index)=>{
    const rect=boundsOf(L.rgba,W,H),chans=[0,1,2,3].map(c=>channel(planeOf(L.rgba,W,rect,c),rect.w,rect.h)),ids=[0,1,2,-1];
    let mrect=null,mdef=255;
    if(L.mask){mdef=L.mask.default===0?0:255;mrect=maskBounds(L.mask.data,W,H,mdef)||{x:0,y:0,w:W,h:H};
      const mp=new Uint8Array(mrect.w*mrect.h);for(let j=0;j<mrect.h;j++)mp.set(L.mask.data.subarray((mrect.y+j)*W+mrect.x,(mrect.y+j)*W+mrect.x+mrect.w),j*mrect.w);
      chans.push(channel(mp,mrect.w,mrect.h));ids.push(-2);}
    data.push(chans);
    o.u32(rect.y);o.u32(rect.x);o.u32(rect.y+rect.h);o.u32(rect.x+rect.w);
    o.u16(chans.length);chans.forEach((c,k)=>{o.u16(ids[k]&0xffff);o.u32(c.length);});
    let key=BLEND[L.blend];if(!key){key='norm';notes.push(`“${L.name}” uses the blend mode ${L.blend}, which Photoshop does not have; it is Normal in the PSD.`);}
    o.sig('8BIM');o.sig(key);o.u8(Math.round(Math.max(0,Math.min(1,L.opacity))*255));o.u8(L.clip?1:0);
    o.u8((L.locks&&L.locks.alpha?1:0)|(L.visible===false?2:0)|8);o.u8(0);
    const extra=o.mark(),exStart=o.n;
    if(mrect){o.u32(20);o.u32(mrect.y);o.u32(mrect.x);o.u32(mrect.y+mrect.h);o.u32(mrect.x+mrect.w);o.u8(mdef);o.u8(L.mask.enabled===false?2:0);o.u16(0);}
    else o.u32(0);
    o.u32(40);for(let k=0;k<5;k++){o.u32(0x0000ffff);o.u32(0x0000ffff);}   // blending ranges: everything blends (gray + 4 channels)
    pascal(o,L.name||'Layer',4);
    block(o,'luni',()=>unicode(o,L.name||'Layer'));
    block(o,'lyid',()=>o.u32(index+1));
    block(o,'iOpa',()=>o.u8(Math.round(Math.max(0,Math.min(1,L.fill??1))*255)));
    const lk=L.locks||{};if(lk.alpha||lk.pixels||lk.position||lk.all)block(o,'lspf',()=>o.u32((lk.alpha?1:0)|(lk.pixels?2:0)|(lk.position?4:0)|(lk.all?0x80000000:0)));
    o.close(extra);if((o.n-exStart)%2)throw Error('PSD writer: odd layer record');});
  for(const chans of data)for(const c of chans)o.bytes(c);
  o.pad(4,liStart);o.close(li);
  o.u32(0);   // global layer mask info: none
  o.close(lmi);
  // merged image: RGB matted with white where transparent, then the transparency
  const comp=doc.composite,planes=[0,1,2,3].map(c=>{const p=new Uint8Array(W*H);
    for(let i=0,s=0;i<W*H;i++,s+=4){const a=comp[s+3];p[i]=c===3?a:Math.round((comp[s+c]*a+255*(255-a))/255);}return p;});
  const rows=planes.map(p=>{const r=[];for(let y=0;y<H;y++)r.push(runsOf(p,y*W,W));return r;});
  o.u16(1);for(const r of rows)for(const row of r)o.u16(runBytes(row));
  planes.forEach((p,c)=>{for(let y=0;y<H;y++)writeRuns(o,p,y*W,rows[c][y]);});
  const bytes=o.done();bytes.notes=notes;return bytes;}

// ----- reading -----------------------------------------------------------------------------------------------------------
class In{
  constructor(buf){this.b=new Uint8Array(buf);this.v=new DataView(this.b.buffer,this.b.byteOffset,this.b.byteLength);this.p=0;}
  need(k){if(this.p+k>this.b.length)throw Error('The PSD file is cut short.');}
  u8(){this.need(1);return this.b[this.p++];}
  u16(){this.need(2);const v=this.v.getUint16(this.p);this.p+=2;return v;}
  i16(){this.need(2);const v=this.v.getInt16(this.p);this.p+=2;return v;}
  u32(){this.need(4);const v=this.v.getUint32(this.p);this.p+=4;return v;}
  i32(){this.need(4);const v=this.v.getInt32(this.p);this.p+=4;return v;}
  str(n){this.need(n);let s='';for(let i=0;i<n;i++)s+=String.fromCharCode(this.b[this.p+i]);this.p+=n;return s;}
  bytes(n){this.need(n);const a=this.b.subarray(this.p,this.p+n);this.p+=n;return a;}
  skip(n){this.need(n);this.p+=n;}}
async function inflate(bytes){
  if(typeof DecompressionStream!=='function')throw Error('This browser cannot unpack ZIP-compressed PSD layers.');
  const s=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));return new Uint8Array(await new Response(s).arrayBuffer());}
function unpackRows(src,counts,w,h,out){let p=0;
  for(let y=0;y<h;y++){const end=p+counts[y];let x=0;const row=y*w;
    while(p<end&&x<w){const n=src[p++];
      if(n<128){const l=n+1;for(let k=0;k<l&&x<w;k++)out[row+x++]=src[p++];}
      else if(n>128){const l=257-n,v=src[p++];for(let k=0;k<l&&x<w;k++)out[row+x++]=v;}}
    p=end;}
  return out;}
// One channel's pixels (w x h bytes) from its data (compression code first).
async function decode(data,w,h){const out=new Uint8Array(w*h);if(!w||!h||data.length<2)return out;
  const c=(data[0]<<8)|data[1],body=data.subarray(2);
  if(c===0){out.set(body.subarray(0,w*h));return out;}
  if(c===1){const counts=new Uint16Array(h);for(let y=0;y<h;y++)counts[y]=(body[y*2]<<8)|body[y*2+1];return unpackRows(body.subarray(2*h),counts,w,h,out);}
  if(c===2||c===3){const raw=await inflate(body);out.set(raw.subarray(0,w*h));
    if(c===3)for(let y=0;y<h;y++){const r=y*w;for(let x=1;x<w;x++)out[r+x]=(out[r+x]+out[r+x-1])&255;}return out;}
  throw Error('A PSD layer uses an unknown compression ('+c+').');}

async function read(buffer){const r=new In(buffer),warnings=[];
  if(r.str(4)!=='8BPS')throw Error('This is not a Photoshop (PSD) file.');
  const version=r.u16();if(version===2)throw Error('This is a large-document (PSB) file. Save it as a regular PSD in Photoshop first.');
  if(version!==1)throw Error('This PSD version is not supported.');
  r.skip(6);const nch=r.u16(),height=r.u32(),width=r.u32(),depth=r.u16(),mode=r.u16();
  if(depth!==8)throw Error(`This PSD has ${depth} bits per channel. Save it with 8 bits per channel (Image > Mode > 8 Bits/Channel in Photoshop).`);
  if(mode!==3&&mode!==1)throw Error('This PSD is not RGB. Convert it to RGB Color (Image > Mode > RGB Color in Photoshop) and save it again.');
  if(!width||!height||width*height>8192*8192)throw Error('This PSD is too large to open here.');
  const gray=mode===1;
  r.skip(r.u32());   // color mode data
  let target=null;const resEnd=r.u32()+r.p;
  while(r.p+12<=resEnd){const sig=r.str(4);if(sig!=='8BIM'&&sig!=='MeSa'&&sig!=='AgHg'&&sig!=='PHUT'&&sig!=='DCSR')break;const id=r.u16(),nl=r.u8();r.skip(nl+((nl+1)%2));const size=r.u32(),start=r.p;
    if(id===1024&&size>=2)target=r.u16();r.p=start+size+(size%2);}
  r.p=resEnd;
  const layers=[];let mergedAlpha=false;const lmLen=r.u32(),lmEnd=r.p+lmLen;
  if(lmLen){let liLen=r.u32();const liEnd=r.p+liLen;
    if(liLen){let count=r.i16();if(count<0){count=-count;mergedAlpha=true;}
      for(let i=0;i<count;i++){const top=r.i32(),left=r.i32(),bottom=r.i32(),right=r.i32(),n=r.u16(),chans=[];
        for(let k=0;k<n;k++)chans.push({id:r.i16(),len:r.u32()});
        if(r.str(4)!=='8BIM')throw Error('A PSD layer record is damaged.');
        const key=r.str(4),opacity=r.u8(),clipping=r.u8(),flags=r.u8();r.skip(1);
        const exLen=r.u32(),exEnd=r.p+exLen,L={name:'',rect:{x:left,y:top,w:Math.max(0,right-left),h:Math.max(0,bottom-top)},chans,blendKey:key,blend:KEYS[key]||null,
          opacity:opacity/255,fill:1,clip:clipping===1,visible:!(flags&2),locks:{alpha:!!(flags&1),pixels:false,position:false,all:false},mask:null,kind:'pixel',notes:[]};
        const mLen=r.u32(),mEnd=r.p+mLen;
        if(mLen>=18){const mt=r.i32(),ml=r.i32(),mb=r.i32(),mr=r.i32(),def=r.u8(),mf=r.u8();
          L.mask={rect:{x:ml,y:mt,w:Math.max(0,mr-ml),h:Math.max(0,mb-mt)},default:def,enabled:!(mf&2),relative:!!(mf&1)};
          if(mLen>=36&&mLen!==20){ // a "real" user mask (pixel mask beside a vector mask) comes after the vector mask's
            r.p=mEnd-18;const rf=r.u8(),rdef=r.u8(),rt=r.i32(),rl=r.i32(),rb=r.i32(),rr=r.i32();L.realMask={rect:{x:rl,y:rt,w:Math.max(0,rr-rl),h:Math.max(0,rb-rt)},default:rdef,enabled:!(rf&2)};}}
        r.p=mEnd;
        r.skip(r.u32());   // blending ranges
        const nl=r.u8();L.name=r.str(nl);r.p+=(4-((nl+1)%4))%4;
        // Additional layer information; a writer that forgot to count its padding is caught by scanning to the next signature.
        while(r.p+12<=exEnd){const sig=r.str(4);if(sig!=='8BIM'&&sig!=='8B64'){r.p-=3;continue;}const k=r.str(4),len=r.u32(),start=r.p;if(start+len>exEnd)break;
          if(k==='luni'&&len>=4){const n=r.u32();let s='';for(let j=0;j<n&&r.p+2<=start+len;j++)s+=String.fromCharCode(r.u16());L.name=s.replace(/\0+$/,'');}
          else if(k==='iOpa'&&len>=1)L.fill=r.u8()/255;
          else if(k==='lsct'||k==='lsdk'){const t=r.u32();if(t===1||t===2)L.kind='group';else if(t===3)L.kind='group-end';}
          else if(k==='lspf'&&len>=4){const f=r.u32();Object.assign(L.locks,{alpha:L.locks.alpha||!!(f&1),pixels:!!(f&2),position:!!(f&4),all:!!(f&0x80000000)});}
          else if(ADJUSTMENTS[k]){L.kind='adjustment';L.adjustment=ADJUSTMENTS[k];}
          else if(FILLS[k]){L.kind='fill';L.adjustment=FILLS[k]+' fill';}
          else if(k==='lfx2'||k==='lrFX'||k==='lmfx'||k==='lfxs')L.notes.push('style');
          else if(k==='TySh'||k==='tySh')L.notes.push('text');
          else if(k==='SoLd'||k==='PlLd'||k==='plLd'||k==='SoLE')L.notes.push('smart');
          else if(k==='vmsk'||k==='vsms')L.notes.push('vector-mask');
          r.p=start+len;}
        if(L.mask&&L.mask.relative){L.mask.rect.x+=left;L.mask.rect.y+=top;}
        r.p=exEnd;layers.push(L);}
      // channel image data, in record order
      for(const L of layers){L.planes={};
        for(const c of L.chans){const data=r.bytes(c.len);
          const rect=c.id===-2?(L.mask&&L.mask.rect):c.id===-3?(L.realMask&&L.realMask.rect):c.id>=-1&&c.id<=2?L.rect:null;if(!rect)continue;
          try{L.planes[c.id]=await decode(data,rect.w,rect.h);}catch(e){L.broken=e.message;}}
        const P=L.planes;delete L.planes;
        // The pixel mask: channel -3 when a vector mask sits beside it ("real" user mask), else channel -2.
        if(P[-3]&&L.realMask)L.mask={...L.realMask,data:P[-3]};else if(P[-2]&&L.mask)L.mask.data=P[-2];else L.mask=null;
        if(L.kind!=='pixel')continue;
        const{w,h}=L.rect,red=P[0];if(!w||!h){L.rgba=null;continue;}
        if(!red||(!gray&&(!P[1]||!P[2]))){L.broken=L.broken||'missing color channels';continue;}
        const g=gray?red:P[1],b=gray?red:P[2],a=P[-1],rgba=new Uint8ClampedArray(w*h*4);
        for(let i=0,j=0;i<w*h;i++,j+=4){rgba[j]=red[i];rgba[j+1]=g[i];rgba[j+2]=b[i];rgba[j+3]=a?a[i]:255;}
        L.rgba=rgba;}
      r.p=liEnd;}}
  r.p=lmEnd;
  // the merged image (used when there are no layers)
  let composite=null;
  if(!layers.some(L=>L.kind==='pixel')&&r.p+2<=r.b.length){try{
      const comp=r.u16(),n=nch,planes=[];
      if(comp===1){const counts=[];for(let i=0;i<n*height;i++)counts.push(r.u16());let p=r.p;
        for(let c=0;c<n;c++){const cs=Uint16Array.from(counts.slice(c*height,(c+1)*height)),len=cs.reduce((s,v)=>s+v,0);
          planes.push(unpackRows(r.b.subarray(p,p+len),cs,width,height,new Uint8Array(width*height)));p+=len;}}
      else if(comp===0){for(let c=0;c<n;c++)planes.push(r.bytes(width*height));}
      else{const all=await inflate(r.b.subarray(r.p));for(let c=0;c<n;c++)planes.push(all.subarray(c*width*height,(c+1)*width*height));}
      const colors=gray?1:3,alpha=n>colors&&mergedAlpha?planes[colors]:null;composite=new Uint8ClampedArray(width*height*4);
      for(let i=0,j=0;i<width*height;i++,j+=4){const a=alpha?alpha[i]:255;for(let c=0;c<3;c++){const v=planes[gray?0:c][i];composite[j+c]=a?(v-(255-a))*255/a:0;}composite[j+3]=a;}}
    catch(e){warnings.push('The flattened picture inside the PSD could not be read.');}}
  return{width,height,gray,layers,composite,target,warnings};}

root.EdPSD={write,read,BLEND,KEYS};
})(window);
