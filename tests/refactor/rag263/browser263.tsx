import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {connect, call} from '../../../refactor/learning/web/src/bridge'
import {useApp} from '../../../refactor/learning/web/src/store'
import {Rag} from '../../../refactor/learning/web/src/pages/Rag'
import '../../../refactor/learning/web/src/theme.css'
import '../../../refactor/learning/web/src/pages/chat-copy.css'

;(window as any).call263 = call
;(window as any).ready263 = connect().then(() => {
  useApp.setState({route: 'rag' as any, settings: {theme:'sakura',font_scale:1,reduce_motion:true} as any,
    account:{name:'本机假账号',account_id:'account-a',role_label:'学习者'},ready:true})
  const root=createRoot(document.getElementById('root')!)
  function App(){const route=useApp(s=>s.route);return route==='rag'?<main style={{height:'100vh',padding:16,boxSizing:'border-box'}}><Rag/></main>:<div data-testid="rag263-away">其他页面</div>}
  root.render(<App/>)
  ;(window as any).navigate263=(route:string)=>useApp.getState().go(route as any)
  ;(window as any).setAccount263=(accountId:string)=>useApp.setState({account:{name:'本机假账号',account_id:accountId,role_label:'学习者'}})
})
