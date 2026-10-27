"""Offline browser contract for semantic recall groups and legacy snapshots."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent


def build_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text(
        'import ' + json.dumps((REPO / "refactor/learning/web/src/theme.css").as_posix())
        + ';import ' + json.dumps((HERE / "recall_ui213_browser.tsx").as_posix()) + ';', encoding="utf-8")
    (root / "vite.config.mjs").write_text(
        'import base from ' + json.dumps((REPO / "refactor/learning/web/vite.config.mjs").as_uri())
        + ';export default {...base,root:' + json.dumps(root.as_posix())
        + ',build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}', encoding="utf-8")
    deps = Path("D:/MentorFlow/.local/web-deps")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix())
    result = subprocess.run(
        ["D:/MentorFlow/.local/node/node.exe", str(deps / "node_modules/vite/bin/vite.js"),
         "build", "--config", str(root / "vite.config.mjs")], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def browser(root, screenshot=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor

    app = QApplication([])
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    view.setPage(page)

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ("http", "https"):
                info.block(True)

    interceptor = Offline(page)
    profile.setUrlRequestInterceptor(interceptor)
    view.resize(1100, 900)
    view.show()

    def pump(seconds=.02):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(.003)

    def js(code):
        values = []
        page.runJavaScript(code, values.append)
        end = time.monotonic() + 5
        while not values and time.monotonic() < end:
            pump()
        assert values, "JS evaluation timed out"
        return values[0]

    def wait(code):
        end = time.monotonic() + 12
        while not js(code):
            assert time.monotonic() < end, "Timed out: " + code
            pump()

    def click(text):
        js('(()=>{const b=[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==='
           + json.dumps(text) + ');if(!b)throw Error("missing button: '+text+'");b.click();return true})()')

    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    wait('!!document.querySelector(".recall-question") && !!window.recall213')
    assert js('document.querySelectorAll(".recall-point").length') == 0
    js('(()=>{const a=document.querySelector(".recall-own textarea");'
       'Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(a,"我知道分片键怎么选");'
       'a.dispatchEvent(new Event("input",{bubbles:true}));return true})()')
    click("写好了，看参考答案")
    wait('document.querySelectorAll(".recall-point").length===6')
    assert js('document.querySelectorAll(".recall-point input").length') == 6
    first_text = json.loads(js('JSON.stringify([...document.querySelectorAll(".recall-point")[0].querySelector("span").childNodes].map(n=>n.textContent))'))
    assert first_text[1].count("\n") == 1
    assert first_text[1].startswith("分片键特点：")
    assert js('document.querySelectorAll(".recall-point")[0].innerText.includes("均衡分散到各个分片")')
    assert js('[...document.querySelectorAll(".recall-point")].every(e=>e.innerText.trim().length>10)')
    # The heading-like phrase belongs to its content group; no checkbox exists for it by itself.
    assert js('[...document.querySelectorAll(".recall-point")].every(e=>e.innerText.trim()!=="分片键特点")')
    assert js('[...document.querySelectorAll("button")].some(e=>e.textContent.trim()==="算我答对")')
    assert js('[...document.querySelectorAll("button")].some(e=>e.textContent.trim()==="算我答错")')
    if screenshot:
        screenshot = Path(screenshot)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        view.grab().save(str(screenshot))

    # Six semantic groups remain individually tickable; self-judgement stays with the learner.
    js('document.querySelectorAll(".recall-point input")[1].click();true')
    wait('window.recall213.calls.filter(c=>c.method==="class.recall_draft").length===1')
    js('document.querySelectorAll(".recall-point input")[5].click();true')
    wait('window.recall213.calls.filter(c=>c.method==="class.recall_draft").length===2')
    click("算我答错")
    wait('!!document.querySelector(".recall-result.missed")')
    assert js('window.recall213.calls.filter(c=>c.method==="class.recall_submit").at(-1).params.correct') is False
    assert js('JSON.stringify(window.recall213.calls.filter(c=>c.method==="class.recall_submit").at(-1).params.ticked)') == "[1,5]"
    assert js('document.querySelector(".recall-result").textContent.includes("你选择：算我答错")')
    assert js('document.querySelectorAll(".recall-point.missed").length') == 4

    # An in-flight legacy snapshot owns its old six strings and tick indices after restoration.
    js('window.recall213.restoreLegacy();true')
    wait('document.querySelectorAll(".recall-point input").length===6')
    assert js('document.querySelectorAll(".recall-point input")[4].checked')
    assert not js('document.querySelectorAll(".recall-point input")[1].checked')
    assert js('document.querySelectorAll(".recall-point")[4].innerText.includes("旧快照要点五")')
    click("算我答对")
    wait('!!document.querySelector(".recall-result.correct")')
    assert js('window.recall213.calls.filter(c=>c.method==="class.recall_submit").at(-1).params.correct') is True
    assert js('JSON.stringify(window.recall213.calls.filter(c=>c.method==="class.recall_submit").at(-1).params.ticked)') == "[4]"
    assert js('document.querySelector(".recall-result").textContent.includes("你选择：算我答对")')
    (root / "browser-result.json").write_text(json.dumps({"ok": True, "new_key_point_count": 6,
        "restored_ticked": [4], "learner_self_judgement": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    view.close()
    page.deleteLater()
    pump()


def test_recall_ui213_semantic_groups_and_restored_snapshot(tmp_path):
    build_fixture(tmp_path)
    screenshot = os.environ.get("MF_RECALL213_SCREENSHOT")
    proc = subprocess.run([sys.executable, "-B", str(Path(__file__)), str(tmp_path),
                           *( ["--screenshot", screenshot] if screenshot else [])],
        capture_output=True, text=True, encoding="utf-8", timeout=90,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert proc.returncode == 0, proc.stdout + proc.stderr


if __name__ == "__main__":
    browser(Path(sys.argv[1]), sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--screenshot" else None)
