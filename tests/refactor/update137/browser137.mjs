import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=ms=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause(30)}throw Error('timeout '+expression+' BODY '+await js('document.body.innerText'))}
function assert(value,message){if(!value)throw Error(message)}
try{
 await until(`!!window.ready137 && !!document.querySelector('[aria-label="关于 MentorFlow"]')`);
 await until('window.initial137()');
 for(const theme of ['sakura','aurora'])for(const phase of ['offer','download','prepare','ready']){
  await js(`window.phase137('${phase}','${theme}')`);
  const text=phase==='offer'?'发现新版本 9.137.2':phase==='download'?'正在下载 2/5 个文件（3.00 MB / 10.00 MB）':phase==='prepare'?'正在准备新版本…':'已下载好，点一下就能更新。';
  await until(`document.querySelector('[role="dialog"]')?.textContent.includes(${JSON.stringify(text)}) && !document.querySelector('[aria-label="关于 MentorFlow"]').textContent.includes(${JSON.stringify(text)})`);
  assert(await js(`document.querySelectorAll('[data-testid="update-status"]').length===1 && !!document.querySelector('[role="dialog"] [data-testid="update-status"]')`),'one update status is presented in the active dialog');
  assert(await js(`![...document.querySelectorAll('[role="dialog"] button')].find(b=>b.textContent.includes('立即更新并重启')).disabled`),'background work must allow consent');
  if(phase==='download')assert(await js(`document.querySelector('[role="dialog"] progress').value===3*1048576 && document.querySelector('[role="dialog"] progress').max===10*1048576`),'byte progress');
  const shot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,`update137-${theme}-${phase}.png`),Buffer.from(shot.data,'base64'));
 }
 assert(await js(`document.querySelector('#slow-generic').textContent.includes('比平时慢，请稍等…') && !document.querySelector('#slow-generic').textContent.includes('模型')`),'generic slow message');
 assert(await js(`document.querySelector('#slow-model').textContent.includes('比平时慢，正在等模型回复…')`),'model slow message');
 fs.writeFileSync(path.join(root,'browser137.json'),JSON.stringify({themes:2,phases:4,settings:true,notice:true,progress:true,generic:true,model:true}));
}finally{ws.close()}
