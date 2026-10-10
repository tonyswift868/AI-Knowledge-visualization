import * as React from "react"
import {createRoot} from "react-dom/client"
import {App} from "../../../refactor/learning/web/src/App"
import {useApp} from "../../../refactor/learning/web/src/store"
import {calls,reset,settings} from "./essay99_bridge"
useApp.getState().set({route:'classroom',planId:'essay99',sidebarCollapsed:true})
createRoot(document.getElementById('root')!).render(<App/> )
;(window as any).essay99={calls,reset,theme:(theme:string)=>useApp.getState().set({settings:{...settings,theme} as any}),
  page:(mode:string)=>{reset(mode);useApp.getState().go(mode==='completed'?'plan':'classroom')}}
