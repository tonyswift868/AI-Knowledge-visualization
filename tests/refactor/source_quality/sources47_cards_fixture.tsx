import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {GroupedSources} from '../../../refactor/learning/web/src/pages/NewPlan'

const cards: any[] = [
  {card_id:'official',kind:'url',url:'https://docs.python.org/3/functions',title:'函数与参数',site:'docs.python.org',credibility:5,relevance:5,status:'new',chars:2000,note:'',quality:{source_type:'official_docs',covered_modules:['函数','参数']}},
  {card_id:'fallback',kind:'url',url:'https://example.org/article',title:'日常网络概念',site:'example.org',credibility:3,relevance:5,status:'new',chars:2000,note:'',quality:{source_type:'general_site',covered_modules:['网络']}}
]
createRoot(document.getElementById('root')!).render(<GroupedSources cards={cards} locked={false} onDecide={()=>{}} />)
setTimeout(()=>{
  const text=document.body.textContent || ''
  const inputs=Array.from(document.querySelectorAll('input[type=checkbox]')) as HTMLInputElement[]
  ;(window as any).plan37Result={ok:text.includes('覆盖：函数、参数') && text.includes('一般网站，可能有错，请自行判断；不默认采用。') && !text.includes('可信依据：') && inputs.length===2 && inputs.every(i=>!i.checked),text,checks:inputs.length}
},100)
