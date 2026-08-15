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
  it("keeps separate visible download entries for 2D resources and voice packs", async () => {
    render(<App />)

    const twoDDownload = screen.getByRole("link", { name: "下载 2D 资源包" })
    expect(twoDDownload).toHaveAttribute("href", "/api/teacher2d/tsubaki-live2d/download")
    expect(twoDDownload).toHaveAttribute("download")

    expect(await screen.findByRole("button", { name: "下载并使用" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "准备教师资源" })).toBeInTheDocument()
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
