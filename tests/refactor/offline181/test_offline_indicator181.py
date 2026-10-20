from types import SimpleNamespace

from refactor.learning.app.window import LearningWindow


def test_offline181_account_indicator_survives_sync_notice_and_clears_on_recovery():
    events = []
    window = SimpleNamespace(_disposed=False, _session_offline=False,
        _display_name='local test', _account_id='account-181', _role='LEARNER',
        _api_label='loopback', _settings_bound=True,
        bridge=SimpleNamespace(push=lambda *args: events.append(args)))
    window.account_info = lambda: LearningWindow.account_info(window)
    LearningWindow.set_session_offline(window, True)
    assert events[-1][0] == 'account' and events[-1][1]['offline']
    LearningWindow.set_session_notice(window, '同步失败，本机记录已保留。', show_verify=False)
    assert window.account_info()['offline']
    assert events[-1][0] == 'session_notice'
    LearningWindow.set_session_offline(window, False)
    assert events[-1][0] == 'account' and not events[-1][1]['offline']
    window._disposed = True
    previous = len(events)
    LearningWindow.set_session_offline(window, True)
    assert len(events) == previous
