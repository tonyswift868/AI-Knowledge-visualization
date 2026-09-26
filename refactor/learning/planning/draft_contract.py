"""Versioned draft-plan contracts shared by planning UI and PlanService.

The contract is deliberately independent from ``CurrentPlan``.  A draft is a
reviewable proposal, while a CurrentPlan is the persisted execution plan.  Old
``planning-ui.json`` files can omit this envelope and are projected into a
fresh DraftPlan when first read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


DRAFT_SCHEMA = "mentorflow.draft-plan.v1"
REVISION_SCHEMA = "mentorflow.plan-revision.v1"
NEEDS_INPUT = "NEEDS_INPUT"
PLAN_READY = "PLAN_READY"
_STATUSES = frozenset((NEEDS_INPUT, PLAN_READY))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 2000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


@dataclass(frozen=True)
class DraftStage:
    """One reviewable section of a plan draft."""

    id: str
    title: str
    summary: str = ""
    locked: bool = False
    state: str = NEEDS_INPUT
    # The short ``summary`` remains the UI copy.  These structured fields
    # keep the draft useful to the scheduler without turning it into the full
    # curriculum before the user confirms it.
    purpose: str = ""
    topics: tuple[str, ...] = ()
    estimated_learning_minutes: int = 0
    dependencies: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "summary": self.summary,
                "locked": self.locked, "state": self.state,
                "purpose": self.purpose, "topics": list(self.topics),
                "estimated_learning_minutes": self.estimated_learning_minutes,
                "dependencies": list(self.dependencies), "source_refs": list(self.source_refs)}

    @classmethod
    def from_dict(cls, value: Any) -> "DraftStage":
        if not isinstance(value, dict):
            raise ValueError("draft stage")
        stage_id, title = _text(value.get("id"), 80), _text(value.get("title"), 160)
        if not stage_id or not title:
            raise ValueError("draft stage identity")
        state = value.get("state", NEEDS_INPUT)
        if state not in _STATUSES:
            state = NEEDS_INPUT
        def items(key, limit=20):
            raw = value.get(key, ())
            if not isinstance(raw, (list, tuple)):
                return ()
            return tuple(_text(item, 300) for item in raw if isinstance(item, str) and item.strip())[:limit]
        minutes = value.get("estimated_learning_minutes", 0)
        if type(minutes) is not int or minutes < 0:
            minutes = 0
        return cls(stage_id, title, _text(value.get("summary"), 2000),
                   bool(value.get("locked")), state,
                   _text(value.get("purpose"), 1000), items("topics"), minutes,
                   items("dependencies"), items("source_refs"))


@dataclass(frozen=True)
class DraftPlan:
    """Account-local proposal shown before a CurrentPlan is saved."""

    revision_id: str
    status: str
    goal: str = ""
    target_deadline: str = ""
    assumptions: tuple[str, ...] = ()
    stages: tuple[DraftStage, ...] = ()
    risk: dict[str, Any] = field(default_factory=dict)
    confirmed: bool = False
    updated_at: str = field(default_factory=_now)
    schema: str = DRAFT_SCHEMA
    normalized_goal: str = ""
    total_initial_learning_minutes: int = 0
    proposed_first_pass: dict[str, str] = field(default_factory=dict)
    schedule_risk: str = "comfortable"
    unresolved_questions: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.revision_id or self.status not in _STATUSES:
            raise ValueError("invalid draft plan")
        if self.schema != DRAFT_SCHEMA:
            raise ValueError("draft schema")
        if self.schedule_risk not in {"comfortable", "tight", "high_risk"}:
            raise ValueError("invalid draft schedule risk")
        if type(self.total_initial_learning_minutes) is not int or self.total_initial_learning_minutes < 0:
            raise ValueError("invalid draft learning minutes")

    def as_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "revision_id": self.revision_id,
                "status": self.status, "goal": self.goal,
                "target_deadline": self.target_deadline,
                "assumptions": list(self.assumptions),
                "stages": [stage.as_dict() for stage in self.stages],
                "risk": dict(self.risk), "confirmed": self.confirmed,
                "updated_at": self.updated_at,
                "normalized_goal": self.normalized_goal or self.goal,
                "total_initial_learning_minutes": self.total_initial_learning_minutes,
                "proposed_first_pass": dict(self.proposed_first_pass),
                "schedule_risk": self.schedule_risk,
                "unresolved_questions": list(self.unresolved_questions)}

    @classmethod
    def from_dict(cls, value: Any) -> "DraftPlan":
        if not isinstance(value, dict) or value.get("schema", DRAFT_SCHEMA) != DRAFT_SCHEMA:
            raise ValueError("draft plan")
        stages = tuple(DraftStage.from_dict(item) for item in value.get("stages", ()))
        assumptions = tuple(_text(item, 500) for item in value.get("assumptions", ())
                            if isinstance(item, str) and item.strip())
        risk = value.get("risk", {})
        if not isinstance(risk, dict):
            risk = {"level": "unknown", "messages": [_text(risk, 500)]}
        status = value.get("status", NEEDS_INPUT)
        if status not in _STATUSES:
            status = NEEDS_INPUT
        minutes = value.get("total_initial_learning_minutes", 0)
        if type(minutes) is not int or minutes < 0:
            minutes = 0
        first_pass = value.get("proposed_first_pass", {})
        if not isinstance(first_pass, dict):
            first_pass = {}
        schedule_risk = value.get("schedule_risk", "comfortable")
        if schedule_risk not in {"comfortable", "tight", "high_risk"}:
            schedule_risk = "comfortable"
        questions = value.get("unresolved_questions", ())
        if not isinstance(questions, (list, tuple)):
            questions = ()
        goal = _text(value.get("goal"))
        return cls(_text(value.get("revision_id"), 120) or uuid4().hex,
                   status, goal, _text(value.get("target_deadline"), 32),
                   assumptions, stages, dict(risk), bool(value.get("confirmed")),
                   _text(value.get("updated_at"), 64) or _now(), DRAFT_SCHEMA,
                   _text(value.get("normalized_goal"), 2000) or goal, minutes,
                   {str(k): _text(v, 1000) for k, v in first_pass.items() if isinstance(k, str) and isinstance(v, str)},
                   schedule_risk, tuple(_text(item, 500) for item in questions if isinstance(item, str) and item.strip()))


@dataclass(frozen=True)
class PlanRevision:
    """Immutable record describing one draft adjustment/replan operation."""

    revision_id: str
    parent_revision_id: str = ""
    action: str = "create"
    reason: str = ""
    draft: DraftPlan | None = None
    created_at: str = field(default_factory=_now)
    schema: str = REVISION_SCHEMA

    def __post_init__(self):
        if not self.revision_id or self.schema != REVISION_SCHEMA:
            raise ValueError("invalid plan revision")

    def as_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "revision_id": self.revision_id,
                "parent_revision_id": self.parent_revision_id, "action": self.action,
                "reason": self.reason, "draft": self.draft.as_dict() if self.draft else None,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, value: Any) -> "PlanRevision":
        if not isinstance(value, dict) or value.get("schema", REVISION_SCHEMA) != REVISION_SCHEMA:
            raise ValueError("plan revision")
        raw = value.get("draft")
        draft = DraftPlan.from_dict(raw) if raw else None
        return cls(_text(value.get("revision_id"), 120) or uuid4().hex,
                   _text(value.get("parent_revision_id"), 120),
                   _text(value.get("action"), 40) or "create",
                   _text(value.get("reason"), 1000), draft,
                   _text(value.get("created_at"), 64) or _now())


def draft_from_form(form: dict[str, Any], *, revision_id: str | None = None,
                    locked_stages: tuple[str, ...] = (), confirmed: bool = False,
                    risk: dict[str, Any] | None = None) -> DraftPlan:
    """Project the existing planning form into a versioned review contract."""
    goal = _text(form.get("goal_statement"))
    deadline = _text(form.get("deadline"), 32)
    assumptions = []
    if not goal:
        assumptions.append("需要先确认学习目标。")
    if not _text(form.get("expected_output")):
        assumptions.append("尚未填写最终交付成果。")
    if not deadline:
        assumptions.append("尚未确定目标截止日期。")
    if not form.get("material_ids") and not form.get("source_ids") and not _text(form.get("sourceText")):
        assumptions.append("尚未选择或导入学习资料，生成时只能依据目标拆解。")
    level = _text(form.get("current_level"), 40) or "beginner"
    material_refs = tuple(str(item) for item in (*form.get("material_ids", ()), *form.get("source_ids", ())) if str(item).strip())
    daily_minutes = form.get("minutes_per_day", 60)
    if type(daily_minutes) is not int or daily_minutes < 1:
        daily_minutes = 60
    start_date = _text(form.get("start_date"), 32)
    # The draft is intentionally conservative: it records a preliminary
    # first-pass estimate, while the formal scheduler remains authoritative
    # after confirmation.  No fixed learning/review ratio is used.
    try:
        from datetime import date
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(deadline)
        available_days = max(1, (end - start).days + 1)
    except (TypeError, ValueError):
        available_days = 1
    total_initial = max(30, daily_minutes * min(available_days, 5))
    risk_level = "comfortable" if available_days >= 14 else "tight" if available_days >= 5 else "high_risk"
    first_pass_date = deadline or start_date
    stage_minutes = (15, max(15, 20 * len(material_refs)), 20, 10, 10)
    stages = (
        DraftStage("requirements", "需求确认", goal or "等待学习目标",
                    "requirements" in locked_stages, PLAN_READY if goal else NEEDS_INPUT,
                    "明确目标与边界", (goal,) if goal else (), stage_minutes[0], (), material_refs),
        DraftStage("materials", "资料选择", "已选资料 %d 份" % (len(form.get("material_ids", ())) + len(form.get("source_ids", ()))),
                    "materials" in locked_stages, PLAN_READY if form.get("material_ids") or form.get("source_ids") or _text(form.get("sourceText")) else NEEDS_INPUT,
                    "整理可引用的学习依据", material_refs, stage_minutes[1], ("requirements",), material_refs),
        DraftStage("outcome", "成果与标准", _text(form.get("expected_output")) or "等待交付成果",
                    "outcome" in locked_stages, PLAN_READY if _text(form.get("expected_output")) else NEEDS_INPUT,
                    "定义可验证的产出", (_text(form.get("expected_output")),) if _text(form.get("expected_output")) else (), stage_minutes[2], ("requirements",), ()),
        DraftStage("schedule", "时间安排", "%s 至 %s · 每天 %s 分钟" % (_text(form.get("start_date"), 32), deadline, form.get("minutes_per_day", 60)),
                    "schedule" in locked_stages, PLAN_READY if deadline and start_date else NEEDS_INPUT,
                    "验证截止日期和可用学习日", (), stage_minutes[3], ("requirements",), ()),
        DraftStage("preferences", "学习偏好", "当前水平：%s · 单次约 %s 分钟" % (level, form.get("preferred_session_minutes", 30)),
                    "preferences" in locked_stages, PLAN_READY,
                    "保留节奏、难度和教师偏好", (level,), stage_minutes[4], ("schedule",), ()),
    )
    # Materials and expected output are review assumptions, but they should
    # not make the draft impossible to review: the user may intentionally
    # choose to generate from the goal alone. Goal and deadline are the
    # minimum fields required for PLAN_READY.
    status = PLAN_READY if goal and deadline else NEEDS_INPUT
    return DraftPlan(revision_id or uuid4().hex, status, goal, deadline,
                     tuple(assumptions), stages, dict(risk or {}), confirmed,
                     normalized_goal=goal, total_initial_learning_minutes=total_initial,
                     proposed_first_pass={"target_date": first_pass_date,
                                          "rationale": "根据当前资料规模和可用学习日给出的首次学习预估，正式课程确认后由 Scheduler 复核。"},
                     schedule_risk=risk_level, unresolved_questions=tuple(assumptions))


__all__ = ["DRAFT_SCHEMA", "REVISION_SCHEMA", "NEEDS_INPUT", "PLAN_READY",
           "DraftStage", "DraftPlan", "PlanRevision", "draft_from_form"]
