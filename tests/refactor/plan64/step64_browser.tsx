import * as React from "react"
import { createRoot } from "react-dom/client"
import { App } from "../../../refactor/learning/web/src/App"
import { useApp } from "../../../refactor/learning/web/src/store"
import { show, settings } from "./step64_bridge"

const data = (window as any).step64Data
useApp.getState().set({route:'new-plan',planId:data.plan_id})
createRoot(document.getElementById('root')!).render(<App />)
;(window as any).step64Show = (theme: string, phase: string, collapsed=false, scale=1) => {
  useApp.getState().set({settings:{...settings,theme,font_scale:scale} as any,sidebarCollapsed:collapsed})
  show({...data,phase})
}
;(window as any).step64Audit = () => {
  const box = (el: Element) => { const r=el.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height} }
  const header=document.querySelector('.plan-head')!
  const track=document.querySelector('.stepper')!
  const circles=[...track.querySelectorAll('.step-dot > span')].map(box)
  const labels=[...track.querySelectorAll<HTMLElement>('.step-dot > em')]
  const active=track.querySelector('.step-dot.on')!
  const current=document.querySelector<HTMLElement>('.plan-step-current')
  const title=document.querySelector('.plan-title')!
  const abandon=[...header.querySelectorAll('button')].find(b=>b.textContent?.includes('放弃'))!
  return {width:innerWidth, header:box(header),track:box(track), circles,
    rows:new Set(circles.map(r=>Math.round(r.y))).size,
    visible_labels:labels.filter(el=>getComputedStyle(el).display!=='none').length,
    current:current && getComputedStyle(current).display!=='none' ? {text:current.textContent,box:box(current)} : null,
    active_label:active.querySelector('em')?.textContent,
    active_title:active.getAttribute('title'), active_aria:active.getAttribute('aria-current'),
    step_titles:[...track.querySelectorAll('.step-dot')].map(el=>el.getAttribute('title')),
    title:box(title),abandon:abandon?box(abandon):null,
    page_overflow:document.documentElement.scrollWidth>innerWidth,
    header_overflow:header.scrollWidth>header.clientWidth,
    track_overflow:track.scrollWidth>track.clientWidth,
    container_width:header.clientWidth-parseFloat(getComputedStyle(header).paddingLeft)-parseFloat(getComputedStyle(header).paddingRight),
  }
}
