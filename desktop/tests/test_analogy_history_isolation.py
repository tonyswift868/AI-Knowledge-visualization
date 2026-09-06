"""Mandatory synthetic history isolation; real QSettings, UI consumers and HTTP assembly.

No external provider, live server, formal database, or final package is exercised.
"""
from concurrent.futures import Future, ThreadPoolExecutor
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import httpx
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from desktop.account_identity import AccountIdentity, AccountProfile
from desktop.analogy_history import analogy_settings, analogy_ticket
from desktop.api import DesktopApi
from desktop.main_window import MainWindow
from desktop.runtime import record_enterprise_account_session, clear_enterprise_account_session
from desktop.tests.test_login_smooth import app_context  # isolated lifecycle fixture

ISSUER = 'https://identity.example.test/realms/mentorflow'


def activate(subject, *, issuer=ISSUER, workspace=None):
    identity = AccountIdentity(issuer, subject, 'Same display name', 'org', workspace or 'ws-' + subject)
    record_enterprise_account_session(identity.profile(), {'authenticated': True,
        'context': {'organization_id': identity.organization_id, 'workspace_id': identity.workspace_id}})
    return identity.profile()


def response(marker):
    return {'card': {'title': marker, 'analogy': 'scene ' + marker, 'takeaway': 'lesson ' + marker},
            'memory': {'concept_key': marker, 'scene_family': 'test'}}


class HistoryWindow:
    """Bind production methods without booting unrelated SQL/UI services."""
    def __init__(self, ini, profile):
        self.settings = QSettings(str(ini), QSettings.Format.IniFormat)
        self.account_profile = profile
        self._account_switch_in_progress = False
        self._analogy_prefetch_generation = 0
        self._analogy_prefetch_futures = set()
        self._analogy_prefetch_future = None
        self._personalized_rank_future = None
        self._analogy_prefetch_context = ''
        self._analogy_prefetch_retry_count = 0
        self._analogy_click_pending = False
        self._analogy_button_feedback = ''
        self._analogy_button_feedback_token = 0
        self._analogy_library_cursor = 0
        self.provider_session_id = 'synthetic-session'
        self.offline_fact_title, self.offline_fact_body, self.offline_fact_source = QLabel(), QLabel(), QLabel()
        self.fact_source_button, self.next_fact_button = QPushButton(), QPushButton()
        self.analogy_loading_status = QLabel()
        self._initialize_analogy_policy()
        self._life_example = self._load_cached_life_example()
        self._analogy_recent_cards = self._load_recent_analogy_cards()
        self._analogy_card_library = self._load_analogy_card_library()
        self._analogy_prefetch_queue = self._load_prefetched_life_examples()
        self._analogy_prefetched = self._analogy_prefetch_queue[0] if self._analogy_prefetch_queue else {}


# These are the actual production read/write/render/context/scheduling methods,
# not functions that return predetermined per-account fixture values.
for _name, _method in inspect.getmembers(MainWindow, predicate=inspect.isfunction):
    if any(term in _name for term in ('analogy', 'life_example')) or _name in ('_render_fact', '_poll_personalized_rank'):
        setattr(HistoryWindow, _name, _method)


def write_history(window, marker):
    analogy_settings(window).setValue('analogy/keywords', marker)
    result = response(marker)
    window._remember_life_example(result['card'], result['memory'])
    item = {'schema': 'mentorflow.life-analogy.prefetch.v4', 'context': window._analogy_context_key(), 'result': result}
    window._remember_analogy_library_item(item)
    window._analogy_prefetch_queue = [item]
    window._persist_analogy_prefetch_queue()
    window._life_example = window._load_cached_life_example()
    window._render_fact()


def observe(window):
    window._life_example = window._load_cached_life_example()
    window._render_fact()
    return {'recent': window._load_recent_analogy_cards(), 'latest': window._load_cached_life_example(),
            'queue': window._load_prefetched_life_examples(), 'library': window._load_analogy_card_library(),
            'context_key': window._analogy_context_key(), 'ui': window.offline_fact_body.text()}


def assembled_request(window):
    requests = []
    def receive(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response('synthetic-result'))
    api = DesktopApi.__new__(DesktopApi)
    api.client = httpx.Client(base_url='https://loopback.test', transport=httpx.MockTransport(receive))
    window.api = api
    try:
        window._generate_life_analogy_resilient(window.provider_session_id,
            analogy_settings(window).value('analogy/keywords'), window._load_recent_analogy_cards(),
            window._next_analogy_seed(), api, analogy_ticket(window))
        return requests
    finally:
        api.close()


