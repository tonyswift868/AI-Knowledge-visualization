import * as React from "react"
import {createRoot} from "react-dom/client"
import {connect, call} from "../../../refactor/learning/web/src/bridge"
import {App} from "../../../refactor/learning/web/src/App"
import {Rag} from "../../../refactor/learning/web/src/pages/Rag"
import {PlanDetail} from "../../../refactor/learning/web/src/pages/PlanDetail"
import {useApp} from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"

declare global { interface Window { mountFrontend219:(page:string)=>void; pushFrontend219:(name:string,data:any)=>Promise<any>; navigateFrontend219:(route:string)=>void; statsFrontend219:()=>Promise<any>; failSettingsFrontend219:()=>Promise<any> } }
const root=createRoot(document.getElementById("root")!)
let appMount=0
window.mountFrontend219=(page)=>{
  if(page==="app") { useApp.setState({route:"home",planId:null,account:null,settings:null,ready:false});root.render(<App key={++appMount}/>);return }
  if(page==="rag") { root.render(<Rag/>);return }
  useApp.setState({planId:"plan-219"});root.render(<PlanDetail/> )
}
window.pushFrontend219=(name,data)=>call("test.push",{name,data}).then(()=>true)
window.navigateFrontend219=(route)=>useApp.getState().go(route as any,"models")
window.statsFrontend219=()=>call("test.stats",{})
window.failSettingsFrontend219=()=>call("test.fail_settings",{}).then(()=>true)
void connect().then(()=>window.mountFrontend219("app"))
