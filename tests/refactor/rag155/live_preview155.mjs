import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=(ms=50)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<240;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned live preview page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});let seq=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}throw Error('preview timeout '+expression)}
async function clickIn(selector){const p=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);if(!p)throw Error('citation missing '+selector);await call('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',clickCount:1})}
const captures=[];
try{
 await until(`!!document.querySelector('[data-testid="rag-page"]')&&document.querySelectorAll('.rag-message.assistant').length>=3`);
 await call('Emulation.setDeviceMetricsOverride',{width:1120,height:900,deviceScaleFactor:1,mobile:false});
 const articles=await js(`Array.from(document.querySelectorAll('.rag-message.assistant')).map(e=>e.dataset.testid)`);
 for(let n=0;n<articles.length;n++){
   const id=articles[n];await js(`(()=>{const e=document.querySelector('[data-testid="${id}"]');e?.scrollIntoView({block:'center'});const box=document.querySelector('[data-testid="rag-messages"]');if(box)box.scrollTop+=0})()`);await pause(180);
   const citation=await js(`(()=>{const e=document.querySelector('[data-testid="${id}"] [data-testid^="rag-citation-"]');return e?.getAttribute('data-testid')||''})()`);
   if(citation&&n<2){await clickIn(`[data-testid="${citation}"]`);await pause(150)}
   for(const theme of ['sakura','aurora']){await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)}`);await pause(220);const shot=await call('Page.captureScreenshot',{format:'png'});const file=path.join(root,`live-answer${n+1}-${theme}-155.png`);fs.writeFileSync(file,Buffer.from(shot.data,'base64'));captures.push(file)}
 }
 console.log(JSON.stringify({ok:true,captures}));
}finally{ws.close()}
