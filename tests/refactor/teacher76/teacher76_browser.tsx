import * as React from "react"
import { createRoot } from "react-dom/client"
import { App } from "../../../refactor/learning/web/src/App"
import { useApp } from "../../../refactor/learning/web/src/store"
import { TeacherRuntime } from "../../../refactor/learning/web/src/teacher/teacherRuntime"
import { log,settings } from "./teacher76_bridge"
const runtimes:TeacherRuntime[]=[]
const load=TeacherRuntime.prototype.load
TeacherRuntime.prototype.load=async function(manifest) { runtimes.push(this);return load.call(this,manifest) }
localStorage.removeItem('mf.classroom.teacher-window.v1')
localStorage.removeItem('mf.classroom.teacher-window.v1.avatar')
localStorage.removeItem('mf.classroom.read')
useApp.getState().set({route:'classroom',planId:'teacher76'})
const root=createRoot(document.getElementById('root')!)
let generation=0
root.render(<App key={generation} />)
;(window as any).teacher76={log,snapshot:()=>runtimes.at(-1)?.snapshot(),
  reopen:()=>root.render(<App key={++generation} />),
  theme:(theme:string)=>useApp.getState().set({settings:{...settings,theme} as any}),
  collapse:(value:boolean)=>useApp.getState().set({sidebarCollapsed:value})}
