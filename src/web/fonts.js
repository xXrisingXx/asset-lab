'use strict';
// Text & fonts: re-draw the game's own text (player names, menu text) in any font on this PC, with outlines, glow,
// shadow and more. The preview is rendered by the lab exactly the way the game sets the text.
// Uses app.js globals: api, message, status, run, S.
(()=>{
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const dlg=el('dialog','fonts');dlg.id='fontsDialog';dlg.setAttribute('aria-label','Text and fonts');document.body.append(dlg);
const F={data:null,font:'nicktext',style:{},text:'Cellmate',ticket:0,timer:0};

function clean(style){return {...F.data.defaults,...(style||{})};}
function fontLabel(t){return `${t.family} · ${t.style}`;}

async function open(font,style,text){
  if(!F.data)F.data=await api('/api/fonts');
  F.font=font||F.font;const saved=F.data.game.find(g=>g.id===F.font);
  F.style=clean(style||(saved&&saved.style)||{});if(text)F.text=text;
  render();dlg.showModal();refresh();
}
window.openFonts=run(open);

function slider(label,key,min,max,step,show){
  const row=el('label','fonts-slider');const r=el('input');r.type='range';r.min=min;r.max=max;r.step=step;r.value=F.style[key];
  const v=el('span','fonts-value',show(F.style[key]));r.oninput=()=>{F.style[key]=+r.value;v.textContent=show(+r.value);refresh();};
  row.append(el('span','',label),r,v);return row;
}
function colorToggle(label,key,fallback,extras){
  const box=el('div','fonts-effect');const head=el('label','fonts-effect-head');const on=el('input');on.type='checkbox';on.checked=!!F.style[key];
  const pick=el('input');pick.type='color';pick.value=F.style[key]||fallback;pick.disabled=!on.checked;
  const more=el('div','fonts-effect-more');more.hidden=!on.checked;for(const x of extras||[])more.append(x);
  on.onchange=()=>{F.style[key]=on.checked?pick.value:'';pick.disabled=!on.checked;more.hidden=!on.checked;refresh();};
  pick.oninput=()=>{F.style[key]=pick.value;refresh();};
  head.append(on,el('span','',label),pick);box.append(head,more);return box;
}

function render(){
  const d=F.data;dlg.replaceChildren();
  const head=el('div','fonts-head');const h=el('h2','','Text & fonts');const x=el('button','quiet','✕');x.type='button';x.onclick=()=>dlg.close();head.append(h,x);
  const sub=el('p','fonts-sub','Draw the game’s own text in any font on this PC, with outlines, glow and shadow. Every letter is rendered at the game’s resolution, so it stays crisp.');
  const body=el('div','fonts-body');const side=el('div','fonts-controls');const stage=el('div','fonts-stage');body.append(side,stage);dlg.append(head,sub,body);

  // Which text
  const target=el('select');for(const g of d.game){const o=el('option','',g.label+(g.staged?' · changed':''));o.value=g.id;target.append(o);}target.value=F.font;
  const note=el('p','hint',(d.game.find(g=>g.id===F.font)||{}).note||'');
  target.onchange=()=>{F.font=target.value;const g=d.game.find(g=>g.id===F.font);F.style=clean(g&&g.style);render();refresh();};
  side.append(el('div','section-label','TEXT IN THE GAME'),target,note);

  // Typeface
  const search=el('input');search.type='search';search.placeholder=`Search ${d.typefaces.length} fonts on this PC`;
  const list=el('select','fonts-list');list.size=8;
  const fill=()=>{const q=search.value.trim().toLowerCase();list.replaceChildren();const own=el('option','',"The game’s own font");own.value='';list.append(own);
    for(const t of d.typefaces){if(q&&!fontLabel(t).toLowerCase().includes(q))continue;const o=el('option','',fontLabel(t));o.value=t.id;list.append(o);}
    list.value=F.style.font||'';};
  fill();search.oninput=fill;list.onchange=()=>{F.style.font=list.value;refresh();};
  side.append(el('div','section-label','FONT'),search,list);

  // Looks
  const looks=el('div','fonts-presets');
  for(const [name,preset] of Object.entries(d.presets)){const b=el('button','',name);b.type='button';b.onclick=()=>{F.style=clean(preset);render();refresh();};looks.append(b);}
  side.append(el('div','section-label','LOOKS'),looks);

  // Shape
  const caps=el('label','fonts-check');const capsBox=el('input');capsBox.type='checkbox';capsBox.checked=!!F.style.caps;capsBox.onchange=()=>{F.style.caps=capsBox.checked;refresh();};caps.append(capsBox,el('span','','ALL CAPS'));
  side.append(el('div','section-label','SHAPE'),slider('Size','size',0.5,2,0.05,v=>Math.round(v*100)+'%'),slider('Letter spacing','spacing',-20,40,1,v=>v+' px'),
    slider('Bold','bold',0,8,1,v=>v?v+' px':'off'),slider('Slant','italic',-0.4,0.4,0.02,v=>v?Math.round(v*100)+'%':'off'),caps);

  // Color and effects
  const fillBox=el('div','fonts-effect');const fh=el('label','fonts-effect-head');const fill1=el('input');fill1.type='color';fill1.value=F.style.fill;fill1.oninput=()=>{F.style.fill=fill1.value;refresh();};
  const grad=el('input');grad.type='checkbox';grad.checked=!!F.style.fill2;const fill2=el('input');fill2.type='color';fill2.value=F.style.fill2||'#ff8a1a';fill2.disabled=!grad.checked;
  grad.onchange=()=>{F.style.fill2=grad.checked?fill2.value:'';fill2.disabled=!grad.checked;refresh();};fill2.oninput=()=>{F.style.fill2=fill2.value;refresh();};
  fh.append(el('span','','Letters'),fill1,grad,el('span','fonts-small','fade to'),fill2);fillBox.append(fh);
  side.append(el('div','section-label','COLOR & EFFECTS'),fillBox,
    colorToggle('Outline','outline','#000000',[slider('Width','outline_width',0,16,1,v=>v+' px')]),
    colorToggle('Second outline','outline2','#ffffff',[slider('Width','outline2_width',0,16,1,v=>v+' px')]),
    colorToggle('Glow','glow','#ffffff',[slider('Size','glow_size',0,32,1,v=>v+' px'),slider('Strength','glow_strength',0,4,0.1,v=>v.toFixed(1))]),
    colorToggle('Shadow','shadow','#000000',[slider('Across','shadow_x',-24,24,1,v=>v+' px'),slider('Down','shadow_y',-24,24,1,v=>v+' px'),slider('Blur','shadow_blur',0,24,1,v=>v+' px'),slider('Strength','shadow_opacity',0,1,0.05,v=>Math.round(v*100)+'%')]));

  // Preview and actions
  const sample=el('input','fonts-text');sample.value=F.text;sample.placeholder='Example text';sample.maxLength=40;sample.oninput=()=>{F.text=sample.value;refresh();};
  const img=el('img','fonts-preview');img.id='fontsPreview';img.alt='Preview';
  const meta=el('p','hint');meta.id='fontsMeta';meta.textContent='On the menu, on a red cell and on a light cell, at the size the game stores it.';
  const actions=el('div','theme-actions');const apply=el('button','primary','Apply to the game font');const reset=el('button','',"Use the game’s font");
  apply.onclick=run(async()=>{apply.disabled=true;try{const r=await api('/api/font-apply',{font:F.font,style:F.style});
      message(`${r.label} saved in ${r.face}: ${r.drawn} letters${r.missing?`, ${r.missing} taken from the game font`:''}. Install in Mitosis to see it.`);
      F.data=await api('/api/fonts');S.build=null;await status();render();refresh();}finally{apply.disabled=false;}});
  reset.onclick=run(async()=>{await api('/api/font-remove',{font:F.font});F.data=await api('/api/fonts');F.style=clean({});message('Back to the game’s own font at the next install.');S.build=null;await status();render();refresh();});
  actions.append(apply,reset);
  stage.append(el('div','section-label','EXAMPLE TEXT'),sample,img,meta,actions);
}

function refresh(){
  clearTimeout(F.timer);
  F.timer=setTimeout(async()=>{const ticket=++F.ticket;
    try{const r=await api('/api/font-preview',{font:F.font,style:F.style,text:F.text});if(ticket===F.ticket){const img=document.getElementById('fontsPreview');if(img)img.src=r.png;}}
    catch(e){const m=document.getElementById('fontsMeta');if(m)m.textContent=e.message;}},140);
}
})();
