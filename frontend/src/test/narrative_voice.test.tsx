import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const voiceMocks = vi.hoisted(() => ({
  speak: vi.fn(async (_text: string, _options?: {
    onStart?: () => void
    onPreparing?: (phase: "starting" | "synthesizing") => void
  }) => ({ ok: true })),
  stop: vi.fn(),
}))

vi.mock("../features/voice/localTeacherVoice", () => ({
  speakLocalTeacherVoice: voiceMocks.speak,
  stopLocalTeacherVoice: voiceMocks.stop,
}))

vi.mock("../features/voice/VoicePackSettings", () => ({ VoicePackSettings: () => null }))

import { CampaignView } from "../App"
import { useNarrativeStore } from "../stores/useNarrativeStore"

afterEach(() => {
  cleanup()
  voiceMocks.speak.mockReset()
  voiceMocks.speak.mockResolvedValue({ ok: true })
  voiceMocks.stop.mockClear()
  useNarrativeStore.getState().reset()
})

function campaignWithLine(lineText: string) {
  return {
    campaign_id: "campaign_voice_text_first",
    current_scene_id: "scene_voice_text_first",
    current_story_stage: "opening",
    revision: 1,
    story_pack: { story_pack_id: "voice_text_first", language: "zh-CN" },
    state: {},
    current_scene: {
      scene_id: "scene_voice_text_first",
      story_order: 1,
      lines_complete: false,
      choices: [],
      current_line: { speaker_id: "iris", text: lineText },
      characters: [{
        character_id: "iris",
        display_name: "艾莉丝",
        public_identity: "AI 教师",
        personality_rules: [],
      }],
    },
  }
}

describe("narrative voice", () => {
  it("keeps listen disabled until the current line is fully visible", async () => {
    const lineText = "文字先完整显示，然后才允许点击听语音。"
    const campaign = campaignWithLine(lineText)

    render(
      <CampaignView
        workspace={{ id: "ws_voice_text_first" }}
        campaign={campaign}
        currentTask={null}
        onOpenLearn={vi.fn()}
        onRefresh={vi.fn(async () => undefined)}
      />,
    )

    const waitingButton = screen.getByRole("button", { name: "听语音朗读台词" })
    expect(waitingButton).toBeDisabled()
    expect(waitingButton).toHaveTextContent("文字显示中…")
    fireEvent.click(waitingButton)
    expect(voiceMocks.speak).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "跳过已读" }))
    expect(screen.getByText(lineText, { exact: false })).toBeInTheDocument()
    const listenButton = await screen.findByRole("button", { name: "听语音朗读台词" })
    expect(listenButton).toBeEnabled()
    expect(listenButton).toHaveTextContent("听语音")

    fireEvent.click(listenButton)

    await waitFor(() => expect(voiceMocks.speak).toHaveBeenCalledWith(
      lineText,
      expect.objectContaining({ style: "lively" }),
    ))
  })

  it("stops narrative playback when the pressed listen control is clicked again", async () => {
    const lineText = "正在播放的台词再次点击就停止。"
    voiceMocks.speak.mockImplementation(async (_text, options) => {
      options?.onStart?.()
      return { ok: true }
    })
    render(
      <CampaignView
        workspace={{ id: "ws_voice_toggle" }}
        campaign={campaignWithLine(lineText)}
        currentTask={null}
        onOpenLearn={vi.fn()}
        onRefresh={vi.fn(async () => undefined)}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "跳过已读" }))
    voiceMocks.stop.mockClear()
    fireEvent.click(screen.getByRole("button", { name: "听语音朗读台词" }))

    const stopButton = await screen.findByRole("button", { name: "停止朗读台词" })
    expect(stopButton).toHaveAttribute("aria-pressed", "true")
    expect(stopButton).toHaveTextContent("停止朗读")

    fireEvent.click(stopButton)

    await waitFor(() => expect(voiceMocks.stop).toHaveBeenCalledTimes(1))
    expect(screen.getByRole("button", { name: "听语音朗读台词" })).toHaveAttribute("aria-pressed", "false")
  })

  it("shows and can cancel a cold local-model startup", async () => {
    const lineText = "首次点击才启动本地模型。"
    let resolveSpeech!: (result: { ok: boolean; reason?: string }) => void
    voiceMocks.speak.mockImplementation((_text, options) => new Promise(resolve => {
      resolveSpeech = resolve
      options?.onPreparing?.("starting")
    }))
    render(
      <CampaignView
        workspace={{ id: "ws_voice_cold_start" }}
        campaign={campaignWithLine(lineText)}
        currentTask={null}
        onOpenLearn={vi.fn()}
        onRefresh={vi.fn(async () => undefined)}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "跳过已读" }))
    voiceMocks.stop.mockClear()
    fireEvent.click(screen.getByRole("button", { name: "听语音朗读台词" }))

    const cancelButton = await screen.findByRole("button", { name: "停止朗读台词" })
    expect(cancelButton).toHaveTextContent("取消模型启动")
    expect(cancelButton).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByText("正在准备教师语音")).toBeInTheDocument()

    fireEvent.click(cancelButton)
    expect(voiceMocks.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole("button", { name: "听语音朗读台词" })).toHaveTextContent("听语音")
    resolveSpeech({ ok: false, reason: "cancelled" })
  })
})
