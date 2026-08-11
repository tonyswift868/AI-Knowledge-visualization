"""Fixed-root verification for MentorFlow release signing policy chains.

This module is deliberately side-effect free: importing it does not create
files, inspect environment variables, start services, or load a model.  Both
the release-key CLI and the production runtime call this verifier so the
application trust boundary cannot drift from the operator verification path.

Production trust always starts at :data:`RELEASE_TRUST_ROOTS_PATH`.  The only
registry override is an underscored, test-only argument which is rejected
unless test roots were explicitly enabled by an in-process test fixture.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import stat
from ctypes import wintypes
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


TRUST_RECEIPT_SCHEMA = "mentorflow.voice-release-public-trust.v1"
TRUST_ROOTS_SCHEMA = "mentorflow.release-trust-roots.v1"
TRUST_POLICY_SCHEMA = "mentorflow.release-trust-policy.v1"
RELEASE_TRUST_BINDING_SCHEMA = "mentorflow.release-trust-binding.v1"
RELEASE_TRUST_ROOTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "voice"
    / "release_trust_roots.v1.json"
)
ALLOWED_PURPOSES = frozenset(
    {
        "mentorflow-voice-pack-release",
        "mentorflow-engine-pack-release",
        "mentorflow-voice-and-engine-release",
        "mentorflow-release-catalog",
    }
)
MANIFEST_CONTRACTS: dict[str, dict[str, str]] = {
    "engine-manifest.json": {
        "schema": "mentorflow.qwen-engine-manifest.v1",
        "signature": "engine-manifest.sig",
        "scope": "engine",
    },
    "voice-pack.json": {
        "schema": "mentorflow.voice-pack.v1",
        "signature": "voice-pack.sig",
        "scope": "voice",
    },
    "engine-catalog.json": {
        "schema": "mentorflow.qwen-engine-catalog.v1",
        "signature": "engine-catalog.sig",
        "scope": "catalog",
    },
    "voice-pack-catalog.json": {
        "schema": "mentorflow.voice-pack-catalog.v1",
        "signature": "voice-pack-catalog.sig",
        "scope": "catalog",
    },
}
PURPOSE_SCOPES = {
    "mentorflow-voice-pack-release": frozenset({"voice"}),
    "mentorflow-engine-pack-release": frozenset({"engine"}),
    "mentorflow-voice-and-engine-release": frozenset({"voice", "engine"}),
    "mentorflow-release-catalog": frozenset({"catalog"}),
}

_KEY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_REPARSE_ATTRIBUTE = 0x00000400
_DIRECTORY_ATTRIBUTE = 0x00000010
_GENERIC_READ = 0x80000000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_TRUST_TOKEN = object()
ReadFile = Callable[[Path, str, int], bytes]


class ReleaseTrustError(RuntimeError):
    """Stable, non-secret failure returned by the shared trust verifier."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class _FileIdentity:
    volume_serial: int
    file_id: int
    size: int
    links: int
    attributes: int


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
else:  # pragma: no cover - production package is Windows-only.
    _KERNEL32 = None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_json(raw: bytes, *, code: str, label: str) -> Any:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def no_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=no_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReleaseTrustError(code, f"{label} is not strict UTF-8 JSON") from exc


def _safe_key_id(value: Any, *, code: str = "KEY_ID_INVALID") -> str:
    if not isinstance(value, str) or not _KEY_ID.fullmatch(value):
        raise ReleaseTrustError(code, "key id must be a stable lowercase token")
    return value


