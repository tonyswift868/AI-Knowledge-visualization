import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {PlanDetail} from '../../../refactor/learning/web/src/pages/PlanDetail'
import {useApp} from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'
import {control,emit,requests} from './status88_bridge'
const w=window as any
const root=createRoot(document.getElementById('root')!)
const pause=(ms:number)=>new Promise(r=>setTimeout(r,ms))
const checks:string[]=[]
function assert(ok:unknown, name:string){if(!ok)throw Error(name+': '+document.body.innerText);checks.push(name)}
async function until(fn:()=>boolean){const end=performance.now()+6000;while(!fn()){if(performance.now()>end)throw Error(document.body.innerText);await pause(10)}}
function mount(key:string){useApp.setState({planId:'fixture',ready:true});root.render(<PlanDetail key={key}/>)}
async function run(){
  mount('initial')
  await until(()=>!!document.querySelector('.knowledge-map'))
  await pause(350)
  assert(document.body.innerText.includes('正在按内容重算知识点时长'),'minute initial running without event')
  assert(document.body.innerText.includes('正在后台准备模块题目'),'preparation initial running without event')
  assert(document.body.innerText.includes('正在整理知识点之间的关系'),'links initial running without event')
  control.minutes='ready';control.links='ready';control.preparation='ready';control.total=30
  emit('plan.point_minutes_changed','ready');emit('plan.point_links_changed','ready');emit('plan_preparation','ready')
  await until(()=>!document.querySelector('.wait')&&document.body.innerText.includes('0.5 小时'))
  assert(true,'completion refreshes totals and removes all waits')
  control.minutes='failed';control.links='partial';control.preparation='failed'
  emit('plan.point_minutes_changed','failed');emit('plan.point_links_changed','partial');emit('plan_preparation','failed')
  await until(()=>document.body.innerText.includes('重试时长重算'))
  assert(document.body.innerText.includes('进入对应模块可重试'),'preparation failure uses existing classroom retry')
  const retry=Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='重试时长重算')!
  retry.click();await until(()=>requests.includes('plan.retry_point_minutes'));await pause(350)
  assert(document.body.innerText.includes('正在按内容重算'),'minute retry starts wait')
  const linkRetry=Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='重试')!
  linkRetry.click();await until(()=>requests.includes('plan.retry_point_links'));await pause(350)
  assert(document.body.innerText.includes('正在整理知识点'),'links retry starts wait')
  w.status88Show=async(theme:string,mode:string)=>{
    w.status88Ready=false
    document.documentElement.dataset.theme=theme
    control.minutes=mode==='minutes'?'running':'ready';control.links=mode==='links'?'running':'ready';control.preparation='ready'
    mount(theme+mode)
    await until(()=>!!document.querySelector('.knowledge-map'))
    await pause(3100)
    const marker=document.createElement('div');marker.id='marker88';marker.style.cssText='position:fixed;left:0;top:0;width:2px;height:2px;z-index:99999;background:'+ (theme==='sakura'?'#aa112':'#bb223')+(mode==='minutes'?'2':'3')
    document.getElementById('marker88')?.remove();document.body.append(marker)
    await pause(50);w.status88Ready=true
  }
  w.status88Result={ok:true,checks,requests}
}
run().catch(e=>w.status88Result={ok:false,error:String(e),stack:e.stack})
