import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { connect, call } from '../../../refactor/learning/web/src/bridge'
import { useApp } from '../../../refactor/learning/web/src/store'
import { Rag } from '../../../refactor/learning/web/src/pages/Rag'
import '../../../refactor/learning/web/src/theme.css'

;(window as any).call269 = call
;(window as any).ready269 = connect().then(() => {
  useApp.setState({ route: 'rag' as any, settings: { theme: 'sakura', font_scale: 1, reduce_motion: true } as any,
    account: { name: '本机假账号', account_id: 'rag269-test-account', role_label: '学习者' }, ready: true })
  function App() {
    const route = useApp((state) => state.route)
    return route === 'rag'
      ? <main style={{ height: '100vh', padding: 8, boxSizing: 'border-box' }}><Rag /></main>
      : <div data-testid="rag269-away">离开资料问答</div>
  }
  createRoot(document.getElementById('root')!).render(<App />)
  ;(window as any).navigate269 = (route: string) => useApp.setState({ route: route as any })
})
