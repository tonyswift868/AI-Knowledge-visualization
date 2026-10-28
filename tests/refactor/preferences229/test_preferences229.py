"""Browser regressions for same-account setting-save ordering (BUG-190)."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "refactor/learning/web"
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
sys.path.insert(0, str(REPO))


def _host(root):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge, BridgeError
    from tests.refactor.simulation.engine import ensure_offscreen_fonts

    app = QApplication([])
    ensure_offscreen_fonts()
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    view.setPage(page)
    bridge = AppBridge(page)
    channel = QWebChannel(page)
    channel.registerObject("mf", bridge)
    page.setWebChannel(channel)

    settings = {"version": 1, "theme": "sakura", "font_scale": 1, "reduce_motion": True, "teacher_style": "warm",
        "detail": "normal", "examples": "normal", "analogy_domains": ["life"], "refresh_time": "04:00",
        "reminder_enabled": False, "reminder_time": "20:00", "show_plan_name_in_notification": True, "chat_persona": ""}
    lock = threading.Lock()
    revision = 0
    counts = {}
    preset_calls = 0
    model_calls = 0

    def save(params, _context):
        nonlocal revision, settings
        changes = params.get("changes", {})
        if changes.get("detail") == "detailed" or changes.get("chat_persona") == "slow failure":
            time.sleep(0.65)
            raise BridgeError("OFFLINE_TEST", "模拟保存失败")
        with lock:
            revision += 1
            own_revision = revision
            settings = {**settings, **changes}
            snapshot = dict(settings)
            counts[own_revision] = changes
        if changes.get("theme") == "aurora" or (isinstance(changes.get("chat_persona"), str) and changes["chat_persona"].startswith("slow success")):
            time.sleep(1.5)
        else:
            time.sleep(0.08)
        return {"settings": snapshot, "settings_revision": own_revision, "account_id": "a229"}

    bridge.register("settings.save", save)
    def settings_get(_params, _context):
        nonlocal revision, settings
        with lock:
            own_revision = revision
            snapshot = dict(settings)
        time.sleep(0.8)
        return {"settings": snapshot, "settings_revision": own_revision, "account_id": "a229"}
    bridge.register("settings.get", settings_get)
    def bootstrap(_params, _context):
        with lock:
            snapshot = dict(settings)
            own_revision = revision
        return {"version":"test", "account":{"name":"A","account_id":"a229","role_label":"学习者"}, "settings":snapshot,
            "settings_revision":own_revision, "account_id":"a229", "options":{}, "notice":None, "ready":True}
    bridge.register("app.bootstrap", bootstrap)
    bridge.register("sync.status", lambda _p, _c: None)
    bridge.register("models.status", lambda _p, _c: {"state":"unknown", "short":"待验证"})
    bridge.register("update.status", lambda _p, _c: {"available":False,"busy":False,"state":"UP_TO_DATE","version":"test","current":"test"})
    bridge.register("update.automatic", lambda _p, _c: {"available":False,"busy":False,"state":"UP_TO_DATE","version":"test","current":"test"})
    bridge.register("plans.list", lambda _p, _c: {"plans":[],"study_day":"229-test"})
    bridge.register("test.push", lambda p, _c: bridge.push(p["name"], p.get("data")) or {"ok": True})
    def remote(params, _context):
        nonlocal revision, settings
        with lock:
            revision += 1
            settings = {**settings, "theme": "sakura"}
        return {"ok": True}
    bridge.register("test.remote", remote)
    bridge.register("chat.ui_get", lambda _p, _c: {"list_collapsed": False, "available": True})
    bridge.register("chat.list", lambda _p, _c: {"sessions": []})
    def teacher_presets(_params, _context):
        time.sleep(0.7)
        return {"presets": [{"id": "preset-a", "name": "现有老师", "persona": "", "style": "warm", "detail": "brief", "examples": "normal", "analogies": ["life"]}], "default_id": "preset-a"}
    bridge.register("settings.teacher_presets", teacher_presets)
    def save_teacher_presets(_params, _context):
        nonlocal preset_calls
        preset_calls += 1
        return {"presets": [], "default_id": ""}
    bridge.register("settings.save_teacher_presets", save_teacher_presets)
    bridge.register("models.providers", lambda _p, _c: {"providers": [{"id":"deepseek","name":"DeepSeek","default_base_url":"https://invalid.local","default_model":"offline","search_supported":False,"search_verified":False}]})
    model_presets = [{"id":"model-a","provider_id":"deepseek","display_name":"A 模型","base_url":"https://invalid.local","model":"a","temperature":None,"timeout":10,"has_api_key":True},
        {"id":"model-b","provider_id":"deepseek","display_name":"B 模型","base_url":"https://invalid.local","model":"b","temperature":None,"timeout":10,"has_api_key":True}]
    bridge.register("models.list", lambda _p, _c: {"presets": model_presets, "active_id":"model-a"})
    def select_model(_params, _context):
        nonlocal model_calls
        model_calls += 1
        time.sleep(0.65)
        return {"presets": model_presets, "active_id":"model-b"}
    bridge.register("models.select", select_model)
    bridge.register("models.delete", lambda _p, _c: {"presets": model_presets, "active_id":"model-a"})
    bridge.register("test.stats", lambda _p, _c: {"preset_saves": preset_calls, "model_calls": model_calls})
    view.resize(800, 500)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if (root / "done").exists() else None)
    timer.start(100)
    try:
        app.exec()
    finally:
        bridge.close()


def test_settings_save_responses_merge_by_revision_and_old_failure_does_not_rollback(tmp_path):
    if not NODE.exists() or not (DEPS / "node_modules/vite/bin/vite.js").exists():
        pytest.skip("configured frontend toolchain unavailable")
    root = tmp_path / "preferences229"
    root.mkdir()
    here = Path(__file__).resolve().parent
    (root / "index.html").write_text('<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text('import ' + json.dumps((here / "preferences229.tsx").as_posix()), encoding="utf-8")
    (root / "vite.config.mjs").write_text('import base from ' + json.dumps((WEB / "vite.config.mjs").as_uri()) + ';export default {...base,root:' + json.dumps(root.as_posix()) + ',build:{outDir:"dist",emptyOutDir:true}}', encoding="utf-8")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS), TEMP=str(root), TMP=str(root))
    build = subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert build.returncode == 0, build.stdout + build.stderr

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env.update(QT_QPA_PLATFORM="offscreen", QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*", MF_CDP_ENDPOINT=f"http://127.0.0.1:{port}",
        PYTHONDONTWRITEBYTECODE="1", PATH=str(NODE.parent) + os.pathsep + os.environ.get("PATH", ""))
    log_path = root / "qt.log"
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(root)], env=env, stdout=log, stderr=log)
        try:
            result = subprocess.run([str(NODE), str(here / "preferences229.mjs"), str(root)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            (root / "done").touch()
            child.wait(timeout=15)

    data = json.loads((root / "preferences229-result.json").read_text(encoding="utf-8"))
    assert data["ok"], data


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--host":
    _host(Path(sys.argv[2]))
