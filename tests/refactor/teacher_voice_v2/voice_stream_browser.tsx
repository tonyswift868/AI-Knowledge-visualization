import * as React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "@classroom"
import { useTeacherVoice } from "@voice"
import { useApp } from "@store"
import * as fake from "./voice_stream_browser_fixture"

declare global { interface Window { result: any } }
const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms))
const assert = (value: unknown, message: string) => { if (!value) throw new Error(message) }
const calls = (method: string) => fake.log.filter((c) => c.method === `speech.${method}`)
const click = (text: string) => {
  const button = [...document.querySelectorAll("button")].find((b) => b.textContent === text)
  assert(button, `Missing button: ${text}`); button!.click()
}
let hook: ReturnType<typeof useTeacherVoice>
function Probe() { hook = useTeacherVoice(React.useRef(null), { autoWarm: false }); return null }
async function run() {
  const checks: string[] = []
  const element = document.getElementById("root")!
  let root = createRoot(element)
  async function mount(ready = true, reading = true) {
    root.unmount(); root = createRoot(element)
    fake.configure({ ready }); fake.log.length = 0
    localStorage.setItem("mf.classroom.read", reading ? "on" : "off")
    useApp.setState({ planId: "p", route: "classroom" })
    root.render(<Classroom />); await wait(80)
  }
  await mount()
  assert(calls("speak").length === 0, "entry should not replay history")
  click("开始上课"); await wait()
  fake.request!.opts.onStream("第一句。"); await wait()
  const old = calls("stream_append").at(-1)!.params.utterance_id
  assert(calls("stream_append").at(-1)!.params.text === "第一句。", "must append before teacher finishes")
  fake.configure({ holdAppend: true })
  fake.request!.opts.onStream("旧的在途片段。"); await wait()
  fake.request!.opts.onStream("旧的排队片段。")
  fake.request!.opts.onStream("\u0000")
  fake.configure({ holdAppend: false })
  fake.request!.opts.onStream("重试第一句。"); await wait()
  fake.heldAppend!(); await wait()
  assert(!calls("stream_append").some((c) => c.params.text === "旧的排队片段。"), "reset must discard chunks behind an in-flight append")
  const retry = calls("stream_append").at(-1)!.params.utterance_id
  assert(retry !== old && calls("stop").some((c) => c.params.utterance_id === old), "reset must stop old and restart")
  fake.request!.opts.onStream("重试第二句。"); fake.finish("重试第一句。重试第二句。")
  await wait(80)
  assert(calls("stream_end").at(-1)!.params.utterance_id === retry, "must close retry input")
  assert(calls("speak").length === 0, "final message must not replay streamed text")
  checks.push("classroom streams before completion, reset drops old utterance, final response never repeats")

  const input = document.querySelector("textarea")!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "我有一个问题")
  input.dispatchEvent(new Event("input", { bubbles: true })); await wait()
  click("发送"); await wait()
  assert(calls("stop").some((c) => c.params.utterance_id === retry), "learner message must interrupt previous reading")
  assert(fake.log.some((c) => c.method === "classroom.reply" && c.params.text === "我有一个问题"), "learner reply must still send")
  fake.finish(); await wait()
  checks.push("new learner message interrupts reading and still reaches classroom")

  // The read-stop control must prevent future teacher chunks from reviving it.
  await mount()
  click("开始上课"); await wait()
  fake.request!.opts.onStream("开头。"); await wait()
  click("停"); const count = calls("stream_append").length
  fake.request!.opts.onStream("停止以后。"); fake.finish(); await wait(60)
  assert(calls("stream_append").length === count && calls("speak").length === 0, "read stop must discard future text")
  checks.push("read stop suppresses queued chunks and final replay")

  await mount()
  click("开始上课"); await wait()
  fake.request!.opts.onStream("未完的回答。"); await wait()
  const stopped = calls("stop").length
  fake.restoreAfterCancel("停止后只显示，不再朗读。")
  click("停止"); await wait(60)
  assert(calls("stop").length > stopped, "generation Stop must stop voice immediately")
  assert(calls("speak").length === 0, "state refresh must not restart a cancelled response")
  checks.push("generation stop also stops reading")

  // A response still awaiting stream_start must never revive after leaving.
  await mount()
  fake.configure({ holdStart: true })
  click("开始上课"); await wait()
  fake.request!.opts.onStream("离开后不能读。"); root.unmount(); await wait()
  fake.heldStart!(); fake.finish(); await wait(50)
  assert(calls("stream_append").length === 0 && calls("speak").length === 0, "late start after unmount must be ignored")
  root = createRoot(element)
  checks.push("leaving with delayed start ignores old results")

  for (const [ready, reading] of [[true, false], [false, true]]) {
    await mount(ready, reading)
    click("开始上课"); await wait()
    fake.request!.opts.onStream("纯文字正常显示。"); fake.finish("纯文字正常显示。"); await wait()
    assert(calls("stream_start").length === 0 && calls("speak").length === 0, "disabled voice must not start")
    assert(document.body.textContent!.includes("纯文字正常显示。"), "text teaching must continue")
  }
  checks.push("disabled/unavailable voice preserves text-only teaching")

  await mount()
  click("开始上课"); await wait(); fake.finish("非流式的固定问题。"); await wait(60)
  assert(calls("speak").length === 1, "non-streamed teacher result must retain fallback")
  checks.push("non-streamed fixed result reads once")

  root.unmount(); root = createRoot(element)
  fake.configure({ ready: true }); fake.log.length = 0
  root.render(<Probe />); await wait(60)
  const first = await hook!.startStream()
  fake.configure({ holdEnd: true })
  const ending = hook!.endStream(first)
  const newer = await hook!.startStream()
  fake.heldEnd!(); await ending
  assert(await hook!.appendStream("新段落。", newer), "late end must not clear new stream")
  assert(!await hook!.appendStream("旧段落。", first), "old append must be rejected")
  assert(!await hook!.endStream(first), "old end must be rejected")
  root.unmount()
  checks.push("hook guards explicit stream IDs and late end replies")
  window.result = { ok: true, checks, log: fake.log }
}
run().catch((error) => { window.result = { ok: false, error: String(error), stack: error.stack, log: fake.log } })
