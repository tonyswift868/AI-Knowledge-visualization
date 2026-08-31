from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .credential_store import CredentialStoreError, delete_secret, get_secret
from .enterprise.contracts_v1 import ActorContext, TenantContext, TenantMode
from .provider_session_repository import (
    ProviderSessionRecord,
    ProviderSessionRepository,
)
from .providers.telemetry import provider_request_context
from .provider_service import ProviderError, get_provider
from .provider_url_policy import ProviderURLPolicyError, validate_provider_base_url

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
    subject_id: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None
    updated_at: datetime | None = None
    verified_at: datetime | None = None
    revoked_at: datetime | None = None


_sessions: dict[str, ProviderSession] = {}
_durable_repository: ProviderSessionRepository | None = None


def configure_provider_session_repository(
    repository: ProviderSessionRepository | None,
) -> None:
    """Configure the shared enterprise repository for this process.

    Backend and Worker configure independent repository instances against the
    same PostgreSQL database.  Personal Local callers that omit actor/tenant
    context continue to use the process-local compatibility path.
    """

    global _durable_repository
    _durable_repository = repository


def _durable_binding(
    *,
    actor: ActorContext | None,
    tenant: TenantContext | None,
    repository: ProviderSessionRepository | None,
) -> tuple[ProviderSessionRepository, ActorContext, TenantContext] | None:
    if actor is None and tenant is None and repository is None:
        return None
    effective_repository = repository or _durable_repository
    if actor is None or tenant is None or effective_repository is None:
        raise ProviderError(
            "PROVIDER_SESSION_CONTEXT_REQUIRED",
            "Enterprise Provider 会话需要完整的 actor、tenant 和 repository 上下文",
            403,
        )
    if tenant.mode is not TenantMode.ENTERPRISE_SERVER:
        raise ProviderError(
            "PROVIDER_SESSION_CONTEXT_REQUIRED",
            "持久化 Provider 会话仅用于 Enterprise Server Mode",
            403,
        )
    if tenant.organization_id is None or tenant.workspace_id is None:
        raise ProviderError(
            "PROVIDER_SESSION_CONTEXT_REQUIRED",
            "Enterprise Provider 会话需要 organization 和 workspace 上下文",
            403,
        )
    if (
        actor.organization_id != tenant.organization_id
        or actor.workspace_id != tenant.workspace_id
    ):
        raise ProviderError(
            "PROVIDER_SESSION_ACCESS_DENIED",
            "Provider 会话不属于当前租户上下文",
            403,
        )
    return effective_repository, actor, tenant


def _from_record(record: ProviderSessionRecord) -> ProviderSession:
    return ProviderSession(
        id=record.provider_session_id,
        provider=record.provider,
        base_url=record.base_url,
        model=record.model,
        credential_ref=record.credential_ref,
        temperature=record.temperature,
        created_at=record.created_at,
        expires_at=record.expires_at,
        last_tested_at=record.last_tested_at,
        verified=record.verified_at is not None,
        last_latency_ms=record.last_latency_ms,
        last_error_code=record.last_error_code,
        model_reported=record.model_reported,
        owner_user_id=record.subject_id,
        subject_id=record.subject_id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        updated_at=record.updated_at,
        verified_at=record.verified_at,
        revoked_at=record.revoked_at,
    )


def _to_record(session: ProviderSession) -> ProviderSessionRecord:
    if not session.subject_id or not session.organization_id or not session.workspace_id:
        raise ValueError("durable Provider session requires complete ownership")
    return ProviderSessionRecord(
        provider_session_id=session.id,
        subject_id=session.subject_id,
        organization_id=session.organization_id,
        workspace_id=session.workspace_id,
        provider=session.provider,
        base_url=session.base_url,
        model=session.model,
        credential_ref=session.credential_ref,
        temperature=session.temperature,
        verified_at=session.verified_at,
        expires_at=session.expires_at,
        revoked_at=session.revoked_at,
        last_tested_at=session.last_tested_at,
        last_latency_ms=session.last_latency_ms,
        last_error_code=session.last_error_code,
        model_reported=session.model_reported,
        created_at=session.created_at,
        updated_at=session.updated_at or session.created_at,
    )


def _credential_error_status(error: CredentialStoreError) -> int:
    if error.code in {"CREDENTIAL_REQUIRED", "CREDENTIAL_REFERENCE_INVALID"}:
        return 400
    if error.code == "CREDENTIAL_NOT_FOUND":
        return 404
    return 503


def _credential_provider_error(
    error: CredentialStoreError,
    *,
    enterprise: bool,
) -> ProviderError:
    if enterprise:
        return ProviderError(
            "PROVIDER_CONFIGURATION_REQUIRED",
            "Enterprise Provider 凭据未配置或已不可用",
            428,
        )
    return ProviderError(error.code, error.message, _credential_error_status(error))


