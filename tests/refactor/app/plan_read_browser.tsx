import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Home } from '../../../refactor/learning/web/src/pages/Home'
import { Plans } from '../../../refactor/learning/web/src/pages/Plans'
import { useApp } from '../../../refactor/learning/web/src/store'
import { emit, setFail } from './plan_read_browser_bridge'

declare global { interface Window { result?: { ok: boolean; checks: string[]; error?: string } } }
const checks: string[] = []
const wait = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms))
const waitForText = async (text: string, timeout = 5000) => {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (document.body.textContent?.includes(text)) return
    await wait()
  }
  throw new Error(`missing ${text}: ${document.body.textContent}`)
}
const requireText = (text: string) => {
  if (!document.body.textContent?.includes(text)) throw new Error(`missing ${text}: ${document.body.textContent}`)
  checks.push(text)
}
const retry = async () => {
  const deadline = Date.now() + 5000
  while (Date.now() < deadline) {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('重试'))
    if (button) { button.click(); return }
    await wait()
  }
  throw new Error('missing retry button')
}
async function main() {
  useApp.getState().set({ready: true, account: {name:'同学', role_label:'学习者'}})
  const root = createRoot(document.getElementById('root')!)
  root.render(<Home />)
  await waitForText('读取失败'); await retry()
  setFail(false); await retry(); await waitForText('学习日 2026-09-30')
  setFail(true); emit('study_day'); await waitForText('可能不是最新')
  root.render(<Plans />); await waitForText('读取失败')
  setFail(false); await retry(); await waitForText('旧计划')
  useApp.getState().set({ready: false}); await wait()
  setFail(true); useApp.getState().set({ready: true}); await waitForText('可能不是最新')
  window.result = {ok: true, checks}
}
main().catch((error) => { window.result = {ok: false, checks, error: String(error)} })
