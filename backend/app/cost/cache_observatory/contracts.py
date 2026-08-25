from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class CacheObservatoryError(ValueError):
    pass


class CacheEventConflict(CacheObservatoryError):
    pass


class CacheLayer(str, Enum):
    PROVIDER_PROMPT_CACHE = "PROVIDER_PROMPT_CACHE"
    LOCAL_RESULT_CACHE = "LOCAL_RESULT_CACHE"
    EVIDENCE_CACHE = "EVIDENCE_CACHE"
    PARSE_CACHE = "PARSE_CACHE"
    QUESTION_BANK_CACHE = "QUESTION_BANK_CACHE"
    EVALUATION_ARTIFACT_CACHE = "EVALUATION_ARTIFACT_CACHE"


class CacheMissReason(str, Enum):
    COLD = "COLD"
    DYNAMIC_CONTEXT_CHANGED = "DYNAMIC_CONTEXT_CHANGED"
    EVIDENCE_CHANGED = "EVIDENCE_CHANGED"
    MODEL_CHANGED = "MODEL_CHANGED"
    PROMPT_VERSION_CHANGED = "PROMPT_VERSION_CHANGED"
    ROUTE_CHANGED = "ROUTE_CHANGED"
    EXPIRED = "EXPIRED"
    NOT_CACHEABLE = "NOT_CACHEABLE"
    UNKNOWN = "UNKNOWN"


def _required(name: str, value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise CacheObservatoryError(f"{name} must not be empty")
    if len(normalized) > 512 or any(ord(character) < 32 for character in normalized):
        raise CacheObservatoryError(f"{name} must be an opaque identifier")
    return normalized


def _optional(value: object) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 512 or any(ord(character) < 32 for character in normalized):
        raise CacheObservatoryError("cache identity values must be opaque identifiers")
    return normalized


def _non_negative_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or value < 0:
        raise CacheObservatoryError(f"{name} must be finite and non-negative")


def _non_negative_int(name: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CacheObservatoryError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CurrencyAmount:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "currency", _required("currency", self.currency).upper()
        )
        _non_negative_decimal("amount", self.amount)


@dataclass(frozen=True, slots=True)
class MissReasonCount:
    reason: CacheMissReason
    count: int

    def __post_init__(self) -> None:
        _non_negative_int("count", self.count)


@dataclass(frozen=True, slots=True)
class CacheIdentity:
    layer: CacheLayer
    namespace: str
    artifact_id: str
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    route_identity: str | None = None
    stable_prefix_hash: str | None = None
    dynamic_context_hash: str | None = None
    evidence_hash: str | None = None
    question_hash: str | None = None
    parser_version: str | None = None
    evaluation_version: str | None = None
    expires_at_ms: int | None = None

    def __post_init__(self) -> None:
        layer = (
            self.layer if isinstance(self.layer, CacheLayer) else CacheLayer(self.layer)
        )
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "namespace", _required("namespace", self.namespace))
        object.__setattr__(
            self, "artifact_id", _required("artifact_id", self.artifact_id)
        )
        for field_name in (
            "provider",
            "model",
            "prompt_version",
            "route_identity",
            "stable_prefix_hash",
            "dynamic_context_hash",
            "evidence_hash",
            "question_hash",
            "parser_version",
            "evaluation_version",
        ):
            object.__setattr__(self, field_name, _optional(getattr(self, field_name)))
        _non_negative_int("expires_at_ms", self.expires_at_ms)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "namespace": self.namespace,
            "artifact_id": self.artifact_id,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "route_identity": self.route_identity,
            "stable_prefix_hash": self.stable_prefix_hash,
            "dynamic_context_hash": self.dynamic_context_hash,
            "evidence_hash": self.evidence_hash,
            "question_hash": self.question_hash,
            "parser_version": self.parser_version,
            "evaluation_version": self.evaluation_version,
            "expires_at_ms": self.expires_at_ms,
        }

    @property
    def cache_key(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def classify_cache_miss(
    requested: CacheIdentity,
    candidate: CacheIdentity | None,
    *,
    cacheable: bool = True,
    observed_at_ms: int | None = None,
) -> CacheMissReason:
    """Explain a miss from observable identity differences only."""

    if not cacheable:
        return CacheMissReason.NOT_CACHEABLE
    if candidate is not None and candidate.layer != requested.layer:
        return CacheMissReason.UNKNOWN
    if (
        candidate is not None
        and candidate.expires_at_ms is not None
        and observed_at_ms is not None
        and candidate.expires_at_ms <= observed_at_ms
    ):
        return CacheMissReason.EXPIRED
    if candidate is None:
        return CacheMissReason.COLD
    if candidate.prompt_version != requested.prompt_version:
        return CacheMissReason.PROMPT_VERSION_CHANGED
    if candidate.model != requested.model:
        return CacheMissReason.MODEL_CHANGED
    if candidate.route_identity != requested.route_identity:
        return CacheMissReason.ROUTE_CHANGED
    if candidate.evidence_hash != requested.evidence_hash:
        return CacheMissReason.EVIDENCE_CHANGED
    if candidate.dynamic_context_hash != requested.dynamic_context_hash:
        return CacheMissReason.DYNAMIC_CONTEXT_CHANGED
    return CacheMissReason.UNKNOWN


@dataclass(frozen=True, slots=True)
class CacheEvent:
    event_id: str
    trace_id: str
    identity: CacheIdentity
    hit: bool
    miss_reason: CacheMissReason | None = None
    bytes_reused: int = 0
    tokens_reused: int = 0
    avoided_provider_calls: int = 0
    estimated_cost_avoided: Decimal = Decimal(0)
    currency: str | None = None
    lookup_ms: Decimal | None = None
    occurred_at_ms: int | None = None
    synthetic: bool = False
    savings_classification: str = "estimated"

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required("event_id", self.event_id))
        object.__setattr__(self, "trace_id", _required("trace_id", self.trace_id))
        if not isinstance(self.identity, CacheIdentity):
            raise TypeError("identity must be CacheIdentity")
        reason = self.miss_reason
        if reason is not None and not isinstance(reason, CacheMissReason):
            reason = CacheMissReason(reason)
            object.__setattr__(self, "miss_reason", reason)
        if self.hit and reason is not None:
            raise CacheObservatoryError("a cache hit cannot have a miss reason")
        if not self.hit and reason is None:
            raise CacheObservatoryError("a cache miss requires CacheMissReason")
        for name in ("bytes_reused", "tokens_reused", "avoided_provider_calls"):
            _non_negative_int(name, getattr(self, name))
        _non_negative_decimal("estimated_cost_avoided", self.estimated_cost_avoided)
        if self.lookup_ms is not None:
            _non_negative_decimal("lookup_ms", self.lookup_ms)
        _non_negative_int("occurred_at_ms", self.occurred_at_ms)
        currency = _optional(self.currency)
        if currency is not None:
            currency = currency.upper()
        if self.estimated_cost_avoided > 0 and currency is None:
            raise CacheObservatoryError("estimated savings require a currency")
        object.__setattr__(self, "currency", currency)
        if not self.hit and any(
            (
                self.bytes_reused,
                self.tokens_reused,
                self.avoided_provider_calls,
                self.estimated_cost_avoided,
            )
        ):
            raise CacheObservatoryError(
                "a cache miss cannot claim reused work or savings"
            )
        if self.savings_classification != "estimated":
            raise CacheObservatoryError("cache savings must be explicitly estimated")

    @property
    def layer(self) -> CacheLayer:
        return self.identity.layer


