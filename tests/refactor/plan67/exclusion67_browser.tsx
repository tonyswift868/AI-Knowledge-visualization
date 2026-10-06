import * as React from "react"
import {createRoot} from "react-dom/client"
import {NewPlan} from "../../../refactor/learning/web/src/pages/NewPlan"
import {useApp} from "../../../refactor/learning/web/src/store"
import {show} from "../plan61/graph61_bridge"
const data = (window as any).graph61Data
const root = createRoot(document.getElementById('root')!)
const wait = (ms: number) => new Promise(r => setTimeout(r, ms))
const check = (v: any, why: string) => {if (!v) throw Error(why)}
function render() {show(structuredClone(data.structure)); root.render(<NewPlan />)}
;(window as any).plan37Show = () => {render();setTimeout(()=>document.querySelector('.material-excluded')?.scrollIntoView({block:'center'}),100)}
useApp.getState().set({planId:data.structure.plan_id})
async function run() {
  render();await wait(350)
  const note=document.querySelector('.material-excluded')!
  const expected=data.structure.material_structure.excluded
  check(expected.length&&expected.every((r: any)=>note?.textContent?.includes(r.name)),'excluded names visible')
  check(note.classList.contains('muted')&&note.classList.contains('small'),'grey small note')
  check(note.textContent?.includes('把××也加进来'),'restoration instruction')
  check(document.querySelector('.material-excluded span')?.getAttribute('title')===expected[0].reason,'reason visible')
  check([...document.querySelectorAll('button')].some(b=>b.textContent?.includes('确认结构')),'visible before confirmation')
  check(!document.querySelector('.material-missing')?.textContent?.includes('导航'),'excluded is not missing')
  ;(window as any).plan37Result={ok:true,checks:['names','grey note','reason','restore instruction','before confirmation','not missing']}
}
run().catch(e=>{(window as any).plan37Result={ok:false,error:String(e)}})
