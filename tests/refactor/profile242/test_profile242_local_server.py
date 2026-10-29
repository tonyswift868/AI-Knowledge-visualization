"""Desktop account profile flow through local HTTPS and the real Qt login list."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import uvicorn
from PIL import Image
from sqlalchemy import create_engine, text
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog

from refactor.desktop.account_profile import AccountProfileClient, cached_profile
from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.login_shell import LoginGateWindow
from refactor.learning.app import account_profile as account_profile_bridge
from refactor.learning.app.bridge import AppBridge, BridgeError, CallContext
from tests.refactor.server.contract_support import ISSUER, runtime


def _tls_helper():
    helper_dir = Path(__file__).resolve().parents[1] / "server_java"
    if str(helper_dir) not in sys.path:
        sys.path.insert(0, str(helper_dir))
    from contract_support144 import make_tls144
    return make_tls144


def _png(path: Path) -> bytes:
    image = Image.new("RGB", (64, 64), (40, 110, 180))
    image.save(path, format="PNG")
    return path.read_bytes()


def _context():
    return CallContext("profile242", threading.Event(), lambda _chunk: None)


def _capture_combo(gate: LoginGateWindow, output_dir: Path, label: str):
    gate.show()
    gate.account_selector.showPopup()
    QApplication.processEvents()
    popup = gate.account_selector.view().grab()
    assert not popup.isNull()
    assert popup.save(str(output_dir / f"login-list-{label}.png"), "PNG")
    gate.account_selector.hidePopup()
    assert gate.grab().save(str(output_dir / f"login-window-{label}.png"), "PNG")


def test_profile242_https_client_bridge_and_qt_login_list(tmp_path, monkeypatch):
    app_qt = QApplication.instance() or QApplication([])
    artifacts = Path("D:/MentorFlow/.local/test-tmp/ui242")
    artifacts.mkdir(parents=True, exist_ok=True)
    screenshots = artifacts / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "local-profile-e2e"
    root.mkdir(parents=True, exist_ok=True)
    cache_root = root / "cache"
    cache_root.mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{(root / 'profile.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE IF NOT EXISTS account_profiles (
            account_scope TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT '',
            signature TEXT NOT NULL DEFAULT '', avatar_bytes BLOB, avatar_etag TEXT,
            avatar_width INTEGER, avatar_height INTEGER, version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""))

    from refactor.server import app as account_core
    from refactor.server.enterprise.account_profiles import AccountProfileService

    monkeypatch.setattr(account_core.app.state, "enterprise_identity_runtime", runtime("refactor.server"), raising=False)
    monkeypatch.setattr(account_core.app.state, "account_profile_service", AccountProfileService(engine), raising=False)
    tls = _tls_helper()(root / "tls")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        account_core.app, host="127.0.0.1", port=port,
        ssl_certfile=str(tls["server_cert"]), ssl_keyfile=str(tls["server_key"]),
        log_level="error", lifespan="off", access_log=False,
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started

        account_id = "profile242-local-account"
        session = SimpleNamespace(identity=SimpleNamespace(account_id=account_id), access_token="token-a")
        client = AccountProfileClient(
            account_id=account_id, data_root=cache_root,
            session_provider=lambda: session,
            api_base_url=f"https://127.0.0.1:{port}", ca_bundle=tls["ca"],
        )
        assert client.refresh()["version"] == 1
        services = SimpleNamespace(account_profile=client, _closed=False, _avatar_preview=None)
        window = SimpleNamespace(services=services, _session_offline=False, _personal_profile={})
        window.account_info = lambda: {**client.snapshot(), "name": "login-name"}
        bridge = AppBridge()
        account_events = []
        bridge.event.connect(lambda raw: account_events.append(json.loads(raw)))
        account_profile_bridge.register(bridge, window)
        handlers = {key: value.function for key, value in bridge._handlers.items()}

        image_path = root / "avatar-source.png"
        _png(image_path)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *_args: (str(image_path), "PNG")))
        choice = handlers["account.profile_choose_avatar"]({}, _context())
        assert not choice["cancelled"] and choice["width"] == choice["height"] == 64
        saved = handlers["account.profile_save_avatar"]({"crop": {"x": 0, "y": 0, "size": 64}, "version": 1}, _context())
        assert saved["version"] == 2 and saved["avatar_url"].startswith("data:image/jpeg;base64,")
        text_saved = handlers["account.profile_save"]({"nickname": "显示昵称", "signature": "本机测试签名", "version": 2}, _context())
        assert text_saved["version"] == 3 and text_saved["nickname"] == "显示昵称"
        QApplication.processEvents()
        assert any(event.get("name") == "account" and event["data"]["nickname"] == "显示昵称"
                   for event in account_events)

        class Registry:
            def snapshot(self):
                return {"accounts": [{"account_id": account_id, "username": "login-name"}],
                        "current_account_id": account_id}

        gate = LoginGateWindow()
        shell = AccountShell.__new__(AccountShell)
        shell.gate = gate
        shell.session = SimpleNamespace(registry=Registry())
        shell.runtime = SimpleNamespace(data_root=cache_root)
        shell._refresh_local_accounts()
        assert gate.account_selector.count() == 1
        assert gate.account_selector.itemText(0) == "显示昵称 · login-name"
        icon = gate.account_selector.itemIcon(0).pixmap(32, 32).toImage()
        assert icon.pixelColor(0, 0).alpha() == 0
        assert icon.pixelColor(16, 16).alpha() > 0
        gate.set_loading_appearance("sakura")
        _capture_combo(gate, screenshots, "light-avatar")
        gate.set_loading_appearance("aurora")
        _capture_combo(gate, screenshots, "dark-avatar")

        deleted = handlers["account.profile_delete_avatar"]({"version": 3}, _context())
        assert deleted["version"] == 4 and deleted["avatar_url"] == ""
        shell._refresh_local_accounts()
        fallback_icon = gate.account_selector.itemIcon(0).pixmap(32, 32).toImage()
        assert fallback_icon.pixelColor(0, 0).alpha() == 0
        assert fallback_icon.pixelColor(16, 16).alpha() > 0
        gate.set_loading_appearance("sakura")
        _capture_combo(gate, screenshots, "light-no-avatar")
        gate.set_loading_appearance("aurora")
        _capture_combo(gate, screenshots, "dark-no-avatar")

        window._session_offline = True
        with pytest.raises(BridgeError, match="联网"):
            handlers["account.profile_save"]({"nickname": "离线修改", "signature": "", "version": 4}, _context())
        window._session_offline = False
        session.access_token = ""
        assert client.refresh()["offline"] is True
        assert cached_profile(account_id, cache_root)["nickname"] == "显示昵称"
        assert client.snapshot()["avatar_url"] == ""
        gate.close()
        bridge.close()
        client.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        engine.dispose()
