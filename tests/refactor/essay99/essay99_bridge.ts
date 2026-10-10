export class BridgeFailure extends Error {}
export const settings={theme:'sakura',reduce_motion:true,version:1,font_scale:1}
export const calls:any[]=[]
const listeners=new Map<string,Set<(data:any)=>void>>()
export function on(name:string,fn:(data:any)=>void){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name)!.add(fn);return()=>{listeners.get(name)?.delete(fn)}}
const key_points=['接收用户输入','按程序处理输入','输出处理结果','保留必要状态','检查输入格式','识别处理顺序','区分输入与输出','解释处理原因','说明数据流向','给出具体例子']
const question={id:'q99',kind:'recall',stem:'结合例子说明计算机怎样处理信息。',options:[],purpose:'模块检测',number:1,total:2,
  source:{title:'离线测试原文',url:null,locator:'第一节'},reference:key_points.join('。')+'。',key_points}
let mode='module';let revealed=false;let ticked:number[]=[];let submitted=false
export function reset(value='module'){mode=value;revealed=false;ticked=[];submitted=false;listeners.get('classroom.materials_ready')?.forEach(f=>f({plan_id:'essay99'}))}
function room(){return {plan:{id:'essay99',goal:'信息处理（离线测试）',state:mode==='check'?'checking':'active'},
  module:{id:'m99',title:'理解输入、处理与输出',number:1,total:1,state:'passed'},points:[],step:mode==='check'?'checking':'testing',
  question:submitted?null:{...question,revealed,ticked,reference:revealed?question.reference:undefined,key_points:revealed?key_points:undefined,answer:revealed?'接收输入，按程序处理，再输出结果。':undefined},
  messages:[],can_skip:false,red:[],more_modules:false,check:mode==='check'?{passed:1,total:4}:null,today:null}}
const recall={...question,kind:'recall'}
const objective={stem:'下列哪项是输入设备？',kind:'objective',options:['键盘','显示器'],answer:'键盘',correct_options:[0]}
const dialogue=[{id:'d1',role:'assistant',content:'请回想刚才讲的数据处理流程。'},{id:'d2',role:'user',content:'我想到键盘输入文字。'},{id:'d3',role:'assistant',content:'我们用这道题回看具体过程。'}]
export const notebook=[{question_id:'q99',question:recall,wrong_count:2,attempts:[
  {id:'a1',at:'2026-10-10T04:10:00Z',context:'module_test',attempt_id:'test99',correct:false,self_correct:false,answer:'输入，然后输出。',ticked:[0,2],missed:[1,3,4,5,6,7,8,9],question:recall,dialogue},
  {id:'a2',at:'2026-10-10T04:20:00Z',context:'check',attempt_id:'check99',correct:false,self_correct:false,answer:'接收输入，按程序处理，再输出结果。',ticked:[0,1,2,3,4,5,6],missed:[7,8,9],question:recall,dialogue},
  {id:'a3',at:'2026-10-10T04:30:00Z',context:'check',attempt_id:'check99',correct:true,self_correct:true,answer:'现在理解完整了。',ticked:[0,1,2,3,4,5,6],missed:[7,8,9],question:recall,dialogue}]},
  {question_id:'o99',question:objective,wrong_count:1,attempts:[{id:'a4',at:'2026-10-10T04:12:00Z',context:'module_test',correct:false,answer:[1],ticked:[],missed:[],question:objective,dialogue}]}]
export async function call<T=any>(method:string,params:any={}):Promise<T>{
  calls.push({method,params})
  let result:any
  if(method==='app.bootstrap')result={ready:true,account:{name:'界面测试账号'},settings,options:{}}
  else if(method==='classroom.enter'||method==='classroom.state')result=room()
  else if(method==='class.recall_reveal'){revealed=true;result={reference:question.reference,key_points}}
  else if(method==='class.recall_draft'){ticked=params.ticked;result=room()}
  else if(method==='class.recall_submit'){submitted=true;result={correct:params.correct,missed:key_points.map((_,i)=>i).filter(i=>!params.ticked.includes(i))}}
  else if(method==='classroom.wrong_answers')result=notebook
  else if(method==='plan.detail')result={plan:{id:'essay99',title:'信息处理',goal:'理解计算机信息处理',state:mode==='completed'?'completed':'active',total_minutes:30,window:null},
    stages:[{id:'s99',title:'入门',modules:[{id:'m99',title:'理解输入、处理与输出',state:'passed',learning_minutes:26,check_minutes:4,question_count:2,passed_questions:2,points:[]}]}],sources:[],point_links:[],point_links_status:'ready',teacher_preset:{id:'default',name:'默认老师'}}
  else if(method==='settings.teacher_presets')result={presets:[{id:'default',name:'默认老师'}]}
  else if(method==='classroom.gaps')result=[]
  else if(method.startsWith('update.'))result={available:false,busy:false,state:'UNPACKAGED'}
  else if(method.startsWith('voice.')||method.startsWith('speech.'))result={ready:false,components:[]}
  else result={}
  return structuredClone(result) as T
}
