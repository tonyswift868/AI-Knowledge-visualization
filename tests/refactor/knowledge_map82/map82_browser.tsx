import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {App} from '../../../refactor/learning/web/src/App'
import {useApp} from '../../../refactor/learning/web/src/store'
import {knowledgeLayout,pointProgress} from '../../../refactor/learning/web/src/planning/KnowledgeMap'
import '../../../refactor/learning/web/src/theme.css'
import {control,requests,emit} from './map82_bridge'
const w=window as any
const checks:string[]=[]
const wait=()=>new Promise(r=>setTimeout(r,10))
async function until(fn:()=>boolean,ms=10000){const end=performance.now()+ms;while(!fn()){if(performance.now()>end)throw Error('timeout: '+document.body.innerText);await wait()}}
function assert(ok:unknown,label:string){if(!ok)throw Error(label);checks.push(label)}
const btn=(text:string)=>[...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent?.trim()===text)!
const node=(id:string)=>document.querySelector<HTMLElement>(`.knowledge-map .react-flow__node[data-id="${id}"]`)
const visible=(el:HTMLElement|null)=>{if(!el)return false;const r=el.getBoundingClientRect(),c=document.querySelector('.km-workspace')!.getBoundingClientRect();return r.right>c.left&&r.left<c.right&&r.bottom>c.top&&r.top<c.bottom}
const all=control.data.stages.flatMap((s:any)=>s.modules)
const select=(id:string)=>{const e=document.querySelector<HTMLSelectElement>('[aria-label="选择地图模块"]')!;e.value=id;e.dispatchEvent(new Event('change',{bubbles:true}))}
useApp.setState({route:'plans',planId:control.data.plan.id,ready:true})
createRoot(document.getElementById('root')!).render(<App/> )
async function run(){
 await until(()=>!!btn('查看路线'));let start=performance.now();btn('查看路线').click()
 await until(()=>visible(document.querySelector('.knowledge-map .react-flow__node')))
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))
 const overviewMs=performance.now()-start
 assert(overviewMs<1000,'总览点击到节点可见小于1秒')
 assert(!document.querySelector('[data-testid="detail-route"]'),'默认图而非列表')
 assert(document.querySelectorAll('.knowledge-map .react-flow__node').length<=all.length+1,'总览只挂载模块和目标')
 const largest=all.reduce((a:any,b:any)=>a.points.length>b.points.length?a:b)
 start=performance.now();select(largest.id)
 await until(()=>document.querySelector('[data-testid="detail-map"]')?.getAttribute('data-point-count')===String(largest.points.length)&&visible(node(largest.points[0].id)))
 await until(()=>document.querySelector<HTMLElement>('.knowledge-map .react-flow__viewport')!.style.transform.includes('scale(0.85)'))
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))
 const networkMs=performance.now()-start
 assert(networkMs<1000,'最大模块点开到知识点节点可见小于1秒')
 assert(!node(all[0].id),'知识点视图不挂载其他模块所有知识点')
 const native=w.map82Native
 if(!native){
   select(all[0].id);await until(()=>!!node(all[0].points[0].id))
   await until(()=>!!document.querySelector('.km-prerequisite')&&!!document.querySelector('.km-related'))
   assert(!!document.querySelector('.km-related path[stroke-dasharray]')||getComputedStyle(document.querySelector('.km-related path')!).strokeDasharray!=='none','相关连线虚线')
   assert(!!document.querySelector(`[data-external-id="${all[1].id}"]`),'跨模块前置是模块小标签')
   document.querySelector<HTMLElement>(`[data-external-id="${all[1].id}"]`)!.click();await until(()=>document.querySelector('[data-testid="detail-map"]')?.getAttribute('data-module-id')===all[1].id)
   assert(true,'跨模块标签可跳转')
   select(all[0].id);await until(()=>!!node(all[0].points[0].id));node(all[0].points[0].id)!.click()
   await until(()=>!!document.querySelector('[aria-label="知识点详情"]'))
   const source=document.querySelector<HTMLElement>('.km-sources summary')!;assert(!source.parentElement!.hasAttribute('open'),'出处默认收起');source.click()
   await until(()=>!!document.querySelector('.km-markdown table')&&!!document.querySelector('.km-markdown pre code'))
   assert(true,'出处Markdown表格及代码块真实排版')
   const reads=requests.filter(r=>r.method==='plan.detail').length
   emit('plan.point_links_changed',{plan_id:'other',status:'ready'});await wait()
   assert(requests.filter(r=>r.method==='plan.detail').length===reads,'其他计划事件不重读本计划')
   control.status='running';emit('plan.point_links_changed',{plan_id:control.data.plan.id,status:'running'})
   await until(()=>document.body.innerText.includes('正在整理知识点之间的关系…')&&!document.querySelector('.km-related'))
   assert(!!document.querySelector('.react-flow__node'),'整理中仍显示节点')
   control.status='partial';emit('plan.point_links_changed',{plan_id:control.data.plan.id,status:'partial'});await until(()=>!!btn('重试'))
   btn('重试').click();await until(()=>requests.some(r=>r.method==='plan.retry_point_links'&&r.params.plan_id===control.data.plan.id))
   assert(true,'部分失败重试发送约定动作')
   control.status='failed';emit('plan.point_links_changed',{plan_id:control.data.plan.id,status:'failed'});await until(()=>document.body.innerText.includes('知识点关系整理失败')&&!!btn('重试'))
   assert(true,'全部失败也可重试')
   control.status='ready';emit('plan.point_links_changed',{plan_id:control.data.plan.id,status:'ready'});await until(()=>!!document.querySelector('.km-related'))
   assert(!!document.querySelector('[aria-label="知识点详情"]'),'事件重读保留当前知识点面板')
   assert(pointProgress({needs_review:true} as any)[1]==='red'&&pointProgress({state:'green'} as any)[1]==='green'&&pointProgress({taught:true} as any)[1]==='yellow'&&pointProgress({} as any)[1]==='quiet','状态红绿黄灰映射且已学不冒充掌握')
   const layout=knowledgeLayout(control.data,null)
   assert(layout.nodes.length===all.length+1,'布局只包含模块和目标')
   assert(document.querySelector('.knowledge-map')!.classList.contains('km-reduced'),'减少动态效果禁用动画')
 }
 btn('返回总览').click();await until(()=>document.querySelector('[data-testid="detail-map"]')?.getAttribute('data-module-id')==='')
 await until(()=>visible(node('goal')))
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))
 const overviewZoom=Number(document.querySelector<HTMLElement>('.knowledge-map .react-flow__viewport')!.style.transform.match(/scale\(([^)]+)\)/)![1])
 assert(overviewZoom>.6,'返回总览按当前模块布局适配，不沿用旧知识点网范围')
 assert(true,'返回总览')
 btn('回到我学到哪').click();await until(()=>document.querySelector('[data-testid="detail-map"]')?.getAttribute('data-module-id')!=='')
 assert(true,'回到学习位置')
 const viewport=document.querySelector<HTMLElement>('.knowledge-map .react-flow__viewport')!
 await until(()=>viewport.style.transform.includes('scale(0.85)'))
 const transform=viewport.style.transform
 document.querySelector<HTMLButtonElement>('.knowledge-map .react-flow__controls-zoomin')!.click()
 await until(()=>viewport.style.transform!==transform)
 assert(true,'缩放按钮改变视口')
 btn('回到我学到哪').click();await until(()=>viewport.style.transform.includes('scale(0.85)'))
 assert(true,'在当前模块内也可重新定位')
 btn('列表').click();await until(()=>!!document.querySelector('[data-testid="detail-route"]'))
 assert(document.body.innerText.includes('个点'),'列表兜底保留')
 btn('图').click();await until(()=>!!document.querySelector('[data-testid="detail-map"]'))
 w.map82Result={ok:true,checks,overviewMs,networkMs,largestPoints:largest.points.length,requests}
}
w.map82Show=async(theme:string,mode:string)=>{
 w.map82ShotReady=false
 useApp.setState({settings:{...useApp.getState().settings,theme} as any})
 if(!w.map82Native){control.status=mode==='running'?'running':'ready';emit('plan.point_links_changed',{plan_id:control.data.plan.id,status:control.status});await wait()}
 select(mode==='overview'?'':all[0].id)
 await until(()=>mode==='overview'?!!node('goal'):!!node(all[0].points[0].id))
 if(mode==='source'){node(all[0].points[0].id)!.click();await until(()=>!!document.querySelector('.km-sources summary'));document.querySelector<HTMLElement>('.km-sources summary')!.click();await until(()=>!!document.querySelector('.km-markdown table'))}
 document.querySelector('.content')!.scrollTop=document.querySelector('.knowledge-map')!.getBoundingClientRect().top+document.querySelector('.content')!.scrollTop-80
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))
 if(mode==='overview') {const r=node('goal')!.getBoundingClientRect(),c=document.querySelector('.km-workspace')!.getBoundingClientRect();assert(r.left>=c.left&&r.right<=c.right&&r.top>=c.top&&r.bottom<=c.bottom,'从出处面板返回总览目标节点完整可见')}
 let marker=document.getElementById('paint');if(!marker){marker=document.createElement('i');marker.id='paint';document.body.append(marker)}
 const code=['overview','network','running','source'].indexOf(mode)
 marker.style.cssText=`position:fixed;left:0;top:0;width:1px;height:1px;z-index:2147483647;background:${theme==='sakura'?'#aa112':'#bb223'}${code+2}`
 w.map82ShotReady=true
}
run().catch(e=>w.map82Result={ok:false,checks,error:String(e.stack||e),body:document.body.innerText})