def _validated_base_url(value: object) -> str:
    """Validate a real Provider URL without echoing the rejected value."""

    try:
        return validate_provider_base_url(value)  # type: ignore[arg-type]
    except ProviderURLPolicyError as exc:
        raise ProviderError(
            exc.code,
            exc.message,
            exc.status,
            {"field": "base_url"},
        ) from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def public_session(session: ProviderSession, request_id: str | None = None, latency_ms: int | None = None) -> dict[str, Any]:
    return {"provider_session_id": session.id, "provider": session.provider, "credential_ref": session.credential_ref or None, "base_url_host": _host(session.base_url), "model_requested": session.model, "model_reported": session.model_reported or session.model, "verified": session.verified, "expires_at": _iso(session.expires_at), "last_tested_at": _iso(session.last_tested_at), "latency_ms": latency_ms if latency_ms is not None else session.last_latency_ms, "last_error_code": session.last_error_code, "request_id": request_id}


def create_provider_session(
    config: dict[str, Any],
    *,
    owner_user_id: str | None = None,
    actor: ActorContext | None = None,
    tenant: TenantContext | None = None,
    repository: ProviderSessionRepository | None = None,
) -> dict[str, Any]:
    durable = _durable_binding(actor=actor, tenant=tenant, repository=repository)
    raw_provider = str(config.get("provider", "fake"))
    provider = "openai_compatible" if raw_provider == "openai-compatible" else raw_provider
    if durable is not None and provider == "fake":
        raise ProviderError(
            "PROVIDER_CONFIGURATION_REQUIRED",
            "Enterprise Server Mode 不允许使用 fake Provider 会话",
            428,
        )
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
        base_url = _validated_base_url(config.get("base_url"))
        effective_config["base_url"] = base_url
        try:
            credential_ref = str(config.get("credential_ref") or "").strip()
            api_key = get_secret(credential_ref)
        except CredentialStoreError as exc:
            raise _credential_provider_error(
                exc,
                enterprise=durable is not None,
            ) from exc
        get_provider({**effective_config, "api_key": api_key})
    created = _now()
    subject_id = durable[1].subject_id if durable is not None else None
    organization_id = durable[2].organization_id if durable is not None else None
    workspace_id = durable[2].workspace_id if durable is not None else None
    session = ProviderSession(id="ps_" + secrets.token_urlsafe(12), provider=provider, base_url=str(effective_config.get("base_url", "")), model=str(effective_config.get("model", "fake")), credential_ref=credential_ref, temperature=float(config.get("temperature", 0.2)), created_at=created, expires_at=None, owner_user_id=subject_id or owner_user_id, subject_id=subject_id, organization_id=organization_id, workspace_id=workspace_id, updated_at=created)
    if durable is not None:
        durable[0].create(_to_record(session), actor=durable[1], tenant=durable[2])
        return public_session(session)
    _sessions[session.id] = session
    return public_session(session)


def get_provider_session(
    session_id: str | None,
    *,
    owner_user_id: str | None = None,
    actor: ActorContext | None = None,
    tenant: TenantContext | None = None,
    repository: ProviderSessionRepository | None = None,
) -> ProviderSession | None:
    if not session_id:
        return None
    durable = _durable_binding(actor=actor, tenant=tenant, repository=repository)
    if durable is not None:
        record = durable[0].get(session_id, actor=durable[1], tenant=durable[2])
        return _from_record(record) if record is not None else None
    session = _sessions.get(session_id)
    if not session:
        return None
    # An authenticated request can never use an unowned legacy handle or
    # another account's handle.  Auth-disabled requests preserve the existing
    # test/development behavior by omitting the owner filter.
    if owner_user_id is not None and session.owner_user_id != owner_user_id:
        return None
    return session


def require_provider_session(
    session_id: str | None,
    verified: bool = True,
    *,
    owner_user_id: str | None = None,
    actor: ActorContext | None = None,
    tenant: TenantContext | None = None,
    repository: ProviderSessionRepository | None = None,
) -> ProviderSession:
    session = get_provider_session(
        session_id,
        owner_user_id=owner_user_id,
        actor=actor,
        tenant=tenant,
        repository=repository,
    )
    if not session:
        raise ProviderError("PROVIDER_SESSION_REQUIRED", "请先验证 Provider 连接", 428)
    if session.revoked_at is not None:
        raise ProviderError(
            "PROVIDER_SESSION_REVOKED",
            "Provider 会话已撤销",
            428,
        )
    if session.expires_at is not None and session.expires_at <= _now():
        raise ProviderError(
            "PROVIDER_SESSION_EXPIRED",
            "Provider 会话已过期",
            428,
        )
    if session.provider != "fake" and not session.credential_ref:
        raise ProviderError(
            "PROVIDER_CONFIGURATION_REQUIRED",
            "Enterprise Provider 凭据未配置或已不可用",
            428,
        )
    if verified and not session.verified:
        raise ProviderError("PROVIDER_SESSION_UNVERIFIED", "Provider 尚未验证，请先测试连接", 428)
    return session


