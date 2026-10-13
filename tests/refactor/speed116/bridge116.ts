import states from './states116.json'
let room:any=states.first
export const calls:{method:string;params:any}[]=[]
export class BridgeFailure extends Error {constructor(public code:string,message:string){super(message)}}
export const on=()=>()=>undefined
export async function call(method:string,params:any={},opts:any={}) {
  calls.push({method,params})
  if(method==='speech.status')return {ready:true,components:[],download:null}
  if(method.startsWith('speech.'))return {accepted:true,stopped:true}
  if(method==='classroom.prefetch'||method==='classroom.cancel_prefetch')return {total:2,ready:2}
  if(method==='classroom.confused'){
    opts.onStream?.('边生成边显示')
    await new Promise<void>(resolve=>{(window as any).finish116=resolve})
    opts.onStream?.('。');opts.onStream?.('\u0000');opts.onStream?.('边生成边显示。')
    room={...room,messages:[...room.messages,{id:'new116',role:'assistant',content:'边生成边显示。',sources:[]}],teaching_version:'next116'}
  }
  if(method==='classroom.skip_point')room={...room,concept:null,points:[{...room.points[0],current:false},{...room.points[0],id:'next-point',title:'下一大点',current:true}],teaching_version:'skip116'}
  return structuredClone(room)
}
