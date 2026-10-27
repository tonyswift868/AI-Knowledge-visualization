"""Real App shell and SyncStatusChip rendered in isolated Qt Chromium with a local fake bridge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "refactor/learning/web"
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
sys.path.insert(0, str(REPO))


def _build(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).parent
    (root / "index.html").write_text('<meta charset="utf-8"><div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text("import " + json.dumps((here / "browser203.tsx").as_posix()), encoding="utf-8")
    config = (
        "import base from " + json.dumps((WEB / "vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ",resolve:{...base.resolve,alias:[...(base.resolve?.alias??[]),{find:'./bridge',replacement:"
        + json.dumps((here / "bridge203.ts").as_posix()) + "}]},build:{outDir:'dist',emptyOutDir:true}}"
    )
    (root / "vite.config.mjs").write_text(config, encoding="utf-8")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS), PATH=str(NODE.parent) + os.pathsep + os.environ.get("PATH", ""))
    result = subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _browser(root: Path) -> dict:
    ARTIFACTS = Path(os.environ.get("SYNC203_ARTIFACTS", str(root / "screenshots")))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app = QApplication([])
    view = QWebEngineView()
    view.resize(1200, 760)
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    view.show()

    def pump(seconds=.05):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(.005)

    def js(expression):
        replies = []
        view.page().runJavaScript(expression, replies.append)
        end = time.monotonic() + 5
        while not replies and time.monotonic() < end:
            pump()
        assert replies, "browser JS evaluation timed out"
        return replies[0]

    def until(expression, reason):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if js(expression): return
            pump(.04)
        raise AssertionError(f"{reason}; page={js('document.body.innerText.slice(0,1200)')}")

    def settle_layout():
        js("window.scrollTo(0,0); window.__sync203Settled=false; "
           "Promise.all(document.getAnimations().filter(a=>Number.isFinite(a.effect?.getComputedTiming().endTime))"
           ".map(a=>a.finished.catch(()=>{}))).then(()=>requestAnimationFrame(()=>requestAnimationFrame(()=>window.__sync203Settled=true))); true")
        until("window.__sync203Settled === true", "finite CSS animations and two paint frames settle")

    try:
        until("!!document.querySelector('.chip.sync.retrying')", "retrying chip appears")
        assert "正在重试" in js("document.querySelector('.chip.sync').textContent")
        assert "模型正常" in js("document.querySelector('.chip.model-status').textContent")
        settle_layout()
        assert js("(()=>{const r=document.querySelector('.chip.sync').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight})()"), "retrying chip fully visible before capture"
        view.grab().save(str(ARTIFACTS / "retrying.png"))
        js("document.querySelector('.chip.sync').click()")
        until("!!document.querySelector('#sync-status-details')", "details open")
        detail = js("document.querySelector('#sync-status-details').innerText")
        initial_status = json.loads(js("JSON.stringify(window.sync203.currentStatus())"))
        expected_last_success = js("new Date(" + json.dumps(initial_status["at"]) + ").toLocaleString()")
        expected_retry = js("new Date(" + json.dumps(initial_status["retry_at"]) + ").toLocaleString()")
        assert expected_last_success in detail, detail
        assert "7 条" in detail and expected_retry in detail and "网络暂时不可用" in detail, detail
        assert "模型正常" in js("document.querySelector('.chip.model-status').textContent")
        view.grab().save(str(ARTIFACTS / "details.png"))
        js("const retryDeadline=Date.parse(window.sync203.currentStatus().retry_at); " +
           "Date.now=()=>retryDeadline+60000; " +
           "window.sync203.setStatus(window.sync203.currentStatus())")
        until("document.querySelector('#sync-status-details')?.innerText.includes('正在尝试')",
              "old retry deadline is active after advancing the browser clock")
        assert "正在重试" in js("document.querySelector('.chip.sync').textContent")
        js("document.querySelector('.sync-detail-retry').click()")
        until("!!document.querySelector('.chip.sync.synced')", "retry success status")
        assert "已同步" in js("document.querySelector('.chip.sync').textContent")
        until("document.querySelector('#sync-status-details')?.innerText.includes('已恢复')", "success shows recovered marker")
        js("document.querySelector('.sync-detail-close').click()")
        assert "模型正常" in js("document.querySelector('.chip.model-status').textContent")
        view.grab().save(str(ARTIFACTS / "synced.png"))

        js("window.sync203.setStatus({state:'failed',at:null,message:'仍无法连接',pending:null,retry_at:null,last_error:null})")
        until("!!document.querySelector('.chip.sync.failed')", "failure remains distinct")
        assert "同步失败" in js("document.querySelector('.chip.sync').textContent")
        js("window.sync203.setStatus({state:'reauth',at:null,message:'',pending:3,retry_at:null,last_error:null})")
        until("!!document.querySelector('.chip.sync.reauth')", "reauth distinct")
        assert "需要重新登录" in js("document.querySelector('.chip.sync').textContent")
        js("document.querySelector('.chip.sync').click()")
        until("!!document.querySelector('#sync-status-details .sync-auth-note')", "reauth details")
        assert not js("!!document.querySelector('.sync-detail-retry')"), "reauth must not offer a fake recovery action"
        js("window.sync203.setStatus({state:'superseded',at:null,message:'',pending:3,retry_at:null,last_error:null})")
        until("!!document.querySelector('.chip.sync.superseded')", "superseded distinct")
        assert "已在别处登录" in js("document.querySelector('.chip.sync').textContent")
        assert "模型正常" in js("document.querySelector('.chip.model-status').textContent")
        js("window.sync203.setStatus({state:'waiting',at:null,message:'',pending:null,retry_at:null,last_error:null})")
        until("!!document.querySelector('.chip.sync.waiting')", "waiting distinct")
        assert "等待账号连接" in js("document.querySelector('.chip.sync').textContent")
        js("window.sync203.setStatus({state:'retrying',at:null,message:'',pending:null,retry_at:null,last_error:null})")
        until("document.querySelector('#sync-status-details')?.innerText.includes('正在尝试')", "missing retry time shows active attempt")
        assert "正在重试" in js("document.querySelector('.chip.sync').textContent")
        result = {"states": ["retrying", "failed", "reauth", "superseded", "waiting", "synced"],
                  "detail": detail, "retryCalls": js("window.sync203.retryCalls()"),
                  "modelStatePreserved": js("document.querySelector('.chip.model-status')?.textContent.includes('模型正常')")}
        (root / "result203.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        view.close()
        pump(.1)


def test_sync203_status_chip_details_retry_and_auth_states(tmp_path):
    ARTIFACTS = Path(os.environ.get("SYNC203_ARTIFACTS", str(tmp_path / "screenshots")))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    _build(tmp_path)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               QT_QPA_PLATFORM="offscreen", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu")
    child = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--browser", str(tmp_path)],
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads((tmp_path / "result203.json").read_text(encoding="utf-8"))
    assert result["retryCalls"] == 1
    assert result["modelStatePreserved"]
    assert result["states"] == ["retrying", "failed", "reauth", "superseded", "waiting", "synced"]
    assert all((ARTIFACTS / name).is_file() for name in ("retrying.png", "details.png", "synced.png"))


def _browser_expired_retry(root: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app = QApplication([])
    view = QWebEngineView()
    view.resize(1000, 700)
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    view.show()

    def js(expression):
        replies = []
        view.page().runJavaScript(expression, replies.append)
        end = time.monotonic() + 5
        while not replies and time.monotonic() < end:
            app.processEvents()
            time.sleep(.005)
        assert replies, "browser JS evaluation timed out"
        return replies[0]

    def until(expression, label):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if js(expression):
                return
            app.processEvents()
            time.sleep(.02)
        raise AssertionError(f"{label}; page={js('document.body.innerText.slice(0,900)')}")

    try:
        until("!!document.querySelector('.chip.sync.retrying')", "retrying chip appears")
        js("window.sync203.setStatus({state:'retrying',at:null,message:'',pending:3," +
           "retry_at:new Date(Date.now()-60000).toISOString(),last_error:null})")
        until("!!document.querySelector('.chip.sync.retrying')", "expired retry remains retrying")
        assert "正在重试" in js("document.querySelector('.chip.sync').textContent")
        js("document.querySelector('.chip.sync').click()")
        until("!!document.querySelector('#sync-status-details')", "details open")
        until("document.querySelector('#sync-status-details')?.innerText.includes('正在尝试')",
              "past retry time displays active attempt")
        assert "正在尝试" in js("document.querySelector('#sync-status-details').innerText")
    finally:
        view.close()
        app.processEvents()


if __name__ == "__main__" and len(sys.argv) == 3:
    if sys.argv[1] == "--browser":
        _browser(Path(sys.argv[2]))
    elif sys.argv[1] == "--browser-expired":
        _browser_expired_retry(Path(sys.argv[2]))
