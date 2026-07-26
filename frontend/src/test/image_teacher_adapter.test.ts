import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ImageTeacherAdapter } from "../features/teachers/presentation/adapters/ImageTeacherAdapter"
import type { Teacher2DManifest } from "../features/teachers/presentation/teacherPresentationTypes"

const manifest: Teacher2DManifest = {
  id: "v9",
  displayName: "V9 teacher",
  renderer: "image2d",
  skeleton: "dom-css-v1",
  textures: ["/teaching.png", "/swimsuit.png"],
  imageStates: { teaching: "/teaching.png", swimsuit: "/swimsuit.png" },
  armature: "v9",
  defaultAnimation: "idle",
  animationAliases: { idle: "idle", explaining: "explain", praising: "cheer" },
}

class InstantImage {
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  set src(_value: string) { queueMicrotask(() => this.onload?.()) }
}

describe("V9 lightweight image teacher adapter", () => {
  beforeEach(() => {
    vi.stubGlobal("Image", InstantImage)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    document.body.replaceChildren()
  })

  it("loads both approved forms without WebGL and maps teaching reactions", async () => {
    const host = document.createElement("div")
    host.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0, right: 400, bottom: 700, width: 400, height: 700, toJSON: () => ({}) })
    document.body.append(host)
    const snapshots: Array<Record<string, unknown>> = []
    const adapter = new ImageTeacherAdapter({ host, onSnapshot: value => snapshots.push(value) })

    await adapter.load(manifest)
    expect(host.querySelectorAll(".image-teacher-form")).toHaveLength(2)
    expect(adapter.listSkins()).toEqual(["teaching", "swimsuit"])
    expect(snapshots.at(-1)).toMatchObject({ webgl: false, canvasWidth: 400, canvasHeight: 700 })

    adapter.playSemanticState("explaining")
    expect(host.querySelector(".image-teacher")).toHaveAttribute("data-state", "explain")
    expect(host.querySelector(".image-teacher-reaction")).toHaveTextContent("看这里")
  })

  it("uses a visible transition when switching forms and reacts by click zone", async () => {
    vi.useFakeTimers()
    const host = document.createElement("div")
    host.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0, right: 400, bottom: 700, width: 400, height: 700, toJSON: () => ({}) })
    document.body.append(host)
    const adapter = new ImageTeacherAdapter({ host })
    await adapter.load(manifest)

    adapter.setSkin("swimsuit")
    const root = host.querySelector(".image-teacher")!
    expect(root).toHaveAttribute("data-skin", "swimsuit")
    expect(root).toHaveClass("is-transforming")
    expect(host.querySelector(".form-swimsuit")).toHaveClass("active")

    root.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, clientY: 90 }))
    expect(root).toHaveAttribute("data-state", "surprised")
    expect(host.querySelector(".image-teacher-reaction")).toHaveTextContent("呀！")

    vi.advanceTimersByTime(1500)
    expect(root).toHaveAttribute("data-state", "idle")
    expect(root).not.toHaveClass("is-transforming")
  })
})
