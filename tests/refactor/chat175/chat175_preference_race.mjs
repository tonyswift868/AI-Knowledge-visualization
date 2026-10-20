import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(process.argv[2]);
const endpoint = process.env.MF_CDP_ENDPOINT;
const pause = (ms=25) => new Promise(resolve => setTimeout(resolve, ms));
const waitFile = async name => { for (let i=0;i<400;i++) { if (fs.existsSync(path.join(root,name))) return; await pause(); } throw Error('timeout waiting for '+name) };
let target;
for (let i=0;i<200;i++) {
  try {
    target=(await (await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));
    if(target)break;
  } catch {}
  await pause(100);
}
if(!target)throw Error('owned delayed-preference page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let id=0;
const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(!m.id)return;const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))});
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js('!!('+expression+')'))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('missing '+${JSON.stringify(selector)});e.click();return true})()`);}

const result={snapshot:false,clickWon:false,collapsed:false};
try {
  await until('!!window.ready175 && !!document.querySelector("button[aria-label=\\"收起对话列表\\"]")');
  await waitFile('ui-get-started');
  // The fake backend's initial preference is false; do not issue a second
  // ui_get while the intentionally delayed first read occupies this scenario.
  result.snapshot=true;
  await click('button[aria-label="收起对话列表"]');
  await until('document.querySelector(".chat-page").classList.contains("list-collapsed")');
  await waitFile('ui-set-true');
  result.clickWon=true;
  fs.writeFileSync(path.join(root,'release-ui-get'),'');
  await waitFile('ui-get-returned');
  await pause(250);
  result.collapsed=await js('document.querySelector(".chat-page").classList.contains("list-collapsed")');
} catch(e) { result.error=String(e.stack||e); }
finally { fs.writeFileSync(path.join(root,'release-ui-get'),'');fs.writeFileSync(path.join(root,'preference-race-result.json'),JSON.stringify(result,null,2));ws.close(); }
console.log(JSON.stringify(result));
if(!result.collapsed)process.exitCode=1;
