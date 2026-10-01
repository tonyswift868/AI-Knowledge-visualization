"""Offline reproduction of the saved A/B runner's pre-response TypeError."""
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

import pytest

from tests.refactor.cache_ab.scenario_threads import synchronous_classroom


def test_old_global_immediate_thread_patch_breaks_executor_before_any_network():
    class OldImmediateThread:
        def __init__(self, *, target, **options):
            self.target = target

        def start(self):
            self.target()

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with patch.object(threading, 'Thread', OldImmediateThread):
            with pytest.raises(TypeError, match='required positional arguments'):
                executor.submit(lambda: 'offline')
    finally:
        executor.shutdown(wait=False)


def test_classroom_only_synchronization_preserves_real_executor_and_host_result():
    from refactor.learning.app import classroom
    native = threading.Thread
    recorded = []
    with synchronous_classroom():
        assert threading.Thread is native
        classroom.threading.Thread(target=recorded.append, args=('host',), daemon=True).start()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(lambda: 'offline').result(timeout=3) == 'offline'
    assert recorded == ['host']
    assert classroom.threading is threading
