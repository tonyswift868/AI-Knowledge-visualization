import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,before=process.argv.includes('--before');
if(!endpoint?.startsWith('http://127.0.0.1:'))throw Error('owned local CDP endpoint required');
const pause=(ms=35)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map(),checks=[];
ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
function assert(value,message){if(!value)throw Error(message)}
async function until(expression){for(let i=0;i<250;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){const p=await js(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await call('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',buttons:1,clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',buttons:0,clickCount:1});await pause()}
async function metrics(width,theme){await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);await js(`Promise.all(document.getAnimations().filter(a=>a.effect?.target?.classList?.contains('class-page')).map(a=>a.finished))`);await pause(150);assert(!await js('document.documentElement.scrollWidth>innerWidth'),'horizontal overflow')}
async function shot(name){const size=await js('({width:innerWidth,height:innerHeight})');const r=await call('Page.captureScreenshot',{format:'png',clip:{x:0,y:0,...size,scale:1}});fs.writeFileSync(path.join(root,name),Buffer.from(r.data,'base64'))}
const chat=()=>js(`(()=>{const r=document.querySelector('.class-scroll').getBoundingClientRect();return {height:r.height,top:r.top}})()`);
async function escape(){await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await pause()}
let result;
try{
 await until('!!window.ready129');await js('window.ready129');await metrics(1000,'sakura');
 if(before){await shot('before129-sakura.png');await metrics(600,'aurora');await shot('before129-aurora-narrow.png');result={ok:true,chat:await chat()}}
 else{
  assert(!await js(`document.body.innerText.includes('取消准备')`),'obsolete cancel preparation visible');
  assert(await js(`document.querySelector('.class-preparation129').textContent.includes('题目准备中 3/22')`),'badge progress missing');
  const rect=await chat();
  await click('[data-materials="notes"]');await until(`!!document.querySelector('dialog[open][aria-label="讲义"]')`);
  assert(JSON.stringify(await chat())===JSON.stringify(rect),'notes popup shifted chat '+JSON.stringify({before:rect,after:await chat()}));
  assert(await js(`document.querySelector('dialog[open]').textContent.includes('掌握目标')`),'goal missing');
  await click('dialog[open] .class-note-entry button');await until(`!!document.querySelector('dialog[open] .original-fulltext')`);
  assert(await js(`document.querySelector('dialog[open] .original-fulltext').textContent.includes('公式')`),'source did not open');
  await click('[data-materials="navigation"]');await until(`!!document.querySelector('dialog[open][aria-label="资料导航"]')`);
  assert(await js(`document.querySelectorAll('dialog[open]').length===1`),'two popups open');
  assert(JSON.stringify(await chat())===JSON.stringify(rect),'navigation popup shifted chat');
  await escape();assert(!await js(`!!document.querySelector('dialog[open]')`),'Esc did not close');
  assert(await js(`document.activeElement.dataset.materials==='navigation'`),'Esc did not restore focus');
  await click('[data-materials="notes"]');await click('.class-title');assert(!await js(`!!document.querySelector('dialog[open]')`),'outside did not close');
  assert(await js(`document.activeElement.dataset.materials==='notes'`),'outside did not restore focus');checks.push('讲义/导航浮层不挤聊天；一次一个；原文可展开；Esc/外点关闭并还焦');
  await metrics(1000,'sakura');await shot('after129-sakura.png');
  await metrics(600,'aurora');const row=await js(`(()=>{const e=document.querySelector('.class-material-row129'),r=e.getBoundingClientRect();return {height:r.height,overflow:e.scrollWidth>e.clientWidth}})()`);
  assert(row.height<=48&&!row.overflow,'compact row wraps or overflows');
  await click('[data-materials="notes"]');await shot('after129-aurora-narrow.png');await escape();checks.push('亮暗/窄窗单行；顶部迟到提醒小标签');
  assert(await js(`getComputedStyle(document.querySelector('.class-preparation129 svg')).animationName!=='none'`),'spinner missing');
  await js('window.reduced129(true)');assert(await js(`getComputedStyle(document.querySelector('.class-preparation129 svg')).animationName==='none'`),'app reduced motion ignored');
  await js('window.reduced129(false)');await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});assert(await js(`getComputedStyle(document.querySelector('.class-preparation129 svg')).animationName==='none'`),'system reduced motion ignored');await call('Emulation.setEmulatedMedia',{features:[]});
  await js(`window.mount129('failure')`);await until(`!!document.querySelector('button.class-preparation129')`);await click('button.class-preparation129');await until(`!document.querySelector('button.class-preparation129')`);assert(await js(`window.calls129.some(c=>c.method==='classroom.retry')`),'retry not wired');checks.push('进度/失败重试；减少动态效果停转');
  await js(`window.mount129('wait')`);await until(`!!document.querySelector('.class-wait129')`);assert(await js(`document.querySelector('.class-wait129').textContent.includes('备好后自动继续')`),'wait explanation missing');await click('.class-wait129 button');await until(`!document.querySelector('.class-wait129')`);assert(await js(`!!document.querySelector('.class-preparation129')&&window.calls129.some(c=>c.method==='classroom.cancel_preparation')`),'wait exit lost background status');checks.push('等待时回去接着学；保留准备状态');
  await js(`window.mount129('legacy')`);assert(!await js(`!!document.querySelector('.class-subpoint129')`),'legacy subpoint label');assert(!await js(`!!document.querySelector('.class-material-row129')`),'empty toolbar visible');checks.push('旧式点不显示小要点；无内容隐藏按钮');
  result={ok:true,checks,chat:rect,row};
 }
}catch(e){result={ok:false,checks,error:String(e.stack||e),dom:await js('document.body.innerText').catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'result129.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
