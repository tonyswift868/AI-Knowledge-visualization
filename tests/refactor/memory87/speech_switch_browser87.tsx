import * as React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "@classroom"
import { useTeacherVoice } from "@voice"
import { useApp } from "@store"
import * as fake from "../teacher_voice_v2/voice_stream_browser_fixture"
const wait = (ms=30) => new Promise(r=>setTimeout(r,ms))
const assert = (x:any,m:string)=>{if(!x)throw Error(m)}
const calls = (name:string)=>fake.log.filter(x=>x.method===`speech.${name}`)
const button = (text:string)=>[...document.querySelectorAll("button")].find(b=>b.textContent?.trim()===text)!
let hook:any
function Probe(){hook=useTeacherVoice(React.useRef(null));return null}
async function run(){
 const checks:string[]=[];const el=document.getElementById("root")!;let root=createRoot(el)
 async function mount(saved:string|null){root.unmount();root=createRoot(el);fake.configure({ready:true});fake.log.length=0
 saved===null?localStorage.removeItem("mf.classroom.read"):localStorage.setItem("mf.classroom.read",saved)
 useApp.setState({planId:"p",route:"classroom"});root.render(<Classroom/>);
 const until=performance.now()+3000;while(!button("开始上课")||!button(saved==="on"?"朗读：开":"朗读：关")){assert(performance.now()<until,"entry timeout");await wait()}await wait(80)}
 await mount(null);assert(button("朗读：关")?.getAttribute("aria-pressed")==="false","default visibly off")
 assert(!calls("warm_up").length,"default must not warm");button("开始上课").click();await wait();fake.request!.opts.onStream("老师的话。");fake.finish("老师的话。");await wait(100)
 assert(!calls("stream_start").length&&!calls("speak").length,"off lesson cannot start engine");checks.push("never-selected default off and text teaching does not start voice")
 button("朗读：关").click();await wait(100);assert(button("朗读：开")?.getAttribute("aria-pressed")==="true"&&calls("warm_up").length===1,"explicit on warms and visibly on")
 button("朗读：开").click();await wait(100);assert(calls("stop").some(x=>x.params.release===true),"explicit off releases");assert(localStorage.getItem("mf.classroom.read")==="off","off persisted");checks.push("visible toggle warms only on and releases on off")
 const play=[...document.querySelectorAll("button")].find(b=>b.title==="听这段");assert(play,"manual play exists");play!.click();await wait(100);assert(calls("speak").length===1,"manual listening remains available while off");checks.push("manual sentence play works while automatic reading off")
 await mount("on");assert(button("朗读：开")&&calls("warm_up").length===1,"previous explicit on retained");root.unmount();await wait(100);assert(calls("stop").some(x=>x.params.release===true),"leave releases");checks.push("explicit saved on retained; leaving releases owned engine")
 root=createRoot(el);fake.log.length=0;root.render(<Probe/>);await wait(100);assert(!calls("warm_up").length,"hook default must not warm");root.unmount();checks.push("hook default autoWarm false")
 ;(window as any).result={ok:true,checks,log:fake.log}
}
run().catch(e=>(window as any).result={ok:false,error:String(e),stack:e.stack,log:fake.log})
