"""Private Keycloak 26.7.3 administration port; deliberately no public routes.

The caller supplies an existing least-privilege administrative token resolver.
This module neither authenticates an administrator nor mints action tokens. It
must not be exposed until email acceptance, throttling and execute-actions reset
completion have been qualified. Release capabilities remain independently off.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx


class LifecycleError(RuntimeError):
    """Bounded outcome; never retain HTTP responses, credentials or provider text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_new_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise LifecycleError('PASSWORD_INPUT_INVALID')


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', value):
        raise LifecycleError('INVALID_PROVIDER_IDENTIFIER')
    return value


class KeycloakAccountAdmin:
    """Explicit operations with no automatic retries, redirects or email resend.

    A timeout after a mutation is ambiguous. Reconcile the exact disposable
    account administratively before retrying; never delete or recreate it here.
    An email 204 means provider submission only, not receipt or action completion.
    """

    def __init__(self, origin: str, realm: str,
                 token_resolver: Callable[[], Awaitable[str]], *,
                 transport: httpx.AsyncBaseTransport | None = None, verify=True):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('Exact HTTPS Keycloak administration origin required')
        self.base = origin.rstrip('/') + '/admin/realms/' + _identifier(realm)
        self.resolve_token = token_resolver
        if verify is False:
            raise ValueError('Verified TLS required')
        self.http = httpx.AsyncClient(verify=verify, timeout=10, follow_redirects=False,
                                      trust_env=False, transport=transport)

    async def aclose(self):
        await self.http.aclose()

    async def _call(self, method, path, *, payload=None, params=None, expected=204,
                    conflict_code=None, missing_code=None):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        raw = bytearray()
        try:
            async with asyncio.timeout(12):
                token = await self.resolve_token()
                if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
                    raise LifecycleError('ADMIN_AUTHORITY_UNAVAILABLE')
                headers['Authorization'] = 'Bearer ' + token
                token = None
                async with self.http.stream(method, self.base + path, headers=headers,
                                            json=payload, params=params) as response:
                    if response.status_code == 404 and missing_code:
                        raise LifecycleError(missing_code)
                    if response.status_code == 409:
                        # A Keycloak conflict is only an account-creation
                        # conflict when the caller explicitly opts into that
                        # interpretation.  Other operations (especially
                        # send-verify-email) must remain operation failures;
                        # never turn their 409 into a duplicate account claim.
                        raise LifecycleError(conflict_code or 'KEYCLOAK_OPERATION_REJECTED')
                    if response.status_code != expected:
                        # Never parse/echo provider errors (may contain user fields).
                        raise LifecycleError('KEYCLOAK_OPERATION_REJECTED')
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 65536:
                            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
                        raw.extend(chunk)
                    location = response.headers.get('location')
            return (json.loads(raw) if raw else None), location
        except LifecycleError:
            raise
        except Exception:
            raise LifecycleError('KEYCLOAK_OUTCOME_UNKNOWN') from None
        finally:
            headers.clear()
            raw[:] = b'\0' * len(raw)

    async def create_account(self, *, username: str, password: str, email: str) -> str:
        """Use UsersResource.createUser; uniqueness is decided by Keycloak's DB.

        Realm default-group/role policy must be qualified as LEARNER before use.
        No caller-supplied roles, verified flags or required actions are accepted.
        """
        if (not isinstance(username, str) or not username.strip() or len(username) > 255
                or username != username.strip() or any(ord(c) < 32 for c in username)
                or not isinstance(email, str) or len(email) > 254
                or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
            raise LifecycleError('REGISTRATION_INPUT_INVALID')
        try:
            validate_new_password(password)
        except LifecycleError:
            raise LifecycleError('REGISTRATION_INPUT_INVALID') from None
        credential = {'type': 'password', 'value': password, 'temporary': False}
        payload = {'username': username, 'email': email, 'enabled': True,
                   'emailVerified': False, 'requiredActions': ['VERIFY_EMAIL'],
                   'credentials': [credential]}
        try:
            _, location = await self._call('POST', '/users', payload=payload, expected=201,
                                           conflict_code='ACCOUNT_ALREADY_IN_USE')
            # Do not follow a returned Location or treat it as another authority.
            prefix = self.base + '/users/'
            if not isinstance(location, str) or not location.startswith(prefix):
                raise LifecycleError('CREATED_ACCOUNT_RECONCILIATION_REQUIRED')
            return _identifier(location[len(prefix):])
        except LifecycleError as exc:
            if exc.code == 'ACCOUNT_ALREADY_IN_USE':
                code = await self._registration_conflict(username, email)
                raise LifecycleError(code)
            raise
        finally:
            credential.clear()
            payload.clear()

    async def _registration_conflict(self, username: str, email: str):
        """Resolve a create conflict only after Keycloak reported HTTP 409.

        These exact lookups are deliberately absent from the normal
        registration path.  If either reconciliation call is unavailable or
        returns an unexpected result, the conflict cause stays unknown rather
        than inventing a username/email answer.  When both values are already
        present, username wins deterministically; a later retry after changing
        it can then expose the email conflict.
        """
        try:
            if await self.find_username(username):
                return 'USERNAME_ALREADY_IN_USE'
            if await self.find_account(email):
                return 'EMAIL_ALREADY_IN_USE'
        except Exception:
            return 'REGISTRATION_CONFLICT_UNRESOLVED'
        return 'REGISTRATION_CONFLICT_UNRESOLVED'

    async def send_verification(self, subject: str):
        await self._call('PUT', '/users/' + _identifier(subject) + '/send-verify-email',
                         params={'lifespan': 600})

    async def send_password_reset(self, subject: str):
        """Uses official execute-actions, NOT the reset-credentials token handler.

        This is a private subject-based primitive, not the anonymous forgot API.
        The candidate wiring observes the verified execute-actions required
        action through the existing after-commit completion mechanism.
        """
        await self._call('PUT', '/users/' + _identifier(subject) + '/execute-actions-email',
                         payload=['UPDATE_PASSWORD'], params={'lifespan': 600})

    async def change_password(self, subject: str, password: str):
        """Replace a credential only after the caller verified the current one."""
        validate_new_password(password)
        payload = {'type': 'password', 'value': password, 'temporary': False}
        try:
            await self._call('PUT', '/users/' + _identifier(subject) + '/reset-password',
                             payload=payload)
        finally:
            payload.clear()

    async def username_for_subject(self, subject: str) -> str:
        value, _ = await self._call('GET', '/users/' + _identifier(subject), expected=200)
        if (not isinstance(value, dict) or value.get('id') != subject
                or not isinstance(value.get('username'), str) or not value['username']
                or len(value['username']) > 255 or value.get('enabled') is not True):
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        return value['username']

    async def native_session_state(self, subject: str) -> str:
        """Account status without an expiring SPI verification handle.

        Keycloak 26.7.3 replaces password createdDate on password changes and
        retains it on rehash. Only public credential metadata is read here.
        HTTP 404/disabled are explicit revocations; admin/network failures are
        unavailable, never evidence that the user's session was revoked.
        """
        path = '/users/' + _identifier(subject)
        user, _ = await self._call('GET', path, expected=200,
                                   missing_code='NATIVE_ACCOUNT_REVOKED')
        if not isinstance(user, dict) or user.get('id') != subject:
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        if user.get('enabled') is False:
            raise LifecycleError('NATIVE_ACCOUNT_REVOKED')
        if user.get('enabled') is not True:
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        credentials, _ = await self._call('GET', path + '/credentials', expected=200,
                                          missing_code='NATIVE_ACCOUNT_REVOKED')
        if not isinstance(credentials, list) or any(not isinstance(c, dict) for c in credentials):
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        passwords = [c for c in credentials if isinstance(c, dict) and c.get('type') == 'password']
        if not passwords:
            raise LifecycleError('NATIVE_ACCOUNT_REVOKED')
        if (len(passwords) != 1 or not isinstance(passwords[0].get('id'), str)
                or type(passwords[0].get('createdDate')) is not int
                or type(user.get('notBefore', 0)) is not int):
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        binding = [passwords[0]['id'], passwords[0]['createdDate'], user.get('notBefore', 0)]
        return hashlib.sha256(json.dumps(binding).encode()).hexdigest()

    async def find_username(self, username: str) -> str | None:
        """Exact private lookup; public callers may expose only its boolean result."""
        if not isinstance(username, str) or not username or len(username) > 255:
            raise LifecycleError('REGISTRATION_INPUT_INVALID')
        value, _ = await self._call('GET', '/users',
            params={'username': username, 'exact': 'true', 'max': 2}, expected=200)
        if not isinstance(value, list) or len(value) > 1:
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        if not value:
            return None
        if (not isinstance(value[0], dict) or not isinstance(value[0].get('username'), str)
                or value[0]['username'].casefold() != username.casefold()):
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        return _identifier(value[0].get('id'))

    async def find_account(self, name: str) -> str | None:
        if '@' not in name:
            return await self.find_username(name)
        if not name or len(name) > 254:
            raise LifecycleError('REGISTRATION_INPUT_INVALID')
        value, _ = await self._call('GET', '/users', params={'email': name, 'exact': 'true', 'max': 2}, expected=200)
        if not isinstance(value, list) or len(value) > 1:
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        if not value:
            return None
        if (not isinstance(value[0], dict)
                or str(value[0].get('email', '')).casefold() != name.casefold()):
            raise LifecycleError('KEYCLOAK_RESPONSE_INVALID')
        return _identifier(value[0].get('id'))
