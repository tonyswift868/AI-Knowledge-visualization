import hashlib
import io
from pathlib import Path
import zipfile

import pytest

from scripts.release import sqlite_runtime


def _zip_with_dll(contents):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sqlite3.dll", contents)
    return buffer.getvalue()


def _sha(data, name):
    return hashlib.new(name, data).hexdigest()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def test_sqlite273_downloads_and_verifies_archive_and_extracted_dll(tmp_path, monkeypatch):
    dll_bytes = b"locked sqlite runtime"
    archive_bytes = _zip_with_dll(dll_bytes)
    monkeypatch.setattr(sqlite_runtime, "SQLITE_ARCHIVE_SHA3_256", _sha(archive_bytes, "sha3_256"))
    monkeypatch.setattr(sqlite_runtime, "SQLITE_DLL_SHA256", _sha(dll_bytes, "sha256"))
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, timeout))
        return _Response(archive_bytes)

    monkeypatch.setattr(sqlite_runtime.urllib.request, "urlopen", fake_urlopen)
    path = sqlite_runtime.ensure_sqlite_runtime(tmp_path / "cache")

    assert path.read_bytes() == dll_bytes
    assert requests == [(sqlite_runtime.SQLITE_ARCHIVE_URL, 60)]
    assert not list(path.parent.glob("*.download"))
    assert not list(path.parent.glob("*.extracting"))


def test_sqlite273_rejects_bad_archive_without_extraction(tmp_path, monkeypatch):
    archive_bytes = _zip_with_dll(b"not trusted")
    monkeypatch.setattr(sqlite_runtime, "SQLITE_ARCHIVE_SHA3_256", "0" * 64)
    monkeypatch.setattr(sqlite_runtime.urllib.request, "urlopen", lambda *_a, **_k: _Response(archive_bytes))

    with pytest.raises(ValueError, match="archive SHA3-256 mismatch"):
        sqlite_runtime.ensure_sqlite_runtime(tmp_path / "cache")

    cache = tmp_path / "cache"
    assert not (cache / "sqlite3.dll").exists()
    assert not (cache / sqlite_runtime.SQLITE_ARCHIVE_NAME).exists()
    assert not list(cache.glob("*.download"))


def test_sqlite273_rejects_wrong_dll_hash_and_cleans_partial(tmp_path, monkeypatch):
    archive_bytes = _zip_with_dll(b"wrong sqlite dll")
    monkeypatch.setattr(sqlite_runtime, "SQLITE_ARCHIVE_SHA3_256", _sha(archive_bytes, "sha3_256"))
    monkeypatch.setattr(sqlite_runtime, "SQLITE_DLL_SHA256", "0" * 64)
    monkeypatch.setattr(sqlite_runtime.urllib.request, "urlopen", lambda *_a, **_k: _Response(archive_bytes))

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        sqlite_runtime.ensure_sqlite_runtime(tmp_path / "cache")

    cache = tmp_path / "cache"
    assert not (cache / "sqlite3.dll").exists()
    assert not list(cache.glob("*.extracting"))


def test_sqlite273_refuses_ambiguous_archive_member(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x/sqlite3.dll", b"first")
        archive.writestr("y/sqlite3.dll", b"second")
    archive_bytes = buffer.getvalue()
    monkeypatch.setattr(sqlite_runtime, "SQLITE_ARCHIVE_SHA3_256", _sha(archive_bytes, "sha3_256"))
    monkeypatch.setattr(sqlite_runtime, "SQLITE_DLL_SHA256", "1" * 64)
    monkeypatch.setattr(sqlite_runtime.urllib.request, "urlopen", lambda *_a, **_k: _Response(archive_bytes))

    with pytest.raises(ValueError, match="must contain one sqlite3.dll"):
        sqlite_runtime.ensure_sqlite_runtime(tmp_path / "cache")


def test_sqlite273_replaces_exact_analysis_binary_and_requires_one_match(tmp_path, monkeypatch):
    dll = tmp_path / "sqlite3.dll"
    dll.write_bytes(b"official dll")
    monkeypatch.setattr(sqlite_runtime, "SQLITE_DLL_SHA256", _sha(dll.read_bytes(), "sha256"))

    class Analysis:
        binaries = [
            (r"_internal\sqlite3.dll", r"C:\python\DLLs\sqlite3.dll", "BINARY"),
            ("_internal/other.dll", "C:/python/other.dll", "BINARY"),
        ]

    analysis = Analysis()
    sqlite_runtime.replace_analysis_sqlite(analysis, dll)

    assert analysis.binaries[0] == (r"_internal\sqlite3.dll", str(dll.resolve()), "BINARY")
    assert analysis.binaries[1][1] == "C:/python/other.dll"
    Analysis.binaries = [("other.dll", "other.dll", "BINARY")]
    with pytest.raises(ValueError, match="expected one auto-collected sqlite3.dll"):
        sqlite_runtime.replace_analysis_sqlite(Analysis(), dll)


def test_sqlite273_lock_rejects_unpinned_dll_hash(monkeypatch):
    monkeypatch.setattr(sqlite_runtime, "SQLITE_DLL_SHA256", "PENDING_OFFICIAL_DLL_HASH")
    with pytest.raises(ValueError, match="lock is incomplete"):
        sqlite_runtime.validate_lock()


def test_sqlite273_revalidates_cached_archive_before_using_cached_dll(tmp_path, monkeypatch):
    (tmp_path / sqlite_runtime.SQLITE_ARCHIVE_NAME).write_bytes(b'corrupt cached archive')
    (tmp_path / 'sqlite3.dll').write_bytes(b'dll')
    monkeypatch.setattr(sqlite_runtime.urllib.request, 'urlopen', lambda *_a, **_k: pytest.fail('cache should be checked locally'))
    with pytest.raises(ValueError, match='archive SHA3-256 mismatch'):
        sqlite_runtime.ensure_sqlite_runtime(tmp_path)


def test_sqlite273_repairs_bad_cached_dll_only_from_verified_archive(tmp_path, monkeypatch):
    good = b'locked cached dll'
    archive = _zip_with_dll(good)
    (tmp_path / sqlite_runtime.SQLITE_ARCHIVE_NAME).write_bytes(archive)
    (tmp_path / 'sqlite3.dll').write_bytes(b'corrupt dll')
    monkeypatch.setattr(sqlite_runtime, 'SQLITE_ARCHIVE_SHA3_256', _sha(archive, 'sha3_256'))
    monkeypatch.setattr(sqlite_runtime, 'SQLITE_DLL_SHA256', _sha(good, 'sha256'))
    monkeypatch.setattr(sqlite_runtime.urllib.request, 'urlopen', lambda *_a, **_k: pytest.fail('verified archive is already cached'))
    assert sqlite_runtime.ensure_sqlite_runtime(tmp_path).read_bytes() == good

