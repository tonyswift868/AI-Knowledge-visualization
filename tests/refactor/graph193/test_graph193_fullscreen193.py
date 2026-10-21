"""Real React/ReactFlow fullscreen lifecycle regression in local Qt Chromium."""
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
    (root / "index.html").write_text(
        '<meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div>'
        '<script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text(
        'import ' + json.dumps((repo / "refactor/learning/web/src/theme.css").as_posix()) + ";\n"
        'await import(' + json.dumps((here / "graph193_browser.tsx").as_posix()) + ");\n", encoding="utf-8")
    (root / "vite.config.mjs").write_text(
        "import base from " + json.dumps((repo / "refactor/learning/web/vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ',build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}', encoding="utf-8")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix(), TEMP=root.as_posix(), TMP=root.as_posix())
    env["PATH"] = str(Path("D:/MentorFlow/.local/node")) + os.pathsep + env.get("PATH", "")
    built = subprocess.run([str(node), str(deps / "node_modules/vite/bin/vite.js"), "build", "--config",
                           str(root / "vite.config.mjs")], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=90)
    assert built.returncode == 0, built.stdout + built.stderr


def browser(root: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    import time

    app = QApplication([])
    view = QWebEngineView()
    view.resize(1280, 900)
    view.show()
    view.setFocus()
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    artifacts = Path(os.environ.get("GRAPH193_ARTIFACTS", str(root / "artifacts")))
    artifacts.mkdir(parents=True, exist_ok=True)

    def pump():
        app.processEvents()

    def js(code: str):
        result = []
        view.page().runJavaScript(code, result.append)
        deadline = time.monotonic() + 12
        while not result and time.monotonic() < deadline:
            pump()
            time.sleep(.004)
        assert result, f"JavaScript callback timed out: {code}"
        return result[0]

    deadline = time.monotonic() + 30
    while not js("!!window.graph193Result"):
        assert time.monotonic() < deadline, "graph193 browser scenario timed out"
        resize = js("window.graph193ResizeRequest || null")
        if resize:
            width, height = (int(part) for part in resize.split("x", 1))
            size_ack = f"{width}x{height}"
        else:
            width = height = 0
            size_ack = ""
        if resize and js("window.graph193ResizeAck !== " + json.dumps(size_ack)):
            view.resize(width, height)
            pump()
            js("window.graph193ResizeAck=" + json.dumps(size_ack) + "; true")
        capture = js("window.graph193Capture || null")
        if capture and js("window.graph193CaptureAck !== " + json.dumps(capture)):
            filename = artifacts / (capture + ".png")
            assert view.grab().save(str(filename)), f"could not save {filename}"
            js("window.graph193CaptureAck=" + json.dumps(capture) + "; true")
        pump()
        time.sleep(.008)
    outcome = js("JSON.stringify(window.graph193Result)")
    parsed = json.loads(outcome)
    (artifacts / "browser-result.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    view.close()
    app.processEvents()
    print(json.dumps(parsed, ensure_ascii=False))
    assert parsed.get("ok"), parsed


def test_graph193_fullscreen_preserves_flow_and_visible_nodes(tmp_path):
    build_fixture(tmp_path)
    artifact_dir = Path(os.environ.get("GRAPH193_ARTIFACTS", str(tmp_path / "artifacts")))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GRAPH193_ARTIFACTS=artifact_dir.as_posix(), TEMP=tmp_path.as_posix(), TMP=tmp_path.as_posix())
    env["PATH"] = str(Path("D:/MentorFlow/.local/node")) + os.pathsep + env.get("PATH", "")
    run = subprocess.run([sys.executable, "-B", str(Path(__file__)), str(tmp_path)], env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    evidence = json.loads((artifact_dir / "browser-result.json").read_text(encoding="utf-8"))
    assert evidence["ok"]


if __name__ == "__main__":
    browser(Path(sys.argv[1]))
