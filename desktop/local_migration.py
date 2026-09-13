"""Safe, portable migration archives for MentorFlow's local data mode.

The archive is deliberately independent of the running executable.  It contains
only app-owned files and a hash manifest; credentials held by Windows
Credential Locker never enter the archive.  Import is staged and only allowed
into a new or empty directory so an active workspace cannot be overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping
from urllib.parse import urlsplit

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes


MIGRATION_FORMAT = "mentorflow-local-migration-v1"
MANIFEST_NAME = "mentorflow-migration.json"
MAX_MEMBERS = 20_000
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CONFIG_JSON_BYTES = 8 * 1024 * 1024
MAX_NEXT_LAUNCH_POINTER_BYTES = 64 * 1024
NEXT_LAUNCH_POINTER_NAME = ".mentorflow-next-data-root.json"

# The app root contains a considerable amount of disposable test/build data.
# A migration is a user-data operation, so keep the boundary explicit: the
# live databases, active teaching/classroom payloads, and user presets are the
# only roots that can be copied.  Everything else is reported as excluded.
_PORTABLE_TOP_LEVEL_DIRS = {"data", "presets"}
_PORTABLE_TOP_LEVEL_FILES = {"preset_settings.json", "desktop_settings.json"}
_DISPOSABLE_DIR_NAMES = {
    "backup",
    "backups",
    "cache",
    "logs",
    "log",
    "temp",
    "tmp",
    "ui-review",
    "runtime-cache",
    "archive_previews",
    "demo_materials",
    "expedition_art_pack_previews",
}
_DISPOSABLE_FILE_SUFFIXES = (".log", ".tmp", ".temp", ".bak", ".part")
_GENERATED_INDEX_DIR_NAMES = {
    "index",
    "indexes",
    "indices",
    "generated-index",
    "generated-indexes",
    "runtime-index",
    "runtime-indexes",
    "search-index",
    "search-indexes",
}
_GENERATED_INDEX_FILE_MARKERS = (
    "index.",
    "index-",
    "index_",
    "-index.",
    "-index-",
    "-index_",
    "_index.",
    "_index_",
)

# This is intentionally a conservative *detection* policy.  Files outside the
# portable allowlist are excluded as before; an allowlisted file containing a
# credential-shaped key/value is rejected rather than copied.  That keeps a
# typo in a user export from silently turning into a secret-bearing package.
_SECRET_KEY_RE = re.compile(
    r"(?ix)"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|session[_-]?token|"
    r"client[_-]?secret|credential[_-]?ref|provider[_-]?session(?:[_-]?id)?|"
    r"authorization|password(?![_-]?hash)|private[_-]?key)"
    r"\s*[\"']?\s*(?:[=:]|=>)\s*"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{12,}\b|\bps_[A-Za-z0-9_-]{8,}\b|\b(?:mfcred|cred)_[A-Za-z0-9_-]{8,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~-]{12,}\b)"
)
_SECRET_COLUMN_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?token|credential[_-]?ref|"
    r"provider[_-]?session|authorization|password(?![_-]?hash)|private[_-]?key|client[_-]?secret)"
)
SAFE_SETTINGS_MEMBER = "desktop_settings.json"


class MigrationError(RuntimeError):
    """Raised when a migration archive cannot be safely created or imported."""


_REPARSE_POINT_ATTRIBUTE = int(
    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or 0x400
)


# Windows path validation cannot be made atomic with ``lstat`` followed by a
# normal ``open``: an attacker can replace the final entry (or a parent
# junction) in between those calls.  Keep the native handle machinery lazy so
# source-mode test runs on other platforms retain the same portable fallback.
_WIN_GENERIC_READ = 0x80000000
_WIN_GENERIC_WRITE = 0x40000000
_WIN_FILE_SHARE_READ = 0x00000001
_WIN_FILE_SHARE_WRITE = 0x00000002
_WIN_OPEN_EXISTING = 3
_WIN_CREATE_NEW = 1
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_INVALID_HANDLE = ctypes.c_void_p(-1).value if os.name == "nt" else -1
_WIN_HANDLE_API: tuple[Any, Any, Any] | None = None


def _file_identity(info: os.stat_result) -> tuple[int, int]:
    """Return the volume/file-id pair used to detect a replaced entry."""

    device = int(getattr(info, "st_dev", 0) or 0)
    # CPython's Windows ``st_dev`` carries additional volume metadata in the
    # high bits; GetFileInformationByHandle exposes the authoritative low
    # 32-bit serial number.
    if os.name == "nt":
        device &= 0xFFFFFFFF
    return (device, int(getattr(info, "st_ino", 0) or 0))


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return _file_identity(left) == _file_identity(right)


def _win_handle_api() -> tuple[Any, Any, Any]:
    global _WIN_HANDLE_API
    if _WIN_HANDLE_API is None:
        if os.name != "nt":
            raise RuntimeError("Windows native handle API is unavailable")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        get_file_info = kernel32.GetFileInformationByHandle
        get_file_info.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
        get_file_info.restype = wintypes.BOOL
        _WIN_HANDLE_API = (create_file, close_handle, get_file_info)
    return _WIN_HANDLE_API


if os.name == "nt":

    class _WinFileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


    class _WinByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _WinFileTime),
            ("ftLastAccessTime", _WinFileTime),
            ("ftLastWriteTime", _WinFileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]


def _win_raise(path: Path, *, operation: str) -> OSError:
    error = ctypes.get_last_error()
    message = ctypes.FormatError(error) if error else "unknown Windows error"
    return OSError(error, f"{operation} failed for {path}: {message}")


def _win_open_handle(
    path: Path,
    *,
    access: int,
    creation: int,
    directory: bool = False,
) -> int:
    """Open an entry without following its final reparse point.

    The handle deliberately omits ``FILE_SHARE_DELETE``.  Once acquired, a
    concurrent rename/unlink of the entry fails, so all reads/writes below use
    the object whose identity was checked rather than a subsequently resolved
    pathname.
    """

    create_file, _, _ = _win_handle_api()
    flags = _WIN_FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _WIN_FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(
        str(path),
        access,
        _WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE,
        None,
        creation,
        flags,
        None,
    )
    if handle in (None, _WIN_INVALID_HANDLE):
        raise _win_raise(path, operation="CreateFileW")
    return int(handle)


def _win_handle_identity(handle: int, path: Path) -> tuple[int, int]:
    """Read the stable volume/file-id pair without reopening the path."""

    _, _, get_file_info = _win_handle_api()
    native = _WinByHandleFileInformation()
    if not get_file_info(handle, ctypes.byref(native)):
        raise _win_raise(path, operation="GetFileInformationByHandle")
    return (
        int(native.dwVolumeSerialNumber),
        (int(native.nFileIndexHigh) << 32) | int(native.nFileIndexLow),
    )


@contextmanager
def _hold_directory_chain(
    path: Path,
    *,
    role: str,
    expected_info: os.stat_result | None = None,
) -> Iterator[None]:
    """Pin every existing directory component while a path operation runs."""

    lexical = _lexical_path(path)
    _validate_parent_chain(lexical, role=role)
    if expected_info is not None:
        current_info = _validate_existing_entry(lexical, role=role)
        if current_info is None or not _same_file_identity(current_info, expected_info):
            raise MigrationError(f"迁移{role}在打开前发生变化：{lexical}")
    if os.name != "nt":
        yield
        return
    chain: list[Path] = []
    current = lexical
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    with ExitStack() as stack:
        for component in reversed(chain):
            try:
                info = os.lstat(component)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise MigrationError(f"无法锁定迁移{role}父路径：{component}（{exc}）") from exc
            if _is_reparse_info(info) or not stat.S_ISDIR(info.st_mode):
                raise MigrationError(f"拒绝迁移{role}父路径中的符号链接或重解析点：{component}")
            try:
                handle = _win_open_handle(
                    component,
                    access=0,
                    creation=_WIN_OPEN_EXISTING,
                    directory=True,
                )
            except OSError as exc:
                raise MigrationError(f"无法锁定迁移{role}父路径：{component}（{exc}）") from exc
            _, close_handle, _ = _win_handle_api()
            try:
                if _win_handle_identity(handle, component) != _file_identity(info):
                    raise MigrationError(f"迁移{role}父路径在打开过程中发生变化：{component}")
            except BaseException:
                close_handle(handle)
                raise
            stack.callback(close_handle, handle)
        yield


@contextmanager
def _open_verified_file(
    path: Path,
    *,
    role: str,
    expected_info: os.stat_result | None = None,
    create_new: bool = False,
    mode: str = "rb",
) -> Iterator[BinaryIO]:
    """Open one regular file by identity and keep that object pinned."""

    lexical = _lexical_path(path)
    _validate_parent_chain(lexical.parent, role=role)
    with _hold_directory_chain(lexical.parent, role=role):
        native = os.name == "nt"
        fd: int | None = None
        stream: BinaryIO | None = None
        if native:
            try:
                handle = _win_open_handle(
                    lexical,
                    access=_WIN_GENERIC_READ | (_WIN_GENERIC_WRITE if "w" in mode else 0),
                    creation=_WIN_CREATE_NEW if create_new else _WIN_OPEN_EXISTING,
                    directory=False,
                )
                fd = msvcrt.open_osfhandle(handle, os.O_BINARY)
            except OSError as exc:
                if "handle" in locals() and fd is None:
                    try:
                        _, close_handle, _ = _win_handle_api()
                        close_handle(handle)
                    except Exception:
                        pass
                raise MigrationError(f"无法安全打开迁移{role}：{lexical}（{exc}）") from exc
        else:
            flags = getattr(os, "O_BINARY", 0) | (os.O_RDWR if "w" in mode else os.O_RDONLY)
            if create_new:
                flags |= os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(lexical, flags, 0o600)
            except OSError as exc:
                raise MigrationError(f"无法安全打开迁移{role}：{lexical}（{exc}）") from exc
        assert fd is not None
        try:
            info = os.fstat(fd)
            if _is_reparse_info(info) or not stat.S_ISREG(info.st_mode):
                raise MigrationError(f"拒绝迁移{role}中的符号链接或特殊文件：{lexical}")
            if _is_hardlink_info(info):
                raise MigrationError(f"拒绝迁移{role}中的硬链接文件：{lexical}")
            if expected_info is not None and not _same_file_identity(info, expected_info):
                raise MigrationError(f"迁移{role}在打开前发生变化：{lexical}")
            stream = os.fdopen(fd, mode, closefd=True)
            fd = None
            failed = False
            try:
                yield stream
            except BaseException:
                failed = True
                raise
            finally:
                try:
                    end_info = os.fstat(stream.fileno())
                    if _is_reparse_info(end_info) or not stat.S_ISREG(end_info.st_mode):
                        raise MigrationError(f"迁移{role}在读取过程中发生变化：{lexical}")
                    if expected_info is not None and not _same_file_identity(end_info, expected_info):
                        raise MigrationError(f"迁移{role}在读取过程中发生变化：{lexical}")
                except (OSError, ValueError) as exc:
                    if not failed:
                        raise MigrationError(f"无法复核迁移{role}：{lexical}（{exc}）") from exc
                finally:
                    stream.close()
        finally:
            if fd is not None:
                os.close(fd)


def _copy_file_no_follow(
    source: Path,
    destination: Path,
    *,
    expected_source_info: os.stat_result | None = None,
    role: str = "源文件",
) -> None:
    """Copy bytes from a verified source handle into a create-new target."""

    source = _lexical_path(source)
    destination = _lexical_path(destination)
    source_info = expected_source_info or _validate_existing_entry(source, role=role)
    if source_info is None or not stat.S_ISREG(source_info.st_mode):
        raise MigrationError(f"迁移{role}不是普通文件：{source}")
    _validate_parent_chain(destination.parent, role="暂存目标")
    _ensure_directory(destination.parent, role="暂存目标")
    _validate_parent_chain(destination.parent, role="暂存目标")
    created_identity: tuple[int, int] | None = None
    try:
        with _open_verified_file(source, role=role, expected_info=source_info) as input_stream:
            with _open_verified_file(
                destination,
                role="暂存目标",
                create_new=True,
                mode="wb",
            ) as output_stream:
                created_identity = _file_identity(os.fstat(output_stream.fileno()))
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                try:
                    os.fsync(output_stream.fileno())
                except OSError:
                    pass
    except MigrationError:
        if created_identity is not None:
            try:
                current = _validate_existing_entry(destination, role="暂存目标", allow_missing=True)
                if current is not None and _file_identity(current) == created_identity:
                    destination.unlink(missing_ok=True)
            except (MigrationError, OSError):
                pass
        raise


def _write_new_text_no_follow(path: Path, text: str, *, role: str) -> None:
    """Create a marker/config file without truncating a raced hardlink."""

    lexical = _lexical_path(path)
    _validate_parent_chain(lexical.parent, role=role)
    _ensure_directory(lexical.parent, role=role)
    _validate_parent_chain(lexical.parent, role=role)
    created_identity: tuple[int, int] | None = None
    try:
        with _open_verified_file(lexical, role=role, create_new=True, mode="wb") as stream:
            created_identity = _file_identity(os.fstat(stream.fileno()))
            stream.write(text.encode("utf-8"))
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
    except MigrationError:
        if created_identity is not None:
            try:
                current = _validate_existing_entry(lexical, role=role, allow_missing=True)
                if current is not None and _file_identity(current) == created_identity:
                    lexical.unlink(missing_ok=True)
            except (MigrationError, OSError):
                pass
        raise


def _is_reparse_info(info: os.stat_result) -> bool:
    """Return whether an ``lstat`` result is a link or Windows reparse point."""

    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    if attributes & _REPARSE_POINT_ATTRIBUTE:
        return True
    # Some Python/Windows combinations expose only the tag.  A non-zero tag
    # still means the path is a reparse point and must not be traversed.
    return bool(getattr(info, "st_reparse_tag", 0))


def _is_hardlink_info(info: os.stat_result) -> bool:
    """Return whether a regular file has more than one directory entry."""

    return stat.S_ISREG(info.st_mode) and int(getattr(info, "st_nlink", 1) or 1) > 1


def _lexical_path(path: Path) -> Path:
    """Normalize a path without resolving links (which are rejected first)."""

    return Path(os.path.abspath(os.fspath(path)))


def _is_reparse_or_symlink(path: Path) -> bool:
    """Inspect a path with ``lstat`` so a link target is never followed."""

    return _reparse_ancestor(path) is not None


def _reparse_ancestor(path: Path) -> Path | None:
    """Return a reparse/symlink component without following it first."""

    candidate = _lexical_path(path)
    while True:
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            info = None
        except OSError:
            # An unreadable component is unsafe to use as a data boundary.
            return candidate
        if info is not None and _is_reparse_info(info):
            return candidate
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent


def _validate_existing_entry(
    path: Path,
    *,
    role: str,
    allow_missing: bool = False,
) -> os.stat_result | None:
    """Lstat one migration entry and reject links, special files and hardlinks."""

    lexical = _lexical_path(path)
    _validate_parent_chain(lexical.parent, role=role)
    try:
        info = os.lstat(lexical)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise MigrationError(f"迁移{role}不存在：{lexical}")
    except OSError as exc:
        raise MigrationError(f"无法检查迁移{role}：{lexical}（{exc}）") from exc
    if _is_reparse_info(info):
        raise MigrationError(f"拒绝迁移{role}中的符号链接或重解析点：{lexical}")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise MigrationError(f"拒绝迁移{role}中的特殊文件：{lexical}")
    if _is_hardlink_info(info):
        raise MigrationError(f"拒绝迁移{role}中的硬链接文件：{lexical}")
    return info


def _validate_parent_chain(path: Path, *, role: str) -> None:
    """Check existing parents so a missing child cannot hide a link ancestor."""

    current = _lexical_path(path)
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if current.parent == current:
                return
            current = current.parent
            continue
        except OSError as exc:
            raise MigrationError(f"无法检查迁移{role}父路径：{current}（{exc}）") from exc
        if _is_reparse_info(info):
            raise MigrationError(f"拒绝迁移{role}父路径中的符号链接或重解析点：{current}")
        if not stat.S_ISDIR(info.st_mode):
            raise MigrationError(f"迁移{role}父路径不是目录：{current}")
        if current.parent == current:
            return
        current = current.parent


def _assert_within(path: Path, root: Path, *, role: str) -> None:
    """Ensure lexical traversal cannot escape its original migration root.

    All components are checked with ``lstat`` before this helper is called, so
    resolving here would only reopen the validate-then-resolve race that the
    migration boundary is designed to close.  Absolute lexical paths also
    make ``..`` traversal explicit without following a junction or symlink.
    """

    candidate = _lexical_path(path)
    boundary = _lexical_path(root)
    _validate_parent_chain(candidate.parent, role=role)
    _validate_parent_chain(boundary, role=f"{role}根")
    try:
        candidate.relative_to(boundary)
    except ValueError as exc:
        raise MigrationError(f"迁移{role}解析路径逃逸根目录：{candidate}") from exc


def _ensure_directory(path: Path, *, role: str) -> Path:
    """Create a directory one component at a time without following links.

    ``Path.mkdir(parents=True)`` resolves every missing component in one
    syscall sequence.  A parent can be replaced with a junction after a
    preceding ``lstat`` and before that sequence reaches it.  Creating each
    component with its already-existing parent chain held open makes the
    operation fail closed on Windows and keeps the portable ``O_NOFOLLOW``
    checks on other platforms.
    """

    lexical = _lexical_path(path)
    chain: list[Path] = []
    current = lexical
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for component in reversed(chain):
        info = _validate_existing_entry(component, role=role, allow_missing=True)
        if info is not None:
            if not stat.S_ISDIR(info.st_mode):
                raise MigrationError(f"迁移{role}必须是目录：{component}")
            continue
        parent = component.parent
        parent_info = _validate_existing_entry(parent, role=role)
        if parent_info is None or not stat.S_ISDIR(parent_info.st_mode):
            raise MigrationError(f"迁移{role}父路径不是目录：{parent}")
        try:
            with _hold_directory_chain(parent, role=role, expected_info=parent_info):
                try:
                    component.mkdir(exist_ok=False)
                except FileExistsError:
                    # A competing creator is safe only if it produced a
                    # normal directory rather than a link/special entry.
                    pass
                created_info = _validate_existing_entry(component, role=role)
                if created_info is None or not stat.S_ISDIR(created_info.st_mode):
                    raise MigrationError(f"迁移{role}创建结果不是目录：{component}")
        except OSError as exc:
            raise MigrationError(f"无法创建迁移{role}：{component}（{exc}）") from exc
    return lexical


def _cleanup_private_tree(
    path: Path,
    *,
    parent_info: os.stat_result | None,
    role: str,
) -> None:
    """Best-effort cleanup that never follows a replaced staging parent."""

    lexical = _lexical_path(path)
    if parent_info is None:
        return
    try:
        with _hold_directory_chain(
            lexical.parent,
            role=role,
            expected_info=parent_info,
        ):
            info = _validate_existing_entry(lexical, role=role, allow_missing=True)
            if info is None:
                return
            if not stat.S_ISDIR(info.st_mode):
                return
            # ``rmtree`` does not follow normal symlinks on supported Python
            # versions; validating the root and pinning its parent additionally
            # prevents a parent junction swap from redirecting this cleanup.
            shutil.rmtree(lexical, ignore_errors=True)
    except (MigrationError, OSError):
        # Cleanup is never allowed to turn a completed migration into a new
        # failure, and a failed identity check is safer left for later cleanup.
        return


def _cleanup_private_file(
    path: Path,
    *,
    parent_info: os.stat_result | None,
    expected_identity: tuple[int, int] | None,
    role: str,
) -> None:
    """Remove only an owned temporary file under an identity-pinned parent."""

    if parent_info is None or expected_identity is None:
        return
    lexical = _lexical_path(path)
    try:
        with _hold_directory_chain(
            lexical.parent,
            role=role,
            expected_info=parent_info,
        ):
            info = _validate_existing_entry(lexical, role=role, allow_missing=True)
            if info is None or _file_identity(info) != expected_identity:
                return
            lexical.unlink(missing_ok=True)
    except (MigrationError, OSError):
        return


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_verified_file(path, role="校验") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _secret_scan_text(value: str, *, path: str) -> None:
    """Reject credential-shaped text in an otherwise allowlisted file."""

    if _SECRET_KEY_RE.search(value) or _SECRET_VALUE_RE.search(value):
        raise MigrationError(f"便携数据包含疑似凭据或 Provider 会话：{path}")


_SENSITIVE_JSON_NAMES = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "authtoken",
        "sessiontoken",
        "clientsecret",
        "credentialref",
        "providersession",
        "providersessionid",
        "authorization",
        "password",
        "privatekey",
    }
)
_SENSITIVE_MARKER_RE = re.compile(
    r"(?ix)(?<![a-z0-9_])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth[_-]?token|session[_-]?token|client[_-]?secret|credential[_-]?ref|"
    r"provider[_-]?session(?:[_-]?id)?|authorization|password(?![_-]?hash)|private[_-]?key)"
    r"(?![a-z0-9_])"
)
_SENSITIVE_JSON_VARIANT_RE = re.compile(
    r"(?ix)^(?:(?:previous|requested))?"
    r"(?:apikey|accesstoken|refreshtoken|idtoken|authtoken|sessiontoken|"
    r"clientsecret|credentialref|providersession(?:id)?|authorization|"
    r"password(?!hash)|privatekey)$"
)


def _canonical_secret_name(value: object) -> str:
    """Normalize JSON/SQLite names before comparing sensitive fields."""

    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_sensitive_json_name(value: object) -> bool:
    # SQLite/state payloads can retain reconnect-era names such as
    # ``previous_provider_session_id`` and ``requested_provider_session``.
    # Match each dotted/path segment independently so a nested key path is
    # redacted while ordinary names containing a sensitive word remain intact.
    for segment in re.split(r"[./\\:]", str(value)):
        canonical = _canonical_secret_name(segment)
        if canonical in _SENSITIVE_JSON_NAMES or _SENSITIVE_JSON_VARIANT_RE.fullmatch(canonical):
            return True
    return False


def _is_sensitive_sqlite_name(value: object) -> bool:
    """Return whether a SQLite table/column name denotes secret material."""

    return _is_sensitive_json_name(value) or bool(_SECRET_COLUMN_RE.search(str(value)))


def _redact_json_value(value: Any, *, path: str) -> tuple[Any, bool]:
    """Recursively remove explicitly sensitive keys from a JSON value."""

    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            if _is_sensitive_json_name(key) or (
                isinstance(item, str) and _SECRET_VALUE_RE.fullmatch(item.strip())
            ):
                changed = True
                continue
            cleaned, item_changed = _redact_json_value(item, path=path)
            redacted[key] = cleaned
            changed = changed or item_changed
        return redacted, changed
    if isinstance(value, list):
        redacted_items: list[Any] = []
        changed = False
        for item in value:
            cleaned, item_changed = _redact_json_value(item, path=path)
            redacted_items.append(cleaned)
            changed = changed or item_changed
        return redacted_items, changed
    if isinstance(value, str):
        return _redact_json_text(value, path=path)
    return value, False


def _redact_json_document(value: Any) -> tuple[Any, bool]:
    """Recursively remove sensitive object keys from a staged JSON document.

    Unlike ``_redact_json_value`` (which also understands JSON embedded in
    SQLite text cells), preset/config sanitization only walks actual JSON
    objects and arrays.  Ordinary string fields remain byte-for-byte values;
    any raw credential marker left in those fields is rejected by the scan
    after redaction.
    """

    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            if _is_sensitive_json_name(key) or (
                isinstance(item, str) and _SECRET_VALUE_RE.fullmatch(item.strip())
            ):
                changed = True
                continue
            cleaned, item_changed = _redact_json_document(item)
            redacted[key] = cleaned
            changed = changed or item_changed
        return redacted, changed
    if isinstance(value, list):
        redacted_items: list[Any] = []
        changed = False
        for item in value:
            cleaned, item_changed = _redact_json_document(item)
            redacted_items.append(cleaned)
            changed = changed or item_changed
        return redacted_items, changed
    return value, False


def _reject_nonstandard_json_constant(value: str) -> Any:
    """Reject NaN/Infinity accepted by Python but not by strict JSON."""

    raise ValueError(f"non-standard JSON constant: {value}")


def _load_strict_json(raw: bytes, *, path: str) -> tuple[str, Any]:
    """Decode and parse one portable config using strict JSON semantics."""

    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text, parse_constant=_reject_nonstandard_json_constant)
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MigrationError(f"便携配置 JSON 无法解析：{path}") from exc
    return text, parsed


def _snapshot_json_file(
    source: Path,
    destination: Path,
    *,
    expected_source_info: os.stat_result | None,
    relative: str,
) -> None:
    """Create a redacted staged copy of an allowlisted preset/config JSON."""

    source = _lexical_path(source)
    destination = _lexical_path(destination)
    source_info = expected_source_info or _validate_existing_entry(source, role="源文件")
    if source_info is None or not stat.S_ISREG(source_info.st_mode):
        raise MigrationError(f"迁移源文件不是普通文件：{source}")
    with _open_verified_file(source, role="源文件", expected_info=source_info) as stream:
        raw = stream.read(MAX_CONFIG_JSON_BYTES + 1)
    if len(raw) > MAX_CONFIG_JSON_BYTES:
        raise MigrationError(f"便携配置 JSON 超过安全上限：{relative}")
    original_text, parsed = _load_strict_json(raw, path=relative)
    cleaned, changed = _redact_json_document(parsed)
    if changed:
        try:
            cleaned_text = json.dumps(
                cleaned,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            # Validate the serialized staged representation before publishing
            # it.  This also keeps the fail-closed contract local to the copy.
            _load_strict_json(cleaned_text.encode("utf-8"), path=relative)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise MigrationError(f"便携配置 JSON 无法清理：{relative}") from exc
    else:
        cleaned_text = original_text
    _secret_scan_text(cleaned_text, path=relative)
    _write_new_text_no_follow(destination, cleaned_text, role="配置暂存")


def _redact_json_text(value: str, *, path: str) -> tuple[str, bool]:
    """Redact JSON embedded in a SQLite text cell and reject raw secrets."""

    stripped = value.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            if (
                _SECRET_KEY_RE.search(value)
                or _SECRET_VALUE_RE.search(value)
                or _SENSITIVE_MARKER_RE.search(value)
            ):
                raise MigrationError(f"便携数据包含无法解析的凭据文本：{path}")
            return value, False
        cleaned, changed = _redact_json_value(parsed, path=path)
        if not changed:
            _secret_scan_text(value, path=path)
            return value, False
        serialized = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
        # A non-sensitive sibling value may still contain a raw token.  Scan
        # the cleaned document so only the explicitly named fields are
        # removed while all unrelated content remains intact.
        _secret_scan_text(serialized, path=path)
        return serialized, True
    _secret_scan_text(value, path=path)
    return value, False


def _redact_sqlite_value(value: Any, *, path: str) -> tuple[Any, bool]:
    """Sanitize a TEXT/BLOB SQLite value without changing its storage kind."""

    if isinstance(value, str):
        return _redact_json_text(value, path=path)
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            # A binary upload is valid user data, but a recognizable raw
            # credential in it is not portable.  Keep opaque bytes unchanged.
            marker_text = value.decode("utf-8", errors="ignore")
            if _SECRET_KEY_RE.search(marker_text) or _SECRET_VALUE_RE.search(marker_text):
                raise MigrationError(f"便携数据库包含无法清理的凭据文本：{path}")
            return value, False
        cleaned, changed = _redact_json_text(decoded, path=path)
        return (cleaned.encode("utf-8"), True) if changed else (value, False)
    return value, False


def _sqlite_identifier(value: object) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _sqlite_neutral_value(declared_type: object) -> Any:
    normalized = str(declared_type or "").casefold()
    if any(marker in normalized for marker in ("int", "real", "float", "double", "numeric", "decimal")):
        return 0
    if "blob" in normalized:
        return b""
    return ""


_WINDOWS_DEVICE_PATH_RE = re.compile(r"(?i)^[a-z]:[\\/]")


def _is_absolute_device_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    parsed = urlsplit(text)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.hostname:
        return False
    return bool(
        _WINDOWS_DEVICE_PATH_RE.match(text)
        or text.startswith(("\\\\", "//", "/"))
    )


def _redact_account_local_paths(value: Any) -> tuple[Any, bool]:
    """Remove device-bound absolute paths from account-portable DB values."""

    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                if _is_absolute_device_path(value):
                    return None, True
                return value, False
            cleaned, changed = _redact_account_local_paths(parsed)
            if changed:
                return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")), True
            return value, False
        if _is_absolute_device_path(value):
            return None, True
        return value, False
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            return value, False
        cleaned, changed = _redact_account_local_paths(decoded)
        if not changed:
            return value, False
        if cleaned is None:
            return None, True
        return str(cleaned).encode("utf-8"), True
    if isinstance(value, list):
        output: list[Any] = []
        changed = False
        for item in value:
            cleaned, item_changed = _redact_account_local_paths(item)
            output.append(cleaned)
            changed = changed or item_changed
        return output, changed
    if isinstance(value, dict):
        output: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            cleaned, item_changed = _redact_account_local_paths(item)
            output[key] = cleaned
            changed = changed or item_changed
        return output, changed
    return value, False


def _sanitize_sqlite_snapshot(path: Path, *, account_portable: bool = False) -> None:
    """Redact secret fields in a staged SQLite backup only.

    The source database is never opened for writes.  Sensitive columns are
    retained for schema compatibility and set to NULL where possible; JSON
    objects in unrelated TEXT/BLOB cells have sensitive keys removed.  A
    NOT-NULL/primary-key sensitive column or a WITHOUT ROWID table without a
    primary key is unsupported and fails closed.
    """

    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise MigrationError(f"无法清理便携数据库：{path.name}") from exc
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        connection.execute("BEGIN")
        for table_row in table_rows:
            table_name = str(table_row[0])
            if _SECRET_COLUMN_RE.search(table_name):
                if not account_portable:
                    raise MigrationError(f"便携数据库包含不支持的凭据表：{path.name}")
                connection.execute(f"DELETE FROM {_sqlite_identifier(table_name)}")
                continue
            quoted_table = _sqlite_identifier(table_name)
            columns = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            if not columns:
                raise MigrationError(f"便携数据库包含无法清理的表：{table_name}")
            names = [str(row[1]) for row in columns]
            sensitive_indexes = {
                index
                for index, name in enumerate(names)
                if _is_sensitive_sqlite_name(name)
            }
            for index in sensitive_indexes:
                not_null = bool(columns[index][3])
                is_primary_key = bool(columns[index][5])
                if not_null or is_primary_key:
                    values = connection.execute(
                        f"SELECT {quoted_table}.{_sqlite_identifier(names[index])} FROM {quoted_table} "
                        f"WHERE {quoted_table}.{_sqlite_identifier(names[index])} IS NOT NULL LIMIT 1"
                    ).fetchone()
                    if values is not None and not account_portable:
                        raise MigrationError(
                            f"便携数据库包含无法清理的非空凭据字段：{table_name}.{names[index]}"
                        )

            try:
                rows = connection.execute(f"SELECT rowid, * FROM {quoted_table}").fetchall()
                rowid_mode = True
            except sqlite3.OperationalError:
                rows = connection.execute(f"SELECT * FROM {quoted_table}").fetchall()
                rowid_mode = False
            primary_key_indexes = [
                index for index, column in enumerate(columns) if int(column[5] or 0) > 0
            ]
            if not rowid_mode and not primary_key_indexes and rows:
                raise MigrationError(f"便携数据库包含无法定位的敏感存储：{table_name}")
            for raw_row in rows:
                values = list(raw_row[1:] if rowid_mode else raw_row)
                updates: dict[int, Any] = {}
                for index, value in enumerate(values):
                    if index in sensitive_indexes:
                        if value is not None:
                            updates[index] = (
                                _sqlite_neutral_value(columns[index][2])
                                if account_portable
                                and (bool(columns[index][3]) or bool(columns[index][5]))
                                else None
                            )
                        continue
                    if isinstance(value, (str, bytes)):
                        cleaned, changed = _redact_sqlite_value(
                            value,
                            path=f"{path.name}:{table_name}.{names[index]}",
                        )
                        if account_portable:
                            cleaned, path_changed = _redact_account_local_paths(cleaned)
                            changed = changed or path_changed
                            if cleaned is None and (bool(columns[index][3]) or bool(columns[index][5])):
                                cleaned = _sqlite_neutral_value(columns[index][2])
                        if changed:
                            updates[index] = cleaned
                if not updates:
                    continue
                if rowid_mode:
                    where = "rowid = ?"
                    identity: list[Any] = [raw_row[0]]
                else:
                    where_parts: list[str] = []
                    identity = []
                    for index in primary_key_indexes:
                        where_parts.append(f"{_sqlite_identifier(names[index])} = ?")
                        identity.append(values[index])
                    where = " AND ".join(where_parts)
                assignments = ", ".join(
                    f"{_sqlite_identifier(names[index])} = ?" for index in updates
                )
                connection.execute(
                    f"UPDATE {quoted_table} SET {assignments} WHERE {where}",
                    [*updates.values(), *identity],
                )
        connection.commit()
        # SQLite updates can leave old cell payloads in free pages.  Rebuild
        # the staged copy so a byte-level credential scan cannot recover a
        # redacted API key or provider session from the snapshot's slack.
        connection.execute("VACUUM")
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.casefold() != "ok":
            raise MigrationError(f"数据库完整性检查失败：{path.name} ({integrity})")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise MigrationError(f"数据库外键检查失败：{path.name}")
    except MigrationError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise MigrationError(f"无法清理便携数据库：{path.name}") from exc
    finally:
        connection.close()


def scan_account_portable_sqlite(path: Path) -> None:
    """Verify that an account snapshot contains no usable secret/session rows."""

    try:
        with _open_verified_file(path, role="账号便携数据库扫描") as stream:
            decoded = stream.read().decode("utf-8", errors="ignore")
        _secret_scan_text(decoded, path=path.name)
        connection = sqlite3.connect(path)
        try:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                quoted_table = _sqlite_identifier(table)
                if _SECRET_COLUMN_RE.search(table):
                    if connection.execute(f"SELECT 1 FROM {quoted_table} LIMIT 1").fetchone():
                        raise MigrationError(f"账号便携数据库包含 Provider/凭据会话：{path.name}")
                    continue
                for column in connection.execute(f"PRAGMA table_info({quoted_table})"):
                    name = str(column[1])
                    if not _is_sensitive_sqlite_name(name):
                        continue
                    quoted_column = _sqlite_identifier(name)
                    populated = connection.execute(
                        f"SELECT 1 FROM {quoted_table} "
                        f"WHERE {quoted_column} IS NOT NULL "
                        f"AND TRIM(CAST({quoted_column} AS TEXT)) NOT IN ('', '0') LIMIT 1"
                    ).fetchone()
                    if populated is not None:
                        raise MigrationError(f"账号便携数据库包含 Provider/凭据字段：{path.name}")
                for values in connection.execute(f"SELECT * FROM {quoted_table}"):
                    for value in values:
                        if isinstance(value, (str, bytes)):
                            _cleaned, path_changed = _redact_account_local_paths(value)
                            if path_changed:
                                raise MigrationError(
                                    f"账号便携数据库包含设备绝对路径：{path.name}"
                                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise MigrationError(f"账号便携数据库无法校验：{path.name}") from exc


def sanitize_account_portable_sqlite(path: Path) -> None:
    """Remove account secrets from an owned staged DB, never the live source."""

    _sanitize_sqlite_snapshot(path, account_portable=True)
    scan_account_portable_sqlite(path)


def _scan_portable_secret(path: Path, *, relative: str) -> None:
    """Fail closed on raw secrets without interpreting arbitrary user bytes.

    Text/config files are decoded strictly enough to catch JSON and common
    text material.  SQLite gets a schema/column check plus a byte marker scan;
    arbitrary binary/raw uploads are not decoded, but well-known key/value
    markers still fail closed when present.
    """

    lowered = relative.casefold()
    if lowered.endswith((".db", ".sqlite", ".sqlite3")):
        try:
            with _open_verified_file(path, role="凭据扫描") as stream:
                raw = stream.read()
            decoded = raw.decode("utf-8", errors="ignore")
            _secret_scan_text(decoded, path=relative)
            connection = sqlite3.connect(path)
            try:
                tables = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ]
                for table in tables:
                    if _SECRET_COLUMN_RE.search(table):
                        raise MigrationError(f"便携数据库包含凭据表：{relative}")
                    quoted_table = _sqlite_identifier(table)
                    columns = connection.execute(
                        f"PRAGMA table_info({quoted_table})"
                    ).fetchall()
                    sensitive_columns = [
                        str(row[1])
                        for row in columns
                        if _is_sensitive_sqlite_name(row[1] or "")
                    ]
                    for column in sensitive_columns:
                        quoted_column = _sqlite_identifier(column)
                        non_null = connection.execute(
                            f"SELECT 1 FROM {quoted_table} "
                            f"WHERE {quoted_column} IS NOT NULL LIMIT 1"
                        ).fetchone()
                        if non_null is not None:
                            raise MigrationError(f"便携数据库包含凭据字段：{relative}")
            finally:
                connection.close()
        except sqlite3.Error:
            # The normal SQLite integrity gate below owns malformed/corrupt
            # database diagnostics.  Do not mask its stable error contract
            # with a credential-scan message.
            return
        return
    # A binary upload is a valid raw material, so decode only when it looks
    # like text.  The marker scan still catches a raw API token in UTF-8 data.
    try:
        with _open_verified_file(path, role="凭据扫描") as stream:
            raw = stream.read()
    except MigrationError:
        raise
    if not raw:
        return
    if b"\x00" in raw[:4096] and not lowered.endswith((".json", ".txt", ".md", ".csv", ".xml", ".yaml", ".yml")):
        decoded = raw.decode("utf-8", errors="ignore")
    else:
        decoded = raw.decode("utf-8", errors="ignore")
    _secret_scan_text(decoded, path=relative)


def _validate_safe_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the small, nonsecret Qt settings subset in a package."""

    allowed_prefixes = ("task-colors/",)
    allowed = {
        "official_updates/enabled",
        "tutorial/completed",
        "tutorial/step",
        "analogy/generation_policy",
        "analogy/keywords",
        "news/interests",
        "news/blocked",
        "provider/base_url",
        "provider/model",
        "provider/preset_name",
    }
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in settings.items():
        key = str(raw_key or "").strip()
        if key not in allowed and not any(key.startswith(prefix) for prefix in allowed_prefixes):
            continue
        if key.casefold().endswith(("credential_ref", "provider_session", "provider_session_id")):
            continue
        if isinstance(raw_value, (bool, int, float)):
            value: Any = raw_value
        elif isinstance(raw_value, str):
            value = raw_value
        else:
            continue
        if isinstance(value, str) and len(value) > 4096:
            raise MigrationError(f"安全设置过长：{key}")
        if key == "provider/base_url" and isinstance(value, str):
            value = value.strip()
            if value:
                try:
                    parsed = urlsplit(value)
                    _ = parsed.port
                except ValueError:
                    continue
                if (
                    parsed.scheme.casefold() not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or "?" in value
                    or "#" in value
                    or any(character.isspace() or ord(character) < 32 for character in value)
                ):
                    # An invalid legacy URL may contain credentials in user-info
                    # or query form.  Omit only that nonessential setting; the
                    # learner's data and other safe preferences remain portable.
                    continue
        _secret_scan_text(str(value), path=key)
        normalized[key] = value
    return normalized


