import states from './states119.json'
let room:any=structuredClone(states.first)
export const calls:{method:string;params:any}[]=[]
export class BridgeFailure extends Error { constructor(public code:string,message:string){super(message)} }
export const on=()=>()=>undefined
export function scenario127(noPoint=false){room=structuredClone(states.first);if(noPoint)room.points=room.points.map((p:any)=>({...p,current:false}));room.can_skip=true}
export async function call(method:string,params:any={},options:any={}){
 calls.push({method,params})
 if(method==='speech.status')return {ready:false,components:[],download:null}
 if(method.startsWith('speech.'))return {accepted:true,stopped:true}
 if(method==='classroom.prefetch'||method==='classroom.cancel_prefetch')return {total:0,ready:0}
 if(method==='classroom.confused')await new Promise<void>(resolve=>{(window as any).finishSkip127=resolve})
 // Deliberately preserve the same current point: a successful skip action
 // must reset scope even when the bridge snapshot has not changed location.
 return structuredClone(room)
}
