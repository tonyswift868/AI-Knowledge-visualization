import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root=path.resolve(process.argv[2]);
const endpoint=process.env.MF_CDP_ENDPOINT;
const artifacts=process.env.UPDATE194_ARTIFACTS || path.join(root,'screenshots');
fs.mkdirSync(artifacts,{recursive:true});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let target;
for(let i=0;i<150;i++){
  try {
    target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));
    if(target)break;
  } catch {}
  await pause(100);
}
if(!target)throw Error('owned Qt WebEngine page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
let nextId=0;
const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++nextId;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause(30)}throw Error('timeout '+expression+'; body='+await js('document.body.innerText'))}
function check(ok,message){if(!ok)throw Error(message)}
async function state(expected,modalOpen){
  await until(`document.querySelectorAll('[data-testid="update-status"][role="status"][aria-live="polite"]').length===1`);
  const got=await js(`(()=>{const nodes=[...document.querySelectorAll('[data-testid="update-status"][role="status"][aria-live="polite"]')];const n=nodes[0];const d=n.closest('[role="dialog"]');const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';return {count:nodes.length,text:n.innerText,insideDialog:!!d&&visible(d),statusVisible:visible(n),modalCount:[...document.querySelectorAll('[role="dialog"][aria-label="MentorFlow 更新"]')].filter(visible).length,progressContainers:[...document.querySelectorAll('[data-testid="update-progress"]')].filter(visible).length,progressBars:[...document.querySelectorAll('progress[aria-label="下载进度"]')].filter(visible).length,progressText:[...document.querySelectorAll('[data-testid="update-progress"]')].filter(visible).map(x=>x.innerText).join(' ')}})()`);
  check(got.count===1,'expected one semantic update status, got '+JSON.stringify(got));
  check(got.statusVisible,'update status is not visibly rendered: '+JSON.stringify(got));
  check(got.modalCount===(modalOpen?1:0),'unexpected modal count: '+JSON.stringify(got));
  check(got.insideDialog===modalOpen,'status must move to the modal only while modal is open: '+JSON.stringify(got));
  if(expected)check((await js('document.body.innerText')).includes(expected),'visible update state does not include '+expected+': '+JSON.stringify(got));
  return got;
}
async function visibleTextCount(text){return await js(`(()=>{const body=document.body.innerText;return body.split(${JSON.stringify(text)}).length-1})()`)}
async function noGlobalWait(){
  const visible=await js(`(()=>[...document.querySelectorAll('.wait-global')].some(e=>e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden'))()`);
  check(!visible,'update request produced a second global loading card');
}
async function screenshot(name,modal){
  const params={format:'png',captureBeyondViewport:!modal};
  if(!modal){
    const rect=await js(`(()=>{const r=document.querySelector('section[aria-label="关于 MentorFlow"]')?.getBoundingClientRect();return r&&{x:r.x,y:r.y+window.scrollY,width:r.width,height:r.height}})()`);
    if(!rect)throw Error('About update card missing for focused status screenshot');
    params.clip={...rect,scale:1};
  }
  const result=await call('Page.captureScreenshot',params);
  fs.writeFileSync(path.join(artifacts,name+'.png'),Buffer.from(result.data,'base64'));
}
async function configure(status,open,manual=false,error=''){
  await js(`window.set194Status(${JSON.stringify(status)},${JSON.stringify(open)},${JSON.stringify(manual)},${JSON.stringify(error)})`);
}

