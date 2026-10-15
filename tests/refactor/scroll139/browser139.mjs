import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),probe=process.argv.includes('--probe'),programOnly=process.argv.includes('--program-only'),endpoint=process.env.MF_CDP_ENDPOINT;
const pause=(ms=30)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<200;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map();ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
const geometry=()=>js(`(()=>{const s=document.querySelector('.probe139'),a=s.querySelector('[data-turn-kind="recap"]')||s.querySelector('[data-turn-role="assistant"]'),r=s.getBoundingClientRect();return{top:s.scrollTop,max:s.scrollHeight-s.clientHeight,delta:a.getBoundingClientRect().top-r.top,x:r.left+s.clientWidth/2,y:r.top+s.clientHeight/2,right:r.right,height:s.clientHeight}})()`);
const errors=[],rows=[];let result;
try{
 await until('!!window.ready139');await js('window.ready139');
 const cases=programOnly?[{mode:'recap',action:'program'}]:['teacher','recap'].flatMap(mode=>['down','up','PageDown'].map(action=>({mode,action})));
 if(!probe&&!programOnly)cases.push(...['ArrowDown','ArrowUp','PageUp','Home','End',' ','shift-space','touch','scrollbar'].map(action=>({mode:'recap',action})));
 for(const {mode,action} of cases){
  await js(`window.mount139(false)`);await js(`window.control139.round('${mode}')`);
  await until(`!!document.querySelector('[data-turn-role="assistant"]')`);
  await js('for(let i=0;i<18;i++)window.control139.grow()');await until(`document.querySelectorAll('.probe139 p').length===18`);
  await until(`(()=>{const s=document.querySelector('.probe139'),a=s.querySelector('[data-turn-kind="recap"]')||s.querySelector('[data-turn-role="assistant"]');return Math.abs(a.getBoundingClientRect().top-s.getBoundingClientRect().top-12)<2})()`);
  const before=await geometry();
  // No pointer click/focus gesture can accidentally make downward wheel pass.
  await js(`(()=>{const s=document.querySelector('.probe139');s.focus({preventScroll:true});window.frames139=[];window.tokens139=0;window.active139=true;const frame=()=>{if(!window.active139)return;window.frames139.push(s.scrollTop);requestAnimationFrame(frame)};requestAnimationFrame(frame);window.stream139=setInterval(()=>{window.control139.grow();window.tokens139++},55)})()`);
  let direction=action==='up'||['ArrowUp','PageUp','Home','shift-space'].includes(action)?-1:1;
  if(action==='program'){
   await js('document.querySelector(".probe139").scrollTop+=150');
  }else if(action==='down'||action==='up'){
   await call('Input.dispatchMouseEvent',{type:'mouseWheel',x:before.x,y:before.y,deltaX:0,deltaY:direction*150});
  }else if(action==='touch'){
   await call('Emulation.setTouchEmulationEnabled',{enabled:true});
   await call('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:before.x,y:before.y}]});
   await call('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:before.x,y:before.y-120}]});
   await call('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
   await call('Emulation.setTouchEmulationEnabled',{enabled:false});
  }else if(action==='scrollbar'){
   const thumb=await js(`(()=>{const s=document.querySelector('.probe139'),r=s.getBoundingClientRect(),h=s.clientHeight;return{x:r.right-7,y:r.top+(s.scrollTop/(s.scrollHeight-h))*(h-h*h/s.scrollHeight)+h*h/s.scrollHeight/2}})()`);
   await call('Input.dispatchMouseEvent',{type:'mouseMoved',...thumb});
   await pause(80);
   await call('Input.dispatchMouseEvent',{type:'mousePressed',...thumb,button:'left',buttons:1,clickCount:1});
   await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:thumb.x,y:thumb.y+55,button:'left',buttons:1});
   await call('Input.dispatchMouseEvent',{type:'mouseReleased',x:thumb.x,y:thumb.y+55,button:'left',buttons:0,clickCount:1});
  }else{
   const key=action==='shift-space'?' ':action;
   const codes={ArrowDown:40,ArrowUp:38,PageDown:34,PageUp:33,Home:36,End:35,' ':32};
   await call('Input.dispatchKeyEvent',{type:key===' '?'rawKeyDown':'keyDown',key,code:key===' '?'Space':key,windowsVirtualKeyCode:codes[key],modifiers:action==='shift-space'?8:0});
   if(key===' ')await call('Input.dispatchKeyEvent',{type:'char',key,text:' ',unmodifiedText:' ',code:'Space',windowsVirtualKeyCode:32,modifiers:action==='shift-space'?8:0});
   await call('Input.dispatchKeyEvent',{type:'keyUp',key,code:key===' '?'Space':key,windowsVirtualKeyCode:codes[key]});
  }
  await pause(900);
  const samples=await js('window.active139=false;clearInterval(window.stream139);({frames:window.frames139,tokens:window.tokens139})');
  if(action==='program')await until(`Math.abs((document.querySelector('.probe139 [data-turn-kind="recap"]').getBoundingClientRect().top-document.querySelector('.probe139').getBoundingClientRect().top)-12)<2`);
  const after=await geometry(),frames=[before.top,...samples.frames,after.top];
  const movement=direction*(after.top-before.top);
  const reversal=frames.slice(1).some((v,i)=>direction*(v-frames[i]) < -2);
  const kept=action==='program'?Math.abs(after.delta-12)<2&&frames.some(v=>v>before.top+10):movement>10&&!reversal;
  if(!kept)errors.push(`${mode} ${JSON.stringify(action)} control lost: movement=${movement},reversal=${reversal}`);
  if(samples.tokens<5)errors.push('insufficient live streaming updates');
  // A fresh operation resets stopped, including after each direction/key.
  await js(`window.control139.round('teacher')`);
  await until(`Math.abs(document.querySelector('.probe139 [data-turn-role="assistant"]').getBoundingClientRect().top-document.querySelector('.probe139').getBoundingClientRect().top-12)<2`);
  const next=await geometry();rows.push({mode,action,kept,before,after,tokens:samples.tokens,frames,next});
 }
 result={ok:!errors.length,probe,rows,errors};
}catch(e){result={ok:false,probe,rows,errors,error:String(e.stack||e),dom:await js('document.body.innerText').catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'result139.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify({ok:result.ok,probe,rows:rows.map(({mode,action,kept,frames,...rest})=>({mode,action,kept,frame_count:frames.length})),errors,error:result.error}));
if(!result.ok&&!probe)process.exitCode=1;
