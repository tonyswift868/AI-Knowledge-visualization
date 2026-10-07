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
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .models import AuthenticatedPrincipal
from .oidc import IdentityError
from .continuity import canonical_principal_issuer, from_env as continuity_from_env

PREFIX = '/api/enterprise/auth/native'
# Session probes authenticate their bearer directly, without requiring a
# selected workspace, just as login/refresh own their authentication boundary.
PUBLIC_PATHS = frozenset(PREFIX + '/' + name for name in ('login', 'refresh', 'logout', 'session'))
ACCESS_PREFIX, REFRESH_PREFIX = 'mfn1_a_', 'mfn1_r_'


class CredentialVerifier(Protocol):
    async def authenticate(self, username: str, password: str) -> AuthenticatedPrincipal: ...
    async def revalidate(self, principal: AuthenticatedPrincipal) -> AuthenticatedPrincipal: ...
    async def aclose(self) -> None: ...


def denied():
    return IdentityError('NATIVE_CREDENTIALS_INVALID', '账号或密码不正确', status=401)


def expired():
    return IdentityError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '登录已失效，请重新登录。', status=401)


def superseded():
    return IdentityError('NATIVE_SESSION_SUPERSEDED', '账号已在另一台设备登录', status=401)


def unavailable():
    return IdentityError('NATIVE_AUTH_UNAVAILABLE', '账号服务暂不可用，请稍后重试。', status=503)


NATIVE_FAMILIES = sa.Table('native_session_families', sa.MetaData(),
    sa.Column('family_id', sa.String(64), primary_key=True),
    sa.Column('issuer', sa.String(500)), sa.Column('subject', sa.String(300)),
    sa.Column('refresh_digest', sa.String(64)), sa.Column('used_digests', sa.JSON()),
    sa.Column('principal', sa.JSON()), sa.Column('account_binding', sa.String(64)),
    sa.Column('deadline', sa.Float()), sa.Column('revoked_reason', sa.String(64)))


