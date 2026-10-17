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
  await until('!!(window.ready160 && document.querySelector(\'textarea[aria-label="课堂输入"]\'))');
  await until('window.fixture160.checks>0 && window.fixture160.status.state==="UP_TO_DATE"');
  await js(`(()=>{const el=document.querySelector('textarea[aria-label="课堂输入"]');const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(el,'还没发送的课堂输入');el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
  await until(`document.querySelector('textarea[aria-label="课堂输入"]').value==='还没发送的课堂输入'`);
  assert(await js('localStorage.getItem("mf.classroom.unsent.account-A.plan160")==="还没发送的课堂输入"'),'draft key must include the authenticated account scope');
  await js('window.setAccount160("account-B")');
  await until(`document.querySelector('textarea[aria-label="课堂输入"]').value===''`);
  assert(await js('localStorage.getItem("mf.classroom.unsent.account-A.plan160")==="还没发送的课堂输入"'),'switching accounts must not delete the first account draft');
  await js('window.setAccount160("account-A")');
  await until(`document.querySelector('textarea[aria-label="课堂输入"]').value==='还没发送的课堂输入'`);
  await js('window.makeMandatory160()');
  await until('!!(window.fixture160.stateCalls===1 && document.querySelector(\'[aria-label="必须更新前保存课堂"]\'))');
  assert(await js('document.querySelector(".shell").inert'),'classroom must be inert while its progress is saved');
  assert(await js('localStorage.getItem("mf.classroom.unsent.account-A.plan160")==="还没发送的课堂输入"'),'unsent input must be persisted before update prompt');
  assert(!(await js('document.querySelector(\'[aria-label="MentorFlow 更新"]\')')),'update prompt must wait for classroom save');
  await js('window.fixture160.releaseState()');
  await until('!!(window.fixture160.stateSaved && document.querySelector(\'[aria-label="MentorFlow 更新"]\'))');
  assert(await js('document.querySelector(".shell").inert'),'required update must block the underlying classroom');
  assert(await js('!![...document.querySelectorAll(\'[aria-label="MentorFlow 更新"] button\')].find(b=>b.textContent.includes("立即更新"))'),'mandatory update action must be present');
  assert(!(await js('[...document.querySelectorAll(\'[aria-label="MentorFlow 更新"] button\')].some(b=>b.textContent.includes("稍后"))')),'mandatory update must have no later action');
  const shot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'mandatory160.png'),Buffer.from(shot.data,'base64'));
  const calls=await js('window.fixture160.stateCalls');
  await js('window.makeOptional160()');
  await pause(250);
  assert(!(await js('document.querySelector(\'[aria-label="MentorFlow 更新"]\')')),'optional update must remain deferred in classroom');
  assert(!(await js('document.querySelector(".shell").inert')),'optional update must not block classroom');
  assert(await js('window.fixture160.stateCalls===1'),'optional update must not run mandatory-save guard');
  fs.writeFileSync(path.join(root,'browser160.json'),JSON.stringify({saveBeforePrompt:true,unsentInputPersisted:true,accountScopedDraft:true,mandatoryNoLater:true,underlayBlocked:true,optionalClassroomDeferred:true,stateCalls:calls}));
}finally{ws.close()}
