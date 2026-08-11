"""Single-handle immutable writes for local release evidence.

Windows targets are created with ``CREATE_NEW`` and held without
``FILE_SHARE_WRITE`` or ``FILE_SHARE_DELETE`` from the first write through the
durable flush, rewind, read-back digest, size check, and FileId check.  Existing
targets are verified through one equally restrictive read handle.  POSIX uses
the same file descriptor for the equivalent lifecycle.  Different bytes never
replace an existing target; the requested bytes are retained in an adjacent
``.audit.tmp`` file for review.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


FILE_ATTRIBUTE_DIRECTORY = 0x0010
FILE_ATTRIBUTE_NORMAL = 0x0080
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class ImmutableEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str, *, temporary_path: Path | None = None):
        super().__init__(message)
        self.code = code
        self.temporary_path = temporary_path


@dataclass(frozen=True)
class FileIdentity:
    volume: int
    file_id: int
    size: int


def _is_reparse_or_link(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(value, "st_file_attributes", 0))
    return stat.S_ISLNK(value.st_mode) or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


if os.name == "nt":
    from ctypes import wintypes

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
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

    _KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _KERNEL32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _KERNEL32.FlushFileBuffers.restype = wintypes.BOOL


def _win_information(handle: int) -> "_BY_HANDLE_FILE_INFORMATION":
    info = _BY_HANDLE_FILE_INFORMATION()
    if not _KERNEL32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
    return info


def _win_identity(handle: int) -> FileIdentity:
    info = _win_information(handle)
    return FileIdentity(
        int(info.dwVolumeSerialNumber),
        (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow),
    )


def _fd_identity(fd: int) -> FileIdentity:
    if os.name == "nt":
        import msvcrt

        return _win_identity(msvcrt.get_osfhandle(fd))
    value = os.fstat(fd)
    return FileIdentity(int(value.st_dev), int(value.st_ino), int(value.st_size))


def _ancestor_paths(parent: Path) -> list[Path]:
    absolute = Path(os.path.abspath(parent))
    paths: list[Path] = []
    current = Path(absolute.anchor)
    if not current.anchor:
        raise ImmutableEvidenceError("IMMUTABLE_PATH_INVALID", f"path is not absolute: {parent}")
    paths.append(current)
    for part in absolute.parts[1:]:
        current = current / part
        paths.append(current)
    return paths


@contextlib.contextmanager
def _held_safe_ancestors(parent: Path) -> Iterator[None]:
    """Create missing ancestors and hold each one against rename/delete."""

    held: list[int] = []
    try:
        for directory in _ancestor_paths(parent):
            if not directory.exists():
                try:
                    os.mkdir(directory)
                except FileExistsError:
                    pass
            if _is_reparse_or_link(directory):
                raise ImmutableEvidenceError(
                    "IMMUTABLE_REPARSE_FORBIDDEN",
                    f"output ancestor is a symlink or reparse point: {directory}",
                )
            if not directory.is_dir():
                raise ImmutableEvidenceError(
                    "IMMUTABLE_ANCESTOR_INVALID", f"output ancestor is not a directory: {directory}"
                )
            if os.name == "nt":
                handle = _KERNEL32.CreateFileW(
                    str(directory),
                    _FILE_READ_ATTRIBUTES,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                    None,
                    _OPEN_EXISTING,
                    _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
                    None,
                )
                if handle == _INVALID_HANDLE_VALUE:
                    raise OSError(ctypes.get_last_error(), f"cannot hold output ancestor: {directory}")
                info = _win_information(int(handle))
                if int(info.dwFileAttributes) & FILE_ATTRIBUTE_REPARSE_POINT:
                    _KERNEL32.CloseHandle(handle)
                    raise ImmutableEvidenceError(
                        "IMMUTABLE_REPARSE_FORBIDDEN",
                        f"output ancestor handle resolves to a reparse point: {directory}",
                    )
                held.append(int(handle))
            else:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                held.append(os.open(directory, flags))
        yield
    finally:
        for handle in reversed(held):
            if os.name == "nt":
                _KERNEL32.CloseHandle(handle)
            else:
                os.close(handle)


@contextlib.contextmanager
def held_safe_ancestors(parent: Path) -> Iterator[None]:
    """Public wrapper that holds a plain ancestor chain against replacement.

    Callers that create a directory tree before committing immutable evidence
    can use the same Windows reparse/rename protections as ``immutable_write``.
    Missing directories in the supplied ancestor chain are created. Callers
    that need a previously nonexistent output leaf must pass its parent here,
    create the leaf with an exclusive operation, and then hold the leaf in a
    second context.
    """

    with _held_safe_ancestors(parent):
        yield


def _windows_fd(path: Path, *, create: bool) -> int:
    import msvcrt

    access = _GENERIC_READ | (_GENERIC_WRITE if create else 0)
    handle = _KERNEL32.CreateFileW(
        str(path),
        access,
        _FILE_SHARE_READ,
        None,
        _CREATE_NEW if create else _OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if create and error in {80, 183}:
            raise FileExistsError(error, "immutable target already exists", str(path))
        raise OSError(error, f"cannot {'create' if create else 'open'} immutable target: {path}")
    handle_value = int(handle)
    try:
        info = _win_information(handle_value)
        if int(info.dwFileAttributes) & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY):
            raise ImmutableEvidenceError(
                "IMMUTABLE_TARGET_INVALID", f"target is not a plain file: {path}"
            )
        flags = (os.O_RDWR if create else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
        return msvcrt.open_osfhandle(handle_value, flags)
    except BaseException:
        _KERNEL32.CloseHandle(handle_value)
        raise


def _create_new_fd(path: Path) -> int:
    if os.name == "nt":
        return _windows_fd(path, create=True)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    return os.open(path, flags, 0o600)


def _open_existing_fd(path: Path) -> int:
    if os.name == "nt":
        return _windows_fd(path, create=False)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ImmutableEvidenceError("IMMUTABLE_TARGET_INVALID", f"target is not a plain file: {path}")
    return fd


def _write_all(fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "short immutable evidence write")
        offset += written


def _flush_fd(fd: int) -> None:
    os.fsync(fd)
    if os.name == "nt":
        import msvcrt

        if not _KERNEL32.FlushFileBuffers(msvcrt.get_osfhandle(fd)):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")


def _read_digest_from_same_fd(fd: int) -> tuple[int, str]:
    os.lseek(fd, 0, os.SEEK_SET)
    size = 0
    digest = hashlib.sha256()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        size += len(block)
        digest.update(block)
    return size, digest.hexdigest()


def _verify_same_fd(
    fd: int,
    *,
    path: Path,
    expected_content: bytes,
    initial_identity: FileIdentity,
) -> bool:
    read_size, read_sha256 = _read_digest_from_same_fd(fd)
    final_identity = _fd_identity(fd)
    if (
        final_identity.volume != initial_identity.volume
        or final_identity.file_id != initial_identity.file_id
    ):
        raise ImmutableEvidenceError(
            "IMMUTABLE_FILE_ID_CHANGED", f"immutable target FileId changed while held: {path}"
        )
    expected_sha256 = hashlib.sha256(expected_content).hexdigest()
    return (
        final_identity.size == len(expected_content)
        and read_size == len(expected_content)
        and read_sha256 == expected_sha256
    )


def _write_new_path(path: Path, content: bytes) -> None:
    fd = _create_new_fd(path)
    initial_identity = _fd_identity(fd)
    try:
        _write_all(fd, content)
        _flush_fd(fd)
        if not _verify_same_fd(
            fd,
            path=path,
            expected_content=content,
            initial_identity=initial_identity,
        ):
            raise ImmutableEvidenceError(
                "IMMUTABLE_CONTENT_MISMATCH",
                f"immutable target content changed while its committed handle was held: {path}",
                temporary_path=path,
            )
    finally:
        os.close(fd)


def _preserve_audit_temp(target: Path, content: bytes) -> Path:
    while True:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.audit.tmp")
        try:
            _write_new_path(temporary, content)
            return temporary
        except FileExistsError:
            continue


def immutable_write(path: Path, content: bytes) -> None:
    """Create *path* once or verify identical bytes without reopening a handle."""

    target = Path(os.path.abspath(path))
    if target.parent == target:
        raise ImmutableEvidenceError("IMMUTABLE_PATH_INVALID", "output cannot be a filesystem root")
    with _held_safe_ancestors(target.parent):
        try:
            _write_new_path(target, content)
            return
        except FileExistsError:
            pass
        except ImmutableEvidenceError:
            raise
        except BaseException as exc:
            raise ImmutableEvidenceError(
                "IMMUTABLE_WRITE_FAILED",
                f"single-handle immutable write failed; target retained at {target}: {exc}",
                temporary_path=target if target.exists() else None,
            ) from exc

        try:
            existing_fd = _open_existing_fd(target)
        except ImmutableEvidenceError:
            raise
        except BaseException as exc:
            try:
                temporary = _preserve_audit_temp(target, content)
            except BaseException:
                temporary = None
            raise ImmutableEvidenceError(
                "IMMUTABLE_TARGET_BUSY",
                f"existing immutable target could not be held without write/delete sharing: {target}: {exc}",
                temporary_path=temporary,
            ) from exc
        try:
            initial_identity = _fd_identity(existing_fd)
            matches = _verify_same_fd(
                existing_fd,
                path=target,
                expected_content=content,
                initial_identity=initial_identity,
            )
            if matches:
                return
            temporary = _preserve_audit_temp(target, content)
            raise ImmutableEvidenceError(
                "IMMUTABLE_TARGET_EXISTS",
                f"refusing to replace different immutable evidence: {target}",
                temporary_path=temporary,
            )
        finally:
            os.close(existing_fd)
