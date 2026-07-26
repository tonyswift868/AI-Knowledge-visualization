import { useMemo, useState } from "react"
import { BookOpenText, Heart, MessageCircle, Palette, Play, Send, Sparkles } from "lucide-react"
import type { TeacherAnimationState } from "./SpineTeacherPlayer"
import { Teacher2DStage } from "./presentation/Teacher2DStage"
import type { TeacherPresentationState } from "./presentation/teacherPresentationTypes"

type TeacherMood = "calm" | "encouraging" | "explaining"
export type TeacherCompanionState = TeacherAnimationState | TeacherPresentationState

const moodCopy: Record<TeacherMood, { label: string; line: string }> = {
  calm: {
    label: "专注",
    line: "我已经准备好了。把今天最难理解的那一段交给我，我们从一个小问题开始。",
  },
  encouraging: {
    label: "鼓励",
    line: "不用急着一次答对。先说出你的判断，我会陪你把证据和推理一步一步补齐。",
  },
  explaining: {
    label: "讲解",
    line: "我会先给直觉，再给定义和例子；任何一步不清楚，都可以随时打断我。",
  },
}

const illustrationByState: Record<"idle" | "explaining" | "encouraging", string> = {
  idle: "/assets/teachers/mentor-iris.png",
  explaining: "/assets/teachers/mentor-iris-explaining.png",
  encouraging: "/assets/teachers/mentor-iris-encouraging.png",
}

export function illustrationForTeacherState(state: TeacherAnimationState) {
  if (state === "encouraging") return illustrationByState.encouraging
  if (state === "explaining" || state === "thinking") return illustrationByState.explaining
  return illustrationByState.idle
}

export function TeacherCompanionPortrait({
  teacherName = "艾莉丝",
  spineManifestUrl,
  state = "idle",
  }: {
  teacherName?: string
  spineManifestUrl?: string
  state?: TeacherCompanionState
}) {
  const [runtime, setRuntime] = useState<"dragonbones" | "error" | "loading">("loading")
  const stateLabel: Record<TeacherCompanionState, string> = {
    loading: "加载",
    idle: "专注",
    listening: "倾听",
    greeting: "问候",
    thinking: "思考",
    talking: "回应",
    explaining: "讲解",
    questioning: "提问",
    praising: "表扬",
    encouraging: "鼓励",
    confused: "困惑",
    ending: "收尾",
    error: "错误",
    hidden: "收起",
  }

  return (
    <section
      className={`teacher-companion-portrait state-${state}`}
      data-testid="teacher-companion-portrait"
      data-visual-mode="2d-skeleton"
      data-runtime-target="dragonbones2d"
      data-teacher-state={state}
      aria-label={`今日 AI 教师 ${teacherName}`}
    >
      <div className="teacher-companion-atmosphere" aria-hidden="true">
        <i /><i /><i /><i /><i />
      </div>
      <div className="teacher-companion-portrait-copy">
        <span>今日 AI 教师</span>
        <h2>{teacherName}</h2>
        <p>陪你完成当前学习回合</p>
      </div>
      <div className="teacher-companion-character">
        <div className="teacher-companion-halo" aria-hidden="true" />
        <Teacher2DStage teacherName={teacherName} state={state} compact onRuntimeChange={setRuntime} />
        <div className="teacher-companion-shadow" aria-hidden="true" />
      </div>
      <div className="teacher-companion-status">
        <i /><span>{stateLabel[state]}模式 · {runtime === "dragonbones" ? "DragonBones Shizuku" : runtime === "loading" ? "正在加载" : "资源错误"}</span>
      </div>
    </section>
  )
}

export function TeacherCompanionStage({
  teacherName = "艾莉丝",
  spineManifestUrl,
}: {
  teacherName?: string
  spineManifestUrl?: string
}) {
  const [mood, setMood] = useState<TeacherMood>("calm")
  const [runtime, setRuntime] = useState<"dragonbones" | "error" | "loading">("loading")
  const [message, setMessage] = useState("")
  const [lastQuestion, setLastQuestion] = useState("")
  const copy = moodCopy[mood]
  const animationState: TeacherAnimationState = mood === "encouraging"
    ? "encouraging"
    : mood === "explaining"
      ? "explaining"
      : "idle"

  const reply = useMemo(
    () => lastQuestion
      ? `我收到了：“${lastQuestion}”。接下来我会先确认你的目标，再用一个具体例子讲清楚。`
      : copy.line,
    [copy.line, lastQuestion],
  )

  function sendMessage() {
    const value = message.trim()
    if (!value) return
    setLastQuestion(value)
    setMood("explaining")
    setMessage("")
  }

  return (
    <section
      className={`teacher-companion-stage mood-${mood}`}
      data-testid="teacher-companion-stage"
      data-visual-mode="2d-skeleton"
      data-runtime-target="dragonbones2d"
      aria-label="2D AI 教师互动舞台"
    >
      <div className="teacher-companion-atmosphere" aria-hidden="true">
        <i /><i /><i /><i /><i />
      </div>

      <div className="teacher-companion-chat">
        <header>
          <div className="teacher-companion-avatar"><Sparkles size={18} /></div>
          <div>
            <span>今日 AI 教师</span>
            <h2>{teacherName}</h2>
          </div>
          <em><i />在线</em>
        </header>

        <div className="teacher-companion-message">
          <span>{teacherName}</span>
          <p>{reply}</p>
        </div>

        <div className="teacher-companion-focus">
          <BookOpenText size={18} />
          <div><span>当前教学方式</span><b>直觉 → 例子 → 检查理解</b></div>
        </div>

        <div className="teacher-companion-compose">
          <textarea
            aria-label="向 AI 教师提问"
            value={message}
            onChange={event => setMessage(event.target.value)}
            onKeyDown={event => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                sendMessage()
              }
            }}
            placeholder="把不理解的地方告诉我…"
          />
          <button type="button" aria-label="发送给 AI 教师" onClick={sendMessage} disabled={!message.trim()}>
            <Send size={17} />
          </button>
        </div>
      </div>

      <div className="teacher-companion-character">
        <div className="teacher-companion-halo" aria-hidden="true" />
        <Teacher2DStage teacherName={teacherName} state={animationState} onRuntimeChange={setRuntime} />
        <div className="teacher-companion-shadow" aria-hidden="true" />
        <div className="teacher-companion-status"><i /><span>{copy.label}模式 · {runtime === "dragonbones" ? "DragonBones Shizuku" : runtime === "loading" ? "正在加载" : "资源错误"}</span></div>
      </div>

      <nav className="teacher-companion-actions" aria-label="教师表现控制">
        <button type="button" className={mood === "calm" ? "active" : ""} onClick={() => setMood("calm")}>
          <MessageCircle size={17} /><span>专注</span>
        </button>
        <button type="button" className={mood === "encouraging" ? "active" : ""} onClick={() => setMood("encouraging")}>
          <Heart size={17} /><span>鼓励</span>
        </button>
        <button type="button" className={mood === "explaining" ? "active" : ""} onClick={() => setMood("explaining")}>
          <Play size={17} /><span>讲解</span>
        </button>
        <button type="button" disabled title="接入授权的 Spine 换装资源后启用">
          <Palette size={17} /><span>换装</span>
        </button>
      </nav>
    </section>
  )
}
