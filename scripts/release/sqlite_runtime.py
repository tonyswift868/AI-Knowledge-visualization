"""Fetch and lock the SQLite runtime DLL used by every frozen build.

The CPython embedded SQLite version can differ between the local release venv
and the GitHub runner. Freeze the same official SQLite DLL into both images;
never modify a system or Python installation DLL in place.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import urllib.request
import zipfile


SQLITE_VERSION = "3.54.1"
SQLITE_ARCHIVE_NAME = "sqlite-dll-win-x64-3540100.zip"
SQLITE_ARCHIVE_URL = "https://sqlite.org/2026/sqlite-dll-win-x64-3540100.zip"
SQLITE_ARCHIVE_SHA3_256 = "c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4"
SQLITE_DLL_SHA256 = "4b59d3cded9dc1608544160f3b4b10ac974c6ac0d6ffc23064e5f4c8dbd05aea"


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path: Path) -> None:
    actual = _hash(Path(path), "sha3_256")
    if actual != SQLITE_ARCHIVE_SHA3_256:
        raise ValueError(f"SQLite {SQLITE_VERSION} archive SHA3-256 mismatch: {actual}")


def verify_dll(path: Path) -> None:
    if SQLITE_DLL_SHA256 == "PENDING_OFFICIAL_DLL_HASH":
        raise RuntimeError("SQLite DLL SHA256 lock has not been supplied")
    actual = _hash(Path(path), "sha256")
    if actual != SQLITE_DLL_SHA256:
        raise ValueError(f"SQLite {SQLITE_VERSION} sqlite3.dll SHA256 mismatch: {actual}")


def ensure_sqlite_runtime(cache_dir: Path) -> Path:
    """Return the verified sqlite3.dll, fetching only into the supplied cache."""
    root = Path(cache_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / SQLITE_ARCHIVE_NAME
    if not archive.is_file():
        partial = root / (SQLITE_ARCHIVE_NAME + ".download")
        try:
            request = urllib.request.Request(SQLITE_ARCHIVE_URL, headers={"User-Agent": "MentorFlow release build"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output)
            verify_archive(partial)
            os.replace(partial, archive)
        finally:
            partial.unlink(missing_ok=True)
    verify_archive(archive)

    dll = root / "sqlite3.dll"
    if dll.is_file():
        try:
            verify_dll(dll)
            return dll
        except ValueError:
            # Re-extract from the verified archive below; never trust cache state.
            pass
    temporary = root / "sqlite3.dll.extracting"
    try:
        with zipfile.ZipFile(archive) as package:
            candidates = [name for name in package.namelist()
                          if Path(name).name.casefold() == "sqlite3.dll" and not name.endswith("/")]
            if len(candidates) != 1:
                raise ValueError(f"SQLite archive must contain one sqlite3.dll, found {len(candidates)}")
            with package.open(candidates[0]) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
        verify_dll(temporary)
        os.replace(temporary, dll)
    finally:
        temporary.unlink(missing_ok=True)
    return dll


def replace_analysis_sqlite(analysis, sqlite_dll: Path) -> None:
    """Replace PyInstaller's automatically resolved sqlite3.dll in Analysis."""
    verify_dll(Path(sqlite_dll))
    matches = [row for row in analysis.binaries
               if Path(str(row[0]).replace("\\", "/")).name.casefold() == "sqlite3.dll"]
    if len(matches) != 1:
        raise ValueError(f"expected one auto-collected sqlite3.dll, found {len(matches)}")
    original = matches[0]
    replacement = (original[0], str(Path(sqlite_dll).resolve()), original[2])
    analysis.binaries = [replacement if row is original else row for row in analysis.binaries]


def validate_lock() -> None:
    """Fail early if source constants no longer identify the locked artifact."""
    if (SQLITE_VERSION != "3.54.1" or SQLITE_ARCHIVE_NAME != "sqlite-dll-win-x64-3540100.zip"
            or not re.fullmatch(r"https://sqlite\.org/\d{4}/" + re.escape(SQLITE_ARCHIVE_NAME), SQLITE_ARCHIVE_URL)
            or not re.fullmatch(r"[0-9a-f]{64}", SQLITE_ARCHIVE_SHA3_256)
            or not re.fullmatch(r"[0-9a-f]{64}", SQLITE_DLL_SHA256)):
        raise ValueError("SQLite release input lock is incomplete or inconsistent")
