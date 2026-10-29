import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { connect, call } from '../../../refactor/learning/web/src/bridge'
import { useApp } from '../../../refactor/learning/web/src/store'
import { Rag } from '../../../refactor/learning/web/src/pages/Rag'
import '../../../refactor/learning/web/src/theme.css'

;(window as any).call249 = call
;(window as any).ready249 = connect().then(() => {
  useApp.setState({ route: 'rag' as any, settings: { theme: 'sakura', font_scale: 1, reduce_motion: true } as any,
    account: { name: '本地测试', role_label: '学习者' }, ready: true })
  createRoot(document.getElementById('root')!).render(<main style={{height:'100vh',padding:16,boxSizing:'border-box'}}><Rag /></main>)
})
