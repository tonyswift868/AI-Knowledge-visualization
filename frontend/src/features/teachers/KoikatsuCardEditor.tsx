import { useEffect, useMemo, useState } from "react"
import { Download, SlidersHorizontal, Upload } from "lucide-react"
import { teacherAvatarApi, type KoikatsuCardInspection } from "./api"

type Values = Record<string, number>

function valuesOf(card: KoikatsuCardInspection, section: "body" | "face"): Values {
  return Object.fromEntries(card[section].map(slider => [section === "body" ? slider.key! : String(slider.index!), slider.value]))
}

export function KoikatsuCardEditor() {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState("")
  const [card, setCard] = useState<KoikatsuCardInspection | null>(null)
  const [body, setBody] = useState<Values>({})
  const [face, setFace] = useState<Values>({})
  const [tab, setTab] = useState<"body" | "face">("body")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const changed = useMemo(() => Boolean(card && card.body.some(item => body[item.key!] !== item.value) || card && card.face.some(item => face[String(item.index!)] !== item.value)), [body, card, face])

  async function load(selected: File | null) {
    if (!selected) return
    try {
      setBusy(true)
      setNotice("")
      const data = new FormData()
      data.set("file", selected)
      const result = await teacherAvatarApi.inspectKoikatsuCard(data)
      setFile(selected)
      setCard(result.card)
      setBody(valuesOf(result.card, "body"))
      setFace(valuesOf(result.card, "face"))
      setPreview(previous => { if (previous) URL.revokeObjectURL(previous); return URL.createObjectURL(selected) })
    } catch (error) {
      setCard(null)
      setNotice(error instanceof Error ? error.message : "角色卡读取失败")
    } finally { setBusy(false) }
  }

  async function exportCard() {
    if (!file || !card) return
    try {
      setBusy(true)
      setNotice("")
      const data = new FormData()
      data.set("file", file)
      data.set("body_values", JSON.stringify(body))
      data.set("face_values", JSON.stringify(face))
      const blob = await teacherAvatarApi.exportKoikatsuCard(data)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = `${file.name.replace(/\.png$/i, "")}-edited.png`
      anchor.click()
      URL.revokeObjectURL(url)
      setNotice("已导出原生恋活角色卡；放回 UserData/chara/female 即可在恋活加载。")
    } catch (error) { setNotice(error instanceof Error ? error.message : "角色卡导出失败")
    } finally { setBusy(false) }
  }

  const sliders = card ? card[tab] : []
  const activeValues = tab === "body" ? body : face
  const setActiveValues = tab === "body" ? setBody : setFace
  return <section className="koikatsu-card-editor" aria-label="恋活角色卡编辑器">
    <header><div><span>KOIKATSU SUNSHINE CHARACTER CARD</span><h2>恋活捏人</h2><p>直接编辑原生 PNG 角色卡中的身体与脸部滑块，导出的卡片可回到恋活使用。</p></div><SlidersHorizontal size={24} /></header>
    <label className="koikatsu-file"><Upload size={16} /><span>{file?.name || "选择恋活 Sunshine 角色卡 PNG"}</span><input type="file" accept="image/png,.png" onChange={event => void load(event.target.files?.[0] || null)} disabled={busy} /></label>
    {card && <div className="koikatsu-card-workbench">
      <aside>{preview && <img src={preview} alt={`${card.character.display_name || "恋活"}角色卡预览`} />}<b>{card.character.display_name || card.character.nickname || "未命名角色"}</b><small>{card.native_summary.body_slider_count} 个身体参数 · {card.native_summary.face_slider_count} 个脸部参数</small>{card.native_summary.has_mod_data && <small>含 Mod / 插件数据：导出时会保留</small>}</aside>
      <div className="koikatsu-controls"><div className="koikatsu-tabs"><button className={tab === "body" ? "active" : ""} onClick={() => setTab("body")}>身体</button><button className={tab === "face" ? "active" : ""} onClick={() => setTab("face")}>脸部</button></div><div className="koikatsu-sliders">{sliders.map(slider => { const key = tab === "body" ? slider.key! : String(slider.index!); const value = activeValues[key] ?? slider.value; return <label key={key}><span>{slider.label}<b>{Math.round(value * 100)}</b></span><input type="range" min={slider.min} max={slider.max} step="0.01" value={value} onChange={event => setActiveValues(values => ({ ...values, [key]: Number(event.target.value) }))} /></label> })}</div><div className="koikatsu-actions"><button type="button" onClick={() => { if (!card) return; setBody(valuesOf(card, "body")); setFace(valuesOf(card, "face")) }} disabled={!changed || busy}>恢复卡片原值</button><button type="button" onClick={() => void exportCard()} disabled={!changed || busy}><Download size={15} />{busy ? "处理中…" : "导出恋活角色卡"}</button></div></div>
    </div>}
    {notice && <div className="teacher-avatar-notice" role="status">{notice}</div>}
  </section>
}
