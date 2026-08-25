from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .contracts import CacheIdentity, CacheLayer, CacheObservatoryError


class CacheIdentityPolicyViolation(CacheObservatoryError):
    pass


@dataclass(frozen=True, slots=True)
class LayerIdentityPolicy:
    layer: CacheLayer
    included_fields: tuple[str, ...]
    required_fields: tuple[str, ...]
    conditional_provider_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CacheIdentityValidation:
    valid: bool
    layer: CacheLayer
    policy_version: str
    missing_fields: tuple[str, ...]


class CacheIdentityPolicy:
    """Versioned, layer-specific cache-key policy.

    `namespace` and `artifact_id` always scope the key. Only the fields listed
    for the layer participate, so dynamic Teacher inputs cannot accidentally
    destroy Provider stable-prefix reuse while versioned inputs still prevent
    stale hits.
    """

    VERSION = "cache-identity-policy-v1"

    def __init__(self, *, version: str = VERSION) -> None:
        normalized = str(version or "").strip()
        if not normalized:
            raise ValueError("identity policy version must not be empty")
        self._version = normalized
        self._policies = {
            CacheLayer.PROVIDER_PROMPT_CACHE: LayerIdentityPolicy(
                layer=CacheLayer.PROVIDER_PROMPT_CACHE,
                included_fields=(
                    "provider",
                    "model",
                    "prompt_version",
                    "route_identity",
                    "stable_prefix_hash",
                ),
                required_fields=(
                    "provider",
                    "model",
                    "prompt_version",
                    "route_identity",
                    "stable_prefix_hash",
                ),
            ),
            CacheLayer.LOCAL_RESULT_CACHE: LayerIdentityPolicy(
                layer=CacheLayer.LOCAL_RESULT_CACHE,
                included_fields=(
                    "provider",
                    "model",
                    "prompt_version",
                    "route_identity",
                    "stable_prefix_hash",
                    "dynamic_context_hash",
                    "source_hash",
                    "evidence_hash",
                    "question_hash",
                    "evaluation_version",
                ),
                required_fields=(
                    "provider",
                    "model",
                    "prompt_version",
                    "route_identity",
                    "dynamic_context_hash",
                ),
            ),
            CacheLayer.EVIDENCE_CACHE: LayerIdentityPolicy(
                layer=CacheLayer.EVIDENCE_CACHE,
                included_fields=("source_hash", "evidence_hash"),
                required_fields=("source_hash", "evidence_hash"),
            ),
            CacheLayer.PARSE_CACHE: LayerIdentityPolicy(
                layer=CacheLayer.PARSE_CACHE,
                included_fields=("source_hash", "evidence_hash", "parser_version"),
                required_fields=("source_hash", "evidence_hash", "parser_version"),
            ),
            CacheLayer.QUESTION_BANK_CACHE: LayerIdentityPolicy(
                layer=CacheLayer.QUESTION_BANK_CACHE,
                included_fields=("question_hash", "question_bank_version"),
                required_fields=("question_hash", "question_bank_version"),
            ),
            CacheLayer.EVALUATION_ARTIFACT_CACHE: LayerIdentityPolicy(
                layer=CacheLayer.EVALUATION_ARTIFACT_CACHE,
                included_fields=(
                    "question_hash",
                    "dynamic_context_hash",
                    "source_hash",
                    "evidence_hash",
                    "evaluation_version",
                    "provider",
                    "model",
                    "prompt_version",
                    "route_identity",
                ),
                required_fields=(
                    "question_hash",
                    "dynamic_context_hash",
                    "evidence_hash",
                    "evaluation_version",
                ),
                conditional_provider_fields=(
                    "model",
                    "prompt_version",
                    "route_identity",
                ),
            ),
        }

    @property
    def version(self) -> str:
        return self._version

    def for_layer(self, layer: CacheLayer) -> LayerIdentityPolicy:
        normalized = layer if isinstance(layer, CacheLayer) else CacheLayer(layer)
        return self._policies[normalized]

    def validate(self, identity: CacheIdentity) -> CacheIdentityValidation:
        if not isinstance(identity, CacheIdentity):
            raise TypeError("identity must be CacheIdentity")
        policy = self.for_layer(identity.layer)
        required = list(policy.required_fields)
        if identity.provider is not None:
            required.extend(policy.conditional_provider_fields)
        missing = [field for field in required if getattr(identity, field) is None]
        if identity.identity_policy_version != self.version:
            missing.append("identity_policy_version")
        return CacheIdentityValidation(
            valid=not missing,
            layer=identity.layer,
            policy_version=self.version,
            missing_fields=tuple(dict.fromkeys(missing)),
        )

    def require(self, identity: CacheIdentity) -> None:
        validation = self.validate(identity)
        if not validation.valid:
            raise CacheIdentityPolicyViolation(
                f"{identity.layer.value} identity is missing policy fields: "
                + ", ".join(validation.missing_fields)
            )

    def canonical_payload(self, identity: CacheIdentity) -> dict[str, object]:
        self.require(identity)
        policy = self.for_layer(identity.layer)
        return {
            "identity_policy_version": self.version,
            "layer": identity.layer.value,
            "namespace": identity.namespace,
            "artifact_id": identity.artifact_id,
            **{field: getattr(identity, field) for field in policy.included_fields},
        }

    def cache_key(self, identity: CacheIdentity) -> str:
        encoded = json.dumps(
            self.canonical_payload(identity),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


DEFAULT_CACHE_IDENTITY_POLICY = CacheIdentityPolicy()
