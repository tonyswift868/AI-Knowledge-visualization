import initial from './states129.json'
let room:any=structuredClone(initial)
export const calls:{method:string;params:any}[]=[]
export class BridgeFailure extends Error { constructor(public code:string,message:string){super(message)} }
const listeners=new Set<(data:any)=>void>()
export const on=(name:string,callback:any)=>{if(name==='classroom.materials_ready')listeners.add(callback);return()=>{listeners.delete(callback)}}
export function scenario129(mode:string){
 room=structuredClone(initial)
 if(mode==='legacy'){room.concept=null;room.module.navigation=[]}
 if(mode==='failure'){room.preparation.error='离线准备失败';room.failure={message:'老师暂时无法连接，请重试'}}
 if(mode==='wait'){room.step='preparing_materials';room.can_skip=false}
}
export async function call(method:string,params:any={}){
 calls.push({method,params})
 if(method==='speech.status')return {ready:false,components:[],download:null}
 if(method.startsWith('speech.'))return {accepted:true,stopped:true}
 if(method==='classroom.prefetch'||method==='classroom.cancel_prefetch')return {total:0,ready:0}
 if(method==='classroom.cancel_preparation')room.step='teaching'
 if(method==='classroom.retry'){room.preparation.error=null;room.failure=null}
 return structuredClone(room)
}
