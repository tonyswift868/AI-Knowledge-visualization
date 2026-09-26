import * as React from "react"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { Teacher2DStage, normalizePresentationState } from "../../../../../frontend/src/features/teachers/presentation/Teacher2DStage"

import "../../../../../frontend/src/features/teachers/presentation/teacherPresentation.css"
import "../../../../../frontend/src/features/teachers/presentation/teacherPresentationWarm.css"
import "../../../../../frontend/src/features/teachers/presentation/imageTeacher.css"
import "../../../../../frontend/src/features/teachers/presentation/layeredTeacher.css"

import "./stage.css"

const DEFAULT_MANIFEST = "mentorflow://assets/tsubaki-live2d/manifest.json"
const STAGE_STATES = new Set([
  "loading", "idle", "greeting", "thinking", "talking", "explaining", "questioning",
  "praising", "encouraging", "confused", "shy", "playful_annoyed", "ending", "error", "hidden",
])

function stateFromValue(value: unknown) {
  const candidate = typeof value === "string" ? value : "idle"
  return STAGE_STATES.has(candidate) ? normalizePresentationState(candidate) : "idle"
}

function queryManifest() {
  const value = new URLSearchParams(window.location.search).get("manifest")
  return value || DEFAULT_MANIFEST
}

function App() {
  const [state, setState] = React.useState("idle")
  const [theme, setTheme] = React.useState("sakura")
  const [reply, setReply] = React.useState("")
  React.useEffect(() => {
    const receive = (event: MessageEvent) => {
      const message = event.data
      if (!message || typeof message !== "object") return
      if (message.type === "mentorflow.teacher-state") setState(stateFromValue(message.state))
      if (message.type === "mentorflow.teacher-theme" && typeof message.theme === "string") setTheme(message.theme)
      if (message.type === "mentorflow.teacher-reply" && typeof message.text === "string") setReply(message.text.slice(0, 4000))
    }
    window.addEventListener("message", receive)
    return () => window.removeEventListener("message", receive)
  }, [])
  return <main className={`classroom-teacher-stage theme-${theme}`} data-testid="classroom-teacher-stage" data-stage-state={state}>
    {reply && <aside className="classroom-teacher-reply" aria-label="椿老师的最新回复" aria-live="polite">{reply}</aside>}
    <Teacher2DStage teacherName="椿" state={state} manifestUrl={queryManifest()} compact cameraPreset="full" onRuntimeChange={runtime => { document.title = runtime === "live2d" ? "ready" : runtime === "error" ? "error" : runtime === "loading" ? "loading" : document.title }} />
  </main>
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>)
