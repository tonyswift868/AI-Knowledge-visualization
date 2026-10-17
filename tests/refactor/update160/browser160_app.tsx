import * as React from "react"
import {createRoot} from "react-dom/client"
import {App} from "../../../refactor/learning/web/src/App"
import {useApp} from "../../../refactor/learning/web/src/store"
import {connect} from "./bridge160"
import {fixture160} from "./bridge160"
import "../../../refactor/learning/web/src/theme.css"

useApp.setState({ready:false,route:"classroom",planId:"plan160",account:null,settings:null})
fixture160.bootstrapAccount=null
;(window as any).fixture160=fixture160
;(window as any).makeMandatory160=()=>{fixture160.appMandatory=true}
;(window as any).setReady160=(ready:boolean)=>useApp.getState().set({ready})
;(window as any).setRoute160=(route:"home"|"classroom")=>useApp.getState().go(route)
;(window as any).setAccount160=(name:string|null)=>useApp.getState().set({account:name?{name,role_label:"学习者"}:null})
connect().then(connected=>{useApp.getState().set({connected});createRoot(document.getElementById("root")!).render(<App/>);(window as any).ready160=true})
