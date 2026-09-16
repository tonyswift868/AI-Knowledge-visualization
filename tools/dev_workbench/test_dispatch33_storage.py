"""Dispatch33 authorization does not grant arbitrary D or cross-worker access."""
import os
import subprocess
from pathlib import Path

import pytest

from scripts import test_storage_policy as policy
from desktop.test_device_profile import device_names
from tools.dev_workbench.devices import Devices
from tools.dev_workbench.targets import Refused

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows assigned roots')


@pytest.mark.parametrize('selected', [policy.ASSIGNED_ROOT, policy.GUI_AB_ROOT])
def test_exact_assignment_and_cross_dispatch_denial(selected):
    env = {policy.SCRATCH_ENV: str(selected), policy.TEST_MODE_ENV: '1'}
    assert policy.require_test_path(selected / 'devices/A', environ=env)
    other = next(root for root in policy.AUTHORIZED_ROOTS if root != selected)
    for path in (other / 'devices/A', selected, selected.parent / '03/x', selected / '../03/x'):
        with pytest.raises(ValueError):
            policy.require_test_path(path, environ=env)
    for root in (selected.parent, selected / 'subroot', Path('D:/arbitrary')):
        with pytest.raises(ValueError):
            policy.assigned_root({**env, policy.SCRATCH_ENV: str(root)})


def test_negative_profile_is_only_explicit_dispatch33_device(monkeypatch):
    monkeypatch.setenv(policy.TEST_MODE_ENV, '1')
    monkeypatch.setenv(policy.SCRATCH_ENV, str(policy.GUI_AB_ROOT))
    devices = Devices(policy.GUI_AB_ROOT / 'devices')
    assert devices.root('NEGATIVE') == policy.GUI_AB_ROOT / 'devices/NEGATIVE'
    for base in (policy.ASSIGNED_ROOT / 'devices', policy.GUI_AB_ROOT / 'other', Path('E:/Codex/TestDevices/MentorFlow')):
        assert 'NEGATIVE' not in device_names(base)
    for name in ('../B', 'C', 'negative'):
        with pytest.raises(Refused): devices.root(name)


def test_new_root_still_requires_explicit_test_mode():
    with pytest.raises(ValueError):
        policy.assigned_root({policy.SCRATCH_ENV: str(policy.GUI_AB_ROOT)})


def test_device_ownership_isolation_and_reparse_guard(tmp_path, monkeypatch):
    monkeypatch.setenv(policy.TEST_MODE_ENV, '1')
    monkeypatch.setenv(policy.SCRATCH_ENV, str(policy.GUI_AB_ROOT))
    devices = Devices(tmp_path / 'devices')
    a, b = devices.prepare('A'), devices.prepare('B')
    assert a['owner'] != b['owner'] and a['generation'] != b['generation']
    sentinel = devices.root('B') / 'vault/keep'
    sentinel.write_text('sibling')
    devices.reset('A')
    assert sentinel.read_text() == 'sibling'
    devices.prepare('A')
    link = devices.root('A') / 'alias'
    result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(devices.root('B'))], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError): policy.require_test_path(link / 'vault/keep')
        with pytest.raises(ValueError): devices.reset('A')
        assert sentinel.read_text() == 'sibling'
    finally:
        link.rmdir()
