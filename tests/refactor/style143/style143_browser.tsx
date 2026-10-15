import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { ClassroomMarkdown } from '../../../refactor/learning/web/src/classroom/Markdown'
import '../../../refactor/learning/web/src/theme.css'

function App() {
  React.useEffect(() => {
    void (async () => {
    const checks: string[] = []
    const plain = document.querySelector('[data-test="plain"]')!
    const plainBodies = [...plain.querySelectorAll<HTMLElement>('.focus-body')]
    const reveals = [...plain.querySelectorAll<HTMLElement>('.focus-reveal')]
    const plainOk = reveals.every(e => getComputedStyle(e).display === 'none') && plainBodies.length === 2 &&
      plainBodies.every(e => getComputedStyle(e).display !== 'none') &&
      plain.textContent?.includes('没有荧光标记的完整回复') === true
    checks.push(`plain=${plainOk}; reveal-displays=${reveals.map(e => getComputedStyle(e).display).join(',')}; bodies=${plainBodies.length}; displays=${plainBodies.map(e => getComputedStyle(e).display).join(',')}; text=${plain.textContent}`)
    const marked = document.querySelector('[data-test="marked"]')!
    const marks = marked.querySelectorAll('mark.hl')
    checks.push(`mark-count=${marks.length === 5}`)
    checks.push(`question-highlight=${[...marks].some(m => m.textContent?.includes('这是一个问题？'))}`)
    const markBlocks = [...marks].map(m => m.closest<HTMLElement>('.focus-block'))
    const markedVisible = markBlocks.every(block => !!block && getComputedStyle(block.querySelector('.focus-body')!).display !== 'none')
    checks.push(`marked-visible=${markedVisible}; mark-displays=${markBlocks.map(e => e ? getComputedStyle(e.querySelector('.focus-body')!).display : 'missing').join(',')}`)
    const supplemental = [...marked.querySelectorAll<HTMLButtonElement>('.focus-reveal')]
      .find(button => getComputedStyle(button).display !== 'none')
    const supplementalBody = supplemental?.parentElement?.querySelector<HTMLElement>('.focus-body')
    const startsFolded = !!supplemental && !!supplementalBody && getComputedStyle(supplementalBody).display === 'none'
    supplemental?.click()
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    const expands = !!supplemental && supplemental.getAttribute('aria-expanded') === 'true' &&
      !!supplementalBody && getComputedStyle(supplementalBody).display !== 'none'
    checks.push(`supplement-expands=${startsFolded && expands}`)
    Object.assign(window, { style143Result: { ok: plainOk && marks.length === 5 &&
      [...marks].some(m => m.textContent?.includes('这是一个问题？')) && markedVisible && startsFolded && expands, checks } })
    })()
  }, [])

  return <main style={{maxWidth: 820, margin: '24px auto', padding: 20}}>
    <h1>只看重点兜底</h1>
    <section data-test="plain" aria-label="无荧光完整回复">
      <ClassroomMarkdown focus content={'没有荧光标记的完整回复，仍然全部显示。\n\n第二段也保持可见，用户可以读完老师内容。'} />
    </section>
    <section data-test="marked" aria-label="多个重点和问句">
      <ClassroomMarkdown focus content={'==重点一。==\n\n补充说明一。\n\n==重点二。==\n\n补充说明二。\n\n==重点三。==\n\n==重点四。==\n\n==这是一个问题？=='} />
    </section>
  </main>
}

createRoot(document.getElementById('root')!).render(<App />)
