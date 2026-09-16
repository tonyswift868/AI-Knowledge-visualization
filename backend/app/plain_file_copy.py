"""Fail-closed plain-file copies for Windows-portable release material.

Windows' legacy 260-character path boundary is still relevant on clean
machines whose policy has not enabled long paths.  The copy primitive itself
uses the Win32 wide-character API with extended-length paths, while callers
must separately enforce an install-root budget for the resulting package.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import stat
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


class PlainFileCopyError(RuntimeError):
    """Stable error raised when an exact plain-file copy cannot be proved."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PlainFileCopyResult:
    bytes: int
    sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with _native_path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        metadata = _native_path(path).lstat()
    except OSError as exc:
        raise PlainFileCopyError("COPY_PATH_STAT_FAILED", f"cannot stat copy path: {path}: {exc}") from exc
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0) or 0) & 0x400
    )


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def assert_no_reparse_ancestors(path: Path, boundary: Path) -> None:
    """Reject a lexical path whose existing boundary chain contains a link."""

    candidate = _absolute_lexical(path)
    root = _absolute_lexical(boundary)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PlainFileCopyError(
            "COPY_PATH_ESCAPE", f"copy path escapes its declared boundary: {candidate}"
        ) from exc
    current = root
    if _native_path(current).exists() and _is_reparse(current):
        raise PlainFileCopyError("COPY_REPARSE_FORBIDDEN", f"copy boundary is a reparse point: {current}")
    for part in relative.parts:
        current = current / part
        if _native_path(current).exists() and _is_reparse(current):
            raise PlainFileCopyError("COPY_REPARSE_FORBIDDEN", f"copy path uses a reparse point: {current}")


def _plain_file_snapshot(path: Path, *, label: str) -> tuple[tuple[int, int, int, int], str]:
    try:
        metadata = _native_path(path).lstat()
    except OSError as exc:
        raise PlainFileCopyError("COPY_FILE_STAT_FAILED", f"cannot stat {label}: {path}: {exc}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_reparse(path)
        or int(getattr(metadata, "st_nlink", 1) or 1) != 1
    ):
        raise PlainFileCopyError(
            "COPY_PLAIN_FILE_REQUIRED", f"{label} must be one regular non-reparse, non-hardlinked file: {path}"
        )
    try:
        digest = _sha256_file(path)
    except OSError as exc:
        raise PlainFileCopyError("COPY_FILE_READ_FAILED", f"cannot hash {label}: {path}: {exc}") from exc
    identity = (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(getattr(metadata, "st_nlink", 1) or 1),
    )
    return identity, digest


def _extended_windows_path(path: Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _native_path(path: Path) -> Path:
    return Path(_extended_windows_path(path)) if os.name == "nt" else path


def canonical_io_path(value: str | Path) -> Path:
    """Absolute identity for resource I/O; extended syntax grants no authority.

    Callers still enforce their account/workspace boundary. Relative paths and
    traversal are rejected before normalization, never silently made absolute.
    Only the existing _native_path converts identity to Win32 I/O spelling.
    """
    text = os.fspath(value)
    if os.name == "nt":
        text = text.replace("/", "\\")
        if text.upper().startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        if text.startswith(("\\\\.\\", "\\??\\")):
            raise ValueError("RESOURCE_DEVICE_PATH_FORBIDDEN")
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("RESOURCE_ABSOLUTE_PLAIN_PATH_REQUIRED")
    if os.name == "nt" and any(":" in part for part in path.parts[1:]):
        raise ValueError("RESOURCE_STREAM_PATH_FORBIDDEN")
    return path


@contextmanager
def open_resource_source(value: str | Path):
    """Open a user-selected plain source without changing its domain identity.

    This does not authorize an account destination or copy/delete the source.
    Stat, reparse checks, and open share the same native I/O representation.
    The descriptor must still identify the checked regular file before reads.
    """
    path = canonical_io_path(value)
    native = _native_path(path)
    before = native.lstat()  # Preserve FileNotFoundError for the product adapter.
    assert_no_reparse_ancestors(path, Path(path.anchor))
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("RESOURCE_PLAIN_FILE_REQUIRED")
    with native.open("rb") as stream:
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_nlink) != (
            after.st_dev, after.st_ino, after.st_nlink
        ) or not stat.S_ISREG(after.st_mode):
            raise ValueError("RESOURCE_SOURCE_CHANGED")
        yield path, stream


def _copy_file_windows(source: Path, destination: Path) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    copy_file = kernel32.CopyFileW
    copy_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.BOOL]
    copy_file.restype = wintypes.BOOL
    if not copy_file(
        _extended_windows_path(source),
        _extended_windows_path(destination),
        True,
    ):
        error = ctypes.get_last_error()
        raise PlainFileCopyError(
            "COPY_WIN32_FAILED",
            f"CopyFileW failed with Win32 error {error}: {source} -> {destination}",
        )


def copy_plain_file_exact(
    source: Path,
    destination: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
    source_root: Path,
    destination_root: Path,
) -> PlainFileCopyResult:
    """Copy once, never overwrite, and prove both endpoints stayed plain/exact."""

    source = _absolute_lexical(source)
    destination = _absolute_lexical(destination)
    source_root = _absolute_lexical(source_root)
    destination_root = _absolute_lexical(destination_root)
    assert_no_reparse_ancestors(source, source_root)
    assert_no_reparse_ancestors(destination.parent, destination_root)
    if os.path.lexists(_native_path(destination)):
        raise PlainFileCopyError("COPY_TARGET_EXISTS", f"copy target already exists: {destination}")
    before_identity, before_digest = _plain_file_snapshot(source, label="copy source")
    if before_identity[2] != expected_bytes or before_digest != expected_sha256:
        raise PlainFileCopyError("COPY_SOURCE_MISMATCH", f"copy source does not match its pinned bytes: {source}")
    try:
        _native_path(destination.parent).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PlainFileCopyError(
            "COPY_TARGET_DIRECTORY_FAILED", f"cannot create copy target directory: {destination.parent}: {exc}"
        ) from exc
    assert_no_reparse_ancestors(destination.parent, destination_root)
    try:
        if os.name == "nt":
            _copy_file_windows(source, destination)
        else:
            with source.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
    except PlainFileCopyError:
        raise
    except OSError as exc:
        raise PlainFileCopyError("COPY_FAILED", f"plain-file copy failed: {source} -> {destination}: {exc}") from exc
    after_identity, after_digest = _plain_file_snapshot(source, label="copy source")
    destination_identity, destination_digest = _plain_file_snapshot(destination, label="copy target")
    if after_identity != before_identity or after_digest != before_digest:
        raise PlainFileCopyError("COPY_SOURCE_CHANGED", f"copy source changed during copy: {source}")
    if destination_identity[2] != expected_bytes or destination_digest != expected_sha256:
        raise PlainFileCopyError("COPY_TARGET_MISMATCH", f"copy target differs from pinned bytes: {destination}")
    return PlainFileCopyResult(bytes=expected_bytes, sha256=expected_sha256)
