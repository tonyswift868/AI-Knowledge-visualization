export { BridgeFailure } from '../../../refactor/learning/web/src/bridge'
const listeners = new Map<string, Set<(data: any)=>void>>()
export const requests: string[] = []
export const control = {minutes:'running', links:'running', preparation:'running', total:60}
export const emit = (name:string, status:string) => listeners.get(name)?.forEach(fn=>fn({plan_id:'fixture',status}))
export function on(name:string, fn:(data:any)=>void) {
  if(!listeners.has(name))listeners.set(name,new Set())
  listeners.get(name)!.add(fn)
  return ()=>listeners.get(name)?.delete(fn)
}
export async function call<T=any>(method:string):Promise<T> {
  requests.push(method)
  if(method==='plan.retry_point_minutes'){control.minutes='running';return {status:'running'} as T}
  if(method==='plan.retry_point_links'){control.links='running';return {status:'running'} as T}
  if(method==='classroom.gaps')return [] as T
  if(method==='plan.detail')return {
    plan:{id:'fixture',title:'计算机入门',goal:'认识处理器与内存',state:'ready',total_minutes:control.total},
    teacher_preset:{id:'a',name:'温柔老师'}, sources:[],
    point_minutes_status:control.minutes,point_links_status:control.links,point_links:[],
    preparation_status:control.preparation,preparation:{done:0,failed:0,total:1},
    stages:[{id:'s',title:'基础',modules:[{id:'m',title:'计算机基础',summary:'计算机基础概念',learning_minutes:60,check_minutes:0,question_count:0,points:[
      {id:'p1',title:'处理器',summary:'执行运算和控制',minutes:30},{id:'p2',title:'内存',summary:'存放正在使用的数据',minutes:30}]}]}]
  } as T
  return {} as T
}
