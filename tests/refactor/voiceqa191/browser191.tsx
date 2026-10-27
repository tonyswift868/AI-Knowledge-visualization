import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Classroom} from '../../../refactor/learning/web/src/pages/Classroom'
import {useApp} from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'
import {enter191,emit191,requests,lastVoiceSession191,voiceStatusTrace191} from './bridge191'

declare global { interface Window { result191?:any; debug191?:any; mount191:(mode:'recall'|'teaching')=>void; screenshotReady191?:boolean; resumeAfterScreenshot191?:boolean } }
const root=createRoot(document.getElementById('root')!)
function App(){const route=useApp(s=>s.route);return route==='classroom'?<Classroom/>:<div data-testid="home191">home</div>}
const checks:string[]=[]
const wait=(ms=20)=>new Promise(r=>setTimeout(r,ms))
const text=()=>document.body.textContent??''
async function until(test:()=>boolean,label:string){const end=Date.now()+10000;while(Date.now()<end){if(test())return;await wait()}throw Error('timeout: '+label+'; '+text())}
function button(label:string,rootEl:ParentNode=document){const b=[...rootEl.querySelectorAll<HTMLButtonElement>('button')].find(x=>x.getAttribute('aria-label')===label||x.textContent?.trim()===label);if(!b)throw Error('button missing '+label);return b}
function fill(el:HTMLTextAreaElement,value:string){Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}))}
async function mount(mode:'recall'|'teaching'){
  useApp.setState({route:'home'} as never)
  root.render(<main><App/></main>)
  await until(()=>!!document.querySelector('[data-testid="home191"]'),'unmount prior classroom')
  enter191(mode)
  const before=requests.filter(r=>r.method==='classroom.enter').length
  useApp.setState({planId:'a'.repeat(32),route:'classroom',account:null,
    settings:{theme:'sakura',reduce_motion:true} as never})
  root.render(<main data-mount191={mode} style={{height:'100vh',padding:12,boxSizing:'border-box'}}><App/></main>)
  await until(()=>!!document.querySelector('.class-scroll')&&requests.filter(r=>r.method==='classroom.enter').length>before,'classroom enter '+mode)
}
window.mount191=mode=>{void mount(mode)}

async function run(){
  await mount('teaching')
  const composer=document.querySelector<HTMLTextAreaElement>('[aria-label="课堂输入"]')!
  const bottomMic=document.querySelector('.composer .mf-voice') as HTMLElement
  if(!bottomMic)throw Error('existing bottom microphone missing')
  button('开始录音',bottomMic).click()
  await until(()=>bottomMic.matches('.mf-voice[data-voice-state="recording"]'),'bottom recording')
  button('结束录音',bottomMic).click()
  await until(()=>composer.value==='底部识别：我想问压强怎么比较？','bottom transcript')
  checks.push('原底部麦克风仍将识别文字写入课堂输入')

  await mount('recall')
  await until(()=>!!document.querySelector('[aria-label="先写下你的回答"]'),'recall question')
  const card=document.querySelector('[aria-label="问答题"]')!
  const answer=card.querySelector<HTMLTextAreaElement>('[aria-label="先写下你的回答"]')!
  const cardMic=card.querySelector('[data-testid="recall-answer-mic"]') as HTMLElement|null
  if(!cardMic)throw Error('answer microphone missing from recall card')
  if(answer.readOnly || !button('写好了，看参考答案',card))throw Error('initial recall answer must be editable before voice input')
  const bottomDuringRecall=document.querySelector('.composer .mf-voice') as HTMLElement|null
  if(!bottomDuringRecall)throw Error('bottom microphone disappeared during recall')
  button('我想问…').click()
  await until(()=>!document.querySelector<HTMLTextAreaElement>('[aria-label="课堂输入"]')?.disabled,'enable bottom input for ask')
  button('开始录音',bottomDuringRecall).click()
  await until(()=>bottomDuringRecall.matches('.mf-voice[data-voice-state="recording"]'),'bottom recording while recall visible')
  button('结束录音',bottomDuringRecall).click()
  const recallComposer=document.querySelector<HTMLTextAreaElement>('[aria-label="课堂输入"]')!
  await until(()=>recallComposer.value==='底部识别：我想问压强怎么比较？','bottom transcript during recall')
  if(answer.value!=='')throw Error('bottom microphone wrote into recall answer')
  button('取消').click()
  await until(()=>!document.querySelector('.class-question-tag'),'cancel ask mode')
  checks.push('问答卡显示时底部麦克风仍只写提问输入框')
  document.documentElement.dataset.theme='sakura'
  button('开始录音',cardMic).click()
  await until(()=>!!cardMic.querySelector('.mf-voice[data-voice-state="recording"]'),'answer recording')
  button('结束录音',cardMic).click()
  await until(()=>answer.value==='识别纠正：受力面积越小，压强越大。','corrected answer transcript')
  if(answer.value.includes('原始误识别结果'))throw Error('raw recognition was written instead of corrected transcript')
  if(recallComposer.value!=='底部识别：我想问压强怎么比较？')throw Error('answer microphone changed the bottom input')
  checks.push('问答作答框收到纠正后的语音识别文本')

  if(requests.some(r=>r.method==='class.recall_reveal'))throw Error('voice recognition must not save/reveal the recall answer')
  window.screenshotReady191=true
  await until(()=>window.resumeAfterScreenshot191===true,'capture editable corrected-answer screenshots')

  button('写好了，看参考答案',card).click()
  await until(()=>answer.readOnly&&!!card.querySelector('.recall-reference'),'saved answer readonly')
  const saved=answer.value
  emit191('voice.final',{session_id:lastVoiceSession191(),text:'迟到的识别结果',raw_text:'迟到原文',replacements:[]})
  await wait(120)
  if(answer.value!==saved)throw Error('late transcript changed saved answer')
  if(card.querySelector('[data-testid="recall-answer-mic"]'))throw Error('saved answer still accepts voice input')
  if(!answer.readOnly)throw Error('saved answer is editable')
  checks.push('保存后作答框只读、麦克风移除，迟到识别不会覆盖')

  window.result191={ok:true,checks}
}
run().catch(e=>window.result191={ok:false,checks,error:String(e.stack||e),dom:text(),requests,
  voiceStatusTrace:voiceStatusTrace191,
  voiceWidgets:[...document.querySelectorAll<HTMLElement>('.mf-voice')].map((widget,index)=>{
    const mic=widget.querySelector<HTMLButtonElement>('.mf-voice-mic'),dialog=widget.querySelector<HTMLDialogElement>('.mf-voice-dialog')
    return {index,state:widget.dataset.voiceState,micDisabled:mic?.disabled,micLabel:mic?.getAttribute('aria-label'),
      dialogOpen:dialog?.open,dialogText:dialog?.innerText,widgetText:widget.innerText}
  }),qtTransportAvailable:!!window.qt?.webChannelTransport})
