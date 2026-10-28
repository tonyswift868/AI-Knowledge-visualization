from pathlib import Path
import errno
import os
import subprocess
import sys

import pytest

from refactor import filesystem_ops
from refactor.desktop.packaging import payload
from scripts.release import build_release


def test_payload_rename_retries_windows_transient_denial(tmp_path, monkeypatch):
    staged = tmp_path / 'staged'
    output = tmp_path / 'payload'
    staged.mkdir()
    real_rename = filesystem_ops.os.rename
    calls = []

    def flaky_rename(source, target):
        calls.append((Path(source), Path(target)))
        if len(calls) == 1:
            error = PermissionError(errno.EACCES, 'sharing violation')
            error.winerror = 5
            raise error
        return real_rename(source, target)

    monkeypatch.setattr(filesystem_ops.os, 'name', 'nt')
    monkeypatch.setattr(filesystem_ops.os, 'rename', flaky_rename)
    monkeypatch.setattr(filesystem_ops.time, 'sleep', lambda _: None)
    payload.rename_payload_with_retry(staged, output, component='test')
    assert output.is_dir()
    assert len(calls) == 2


def test_allow_dirty_remains_local_only(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'tracked.py').write_text('dirty content', encoding='utf-8')
    monkeypatch.setattr(build_release, 'git', lambda *args: '?? tracked.py')
    assert build_release.ensure_release_source(repo, allow_dirty=True) is True
    out = tmp_path / 'out'
    recipe = {'source_dirty': True, 'parent_head': 'a' * 40}
    called = []
    result = build_release.release_or_record_local(source_dirty=True, out=out,
        recipe=recipe, parent_head='a' * 40, package={'files': 2},
        smoke_result={'ok': True}, created_at='fixed-time',
        create_release=lambda: called.append('release'))
    persisted = __import__('json').loads((out / 'build-evidence.json').read_text(encoding='utf-8'))
    assert result['local_only'] is True
    assert called == []
    assert persisted['source'] == {'dirty': True, 'parent_head': 'a' * 40}
    assert 'architect_commands' not in persisted
    assert not (out / 'unsigned-release.json').exists()
    assert build_release.release_or_record_local(source_dirty=False, out=out,
        recipe={}, parent_head=None, package={}, smoke_result={}, created_at='fixed-time',
        create_release=lambda: called.append('release')) is None
    assert called == ['release']


def test_release_build_publishes_frozen_payload_through_retry_helper(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(payload, 'rename_payload_with_retry',
                        lambda source, target, component: calls.append((source, target, component)))
    built, target = tmp_path / 'built', tmp_path / 'target'
    build_release.publish_frozen_payload(built, target)
    assert calls == [(built, target, 'release-build-payload')]


def test_build_release_help_works_from_unrelated_cwd(tmp_path):
    script = Path(__file__).resolve().parents[3] / 'scripts/release/build_release.py'
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-B', str(script), '--help'], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()
