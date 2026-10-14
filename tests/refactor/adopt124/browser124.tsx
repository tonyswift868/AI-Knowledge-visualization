import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {RecommendationCards} from '../../../refactor/learning/web/src/pages/NewPlan'
import '../../../refactor/learning/web/src/theme.css'
const root=createRoot(document.getElementById('root')!)
const pause=()=>new Promise(r=>setTimeout(r,20))
async function until(fn:()=>boolean){for(let i=0;i<120;i++){if(fn())return;await pause()}throw Error('UI timeout: '+document.body.textContent)}
let cancel:()=>void=()=>{}
const seen:string[]=[]
function Test(){
  const [cards,setCards]=React.useState<any[]>([{card_id:'ok',title:'网页教材',url:'https://example.edu/lesson',type:'文档',why:'真实测试替身',modules:['基础'],credibility:3,status:'new'},
    {card_id:'bad',title:'登录资料',url:'https://example.edu/locked',type:'文档',why:'读取会失败的明确替身',modules:['基础'],credibility:3,status:'new'}])
  const generation=React.useRef(0)
  function use(url:string){
    seen.push(url);const own=++generation.current
    setCards(cs=>cs.map(c=>c.url===url?{...c,read_status:'reading',read_error:''}:c))
    const timer=setTimeout(()=>{if(own!==generation.current)return;setCards(cs=>cs.map(c=>c.url===url?
      url.endsWith('locked')?{...c,read_status:'failed',read_error:'需要登录，请下载后上传。'}:{...c,status:'adopted',read_status:'ready',chars:3800}:c))},400)
    cancel=()=>{generation.current++;clearTimeout(timer);setCards(cs=>cs.map(c=>c.url===url?{...c,read_status:'cancelled',read_error:'已取消读取。'}:c))}
  }
  return <RecommendationCards cards={cards} busy={cards.some(c=>c.read_status==='reading')} onUse={use} onCancel={()=>cancel()}/>
}
root.render(<Test/>)
async function run(){
  await until(()=>document.querySelectorAll('input[type=checkbox]').length===2)
  const checkbox=(n:number)=>document.querySelectorAll<HTMLInputElement>('input[type=checkbox]')[n]
  if(checkbox(0).checked||checkbox(1).checked)throw Error('Default adopted')
  checkbox(0).click();await until(()=>document.body.textContent!.includes('正在读取…'))
  const stop=[...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent==='取消读取')!
  if(!stop||stop.disabled)throw Error('Cannot cancel locked reading')
  stop.click();await until(()=>document.body.textContent!.includes('已取消读取。'))
  checkbox(0).click();await until(()=>checkbox(0).checked)
  if(!document.body.textContent!.includes('约 3.8 千字'))throw Error('Missing size')
  checkbox(1).click();await until(()=>!!document.querySelector('[role=alert]'))
  if(checkbox(1).checked||!document.body.textContent!.includes('下载后上传'))throw Error('Failed link adopted')
  if(!document.body.textContent!.includes('一般网站，可能有错，请自行判断；不默认采用。'))throw Error('General source notice lost')
  ;(window as any).conversationResult={ok:true,checks:['checkbox','reading','cancel','adopted','size','failed','general'],calls:seen}
}
run().catch(e=>(window as any).conversationResult={ok:false,error:String(e)})
