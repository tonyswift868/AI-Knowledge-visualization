import { afterEach, describe, expect, it, vi } from "vitest"
import { teacherAvatarApi } from "../features/teachers/api"

describe("teacher avatar API", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("imports a licensed model as multipart data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ item: { model_id: "vrm-a" } }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    const body = new FormData()
    body.append("file", new File(["vrm"], "teacher.vrm"))
    body.append("rights_confirmed", "true")
    body.append("adult_character_confirmed", "true")
    await teacherAvatarApi.importModel(body)
    expect(fetchMock).toHaveBeenCalledWith("/api/teacher-avatars/import", expect.objectContaining({ method: "POST", body }))
  })

  it("loads and imports a licensed Spine teacher package", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ item: null }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ item: { teacher_id: "spine-a", format: "spine-4.1" } }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    const body = new FormData()
    body.append("file", new File(["zip"], "teacher.zip"))
    await teacherAvatarApi.activeSpineTeacher()
    await teacherAvatarApi.importSpineTeacher(body)
    expect(fetchMock.mock.calls[0]).toEqual(["/api/spine-teachers/active", undefined])
    expect(fetchMock.mock.calls[1]).toEqual(["/api/spine-teachers/import", expect.objectContaining({ method: "POST", body })])
  })

  it("imports a user-selected model collection as multipart data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], imported_count: 2, rejected: [], rejected_count: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    const body = new FormData()
    body.append("file", new File(["vrm-a"], "teacher-a.vrm"))
    body.append("file", new File(["vrm-b"], "teacher-b.vrm"))
    await teacherAvatarApi.importModelBatch(body)
    expect(fetchMock).toHaveBeenCalledWith("/api/teacher-avatars/import-batch", expect.objectContaining({ method: "POST", body }))
  })

  it("persists standardized morph controls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ item: { model_id: "vrm-a", morph: { upper_chest: 1.2 } } }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.updateMorph("vrm-a", { upper_chest: 1.2 })
    const init = fetchMock.mock.calls[0][1]
    expect(init.method).toBe("PATCH")
    expect(JSON.parse(init.body)).toEqual({ upper_chest: 1.2 })
  })

  it("loads the bundled avatar preset catalog", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{ preset_id: "avatar-sample-a" }], count: 1 }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.presets()
    expect(fetchMock).toHaveBeenCalledWith("/api/teacher-avatar-presets", undefined)
  })

  it("searches and paginates the bundled preset catalog", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], count: 0, total: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.presets({ query: "狼耳", tag: "奇幻", limit: 24, cursor: "24" })
    expect(fetchMock).toHaveBeenCalledWith("/api/teacher-avatar-presets?q=%E7%8B%BC%E8%80%B3&tag=%E5%A5%87%E5%B9%BB&limit=24&cursor=24", undefined)
  })

  it("activates a preset with adult teacher confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ item: { model_id: "vrm-preset" } }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.activatePreset("avatar-sample-a")
    const init = fetchMock.mock.calls[0][1]
    expect(fetchMock.mock.calls[0][0]).toBe("/api/teacher-avatar-presets/avatar-sample-a/activate")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body)).toEqual({ adult_character_confirmed: true })
  })

  it("searches official personal-use avatar sources", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], count: 0, total: 13 }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.personalSources("Kizuna AI")
    expect(fetchMock).toHaveBeenCalledWith("/api/teacher-avatar-personal-sources?q=Kizuna%20AI", undefined)
  })

  it("reads VRoid Hub connection status without assuming it is connected", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ provider: "VRoid Hub", status: "credentials_required" }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    const result = await teacherAvatarApi.catalogStatus()
    expect(result.status).toBe("credentials_required")
    expect(fetchMock).toHaveBeenCalledWith("/api/teacher-avatar-catalog/status", undefined)
  })

  it("searches the safe VRoid Hub catalog with opaque pagination", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], count: 0, next_page: null, usage: "teacher_safe" }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.searchCatalog({ query: "teacher", usage: "teacher_safe", page: "opaque-token" })
    expect(fetchMock).toHaveBeenCalledWith("/api/teacher-avatar-catalog/search?q=teacher&usage=teacher_safe&page=opaque-token", undefined)
  })

  it("activates an eligible Hub model with explicit confirmations", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ item: { model_id: "hub-vrm" } }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.activateCatalogModel("hub/model")
    const init = fetchMock.mock.calls[0][1]
    expect(fetchMock.mock.calls[0][0]).toBe("/api/teacher-avatar-catalog/hub%2Fmodel/activate")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body)).toEqual({ adult_character_confirmed: true, usage_conditions_confirmed: true })
  })

  it("searches and activates the verified open CC0 catalog", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], count: 0, total: 4473, collections: [], collection: "全部开放合集", snapshot_commit: "" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ item: { model_id: "open-vrm" } }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    await teacherAvatarApi.openCatalog({ query: "Grifter", collectionId: "grifters-squaddies", licenseCode: "CC0", variant: "standard", limit: 24, cursor: "24" })
    await teacherAvatarApi.activateOpenCatalog("0xabc/24", "grifters-squaddies")
    expect(fetchMock.mock.calls[0]).toEqual(["/api/teacher-avatar-open-catalog?q=Grifter&collection_id=grifters-squaddies&license_code=CC0&variant=standard&limit=24&cursor=24", undefined])
    expect(fetchMock.mock.calls[1][0]).toBe("/api/teacher-avatar-open-catalog/0xabc%2F24/activate")
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ collection_id: "grifters-squaddies", adult_character_confirmed: true, attribution_confirmed: true })
  })
})
