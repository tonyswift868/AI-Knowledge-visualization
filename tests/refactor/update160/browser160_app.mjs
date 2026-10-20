import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=ms=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<400;i++){if(await js(expression))return;await pause(25)}throw Error('timeout '+expression+' BODY '+await js('document.body.innerText'))}
function assert(value,message){if(!value)throw Error(message)}
try{
  await until('!!(window.ready160 && window.fixture160.appStatusCalls>0 && document.querySelector(\'textarea[aria-label="课堂输入"]\'))');
  await js(`(()=>{const el=document.querySelector('textarea[aria-label="课堂输入"]');const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(el,'从真实App轮询收到强制更新前的输入');el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
  await until(`document.querySelector('textarea[aria-label="课堂输入"]').value==='从真实App轮询收到强制更新前的输入'`);
  assert(!(await js('localStorage.getItem("mf.classroom.unsent.anonymous.plan160")')),'pre-identity input must not be persisted under an anonymous namespace');
  await js('window.setAccount160("account-A")');
  await until('document.querySelector(\'textarea[aria-label="课堂输入"]\').value===""');
  assert(!(await js('localStorage.getItem("mf.classroom.unsent.v2:account-A.plan160")')),'identity arriving late must not migrate unowned input');
  await js(`(()=>{const el=document.querySelector('textarea[aria-label="课堂输入"]');const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(el,'身份确认后的课堂输入');el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
  await until('document.querySelector(\'textarea[aria-label="课堂输入"]\').value==="身份确认后的课堂输入"');
  // Simulate account-service readiness dropping while the classroom route is
  // still mounted. Mandatory status must remain observable and actionable.
  await js('window.setReady160(false)');
  const before=await js('window.fixture160.appStatusCalls');
  await js('window.fixture160.draftSetFail=true');
  await js('window.makeMandatory160()');
  await until('!!(window.fixture160.appStatusCalls>'+String(before)+' && document.querySelector(\'[aria-label="必须更新前保存课堂"] [role="alert"]\'))');
  assert(await js('window.fixture160.stateCalls===0 && window.fixture160.draftSetCalls===1'),'failed durable draft save must stop before progress save');
  assert(await js('document.querySelector(".shell").inert'),'failed save must keep the classroom locked');
  assert(!(await js('document.querySelector(\'[aria-label="MentorFlow 更新"]\')')),'failed durable save must not show the update modal');
  await js('window.fixture160.draftSetFail=false;[...document.querySelectorAll("button")].find(b=>b.textContent.includes("重试保存")).click()');
  await until('!!(window.fixture160.stateCalls===1)');
  assert(await js('window.fixture160.draftSetCalls===2 && window.fixture160.events.indexOf("draft_set",window.fixture160.events.indexOf("draft_set")+1)<window.fixture160.events.indexOf("classroom.state")'),'durable input save must finish before progress save');
  assert(await js('window.fixture160.durableDraft==="身份确认后的课堂输入"'),'mandatory barrier must persist unsent text to the account store');
  assert(await js('document.querySelector(".shell").inert'),'whole app must be locked while classroom state is saved');
  assert(await js('localStorage.getItem("mf.classroom.unsent.v2:account-A.plan160")==="身份确认后的课堂输入"'),'actual App path must persist current unsent input');
  assert(!(await js('document.querySelector(\'[aria-label="MentorFlow 更新"]\')')),'update modal must wait for bridge-save completion');
  await js('window.fixture160.releaseState()');
  await until('!!(window.fixture160.stateSaved && document.querySelector(\'[aria-label="MentorFlow 更新"]\'))');
  assert(!(await js('[...document.querySelectorAll(\'[aria-label="MentorFlow 更新"] button\')].some(b=>b.textContent.includes("稍后"))')),'mandatory modal must not offer later');
  await js('localStorage.removeItem("mf.classroom.unsent.v2:account-A.plan160");window.setRoute160("home");true');
  await pause(150);
  await js('window.setRoute160("classroom")');
  await until(`document.querySelector('textarea[aria-label="课堂输入"]').value==='身份确认后的课堂输入'`);
  assert(await js('window.fixture160.draftGetCalls>=2'),'re-entered classroom must request account scoped durable draft');
  fs.writeFileSync(path.join(root,'app-polling160.json'),JSON.stringify({actualApp:true,bridgePolledStatus:true,draftSaveFailureKeepsBarrier:true,savedBeforeModal:true,accountDraftPersisted:true,draftRestoredAfterLocalStorageLoss:true,mandatoryNoLater:true,bridgeCalls:await js('window.fixture160.appStatusCalls')}));
  const shot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'app-polling160.png'),Buffer.from(shot.data,'base64'));
}finally{ws.close()}
