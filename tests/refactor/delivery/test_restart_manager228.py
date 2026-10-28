"""Restart Manager diagnostics are read-only and remain best-effort."""
import os
import ctypes
import time

import pytest


def test_restart_manager_reports_registered_file_users(tmp_path):
    from refactor.desktop.delivery import restart_manager

    file_path = tmp_path / 'held.bin'
    file_path.write_bytes(b'fixture')
    handle, close = _hold_file_without_delete_sharing(file_path)
    try:
        found = restart_manager.query_directory_users(tmp_path)
        assert found['status'] == 'ok'
        assert any(row['pid'] == os.getpid() for row in found['processes'])
        assert all({'name', 'pid', 'type'} <= row.keys() for row in found['processes'])
    finally:
        close(handle)


def test_228_budgeted_retry_releases_inside_budget_and_reports_attempt_metadata(
    tmp_path, monkeypatch
):
    from refactor import filesystem_ops as ops

    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / 'keep.bin').write_bytes(b'data')
    real_rename = os.rename
    real_sleep = time.sleep
    attempts, rows = [], []

    def move(*paths):
        attempts.append(paths)
        if len(attempts) < 3:
            error = PermissionError(5, 'busy')
            error.winerror = 5
            raise error
        real_rename(*paths)

    monkeypatch.setattr(ops, 'os', type('OS', (), {'name': 'nt', 'rename': move, 'replace': move}))
    monkeypatch.setattr(ops, '_query_rename_users', lambda *args, **kwargs: {
        'status': 'ok', 'processes': [
            {'name': 'test-owner.exe', 'pid': 321, 'type': 'application'}
        ]
    })
    monkeypatch.setattr(ops.time, 'sleep', lambda delay: real_sleep(min(delay, .001)))
    ops.rename_with_retry(source, target, component='APP_ACTIVATE', diagnose=True,
                          budget_seconds=3, on_attempt=rows.append)

    assert len(attempts) == 3
    assert [row['attempt'] for row in rows] == [1, 2]
    assert all(row['error_code'] == 5 and row['blockers'][0]['pid'] == 321 for row in rows)
    assert all(row['timestamp'] and row['elapsed_seconds'] >= 0 for row in rows)
    assert (target / 'keep.bin').read_bytes() == b'data'


def test_228_budget_exhaustion_stops_retries_and_reports_last_error(tmp_path, monkeypatch):
    from refactor import filesystem_ops as ops

    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    calls, rows = [], []
    clock = iter([0, .01, .01, .02, .02, 3.01, 3.01])
    monkeypatch.setattr(ops, 'os', type('OS', (), {
        'name': 'nt', 'rename': lambda *_: calls.append(1) or _denied(),
        'replace': lambda *_: _denied()
    }))
    monkeypatch.setattr(ops.time, 'monotonic', lambda: next(clock, 3.01))
    monkeypatch.setattr(ops.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ops, '_query_rename_users', lambda *_args, **_kwargs: {
        'status': 'ok', 'processes': []
    })

    with pytest.raises(PermissionError) as caught:
        ops.rename_with_retry(source, target, component='APP_ACTIVATE', diagnose=True,
                              budget_seconds=3, on_attempt=rows.append)
    assert caught.value.winerror == 5
    assert len(calls) <= 2
    assert len(rows) == len(calls)
    assert rows[-1]['error_code'] == 5
    assert rows[-1]['blockers'] == []


def _denied():
    error = PermissionError(5, 'busy')
    error.winerror = 5
    raise error


def test_228_best_effort_query_returns_empty_on_unsupported_platform(tmp_path, monkeypatch):
    from refactor.desktop.delivery import restart_manager

    monkeypatch.setattr(restart_manager.os, 'name', 'posix')
    assert restart_manager.query_directory_users(tmp_path) == {
        'status': 'unknown', 'processes': []
    }


def test_228_zero_time_diagnostic_returns_unknown_without_starting_worker(tmp_path, monkeypatch):
    from refactor.desktop.delivery import restart_manager

    monkeypatch.setattr(restart_manager, '_query_windows',
                        lambda *_: pytest.fail('must not start a query worker'))
    assert restart_manager.query_directory_users(tmp_path, timeout_seconds=0) == {
        'status': 'unknown', 'processes': [], 'reason': 'diagnostic_timeout'
    }


def _hold_file_without_delete_sharing(path):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p)
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int
    handle = create_file(str(path), 0x80000000, 0x00000003, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle, close_handle

