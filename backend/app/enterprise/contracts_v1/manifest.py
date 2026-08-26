"""Deterministic schema hashing for the frozen Enterprise Context v1 pack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .models import (
    ENTERPRISE_CONTEXT_CONTRACT_VERSION,
    AccessDecision,
    ActorContext,
    AuditEnvelope,
    ResourceScope,
    TenantContext,
)


CONTRACT_HASH_MANIFEST_FILENAME = "contract_hash_manifest.json"
_CONTRACT_MODELS = {
    "AccessDecision": AccessDecision,
    "ActorContext": ActorContext,
    "AuditEnvelope": AuditEnvelope,
    "ResourceScope": ResourceScope,
    "TenantContext": TenantContext,
}
_EXPECTED_HASH_KEYS = frozenset({*_CONTRACT_MODELS, "ModeBoundary", "bundle"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compute_contract_hashes() -> dict[str, str]:
    model_hashes = {
        name: hashlib.sha256(_canonical_json_bytes(model.model_json_schema())).hexdigest()
        for name, model in sorted(_CONTRACT_MODELS.items())
    }
    mode_boundary_path = Path(__file__).with_name("mode_boundary.json")
    mode_boundary = json.loads(mode_boundary_path.read_text(encoding="utf-8"))
    component_hashes = {
        **model_hashes,
        "ModeBoundary": hashlib.sha256(_canonical_json_bytes(mode_boundary)).hexdigest(),
    }
    bundle_hash = hashlib.sha256(_canonical_json_bytes(component_hashes)).hexdigest()
    return {**component_hashes, "bundle": bundle_hash}


def load_frozen_contract_hashes() -> dict[str, Any]:
    path = Path(__file__).with_name(CONTRACT_HASH_MANIFEST_FILENAME)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("contract hash manifest must be a JSON object")
    if payload.get("manifest_version") != "enterprise-contract-hashes.v1":
        raise ValueError("unsupported contract hash manifest version")
    if payload.get("contract_version") != ENTERPRISE_CONTEXT_CONTRACT_VERSION:
        raise ValueError("contract hash manifest version does not match contract")
    if payload.get("hash_algorithm") != "sha256":
        raise ValueError("contract hash manifest must use sha256")
    hashes = payload.get("hashes")
    if not isinstance(hashes, dict) or set(hashes) != _EXPECTED_HASH_KEYS:
        raise ValueError("contract hash manifest contains an unexpected hash key set")
    if any(not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None for value in hashes.values()):
        raise ValueError("contract hash manifest contains an invalid sha256 value")
    return payload


def verify_frozen_contract_hashes() -> bool:
    frozen = load_frozen_contract_hashes()
    return frozen.get("hashes") == compute_contract_hashes()


__all__ = [
    "CONTRACT_HASH_MANIFEST_FILENAME",
    "compute_contract_hashes",
    "load_frozen_contract_hashes",
    "verify_frozen_contract_hashes",
]
