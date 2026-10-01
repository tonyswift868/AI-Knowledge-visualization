// Explicit offline data for the batch-25 contract. No production mocks added.
import type { Lesson } from "../../../refactor/learning/web/src/classroom/GuidedLesson"

type Message = { id: string; role: "assistant" | "user" | "system"; kind: string; content: string }
let serial = 0
export const message = (kind: string, content: string, role: Message["role"] = "assistant"): Message =>
  ({ id: `fixture-${++serial}`, role, kind, content })
export const firstText = "想象一个天平：两边重量相等，天平就平衡。**等式**也是这样，等号两边表示同一个数。比如 $x + 3 = 8$，意思是一个未知数加上 3，与 8 一样大。接下来我们把两边同时减去 3，让未知数单独留在一边；保持两边做同样的操作，等式才不会失去平衡。[教材原文](mf-source:chunk-equality)"
export const guideOptions = ["只把左边减去 3", "等号两边都减去 3", "把两边都加上 3"]
export const casualOptions = ["x = 2", "x = 5", "x = 8", "x = 11"]
export const secondOptions = ["y = 4", "y = 7", "y = 10", "y = 13"]
export function lesson(step = 1, awaiting: Lesson["awaiting"] = "continue"): Lesson {
  return { point_id: "p1", point_title: "等式的性质", step, total: 3, awaiting,
    options: awaiting === "choice" ? guideOptions : [] }
}
export function snapshot() {
  return {
    plan: { id: "guided-plan", goal: "从等式到一元一次方程", state: "active" },
    module: { id: "m1", title: "用天平理解等式", number: 1, total: 3, state: "active" },
    points: [{ id: "p1", title: "等式的性质", minutes: 12, taught: false, state: "unknown", current: true },
      { id: "p2", title: "移项与解方程", minutes: 15, taught: false, state: "unknown", current: false }],
    step: "teach", question: null as null | { type: "choice"; kind: string; stem: string; options: string[] },
    lesson: lesson() as Lesson | null, messages: [message("step", firstText)], can_skip: false,
    red: [], more_modules: true, check: null,
    today: { today_target: 30, today_confirmed: 0, today_tentative: 0 },
  }
}
export function casual(room = snapshot(), second = false) {
  const stem = second ? "再试一个：y + 3 = 10，y 是多少？" : "随口问：x + 3 = 8，x 是多少？"
  return { ...room, step: "answer", lesson: lesson(3, null),
    question: { type: "choice" as const, kind: "casual", stem, options: second ? secondOptions : casualOptions },
    messages: [...room.messages, message("casual", stem)] }
}
export function guide(room = snapshot()) {
  return { ...room, lesson: lesson(2, "choice"), messages: [...room.messages,
    message("step", "为了让 $x$ 单独留在左边，应该怎么处理等号两边？像天平一样，同时做同样的操作才能保持平衡。[教材原文](mf-source:chunk-equality)")] }
}
export function guideReply(room: ReturnType<typeof snapshot> = guide(), option = 1) {
  return { ...room, lesson: lesson(3), messages: [...room.messages,
    message("choice", guideOptions[option], "user"),
    message("step_reply", option === 1 ? "这个想法很合适！两边同时减去 3，得到 $x = 5$。这一步保持了等式的平衡。" : "可以用天平检查一下：两边必须做同样的操作，才能保持平衡。两边同时减去 3，就得到 $x = 5$。"),
    message("step", "记住这个方法：等式两边同时加上或减去同一个数，等式仍然成立。现在来一道随口问，确认一下。") ] }
}
export function returned(room = guideReply()) {
  return { ...room, lesson: { ...room.lesson!, awaiting: "return" as const }, messages: [...room.messages,
    message("speech", "为什么两边要做同样的操作？", "user"),
    message("speech", "可以想象天平两边各拿走同样重量的物品：原来平衡，拿走后还是平衡。回到刚才，我们接着看等式的性质。") ] }
}
export const report = (live = true) => ({
  report: {
    plan: { goal: "从等式到一元一次方程", final_goal: "能用等式性质解释并解一元一次方程。", start_date: "2026-09-28", end_date: "2026-10-03",
      actual_start_date: "2026-09-28", actual_completion_date: "2026-10-01", early_days: 2, overdue_days: 0, replan_count: 0, planned_minutes: 90, credited_minutes: 90 },
    modules: [{ module_id: "m1", title: "用天平理解等式", state: "passed" }],
    points: [{ point_id: "p1", module_id: "m1", summary: "等式的性质", state: "green", casual_errors: 1, module_errors: 0, check_questions: 2, check_correct: 2 }],
    weakest_points: [{ point_id: "p1", score: 1 }], point_comments: { p1: "曾只对等号一边做操作，现在能正确保持平衡。" },
    teacher_profile: { text: "你习惯用生活中的例子理解概念。" },
    live_points: live ? [{ point_id: "p2", title: "移项与解方程" }, { point_id: "p3", title: "含括号的方程" }] : [],
  },
  titles: { p1: "等式的性质" },
  views: { modules: [{ module_id: "m1", title: "用天平理解等式", mastered: 2, total: 2 }],
    history: [{ day: "2026-09-28", target_minutes: 30, actual_minutes: 30, confirmed_minutes: 30, tentative_minutes: 0, debt_minutes: 0, ahead_minutes: 0 }],
    errors: { casual: 1, module: 0, check: 0 } },
})
