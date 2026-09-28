"""Synchronous provider API; call from a worker, not the UI/event-loop thread.

http_client accepts an httpx.AsyncClient (caller owns it), or an async transport
including httpx.MockTransport (owned for this call). No SDK or credentials store
is needed. on_text enables real SSE for Messages, Chat, Responses and DashScope;
Gemini GenerateContent delivers one final callback. No automatic paid retries.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading

import httpx

from ..errors import (ModelError, ModelCancelled, MODEL_SEARCH_UNSUPPORTED,
                      SEARCH_UNSUPPORTED_MESSAGE)
from ..transport import normalize_url
from .base import ModelResult, ProviderSpec, SearchSupport, Usage, WebHit, invalid
from .registry import REGISTRY, detect_provider
from ._http import HTTP, guarded
from ._accounting import Accounting
from . import anthropic_messages, dashscope, gemini, openai_chat, openai_responses

__all__ = ['SearchSupport', 'ProviderSpec', 'REGISTRY', 'detect_provider', 'Usage',
           'WebHit', 'ModelResult', 'ModelError', 'ModelCancelled', 'complete']

_ADAPTERS = {'anthropic_messages': anthropic_messages, 'dashscope_generation': dashscope,
             'gemini_generate_content': gemini, 'openai_chat': openai_chat,
             'openai_responses': openai_responses}


def complete(provider_id, *, base_url, model, api_key, messages, system=None,
             enable_search=False, max_search_uses=8, max_tokens=4096, temperature=None,
             timeout=120.0, cancel=None, on_text=None, http_client=None) -> ModelResult:
    if not isinstance(provider_id, str) or provider_id not in REGISTRY:
        raise ModelError('MODEL_CONFIG_INVALID', '请选择支持的模型厂商。')
    spec = REGISTRY[provider_id]
    if enable_search and spec.search is None:
        raise ModelError(MODEL_SEARCH_UNSUPPORTED, SEARCH_UNSUPPORTED_MESSAGE)
    base_url = normalize_url(base_url)
    if (not isinstance(model, str) or not model.strip() or len(model) > 256
            or any(ord(c) < 32 for c in model)):
        raise ModelError('MODEL_CONFIG_INVALID', '请填写有效的模型名称。')
    if not isinstance(api_key, str) or not api_key.strip():
        raise ModelError('MODEL_KEY_REQUIRED', '请填写 API Key。')
    if len(api_key) > 16384 or not api_key.isascii() or any(c.isspace() or ord(c) < 32 for c in api_key):
        raise ModelError('MODEL_CONFIG_INVALID', 'API Key 格式无效。')
    if (not isinstance(messages, (list, tuple)) or not messages or any(
            not isinstance(m, dict) or not isinstance(m.get('role'), str)
            or m.get('role') not in {'user', 'assistant'}
            or not isinstance(m.get('content'), str) for m in messages)
            or (system is not None and not isinstance(system, str))):
        raise ModelError('MODEL_MESSAGES_INVALID', '请提供有效的对话内容，系统指令须单独传入。')
    # Copy only the public contract fields; no arbitrary tool/client state injection.
    messages = [{'role': m['role'], 'content': m['content']} for m in messages]
    try:
        size = len(json.dumps([system, messages], ensure_ascii=False).encode('utf-8'))
    except UnicodeError:
        raise ModelError('MODEL_MESSAGES_INVALID', '对话内容包含无效字符。') from None
    if size > 2 * 1024 * 1024:
        raise ModelError('MODEL_MESSAGES_TOO_LARGE', '对话内容过大，请缩小请求后重试。')
    if (type(max_search_uses) is not int or not 1 <= max_search_uses <= 1024
            or type(max_tokens) is not int or max_tokens < 1
            or type(enable_search) is not bool
            or type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0
            or (temperature is not None and (type(temperature) not in {int, float}
                or not math.isfinite(temperature)))):
        raise ModelError('MODEL_CONFIG_INVALID', '模型调用参数无效。')
    if cancel is not None and not isinstance(cancel, threading.Event):
        raise ModelError('MODEL_CANCEL_INVALID', '取消标记必须为 threading.Event。')
    if (on_text is not None and not callable(on_text)) or (
            http_client is not None and not isinstance(http_client, (httpx.AsyncClient, httpx.AsyncBaseTransport))):
        raise ModelError('MODEL_CONFIG_INVALID', '回调或 HTTP 客户端类型无效。')
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ModelError('MODEL_CONFIG_INVALID', '请在界面与异步事件循环以外的工作线程调用模型。')
    config = dict(provider_id=provider_id, protocol=spec.protocol, base_url=base_url, model=model, messages=messages,
                  system=system, enable_search=enable_search, max_search_uses=max_search_uses,
                  max_tokens=max_tokens, temperature=temperature, on_text=on_text)
    accounting = Accounting(config)

    def failure(error):
        error = accounting.attach(error)
        # Model names are vendor-controlled too; never journal an echoed key.
        if isinstance(error.model, str) and api_key in error.model:
            error.model = ''
        return error

    async def call():
        async with HTTP(provider_id, api_key, timeout, http_client, cancel, accounting) as http:
            result = await _ADAPTERS[spec.protocol].run(http, config)
            http.check()
            accounting.check_truncated()
            if (not isinstance(result.text, str) or not result.text.strip()
                    or not isinstance(result.model, str) or api_key in result.model):
                raise invalid()
            return result

    try:
        return asyncio.run(guarded(call, timeout, cancel))
    except ModelError as exc:
        raise failure(exc) from None
    except (TimeoutError, httpx.TimeoutException):
        raise failure(ModelError('MODEL_TIMEOUT', '模型请求超时，请稍后重试。')) from None
    except httpx.HTTPError:
        raise failure(ModelError('MODEL_CONNECTION_FAILED', '无法连接模型服务，请检查地址和网络。')) from None
    except Exception:
        # Parse failures, callback/client exceptions may contain request secrets.
        raise failure(invalid()) from None
