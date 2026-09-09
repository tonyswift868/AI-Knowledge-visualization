"""One-click source acceptance. Offscreen only; external launches are blocked."""
import ast
import os
from pathlib import Path
import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings, QTimer, QPoint
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea

from desktop.update_controller import UpdateController, UpdateState
from desktop.update_lifecycle import CoreUpdatePort
from desktop.update_panel import UpdatePanel, release_summary, release_details
from desktop.tests.test_mature_update import Feed


def until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.instance().processEvents()
        if predicate():
            QApplication.instance().processEvents()
            return
        time.sleep(.005)
    raise AssertionError('UI condition did not become true')


@pytest.fixture(autouse=True)
def no_external_launch(monkeypatch):
    import subprocess
    import webbrowser
    from PySide6.QtGui import QDesktopServices
    def forbidden(*_, **__):
        pytest.fail('External launch forbidden in source tests')
    for target in ('open', 'open_new', 'open_new_tab'):
        monkeypatch.setattr(webbrowser, target, forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(os, 'system', forbidden)
    monkeypatch.setattr(os, 'startfile', forbidden)
    monkeypatch.setattr(QDesktopServices, 'openUrl', forbidden)
    assert os.environ.get('QT_QPA_PLATFORM') == 'offscreen'


class Port:
    """No service/cache/manifest fields: UI depends only on the port."""
    def __init__(self, state='UPDATE_AVAILABLE', result='PREPARING'):
        self.snapshot = dict(state=state, current='1.4.0', latest='1.4.1', notes='改进更新体验。\n修复界面问题。',
            downloaded=0, total=round(8.7*1024**2), has_update=True, available=True, error_kind='network')
        self.result = result
        self.calls = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.apply_result = 'PREPARING'
        self.on_apply = lambda: None

    def check_latest(self):
        self.calls.append('check')
        return dict(self.snapshot)

    def download_latest(self, progress, cancel):
        self.calls.append('download')
        self.snapshot.update(state='DOWNLOADING', downloaded=4*1024**2)
        self.entered.set(); progress(dict(self.snapshot))
        deadline = time.monotonic() + 3
        while not self.release.wait(.005) and not cancel.is_set():
            if time.monotonic() > deadline:
                raise TimeoutError('test did not release download')
        self.snapshot['state'] = self.result
        if cancel.is_set():
            self.snapshot.update(state='RECOVERABLE_ERROR', error_kind='cancelled')
        return dict(self.snapshot)

    def apply_and_relaunch(self):
        self.calls.append('apply')
        self.on_apply()
        self.snapshot['state'] = self.apply_result
        return dict(self.snapshot)

    def close(self):
        self.calls.append('close'); self.release.set()


@pytest.fixture
def panel():
    port = Port()
    view = UpdatePanel(port=port, auto_delay_ms=60000)
    view.resize(850, 600); view.show()
    QApplication.instance().processEvents()
    yield view, port
    view.shutdown(); view.close()


def visible_actions(panel):
    return [b.text() for b in panel.findChildren(QPushButton) if b.isVisible()]


def test_one_primary_click_downloads_then_applies_once_and_saves_ui(panel):
    view, port = panel
    assert visible_actions(view) == ['更新到最新版本']
    assert not hasattr(view, 'apply_button')
    assert '新版本 1.4.1' in view.sizes.text() and '预计下载 8.7 MB' in view.sizes.text()
    def state_saved():
        assert view.controller.settings.contains(view.controller.key + 'UpdatePanel/geometry')
    port.on_apply = state_saved
    view.download_button.click()
    first = view._thread
    view.download_button.click()
    assert first is view._thread
    until(port.entered.is_set)
    view.refresh()
    assert view.progress.value() == 46
    assert view.transfer_label.text() == '4.0 MB / 8.7 MB'
    assert visible_actions(view) == ['取消']
    port.release.set()
    until(lambda: 'apply' in port.calls and not view.controller.busy)
    view.refresh()
    assert port.calls == ['download', 'apply']
    assert 'MentorFlow 将自动重新启动' in view.status.text()
    assert visible_actions(view) == []
    view.controller._finished('download')
    assert port.calls.count('apply') == 1


@pytest.mark.parametrize('result', ['RECOVERABLE_ERROR', 'DEFERRED_TO_NEXT_LAUNCH'])
def test_download_failure_or_deferral_never_auto_applies(panel, result):
    view, port = panel; port.result = result
    view.download_button.click(); until(port.entered.is_set); port.release.set()
    until(lambda: not view.controller.busy)
    view.refresh()
    assert 'apply' not in port.calls
    expected = ('更新已下载，将在下次启动时完成。' if result == 'DEFERRED_TO_NEXT_LAUNCH'
                else '暂时无法检查更新，请稍后重试。')
    assert view.status.text() == expected
    if result == 'DEFERRED_TO_NEXT_LAUNCH': assert visible_actions(view) == []


def test_deferred_apply_return_is_not_overwritten_by_prepared_snapshot(panel):
    view, port = panel
    def apply():
        port.calls.append('apply')
        return {'state': 'DEFERRED_TO_NEXT_LAUNCH'}
    port.apply_and_relaunch = apply
    view.download_button.click(); port.release.set()
    until(lambda: 'apply' in port.calls and not view.controller.busy)
    view.refresh()
    assert view.status.text() == '更新已下载，将在下次启动时完成。'
    assert port.snapshot['state'] == 'PREPARING'


def test_cancel_prevents_restart_then_retry_is_one_click(panel):
    view, port = panel
    view.download_button.click(); until(port.entered.is_set); view.refresh()
    view.cancel_button.click(); port.release.set()
    until(lambda: not view.controller.busy)
    view.refresh()
    assert 'apply' not in port.calls and '取消' in view.status.text()
    port.snapshot['error_kind'] = 'network'
    view.download_button.click()
    until(lambda: 'apply' in port.calls and not view.controller.busy)
    assert port.calls.count('download') == 2 and port.calls.count('apply') == 1


@pytest.mark.parametrize('action', ['close', 'session'])
def test_close_or_session_ending_stops_automatic_apply(panel, action):
    view, port = panel
    view.download_button.click(); until(port.entered.is_set)
    if action == 'close': view.close()
    else: view.controller._session_ending()
    port.release.set(); until(lambda: not view.controller.busy)
    assert 'apply' not in port.calls


@pytest.mark.parametrize('kind,expected', [
    ('network', '暂时无法检查更新，请稍后重试。'),
    ('validation', '更新文件验证失败，当前版本不会受到影响。'),
])
def test_errors_inline_never_render_core_diagnostics(panel, kind, expected):
    view, port = panel
    port.snapshot.update(state='RECOVERABLE_ERROR', error_kind=kind,
        message='HTTP stack manifest SHA receipt hardlink helper SECRET_SENTINEL')
    view.refresh()
    assert view.status.text() == expected
    assert 'SECRET_SENTINEL' not in ' '.join(label.text() for label in view.findChildren(QLabel))


def test_latest_version_and_notes_expand_only_inside_card(panel):
    view, port = panel
    notes = '第一条改进\n第二条改进\n第三条改进\n第四条改进\n第五条改进'
    port.snapshot['notes'] = notes
    view.refresh()
    assert view.notes.text().splitlines() == notes.splitlines()[:3]
    before = set(QApplication.topLevelWidgets())
    view.notes_more.click()
    assert view.details.isVisible() and view.details.toPlainText() == notes and view.details.isReadOnly()
    assert set(QApplication.topLevelWidgets()) == before
    view.notes_more.click(); assert not view.details.isVisible()
    port.snapshot.update(state='UP_TO_DATE', current='1.4.1', has_update=False)
    view.refresh()
    assert view.status.text() == '✓ 已是最新版本'
    assert view.version_label.text() == '当前版本 1.4.1'
    assert visible_actions(view) == ['检查更新']


def test_notes_bounded_plain_text():
    assert len(release_summary('x'*900)) == 240
    assert len(release_details('x'*6000)) == 4000
    assert release_details('manifest\nSHA\nHTTP stack\nreceipt\nhelper\nhardlink\nhttps://github.com/release') == '此更新包含功能改进与问题修复。'
    assert UpdatePanel._size(2*1024**3) == '2.0 GB'


def test_startup_async_and_old_ui_pending_flags_not_authority(panel):
    view, port = panel
    entered, release = threading.Event(), threading.Event()
    def check():
        entered.set(); release.wait(2)
        return dict(port.snapshot)
    port.check_latest = check
    view.controller.settings.setValue(view.controller.key + 'pending_release', 'untrusted_old_flag')
    ticks = []; timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda: ticks.append(True)); timer.start()
    try:
        view.controller.start(); until(lambda: entered.is_set() and len(ticks) > 2)
        release.set(); until(lambda: not view.controller.busy)
        assert 'download' not in port.calls and 'apply' not in port.calls
    finally: release.set(); timer.stop()


