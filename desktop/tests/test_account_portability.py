from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

import desktop.account_portability as account_portability_module
from desktop.account_portability import (
    ASSET_MANIFESTS_MEMBER,
    CHECKSUMS_MEMBER,
    MANIFEST_MEMBER,
    PACKAGE_FORMAT_VERSION,
    PACKAGE_SCHEMA,
    RECOVERY_INDEX_MEMBER,
    AccountPortabilityError,
    PackageMode,
    export_account_package,
    import_account_package,
)


ACCOUNT = {
    "issuer": "https://identity.example.test/realms/mentorflow",
    "subject": "enterprise-learner-1",
    "organization_id": "org-1",
}
OTHER_ACCOUNT = {
    "issuer": "https://identity.example.test/realms/mentorflow",
    "subject": "enterprise-learner-2",
}
MANAGED_BYTES = b"MentorFlow portable managed material\n"
DERIVED_BYTES = b'{"chunks":[{"id":"chunk-1","text":"portable"}]}\n'


@contextmanager
def _new_device_root():
    base = Path(r"D:\CodexScratch")
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="E1-R36-05-new-device-", dir=base))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _fixture(vault: Path) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    managed = vault / "managed" / "local-managed-1" / "lesson.pdf"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(MANAGED_BYTES)
    derived = vault / "derived" / "local-managed-1" / "chunks.json"
    derived.parent.mkdir(parents=True)
    derived.write_bytes(DERIVED_BYTES)
    database = vault / "data" / "app.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE provider_sessions (id TEXT PRIMARY KEY, access_token TEXT)"
        )
        connection.execute(
            "INSERT INTO provider_sessions VALUES ('ps_db_must_not_export_12345678', "
            "'access-db-must-not-export-12345678')"
        )
        connection.execute(
            "CREATE TABLE plans (id TEXT PRIMARY KEY, title TEXT NOT NULL, archived INTEGER NOT NULL, "
            "local_path TEXT, metadata TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, ?)",
            (
                "db-plan-1",
                "Portable database plan",
                1,
                r"C:\Users\old-device\MentorFlowData\plan.json",
                json.dumps(
                    {
                        "legacy_root": r"E:\MentorFlowData",
                        "verified_url": "https://downloads.example.test/plan.json",
                    }
                ),
            ),
        )
        connection.commit()
    plans = [
        {
            "plan_id": f"plan-{index + 1}",
            "workspace_id": f"workspace-{index + 1}",
            "archived": index < 55,
            "progress": {"completed": index % 7, "total": 7},
        }
        for index in range(56)
    ]
    snapshot: dict[str, object] = {
        "workspaces": [
            {"workspace_id": item["workspace_id"], "archived": item["archived"]}
            for item in plans
        ],
        "goals": [{"goal_id": "goal-1", "title": "Portable recovery"}],
        "plans": plans,
        "progress": [{"plan_id": item["plan_id"], **item["progress"]} for item in plans],
        "knowledge_inventory": [{"stable_item_id": "knowledge-1"}],
        "knowledge_map": [{"snapshot_id": "map-1"}],
        "evidence": [{"evidence_id": "evidence-1", "revision": "ev-rev-1"}],
        "legacy_device_path": r"C:\Users\old-device\MentorFlowData",
        "provider_session_id": "ps_must_not_export_12345678",
    }
    assets: list[dict[str, object]] = [
        {
            "asset_id": "asset-managed-1",
            "asset_revision_id": "asset-revision-managed-1",
            "evidence_revision": "evidence-revision-managed-1",
            "local_asset_id": "local-managed-1",
            "storage_kind": "MANAGED",
            "filename": "lesson.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(MANAGED_BYTES),
            "sha256": hashlib.sha256(MANAGED_BYTES).hexdigest(),
            "vault_relative_path": "managed/local-managed-1/lesson.pdf",
            "required_derived_artifacts": [
                {
                    "artifact_id": "chunks",
                    "filename": "chunks.json",
                    "mime_type": "application/json",
                    "size_bytes": len(DERIVED_BYTES),
                    "sha256": hashlib.sha256(DERIVED_BYTES).hexdigest(),
                    "vault_relative_path": "derived/local-managed-1/chunks.json",
                }
            ],
            "absolute_path": str(managed),
        },
        {
            "asset_id": "asset-linked-1",
            "asset_revision_id": "asset-revision-linked-1",
            "evidence_revision": "evidence-revision-linked-1",
            "local_asset_id": "local-linked-1",
            "storage_kind": "LINKED",
            "filename": "linked-notes.md",
            "mime_type": "text/markdown",
            "size_bytes": 100,
            "sha256": "1" * 64,
            "absolute_path": r"C:\Users\old-device\Documents\linked-notes.md",
            "verified_source_url": "https://downloads.example.test/linked-notes.md",
        },
        {
            "asset_id": "asset-redownload-1",
            "asset_revision_id": "asset-revision-redownload-1",
            "evidence_revision": "evidence-revision-redownload-1",
            "local_asset_id": "local-redownload-1",
            "storage_kind": "MANAGED",
            "filename": "remote-lesson.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 200,
            "sha256": "3" * 64,
            "verified_source_url": "https://downloads.example.test/remote-lesson.pdf",
        },
    ]
    models: list[dict[str, object]] = [
        {
            "model_id": "model-manifest-1",
            "version": "2026.09",
            "sha256": "2" * 64,
            "selected": True,
            "binary_path": r"E:\MentorFlowData\shared-models\model.gguf",
            "provider_session_id": "ps_model_session_must_not_export",
        }
    ]
    return snapshot, assets, models


