import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Classroom} from '../../../refactor/learning/web/src/pages/Classroom'
import {useApp} from '../../../refactor/learning/web/src/store'
import {calls} from './bridge116'
import '../../../refactor/learning/web/src/theme.css'
const pause=()=>new Promise(r=>setTimeout(r,20))
async function until(fn:()=>boolean){for(let i=0;i<400;i++){if(fn())return;await pause()}throw Error('UI timed out')}
function click(text:string){const el=[...document.querySelectorAll<HTMLButtonElement>('button')].find(x=>x.textContent?.trim()===text);if(!el||el.disabled)throw Error('Missing '+text);el.click()}
useApp.setState({planId:'a'.repeat(32),settings:{reduce_motion:true} as any})
localStorage.setItem('mf.classroom.read','on')
createRoot(document.getElementById('root')!).render(<div style={{height:'100vh'}}><Classroom/></div>)
async function run(){
  await until(()=>calls.some(c=>c.method==='classroom.prefetch'))
  click('没太懂');await until(()=>document.body.textContent?.includes('边生成边显示')===true)
  if(calls.some(c=>c.method==='speech.stream_end'))throw Error('Must stream before completion')
  ;(window as any).finish116();await until(()=>calls.some(c=>c.method==='speech.stream_end'))
  if(calls.filter(c=>c.method==='speech.stream_start').length!==1)throw Error('Parsed final repeated the voice stream')
  await until(()=>!document.querySelector<HTMLButtonElement>('[aria-label="切换为跳过该模块"]')?.disabled)
  const toggle=()=>document.querySelector<HTMLButtonElement>('[aria-label="切换为跳过该模块"]')!
  toggle().click();await pause();click('跳过该模块');await until(()=>!!document.querySelector('[aria-label="确认跳过该模块"]'))
  click('取消');await pause();if(calls.some(c=>c.method==='classroom.test'))throw Error('Cancel invoked test')
  click('跳过该模块');await pause();click('确认，进入直接考');await until(()=>calls.some(c=>c.method==='classroom.test'&&c.params.kind==='skip_test'))
  await until(()=>!toggle().disabled)
  await until(()=>toggle().getAttribute('aria-checked')==='false');click('跳过该知识点');await until(()=>calls.some(c=>c.method==='classroom.skip_point'))
  await until(()=>toggle().getAttribute('aria-checked')==='false')
  if(document.querySelector('[aria-label="确认跳过该模块"]'))throw Error('Point skip opened confirmation')
  ;(window as any).conversationResult={ok:true,checks:['预备入口','流式先于完成可见','定稿替换','模块取消不跳/确认直接考','知识点无确认跳过','跨点开关复位']}
}
run().catch(e=>{(window as any).conversationResult={ok:false,error:String(e.stack||e)}})
