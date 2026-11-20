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


def test_direct_shutdown_does_not_wait_for_logging_lock(monkeypatch):
    import logging
    import threading
    from refactor.desktop import account_shell_main

    events = []
    monkeypatch.setattr(account_shell_main, 'diagnostic_event',
                        lambda *args, **kwargs: events.append(('event', args, kwargs)))
    monkeypatch.setattr(account_shell_main.os, '_exit', lambda code: events.append(('os_exit', code)))
    held = threading.Event()
    release = threading.Event()
    def hold_logging_lock():
        logging._lock.acquire()
        held.set()
        release.wait(2)
        logging._lock.release()
    thread = threading.Thread(target=hold_logging_lock)
    thread.start()
    assert held.wait(1)

    account_shell_main._exit_after_shutdown(0, reason='USER_EXIT_DIRECT')
    release.set()
    thread.join(1)

    assert events == [('os_exit', 0)]
