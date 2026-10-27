"""Browser contract for default-ticked recall points and the actual deep-pagination question."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
SCRATCH = Path("D:/MentorFlow/.local/test-tmp/points214/ui")
QUESTION = Path(os.environ.get("MF_RECALL214_FIXTURE", "D:/MentorFlow/.local/test-tmp/points214/analysis/deep-pagination-ui214.json"))


def _question_fixture():
    if QUESTION.exists():
        data = json.loads(QUESTION.read_text(encoding="utf-8"))
        source = data.get("presentation_fields") or data
        return {"reference": source["reference"], "key_points": source["key_points"],
                "key_point_headings": source.get("key_point_headings")}
    return {"reference": "参考答案。", "key_points": ["原因一。", "原因二。", "解决办法。"],
            "key_point_headings": ["原因", "原因", "解决"]}


def build(root: Path, fixture: dict):
    root.mkdir(parents=True, exist_ok=True)
    deps = Path("D:/MentorFlow/.local/web-deps")
    node = Path("D:/MentorFlow/.local/node/node.exe")
    (root / "index.html").write_text(
        '<div id="root"></div><script>window.__recall214Fixture=' +
        json.dumps(fixture, ensure_ascii=False) +
        ';</script><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text(
        'import ' + json.dumps((REPO / "refactor/learning/web/src/theme.css").as_posix()) +
        ';import ' + json.dumps((HERE / "recall_ui214_browser.tsx").as_posix()) + ';', encoding="utf-8")
    aliases = [
        {"find": "../bridge", "replacement": (HERE / "recall_ui214_bridge.ts").as_posix()},
        {"find": "../teacher/TeacherStage", "replacement": (REPO / "tests/refactor/direct97/direct97_optional.tsx").as_posix()},
        {"find": "../voice", "replacement": (REPO / "tests/refactor/direct97/direct97_optional.tsx").as_posix()},
    ]
    config = f'''import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(root.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},
plugins:[react(),{{name:'shared-deps',enforce:'pre',async resolveId(s,i,o){{
if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null
return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true,target:'esnext'}}}}
'''
    (root / "vite.config.mjs").write_text(config, encoding="utf-8")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix(), PYTHONDONTWRITEBYTECODE="1",
               PATH=str(Path("D:/MentorFlow/.local/node")) + os.pathsep + os.environ.get("PATH", ""))
    built = subprocess.run([str(node), str(deps / "node_modules/vite/bin/vite.js"), "build", "--config",
                           str(root / "vite.config.mjs")], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=90)
    assert built.returncode == 0, built.stdout + built.stderr


def browser(root: Path, shots: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication([])
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    view.setPage(page)
    view.resize(1280, 900)
    view.show()

    def pump(seconds=.02):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)

    def js(script):
        values = []
        page.runJavaScript(script, values.append)
        deadline = time.monotonic() + 8
        while not values and time.monotonic() < deadline:
            pump()
        assert values, f"JavaScript evaluation timed out: {script}"
        return values[0]

    def wait(script, label):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if js('window.recall214Result?.ok===false'):
                raise AssertionError(f"Browser scenario failed: {js('JSON.stringify(window.recall214Result)')}")
            if js(script):
                return
            pump()
        raise AssertionError(f"Timed out waiting for {label}: {js('JSON.stringify(window.recall214Debug?.() ?? document.body.innerText)')}")

    def save(theme: str, filename: str):
        js(f'document.documentElement.dataset.theme={json.dumps(theme)}')
        pump(.18)
        assert view.grab().save(str(shots / filename)), f"Could not save {filename}"

    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    wait('!!document.querySelector(".recall-question")', "recall question render")
    shots.mkdir(parents=True, exist_ok=True)
    wait('window.recall214DefaultShotReady===true', "fresh answer with all points ticked")
    # Collapse the lengthy source pane so the question and all tick/X controls fit in the screenshot.
    js('(()=>{const d=document.querySelector(".recall-reference");if(d?.open)d.querySelector("summary").click();return true})()')
    save("sakura", "deep-pagination-default-light.png")
    save("aurora", "deep-pagination-default-dark.png")
    js('window.recall214ResolveDefaultShots()')
    wait('window.recall214ShotReady===true', "one missed point")
    assert js('document.querySelectorAll(".recall-point input:checked").length === window.__recall214Fixture.key_points.length - 1')
    assert js('!!document.querySelector(".recall-point.missed em")')
    save("sakura", "deep-pagination-missed-light.png")
    save("aurora", "deep-pagination-missed-dark.png")
    js('window.recall214ResolveShots()')
    wait('!!window.recall214Result', "browser scenario completion")
    result = json.loads(js("JSON.stringify(window.recall214Result)"))
    view.close()
    pump()
    assert result["ok"], result
    (shots / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def test_recall_ui214_default_ticks_x_mark_self_test_and_persisted_empty(tmp_path):
    fixture = _question_fixture()
    assert fixture["key_points"] and len(fixture["key_points"]) >= 2
    root = SCRATCH / "browser-build"
    shots = SCRATCH / "screenshots"
    build(root, fixture)
    runner = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--browser", str(root), "--shots", str(shots)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"))
    assert runner.returncode == 0, runner.stdout + runner.stderr
    result = json.loads((shots / "result.json").read_text(encoding="utf-8"))
    assert result["pointCount"] == len(fixture["key_points"])
    assert result["selfTestMissed"] == 2
    assert result["nextCount"] == 2


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--shots", type=Path)
    args = parser.parse_args()
    try:
        result = browser(args.browser, args.shots)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": repr(exc)}, ensure_ascii=False))
        raise
