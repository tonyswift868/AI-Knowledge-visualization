import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { KoikatsuCardEditor } from "../features/teachers/KoikatsuCardEditor"
import { teacherAvatarApi } from "../features/teachers/api"

describe("KKS unavailable fallback contract", () => {
  afterEach(() => vi.restoreAllMocks())

  it("returns an explicit unavailable error from the backend contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: { code: "KKS_RUNTIME_UNAVAILABLE", message: "native parser unavailable" } }),
    })
    vi.stubGlobal("fetch", fetchMock)
    await expect(teacherAvatarApi.inspectKoikatsuCard(new FormData())).rejects.toThrow("KKS_RUNTIME_UNAVAILABLE")
    await expect(teacherAvatarApi.exportKoikatsuCard(new FormData())).rejects.toThrow("KKS_RUNTIME_UNAVAILABLE")
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("keeps the card editor mounted and shows the unavailable state", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: { code: "KKS_RUNTIME_UNAVAILABLE", message: "native parser unavailable" } }),
    })
    vi.stubGlobal("fetch", fetchMock)
    render(<KoikatsuCardEditor />)
    const file = new File(["not-a-native-card"], "teacher.png", { type: "image/png" })
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("KKS_RUNTIME_UNAVAILABLE"))
    expect(screen.getByRole("heading", { level: 2 })).toBeInTheDocument()
    // The editor also warms the local-card catalog on mount; the inspected
    // file is the second request and must still surface the native error.
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
