"""RAG plan picker and voice composer browser regression tests (BUG-198 / VOICE-01)."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import shutil

import pytest

REPO = Path(__file__).resolve().parents[3]
OUT = Path("D:/MentorFlow/.local/test-tmp/ragui249")
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
WEB = REPO / "refactor/learning/web"
sys.path.insert(0, str(REPO))


def _build(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name("browser249.tsx")
    (root / "entry.tsx").write_text("import " + json.dumps(entry.as_posix()), encoding="utf-8")
    (root / "index.html").write_text(
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div><script type="module" src="/entry.tsx"></script>',
        encoding="utf-8",
    )
    (root / "vite.config.mjs").write_text(
        "import base from " + json.dumps((WEB / "vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ",build:{outDir:'dist',emptyOutDir:true}}",
        encoding="utf-8",
    )
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS))
    result = subprocess.run(
        [str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _host(root: Path):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge
    from tests.refactor.simulation.engine import ensure_offscreen_fonts

    app = QApplication([])
    ensure_offscreen_fonts()
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in {"http", "https"}:
                info.block(True)

    blocker = Offline(page)
    profile.setUrlRequestInterceptor(blocker)
    view.setPage(page)
    bridge = AppBridge(page)
    channel = QWebChannel(page)
    channel.registerObject("mf", bridge)
    page.setWebChannel(channel)

    plan_id = "plan-long"
    plans = [
        {"id": "plan-short", "title": "短计划", "document_ids": ["doc-short"]},
        {"id": plan_id, "title": "Java 服务端工程实践与数据库系统设计的长期综合学习计划（用于验证超长名称截断和完整名称提示）", "document_ids": ["doc-long"]},
    ]
    docs = [
        {"id": "doc-short", "title": "短资料.txt", "source": "上传资料", "plan_ids": ["plan-short"], "status": "ready", "total_chunks": 1, "uploaded_chunks": 1},
        {"id": "doc-long", "title": "长计划资料.txt", "source": "上传资料", "plan_ids": [plan_id], "status": "ready", "total_chunks": 1, "uploaded_chunks": 1},
    ]
    calls = []
    sessions = []
    by_id = {}
    voice_sessions = set()
    voice_status = {"state": "ready", "installed": True, "invalid": False,
        "model_name": "本机测试模型", "size_bytes": 1, "download_url": "", "job_id": None,
        "downloaded_bytes": 0, "total_bytes": 0, "cancelling": False}

    bridge.register("rag.sources", lambda p, c: {"plans": plans, "documents": docs, "sessions": sessions})

    def create(p, c):
        calls.append(("rag.create", p))
        row = {"id": "session-249", "title": "资料问答", "document_ids": list(p.get("document_ids", [])),
               "updated_at": "2026-10-30T00:00:00", "count": 0, "messages": []}
        sessions.insert(0, {k: v for k, v in row.items() if k != "messages"})
        by_id[row["id"]] = row
        return {"session": row}

    bridge.register("rag.create", create)
    bridge.register("rag.get", lambda p, c: {"session": by_id.get(p.get("session_id"))})

    def send(p, c):
        calls.append(("rag.send", p))
        raise AssertionError("voice transcript must never submit automatically")

    bridge.register("rag.send", send)
    bridge.register("test.trace", lambda p, c: {
        "send": sum(method == "rag.send" for method, _ in calls),
        "create": sum(method == "rag.create" for method, _ in calls),
    })
    bridge.register("voice.status", lambda p, c: dict(voice_status))
    bridge.register("voice.context", lambda p, c: {"terms": [], "question": "", "protected": []})

    def not_ready(p, c):
        voice_status["state"] = "not_downloaded"
        voice_status["installed"] = False
        return {"ok": True}

    bridge.register("test.voice_not_ready", not_ready)

    def voice_start(p, c):
        session_id = p["session_id"]
        voice_sessions.add(session_id)
        bridge.push("voice.state", {"session_id": session_id, "state": "recording"})
        bridge.push("voice.partial", {"session_id": session_id, "text": "识别过程中的文字", "raw_text": "识别过程中的文字", "replacements": []})
        return {"ok": True}

    bridge.register("voice.start", voice_start)
    bridge.register("voice.stop", lambda p, c: {"session_id": p["session_id"],
        "text": "识别结果可编辑后再提问", "raw_text": "识别结果可编辑后再提问", "replacements": []})
    bridge.register("voice.cancel", lambda p, c: {"ok": True})

    view.resize(1100, 820)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if (root / "done249").exists() else None)
    timer.start(100)
    try:
        app.exec()
    finally:
        bridge.close()


def _drive(root: Path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
        MF_CDP_ENDPOINT=f"http://127.0.0.1:{port}",
        PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
        TEMP=str(root), TMP=str(root), TMPDIR=str(root),
        PATH="D:/MentorFlow/.local/node;" + os.environ.get("PATH", ""))
    log = (root / "qt249.log").open("w", encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(root)], env=env, stdout=log, stderr=log)
    try:
        run = subprocess.run([str(NODE), str(Path(__file__).with_name("browser249.mjs")), str(root)],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=100)
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        (root / "done249").touch()
        child.wait(timeout=15)
        log.close()
    return json.loads((root / "result249.json").read_text(encoding="utf-8"))


def test_rag249_plan_picker_voice_composer_and_both_themes(tmp_path):
    if not NODE.is_file() or not (DEPS / "node_modules/vite/bin/vite.js").is_file():
        pytest.skip("共享前端工具不存在")
    browser_root = tmp_path / "browser249"
    _build(browser_root)
    result = _drive(browser_root)
    assert result.get("ok"), result
    screenshots = OUT / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    for theme in ("sakura", "aurora"):
        source = browser_root / "screenshots" / f"rag-{theme}-249.png"
        assert source.is_file()
        shutil.copy2(source, screenshots / source.name)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--host":
    _host(Path(sys.argv[2]))
