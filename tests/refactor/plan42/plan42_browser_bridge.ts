export class BridgeFailure extends Error {}
export const fixture = {
  calls: [] as any[],
  draft: {plan_id:'plan42',phase:'questions',form:null,goal:'理解计算机术语',name:'计算机术语',sources:[],outline:null,
    generation:{},busy:null,messages:[{id:'q1',role:'ai',kind:'questions',text:'更希望用于交流还是工作？',options:['交流时能解释','工作中能听懂']}] as any[], preview:undefined as any}
}
export function on(){return ()=>{}}
export async function call(method:string,params:any={}) {
  fixture.calls.push({method,params})
  if(method==='plan.get')return {draft:{...fixture.draft}}
  if(method==='plan.say') {
    const d=fixture.draft
    d.messages.push({id:'user'+fixture.calls.length,role:'user',kind:'user_text',text:params.text})
    if(params.text==='确认并找资料') {d.phase='sources'}
    else {
      const version=(d.preview?.version||0)+1
      d.phase='preview';d.preview={summary:'只理解术语，不做操作',modules:[{title:version===1?'硬件':'网络',summary:'用生活化例子理解'}],version}
      d.messages.push({id:'preview'+version,role:'ai',kind:'preview',text:`需求摘要\n${version===1?'硬件':'网络'}｜用生活化例子理解\n这样可以吗？`,version})
    }
    return {draft:{...d,messages:[...d.messages]}}
  }
  if(method==='plan.detail')return {plan:{id:'plan42',title:'计算机术语',goal:'理解术语',state:'ready',total_minutes:30,window:null},
    sources:[],stages:[{id:'s1',title:'基础',modules:[{id:'m1',title:'网络',summary:'理解概念',learning_minutes:15,check_minutes:2,state:'passed',question_count:3,passed_questions:2,cloze_heavy:true,points:[]},
    {id:'m2',title:'硬件',summary:'理解概念',learning_minutes:15,check_minutes:2,question_count:0,passed_questions:0,cloze_heavy:false,points:[]}]}]}
  return {}
}
