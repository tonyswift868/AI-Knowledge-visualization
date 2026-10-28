import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Report } from '../../../refactor/learning/web/src/pages/Report'
import { ProgressStatus } from '../../../refactor/learning/web/src/ProgressStatus'
import { useApp } from '../../../refactor/learning/web/src/store'
import { calls, finish } from './fake_bridge'
import '../../../refactor/learning/web/src/theme.css'

declare global { interface Window { result219: any; baselineStage219:string; showProgress219:(state:string,theme:string)=>void } }
let root=createRoot(document.getElementById('root')!)
const nativeTimeout=window.setTimeout.bind(window)
const realNow=Date.now.bind(Date); let timeOffset=0; Date.now=()=>realNow()+timeOffset
window.setTimeout=((handler:TimerHandler, timeout?:number, ...args:any[])=>{
  if(timeout&&timeout>14000&&typeof handler==='function')return nativeTimeout(()=>{timeOffset+=20000;handler(...args)},0)
  return nativeTimeout(handler,timeout,...args)
}) as typeof window.setTimeout
const samples:any={
  debt:{today_target:60,today_confirmed:0,today_tentative:0,debt_minutes:36,today_repaid_confirmed:0,today_repaid_tentative:24,needs_negotiation:false,oldest_debt_day:'2026-10-07'},
  severe:{today_target:60,today_confirmed:0,today_tentative:0,debt_minutes:156,today_repaid_confirmed:8,today_repaid_tentative:16,needs_negotiation:true,oldest_debt_day:'2026-10-05'},
  today:{today_target:60,today_confirmed:16,today_tentative:8,debt_minutes:0,ahead_minutes:0},
  ahead:{today_target:60,today_confirmed:60,today_tentative:0,debt_minutes:0,ahead_minutes:24,ahead_tentative:8},
}
const expected:any={debt:'逾期待还 36 分今天已还 24 分',severe:'逾期待还 156 分今天已还 24 分',today:'今天 24 / 60 分',ahead:'超前完成 24 分'}
window.showProgress219=(state,theme)=>{document.documentElement.dataset.theme=theme;root.render(<ProgressStatus value={samples[state]}/>) }
const wait=(ms=20)=>new Promise(r=>setTimeout(r,ms))
async function until(test:()=>boolean){for(let i=0;i<300;i++){if(test())return;await wait()}throw Error('browser state timed out')}
async function run(){
  for(const theme of ['sakura','aurora']){
    window.showProgress219('today',theme);await until(()=>!!document.querySelector('.progress-status'));await wait(120)
    if(!document.querySelector('.progress-status')!.textContent!.includes('24 / 60'))throw Error('baseline progress number mismatch')
    window.baselineStage219=theme
    await wait(500)
  }
  document.documentElement.dataset.theme='sakura'
  useApp.setState({route:'report',planId:'a'.repeat(32)})
  root.render(<Report/>); await until(()=>calls.length===1)
  await until(()=>[...document.querySelectorAll('button')].some(b=>b.textContent?.trim()==='取消'))
  ;[...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent?.trim()==='取消')!.click()
  if(useApp.getState().route!=='plan')throw Error('cancel no longer leaves report normally')
  root.unmount(); root=createRoot(document.getElementById('root')!); useApp.setState({route:'home'}); finish(0,'reject'); await wait(80)
  if(useApp.getState().route!=='home')throw Error('late failure after leaving report navigated to '+useApp.getState().route)
  if(useApp.getState().toasts.some(t=>t.kind==='error'))throw Error('late failure toasted after report unmounted')

  const nextRoot=createRoot(document.getElementById('root')!)
  useApp.setState({route:'report',planId:'b'.repeat(32),toasts:[]})
  nextRoot.render(<Report/>); await until(()=>calls.length===2)
  nextRoot.unmount(); useApp.setState({route:'home'}); finish(1,'resolve'); await wait(80)
  if(useApp.getState().route!=='home')throw Error('late success changed route')
  if(useApp.getState().toasts.length)throw Error('late success caused notification')

  const activeRoot=createRoot(document.getElementById('root')!)
  useApp.setState({route:'report',planId:'c'.repeat(32),toasts:[]})
  activeRoot.render(<Report/>); await until(()=>calls.length===3); finish(2,'reject')
  await until(()=>useApp.getState().route==='plan')
  if(!useApp.getState().toasts.some(t=>t.kind==='error'))throw Error('active report failure lost normal error handling')
  activeRoot.unmount()

  const numbers:any={}
  for(const state of Object.keys(samples)){
    window.showProgress219(state,'sakura');await until(()=>document.querySelector('.progress-status')?.textContent?.trim()===expected[state])
    const actual=document.querySelector('.progress-status')!.textContent!.trim()
    if(actual!==expected[state])throw Error(`${state} status/numbers changed: ${actual}`)
    if(actual.includes('待确认'))throw Error(`${state} still labels tentative minutes separately`)
    numbers[state]=actual
  }
  window.result219={ok:true,checks:['离页后的旧失败无toast且不导航','离页后的旧成功无副作用','当前失败仍toast并返回计划页','四种状态数字保持（暂记仍计入汇总）'],expected,numbers}
}
run().catch(e=>window.result219={ok:false,error:String(e),calls})
