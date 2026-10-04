"""Recorder permits existing module splitting, never retries or forgives transport."""
import pytest
from refactor.learning.connections.errors import ModelError, MODEL_OUTPUT_TRUNCATED
from .engine import RecordedGateway, Evidence
from .actors import OfflineGateway


def test_simulation52_module_truncation_left_to_host(tmp_path):
    real=OfflineGateway();calls=[]
    def fail(*a,**kw):
        calls.append(a)
        raise ModelError(MODEL_OUTPUT_TRUNCATED,'故意截断')
    real.complete=fail
    recorded=RecordedGateway(real,Evidence(tmp_path),offline=True)
    for _ in range(2):
        with pytest.raises(ModelError):recorded.complete('generate',[])
    assert len(calls)==2 and not recorded.halted.is_set()
    assert all(c['host_batch_split_allowed'] for c in recorded.evidence.calls)
    with pytest.raises(ModelError):recorded.complete('simulation_user',[])
    assert recorded.halted.is_set()
