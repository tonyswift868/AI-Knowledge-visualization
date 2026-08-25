from __future__ import annotations

from decimal import Decimal

from app.providers.telemetry import ProviderUsageReceipt

from .contracts import (
    CacheEvent,
    CacheIdentity,
    CacheLayer,
    CacheMissReason,
    CacheObservatoryError,
)

_LOCAL_REASON_MAP = {
    "not_found": CacheMissReason.COLD,
    "expired": CacheMissReason.EXPIRED,
    "policy_disabled": CacheMissReason.NOT_CACHEABLE,
    "not_cacheable": CacheMissReason.NOT_CACHEABLE,
    "identity_mismatch": CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
    "schema_mismatch": CacheMissReason.PROMPT_VERSION_CHANGED,
    "key_mismatch": CacheMissReason.UNKNOWN,
    "cache_corrupt": CacheMissReason.UNKNOWN,
    "entry_oversized": CacheMissReason.UNKNOWN,
    "not_validated": CacheMissReason.UNKNOWN,
}


def local_cache_miss_reason(reason: str | None) -> CacheMissReason:
    return _LOCAL_REASON_MAP.get(
        str(reason or "").strip().casefold(), CacheMissReason.UNKNOWN
    )


def cache_events_from_usage_receipt(
    *,
    trace_id: str,
    receipt: ProviderUsageReceipt,
    provider_prompt_identity: CacheIdentity | None = None,
    local_result_identity: CacheIdentity | None = None,
    provider_miss_reason: CacheMissReason = CacheMissReason.UNKNOWN,
    local_miss_reason: CacheMissReason = CacheMissReason.UNKNOWN,
    provider_cache_estimated_cost_avoided: Decimal = Decimal(0),
    local_result_estimated_cost_avoided: Decimal = Decimal(0),
    local_result_bytes_reused: int = 0,
    currency: str = "USD",
) -> tuple[CacheEvent, ...]:
    """Adapt current telemetry without merging Provider and local cache facts.

    Provider telemetry cannot explain identity drift on its own. Callers may
    supply a reason derived from compared CacheIdentity values; otherwise the
    honest miss classification remains UNKNOWN.
    """

    if not isinstance(receipt, ProviderUsageReceipt):
        raise TypeError("receipt must be ProviderUsageReceipt")
    if (
        provider_prompt_identity is not None
        and provider_prompt_identity.layer != CacheLayer.PROVIDER_PROMPT_CACHE
    ):
        raise CacheObservatoryError(
            "provider_prompt_identity must use PROVIDER_PROMPT_CACHE"
        )
    if (
        local_result_identity is not None
        and local_result_identity.layer != CacheLayer.LOCAL_RESULT_CACHE
    ):
        raise CacheObservatoryError("local_result_identity must use LOCAL_RESULT_CACHE")
    events: list[CacheEvent] = []
    if (
        provider_prompt_identity is not None
        and receipt.provider_called
        and receipt.prompt_cache_hit_tokens is not None
        and receipt.prompt_cache_miss_tokens is not None
    ):
        provider_hit = receipt.prompt_cache_hit_tokens > 0
        events.append(
            CacheEvent(
                event_id=f"{trace_id}:provider-prompt-cache",
                trace_id=trace_id,
                identity=provider_prompt_identity,
                hit=provider_hit,
                miss_reason=None if provider_hit else provider_miss_reason,
                tokens_reused=(receipt.prompt_cache_hit_tokens if provider_hit else 0),
                estimated_cost_avoided=(
                    provider_cache_estimated_cost_avoided
                    if provider_hit
                    else Decimal(0)
                ),
                currency=currency,
                lookup_ms=None,
                synthetic=False,
            )
        )
    local_hit = receipt.local_result_cache_hit is True
    local_miss = receipt.local_cache_miss is True or (
        receipt.local_result_cache_hit is False and receipt.provider_called
    )
    if local_result_identity is not None and (local_hit or local_miss):
        events.append(
            CacheEvent(
                event_id=f"{trace_id}:local-result-cache",
                trace_id=trace_id,
                identity=local_result_identity,
                hit=local_hit,
                miss_reason=None if local_hit else local_miss_reason,
                bytes_reused=local_result_bytes_reused if local_hit else 0,
                avoided_provider_calls=1 if local_hit else 0,
                estimated_cost_avoided=(
                    local_result_estimated_cost_avoided if local_hit else Decimal(0)
                ),
                currency=currency,
                lookup_ms=(
                    Decimal(receipt.cache_lookup_ms)
                    if receipt.cache_lookup_ms is not None
                    else None
                ),
                synthetic=False,
            )
        )
    return tuple(events)
