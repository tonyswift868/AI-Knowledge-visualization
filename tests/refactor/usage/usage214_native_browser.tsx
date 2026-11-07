import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { connect } from '../../../refactor/learning/web/src/bridge'
import { Usage } from '../../../refactor/learning/web/src/pages/Usage'

declare global { interface Window { usage214Result?: any } }
async function mount() {
  const connected = await connect()
  if (!connected) throw new Error('production bridge fell back to browser mock')
  const root = createRoot(document.getElementById('root')!)
  root.render(<div style={{ padding: 24 }}><Usage /></div>)
  window.usage214Result = { mounted: true }
}
mount().catch(error => { window.usage214Result = { mounted: false, error: String(error) } })
