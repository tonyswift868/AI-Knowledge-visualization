import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Settings } from '../../../refactor/learning/web/src/pages/Settings'
import { useApp } from '../../../refactor/learning/web/src/store'
import { fixture, tickTimers } from './spend81_browser_bridge'
import { addMoney, money } from '../../../refactor/learning/web/src/usageMoney'

declare global { interface Window { result?: any; captureReady?: string; captured?: string } }
const checks: string[] = []
const wait = (ms=30) => new Promise((r)=>setTimeout(r,ms))
const text = (s: string) => document.body.textContent?.includes(s) ?? false
async function until(fn: ()=>boolean, label: string) {
  const end = Date.now()+6000
  while (Date.now()<end) { if (fn()) { checks.push(label); return }; await wait() }
  throw new Error(label + ': ' + document.body.textContent)
}
function button(label: string, within?: string) {
  const scope = within ? [...document.querySelectorAll<HTMLElement>('[role="alert"]')]
    .find((item)=>item.textContent?.includes(within)) : undefined
  const found = [...(scope ?? document).querySelectorAll('button')].find((item)=>item.textContent?.trim()===label)
  if (!found) throw new Error(`button not found: ${label}`)
  found.click()
}
async function capture(name: string) {
  const animations=[...document.querySelectorAll('.page')].flatMap(page=>page.getAnimations())
    .filter(animation=>Number.isFinite(animation.effect?.getComputedTiming().endTime))
  await Promise.all(animations.map(animation=>animation.finished.catch(()=>undefined)))
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  // Qt's grab reads its last composited frame, after DOM styles and animations settle.
  await wait(60)
  window.captureReady = name
  await until(()=>window.captured === name, '截图 '+name)
  window.captureReady = ''
}
async function main() {
  const intervals: number[] = []
  const nativeInterval = window.setInterval
  const nativeClearInterval = window.clearInterval
  window.setInterval = ((callback: TimerHandler, delay?: number) => {
    intervals.push(delay ?? 0)
    const id = -intervals.length
    fixture.timers.set(id, () => typeof callback === 'function' && callback())
    return id as unknown as number
  }) as typeof window.setInterval
  window.clearInterval = ((id: number) => { fixture.timers.delete(id) }) as typeof window.clearInterval
  useApp.getState().set({route:'settings',settingsTab:'usage',ready:true})
  const root = createRoot(document.getElementById('root')!)
  root.render(<div style={{padding:24}}><Settings /></div>)

  await until(()=>text('0.000015')&&text('本应用估算费用'),'精确显示小额首页费用')
  if (money('0.000015','CNY')!=='¥0.000015' || money('1.5e-7','USD')!=='$0.00000015'
      || addMoney('0.1','0.2')!=='0.3' || addMoney('0.000015','0.000005')!=='0.000020')
    throw new Error('exact decimal/exponent currency formatter regression')
  if (text('连接测试') || text('逐笔费用') || fixture.requests.some(({method})=>method==='usage.summary'||method==='usage.details'))
    throw new Error('usage details shown before user opens details')
  if (intervals.length!==1 || intervals[0]!==30000) throw new Error('overview must refresh on a 30 second interval')
  checks.push('首页仅请求 overview 与 spend，隐藏明细')

  await until(()=>text('今天')&&text('本周')&&text('本月'),'显示本地日周月汇总')
  await until(()=>text('账户余额')&&text('48.61'),'厂商账户余额独立显示')
  const spendRequests = fixture.requests.filter(({method})=>method==='usage.spend')
  if (spendRequests.length!==1) throw new Error('balance query should remain separate from app usage overview')
  if (text('21.38') || text('今天花了') || text('最近 14 天')) throw new Error('legacy balance delta leaked into app usage')
  document.documentElement.dataset.theme='sakura'; await capture('spend-sakura')
  document.documentElement.dataset.theme='aurora'; await capture('spend-aurora')

  button('查看详情')
  await until(()=>text('连接测试')&&text('0.000015'),'详情按需加载并展示逐笔金额')
  const detailRequest = fixture.requests.find(({method})=>method==='usage.details')
  if (!detailRequest) throw new Error('details bridge method not called')
  if (!text('近 7 天') || !text('按用途') || !text('按模型') || !text('按本地日期'))
    throw new Error('detail filters/groupings missing')
  if (!document.body.textContent?.includes('已显示 2 / 3 次调用') || !document.body.textContent?.includes('失败'))
    throw new Error('detail rows/count/status missing')
  const summaryCalls = fixture.requests.filter(({method})=>method==='usage.summary').length
  const detailsCalls = fixture.requests.filter(({method})=>method==='usage.details').length
  if (summaryCalls!==1 || detailsCalls!==1) throw new Error('opening details should request one summary and first page')
  fixture.incoming=true
  button('加载更多')
  await until(()=>text('已显示 3 / 3 次调用'),'加载更多逐笔记录')
  if (fixture.requests.filter(({method})=>method==='usage.details').length!==2) throw new Error('second page not requested')
  const pageRequests=fixture.requests.filter(({method})=>method==='usage.details')
  if (pageRequests[0].params.end!==undefined || pageRequests[1].params.end!=='2026-11-06T12:00:00-07:00'
      || pageRequests[1].params.offset!==2) throw new Error('pagination must retain first page end anchor')
  if (text('找资料')) throw new Error('new call shifted anchored pagination')
  fixture.incoming=false
  button('近 30 天')
  await until(()=>fixture.requests.filter(({method})=>method==='usage.details').length===3,'切换范围重新加载首屏')
  const changedRange=fixture.requests.filter(({method})=>method==='usage.details')[2]
  if (changedRange.params.days!==30 || changedRange.params.offset!==0 || changedRange.params.end!==undefined)
    throw new Error('changing range must reset first page without old end anchor')
  button('按模型')
  await until(()=>!!document.querySelector('[aria-label="用量详情"] tbody')?.textContent?.includes('deepseek · deepseek-v4-flash'),'按模型汇总')
  button('按本地日期')
  await until(()=>!!document.querySelector('[aria-label="用量详情"] tbody')?.textContent?.includes('2026-11-06'),'按本地日期汇总')
  const beforeRefresh=fixture.requests.filter(({method})=>method==='usage.details').length
  button('刷新用量')
  await until(()=>fixture.requests.filter(({method})=>method==='usage.details').length===beforeRefresh+1,'显式刷新重建逐笔窗口')
  const refreshed=fixture.requests.filter(({method})=>method==='usage.details').at(-1)!
  if(refreshed.params.offset!==0 || refreshed.params.end!==undefined) throw new Error('refresh retained old pagination window')
  button('关闭详情')
  await until(()=>!text('逐笔费用 · 估算')&&text('查看详情'),'关闭详情返回简洁首页')
  button('查看详情')
  await until(()=>fixture.requests.filter(({method})=>method==='usage.details').length===beforeRefresh+2,'重新打开详情重新请求首屏')
  if (fixture.requests.filter(({method})=>method==='usage.details').at(-1)!.params.end!==undefined)
    throw new Error('reopened details must not inherit previous end anchor')
  button('关闭详情')
  await until(()=>text('查看详情'),'重开后关闭详情')

  fixture.failDetails=true
  button('查看详情')
  await until(()=>text('逐笔费用暂时无法读取')&&text('重试'),'详情失败可重试')
  fixture.failDetails=false
  button('重试','逐笔费用暂时无法读取')
  await until(()=>text('已显示 2 / 3 次调用'),'详情重试成功')
  button('关闭详情')
  await until(()=>text('查看详情'),'重试后关闭详情')

  fixture.mode='zero'; tickTimers()
  await until(()=>document.querySelectorAll('.stat .value').length===3 && [...document.querySelectorAll('.stat .value')].every(el=>el.textContent==='0'),'没有调用时明确显示零')
  fixture.mode='unknown'; tickTimers()
  await until(()=>text('未知')&&text('2 次费用未知')&&text('1 次仅返回部分用量'),'全部未知及部分用量')
  fixture.mode='partial'; tickTimers()
  await until(()=>text('已知小计；另有 1 次费用未知'),'部分计价明确显示未知部分')
  fixture.mode='multi'; tickTimers()
  await until(()=>text('¥0.00002')&&text('$0.000002'),'同币种精确合并，多币种分别展示')

  fixture.failOverview=true; tickTimers()
  await until(()=>text('本应用用量暂时无法读取')&&text('重试'),'overview失败展示重试')
  fixture.failOverview=false
  button('重试')
  await until(()=>!text('本应用用量暂时无法读取')&&text('¥0.00002'),'overview失败后重试成功')

  fixture.today='2026-11-07'; tickTimers()
  await until(()=>text('2026/11/7'),'30秒刷新跨午夜后更新日期')
  fixture.failSpend=true
  button('刷新余额')
  await until(()=>text('厂商账户余额暂时无法读取')&&text('48.61'),'余额失败保留上次结果')
  fixture.failSpend=false
  button('刷新余额')
  await until(()=>!text('厂商账户余额暂时无法读取')&&text('48.61'),'余额失败后重试成功')
  useApp.getState().go('settings','models')
  await until(()=>text('已保存的预设')&&text('¥48.61'),'预设列表显示厂商账户余额')
  if (text('21.38') || text('今天花了') || text('今天花费')) throw new Error('preset list still uses balance deltas')
  await capture('preset-aurora')
  document.documentElement.dataset.theme='sakura'; await capture('preset-sakura')
  root.unmount()
  window.setInterval=nativeInterval
  window.clearInterval=nativeClearInterval

  window.result={ok:true,checks,requests:fixture.requests}
}
main().catch((error)=>{window.result={ok:false,checks,error:String(error)}})
