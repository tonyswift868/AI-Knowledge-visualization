import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {PlanTree} from '../../../refactor/learning/web/src/pages/NewPlan'
import '../../../refactor/learning/web/src/theme.css'
const root=createRoot(document.getElementById('root')!)
const pause=()=>new Promise(r=>setTimeout(r,20))
const detail:any={plan:{id:'p',goal:'教材',state:'ready',total_minutes:9,window:null},sources:[],stages:[{id:'s',title:'阶段',modules:[{
  id:'m',title:'教材知识',summary:'按原文目录',learning_minutes:9,check_minutes:0,
  navigation:[{id:'n',title:'书籍推荐',reason:'整节为书单',citations:[{title:'教材',chunk_id:'n',quote:'推荐阅读教材。',locator:'书籍推荐',url:null}]}],
  points:[{id:'p1',title:'队列与栈',summary:'掌握处理顺序',minutes:9,structure:'concept_v1',subpoints:[1,2,3].map(i=>({id:'p1-s'+i,title:'原文小节'+i,minutes:3}))}]
}]}]}
async function run(){
  root.render(<PlanTree detail={detail}/>);await pause();await pause()
  const text=document.body.textContent??''
  if(!text.includes('1 个大知识点')||!text.includes('3 个小要点'))throw Error('Big/small count missing: '+text)
  if(!text.includes('资料导航（1 条，不计学习时长）'))throw Error('Navigation missing')
  const nav=[...document.querySelectorAll('details')][0];nav.open=true;await pause()
  if(!document.body.textContent?.includes('推荐阅读教材。'))throw Error('Original navigation text missing')
  ;(window as any).conversationResult={ok:true,counts:true,navigation:true}
}
run().catch(e=>(window as any).conversationResult={ok:false,error:String(e)})
