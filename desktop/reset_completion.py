"""Canonical backend observation consumer. Never yields a login or OAuth token."""
from __future__ import annotations

import asyncio
import json
import re
import time

from .enterprise_oidc import EnterpriseOidcError
from .native_auth import NativeCredentialClient

SCHEMA = 'mentorflow.reset-completion.v1'
COMPLETED_CODE = 'ENTERPRISE_PASSWORD_RESET_COMPLETED'
COMPLETED_MESSAGE = '密码已重置，请使用新密码登录 MentorFlow。'
STATES = frozenset(('PENDING', 'COMPLETED', 'EXPIRED', 'INVALID'))


class ResetCompletionClient(NativeCredentialClient):
    async def _observation(self, operation, value):
        field = 'oidc_state' if operation == 'watch' else 'transaction_id'
        pattern = r'[A-Za-z0-9_-]{43,128}' if operation == 'watch' else r'[A-Za-z0-9_-]{43}'
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise EnterpriseOidcError('RESET_REQUEST_INVALID', '重置状态请求无法处理。')
        try:
            async with self._http.stream('POST', self.config.api_base_url + '/api/enterprise/auth/reset/' + operation,
                                         json={field: value}, headers={'Accept': 'application/json'}) as response:
                if response.status_code != 200:
                    raise ValueError()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 2048:
                        raise ValueError()
                    raw.extend(chunk)
                def strict(pairs):
                    result = dict(pairs)
                    if len(result) != len(pairs): raise ValueError()
                    return result
                result = json.loads(raw, object_pairs_hook=strict)
            if (not isinstance(result, dict) or set(result) != {'schema', 'transaction_id', 'status'}
                    or result['schema'] != SCHEMA or result['status'] not in STATES
                    or not isinstance(result['transaction_id'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', result['transaction_id'])
                    or operation == 'watch' and result['status'] != 'PENDING'
                    or operation == 'status' and result['transaction_id'] != value):
                raise ValueError()
            return result
        except Exception:
            raise EnterpriseOidcError('RESET_OBSERVATION_UNAVAILABLE', '暂时无法确认密码重置状态，请稍后重试。') from None

    async def begin(self, oidc_state):
        return (await self._observation('watch', oidc_state))['transaction_id']

    async def status(self, transaction_id):
        return (await self._observation('status', transaction_id))['status']

    async def wait_completed(self, transaction_id, cancelled, *, timeout=900):
        deadline = time.monotonic() + min(timeout, 900)
        while not cancelled.is_set() and time.monotonic() < deadline:
            state = await self.status(transaction_id)
            if cancelled.is_set(): break
            if state == 'COMPLETED':
                return  # No session creation, callback exchange, or credential persistence.
            if state in ('INVALID', 'EXPIRED'):
                raise EnterpriseOidcError('RESET_OBSERVATION_EXPIRED', '重置状态已失效，请重新发起密码找回。')
            await asyncio.sleep(1)
        raise EnterpriseOidcError('RESET_OBSERVATION_CANCELLED', '已返回登录页面。')
