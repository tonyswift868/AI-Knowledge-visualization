import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react"
import { Check, ExternalLink, FileDown, RotateCcw, Trash2, Upload } from "lucide-react"
import { teacherAvatarApi, type KksCharacterPack, type SpineTeacher, type TeacherAvatar, type TeacherAvatarCardPreset, type TeacherAvatarCardPresetList, type TeacherAvatarMorph } from "./api"
import {
  activeLearningTasks,
  activeTeacherPersonas,
  activeTeacherVisualProfiles,
  applyActiveTeacherPersona,
  deleteActiveTeacherPersona,
  saveActiveTeacherPersona,
  type ActiveTeacherPersona,
  type ActiveTeacherVisualProfile,
} from "../active-teaching/api"
import { ParametricCardPreview } from "./ParametricCardPreview"
import { TeacherCompanionPortrait, TeacherCompanionStage, type TeacherCompanionState } from "./TeacherCompanionStage"
import { friendlyKksDisplayName } from "./kksDisplayNames"
import "./teacher-avatar.css"
import "./teacher-avatar-warm.css"

const KoikatsuCardEditor = lazy(() => import("./KoikatsuCardEditor").then(module => ({ default: module.KoikatsuCardEditor })))

export type TeacherMotherImageId = "auburn" | "blonde-fang-teaching" | "blonde-fang-swimsuit"

const motherImageCandidates: Record<TeacherMotherImageId, {
  eyebrow: string
  title: string
  description: string
  traits: string
  src: string
  alt: string
}> = {
  auburn: {
    eyebrow: "MOTHER IMAGE REVIEW · TEACHER 01",
    title: "红发成年 AI 教师母图",
    description: "确认成熟头身、职业装、材质光影与露趾全身构图。母图尚未拆层，也没有绑定骨骼。",
    traits: "红棕长发 · 成熟曲线 · 职业装",
    src: "/teacher2d/original-ai-teacher/teacher-master-v1-alpha2.png",
    alt: "第一名原创成年 AI 教师全身母图，完整展示成熟身材与露趾鞋",
  },
  "blonde-fang-teaching": {
    eyebrow: "MOTHER IMAGE REVIEW · TEACHER 02 · TEACHING",
    title: "金发虎牙教师 · 小熊软糖教学态",
    description: "日常教学使用小熊软糖短外套、可爱百褶裙、长袜与玛丽珍鞋；爱心钥匙项链、造型拉链和腿环已经同步到双形态。",
    traits: "更白肤色 · 明显虎牙 · 小熊软糖外套",
    src: "/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-teaching-heart-key.png",
    alt: "第二名原创金发虎牙成年 AI 教师小熊软糖外套教学态全身母图",
  },
  "blonde-fang-swimsuit": {
    eyebrow: "MOTHER IMAGE REVIEW · TEACHER 02 · CHAT",
    title: "金发虎牙教师 · 泳装聊天态",
    description: "学习之余关闭外套、裙子、袜子和鞋的覆盖层，显示同一角色的白蓝泳装与赤足露趾形态。",
    traits: "同一身份 · 白蓝泳装 · 胸腿光泽",
    src: "/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-swimsuit-heart-key.png",
    alt: "第二名原创金发虎牙成年 AI 教师白蓝泳装聊天态全身母图，赤足露趾",
  },
}

