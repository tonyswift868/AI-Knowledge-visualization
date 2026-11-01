import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
import {click253} from '../rag155/click253.mjs';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=(ms=80)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<300;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('RAG 263 browser page not found');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});let seq=0;const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression,limit=500){for(let i=0;i<limit;i++){if(await js(expression))return;await pause()}throw Error('timeout '+expression)}
async function click(selector){await click253(js,(type,x,y)=>cdp('Input.dispatchMouseEvent',{type,x,y,button:'left',clickCount:1}),selector);await pause(120)}
async function typeQuestion(text){await js(`(()=>{const e=document.querySelector('[data-testid="rag-question"]');const d=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;d.call(e,${JSON.stringify(text)});e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`)}
let stage='startup';const result={ok:false,checks:[],screenshots:[],trace:null},check=(v,l)=>{if(!v)throw Error(l);result.checks.push(l)};
async function shot(theme){await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);await pause(180);const x=await cdp('Page.captureScreenshot',{format:'png'});const file=path.join(root,'screenshots',`rag263-${theme}.png`);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,Buffer.from(x.data,'base64'));result.screenshots.push(file)}
try{
 stage='mount';await until(`!!window.ready263&&!!document.querySelector('[data-testid="rag-page"]')`);
 stage='select-a';await until(`!!document.querySelector('[data-testid="rag-session-session-a"][data-active="true"]')&&!!document.querySelector('[data-testid="rag-question"]')`);
 stage='question-a';await typeQuestion('离页后保留的草稿');
 check(await js(`document.querySelector('[data-testid="rag-question"]')?.value==='离页后保留的草稿'`),'question input accepts the test draft');
 check(await js(`!document.querySelector('[data-testid="rag-send"]')?.disabled`),'send is enabled for the selected document and question');
 await click('[data-testid="rag-send"]');
 stage='wait-job-a';
 await until(`!!document.querySelector('[data-testid="rag-prepare-status"]')`);
 check(await js(`document.querySelector('[data-testid="rag-prepare-status"]')?.innerText.includes('3')`),'real React page displays backend job progress');
 await shot('sakura');await shot('aurora');
 await js(`window.navigate263('home')`);await until(`!!document.querySelector('[data-testid="rag263-away"]')`);
 await js(`window.call263('test.progress',{state:'running',uploaded_chunks:6}).then(x=>x)`);
 check((await js(`window.call263('test.trace').then(x=>x)`)).cancel===0,'route unmount did not call explicit cancellation');
 await js(`window.navigate263('rag')`);await until(`!!document.querySelector('[data-testid="rag-page"]')`);
 await until(`document.querySelector('[data-testid="rag-prepare-status"]')?.innerText.includes('6')`);
 check(await js(`document.querySelector('[data-testid="rag-question"]')?.value==='离页后保留的草稿'`),'returning to the same session restores the question draft');
 check((await js(`window.call263('test.trace').then(x=>x)`)).send===0,'returning to a prepared job does not auto-send the waiting question');
 await js(`window.call263('test.progress',{state:'ready',uploaded_chunks:10}).then(x=>x)`);
 await until(`!document.querySelector('[data-testid="rag-prepare-status"]')`);
 check((await js(`window.call263('test.trace').then(x=>x)`)).send===0,'job completion while the user is back still waits for an explicit send');
 await click('[data-testid="rag-send"]');await until(`window.call263('test.trace').then(x=>x.send===1)`);
 check(true,'explicit user send after returning sends the preserved draft once');

 await click('[data-testid="rag-session-session-b"]');await until(`!!document.querySelector('[data-testid="rag-question"]')`);
 await typeQuestion('留页等待后继续发送');await click('[data-testid="rag-send"]');await until(`!!document.querySelector('[data-testid="rag-prepare-status"]')`);
 await js(`window.call263('test.progress',{state:'ready',uploaded_chunks:10}).then(x=>x)`);await until(`window.call263('test.trace').then(x=>x.send===2)`);
 check((await js(`window.call263('test.trace').then(x=>x)`)).send===2,'staying on the page auto-sends after preparation completes');

 await click('[data-testid="rag-session-session-c"]');await until(`!!document.querySelector('[data-testid="rag-question"]')`);
 await typeQuestion('显式取消后保留');await click('[data-testid="rag-send"]');await until(`!!document.querySelector('[data-testid="rag-prepare-status"]')`);
 await click('[data-testid="rag-cancel-preparation"]');await until(`window.call263('test.trace').then(x=>x.cancel===1)`);
 check(await js(`document.querySelector('[data-testid="rag-question"]')?.value==='显式取消后保留'`),'explicit cancel preserves the draft');
 check((await js(`window.call263('test.trace').then(x=>x)`)).send===2,'explicit cancellation prevents model send');

 await click('[data-testid="rag-session-session-d"]');await until(`!!document.querySelector('[data-testid="rag-question"]')`);
 await typeQuestion('账号范围隔离');await click('[data-testid="rag-send"]');await until(`!!document.querySelector('[data-testid="rag-prepare-status"]')`);
 const beforeScopeSwitch=await js(`window.call263('test.trace').then(x=>x)`);
 await js(`window.setAccount263('account-b');window.call263('test.switch_account').then(x=>x)`);
 await until(`!!document.querySelector('[data-testid="rag-session-session-new"]')`);
 await click('[data-testid="rag-session-session-new"]');
 await until(`!!document.querySelector('[data-testid="rag-session-session-new"][data-active="true"]')&&!!document.querySelector('[data-testid="rag-session-documents"]')`);
 check(await js(`!document.body.innerText.includes('账号范围隔离')&&!document.querySelector('[data-testid="rag-prepare-status"]')`),'new account ignores the old-scope late event and draft');
 const afterScopeSwitch=await js(`window.call263('test.trace').then(x=>x)`);
 check(afterScopeSwitch.prepare===beforeScopeSwitch.prepare,'opening a new account does not auto-start its partial document');
 check(await js(`!!document.querySelector('[data-testid="rag-document-continue-doc-new"]')`),'a restarted partial document shows an explicit continue control');
 await click('[data-testid="rag-document-continue-doc-new"]');
 await until(`window.call263('test.trace').then(x=>x.prepare===${beforeScopeSwitch.prepare+1})`);
 check(await js(`document.querySelector('[data-testid="rag-prepare-status"]')?.innerText.includes('3')`),'explicit continue starts the new-scope preparation job');
 const trace=await js(`window.call263('test.trace').then(x=>x)`);result.trace=trace;
 result.ok=true;
}catch(e){result.stage=stage;result.error=String(e.stack||e);result.dom=await js('document.body.innerText').catch(()=>'' );result.ids=await js(`Array.from(document.querySelectorAll('[data-testid]')).map(x=>x.dataset.testid)`).catch(()=>[]);result.trace=await js(`window.call263('test.trace').then(x=>x)`).catch(()=>null)}
finally{fs.writeFileSync(path.join(root,'result263.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
