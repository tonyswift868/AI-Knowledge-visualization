"""Smoke-test the local single-user service and native desktop shell."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Never let a smoke run touch a developer's databases or upload tree. The
# runtime reads these settings when LocalService imports the backend.
_SMOKE_ROOT = Path(tempfile.mkdtemp(prefix="mentorflow-native-smoke-"))
os.environ.setdefault("MENTORFLOW_DB", str(_SMOKE_ROOT / "app.db"))
os.environ.setdefault("MENTORFLOW_ORM_DB", str(_SMOKE_ROOT / "mentorflow_v2.db"))
os.environ.setdefault("MENTORFLOW_ACTIVE_UPLOAD_ROOT", str(_SMOKE_ROOT / "active-uploads"))
os.environ.setdefault("MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT", str(_SMOKE_ROOT / "classroom-uploads"))
os.environ["MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE"] = "1"

from desktop.api import DesktopApi
from desktop.main_window import MainWindow
from desktop.runtime import LocalService
from desktop.style import STYLE


def main() -> int:
    output = Path(
        os.environ.get(
            "MENTORFLOW_DESKTOP_SMOKE_SCREENSHOT",
            str(ROOT / "test-results" / "native-desktop-smoke.png"),
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    application = QApplication(sys.argv)
    application.setStyleSheet(STYLE)
    service = LocalService()
    state = {"exit": 1}
    window: MainWindow | None = None
    api: DesktopApi | None = None

    def fail(message: str) -> None:
        print(message, file=sys.stderr)
        application.quit()

    def started(origin: str) -> None:
        nonlocal window, api
        try:
            api = DesktopApi(origin)
            health = api.request("GET", "/api/health")
            if health.get("status") != "ok":
                fail(f"local API health check failed: {health!r}")
                return

            created = api.request(
                "POST",
                "/api/workspaces",
                json={"title": "Native single-user smoke", "description": "isolated smoke workspace"},
            )
            workspace_id = str(created.get("id") or "")
            if not workspace_id:
                fail(f"workspace creation returned no id: {created!r}")
                return
            workspaces = api.workspaces()
            if not any(str(item.get("id") or "") == workspace_id for item in workspaces):
                fail("created workspace was not visible in the local workspace list")
                return

            window = MainWindow(
                api,
                {"id": "local-data", "username": "local-data", "display_name": "本地学习空间"},
                origin,
            )
            window.navigation.setCurrentRow(1)
            window.show()

            def capture() -> None:
                if window is None:
                    fail("native main window was not created")
                    return
                window.grab().save(str(output))
                state["exit"] = 0
                application.quit()

            QTimer.singleShot(250, capture)
        except Exception as exc:
            fail(f"native single-user smoke failed: {type(exc).__name__}: {exc}")

    service.ready.connect(started)
    service.failed.connect(fail)
    application.aboutToQuit.connect(service.stop)
    QTimer.singleShot(120_000, lambda: fail("native smoke timed out"))
    service.start()
    application.exec()
    if api:
        api.close()
    print(output)
    return int(state["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
