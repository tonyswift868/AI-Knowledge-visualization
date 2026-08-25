"""Atomic JSON adapter for local, caller-scoped student learning state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..contracts import ExampleDepth, ExampleHistory, ExampleStrategy, HintDependency, TeacherMove
from ..student_model import (
    DuplicateEventConflictError,
    StudentLearningEvent,
    StudentLearningEventType,
)
from .reducer import (
    ConceptLearningState,
    LearningStage,
    LearningStatePolicy,
    LearningStateReducer,
    LearningStateTransition,
    ReducerResult,
)


STORE_SCHEMA_VERSION = "atomic-json-student-model.v1"


class StudentStoreError(RuntimeError):
    pass


class StudentStoreCorruptionError(StudentStoreError):
    pass


class StudentStoreIdentityError(StudentStoreError):
    pass


_LOCK_REGISTRY_GUARD = threading.Lock()
_LOCK_REGISTRY: dict[tuple[str, str], threading.RLock] = {}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _parse_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _event_order_key(event: StudentLearningEvent) -> tuple[datetime, str]:
    return event.occurred_at.astimezone(timezone.utc), event.event_id


def _event_to_data(event: StudentLearningEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "student_id": event.student_id,
        "session_id": event.session_id,
        "concept_id": event.concept_id,
        "event_type": event.event_type.value,
        "occurred_at": _utc_text(event.occurred_at),
        "attempt_id": event.attempt_id,
        "detail": event.detail,
        "confidence": event.confidence,
        "evidence_ids": list(event.evidence_ids),
        "teacher_move": None if event.teacher_move is None else event.teacher_move.value,
        "example_id": event.example_id,
        "example_strategy": event.example_strategy.value,
        "example_depth": None if event.example_depth is None else event.example_depth.value,
        "example_effective": event.example_effective,
        "active_learning_seconds": event.active_learning_seconds,
        "assessment_score": event.assessment_score,
        "assessment_passed": event.assessment_passed,
    }


def _event_from_data(data: object) -> StudentLearningEvent:
    if not isinstance(data, dict):
        raise TypeError("stored event must be an object")
    return StudentLearningEvent(
        event_id=data["event_id"],
        student_id=data["student_id"],
        session_id=data["session_id"],
        concept_id=data["concept_id"],
        event_type=StudentLearningEventType(data["event_type"]),
        occurred_at=_parse_utc(data["occurred_at"]),
        attempt_id=data.get("attempt_id"),
        detail=data.get("detail"),
        confidence=data.get("confidence"),
        evidence_ids=tuple(data.get("evidence_ids", ())),
        teacher_move=data.get("teacher_move"),
        example_id=data.get("example_id"),
        example_strategy=data.get("example_strategy", ExampleStrategy.NONE.value),
        example_depth=data.get("example_depth"),
        example_effective=data.get("example_effective"),
        active_learning_seconds=data.get("active_learning_seconds"),
        assessment_score=data.get("assessment_score"),
        assessment_passed=data.get("assessment_passed"),
    )


def _example_to_data(example: ExampleHistory) -> dict[str, object]:
    return {
        "example_id": example.example_id,
        "strategy": example.strategy.value,
        "depth": example.depth.value,
        "effective": example.effective,
    }


def _example_from_data(data: dict[str, object]) -> ExampleHistory:
    return ExampleHistory(
        example_id=data["example_id"],
        strategy=ExampleStrategy(data["strategy"]),
        depth=ExampleDepth(data["depth"]),
        effective=data.get("effective"),
    )


def _state_to_data(state: ConceptLearningState) -> dict[str, object]:
    return {
        "concept_id": state.concept_id,
        "stage": state.stage.value,
        "confidence": state.confidence,
        "self_attested_ever": state.self_attested_ever,
        "independently_verified": state.independently_verified,
        "verification_evidence_ids": list(state.verification_evidence_ids),
        "recent_evidence_ids": list(state.recent_evidence_ids),
        "attempt_count": state.attempt_count,
        "correct_answer_count": state.correct_answer_count,
        "partial_answer_count": state.partial_answer_count,
        "incorrect_answer_count": state.incorrect_answer_count,
        "assessment_result_count": state.assessment_result_count,
        "assessment_pass_count": state.assessment_pass_count,
        "assessment_fail_count": state.assessment_fail_count,
        "hint_request_count": state.hint_request_count,
        "hint_dependency": state.hint_dependency.value,
        "misconception_signal_count": state.misconception_signal_count,
        "misconception_history": list(state.misconception_history),
        "active_misconceptions": list(state.active_misconceptions),
        "recent_mistakes": list(state.recent_mistakes),
        "recent_teaching_moves": [move.value for move in state.recent_teaching_moves],
        "recent_examples": [_example_to_data(item) for item in state.recent_examples],
        "consecutive_correct_answers": state.consecutive_correct_answers,
        "consecutive_failures": state.consecutive_failures,
        "active_learning_seconds": state.active_learning_seconds,
        "last_event_at": _utc_text(state.last_event_at),
        "last_event_id": state.last_event_id,
    }


def _state_from_data(data: dict[str, object]) -> ConceptLearningState:
    return ConceptLearningState(
        concept_id=data["concept_id"],
        stage=LearningStage(data["stage"]),
        confidence=data["confidence"],
        self_attested_ever=data["self_attested_ever"],
        independently_verified=data["independently_verified"],
        verification_evidence_ids=tuple(data["verification_evidence_ids"]),
        recent_evidence_ids=tuple(data["recent_evidence_ids"]),
        attempt_count=data["attempt_count"],
        correct_answer_count=data["correct_answer_count"],
        partial_answer_count=data["partial_answer_count"],
        incorrect_answer_count=data["incorrect_answer_count"],
        assessment_result_count=data["assessment_result_count"],
        assessment_pass_count=data["assessment_pass_count"],
        assessment_fail_count=data["assessment_fail_count"],
        hint_request_count=data["hint_request_count"],
        hint_dependency=HintDependency(data["hint_dependency"]),
        misconception_signal_count=data["misconception_signal_count"],
        misconception_history=tuple(data["misconception_history"]),
        active_misconceptions=tuple(data["active_misconceptions"]),
        recent_mistakes=tuple(data["recent_mistakes"]),
        recent_teaching_moves=tuple(
            TeacherMove(value) for value in data["recent_teaching_moves"]
        ),
        recent_examples=tuple(
            _example_from_data(item) for item in data["recent_examples"]
        ),
        consecutive_correct_answers=data["consecutive_correct_answers"],
        consecutive_failures=data["consecutive_failures"],
        active_learning_seconds=data["active_learning_seconds"],
        last_event_at=_parse_utc(data["last_event_at"]),
        last_event_id=data["last_event_id"],
    )


def _transition_to_data(transition: LearningStateTransition) -> dict[str, object]:
    return {
        "concept_id": transition.concept_id,
        "from_stage": transition.from_stage.value,
        "to_stage": transition.to_stage.value,
        "reason": transition.reason,
        "source_event_ids": list(transition.source_event_ids),
        "confidence": transition.confidence,
        "occurred_at": _utc_text(transition.occurred_at),
        "order_index": transition.order_index,
        "ordering_policy": transition.ordering_policy,
    }


def _transition_from_data(data: dict[str, object]) -> LearningStateTransition:
    return LearningStateTransition(
        concept_id=data["concept_id"],
        from_stage=LearningStage(data["from_stage"]),
        to_stage=LearningStage(data["to_stage"]),
        reason=data["reason"],
        source_event_ids=tuple(data["source_event_ids"]),
        confidence=data["confidence"],
        occurred_at=_parse_utc(data["occurred_at"]),
        order_index=data["order_index"],
        ordering_policy=data["ordering_policy"],
    )


def _result_to_data(result: ReducerResult) -> dict[str, object]:
    return {
        "student_id": result.student_id,
        "session_id": result.session_id,
        "concept_states": [_state_to_data(state) for state in result.concept_states],
        "transitions": [
            _transition_to_data(transition) for transition in result.transitions
        ],
        "applied_event_ids": list(result.applied_event_ids),
        "duplicate_event_ids": list(result.duplicate_event_ids),
        "ordering_policy": result.ordering_policy,
        "last_event_at": _utc_text(result.last_event_at),
    }


def _result_from_data(data: object) -> ReducerResult:
    if not isinstance(data, dict):
        raise TypeError("stored snapshot must be an object")
    return ReducerResult(
        student_id=data["student_id"],
        session_id=data["session_id"],
        concept_states=tuple(_state_from_data(item) for item in data["concept_states"]),
        transitions=tuple(
            _transition_from_data(item) for item in data["transitions"]
        ),
        applied_event_ids=tuple(data["applied_event_ids"]),
        duplicate_event_ids=tuple(data["duplicate_event_ids"]),
        ordering_policy=data["ordering_policy"],
        last_event_at=_parse_utc(data["last_event_at"]),
    )


class AtomicJsonStudentModelStore:
    """Durable event/snapshot store rooted only at a caller-provided path.

    Updates for one student are serialized across store instances in this
    process.  Different students use independent locks.  Cross-process locking
    is intentionally left to the future canonical persistence integration.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        policy: LearningStatePolicy | None = None,
    ) -> None:
        candidate = Path(root)
        if not candidate.is_absolute():
            raise ValueError("store root must be an absolute caller-provided path")
        self.root = candidate.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.reducer = LearningStateReducer(policy)

    def _student_lock(self, student_id: str) -> threading.RLock:
        student_key = hashlib.sha256(student_id.strip().encode("utf-8")).hexdigest()
        key = (str(self.root).casefold(), student_key)
        with _LOCK_REGISTRY_GUARD:
            return _LOCK_REGISTRY.setdefault(key, threading.RLock())

    def scope_path(self, *, student_id: str, session_id: str) -> Path:
        normalized_student_id = student_id.strip()
        normalized_session_id = session_id.strip()
        if not normalized_student_id or not normalized_session_id:
            raise ValueError("student_id and session_id must be non-empty")
        identity = _canonical_json_bytes([normalized_student_id, normalized_session_id])
        digest = hashlib.sha256(identity).hexdigest()
        return self.root / "scopes" / digest[:2] / f"{digest}.json"

    def _before_replace(self, temporary_path: Path, target_path: Path) -> None:
        """Crash-injection seam used by deterministic durability tests."""

    def _atomic_write(self, target: Path, data: dict[str, object]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_canonical_json_bytes(data) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._before_replace(temporary_path, target)
            os.replace(temporary_path, target)
            try:
                directory_descriptor = os.open(target.parent, os.O_RDONLY)
            except OSError:
                directory_descriptor = None
            if directory_descriptor is not None:
                try:
                    os.fsync(directory_descriptor)
                except OSError:
                    pass
                finally:
                    os.close(directory_descriptor)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _wrap_payload(payload: dict[str, object]) -> dict[str, object]:
        wrapped = dict(payload)
        wrapped["checksum"] = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
        return wrapped

    def _read_payload(
        self,
        target: Path,
        *,
        student_id: str,
        session_id: str,
    ) -> dict[str, object]:
        if not target.exists():
            return {
                "schema_version": STORE_SCHEMA_VERSION,
                "student_id": student_id,
                "session_id": session_id,
                "events": [],
                "snapshot": None,
            }
        try:
            wrapped = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(wrapped, dict):
                raise TypeError("store envelope must be an object")
            stored_checksum = wrapped.pop("checksum")
            actual_checksum = hashlib.sha256(_canonical_json_bytes(wrapped)).hexdigest()
            if stored_checksum != actual_checksum:
                raise ValueError("checksum mismatch")
            if wrapped.get("schema_version") != STORE_SCHEMA_VERSION:
                raise ValueError("unsupported store schema")
            if (
                wrapped.get("student_id") != student_id
                or wrapped.get("session_id") != session_id
            ):
                raise StudentStoreIdentityError("stored scope identity does not match path")
            if not isinstance(wrapped.get("events"), list):
                raise TypeError("stored events must be a list")
            return wrapped
        except StudentStoreIdentityError:
            raise
        except Exception as exc:
            raise StudentStoreCorruptionError(f"cannot read {target}: {exc}") from exc

    @staticmethod
    def _decode_events(payload: dict[str, object]) -> tuple[StudentLearningEvent, ...]:
        try:
            events = tuple(_event_from_data(item) for item in payload["events"])
            by_id: dict[str, StudentLearningEvent] = {}
            for event in events:
                if event.event_id in by_id:
                    raise ValueError(f"duplicate stored event id {event.event_id!r}")
                by_id[event.event_id] = event
            return tuple(sorted(events, key=_event_order_key))
        except Exception as exc:
            raise StudentStoreCorruptionError(f"invalid stored event data: {exc}") from exc

    def _write_scope(
        self,
        target: Path,
        *,
        student_id: str,
        session_id: str,
        events: Sequence[StudentLearningEvent],
        snapshot: ReducerResult | None,
    ) -> None:
        payload = {
            "schema_version": STORE_SCHEMA_VERSION,
            "student_id": student_id,
            "session_id": session_id,
            "events": [_event_to_data(event) for event in events],
            "snapshot": None if snapshot is None else _result_to_data(snapshot),
        }
        self._atomic_write(target, self._wrap_payload(payload))

    def append_events(self, events: Sequence[StudentLearningEvent]) -> None:
        grouped: dict[tuple[str, str], list[StudentLearningEvent]] = {}
        for event in events:
            grouped.setdefault((event.student_id, event.session_id), []).append(event)
        for student_id, session_id in sorted(grouped):
            with self._student_lock(student_id):
                target = self.scope_path(student_id=student_id, session_id=session_id)
                payload = self._read_payload(
                    target,
                    student_id=student_id,
                    session_id=session_id,
                )
                stored_events = list(self._decode_events(payload))
                by_id = {event.event_id: event for event in stored_events}
                changed = False
                for event in grouped[(student_id, session_id)]:
                    previous = by_id.get(event.event_id)
                    if previous is None:
                        by_id[event.event_id] = event
                        changed = True
                    elif previous != event:
                        raise DuplicateEventConflictError(
                            f"event_id {event.event_id!r} has conflicting content"
                        )
                if not changed:
                    continue
                ordered_events = tuple(sorted(by_id.values(), key=_event_order_key))
                snapshot = self.reducer.reduce(
                    ordered_events,
                    student_id=student_id,
                    session_id=session_id,
                )
                self._write_scope(
                    target,
                    student_id=student_id,
                    session_id=session_id,
                    events=ordered_events,
                    snapshot=snapshot,
                )

    def load_events(
        self,
        *,
        student_id: str,
        session_id: str,
    ) -> Sequence[StudentLearningEvent]:
        normalized_student_id = student_id.strip()
        normalized_session_id = session_id.strip()
        with self._student_lock(normalized_student_id):
            target = self.scope_path(
                student_id=normalized_student_id,
                session_id=normalized_session_id,
            )
            payload = self._read_payload(
                target,
                student_id=normalized_student_id,
                session_id=normalized_session_id,
            )
            return self._decode_events(payload)

    def save_snapshot(self, snapshot: ReducerResult) -> None:
        student_id = snapshot.student_id
        session_id = snapshot.session_id
        with self._student_lock(student_id):
            target = self.scope_path(student_id=student_id, session_id=session_id)
            payload = self._read_payload(
                target,
                student_id=student_id,
                session_id=session_id,
            )
            events = self._decode_events(payload)
            expected = self.reducer.reduce(
                events,
                student_id=student_id,
                session_id=session_id,
            )
            if snapshot != expected:
                raise ValueError("snapshot does not match deterministic event replay")
            self._write_scope(
                target,
                student_id=student_id,
                session_id=session_id,
                events=events,
                snapshot=snapshot,
            )

    def load_snapshot(
        self,
        *,
        student_id: str,
        session_id: str,
    ) -> ReducerResult | None:
        normalized_student_id = student_id.strip()
        normalized_session_id = session_id.strip()
        with self._student_lock(normalized_student_id):
            target = self.scope_path(
                student_id=normalized_student_id,
                session_id=normalized_session_id,
            )
            payload = self._read_payload(
                target,
                student_id=normalized_student_id,
                session_id=normalized_session_id,
            )
            if payload.get("snapshot") is None:
                return None
            events = self._decode_events(payload)
            try:
                stored = _result_from_data(payload["snapshot"])
            except Exception as exc:
                raise StudentStoreCorruptionError(
                    f"invalid stored snapshot data: {exc}"
                ) from exc
            expected = self.reducer.reduce(
                events,
                student_id=normalized_student_id,
                session_id=normalized_session_id,
            )
            if stored != expected:
                raise StudentStoreCorruptionError(
                    "stored snapshot does not match deterministic event replay"
                )
            return stored


__all__ = [
    "STORE_SCHEMA_VERSION",
    "AtomicJsonStudentModelStore",
    "StudentStoreCorruptionError",
    "StudentStoreError",
    "StudentStoreIdentityError",
]
