"""RAG 251 left session rail, explicit source editing, and automatic preparation."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
WEB = REPO / "refactor/learning/web"
OUTPUT = Path("D:/MentorFlow/.local/test-tmp/ragux251/screenshots")
sys.path.insert(0, str(REPO))


def _build(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name("browser251.tsx")
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
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _host(root: Path):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge, BridgeError
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

    docs = [
        {"id": "doc-a", "title": "待准备的资料.md", "source": "本地文件", "plan_ids": [], "status": "not_uploaded", "total_chunks": 10, "uploaded_chunks": 0, "error": ""},
        {"id": "doc-b", "title": "旧会话资料.md", "source": "本地文件", "plan_ids": [], "status": "ready", "total_chunks": 3, "uploaded_chunks": 3, "error": ""},
        {"id": "doc-c", "title": "第二份待准备资料.md", "source": "本地文件", "plan_ids": [], "status": "not_uploaded", "total_chunks": 6, "uploaded_chunks": 0, "error": ""},
        {"id": "doc-d", "title": "账户切换待准备资料.md", "source": "本地文件", "plan_ids": [], "status": "not_uploaded", "total_chunks": 4, "uploaded_chunks": 0, "error": ""},
    ]
    plans = [
        {"id": "plan-a", "title": "入门计划", "document_ids": ["doc-a", "doc-b"]},
        {"id": "plan-b", "title": "进阶计划", "document_ids": ["doc-c"]},
    ]
    old_messages = [
        {"id": "legacy-question", "role": "user", "content": "旧会话里的问题", "passages": []},
        {"id": "legacy-answer", "role": "assistant", "content": "旧会话的回答 [1]", "passages": [
            {"number": 1, "document_id": "doc-b", "seq": 2, "text": "这是旧会话保留的原文引用。", "title": "旧会话资料.md"}]},
    ]
    legacy = {"id": "legacy-session", "title": "旧会话标题", "document_ids": ["doc-b"],
              "updated_at": "2026-10-29T12:00:00+00:00", "count": 2, "messages": old_messages}
    sessions = []
    by_id = {}
    lock = __import__("threading").RLock()
    gate = __import__("threading").Event()
    mode = {"value": "block"}
    metrics = {"ingestStarted": 0, "ingestCancelled": 0, "create": 0, "send": 0,
        "sendAttempts": 0, "modelInvocations": 0, "sendCancelled": 0, "lastUpdate": None}
    send_mode = {"value": "success"}

    def summaries():
        return [{key: value for key, value in item.items() if key != "messages"} for item in sessions]

    bridge.register("rag.sources", lambda p, c: {"plans": plans, "documents": docs, "sessions": summaries(),
        "server_status": {"state": "unavailable", "message": "本机替身资料服务"}})

    def create(p, c):
        with lock:
            metrics["create"] += 1
            session_id = f"new-session-{metrics['create']}"
            selected = list(p.get("document_ids", []))
            row = {"id": session_id, "title": "新会话", "document_ids": selected,
                   "updated_at": "2026-10-30T00:00:00+00:00", "count": 0, "messages": []}
            sessions.insert(0, row)
            by_id[session_id] = row
            return {"session": dict(row)}

    bridge.register("rag.create", create)
    bridge.register("rag.get", lambda p, c: {"session": dict(by_id[p["session_id"]])})

    def update_documents(p, c):
        with lock:
            row = by_id[p["session_id"]]
            row["document_ids"] = list(p["document_ids"])
            row["updated_at"] = "2026-10-30T00:01:00+00:00"
            metrics["lastUpdate"] = {"session_id": p["session_id"], "document_ids": list(p["document_ids"])}
            return {"session": dict(row)}

    bridge.register("rag.update_documents", update_documents)

    def ingest(p, c):
        with lock:
            metrics["ingestStarted"] += 1
            current_mode = mode["value"]
            selected = list(p.get("document_ids", []))
            for document_id in selected:
                doc = next(item for item in docs if item["id"] == document_id)
                doc.update(status="uploading", uploaded_chunks=3, error="")
                bridge.push("rag.progress", {"document_id": document_id, "status": "uploading",
                    "uploaded_chunks": 3, "total_chunks": doc["total_chunks"], "completed": 3, "total": doc["total_chunks"]})
        if current_mode == "block":
            while not gate.wait(0.02):
                if c.cancel.is_set():
                    with lock:
                        metrics["ingestCancelled"] += 1
                        for document_id in selected:
                            doc = next(item for item in docs if item["id"] == document_id)
                            doc.update(status="not_uploaded", uploaded_chunks=0)
                    raise BridgeError("CANCELLED", "资料准备已取消。")
        with lock:
            if current_mode == "fail":
                result = []
                for document_id in selected:
                    doc = next(item for item in docs if item["id"] == document_id)
                    doc.update(status="failed", uploaded_chunks=3, error="本机替身模拟的网络错误")
                    result.append(dict(doc))
                    bridge.push("rag.progress", {"document_id": document_id, **doc})
                return {"documents": result}
            result = []
            for document_id in selected:
                doc = next(item for item in docs if item["id"] == document_id)
                doc.update(status="ready", uploaded_chunks=doc["total_chunks"], error="")
                result.append(dict(doc))
                bridge.push("rag.progress", {"document_id": document_id, **doc})
            return {"documents": result}

    bridge.register("rag.ingest", ingest)

    def send(p, c):
        with lock:
            metrics["sendAttempts"] += 1
            row = by_id[p["session_id"]]
            if send_mode["value"] != "success":
                document_id = row["document_ids"][0]
                doc = next(item for item in docs if item["id"] == document_id)
                doc.update(status="uploading", uploaded_chunks=2)
                bridge.push("rag.progress", {"document_id": document_id, **doc})
                current_mode = send_mode["value"]
            else:
                current_mode = "success"
        if current_mode == "not_ready_block":
            while not c.cancel.wait(0.02):
                pass
            with lock:
                metrics["sendCancelled"] += 1
            raise BridgeError("CANCELLED", "本机替身已取消服务端补准备。")
        if current_mode == "not_ready_fail":
            import time
            time.sleep(0.3)
            with lock:
                doc = next(item for item in docs if item["id"] == document_id)
                doc.update(status="failed", error="本机替身检测到资料未准备")
                bridge.push("rag.progress", {"document_id": document_id, **doc})
            raise BridgeError("RAG_DOCUMENT_NOT_READY", "资料尚未准备，服务端未发送模型请求。")
        with lock:
            metrics["send"] += 1
            metrics["modelInvocations"] += 1
            question = p["text"]
            row["messages"].extend([
                {"id": f"question-{metrics['send']}", "role": "user", "content": question, "passages": []},
                {"id": f"answer-{metrics['send']}", "role": "assistant", "content": "这是本机替身的资料回答", "passages": []},
            ])
            row["count"] = len(row["messages"])
            return {"session": dict(row)}

    bridge.register("rag.send", send)
    bridge.register("voice.status", lambda p, c: {"state": "not_downloaded", "installed": False, "invalid": False,
        "model_name": "替身语音模型", "size_bytes": 1, "download_url": "", "job_id": None,
        "downloaded_bytes": 0, "total_bytes": 0, "cancelling": False})
    bridge.register("voice.context", lambda p, c: {"terms": [], "question": "", "protected": []})
    bridge.register("voice.start", lambda p, c: (bridge.push("voice.state", {"session_id": p["session_id"], "state": "recording"}) or {"ok": True}))
    bridge.register("voice.stop", lambda p, c: {"session_id": p["session_id"], "text": "语音草稿", "raw_text": "语音草稿", "replacements": []})
    bridge.register("voice.cancel", lambda p, c: {"ok": True})
    bridge.register("test.ingest_mode", lambda p, c: (mode.update(value=p["mode"]) or gate.clear() or {"ok": True}))
    bridge.register("test.send_mode", lambda p, c: (send_mode.update(value=p["mode"]) or {"ok": True}))
    bridge.register("test.release_ingest", lambda p, c: (gate.set() or {"ok": True}))
    bridge.register("test.scope_changed", lambda p, c: (bridge.push("scope_changed", {"account_id": "second-account"}) or {"ok": True}))
    def seed_legacy(p, c):
        with lock:
            sessions.append(legacy)
            by_id[legacy["id"]] = legacy
        return {"ok": True}
    bridge.register("test.seed_legacy", seed_legacy)
    bridge.register("test.trace", lambda p, c: {**metrics, "sessions": summaries()})

    from tests.refactor.rag263.legacy_bridge263 import register_legacy_preparation
    register_legacy_preparation(bridge, docs, ingest)
    view.resize(1200, 1200)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if (root / "done251").exists() else None)
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
    log_path = root / "qt251.log"
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(root)],
                                 env=env, stdout=log, stderr=log)
        try:
            run = subprocess.run([str(NODE), str(Path(__file__).with_name("browser251.mjs")), str(root)],
                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150)
            assert run.returncode == 0, run.stdout + run.stderr
        finally:
            (root / "done251").touch()
            child.wait(timeout=20)
    return json.loads((root / "result251.json").read_text(encoding="utf-8"))


def test_rag_ui251_session_rail_explicit_documents_and_auto_prepare(tmp_path):
    if not NODE.is_file() or not (DEPS / "node_modules/vite/bin/vite.js").is_file():
        pytest.skip("共享前端工具不存在")
    browser_root = tmp_path / "browser251"
    _build(browser_root)
    result = _drive(browser_root)
    assert result.get("ok"), result
    screenshots = OUTPUT
    screenshots.mkdir(parents=True, exist_ok=True)
    for theme in ("sakura", "aurora"):
        for state in ("empty", "new", "preparing", "conversation"):
            source = browser_root / "screenshots" / f"rag-{theme}-{state}-251.png"
            assert source.is_file(), source
            shutil.copy2(source, screenshots / source.name)
    assert len(list(screenshots.glob("rag-*-*-251.png"))) >= 8


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--host":
    _host(Path(sys.argv[2]))
