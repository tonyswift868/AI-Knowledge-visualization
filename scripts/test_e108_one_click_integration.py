"""Partial E108 assembly gates. Inert data/offscreen widgets; no product runs."""
import ast
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop.update_lifecycle import CoreUpdatePort, configured_update_service
from scripts import per_user_install as install, per_user_update_core as core
from scripts import build_e108_user_candidate as builder
from scripts.test_per_user_install import case

ROOT = Path(__file__).resolve().parents[1]
FROZEN_07 = '78abd7d85d6cdeba0a53439908ad1417126bc456'


@pytest.fixture
def port(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        pytest.fail('Per-user UI must use 07 high-level API, not legacy service operations')
    service = SimpleNamespace(install_root=tmp_path, current='1.4.0', apply_bridge=None,
        snapshot={'state': 'IDLE'}, cache=SimpleNamespace(transport=SimpleNamespace(busy=False)),
        check=forbidden, download=forbidden, apply_and_restart=forbidden, close=Mock())
    monkeypatch.setattr(install, 'is_install', lambda _: True)
    value = CoreUpdatePort(service, window_id=lambda: 'ab12')
    yield value
    value.close()


def test_per_user_port_uses_only_high_level_api_and_one_automatic_continuation(port, monkeypatch):
    events = []
    check = Mock(return_value={'state': 'UPDATE_AVAILABLE', 'latest': '1.4.1'})
    monkeypatch.setattr(core, 'check_latest', check)
    assert port.check_latest()['state'] == 'UPDATE_AVAILABLE'
    check.assert_called_once_with(port.service.install_root, service=port.service)
    def download(root, progress, cancel, *, service):
        assert root == service.install_root and not cancel()
        events.append('download')
        progress({'state': 'DOWNLOADING', 'actual_downloaded_bytes': 50, 'expected_download_bytes': 100})
        return {'state': 'UPDATE_DOWNLOADED', 'stage': 'PRIVATE_CORE_PATH', 'target': '1.4.1'}
    monkeypatch.setattr(core, 'download_latest', download)
    def apply(root, **kwargs):
        assert kwargs == dict(hwnd_hex='ab12', reason='normal', service=port.service)
        events.append('apply-and-relaunch')
        return {'state': 'APPLYING', 'coordinator_started': True}
    monkeypatch.setattr(core, 'apply_and_relaunch', apply)
    result = port.update_to_latest(lambda s: events.append(s['state']), threading.Event(),
                                  lambda: events.append('save-ui'))
    assert events.index('download') < events.index('save-ui') < events.index('apply-and-relaunch')
    assert events.count('apply-and-relaunch') == 1 and result['state'] == 'PREPARING'
    assert 'PRIVATE_CORE_PATH' not in str(port.snapshot)


def test_gui_session_event_is_retained_by_coordinator_and_core_defers(port, monkeypatch):
    from scripts.per_user_runtime_session import runtime_session, session_ending
    from desktop.update_session import SessionEnding
    apply = Mock(return_value={'state': 'PENDING_INSTALL', 'destructive_apply': False})
    monkeypatch.setattr(core, 'apply_and_relaunch', apply)
    with runtime_session(os.getpid()):
        assert not session_ending()
        port.defer_session()
        with pytest.raises(SessionEnding): port._session.require_running()
        assert port.apply_and_relaunch()['state'] == 'DEFERRED_TO_NEXT_LAUNCH'
        assert apply.call_args.kwargs['reason'] == 'unsafe'
        port.close()
        assert session_ending()


@pytest.mark.parametrize('action,method', [('apply-latest', 'apply_and_relaunch'),
                                         ('update-to-latest', 'update_to_latest')])
def test_native_dispatch_retains_session_scope(action, method, tmp_path, monkeypatch):
    from desktop.windows_process_peer import ProcessPeer
    from scripts import per_user_runtime_session
    events = []
    @contextmanager
    def scope(pid):
        events.append(('enter', pid))
        yield
        events.append(('exit', pid))
    monkeypatch.setattr(per_user_runtime_session, 'runtime_session', scope)
    monkeypatch.setattr(ProcessPeer, 'inspect', lambda _: dict(elevated=False, integrity_rid=8192))
    def call(root, **kwargs):
        assert events == [('enter', 42)]
        assert root == tmp_path and kwargs['pid'] == 42 and kwargs['hwnd_hex'] == 'abcd'
        return {'state': 'APPLYING'}
    monkeypatch.setattr(core, method, call)
    monkeypatch.setattr(sys, 'argv', ['UserRuntime', action, '--root', str(tmp_path), '--pid', '42', '--hwnd-hex', 'abcd'])
    assert install.main()['state'] == 'APPLYING'
    assert events == [('enter', 42), ('exit', 42)]


def test_per_user_factory_cannot_fall_back_when_signed_feed_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'App/MentorFlow.exe'))
    monkeypatch.setattr(install, 'is_install', lambda root: root == tmp_path)
    from desktop.update_protocol import RecoverableError
    def missing(root): raise RecoverableError('PENDING_04')
    monkeypatch.setattr(core, 'one_click_service', missing)
    monkeypatch.setattr('desktop.update_service.configured_mature_service',
                        lambda: pytest.fail('No legacy/localhost fallback for E108'))
    with pytest.raises(RecoverableError, match='PENDING_04'):
        configured_update_service()


