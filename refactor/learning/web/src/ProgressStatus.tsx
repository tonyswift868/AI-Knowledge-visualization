import * as React from "react"
import "./progress-status.css"

export type StudyProgress = {
  today_target: number; today_confirmed: number; today_tentative: number
  debt_minutes?: number; needs_negotiation?: boolean; ahead_minutes?: number
  today_repaid_confirmed?: number; today_repaid_tentative?: number
  ahead_tentative?: number; oldest_debt_day?: string | null
}

/** One mutually exclusive state, with tentative minutes included in its total. */
export function ProgressStatus({ value }: { value: StudyProgress }) {
  const debt = value.debt_minutes ?? 0, ahead = value.ahead_minutes ?? 0
  const mode = debt > 0 ? (value.needs_negotiation ? "severe" : "debt") : ahead > 0 ? "ahead" : "today"
  const reason = debt > 0
    ? `${value.oldest_debt_day ? `${value.oldest_debt_day} 起` : "之前"}没学完的分钟先从今天学的时长里补；已还只统计今天记录且实际填到过去的部分。`
    : ahead > 0 ? "今天和之前的目标已填满，这些分钟已填到未来日期。"
    : "今天的目标进度包含已完成的学习时长。"
  return <span className={`progress-status progress-${mode}`} data-progress={mode} role="status"
    title={reason}>
    {debt > 0 ? <><span>逾期待还 <b>{debt}</b> 分</span>
      <span>今天已还 <b>{(value.today_repaid_confirmed ?? 0) + (value.today_repaid_tentative ?? 0)}</b> 分</span></>
      : ahead > 0 ? <span>超前完成 <b>{ahead}</b> 分</span>
      : <span>今天 <b>{value.today_confirmed + value.today_tentative} / {value.today_target}</b> 分</span>}
  </span>
}

export function aggregateProgress(values: StudyProgress[]): StudyProgress {
  const sum = (key: keyof StudyProgress) => values.reduce((n, v) => n + (typeof v[key] === "number" ? v[key] as number : 0), 0)
  return {
    today_target: sum("today_target"), today_confirmed: sum("today_confirmed"), today_tentative: sum("today_tentative"),
    debt_minutes: sum("debt_minutes"), needs_negotiation: values.some(v => (v.debt_minutes ?? 0) > 0 && v.needs_negotiation),
    // A plan with future minutes cannot mask another plan's remaining debt.
    ahead_minutes: sum("ahead_minutes"), ahead_tentative: sum("ahead_tentative"),
    today_repaid_confirmed: sum("today_repaid_confirmed"), today_repaid_tentative: sum("today_repaid_tentative"),
    oldest_debt_day: values.filter(v => (v.debt_minutes ?? 0) > 0 && v.oldest_debt_day).map(v => v.oldest_debt_day!).sort()[0] ?? null,
  }
}
