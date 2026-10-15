import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Chat} from '../../../refactor/learning/web/src/pages/Chat'
import {connect} from '../../../refactor/learning/web/src/bridge'
import {useApp} from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'

function Fixture() {
  const toasts=useApp(s=>s.toasts)
  return <><main style={{height:'100vh',padding:12}}><Chat/></main>
    <div role="status" style={{position:'fixed',bottom:8,left:12}}>{toasts.map(t=><p key={t.id} role={t.kind==='error'?'alert':undefined}>{t.text}</p>)}</div></>
}
;(window as any).ready140=connect().then(()=>{
  document.documentElement.classList.add('reduce-motion')
  ;(window as any).clearToasts140=()=>useApp.setState({toasts:[]})
  useApp.setState({settings:{theme:'sakura',reduce_motion:true} as any})
  createRoot(document.getElementById('root')!).render(<Fixture/>)
})
