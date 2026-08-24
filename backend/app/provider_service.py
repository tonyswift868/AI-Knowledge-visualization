from __future__ import annotations

import json
import base64
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from .knowledge_policy import PROVIDER_POLICY
from .provider_url_policy import ProviderURLPolicyError, validate_provider_base_url


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


PROVIDER_TIMEOUT = httpx.Timeout(
    timeout=PROVIDER_POLICY.total_timeout_seconds,
    connect=PROVIDER_POLICY.connect_timeout_seconds,
    write=PROVIDER_POLICY.write_timeout_seconds,
    pool=PROVIDER_POLICY.pool_timeout_seconds,
)


class ProviderError(Exception):
    def __init__(self, code: str, message: str, status: int = 502, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def _validated_base_url(value: object) -> str:
    """Convert lexical URL policy failures to the shared Provider error type."""

    try:
        return validate_provider_base_url(value)  # type: ignore[arg-type]
    except ProviderURLPolicyError as exc:
        raise ProviderError(
            exc.code,
            exc.message,
            exc.status,
            {"field": "base_url"},
        ) from exc


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Extract a JSON object from a model response without accepting prose as JSON.

    Models sometimes wrap an otherwise valid object in a Markdown fence or add
    a short lead-in/trailing sentence.  ``JSONDecoder.raw_decode`` lets us
    recover the first complete object while still rejecting arrays and plain
    text.  Callers that need a strict structured response can treat ``None``
    as a protocol error; conversational callers may safely fall back to the
    original text.
    """

    text = str(raw or "").strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_conversational_payload(
    raw: str,
    *,
    text_field: str,
    fallback: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return a usable conversational payload even when a model emits prose.

    Structured planning and assessment callers should continue to use
    :meth:`Provider.json`.  Teacher/chat expression callers may use this
    helper because ordinary prose is a valid user-facing answer even when the
    model ignored the requested JSON envelope.
    """

    parsed = parse_json_object(raw)
    if parsed is not None:
        return parsed, False
    text = str(raw or "").strip()
    if not text:
        raise ProviderError(
            "PROVIDER_EMPTY_RESPONSE",
            "Provider 返回了空的教师回复",
            502,
        )
    payload = dict(fallback or {})
    payload[text_field] = text
    return payload, True


class Provider:
    provider_name = "unknown"

    async def test(self) -> dict[str, Any]:
        raise NotImplementedError

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        raise NotImplementedError

    async def vision(
        self,
        prompt: str,
        images: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> str:
        raise ProviderError(
            "PROVIDER_VISION_UNSUPPORTED",
            "当前 Provider 不支持图片理解",
            422,
            {"provider": self.provider_name},
        )

    async def grounded_web_search(
        self,
        prompt: str,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Return a web-grounded answer together with machine-verifiable sources.

        A normal language-model completion is not web search. Providers must
        override this method only when their API returns source metadata for an
        actual search call; callers can then reject uncited recommendations.
        """
        raise ProviderError(
            "PROVIDER_WEB_SEARCH_UNSUPPORTED",
            "当前 Provider 不支持带来源引用的联网检索",
            422,
            {"provider": self.provider_name},
        )

    async def json(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> dict[str, Any]:
        raw = await self.text(messages, temperature)
        result = parse_json_object(raw)
        if result is not None:
            return result
        cause = ValueError("Provider JSON must be an object")
        raise ProviderError("INVALID_PROVIDER_JSON", "Provider 返回的不是合法 JSON", 502) from cause

    async def compact_json(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Return bounded JSON; adapters may use provider-native fast controls."""

        return await self.json(messages, temperature)

    async def structured(
        self,
        messages: list[dict[str, Any]],
        response_model: type[StructuredModel],
        temperature: float = 0.2,
    ) -> StructuredModel:
        """Validate adapter JSON against the application-owned Pydantic schema."""

        try:
            return response_model.model_validate(await self.json(messages, temperature))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_STRUCTURED_OUTPUT_INVALID",
                "Provider 结构化输出不符合知识地图协议",
                502,
                {"provider": self.provider_name},
            ) from exc


class FakeProvider(Provider):
    provider_name = "fake"

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.provider_name, "model": "fake", "model_reported": "fake"}

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        return "Fake Provider：请依据资料证据完成一个可核验的小步骤。"

    async def vision(
        self,
        prompt: str,
        images: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> str:
        return "Fake Provider 未读取图片内容。"

    async def json(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> dict[str, Any]:
        text = "\n".join(str(x.get("content", "")) for x in messages)
        return {"message": "请用自己的话解释证据中的关键点，并给出一个例子。", "status": "grounded" if "EVIDENCE" in text else "insufficient", "confidence": 0.8}


class HTTPProvider(Provider):
    def __init__(self, base_url: str, model: str, key: str, temperature: float = 0.2):
        self.base_url = _validated_base_url(base_url).rstrip("/")
        self.model = model
        self.key = key
        self.temperature = temperature
        self.last_model: str | None = None

    async def test(self) -> dict[str, Any]:
        await self.text([{"role": "user", "content": "Respond with exactly OK."}], temperature=0)
        return {"ok": True, "provider": self.provider_name, "model": self.model, "model_reported": self.last_model or self.model}

    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        try:
            # Re-check at the network boundary as a defense against mutable
            # legacy handles or callers changing ``base_url`` after
            # construction.
            _validated_base_url(self.base_url)
            # Semantic extraction and grounded teaching prompts are materially
            # larger than the lightweight connection probe. Keep connection
            # failures fast, while allowing a real model enough time to finish
            # a bounded response without forcing a Fake fallback.
            # Force this per-request as well as at client construction so a
            # future caller cannot accidentally opt a Provider request into
            # redirect following.
            kwargs["follow_redirects"] = False
            async with httpx.AsyncClient(
                timeout=PROVIDER_TIMEOUT,
                follow_redirects=False,
            ) as client:
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
        if 300 <= response.status_code < 400:
            # Keep redirect responses inside the JSON Provider error surface;
            # returning the upstream 3xx as our API status would make clients
            # follow a redirect before they can inspect the error payload.
            code, message, status = (
                "PROVIDER_HTTP_ERROR",
                "Provider 请求被重定向；请直接填写最终 Base URL",
                502,
            )
        else:
            code, message = code_map.get(response.status_code, ("PROVIDER_HTTP_ERROR", f"{provider} Provider 返回 HTTP {response.status_code}"))
            status = response.status_code
        return ProviderError(code, message, status, {"provider": provider, "model": self.model, "upstream_status": response.status_code})


class OpenAICompatibleProvider(HTTPProvider):
    provider_name = "openai_compatible"

    def api_base_url(self) -> str:
        """Return the OpenAI SDK base URL matching ``completions_url``.

        Users may enter a server root, an OpenAI-style ``/v1`` root, or the
        complete chat-completions endpoint.  The handwritten transport has
        historically accepted all three, so Instructor must preserve that
        contract instead of silently calling a different route.
        """

        if self.base_url.endswith("/chat/completions"):
            return self.base_url.removesuffix("/chat/completions")
        if self.base_url.endswith("/v1"):
            return self.base_url
        return self.base_url + "/v1"

    def completions_url(self) -> str:
        return self.api_base_url() + "/chat/completions"

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        response = await self.request("POST", self.completions_url(), headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "temperature": temperature})
        if response.status_code >= 300:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("model") or self.model
            return str(payload["choices"][0]["message"]["content"])
        except Exception as exc:
            raise ProviderError("PROVIDER_SCHEMA_ERROR", "OpenAI-compatible 返回结构不兼容", 502, {"provider": self.provider_name, "model": self.model}) from exc

    async def compact_json(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Use bounded JSON mode and disable DeepSeek thinking for small UI cards."""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max(64, int(max_tokens)),
            "response_format": {"type": "json_object"},
        }
        if "api.deepseek.com" in self.base_url.casefold():
            payload["thinking"] = {"type": "disabled"}
        response = await self.request(
            "POST",
            self.completions_url(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code >= 300:
            raise self.response_error(response, self.provider_name)
        try:
            body = response.json()
            choice = body["choices"][0]
            self.last_model = body.get("model") or self.model
            if choice.get("finish_reason") == "length":
                raise ProviderError(
                    "PROVIDER_OUTPUT_TRUNCATED",
                    "Provider 输出超过卡片长度限制",
                    502,
                )
            result = parse_json_object(str(choice["message"]["content"] or ""))
            if result is None:
                raise ProviderError("INVALID_PROVIDER_JSON", "Provider 返回的不是合法 JSON", 502)
            return result
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_SCHEMA_ERROR",
                "OpenAI-compatible 返回结构不兼容",
                502,
                {"provider": self.provider_name, "model": self.model},
            ) from exc

    async def structured(
        self,
        messages: list[dict[str, Any]],
        response_model: type[StructuredModel],
        temperature: float = 0.2,
    ) -> StructuredModel:
        """Use Instructor retries/schema transport on the existing Provider config."""

        try:
            _validated_base_url(self.base_url)
            import instructor
            from openai import AsyncOpenAI

            # The OpenAI SDK's default AsyncClient follows redirects.  Pass an
            # explicit client so a 3xx response remains an upstream error and
            # cannot silently switch the Provider destination.
            async with httpx.AsyncClient(
                timeout=PROVIDER_TIMEOUT,
                follow_redirects=False,
            ) as http_client:
                client = instructor.from_openai(
                    AsyncOpenAI(
                        api_key=self.key,
                        base_url=self.api_base_url(),
                        timeout=PROVIDER_TIMEOUT,
                        max_retries=0,
                        http_client=http_client,
                    ),
                    mode=instructor.Mode.JSON,
                )
                result = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_model=response_model,
                    temperature=temperature,
                    max_retries=PROVIDER_POLICY.structured_max_retries,
                )
            self.last_model = self.model
            return result
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_STRUCTURED_OUTPUT_INVALID",
                "OpenAI-compatible Provider 未能返回可验证结构",
                502,
                {"provider": self.provider_name, "model": self.model},
            ) from exc

    async def vision(
        self,
        prompt: str,
        images: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image["data"]).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image['media_type']};base64,{encoded}",
                        "detail": "high",
                    },
                }
            )
        return await self.text(
            [{"role": "user", "content": content}],
            temperature=temperature,
        )


class GeminiProvider(HTTPProvider):
    provider_name = "gemini"

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        content = "\n".join(str(x.get("content", "")) for x in messages)
        response = await self.request("POST", f"{self.base_url}/models/{self.model}:generateContent", headers={"x-goog-api-key": self.key, "content-type": "application/json"}, json={"contents": [{"parts": [{"text": content}]}], "generationConfig": {"temperature": temperature}})
        if response.status_code >= 300:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("modelVersion") or self.model
            return str(payload["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as exc:
            raise ProviderError("PROVIDER_SCHEMA_ERROR", "Gemini 返回结构不兼容", 502, {"provider": self.provider_name, "model": self.model}) from exc

    async def vision(
        self,
        prompt: str,
        images: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> str:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for image in images:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image["media_type"],
                        "data": base64.b64encode(image["data"]).decode("ascii"),
                    }
                }
            )
        response = await self.request(
            "POST",
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.key, "content-type": "application/json"},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"temperature": temperature},
            },
        )
        if response.status_code >= 300:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("modelVersion") or self.model
            return str(payload["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_SCHEMA_ERROR",
                "Gemini 图片理解返回结构不兼容",
                502,
                {"provider": self.provider_name, "model": self.model},
            ) from exc

    async def grounded_web_search(
        self,
        prompt: str,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Use Gemini's native Google Search grounding, not model recall."""
        response = await self.request(
            "POST",
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={
                "x-goog-api-key": self.key,
                "content-type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": temperature},
            },
        )
        if response.status_code >= 300:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            candidate = payload["candidates"][0]
            self.last_model = payload.get("modelVersion") or self.model
            text = "".join(
                str(part.get("text") or "")
                for part in candidate.get("content", {}).get("parts", [])
            ).strip()
            metadata = candidate.get("groundingMetadata") or {}
            sources: list[dict[str, Any]] = []
            for index, chunk in enumerate(metadata.get("groundingChunks") or []):
                web = chunk.get("web") if isinstance(chunk, dict) else None
                if not isinstance(web, dict) or not web.get("uri"):
                    continue
                sources.append(
                    {
                        "index": index,
                        "url": str(web["uri"]),
                        "title": str(web.get("title") or ""),
                    }
                )
            supports: list[dict[str, Any]] = []
            for support in metadata.get("groundingSupports") or []:
                segment = support.get("segment") if isinstance(support, dict) else None
                if not isinstance(segment, dict):
                    continue
                supports.append(
                    {
                        "start_index": int(segment.get("startIndex") or 0),
                        "end_index": int(segment.get("endIndex") or 0),
                        "text": str(segment.get("text") or ""),
                        "source_indices": [
                            int(value)
                            for value in support.get("groundingChunkIndices") or []
                        ],
                    }
                )
            return {
                "text": text,
                "sources": sources,
                "supports": supports,
                "queries": [
                    str(value)
                    for value in metadata.get("webSearchQueries") or []
                    if str(value).strip()
                ],
                "provider": self.provider_name,
                "model": self.last_model or self.model,
                "search_grounded": bool(sources),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "PROVIDER_SEARCH_SCHEMA_ERROR",
                "Gemini 联网检索返回结构不兼容",
                502,
                {"provider": self.provider_name, "model": self.model},
            ) from exc


class AnthropicProvider(HTTPProvider):
    provider_name = "anthropic"

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        response = await self.request("POST", self.base_url + "/messages", headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={"model": self.model, "max_tokens": 1200, "messages": messages, "temperature": temperature})
        if response.status_code >= 300:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("model") or self.model
            return str(payload["content"][0]["text"])
        except Exception as exc:
            raise ProviderError("PROVIDER_SCHEMA_ERROR", "Anthropic 返回结构不兼容", 502, {"provider": self.provider_name, "model": self.model}) from exc

    async def vision(
        self,
        prompt: str,
        images: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> str:
        content: list[dict[str, Any]] = []
        for image in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image["media_type"],
                        "data": base64.b64encode(image["data"]).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": prompt})
        response = await self.request(
            "POST",
            self.base_url + "/messages",
            headers={
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1200,
                "messages": [{"role": "user", "content": content}],
                "temperature": temperature,
            },
        )
        if response.status_code >= 300:
            raise self.response_error(response, self.provider_name)
        try:
            payload = response.json()
            self.last_model = payload.get("model") or self.model
            return str(payload["content"][0]["text"])
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_SCHEMA_ERROR",
                "Anthropic 图片理解返回结构不兼容",
                502,
                {"provider": self.provider_name, "model": self.model},
            ) from exc


def get_provider(config: dict[str, Any] | None):
    config = config or {"provider": "fake"}
    kind = config.get("provider", "fake")
    if kind == "fake":
        return FakeProvider()
    if kind not in {"openai-compatible", "gemini", "claude", "anthropic"}:
        raise ProviderError("PROVIDER_UNSUPPORTED", "不支持的 Provider", 400, {"provider": kind})
    if not all(config.get(field) for field in ("base_url", "model", "api_key")):
        raise ProviderError("PROVIDER_CONFIG_MISSING", "真实 Provider 需要 Base URL、Model 和 API Key", 400, {"provider": kind, "model": config.get("model", "")})
    base_url = _validated_base_url(config["base_url"])
    provider_cls = {"openai-compatible": OpenAICompatibleProvider, "gemini": GeminiProvider, "claude": AnthropicProvider, "anthropic": AnthropicProvider}[kind]
    return provider_cls(base_url, config["model"], config["api_key"], config.get("temperature", 0.2))
