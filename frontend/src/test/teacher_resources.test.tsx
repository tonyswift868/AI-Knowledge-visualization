import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { App } from "../App"

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

const voicePack = {
  pack_id: "mentorflow-original-iris",
  version: "1.0.0",
  display_name: "艾莉丝原创教师声包",
  state: "not_installed",
  archive_bytes: 335_544_320,
  default: true,
  styles: ["lively", "relaxed"],
  runtime_adapter: "qwen3_tts",
}

beforeEach(() => {
  window.history.pushState({}, "", "/?tool=teacher-resources")
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === "/api/workspaces") return jsonResponse([])
    if (url.endsWith("/teacher-voice/status")) {
      return jsonResponse({
        schema: "mentorflow.teacher_voice.status.v1",
        fallback_policy: "off",
        active_pack: null,
        runtime: { state: "stopped", starts_model: false },
        packs: [voicePack],
      })
    }
    if (url.endsWith("/teacher-voice/packs")) return jsonResponse({ packs: [voicePack] })
    if (url.endsWith("/teacher-voice/settings")) return jsonResponse({ fallback_policy: "off" })
    return jsonResponse({})
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, "", "/")
  localStorage.clear()
})

describe("standalone teacher resources", () => {
  it("restores the workspace's explicitly saved local Live2D teacher on the detached stage", async () => {
    window.history.pushState({}, "", "/?tool=teacher-stage&workspaceId=workspace-tsubaki&state=idle&theme=rainy_window")
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/api/active-teaching/workspaces/workspace-tsubaki/snapshot")) {
        return jsonResponse({
          workspace_id: "workspace-tsubaki",
          materials: [],
          receipt: null,
          knowledge_graph: { knowledge_points: [], edges: [] },
          plan: null,
          session: {
            state: {
              teacher_persona_name: "艾莉丝",
              teacher_visual_manifest_url: "/teacher2d/tsubaki-live2d/manifest.json",
            },
          },
        })
      }
      if (url === "/teacher2d/tsubaki-live2d/manifest.json") {
        return jsonResponse({
          id: "teacher-live2d-tsubaki-local",
          displayName: "艾莉丝",
          renderer: "image2d",
          skeleton: "teacher.png",
          armature: "teacher-live2d-tsubaki-local",
          defaultAnimation: "idle",
          textures: ["teacher.png"],
          imageStates: { teaching: "teacher.png", swimsuit: "teacher.png" },
        })
      }
      if (url.includes("/api/teacher-avatars/today")) return jsonResponse({ item: null, requires_import: false, rotation_key: "today" })
      if (url.includes("/api/teacher-avatar-card-presets")) return jsonResponse({ items: [], count: 0, total: 0, status: "unavailable", available: false })
      if (url.includes("/api/teacher-avatar-character-packs")) return jsonResponse({ items: [], count: 0 })
      if (url.includes("/api/teacher-avatars")) return jsonResponse({ items: [], motion_library: [] })
      return jsonResponse({})
    }))

    render(<App />)

    expect(await screen.findByRole("heading", { name: "艾莉丝" })).toBeInTheDocument()
    const stage = screen.getByRole("main", { name: "课堂 2D 教师" })
    expect(stage).toHaveAttribute("data-classroom-theme", "rainy_window")
    fireEvent(window, new MessageEvent("message", {
      origin: window.location.origin,
      data: { type: "mentorflow.teacher-theme", theme: "cyber" },
    }))
    expect(stage).toHaveAttribute("data-classroom-theme", "cyber")
    await vi.waitFor(() => {
      expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toContain(
        "/teacher2d/tsubaki-live2d/manifest.json",
      )
    })
    const requestedUrls = vi.mocked(fetch).mock.calls.map(([input]) => String(input))
    expect(requestedUrls).not.toContain("/api/provider-sessions/current")
  })

  it("does not redistribute private 2D models and keeps voice packs separate", async () => {
    render(<App />)

    expect(screen.queryByRole("link", { name: "下载 2D 资源包" })).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "连接你已合法持有的 2D 教师" })).toBeInTheDocument()
    expect(screen.getByText(/不提供或再分发第三方 Live2D 模型/)).toBeInTheDocument()

    expect(await screen.findByRole("button", { name: "下载并使用" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "准备教师资源" })).toBeInTheDocument()
    const requestedUrls = vi.mocked(fetch).mock.calls.map(([input]) => String(input))
    expect(requestedUrls).not.toContain("/api/provider-sessions/current")
    expect(requestedUrls).not.toContain("/api/workspaces")
  })

  it("keeps an empty catalog explicit and matches local 2D and voice selections", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === "/api/workspaces") return jsonResponse([])
      if (url.endsWith("/teacher-voice/status")) {
        return jsonResponse({
          schema: "mentorflow.teacher_voice.status.v1",
          fallback_policy: "off",
          active_pack: null,
          runtime: { state: "stopped", starts_model: false },
          packs: [],
        })
      }
      if (url.endsWith("/teacher-voice/packs")) return jsonResponse({ packs: [] })
      if (url.endsWith("/teacher-voice/settings")) return jsonResponse({ fallback_policy: "off" })
      return jsonResponse({})
    }))

    render(<App />)

    expect(await screen.findByTestId("voice-pack-state")).toHaveTextContent("未找到")
    const voicePicker = screen.getByTestId("voice-pack-local-picker")
    fireEvent.change(voicePicker, { target: { files: [] } })
    expect(screen.getByTestId("voice-local-resource-state")).toHaveTextContent("已检查 0 个文件")

    fireEvent.change(voicePicker, {
      target: { files: [new File(["voice"], "teacher-voice-pack.zip", { type: "application/zip" })] },
    })
    expect(screen.getByTestId("voice-local-resource-state")).toHaveTextContent("已匹配本地候选")

    const twoDPicker = screen.getByTestId("teacher-2d-local-picker")
    fireEvent.change(twoDPicker, { target: { files: [] } })
    expect(screen.getByTestId("teacher-2d-resource-state")).toHaveTextContent("已检查 0 个文件")
    fireEvent.change(twoDPicker, {
      target: { files: [new File(["model"], "teacher.model3.json", { type: "application/json" })] },
    })
    expect(screen.getByTestId("teacher-2d-resource-state")).toHaveTextContent("已匹配本地 2D 资源")
  })
})
