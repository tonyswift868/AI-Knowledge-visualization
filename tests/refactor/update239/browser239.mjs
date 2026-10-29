import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=ms=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned update page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause(30)}throw Error('timeout '+expression+' BODY '+await js('document.body.innerText'))}
function assert(value,message){if(!value)throw Error(message)}
try{
  await until('!!(document.querySelector("#ready239") && document.querySelector("[aria-label=\\\"软件更新提示\\\"]"))');
  assert(!(await js('!!document.querySelector("[aria-label=\\\"MentorFlow 更新\\\"]")')),'automatic optional offer must not open a modal');
  assert(!(await js('document.querySelector(".shell").inert')),'optional offer must not block the main screen');
  await js('window.classroom239()');
  await until('document.querySelector("[data-route239=classroom]") && document.body.innerText.includes("发现新版本")');
  assert(await js(`(()=>{const e=document.querySelector('[aria-label="软件更新提示"]');const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&!!e.closest('.main')})()`),'inline banner must be visible inside main layout, not below full-height shell');
  assert(!(await js('document.querySelector(".shell").inert')),'optional banner also leaves classroom usable');
  await js('window.manualCheck239()');
  await until('!!(document.querySelector("[aria-label=\\\"软件更新提示\\\"]") && window.fixture239.calls.includes("update.check"))');
  assert(!(await js('!!document.querySelector("[aria-label=\\\"MentorFlow 更新\\\"]")')),'manual optional check must remain non-modal');
  await js(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='立即更新').click()`);
  await until('!!(window.fixture239.calls.includes("update.install") && document.querySelector("[aria-label=\\\"MentorFlow 更新\\\"]"))');
  assert(await js('!!document.querySelector("[aria-label=\\\"MentorFlow 更新\\\"]")'),'explicit update click may open progress dialog');
  console.log(JSON.stringify({automaticOptionalNonModal:true,manualOptionalNonModal:true,clickStartsInstall:true}));
}finally{ws.close()}
