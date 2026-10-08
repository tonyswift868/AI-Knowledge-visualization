import * as React from "react"
import { Loading } from '../loading/Loading'
import dagre from "@dagrejs/dagre"
import { Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider,
  applyNodeChanges, type Node, type NodeProps, type Edge, type ReactFlowInstance } from "@xyflow/react"
import { ArrowLeft, BookOpen, LocateFixed, X } from "lucide-react"
import { ClassroomMarkdown } from "../classroom/Markdown"
import { useApp, type KnowledgeDetail, type KnowledgeModule, type KnowledgePoint } from "../store"
import "@xyflow/react/dist/style.css"
import "./plan-graph.css"
import "./knowledge-map.css"

export function moduleProgress(m: KnowledgeModule): [string, string] {
  if (m.question_count === 0) return ["只学不考", "quiet"]
  if (m.state === "passed" || m.state === "skipped") return ["已通过", "green"]
  if (m.state === "failed") return ["未通过", "red"]
  if (m.points.some(p => p.taught)) return ["学习中", "yellow"]
  return ["未学", "quiet"]
}
export function pointProgress(p: KnowledgePoint): [string, string] {
  if (p.needs_review || p.state === "red") return ["需要复习", "red"]
  if (p.state === "green") return ["已掌握", "green"]
  if (p.state === "weak") return ["有瑕疵", "yellow"]
  return p.taught ? ["已学（掌握按题统计）", "yellow"] : ["未学／未检测", "quiet"]
}
type MapData = { title: string; summary?: string; label?: string; tone?: string; tags?: string; current?: boolean; number?: number; stage?: string }
type MapNode = Node<MapData, "mapCard">
const Card = React.memo(function Card({data}: NodeProps<MapNode>) {
  return <div className={`km-node km-${data.tone ?? "quiet"} ${data.current ? "km-current" : ""}`} title={data.summary}>
    <Handle type="target" position={Position.Left}/><span className="km-stage">{data.stage ?? data.label}</span>
    <strong>{data.number ? <em>{data.number}</em> : null}{data.title}</strong>
    {data.summary && <p>{data.summary}</p>}
    <span className="km-node-label">{data.label}</span>{data.tags && <small>{data.tags}</small>}
    <Handle type="source" position={Position.Right}/>
  </div>
})
const nodeTypes = { mapCard: Card }
const W = 250, H = 150
function edge(id: string, source: string, target: string, related = false): Edge {
  return { id, source, target, type: "smoothstep", className: related ? "km-related" : "km-prerequisite",
    markerEnd: related ? undefined : {type: MarkerType.ArrowClosed, color: "var(--accent)"} }
}
function layout(nodes: MapNode[], edges: Edge[], network = false) {
  const g = new dagre.graphlib.Graph().setGraph({rankdir: "LR", ranksep: 100, nodesep: 38}).setDefaultEdgeLabel(()=>({}))
  const linked = new Set(edges.flatMap(e => [e.source, e.target]))
  const ranked = network ? nodes.filter(n => linked.has(n.id)) : nodes
  ranked.forEach(n => g.setNode(n.id,{width: W,height: H}))
  edges.forEach(e => { if (g.hasNode(e.source) && g.hasNode(e.target)) g.setEdge(e.source,e.target) })
  if (ranked.length) dagre.layout(g)
  // Fold the ranked overview into bounded rows, as in PlanGraph. A tall star
  // or a long module chain would otherwise fit to illegibly tiny cards.
  if (!network && nodes.length > 5) {
    const ordered = ranked.filter(n=>n.id!=="goal").sort((a,b)=>g.node(a.id).x-g.node(b.id).x || nodes.indexOf(a)-nodes.indexOf(b))
    const positions = new Map(ordered.map((n,i)=>[n.id,{x:W+70+(i%3)*(W+45),y:Math.floor(i/3)*(H+35)}]))
    positions.set("goal",{x:0,y:(Math.ceil(ordered.length/3)-1)*(H+35)/2})
    return nodes.map(n=>({...n,position:positions.get(n.id)!}))
  }
  const bottom = ranked.reduce((max,n)=>Math.max(max,g.node(n.id).y + H),0)
  let loose = 0
  return nodes.map(n => {
    if (g.hasNode(n.id)) {const p = g.node(n.id);return {...n,position:{x:p.x-W/2,y:p.y-H/2}}}
    const i = loose++; return {...n,position:{x:(i%5)*(W+65),y:bottom+Math.floor(i/5)*(H+35)}}
  })
}
export function knowledgeLayout(detail: KnowledgeDetail, moduleId: string | null) {
  const modules = detail.stages.flatMap(s => s.modules)
  const current = modules.find(m => m.points.some(p=>p.taught) && m.state !== "passed" && m.state !== "skipped") ?? modules.find(m=>m.state !== "passed" && m.state !== "skipped") ?? modules[0]
  const nodes: MapNode[] = [], edges: Edge[] = []
  const selected = modules.find(m=>m.id===moduleId)
  if (!selected) {
    nodes.push({id:"goal",type:"mapCard",position:{x:0,y:0},data:{title:detail.plan.goal,label:"计划目标",tone:"goal"},style:{width:W,height:H},draggable:false})
    const ids = new Set(modules.map(m=>m.id))
    detail.stages.forEach((s,si)=>s.modules.forEach((m)=>{
      const [label,tone] = moduleProgress(m)
      nodes.push({id:m.id,type:"mapCard",position:{x:0,y:0},data:{title:m.title,summary:m.summary,label,tone,current:m.id===current?.id,
        stage:`阶段 ${si+1} · ${s.title}`,number:modules.indexOf(m)+1,
        tags:`${m.question_count === undefined ? "题数暂不可用" : `题目 ${m.question_count} 道，已过 ${m.passed_questions ?? 0} 道`} · ${m.points.length} 个点 · ${m.learning_minutes+m.check_minutes} 分钟`},style:{width:W,height:H}})
      const previous = (m.prerequisites??[]).filter(id=>ids.has(id)&&id!==m.id)
      if (!previous.length) edges.push(edge(`goal:${m.id}`,"goal",m.id))
      previous.forEach(p=>edges.push(edge(`${p}:${m.id}`,p,m.id)))
    }))
  } else {
    const points = new Set(selected.points.map(p=>p.id))
    selected.points.forEach((p,i)=>{const [label,tone]=pointProgress(p);nodes.push({id:p.id,type:"mapCard",position:{x:0,y:0},
      data:{title:p.title,summary:p.summary,label,tone,number:i+1,tags:`${p.minutes} 分钟`},style:{width:W,height:H}})})
    const owners = new Map(modules.flatMap(m=>m.points.map(p=>[p.id,m] as const)))
    const external = new Set<string>()
    const links = detail.point_links_status === "none" || detail.point_links_status === "running" ? [] : detail.point_links??[]
    for (const [i,l] of links.entries()) {
      const insideFrom=points.has(l.from), insideTo=points.has(l.to)
      if (insideFrom && insideTo && l.from!==l.to) edges.push(edge(`point:${i}`,l.from,l.to,l.kind==="related"))
      else if (l.kind==="prerequisite" && insideFrom !== insideTo) {
        const other=owners.get(insideFrom?l.to:l.from)
        if (!other || other.id===selected.id) continue
        const tag=`external:${other.id}`
        if (!external.has(tag)) {external.add(tag);nodes.push({id:tag,type:"mapCard",position:{x:0,y:0},data:{title:other.title,label:"跨模块 · 点此跳转",tone:"external"},style:{width:W,height:H}})}
        edges.push(edge(`external-link:${i}`,insideFrom?l.from:tag,insideTo?l.to:tag))
      }
    }
  }
  return {nodes:layout(nodes,edges,!!selected),edges,current,selected,modules}
}
export function PointSources({point:p}:{point:KnowledgePoint}) {
  return <div className="km-sources">{p.citations?.length ? p.citations.map((c,i)=><details className="pg-citation" key={`${c.chunk_id}:${i}`}>
    <summary>依据：{c.title} · {c.locator}</summary><div className="km-markdown"><ClassroomMarkdown content={c.quote}/></div>
    <small>{c.chunk_id}</small>{c.url && <a href={c.url} target="_blank" rel="noreferrer">打开原文</a>}
  </details>) : <p className="muted small">未返回出处原文</p>}</div>
}
export function KnowledgeMap({detail,onRetry,busy}:{detail:KnowledgeDetail;onRetry:()=>void;busy?:boolean}) {
  const [moduleId,showModule]=React.useState<string|null>(null)
  const [pointId,showPoint]=React.useState<string|null>(null)
  const reduce = useApp(s=>s.settings?.reduce_motion) || matchMedia('(prefers-reduced-motion: reduce)').matches
  const view = React.useMemo(()=>knowledgeLayout(detail,moduleId),[detail,moduleId])
  const [nodes,setNodes]=React.useState(view.nodes)
  const [instance,setInstance]=React.useState<ReactFlowInstance|null>(null)
  const canvas = React.useRef<HTMLDivElement>(null)
  React.useEffect(()=>{setNodes(view.nodes)},[view])
  const point=view.selected?.points.find(p=>p.id===pointId)
  const status=detail.point_links_status??"none"
  function center(x:number,y:number,zoom:number) {
    const width=canvas.current?.clientWidth??800, height=canvas.current?.clientHeight??520
    // The internal viewport width also lags when the source drawer closes.
    // DOM dimensions already reflect this render; use them for translation.
    return instance?.setViewport({x:width/2-x*zoom,y:height/2-y*zoom,zoom},{duration:reduce?0:260})
  }
  React.useEffect(()=>{
    if (!instance) return
    const frame=requestAnimationFrame(()=>{
      if (view.selected) {
        const target=view.nodes.find(n=>n.id===(view.selected?.points.find(p=>!p.taught)?.id))??view.nodes[0]
        if (target) center(target.position.x+W/2,target.position.y+H/2,.85)
      } else {
        // React Flow's internal node measurements may still belong to the
        // previous module. Fit the current fixed-size layout, not that store.
        const minX=Math.min(...view.nodes.map(n=>n.position.x)), minY=Math.min(...view.nodes.map(n=>n.position.y))
        const maxX=Math.max(...view.nodes.map(n=>n.position.x+W)), maxY=Math.max(...view.nodes.map(n=>n.position.y+H))
        const zoom=Math.max(.04,Math.min(1,(canvas.current?.clientWidth??800)/((maxX-minX)*1.2),(canvas.current?.clientHeight??520)/((maxY-minY)*1.2)))
        center((minX+maxX)/2,(minY+maxY)/2,zoom)
      }
    })
    return ()=>cancelAnimationFrame(frame)
    // Relationship events update the graph, but must not move the viewport
    // away from the point or source the user is currently reading.
  },[instance,moduleId,reduce])
  React.useEffect(()=>{const esc=(e:KeyboardEvent)=>{if(e.key==="Escape")showPoint(null)};document.addEventListener('keydown',esc);return()=>document.removeEventListener('keydown',esc)},[])
  const open=(id:string)=>{showPoint(null);showModule(id)}
  function resume(){
    if (!view.current) return
    if (view.current.id !== moduleId) { open(view.current.id); return }
    const next = view.current.points.find(p=>!p.taught) ?? view.current.points[0]
    const target = view.nodes.find(n=>n.id===next?.id)
    if (target) center(target.position.x+W/2,target.position.y+H/2,.85)
  }
  return <ReactFlowProvider><section className={`plan-graph knowledge-map ${reduce?"km-reduced":""}`} data-testid="detail-map" data-module-id={moduleId??""} data-point-count={view.selected?.points.length??0} aria-label="可交互知识地图">
    <div className="pg-toolbar"><div><BookOpen size={17}/><b>{view.selected?.title??"路线总览"}</b><span>{view.selected?`${view.selected.points.length} 个知识点`:`${view.modules.length} 个模块 · 按题统计掌握情况`}</span></div>
      <div>{moduleId && <button className="pg-expand" onClick={()=>{showModule(null);showPoint(null)}}><ArrowLeft size={14}/>返回总览</button>}
        <button className="pg-expand" onClick={resume}><LocateFixed size={14}/>回到我学到哪</button></div></div>
    {moduleId && view.nodes.some(n=>n.id.startsWith("external:")) && <div className="km-cross-modules" aria-label="跨模块前置关系">
      {view.nodes.filter(n=>n.id.startsWith("external:")).map(n=><button className="pg-expand" key={n.id} data-external-id={n.id.slice(9)} onClick={()=>open(n.id.slice(9))}>
        {view.edges.some(e=>e.source===n.id)?"先学":"后续"}：{n.data.title} →</button>)}
    </div>}
    <div className="pg-workspace km-workspace">
      <div className="pg-canvas" ref={canvas}><ReactFlow nodes={nodes} edges={view.edges} nodeTypes={nodeTypes} onInit={setInstance}
        onNodesChange={c=>setNodes(n=>applyNodeChanges(c,n))} onNodeClick={(_,n)=>{
          if(n.id==="goal")return
          if(n.id.startsWith("external:"))open(n.id.slice(9))
          else if(moduleId)showPoint(n.id)
          else open(n.id)
        }} nodesConnectable={false} deleteKeyCode={null} minZoom={.04} maxZoom={2} fitView onlyRenderVisibleElements
        fitViewOptions={{padding:.15,minZoom:.05,maxZoom:1}}>
        <Background variant={BackgroundVariant.Dots} color="var(--border-strong)" gap={20}/><Controls position="bottom-right" showInteractive={false}/>
        <MiniMap position="bottom-left" style={{width:110,height:70}} pannable zoomable nodeColor={n=>n.data.tone==="green"?"var(--ok)":n.data.tone==="red"?"var(--danger)":"var(--accent)"}/>
      </ReactFlow></div>
      {point && <aside className="pg-details km-details" aria-label="知识点详情"><header><span className="pg-eyebrow">知识点详情</span><button className="pg-icon" onClick={()=>showPoint(null)}><X size={16}/>关闭</button></header>
        <h3>{point.title}</h3><p>{point.summary}</p><p className="pg-meta">{point.minutes} 分钟 · {pointProgress(point)[0]}</p><h4>出处原文 · 点开查看</h4><PointSources key={point.id} point={point}/></aside>}
    </div>
    <footer className="km-footer"><span><i className="km-line"/>前置关系（箭头）</span><span><i className="km-line dashed"/>相关关系</span>
      {status==='running' ? <Loading label="正在整理知识点之间的关系…" onRetry={onRetry}/>
        : status==='none' ? <span className="km-links-status" role="status">知识点关系尚未整理</span>
        : <span className="km-links-status" role="status">{status==="partial"?"部分模块关系整理失败":status==="failed"?"知识点关系整理失败":"知识点关系已整理"}</span>}
      {(status==="partial"||status==="failed") && <button className="pg-expand" disabled={busy} onClick={onRetry}>{busy?"正在重试…":"重试"}</button>}
      <select aria-label="选择地图模块" value={moduleId??""} onChange={e=>{showModule(e.target.value||null);showPoint(null)}}><option value="">路线总览</option>{view.modules.map(m=><option key={m.id} value={m.id}>{m.title}</option>)}</select>
    </footer>
  </section></ReactFlowProvider>
}
