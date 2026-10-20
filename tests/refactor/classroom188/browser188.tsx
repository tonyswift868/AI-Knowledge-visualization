import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Classroom } from '../../../refactor/learning/web/src/pages/Classroom'
import { useApp } from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'
import { calls, finishSlow188, publishReady188, scenario188 } from './bridge188'

declare global { interface Window { classroom188Result?: any; mount188: (name: string) => Promise<void>; ready188: () => void; finishSlow188: () => void; calls188: typeof calls } }
const root = createRoot(document.getElementById('root')!)
function AppHarness() {
  const route = useApp(state => state.route)
  return route === 'home' ? <div data-testid="home188">首页</div> : <Classroom />
}
let mountId = 0
const pause = () => new Promise(resolve => setTimeout(resolve, 20))
const text = () => document.body.textContent ?? ''
const checks: string[] = []
const errors: string[] = []
window.addEventListener('error', e => errors.push(String(e.message || e.error || 'window error')))
window.addEventListener('unhandledrejection', e => errors.push(String(e.reason || 'unhandled rejection')))
async function until(check: () => boolean, label: string) {
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) { if (check()) return; await pause() }
  throw new Error(`Timed out: ${label}; text=${text()}`)
}
function button(label: string) {
  const found = [...document.querySelectorAll<HTMLButtonElement>('button')]
    .find(b => b.textContent?.trim() === label || b.getAttribute('aria-label') === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
function settledModule(title: string) {
  const visibleTitle = document.querySelector('.class-title > b')?.textContent?.trim()
  const busy = [...document.querySelectorAll<HTMLButtonElement>('button')].some(b => b.textContent?.trim() === '停止')
  return visibleTitle === title && !busy
}
async function mount(name: string) {
  scenario188(name)
  const thisMount = ++mountId
  useApp.setState({ planId: 'a'.repeat(32), route: 'classroom', account: null,
    settings: { theme: 'sakura', reduce_motion: true } as never })
  root.render(<main data-harness188={thisMount} style={{ height: '100vh' }}><AppHarness key={thisMount}/></main>)
  await until(() => {
    const harness = document.querySelector<HTMLElement>(`[data-harness188="${thisMount}"]`)
    return !!harness?.querySelector('.class-scroll') && calls.some(c => c.method === 'classroom.enter')
  }, `classroom mount ${thisMount} enter complete`)
}
window.mount188 = mount
window.calls188 = calls
window.ready188 = () => {
  publishReady188()
}
window.finishSlow188 = finishSlow188

async function run() {
  await mount('teaching')
  if (!text().includes('老师照着讲义解释面积与压强')) throw new Error('lesson content disappeared while questions unready')
  checks.push('题未就绪时课堂仍显示讲课内容与可开始入口')

  await mount('testOffer')
  const test = button('开始模块检测')
  if (!test.disabled) throw new Error('module test button is not gated while bank is preparing')
  if (!document.querySelector('.class-switch-module188')) throw new Error('module switch menu should be available before any slow action')
  if (!text().includes('这个模块的题还在准备，好了再考')) throw new Error('module test wait notice missing')
  if (button('先学下一个模块').disabled) throw new Error('next module option was locked')
  button('先学下一个模块').click()
  await until(() => settledModule('模块二'), 'switch to next module')
  await until(() => document.querySelector('.class-deferred-tests188')?.textContent.includes('回去检测 · 模块一'), 'deferred test entry')
  if (!calls.some(c => c.method === 'classroom.next_module')) throw new Error('next module action was not sent')
  if (!text().includes('回去检测 · 模块一')) throw new Error('deferred module return entry missing')
  button('回去检测 · 模块一').click()
  await until(() => text().includes('开始模块检测'), 'return to deferred module')
  if (!button('开始模块检测').disabled) throw new Error('deferred module should retain its not-ready state')
  window.ready188()
  await until(() => !button('开始模块检测').disabled, 'automatic readiness refresh')
  button('开始模块检测').click()
  await until(() => text().includes('检测题已准备好'), 'start newly ready module test')
  checks.push('检测题未就绪时可转学下一模块，延期模块仍有入口')

  await mount('slowTestOffer')
  const testInput = document.querySelector<HTMLTextAreaElement>('.composer textarea')!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(testInput, '我想确认一道学习问题')
  testInput.dispatchEvent(new Event('input', { bubbles: true }))
  button('发送').click()
  await until(() => text().includes('老师回复仍在生成'), 'slow reply started at test offer')
  if (!button('开始模块检测').disabled) throw new Error('test action should remain locked during a teacher reply')
  window.ready188()
  await until(() => !document.querySelector('[data-testid="material-notice188"]'), 'readiness merged while teacher reply is busy')
  if (!text().includes('老师回复仍在生成')) throw new Error('readiness refresh replaced the active teacher stream')
  if (!calls.some(c => c.method === 'classroom.state')) throw new Error('materials_ready did not request a read-only state refresh')
  window.finishSlow188()
  await until(() => !button('开始模块检测').disabled, 'slow reply settled after readiness')
  checks.push('备题就绪在老师回复期间合并到门禁，不覆盖流或课堂位置')

  await mount('slowObjective')
  const scoredInput = document.querySelector<HTMLTextAreaElement>('.composer textarea')!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(scoredInput, '第一次作答')
  scoredInput.dispatchEvent(new Event('input', { bubbles: true }))
  button('发送').click()
  await until(() => text().includes('老师回复仍在生成'), 'scored answer request pending')
  const firstReplyCount = calls.filter(c => c.method === 'classroom.reply').length
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(scoredInput, '第二次作答')
  scoredInput.dispatchEvent(new Event('input', { bubbles: true }))
  if (!button('发送').disabled) throw new Error('scored reply send button remained enabled while pending')
  button('发送').click()
  await pause()
  if (calls.filter(c => c.method === 'classroom.reply').length !== firstReplyCount) throw new Error('second scored answer was sent while the first was pending')
  if (calls.some(c => c.method === 'cancel')) throw new Error('second scored answer cancelled the pending request')
  if (scoredInput.value !== '第二次作答') throw new Error('blocked duplicate submission cleared the input draft')
  window.finishSlow188()
  await until(() => !button('发送').disabled, 'scored reply settled')
  checks.push('题卡输入的计分 reply 等待期间不能被第二次发送接管，草稿保留')

  await mount('prepError')
  if (!text().includes('题目准备失败') || text().includes('课堂操作失败')) throw new Error('preparation enqueue failure was shown as a teacher failure')
  button('重试').click()
  await until(() => calls.some(c => c.method === 'classroom.retry'), 'retry preparation')
  checks.push('后台排队失败独立显示并调用现有 retry，不冒充老师回复错误')

  await mount('slowTeaching')
  button('懂了，继续').click()
  await until(() => !!document.querySelector('.composer textarea') && text().includes('旧操作的迟到文字'), 'slow action started')
  const input = document.querySelector<HTMLTextAreaElement>('.composer textarea')!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, '我有个问题')
  input.dispatchEvent(new Event('input', { bubbles: true }))
  const send = button('发送')
  if (send.disabled) throw new Error('send button locked while teacher action is slow')
  send.click()
  await until(() => text().includes('新回复已接上'), 'message takes over slow action')
  await new Promise(resolve => setTimeout(resolve, 180))
  if (text().includes('旧操作的迟到文字')) throw new Error('stale stream was rendered after replacement')
  checks.push('慢老师请求中可发消息，旧流与旧快照不能覆盖新一轮')

  await mount('slowTeaching')
  button('懂了，继续').click()
  await until(() => !!document.querySelector('.class-switch-module188'), 'module switch during slow action')
  document.querySelector<HTMLButtonElement>('.class-switch-module188 summary')!.click()
  button('模块二').click()
  await until(() => settledModule('模块二'), 'module switch completes and settles')
  checks.push('慢老师请求中可立即切模块')

  await mount('preparing')
  const resume = [...document.querySelectorAll<HTMLButtonElement>('.class-wait129 button')][0]
  if (!resume || resume.disabled || resume.textContent?.trim() !== '先回去接着学') throw new Error('resume lesson button is disabled/missing')
  resume.click()
  await until(() => !document.querySelector('.class-wait129'), 'resume from waiting')
  if (!calls.some(c => c.method === 'classroom.cancel_preparation')) throw new Error('wait exit action missing')
  if (!text().includes('老师照着讲义解释面积与压强')) throw new Error('wait exit did not return to lesson')
  checks.push('等待按钮可用，退出后显示原讲课位置')

  await mount('slowTeaching')
  button('懂了，继续').click()
  await until(() => !!document.querySelector('.composer textarea') && text().includes('旧操作的迟到文字'), 'slow action before leave')
  button('下课').click()
  await until(() => calls.some(c => c.method === 'classroom.leave'), 'leave sent during slow action')
  checks.push('慢老师请求中下课立即发送 leave')

  if (errors.length) throw new Error('browser errors: ' + errors.join(' | '))
  window.classroom188Result = { ok: true, checks }
}
run().catch(error => { window.classroom188Result = { ok: false, checks, errors, error: String(error.stack || error), dom: text(), calls: structuredClone(calls) } })