export function TeacherMotherImageReview({ candidate = "auburn" }: { candidate?: TeacherMotherImageId }) {
  const teacher = motherImageCandidates[candidate]
  const approved = candidate !== "auburn"
  return (
    <section className="teacher-mother-review" aria-label="原创 AI 教师母图审稿">
      <div className="teacher-mother-review-copy">
        <span>{teacher.eyebrow}</span>
        <h3>{teacher.title}</h3>
        <p>{teacher.description}</p>
        <dl>
          <div><dt>人物设定</dt><dd>{teacher.traits}</dd></div>
          <div><dt>当前状态</dt><dd>{approved ? "V9 正式母图 · 已接入轻量动态" : "透明母图候选 · 等待审稿"}</dd></div>
          <div><dt>确认之后</dt><dd>拆分五官、头发、四肢、躯干与衣服</dd></div>
        </dl>
      </div>
      <div className="teacher-mother-review-stage">
        <div className="teacher-mother-review-halo" aria-hidden="true" />
        <img
          src={teacher.src}
          alt={teacher.alt}
        />
        <span className="teacher-mother-review-status">{approved ? "V9 正式母图 · 轻量动态已接入" : "母图候选 · 未绑定骨骼"}</span>
      </div>
    </section>
  )
}

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
  standalone = false,
  portrait = false,
  presentationState = "idle",
  teacherName = "艾莉丝",
  presentationManifestUrl,
  }: {
  workspaceId?: string
  compact?: boolean
  standalone?: boolean
  portrait?: boolean
  presentationState?: TeacherCompanionState
  teacherName?: string
  presentationManifestUrl?: string
}) {
  const [items, setItems] = useState<TeacherAvatar[]>([])
  const [characterPacks, setCharacterPacks] = useState<KksCharacterPack[]>([])
  const [cardPresets, setCardPresets] = useState<TeacherAvatarCardPreset[]>([])
  const [cardPresetStatus, setCardPresetStatus] = useState<TeacherAvatarCardPresetList>({
    items: [],
    count: 0,
    total: 0,
    source: "local-card-roster",
    status: "unavailable",
    available: false,
    reason: "正在检查内置角色卡资源…",
  })
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
  const [reviewMode, setReviewMode] = useState<`mother-${TeacherMotherImageId}` | "skeleton">("skeleton")
  const [personas, setPersonas] = useState<ActiveTeacherPersona[]>([])
  const [visualProfiles, setVisualProfiles] = useState<ActiveTeacherVisualProfile[]>([])
  const [personaId, setPersonaId] = useState("")
  const [personaName, setPersonaName] = useState("")
  const [personaPrompt, setPersonaPrompt] = useState("")
  const [personaStyle, setPersonaStyle] = useState("")
  const [personaVisualId, setPersonaVisualId] = useState("")
  const [personaNotice, setPersonaNotice] = useState("")
  const [activeTaskWorkspaceId, setActiveTaskWorkspaceId] = useState("")
  const [activeTaskTitle, setActiveTaskTitle] = useState("")
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
        setCardPresetStatus(cards)
      } catch (error) {
        // Card presets are an optional local roster; an older backend should
        // not prevent the normal VRM roster and preview from loading.
        setCardPresets([])
        setCardPresetStatus({
          items: [],
          count: 0,
          total: 0,
          source: "local-card-roster",
          status: "unavailable",
          available: false,
          reason: error instanceof Error ? error.message : "内置角色卡资源暂不可用。",
        })
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
  useEffect(() => {
    if (compact) return
    void Promise.all([
      activeTeacherPersonas(),
      activeTeacherVisualProfiles(),
      activeLearningTasks(false),
    ]).then(([value, profiles, tasks]) => {
      setPersonas(value)
      setVisualProfiles(profiles)
      let selectedWorkspaceId = ""
      try {
        selectedWorkspaceId = localStorage.getItem("mentorflow_active_teaching_workspace_v1") || ""
      } catch {
        selectedWorkspaceId = ""
      }
      const selectedTask = tasks.find(item => item.workspace_id === selectedWorkspaceId)
        || tasks.find(item => item.status === "learning")
        || tasks.find(item => item.status === "planned")
        || tasks[0]
      setActiveTaskWorkspaceId(selectedTask?.workspace_id || "")
      setActiveTaskTitle(selectedTask?.title || "")
      const selected = value.find(item => item.is_default) || value[0]
      if (selected) {
        setPersonaId(selected.id)
        setPersonaName(selected.name)
        setPersonaPrompt(selected.prompt)
        setPersonaStyle(selected.teaching_style)
        setPersonaVisualId(selected.visual_profile_id)
      }
    }).catch(error => setPersonaNotice(error instanceof Error ? error.message : "教师人设加载失败"))
  }, [compact])

  if (compact) {
    return (
      <section className="teacher-avatar-studio compact">
        <TeacherCompanionPortrait
          teacherName={teacherName || spineTeacher?.display_name || "艾莉丝"}
          presentationManifestUrl={presentationManifestUrl}
          state={presentationState}
          portrait={portrait}
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

  function selectPersona(id: string) {
    setPersonaId(id)
    const selected = personas.find(item => item.id === id)
    setPersonaName(selected?.name || "")
    setPersonaPrompt(selected?.prompt || "")
    setPersonaStyle(selected?.teaching_style || "")
    setPersonaVisualId(selected?.visual_profile_id || "")
    setPersonaNotice("")
  }

  async function savePersona() {
    try {
      setBusy(true)
      setPersonaNotice("")
      const saved = await saveActiveTeacherPersona({
        id: personaId || undefined,
        name: personaName,
        prompt: personaPrompt,
        teaching_style: personaStyle,
        visual_profile_id: personaVisualId,
      })
      const next = await activeTeacherPersonas()
      setPersonas(next)
      setPersonaId(saved.id)
      setPersonaName(saved.name)
      setPersonaPrompt(saved.prompt)
      setPersonaStyle(saved.teaching_style)
      setPersonaVisualId(saved.visual_profile_id)
      setPersonaNotice(personaId ? "教师人设已更新。" : "新教师人设已加入角色库。")
    } catch (error) {
      setPersonaNotice(error instanceof Error ? error.message : "教师人设保存失败")
    } finally {
      setBusy(false)
    }
  }

  async function saveAndApplyPersona() {
    if (!activeTaskWorkspaceId) {
      setPersonaNotice("还没有可绑定的学习任务；请先在“学习任务”中建立任务。")
      return
    }
    try {
      setBusy(true)
      setPersonaNotice("")
      const saved = await saveActiveTeacherPersona({
        id: personaId || undefined,
        name: personaName,
        prompt: personaPrompt,
        teaching_style: personaStyle,
        visual_profile_id: personaVisualId,
      })
      const result = await applyActiveTeacherPersona(activeTaskWorkspaceId, saved.id)
      const next = await activeTeacherPersonas()
      setPersonas(next)
      setPersonaId(saved.id)
      setPersonaName(saved.name)
      setPersonaPrompt(saved.prompt)
      setPersonaStyle(saved.teaching_style)
      setPersonaVisualId(saved.visual_profile_id)
      setPersonaNotice(
        `“${saved.name}”已应用到“${activeTaskTitle || activeTaskWorkspaceId}”；`
        + `${result.updated_session_count ? "正在进行的课堂也已同步，下一轮回复开始生效。" : "下次进入课堂时生效。"}`,
      )
    } catch (error) {
      setPersonaNotice(error instanceof Error ? error.message : "教师人设应用失败")
    } finally {
      setBusy(false)
    }
  }

  async function deletePersona() {
    const selected = personas.find(item => item.id === personaId)
    if (!selected || selected.is_default) return
    const usage = selected.usage_count
      ? `它正被 ${selected.usage_count} 个学习任务使用；删除后这些任务会安全切换到默认教师。`
      : "它当前没有绑定学习任务。"
    if (!window.confirm(`确定删除“${selected.name}”吗？${usage}`)) return
    try {
      setBusy(true)
      setPersonaNotice("")
      const result = await deleteActiveTeacherPersona(selected.id)
      const next = await activeTeacherPersonas()
      setPersonas(next)
      const replacement = next.find(item => item.id === result.replacement.id)
        || next.find(item => item.is_default)
        || next[0]
      if (replacement) {
        setPersonaId(replacement.id)
        setPersonaName(replacement.name)
        setPersonaPrompt(replacement.prompt)
        setPersonaStyle(replacement.teaching_style)
        setPersonaVisualId(replacement.visual_profile_id)
      }
      setPersonaNotice(result.reassigned_task_count
        ? `人设已删除，${result.reassigned_task_count} 个学习任务已切换到默认教师。`
        : "人设已删除。")
    } catch (error) {
      setPersonaNotice(error instanceof Error ? error.message : "教师人设删除失败")
    } finally {
      setBusy(false)
    }
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

  return <section className={`teacher-avatar-studio ${compact ? "compact" : ""} ${standalone ? "standalone" : ""}`}>
      <div className="teacher-avatar-heading">
      <div><span>{compact ? "LICENSED AVATAR ROSTER" : "2D AI TEACHER COMPANION"}</span><h2>{compact ? "今日教师模型" : "教师人设与教学风格"}</h2><p>{compact ? "当前教师的课程展示模型。" : "先编辑可复用的教师人设，再按需调整课堂外观与模型资源。"}</p></div>
        {!standalone && <a href="https://github.com/DragonBones/DragonBonesJS" target="_blank" rel="noreferrer">查看备用 DragonBones 方案 <ExternalLink size={14} /></a>}
    </div>

      {!compact && <section className="teacher-persona-library" aria-label="AI 教师人设库">
        <header>
          <div><span>主要设置 · TEACHING PERSONA</span><h3>教师大脑与教学风格</h3></div>
          <button type="button" onClick={() => selectPersona("")}>＋ 新建人设</button>
        </header>
        <p>学习任务只选择这里的角色，不再各自保存一份难以管理的人设；此前任务里填写的内容会自动迁移到这里。</p>
        <div className="teacher-persona-library__grid">
          <nav aria-label="已保存教师人设">
            {personas.map(item => <button
              type="button"
              key={item.id}
              className={item.id === personaId ? "active" : ""}
              onClick={() => selectPersona(item.id)}
            >
              <b>{item.name}</b>
              <small>{item.is_default ? "默认人设" : item.teaching_style || "自定义人设"}</small>
              <small>{item.visual_profile_name || "未绑定课堂外观"} · {item.usage_count || 0} 个任务</small>
            </button>)}
          </nav>
          <div className="teacher-persona-library__editor">
            <label>角色名称<input value={personaName} onChange={event => setPersonaName(event.target.value)} placeholder="例如：艾莉丝 · 零基础陪练" /></label>
            <label>教学风格<input value={personaStyle} onChange={event => setPersonaStyle(event.target.value)} placeholder="例如：短讲解、生活类比、逐步撤除提示" /></label>
            <label>
              绑定课堂外观
              <select value={personaVisualId} onChange={event => setPersonaVisualId(event.target.value)}>
                <option value="">未绑定（课堂使用系统默认外观）</option>
                {visualProfiles.map(profile => (
                  <option key={profile.id} value={profile.id}>{profile.name}</option>
                ))}
              </select>
              <small>选择“教师 02 · 金发虎牙小熊”后，这个人设进入课堂时就会使用她的小熊教学态与泳装聊天态。</small>
            </label>
            <label>完整人设<textarea value={personaPrompt} onChange={event => setPersonaPrompt(event.target.value)} placeholder="说明她怎样主动提问、如何照顾零基础、何时进入练习与验收。" /></label>
            <div className="teacher-persona-library__actions">
              <button
                type="button"
                className="danger"
                onClick={() => void deletePersona()}
                disabled={busy || !personaId || personas.find(item => item.id === personaId)?.is_default}
                title={personas.find(item => item.id === personaId)?.is_default ? "默认教师不能删除" : "删除当前人设"}
              >
                <Trash2 size={14} />删除人设
              </button>
              <button type="button" className="secondary" onClick={() => void savePersona()} disabled={busy || !personaName.trim() || !personaPrompt.trim()}>
                {busy ? "正在保存…" : personaId ? "仅保存到角色库" : "加入角色库"}
              </button>
              <button
                type="button"
                onClick={() => void saveAndApplyPersona()}
                disabled={busy || !personaName.trim() || !personaPrompt.trim() || !activeTaskWorkspaceId}
                title={activeTaskWorkspaceId ? `应用到当前学习任务：${activeTaskTitle || activeTaskWorkspaceId}` : "请先创建学习任务"}
              >
                保存并应用到当前任务
              </button>
            </div>
            <small className="teacher-persona-library__target">
              当前应用目标：{activeTaskTitle || "暂无可绑定的学习任务"}
            </small>
          </div>
        </div>
        {personaNotice && <div className="teacher-avatar-notice" role="status">{personaNotice}</div>}
      </section>}

      {!compact && <section className="teacher-visual-advanced" aria-label="视觉头像与模型高级设置">
        <header className="teacher-visual-advanced__header">
          <div><span>ADVANCED · VISUAL AVATAR</span><h3>视觉头像与模型高级设置</h3></div>
          <p>人设决定教师如何教学；这里仅调整展示外观、骨骼和本地模型资源。</p>
        </header>
      <div className="teacher-review-switch" role="group" aria-label="教师视觉审稿模式">
        <button type="button" className={reviewMode === "mother-auburn" ? "active" : ""} aria-pressed={reviewMode === "mother-auburn"} onClick={() => setReviewMode("mother-auburn")}>教师 01 · 红发</button>
        <button type="button" className={reviewMode === "mother-blonde-fang-teaching" ? "active" : ""} aria-pressed={reviewMode === "mother-blonde-fang-teaching"} onClick={() => setReviewMode("mother-blonde-fang-teaching")}>教师 02 · 小熊教学态</button>
        <button type="button" className={reviewMode === "mother-blonde-fang-swimsuit" ? "active" : ""} aria-pressed={reviewMode === "mother-blonde-fang-swimsuit"} onClick={() => setReviewMode("mother-blonde-fang-swimsuit")}>教师 02 · 泳装聊天态</button>
        <button type="button" className={reviewMode === "skeleton" ? "active" : ""} aria-pressed={reviewMode === "skeleton"} onClick={() => setReviewMode("skeleton")}>椿 · Live2D 动态</button>
        <span>公开默认教师是项目自有的分层形象；只有你明确选择并在本机合法持有的 Live2D 模型才会启用 Cubism 动作，两者不会互相覆盖。</span>
      </div>

      {!compact && (reviewMode !== "skeleton"
        ? <TeacherMotherImageReview candidate={reviewMode === "mother-auburn" ? "auburn" : reviewMode === "mother-blonde-fang-swimsuit" ? "blonde-fang-swimsuit" : "blonde-fang-teaching"} />
        : <TeacherCompanionStage teacherName={spineTeacher?.display_name || "艾莉丝"} spineManifestUrl={configuredSpineManifestUrl || spineTeacher?.manifest_url} />)}

      <form className="spine-teacher-import" onSubmit={event => { event.preventDefault(); void importSpineTeacher(event.currentTarget) }}>
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
      </form>

      <details className="teacher-legacy-tools">
        <summary>旧版 3D / 恋活导入工具</summary>
        <p>仅用于迁移已有角色卡。当前 AI 教师展示默认使用 2D 角色舞台。</p>
        <Suspense fallback={<div className="teacher-avatar-empty" role="status">正在加载旧版 3D 导入工具…</div>}>
          <KoikatsuCardEditor modelUrl={koikatsuModelUrlOverride !== undefined ? koikatsuModelUrlOverride : (koikatsuBase?.model_url || undefined)} modelFormat={koikatsuBase?.format === "runtime-pack" ? undefined : koikatsuBase?.format} previewMorph={morph} onMorphChange={setMorph} onNativeCardChange={setKoikatsuNativeCardActive} onModelUrlChange={setKoikatsuModelUrlOverride} onPreviewChange={setKoikatsuCardPreviewUrl} show3dPreview={false} />
        </Suspense>
      </details>

    <section className="teacher-avatar-sources" aria-label="模型来源网站">
      <div className="teacher-avatar-sources-heading"><div><b>自行获取模型</b><span>打开来源页下载 VRM/GLB，确认作者许可后再导入。</span></div><FileDown size={20} /></div>
      <div className="teacher-avatar-source-links">{sourceLinks.map(source => <a key={source.url} href={source.url} target="_blank" rel="noreferrer"><span><b>{source.label}</b><small>{source.note}</small></span><ExternalLink size={15} /></a>)}</div>
    </section>

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
      {cardPresetStatus.status !== "ready" && <div className="teacher-avatar-notice" role="status">
        内置角色卡{cardPresetStatus.status === "partial" ? "部分可用" : "暂不可用"}：{cardPresetStatus.reason || "请安装匹配资源，或在下方导入角色卡。"}
      </div>}
      {cardPresets.length > 0 && <div className="teacher-card-presets" aria-label="内置角色卡"><div><b>内置角色卡</b><small>已加载 {cardPresets.length} 张起始卡；其它卡片请自行导入</small></div><div className="teacher-card-preset-list">{cardPresets.map(card => <button type="button" key={card.card_id} onClick={() => applyCardPreset(card)}><b>{card.display_name}</b><small>{card.source_format === "koikatsu_sunshine" ? "恋活 Sunshine 参数卡" : "MentorFlow 参数卡"}</small></button>)}</div></div>}
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
      {characterPacks.length > 0 && <section className="teacher-character-packs" aria-label="可下载角色资源包">
        <h3><FileDown size={16} />可下载角色资源包</h3>
        <small>下载ZIP后在下方导入；再次选择匹配角色卡时会自动启用完整模型。</small>
        <div>{characterPackGroups.map(([category, packs]) => <div className="teacher-character-pack-group" key={category}><h4>{category}</h4>{packs.map(pack => <a key={pack.pack_id} href={pack.download_url} download>
          <span><b>{friendlyKksDisplayName(pack.display_name)}</b><small>{pack.creator || "本地资源"} · {(pack.size_bytes / 1024 / 1024).toFixed(1)} MB</small></span>
          <em>{pack.installed ? "已安装" : "下载"}</em>
        </a>)}</div>)}</div>
      </section>}
      <form id="teacher-avatar-import" className="teacher-avatar-import" onSubmit={event => { event.preventDefault(); void importModel(event.currentTarget) }}>
        <h3><Upload size={16} />导入已下载模型</h3>
        <small>支持 VRM、GLB 和 MentorFlow 恋活角色资源包 ZIP；资源只保存到本机，匹配角色卡后会自动启用。</small>
        <input type="file" name="file" accept=".vrm,.glb,.zip" multiple required />
        <div className="teacher-import-fields"><input name="display_name" placeholder="角色显示名（单个文件可选）" /><input name="creator" placeholder="作者 / 模型作者" required /></div>
        <label><input type="checkbox" name="rights_confirmed" value="true" required />我确认拥有在本应用中使用该模型的授权</label>
        <label><input type="checkbox" name="adult_character_confirmed" value="true" required />我确认该教师角色为成年人设定</label>
        <button type="submit" disabled={busy}>{busy ? "正在导入…" : "加入本地轮换池"}</button>
      </form>
      <form id="teacher-card-import" className="teacher-card-import" onSubmit={event => { event.preventDefault(); void importCard(event.currentTarget) }}>
        <h3>导入 MentorFlow 角色卡</h3>
        <small>支持 MentorFlow JSON/PNG，也支持恋活 Sunshine 原生 PNG；原生卡只转换参数与资源引用，不复制游戏本体或 Mod。</small>
        <input type="file" name="file" accept=".json,.png" required />
        <button type="submit" disabled={busy}>{busy ? "正在读取…" : "读取角色卡"}</button>
      </form>
      {notice && <div className="teacher-avatar-notice" role="status">{notice}</div>}
      </div>
    </div>
      </section>}
  </section>
}

function Morph({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  const safeValue = Number.isFinite(value) ? value : 0
  return <label><span>{label}<b>{safeValue.toFixed(2)}×</b></span><input type="range" min={min} max={max} step={.01} value={safeValue} onChange={event => onChange(Number(event.target.value))} /></label>
}
