"""Synchronize classroom fixtures without replacing Python's shared Thread."""
from contextlib import contextmanager
import threading
from types import SimpleNamespace
from unittest.mock import patch


class ImmediateThread:
    def __init__(self, *, target, args=(), kwargs=None, **options):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


@contextmanager
def synchronous_classroom():
    from refactor.learning.app import classroom
    # Patch the host's reference, not threading.Thread itself: httpx/asyncio's
    # DNS executor must retain genuine threads and its normal worker arguments.
    local = SimpleNamespace(**{name: getattr(threading, name) for name in dir(threading)
                               if not name.startswith('__')})
    local.Thread = ImmediateThread
    with patch.object(classroom, 'threading', local):
        yield
