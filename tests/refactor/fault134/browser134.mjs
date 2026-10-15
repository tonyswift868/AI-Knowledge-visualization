import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=ms=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause(30)}throw Error('timeout: '+expression+' BODY '+await js('document.body.innerText.slice(-3000)'))}
function assert(value,message){if(!value)throw Error(message)}
async function shot(name){const r=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,name),Buffer.from(r.data,'base64'))}
try{
 await until('!!window.ready134');await js('window.ready134');await until('!!document.querySelector(".class-scroll")');
 for(const [theme,width] of [['sakura',1050],['aurora',1050],['sakura',520],['aurora',520]]){
  await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});await js(`window.scenario134('${theme}')`);
  await until('!!document.querySelector(".model-status.unknown")');await js('window.fail134()');
  await until('!!document.querySelector(".model-fault-toast") && !!document.querySelector(".model-status.error")');
  assert(await js(`document.querySelectorAll('.model-fault-toast').length===1 && !document.querySelector('.model-fault-toast').textContent.includes('PRIVATE')`),'one red safe warning');
  assert(await js(`!document.querySelector('.model-fault-toast button') && !document.querySelector('.model-fault-toast a')`),'warning bar has no buttons');
  await js('window.hide134()');await until('!document.querySelector(".model-fault-toast")');
  await js(`document.querySelector('.model-status').click()`);await until('!!document.querySelector(".model-fault-toast")');
  await js('window.hide134();window.fail134()');await until('!!document.querySelector(".model-fault-toast")');
  const box=await js(`(()=>{const r=document.querySelector('.model-fault-toast').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,width:innerWidth}})()`);assert(box.left>=0&&box.right<=box.width+1,'toast clipped '+JSON.stringify(box));
  assert(box.top<40&&Math.abs((box.left+box.right)/2-box.width/2)<2,'warning bar not at top centre '+JSON.stringify(box));
  await shot(`fault134-${theme}-${width}.png`);await js('window.success134()');
  await until('!!document.querySelector(".model-status.ok") && !document.querySelector(".model-fault-toast")');
 }
 await call('Emulation.setDeviceMetricsOverride',{width:1100,height:900,deviceScaleFactor:1,mobile:false});
 await js(`window.scenario134('sakura')`);await until('!!document.querySelector(".class-scroll")');
 await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='懂了，继续');if(!b)throw Error('continue missing');b.click()})()`);
 await until('!!document.querySelector(".class-scroll .model-fault-panel")');
 await until(`(()=>{const p=document.querySelector('.class-scroll .model-fault-panel').getBoundingClientRect(),s=document.querySelector('.class-scroll').getBoundingClientRect();return p.top>=s.top && p.bottom<=s.bottom})()`);
 assert(await js(`!document.querySelector('.class-scroll').textContent.includes('老师正在想')`),'loading replaced');await shot('fault134-classroom.png');
 await js('window.settings134()');await until(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='测试连接'&&!b.disabled)`);
 await js(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='测试连接').click()`);
 await until(`!!document.querySelector('.content .model-fault-panel')`);await shot('fault134-settings.png');
 assert(await js(`!!document.querySelector('.model-fault-toast')`),'settings failure shows the bar');await pause(6200);assert(await js(`!document.querySelector('.model-fault-toast')`),'warning bar should leave after 6 s');
 await js('window.fail134()');await until('!!document.querySelector(".model-fault-toast")');
 await js('window.success134()');await until('!document.querySelector(".model-fault-toast")');
 fs.writeFileSync(path.join(root,'browser134.json'),JSON.stringify({themes:2,widths:[1050,520],repeat:true,autoHide:true,chipRecovery:true,classroom:true,settings:true}));
}finally{ws.close()}
