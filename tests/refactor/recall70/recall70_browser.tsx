import * as React from "react"
import { createRoot } from "react-dom/client"
import { App } from "../../../refactor/learning/web/src/App"
import { useApp } from "../../../refactor/learning/web/src/store"
import { calls, control, reset, settings } from "./recall70_bridge"
useApp.getState().set({route:'classroom',planId:'recall70-plan'})
createRoot(document.getElementById('root')!).render(<App />)
;(window as any).recall70={calls,control,reset,theme:(theme:string)=>useApp.getState().set({settings:{...settings,theme} as any})}
;(window as any).recall70.collapse=(value:boolean)=>useApp.getState().set({sidebarCollapsed:value})
