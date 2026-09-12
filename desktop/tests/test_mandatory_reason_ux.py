"""Consumer tests for 02's public snapshot; no feed or policy implementation."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop.tests.test_prelogin_update_ux import prelogin
from desktop.tests.test_update_ui_flow import until, no_external_launch


REASON_CASES = [
    ('security', '当前版本存在必须修复的安全问题，需要更新到最新版本后才能继续使用 MentorFlow。'),
    ('protocol', '当前版本已无法兼容最新服务，需要更新到最新版本后才能继续使用 MentorFlow。'),
    ('data_compatibility', '当前版本的数据兼容性已发生变化，需要更新到最新版本后才能继续使用 MentorFlow。'),
    ('support_floor', '当前版本已停止支持，需要更新到最新版本后才能继续使用 MentorFlow。'),
]
FALLBACK = '当前版本需要更新到最新版本后才能继续使用 MentorFlow。'


def assert_gate(gate, explanation):
    panel = gate.update_entry
    panel.refresh()
    assert gate.login_is_blocked and not gate.primary_button.isEnabled()
    assert not gate.primary_button.isVisible()
    assert not panel.later_button.isVisible() and not panel.exit_update_button.isVisible()
    assert not panel.check_button.isVisible()
    assert panel.exit_button.isVisible() and panel.exit_button.text() == '退出 MentorFlow'
    assert '必须更新到最新版本 1.4.0。' in panel.status.text()
    assert explanation in panel.status.text()
    assert '1.2.0' not in panel.status.text() + panel.sizes.text()


@pytest.mark.parametrize('reason,explanation', REASON_CASES)
def test_public_reason_reaches_historical_latest_gate(prelogin, reason, explanation):
    gate, _, controller, *_ = prelogin
    # Public shape verified at 521ba9f. The UI never receives a signed envelope
    # or a release object and never chooses the historical floor as its target.
    service = SimpleNamespace(current='1.0.0', apply_bridge=None, close=Mock(), snapshot={
        'state': 'UPDATE_AVAILABLE', 'release_notes': '改进课程体验。',
        'expected_download_bytes': 1048576, 'update_path': 'FULL_FALLBACK'})
    controller.configure_service(service)
    controller.port.set_startup_state(dict(state='MANDATORY_UPDATE', current='1.0.0',
        latest='1.4.0', min_supported_version='1.2.0', mandatory_reason=reason))
    assert_gate(gate, explanation)
    panel = gate.update_entry
    assert '1.0.0' in panel.version_label.text()
    assert '最新版本 1.4.0' in panel.sizes.text()
    assert panel.notes.isVisible() and panel.notes.text() == '改进课程体验。'
    assert panel.download_button.text() == '更新到最新版本'
    assert reason not in panel.status.text()


@pytest.mark.parametrize('unknown', ['future_reason_v2', '', None, ['future_reason'], {'code': 'future_reason'}],
                         ids=['future-enum', 'empty', 'null', 'list', 'object'])
def test_unknown_public_reason_replaces_old_explanation_without_crash(prelogin, unknown):
    gate, port, *_ = prelogin
    port.snapshot.update(state='MANDATORY_UPDATE', current='1.0.0', latest='1.4.0',
                         mandatory_reason='security')
    assert_gate(gate, REASON_CASES[0][1])
    # Same target, fresh explicit reason: do not reuse an obsolete category.
    port.snapshot['mandatory_reason'] = unknown
    assert_gate(gate, FALLBACK)
    text = gate.update_entry.status.text()
    assert '安全问题' not in text and 'future_reason' not in text
    assert gate.update_entry.download_button.text() == '更新到最新版本'


@pytest.mark.parametrize('reason', [value[0] for value in REASON_CASES] + ['future_reason_v2'])
@pytest.mark.parametrize('previous_gate', [False, True])
def test_optional_state_ignores_stale_or_unknown_reason(prelogin, reason, previous_gate):
    gate, port, controller, *_ = prelogin
    if previous_gate:
        port.snapshot.update(state='MANDATORY_UPDATE', mandatory_reason='security')
        gate.update_entry.refresh()
        assert gate.login_is_blocked
    port.snapshot.update(state='OPTIONAL_UPDATE', mandatory_reason=reason)
    panel = gate.update_entry; panel.refresh()
    assert not gate.login_is_blocked and not controller.mandatory_required
    assert gate.primary_button.isEnabled()
    assert panel.later_button.isVisible() and panel.later_button.text() == '稍后'
    assert panel.download_button.isVisible() and panel.download_button.text() == '更新并重启'
    assert panel.exit_update_button.isVisible() and panel.exit_update_button.text() == '退出时更新'
    assert not panel.exit_button.isVisible() and '必须更新' not in panel.status.text()
    assert reason not in panel.status.text()
    requested = Mock(); gate.login_requested.connect(requested)
    gate.primary_button.click(); requested.assert_called_once()


@pytest.mark.parametrize('failure', ['network', 'validation', 'cancel'])
def test_temporary_empty_reason_preserves_required_explanation_and_retry(prelogin, failure):
    gate, port, controller, *_ = prelogin
    port.snapshot.update(state='MANDATORY_UPDATE', current='1.0.0', latest='1.4.0',
                         mandatory_reason='protocol')
    assert_gate(gate, REASON_CASES[1][1])
    download = port.download_latest
    def transient_download(progress, cancel):
        # 02's adapter exposes an empty reason outside MANDATORY_UPDATE.
        port.snapshot.update(state='DOWNLOADING', mandatory_reason='')
        return download(progress, cancel)
    port.download_latest = transient_download
    port.result = 'RECOVERABLE_ERROR'
    port.snapshot['error_kind'] = 'validation' if failure == 'validation' else 'network'
    gate.update_entry.download_button.click(); until(port.entered.is_set)
    try:
        assert_gate(gate, REASON_CASES[1][1])
        if failure == 'cancel': gate.update_entry.cancel_button.click()
        port.release.set(); until(lambda: not controller.busy)
        assert_gate(gate, REASON_CASES[1][1])
        assert gate.update_entry.download_button.text() == '重试'
        assert port.calls == ['download']  # No apply after failure or cancellation.
    finally: port.release.set()