@pytest.fixture(autouse=True)
def isolated_session(monkeypatch):
    clear_enterprise_account_session()
    monkeypatch.delenv('MENTORFLOW_DESKTOP_OIDC_ISSUER', raising=False)
    monkeypatch.delenv('MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE', raising=False)
    yield
    clear_enterprise_account_session()


def test_real_settings_a_b_a_ui_queries_context_and_history_preserved(tmp_path):
    ini = tmp_path/'shared.ini'
    a = HistoryWindow(ini, activate('a'))
    write_history(a, 'A_ONLY_RECENT')
    a_scope = analogy_settings(a).owner
    a._seal_analogy_history()
    b = HistoryWindow(ini, activate('b'))
    assert 'A_ONLY_RECENT' not in json.dumps(observe(b))
    write_history(b, 'B_ONLY_RECENT')
    b_state = observe(b)
    assert 'B_ONLY_RECENT' in b_state['ui']
    assert 'A_ONLY_RECENT' not in json.dumps(b_state)
    b_request = assembled_request(b)
    assert 'B_ONLY_RECENT' in json.dumps(b_request) and 'A_ONLY_RECENT' not in json.dumps(b_request)
    b._seal_analogy_history()
    reopened_a = HistoryWindow(ini, activate('a'))
    assert analogy_settings(reopened_a).owner == a_scope
    a_state = observe(reopened_a)
    assert 'A_ONLY_RECENT' in a_state['ui'] and 'B_ONLY_RECENT' not in json.dumps(a_state)
    assert a_state['recent'] and a_state['queue'] and a_state['library']
    a_request = assembled_request(reopened_a)
    assert 'A_ONLY_RECENT' in json.dumps(a_request) and 'B_ONLY_RECENT' not in json.dumps(a_request)


def test_no_identity_expired_session_and_legacy_global_preservation(tmp_path, monkeypatch):
    ini = tmp_path/'shared.ini'
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    legacy = json.dumps([{'summary': 'LEGACY_UNOWNED'}])
    settings.setValue('analogy/recent_cards', legacy)
    settings.sync()
    for profile in (None, AccountIdentity(ISSUER, 'unverified', 'name').profile()):
        window = HistoryWindow(ini, profile)
        assert window._load_recent_analogy_cards() == []
        window._remember_life_example(response('FORBIDDEN')['card'])
        assert window._analogy_recent_cards == []
        assert window._analogy_context_key() == ''
    a = HistoryWindow(ini, activate('a'))
    write_history(a, 'A_ONLY_RECENT')
    clear_enterprise_account_session()
    assert a._load_recent_analogy_cards() == [] and a._load_cached_life_example() == {}
    a._remember_life_example(response('EXPIRED')['card'])
    assert 'EXPIRED' not in str(a.settings.value(f'{analogy_settings(a).owner}/analogy/recent_cards'))
    local = HistoryWindow(ini, AccountProfile.personal_local())
    assert local._load_recent_analogy_cards()[0]['summary'] == 'LEGACY_UNOWNED'
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER', ISSUER)
    assert local._load_recent_analogy_cards() == []
    assert settings.value('analogy/recent_cards') == legacy


def test_obsolete_personal_cache_is_preserved_not_revived_or_assigned(tmp_path):
    ini = tmp_path/'legacy.ini'
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    old = json.dumps({'schema': 'mentorflow.life-analogy.v7', **response('LEGACY_UNOWNED')['card']})
    settings.setValue('analogy/generation_policy', 'old-policy')
    settings.setValue('analogy/latest_card', old)
    settings.setValue('analogy/prefetched_card', 'old-buffer')
    settings.setValue('analogy/card_library', 'old-library')
    settings.sync()
    for _ in range(2):
        local = HistoryWindow(ini, AccountProfile.personal_local())
        assert local._load_cached_life_example() == {}
        assert settings.value('analogy/latest_card') == old
        assert settings.value('analogy/generation_policy') == 'old-policy'
    enterprise = HistoryWindow(ini, activate('b'))
    assert enterprise._load_cached_life_example() == {}
    assert settings.value('analogy/prefetched_card') == 'old-buffer'
    assert settings.value('analogy/card_library') == 'old-library'


