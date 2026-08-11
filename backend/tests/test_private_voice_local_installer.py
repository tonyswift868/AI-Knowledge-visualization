from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from backend.app.private_voice_release_registration import resolve_app_private_release


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "private_voice_local_installer.py"
SPEC = importlib.util.spec_from_file_location("private_voice_local_installer", SCRIPT)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package(root: Path, release_id: str) -> Path:
    installer_files = {
        "private_voice_local_installer.py": ("installer", b"fixture installer"),
        "INSTALL_VOICE.cmd": ("installer_entrypoint", b"fixture command"),
        "INSTALL_VOICE_README.txt": ("installer_documentation", b"fixture readme"),
    }
    for relative, (_role, raw) in installer_files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(raw)
    archive = root / "catalog" / "bundled" / "voice.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"small fixture archive")
    component = root / "runtime-component" / "PRIVATE_RUNTIME_COMPONENT.json"
    write_json(component, {"schema": "fixture", "scope": installer.SCOPE})
    (root / "runtime-cache").mkdir()
    write_json(
        root / "catalog" / "private-runtime-registration.json",
        {
            "schema": "mentorflow.private-qwen-runtime-registration.v1",
            "enabled": True,
            "registration_id": "fixture-runtime",
            "runtime_root": "stale-machine-path-is-ignored",
            "cache_root": "stale-machine-path-is-ignored",
            "component_sha256": sha(component),
            "component_signature": "fixture",
            "signing_key_id": "fixture-key",
            "public_key": "fixture",
            "port": 18893,
        },
    )
    write_json(
        root / "catalog" / "teacher_voice_pack_catalog.private.v1.json",
        {
            "schema": "mentorflow.voice-pack-catalog.v1",
            "https_allowed_hosts": [],
            "trusted_keys": [],
            "packs": [
                {
                    "pack_id": "fixture-private-voice",
                    "version": "0.1.0-prerelease",
                    "default": True,
                    "release_channel": "prerelease",
                    "scope": installer.SCOPE,
                    "usage_scope": "local_private",
                    "signing_key_id": "fixture-key",
                    "source": {"kind": "bundled", "archive_path": "bundled/voice.zip"},
                    "archive": {"bytes": archive.stat().st_size, "sha256": sha(archive)},
                }
            ],
        },
    )
    write_json(
        root / "installed-private-voice" / ".state" / "active.json",
        {
            "schema": "mentorflow.active-voice-pack.v1",
            "pack_id": "fixture-private-voice",
            "version": "0.1.0-prerelease",
            "manifest_sha256": "a" * 64,
        },
    )
    verification = root / "PRIVATE_RELEASE_VERIFICATION.json"
    write_json(
        verification,
        {
            "schema": "mentorflow.private-voice-release-verification.v1",
            "status": "pass",
            "scope": installer.SCOPE,
            "release_root": ".",
        },
    )
    catalog = root / "catalog" / "teacher_voice_pack_catalog.private.v1.json"
    registration = root / "catalog" / "private-runtime-registration.json"
    write_json(
        root / "PRIVATE_RELEASE_RECEIPT.json",
        {
            "schema": "mentorflow.private-voice-release.v1",
            "release_id": release_id,
            "scope": installer.SCOPE,
            "status": "installed_active_verified",
            "exportable": False,
            "public_release": False,
            "commercial_use": False,
            "catalog_sha256": sha(catalog),
            "registration_sha256": sha(registration),
            "verification_sha256": sha(verification),
            "distribution": {
                "mode": "self_contained_double_click_offline",
                "api_key_required": False,
                "system_python_required": False,
                "files": [
                    {
                        "path": relative,
                        "role": role,
                        "bytes": (root / relative).stat().st_size,
                        "sha256": sha(root / relative),
                    }
                    for relative, (role, _raw) in installer_files.items()
                ],
            },
        },
    )
    return root


