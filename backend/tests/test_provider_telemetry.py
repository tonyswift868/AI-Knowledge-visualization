from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.provider_service import GeminiProvider, OpenAICompatibleProvider, ProviderError
from app.providers.telemetry import (
    normalize_provider_usage,
    provider_request_context,
    provider_telemetry_store,
)


@pytest.fixture(autouse=True)
def _isolated_telemetry(monkeypatch):
    provider_telemetry_store.clear()
    monkeypatch.delenv("MENTORFLOW_PROVIDER_TELEMETRY_PATH", raising=False)
    monkeypatch.delenv("MENTORFLOW_USER_DATA_ROOT", raising=False)
    yield
    provider_telemetry_store.clear()


def _install_response_client(monkeypatch, payload: dict, status_code: int = 200):
    class Client:
        def __init__(self, *args, **kwargs):
            self.follow_redirects = kwargs.get("follow_redirects")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, *args, **kwargs):
            assert kwargs["follow_redirects"] is False
            return httpx.Response(
                status_code,
                json=payload,
                request=httpx.Request("POST", "https://provider.test/v1/chat/completions"),
            )

    monkeypatch.setattr("app.provider_service.httpx.AsyncClient", Client)


def _completion_payload(*, usage: dict | None = None) -> dict:
    payload = {
        "model": "deepseek-chat",
        "choices": [{"message": {"content": "ok"}}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def test_deepseek_cache_ratio_preserves_reported_token_counts():
    usage = normalize_provider_usage(
        _completion_payload(
            usage={
                "prompt_tokens": 1_000,
                "completion_tokens": 200,
                "total_tokens": 1_200,
                "prompt_cache_hit_tokens": 700,
                "prompt_cache_miss_tokens": 300,
            }
        )
    )
    assert usage.prompt_tokens == 1_000
    assert usage.completion_tokens == 200
    assert usage.total_tokens == 1_200
    assert usage.prompt_cache_hit_tokens == 700
    assert usage.prompt_cache_miss_tokens == 300
    assert usage.cache_hit_ratio == pytest.approx(0.7)


def test_zero_deepseek_cache_hits_is_a_real_zero_ratio():
    usage = normalize_provider_usage(
        {"usage": {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1_000}}
    )
    assert usage.prompt_cache_hit_tokens == 0
    assert usage.prompt_cache_miss_tokens == 1_000
    assert usage.cache_hit_ratio == 0.0


def test_absent_cache_fields_are_unavailable_not_zero():
    usage = normalize_provider_usage(
        {"usage": {"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30}}
    )
    assert usage.prompt_cache_hit_tokens is None
    assert usage.prompt_cache_miss_tokens is None
    assert usage.cache_hit_ratio is None


def test_success_without_usage_still_records_a_successful_request(monkeypatch):
    _install_response_client(monkeypatch, _completion_payload())
    provider = OpenAICompatibleProvider(
        "https://provider.test",
        "deepseek-chat",
        "secret",
    )
    assert asyncio.run(provider.text([{"role": "user", "content": "hi"}])) == "ok"

    receipt = provider_telemetry_store.latest()
    assert receipt is not None
    assert receipt.success is True
    assert receipt.task_type == "generic"
    assert receipt.prompt_version == "unversioned"
    assert receipt.local_result_cache_hit is None
    assert receipt.estimated_cost_usd is None
    assert receipt.pricing_version is None
    assert receipt.prompt_tokens is None
    assert receipt.completion_tokens is None
    assert receipt.total_tokens is None
    assert receipt.prompt_cache_hit_tokens is None
    assert receipt.prompt_cache_miss_tokens is None
    assert receipt.cache_hit_ratio is None


def test_async_client_compatibility_response_without_extensions(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: _completion_payload(),
            )

    monkeypatch.setattr("app.provider_service.httpx.AsyncClient", Client)
    provider = OpenAICompatibleProvider(
        "https://provider.test",
        "deepseek-chat",
        "secret",
    )
    assert asyncio.run(provider.text([{"role": "user", "content": "hi"}])) == "ok"
    assert len(provider_telemetry_store.receipts()) == 1
    receipt = provider_telemetry_store.latest()
    assert receipt is not None and receipt.success is True
    assert receipt.total_tokens is None


def test_request_context_and_deepseek_usage_are_recorded_and_persisted(monkeypatch, tmp_path):
    path = tmp_path / "provider-usage.jsonl"
    monkeypatch.setenv("MENTORFLOW_PROVIDER_TELEMETRY_PATH", str(path))
    _install_response_client(
        monkeypatch,
        _completion_payload(
            usage={
                "prompt_tokens": 1_000,
                "completion_tokens": 200,
                "total_tokens": 1_200,
                "prompt_cache_hit_tokens": 700,
                "prompt_cache_miss_tokens": 300,
            }
        ),
    )
    provider = OpenAICompatibleProvider(
        "https://provider.test",
        "deepseek-chat",
        "secret",
    )

    with provider_request_context(
        task_type="planning",
        request_id="req_planning_1",
        prompt_version="planning-v3",
        local_result_cache_hit=False,
    ):
        assert asyncio.run(provider.text([{"role": "user", "content": "plan"}])) == "ok"

    receipt = provider_telemetry_store.latest()
    assert receipt is not None
    assert receipt.provider == "openai_compatible"
    assert receipt.model == "deepseek-chat"
    assert receipt.task_type == "planning"
    assert receipt.request_id == "req_planning_1"
    assert receipt.prompt_version == "planning-v3"
    assert receipt.schema_version == "provider_usage_v1"
    assert receipt.local_result_cache_hit is False
    assert receipt.cache_hit_ratio == pytest.approx(0.7)
    persisted = json.loads(path.read_text(encoding="utf-8").strip())
    assert persisted == receipt.to_dict()


def test_provider_http_error_records_latency_without_fabricated_usage(monkeypatch):
    _install_response_client(monkeypatch, {"error": {"message": "rate limited"}}, 429)
    provider = OpenAICompatibleProvider(
        "https://provider.test",
        "deepseek-chat",
        "secret",
    )

    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.text([{"role": "user", "content": "hi"}]))
    assert caught.value.code == "PROVIDER_RATE_LIMITED"

    receipt = provider_telemetry_store.latest()
    assert receipt is not None
    assert receipt.success is False
    assert receipt.error_class == "PROVIDER_RATE_LIMITED"
    assert receipt.request_id.startswith("req_telemetry_")
    assert receipt.latency_ms >= 1
    assert receipt.prompt_tokens is None
    assert receipt.completion_tokens is None
    assert receipt.total_tokens is None
    assert receipt.prompt_cache_hit_tokens is None
    assert receipt.prompt_cache_miss_tokens is None
    assert receipt.cache_hit_ratio is None


def test_provider_schema_error_replaces_transport_success_with_error(monkeypatch):
    _install_response_client(
        monkeypatch,
        {
            "model": "deepseek-chat",
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 2,
                "total_tokens": 22,
            },
        },
    )
    provider = OpenAICompatibleProvider(
        "https://provider.test",
        "deepseek-chat",
        "secret",
    )

    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.text([{"role": "user", "content": "hi"}]))
    assert caught.value.code == "PROVIDER_SCHEMA_ERROR"
    assert len(provider_telemetry_store.receipts()) == 1
    receipt = provider_telemetry_store.latest()
    assert receipt is not None
    assert receipt.success is False
    assert receipt.error_class == "PROVIDER_SCHEMA_ERROR"
    assert receipt.total_tokens == 22


def test_provider_timeout_records_error_receipt(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, *args, **kwargs):
            request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
            raise httpx.ReadTimeout("timeout", request=request)

    monkeypatch.setattr("app.provider_service.httpx.AsyncClient", Client)
    provider = OpenAICompatibleProvider(
        "https://provider.test",
        "deepseek-chat",
        "secret",
    )

    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.text([{"role": "user", "content": "hi"}]))
    assert caught.value.code == "PROVIDER_TIMEOUT"
    receipt = provider_telemetry_store.latest()
    assert receipt is not None
    assert receipt.success is False
    assert receipt.error_class == "PROVIDER_TIMEOUT"
    assert receipt.total_tokens is None


def test_gemini_malformed_grounding_records_schema_error(monkeypatch):
    _install_response_client(
        monkeypatch,
        {
            "modelVersion": "gemini-test",
            "candidates": [
                {
                    "content": {"parts": [{"text": "answer"}]},
                    "groundingMetadata": "malformed",
                }
            ],
        },
    )
    provider = GeminiProvider(
        "https://provider.test",
        "gemini-test",
        "secret",
    )

    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.grounded_web_search("query"))
    assert caught.value.code == "PROVIDER_SEARCH_SCHEMA_ERROR"
    receipt = provider_telemetry_store.latest()
    assert receipt is not None
    assert receipt.success is False
    assert receipt.error_class == "PROVIDER_SEARCH_SCHEMA_ERROR"