class MtlsIdentityVerifier:
    """Controlled identity bridge, not Keycloak's token/admin/login-page API."""
    def __init__(self, endpoint, issuer, *, context=None, http_client=None,
                 external_issuer=None, continuity=None):
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
        self.external_issuer = external_issuer or issuer
        self.continuity = continuity
        self._email_context = context
        self._email_lock = threading.Lock()
        self._email_sample = (False, 'EMAIL_AUTHORITY_UNAVAILABLE')
        self._email_deadline = 0.0
        self.http = http_client or httpx.AsyncClient(verify=context, timeout=10, follow_redirects=False, trust_env=False)

    def email_transport_health(self):
        """Synchronous HealthRegistry adapter, coalesced/cached for 60 seconds.

        Observe Keycloak's SMTP egress over the same fixed mTLS endpoint; Backend
        never receives SMTP credentials or connects to the relay for this probe.
        """
        with self._email_lock:
            if time.monotonic() < self._email_deadline:
                return self._email_sample
            sample = (False, 'EMAIL_TRANSPORT_UNAVAILABLE')
            try:
                if (self._email_context is None or not self._email_context.check_hostname
                        or self._email_context.verify_mode != ssl.CERT_REQUIRED):
                    raise ValueError()
                with httpx.Client(verify=self._email_context, timeout=5, follow_redirects=False, trust_env=False) as client:
                    with client.stream('POST', self.endpoint, json={'operation':'email-transport-health'}) as response:
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            if len(raw)+len(chunk)>512: raise ValueError()
                            raw.extend(chunk)
                        from .reset_completion import _object
                        value = json.loads(raw, object_pairs_hook=_object)
                        codes = {'EMAIL_AUTHORITY_NOT_CONFIGURED', 'EMAIL_CONFIGURATION_UNAVAILABLE',
                                 'EMAIL_TRANSPORT_UNAVAILABLE'}
                        if response.status_code != 200 or set(value) != {'status','code'}: raise ValueError()
                        if value == {'status':'READY','code':'EMAIL_TLS_AUTH_READY'}:
                            sample = (True, 'EMAIL_TLS_AUTH_READY')
                        elif value['status']=='UNAVAILABLE' and value['code'] in codes:
                            sample = (False, value['code'])
                        else: raise ValueError()
            except Exception:
                pass  # No provider payloads, exceptions or credentials in diagnostics.
            self._email_sample = sample
            self._email_deadline = time.monotonic()+60
            return sample

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
                    or value['issuer'] != self.external_issuer or value['authentication_strength'] != 'password_verified'
                    or not isinstance(value['subject'], str) or not value['subject']
                    or value['subject'] != value['subject'].strip()
                    or not isinstance(value['verification_handle'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['verification_handle'])):
                raise IdentityError('NATIVE_IDENTITY_INVALID', '账号服务响应无法验证。', status=503)
            canonical_principal_issuer(value['issuer'], value['subject'], self.issuer, self.continuity)
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
        result = await self._verify({'operation': 'revalidate', 'issuer': self.external_issuer, 'subject': principal.subject,
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
    account_binding: str = ''


class NativeSessions:
    """Durable families; short-lived access tokens remain process-local.

    Password verification occurs only at login. Refresh checks account state
    before committing rotation; outages leave the current digest usable.
    The no-engine mode is the isolated in-memory test adapter only.
    """
    def __init__(self, verifier, issuer, *, clock=time.time, capacity=4096,
                 superseded_capacity=16384, superseded_ttl=8 * 3600,
                 engine=None, session_ttl_seconds=0, account_check=None):
        if superseded_capacity < 1 or not 0 < superseded_ttl <= 8 * 3600:
            raise ValueError('superseded reasons require a positive capacity and at most eight hours TTL')
        self.verifier, self.issuer, self.clock, self.capacity = verifier, issuer, clock, capacity
        self.lock = threading.RLock()
        self.families = {}
        self.access = {}
        self.refreshes = {}
        self.attempts = {}
        self.revocation_epoch = 0
        self.pending_logins = {}
        self.superseded = OrderedDict()
        self.superseded_capacity, self.superseded_ttl = superseded_capacity, superseded_ttl
        self.engine, self.session_ttl_seconds, self.account_check = engine, session_ttl_seconds, account_check
        self.inflight = set()

    def _sync(self):
        """Load committed family state; never persist access tokens or SPI handles."""
        if self.engine is None:
            return
        try:
            with self.engine.connect() as conn:
                records = conn.execute(sa.select(NATIVE_FAMILIES).where(
                    NATIVE_FAMILIES.c.issuer == self.issuer)).mappings().all()
            families, refreshes, access = {}, {}, {}
            for record in records:
                fid = record['family_id']
                hashes = set(record['used_digests']) | {record['refresh_digest']}
                if record['revoked_reason']:
                    if record['revoked_reason'] == 'NATIVE_SESSION_SUPERSEDED':
                        for digest in hashes:
                            self.superseded[digest] = (record['revoked_reason'], self.clock() + self.superseded_ttl)
                    continue
                old = self.families.get(fid)
                row = _Family(AuthenticatedPrincipal.model_validate(record['principal']),
                    record['deadline'] if record['deadline'] is not None else float('inf'),
                    refresh_hash=record['refresh_digest'], used_refreshes=set(record['used_digests']),
                    account_binding=record['account_binding'])
                if old and old.refresh_hash == row.refresh_hash:
                    row.access_hash, row.access_deadline = old.access_hash, old.access_deadline
                    if row.access_hash:
                        access[row.access_hash] = fid
                families[fid] = row
                refreshes.update((digest, fid) for digest in hashes)
            while len(self.superseded) > self.superseded_capacity:
                self.superseded.popitem(last=False)
            self.families, self.refreshes, self.access = families, refreshes, access
        except Exception:
            raise unavailable() from None

    def _persist(self, fid, row, *, expected=None):
        if self.engine is None:
            return
        principal = row.principal.model_copy(update={'identity_provider_session_id': None})
        values = dict(issuer=self.issuer, subject=principal.subject,
            refresh_digest=row.refresh_hash, used_digests=sorted(row.used_refreshes),
            principal=principal.model_dump(mode='json'), account_binding=row.account_binding,
            deadline=None if row.deadline == float('inf') else row.deadline, revoked_reason=None)
        try:
            with self.engine.begin() as conn:
                if expected is None:
                    conn.execute(sa.update(NATIVE_FAMILIES).where(
                        NATIVE_FAMILIES.c.issuer == self.issuer,
                        NATIVE_FAMILIES.c.subject == principal.subject,
                        NATIVE_FAMILIES.c.revoked_reason.is_(None)).values(revoked_reason='NATIVE_SESSION_SUPERSEDED'))
                    conn.execute(sa.insert(NATIVE_FAMILIES).values(family_id=fid, **values))
                else:
                    result = conn.execute(sa.update(NATIVE_FAMILIES).where(
                        NATIVE_FAMILIES.c.family_id == fid,
                        NATIVE_FAMILIES.c.refresh_digest == expected,
                        NATIVE_FAMILIES.c.revoked_reason.is_(None)).values(**values))
                    if result.rowcount != 1:
                        raise expired()
        except IdentityError:
            raise
        except Exception:
            raise unavailable() from None

    async def _check_account(self, principal):
        # Production installs the existing administrative account/closure check.
        if self.account_check is None:
            if self.engine is not None:
                raise unavailable()
            return ''
        try:
            return await self.account_check(principal)
        except IdentityError:
            raise
        except Exception:
            raise unavailable() from None

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def _drop(self, fid, *, superseding=False, persisted=False):
        if self.engine is not None and not persisted:
            try:
                with self.engine.begin() as conn:
                    conn.execute(sa.update(NATIVE_FAMILIES).where(
                        NATIVE_FAMILIES.c.family_id == fid).values(
                            revoked_reason='NATIVE_SESSION_SUPERSEDED' if superseding else 'REVOKED'))
            except Exception:
                raise unavailable() from None
        row = self.families.pop(fid, None)
        if row:
            if superseding:
                deadline = min(row.deadline, self.clock() + self.superseded_ttl)
                # Retain current tokens last so small reason caches evict old
                # rotated refresh digests first. Never retain bearer plaintext.
                hashes = [*sorted(row.used_refreshes), row.refresh_hash, row.access_hash]
                for digest in hashes:
                    if digest:
                        self.superseded.pop(digest, None)
                        self.superseded[digest] = ('NATIVE_SESSION_SUPERSEDED', deadline)
                        while len(self.superseded) > self.superseded_capacity:
                            self.superseded.popitem(last=False)
            self.access.pop(row.access_hash, None)
            for token in row.used_refreshes | {row.refresh_hash}:
                self.refreshes.pop(token, None)

    def _prune(self):
        now = self.clock()
        for digest, (_, deadline) in list(self.superseded.items()):
            if deadline <= now:
                self.superseded.pop(digest)
        for fid, row in list(self.families.items()):
            if row.deadline <= now:
                self._drop(fid)
        for key, (_, deadline) in list(self.attempts.items()):
            if deadline <= now:
                self.attempts.pop(key)

    def _raise_if_superseded(self, digest):
        if digest in self.superseded:
            raise superseded()

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

    def _rotate(self, fid, row, *, expected=None):
        old_access = row.access_hash
        row = replace(row, used_refreshes=set(row.used_refreshes))
        access = ACCESS_PREFIX + secrets.token_urlsafe(32)
        refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
        row.access_hash, row.refresh_hash = self.digest(access), self.digest(refresh)
        row.access_deadline = min(self.clock() + 300, row.deadline, row.principal.expires_at.timestamp())
        self._persist(fid, row, expected=expected)
        self.access.pop(old_access, None)
        self.families[fid] = row
        self.access[row.access_hash] = fid
        self.refreshes[row.refresh_hash] = fid
        return {'schema': 'mentorflow.native-session.v1', 'access_token': access, 'refresh_token': refresh,
                'expires_in': max(0, int(row.access_deadline - self.clock())),
                'identity': {'issuer': row.principal.issuer, 'subject': row.principal.subject,
                             'display_name': row.principal.display_name or 'MentorFlow'}}

    async def login(self, username, password, peer):
        self.throttle(username, peer)
        ticket = object()
        with self.lock:
            if len(self.pending_logins) >= self.capacity:
                raise IdentityError('NATIVE_SESSION_CAPACITY', '账号服务暂不可用。', status=503)
            revoked_subjects = self.pending_logins[ticket] = set()
        try:
            principal = await self.verifier.authenticate(username, password)
            self._principal(principal)
            binding = await self._check_account(principal)
            with self.lock:
                if principal.subject in revoked_subjects:
                    raise expired()
                self._sync()
                self._prune()
                previous = [fid for fid, row in self.families.items() if row.principal.subject == principal.subject]
                if len(self.families) - len(previous) >= self.capacity:
                    raise IdentityError('NATIVE_SESSION_CAPACITY', '账号服务暂不可用。', status=503)
                fid = secrets.token_hex(32)
                deadline = self.clock() + self.session_ttl_seconds if self.session_ttl_seconds else float('inf')
                row = _Family(principal, deadline, account_binding=binding)
                result = self._rotate(fid, row)
                for previous_fid in previous:
                    self._drop(previous_fid, superseding=True, persisted=True)
                return result
        finally:
            with self.lock:
                self.pending_logins.pop(ticket, None)

    def authenticate(self, token):
        if not token.startswith(ACCESS_PREFIX) or len(token) > 128:
            raise expired()
        with self.lock:
            self._sync()
            self._prune()
            digest = self.digest(token)
            self._raise_if_superseded(digest)
            row = self.families.get(self.access.get(digest))
            if row is None or row.access_deadline <= self.clock():
                raise expired()
            return row.principal

    async def refresh(self, token):
        if not token.startswith(REFRESH_PREFIX) or len(token) > 128:
            raise expired()
        digest = self.digest(token)
        with self.lock:
            self._sync()
            self._prune()
            self._raise_if_superseded(digest)
            fid = self.refreshes.get(digest)
            row = self.families.get(fid)
            if row is None:
                raise expired()
            if digest != row.refresh_hash or digest in self.inflight:
                self._drop(fid)
                raise expired()
            self.inflight.add(digest)
        try:
            try:
                binding = await self._check_account(row.principal)
                if binding != row.account_binding:
                    raise expired()
            except IdentityError as exc:
                with self.lock:
                    self._sync()
                    self._raise_if_superseded(digest)
                    if exc.status == 401:
                        self._drop(fid)
                raise
            with self.lock:
                self._sync()
                self._prune()
                self._raise_if_superseded(digest)
                current = self.families.get(fid)
                if current is None:
                    raise expired()
                if current.refresh_hash != digest:
                    self._drop(fid)
                    raise expired()
                now = datetime.fromtimestamp(int(self.clock()), timezone.utc)
                principal = current.principal.model_copy(update={'issued_at': now,
                    'expires_at': now + timedelta(minutes=5), 'token_id': secrets.token_urlsafe(32)})
                updated = replace(current, principal=principal, used_refreshes=current.used_refreshes | {digest})
                return self._rotate(fid, updated, expected=digest)
        finally:
            with self.lock:
                self.inflight.discard(digest)

    def logout(self, token):
        with self.lock:
            self._sync()
            fid = self.refreshes.get(self.digest(token))
            if fid:
                self._drop(fid)

    def revoke_subject(self, subject):
        """A committed credential reset also fences in-flight pre-reset logins."""
        with self.lock:
            self.revocation_epoch += 1
            for revoked_subjects in self.pending_logins.values():
                revoked_subjects.add(subject)
            self._sync()
            for fid, row in list(self.families.items()):
                if row.principal.subject == subject:
                    self._drop(fid)

    async def aclose(self):
        with self.lock:
            self.families.clear()
            self.access.clear()
            self.refreshes.clear()
            self.attempts.clear()
            self.superseded.clear()
        await self.verifier.aclose()


def from_env(env, issuer, *, engine=None, session_ttl_seconds=0):
    endpoint = env.get('MENTORFLOW_NATIVE_IDENTITY_ENDPOINT', '')
    if not endpoint:
        return None
    if env.get('MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS') != '1' or env.get('WEB_CONCURRENCY', '1') != '1':
        raise ValueError('Native sessions require explicitly approved single-process deployment')
    if engine is None:
        raise ValueError('Native sessions require the backend database')
    context = ssl.create_default_context(cafile=env['MENTORFLOW_NATIVE_IDENTITY_CA_FILE'])
    context.load_cert_chain(env['MENTORFLOW_NATIVE_IDENTITY_CERT_FILE'], env['MENTORFLOW_NATIVE_IDENTITY_KEY_FILE'])
    return NativeSessions(MtlsIdentityVerifier(endpoint, issuer, context=context,
        external_issuer=env.get('MENTORFLOW_NATIVE_EXTERNAL_ISSUER') or issuer,
        continuity=continuity_from_env(env)), issuer, engine=engine, session_ttl_seconds=session_ttl_seconds)


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
            if operation != 'logout' and getattr(service, 'account_check', None) is None:
                from refactor.server.account.closure_api import guard_native_result
                await guard_native_result(request.app.state.enterprise_identity_runtime, result)
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


@router.get(PREFIX + '/session')
async def session(request: Request):
    try:
        if request.url.scheme != 'https' or request.headers.get('Origin') or request.cookies:
            raise IdentityError('NATIVE_TRANSPORT_REQUIRED', '需要安全的原生账号连接。', status=403)
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('Bearer ') or authorization.count(' ') != 1:
            raise expired()
        runtime = getattr(request.app.state, 'enterprise_identity_runtime', None)
        service = getattr(runtime, 'native_sessions', None)
        if service is None:
            raise IdentityError('NATIVE_AUTH_NOT_READY', '账号服务尚未准备好，请稍后重试。', status=503)
        principal = service.authenticate(authorization[7:])
        from refactor.server.account.closure_api import assert_principal_open
        await assert_principal_open(runtime, principal)
        return JSONResponse({'state': 'active'}, headers={'Cache-Control': 'no-store'})
    except IdentityError as exc:
        return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status,
                            headers={'Cache-Control': 'no-store'})
    except Exception:
        return JSONResponse({'code': 'NATIVE_AUTH_UNAVAILABLE', 'message': '账号服务暂不可用。'},
                            status_code=503, headers={'Cache-Control': 'no-store'})
