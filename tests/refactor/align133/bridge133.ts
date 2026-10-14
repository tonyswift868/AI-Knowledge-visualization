import initial from './states133.json'
let room:any,mode='teacher',reply=''
export class BridgeFailure extends Error{code='TEST'}
export const on=()=>()=>undefined
const history=Array.from({length:12},(_,i)=>({id:`old133-${i}`,role:i%2?'user':'assistant',kind:'text',content:'==先区分压力和受力面积。==\n\n'+('之前的详细说明，先确认比较时压力相同，再考虑面积的变化。'.repeat(5)),sources:[]}))
export function scenario133(value:string){mode=value;reply='';room=structuredClone(initial);room.messages=structuredClone(history)}
export async function call(method:string,params:any={},options:any={}){
 if(method==='speech.status')return{ready:false,components:[],download:null}
 if(method.startsWith('speech.'))return{accepted:true}
 if(method==='classroom.prefetch'||method==='classroom.cancel_prefetch')return{total:0,ready:0}
 if(method==='classroom.next'||method==='classroom.reply'){
  await new Promise<void>(resolve=>{
   (window as any).chunk133=(text:string)=>{reply+=text;options.onStream?.(text)}
   ;(window as any).finish133=resolve
  })
  if(method==='classroom.reply')room.messages.push({id:'user133',role:'user',kind:'text',content:params.text,sources:[]})
  if(mode==='recap')room.messages.push({id:'recap133',role:'assistant',kind:'recap',content:'{}',sources:[],recap:{title:'压力与面积',items:[{title:'比较压力',text:'先看压力是否相同。'},{title:'比较面积',text:'相同压力，面积越小压强越大。'},{title:'使用公式',text:'用p=F/S比较压强。'}]}})
  room.messages.push({id:'teacher133',role:'assistant',kind:'step_reply',content:reply,sources:[]})
  delete (window as any).chunk133;delete (window as any).finish133
 }
 return structuredClone(room)
}
