import { useCallback, useEffect, useMemo, useState } from "react"
import { Check, ExternalLink, FileDown, RotateCcw, Upload } from "lucide-react"
import { teacherAvatarApi, type KksCharacterPack, type SpineTeacher, type TeacherAvatar, type TeacherAvatarCardPreset, type TeacherAvatarMorph } from "./api"
import { KoikatsuCardEditor } from "./KoikatsuCardEditor"
import { ParametricCardPreview } from "./ParametricCardPreview"
import { TeacherCompanionPortrait, TeacherCompanionStage } from "./TeacherCompanionStage"
import type { TeacherAnimationState } from "./SpineTeacherPlayer"
import { friendlyKksDisplayName } from "./kksDisplayNames"
import "./teacher-avatar.css"

// `blink` is an eye-open amount in the PMX rig (1 = open, 0 = closed).
// Keeping the default at zero made a freshly reset teacher look half-asleep
// even though the imported Koikatsu card itself is neutral/open-eyed.
const defaultMorph: TeacherAvatarMorph = { height: 1, head_scale: 1, upper_chest: 1, shoulder_width: 1, waist_width: 1, hip_width: 1, arm_length: 1, leg_length: 1, smile: .18, blink: 1 }
function normalizeMorph(value?: Partial<TeacherAvatarMorph>): TeacherAvatarMorph {
  const merged = { ...defaultMorph, ...(value || {}) }
  return {
    ...merged,
    height: Number.isFinite(merged.height) ? merged.height : defaultMorph.height,
    head_scale: Number.isFinite(merged.head_scale) ? merged.head_scale : defaultMorph.head_scale,
    upper_chest: Number.isFinite(merged.upper_chest) ? merged.upper_chest : defaultMorph.upper_chest,
    shoulder_width: Number.isFinite(merged.shoulder_width) ? merged.shoulder_width : defaultMorph.shoulder_width,
    waist_width: Number.isFinite(merged.waist_width) ? merged.waist_width : defaultMorph.waist_width,
    hip_width: Number.isFinite(merged.hip_width) ? merged.hip_width : defaultMorph.hip_width,
    arm_length: Number.isFinite(merged.arm_length) ? merged.arm_length : defaultMorph.arm_length,
    leg_length: Number.isFinite(merged.leg_length) ? merged.leg_length : defaultMorph.leg_length,
    smile: Number.isFinite(merged.smile) ? merged.smile : defaultMorph.smile,
    blink: Number.isFinite(merged.blink) ? merged.blink : defaultMorph.blink,
  }
}

const sourceLinks = [
  { label: "VRoid Hub", url: "https://hub.vroid.com/en/", note: "浏览作者发布的 VRM，并按作者条款下载" },
]

export function resolveSpineTeacherManifestUrl(search = window.location.search) {
  const configured = import.meta.env.VITE_SPINE_TEACHER_MANIFEST_URL
  if (configured) return configured
  const preview = new URLSearchParams(search).get("spineManifest")
  return preview?.startsWith("/assets/teachers/spine/") ? preview : undefined
}