def test_actual_settings_card_port_and_progress_visibility(tmp_path, monkeypatch):
    from desktop.system_page import SystemPage
    from desktop.style import STYLE
    app = QApplication.instance(); port = Port()
    controller = UpdateController(port=port)
    monkeypatch.setattr(app, '_mentorflow_updater', controller, raising=False)
    monkeypatch.setenv('MENTORFLOW_USER_DATA_ROOT', str(tmp_path/'data'))
    page = SystemPage({}, 'http://127.0.0.1:0', lambda: None, object())
    page.setStyleSheet(STYLE); page.resize(1100, 850); page.show(); app.processEvents()
    view = page.update_panel; scroller = page.findChild(QScrollArea)
    try:
        scroller.verticalScrollBar().setValue(view.mapTo(scroller.widget(), QPoint()).y() + 70)
        view.download_button.click(); until(port.entered.is_set); view.refresh(); app.processEvents()
        point = view.mapTo(scroller.viewport(), QPoint())
        assert point.y() >= 0 and point.y() + view.height() <= scroller.viewport().height()
        port.release.set(); until(lambda: 'apply' in port.calls and not controller.busy)
        view.refresh(); assert not hasattr(view, 'apply_button')
    finally: page.close(); controller.shutdown()


def test_about_uses_same_one_click_port_and_no_dialog_for_release_notes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from desktop.login_shell import LoginGateWindow
    from desktop import gui_update_runtime
    app = QApplication.instance(); port = Port()
    controller = UpdateController(port=port)
    monkeypatch.setattr(app, '_mentorflow_updater', controller, raising=False)
    monkeypatch.setattr(gui_update_runtime, 'notify_health', Mock())
    monkeypatch.setattr('scripts.install_product.configured', lambda: SimpleNamespace(uat=False))
    gate = LoginGateWindow()
    try:
        gui_update_runtime.attach_updates(app, gate, SimpleNamespace(results={'SECURITY_GATE': True}), tmp_path/'install')
        gate.show(); until(lambda: gate.about_button.isEnabled())
        gate.about_button.click(); app.processEvents()
        view = gate.findChild(UpdatePanel)
        assert view is not None and view.controller is controller
        assert visible_actions(view) == ['更新到最新版本']
        view.download_button.click(); until(port.entered.is_set); port.release.set()
        until(lambda: port.calls.count('apply') == 1 and not controller.busy)
    finally: gate.close(); controller.shutdown()


