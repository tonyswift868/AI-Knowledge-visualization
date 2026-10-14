import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
if(!endpoint?.startsWith('http://127.0.0.1:'))throw Error('own local CDP endpoint required');
const pause=(ms=35)=>new Promise(r=>setTimeout(r,ms));
let target;
for(let i=0;i<150;i++){
 try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}
 await pause(100);
}
if(!target)throw Error('owned test page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map(),checks=[];
ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
function assert(value,message){if(!value)throw Error(message)}
async function until(expression){for(let i=0;i<250;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
const handle='.skip127-handle';
const mode=()=>js(`document.querySelector('${handle}').getAttribute('aria-checked')==='true'`);
async function waitMode(value){await until(`document.querySelector('${handle}').getAttribute('aria-checked')==='${value}'`)}
async function bounds(selector=handle){return js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,width:r.width,travel:e.clientWidth-8-(e.querySelector('.skip127-thumb')?.offsetWidth||0)}})()`)}
async function mouse(type,x,y,extra={}){await call('Input.dispatchMouseEvent',{type,x,y,...extra})}
async function click(selector){const p=await bounds(selector);await mouse('mouseMoved',p.x,p.y);await mouse('mousePressed',p.x,p.y,{button:'left',buttons:1,clickCount:1});await mouse('mouseReleased',p.x,p.y,{button:'left',buttons:0,clickCount:1});await pause()}
async function drag(ratio){const p=await bounds();await mouse('mouseMoved',p.x,p.y);await mouse('mousePressed',p.x,p.y,{button:'left',buttons:1,clickCount:1});assert(await js(`document.querySelector('${handle}').hasPointerCapture(1)`),'pointer was not captured');await mouse('mouseMoved',p.x+p.travel*ratio,p.y,{button:'left',buttons:1});await mouse('mouseReleased',p.x+p.travel*ratio,p.y,{button:'left',buttons:0,clickCount:1});await pause()}
async function key(value,code,virtual){await js(`document.querySelector('${handle}').focus()`);await call('Input.dispatchKeyEvent',{type:'keyDown',key:value,code,windowsVirtualKeyCode:virtual});await call('Input.dispatchKeyEvent',{type:'keyUp',key:value,code,windowsVirtualKeyCode:virtual});await pause()}
async function button(label){await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)}&&!b.disabled);if(!b)throw Error('missing '+${JSON.stringify(label)});b.click()})()`);await pause()}
const executions=()=>js(`window.calls127.filter(c=>c.method==='classroom.skip_point'||c.method==='classroom.test').length`);
async function metrics(width,theme){await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);await pause(200);assert(!await js('document.documentElement.scrollWidth>innerWidth'),'horizontal overflow')}
async function shot(name){const size=await js('({width:innerWidth,height:innerHeight})');const r=await call('Page.captureScreenshot',{format:'png',clip:{x:0,y:0,...size,scale:1}});fs.writeFileSync(path.join(root,name),Buffer.from(r.data,'base64'))}
let result;
try{
 await until('!!window.ready127');await js('window.ready127');await metrics(900,'sakura');await mouse('mouseMoved',5,5);await shot('default127-sakura.png');
 assert(!await mode(),'initial scope must be point');const width=(await bounds('.skip127')).width;const prefix=await js(`document.querySelector('.skip127-main>span').getBoundingClientRect().x`);
 await drag(.4);await waitMode(false);checks.push('不到一半弹回；真实pointer capture');
 await drag(.75);await waitMode(true);assert((await bounds('.skip127')).width===width,'mode changed control width');assert(await js(`document.querySelector('.skip127-main>span').getBoundingClientRect().x`)===prefix,'fixed prefix moved');
 assert(await js(`getComputedStyle(document.querySelector('.skip127-word')).animationName==='skip127-replace'`),'replacement animation missing');checks.push('拖过一半切模块；仅词组动画/宽度不变');
 await drag(-.75);await waitMode(false);checks.push('向左拉回知识点');
 await click(handle);await waitMode(true);await key('Enter','Enter',13);await waitMode(false);await key(' ','Space',32);await waitMode(true);await key(' ','Space',32);await waitMode(false);
 assert(await executions()===0,'changing mode executed skip');checks.push('点击/Enter/空格只切换不执行');
 await metrics(600,'sakura');await js(`document.querySelector('${handle}').blur()`);const hover=await bounds();await mouse('mouseMoved',hover.x,hover.y);await pause(200);
 assert(await js(`getComputedStyle(document.querySelector('.skip127-hint')).visibility==='visible'`),'hover hint missing');
 assert(await js(`getComputedStyle(document.querySelector('.skip127-thumb svg')).transform!=='none'`),'hover arrow did not move');await shot('hover127-sakura-narrow.png');checks.push('窄窗独立拉手分隔/高亮/右移/提示');
 await drag(.8);await waitMode(true);await metrics(600,'aurora');await mouse('mouseMoved',5,5);await js(`document.querySelector('${handle}').blur()`);await shot('module127-aurora-narrow.png');
 await click('.skip127-main');await until(`!!document.querySelector('[aria-label="确认跳过该模块"]')`);assert(await executions()===0,'module ran without confirmation');
 await button('取消');assert(await mode(),'cancel changed scope');assert(await executions()===0,'cancel ran module');
 await click('.skip127-main');await button('确认，进入直接考');await until(`!document.querySelector('${handle}').disabled`);await waitMode(false);
 assert(await js(`window.calls127.filter(c=>c.method==='classroom.test'&&c.params.kind==='skip_test').length===1`),'module not wired to direct test');checks.push('模块确认前不执行；取消不跳；确认接原直接考，成功后复位（同一当前点）');
 await click('.skip127-main');await until('window.calls127.some(c=>c.method==="classroom.skip_point")');assert(!await js(`!!document.querySelector('[aria-label="确认跳过该模块"]')`),'point opened confirmation');await waitMode(false);checks.push('知识点直接跳、不弹窗');
 await click(handle);await waitMode(true);await js('window.mount127()');await waitMode(false);checks.push('退出再进复位');
 await js('window.reduced127(true)');await click(handle);await waitMode(true);assert(await js(`getComputedStyle(document.querySelector('.skip127-word')).animationName==='none'&&getComputedStyle(document.querySelector('.skip127-thumb')).transitionDuration==='0s'`),'app reduced motion ignored');
 await js('window.reduced127(false)');await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await click(handle);await waitMode(false);assert(await js(`getComputedStyle(document.querySelector('.skip127-word')).animationName==='none'`),'system reduced motion ignored');await call('Emulation.setEmulatedMedia',{features:[]});checks.push('应用/系统减少动态效果均无动画');
 await button('没太懂');await until(`document.querySelector('${handle}').disabled`);assert(await js(`document.querySelector('.skip127-main').disabled`),'main enabled while busy');const n=await executions();await js(`document.querySelector('${handle}').click();document.querySelector('.skip127-main').click()`);assert(!await mode()&&await executions()===n,'busy control executed');await js('window.finishSkip127()');await until(`!document.querySelector('${handle}').disabled`);await js(`document.querySelector('${handle}').click()`);await waitMode(true);await click(handle);await waitMode(false);checks.push('busy主体/拉手均禁用；恢复后辅助技术click可用');
 await js('window.mount127(true)');assert(await js(`document.querySelector('.skip127-main').disabled&&!document.querySelector('${handle}').disabled`),'no-point availability wrong');await click(handle);await waitMode(true);assert(!await js(`document.querySelector('.skip127-main').disabled`),'module unavailable without point');checks.push('没有当前点只禁知识点，拉手仍可切模块');
 result={ok:true,checks};
}catch(e){result={ok:false,checks,error:String(e.stack||e),dom:await js('document.body.innerText').catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'result127.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
