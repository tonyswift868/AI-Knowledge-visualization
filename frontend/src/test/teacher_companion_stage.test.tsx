import { StrictMode } from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { TeacherCompanionPortrait, TeacherCompanionStage, teacherRuntimeLabel } from "../features/teachers/TeacherCompanionStage"
import { Teacher2DStage } from "../features/teachers/presentation/Teacher2DStage"
import type { TeacherPresentationController } from "../features/teachers/presentation/TeacherPresentationController"
import type { TeacherPresentationControllerOptions } from "../features/teachers/presentation/TeacherPresentationController"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function fakeController(
  options: TeacherPresentationControllerOptions,
  overrides: Partial<TeacherPresentationController> = {},
): TeacherPresentationController {
  return {
    mount: vi.fn(async () => {
      options.onSnapshot({
        status: "ready",
        renderer: "dragonbones2d",
        armature: "shizuku",
        currentAnimation: "idle_00",
        semanticState: "idle",
        animations: ["idle_00", "tap_body_00"],
        skins: ["default"],
        canvasWidth: 420,
        canvasHeight: 680,
        webgl: true,
        loadedTextures: ["/0.png"],
      })
    }),
    dispose: vi.fn(),
    setState: vi.fn(),
    setScale: vi.fn(),
    setOffset: vi.fn(),
    setMirror: vi.fn(),
    setSkin: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    reset: vi.fn(),
    playAnimation: vi.fn(),
    getSnapshot: vi.fn(),
    ...overrides,
  } as unknown as TeacherPresentationController
}

