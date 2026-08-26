"""Business instrumentation adapters for jobs, documents, Teacher and providers."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator, Mapping

from .context import correlation_scope, current_correlation
from .metrics import registry
from .runtime import business_span
from .slo import error_budgets


_queue_lock = threading.RLock()
_queued_at: dict[str, dict[str, float]] = {}


def _queue_name(kind: object) -> str:
    value = str(kind or "default").casefold()
    if "document" in value:
        return "documents"
    if "knowledge" in value:
        return "knowledge"
    if "video" in value:
        return "video"
    return "default"


def _refresh_queue(queue: str) -> None:
    with _queue_lock:
        jobs = _queued_at.get(queue, {})
        age = max((time.time() - created for created in jobs.values()), default=0.0)
        registry.set("mentorflow_job_queue_depth", len(jobs), queue=queue)
        registry.set("mentorflow_job_oldest_age_seconds", max(0.0, age), queue=queue)


def record_job_queued(job_id: str, kind: str) -> None:
    queue = _queue_name(kind)
    with _queue_lock:
        _queued_at.setdefault(queue, {})[str(job_id)] = time.time()
    _refresh_queue(queue)


@contextmanager
def observe_job(job_id: str, kind: str, *, workspace_id: str | None = None) -> Iterator[None]:
    queue = _queue_name(kind)
    with _queue_lock:
        _queued_at.setdefault(queue, {}).pop(str(job_id), None)
    _refresh_queue(queue)
    attributes = {"job.queue": queue, "job.id": str(job_id)[:128]}
    try:
        with correlation_scope(job_id=job_id, workspace_id=workspace_id, feature="jobs"):
            with business_span("mentorflow.job.execute", attributes):
                yield
    except Exception:
        registry.add("mentorflow_job_failures_total", queue=queue, reason="unhandled")
        error_budgets.record("job_success", False)
        raise
    else:
        error_budgets.record("job_success", True)


def record_document_result(result: object, *, elapsed_seconds: float) -> None:
    raw_status = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", "failed"))).casefold()
    status = raw_status if raw_status in {"success", "partial", "failed", "interrupted"} else "failed"
    registry.observe("mentorflow_document_processing_duration_seconds", max(0.0, elapsed_seconds), status=status)
    receipts = tuple(getattr(result, "receipts", ()) or ())
    for receipt in receipts:
        raw_item_status = str(getattr(getattr(receipt, "status", None), "value", getattr(receipt, "status", "failed"))).casefold()
        item_status = {
            "skipped_duplicate": "duplicate",
            "unsupported": "unsupported",
            "limited": "limited",
            "success": "success",
            "failed": "failed",
        }.get(raw_item_status, "interrupted" if status == "interrupted" else "failed")
        raw_stage = str(getattr(getattr(receipt, "stage", None), "value", getattr(receipt, "stage", "complete"))).casefold()
        registry.add("mentorflow_document_processed_total", stage=raw_stage, status=item_status)
    error_budgets.record("document_completion", status in {"success", "partial"})


def record_teacher_turn(*, route: str, status: str, ttft_ms: float | None = None, total_ms: float | None = None) -> None:
    normalized_route = "teacher-deep" if "deep" in str(route).casefold() else "teacher-fast" if "fast" in str(route).casefold() else "unknown"
    normalized_status = status if status in {"success", "failed", "cancelled", "timeout"} else "failed"
    registry.add("mentorflow_teacher_turns_total", route=normalized_route, status=normalized_status)
    if ttft_ms is not None:
        registry.observe("mentorflow_teacher_ttft_seconds", max(0.0, float(ttft_ms) / 1000.0), route=normalized_route)
    if total_ms is not None:
        registry.observe("mentorflow_teacher_total_seconds", max(0.0, float(total_ms) / 1000.0), route=normalized_route)
    error_budgets.record("teacher_availability", normalized_status == "success")


def _provider_route(value: object) -> str:
    normalized = str(value or "unknown").strip().casefold().replace("_", "-")
    if normalized.startswith("openai"):
        return "openai-compatible"
    return normalized


def record_provider_receipt(receipt: object) -> None:
    route = _provider_route(getattr(receipt, "provider", "unknown"))
    success = bool(getattr(receipt, "success", False))
    if bool(getattr(receipt, "provider_called", True)):
        registry.add("mentorflow_provider_calls_total", provider_route=route, status="success" if success else "failed")
    cost = getattr(receipt, "estimated_cost_usd", None)
    classification = "estimated" if cost is not None else "unavailable"
    registry.add("mentorflow_provider_cost_usd_total", float(cost or 0.0), provider_route=route, cost_classification=classification)
    cache_hit = getattr(receipt, "local_result_cache_hit", None)
    cache_corrupt = bool(getattr(receipt, "local_cache_corrupt", False))
    result = "corrupt" if cache_corrupt else "hit" if cache_hit is True else "miss" if cache_hit is False else "unknown"
    registry.add("mentorflow_cache_requests_total", layer="result", result=result)
    provider_cache_ratio = getattr(receipt, "cache_hit_ratio", None)
    if provider_cache_ratio is not None:
        registry.add("mentorflow_cache_requests_total", layer="provider", result="hit" if float(provider_cache_ratio) > 0 else "miss")


def record_db_pool(*, size: int, checked_in: int, checked_out: int, overflow: int) -> None:
    for state, value in {"size": size, "checked_in": checked_in, "checked_out": checked_out, "overflow": overflow}.items():
        registry.set("mentorflow_db_pool_connections", max(0, int(value)), state=state)


def record_rls_denial(resource: str) -> None:
    registry.add("mentorflow_rls_denials_total", resource=resource)


def record_authorization_denial(feature: str, reason: str) -> None:
    registry.add("mentorflow_authorization_denials_total", feature=feature, reason=reason)


def record_auth_attempt(success: bool, *, denied: bool = False) -> None:
    registry.add("mentorflow_auth_attempts_total", result="denied" if denied else "success" if success else "failed")
    error_budgets.record("auth_success", success)


def record_backup_freshness(component: str, age_seconds: float, *, maximum_age_seconds: float = 86_400) -> None:
    registry.set("mentorflow_backup_freshness_seconds", max(0.0, age_seconds), component=component)
    error_budgets.record("backup_freshness", age_seconds <= maximum_age_seconds)


__all__ = [
    "observe_job",
    "record_auth_attempt",
    "record_authorization_denial",
    "record_backup_freshness",
    "record_db_pool",
    "record_document_result",
    "record_job_queued",
    "record_provider_receipt",
    "record_rls_denial",
    "record_teacher_turn",
]
