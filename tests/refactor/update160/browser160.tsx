import * as React from "react"
import {createRoot} from "react-dom/client"
import {Classroom} from "../../../refactor/learning/web/src/pages/Classroom"
import {UpdateNotice,useUpdates} from "../../../refactor/learning/web/src/updates/UpdateNotice"
import {useApp} from "../../../refactor/learning/web/src/store"
import {fixture160} from "./bridge160"
import "../../../refactor/learning/web/src/theme.css"

useApp.setState({ready:true,route:"classroom",planId:"plan160",account:{name:"account-A",account_id:"account-A",role_label:"学习者"},settings:{theme:"sakura",reduce_motion:true} as any})
;(window as any).fixture160=fixture160
;(window as any).setAccount160=(name:string)=>{fixture160.accountId=name;useApp.setState({account:{name,account_id:name,role_label:"学习者"}})}
;(window as any).makeMandatory160=()=>useUpdates.setState({status:{available:true,busy:false,state:"MANDATORY_UPDATE",version:"9.160.0",current:"9.160.0",latest:"9.160.1",mandatory:true,mandatory_floor:"9.160.1",mandatory_reason:"support_floor",message:"必须安装新版本"},open:false,later:false,manual:false,error:""})
;(window as any).makeOptional160=()=>useUpdates.setState({status:{available:true,busy:false,state:"OPTIONAL_UPDATE",version:"9.160.0",current:"9.160.0",latest:"9.160.1",mandatory:false,message:"有新版本"},open:false,later:false,manual:false,error:""})
createRoot(document.getElementById("root")!).render(<><div className="shell"><Classroom/></div><UpdateNotice/></>)
;(window as any).ready160=true
