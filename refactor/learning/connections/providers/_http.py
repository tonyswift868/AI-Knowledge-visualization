"""Bounded, cancellable HTTP/SSE with failure-only metadata; no redirects/retries."""
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
from ._failure_evidence import FailureEvidence


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
    if kind == 'overloaded_error':
        return http_error(503)
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
    def __init__(self, provider, key, timeout, client=None, cancel=None, accounting=None, evidence=None):
        self.provider, self.key, self.timeout, self.client = provider, key, timeout, client
        self.cancel, self.deadline = cancel, time.monotonic() + timeout
        self.accounting = accounting
        self.evidence = evidence or FailureEvidence(provider, key)

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
                # Connection establishment gets the same deadline as the whole
                # request.  A separate five-second cap made slow networks
                # fail before account/sync requests using this timeout budget.
                timeout=httpx.Timeout(self.timeout, connect=self.timeout))
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
            self.evidence.request(body)
            self.evidence.stage = 'connecting'
            if self.accounting:
                self.accounting.start(body)
            async with self.client.stream(
                'POST', url, json=body, headers=auth, follow_redirects=False,
                timeout=httpx.Timeout(self.timeout, connect=self.timeout),
            ) as response:
                self.evidence.status = response.status_code
                self.evidence.content_type = self.evidence.clean(response.headers.get('content-type'))
                self.evidence.stage = 'response_received'
                if self.accounting:
                    self.accounting.begin()
                    if response.is_stream_consumed:
                        self.observe_bytes(response.content)
                if response.status_code != 200:
                    if self.accounting:
                        self.accounting.first_event()  # A definitive HTTP error is not a queued response.
                    self.evidence.stage = 'http_error_response'
                    # Redirects are rejected before reading the body or credentials
                    # could be sent to a second host, even on a supplied client.
                    if 300 <= response.status_code < 400:
                        raise http_error(response.status_code)
                    raw = await self.read(response, limit=65536, truncate=True)
                    try:
                        data = json.loads(raw)
                    except (ValueError, UnicodeError):
                        data = None
                    if self.accounting and isinstance(data, dict):
                        self.accounting.snapshot(data)
                    self.evidence.error(data)
                    raise vendor_error(self.provider, response.status_code, data)
                yield response
        finally:
            auth.clear()

    async def read(self, response, limit=MAX_BYTES, truncate=False):
        raw = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > limit:
                    if truncate:
                        raw.extend(chunk[:limit - len(raw)])
                        break
                    raise ModelError('MODEL_RESPONSE_TOO_LARGE', '模型回复过大，请缩小请求后重试。')
                raw.extend(chunk)
                if raw.strip() and self.accounting and response.status_code == 200:
                    self.accounting.first_event()
                self.check()
        finally:
            # A cancel/timeout/read failure can race the final body chunk. Meter
            # JSON already received before propagating it; never read more here.
            self.observe_bytes(raw)
        return raw

    def observe_bytes(self, raw):
        if not self.accounting or len(raw) > MAX_BYTES:
            return
        try:
            data = decode(raw)
        except ModelError:
            return
        self.accounting.snapshot(data)

    async def json(self, url, body, headers=None):
        async with self.response(url, body, headers) as response:
            self.evidence.stage = 'decoding_json_response'
            data = decode(await self.read(response))
            self.evidence.error(data)
            if self.accounting:
                self.accounting.snapshot(data)
            self.check()
        if data.get('error') or (self.provider == 'qwen' and data.get('code')):
            raise vendor_error(self.provider, 200, data)
        return data

    async def events(self, url, body, headers=None):
        async with self.response(url, body, headers) as response:
            if 'text/event-stream' not in response.headers.get('content-type', '').lower():
                # A JSON error/fallback body can still contain billable meters.
                # Read it within the same size/deadline limits, then reject the
                # protocol mismatch rather than silently accepting a reply.
                raw = await self.read(response)
                try:
                    self.evidence.error(decode(raw))
                except ModelError:
                    pass
                self.evidence.stage = 'expected_event_stream'
                raise invalid()
            self.evidence.stage = 'reading_event_stream'
            decoder = codecs.getincrementaldecoder('utf-8')()
            buffer, event, lines, size = '', '', [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ModelError('MODEL_RESPONSE_TOO_LARGE', '模型回复过大，请缩小请求后重试。')
                buffer += decoder.decode(chunk)
                ready = []
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.rstrip('\r')
                    if not line:
                        if lines:
                            value = '\n'.join(lines)
                            if value == '[DONE]':
                                data = None
                            else:
                                self.evidence.stage = 'decoding_sse_event'
                                try:
                                    data = decode(value)
                                except ModelError:
                                    self.evidence.event(event, None)
                                    raise
                            self.evidence.event(event, data)
                            if self.accounting and data is not None and event != 'ping' and data.get('type') != 'ping':
                                self.accounting.first_event()
                            if self.accounting:
                                self.accounting.event(event, data)
                            ready.append((event, data))
                        event, lines = '', []
                    elif line.startswith('event:'):
                        event = line[6:].lstrip(' ')
                    elif line.startswith('data:'):
                        lines.append(line[5:].lstrip(' '))
                # Account every complete frame already received in this chunk
                # before callbacks/cancellation can reject its answer.
                self.evidence.stage = 'reading_event_stream'
                self.check()
                for event_name, data in ready:
                    self.check()
                    if event_name == 'error' or (data is not None and data.get('error')):
                        raise vendor_error(self.provider, 200, data)
                    yield event_name, data
            buffer += decoder.decode(b'', final=True)
            # An unterminated data frame is a truncated stream, not completion.
            if buffer.strip() or lines:
                self.evidence.event(event, None)
                if self.accounting:
                    pending = lines + [line[5:].lstrip(' ') for line in buffer.splitlines() if line.startswith('data:')]
                    try:
                        data = decode('\n'.join(pending))
                    except ModelError:
                        pass
                    else:
                        self.accounting.event(event, data)
                self.evidence.stage = 'unterminated_sse_frame'
                raise invalid()


async def guarded(factory, timeout, cancel, accounting=None):
    if cancel is not None and cancel.is_set():
        raise ModelCancelled()
    task = asyncio.create_task(factory())
    try:
        async with asyncio.timeout(timeout):
            while not task.done():
                if cancel is not None and cancel.is_set():
                    raise ModelCancelled()
                if accounting is not None and accounting.first_expired():
                    raise ModelError('MODEL_FIRST_EVENT_TIMEOUT', '模型暂时无法使用，请稍后再试。')
                await asyncio.wait({task}, timeout=.02)
            if cancel is not None and cancel.is_set():
                raise ModelCancelled()
            return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
