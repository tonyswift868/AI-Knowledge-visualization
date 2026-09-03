from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from desktop.api import ApiError  # noqa: E402
from desktop.local_asset_page import AccountResourceRow, LocalAssetPage  # noqa: E402
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


class _RelinkRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("RELINK_HASH_MISMATCH")
        self.user_message = "所选文件的 SHA-256 不匹配；本机绑定未更改。"


class _AccountResourceService:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.calls: list[tuple[str, Path]] = []
        self.resource = AccountResourceRow(
            resource_id="resource-1",
            filename="lesson.pdf",
            size_bytes=128,
            source_kind="MANAGED",
            state="MISSING_ON_THIS_DEVICE",
            actions=("relink",),
        )

    def list_resources(self) -> list[AccountResourceRow]:
        return [self.resource]

    def relink_resource(self, resource_id: str, source: Path) -> AccountResourceRow:
        self.calls.append((resource_id, source))
        if self.reject:
            raise _RelinkRejected()
        self.resource = replace(
            self.resource,
            state="AVAILABLE_LOCAL",
            actions=(),
        )
        return self.resource


@pytest.mark.parametrize("reject", [False, True])
def test_account_resource_relink_ui_delegates_hash_verification_and_refreshes(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reject: bool,
) -> None:
    service = _AccountResourceService(reject=reject)
    selected = tmp_path / ("wrong.pdf" if reject else "matching.pdf")
    selected.write_bytes(b"selected by user")
    monkeypatch.setattr(
        "desktop.local_asset_page.QFileDialog.getOpenFileName",
        lambda *_args, **_kwargs: (str(selected), ""),
    )
    page = LocalAssetPage(
        object(),  # type: ignore[arg-type]
        list_resources=service.list_resources,
        relink_resource=service.relink_resource,
    )
    qtbot.addWidget(page)
    page.show()

    assert "MISSING_ON_THIS_DEVICE" in page.table.item(0, 2).text()
    assert "重新关联" in page.status.text()
    button = page.findChild(QPushButton, "localAssetAction_relink")
    assert button is not None
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    assert service.calls == [("resource-1", selected)]
    if reject:
        assert service.resource.state == "MISSING_ON_THIS_DEVICE"
        assert "SHA-256 不匹配" in page.status.text()
        assert "未更改" in page.status.text()
        assert "MISSING_ON_THIS_DEVICE" in page.table.item(0, 2).text()
    else:
        assert service.resource.resource_id == "resource-1"
        assert "RELINKED" in page.status.text()
        assert "同一资源" in page.status.text()
        assert "AVAILABLE_LOCAL" in page.table.item(0, 2).text()


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
    page.workspace.blockSignals(True)
    page.workspace.addItem("Workspace", "workspace-1")
    page.workspace.blockSignals(False)
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