def _portable_path(relative: str) -> tuple[bool, str]:
    parts = [part for part in relative.replace("\\", "/").split("/") if part]
    if not parts:
        return False, "empty path"
    lowered = [part.casefold() for part in parts]
    name = lowered[-1]
    if any(
        part in _GENERATED_INDEX_DIR_NAMES
        or part.startswith("index_")
        or part.startswith("index-")
        or part.endswith(("_index", "-index"))
        for part in lowered[:-1]
    ):
        return False, "generated index data is not portable"
    if (
        any(marker in name for marker in _GENERATED_INDEX_FILE_MARKERS)
        or name.endswith((".index", ".idx", ".index.db", ".index.sqlite", ".index.sqlite3"))
    ):
        return False, "generated index data is not portable"
    if any(part in _DISPOSABLE_DIR_NAMES for part in lowered):
        return False, "backup/temp/review/cache data is not portable"
    if (
        name.endswith(_DISPOSABLE_FILE_SUFFIXES)
        or name.endswith(".zip")
        or name.endswith(("-wal", "-shm"))
    ):
        return False, "backup/temp/archive output is not portable"
    if lowered[0] not in _PORTABLE_TOP_LEVEL_DIRS and name not in _PORTABLE_TOP_LEVEL_FILES:
        return False, "not an allowlisted live data/config path"
    if name.endswith((".key", ".pem", ".p12", ".pfx")) or name in {
        "secrets.json",
        "credentials.json",
        "credential.json",
        "provider_session.json",
        "provider_sessions.json",
        "provider-session.json",
        "provider-sessions.json",
    }:
        return False, "credential material is not portable"
    return True, ""


