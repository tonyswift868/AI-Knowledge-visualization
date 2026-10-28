"""BUG-140: remaining provider budget cannot grow from float round-off."""
from types import SimpleNamespace

import pytest

from refactor.learning.connections import gateway as gateway_module, providers
from refactor.learning.connections.errors import ModelError
from tests.refactor.gateway.test_gateway import MESSAGES, answer, configured


def test_budget219_background_roundoff_cannot_exceed_configured_budget(tmp_path, monkeypatch):
    gateway, _ = configured(tmp_path)
    calls = []
    # Reproduces (248.3 + 1800.) - 248.3 == 1800.0000000000002.
    monkeypatch.setattr(gateway_module, 'time', SimpleNamespace(monotonic=lambda: 248.3))
    monkeypatch.setattr(providers, 'complete', lambda provider, **kw: (calls.append(kw), answer())[1])

    gateway.complete('plan_intent', MESSAGES)

    assert calls[0]['timeout'] == gateway_module.BACKGROUND_TIMEOUT


def test_budget219_retry_uses_remaining_budget_not_a_fresh_budget(tmp_path, monkeypatch):
    gateway, _ = configured(tmp_path)
    now, calls = [248.3], []
    monkeypatch.setattr(gateway_module, 'time', SimpleNamespace(monotonic=lambda: now[0]))

    def wait(token, delay=None):
        now[0] += delay
        return token.is_set()

    def complete(provider, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) == 1:
            now[0] += 10.
            failure = ModelError('MODEL_CONNECTION_FAILED', 'offline fake')
            failure.safe_to_retry = True
            raise failure
        return answer()

    monkeypatch.setattr(gateway_module._Cancellation, 'wait', wait)
    monkeypatch.setattr(providers, 'complete', complete)
    gateway.complete('plan_intent', MESSAGES)

    assert len(calls) == 2
    assert calls[0] <= gateway_module.BACKGROUND_TIMEOUT
    assert calls[1] == pytest.approx(gateway_module.BACKGROUND_TIMEOUT - 11.)
