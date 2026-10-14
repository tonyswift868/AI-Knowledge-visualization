import states from './states130.json'
let room:any=structuredClone(states.first),mode='normal'
export const calls:{method:string;params:any}[]=[]
export class BridgeFailure extends Error { constructor(public code:string,message:string){super(message)} }
export const on=()=>()=>undefined
export function scenario130(value:string){mode=value;room=structuredClone(states.first)}
export async function call(method:string,params:any={},options:any={}){
 calls.push({method,params})
 if(method==='speech.status')return {ready:false,components:[],download:null}
 if(method.startsWith('speech.'))return {accepted:true,stopped:true}
 if(method==='classroom.prefetch'||method==='classroom.cancel_prefetch')return {total:0,ready:0}
 if(method==='classroom.next'){
  if(mode==='inflight'){
   options.onStream?.(states.finished.messages.at(-1)!.content)
   await new Promise<void>(resolve=>{(window as any).finish130=resolve})
  }
  room=structuredClone(states.finished)
  if(mode==='user'){
   const index=room.messages.findIndex((m:any)=>m.kind==='recap')
   room.messages.splice(index,0,{id:'user-continue130',role:'user',kind:'text',content:'懂了，继续',sources:[]})
  }
 }
 return structuredClone(room)
}
