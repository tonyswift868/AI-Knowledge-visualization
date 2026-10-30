import {click253} from './click253.mjs';
const endpoint=process.env.MF_CDP_ENDPOINT;
let pages=[];
for(let i=0;i<100;i++){try{pages=await(await fetch(endpoint+'/json/list')).json();if(pages.some(p=>p.type==='page'))break}catch{}await new Promise(r=>setTimeout(r,100));}
const page=pages.find(p=>p.type==='page');if(!page)throw Error('Qt page absent');
const ws=new WebSocket(page.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let seq=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(!d.id)return;const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result);};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const dispatch=(type,x,y)=>call('Input.dispatchMouseEvent',{type,x,y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
try{
  await evaluate(`document.write('<style>body{margin:0}#target{position:absolute;left:120px;top:130px;width:140px;height:50px}</style><button id="target">click me</button><div id="cover" style="display:none;position:fixed;inset:0;background:#000;z-index:9"></div><script>window.clicks=0;document.addEventListener("click",e=>{if(e.target.closest("#target"))window.clicks++})<\\/script>')`);
  // Old strategy: two RAFs then a one-off rect. It observes the delayed
  // element at (0,0), producing the same invalid coordinates as the reported bug.
  await evaluate(`(()=>{const e=document.querySelector('#target');e.style.display='none';return true})()`);
  const old=await evaluate(`(async()=>{const e=document.querySelector('#target');e.scrollIntoView({block:'nearest'});await new Promise(r=>requestAnimationFrame(()=>{e.replaceWith(e.cloneNode(true));requestAnimationFrame(r)}));const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2,width:r.width,height:r.height,connected:e.isConnected}})()`);
  if(old.connected||old.x!==0||old.y!==0||old.width!==0||old.height!==0)throw Error('old strategy did not reproduce detached zero rect: '+JSON.stringify(old));
  await evaluate(`document.querySelector('#target').style.display='none';setTimeout(()=>document.querySelector('#target').style.display='block',130);document.querySelector('#cover').style.display='block';setTimeout(()=>document.querySelector('#cover').style.display='none',260)`);
  const first=await click253(evaluate,dispatch,'#target',{timeoutMs:2500,pollMs:20,stableSamples:3});
  if(!first.ready||first.width<=0||first.height<=0)throw Error('not actionable after delayed visibility/occlusion: '+JSON.stringify(first));
  if(await evaluate('window.clicks')!==1)throw Error('delayed target click count was not one');

  await evaluate(`window.clicks=0;document.querySelector('#target').replaceWith(Object.assign(document.createElement('button'),{id:'target',textContent:'old generation'}));setTimeout(()=>{const e=document.querySelector('#target');const n=e.cloneNode(true);n.textContent='new generation';e.replaceWith(n)},45)`);
  let initialIdentity=null,didReplace=false;
  const replaced=await click253(evaluate,dispatch,'#target',{timeoutMs:2500,pollMs:20,stableSamples:3,onSample:async sample=>{
    if(sample.ready&&!didReplace){initialIdentity=sample.identity;didReplace=true;await evaluate(`(()=>{const e=document.querySelector('#target');const n=e.cloneNode(true);n.textContent='new generation';e.replaceWith(n)})()`);}
  }});
  if(!didReplace||replaced.identity===initialIdentity||replaced.text!=='new generation'||await evaluate('window.clicks')!==1)throw Error('replacement click failed or dispatched more than once: '+JSON.stringify({replaced,initialIdentity,didReplace}));
  console.log(JSON.stringify({ok:true,oldRect:old,delayedAndOccluded:first,reactReplacement:replaced,clicks:await evaluate('window.clicks')}));
}finally{ws.close();}
