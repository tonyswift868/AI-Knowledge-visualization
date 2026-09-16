"""Only synthetic memory projections. No sync server, Vault or pack IO."""
from copy import deepcopy
from pathlib import Path
import ast

import pytest
from PySide6.QtWidgets import QLabel

from desktop.rehydration_state import RehydrationState, SyncState
from desktop.rehydration_page import RehydrationPage, PortablePackEntry


@pytest.fixture
def projection():
    return dict(schema='mentorflow.rehydration-ui.v1', account_key='account-a', revision='revision-private',
        plans=[dict(key='plan-a', name='我的英语计划', progress='已完成 3 / 8')],
        resources=[dict(key='pdf-stable-private', plan_key='plan-a', name='课程讲义', kind='file',
                        missing=True, verified_redownload=True, package_supported=True),
                   dict(key='model-stable-private', plan_key='plan-a', name='学习模型', kind='model',
                        missing=True, verified_redownload=False, package_supported=True)],
        progress_count=3, review_count=2, history_count=6, empty_account=False, server_confirmed=True)


@pytest.fixture
def page(desktop_qt_session):
    result = RehydrationPage()
    result.resize(760, 850)
    result.show()
    yield result
    result.close()
    result.deleteLater()
    desktop_qt_session.processEvents()


def begin(page):
    page.authenticated('account-a')
    return page.start_sync()


def test_login_waits_for_evidence_and_sync_success(page, projection):
    opened = []
    page.workspace_requested.connect(opened.append)
    assert not page.enter.isEnabled()
    page.authenticated('account-a')
    assert page.model.state == SyncState.AUTH_SUCCESS
    assert page.title.text() == '正在恢复你的学习空间…'
    assert page.progress.isVisible() and not page.enter.isEnabled()
    generation = page.start_sync()
    assert page.model.state == SyncState.SYNCING
    assert not page.enter.isEnabled()
    assert not opened
    assert page.receive(generation, 'SYNC_COMPLETE', projection)
    assert page.title.text() == '你的学习空间已恢复。'
    assert not page.progress.isVisible() and page.enter.isEnabled()
    assert '计划 1 · 进度 3 · 复习 2 · 历史 6' == page.summary.text()
    page.enter.click()
    assert len(opened) == 1


def test_true_empty_only_after_confirmed_complete(page, projection):
    projection.update(plans=[], resources=[], progress_count=0, review_count=0, history_count=0, empty_account=True)
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    assert page.title.text() == '这个账号还没有学习计划。'
    assert not page.resource_buttons and page.enter.isEnabled()


@pytest.mark.parametrize('state,copy', [(SyncState.SYNC_OFFLINE, '暂时无法连接到云端学习数据。'),
                                     (SyncState.SYNC_FAILED, '学习空间暂时未能恢复，请重试。')])
def test_failure_is_not_empty_or_lost_and_retry(page, state, copy):
    first = begin(page)
    page.receive(first, state)
    assert page.title.text() == copy
    assert page.summary.text() == '' and not page.enter.isEnabled()
    assert '数据丢失' not in page.title.text() and '没有学习计划' not in page.title.text()
    requests = []
    page.sync_requested.connect(requests.append)
    page.retry.click()
    assert page.model.state == SyncState.SYNCING
    assert requests[0]['generation'] > first


@pytest.mark.parametrize('state', [SyncState.SYNC_OFFLINE, SyncState.SYNC_FAILED])
@pytest.mark.parametrize('trusted,stamp', [(True, '2026-09-10 12:00'), (False, '2026-09-10'), (True, '')])
def test_only_scoped_trusted_timestamped_cache_can_open(page, projection, state, trusted, stamp):
    projection.update(server_confirmed=False, trusted_cache=trusted, last_sync=stamp)
    page.receive(begin(page), state, projection)
    allowed = trusted and bool(stamp)
    assert page.enter.isEnabled() is allowed
    if allowed:
        assert stamp in page.cache_notice.text() and '可信本地缓存' in page.cache_notice.text()
        assert page.plan_labels['plan-a'].text() == '我的英语计划'
    else:
        assert not page.plan_labels


