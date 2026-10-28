import importlib.util
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/release/rehearsal102/scenario160.py'


def load_scenario(monkeypatch, task_root):
    monkeypatch.setenv('MF_UAT_ROOT', str(task_root))
    monkeypatch.setenv('MF_UAT_APP_ID', 'MentorFlow.UAT.gate160')
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    # `common` is cached between test cases; bind its task constants before
    # importing scenario160 so every case validates against its own root.
    common = importlib.import_module('common')
    monkeypatch.setattr(common, 'TASK', task_root)
    monkeypatch.setattr(common, 'APP_ID', 'MentorFlow.UAT.gate160')
    name = 'scenario160_rehearsal172_test'
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert module.TASK == task_root
    return module


def test_core_install_rejects_root_outside_task_before_publish(monkeypatch, tmp_path):
    task = tmp_path / 'task'
    outside = tmp_path / 'outside' / 'install'
    task.mkdir()
    module = load_scenario(monkeypatch, task)
    monkeypatch.setattr(sys, 'argv', ['scenario160.py', 'core-install', '--root', str(outside)])
    monkeypatch.setattr(module, 'publish', lambda *_: (_ for _ in ()).throw(AssertionError('published')))
    try:
        module.main()
    except ValueError as exc:
        assert 'child of the rehearsal task directory' in str(exc)
    else:
        raise AssertionError('out-of-task root was accepted')
    assert not outside.parent.exists()


def test_core_install_rejects_task_root_itself(monkeypatch, tmp_path):
    task = tmp_path / 'task'
    task.mkdir()
    module = load_scenario(monkeypatch, task)
    monkeypatch.setattr(sys, 'argv', ['scenario160.py', 'core-install', '--root', str(task)])
    try:
        module.main()
    except ValueError as exc:
        assert 'child of the rehearsal task directory' in str(exc)
    else:
        raise AssertionError('task root itself was accepted')


def test_core_install_rejects_junction_escape_before_publish(monkeypatch, tmp_path):
    if os.name != 'nt':
        pytest.skip('junction validation requires Windows')
    task = tmp_path / 'task'
    outside = tmp_path / 'outside'
    task.mkdir()
    outside.mkdir()
    link = task / 'escape'
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                          capture_output=True, text=True)
    if made.returncode:
        pytest.skip('Windows junction creation unavailable: ' + made.stderr.strip())

    module = load_scenario(monkeypatch, task)
    monkeypatch.setattr(sys, 'argv', ['scenario160.py', 'core-install', '--root', str(link / 'install')])
    monkeypatch.setattr(module, 'publish', lambda *_: (_ for _ in ()).throw(AssertionError('published')))
    with pytest.raises(ValueError, match='child of the rehearsal task directory'):
        module.main()
    assert not (outside / 'install').exists()
