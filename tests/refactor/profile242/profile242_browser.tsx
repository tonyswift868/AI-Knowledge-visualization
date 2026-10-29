import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { AccountMenu } from '../../../refactor/learning/web/src/App'
import { AccountProfilePanel } from '../../../refactor/learning/web/src/pages/Settings'
import { useApp } from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'

declare global { interface Window { getAccount242?: () => unknown; mountProfile242: (page: 'profile'|'card') => Promise<void>; profile242Ready?: boolean; switchProfile242Account?: () => void; prepareOtherProfile242?: () => void } }
const root = createRoot(document.getElementById('root')!)
let mountRevision = 0
window.getAccount242 = () => useApp.getState().account
const account = { name: 'mira', nickname: 'Tony', signature: '仅本人可见的测试签名', avatar_url: null, account_id: 'test-account', role_label: '管理员', settings_available: true, offline: false }
function Harness({page}:{page:'profile'|'card'}) {
  return <div className="shell" style={{display:'grid',gridTemplateColumns:'256px minmax(0,1fr)',height:'100vh',overflow:'hidden'}}>
    <aside className="sidebar" style={{width:256,minWidth:0,position:'relative',boxSizing:'border-box',display:'flex',flexDirection:'column',padding:'22px 16px 18px'}}><div style={{flex:1}}/><AccountMenu/></aside>
    <main className="main" style={{minWidth:0,minHeight:0,display:'flex',flexDirection:'column'}}>
      <header className="topbar"><span>{page==='card'?'账号卡片':'设置 · 账号与安全'}</span></header>
      <div className="content" style={{minWidth:0,overflowY:'auto',overflowX:'hidden'}}>
        {page==='profile'&&<div className="page" style={{maxWidth:1180,width:'100%'}}><h2>设置</h2><div className="settings-layout" style={{gridTemplateColumns:'190px minmax(0,1fr)',gap:22}}>
          <nav className="card subnav" aria-label="设置分类"><button>模型连接</button><button className="on">账号与安全</button></nav>
          <div className="fade-in"><AccountProfilePanel/></div></div></div>}
      </div>
    </main>
  </div>
}
window.mountProfile242 = async page => {
  const visibleAccount = page === 'card' ? (useApp.getState().account ?? account) : account
  useApp.setState({account:visibleAccount, route:'settings',settingsTab:'account',settings:{theme:'sakura',font_scale:1,reduce_motion:false} as never})
  window.profile242Ready = false
  root.render(<Harness key={`${page}-${++mountRevision}`} page={page}/> )
  if(page==='card') window.profile242Ready=true
}
window.switchProfile242Account = () => {
  useApp.setState({ account: { ...account, name: 'other-user', nickname: 'New account', signature: '', avatar_url: null, account_id: 'other-account' } })
}
window.prepareOtherProfile242 = () => window.setProfile242?.({ nickname: 'Other profile', signature: 'Other signature', version: 7, avatar_url: null, available: true, offline: false })
