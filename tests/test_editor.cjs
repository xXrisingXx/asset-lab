'use strict';
const {chromium}=require('C:/Users/Typic/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const url='http://127.0.0.1:8754';
const evidence=path.resolve(__dirname,'evidence');
const checks=[],errors=[];
function check(name,result){assert.ok(result,name);checks.push(name);}
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
(async()=>{
 const state=await (await fetch(url+'/api/status')).json();
 assert.ok(state.game.includes('MitosisAssetLab\\qa\\editor-fixture\\'),'This test only permits its disposable fixture.');
 const post=async(p,data)=>{const r=await fetch(url+p,{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Token':state.token},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw Error(d.error);return d;};
 if(state.active)await post('/api/restore',{});
 const saved=await(await fetch(url+'/api/assets?saved=1')).json();
 for(const row of saved.items){let a=await(await fetch(url+'/api/asset?key='+row.key)).json();while(a.can_undo)a=await post('/api/undo',{key:a.key,revision:a.revision});}
 const files=fs.readdirSync(path.join(state.game,'assets')).map(n=>path.join(state.game,'assets',n));
 const before=Object.fromEntries(files.map(p=>[p,hash(p)]));
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
 page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('favicon.ico'))errors.push(m.text());});
 const idle=()=>page.waitForFunction(()=>!S.busy);
 async function select(name){await page.locator('#search').fill(name);await page.waitForTimeout(300);await page.locator('.asset-card').filter({has:page.locator('.asset-label',{hasText:new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$')})}).first().click();await page.waitForFunction(n=>S.asset?.name===n,name);}
 async function applyTint(color){await page.locator('#tint').fill(color);await page.locator('#tint_amount').fill('1');await page.locator('#applyAdjust').click();await idle();}
 try{
  await page.goto(url);await page.waitForSelector('.asset-card');
  check('Library loads real fixture catalog',await page.locator('.asset-card').count()>3);
  check('No horizontal overflow at 1600px',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await select('button-up-skin');
  check('Button states are grouped',await page.locator('#relatedStates').textContent().then(t=>t.includes('button-down-skin')));
  check('Shared atlas pixels are disclosed',await page.locator('#sharedWarning').isVisible());
  await page.locator('[data-view="button"]').click();
  await page.waitForFunction(()=>!document.getElementById('buttonHelp').hidden);
  check('Nine-slice inspection mode',await page.locator('#buttonHelp').isVisible());
  await applyTint('#16c9be');
  check('Recolor creates unsaved draft',await page.evaluate(()=>S.dirty));
  await page.locator('#undoDraft').click();await page.waitForFunction(()=>S.redo.length===1);
  await page.locator('#redoDraft').click();await page.waitForFunction(()=>S.undo.length===1);
  check('Draft undo and redo',await page.evaluate(()=>S.redo.length===0));
  await page.locator('#view').click({button:'right',position:{x:200,y:180}});
  check('Right-click uses asset menu',await page.locator('#contextMenu').isVisible());
  await page.locator('[data-action="fit"]').click();
  await page.locator('#saveButton').click();await idle();
  const first=await page.evaluate(()=>S.asset.revision);
  check('Save persists and stages the revision',first!=='original'&&await page.locator('#includeMod').isChecked());
  await page.locator('summary').filter({hasText:'Saved history'}).click();
  await page.locator('#undoSaved').click();await page.waitForFunction(()=>S.asset.revision==='original');
  await page.locator('#redoSaved').click();await page.waitForFunction(r=>S.asset.revision===r,first);
  check('Saved history reaches original and restores revision',true);
  await page.locator('#includeMod').check();
  await page.locator('#compare').check();await page.locator('#compare').uncheck();
  const download=page.waitForEvent('download');await page.locator('#exportButton').click();const dl=await download;check('PNG export creates a download',dl.suggestedFilename().endsWith('.png'));
  const recipeDownload=page.waitForEvent('download');await page.locator('#recipeButton').click();check('Recipe export creates a ZIP',(await recipeDownload).suggestedFilename().endsWith('.zip'));
  await page.locator('[data-view="atlas"]').click();await page.waitForFunction(()=>S.atlas!==null);
  check('Atlas mode exposes original sheet regions',await page.evaluate(()=>S.atlasInfo.children.length===4));
  await page.locator('#sheetButton').click();await page.waitForSelector('#clearParent',{state:'visible'});check('Sheet filter drills into sprites',await page.locator('#clearParent').isVisible());
  await page.locator('#clearParent').click();
  await select('bg2.png');await page.locator('[data-view="tile"]').click();check('Tile preview available',await page.evaluate(()=>S.mode==='tile'));
  await page.locator('[data-view="cell"]').click();check('Cell preview available',await page.evaluate(()=>S.mode==='cell'));
  await page.locator('[data-view="texture"]').click();
  await page.locator('#importFile').setInputFiles({name:'replacement.png',mimeType:'image/png',buffer:fs.readFileSync(path.join(state.game,'assets/bg2.png'))});
  await page.waitForFunction(()=>S.dirty);
  await page.locator('[data-tool="brush"]').click();
  const point=await page.evaluate(()=>({x:document.getElementById('view').getBoundingClientRect().left+transform.x+32*transform.scale,y:document.getElementById('view').getBoundingClientRect().top+transform.y+32*transform.scale}));
  await page.mouse.move(point.x,point.y);await page.mouse.down();await page.mouse.move(point.x+25,point.y+12,{steps:6});await page.mouse.up();
  check('Painting records a stroke',await page.evaluate(()=>S.ops.some(x=>x.tool==='brush')));
  await page.locator('#saveButton').click();await idle();
  check('Mod includes two edited assets',await page.evaluate(()=>S.status.staged.length===2));
  await page.locator('#reviewButton').click();await page.waitForSelector('#dialog[open]');check('Build review lists real target files',(await page.locator('#dialogDetails').textContent()).includes('assets/bg2.png'));await page.locator('#dialogAccept').click();
  await page.locator('#installButton').click();await idle();
  check('UI installs into fixture and offers restore',await page.evaluate(()=>S.status.active?.state==='installed'));
  check('Stacked install is disabled',await page.locator('#installButton').isDisabled());
  await page.locator('#restoreButton').click();await idle();
  check('UI restores original state',await page.evaluate(()=>S.status.active===null));
  check('Every fixture game file restored byte-for-byte',files.every(p=>hash(p)===before[p]));
  await page.setViewportSize({width:1280,height:800});await page.waitForTimeout(200);
  check('No horizontal overflow at 1280px',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(evidence,'fixture-editor.png'),fullPage:true});
  check('No browser JavaScript or resource errors',errors.length===0);
  fs.writeFileSync(path.join(evidence,'browser-tests.json'),JSON.stringify({passed:true,checks,errors},null,2));
  console.log(JSON.stringify({passed:true,count:checks.length,checks,errors},null,2));
 }catch(e){
  await page.screenshot({path:path.join(evidence,'browser-failure.png'),fullPage:true});
  console.error('Browser errors:',errors);
  throw e;
 }finally{
  // Recover fixture originals even when a UI assertion fails.
  const s=await(await fetch(url+'/api/status')).json();
  if(s.active)await fetch(url+'/api/restore',{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Token':s.token},body:'{}'});
  await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
