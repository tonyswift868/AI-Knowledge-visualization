from __future__ import annotations

import asyncio

import httpx
import pytest

from app import provider_runtime, provider_service
from app.provider_runtime import reset_provider_sessions
from app.provider_service import ProviderError
from app.provider_url_policy import ProviderURLPolicyError, validate_provider_base_url


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:11434",
        "http://localhost:1234/v1",
        "http://192.168.10.20:8080/v1",
        "http://[fd00::1]:1234/v1",
        "http://printer.local",
        "https://api.example.com/v1",
    ],
)
def test_provider_url_policy_allows_local_lan_and_cloud_urls(value: str) -> None:
    assert validate_provider_base_url(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "file:///tmp/provider",
        "ftp://provider.example",
        "data:text/plain,provider",
        "javascript:alert(1)",
        "gopher://127.0.0.1",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1#fragment",
        "provider.example/v1",
        "http:///missing-host",
        "http://provider.example:bad",
        "http://provider.example:65536",
        "http://provider.example:0",
        "http://provider.example/%ZZ",
        "https://provider.example/v1?api_key=SECRET",
        "https://provider.example/v1?",
        "http://provider.example/\r\nX-Injected: yes",
        " https://provider.example/v1",
    ],
)
def test_provider_url_policy_rejects_unsafe_or_malformed_urls(value: str) -> None:
    with pytest.raises(ProviderURLPolicyError) as error:
        validate_provider_base_url(value)
    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
    assert "password" not in error.value.message
    assert value not in error.value.message


def test_runtime_rejects_invalid_url_before_credential_lookup(monkeypatch) -> None:
    reset_provider_sessions()

    def unexpected_lookup(_credential_ref: str):
        raise AssertionError("credential lookup must not happen for invalid URL")

    monkeypatch.setattr(provider_runtime, "get_secret", unexpected_lookup)
    with pytest.raises(ProviderError) as error:
        provider_runtime.create_provider_session(
            {
                "provider": "openai-compatible",
                "base_url": "file:///tmp/provider",
                "model": "local-model",
                "credential_ref": "mfcred_invalid_url",
            }
        )
    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
    assert error.value.status == 400
    assert "mfcred_invalid_url" not in str(error.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://provider.example/v1?api_key=SECRET",
        "https://provider.example/v1?",
    ],
)
def test_runtime_rejects_query_url_before_credential_lookup_and_session_creation(
    monkeypatch, base_url: str
) -> None:
    reset_provider_sessions()
    looked_up = False

    def unexpected_lookup(_credential_ref: str):
        nonlocal looked_up
        looked_up = True
        raise AssertionError("credential lookup must not happen for query URL")

    monkeypatch.setattr(provider_runtime, "get_secret", unexpected_lookup)
    with pytest.raises(ProviderError) as error:
        provider_runtime.create_provider_session(
            {
                "provider": "openai-compatible",
                "base_url": base_url,
                "model": "local-model",
                "credential_ref": "mfcred_query_secret",
            }
        )
    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
    assert error.value.status == 400
    assert looked_up is False
    assert "SECRET" not in str(error.value)
    assert provider_runtime.latest_verified_provider_session() is None


def test_http_provider_does_not_follow_redirects_and_returns_provider_error(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RedirectClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            captured["request_follow_redirects"] = _kwargs.get("follow_redirects")
            return httpx.Response(
                302,
                headers={"location": "https://other.example/v1"},
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(provider_service.httpx, "AsyncClient", RedirectClient)
    provider = provider_service.OpenAICompatibleProvider(
        "http://127.0.0.1:1234/v1",
        "local-model",
        "secret-value",
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.text([]))
    assert captured["follow_redirects"] is False
    assert captured["request_follow_redirects"] is False
    assert error.value.code == "PROVIDER_HTTP_ERROR"
    assert error.value.status == 502
    assert error.value.details["upstream_status"] == 302
    assert "secret-value" not in str(error.value)


def test_provider_factory_applies_url_policy_at_runtime_boundary() -> None:
    with pytest.raises(ProviderError) as error:
        provider_service.get_provider(
            {
                "provider": "openai-compatible",
                "base_url": "https://user:secret@provider.example/v1",
                "model": "model",
                "api_key": "secret-value",
            }
        )
    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
    assert "secret" not in str(error.value)
