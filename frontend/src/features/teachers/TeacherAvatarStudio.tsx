import { useCallback, useEffect, useMemo, useState } from "react"
import { Check, ExternalLink, Upload, UserRoundCog } from "lucide-react"
import { TeacherAvatarRig } from "./TeacherAvatarRig"
import { teacherAvatarApi, type TeacherAvatar, type TeacherAvatarMorph } from "./api"
import "./teacher-avatar.css"

const motionLabels: Record<string, string> = { idle: "待机", explain: "讲解", question: "提问", encourage: "鼓励", quiz: "考核", celebrate: "庆祝", redirect: "温柔引导" }

export function TeacherAvatarStudio({ workspaceId = "default", compact = false }: { workspaceId?: string; compact?: boolean }) {
  const [items, setItems] = useState<TeacherAvatar[]>([])
  const [activeId, setActiveId] = useState("")
  const [motion, setMotion] = useState("idle")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState(false)
  const [morph, setMorph] = useState<TeacherAvatarMorph>({ height: 1, head_scale: 1, upper_chest: 1, shoulder_width: 1, smile: .18, blink: 1 })
  const active = useMemo(() => items.find(item => item.model_id === activeId) || items[0] || null, [activeId, items])

  const load = useCallback(async () => {
    try {
      const [list, today] = await Promise.all([teacherAvatarApi.list(), teacherAvatarApi.today(workspaceId)])
      setItems(list.items)
      const selected = today.item || list.items[0]
      if (selected) { setActiveId(selected.model_id); setMorph(selected.morph) }
    } catch (error) { setNotice(error instanceof Error ? error.message : "教师模型加载失败") }
  }, [workspaceId])
  useEffect(() => { void load() }, [load])
  useEffect(() => { if (active) setMorph(active.morph) }, [active?.model_id])

  async function importModel(form: HTMLFormElement) {
    const body = new FormData(form)
    try {
      setBusy(true); setNotice("")
      const result = await teacherAvatarApi.importModel(body)
      await load(); setActiveId(result.item.model_id); setNotice("模型已加入每日轮换池")
      form.reset()
    } catch (error) { setNotice(error instanceof Error ? error.message : "模型导入失败") }
    finally { setBusy(false) }
  }

  async function saveMorph() {
    if (!active) return
    try {
      setBusy(true)
      const result = await teacherAvatarApi.updateMorph(active.model_id, morph)
      setItems(value => value.map(item => item.model_id === active.model_id ? result.item : item))
      setNotice("体型与表情参数已保存")
    } catch (error) { setNotice(error instanceof Error ? error.message : "参数保存失败") }
    finally { setBusy(false) }
  }

  return <section className={`teacher-avatar-studio ${compact ? "compact" : ""}`}>
    <div className="teacher-avatar-heading"><div><span>LICENSED AVATAR ROSTER</span><h2>今日教师模型</h2><p>教学人格、知识记忆与外观模型相互独立；每天从已授权模型中稳定轮换。</p></div><a href="https://hub.vroid.com/en/" target="_blank" rel="noreferrer">浏览 VRoid Hub <ExternalLink size={14} /></a></div>
    <div className="teacher-avatar-grid">
      <div className="teacher-avatar-preview">
        {active ? <TeacherAvatarRig modelUrl={active.model_url} motion={motion} morph={morph} onError={setNotice} /> : <div className="teacher-avatar-empty"><UserRoundCog size={36} /><b>尚未导入授权模型</b><span>支持 VRM 1.0 和 GLB；导入后会成为每日教师外观。</span></div>}
        {active && <div className="teacher-avatar-credit"><b>{active.display_name}</b><span>{active.creator} · {active.license_name}</span></div>}
      </div>
      <div className="teacher-avatar-controls">
        <div className="teacher-avatar-roster">{items.map(item => <button key={item.model_id} className={item.model_id === active?.model_id ? "active" : ""} onClick={() => setActiveId(item.model_id)}>{item.display_name}<small>{item.license_name}</small></button>)}</div>
        <div className="teacher-motion-row">{(active?.motions || Object.keys(motionLabels)).map(name => <button key={name} className={motion === name ? "active" : ""} onClick={() => setMotion(name)}>{motionLabels[name] || name}</button>)}</div>
        {active && <div className="teacher-morph-grid">
          <Morph label="身高" value={morph.height} min={.85} max={1.15} onChange={height => setMorph({ ...morph, height })} />
          <Morph label="头部比例" value={morph.head_scale} min={.85} max={1.15} onChange={head_scale => setMorph({ ...morph, head_scale })} />
          <Morph label="上胸比例" value={morph.upper_chest} min={.9} max={1.25} onChange={upper_chest => setMorph({ ...morph, upper_chest })} />
          <Morph label="肩宽" value={morph.shoulder_width} min={.85} max={1.2} onChange={shoulder_width => setMorph({ ...morph, shoulder_width })} />
          <Morph label="微笑" value={morph.smile} min={0} max={1} onChange={smile => setMorph({ ...morph, smile })} />
          <button className="teacher-save" onClick={saveMorph} disabled={busy}><Check size={14} />保存参数</button>
        </div>}
        {!compact && <form className="teacher-avatar-import" onSubmit={event => { event.preventDefault(); void importModel(event.currentTarget) }}>
          <h3><Upload size={16} />导入已授权模型</h3>
          <input type="file" name="file" accept=".vrm,.glb" required />
          <div className="teacher-import-fields"><input name="display_name" placeholder="角色显示名" required /><input name="creator" placeholder="作者/模型师" required /><input name="source_url" type="url" placeholder="来源页面 URL" required /><input name="license_name" placeholder="许可名称" required /><input name="license_url" type="url" placeholder="许可条款 URL（可选）" /></div>
          <label><input type="checkbox" name="rights_confirmed" value="true" required />我确认拥有在本应用中使用该模型的授权</label>
          <label><input type="checkbox" name="adult_character_confirmed" value="true" required />我确认该教师角色为成年人</label>
          <button type="submit" disabled={busy}>{busy ? "正在导入…" : "加入轮换池"}</button>
        </form>}
        {notice && <div className="teacher-avatar-notice">{notice}</div>}
      </div>
    </div>
  </section>
}

function Morph({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <label><span>{label}<b>{value.toFixed(2)}</b></span><input type="range" min={min} max={max} step={.01} value={value} onChange={event => onChange(Number(event.target.value))} /></label>
}