def _fingerprint(value: Any, *, field: str, code: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ReleaseTrustError(code, f"{field} must be a canonical lowercase SHA256 value")
    return value


def _decode_base64(value: Any, *, expected: int, code: str, field: str) -> bytes:
    if not isinstance(value, str):
        raise ReleaseTrustError(code, f"{field} is not canonical base64")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ReleaseTrustError(code, f"{field} is not canonical base64") from exc
    if len(decoded) != expected or base64.b64encode(decoded).decode("ascii") != value:
        raise ReleaseTrustError(code, f"{field} has the wrong length or encoding")
    return decoded


def _utc_timestamp(value: Any, *, field: str, code: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        raise ReleaseTrustError(code, f"{field} must use canonical YYYY-MM-DDTHH:MM:SSZ UTC")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ReleaseTrustError(code, f"{field} is not a valid UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ReleaseTrustError(code, f"{field} is not canonical UTC")
    return parsed


def _iso_date(value: Any, *, field: str) -> date:
    if not isinstance(value, str):
        raise ReleaseTrustError("ROTATION_INVALID", f"{field} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ReleaseTrustError("ROTATION_INVALID", f"{field} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ReleaseTrustError("ROTATION_INVALID", f"{field} must use canonical YYYY-MM-DD")
    return parsed


def _assert_safe_existing_chain(path: Path, *, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise ReleaseTrustError("TRUST_FILE_INVALID", f"{label} cannot be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode) or bool(
            int(getattr(metadata, "st_file_attributes", 0) or 0) & _REPARSE_ATTRIBUTE
        ):
            raise ReleaseTrustError("TRUST_FILE_REPARSE_FORBIDDEN", f"{label} cannot use a reparse point")


def _win_handle_value(value: object) -> int:
    if isinstance(value, int):
        return value
    return int(getattr(value, "value", 0) or 0)


def _win_close(handle: int) -> None:
    if _KERNEL32 is not None and handle not in {0, _INVALID_HANDLE_VALUE}:
        _KERNEL32.CloseHandle(wintypes.HANDLE(int(handle)))


def _win_open(path: Path, *, label: str) -> int:
    assert _KERNEL32 is not None
    ctypes.set_last_error(0)
    handle = _win_handle_value(
        _KERNEL32.CreateFileW(
            str(path),
            _GENERIC_READ | _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    )
    if handle in {0, _INVALID_HANDLE_VALUE}:
        raise ReleaseTrustError(
            "TRUST_FILE_LOCK_FAILED",
            f"{label} could not be opened with write/delete sharing denied",
        )
    return handle


def _win_identity(handle: int, *, label: str) -> _FileIdentity:
    assert _KERNEL32 is not None
    value = _ByHandleFileInformation()
    if not _KERNEL32.GetFileInformationByHandle(wintypes.HANDLE(int(handle)), ctypes.byref(value)):
        raise ReleaseTrustError("TRUST_FILE_IDENTITY_FAILED", f"{label} FileId could not be read")
    attributes = int(value.dwFileAttributes)
    links = int(value.nNumberOfLinks)
    if attributes & (_DIRECTORY_ATTRIBUTE | _REPARSE_ATTRIBUTE):
        raise ReleaseTrustError("TRUST_FILE_TYPE_INVALID", f"{label} must be a plain file")
    if links != 1:
        raise ReleaseTrustError("TRUST_FILE_HARDLINK_FORBIDDEN", f"{label} cannot be a hardlink")
    return _FileIdentity(
        volume_serial=int(value.dwVolumeSerialNumber),
        file_id=(int(value.nFileIndexHigh) << 32) | int(value.nFileIndexLow),
        size=(int(value.nFileSizeHigh) << 32) | int(value.nFileSizeLow),
        links=links,
        attributes=attributes,
    )


def _secure_read_file(path: Path, label: str, maximum_bytes: int) -> bytes:
    """Read a plain file from one fixed handle and reject path substitution."""

    if not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive")
    absolute = Path(os.path.abspath(path))
    _assert_safe_existing_chain(absolute, label=label)
    if os.name == "nt":
        first = _win_open(absolute, label=label)
        try:
            identity = _win_identity(first, label=label)
            if identity.size <= 0 or identity.size > maximum_bytes:
                raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} exceeds its size boundary")
            chunks: list[bytes] = []
            total = 0
            while True:
                buffer = ctypes.create_string_buffer(min(1024 * 1024, maximum_bytes + 1))
                read = wintypes.DWORD(0)
                assert _KERNEL32 is not None
                if not _KERNEL32.ReadFile(
                    wintypes.HANDLE(int(first)),
                    buffer,
                    len(buffer),
                    ctypes.byref(read),
                    None,
                ):
                    raise ReleaseTrustError("TRUST_FILE_READ_FAILED", f"{label} could not be read")
                count = int(read.value)
                if count == 0:
                    break
                total += count
                if total > maximum_bytes or total > identity.size:
                    raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} changed size while reading")
                chunks.append(buffer.raw[:count])
            if total != identity.size or _win_identity(first, label=label) != identity:
                raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} changed while reading")
            second = _win_open(absolute, label=label)
            try:
                if _win_identity(second, label=label) != identity:
                    raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} path identity changed")
            finally:
                _win_close(second)
            return b"".join(chunks)
        finally:
            _win_close(first)

    try:  # pragma: no cover - production path is Windows, used by CI only.
        before = os.lstat(absolute)
        if not stat.S_ISREG(before.st_mode) or int(getattr(before, "st_nlink", 1) or 1) != 1:
            raise ReleaseTrustError("TRUST_FILE_TYPE_INVALID", f"{label} must be one plain non-hardlinked file")
        if not 1 <= int(before.st_size) <= maximum_bytes:
            raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} exceeds its size boundary")
        with absolute.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} changed before open")
            raw = handle.read(maximum_bytes + 1)
            after_open = os.fstat(handle.fileno())
        after = os.lstat(absolute)
    except ReleaseTrustError:
        raise
    except OSError as exc:
        raise ReleaseTrustError("TRUST_FILE_READ_FAILED", f"{label} could not be read") from exc
    if (
        len(raw) != before.st_size
        or len(raw) > maximum_bytes
        or (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    ):
        raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} changed while reading")
    return raw


def secure_read_release_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    """Public fixed-handle reader for registration/catalog descriptor bytes."""

    return _secure_read_file(Path(path), label, maximum_bytes)


