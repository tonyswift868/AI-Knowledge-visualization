import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Classroom} from '../../../refactor/learning/web/src/pages/Classroom'
import {useApp} from '../../../refactor/learning/web/src/store'
import {calls} from './bridge119'
import {focusSpeech} from '../../../refactor/learning/web/src/classroom/spokenFocus'
import '../../../refactor/learning/web/src/theme.css'
const root=createRoot(document.getElementById('root')!)
const pause=()=>new Promise(r=>setTimeout(r,20))
async function until(fn:()=>boolean){for(let i=0;i<400;i++){if(fn())return;await pause()}throw Error('UI timeout: '+calls.join(',')+' / '+document.body.textContent?.slice(-1200))}
function button(label:string){const e=[...document.querySelectorAll<HTMLButtonElement>('button')].find(e=>e.textContent?.trim()===label);if(!e||e.disabled)throw Error('Missing '+label);e.click()}
const toggle=()=>document.querySelector<HTMLButtonElement>('[aria-label="只看重点"]')!
let generation=0
async function mount(){const own=++generation;root.render(<div data-generation={own} style={{height:'100vh'}}><Classroom key={own}/></div>);await until(()=>document.querySelector('[data-generation]')?.getAttribute('data-generation')===String(own))}
useApp.setState({planId:'a'.repeat(32),settings:{reduce_motion:true} as any})
localStorage.removeItem('mf.classroom.focus');mount()
async function run(){
  const raw='开头。==面积越小，压强越大。==继续。'
  let spoken=''
  for(let i=1;i<=raw.length;i++){
    const next=focusSpeech(raw.slice(0,i))
    if(!next.startsWith(spoken))throw Error('Speech prefix changed')
    spoken=next
  }
  if(spoken!=='开头。面积越小，压强越大。继续。')throw Error('Highlight markers reach speech')
  const literal='`a==b` $a==b$ ==结论。=='
  if(focusSpeech(literal,true)!=='`a==b` $a==b$ 结论。')throw Error('Speech changed code/math')
  if(focusSpeech('x==y',true)!=='x==y')throw Error('Unpaired comparison changed')
  await until(()=>!!toggle())
  if(toggle().getAttribute('aria-checked')!=='false')throw Error('Default must be off')
  if(!document.querySelector('mark.hl'))throw Error('Real teacher highlights missing')
  toggle().click();await until(()=>!!document.querySelector('[data-focus="true"]'))
  const folded=[...document.querySelectorAll<HTMLElement>('.focus-body')].find(e=>!e.querySelector('mark.hl')&&!e.hasAttribute('data-focus-question'))!
  if(getComputedStyle(folded).display!=='none')throw Error('Non-highlight paragraph not folded')
  await mount();await until(()=>!!toggle()&&toggle().getAttribute('aria-checked')==='true'&&!![...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent==='没太懂'&&!b.disabled))
  button('没太懂');await until(()=>!!document.querySelector('.streaming .focus-reveal'))
  const reveal=document.querySelector<HTMLButtonElement>('.streaming .focus-reveal')!
  reveal.click();await until(()=>reveal.getAttribute('aria-expanded')==='true')
  ;(window as any).focus119Append();await pause()
  if(document.querySelector('.streaming .focus-reveal')?.getAttribute('aria-expanded')!=='true')throw Error('Streaming messageKey changed')
  ;(window as any).focus119Finish();await until(()=>!document.querySelector<HTMLButtonElement>('[aria-label="只看重点"]')?.disabled&&document.body.textContent?.includes('继续补充。')===true)
  await until(()=>!![...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent==='懂了，继续'&&!b.disabled))
  button('懂了，继续');await until(()=>!!document.querySelector('.recap-card'))
  if(document.querySelectorAll('.recap-row').length!==3)throw Error('Recap items missing')
  document.querySelector<HTMLButtonElement>('.recap-source')!.click();await until(()=>!!document.querySelector('.class-source'))
  if(!document.querySelector('.class-source-text')?.textContent?.includes('公式p=F/S'))throw Error('Original source missing')
  document.querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click();await pause()
  if(!calls.includes('classroom.source'))throw Error('Source not wired')
  toggle().click();await pause();await mount();await until(()=>!!toggle()&&toggle().getAttribute('aria-checked')==='false')
  ;(window as any).focus119Show=async(theme:string,focus:boolean)=>{
    document.documentElement.dataset.theme=theme
    if((toggle().getAttribute('aria-checked')==='true')!==focus)toggle().click()
    await pause();document.querySelector('.recap-card')?.scrollIntoView({block:'end'})
  }
  ;(window as any).conversationResult={ok:true,checks:['荧光','默认关/本机持久','非重点折叠','流式key稳定','recap/原文按钮','重开保持/关闭恢复全文']}
}
run().catch(e=>{(window as any).conversationResult={ok:false,error:String(e.stack||e)}})
