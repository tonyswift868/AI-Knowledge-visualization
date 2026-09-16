"""Session-owned synchronous transport for the existing structured sync client.

No sync schema, snapshot interpretation, cache contents or presentation here.
Consumers publish envelopes through their epoch gate, including deferred commits.
"""
from __future__ import annotations

import copy
import os
import re
import ssl
import threading
import time
from dataclasses import dataclass

import httpx

_gate = threading.RLock()
_active = None
_epoch = 0
PREFIX = '/api/enterprise/sync/v1'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# The server bounds full snapshots at 128 MiB of entity data. Match the
# existing 160 MiB product-sync IPC envelope budget, including JSON framing.
MAX_SNAPSHOT_RESPONSE_BYTES = 160 * 1024 * 1024


class RehydrationTransportError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)  # Never include tokens, request objects or server bodies.


@dataclass(frozen=True)
class BoundAccountContext:
    account_id: str
    subject: str
    organization_id: str
    workspace_id: str


def _context(identity, state):
    try:
        account, context = state['account'], state['context']
        if (state['authenticated'] is not True or account['account_id'] != identity.account_id
                or account['subject_id'] != identity.subject
                or context['organization_id'] != identity.organization_id
                or context['workspace_id'] != identity.workspace_id
                or not identity.organization_id or not identity.workspace_id):
            raise ValueError()
        return BoundAccountContext(account['account_id'], account['subject_id'],
                                   context['organization_id'], context['workspace_id'])
    except (KeyError, TypeError, ValueError):
        raise RehydrationTransportError('UNAUTHENTICATED') from None


def invalidate_active(profile=None):
    """Synchronous fence, before executor drains or remote logout. Returns private rotation token."""
    with _gate:
        if _active is not None and (profile is None or _active.context.account_id == profile.account_id):
            token = _active._refresh
            _active.invalidate()
            return token
    return None


class ResultEnvelope:
    def __init__(self, session, epoch, payload=None, *, status='SERVER_CONFIRMED'):
        self._session, self._epoch = session, epoch
        self._payload = copy.deepcopy(payload)
        self._status = status

    @property
    def provenance(self):
        with _gate:
            if self._status != 'SERVER_CONFIRMED':
                return self._status
            return 'SERVER_CONFIRMED' if self._session._current(self._epoch) else 'SESSION_STALE'

    @property
    def context(self):
        return self._session.context

    @property
    def epoch(self):
        return self._epoch

    def publish(self, consumer):
        """Atomic bounded consumer commit. Deferred UI callbacks must call this again."""
        with _gate:
            if self.provenance != 'SERVER_CONFIRMED':
                raise RehydrationTransportError(self.provenance)
            return consumer(copy.deepcopy(self._payload), self.context)


class _Response:
    def __init__(self, session, epoch, response):
        self._session, self._epoch, self._response = session, epoch, response

    @property
    def status_code(self):
        with _gate:
            self._session._require(self._epoch)
            return self._response.status_code

    def raise_for_status(self):
        with _gate:
            self._session._require(self._epoch)
            if not self._response.is_success:
                raise RehydrationTransportError('TRANSPORT_UNAVAILABLE')

    def json(self, *, allow_error=False):
        with _gate:
            self._session._require(self._epoch)
            if not allow_error:
                self.raise_for_status()
            try:
                return self._response.json()
            except ValueError:
                raise RehydrationTransportError('TRANSPORT_UNAVAILABLE') from None