def _export(
    vault: Path,
    destination: Path,
    *,
    mode: PackageMode,
) -> dict[str, object]:
    snapshot, assets, models = _fixture(vault)
    return export_account_package(
        vault,
        destination,
        account_identity=ACCOUNT,
        mode=mode,
        structured_snapshot=snapshot,
        preferences={
            "language": "zh-CN",
            "task-colors/plan-1": "#c95f8d",
            "access_token": "access-token-must-not-export",
            "credential_ref": "mfcred_must_not_export",
        },
        asset_manifests=assets,
        model_manifests=models,
    )


def _package_projection(path: Path) -> tuple[set[str], str, dict, dict, list, list]:
    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        combined = "\n".join(
            bundle.read(name).decode("utf-8", errors="ignore") for name in names
        )
        manifest = json.loads(bundle.read(MANIFEST_MEMBER))
        structured = json.loads(bundle.read("structured/account.json"))
        assets = json.loads(bundle.read(ASSET_MANIFESTS_MEMBER))
        models = json.loads(bundle.read("manifests/models.json"))
    return names, combined, manifest, structured, assets, models


def test_quick_export_import_preserves_56_plans_and_excludes_raw_secrets_paths_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    vault = tmp_path / "accounts" / "current"
    package = tmp_path / "quick.mentorflowpack"
    result = _export(vault, package, mode=PackageMode.QUICK)
    names, combined, manifest, structured, assets, models = _package_projection(package)

    assert result["raw_assets"] == 0
    assert not any(name.startswith("raw-assets/") for name in names)
    assert not any(name.startswith("derived-assets/") for name in names)
    assert {MANIFEST_MEMBER, CHECKSUMS_MEMBER, RECOVERY_INDEX_MEMBER} <= names
    assert manifest["schema"] == PACKAGE_SCHEMA
    assert manifest["package_format_version"] == PACKAGE_FORMAT_VERSION
    assert manifest["object_inventory"]["plans"] == 56
    assert manifest["object_inventory"]["archived_plans"] == 55
    assert len(structured["plans"]) == 56
    assert sum(bool(plan["archived"]) for plan in structured["plans"]) == 55
    assert models == [
        {
            "binary_exported": False,
            "device_state": "REDOWNLOAD_REQUIRED",
            "model_id": "model-manifest-1",
            "selected": True,
            "sha256": "2" * 64,
            "version": "2026.09",
        }
    ]
    assert assets[0]["availability"] == "MISSING_ON_DEVICE"
    assert assets[0]["derived_artifacts"] == []
    assert assets[1]["availability"] == "LINKED_FILE_MISSING"
    assert assets[2]["availability"] == "REDOWNLOAD_AVAILABLE"
    lowered = combined.casefold()
    for forbidden in (
        "access-token-must-not-export",
        "mfcred_must_not_export",
        "ps_must_not_export",
        "ps_model_session",
        "ps_db_must_not_export",
        "access-db-must-not-export",
        "c:\\users",
        "e:\\mentorflowdata",
        "model.gguf",
    ):
        assert forbidden.casefold() not in lowered

    with _new_device_root() as device:
        target = device / "accounts" / "current"
        imported = import_account_package(
            package,
            target,
            expected_account_identity=ACCOUNT,
            test_only=True,
        )
        assert imported["duplicate"] is False
        assert imported["raw_assets_restored"] == 0
        restored = json.loads(
            (target / "data/portability/structured-account.json").read_text(encoding="utf-8")
        )
        assert len(restored["plans"]) == 56
        assert sum(bool(plan["archived"]) for plan in restored["plans"]) == 55
        with sqlite3.connect(target / "data/app.db") as connection:
            assert connection.execute("SELECT COUNT(*) FROM provider_sessions").fetchone()[0] == 0
            row = connection.execute(
                "SELECT title, archived, local_path, metadata FROM plans"
            ).fetchone()
            assert row[:3] == ("Portable database plan", 1, None)
            assert json.loads(row[3]) == {
                "legacy_root": None,
                "verified_url": "https://downloads.example.test/plan.json",
            }
        local_map = json.loads(
            (target / "data/portability/local-asset-map.json").read_text(encoding="utf-8")
        )
        assert {row["state"] for row in local_map} == {
            "MISSING_ON_DEVICE",
            "LINKED_FILE_MISSING",
            "REDOWNLOAD_AVAILABLE",
        }
        assert all(row["local_relative_path"] is None for row in local_map)
        repeated = import_account_package(
            package,
            target,
            expected_account_identity=ACCOUNT,
            test_only=True,
        )
        assert repeated["duplicate"] is True
        assert repeated["object_inventory"]["plans"] == 56


