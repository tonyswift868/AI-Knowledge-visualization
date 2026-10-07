import data from './map82_data.json'
import * as native from '../../../refactor/learning/web/src/bridge'
const connected = (window as any).map82Native ? native.connect() : null
const listeners = new Map<string,Set<(data:any)=>void>>()
export const requests: {method:string;params:any}[]=[]
export const control:any={data, status:'ready', links:[]}
const modules=data.stages.flatMap(s=>s.modules)
const first=modules[0].points
// Explicit contract fixture, separate from native detail measurement.
control.links=[{from:first[0].id,to:first[1].id,kind:'prerequisite'},{from:first[1].id,to:first[2].id,kind:'related'},
 {from:first[1].id,to:modules[1].points[0].id,kind:'prerequisite'}]
const largest=modules.reduce((a,b)=>a.points.length>b.points.length?a:b)
control.links.push(...largest.points.slice(1).map((p,i)=>({from:largest.points[i].id,to:p.id,kind:i%4?'prerequisite':'related'})))
export const emit=(name:string,event:any)=>listeners.get(name)?.forEach(fn=>fn(event))
export function on(name:string,fn:(d:any)=>void){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name)!.add(fn);return()=>listeners.get(name)?.delete(fn)}
export async function call<T=any>(method:string,params:any={}):Promise<T>{
 requests.push({method,params})
 if(method==='plan.detail') {
  if(connected) {await connected;return native.call(method,params)}
  const result=structuredClone(control.data)
  result.point_links_status=control.status;result.point_links=control.status==='running'||control.status==='none'?[]:control.links
  result.stages[0].modules[0].points[0].citations=[{chunk_id:'md',title:'Markdown测试原文',locator:'第一段',url:null,
    quote:'# 原文标题\n\n| 类型 | 用途 |\n| --- | --- |\n| CPU | 计算 |\n\n```python\nprint("hello")\n```'}]
  result.stages[0].modules[0].points[0].taught=true
  result.stages[0].modules[1].prerequisites=[result.stages[0].modules[0].id]
  return {...result,teacher_preset:{id:'a',name:'温柔老师'}}
 }
 if(method==='plan.retry_point_links'){control.status='running';return {status:'running'} as T}
 if(method==='app.bootstrap')return {ready:true,account:{name:'隔离测试'},settings:{theme:'sakura',reduce_motion:!connected},options:{}} as T
 if(method==='plans.list')return {plans:[{...data.plan,archived:false}]} as T
 if(method==='classroom.gaps')return [] as T
 if(method==='settings.teacher_presets')return {presets:[{id:'a',name:'温柔老师'},{id:'b',name:'严谨老师'}]} as T
 if(method.startsWith('update.'))return {available:false,busy:false,state:'UNPACKAGED'} as T
 if(method==='teacher.assets'||method==='voice.status')return {available:false} as T
 return {} as T
}
