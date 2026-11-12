"""Offline regression coverage for 368 output truncation and planner reuse."""

import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections import providers
from refactor.learning.connections.errors import (
    MODEL_CONTEXT_WINDOW_EXCEEDED,
    MODEL_OUTPUT_TRUNCATED,
    ModelError,
)
from refactor.learning.connections.gateway import MODEL_MAX_OUTPUT
from refactor.learning.course import planner_agents
from tests.refactor.gateway.test_gateway import MESSAGES, answer, configured


def _truncated(length, *, reason="max_tokens"):
    error = ModelError(MODEL_OUTPUT_TRUNCATED, "达到输出上限")
    error.response_received = True
    error.generated_text_length = length
    error.finish_reason = reason
    error.model = "deepseek-v4-flash"
    error.usage = providers.Usage(10, 0, 10, length, 0, 0, {})
    return error


def test_deepseek_at_model_cap_does_not_retry(tmp_path, monkeypatch):
    gateway, _ = configured(tmp_path)
    calls = []

    def fake_complete(provider, **kwargs):
        calls.append(kwargs)
        raise _truncated(MODEL_MAX_OUTPUT["deepseek-v4-flash"])

    monkeypatch.setattr(providers, "complete", fake_complete)
    with pytest.raises(ModelError) as caught:
        gateway.complete("teacher", MESSAGES, max_tokens=1024)
    assert caught.value.code == MODEL_OUTPUT_TRUNCATED
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == MODEL_MAX_OUTPUT["deepseek-v4-flash"]


def test_deepseek_below_cap_retries_once_with_doubled_budget(tmp_path, monkeypatch):
    class Gateway:
        def __init__(self):
            self.calls = []

        @staticmethod
        def effective_output_limit(_purpose, requested):
            return requested

        def complete(self, _purpose, _messages, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise _truncated(32)
            return answer(model="fixture-model")

    gateway = Gateway()
    host = SimpleNamespace(services=SimpleNamespace(gateway=gateway))
    ctx = SimpleNamespace(cancel=threading.Event())
    parsed, result = PlanFlow._ask(host, "teacher", (None, MESSAGES), lambda text: text, ctx,
                                    plan_id="trunc368", max_tokens=128)
    assert parsed == "你好" and result.text == "你好"
    assert [call["max_tokens"] for call in gateway.calls] == [128, 256]


def test_anthropic_context_window_error_is_distinct_and_not_retried(tmp_path, monkeypatch):
    gateway = configured(tmp_path, account="anthropic-account")[0]
    gateway.save_preset("anthropic", api_key="sk-anthropic-fake")
    calls = []

    def fake_complete(provider, **kwargs):
        calls.append(kwargs)
        raise ModelError(MODEL_CONTEXT_WINDOW_EXCEEDED, "这次给模型的内容太长")

    monkeypatch.setattr(providers, "complete", fake_complete)
    with pytest.raises(ModelError) as caught:
        gateway.complete("teacher", MESSAGES)
    assert caught.value.code == MODEL_CONTEXT_WINDOW_EXCEEDED
    assert "太长" in caught.value.message
    assert len(calls) == 1
    assert not PlanFlow._retryable_material_error(MODEL_CONTEXT_WINDOW_EXCEEDED)


def test_directory_purposes_reuses_previous_model_partition_without_calling_provider():
    paragraphs = {
        "p1": {"id": "p1", "text": "正文段落", "section": ["第一章"], "source_id": "src"},
    }
    previous = [{
        "id": "S1", "section": ["第一章"], "source_id": "src", "refs": ["p1"],
        "decision": "model", "knowledge": ["p1"], "navigation": [],
        "title": "第一章", "reason": "模型判断",
    }]
    calls = []

    def should_not_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("reused model partition should avoid a provider call")

    result = planner_agents.directory_purposes(paragraphs, should_not_call, previous=previous)
    assert calls == []
    assert result[0]["decision"] == "model"
    assert result[0]["refs"] == ["p1"]
