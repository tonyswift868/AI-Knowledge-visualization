import * as React from "react"
import { createPortal } from "react-dom"
import dagre from "@dagrejs/dagre"
import { Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position,
  ReactFlow, ReactFlowProvider, applyNodeChanges, type BuiltInEdge, type Node, type NodeProps, type ReactFlowInstance } from "@xyflow/react"
import { BookOpen, Clock3, GitBranch, Maximize2, Minimize2, X } from "lucide-react"
import "@xyflow/react/dist/style.css"
import "./plan-graph.css"

export type GraphCitation = { chunk_id: string; title: string; url: string | null; locator: string; quote: string }
export type GraphModule = {
  key: string; title: string; summary: string; id?: string; chapters?: string[]; refs?: string[]; ref_titles?: string[]
  prerequisites?: string[]; minutes?: [number, number]
  points?: { title: string; summary: string; citations?: GraphCitation[]; source_general?: boolean }[]
}
export type GraphStage = { title: string; modules: GraphModule[] }
type ModuleData = { module: GraphModule; number: number; mode: "structure" | "outline"; active?: boolean }
type ModuleNode = Node<ModuleData, "module">
const WIDTH = 294, HEIGHT = 174, GAP = 44, PAD = 26

const ModuleCard = React.memo(function ModuleCard({ data }: NodeProps<ModuleNode>) {
  const m = data.module
  return <div className={`pg-module ${data.active ? "pg-active" : ""}`} style={{ animationDelay: `${Math.min(data.number, 15) * 35}ms` }}>
    <Handle type="target" position={Position.Left} id="left" /><Handle type="target" position={Position.Top} id="top" />
    <div className="pg-module-heading"><span className="pg-number">{String(data.number).padStart(2, "0")}</span><strong>{m.title}</strong></div>
    <p className="pg-module-summary">{m.summary || "点开查看学习内容与原文依据"}</p>
    <div className="pg-tags">
      {m.minutes && <span><Clock3 size={12} />{m.minutes[0]}–{m.minutes[1]} 分钟</span>}
      <span><BookOpen size={12} />{data.mode === "outline" ? `${m.points?.length ?? 0} 个点` : `${(m.chapters ?? m.ref_titles ?? m.refs ?? []).length} 处依据`}</span>
    </div>
    <Handle type="source" position={Position.Right} id="right" /><Handle type="source" position={Position.Bottom} id="bottom" />
  </div>
})
function StageCard({ data }: NodeProps) { return <div className="pg-stage-label"><span>{data.label as string}</span><b title={data.title as string}>{data.title as string}</b></div> }
const nodeTypes = { module: ModuleCard, stage: StageCard }

