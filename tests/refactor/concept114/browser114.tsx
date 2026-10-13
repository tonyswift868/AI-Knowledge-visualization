import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Classroom } from '../../../refactor/learning/web/src/pages/Classroom'
import { useApp } from '../../../refactor/learning/web/src/store'
import { calls } from './bridge114'
import '../../../refactor/learning/web/src/theme.css'

const pause = () => new Promise(r => setTimeout(r, 30))
async function until(fn: () => boolean) { for (let i=0;i<400;i++) { if(fn()) return; await pause() } throw new Error('UI wait timed out') }
function click(label: string) {
  const b=[...document.querySelectorAll<HTMLButtonElement>('button')].find(x=>x.textContent?.trim()===label)
  if(!b || b.disabled) throw new Error('Button missing: '+label)
  b.click()
}
useApp.setState({planId:'a'.repeat(32),settings:{reduce_motion:true} as any})
createRoot(document.getElementById('root')!).render(<div style={{height:'100vh'}}><Classroom /></div>)
async function run() {
  await until(()=>!!document.querySelector('[aria-label="小要点讲义"]'))
  if(!document.body.textContent?.includes('小要点 1/3')) throw new Error('Missing subpoint progress')
  const nav=document.querySelector<HTMLDetailsElement>('[aria-label="资料导航"]')!
  if(nav.open) throw new Error('Navigation must start collapsed')
  nav.querySelector('summary')!.click(); await pause()
  if(!nav.textContent?.includes('压强读物') || !nav.textContent.includes('不计时长')) throw new Error('Navigation missing')
  const before=calls.length
  nav.querySelector<HTMLButtonElement>('.original-toggle')!.click(); await pause()
  nav.querySelector<HTMLButtonElement>('.original-card')!.click(); await pause()
  if(!nav.querySelector('.original-fulltext')?.textContent?.includes('书单')) throw new Error('Navigation source missing')
  if(calls.length!==before) throw new Error('Viewing navigation invoked learning')
  const notes=document.querySelector('[aria-label="小要点讲义"]')!
  notes.querySelector('summary')!.click(); await pause()
  click('看这条的原文'); await until(()=>!!notes.querySelector('.original-fulltext'))
  if(!notes.textContent?.includes('公式p=F/S')) throw new Error('Note source missing')
  click('没太懂'); await until(()=>calls.includes('classroom.confused'))
  await until(()=>!![...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent==='懂了，继续'&&!b.disabled))
  if(!document.body.textContent?.includes('小要点 1/3')) throw new Error('Confused advanced position')
  click('懂了，继续'); await until(()=>document.body.textContent?.includes('小要点 2/3')===true)
  if(!document.body.textContent?.includes('定义压强')) throw new Error('Next subpoint missing')
  ;(window as any).conversationResult={ok:true,checks:['实际课堂页小要点进度','讲义点击原文','导航折叠查看不触发学习','没懂不推进','懂了进下一小要点']}
}
run().catch(e=>{(window as any).conversationResult={ok:false,error:String(e.stack||e)}})
