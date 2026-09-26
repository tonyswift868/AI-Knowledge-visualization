"""Plain source I/O extracted from backend/app/plain_file_copy.py.

Only the file-reading and reparse-boundary primitives are retained.
"""
from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path


class PlainFileCopyError(RuntimeError):
    """Stable error raised when an exact plain-file copy cannot be proved."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
    if os.path.lexists(_native_path(current)) and _is_reparse(current):
        raise PlainFileCopyError("COPY_REPARSE_FORBIDDEN", f"copy boundary is a reparse point: {current}")
    for part in relative.parts:
        current = current / part
        if os.path.lexists(_native_path(current)) and _is_reparse(current):
            raise PlainFileCopyError("COPY_REPARSE_FORBIDDEN", f"copy path uses a reparse point: {current}")


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

