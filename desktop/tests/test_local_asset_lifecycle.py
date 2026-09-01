from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from desktop.account_identity import AccountIdentity, AccountProfile
from desktop.account_portability import PackageMode, export_account_package
from desktop.account_vault import AccountVaultManager, AssetAvailability, VaultLayout
from desktop.local_asset_lifecycle import LocalAssetLifecycle, LocalAssetLifecycleError


ACCOUNT = {
    "issuer": "https://identity.example.test/realms/mentorflow",
    "subject": "enterprise-lifecycle-user",
}
_OPEN_MANAGERS: list[AccountVaultManager] = []


def _profile(subject: str = "enterprise-lifecycle-user"):
    return AccountIdentity(
        issuer=ACCOUNT["issuer"],
        subject=subject,
        display_name=subject,
    ).profile()


@pytest.fixture
def scratch_root() -> Path:
    base = Path(r"D:\CodexScratch\r")
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="", dir=base))
    try:
        yield root
    finally:
        for manager in reversed(_OPEN_MANAGERS):
            manager.unmount()
        _OPEN_MANAGERS.clear()
        package_temp = root / "package-temp"
        if package_temp.exists():
            shutil.rmtree(package_temp, ignore_errors=True)
        cleanup_target = str(root.resolve())
        if os.name == "nt":
            cleanup_target = "\\\\?\\" + cleanup_target
        shutil.rmtree(cleanup_target, ignore_errors=True)


def _service(root: Path, *, downloader=None):
    manager = AccountVaultManager(VaultLayout(root / "v"))
    _OPEN_MANAGERS.append(manager)
    vault = manager.mount(_profile())
    return manager, vault, LocalAssetLifecycle(
        vault,
        account_identity=ACCOUNT,
        downloader=downloader,
    )


def test_managed_import_survives_source_delete_and_dto_has_no_device_path_or_bytes(
    scratch_root: Path,
) -> None:
    _manager, vault, lifecycle = _service(scratch_root)
    source = scratch_root / "downloads" / "lesson.txt"
    source.parent.mkdir()
    source.write_bytes(b"managed revision")

    view = lifecycle.import_managed(source)
    source.unlink()

    assert view.source_kind == "MANAGED"
    assert lifecycle.resolve(view.local_asset_id).availability is AssetAvailability.AVAILABLE_LOCAL
    assert vault.managed_object_path(vault.list_assets()[0]).read_bytes() == b"managed revision"
    dto = lifecycle.server_manifest_dto(view.local_asset_id)
    assert dto == {
        "local_asset_id": view.local_asset_id,
        "filename": "lesson.txt",
        "mime_type": "text/plain",
        "size_bytes": len(b"managed revision"),
        "sha256": hashlib.sha256(b"managed revision").hexdigest(),
        "import_mode": "MANAGED",
        "portability": "PORTABLE",
        "availability": "AVAILABLE",
    }
    assert not any(isinstance(value, (bytes, bytearray, memoryview)) for value in dto.values())
    assert not any(
        isinstance(value, str) and len(value) > 2 and value[1:3] in {":\\", ":/"}
        for value in dto.values()
    )


def test_linked_import_does_not_copy_and_relink_changes_only_device_overlay(
    scratch_root: Path,
) -> None:
    _manager, vault, lifecycle = _service(scratch_root)
    original = scratch_root / "external" / "linked.pdf"
    original.parent.mkdir()
    original.write_bytes(b"linked revision")

    linked = lifecycle.import_linked(original)
    base_record = vault.list_assets()[0]
    assert linked.labels == ("LINKED", "NOT_PORTABLE")
    assert base_record.object_relative_path is None
    assert not any((vault.root / "managed-assets" / "objects").rglob("*"))

    original.unlink()
    missing = lifecycle.resolve(linked.local_asset_id)
    assert missing.availability is AssetAvailability.LINKED_FILE_MISSING
    assert missing.actions == ("relink",)

    replacement = scratch_root / "replacement" / "same.pdf"
    replacement.parent.mkdir()
    replacement.write_bytes(b"linked revision")
    lifecycle.relink(linked.local_asset_id, replacement)

    assert (
        lifecycle.resolve(linked.local_asset_id).availability
        is AssetAvailability.AVAILABLE_LOCAL
    )
    assert vault.list_assets()[0].linked_path == base_record.linked_path
    dto = lifecycle.server_manifest_dto(linked.local_asset_id)
    assert dto["portability"] == "NONPORTABLE"
    assert dto["device_id"].startswith("device_")
    assert str(original) not in str(dto)
    assert str(replacement) not in str(dto)


