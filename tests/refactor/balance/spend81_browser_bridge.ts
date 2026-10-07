export class BridgeFailure extends Error { code = 'OFFLINE_TEST' }
const listeners = new Map<string, Set<(data: any) => void>>()
export function on(name: string, fn: (data: any) => void) {
  const set = listeners.get(name) ?? new Set(); set.add(fn); listeners.set(name, set)
  return () => { set.delete(fn) }
}
export function emit() { listeners.get('balance_spend')?.forEach((fn) => fn({preset_id:'p'})) }
export const fixture = { single: false, fail: false, requests: [] as string[] }
const preset = { id:'p', provider_id:'deepseek', display_name:'DeepSeek 测试预设', model:'deepseek-v4-flash',
  base_url:'https://api.deepseek.com', timeout:120, temperature:null, has_api_key:true }
const amounts = [0, .16, .2, .31, .02, 0, .3, 0, .03, .07, .4, .61, .6, 1.25]
export async function call(method: string, params: any = {}) {
  fixture.requests.push(method)
  if (method === 'usage.spend') {
    if (fixture.fail) throw new Error('offline balance failure')
    return {preset, spend:{ supported:true, ready:!fixture.single, observations:fixture.single ? 1 : 16,
      currencies:[{currency:'CNY', ready:!fixture.single, today:fixture.single ? '0' : '1.25', week:'3.40', month:'8.75',
        days:amounts.map((amount,i)=>({date:`2026-10-${String(i+1).padStart(2,'0')}`,amount:String(fixture.single ? 0 : amount)}))}],
      balance:{ label:'账户余额', balances:[{currency:'CNY',amount:'96.75'}], updated_at:new Date().toISOString() }}}
  }
  if (method === 'usage.summary') {
    const stats = {calls:4, failed:1, cancelled:0, in_progress:0, input_tokens:1000, cached_input_tokens:850,
      uncached_input_tokens:150, output_tokens:100, search_requests:0, cache_hit_rate:'0.85', usage_complete:true,
      unknown_fields:[], incomplete_reasons:{}}
    return {total:stats, groups:[{...stats, key:'teacher'}]}
  }
  if (method === 'models.list') return {presets:[preset], active_id:'p'}
  if (method === 'models.providers') return {providers:[{id:'deepseek',name:'DeepSeek',default_model:preset.model,
    default_base_url:preset.base_url,search_supported:true,search_verified:true}]}
  return {}
}
