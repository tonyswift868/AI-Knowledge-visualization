"""Real React Flow regression repro in local Qt Chromium; no network or user UI."""
import json
import os
from pathlib import Path
import subprocess
import sys


def build_fixture(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).parent
    deps = Path("D:/MentorFlow/.local/web-deps")
    node = Path("D:/MentorFlow/.local/node/node.exe")
    (root / "index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text(
        'import ' + json.dumps((repo / "refactor/learning/web/src/theme.css").as_posix()) + ";\n"
        'await import(' + json.dumps((here / "graph171_browser.tsx").as_posix()) + ");\n",
        encoding="utf-8",
    )
    (root / "vite.config.mjs").write_text(
        "import base from " + json.dumps((repo / "refactor/learning/web/vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ',build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}',
        encoding="utf-8",
    )
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix(), TEMP=root.as_posix(), TMP=root.as_posix())
    env["PATH"] = str(Path("D:/MentorFlow/.local/node")) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [str(node), str(deps / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def browser(root: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication([])
    view = QWebEngineView()
    view.resize(1360, 920)
    view.show()
    view.setFocus()
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))

    def pump():
        app.processEvents()

    def js(code: str):
        values = []
        view.page().runJavaScript(code, values.append)
        import time
        end = time.monotonic() + 10
        while not values and time.monotonic() < end:
            pump()
            time.sleep(.004)
        assert values, f"JS callback timeout: {code}"
        return values[0]

    def browser_drag(selector: str, done_flag: str):
        js("window.graph171InputTrace=[]; const log=e=>window.graph171InputTrace.push({type:e.type,target:e.target?.closest?.('.react-flow__node')?.getAttribute('data-id'),buttons:e.buttons,button:e.button,x:e.clientX,y:e.clientY}); for(const t of ['mousedown','mousemove','mouseup']) {document.addEventListener(t,log,true);window.addEventListener(t,log,true)}")
        rect = json.loads(js("JSON.stringify((() => { const e=document.querySelector(" + json.dumps(selector) + "); if(!e)return null; const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; })())"))
        if not rect:
            nodes = json.loads(js("JSON.stringify(Array.from(document.querySelectorAll('.react-flow__node')).map(e=>({id:e.getAttribute('data-id'),className:e.className})))"))
            raise AssertionError(f"drag target not rendered: {selector}; nodes={nodes}")
        x, y = round(rect["x"] + min(rect["w"] / 2, 90)), round(rect["y"] + min(rect["h"] / 2, 55))
        assert 5 < x < view.width() - 5 and 5 < y < view.height() - 5, f"drag target outside browser content: {rect}"
        # React Flow's d3 drag listens for node mousedown, then attaches
        # mousemove/up to event.view; supply window explicitly in headless Chromium.
        js("""(() => { const node=document.querySelector(%s), r=node.getBoundingClientRect(); const x=r.x+r.width/2,y=r.y+r.height/2;
          node.dispatchEvent(new MouseEvent('mousedown',{view:window,bubbles:true,cancelable:true,button:0,buttons:1,clientX:x,clientY:y}));
          window.dispatchEvent(new MouseEvent('mousemove',{view:window,bubbles:true,cancelable:true,button:0,buttons:1,clientX:x+112,clientY:y+70}));
          window.dispatchEvent(new MouseEvent('mousemove',{view:window,bubbles:true,cancelable:true,button:0,buttons:1,clientX:x+130,clientY:y+84}));
          window.dispatchEvent(new MouseEvent('mouseup',{view:window,bubbles:true,cancelable:true,button:0,buttons:0,clientX:x+130,clientY:y+84})); return true; })()""" % json.dumps(selector))
        trace = json.loads(js("JSON.stringify(window.graph171InputTrace)"))
        js(f"window.{done_flag}=true; true")
        if not any(item["type"] == "mousemove" and item["buttons"] == 1 for item in trace):
            raise AssertionError(f"d3 drag move event not delivered with button held: {trace}")

    import time
    artifact_root = Path(os.environ.get("GRAPH171_ROOT", "D:/MentorFlow/.local/test-tmp/graph171"))
    artifact_root.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + 30
    zoom_captured = False
    dark_captured = False
    plan_dragged = False
    map_dragged = False
    while not js("!!window.graph171Result"):
        assert time.monotonic() < end, "browser scenario timeout"
        if not plan_dragged and js("window.graph171WaitForNodeDrag === true"):
            browser_drag(js("window.graph171DragSelector"), "graph171NodeDragDone")
            plan_dragged = True
        if not map_dragged and js("window.graph171WaitForMapDrag === true"):
            browser_drag(".knowledge-map .react-flow__node:not([data-id='goal'])", "graph171MapDragDone")
            map_dragged = True
        if not zoom_captured and js("window.graph171CaptureZoom === true"):
            assert view.grab().save(str(artifact_root / "zoom-after.png")), "could not save post-fix zoom screenshot"
            zoom_captured = True
        if zoom_captured and not dark_captured and js("window.graph171CaptureDark === true"):
            assert view.grab().save(str(artifact_root / "zoom-aurora.png")), "could not save dark-theme zoom screenshot"
            dark_captured = True
        pump()
        time.sleep(.01)
    result = json.loads(js("JSON.stringify(window.graph171Result)"))
    (artifact_root / "browser-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    picture = view.grab()
    picture.save(str(artifact_root / "final-frame.png"))
    view.close()
    app.processEvents()
    print(json.dumps(result, ensure_ascii=False))
    if not result.get("ok"):
        raise AssertionError(result)


def test_graph171_viewport_does_not_reset_on_same_content_parent_rerender(tmp_path):
    build_fixture(tmp_path)
    # Per-run artifacts: a fixed shared folder collided when workers ran full suites concurrently.
    env = dict(os.environ, GRAPH171_ROOT=(tmp_path / "artifacts").as_posix(), TEMP=tmp_path.as_posix(), TMP=tmp_path.as_posix())
    env["PATH"] = str(Path("D:/MentorFlow/.local/node")) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([sys.executable, "-B", str(Path(__file__)), str(tmp_path)], env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((Path(env["GRAPH171_ROOT"]) / "browser-result.json").read_text(encoding="utf-8"))
    assert evidence["ok"]


if __name__ == "__main__":
    browser(Path(sys.argv[1]))
