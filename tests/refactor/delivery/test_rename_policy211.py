"""Retry classification and diagnostic contract without changing global os.name."""
import os
import json
from types import SimpleNamespace

import pytest

from refactor import filesystem_ops as ops


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_rename211_retries_only_transient_windows_errors_and_records_attempts(
    tmp_path, monkeypatch, winerror
):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / 'keep.bin').write_bytes(b'whole tree')
    real_rename = os.rename
    calls, delays, events = [], [], []

    def move(*paths):
        calls.append(paths)
        if len(calls) <= 3:
            error = OSError('transient denial')
            error.winerror = winerror
            raise error
        real_rename(*paths)

    monkeypatch.setattr(ops, 'os', SimpleNamespace(name='nt', rename=move, replace=move))
    monkeypatch.setattr(ops.time, 'sleep', delays.append)
    monkeypatch.setattr(ops, '_log_rename', lambda state, component, count, start:
                        events.append((state, component, count, start)))
    ops.rename_with_retry(source, target, component='POLICY_TEST')

    assert delays == [.01, .02, .04]
    assert len(calls) == 4
    assert [event[2] for event in events] == [1, 2, 3, 4]
    assert events[-1][:3] == ('DIRECTORY_RENAME_SUCCEEDED', 'POLICY_TEST', 4)
    assert all(isinstance(event[3], float) for event in events)
    assert (target / 'keep.bin').read_bytes() == b'whole tree'


@pytest.mark.parametrize('platform,winerror', [
    ('nt', None), ('nt', 2), ('nt', 17), ('nt', 112), ('nt', 183), ('posix', 5),
])
def test_rename211_other_errors_are_immediate_and_preserve_original(
    tmp_path, monkeypatch, platform, winerror
):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / 'keep.bin').write_bytes(b'unchanged')
    failure = OSError('original failure')
    if winerror is not None:
        failure.winerror = winerror
    calls = []

    def denied(*paths):
        calls.append(paths)
        raise failure

    monkeypatch.setattr(ops, 'os', SimpleNamespace(name=platform, rename=denied, replace=denied))
    monkeypatch.setattr(ops.time, 'sleep', lambda _: pytest.fail('must not retry this error'))
    with pytest.raises(OSError) as caught:
        ops.rename_with_retry(source, target, component='POLICY_TEST')

    assert caught.value is failure
    assert len(calls) == 1
    assert not target.exists()
    assert (source / 'keep.bin').read_bytes() == b'unchanged'


def test_rename211_installer_retries_reach_existing_maintenance_log(tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_install as install

    root = tmp_path / 'MentorFlow'
    source = root / 'private-source-name'
    target = root / 'target'
    source.mkdir(parents=True)
    (source / 'keep.bin').write_bytes(b'private student content')
    real_rename = os.rename
    attempts = []

    def move(*paths):
        attempts.append(paths)
        if len(attempts) == 1:
            error = OSError('denied')
            error.winerror = 5
            raise error
        real_rename(*paths)

    monkeypatch.setattr(ops, 'os', SimpleNamespace(name='nt', rename=move, replace=move))
    monkeypatch.setattr(ops.time, 'sleep', lambda _: None)
    install._rename_directory(root, source, target, component='TEST_INSTALL_LOG')

    with (root / 'Data/logs/mentorflow.log').open(encoding='utf-8') as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    assert [row['count'] for row in rows] == [1, 2]
    assert [row['state'] for row in rows] == [
        'DIRECTORY_RENAME_RETRY', 'DIRECTORY_RENAME_SUCCEEDED']
    assert all(row['step'] == 'TEST_INSTALL_LOG' and row['elapsed_seconds'] >= 0 for row in rows)
    serialized = json.dumps(rows)
    assert 'private-source-name' not in serialized and 'private student content' not in serialized
