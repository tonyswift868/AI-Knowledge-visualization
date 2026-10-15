import * as React from 'react'
import { Sparkles } from 'lucide-react'
import { Skeleton } from '../ui'
import { getActivities, showActivity, subscribeActivities, waitLabel } from './activity'
import './loading.css'

export type LoadingProps = {active?: boolean; label: string; shape?: 'page'|'cards'|'conversation'|'form'|'inline';
  started?: number; resetKey?: string|number; onRetry?: () => void; onCancel?: () => void; detail?: string; slowHint?: boolean; slowMessage?: string}
export const MODEL_SLOW_MESSAGE = '比平时慢，正在等模型回复…'
/** One clock per wait, not per progress update. Reserve space before 300ms to avoid layout jumps. */
export function Loading({active = true, label, shape = 'inline', started, resetKey, onRetry, onCancel, detail, slowHint = true, slowMessage = '比平时慢，请稍等…'}: LoadingProps) {
  const [age, setAge] = React.useState(0)
  React.useEffect(() => {
    if (!active) { setAge(0); return }
    const begin = started ?? Date.now()
    const update = () => setAge(Math.max(0, Date.now() - begin))
    update()
    const timers = [300, 3000, 15000].map(ms => window.setTimeout(update, Math.max(0, begin + ms - Date.now())))
    return () => timers.forEach(window.clearTimeout)
  }, [active, started, resetKey])
  if (!active) return null
  const tier = age < 300 ? 'quiet' : age < 3000 ? 'medium' : 'long'
  const slow = age >= 15000
  return <div className={`wait wait-${shape} wait-${tier}`} data-wait-tier={tier} data-wait-slow={slow || undefined}
    aria-hidden={tier === 'quiet' || undefined} role={tier === 'quiet' ? undefined : 'status'} aria-live="polite" aria-busy="true">
    {shape !== 'inline' && <div className="wait-skeleton" aria-hidden="true">
      {shape === 'cards' ? [0,1,2].map(i => <div className="card stack" key={i}><Skeleton h={22} w="60%"/><Skeleton h={70}/><Skeleton w="45%"/></div>)
        : <><Skeleton h={24} w="52%"/><Skeleton h={shape === 'conversation' ? 32 : 96}/><Skeleton w="80%"/><Skeleton w="60%"/></>}
    </div>}
    <div className="wait-message">
      <div className="wait-art" aria-hidden="true"><Sparkles/>{[0,1,2,3,4].map(i => <i key={i} style={{'--wait-i':i} as React.CSSProperties}/>)}</div>
      <div className="wait-copy"><b>{label}</b>{detail && <span>{detail}</span>}
        {slow && slowHint && <span>{slowMessage}</span>}
      </div>
      {(slow && (onRetry || onCancel)) && <div className="wait-actions">
        {onRetry && <button className="btn small" type="button" onClick={onRetry}>重试</button>}
        {onCancel && <button className="btn small ghost" type="button" onClick={onCancel}>取消</button>}
      </div>}
    </div>
  </div>
}

/** Fallback for small actions with no dedicated content slot, including new bridge actions. */
export function RequestLoading() {
  const waits = React.useSyncExternalStore(subscribeActivities, getActivities, getActivities)
  const active = waits.filter(showActivity).sort((a,b) => a.started-b.started)[0]
  return active ? <div className="wait-global"><Loading key={active.id} started={active.started} label={waitLabel(active.method)}
    onCancel={active.cancel} /></div> : null
}
