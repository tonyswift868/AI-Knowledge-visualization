"""A hidden handoff window must not strand a legacy App during update."""

from refactor.desktop.delivery import per_user_install as install


def test_wait_for_app_exit_accepts_hidden_handoff_window(monkeypatch, tmp_path):
    from refactor.desktop.delivery import windows_process_peer

    events = []

    class Peer:
        def __init__(self, pid):
            self.pid = pid
            self.states = iter((True, True, False))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def require(self, **kwargs):
            events.append(('require', self.pid, kwargs))

        def visible_responsive_window(self, hwnd):
            raise ValueError('handoff window is hidden')

        def visible_windows(self):
            return []

        def alive(self):
            return next(self.states, False)

        def terminate(self):
            events.append(('terminate', self.pid))

    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', Peer)
    monkeypatch.setattr(install, 'app_exit_confirmed', lambda root, pid: True)
    monkeypatch.setattr(install, 'maintenance_log',
                        lambda root, phase, **fields: events.append((phase, fields)))

    install.wait_for_app_exit(
        tmp_path / 'MentorFlow', 321, '0000000000000001', timeout=0,
        ready=lambda: events.append('ack'),
    )

    assert [event[0] if isinstance(event, tuple) else event for event in events] == [
        'require', 'ack', 'terminate', 'stale_app_terminated',
    ]
