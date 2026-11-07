"""Real React/QWebChannel/import/SQLite regression; external services stay offline."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")


def _build(root):
    web = REPO / "refactor/learning/web"
    entry = """
import * as React from 'react';
import {createRoot} from 'react-dom/client';
import {connect,call} from 'BRIDGE';
import {useApp} from 'STORE';
import {Rag} from 'PAGE';
import {Toasts} from 'UI';
import 'THEME';
(window as any).call320=call;
(window as any).switchAccount320=(account_id:string)=>useApp.setState({
  account:{name:'合成账号',account_id,role_label:'学习者'}});
connect().then(()=>{
  (window as any).switchAccount320('rag320-ui-a');
  useApp.setState({route:'rag' as any,ready:true,
    settings:{theme:'sakura',font_scale:1,reduce_motion:true} as any});
  createRoot(document.getElementById('root')!).render(
    <><main style={{height:'100vh',padding:8,boxSizing:'border-box'}}><Rag/></main><Toasts/></>);
});
"""
    for name, relative in {"BRIDGE": "bridge", "STORE": "store", "PAGE": "pages/Rag",
                           "UI": "ui", "THEME": "theme.css"}.items():
        entry = entry.replace(name, (web / "src" / relative).as_posix())
    (root / "entry.tsx").write_text(entry, encoding="utf-8")
    (root / "index.html").write_text(
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div>'
        '<script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "vite.config.mjs").write_text(
        "import base from " + json.dumps((web / "vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ",build:{outDir:'dist',emptyOutDir:true}}", encoding="utf-8")
    run = subprocess.run(
        [str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build",
         "--config", str(root / "vite.config.mjs")],
        env=dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS)),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    (root / "build.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    assert run.returncode == 0, run.stdout + run.stderr


def _host(root):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication, QFileDialog
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app import rag
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.course.store import CourseStore
    from tests.refactor.rag155.test_rag155_backend import CA, FakeGateway, FakeRagServer, rag155_session
    from tests.refactor.simulation.engine import ensure_offscreen_fonts

    app = QApplication([])
    ensure_offscreen_fonts()
    gate = threading.Event()
    entered = threading.Event()
    inputs = root / "inputs"
    inputs.mkdir()
    content = {"seed.txt": "原始资料。", "first.txt": "中文 Unicode λ🙂。",
               "blank.txt": "\ufeff　 \t\r\n",
               "last.html": "<html><body><h1>章节</h1><p>后续HTML正文。</p></body></html>",
               "slow.txt": "切会话期间导入的正文。", "account.txt": "旧账号的资料。",
               "prefix.txt": "全局失败前已提交的正文。", "deleting.txt": "删除待定的正文。"}
    for name, text in content.items():
        (inputs / name).write_text(text, encoding="utf-8")
    batches = {"mixed": ["first.txt", "blank.txt", "last.html"],
               "valid": ["first.txt", "last.html"],
               "slow": ["slow.txt", "blank.txt"],
               "account": ["account.txt", "blank.txt"],
               "global": ["prefix.txt", "deleting.txt"]}
    selection = []
    services = []
    stores = []

    def create_service(subject):
        state = rag155_session(subject)
        store = CourseStore(state.identity.account_id, root / "accounts")
        instance = rag.RagService(
            store=store, account_id=state.identity.account_id, session_provider=lambda: state,
            api_base_url="https://rag320.invalid", ca_bundle=CA, gateway=FakeGateway(),
            http_transport=httpx.MockTransport(FakeRagServer()))
        stores.append(store)
        services.append(instance)
        return instance

    holder = SimpleNamespace(rag=create_service("rag320-ui-a"))
    instance = holder.rag
    seed = inputs / "seed.txt"
    instance._picked_paths.add(str(seed.resolve()))
    seed_id = instance.import_files([str(seed)])["documents"][0]["id"]
    instance.ingest([seed_id])
    original = instance.create([seed_id])["session"]["id"]
    other = instance.create([seed_id])["session"]["id"]
    instance.rename_session(original, "原会话")
    instance.rename_session(other, "另一会话")

    bridge = AppBridge()
    rag.register(bridge, lambda: holder, None)
    bridge.register("voice.status", lambda p, c: {"state": "not_downloaded", "installed": False,
        "invalid": False, "model_name": "测试语音", "size_bytes": 0, "download_url": "",
        "job_id": None, "downloaded_bytes": 0, "total_bytes": 0, "cancelling": False})
    bridge.register("voice.context", lambda p, c: {"terms": [], "question": "", "protected": []})

    def inspect(params, context):
        return {"documents": holder.rag.local.documents(),
                "sessions": holder.rag.local.list_sessions(),
                "original": original, "other": other, "entered": entered.is_set()}

    def choose(params, context):
        gate.clear()
        entered.clear()
        selection[:] = [str(inputs / name) for name in batches[params["batch"]]]
        if params["batch"] == "global":
            holder.rag._pending_deletion_ids.add(
                hashlib.sha256((inputs / "deleting.txt").read_bytes()).hexdigest())
        return {}

    def switch_account(params, context):
        bridge.begin_session_epoch()
        holder.rag.close()
        holder.rag = create_service("rag320-ui-b")
        bridge.push("scope_changed")
        return {"account_id": "rag320-ui-b"}

    bridge.register("test.inspect", inspect)
    bridge.register("test.choose", choose)
    bridge.register("test.release", lambda p, c: gate.set() or {})
    bridge.register("test.switch_account", switch_account)

    original_extract = rag.extract_file

    def controlled_extract(path, **kwargs):
        if Path(path).name in {"slow.txt", "account.txt"}:
            entered.set()
            assert gate.wait(10), "owned extraction gate expired"
        return original_extract(path, **kwargs)

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in {"http", "https"}:
                info.block(True)

    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    blocker = Offline(page)
    profile.setUrlRequestInterceptor(blocker)
    view.setPage(page)
    channel = QWebChannel(page)
    channel.registerObject("mf", bridge)
    page.setWebChannel(channel)
    view.resize(1280, 900)
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if (root / "done").exists() else None)
    timer.start(100)
    QTimer.singleShot(120_000, app.quit)
    try:
        with patch.object(QFileDialog, "getOpenFileNames", return_value=(selection, "")), \
                patch.object(rag, "extract_file", controlled_extract):
            view.show()
            view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
            app.exec()
    finally:
        gate.set()
        bridge.close()
        for instance in services:
            instance.close()
        deadline = time.monotonic() + 8
        while any(not instance.local._closed for instance in services) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert all(instance.local._closed for instance in services)
        for store in stores:
            store.close()


def test_rag320_real_browser_partial_import_and_scope(tmp_path):
    if not NODE.is_file() or not (DEPS / "node_modules/vite/bin/vite.js").is_file():
        pytest.skip("指定Node/web依赖不可用")
    root = tmp_path / "browser320"
    root.mkdir()
    _build(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
               QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
               MF_CDP_ENDPOINT=f"http://127.0.0.1:{port}",
               TEMP=str(root), TMP=str(root), TMPDIR=str(root),
               APPDATA=str(root / "appdata"), LOCALAPPDATA=str(root / "local"),
               XDG_CACHE_HOME=str(root / "cache"), PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8")
    with (root / "qt.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(root)],
                                env=env, stdout=log, stderr=log, cwd=REPO)
        try:
            run = subprocess.run([str(NODE), str(Path(__file__).with_name("browser320.mjs")), str(root)],
                                 env=env, capture_output=True, text=True, encoding="utf-8", timeout=100)
            (root / "browser.log").write_text(run.stdout + run.stderr, encoding="utf-8")
            assert run.returncode == 0, run.stdout + run.stderr
        finally:
            (root / "done").touch()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)
    with (root / "result.json").open(encoding="utf-8") as stream:
        assert json.load(stream)["ok"]


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--host":
    sys.path.insert(0, str(REPO))
    _host(Path(sys.argv[2]))
