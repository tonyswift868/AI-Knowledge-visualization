import * as React from "react"
import {createRoot} from "react-dom/client"
import {NewPlan, visiblePlanMessages} from "../../../refactor/learning/web/src/pages/NewPlan"
import {useApp} from "../../../refactor/learning/web/src/store"
import {show, calls} from "../plan61/graph61_bridge"
const data = (window as any).graph61Data
const root = createRoot(document.getElementById('root')!)
const wait = (ms: number) => new Promise(r => setTimeout(r, ms))
const check = (v: any, why: string) => {if (!v) throw Error(why)}
const button = (t: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.includes(t))!
function render(mode: string) {show(structuredClone(data[mode])); root.render(<NewPlan />)}
;(window as any).plan37Show = (theme: string) => {
  document.documentElement.dataset.theme = theme; render(theme === 'aurora' ? 'over' : 'structure')
  setTimeout(() => document.querySelector('.structure-estimate')?.scrollIntoView({block:'end'}), 100)
}
useApp.getState().set({planId: data.structure.plan_id})
async function run() {
  check(visiblePlanMessages([{id:'old',role:'ai',kind:'estimate',text:'旧上限'}, {id:'new',role:'ai',kind:'estimate',text:'新上限'}]).length === 1, 'latest estimate only')
  render('structure'); await wait(350)
  check(document.body.textContent?.includes('填知识点前的粗略时间预估'), 'estimate before generation')
  check(document.body.textContent?.includes('输入约'), 'input tokens shown')
  check(!document.querySelector('.time-choices'), 'no warning below user cap')
  document.querySelector<HTMLSelectElement>('[aria-label="定位模块"]')!.value = data.structure.material_structure.modules[0].key
  document.querySelector<HTMLSelectElement>('[aria-label="定位模块"]')!.dispatchEvent(new Event('change',{bubbles:true}))
  await wait(150)
  check(document.querySelector('.pg-details')?.textContent?.includes('估计'), 'module point range')
  render('over'); await wait(200)
  check(document.body.textContent?.includes(`每日上限 ${data.over.material_structure.ranges.cap}`), 'actual cap')
  check(!button('确认结构，开始填知识点').disabled, 'ignore warning allowed')
  button('把每天时长提高到').click(); await wait(100)
  check(calls.at(-1)?.method === 'plan.window' && calls.at(-1)?.params.changes.daily_cap === data.over.material_structure.ranges.suggested_cap, 'raise exact rounded cap via host')
  button('把结束日期延到').click(); await wait(100)
  check(calls.at(-1)?.params.changes.end === data.over.material_structure.ranges.suggested_end, 'extend via same host')
  button('调整内容').click(); await wait(100)
  check(document.querySelector<HTMLTextAreaElement>('.plan-chat textarea')?.value.includes('调整') || document.querySelector<HTMLTextAreaElement>('textarea')?.value.includes('调整'), 'adjust contents')
  check(document.activeElement?.tagName === 'TEXTAREA', 'focus adjustment input')
  render('outline'); await wait(200)
  check(document.body.textContent?.includes('平均 8 分钟'), 'exact eight minute estimate')
  check(!button('确认，保存').disabled && button('把每天时长提高到'), 'precise choices and ignore')
  render('over'); await wait(100)
  document.querySelector('.structure-estimate')?.scrollIntoView({block:'center'})
  ;(window as any).plan37Result = {ok:true, checks:['structure estimate','module detail','user cap','raise','extend','adjust','exact estimate','ignore warning']}
}
run().catch(e => {(window as any).plan37Result={ok:false,error:String(e)}})