def test_missing_pdf_and_model_preserve_plan_progress_and_bindings(page, projection):
    original = deepcopy(projection)
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    labels = '\n'.join(w.text() for w in page.findChildren(QLabel))
    assert '这份资料还没有下载到这台设备。' in labels
    assert '模型尚未安装在这台设备。' in labels
    assert page.plan_labels['plan-a'].isVisible()
    assert page.progress_labels['plan-a'].text() == '学习进度：已完成 3 / 8'
    assert page.model.state == SyncState.SYNC_COMPLETE
    for buttons in page.resource_buttons.values():
        buttons['later'].click()
    assert page.plan_labels['plan-a'].isVisible() and page.progress_labels['plan-a'].isVisible()
    assert len(page.model.snapshot.resources) == 2
    assert all(r.plan_key == 'plan-a' for r in page.model.snapshot.resources)
    assert projection == original


@pytest.mark.parametrize('kind', ['file', 'model'])
@pytest.mark.parametrize('verified,pack', [(True, True), (True, False), (False, True), (False, False)])
def test_recovery_choices_and_no_execution(page, projection, kind, verified, pack):
    resource = projection['resources'][0]
    resource.update(kind=kind, verified_redownload=verified, package_supported=pack)
    projection['resources'] = [resource]
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    buttons = page.resource_buttons[resource['key']]
    assert ('redownload' in buttons) is verified
    assert ('package' in buttons) is (kind == 'file' or pack)
    if 'package' in buttons:
        assert not buttons['package'].isEnabled()
    intents = []
    page.resource_action_requested.connect(intents.append)
    if verified:
        buttons['redownload'].click()
        assert not buttons['redownload'].isEnabled()
        assert intents == []
    else:
        assert '旧设备迁移包恢复' in page.resource_status[resource['key']].text()
    assert page.model.snapshot.resources[0].missing is True


def test_bound_original_package_button_uses_server_id_hash_and_account_fence(page, projection, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    calls = []
    class Lifecycle:
        account_key = 'account-a'
        def restore_resource_from_package(self, resource, package, *, fingerprint_sha256, cancel=None):
            calls.append((resource, package, fingerprint_sha256))
    resource = projection['resources'][0]
    resource.update(fingerprint_sha256='a' * 64, verified_redownload=False)
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    page.bind_resource_recovery(Lifecycle())
    assert page.resource_buttons[resource['key']]['package'].isEnabled()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: ('fixture.mentorflowpack', ''))
    page.resource_buttons[resource['key']]['package'].click()
    page._download_job[0].result(timeout=5)
    page._poll_model_download()
    assert calls == [(resource['key'], 'fixture.mentorflowpack', 'a' * 64)]
    assert not next(r for r in page.model.snapshot.resources if r.key == resource['key']).missing
    assert page.model.snapshot.plans[0].key == 'plan-a'
    page.authenticated('account-b')
    assert page._resource_lifecycle is None
    with pytest.raises(ValueError, match='RECOVERY_ACCOUNT_MISMATCH'):
        page.bind_resource_recovery(Lifecycle())