describe("2D teacher companion stage", () => {
  it("reports the hierarchical runtime instead of calling it an image fallback", () => {
    expect(teacherRuntimeLabel("live2d")).toBe("Live2D Cubism")
    expect(teacherRuntimeLabel("layered2d")).toBe("Pixi 层级骨骼")
    expect(teacherRuntimeLabel("image2d")).toBe("V9 图片回退")
  })

  it("uses the lightweight V9 image renderer boundary", () => {
    render(<TeacherCompanionStage teacherName="艾莉丝" />)
    const stage = screen.getByTestId("teacher-companion-stage")
    expect(stage).toHaveAttribute("data-visual-mode", "2d-layered")
    expect(stage).toHaveAttribute("data-runtime-target", "layered2d")
    expect(screen.getByTestId("teacher-2d-stage")).toHaveAttribute("data-renderer-kind", "image2d")
  })

  it("switches presentation state without invoking the legacy 3D renderer", () => {
    render(<TeacherCompanionStage />)
    fireEvent.click(screen.getByRole("button", { name: /鼓励/ }))
    expect(screen.getByTestId("teacher-companion-stage")).toHaveClass("mood-encouraging")
    expect(screen.getByText(/不用急着一次答对/)).toBeInTheDocument()
    expect(screen.getByTestId("teacher-2d-stage")).toHaveAttribute("data-semantic-state", "encouraging")
    const formButton = screen.getByRole("button", { name: "切换教师形态" })
    fireEvent.click(formButton)
    expect(formButton).toHaveAttribute("aria-pressed", "true")
  })

  it("accepts a question and enters explanation mode", () => {
    render(<TeacherCompanionStage teacherName="艾莉丝" />)
    fireEvent.change(screen.getByLabelText("向 AI 教师提问"), { target: { value: "为什么这里要用反证法？" } })
    fireEvent.click(screen.getByRole("button", { name: "发送给 AI 教师" }))
    expect(screen.getByTestId("teacher-companion-stage")).toHaveClass("mood-explaining")
    expect(screen.getByText(/为什么这里要用反证法/)).toBeInTheDocument()
    expect(screen.getByTestId("teacher-2d-stage")).toHaveAttribute("data-semantic-state", "explaining")
  })

  it("renders a compact learning-session portrait from the real teaching state", () => {
    render(<TeacherCompanionPortrait teacherName="艾莉丝" state="encouraging" />)
    const portrait = screen.getByTestId("teacher-companion-portrait")
    expect(portrait).toHaveAttribute("data-teacher-state", "encouraging")
    expect(screen.getByTestId("teacher-2d-stage")).toHaveAttribute("data-semantic-state", "encouraging")
    expect(screen.getByText(/鼓励模式/)).toBeInTheDocument()
  })

  it("exposes the live stage toolbar without mounting a second renderer", () => {
    render(<TeacherCompanionStage />)
    const stage = screen.getByTestId("teacher-2d-stage")
    fireEvent.click(screen.getByRole("button", { name: "左右镜像教师" }))
    expect(screen.getByRole("button", { name: "左右镜像教师" })).toHaveClass("active")
    expect(screen.getByRole("button", { name: "左右镜像教师" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(screen.getByRole("button", { name: "收起教师舞台" }))
    expect(stage).toHaveClass("collapsed")
    expect(screen.getByRole("button", { name: "展开教师舞台" })).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(screen.getByRole("button", { name: "展开教师舞台" }))
    expect(stage).not.toHaveClass("collapsed")
    fireEvent.click(screen.getByRole("button", { name: "显示或隐藏教师调试信息" }))
    expect(screen.getByTestId("teacher-2d-debug")).toBeInTheDocument()
  })

  it("offers face, half-body and full-body camera presets with position tuning", async () => {
    const setScale = vi.fn()
    const setOffset = vi.fn()
    const controllerFactory = (options: TeacherPresentationControllerOptions) => fakeController(options, {
      setScale,
      setOffset,
    })
    render(<Teacher2DStage compact controllerFactory={controllerFactory} />)

    const stage = screen.getByTestId("teacher-2d-stage")
    expect(stage).toHaveAttribute("data-camera-preset", "half")
    expect(stage).toHaveAttribute("data-camera-zoom", "1.52")
    expect(stage).toHaveAttribute("data-camera-y", "0.44")
    fireEvent.click(screen.getByRole("button", { name: "脸部" }))
    expect(stage).toHaveAttribute("data-camera-preset", "face")
    expect(stage).toHaveAttribute("data-camera-zoom", "2.30")
    expect(stage).toHaveAttribute("data-camera-y", "0.70")
    fireEvent.click(screen.getByRole("button", { name: "教师向上移动" }))
    expect(stage).toHaveAttribute("data-camera-preset", "custom")
    expect(stage).toHaveAttribute("data-camera-y", "0.64")
    fireEvent.click(screen.getByRole("button", { name: "恢复教师默认状态" }))
    expect(stage).toHaveAttribute("data-camera-preset", "half")
    await waitFor(() => {
      expect(setScale).toHaveBeenCalledWith(1.52)
      expect(setOffset).toHaveBeenCalledWith(0, .44)
    })
  })

  it("switches the V9 form through the visible dual-form controls without remounting", async () => {
    const setSkin = vi.fn()
    const mount = vi.fn()
    const controllerFactory = (options: TeacherPresentationControllerOptions) => fakeController(options, {
      mount: vi.fn(async () => {
        mount()
        options.onSnapshot({
          status: "ready",
          renderer: "image2d",
          armature: "teacher-blonde-fang-v9",
          currentAnimation: "idle",
          semanticState: "idle",
          animations: ["idle", "heart"],
          skins: ["teaching", "swimsuit"],
          canvasWidth: 420,
          canvasHeight: 680,
          webgl: false,
          loadedTextures: ["/teaching.png", "/swimsuit.png"],
        })
      }),
      setSkin,
    })
    render(<Teacher2DStage controllerFactory={controllerFactory} />)

    const swimsuitButton = await screen.findByTestId("teacher-form-swimsuit")
    expect(swimsuitButton).toHaveAttribute("aria-pressed", "false")
    fireEvent.click(swimsuitButton)
    expect(swimsuitButton).toHaveAttribute("aria-pressed", "true")
    expect(setSkin).toHaveBeenCalledWith("swimsuit")
    expect(mount).toHaveBeenCalledTimes(1)
  })

  it("does not feed renderer skin snapshots back into an update loop", async () => {
    let publish: TeacherPresentationControllerOptions["onSnapshot"] = () => undefined
    const ready = () => ({
      status: "ready" as const,
      renderer: "image2d" as const,
      armature: "teacher-v9",
      currentAnimation: "idle",
      semanticState: "idle" as const,
      animations: ["idle"],
      skins: ["teaching", "swimsuit"],
      canvasWidth: 420,
      canvasHeight: 680,
      webgl: false,
      loadedTextures: ["/teaching.png", "/swimsuit.png"],
    })
    const setSkin = vi.fn(() => publish(ready()))
    const controllerFactory = (options: TeacherPresentationControllerOptions) => {
      publish = options.onSnapshot
      return fakeController(options, {
        mount: vi.fn(async () => options.onSnapshot(ready())),
        setSkin,
      })
    }

    render(<Teacher2DStage controllerFactory={controllerFactory} />)

    await screen.findByTestId("teacher-form-swimsuit")
    await waitFor(() => expect(setSkin).toHaveBeenCalledTimes(1))
  })

  it("keeps surrounding learning UI available when the model fails", async () => {
    const controllerFactory = (options: TeacherPresentationControllerOptions) => fakeController(options, {
      mount: vi.fn(async () => {
        options.onSnapshot({
          status: "error",
          renderer: "dragonbones2d",
          semanticState: "error",
          animations: [],
          skins: [],
          canvasWidth: 0,
          canvasHeight: 0,
          webgl: false,
          loadedTextures: [],
          error: "测试模型加载失败",
        })
        throw new Error("测试模型加载失败")
      }),
    })
    render(<><input aria-label="学习回答" /><Teacher2DStage controllerFactory={controllerFactory} /></>)
    expect(await screen.findByRole("status")).toHaveTextContent("测试模型加载失败")
    expect(screen.getByLabelText("学习回答")).toBeEnabled()
  })

  it("honors reduced motion by starting paused", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
    const pause = vi.fn()
    const controllerFactory = (options: TeacherPresentationControllerOptions) => fakeController(options, { pause })
    render(<Teacher2DStage controllerFactory={controllerFactory} />)
    await waitFor(() => expect(pause).toHaveBeenCalled())
    expect(screen.getByRole("button", { name: "继续教师动画" })).toBeInTheDocument()
  })

  it("disposes the previous renderer before a manifest switch", async () => {
    const controllers: TeacherPresentationController[] = []
    const controllerFactory = (options: TeacherPresentationControllerOptions) => {
      const controller = fakeController(options)
      controllers.push(controller)
      return controller
    }
    const view = render(<Teacher2DStage manifestUrl="/teacher-a.json" controllerFactory={controllerFactory} />)
    await waitFor(() => expect(controllers).toHaveLength(1))
    view.rerender(<Teacher2DStage manifestUrl="/teacher-b.json" controllerFactory={controllerFactory} />)
    await waitFor(() => expect(controllers).toHaveLength(2))
    expect(controllers[0].dispose).toHaveBeenCalledTimes(1)
  })

  it("never leaves two active renderers during StrictMode remount", async () => {
    let active = 0
    let maximumActive = 0
    let mounts = 0
    const controllerFactory = (options: TeacherPresentationControllerOptions) => fakeController(options, {
      mount: vi.fn(async () => {
        active += 1
        mounts += 1
        maximumActive = Math.max(maximumActive, active)
      }),
      dispose: vi.fn(() => { active -= 1 }),
    })
    render(<StrictMode><Teacher2DStage controllerFactory={controllerFactory} /></StrictMode>)
    await waitFor(() => expect(mounts).toBeGreaterThanOrEqual(2))
    expect(maximumActive).toBe(1)
    expect(active).toBe(1)
  })
})
