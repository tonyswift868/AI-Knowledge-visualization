from pathlib import Path
import sqlite3
import json
import hashlib

import pytest

from refactor.desktop.packaging import smoke


def write_lock(payload, version, data=b'dll bytes'):
    internal = payload / '_internal'
    internal.mkdir(parents=True, exist_ok=True)
    (internal / 'sqlite3.dll').write_bytes(data)
    (internal / 'sqlite-runtime.json').write_text(json.dumps(
        dict(version=version, sha256=hashlib.sha256(data).hexdigest())))


def test_sqlite273_smoke_rejects_dll_loaded_from_outside_payload(tmp_path, monkeypatch):
    write_lock(tmp_path / 'payload', sqlite3.sqlite_version)
    monkeypatch.setattr(smoke, 'loaded_sqlite_path', lambda: tmp_path / 'system/sqlite3.dll')
    with pytest.raises(RuntimeError, match='outside the frozen payload'):
        smoke.sqlite_runtime_info(tmp_path / 'payload')


def test_sqlite273_smoke_checks_loaded_dll_bytes_and_runtime_version(tmp_path, monkeypatch):
    expected = (tmp_path / '_internal/sqlite3.dll').resolve()
    write_lock(tmp_path, sqlite3.sqlite_version)
    monkeypatch.setattr(smoke, 'loaded_sqlite_path', lambda: expected)
    info = smoke.sqlite_runtime_info(tmp_path)
    assert info['version'] == sqlite3.sqlite_version
    assert info['source_id']
    write_lock(tmp_path, '0.0.0')
    with pytest.raises(RuntimeError, match='Unexpected frozen SQLite runtime'):
        smoke.sqlite_runtime_info(tmp_path)
    write_lock(tmp_path, sqlite3.sqlite_version)
    expected.write_bytes(b'changed after freeze')
    with pytest.raises(RuntimeError, match='SHA256 mismatch'):
        smoke.sqlite_runtime_info(tmp_path)