@pytest.mark.parametrize('change', ['issuer', 'workspace'])
def test_same_subject_different_authorized_scope_does_not_share_history(tmp_path, change):
    ini = tmp_path/'shared.ini'
    a = HistoryWindow(ini, activate('same'))
    write_history(a, 'A_ONLY_RECENT')
    kwargs = {'issuer': 'https://other.example.test/realm'} if change == 'issuer' else {'workspace': 'other-ws'}
    b = HistoryWindow(ini, activate('same', **kwargs))
    assert 'A_ONLY_RECENT' not in json.dumps(observe(b))
    assert analogy_settings(b).owner != analogy_settings(a).owner


def test_delayed_running_a_request_and_callbacks_cannot_modify_b(tmp_path, monkeypatch):
    monkeypatch.setattr('desktop.main_window.QTimer.singleShot', lambda *args: None)
    ini = tmp_path/'shared.ini'
    a = HistoryWindow(ini, activate('a'))
    write_history(a, 'A_ONLY_RECENT')
    entered, release = threading.Event(), threading.Event()
    sent = []
    def receive(request):
        sent.append(json.loads(request.content))
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json=response('A_LATE_RESULT'))
    api_a = DesktopApi.__new__(DesktopApi)
    api_a.client = httpx.Client(base_url='https://loopback.test', transport=httpx.MockTransport(receive))
    a.api = api_a
    ticket, context = analogy_ticket(a), a._analogy_context_key()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(a._generate_life_analogy_resilient, a.provider_session_id,
            'A_ONLY_RECENT', a._load_recent_analogy_cards(), 1, api_a, ticket)
        try:
            assert entered.wait(5)
            a._analogy_prefetch_futures = {future}
            a._seal_analogy_history()
            b = HistoryWindow(ini, activate('b'))
            write_history(b, 'B_ONLY_RECENT')
            b_future = Future()
            b._analogy_prefetch_futures = {b_future}
            before = observe(b)
            storage_before = {key: b.settings.value(key) for key in b.settings.allKeys()}
            release.set()
            future.result(timeout=5)
            a._poll_analogy_prefetch(ticket[1], context, future)
            a._poll_personalized_rank()
            a._seal_analogy_history()
            assert not b_future.cancelled()
            assert observe(b) == before
            assert {key: b.settings.value(key) for key in b.settings.allKeys()} == storage_before
            assert 'B_ONLY_RECENT' not in json.dumps(sent)
            assert 'A_ONLY_RECENT' in json.dumps(sent)
            with pytest.raises(RuntimeError, match='no longer active'):
                a._generate_life_analogy_resilient('old', 'old', [], 2, api_a, ticket)
            assert len(sent) == 1
        finally:
            release.set()
            api_a.close()


