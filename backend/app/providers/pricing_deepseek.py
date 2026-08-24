from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

from app.providers.pricing import (
    InputTokenPricingMode,
    ProviderPricingCatalog,
    ProviderPricingSnapshot,
    UtcTimeWindow,
)


DEEPSEEK_OFFICIAL_PRICING_URL = (
    "https://api-docs.deepseek.com/quick_start/pricing/"
)
DEEPSEEK_PRICING_RETRIEVED_AT = datetime(
    2026,
    8,
    24,
    17,
    58,
    31,
    tzinfo=timezone.utc,
)
DEEPSEEK_V4_FLASH_PRICING_VERSION = (
    "deepseek-v4-flash-0731-usd-2026-08-24"
)

_DEEPSEEK_PROVIDER_ALIASES = ("openai_compatible", "openai-compatible")
_DEEPSEEK_PEAK_WEEKDAYS_UTC = (0, 1, 2, 3, 4)
_DEEPSEEK_PEAK_WINDOWS_UTC = (
    UtcTimeWindow(time(1, 0), time(4, 0)),
    UtcTimeWindow(time(6, 0), time(10, 0)),
)


DEEPSEEK_V4_FLASH_OFF_PEAK = ProviderPricingSnapshot(
    provider="deepseek",
    provider_aliases=_DEEPSEEK_PROVIDER_ALIASES,
    model="deepseek-v4-flash",
    currency="USD",
    input_cache_hit_per_1m=Decimal("0.007"),
    input_cache_miss_per_1m=Decimal("0.22"),
    output_per_1m=Decimal("0.66"),
    effective_from=DEEPSEEK_PRICING_RETRIEVED_AT,
    retrieved_at=DEEPSEEK_PRICING_RETRIEVED_AT,
    source=DEEPSEEK_OFFICIAL_PRICING_URL,
    pricing_version=DEEPSEEK_V4_FLASH_PRICING_VERSION,
    input_pricing_mode=InputTokenPricingMode.CACHE_HIT_MISS_REQUIRED,
    rate_tier="off_peak",
)

DEEPSEEK_V4_FLASH_PEAK = ProviderPricingSnapshot(
    provider="deepseek",
    provider_aliases=_DEEPSEEK_PROVIDER_ALIASES,
    model="deepseek-v4-flash",
    currency="USD",
    input_cache_hit_per_1m=Decimal("0.014"),
    input_cache_miss_per_1m=Decimal("0.44"),
    output_per_1m=Decimal("1.32"),
    effective_from=DEEPSEEK_PRICING_RETRIEVED_AT,
    retrieved_at=DEEPSEEK_PRICING_RETRIEVED_AT,
    source=DEEPSEEK_OFFICIAL_PRICING_URL,
    pricing_version=DEEPSEEK_V4_FLASH_PRICING_VERSION,
    input_pricing_mode=InputTokenPricingMode.CACHE_HIT_MISS_REQUIRED,
    rate_tier="peak",
    applicable_weekdays_utc=_DEEPSEEK_PEAK_WEEKDAYS_UTC,
    applicable_time_windows_utc=_DEEPSEEK_PEAK_WINDOWS_UTC,
)


DEEPSEEK_PRICING_CATALOG = ProviderPricingCatalog(
    (DEEPSEEK_V4_FLASH_OFF_PEAK, DEEPSEEK_V4_FLASH_PEAK)
)
