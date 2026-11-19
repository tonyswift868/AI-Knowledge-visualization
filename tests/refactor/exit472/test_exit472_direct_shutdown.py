"""BUG-279: every frozen App exit uses the direct process shutdown path."""


def test_direct_shutdown_records_reason_then_bypasses_python_cleanup(monkeypatch):
    from refactor.desktop import account_shell_main

    events = []
    monkeypatch.setattr(account_shell_main, 'diagnostic_event',
                        lambda name, **fields: events.append((name, fields)))
    monkeypatch.setattr(account_shell_main.os, '_exit',
                        lambda code: events.append(('os_exit', code)))

    account_shell_main._exit_after_shutdown(0, reason='USER_EXIT_DIRECT')

    assert events == [
        ('exit', {'reason': 'USER_EXIT_DIRECT'}),
        ('os_exit', 0),
    ]
