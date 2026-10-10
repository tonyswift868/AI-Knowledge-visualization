import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { UpdateNotice } from '../../../refactor/learning/web/src/updates/UpdateNotice'
import { useApp } from '../../../refactor/learning/web/src/store'
declare global { interface Window { result: any; captureReady: boolean; captured: boolean } }
useApp.setState({ ready: true, route: 'home' })
createRoot(document.getElementById('root')!).render(<UpdateNotice />)
const wait = (ms: number) => new Promise(r => setTimeout(r, ms))
async function run() {
  for (let i = 0; i < 100; i++) {
    if (document.querySelector('[role="dialog"]')) break
    await wait(50)
  }
  const dialog = document.querySelector('[role="dialog"]')
  const text = dialog?.textContent || ''
  if (!dialog || text.includes('继续登录') || !text.includes('关于')) throw Error('登录后提示文字不正确: ' + text)
  window.captureReady = true
  while (!window.captured) await wait(30)
  window.result = { ok: true, text }
}
run().catch(e => { window.result = { ok: false, error: String(e) } })
