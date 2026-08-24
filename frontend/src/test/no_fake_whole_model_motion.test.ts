import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ImageTeacherAdapter } from "../features/teachers/presentation/adapters/ImageTeacherAdapter"
import { layeredTeacherAnimations, resolveLayeredBonePose } from "../features/teachers/presentation/adapters/LayeredTeacherAdapter"
import type { Teacher2DManifest } from "../features/teachers/presentation/teacherPresentationTypes"

// Read the authored CSS in Vitest's Node runtime so the regression test guards
// selectors/keyframes instead of relying on jsdom's empty CSS implementation.
// @ts-ignore Node is supplied by Vitest; the app's browser build does not import this test.
import { readFileSync } from "node:fs"
// @ts-ignore Node is supplied by Vitest; the app's browser build does not import this test.
import { resolve } from "node:path"

// @ts-ignore Node is supplied by Vitest; the app's browser build does not import this test.
const testProjectRoot = /[\\/]frontend$/.test(process.cwd()) ? process.cwd() : resolve(process.cwd(), "frontend")
const imageTeacherCss = readFileSync(resolve(testProjectRoot, "src/features/teachers/presentation/imageTeacher.css"), "utf8")
const layeredTeacherCss = readFileSync(resolve(testProjectRoot, "src/features/teachers/presentation/layeredTeacher.css"), "utf8")
const companionCss = readFileSync(resolve(testProjectRoot, "src/features/teachers/teacher-avatar.css"), "utf8")

const manifest: Teacher2DManifest = {
  id: "v9",
  displayName: "V9 teacher",
  renderer: "image2d",
  skeleton: "dom-css-v1",
  textures: ["/teaching.png", "/swimsuit.png"],
  imageStates: { teaching: "/teaching.png", swimsuit: "/swimsuit.png" },
  armature: "v9",
  defaultAnimation: "idle",
  animationAliases: { idle: "idle", explaining: "explain" },
}

class InstantImage {
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  set src(_value: string) { queueMicrotask(() => this.onload?.()) }
}

describe("fallback teacher fake whole-model motion guard", () => {
  beforeEach(() => vi.stubGlobal("Image", InstantImage))

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.replaceChildren()
  })

  it("keeps image semantic actions static while preserving labels, reactions, and camera controls", async () => {
    const host = document.createElement("div")
    host.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0, right: 400, bottom: 700, width: 400, height: 700, toJSON: () => ({}) })
    document.body.append(host)
    const snapshots: Array<Record<string, unknown>> = []
    const adapter = new ImageTeacherAdapter({ host, onSnapshot: value => snapshots.push(value) })

    await adapter.load(manifest)
    adapter.playSemanticState("explaining")

    const root = host.querySelector<HTMLElement>(".image-teacher")!
    const body = host.querySelector<HTMLElement>(".image-teacher-body")!
    expect(root.dataset.motionModel).toBe("static-image-fallback")
    expect(root.dataset.semanticMotion).toBe("unsupported")
    expect(root).toHaveAttribute("data-state", "explain")
    expect(root).toHaveAttribute("aria-label", expect.stringContaining("静态图片回退"))
    expect(adapter.listAnimations()).toEqual(["idle"])
    expect(snapshots.at(-1)).toMatchObject({ currentAnimation: "idle", animations: ["idle"] })
    expect(body.style.animation).toBe("")
    expect(body.style.transform).toBe("")

    adapter.setScale(1.4)
    adapter.setOffset(.2, -.15)
    adapter.setMirror(true)
    expect(root.style.getPropertyValue("--teacher-scale")).toBe("1.4")
    expect(root.style.getPropertyValue("--teacher-offset-x")).toBe("20%")
    expect(root.style.getPropertyValue("--teacher-offset-y")).toBe("-15%")
    expect(root.style.getPropertyValue("--teacher-mirror")).toBe("-1")
  })

  it("does not retain semantic whole-body keyframes while keeping authored form and environmental animation", () => {
    expect(imageTeacherCss).not.toMatch(/image-teacher-(wave|think|talk|explain|question|cheer|heart|confused|surprised)/)
    expect(imageTeacherCss).not.toMatch(/\.image-teacher\[data-state=.*?\][^{]*\.image-teacher-body[^}]*animation/)
    expect(imageTeacherCss).not.toContain("animation:image-teacher-breathe")
    expect(imageTeacherCss).toContain("animation:image-teacher-glow")
    expect(imageTeacherCss).toContain("image-teacher-form-in")
  })

  it("keeps layered semantic poses off the root pelvis transform", () => {
    const idle = resolveLayeredBonePose(1.2, { x: 0, y: 0 }, "idle", .75)
    for (const animation of layeredTeacherAnimations) {
      const pose = resolveLayeredBonePose(1.2, { x: 0, y: 0 }, animation, .75)
      expect(pose.pelvisRotation).toBe(idle.pelvisRotation)
      expect(pose.pelvisShiftX).toBe(idle.pelvisShiftX)
      expect(pose.jumpY).toBe(0)
      expect(pose.headScale).toBe(1)
      expect(pose.neckScale).toBe(1)
    }
    expect(layeredTeacherCss).not.toMatch(/\.layered-teacher\[data-state=/)
    expect(layeredTeacherCss).toContain("animation:layered-glow")
    expect(layeredTeacherCss).toContain("layered-teacher-transition")
  })

  it("keeps the legacy Spine illustration fallback static", () => {
    expect(companionCss).not.toContain("teacher-breathe")
    expect(companionCss).toContain("animation:teacher-pose-in .32s ease-out")
    expect(companionCss).toContain("@keyframes teacher-rain")
  })
})
