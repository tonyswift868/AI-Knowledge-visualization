from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from decimal import Decimal

from app.cost.contracts import LocalCacheSavingsBaseline
from app.cost.estimation import CostEstimate, EstimationConfidence
from app.providers.telemetry import ProviderUsageReceipt


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def receipt_payload(receipt: ProviderUsageReceipt) -> dict[str, object]:
    payload = receipt.to_dict()
    for value in payload.values():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("receipt contains a non-finite numeric value")
    return payload


def receipt_payload_json(receipt: ProviderUsageReceipt) -> str:
    return canonical_json(receipt_payload(receipt))


def receipt_payload_hash(receipt: ProviderUsageReceipt) -> str:
    return hashlib.sha256(receipt_payload_json(receipt).encode("utf-8")).hexdigest()


def receipt_from_json(value: str) -> ProviderUsageReceipt:
    return ProviderUsageReceipt(**json.loads(value))


def estimate_to_json(estimate: CostEstimate | None) -> str | None:
    if estimate is None:
        return None
    payload = asdict(estimate)
    for key in ("minimum", "expected", "maximum"):
        payload[key] = str(payload[key])
    payload["confidence"] = estimate.confidence.value
    return canonical_json(payload)


def estimate_from_json(value: str | None) -> CostEstimate | None:
    if value is None:
        return None
    payload = json.loads(value)
    for key in ("minimum", "expected", "maximum"):
        payload[key] = Decimal(payload[key])
    payload["confidence"] = EstimationConfidence(payload["confidence"])
    return CostEstimate(**payload)


def baseline_to_json(value: LocalCacheSavingsBaseline | None) -> str | None:
    if value is None:
        return None
    return canonical_json(
        {
            "amount": str(value.amount),
            "currency": value.currency,
            "estimation_basis": value.estimation_basis,
        }
    )


def baseline_from_json(value: str | None) -> LocalCacheSavingsBaseline | None:
    if value is None:
        return None
    payload = json.loads(value)
    return LocalCacheSavingsBaseline(
        amount=Decimal(payload["amount"]),
        currency=payload["currency"],
        estimation_basis=payload["estimation_basis"],
    )
