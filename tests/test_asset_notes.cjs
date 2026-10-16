'use strict';
// Read-only browser coverage: notes, export, filtering, and draft isolation.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.LAB_NOTES_URL||'http://127.0.0.1:8756';
const checks=[],errors=[];
const check=(name,ok)=>{assert.ok(ok,name);checks.push(name);};
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
 page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text()+' '+m.location().url);});
 const before=await(await fetch(base+'/api/status')).json();
 try{
  await page.goto(base);await page.waitForSelector('.asset-card');
  check('All asset categories populate alongside optional Favorites',await page.locator('#group option').evaluateAll(options=>options.some(o=>o.value==='Potions & alchemy')&&options.some(o=>o.value==='Buttons & panels')));
  await page.evaluate(()=>status());
  check('Status refresh does not duplicate category options',await page.locator('#group option').evaluateAll(options=>new Set(options.map(o=>o.value)).size===options.length));
  check('Location notes panel loads',await page.locator('#locationNotes').count()===1);
  await page.locator('#search').fill('bg2.png');
  await page.waitForTimeout(350);await page.locator('.asset-card').first().click();
  await page.waitForFunction(()=>document.querySelector('#locationContent').textContent.includes('Pixels:'));
  check('Loose image location appears',await page.locator('#locationContent').textContent().then(t=>t.includes('loose image file')&&t.includes('bg2.png')));
  const noteDownload=page.waitForEvent('download');await page.locator('#downloadAssetNotes').click();
  const note=await noteDownload;const text=fs.readFileSync(await note.path(),'utf8');
  check('Individual notes include source and executable fingerprint',text.includes('bg2.png')&&text.includes('EXE SHA-256:'));
  if(!before.game.includes('\\qa\\')){
   check('Normal background has verified loader and color notes',text.includes('0x140471EB0')&&text.includes('0x39F7C3'));
   check('Encoded background filename includes its byte locations',text.includes('Encoded initialization operands:')&&text.includes('assets/bg2.png'));
  }
  const bulkDownload=page.waitForEvent('download');await page.locator('a[href="/api/asset-notes-all"]').click();
  const bulk=await bulkDownload;const bytes=fs.readFileSync(await bulk.path());
  check('All visual notes export downloads a ZIP',bytes.subarray(0,4).equals(Buffer.from([80,75,3,4])));
  await page.locator('#search').fill('button-up-skin');await page.waitForTimeout(350);await page.locator('.asset-card').first().click();
  await page.waitForFunction(()=>S.asset?.name==='button-up-skin'&&document.querySelector('#locationContent').textContent.includes('Atlas rectangle:'));
  check('Sprite notes include coordinates and region metadata',await page.locator('#locationContent').textContent().then(t=>t.includes('Region definition:')));
  const savedState=await page.evaluate(()=>({dirty:S.dirty,revision:S.asset.revision,undo:S.undo.length}));
  await page.locator('#locationNotes summary').click();await page.locator('#locationNotes summary').click();
  check('Inspecting notes preserves the current draft',JSON.stringify(savedState)===JSON.stringify(await page.evaluate(()=>({dirty:S.dirty,revision:S.asset.revision,undo:S.undo.length}))));
  if(!before.game.includes('\\qa\\')){
   await page.locator('#search').fill('icon-goto-previous');await page.waitForTimeout(350);await page.locator('.asset-card').first().click();
   await page.waitForFunction(()=>S.asset?.name==='icon-goto-previous'&&document.querySelector('#locationContent').textContent.includes('ACTIVE_ITEM_COLOR'));
   const arrowText=await page.locator('#locationContent').textContent();
   check('Arrow inspection identifies exact pink and both operands',arrowText.includes('#E52C58')&&arrowText.includes('0x39F7CD')&&arrowText.includes('0x3978A5'));
   check('Arrow inspection explains shared palette and white sprite',arrowText.includes('white')&&arrowText.includes('shared palette'));
  }
  await page.locator('#findPotions').click();await page.waitForFunction(()=>!S.busy&&document.querySelector('#group').value==='Potions & alchemy');
  check('Potion shortcut selects the category',await page.locator('#group').inputValue()==='Potions & alchemy');
  if(!before.game.includes('\\qa\\')){
   await page.waitForSelector('.asset-card');
   check('Real potion library has artwork',await page.locator('.asset-card').count()>5);
   await page.locator('.asset-card').first().click();
   await page.waitForFunction(()=>S.asset?.group==='Potions & alchemy'&&document.querySelector('#locationContent').textContent.includes('visually verified'));
   check('Potion notes are backed by an archive member',await page.locator('#locationContent').textContent().then(t=>t.includes('Inside archive:')&&t.includes('visually verified')));
  }
  for(const width of [1600,1280]){
   await page.setViewportSize({width,height:900});
   check('No horizontal overflow at '+width+'px',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  check('No JavaScript or resource errors',errors.length===0);
  const after=await(await fetch(base+'/api/status')).json();
  check('Browsing notes does not change saved or installed edits',JSON.stringify([before.active,before.staged,before.saved_count])===JSON.stringify([after.active,after.staged,after.saved_count]));
  const label=before.game.includes('\\qa\\')?'fixture':'real';
  await page.screenshot({path:path.join(__dirname,'evidence',label+'-location-notes.png'),fullPage:true});
  const report={passed:true,url:base,count:checks.length,checks,errors};
  fs.writeFileSync(path.join(__dirname,'evidence',label+'-location-tests.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }catch(e){console.error('Browser errors:',errors);throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
