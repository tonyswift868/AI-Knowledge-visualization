import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { VoicePackSettings } from "../features/voice/VoicePackSettings"

type PackState = "not_installed" | "installed" | "active" | "failed"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function pack(state: PackState) {
  return versionedPack(state, "1.0.0")
}

function versionedPack(state: PackState, version: string) {
  return {
    pack_id: "mentorflow-original-iris",
    version,
    display_name: "艾莉丝原创教师声包",
    state,
    archive_bytes: 335_544_320,
    default: true,
    styles: ["lively", "relaxed"],
    runtime_adapter: "qwen3_tts",
  }
}

function status(state: PackState, fallbackPolicy: "off" | "system_voice") {
  return {
    schema: "mentorflow.teacher_voice.status.v1",
    fallback_policy: fallbackPolicy,
    active_pack: state === "active" ? pack("active") : null,
    runtime: { state: state === "active" ? "ready" : "stopped", starts_model: false },
    packs: [pack(state)],
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("offline teacher voice pack settings", () => {
  it("installs and activates an original offline preset without exposing a port or API", async () => {
    let packState: PackState = "not_installed"
    let fallbackPolicy: "off" | "system_voice" = "off"
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) return jsonResponse(status(packState, fallbackPolicy))
      if (url.endsWith("/api/teacher-voice/packs") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ packs: [pack(packState)] })
      }
      if (url.endsWith("/api/teacher-voice/settings") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ fallback_policy: fallbackPolicy })
      }
      if (url.endsWith("/api/teacher-voice/packs/mentorflow-original-iris/install") && init?.method === "POST") {
        packState = packState === "active" ? "active" : "installed"
        return jsonResponse({
          job: {
            job_id: `job-install-${packState}`,
            action: "install",
            pack_id: "mentorflow-original-iris",
            version: "1.0.0",
            status: "installed",
            history: [
              { status: "staging", at: "2026-08-07T01:00:00Z" },
              { status: "verified", at: "2026-08-07T01:00:01Z" },
              { status: "installed", at: "2026-08-07T01:00:02Z" },
            ],
            error: null,
          },
          pack: pack(packState),
        })
      }
      if (url.endsWith("/api/teacher-voice/packs/mentorflow-original-iris/activate") && init?.method === "POST") {
        packState = "active"
        return jsonResponse({
          job: {
            job_id: "job-activate",
            action: "activate",
            pack_id: "mentorflow-original-iris",
            version: "1.0.0",
            status: "active",
            history: [{ status: "active", at: "2026-08-07T01:00:03Z" }],
            error: null,
          },
          pack: pack("active"),
        })
      }
      throw new Error(`unexpected request: ${url} ${init?.method || "GET"}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)

    await screen.findByText("艾莉丝原创教师声包")
    expect(screen.getByText("原创声线")).toBeInTheDocument()
    expect(screen.getByText("完全离线")).toBeInTheDocument()
    expect(screen.getByText("版本 1.0.0")).toBeInTheDocument()
    expect(screen.getByText("320 MB")).toBeInTheDocument()
    expect(screen.queryByText(/小晓/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/8892|127\.0\.0\.1|API Key/i)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /下载并使用/ }))
    await screen.findByText("声包已安装并设为默认；现在点击“听语音”即可离线朗读。")
    await screen.findByText("正在使用")
    expect(screen.getByTestId("voice-pack-state")).toHaveTextContent("可用")
    expect(screen.queryByRole("button", { name: /检查更新|更新声包/ })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/install"))).toHaveLength(1)
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/activate"))).toHaveLength(1)
  })

  it("treats an installed pack as pending activation and never offers a duplicate install", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse(status("installed", "off"))
      if (url.endsWith("/packs")) return jsonResponse({ packs: [pack("installed")] })
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)

    await screen.findByRole("button", { name: /设为默认/ })
    expect(screen.getByTestId("voice-pack-state")).toHaveTextContent("已安装 · 可切换")
    expect(screen.queryByRole("button", { name: /下载并使用|检查更新|更新声包/ })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/install"))).toBe(false)
  })

  it("labels a private prerelease pack as local-only and non-exportable", async () => {
    const privatePack = {
      ...pack("not_installed"),
      release_channel: "prerelease",
      usage_scope: "local_private",
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse({
        ...status("not_installed", "off"),
        packs: [privatePack],
      })
      if (url.endsWith("/packs")) return jsonResponse({ packs: [privatePack] })
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)

    expect(await screen.findByText("预发布")).toBeInTheDocument()
    expect(screen.getByText("仅本机私用 · 不可导出")).toBeInTheDocument()
  })

  it("does not claim a default pack when activation fails after installation", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse(status("not_installed", "off"))
      if (url.endsWith("/packs") && (!init?.method || init.method === "GET")) {
        return jsonResponse({ packs: [pack("not_installed")] })
      }
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      if (url.endsWith("/install") && init?.method === "POST") return jsonResponse({
        job: {
          job_id: "job-installed-before-activation-error",
          action: "install",
          pack_id: "mentorflow-original-iris",
          version: "1.0.0",
          status: "installed",
          history: [{ status: "installed", at: "2026-08-08T01:00:00Z" }],
          error: null,
        },
        pack: pack("installed"),
      })
      if (url.endsWith("/activate") && init?.method === "POST") return jsonResponse({
        error: { code: "VOICE_PACK_ACTIVATE_FAILED", message: "本机运行时校验失败" },
      }, 503)
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)
    fireEvent.click(await screen.findByRole("button", { name: /下载并使用/ }))

    const alert = (await screen.findByText(/声包已安全安装，但未能设为默认/)).closest("[role='alert']")
    expect(alert).not.toBeNull()
    expect(alert).toHaveTextContent("声包已安全安装，但未能设为默认")
    expect(alert).toHaveTextContent("本机运行时校验失败")
    expect(alert).toHaveTextContent("请点击“设为默认”重试")
    expect(screen.queryByText(/已安装并设为默认/)).not.toBeInTheDocument()
  })

  it("marks exactly one matching pack id and version active when multiple versions exist", async () => {
    const activeV1 = versionedPack("active", "1.0.0")
    const installedV2 = versionedPack("installed", "2.0.0")
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/status")) {
        return jsonResponse({
          schema: "mentorflow.teacher_voice.status.v1",
          fallback_policy: "off",
          active_pack: activeV1,
          runtime: { state: "ready", starts_model: false },
          packs: [activeV1, installedV2],
        })
      }
      if (url.endsWith("/packs")) return jsonResponse({ packs: [activeV1, installedV2] })
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)

    const v1 = await screen.findByTestId("voice-pack-mentorflow-original-iris-1.0.0")
    const v2 = screen.getByTestId("voice-pack-mentorflow-original-iris-2.0.0")
    expect(screen.getAllByText("正在使用")).toHaveLength(1)
    expect(within(v1).getByText("正在使用")).toBeInTheDocument()
    expect(within(v1).queryByRole("button", { name: /设为默认|删除/ })).not.toBeInTheDocument()
    expect(within(v2).queryByText("正在使用")).not.toBeInTheDocument()
    expect(within(v2).getByText("已安装 · 可切换")).toBeInTheDocument()
    expect(within(v2).getByRole("button", { name: /设为默认/ })).toBeInTheDocument()
    expect(within(v2).queryByRole("button", { name: /下载并使用|检查更新|更新声包/ })).not.toBeInTheDocument()
  })

  it("persists system voice fallback only after the learner explicitly checks it", async () => {
    let fallbackPolicy: "off" | "system_voice" = "off"
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse(status("not_installed", fallbackPolicy))
      if (url.endsWith("/packs")) return jsonResponse({ packs: [pack("not_installed")] })
      if (url.endsWith("/settings") && init?.method === "PATCH") {
        fallbackPolicy = JSON.parse(String(init.body)).fallback_policy
        return jsonResponse({ fallback_policy: fallbackPolicy })
      }
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: fallbackPolicy })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)
    const fallback = await screen.findByTestId("voice-system-fallback")
    expect(fallback).not.toBeChecked()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false)

    fireEvent.click(fallback)

    await screen.findByText("已明确开启系统语音兼容回退。")
    expect(fallback).toBeChecked()
    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH")
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ fallback_policy: "system_voice" })
  })

  it("asks for confirmation before deleting an inactive installed version", async () => {
    let packState: PackState = "installed"
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse(status(packState, "off"))
      if (url.endsWith("/packs")) return jsonResponse({ packs: [pack(packState)] })
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      if (url.endsWith("/packs/mentorflow-original-iris/versions/1.0.0") && init?.method === "DELETE") {
        packState = "not_installed"
        return jsonResponse({ pack_id: "mentorflow-original-iris", version: "1.0.0", status: "not_installed" })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true)

    render(<VoicePackSettings />)
    const deleteButton = await screen.findByRole("button", { name: "删除 艾莉丝原创教师声包" })
    fireEvent.click(deleteButton)
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false)

    fireEvent.click(deleteButton)
    await screen.findByText("声包已从本机删除，文字回复不受影响。")
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/packs/mentorflow-original-iris/versions/1.0.0")
      && init?.method === "DELETE",
    )).toBe(true)
  })

  it("shows installation and verification transitions while polling the centralized job endpoint", async () => {
    let packState: PackState = "not_installed"
    let jobPolls = 0
    const job = (jobStatus: string) => ({
      job_id: "job-progress",
      action: "install",
      pack_id: "mentorflow-original-iris",
      version: "1.0.0",
      status: jobStatus,
      history: [{ status: jobStatus, at: "2026-08-07T01:00:00Z" }],
      error: null,
    })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse(status(packState, "off"))
      if (url.endsWith("/packs") && (!init?.method || init.method === "GET")) return jsonResponse({ packs: [pack(packState)] })
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      if (url.endsWith("/install")) return jsonResponse({ job: job("staging"), pack: pack("not_installed") })
      if (url.endsWith("/jobs/job-progress")) {
        jobPolls += 1
        if (jobPolls === 1) return jsonResponse(job("verified"))
        packState = "installed"
        return jsonResponse(job("installed"))
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)
    fireEvent.click(await screen.findByRole("button", { name: /下载并使用/ }))

    await waitFor(() => expect(screen.getByTestId("voice-pack-state")).toHaveTextContent("安装中"))
    await waitFor(() => expect(screen.getByTestId("voice-pack-state")).toHaveTextContent("校验中"), { timeout: 3000 })
    await waitFor(() => expect(screen.getByTestId("voice-pack-state")).toHaveTextContent("已安装 · 可切换"), { timeout: 5000 })
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/jobs/job-progress"))).toBe(true)
  })

  it("retries a bounded polling network error and then accepts a successful job result", async () => {
    let packState: PackState = "not_installed"
    let jobCalls = 0
    const stagingJob = {
      job_id: "job-network-retry",
      action: "install",
      pack_id: "mentorflow-original-iris",
      version: "1.0.0",
      status: "staging",
      history: [{ status: "staging", at: "2026-08-07T01:00:00Z" }],
      error: null,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse(status(packState, "off"))
      if (url.endsWith("/packs") && (!init?.method || init.method === "GET")) return jsonResponse({ packs: [pack(packState)] })
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      if (url.endsWith("/install")) return jsonResponse({ job: stagingJob, pack: pack("not_installed") })
      if (url.endsWith("/jobs/job-network-retry")) {
        jobCalls += 1
        if (jobCalls === 1) throw new Error("temporary network error")
        packState = "installed"
        return jsonResponse({ ...stagingJob, status: "installed" })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)
    const installButton = await screen.findByRole("button", { name: /下载并使用/ })
    vi.useFakeTimers()
    fireEvent.click(installButton)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    await act(async () => { await vi.advanceTimersByTimeAsync(1200) })
    expect(jobCalls).toBe(1)
    expect(screen.getByRole("alert")).toHaveTextContent("temporary network error，正在自动重试")

    await act(async () => { await vi.advanceTimersByTimeAsync(2400) })
    expect(jobCalls).toBe(2)
    expect(screen.getByTestId("voice-pack-state")).toHaveTextContent("已安装 · 可切换")
    expect(screen.queryByText(/temporary network error/)).not.toBeInTheDocument()
  })

  it("keeps exactly one job request in flight while a slow poll is unresolved", async () => {
    let packState: PackState = "not_installed"
    let jobCalls = 0
    let activeJobCalls = 0
    let maxActiveJobCalls = 0
    const stagingJob = {
      job_id: "job-serial-poll",
      action: "install",
      pack_id: "mentorflow-original-iris",
      version: "1.0.0",
      status: "staging",
      history: [{ status: "staging", at: "2026-08-07T01:00:00Z" }],
      error: null,
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      if (url.endsWith("/status")) return Promise.resolve(jsonResponse(status(packState, "off")))
      if (url.endsWith("/packs") && (!init?.method || init.method === "GET")) return Promise.resolve(jsonResponse({ packs: [pack(packState)] }))
      if (url.endsWith("/settings")) return Promise.resolve(jsonResponse({ fallback_policy: "off" }))
      if (url.endsWith("/install")) return Promise.resolve(jsonResponse({ job: stagingJob, pack: pack("not_installed") }))
      if (url.endsWith("/jobs/job-serial-poll")) {
        jobCalls += 1
        activeJobCalls += 1
        maxActiveJobCalls = Math.max(maxActiveJobCalls, activeJobCalls)
        return new Promise(resolve => {
          window.setTimeout(() => {
            activeJobCalls -= 1
            packState = "installed"
            resolve(jsonResponse({ ...stagingJob, status: "installed" }))
          }, 2500)
        })
      }
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)
    const installButton = await screen.findByRole("button", { name: /下载并使用/ })
    vi.useFakeTimers()
    fireEvent.click(installButton)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    await act(async () => { await vi.advanceTimersByTimeAsync(3600) })
    expect(jobCalls).toBe(1)
    expect(activeJobCalls).toBe(1)
    expect(maxActiveJobCalls).toBe(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
    expect(activeJobCalls).toBe(0)
    expect(jobCalls).toBe(1)
    expect(screen.getByTestId("voice-pack-state")).toHaveTextContent("已安装 · 可切换")
  })

  it("aborts never-resolving job requests and reaches an overall terminal timeout", async () => {
    const polledSignals: AbortSignal[] = []
    const stagingJob = {
      job_id: "job-never-resolves",
      action: "install",
      pack_id: "mentorflow-original-iris",
      version: "1.0.0",
      status: "staging",
      history: [{ status: "staging", at: "2026-08-07T01:00:00Z" }],
      error: null,
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      if (url.endsWith("/status")) return Promise.resolve(jsonResponse(status("not_installed", "off")))
      if (url.endsWith("/packs") && (!init?.method || init.method === "GET")) return Promise.resolve(jsonResponse({ packs: [pack("not_installed")] }))
      if (url.endsWith("/settings")) return Promise.resolve(jsonResponse({ fallback_policy: "off" }))
      if (url.endsWith("/install")) return Promise.resolve(jsonResponse({ job: stagingJob, pack: pack("not_installed") }))
      if (url.endsWith("/jobs/job-never-resolves")) {
        const signal = init?.signal
        if (signal) polledSignals.push(signal)
        return new Promise((_resolve, reject) => {
          const abort = () => reject(new DOMException("aborted", "AbortError"))
          if (signal?.aborted) abort()
          else signal?.addEventListener("abort", abort, { once: true })
        })
      }
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)
    const installButton = await screen.findByRole("button", { name: /下载并使用/ })
    vi.useFakeTimers()
    fireEvent.click(installButton)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    await act(async () => { await vi.advanceTimersByTimeAsync(120_001) })

    expect(polledSignals.length).toBeGreaterThan(0)
    expect(polledSignals.every(signal => signal.aborted)).toBe(true)
    expect(screen.getByTestId("voice-pack-job")).toHaveTextContent("任务失败")
    expect(screen.getByTestId("voice-pack-job")).toHaveTextContent("声包任务状态更新超时")
    expect(vi.getTimerCount()).toBe(0)
  })

  it("aborts an in-flight poll on unmount without scheduling another request", async () => {
    const pollSignals: AbortSignal[] = []
    let jobCalls = 0
    const stagingJob = {
      job_id: "job-unmount",
      action: "install",
      pack_id: "mentorflow-original-iris",
      version: "1.0.0",
      status: "staging",
      history: [{ status: "staging", at: "2026-08-07T01:00:00Z" }],
      error: null,
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      if (url.endsWith("/status")) return Promise.resolve(jsonResponse(status("not_installed", "off")))
      if (url.endsWith("/packs") && (!init?.method || init.method === "GET")) return Promise.resolve(jsonResponse({ packs: [pack("not_installed")] }))
      if (url.endsWith("/settings")) return Promise.resolve(jsonResponse({ fallback_policy: "off" }))
      if (url.endsWith("/install")) return Promise.resolve(jsonResponse({ job: stagingJob, pack: pack("not_installed") }))
      if (url.endsWith("/jobs/job-unmount")) {
        jobCalls += 1
        const activeSignal = init?.signal
        if (activeSignal) pollSignals.push(activeSignal)
        return new Promise((_resolve, reject) => {
          const abort = () => reject(new DOMException("aborted", "AbortError"))
          if (activeSignal?.aborted) abort()
          else activeSignal?.addEventListener("abort", abort, { once: true })
        })
      }
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })
    vi.stubGlobal("fetch", fetchMock)

    const view = render(<VoicePackSettings />)
    const installButton = await screen.findByRole("button", { name: /下载并使用/ })
    vi.useFakeTimers()
    fireEvent.click(installButton)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(1200) })
    expect(jobCalls).toBe(1)

    view.unmount()
    expect(pollSignals).toHaveLength(1)
    expect(pollSignals[0].aborted).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(120_001) })
    expect(jobCalls).toBe(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("shows a structured backend failure with a repair and retry instruction", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/status")) return jsonResponse(status("failed", "off"))
      if (url.endsWith("/packs") && init?.method !== "POST") return jsonResponse({ packs: [pack("failed")] })
      if (url.endsWith("/settings")) return jsonResponse({ fallback_policy: "off" })
      if (url.endsWith("/install")) {
        return jsonResponse({
          error: {
            code: "VOICE_PACK_ARCHIVE_INVALID",
            message: "声包完整性校验失败",
            details: {},
            request_id: "req_voice_fail",
          },
        }, 422)
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<VoicePackSettings />)
    fireEvent.click(await screen.findByRole("button", { name: /重试安装/ }))

    const alert = await screen.findByText(/声包完整性校验失败/)
    expect(alert).toHaveTextContent("声包完整性校验失败")
    expect(alert).toHaveTextContent(/修复或重试声包/)
  })
})
