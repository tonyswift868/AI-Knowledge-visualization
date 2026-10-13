/** Test-only clock and DOM readiness. Polling deadlines remain real. */
const nativeTimeout = window.setTimeout.bind(window)
export const pause = (ms: number) => new Promise<void>(resolve => nativeTimeout(resolve, ms))
export async function until(fn: () => boolean, label = 'DOM ready', limit = 15000) {
  const end = performance.now() + limit
  while (!fn()) {
    if (performance.now() >= end) throw Error(label + ': ' + document.body.innerText)
    await pause(10)
  }
}
export function clock115() {
  const originalNow = Date.now, originalTimeout = window.setTimeout, originalClear = window.clearTimeout
  let now = 1700000000000, id = -1
  const pending = new Map<number, {at: number; delay: number; fn: () => void}>()
  Date.now = () => now
  window.setTimeout = ((fn: () => void, delay = 0, ...args: any[]) => {
    const key = id--; pending.set(key, {at: now + delay, delay, fn: () => fn(...args)}); return key
  }) as any
  window.clearTimeout = ((key: number) => { pending.delete(key) }) as any
  return {
    pending: () => pending.size,
    advance(ms: number) {
      now += ms
      for (const [key, task] of [...pending]) if (task.at <= now) { pending.delete(key); task.fn() }
    },
    fire(delay: number) {
      const matching=[...pending.values()].filter(task=>task.delay===delay)
      if(matching.length)this.advance(Math.max(0,Math.max(...matching.map(task=>task.at))-now))
    },
    restore() { Date.now = originalNow; window.setTimeout = originalTimeout; window.clearTimeout = originalClear }
  }
}