def _should_sanitize_json_member(relative: str) -> bool:
    """Return whether a staged member is a portable preset/config JSON."""

    normalized = relative.replace("\\", "/").casefold()
    if normalized in {"preset_settings.json", "desktop_settings.json"}:
        return True
    return normalized.startswith("presets/") and normalized.endswith(".json")


def _iter_files(
    root: Path,
    archive_destination: Path | None,
    *,
    _directory_locks: ExitStack | None = None,
    _expected_root_info: os.stat_result | None = None,
    _file_infos: dict[Path, os.stat_result] | None = None,
) -> tuple[list[Path], list[str]]:
    root = _lexical_path(root)
    _validate_parent_chain(root.parent, role="数据")
    root_info = _validate_existing_entry(root, role="数据目录")
    if root_info is None or not stat.S_ISDIR(root_info.st_mode):
        raise MigrationError(f"本地数据目录不存在：{root}")
    if _expected_root_info is not None and not _same_file_identity(root_info, _expected_root_info):
        raise MigrationError(f"迁移数据目录在打开前发生变化：{root}")
    files: list[Path] = []
    excluded: list[str] = []
    destination = _lexical_path(archive_destination) if archive_destination else None
    destination_lexical = None
    if destination is not None:
        _validate_parent_chain(destination.parent, role="导出目标")
        destination_lexical = destination
    pending: list[tuple[Path, os.stat_result]] = [(root, root_info)]
    while pending:
        current, expected_current_info = pending.pop()
        if _directory_locks is not None:
            _directory_locks.enter_context(
                _hold_directory_chain(
                    current,
                    role="数据",
                    expected_info=expected_current_info,
                )
            )
        _assert_within(current, root, role="数据")
        try:
            children = sorted(current.iterdir(), key=lambda item: item.as_posix())
        except OSError as exc:
            raise MigrationError(f"无法读取迁移数据目录：{current}（{exc}）") from exc
        for path in children:
            relative = path.relative_to(root).as_posix()
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise MigrationError(f"无法检查迁移数据路径：{path}（{exc}）") from exc
            if _is_reparse_info(info):
                excluded.append(f"{relative}: reparse point or symlink")
                continue
            _assert_within(path, root, role="数据")
            if stat.S_ISDIR(info.st_mode):
                pending.append((path, info))
                continue
            if not stat.S_ISREG(info.st_mode):
                excluded.append(f"{relative}: special file is not portable")
                continue
            if _is_hardlink_info(info):
                raise MigrationError(f"拒绝迁移源中的硬链接文件：{path}")
            if destination_lexical is not None and _lexical_path(path) == destination_lexical:
                excluded.append(f"{relative}: selected archive destination")
                continue
            allowed, reason = _portable_path(relative)
            if not allowed:
                excluded.append(f"{relative}: {reason}")
                continue
            files.append(path)
            if _file_infos is not None:
                _file_infos[path] = info
    return files, excluded


