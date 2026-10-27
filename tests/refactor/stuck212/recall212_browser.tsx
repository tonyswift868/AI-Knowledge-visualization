import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Classroom } from '../../../refactor/learning/web/src/pages/Classroom'
import { useApp } from '../../../refactor/learning/web/src/store'
import { calls, diagnostic, show, showPersisted, showUnrevealed } from './recall212_bridge'

declare global { interface Window { stuck212: { show: typeof show; showPersisted: typeof showPersisted; showUnrevealed: typeof showUnrevealed; diagnostic: typeof diagnostic; calls: typeof calls } } }
const root = createRoot(document.getElementById('root')!)
let generation = 0
const content = () => document.body.textContent ?? ''
const sleep = (ms = 25) => new Promise(resolve => setTimeout(resolve, ms))
async function until(predicate: () => boolean, label: string) {
  const deadline = Date.now() + 12000
  while (Date.now() < deadline) { if (predicate()) return; await sleep() }
  throw new Error(`Timed out (${label}): ${content()}`)
}
function render(kind: 'check' | 'module' | 'practice' | 'classroom', unrevealed = false) {
  if (unrevealed) showUnrevealed(kind); else show(kind)
  useApp.getState().set({ route: 'classroom', planId: '212-plan', sidebarCollapsed: true,
    settings: { theme: 'sakura', reduce_motion: true } as never })
  root.render(<main style={{ height: '100%', padding: 12 }}><Classroom key={++generation}/></main>)
}
function button(label: string) {
  const value = [...document.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === label)
  if (!value || value.disabled) throw new Error(`Unavailable button: ${label}`)
  return value
}
function fillAnswer(text: string) {
  const input = document.querySelector<HTMLTextAreaElement>('.recall-own textarea')!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, text)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
function metrics() {
  const scroll = document.querySelector<HTMLElement>('.class-scroll')!
  const outside = document.querySelector<HTMLElement>('.answer-explanation')
  const source = document.querySelector<HTMLElement>('.class-scroll .bubble.bot')
  const next = [...document.querySelectorAll<HTMLButtonElement>('.recall-result button')].at(-1)
  return {
    scrollHeight: scroll.scrollHeight, clientHeight: scroll.clientHeight, scrollTop: scroll.scrollTop,
    scrollable: scroll.scrollHeight > scroll.clientHeight, answerSourceCount: document.querySelectorAll('.class-scroll .bubble.bot').length,
    outsideExplanationHeight: outside && !scroll.contains(outside) ? outside.scrollHeight : 0,
    explanationInsideScroller: !!outside && scroll.contains(outside), sourceText: source?.textContent?.length ?? 0,
    referenceVisible: !!document.querySelector('.recall-reference'),
    merchantLineCount: (document.body.innerText.match(/商家维度：/g) ?? []).length,
    nextButton: next?.textContent?.trim() ?? null,
  }
}

window.stuck212 = { show, showPersisted, showUnrevealed, diagnostic, calls }
window.addEventListener('error', event => console.error('stuck212 browser error', event.error || event.message))
const results: Record<string, unknown> = {}
async function run() {
  // Full React interaction first, before scenario remounts: answer -> reveal -> tick -> self-judge.
  render('check', true)
  await until(() => !!document.querySelector('.recall-question') && calls.some(c => c.method === 'classroom.enter'), 'interactive question entered')
  fillAnswer('分片键要结合主要查询路径，并兼顾数据分布。')
  await until(() => {
    const candidate = [...document.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === '写好了，看参考答案')
    return !!candidate && !candidate.disabled
  }, 'answer enabled')
  button('写好了，看参考答案').click()
  await until(() => document.querySelectorAll('.recall-point input').length === 2, 'reference reveal')
  const drafts = calls.filter(call => call.method === 'class.recall_draft').length
  document.querySelector<HTMLInputElement>('.recall-point input')!.click()
  await until(() => calls.filter(call => call.method === 'class.recall_draft').length > drafts, 'draft tick save')
  await until(() => {
    const candidate = [...document.querySelectorAll<HTMLButtonElement>('.recall-review .row button')].find(item => item.textContent?.trim() === '算我答错')
    return !!candidate && !candidate.disabled
  }, 'draft completes')
  button('算我答错').click()
  await until(() => !!document.querySelector('.recall-result'), 'self-judgement result')
  const submit = calls.filter(call => call.method === 'class.recall_submit').at(-1)?.params
  if (submit?.correct !== false || JSON.stringify(submit.ticked) !== '[1]') throw new Error('self judgement and checked-point record mismatch')
  const actual = metrics()
  results['interactive-check-wrong'] = actual
  const resultScroll = document.querySelector<HTMLElement>('.class-scroll')!
  resultScroll.scrollTop = resultScroll.scrollHeight
  await sleep(60)
  const next = [...document.querySelectorAll<HTMLButtonElement>('.recall-result button')].at(-1)
  const rect = next?.getBoundingClientRect(), bounds = resultScroll.getBoundingClientRect()
  if (!rect || rect.top < bounds.top || rect.bottom > bounds.bottom || next?.textContent?.trim() !== '下一题')
    throw new Error(`self-judgement continuation unavailable after scroll: ${JSON.stringify(metrics())}`)

  for (const scenario of ['check', 'module', 'practice', 'classroom'] as const) {
    for (const correct of [false, true]) {
      const key = `${scenario}-${correct ? 'right' : 'wrong'}`
      ;(window as any).stuck212Progress = `${key}: render persisted DOM snapshot`
      window.stuck212.showPersisted(scenario, correct)
      useApp.getState().set({ route: 'classroom', planId: '212-plan', sidebarCollapsed: true,
        settings: { theme: 'sakura', reduce_motion: true } as never })
      root.render(<main style={{ height: '100%', padding: 12 }}><Classroom key={++generation}/></main>)
      await until(() => !!document.querySelector('.recall-question'), `${key} question`)
      await sleep(120)
      const snapshot = metrics()
      results[key] = snapshot
      const scroll = document.querySelector<HTMLElement>('.class-scroll')!
      scroll.scrollTop = scroll.scrollHeight
      await sleep(80)
      if (scroll.scrollTop <= 0) throw new Error(`${key} cannot scroll the long explanation: ${JSON.stringify(metrics())}`)
      const ask = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === '我想问…')
      if (!ask) throw new Error(`${key} ask-teacher control missing`)
      ask.click(); await sleep(20)
      const input = document.querySelector<HTMLTextAreaElement>('.composer textarea')
      if (!input || input.disabled) throw new Error(`${key} ask-teacher input did not activate`)
      ;(window as any).stuck212Progress = `${key}: done`
    }
  }
  window.dispatchEvent(new Event('stuck212done'))
  ;(window as any).stuck212Result = { ok: true, results }
}
run().catch(error => { (window as any).stuck212Result = { ok: false, error: String(error?.stack || error), results, calls }; console.error(error) })