def test_full_export_import_restores_only_current_managed_asset_and_rebuilds_local_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    vault = tmp_path / "accounts" / "current"
    other_vault = tmp_path / "accounts" / "other"
    other_vault.mkdir(parents=True)
    (other_vault / "other-account-secret.txt").write_text("OTHER_ACCOUNT_DATA", encoding="utf-8")
    package = tmp_path / "full.mentorflowpack"
    result = _export(vault, package, mode=PackageMode.FULL)
    names, combined, manifest, _structured, assets, _models = _package_projection(package)

    raw_names = {name for name in names if name.startswith("raw-assets/")}
    derived_names = {name for name in names if name.startswith("derived-assets/")}
    assert raw_names == {"raw-assets/local-managed-1/lesson.pdf"}
    assert derived_names == {"derived-assets/local-managed-1/chunks/chunks.json"}
    assert result["raw_assets"] == 1
    assert result["derived_artifacts"] == 1
    assert "OTHER_ACCOUNT_DATA" not in combined
    assert not any("linked-notes" in name for name in raw_names)
    assert assets[0]["availability"] == "RESTORABLE_FROM_PACKAGE"
    assert assets[2]["availability"] == "REDOWNLOAD_AVAILABLE"
    assert manifest["package_mode"] == "FULL_PORTABLE_EXPORT"

    with _new_device_root() as device:
        target = device / "accounts" / "current"
        imported = import_account_package(
            package,
            target,
            expected_account_identity=ACCOUNT,
            test_only=True,
        )
        assert imported["raw_assets_restored"] == 1
        assert imported["derived_artifacts_restored"] == 1
        local_map = json.loads(
            (target / "data/portability/local-asset-map.json").read_text(encoding="utf-8")
        )
        managed = next(row for row in local_map if row["asset_id"] == "asset-managed-1")
        linked = next(row for row in local_map if row["asset_id"] == "asset-linked-1")
        assert managed["local_asset_id"].startswith("restored_")
        assert managed["local_asset_id"] != managed["original_local_asset_id"]
        assert managed["asset_revision_id"] == "asset-revision-managed-1"
        assert managed["evidence_revision"] == "evidence-revision-managed-1"
        assert managed["state"] == "AVAILABLE_LOCAL"
        restored_path = target / managed["local_relative_path"]
        assert restored_path.read_bytes() == MANAGED_BYTES
        assert hashlib.sha256(restored_path.read_bytes()).hexdigest() == hashlib.sha256(
            MANAGED_BYTES
        ).hexdigest()
        derived_path = target / managed["derived_local_relative_paths"][0]
        assert derived_path.read_bytes() == DERIVED_BYTES
        assert linked["state"] == "LINKED_FILE_MISSING"
        assert linked["local_relative_path"] is None
        restored_models = json.loads(
            (target / "data/portability/model-manifests.json").read_text(encoding="utf-8")
        )
        assert restored_models[0]["device_state"] == "REDOWNLOAD_REQUIRED"
        assert restored_models[0]["binary_exported"] is False


