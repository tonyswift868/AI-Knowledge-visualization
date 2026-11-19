"""Account-only contracts and server identity validation over the existing API."""
from __future__ import annotations

import json
import os
import secrets
import re
from uuid import uuid4
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from .account_identity import AccountIdentity, canonicalize_issuer

PUBLIC_ISSUER = 'https://auth.clannad.win/realms/mentorflow'
CANONICAL_ISSUER = 'https://localhost:18080/realms/mentorflow'


class AccountError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _attach_request_diagnostic(error, error_type, client_request_id, response=None):
    """Attach bounded correlation metadata without retaining request/response text."""
    error.diagnostic_error_type = error_type
    error.client_request_id = client_request_id
    value = response.headers.get('X-Request-ID', '') if response is not None else ''
    error.response_request_id = (value if isinstance(value, str)
                                 and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value) else '')
    return error


@dataclass(frozen=True, slots=True)
class AccountConfig:
    issuer: str
    client_id: str
    audience: str
    api_base_url: str
    organization_id: str = ''
    workspace_id: str = ''
    # Keep account calls on the same 20-second wall as the sync transport.
    # A slow connect must not fail before the already-working sync path.
    request_timeout_seconds: float = 20.0
    identity_issuer: str | None = None

    def __post_init__(self):
        try:
            issuer = canonicalize_issuer(self.issuer)
            canonical = canonicalize_issuer(self.identity_issuer) if self.identity_issuer else issuer
            # The public connection address keeps the deployed account namespace.
            if issuer == PUBLIC_ISSUER and canonical != CANONICAL_ISSUER:
                raise ValueError()
            if canonical != issuer and (issuer, canonical) != (PUBLIC_ISSUER, CANONICAL_ISSUER):
                raise ValueError()
            url = urlsplit(self.api_base_url)
            if (url.scheme != 'https' or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment
                    or any(ord(c) < 33 for c in self.api_base_url)):
                raise ValueError()
            url.port  # Validate malformed ports before any credentials are sent.
            if self.client_id != 'mentorflow-desktop' or self.audience != 'mentorflow-api':
                raise ValueError()
            if not 1 <= float(self.request_timeout_seconds) <= 60:
                raise ValueError()
            if self.workspace_id and not self.organization_id:
                raise ValueError()
            for hint in (self.workspace_id, self.organization_id):
                if not isinstance(hint, str) or len(hint) > 240 or any(ord(c) < 32 for c in hint):
                    raise ValueError()
            object.__setattr__(self, 'issuer', issuer)
            object.__setattr__(self, 'identity_issuer', canonical)
            object.__setattr__(self, 'api_base_url', self.api_base_url.rstrip('/'))
        except (ValueError, TypeError, AttributeError):
            raise AccountError('ENTERPRISE_OIDC_CONFIG_INVALID', '账号服务配置无法验证。') from None

    @property
    def canonical_identity_issuer(self):
        return self.identity_issuer

    @classmethod
    def from_env(cls, source=None):
        env = os.environ if source is None else source
        return cls(
            issuer=env.get('MENTORFLOW_DESKTOP_OIDC_ISSUER', ''),
            client_id=env.get('MENTORFLOW_DESKTOP_OIDC_CLIENT_ID', ''),
            audience=env.get('MENTORFLOW_DESKTOP_OIDC_AUDIENCE', ''),
            api_base_url=env.get('MENTORFLOW_ENTERPRISE_API_BASE_URL', ''),
            identity_issuer=env.get('MENTORFLOW_DESKTOP_IDENTITY_ISSUER') or None,
            organization_id=env.get('MENTORFLOW_DESKTOP_ORGANIZATION_ID', '').strip(),
            workspace_id=env.get('MENTORFLOW_DESKTOP_WORKSPACE_ID', '').strip(),
            request_timeout_seconds=env.get('MENTORFLOW_DESKTOP_OIDC_HTTP_TIMEOUT_SECONDS') or 20,
        )


@dataclass(frozen=True, slots=True)
class ValidatedTokenIdentity:
    issuer: str
    subject: str
    display_name: str


@dataclass(frozen=True, slots=True)
class AccountLoginResult:
    identity: AccountIdentity = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    server_state: Mapping[str, Any] = field(repr=False)
    restored: bool = False

    @property
    def sync_cursor(self):
        context = self.server_state.get('context')
        return str(context.get('context_version') or '') if isinstance(context, Mapping) else ''


