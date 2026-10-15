import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {useRoundScroll} from './hook139'
import {useApp} from '../../../refactor/learning/web/src/store'
import {RecapCard} from '../../../refactor/learning/web/src/classroom/RecapCard'
import '../../../refactor/learning/web/src/theme.css'

const root=createRoot(document.getElementById('root')!)
let generation=0
function Fixture({own}:{own:number}) {
 const scroller=React.useRef<HTMLDivElement>(null)
 const begin=useRoundScroll(scroller,'scroll139-'+own,true)
 const [turn,setTurn]=React.useState(0),[mode,setMode]=React.useState('teacher'),[chunks,setChunks]=React.useState(0)
 React.useEffect(()=>{
  ;(window as any).control139={own,round:(value='teacher')=>{begin();setMode(value);setChunks(0);setTurn(t=>t+1)},grow:()=>setChunks(n=>n+1)}
 },[begin,own])
 return <div ref={scroller} className="class-scroll probe139" tabIndex={0} data-generation={own}
  style={{height:480,width:680,overflowY:'scroll',boxSizing:'border-box',padding:'12px 16px'}}>
  {Array.from({length:12},(_,i)=><article data-turn-key={'old139-'+i} key={i} style={{height:92}}>之前的讲解第{i+1}轮。</article>)}
  {turn>0&&<React.Fragment key={turn}>
   {mode==='recap'&&<div data-turn-key={'recap139-'+turn} data-turn-kind="recap" data-turn-role="assistant">
    <RecapCard content={{title:'队列与栈',items:[{title:'队列',text:'队列先进先出。'},{title:'栈',text:'栈后进先出。'}]}}/>
   </div>}
   <article data-turn-key={'teacher139-'+turn} data-turn-role="assistant" style={{minHeight:70}}>
    <strong>下一条老师回复，正在流式输出。</strong>
    {Array.from({length:chunks},(_,i)=><p key={i} style={{height:48,margin:'8px 0'}}>第{i+1}段：先确认条件，再比较压力与面积的关系。</p>)}
   </article>
  </React.Fragment>}
 </div>
}
;(window as any).mount139=async(reduced=false)=>{
 useApp.setState({settings:{reduce_motion:reduced} as any})
 const own=++generation
 root.render(<Fixture key={own} own={own}/>)
 while((window as any).control139?.own!==own)await new Promise(requestAnimationFrame)
 await new Promise(requestAnimationFrame)
 return own
}
;(window as any).ready139=(window as any).mount139()
