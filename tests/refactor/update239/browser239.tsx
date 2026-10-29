import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {UpdateBanner,UpdateNotice,checkUpdate} from '../../../refactor/learning/web/src/updates/UpdateNotice'
import {useApp} from '../../../refactor/learning/web/src/store'
import {fixture239} from './bridge239'
import '../../../refactor/learning/web/src/theme.css'

useApp.setState({ready:true,route:'home',settings:{theme:'sakura',reduce_motion:true} as any})
;(window as any).fixture239=fixture239
;(window as any).classroom239=()=>useApp.setState({route:'classroom'})
;(window as any).manualCheck239=async()=>{useApp.setState({route:'settings'});await checkUpdate(true)}
function Fixture239(){
  const route=useApp(s=>s.route)
  return <><div className="shell" data-route239={route}><main className="main"><header className="topbar">MentorFlow</header><UpdateBanner/><div className="content">当前页面可继续使用</div></main></div><UpdateNotice/><div id="ready239">ready</div></>
}
createRoot(document.getElementById('root')!).render(<Fixture239/>)
