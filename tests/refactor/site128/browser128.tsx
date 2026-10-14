import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {RecommendationCards} from '../../../refactor/learning/web/src/pages/NewPlan'
import '../../../refactor/learning/web/src/theme.css'
const warning='网站的 robots.txt 暂时读不到，整本读取请稍后重试。'
createRoot(document.getElementById('root')!).render(<RecommendationCards cards={[{card_id:'a',title:'普通单页',url:'https://example.edu/',type:'网页',why:'离线替身',modules:[],status:'adopted',source_id:'a',chars:5000,read_warning:warning} as any]} busy={false} onUse={()=>{}}/>)
setTimeout(()=>{
 const note=[...document.querySelectorAll('[role="note"]')].find(v=>v.textContent===warning)
 ;(window as any).conversationResult={ok:!!note&&document.body.textContent!.includes('已采用'),text:document.body.textContent,checks:['single-page-adopted','robots-warning-visible']}
},100)
