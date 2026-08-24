from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import Iterable
from urllib.parse import urlparse


class InputTokenPricingMode(str, Enum):
    """How a Provider pricing contract maps reported input tokens to prices."""

    CACHE_HIT_MISS_REQUIRED = "cache_hit_miss_required"
    TOTAL_INPUT_AT_MISS_RATE = "total_input_at_miss_rate"


@dataclass(frozen=True)
class UtcTimeWindow:
    """Half-open UTC time range used by a time-tiered pricing snapshot."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("UTC pricing window times must be timezone-naive")
        if self.start == self.end:
            raise ValueError("UTC pricing window cannot cover zero hours")

    def contains(self, value: time) -> bool:
        candidate = value.replace(tzinfo=None)
        if self.start < self.end:
            return self.start <= candidate < self.end
        return candidate >= self.start or candidate < self.end

    def to_public_dict(self) -> dict[str, str]:
        return {
            "start": self.start.isoformat(timespec="minutes"),
            "end": self.end.isoformat(timespec="minutes"),
        }


def _require_decimal(name: str, value: Decimal | None) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class ProviderPricingSnapshot:
    """Immutable, versioned Provider price facts separated from cost formulas."""

    provider: str
    model: str
    currency: str
    input_cache_hit_per_1m: Decimal
    input_cache_miss_per_1m: Decimal
    output_per_1m: Decimal
    effective_from: datetime
    retrieved_at: datetime
    source: str
    pricing_version: str
    input_pricing_mode: InputTokenPricingMode = (
        InputTokenPricingMode.CACHE_HIT_MISS_REQUIRED
    )
    cache_write_per_1m: Decimal | None = None
    cache_storage_per_1m: Decimal | None = None
    rate_tier: str = "standard"
    provider_aliases: tuple[str, ...] = ()
    applicable_weekdays_utc: tuple[int, ...] = ()
    applicable_time_windows_utc: tuple[UtcTimeWindow, ...] = ()

    def __post_init__(self) -> None:
        for name in ("provider", "model", "currency", "source", "pricing_version"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        parsed_source = urlparse(self.source)
        if parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
            raise ValueError("source must be an absolute HTTP(S) URL")
        for name in (
            "input_cache_hit_per_1m",
            "input_cache_miss_per_1m",
            "output_per_1m",
            "cache_write_per_1m",
            "cache_storage_per_1m",
        ):
            _require_decimal(name, getattr(self, name))
        if (
            self.input_pricing_mode == InputTokenPricingMode.CACHE_HIT_MISS_REQUIRED
            and self.input_cache_hit_per_1m > self.input_cache_miss_per_1m
        ):
            raise ValueError("cache-hit input price cannot exceed cache-miss price")
        _require_aware_datetime("effective_from", self.effective_from)
        _require_aware_datetime("retrieved_at", self.retrieved_at)
        if not str(self.rate_tier).strip():
            raise ValueError("rate_tier must not be empty")
        if any(day < 0 or day > 6 for day in self.applicable_weekdays_utc):
            raise ValueError("applicable weekdays must use Python's 0..6 convention")
        if bool(self.applicable_weekdays_utc) != bool(
            self.applicable_time_windows_utc
        ):
            raise ValueError(
                "weekday-scoped pricing requires both weekdays and UTC windows"
            )

    @property
    def accepted_provider_names(self) -> frozenset[str]:
        return frozenset(
            str(value).strip().casefold()
            for value in (self.provider, *self.provider_aliases)
            if str(value).strip()
        )

    @property
    def is_time_scoped(self) -> bool:
        return bool(self.applicable_time_windows_utc)

    def applies_at(self, occurred_at: datetime) -> bool:
        _require_aware_datetime("occurred_at", occurred_at)
        occurred_utc = occurred_at.astimezone(timezone.utc)
        if occurred_utc < self.effective_from.astimezone(timezone.utc):
            return False
        if not self.is_time_scoped:
            return True
        if occurred_utc.weekday() not in self.applicable_weekdays_utc:
            return False
        return any(
            window.contains(occurred_utc.time())
            for window in self.applicable_time_windows_utc
        )

    def to_public_dict(self) -> dict[str, object]:
        """Return the explicit non-secret fields suitable for fixtures or reports."""

        return {
            "provider": self.provider,
            "provider_aliases": list(self.provider_aliases),
            "model": self.model,
            "currency": self.currency,
            "input_cache_hit_per_1m": str(self.input_cache_hit_per_1m),
            "input_cache_miss_per_1m": str(self.input_cache_miss_per_1m),
            "output_per_1m": str(self.output_per_1m),
            "cache_write_per_1m": (
                str(self.cache_write_per_1m)
                if self.cache_write_per_1m is not None
                else None
            ),
            "cache_storage_per_1m": (
                str(self.cache_storage_per_1m)
                if self.cache_storage_per_1m is not None
                else None
            ),
            "effective_from": self.effective_from.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "source": self.source,
            "pricing_version": self.pricing_version,
            "input_pricing_mode": self.input_pricing_mode.value,
            "rate_tier": self.rate_tier,
            "applicable_weekdays_utc": list(self.applicable_weekdays_utc),
            "applicable_time_windows_utc": [
                window.to_public_dict()
                for window in self.applicable_time_windows_utc
            ],
        }


class ProviderPricingCatalog:
    """Resolve the latest applicable snapshot without embedding price values."""

    def __init__(self, snapshots: Iterable[ProviderPricingSnapshot]):
        self._snapshots = tuple(snapshots)

    @property
    def snapshots(self) -> tuple[ProviderPricingSnapshot, ...]:
        return self._snapshots

    def resolve(
        self,
        *,
        provider: str,
        model: str,
        occurred_at: datetime,
    ) -> ProviderPricingSnapshot | None:
        normalized_provider = str(provider or "").strip().casefold()
        normalized_model = str(model or "").strip().casefold()
        if not normalized_provider or not normalized_model:
            return None
        _require_aware_datetime("occurred_at", occurred_at)
        candidates = [
            snapshot
            for snapshot in self._snapshots
            if normalized_provider in snapshot.accepted_provider_names
            and normalized_model == snapshot.model.strip().casefold()
            and snapshot.applies_at(occurred_at)
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda snapshot: (
                snapshot.effective_from.astimezone(timezone.utc),
                snapshot.is_time_scoped,
                snapshot.retrieved_at.astimezone(timezone.utc),
                snapshot.pricing_version,
            ),
        )
