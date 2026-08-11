"""Shared Windows-safe relative POSIX path validation.

The runtime pack builder and the installed-pack verifier must interpret signed
paths identically. This module deliberately performs no trimming or Unicode
normalisation: the exact signed string is either safe as-is or rejected.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


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


class WindowsSafeRelativePathError(ValueError):
    """Raised when a signed relative path is ambiguous or unsafe on Windows."""


def validate_windows_safe_relative(value: Any) -> PurePosixPath:
    """Return ``value`` unchanged as a safe relative POSIX path.

    Backslashes, drive/ADS colons, control characters, Windows-invalid
    characters, device names (including superscript COM/LPT aliases), trailing
    dots/spaces, traversal, duplicate separators, and rooted paths are rejected.
    """

    if not isinstance(value, str):
        raise WindowsSafeRelativePathError("path must be a string")
    text = value
    if (
        not text
        or text != text.strip()
        or "\\" in text
        or text.startswith("/")
        or text.endswith("/")
        or "//" in text
    ):
        raise WindowsSafeRelativePathError("path must be an exact relative POSIX path")

    parts = text.split("/")
    for part in parts:
        if part in {"", ".", ".."}:
            raise WindowsSafeRelativePathError("path contains traversal or an empty segment")
        if part.startswith(" "):
            raise WindowsSafeRelativePathError("path segment starts with an ambiguous space")
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            raise WindowsSafeRelativePathError("path contains a control character")
        if any(character in WINDOWS_FORBIDDEN_CHARACTERS for character in part):
            raise WindowsSafeRelativePathError("path contains a Windows-invalid character")
        if part.endswith((".", " ")):
            raise WindowsSafeRelativePathError("path has a Windows-normalised suffix")
        if len(part.encode("utf-16-le")) // 2 > 255:
            raise WindowsSafeRelativePathError("path segment exceeds 255 UTF-16 code units")
        device_stem = part.split(".", 1)[0].rstrip(" .").casefold()
        if device_stem in WINDOWS_RESERVED_NAMES:
            raise WindowsSafeRelativePathError("path contains a Windows device name")

    path = PurePosixPath(*parts)
    if path.is_absolute() or path.as_posix() != text:
        raise WindowsSafeRelativePathError("path changes under POSIX normalisation")
    return path


def windows_casefold_key(path: PurePosixPath | str) -> str:
    """Return the case-insensitive comparison key used by both consumers."""

    return (path.as_posix() if isinstance(path, PurePosixPath) else path).casefold()
