import {click253} from './click253.mjs';
import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=40)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<240;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned file page not found');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let seq=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(p?.method+': '+JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject,method});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){try{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}catch(e){throw Error(String(e)+' expression='+expression)}}
async function until(expression,limit=300){for(let i=0;i<limit;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){
 await click253(js,(type,x,y)=>call('Input.dispatchMouseEvent',{type,x,y,button:'left',clickCount:1}),selector,{
   onSample:state=>fs.appendFileSync(path.join(root,'click-diagnostics155.jsonl'),JSON.stringify({selector,...state})+'\n'),
 });
 await pause(80);
}
async function key(key,code,value){await call('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:value});await call('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:value});await pause(50)}
async function select(selector,value){
 await click(selector);await until(`!!document.querySelector('[role="listbox"]')`);
 const wanted=value?`rag-plan-option-${value}`:'rag-plan-option-empty';let found=false;
 for(let i=0;i<8;i++){
  const state=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),id=e?.getAttribute('aria-activedescendant'),items=[...document.querySelectorAll('[role="listbox"] [role="option"]')];return{active:items.findIndex(x=>x.id===id),target:items.findIndex(x=>x.dataset.testid===${JSON.stringify(wanted)})}})()`);
  if(state.active===state.target){found=true;break}
  await key(state.active<state.target?'ArrowDown':'ArrowUp',state.active<state.target?'ArrowDown':'ArrowUp',state.active<state.target?40:38);
 }
 if(!found)throw Error('custom plan option not keyboard reachable: '+wanted);
 await key('Enter','Enter',13);await until(`!document.querySelector('[role="listbox"]')`);
}
async function input(selector,value){await js(`document.querySelector(${JSON.stringify(selector)})?.focus()`);await call('Input.insertText',{text:value});}
const checks=[];const check=(yes,label)=>{if(!yes)throw Error(label);checks.push(label)};let result,stage='start';
try{
 stage='nav and sources';
 await until(`!!document.querySelector('[data-testid="rag-page"]')`);
 check(await js(`!!document.querySelector('[data-testid="nav-rag"]')`),'資料問答 is a first-class left navigation entry');
 // Path 1: choose one plan, selecting all its documents.
 stage='plan selection';await select('[data-testid="rag-plan-select"]','plan-a');
 await click('[data-testid="rag-plan-select-all"]');
 await until(`!!document.querySelector('[data-testid="rag-session-remove-document-doc-a"]')&&!!document.querySelector('[data-testid="rag-session-remove-document-doc-b"]')`);
 // Adding a plan's complete set is idempotent; membership changes through the
 // per-session remove chip, not by toggling the bulk-add action.
 await click('[data-testid="rag-plan-select-all"]');
 await until(`!!document.querySelector('[data-testid="rag-session-remove-document-doc-a"]')&&!!document.querySelector('[data-testid="rag-session-remove-document-doc-b"]')`);
 await click('[data-testid="rag-session-remove-document-doc-b"]');
 check(await js(`!document.querySelector('[data-testid="rag-session-remove-document-doc-b"]')`),'selected document can be removed immediately from the session');
 // Path 2: independently select sources from different plans.
 stage='cross-plan selection';await select('[data-testid="rag-plan-select"]','');
 await click('[data-testid="rag-add-document-doc-c"]');
 await until(`!!document.querySelector('[data-testid="rag-session-remove-document-doc-a"]')&&!!document.querySelector('[data-testid="rag-session-remove-document-doc-c"]')`);
 // Path 3: upload a local text file through the page's picker/import workflow.
 stage='file import';await click('[data-testid="rag-pick-files"]');
 await until(`!!document.querySelector('[data-testid="rag-library-document-upload-155"]')`);
 check(await js(`document.querySelector('[data-testid="rag-library-document-upload-155"]')?.innerText.includes('本地上传')&&!!document.querySelector('[data-testid="rag-session-remove-document-upload-155"]')`),'new text file is imported as an unplanned source and joins the draft selection');
 // Preparing is now automatic when a session is created; it replaces the old
 // explicit “ingest” action while retaining progress and retry behavior.
 await until(`!document.querySelector('[data-testid="rag-add-document-doc-b"]')?.disabled`);
 await click('[data-testid="rag-add-document-doc-b"]');
 stage='automatic preparation';await click('[data-testid="rag-new-session"]');
 await until(`!!document.querySelector('[data-testid="rag-prepare-status"]')`);
 check(await js(`/\\d+\\s*\\/\\s*\\d+/.test(document.querySelector('[data-testid="rag-prepare-status"]')?.innerText||'')`),'automatic preparation shows uploaded and total chunk counts');
 fs.writeFileSync(path.join(root,'release-ingest155'),'');
 await until(`document.querySelector('[data-testid="rag-library-document-upload-155"]')?.innerText.includes('准备失败')`);
 check(await js(`document.querySelector('[data-testid="rag-library-document-doc-b"]')?.innerText.includes('已就绪')`),'partial ingest failure is visible while successful sources stay ready');
 await click('[data-testid="rag-retry-preparation"]');
 await until(`document.querySelector('[data-testid="rag-library-document-upload-155"]')?.innerText.includes('已就绪')`);
 check(await js(`document.querySelector('[data-testid="rag-library-document-doc-b"]')?.innerText.includes('已就绪')`),'retry completes the failed source without losing the successful one');
 // Create a session for a selected document set, then exercise streaming answer and citations.
 stage='use prepared session';
 await until(`!document.querySelector('[data-testid="rag-question"]')?.disabled`);
 await input('[data-testid="rag-question"]','向量检索如何工作？');
 await until(`!document.querySelector('[data-testid="rag-send"]')?.disabled&&document.querySelector('[data-testid="rag-question"]')?.value==='向量检索如何工作？'`);
 await until(`!document.querySelector('.toasts .toast')`,700);
 stage='model fault and retry';fs.writeFileSync(path.join(root,'fail-next-send155'),'');await click('[data-testid="rag-send"]');
 await until(`!!document.querySelector('[data-testid="rag-send-failure"]')`);
 check(await js(`document.querySelector('[data-testid="rag-send-failure"]')?.innerText.includes('连不上模型服务')`),'model connection fault is shown beside the failed question');
 check(await js(`document.querySelector('[data-testid="rag-send-failure"]')?.innerText.includes('没有自动重发')&&!!document.querySelector('[data-testid="rag-retry-send"]')`),'failed question provides an explicit retry action without automatic resending');
 await click('[data-testid="rag-retry-send"]');
 stage='send question';
 await until(`!!document.querySelector('[data-testid="rag-streaming-reply"]')`);
 check(await js(`document.querySelector('[data-testid="rag-streaming-reply"]')?.textContent?.length>0`),'teacher answer appears incrementally while streaming');
 fs.writeFileSync(path.join(root,'release-stream155'),'');
 await until(`!document.querySelector('[data-testid="rag-send-failure"]')&&!document.querySelector('[data-testid="rag-model-fault"]')`);
 check(await js(`![...document.querySelectorAll('[role="alert"]')].some(e=>e.innerText.includes('连不上模型服务'))`),'successful retry model_status clears the model fault');
 await until(`!!document.querySelector('[data-testid="rag-citation-1"]')`);
 stage='citation';await click('[data-testid="rag-citation-1"]');
 await until(`!!document.querySelector('[data-testid="rag-source-1"]')`);
 check(await js(`document.querySelector('[data-testid="rag-source-1"]')?.innerText.includes('第 4 页')||document.querySelector('[data-testid="rag-source-1"]')?.innerText.includes('第4页')||document.querySelector('[data-testid="rag-source-1"]')?.innerText.includes('向量检索会把文字转换')`),'citation opens its original passage and location');
 check(await js(`document.body.innerText.includes('资料外补充：')`),'supplementary knowledge is clearly labelled');
 // Copy preserves Markdown source, not rendered DOM text.
 stage='copy';await js(`(()=>{window.copy155='';navigator.permissions.query=async()=>({state:'granted'});Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copy155=text}}})})()`);
 await click('[data-testid^="rag-copy-"]');await until(`document.body.innerText.includes('已复制')`);
 const copied=await js('window.copy155');
 check(copied.includes('[1]')&&copied.includes('资料外补充：'),'copy button preserves exact answer Markdown source');
 check(await js(`document.querySelectorAll('.rag-session').length>=1`),'created session appears in its session list');
 // Capture narrow screens in both themes and check horizontal overflow.
 await until(`!document.querySelector('.toasts .toast')`,700);
 await click('[aria-label="收起侧栏"]');
 check(await js(`document.querySelector('aside.sidebar')?.classList.contains('collapsed')`),'sidebar can be collapsed for narrow-window use');
 const sizes=[];
 for(const theme of ['sakura','aurora']){
   await call('Emulation.setDeviceMetricsOverride',{width:600,height:850,deviceScaleFactor:1,mobile:false});
   await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)};document.querySelector('.rag-page')?.scrollIntoView({block:'start'})`);await pause(250);
   const geom=await js(`(()=>{const selected=document.querySelector('.rag-selected')?.getBoundingClientRect(),upload=document.querySelector('.rag-upload-row')?.getBoundingClientRect(),list=document.querySelector('.rag-document-list'),item=list?.querySelector('.rag-document-row'),page=document.querySelector('.rag-page')?.getBoundingClientRect();return{viewport:innerWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth,selectedTop:selected?.top,uploadBottom:upload?.bottom,listHeight:list?.clientHeight,firstDocumentHeight:item?.getBoundingClientRect().height,pageTop:page?.top,topbarBottom:document.querySelector('.topbar')?.getBoundingClientRect().bottom}})()`);
   check(geom.selectedTop>=geom.uploadBottom-1,theme+' selected source tags remain separated from the upload action');
   check(geom.listHeight>=geom.firstDocumentHeight,theme+' document list can fully show at least one source');
   check(geom.pageTop>=geom.topbarBottom-1,theme+' page title remains below the top bar');
   check(geom.document<=geom.viewport+1&&geom.body<=geom.viewport+1,theme+' narrow layout has no horizontal overflow');sizes.push({theme,...geom});
   const shot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,`${theme}155.png`),Buffer.from(shot.data,'base64'));
 }
 await click('[aria-label="删除会话：计算机基础问答"]');
 await until(`document.querySelectorAll('.rag-session').length===0`);
 checks.push('session can be deleted');
 result={ok:true,checks,sizes,copied};
}catch(e){result={ok:false,stage,checks,error:String(e.stack||e),body:await js('document.body.innerText').catch(()=>''),selectors:await js(`Array.from(document.querySelectorAll('[data-testid]')).map(e=>e.dataset.testid).slice(0,100)`).catch(()=>[]),question:await js(`({value:document.querySelector('[data-testid="rag-question"]')?.value,disabled:document.querySelector('[data-testid="rag-question"]')?.disabled,sendDisabled:document.querySelector('[data-testid="rag-send"]')?.disabled,send:document.querySelector('[data-testid="rag-send"]')?.outerHTML,active:document.activeElement?.getAttribute('data-testid'),pending:!!document.querySelector('[data-testid="rag-streaming-reply"]'),alerts:[...document.querySelectorAll('[role="alert"]')].map(x=>x.innerText)})`).catch(()=>null),trace:fs.existsSync(path.join(root,'bridge-trace155.txt'))?fs.readFileSync(path.join(root,'bridge-trace155.txt'),'utf8'):''} }
finally{fs.writeFileSync(path.join(root,'result155.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
