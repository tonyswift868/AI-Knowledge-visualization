"""Real React/QtWebEngine coverage for RAG background preparation UX."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
PYTHON = Path("D:/MentorFlow/.venv/Scripts/python.exe")
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
WEB = REPO / "refactor/learning/web"
OUTPUT = Path("D:/MentorFlow/.local/test-tmp/ragdel269c/legacy/rag263/screenshots")
sys.path.insert(0, str(REPO))


def _build(root):
    root.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name("browser263.tsx")
    (root / "entry.tsx").write_text("import " + json.dumps(entry.as_posix()), encoding="utf-8")
    (root / "index.html").write_text(
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div><script type="module" src="/entry.tsx"></script>',
        encoding="utf-8")
    (root / "vite.config.mjs").write_text(
        "import base from " + json.dumps((WEB / "vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ",build:{outDir:'dist',emptyOutDir:true}}", encoding="utf-8")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS), TEMP=str(root / "temp"),
        TMP=str(root / "tmp"), TMPDIR=str(root / "tmp"), HOME=str(root / "home"),
        LOCALAPPDATA=str(root / "localappdata"),
        APPDATA=str(root / "appdata"), npm_config_cache=str(root / "node-cache"),
        XDG_CACHE_HOME=str(root / "node-cache"),
        PATH="D:/MentorFlow/.local/node;" + os.environ.get("PATH", ""))
    for key in ("TEMP", "TMP", "HOME", "LOCALAPPDATA", "APPDATA", "npm_config_cache"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    run = subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"),
        "build", "--config", str(root / "vite.config.mjs")], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr


def _host(root):
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
    docs = [
        {"id": "doc-a", "title": "第一份资料.md", "source": "本地文件", "plan_ids": [], "status": "not_uploaded", "total_chunks": 10, "uploaded_chunks": 0, "error": ""},
        {"id": "doc-b", "title": "第二份资料.md", "source": "本地文件", "plan_ids": [], "status": "not_uploaded", "total_chunks": 10, "uploaded_chunks": 0, "error": ""},
        {"id": "doc-c", "title": "取消资料.md", "source": "本地文件", "plan_ids": [], "status": "not_uploaded", "total_chunks": 10, "uploaded_chunks": 0, "error": ""},
        {"id": "doc-d", "title": "旧账号资料.md", "source": "本地文件", "plan_ids": [], "status": "not_uploaded", "total_chunks": 10, "uploaded_chunks": 0, "error": ""},
    ]
    plans=[]
    def session(sid, doc):
        return {"id": sid, "title": "会话", "document_ids": [doc], "updated_at": "2026-10-31T00:00:00Z", "count": 0, "messages": []}
    sessions = [session("session-a", "doc-a"), session("session-b", "doc-b"),
                session("session-c", "doc-c"), session("session-d", "doc-d")]
    jobs = {}
    metrics = {"send": 0, "prepare": 0, "cancel": 0, "model": 0, "last_prepared": []}
    lock=threading.RLock()
    scope={"id":"scope-account-a"}
    def register(name, handler): bridge.register(name, handler)
    register("rag.sources", lambda p,c: {"plans":plans,"documents":docs,"sessions":[{k:v for k,v in x.items() if k!="messages"} for x in sessions],"server_status":{"state":"ready"}})
    register("rag.get", lambda p,c: {"session": next(dict(x) for x in sessions if x["id"]==p["session_id"])})
    def create(p,c):
        row=session("created-session",p.get("document_ids",[])[0]);sessions.insert(0,row);return {"session":dict(row)}
    register("rag.create",create)
    def update(p,c):
        row=next(x for x in sessions if x["id"]==p["session_id"]);row["document_ids"]=p["document_ids"];return {"session":dict(row)}
    register("rag.update_documents",update)
    def prepare(p,c):
        with lock:
            metrics["prepare"]+=1;metrics["last_prepared"]=list(p["document_ids"]);out=[]
            for doc in p["document_ids"]:
                job=jobs.get(doc)
                if not job or job["state"] in ("ready","cancelled"):
                    job={"id":"job-"+doc,"document_id":doc,"state":"running","uploaded_chunks":3,"total_chunks":10};jobs[doc]=job
                out.append(dict(job))
            return {"scope_id":scope["id"],"jobs":out}
    register("rag.prepare",prepare)
    def preparation(p,c):
        ids=set(p.get("document_ids") or []);return {"scope_id":scope["id"],"jobs":[dict(j) for d,j in jobs.items() if not ids or d in ids]}
    register("rag.preparation",preparation)
    def cancel(p,c):
        with lock:
            metrics["cancel"]+=1
            for job in jobs.values():
                if job["id"] in p["job_ids"]:job["state"]="cancelled"
            return preparation({},c)
    register("rag.cancel_preparation",cancel)
    def send(p,c):
        with lock:
            metrics["send"]+=1;metrics["model"]+=1
            row=next(x for x in sessions if x["id"]==p["session_id"])
            row["messages"] += [{"id":"q","role":"user","content":p["text"],"passages":[]},{"id":"a","role":"assistant","content":"假回答","passages":[]}]
            row["count"]=len(row["messages"])
            return {"session":dict(row)}
    register("rag.send",send)
    def set_progress(p,c):
        doc=metrics["last_prepared"][0]
        job=jobs[doc];job["state"]=p["state"];job["uploaded_chunks"]=p["uploaded_chunks"]
        if p["state"]=="ready":
            for item in docs:
                if item["id"]==doc:item.update(status="ready",uploaded_chunks=10)
        bridge.push("rag.progress",{"document_id":doc,"job_id":job["id"],"scope_id":scope["id"],"preparation_state":job["state"],"uploaded_chunks":job["uploaded_chunks"],"total_chunks":job["total_chunks"]})
        return {"ok":True}
    register("test.progress",set_progress)
    def switch_account(p,c):
        with lock:
            stale_job=jobs.get("doc-d")
            stale_event={"document_id":"doc-d","job_id":(stale_job or {}).get("id","job-doc-d"),
                "scope_id":scope["id"],"sequence":999,"preparation_state":"running",
                "uploaded_chunks":9,"total_chunks":10,"status":"uploading"}
            docs.clear();docs.append({"id":"doc-new","title":"新账号资料.md","source":"本地文件","plan_ids":[],"status":"uploading","total_chunks":4,"uploaded_chunks":2,"error":""})
            sessions.clear();sessions.append(session("session-new","doc-new"));jobs.clear();scope["id"]="scope-account-b"
            bridge.push("scope_changed",{"account_id":"account-b"})
            bridge.push("rag.progress",stale_event)
        return {"ok":True}
    register("test.switch_account",switch_account)
    register("test.trace",lambda p,c:{**metrics,"jobs":{k:dict(v) for k,v in jobs.items()},"scope_id":scope["id"]})
    register("voice.status",lambda p,c:{"state":"not_downloaded","installed":False,"invalid":False,"model_name":"假语音","size_bytes":1,"download_url":"","job_id":None,"downloaded_bytes":0,"total_bytes":0,"cancelling":False})
    register("voice.context",lambda p,c:{"terms":[],"question":"","protected":[]})
    register("voice.start",lambda p,c:{"ok":True})
    register("voice.stop",lambda p,c:{"session_id":p.get("session_id",""),"text":"","raw_text":"","replacements":[]})
    register("voice.cancel",lambda p,c:{"ok":True})
    view.resize(1200,1000);view.show();view.setUrl(QUrl.fromLocalFile(str(root/"dist/index.html")))
    timer=QTimer();timer.timeout.connect(lambda:app.quit() if (root/"done263").exists() else None);timer.start(100)
    try:app.exec()
    finally:bridge.close()


def _drive(root):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1",0));port=listener.getsockname()[1]
    dirs={name:root/name for name in ("temp","tmp","localappdata","appdata","node-cache","home")}
    for path in dirs.values():path.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,QT_QPA_PLATFORM="offscreen",QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",MF_CDP_ENDPOINT=f"http://127.0.0.1:{port}",
        PYTHONIOENCODING="utf-8",PYTHONDONTWRITEBYTECODE="1",TEMP=str(dirs["temp"]),TMP=str(dirs["tmp"]),
        LOCALAPPDATA=str(dirs["localappdata"]),APPDATA=str(dirs["appdata"]),npm_config_cache=str(dirs["node-cache"]),
        NODE_CACHE_DIR=str(dirs["node-cache"]),XDG_CACHE_HOME=str(dirs["node-cache"]),
        TMPDIR=str(dirs["tmp"]),HOME=str(dirs["home"]),PATH="D:/MentorFlow/.local/node;"+os.environ.get("PATH",""))
    log_path=root/"qt263.log"
    with log_path.open("w",encoding="utf-8") as log:
        child=subprocess.Popen([str(PYTHON),"-B",str(Path(__file__)),"--host",str(root)],env=env,stdout=log,stderr=log)
        try:
            run=subprocess.run([str(NODE),str(Path(__file__).with_name("browser263.mjs")),str(root)],env=env,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=180)
            assert run.returncode==0,run.stdout+run.stderr
        finally:
            (root/"done263").touch();child.wait(timeout=25)
    return json.loads((root/"result263.json").read_text(encoding="utf-8"))


def test_rag263_real_react_page_background_jobs_preserve_draft_and_account_scope(tmp_path):
    if not PYTHON.is_file() or not NODE.is_file() or not (DEPS/"node_modules/vite/bin/vite.js").is_file():pytest.skip("指定的前端运行环境不可用")
    root=tmp_path/"browser263";_build(root);result=_drive(root)
    assert result.get("ok"),result
    OUTPUT.mkdir(parents=True,exist_ok=True)
    for theme in ("sakura","aurora"):
        source=root/"screenshots"/f"rag263-{theme}.png";assert source.is_file(),source
        shutil.copy2(source,OUTPUT/source.name)


if __name__=="__main__" and len(sys.argv)>1 and sys.argv[1]=="--host":
    _host(Path(sys.argv[2]))
