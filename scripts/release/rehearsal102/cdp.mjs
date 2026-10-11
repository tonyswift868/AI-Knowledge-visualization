// CDP commands target only the explicitly enabled preview debugging endpoint.
// No OS keyboard/mouse, UIA focus, or foreground calls are used.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const [action='inspect', selector='', value='', targetHint=''] = process.argv.slice(2);
const endpoint=process.env.MF_CDP_ENDPOINT || 'http://127.0.0.1:18861';
if (!endpoint.startsWith('http://127.0.0.1:')) throw Error('local preview endpoint required');
const targets=await (await fetch(endpoint+'/json/list')).json();
const root=path.resolve(process.env.MF_UAT_ROOT || 'D:/MentorFlow/.local/test-tmp/inapp102c').toLowerCase()+path.sep;
const owned=targets.filter(t=>{
 try{return t.type==='page' && t.url.startsWith('file:') && path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root)}
 catch{return false}
});
if(action==='targets'){console.log(JSON.stringify(owned));process.exit(0)}
const target=owned.find(t=>!targetHint || t.url.includes(targetHint));
if(!target) throw Error('preview page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();const scripts=[];
ws.onmessage=e=>{const data=JSON.parse(e.data);if(data.method==='Debugger.scriptParsed')scripts.push(data.params);if(data.id){const p=pending.get(data.id);pending.delete(data.id);data.error?p.reject(Error(JSON.stringify(data.error))):p.resolve(data.result)}};
function call(method,params={}){return new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}))})}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
let result;
if(action==='wait-text')result=await evaluate(`new Promise((resolve,reject)=>{const until=Date.now()+30000;function tick(){if(document.body?.innerText.includes(${JSON.stringify(selector)}))return resolve({found:${JSON.stringify(selector)},at:Date.now()});if(Date.now()>until)return reject(Error('page text timeout'));setTimeout(tick,100)}tick()})`);
else if(action==='inspect')result=await evaluate(`({text:document.body.innerText,inputs:[...document.querySelectorAll('input,textarea,select')].map(e=>({tag:e.tagName,type:e.type,placeholder:e.placeholder,id:e.id,name:e.name,aria:e.getAttribute('aria-label')})),buttons:[...document.querySelectorAll('button')].map(e=>({text:e.innerText,disabled:e.disabled}))})`);
else if(action==='click')result=await evaluate(`(()=>{const e=[...document.querySelectorAll('button,a')].find(e=>e.textContent.trim()===${JSON.stringify(selector)}&&!e.disabled&&e.getClientRects().length);if(!e)throw Error('enabled visible button missing');e.click();return{clicked:${JSON.stringify(selector)},at:Date.now()}})()`);
else if(action==='input'){
 await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('input missing');const setter=Object.getOwnPropertyDescriptor(e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set;setter.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
 result={input:selector,characters:value.length};
}else if(action==='evaluate')result=await evaluate(selector);
else if(action==='script-contains'){
 await call('Debugger.enable');
 const matches=[];
 for(const script of scripts){
  if(!script.url.startsWith('file:'))continue;
  if(!path.resolve(fileURLToPath(script.url)).toLowerCase().startsWith(root))continue;
  const source=await call('Debugger.getScriptSource',{scriptId:script.scriptId});
  if(source.scriptSource.includes(selector))matches.push(script.url);
 }
 result={found:matches.length>0,loaded_scripts:matches};
}
else if(action==='shot'){const image=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(selector,Buffer.from(image.data,'base64'));result={screenshot:selector};}
else throw Error('unknown action');
console.log(JSON.stringify(result ?? null));ws.close();
