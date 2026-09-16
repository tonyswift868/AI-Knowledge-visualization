"""Explicit, dispatch-scoped TEST_EPHEMERAL_ROOT; never production authority.

Standard-library only so the shipped SELF-CHECK can use the same policy.
An environment variable selects the assigned root; it cannot authorize a new
root, another worker, or an entire drive. Existing link and ownership checks
at each consumer remain mandatory.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile

__test__ = False

SCRATCH_ENV = "MENTORFLOW_TEST_SCRATCH_ROOT"
TEST_MODE_ENV = "MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY"
ASSIGNED_ROOT = Path(
    "D:/CodexScratch/MentorFlow/"
    "MF-MAIN3-A-P0-SOURCE-INTEGRATION-FROZEN-REBUILD-20260916-32/A"
)


def _plain(path: Path) -> None:
    if (not path.is_absolute() or ".." in path.parts
            or path.drive.startswith("\\\\")
            or any(":" in part for part in path.parts[1:])):
        raise ValueError("TEST_EPHEMERAL_UNSAFE_PATH")
    for entry in (*reversed(path.parents), path):
        try:
            probe = Path('\\\\?\\' + str(entry)) if os.name == 'nt' and entry.drive.upper() == 'D:' else entry
            info = probe.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("TEST_EPHEMERAL_REPARSE_REJECTED")


def assigned_root(environ=None) -> Path:
    env = os.environ if environ is None else environ
    if env.get(TEST_MODE_ENV) != "1" or not env.get(SCRATCH_ENV):
        raise ValueError("TEST_EPHEMERAL_EXPLICIT_MODE_REQUIRED")
    root = Path(env[SCRATCH_ENV])
    _plain(root)
    if root != ASSIGNED_ROOT or root.resolve(strict=False) != ASSIGNED_ROOT:
        raise ValueError("TEST_EPHEMERAL_UNASSIGNED_ROOT")
    return root


def require_test_path(path, *, environ=None) -> Path:
    """Accept a plain strict descendant only; the assignment itself is protected."""
    root = assigned_root(environ)
    # Extended local syntax is an I/O representation, not a second authority.
    # Strip only the exact local D: prefix; UNC/device paths remain rejected.
    text = str(path)
    if os.name == 'nt' and text[:7].upper() == '\\\\?\\D:\\':
        text = text[4:]
    candidate = Path(text)
    _plain(candidate)
    if (candidate == root or not candidate.is_relative_to(root)
            or not candidate.resolve(strict=False).is_relative_to(root)):
        raise ValueError("TEST_EPHEMERAL_OUTSIDE_ASSIGNED_ROOT")
    return candidate


def test_io_path(path) -> Path:
    """Long-path I/O for a validated TEST root; no machine/global setting."""
    candidate = require_test_path(path)
    return Path('\\\\?\\' + str(candidate)) if os.name == 'nt' else candidate


def configured_test_temp_root() -> Path:
    """Fixture I/O and cleanup use the same validated long-path representation."""
    root = Path(tempfile.gettempdir())
    return test_io_path(root) if root.drive.upper() == 'D:' else root
