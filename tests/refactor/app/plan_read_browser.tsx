import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Home } from '../../../refactor/learning/web/src/pages/Home'
import { Plans } from '../../../refactor/learning/web/src/pages/Plans'
import { useApp } from '../../../refactor/learning/web/src/store'
import { emit, setFail } from './plan_read_browser_bridge'

declare global { interface Window { result?: { ok: boolean; checks: string[]; error?: string } } }
const checks: string[] = []
const wait = () => new Promise((resolve) => setTimeout(resolve, 80))
const requireText = (text: string) => {
  if (!document.body.textContent?.includes(text)) throw new Error(`missing ${text}: ${document.body.textContent}`)
  checks.push(text)
}
const retry = () => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('重试'))
  if (!button) throw new Error('missing retry button')
  button.click()
}
async function main() {
  useApp.getState().set({ready: true, account: {name:'同学', role_label:'学习者'}})
  const root = createRoot(document.getElementById('root')!)
  root.render(<Home />)
  await wait(); requireText('读取失败'); retry()
  setFail(false); retry(); await wait(); requireText('学习日 2026-09-30')
  setFail(true); emit('study_day'); await wait(); requireText('可能不是最新')
  root.render(<Plans />); await wait(); requireText('读取失败')
  setFail(false); retry(); await wait(); requireText('旧计划')
  useApp.getState().set({ready: false}); await wait()
  setFail(true); useApp.getState().set({ready: true}); await wait(); requireText('可能不是最新')
  window.result = {ok: true, checks}
}
main().catch((error) => { window.result = {ok: false, checks, error: String(error)} })
