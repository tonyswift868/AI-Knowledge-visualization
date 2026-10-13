import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Classroom } from '../../../refactor/learning/web/src/pages/Classroom'
import { useApp } from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'
import { show, calls } from './direct97_bridge'

declare global { interface Window { direct97Result?: object; direct97Show: (stage: 'entry' | 'original' | 'choice' | 'graded') => void } }
const root = createRoot(document.getElementById('root')!)
const wait = () => new Promise(resolve => setTimeout(resolve, 20))
const content = () => document.body.textContent ?? ''
const checks: string[] = []
function button(text: string) {
  const value = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === text)
  if (!value) throw new Error(`Missing button: ${text}`)
  return value
}
async function until(predicate: () => boolean) {
  for (let i = 0; i < 250; i++) { if (predicate()) return; await wait() }
  throw new Error(`Timed out: ${content()}`)
}
function fill(text: string) {
  const input = document.querySelector('textarea')!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, text)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
let generation = 0
window.direct97Show = stage => {
  show(stage)
  root.render(<main style={{ height: '100%', padding: 18 }}><Classroom key={++generation}/></main>)
}
async function run() {
  useApp.setState({ planId: 'd'.repeat(32), route: 'classroom', settings: { theme: 'sakura', reduce_motion: true } as never })
  document.documentElement.dataset.theme = 'sakura'
  window.direct97Show('entry')
  await until(() => content().includes('跳过该知识点'))
  checks.push('讲到一半仍有直接考入口')
  document.querySelector<HTMLButtonElement>('[role="switch"]')!.click()
  await until(() => content().includes('跳过该模块'))
  button('跳过该模块').click()
  await until(() => content().includes('确认，进入直接考'))
  button('确认，进入直接考').click()
  await until(() => content().includes('随口聊聊 1/3'))
  if (document.querySelector('.bank-question-controls')) throw new Error('Unscored dialogue became a scored question card')
  for (let i = 0; i < 3; i++) {
    fill('我按自己的理解说一句。'); await wait(); button('发送').click()
    await until(() => content().includes('对照原文，自己判断理解得对不对') && !!document.querySelector<HTMLTextAreaElement>('textarea')?.disabled)
    if (content().includes('回到刚才')) throw new Error('Lecture controls leaked into unscored direct review')
    if (!content().includes('队列采用先进先出的顺序')) throw new Error('Source not shown after answer')
    if (i < 2) { button('看完原文，下一句').click(); await until(() => content().includes(`随口聊聊 ${i + 2}/3`) && !document.querySelector<HTMLTextAreaElement>('textarea')?.disabled) }
  }
  checks.push('随口回答后显示原文，不使用计分卡；三句后两个自主选择按钮')
  button('接着学').click()
  await until(() => !!document.querySelector('[role="switch"]'))
  if (!calls.some(c => c.method === 'classroom.direct_choice' && c.params.skip === false)) throw new Error('Resume choice bridge missing')
  window.direct97Show('choice')
  await until(() => content().includes('跳过这个模块'))
  button('跳过这个模块').click()
  await until(() => content().includes('你选择跳过这个模块'))
  if (!calls.some(c => c.method === 'classroom.direct_choice' && c.params.skip === true)) throw new Error('Skip choice bridge missing')
  window.direct97Show('graded')
  await until(() => !!document.querySelector('.bank-question-controls'))
  if (document.querySelector('[role="switch"]')) throw new Error('Direct entry visible while answering')
  checks.push('选择接着学/跳过分别传 false/true；计分答题中不显示直接考入口')
  window.direct97Result = { ok: true, checks }
}
run().catch(error => { window.direct97Result = { ok: false, checks, error: String(error.stack || error) } })
