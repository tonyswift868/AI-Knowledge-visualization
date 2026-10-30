import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
const root=path.resolve(process.argv[2]), endpoint=process.env.MF_CDP_ENDPOINT
const pause=(ms=30)=>new Promise(r=>setTimeout(r,ms))
let target
for(let i=0;i<200;i++){
  try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}
  await pause(100)
}
if(!target)throw Error('isolated frontend219 page not found')
const ws=new WebSocket(target.webSocketDebuggerUrl)
await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j})
let seq=0;const waiting=new Map()
ws.onmessage=e=>{const m=JSON.parse(e.data);if(!m.id)return;const p=waiting.get(m.id);waiting.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;waiting.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression,label=expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}throw Error('timeout: '+label)}
const checks=[]
function check(ok,label){if(!ok)throw Error(label);checks.push(label)}
async function push(name,data={}){await js(`(window.pushFrontend219(${JSON.stringify(name)},${JSON.stringify(data)}),true)`)}
async function stats(){return await js('window.statsFrontend219()')}
let result
try{
  await until('!!window.mountFrontend219&&!!document.querySelector(".topbar")','App shell mounted')
  await pause(100)
  await push('ready',{account:{name:'账号 A',account_id:'a219',role_label:'学习者'}})
  await push('scope_changed',{})
  await push('ready',{account:{name:'账号 B',account_id:'b219',role_label:'学习者'}})
  await until('document.documentElement.dataset.theme==="aurora"','B settings applied')
  await pause(1500)
  check(await js('document.documentElement.dataset.theme==="aurora"'),'late A settings and stale bootstrap responses cannot replace B theme or readiness')
  await push('settings',{theme:'sakura',font_scale:1,reduce_motion:true})
  await pause(350)
  check(await js('document.documentElement.dataset.theme==="aurora"'),'unscoped late settings event triggers a current-account read instead of applying its old payload')

  await push('plans_changed',{})
  await until('window.statsFrontend219().then(s=>s.plans>=2)','old-account Home refresh started')
  await push('scope_changed',{})
  await pause(950)
  check(await js('!document.querySelector(".home-page")&&!document.body.innerText.includes("旧账号计划")'),'Home is gated during scope transition; old plan response is not shown')
  await push('ready',{account:{name:'账号 B',account_id:'b219',role_label:'学习者'}})
  await until('document.body.innerText.includes("new-day")','new-account Home data loaded')
  await pause(900)
  check(await js('document.body.innerText.includes("new-day")&&!document.body.innerText.includes("old-day")&&!document.body.innerText.includes("旧账号计划")'),'late Home response cannot replace new-account plan list')

  await js('window.navigateFrontend219("new-plan")')
  await until('window.statsFrontend219().then(s=>s.drafts===1)','old-account draft read started')
  await push('scope_changed',{})
  await pause(950)
  check(await js('!document.body.innerText.includes("旧账号草稿")'),'old NewPlan draft is not shown after account scope changes')
  await push('ready',{account:{name:'账号 B',account_id:'b219',role_label:'学习者'}})
  await until('document.querySelector(".plan-title")?.textContent==="新账号草稿"','new-account draft loaded')
  await pause(900)
  check(await js('document.querySelector(".plan-title")?.textContent==="新账号草稿"&&!document.body.innerText.includes("旧账号草稿")'),'late NewPlan response cannot replace new-account draft')

  await js('window.navigateFrontend219("settings")')
  await until('window.statsFrontend219().then(s=>s.providers===1)','old-account Settings requests started')
  await push('scope_changed',{})
  await pause(100)
  await push('ready',{account:{name:'账号 B',account_id:'b219',role_label:'学习者'}})
  await until('document.body.innerText.includes("新账号厂商")','new-account provider list loaded')
  await pause(900)
  check(await js('document.body.innerText.includes("新账号厂商")&&!document.body.innerText.includes("旧账号厂商")'),'late provider/list response cannot replace new-account Settings')

  await js('window.navigateFrontend219("rag")')
  await until('window.statsFrontend219().then(s=>s.rag_gets===1)','old-account RAG session read started')
  await push('scope_changed',{})
  await pause(100)
  await push('ready',{account:{name:'账号 B',account_id:'b219',role_label:'学习者'}})
  await until('document.querySelector(".rag-chat-head h2")?.textContent==="新账号资料会话"','new-account RAG session opened')
  await pause(900)
  check(await js('document.querySelector(".rag-chat-head h2")?.textContent==="新账号资料会话"'),'late old-account session cannot replace new-account RAG')

  await js('window.mountFrontend219("rag")')
  await until('!!document.querySelector(".rag-session-list button[title=\\"会话 B\\"]")','Rag sessions loaded')
  await js('document.querySelector(".rag-session-list button[title=\\"会话 B\\"]").click()')
  await until('document.querySelector(".rag-chat-head h2")?.textContent==="会话 B"','B RAG session opened')
  await pause(1000)
  check(await js('document.querySelector(".rag-chat-head h2")?.textContent==="会话 B"&&!!document.querySelector("input[aria-label=\\"选择资料：资料 B\\"]")?.checked'),'late A session response cannot replace B session or selected document')

  await js('window.mountFrontend219("plan")')
  await until('document.querySelector(".hero h1")?.textContent==="初始详情"','initial plan detail loaded')
  await push('plan.point_links_changed',{plan_id:'plan-219',status:'changed'})
  await push('plan.point_links_changed',{plan_id:'plan-219',status:'changed'})
  await until('document.querySelector(".hero h1")?.textContent==="较新详情"','new refresh succeeded')
  await pause(800)
  check(await js('document.querySelector(".hero h1")?.textContent==="较新详情"&&!document.body.innerText.includes("旧刷新失败")'),'late failure from old plan refresh cannot overwrite newer result')

  await js('window.mountFrontend219("app")')
  await until('!!document.querySelector(".topbar")','App remounted for settings retry path')
  await js('(window.failSettingsFrontend219(),true)')
  await push('scope_changed',{})
  await push('ready',{account:{name:'账号 B',account_id:'b219',role_label:'学习者'}})
  await until('document.body.innerText.includes("读取当前账号设置失败，请重试连接")','current settings read failure is actionable')
  check(await js('!!document.querySelector("[role=alert] button")'),'settings failure exposes the existing bootstrap retry action')
  await js('document.querySelector("[role=alert] button").click()')
  await until('!!document.querySelector(".home-page")','bootstrap retry restored the current account shell')
  check(await js('!!document.querySelector(".home-page")&&document.documentElement.dataset.theme==="sakura"'),'retry recovers after a scoped settings read failure')

  await js('(window.mountFrontend219("app"),true)')
  await until('!!document.querySelector(".topbar")','fresh App scope mounted')
  await js('(window.failSettingsFrontend219(),true)')
  await push('ready',{account:{name:'首次读取账号',account_id:'initial-fail219',role_label:'学习者'}})
  await until('document.body.innerText.includes("读取当前账号设置失败，请重试连接")','initial settings read failure shown')
  await pause(1200)
  check(await js('!!document.querySelector("[role=alert] button")'),'late bootstrap with ready=false cannot hide initial settings retry')
  await js('document.querySelector("[role=alert] button").click()')
  await until('!!document.querySelector(".home-page")','initial settings bootstrap retry recovered')
  check(await js('!!document.querySelector(".home-page")'),'initial ready-before-bootstrap failure is recoverable')
  result={ok:true,checks}
}catch(e){result={ok:false,checks,error:String(e.stack||e),body:await js('document.body.innerText').catch(()=>null)}}
finally{fs.writeFileSync(path.join(root,'async-result219.json'),JSON.stringify(result,null,2));ws.close()}
console.log(JSON.stringify(result))
if(!result.ok)process.exitCode=1
