import { ChevronRight, RotateCcw } from "lucide-react"
import { Button } from "../ui"
import * as React from "react"
import "./guided-lesson.css"

export type Lesson = {
  point_id: string
  point_title: string
  step: number
  total: number
  awaiting: "continue" | "choice" | "return" | null
  options: string[]
}

export type LessonWait = { point_id: string; elapsed_seconds: number; error?: string | null }

export function LessonPreparing({ wait, busy, act }: {
  wait: LessonWait; busy: boolean
  act: (method: string, params?: Record<string, unknown>, label?: string) => Promise<boolean>
}) {
  const [seconds, setSeconds] = React.useState(wait.elapsed_seconds)
  React.useEffect(() => {
    const started = performance.now()
    setSeconds(wait.elapsed_seconds)
    const timer = window.setInterval(() => setSeconds(wait.elapsed_seconds + Math.floor((performance.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [wait.point_id, wait.elapsed_seconds])
  return <div className="class-note" role="status">
    {wait.error ? <><span>{wait.error}</span><Button disabled={busy} onClick={() => act("classroom.next")}>重新准备这一点</Button></>
      : <span>老师正在准备这一点… 已等 {seconds} 秒。备好后会自动接着讲，你可以先打字问老师。</span>}
  </div>
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