try {
  await until('window.ready194===true && !!document.querySelector("nav[aria-label]")');
  await until(`window.fixture194.calls.includes('update.automatic') && window.fixture194.pending===0`);
  const summaries=[];
  const base={available:true,busy:false,state:'CHECKING',version:'9.194.0',current:'9.194.0',message:'正在检查更新…'};

  // The compact Settings surface owns idle/checking/latest/error; an automatic offer
  // moves the one semantic status into the global modal while Settings stays mounted.
  await js('window.set194Theme("sakura")');
  await configure(base,false,false);
  summaries.push({theme:'sakura',phase:'checking',...(await state('正在检查更新',false))});
  check(await visibleTextCount('正在检查更新')===1,'checking message appears more than once');

  await js(`(async()=>{window.fixture194.response=${JSON.stringify({available:true,busy:false,state:'OPTIONAL_UPDATE',version:'9.194.0',current:'9.194.0',latest:'9.194.1',message:'发现新版本。',release_notes:'更新状态唯一展示测试'})}; window.set194Theme('sakura');window.set194Route('home'); await window.check194Automatically()})()`);
  await until('!!document.querySelector(\'[role="dialog"][aria-label="MentorFlow 更新"]\')');
  const automatic=await state('9.194.1',true);
  check(await visibleTextCount(automatic.text)===1,'automatic offer status message appears more than once across Settings and modal');
  summaries.push({theme:'sakura',phase:'automatic-offer',...automatic});
  check(await js(`!![...document.querySelectorAll('[role="dialog"][aria-label="MentorFlow 更新"] button')].find(b=>b.innerText.includes('立即更新并重启'))`),'automatic offer lost its update action');

  // A click on the actual Settings button starts a manual request. When an update
  // is found, the global modal takes over the one status slot from About.
  await js(`window.set194Status({available:true,busy:false,state:'UP_TO_DATE',version:'9.194.0',current:'9.194.0',message:'已是最新版本。'},false,false);window.fixture194.response=${JSON.stringify({available:true,busy:false,state:'OPTIONAL_UPDATE',version:'9.194.0',current:'9.194.0',latest:'9.194.2',message:'发现新版本。'})};window.set194Theme('aurora');window.set194Route('settings');`);
  await js(`window.fixture194.hold=true`);
  await js(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='检查更新');if(!b)throw Error('Settings manual update button missing');b.click();return true})()`);
  await until(`window.fixture194.calls.includes('update.check')&&window.fixture194.pending===1&&!!window.fixture194.releasePending`);
  await noGlobalWait();
  await until(`document.querySelector('[data-testid="update-status"]')?.innerText.includes('正在检查更新')`);
  const manualChecking=await state('正在检查更新',false);
  check(await visibleTextCount(manualChecking.text)===1,'manual checking status is duplicated while the real check bridge is pending');
  summaries.push({theme:'aurora',phase:'manual-checking-pending',...manualChecking});
  await js(`window.fixture194.releasePending();window.fixture194.hold=false`);
  await until(`document.body.innerText.includes('9.194.2')`);
  const manual=await state('9.194.2',true);
  summaries.push({theme:'aurora',phase:'manual-offer',...manual});
  check(await visibleTextCount(manual.text)===1,'manual offer status message appears more than once across Settings and modal');
  check(await js(`!![...document.querySelectorAll('[role="dialog"][aria-label="MentorFlow 更新"] button')].find(b=>b.innerText.includes('立即更新并重启'))`),'manual update offer lost its global update action');

  await js(`window.fixture194.hold=true`);
  await js(`(()=>{const b=[...document.querySelectorAll('[role="dialog"][aria-label="MentorFlow 更新"] button')].find(x=>x.innerText.includes('立即更新并重启'));if(!b)throw Error('global update action missing');b.click();return true})()`);
  await until(`window.fixture194.calls.includes('update.install')&&window.fixture194.pending===1&&!!window.fixture194.releasePending`);
  await noGlobalWait();
  await until(`document.querySelector('[data-testid="update-status"]')?.innerText.includes('正在准备新版本')`);
  const preparing=await state('正在准备新版本',true);
  check(await visibleTextCount(preparing.text)===1,'pending install preparation status is not unique');
  await js(`window.fixture194.releasePending();window.fixture194.hold=false`);
  const progress=await state('9.194.2',true);
  check(progress.progressContainers===1&&progress.progressBars===1,'download progress must appear exactly once: '+JSON.stringify(progress));
  check(progress.text.includes('3/8')||progress.text.includes('3 / 8'),'progress lost file counts in the unique status message: '+JSON.stringify(progress));
  check(await visibleTextCount(progress.text)===1,'complete download status message appears more than once');
  summaries.push({theme:'aurora',phase:'downloading',...progress});

  await js(`(async()=>{window.fixture194.response=${JSON.stringify({available:true,busy:false,state:'UP_TO_DATE',version:'9.194.0',current:'9.194.0',message:'已是最新版本。'})};window.set194Theme('sakura');window.set194Route('home');await window.check194Automatically()})()`);
  await until(`!document.querySelector('[role="dialog"][aria-label="MentorFlow 更新"]')`);
  const latestAuto=await state('已是最新版本',false);
  check(await visibleTextCount('已是最新版本 9.194.0')===1,'automatic latest state is duplicated');
  summaries.push({theme:'sakura',phase:'latest-automatic',...latestAuto});

  // On the Settings route, a manual successful check must not add a duplicate toast.
  await js(`window.set194Route('settings');window.set194Status({available:true,busy:false,state:'UP_TO_DATE',version:'9.194.0',current:'9.194.0',message:'已是最新版本。'},false,false);`);
  await js(`window.fixture194.hold=true`);
  await js(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='检查更新');if(!b)throw Error('Settings manual update button missing');b.click();return true})()`);
  await until(`window.fixture194.calls.filter(x=>x==='update.check').length>=2&&window.fixture194.pending===1&&!!window.fixture194.releasePending`);
  await noGlobalWait();
  await js(`window.fixture194.releasePending();window.fixture194.hold=false`);
  await until(`window.fixture194.pending===0`);
  const latestManual=await state('已是最新版本',false);
  check(await visibleTextCount('已是最新版本 9.194.0')===1,'manual latest state is duplicated, likely with a toast');
  summaries.push({theme:'sakura',phase:'latest-manual',...latestManual});

  // Exercise an error through the real manual Settings action rather than setting
  // only the Zustand store, then require one visible localized failure status.
  await js(`window.fixture194.fail=true;window.set194Theme('aurora');`);
  await js(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='检查更新');if(!b)throw Error('Settings manual update button missing');b.click();return true})()`);
  await until(`document.body.innerText.includes('更新服务暂时无法连接')`);
  const manualError=await state('更新服务暂时无法连接',false);
  check(await visibleTextCount('更新服务暂时无法连接。')===1,'manual update error is duplicated, likely with a toast');
  summaries.push({theme:'aurora',phase:'manual-error',...manualError});

  // Stale progress/prepared metadata must not mask a terminal failure or APPLYING.
  const staleFailure={available:true,busy:false,state:'FAILED_RECOVERABLE',version:'9.194.0',current:'9.194.0',
    prepared:true,download_state:'DOWNLOADING',message:'更新暂时无法完成。'};
  await configure(staleFailure,false,false,'');
  const failed=await state('更新暂时无法完成',false);
  check(await visibleTextCount('更新暂时无法完成。')===1,'terminal update failure is hidden or duplicated by stale download flags');
  summaries.push({theme:'aurora',phase:'stale-download-failure',...failed});
  const staleApplying={available:true,busy:true,state:'APPLYING',version:'9.194.0',current:'9.194.0',
    prepared:true,download_state:'DOWNLOADING',message:'正在安装更新，完成后将自动重启。'};
  await configure(staleApplying,false,false,'');
  const applying=await state('正在安装更新',false);
  check(await visibleTextCount('正在安装更新，完成后将自动重启。')===1,'applying state is hidden or duplicated by stale download flags');
  summaries.push({theme:'aurora',phase:'stale-download-applying',...applying});

  // A compact matrix checks every representative status in both themes on the
  // real composed page. Count the complete status sentence, not a bare version
  // number that is also legitimately shown in the current→target metadata.
  const matrix=[
    {phase:'checking',modal:false,expected:'正在检查更新…',status:{available:true,busy:true,state:'CHECKING',version:'9.194.0',current:'9.194.0',message:'正在检查更新…'}},
    {phase:'offer',modal:true,expected:'发现新版本 9.194.2。',status:{available:true,busy:false,state:'OPTIONAL_UPDATE',version:'9.194.0',current:'9.194.0',latest:'9.194.2',message:'发现新版本 9.194.2。',release_notes:'主题矩阵测试'}},
    {phase:'downloading',modal:true,expected:'正在下载 3/8 个文件（3.00 MB / 8.00 MB）',status:{available:true,busy:true,state:'DOWNLOADING',download_state:'DOWNLOADING',version:'9.194.0',current:'9.194.0',latest:'9.194.2',downloaded_bytes:3145728,expected_download_bytes:8388608,downloaded_files:3,total_download_files:8}},
    {phase:'latest',modal:false,expected:'已是最新版本 9.194.0',status:{available:true,busy:false,state:'UP_TO_DATE',version:'9.194.0',current:'9.194.0',message:'已是最新版本。'}},
    {phase:'error',modal:false,expected:'更新服务暂时无法连接。',status:{available:true,busy:false,state:'FAILED_RECOVERABLE',version:'9.194.0',current:'9.194.0',message:'更新服务暂时无法连接。'}},
  ];
  for(const item of matrix){
    for(const theme of ['sakura','aurora']){
      await js(`window.set194Theme(${JSON.stringify(theme)});window.set194Route('settings');window.fixture194.response=${JSON.stringify(item.status)};window.set194Status(${JSON.stringify(item.status)},${item.modal},${item.modal});`);
      const snapshot=await state(item.expected,item.modal);
      check(await visibleTextCount(item.expected)===1,`${item.phase}/${theme}: full state message is not unique (${await visibleTextCount(item.expected)})`);
      const expectedBg=theme==='sakura'?'#fbf4f7':'#0c0e1c';
      const actualTheme=await js(`(()=>({theme:document.documentElement.dataset.theme,bg:getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()}))()`);
      check(actualTheme.theme===theme&&actualTheme.bg===expectedBg,`${item.phase}/${theme}: actual document theme did not apply: ${JSON.stringify(actualTheme)}`);
      if(!item.modal) await js(`(()=>{let n=document.querySelector('[data-testid="update-status"]');if(n)n.scrollIntoView({block:'center'});for(let e=n?.parentElement;e;e=e.parentElement){if(e.scrollHeight>e.clientHeight+4)e.scrollTop=e.scrollHeight}})()`);
      if(item.phase==='downloading'){
        const current=await state(item.expected,true);
        check(current.text===snapshot.text&&current.progressBars===1,`${theme}: download screenshot state changed before capture: ${JSON.stringify(current)}`);
      }
      if(item.phase==='downloading')check(snapshot.progressContainers===1&&snapshot.progressBars===1,`${theme}: download progress missing/duplicated`);
      summaries.push({theme,phase:`matrix-${item.phase}`,...snapshot});
      await screenshot(`${item.phase}-${theme}`,item.modal);
    }
  }

  fs.writeFileSync(path.join(root,'update194-browser.json'),JSON.stringify({checks:summaries,automaticCheckCalls:await js(`window.fixture194.calls.filter(x=>x==='update.automatic').length`),manualCheckCalls:await js(`window.fixture194.calls.filter(x=>x==='update.check').length`)},null,2));
  check(await js(`window.fixture194.calls.includes('update.automatic')`),'automatic update path was not exercised');
  check(await js(`window.fixture194.calls.includes('update.check')`),'manual update path was not exercised');
} finally { ws.close() }