class AuthenticatedRehydrationTransport:
    """Minted from native session validation and authenticated /auth/me, never preferences."""
    def __init__(self, config, identity, state, pair, *, client=None, clock=time.monotonic, received_at=None):
        from .native_auth import NativeCredentialClient
        self._config = config
        NativeCredentialClient._validate_config(config)
        NativeCredentialClient._validate_session(self, pair)
        self._context = _context(identity, state)
        if pair['identity']['subject'] != identity.subject or pair['identity']['issuer'] != identity.issuer:
            raise RehydrationTransportError('UNAUTHENTICATED')
        self._identity = identity
        self._access, self._refresh = pair['access_token'], pair['refresh_token']
        self._clock = clock
        self._deadline = (clock() if received_at is None else received_at) + pair['expires_in']
        self._epoch = None
        self._invalid = False
        self._client = client
        self._operation = threading.RLock()
        self._rotation_writer = None

    @property
    def config(self):
        return self._config

    @property
    def context(self):
        return self._context

    def activate(self, *, rotation_writer=None):
        global _active, _epoch
        with _gate:
            if self._invalid or self._epoch is not None:
                raise RehydrationTransportError('SESSION_STALE')
            if _active is not None:
                _active.invalidate()
            _epoch += 1
            self._epoch, _active = _epoch, self
            self._rotation_writer = rotation_writer

    def _current(self, epoch):
        return (_active is self and not self._invalid and epoch == self._epoch
                and self._clock() < self._deadline)

    def _require(self, epoch=None, *, allow_expired=False):
        if self._epoch is None:
            raise RehydrationTransportError('UNAUTHENTICATED')
        if (_active is not self or self._invalid or epoch is not None and epoch != self._epoch
                or not allow_expired and self._clock() >= self._deadline):
            raise RehydrationTransportError('SESSION_STALE')

    def invalidate(self):
        global _active, _epoch
        with _gate:
            self._invalid = True
            self._access = self._refresh = ''
            self._rotation_writer = None
            if _active is self:
                _epoch += 1
                _active = None
        # Do not wait for an in-flight request on the UI/logout thread.
        if self._operation.acquire(blocking=False):
            try:
                if self._client is not None:
                    self._client.close()
                    self._client = None
            finally:
                self._operation.release()

    def _http(self):
        if self._client is None:
            self._client = httpx.Client(verify=ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE')),
                trust_env=False, timeout=self.config.request_timeout_seconds, follow_redirects=False)
        return self._client

    def _headers(self, access):
        return {'Accept':'application/json', 'Authorization':'Bearer '+access,
            'X-MentorFlow-Organization-ID':self.context.organization_id,
            'X-MentorFlow-Workspace-ID':self.context.workspace_id}

    def _renew(self):
        try:
            self._renew_once()
        except RehydrationTransportError:
            self.invalidate()
            raise
        except Exception:
            self.invalidate()
            raise RehydrationTransportError('UNAUTHENTICATED') from None

    def _renew_once(self):
        from .native_auth import NativeCredentialClient, PREFIX as NATIVE_PREFIX
        from .login_session import account_session_lock
        global _epoch
        with account_session_lock(self._identity.profile()):
            with _gate:
                self._require(allow_expired=True)
                epoch, refresh = self._epoch, self._refresh
            response = self._http().post(self.config.api_base_url+NATIVE_PREFIX+'/refresh',
                json={'refresh_token':refresh}, headers={'Accept':'application/json'})
            received_at = self._clock()
            if response.status_code != 200 or len(response.content) > 8192:
                self.invalidate()
                raise RehydrationTransportError('UNAUTHENTICATED')
            pair = response.json()
            validated = NativeCredentialClient._validate_session(self, pair)
            if (validated.issuer, validated.subject) != (self._identity.issuer, self._identity.subject):
                self.invalidate()
                raise RehydrationTransportError('UNAUTHENTICATED')
            response = self._http().get(self.config.api_base_url+'/api/enterprise/auth/me',
                                        headers=self._headers(pair['access_token']))
            if response.status_code != 200 or len(response.content) > 65536:
                self.invalidate()
                raise RehydrationTransportError('UNAUTHENTICATED')
            if _context(self._identity, response.json()) != self.context:
                self.invalidate()
                raise RehydrationTransportError('UNAUTHENTICATED')
            with _gate:
                self._require(epoch, allow_expired=True)
                if self._rotation_writer:
                    self._rotation_writer(self._identity, pair['access_token'], pair['refresh_token'])
                self._access, self._refresh = pair['access_token'], pair['refresh_token']
                self._deadline = received_at + pair['expires_in']
                _epoch += 1
                self._epoch = _epoch

    def _request(self, method, path, *, json=None):
        security = {
            '/api/enterprise/auth/lifecycle/email': ('GET', None),
            '/api/enterprise/auth/lifecycle/email-code/request': ('POST', {'email'}),
            '/api/enterprise/auth/lifecycle/email-code/verify': ('POST', {'transaction_id', 'code'}),
            '/api/enterprise/auth/lifecycle/change-password': ('POST', {'current_password', 'new_password'}),
        }
        if path in security:
            verb, fields = security[path]
            if method != verb or (fields is None and json is not None) or (fields is not None and (
                    not isinstance(json, dict) or set(json) != fields or any(not isinstance(v, str) for v in json.values()))):
                raise RehydrationTransportError('CONTEXT_OVERRIDE_REJECTED')
            return self._send(method, path, json=json, retry_unauthenticated=method == 'GET')
        allowed = method == 'GET' and path == PREFIX+'/snapshot' or (
            method in {'PUT','POST'} and re.fullmatch(re.escape(PREFIX)+
                r'/entities/(plan|progress|resource|task|calendar|review|learner_profile|learning_run|history|setting)/[A-Za-z0-9][A-Za-z0-9._:-]{0,159}'+
                ('/delete' if method == 'POST' else ''), path))
        expected = ({'request_id','expected_revision'} if method == 'POST'
                    else {'request_id','expected_revision','payload'})
        if not allowed or json is not None and (not isinstance(json, dict) or set(json) != expected):
            raise RehydrationTransportError('CONTEXT_OVERRIDE_REJECTED')
        return self._send(method, path, json=json)

    def _send(self, method, path, *, json=None, retry_unauthenticated=True):
        with self._operation:
            try:
                with _gate:
                    self._require(allow_expired=True)
                    expired = self._clock() >= self._deadline
                if expired:
                    self._renew()
                for attempt in range(2):
                    with _gate:
                        self._require()
                        epoch, access = self._epoch, self._access
                    response = self._http().request(method, self.config.api_base_url+path,
                        headers=self._headers(access), **({'json':json} if json is not None else {}))
                    with _gate:
                        self._require(epoch)
                    if response.status_code == 401 and attempt == 0 and retry_unauthenticated:
                        self._renew()
                        continue
                    if response.status_code in {401,403}:
                        self.invalidate()
                        raise RehydrationTransportError('UNAUTHENTICATED')
                    response_limit = (MAX_SNAPSHOT_RESPONSE_BYTES
                                      if method == 'GET' and path == PREFIX+'/snapshot'
                                      else MAX_RESPONSE_BYTES)
                    if len(response.content) > response_limit:
                        raise RehydrationTransportError('TRANSPORT_UNAVAILABLE')
                    return _Response(self, epoch, response)
            except RehydrationTransportError:
                raise
            except Exception:
                # Failed/ambiguous renewal must never reuse a claimed refresh token.
                self.invalidate()
                raise RehydrationTransportError('TRANSPORT_UNAVAILABLE') from None
            finally:
                if self._invalid and self._client is not None:
                    self._client.close()
                    self._client = None

    def get(self, path): return self._request('GET', path)
    def put(self, path, *, json): return self._request('PUT', path, json=json)
    def post(self, path, *, json): return self._request('POST', path, json=json)

    def account_metadata(self):
        return self._send('GET', '/api/enterprise/account/metadata')

    def admin_overview(self):
        """Use the already authenticated enterprise session; never expose its token."""
        response = self._send('GET', '/api/enterprise/auth/admin/overview')
        response.raise_for_status()
        return response.json()

    def put_account_progress(self, payload):
        return self._send('PUT', '/api/enterprise/account/metadata/progress', json=payload)

    def require_current(self):
        with _gate:
            self._require()

    def bootstrap(self):
        try:
            with _gate:
                self._require(allow_expired=True)
            # 04 owns this class and its snapshot/data semantics. No local fallback.
            from .structured_sync import StructuredSyncClient
            with self._operation:
                payload = StructuredSyncClient(self).rehydrate()
                with _gate:
                    self._require()
                    return ResultEnvelope(self, self._epoch, payload)
        except RehydrationTransportError as exc:
            return ResultEnvelope(self, self._epoch, status=exc.code)
        except Exception:
            return ResultEnvelope(self, self._epoch, status='TRANSPORT_UNAVAILABLE')
