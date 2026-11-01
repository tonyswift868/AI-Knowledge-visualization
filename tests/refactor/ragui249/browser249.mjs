import {reachRagTarget278,openMaterials278,closeMaterials278} from '../rag278/materials_navigation278.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(process.argv[2]), endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=50)=>new Promise(resolve=>setTimeout(resolve,ms));
let target;
for(let i=0;i<240;i++){
  try{
    target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));
    if(target)break;
  }catch{}
  await pause(100);
}
if(!target)throw Error('RAG page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
let seq=0;const pending=new Map();
ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){const request=pending.get(message.id);pending.delete(message.id);message.error?request.reject(Error(JSON.stringify(message.error))):request.resolve(message.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value}
async function until(expression,limit=400){for(let i=0;i<limit;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function rawClick(selector){const point=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);if(!point)throw Error('missing selector '+selector);await call('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',clickCount:1});await pause(100)}
async function key(key,code,value){await call('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:value});await call('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:value});await pause(60)}
const result={ok:false,checks:[],screenshots:[]};
const check=(ok,label)=>{if(!ok)throw Error(label);result.checks.push(label)};
try{
  await until(`!!document.querySelector('[data-testid="rag-page"]')&&!!window.call249`);
  await until(`!!document.querySelector('[data-testid="rag-plan-select"]')`);
  const title='Java 服务端工程实践与数据库系统设计的长期综合学习计划（用于验证超长名称截断和完整名称提示）';
  const trigger='[data-testid="rag-plan-select"]';
  await click(trigger);
  await until(`!!document.querySelector('[role="listbox"]')`);
  const geometry=await js(`(()=>{const trigger=document.querySelector(${JSON.stringify(trigger)}),list=document.querySelector('[role="listbox"]'),option=document.querySelector('[data-testid="rag-plan-option-plan-long"]'),card=trigger.closest('.rag-sources'),text=trigger.querySelector('.select-menu-label');const tr=trigger.getBoundingClientRect(),lr=list.getBoundingClientRect(),cr=card.getBoundingClientRect();return{trigger:{left:tr.left,right:tr.right,width:tr.width},list:{left:lr.left,right:lr.right,width:lr.width},card:{left:cr.left,right:cr.right,width:cr.width},option:option?.innerText,optionTitle:option?.title,title:trigger.title,text:text?.innerText,style:{overflow:getComputedStyle(text).overflow,textOverflow:getComputedStyle(text).textOverflow,whiteSpace:getComputedStyle(text).whiteSpace}}})()`);
  check(geometry.list.width<=geometry.trigger.width+1&&geometry.list.right<=geometry.card.right+1,'open custom plan menu stays within the selector and source card width');
  check(geometry.optionTitle===title&&geometry.option?.includes(title),'long plan option keeps full name available in its tooltip');
  await key('Escape','Escape',27);
  await until(`!document.querySelector('[role="listbox"]')`);
  check(await js(`document.activeElement===document.querySelector('[data-testid="rag-plan-select"]')`),'Escape closes the menu and returns focus to trigger');

  await click(trigger);
  await until(`!!document.querySelector('[role="listbox"]')`);
  let selectedActive=false;
  for(let i=0;i<6;i++){
    const active=await js(`(()=>{const t=document.querySelector(${JSON.stringify(trigger)}),id=t.getAttribute('aria-activedescendant');return id?document.getElementById(id)?.dataset.testid||document.getElementById(id)?.innerText:''})()`);
    if(String(active).includes('plan-long')||String(active).includes(title)){selectedActive=true;break}
    await key('ArrowDown','ArrowDown',40);
  }
  check(selectedActive,'ArrowDown can keyboard-highlight the long plan option');
  await key('Enter','Enter',13);
  await until(`document.body.innerText.includes(${JSON.stringify(title)})`);
  check(await js(`(()=>{const t=document.querySelector(${JSON.stringify(trigger)}),label=t?.querySelector('.select-menu-label');return t?.getAttribute('title')===${JSON.stringify(title)}&&getComputedStyle(label).textOverflow==='ellipsis'&&getComputedStyle(label).whiteSpace==='nowrap'&&label.clientWidth>0&&label.getBoundingClientRect().right<=t.getBoundingClientRect().right})()`),'selected long plan fits or ellipsizes inside the wider control while its full name remains available');
  await until(`!!document.querySelector('[data-testid="rag-question-mic"] .mf-voice-mic')`);
  await closeMaterials278(js,rawClick);
  check(await js(`!!document.querySelector('[data-testid="rag-question-mic"] .mf-voice-mic')`),'question composer uses the existing MicButton component');

  result.voiceStatus=await js(`window.call249('voice.status').then(x=>x)`);
  await click('[data-testid="rag-question-mic"] .mf-voice-mic');
  await until(`document.querySelector('[data-testid="rag-question-mic"] .mf-voice-mic')?.getAttribute('aria-label')==='结束录音'`);
  await click('[data-testid="rag-question-mic"] .mf-voice-mic');
  await until(`document.querySelector('[data-testid="rag-question"]')?.value==='识别结果可编辑后再提问'`);
  check(await js(`!document.querySelector('[data-testid="rag-question"]')?.readOnly&&!document.querySelector('[data-testid="rag-question"]')?.disabled`),'recognized voice text remains editable in the question box');
  const callsBeforeEdit=await js(`window.call249('test.trace').then(x=>x)`);
  check(callsBeforeEdit.send===0&&callsBeforeEdit.create===0,'voice result does not send a question or create a session automatically');
  await js(`(()=>{const e=document.querySelector('[data-testid="rag-question"]');const set=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;set.call(e,e.value+'，我可以修改');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
  await until(`document.querySelector('[data-testid="rag-question"]')?.value.includes('我可以修改')`);
  check(true,'recognized text can be edited before the learner chooses to send');
  const trace=await js(`window.call249('test.trace').then(x=>x)`);
  check(trace.send===0,'rag.send remains unused until a separate send action');

  for(const theme of ['sakura','aurora']){
    await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);
    await click(trigger);
    await until(`!!document.querySelector('[role="listbox"]')`);
    const shot=await call('Page.captureScreenshot',{format:'png'});
    const screenshot=path.join(root,'screenshots',`rag-${theme}-249.png`);
    fs.mkdirSync(path.dirname(screenshot),{recursive:true});
    fs.writeFileSync(screenshot,Buffer.from(shot.data,'base64'));
    result.screenshots.push(screenshot);
    await key('Escape','Escape',27);
    await until(`!document.querySelector('[role="listbox"]')`);
  }
  await js(`window.call249('test.voice_not_ready').then(x=>x)`);
  await click('[data-testid="rag-question-mic"] .mf-voice-mic');
  await until(`document.querySelector('.mf-voice-dialog')?.open===true`);
  check(await js(`document.querySelector('.mf-voice-dialog')?.innerText.includes('同意并下载')`),'uninstalled voice model opens the existing preparation prompt');
  result.ok=true;
}catch(error){
  result.error=String(error.stack||error);
  result.dom=await js('document.body.innerText').catch(()=>''),
  result.testIds=await js(`Array.from(document.querySelectorAll('[data-testid]')).map(e=>e.dataset.testid)`).catch(()=>[]);
  result.selectState=await js(`(()=>{const t=document.querySelector('[data-testid="rag-plan-select"]'),l=document.querySelector('[role="listbox"]');return{trigger:t?.outerHTML,list:l?.outerHTML,active:document.activeElement?.outerHTML}})()`).catch(()=>null);
}finally{
  fs.writeFileSync(path.join(root,'result249.json'),JSON.stringify(result,null,2));
  ws.close();
}
console.log(JSON.stringify(result));
if(!result.ok)process.exitCode=1;

async function click(selector){await reachRagTarget278(js,rawClick,selector);await rawClick(selector)}
