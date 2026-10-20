import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=50)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<240;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('fixture page not found');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let seq=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression,limit=500){for(let i=0;i<limit;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){const p=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);if(!p)throw Error('missing '+selector);await call('Input.dispatchMouseEvent',{type:'mousePressed',x:p.x,y:p.y,button:'left',clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',x:p.x,y:p.y,button:'left',clickCount:1});await pause(80)}
const result={ok:false,checks:[]};const check=(ok,label)=>{if(!ok)throw Error(label);result.checks.push(label)};
try{
 await until(`!!document.querySelector('[data-testid="rag-page"]')`);
 await until(`!!document.querySelector('[data-testid="rag-offline-notice"]')`);
 const first=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'../screenshots/offline-rag181.png'),Buffer.from(first.data,'base64'));
 await js("window.store181.getState().set({route:'home'})");await until(`!!document.querySelector('.home-page')`);await until(`document.querySelector('.topbar')?.innerText.includes('离线学习')`);check(await js(`document.querySelector('.banner')?.innerText.includes('当前离线，已使用此设备上次验证的账号进入')`),'offline account state appears on the real App home');const accountshot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'../screenshots/offline-account181.png'),Buffer.from(accountshot.data,'base64'));
 await js("window.store181.getState().set({route:'rag'})");await until(`!!document.querySelector('[data-testid="rag-offline-notice"]')`);
 fs.writeFileSync(path.join(root,'recover-marker181'),'');
 await until(`!document.querySelector('[data-testid="rag-offline-notice"]')`,800);
 check(await js(`document.body.innerText.includes('资料问答')`),'RAG service recovery clears the offline notice automatically');
 await until(`document.querySelector('.topbar')?.innerText.includes('已登录')`);await until(`!document.querySelector('.banner')`);
 const recoveredCount=JSON.parse(fs.readFileSync(path.join(root,'sources-count181.json'),'utf8')).calls;await pause(1800);check(JSON.parse(fs.readFileSync(path.join(root,'sources-count181.json'),'utf8')).calls===recoveredCount,'recovery cancels the stale poll schedule');
 const recovered=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'../screenshots/offline-recovered181.png'),Buffer.from(recovered.data,'base64'));
 await click('[data-testid="rag-document-doc-a"]');
 await click('[data-testid="rag-new-session"]');
 await until(`!document.querySelector('[data-testid="rag-question"]')?.disabled`);
 await js(`(()=>{const e=document.querySelector('[data-testid="rag-question"]');const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(e,'测试断线后手动重试');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 await until(`document.querySelector('[data-testid="rag-question"]')?.value==='测试断线后手动重试'`);
 fs.writeFileSync(path.join(root,'fail-next-send181'),'');await click('[data-testid="rag-send"]');
 await until(`!!document.querySelector('[data-testid="rag-retry-send"]')`);
 check(await js(`document.querySelector('[data-testid="rag-question"]')?.value==='测试断线后手动重试'`),'failed question text remains in the composer');
 const trace=fs.readFileSync(path.join(root,'bridge-trace181.txt'),'utf8');
 check((trace.match(/^send /gm)||[]).length===1,'failed question is never automatically resent');
 await click('[data-testid="rag-document-doc-c"]');await until(`!document.querySelector('[data-testid="rag-retry-send"]')`);check(true,'changing selected materials clears the stale retry');
 await click('[data-testid="rag-document-doc-c"]');fs.writeFileSync(path.join(root,'fail-next-send181'),'');await click('[data-testid="rag-send"]');await until(`!!document.querySelector('[data-testid="rag-retry-send"]')`);
 await pause(1800);const traceAgain=fs.readFileSync(path.join(root,'bridge-trace181.txt'),'utf8');check((traceAgain.match(/^send /gm)||[]).length===2,'question remains unsent until the user chooses an action');
 const probeStats=JSON.parse(fs.readFileSync(path.join(root,'sources-count181.json'),'utf8'));check(probeStats.max_active===1,'status probes never overlap');const stableProbeCount=probeStats.calls;await pause(1300);check(JSON.parse(fs.readFileSync(path.join(root,'sources-count181.json'),'utf8')).calls===stableProbeCount,'one settled recovery poll does not create another chain');
 await js("document.querySelector('[data-testid=\"rag-retry-send\"]')?.scrollIntoView({block:'center'})");await pause(150);const retryshot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'../screenshots/question-retry181.png'),Buffer.from(retryshot.data,'base64'));
 await click('[data-testid="rag-retry-send"]');
 await until(`!!document.querySelector('[data-testid="rag-streaming-reply"]')`);
 fs.writeFileSync(path.join(root,'release-stream181'),'');
 await until(`!!document.querySelector('[data-testid^="rag-message-"]')`);
 const traceAfter=fs.readFileSync(path.join(root,'bridge-trace181.txt'),'utf8');
 check((traceAfter.match(/^send /gm)||[]).length===3,'the explicit retry sends exactly once');
 await until(`!document.querySelector('[data-testid="rag-question"]')?.disabled`);
 await js(`(()=>{const e=document.querySelector('[data-testid="rag-question"]');const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(e,'检查密钥故障入口');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);fs.writeFileSync(path.join(root,'key-next-send181'),'');await click('[data-testid="rag-send"]');await until(`!!document.querySelector('[data-testid="rag-model-fault"]')`);check(await js(`document.querySelector('[data-testid="rag-model-fault"]')?.innerText.includes('去设置')`),'API key fault keeps its existing settings action');check(await js(`!document.querySelector('[data-testid="rag-retry-send"]')`),'non-network model faults do not get the network retry prompt');
 result.ok=true;
}catch(e){result.error=String(e.stack||e);result.body=await js('document.body.innerText').catch(()=> '');result.trace=fs.existsSync(path.join(root,'bridge-trace181.txt'))?fs.readFileSync(path.join(root,'bridge-trace181.txt'),'utf8'):''}
finally{fs.writeFileSync(path.join(root,'result181.json'),JSON.stringify(result,null,2));ws.close()}
