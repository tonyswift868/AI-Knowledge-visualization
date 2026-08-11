import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { VoicePlaybackRateControl } from "../features/voice/VoicePlaybackRateControl"

afterEach(() => {
  cleanup()
  localStorage.removeItem("mentorflow_voice_playback_rate_v1")
})

describe("global voice playback rate", () => {
  it("offers an accessible bounded slider and persists changes", () => {
    const view = render(<VoicePlaybackRateControl />)

    const slider = screen.getByRole("slider", { name: "朗读速度" })
    expect(slider).toHaveAttribute("min", "0.4")
    expect(slider).toHaveAttribute("max", "1.5")
    expect(slider).toHaveAttribute("step", "0.05")
    expect(view.container.querySelector(".voice-playback-rate__value")).toHaveTextContent("1×")

    fireEvent.change(slider, { target: { value: "0.65" } })
    expect(view.container.querySelector(".voice-playback-rate__value")).toHaveTextContent("0.65×")
    expect(localStorage.getItem("mentorflow_voice_playback_rate_v1")).toBe("0.65")
    expect(screen.getByRole("button", { name: "0.65×" })).toHaveAttribute("aria-pressed", "true")
  })

  it("keeps multiple visible controls synchronized", () => {
    render(<><VoicePlaybackRateControl compact /><VoicePlaybackRateControl /></>)
    const sliders = screen.getAllByRole("slider", { name: "朗读速度" })
    fireEvent.change(sliders[0], { target: { value: "1.2" } })
    expect(sliders[0]).toHaveValue("1.2")
    expect(sliders[1]).toHaveValue("1.2")
    expect(screen.getAllByText("1.2×")).toHaveLength(3)
  })
})
