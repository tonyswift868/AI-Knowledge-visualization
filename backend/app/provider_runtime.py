from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .credential_store import CredentialStoreError, delete_secret, get_secret
from .provider_service import ProviderError, get_provider

@dataclass
class ProviderSession:
    id: str
    provider: str
    base_url: str
    model: str
    credential_ref: str
    temperature: float
    created_at: datetime
    expires_at: datetime | None
    last_tested_at: datetime | None = None
    verified: bool = False
    last_latency_ms: int | None = None
    last_error_code: str | None = None
    model_reported: str | None = None
    # Provider handles are process-local, but must still be bound to the
    # authenticated account that created them.  ``None`` is retained only for
    # legacy/auth-disabled development requests.
    owner_user_id: str | None = None


_sessions: dict[str, ProviderSession] = {}


def _credential_error_status(error: CredentialStoreError) -> int:
    if error.code in {"CREDENTIAL_REQUIRED", "CREDENTIAL_REFERENCE_INVALID"}:
        return 400
    if error.code == "CREDENTIAL_NOT_FOUND":
        return 404
    return 503


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def public_session(session: ProviderSession, request_id: str | None = None, latency_ms: int | None = None) -> dict[str, Any]:
    return {"provider_session_id": session.id, "provider": session.provider, "credential_ref": session.credential_ref or None, "base_url_host": _host(session.base_url), "model_requested": session.model, "model_reported": session.model_reported or session.model, "verified": session.verified, "expires_at": _iso(session.expires_at), "last_tested_at": _iso(session.last_tested_at), "latency_ms": latency_ms if latency_ms is not None else session.last_latency_ms, "last_error_code": session.last_error_code, "request_id": request_id}


def create_provider_session(config: dict[str, Any], *, owner_user_id: str | None = None) -> dict[str, Any]:
    raw_provider = str(config.get("provider", "fake"))
    provider = "openai_compatible" if raw_provider == "openai-compatible" else raw_provider
    effective_config = {**config}
    credential_ref = ""
    if provider != "fake":
        if "api_key" in config:
            raise ProviderError(
                "RAW_PROVIDER_CREDENTIAL_FORBIDDEN",
                "Provider 密钥不得通过应用 API 或会话配置流转；请先写入 Windows Credential Locker 并传递引用 ID",
                400,
                {"provider": provider},
            )
        if not str(config.get("base_url") or "").strip() or not str(config.get("model") or "").strip():
            raise ProviderError(
                "PROVIDER_CONFIG_MISSING",
                "真实 Provider 需要显式填写 Base URL 和 Model",
                400,
                {"provider": provider},
            )
        try:
            credential_ref = str(config.get("credential_ref") or "").strip()
            api_key = get_secret(credential_ref)
        except CredentialStoreError as exc:
            raise ProviderError(exc.code, exc.message, _credential_error_status(exc)) from exc
        get_provider({**effective_config, "api_key": api_key})
    created = _now()
    session = ProviderSession(id="ps_" + secrets.token_urlsafe(12), provider=provider, base_url=str(effective_config.get("base_url", "")), model=str(effective_config.get("model", "fake")), credential_ref=credential_ref, temperature=float(config.get("temperature", 0.2)), created_at=created, expires_at=None, owner_user_id=owner_user_id)
    _sessions[session.id] = session
    return public_session(session)


def get_provider_session(session_id: str | None, *, owner_user_id: str | None = None) -> ProviderSession | None:
    if not session_id:
        return None
    session = _sessions.get(session_id)
    if not session:
        return None
    # An authenticated request can never use an unowned legacy handle or
    # another account's handle.  Auth-disabled requests preserve the existing
    # test/development behavior by omitting the owner filter.
    if owner_user_id is not None and session.owner_user_id != owner_user_id:
        return None
    return session


def require_provider_session(session_id: str | None, verified: bool = True, *, owner_user_id: str | None = None) -> ProviderSession:
    session = get_provider_session(session_id, owner_user_id=owner_user_id)
    if not session:
        raise ProviderError("PROVIDER_SESSION_REQUIRED", "请先验证 Provider 连接", 428)
    if verified and not session.verified:
        raise ProviderError("PROVIDER_SESSION_UNVERIFIED", "Provider 尚未验证，请先测试连接", 428)
    return session