// Dagre lays out bounded rows rather than one very long sequential chain.
// Phase parents contain only module nodes; hundreds of points stay in the drawer.
export function graphLayout(stages: GraphStage[], mode: "structure" | "outline") {
  const count = stages.reduce((n, s) => n + s.modules.length, 0)
  const columns = count <= 2 ? 1 : 3
  const nodes: Node[] = [], edges: BuiltInEdge[] = [], positions = new Map<string, { row: number; stage: number }>()
  let y = 0, number = 0
  stages.forEach((s, si) => {
    if (!s.modules.length) return
    const rows = Math.ceil(s.modules.length / columns), usedColumns = Math.min(columns, s.modules.length)
    const width = usedColumns * WIDTH + (usedColumns - 1) * GAP + PAD * 2
    const height = rows * HEIGHT + (rows - 1) * GAP + 68 + PAD
    nodes.push({ id: `phase:${si}`, type: "stage", position: { x: 0, y }, data: { title: s.title, label: mode === "structure" ? "资料结构" : `阶段 ${si + 1}` },
      style: { width, height, "--pg-phase": `${(si % 3) * 4 + 6}%` } as React.CSSProperties, className: "pg-stage", selectable: false, draggable: false })
    for (let row = 0; row < rows; row++) {
      const part = s.modules.slice(row * columns, (row + 1) * columns)
      const g = new dagre.graphlib.Graph().setGraph({ rankdir: "LR", ranksep: GAP, nodesep: GAP, marginx: 0, marginy: 0 }).setDefaultEdgeLabel(() => ({}))
      part.forEach((m, i) => { g.setNode(m.key, { width: WIDTH, height: HEIGHT }); if (i) g.setEdge(part[i - 1].key, m.key) })
      dagre.layout(g)
      part.forEach(m => {
        number++
        const p = g.node(m.key)
        nodes.push({ id: m.key, type: "module", parentId: `phase:${si}`, extent: "parent", position: { x: PAD + p.x - WIDTH / 2, y: 68 + row * (HEIGHT + GAP) },
          data: { module: m, number, mode }, style: { width: WIDTH, height: HEIGHT }, ariaLabel: `模块 ${number}：${m.title}` })
        positions.set(m.key, { row, stage: si })
      })
    }
    y += height + 64
  })
  const all = stages.flatMap(s => s.modules)
  const keys = new Set(all.map(m => m.key))
  const prerequisitePairs = new Set(all.flatMap(m => (m.prerequisites ?? []).map(k => `${k}->${m.key}`)))
  function add(source: string, target: string, prerequisite: boolean) {
    const a = positions.get(source)!, b = positions.get(target)!
    const acrossRows = a.stage !== b.stage || a.row !== b.row
    edges.push({ id: `${source}->${target}`, source, target, sourceHandle: acrossRows ? "bottom" : "right", targetHandle: acrossRows ? "top" : "left",
      type: "smoothstep", pathOptions: { borderRadius: 24 }, className: prerequisite ? "pg-prerequisite" : "pg-sequence",
      markerEnd: prerequisite ? { type: MarkerType.ArrowClosed, color: "var(--accent)" } : undefined,
      data: { prerequisite }, ariaLabel: `${all.find(m => m.key === source)?.title} → ${all.find(m => m.key === target)?.title}${prerequisite ? "（先学）" : "（顺序）"}` })
  }
  all.forEach((m, i) => {
    if (i && !prerequisitePairs.has(`${all[i - 1].key}->${m.key}`)) add(all[i - 1].key, m.key, false)
    ;(m.prerequisites ?? []).filter(k => keys.has(k) && k !== m.key).forEach(k => add(k, m.key, true))
  })
  return { nodes, edges }
}

function ModuleDetails({ module: m, stages, mode, close }: { module: GraphModule; stages: GraphStage[]; mode: "structure" | "outline"; close: () => void }) {
  const titleOf = Object.fromEntries(stages.flatMap(s => s.modules).map(m => [m.key, m.title]))
  const chapters = m.chapters ?? m.ref_titles ?? m.refs ?? []
  return <aside className="pg-details nodrag nowheel" aria-label={`${m.title}详情`}>
    <header><span className="pg-eyebrow">{mode === "structure" ? "模块内容与依据" : `${m.points?.length ?? 0} 个知识点`}</span><button className="pg-icon" onClick={close} aria-label="关闭模块详情"><X size={18} />关闭</button></header>
    <h3>{m.title}</h3><h4>学什么</h4><p>{m.summary}</p>
    {m.minutes && <p className="pg-meta">预计 {m.minutes[0]}–{m.minutes[1]} 分钟</p>}
    {!!m.prerequisites?.length && <p className="pg-meta">先学：{m.prerequisites.map(k => titleOf[k] ?? k).join("、")}</p>}
    {mode === "structure" && <p className="pg-meta">知识点将从这些资料章节生成，确认结构后会给你看完整知识地图。</p>}
    {!!chapters.length && <details className="pg-chapters" open={mode === "structure"}><summary><BookOpen size={14} />全部依据章节 · {chapters.length} 处</summary><ol>{chapters.map((t, i) => <li key={i}>{t}{m.refs?.[i] && <small>{m.refs[i]}</small>}</li>)}</ol></details>}
    {mode === "outline" && <ol className="pg-points">{m.points?.map((p, i) => <li key={i}>
      <div className="pg-point-title"><span>{i + 1}</span><b>{p.title}</b></div><p>{p.summary}</p>
      {p.source_general && <span className="pg-meta">来源一般</span>}
      {p.citations?.map((c, ci) => <details className="pg-citation" key={ci}><summary>依据：{c.title} · {c.locator}</summary>
        <blockquote>{c.quote}</blockquote><small>{c.chunk_id}</small>{c.url && <a href={c.url} target="_blank" rel="noreferrer">打开原文</a>}</details>)}
    </li>)}</ol>}
  </aside>
}

