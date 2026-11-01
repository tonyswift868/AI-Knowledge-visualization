"""Real React/QtWebEngine coverage for the RAG 269 library/session redesign."""
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
OUTPUT = Path("D:/MentorFlow/.local/test-tmp/ragui278/screenshots269")
sys.path.insert(0, str(REPO))


def _build(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name("browser269.tsx")
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
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS), TEMP=str(root / "temp"), TMP=str(root / "tmp"),
        TMPDIR=str(root / "tmp"), HOME=str(root / "home"), LOCALAPPDATA=str(root / "local"),
        APPDATA=str(root / "appdata"), npm_config_cache=str(root / "node-cache"),
        XDG_CACHE_HOME=str(root / "node-cache"), PATH="D:/MentorFlow/.local/node;" + os.environ.get("PATH", ""))
    for key in ("TEMP", "TMP", "HOME", "LOCALAPPDATA", "APPDATA", "npm_config_cache"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    run = subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr


def _host(root: Path):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge
    from tests.refactor.simulation.engine import ensure_offscreen_fonts
    import threading

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

    plans = [{"id": "plan-a", "title": "工程学习计划", "document_ids": ["doc-plan"]}]
    docs = [
        {"id": "doc-plan", "title": "数据库课程资料.pdf", "source": "工程学习计划", "plan_ids": ["plan-a"], "status": "ready", "total_chunks": 4, "uploaded_chunks": 4, "error": "", "remote_upload_id": "remote-plan", "canonical_id": "canon-plan"},
        {"id": "doc-upload", "title": "接口规范与示例说明.md", "source": "上传资料", "plan_ids": [], "status": "ready", "total_chunks": 2, "uploaded_chunks": 2, "error": "", "remote_upload_id": "remote-upload", "canonical_id": "canon-upload"},
        {"id": "doc-other", "title": "第二份长标题资料：用于窄屏布局检查的详细文档.txt", "source": "另一个计划", "plan_ids": ["plan-b"], "status": "not_uploaded", "total_chunks": 7, "uploaded_chunks": 0, "error": ""},
    ]
    plans.append({"id": "plan-b", "title": "另一个计划", "document_ids": ["doc-other"]})
    messages = [
        {"id": "old-q", "role": "user", "content": "旧问题保留", "passages": []},
        {"id": "old-a", "role": "assistant", "content": "旧回答 [1]", "passages": [
            {"number": 1, "document_id": "doc-upload", "seq": 1, "text": "历史出处原文仍保留在快照中。", "title": "接口规范与示例说明.md"},
        ]},
    ]
    sessions = []
    lock = threading.RLock()
    import_gate = threading.Event()
    metrics = {"create": 0, "send": 0, "prepare": 0, "updates": [], "deleted": [], "renamed": []}

    def session_by_id(sid):
        return next((item for item in sessions if item["id"] == sid), None)

    def summaries():
        return [{key: value for key, value in item.items() if key != "messages"} for item in sessions]

    def source_views():
        return {"plans": plans, "documents": docs, "sessions": summaries(), "server_status": {"state": "ready"}}

    bridge.register("rag.sources", lambda p, c: source_views())
    bridge.register("rag.get", lambda p, c: {"session": session_by_id(p.get("session_id"))})

    def update_documents(p, c):
        with lock:
            row = session_by_id(p["session_id"])
            if row is None:
                raise RuntimeError("unknown session")
            row["document_ids"] = list(p["document_ids"])
            metrics["updates"].append({"session_id": row["id"], "document_ids": list(row["document_ids"])})
            return {"session": row}

    bridge.register("rag.update_documents", update_documents)

    def create(p, c):
        with lock:
            metrics["create"] += 1
            row = {"id": f"session-new-{metrics['create']}", "title": "新会话", "title_manual": False,
                "document_ids": list(p.get("document_ids", [])), "updated_at": "2026-11-01T00:00:00Z", "count": 0, "messages": []}
            sessions.insert(0, row)
            return {"session": row}

    bridge.register("rag.create", create)

    def seed_history(p, c):
        if not session_by_id("session-a"):
            sessions.extend([
                {"id": "session-a", "title": "接口问答", "title_manual": True, "document_ids": ["doc-plan", "doc-upload"], "updated_at": "2026-11-01T00:00:00Z", "count": 2, "messages": messages},
                {"id": "session-b", "title": "空会话保留", "title_manual": True, "document_ids": ["doc-upload"], "updated_at": "2026-10-31T00:00:00Z", "count": 0, "messages": []},
                *[{"id": f"session-related-{index:02}", "title": f"资料复习会话 {index:02}", "title_manual": True,
                    "document_ids": ["doc-upload"], "updated_at": "2026-10-29T00:00:00Z", "count": 0, "messages": []}
                    for index in range(1, 21)],
            ])
        return {"ok": True}

    bridge.register("test.seed_history", seed_history)

    def rename(p, c):
        row = session_by_id(p["session_id"])
        if row is None:
            raise RuntimeError("unknown session")
        row["title"] = p["title"]
        row["title_manual"] = True
        metrics["renamed"].append(row["id"])
        return {"session": row}

    bridge.register("rag.rename_session", rename)

    def usage(p, c):
        doc = next((row for row in docs if row["id"] == p["document_id"]), None)
        names = [row["title"] for row in sessions if p["document_id"] in row["document_ids"]]
        return {"document": doc, "session_names": names}

    bridge.register("rag.document_usage", usage)

    def delete_document(p, c):
        with lock:
            doc_id = p["document_id"]
            doc = next((row for row in docs if row["id"] == doc_id), None)
            if doc is None:
                raise RuntimeError("missing document")
            content_deleted = doc.get("canonical_id") == doc_id
            # This fake represents the server's selected logical item only.
            metrics["deleted"].append(doc_id)
            for row in sessions:
                row["document_ids"] = [value for value in row["document_ids"] if value != doc_id]
                for msg in row["messages"]:
                    for passage in msg.get("passages", []):
                        if passage.get("document_id") == doc_id:
                            passage["deleted"] = True
            docs.remove(doc)
            return {"ok": True, "document_id": doc_id, "content_deleted": content_deleted,
                "canonical_id": None, "documents": list(docs), "sessions": summaries()}

    bridge.register("rag.delete_document", delete_document)

    def delete_session(p, c):
        sessions[:] = [row for row in sessions if row["id"] != p["session_id"]]
        return {"ok": True}

    bridge.register("rag.delete", delete_session)

    def pick_files(p, c):
        metrics["pick_started"] = metrics.get("pick_started", 0) + 1
        # Keep the native picker response pending so the browser can switch
        # conversations before the import completes, matching a late dialog.
        import_gate.wait(timeout=20)
        return {"paths": [str(root / "auto-added-note.txt")]}

    bridge.register("rag.pick_files", pick_files)

    def import_files(p, c):
        imported = {"id": "doc-uploaded", "title": "自动加入的上传资料.txt", "source": "本地文件",
            "plan_ids": [], "status": "not_uploaded", "total_chunks": 3, "uploaded_chunks": 0, "error": ""}
        docs[:] = [row for row in docs if row["id"] != imported["id"]] + [imported]
        return {"documents": [imported]}

    bridge.register("rag.import_files", import_files)
    bridge.register("test.release_import", lambda p, c: (import_gate.set() or {"ok": True}))

    def prepare(p, c):
        metrics["prepare"] += 1
        jobs = [{"id": "job-" + doc_id, "document_id": doc_id, "state": "ready", "uploaded_chunks": 3, "total_chunks": 3}
            for doc_id in p.get("document_ids", [])]
        return {"scope_id": "scope-a", "jobs": jobs}

    bridge.register("rag.prepare", prepare)
    bridge.register("rag.preparation", lambda p, c: {"scope_id": "scope-a", "jobs": []})
    bridge.register("rag.cancel_preparation", lambda p, c: {"scope_id": "scope-a", "jobs": []})

    def send(p, c):
        metrics["send"] += 1
        row = session_by_id(p["session_id"])
        if not row.get("title_manual"):
            row["title"] = p["text"][:28]
        row["messages"] += [
            {"id": "new-q", "role": "user", "content": p["text"], "passages": []},
            {"id": "new-a", "role": "assistant", "content": "本机替身回答 [1]", "passages": [
                {"number": 1, "document_id": row["document_ids"][0], "seq": 1, "text": "新的资料原文。", "title": "当前资料"},
            ]},
        ]
        row["count"] = len(row["messages"])
        return {"session": row}

    bridge.register("rag.send", send)
    bridge.register("test.trace", lambda p, c: {**metrics, "sessions": summaries(), "documents": list(docs)})
    bridge.register("voice.status", lambda p, c: {"state": "not_downloaded", "installed": False, "invalid": False,
        "model_name": "假语音", "size_bytes": 1, "download_url": "", "job_id": None, "downloaded_bytes": 0, "total_bytes": 0, "cancelling": False})
    bridge.register("voice.context", lambda p, c: {"terms": [], "question": "", "protected": []})
    bridge.register("voice.start", lambda p, c: {"ok": True})
    bridge.register("voice.stop", lambda p, c: {"session_id": p.get("session_id", ""), "text": "", "raw_text": "", "replacements": []})
    bridge.register("voice.cancel", lambda p, c: {"ok": True})

    view.resize(1440, 1000)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if (root / "done269").exists() else None)
    timer.start(100)
    try:
        app.exec()
    finally:
        bridge.close()


