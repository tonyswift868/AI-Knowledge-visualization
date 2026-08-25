from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import CacheIdentity, CacheObservatoryError
from .identity_policy import DEFAULT_CACHE_IDENTITY_POLICY, CacheIdentityPolicy


class CacheInvalidationReason(str, Enum):
    SOURCE_CHANGED = "SOURCE_CHANGED"
    PROMPT_CHANGED = "PROMPT_CHANGED"
    MODEL_CHANGED = "MODEL_CHANGED"
    ROUTE_CHANGED = "ROUTE_CHANGED"
    EVIDENCE_CHANGED = "EVIDENCE_CHANGED"
    TTL = "TTL"
    MANUAL_INVALIDATE = "MANUAL_INVALIDATE"


@dataclass(frozen=True, slots=True)
class CacheInvalidationDecision:
    invalidate: bool
    reasons: tuple[CacheInvalidationReason, ...]
    layer: str
    namespace: str
    artifact_id: str
    target_identity_key: str
    replacement_identity_key: str | None
    decision_policy: str = "targeted-cache-invalidation-v1"
    clear_all: bool = False

    def __post_init__(self) -> None:
        if self.invalidate != bool(self.reasons):
            raise CacheObservatoryError("invalidation decision and reasons must agree")
        if self.clear_all:
            raise CacheObservatoryError("cache invalidation must never be clear-all")
        for name in (
            "layer",
            "namespace",
            "artifact_id",
            "target_identity_key",
        ):
            if not str(getattr(self, name) or "").strip():
                raise CacheObservatoryError(f"{name} must not be empty")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "invalidate": self.invalidate,
            "reasons": [reason.value for reason in self.reasons],
            "scope": {
                "layer": self.layer,
                "namespace": self.namespace,
                "artifact_id": self.artifact_id,
                "target_identity_key": self.target_identity_key,
            },
            "replacement_identity_key": self.replacement_identity_key,
            "decision_policy": self.decision_policy,
            "clear_all": self.clear_all,
        }


class CacheInvalidationPlanner:
    def __init__(
        self,
        *,
        identity_policy: CacheIdentityPolicy = DEFAULT_CACHE_IDENTITY_POLICY,
    ) -> None:
        self._identity_policy = identity_policy

    def decide(
        self,
        cached: CacheIdentity,
        requested: CacheIdentity | None = None,
        *,
        observed_at_ms: int | None = None,
        manual_invalidate: bool = False,
    ) -> CacheInvalidationDecision:
        self._identity_policy.require(cached)
        if requested is not None:
            self._identity_policy.require(requested)
            if (
                requested.layer != cached.layer
                or requested.namespace != cached.namespace
                or requested.artifact_id != cached.artifact_id
            ):
                raise CacheObservatoryError(
                    "invalidation comparison requires the same targeted cache artifact"
                )
        if observed_at_ms is not None and (
            isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, int)
            or observed_at_ms < 0
        ):
            raise CacheObservatoryError("observed_at_ms must be a non-negative integer")
        reasons: list[CacheInvalidationReason] = []
        if requested is not None:
            comparisons = (
                (
                    "source_hash",
                    CacheInvalidationReason.SOURCE_CHANGED,
                ),
                ("prompt_version", CacheInvalidationReason.PROMPT_CHANGED),
                ("model", CacheInvalidationReason.MODEL_CHANGED),
                ("route_identity", CacheInvalidationReason.ROUTE_CHANGED),
                ("evidence_hash", CacheInvalidationReason.EVIDENCE_CHANGED),
            )
            for field, reason in comparisons:
                if getattr(cached, field) != getattr(requested, field):
                    reasons.append(reason)
        if (
            cached.expires_at_ms is not None
            and observed_at_ms is not None
            and cached.expires_at_ms <= observed_at_ms
        ):
            reasons.append(CacheInvalidationReason.TTL)
        if manual_invalidate:
            reasons.append(CacheInvalidationReason.MANUAL_INVALIDATE)
        unique_reasons = tuple(dict.fromkeys(reasons))
        return CacheInvalidationDecision(
            invalidate=bool(unique_reasons),
            reasons=unique_reasons,
            layer=cached.layer.value,
            namespace=cached.namespace,
            artifact_id=cached.artifact_id,
            target_identity_key=self._identity_policy.cache_key(cached),
            replacement_identity_key=(
                self._identity_policy.cache_key(requested)
                if requested is not None
                else None
            ),
        )
