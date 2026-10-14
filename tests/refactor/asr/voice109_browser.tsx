import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {useVoiceInput} from '@asrHook'
import {MicButton} from '@asrMic'
import '@asrTheme'

const events=new Map<string,Set<(v:any)=>void>>()
const log:{method:string;params:any}[]=[]
const transport:any={available:()=>true,on:(name:string,fn:any)=>{
  if(!events.has(name))events.set(name,new Set());events.get(name)!.add(fn)
  return()=>events.get(name)!.delete(fn)
},call:async(method:string,params:any)=>{
  log.push({method,params})
  if(method==='voice.status')return {state:'ready',installed:true,size_bytes:10}
  if(method==='voice.context')return {terms:['向量','Agent'],question:'向量那边还是 GraphRAG？',protected:['那个']}
  if(method==='voice.start'){events.get('voice.state')?.forEach(f=>f({session_id:params.session_id,state:'recording'}));return {}}
  if(method==='voice.stop')return {session_id:params.session_id,text:'向量那个。',raw_text:'下料来个。',replacements:[[0,4,'下料来个','向量那个']]}
  return {}
}}
function App(){
  const [text,setText]=React.useState('')
  const voice=useVoiceInput({value:text,onChange:setText,transport,context:{plan_id:'p',module_id:'m',point_id:'x'}})
  ;(window as any).asr109={text,voice}
  return <main style={{width:'min(580px,100%)',padding:12,boxSizing:'border-box'}}>
    <textarea aria-label="回答" style={{width:'100%',boxSizing:'border-box'}} value={text} onChange={e=>setText(e.target.value)}/>
    <MicButton voice={voice}/><button id="send" onClick={()=>transport.call('classroom.reply',{text})}>发送</button>
  </main>
}
createRoot(document.getElementById('root')!).render(<App/> )
const wait=async(test:()=>boolean)=>{const end=Date.now()+3000;while(!test()){if(Date.now()>end)throw Error('state timeout');await new Promise(r=>setTimeout(r,10))}}
const assert=(ok:any,msg:string)=>{if(!ok)throw Error(msg)}
async function run(){
 const checks:string[]=[]
 await wait(()=>!!(window as any).asr109)
 await (window as any).asr109.voice.start();await wait(()=>(window as any).asr109.voice.state==='recording')
 assert(log.some(x=>x.method==='voice.context'&&x.params.point_id==='x'),'current lesson context');checks.push('context')
 await (window as any).asr109.voice.stop();await wait(()=>(window as any).asr109.text==='向量那个。')
 assert(!log.some(x=>x.method==='classroom.reply'),'must not submit automatically');checks.push('no auto-send')
 // BUG-94 (user 2026-10-14): only the corrected text, in the input; no separate original-text line.
 assert(!document.body.textContent!.includes('原识别')&&!document.querySelector('[aria-label="换回原识别文字"]'),'no original line');checks.push('corrected only in input')
 const input=document.querySelector('textarea')!
 Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(input,'我确认：向量检索。')
 input.dispatchEvent(new Event('input',{bubbles:true}))
 await wait(()=>(window as any).asr109.text==='我确认：向量检索。')
 ;(document.querySelector('#send') as HTMLButtonElement).click()
 assert(log.at(-1)?.params.text==='我确认：向量检索。','send confirmed text');checks.push('confirmed edit sent')
 for(const theme of ['sakura','aurora']){document.documentElement.dataset.theme=theme;assert(document.querySelector('main')!.scrollWidth<=600,'no overflow')}
 checks.push('themes narrow')
 ;(window as any).result={ok:true,checks}
}
run().catch(e=>(window as any).result={ok:false,error:String(e)})
