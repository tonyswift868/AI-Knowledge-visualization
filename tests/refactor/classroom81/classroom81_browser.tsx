import React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "../../../refactor/learning/web/src/pages/Classroom"
import { ClassroomMarkdown, cjkStrong } from "../../../refactor/learning/web/src/classroom/Markdown"
import { OriginalCards } from "../../../refactor/learning/web/src/classroom/Conversation"
import { useApp } from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"
import { calls, requests, showRecall } from "../conversation37/conversation37_bridge"

const root=createRoot(document.getElementById("root")!)
const wait=(ms=20)=>new Promise(r=>setTimeout(r,ms))
const checks:string[]=[]
function expect(value:unknown, message:string) { if(!value)throw new Error(message) }
async function until(f:()=>boolean) { for(let i=0;i<250;i++){if(f())return;await wait()};throw new Error("UI timeout") }
const content=()=>document.body.textContent??""
function button(text:string) {
 const result=[...document.querySelectorAll<HTMLButtonElement>("button")].find(b=>b.textContent?.trim()===text)
 if(!result)throw new Error("Missing button "+text);return result
}
function fill(text:string) {
 const input=document.querySelector("textarea")!
 Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")!.set!.call(input,text)
 input.dispatchEvent(new Event("input",{bubbles:true}));input.dispatchEvent(new Event("change",{bubbles:true}))
}
async function run(){
 const cases=["**先分清两种单位：**线程", "**一句话。**后续", "前面**“重点”**后面", "**注意！**后续", "**为什么？**接着"]
 const code="`**代码：**后面`\n\n```python\n**代码：**后面\n```"
 expect(cjkStrong(code)===code,"Code was rewritten")
 expect(cjkStrong("\\*\\*文字：\\*\\*")==="\\*\\*文字：\\*\\*","Escaped literal changed")
 root.render(<><ClassroomMarkdown content={cases.join("\n\n")}/><OriginalCards cards={[{id:"cjk",text:cases[0],title:"中文原文",url:null,locator:"第一段"}]}/></>)
 await until(()=>document.querySelectorAll("strong").length===5)
 expect(!content().includes("**"),"CJK strong shows stars")
 expect(!content().includes("\u2060"),"Invisible parser helper leaked to copied text")
 document.querySelector<HTMLButtonElement>(".original-toggle")!.click();await wait();document.querySelector<HTMLButtonElement>(".original-card")!.click()
 await until(()=>document.querySelectorAll("strong").length===6)
 expect(document.querySelector(".original-fulltext strong")?.textContent?.includes("先分清两种单位："),"Source strong broken")
 checks.push("中文冒号、句号、引号、问号、叹号加粗；原文一致；代码和转义不改")
 useApp.setState({planId:"a".repeat(32),route:"classroom",settings:{theme:"sakura",reduce_motion:true} as never})
 root.render(<main style={{height:"100%",padding:18}}><Classroom/></main>)
 await until(()=>!!document.querySelector(".class-module-progress"))
 expect(document.querySelectorAll(".class-point").length===0,"Points expanded by default")
 expect(content().includes("本模块 0 / 1 个点已讲"),"Wrong module count")
 expect(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuemax")==="1","Wrong progressbar total")
 button("展开知识点").click();await until(()=>document.querySelectorAll(".class-point").length===1)
 expect(document.querySelectorAll(".class-point.current").length===1,"Current point missing")
 button("收起知识点").click();await until(()=>document.querySelectorAll(".class-point").length===0)
 checks.push("知识点默认收起，进度可读、展开保留当前点")
 button("针尖").click();await until(()=>content().includes("懂了，继续"))
 expect(!document.querySelector(".ask-mode"),"Mode toggle remains")
 button("我想问…").click();await wait()
 expect(!content().includes("这句是提问"),"Teaching asks must use common input")
 fill("硬度相同吗？");await wait();button("发送").click();await wait()
 await until(()=>calls.includes("classroom.reply")&&!button("懂了，继续").disabled)
 expect(!calls.includes("classroom.ask"),"Teaching used separate ask")
 checks.push("讲课直接输入问题使用reply，无左侧开关")
 button("懂了，继续").click();await until(()=>!!document.querySelector(".class-options"))
 expect(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")==="1","Taught progress stale")
 button("展开知识点").click();await wait();expect(!!document.querySelector(".class-point.taught svg"),"Taught point has no tick")
 button("收起知识点").click();await wait()
 button("我想问…").click();await until(()=>content().includes("这句是提问"))
 button("取消").click();await until(()=>!content().includes("这句是提问"))
 button("我想问…").click();await wait();fill("这句是提问");await wait();button("发送").click();await wait()
 await until(()=>calls.includes("classroom.ask")&&!content().includes("这句是提问 ·")&&!button("我想问…").disabled)
 expect(requests.find(r=>r.method==="classroom.ask")?.params.text==="这句是提问","One-shot ask wrong payload")
 expect(!!document.querySelector(".class-options"),"Ask consumed pending question")
 fill("A");await wait();button("发送").click();await wait()
 await until(()=>requests.filter(r=>r.method==="classroom.reply").length===2)
 expect(requests.filter(r=>r.method==="classroom.reply").at(-1)?.params.text==="A","Answer inherited question mode")
 checks.push("检测提问一次、可取消、发送后下一条恢复作答")
 showRecall();root.render(<main style={{height:"100%",padding:18}}><Classroom key="recall"/></main>)
 await until(()=>document.querySelector<HTMLTextAreaElement>('[aria-label="课堂输入"]')?.disabled===true)
 button("我想问…").click();await until(()=>document.querySelector<HTMLTextAreaElement>('[aria-label="课堂输入"]')?.disabled===false)
 expect(document.activeElement?.getAttribute("aria-label")==="课堂输入","Recall question did not focus common input")
 // Fill only the common input, never the recall card's answer box.
 const input=document.querySelector<HTMLTextAreaElement>('[aria-label="课堂输入"]')!
 Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")!.set!.call(input,"卡片应该怎么用？")
 input.dispatchEvent(new Event("input",{bubbles:true}));await wait();button("发送").click();await wait()
 await until(()=>requests.filter(r=>r.method==="classroom.ask").length===2&&input.disabled)
 expect(!document.querySelector(".class-question-tag"),"Recall tag did not clear")
 checks.push("问答卡片照旧，提问时共用输入获得焦点、发送后回到卡片作答")
 ;(window as any).conversationResult={ok:true,checks}
}
run().catch(e=>{(window as any).conversationResult={ok:false,checks,error:String(e.stack||e)+JSON.stringify({calls,requests,text:content()})}})
