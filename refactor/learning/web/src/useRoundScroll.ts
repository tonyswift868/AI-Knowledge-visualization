import * as React from 'react'
import { useApp } from './store'
import './round-scroll.css'

/** Anchor a conversation turn, never its growing tail. No spacer or history rewrite. */
export function useRoundScroll(container: React.RefObject<HTMLDivElement | null>, scope: string | null | undefined) {
  const reduced = useApp(s => s.settings?.reduce_motion ?? false)
  const state = React.useRef({ scope, loaded: false, waiting: false, stopped: false,
    before: new Set<string>(), anchor: '', aligned: false, top: 0 })
  const begin = React.useCallback((nextScope = scope) => {
    const s = state.current
    s.scope = nextScope
    s.before = new Set(Array.from(container.current?.querySelectorAll<HTMLElement>('[data-turn-key]') ?? []).map(e => e.dataset.turnKey!))
    s.waiting = true; s.stopped = false; s.anchor = ''; s.aligned = false
  }, [container, scope])
  React.useLayoutEffect(() => {
    const el = container.current
    if (!el) return
    const s = state.current
    if (s.scope !== scope) {
      Object.assign(s, { scope, loaded: false, waiting: false, stopped: false, anchor: '' })
    }
    if (!s.loaded) { el.scrollTop = el.scrollHeight; s.top = el.scrollTop; s.loaded = true }
    if (!s.waiting || s.stopped) return
    const items = Array.from(el.querySelectorAll<HTMLElement>('[data-turn-key]'))
    const fresh = items.filter(e => !s.before.has(e.dataset.turnKey!))
    // A streamed placeholder may be replaced by the persisted teacher bubble.
    let anchor = items.find(e => e.dataset.turnKey === s.anchor)
    const user = fresh.find(e => e.dataset.turnRole === 'user')
    if (user && (!anchor || anchor.dataset.turnRole !== 'user')) {
      anchor = user; s.aligned = false
    } else if (!anchor) { anchor = fresh[0]; s.aligned = false }
    if (!anchor) return
    s.anchor = anchor.dataset.turnKey!
    if (s.aligned) return
    const move = () => {
      if (s.stopped || !anchor?.isConnected) return
      anchor.scrollIntoView({ block: 'start', inline: 'nearest', behavior:
        reduced || window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
      // If the turn fits on screen the browser clamps naturally. Retry only
      // while it grows towards the top, not after the top has been reached.
      s.aligned = Math.abs(anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - 12) < 2
      s.top = el.scrollTop
    }
    move()
  })
  React.useEffect(() => {
    const el = container.current
    if (!el) return
    const stop = () => { state.current.stopped = true }
    const wheel = (e: WheelEvent) => { if (e.deltaY < 0) stop() }
    const key = (e: KeyboardEvent) => { if (['ArrowUp','PageUp','Home'].includes(e.key)) stop() }
    // A pointer on the scroll container also covers dragging its scrollbar.
    // Do not infer intent from scroll events: smooth programmatic scrolling
    // generates those too, including upward movement on a new turn.
    el.addEventListener('wheel', wheel, { passive: true }); el.addEventListener('touchstart', stop, { passive: true })
    el.addEventListener('keydown', key); el.addEventListener('pointerdown', stop)
    return () => { el.removeEventListener('wheel', wheel); el.removeEventListener('touchstart', stop); el.removeEventListener('keydown', key); el.removeEventListener('pointerdown', stop) }
  })
  return begin
}
