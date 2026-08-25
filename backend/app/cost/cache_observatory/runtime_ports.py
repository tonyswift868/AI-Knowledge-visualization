from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .contracts import CacheEvent, CacheObservatoryError


class CacheObservationSource(str, Enum):
    BUDGETED_EXECUTION = "BUDGETED_EXECUTION"
    TEACHER_TRACE = "TEACHER_TRACE"
    REVIEW_TRACE = "REVIEW_TRACE"


@runtime_checkable
class CacheEventSourcePort(Protocol):
    @property
    def cache_trace_id(self) -> str: ...

    def iter_cache_events(self) -> Iterable[CacheEvent]: ...


@runtime_checkable
class CacheEventSinkPort(Protocol):
    def extend(self, events: tuple[CacheEvent, ...] | list[CacheEvent]) -> int: ...


@dataclass(frozen=True, slots=True)
class CacheObservationReceipt:
    source: CacheObservationSource
    trace_id: str
    received_event_count: int
    accepted_event_count: int
    duplicate_event_count: int

    def __post_init__(self) -> None:
        for name in (
            "received_event_count",
            "accepted_event_count",
            "duplicate_event_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CacheObservatoryError(f"{name} must be a non-negative integer")
        if (
            self.accepted_event_count + self.duplicate_event_count
            != self.received_event_count
        ):
            raise CacheObservatoryError("observation receipt counts must reconcile")


class ExecutionObservatoryAdapter:
    """Connect runtime cache traces through structural ports only.

    This module deliberately knows nothing about BudgetedExecution, Teacher,
    or Review implementation classes. Integration code supplies a structural
    source port; the adapter validates correlation and writes only CacheEvent.
    """

    def __init__(self, sink: CacheEventSinkPort) -> None:
        if not isinstance(sink, CacheEventSinkPort):
            raise TypeError("sink must implement CacheEventSinkPort")
        self._sink = sink

    def _observe(
        self,
        source_kind: CacheObservationSource,
        source: CacheEventSourcePort,
    ) -> CacheObservationReceipt:
        if not isinstance(source, CacheEventSourcePort):
            raise TypeError("source must implement CacheEventSourcePort")
        trace_id = str(source.cache_trace_id or "").strip()
        if not trace_id:
            raise CacheObservatoryError("cache source trace id must not be empty")
        events = tuple(source.iter_cache_events())
        if any(not isinstance(event, CacheEvent) for event in events):
            raise TypeError("cache source must emit CacheEvent values")
        if any(event.trace_id != trace_id for event in events):
            raise CacheObservatoryError(
                "cache event trace id must match its runtime source"
            )
        accepted = self._sink.extend(events)
        if (
            isinstance(accepted, bool)
            or not isinstance(accepted, int)
            or not 0 <= accepted <= len(events)
        ):
            raise CacheObservatoryError(
                "cache event sink returned an invalid accepted count"
            )
        return CacheObservationReceipt(
            source=source_kind,
            trace_id=trace_id,
            received_event_count=len(events),
            accepted_event_count=accepted,
            duplicate_event_count=len(events) - accepted,
        )

    def observe_budgeted_execution(
        self, source: CacheEventSourcePort
    ) -> CacheObservationReceipt:
        return self._observe(CacheObservationSource.BUDGETED_EXECUTION, source)

    def observe_teacher_trace(
        self, source: CacheEventSourcePort
    ) -> CacheObservationReceipt:
        return self._observe(CacheObservationSource.TEACHER_TRACE, source)

    def observe_review_trace(
        self, source: CacheEventSourcePort
    ) -> CacheObservationReceipt:
        return self._observe(CacheObservationSource.REVIEW_TRACE, source)