def secure_hash_release_file(
    path: Path,
    *,
    label: str,
    expected_bytes: int,
) -> tuple[int, str]:
    """Hash a large release artifact from one identity-bound fixed handle."""

    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes <= 0:
        raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} expected size is invalid")
    absolute = Path(os.path.abspath(path))
    _assert_safe_existing_chain(absolute, label=label)
    digest = hashlib.sha256()
    total = 0
    if os.name == "nt":
        first = _win_open(absolute, label=label)
        try:
            identity = _win_identity(first, label=label)
            if identity.size != expected_bytes:
                raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} size changed")
            while True:
                buffer = ctypes.create_string_buffer(1024 * 1024)
                read = wintypes.DWORD(0)
                assert _KERNEL32 is not None
                if not _KERNEL32.ReadFile(
                    wintypes.HANDLE(int(first)),
                    buffer,
                    len(buffer),
                    ctypes.byref(read),
                    None,
                ):
                    raise ReleaseTrustError("TRUST_FILE_READ_FAILED", f"{label} could not be hashed")
                count = int(read.value)
                if count == 0:
                    break
                total += count
                if total > expected_bytes:
                    raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} grew while hashing")
                digest.update(buffer.raw[:count])
            if total != expected_bytes or _win_identity(first, label=label) != identity:
                raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} changed while hashing")
            second = _win_open(absolute, label=label)
            try:
                if _win_identity(second, label=label) != identity:
                    raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} path identity changed")
            finally:
                _win_close(second)
        finally:
            _win_close(first)
        return total, digest.hexdigest()

    try:  # pragma: no cover - production path is Windows, used by CI only.
        before = os.lstat(absolute)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(getattr(before, "st_nlink", 1) or 1) != 1
            or int(before.st_size) != expected_bytes
        ):
            raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} identity or size is invalid")
        with absolute.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} changed before open")
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(chunk)
                if total > expected_bytes:
                    raise ReleaseTrustError("TRUST_FILE_SIZE_INVALID", f"{label} grew while hashing")
                digest.update(chunk)
            after_open = os.fstat(handle.fileno())
        after = os.lstat(absolute)
    except ReleaseTrustError:
        raise
    except OSError as exc:
        raise ReleaseTrustError("TRUST_FILE_READ_FAILED", f"{label} could not be hashed") from exc
    if (
        total != expected_bytes
        or (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    ):
        raise ReleaseTrustError("TRUST_FILE_CHANGED", f"{label} changed while hashing")
    return total, digest.hexdigest()


def parse_strict_json(raw: bytes, *, code: str, label: str) -> Any:
    """Parse canonical JSON syntax while rejecting duplicate/non-finite fields."""

    return _strict_json(raw, code=code, label=label)


def _read(reader: ReadFile | None, path: Path, label: str, maximum_bytes: int) -> bytes:
    return (reader or _secure_read_file)(Path(path), label, maximum_bytes)


def _manifest_is_test(value: Mapping[str, Any], scope: str) -> bool:
    def marked(identifier: Any, version: Any) -> bool:
        return (
            isinstance(identifier, str)
            and identifier.startswith("test-")
            and isinstance(version, str)
            and "test" in version.casefold()
        )

    if scope == "engine":
        return marked(value.get("package_id"), value.get("version"))
    if scope == "voice":
        return marked(value.get("pack_id"), value.get("version"))
    if scope == "catalog":
        entries = value.get("engines") if isinstance(value.get("engines"), list) else value.get("packs")
        return bool(entries) and all(
            isinstance(item, Mapping)
            and marked(item.get("package_id") or item.get("pack_id"), item.get("version"))
            for item in entries
        )
    return False


@dataclass(frozen=True)
class _TrustSources:
    public_key_path: Path
    trust_receipt_path: Path
    trust_policy_path: Path
    trust_policy_signature_path: Path
    manifest_name: str
    manifest_value: dict[str, Any]
    operation: str
    allow_test_roots: bool
    registry_path_for_tests: Path | None


class VerifiedReleaseTrust:
    """Opaque fixed-root authorization for one leaf and manifest contract."""

    __slots__ = (
        "_token",
        "root_key_id",
        "root_public_key_sha256",
        "registry_sha256",
        "policy_id",
        "policy_sha256",
        "policy_signature_sha256",
        "policy_not_before",
        "policy_not_after",
        "test_only",
        "leaf_key_id",
        "leaf_public_key_sha256",
        "leaf_public_pem_sha256",
        "leaf_receipt_sha256",
        "purpose",
        "manifest_schema",
        "raw_public_key",
        "_sources",
    )

    def __init__(
        self,
        token: object,
        *,
        root_key_id: str,
        root_public_key_sha256: str,
        registry_sha256: str,
        policy_id: str,
        policy_sha256: str,
        policy_signature_sha256: str,
        policy_not_before: str,
        policy_not_after: str,
        test_only: bool,
        leaf_key_id: str,
        leaf_public_key_sha256: str,
        leaf_public_pem_sha256: str,
        leaf_receipt_sha256: str,
        purpose: str,
        manifest_schema: str,
        raw_public_key: bytes,
        sources: _TrustSources,
    ) -> None:
        if token is not _TRUST_TOKEN:
            raise TypeError("verified release trust cannot be constructed directly")
        object.__setattr__(self, "_token", token)
        for name, value in locals().copy().items():
            if name not in {"self", "token", "sources"}:
                object.__setattr__(self, name, value)
        object.__setattr__(self, "_sources", sources)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("verified release trust is immutable")

    def is_valid(self) -> bool:
        return self._token is _TRUST_TOKEN and len(self.raw_public_key) == 32

    @property
    def trust_digest_payload(self) -> dict[str, Any]:
        return {
            "schema": RELEASE_TRUST_BINDING_SCHEMA,
            "registry_sha256": self.registry_sha256,
            "root_key_id": self.root_key_id,
            "root_public_key_sha256": self.root_public_key_sha256,
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "policy_signature_sha256": self.policy_signature_sha256,
            "policy_not_before": self.policy_not_before,
            "policy_not_after": self.policy_not_after,
            "test_only": self.test_only,
            "leaf_key_id": self.leaf_key_id,
            "leaf_public_key_sha256": self.leaf_public_key_sha256,
            "leaf_public_pem_sha256": self.leaf_public_pem_sha256,
            "leaf_receipt_sha256": self.leaf_receipt_sha256,
            "purpose": self.purpose,
            "manifest_schema": self.manifest_schema,
        }

    @property
    def trust_binding_sha256(self) -> str:
        return _sha256(_canonical_json_bytes(self.trust_digest_payload))

    def bind_engine_release(
        self,
        *,
        catalog_sha256: str,
        catalog_bytes: int,
        archive_sha256: str,
        archive_bytes: int,
        catalog_path: Path,
        archive_path: Path,
    ) -> "ReleaseTrustBinding":
        if not self.is_valid() or self.manifest_schema != MANIFEST_CONTRACTS["engine-manifest.json"]["schema"]:
            raise ReleaseTrustError("TRUST_BINDING_INVALID", "engine release requires verified engine-manifest trust")
        return ReleaseTrustBinding(
            _TRUST_TOKEN,
            trust=self,
            catalog_sha256=_fingerprint(catalog_sha256, field="catalog_sha256", code="TRUST_BINDING_INVALID"),
            catalog_bytes=catalog_bytes,
            archive_sha256=_fingerprint(archive_sha256, field="archive_sha256", code="TRUST_BINDING_INVALID"),
            archive_bytes=archive_bytes,
            catalog_path=Path(os.path.abspath(catalog_path)),
            archive_path=Path(os.path.abspath(archive_path)),
        )


class ReleaseTrustBinding:
    """Opaque registration receipt binding trust, catalog and archive bytes."""

    __slots__ = (
        "_token",
        "trust",
        "catalog_sha256",
        "catalog_bytes",
        "archive_sha256",
        "archive_bytes",
        "catalog_path",
        "archive_path",
    )

    def __init__(
        self,
        token: object,
        *,
        trust: VerifiedReleaseTrust,
        catalog_sha256: str,
        catalog_bytes: int,
        archive_sha256: str,
        archive_bytes: int,
        catalog_path: Path,
        archive_path: Path,
    ) -> None:
        if token is not _TRUST_TOKEN or not isinstance(trust, VerifiedReleaseTrust) or not trust.is_valid():
            raise TypeError("release trust bindings cannot be constructed directly")
        object.__setattr__(self, "_token", token)
        object.__setattr__(self, "trust", trust)
        object.__setattr__(self, "catalog_sha256", catalog_sha256)
        if isinstance(catalog_bytes, bool) or not isinstance(catalog_bytes, int) or catalog_bytes <= 0:
            raise TypeError("catalog byte count is invalid")
        object.__setattr__(self, "catalog_bytes", catalog_bytes)
        object.__setattr__(self, "archive_sha256", archive_sha256)
        if isinstance(archive_bytes, bool) or not isinstance(archive_bytes, int) or archive_bytes <= 0:
            raise TypeError("archive byte count is invalid")
        object.__setattr__(self, "archive_bytes", archive_bytes)
        object.__setattr__(self, "catalog_path", catalog_path)
        object.__setattr__(self, "archive_path", archive_path)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("release trust bindings are immutable")

    def is_valid(self) -> bool:
        return self._token is _TRUST_TOKEN and self.trust.is_valid()

    @property
    def receipt(self) -> dict[str, Any]:
        payload = {
            **self.trust.trust_digest_payload,
            "catalog_sha256": self.catalog_sha256,
            "catalog_bytes": self.catalog_bytes,
            "archive_sha256": self.archive_sha256,
            "archive_bytes": self.archive_bytes,
        }
        return {**payload, "binding_sha256": _sha256(_canonical_json_bytes(payload))}


def _load_roots(
    *,
    allow_test_roots: bool,
    registry_path_for_tests: Path | None,
    reader: ReadFile | None,
) -> tuple[dict[str, dict[str, Any]], str]:
    if registry_path_for_tests is not None and not allow_test_roots:
        raise ReleaseTrustError(
            "TRUST_ROOT_OVERRIDE_FORBIDDEN",
            "a registry override is permitted only to an explicit in-process test fixture",
        )
    registry_path = Path(registry_path_for_tests) if registry_path_for_tests is not None else RELEASE_TRUST_ROOTS_PATH
    raw_registry = _read(reader, registry_path, "application release trust-root registry", 256 * 1024)
    registry = _strict_json(raw_registry, code="TRUST_ROOT_REGISTRY_INVALID", label="application release trust-root registry")
    if not isinstance(registry, dict) or set(registry) != {"schema", "production_roots", "test_roots"}:
        raise ReleaseTrustError("TRUST_ROOT_REGISTRY_INVALID", "trust-root registry fields are invalid")
    if registry.get("schema") != TRUST_ROOTS_SCHEMA:
        raise ReleaseTrustError("TRUST_ROOT_REGISTRY_INVALID", "trust-root registry schema is invalid")
    production = registry.get("production_roots")
    testing = registry.get("test_roots")
    if not isinstance(production, list) or not isinstance(testing, list):
        raise ReleaseTrustError("TRUST_ROOT_REGISTRY_INVALID", "trust-root registry sections must be lists")
    if not allow_test_roots and not production:
        raise ReleaseTrustError("TRUST_PRODUCTION_ROOTS_EMPTY", "no production release trust root is installed")

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    roots: dict[str, dict[str, Any]] = {}
    declared_ids: set[str] = set()
    sections = [(production, False), (testing, True)]
    for declarations, section_test_only in sections:
        for declaration in declarations:
            if not isinstance(declaration, dict) or set(declaration) != {
                "algorithm", "root_key_id", "public_key", "public_key_sha256", "status", "test_only"
            }:
                raise ReleaseTrustError("TRUST_ROOT_REGISTRY_INVALID", "trust-root declaration fields are invalid")
            root_key_id = _safe_key_id(declaration.get("root_key_id"), code="TRUST_ROOT_REGISTRY_INVALID")
            if root_key_id in declared_ids:
                raise ReleaseTrustError("TRUST_ROOT_REGISTRY_INVALID", "duplicate trust-root key id")
            declared_ids.add(root_key_id)
            if (
                declaration.get("algorithm") != "ed25519"
                or declaration.get("test_only") is not section_test_only
                or declaration.get("status") != ("test-only" if section_test_only else "active")
                or root_key_id.startswith("test-") != section_test_only
            ):
                raise ReleaseTrustError("TRUST_ROOT_REGISTRY_INVALID", "trust-root boundary fields are invalid")
            raw_public = _decode_base64(
                declaration.get("public_key"), expected=32, code="TRUST_ROOT_REGISTRY_INVALID", field="trust-root public key"
            )
            actual = _sha256(raw_public)
            declared = _fingerprint(
                declaration.get("public_key_sha256"), field="trust-root public key fingerprint", code="TRUST_ROOT_REGISTRY_INVALID"
            )
            if not hmac.compare_digest(actual, declared):
                raise ReleaseTrustError("TRUST_ROOT_REGISTRY_INVALID", "trust-root public key fingerprint is invalid")
            if not section_test_only or allow_test_roots:
                roots[root_key_id] = {
                    "public_key": Ed25519PublicKey.from_public_bytes(raw_public),
                    "public_key_sha256": actual,
                    "test_only": section_test_only,
                }
    return roots, _sha256(raw_registry)


def _load_policy(
    *,
    trust_policy_path: Path,
    trust_policy_signature_path: Path,
    allow_test_roots: bool,
    registry_path_for_tests: Path | None,
    reader: ReadFile | None,
    now: datetime,
) -> dict[str, Any]:
    raw_policy = _read(reader, trust_policy_path, "release trust policy", 512 * 1024)
    raw_signature = _read(reader, trust_policy_signature_path, "release trust policy signature", 64 * 1024)
    signature_document = _strict_json(raw_signature, code="TRUST_POLICY_SIGNATURE_INVALID", label="release trust policy signature")
    if not isinstance(signature_document, dict) or set(signature_document) != {"algorithm", "root_key_id", "signature"}:
        raise ReleaseTrustError("TRUST_POLICY_SIGNATURE_INVALID", "trust policy signature fields are invalid")
    root_key_id = _safe_key_id(signature_document.get("root_key_id"), code="TRUST_POLICY_SIGNATURE_INVALID")
    if signature_document.get("algorithm") != "ed25519":
        raise ReleaseTrustError("TRUST_POLICY_SIGNATURE_INVALID", "trust policy signature algorithm is invalid")
    roots, registry_sha256 = _load_roots(
        allow_test_roots=allow_test_roots,
        registry_path_for_tests=registry_path_for_tests,
        reader=reader,
    )
    root = roots.get(root_key_id)
    if root is None:
        raise ReleaseTrustError("TRUST_ROOT_UNKNOWN", "trust policy root is not installed by the application")
    signature = _decode_base64(
        signature_document.get("signature"), expected=64, code="TRUST_POLICY_SIGNATURE_INVALID", field="trust policy signature"
    )
    try:
        root["public_key"].verify(signature, raw_policy)
    except Exception as exc:
        raise ReleaseTrustError("TRUST_POLICY_SIGNATURE_INVALID", "trust policy signature is invalid") from exc

    policy = _strict_json(raw_policy, code="TRUST_POLICY_INVALID", label="release trust policy")
    if not isinstance(policy, dict) or set(policy) != {
        "schema", "policy_id", "root_key_id", "test_only", "created_at_utc", "validity", "allowed_leaf_keys", "revoked_key_ids"
    }:
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy fields are invalid")
    if policy.get("schema") != TRUST_POLICY_SCHEMA or policy.get("root_key_id") != root_key_id:
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy schema or root binding is invalid")
    policy_id = _safe_key_id(policy.get("policy_id"), code="TRUST_POLICY_INVALID")
    test_only = policy.get("test_only")
    if (
        not isinstance(test_only, bool)
        or test_only is not root["test_only"]
        or policy_id.startswith("test-") != test_only
    ):
        raise ReleaseTrustError("TRUST_POLICY_TEST_BOUNDARY", "test and production trust roots/policies cannot authorize each other")
    if test_only and not allow_test_roots:
        raise ReleaseTrustError("TRUST_POLICY_TEST_BOUNDARY", "production bootstrap cannot use a test trust policy")
    created = _utc_timestamp(policy.get("created_at_utc"), field="trust policy created_at_utc", code="TRUST_POLICY_INVALID")
    validity = policy.get("validity")
    if not isinstance(validity, dict) or set(validity) != {"not_before", "not_after"}:
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy validity fields are invalid")
    not_before = _utc_timestamp(validity.get("not_before"), field="trust policy validity.not_before", code="TRUST_POLICY_INVALID")
    not_after = _utc_timestamp(validity.get("not_after"), field="trust policy validity.not_after", code="TRUST_POLICY_INVALID")
    if not_after <= not_before or created > not_after:
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy timestamps are inconsistent")
    if created > now + timedelta(minutes=5):
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy creation time is in the future")
    if now < not_before or now > not_after:
        raise ReleaseTrustError("TRUST_POLICY_OUTSIDE_VALIDITY", "release trust policy is outside its validity window")

    raw_revoked = policy.get("revoked_key_ids")
    if not isinstance(raw_revoked, list):
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy revoked_key_ids must be a list")
    revoked = tuple(_safe_key_id(value, code="TRUST_POLICY_INVALID") for value in raw_revoked)
    if len(set(revoked)) != len(revoked):
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy contains duplicate revoked key ids")
    allowed_schemas = {contract["schema"] for contract in MANIFEST_CONTRACTS.values()}
    declarations = policy.get("allowed_leaf_keys")
    if not isinstance(declarations, list) or not declarations:
        raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy must authorize at least one leaf key")
    allowed: dict[str, dict[str, Any]] = {}
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {"key_id", "public_key_sha256", "purposes", "schemas"}:
            raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy leaf authorization fields are invalid")
        key_id = _safe_key_id(declaration.get("key_id"), code="TRUST_POLICY_INVALID")
        if key_id in allowed:
            raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy contains a duplicate leaf key id")
        if key_id.startswith("test-") != test_only:
            raise ReleaseTrustError("TRUST_POLICY_TEST_BOUNDARY", "test policy leaf identities must remain test-only")
        purposes = declaration.get("purposes")
        schemas = declaration.get("schemas")
        if (
            not isinstance(purposes, list)
            or not purposes
            or any(not isinstance(item, str) or item not in ALLOWED_PURPOSES for item in purposes)
            or len(set(purposes)) != len(purposes)
            or not isinstance(schemas, list)
            or not schemas
            or any(not isinstance(item, str) or item not in allowed_schemas for item in schemas)
            or len(set(schemas)) != len(schemas)
        ):
            raise ReleaseTrustError("TRUST_POLICY_INVALID", "trust policy leaf purpose/schema grants are invalid")
        allowed[key_id] = {
            "public_key_sha256": _fingerprint(
                declaration.get("public_key_sha256"), field="trust policy leaf fingerprint", code="TRUST_POLICY_INVALID"
            ),
            "purposes": frozenset(purposes),
            "schemas": frozenset(schemas),
        }
    return {
        "policy_id": policy_id,
        "root_key_id": root_key_id,
        "root_public_key_sha256": root["public_key_sha256"],
        "registry_sha256": registry_sha256,
        "policy_sha256": _sha256(raw_policy),
        "policy_signature_sha256": _sha256(raw_signature),
        "not_before": not_before,
        "not_after": not_after,
        "test_only": test_only,
        "allowed_leaf_keys": allowed,
        "revoked_key_ids": frozenset(revoked),
    }


def _load_receipt(
    *,
    public_key_path: Path,
    trust_receipt_path: Path,
    contract: Mapping[str, str],
    manifest_value: Mapping[str, Any],
    operation: str,
    reader: ReadFile | None,
    now: datetime,
) -> dict[str, Any]:
    raw_receipt = _read(reader, trust_receipt_path, "public trust receipt", 256 * 1024)
    raw_pem = _read(reader, public_key_path, "public key", 64 * 1024)
    receipt = _strict_json(raw_receipt, code="TRUST_RECEIPT_INVALID", label="public trust receipt")
    expected = {
        "schema", "algorithm", "key_id", "purpose", "status", "test_only", "created_at_utc", "fingerprint",
        "public_key", "rotation", "validity", "application_trust", "self_signature",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise ReleaseTrustError("TRUST_RECEIPT_INVALID", "public trust receipt fields are invalid")
    if receipt.get("schema") != TRUST_RECEIPT_SCHEMA or receipt.get("algorithm") != "ed25519":
        raise ReleaseTrustError("TRUST_RECEIPT_INVALID", "public trust receipt schema/algorithm is invalid")
    key_id = _safe_key_id(receipt.get("key_id"), code="TRUST_RECEIPT_INVALID")
    purpose = receipt.get("purpose")
    if not isinstance(purpose, str) or purpose not in PURPOSE_SCOPES or contract["scope"] not in PURPOSE_SCOPES[purpose]:
        raise ReleaseTrustError("KEY_PURPOSE_MISMATCH", "release key purpose does not authorize this manifest schema")
    test_only = receipt.get("test_only")
    if (
        not isinstance(test_only, bool)
        or receipt.get("status") != ("test-only" if test_only else "active")
        or key_id.startswith("test-") != test_only
    ):
        raise ReleaseTrustError("TRUST_STATUS_INVALID", "trust receipt status/test_only fields are inconsistent or revoked")
    if test_only and not _manifest_is_test(manifest_value, contract["scope"]):
        raise ReleaseTrustError("TEST_KEY_PRODUCTION_FORBIDDEN", "test-only release keys cannot validate production identities")

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = serialization.load_pem_public_key(raw_pem)
    except (TypeError, ValueError) as exc:
        raise ReleaseTrustError("PUBLIC_KEY_INVALID", "public key is not a PEM Ed25519 key") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise ReleaseTrustError("PUBLIC_KEY_INVALID", "public key is not Ed25519")
    raw_public = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public_fingerprint = _sha256(raw_public)
    if receipt.get("fingerprint") != {
        "algorithm": "sha256", "scope": "raw-ed25519-public-key", "value": public_fingerprint
    }:
        raise ReleaseTrustError("PUBLIC_FINGERPRINT_MISMATCH", "trust receipt public-key fingerprint is invalid")
    public_doc = receipt.get("public_key")
    if (
        not isinstance(public_doc, dict)
        or set(public_doc) != {"encoding", "value", "pem_filename", "pem_sha256"}
        or public_doc.get("encoding") != "base64-raw-ed25519"
        or _decode_base64(public_doc.get("value"), expected=32, code="TRUST_RECEIPT_INVALID", field="receipt public key") != raw_public
        or public_doc.get("pem_filename") != public_key_path.name
        or public_doc.get("pem_sha256") != _sha256(raw_pem)
    ):
        raise ReleaseTrustError("TRUST_RECEIPT_INVALID", "trust receipt public-key binding is invalid")

    rotation = receipt.get("rotation")
    if not isinstance(rotation, dict) or set(rotation) != {
        "starts_on", "rotate_after", "overlap_until", "overlap_days", "predecessor_key_id"
    }:
        raise ReleaseTrustError("ROTATION_INVALID", "trust receipt rotation fields are invalid")
    starts = _iso_date(rotation.get("starts_on"), field="starts_on")
    rotate = _iso_date(rotation.get("rotate_after"), field="rotate_after")
    overlap = _iso_date(rotation.get("overlap_until"), field="overlap_until")
    overlap_days = rotation.get("overlap_days")
    if (
        isinstance(overlap_days, bool)
        or not isinstance(overlap_days, int)
        or not 1 <= overlap_days <= 180
        or rotate <= starts
        or overlap != rotate + timedelta(days=overlap_days)
    ):
        raise ReleaseTrustError("ROTATION_INVALID", "trust receipt rotation window is not self-consistent")
    predecessor = rotation.get("predecessor_key_id")
    if predecessor is not None:
        _safe_key_id(predecessor, code="ROTATION_INVALID")
    expected_validity = {
        "signing": {"not_before": starts.isoformat(), "not_after": rotate.isoformat()},
        "verification": {"not_before": starts.isoformat(), "not_after": overlap.isoformat()},
    }
    if receipt.get("validity") != expected_validity:
        raise ReleaseTrustError("VALIDITY_INVALID", "trust receipt validity does not match its rotation window")
    not_after_date = rotate if operation == "sign" else overlap
    if now.date() < starts or now.date() > not_after_date:
        raise ReleaseTrustError("KEY_OUTSIDE_VALIDITY", "release key is outside the allowed operation window")
    created = _utc_timestamp(receipt.get("created_at_utc"), field="trust receipt created_at_utc", code="TRUST_RECEIPT_INVALID")
    if created > now + timedelta(minutes=5):
        raise ReleaseTrustError("TRUST_RECEIPT_INVALID", "trust receipt creation time is in the future")
    expected_catalog = {
        "key_id": key_id,
        "public_key": base64.b64encode(raw_public).decode("ascii"),
        "public_key_sha256": public_fingerprint,
    }
    if receipt.get("application_trust") != {"private_key_allowed": False, "catalog_fields": expected_catalog}:
        raise ReleaseTrustError("TRUST_RECEIPT_INVALID", "application trust binding is not public-key-only")
    self_signature = receipt.get("self_signature")
    payload = dict(receipt)
    payload.pop("self_signature", None)
    payload_bytes = _canonical_json_bytes(payload)
    if (
        not isinstance(self_signature, dict)
        or set(self_signature) != {"algorithm", "key_id", "payload_sha256", "signature"}
        or self_signature.get("algorithm") != "ed25519"
        or self_signature.get("key_id") != key_id
        or self_signature.get("payload_sha256") != _sha256(payload_bytes)
    ):
        raise ReleaseTrustError("TRUST_RECEIPT_SIGNATURE_INVALID", "trust receipt self-signature metadata is invalid")
    receipt_signature = _decode_base64(
        self_signature.get("signature"), expected=64, code="TRUST_RECEIPT_SIGNATURE_INVALID", field="trust receipt self-signature"
    )
    try:
        public_key.verify(receipt_signature, payload_bytes)
    except Exception as exc:
        raise ReleaseTrustError("TRUST_RECEIPT_SIGNATURE_INVALID", "trust receipt self-signature is invalid") from exc
    return {
        "receipt": receipt,
        "public_key": public_key,
        "raw_public": raw_public,
        "fingerprint": public_fingerprint,
        "public_pem_sha256": _sha256(raw_pem),
        "receipt_sha256": _sha256(raw_receipt),
    }


def verify_release_trust_chain(
    *,
    manifest_name: str,
    manifest_value: Mapping[str, Any],
    public_key_path: Path,
    trust_receipt_path: Path,
    trust_policy_path: Path,
    trust_policy_signature_path: Path,
    operation: str = "verify",
    trusted_fingerprint: str = "",
    expected_key_id: str = "",
    revoked_key_ids: Iterable[str] = (),
    _allow_test_roots: bool = False,
    _registry_path_for_tests: Path | None = None,
    _read_file: ReadFile | None = None,
    _now_for_tests: datetime | None = None,
) -> VerifiedReleaseTrust:
    """Verify fixed registry -> raw policy signature -> receipt/public leaf.

    ``_allow_test_roots``, ``_registry_path_for_tests`` and ``_now_for_tests``
    are intentionally underscored.  Production bootstrap never forwards
    environment or descriptor data to them.
    """

    contract = MANIFEST_CONTRACTS.get(manifest_name)
    if contract is None:
        raise ReleaseTrustError("MANIFEST_NAME_INVALID", "manifest filename is not a supported release contract")
    if not isinstance(manifest_value, Mapping) or manifest_value.get("schema") != contract["schema"]:
        raise ReleaseTrustError("MANIFEST_SCHEMA_INVALID", "manifest schema does not match its filename")
    if operation not in {"sign", "verify"}:
        raise ReleaseTrustError("TRUST_OPERATION_INVALID", "release trust operation is invalid")
    now = _now_for_tests or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ReleaseTrustError("TRUST_TIME_INVALID", "verification time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    public_path = Path(os.path.abspath(public_key_path))
    receipt_path = Path(os.path.abspath(trust_receipt_path))
    policy_path = Path(os.path.abspath(trust_policy_path))
    policy_signature_path = Path(os.path.abspath(trust_policy_signature_path))
    policy = _load_policy(
        trust_policy_path=policy_path,
        trust_policy_signature_path=policy_signature_path,
        allow_test_roots=bool(_allow_test_roots),
        registry_path_for_tests=_registry_path_for_tests,
        reader=_read_file,
        now=now,
    )
    leaf = _load_receipt(
        public_key_path=public_path,
        trust_receipt_path=receipt_path,
        contract=contract,
        manifest_value=manifest_value,
        operation=operation,
        reader=_read_file,
        now=now,
    )
    receipt = leaf["receipt"]
    key_id = receipt["key_id"]
    supplemental_revoked = frozenset(
        _safe_key_id(value, code="TRUST_CONSTRAINT_INVALID") for value in revoked_key_ids
    )
    if key_id in policy["revoked_key_ids"] | supplemental_revoked:
        raise ReleaseTrustError("TRUST_POLICY_REVOKED", "release leaf key is revoked")
    authorization = policy["allowed_leaf_keys"].get(key_id)
    if authorization is None:
        raise ReleaseTrustError("TRUST_POLICY_LEAF_UNKNOWN", "release leaf key is not authorized by the signed policy")
    if not hmac.compare_digest(leaf["fingerprint"], authorization["public_key_sha256"]):
        raise ReleaseTrustError("TRUST_POLICY_LEAF_MISMATCH", "release leaf fingerprint does not match the signed policy")
    if receipt["purpose"] not in authorization["purposes"]:
        raise ReleaseTrustError("TRUST_POLICY_PURPOSE_FORBIDDEN", "signed policy does not grant the receipt purpose")
    if contract["schema"] not in authorization["schemas"]:
        raise ReleaseTrustError("TRUST_POLICY_SCHEMA_FORBIDDEN", "signed policy does not grant the manifest schema")
    if policy["test_only"] != receipt["test_only"]:
        raise ReleaseTrustError("TRUST_POLICY_TEST_BOUNDARY", "policy and leaf test boundaries differ")
    if receipt["test_only"] and not _allow_test_roots:
        raise ReleaseTrustError("TRUST_POLICY_TEST_BOUNDARY", "production bootstrap cannot use a test leaf")
    if expected_key_id and key_id != _safe_key_id(expected_key_id, code="TRUST_CONSTRAINT_INVALID"):
        raise ReleaseTrustError("TRUST_CONSTRAINT_KEY_ID_MISMATCH", "leaf key id violates the supplemental constraint")
    if trusted_fingerprint:
        constrained = _fingerprint(trusted_fingerprint, field="supplemental trusted fingerprint", code="TRUST_CONSTRAINT_INVALID")
        if not hmac.compare_digest(leaf["fingerprint"], constrained):
            raise ReleaseTrustError("TRUST_CONSTRAINT_FINGERPRINT_MISMATCH", "leaf fingerprint violates the supplemental constraint")
    return VerifiedReleaseTrust(
        _TRUST_TOKEN,
        root_key_id=policy["root_key_id"],
        root_public_key_sha256=policy["root_public_key_sha256"],
        registry_sha256=policy["registry_sha256"],
        policy_id=policy["policy_id"],
        policy_sha256=policy["policy_sha256"],
        policy_signature_sha256=policy["policy_signature_sha256"],
        policy_not_before=policy["not_before"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        policy_not_after=policy["not_after"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        test_only=policy["test_only"],
        leaf_key_id=key_id,
        leaf_public_key_sha256=leaf["fingerprint"],
        leaf_public_pem_sha256=leaf["public_pem_sha256"],
        leaf_receipt_sha256=leaf["receipt_sha256"],
        purpose=receipt["purpose"],
        manifest_schema=contract["schema"],
        raw_public_key=leaf["raw_public"],
        sources=_TrustSources(
            public_key_path=public_path,
            trust_receipt_path=receipt_path,
            trust_policy_path=policy_path,
            trust_policy_signature_path=policy_signature_path,
            manifest_name=manifest_name,
            manifest_value=dict(manifest_value),
            operation=operation,
            allow_test_roots=bool(_allow_test_roots),
            registry_path_for_tests=Path(_registry_path_for_tests) if _registry_path_for_tests is not None else None,
        ),
    )


def reverify_release_trust(trust: VerifiedReleaseTrust) -> VerifiedReleaseTrust:
    """Re-read the entire fixed chain and require byte-identical trust binding."""

    if not isinstance(trust, VerifiedReleaseTrust) or not trust.is_valid():
        raise ReleaseTrustError("TRUST_BINDING_INVALID", "a verified release trust receipt is required")
    source = trust._sources
    current = verify_release_trust_chain(
        manifest_name=source.manifest_name,
        manifest_value=source.manifest_value,
        public_key_path=source.public_key_path,
        trust_receipt_path=source.trust_receipt_path,
        trust_policy_path=source.trust_policy_path,
        trust_policy_signature_path=source.trust_policy_signature_path,
        operation=source.operation,
        _allow_test_roots=source.allow_test_roots,
        _registry_path_for_tests=source.registry_path_for_tests,
    )
    if not hmac.compare_digest(current.trust_binding_sha256, trust.trust_binding_sha256):
        raise ReleaseTrustError("TRUST_BINDING_CHANGED", "release root, policy, leaf, public key or receipt changed")
    return current


def reverify_engine_release_binding(binding: ReleaseTrustBinding) -> VerifiedReleaseTrust:
    """Revalidate policy state and fixed catalog/archive digests for a launch."""

    if not isinstance(binding, ReleaseTrustBinding) or not binding.is_valid():
        raise ReleaseTrustError("TRUST_BINDING_INVALID", "a complete release trust binding is required")
    current = reverify_release_trust(binding.trust)
    catalog = _secure_read_file(binding.catalog_path, "engine release catalog", 1024 * 1024)
    if len(catalog) != binding.catalog_bytes or not hmac.compare_digest(_sha256(catalog), binding.catalog_sha256):
        raise ReleaseTrustError("TRUST_CATALOG_CHANGED", "engine release catalog changed after registration")
    # The archive is not executed, but its digest is part of the registration
    # receipt. Rehash it for every new child generation to prevent a stale
    # descriptor/capability from silently surviving release replacement.
    _archive_bytes, archive_sha256 = secure_hash_release_file(
        binding.archive_path,
        label="engine release archive",
        expected_bytes=binding.archive_bytes,
    )
    if not hmac.compare_digest(archive_sha256, binding.archive_sha256):
        raise ReleaseTrustError("TRUST_ARCHIVE_CHANGED", "engine archive changed after registration")
    return current
