from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


PROVIDER_USAGE_SCHEMA_VERSION = "provider_usage_v1"
DEFAULT_TASK_TYPE = "generic"
DEFAULT_PROMPT_VERSION = "unversioned"
MAX_IN_MEMORY_RECEIPTS = 2_048


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    cache_hit_ratio: float | None = None


def normalize_provider_usage(payload: object) -> ProviderUsage:
    """Normalize Provider usage without turning unavailable values into zero.

    DeepSeek exposes prompt-cache counters in its OpenAI-compatible ``usage``
    object. Other adapters may expose token counts but no cache counters. A
    zero hit count is meaningful only when the Provider also reports a cache
    miss count; absent fields remain ``None``.
    """

    body = payload if isinstance(payload, Mapping) else {}
    usage = body.get("usage") if isinstance(body, Mapping) else None
    if not isinstance(usage, Mapping):
        usage_metadata = body.get("usageMetadata") if isinstance(body, Mapping) else None
        if isinstance(usage_metadata, Mapping):
            usage = usage_metadata
        else:
            return ProviderUsage()

    prompt_tokens = _optional_non_negative_int(
        usage.get("prompt_tokens", usage.get("input_tokens", usage.get("promptTokenCount")))
    )
    completion_tokens = _optional_non_negative_int(
        usage.get(
            "completion_tokens",
            usage.get("output_tokens", usage.get("candidatesTokenCount")),
        )
    )
    total_tokens = _optional_non_negative_int(
        usage.get("total_tokens", usage.get("totalTokenCount"))
    )
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    cache_hit_tokens = _optional_non_negative_int(usage.get("prompt_cache_hit_tokens"))
    cache_miss_tokens = _optional_non_negative_int(usage.get("prompt_cache_miss_tokens"))
    cache_hit_ratio: float | None = None
    if cache_hit_tokens is not None and cache_miss_tokens is not None:
        cache_total = cache_hit_tokens + cache_miss_tokens
        if cache_total > 0:
            cache_hit_ratio = cache_hit_tokens / cache_total

    return ProviderUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=cache_hit_tokens,
        prompt_cache_miss_tokens=cache_miss_tokens,
        cache_hit_ratio=cache_hit_ratio,
    )


@dataclass(frozen=True)
class ProviderUsageReceipt:
    provider: str
    model: str
    task_type: str
    request_id: str
    started_at: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    prompt_cache_hit_tokens: int | None
    prompt_cache_miss_tokens: int | None
    cache_hit_ratio: float | None
    local_result_cache_hit: bool | None
    estimated_cost_usd: float | None
    pricing_version: str | None
    success: bool
    error_class: str | None
    prompt_version: str
    schema_version: str = PROVIDER_USAGE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _RequestContext:
    task_type: str = DEFAULT_TASK_TYPE
    request_id: str | None = None
    prompt_version: str = DEFAULT_PROMPT_VERSION
    local_result_cache_hit: bool | None = None


_request_context: ContextVar[_RequestContext] = ContextVar(
    "provider_request_context",
    default=_RequestContext(),
)


@contextmanager
def provider_request_context(
    *,
    task_type: str = DEFAULT_TASK_TYPE,
    request_id: str | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    local_result_cache_hit: bool | None = None,
) -> Iterator[None]:
    """Attach stable business metadata to nested Provider calls.

    Existing callers need no changes and receive the explicit ``generic`` /
    ``unversioned`` defaults. New call sites can opt into a stable task and
    prompt version without changing every Provider method signature.
    """

    normalized_task_type = str(task_type or DEFAULT_TASK_TYPE).strip() or DEFAULT_TASK_TYPE
    normalized_prompt_version = (
        str(prompt_version or DEFAULT_PROMPT_VERSION).strip() or DEFAULT_PROMPT_VERSION
    )
    token = _request_context.set(
        _RequestContext(
            task_type=normalized_task_type,
            request_id=str(request_id or "").strip() or None,
            prompt_version=normalized_prompt_version,
            local_result_cache_hit=(
                local_result_cache_hit
                if isinstance(local_result_cache_hit, bool)
                else None
            ),
        )
    )
    try:
        yield
    finally:
        _request_context.reset(token)


