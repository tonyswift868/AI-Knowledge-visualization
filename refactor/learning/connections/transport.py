"""Bounded OpenAI-compatible HTTP adapted from provider_service.HTTPProvider.

Retains explicit connection probes, URL normalization, no redirects and safe
HTTP error categories; replaces runtime/telemetry coupling with cancellation.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import random
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from .errors import ModelError, ModelCancelled


def normalize_url(value):
    try:
        if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            raise ValueError()
        url = urlsplit(value)
        if not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment or "%" in url.netloc:
            raise ValueError()
        _ = url.port
        try:
            loopback = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            loopback = url.hostname.casefold() == "localhost"
        if url.scheme != "https" and not (url.scheme == "http" and loopback):
            raise ValueError()
        return urlunsplit((url.scheme, url.netloc, url.path.rstrip("/"), "", ""))
    except ValueError:
        raise ModelError("MODEL_URL_INVALID", "请填写不含账号、查询参数或片段的 HTTPS 模型地址；本机服务可使用 HTTP。") from None


def completions_url(base):
    base = normalize_url(base)
    if base.endswith("/chat/completions"):
        return base
    # DeepSeek's current official base uses /chat/completions directly.  Keep
    # the existing /v1 convention for generic OpenAI-compatible local hosts
    # and explicit /v1 bases for backwards compatibility.
    if base.endswith("/v1") or base.casefold().split("//", 1)[-1].split("/", 1)[0] == "api.deepseek.com":
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def http_error(status):
    code, message = {
        401: ("MODEL_AUTH_FAILED", "API Key 无效或没有访问权限。"),
        403: ("MODEL_PERMISSION_DENIED", "模型服务拒绝访问。"),
        404: ("MODEL_NOT_FOUND", "请检查模型地址和模型名称。"),
        429: ("MODEL_RATE_LIMITED", "请求太频繁，请稍等再试。"),
    }.get(status, ("MODEL_HTTP_ERROR", "模型服务暂时无法完成请求。"))
    if 300 <= status < 400:
        code, message = "MODEL_REDIRECT_REJECTED", "模型地址发生重定向，请填写最终 API 地址。"
    return ModelError(code, message, status)


async def _send(config, secret, messages, idempotency_key=None):
    headers = {"Authorization": "Bearer " + secret, "Content-Type": "application/json"}
    if idempotency_key:
        headers["X-Idempotency-Key"] = str(idempotency_key)[:200]
    raw = bytearray()
    try:
        # Legacy component clients share the same independent first-event guard.
        # Import at call time: the provider HTTP layer also uses http_error above.
        from .providers._http import HTTP, guarded
        from .providers._accounting import Accounting
        accounting = Accounting(dict(config, provider_id='relay', protocol='openai_chat', enable_search=False))
        async def read():
            async with HTTP('relay', secret, config['timeout'], accounting=accounting) as http:
                async with http.response(completions_url(config['base_url']),
                    dict(model=config['model'],messages=messages,temperature=config['temperature']),headers) as response:
                    raw.extend(await http.read(response,limit=2*1024*1024))
        await guarded(read,config['timeout'],None,accounting)
        try:
            data = json.loads(raw)
            result = data["choices"][0]["message"]["content"]
            if not isinstance(result, str) or not result.strip():
                raise ValueError()
            return result
        except (ValueError, TypeError, KeyError, IndexError):
            raise ModelError("MODEL_RESPONSE_INVALID", "模型没有返回有效的文字回复。") from None
    finally:
        headers.clear()
        raw[:] = b"\0" * len(raw)


async def request(config, secret, messages, cancel, stopped, *, idempotency_key=None):
    """Execute with bounded retry policy and one idempotent request envelope.

    The client owns the idempotency key at the observability/request layer;
    retries here reuse the same request body and therefore cannot advance
    application state more than once.  Client errors (400/401/402/422) are
    deliberately returned immediately.
    """
    for attempt in range(1, 4):
        task = asyncio.create_task(_send(config, secret, messages, idempotency_key))
        try:
            async with asyncio.timeout(config["timeout"]):
                while not task.done():
                    if stopped.is_set() or (cancel is not None and cancel.is_set()):
                        raise ModelCancelled()
                    await asyncio.wait({task}, timeout=0.03)
                if stopped.is_set() or (cancel is not None and cancel.is_set()):
                    raise ModelCancelled()
                return await task
        except ModelCancelled:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            error = ModelError("MODEL_TIMEOUT", "模型请求超时，请稍后重试。")
        except ModelError as exc:
            error = exc
        except httpx.HTTPError as exc:
            error = ModelError("MODEL_CONNECTION_FAILED", "无法连接模型服务，请检查地址和网络。")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        retryable_status = getattr(error, "status", None) in {429, 500, 503}
        retryable_network = getattr(error, "code", None) in {"MODEL_TIMEOUT", "MODEL_CONNECTION_FAILED"}
        if attempt >= 3 or not (retryable_status or retryable_network):
            raise error
        delay = min(4.0, 0.25 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.15)
        end = time.monotonic() + delay
        while time.monotonic() < end:
            if stopped.is_set() or (cancel is not None and cancel.is_set()):
                raise ModelCancelled()
            await asyncio.sleep(min(0.05, end - time.monotonic()))
