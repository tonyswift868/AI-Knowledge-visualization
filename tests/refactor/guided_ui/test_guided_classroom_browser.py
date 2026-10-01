"""Real classroom/report React pages driven by explicit batch-25 offline data.

Run directly with --artifacts D:/MentorFlow/.local/test-tmp/guided-ui to
rebuild the fixture, check interactions and capture the theme/viewport matrix.
No network, model, audio device or external renderer is used.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


def build_fixture(directory):
    local = Path(os.environ.get("MF_WEB_TOOLCHAIN", "D:/MentorFlow/.local"))
    node, deps = local / "node/node.exe", local / "web-deps"
    if not node.is_file() or not (deps / "node_modules/vite/bin/vite.js").is_file():
        pytest.skip("Shared offline web toolchain unavailable")
    directory.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).parent
    aliases = [{"find": key, "replacement": (fixture / target).as_posix()} for key, target in [
        ("../bridge", "guided_bridge.ts"),
        ("../teacher/TeacherStage", "guided_optional.tsx"), ("../voice", "guided_optional.tsx"),
    ]]
    (directory / "index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (directory / "entry.tsx").write_text(f'import {json.dumps((fixture / "guided_browser.tsx").as_posix())}', encoding="utf-8")
    config = f'''
import path from "node:path"
import {{ pathToFileURL }} from "node:url"
const deps = {json.dumps(deps.as_posix())}
const {{ default: react }} = await import(pathToFileURL(path.join(deps, "node_modules/@vitejs/plugin-react/dist/index.js")))
export default {{ root: {json.dumps(directory.as_posix())}, base: "./", resolve: {{ alias: {json.dumps(aliases)} }},
plugins: [react(), {{ name: "shared-deps", enforce: "pre", async resolveId(source, importer, options) {{
 if (!importer || source.startsWith(".") || source.startsWith("/") || source.includes(":")) return null
 if (importer.includes("node_modules")) return null
 return this.resolve(source, path.join(deps, "package.json"), {{ ...options, skipSelf: true }})
}} }}], build: {{ outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 }} }}
'''
    config_path = directory / "vite.config.mjs"
    config_path.write_text(config, encoding="utf-8")
    env = dict(os.environ, PATH=str(node.parent) + os.pathsep + os.environ.get("PATH", ""))
    result = subprocess.run([str(node), str(deps / "node_modules/vite/bin/vite.js"), "build", "--config", str(config_path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90, env=env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_guided_classroom_contract_in_offline_chromium(tmp_path):
    build_fixture(tmp_path)
    result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--browser", str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=75)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "browser-result.json").read_text(encoding="utf-8"))
    assert report["ok"], report
    assert len(report["checks"]) == 10


def browser(directory, artifacts=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Offscreen Chromium does not need Windows D3D shared-image surfaces.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in {"http", "https"}:
                info.block(True)

    app = QApplication([])
    view = QWebEngineView()
    profile = QWebEngineProfile(view)  # unnamed profile is off the record
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    page = QWebEnginePage(profile, view)
    offline = Offline(page)
    profile.setUrlRequestInterceptor(offline)
    view.setPage(page)
    view.resize(1440, 900)
    view.show()

    def pump(seconds=.1):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(.005)

    def evaluate(script):
        pending = []
        page.runJavaScript(script, pending.append)
        end = time.monotonic() + 5
        while not pending and time.monotonic() < end:
            pump(.01)
        assert pending, "JavaScript callback timed out"
        return pending[0]

    view.setUrl(QUrl.fromLocalFile(str(directory / "dist/index.html")))
    end = time.monotonic() + 50
    report = None
    while time.monotonic() < end:
        pump()
        result = evaluate("JSON.stringify(window.guidedResult || null)")
        if result and result != "null":
            report = json.loads(result)
            break
    report = report or {"ok": False, "checks": [], "error": "browser timed out"}
    captures = []
    try:
        if artifacts and report["ok"]:
            for theme, width, height, scale in [("sakura", 1440, 900, 1), ("aurora", 1440, 900, 1),
                                                ("sakura", 600, 900, 1.4), ("aurora", 600, 900, 1.4)]:
                view.resize(width, height)
                pump(.15)
                for scenario in ("continue", "choice", "reply", "return", "casual", "wrong", "report"):
                    evaluate(f"window.guidedShow({json.dumps(scenario)}, {json.dumps(theme)}, {scale}); true")
                    end = time.monotonic() + 8
                    while time.monotonic() < end and not evaluate("window.guidedCaptureReady"):
                        pump()
                    assert evaluate("window.guidedCaptureReady"), f"capture not ready: {scenario}"
                    pump(.15)
                    layout = json.loads(evaluate('''JSON.stringify({
                      overflow: document.documentElement.scrollWidth > innerWidth,
                      composer: document.querySelector('.composer')?.getBoundingClientRect().bottom,
                      height: innerHeight,
                      options: [...document.querySelectorAll('.class-options .choice')].map(x => ({
                        left:x.getBoundingClientRect().left, right:x.getBoundingClientRect().right,
                        bottom:x.getBoundingClientRect().bottom }))
                    })'''))
                    assert not layout["overflow"], (scenario, theme, layout)
                    assert layout.get("composer", 0) <= height + 1, (scenario, theme, layout)
                    assert all(0 <= option["left"] and option["right"] <= width + 1 and option["bottom"] <= height
                               for option in layout["options"]), (scenario, theme, layout)
                    filename = f"{theme}-{'narrow' if width == 600 else 'wide'}-{scenario}.png"
                    assert view.grab().save(str(artifacts / filename)), filename
                    captures.append({"path": filename, "theme": theme, "width": width, "height": height,
                                     "scale": scale, "scenario": scenario, "layout": layout})
    finally:
        report["captures"] = captures
        target = artifacts or directory
        (target / "browser-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        view.close()
        pump()
    assert report["ok"], report
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    if args.browser:
        browser(args.browser)
    elif args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
        build = args.artifacts / "fixture-build"
        try:
            build_fixture(build)
            result = browser(build, args.artifacts)
            lines = ["# 引导式课堂离线核对", "", "基线：008ca8f；真实 React 页面 + 假 snapshot/桥；未调用模型、麦克风或联网资源。", "",
                     "## 交互检查", ""] + [f"- {check}" for check in result["checks"]]
            lines += ["", "## 截图", "", "宽窗口 1440×900、100% 文字；窄窗口 600×900、140% 文字；截图均开启减少动态效果。",
                      "教师形象/麦克风为明确标注的离线占位，课堂、Markdown、引用和报告为真实组件。", ""]
            for capture in result["captures"]:
                lines += [f"### {capture['theme']} / {capture['width']}×{capture['height']} / {capture['scenario']}", "",
                          f"![{capture['scenario']}]({capture['path']})", ""]
            (args.artifacts / "report.md").write_text("\n".join(lines), encoding="utf-8")
        finally:
            if build.exists():
                # One known task-owned directory, entirely on D:.
                assert build.resolve().parent == args.artifacts.resolve() and build.resolve().drive.upper() == "D:"
                shutil.rmtree(build)
    else:
        parser.error("supply --browser or --artifacts")