def _snapshot_file(
    source: Path,
    destination: Path,
    *,
    expected_source_info: os.stat_result | None = None,
    sanitize_sqlite: bool = False,
    sanitize_json: bool = False,
    relative: str | None = None,
) -> None:
    """Snapshot one source entry through a pinned, identity-checked handle."""

    source = _lexical_path(source)
    destination = _lexical_path(destination)
    _validate_parent_chain(source.parent, role="源")
    source_info = expected_source_info or _validate_existing_entry(source, role="源文件")
    if source_info is None or not stat.S_ISREG(source_info.st_mode):
        raise MigrationError(f"迁移源文件不是普通文件：{source}")
    _validate_parent_chain(destination.parent, role="暂存目标")
    _validate_existing_entry(destination, role="暂存目标", allow_missing=True)
    _ensure_directory(destination.parent, role="暂存目标")
    _validate_parent_chain(destination.parent, role="暂存目标")
    if source.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
        created_identity: tuple[int, int] | None = None
        try:
            # Keep both no-follow handles open while SQLite performs its
            # transactional backup.  The source handle pins the selected
            # inode against replacement; the destination handle prevents a
            # raced hardlink from being opened/truncated by SQLite.
            with _open_verified_file(source, role="源文件", expected_info=source_info):
                with _open_verified_file(
                    destination,
                    role="暂存目标",
                    create_new=True,
                    mode="w+b",
                ) as destination_stream:
                    created_identity = _file_identity(os.fstat(destination_stream.fileno()))
                    source_connection = sqlite3.connect(
                        source.as_uri() + "?mode=ro",
                        uri=True,
                    )
                    target_connection = sqlite3.connect(destination)
                    try:
                        source_connection.backup(target_connection)
                        integrity = str(target_connection.execute("PRAGMA integrity_check").fetchone()[0])
                        if integrity != "ok":
                            raise MigrationError(f"数据库完整性检查失败：{source.name} ({integrity})")
                    except sqlite3.Error as exc:
                        raise MigrationError(f"数据库完整性检查失败：{source.name}") from exc
                    finally:
                        target_connection.close()
                        source_connection.close()
                    destination_stream.flush()
        except MigrationError:
            if created_identity is not None:
                try:
                    current = _validate_existing_entry(destination, role="暂存目标", allow_missing=True)
                    if current is not None and _file_identity(current) == created_identity:
                        destination.unlink(missing_ok=True)
                except (MigrationError, OSError):
                    pass
            raise
        if sanitize_sqlite:
            _sanitize_sqlite_snapshot(destination)
            _scan_portable_secret(destination, relative=source.name)
            _verify_sqlite_integrity(destination)
        _validate_existing_entry(destination, role="暂存目标")
        return
    if sanitize_json:
        _snapshot_json_file(
            source,
            destination,
            expected_source_info=source_info,
            relative=relative or source.name,
        )
        _validate_existing_entry(destination, role="暂存目标")
        return
    _copy_file_no_follow(
        source,
        destination,
        expected_source_info=source_info,
        role="源文件",
    )
    _validate_existing_entry(destination, role="暂存目标")


