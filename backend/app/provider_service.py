from __future__ import annotations

import json
from typing import Any

import httpx


class ProviderError(Exception):
    def __init__(self, code: str, message: str, status: int = 502, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


class Provider:
    provider_name = "unknown"

    async def test(self) -> dict[str, Any]:
        raise NotImplementedError

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        raise NotImplementedError

    async def json(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> dict[str, Any]:
        raw = await self.text(messages, temperature)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Provider JSON must be an object")
            return result
        except Exception as exc:
            raise ProviderError("INVALID_PROVIDER_JSON", "Provider 返回的不是合法 JSON", 502) from exc


class FakeProvider(Provider):
    provider_name = "fake"

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.provider_name, "model": "fake", "model_reported": "fake"}

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        return "Fake Provider：请依据资料证据完成一个可核验的小步骤。"

    async def json(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> dict[str, Any]:
        text = "\n".join(str(x.get("content", "")) for x in messages)
        return {"message": "请用自己的话解释证据中的关键点，并给出一个例子。", "status": "grounded" if "EVIDENCE" in text else "insufficient", "confidence": 0.8}


class HTTPProvider(Provider):
    def __init__(self, base_url: str, model: str, key: str, temperature: float = 0.2):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.key = key
        self.temperature = temperature
        self.last_model: str | None = None

    async def test(self) -> dict[str, Any]:
        await self.text([{"role": "user", "content": "Respond with exactly OK."}], temperature=0)
        return {"ok": True, "provider": self.provider_name, "model": self.model, "model_reported": self.last_model or self.model}

    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                return await client.request(*args, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderError("PROVIDER_TIMEOUT", "Provider 请求超时", 504) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("PROVIDER_CONNECTION_ERROR", "无法连接到 Provider", 502) from exc

    def response_error(self, response: httpx.Response, provider: str) -> ProviderError:
        code_map = {
            401: ("PROVIDER_AUTH_FAILED", "API Key 无效或没有权限"),
            403: ("PROVIDER_PERMISSION_DENIED", "Provider 拒绝访问该资源"),
            404: ("PROVIDER_ROUTE_NOT_FOUND", "Provider Base URL 或请求路由不存在"),
            429: ("PROVIDER_RATE_LIMITED", "Provider 请求过于频繁或余额不足"),
        }
        code, message = code_map.get(response.status_code, ("PROVIDER_HTTP_ERROR", f"{provider} Provider 返回 HTTP {response.status_code}"))
        return ProviderError(code, message, response.status_code, {"provider": provider, "model": self.model})


class OpenAICompatibleProvider(HTTPProvider):
    provider_name = "openai_compatible"

    def completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return self.base_url + "/chat/completions"
        return self.base_url + "/v1/chat/completions"

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        response = await self.request("POST", self.completions_url(), headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "temperature": temperature})
        if response.status_code >= 400:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("model") or self.model
            return str(payload["choices"][0]["message"]["content"])
        except Exception as exc:
            raise ProviderError("PROVIDER_SCHEMA_ERROR", "OpenAI-compatible 返回结构不兼容", 502, {"provider": self.provider_name, "model": self.model}) from exc


class GeminiProvider(HTTPProvider):
    provider_name = "gemini"

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        content = "\n".join(str(x.get("content", "")) for x in messages)
        response = await self.request("POST", f"{self.base_url}/models/{self.model}:generateContent", headers={"x-goog-api-key": self.key, "content-type": "application/json"}, json={"contents": [{"parts": [{"text": content}]}], "generationConfig": {"temperature": temperature}})
        if response.status_code >= 400:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("modelVersion") or self.model
            return str(payload["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as exc:
            raise ProviderError("PROVIDER_SCHEMA_ERROR", "Gemini 返回结构不兼容", 502, {"provider": self.provider_name, "model": self.model}) from exc


class AnthropicProvider(HTTPProvider):
    provider_name = "anthropic"

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        response = await self.request("POST", self.base_url + "/messages", headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={"model": self.model, "max_tokens": 1200, "messages": messages, "temperature": temperature})
        if response.status_code >= 400:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("model") or self.model
            return str(payload["content"][0]["text"])
        except Exception as exc:
            raise ProviderError("PROVIDER_SCHEMA_ERROR", "Anthropic 返回结构不兼容", 502, {"provider": self.provider_name, "model": self.model}) from exc


def get_provider(config: dict[str, Any] | None):
    config = config or {"provider": "fake"}
    kind = config.get("provider", "fake")
    if kind == "fake":
        return FakeProvider()
    if kind not in {"openai-compatible", "gemini", "claude", "anthropic"}:
        raise ProviderError("PROVIDER_UNSUPPORTED", "不支持的 Provider", 400, {"provider": kind})
    if not all(config.get(field) for field in ("base_url", "model", "api_key")):
        raise ProviderError("PROVIDER_CONFIG_MISSING", "真实 Provider 需要 Base URL、Model 和 API Key", 400, {"provider": kind, "model": config.get("model", "")})
    provider_cls = {"openai-compatible": OpenAICompatibleProvider, "gemini": GeminiProvider, "claude": AnthropicProvider, "anthropic": AnthropicProvider}[kind]
    return provider_cls(config["base_url"], config["model"], config["api_key"], config.get("temperature", 0.2))
