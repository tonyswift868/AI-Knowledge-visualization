import {reachRagTarget278,openMaterials278,closeMaterials278} from '../rag278/materials_navigation278.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=60)=>new Promise(resolve=>setTimeout(resolve,ms));
let target;
for(let i=0;i<300;i++){
  try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}
  await pause(100);
}
if(!target)throw Error('RAG 251 browser page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
let seq=0;const pending=new Map();
ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){const request=pending.get(message.id);pending.delete(message.id);message.error?request.reject(Error(JSON.stringify(message.error))):request.resolve(message.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const response=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(response.exceptionDetails)throw Error(JSON.stringify(response.exceptionDetails));return response.result.value}
async function until(expression,limit=400){for(let i=0;i<limit;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function rawClick(selector){const point=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);if(!point)throw Error('missing selector '+selector);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',clickCount:1});await pause(120)}
async function key(key,code,value){await cdp('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:value});await cdp('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:value});await pause(80)}
const result={ok:false,checks:[],screenshots:[],trace:null};
const check=(ok,label)=>{if(!ok)throw Error(label);result.checks.push(label)};
async function screenshot(theme,state){
  await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)};window.scrollTo(0,0);document.querySelector('[data-testid="rag-page"]')?.scrollIntoView({block:'start'})`);await pause(160);
  const shot=await cdp('Page.captureScreenshot',{format:'png'});
  const filename=`rag-${theme}-${state}-251.png`;fs.mkdirSync(path.join(root,'screenshots'),{recursive:true});
  fs.writeFileSync(path.join(root,'screenshots',filename),Buffer.from(shot.data,'base64'));result.screenshots.push(filename);
}
async function trace(){return await js(`window.call251('test.trace').then(x=>x)`)}
try{
  await until(`!!document.querySelector('[data-testid="rag-page"]')&&!!window.call251`);
  await until(`!!document.querySelector('[data-testid="rag-session-list"]')`);
  check(await js(`!!document.querySelector('[data-testid="rag-sidebar"].rag-sidebar-sessions')&&!document.querySelector('[data-testid="rag-sidebar"] .rag-sources')&&!document.querySelector('[data-testid="rag-sidebar"] .rag-current-documents')`),'the sidebar is dedicated to sessions while materials live in their own window');
  check(await js(`document.querySelector('.rag-session-list-empty')?.textContent.includes('还没有会话')`),'empty account displays an empty session-list state');
  for(const theme of ['sakura','aurora'])await screenshot(theme,'empty');

  // Asking from the initial no-session state with unready sources must create
  // a named session and automatically prepare those sources. Hold the fake
  // upload so the progress/cancel state can be inspected deterministically.
  await js(`window.call251('test.ingest_mode',{mode:'block'}).then(x=>x)`);
  await click('[data-testid="rag-add-document-doc-a"]');
  await closeMaterials278(js,rawClick);
  await js(`(()=>{const e=document.querySelector('[data-testid="rag-question"]');const d=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;d.call(e,'请解释这段资料');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
  await click('[data-testid="rag-send"]');
  await until(`!!document.querySelector('[data-testid="rag-empty-session"]')`);
  await until(`document.querySelector('[data-testid="rag-empty-session"]')?.innerText.includes('新会话')`);
  check(await js(`!!document.querySelector('[data-testid="rag-session-list"] [data-active="true"]')`),'newly created session appears selected in the vertical session list');
  check(await js(`document.querySelector('.rag-chat-head')?.innerText.includes('1 份资料')`),'new session heading communicates selected source count');
  check(await js(`document.body.innerText.includes('下一步')||document.body.innerText.includes('提问')`),'new session explains the next step');
  await until(`document.querySelector('[data-testid="rag-prepare-status"]')?.innerText.includes('正在准备资料')`);
  check(await js(`/3\\s*\\/\\s*10/.test(document.querySelector('[data-testid="rag-prepare-status"]')?.innerText||'')`),'preparation status reports the actual uploaded and total chunk counts');
  await until(`window.call251('test.trace').then(x=>x.ingestStarted>0)`);
  check(await js(`!!document.querySelector('[data-testid="rag-prepare-status"]')`),'new-session creation automatically begins preparing unready selected documents');
  check(await js(`document.querySelector('[data-testid="rag-send"]')?.disabled`),'question sending stays disabled while preparation is in progress');
  await openMaterials278(js,rawClick);
  check(await js(`document.body.innerText.includes('原文件不会上传')||document.body.innerText.includes('只上传抽取后的文字')`),'preparation privacy copy explains that only extracted text is uploaded');
  await closeMaterials278(js,rawClick);
  for(const theme of ['sakura','aurora'])await screenshot(theme,'preparing');

  await click('[data-testid="rag-cancel-preparation"]');
  await until(`window.call251('test.trace').then(x=>x.ingestCancelled>0)`);
  check(await js(`!!document.querySelector('[data-testid="rag-session-list"] [data-active="true"]')`),'cancelling preparation keeps the newly created conversation selected');
  check(await js(`!document.querySelector('[data-testid="rag-prepare-status"]')&&!!document.querySelector('[data-testid="rag-prepare-resume"]')`),'cancelled preparation clears the spinner and leaves a continue-preparing action');
  check((await trace()).send===0,'cancelling preparation does not send an old or draft question');

  await js(`window.call251('test.ingest_mode',{mode:'success'}).then(x=>x)`);
  await click('[data-testid="rag-send"]');
  await openMaterials278(js,rawClick);
  await until(`document.querySelector('[data-testid="rag-library-document-doc-a"]')?.innerText.includes('已就绪')`);
  await closeMaterials278(js,rawClick);
  await until(`document.body.innerText.includes('这是本机替身的资料回答')`);
  check((await trace()).send===1,'after cancellation the learner can retry, preparation completes, and the preserved question is sent once');

  // Add a stored turn so the explicit source-edit action can prove it keeps
  // the same conversation and history.
  const created=await trace();
  const originalSessionId=created.sessions.find(item=>item.id.startsWith('new-session-'))?.id;
  if(!originalSessionId)throw Error('fake bridge did not return created session id');
  // Confirm the explicit New Session control creates a separate visible row.
  await click('[data-testid="rag-new-session"]');
  await until(`document.querySelector('[data-testid="rag-session-list"] [data-active="true"]')?.textContent.includes('新会话')`);
  await until(`!!document.querySelector('[data-testid="rag-empty-session"]')`);
  for(const theme of ['sakura','aurora'])await screenshot(theme,'new');
  const newId=(await trace()).sessions.find(item=>item.id!==originalSessionId)?.id;
  if(!newId)throw Error('second session did not appear in the sidebar');
  await click(`[data-testid="rag-session-${originalSessionId}"]`);
  await until(`document.body.innerText.includes('这是本机替身的资料回答')`);
  const createdRow=await trace();
  check(createdRow.send===1,'opening the just-created conversation preserves its recorded answer');
  check(await js(`document.body.innerText.includes('请解释这段资料')`),'the automatically prepared initial question and answer are rendered as a conversation');
  for(const theme of ['sakura','aurora'])await screenshot(theme,'conversation');

  await js(`window.call251('test.ingest_mode',{mode:'fail'}).then(x=>x)`);
  await click('[data-testid="rag-add-document-doc-c"]');
  await until(`document.querySelector('[data-testid="rag-library-document-doc-c"]')?.innerText.includes('准备失败')`);
  check(await js(`document.querySelector('[data-testid="rag-library-document-doc-c"]')?.innerText.includes('本机替身模拟的网络错误')`),'the affected document card shows the specific preparation failure reason');
  check(await js(`!!document.querySelector('[data-testid="rag-document-continue-doc-c"]')`),'the failed background source has a continue-preparing action');
  check(await js(`document.body.innerText.includes('这是本机替身的资料回答')`),'changing a conversation source preserves its existing answer');
  await js(`window.call251('test.ingest_mode',{mode:'success'}).then(x=>x)`);
  await click('[data-testid="rag-document-continue-doc-c"]');
  await until(`document.querySelector('[data-testid="rag-library-document-doc-c"]')?.innerText.includes('已就绪')`);
  check((await trace()).send===1,'source preparation retry does not re-send or duplicate the previous question');
  check((await trace()).lastUpdate?.session_id===originalSessionId,'source edits leave the current conversation ID unchanged');

  await js(`window.call251('test.seed_legacy').then(x=>x)`);
  await until(`!!document.querySelector('[data-testid="rag-session-legacy-session"]')`);
  await click('[data-testid="rag-session-legacy-session"]');
  await until(`document.body.innerText.includes('旧会话里的问题')`);
  check(await js(`!!document.querySelector('[data-testid="rag-session-remove-document-doc-b"]')`),'opening an existing conversation reflects its selected document');
  check(await js(`document.body.innerText.includes('旧会话的回答')`),'legacy conversation messages remain readable');
  await click('[data-testid="rag-citation-1"]');
  await until(`!!document.querySelector('[data-testid="rag-source-1"]')`);
  check(await js(`document.querySelector('[data-testid="rag-source-1"]')?.innerText.includes('旧会话保留的原文引用')`),'an old session keeps its reference passage openable');

  // Switching between an existing and newly created conversation updates the
  // selection to that session's own source set.
  await click(`[data-testid="rag-session-${originalSessionId}"]`);
  await until(`!!document.querySelector('[data-testid="rag-session-remove-document-doc-a"]')`);
  check(await js(`!!document.querySelector('[data-testid="rag-session-remove-document-doc-c"]')`),'switching back restores the edited session source set');
  await click('[data-testid="rag-session-legacy-session"]');
  await until(`!!document.querySelector('[data-testid="rag-session-remove-document-doc-b"]')`);
  check(await js(`!document.querySelector('[data-testid="rag-session-remove-document-doc-a"]')`),'switching sessions updates selected sources to the chosen conversation');

  // The 249 picker and voice control remain available in the reorganized page.
  await until(`!!document.querySelector('[data-testid="rag-plan-select"]')`);
  check(await js(`!!document.querySelector('[data-testid="rag-question-mic"] .mf-voice-mic')`),'249 voice input button remains available in the reorganized page');
  check(await js(`!document.querySelector('[data-testid="rag-plan-select"]')?.disabled`),'library plan filter remains available while session membership is managed separately');

  // Exercise the backend race path where the UI thought the selected source
  // was ready but rag.send reports that indexing is incomplete. Progress and
  // the persistent failure state must be surfaced; cancellation must never
  // create an answer or invoke the model.
  await click('[data-testid="rag-new-session"]');
  await until(`!!document.querySelector('[data-testid="rag-empty-session"]')`);
  await js(`window.call251('test.send_mode',{mode:'not_ready_fail'}).then(x=>x)`);
  await js(`(()=>{const e=document.querySelector('[data-testid="rag-question"]');const d=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;d.call(e,'服务端准备竞态失败问题');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
  await click('[data-testid="rag-send"]');
  await until(`!!document.querySelector('[data-testid="rag-prepare-status"]')`);
  check(await js(`/2\\s*\\/\\s*3/.test(document.querySelector('[data-testid="rag-prepare-status"]')?.innerText||'')`),'rag.send progress is surfaced with uploaded/total chunks while the server checks readiness');
  await until(`!!document.querySelector('[data-testid="rag-prepare-failure"]')`);
  check(await js(`document.querySelector('[data-testid="rag-prepare-failure"]')?.innerText.includes('资料尚未准备')`),'rag.send readiness failure becomes an actionable preparation failure');
  check((await trace()).modelInvocations===1,'a not-ready send failure does not reach the model');
  await click(`[data-testid="rag-session-${originalSessionId}"]`);
  await until(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('第二份待准备资料.md')`);
  await js(`window.call251('test.send_mode',{mode:'not_ready_block'}).then(x=>x)`);
  await js(`(()=>{const e=document.querySelector('[data-testid="rag-question"]');const d=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;d.call(e,'取消服务端准备的问题');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
  const userTurnsBeforeCancel=await js(`document.querySelectorAll('[data-testid^="rag-message-user-"]').length`);
  await click('[data-testid="rag-send"]');
  await until(`!!document.querySelector('[data-testid="rag-prepare-status"]')`);
  check(await js(`!!document.querySelector('[data-testid="rag-cancel"]')`),'the in-flight backend send can be cancelled');
  await click('[data-testid="rag-cancel"]');
  await until(`window.call251('test.trace').then(x=>x.sendCancelled===1)`);
  check(await js(`document.querySelector('[data-testid="rag-question"]')?.value==='取消服务端准备的问题'`),'cancelling backend preparation retains the draft question');
  check(await js(`document.querySelectorAll('[data-testid^="rag-message-user-"]').length===${userTurnsBeforeCancel}`),'cancelled backend preparation adds no user/assistant turn');
  check((await trace()).modelInvocations===1,'cancelling backend preparation does not invoke the model');

  await until(`document.querySelector('[data-testid="rag-library-document-doc-d"]')?.innerText.includes('尚未准备')`);
  await js(`window.call251('test.ingest_mode',{mode:'block'}).then(x=>x)`);
  await click('[data-testid="rag-session-remove-document-doc-a"]');
  await click('[data-testid="rag-add-document-doc-d"]');
  await closeMaterials278(js,rawClick);
  await until(`document.querySelector('[data-testid="rag-prepare-status"]')?.innerText.includes('正在准备')`);
  await js(`window.call251('test.scope_changed').then(x=>x)`);
  await until(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('新会话要用的资料')&&!document.querySelector('[data-testid="rag-prepare-status"]')`);
  await until(`window.call251('test.trace').then(x=>x.ingestCancelled>=2)`);
  check(await js(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('新会话要用的资料')`),'changing account scope clears the previous conversation and preparation state');
  check((await trace()).ingestCancelled>=2,'account scope change cancels in-flight preparation');

  result.trace=await trace();result.ok=true;
}catch(error){
  result.error=String(error.stack||error);
  result.dom=await js('document.body.innerText').catch(()=>''),
  result.ids=await js(`Array.from(document.querySelectorAll('[data-testid]')).map(e=>e.dataset.testid)`).catch(()=>[]);
  result.trace=await trace().catch(()=>null);
}finally{
  fs.writeFileSync(path.join(root,'result251.json'),JSON.stringify(result,null,2));ws.close();
}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;

async function click(selector){await reachRagTarget278(js,rawClick,selector);await rawClick(selector)}