def test_independent_python_processes_restore_actual_qsettings_a_b_a(tmp_path):
    ini = tmp_path/'process-shared.ini'
    def run(subject, action):
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
            MENTORFLOW_DATA_DIR=str(tmp_path/'isolated'), MENTORFLOW_USER_DATA_ROOT=str(tmp_path/'isolated'),
            MENTORFLOW_DB=str(tmp_path/'isolated/app.db'), MENTORFLOW_ORM_DB=str(tmp_path/'isolated/orm.db'))
        result = subprocess.run([sys.executable, '-m', 'desktop.tests.test_analogy_history_isolation', '--probe', str(ini), subject, action],
            cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert 'A_ONLY_RECENT' in json.dumps(run('a', 'write'))
    assert 'A_ONLY_RECENT' not in json.dumps(run('b', 'read'))
    assert 'B_ONLY_RECENT' in json.dumps(run('b', 'write'))
    a = run('a', 'read')
    assert 'A_ONLY_RECENT' in a['ui'] and 'B_ONLY_RECENT' not in json.dumps(a)
    b = run('b', 'read')
    assert 'B_ONLY_RECENT' in b['ui'] and 'A_ONLY_RECENT' not in json.dumps(b)


def test_portable_preference_consumer_uses_same_scope_not_legacy_globals(tmp_path):
    from desktop.system_page import _safe_qsettings_snapshot
    ini = tmp_path/'shared.ini'
    a = HistoryWindow(ini, activate('a'))
    a.settings.setValue('analogy/keywords', 'LEGACY_UNOWNED')
    a.settings.setValue('news/interests', 'LEGACY_UNOWNED')
    a.settings.setValue('tutorial/step', 2)
    write_history(a, 'A_ONLY_RECENT')
    b = HistoryWindow(ini, activate('b'))
    write_history(b, 'B_ONLY_RECENT')
    snapshot = _safe_qsettings_snapshot(history_settings=analogy_settings(b), settings=b.settings)
    assert snapshot['analogy/keywords'] == 'B_ONLY_RECENT'
    assert snapshot['tutorial/step'] == 2
    assert 'A_ONLY_RECENT' not in json.dumps(snapshot) and 'LEGACY_UNOWNED' not in json.dumps(snapshot)
    assert 'analogy/keywords' not in _safe_qsettings_snapshot(settings=b.settings)
    assert a.settings.value('analogy/keywords') == 'LEGACY_UNOWNED'


def test_scheduler_captures_owner_api_and_reads_scoped_history(tmp_path, monkeypatch):
    from backend.tests.test_life_analogy_prefetch_buffer import FakeExecutor
    monkeypatch.setattr('desktop.main_window.QTimer.singleShot', lambda *args: None)
    a = HistoryWindow(tmp_path/'shared.ini', activate('a'))
    write_history(a, 'A_ONLY_RECENT')
    a._analogy_recent_cards = [{'summary': 'B_ONLY_RECENT'}]  # stale cache must not enter request assembly
    sent_a, sent_b = [], []
    def api_for(sent):
        api = DesktopApi.__new__(DesktopApi)
        def receive(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=response('generated'))
        api.client = httpx.Client(base_url='https://loopback.test', transport=httpx.MockTransport(receive))
        return api
    api_a, api_b = api_for(sent_a), api_for(sent_b)
    try:
        a.api = api_a
        a._analogy_prefetch_queue = []
        a._analogy_executor = FakeExecutor()
        a._schedule_analogy_prefetch()
        assert len(a._analogy_executor.submissions) == 3
        a.api = api_b  # replacement cannot redirect an already captured request
        first, callback, args = a._analogy_executor.submissions[0]
        first.set_result(callback(*args))
        assert 'A_ONLY_RECENT' in json.dumps(sent_a) and 'B_ONLY_RECENT' not in json.dumps(sent_a)
        assert sent_b == []
        a._seal_analogy_history()
        activate('b')
        _, callback, args = a._analogy_executor.submissions[1]
        with pytest.raises(RuntimeError, match='no longer active'):
            callback(*args)
        assert len(sent_a) == 1 and sent_b == []
    finally:
        api_a.close()
        api_b.close()


def test_switch_fences_history_before_existing_api_drain(app_context):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget
    from PySide6.QtTest import QTest
    target, qt, _ = app_context
    target._show_login_gate()
    qt.processEvents()
    events, release = [], threading.Event()
    class OldMain(QWidget):
        def __init__(self):
            super().__init__()
            self.account_profile = activate('a')
            self._account_switch_in_progress = False
            self.account_vault_manager = SimpleNamespace(unmount=lambda: events.append('unmount'))
        def _seal_analogy_history(self):
            MainWindow._seal_analogy_history(self)
            events.append('history-fenced')
        def _close_current_account_handles(self): events.append('handles-closed')
        def _clear_account_ui_and_runtime_cache(self): events.append('ui-cleared')
    class Service:
        handles_closed = False
        def stop(self):
            assert old._analogy_history_closed
            assert release.wait(5)
            self.handles_closed = True
            events.append('api-drained')
    old = OldMain()
    target.main, target.service = old, Service()
    try:
        target._return_to_login_gate('switch')
        assert events[0] == 'history-fenced' and old._analogy_history_closed
        assert target.main is old  # replacement still waits for the original drain
        release.set()
        for _ in range(100):
            QTest.qWait(20)
            if target.main is None:
                break
        assert target.main is None
        assert events.index('history-fenced') < events.index('api-drained') < events.index('handles-closed') < events.index('unmount')
    finally:
        release.set()


if __name__ == '__main__':
    assert sys.argv[1] == '--probe'
    app = QApplication([])
    window = HistoryWindow(Path(sys.argv[2]), activate(sys.argv[3]))
    if sys.argv[4] == 'write':
        write_history(window, sys.argv[3].upper() + '_ONLY_RECENT')
    print(json.dumps(observe(window)))