def _json_object(response, label):
    """Retain bounded, duplicate-key rejecting server response parsing."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    try:
        if len(response.content) > 1_048_576:
            raise ValueError()
        value = json.loads(response.content.decode('utf-8'), object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise AccountError('ENTERPRISE_ACCOUNT_STATE_INVALID', f'{label}响应无法验证。') from None


class AccountStateClient:
    def __init__(self, config, *, http_client=None):
        self.config = config
        self._http = http_client or httpx.AsyncClient(
            timeout=float(config.request_timeout_seconds), follow_redirects=False,
        )
        self._owns_http = http_client is None

    async def aclose(self):
        if self._owns_http:
            await self._http.aclose()

    async def _state_request(self, path, access_token, *, selected=None, initialize=False):
        client_request_id = uuid4().hex
        response = None
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
        headers['X-Request-ID'] = client_request_id
        if selected is not None:
            headers['X-MentorFlow-Organization-ID'] = selected['organization_id']
            headers['X-MentorFlow-Workspace-ID'] = selected['id']
        try:
            response = await self._http.request(
                'POST' if initialize else 'GET', self.config.api_base_url + path,
                headers=headers, **({'json': {}} if initialize else {}), follow_redirects=False,
            )
            if response.status_code == 401:
                error = AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '登录已失效或账号无权访问，请重新登录。')
                raise _attach_request_diagnostic(error, 'HTTPStatusError', client_request_id, response)
            response.raise_for_status()
        except AccountError as exc:
            if not hasattr(exc, 'client_request_id'):
                _attach_request_diagnostic(exc, 'AccountError', client_request_id, response)
            raise
        except httpx.HTTPError as exc:
            error = AccountError('ENTERPRISE_ACCOUNT_STATE_UNAVAILABLE', '暂时无法确认账号身份，请稍后重试。')
            _attach_request_diagnostic(error, type(exc).__name__, client_request_id,
                                       getattr(exc, 'response', None) or response)
            raise error from None
        return _json_object(response, '账号服务')

    async def _discover_workspace(self, access_token):
        """Resolve membership required by /auth/me, without starting learning.

        The existing backend requires a tenant for its role response. Only an
        account with no discoverable workspace uses its existing initialization
        endpoint. PROFILE_REQUIRED and other workspaces' readiness do not gate
        the account home; /auth/me remains the authority for the selected scope.
        """
        path = '/api/enterprise/account/workspaces'

        async def discover(initialize=False):
            payload = await self._state_request(path + ('/initialize' if initialize else ''),
                                                access_token, initialize=initialize)
            rows = payload.get('workspaces')
            if not isinstance(rows, list) or type(payload.get('initialization_required')) is not bool:
                raise AccountError('ENTERPRISE_WORKSPACE_RESPONSE_INVALID', '账号空间响应无法验证。')
            seen = set()
            for row in rows:
                if (not isinstance(row, dict) or any(
                    not isinstance(row.get(key), str) or not row[key].strip()
                    or len(row[key]) > 240 or any(ord(c) < 32 for c in row[key])
                    for key in ('id', 'organization_id'))
                    or row.get('status') != 'active'
                    or row.get('initialization_status') not in {'READY', 'PROFILE_REQUIRED'}
                    or row['id'] in seen):
                    raise AccountError('ENTERPRISE_WORKSPACE_RESPONSE_INVALID', '账号空间响应无法验证。')
                seen.add(row['id'])
            default = payload.get('default_workspace_id')
            if default is not None and (not isinstance(default, str) or default not in seen):
                raise AccountError('ENTERPRISE_WORKSPACE_RESPONSE_INVALID', '账号空间响应无法验证。')
            return payload

        payload = await discover()
        if not payload['workspaces']:
            payload = await discover(initialize=True)
        rows = payload['workspaces']
        if not rows:
            raise AccountError('ENTERPRISE_WORKSPACE_INITIALIZATION_REQUIRED', '账号空间尚未准备好，请稍后重试。')
        selected = next((row for row in rows if row['id'] == self.config.workspace_id
                         and row['organization_id'] == self.config.organization_id), None)
        return selected or next((row for row in rows if row['id'] == payload.get('default_workspace_id')), rows[0])

    async def _server_state(self, validated, access_token):
        if validated.issuer != self.config.canonical_identity_issuer:
            raise AccountError('ENTERPRISE_OIDC_ISSUER_INVALID', '登录身份来源不匹配。')
        selected = await self._discover_workspace(access_token)
        state = await self._state_request('/api/enterprise/auth/me', access_token, selected=selected)
        account, context = state.get('account'), state.get('context')
        if state.get('authenticated') is not True or not isinstance(account, dict) or not isinstance(context, dict):
            raise AccountError('ENTERPRISE_ACCOUNT_STATE_INVALID', '账号身份响应无法验证。')
        expected = AccountIdentity(self.config.canonical_identity_issuer, validated.subject, '')
        subject = account.get('subject_id')
        if (not isinstance(subject, str) or not secrets.compare_digest(subject.encode('utf-8'), validated.subject.encode('utf-8'))
                or account.get('account_id') != expected.account_id
                or context.get('organization_id') != selected['organization_id']
                or context.get('workspace_id') != selected['id']):
            raise AccountError('ENTERPRISE_ACCOUNT_STATE_MISMATCH', '服务端身份或账号空间与登录账号不匹配。')
        for key in ('display_name', 'preferred_username'):
            if account.get(key) is not None and (not isinstance(account[key], str) or len(account[key]) > 320):
                raise AccountError('ENTERPRISE_ACCOUNT_STATE_INVALID', '账号名称响应无法验证。')
        display = (account.get('display_name') or '').strip() or (account.get('preferred_username') or '').strip() or validated.display_name
        return AccountIdentity(expected.issuer, expected.subject, display,
                               selected['organization_id'], selected['id']), state
