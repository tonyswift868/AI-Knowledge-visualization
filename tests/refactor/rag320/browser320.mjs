import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const root = path.resolve(process.argv[2]);
const endpoint = process.env.MF_CDP_ENDPOINT;
const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms));
let target;
for (let i = 0; i < 180; i++) {
  try {
    const pages = await (await fetch(endpoint + "/json/list")).json();
    target = pages.find(page => page.type === "page" && page.url.startsWith("file:")
      && path.resolve(fileURLToPath(page.url)).toLowerCase().startsWith(root.toLowerCase() + path.sep));
    if (target) break;
  } catch {}
  await pause(100);
}
if (!target) throw Error("Owned Qt browser page did not start");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject;});
let sequence = 0;
const operations = new Map();
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (!message.id) return;
  const operation = operations.get(message.id);
  operations.delete(message.id);
  if (message.error) operation.reject(Error(JSON.stringify(message.error)));
  else operation.resolve(message.result);
};
function cdp(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    operations.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
  return result.result?.value;
}
async function until(expression) {
  for (let i = 0; i < 160; i++) {
    if (await evaluate(expression)) return;
    await pause();
  }
  throw Error("Condition timed out: " + expression);
}
async function call(method, params = {}) {
  return evaluate(`window.call320(${JSON.stringify(method)},${JSON.stringify(params)})`);
}
async function click(testId) {
  await evaluate(`(() => {
    const element = document.querySelector('[data-testid="${testId}"]');
    if (!element || element.disabled) throw Error('Missing or disabled control: ${testId}');
    element.click();
  })()`);
  await pause(80);
}
async function choose(batch) {
  await call("test.choose", {batch});
  await click("rag-pick-files");
}
async function currentSession(id) {
  await click("rag-materials-close");
  await click("rag-session-" + id);
  await until(`document.querySelector('[data-testid="rag-session-${id}"]')?.dataset.active === "true"`);
  await click("rag-materials-open");
}
function associated(source, target) {
  return `window.call320('test.inspect').then(state => {
    const document = state.documents.find(item => item.source === ${JSON.stringify(source)});
    return document?.status === 'ready'
      && state.sessions.find(item => item.id === ${JSON.stringify(target)})?.document_ids.includes(document.id);
  })`;
}

const result = {ok: false, checks: []};
let stage = "mount";
try {
  await until("!!window.call320 && !!document.querySelector('[data-testid=\"rag-materials-open\"]')");
  const initial = await call("test.inspect");
  await click("rag-session-" + initial.original);
  await click("rag-materials-open");

  stage = "mixed-batch";
  await choose("mixed");
  await until(associated("first.txt", initial.original));
  await until(associated("last.html", initial.original));
  await until("document.querySelector('[data-testid=\"rag-import-errors\"]')?.innerText.includes('blank.txt')");
  const failure = await evaluate("document.querySelector('[data-testid=\"rag-import-errors\"]').innerText");
  if (!failure.includes("blank.txt") || !failure.includes("正文")) throw Error("Filename/reason missing: " + failure);
  if (!await evaluate("document.querySelector('[data-testid=\"rag-import-errors\"]').closest('dialog').open")) {
    throw Error("Partial errors are not visible inside the open materials dialog");
  }
  const firstState = await call("test.inspect");
  if (firstState.documents.length !== 3) throw Error("Mixed batch must persist exactly two successes");
  if (await evaluate("document.querySelectorAll('[data-testid^=\"rag-add-document-\"]').length") !== 3) {
    throw Error("Real saved successes are absent from library UI");
  }
  const shot = await cdp("Page.captureScreenshot", {format: "png"});
  fs.writeFileSync(path.join(root, "partial-import.png"), Buffer.from(shot.data, "base64"));
  result.checks.push("real TXT/blank/HTML: two stored, visible, associated, ready; one named error");

  stage = "retry-and-all-valid";
  await choose("mixed");
  await until("!document.querySelector('[data-testid=\"rag-pick-files\"]').disabled");
  if ((await call("test.inspect")).documents.length !== 3) throw Error("Retry duplicated files");
  await choose("valid");
  await until("!document.querySelector('[data-testid=\"rag-pick-files\"]').disabled");
  if (await evaluate("!!document.querySelector('[data-testid=\"rag-import-errors\"]')")) {
    throw Error("All-valid retry retained stale errors");
  }
  result.checks.push("retry deduplicates; all-valid response clears errors");

  stage = "session-switch";
  await choose("slow");
  await until("window.call320('test.inspect').then(state => state.entered)");
  await currentSession(initial.other);
  await call("test.release");
  await until(associated("slow.txt", initial.original));
  const switched = await call("test.inspect");
  if (switched.sessions.find(item => item.id === initial.other).document_ids.length !== 1) {
    throw Error("Completed upload was attached to the newly active session");
  }
  result.checks.push("in-flight upload belongs to its captured original session after switching");

  stage = "committed-prefix-before-global-error";
  await currentSession(initial.original);
  await choose("global");
  await until(associated("prefix.txt", initial.original));
  await until("!document.querySelector('[data-testid=\"rag-pick-files\"]').disabled");
  if (!await evaluate("document.body.innerText.includes('正在删除')")) {
    throw Error("Global deletion guard was swallowed");
  }
  result.checks.push("global failure still rejects while streamed committed prefix stays visible and associated");

  stage = "account-switch";
  await choose("account");
  await until("window.call320('test.inspect').then(state => state.entered)");
  const account = await call("test.switch_account");
  await evaluate(`window.switchAccount320(${JSON.stringify(account.account_id)})`);
  await call("test.release");
  await until("window.call320('test.inspect').then(state => state.documents.length === 0)");
  await pause(250);
  const nextAccount = await call("test.inspect");
  if (nextAccount.documents.length || nextAccount.sessions.length) throw Error("Old import reached new account data");
  if (await evaluate("!!document.querySelector('[data-testid=\"rag-import-errors\"]')")) {
    throw Error("Old account import errors remained visible");
  }
  if (await evaluate("document.body.innerText.includes('资料问答已关闭')")) {
    throw Error("Old account failure produced a toast in the new account");
  }
  result.checks.push("account generation drops old import response/errors and new account data stays empty");
  result.ok = true;
} catch (error) {
  result.stage = stage;
  result.error = error.stack;
} finally {
  fs.writeFileSync(path.join(root, "result.json"), JSON.stringify(result, null, 2));
  socket.close();
}
console.log(JSON.stringify(result, null, 2));
if (!result.ok) process.exitCode = 1;
