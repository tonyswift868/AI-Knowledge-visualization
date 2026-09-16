"""Headless launch ordering checks: no QApplication, window or real profile."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from desktop import startup_preparation as preparation


def test_normal_launch_does_not_require_workbench(monkeypatch):
    monkeypatch.delenv('MENTORFLOW_WORKBENCH_PREPARATION', raising=False)
    assert preparation.wait_for_preparation(lambda: False)


def test_pending_waits_then_ready_and_cancel_wins(tmp_path, monkeypatch):
    marker = tmp_path / 'preparation.json'
    marker.write_text(json.dumps({'state': 'pending'}))
    monkeypatch.setenv('MENTORFLOW_WORKBENCH_PREPARATION', str(marker))
    sleeps = []
    def prepared(_):
        sleeps.append(True)
        marker.write_text(json.dumps({'state': 'ready'}))
    monkeypatch.setattr(preparation.time, 'sleep', prepared)
    assert preparation.wait_for_preparation(lambda: False)
    assert len(sleeps) == 1
    assert not preparation.wait_for_preparation(lambda: True)


@pytest.mark.parametrize('contents', ['{}', 'invalid', '{"state":"error"}', '{"state":"unknown"}'])
def test_failed_or_invalid_preparation_cannot_start_service(tmp_path, monkeypatch, contents):
    marker = tmp_path / 'preparation.json'
    marker.write_text(contents)
    monkeypatch.setenv('MENTORFLOW_WORKBENCH_PREPARATION', str(marker))
    with pytest.raises(RuntimeError, match='WORKBENCH_ASSET_PREPARATION_FAILED'):
        preparation.wait_for_preparation(lambda: False)


def test_missing_and_relative_marker_fail_closed(tmp_path, monkeypatch):
    for path in ('relative.json', str(tmp_path/'missing.json')):
        monkeypatch.setenv('MENTORFLOW_WORKBENCH_PREPARATION', path)
        assert preparation.preparation_state() == 'error'


def test_orphaned_pending_build_has_bounded_wait(tmp_path, monkeypatch):
    marker = tmp_path/'preparation.json'
    marker.write_text('{"state":"pending"}')
    monkeypatch.setenv('MENTORFLOW_WORKBENCH_PREPARATION', str(marker))
    with pytest.raises(RuntimeError):
        preparation.wait_for_preparation(lambda: False, timeout=0)


def test_login_shell_and_solver_import_without_heavy_runtime():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, '-B', '-c',
        'import sys; from desktop import login_shell; '
        'from backend.app.planning.scheduling import solver; '
        'assert "ortools.sat.python.cp_model" not in sys.modules; '
        'assert "pandas" not in sys.modules; '
        'assert "desktop.startup_flow" not in sys.modules'],
        cwd=root, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'},
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


@pytest.mark.parametrize('outcome', ['ready', 'cancelled', 'changed_during_frontend', 'failed'])
def test_service_start_obeys_readiness_and_account_generation(monkeypatch, outcome):
    # Execute the production method with plain test doubles, never a Qt app.
    import ast
    from types import SimpleNamespace, ModuleType
    from unittest.mock import Mock
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root/'desktop/main.py').read_text(encoding='utf-8'))
    app_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='MentorFlowApplication')
    method = next(n for n in app_class.body if isinstance(n, ast.FunctionDef) and n.name=='_start_enterprise_local_service')
    service=Mock()
    runtime=ModuleType('desktop.runtime'); runtime.LocalService=Mock(return_value=service)
    flow=ModuleType('desktop.startup_flow')
    flow.LoginGateState=SimpleNamespace(AUTH_SUCCESS_BOOTSTRAPPING='bootstrapping')
    diagnostics=ModuleType('desktop.startup_diagnostics'); diagnostics.record=Mock()
    monkeypatch.setitem(sys.modules,'desktop.runtime',runtime)
    monkeypatch.setitem(sys.modules,'desktop.startup_flow',flow)
    monkeypatch.setitem(sys.modules,'desktop.startup_diagnostics',diagnostics)
    instance=SimpleNamespace(bootstrap_result=SimpleNamespace(profile=object(),rehydration_session=object()),
        login_gate=Mock(login_is_blocked=False), service=None, _closing=False,
        _login_generation=3, app=object(), _service_ready=Mock(), _service_failed=Mock())
    jobs=[]
    def thread(**kwargs):
        jobs.append(kwargs['target'])
        return Mock()
    frontend=Mock()
    namespace=dict(__package__='desktop', QTimer=Mock(), threading=SimpleNamespace(Thread=thread),
                   _prepare_desktop_frontend=frontend)
    exec(compile(ast.Module(body=[method],type_ignores=[]),'startup-method','exec'),namespace)
    namespace[method.name](instance)
    assert len(jobs)==1
    service.start.assert_not_called()
    def wait(cancelled):
        service.start.assert_not_called()
        assert not cancelled()
        if outcome=='failed': raise RuntimeError('failed')
        return outcome!='cancelled'
    monkeypatch.setattr(preparation,'wait_for_preparation',wait)
    if outcome=='changed_during_frontend':
        frontend.side_effect=lambda: setattr(instance,'_login_generation',4)
    jobs[0]()
    if outcome=='ready':
        service.start.assert_called_once()
    else:
        service.start.assert_not_called()
    if outcome=='failed': service.failed.emit.assert_called_once()
    # A late ready signal cannot open a window after an account change.
    instance._login_generation=4
    service.ready.connect.call_args_list[0].args[0]('http://isolated')
    instance._service_ready.assert_not_called()
