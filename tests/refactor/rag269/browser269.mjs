import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(process.argv[2]), endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=80)=>new Promise(resolve=>setTimeout(resolve,ms));
let target;
for(let i=0;i<300;i++){
  try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}
  await pause(100);
}
if(!target)throw Error('RAG 269 browser page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
let seq=0;const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression,limit=500){for(let i=0;i<limit;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){const point=JSON.parse(await js(`JSON.stringify((()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})())`));if(!point)throw Error('missing/disabled selector '+selector);await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x,y:point.y});await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',buttons:1,clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',buttons:0,clickCount:1});await pause(120)}
async function hover(selector){const point=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);if(!point)throw Error('missing hover target '+selector);await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x,y:point.y});await pause(200)}
async function key(key,code,value){await cdp('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:value});await cdp('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:value});await pause(100)}
async function screenshot(theme,name){await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)};window.scrollTo({top:0,left:0,behavior:'instant'})`);await pause(180);const shot=await cdp('Page.captureScreenshot',{format:'png'});const file=path.join(root,'screenshots',`rag269-${theme}-${name}.png`);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,Buffer.from(shot.data,'base64'));return file}
async function trace(){return await js(`window.call269('test.trace').then(x=>x)`)}
const result={ok:false,checks:[],screenshots:[],trace:null};
const check=(v,label)=>{if(!v)throw Error(label);result.checks.push(label)};
async function type(selector,text){await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('missing input');const d=Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set;d.call(e,${JSON.stringify(text)});e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`)}
async function lightDark(name){for(const theme of ['sakura','aurora'])result.screenshots.push(await screenshot(theme,name))}
let stage='startup';
try{
  stage='mount';await until(`!!window.ready269&&!!document.querySelector('[data-testid="rag-page"]')`);
  await until(`!!document.querySelector('[data-testid="rag-library"]')&&!!document.querySelector('[data-testid="rag-session-list"]')`);
  check(await js(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('新会话要用的资料')`),'without an active conversation the source panel is labeled for the next session');
  await click('[data-testid="rag-add-document-doc-plan"]');
  await click('[data-testid="rag-add-document-doc-upload"]');
  check(await js(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('数据库课程资料.pdf')&&document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('接口规范与示例说明.md')`),'a draft conversation keeps the current library selection for its next session');
  await click('[data-testid="rag-new-session"]');
  await until(`document.querySelector('[data-testid="rag-session-session-new-1"]')?.getAttribute('data-active')==='true'`);
  check(await js(`(()=>{const row=window.call269('test.trace');return row.then(x=>x.sessions.find(s=>s.id==='session-new-1')?.document_ids.includes('doc-plan')&&x.sessions.find(s=>s.id==='session-new-1')?.document_ids.includes('doc-upload'))})()`),'new sessions inherit the selected material group');
  await js(`window.call269('test.seed_history').then(x=>x)`);
  await js(`window.navigate269('home')`);
  await until(`!!document.querySelector('[data-testid="rag269-away"]')`);
  await js(`window.navigate269('rag')`);
  await until(`!!document.querySelector('[data-testid="rag-page"]')&&!!document.querySelector('[data-testid="rag-session-session-a"]')`);
  check(true,'existing conversation history and linked sessions load after returning to the page');
  await click('[data-testid="rag-session-session-a"]');
  await until(`document.querySelector('[data-testid="rag-session-session-a"]')?.getAttribute('data-active')==='true'`);

  stage='layout';
  check(await js(`!!document.querySelector('[data-testid="rag-library"]')&&!!document.querySelector('[data-testid="rag-session-documents"]')&&!!document.querySelector('[data-testid="rag-session-list"]')`),'the sidebar contains library, current-session materials, and session list');
  check(await js(`!document.querySelector('[data-testid="rag-session-sources"]')&&!document.querySelector('.rag-chat-sources')&&!Array.from(document.querySelectorAll('button')).some(x=>x.innerText.includes('改资料'))`),'the conversation pane has no duplicate material strip or edit-material button');
  check(await js(`!document.querySelector('[data-testid^="rag-document-"] input[type="checkbox"]')&&!document.querySelector('[data-testid="rag-library"] input[type="checkbox"]')`),'library selection uses direct add controls instead of checkboxes');
  check(await js(`(()=>{const p=document.querySelector('[data-testid="rag-page"]'),r=p.getBoundingClientRect();return r.width>=innerWidth-20&&document.documentElement.scrollWidth<=innerWidth+1})()`),'the redesigned page uses the available width without horizontal overflow');
  check(await js(`(()=>{const l=document.querySelector('.rag-layout'),s=document.querySelector('.rag-sidebar'),last=s?.lastElementChild,lr=l?.getBoundingClientRect(),sr=s?.getBoundingClientRect(),cr=last?.getBoundingClientRect();return !!l&&lr.height<=innerHeight+1&&cr.bottom<=lr.bottom+1&&sr.bottom<=lr.bottom+1})()`),'desktop layout stays within viewport height and the third sidebar panel stays inside its grid bounds');
  await lightDark('layout');

  stage='instant-add-remove';
  await click('[data-testid="rag-plan-select"]');
  await until(`!!document.querySelector('[role="listbox"]')`);
  await click('[data-testid="rag-plan-option-empty"]');
  await until(`!document.querySelector('[role="listbox"]')`);
  await until(`!!document.querySelector('[data-testid="rag-add-document-doc-other"]')`);
  await click('[data-testid="rag-add-document-doc-other"]');
  await until(`window.call269('test.trace').then(x=>x.updates.some(u=>u.session_id==='session-a'&&u.document_ids.includes('doc-other')))`);
  check(await js(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('第二份长标题资料')`),'adding a library document immediately adds it to the active conversation');
  check(await js(`document.querySelector('[data-testid="rag-add-document-doc-other"]')?.innerText.includes('已在本会话')`),'an included library document is labeled as already in this conversation');
  await click('[data-testid="rag-session-remove-document-doc-other"]');
  await until(`!document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('第二份长标题资料')`);
  {const t=await trace();check(t.updates[t.updates.length-1]?.document_ids?.includes('doc-other')===false,'removing a conversation chip immediately persists without a save button')}

  stage='empty-session';
  await click('[data-testid="rag-session-remove-document-doc-plan"]');
  await until(`window.call269('test.trace').then(x=>x.updates[x.updates.length-1]?.document_ids?.length===1)`);
  await click('[data-testid="rag-session-remove-document-doc-upload"]');
  await until(`window.call269('test.trace').then(x=>x.updates[x.updates.length-1]?.document_ids?.length===0)`);
  await until(`document.querySelector('[data-testid="rag-session-session-a"]')?.getAttribute('data-active')==='true'`);
  check(await js(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('尚未加入资料')`),'empty conversation has a clear add-materials state');
  check(await js(`document.querySelector('[data-testid="rag-send"]')?.disabled&&document.querySelector('[data-testid="rag-message-old-q"]')?.innerText.includes('旧问题保留')`),'removing the last source keeps session history and disables asking until a source is added');
  await click('[data-testid="rag-plan-select"]');
  await until(`!!document.querySelector('[role="listbox"]')`);
  await click('[data-testid="rag-plan-option-empty"]');
  await until(`!document.querySelector('[role="listbox"]')`);
  await click('[data-testid="rag-add-document-doc-upload"]');
  await until(`window.call269('test.trace').then(x=>x.updates[x.updates.length-1]?.document_ids?.includes('doc-upload'))`);
  await click('[data-testid="rag-add-document-doc-plan"]');
  await until(`window.call269('test.trace').then(x=>x.updates[x.updates.length-1]?.document_ids?.includes('doc-plan'))`);

  stage='rename';
  await click('[data-testid="rag-session-session-new-1"]');
  await until(`document.querySelector('[data-testid="rag-session-session-new-1"]')?.getAttribute('data-active')==='true'`);
  check(await js(`!!document.querySelector('[data-testid="rag-rename-session"]')`),'the selected conversation exposes an inline rename action');
  await click('[data-testid="rag-rename-session"]');
  await until(`!!document.querySelector('[data-testid="rag-rename-session-input"]')`);
  await type('[data-testid="rag-rename-session-input"]','接口边界复习');
  await click('[data-testid="rag-save-session-title"]');
  await until(`document.body.innerText.includes('接口边界复习')`);
  check((await trace()).renamed.includes('session-new-1'),'renaming saves the title for the current session');
  await lightDark('rename');
  await type('[data-testid="rag-question"]','手动标题应保留');
  await click('[data-testid="rag-send"]');
  await until(`document.body.innerText.includes('本机替身回答')`);
  check(await js(`document.querySelector('[data-testid="rag-session-session-new-1"]')?.innerText.includes('接口边界复习')`),'the first question does not overwrite a manually named conversation');

  stage='upload-target';
  await click('[data-testid="rag-pick-files"]');
  await until(`window.call269('test.trace').then(x=>x.pick_started===1)`);
  await click('[data-testid="rag-session-session-a"]');
  await until(`document.querySelector('[data-testid="rag-session-session-a"]')?.getAttribute('data-active')==='true'`);
  await js(`window.call269('test.release_import').then(x=>x)`);
  await until(`window.call269('test.trace').then(x=>x.updates.some(u=>u.session_id==='session-new-1'&&u.document_ids.includes('doc-uploaded')))`);
  const uploadTrace=await trace();
  check(uploadTrace.updates.some(u=>u.session_id==='session-new-1'&&u.document_ids.includes('doc-uploaded'))&&!uploadTrace.updates.some(u=>u.session_id==='session-a'&&u.document_ids.includes('doc-uploaded')),'a delayed upload remains attached only to the session captured when the picker opened, after switching conversations');
  check(uploadTrace.prepare>1,'the delayed uploaded file starts background preparation for its captured session');
  check(await js(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('接口规范与示例说明.md')&&!document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('自动加入的上传资料.txt')`),'the newly selected conversation does not display the late upload');
  await click('[data-testid="rag-session-session-new-1"]');
  await until(`document.querySelector('[data-testid="rag-session-session-new-1"]')?.getAttribute('data-active')==='true'`);
  await until(`document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('自动加入的上传资料.txt')`);

  stage='delete-dialog';
  // The uploaded item is in session-empty; choose doc-upload, which is shared
  // by session-a and session-b, to inspect distinct affected conversation names.
  await click('[data-testid="rag-plan-select"]');
  await until(`!!document.querySelector('[role="listbox"]')`);
  await click('[data-testid="rag-plan-option-empty"]');
  await until(`!document.querySelector('[role="listbox"]')`);
  await until(`!!document.querySelector('[data-testid="rag-document-delete-doc-upload"]')`);
  await click('[data-testid="rag-document-delete-doc-upload"]');
  await until(`!!document.querySelector('[data-testid="rag-delete-dialog"]')?.open`);
  const dialogText=await js(`document.querySelector('[data-testid="rag-delete-dialog"]')?.innerText||''`);
  check(dialogText.includes('接口规范与示例说明.md')&&/23\s*个会话/.test(dialogText),'deletion confirmation names the selected document and its twenty-three linked sessions');
  await click('[data-testid="rag-delete-sessions-info"]');
  await until(`!!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  const usage=await js(`document.querySelector('[data-testid="rag-delete-sessions-popover"]')?.innerText||''`);
  check(usage.includes('接口问答')&&usage.includes('资料复习会话 20'),'the disclosure lists distinct affected session names, including the last one');
  check(await js(`(()=>{const e=document.querySelector('[data-testid="rag-delete-sessions-popover"]');return !!e&&['auto','scroll'].includes(getComputedStyle(e).overflowY)&&e.scrollHeight>e.clientHeight})()`),'the long linked-session name list is rendered in a bounded scrollable popover');
  await lightDark('delete-confirm-list');

  // Keyboard and pointer affordances for the names popover: close via Esc and
  // return focus, reopen by keyboard, then close by clicking outside.
  await key('Escape','Escape',27);
  await until(`!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  check(await js(`document.activeElement===document.querySelector('[data-testid="rag-delete-sessions-info"]')`),'Escape closes the names list and returns focus to its trigger');
  await js(`document.activeElement.blur()`);
  await hover('#rag-delete-title');
  await hover('[data-testid="rag-delete-sessions-info"]');
  await until(`!!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  check(true,'hovering the information marker opens the linked-session names');
  await hover('#rag-delete-title');
  await until(`!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  await click('[data-testid="rag-delete-sessions-info"]');
  await until(`!!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  check(true,'clicking the information marker opens the linked-session names');
  await key('Escape','Escape',27);
  await until(`!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  check(await js(`document.activeElement===document.querySelector('[data-testid="rag-delete-sessions-info"]')`),'Escape from the trigger closes the popover and restores focus');
  await key('Enter','Enter',13);
  await until(`!!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  check(true,'Enter activates the linked-session names disclosure');
  await key('Escape','Escape',27);
  await until(`!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  await key(' ','Space',32);
  await until(`!!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  check(true,'Space activates the linked-session names disclosure');
  await click('#rag-delete-title');
  await until(`!document.querySelector('[data-testid="rag-delete-sessions-popover"]')`);
  await lightDark('delete-confirm');
  await click('[data-testid="rag-delete-confirm"]');
  await until(`window.call269('test.trace').then(x=>x.deleted.includes('doc-upload'))`);
  await until(`!document.querySelector('[data-testid="rag-library-document-doc-upload"]')`);
  await click('[data-testid="rag-session-session-a"]');
  await until(`document.querySelector('[data-testid="rag-session-session-a"]')?.getAttribute('data-active')==='true'`);
  await click('[data-testid="rag-citation-1"]');
  await until(`document.querySelector('[data-testid="rag-message-old-a"]')?.innerText.includes('资料已删除')`);
  check(await js(`document.querySelector('[data-testid="rag-message-old-a"]')?.innerText.includes('旧回答')&&document.querySelector('[data-testid="rag-source-1"]')?.innerText.includes('资料已删除')`),'deleting a document retains its answer and marks its historical citation as deleted');
  check(await js(`!document.querySelector('[data-testid="rag-session-documents"]')?.innerText.includes('接口规范与示例说明.md')`),'the deleted material is detached from each linked conversation');

  stage='narrow';
  await js(`window.resizeTo(620,820)`);
  // QtWebEngine may ignore window.resizeTo; use the CDP viewport override.
  await cdp('Emulation.setDeviceMetricsOverride',{width:620,height:820,deviceScaleFactor:1,mobile:false});
  await pause(180);
  check(await js(`document.documentElement.scrollWidth<=innerWidth+1&&document.querySelector('[data-testid="rag-sidebar"]')?.getBoundingClientRect().width<innerWidth`),'narrow layout keeps the sidebar and page within the viewport');
  await lightDark('narrow');
  result.trace=await trace();
  result.ok=true;
}catch(error){
  result.stage=stage;result.error=String(error.stack||error);
  result.dom=await js('document.body.innerText').catch(()=>'' );
  result.ids=await js(`Array.from(document.querySelectorAll('[data-testid]')).map(x=>x.dataset.testid)`).catch(()=>[]);
  result.geometry=await js(`(()=>{const e=document.querySelector('[data-testid="rag-page"]'),r=e?.getBoundingClientRect();return{innerWidth,scrollWidth:document.documentElement.scrollWidth,page:r?{left:r.left,right:r.right,width:r.width,maxWidth:getComputedStyle(e).maxWidth}:null}})()`).catch(()=>null);
  result.trace=await trace().catch(()=>null);
}finally{
  fs.writeFileSync(path.join(root,'result269.json'),JSON.stringify(result,null,2));ws.close();
}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
