import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { App } from '../../../refactor/learning/web/src/App'
import { Loading } from '../../../refactor/learning/web/src/loading/Loading'
import { connect, call } from '../../../refactor/learning/web/src/bridge'
import { useApp } from '../../../refactor/learning/web/src/store'
import { beginActivity, endActivity, getActivities } from '../../../refactor/learning/web/src/loading/activity'
import '../../../refactor/learning/web/src/theme.css'

declare global {interface Window { loading86Native?:boolean; loading86ReaderOnly?:boolean; loading86Result?:any; loading86Show?:any; loading86Ready?:boolean; loading86Hold?:string; loading86Wire?:any }}
const pause=(ms:number)=>new Promise(r=>setTimeout(r,ms))
const frames=async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)}
const wait=async(fn:()=>boolean,label:string,limit=5000)=>{const end=performance.now()+limit;while(!fn()){if(performance.now()>end)throw Error(label+': '+document.body.innerText);await pause(10)}await frames()}
const assert=(b:unknown,label:string)=>{if(!b)throw Error(label)}
const settings={version:1,theme:'sakura',font_scale:1,reduce_motion:false,teacher_style:'warm',detail:'normal',examples:'normal',analogy_domains:['life'],refresh_time:'04:00',reminder_enabled:false,reminder_time:'20:00',chat_persona:''}
const options={themes:['sakura','aurora'],teacher_styles:['warm'],detail_levels:['normal'],example_amounts:['normal'],analogy_domains:['life']}
const plan={id:'fixture',title:'计算机入门',goal:'认识计算机概念',state:'active',total_minutes:60,archived:false,module_count:1,today:null,window:null}
const detail={plan,teacher_preset:{id:'t1',name:'预设一'},stages:[{id:'s',title:'入门',modules:[{id:'m',title:'计算机',summary:'认识硬件与软件',state:'passed',question_count:2,passed_questions:2,learning_minutes:30,check_minutes:10,points:[{id:'p',title:'CPU',summary:'理解处理器作用',minutes:8,citations:[]}]}]}],sources:[],point_links:[],point_links_status:'ready'}
const room={plan,module:{id:'m',title:'计算机',number:1,total:1,state:'untested'},points:[{id:'p',title:'CPU',minutes:8,taught:false,state:'gray',current:true}],step:'teaching',question:null,messages:[{id:'a1',role:'assistant',kind:'teacher',content:'CPU 可以理解为处理任务的中心。',sources:[]}],can_skip:false,red:[],more_modules:false,check:null,today:null,conversation:{options:[],interrupted:false}}
let receiver:(s:string)=>void=()=>{}
let held:any[]=[]
let wire:any[]=[]
let plansDelay=0
function response(req:any,result:any) {receiver(JSON.stringify({type:'response',id:req.id,ok:true,result}))}
function fake(req:any){
  wire.push({method:req.method,id:req.id})
  if(req.method==='cancel'){const match=held.find(r=>r.id===req.params.target);if(match){held=held.filter(r=>r!==match);receiver(JSON.stringify({type:'response',id:match.id,ok:false,error:{code:'CANCELLED',message:'操作已取消'}}))}return}
  if(window.loading86Hold===req.method){held.push(req);return}
  if(req.method==='app.bootstrap')return response(req,{version:'test',account:{name:'离线检查',role_label:'学习者'},settings,options,ready:true,notice:null})
  if(req.method==='plans.list')return setTimeout(()=>response(req,{plans:[plan],study_day:'2026-10-08'}),plansDelay)
  if(req.method==='plan.detail')return response(req,detail)
  if(req.method==='classroom.enter'||req.method==='classroom.state')return response(req,room)
  if(req.method==='classroom.reply'){held.push(req);return}
  if(req.method==='settings.teacher_presets')return response(req,{presets:[{id:'t1',name:'预设一'}],default_id:'t1'})
  if(req.method==='chat.list')return response(req,{sessions:[]})
  if(req.method==='selftest.list')return response(req,{sets:[]})
  if(req.method==='models.providers')return response(req,{providers:[]})
  if(req.method==='models.list')return response(req,{presets:[],active_id:null})
  if(req.method==='usage.spend')return response(req,{preset:null,spend:{supported:false,observations:0,ready:false,balance:null,currencies:[]}})
  if(req.method==='usage.summary')return response(req,{total:{calls:0,input_tokens:0},groups:[]})
  if(req.method==='classroom.gaps')return response(req,[])
  if(req.method==='app.window')return response(req,{teacher:{open:false,width:320,view:{scale:1,panX:0,panY:0},mode:'internal'}})
  if(req.method==='update.status'||req.method==='update.automatic')return response(req,{state:'UNPACKAGED',busy:false,available:false,version:'test'})
  if(req.method==='teacher2d.status')return response(req,{ready:false,message:'测试不加载老师资源',manifest:null,installation:{message:''}})
  response(req,{ok:true})
}
if(!window.loading86Native){
  window.qt={webChannelTransport:{}}
  window.QWebChannel=class {
    constructor(_t:unknown,ready:(v:any)=>void) {
      const mf={call:(s:string)=>fake(JSON.parse(s)),event:{connect:(fn:(s:string)=>void)=>{receiver=fn}}}
      ready({objects:{mf}})
    }
  } as any
}
const root=createRoot(document.getElementById('root')!)
async function main(){
  await connect()
  const result:any={checks:[],timings:[],ok:false}
  if(window.loading86Native){
    const visibleWaits=new Set<string>();const capture=setInterval(()=>document.querySelectorAll<HTMLElement>('.wait').forEach(el=>{if(getComputedStyle(el).visibility!=='hidden'&&el.offsetWidth)visibleWaits.add(`${el.dataset.waitTier}: ${el.textContent}`)}),10)
    const start=performance.now();root.render(<App/>);await wait(()=>!!document.querySelector('.home-plan-grid')||document.body.innerText.includes('今天还没有'),'native home',15000)
    result.timings.push({operation:'startup',ms:performance.now()-start})
    const list=await call<any>('plans.list',{include_archived:true});const id=list.plans.find((p:any)=>p.id==='029b076315034c00bbd6842a5c9c31d9')?.id??list.plans[0].id
    useApp.getState().set({planId:id});let at=performance.now();useApp.getState().go('plan');await wait(()=>!!document.querySelector('.knowledge-map'),'native detail',15000);result.timings.push({operation:'detail',ms:performance.now()-at})
    at=performance.now();Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='加资料')?.click()
    await wait(()=>document.body.innerText.includes('选择文件'),'addition entry');result.timings.push({operation:'addition_entry',ms:performance.now()-at})
    at=performance.now();useApp.getState().go('classroom');await wait(()=>!!document.querySelector('.class-main'),'native classroom',15000);result.timings.push({operation:'classroom_enter',ms:performance.now()-at})
    if(window.loading86ReaderOnly){clearInterval(capture);result.intermediate=[...visibleWaits];result.ok=true;window.loading86Result=result;return}
    Array.from(document.querySelectorAll('button')).find(b=>b.textContent?.includes('我想问'))?.click();await pause(30)
    const ta=document.querySelector('.composer textarea') as HTMLTextAreaElement
    const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!
    setter.call(ta,'用一句话解释刚才讲的概念，不需要继续新知识点。');ta.dispatchEvent(new Event('input',{bubbles:true}));await pause(40)
    const seen=new Set(Array.from(document.querySelectorAll('.bubble.bot')).map(n=>n.textContent))
    at=performance.now();Array.from(document.querySelectorAll('.composer button')).find(b=>b.textContent?.includes('发送'))?.click()
    let middle:string[]=[]
    const sampling=setInterval(()=>{const text=document.querySelector('.class-scroll .wait')?.textContent;if(text&&!middle.includes(text))middle.push(text)},100)
    await wait(()=>Array.from(document.querySelectorAll('.bubble.bot')).some(n=>!seen.has(n.textContent))&&!document.querySelector('.class-scroll .wait'), 'native teacher',240000)
    clearInterval(sampling);clearInterval(capture);result.intermediate=[...visibleWaits];result.timings.push({operation:'teacher_reply',ms:performance.now()-at,intermediate:middle});result.ok=true;window.loading86Result=result;return
  }
  // Exact boundaries via an already running wait plus real debounce measurements.
  let action=0
  root.render(<Loading shape="cards" label="正在读取我的计划…" onRetry={()=>action++} onCancel={()=>action++}/>)
  await frames();assert(document.querySelector('[data-wait-tier="quiet"]'),'quiet');assert(getComputedStyle(document.querySelector('.wait')!).visibility==='hidden','under 300ms hidden')
  await pause(340);assert(document.querySelector('[data-wait-tier="medium"]'),'medium');await pause(2750);assert(document.querySelector('[data-wait-tier="long"]'),'long')
  root.render(<Loading key="slow" started={Date.now()-16000} shape="conversation" label="老师正在想…" onCancel={()=>action++} onRetry={()=>action++}/>)
  await wait(()=>!!document.querySelector('[data-wait-slow="true"]'),'slow');assert(document.body.innerText.includes('比平时慢'),'slow copy');document.querySelectorAll<HTMLButtonElement>('.wait-actions button').forEach(b=>b.click());assert(action===2,'retry and cancel wired')
  result.checks.push('300ms/3s/15s boundaries; retry/cancel callbacks')
  window.loading86Hold='app.bootstrap';root.render(<App/>);
  await wait(()=>held.some(r=>r.method==='app.bootstrap'),'bootstrap actually pending')
  await wait(()=>document.body.innerText.includes('正在准备学习空间'),'startup placeholder')
  window.loading86Hold=undefined;held.filter(r=>r.method==='app.bootstrap').forEach(r=>fake(r));held=held.filter(r=>r.method!=='app.bootstrap')
  await wait(()=>document.body.innerText.includes('查看全部计划'),'home loaded')
  plansDelay=650;useApp.getState().go('plans');await pause(360);assert(document.querySelector('.wait-cards'),'plans shape');await wait(()=>document.body.innerText.includes('查看路线'),'plans content')
  useApp.getState().set({planId:'fixture'});useApp.getState().go('classroom');await wait(()=>!!document.querySelector('.class-main'),'classroom');
  const ta=document.querySelector('.composer textarea') as HTMLTextAreaElement
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(ta,'我不太明白');ta.dispatchEvent(new Event('input',{bubbles:true}));await pause(30)
  Array.from(document.querySelectorAll('.composer button')).find(b=>b.textContent?.includes('发送'))?.click();await wait(()=>document.body.innerText.includes('老师正在想'),'teacher slot');
  const req=held.find(r=>r.method==='classroom.reply');assert(req,'teacher request')
  receiver(JSON.stringify({type:'stream',id:req.id,delta:'我们换个生活中的例子。'}));await wait(()=>!!document.querySelector('.class-scroll .wait-content'),'stream content');assert(!document.querySelector('.class-scroll .wait'),'placeholder replaced')
  response(req,{...room,messages:[...room.messages,{id:'a2',role:'assistant',kind:'teacher',content:'我们换个生活中的例子。',sources:[]}]});held=held.filter(r=>r!==req)
  await wait(()=>!getActivities().some(a=>a.id===req.id),'activity completion')
  result.checks.push('startup/home/plans/classroom placeholders; first text replaces teacher wait')
  // Non-content actions use the same component, with no request body stored.
  useApp.getState().go('tools');window.loading86Hold='settings.save'
  const pending=call('settings.save',{changes:{chat_persona:'DO_NOT_RETAIN'}});await pause(340)
  assert(document.querySelector('.wait-global'),'fallback action');assert(!JSON.stringify(getActivities()).includes('DO_NOT_RETAIN'),'metadata only')
  const save=held.find(r=>r.method==='settings.save');response(save,{settings});await pending;held=held.filter(r=>r!==save);window.loading86Hold=undefined
  await wait(()=>!document.querySelector('.wait-global'),'fallback dismissed')
  beginActivity('background','voice.status');assert(!getActivities().find(a=>a.id==='background')?.streamed,'pending tracked');endActivity('background')
  result.checks.push('global fallback metadata only; response removes activity')
  const slowReq=call('selftest.import',{}, {signal:new AbortController().signal});await slowReq
  result.checks.push('no real model/credentials')
  window.loading86Show=async(theme:string,mode:string,reduced=false)=>{
    window.loading86Ready=false
    document.documentElement.dataset.theme=theme;document.documentElement.classList.toggle('reduce-motion',reduced)
    root.render(<><div className="page" style={{maxWidth:900,margin:'30px auto'}}><h1>学习正在准备好</h1><p className="muted">每一步都看得见；准备好后会自动继续。</p><Loading key={`${theme}-${mode}-${reduced}`} label={mode==='teacher'?'老师正在想…':mode==='slow'?'正在按资料填知识点…':'正在读取我的计划…'}
      started={Date.now()-(mode==='medium'?600:mode==='slow'?16000:4000)} shape={mode==='medium'?'cards':'conversation'} detail={mode==='slow'?'当前模块 2/4':undefined} onCancel={mode==='slow'?()=>{}:undefined} onRetry={mode==='slow'?()=>{}:undefined}/>
      </div><div style={{position:'fixed',left:0,top:0,width:2,height:2,background:(theme==='sakura'?'#aa112':'#bb223')+(['medium','long','teacher','slow'].indexOf(mode)+2)}}/></>)
    await pause(70);await frames();window.loading86Ready=true
  }
  result.ok=true;window.loading86Result=result
}
main().catch(e=>{window.loading86Result={ok:false,error:String(e),stack:e.stack,wire}})
