import * as React from "react"
import { createRoot } from "react-dom/client"
import { NewPlan } from "../../../refactor/learning/web/src/pages/NewPlan"
import { useApp } from "../../../refactor/learning/web/src/store"
import { show } from "../plan61/graph61_bridge"

const root = createRoot(document.getElementById("root")!)
const data = (window as any).graph61Data
const before = (window as any).graph62Before
const check = (value: any, why: string) => { if (!value) throw Error(why) }
async function until(f: () => any, why: string) {
  const end = performance.now() + 6000
  while (!f()) { if (performance.now() > end) throw Error(why); await new Promise(r => setTimeout(r, 20)) }
}
function render(name: string, theme = "sakura") {
  document.documentElement.dataset.theme = theme
  show(structuredClone(data[name])); root.render(<NewPlan />)
}
;(window as any).graph62Show = render
useApp.getState().set({planId: data.structure.plan_id})
const clickNode = (index = 0) => document.querySelectorAll<HTMLElement>('.react-flow__node-module')[index].click()
const panel = () => document.querySelector<HTMLElement>('.pg-details')
const escape = () => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))

async function run() {
  const results = []
  for (const mode of ['structure', 'outline']) {
    render(mode)
    await until(() => document.querySelector(mode === 'structure' ? '[aria-label="资料结构图"]' : '[aria-label="知识地图"]'), mode)
    await until(() => document.querySelectorAll('.react-flow__node-module').length >= 2, 'nodes')
    clickNode(); await until(panel, 'panel opens')
    const clone = structuredClone(data[mode])
    const modules = mode === 'structure' ? clone.material_structure.modules : clone.outline.stages.flatMap((s: any) => s.modules)
    modules[0].summary += '（刷新后的内容）'
    show(clone)
    await until(() => document.querySelector('.pg-module-summary')?.textContent?.includes('刷新后的内容'), 'host refresh rendered')
    if (before) {
      await until(() => !panel(), 'baseline layout refresh closes panel')
      clickNode(); await until(panel, 'reopen baseline')
      document.querySelector<HTMLElement>('.react-flow__pane')!.click()
      await until(() => !panel(), 'baseline blank canvas closes panel')
      results.push({mode, opened: true, survives_refresh: false, blank_canvas_closes: true})
      continue
    }
    check(panel()?.textContent?.includes('刷新后的内容'), 'refresh keeps selection and updates detail')
    const drawer = panel()
    for (let i = 0; i < 5; i++) show(structuredClone(clone))
    // A real parent state change, without changing the graph, must retain the drawer.
    const input = document.querySelector<HTMLTextAreaElement>('textarea')!
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, '我想调整')
    input.dispatchEvent(new Event('input', {bubbles:true}))
    await until(() => input.value === '我想调整', 'input render')
    document.querySelector<HTMLElement>('.react-flow__pane')!.click()
    await until(() => document.querySelector('.pg-details p')?.textContent?.includes('刷新后的内容'), 'refresh settled')
    check(panel() === drawer, 'drawer never remounted or cleared by refresh/input/canvas')
    if (mode === 'structure') {
      check(document.querySelector<HTMLDetailsElement>('.pg-chapters')?.open, 'all chapter references visible immediately')
      check(document.querySelectorAll('.pg-chapters li').length === modules[0].chapters.length, 'all chapter references')
      check(panel()?.textContent?.includes('知识点将从这些资料章节生成'), 'generation explanation')
      check(!document.querySelector('.pg-points'), 'structure has no invented knowledge points')
    } else check(document.querySelectorAll('.pg-points > li').length === modules[0].points.length, 'all points retained')
    const scrollable = panel()!
    check(scrollable.scrollHeight > scrollable.clientHeight, 'detail scrolls internally')
    scrollable.scrollTop = 200
    check(scrollable.scrollTop > 0 && scrollable.clientHeight <= 460, 'bounded panel')
    const closeRect = scrollable.querySelector('button')!.getBoundingClientRect()
    const panelRect = scrollable.getBoundingClientRect()
    check(closeRect.top >= panelRect.top && closeRect.bottom <= panelRect.bottom, 'close remains fully visible after scrolling')
    clickNode(1)
    await until(() => panel()?.getAttribute('aria-label') === `${modules[1].title}详情`, 'switch module')
    const close = document.querySelector<HTMLButtonElement>('[aria-label="关闭模块详情"]')!
    check(close.textContent?.includes('关闭'), 'visible close button')
    close.click(); await until(() => !panel(), 'explicit close')
    clickNode(); await until(panel, 'reopen')
    escape(); await until(() => !panel(), 'Escape closes normal drawer')
    clickNode(); await until(panel, 'reopen before fullscreen')
    ;[...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '全屏查看')!.click()
    await until(() => document.querySelector('.pg-fullscreen .pg-details'), 'fullscreen retains selection')
    escape(); await until(() => !panel(), 'Escape closes fullscreen drawer')
    check(document.querySelector('.pg-fullscreen'), 'first Escape only closes drawer')
    escape(); await until(() => !document.querySelector('.pg-fullscreen'), 'second Escape exits fullscreen')
    results.push({mode, opened:true, survives_refresh:true, blank_canvas_closes:false, explicit_close:true, escape:true, switches:true, internal_scroll:true})
  }
  ;(window as any).plan37Result = {ok:true, baseline:before, modes:results}
}
run().catch(e => { (window as any).plan37Result = {ok:false,error:String(e),body:document.body.textContent} })
