import { ChevronDown, ChevronUp, FlipHorizontal2, ListVideo, Maximize2, Minimize2, Pause, Play, RotateCcw, Settings2, Shirt } from "lucide-react"

type Props = {
  collapsed: boolean
  paused: boolean
  mirrored: boolean
  debug: boolean
  animations: string[]
  selectedAnimation: string
  skins: string[]
  selectedSkin: string
  onCollapsedChange: (value: boolean) => void
  onPausedChange: (value: boolean) => void
  onMirroredChange: (value: boolean) => void
  onDebugChange: (value: boolean) => void
  onZoom: (delta: number) => void
  onReset: () => void
  onAnimationChange: (value: string) => void
  onSkinChange: (value: string) => void
}

export function teacherSkinLabel(name: string): string {
  if (name === "teaching") return "教学形态"
  if (name === "swimsuit") return "泳装形态"
  if (name === "default") return "银白发"
  if (name === "red-hair") return "山茶红发"
  return name
}

export function Teacher2DToolbar({ collapsed, paused, mirrored, debug, animations, selectedAnimation, skins, selectedSkin, onCollapsedChange, onPausedChange, onMirroredChange, onDebugChange, onZoom, onReset, onAnimationChange, onSkinChange }: Props) {
  return <nav className="teacher-2d-toolbar" aria-label="2D 教师控制栏">
    <button type="button" aria-expanded={!collapsed} aria-label={collapsed ? "展开教师舞台" : "收起教师舞台"} title={collapsed ? "展开" : "收起"} onClick={() => onCollapsedChange(!collapsed)}>{collapsed ? <ChevronUp size={16} /> : <ChevronDown size={16} />}<span>{collapsed ? "展开" : "收起"}</span></button>
    <label className="teacher-2d-animation-picker"><ListVideo size={16} /><span>动作</span><select aria-label="真实动画列表" value={selectedAnimation} onChange={event => onAnimationChange(event.target.value)} disabled={!animations.length}><option value="">选择动画</option>{animations.map(name => <option value={name} key={name}>{name}</option>)}</select></label>
    {skins.length > 1 && <label className="teacher-2d-skin-picker"><Shirt size={16} /><span>形态</span><select aria-label="教师形态" value={selectedSkin} onChange={event => onSkinChange(event.target.value)}>{skins.map(name => <option value={name} key={name}>{teacherSkinLabel(name)}</option>)}</select></label>}
    <button type="button" aria-label="放大教师" title="放大" onClick={() => onZoom(.12)}><Maximize2 size={16} /><span>放大</span></button>
    <button type="button" aria-label="缩小教师" title="缩小" onClick={() => onZoom(-.12)}><Minimize2 size={16} /><span>缩小</span></button>
    <button type="button" aria-pressed={mirrored} aria-label="左右镜像教师" title="镜像" className={mirrored ? "active" : ""} onClick={() => onMirroredChange(!mirrored)}><FlipHorizontal2 size={16} /><span>镜像</span></button>
    <button type="button" aria-label="恢复教师默认状态" title="恢复默认" onClick={onReset}><RotateCcw size={16} /><span>复位</span></button>
    <button type="button" aria-pressed={paused} aria-label={paused ? "继续教师动画" : "暂停教师动画"} title={paused ? "继续" : "暂停"} onClick={() => onPausedChange(!paused)}>{paused ? <Play size={16} /> : <Pause size={16} />}<span>{paused ? "继续" : "暂停"}</span></button>
    <button type="button" aria-pressed={debug} aria-label="显示或隐藏教师调试信息" title="调试信息" className={debug ? "active" : ""} onClick={() => onDebugChange(!debug)}><Settings2 size={16} /><span>调试</span></button>
  </nav>
}
