import * as React from "react"
import { BookOpen, ListChecks } from "lucide-react"
import "./focus.css"

export type RecapContent = {
  title: string
  items: { title: string; text: string; chunk_id?: string | null }[]
}

export type RecapCardProps = {
  /** Content of a standalone kind="recap" classroom message. */
  content: RecapContent
  /** Open this chunk using the host's existing source viewer. */
  onSource?: (chunkId: string) => void
}

export function RecapCard({ content, onSource }: RecapCardProps) {
  const heading = React.useId()
  return <section className="recap-card" aria-labelledby={heading}>
    <header className="recap-header"><span className="recap-icon"><ListChecks size={20} aria-hidden="true" /></span>
      <div><span className="recap-label">要点卡</span><h3 id={heading}>{content.title}</h3></div></header>
    <ul className="recap-items">{content.items.map((item, index) => <li className="recap-row" key={index}>
      <span className="recap-number" aria-hidden="true">{index + 1}</span>
      <div className="recap-line"><strong>{item.title}</strong><span>{item.text}</span></div>
      {item.chunk_id && <button type="button" className="recap-source" disabled={!onSource}
        aria-label={`查看${item.title}的原文`} onClick={() => onSource?.(item.chunk_id!)}>
        <BookOpen size={13} aria-hidden="true" />原文</button>}
    </li>)}</ul>
  </section>
}
