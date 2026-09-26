"""Small, privacy-safe telemetry boundary for learning model calls."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import threading, time, uuid
from typing import Any, Mapping

@dataclass(frozen=True)
class ModelCallEvent:
    event_id: str; request_id: str; occurred_at: str; state: str; account_id: str; plan_key: str; session_id: str; task_id: str; role: str; provider: str; model: str; latency_ms: int | None = None; error_code: str | None = None
    def as_dict(self) -> dict[str, Any]: return asdict(self)

class TelemetryBuffer:
    def __init__(self, *, limit: int = 512):
        if type(limit) is not int or not 1 <= limit <= 10000: raise ValueError("telemetry limit must be between 1 and 10000")
        self._limit, self._lock, self._events = limit, threading.Lock(), []
    def record(self, event: ModelCallEvent) -> None:
        if not isinstance(event, ModelCallEvent): raise TypeError("event must be ModelCallEvent")
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._limit: del self._events[: len(self._events) - self._limit]
    def snapshot(self) -> tuple[ModelCallEvent, ...]:
        with self._lock: return tuple(self._events)

def _text(value: Any, default: str = "") -> str:
    return value.strip()[:256] if isinstance(value, str) else default

def begin_model_call(*, account_id: str, metadata: Mapping[str, Any] | None, provider: str, model: str):
    metadata = metadata if isinstance(metadata, Mapping) else {}
    request_id = _text(metadata.get("request_id")) or uuid.uuid4().hex
    return request_id, time.perf_counter(), ModelCallEvent(event_id=uuid.uuid4().hex, request_id=request_id, occurred_at=datetime.now(timezone.utc).isoformat(), state="started", account_id=_text(account_id), plan_key=_text(metadata.get("plan_key")), session_id=_text(metadata.get("session_id")), task_id=_text(metadata.get("task_id")), role=_text(metadata.get("role"), "UNKNOWN") or "UNKNOWN", provider=_text(metadata.get("provider"), provider) or provider, model=_text(metadata.get("model"), model) or model)

def finish_model_call(started: ModelCallEvent, *, elapsed: float, state: str, error_code: str | None = None) -> ModelCallEvent:
    return ModelCallEvent(event_id=started.event_id, request_id=started.request_id, occurred_at=datetime.now(timezone.utc).isoformat(), state=state, account_id=started.account_id, plan_key=started.plan_key, session_id=started.session_id, task_id=started.task_id, role=started.role, provider=started.provider, model=started.model, latency_ms=max(0, int(round(elapsed * 1000))), error_code=_text(error_code) or None)

def complete_with_metadata(client: Any, messages: list[dict[str, str]], *, cancel: Any,
                           metadata: Mapping[str, Any] | None = None) -> str:
    """Call a model while keeping older injected doubles compatible."""
    kwargs = {"cancel": cancel}
    if getattr(client, "supports_metadata", False):
        kwargs["metadata"] = dict(metadata or {})
    return client.complete(messages, **kwargs)

__all__ = ["ModelCallEvent", "TelemetryBuffer", "begin_model_call", "finish_model_call", "complete_with_metadata"]
