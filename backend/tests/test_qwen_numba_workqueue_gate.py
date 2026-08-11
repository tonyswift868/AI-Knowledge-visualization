from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from runtime import qwen_base_clone_server as runner


def test_numba_gate_defaults_to_non_tbb_workqueue(monkeypatch):
    monkeypatch.delenv(runner.NUMBA_THREADING_LAYER_ENV, raising=False)
    monkeypatch.delitem(sys.modules, runner.NUMBA_TBBPOOL_MODULE, raising=False)
    monkeypatch.delitem(sys.modules, "numba", raising=False)
    state = runner._enforce_numba_workqueue()
    assert state == {
        "configured": "workqueue",
        "active_config": None,
        "tbbpool_loaded": False,
        "tbb": "optional-not-shipped",
    }
    assert runner.os.environ[runner.NUMBA_THREADING_LAYER_ENV] == "workqueue"


def test_numba_gate_rejects_conflicting_host_threading_layer(monkeypatch):
    monkeypatch.setenv(runner.NUMBA_THREADING_LAYER_ENV, "tbb")
    with pytest.raises(RuntimeError, match="must be workqueue"):
        runner._enforce_numba_workqueue()


def test_numba_gate_rejects_preloaded_tbbpool(monkeypatch):
    monkeypatch.setenv(runner.NUMBA_THREADING_LAYER_ENV, "workqueue")
    monkeypatch.setitem(sys.modules, runner.NUMBA_TBBPOOL_MODULE, object())
    with pytest.raises(RuntimeError, match="TBB pool was loaded"):
        runner._enforce_numba_workqueue()


def test_numba_gate_rejects_loaded_numba_config_drift(monkeypatch):
    monkeypatch.setenv(runner.NUMBA_THREADING_LAYER_ENV, "workqueue")
    monkeypatch.delitem(sys.modules, runner.NUMBA_TBBPOOL_MODULE, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "numba",
        SimpleNamespace(config=SimpleNamespace(THREADING_LAYER="omp")),
    )
    with pytest.raises(RuntimeError, match="loaded Numba threading layer"):
        runner._enforce_numba_workqueue()

