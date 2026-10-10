export class BridgeFailure extends Error {}
export const settings = {version:1, theme:'sakura', font_scale:1, reduce_motion:true}
const reference = '计算机接收输入的数据。计算机按程序处理数据。计算机输出处理结果。'
const key_points = ['计算机接收输入的数据。', '计算机按程序处理数据。', '计算机输出处理结果。']
const question = {id:'recall70', kind:'recall', stem:'计算机如何处理信息？', options:[], purpose:'模块检测', number:1, total:2,
  source:{title:'计算机入门（测试资料）',url:null,locator:'第 1 节'},origin:'source',reference:'不应提前展示的参考答案',key_points:['不应提前展示的要点']}
let room:any = {plan:{id:'recall70-plan',goal:'计算机入门'},module:{id:'m1',title:'认识计算机',number:1,total:1,state:'learning'},
  points:[],step:'testing',question,messages:[],can_skip:false,red:[],more_modules:false,check:null,today:null}
let submitted = false
export const calls: any[] = []
export const control = {failReveal:false,hold:false,pending:null as null | (()=>void)}
const listeners = new Map<string, Set<(data:any)=>void>>()
export function on(name:string,fn:(data:any)=>void) {
  if(!listeners.has(name))listeners.set(name,new Set())
  listeners.get(name)!.add(fn);return ()=>{listeners.get(name)?.delete(fn)}
}
export function reset(id='recall70') {
  submitted=false;room={...room,question:{...question,id}};listeners.get('classroom.materials_ready')?.forEach(fn=>fn({plan_id:'recall70-plan'}))
}
export async function call(method:string,params:any={}) {
  calls.push({method,params})
  if(method==='app.bootstrap')return {account:{name:'界面测试账号',role_label:'学习者'},settings,options:{},notice:null,ready:true}
  if(method.startsWith('update.'))return {available:false,busy:false,state:'UP_TO_DATE',version:'test',current:'test'}
  if(method==='sync.status')return {state:'synced',at:null,message:''}
  if(method.startsWith('speech.')||method.startsWith('voice.'))return {ready:false,components:[],download:null}
  if(method==='classroom.enter'||method==='classroom.state') {
    if(submitted)room={...room,question:{...question,id:'objective70',kind:'objective',question_type:'choice',stem:'下列哪项是输入设备？',options:['键盘','显示器']}}
    return room
  }
  if(method==='classroom.leave')return {}
  if(method==='class.recall_reveal') {
    if(control.hold)await new Promise<void>(resolve=>{control.pending=resolve})
    if(control.failReveal)throw new Error('测试：保存失败，请重试')
    return {reference,key_points}
  }
  if(method==='class.recall_draft')return {}
  if(method==='class.recall_submit') {
    submitted=true
    const missed=key_points.map((_,i)=>i).filter(i=>!params.ticked.includes(i))
    return {correct:params.correct,missed}
  }
  if(method==='classroom.answer')return {...room,messages:[{id:'verdict70',role:'assistant',kind:'verdict',content:'✓ 答对了',sources:[]}],question:null}
  throw new Error('Unexpected offline bridge call: '+method)
}