def latest_verified_provider_session(
    *,
    owner_user_id: str | None = None,
    actor: ActorContext | None = None,
    tenant: TenantContext | None = None,
    repository: ProviderSessionRepository | None = None,
) -> ProviderSession | None:
    """Return the newest usable real session for the current local owner.

    The desktop shell and its embedded WebEngine share one loopback backend,
    but not one JavaScript storage area.  This read-only lookup lets the web
    surface adopt the already verified process-local handle without exposing
    the API key or creating another credential.
    """

    now = _now()
    durable = _durable_binding(actor=actor, tenant=tenant, repository=repository)
    if durable is not None:
        record = durable[0].latest_verified(
            actor=durable[1],
            tenant=durable[2],
            now=now,
        )
        return _from_record(record) if record is not None else None
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


async def test_provider_session(
    session_id: str,
    *,
    actor: ActorContext | None = None,
    tenant: TenantContext | None = None,
    repository: ProviderSessionRepository | None = None,
) -> dict[str, Any]:
    durable = _durable_binding(actor=actor, tenant=tenant, repository=repository)
    session = require_provider_session(
        session_id,
        verified=False,
        actor=actor,
        tenant=tenant,
        repository=repository,
    )
    started = time.perf_counter()
    request_id = "req_" + secrets.token_hex(10)
    try:
        with provider_request_context(
            task_type="provider_connection_test",
            request_id=request_id,
            prompt_version="provider-connection-v1",
        ):
            result = await provider_for_session(session).test()
    except ProviderError as exc:
        tested_at = _now()
        session.verified = False
        session.verified_at = None
        session.last_tested_at = tested_at
        session.last_error_code = exc.code
        session.updated_at = tested_at
        if durable is not None:
            durable[0].record_test_result(
                session.id,
                actor=durable[1],
                tenant=durable[2],
                tested_at=tested_at,
                verified=False,
                latency_ms=None,
                error_code=exc.code,
                model_reported=None,
            )
        raise
    latency = max(1, int((time.perf_counter() - started) * 1000))
    tested_at = _now()
    session.verified = True
    session.verified_at = tested_at
    session.last_tested_at = tested_at
    session.last_latency_ms = latency
    session.last_error_code = None
    session.model_reported = result.get("model_reported") or result.get("model") or session.model
    session.updated_at = tested_at
    if durable is not None:
        persisted = durable[0].record_test_result(
            session.id,
            actor=durable[1],
            tenant=durable[2],
            tested_at=tested_at,
            verified=True,
            latency_ms=latency,
            error_code=None,
            model_reported=session.model_reported,
        )
        if not persisted:
            raise ProviderError(
                "PROVIDER_SESSION_REQUIRED",
                "Provider 会话已不存在或已撤销",
                428,
            )
    return public_session(session, request_id=request_id, latency_ms=latency) | {"ok": True, "mode": "demo" if session.provider == "fake" else "real", "fallback_used": False}


def provider_for_session(session: ProviderSession):
    if session.provider != "fake":
        # Validate before touching the credential store as well as at the
        # Provider factory boundary.  This protects legacy/manually-created
        # in-memory handles from reaching a network client.
        _validated_base_url(session.base_url)
    try:
        api_key = get_secret(session.credential_ref) if session.provider != "fake" else ""
    except CredentialStoreError as exc:
        raise _credential_provider_error(
            exc,
            enterprise=session.organization_id is not None,
        ) from exc
    return get_provider({"provider": session.provider if session.provider != "openai_compatible" else "openai-compatible", "base_url": session.base_url, "model": session.model, "api_key": api_key, "temperature": session.temperature})


def generation_meta(session: ProviderSession, request_id: str | None = None, latency_ms: int | None = None) -> dict[str, Any]:
    return {"mode": "demo" if session.provider == "fake" else "real", "provider": session.provider, "model": session.model, "provider_session_id": session.id, "request_id": request_id or "req_" + secrets.token_hex(10), "latency_ms": latency_ms, "fallback_used": False}


def delete_provider_session(
    session_id: str,
    *,
    owner_user_id: str | None = None,
    actor: ActorContext | None = None,
    tenant: TenantContext | None = None,
    repository: ProviderSessionRepository | None = None,
) -> bool:
    durable = _durable_binding(actor=actor, tenant=tenant, repository=repository)
    if durable is not None:
        return durable[0].delete(
            session_id,
            actor=durable[1],
            tenant=durable[2],
        )
    session = get_provider_session(session_id, owner_user_id=owner_user_id)
    if not session:
        return False
    _sessions.pop(session_id, None)
    return True


def revoke_provider_session(
    session_id: str,
    *,
    actor: ActorContext,
    tenant: TenantContext,
    repository: ProviderSessionRepository | None = None,
) -> bool:
    durable = _durable_binding(actor=actor, tenant=tenant, repository=repository)
    if durable is None:
        raise ProviderError(
            "PROVIDER_SESSION_CONTEXT_REQUIRED",
            "Enterprise Provider 会话需要完整的持久化上下文",
            403,
        )
    return durable[0].revoke(
        session_id,
        actor=durable[1],
        tenant=durable[2],
        revoked_at=_now(),
    )


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
