from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.providers.pricing_deepseek import (
    DEEPSEEK_OFFICIAL_PRICING_URL,
    DEEPSEEK_PRICING_CATALOG,
    DEEPSEEK_PRICING_RETRIEVED_AT,
    DEEPSEEK_V4_FLASH_OFF_PEAK,
    DEEPSEEK_V4_FLASH_PEAK,
    DEEPSEEK_V4_FLASH_PRICING_VERSION,
)


def test_deepseek_snapshot_uses_current_official_usd_prices() -> None:
    assert DEEPSEEK_OFFICIAL_PRICING_URL == (
        "https://api-docs.deepseek.com/quick_start/pricing/"
    )
    assert DEEPSEEK_V4_FLASH_OFF_PEAK.currency == "USD"
    assert DEEPSEEK_V4_FLASH_OFF_PEAK.input_cache_hit_per_1m == Decimal("0.007")
    assert DEEPSEEK_V4_FLASH_OFF_PEAK.input_cache_miss_per_1m == Decimal("0.22")
    assert DEEPSEEK_V4_FLASH_OFF_PEAK.output_per_1m == Decimal("0.66")
    assert DEEPSEEK_V4_FLASH_PEAK.input_cache_hit_per_1m == Decimal("0.014")
    assert DEEPSEEK_V4_FLASH_PEAK.input_cache_miss_per_1m == Decimal("0.44")
    assert DEEPSEEK_V4_FLASH_PEAK.output_per_1m == Decimal("1.32")
    assert DEEPSEEK_PRICING_RETRIEVED_AT.tzinfo == timezone.utc
    assert DEEPSEEK_V4_FLASH_OFF_PEAK.pricing_version == (
        DEEPSEEK_V4_FLASH_PRICING_VERSION
    )


@pytest.mark.parametrize(
    ("occurred_at", "expected_tier"),
    [
        (datetime(2026, 8, 25, 1, 30, tzinfo=timezone.utc), "peak"),
        (datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc), "off_peak"),
        (datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc), "peak"),
        (datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc), "off_peak"),
    ],
)
def test_deepseek_peak_and_off_peak_tiers_are_time_resolved(
    occurred_at: datetime,
    expected_tier: str,
) -> None:
    snapshot = DEEPSEEK_PRICING_CATALOG.resolve(
        provider="openai_compatible",
        model="deepseek-v4-flash",
        occurred_at=occurred_at,
    )

    assert snapshot is not None
    assert snapshot.rate_tier == expected_tier


def test_pricing_public_payload_has_no_secret_or_account_fields() -> None:
    payload = DEEPSEEK_V4_FLASH_OFF_PEAK.to_public_dict()
    serialized = json.dumps(payload, sort_keys=True).casefold()

    assert set(payload) == {
        "provider",
        "provider_aliases",
        "model",
        "currency",
        "input_cache_hit_per_1m",
        "input_cache_miss_per_1m",
        "output_per_1m",
        "cache_write_per_1m",
        "cache_storage_per_1m",
        "effective_from",
        "retrieved_at",
        "source",
        "pricing_version",
        "input_pricing_mode",
        "rate_tier",
        "applicable_weekdays_utc",
        "applicable_time_windows_utc",
    }
    for forbidden in ("api_key", "credential", "account_balance", "user_bill"):
        assert forbidden not in serialized


def test_pricing_contract_rejects_binary_float_money() -> None:
    with pytest.raises(TypeError):
        replace(
            DEEPSEEK_V4_FLASH_OFF_PEAK,
            input_cache_hit_per_1m=0.007,  # type: ignore[arg-type]
        )
