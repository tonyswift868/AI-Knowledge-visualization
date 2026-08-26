"""Deterministic bounded retry policy for workflow failures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import FailureClass, require_aware
from .redaction import redact_text


@dataclass(frozen=True, slots=True)
class WorkflowFailure(Exception):
    failure_class: FailureClass
    error_code: str
    safe_message: str
    retry_after_seconds: float | None = None

    def __str__(self) -> str:
        return f"{self.error_code}: {self.safe_message}"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    retry_at: datetime | None
    error_redacted: str


class RetryPolicy:
    def __init__(
        self,
        *,
        base_seconds: float = 2.0,
        maximum_seconds: float = 15 * 60,
        jitter_ratio: float = 0.20,
    ) -> None:
        if base_seconds <= 0 or maximum_seconds < base_seconds:
            raise ValueError("retry bounds are invalid")
        if not 0 <= jitter_ratio <= 0.5:
            raise ValueError("jitter_ratio must be between 0 and 0.5")
        self.base_seconds = base_seconds
        self.maximum_seconds = maximum_seconds
        self.jitter_ratio = jitter_ratio

    def decide(
        self,
        *,
        job_id: str,
        attempt: int,
        max_attempts: int,
        failure: WorkflowFailure,
        now: datetime,
    ) -> RetryDecision:
        now = require_aware(now, "now")
        retryable = failure.failure_class in {
            FailureClass.RETRYABLE,
            FailureClass.RATE_LIMITED,
            FailureClass.PROVIDER_FAILURE,
            FailureClass.WORKER_LOST,
        }
        message = redact_text(failure.safe_message)
        if not retryable or attempt >= max_attempts:
            return RetryDecision(retry=False, retry_at=None, error_redacted=message)
        if failure.retry_after_seconds is not None:
            bounded = min(self.maximum_seconds, max(0.0, failure.retry_after_seconds))
        else:
            bounded = min(self.maximum_seconds, self.base_seconds * (2 ** max(0, attempt - 1)))
        digest = hashlib.sha256(f"{job_id}:{attempt}".encode("utf-8")).digest()
        unit = int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)
        factor = 1 - self.jitter_ratio + (2 * self.jitter_ratio * unit)
        delay = min(self.maximum_seconds, max(0.0, bounded * factor))
        return RetryDecision(
            retry=True,
            retry_at=now + timedelta(seconds=delay),
            error_redacted=message,
        )


__all__ = ["RetryDecision", "RetryPolicy", "WorkflowFailure"]