def _drive(root: Path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    dirs = {name: root / name for name in ("temp", "tmp", "home", "local", "appdata", "node-cache")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*", MF_CDP_ENDPOINT=f"http://127.0.0.1:{port}",
        PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", TEMP=str(dirs["temp"]), TMP=str(dirs["tmp"]),
        TMPDIR=str(dirs["tmp"]), HOME=str(dirs["home"]), LOCALAPPDATA=str(dirs["local"]), APPDATA=str(dirs["appdata"]),
        npm_config_cache=str(dirs["node-cache"]), XDG_CACHE_HOME=str(dirs["node-cache"]),
        PATH="D:/MentorFlow/.local/node;" + os.environ.get("PATH", ""))
    log_path = root / "qt269.log"
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(root)], env=env, stdout=log, stderr=log)
        try:
            run = subprocess.run([str(NODE), str(Path(__file__).with_name("browser269.mjs")), str(root)], env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            assert run.returncode == 0, run.stdout + run.stderr
        finally:
            (root / "done269").touch()
            child.wait(timeout=25)
    return json.loads((root / "result269.json").read_text(encoding="utf-8"))


def test_rag269_library_sessions_delete_rename_and_upload_current_session(tmp_path):
    if not NODE.is_file() or not (DEPS / "node_modules/vite/bin/vite.js").is_file():
        pytest.skip("指定的前端运行环境不可用")
    root = tmp_path / "browser269"
    _build(root)
    result = _drive(root)
    assert result.get("ok"), result
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for source in result["screenshots"]:
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = root / "screenshots" / source_path
        shutil.copy2(source_path, OUTPUT / source_path.name)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--host":
    _host(Path(sys.argv[2]))
