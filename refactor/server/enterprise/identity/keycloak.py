from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx

from .config import EnterpriseIdentityConfig, KeycloakOidcConfig
from .models import AuthenticatedPrincipal, EnterpriseSession
from .oidc import IdentityError, IdentityProviderPort, OidcIdentityAdapter


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_urlsafe(size: int = 48) -> str:
    return secrets.token_urlsafe(size)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    state: str
    nonce: str
    code_verifier: str = field(repr=False)
    redirect_uri: str
    created_at: datetime
    expires_at: datetime
    browser_binding_hash: str | None = field(default=None, repr=False)
    selected_organization_id: str | None = None
    selected_workspace_id: str | None = None
    return_path: str = "/"


@dataclass(frozen=True, slots=True)
class CompletedAuthorization:
    session: EnterpriseSession
    return_path: str


@dataclass(frozen=True, slots=True)
class _StoredSession:
    public: EnterpriseSession
    refresh_token: str | None = field(repr=False)


class InMemoryEnterpriseSessionStore:
    """Process-local opaque sessions; only the server-side refresh token is retained."""

    def __init__(
        self,
        *,
        max_pending: int = 4_096,
        max_sessions: int = 10_000,
        logout_replay_window_seconds: int = 300,
    ) -> None:
        if max_pending <= 0 or max_sessions <= 0 or logout_replay_window_seconds <= 0:
            raise ValueError("session-store capacities must be positive")
        self._max_pending = max_pending
        self._max_sessions = max_sessions
        self._logout_replay_window_seconds = logout_replay_window_seconds
        self._pending: dict[str, PendingAuthorization] = {}
        self._sessions: dict[str, _StoredSession] = {}
        self._logout_token_ids: dict[str, datetime] = {}
        self._lock = RLock()

    def add_pending(self, pending: PendingAuthorization) -> None:
        with self._lock:
            self._purge_pending(now=_now())
            if len(self._pending) >= self._max_pending:
                oldest = min(
                    self._pending,
                    key=lambda key: self._pending[key].created_at,
                )
                self._pending.pop(oldest, None)
            self._pending[pending.state] = pending

    def consume_pending(
        self,
        state: str,
        *,
        browser_binding: str | None = None,
        now: datetime | None = None,
    ) -> PendingAuthorization:
        current = now or _now()
        with self._lock:
            self._purge_pending(now=current)
            pending = self._pending.get(state)
            if pending is None or pending.expires_at <= current:
                raise IdentityError("OIDC_STATE_INVALID", "OIDC login state is invalid or expired")
            if pending.browser_binding_hash is not None:
                supplied_hash = (
                    hashlib.sha256(browser_binding.encode("utf-8")).hexdigest()
                    if browser_binding
                    else ""
                )
                if not secrets.compare_digest(supplied_hash, pending.browser_binding_hash):
                    raise IdentityError(
                        "OIDC_BROWSER_TRANSACTION_INVALID",
                        "OIDC browser transaction is invalid",
                    )
            self._pending.pop(state, None)
            return pending

    def _purge_pending(self, *, now: datetime) -> None:
        self._pending = {
            key: value for key, value in self._pending.items() if value.expires_at > now
        }

    def put(
        self,
        public: EnterpriseSession,
        *,
        refresh_token: str | None,
        expected_generation: int | None = None,
    ) -> None:
        with self._lock:
            current = _now()
            self._sessions = {
                key: value
                for key, value in self._sessions.items()
                if not value.public.revoked and (value.public.session_expires_at is None or value.public.session_expires_at > current)
            }
            existing = self._sessions.get(public.session_id)
            if expected_generation is not None and (
                existing is None
                or existing.public.revoked
                or existing.public.session_generation != expected_generation
            ):
                raise IdentityError(
                    "ENTERPRISE_SESSION_FENCED",
                    "Enterprise session changed during the operation",
                    status=409,
                )
            if existing is None and len(self._sessions) >= self._max_sessions:
                raise IdentityError(
                    "ENTERPRISE_SESSION_CAPACITY_EXCEEDED",
                    "Enterprise session capacity is exhausted",
                    status=503,
                )
            self._sessions[public.session_id] = _StoredSession(
                public=public,
                refresh_token=refresh_token,
            )

    def get(self, session_id: str, *, now: datetime | None = None) -> _StoredSession:
        current = now or _now()
        with self._lock:
            stored = self._sessions.get(session_id)
        if (
            stored is None
            or stored.public.revoked
            or (stored.public.session_expires_at is not None and stored.public.session_expires_at <= current)
        ):
            if stored is not None and not stored.public.revoked:
                self.revoke(session_id)
            raise IdentityError("ENTERPRISE_SESSION_INVALID", "Enterprise session is invalid")
        return stored

    def get_for_refresh(
        self,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> _StoredSession:
        current = now or _now()
        stored = self.get(session_id, now=current)
        if (
            stored.public.refresh_token_expires_at is not None
            and stored.public.refresh_token_expires_at <= current
        ):
            self.revoke(session_id)
            raise IdentityError(
                "ENTERPRISE_SESSION_REFRESH_EXPIRED",
                "Enterprise refresh authority has expired",
            )
        return stored

    def get_public(self, session_id: str, *, now: datetime | None = None) -> EnterpriseSession:
        return self.get(session_id, now=now).public

    def revoke(self, session_id: str) -> None:
        with self._lock:
            stored = self._sessions.get(session_id)
            if stored is not None:
                self._sessions[session_id] = replace(
                    stored,
                    refresh_token=None,
                    public=stored.public.model_copy(
                        update={
                            "revoked": True,
                            "session_generation": stored.public.session_generation + 1,
                        }
                    ),
                )

    def revoke_by_subject_or_sid(self, *, subject: str | None, sid: str | None) -> int:
        revoked = 0
        with self._lock:
            for key, stored in tuple(self._sessions.items()):
                principal = stored.public.principal
                if (sid and principal.identity_provider_session_id == sid) or (
                    subject and principal.subject == subject
                ):
                    if not stored.public.revoked:
                        self._sessions[key] = replace(
                            stored,
                            refresh_token=None,
                            public=stored.public.model_copy(
                                update={
                                    "revoked": True,
                                    "session_generation": stored.public.session_generation + 1,
                                }
                            ),
                        )
                        revoked += 1
        return revoked

    def register_logout_token(self, token_id: str, *, now: datetime | None = None) -> bool:
        current = now or _now()
        cutoff = current - timedelta(seconds=self._logout_replay_window_seconds)
        with self._lock:
            self._logout_token_ids = {
                key: timestamp
                for key, timestamp in self._logout_token_ids.items()
                if timestamp > cutoff
            }
            if token_id in self._logout_token_ids:
                return False
            if len(self._logout_token_ids) >= 4_096:
                oldest = min(self._logout_token_ids, key=self._logout_token_ids.__getitem__)
                self._logout_token_ids.pop(oldest, None)
            self._logout_token_ids[token_id] = current
        return True

    def preview_context(
        self,
        session_id: str,
        *,
        organization_id: str,
        workspace_id: str | None,
    ) -> EnterpriseSession:
        organization_id = organization_id.strip()
        workspace_id = workspace_id.strip() if workspace_id else None
        if not organization_id:
            raise IdentityError(
                "ENTERPRISE_ORGANIZATION_REQUIRED",
                "Enterprise organization selection is required",
                status=403,
            )
        stored = self.get(session_id)
        return EnterpriseSession.model_validate(
            {
                **stored.public.model_dump(),
                "selected_organization_id": organization_id,
                "selected_workspace_id": workspace_id,
            }
        )

    def rotate_context(
        self,
        session_id: str,
        *,
        organization_id: str,
        workspace_id: str | None,
        expected_context_version: int,
        expected_generation: int,
    ) -> EnterpriseSession:
        candidate = self.preview_context(
            session_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        with self._lock:
            stored = self._sessions.get(session_id)
            if (
                stored is None
                or stored.public.revoked
                or stored.public.context_version != expected_context_version
                or stored.public.session_generation != expected_generation
            ):
                raise IdentityError(
                    "ENTERPRISE_CONTEXT_FENCED",
                    "Enterprise context changed during the operation",
                    status=409,
                )
            new_session = candidate.model_copy(
                update={
                    "session_id": _token_urlsafe(),
                    "context_version": stored.public.context_version + 1,
                    "session_generation": 0,
                }
            )
            self._sessions.pop(session_id, None)
            self._sessions[new_session.session_id] = _StoredSession(
                public=new_session,
                refresh_token=stored.refresh_token,
            )
            return new_session


class KeycloakOidcClient(IdentityProviderPort):
    def __init__(
        self,
        identity_config: EnterpriseIdentityConfig,
        *,
        oidc: OidcIdentityAdapter | None = None,
        store: InMemoryEnterpriseSessionStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if identity_config.keycloak is None:
            raise ValueError("Keycloak client requires ENTERPRISE_SERVER configuration")
        self.identity_config = identity_config
        self.config: KeycloakOidcConfig = identity_config.keycloak
        self._http = http_client or httpx.AsyncClient(
            timeout=self.config.http_timeout_seconds,
            follow_redirects=False,
            verify=self.config.tls_verification,
        )
        self._owns_http = http_client is None
        self._owns_oidc = oidc is None
        self.oidc = oidc or OidcIdentityAdapter(self.config)
        self.store = store or InMemoryEnterpriseSessionStore(
            logout_replay_window_seconds=(
                self.config.logout_token_max_age_seconds
                + self.config.clock_skew_seconds
            )
        )
        self._session_mutation_locks = tuple(asyncio.Lock() for _ in range(64))

    def _session_mutation_lock(self, session_id: str) -> asyncio.Lock:
        digest = hashlib.sha256(session_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % len(self._session_mutation_locks)
        return self._session_mutation_locks[index]

    async def begin_login(
        self,
        *,
        browser_binding: str | None = None,
        organization_id: str | None = None,
        workspace_id: str | None = None,
        return_path: str = "/",
    ) -> str:
        organization_id = organization_id.strip() if organization_id else None
        workspace_id = workspace_id.strip() if workspace_id else None
        if workspace_id and not organization_id:
            raise IdentityError(
                "ENTERPRISE_ORGANIZATION_REQUIRED",
                "Enterprise workspace selection requires an organization",
                status=403,
            )
        state = _token_urlsafe()
        nonce = _token_urlsafe()
        verifier = _token_urlsafe(64)
        created = _now()
        self.store.add_pending(
            PendingAuthorization(
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                redirect_uri=self.config.redirect_uri,
                created_at=created,
                expires_at=created
                + timedelta(seconds=self.identity_config.pending_login_ttl_seconds),
                browser_binding_hash=(
                    hashlib.sha256(browser_binding.encode("utf-8")).hexdigest()
                    if browser_binding
                    else None
                ),
                selected_organization_id=organization_id,
                selected_workspace_id=workspace_id,
                return_path=return_path,
            )
        )
        query = urlencode(
            {
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.config.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{self.config.authorization_endpoint}?{query}"

    def _client_auth(self) -> dict[str, str]:
        values = {"client_id": self.config.client_id}
        if self.config.client_secret:
            values["client_secret"] = self.config.client_secret
        return values

    async def _post_form_json(
        self,
        url: str,
        data: Mapping[str, str],
    ) -> Mapping[str, Any]:
        try:
            response = await self._http.post(
                url,
                data=dict(data),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise IdentityError("KEYCLOAK_UNAVAILABLE", "Keycloak is unavailable", status=503) from exc
        if response.status_code >= 500:
            raise IdentityError("KEYCLOAK_UNAVAILABLE", "Keycloak is temporarily unavailable", status=503)
        if len(response.content) > 1_048_576:
            raise IdentityError("KEYCLOAK_RESPONSE_INVALID", "Keycloak response is too large", status=502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise IdentityError(
                "KEYCLOAK_RESPONSE_INVALID",
                "Keycloak returned an invalid response",
                status=502,
            ) from exc
        if response.status_code >= 400 or not isinstance(payload, Mapping):
            error = str(payload.get("error") if isinstance(payload, Mapping) else "")
            code = (
                "IDENTITY_DISABLED_OR_REVOKED"
                if error in {"invalid_grant", "invalid_token"}
                else "KEYCLOAK_REQUEST_REJECTED"
            )
            raise IdentityError(code, "Keycloak rejected the identity request", status=401)
        return payload

    async def _post_form_no_content(
        self,
        url: str,
        data: Mapping[str, str],
    ) -> None:
        try:
            response = await self._http.post(url, data=dict(data))
        except httpx.HTTPError as exc:
            raise IdentityError("KEYCLOAK_UNAVAILABLE", "Keycloak is unavailable", status=503) from exc
        if response.status_code >= 400:
            raise IdentityError(
                "KEYCLOAK_REQUEST_REJECTED",
                "Keycloak rejected the identity request",
                status=401,
            )

    @staticmethod
    def _required_token(payload: Mapping[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > 24_000:
            raise IdentityError(
                "KEYCLOAK_TOKEN_RESPONSE_INVALID",
                f"Keycloak omitted or invalidated {name}",
                status=502,
            )
        return value.strip()

    @staticmethod
    def _optional_token(payload: Mapping[str, Any], name: str) -> str | None:
        value = payload.get(name)
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > 24_000:
            raise IdentityError(
                "KEYCLOAK_TOKEN_RESPONSE_INVALID",
                f"Keycloak returned an invalid {name}",
                status=502,
            )
        return value.strip()

    def _new_session(
        self,
        *,
        principal: AuthenticatedPrincipal,
        refresh_token: str | None,
        refresh_expires_in: object,
        existing_session: EnterpriseSession | None = None,
        selected_organization_id: str | None = None,
        selected_workspace_id: str | None = None,
    ) -> EnterpriseSession:
        current = _now()
        refresh_expiry = existing_session.refresh_token_expires_at if existing_session else None
        if isinstance(refresh_expires_in, (int, float)) and not isinstance(refresh_expires_in, bool):
            if refresh_expires_in < 0:
                raise IdentityError(
                    "KEYCLOAK_TOKEN_RESPONSE_INVALID",
                    "Keycloak refresh expiry is invalid",
                    status=502,
                )
            # Keycloak returns zero for refresh tokens with no exp claim.
            refresh_expiry = (current + timedelta(seconds=float(refresh_expires_in))
                              if refresh_expires_in > 0 else None)
        created_at = existing_session.created_at if existing_session else current
        session = EnterpriseSession(
            session_id=existing_session.session_id if existing_session else _token_urlsafe(),
            principal=principal,
            created_at=created_at,
            refreshed_at=current,
            session_expires_at=(
                existing_session.session_expires_at
                if existing_session
                else (current + timedelta(seconds=self.identity_config.session_ttl_seconds)
                      if self.identity_config.session_ttl_seconds else None)
            ),
            access_token_expires_at=principal.expires_at,
            refresh_token_expires_at=refresh_expiry,
            selected_organization_id=(
                existing_session.selected_organization_id
                if existing_session
                else selected_organization_id
            ),
            selected_workspace_id=(
                existing_session.selected_workspace_id
                if existing_session
                else selected_workspace_id
            ),
            context_version=(existing_session.context_version if existing_session else 0),
            session_generation=(
                existing_session.session_generation + 1 if existing_session else 0
            ),
        )
        self.store.put(
            session,
            refresh_token=refresh_token,
            expected_generation=(
                existing_session.session_generation if existing_session else None
            ),
        )
        return session

    async def complete_login_transaction(
        self,
        *,
        code: str,
        state: str,
        browser_binding: str | None = None,
    ) -> CompletedAuthorization:
        if not code or not state or len(code) > 8_192 or len(state) > 1_024:
            raise IdentityError("OIDC_CALLBACK_INVALID", "OIDC callback is missing or invalid")
        pending = self.store.consume_pending(
            state,
            browser_binding=browser_binding,
        )
        payload = await self._post_form_json(
            self.config.token_endpoint,
            {
                **self._client_auth(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "code_verifier": pending.code_verifier,
            },
        )
        access_token = self._required_token(payload, "access_token")
        id_token = self._required_token(payload, "id_token")
        access_principal = await self.oidc.validate_access_token(access_token)
        id_principal = await self.oidc.validate_id_token(
            id_token,
            expected_nonce=pending.nonce,
        )
        access_sid = access_principal.identity_provider_session_id
        id_sid = id_principal.identity_provider_session_id
        if (
            access_principal.subject != id_principal.subject
            or access_principal.issuer != id_principal.issuer
            or (access_sid and id_sid and access_sid != id_sid)
        ):
            raise IdentityError("OIDC_SUBJECT_MISMATCH", "OIDC access and ID tokens do not match")
        if access_sid is None and id_sid is not None:
            access_principal = access_principal.model_copy(
                update={"identity_provider_session_id": id_sid}
            )
        session = self._new_session(
            principal=access_principal,
            refresh_token=self._optional_token(payload, "refresh_token"),
            refresh_expires_in=payload.get("refresh_expires_in"),
            selected_organization_id=pending.selected_organization_id,
            selected_workspace_id=pending.selected_workspace_id,
        )
        return CompletedAuthorization(session=session, return_path=pending.return_path)

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        browser_binding: str | None = None,
    ) -> EnterpriseSession:
        completed = await self.complete_login_transaction(
            code=code,
            state=state,
            browser_binding=browser_binding,
        )
        return completed.session

    async def refresh_session(self, session_id: str) -> EnterpriseSession:
        async with self._session_mutation_lock(session_id):
            stored = self.store.get_for_refresh(session_id)
            if not stored.refresh_token:
                self.store.revoke(session_id)
                raise IdentityError(
                    "ENTERPRISE_SESSION_REFRESH_UNAVAILABLE",
                    "Enterprise session cannot be refreshed",
                )
            try:
                payload = await self._post_form_json(
                    self.config.token_endpoint,
                    {
                        **self._client_auth(),
                        "grant_type": "refresh_token",
                        "refresh_token": stored.refresh_token,
                    },
                )
            except IdentityError as exc:
                if exc.code == "IDENTITY_DISABLED_OR_REVOKED":
                    self.store.revoke(session_id)
                raise
            access_token = self._required_token(payload, "access_token")
            principal = await self.oidc.validate_access_token(access_token)
            previous = stored.public.principal
            if (
                principal.subject != previous.subject
                or principal.issuer != previous.issuer
                or principal.actor_kind is not previous.actor_kind
                or principal.client_id != previous.client_id
            ):
                self.store.revoke(session_id)
                raise IdentityError("OIDC_SUBJECT_MISMATCH", "Refreshed OIDC identity changed")
            return self._new_session(
                principal=principal,
                refresh_token=(
                    self._optional_token(payload, "refresh_token") or stored.refresh_token
                ),
                refresh_expires_in=payload.get("refresh_expires_in"),
                existing_session=stored.public,
            )

    async def rotate_context(
        self,
        session_id: str,
        *,
        organization_id: str,
        workspace_id: str | None,
        expected_context_version: int,
        expected_generation: int,
    ) -> EnterpriseSession:
        async with self._session_mutation_lock(session_id):
            return self.store.rotate_context(
                session_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                expected_context_version=expected_context_version,
                expected_generation=expected_generation,
            )

    async def authenticate_access_token(self, token: str) -> AuthenticatedPrincipal:
        principal = await self.oidc.validate_access_token(token)
        if self.config.introspection_enabled:
            payload = await self._post_form_json(
                self.config.introspection_endpoint,
                {**self._client_auth(), "token": token},
            )
            if payload.get("active") is not True:
                raise IdentityError(
                    "IDENTITY_DISABLED_OR_REVOKED",
                    "Identity is disabled or revoked",
                )
            introspected_subject = payload.get("sub")
            if introspected_subject is not None and introspected_subject != principal.subject:
                raise IdentityError(
                    "OIDC_SUBJECT_MISMATCH",
                    "OIDC introspection subject does not match",
                )
        return principal

    async def logout(self, session_id: str) -> str | None:
        async with self._session_mutation_lock(session_id):
            stored = self.store.get(session_id)
            try:
                if stored.refresh_token:
                    await self._post_form_no_content(
                        self.config.revocation_endpoint,
                        {
                            **self._client_auth(),
                            "token": stored.refresh_token,
                            "token_type_hint": "refresh_token",
                        },
                    )
            finally:
                self.store.revoke(session_id)
        query: dict[str, str] = {"client_id": self.config.client_id}
        if self.config.post_logout_redirect_uri:
            query["post_logout_redirect_uri"] = self.config.post_logout_redirect_uri
        return f"{self.config.end_session_endpoint}?{urlencode(query)}"

    async def consume_backchannel_logout(self, logout_token: str) -> int:
        claims = await self.oidc.validate_logout_token(logout_token)
        token_id = str(claims["jti"])
        if not self.store.register_logout_token(token_id):
            return 0
        return self.store.revoke_by_subject_or_sid(
            subject=str(claims.get("sub") or "").strip() or None,
            sid=str(claims.get("sid") or "").strip() or None,
        )

    async def aclose(self) -> None:
        if self._owns_oidc:
            await self.oidc.aclose()
        if self._owns_http:
            await self._http.aclose()


__all__ = [
    "CompletedAuthorization",
    "InMemoryEnterpriseSessionStore",
    "KeycloakOidcClient",
    "PendingAuthorization",
]