@dataclass(frozen=True, slots=True)
class CacheMetricsSnapshot:
    layer: CacheLayer
    lookup_count: int
    hit_count: int
    miss_count: int
    hit_ratio: Decimal | None
    bytes_reused: int
    tokens_reused: int
    avoided_provider_calls: int
    estimated_cost_avoided: Decimal | None
    estimated_costs_by_currency: tuple[CurrencyAmount, ...]
    miss_reasons: tuple[MissReasonCount, ...]
    synthetic_event_count: int
    savings_classification: str = "estimated"

    def __post_init__(self) -> None:
        for name in (
            "lookup_count",
            "hit_count",
            "miss_count",
            "bytes_reused",
            "tokens_reused",
            "avoided_provider_calls",
            "synthetic_event_count",
        ):
            _non_negative_int(name, getattr(self, name))
        if self.hit_count + self.miss_count != self.lookup_count:
            raise CacheObservatoryError("cache counts must reconcile")
        if self.hit_ratio is not None and (
            not isinstance(self.hit_ratio, Decimal)
            or not Decimal(0) <= self.hit_ratio <= Decimal(1)
        ):
            raise CacheObservatoryError("hit_ratio must be a Decimal from 0 to 1")
        if self.lookup_count == 0 and self.hit_ratio is not None:
            raise CacheObservatoryError("an unused cache layer has no hit ratio")
        if self.lookup_count and self.hit_ratio is None:
            raise CacheObservatoryError("a used cache layer requires a hit ratio")
        if self.estimated_cost_avoided is not None:
            _non_negative_decimal("estimated_cost_avoided", self.estimated_cost_avoided)
        if self.savings_classification != "estimated":
            raise CacheObservatoryError("cache savings must be explicitly estimated")

    @property
    def miss_reason_counts(self) -> dict[CacheMissReason, int]:
        return {item.reason: item.count for item in self.miss_reasons}

    def to_public_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "lookup_count": self.lookup_count,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "hit_ratio": str(self.hit_ratio) if self.hit_ratio is not None else None,
            "bytes_reused": self.bytes_reused,
            "tokens_reused": self.tokens_reused,
            "avoided_provider_calls": self.avoided_provider_calls,
            "estimated_cost_avoided": (
                str(self.estimated_cost_avoided)
                if self.estimated_cost_avoided is not None
                else None
            ),
            "estimated_costs_by_currency": [
                {"currency": item.currency, "amount": str(item.amount)}
                for item in self.estimated_costs_by_currency
            ],
            "savings_classification": self.savings_classification,
            "miss_reasons": {
                item.reason.value: item.count for item in self.miss_reasons
            },
            "synthetic_event_count": self.synthetic_event_count,
        }


@dataclass(frozen=True, slots=True)
class CacheObservatoryReport:
    layers: tuple[CacheMetricsSnapshot, ...]

    def __post_init__(self) -> None:
        present = tuple(item.layer for item in self.layers)
        if len(present) != len(set(present)):
            raise CacheObservatoryError("cache observatory layers must be unique")
        if set(present) != set(CacheLayer):
            raise CacheObservatoryError(
                "cache observatory must report every cache layer"
            )

    def for_layer(self, layer: CacheLayer) -> CacheMetricsSnapshot:
        normalized = layer if isinstance(layer, CacheLayer) else CacheLayer(layer)
        return next(item for item in self.layers if item.layer == normalized)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "layers": {item.layer.value: item.to_public_dict() for item in self.layers},
            "combined_hit_ratio": None,
            "combined_hit_ratio_reason": (
                "Provider Prompt Cache and Local Result Cache are intentionally separate."
            ),
        }