def test_install_verify_activate_and_rollback_are_reference_only(tmp_path: Path) -> None:
    first = package(tmp_path / "portable-a", "fixture-a")
    second = package(tmp_path / "portable-b", "fixture-b")
    registry = tmp_path / "app-owned-registry"

    installed_a = installer.install(first, registry)
    installed_b = installer.install(second, registry)
    assert installed_a["copied_large_payloads"] is False
    assert installed_b["production_catalog_unchanged"] is True
    descriptor = json.loads(Path(installed_a["descriptor"]).read_text(encoding="utf-8"))
    assert descriptor["package_root"] == str(first.resolve())
    assert all(not Path(value).is_absolute() for value in descriptor["paths"].values())
    assert installer.verify(registry, "fixture-a")["status"] == "verified"

    installer.activate(registry, "fixture-a")
    assert installer.rollback(registry)["restored_release_root"] is None
    assert not (registry / "active-private-voice.json").exists()
    installer.activate(registry, "fixture-a")
    installer.activate(registry, "fixture-b")
    assert installer.rollback(registry)["restored_release_root"] == str(first.resolve())
    active = json.loads((registry / "active-private-voice.json").read_text(encoding="utf-8"))
    assert active["release_root"] == str(first.resolve())
    assert set(active) == {
        "schema", "enabled", "scope", "release_root", "release_receipt_sha256",
        "release_verification_sha256", "catalog_sha256", "runtime_registration_sha256",
    }


def test_verify_fails_closed_after_payload_tamper(tmp_path: Path) -> None:
    source = package(tmp_path / "portable", "fixture-a")
    registry = tmp_path / "registry"
    installer.install(source, registry)
    (source / "catalog" / "bundled" / "voice.zip").write_bytes(b"tampered")
    with pytest.raises(installer.InstallError, match="archive size"):
        installer.verify(registry, "fixture-a")


def test_install_rejects_tampered_double_click_entrypoint(tmp_path: Path) -> None:
    source = package(tmp_path / "portable", "fixture-a")
    (source / "INSTALL_VOICE.cmd").write_bytes(b"tampered command")
    with pytest.raises(installer.InstallError, match="distribution file binding mismatch"):
        installer.install(source, tmp_path / "registry")


def test_private_boundary_rejects_public_release(tmp_path: Path) -> None:
    source = package(tmp_path / "portable", "fixture-a")
    receipt = source / "PRIVATE_RELEASE_RECEIPT.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["public_release"] = True
    write_json(receipt, value)
    with pytest.raises(installer.InstallError, match="non-exportable private"):
        installer.inspect_package(source)


def test_installer_has_no_machine_specific_drive_or_model_identity() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "E:\\" not in source
    assert "C:\\" not in source
    assert "tsubaki" not in source.lower()
    assert "SetEnvironmentVariable" not in source
    assert "subprocess" not in source


def test_uppercase_real_builder_release_id_is_normalized_for_registry(tmp_path: Path) -> None:
    source = package(tmp_path / "portable", "vp-20260808T073727Z-ba09c8eb")
    descriptor = installer.inspect_package(source)
    assert descriptor["release_id"] == "vp-20260808T073727Z-ba09c8eb"
    assert descriptor["registration_id"] == "vp-20260808t073727z-ba09c8eb"


def test_relocated_registration_tracks_package_after_move(tmp_path: Path) -> None:
    original = package(tmp_path / "source" / "portable", "fixture-a")
    moved = tmp_path / "different-volume-fixture" / "portable"
    moved.parent.mkdir()
    original.rename(moved)
    registry = tmp_path / "registry"
    result = installer.install(moved, registry)
    relocated = json.loads(Path(result["runtime_registration"]).read_text(encoding="utf-8"))
    assert Path(relocated["runtime_root"]) == moved.resolve() / "runtime-component"
    assert Path(relocated["cache_root"]) == moved.resolve() / "runtime-cache"
    assert relocated["runtime_root"] != "stale-machine-path-is-ignored"
    assert installer.verify(registry, "fixture-a")["status"] == "verified"


def test_backend_auto_discovers_app_owned_active_pointer_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = package(tmp_path / "portable", "fixture-a")
    local_app_data = tmp_path / "local-app-data"
    registry = local_app_data / "MentorFlow" / "private-voice"
    result = installer.install(source, registry)
    installer.activate(registry, result["registration_id"])
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    resolved = resolve_app_private_release()
    assert resolved is not None
    assert resolved["release_root"] == source.resolve()
    assert resolved["catalog_path"] == source.resolve() / "catalog" / "teacher_voice_pack_catalog.private.v1.json"
    assert resolved["runtime_registration_path"] == registry / "runtime-registrations" / "fixture-a.json"


def test_backend_without_app_owned_pointer_does_not_read_repo_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty-local-app-data"))
    assert resolve_app_private_release() is None