def test_future_port_failure_text_is_not_exposed_or_auto_retried(panel):
    view, port = panel
    def failed(progress, cancel):
        port.calls.append('download')
        raise RuntimeError('HTTP stack SECRET_SENTINEL manifest SHA receipt helper')
    port.download_latest = failed
    view.download_button.click(); until(lambda: not view.controller.busy)
    view.refresh()
    assert view.status.text() == '暂时无法检查更新，请稍后重试。'
    assert port.calls == ['download']


def test_cancel_after_core_ready_before_callback_prevents_apply(panel):
    view, port = panel
    # Finish synchronously on the worker, but cancel before the GUI consumes
    # finished. This must not produce an unsolicited restart after cancellation.
    port.release.set()
    view.download_button.click()
    view._thread.join(timeout=2)
    assert not view._thread.is_alive()
    view.controller.cancel()
    QApplication.instance().processEvents()
    assert 'apply' not in port.calls


def test_missing_window_does_not_invent_core_deferral(panel):
    view, port = panel
    view.controller._forget_window(id(view))
    view.controller.run('apply_and_restart')
    assert view.controller.state == UpdateState.RECOVERABLE_ERROR
    assert 'apply' not in port.calls


@pytest.mark.parametrize('state', ['UP_TO_DATE', 'UPDATE_AVAILABLE', 'DOWNLOADING',
    'PREPARING', 'DEFERRED_TO_NEXT_LAUNCH', 'RECOVERABLE_ERROR'])