def _durable_jsonl_path() -> Path | None:
    explicit = str(os.getenv("MENTORFLOW_PROVIDER_TELEMETRY_PATH") or "").strip()
    if explicit:
        return Path(explicit)
    user_root = str(os.getenv("MENTORFLOW_USER_DATA_ROOT") or "").strip()
    if not user_root:
        return None
    return Path(user_root) / "telemetry" / "provider-usage.jsonl"


class ProviderTelemetryStore:
    """Thread-safe bounded receipts with optional local JSONL durability.

    The JSONL sink activates only when the application supplies its owned user
    data root (or an explicit telemetry path). Telemetry failures never change
    the Provider result. Receipts contain metrics only: no prompt, URL, key, or
    response content.
    """

    def __init__(self, max_receipts: int = MAX_IN_MEMORY_RECEIPTS):
        self._receipts: deque[ProviderUsageReceipt] = deque(maxlen=max_receipts)
        self._lock = threading.Lock()

    def record(self, receipt: ProviderUsageReceipt) -> None:
        with self._lock:
            self._receipts.append(receipt)
            path = _durable_jsonl_path()
            if path is None:
                return
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(receipt.to_dict(), ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
            except (OSError, TypeError, ValueError):
                # Observability must never make an otherwise valid model call fail.
                return

    def receipts(self) -> list[ProviderUsageReceipt]:
        with self._lock:
            return list(self._receipts)

    def latest(self) -> ProviderUsageReceipt | None:
        with self._lock:
            return self._receipts[-1] if self._receipts else None

    def clear(self) -> None:
        with self._lock:
            self._receipts.clear()


provider_telemetry_store = ProviderTelemetryStore()


@dataclass(frozen=True)
class ProviderRequestTimer:
    provider: str
    model: str
    task_type: str
    request_id: str
    prompt_version: str
    local_result_cache_hit: bool | None
    started_at: str
    started_perf: float

    def finish(
        self,
        *,
        payload: object = None,
        success: bool,
        error_class: str | None = None,
    ) -> ProviderUsageReceipt:
        usage = normalize_provider_usage(payload)
        receipt = ProviderUsageReceipt(
            provider=self.provider,
            model=self.model,
            task_type=self.task_type,
            request_id=self.request_id,
            started_at=self.started_at,
            latency_ms=max(1, int((time.perf_counter() - self.started_perf) * 1000)),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=usage.prompt_cache_miss_tokens,
            cache_hit_ratio=usage.cache_hit_ratio,
            local_result_cache_hit=self.local_result_cache_hit,
            # AI-01 measures raw usage first. Cost stays explicitly
            # unavailable until a separately versioned pricing source exists.
            estimated_cost_usd=None,
            pricing_version=None,
            success=bool(success),
            error_class=str(error_class or "").strip() or None,
            prompt_version=self.prompt_version,
        )
        provider_telemetry_store.record(receipt)
        return receipt


def start_provider_request(*, provider: str, model: str) -> ProviderRequestTimer:
    context = _request_context.get()
    return ProviderRequestTimer(
        provider=str(provider or "unknown").strip() or "unknown",
        model=str(model or "unknown").strip() or "unknown",
        task_type=context.task_type,
        request_id=context.request_id or "req_telemetry_" + secrets.token_hex(10),
        prompt_version=context.prompt_version,
        local_result_cache_hit=context.local_result_cache_hit,
        started_at=datetime.now(timezone.utc).isoformat(),
        started_perf=time.perf_counter(),
    )


def provider_http_error_class(status_code: int) -> str:
    return {
        401: "PROVIDER_AUTH_FAILED",
        403: "PROVIDER_PERMISSION_DENIED",
        404: "PROVIDER_ROUTE_NOT_FOUND",
        429: "PROVIDER_RATE_LIMITED",
    }.get(int(status_code), "PROVIDER_HTTP_ERROR")
