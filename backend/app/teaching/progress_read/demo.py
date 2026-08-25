"""Deterministic, explicitly labelled progress demo persisted in real SQLite."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..contracts import HintDependency, TeacherMove
from ..review_memory import (
    DurableFsrsReviewMemoryService,
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewObservation,
    ReviewObservationOutcome,
)
from ..student_model import StudentLearningEvent, StudentLearningEventType
from .service import ProgressReadService


DEMO_STUDENT_ID = "DEMO-student-progress"
DEMO_SESSION_ID = "DEMO-session-progress"
DEMO_LABEL = "DEMO 学生 · 数据仅用于产品演示"
DEMO_CONCEPT_LABELS = {
    "binary-search-boundary": "二分查找边界",
    "recursion-base-case": "递归终止条件",
    "hash-collision": "哈希冲突处理",
    "dynamic-programming-transition": "动态规划状态转移",
}
_BASE = datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)


def _event(
    index: int,
    concept_id: str,
    event_type: StudentLearningEventType,
    **values: object,
) -> StudentLearningEvent:
    return StudentLearningEvent(
        event_id=f"DEMO-event-{index:02d}",
        student_id=DEMO_STUDENT_ID,
        session_id=DEMO_SESSION_ID,
        concept_id=concept_id,
        event_type=event_type,
        occurred_at=_BASE + timedelta(hours=index),
        **values,
    )


def ensure_demo_progress(
    *,
    service: ProgressReadService,
    review_service: DurableFsrsReviewMemoryService,
) -> None:
    """Idempotently materialize a realistic local demo into both repositories."""

    events = (
        _event(
            1,
            "binary-search-boundary",
            StudentLearningEventType.TEACHING_MOVE,
            teacher_move=TeacherMove.WORKED_EXAMPLE,
        ),
        _event(
            2,
            "binary-search-boundary",
            StudentLearningEventType.ASSESSMENT_RESULT,
            attempt_id="DEMO-attempt-binary",
            assessment_score=0.92,
            assessment_passed=True,
            evidence_ids=("DEMO-evidence-binary-assessment",),
        ),
        _event(
            3,
            "binary-search-boundary",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            evidence_ids=("DEMO-evidence-binary-independent",),
        ),
        _event(
            4,
            "recursion-base-case",
            StudentLearningEventType.TEACHING_MOVE,
            teacher_move=TeacherMove.EXPLAIN,
        ),
        _event(
            5,
            "recursion-base-case",
            StudentLearningEventType.SELF_ATTESTED_MASTERY,
        ),
        _event(
            6,
            "hash-collision",
            StudentLearningEventType.TEACHING_MOVE,
            teacher_move=TeacherMove.COMPARE,
        ),
        _event(
            7,
            "hash-collision",
            StudentLearningEventType.MISCONCEPTION_SIGNAL,
            detail="把哈希冲突误认为哈希函数必然失效，需要区分冲突与退化。",
        ),
        _event(
            8,
            "hash-collision",
            StudentLearningEventType.ASSESSMENT_RESULT,
            attempt_id="DEMO-attempt-hash",
            assessment_score=0.42,
            assessment_passed=False,
            evidence_ids=("DEMO-evidence-hash-assessment",),
        ),
        _event(
            9,
            "hash-collision",
            StudentLearningEventType.HINT_REQUESTED,
        ),
        _event(
            10,
            "dynamic-programming-transition",
            StudentLearningEventType.TEACHING_MOVE,
            teacher_move=TeacherMove.GUIDED_PRACTICE,
        ),
        _event(
            11,
            "dynamic-programming-transition",
            StudentLearningEventType.ANSWER_CORRECT,
            attempt_id="DEMO-attempt-dp",
            evidence_ids=("DEMO-evidence-dp-practice",),
        ),
    )
    service.student_repository.append_events(events)
    items = (
        ReviewMemoryItem(
            item_id="DEMO-review-binary",
            student_id=DEMO_STUDENT_ID,
            concept_id="binary-search-boundary",
            question_id="DEMO-question-binary",
            evidence_ids=("DEMO-evidence-binary-independent",),
            source_event_ids=("DEMO-event-03",),
            critical_weight=1.5,
        ),
        ReviewMemoryItem(
            item_id="DEMO-review-hash",
            student_id=DEMO_STUDENT_ID,
            concept_id="hash-collision",
            question_id="DEMO-question-hash",
            evidence_ids=("DEMO-evidence-hash-assessment",),
            source_event_ids=("DEMO-event-08", "DEMO-event-09"),
            critical_weight=2.0,
        ),
        ReviewMemoryItem(
            item_id="DEMO-review-dp",
            student_id=DEMO_STUDENT_ID,
            concept_id="dynamic-programming-transition",
            question_id="DEMO-question-dp",
            evidence_ids=("DEMO-evidence-dp-practice",),
            source_event_ids=("DEMO-event-11",),
        ),
    )
    review_service.store.register_items(items)
    observations = (
        ReviewObservation(
            observation_id="DEMO-observation-binary",
            student_id=DEMO_STUDENT_ID,
            item_id="DEMO-review-binary",
            concept_id="binary-search-boundary",
            question_id="DEMO-question-binary",
            outcome=ReviewObservationOutcome.RECALLED,
            observed_at=_BASE + timedelta(hours=12),
            evidence_ids=("DEMO-evidence-binary-independent",),
            source_event_ids=("DEMO-event-03",),
            macro_round_index=1,
        ),
        ReviewObservation(
            observation_id="DEMO-observation-hash",
            student_id=DEMO_STUDENT_ID,
            item_id="DEMO-review-hash",
            concept_id="hash-collision",
            question_id="DEMO-question-hash",
            outcome=ReviewObservationOutcome.HINT_DEPENDENT,
            observed_at=_BASE + timedelta(hours=13),
            evidence_ids=("DEMO-evidence-hash-assessment",),
            source_event_ids=("DEMO-event-08", "DEMO-event-09"),
            hint_used=True,
            hint_dependency=HintDependency.RECENT,
            macro_round_index=1,
        ),
        ReviewObservation(
            observation_id="DEMO-observation-dp",
            student_id=DEMO_STUDENT_ID,
            item_id="DEMO-review-dp",
            concept_id="dynamic-programming-transition",
            question_id="DEMO-question-dp",
            outcome=ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
            observed_at=_BASE + timedelta(hours=14),
            evidence_ids=("DEMO-evidence-dp-practice",),
            source_event_ids=("DEMO-event-11",),
            hesitation_observed=True,
            macro_round_index=1,
        ),
    )
    boundary = MacroRoundBoundary(
        configured_macro_rounds=3,
        current_macro_round=1,
    )
    review_service.intake_batch(tuple((observation, boundary) for observation in observations))


__all__ = [
    "DEMO_CONCEPT_LABELS",
    "DEMO_LABEL",
    "DEMO_SESSION_ID",
    "DEMO_STUDENT_ID",
    "ensure_demo_progress",
]
