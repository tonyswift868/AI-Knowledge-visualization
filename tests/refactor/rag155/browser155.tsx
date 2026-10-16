import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { connect } from '../../../refactor/learning/web/src/bridge'
import { useApp } from '../../../refactor/learning/web/src/store'
import { App } from '../../../refactor/learning/web/src/App'
import '../../../refactor/learning/web/src/theme.css'

;(window as any).ready155 = connect().then(() => {
  document.documentElement.classList.add('reduce-motion')
  useApp.setState({ route: 'rag' as any, settings: { theme: 'sakura', reduce_motion: true } as any,
    account: { name: '浏览器测试', role_label: '学习者' }, ready: true })
  createRoot(document.getElementById('root')!).render(<App />)
})
