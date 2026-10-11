import states from './states.json'
let room:any=states.entry
let index=0
const old=Array.from({length:16},(_,i)=>({id:`old-${i}`,seq:i,role:i%2?'user':'assistant',kind:'step',content:'之前的对话。'.repeat(20),text:'之前的对话。'.repeat(20),sources:[]}))
export let mode='classroom'
let session:any={id:'chat103',title:'闲聊',updated_at:new Date().toISOString(),count:old.length,messages:structuredClone(old)}
let draft:any={plan_id:'draft103',phase:'questions',name:'测试计划',messages:structuredClone(old).map(m=>({...m,role:m.role==='assistant'?'ai':m.role,kind:'text'})),sources:[],busy:null}
export const on=()=>()=>{}
export class BridgeFailure extends Error {code='TEST'}
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms))
export function show(value:string){mode=value;room=structuredClone(states.entry);room.messages=structuredClone(old);index=0}
export async function call(method:string,params:any={},options:any={}) {
 if(method==='speech.status')return {ready:false,components:[]}
 if(method==='classroom.leave')return {accepted:true}
 if(method==='classroom.test'){room=structuredClone(states.prompts[0]);room.messages=structuredClone(old)}
 if(method==='classroom.direct_next'){room=structuredClone(states.prompts[++index]);room.messages=structuredClone(old)}
 if(method==='classroom.reply'&&room.direct_review){room=structuredClone(states.answers[index]);room.messages=structuredClone(old)}
 else if(method==='classroom.reply'||method==='classroom.next'){
  const n=room.messages.length
  if(method==='classroom.reply')room.messages.push({id:`u-${n}`,role:'user',kind:'dialogue',content:params.text,sources:[]})
  for(let i=0;i<6;i++){await sleep(90);options.onStream?.('老师回复内容。'.repeat(45))}
  room.messages.push({id:`a-${n}`,role:'assistant',kind:'step_reply',content:'老师回复内容。'.repeat(270),sources:[]})
 }
 if(method.startsWith('classroom.'))return structuredClone(room)
 if(method==='chat.list')return {sessions:[session]}
 if(method==='chat.get'||method==='chat.create')return {session:structuredClone(session)}
 if(method==='chat.send'){
  const n=session.messages.length
  session.messages.push({role:'user',content:params.text,seq:n})
  const short=params.text==='短回复'
  for(let i=0;i<6;i++){await sleep(90);options.onStream?.(short?'好':'闲聊回复。'.repeat(45))}
  session.messages.push({role:'assistant',content:short?'好': '闲聊回复。'.repeat(270),seq:n+1})
  return {session:structuredClone(session)}
 }
 if(method==='plan.current'||method==='plan.get')return {draft:structuredClone(draft)}
 if(method==='plan.say'){
  draft.messages.push({id:`u-${draft.messages.length}`,role:'user',kind:'text',text:params.text})
  await sleep(150)
  draft.messages.push({id:`a-${draft.messages.length}`,role:'ai',kind:'text',text:'建计划回复。'.repeat(270)})
  return {draft:structuredClone(draft)}
 }
 return {}
}
