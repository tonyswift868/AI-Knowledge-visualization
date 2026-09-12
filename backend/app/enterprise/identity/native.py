"""First-party native sessions. No OAuth password grant or password database.

An identity-owned verifier is mandatory. The shipped configuration leaves this
boundary unavailable until its mTLS bridge and single-process deployment pass QA.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .models import AuthenticatedPrincipal
from .oidc import IdentityError

PREFIX = '/api/enterprise/auth/native'
PUBLIC_PATHS = frozenset(PREFIX + '/' + name for name in ('login', 'refresh', 'logout'))
ACCESS_PREFIX, REFRESH_PREFIX = 'mfn1_a_', 'mfn1_r_'


class CredentialVerifier(Protocol):
    async def authenticate(self, username: str, password: str) -> AuthenticatedPrincipal: ...
    async def revalidate(self, principal: AuthenticatedPrincipal) -> AuthenticatedPrincipal: ...
    async def aclose(self) -> None: ...


def denied():
    return IdentityError('NATIVE_CREDENTIALS_INVALID', '账号或密码不正确', status=401)


def expired():
    return IdentityError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '登录已失效，请重新登录。', status=401)


class MtlsIdentityVerifier:
    """Controlled identity bridge, not Keycloak's token/admin/login-page API."""
    def __init__(self, endpoint, issuer, *, context=None, http_client=None):
        url = urlsplit(endpoint)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in (
                    '/mentorflow-native-identity/v1',
                    urlsplit(issuer).path.rstrip('/') + '/mentorflow-native-identity/v1')):
            raise ValueError('Exact HTTPS native identity bridge endpoint required')
        if http_client is None and (context is None or not context.check_hostname
                                    or context.verify_mode != ssl.CERT_REQUIRED):
            raise ValueError('Verified mTLS context required')
        self.endpoint, self.issuer = endpoint, issuer
        self.http = http_client or httpx.AsyncClient(verify=context, timeout=10, follow_redirects=False, trust_env=False)

    async def _verify(self, payload):
        try:
            async with asyncio.timeout(10):
                async with self.http.stream('POST', self.endpoint, json=payload) as response:
                    if response.status_code == 401:
                        raise denied()
                    if response.status_code != 200:
                        raise IdentityError('NATIVE_IDENTITY_UNAVAILABLE', '账号服务暂不可用。', status=503)
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 8192:
                            raise IdentityError('NATIVE_IDENTITY_INVALID', '账号服务响应无法验证。', status=503)
                        content.extend(chunk)
                    value = json.loads(content)
            # MFA/required actions are a hard stop, never silently bypassed.
            if value.get('outcome') == 'challenge_required':
                raise IdentityError('NATIVE_ADDITIONAL_VERIFICATION_REQUIRED', '此账号需要额外验证，请联系账号管理员。', status=403)
            if (set(value) != {'schema', 'outcome', 'issuer', 'subject', 'display_name', 'authentication_strength', 'verification_handle'}
                    or value['schema'] != 'mentorflow.native-identity.v1' or value['outcome'] != 'authenticated'
                    or value['issuer'] != self.issuer or value['authentication_strength'] != 'password_verified'
                    or not isinstance(value['subject'], str) or not value['subject']
                    or value['subject'] != value['subject'].strip()
                    or not isinstance(value['verification_handle'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['verification_handle'])):
                raise IdentityError('NATIVE_IDENTITY_INVALID', '账号服务响应无法验证。', status=503)
            # Integer-second issuance avoids datetime/float rounding appearing
            # fractionally in the future on Windows. Expiry is rounded down too.
            now = datetime.fromtimestamp(int(time.time()), timezone.utc)
            return AuthenticatedPrincipal(issuer=self.issuer, subject=value['subject'],
                display_name=value['display_name'], audiences=('mentorflow-api',),
                token_id=secrets.token_urlsafe(32), issued_at=now, expires_at=now + timedelta(minutes=5),
                authentication_time=now, authentication_strength='password_verified', client_id='mentorflow-desktop',
                identity_provider_session_id=value['verification_handle'])
        except IdentityError:
            raise
        except Exception:
            raise IdentityError('NATIVE_IDENTITY_UNAVAILABLE', '账号服务暂不可用。', status=503) from None
        finally:
            payload.clear()

    async def authenticate(self, username, password):
        return await self._verify({'operation': 'authenticate', 'username': username, 'password': password})

    async def revalidate(self, principal):
        result = await self._verify({'operation': 'revalidate', 'issuer': principal.issuer, 'subject': principal.subject,
                                    'verification_handle': principal.identity_provider_session_id})
        if (result.issuer, result.subject) != (principal.issuer, principal.subject):
            raise expired()
        if result.identity_provider_session_id != principal.identity_provider_session_id:
            raise expired()
        return result

    async def aclose(self):
        await self.http.aclose()


@dataclass(repr=False)
class _Family:
    principal: AuthenticatedPrincipal
    deadline: float
    access_hash: str = ''
    access_deadline: float = 0
    refresh_hash: str = ''
    used_refreshes: set[str] = field(default_factory=set)


class NativeSessions:
    """Bounded single-process opaque session store; restart fails closed.

    Only token hashes are retained server-side. Rotation is atomic; replay
    revokes the family. Multi-replica persistence is a separate deployment gate.
    """
    def __init__(self, verifier, issuer, *, clock=time.time, capacity=4096):
        self.verifier, self.issuer, self.clock, self.capacity = verifier, issuer, clock, capacity
        self.lock = threading.RLock()
        self.families = {}
        self.access = {}
        self.refreshes = {}
        self.attempts = {}

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def _drop(self, fid):
        row = self.families.pop(fid, None)
        if row:
            self.access.pop(row.access_hash, None)
            for token in row.used_refreshes | {row.refresh_hash}:
                self.refreshes.pop(token, None)

    def _prune(self):
        now = self.clock()
        for fid, row in list(self.families.items()):
            if row.deadline <= now:
                self._drop(fid)
        for key, (_, deadline) in list(self.attempts.items()):
            if deadline <= now:
                self.attempts.pop(key)

    def throttle(self, username, peer):
        with self.lock:
            self._prune()
            keys = [('account:' + self.digest(username.strip().casefold()), 5),
                    ('peer:' + self.digest(peer), 20), ('global', 100)]
            if len(self.attempts) + 2 > self.capacity:
                raise IdentityError('NATIVE_RATE_LIMITED', '登录尝试过于频繁，请稍后重试。', status=429)
            for key, limit in keys:
                count, deadline = self.attempts.get(key, (0, self.clock() + 60))
                if count >= limit:
                    raise IdentityError('NATIVE_RATE_LIMITED', '登录尝试过于频繁，请稍后重试。', status=429)
            for key, _ in keys:
                count, deadline = self.attempts.get(key, (0, self.clock() + 60))
                self.attempts[key] = (count + 1, deadline)

    def _principal(self, principal):
        if (not isinstance(principal, AuthenticatedPrincipal) or principal.issuer != self.issuer
                or principal.actor_kind.value != 'HUMAN' or principal.audiences != ('mentorflow-api',)
                or principal.client_id != 'mentorflow-desktop'
                or principal.authentication_strength != 'password_verified'
                or principal.issued_at.timestamp() > self.clock()
                or principal.not_before is not None and principal.not_before.timestamp() > self.clock()
                or principal.expires_at.timestamp() <= self.clock()):
            raise denied()

    def _rotate(self, fid, row):
        self.access.pop(row.access_hash, None)
        access = ACCESS_PREFIX + secrets.token_urlsafe(32)
        refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
        row.access_hash, row.refresh_hash = self.digest(access), self.digest(refresh)
        row.access_deadline = min(self.clock() + 300, row.deadline, row.principal.expires_at.timestamp())
        self.access[row.access_hash] = fid
        self.refreshes[row.refresh_hash] = fid
        return {'schema': 'mentorflow.native-session.v1', 'access_token': access, 'refresh_token': refresh,
                'expires_in': max(0, int(row.access_deadline - self.clock())),
                'identity': {'issuer': row.principal.issuer, 'subject': row.principal.subject,
                             'display_name': row.principal.display_name or 'MentorFlow'}}

    async def login(self, username, password, peer):
        self.throttle(username, peer)
        principal = await self.verifier.authenticate(username, password)
        self._principal(principal)
        with self.lock:
            self._prune()
            if len(self.families) >= self.capacity:
                raise IdentityError('NATIVE_SESSION_CAPACITY', '账号服务暂不可用。', status=503)
            fid = secrets.token_hex(32)
            row = _Family(principal, self.clock() + 8 * 3600)
            self.families[fid] = row
            return self._rotate(fid, row)

    def authenticate(self, token):
        if not token.startswith(ACCESS_PREFIX) or len(token) > 128:
            raise expired()
        with self.lock:
            self._prune()
            row = self.families.get(self.access.get(self.digest(token)))
            if row is None or row.access_deadline <= self.clock():
                raise expired()
            return row.principal

    async def refresh(self, token):
        if not token.startswith(REFRESH_PREFIX) or len(token) > 128:
            raise expired()
        digest = self.digest(token)
        with self.lock:
            self._prune()
            fid = self.refreshes.get(digest)
            row = self.families.get(fid)
            if row is None:
                raise expired()
            if digest != row.refresh_hash or len(row.used_refreshes) >= 128:
                self._drop(fid)
                raise expired()
            # Claim before await: concurrent/replayed refresh cannot mint a pair.
            row.used_refreshes.add(digest)
            row.refresh_hash = ''
        try:
            principal = await self.verifier.revalidate(row.principal)
            self._principal(principal)
            if (principal.issuer, principal.subject) != (row.principal.issuer, row.principal.subject):
                raise expired()
        except Exception:
            with self.lock:
                self._drop(fid)
            raise expired() from None
        with self.lock:
            if self.families.get(fid) is not row or row.deadline <= self.clock():
                self._drop(fid)
                raise expired()
            # Refresh is not a fresh password/MFA event.
            row.principal = principal.model_copy(update={'authentication_time': row.principal.authentication_time})
            return self._rotate(fid, row)

    def logout(self, token):
        with self.lock:
            fid = self.refreshes.get(self.digest(token))
            if fid:
                self._drop(fid)

    async def aclose(self):
        with self.lock:
            for fid in list(self.families):
                self._drop(fid)
            self.attempts.clear()
        await self.verifier.aclose()


def from_env(env, issuer):
    endpoint = env.get('MENTORFLOW_NATIVE_IDENTITY_ENDPOINT', '')
    if not endpoint:
        return None
    if env.get('MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS') != '1' or env.get('WEB_CONCURRENCY', '1') != '1':
        raise ValueError('Native sessions require explicitly approved single-process deployment')
    context = ssl.create_default_context(cafile=env['MENTORFLOW_NATIVE_IDENTITY_CA_FILE'])
    context.load_cert_chain(env['MENTORFLOW_NATIVE_IDENTITY_CERT_FILE'], env['MENTORFLOW_NATIVE_IDENTITY_KEY_FILE'])
    return NativeSessions(MtlsIdentityVerifier(endpoint, issuer, context=context), issuer)


router = APIRouter()


async def _operation(request, operation):
    body, raw = None, bytearray()
    try:
        if request.url.scheme != 'https' or request.headers.get('Origin') or request.cookies or request.headers.get('Authorization'):
            raise IdentityError('NATIVE_TRANSPORT_REQUIRED', '需要安全的原生账号连接。', status=403)
        runtime = getattr(request.app.state, 'enterprise_identity_runtime', None)
        service = getattr(runtime, 'native_sessions', None)
        if service is None:
            raise IdentityError('NATIVE_AUTH_NOT_READY', '账号服务尚未准备好，请稍后重试。', status=503)
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise ValueError()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise ValueError()
        try:
            body = json.loads(raw)
        finally:
            raw[:] = b'\0' * len(raw)
        expected = {'username', 'password'} if operation == 'login' else {'refresh_token'}
        if not isinstance(body, dict) or set(body) != expected or any(not isinstance(v, str) or not v for v in body.values()):
            raise ValueError()
        try:
            if operation == 'login':
                if not body['username'].strip() or len(body['username']) > 320 or len(body['password']) > 1024:
                    raise ValueError()
                result = await service.login(body['username'].strip(), body['password'], request.client.host if request.client else 'unknown')
            elif operation == 'refresh':
                result = await service.refresh(body['refresh_token'])
            else:
                service.logout(body['refresh_token'])
                result = {'logged_out': True}
        finally:
            body.clear()
        return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
    except IdentityError as exc:
        return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status, headers={'Cache-Control': 'no-store'})
    except ValueError:
        return JSONResponse({'code': 'NATIVE_REQUEST_INVALID', 'message': '登录请求无法处理。'}, status_code=400, headers={'Cache-Control': 'no-store'})
    except Exception:
        return JSONResponse({'code': 'NATIVE_AUTH_UNAVAILABLE', 'message': '账号服务暂不可用。'}, status_code=503, headers={'Cache-Control': 'no-store'})
    finally:
        raw[:] = b'\0' * len(raw)
        if isinstance(body, dict):
            body.clear()


@router.post(PREFIX + '/login')
async def login(request: Request):
    return await _operation(request, 'login')


@router.post(PREFIX + '/refresh')
async def refresh(request: Request):
    return await _operation(request, 'refresh')


@router.post(PREFIX + '/logout')
async def logout(request: Request):
    return await _operation(request, 'logout')
