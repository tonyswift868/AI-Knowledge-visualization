import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(process.argv[2]);
const artifacts = path.resolve(process.env.MF_CHAT219_ARTIFACTS || path.join(root, 'screenshots'));
const endpoint = process.env.MF_CDP_ENDPOINT;
const pause = (ms=35) => new Promise(resolve => setTimeout(resolve, ms));
let target;
for (let i=0; i<200; i++) {
  try {
    target = (await (await fetch(endpoint+'/json/list')).json()).find(t => t.type==='page' && t.url.startsWith('file:') &&
      path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));
    if (target) break;
  } catch {}
  await pause(100);
}
if (!target) throw Error('owned synthetic page not found');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject) => { ws.onopen=resolve; ws.onerror=reject; });
let id=0;
const pending=new Map();
ws.onmessage=e=>{const msg=JSON.parse(e.data);if(!msg.id)return;const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(Error(JSON.stringify(msg.error))):p.resolve(msg.result)};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))});
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
function check(condition,label,checks){if(!condition)throw Error(label);checks.push(label)}
async function click(selector){
  const p=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('missing '+${JSON.stringify(selector)});e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  await cdp('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',clickCount:1});
  await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',clickCount:1});
}
async function type(selector,value){
  await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.focus();const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
}
async function screenshot(name){const shot=await cdp('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(artifacts,name),Buffer.from(shot.data,'base64'))}
async function key(key,code){await cdp('Input.dispatchKeyEvent',{type:'rawKeyDown',key,code});await cdp('Input.dispatchKeyEvent',{type:'keyUp',key,code})}

const checks=[];
let result;
try {
  await until('!!window.ready219 && !!document.querySelector(".chat-session-open")');
  check(await js(`document.querySelectorAll('.chat-session-open').length===2 && !!document.querySelector('[role="tablist"]')`), 'initial active tab lists active synthetic conversations', checks);
  check(await js(`getComputedStyle(document.querySelector('.chat-list')).animationDuration==='0s'`), 'reduced-motion class disables chat-list animation', checks);
  await screenshot('list-light.png');

  await type('.composer textarea','保留的草稿文本');
  await click('button[aria-label="收起对话列表"]');
  await until('document.querySelector(".chat-page").classList.contains("list-collapsed")');
  check(await js(`document.querySelector('.composer textarea').value==='保留的草稿文本' && getComputedStyle(document.querySelector('.chat-list')).display==='none'`), 'desktop collapse hides the entire list and preserves the composer draft', checks);
  await screenshot('collapsed-light.png');
  await until('!!window.chat219Preference && window.chat219Preference().then(p=>p.list_collapsed===true)');
  await cdp('Page.reload');
  await until('!!window.ready219 && !!document.querySelector(".chat-page.list-collapsed")');
  check(await js('document.querySelector(".chat-page").classList.contains("list-collapsed")'), 'collapsed list preference survives a page reload', checks);
  await type('.composer textarea','保留的草稿文本');
  await click('button[aria-label="展开对话列表"]');
  await until('!document.querySelector(".chat-page").classList.contains("list-collapsed")');
  check(await js(`document.querySelector('.composer textarea').value==='保留的草稿文本'`), 'reopening the list keeps the unsent draft', checks);
  await type('.composer textarea','');

  await click('button[aria-label="归档对话：合成对话 Alpha"]');
  await until('!document.querySelector("button[aria-label=\\"归档对话：合成对话 Alpha\\"]")');
  await click('[role="tab"][aria-selected="false"]');
  await until('document.querySelectorAll(".chat-session-open").length===2 && document.body.innerText.includes("合成对话 Alpha")');
  check(await js(`document.querySelector('[role="tab"][aria-selected="true"]').textContent==='已归档'`), 'archive action moves the synthetic chat into the archived tab', checks);
  await click('button[aria-label="恢复对话：合成对话 Alpha"]');
  await until('!document.querySelector("button[aria-label=\\"恢复对话：合成对话 Alpha\\"]")');
  check(await js(`document.querySelectorAll('.chat-session-open').length===1 && document.body.innerText.includes('已归档合成对话')`), 'restore action returns one conversation to active and leaves the other archived', checks);
  await click('button[aria-label="恢复对话：已归档合成对话"]');
  await until('document.querySelectorAll(".chat-session-open").length===0');
  await click('[role="tab"][aria-selected="false"]');
  await until('document.querySelectorAll(".chat-session-open").length===3');
  await click('button[aria-label="归档对话：已归档合成对话"]');
  await until('!document.querySelector("button[aria-label=\\"归档对话：已归档合成对话\\"]")');
  await click('[role="tab"][aria-selected="false"]');
  await until('document.querySelectorAll(".chat-session-open").length===1 && document.body.innerText.includes("已归档合成对话")');
  check(true, 'restore is available from the archive list and the session can be archived again', checks);
  await screenshot('archive-light.png');

  await click('button[aria-label="打开对话：已归档合成对话"]');
  await until('!!document.querySelector(".chat-archived-note")');
  check(await js(`document.querySelector('.chat-archived-note').textContent.includes('发送消息会自动恢复') && document.querySelector('.chat-archived-note button').textContent.includes('恢复')`), 'archived conversation remains readable and exposes restore while composing auto-restores', checks);
  await type('.composer textarea','继续这个归档对话');
  await click('.composer button');
  await until('!!document.querySelector(".bubble.streaming")');
  await click('button[aria-label="收起对话列表"]');
  await until('document.querySelector(".chat-page").classList.contains("list-collapsed")');
  check(await js(`!!document.querySelector('.bubble.streaming') && document.querySelector('.bubble.streaming').textContent.includes('替身正在生成')`), 'collapsing the list leaves the in-progress reply visible', checks);
  fs.writeFileSync(path.join(root,'finish'),'');
  await until('!document.querySelector(".bubble.streaming") && !document.querySelector(".chat-archived-note") && document.body.innerText.includes("替身回复已完成")');
  check(await js(`document.body.innerText.includes('继续这个归档对话') && document.body.innerText.includes('替身回复已完成')`), 'sending from an archived session completes and auto-restores it', checks);
  await click('button[aria-label="展开对话列表"]');
  await until('!!document.querySelector("button[aria-label=\\"归档对话：已归档合成对话\\"]")');
  check(await js(`document.querySelector('[role="tab"][aria-selected="true"]').textContent==='对话'`), 'restored conversation appears on the active tab', checks);

  await click('button[aria-label="归档对话：已归档合成对话"]');
  await until('!document.querySelector("button[aria-label=\\"归档对话：已归档合成对话\\"]")');
  await click('[role="tab"][aria-selected="false"]');
  await until('!!document.querySelector("button[aria-label=\\"删除对话：已归档合成对话\\"]")');
  await click('button[aria-label="删除对话：已归档合成对话"]');
  await click('button[aria-label="确认删除对话：已归档合成对话"]');
  await until('!document.body.innerText.includes("已归档合成对话")');
  check(true, 'archived conversation can be removed through the existing two-step delete control', checks);
  await click('[role="tab"][aria-selected="false"]');
  await until('document.querySelectorAll(".chat-session-open").length===2');

  await click('button[aria-label="删除对话：合成对话 Beta"]');
  await until('!!document.querySelector("button[aria-label=\\"确认删除对话：合成对话 Beta\\"]")');
  check(await js(`!!document.querySelector('button[aria-label="确认删除对话：合成对话 Beta"]') && document.body.innerText.includes('合成对话 Beta')`), 'unarchived conversation offers a separate confirmed direct-delete action', checks);
  await screenshot('delete-unarchived-before.png');
  await click('button[aria-label="确认删除对话：合成对话 Beta"]');
  await until('!document.body.innerText.includes("合成对话 Beta")');
  await screenshot('delete-unarchived-after.png');
  check(await js(`document.querySelectorAll('.chat-session-open').length===1 && !document.body.innerText.includes('合成对话 Beta')`), 'confirmed delete removes an unarchived conversation from the active list', checks);

  await cdp('Emulation.setDeviceMetricsOverride',{width:600,height:820,deviceScaleFactor:1,mobile:false});
  await until('innerWidth===600 && document.querySelector(".chat-list")?.getAttribute("aria-hidden")==="true" && !!document.querySelector("button[aria-label=\\"打开对话列表\\"]")');
  check(await js('document.documentElement.scrollWidth<=innerWidth'), 'narrow chat layout has no horizontal overflow', checks);
  await until('!document.querySelector(".chat-list").classList.contains("drawer-open")');
  check(await js('getComputedStyle(document.querySelector(".chat-list")).display==="none"'), 'narrow layout starts with its conversation drawer closed', checks);
  await click('button[aria-label="打开对话列表"]');
  await until('document.querySelector(".chat-list").classList.contains("drawer-open")');
  await until('!!document.activeElement.closest(".chat-list")?.classList.contains("drawer-open") && !!document.activeElement.closest(".chat-list-tools")');
  check(await js(`document.activeElement.closest('.chat-list')?.classList.contains('drawer-open') && !!document.activeElement.closest('.chat-list-tools')`), 'opening the narrow drawer moves focus to its close control', checks);
  await key('Escape','Escape');
  await until('!document.querySelector(".chat-list").classList.contains("drawer-open")');
  await until('document.activeElement.getAttribute("aria-label")==="打开对话列表"');
  check(await js(`document.activeElement.getAttribute('aria-label')==='打开对话列表'`), 'Escape closes the drawer and restores focus to its opener', checks);
  await click('button[aria-label="打开对话列表"]');
  await until('document.querySelector(".chat-list").classList.contains("drawer-open")');
  await click('button[aria-label="打开对话：合成对话 Alpha"]');
  await until('document.activeElement.matches(".composer textarea") && !document.querySelector(".chat-list").classList.contains("drawer-open")');
  await until('document.querySelector(".chat-title").textContent==="合成对话 Alpha"');
  check(await js('document.documentElement.scrollWidth<=innerWidth'), 'selecting a chat closes the drawer, focuses the composer and stays within 600px', checks);

  result={ok:true,checks,preferenceExpectedCollapsed:true,viewport:await js('innerWidth')};
} catch(e) {
  result={ok:false,checks,error:String(e.stack||e),body:await js('document.body.innerText').catch(()=>null),active:await js('document.activeElement?.outerHTML').catch(()=>null)};
} finally {
  fs.writeFileSync(path.join(root,'browser-result219.json'),JSON.stringify(result,null,2));
  ws.close();
}
console.log(JSON.stringify(result));
if(!result.ok)process.exitCode=1;
