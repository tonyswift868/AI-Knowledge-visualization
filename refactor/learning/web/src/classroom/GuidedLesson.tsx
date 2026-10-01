import { ChevronRight, RotateCcw } from "lucide-react"
import { Button } from "../ui"
import "./guided-lesson.css"

export type Lesson = {
  point_id: string
  point_title: string
  step: number
  total: number
  awaiting: "continue" | "choice" | "return" | null
  options: string[]
}

export function LessonProgress({ lesson }: { lesson: Lesson }) {
  return (
    <div className="lesson-progress" aria-label="当前讲解进度">
      <span>{lesson.point_title}</span>
      <b>第 {lesson.step}/{lesson.total} 步</b>
    </div>
  )
}

export function LessonActions({ lesson, busy, selected, act }: {
  lesson: Lesson
  busy: boolean
  selected: number | null
  act: (method: string, params?: Record<string, unknown>, label?: string) => Promise<boolean>
}) {
  if (lesson.awaiting === "choice") return (
    <div className="lesson-interaction" aria-label="引导小问题">
      <span className="muted small">选一个想法，接着往下看</span>
      <div className="class-options">
        {lesson.options.map((option, index) => (
          <button key={`${index}:${option}`} className={`choice ${selected === index ? "on" : ""}`}
                  disabled={busy} aria-pressed={selected === index}
                  onClick={() => act("classroom.choose", { option: index }, "正在接着讲…")}>
            <b>{index + 1}</b><span>{option}</span>
          </button>
        ))}
      </div>
    </div>
  )
  if (lesson.awaiting === "continue") return (
    <div className="plan-actions lesson-actions">
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.next", {}, "正在接着讲…")}>
        <ChevronRight />继续
      </Button>
      <span className="muted small">也可以按回车继续，或随时打字问老师</span>
    </div>
  )
  if (lesson.awaiting === "return") return (
    <div className="plan-actions lesson-actions">
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.back", {}, "正在回到刚才…")}>
        <RotateCcw />回到刚才
      </Button>
    </div>
  )
  return null
}
