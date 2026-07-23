import { describe, expect, it } from "vitest"
import { createOnDemandRenderScheduler, KksSwitchLifecycle } from "./kksSwitchLifecycle"

type FakeModel = { id: string; disposed: number; dispose: () => void }

const deferred = <T>() => {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((ok, bad) => { resolve = ok; reject = bad })
  return { promise, resolve, reject }
}

describe("KKS generation-safe card switching", () => {
  it("keeps the last complete model on a failed replacement", async () => {
    const pending = new Map<string, ReturnType<typeof deferred<FakeModel>>>()
    const loader = (id: string) => {
      const item = deferred<FakeModel>(); pending.set(id, item); return item.promise
    }
    const lifecycle = new KksSwitchLifecycle(loader)
    const first = lifecycle.switchTo("card-a")
    const model = { id: "card-a", disposed: 0, dispose() { this.disposed += 1 } }
    pending.get("card-a")!.resolve(model)
    expect((await first).status).toBe("ready")
    const second = lifecycle.switchTo("card-b")
    pending.get("card-b")!.reject(new Error("cache miss"))
    const result = await second
    expect(result.status).toBe("fallback")
    expect(result.model).toBe(model)
    expect(model.disposed).toBe(0)
  })

  it("disposes stale results and does not let twenty rapid switches race", async () => {
    const pending = new Map<string, ReturnType<typeof deferred<FakeModel>>>()
    const loader = (id: string) => { const item = deferred<FakeModel>(); pending.set(id, item); return item.promise }
    const lifecycle = new KksSwitchLifecycle(loader)
    const requests = Array.from({ length: 20 }, (_, index) => lifecycle.switchTo(`card-${index}`))
    const models = Array.from({ length: 20 }, (_, index) => ({ id: `card-${index}`, disposed: 0, dispose() { this.disposed += 1 } }))
    models.forEach((model, index) => pending.get(`card-${index}`)!.resolve(model))
    const results = await Promise.all(requests)
    expect(results.filter(item => item.status === "ready")).toHaveLength(1)
    expect(lifecycle.getState().activeCardId).toBe("card-19")
    expect(models.slice(0, 19).every(model => model.disposed === 1)).toBe(true)
    expect(models[19].disposed).toBe(0)
    lifecycle.dispose()
    expect(models[19].disposed).toBe(1)
  })

  it("schedules at most one visible render and cancels hidden work", () => {
    const callbacks: Array<() => void> = []
    const rendered: number[] = []
    const scheduler = createOnDemandRenderScheduler(() => rendered.push(1), callback => { callbacks.push(callback); return callbacks.length }, () => {})
    scheduler.invalidate(); scheduler.invalidate()
    expect(callbacks).toHaveLength(1)
    scheduler.setVisible(false); callbacks.shift()!(); expect(rendered).toHaveLength(0)
    scheduler.setVisible(true); expect(callbacks).toHaveLength(1); callbacks.shift()!(); expect(rendered).toHaveLength(1)
    scheduler.dispose(); scheduler.invalidate(); expect(callbacks).toHaveLength(0)
  })
})

