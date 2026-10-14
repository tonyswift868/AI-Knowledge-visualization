import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,before=process.argv.includes('--before');
if(!endpoint?.startsWith('http://127.0.0.1:'))throw Error('owned local CDP endpoint required');
const pause=(ms=35)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map(),checks=[];
ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
function assert(value,message){if(!value)throw Error(message)}
async function until(expression){for(let i=0;i<250;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function button(label){await js(`(()=>{const e=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)}&&!b.disabled);if(!e)throw Error('missing '+${JSON.stringify(label)});e.click()})()`);await pause()}
async function metrics(width,theme){await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);await js(`Promise.all(document.getAnimations().filter(a=>a.effect?.target?.classList?.contains('class-page')).map(a=>a.finished))`);await pause(150);assert(!await js('document.documentElement.scrollWidth>innerWidth'),'horizontal overflow')}
async function shot(name){const size=await js('({width:innerWidth,height:innerHeight})');const r=await call('Page.captureScreenshot',{format:'png',clip:{x:0,y:0,...size,scale:1}});fs.writeFileSync(path.join(root,name),Buffer.from(r.data,'base64'))}
const geometry=()=>js(`(()=>{const s=document.querySelector('.class-scroll'),c=s.querySelector('.recap-card').parentElement,n=c.nextElementSibling;return {delta:c.getBoundingClientRect().top-s.getBoundingClientRect().top,gap:n?n.getBoundingClientRect().top-c.getBoundingClientRect().bottom:null,next:n?.dataset.turnRole,recaps:s.querySelectorAll('.recap-card').length,continue:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='继续')}})()`);
let result;
try{
 await until('!!window.ready130');await js('window.ready130');const rows=[];
 for(const [width,theme] of [[1000,'sakura'],[600,'aurora']]){
  await js(`window.mount130('normal')`);await metrics(width,theme);await button('懂了，继续');await until(`!!document.querySelector('.recap-card')`);
  if(before){assert((await geometry()).continue,'baseline missing extra continue');await shot(`before130-${theme}.png`)}
  else{
   await until(`document.querySelector('.class-subpoint129').textContent.includes('小要点 1/2')`);
   const g=await geometry();assert(Math.abs(g.delta-12)<2,'recap not at top '+JSON.stringify(g));assert(g.next==='assistant'&&g.gap>=0&&g.gap<20,'teacher not immediately below card '+JSON.stringify(g));assert(!g.continue&&g.recaps===1,'extra continue/card');
   rows.push({width,theme,...g});await shot(`after130-${theme}.png`);
  }
 }
 if(!before){
  checks.push('真实课堂同操作卡片贴顶，老师紧接其下，无额外继续；樱花宽窗/极光夜窄窗无溢出');
  await js(`window.mount130('user')`);await metrics(1000,'sakura');await button('懂了，继续');await until(`!!document.querySelector('[data-turn-key="user-continue130"]')`);
  assert(Math.abs((await geometry()).delta-12)<2,'user bubble overrode recap anchor');checks.push('同一操作有学生气泡时，要点卡优先');
  await js(`(()=>{const s=document.querySelector('.class-scroll');s.dispatchEvent(new WheelEvent('wheel',{deltaY:-100}));s.scrollTop=100})()`);
  const anchor=await js(`(()=>{const s=document.querySelector('.class-scroll'),t=[...s.querySelectorAll('[data-turn-key]')].find(e=>e.getBoundingClientRect().bottom>s.getBoundingClientRect().top+1);return {key:t.dataset.turnKey,offset:Math.max(0,t.getBoundingClientRect().top-s.getBoundingClientRect().top)}})()`);
  await js(`document.querySelector('[aria-label="只看重点"]').click()`);
  const offset=await js(`(()=>{const s=document.querySelector('.class-scroll'),t=[...s.querySelectorAll('[data-turn-key]')].find(e=>e.dataset.turnKey===${JSON.stringify(anchor.key)});return t.getBoundingClientRect().top-s.getBoundingClientRect().top})()`);
  assert(Math.abs(offset-anchor.offset)<2,'BUG-93 focus toggle lost viewport anchor');checks.push('只看重点切换仍保留当前视口消息');
  await js(`window.mount130('inflight')`);await button('懂了，继续');await until(`typeof window.finish130==='function'`);
  await js(`(()=>{const s=document.querySelector('.class-scroll');s.dispatchEvent(new WheelEvent('wheel',{deltaY:-100}));s.scrollTop=0;window.finish130()})()`);await until(`!!document.querySelector('.recap-card')`);
  assert(await js(`document.querySelector('.class-scroll').scrollTop===0`),'recap stole deliberate upward scroll');checks.push('用户向上翻后，最终卡片不抢滚动');
 }
 result={ok:true,before,checks,rows};
}catch(e){result={ok:false,before,checks,error:String(e.stack||e),dom:await js('document.body.innerText').catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'result130.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
