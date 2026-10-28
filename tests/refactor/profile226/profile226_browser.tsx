import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { AccountMenu } from '../../../refactor/learning/web/src/App'
import { AccountProfilePanel } from '../../../refactor/learning/web/src/pages/Settings'
import { useApp } from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'

declare global { interface Window { mountProfile226: (page: 'profile'|'card') => Promise<void>; profile226Ready?: boolean; switchProfile226Account?: () => void; prepareOtherProfile226?: () => void } }
const root = createRoot(document.getElementById('root')!)
const account = { name: 'tonyswift', nickname: 'Tony', signature: '仅本人可见的测试签名', avatar_url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="64" height="64"%3E%3Crect width="64" height="64" fill="%23d65a86"/%3E%3C/svg%3E', account_id: 'test-account', role_label: '管理员', settings_available: true, offline: false }
function Harness({page}:{page:'profile'|'card'}) {
  if (page === 'card') return <aside className="sidebar" style={{height:'100vh',width:280,position:'relative',padding:18,boxSizing:'border-box'}}><AccountMenu/></aside>
  return <main className="page" style={{maxWidth:740,margin:'24px auto'}}><AccountProfilePanel/></main>
}
window.mountProfile226 = async page => {
  const visibleAccount = page === 'card' ? (useApp.getState().account ?? account) : account
  useApp.setState({account:visibleAccount, route:'settings',settingsTab:'account',settings:{theme:'sakura',font_scale:1,reduce_motion:true} as never})
  window.profile226Ready = false
  root.render(<Harness page={page}/> )
  if(page==='card') window.profile226Ready=true
}
window.switchProfile226Account = () => {
  useApp.setState({ account: { ...account, name: 'other-user', nickname: 'New account', signature: '', avatar_url: null, account_id: 'other-account' } })
}
window.prepareOtherProfile226 = () => window.setProfile226?.({ nickname: 'Other profile', signature: 'Other signature', version: 7, avatar_url: null, available: true, offline: false })
