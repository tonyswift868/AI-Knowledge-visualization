import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { KoikatsuCardEditor } from "../features/teachers/KoikatsuCardEditor"
import { teacherAvatarApi } from "../features/teachers/api"

describe("KKS unavailable fallback contract", () => {
  afterEach(() => vi.restoreAllMocks())

  it("returns an explicit unavailable error without issuing a request", async () => {
    await expect(teacherAvatarApi.inspectKoikatsuCard(new FormData())).rejects.toThrow("KKS_RUNTIME_UNAVAILABLE")
    await expect(teacherAvatarApi.exportKoikatsuCard(new FormData())).rejects.toThrow("KKS_RUNTIME_UNAVAILABLE")
  })

  it("keeps the card editor mounted and shows the unavailable state", async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
    render(<KoikatsuCardEditor />)
    const file = new File(["not-a-native-card"], "teacher.png", { type: "image/png" })
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("KKS_RUNTIME_UNAVAILABLE"))
    expect(screen.getByRole("heading", { level: 2 })).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
