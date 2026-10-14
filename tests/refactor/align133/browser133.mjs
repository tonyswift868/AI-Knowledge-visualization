import fs from 'node:fs';import path from 'node:path';import{fileURLToPath}from'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,probe=process.argv.includes('--probe');
const pause=(ms=35)=>new Promise(r=>setTimeout(r,ms));let target;
for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('owned page missing');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let id=0;const pending=new Map(),rows=[],errors=[];
ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+expression)}
async function button(label){await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)}&&!b.disabled);if(!b)throw Error('missing '+${JSON.stringify(label)});b.click()})()`)}
function assert(value,message){if(!value)errors.push(message)}
async function shot(name){const r=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,name),Buffer.from(r.data,'base64'))}
const geometry=key=>js(`(()=>{const s=document.querySelector('.class-scroll'),a=s.querySelector('[data-turn-key="${key}"]');return{delta:a?a.getBoundingClientRect().top-s.getBoundingClientRect().top:null,scroll:s.scrollTop,max:s.scrollHeight-s.clientHeight,height:s.clientHeight,body:a?.clientHeight}})()`);
const reply='==压力相同，面积越小压强越大。==\n\n先把压力相同这个条件留下，再比较手指和针尖接触物体的面积。压强是单位面积上受到的压力，面积减小时，同样大小的压力作用在更小的面积上。\n\n用公式p=F/S比较时，先确认哪些量相同，不把面积与压力混成一个量；条件变了，就回到原文和公式重新比较。';
let result;
try{
 await until('!!window.ready133');await js('window.ready133');
 const cases=(process.argv.includes('--ordinary')?[[1000,'sakura']]:[[1000,'sakura'],[600,'aurora'],[600,'sakura'],[1000,'aurora']]).flatMap(([width,theme])=>(process.argv.includes('--ordinary')?['student','teacher']:['student','teacher','recap']).map(mode=>({width,theme,mode,long:false})));
 if(!probe)cases.push({width:600,theme:'aurora',mode:'teacher',long:true});
 for(const {width,theme,mode,long} of cases){
  await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});await js(`localStorage.removeItem('mf.classroom.focus');document.documentElement.dataset.theme='${theme}';window.mount133('${mode}',${long})`);await pause(500);
  if(mode==='student'){
   await js(`(()=>{const e=document.querySelector('[aria-label="课堂输入"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'我理解了，请接着说。');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);await pause();await button('发送');
  }else await button('懂了，继续');
  await until(`typeof window.chunk133==='function'`);const samples=[];
  for(const chunk of [reply.slice(0,20),reply.slice(20,60),reply.slice(60,120),reply.slice(120)+(long?'\n\n'+reply.repeat(12):'')]){
   await js(`window.chunk133(${JSON.stringify(chunk)})`);await pause(400);samples.push(await geometry(mode==='student'?'pending-student':'teacher-stream'));
  }
  await js('window.finish133()');await until(`!!document.querySelector('[data-turn-key="teacher133"]')`);await pause(500);
  const key=mode==='student'?'user133':mode==='recap'?'recap133':'teacher133',g=await geometry(key);
  assert(Math.abs(g.delta-12)<2,`${width} ${mode} final alignment ${JSON.stringify(g)}`);
  assert(samples.every((s,i)=>!i||s.scroll>=samples[i-1].scroll-2),`${width} ${mode} stream jumps backwards ${JSON.stringify(samples)}`);
  if(!probe){
   assert(samples.every(s=>Math.abs(s.delta-12)<2),`${width} ${mode} streaming anchor ${JSON.stringify(samples)}`);
   for(let i=0;i<2;i++){
    await js(`document.querySelector('[aria-label="只看重点"]').click()`);await pause(450);
    const focus=await geometry(key);assert(Math.abs(focus.delta-12)<2,`${width} ${mode} focus toggle ${JSON.stringify(focus)}`);
   }
   for(const state of ['recording','finishing','idle']){
    await js(`window.dispatchEvent(new CustomEvent('voice133',{detail:'${state}'}))`);await pause(150);
    const voice=await geometry(key);assert(Math.abs(voice.delta-g.delta)<2&&voice.height===g.height,`${width} ${mode} voice ${state} ${JSON.stringify(voice)}`);
   }
  }
  await shot(`${probe?'before':'after'}133-${width}-${theme}-${mode}${long?'-long':''}.png`);rows.push({width,theme,mode,long,...g,samples});
 }
 if(!probe){
  // A user reading older messages must keep control even as tokens arrive.
  await js(`window.mount133('teacher',true)`);await pause(100);await button('懂了，继续');await until(`typeof window.chunk133==='function'`);
  await js(`window.chunk133(${JSON.stringify(reply)})`);await pause(100);
  await js(`(()=>{const s=document.querySelector('.class-scroll');s.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,bubbles:true}));s.scrollTop-=120})()`);
  const previous=await geometry('teacher-stream');await js(`window.chunk133(${JSON.stringify(reply)})`);await pause(150);
  const after=await geometry('teacher-stream');assert(Math.abs(after.scroll-previous.scroll)<2,'user upward scroll was overridden');
  await js('window.finish133()');await until(`!!document.querySelector('[data-turn-key="teacher133"]')`);
 }
 result={ok:!errors.length,rows,errors};
}catch(e){result={ok:false,rows,errors,error:String(e.stack||e),dom:await js('document.body.innerText').catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'result133.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result));if(!result.ok&&!probe)process.exitCode=1;
