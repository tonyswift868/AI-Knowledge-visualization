"""Bounded, cancellable HTTP/SSE. No redirects, logging or automatic retries."""
from __future__ import annotations

import asyncio
import codecs
import json
import time
from contextlib import asynccontextmanager

import httpx

from ..errors import (ModelCancelled, ModelError, MODEL_INSUFFICIENT_BALANCE,
                      INSUFFICIENT_BALANCE_MESSAGE)
from ..transport import http_error, normalize_url
from .base import invalid


MAX_BYTES = 8 * 1024 * 1024


def vendor_error(provider, status, data=None):
    """Only documented machine codes are inspected; never echo vendor messages.

    Sources: api-docs.deepseek.com/quick_start/error_codes/;
    platform.claude.com/docs/en/api/errors;
    help.aliyun.com/zh/model-studio/error-code;
    docs.bigmodel.cn/cn/api/api-code;
    developers.openai.com/api/docs/guides/error-codes (checked 2026-09-27).
    Quota/rate/spending limits without a distinct balance code stay rate errors.
    """
    data = data if isinstance(data, dict) else {}
    error = data.get('error')
    error = error if isinstance(error, dict) else data
    code = str(error.get('code', ''))
    kind = str(error.get('type', ''))
    balance = (provider in {'deepseek', 'anthropic'} and status == 402)
    balance |= provider == 'qwen' and (code in {'Arrearage', 'isv.OUT_OF_SERVICE'}
                                      or kind == 'Arrearage')
    balance |= provider == 'zhipu' and code == '1113'
    balance |= provider == 'openai' and code == 'credit_balance_exhausted'
    if balance:
        return ModelError(MODEL_INSUFFICIENT_BALANCE, INSUFFICIENT_BALANCE_MESSAGE, status)
    if status < 400:
        status = {'authentication_error': 401, 'permission_error': 403,
                  'rate_limit_error': 429, 'overloaded_error': 503}.get(kind, 500)
    return http_error(status)


def endpoint(base, suffix, *, version=None):
    base = normalize_url(base)
    if base.endswith(suffix):
        return base
    if version and not base.endswith('/' + version):
        base += '/' + version
    return base + suffix


def decode(raw):
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError, UnicodeError):
        raise invalid() from None


class HTTP:
    def __init__(self, provider, key, timeout, client=None, cancel=None):
        self.provider, self.key, self.timeout, self.client = provider, key, timeout, client
        self.cancel, self.deadline = cancel, time.monotonic() + timeout

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise ModelCancelled()
        if time.monotonic() >= self.deadline:
            raise ModelError('MODEL_TIMEOUT', '模型请求超时，请稍后重试。')

    async def __aenter__(self):
        self.owned = not isinstance(self.client, httpx.AsyncClient)
        if self.owned:
            self.client = httpx.AsyncClient(
                transport=self.client, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(self.timeout, connect=min(5., self.timeout)))
        return self

    async def __aexit__(self, *_):
        self.key = ''
        if self.owned:
            await self.client.aclose()

    @asynccontextmanager
    async def response(self, url, body, headers=None):
        self.check()
        normalize_url(url)
        auth = ({'x-api-key': self.key, 'anthropic-version': '2023-06-01'}
                if self.provider in {'deepseek', 'anthropic', 'relay_anthropic'}
                else {'x-goog-api-key': self.key} if self.provider == 'gemini'
                else {'Authorization': 'Bearer ' + self.key})
        auth.update({'Content-Type': 'application/json'})
        auth.update(headers or {})
        try:
            async with self.client.stream(
                'POST', url, json=body, headers=auth, follow_redirects=False,
                timeout=httpx.Timeout(self.timeout, connect=min(5., self.timeout)),
            ) as response:
                self.check()
                if response.status_code != 200:
                    # Redirects are rejected before reading the body or credentials
                    # could be sent to a second host, even on a supplied client.
                    if 300 <= response.status_code < 400:
                        raise http_error(response.status_code)
                    raw = await self.read(response, limit=65536, truncate=True)
                    try:
                        data = json.loads(raw)
                    except (ValueError, UnicodeError):
                        data = None
                    raise vendor_error(self.provider, response.status_code, data)
                yield response
        finally:
            auth.clear()

    async def read(self, response, limit=MAX_BYTES, truncate=False):
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            self.check()
            if len(raw) + len(chunk) > limit:
                if truncate:
                    raw.extend(chunk[:limit - len(raw)])
                    break
                raise ModelError('MODEL_RESPONSE_TOO_LARGE', '模型回复过大，请缩小请求后重试。')
            raw.extend(chunk)
        return raw

    async def json(self, url, body, headers=None):
        async with self.response(url, body, headers) as response:
            data = decode(await self.read(response))
        if data.get('error') or (self.provider == 'qwen' and data.get('code')):
            raise vendor_error(self.provider, 200, data)
        return data

    async def events(self, url, body, headers=None):
        async with self.response(url, body, headers) as response:
            if 'text/event-stream' not in response.headers.get('content-type', '').lower():
                raise invalid()
            decoder = codecs.getincrementaldecoder('utf-8')()
            buffer, event, lines, size = '', '', [], 0
            async for chunk in response.aiter_bytes():
                self.check()
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ModelError('MODEL_RESPONSE_TOO_LARGE', '模型回复过大，请缩小请求后重试。')
                buffer += decoder.decode(chunk)
                while '\n' in buffer:
                    self.check()
                    line, buffer = buffer.split('\n', 1)
                    line = line.rstrip('\r')
                    if not line:
                        if lines:
                            value = '\n'.join(lines)
                            if value == '[DONE]':
                                yield event, None
                            else:
                                data = decode(value)
                                if event == 'error' or data.get('error'):
                                    raise vendor_error(self.provider, 200, data)
                                yield event, data
                        event, lines = '', []
                    elif line.startswith('event:'):
                        event = line[6:].lstrip(' ')
                    elif line.startswith('data:'):
                        lines.append(line[5:].lstrip(' '))
            buffer += decoder.decode(b'', final=True)
            # An unterminated data frame is a truncated stream, not completion.
            if buffer.strip() or lines:
                raise invalid()


async def guarded(factory, timeout, cancel):
    if cancel is not None and cancel.is_set():
        raise ModelCancelled()
    task = asyncio.create_task(factory())
    try:
        async with asyncio.timeout(timeout):
            while not task.done():
                if cancel is not None and cancel.is_set():
                    raise ModelCancelled()
                await asyncio.wait({task}, timeout=.02)
            if cancel is not None and cancel.is_set():
                raise ModelCancelled()
            return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
