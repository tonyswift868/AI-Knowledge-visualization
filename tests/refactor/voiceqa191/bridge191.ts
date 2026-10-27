import states from './states191.json'

export const requests: {method:string;params:any}[] = []
export const voiceStatusTrace191: {at:number; result:any}[] = []
const listeners = new Map<string, Set<(data:any)=>void>>()
let room:any = structuredClone((states as any).recall)
let voiceSession = ''
let voiceContext:any = null
let voiceStarts = 0

export class BridgeFailure extends Error { code:string; constructor(code:string,message:string){super(message);this.code=code} }
export function on(name:string, fn:(data:any)=>void) {
  if(!listeners.has(name)) listeners.set(name,new Set())
  listeners.get(name)!.add(fn)
  return ()=>listeners.get(name)?.delete(fn)
}
export function emit191(name:string,data:any) { for(const fn of listeners.get(name)??[]) fn(data) }
export function enter191(mode:'recall'|'teaching') { room=structuredClone((states as any)[mode]) }
export async function call(method:string,params:any={},_opts:any={}) {
  requests.push({method,params})
  if(method==='voice.status') {
    const result = {state:'ready',installed:true,size_bytes:32}
    voiceStatusTrace191.push({at:performance.now(),result})
    return result
  }
  if(method==='voice.context') { voiceContext=params; return {terms:['压强'],question:'课堂问答',protected:[]} }
  if(method==='voice.start') {
    voiceStarts++
    voiceSession=params.session_id
    emit191('voice.state',{session_id:voiceSession,state:'recording'})
    return {}
  }
  if(method==='voice.stop') return {session_id:params.session_id,
    text:voiceStarts<=2 ? '底部识别：我想问压强怎么比较？' : '识别纠正：受力面积越小，压强越大。',
    raw_text:'原始误识别结果',replacements:[[0,2,'原始','识别纠正']]}
  if(method==='voice.cancel') return {cancelled:true}
  if(method==='speech.status') return {ready:false,components:[],download:null}
  if(method.startsWith('speech.')) return {accepted:true,stopped:true}
  if(method==='classroom.enter'||method==='classroom.state') return structuredClone(room)
  if(method==='classroom.draft_get') return {text:''}
  if(method==='classroom.draft_set') return {saved:true}
  if(method==='class.recall_reveal') return {reference:'压力相同时，面积越小，压强越大。',key_points:['压力相同','面积越小，压强越大']}
  if(method==='class.recall_draft') return {}
  if(method==='classroom.prefetch'||method==='classroom.cancel_prefetch') return {ready:0,total:0}
  if(method==='classroom.leave') return {left:true}
  return structuredClone(room)
}

export const lastVoiceSession191=()=>voiceSession
