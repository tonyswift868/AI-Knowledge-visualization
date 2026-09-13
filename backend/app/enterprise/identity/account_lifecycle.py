"""Private Keycloak 26.7.3 administration port; deliberately no public routes.

The caller supplies an existing least-privilege administrative token resolver.
This module neither authenticates an administrator nor mints action tokens. It
must not be exposed until email acceptance, throttling and execute-actions reset
completion have been qualified. Release capabilities remain independently off.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx


class LifecycleError(RuntimeError):
    """Bounded outcome; never retain HTTP responses, credentials or provider text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
                 transport: httpx.AsyncBaseTransport | None = None):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('Exact HTTPS Keycloak administration origin required')
        self.base = origin.rstrip('/') + '/admin/realms/' + _identifier(realm)
        self.resolve_token = token_resolver
        self.http = httpx.AsyncClient(timeout=10, follow_redirects=False,
                                      trust_env=False, transport=transport)

    async def aclose(self):
        await self.http.aclose()

    async def _call(self, method, path, *, payload=None, params=None, expected=204):
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
                    if response.status_code == 409:
                        raise LifecycleError('ACCOUNT_ALREADY_IN_USE')
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
                or not isinstance(password, str) or not 8 <= len(password) <= 128
                or not isinstance(email, str) or len(email) > 254
                or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
            raise LifecycleError('REGISTRATION_INPUT_INVALID')
        credential = {'type': 'password', 'value': password, 'temporary': False}
        payload = {'username': username, 'email': email, 'enabled': True,
                   'emailVerified': False, 'requiredActions': ['VERIFY_EMAIL'],
                   'credentials': [credential]}
        try:
            _, location = await self._call('POST', '/users', payload=payload, expected=201)
            # Do not follow a returned Location or treat it as another authority.
            prefix = self.base + '/users/'
            if not isinstance(location, str) or not location.startswith(prefix):
                raise LifecycleError('CREATED_ACCOUNT_RECONCILIATION_REQUIRED')
            return _identifier(location[len(prefix):])
        finally:
            credential.clear()
            payload.clear()

    async def send_verification(self, subject: str):
        await self._call('PUT', '/users/' + _identifier(subject) + '/send-verify-email',
                         params={'lifespan': 600})

    async def send_password_reset(self, subject: str):
        """Uses official execute-actions, NOT the reset-credentials token handler.

        This is a private subject-based primitive, not the anonymous forgot API.
        Existing OIDC reset completion cannot attest this operation yet.
        """
        await self._call('PUT', '/users/' + _identifier(subject) + '/execute-actions-email',
                         payload=['UPDATE_PASSWORD'], params={'lifespan': 600})

    async def find_username(self, username: str) -> str | None:
        """Private reconciliation only; never return this result to anonymous UI."""
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
