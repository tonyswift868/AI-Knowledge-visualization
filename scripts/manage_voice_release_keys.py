#!/usr/bin/env python3
"""Plan, create, and use MentorFlow Ed25519 release keys safely on Windows.

The default mode is a non-mutating plan.  Key creation requires the explicit
``--generate`` switch and a brand-new absolute E: directory outside the source
tree and temporary directories.  The application never needs this tool's
private key: only the generated public PEM/raw-key trust receipt belongs in an
application trust configuration.  Verification trust starts from the fixed,
application-installed release-root registry and a root-signed leaf policy.

This module intentionally does not download anything, contact a network
service, or create a production key during tests.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Iterator


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app import release_trust_policy as release_trust

REQUIRED_PRIVATE_DRIVE = "e:"
TRUST_RECEIPT_SCHEMA = release_trust.TRUST_RECEIPT_SCHEMA
TRUST_ROOTS_SCHEMA = release_trust.TRUST_ROOTS_SCHEMA
TRUST_POLICY_SCHEMA = release_trust.TRUST_POLICY_SCHEMA
KEY_INDEX_SCHEMA = "mentorflow.voice-release-protected-key-index.v1"
KEY_INDEX_BASENAME = ".mentorflow-key-index.json"
RELEASE_TRUST_ROOTS_PATH = release_trust.RELEASE_TRUST_ROOTS_PATH
ALLOWED_PURPOSES = release_trust.ALLOWED_PURPOSES
MANIFEST_CONTRACTS = release_trust.MANIFEST_CONTRACTS
PURPOSE_SCOPES = release_trust.PURPOSE_SCOPES
KEY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
ACL_SIDS = ("S-1-5-18", "S-1-5-32-544")  # SYSTEM, built-in Administrators.
WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
WINDOWS_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "clock$",
        "conin$",
        "conout$",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
DELETE_ACCESS = 0x00010000
FILE_READ_ATTRIBUTES = 0x00000080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
CREATE_NEW = 1
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_DISPOSITION_INFO_CLASS = 4
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


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


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOL)]


@dataclass(frozen=True)
class FileIdentity:
    volume_serial: int
    file_id: str
    number_of_links: int
    attributes: int
    size: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "volume_serial": f"{self.volume_serial:08x}",
            "file_id": self.file_id,
            "number_of_links": self.number_of_links,
            "attributes": self.attributes,
            "size": self.size,
        }


@dataclass(frozen=True)
class CreatedObject:
    path: Path
    identity: FileIdentity
    directory: bool


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
    _KERNEL32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _KERNEL32.WriteFile.restype = wintypes.BOOL
    _KERNEL32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _KERNEL32.FlushFileBuffers.restype = wintypes.BOOL
    _KERNEL32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _KERNEL32.SetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
else:  # pragma: no cover - generation is intentionally Windows-only.
    _KERNEL32 = None


class KeyToolError(RuntimeError):
    """Fail-closed error carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _win_error(code: str, message: str) -> KeyToolError:
    error = ctypes.get_last_error() if os.name == "nt" else 0
    return KeyToolError(code, f"{message} (winerror={error})")


def _create_file_handle(
    path: Path,
    *,
    desired_access: int,
    share_mode: int,
    creation: int,
    directory: bool,
    write_through: bool = False,
) -> int:
    if os.name != "nt" or _KERNEL32 is None:
        raise KeyToolError("WINDOWS_REQUIRED", "fixed-handle release-key operations require Windows")
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    elif creation == CREATE_NEW:
        flags |= FILE_ATTRIBUTE_NORMAL
    if write_through:
        flags |= FILE_FLAG_WRITE_THROUGH
    handle = _KERNEL32.CreateFileW(
        str(path),
        desired_access,
        share_mode,
        None,
        creation,
        flags,
        None,
    )
    numeric = int(handle) if handle is not None else INVALID_HANDLE_VALUE
    if numeric == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if creation == CREATE_NEW and error in {80, 183}:
            raise KeyToolError("OUTPUT_EXISTS", "output already exists; overwrite is forbidden")
        raise _win_error("HANDLE_OPEN_FAILED", "Windows could not open the requested object safely")
    return numeric


def _close_handle(handle: int) -> None:
    if _KERNEL32 is not None and handle not in {0, INVALID_HANDLE_VALUE}:
        _KERNEL32.CloseHandle(handle)


def _handle_identity(handle: int) -> FileIdentity:
    if _KERNEL32 is None:
        raise KeyToolError("WINDOWS_REQUIRED", "file identity requires Windows")
    information = _ByHandleFileInformation()
    if not _KERNEL32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise _win_error("FILE_IDENTITY_FAILED", "Windows could not read VolumeSerial/FileId")
    size = (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow)
    file_id = f"{int(information.nFileIndexHigh):08x}{int(information.nFileIndexLow):08x}"
    return FileIdentity(
        volume_serial=int(information.dwVolumeSerialNumber),
        file_id=file_id,
        number_of_links=int(information.nNumberOfLinks),
        attributes=int(information.dwFileAttributes),
        size=size,
    )


