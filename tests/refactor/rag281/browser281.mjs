import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=80)=>new Promise(resolve=>setTimeout(resolve,ms));
let target;
for(let i=0;i<300;i++){
  try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}
  await pause(100);
}
if(!target)throw Error('RAG 281 browser page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
let seq=0;const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression,limit=500){for(let i=0;i<limit;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){const p=JSON.parse(await js(`JSON.stringify((()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})())`));if(!p)throw Error('missing/disabled '+selector);await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x:p.x,y:p.y});await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x:p.x,y:p.y,button:'left',buttons:1,clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:p.x,y:p.y,button:'left',buttons:0,clickCount:1});await pause(120)}
async function wheel(x,y,deltaY){await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x,y});await cdp('Input.dispatchMouseEvent',{type:'mouseWheel',x,y,deltaX:0,deltaY});await pause(120)}
async function choosePlan(id){await click('[data-testid="rag-plan-select"]');await until(`!!document.querySelector('[role="listbox"]')`);await click(`[data-testid="rag-plan-option-${id}"]`);await until(`!document.querySelector('[role="listbox"]')`)}
async function screenshot(theme){await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)};window.scrollTo({top:0,left:0,behavior:'instant'})`);await pause(150);const shot=await cdp('Page.captureScreenshot',{format:'png'});const file=path.join(root,'screenshots',`rag281-${theme}-materials-wide.png`);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,Buffer.from(shot.data,'base64'));return file}
const result={ok:false,checks:[],screenshots:[]};
const check=(ok,label)=>{if(!ok)throw Error(label);result.checks.push(label)};
async function filterLabel(){return await js(`document.querySelector('[data-testid="rag-plan-select"] .select-menu-label')?.textContent||''`)}
async function upload(expectedId){await click('[data-testid="rag-pick-files"]');await until(`window.call281('test.trace').then(x=>x.pick_started>=${expectedId==='doc-uploaded'?1:2})`);await js(`window.call281('test.release_import').then(x=>x)`);await until(`window.call281('test.trace').then(x=>x.updates.some(u=>u.session_id==='session-a'&&u.document_ids.includes('${expectedId}')))`) }
let stage='mount';
try{
  await until(`!!window.ready281&&!!document.querySelector('[data-testid="rag-page"]')`);
  await until(`!!document.querySelector('[data-testid="rag-session-list"]')`);
  await js(`window.call281('test.seed_history').then(x=>x)`);
  await js(`window.navigate281('home')`);await until(`!!document.querySelector('[data-testid="rag281-away"]')`);
  await js(`window.navigate281('rag')`);await until(`!!document.querySelector('[data-testid="rag-session-session-a"]')`);
  await click('[data-testid="rag-session-session-a"]');await until(`document.querySelector('[data-testid="rag-session-session-a"]')?.getAttribute('data-active')==='true'`);
  await cdp('Emulation.setDeviceMetricsOverride',{width:1764,height:1052,deviceScaleFactor:1,mobile:false});

  stage='all-filter';
  await click('[data-testid="rag-materials-open"]');await until(`document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  check(await filterLabel()==='全部资料','the materials filter starts at all materials');
  await click('[data-testid="rag-add-document-doc-other"]');
  await until(`window.call281('test.trace').then(x=>x.updates.at(-1)?.document_ids.includes('doc-other'))`);
  await js(`(()=>{const e=document.querySelector('[data-testid="rag-session-documents"] .rag-selected');e.scrollTop=e.scrollHeight})()`);
  await lightScreenshots();
  await js(`(()=>{document.querySelector('[data-testid="rag-session-documents"] .rag-selected').scrollTop=0})()`);
  await click('[data-testid="rag-session-remove-document-doc-other"]');
  await until(`!document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('第二份长标题资料')`);
  await click('[data-testid="rag-add-document-doc-other"]');
  await until(`window.call281('test.trace').then(x=>x.updates.at(-1)?.document_ids.includes('doc-other'))`);
  check(await filterLabel()==='全部资料','adding a document preserves the all-materials filter');
  await click('[data-testid="rag-session-remove-document-doc-other"]');
  await until(`!document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('第二份长标题资料')`);
  check(await filterLabel()==='全部资料','removing a document preserves the all-materials filter');
  await click('[data-testid="rag-document-delete-doc-upload"]');await until(`document.querySelector('[data-testid="rag-delete-dialog"]')?.open`);
  await click('[data-testid="rag-delete-confirm"]');await until(`!document.querySelector('[data-testid="rag-library-document-doc-upload"]')`);
  check(await filterLabel()==='全部资料','deleting a document preserves the all-materials filter');
  await upload('doc-uploaded');
  check(await filterLabel()==='全部资料','uploading preserves the all-materials filter');
  await click('[data-testid="rag-materials-close"]');await until(`!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  await click('[data-testid="rag-materials-open"]');await until(`document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  check(await filterLabel()==='全部资料','closing and reopening preserves the all-materials filter');
  await click('[data-testid="rag-materials-close"]');await until(`!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  await click('[data-testid="rag-session-session-b"]');await until(`document.querySelector('[data-testid="rag-session-session-b"]')?.getAttribute('data-active')==='true'`);
  await click('[data-testid="rag-materials-open"]');await until(`document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  check(await filterLabel()==='全部资料','switching conversations preserves the all-materials filter');
  await click('[data-testid="rag-materials-close"]');await until(`!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  await click('[data-testid="rag-session-session-a"]');await until(`document.querySelector('[data-testid="rag-session-session-a"]')?.getAttribute('data-active')==='true'`);

  stage='plan-filter';
  await click('[data-testid="rag-materials-open"]');await until(`document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  await choosePlan('plan-b');
  check(await filterLabel()==='另一个计划','selecting a plan changes the visible filter');
  await click('[data-testid="rag-add-document-doc-other"]');
  await until(`window.call281('test.trace').then(x=>x.updates.at(-1)?.document_ids.includes('doc-other'))`);
  check(await filterLabel()==='另一个计划','adding a document preserves the selected-plan filter');
  await click('[data-testid="rag-session-remove-document-doc-other"]');
  await until(`!document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('第二份长标题资料')`);
  check(await filterLabel()==='另一个计划','removing a document preserves the selected-plan filter');
  await click('[data-testid="rag-add-document-doc-other"]');
  await until(`window.call281('test.trace').then(x=>x.updates.at(-1)?.document_ids.includes('doc-other'))`);
  await upload('doc-uploaded-2');
  check(await filterLabel()==='另一个计划','uploading preserves the selected-plan filter');
  await click('[data-testid="rag-materials-close"]');await until(`!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  await click('[data-testid="rag-materials-open"]');await until(`document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  check(await filterLabel()==='另一个计划','closing and reopening preserves the selected-plan filter');
  await click('[data-testid="rag-materials-close"]');await until(`!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  await click('[data-testid="rag-session-session-b"]');await until(`document.querySelector('[data-testid="rag-session-session-b"]')?.getAttribute('data-active')==='true'`);
  await click('[data-testid="rag-materials-open"]');await until(`document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  check(await filterLabel()==='另一个计划','switching conversations preserves the selected-plan filter');
  await click('[data-testid="rag-materials-close"]');await until(`!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);
  await click('[data-testid="rag-session-session-a"]');await until(`document.querySelector('[data-testid="rag-session-session-a"]')?.getAttribute('data-active')==='true'`);
  await click('[data-testid="rag-materials-open"]');await until(`document.querySelector('[data-testid="rag-materials-dialog"]')?.open`);

  stage='layout-wheel';
  check(await js(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('第二份长标题资料')`),'the long material remains attached in the actual right panel for wheel checks');
  check(await js(`(()=>{const rows=[...document.querySelectorAll('[data-testid="rag-session-documents"] .rag-chip')];return rows.length>0&&rows.every(row=>{const x=row.querySelector('button'),name=row.querySelector('span');return !!x&&!!name&&x.getBoundingClientRect().left<name.getBoundingClientRect().left})})()`),'every selected-material row places × before its title');
  const horizontal=await js(`(()=>{const d=document.querySelector('[data-testid="rag-materials-dialog"]'),p=d.querySelector('[data-testid="rag-session-documents"]'),e=[...p.querySelectorAll('*')].filter(e=>['auto','scroll'].includes(getComputedStyle(e).overflowX)&&e.scrollWidth>e.clientWidth+2).sort((a,b)=>(b.scrollWidth-b.clientWidth)-(a.scrollWidth-a.clientWidth))[0];if(!e)return null;e.dataset.rag281ScrollTarget='true';const r=e.getBoundingClientRect(),pr=p.getBoundingClientRect();return{x:r.left+r.width/2,y:r.bottom-12,max:e.scrollWidth-e.clientWidth,inRightPanel:p.contains(e),inSelectedList:!!e.closest('.rag-selected'),visible:r.bottom>pr.top&&r.top<pr.bottom&&r.right>pr.left&&r.left<pr.right}})()`);
  check(horizontal&&horizontal.max>0&&horizontal.inRightPanel&&horizontal.inSelectedList&&horizontal.visible,'the long material name creates visible horizontal overflow in the actual right-hand selected-material list');
  const scrollTarget='[data-rag281-scroll-target="true"]';
  await js(`(()=>{const e=document.querySelector(${JSON.stringify(scrollTarget)});e.scrollLeft=e.scrollWidth-e.clientWidth})()`);
  const right=await js(`(()=>{const e=document.querySelector(${JSON.stringify(scrollTarget)}),r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.bottom-12,left:e.scrollLeft,top:e.scrollTop}})()`);
  await wheel(right.x,right.y,360);
  check(await js(`(()=>{const e=document.querySelector(${JSON.stringify(scrollTarget)});return e.scrollLeft===${right.left}&&e.scrollTop===${right.top}})()`),'wheel-down at the horizontal right edge does not spill into vertical scrolling');
  await wheel(right.x,right.y,-360);
  check(await js(`document.querySelector(${JSON.stringify(scrollTarget)})?.scrollLeft<${right.left}`),'wheel-up in the bottom scrollbar band moves left');
  await js(`(()=>{document.querySelector(${JSON.stringify(scrollTarget)}).scrollLeft=0})()`);
  const left=await js(`(()=>{const e=document.querySelector(${JSON.stringify(scrollTarget)}),r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.bottom-12,left:e.scrollLeft,top:e.scrollTop}})()`);
  await wheel(left.x,left.y,-360);
  check(await js(`(()=>{const e=document.querySelector(${JSON.stringify(scrollTarget)});return e.scrollLeft===${left.left}&&e.scrollTop===${left.top}})()`),'wheel-up at the horizontal left edge does not spill into vertical scrolling');
  await wheel(left.x,left.y,360);
  check(await js(`document.querySelector(${JSON.stringify(scrollTarget)})?.scrollLeft>${left.left}`),'wheel-down in the bottom scrollbar band moves right');
  await choosePlan('empty');
  const title=await js(`(()=>{const e=document.querySelector('[data-testid="rag-library"] .rag-document-list'),b=e.querySelector('.rag-document-info b');e.scrollTop=0;const r=b.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2,top:e.scrollTop}})()`);
  result.verticalBefore=await js(`(()=>{const e=document.querySelector('[data-testid="rag-library"] .rag-document-list');document.addEventListener('wheel',event=>{window.wheel281={target:event.target instanceof Element?event.target.outerHTML.slice(0,220):String(event.target),x:event.clientX,y:event.clientY,defaultPrevented:event.defaultPrevented}},{once:true});return{height:e.clientHeight,scrollHeight:e.scrollHeight,rect:e.getBoundingClientRect().toJSON(),hit:document.elementFromPoint(${title.x},${title.y})?.outerHTML.slice(0,220),point:${JSON.stringify(title)}}})()`);
  await wheel(title.x,title.y,360);
  for(let i=0;i<25;i++){if(await js(`document.querySelector('[data-testid="rag-library"] .rag-document-list')?.scrollTop>${title.top}`))break;await pause(80)}
  result.verticalAfter=await js(`(()=>{const e=document.querySelector('[data-testid="rag-library"] .rag-document-list');return{top:e.scrollTop,left:e.scrollLeft,wheel:window.wheel281}})()`);
  check(await js(`document.querySelector('[data-testid="rag-library"] .rag-document-list')?.scrollTop>${title.top}`),'wheel over an ellipsis title keeps normal vertical scrolling');

  stage='empty-plan';
  await choosePlan('plan-b');
  await click('[data-testid="rag-document-delete-doc-extra-22"]');await until(`document.querySelector('[data-testid="rag-delete-dialog"]')?.open`);
  await click('[data-testid="rag-delete-confirm"]');await until(`!document.querySelector('[data-testid="rag-library-document-doc-extra-22"]')`);
  check(await filterLabel()==='另一个计划','deleting one source leaves the still-valid selected plan filter in place');
  await click('[data-testid="rag-document-delete-doc-other"]');await until(`document.querySelector('[data-testid="rag-delete-dialog"]')?.open`);
  await click('[data-testid="rag-delete-confirm"]');await until(`!document.querySelector('[data-testid="rag-library-document-doc-other"]')`);
  await until(`document.querySelector('[data-testid="rag-plan-select"] .select-menu-label')?.textContent==='全部资料'`);
  check(true,'deleting the selected plan’s last source returns the filter to all materials');
  result.trace=await js(`window.call281('test.trace').then(x=>x)`);
  result.ok=true;
}catch(error){
  result.stage=stage;result.error=String(error.stack||error);
  result.dom=await js('document.body.innerText').catch(()=>'' );
  result.ids=await js(`Array.from(document.querySelectorAll('[data-testid]')).map(x=>x.dataset.testid)`).catch(()=>[]);
  result.trace=await js(`window.call281('test.trace').then(x=>x)`).catch(()=>null);
}finally{
  fs.writeFileSync(path.join(root,'result281.json'),JSON.stringify(result,null,2));ws.close();
}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;

async function lightScreenshots(){
  for(const theme of ['sakura','aurora'])result.screenshots.push(await screenshot(theme));
}
