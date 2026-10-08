"""Offline Chromium checks using real React, Classroom and useTeacherVoice.

Only the bridge, microphone and Live2D renderer are explicit test doubles.
The shared pinned web toolchain is used without installing any packages.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_memory87_default_off_switch_and_manual_play(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    local = Path(os.environ.get("MF_WEB_TOOLCHAIN", "D:/MentorFlow/.local"))
    node, deps = local / "node/node.exe", local / "web-deps"
    if not node.is_file() or not (deps / "node_modules/vite/bin/vite.js").is_file():
        pytest.skip("Shared offline web toolchain is not installed")
    src = repo / "refactor/learning/web/src"
    fixture = Path(__file__).parents[1] / "teacher_voice_v2"
    (tmp_path / "index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (tmp_path / "entry.tsx").write_text(f'import {json.dumps((Path(__file__).parent / "speech_switch_browser87.tsx").as_posix())}', encoding="utf-8")
    (tmp_path / "optional.tsx").write_text('''
import * as React from "react"
export const TeacherStage = React.forwardRef((props, ref) => {
  React.useImperativeHandle(ref, () => ({ setMouthLevel() {}, nod() {}, think() {} }))
  return <div>Explicit fake Live2D renderer</div>
})
export const TeacherWindow = React.forwardRef((props, ref) => <><TeacherStage ref={ref} />{props.children}</>)
export const useVoiceInput = () => ({})
export const MicButton = () => null
''', encoding="utf-8")
    aliases = [{"find": key, "replacement": path.as_posix()} for key, path in [
        ("@classroom", src / "pages/Classroom.tsx"), ("@voice", src / "teacher/useTeacherVoice.ts"),
        ("@store", src / "store.ts"), ("../bridge", fixture / "voice_stream_browser_fixture.ts"),
        ("../teacher/TeacherStage", tmp_path / "optional.tsx"), ("../voice", tmp_path / "optional.tsx")]]
    config = f'''
import path from "node:path"
import {{ pathToFileURL }} from "node:url"
const deps = {json.dumps(deps.as_posix())}
const {{ default: react }} = await import(pathToFileURL(path.join(deps, "node_modules/@vitejs/plugin-react/dist/index.js")))
export default {{ root: {json.dumps(tmp_path.as_posix())}, base: "./", resolve: {{ alias: {json.dumps(aliases)} }},
plugins: [react(), {{ name: "shared-deps", enforce: "pre", async resolveId(source, importer, options) {{
 if (!importer || source.startsWith(".") || source.startsWith("/") || source.includes(":")) return null
 if (importer.includes("node_modules")) return null
 return this.resolve(source, path.join(deps, "package.json"), {{ ...options, skipSelf: true }})
}} }}], build: {{ outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 }} }}
'''
    config_path = tmp_path / "vite.config.mjs"
    config_path.write_text(config, encoding="utf-8")
    result = subprocess.run([str(node), str(deps / "node_modules/vite/bin/vite.js"), "build", "--config", str(config_path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    (tmp_path / "build.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run([sys.executable, "-B", str(Path(__file__)), str(tmp_path)], capture_output=True,
                            text=True, encoding="utf-8", timeout=50)
    (tmp_path / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "browser-result.json").read_text(encoding="utf-8"))
    assert report["ok"], report
    assert len(report["checks"]) == 5


def browser(directory):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in {"http", "https"}: info.block(True)
    app = QApplication([])
    view = QWebEngineView()
    page = QWebEnginePage(view)
    offline = Offline(page)
    page.profile().setUrlRequestInterceptor(offline)
    view.setPage(page)
    view.resize(1280, 800); view.show()
    view.setUrl(QUrl.fromLocalFile(str(directory / "dist/index.html")))
    result = []
    until = time.monotonic() + 35
    while time.monotonic() < until:
        app.processEvents(); time.sleep(.005)
        if not result:
            pending = []
            page.runJavaScript("JSON.stringify(window.result || null)", pending.append)
            while not pending and time.monotonic() < until:
                app.processEvents(); time.sleep(.002)
            if pending and pending[0] and pending[0] != "null": result.append(json.loads(pending[0]))
        if result: break
    report = result[0] if result else {"ok": False, "error": "browser timed out"}
    (directory / "browser-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    view.close(); app.processEvents()


if __name__ == "__main__":
    browser(Path(sys.argv[1]))
