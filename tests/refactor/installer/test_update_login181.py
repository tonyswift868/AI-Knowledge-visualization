"""User 2026-10-20: account entry never waits for network update discovery."""
import pytest

from test_startup_pointer_gate168 import harness168, policy168, pump168


@pytest.mark.parametrize('hint', [None, policy168(), policy168(mandatory=True, floor='2.0.27')])
def test_update181_pending_or_unavailable_network_does_not_hold_account(harness168, hint):
    app, gate, core, ui, prompts = harness168(hint, final='HEALTHY', feed='UNAVAILABLE_RECOVERABLE')
    # The network worker is deliberately still pending. No time advance, timeout
    # or synthetic response is needed for login permission.
    assert core.entered.is_set() and not core.exited.is_set()
    assert ui.snapshot()['login_allowed'] and not ui.snapshot()['login_check_pending']
    assert ui.snapshot()['busy'] and not ui._cancel.is_set()
    assert gate.isEnabled() and not prompts
    core.release.set()
    pump168(app, lambda: ui.state.get('feed') == 'UNAVAILABLE_RECOVERABLE')
    assert ui.snapshot()['login_allowed'] and not ui.snapshot()['mandatory']


@pytest.mark.parametrize('cached', ['MANDATORY_UPDATE', 'REPAIR_REQUIRED'])
def test_update181_already_verified_local_restriction_applies_without_network(harness168, cached):
    _app, gate, core, ui, prompts = harness168(None, cached=cached, final=cached)
    assert not core.exited.is_set()
    assert not ui.snapshot()['login_allowed'] and not ui.snapshot()['login_check_pending']
    assert not gate.isEnabled() and prompts[-1]['state'] == cached


def test_update181_late_signed_mandatory_still_locks_after_entry(harness168):
    app, _gate, core, ui, prompts = harness168(None, final='MANDATORY_UPDATE')
    assert ui.state['login_allowed'] and not prompts
    core.release.set()
    pump168(app, lambda: ui.state.get('feed') == 'VERIFIED')
    assert ui.state['mandatory'] and not ui.state['login_allowed']
    assert prompts[-1]['state'] == 'MANDATORY_UPDATE'
