"""Read-only reset observations. No email, credentials, login or capability enabling."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .oidc import IdentityError

PREFIX = '/api/enterprise/auth/reset'
PUBLIC_PATHS = frozenset((PREFIX + '/watch', PREFIX + '/status'))
SCHEMA = 'mentorflow.reset-completion.v1'
STATUSES = frozenset(('PENDING', 'COMPLETED', 'EXPIRED', 'INVALID'))
router = APIRouter()
_lock = threading.Lock()
_rates = {}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _throttle(peer, operation):
    now = time.monotonic()
    with _lock:
        for key, (_, end) in list(_rates.items()):
            if end <= now:
                _rates.pop(key)
        keys = [('global', 600), (operation + ':' + hashlib.sha256(peer.encode()).hexdigest(), 20 if operation == 'watch' else 180)]
        if len(_rates) > 4096:
            raise IdentityError('RESET_RATE_LIMITED', '请稍后重试。', status=429)
        for key, limit in keys:
            count, end = _rates.get(key, (0, now + 60))
            if count >= limit:
                raise IdentityError('RESET_RATE_LIMITED', '请稍后重试。', status=429)
        for key, _ in keys:
            count, end = _rates.get(key, (0, now + 60))
            _rates[key] = (count + 1, end)


async def identity_observation(verifier, operation, value):
    """Use the configured certificate-pinned identity bridge, never a client URL."""
    payload = {'operation': 'reset-' + operation, 'state' if operation == 'watch' else 'transaction_id': value}
    try:
        async with asyncio.timeout(10):
            async with verifier.http.stream('POST', verifier.endpoint, json=payload) as response:
                if response.status_code != 200:
                    raise ValueError()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 2048:
                        raise ValueError()
                    raw.extend(chunk)
                result = json.loads(raw, object_pairs_hook=_object)
        if (not isinstance(result, dict) or set(result) != {'schema', 'transaction_id', 'status'}
                or result['schema'] != SCHEMA or result['status'] not in STATUSES
                or not isinstance(result['transaction_id'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', result['transaction_id'])
                or operation == 'watch' and result['status'] != 'PENDING'
                or operation == 'status' and result['transaction_id'] != value):
            raise ValueError()
        return result
    except Exception:
        raise IdentityError('RESET_OBSERVATION_UNAVAILABLE', '暂时无法确认密码重置状态，请稍后重试。', status=503) from None
    finally:
        payload.clear()


async def _operation(request, operation):
    raw, body = bytearray(), None
    try:
        if request.url.scheme != 'https' or request.headers.get('Origin') or request.cookies or request.headers.get('Authorization'):
            raise IdentityError('RESET_TRANSPORT_REQUIRED', '需要安全的原生账号连接。', status=403)
        service = getattr(getattr(request.app.state, 'enterprise_identity_runtime', None), 'native_sessions', None)
        if service is None:
            raise IdentityError('RESET_OBSERVATION_UNAVAILABLE', '账号服务暂不可用。', status=503)
        _throttle(request.client.host if request.client else 'unknown', operation)
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise ValueError()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024:
                raise ValueError()
            raw.extend(chunk)
        body = json.loads(raw, object_pairs_hook=_object)
        field = 'oidc_state' if operation == 'watch' else 'transaction_id'
        pattern = r'[A-Za-z0-9_-]{43,128}' if operation == 'watch' else r'[A-Za-z0-9_-]{43}'
        if not isinstance(body, dict) or set(body) != {field} or not isinstance(body[field], str) or not re.fullmatch(pattern, body[field]):
            raise ValueError()
        result = await identity_observation(service.verifier, operation, body[field])
        return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
    except IdentityError as error:
        return JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status, headers={'Cache-Control': 'no-store'})
    except Exception:
        return JSONResponse({'code': 'RESET_REQUEST_INVALID', 'message': '重置状态请求无法处理。'}, status_code=400, headers={'Cache-Control': 'no-store'})
    finally:
        raw[:] = b'\0' * len(raw)
        if isinstance(body, dict):
            body.clear()


@router.post(PREFIX + '/watch')
async def watch(request: Request):
    return await _operation(request, 'watch')


@router.post(PREFIX + '/status')
async def status(request: Request):
    return await _operation(request, 'status')
