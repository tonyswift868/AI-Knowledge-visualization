import { createRoot } from 'react-dom/client'
import { Chat } from '../../../refactor/learning/web/src/pages/Chat'
import { call, connect } from '../../../refactor/learning/web/src/bridge'
import { useApp } from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'

function Fixture() {
  const toasts = useApp(s => s.toasts)
  return <>
    <main style={{ height: '100vh', padding: 12 }}><Chat /></main>
    <div style={{ position: 'fixed', right: 8, bottom: 8 }}>
      {toasts.map(t => <p key={t.id} role={t.kind === 'error' ? 'alert' : 'status'}>{t.text}</p>)}
    </div>
  </>
}

;(window as any).ready175 = connect().then(() => {
  ;(window as any).chat175Preference = () => call('chat.ui_get', {})
  document.documentElement.dataset.theme = 'sakura'
  document.documentElement.classList.add('reduce-motion')
  useApp.setState({ account: { name: 'Synthetic account', role_label: '管理员', settings_available: true },
    settings: { theme: 'sakura', reduce_motion: true } as any })
  createRoot(document.getElementById('root')!).render(<Fixture />)
})