def test_all_five_states_and_fail_closed_redownload(
    scratch_root: Path,
) -> None:
    payloads = {
        "available.txt": b"available",
        "missing.txt": b"missing",
        "redownload.txt": b"redownload",
        "package.txt": b"package",
    }

    def downloader(url: str, destination: Path) -> None:
        assert url == "https://downloads.example.test/redownload.txt"
        destination.write_bytes(payloads["redownload.txt"])

    _manager, vault, lifecycle = _service(scratch_root, downloader=downloader)
    views = {}
    for filename, content in payloads.items():
        source = scratch_root / filename
        source.write_bytes(content)
        views[filename] = lifecycle.import_managed(source)
    linked_source = scratch_root / "linked.txt"
    linked_source.write_bytes(b"linked")
    linked = lifecycle.import_linked(linked_source)

    records = {record.local_asset_id: record for record in vault.list_assets()}
    vault.managed_object_path(records[views["missing.txt"].local_asset_id]).unlink()
    vault.managed_object_path(records[views["redownload.txt"].local_asset_id]).unlink()
    vault.managed_object_path(records[views["package.txt"].local_asset_id]).unlink()
    linked_source.unlink()
    lifecycle.register_recovery_options(
        views["redownload.txt"].local_asset_id,
        verified_source_url="https://downloads.example.test/redownload.txt",
    )
    lifecycle.register_recovery_options(
        views["package.txt"].local_asset_id,
        restorable_from_package=True,
    )

    observed = {item.filename: item.availability.value for item in lifecycle.list_assets()}
    assert observed == {
        "available.txt": "AVAILABLE_LOCAL",
        "missing.txt": "MISSING_ON_DEVICE",
        "redownload.txt": "REDOWNLOAD_AVAILABLE",
        "package.txt": "RESTORABLE_FROM_PACKAGE",
        "linked.txt": "LINKED_FILE_MISSING",
    }
    restored = lifecycle.redownload(views["redownload.txt"].local_asset_id)
    assert restored.availability is AssetAvailability.AVAILABLE_LOCAL

    with pytest.raises(LocalAssetLifecycleError, match="SOURCE_NOT_VERIFIED"):
        lifecycle.redownload(views["missing.txt"].local_asset_id)


def test_restore_from_verified_current_account_package_preserves_sha(
    scratch_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(scratch_root / "package-temp"))
    _manager, vault, lifecycle = _service(scratch_root)
    source = scratch_root / "restore-me.pdf"
    source.write_bytes(b"package revision bytes")
    imported = lifecycle.import_managed(source)
    record = vault.list_assets()[0]
    package = scratch_root / "current-account.mentorflowpack"
    # This focused package contains only the raw asset under test. Avoid
    # copying unrelated empty runtime databases into disposable staging.
    for database_name in ("app.db", "mentorflow_v2.db"):
        (vault.root / "data" / database_name).unlink(missing_ok=True)
    export_account_package(
        vault.root,
        package,
        account_identity=ACCOUNT,
        mode=PackageMode.FULL,
        asset_manifests=[
            {
                "asset_id": "asset-restore-1",
                "asset_revision_id": "revision-restore-1",
                "local_asset_id": record.local_asset_id,
                "storage_kind": "MANAGED",
                "filename": record.display_name,
                "mime_type": record.mime_type,
                "size_bytes": record.size_bytes,
                "sha256": record.sha256,
                "vault_relative_path": record.object_relative_path,
            }
        ],
    )
    target = vault.managed_object_path(record)
    target.unlink()
    lifecycle.register_recovery_options(imported.local_asset_id, restorable_from_package=True)

    restored = lifecycle.restore_from_package(imported.local_asset_id, package)

    assert restored.availability is AssetAvailability.AVAILABLE_LOCAL
    assert restored.sha256 == imported.sha256
    assert target.read_bytes() == b"package revision bytes"


def test_account_b_cannot_enumerate_account_a_local_metadata(scratch_root: Path) -> None:
    manager, vault_a, lifecycle_a = _service(scratch_root)
    private = scratch_root / "account-a-private.txt"
    private.write_bytes(b"account A")
    lifecycle_a.import_managed(private)
    root_a = vault_a.root

    account_b = {
        "issuer": ACCOUNT["issuer"],
        "subject": "enterprise-lifecycle-user-b",
    }
    vault_b = manager.mount(_profile("enterprise-lifecycle-user-b"))
    lifecycle_b = LocalAssetLifecycle(vault_b, account_identity=account_b)

    assert lifecycle_b.list_assets() == []
    assert vault_b.root != root_a
    assert not lifecycle_b._overlay_path.exists()
    assert manager.layout.shared_models_root not in (vault_a.root, vault_b.root)


def test_personal_local_mount_is_unchanged_until_an_explicit_asset_action(
    scratch_root: Path,
) -> None:
    manager = AccountVaultManager(VaultLayout(scratch_root / "p"))
    _OPEN_MANAGERS.append(manager)
    vault = manager.mount(AccountProfile.personal_local())
    before = {path.relative_to(vault.root).as_posix() for path in vault.root.rglob("*")}

    lifecycle = LocalAssetLifecycle(
        vault,
        account_identity={"id": "personal-local-user"},
    )

    after = {path.relative_to(vault.root).as_posix() for path in vault.root.rglob("*")}
    assert before == after
    assert lifecycle.list_assets() == []
    assert not (vault.root / "metadata" / "local-asset-lifecycle.v1.json").exists()


def test_desktop_page_renders_linked_missing_without_raw_exception(
    scratch_root: Path,
) -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton
    from desktop.local_asset_page import LocalAssetPage, safe_lifecycle_message

    app = QApplication.instance() or QApplication([])
    _manager, _vault, lifecycle = _service(scratch_root)
    linked_source = scratch_root / "page-linked.txt"
    linked_source.write_bytes(b"page linked")
    linked = lifecycle.import_linked(linked_source)
    linked_source.unlink()
    page = LocalAssetPage(lifecycle)
    try:
        assert page.table.item(0, 1).text() == "LINKED · NOT_PORTABLE"
        assert "LINKED_FILE_MISSING" in page.table.item(0, 2).text()
        buttons = page.table.cellWidget(0, 4).findChildren(QPushButton)
        assert [button.text() for button in buttons] == ["重新关联"]
        message = safe_lifecycle_message(FileNotFoundError('{"detail":"Not Found"}'))
        assert "FileNotFoundError" not in message
        assert "Not Found" not in message
        assert '{"detail"' not in message
        assert lifecycle.resolve(linked.local_asset_id).actions == ("relink",)
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
