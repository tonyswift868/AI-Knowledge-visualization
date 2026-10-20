"""Hints never authorize updates; batch 181 keeps discovery off the login path."""
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from refactor.desktop.updates import DesktopUpdates
from refactor.desktop.account_shell_main import AccountShell


class PointerCore168:
    def __init__(self, policy, final='OPTIONAL_UPDATE', cached='HEALTHY', feed='VERIFIED'):
        self.policy, self.final, self.cached, self.feed = policy, final, cached, feed
        self.entered, self.release, self.exited = (threading.Event() for _ in range(3))

    def attach_shutdown_guard(self, _app):
        pass

    def get_startup_update_state(self, _root, *, full, check_feed, cancel,
                                 on_pointer_policy=None):
        if check_feed:
            if self.policy is not None:
                on_pointer_policy(self.policy)
            self.entered.set()
            while not self.release.wait(.005):
                if cancel():
                    break
            self.exited.set()
        state = self.final if check_feed else self.cached
        return dict(state=state, current='2.0.26', latest='2.0.27',
                    login_allowed=state in ('HEALTHY', 'OPTIONAL_UPDATE'),
                    mandatory_floor='2.0.27' if state == 'MANDATORY_UPDATE' else '2.0.3',
                    feed=self.feed if check_feed else 'NOT_CHECKED', message='更新检查结果')


def pump168(app, predicate):
    until = time.monotonic() + 3
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert predicate()


@pytest.fixture
def harness168(tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_install
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda *_a, **_k: None)
    monkeypatch.setattr(per_user_install, 'schedule_program_cleanup', lambda *_a, **_k: None)
    monkeypatch.setattr(DesktopUpdates, '_prepare_in_background', lambda _s: None)
    prompts = []
    monkeypatch.setattr(DesktopUpdates, '_show_prompt', lambda obj: prompts.append(obj.snapshot()))
    made = []
    def make(policy=None, **kwargs):
        gate = QWidget()
        core = PointerCore168(policy, **kwargs)
        ui = DesktopUpdates(app, tmp_path, gate, core=core)
        made.append((gate, core, ui))
        ui.start()
        pump168(app, lambda: core.entered.is_set() and ui._startup_cached is not None)
        return app, gate, core, ui, prompts
    yield make
    for gate, core, ui in made:
        core.release.set()
        ui.close()
        assert core.exited.wait(3)
        app.processEvents()
        gate.close()


def policy168(*, mandatory=False, floor='2.0.3'):
    return dict(version='2.0.27', metadata_version=27, sha256='0'*64, size=2000000,
                mandatory=mandatory, mandatory_floor=floor)


def test_optional_pointer168_releases_login_before_manifest_without_offer(harness168):
    app, gate, core, ui, prompts = harness168(policy168())
    assert ui.state.get('login_check_status') == 'background'
    assert ui.state['login_allowed'] and not ui.state['login_check_pending']
    assert ui.snapshot()['busy'] and not core.exited.is_set()
    assert AccountShell._updates_allow_login(SimpleNamespace(app=SimpleNamespace(_mentorflow_updates=ui)))
    assert gate.isEnabled() and prompts == []
    core.release.set()
    pump168(app, lambda: ui.state.get('feed') == 'VERIFIED')
    assert ui.state['state'] == 'OPTIONAL_UPDATE' and len(prompts) == 1


@pytest.mark.parametrize('policy', [None, policy168(mandatory=True, floor='2.0.27'),
                                  policy168(floor='2.0.27')])
def test_missing_or_mandatory_pointer168_cannot_block_before_signed_manifest(harness168, policy):
    app, gate, core, ui, prompts = harness168(policy, final='MANDATORY_UPDATE')
    assert not ui.state['login_check_pending'] and ui.state.get('login_allowed', False)
    assert prompts == []
    assert gate.isEnabled()  # unsigned hints do not constrain the logged-in account
    core.release.set()
    pump168(app, lambda: ui.state.get('feed') == 'VERIFIED')
    assert ui.state['mandatory'] and not ui.state['login_allowed']
    assert len(prompts) == 1 and prompts[0]['state'] == 'MANDATORY_UPDATE'


def test_optional_pointer168_cannot_override_cached_mandatory(harness168):
    app, _gate, core, ui, prompts = harness168(policy168(), cached='MANDATORY_UPDATE',
                                             final='MANDATORY_UPDATE')
    assert ui.state['mandatory'] and not ui.state['login_allowed']
    assert not ui.state['login_check_pending']
    core.release.set()
    pump168(app, lambda: ui.state.get('feed') == 'VERIFIED')
    assert ui.state['mandatory'] and not ui.state['login_allowed']


def test_optional_pointer168_signed_contradiction_blocks_after_login(harness168):
    app, _gate, core, ui, prompts = harness168(policy168(), final='MANDATORY_UPDATE')
    assert ui.state.get('login_check_status') == 'background'
    assert AccountShell._updates_allow_login(SimpleNamespace(app=SimpleNamespace(_mentorflow_updates=ui)))
    observed = []
    ui.changed.connect(lambda status: observed.append(status))
    core.release.set()
    pump168(app, lambda: ui.state.get('mandatory') is True)
    assert not ui.state['login_allowed'] and ui.state['feed'] == 'VERIFIED'
    assert observed[-1]['state'] == 'MANDATORY_UPDATE'  # existing all-page web modal consumes this
    assert prompts[-1]['mandatory']


def test_mandatory_pointer168_failed_verification_uses_signed_cached_policy(harness168):
    app, _gate, core, ui, _prompts = harness168(policy168(mandatory=True, floor='2.0.27'),
                                              final='HEALTHY', feed='UNAVAILABLE_RECOVERABLE')
    core.release.set()
    pump168(app, lambda: ui.state.get('feed') == 'UNAVAILABLE_RECOVERABLE')
    assert ui.state['login_allowed']
    assert ui.state['state'] == 'HEALTHY' and not ui.state['mandatory']


def test_missing_pointer168_does_not_hold_login_or_cancel_signed_check(harness168):
    app, _gate, core, ui, _prompts = harness168(None, final='MANDATORY_UPDATE')
    assert not ui.state['login_check_pending'] and ui.state.get('login_allowed', False)
    assert ui.snapshot()['busy'] and not core.exited.is_set()
    core.release.set()
    pump168(app, lambda: ui.state.get('mandatory') is True)
    assert not ui.state['login_allowed']


def test_unavailable_feed168_retains_verified_cache_after_worker_result(harness168):
    app, _gate, core, ui, _prompts = harness168(None, final='HEALTHY',
                                              feed='UNAVAILABLE_RECOVERABLE')
    assert not ui.state['login_check_pending'] and ui.state.get('login_allowed', False)
    core.release.set()
    pump168(app, lambda: ui.state.get('login_check_status') == 'offline_fallback')
    assert ui.state['login_allowed'] and ui.state['login_check_status'] == 'offline_fallback'
