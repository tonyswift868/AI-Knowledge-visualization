from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.refactor.teacher226 import live_compare226
from tests.refactor.teacher228 import live_teacher228
from tests.refactor.teacher229 import live_teacher229


class _FakeGateway:
    def __init__(self):
        self.sent = 0
        self.price_catalog = SimpleNamespace(
            cost=lambda *args, **kwargs: SimpleNamespace(amount=Decimal("0.10")))

    def get_configuration(self):
        return {"provider_id": "deepseek", "model": "deepseek-v4-flash"}

    def estimate_context(self):
        return {}

    def complete(self, purpose, messages, *, system=None, **kwargs):
        self.sent += 1
        usage = SimpleNamespace(input_tokens=10, cached_input_tokens=0,
            uncached_input_tokens=10, output_tokens=3, reasoning_tokens=0,
            raw={"_mentorflow": {"unreported_fields": []}})
        return SimpleNamespace(text="回答。", usage=usage,
            model="deepseek-v4-flash", finish_reason="stop")


def test_teacher229_budget_releases_priced_reservations_not_spend(monkeypatch):
    gateway = _FakeGateway()
    monkeypatch.setattr(live_compare226, "_estimate", lambda *args: Decimal("0.20"))
    monkeypatch.setattr(live_teacher228, "BUDGET_CNY", Decimal("10"))
    capture = live_teacher229._BudgetGateway(gateway, lambda: None)
    # Historic worst-case reservations exceed ¥5, but successful calls have
    # known peak cost and no outstanding unpriced request.
    capture.reserved = Decimal("5.40")
    capture.calls.extend({"cost_cny": "0.10",
                          "reserved_retry_worst_case_cny": "0.90"}
                         for _ in range(6))

    result = capture.complete("teacher", [{"role": "user", "content": "Q"}])

    assert result.text == "回答。"
    assert gateway.sent == 1
    assert live_teacher229._committed_outlay(capture.calls) == Decimal("0.70")
    assert live_teacher228.BUDGET_CNY == Decimal("10")


def test_teacher229_budget_blocks_before_send_when_measured_spend_would_exceed_cap(monkeypatch):
    gateway = _FakeGateway()
    monkeypatch.setattr(live_compare226, "_estimate", lambda *args: Decimal("0.20"))
    monkeypatch.setattr(live_teacher228, "BUDGET_CNY", Decimal("10"))
    capture = live_teacher229._BudgetGateway(gateway, lambda: None)
    capture.calls.append({"cost_cny": "4.90",
                          "reserved_retry_worst_case_cny": "1.00"})

    with pytest.raises(live_teacher228._BudgetExceeded, match="¥5"):
        capture.complete("teacher", [{"role": "user", "content": "Q"}])

    assert gateway.sent == 0
    assert len(capture.calls) == 1
    assert live_teacher228.BUDGET_CNY == Decimal("10")


def test_teacher229_budget_keeps_unpriced_failure_reservation_cumulative():
    calls = [
        {"cost_cny": "0.15", "reserved_retry_worst_case_cny": "0.80"},
        {"cost_cny": None, "reserved_retry_worst_case_cny": "0.60"},
    ]

    assert live_teacher229._committed_outlay(calls) == Decimal("0.75")
    assert live_teacher229._within_budget(Decimal("0"),
        live_teacher229._committed_outlay(calls), Decimal("4.24"))
    assert not live_teacher229._within_budget(Decimal("0"),
        live_teacher229._committed_outlay(calls), Decimal("4.26"))


def test_teacher229_budget_recheck_keeps_prior_paid_run_spend(monkeypatch):
    gateway = _FakeGateway()
    monkeypatch.setattr(live_compare226, "_estimate", lambda *args: Decimal("0.20"))
    monkeypatch.setattr(live_teacher228, "BUDGET_CNY", Decimal("10"))
    capture = live_teacher229._BudgetGateway(gateway, lambda: None,
        committed_before=Decimal("0.06521960"))
    capture.complete("teacher", [{"role": "user", "content": "CPU cache"}])
    assert gateway.sent == 1
    assert live_teacher229._committed_outlay(capture.calls) == Decimal("0.10")
    assert capture.committed_before + live_teacher229._committed_outlay(capture.calls) == Decimal("0.16521960")
