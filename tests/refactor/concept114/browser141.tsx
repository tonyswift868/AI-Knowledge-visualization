import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { MaterialToolbar } from '../../../refactor/learning/web/src/classroom/Conversation'
import '../../../refactor/learning/web/src/theme.css'

// Commit-time click deliberately runs before passive effects. A user/test action
// must not be undone by an initial "close on changed material" passive effect.
function EarliestClick({number, click}: {number: number; click: boolean}) {
  React.useLayoutEffect(() => {
    if (click) document.querySelector<HTMLButtonElement>('[data-materials="navigation"]')!.click()
  }, [number, click])
  return <MaterialToolbar entries={[{id:'nav141-'+number,title:'资料导航141-'+number,reason:'供查阅',sources:[]}]}/>
}
const root = createRoot(document.getElementById('root')!)
const until = async (stage: string, condition: () => boolean) => {
  for (let i=0;i<100;i++) { if (condition()) return; await new Promise(r=>setTimeout(r,20)) }
  throw new Error(stage+': '+document.body.innerHTML)
}
async function run() {
  root.render(<EarliestClick number={1} click />)
  await until('Initial navigation click was lost',()=>!!document.querySelector('dialog[open][aria-label="资料导航"]'))
  const clockBefore = new Date().toISOString()
  // The midnight scenario advances the browser clock without changing OS time.
  ;(window as any).advanceClock141()
  root.render(<EarliestClick number={2} click={false} />)
  await until('Changed materials must close old popup',()=>!document.querySelector('dialog'))
  document.querySelector<HTMLButtonElement>('[data-materials="navigation"]')!.click()
  await until('Changed navigation must reopen',()=>document.querySelector('dialog[open]')?.textContent?.includes('资料导航141-2')===true)
  root.render(<EarliestClick number={3} click={false} />)
  await until('Final material change must close popup',()=>!document.querySelector('dialog') && document.querySelector('[data-materials="navigation"]')?.getAttribute('aria-expanded')==='false')
  ;(window as any).conversationResult={ok:true,clockBefore,clockAfter:new Date().toISOString(),checks:[
    'initial click survives passive effect flush','changed materials close and reopen','material change still closes previous popup',
  ]}
}
run().catch(error=>{(window as any).conversationResult={ok:false,error:String(error)}})