def test_offscreen_state_card_evidence(panel, tmp_path, state):
    from PySide6.QtGui import QFontDatabase
    from desktop.style import STYLE
    view, port = panel
    port.snapshot.update(state=state, downloaded=4*1024**2,
        has_update=state != 'UP_TO_DATE', current='1.4.1' if state == 'UP_TO_DATE' else '1.4.0')
    # The offscreen plugin does not enumerate Windows' registered fonts.
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    assert font_id >= 0
    assert QFontDatabase.addApplicationFont('C:/Windows/Fonts/seguisym.ttf') >= 0
    family = QFontDatabase.applicationFontFamilies(font_id)[0]
    view.setStyleSheet(STYLE + f'\nQWidget {{ font-family: "{family}"; }}')
    view.resize(700, 450); view.refresh()
    QApplication.instance().processEvents()
    assert view.grab().save(str(tmp_path / (state + '.png')))
    assert sum(button.isVisible() and bool(button.property('primary'))
        for button in view.findChildren(QPushButton)) <= 1


@pytest.fixture
def feed(tmp_path):
    value = Feed(tmp_path)
    yield value
    value.close()


def test_real_signed_download_auto_handoff_and_data_untouched(feed):
    service = feed.service(); service.check(); calls = []
    service.apply_bridge = lambda *_: calls.append('apply')
    view = UpdatePanel(service=service, auto_delay_ms=60000); view.show()
    try:
        QApplication.instance().processEvents(); view.download_button.click()
        until(lambda: calls and not view.controller.busy)
        assert calls == ['apply'] and service.snapshot['state'] == 'RESTART_REQUIRED'
        assert (feed.app/'MentorFlow.exe').read_bytes() == feed.old_files['MentorFlow.exe']
    finally: view.shutdown(); view.close()


def test_real_core_cancel_and_retry_auto_applies(feed):
    feed.publish('1.1.1', {**feed.old_files, '_internal/frontend/ui.txt': b'retry\n'*100000})
    feed.delay = .01
    service = feed.service(); service.check(); calls = []
    service.apply_bridge = lambda *_: calls.append('apply')
    view = UpdatePanel(service=service, auto_delay_ms=60000); view.show()
    try:
        QApplication.instance().processEvents(); view.download_button.click()
        first = view._thread; view.download_button.click(); assert view._thread is first
        until(lambda: service.snapshot.get('actual_downloaded_bytes', 0) > 0)
        view.refresh(); view.cancel_button.click()
        until(lambda: not view.controller.busy)
        view.refresh(); assert calls == [] and '取消' in view.status.text()
        view.download_button.click()
        until(lambda: calls and not view.controller.busy, seconds=8)
        assert calls == ['apply']
    finally: view.shutdown(); view.close()


@pytest.mark.parametrize('action', ['close', 'session'])
def test_close_during_core_apply_recheck_never_handoffs(feed, monkeypatch, action):
    service = feed.service(); service.check(); service.download(); calls = []
    service.apply_bridge = lambda *_: calls.append('apply')
    view = UpdatePanel(service=service, auto_delay_ms=60000); view.show()
    entered, release = threading.Event(), threading.Event()
    original = service.cache.transport.fetch_metadata
    def delayed(*args):
        entered.set(); release.wait(2); return original(*args)
    monkeypatch.setattr(service.cache.transport, 'fetch_metadata', delayed)
    try:
        QApplication.instance().processEvents(); view.controller.run('apply_and_restart')
        until(entered.is_set)
        if action == 'close': view.close()
        else: view.controller._session_ending()
        release.set(); until(lambda: not view.controller.busy)
        assert calls == []
    finally: release.set(); view.shutdown(); view.close()


@pytest.mark.parametrize('kind', ['offline', 'signature', 'withdrawn'])
def test_core_failures_never_handoff(feed, kind):
    service = feed.service(); service.check(); calls = []
    service.apply_bridge = lambda *_: calls.append('apply')
    if kind == 'offline': service.metadata_url = feed.origin+'/offline'
    elif kind == 'signature': feed.raw = b'unsigned manifest secret'
    else:
        feed.signed['metadata_version'] += 1; feed.signed['withdrawn'] = True; feed.sign(feed.signed)
    view = UpdatePanel(service=service, auto_delay_ms=60000); view.show()
    try:
        QApplication.instance().processEvents(); view.download_button.click()
        until(lambda: view.controller.state == UpdateState.RECOVERABLE_ERROR and not view.controller.busy)
        view.refresh(); assert calls == []
        assert view.status.text() == ('暂时无法检查更新，请稍后重试。' if kind == 'offline'
            else '更新文件验证失败，当前版本不会受到影响。')
    finally: view.shutdown(); view.close()


