import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { Home } from '../../../refactor/learning/web/src/pages/Home'
import { BrandMark } from '../../../refactor/learning/web/src/ui'
import { useApp } from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'

declare global { interface Window { home37Check: () => object } }
document.documentElement.dataset.theme = 'sakura'
document.documentElement.classList.add('reduce-motion')
useApp.getState().set({ ready: true, account: { name: '同学', role_label: '学习者' } })
createRoot(document.getElementById('root')!).render(<div className="shell">
  <aside className="sidebar">
    <div className="brand"><BrandMark /><div className="brand-text"><b>MentorFlow</b><span>你的 AI 学习导师</span></div></div>
    <nav className="nav"><span className="nav-heading">学习</span>
      {['首页', '我的计划', '新建计划', '更多工具', '设置'].map((text, i) => <button key={text} className={`nav-item ${i === 0 ? 'active' : ''}`}>{text}</button>)}
    </nav>
  </aside>
  <main className="main"><header className="topbar"><h1>首页 · 假数据布局检查</h1></header>
    <div className="content"><Home /></div>
  </main>
</div>)

window.home37Check = () => {
  const checks: string[] = []
  const require = (ok: boolean, message: string) => { if (!ok) throw new Error(message); checks.push(message) }
  const cards = [...document.querySelectorAll<HTMLElement>('.today-plan')]
  if (cards.length !== 4) return { ready: false }
  try {
    require(document.querySelectorAll('.home-page .ring').length === 1, '仅有合计圆环')
    require(document.body.textContent!.includes('今天全部计划'), '合计圆环标注全部计划')
    require(document.body.textContent!.includes('今天 0/24 分钟'), '每份计划显示今日进度文字')
    require(document.body.textContent!.includes('已学 10 分（模块检测通过后计入完成）'), '解释待确认时长')
    require(!document.querySelector('.home-page')!.textContent!.includes('暂记'), '首页没有暂记术语')
    const columns = getComputedStyle(document.querySelector('.home-plan-grid')!).gridTemplateColumns.split(' ').length
    require(columns > 1, '多份计划网格排列')
    cards.forEach((card, i) => {
      const box = card.getBoundingClientRect()
      const title = card.querySelector<HTMLElement>('.today-plan-title')!
      require(title.title === title.textContent, `卡片${i}悬停可看完整标题`)
      require(getComputedStyle(title).webkitLineClamp === '2', `卡片${i}标题限制两行`)
      require(title.getBoundingClientRect().height <= parseFloat(getComputedStyle(title).lineHeight) * 2 + 1, `卡片${i}标题高度不超过两行`)
      require(card.scrollWidth <= card.clientWidth + 1, `卡片${i}内部不横向溢出`)
      require(box.left >= 0 && box.right <= window.innerWidth, `卡片${i}在窗口内`)
      for (const el of card.querySelectorAll('b,p,button,span,div')) {
        const r = el.getBoundingClientRect()
        require(r.left >= box.left && r.right <= box.right + 1, `卡片${i}${el.className || el.tagName}在卡片内`)
      }
      const button = card.querySelector<HTMLButtonElement>('.today-plan-start')!
      button.click()
      require(useApp.getState().planId === `home37-${i}` && useApp.getState().route === 'classroom', `卡片${i}开课仍选中对应计划`)
    })
    const content = document.querySelector<HTMLElement>('.content')!
    require(content.scrollWidth <= content.clientWidth + 1, '内容区没有横向溢出')
    return { ready: true, ok: true, width: window.innerWidth, columns, checks }
  } catch (e) { return { ready: true, ok: false, width: window.innerWidth, checks, error: String(e) } }
}
