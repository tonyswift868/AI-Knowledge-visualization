"""F1: assigned disposable storage never expands production authority."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import test_storage_policy as policy
from scripts import verify_windows_package as verifier
from desktop import account_storage, test_device_profile as profile
from tools.dev_workbench.devices import Devices
from tools.dev_workbench.targets import Refused

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows drive/reparse contract')


@pytest.fixture(autouse=True)
def test_mode(monkeypatch):
    monkeypatch.setenv(policy.SCRATCH_ENV, str(policy.ASSIGNED_ROOT))
    monkeypatch.setenv(policy.TEST_MODE_ENV, '1')


@pytest.mark.parametrize('path', [
    Path('D:/arbitrary/fixture'),
    Path('D:/CodexScratch/outside/fixture'),
    policy.ASSIGNED_ROOT.parent / 'B/tests/fixture',
    policy.ASSIGNED_ROOT / '../B/fixture',
    policy.ASSIGNED_ROOT,
])
def test_unassigned_roots_rejected(path):
    with pytest.raises(ValueError): policy.require_test_path(path)
    with pytest.raises(Refused): Devices(path)
    with pytest.raises(verifier.PackageError): verifier._ensure_storage_root(path, label='test data')
    with pytest.raises(ValueError): account_storage.device_storage_root(root=path)


@pytest.mark.parametrize('mode,root', [
    ('0', policy.ASSIGNED_ROOT),
    ('1', Path('D:/arbitrary')),
    ('1', policy.ASSIGNED_ROOT.parent / 'B'),
])
def test_environment_cannot_authorize_other_root(monkeypatch, mode, root):
    monkeypatch.setenv(policy.TEST_MODE_ENV, mode)
    monkeypatch.setenv(policy.SCRATCH_ENV, str(root))
    with pytest.raises(ValueError): policy.require_test_path(policy.ASSIGNED_ROOT / 'tests/x')


def test_assigned_root_accepted_without_changing_durable_policy(tmp_path):
    policy.require_test_path(tmp_path)
    verifier._ensure_storage_root(tmp_path, label='disposable database')
    assert account_storage.device_storage_root(root=tmp_path) == tmp_path
    assert not account_storage.durable_path(tmp_path)
    assert account_storage.DEFAULT_ROOT == Path('E:/MentorFlowData')
    assert account_storage.durable_path(Path('E:/MentorFlowData/account-fixture'))
    verifier._ensure_e_drive(Path('E:/MentorFlowUpdates'), label='production staging')
    with pytest.raises(verifier.PackageError):
        verifier._ensure_e_drive(tmp_path, label='production staging')
    with pytest.raises(ValueError, match='TEST_ONLY processes'):
        account_storage.require_migration_target(tmp_path)


def test_device_reset_keeps_sibling_and_profile_binding(tmp_path):
    devices = Devices(tmp_path / 'devices')
    first, second = devices.prepare('A'), devices.prepare('B')
    sentinel = devices.root('B') / 'vault/prelaunch/data/keep'
    sentinel.write_text('sibling')
    assert profile.read_profile(devices.root('A')) == first
    environment = profile.environment(devices.root('A'), first)
    assert environment[policy.SCRATCH_ENV] == str(policy.ASSIGNED_ROOT)
    marker = devices.root('A') / profile.MARKER
    tampered = dict(first, root=str(devices.root('B')))
    marker.write_text(json.dumps(tampered))
    with pytest.raises(ValueError): devices.reset('A')
    assert sentinel.read_text() == 'sibling'
    marker.write_text(json.dumps(first))
    devices.reset('A')
    assert devices.owned('B') == second
    assert sentinel.read_text() == 'sibling'
    with pytest.raises(Refused): devices.reset('../B')


def test_reparse_escape_rejected_before_reset_or_database_read(tmp_path):
    devices = Devices(tmp_path / 'devices'); devices.prepare('A')
    outside = tmp_path / 'outside'; outside.mkdir()
    sentinel = outside / 'keep'; sentinel.write_text('unchanged')
    link = devices.root('A') / 'alias'
    result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError): policy.require_test_path(link / 'keep')
        with pytest.raises(verifier.PackageError): verifier.check_sqlite_root(link)
        with pytest.raises(ValueError): devices.reset('A')
        assert sentinel.read_text() == 'unchanged'
    finally:
        # Unlink the verified directory entry only, never traverse its target.
        link.rmdir()


def test_extended_io_syntax_cannot_authorize_outside_or_sibling():
    for path in [Path('D:/arbitrary'), policy.ASSIGNED_ROOT.parent / 'B/file']:
        with pytest.raises(ValueError): policy.test_io_path(path)
        with pytest.raises(ValueError): policy.require_test_path('\\\\?\\' + str(path))


def test_disposable_sqlite_url_keeps_extended_prefix_out_of_query(tmp_path):
    from backend.app import orm
    from sqlalchemy import text
    path = policy.test_io_path(tmp_path / 'extended.db')
    url = orm._personal_sqlite_url(path)
    assert url.database == str(path) and not url.query
    assert orm.resolve_migration_database_url(url, source={}) == url
    engine = orm._create_engine_from_url(url)
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE fixture (value INTEGER)'))
            connection.execute(text('INSERT INTO fixture VALUES (1)'))
            assert connection.execute(text('SELECT value FROM fixture')).scalar_one() == 1
    finally:
        engine.dispose()
    import sqlite3
    with sqlite3.connect(policy.test_sqlite_uri(path, mode='rw'), uri=True) as connection:
        assert connection.execute('SELECT value FROM fixture').fetchone() == (1,)
    with sqlite3.connect(policy.test_sqlite_uri(path, mode='ro'), uri=True) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('DELETE FROM fixture')
    missing = path.parent / 'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(policy.test_sqlite_uri(missing, mode='rw'), uri=True)
    assert not missing.exists()
    with pytest.raises(ValueError): policy.test_sqlite_uri(path, mode='rwc')
    for outside in ('D:/arbitrary/out.db', str(policy.ASSIGNED_ROOT.parent / 'B/out.db')):
        with pytest.raises(ValueError):
            orm._personal_sqlite_url(Path('\\\\?\\' + outside.replace('/', '\\')))
    production = Path('E:/MentorFlowData/fixture.db')
    assert orm._personal_sqlite_url(production).database == production.as_posix()


def test_build_refuses_unassigned_scratch_before_cleanup(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    marker = tmp_path / 'keep'; marker.write_text('unchanged')
    monkeypatch.setenv(policy.SCRATCH_ENV, str(policy.ASSIGNED_ROOT))
    result = subprocess.run([
        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        str(root / 'scripts/build_distributable.ps1'),
        '-TestScratchRoot', str(policy.ASSIGNED_ROOT.parent / 'B'),
    ], cwd=tmp_path, capture_output=True)
    assert result.returncode != 0
    assert b'Assigned TEST scratch validation failed' in result.stderr
    assert marker.read_text() == 'unchanged'