def export_migration_archive(
    data_root: Path,
    destination: Path,
    *,
    safe_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a portable ZIP at ``destination`` without overwriting it.

    ``safe_settings`` is an optional, already-selected Qt settings subset.
    The migration layer validates it again and writes it as a normal member;
    no Credential Locker value or provider session is ever accepted.
    """

    requested_root = _lexical_path(data_root.expanduser())
    _validate_parent_chain(requested_root.parent, role="数据")
    root_info = _validate_existing_entry(requested_root, role="数据目录")
    if root_info is None or not stat.S_ISDIR(root_info.st_mode):
        raise MigrationError(f"本地数据目录不存在：{requested_root}")
    # Keep the source root lexical.  The identity captured above is carried
    # into traversal, where it is checked again while the directory chain is
    # pinned; resolving here would reopen the lstat/resolve race.
    root = requested_root
    output = _lexical_path(destination.expanduser())
    _validate_parent_chain(output.parent, role="导出目标")
    if _validate_existing_entry(output, role="导出目标", allow_missing=True) is not None:
        raise MigrationError(f"目标文件已存在，请选择新的文件名：{output}")
    _ensure_directory(output.parent, role="导出目标")
    output_parent_info = _validate_existing_entry(output.parent, role="导出目标")
    assert output_parent_info is not None
    temp_root = _ensure_directory(
        Path(os.getenv("MENTORFLOW_TEMP_ROOT", tempfile.gettempdir())),
        role="迁移暂存",
    )
    temp_root_info = _validate_existing_entry(temp_root, role="迁移暂存")
    assert temp_root_info is not None
    with _hold_directory_chain(
        temp_root,
        role="迁移暂存",
        expected_info=temp_root_info,
    ):
        staging = _lexical_path(
            Path(tempfile.mkdtemp(prefix="mentorflow-migration-", dir=temp_root))
        )
    staging_info = _validate_existing_entry(staging, role="迁移暂存")
    assert staging_info is not None
    _assert_within(staging, temp_root, role="迁移暂存")
    staging_locks = ExitStack()
    try:
        staging_locks.enter_context(
            _hold_directory_chain(
                staging,
                role="迁移暂存",
                expected_info=staging_info,
            )
        )
    except BaseException:
        staging_locks.close()
        _cleanup_private_tree(staging, parent_info=temp_root_info, role="迁移暂存清理")
        raise
    temporary_archive = output.with_name(output.name + f".{uuid.uuid4().hex}.part")
    _validate_parent_chain(temporary_archive.parent, role="导出目标")
    if _validate_existing_entry(temporary_archive, role="导出暂存文件", allow_missing=True) is not None:
        raise MigrationError(f"导出暂存文件已存在：{temporary_archive}")
    temporary_archive_identity: tuple[int, int] | None = None
    try:
        with ExitStack() as source_locks:
            source_file_infos: dict[Path, os.stat_result] = {}
            files, excluded = _iter_files(
                root,
                output,
                _directory_locks=source_locks,
                _expected_root_info=root_info,
                _file_infos=source_file_infos,
            )
            entries: list[dict[str, Any]] = []
            for source in files:
                relative = source.relative_to(root).as_posix()
                if safe_settings is not None and relative.casefold() == SAFE_SETTINGS_MEMBER.casefold():
                    excluded.append(f"{relative}: replaced by the validated Qt settings snapshot")
                    continue
                staged = staging / Path(relative)
                _snapshot_file(
                    source,
                    staged,
                    expected_source_info=source_file_infos.get(source),
                    sanitize_sqlite=True,
                    sanitize_json=_should_sanitize_json_member(relative),
                    relative=relative,
                )
                if source.suffix.casefold() not in {".db", ".sqlite", ".sqlite3"}:
                    _scan_portable_secret(staged, relative=relative)
                entries.append(
                    {
                        "path": relative,
                        "bytes": staged.stat().st_size,
                        "sha256": _sha256(staged),
                    }
                )
            if safe_settings is not None:
                settings_payload = _validate_safe_settings(safe_settings)
                settings_staged = staging / SAFE_SETTINGS_MEMBER
                _write_new_text_no_follow(
                    settings_staged,
                    json.dumps(settings_payload, ensure_ascii=False, indent=2, sort_keys=True),
                    role="安全设置暂存",
                )
                _scan_portable_secret(settings_staged, relative=SAFE_SETTINGS_MEMBER)
                entries.append(
                    {
                        "path": SAFE_SETTINGS_MEMBER,
                        "bytes": settings_staged.stat().st_size,
                        "sha256": _sha256(settings_staged),
                    }
                )
        manifest: dict[str, Any] = {
            "format": MIGRATION_FORMAT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_label": root.name or "MentorFlowData",
            "files": entries,
            "excluded": excluded,
            "credentials": "Windows Credential Locker references are not exported; enter API keys again on a new device.",
            "credential_policy": "fail-closed; raw keys, credential references, and provider sessions are never portable",
            "archive_policy": "plain ZIP without encryption or signing claims",
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        with _hold_directory_chain(
            output.parent,
            role="导出目标",
            expected_info=output_parent_info,
        ):
            with zipfile.ZipFile(temporary_archive, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST_NAME, manifest_bytes)
                for item in entries:
                    archive.write(staging / Path(item["path"]), item["path"])
            temporary_archive_info = _validate_existing_entry(temporary_archive, role="导出暂存文件")
            assert temporary_archive_info is not None
            temporary_archive_identity = _file_identity(temporary_archive_info)
            _validate_parent_chain(output.parent, role="导出目标")
            if _validate_existing_entry(output, role="导出目标", allow_missing=True) is not None:
                raise MigrationError(f"目标文件已存在，请选择新的文件名：{output}")
            try:
                os.replace(temporary_archive, output)
            except OSError as exc:
                raise MigrationError(f"导出目标在发布过程中发生变化：{output}（{exc}）") from exc
        _validate_existing_entry(output, role="导出目标")
        return {
            "archive": output,
            "files": len(entries),
            "excluded": excluded,
            "bytes": output.stat().st_size,
        }
    except Exception:
        _cleanup_private_file(
            temporary_archive,
            parent_info=output_parent_info,
            expected_identity=temporary_archive_identity,
            role="导出暂存文件清理",
        )
        raise
    finally:
        staging_locks.close()
        _cleanup_private_tree(
            staging,
            parent_info=temp_root_info,
            role="迁移暂存清理",
        )


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    if not normalized or normalized.startswith("/") or path.drive or ".." in path.parts:
        raise MigrationError(f"迁移包包含不安全路径：{name}")
    return normalized


def import_migration_archive(archive_path: Path, target_root: Path, *, test_only: bool = False) -> dict[str, Any]:
    """Verify and extract a migration ZIP into a new or empty directory."""

    from .account_storage import require_migration_target
    try:
        require_migration_target(target_root, test_only=test_only)
    except ValueError as exc:
        raise MigrationError(str(exc)) from None
    requested_source = _lexical_path(archive_path.expanduser())
    requested_target = _lexical_path(target_root.expanduser())
    _validate_parent_chain(requested_source.parent, role="迁移包")
    source_info = _validate_existing_entry(requested_source, role="迁移包")
    if source_info is None or not stat.S_ISREG(source_info.st_mode):
        raise MigrationError("所选文件不是有效的 MentorFlow 迁移压缩包。")
    _validate_parent_chain(requested_target.parent, role="导入目标")
    # The staging directory must be a sibling of the final target.  Using the
    # process-wide temp root here can put staging on another volume, making
    # the final ``os.replace`` fail with EXDEV exactly when an import is most
    # needed.  Ensure the target parent one component at a time, then keep its
    # identity pinned while creating a private sibling staging directory.
    target_parent = _ensure_directory(requested_target.parent, role="导入目标")
    target_parent_info = _validate_existing_entry(target_parent, role="导入目标")
    assert target_parent_info is not None
    with _hold_directory_chain(
        target_parent,
        role="导入目标",
        expected_info=target_parent_info,
    ):
        target_info = _validate_existing_entry(requested_target, role="导入目标", allow_missing=True)
        if target_info is not None and not stat.S_ISDIR(target_info.st_mode):
            raise MigrationError(f"导入目标必须是新目录或空目录：{requested_target}")
        if target_info is not None and any(requested_target.iterdir()):
            raise MigrationError(f"导入目标必须是新目录或空目录：{requested_target}")
        staging = _lexical_path(
            Path(tempfile.mkdtemp(prefix=".mentorflow-import-", dir=target_parent))
        )
        staging_info = _validate_existing_entry(staging, role="迁移暂存")
        assert staging_info is not None
        _assert_within(staging, target_parent, role="迁移暂存")
    initial_target_info = target_info
    # Keep the target lexical.  Resolving here would follow a concurrently
    # installed junction before the publication lock can reject it.
    target = requested_target
    source_snapshot = staging / ".mentorflow-source.zip"
    staging_locks = ExitStack()
    try:
        staging_locks.enter_context(
            _hold_directory_chain(
                staging,
                role="迁移暂存",
                expected_info=staging_info,
            )
        )
    except BaseException:
        staging_locks.close()
        _cleanup_private_tree(staging, parent_info=target_parent_info, role="迁移暂存清理")
        raise
    try:
        # Copy the selected archive through a no-follow handle before asking
        # ``zipfile`` to parse it.  The path may be replaced after the initial
        # lstat; parsing the owned snapshot means a junction/hardlink race can
        # only fail closed and can never redirect reads outside the selection.
        _snapshot_file(
            requested_source,
            source_snapshot,
            expected_source_info=source_info,
        )
        source = source_snapshot
        if not zipfile.is_zipfile(source):
            raise MigrationError("所选文件不是有效的 MentorFlow 迁移压缩包。")
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise MigrationError("迁移包文件数量超过安全上限。")
            names: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                name = _safe_member_name(info.filename.rstrip("/"))
                if not name:
                    continue
                if name in names:
                    raise MigrationError(f"迁移包包含重复文件：{name}")
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise MigrationError(f"迁移包包含符号链接：{name}")
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise MigrationError("迁移包不支持加密 ZIP 成员；请使用普通 ZIP。")
                if info.file_size > 0 and info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                    raise MigrationError(f"迁移包压缩比例不安全：{name}")
                names[name] = info
            if MANIFEST_NAME not in names:
                raise MigrationError("迁移包缺少清单文件。")
            if names[MANIFEST_NAME].file_size > MAX_MANIFEST_BYTES:
                raise MigrationError("迁移包清单超过安全上限。")
            try:
                manifest = json.loads(archive.read(names[MANIFEST_NAME]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MigrationError("迁移包清单无法读取。") from exc
            if not isinstance(manifest, dict):
                raise MigrationError("迁移包清单必须是对象。")
            # The explanatory ``credentials`` field is retained for v1
            # compatibility, but arbitrary credential-looking manifest data
            # is not accepted.
            for key, value in manifest.items():
                if str(key).casefold() in {
                    "api_key",
                    "apikey",
                    "credential_ref",
                    "provider_session_id",
                    "provider_session",
                    "secret",
                    "password",
                }:
                    raise MigrationError("迁移包清单包含凭据字段。")
                if isinstance(value, str):
                    _secret_scan_text(value, path=MANIFEST_NAME)
            if manifest.get("format") != MIGRATION_FORMAT:
                raise MigrationError("迁移包版本不受当前软件支持。")
            declared = manifest.get("files")
            if not isinstance(declared, list):
                raise MigrationError("迁移包清单缺少文件列表。")
            expected: dict[str, dict[str, Any]] = {}
            for entry in declared:
                if not isinstance(entry, dict):
                    raise MigrationError("迁移包清单包含无效条目。")
                name = _safe_member_name(str(entry.get("path") or ""))
                if name in expected or name == MANIFEST_NAME:
                    raise MigrationError(f"迁移包清单包含重复文件：{name}")
                allowed, reason = _portable_path(name)
                if not allowed:
                    raise MigrationError(f"迁移包包含非便携路径：{name}（{reason}）")
                expected[name] = entry
            if set(expected) != set(names) - {MANIFEST_NAME}:
                raise MigrationError("迁移包内容与清单不一致。")
            total = 0
            for name, entry in expected.items():
                info = names[name]
                declared_bytes = int(entry.get("bytes", -1))
                if declared_bytes < 0 or info.file_size != declared_bytes:
                    raise MigrationError(f"文件大小校验失败：{name}")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise MigrationError("迁移包超过安全容量上限。")
                destination = staging / Path(name)
                _assert_within(destination, staging, role="导入暂存")
                _validate_parent_chain(destination.parent, role="导入暂存")
                _ensure_directory(destination.parent, role="导入暂存")
                _validate_parent_chain(destination.parent, role="导入暂存")
                if _validate_existing_entry(destination, role="导入暂存", allow_missing=True) is not None:
                    raise MigrationError(f"迁移暂存文件已存在：{name}")
                with archive.open(info, "r") as input_stream:
                    with _open_verified_file(
                        destination,
                        role="导入暂存",
                        create_new=True,
                        mode="wb",
                    ) as output:
                        shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                        output.flush()
                _validate_existing_entry(destination, role="导入暂存")
                if _sha256(destination) != str(entry.get("sha256") or ""):
                    raise MigrationError(f"文件校验失败：{name}")
                _scan_portable_secret(destination, relative=name)
                if destination.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
                    _verify_sqlite_integrity(destination)
        with _hold_directory_chain(
            target.parent,
            role="导入目标",
            expected_info=target_parent_info,
        ):
            _validate_parent_chain(target.parent, role="导入目标")
            target_parent_info = _validate_existing_entry(target.parent, role="导入目标")
            assert target_parent_info is not None
            current_target_info = _validate_existing_entry(target, role="导入目标", allow_missing=True)
            if initial_target_info is None:
                if current_target_info is not None:
                    raise MigrationError(f"导入目标在过程中发生变化：{target}")
                try:
                    target.mkdir(exist_ok=False)
                except OSError as exc:
                    raise MigrationError(f"导入目标在过程中发生变化：{target}（{exc}）") from exc
                target_info = _validate_existing_entry(target, role="导入目标")
            else:
                if (
                    current_target_info is None
                    or not _same_file_identity(current_target_info, initial_target_info)
                    or not stat.S_ISDIR(current_target_info.st_mode)
                    or any(target.iterdir())
                ):
                    raise MigrationError(f"导入目标在过程中发生变化：{target}")
                target_info = current_target_info
            if target_info is None or not stat.S_ISDIR(target_info.st_mode):
                raise MigrationError(f"导入目标在过程中发生变化：{target}")
        # Pin the destination itself and all parents while publishing each
        # already-verified staged entry.  ``shutil.move`` may recurse into a
        # destination junction; a direct rename only replaces the directory
        # entry and therefore cannot write through it.
        published: list[tuple[Path, tuple[int, int]]] = []
        try:
            with _hold_directory_chain(
                target.parent,
                role="导入目标",
                expected_info=target_parent_info,
            ):
                with _hold_directory_chain(
                    target,
                    role="导入目标",
                    expected_info=target_info,
                ):
                    for item in sorted(staging.iterdir(), key=lambda path: path.name):
                        if item == source_snapshot:
                            continue
                        _validate_existing_entry(item, role="导入暂存")
                        destination = target / item.name
                        _assert_within(destination, target, role="导入目标")
                        _validate_parent_chain(destination.parent, role="导入目标")
                        if _validate_existing_entry(destination, role="导入目标", allow_missing=True) is not None:
                            raise MigrationError(f"导入目标在过程中发生变化：{destination}")
                        try:
                            os.replace(item, destination)
                        except OSError as exc:
                            raise MigrationError(f"导入目标在发布过程中发生变化：{destination}（{exc}）") from exc
                        destination_info = _validate_existing_entry(destination, role="导入目标")
                        assert destination_info is not None
                        published.append((destination, _file_identity(destination_info)))
        except Exception:
            # Publication is all-or-nothing for a new/empty target.  Move any
            # entries that were already published back under the owned staging
            # directory before the final cleanup; a raced replacement with a
            # different identity is left untouched and cannot be an outside
            # write by this process.
            try:
                with _hold_directory_chain(
                    target.parent,
                    role="导入目标回滚",
                    expected_info=target_parent_info,
                ):
                    with _hold_directory_chain(
                        target,
                        role="导入目标回滚",
                        expected_info=target_info,
                    ):
                        for destination, identity in reversed(published):
                            try:
                                info = _validate_existing_entry(
                                    destination,
                                    role="导入目标回滚",
                                    allow_missing=True,
                                )
                                if info is None or _file_identity(info) != identity:
                                    continue
                                rollback = staging / f".rollback-{uuid.uuid4().hex}"
                                os.replace(destination, rollback)
                            except (MigrationError, OSError):
                                continue
            except (MigrationError, OSError):
                pass
            raise
        return {"target": target, "files": len(expected), "bytes": total}
    finally:
        staging_locks.close()
        _cleanup_private_tree(
            staging,
            parent_info=target_parent_info,
            role="迁移暂存清理",
        )


def _verify_sqlite_integrity(path: Path) -> None:
    """Fail closed before publishing an imported SQLite database."""

    try:
        info = _validate_existing_entry(path, role="导入数据库")
        if info is None or not stat.S_ISREG(info.st_mode):
            raise MigrationError(f"导入数据库完整性检查失败：{path.name}")
        with _open_verified_file(path, role="导入数据库", expected_info=info):
            connection = sqlite3.connect(path)
            try:
                result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                connection.close()
    except (MigrationError, sqlite3.Error) as exc:
        raise MigrationError(f"导入数据库完整性检查失败：{path.name}") from exc
    if result.casefold() != "ok":
        raise MigrationError(f"导入数据库完整性检查失败：{path.name} ({result})")


def write_next_launch_data_root_pointer(current_root: Path, target_root: Path) -> Path:
    """Publish a discoverable handoff without changing the running data root."""

    requested_current = _lexical_path(current_root.expanduser())
    requested_target = _lexical_path(target_root.expanduser())
    _validate_parent_chain(requested_current.parent, role="当前数据目录")
    current_info = _validate_existing_entry(requested_current, role="当前数据目录", allow_missing=True)
    if current_info is not None and not stat.S_ISDIR(current_info.st_mode):
        raise MigrationError(f"当前数据目录必须是目录：{requested_current}")
    _validate_parent_chain(requested_target.parent, role="下一次启动数据目录")
    target_info = _validate_existing_entry(requested_target, role="下一次启动数据目录", allow_missing=True)
    if target_info is not None and not stat.S_ISDIR(target_info.st_mode):
        raise MigrationError(f"下一次启动数据目录必须是目录：{requested_target}")
    # Keep both paths lexical until the parent locks are held.  Resolving a
    # path in the race window could follow a newly installed junction.
    current = requested_current
    target = requested_target
    if current == target:
        raise MigrationError("下一次启动目录不能是当前正在使用的目录。")
    initial_current_info = current_info
    current = _ensure_directory(current, role="当前数据目录")
    current_info = _validate_existing_entry(current, role="当前数据目录")
    assert current_info is not None
    if initial_current_info is not None and not _same_file_identity(current_info, initial_current_info):
        raise MigrationError(f"当前数据目录在创建指针前发生变化：{current}")
    pointer = current / NEXT_LAUNCH_POINTER_NAME
    _validate_parent_chain(pointer.parent, role="下一次启动指针")
    _validate_existing_entry(pointer, role="下一次启动指针", allow_missing=True)
    payload = {
        "format": "mentorflow-next-data-root-v1",
        "data_root": str(target),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = pointer.with_name(pointer.name + f".{uuid.uuid4().hex}.part")
    _validate_parent_chain(temporary.parent, role="下一次启动指针")
    if _validate_existing_entry(temporary, role="下一次启动指针暂存", allow_missing=True) is not None:
        raise MigrationError(f"下一次启动指针暂存文件已存在：{temporary}")
    target_parent_info = _validate_existing_entry(
        target.parent,
        role="下一次启动数据目录",
        allow_missing=True,
    )
    temporary_identity: tuple[int, int] | None = None
    try:
        with ExitStack() as locks:
            if target_parent_info is not None:
                locks.enter_context(
                    _hold_directory_chain(
                        target.parent,
                        role="下一次启动数据目录",
                        expected_info=target_parent_info,
                    )
                )
            if target_info is not None:
                locks.enter_context(
                    _hold_directory_chain(
                        target,
                        role="下一次启动数据目录",
                        expected_info=target_info,
                    )
                )
            locks.enter_context(
                _hold_directory_chain(
                    pointer.parent,
                    role="下一次启动指针",
                    expected_info=current_info,
                )
            )
            _write_new_text_no_follow(
                temporary,
                json.dumps(payload, ensure_ascii=False, indent=2),
                role="下一次启动指针暂存",
            )
            temporary_info = _validate_existing_entry(temporary, role="下一次启动指针暂存")
            assert temporary_info is not None
            temporary_identity = _file_identity(temporary_info)
            _validate_parent_chain(pointer.parent, role="下一次启动指针")
            _validate_existing_entry(pointer, role="下一次启动指针", allow_missing=True)
            try:
                os.replace(temporary, pointer)
            except OSError as exc:
                raise MigrationError(f"下一次启动指针发布失败：{pointer}（{exc}）") from exc
            _validate_existing_entry(pointer, role="下一次启动指针")
    finally:
        temporary_parent_info: os.stat_result | None = None
        try:
            temporary_parent_info = _validate_existing_entry(
                pointer.parent,
                role="下一次启动指针",
                allow_missing=True,
            )
        except (MigrationError, OSError):
            pass
        _cleanup_private_file(
            temporary,
            parent_info=temporary_parent_info,
            expected_identity=temporary_identity,
            role="下一次启动指针暂存清理",
        )
    return pointer