export function TeacherAvatarStudio({
  workspaceId = "default",
  compact = false,
  presentationState = "idle",
}: {
  workspaceId?: string
  compact?: boolean
  presentationState?: TeacherAnimationState
}) {
  const [items, setItems] = useState<TeacherAvatar[]>([])
  const [characterPacks, setCharacterPacks] = useState<KksCharacterPack[]>([])
  const [cardPresets, setCardPresets] = useState<TeacherAvatarCardPreset[]>([])
  const [activeId, setActiveId] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState(false)
  const [morph, setMorph] = useState<TeacherAvatarMorph>(defaultMorph)
  const [koikatsuModelUrlOverride, setKoikatsuModelUrlOverride] = useState<string | undefined>()
  const [koikatsuCardPreviewUrl, setKoikatsuCardPreviewUrl] = useState<string | undefined>()
  const [koikatsuNativeCardActive, setKoikatsuNativeCardActive] = useState(false)
  const [previewZoom, setPreviewZoom] = useState(1)
  const configuredSpineManifestUrl = useMemo(() => resolveSpineTeacherManifestUrl(), [])
  const [spineTeacher, setSpineTeacher] = useState<SpineTeacher | null>(null)
  const [spineNotice, setSpineNotice] = useState("")
  // Prefer a native Koikatsu/KKBP export when one is available.  PMX keeps
  // the game's actual body and skeleton instead of falling back to the
  // generic VRM rig that caused the old bone distortions.
  const active = useMemo(() => items.find(item => item.model_id === activeId) || items[0] || null, [activeId, items])
  const koikatsuBase = useMemo(() => items.find(item => item.model_id === activeId && item.format === "pmx") || items.find(item => item.format === "pmx") || active, [active, activeId, items])
  const cardPreview = koikatsuCardPreviewUrl || "/api/koikatsu-cards/default"
  const characterPackGroups = useMemo(() => {
    const groups = new Map<string, KksCharacterPack[]>()
    for (const pack of characterPacks) {
      const key = pack.category_path || pack.category || "未分类"
      groups.set(key, [...(groups.get(key) || []), pack])
    }
    return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right, "zh-CN"))
  }, [characterPacks])

  const load = useCallback(async () => {
    try {
      const [list, today] = await Promise.all([teacherAvatarApi.list(), teacherAvatarApi.today(workspaceId)])
      // Runtime packs are card-matched native assets, not standalone roster
      // models. Keep them available to the card editor's matching API without
      // rendering a broken blank entry in the avatar switcher.
      const visibleItems = list.items.filter(item => item.asset_profile !== "kks-runtime-pack")
      setItems(visibleItems)
      try {
        const cards = await teacherAvatarApi.cardPresets()
        setCardPresets(cards.items)
      } catch {
        // Card presets are an optional local roster; an older backend should
        // not prevent the normal VRM roster and preview from loading.
        setCardPresets([])
      }
      try {
        const packs = await teacherAvatarApi.characterPacks()
        setCharacterPacks(packs.items)
      } catch {
        setCharacterPacks([])
      }
      try {
        const spine = await teacherAvatarApi.activeSpineTeacher()
        setSpineTeacher(spine.item)
      } catch {
        setSpineTeacher(null)
      }
      const selected = today.item && today.item.asset_profile !== "kks-runtime-pack" ? today.item : visibleItems[0]
      if (selected) {
        setActiveId(selected.model_id)
        setMorph(normalizeMorph(selected.morph))
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "教师模型加载失败")
    }
  }, [workspaceId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { if (active) setMorph(normalizeMorph(active.morph)) }, [active?.model_id])

  if (compact) {
    return (
      <section className="teacher-avatar-studio compact">
        <TeacherCompanionPortrait
          teacherName={spineTeacher?.display_name || "艾莉丝"}
          spineManifestUrl={configuredSpineManifestUrl || spineTeacher?.manifest_url}
          state={presentationState}
        />
      </section>
    )
  }

  async function importModel(form: HTMLFormElement) {
    const body = new FormData(form)
    // The visible form intentionally stays focused on the fields users can
    // reasonably provide. Keep the registry's provenance contract satisfied
    // with explicit local-import defaults when those fields are omitted.
    if (!String(body.get("source_url") || "").trim()) body.set("source_url", "local://user-import")
    if (!String(body.get("license_name") || "").trim()) body.set("license_name", "用户已确认授权")
    try {
      setBusy(true)
      setNotice("")
      const selected = body.getAll("file").filter((value): value is File => value instanceof File && value.size > 0)
      if (selected.length > 1) {
        const result = await teacherAvatarApi.importModelBatch(body)
        await load()
        if (result.items[0] && result.items[0].asset_profile !== "kks-runtime-pack") setActiveId(result.items[0].model_id)
        setNotice(result.rejected_count ? `已导入 ${result.imported_count} 个模型；${result.rejected_count} 个文件未通过校验` : `已导入 ${result.imported_count} 个模型`)
      } else {
        const result = await teacherAvatarApi.importModel(body)
        await load()
        if (result.item.asset_profile !== "kks-runtime-pack") setActiveId(result.item.model_id)
        setNotice("模型已导入，可在下方轮换池中选择")
      }
      form.reset()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "模型导入失败")
    } finally {
      setBusy(false)
    }
  }

  async function importSpineTeacher(form: HTMLFormElement) {
    try {
      setBusy(true)
      setSpineNotice("")
      const result = await teacherAvatarApi.importSpineTeacher(new FormData(form))
      setSpineTeacher(result.item)
      setSpineNotice("Spine 4.1 教师资源已验证并启用。")
      form.reset()
    } catch (error) {
      setSpineNotice(error instanceof Error ? error.message : "Spine 教师资源导入失败")
    } finally {
      setBusy(false)
    }
  }

  async function saveMorph() {
    if (!active) return
    try {
      setBusy(true)
      const result = await teacherAvatarApi.updateMorph(active.model_id, morph)
      setItems(value => value.map(item => item.model_id === active.model_id ? result.item : item))
      setNotice("身体比例与表情参数已保存")
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "参数保存失败")
    } finally {
      setBusy(false)
    }
  }

  function applyCardPreset(card: TeacherAvatarCardPreset) {
    setActiveId(card.body_template_id)
    setMorph(normalizeMorph(card.morph))
    setNotice(card.missing_dependencies.length ? `${card.display_name} 已载入；${card.missing_dependencies.length} 个卡片明确引用的本地资源待解析` : `${card.display_name} 已载入统一身体模板`)
  }

  async function importCard(form: HTMLFormElement) {
    const body = new FormData(form)
    try {
      setBusy(true)
      setNotice("")
      const result = await teacherAvatarApi.validateCard(body)
      const card = result.card
      setActiveId(card.resolved_template.model_id)
      setMorph(normalizeMorph(card.morph))
      if (card.source_format === "koikatsu_sunshine") {
        setNotice(card.missing_dependencies.length
          ? `已读取恋活 Sunshine 当前服装坐标；${card.missing_dependencies.length} 个卡片明确引用的资源仍需解析，空的可选槽位不会补件。`
          : "已读取恋活 Sunshine 当前服装坐标；空的可选槽位按卡片原样保留。")
      } else {
        setNotice(card.missing_dependencies.length ? `角色卡已读取；${card.missing_dependencies.length} 个明确引用的资源待解析` : "角色卡已读取，已套用统一身体模板参数")
      }
      form.reset()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "角色卡导入失败")
    } finally {
      setBusy(false)
    }
  }

  return <section className={`teacher-avatar-studio ${compact ? "compact" : ""}`}>
    <div className="teacher-avatar-heading">
      <div><span>{compact ? "LICENSED AVATAR ROSTER" : "2D AI TEACHER COMPANION"}</span><h2>{compact ? "今日教师模型" : "AI 教师角色库"}</h2><p>{compact ? "当前教师的课程展示模型。" : "以高质量 2D 角色、教学状态和自然互动构成教师陪伴体验。"}</p></div>
      <a href="https://github.com/EsotericSoftware/spine-runtimes" target="_blank" rel="noreferrer">查看 Spine 运行时 <ExternalLink size={14} /></a>
    </div>

      {!compact && <TeacherCompanionStage teacherName={spineTeacher?.display_name || "艾莉丝"} spineManifestUrl={configuredSpineManifestUrl || spineTeacher?.manifest_url} />}

      {!compact && <form className="spine-teacher-import" onSubmit={event => { event.preventDefault(); void importSpineTeacher(event.currentTarget) }}>
        <header>
          <div><span>SPINE 4.1 PACKAGE</span><h3>{spineTeacher ? `当前骨骼教师：${spineTeacher.display_name}` : "导入授权的 2D 骨骼教师"}</h3></div>
          <b>{spineTeacher ? "已启用" : "等待资源包"}</b>
        </header>
        <p>ZIP 需要包含 teacher.manifest.json、骨骼、atlas 和贴图；播放器、版本匹配与安全校验由系统处理。</p>
        <div className="spine-teacher-import-fields">
          <input type="file" name="file" accept=".zip" required />
          <input name="display_name" placeholder="教师显示名" required />
          <input name="creator" placeholder="角色 / 骨骼作者" required />
        </div>
        <div className="spine-teacher-confirmations">
          <label><input type="checkbox" name="spine_editor_license_confirmed" value="true" required />已确认有效的 Spine Editor 授权</label>
          <label><input type="checkbox" name="character_rights_confirmed" value="true" required />已确认角色和动作资源使用权</label>
          <label><input type="checkbox" name="adult_character_confirmed" value="true" required />角色设定为成年人</label>
        </div>
        <button type="submit" disabled={busy}>{busy ? "正在验证…" : "验证并启用 Spine 教师"}</button>
        {spineNotice && <div className="teacher-avatar-notice" role="status">{spineNotice}</div>}
      </form>}

      {!compact && <details className="teacher-legacy-tools">
        <summary>旧版 3D / 恋活导入工具</summary>
        <p>仅用于迁移已有角色卡。当前 AI 教师展示默认使用 2D 角色舞台。</p>
        <KoikatsuCardEditor modelUrl={koikatsuModelUrlOverride !== undefined ? koikatsuModelUrlOverride : (koikatsuBase?.model_url || undefined)} modelFormat={koikatsuBase?.format === "runtime-pack" ? undefined : koikatsuBase?.format} previewMorph={morph} onMorphChange={setMorph} onNativeCardChange={setKoikatsuNativeCardActive} onModelUrlChange={setKoikatsuModelUrlOverride} onPreviewChange={setKoikatsuCardPreviewUrl} show3dPreview={false} />
      </details>}

    {!compact && <section className="teacher-avatar-sources" aria-label="模型来源网站">
      <div className="teacher-avatar-sources-heading"><div><b>自行获取模型</b><span>打开来源页下载 VRM/GLB，确认作者许可后再导入。</span></div><FileDown size={20} /></div>
      <div className="teacher-avatar-source-links">{sourceLinks.map(source => <a key={source.url} href={source.url} target="_blank" rel="noreferrer"><span><b>{source.label}</b><small>{source.note}</small></span><ExternalLink size={15} /></a>)}</div>
    </section>}

    <div className="teacher-avatar-grid">
      <div className="teacher-avatar-preview-panel">
        <div className="teacher-avatar-preview-shell">
          {active && <div className="teacher-avatar-preview-tools"><span>恋活卡片参数实时预览</span><label>预览缩放<input aria-label="主预览缩放" type="range" min=".8" max="2.4" step=".01" value={previewZoom} onChange={event => setPreviewZoom(Number(event.target.value))}/></label><button type="button" onClick={() => setPreviewZoom(1)}><RotateCcw size={14}/>复位缩放</button></div>}
          {koikatsuBase ? <div className="teacher-avatar-light-preview"><ParametricCardPreview src={cardPreview} morph={morph} zoom={previewZoom} alt="当前恋活角色卡" /><b>恋活参数预览</b><span>身体与脸部滑块会实时改变预览；衣装、发型与附加数据保持原卡片</span></div> : <div className="teacher-avatar-empty">尚未导入恋活 PMX 教师模型</div>}
        </div>
        {active && <div className="teacher-avatar-credit"><b>{active.display_name}</b><span>{active.creator} · {active.license_name}</span></div>}
      </div>
      <div className="teacher-avatar-controls">
      <div className="teacher-avatar-current">{active ? <><b>当前模型：{active.display_name}</b><span>{active.creator} · {active.license_name}</span></> : <span>尚未导入模型</span>}</div>
      <div className="teacher-avatar-roster" aria-label="本地模型轮换池">{items.map(item => <button key={item.model_id} className={item.model_id === active?.model_id ? "active" : ""} onClick={() => { setActiveId(item.model_id); setMorph(normalizeMorph(item.morph)) }}>{item.display_name}<small>{item.license_name}</small></button>)}</div>
      {cardPresets.length > 0 && <div className="teacher-card-presets" aria-label="内置角色卡"><div><b>内置角色卡</b><small>仅保留 {cardPresets.length} 张起始卡；其它卡片请自行导入</small></div><div className="teacher-card-preset-list">{cardPresets.map(card => <button type="button" key={card.card_id} onClick={() => applyCardPreset(card)}><b>{card.display_name}</b><small>{card.source_format === "koikatsu_sunshine" ? "恋活 Sunshine 参数卡" : "MentorFlow 参数卡"}</small></button>)}</div></div>}
      {active && !koikatsuNativeCardActive && <div className="teacher-morph-grid" aria-label="身体比例与表情参数">
        <Morph label="身高比例" value={morph.height} min={.5} max={2} onChange={height => setMorph({ ...morph, height })} />
        <Morph label="头部比例" value={morph.head_scale} min={.1} max={10} onChange={head_scale => setMorph({ ...morph, head_scale })} />
        <Morph label="胸部深度" value={morph.upper_chest} min={.1} max={10} onChange={upper_chest => setMorph({ ...morph, upper_chest })} />
        <Morph label="肩宽比例" value={morph.shoulder_width} min={.1} max={10} onChange={shoulder_width => setMorph({ ...morph, shoulder_width })} />
        <Morph label="腰围比例" value={morph.waist_width} min={.1} max={10} onChange={waist_width => setMorph({ ...morph, waist_width })} />
        <Morph label="胯宽比例" value={morph.hip_width} min={.1} max={10} onChange={hip_width => setMorph({ ...morph, hip_width })} />
        <Morph label="手臂长度" value={morph.arm_length} min={.1} max={10} onChange={arm_length => setMorph({ ...morph, arm_length })} />
        <Morph label="腿部长度" value={morph.leg_length} min={.1} max={10} onChange={leg_length => setMorph({ ...morph, leg_length })} />
        <Morph label="微笑" value={morph.smile} min={0} max={1} onChange={smile => setMorph({ ...morph, smile })} />
        <Morph label="眨眼" value={morph.blink} min={0} max={1} onChange={blink => setMorph({ ...morph, blink })} />
        <button type="button" className="teacher-reset" onClick={() => setMorph(normalizeMorph())}>恢复默认</button>
        <button className="teacher-save" onClick={saveMorph} disabled={busy}><Check size={14} />保存参数</button>
      </div>}

      {active && koikatsuNativeCardActive && <div className="teacher-native-driver-note" role="status">恋活原生卡已启用统一身体驱动；请使用上方原生参数调整，避免同一参数被重复变形。</div>}
      {!compact && characterPacks.length > 0 && <section className="teacher-character-packs" aria-label="可下载角色资源包">
        <h3><FileDown size={16} />可下载角色资源包</h3>
        <small>下载ZIP后在下方导入；再次选择匹配角色卡时会自动启用完整模型。</small>
        <div>{characterPackGroups.map(([category, packs]) => <div className="teacher-character-pack-group" key={category}><h4>{category}</h4>{packs.map(pack => <a key={pack.pack_id} href={pack.download_url} download>
          <span><b>{friendlyKksDisplayName(pack.display_name)}</b><small>{pack.creator || "本地资源"} · {(pack.size_bytes / 1024 / 1024).toFixed(1)} MB</small></span>
          <em>{pack.installed ? "已安装" : "下载"}</em>
        </a>)}</div>)}</div>
      </section>}
      {!compact && <form id="teacher-avatar-import" className="teacher-avatar-import" onSubmit={event => { event.preventDefault(); void importModel(event.currentTarget) }}>
        <h3><Upload size={16} />导入已下载模型</h3>
        <small>支持 VRM、GLB 和 MentorFlow 恋活角色资源包 ZIP；资源只保存到本机，匹配角色卡后会自动启用。</small>
        <input type="file" name="file" accept=".vrm,.glb,.zip" multiple required />
        <div className="teacher-import-fields"><input name="display_name" placeholder="角色显示名（单个文件可选）" /><input name="creator" placeholder="作者 / 模型作者" required /></div>
        <label><input type="checkbox" name="rights_confirmed" value="true" required />我确认拥有在本应用中使用该模型的授权</label>
        <label><input type="checkbox" name="adult_character_confirmed" value="true" required />我确认该教师角色为成年人设定</label>
        <button type="submit" disabled={busy}>{busy ? "正在导入…" : "加入本地轮换池"}</button>
      </form>}
      {!compact && <form id="teacher-card-import" className="teacher-card-import" onSubmit={event => { event.preventDefault(); void importCard(event.currentTarget) }}>
        <h3>导入 MentorFlow 角色卡</h3>
        <small>支持 MentorFlow JSON/PNG，也支持恋活 Sunshine 原生 PNG；原生卡只转换参数与资源引用，不复制游戏本体或 Mod。</small>
        <input type="file" name="file" accept=".json,.png" required />
        <button type="submit" disabled={busy}>{busy ? "正在读取…" : "读取角色卡"}</button>
      </form>}
      {notice && <div className="teacher-avatar-notice" role="status">{notice}</div>}
      </div>
    </div>
  </section>
}

function Morph({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  const safeValue = Number.isFinite(value) ? value : 0
  return <label><span>{label}<b>{safeValue.toFixed(2)}×</b></span><input type="range" min={min} max={max} step={.01} value={safeValue} onChange={event => onChange(Number(event.target.value))} /></label>
}