@pytest.mark.parametrize('code', ['DEFERRED_TO_NEXT_LAUNCH', 'PENDING_INSTALL'])
def test_adapter_normalizes_core_deferral_without_private_state(feed, monkeypatch, code):
    from scripts import per_user_install, per_user_update_core
    service = feed.service(); service.check(); service.download()
    monkeypatch.setattr(per_user_install, 'is_install', lambda _: True)
    handoff = Mock(return_value={'state': code})
    monkeypatch.setattr(per_user_update_core, 'apply_and_relaunch', handoff)
    port = CoreUpdatePort(service, window_id=lambda: 'a'*16)
    assert port.apply_and_relaunch()['state'] == 'DEFERRED_TO_NEXT_LAUNCH'
    handoff.assert_called_once_with(service.install_root, hwnd_hex='a'*16, reason='normal', service=service)
    assert not {'release', 'stage', 'manifest', 'receipt', 'cache'}.intersection(port.snapshot)
    port.close()


def test_port_update_to_latest_delegates_download_and_apply_in_order(feed):
    service = feed.service(); service.check(); events = []
    service.apply_bridge = lambda *_: events.append('apply')
    port = CoreUpdatePort(service)
    result = port.update_to_latest(lambda s: events.append(s['state']), threading.Event(), lambda: events.append('save-ui'))
    assert result['state'] == 'PREPARING'
    assert events.index('save-ui') < events.index('apply')
    assert events.count('apply') == 1
    port.close()


def test_adapter_failed_pending_admission_never_applies(feed, monkeypatch):
    service = feed.service(); service.check(); calls = []
    service.apply_bridge = lambda *_: calls.append('apply')
    monkeypatch.setattr('scripts.per_user_install.is_install', lambda _: True)
    monkeypatch.setattr('scripts.per_user_update_core.remember_download', Mock(side_effect=RuntimeError('opaque core failure')))
    port = CoreUpdatePort(service)
    result = port.update_to_latest(lambda _: None, threading.Event(), lambda: calls.append('save-ui'))
    assert result['state'] == 'RECOVERABLE_ERROR' and calls == []
    port.close()


def test_adapter_interrupted_network_is_not_presented_as_user_cancel(feed):
    service = feed.service(); service.check()
    service.download = lambda: service.snapshot.update(state='DOWNLOAD_PAUSED_OR_INTERRUPTED')
    port = CoreUpdatePort(service)
    result = port.download_latest(lambda _: None, threading.Event())
    assert result['state'] == 'RECOVERABLE_ERROR' and result['error_kind'] == 'network'
    port.close()


def test_frozen_core_initial_preparation_still_presents_download(feed):
    service = feed.service(); service.check(); states = []
    port = CoreUpdatePort(service)
    def download():
        service.snapshot['state'] = 'PREPARING'
        states.append(port.snapshot['state'])
        service.snapshot['state'] = 'FAILED_RECOVERABLE'
    service.download = download
    port.download_latest(lambda _: None, threading.Event())
    assert states == ['DOWNLOADING']
    port.close()


def test_explicit_core_deferral_overrides_stale_ready_hint(feed):
    service = feed.service(); service.check(); service.download()
    service.apply_bridge = lambda *_: {'state': 'PENDING_INSTALL', 'ui_state': 'READY_TO_APPLY'}
    port = CoreUpdatePort(service)
    assert port.apply_and_relaunch()['state'] == 'DEFERRED_TO_NEXT_LAUNCH'
    port.close()


def test_ui_has_no_asset_parser_network_launcher_or_transaction():
    root = Path(__file__).parents[1]
    for name in ('update_panel.py', 'update_controller.py', 'update_lifecycle.py'):
        tree = ast.parse((root/name).read_text(encoding='utf-8'))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert not names.intersection({'subprocess', 'webbrowser', 'httpx', 'requests', 'hashlib', 'json'})
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not attrs.intersection({'openUrl', 'startfile', 'Popen', 'transact', 'verify_release', 'fetch_metadata', 'read_bytes'})
    assert 'pending_release' not in (root/'update_controller.py').read_text()
