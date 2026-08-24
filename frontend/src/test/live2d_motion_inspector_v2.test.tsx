import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { Live2DMotionInspector } from "../features/teachers/presentation/live2d/Live2DMotionInspector"

describe("Live2D motion inspector v2", () => {
  it("is opt-in and exposes provenance, play, freeze/stop, and restore controls", () => {
    const onTrigger = vi.fn()
    const onStop = vi.fn()
    const onFreeze = vi.fn()
    const onRestore = vi.fn()
    const action = {
      id: "BLINK",
      label: "BLINK",
      source: "internal-parameters" as const,
      semanticRole: "blink",
      sourceLicense: "retargeted" as const,
      implementation: "internal-parameters" as const,
      requiredParameters: ["ParamEyeLOpen"],
      priority: 2,
      interruptible: true,
      cooldownMs: 0,
      returnToIdle: true,
      parameterIds: ["ParamEyeLOpen"],
    }
    const { container } = render(<Live2DMotionInspector actions={[action]} onTrigger={onTrigger} onStop={onStop} onFreeze={onFreeze} onRestore={onRestore} />)
    expect(container).toBeEmptyDOMElement()
    render(<Live2DMotionInspector enabled actions={[action]} onTrigger={onTrigger} onStop={onStop} onFreeze={onFreeze} onRestore={onRestore} />)
    expect(screen.getByTestId("live2d-motion-inspector")).toBeInTheDocument()
    const play = screen.getByRole("button", { name: "Play BLINK" })
    expect(play).toHaveAttribute("data-action-id", "BLINK")
    expect(play).toHaveAttribute("data-action-source", "retargeted")
    expect(play).toHaveAttribute("data-action-implementation", "internal-parameters")
    expect(play).toHaveAttribute("data-action-parameters", "ParamEyeLOpen")
    fireEvent.click(play)
    fireEvent.click(screen.getByRole("button", { name: "Stop" }))
    fireEvent.click(screen.getByRole("button", { name: "Freeze" }))
    fireEvent.click(screen.getByRole("button", { name: "Restore authoritative state" }))
    expect(onTrigger).toHaveBeenCalledWith("BLINK")
    expect(onStop).toHaveBeenCalledOnce()
    expect(onFreeze).toHaveBeenCalledOnce()
    expect(onRestore).toHaveBeenCalledOnce()
  })
})