def test_account_switch_while_package_picker_open_discards_selection(page, projection, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    calls = []
    class Lifecycle:
        account_key = 'account-a'
        def restore_resource_from_package(self, *args, **kwargs):
            calls.append(True)
    resource = projection['resources'][0]
    resource['fingerprint_sha256'] = 'a' * 64
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    page.bind_resource_recovery(Lifecycle())
    def select_after_logout(*args, **kwargs):
        page.authenticated('account-b')
        return 'fixture.mentorflowpack', ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', select_after_logout)
    page._resource_action(page.model.snapshot.resources[0], 'package')
    assert calls == []


def test_details_default_hidden_and_plain_text(page, projection):
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    assert not page.details.isVisible() and not page.details_toggle.isChecked()
    visible = '\n'.join(w.text() for w in page.findChildren(QLabel) if w.isVisible())
    for technical in ('revision-private', 'pdf-stable-private', 'MISSING_ON_THIS_DEVICE'):
        assert technical not in visible and technical in page.details.toPlainText()
    page.details_toggle.click()
    assert page.details.isVisible() and page.details.isReadOnly()
    page.authenticated('account-b')
    assert not page.details.isVisible() and page.details.toPlainText() == ''


@pytest.mark.parametrize('capability,ready', [(False, False), (True, False), (False, True), (True, True), ('true', 1)])
def test_v2_gate_never_routes_to_v1(page, capability, ready):
    entry = page.portable_pack
    assert entry.button.text() == '导入 MentorFlow 迁移包'
    assert entry.button.isVisible()
    entry.set_readiness(capability=capability, ready=ready)
    assert not entry.button.isEnabled()


def test_settings_exposes_v2_entry_without_vault_io(monkeypatch, desktop_qt_session):
    from desktop.account_settings_page import AccountSettingsPage
    monkeypatch.setattr(AccountSettingsPage, 'refresh', lambda self: None)
    view = AccountSettingsPage(profile=None, vault_manager=None, credential_vault=None,
        switch_account=lambda _: None, logout_account=lambda: None)
    view.show()
    try:
        assert isinstance(view.portable_pack_entry, PortablePackEntry)
        assert view.portable_pack_entry.button.isVisible()
        assert not view.portable_pack_entry.button.isEnabled()
    finally:
        view.close()
        view.deleteLater()


def test_stale_account_attempt_and_duplicate_cannot_replace_state(page, projection):
    old = begin(page)
    new = page.start_sync()
    assert not page.receive(old, SyncState.SYNC_COMPLETE, projection)
    assert page.receive(new, SyncState.SYNC_COMPLETE, projection)
    assert not page.receive(new, SyncState.SYNC_FAILED)
    page.authenticated('account-b')
    attempt = page.start_sync()
    assert not page.receive(attempt, SyncState.SYNC_COMPLETE, projection)
    assert not page.enter.isEnabled() and not page.plan_labels


@pytest.mark.parametrize('field,value', [('server_confirmed', False), ('server_confirmed', 1),
    ('plans', None), ('progress_count', -1), ('empty_account', True), ('resources', [{'kind': 'unknown'}])])
def test_invalid_success_is_failure_not_empty(page, projection, field, value):
    projection[field] = value
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    assert page.model.state == SyncState.SYNC_FAILED
    assert not page.enter.isEnabled() and not page.plan_labels


def test_presentation_modules_have_no_io_or_v1_imports():
    root = Path(__file__).parents[1]
    allowed = {'dataclasses', 'enum', 'typing', 'PySide6.QtCore', 'PySide6.QtWidgets', 'rehydration_state', 'style', 'concurrent.futures', 'threading'}
    for name in ('rehydration_state.py', 'rehydration_page.py'):
        tree = ast.parse((root / name).read_text(encoding='utf-8'))
        assert {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} <= allowed
        assert not any(isinstance(n, ast.Import) for n in ast.walk(tree))
        assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and
            n.func.id in ('open', 'exec', 'eval', '__import__') for n in ast.walk(tree))


def test_production_bootstrap_retains_waiting_gate_copy():
    tree = ast.parse((Path(__file__).parents[1] / 'main.py').read_text(encoding='utf-8'))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_start_account_bootstrap')
    assert any(isinstance(n, ast.Constant) and n.value == '正在恢复你的学习空间…' for n in ast.walk(method))


def test_bound_public_model_runs_background_and_publishes_current_generation(page,projection):
    from PySide6.QtTest import QTest
    calls=[]
    class Lifecycle:
        account_key=projection['account_key']
        def install_public_model(self,resource_id,**kwargs):
            calls.append((resource_id,kwargs['fingerprint_sha256']))
    resource=projection['resources'][0]
    resource.update(kind='model',verified_redownload=True,fingerprint_sha256='a'*64)
    projection['resources']=[resource]
    page.receive(begin(page),SyncState.SYNC_COMPLETE,projection)
    page.bind_resource_recovery(Lifecycle())
    page.resource_buttons[resource['key']]['redownload'].click()
    for _ in range(20):
        QTest.qWait(30)
        if page._download_job is None:break
    assert calls==[(resource['key'],'a'*64)]
    assert not page.model.snapshot.resources[0].missing


def test_background_original_recovery_is_cancelled_and_fenced_on_switch(page, projection, monkeypatch):
    from threading import Event, get_ident
    from PySide6.QtWidgets import QFileDialog
    started, release = Event(), Event()
    ui_thread = get_ident()
    class Lifecycle:
        account_key = 'account-a'
        def restore_resource_from_package(self, *args, cancel, **kwargs):
            assert get_ident() != ui_thread
            started.set()
            release.wait(5)
            assert cancel.is_set()
    projection['resources'][0]['fingerprint_sha256']='a'*64
    page.receive(begin(page), SyncState.SYNC_COMPLETE, projection)
    page.bind_resource_recovery(Lifecycle())
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:('fixture.mentorflowpack',''))
    page.resource_buttons['pdf-stable-private']['package'].click()
    future = page._download_job[0]
    assert started.wait(3)
    page.authenticated('account-b')
    release.set()
    future.result(timeout=5)
    page._poll_model_download()
    assert page._resource_lifecycle is None
    assert page.model.snapshot is None
    assert page.model.account_key == 'account-b'
