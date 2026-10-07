import * as React from "react"
import { createRoot } from "react-dom/client"
import { App } from "../../../refactor/learning/web/src/App"
import { useApp } from "../../../refactor/learning/web/src/store"
import { TeacherRuntime } from "../../../refactor/learning/web/src/teacher/teacherRuntime"
import { log,settings,emit } from "./teacher78_bridge"
const runtimes:TeacherRuntime[]=[]
let rendered:Record<string,number>={}
const load=TeacherRuntime.prototype.load
TeacherRuntime.prototype.load=async function(manifest) {
  runtimes.push(this)
  await load.call(this,manifest)
  const internal=(this as any).model.internalModel
  internal.on("beforeModelUpdate",()=>{
    const raw=internal.coreModel.getModel().parameters
    rendered=Object.fromEntries(Array.from(raw.ids as string[]).flatMap((id,i)=>id==="Button2"||id==="Button7"?[[id,raw.values[i]]]:[]))
  })
}
localStorage.removeItem('mf.classroom.teacher-drawer.v1')
localStorage.removeItem('mf.classroom.teacher-drawer.v1.avatar')
localStorage.removeItem('mf.classroom.read')
useApp.getState().set({route:'classroom',planId:'teacher78'})
const root=createRoot(document.getElementById('root')!)
let generation=0
root.render(<App key={generation} />)
;(window as any).teacher78={log,snapshot:()=>runtimes.at(-1)?.snapshot(),
  rendered:()=>rendered,
  mouth:(value:number)=>runtimes.at(-1)?.setMouthLevel(value,false),
  react:(name:string)=>name==="greet"?runtimes.at(-1)?.greet():runtimes.at(-1)?.react(name as any), reduced:(value:boolean)=>useApp.getState().set({settings:{...useApp.getState().settings,reduce_motion:value} as any}), emit,
  reopen:()=>root.render(<App key={++generation} />),
  theme:(theme:string)=>useApp.getState().set({settings:{...settings,theme} as any}),
  collapse:(value:boolean)=>useApp.getState().set({sidebarCollapsed:value})}
