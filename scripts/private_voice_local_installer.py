#!/usr/bin/env python3
"""Reference-only installer for private MentorFlow voice packages.

The installer never copies model/runtime payloads, never changes user or
machine environment variables, and never launches MentorFlow or a model
service.  It stores an app-owned descriptor whose component paths are relative
to the package root.  Activation and rollback only switch a small atomic
pointer in the app-owned registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


REGISTRY_SCHEMA = "mentorflow.private-voice-local-registry.v1"
DESCRIPTOR_SCHEMA = "mentorflow.private-voice-local-registration.v1"
ACTIVE_SCHEMA = "mentorflow.private-voice-local-active.v1"
SCOPE = "private_local_only_nonexportable"
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
SAFE_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InstallError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"{label} must be a JSON object")
    return value


def _plain_dir(path: Path, label: str) -> Path:
    try:
        absolute = Path(os.path.abspath(path)).resolve(strict=True)
    except OSError as exc:
        raise InstallError(f"{label} is unavailable: {exc}") from exc
    if not absolute.is_dir() or path.is_symlink():
        raise InstallError(f"{label} must be a plain directory")
    if absolute.parent == absolute:
        raise InstallError(f"{label} cannot be a filesystem root")
    return absolute


def _plain_file(path: Path, label: str) -> Path:
    try:
        absolute = Path(os.path.abspath(path)).resolve(strict=True)
        links = absolute.stat().st_nlink
    except OSError as exc:
        raise InstallError(f"{label} is unavailable: {exc}") from exc
    if not absolute.is_file() or path.is_symlink() or links != 1:
        raise InstallError(f"{label} must be a plain single-link file")
    return absolute


def _relative(value: Any, label: str) -> str:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise InstallError(f"{label} must be a safe relative POSIX path")
    return path.as_posix()


def _join(root: Path, relative: str, label: str, *, directory: bool = False) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    result = _plain_dir(candidate, label) if directory else _plain_file(candidate, label)
    if not result.is_relative_to(root):
        raise InstallError(f"{label} escapes package root")
    return result


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "")
    if not SAFE_ID.fullmatch(text):
        raise InstallError(f"{label} is invalid")
    return text


def _release_registration_id(value: Any) -> tuple[str, str]:
    release_id = str(value or "")
    if not SAFE_RELEASE_ID.fullmatch(release_id):
        raise InstallError("release_id is invalid")
    return release_id, release_id.lower()


def default_config_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise InstallError("LOCALAPPDATA is unavailable; pass --config-root")
    return Path(local) / "MentorFlow" / "private-voice"


def inspect_package(package_root: Path, *, hash_archive: bool = True) -> dict[str, Any]:
    root = _plain_dir(package_root, "package root")
    receipt_rel = "PRIVATE_RELEASE_RECEIPT.json"
    verification_rel = "PRIVATE_RELEASE_VERIFICATION.json"
    catalog_rel = "catalog/teacher_voice_pack_catalog.private.v1.json"
    registration_rel = "catalog/private-runtime-registration.json"
    pack_root_rel = "installed-private-voice"
    runtime_root_rel = "runtime-component"
    cache_root_rel = "runtime-cache"
    receipt_path = _join(root, receipt_rel, "private release receipt")
    verification_path = _join(root, verification_rel, "private release verification")
    catalog_path = _join(root, catalog_rel, "private catalog")
    source_registration_path = _join(root, registration_rel, "private runtime registration")
    pack_root = _join(root, pack_root_rel, "installed voice root", directory=True)
    runtime_root = _join(root, runtime_root_rel, "private runtime root", directory=True)
    _join(root, cache_root_rel, "private runtime cache", directory=True)

    receipt = _json(receipt_path, "private release receipt")
    if (
        receipt.get("schema") != "mentorflow.private-voice-release.v1"
        or receipt.get("scope") != SCOPE
        or receipt.get("status") != "installed_active_verified"
        or receipt.get("exportable") is not False
        or receipt.get("public_release") is not False
        or receipt.get("commercial_use") is not False
    ):
        raise InstallError("release is not an installed, non-exportable private package")
    distribution = receipt.get("distribution")
    files = distribution.get("files") if isinstance(distribution, dict) else None
    if (
        not isinstance(distribution, dict)
        or distribution.get("mode") != "self_contained_double_click_offline"
        or distribution.get("api_key_required") is not False
        or distribution.get("system_python_required") is not False
        or not isinstance(files, list)
        or len(files) != 3
    ):
        raise InstallError("release does not contain a bound self-contained installer")
    expected_installer_roles = {
        "private_voice_local_installer.py": "installer",
        "INSTALL_VOICE.cmd": "installer_entrypoint",
        "INSTALL_VOICE_README.txt": "installer_documentation",
    }
    if {str(item.get("path")): str(item.get("role")) for item in files if isinstance(item, dict)} != expected_installer_roles:
        raise InstallError("release installer file set is invalid")
    for item in files:
        relative = _relative(item.get("path"), "distribution file path")
        target = _join(root, relative, f"distribution file {relative}")
        if (
            isinstance(item.get("bytes"), bool)
            or not isinstance(item.get("bytes"), int)
            or item["bytes"] != target.stat().st_size
            or not SHA256.fullmatch(str(item.get("sha256") or ""))
            or _sha(target) != item["sha256"]
        ):
            raise InstallError(f"distribution file binding mismatch: {relative}")
    release_id, registration_id = _release_registration_id(receipt.get("release_id"))

    catalog = _json(catalog_path, "private catalog")
    packs = catalog.get("packs")
    if catalog.get("schema") != "mentorflow.voice-pack-catalog.v1" or not isinstance(packs, list) or len(packs) != 1:
        raise InstallError("private catalog must contain exactly one pack")
    pack = packs[0]
    if not isinstance(pack, dict):
        raise InstallError("private catalog entry must be an object")
    if (
        pack.get("scope") != SCOPE
        or pack.get("usage_scope") != "local_private"
        or pack.get("release_channel") != "prerelease"
        or pack.get("default") is not True
        or not isinstance(pack.get("source"), dict)
        or pack["source"].get("kind") != "bundled"
    ):
        raise InstallError("catalog entry crosses the local-private boundary")
    pack_id = _safe_id(pack.get("pack_id"), "pack_id")
    version = _safe_id(pack.get("version"), "version")
    archive_rel_from_catalog = _relative(pack["source"].get("archive_path"), "source.archive_path")
    archive_rel = (PurePosixPath("catalog") / archive_rel_from_catalog).as_posix()
    archive_path = _join(root, archive_rel, "bundled voice archive")
    archive = pack.get("archive")
    if not isinstance(archive, dict) or archive_path.stat().st_size != archive.get("bytes"):
        raise InstallError("bundled voice archive size does not match catalog")
    archive_digest = str(archive.get("sha256") or "")
    if not SHA256.fullmatch(archive_digest):
        raise InstallError("bundled voice archive digest is invalid")
    if hash_archive and _sha(archive_path) != archive_digest:
        raise InstallError("bundled voice archive digest does not match catalog")

    active_rel = "installed-private-voice/.state/active.json"
    active_path = _join(root, active_rel, "active voice state")
    active = _json(active_path, "active voice state")
    if (
        active.get("schema") != "mentorflow.active-voice-pack.v1"
        or active.get("pack_id") != pack_id
        or active.get("version") != version
        or not SHA256.fullmatch(str(active.get("manifest_sha256") or ""))
    ):
        raise InstallError("installed active state does not match catalog")

    registration = _json(source_registration_path, "private runtime registration")
    component_rel = "runtime-component/PRIVATE_RUNTIME_COMPONENT.json"
    component_path = _join(root, component_rel, "signed runtime component receipt")
    if (
        registration.get("schema") != "mentorflow.private-qwen-runtime-registration.v1"
        or registration.get("enabled") is not True
        or _safe_id(registration.get("registration_id"), "runtime registration id") == ""
        or registration.get("signing_key_id") != pack.get("signing_key_id")
        or registration.get("component_sha256") != _sha(component_path)
    ):
        raise InstallError("runtime registration does not match signed component")
    verification = _json(verification_path, "private release verification")
    if (
        verification.get("schema") != "mentorflow.private-voice-release-verification.v1"
        or verification.get("status") != "pass"
        or verification.get("scope") != SCOPE
        or receipt.get("catalog_sha256") != _sha(catalog_path)
        or receipt.get("registration_sha256") != _sha(source_registration_path)
        or receipt.get("verification_sha256") != _sha(verification_path)
    ):
        raise InstallError("release verification or receipt bindings are invalid")

    return {
        "schema": DESCRIPTOR_SCHEMA,
        "registration_id": registration_id,
        "release_id": release_id,
        "scope": SCOPE,
        "exportable": False,
        "public_release": False,
        "commercial_use": False,
        "package_root": str(root),
        "paths": {
            "receipt": receipt_rel,
            "verification": verification_rel,
            "catalog": catalog_rel,
            "pack_root": pack_root_rel,
            "active_state": active_rel,
            "runtime_registration_source": registration_rel,
            "runtime_root": runtime_root_rel,
            "runtime_component": component_rel,
            "runtime_cache": cache_root_rel,
            "voice_archive": archive_rel,
        },
        "identity": {"pack_id": pack_id, "version": version},
        "bindings": {
            "receipt_sha256": _sha(receipt_path),
            "verification_sha256": _sha(verification_path),
            "catalog_sha256": _sha(catalog_path),
            "source_runtime_registration_sha256": _sha(source_registration_path),
            "active_state_sha256": _sha(active_path),
            "runtime_component_sha256": _sha(component_path),
            "voice_archive_bytes": archive_path.stat().st_size,
            "voice_archive_sha256": archive_digest,
        },
        "installation": {
            "mode": "reference_only",
            "copied_large_payloads": False,
            "environment_scope": "child_process_only",
            "production_catalog_unchanged": True,
        },
    }


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _registry(config_root: Path) -> Path:
    root = Path(os.path.abspath(config_root))
    if root.parent == root:
        raise InstallError("config root cannot be a filesystem root")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _relocated_registration(descriptor: dict[str, Any]) -> dict[str, Any]:
    package_root = Path(descriptor["package_root"])
    paths = descriptor["paths"]
    source = _json(
        _join(package_root, paths["runtime_registration_source"], "private runtime registration source"),
        "private runtime registration source",
    )
    relocated = dict(source)
    relocated["runtime_root"] = str(
        _join(package_root, paths["runtime_root"], "private runtime root", directory=True)
    )
    relocated["cache_root"] = str(
        _join(package_root, paths["runtime_cache"], "private runtime cache", directory=True)
    )
    return relocated


def install(package_root: Path, config_root: Path) -> dict[str, Any]:
    descriptor = inspect_package(package_root)
    root = _registry(config_root)
    runtime_registration = _relocated_registration(descriptor)
    runtime_target = root / "runtime-registrations" / f"{descriptor['registration_id']}.json"
    _write_atomic(runtime_target, runtime_registration)
    descriptor["bindings"]["runtime_registration_sha256"] = _sha(runtime_target)
    target = root / "registrations" / f"{descriptor['registration_id']}.json"
    if target.exists():
        existing = _json(target, "existing registration")
        if existing != descriptor:
            raise InstallError("registration id already exists with different content")
    else:
        _write_atomic(target, descriptor)
    return {
        "status": "installed",
        "registration_id": descriptor["registration_id"],
        "descriptor": str(target),
        "runtime_registration": str(runtime_target),
        **descriptor["installation"],
    }


def _load_registration(config_root: Path, registration_id: str) -> tuple[Path, dict[str, Any]]:
    safe = _safe_id(registration_id, "registration_id")
    path = _registry(config_root) / "registrations" / f"{safe}.json"
    value = _json(_plain_file(path, "installed registration"), "installed registration")
    if value.get("schema") != DESCRIPTOR_SCHEMA or value.get("registration_id") != safe or value.get("scope") != SCOPE:
        raise InstallError("installed registration semantics are invalid")
    return path, value


def verify(config_root: Path, registration_id: str) -> dict[str, Any]:
    path, stored = _load_registration(config_root, registration_id)
    current = inspect_package(Path(stored["package_root"]))
    runtime_path = _registry(config_root) / "runtime-registrations" / f"{registration_id}.json"
    runtime_path = _plain_file(runtime_path, "relocated private runtime registration")
    if _json(runtime_path, "relocated private runtime registration") != _relocated_registration(current):
        raise InstallError("relocated private runtime registration no longer matches package root")
    current["bindings"]["runtime_registration_sha256"] = _sha(runtime_path)
    if current != stored:
        raise InstallError("installed private package no longer matches its registration")
    return {"status": "verified", "registration_id": registration_id, "descriptor": str(path), **current["installation"]}


def activate(config_root: Path, registration_id: str) -> dict[str, Any]:
    root = _registry(config_root)
    verify(root, registration_id)
    _descriptor_path, descriptor = _load_registration(root, registration_id)
    active_path = root / "active-private-voice.json"
    rollback_path = root / "rollback.json"
    previous = _json(active_path, "active registration pointer") if active_path.exists() else None
    _write_atomic(
        rollback_path,
        {"schema": REGISTRY_SCHEMA, "previous": previous},
    )
    pointer = {
        "schema": "mentorflow.app-private-voice-release-registration.v1",
        "enabled": True,
        "scope": SCOPE,
        "release_root": descriptor["package_root"],
        "release_receipt_sha256": descriptor["bindings"]["receipt_sha256"],
        "release_verification_sha256": descriptor["bindings"]["verification_sha256"],
        "catalog_sha256": descriptor["bindings"]["catalog_sha256"],
        "runtime_registration_sha256": descriptor["bindings"]["runtime_registration_sha256"],
    }
    _write_atomic(active_path, pointer)
    return {"status": "active", "registration_id": registration_id, "active_pointer": str(active_path), "production_catalog_unchanged": True}


def rollback(config_root: Path) -> dict[str, Any]:
    root = _registry(config_root)
    active_path = root / "active-private-voice.json"
    rollback_path = root / "rollback.json"
    rollback_state = _json(_plain_file(rollback_path, "rollback pointer"), "rollback pointer")
    if set(rollback_state) != {"schema", "previous"} or rollback_state.get("schema") != REGISTRY_SCHEMA:
        raise InstallError("rollback pointer semantics are invalid")
    previous = rollback_state["previous"]
    if previous is None:
        active_path.unlink(missing_ok=True)
        restored = None
    else:
        if not isinstance(previous, dict) or previous.get("schema") != "mentorflow.app-private-voice-release-registration.v1" or previous.get("scope") != SCOPE:
            raise InstallError("previous active pointer semantics are invalid")
        _write_atomic(active_path, previous)
        restored = str(previous["release_root"])
    rollback_path.unlink(missing_ok=True)
    return {"status": "rolled_back", "restored_release_root": restored, "production_catalog_unchanged": True}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("plan", "install", "verify", "activate", "rollback"))
    result.add_argument("--package-root", type=Path)
    result.add_argument("--registration-id")
    result.add_argument("--config-root", type=Path, default=None)
    result.add_argument("--no-activate", action="store_true", help="register only; install activates by default")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        config_root = args.config_root or default_config_root()
        if args.action in {"plan", "install"} and args.package_root is None:
            raise InstallError("--package-root is required")
        if args.action in {"verify", "activate"} and not args.registration_id:
            raise InstallError("--registration-id is required")
        if args.action == "plan":
            result = inspect_package(args.package_root)
        elif args.action == "install":
            result = install(args.package_root, config_root)
            if not args.no_activate:
                activated = activate(config_root, result["registration_id"])
                result = {**result, "status": "installed_active", "active_pointer": activated["active_pointer"]}
        elif args.action == "verify":
            result = verify(config_root, args.registration_id)
        elif args.action == "activate":
            result = activate(config_root, args.registration_id)
        else:
            result = rollback(config_root)
    except InstallError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
