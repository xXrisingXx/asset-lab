'use strict';
// Asset Lab in the player's language: English, Spanish, Portuguese, French or German.
//
// Loaded first (in <head>), before any other script. The language is the "language" setting (settings.json in the data
// home: '', 'en', 'es', 'pt', 'fr' or 'de'), which lab_server.py writes into <html data-language>; '' or no setting follows
// the browser (navigator.languages). The page stays invisible until i18n/<lang>.json has arrived and translated it, so
// English never flashes up.
//
// The rest of the lab is written in English and knows nothing about this: the translation happens in the DOM. Text
// nodes and the title, placeholder, aria-label, alt and label attributes (and the value of <input type=button|submit>)
// are looked up in the dictionary, now and whenever the page changes (one MutationObserver). Text nodes are changed
// in place (node.data), never replaced, so event handlers and references to them keep working. What the player made or
// typed is never touched: input values, <pre>, <code>, <textarea>, asset and layer names, their own chat messages
// (SKIP below). Text that is not in the dictionary simply stays English, so new UI text shows up in English until it is
// translated.
//
// i18n/<lang>.json: {"strings": {"English": "translation"}, "patterns": {"English with {n} and {x}": "…"},
// "keys": {"Ctrl": "Strg"}}.
// Lookup order: exact string; patterns; the string without leading/trailing symbols ("▶ Play", "Next →"); ALL CAPS
// text through the dictionary in any case; and pieces: lines, " · " parts, sentences (each translated on its own) and
// comma lists (only when every item translates). Pattern placeholders: {n}, {n2}... a number; {s}, {s2}... any text kept
// as it is (names, paths, colors); any other {name} is text that is translated in turn when the dictionary knows it
// (and must be, when the pattern has little text of its own, like "{x} and {y}"). Patterns are tried longest-literal
// first, so the most specific one wins. Keyboard shortcuts ("Ctrl+Z", "Brush (B)") keep their keys, named through
// "keys" (Strg on German keyboards).
//
// window.LabI18n = {lang, languages, ready (a promise), t(english), set(code), untranslated(root)}: t() translates a
// string for code that does not go through the DOM (native dialogs, canvas text); set() switches the page to another
// language at once (it does not save the setting: settings.js does, then reloads).
(()=>{
const LANGS=['en','es','pt','fr','de'];
const html=document.documentElement;
// What the player made or typed, and names and paths: never translated.
const SKIP='script,style,pre,code,textarea,svg,[contenteditable=""],[contenteditable="true"],[translate="no"],.notranslate,.asset-label,#textPreview,#themeModel,.theme-bubble.user,.ed-layer-name,.ed-rename,#aeSrcName,.settings-path,.settings-host';
// Where names live beside a few fixed texts: whole-string matches only.
const EXACT='#assetName,.mod-chip,.review-text>b,.ae-titles>b,.fonts-list,.design-part-title';
const ATTRS=['title','placeholder','aria-label','alt','label'];
const SYM='\\s←→↑↓↗↘↙↖↶↷⇤⇥⇄▶▸◀■□◉●○★☆✦✕✓✔×‹›«»▾▴↺↻•·+–—';
const EDGE=new RegExp(`^([${SYM}]*)([\\s\\S]*?)([${SYM}]*)$`);
const LETTER=/[A-Za-z]/;
const code=v=>{v=String(v||'').toLowerCase().split(/[-_]/)[0];return LANGS.includes(v)?v:'';};
function browserLanguage(){for(const l of (navigator.languages&&navigator.languages.length?navigator.languages:[navigator.language])){const c=code(l);if(c)return c;}return 'en';}

let lang='en',strings=null,lower=null,patterns=[],keys=new Map(),cache=new Map(),ready=false;
const textState=new WeakMap(),textOrig=new WeakMap(),attrState=new WeakMap();

// ----- lookups -----------------------------------------------------------------------------------------------------
const escape=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const HOLE=/\{([a-z]+\d*)\}/g;
function compile(en,tr){
  // A name ({s}) never runs across a " · " or a line break the pattern does not have itself: "Edits 1 KB · builds 0 KB"
  // is two pieces. Phrases ({x}) may ("This mod: 2 edits · 1 sound."), as they are translated piece by piece in turn.
  const name=en.includes(' · ')||en.includes('\n')?'([\\s\\S]+?)':'((?:(?! · |\\n)[\\s\\S])+?)';
  const names=[];let re='^',last=0;
  for(const m of en.matchAll(HOLE)){re+=escape(en.slice(last,m.index));names.push(m[1]);re+=m[1][0]==='n'?'(-?\\d[\\d.,\\u00a0\\u202f]*)':m[1][0]==='s'?name:'([\\s\\S]+?)';last=m.index+m[0].length;}
  const literal=en.split(/\{[a-z]+\d*\}/).sort((a,b)=>b.length-a.length)[0]||'';
  // Little fixed text ("{x} and {y}", "Undo {x}"): the phrases must translate too, or "and" alone would be translated.
  const strict=en.replace(/\{[a-z]+\d*\}/g,'').replace(/[^A-Za-z]/g,'').length<8;
  return {re:new RegExp(re+escape(en.slice(last))+'$'),names,tr,literal,strict};
}
function usePattern(p,s,depth){
  const m=p.re.exec(s);if(!m)return null;const values={};
  for(let i=0;i<p.names.length;i++){const name=p.names[i];let v=m[i+1];
    if(name[0]!=='n'&&name[0]!=='s'){const core=v.trim(),t=find(core,depth+1);if(t!=null)v=v.replace(core,()=>t);else if(p.strict&&LETTER.test(core))return null;}
    values[name]=v;}
  return p.tr.replace(HOLE,(all,name)=>name in values?values[name]:all);
}
// Splits at sep, but not inside parentheses: "rainbow gradient (45°, colorize), gloss 0.3" is two items.
function splitTop(s,sep){
  const out=[];let depth=0,from=0;
  for(let i=0;i<s.length;i++){const c=s[i];if(c==='(')depth++;else if(c===')')depth=Math.max(0,depth-1);
    else if(!depth&&s.startsWith(sep,i)){out.push(s.slice(from,i));from=i+sep.length;i+=sep.length-1;}}
  out.push(s.slice(from));return out;
}
// Translates the parts on their own; all=true: only when every part with letters translates (comma lists).
function pieces(s,sep,depth,all,join=sep){
  const parts=sep===', '?splitTop(s,sep):s.split(sep);if(parts.length<2)return null;let hits=0;
  const out=parts.map(part=>{const core=part.trim();if(!core||!LETTER.test(core))return part;const t=find(core,depth+1);if(t==null)return part;hits++;return part.replace(core,()=>t);});
  return hits&&(!all||hits===parts.filter(p=>LETTER.test(p)).length)?out.join(join):null;
}
// Keyboard shortcuts: "Ctrl+Shift+Z", "F", "Ctrl++". Key names go through "keys" (German keyboards say Strg, Umschalt).
const KEY=/^(?:Ctrl|Shift|Alt|Esc|Delete|Backspace|Space|Tab|Enter|Home|End|F\d{1,2}|[A-Z0-9]|[←→↑↓[\],.=+\-])$/;
function shortcut(k){
  if(!k||k.length>24)return null;const parts=k.split(/\+(?!$)/);
  return parts.every(p=>KEY.test(p))?parts.map(p=>keys.get(p)??p).join('+'):null;
}
function find(s,depth=0){
  if(!s||depth>6)return null;
  const own=strings.get(s);if(own!==undefined)return own;
  for(const p of patterns){if(p.literal&&!s.includes(p.literal))continue;const r=usePattern(p,s,depth);if(r!=null)return r;}
  const k=shortcut(s);if(k!=null)return k===s?null:k;
  const tip=/^([\s\S]*?\S)\s*\(([^()]{1,24})\)$/.exec(s);   // "Fit on screen (Ctrl+0)", "Brush (B)"
  if(tip){const key=shortcut(tip[2]);if(key!=null){const head=find(tip[1],depth+1);if(head!=null||key!==tip[2])return `${head??tip[1]} (${key})`;}}
  const e=EDGE.exec(s);if(e&&(e[1]||e[3])&&e[2]&&LETTER.test(e[2])){const t=find(e[2],depth+1);if(t!=null)return e[1]+t+e[3];}
  if(s===s.toUpperCase()&&s!==s.toLowerCase()){const t=lower.get(s.toLowerCase());if(t!==undefined)return t.toUpperCase();}
  if(s.includes('\n'))return pieces(s,'\n',depth);
  const sentences=s.split(/(?<=[.!?…:])\s+(?=[A-Z“"(«„])/);
  if(sentences.length>1)return pieces(sentences.join('\u0000'),'\u0000',depth,false,' ');
  if(s.includes(' · '))return pieces(s,' · ',depth);
  if(s.includes(' + '))return pieces(s,' + ',depth);
  if(s.includes(', '))return pieces(s,', ',depth,true);
  return null;
}
function lookup(s){
  if(!strings||!s||!LETTER.test(s))return null;
  if(cache.has(s))return cache.get(s);
  const r=find(s);if(cache.size>20000)cache.clear();cache.set(s,r);return r;
}
function exactLookup(s){
  if(!strings)return null;const own=strings.get(s);if(own!==undefined)return own;
  const e=EDGE.exec(s);if(e&&(e[1]||e[3])&&e[2]){const t=strings.get(e[2]);if(t!==undefined)return e[1]+t+e[3];}
  return null;
}
function t(english){
  const s=String(english??'');const core=s.trim();const r=lookup(core);
  return r==null?s:s.replace(core,()=>r);
}

// ----- the page ----------------------------------------------------------------------------------------------------
function doText(node,checked){
  const v=node.data;if(textState.get(node)===v)return;   // our own change coming back
  const host=node.parentElement;if(!host||!LETTER.test(v))return;
  if(!checked&&host.closest(SKIP))return;
  const core=v.trim();const r=host.closest(EXACT)?exactLookup(core):lookup(core);
  if(r==null||r===core)return;
  const out=v.replace(core,()=>r);textOrig.set(node,v);textState.set(node,out);node.data=out;
}
function doAttr(el,name,checked){
  const v=el.getAttribute(name);if(v==null||!LETTER.test(v))return;
  let st=attrState.get(el);if(st&&st[name]&&st[name][1]===v)return;
  if(!checked&&el.closest(SKIP))return;
  const core=v.trim(),r=lookup(core);if(r==null||r===core)return;
  const out=v.replace(core,()=>r);if(!st)attrState.set(el,st={});st[name]=[v,out];el.setAttribute(name,out);
}
function doElement(el,checked){
  for(const a of ATTRS)if(el.hasAttribute(a))doAttr(el,a,checked);
  if(el.tagName==='INPUT'&&/^(button|submit|reset)$/i.test(el.type)&&el.hasAttribute('value'))doAttr(el,'value',checked);
}
const reject={acceptNode:n=>n.nodeType===1&&n.matches(SKIP)?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT};
function walk(root){
  if(root.nodeType===3){doText(root);return;}
  if(root.nodeType!==1||root.closest(SKIP))return;
  doElement(root,true);
  const w=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT,reject);let n;
  while((n=w.nextNode()))n.nodeType===3?doText(n,true):doElement(n,true);
}
// Puts the English back everywhere (before another language, or English itself).
function restore(){
  const w=document.createTreeWalker(html,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);let n;
  while((n=w.nextNode())){
    if(n.nodeType===3){if(textState.get(n)===n.data){const v=textOrig.get(n);textState.delete(n);n.data=v;}continue;}
    const st=attrState.get(n);if(!st)continue;
    for(const [name,[v,out]] of Object.entries(st))if(n.getAttribute(name)===out)n.setAttribute(name,v);
    attrState.delete(n);
  }
}
const observer=new MutationObserver(records=>{
  if(!ready)return;
  for(const r of records){
    if(r.type==='childList'){for(const n of r.addedNodes)if(n.isConnected)walk(n);}
    else if(r.type==='characterData')doText(r.target);
    else if(r.type==='attributes')doAttr(r.target,r.attributeName);
  }
});
observer.observe(html,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:[...ATTRS,'value']});

function use(data,next){
  strings=new Map(Object.entries(data&&data.strings||{}).filter(([k,v])=>typeof v==='string'&&v));
  lower=new Map();for(const [k,v] of strings)if(!lower.has(k.toLowerCase()))lower.set(k.toLowerCase(),v);
  patterns=Object.entries(data&&data.patterns||{}).filter(([k,v])=>typeof v==='string'&&v&&/\{[a-z]+\d*\}/.test(k)).map(([k,v])=>compile(k,v)).sort((a,b)=>b.literal.length-a.literal.length);
  keys=new Map(Object.entries(data&&data.keys||{}).filter(([k,v])=>typeof v==='string'&&v));
  cache=new Map();lang=next;html.lang=next;
}
async function load(next){
  if(next==='en')return null;
  const r=await fetch(`i18n/${next}.json`,{cache:'no-cache'});if(!r.ok)throw Error(`i18n/${next}.json: ${r.status}`);
  return await r.json();
}
async function set(value){
  const next=code(value)||(value?'en':browserLanguage());
  const data=await load(next);
  observer.takeRecords();ready=false;restore();
  if(next==='en'){strings=null;lower=null;patterns=[];keys=new Map();cache=new Map();lang='en';html.lang='en';ready=true;return lang;}
  use(data,next);walk(html);observer.takeRecords();ready=true;return lang;
}

// What still shows in English (for tests and translators): visible text and attributes the dictionary does not cover.
function untranslated(root=document.body){
  const out=new Set();const visible=el=>!!(el.offsetWidth||el.offsetHeight||el.getClientRects().length)&&getComputedStyle(el).visibility!=='hidden';
  const w=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT,reject);let n;
  while((n=w.nextNode())){
    if(n.nodeType===3){const host=n.parentElement,v=n.data.trim();if(!v||!LETTER.test(v)||textState.get(n)===n.data||!host)continue;
      const box=/^(OPTION|OPTGROUP)$/.test(host.tagName)?host.closest('select'):host;if(!box||!visible(box)||box.closest(SKIP))continue;
      if(!host.closest(EXACT)&&shortcut(v)==null&&lookup(v)==null)out.add(v);continue;}   // EXACT zones hold names
    if(!visible(n))continue;
    for(const a of ATTRS){const v=(n.getAttribute(a)||'').trim();if(!v||!LETTER.test(v))continue;const st=attrState.get(n);if(st&&st[a]&&st[a][1]===n.getAttribute(a))continue;if(shortcut(v)==null&&lookup(v)==null)out.add(`[${a}] ${v}`);}
  }
  return [...out];
}

// ----- start -------------------------------------------------------------------------------------------------------
const setting=html.hasAttribute('data-language')?html.getAttribute('data-language'):null;
async function chosen(){
  if(setting!==null)return setting;
  try{const r=await fetch('/api/language');if(r.ok)return (await r.json()).language||'';}catch(e){}
  return '';
}
const first=setting!==null?(code(setting)||browserLanguage()):null;
const hide=first!=='en';
if(hide)html.style.visibility='hidden';   // until the dictionary is in, so no English flashes up
const reveal=()=>{if(hide)html.style.visibility='';};
const fallback=setTimeout(reveal,4000);
const started=(async()=>{
  try{
    const next=first||code(await chosen())||browserLanguage();
    const data=await load(next);
    if(next!=='en'){use(data,next);walk(html);observer.takeRecords();}
    else html.lang='en';
  }catch(e){strings=null;lang='en';html.lang='en';}
  ready=true;clearTimeout(fallback);reveal();return lang;
})();

window.LabI18n={get lang(){return lang;},languages:LANGS.slice(),ready:started,t,set,untranslated};
})();
