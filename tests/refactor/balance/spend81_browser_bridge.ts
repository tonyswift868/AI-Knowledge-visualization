export class BridgeFailure extends Error { code = 'OFFLINE_TEST' }

const listeners = new Map<string, Set<(data: any) => void>>()
export function on(name: string, fn: (data: any) => void) {
  const set = listeners.get(name) ?? new Set(); set.add(fn); listeners.set(name, set)
  return () => { set.delete(fn) }
}
export function emit(name = 'balance_spend') { listeners.get(name)?.forEach((fn) => fn({preset_id:'p'})) }

export const fixture = {
  failSpend: false,
  failOverview: false,
  failDetails: false,
  mode: 'tiny',
  today: '2026-11-06',
  incoming: false,
  detailCalls: 0,
  timers: new Map<number, () => void>(),
  requests: [] as Array<{method:string; params:any}>,
}
export function tickTimers() { fixture.timers.forEach((tick) => tick()) }

const preset = { id:'p', provider_id:'deepseek', display_name:'DeepSeek 测试预设', model:'deepseek-v4-flash',
  base_url:'https://api.deepseek.com', timeout:120, temperature:null, has_api_key:true }

const agg = (amounts: Record<string,string> = {CNY:'0.000015'}, extra: Record<string,any> = {}) => ({
  calls:1, failed:0, cancelled:0, in_progress:0, input_tokens:12, cached_input_tokens:4,
  uncached_input_tokens:8, output_tokens:3, search_requests:0, cache_hit_rate:'0.333333',
  usage_complete:true, unknown_fields:[], incomplete_reasons:{}, transport_unknown_calls:0,
  cancelled_unknown_calls:0, partial_usage_calls:0, known_amounts:{}, estimated_amounts:amounts,
  unpriced_calls:0, priced_calls:1, estimated_calls:1, amount_complete:true, ...extra,
})

const period = (total:any) => ({start:`${fixture.today}T00:00:00-07:00`,end:`${fixture.today}T12:00:00-07:00`,total})

export async function call(method: string, params: any = {}) {
  fixture.requests.push({method, params})
  if (method === 'usage.spend') {
    if (fixture.failSpend) throw new Error('offline provider balance failure')
    return {preset, spend:{supported:true,ready:true,observations:16,currencies:[{currency:'CNY',ready:true,
      today:'21.38',week:'21.38',month:'21.38',days:[{date:'2026-11-06',amount:'21.38'}]}],balance:{label:'账户余额',
      balances:[{currency:'CNY',amount:'48.61'}],updated_at:'2026-11-06T12:00:00-07:00'}}}
  }
  if (method === 'usage.overview') {
    if (fixture.failOverview) throw new Error('offline overview failure')
    if (fixture.mode === 'zero') {
      const empty=agg({}, {calls:0,input_tokens:0,cached_input_tokens:0,uncached_input_tokens:0,output_tokens:0,cache_hit_rate:null})
      return {timezone:'America/Denver',periods:{today:period(empty),week:period(empty),month:period(empty)}}
    }
    if (fixture.mode === 'unknown') {
      const unknown = agg({}, {calls:2, estimated_amounts:{}, known_amounts:{}, unpriced_calls:2, priced_calls:0,
        estimated_calls:0, amount_complete:false, usage_complete:false, input_tokens:null, unknown_fields:['input_tokens'],
        partial_usage_calls:1})
      return {timezone:'America/Denver',periods:{today:period(unknown),week:period(unknown),month:period(unknown)}}
    }
    const amounts = fixture.mode === 'multi' ? {CNY:'0.000015',USD:'0.000002'} : {CNY:'0.000015'}
    const total = agg(amounts, fixture.mode === 'partial' ? {calls:2, unpriced_calls:1, priced_calls:1,
      estimated_calls:1, amount_complete:false, partial_usage_calls:1, usage_complete:false} :
      fixture.mode==='multi'?{known_amounts:{CNY:'0.000005'}}:{})
    return {timezone:'America/Denver',periods:{today:period(total),week:period(total),month:period(total)}}
  }
  if (method === 'usage.details') {
    fixture.detailCalls += 1
    if (fixture.failDetails) throw new Error('offline details failure')
    const end = params.end ?? `${fixture.today}T12:00:00-07:00`
    const records = [1,2,3].map(id => ({request_id:`req-${id}`,
      at_utc:`${fixture.today}T18:0${4-id}:00Z`,provider_id:'deepseek',model:'deepseek-v4-flash',purpose:'connection_test',
      state:id===2?'failed':'succeeded',error_code:id===2?'OFFLINE':null,
      usage:fixture.mode==='partial'?{input_tokens:12,_mentorflow:{partial_usage:true}}:{input_tokens:12,cached_input_tokens:4,output_tokens:3},
      cost:fixture.mode==='unknown'?null:{amount:'0.000015',currency:'CNY'}}))
    // A new call arriving after the first page must not shift its anchored next page.
    if (fixture.incoming && !params.end) records.unshift({...records[0],request_id:'incoming',purpose:'research'})
    return {start:`${fixture.today}T00:00:00-07:00`,end,timezone:'America/Denver',
      total_calls:records.length, items:records.slice(params.offset,params.offset+2),
      next_offset:params.offset+2 < records.length ? params.offset+2 : null}
  }
  if (method === 'usage.summary') return {start:`${fixture.today}T00:00:00-07:00`,end:`${fixture.today}T12:00:00-07:00`,
    timezone:'America/Denver',total:agg(),groups:[{key:params.group_by==='model'?['deepseek','deepseek-v4-flash']:
      params.group_by==='day'?fixture.today:'connection_test',...agg()}]}
  if (method === 'models.list') return {presets:[preset],active_id:'p'}
  if (method === 'models.providers') return {providers:[{id:'deepseek',name:'DeepSeek',default_model:preset.model,
    default_base_url:preset.base_url,search_supported:true,search_verified:true}]}
  return {}
}
