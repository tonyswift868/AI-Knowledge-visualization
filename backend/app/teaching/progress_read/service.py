"""Single-pass composition of student evidence and durable review memory."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from ..contracts import HintDependency
from ..learning_state import LearningStage
from ..review_memory import (
    DurableReviewLog,
    PersistedReviewMemoryItem,
    ReviewObservationOutcome,
    ReviewRating,
    SqliteReviewMemoryStore,
)
from ..student_model import StudentLearningEventType
from ..student_repository import (
    StudentEventCursor,
    StudentEventRecord,
    StudentEventRepository,
)
from .models import (
    AttemptResult,
    ConceptProgressDetailReadModel,
    ConceptProgressRead,
    ExplainProgressReadModel,
    MasteryEvidenceRead,
    MasteryStatus,
    MemoryDiagnosticsRead,
    ProgressSummaryRead,
    QueueItemRead,
    RecentAttemptRead,
    ReviewPanelRead,
    TimelineCategory,
    TimelineEntryRead,
)


_WEAK_OUTCOMES = frozenset(
    {
        ReviewObservationOutcome.FAILED,
        ReviewObservationOutcome.PARTIAL,
        ReviewObservationOutcome.HINT_DEPENDENT,
        ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
    }
)
_ATTEMPT_TYPES = frozenset(
    {
        StudentLearningEventType.ATTEMPT,
        StudentLearningEventType.ANSWER_CORRECT,
        StudentLearningEventType.ANSWER_PARTIAL,
        StudentLearningEventType.ANSWER_INCORRECT,
        StudentLearningEventType.ASSESSMENT_RESULT,
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _label(concept_id: str, labels: Mapping[str, str]) -> str:
    explicit = labels.get(concept_id, "").strip()
    if explicit:
        return explicit[:240]
    return concept_id.replace("_", " ").replace("-", " ").strip().title()


def _mastery_status(stage: LearningStage) -> MasteryStatus:
    if stage is LearningStage.INDEPENDENTLY_VERIFIED:
        return MasteryStatus.VERIFIED
    if stage is LearningStage.SELF_ATTESTED:
        return MasteryStatus.SELF_ATTESTED
    return MasteryStatus.LEARNING


def _attempt_result(record: StudentEventRecord) -> AttemptResult:
    event_type = record.event_type
    if event_type is StudentLearningEventType.ANSWER_CORRECT:
        return AttemptResult.CORRECT
    if event_type is StudentLearningEventType.ANSWER_PARTIAL:
        return AttemptResult.PARTIAL
    if event_type is StudentLearningEventType.ANSWER_INCORRECT:
        return AttemptResult.INCORRECT
    if event_type is StudentLearningEventType.ASSESSMENT_RESULT:
        return (
            AttemptResult.ASSESSMENT_PASSED
            if record.event.assessment_passed is True
            else AttemptResult.ASSESSMENT_FAILED
        )
    return AttemptResult.ATTEMPTED


def _student_timeline_entry(
    record: StudentEventRecord,
    labels: Mapping[str, str],
) -> TimelineEntryRead | None:
    event = record.event
    category: TimelineCategory
    title: str
    explanation: str
    if event.event_type in {
        StudentLearningEventType.TEACHING_MOVE,
        StudentLearningEventType.EXAMPLE_USED,
    }:
        category = TimelineCategory.TEACHING
        title = "完成一次教学活动"
        explanation = "这是一条教学过程记录，不是掌握结论。"
    elif event.event_type is StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY:
        category = TimelineCategory.VERIFIED
        title = "独立验证通过"
        explanation = "独立验证带有可追溯证据，因此掌握状态可标记为已验证。"
    elif event.event_type in {
        StudentLearningEventType.MISCONCEPTION_SIGNAL,
        StudentLearningEventType.HINT_REQUESTED,
        StudentLearningEventType.ANSWER_INCORRECT,
    } or (
        event.event_type is StudentLearningEventType.ASSESSMENT_RESULT
        and event.assessment_passed is False
    ):
        category = TimelineCategory.WEAK
        title = "发现需要加强的证据"
        explanation = (
            event.detail
            if event.event_type is StudentLearningEventType.MISCONCEPTION_SIGNAL
            and event.detail
            else "这次学习证据显示该概念需要继续练习。"
        )
    elif event.event_type in _ATTEMPT_TYPES or event.event_type is StudentLearningEventType.SELF_ATTESTED_MASTERY:
        category = TimelineCategory.ASSESSMENT
        title = (
            "学习者自我确认"
            if event.event_type is StudentLearningEventType.SELF_ATTESTED_MASTERY
            else "记录一次作答或测评"
        )
        explanation = (
            "自我确认会单独显示；在独立验证前不会升级为已验证。"
            if event.event_type is StudentLearningEventType.SELF_ATTESTED_MASTERY
            else "结果保留为学习证据，不与 FSRS 记忆参数混合。"
        )
    else:
        return None
    return TimelineEntryRead(
        entry_id=f"student:{record.event_id}",
        concept_id=record.concept_id,
        concept_label=_label(record.concept_id, labels),
        category=category,
        occurred_at=record.occurred_at,
        title=title,
        explanation=explanation,
        evidence_ids=event.evidence_ids,
    )


def _review_timeline_entry(
    log: DurableReviewLog,
    item: PersistedReviewMemoryItem,
    labels: Mapping[str, str],
) -> TimelineEntryRead:
    weak = log.rating is ReviewRating.AGAIN
    return TimelineEntryRead(
        entry_id=f"review:{log.review_log_id}",
        concept_id=item.item.concept_id,
        concept_label=_label(item.item.concept_id, labels),
        category=TimelineCategory.WEAK if weak else TimelineCategory.REVIEW,
        occurred_at=log.reviewed_at,
        title="复习结果需要加强" if weak else "完成一次记忆复习",
        explanation=(
            "这次复习进入薄弱队列；它更新记忆调度，但不会改变掌握状态。"
            if weak
            else "这次复习更新 FSRS 记忆状态，不会被换算为掌握百分比。"
        ),
        evidence_ids=log.source_event_ids,
    )


class ProgressReadService:
    """Build explainable reads without mutating source repositories."""

    def __init__(
        self,
        *,
        student_repository: StudentEventRepository,
        review_store: SqliteReviewMemoryStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.student_repository = student_repository
        self.review_store = review_store
        self._clock = clock or _utc_now

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("progress read clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _events(
        self,
        *,
        student_id: str,
        session_id: str,
        limit: int,
    ) -> tuple[StudentEventRecord, ...]:
        if not 1 <= limit <= 10_000:
            raise ValueError("event_limit must be between 1 and 10000")
        records: list[StudentEventRecord] = []
        cursor: StudentEventCursor | None = None
        while len(records) < limit:
            page = self.student_repository.list_events(
                student_id=student_id,
                session_id=session_id,
                cursor=cursor,
                limit=min(500, limit - len(records)),
            )
            records.extend(page.records)
            cursor = page.next_cursor
            if cursor is None:
                break
        return tuple(records)

    @staticmethod
    def _mastery(
        *,
        stage: LearningStage,
        records: tuple[StudentEventRecord, ...],
        verification_evidence_ids: tuple[str, ...],
    ) -> MasteryEvidenceRead:
        self_attested = tuple(
            record
            for record in records
            if record.event_type is StudentLearningEventType.SELF_ATTESTED_MASTERY
        )
        verified = tuple(
            record
            for record in records
            if record.event_type
            is StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY
            and record.event.evidence_ids
        )
        assessments = tuple(
            record
            for record in records
            if record.event_type is StudentLearningEventType.ASSESSMENT_RESULT
        )
        failed = tuple(
            record for record in assessments if record.event.assessment_passed is False
        )
        misconceptions = tuple(
            record
            for record in records
            if record.event_type is StudentLearningEventType.MISCONCEPTION_SIGNAL
        )
        status = _mastery_status(stage)
        if status is MasteryStatus.VERIFIED:
            explanation = (
                f"已有 {len(verified)} 条带证据的独立验证记录；复习表现不会替代这些证据。"
            )
        elif status is MasteryStatus.SELF_ATTESTED:
            explanation = "学习者表示已经理解，但目前没有带证据的独立验证记录。"
        elif failed or misconceptions:
            explanation = "仍在学习：近期测评失败或误解证据表明需要继续练习。"
        else:
            explanation = "仍在学习：尚未形成带证据的独立验证记录。"
        source_event_ids = tuple(record.event_id for record in records[-128:])
        return MasteryEvidenceRead(
            status=status,
            explanation=explanation,
            self_attested_event_count=len(self_attested),
            verified_event_count=len(verified),
            assessment_evidence_count=sum(
                len(record.event.evidence_ids) for record in (*verified, *assessments)
            ),
            failed_assessment_count=len(failed),
            misconception_count=len(misconceptions),
            source_event_ids=source_event_ids,
            verification_evidence_ids=verification_evidence_ids[-128:],
        )

    @staticmethod
    def _review_panel(
        *,
        items: tuple[PersistedReviewMemoryItem, ...],
        logs: tuple[DurableReviewLog, ...],
        as_of: datetime,
    ) -> ReviewPanelRead:
        latest_log = max(logs, key=lambda log: log.reviewed_at, default=None)
        reviewed_items = tuple(item for item in items if item.item.last_review_at)
        diagnostic_item = (
            max(reviewed_items, key=lambda item: item.item.last_review_at)
            if reviewed_items
            else min(
                items,
                key=lambda item: item.item.due_at or datetime.max.replace(tzinfo=timezone.utc),
                default=None,
            )
        )
        due_values = tuple(item.item.due_at for item in items if item.item.due_at)
        next_due = min(due_values, default=None)
        failure_streak = max((item.failure_streak for item in items), default=0)
        hint_dependency = (
            HintDependency.PERSISTENT
            if any(item.item.hint_dependency is HintDependency.PERSISTENT for item in items)
            else HintDependency.RECENT
            if any(item.item.hint_dependency is HintDependency.RECENT for item in items)
            else HintDependency.NONE
        )
        latest_item = (
            max(reviewed_items, key=lambda item: item.item.last_review_at)
            if reviewed_items
            else None
        )
        recent_result = None if latest_item is None else latest_item.item.last_observation
        is_due = any(item.item.due_at is not None and item.item.due_at <= as_of for item in items)
        is_weak = failure_streak > 0 or hint_dependency is not HintDependency.NONE or any(
            item.item.last_observation in _WEAK_OUTCOMES for item in items
        )
        if not items:
            explanation = "还没有复习卡；记忆状态暂不可用，也不会推断掌握程度。"
        elif len(logs) >= 2 and all(
            log.rating is ReviewRating.AGAIN for log in sorted(
                logs, key=lambda entry: entry.reviewed_at
            )[-2:]
        ):
            explanation = "最近两次复习都需要提示或未能回忆；该结论只影响复习队列。"
        elif is_weak:
            explanation = "近期失败或提示依赖使该概念进入薄弱队列；掌握状态保持独立。"
        elif is_due:
            explanation = "已有到期复习；到期只表示记忆调度时间，不表示掌握下降。"
        else:
            explanation = "FSRS 记忆状态用于安排复习，不参与掌握等级计算。"
        if diagnostic_item is None:
            diagnostics = MemoryDiagnosticsRead()
        else:
            item = diagnostic_item.item
            diagnostics = MemoryDiagnosticsRead(
                difficulty=item.difficulty,
                stability=item.stability,
                retrievability=item.retrievability,
                retrievability_calculated_at=item.last_review_at,
                scheduler_version=diagnostic_item.scheduler_identity.package_version,
                state_version=diagnostic_item.state_version,
            )
        return ReviewPanelRead(
            item_count=len(items),
            review_count=sum(item.item.review_count for item in items),
            next_due_at=next_due,
            recent_result=recent_result,
            recent_rating=None if latest_log is None else latest_log.rating,
            recent_reviewed_at=None if latest_log is None else latest_log.reviewed_at,
            failure_streak=failure_streak,
            hint_dependency=hint_dependency,
            is_due=is_due,
            is_weak=is_weak,
            explanation=explanation,
            advanced_diagnostics=diagnostics,
        )

    def explain(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_labels: Mapping[str, str] | None = None,
        demo: bool = False,
        demo_label: str | None = None,
        concept_limit: int = 1_000,
        event_limit: int = 10_000,
        review_item_limit: int = 10_000,
        timeline_limit: int = 200,
    ) -> ExplainProgressReadModel:
        if not 1 <= concept_limit <= 1_000:
            raise ValueError("concept_limit must be between 1 and 1000")
        if not 1 <= review_item_limit <= 10_000:
            raise ValueError("review_item_limit must be between 1 and 10000")
        if not 1 <= timeline_limit <= 200:
            raise ValueError("timeline_limit must be between 1 and 200")
        labels = concept_labels or {}
        now = self._now()
        events = self._events(
            student_id=student_id,
            session_id=session_id,
            limit=event_limit,
        )
        projection = self.student_repository.reducer.reduce(
            (record.event for record in events),
            student_id=student_id,
            session_id=session_id,
        )
        items = self.review_store.list_student_items(
            student_id=student_id,
            limit=review_item_limit,
        )
        logs = self.review_store.list_student_review_logs(
            student_id=student_id,
            limit=event_limit,
        )
        events_by_concept: dict[str, list[StudentEventRecord]] = defaultdict(list)
        items_by_concept: dict[str, list[PersistedReviewMemoryItem]] = defaultdict(list)
        logs_by_concept: dict[str, list[DurableReviewLog]] = defaultdict(list)
        items_by_id = {item.item.item_id: item for item in items}
        for record in events:
            events_by_concept[record.concept_id].append(record)
        for item in items:
            items_by_concept[item.item.concept_id].append(item)
        for log in logs:
            item = items_by_id.get(log.item_id)
            if item is not None:
                logs_by_concept[item.item.concept_id].append(log)
        state_by_concept = {state.concept_id: state for state in projection.concept_states}
        concept_ids = sorted(
            set(state_by_concept) | set(items_by_concept),
            key=lambda concept_id: (_label(concept_id, labels).casefold(), concept_id),
        )[:concept_limit]
        concepts: list[ConceptProgressRead] = []
        for concept_id in concept_ids:
            state = state_by_concept.get(concept_id)
            concept_events = tuple(events_by_concept[concept_id])
            concept_items = tuple(items_by_concept[concept_id])
            concept_logs = tuple(logs_by_concept[concept_id])
            recent_attempts = tuple(
                RecentAttemptRead(
                    event_id=record.event_id,
                    occurred_at=record.occurred_at,
                    result=_attempt_result(record),
                    evidence_ids=record.event.evidence_ids,
                )
                for record in concept_events
                if record.event_type in _ATTEMPT_TYPES
            )[-20:]
            active_misconceptions = (
                () if state is None else state.active_misconceptions
            )
            verification_evidence_ids = (
                () if state is None else state.verification_evidence_ids
            )
            concepts.append(
                ConceptProgressRead(
                    concept_id=concept_id,
                    label=_label(concept_id, labels),
                    mastery=self._mastery(
                        stage=LearningStage.UNKNOWN if state is None else state.stage,
                        records=concept_events,
                        verification_evidence_ids=verification_evidence_ids,
                    ),
                    review=self._review_panel(
                        items=concept_items,
                        logs=concept_logs,
                        as_of=now,
                    ),
                    recent_attempts=recent_attempts,
                    active_misconceptions=active_misconceptions,
                    last_activity_at=max(
                        (
                            *(record.occurred_at for record in concept_events),
                            *(log.reviewed_at for log in concept_logs),
                        ),
                        default=None,
                    ),
                )
            )
        concept_by_id = {concept.concept_id: concept for concept in concepts}
        due_queue: list[QueueItemRead] = []
        weak_queue: list[QueueItemRead] = []
        for persisted in items:
            item = persisted.item
            if item.concept_id not in concept_by_id:
                continue
            queue_item = QueueItemRead(
                item_id=item.item_id,
                concept_id=item.concept_id,
                concept_label=concept_by_id[item.concept_id].label,
                due_at=item.due_at,
                failure_streak=persisted.failure_streak,
                hint_dependency=item.hint_dependency,
                reason=(
                    "复习已到期"
                    if item.due_at is not None and item.due_at <= now
                    else "近期失败或提示依赖"
                ),
            )
            if item.due_at is not None and item.due_at <= now:
                due_queue.append(queue_item)
            if (
                persisted.failure_streak > 0
                or item.hint_dependency is not HintDependency.NONE
                or item.last_observation in _WEAK_OUTCOMES
            ):
                weak_queue.append(
                    queue_item.model_copy(update={"reason": "近期失败或提示依赖"})
                )
        due_queue.sort(key=lambda entry: (entry.due_at or now, entry.item_id))
        weak_queue.sort(
            key=lambda entry: (-entry.failure_streak, entry.due_at or now, entry.item_id)
        )
        timeline: list[TimelineEntryRead] = []
        for record in events:
            entry = _student_timeline_entry(record, labels)
            if entry is not None and entry.concept_id in concept_by_id:
                timeline.append(entry)
        for log in logs:
            item = items_by_id.get(log.item_id)
            if item is not None and item.item.concept_id in concept_by_id:
                timeline.append(_review_timeline_entry(log, item, labels))
        timeline.sort(key=lambda entry: (entry.occurred_at, entry.entry_id), reverse=True)
        status_counts = defaultdict(int)
        for concept in concepts:
            status_counts[concept.mastery.status] += 1
        return ExplainProgressReadModel(
            student_id=student_id,
            session_id=session_id,
            generated_at=now,
            demo=demo,
            demo_label=demo_label,
            summary=ProgressSummaryRead(
                concept_count=len(concepts),
                learning_count=status_counts[MasteryStatus.LEARNING],
                self_attested_count=status_counts[MasteryStatus.SELF_ATTESTED],
                verified_count=status_counts[MasteryStatus.VERIFIED],
                due_count=len(due_queue),
                weak_count=len(weak_queue),
                recent_attempt_count=sum(len(concept.recent_attempts) for concept in concepts),
            ),
            concepts=tuple(concepts),
            due_queue=tuple(due_queue[:concept_limit]),
            weak_queue=tuple(weak_queue[:concept_limit]),
            recent_activity=tuple(timeline[:timeline_limit]),
        )

    def explain_concept(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
        concept_labels: Mapping[str, str] | None = None,
        demo: bool = False,
    ) -> ConceptProgressDetailReadModel:
        model = self.explain(
            student_id=student_id,
            session_id=session_id,
            concept_labels=concept_labels,
            demo=demo,
            timeline_limit=200,
        )
        concept = next(
            (entry for entry in model.concepts if entry.concept_id == concept_id),
            None,
        )
        if concept is None:
            raise KeyError(f"unknown progress concept: {concept_id}")
        labels = concept_labels or {}
        event_records = self.student_repository.list_recent_events(
            student_id=student_id,
            session_id=session_id,
            concept_id=concept_id,
            limit=500,
        )
        items = self.review_store.list_student_items(
            student_id=student_id,
            limit=10_000,
        )
        concept_items = {
            item.item.item_id: item
            for item in items
            if item.item.concept_id == concept_id
        }
        review_logs = self.review_store.list_student_review_logs(
            student_id=student_id,
            limit=10_000,
        )
        timeline_entries = [
            entry
            for record in event_records
            if (entry := _student_timeline_entry(record, labels)) is not None
        ]
        timeline_entries.extend(
            _review_timeline_entry(log, concept_items[log.item_id], labels)
            for log in review_logs
            if log.item_id in concept_items
        )
        timeline_entries.sort(
            key=lambda entry: (entry.occurred_at, entry.entry_id),
            reverse=True,
        )
        return ConceptProgressDetailReadModel(
            student_id=student_id,
            session_id=session_id,
            generated_at=model.generated_at,
            demo=demo,
            concept=concept,
            evidence_timeline=tuple(timeline_entries[:500]),
        )


__all__ = ["ProgressReadService"]
