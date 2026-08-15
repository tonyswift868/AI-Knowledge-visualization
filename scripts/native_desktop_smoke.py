"""Exercise registration, the native main window and authenticated compatibility view."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["MENTORFLOW_AUTH_REQUIRED"] = "1"
os.environ["MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE"] = "1"

from desktop.api import DesktopApi
from desktop.main_window import MainWindow
from desktop.runtime import LocalService
from desktop.style import STYLE


def main() -> int:
    output = Path(os.environ.get("MENTORFLOW_DESKTOP_SMOKE_SCREENSHOT", str(ROOT / "test-results" / "native-desktop-smoke.png")))
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
            payload = api.register("Smoke_01", "Smoke User", "Correct-Horse-42")
            sentinel = output.parent / "native-material-sentinel.txt"
            sentinel.write_text("NATIVE MATERIAL SENTINEL 814", encoding="utf-8")
            api.upload_material("native-smoke-material", sentinel)
            capabilities = api.material_capabilities()
            if not (capabilities.get("registry") or {}).get("capabilities"):
                fail("material capability registry was empty")
                return
            window = MainWindow(api, payload["user"], origin)
            window.navigation.setCurrentRow(5)
            window.show()

            def loaded(ok: bool) -> None:
                if not ok:
                    fail("compatibility view failed to load")
                    return

                def checked(status: object) -> None:
                    if int(status or 0) != 200:
                        fail(f"compatibility auth check returned {status}")
                        return
                    window.materials_page.refresh_workspaces("native-smoke-material")
                    if window.materials_page.table.rowCount() != 1:
                        fail("native materials page did not render uploaded material")
                        return
                    # Exercise the native canvas independently from the empty
                    # isolated smoke database. These sentinel labels are test-only.
                    window.pages.setCurrentIndex(2)
                    window.map_view.set_graph(
                        [
                            {"id": "smoke-a", "name": "Sentinel Alpha", "layer": "material", "status": "confirmed"},
                            {"id": "smoke-b", "name": "Sentinel Beta", "layer": "material", "status": "confirmed"},
                        ],
                        [{"source_id": "smoke-a", "target_id": "smoke-b", "relation": "prerequisite"}],
                    )
                    window.grab().save(str(output))
                    state["exit"] = 0
                    application.quit()

                window.web.page().runJavaScript(
                    "(() => { const request = new XMLHttpRequest(); "
                    "request.open('GET', '/api/auth/me', false); request.send(); "
                    "return request.status; })()",
                    0,
                    checked,
                )

            window.web.loadFinished.connect(loaded)
        except Exception as exc:
            fail(f"native smoke startup failed: {type(exc).__name__}: {exc}")

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
