"""Real React/QWebEngine checks for account-isolated drafts and reminder setting UI.

All persistence is handled by an offline bridge fixture; no user profile, model,
server, or network access is used.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import argparse

import pytest

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
NODE = Path(r"D:\MentorFlow\.local\node\node.exe")
DEPS = Path(r"D:\MentorFlow\.local\web-deps")


def _build(directory: Path) -> None:
    if not NODE.is_file() or not DEPS.is_dir():
        pytest.skip("共享前端构建工具不可用")
    aliases = [
        {"find": "../bridge", "replacement": (HERE / "bridge177.ts").as_posix()},
        {"find": "./bridge", "replacement": (HERE / "bridge177.ts").as_posix()},
        {"find": "../teacher/TeacherStage", "replacement": (REPO / "tests/refactor/conversation37/conversation37_optional.tsx").as_posix()},
        {"find": "../voice", "replacement": (REPO / "tests/refactor/conversation37/conversation37_optional.tsx").as_posix()},
    ]
    (directory / "index.html").write_text(
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (directory / "entry.tsx").write_text(
        f'import {json.dumps((HERE / "browser177.tsx").as_posix())}', encoding="utf-8")
    (directory / "vite.config.mjs").write_text(f"""
import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(DEPS.as_posix())};
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},
plugins:[react(),{{name:'mentorflow-test-deps',enforce:'pre',async resolveId(s,i,o){{
if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;
return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};
""", encoding="utf-8")
    env = dict(os.environ)
    env["MENTORFLOW_WEB_DEPS"] = str(DEPS)
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(directory / "vite.config.mjs")],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _run_browser(directory: Path) -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --remote-allow-origins=*")
    sys.path.insert(0, str(REPO))
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ("http", "https"):
                info.block(True)

    app = QApplication.instance() or QApplication([])
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    interceptor = Offline(page)
    profile.setUrlRequestInterceptor(interceptor)
    view.setPage(page)
    view.resize(1280, 900)
    view.setUrl(QUrl.fromLocalFile(str(directory / "dist/index.html")))

    deadline = time.monotonic() + 90
    result = None
    while time.monotonic() < deadline:
        app.processEvents()
        rows = []
        page.runJavaScript("JSON.stringify(window.result177 || null)", rows.append)
        until = time.monotonic() + 0.15
        while not rows and time.monotonic() < until:
            app.processEvents()
            time.sleep(0.002)
        if rows and rows[0] and rows[0] != "null":
            result = json.loads(rows[0])
            break
        time.sleep(0.01)
    if result is None:
        rows = []
        page.runJavaScript("document.body.innerText", rows.append)
        end = time.monotonic() + 1
        while not rows and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)
        result = {"ok": False, "error": "browser scenario timed out", "body": rows[0] if rows else ""}
    view.close()
    app.processEvents()
    return result


def test_classroom_draft_isolation_and_notification_setting177_real_react(tmp_path):
    """Exercise the real pages and DOM with an offline fake bridge in Qt Chromium."""
    _build(tmp_path)
    env = dict(os.environ)
    for name in ("temp", "localappdata", "xdg-config"):
        (tmp_path / name).mkdir()
    env.update(QT_QPA_PLATFORM="offscreen", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
        TEMP=str(tmp_path / "temp"), TMP=str(tmp_path / "temp"), LOCALAPPDATA=str(tmp_path / "localappdata"),
        XDG_CONFIG_HOME=str(tmp_path / "xdg-config"), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--browser", str(tmp_path)], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads((tmp_path / "browser-result177.json").read_text(encoding="utf-8"))
    result = payload
    assert result.get("ok") is True, json.dumps(result, ensure_ascii=False)
    assert {
        "同名账号隔离、同账号改名恢复",
        "旧 anonymous/display-name 与无 owner 草稿均不迁移",
        "legacy key/account_id 与 namespace 类似字符串碰撞隔离",
        "账号晚到清除未绑定 live 输入、不迁移",
        "durable fallback 不覆盖新输入",
        "提醒开关默认关闭、保存透传、重挂载保持且按账号隔离",
    }.issubset(set(result.get("checks", [])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    if args.browser:
        result = _run_browser(args.browser)
        (args.browser / "browser-result177.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        if not result.get("ok"):
            raise SystemExit(json.dumps(result, ensure_ascii=False))


def test_durable_classroom_draft_fallback177_is_account_scoped(tmp_path):
    """The production handler fallback remains scoped to AccountJSON's account id."""
    from refactor.learning.app.handlers import register

    def handler(account_id):
        methods = {}
        class Signal:
            def __init__(self): self.callbacks = []
            def connect(self, callback): self.callbacks.append(callback)
            def emit(self, value=None):
                for callback in tuple(self.callbacks): callback(value)
        # This direct handler fixture represents an active account session.
        bridge = SimpleNamespace(register=lambda name, fn, **kw: methods.update({name: fn}), push=lambda *a: None,
                                 event=Signal(), closing=Signal(), require_session=lambda session_epoch=None: None)
        services = SimpleNamespace(account_id=account_id, data_root=tmp_path,
            store=SimpleNamespace(get_plan=lambda key: {"id": "plan177"} if key == "plan177" else None,
                                  list_plans=lambda: []))
        register(bridge, SimpleNamespace(services=services))
        return lambda method, **params: methods[method](params, None)

    account_a = handler("account-durable-A")
    account_b = handler("account-durable-B")
    assert account_a("classroom.draft_set", plan_id="plan177", text="A 的 durable 草稿") == {"saved": True}
    assert account_b("classroom.draft_get", plan_id="plan177") == {"text": ""}
    assert account_a("classroom.draft_get", plan_id="plan177") == {"text": "A 的 durable 草稿"}
