import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {App} from '../../../refactor/learning/web/src/App'
import {useApp} from '../../../refactor/learning/web/src/store'
import '../../../refactor/learning/web/src/theme.css'
import {control,requests} from './detail81_bridge'
const w = window as any
w.detail81Errors=[]
window.addEventListener('error', e => w.detail81Errors.push({message:e.message,stack:e.error?.stack}))
const wait=()=>new Promise(r=>setTimeout(r,10))
async function until(fn:()=>boolean, ms=25000) {const end=performance.now()+ms;while(!fn()){if(performance.now()>end)throw Error('timeout: '+document.body.innerText);await wait()}}
const click=(selector:string)=>document.querySelector<HTMLButtonElement>(selector)!.click()
const checks:string[]=[]
function assert(ok:unknown,msg:string){if(!ok)throw Error(msg);checks.push(msg)}
useApp.setState({route:'plans',planId:'029b076315034c00bbd6842a5c9c31d9',ready:true})
let start=performance.now()
createRoot(document.getElementById('root')!, {onCaughtError(error,info){w.detail81Errors.push({message:String((error as Error).message),stack:(error as Error).stack,componentStack:info.componentStack})}}).render(<App/> )
async function run(){
 await until(()=>[...document.querySelectorAll('button')].some(b=>b.textContent?.includes('查看路线')))
 start=performance.now();[...document.querySelectorAll('button')].find(b=>b.textContent?.includes('查看路线'))!.click()
 await until(()=>!!document.querySelector('.outline-tree') || !!document.querySelector('[data-testid="detail-map"] .react-flow__node') || w.detail81Errors.length>0)
 w.detail81VisibleMs=performance.now()-start
 if (w.detail81Mode==='before'){w.detail81Result={ok:!w.detail81Errors.length,ms:w.detail81VisibleMs,errors:w.detail81Errors};return}
 assert(w.detail81VisibleMs<1000,'541点详情在1秒内可见')
 ;[...document.querySelectorAll('button')].find(b=>b.textContent==='列表')!.click();await until(()=>!!document.querySelector('[data-testid="detail-route"]'))
 assert(document.body.innerText.includes('题目 82 道，已过 0 道'),'按题统计82道，不以知识点等级冒充')
 assert(document.body.innerText.includes('只学不考'),'没有题的模块标只学不考')
 assert(!document.querySelector('.mastery-dot'),'没有知识点等级标色')
 click('[data-testid="detail-route"] summary');await until(()=>!!document.querySelector('[data-testid="detail-route"] .point-list'))
 assert(document.querySelector('[data-testid="detail-route"] .point-list')!.textContent!.length>10,'模块展开包含全部知识点')
 click('[data-testid="teacher-picker"]');await until(()=>!!document.querySelector('[data-preset-id="b"]'))
 click('[data-preset-id="b"]');await until(()=>document.querySelector('[data-testid="teacher-picker"]')!.textContent!.includes('严谨老师'))
 assert(requests.some(r=>r.method==='plan.set_teacher_preset'&&r.params.preset_id==='b'&&r.params.plan_id===useApp.getState().planId),'切换老师发送约定桥接参数')
 // A malformed render result stands in for a page bug; real App must keep navigation.
 w.detail81Throw=true;useApp.getState().go('plans');await until(()=>!!document.querySelector('.nav'));useApp.getState().go('plan')
 await until(()=>document.body.innerText.includes('这一页出错了'))
 assert(!!document.querySelector('.sidebar .nav'),'渲染故障保留侧栏')
 w.detail81Throw=false;click('[data-testid="page-error-retry"]');await until(()=>!!document.querySelector('[data-testid="detail-map"] .react-flow__node'))
 assert(!document.body.innerText.includes('这一页出错了'),'页面错误边界重试可重新挂载')
 w.detail81Throw=true;useApp.getState().go('plans');await wait();useApp.getState().go('plan');await until(()=>document.body.innerText.includes('这一页出错了'))
 click('[data-testid="page-error-back"]');await until(()=>document.body.innerText.includes('每个计划都有自己的路线'))
 assert(!document.body.innerText.includes('这一页出错了'),'返回其他页恢复显示')
 w.detail81ThrowHome=true;useApp.getState().go('home');await until(()=>document.body.innerText.includes('这一页出错了'))
 assert(!!document.querySelector('.sidebar .nav'),'首页渲染故障也只隔离首页')
 w.detail81ThrowHome=false;click('[data-testid="page-error-back"]');await until(()=>useApp.getState().route==='plans'&&document.body.innerText.includes('每个计划都有自己的路线'))
 assert(!document.body.innerText.includes('这一页出错了'),'首页出错返回可用计划页，不困在首页')
 w.detail81Throw=false;control.timeout=true;useApp.getState().go('plan')
 await until(()=>document.body.innerText.includes('正在读取计划…'))
 await until(()=>document.body.innerText.includes('读取超时'),18000)
 assert(!document.body.innerText.includes('只学不考'),'超时不显示上一次计划')
 control.timeout=false;click('[data-testid="detail-retry"]');await until(()=>!!document.querySelector('[data-testid="detail-map"] .react-flow__node'))
 assert(true,'15秒读取超时后重试成功')
 w.detail81Result={ok:true,checks,ms:w.detail81VisibleMs,requests,errors:w.detail81Errors}
}
w.detail81Show=async(theme:string,error:boolean)=>{
 w.detail81ShotReady=false
 w.detail81Throw=error;useApp.setState({route:'plans'});await wait();useApp.setState({route:'plan',settings:{...useApp.getState().settings,theme} as never})
 await until(()=>error?document.body.innerText.includes('这一页出错了'):!!document.querySelector('[data-testid="detail-map"] .react-flow__node'))
 document.querySelector('.content')!.scrollTop=error?0:180
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))
 let marker=document.getElementById('paint');if(!marker){marker=document.createElement('i');marker.id='paint';document.body.append(marker)}
 marker.style.cssText=`position:fixed;left:0;top:0;width:1px;height:1px;z-index:2147483647;background:${error?'#cc3344':theme==='sakura'?'#aa1122':'#bb2233'}`
 w.detail81ShotReady=true
}
run().catch(error=>{w.detail81Result={ok:false,checks,error:String(error.stack||error),body:document.body.innerText}})
