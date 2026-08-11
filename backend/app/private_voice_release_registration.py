"""Resolve the one app-owned, local-private voice release without env setup.

This descriptor is deliberately separate from the production release trust
registry.  It can select only an already materialized, non-exportable private
release and binds the small release/catalog/registration receipts by digest.
Payload and runtime trees remain verified lazily at the first synthesis click.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCHEMA = "mentorflow.app-private-voice-release-registration.v1"
SCOPE = "private_local_only_nonexportable"


class PrivateVoiceReleaseRegistrationError(RuntimeError):
    pass


def app_owned_descriptor_path() -> Path | None:
    """Return the per-user descriptor; never fall back to a repo/build path."""
    local = str(os.getenv("LOCALAPPDATA") or "").strip()
    if not local:
        return None
    return Path(local) / "MentorFlow" / "private-voice" / "active-private-voice.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plain_file(path: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_file() or absolute.is_symlink():
        raise PrivateVoiceReleaseRegistrationError(f"{label} is not a plain file")
    if int(absolute.stat().st_nlink) != 1:
        raise PrivateVoiceReleaseRegistrationError(f"{label} has an unexpected link count")
    return absolute.resolve(strict=True)


def _plain_dir(path: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_dir() or absolute.is_symlink():
        raise PrivateVoiceReleaseRegistrationError(f"{label} is not a plain directory")
    return absolute.resolve(strict=True)


def resolve_app_private_release(
    descriptor_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return bound private-release paths, or ``None`` when not configured.

    This is intentionally a small-file operation.  In particular it never
    hashes the 2.5 GiB model or the 4.8 GiB portable runtime tree.
    """

    path = descriptor_path if descriptor_path is not None else app_owned_descriptor_path()
    if path is None:
        return None
    if not path.exists():
        return None
    path = _plain_file(path, "app private voice descriptor")
    try:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateVoiceReleaseRegistrationError("app private voice descriptor is invalid") from exc
    expected = {
        "schema", "enabled", "scope", "release_root",
        "release_receipt_sha256", "release_verification_sha256",
        "catalog_sha256", "runtime_registration_sha256",
    }
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != expected
        or descriptor.get("schema") != SCHEMA
        or descriptor.get("enabled") is not True
        or descriptor.get("scope") != SCOPE
    ):
        raise PrivateVoiceReleaseRegistrationError("app private voice descriptor fields are invalid")
    for field in expected - {"schema", "enabled", "scope", "release_root"}:
        value = descriptor.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise PrivateVoiceReleaseRegistrationError(f"{field} is not a SHA256 digest")

    release = _plain_dir(Path(str(descriptor["release_root"])), "private voice release")
    receipt_path = _plain_file(release / "PRIVATE_RELEASE_RECEIPT.json", "private release receipt")
    verification_path = _plain_file(release / "PRIVATE_RELEASE_VERIFICATION.json", "private release verification")
    catalog_path = _plain_file(release / "catalog" / "teacher_voice_pack_catalog.private.v1.json", "private voice catalog")
    source_runtime_registration_path = _plain_file(release / "catalog" / "private-runtime-registration.json", "private runtime registration source")
    installed_root = _plain_dir(release / "installed-private-voice", "installed private voice root")
    bound = {
        receipt_path: "release_receipt_sha256",
        verification_path: "release_verification_sha256",
        catalog_path: "catalog_sha256",
    }
    for artifact, field in bound.items():
        if _sha(artifact) != descriptor[field]:
            raise PrivateVoiceReleaseRegistrationError(f"app private voice {field} binding mismatch")

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if (
        receipt.get("schema") != "mentorflow.private-voice-release.v1"
        or receipt.get("status") != "installed_active_verified"
        or receipt.get("scope") != SCOPE
        or receipt.get("exportable") is not False
        or receipt.get("public_release") is not False
        or receipt.get("commercial_use") is not False
        or receipt.get("catalog_sha256") != descriptor["catalog_sha256"]
        or receipt.get("registration_sha256") != _sha(source_runtime_registration_path)
        or receipt.get("verification_sha256") != descriptor["release_verification_sha256"]
        or verification.get("schema") != "mentorflow.private-voice-release-verification.v1"
        or verification.get("status") != "pass"
        or verification.get("scope") != SCOPE
        or (
            str(verification.get("release_root")) != "."
            and Path(str(verification.get("release_root"))).resolve(strict=False) != release
        )
    ):
        raise PrivateVoiceReleaseRegistrationError("app private voice release receipt semantics are invalid")
    release_id = str(receipt.get("release_id") or "")
    if not release_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in release_id):
        raise PrivateVoiceReleaseRegistrationError("app private voice release id is invalid")
    registration_id = release_id.lower()
    runtime_registration_path = _plain_file(
        path.parent / "runtime-registrations" / f"{registration_id}.json",
        "relocated private runtime registration",
    )
    if _sha(runtime_registration_path) != descriptor["runtime_registration_sha256"]:
        raise PrivateVoiceReleaseRegistrationError("app private voice runtime registration binding mismatch")
    source_registration = json.loads(source_runtime_registration_path.read_text(encoding="utf-8"))
    relocated_registration = json.loads(runtime_registration_path.read_text(encoding="utf-8"))
    if not isinstance(source_registration, dict) or not isinstance(relocated_registration, dict):
        raise PrivateVoiceReleaseRegistrationError("private runtime registration is invalid")
    expected_relocated = dict(source_registration)
    expected_relocated["runtime_root"] = str(_plain_dir(release / "runtime-component", "private runtime component"))
    expected_relocated["cache_root"] = str(_plain_dir(release / "runtime-cache", "private runtime cache"))
    if relocated_registration != expected_relocated:
        raise PrivateVoiceReleaseRegistrationError("relocated private runtime registration semantics are invalid")
    return {
        "descriptor_path": path,
        "release_root": release,
        "catalog_path": catalog_path,
        "pack_root": installed_root,
        "runtime_registration_path": runtime_registration_path,
        "release_id": release_id,
        "scope": SCOPE,
    }