export function PlanGraph({ stages, mode = "outline" }: { stages: GraphStage[]; mode?: "structure" | "outline" }) {
  const layout = React.useMemo(() => graphLayout(stages, mode), [stages, mode])
  const [nodes, setNodes] = React.useState(layout.nodes)
  const [selected, select] = React.useState<string | null>(null)
  const [hovered, hover] = React.useState<string | null>(null)
  const [fullscreen, full] = React.useState(false)
  const [instance, setInstance] = React.useState<ReactFlowInstance | null>(null)
  const returnFocus = React.useRef<HTMLButtonElement>(null)
  // Host draft refreshes and parent input renders create a new stages array.
  // Layout updates must not discard the module the user is reading.
  React.useEffect(() => { setNodes(layout.nodes); hover(null) }, [layout])
  React.useEffect(() => {
    if (!fullscreen) return
    const old = document.body.style.overflow
    document.body.style.overflow = "hidden"
    return () => { document.body.style.overflow = old }
  }, [fullscreen])
  React.useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      if (selected) select(null)
      else if (fullscreen) { full(false); returnFocus.current?.focus() }
    }
    document.addEventListener("keydown", esc)
    return () => document.removeEventListener("keydown", esc)
  }, [selected, fullscreen])
  React.useEffect(() => { if (instance) requestAnimationFrame(() => instance.fitView({ padding: .12, duration: 220, minZoom: .025, maxZoom: 1 })) }, [instance, layout, fullscreen])
  const all = stages.flatMap(s => s.modules)
  const detail = all.find(m => m.key === selected)
  const active = hovered ?? selected
  const viewNodes = nodes.map(n => n.type === "module" ? { ...n, data: { ...n.data, active: n.id === active } } : n)
  const edges = layout.edges.map(e => { const lit = e.source === active || e.target === active; return { ...e, animated: lit, className: `${e.className} ${lit ? "pg-edge-active" : ""}` } })
  const content = <ReactFlowProvider><section className={`plan-graph ${fullscreen ? "pg-fullscreen" : ""}`} aria-label={mode === "structure" ? "资料结构图" : "知识地图"} role={fullscreen ? "dialog" : undefined} aria-modal={fullscreen || undefined}>
    <div className="pg-toolbar"><div><GitBranch size={16} /><b>{mode === "structure" ? "学习结构" : "知识地图"}</b><span>{all.length} 个模块{mode === "outline" ? ` · ${all.reduce((n, m) => n + (m.points?.length ?? 0), 0)} 个点` : ""}</span></div>
      <button className="pg-expand" ref={returnFocus} onClick={() => full(!fullscreen)}>{fullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}{fullscreen ? "退出全屏" : "全屏查看"}</button></div>
    <div className={`pg-workspace ${detail ? "pg-has-details" : ""}`}>
      <div className="pg-canvas"><ReactFlow nodes={viewNodes} edges={edges} nodeTypes={nodeTypes} onInit={setInstance}
        onNodesChange={changes => setNodes(ns => applyNodeChanges(changes, ns))} onNodeClick={(_, n) => { if (n.type === "module") select(n.id) }}
        onNodeMouseEnter={(_, n) => { if (n.type === "module") hover(n.id) }} onNodeMouseLeave={() => hover(null)}
        nodesConnectable={false} deleteKeyCode={null} fitView fitViewOptions={{ padding: .12, minZoom: .025, maxZoom: 1 }} minZoom={.025} maxZoom={2.5} onlyRenderVisibleElements>
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="var(--border-strong)" />
        <Controls position="bottom-right" showInteractive={false} aria-label="地图缩放与适配" />
      </ReactFlow></div>
      {detail && <ModuleDetails module={detail} stages={stages} mode={mode} close={() => select(null)} />}
    </div>
    <footer className="pg-legend"><MiniMap position="bottom-left" style={{ width: 110, height: 66 }} pannable zoomable nodeColor={n => n.type === "stage" ? "var(--accent-soft)" : "var(--accent)"} maskColor="var(--surface)" />
      <span><i />学习顺序</span><span><i className="pg-arrow" />先学关系</span><span>拖动画布 · 滚轮缩放 · 点模块看详情{selected ? " · Esc 关闭详情" : fullscreen ? " · Esc 退出" : ""}</span>
      <select aria-label="定位模块" value={selected ?? ""} onChange={e => { const id = e.target.value; if (id) { select(id); instance?.fitView({ nodes: [{ id }], duration: 250, padding: .6, maxZoom: 1 }) } }}>
        <option value="">定位模块…</option>{all.map((m, i) => <option key={m.key} value={m.key}>{i + 1}. {m.title}</option>)}
      </select>
    </footer>
  </section></ReactFlowProvider>
  return fullscreen ? createPortal(content, document.body) : content
}
