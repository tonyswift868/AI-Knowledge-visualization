export class BridgeFailure extends Error {}
export const settings={version:1,theme:'sakura',font_scale:1,reduce_motion:false}
export const log:any[]=[]
const listeners=new Map<string,Set<(data:any)=>void>>()
let teacherPreference:any=null
export function on(name:string,fn:(data:any)=>void) { if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name)!.add(fn);return()=>{listeners.get(name)?.delete(fn)} }
export function emit(name:string,data:any) { listeners.get(name)?.forEach(fn=>fn(data)) }
const room={plan:{id:'teacher78',goal:'用生活化例子理解计算机'},module:{id:'m1',title:'信息如何流动',number:1,total:3,state:'learning'},
  points:[{id:'p1',title:'输入、处理与输出',minutes:10,taught:false,state:'new',current:true}],step:'teaching',question:null,
  messages:[{id:'m1',role:'assistant',kind:'text',content:'你可以把计算机想成一家餐馆：输入是客人下单，处理是厨房做菜，输出是端上成品。\n\n想一想，键盘和显示器各在做什么？',sources:[]}],
  can_skip:false,red:[],more_modules:true,check:null,today:{today_target:30,today_confirmed:10,today_tentative:0}}
export async function call(method:string,params:any={}) {
  log.push({method,params})
  if(method==='app.window')return (window as any).teacher78Native(params)
  if(method==='app.bootstrap')return {account:{name:'离线界面测试',role_label:'学习者'},settings,options:{},notice:null,ready:true}
  if(method.startsWith('update.'))return {available:false,busy:false,state:'UP_TO_DATE',version:'test',current:'test'}
  if(method==='sync.status')return {state:'synced',at:null,message:''}
  if(method==='teacher.assets')return (window as any).teacher78Assets
  if(method==='speech.status')return {ready:true,message:'朗读已就绪',components:[],download:null}
  if(method==='speech.speak') { queueMicrotask(()=>emit('speech.state',{...params,state:'playing'}));return {} }
  if(method.startsWith('speech.')||method.startsWith('voice.'))return {ready:false,components:[],download:null}
  if(method==='classroom.enter'||method==='classroom.state')return room
  if(method==='classroom.leave')return {}
  throw new Error('Unexpected offline bridge call: '+method)
}
