import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=30)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<200;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function click(selector){const p=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);await call('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',clickCount:1})}
const checks=[];let result;
function check(value,label){if(!value)throw Error(label);checks.push(label)}
try{
 await until('!!document.querySelector(".chat-copy")');
 check(await js(`document.querySelectorAll('.chat-copy').length===1 && !document.querySelector('.bubble.user .chat-copy') && !!document.querySelector('.bubble.bot[data-selectable]')`),'only completed teacher reply has copy; selectable preserved');
 await js(`(async()=>{window.native140={available:!!navigator.clipboard?.writeText,permission:(await navigator.permissions.query({name:'clipboard-write'})).state,success:0,errors:[]};if(navigator.clipboard?.writeText){const original=navigator.clipboard.writeText.bind(navigator.clipboard);navigator.clipboard.writeText=async t=>{try{await original(t);window.native140.success++}catch(e){window.native140.errors.push(e.name+': '+e.message);throw e}}}})()`);
 await click('.chat-copy');await until(`document.querySelector('.chat-copy').textContent.includes('已复制')`);
 const native=await js('window.native140');
 await until(`document.querySelector('.chat-copy').textContent.trim()==='复制'`);checks.push('native QWebEngine attempt and two-second feedback restoration');
 // Browser success path must not invoke the Qt fallback.
 const before=fs.existsSync(path.join(root,'clipboard140.jsonl'))?fs.readFileSync(path.join(root,'clipboard140.jsonl'),'utf8'):'';
 await js(`window.browserTexts140=[];navigator.permissions.query=async()=>({state:'granted'});Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async t=>{window.browserTexts140.push(t)}}})`);
 await click('.chat-copy');await until(`document.querySelector('.chat-copy').textContent.includes('已复制')`);
 const browserText=await js('window.browserTexts140[0]');
 check((fs.existsSync(path.join(root,'clipboard140.jsonl'))?fs.readFileSync(path.join(root,'clipboard140.jsonl'),'utf8'):'')===before,'browser success does not call fallback');
 await until(`document.querySelector('.chat-copy').textContent.trim()==='复制'`);
 // Unavailable browser clipboard uses the actual production Qt handler.
 await js(`Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})`);
 await click('.chat-copy');await until(`document.querySelector('.chat-copy').textContent.includes('已复制')`);
 const writes=fs.readFileSync(path.join(root,'clipboard140.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
 check(writes.at(-1).text===browserText&&writes.at(-1).actual===browserText,'native Qt clipboard and browser both preserve exact Markdown, including trailing newline');
 await until(`document.querySelector('.chat-copy').textContent.trim()==='复制'`);
 await js(`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('Permission denied')}}})`);
 await click('.chat-copy');await until(`document.querySelector('.chat-copy').textContent.includes('已复制')`);checks.push('rejected browser write falls back to production Qt handler');
 await until(`document.querySelector('.chat-copy').textContent.trim()==='复制'`);
 fs.writeFileSync(path.join(root,'reject'),'');await js(`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('Permission denied')}}})`);
 await click('.chat-copy');await until(`!!document.querySelector('[role="alert"]')`);
 check(await js(`document.querySelector('[role="alert"]').textContent.includes('复制失败') && document.querySelector('.chat-copy').textContent.trim()==='复制'`),'both clipboard routes fail with visible error and no false success');
 fs.unlinkSync(path.join(root,'reject'));
 // Hold streaming open in the local fake chat handler; no model request.
 await js(`(()=>{const e=document.querySelector('.composer textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'再说一句');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 await until(`!document.querySelector('.composer button').disabled`);await click('.composer button');
 await until(`!!document.querySelector('.bubble.streaming')`);
 check(await js(`!document.querySelector('.bubble.streaming .chat-copy') && document.querySelectorAll('.chat-copy').length===1`),'streaming teacher reply and new user message have no copy button');
 fs.writeFileSync(path.join(root,'finish'),'');await until(`document.querySelectorAll('.chat-copy').length===2 && !document.querySelector('.bubble.streaming')`);checks.push('copy appears after stream completes');
 await js(`window.clearToasts140();document.querySelector('.chat-scroll').scrollTop=0;window.scrollTo(0,0)`);
 const sizes=[];
 for(const theme of ['sakura','aurora']){
  await call('Emulation.setDeviceMetricsOverride',{width:600,height:850,deviceScaleFactor:1,mobile:false});
  await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);await pause(400);
  const fits=await js(`(()=>{const e=document.querySelector('.chat-copy'),r=e.getBoundingClientRect(),b=e.closest('.bubble').getBoundingClientRect();return{viewport:innerWidth,document:document.documentElement.scrollWidth,left:r.left,right:r.right,bubbleRight:b.right}})()`);
  check(fits.document<=fits.viewport+1&&fits.left>=0&&fits.right<=fits.viewport,theme+' narrow layout without overflow');sizes.push({theme,...fits});
  const shot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,theme+'140.png'),Buffer.from(shot.data,'base64'));
 }
 result={ok:true,native,checks,browserText,writes,sizes};
}catch(e){result={ok:false,checks,error:String(e.stack||e),dom:await js('document.body.innerText').catch(()=>null),diagnostic:await js(`navigator.permissions.query({name:'clipboard-write'}).then(p=>({permission:p.state,native:window.native140,button:document.querySelector('.chat-copy')?.outerHTML,focus:document.hasFocus()}))`).catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'result140.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
