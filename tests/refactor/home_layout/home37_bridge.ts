const targets = [24, 60, 30, 0]
const titles = [
  '从零开始掌握 Python 列表、元组、循环和函数，能够独立编写整理学习资料的工具，并解释每一步为什么这样写。'.repeat(4),
  'VeryLongUnbrokenPlanTitle'.repeat(18),
  '一元一次方程 · 最终检验',
  '世界历史 · 今天休息',
]
export function call(method: string) {
  if (method !== 'plans.list') throw new Error(`unexpected ${method}`)
  return Promise.resolve({ study_day: '2026-10-03', plans: titles.map((title, i) => ({
    id: `home37-${i}`, title, goal: title, final_goal: '', state: i === 2 ? 'checking' : 'active',
    archived: false, module_count: 3, window: null, total_minutes: 400,
    today: { today_target: targets[i], today_confirmed: [0, 20, 12, 0][i], today_tentative: i === 0 ? 10 : 0,
      debt_minutes: i === 1 ? 120 : 0, needs_negotiation: i === 1, ahead_minutes: i === 2 ? 30 : 0,
      ahead_days: i === 2 ? 1 : 0, remaining_minutes: 100, overdue_days: i === 1 ? 3 : 0, completed: false },
  })) })
}
export function on() { return () => {} }
