import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Classroom} from '../../../refactor/learning/web/src/pages/Classroom'
import {Chat} from '../../../refactor/learning/web/src/pages/Chat'
import {NewPlan} from '../../../refactor/learning/web/src/pages/NewPlan'
import {useApp} from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'
import {show} from './round103_bridge'
const root=createRoot(document.getElementById('root')!)
const pause=(n=50)=>new Promise(r=>setTimeout(r,n))
const text=()=>document.body.textContent||''
function button(label:string){const e=[...document.querySelectorAll<HTMLButtonElement>('button')].find(e=>e.textContent?.trim()===label);if(!e)throw Error('missing '+label);e.click()}
async function until(fn:()=>boolean){for(let i=0;i<150;i++){if(fn())return;await pause()}throw Error('timeout '+text())}
function fill(value:string){const e=document.querySelector('textarea')!;Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(e,value);e.dispatchEvent(new Event('input',{bubbles:true}))}
let generation=0
async function page(mode:string){show(mode);useApp.setState({planId:'d'.repeat(32),settings:{reduce_motion:true,theme:'sakura'} as never});root.render(<main style={{height:'100%',padding:18}} key={++generation}>{mode==='chat'?<Chat/>:mode==='plan'?<NewPlan/>:<Classroom/>}</main>);await until(()=>!!document.querySelector('.class-scroll,.chat-scroll'));await pause(300)}
function aligned(e:Element){const scroll=document.querySelector('.class-scroll,.chat-scroll')!;const delta=e.getBoundingClientRect().top-scroll.getBoundingClientRect().top;if(Math.abs(delta-12)>3)throw Error('not aligned '+delta+' '+e.getAttribute('data-turn-key'))}
declare global {interface Window { run103:()=>Promise<any>; show103:(mode:string)=>Promise<void> }}
window.show103=async mode=>{await page(mode);if(mode==='direct'){button('这个模块我都会了，直接考');await pause();for(let i=0;i<3;i++){fill('我按自己的理解说一句。');await pause();button('发送');await pause(100);if(i<2){button('看完原文，下一句');await pause(100)}}}else{fill('这一轮学生的话');await pause();button('发送');await pause(900)}}
window.run103=async()=>{
 const checks:string[]=[]
 await page('direct');button('这个模块我都会了，直接考');await pause(150)
 for(let i=0;i<3;i++){
  fill('我按自己的理解说一句。');await pause();button('发送');await pause(150)
  const answers=document.querySelectorAll('[data-testid="direct-answer"]');if(answers.length!==i+1)throw Error('answers lost')
  const answer=answers[i];const scroll=document.querySelector('.class-scroll')! as HTMLElement
  const max=scroll.scrollHeight-scroll.clientHeight
  if(scroll.scrollTop<max-2)aligned(answer)
  if(answer.nextElementSibling!.getBoundingClientRect().top<answer.getBoundingClientRect().bottom)throw Error('source order')
  if(i<2){button('看完原文，下一句');await pause(150)}
 }
 checks.push('三句回答全部留存，回答在原文上方，短轮自然夹到底部')
 for(const mode of ['classroom','chat','plan']){
  await page(mode);fill('这一轮学生的话');await pause();button('发送');await pause(850)
  const users=[...document.querySelectorAll('[data-turn-role="user"]')];aligned(users.at(-1)!)
  const scroll=document.querySelector('.class-scroll,.chat-scroll')! as HTMLElement
  // An upward user scroll cancels growth alignment for the current turn.
  scroll.dispatchEvent(new WheelEvent('wheel',{deltaY:-200}));scroll.scrollTop=0;await pause(200)
  if(scroll.scrollTop!==0)throw Error('stole upward scroll')
  checks.push(mode+' 学生句贴顶，老师在下，向上翻不抢滚动')
 }
 await page('classroom');fill('流式过程中往上翻');await pause();button('发送');await pause(250)
 const streamingScroll=document.querySelector('.class-scroll')! as HTMLElement
 if(!document.querySelector('[data-turn-key="pending-student"]'))throw Error('student missing during streaming')
 streamingScroll.dispatchEvent(new WheelEvent('wheel',{deltaY:-200}));streamingScroll.scrollTop=0
 await pause(650);if(streamingScroll.scrollTop!==0)throw Error('stream stole scroll')
 checks.push('流式中先显示学生回答，用户向上翻后最终保存也不抢滚动')
 await page('chat');fill('短回复');await pause();button('发送');await pause(850)
 const scroll=document.querySelector('.chat-scroll')! as HTMLElement
 if(Math.abs(scroll.scrollTop-(scroll.scrollHeight-scroll.clientHeight))>3)throw Error('short not clamped')
 if([...scroll.children].some(e=>e.clientHeight>scroll.clientHeight&&e.textContent===''))throw Error('blank spacer')
 checks.push('短回复自然到底，无空白占位')
 await page('classroom');button('懂了，继续');await pause(850)
 aligned(document.querySelector('[data-turn-key^="a-"]')!)
 checks.push('没有学生气泡时，老师新内容开头贴顶')
 return {ok:true,checks}
}
