"""Regression for a real materials thread spawning a minutes worker on exit."""
import threading
from tests.refactor.integration38.support import join_workers

def test_worker115_joins_child_started_during_parent_join(monkeypatch):
    parent_release=threading.Event(); child_release=threading.Event(); child_joined=threading.Event()
    original_join=threading.Thread.join
    child=threading.Thread(name='plan-point-minutes-115',target=child_release.wait)
    parent=threading.Thread(name='plan-materials-115',target=lambda:(parent_release.wait(),child.start()))
    def join(worker,timeout=None):
        if worker is parent: parent_release.set()
        if worker is child: child_joined.set();child_release.set()
        return original_join(worker,timeout)
    monkeypatch.setattr(threading.Thread,'join',join)
    parent.start()
    try:
        join_workers()
        assert child_joined.is_set(), 'one enumeration missed the new minutes worker'
        assert not parent.is_alive() and not child.is_alive()
    finally:
        parent_release.set();child_release.set();original_join(parent,5)
        if child.ident is not None:original_join(child,5)
