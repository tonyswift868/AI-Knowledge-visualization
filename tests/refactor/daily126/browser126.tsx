import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {PlanDetail} from '../../../refactor/learning/web/src/pages/PlanDetail'
import {useApp} from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'
import {data} from './bridge126'
const w=window as any
const root=createRoot(document.getElementById('root')!)
useApp.setState({planId:'p',ready:true})
const checks:string[]=[]
async function until(fn:()=>boolean) {
  const end=performance.now()+15000
  while(!fn()){if(performance.now()>end)throw Error(document.body.innerText);await new Promise(r=>setTimeout(r,20))}
}
function daily() {
  return [...document.querySelectorAll('.outline-sum > div')].find(e=>e.textContent?.includes('每日任务时长'))?.querySelector('b')?.textContent
}
async function run() {
  root.render(<PlanDetail key="first"/> )
  await until(()=>daily()===`${data.first.daily.full_day} 分钟`)
  if(document.body.innerText.includes('超过了你设的上限'))throw Error('错误的超上限提示')
  checks.push('延期后的详情采用真实账本目标，不包含欠账')
  w.daily126Second=true
  root.render(<PlanDetail key="second"/> )
  await until(()=>daily()===`${data.second.daily.full_day} 分钟`)
  await until(()=>document.body.innerText.includes(`每日任务时长 ${data.second.daily.full_day} 分钟，超过了你设的上限 10 分钟`))
  checks.push('debt_spread 后显示新的目标和正确的超上限提示')
  w.conversationResult={ok:true,checks}
}
run().catch(e=>w.conversationResult={ok:false,error:String(e.stack||e)})
