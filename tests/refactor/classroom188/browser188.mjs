import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
if(!endpoint?.startsWith('http://127.0.0.1:'))throw Error('owned local CDP endpoint required');
const pause=(ms=35)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned classroom188 page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();
ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
function assert(value,message){if(!value)throw Error(message)}
async function until(expression){for(let i=0;i<500;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){const p=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('missing '+${JSON.stringify(selector)});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',buttons:1,clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',buttons:0,clickCount:1});await pause()}
async function screenshot(name){const size=await js('({width:innerWidth,height:innerHeight})');const r=await cdp('Page.captureScreenshot',{format:'png',clip:{x:0,y:0,...size,scale:1}});fs.writeFileSync(path.join(root,'screenshots',name),Buffer.from(r.data,'base64'))}
let result;
try{
 await until('!!window.classroom188Result');result=await js('window.classroom188Result');assert(result.ok,JSON.stringify(result));
 fs.mkdirSync(path.join(root,'screenshots'),{recursive:true});
 for(const [state,name] of [['teaching','lesson'],['testOffer','test-wait']]){
   for(const [theme,suffix,width] of [['sakura','light',980],['aurora','dark-narrow',620]]){
     await cdp('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:false});
     await js(`(async()=>{document.documentElement.dataset.theme=${JSON.stringify(theme)};await window.mount188(${JSON.stringify(state)})})()`);
     await until(state==='teaching'?'document.body.textContent.includes("老师照着讲义解释面积与压强")':'document.body.textContent.includes("这个模块的题还在准备，好了再考")');
     await pause(180);assert(!(await js('document.documentElement.scrollWidth>innerWidth')),'horizontal overflow '+state+' '+theme);
     if(state==='testOffer')assert(await js(`document.querySelector('button')!==null`),'test wait view missing');
     await screenshot(`${name}-${suffix}.png`);
   }
 }
 result={...result,screenshots:['lesson-light.png','lesson-dark-narrow.png','test-wait-light.png','test-wait-dark-narrow.png']};
}catch(e){result={ok:false,error:String(e.stack||e),body:await js('document.body.innerText').catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'result188.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result?.ok)process.exitCode=1;