@pytest.mark.parametrize('url', [None, '', 'http://updates.example.org/stable.json',
    'https://127.0.0.1/stable.json', 'https://localhost/stable.json',
    'https://10.0.0.1/stable.json', 'https://u:p@updates.example.org/stable.json',
    'https://updates.example.org/stable.json?key=x'])
def test_unbound_or_local_feed_blocks_build_before_output_mutation(url, monkeypatch):
    monkeypatch.setattr(builder, 'identity', lambda: pytest.fail('must reject before reading/building source'))
    with pytest.raises(ValueError): builder.initialize(SimpleNamespace(update_feed_url=url))


def test_https_slot_and_single_installer_output_wiring():
    url = 'https://89.45.45.28/mentorflow/updates/e108/'
    assert builder.UPDATE_FEED_URL == url
    assert builder.require_update_feed_url(url) == url
    source = Path(builder.__file__).read_text(encoding='utf-8')
    assert 'Start-Test-Update-Source.cmd' not in source and 'TestSource/Runtime' not in source
    assert 'serve-test-source' not in (ROOT / 'scripts/per_user_install.py').read_text(encoding='utf-8')
    assert "feed = args.work / 'feed-for-04-publication'" in source
    assert "['MentorFlow-Setup.exe']" in source and "'--work-dir', args.work / 'inno-build'" in source


def test_frozen_core_algorithms_unchanged_by_integration():
    selections = {
        'scripts/per_user_install.py': ['transact', 'recover', 'validate_target', '_initial_repair_release', 'handoff_pending'],
        'scripts/per_user_update_core.py': ['check_latest', 'download_latest', 'update_to_latest',
            'apply_and_relaunch', 'apply_update', 'one_click_service', 'resume_pending_on_normal_launch'],
    }
    for path, names in selections.items():
        frozen = subprocess.check_output(['git', 'show', FROZEN_07 + ':' + path], cwd=ROOT)
        current = (ROOT / path).read_bytes()
        def functions(source):
            return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef)}
        left, right = functions(frozen), functions(current)
        for name in names: assert left[name] == right[name], (path, name)


def test_real_signed_per_user_download_flows_through_ui_port(case, monkeypatch):
    from scripts.test_one_click_update_core import setup
    root, service = setup(case, monkeypatch)
    original = (root / install.CURRENT).read_bytes()
    handoffs = []
    monkeypatch.setattr(install, 'handoff_pending', lambda r, **kw:
                        handoffs.append((r, kw)) or {'state': 'APPLYING', 'coordinator_started': True})
    value = CoreUpdatePort(service, window_id=lambda: 'beef')
    try:
        assert value.check_latest()['state'] == 'UPDATE_AVAILABLE'
        result = value.update_to_latest(cancel=threading.Event())
        assert result['state'] == 'PREPARING'
        assert handoffs == [(root, dict(hwnd_hex='beef', reason='normal'))]
        assert core.read_pending(root)['state'] == 'UPDATE_DOWNLOADED'
        assert core.read_pending(root)['target'] == '1.4.1'
        assert (root / install.CURRENT).read_bytes() == original  # Fake coordinator never swaps/launches.
    finally:
        value.close()
