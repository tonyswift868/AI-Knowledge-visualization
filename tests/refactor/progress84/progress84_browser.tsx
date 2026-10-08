import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Classroom } from '../../../refactor/learning/web/src/pages/Classroom'
import { ProgressStatus, aggregateProgress } from '../../../refactor/learning/web/src/ProgressStatus'
import { useApp } from '../../../refactor/learning/web/src/store'
import states from './states.json'
import { holdNext, releaseNext } from '../conversation37/conversation37_bridge'
import '../../../refactor/learning/web/src/theme.css'

declare global { interface Window { conversationResult: object; progress84Show: (mode: string, theme: string) => number } }
const root = createRoot(document.getElementById('root')!)
const samples: Record<string, object> = {
  debt: { today_target: 60, today_confirmed: 0, today_tentative: 0, debt_minutes: 36, today_repaid_confirmed: 0, today_repaid_tentative: 24, needs_negotiation: false, oldest_debt_day: '2026-10-07' },
  severe: { today_target: 60, today_confirmed: 0, today_tentative: 0, debt_minutes: 156, today_repaid_confirmed: 8, today_repaid_tentative: 16, needs_negotiation: true, oldest_debt_day: '2026-10-05' },
  today: { today_target: 60, today_confirmed: 16, today_tentative: 8, debt_minutes: 0, ahead_minutes: 0 },
  ahead: { today_target: 60, today_confirmed: 60, today_tentative: 0, debt_minutes: 0, ahead_minutes: 24, ahead_tentative: 8 },
}
let renderCount = 0
useApp.getState().set({ ready: true, planId: 'a'.repeat(32), settings: { reduce_motion: true } as never })
window.progress84Show = (mode, theme) => {
  document.documentElement.dataset.theme = theme
  for (const state of Object.values(states)) state.today = { ...state.today, ...samples[mode], needs_rest: false, is_late_night: false } as never
  root.render(<main style={{ height: '100%', padding: 12 }}><Classroom key={++renderCount} /></main>)
  return renderCount
}
const tick = () => new Promise(resolve => setTimeout(resolve, 10))
const wait = async (condition: () => boolean) => { for (let n = 0; n < 1000; n++) { if (condition()) return; await tick() } throw Error('UI not ready') }
const require = (condition: boolean, text: string) => { if (!condition) throw Error(text) }
async function check() {
  for (const [mode, expected] of Object.entries({ debt: '逾期待还 36 分今天已还 24 分含待确认 24 分', severe: '逾期待还 156 分今天已还 24 分含待确认 16 分', today: '今天 24 / 60 分含待确认 8 分', ahead: '超前完成 24 分含待确认 8 分' })) {
    window.progress84Show(mode, 'sakura')
    await wait(() => document.querySelector('.class-head [data-progress]')?.getAttribute('data-progress') === mode)
    require(document.querySelector('.class-head .progress-status')!.textContent === expected, `${mode} numbers/text`)
    require(document.querySelectorAll('.class-head .progress-status').length === 1, `${mode} only one state`)
    if (mode !== 'today') require(!document.querySelector('.progress-status')!.textContent!.includes(' / '), `${mode} no daily fraction`)
  }
  // Actual Classroom response application updates the top badge, no re-entry.
  window.progress84Show('debt', 'sakura')
  const openingButton = () => [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '针尖')
  await wait(() => !!openingButton())
  states.opening.today = { ...states.opening.today, debt_minutes: 60, today_repaid_tentative: 0 } as never
  states.teaching.today = { ...states.teaching.today, ...samples.debt } as never
  openingButton()!.click()
  await wait(() => document.querySelector('.progress-status')?.textContent === '逾期待还 36 分今天已还 24 分含待确认 24 分')
  require(!openingButton(), 'new response applied without remount')
  // Hold the next response like a slow teacher; progress must arrive first.
  const credited = { ...states.teaching.today, debt_minutes: 28, today_repaid_tentative: 32 }
  holdNext(credited)
  const nextButton = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '懂了，继续')!
  nextButton.click()
  await wait(() => document.querySelector('.progress-status')?.textContent === '逾期待还 28 分今天已还 32 分含待确认 32 分')
  require(nextButton.disabled, 'action still in progress')
  require(!document.body.textContent!.includes('TODAY:'), 'UI event not shown as teacher speech')
  releaseNext()
  await wait(() => !nextButton.isConnected)
  const debt = samples.debt as never, ahead = samples.ahead as never
  root.render(<ProgressStatus value={aggregateProgress([debt, ahead])} />)
  await wait(() => !document.querySelector('.class-main'))
  require(document.querySelector('.progress-status')?.getAttribute('data-progress') === 'debt', 'multi-plan debt not masked by ahead')
  window.progress84Show('today', 'sakura')
  await wait(() => !!document.querySelector('.class-main'))
  window.conversationResult = { ok: true, samples: 4, response_refresh: true, progress_before_teacher_response: true }
}
check().catch(error => { window.conversationResult = { ok: false, error: String(error) } })
