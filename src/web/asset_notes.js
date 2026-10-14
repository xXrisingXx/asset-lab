'use strict';
// Location notes stay separate from painting state and never change a draft.
(() => {
 const panel=document.createElement('details');panel.id='locationNotes';panel.open=true;
 const title=document.createElement('summary');title.textContent='Image location & EXE references';panel.append(title);
 const content=document.createElement('div');content.id='locationContent';content.className='info';content.textContent='Select an image to see its exact storage location.';panel.append(content);
 const download=document.createElement('a');download.id='downloadAssetNotes';download.className='button full';download.textContent='Download this asset’s notes';download.hidden=true;panel.append(download);
 const all=document.createElement('a');all.href='/api/asset-notes-all';all.className='button full';all.textContent='Export notes for every image';all.download='mitosis-all-visual-locations.zip';panel.append(all);
 document.getElementById('assetInfo').closest('details').after(panel);
 const potions=document.createElement('button');potions.id='findPotions';potions.className='full';potions.textContent='Browse potions & alchemy';potions.onclick=run(()=>searchFor('', 'Potions & alchemy'));document.querySelector('.library-options').before(potions);
 const sheet=document.createElement('link');sheet.rel='stylesheet';sheet.href='asset_notes.css';document.head.append(sheet);
 let request=0;
 const p=(text,cls='')=>{const e=document.createElement('p');e.textContent=text;e.className=cls;return e;};
 async function refresh(){
  const id=++request,asset=S.asset;download.hidden=true;content.replaceChildren();
  if(!asset?.editable){content.textContent='Select an image to see its exact storage location.';return;}
  content.textContent='Reading image locations…';
  try{
   const notes=await api('/api/asset-notes?key='+encodeURIComponent(asset.key));if(id!==request)return;
   const loc=notes.pixel_location;content.replaceChildren(p('Pixels: '+loc.kind,'location-kind'),p(loc.container,'location-path'));
   if(loc.member)content.append(p('Inside archive: '+loc.member));
   content.append(p('Encoded data starts at '+loc.encoded_data_offset+' in this container.'));
   if(loc.region)content.append(p('Atlas rectangle: '+loc.region.join(', ')+' (x, y, width, height).'+(loc.rotated_clockwise_in_atlas?' Packed rotated.':'')));
   if(loc.metadata.container)content.append(p('Region definition: '+loc.metadata.container+(loc.metadata.member?' → '+loc.metadata.member:'')));
   for(const note of notes.notes)content.append(p(note,'hint'));
   if(notes.verified_usage?.length){
    content.append(p('Verified appearance & color controls','location-kind'));
    for(const finding of notes.verified_usage)content.append(p(finding));
   }
   content.append(p(notes.exe.status,'hint'));
   const refs=notes.exe.references;
   if(refs.length){
    const list=document.createElement('div');list.className='native-references';
    for(const ref of refs.slice(0,12)){
     list.append(p(ref.literal+' · '+ref.relationship,'location-kind'));
     if(ref.kind==='decoded_path')list.append(p(ref.description+' Runtime decoded buffer: '+ref.decoded_buffer_va));
     else list.append(p('EXE string offset '+ref.string_file_offset+' · VA '+ref.string_va));
     for(const use of (ref.uses||[]).slice(0,8))list.append(p(use.function+' @ '+use.function_va+'\nInstruction file offset '+use.file_offset));
     if((ref.uses||[]).length>8)list.append(p('Additional references are included in the downloaded notes.','hint'));
    }content.append(list);
    if(refs.length>12)content.append(p('Showing the first 12 matches. Download the notes for every reference.','hint'));
   }
   download.href='/api/asset-notes?key='+encodeURIComponent(asset.key)+'&download=1';download.hidden=false;
  }catch(e){if(id===request)content.textContent='Location notes unavailable: '+e.message;}
 }
 // The EXE-reference index is research data kept on the developer's PC; shared copies leave the panel out.
 fetch('/api/status').then(r=>r.json()).then(st=>{
  if(!st.research){panel.remove();return;}
  new MutationObserver(()=>refresh()).observe(document.getElementById('assetName'),{childList:true,subtree:true});
  if(S.asset)refresh();
 }).catch(()=>{});
})();
