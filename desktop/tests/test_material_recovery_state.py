from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from desktop.api import ApiError  # noqa: E402
from desktop.materials_page import (  # noqa: E402
    MaterialsPage,
    material_error_message,
    material_recovery_view,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("raw_state", "state", "actions"),
    [
        ("MISSING_ON_DEVICE", "MISSING_ON_DEVICE", ("relink",)),
        ("MISSING_RESTORE_FROM_PACK", "RESTORABLE_FROM_PACKAGE", ("restore",)),
        (
            "MISSING_REDOWNLOAD_VERIFIED_URL",
            "REDOWNLOAD_AVAILABLE",
            ("redownload", "relink"),
        ),
        ("LINKED_NONPORTABLE", "LINKED_FILE_MISSING", ("relink",)),
    ],
)
def test_material_recovery_projection_is_readable_and_actionable(
    raw_state: str,
    state: str,
    actions: tuple[str, ...],
) -> None:
    view = material_recovery_view({"recovery_state": raw_state})
    assert view["state"] == state
    assert view["actions"] == actions
    assert view["label"]
    assert "Not Found" not in str(view)
    assert '{"detail"' not in str(view)


def test_raw_not_found_error_is_never_exposed() -> None:
    error = ApiError(404, "HTTP_ERROR", '{"detail":"Not Found"}')
    message = material_error_message(error)
    assert "Not Found" not in message
    assert '{"detail"' not in message
    assert "还原" in message
    assert "重新关联" in message


def test_materials_page_shows_restore_action_and_preserves_identity_message(
    qapp: QApplication,
) -> None:
    class FakeApi:
        def __init__(self) -> None:
            self.restored: list[tuple[str, str]] = []

        def restore_material_from_package(self, workspace_id: str, material_id: str):
            self.restored.append((workspace_id, material_id))
            return {
                "asset_id": "asset-1",
                "asset_revision_id": "revision-1",
                "evidence_revision": "evidence-revision-1",
            }

    api = FakeApi()
    page = MaterialsPage(api)  # type: ignore[arg-type]
    page.workspace.addItem("Workspace", "workspace-1")
    page._set_materials(
        [
            {
                "id": "material-1",
                "asset_id": "asset-1",
                "asset_revision_id": "revision-1",
                "evidence_revision": "evidence-revision-1",
                "original_filename": "lesson.pdf",
                "availability": "RESTORABLE_FROM_PACKAGE",
                "status": "missing",
            }
        ]
    )
    try:
        buttons = page.table.cellWidget(0, 6).findChildren(QPushButton)
        assert [button.text() for button in buttons] == ["从恢复包还原"]
        page.refresh_materials = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        page._recover_material("material-1", "restore")
        deadline = time.monotonic() + 2
        while page._operation_future is not None and not page._operation_future.done():
            qapp.processEvents()
            time.sleep(0.01)
            assert time.monotonic() < deadline
        page._poll_operation()
        assert api.restored == [("workspace-1", "material-1")]
        assert "Evidence revision 保持不变" in page.status.text()
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()
