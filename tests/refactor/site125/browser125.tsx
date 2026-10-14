import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {RecommendationCards} from '../../../refactor/learning/web/src/pages/NewPlan'
import '../../../refactor/learning/web/src/theme.css'
const pause=()=>new Promise(r=>setTimeout(r,25))
async function until(fn:()=>boolean){for(let i=0;i<150;i++){if(fn())return;await pause()}throw Error(document.body.textContent!)}
let calls:string[][]=[]
function Test(){
 const [card,set]=React.useState<any>({card_id:'book',title:'网络书',url:'https://example.edu/network/',type:'网页书',why:'明确离线替身',modules:['网络'],read_status:'selecting',site_book:{origin:'.sidebar',entries:[
   {id:'p1',title:'传输',url:'a',section:['第一章','传输']},{id:'p2',title:'路由',url:'b',section:['第一章','路由']},{id:'p3',title:'应用',url:'c',section:['第二章','应用']}]}})
 const ref=React.useRef(0)
 function read(id:string,pages:string[]){calls.push(pages);const g=++ref.current;set((c:any)=>({...c,read_status:'reading',read_done:0,read_total:pages.length}));setTimeout(()=>{if(g===ref.current)set((c:any)=>({...c,read_done:1}))},100);setTimeout(()=>{if(g===ref.current)set((c:any)=>({...c,read_done:pages.length,status:'adopted',read_status:'ready',chars:5500,failed_pages:[{url:'b',title:'路由',message:'HTTP 404'}]}))},600)}
 return <RecommendationCards cards={[card]} busy={card.read_status==='reading'} onUse={()=>{}} onRead={read} onCancel={()=>{ref.current++;set((c:any)=>({...c,read_status:'cancelled',read_error:'已取消读取。'}))}}/>
}
createRoot(document.getElementById('root')!).render(<Test/> )
async function run(){
 await until(()=>!!document.querySelector('[aria-label="网站目录"]'))
 const label=(text:string)=>[...document.querySelectorAll('label')].find(l=>l.textContent===text)!.querySelector<HTMLInputElement>('input')!
 label('第二章（1页）').click();await until(()=>document.body.textContent!.includes('已选 2 页'))
 const confirm=()=>[...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent==='确认章节并读取')!
 confirm().click();await until(()=>document.body.textContent!.includes('已读 1 / 2 页'))
 const cancel=[...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent==='取消读取')!;cancel.click()
 await until(()=>document.body.textContent!.includes('已取消读取。'))
 confirm().click();await until(()=>document.body.textContent!.includes('已采用'))
 if(calls.some(v=>JSON.stringify(v)!=='["p1","p2"]'))throw Error('Wrong selected pages')
 if(!document.body.textContent!.includes('跳过 1 个失败页'))throw Error('Missing failed pages')
 ;(window as any).conversationResult={ok:true,checks:['tree','branch-selection','progress','cancel','adopted','failed-pages'],calls}
}
run().catch(e=>(window as any).conversationResult={ok:false,error:String(e)})
