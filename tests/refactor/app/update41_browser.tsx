import * as React from "react"
import { createRoot } from "react-dom/client"
import { App } from "../../../refactor/learning/web/src/App"
import { useApp } from "../../../refactor/learning/web/src/store"
import { fixture } from "./update37_browser_bridge"
import { checkUpdate, useUpdates } from "../../../refactor/learning/web/src/updates/UpdateNotice"
declare global { interface Window { result: any; captureReady: boolean; captured: boolean } }
createRoot(document.getElementById("root")!).render(<App />)
const wait=(ms:number)=>new Promise(r=>setTimeout(r,ms))
const button=(label:string)=>[...document.querySelectorAll("button")].find(b=>b.textContent?.trim()===label) as HTMLButtonElement
const assert=(ok:any,text:string)=>{if(!ok)throw Error(text)}
async function run(){
  await wait(200)
  Object.assign(fixture,{current:"2.0.2",latest:"2.0.4",floor:"2.0.3",mandatory:true,reason:"protocol"})
  useApp.getState().set({ready:true}); await wait(1400)
  assert(!button("稍后"),"2.0.2必须更新，不提供稍后操作")
  assert(document.body.textContent?.includes("版本 2.0.3 必须安装：更新协议兼容要求"),"继承版本线原因")
  assert(button("立即更新") && useUpdates.getState().open,"必须保持更新弹框且只提供立即更新")
  window.captureReady=true
  for(let i=0;i<300&&!window.captured;i++)await wait(20)
  Object.assign(fixture,{current:"2.0.3",mandatory:false})
  await checkUpdate();await wait(1300)
  assert(!button("稍后")?.disabled,"2.0.3可选")
  button("稍后").click();await wait(50)
  assert(!document.querySelector('[role="dialog"]'),"稍后关闭弹框")
  assert(document.querySelector('[aria-label="有新版本"]'),"稍后保留红点")
  window.result={ok:true,passed:["继承强制线无稍后操作","原因显示","2.0.3可选","稍后与红点"]}
}
run().catch(e=>{window.result={ok:false,error:String(e),body:document.body.textContent}})
