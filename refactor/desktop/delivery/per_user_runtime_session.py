"""Wire the desktop deny-only session event into the stable consumer runtime.

This module owns no update transaction. A coordinator retains the GUI's event
and its own hidden broadcast listener after the GUI has exited. The context is
thread-local so embedding callers cannot accidentally share another operation's
guard. No event content can authorize a release or choose an executable.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import os

from refactor.desktop.delivery.update_session import UpdateSession
from refactor.desktop.delivery.update_session import SessionEnding

_current = ContextVar('mentorflow_consumer_runtime_session', default=None)


def session_ending():
    guard = _current.get()
    if guard is None:
        return False
    try:
        guard.require_running()
    except SessionEnding:
        return True
    return False


@contextmanager
def runtime_session(pid=None):
    with ExitStack() as stack:
        if pid is None:
            owner = UpdateSession()
            stack.callback(owner.close)
            pid = os.getpid()
        guard = UpdateSession(pid)
        stack.callback(guard.close)
        token = _current.set(guard)
        stack.callback(_current.reset, token)
        # Check again at each existing transaction gate, not only on entry.
        yield guard
