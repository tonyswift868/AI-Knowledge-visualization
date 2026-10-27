"""Default native credential client. Legacy OIDC UI is never invoked here.

Reuses only account/workspace validation from the existing API client. Credentials
travel to the configured controlled API over verified TLS, never the token endpoint.
"""
from __future__ import annotations

import asyncio
import re
from uuid import uuid4
from urllib.parse import urlsplit

import httpx

from .account_identity import AccountIdentity, issuer_hash
from .account_protocol import (AccountConfig, AccountStateClient,
    AccountError, AccountLoginResult, ValidatedTokenIdentity, _attach_request_diagnostic)

PREFIX = '/api/enterprise/auth/native'
ACCESS_PREFIX, REFRESH_PREFIX = 'mfn1_a_', 'mfn1_r_'
MESSAGES = {
    'NATIVE_CREDENTIALS_INVALID': '账号或密码不正确',
    'NATIVE_RATE_LIMITED': '登录尝试过于频繁，请稍后重试。',
    'NATIVE_AUTH_NOT_READY': '账号服务尚未准备好，请稍后重试。',
    'NATIVE_ADDITIONAL_VERIFICATION_REQUIRED': '此账号需要额外验证，请联系账号管理员。',
    'ENTERPRISE_SESSION_REAUTH_REQUIRED': '登录已失效，请重新登录。',
    'NATIVE_NETWORK_UNAVAILABLE': '暂时无法连接账号服务，请检查网络后重试。',
}


