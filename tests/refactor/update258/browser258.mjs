import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root=path.resolve(process.argv[2]), artifacts=path.resolve(process.argv[3]);
const endpoint=process.env.MF_CDP_ENDPOINT;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let target;
for(let i=0;i<150;i++){
  try { target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep)); if(target)break } catch {}
  await pause(100);
}
if(!target)throw Error('owned Qt WebEngine page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
let nextId=0;const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++nextId;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause(30)}throw Error('timeout '+expression+'; body='+await js('document.body.innerText'))}
function check(ok,message){if(!ok)throw Error(message)}
async function banner(){return await js(`(()=>{const e=document.querySelector('[aria-label="软件更新提示"]');return !!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden'})()`)}
async function screenshot(name){const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(path.join(artifacts,name),Buffer.from(r.data,'base64'))}
async function paint(){await js(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`)}

try {
  await until('window.ready258===true');
  await until(`!!document.querySelector('[aria-label="软件更新提示"]')`);
  check(await js(`!![...document.querySelectorAll('[aria-label="软件更新提示"] button')].find(b=>b.innerText.trim()==='立即更新')`),'missing immediate update action');
  check(await js(`!![...document.querySelectorAll('[aria-label="软件更新提示"] button')].find(b=>b.innerText.trim()==='我知道了')`),'missing dismiss action');
  const originalWidth=await js('window.innerWidth');
  await call('Emulation.setDeviceMetricsOverride',{width:600,height:900,deviceScaleFactor:1,mobile:false});
  await until('window.innerWidth===600'); await paint();
  const narrowFit=await js(`(()=>{const e=document.querySelector('[aria-label="软件更新提示"]'),r=e?.getBoundingClientRect(),buttons=[...(e?.querySelectorAll('button')||[])].map(b=>{const x=b.getBoundingClientRect();return {left:x.left,right:x.right,width:x.width}});return {viewport:innerWidth,banner:r&&{left:r.left,right:r.right,width:r.width,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth},buttons}})()`);
  const narrowFits=!!narrowFit.banner&&narrowFit.banner.left>=0&&narrowFit.banner.right<=600&&narrowFit.banner.scrollWidth<=narrowFit.banner.clientWidth+1&&narrowFit.buttons.length===2&&narrowFit.buttons.every(b=>b.width>0&&b.left>=0&&b.right<=600);
  check(narrowFits,'update banner or action buttons overflow at 600px viewport: '+JSON.stringify(narrowFit));
  await call('Emulation.clearDeviceMetricsOverride');
  await until(`window.innerWidth===${originalWidth}`); await paint();
  await js('window.set258Theme("sakura")'); await paint(); await screenshot('light-banner258.png');
  await js('window.set258Theme("aurora")'); await paint(); await screenshot('dark-banner258.png');
  await js(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='我知道了');b.click()})()`);
  await until(`!document.querySelector('[aria-label="软件更新提示"]')`);
  await js('window.set258Route("plans")');
  await until(`document.querySelector('.content h1')?.innerText==='我的计划'`);
  const dismissedAcrossRoute=!(await banner());
  const dismissedVersionStored=await js(`window.dismissed258()`);
  const updateIndicatorUnchanged=await js(`window.has258Update()`);
  for(let i=0;i<4;i++) await js(`window.set258Status({...window.fixture258.status})`);
  const sameVersionPollingHidden=!(await banner());
  await js(`window.set258Status({...window.fixture258.status,latest:'2.0.41'})`);
  await until(`!!document.querySelector('[aria-label="软件更新提示"]')`);
  const newVersionShown=await banner();
  await js(`(()=>{const b=[...document.querySelectorAll('[aria-label="软件更新提示"] button')].find(x=>x.innerText.trim()==='我知道了');b.click()})()`);
  await until(`!document.querySelector('[aria-label="软件更新提示"]')`);
  const secondDismissedVersion=await js(`window.dismissed258()`);
  await js(`window.set258Status({...window.fixture258.status,latest:'2.0.41',target:'2.0.41',state:'MANDATORY_UPDATE',mandatory:true})`);
  await until(`!!document.querySelector('[role="dialog"][aria-label="MentorFlow 更新"]')`);
  const mandatoryState=await js(`(()=>{const shell=document.querySelector('.shell'),d=document.querySelector('[role="dialog"][aria-label="MentorFlow 更新"]'),b=[...document.querySelectorAll('[role="dialog"] button')].find(x=>x.innerText.includes('立即更新'));return {locked:!!shell?.inert,dialog:!!d,dialogOutsideLock:!!d&&!d.closest('[inert]'),noLater:!!d&&![...d.querySelectorAll('button')].some(x=>x.innerText.trim()==='稍后'),updateEnabled:!!b&&!b.disabled}})()`);
  check(mandatoryState.locked&&mandatoryState.dialog&&mandatoryState.dialogOutsideLock&&mandatoryState.noLater&&mandatoryState.updateEnabled,'mandatory modal should remain interactive outside the inert app shell: '+JSON.stringify(mandatoryState));
  await js(`(()=>{const b=[...document.querySelectorAll('[role="dialog"] button')].find(x=>x.innerText.includes('立即更新'));b.click()})()`);
  await until(`window.fixture258.calls.includes('update.install')`);
  const mandatoryModalAndLock=await js(`!!document.querySelector('.shell')?.inert && !!document.querySelector('[role="dialog"][aria-label="MentorFlow 更新"]')`);
  // Dismiss the same optional version immediately before reload so persistence
  // cannot pass merely because the new login sees a different version.
  await js(`window.set258Status({available:true,busy:false,state:'OPTIONAL_UPDATE',version:'2.0.39',current:'2.0.39',target:'2.0.40',message:'发现新版本。'})`);
  await until(`!!document.querySelector('[aria-label="软件更新提示"]')`);
  await js(`(()=>{const b=[...document.querySelectorAll('[aria-label="软件更新提示"] button')].find(x=>x.innerText.trim()==='我知道了');b.click()})()`);
  await until(`!document.querySelector('[aria-label="软件更新提示"]')`);
  check(await js(`window.dismissed258()`)==='2.0.40','pre-relogin dismissal did not target the version shown');
  await call('Page.reload',{ignoreCache:true});
  await until('window.ready258===true');
  await until(`!!document.querySelector('[aria-label="软件更新提示"]')`);
  const reloginShowsAgain=await banner();
  const reloginDismissedVersion=await js(`window.dismissed258()`);
  check(dismissedAcrossRoute,'dismissed banner returned after route switch');
  check(dismissedVersionStored==='2.0.40','dismiss did not store target fallback: '+dismissedVersionStored);
  check(updateIndicatorUnchanged===true,'dismissal changed hasUpdate indicator semantics');
  check(sameVersionPollingHidden,'same-version status refresh unhid dismissed banner');
  check(newVersionShown,'new available version did not show banner');
  check(secondDismissedVersion==='2.0.41','second dismissal did not store latest version: '+secondDismissedVersion);
  check(mandatoryModalAndLock,'mandatory update did not block the application after dismissal');
  check(reloginShowsAgain&&reloginDismissedVersion===null,'new page/login did not reset in-memory dismissal for same version');
  fs.writeFileSync(path.join(root,'update258-result.json'),JSON.stringify({dismissedAcrossRoute,dismissedVersionStored,updateIndicatorUnchanged,sameVersionPollingHidden,newVersionShown,secondDismissedVersion,mandatoryModalAndLock,reloginShowsAgain,reloginDismissedVersion,narrowFits,narrowFit},null,2));
} finally { ws.close() }
