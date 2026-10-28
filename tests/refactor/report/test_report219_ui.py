"""BUG-171/PLAN-05 regression in the real React components on offline Chromium."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

WEB = Path(__file__).resolve().parents[3] / "refactor/learning/web"
NODE = Path(r"D:\MentorFlow\.local\node\node.exe")
DEPS = Path(r"D:\MentorFlow\.local\web-deps")
ARTIFACTS = (Path(os.environ["MF_TEST_SCRATCH"]) / "report219"
             if os.environ.get("MF_TEST_SCRATCH") else Path(r"D:\MentorFlow\.local\test-tmp\fix219\MF03\ui"))


def build(directory: Path):
    if not NODE.is_file():
        pytest.skip("Configured local Node runtime is unavailable")
    directory.mkdir(parents=True, exist_ok=True)
    bridge = directory / "fake_bridge.ts"
    bridge.write_text("""
type Deferred = {resolve:(v:any)=>void; reject:(e:Error)=>void}
const pending: Deferred[] = []
export const calls: string[] = []
export function call<T=any>(method:string): Promise<T> {
  calls.push(method)
  return new Promise<T>((resolve,reject)=>pending.push({resolve,reject}))
}
export function finish(index:number, outcome:'resolve'|'reject') {
  const p=pending[index]; if(!p) throw Error('missing request '+index)
  outcome==='resolve' ? p.resolve({report:{weakest_points:[],questions:[],points:[],modules:[],teacher_profile:null,plan:{goal:'演示报告',final_goal:'目标',start_date:'2026-10-28',actual_start_date:null,actual_completion_date:null,early_days:null,overdue_days:null,replan_count:0,planned_minutes:60,credited_minutes:24}},titles:{},views:{modules:[],history:[],errors:{casual:0,module:0,check:0}}}) : p.reject(Error('fake failure'))
}
""", encoding="utf-8")
    aliases = [
        {"find": "../bridge", "replacement": bridge.as_posix()},
        {"find": "./fake_bridge", "replacement": bridge.as_posix()},
        {"find": "./theme.css", "replacement": (WEB / "src/theme.css").as_posix()},
    ]
    (directory / "index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (directory / "entry.tsx").write_text(f'import {json.dumps((Path(__file__).with_name("report219_browser.tsx")).as_posix())}', encoding="utf-8")
    config = f'''import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(DEPS.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'shared-deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:{json.dumps((ARTIFACTS / 'dist').as_posix())},emptyOutDir:true,chunkSizeWarningLimit:2000}}}}'''
    (directory / "vite.config.mjs").write_text(config, encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = str(Path(r"D:\MentorFlow\.local\node")) + os.pathsep + env.get("PATH", "")
    env["TEMP"] = env["TMP"] = str(ARTIFACTS)
    result = subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(directory / "vite.config.mjs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, env=env)
    assert result.returncode == 0, result.stdout + result.stderr


def browser(directory: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ("http", "https"): info.block(True)
    app = QApplication([]); view = QWebEngineView(); profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view); profile.setUrlRequestInterceptor(Offline(page)); view.setPage(page)
    view.resize(900, 750); view.show(); view.setUrl(QUrl.fromLocalFile(str(ARTIFACTS / "dist/index.html")))
    def pump(seconds=.03):
        end=time.monotonic()+seconds
        while time.monotonic()<end: app.processEvents(); time.sleep(.002)
    def js(code):
        got=[]; page.runJavaScript(code,got.append); end=time.monotonic()+5
        while not got and time.monotonic()<end: pump(.01)
        assert got, "JavaScript callback timed out"
        return got[0]
    end=time.monotonic()+35
    captured=set()
    while time.monotonic()<end:
        pump()
        stage=js("window.baselineStage219||''")
        if stage in ('sakura','aurora') and stage not in captured:
            path=ARTIFACTS / f"progress219-before-{stage}.png"
            if not path.exists(): assert view.grab().save(str(path))
            captured.add(stage)
        raw=js("JSON.stringify(window.result219||null)")
        if raw and raw!="null": break
    assert raw and raw!="null", "browser fixture did not finish"
    result=json.loads(raw)
    assert result["ok"], result
    for state in ("debt", "severe", "today", "ahead"):
        js(f"window.showProgress219({json.dumps(state)},'sakura')"); pump(.2)
        assert js("document.querySelector('.progress-status')?.textContent") == result["expected"][state]
    for theme in ("sakura", "aurora"):
        js(f"window.showProgress219('today',{json.dumps(theme)})"); pump(.2)
        assert view.grab().save(str(ARTIFACTS / f"progress219-after-{theme}.png"))
    (ARTIFACTS / "browser-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    view.close(); app.processEvents()


def test_report219_ui_regressions(tmp_path):
    build(tmp_path)
    result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--browser", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=75)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (ARTIFACTS / "progress219-before-sakura.png").is_file()
    assert (ARTIFACTS / "progress219-before-aurora.png").is_file()
    assert (ARTIFACTS / "progress219-after-sakura.png").is_file()
    assert (ARTIFACTS / "progress219-after-aurora.png").is_file()


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument("--browser", type=Path); args=parser.parse_args()
    if args.browser: browser(args.browser)