def _validate_handle_identity(identity: FileIdentity, *, directory: bool, field: str) -> None:
    if identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise KeyToolError("REPARSE_FORBIDDEN", f"{field} is a reparse point")
    is_directory = bool(identity.attributes & FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != directory:
        raise KeyToolError("FILE_NOT_REGULAR", f"{field} has the wrong object type")
    if not directory and identity.number_of_links != 1:
        raise KeyToolError("HARDLINK_FORBIDDEN", f"{field} cannot be a hardlink")


def _identity_from_path(path: Path, *, directory: bool, field: str) -> FileIdentity:
    handle = _create_file_handle(
        path,
        desired_access=FILE_READ_ATTRIBUTES,
        share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        creation=OPEN_EXISTING,
        directory=directory,
    )
    try:
        identity = _handle_identity(handle)
        _validate_handle_identity(identity, directory=directory, field=field)
        return identity
    finally:
        _close_handle(handle)


def _same_identity(first: FileIdentity, second: FileIdentity) -> bool:
    return (
        first.volume_serial == second.volume_serial
        and first.file_id == second.file_id
        and first.attributes == second.attributes
        and first.number_of_links == second.number_of_links
    )


@contextmanager
def _hold_identity(path: Path, *, directory: bool, field: str) -> Iterator[tuple[int, FileIdentity]]:
    """Hold an object without DELETE sharing and verify its path stays bound."""

    handle = _create_file_handle(
        path,
        desired_access=FILE_READ_ATTRIBUTES,
        share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
        creation=OPEN_EXISTING,
        directory=directory,
    )
    try:
        identity = _handle_identity(handle)
        _validate_handle_identity(identity, directory=directory, field=field)
        yield handle, identity
        if not _same_identity(identity, _handle_identity(handle)):
            raise KeyToolError("FILE_IDENTITY_CHANGED", f"{field} changed while its handle was held")
        rebound = _identity_from_path(path, directory=directory, field=field)
        if not _same_identity(identity, rebound):
            raise KeyToolError("FILE_IDENTITY_CHANGED", f"{field} path no longer names the held object")
    finally:
        _close_handle(handle)


def _read_held_file(handle: int, *, maximum_bytes: int | None, field: str) -> bytes:
    if _KERNEL32 is None:
        raise KeyToolError("WINDOWS_REQUIRED", "fixed-handle reads require Windows")
    chunks: list[bytes] = []
    total = 0
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD()
        if not _KERNEL32.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
            raise _win_error("FILE_READ_FAILED", f"{field} could not be read from its fixed handle")
        count = int(read.value)
        if count == 0:
            break
        total += count
        if maximum_bytes is not None and total > maximum_bytes:
            raise KeyToolError("FILE_SIZE_INVALID", f"{field} exceeds the allowed size")
        chunks.append(buffer.raw[:count])
    return b"".join(chunks)


def _identity_from_dict(value: Any, *, field: str) -> FileIdentity:
    if not isinstance(value, dict) or set(value) != {
        "volume_serial",
        "file_id",
        "number_of_links",
        "attributes",
        "size",
    }:
        raise KeyToolError("KEY_INDEX_INVALID", f"{field} identity fields are invalid")
    try:
        identity = FileIdentity(
            volume_serial=int(str(value["volume_serial"]), 16),
            file_id=str(value["file_id"]),
            number_of_links=int(value["number_of_links"]),
            attributes=int(value["attributes"]),
            size=int(value["size"]),
        )
    except (TypeError, ValueError) as exc:
        raise KeyToolError("KEY_INDEX_INVALID", f"{field} identity encoding is invalid") from exc
    if not re.fullmatch(r"[0-9a-f]{16}", identity.file_id) or identity.size < 0:
        raise KeyToolError("KEY_INDEX_INVALID", f"{field} FileId/size is invalid")
    return identity


def _delete_if_identity(path: Path, expected: FileIdentity, *, directory: bool) -> None:
    """Delete the exact recorded object by handle; never delete a replacement."""

    handle = _create_file_handle(
        path,
        desired_access=DELETE_ACCESS | FILE_READ_ATTRIBUTES,
        share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        creation=OPEN_EXISTING,
        directory=directory,
    )
    try:
        actual = _handle_identity(handle)
        _validate_handle_identity(actual, directory=directory, field="cleanup target")
        if not _same_identity(actual, expected):
            raise KeyToolError("CLEANUP_IDENTITY_MISMATCH", "cleanup target was replaced; replacement was not deleted")
        disposition = _FileDispositionInformation(True)
        if _KERNEL32 is None or not _KERNEL32.SetFileInformationByHandle(
            handle,
            FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise _win_error("CLEANUP_FAILED", "Windows could not delete the exact recorded object")
    finally:
        _close_handle(handle)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _strict_json(raw: bytes, *, code: str, label: str) -> Any:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise KeyToolError(code, f"{label} is not strict UTF-8 JSON") from exc


def _safe_key_id(value: str) -> str:
    if not KEY_ID.fullmatch(value or ""):
        raise KeyToolError("KEY_ID_INVALID", "key id must be a stable lowercase token")
    return value


def _sha256_fingerprint(value: Any, *, field: str, code: str) -> str:
    text = str(value or "")
    canonical = text.casefold()
    if text != text.strip() or not HEX64.fullmatch(canonical):
        raise KeyToolError(code, f"{field} must be a canonical SHA256 value")
    return canonical


def _parse_iso_date(value: str, *, field: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise KeyToolError("ROTATION_INVALID", f"{field} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise KeyToolError("ROTATION_INVALID", f"{field} must use canonical YYYY-MM-DD")
    return parsed


def _parse_canonical_utc_timestamp(value: Any, *, field: str, code: str) -> datetime:
    text = str(value or "")
    if not CANONICAL_UTC_TIMESTAMP.fullmatch(text):
        raise KeyToolError(code, f"{field} must use canonical YYYY-MM-DDTHH:MM:SSZ UTC")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise KeyToolError(code, f"{field} is not a valid UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != text:
        raise KeyToolError(code, f"{field} is not canonical UTC")
    return parsed


def _normalised_drive_path(value: Path | str, *, field: str) -> Path:
    """Accept only exact, local-drive absolute Windows paths.

    This lexical check happens before any normalisation so traversal, ADS,
    duplicate separators, UNC paths, and Windows device aliases cannot be
    hidden by ``Path.resolve``.
    """

    text = str(value)
    if not text or text != text.strip() or "\x00" in text:
        raise KeyToolError("PATH_INVALID", f"{field} must be an exact absolute Windows path")
    windows = text.replace("/", "\\")
    match = re.fullmatch(r"([A-Za-z]):\\(.+)", windows)
    if match is None or windows.startswith("\\\\"):
        raise KeyToolError("PATH_NOT_ABSOLUTE", f"{field} must be an absolute local-drive path")
    tail = match.group(2)
    if "\\\\" in tail or tail.endswith("\\"):
        raise KeyToolError("PATH_INVALID", f"{field} contains an ambiguous separator")
    parts = tail.split("\\")
    for part in parts:
        device_stem = part.split(".", 1)[0].rstrip(" .").casefold()
        if (
            part in {"", ".", ".."}
            or part != part.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or any(character in WINDOWS_FORBIDDEN_CHARACTERS for character in part)
            or part.endswith((".", " "))
            or device_stem in WINDOWS_RESERVED_NAMES
        ):
            raise KeyToolError("PATH_UNSAFE", f"{field} contains traversal, ADS, or an unsafe Windows segment")
    canonical = Path(f"{match.group(1).upper()}:\\" + "\\".join(parts))
    if not PureWindowsPath(canonical).is_absolute():
        raise KeyToolError("PATH_NOT_ABSOLUTE", f"{field} must be absolute")
    return canonical


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_file_attributes", 0) or 0) & REPARSE_FLAG)


def _assert_existing_chain_safe(path: Path, *, field: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if not os.path.lexists(current):
            break
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise KeyToolError("PATH_INSPECTION_FAILED", f"cannot inspect {field}") from exc
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise KeyToolError("REPARSE_FORBIDDEN", f"{field} uses a symlink, junction, or reparse point")


def _same_or_child(path: Path, parent: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(parent))))
    except ValueError:
        return False
    return common == os.path.normcase(str(parent))


def _temporary_roots() -> tuple[Path, ...]:
    candidates = {tempfile.gettempdir()}
    candidates.update(value for name in ("TEMP", "TMP", "TMPDIR") if (value := os.getenv(name)))
    result = []
    for candidate in candidates:
        try:
            result.append(_normalised_drive_path(candidate, field="temporary directory"))
        except KeyToolError:
            continue
    return tuple(result)


def _validate_private_location(value: Path | str, *, must_exist: bool) -> Path:
    path = _normalised_drive_path(value, field="private key location")
    if path.drive.casefold() != REQUIRED_PRIVATE_DRIVE:
        raise KeyToolError("PRIVATE_KEY_DRIVE_FORBIDDEN", "private key material must be stored on E:")
    _assert_existing_chain_safe(path, field="private key location")
    if _same_or_child(path, REPOSITORY_ROOT):
        raise KeyToolError("PRIVATE_KEY_REPOSITORY_FORBIDDEN", "private key material cannot be stored in the repository")
    if any(_same_or_child(path, root) for root in _temporary_roots()):
        raise KeyToolError("PRIVATE_KEY_TEMP_FORBIDDEN", "private key material cannot be stored in a temporary directory")
    if must_exist:
        _ensure_plain_file(path, field="private key")
    return path


def _validate_private_root(value: Path | str, *, must_exist: bool) -> Path:
    root = _normalised_drive_path(value, field="protected key root")
    if root.drive.casefold() != REQUIRED_PRIVATE_DRIVE:
        raise KeyToolError("PRIVATE_KEY_DRIVE_FORBIDDEN", "protected key root must be stored on E:")
    _assert_existing_chain_safe(root, field="protected key root")
    if _same_or_child(root, REPOSITORY_ROOT):
        raise KeyToolError("PRIVATE_KEY_REPOSITORY_FORBIDDEN", "protected key root cannot be stored in the repository")
    if any(_same_or_child(root, temporary) for temporary in _temporary_roots()):
        raise KeyToolError("PRIVATE_KEY_TEMP_FORBIDDEN", "protected key root cannot be stored in a temporary directory")
    if must_exist:
        try:
            metadata = os.lstat(root)
        except OSError as exc:
            raise KeyToolError("KEY_ROOT_MISSING", "protected key root is missing") from exc
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
            raise KeyToolError("KEY_ROOT_INVALID", "protected key root must be a non-reparse directory")
        _identity_from_path(root, directory=True, field="protected key root")
    return root


def _ensure_plain_file(path: Path, *, field: str) -> os.stat_result:
    _assert_existing_chain_safe(path, field=field)
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise KeyToolError("FILE_MISSING", f"{field} is missing or unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode) or _is_reparse(metadata):
        raise KeyToolError("FILE_NOT_REGULAR", f"{field} must be a regular non-reparse file")
    if int(getattr(metadata, "st_nlink", 1) or 1) != 1:
        raise KeyToolError("HARDLINK_FORBIDDEN", f"{field} cannot be a hardlink")
    return metadata


def _secure_read(
    path: Path,
    *,
    field: str,
    maximum_bytes: int | None = None,
    expected_identity: FileIdentity | None = None,
) -> bytes:
    """Read through a fixed Windows handle that denies write/delete sharing."""

    handle = _create_file_handle(
        path,
        desired_access=GENERIC_READ | FILE_READ_ATTRIBUTES,
        share_mode=FILE_SHARE_READ,
        creation=OPEN_EXISTING,
        directory=False,
    )
    try:
        identity = _handle_identity(handle)
        _validate_handle_identity(identity, directory=False, field=field)
        if expected_identity is not None and (
            not _same_identity(expected_identity, identity) or expected_identity.size != identity.size
        ):
            raise KeyToolError(
                "KEY_INDEX_IDENTITY_MISMATCH",
                f"{field} no longer matches the VolumeSerial/FileId recorded by the protected index",
            )
        if maximum_bytes is not None and identity.size > maximum_bytes:
            raise KeyToolError("FILE_SIZE_INVALID", f"{field} exceeds the allowed size")
        raw = _read_held_file(handle, maximum_bytes=maximum_bytes, field=field)
        after = _handle_identity(handle)
        if not _same_identity(identity, after) or len(raw) != after.size:
            raise KeyToolError("FILE_IDENTITY_CHANGED", f"{field} changed while its handle was held")
        rebound = _identity_from_path(path, directory=False, field=field)
        if not _same_identity(identity, rebound):
            raise KeyToolError("FILE_IDENTITY_CHANGED", f"{field} path no longer names the held object")
        return raw
    finally:
        _close_handle(handle)


def _validate_new_output(path_value: Path | str, *, field: str) -> Path:
    path = _normalised_drive_path(path_value, field=field)
    _assert_existing_chain_safe(path.parent, field=f"{field} parent")
    if not path.parent.is_dir():
        raise KeyToolError("OUTPUT_PARENT_MISSING", f"{field} parent directory must already exist")
    if os.path.lexists(path):
        raise KeyToolError("OUTPUT_EXISTS", f"{field} already exists; overwrite is forbidden")
    return path


_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'PowerShell 7 is required' }
$target = $env:MENTORFLOW_KEY_ACL_TARGET
$apply = $env:MENTORFLOW_KEY_ACL_APPLY -eq '1'
$item = Get-Item -LiteralPath $target -Force
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$currentSid = $identity.User.Value
$allowed = @($currentSid, 'S-1-5-18', 'S-1-5-32-544')
if ($apply) {
  if ($item.PSIsContainer) {
    $security = [System.Security.AccessControl.DirectorySecurity]::new()
    $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    foreach ($sidText in $allowed) {
      $sid = [System.Security.Principal.SecurityIdentifier]::new($sidText)
      $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
      )
      [void]$security.AddAccessRule($rule)
    }
  } else {
    $security = [System.Security.AccessControl.FileSecurity]::new()
    foreach ($sidText in $allowed) {
      $sid = [System.Security.Principal.SecurityIdentifier]::new($sidText)
      $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
      )
      [void]$security.AddAccessRule($rule)
    }
  }
  $security.SetOwner($identity.User)
  $security.SetAccessRuleProtection($true, $false)
  Set-Acl -LiteralPath $target -AclObject $security
}
$acl = Get-Acl -LiteralPath $target
$rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
$full = [int64][System.Security.AccessControl.FileSystemRights]::FullControl
$seen = @{}
$bad = @()
$expectedInheritance = if ($item.PSIsContainer) {
  [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
} else {
  [System.Security.AccessControl.InheritanceFlags]::None
}
foreach ($rule in $rules) {
  $sid = $rule.IdentityReference.Value
  $seen[$sid] = $true
  $rights = [int64]$rule.FileSystemRights
  if (($allowed -notcontains $sid) -or
      ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) -or
      ($rights -ne $full) -or
      ($rule.InheritanceFlags -ne $expectedInheritance) -or
      ($rule.PropagationFlags -ne [System.Security.AccessControl.PropagationFlags]::None) -or
      $rule.IsInherited) {
    $bad += $sid
  }
}
$missing = @($allowed | Where-Object { -not $seen.ContainsKey($_) })
$owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
$ok = $acl.AreAccessRulesProtected -and ($owner -eq $currentSid) -and
      ($bad.Count -eq 0) -and ($missing.Count -eq 0) -and
      ($rules.Count -eq $allowed.Count)
[pscustomobject]@{
  ok = [bool]$ok
  owner = $owner
  current_user_sid = $currentSid
  protected = [bool]$acl.AreAccessRulesProtected
  principals = @($rules | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
  rule_count = $rules.Count
  expected_rights = $full
  expected_inheritance = [string]$expectedInheritance
  expected_propagation = 'None'
  bad = $bad
  missing = $missing
} | ConvertTo-Json -Compress -Depth 4
"""


def _powershell7() -> str:
    executable = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not executable:
        raise KeyToolError("POWERSHELL7_REQUIRED", "PowerShell 7 is required for Windows ACL enforcement")
    return executable


def _run_acl_subprocess(path: Path, *, apply: bool) -> dict[str, Any]:
    if os.name != "nt":
        raise KeyToolError("WINDOWS_REQUIRED", "release-key ACL enforcement is supported only on Windows")
    environment = os.environ.copy()
    environment["MENTORFLOW_KEY_ACL_TARGET"] = str(path)
    environment["MENTORFLOW_KEY_ACL_APPLY"] = "1" if apply else "0"
    try:
        completed = subprocess.run(
            [_powershell7(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _ACL_SCRIPT],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KeyToolError("ACL_TOOL_FAILED", "Windows ACL enforcement could not be executed") from exc
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "no diagnostic"
        diagnostic = diagnostic.replace(str(path), "<redacted-path>")
        diagnostic = re.sub(r"(?i)[a-z]:\\[^\r\n]+", "<redacted-path>", diagnostic)[:240]
        raise KeyToolError("ACL_TOOL_FAILED", f"Windows ACL enforcement failed closed: {diagnostic}")
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise KeyToolError("ACL_VERIFICATION_FAILED", "Windows ACL result was not machine-readable") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise KeyToolError("ACL_VERIFICATION_FAILED", "Windows ACL is not restricted to the approved principals")
    principals = set(result.get("principals") or [])
    if result.get("current_user_sid") not in principals or principals != {
        result.get("current_user_sid"),
        *ACL_SIDS,
    }:
        raise KeyToolError("ACL_VERIFICATION_FAILED", "Windows ACL principal set is not exact")
    if result.get("expected_rights") != 2032127 or result.get("expected_propagation") != "None":
        raise KeyToolError("ACL_VERIFICATION_FAILED", "Windows ACL rights/propagation are not exact")
    return result


def _apply_and_verify_acl(path: Path, *, apply: bool) -> dict[str, Any]:
    """Apply/inspect ACL while a no-DELETE-share handle fixes object identity.

    PowerShell cannot consume a HANDLE owned by this process.  Holding this
    handle prevents rename/delete replacement; VolumeSerial/FileId are checked
    both on the held handle and by reopening the lexical path before release.
    """

    _assert_existing_chain_safe(path, field="ACL target")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise KeyToolError("ACL_TARGET_MISSING", "ACL target is missing") from exc
    directory = stat.S_ISDIR(metadata.st_mode)
    if not directory and not stat.S_ISREG(metadata.st_mode):
        raise KeyToolError("ACL_TARGET_INVALID", "ACL target must be a regular file or directory")
    with _hold_identity(path, directory=directory, field="ACL target"):
        result = _run_acl_subprocess(path, apply=apply)
    expected_inheritance = "ContainerInherit, ObjectInherit" if directory else "None"
    if result.get("expected_inheritance") != expected_inheritance:
        raise KeyToolError("ACL_VERIFICATION_FAILED", "Windows ACL inheritance flags are not exact")
    return result


def _write_all(handle: int, content: bytes) -> None:
    if _KERNEL32 is None:
        raise KeyToolError("WINDOWS_REQUIRED", "fixed-handle writes require Windows")
    offset = 0
    while offset < len(content):
        chunk = content[offset : offset + 1024 * 1024]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not _KERNEL32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            raise _win_error("OUTPUT_WRITE_FAILED", "Windows could not write the fixed output handle")
        if int(written.value) <= 0:
            raise KeyToolError("OUTPUT_WRITE_FAILED", "Windows reported a zero-byte output write")
        offset += int(written.value)
    if not _KERNEL32.FlushFileBuffers(handle):
        raise _win_error("OUTPUT_FLUSH_FAILED", "Windows could not flush the fixed output handle")


def _exclusive_write(path: Path, content: bytes, *, protect_acl: bool) -> FileIdentity:
    """CREATE_NEW + fixed handle + FileId checks; never overwrite by path."""

    handle = _create_file_handle(
        path,
        desired_access=GENERIC_READ | GENERIC_WRITE | FILE_READ_ATTRIBUTES,
        share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
        creation=CREATE_NEW,
        directory=False,
        write_through=True,
    )
    identity: FileIdentity | None = None
    try:
        identity = _handle_identity(handle)
        _validate_handle_identity(identity, directory=False, field="new output")
        _write_all(handle, content)
        identity = _handle_identity(handle)
        if identity.size != len(content):
            raise KeyToolError("OUTPUT_WRITE_FAILED", "fixed output handle size does not match written bytes")
        if protect_acl:
            _apply_and_verify_acl(path, apply=True)
        if not _same_identity(identity, _handle_identity(handle)):
            raise KeyToolError("FILE_IDENTITY_CHANGED", "new output changed while its handle was held")
        rebound = _identity_from_path(path, directory=False, field="new output")
        if not _same_identity(identity, rebound) or rebound.size != identity.size:
            raise KeyToolError("FILE_IDENTITY_CHANGED", "new output path no longer names the created FileId")
        return identity
    except BaseException as write_exc:
        _close_handle(handle)
        handle = 0
        if identity is not None:
            try:
                _delete_if_identity(path, identity, directory=False)
            except KeyToolError as cleanup_exc:
                raise cleanup_exc from write_exc
        raise
    finally:
        _close_handle(handle)


def _cleanup_created(objects: Iterable[CreatedObject]) -> None:
    failures = []
    for created in reversed(tuple(objects)):
        try:
            _delete_if_identity(created.path, created.identity, directory=created.directory)
        except KeyToolError:
            failures.append(created.path.name)
    if failures:
        raise KeyToolError("CLEANUP_FAILED", "failed to remove an incomplete key generation output")


def _load_crypto() -> tuple[Any, Any]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise KeyToolError("CRYPTOGRAPHY_REQUIRED", "cryptography is required for Ed25519 operations") from exc
    return serialization, Ed25519PrivateKey


def _public_raw(public_key: Any) -> bytes:
    serialization, _ = _load_crypto()
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _load_public_key(path: Path, *, expected_identity: FileIdentity | None = None) -> Any:
    serialization, _ = _load_crypto()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    raw = _secure_read(
        path,
        field="public key",
        maximum_bytes=64 * 1024,
        expected_identity=expected_identity,
    )
    try:
        key = serialization.load_pem_public_key(raw)
    except (TypeError, ValueError) as exc:
        raise KeyToolError("PUBLIC_KEY_INVALID", "public key is not a PEM Ed25519 key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise KeyToolError("PUBLIC_KEY_INVALID", "public key is not Ed25519")
    return key


def _load_private_key(path: Path, *, expected_identity: FileIdentity | None = None) -> Any:
    serialization, Ed25519PrivateKey = _load_crypto()
    raw = _secure_read(
        path,
        field="private key",
        maximum_bytes=64 * 1024,
        expected_identity=expected_identity,
    )
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (TypeError, ValueError) as exc:
        raise KeyToolError("PRIVATE_KEY_INVALID", "private key must be an unencrypted PEM Ed25519 key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyToolError("PRIVATE_KEY_INVALID", "private key is not Ed25519")
    return key


def _rotation_metadata(*, starts_on: str, rotate_after: str, overlap_days: int, predecessor_key_id: str) -> dict[str, Any]:
    start = _parse_iso_date(starts_on, field="rotation start")
    rotate = _parse_iso_date(rotate_after, field="rotate-after")
    if rotate <= start:
        raise KeyToolError("ROTATION_INVALID", "rotate-after must be later than rotation start")
    if not 1 <= overlap_days <= 180:
        raise KeyToolError("ROTATION_INVALID", "overlap-days must be between 1 and 180")
    predecessor = _safe_key_id(predecessor_key_id) if predecessor_key_id else None
    return {
        "starts_on": start.isoformat(),
        "rotate_after": rotate.isoformat(),
        "overlap_until": (rotate + timedelta(days=overlap_days)).isoformat(),
        "overlap_days": overlap_days,
        "predecessor_key_id": predecessor,
    }


def _validity_from_rotation(rotation: dict[str, Any]) -> dict[str, Any]:
    return {
        "signing": {
            "not_before": rotation["starts_on"],
            "not_after": rotation["rotate_after"],
        },
        "verification": {
            "not_before": rotation["starts_on"],
            "not_after": rotation["overlap_until"],
        },
    }


def _validate_generation_request(
    *,
    output_root: Path | str,
    key_id: str,
    purpose: str,
    starts_on: str,
    rotate_after: str,
    overlap_days: int,
    predecessor_key_id: str = "",
    test_only: bool = False,
) -> dict[str, Any]:
    safe_id = _safe_key_id(key_id)
    if purpose not in ALLOWED_PURPOSES:
        raise KeyToolError("PURPOSE_INVALID", "purpose is not an approved MentorFlow release purpose")
    if bool(test_only) != safe_id.startswith("test-"):
        raise KeyToolError(
            "TEST_KEY_ID_INVALID",
            "test-only key ids must start with test-, and production key ids must not",
        )
    root = _validate_private_location(output_root, must_exist=False)
    if os.path.lexists(root):
        raise KeyToolError("OUTPUT_EXISTS", "output root already exists; overwrite is forbidden")
    if not root.parent.is_dir():
        raise KeyToolError("OUTPUT_PARENT_MISSING", "output root parent must already exist")
    rotation = _rotation_metadata(
        starts_on=starts_on,
        rotate_after=rotate_after,
        overlap_days=overlap_days,
        predecessor_key_id=predecessor_key_id,
    )
    return {"root": root, "key_id": safe_id, "purpose": purpose, "rotation": rotation, "test_only": bool(test_only)}


def plan_generation(**request: Any) -> dict[str, Any]:
    plan = _validate_generation_request(**request)
    key_id = plan["key_id"]
    return {
        "status": "plan",
        "action": "generate-ed25519-release-key",
        "writes": False,
        "key_id": key_id,
        "purpose": plan["purpose"],
        "rotation": plan["rotation"],
        "test_only": plan["test_only"],
        "public_outputs": [f"{key_id}.public.pem", f"{key_id}.public-trust.json"],
        "private_output": "withheld",
        "acl_principals": ["current-user", "SYSTEM", "Administrators"],
        "requires_explicit_generate": True,
    }


def generate_release_key(**request: Any) -> dict[str, Any]:
    plan = _validate_generation_request(**request)
    root: Path = plan["root"]
    key_id = plan["key_id"]
    private_path = root / f".private-{secrets.token_hex(24)}.pem"
    public_path = root / f"{key_id}.public.pem"
    receipt_path = root / f"{key_id}.public-trust.json"
    index_path = root / KEY_INDEX_BASENAME
    created: list[CreatedObject] = []
    root_handle = 0
    try:
        # Python 3.12 gives mode=0o700 special Windows ACL semantics that can
        # require SeSecurityPrivilege to replace.  The directory is still
        # empty here, so create it normally, immediately install the exact
        # protected DACL, verify it, and only then generate key material.
        os.mkdir(root)
        root_handle = _create_file_handle(
            root,
            desired_access=FILE_READ_ATTRIBUTES,
            share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
            creation=OPEN_EXISTING,
            directory=True,
        )
        root_identity = _handle_identity(root_handle)
        _validate_handle_identity(root_identity, directory=True, field="new protected key root")
        created.append(CreatedObject(root, root_identity, True))
        _apply_and_verify_acl(root, apply=True)
        if not _same_identity(root_identity, _handle_identity(root_handle)):
            raise KeyToolError("FILE_IDENTITY_CHANGED", "protected key root changed during ACL installation")

        serialization, Ed25519PrivateKey = _load_crypto()
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        raw_public = _public_raw(public_key)
        fingerprint = _sha256(raw_public)

        private_identity = _exclusive_write(private_path, private_pem, protect_acl=True)
        created.append(CreatedObject(private_path, private_identity, False))
        public_identity = _exclusive_write(public_path, public_pem, protect_acl=True)
        created.append(CreatedObject(public_path, public_identity, False))
        created_at = _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
        receipt_payload = {
            "schema": TRUST_RECEIPT_SCHEMA,
            "algorithm": "ed25519",
            "key_id": key_id,
            "purpose": plan["purpose"],
            "status": "test-only" if plan["test_only"] else "active",
            "test_only": plan["test_only"],
            "created_at_utc": created_at,
            "fingerprint": {
                "algorithm": "sha256",
                "scope": "raw-ed25519-public-key",
                "value": fingerprint,
            },
            "public_key": {
                "encoding": "base64-raw-ed25519",
                "value": base64.b64encode(raw_public).decode("ascii"),
                "pem_filename": public_path.name,
                "pem_sha256": _sha256(public_pem),
            },
            "rotation": plan["rotation"],
            "validity": _validity_from_rotation(plan["rotation"]),
            "application_trust": {
                "private_key_allowed": False,
                "catalog_fields": {
                    "key_id": key_id,
                    "public_key": base64.b64encode(raw_public).decode("ascii"),
                    "public_key_sha256": fingerprint,
                },
            },
        }
        receipt_payload_bytes = _canonical_json_bytes(receipt_payload)
        receipt = {
            **receipt_payload,
            "self_signature": {
                "algorithm": "ed25519",
                "key_id": key_id,
                "payload_sha256": _sha256(receipt_payload_bytes),
                "signature": base64.b64encode(private_key.sign(receipt_payload_bytes)).decode("ascii"),
            },
        }
        receipt_bytes = _json_bytes(receipt)
        receipt_identity = _exclusive_write(receipt_path, receipt_bytes, protect_acl=True)
        created.append(CreatedObject(receipt_path, receipt_identity, False))

        index = {
            "schema": KEY_INDEX_SCHEMA,
            "key_id": key_id,
            "private_key": {
                "basename": private_path.name,
                "identity": private_identity.public_dict(),
            },
            "public_key": {
                "basename": public_path.name,
                "identity": public_identity.public_dict(),
                "sha256": _sha256(public_pem),
            },
            "trust_receipt": {
                "basename": receipt_path.name,
                "identity": receipt_identity.public_dict(),
                "sha256": _sha256(receipt_bytes),
            },
        }
        index_identity = _exclusive_write(index_path, _json_bytes(index), protect_acl=True)
        created.append(CreatedObject(index_path, index_identity, False))
        _apply_and_verify_acl(root, apply=False)
        rebound_root = _identity_from_path(root, directory=True, field="protected key root")
        if not _same_identity(root_identity, _handle_identity(root_handle)) or not _same_identity(
            root_identity, rebound_root
        ):
            raise KeyToolError("FILE_IDENTITY_CHANGED", "protected key root path no longer names the created FileId")
        _close_handle(root_handle)
        root_handle = 0
        return {
            "status": "generated-test-key" if plan["test_only"] else "generated",
            "key_id": key_id,
            "purpose": plan["purpose"],
            "fingerprint_sha256": fingerprint,
            "public_key_basename": public_path.name,
            "public_trust_receipt_basename": receipt_path.name,
            "public_trust_receipt": receipt,
        }
    except BaseException as exc:
        _close_handle(root_handle)
        root_handle = 0
        if created:
            try:
                _cleanup_created(created)
            except KeyToolError as cleanup_exc:
                raise cleanup_exc from exc
        if isinstance(exc, KeyToolError):
            raise
        if isinstance(exc, FileExistsError):
            raise KeyToolError("OUTPUT_EXISTS", "output root already exists; overwrite is forbidden") from exc
        raise KeyToolError("KEY_GENERATION_FAILED", "release key generation failed closed") from exc
    finally:
        _close_handle(root_handle)


def _manifest_contract(path: Path, raw: bytes) -> tuple[dict[str, str], dict[str, Any]]:
    contract = MANIFEST_CONTRACTS.get(path.name)
    if contract is None:
        raise KeyToolError("MANIFEST_NAME_INVALID", "manifest filename is not a supported voice/engine/catalog contract")
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise KeyToolError("MANIFEST_SIZE_INVALID", "manifest size is outside the allowed range")
    value = _strict_json(raw, code="MANIFEST_INVALID", label="manifest")
    if not isinstance(value, dict) or value.get("schema") != contract["schema"]:
        raise KeyToolError("MANIFEST_SCHEMA_INVALID", "manifest schema does not match its filename")
    return contract, value


def _manifest_is_test(value: dict[str, Any], scope: str) -> bool:
    def marked(identifier: Any, version: Any) -> bool:
        return str(identifier or "").startswith("test-") and "test" in str(version or "").casefold()

    if scope == "engine":
        return marked(value.get("package_id"), value.get("version"))
    if scope == "voice":
        return marked(value.get("pack_id"), value.get("version"))
    if scope == "catalog":
        entries = value.get("engines") if isinstance(value.get("engines"), list) else value.get("packs")
        if not isinstance(entries, list) or not entries:
            return False
        return all(
            isinstance(item, dict)
            and marked(item.get("package_id") or item.get("pack_id"), item.get("version"))
            for item in entries
        )
    return False


def _safe_index_basename(value: Any, *, field: str) -> str:
    text = str(value or "")
    if not text or text in {".", ".."} or "/" in text or "\\" in text or text != text.strip():
        raise KeyToolError("KEY_INDEX_INVALID", f"{field} basename is unsafe")
    device_stem = text.split(".", 1)[0].rstrip(" .").casefold()
    if (
        any(ord(character) < 32 or ord(character) == 127 for character in text)
        or any(character in WINDOWS_FORBIDDEN_CHARACTERS for character in text)
        or text.endswith((".", " "))
        or device_stem in WINDOWS_RESERVED_NAMES
    ):
        raise KeyToolError("KEY_INDEX_INVALID", f"{field} basename is unsafe")
    return text


def _indexed_file(root: Path, declaration: Any, *, field: str) -> tuple[Path, FileIdentity, dict[str, Any]]:
    if not isinstance(declaration, dict) or not {"basename", "identity"}.issubset(declaration):
        raise KeyToolError("KEY_INDEX_INVALID", f"{field} declaration is invalid")
    basename = _safe_index_basename(declaration.get("basename"), field=field)
    path = root / basename
    expected = _identity_from_dict(declaration.get("identity"), field=field)
    actual = _identity_from_path(path, directory=False, field=field)
    if not _same_identity(expected, actual) or expected.size != actual.size:
        raise KeyToolError("KEY_INDEX_IDENTITY_MISMATCH", f"{field} VolumeSerial/FileId binding changed")
    _apply_and_verify_acl(path, apply=False)
    return path, actual, declaration


def _load_protected_key_index(root_value: Path | str) -> dict[str, Any]:
    root = _validate_private_root(root_value, must_exist=True)
    _apply_and_verify_acl(root, apply=False)
    index_path = root / KEY_INDEX_BASENAME
    _apply_and_verify_acl(index_path, apply=False)
    index = _strict_json(
        _secure_read(index_path, field="protected key index", maximum_bytes=128 * 1024),
        code="KEY_INDEX_INVALID",
        label="protected key index",
    )
    if not isinstance(index, dict) or set(index) != {
        "schema",
        "key_id",
        "private_key",
        "public_key",
        "trust_receipt",
    }:
        raise KeyToolError("KEY_INDEX_INVALID", "protected key index fields are invalid")
    if index.get("schema") != KEY_INDEX_SCHEMA:
        raise KeyToolError("KEY_INDEX_INVALID", "protected key index schema is invalid")
    key_id = _safe_key_id(str(index.get("key_id") or ""))
    private_path, private_identity, private_decl = _indexed_file(
        root, index.get("private_key"), field="indexed private key"
    )
    public_path, public_identity, public_decl = _indexed_file(
        root, index.get("public_key"), field="indexed public key"
    )
    receipt_path, receipt_identity, receipt_decl = _indexed_file(
        root, index.get("trust_receipt"), field="indexed trust receipt"
    )
    if set(private_decl) != {"basename", "identity"}:
        raise KeyToolError("KEY_INDEX_INVALID", "private key index fields are invalid")
    for declaration, path, identity, field in (
        (public_decl, public_path, public_identity, "indexed public key"),
        (receipt_decl, receipt_path, receipt_identity, "indexed trust receipt"),
    ):
        if set(declaration) != {"basename", "identity", "sha256"} or not HEX64.fullmatch(
            str(declaration.get("sha256") or "")
        ):
            raise KeyToolError("KEY_INDEX_INVALID", f"{field} hash declaration is invalid")
        if (
            _sha256(
                _secure_read(
                    path,
                    field=field,
                    maximum_bytes=256 * 1024,
                    expected_identity=identity,
                )
            )
            != declaration["sha256"]
        ):
            raise KeyToolError("KEY_INDEX_HASH_MISMATCH", f"{field} content no longer matches the protected index")
    return {
        "root": root,
        "key_id": key_id,
        "private_path": private_path,
        "private_identity": private_identity,
        "public_path": public_path,
        "public_identity": public_identity,
        "receipt_path": receipt_path,
        "receipt_identity": receipt_identity,
    }


def _decode_exact_base64(value: Any, *, expected_bytes: int, code: str, field: str) -> bytes:
    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except ValueError as exc:
        raise KeyToolError(code, f"{field} is not canonical base64") from exc
    if len(decoded) != expected_bytes:
        raise KeyToolError(code, f"{field} has the wrong byte length")
    return decoded


def _validate_public_trust(
    *,
    receipt_path: Path,
    public_path: Path,
    manifest_contract: dict[str, str],
    manifest_value: dict[str, Any],
    operation: str,
    receipt_identity: FileIdentity | None = None,
    public_identity: FileIdentity | None = None,
) -> tuple[dict[str, Any], Any, str]:
    receipt = _strict_json(
        _secure_read(
            receipt_path,
            field="public trust receipt",
            maximum_bytes=256 * 1024,
            expected_identity=receipt_identity,
        ),
        code="TRUST_RECEIPT_INVALID",
        label="public trust receipt",
    )
    expected_fields = {
        "schema",
        "algorithm",
        "key_id",
        "purpose",
        "status",
        "test_only",
        "created_at_utc",
        "fingerprint",
        "public_key",
        "rotation",
        "validity",
        "application_trust",
        "self_signature",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        raise KeyToolError("TRUST_RECEIPT_INVALID", "public trust receipt fields are invalid")
    if receipt.get("schema") != TRUST_RECEIPT_SCHEMA or receipt.get("algorithm") != "ed25519":
        raise KeyToolError("TRUST_RECEIPT_INVALID", "public trust receipt schema/algorithm is invalid")
    key_id = _safe_key_id(str(receipt.get("key_id") or ""))
    purpose = str(receipt.get("purpose") or "")
    if purpose not in PURPOSE_SCOPES or manifest_contract["scope"] not in PURPOSE_SCOPES[purpose]:
        raise KeyToolError("KEY_PURPOSE_MISMATCH", "release key purpose does not authorize this manifest schema")
    test_only = receipt.get("test_only")
    status = receipt.get("status")
    if (
        not isinstance(test_only, bool)
        or status != ("test-only" if test_only else "active")
        or test_only != key_id.startswith("test-")
    ):
        raise KeyToolError("TRUST_STATUS_INVALID", "trust receipt status/test_only fields are inconsistent or revoked")
    if test_only and not _manifest_is_test(manifest_value, manifest_contract["scope"]):
        raise KeyToolError("TEST_KEY_PRODUCTION_FORBIDDEN", "test-only release keys cannot sign or validate production identities")

    public_key = _load_public_key(public_path, expected_identity=public_identity)
    raw_public = _public_raw(public_key)
    fingerprint = _sha256(raw_public)
    fingerprint_doc = receipt.get("fingerprint")
    public_doc = receipt.get("public_key")
    if not isinstance(fingerprint_doc, dict) or fingerprint_doc != {
        "algorithm": "sha256",
        "scope": "raw-ed25519-public-key",
        "value": fingerprint,
    }:
        raise KeyToolError("PUBLIC_FINGERPRINT_MISMATCH", "trust receipt public-key fingerprint is invalid")
    public_pem = _secure_read(
        public_path,
        field="public key",
        maximum_bytes=64 * 1024,
        expected_identity=public_identity,
    )
    if (
        not isinstance(public_doc, dict)
        or set(public_doc) != {"encoding", "value", "pem_filename", "pem_sha256"}
        or public_doc.get("encoding") != "base64-raw-ed25519"
        or _decode_exact_base64(
            public_doc.get("value"), expected_bytes=32, code="TRUST_RECEIPT_INVALID", field="receipt public key"
        )
        != raw_public
        or public_doc.get("pem_filename") != public_path.name
        or public_doc.get("pem_sha256") != _sha256(public_pem)
    ):
        raise KeyToolError("TRUST_RECEIPT_INVALID", "trust receipt public-key binding is invalid")

    rotation = receipt.get("rotation")
    if not isinstance(rotation, dict) or set(rotation) != {
        "starts_on",
        "rotate_after",
        "overlap_until",
        "overlap_days",
        "predecessor_key_id",
    }:
        raise KeyToolError("ROTATION_INVALID", "trust receipt rotation fields are invalid")
    starts = _parse_iso_date(str(rotation.get("starts_on") or ""), field="starts_on")
    rotate = _parse_iso_date(str(rotation.get("rotate_after") or ""), field="rotate_after")
    overlap = _parse_iso_date(str(rotation.get("overlap_until") or ""), field="overlap_until")
    overlap_days = rotation.get("overlap_days")
    if (
        not isinstance(overlap_days, int)
        or not 1 <= overlap_days <= 180
        or rotate <= starts
        or overlap != rotate + timedelta(days=overlap_days)
    ):
        raise KeyToolError("ROTATION_INVALID", "trust receipt rotation window is not self-consistent")
    predecessor = rotation.get("predecessor_key_id")
    if predecessor is not None:
        _safe_key_id(str(predecessor))
    expected_validity = _validity_from_rotation(rotation)
    if receipt.get("validity") != expected_validity:
        raise KeyToolError("VALIDITY_INVALID", "trust receipt validity does not match its rotation window")
    today = _utc_now().date()
    not_after = rotate if operation == "sign" else overlap
    if today < starts or today > not_after:
        raise KeyToolError("KEY_OUTSIDE_VALIDITY", "release key is outside the allowed signing/verification window")
    created = _parse_canonical_utc_timestamp(
        receipt.get("created_at_utc"),
        field="trust receipt created_at_utc",
        code="TRUST_RECEIPT_INVALID",
    )
    if created > _utc_now() + timedelta(minutes=5):
        raise KeyToolError("TRUST_RECEIPT_INVALID", "trust receipt creation time is in the future")

    app_trust = receipt.get("application_trust")
    expected_catalog = {
        "key_id": key_id,
        "public_key": base64.b64encode(raw_public).decode("ascii"),
        "public_key_sha256": fingerprint,
    }
    if app_trust != {"private_key_allowed": False, "catalog_fields": expected_catalog}:
        raise KeyToolError("TRUST_RECEIPT_INVALID", "application trust binding is not public-key-only")
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
        raise KeyToolError("TRUST_RECEIPT_SIGNATURE_INVALID", "trust receipt self-signature metadata is invalid")
    receipt_signature = _decode_exact_base64(
        self_signature.get("signature"),
        expected_bytes=64,
        code="TRUST_RECEIPT_SIGNATURE_INVALID",
        field="trust receipt self-signature",
    )
    try:
        public_key.verify(receipt_signature, payload_bytes)
    except Exception as exc:
        raise KeyToolError("TRUST_RECEIPT_SIGNATURE_INVALID", "trust receipt self-signature is invalid") from exc
    return receipt, public_key, fingerprint


# Fixed-root policy verification lives only in
# backend.app.release_trust_policy and is shared by CLI verification and the
# production runtime. Signing keeps the independent private-key/receipt checks
# above, but no second root-policy implementation is retained here.

def _load_signing_material(
    key_root: Path | str,
    *,
    manifest_contract: dict[str, str],
    manifest_value: dict[str, Any],
) -> tuple[Any, dict[str, Any], Any, str]:
    indexed = _load_protected_key_index(key_root)
    receipt, public_key, fingerprint = _validate_public_trust(
        receipt_path=indexed["receipt_path"],
        public_path=indexed["public_path"],
        manifest_contract=manifest_contract,
        manifest_value=manifest_value,
        operation="sign",
        receipt_identity=indexed["receipt_identity"],
        public_identity=indexed["public_identity"],
    )
    if receipt["key_id"] != indexed["key_id"]:
        raise KeyToolError("KEY_INDEX_MISMATCH", "protected index and trust receipt key ids differ")
    private_key = _load_private_key(
        indexed["private_path"], expected_identity=indexed["private_identity"]
    )
    if _public_raw(private_key.public_key()) != _public_raw(public_key):
        raise KeyToolError("KEY_PAIR_MISMATCH", "indexed private key does not match the self-signed public trust receipt")
    return private_key, receipt, public_key, fingerprint


def sign_manifest(
    *,
    manifest: Path | str,
    key_root: Path | str,
    signature_out: Path | str,
) -> dict[str, Any]:
    manifest_path = _normalised_drive_path(manifest, field="manifest")
    raw_manifest = _secure_read(manifest_path, field="manifest", maximum_bytes=MAX_MANIFEST_BYTES)
    contract, manifest_value = _manifest_contract(manifest_path, raw_manifest)
    signature_path = _validate_new_output(signature_out, field="signature output")
    if signature_path.parent != manifest_path.parent or signature_path.name != contract["signature"]:
        raise KeyToolError("SIGNATURE_PATH_INVALID", "signature output must use the expected name beside the manifest")
    signing_key, receipt, _public_key, fingerprint = _load_signing_material(
        key_root,
        manifest_contract=contract,
        manifest_value=manifest_value,
    )
    key_id = receipt["key_id"]
    signature = signing_key.sign(raw_manifest)
    signature_document = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    _exclusive_write(signature_path, _json_bytes(signature_document), protect_acl=False)
    return {
        "status": "signed",
        "key_id": key_id,
        "manifest_sha256": _sha256(raw_manifest),
        "public_key_sha256": fingerprint,
        "signature_basename": signature_path.name,
    }


def verify_manifest(
    *,
    manifest: Path | str,
    public_key: Path | str,
    trust_receipt: Path | str,
    signature: Path | str,
    trust_policy: Path | str,
    trust_policy_signature: Path | str,
    trusted_fingerprint: str = "",
    expected_key_id: str = "",
    revoked_key_ids: Iterable[str] = (),
) -> dict[str, Any]:
    manifest_path = _normalised_drive_path(manifest, field="manifest")
    signature_path = _normalised_drive_path(signature, field="signature")
    public_path = _normalised_drive_path(public_key, field="public key")
    receipt_path = _normalised_drive_path(trust_receipt, field="public trust receipt")
    policy_path = _normalised_drive_path(trust_policy, field="release trust policy")
    policy_signature_path = _normalised_drive_path(
        trust_policy_signature,
        field="release trust policy signature",
    )
    raw_manifest = _secure_read(manifest_path, field="manifest", maximum_bytes=MAX_MANIFEST_BYTES)
    contract, manifest_value = _manifest_contract(manifest_path, raw_manifest)
    if signature_path.parent != manifest_path.parent or signature_path.name != contract["signature"]:
        raise KeyToolError("SIGNATURE_PATH_INVALID", "signature must use the expected name beside the manifest")
    try:
        trust = release_trust.verify_release_trust_chain(
            manifest_name=manifest_path.name,
            manifest_value=manifest_value,
            public_key_path=public_path,
            trust_receipt_path=receipt_path,
            trust_policy_path=policy_path,
            trust_policy_signature_path=policy_signature_path,
            operation="verify",
            trusted_fingerprint=trusted_fingerprint,
            expected_key_id=expected_key_id,
            revoked_key_ids=revoked_key_ids,
            # The operator tool may explicitly inspect test-marked release
            # fixtures. Production bootstrap never enables this boundary.
            _allow_test_roots=True,
            _read_file=lambda path, label, maximum: _secure_read(
                path,
                field=label,
                maximum_bytes=maximum,
            ),
            _now_for_tests=_utc_now(),
        )
    except release_trust.ReleaseTrustError as exc:
        raise KeyToolError(exc.code, str(exc)) from exc
    key_id = trust.leaf_key_id
    document = _strict_json(
        _secure_read(signature_path, field="signature", maximum_bytes=64 * 1024),
        code="SIGNATURE_INVALID",
        label="signature",
    )
    if not isinstance(document, dict) or set(document) != {"algorithm", "key_id", "signature"}:
        raise KeyToolError("SIGNATURE_INVALID", "signature metadata fields are invalid")
    if document.get("algorithm") != "ed25519" or document.get("key_id") != key_id:
        raise KeyToolError("SIGNATURE_INVALID", "signature algorithm or key id is invalid")
    try:
        raw_signature = base64.b64decode(str(document.get("signature") or ""), validate=True)
    except ValueError as exc:
        raise KeyToolError("SIGNATURE_INVALID", "signature is not canonical base64") from exc
    if len(raw_signature) != 64:
        raise KeyToolError("SIGNATURE_INVALID", "Ed25519 signature must contain 64 bytes")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        trusted_key = Ed25519PublicKey.from_public_bytes(trust.raw_public_key)
        trusted_key.verify(raw_signature, raw_manifest)
    except Exception as exc:  # cryptography raises InvalidSignature without useful public details.
        raise KeyToolError("SIGNATURE_INVALID", "manifest Ed25519 signature verification failed") from exc
    return {
        "status": "verified",
        "integrity_verified": True,
        "policy_signature_verified": True,
        "trust_anchored": True,
        "trust_policy": {
            "policy_id": trust.policy_id,
            "root_key_id": trust.root_key_id,
            "test_only": trust.test_only,
        },
        "key_id": key_id,
        "manifest_sha256": _sha256(raw_manifest),
        "public_key_sha256": trust.leaf_public_key_sha256,
    }


def _generic_plan() -> dict[str, Any]:
    return {
        "status": "plan",
        "writes": False,
        "default_action": "no-op",
        "explicit_actions": ["--generate", "--sign-manifest", "--verify"],
        "generate_requires": ["--output-root", "--key-id", "--rotate-after"],
        "verify_requires": [
            "--manifest",
            "--signature",
            "--public-key",
            "--trust-receipt",
            "--trust-policy",
            "--trust-policy-signature",
        ],
        "private_key_policy": {
            "drive": "E:",
            "repository": "forbidden",
            "temporary_directory": "forbidden",
            "overwrite": "forbidden",
            "acl": ["current-user", "SYSTEM", "Administrators"],
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage MentorFlow offline Ed25519 voice/engine release signing keys.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true", help="validate a generation plan without writing (default)")
    modes.add_argument("--generate", action="store_true", help="explicitly generate a new key in a new E: directory")
    modes.add_argument("--sign-manifest", action="store_true", help="sign an authorized voice/engine/catalog manifest")
    modes.add_argument(
        "--verify",
        action="store_true",
        help="verify integrity and require an independently supplied public-key trust anchor",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--key-id", default="")
    parser.add_argument("--purpose", choices=sorted(ALLOWED_PURPOSES), default="mentorflow-voice-and-engine-release")
    parser.add_argument("--rotation-start", default=_utc_now().date().isoformat())
    parser.add_argument("--rotate-after", default="")
    parser.add_argument("--overlap-days", type=int, default=30)
    parser.add_argument("--predecessor-key-id", default="")
    parser.add_argument("--test-key", action="store_true", help="mark a generated test-only key; never publish it")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--key-root", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--trust-receipt", type=Path)
    parser.add_argument("--trust-policy", type=Path)
    parser.add_argument("--trust-policy-signature", type=Path)
    parser.add_argument("--signature-out", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument(
        "--trusted-fingerprint",
        default="",
        help="optional additional leaf fingerprint constraint; never establishes trust",
    )
    parser.add_argument(
        "--expected-key-id",
        default="",
        help="optional additional leaf key-id constraint; never establishes trust",
    )
    parser.add_argument(
        "--revoked-key-id",
        action="append",
        default=[],
        help="externally revoked key id; repeat for multiple revocations",
    )
    return parser


def _require(value: Any, *, option: str) -> Any:
    if value is None or value == "":
        raise KeyToolError("ARGUMENT_MISSING", f"{option} is required for the selected action")
    return value


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        implicit_plan = not (args.generate or args.plan or args.sign_manifest or args.verify) and any(
            (args.output_root, args.key_id, args.rotate_after)
        )
        if args.generate or args.plan or implicit_plan:
            request = {
                "output_root": _require(args.output_root, option="--output-root"),
                "key_id": _require(args.key_id, option="--key-id"),
                "purpose": args.purpose,
                "starts_on": args.rotation_start,
                "rotate_after": _require(args.rotate_after, option="--rotate-after"),
                "overlap_days": args.overlap_days,
                "predecessor_key_id": args.predecessor_key_id,
                "test_only": args.test_key,
            }
            result = generate_release_key(**request) if args.generate else plan_generation(**request)
        elif args.sign_manifest:
            if args.key_id:
                raise KeyToolError("KEY_ID_CALLER_FORBIDDEN", "signing key id is derived only from the protected trust receipt")
            result = sign_manifest(
                manifest=_require(args.manifest, option="--manifest"),
                key_root=_require(args.key_root, option="--key-root"),
                signature_out=_require(args.signature_out, option="--signature-out"),
            )
        elif args.verify:
            if args.key_id:
                raise KeyToolError(
                    "KEY_ID_CALLER_FORBIDDEN",
                    "--key-id is forbidden for verification; leaf identity comes from the signed policy and receipt",
                )
            result = verify_manifest(
                manifest=_require(args.manifest, option="--manifest"),
                public_key=_require(args.public_key, option="--public-key"),
                trust_receipt=_require(args.trust_receipt, option="--trust-receipt"),
                signature=_require(args.signature, option="--signature"),
                trust_policy=_require(args.trust_policy, option="--trust-policy"),
                trust_policy_signature=_require(
                    args.trust_policy_signature,
                    option="--trust-policy-signature",
                ),
                trusted_fingerprint=args.trusted_fingerprint,
                expected_key_id=args.expected_key_id,
                revoked_key_ids=args.revoked_key_id,
            )
        else:
            result = _generic_plan()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except KeyToolError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
