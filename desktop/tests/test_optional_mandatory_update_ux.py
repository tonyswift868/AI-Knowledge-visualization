"""Optional consent versus mandatory gate; offscreen, no real updater launch."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from desktop.update_controller import UpdateController
from desktop.update_lifecycle import CoreUpdatePort
from desktop.tests.test_prelogin_update_ux import prelogin
from desktop.tests.test_update_ui_flow import Port, until, no_external_launch
from desktop.tests.test_login_smooth import app_context


def assert_required(gate, latest='1.4.1'):
    panel = gate.update_entry
    panel.refresh()
    assert gate.login_is_blocked and not gate.primary_button.isEnabled()
    assert not gate.primary_button.isVisible()
    assert not panel.later_button.isVisible()
    assert not panel.exit_update_button.isVisible()
    assert not panel.check_button.isVisible()
    assert panel.exit_button.isVisible() and panel.exit_button.isEnabled()
    assert latest in panel.status.text()


def test_optional_later_preserves_login_and_stops_no_process(prelogin):
    gate, port, controller, *_ = prelogin
    panel = gate.update_entry
    assert panel.later_button.text() == '稍后'
    assert panel.download_button.text() == '更新并重启'
    assert panel.exit_update_button.text() == '退出时更新'
    assert '1.4.1' in panel.status.text() and panel.notes.isVisible() and panel.sizes.isVisible()
    panel.later_button.click()
    assert controller.postponed and not controller.update_on_exit
    assert not panel.notes.isVisible() and not panel.sizes.isVisible()
    assert not gate.login_is_blocked and gate.primary_button.isEnabled()
    requested = Mock(); gate.login_requested.connect(requested)
    gate.primary_button.click(); requested.assert_called_once()
    assert port.calls == [] and gate.isVisible()


def test_optional_update_restart_is_still_single_consent(prelogin):
    gate, port, controller, *_ = prelogin
    gate.update_entry.download_button.click(); until(port.entered.is_set)
    assert not gate.login_is_blocked
    port.release.set(); until(lambda: port.calls == ['download', 'apply'] and not controller.busy)


def test_optional_exit_update_waits_for_normal_close_and_hands_off_once(prelogin):
    gate, port, controller, *_ = prelogin
    closed = Mock(); gate.closed.connect(closed)
    gate.update_entry.exit_update_button.click()
    assert controller.update_on_exit and not controller.login_blocked
    assert port.calls == []
    assert not gate.close()  # Kept alive until the canonical handoff owns shutdown.
    until(port.entered.is_set)
    assert gate.isVisible(); closed.assert_not_called()
    gate.close(); assert port.calls == ['download']
    port.release.set(); until(lambda: port.calls == ['download', 'apply'] and not controller.busy)
    # The core's eventual window-close request is not intercepted a second time.
    assert gate.close(); closed.assert_called_once()


def test_later_cancels_exit_choice_and_session_shutdown_never_starts_update(prelogin):
    gate, port, controller, *_ = prelogin
    controller.schedule_on_exit(); controller.later()
    assert not controller.update_on_exit
    controller.schedule_on_exit(); controller._session_ending()
    assert not controller.update_on_exit
    gate.close(); assert port.calls == []


@pytest.mark.parametrize('failure', ['cancel', 'network'])
def test_optional_failure_keeps_current_app_and_login_usable(prelogin, failure):
    gate, port, controller, *_ = prelogin
    port.result = 'RECOVERABLE_ERROR'
    gate.update_entry.download_button.click(); until(port.entered.is_set)
    if failure == 'cancel': controller.cancel()
    port.release.set(); until(lambda: not controller.busy)
    gate.update_entry.refresh()
    assert not gate.login_is_blocked and gate.primary_button.isEnabled() and gate.isVisible()
    assert 'apply' not in port.calls


@pytest.mark.parametrize('reason,copy', [('security', '安全问题'), ('protocol', '兼容最新服务'),
                                      ('data_compatibility', '数据兼容性')])
def test_mandatory_targets_latest_not_historical_floor_or_release_flag(prelogin, reason, copy):
    gate, port, controller, *_ = prelogin
    port.snapshot.update(state='MANDATORY_UPDATE', current='1.1.0', latest='1.9.0',
                         min_supported_version='1.5.0', mandatory=False, mandatory_reason=reason)
    assert_required(gate, '1.9.0')
    assert '必须更新到最新版本 1.9.0' in gate.update_entry.status.text()
    assert '1.5.0' not in gate.update_entry.status.text()
    assert copy in gate.update_entry.status.text()
    assert gate.update_entry.download_button.text() == '更新到最新版本'
    controller.later(); controller.schedule_on_exit()
    assert not controller.postponed and not controller.update_on_exit and controller.login_blocked


@pytest.mark.parametrize('failure', ['cancel', 'network', 'validation', 'raises'])
def test_mandatory_failure_keeps_gate_and_retry_uses_same_core(prelogin, failure):
    gate, port, controller, *_ = prelogin
    port.snapshot.update(state='MANDATORY_UPDATE', mandatory_reason='security')
    assert_required(gate)
    original_download = port.download_latest
    if failure == 'raises':
        port.download_latest = Mock(side_effect=ValueError('SECRET_PATH raw exception'))
    else:
        port.result = 'RECOVERABLE_ERROR'
        port.snapshot['error_kind'] = 'validation' if failure == 'validation' else 'network'
    gate.update_entry.download_button.click()
    if failure != 'raises':
        until(port.entered.is_set)
        if failure == 'cancel': controller.cancel()
        port.release.set()
    until(lambda: not controller.busy)
    assert_required(gate)
    assert 'SECRET_PATH' not in gate.update_entry.status.text()
    assert 'apply' not in port.calls
    assert gate.update_entry.download_button.text() == '重试'
    port.download_latest = original_download; port.result = 'PREPARING'; port.release.set()
    gate.update_entry.download_button.click()
    until(lambda: port.calls.count('apply') == 1 and not controller.busy)
    assert controller.login_blocked


@pytest.mark.parametrize('action', ['x', 'exit_button'])
def test_mandatory_close_explicitly_quits_even_with_other_windows(prelogin, monkeypatch, action):
    gate, port, *_ = prelogin
    port.snapshot['state'] = 'MANDATORY_UPDATE'; assert_required(gate)
    quit_app = Mock(); monkeypatch.setattr(QApplication.instance(), 'quit', quit_app)
    other = QWidget(); other.show()
    try:
        if action == 'x': gate.close()
        else: gate.update_entry.exit_button.click()
        quit_app.assert_called_once()
    finally: other.close()


def test_required_gate_blocks_main_creation_and_suspends_existing_main(app_context):
    target, app, _ = app_context
    target._show_login_gate(); app.processEvents()
    gate = target.login_gate; port = Port(); controller = UpdateController(port=port)
    gate.bind_update_controller(controller)
    target.api = SimpleNamespace(close=Mock())
    try:
        port.snapshot['state'] = 'MANDATORY_UPDATE'; controller.refresh_login_policy()
        target._show_main({})
        assert target.main is None
        port.snapshot['state'] = 'OPTIONAL_UPDATE'; controller.refresh_login_policy()
        main = QWidget(); target.main = main; main.show(); gate.hide()
        controller.bind_window(main)
        port.snapshot['state'] = 'MANDATORY_UPDATE'; controller.refresh_login_policy()
        assert not main.isVisible() and not main.isEnabled() and gate.isVisible()
        target._show_main({}); assert not main.isVisible()
        port.snapshot['state'] = 'OPTIONAL_UPDATE'; controller.refresh_login_policy()
        assert main.isVisible() and main.isEnabled() and not gate.isVisible()
    finally:
        if target.main is not None: target.main.close(); target.main = None
        controller.shutdown()


@pytest.mark.parametrize('states', [('MANDATORY_UPDATE', 'REPAIR_REQUIRED'),
                                  ('REPAIR_REQUIRED', 'MANDATORY_UPDATE')])
def test_repair_and_mandatory_together_never_unlock_login(prelogin, states):
    gate, port, controller, *_ = prelogin
    for state in states:
        port.snapshot['state'] = state; gate.update_entry.refresh()
    assert controller.repair_required and controller.mandatory_required and gate.login_is_blocked
    assert gate.update_entry.download_button.text() == '修复并检查更新'
    assert '修复' in gate.update_entry.download_button.text()
    assert not gate.update_entry.later_button.isVisible()


def test_adapter_preserves_sparse_core_gate_and_public_release_details():
    service = SimpleNamespace(snapshot=dict(state='UPDATE_AVAILABLE', latest='1.9.0', mandatory=False,
        release_notes='改进学习体验', expected_download_bytes=12345, update_path='FULL_FALLBACK'),
        current='1.1.0', apply_bridge=None, close=Mock())
    port = CoreUpdatePort(service)
    try:
        port.set_startup_state(dict(state='MANDATORY_UPDATE', latest='1.9.0', mandatory_reason='protocol'))
        snap = port.snapshot
        assert snap['state'] == 'MANDATORY_UPDATE' and snap['latest'] == '1.9.0'
        assert snap['notes'] == '改进学习体验' and snap['total'] == 12345
        assert snap['update_path'] == 'FULL_FALLBACK' and snap['mandatory_reason'] == 'protocol'
    finally: port.close()


def test_views_do_not_calculate_mandatory_floor_or_parse_signed_feed():
    root = Path(__file__).parents[1]
    for name in ('update_controller.py', 'update_panel.py', 'gui_update_runtime.py', 'login_shell.py'):
        tree = ast.parse((root / name).read_text(encoding='utf-8'))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (names | attrs) & {'version_key', 'verify_release', 'startup_version_policy', 'signed'}


@pytest.mark.parametrize('failure', ['cancel', 'network'])
def test_exit_time_failure_leaves_existing_window_usable(prelogin, failure):
    gate, port, controller, *_ = prelogin
    controller.schedule_on_exit()
    port.result = 'RECOVERABLE_ERROR'
    gate.close(); until(port.entered.is_set)
    if failure == 'cancel': controller.cancel()
    port.release.set(); until(lambda: not controller.busy and not controller._exit_update_running)
    assert gate.isVisible() and gate.isEnabled() and not gate.login_is_blocked
    assert port.calls == ['download']
    assert gate.close()


def test_exit_time_choice_follows_active_window_after_login(prelogin):
    gate, port, controller, *_ = prelogin
    controller.schedule_on_exit()
    main = QWidget(); main.show(); gate.hide(); controller.bind_window(main)
    try:
        assert not main.close(); until(port.entered.is_set)
        port.release.set(); until(lambda: port.calls == ['download', 'apply'] and not controller.busy)
        assert main.close()
    finally: port.release.set(); main.close()


@pytest.mark.parametrize('feed', ['UNAVAILABLE_RECOVERABLE', 'NO_FEED'])
def test_unavailable_core_policy_check_cannot_revoke_known_mandatory(prelogin, feed):
    gate, _, controller, *_ = prelogin
    service = SimpleNamespace(snapshot={'state': 'UPDATE_AVAILABLE'}, current='1.1.0',
                              apply_bridge=None, close=Mock())
    controller.configure_service(service)
    gate.set_startup_policy(dict(state='MANDATORY_UPDATE', latest='1.9.0', login_allowed=False))
    assert_required(gate, '1.9.0')
    # These are core results, not a UI interpretation of release/floor metadata.
    controller.port.set_startup_state(dict(state='HEALTHY', feed=feed, login_allowed=True))
    assert_required(gate, '1.9.0')
    assert gate.update_entry.download_button.text() == '重试'
    controller.port.set_startup_state(dict(state='HEALTHY', feed='VERIFIED', login_allowed=True))
    gate.update_entry.refresh(); assert not gate.login_is_blocked


def test_missing_service_can_retry_connection_without_unlocking_gate(prelogin):
    gate, _, controller, *_ = prelogin
    service = SimpleNamespace(snapshot={'state': 'UPDATE_AVAILABLE', 'latest': '1.9.0'},
        current='1.1.0', apply_bridge=None, close=Mock(), cancel_download=Mock(),
        download=Mock(return_value={'state': 'PREPARING'}),
        apply_and_restart=Mock(return_value={'state': 'PREPARING'}))
    factory = Mock(side_effect=[RuntimeError('SECRET_ENDPOINT'), service])
    controller.configure_service(None, factory=factory)
    gate.set_startup_policy(dict(state='MANDATORY_UPDATE', latest='1.9.0', login_allowed=False))
    assert_required(gate, '1.9.0')
    assert '预计下载 检查后确认' in gate.update_entry.sizes.text()
    gate.update_entry.download_button.click()
    until(lambda: not controller.busy)
    assert_required(gate, '1.9.0')
    assert 'SECRET_ENDPOINT' not in gate.update_entry.status.text()
    controller.cancel()  # A previous cancel must not poison the next retry.
    gate.update_entry.download_button.click()
    until(lambda: service.apply_and_restart.called and not controller.busy)
    assert factory.call_count == 2 and controller.login_blocked
    service.download.assert_called_once(); service.apply_and_restart.assert_called_once()


def test_unverified_recovery_runtime_stays_in_repair_gate(prelogin):
    gate, _, controller, *_ = prelogin
    controller.configure_service(None)
    gate.set_startup_policy(dict(state='FATAL_UNRECOVERABLE', login_allowed=False))
    gate.update_entry.refresh()
    assert gate.login_is_blocked and controller.repair_required
    assert not gate.primary_button.isVisible() and not gate.update_entry.later_button.isVisible()
    assert gate.update_entry.exit_button.isVisible()


@pytest.mark.parametrize('path,copy', [('FILE_LEVEL_INCREMENTAL', '仅下载有变化的文件'),
                                    ('FULL_FALLBACK', '将下载完整更新')])
def test_transfer_path_is_presented_as_plain_language(prelogin, path, copy):
    gate, port, *_ = prelogin
    port.snapshot['update_path'] = path
    gate.update_entry.refresh()
    assert copy in gate.update_entry.sizes.text()
    assert path not in gate.update_entry.sizes.text()


def test_mandatory_copy_and_actions_fit_compact_gate(prelogin, tmp_path):
    from PySide6.QtGui import QFontDatabase
    gate, port, *_ = prelogin
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    gate.setStyleSheet(gate.styleSheet() + '\nQWidget {font-family: "Microsoft YaHei UI";font-size: 12px;}')
    port.snapshot.update(state='MANDATORY_UPDATE', mandatory_reason='data_compatibility')
    panel = gate.update_entry; panel.refresh()
    until(lambda: panel.status.height() >= panel.status.heightForWidth(panel.status.width()))
    assert panel.status.geometry().bottom() < panel.download_button.geometry().top()
    assert panel.download_button.geometry().bottom() <= panel.height()
    assert gate.grab().save(str(tmp_path/'mandatory-complete.png'))


@pytest.mark.parametrize('action', ['cancel', 'shutdown'])
def test_service_retry_cancel_or_exit_cannot_late_apply(prelogin, action):
    import threading
    gate, _, controller, *_ = prelogin
    entered, release = threading.Event(), threading.Event()
    service = SimpleNamespace(close=Mock())
    def factory():
        entered.set(); release.wait(3)
        return service
    controller.configure_service(None, factory=factory)
    gate.set_startup_policy(dict(state='MANDATORY_UPDATE', latest='1.9.0', login_allowed=False))
    gate.update_entry.refresh(); gate.update_entry.download_button.click(); until(entered.is_set)
    try:
        getattr(controller, action)()
        release.set(); until(lambda: service.close.called and not controller.busy)
        service.close.assert_called_once()
        assert controller.login_blocked
        assert controller.service is None
    finally: release.set()


def test_core_gate_arriving_during_main_construction_prevents_show(app_context, monkeypatch):
    target, app, _ = app_context
    target._show_login_gate(); app.processEvents()
    port = Port(); controller = UpdateController(port=port)
    monkeypatch.setattr(app, '_mentorflow_updater', controller)
    target.login_gate.bind_update_controller(controller)
    target.api = SimpleNamespace(close=Mock())
    target.service = SimpleNamespace(origin='http://127.0.0.1:0', stop=Mock())
    main = QWidget()
    def create(*_, **__):
        port.snapshot['state'] = 'MANDATORY_UPDATE'
        return main
    monkeypatch.setattr('desktop.main_window.MainWindow', create)
    try:
        # Native identity now requires workspace bootstrap before construction.
        # Exercise the update race only after satisfying that independent gate.
        target._show_main({})
        assert target.main is None
        target.bootstrap_result = SimpleNamespace(profile=None, vault_manager=None,
            local_asset_lifecycle=None, snapshot=None, close=Mock())
        target._show_main({})
        assert target.main is main and not main.isVisible() and not main.isEnabled()
        assert target.login_gate.isVisible() and target.login_gate.login_is_blocked
    finally:
        main.close(); target.main = None; controller.shutdown()


def test_automatic_check_does_not_undo_later_for_same_offer(prelogin):
    gate, port, controller, *_ = prelogin
    controller.later(); controller.start(); until(lambda: not controller.busy)
    gate.update_entry.refresh()
    assert controller.postponed and not gate.update_entry.later_button.isVisible()
    gate.update_entry.check_button.click(); until(lambda: not controller.busy)
    gate.update_entry.refresh(); assert gate.update_entry.later_button.isVisible()


def test_exit_time_check_race_still_downloads_once(prelogin):
    import threading
    gate, port, controller, *_ = prelogin
    entered, release = threading.Event(), threading.Event()
    def check():
        entered.set(); release.wait(3)
        return dict(port.snapshot)
    port.check_latest = check
    controller.schedule_on_exit(); controller.run('check'); until(entered.is_set)
    try:
        assert not gate.close()
        release.set(); until(port.entered.is_set)
        port.release.set(); until(lambda: port.calls == ['download', 'apply'] and not controller.busy)
        assert gate.close()
    finally: release.set(); port.release.set()


def test_exit_time_no_longer_needed_does_not_trap_close(prelogin):
    gate, port, controller, *_ = prelogin
    port.result = 'UP_TO_DATE'
    controller.schedule_on_exit(); assert not gate.close(); until(port.entered.is_set)
    port.release.set(); until(lambda: not gate.isVisible())
    assert port.calls == ['download'] and not controller._exit_update_running


def test_optional_unavailable_connection_can_retry_and_stays_usable(prelogin):
    gate, _, controller, *_ = prelogin
    service = SimpleNamespace(snapshot={'state': 'UPDATE_AVAILABLE', 'latest': '1.9.0'},
        current='1.1.0', apply_bridge=None, close=Mock(), check=Mock(return_value={'state': 'UPDATE_AVAILABLE'}))
    controller.configure_service(None, factory=lambda: service)
    gate.update_entry.refresh()
    assert gate.update_entry.check_button.isEnabled() and not gate.login_is_blocked
    gate.update_entry.check_button.click(); until(lambda: service.check.called and not controller.busy)
    assert controller.service is service and not gate.login_is_blocked