def latest_verified_provider_session(*, owner_user_id: str | None = None) -> ProviderSession | None:
    """Return the newest usable real session for the current local owner.

    The desktop shell and its embedded WebEngine share one loopback backend,
    but not one JavaScript storage area.  This read-only lookup lets the web
    surface adopt the already verified process-local handle without exposing
    the API key or creating another credential.
    """

    now = _now()
    candidates = [
        session
        for session in _sessions.values()
        if session.verified
        and session.provider != "fake"
        and (session.expires_at is None or session.expires_at > now)
        and (owner_user_id is None or session.owner_user_id == owner_user_id)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda session: session.last_tested_at or session.created_at,
    )


def same_provider_configuration(left: ProviderSession, right: ProviderSession) -> bool:
    """Return whether two sessions represent the same evaluator configuration.

    Provider sessions are process-local handles. Recreating an identical handle
    after a page refresh or backend restart must not look like an evaluator
    switch to a durable teaching session.
    """

    left_provider = left.provider.replace("-", "_").strip().lower()
    right_provider = right.provider.replace("-", "_").strip().lower()
    left_base_url = left.base_url.strip().rstrip("/").lower()
    right_base_url = right.base_url.strip().rstrip("/").lower()
    return (
        left_provider == right_provider
        and left_base_url == right_base_url
        and left.model.strip() == right.model.strip()
        and left.credential_ref == right.credential_ref
    )


async def test_provider_session(session_id: str) -> dict[str, Any]:
    session = require_provider_session(session_id, verified=False)
    started = time.perf_counter()
    request_id = "req_" + secrets.token_hex(10)
    try:
        result = await provider_for_session(session).test()
    except ProviderError as exc:
        session.verified = False
        session.last_error_code = exc.code
        raise
    latency = max(1, int((time.perf_counter() - started) * 1000))
    session.verified = True
    session.last_tested_at = _now()
    session.last_latency_ms = latency
    session.last_error_code = None
    session.model_reported = result.get("model_reported") or result.get("model") or session.model
    return public_session(session, request_id=request_id, latency_ms=latency) | {"ok": True, "mode": "demo" if session.provider == "fake" else "real", "fallback_used": False}


def provider_for_session(session: ProviderSession):
    try:
        api_key = get_secret(session.credential_ref) if session.provider != "fake" else ""
    except CredentialStoreError as exc:
        raise ProviderError(exc.code, exc.message, _credential_error_status(exc)) from exc
    return get_provider({"provider": session.provider if session.provider != "openai_compatible" else "openai-compatible", "base_url": session.base_url, "model": session.model, "api_key": api_key, "temperature": session.temperature})


def generation_meta(session: ProviderSession, request_id: str | None = None, latency_ms: int | None = None) -> dict[str, Any]:
    return {"mode": "demo" if session.provider == "fake" else "real", "provider": session.provider, "model": session.model, "provider_session_id": session.id, "request_id": request_id or "req_" + secrets.token_hex(10), "latency_ms": latency_ms, "fallback_used": False}


def delete_provider_session(session_id: str, *, owner_user_id: str | None = None) -> bool:
    session = get_provider_session(session_id, owner_user_id=owner_user_id)
    if not session:
        return False
    _sessions.pop(session_id, None)
    return True


def delete_provider_credential(credential_ref: str) -> bool:
    try:
        return delete_secret(credential_ref)
    except CredentialStoreError as exc:
        raise ProviderError(exc.code, exc.message, _credential_error_status(exc)) from exc


def credential_owned_by(credential_ref: str, owner_user_id: str) -> bool:
    """Return whether an authenticated account owns an in-memory provider ref."""

    ref = str(credential_ref or "").strip()
    user_id = str(owner_user_id or "").strip()
    return bool(ref and user_id and any(
        session.owner_user_id == user_id and session.credential_ref == ref
        for session in _sessions.values()
    ))


def create_verified_demo_session(owner_user_id: str | None = None) -> ProviderSession:
    created = _now()
    session = ProviderSession(id="ps_demo_" + secrets.token_hex(6), provider="fake", base_url="", model="fake", credential_ref="", temperature=0.2, created_at=created, expires_at=None, last_tested_at=created, verified=True, last_latency_ms=0, model_reported="fake", owner_user_id=owner_user_id)
    _sessions[session.id] = session
    return session


def reset_provider_sessions() -> None:
    _sessions.clear()
