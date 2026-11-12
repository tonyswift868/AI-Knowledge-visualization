"""Real React/App shell model chip states over an isolated Qt WebEngine bridge."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "refactor/learning/web"
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
ARTIFACTS = Path("D:/MentorFlow/.local/test-tmp/model199")
sys.path.insert(0, str(REPO))


def _build(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name("browser199.tsx")
    (root / "index.html").write_text(
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>'
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>',
        encoding="utf-8",
    )
    (root / "entry.tsx").write_text("import " + json.dumps(entry.as_posix()), encoding="utf-8")
    config = (
        "import base from " + json.dumps((WEB / "vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ",build:{outDir:'dist',emptyOutDir:true}}"
    )
    (root / "vite.config.mjs").write_text(config, encoding="utf-8")
    env = dict(os.environ)
    env["MENTORFLOW_WEB_DEPS"] = str(DEPS)
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _host(root: Path) -> None:
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
            if info.requestUrl().scheme() in ("http", "https"):
                info.block(True)

    blocker = Offline(page)
    profile.setUrlRequestInterceptor(blocker)
    view.setPage(page)
    bridge = AppBridge(page)
    channel = QWebChannel(page)
    channel.registerObject("mf", bridge)
    page.setWebChannel(channel)

    account_a = {"name": "测试账号 A", "account_id": "account-199-a", "role_label": "学习者"}
    account_b = {"name": "测试账号 B", "account_id": "account-199-b", "role_label": "学习者"}
    settings = {"version": 1, "theme": "sakura", "font_scale": 1, "reduce_motion": True,
                "analogy_domains": [], "teacher_style": "warm", "detail": "normal", "examples": "normal",
                "refresh_time": "04:00", "reminder_enabled": False, "reminder_time": "20:00",
                "show_plan_name_in_notification": True, "chat_persona": "warm"}
    status_calls = 0
    manual_tests = 0
    classroom_enters = 0

    def register(name, fn):
        bridge.register(name, fn)

    register("app.bootstrap", lambda p, c: {"version": "test", "account": account_a, "settings": settings,
                                             "options": {}, "notice": None, "ready": True})
    register("settings.get", lambda p, c: {"settings": settings, "options": {}})
    register("settings.save", lambda p, c: {"settings": settings})
    register("sync.status", lambda p, c: None)
    register("models.status", lambda p, c: model_status())
    register("models.providers", lambda p, c: {"providers": []})
    register("models.list", lambda p, c: {"presets": [], "active_id": None})
    def model_test(p, c):
        nonlocal manual_tests
        manual_tests += 1
        (root / "manual-test-count199").write_text(
            str(manual_tests), encoding="ascii")
        bridge.push("model_status", {"state": "checking", "short": "正在检查模型",
                                      "account_id": account_b["account_id"]})
        def finish_successfully():
            import time
            time.sleep(.08)
            bridge.push("model_status", {"state": "ok", "short": "模型正常",
                                          "account_id": account_b["account_id"]})
        threading.Thread(target=finish_successfully, name="model199-test-result", daemon=True).start()
        return {"ok": True}
    register("models.test", model_test)
    register("models.save", lambda p, c: {"presets": [], "active_id": None})
    register("plans.list", lambda p, c: {"plans": [], "study_day": "test-day"})
    register("update.status", lambda p, c: {"available": False, "busy": False, "state": "UP_TO_DATE",
                                              "version": "test", "current": "test"})
    register("update.automatic", lambda p, c: {"available": False, "busy": False, "state": "UP_TO_DATE",
                                                 "version": "test", "current": "test"})
    def enter_classroom(p, c):
        nonlocal classroom_enters
        classroom_enters += 1
        return classroom()
    register("classroom.enter", enter_classroom)
    register("classroom.state", lambda p, c: classroom())
    register("classroom.leave", lambda p, c: {"ok": True})
    register("speech.status", lambda p, c: {"ready": False, "components": [], "download": None})
    register("voice.status", lambda p, c: {"available": False, "ready": False})
    register("usage.summary", lambda p, c: {"ready": False})
    register("logs.open", lambda p, c: {"ok": True})

    # Deliberately return an old-account result slowly. App must ignore it after a
    # scope_changed/ready pair starts the current account's status query.
    def model_status():
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            import time
            time.sleep(1.2)
            return {"state": "unconfigured", "short": "旧账号未配置 API Key", "account_id": account_a["account_id"]}
        return {"state": "checking", "short": "正在检查模型", "account_id": account_b["account_id"]}

    def classroom():
        return {"plan": {"id": "plan-199", "goal": "状态检查时可继续上课", "state": "active"},
                "module": {"id": "module-199", "title": "测试模块", "number": 1, "total": 1, "state": "active"},
                "points": [], "step": "teach", "question": None, "messages": [], "can_skip": False,
                "red": [], "more_modules": False, "check": None, "today": None}

    for method in ("update.check", "update.install", "update.defer", "models.delete", "logs.open"):
        # Already registered methods above are skipped; remaining app-shell actions
        # used by hidden route components return inert local fixtures.
        try:
            register(method, lambda p, c: {"ok": True})
        except ValueError:
            pass

    view.resize(1120, 760)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    timer = QTimer()

    def dispatch_event():
        marker = root / "event199.json"
        if (root / "done199").exists():
            app.quit()
            return
        if not marker.exists():
            return
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            marker.unlink()
            bridge.push(data["name"], data.get("data"))
            if data.get("sequence") is not None:
                from PySide6.QtCore import QTimer
                sequence = str(data["sequence"])
                QTimer.singleShot(25, lambda value=sequence: page.runJavaScript(
                    f'document.documentElement.dataset.event199Ack="{value}"'))
        except (OSError, ValueError, KeyError):
            pass

    timer.timeout.connect(dispatch_event)
    timer.start(30)
    try:
        app.exec()
    finally:
        (root / "host-result199.json").write_text(json.dumps({"manual_tests": manual_tests,
                                                               "classroom_enters": classroom_enters,
                                                               "status_calls": status_calls}, ensure_ascii=False),
                                                    encoding="utf-8")
        bridge.close()


def test_model_status_chip_updates_without_blocking_navigation_or_classroom(tmp_path):
    output = ARTIFACTS / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("model-*.png"):
        old.unlink()
    temp = tmp_path / "temp"
    local = tmp_path / "localappdata"
    temp.mkdir()
    local.mkdir()
    _build(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "QT_QPA_PLATFORM": "offscreen",
                "QTWEBENGINE_REMOTE_DEBUGGING": f"127.0.0.1:{port}",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu --remote-allow-origins=*",
                "MF_CDP_ENDPOINT": f"http://127.0.0.1:{port}", "TEMP": str(temp), "TMP": str(temp),
                "LOCALAPPDATA": str(local), "MODEL199_ARTIFACTS": str(output)})
    (tmp_path / "manual-test-count199").write_text("0", encoding="ascii")
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    with (tmp_path / "qt199.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(tmp_path)],
                                 env=env, stdout=log, stderr=log)
        try:
            drive = subprocess.run([str(NODE), str(Path(__file__).with_name("browser199.mjs")), str(tmp_path), str(output)],
                                   env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
        finally:
            (tmp_path / "done199").touch()
            child.wait(timeout=15)
    # Qt/WebEngine output may carry non-UTF-8 bytes; this text only feeds the failure message.
    log_text = (tmp_path / "qt199.log").read_text(encoding="utf-8", errors="replace")[-5000:]
    assert drive.returncode == 0, drive.stdout + drive.stderr + log_text
    result = json.loads((tmp_path / "result199.json").read_text(encoding="utf-8"))
    assert result["states"] == ["checking", "ok", "error", "unconfigured"]
    assert result["failureActions"] == ["settings", "retry"]
    assert result["retryCalls"] == 1
    host = json.loads((tmp_path / "host-result199.json").read_text(encoding="utf-8"))
    assert host["manual_tests"] == 1
    assert host["classroom_enters"] == 1
    assert host["status_calls"] >= 2


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--host":
    _host(Path(sys.argv[2]))
