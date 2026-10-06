import * as React from "react"
import {createRoot} from "react-dom/client"
import {NewPlan, requirementWithoutDates} from "../../../refactor/learning/web/src/pages/NewPlan"
import {useApp} from "../../../refactor/learning/web/src/store"
import {show, calls} from "../plan61/graph61_bridge"
const data = (window as any).graph61Data
const root = createRoot(document.getElementById('root')!)
const wait = (ms: number) => new Promise(r => setTimeout(r, ms))
const check = (v: any, why: string) => {if (!v) throw Error(why)}
const button = (t: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.includes(t))!
function render(mode: string) {show(structuredClone(data[mode])); root.render(<NewPlan />)}
;(window as any).plan65Show = (mode: string) => {render(mode);setTimeout(()=>document.querySelector('.structure-estimate')?.scrollIntoView({block:'end'}),100)}
;(window as any).plan37Show = () => (window as any).plan65Show('possible')
useApp.getState().set({planId: data.structure.plan_id})
async function run() {
  const goal='在 2026-10-06 至 2026-10-15 的 10 天内，掌握 Java 后端和 AI Agent，达到面试程度。'
  check(requirementWithoutDates(goal)==='掌握 Java 后端和 AI Agent，达到面试程度。','strip old dates without changing learning goal')
  render('structure');await wait(350)
  check(document.querySelector('.plan-head')?.textContent?.includes('2026-10-06 至 2026-10-30 · 每天最多 600 分钟'),'form dates in header')
  check(!document.querySelector('.plan-head')?.textContent?.includes('10 天内'),'no old days in header')
  check(document.querySelector('.structure-estimate .outline-sum')?.textContent?.includes('498 / 600'),'usual daily main figure')
  check(!document.querySelector('.time-choices')&&!document.querySelector('.estimate-impossible')&&!document.querySelector('.estimate-possible'),'worst case exceeds but usual fits: no warning')
  render('possible');await wait(180)
  check(document.querySelector('.estimate-possible')&&!document.querySelector('.estimate-impossible'),'mild hint only')
  check(button('把每天时长提高到 500')?.classList.contains('ghost'),'secondary adjustments')
  button('把每天时长提高到').click();await wait(80)
  check(calls.at(-1)?.params.changes.daily_cap===500,'usual cap rather than worst')
  button('把结束日期延到').click();await wait(80)
  check(calls.at(-1)?.params.changes.end==='2026-11-02','usual date rather than worst')
  button('调整内容').click();await wait(80)
  check(document.activeElement?.tagName==='TEXTAREA','adjust content focus')
  render('impossible');await wait(180)
  check(document.querySelector('.estimate-impossible')?.textContent?.includes('按最乐观的估计也完成不了'),'yellow serious warning')
  check(button('把每天时长提高到 830')&&button('把结束日期延到 2026-12-07'),'serious uses usual buttons')
  check(document.querySelector('.plan-window')?.textContent?.includes('2026-10-20 · 每天最多 200'),'updated form header immediately')
  render('structure');await wait(80)
  ;(window as any).plan37Result={ok:true,checks:['median main numbers','no worst-case warning','mild','serious','usual buttons','form dates','content focus']}
}
run().catch(e=>{(window as any).plan37Result={ok:false,error:String(e)}})
