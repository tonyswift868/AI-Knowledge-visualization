import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]), endpoint=process.env.MF_CDP_ENDPOINT;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let target;
for(let i=0;i<150;i++){
  try {target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break;}catch{}
  await pause(100);
}
if(!target)throw Error('owned offscreen Qt page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
let id=0;const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<200;i++){if(await js(expression))return;await pause(30)}throw Error('timeout: '+expression+'; '+await js('document.body.innerText'))}
const message='新版已下载好，下次打开软件时完成更新。';let checks=0;
try {
  await until('window.ready194===true && window.fixture194.calls.includes("update.automatic") && window.fixture194.pending===0');
  for(const theme of ['sakura','aurora']){
    for(const state of ['PENDING_INSTALL','OPTIONAL_UPDATE','HEALTHY']){
      const status={available:true,busy:false,state,version:'2.0.36',current:'2.0.36',latest:'2.0.37',
        prepared:true,deferred_update:true,deferred_target:'2.0.37',deferred_message:message,message,
        release_notes:'目录暂时被占用。当前版本与已下载的新版本均已保留。'};
      await js(`window.fixture194.response=${JSON.stringify(status)};window.set194Theme(${JSON.stringify(theme)});window.set194Status(${JSON.stringify(status)},true,false);`);
      await until(`document.querySelector('[data-testid="update-status"]')?.innerText===${JSON.stringify(message)}`);
      if(await js('document.body.innerText.includes("同步失败") || document.body.innerText.includes("更新暂时无法完成")'))throw Error('deferred activation shown as failure');
      if(!await js(`!![...document.querySelectorAll('[role="dialog"] button')].find(b=>!b.disabled&&b.innerText.includes('更新'))`))throw Error('manual update action missing');
      checks++;
      if(state==='PENDING_INSTALL'){
        const shot=await call('Page.captureScreenshot',{format:'png'});
        fs.writeFileSync(path.join(process.env.UPDATE228_ARTIFACTS,`pending-${theme}.png`),Buffer.from(shot.data,'base64'));
      }
    }
  }
  await js('window.check194Manually()');
  const manualCheck=await js('window.fixture194.calls.includes("update.check")');
  if(!manualCheck)throw Error('manual check unavailable');
  fs.writeFileSync(path.join(root,'deferred228.json'),JSON.stringify({checks,manualCheck}));
} finally {ws.close();}
