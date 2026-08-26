"""Deterministic schema hashing for the frozen Enterprise Context v1 pack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import AccessDecision, ActorContext, AuditEnvelope, ResourceScope, TenantContext


CONTRACT_HASH_MANIFEST_FILENAME = "contract_hash_manifest.json"
_CONTRACT_MODELS = {
    "AccessDecision": AccessDecision,
    "ActorContext": ActorContext,
    "AuditEnvelope": AuditEnvelope,
    "ResourceScope": ResourceScope,
    "TenantContext": TenantContext,
}


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
