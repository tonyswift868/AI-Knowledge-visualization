from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .provider_service import ProviderError, get_provider

@dataclass
class ProviderSession:
    id: str
    provider: str
    base_url: str
    model: str
    api_key: str
    temperature: float
    created_at: datetime
    expires_at: datetime | None
    last_tested_at: datetime | None = None
    verified: bool = False
    last_latency_ms: int | None = None
    last_error_code: str | None = None
    model_reported: str | None = None


_sessions: dict[str, ProviderSession] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def public_session(session: ProviderSession, request_id: str | None = None, latency_ms: int | None = None) -> dict[str, Any]:
    return {"provider_session_id": session.id, "provider": session.provider, "base_url_host": _host(session.base_url), "model_requested": session.model, "model_reported": session.model_reported or session.model, "verified": session.verified, "expires_at": _iso(session.expires_at), "last_tested_at": _iso(session.last_tested_at), "latency_ms": latency_ms if latency_ms is not None else session.last_latency_ms, "last_error_code": session.last_error_code, "request_id": request_id}


def create_provider_session(config: dict[str, Any]) -> dict[str, Any]:
    raw_provider = str(config.get("provider", "fake"))
    provider = "openai_compatible" if raw_provider == "openai-compatible" else raw_provider
    effective_config = {**config}
    if provider == "openai_compatible":
        effective_config["base_url"] = str(config.get("base_url") or "https://api.deepseek.com")
        effective_config["model"] = str(config.get("model") or "deepseek-v4-pro")
    if provider == "gemini":
        effective_config["base_url"] = str(
            config.get("base_url")
            or "https://generativelanguage.googleapis.com/v1beta"
        )
        effective_config["model"] = str(
            config.get("model") or "gemini-2.5-flash"
        )
    if provider != "fake":
        get_provider(effective_config)
    created = _now()
    session = ProviderSession(id="ps_" + secrets.token_urlsafe(12), provider=provider, base_url=str(effective_config.get("base_url", "")), model=str(effective_config.get("model", "fake")), api_key=str(effective_config.get("api_key", "")), temperature=float(config.get("temperature", 0.2)), created_at=created, expires_at=None)
    _sessions[session.id] = session
    return public_session(session)


def get_provider_session(session_id: str | None) -> ProviderSession | None:
    if not session_id:
        return None
    return _sessions.get(session_id)


def require_provider_session(session_id: str | None, verified: bool = True) -> ProviderSession:
    session = get_provider_session(session_id)
    if not session:
        raise ProviderError("PROVIDER_SESSION_REQUIRED", "请先验证 Provider 连接", 428)
    if verified and not session.verified:
        raise ProviderError("PROVIDER_SESSION_UNVERIFIED", "Provider 尚未验证，请先测试连接", 428)
    return session


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
        and secrets.compare_digest(left.api_key, right.api_key)
    )


async def test_provider_session(session_id: str) -> dict[str, Any]:
    session = require_provider_session(session_id, verified=False)
    started = time.perf_counter()
    request_id = "req_" + secrets.token_hex(10)
    try:
        result = await get_provider({"provider": session.provider if session.provider != "openai_compatible" else "openai-compatible", "base_url": session.base_url, "model": session.model, "api_key": session.api_key, "temperature": session.temperature}).test()
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
    return get_provider({"provider": session.provider if session.provider != "openai_compatible" else "openai-compatible", "base_url": session.base_url, "model": session.model, "api_key": session.api_key, "temperature": session.temperature})


def generation_meta(session: ProviderSession, request_id: str | None = None, latency_ms: int | None = None) -> dict[str, Any]:
    return {"mode": "demo" if session.provider == "fake" else "real", "provider": session.provider, "model": session.model, "provider_session_id": session.id, "request_id": request_id or "req_" + secrets.token_hex(10), "latency_ms": latency_ms, "fallback_used": False}


def delete_provider_session(session_id: str) -> bool:
    return _sessions.pop(session_id, None) is not None


def create_verified_demo_session() -> ProviderSession:
    created = _now()
    session = ProviderSession(id="ps_demo_" + secrets.token_hex(6), provider="fake", base_url="", model="fake", api_key="", temperature=0.2, created_at=created, expires_at=None, last_tested_at=created, verified=True, last_latency_ms=0, model_reported="fake")
    _sessions[session.id] = session
    return session


def reset_provider_sessions() -> None:
    _sessions.clear()