def test_checksum_tamper_and_account_mismatch_fail_closed_without_partial_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    vault = tmp_path / "vault"
    package = tmp_path / "quick.mentorflowpack"
    _export(vault, package, mode=PackageMode.QUICK)
    tampered = tmp_path / "tampered.mentorflowpack"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(
        tampered, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "structured/account.json":
                payload += b" "
            target.writestr(info, payload)
    with _new_device_root() as device:
        target = device / "tamper-target"
        with pytest.raises(AccountPortabilityError) as failure:
            import_account_package(tampered, target, expected_account_identity=ACCOUNT, test_only=True)
        assert failure.value.code == "PACKAGE_CHECKSUM_MISMATCH"
        assert not target.exists() or not any(target.iterdir())

        mismatch = device / "mismatch-target"
        with pytest.raises(AccountPortabilityError) as denied:
            import_account_package(package, mismatch, expected_account_identity=OTHER_ACCOUNT, test_only=True)
        assert denied.value.code == "PACKAGE_ACCOUNT_MISMATCH"
        assert not mismatch.exists() or not any(mismatch.iterdir())


def test_security_rejects_zip_slip_symlink_compression_bomb_and_nonempty_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    bad_archives: list[tuple[Path, str]] = []
    slip = tmp_path / "slip.mentorflowpack"
    with zipfile.ZipFile(slip, "w") as bundle:
        bundle.writestr("../escape", b"x")
    bad_archives.append((slip, "PACKAGE_ZIP_SLIP_REJECTED"))

    alternate_stream = tmp_path / "alternate-stream.mentorflowpack"
    with zipfile.ZipFile(alternate_stream, "w") as bundle:
        bundle.writestr("manifest.json:stream", b"x")
    bad_archives.append((alternate_stream, "PACKAGE_ZIP_SLIP_REJECTED"))

    symlink = tmp_path / "symlink.mentorflowpack"
    with zipfile.ZipFile(symlink, "w") as bundle:
        info = zipfile.ZipInfo("manifest.json")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, "target")
    bad_archives.append((symlink, "PACKAGE_LINK_REJECTED"))

    ratio = tmp_path / "ratio.mentorflowpack"
    with zipfile.ZipFile(ratio, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", b"0" * 200_000)
    bad_archives.append((ratio, "PACKAGE_COMPRESSION_RATIO"))

    with _new_device_root() as device:
        for archive, expected_code in bad_archives:
            with pytest.raises(AccountPortabilityError) as failure:
                import_account_package(
                    archive,
                    device / f"target-{uuid.uuid4().hex}",
                    expected_account_identity=ACCOUNT,
                    test_only=True,
                )
            assert failure.value.code == expected_code

        vault = tmp_path / "vault"
        package = tmp_path / "valid.mentorflowpack"
        _export(vault, package, mode=PackageMode.QUICK)
        occupied = device / "occupied"
        occupied.mkdir()
        sentinel = occupied / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with pytest.raises(AccountPortabilityError) as overwrite:
            import_account_package(package, occupied, expected_account_identity=ACCOUNT, test_only=True)
        assert overwrite.value.code == "IMPORT_TARGET_NOT_EMPTY"
        assert sentinel.read_text(encoding="utf-8") == "keep"


def test_secret_value_source_hardlink_and_unconfirmed_personal_local_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(AccountPortabilityError) as secret:
        export_account_package(
            vault,
            tmp_path / "secret.mentorflowpack",
            account_identity=ACCOUNT,
            preferences={"ordinary_note": "Bearer abcdefghijklmnopqrstuvwxyz"},
        )
    assert secret.value.code == "SECRET_MATERIAL_REJECTED"
    assert not (tmp_path / "secret.mentorflowpack").exists()

    with pytest.raises(AccountPortabilityError) as secret_url:
        export_account_package(
            vault,
            tmp_path / "secret-url.mentorflowpack",
            account_identity=ACCOUNT,
            asset_manifests=[
                {
                    "asset_id": "asset-secret-url",
                    "asset_revision_id": "revision-secret-url",
                    "local_asset_id": "local-secret-url",
                    "storage_kind": "MANAGED",
                    "filename": "lesson.pdf",
                    "verified_source_url": (
                        "https://downloads.example.test/lesson.pdf?"
                        "access_token=abcdefghijklmnopqrstuvwxyz"
                    ),
                }
            ],
        )
    assert secret_url.value.code == "SECRET_MATERIAL_REJECTED"

    with pytest.raises(AccountPortabilityError) as personal:
        export_account_package(
            vault,
            tmp_path / "personal.mentorflowpack",
            account_identity=ACCOUNT,
            personal_local=True,
        )
    assert personal.value.code == "PERSONAL_LOCAL_EXPORT_NOT_CONFIRMED"

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    linked = vault / "managed" / "local-hardlink" / "hardlink.bin"
    linked.parent.mkdir(parents=True)
    try:
        os.link(outside, linked)
    except OSError as exc:
        pytest.skip(f"hardlink unavailable: {exc}")
    with pytest.raises(AccountPortabilityError) as hardlink:
        export_account_package(
            vault,
            tmp_path / "hardlink.mentorflowpack",
            account_identity=ACCOUNT,
            mode=PackageMode.FULL,
            asset_manifests=[
                {
                    "asset_id": "asset-hardlink",
                    "asset_revision_id": "revision-hardlink",
                    "local_asset_id": "local-hardlink",
                    "storage_kind": "MANAGED",
                    "filename": "hardlink.bin",
                    "vault_relative_path": "managed/local-hardlink/hardlink.bin",
                }
            ],
        )
    assert hardlink.value.code == "UNSAFE_ASSET_REJECTED"


def test_reparse_target_and_atomic_publish_failure_leave_no_partial_account_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    vault = tmp_path / "vault"
    package = tmp_path / "valid.mentorflowpack"
    _export(vault, package, mode=PackageMode.FULL)
    with _new_device_root() as device:
        real_parent = device / "real-parent"
        real_parent.mkdir()
        linked_parent = device / "linked-parent"
        try:
            os.symlink(real_parent, linked_parent, target_is_directory=True)
        except OSError:
            linked_parent = None
        if linked_parent is not None:
            with pytest.raises(AccountPortabilityError) as reparse:
                import_account_package(
                    package,
                    linked_parent / "target",
                    expected_account_identity=ACCOUNT,
                    test_only=True,
                )
            assert reparse.value.code == "PACKAGE_IMPORT_FAILED"
            assert not any(real_parent.iterdir())

        target = device / "atomic-target"
        native_replace = account_portability_module.os.replace

        def fail_publish(source, destination):
            if Path(source).name == "data" and Path(destination).name == "data":
                raise OSError("injected atomic publish failure")
            return native_replace(source, destination)

        monkeypatch.setattr(account_portability_module.os, "replace", fail_publish)
        with pytest.raises(AccountPortabilityError) as partial:
            import_account_package(
                package,
                target,
                expected_account_identity=ACCOUNT,
                test_only=True,
            )
        assert partial.value.code == "PACKAGE_IMPORT_FAILED"
        assert target.is_dir()
        assert not any(target.iterdir())
