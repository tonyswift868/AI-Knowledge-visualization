import states from './states119.json'
let room:any=states.first
export const calls:string[]=[]
export class BridgeFailure extends Error{constructor(public code:string,message:string){super(message)}}
export const on=()=>()=>undefined
export async function call(method:string,params:any={},options:any={}){
  calls.push(method)
  if(method==='speech.status')return {ready:false,components:[],download:null}
  if(method==='classroom.prefetch'||method==='classroom.cancel_prefetch')return {total:0,ready:0}
  if(method==='classroom.source')return states.source
  if(method==='classroom.next')room=states.finished
  if(method==='classroom.confused'){
    let content='==压力相同时要比较受力面积。==\n\n补充说明正在生成。'
    options.onStream?.(content)
    await new Promise<void>(resolve=>{
      ;(window as any).focus119Append=()=>{content+='继续补充。';options.onStream?.('继续补充。')}
      ;(window as any).focus119Finish=resolve
    })
    room={...room,messages:[...room.messages,{id:'stream119',role:'assistant',kind:'teach',content,sources:[]}]}
  }
  return structuredClone(room)
}
