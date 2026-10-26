import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(process.argv[2])
const screenshots = path.resolve(process.argv[3])
const endpoint = process.env.MF_CDP_ENDPOINT
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
let target
for (let i = 0; i < 150; i++) {
  try {
    target = (await (await fetch(endpoint + '/json/list')).json()).find(t => t.type === 'page' && t.url.startsWith('file:') &&
      path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase() + path.sep))
    if (target) break
  } catch {}
  await pause(100)
}
if (!target) throw Error('Owned WebEngine page missing')
const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
let id = 0
const pending = new Map()
let eventSequence = 0
ws.onmessage = event => {
  const data = JSON.parse(event.data)
  if (!data.id) return
  const row = pending.get(data.id)
  pending.delete(data.id)
  if (data.error) row.reject(Error(JSON.stringify(data.error)))
  else row.resolve(data.result)
}
const cdp = (method, params = {}) => new Promise((resolve, reject) => {
  const requestId = ++id
  pending.set(requestId, { resolve, reject })
  ws.send(JSON.stringify({ id: requestId, method, params }))
})
async function js(expression) {
  const reply = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (reply.exceptionDetails) throw Error(JSON.stringify(reply.exceptionDetails))
  return reply.result.value
}
async function until(expression, label) {
  for (let i = 0; i < 250; i++) {
    if (await js(expression)) return
    await pause(40)
  }
  throw Error(`Timed out waiting for ${label || expression}; body=${await js('document.body.innerText.slice(-1800)')}`)
}
function assert(condition, message) { if (!condition) throw Error(message) }
async function event(name, data) {
  const sequence = ++eventSequence
  const marker = path.join(root, 'event199.json')
  const ack = path.join(root, 'event199.ack')
  try { fs.unlinkSync(ack) } catch {}
  fs.writeFileSync(marker, JSON.stringify({ name, data, sequence }), 'utf8')
  await until(`(()=>{try{return document.documentElement.dataset.event199Ack==="${sequence}"}catch{return false}})()`, `event ${name} delivery`)
}
async function screenshot(file) {
  const result = await cdp('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(path.join(screenshots, file), Buffer.from(result.data, 'base64'))
}

try {
  await until('!!document.querySelector(".model-status.unknown")', 'initial unknown chip')
  await event('scope_changed', {})
  await event('ready', { account: { name: '测试账号 B', account_id: 'account-199-b', role_label: '学习者' } })
  await until('!!document.querySelector(".model-status.checking")', 'checking state for current account')
  await pause(1350) // Let the delayed status response from account A arrive.
  assert(await js('document.querySelector(".model-status")?.textContent.trim()==="正在检查模型"'),
    'late models.status response from the previous scope overwrote the current checking state')
  await event('model_status', { state: 'error', short: '旧账号故障', account_id: 'account-199-a',
    fault: { id: 'old-fault', category: 'network', code: 'OLD', short: '旧账号故障', message: '旧账号网络故障', actions: [] } })
  await pause(100)
  assert(await js('document.querySelector(".model-status")?.textContent.trim()==="正在检查模型"'),
    'late model_status push from previous account was not ignored')

  await js('window.model199.enterClassroom()')
  await until('!!document.querySelector(".class-scroll")', 'classroom entered while model check is active')
  assert(await js('document.querySelector(".model-status.checking") && !document.querySelector(".classroom-loading")'),
    'checking status should not block classroom render')
  await screenshot('model-checking-199.png')

  await event('model_status', { state: 'ok', short: '模型正常', account_id: 'account-199-b' })
  await until('!!document.querySelector(".model-status.ok") && document.querySelector(".model-status").textContent.includes("模型正常")', 'normal state')
  await screenshot('model-ok-199.png')

  await event('model_status', { state: 'error', short: '模型连接失败', account_id: 'account-199-b',
    fault: { id: 'network-fault-199', category: 'network', code: 'MODEL_CONNECTION_FAILED',
      short: '模型连接失败', message: '连不上模型服务，请检查网络连接后重试。', purpose: 'connection_test', plan_id: '',
      actions: [{ id: 'retry', label: '重试' }, { id: 'settings', label: '设置 → 模型连接' }] } })
  await until('!!document.querySelector(".model-status.error") && !!document.querySelector(".model-fault-toast")', 'failure state')
  await screenshot('model-error-199.png')
  assert(await js('!document.querySelector("#model-status-details")'), 'failure details should stay closed until the chip is clicked')
  await js('document.querySelector(".model-status.error").click()')
  await until('!!document.querySelector("#model-status-details .model-fault-panel")', 'failure details opened from chip')
  assert(await js('document.querySelector("#model-status-details").innerText.includes("连不上模型服务") && [...document.querySelectorAll("#model-status-details button")].some(b=>b.textContent.trim()==="重试") && [...document.querySelectorAll("#model-status-details button")].some(b=>b.textContent.includes("设置 → 模型连接"))'),
    'failure panel should expose categorized reason, retry and settings actions')
  await screenshot('model-error-actions-199.png')
  assert(fs.readFileSync(path.join(root, 'manual-test-count199'), 'utf8') === '0', 'manual model test ran before user selected retry')
  await js('[...document.querySelectorAll("#model-status-details button")].find(b=>b.textContent.includes("设置 → 模型连接")).click()')
  await until('document.querySelector(".settings-layout") && [...document.querySelectorAll(".subnav button")].some(b=>b.textContent.includes("模型连接"))',
    'failure settings action opens model connection settings')
  await js('window.model199.goHome()')
  await until('!!document.querySelector(".model-status.error")', 'return home with same model failure')
  await js('[...document.querySelectorAll("#model-status-details button")].find(b=>b.textContent.trim()==="重试").click()')
  await until('document.querySelector(".model-status.ok")?.textContent.includes("模型正常")', 'retry runs model test and receives normal result')
  assert(fs.readFileSync(path.join(root, 'manual-test-count199'), 'utf8') === '1', 'retry action must call models.test exactly once')

  await event('model_status', { state: 'unconfigured', short: '未配置 API Key', account_id: 'account-199-b',
    fault: { id: 'key-missing-199', category: 'key', code: 'MODEL_KEY_REQUIRED', short: '未配置 API Key',
      message: '当前模型还没有 API Key，请到设置 → 模型连接填入 API Key 并保存。', purpose: 'connection_test', plan_id: '',
      actions: [{ id: 'settings', label: '设置 → 模型连接' }] } })
  await until('!!document.querySelector(".model-status.unconfigured") && document.querySelector(".model-status").textContent.includes("未配置 API Key") && !document.querySelector(".model-fault-toast")', 'unconfigured state')
  assert(await js('document.querySelector(".model-status").getAttribute("aria-busy")==="false"'), 'unconfigured state remains busy')
  await screenshot('model-unconfigured-199.png')
  await js('document.querySelector(".model-status").click()')
  await until('document.querySelector(".settings-layout") && [...document.querySelectorAll(".subnav button")].some(b=>b.textContent.includes("模型连接"))',
    'unconfigured chip opens model settings')
  await pause(180)

  fs.writeFileSync(path.join(root, 'result199.json'), JSON.stringify({ states: ['checking', 'ok', 'error', 'unconfigured'],
    settingsOpenedFromUnconfigured: true, failureActions: ['settings', 'retry'], retryCalls: 1 }))
  fs.writeFileSync(path.join(root, 'done199'), '')
} finally { ws.close() }
