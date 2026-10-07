import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Settings } from '../../../refactor/learning/web/src/pages/Settings'
import { useApp } from '../../../refactor/learning/web/src/store'
import { fixture, emit } from './spend81_browser_bridge'

declare global { interface Window { result?: any; captureReady?: string; captured?: string } }
const checks: string[] = []
const wait = (ms=25) => new Promise((r)=>setTimeout(r,ms))
async function until(fn: ()=>boolean, label: string) {
  const end = Date.now()+6000
  while (Date.now()<end) { if (fn()) { checks.push(label); return }; await wait() }
  throw new Error(label + ': ' + document.body.textContent)
}
const text = (s: string) => document.body.textContent?.includes(s) ?? false
async function capture(name: string) {
  window.captureReady = name
  await until(()=>window.captured === name, '截图 '+name)
  window.captureReady = ''
}
async function main() {
  useApp.getState().set({route:'settings',settingsTab:'usage',ready:true})
  const root = createRoot(document.getElementById('root')!)
  root.render(<div style={{padding:24}}><Settings /></div>)
  await until(()=>text('¥1.25')&&text('¥3.40')&&text('¥8.75'), '三张花费卡')
  await until(()=>document.querySelectorAll('[role=listitem]').length===14,'14 天柱状图')
  await until(()=>text('85.0%')&&text('输入 token')&&text('如果这把 Key 也在别处使用'),'保留 token / 缓存 / 提示')
  const titles=[...document.querySelectorAll('[role=listitem]')].map((d)=>d.getAttribute('title'))
  if (!titles.includes('2026-10-14：¥1.25')) throw new Error('chart tooltip')
  checks.push('柱状图日期与金额提示')
  document.documentElement.dataset.theme='sakura'; await capture('spend-sakura')
  document.documentElement.dataset.theme='aurora'; await capture('spend-aurora')
  fixture.single=true; emit()
  await until(()=>text('刚开始记录，下次查询后显示花费'),'一次记录边界')
  if (document.querySelectorAll('.stat .value')[0].textContent !== '—') throw new Error('single sample misleading zero')
  fixture.single=false; emit(); await until(()=>text('¥1.25'),'恢复已有花费')
  fixture.fail=true; emit(); await until(()=>text('花费暂时无法读取')&&text('¥1.25'),'失败保留已有花费')
  fixture.fail=false
  useApp.getState().go('settings','models')
  await until(()=>text('今天花了 ¥1.25'),'预设列显示花费')
  await until(()=>!!document.querySelector('[title="账户余额：¥96.75"]'),'余额仅在悬停提示')
  await capture('preset-aurora')
  document.documentElement.dataset.theme='sakura'; await capture('preset-sakura')
  window.result={ok:true,checks,requests:fixture.requests}
}
main().catch((error)=>{window.result={ok:false,checks,error:String(error)}})