class NativeCredentialClient(AccountStateClient):
    @staticmethod
    def _validate_config(config):
        url = urlsplit(config.api_base_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise AccountError('NATIVE_TLS_REQUIRED', '账号服务需要安全连接。')
        if config.client_id != 'mentorflow-desktop' or config.audience != 'mentorflow-api':
            raise AccountError('NATIVE_CLIENT_INVALID', '账号服务配置无法验证。')

    def __init__(self, config, *, http_client=None):
        self._validate_config(config)
        super().__init__(config, http_client=http_client)

    async def _session_request(self, operation, payload):
        client_request_id = uuid4().hex
        response = None
        try:
            response = await self._http.post(self.config.api_base_url + PREFIX + '/' + operation,
                json=payload, headers={'Accept': 'application/json',
                                       'X-Request-ID': client_request_id})
            if response.status_code != 200:
                code = ('NATIVE_CREDENTIALS_INVALID' if operation == 'login'
                        else 'ENTERPRISE_SESSION_REAUTH_REQUIRED') if response.status_code == 401 else ''
                if not code and response.status_code < 500 and len(response.content) <= 8192:
                    try:
                        candidate = response.json().get('code')
                        code = candidate if candidate in MESSAGES and candidate != 'ENTERPRISE_SESSION_REAUTH_REQUIRED' else ''
                    except Exception:
                        pass
                error = AccountError(code or 'NATIVE_AUTH_UNAVAILABLE',
                    MESSAGES.get(code, '账号服务暂不可用，请稍后重试。'))
                raise _attach_request_diagnostic(error, 'HTTPStatusError', client_request_id, response)
            if len(response.content) > 8192:
                raise ValueError()
            return response.json()
        except AccountError as exc:
            if not hasattr(exc, 'client_request_id'):
                _attach_request_diagnostic(exc, 'AccountError', client_request_id, response)
            raise
        except httpx.HTTPError as exc:
            error = AccountError('NATIVE_NETWORK_UNAVAILABLE', MESSAGES['NATIVE_NETWORK_UNAVAILABLE'])
            _attach_request_diagnostic(error, type(exc).__name__, client_request_id,
                                       getattr(exc, 'response', None))
            raise error from None
        except Exception as exc:
            error = AccountError('NATIVE_RESPONSE_INVALID', '账号服务响应无法验证。')
            _attach_request_diagnostic(error, type(exc).__name__, client_request_id, response)
            raise error from None
        finally:
            payload.clear()

    def _validate_session(self, value):
        try:
            if not isinstance(value, dict) or set(value) != {'schema', 'access_token', 'refresh_token', 'expires_in', 'identity'}:
                raise ValueError()
            identity = value['identity']
            if (value['schema'] != 'mentorflow.native-session.v1'
                    or type(value['expires_in']) is not int or not 0 < value['expires_in'] <= 300
                    or set(identity) != {'issuer', 'subject', 'display_name'}
                    or identity['issuer'] != self.config.canonical_identity_issuer
                    or not isinstance(identity['subject'], str) or not 1 <= len(identity['subject']) <= 300
                    or identity['subject'] != identity['subject'].strip()
                    or not isinstance(identity['display_name'], str) or len(identity['display_name']) > 300):
                raise ValueError()
            for field, prefix in (('access_token', ACCESS_PREFIX), ('refresh_token', REFRESH_PREFIX)):
                if not re.fullmatch(prefix + r'[A-Za-z0-9_-]{43}', value[field]):
                    raise ValueError()
            account = AccountIdentity(identity['issuer'], identity['subject'], identity['display_name'])
            # AccountIdentity validates the canonical namespace.
            account.profile()
            return ValidatedTokenIdentity(account.issuer, account.subject, account.display_name)
        except Exception:
            raise AccountError('NATIVE_RESPONSE_INVALID', '账号服务响应无法验证。') from None

    async def login(self, username, password, *, on_authenticated=None):
        if not username.strip() or not password or len(username) > 320 or len(password) > 1024:
            raise AccountError('NATIVE_INPUT_REQUIRED', '请输入账号和密码。')
        try:
            value = await self._session_request('login', {'username': username.strip(), 'password': password})
        finally:
            password = None
        validated = self._validate_session(value)
        if on_authenticated is not None:
            on_authenticated()
        identity, state = await self._server_state(validated, value['access_token'])
        return AccountLoginResult(identity, value['access_token'], value['refresh_token'], state)

    async def restore(self, profile, credentials, *, refresh_session=True):
        if profile.issuer_hash != issuer_hash(self.config.canonical_identity_issuer):
            raise AccountError('ENTERPRISE_ACCOUNT_ISSUER_CHANGED', '登录服务已变更，请重新登录。')
        _, refresh = credentials.load_oauth_tokens(profile)
        value = await self._session_request('refresh', {'refresh_token': refresh})
        validated = self._validate_session(value)
        identity = AccountIdentity(validated.issuer, validated.subject, validated.display_name)
        restored = identity.profile()
        if (restored.issuer_hash, restored.subject_hash) != (profile.issuer_hash, profile.subject_hash):
            raise AccountError('ENTERPRISE_ACCOUNT_RESTORE_MISMATCH', '登录会话与账号不匹配。')
        # Persist rotation before network/bootstrap work; a retry must not reuse it.
        credentials.store_oauth_tokens(identity, access_token=value['access_token'], refresh_token=value['refresh_token'])
        identity, state = await self._server_state(validated, value['access_token'])
        return AccountLoginResult(identity, value['access_token'], value['refresh_token'], state, restored=True)

    async def end_session(self, refresh_token):
        if not refresh_token:
            return False
        value = await self._session_request('logout', {'refresh_token': refresh_token})
        return value == {'logged_out': True}


def login_native_account_from_env(username, password, *, on_authenticated=None):
    async def run():
        client = NativeCredentialClient(AccountConfig.from_env())
        try:
            return await client.login(username, password, on_authenticated=on_authenticated)
        finally:
            await client.aclose()
    return asyncio.run(run())


def restore_native_account_from_env(profile, credentials):
    async def run():
        client = NativeCredentialClient(AccountConfig.from_env())
        try:
            return await client.restore(profile, credentials)
        finally:
            await client.aclose()
    return asyncio.run(run())
