import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { UpdateBanner, UpdateNotice } from '../../../refactor/learning/web/src/updates/UpdateNotice'
import { useApp } from '../../../refactor/learning/web/src/store'
declare global { interface Window { result: any; captureReady: boolean; captured: boolean } }
useApp.setState({ ready: true, route: 'home' })
createRoot(document.getElementById('root')!).render(<>
  <div className="shell"><main className="main"><header className="topbar">MentorFlow</header><UpdateBanner/><div className="content">主界面可继续使用</div></main></div>
  <UpdateNotice />
</>)
const wait = (ms: number) => new Promise(r => setTimeout(r, ms))
async function run() {
  for (let i = 0; i < 100; i++) {
    if (document.querySelector('[aria-label="软件更新提示"]')) break
    await wait(50)
  }
  const banner = document.querySelector('[aria-label="软件更新提示"]')
  const text = banner?.textContent || ''
  const rect = banner?.getBoundingClientRect()
  if (!banner || !text.includes('2.0.6') || document.querySelector('[role="dialog"]') || document.querySelector('.shell')?.inert)
    throw Error('可选更新应显示为非模态主界面横幅: ' + document.body.innerText)
  if (!banner.closest('.main') || !rect || rect.width <= 0 || rect.height <= 0 || rect.top < 0 || rect.bottom > innerHeight)
    throw Error('更新横幅应在主界面可见区域: ' + JSON.stringify(rect))
  window.captureReady = true
  while (!window.captured) await wait(30)
  banner.querySelector('button')?.click()
  await wait(100)
  const dialog = document.querySelector('[role="dialog"]')
  if (!dialog || !dialog.textContent?.includes('2.0.6')) throw Error('用户点击横幅后应进入更新流程')
  window.result = { ok: true, text, explicitClickOpensUpdate: true, mainScreenRemainsUsable: true }
}
run().catch(e => { window.result = { ok: false, error: String(e) } })
