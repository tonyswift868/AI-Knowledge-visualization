import { useState } from 'react'
import type { Citation } from '../types'
import { GroundingBadge } from './GroundingBadge'
export function CitationCard({citation}:{citation:Citation}){const [open,setOpen]=useState(false);return <button className="citation-card" onClick={()=>setOpen(x=>!x)}><div><b>{citation.source_title}</b><span>{citation.page_start ? `第 ${citation.page_start}${citation.page_end&&citation.page_end!==citation.page_start?`-${citation.page_end}`:''} 页` : citation.heading || '章节来源'}</span><GroundingBadge status={citation.status||'grounded'}/></div>{open&&<blockquote>{citation.quote}</blockquote>}<small>{citation.trust_level||'reference'} · {open?'收起原文':'展开原文'}</small></button>}
