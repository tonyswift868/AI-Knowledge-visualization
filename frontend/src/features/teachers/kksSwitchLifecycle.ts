/** Generation-safe, resource-safe card switching primitives.
 *
 * This layer deliberately knows nothing about PMX/GLTF. A production loader
 * can be injected, while tests use a tiny controllable fake loader. Stale
 * results are disposed, a failed replacement keeps the last complete model,
 * and a pending request can be cancelled without tearing down that fallback.
 */

export type KksDisposable = { dispose?: () => void }
export type KksLoadResult<T> = T & KksDisposable
export type KksLoader<T> = (cardId: string, signal: AbortSignal) => Promise<KksLoadResult<T>>
export type KksSwitchStatus = "idle" | "loading" | "ready" | "fallback" | "error"
export type KksSwitchState<T> = {
  generation: number
  requestedCardId: string
  activeCardId: string
  status: KksSwitchStatus
  model: KksLoadResult<T> | null
  error: string
}

export type KksSwitchResult<T> = {
  generation: number
  cardId: string
  status: "ready" | "fallback" | "cancelled" | "stale" | "error"
  model: KksLoadResult<T> | null
  error?: string
}

const abortMessage = (error: unknown) => error instanceof Error ? error.message : String(error || "cancelled")

export class KksSwitchLifecycle<T> {
  private generation = 0
  private pending: { generation: number; controller: AbortController } | null = null
  private committed: KksLoadResult<T> | null = null
  private committedCardId = ""
  private disposed = new Set<object>()
  private state: KksSwitchState<T> = { generation: 0, requestedCardId: "", activeCardId: "", status: "idle", model: null, error: "" }

  constructor(private readonly loader: KksLoader<T>) {}

  getState(): KksSwitchState<T> {
    return { ...this.state }
  }

  async switchTo(cardId: string): Promise<KksSwitchResult<T>> {
    const requested = String(cardId || "").trim()
    const generation = ++this.generation
    this.pending?.controller.abort()
    const controller = new AbortController()
    this.pending = { generation, controller }
    const previous = this.committed
    const previousId = this.committedCardId
    this.state = { generation, requestedCardId: requested, activeCardId: previousId, status: "loading", model: previous, error: "" }
    if (!requested) {
      this.pending = null
      this.state = { ...this.state, status: previous ? "fallback" : "error", error: "card_id_required" }
      return { generation, cardId: requested, status: previous ? "fallback" : "error", model: previous, error: "card_id_required" }
    }
    try {
      const loaded = await this.loader(requested, controller.signal)
      if (generation !== this.generation || controller.signal.aborted) {
        this.disposeOnce(loaded)
        return { generation, cardId: requested, status: controller.signal.aborted ? "cancelled" : "stale", model: null }
      }
      this.pending = null
      this.committed = loaded
      this.committedCardId = requested
      this.state = { generation, requestedCardId: requested, activeCardId: requested, status: "ready", model: loaded, error: "" }
      if (previous && previous !== loaded) this.disposeOnce(previous)
      return { generation, cardId: requested, status: "ready", model: loaded }
    } catch (error) {
      if (generation !== this.generation || controller.signal.aborted) {
        return { generation, cardId: requested, status: "cancelled", model: null, error: abortMessage(error) }
      }
      this.pending = null
      const message = abortMessage(error)
      this.state = { generation, requestedCardId: requested, activeCardId: previousId, status: previous ? "fallback" : "error", model: previous, error: message }
      return { generation, cardId: requested, status: previous ? "fallback" : "error", model: previous, error: message }
    }
  }

  cancel(): void {
    this.generation += 1
    this.pending?.controller.abort()
    this.pending = null
    if (this.committed) this.state = { ...this.state, generation: this.generation, status: "ready", model: this.committed }
  }

  dispose(): void {
    this.generation += 1
    this.pending?.controller.abort()
    this.pending = null
    if (this.committed) this.disposeOnce(this.committed)
    this.committed = null
    this.committedCardId = ""
    this.state = { generation: this.generation, requestedCardId: "", activeCardId: "", status: "idle", model: null, error: "" }
  }

  private disposeOnce(model: KksLoadResult<T>): void {
    if (typeof model !== "object" || model === null || this.disposed.has(model)) return
    this.disposed.add(model)
    model.dispose?.()
  }
}

export type KksRenderScheduler = {
  invalidate: () => void
  setVisible: (visible: boolean) => void
  dispose: () => void
}

/** One render after a state change, only while the preview is visible. */
export function createOnDemandRenderScheduler(
  render: () => void,
  schedule: (callback: () => void) => number = callback => requestAnimationFrame(callback),
  cancel: (handle: number) => void = handle => cancelAnimationFrame(handle),
): KksRenderScheduler {
  let visible = true
  let disposed = false
  let handle: number | null = null
  const invalidate = () => {
    if (disposed || !visible || handle !== null) return
    handle = schedule(() => { handle = null; if (!disposed && visible) render() })
  }
  return {
    invalidate,
    setVisible(next) { visible = next; if (visible) invalidate(); else if (handle !== null) { cancel(handle); handle = null } },
    dispose() { disposed = true; if (handle !== null) cancel(handle); handle = null },
  }
}

