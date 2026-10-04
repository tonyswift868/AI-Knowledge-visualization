import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {NewPlan} from '../../../refactor/learning/web/src/pages/NewPlan'
import {PlanDetail} from '../../../refactor/learning/web/src/pages/PlanDetail'
import {useApp} from '../../../refactor/learning/web/src/store'
import {fixture} from './plan42_browser_bridge'
declare global {interface Window {result:any}}
const root=createRoot(document.getElementById('root')!)
useApp.getState().set({planId:'plan42'})
root.render(<NewPlan />)
const wait=(ms=120)=>new Promise(r=>setTimeout(r,ms))
const button=(label:string)=>[...document.querySelectorAll('button')].find(b=>b.textContent?.trim()===label) as HTMLButtonElement
const assert=(ok:any,why:string)=>{if(!ok)throw Error(why)}
async function run(){
  await wait(300)
  assert(button('交流时能解释'),'可点回答选项')
  button('交流时能解释').click();await wait()
  assert(fixture.calls.find(c=>c.method==='plan.say').params.text==='交流时能解释','选项送出用户原话')
  assert(button('确认，开始找资料'),'确认模块预览入口')
  assert(!button('让 AI 推荐 / 换一批'),'确认前没有搜索入口')
  button('我想调整').click();await wait()
  const input=document.querySelector('textarea') as HTMLTextAreaElement
  assert(!input.disabled && input.value.includes('调整'),'可以继续聊')
  const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!
  setter.call(input,'不要硬件，增加网络');input.dispatchEvent(new Event('input',{bubbles:true}));await wait()
  button('发送').click();await wait()
  assert(document.body.textContent?.includes('网络｜'),'新版预览呈现')
  assert([...document.querySelectorAll('button')].filter(b=>b.textContent?.trim()==='确认，开始找资料').length===1,'旧预览不能确认')
  button('确认，开始找资料').click();await wait()
  assert(button('让 AI 推荐 / 换一批'),'确认后资料操作')
  root.render(<PlanDetail />);await wait(200)
  const warnings=document.body.textContent?.match(/这个模块有较多知识点用的是原文填空题/g)||[]
  assert(warnings.length===1,'只对cloze_heavy模块显示说明')
  window.result={ok:true,passed:['选项回答','预览确认与修改','确认后搜索','cloze_heavy详情提示']}
}
run().catch(e=>{window.result={ok:false,error:String(e),body:document.body.textContent}})
