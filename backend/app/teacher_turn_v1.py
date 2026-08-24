"""Domain-neutral TeacherTurn v1 contract and deterministic state machine.

The provider is deliberately absent from the reducer in this module.  A
provider may supply wording, but only this module may choose a phase, decide
whether an answer is sufficient, or transition the learning state.  The
payload is JSON-shaped so it can be stored in ``TeacherTurnORM`` and rendered
by the active-teaching client without treating generated text as HTML.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .knowledge_policy import PROMPT_POLICY, TEACHING_POLICY, TEXT_LIMITS_POLICY
from .orm import SessionLocal, TeacherEventORM, TeacherTurnORM


SCHEMA_VERSION = "teacher-turn.v1"
PREFERENCE_PROFILE_SCHEMA_VERSION = "learner-preference-profile.v1"
MAX_REMEDIATION_ATTEMPTS = int(TEACHING_POLICY.maximum_remediation_attempts)
REQUIRED_OPENING_BLOCKS = tuple(TEACHING_POLICY.required_opening_blocks)
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_SPACE = re.compile(r"\s+")


class TeacherEvaluationV1(BaseModel):
    """Untrusted semantic evaluation returned by a Provider.

    The application still applies prechecks, thresholds, attempt limits, and
    state transitions after validating this payload.
    """

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=1)
    criteria_met: bool
    rationale: str = Field(
        default="",
        max_length=TEXT_LIMITS_POLICY.explanation_characters,
    )
    misconception: str = Field(
        default="",
        max_length=TEXT_LIMITS_POLICY.medium_text_characters,
    )
    remediation_focus: str = Field(
        default="",
        max_length=TEXT_LIMITS_POLICY.medium_text_characters,
    )


class TeacherExpressionV1(BaseModel):
    """Provider wording for application-owned TeacherTurn blocks."""

    model_config = ConfigDict(extra="forbid")

    explanation: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.long_text_characters,
    )
    analogy: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.explanation_characters,
    )
    worked_example: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.long_text_characters,
    )
    code_example: str = Field(
        default="",
        max_length=TEXT_LIMITS_POLICY.code_characters,
    )
    code_language: str = Field(
        default="",
        max_length=TEXT_LIMITS_POLICY.short_label_characters,
    )
    check_prompt: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.explanation_characters,
    )
    # The Provider may choose only the renderer's visual mood.  Learning
    # state, grading, timing and route transitions remain application-owned.
    presentation_state: Literal[
        "idle",
        "greeting",
        "talking",
        "explaining",
        "questioning",
        "praising",
        "encouraging",
        "confused",
        "shy",
        "playful_annoyed",
        "ending",
    ] = "explaining"


class LearnerPreferenceV1(BaseModel):
    """One user-entered preference; inference from behavior is not accepted."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.preference_key_characters,
    )
    value: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.preference_value_characters,
    )
    source: Literal["explicit"] = "explicit"


class LearnerPreferenceProfileV1(BaseModel):
    """Versioned, explicit-only preference contract used by teacher analogies."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["learner-preference-profile.v1"] = (
        PREFERENCE_PROFILE_SCHEMA_VERSION
    )
    workspace_id: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.identifier_characters,
    )
    revision: int = Field(default=0, ge=0)
    preferences: list[LearnerPreferenceV1] = Field(
        default_factory=list,
        max_length=TEXT_LIMITS_POLICY.maximum_preference_entries,
    )


def _text(
    value: Any,
    *,
    limit: int = TEXT_LIMITS_POLICY.long_text_characters,
) -> str:
    """Convert untrusted provider/user values to bounded plain text."""

    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "\n".join(_text(item, limit=limit) for item in value)
    elif isinstance(value, Mapping):
        value = value.get("content", value.get("text", value.get("value", "")))
    value = str(value).replace("\x00", "").strip()
    # Raw HTML is not part of the v1 wire contract.  Keep the visible text,
    # while making the value safe for both the Markdown and plain-text paths.
    value = _HTML_TAG.sub("", value)
    return value[:limit]


def _plain_parts(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [
            item
            for item in (
                _text(value.get("title")),
                _text(value.get("content") or value.get("text") or value.get("value")),
            )
            if item
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _block(block_type: str, content: Any, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"type": block_type, "content": _text(content)}
    item.update({key: value for key, value in extra.items() if value not in (None, "")})
    return item


def _normalise_preferences(preferences: Any) -> list[dict[str, str]]:
    """Return only explicit, active preference records.

    This function intentionally accepts the JSON shapes used by receipts and
    ``LearnerPreferenceORM`` but never derives a preference from language,
    response length, timing, or any transcript.
    """

    if not preferences:
        return []
    raw_items: list[Any]
    if isinstance(preferences, Mapping):
        if "preferences" in preferences:
            raw_items = list(preferences.get("preferences") or [])
        else:
            raw_items = [preferences]
    elif isinstance(preferences, Sequence) and not isinstance(preferences, (str, bytes, bytearray)):
        raw_items = list(preferences)
    else:
        raw_items = []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items:
        if isinstance(item, Mapping):
            if str(item.get("status", "active")) not in {"active", "explicit", ""}:
                continue
            key = _text(
                item.get("preference_key") or item.get("key") or item.get("type"),
                limit=TEXT_LIMITS_POLICY.preference_key_characters,
            )
            value = item.get("value_json", item.get("value", item.get("name", "")))
        else:
            key = "interest"
            value = item
        if isinstance(value, Mapping):
            # ORM JSON commonly stores interests as ``{"items": [...]}`` or
            # ``{"value": "..."}``.  Read those explicit values without
            # interpreting arbitrary surrounding metadata as a preference.
            values = value.get("items", value.get("values", value.get("interests", value.get("value", []))))
            values = values if isinstance(values, list) else [values]
        else:
            values = value if isinstance(value, list) else [value]
        for candidate in values:
            value_text = _text(
                candidate,
                limit=TEXT_LIMITS_POLICY.preference_value_characters,
            )
            if not value_text:
                continue
            pair = (key or "interest", value_text)
            if pair in seen:
                continue
            seen.add(pair)
            result.append({"key": pair[0] or "interest", "value": pair[1], "source": "explicit"})
    return result[: TEXT_LIMITS_POLICY.maximum_preference_entries]


def explicit_preferences_from_receipt(receipt: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Read explicit interests from the receipt only.

    ``learner_interests`` is a user-entered field.  Empty or malformed values
    intentionally result in an empty preference set and therefore a neutral
    structural analogy.
    """

    receipt = receipt or {}
    interests = receipt.get("learner_interests")
    if isinstance(interests, str):
        interests = [item.strip() for item in re.split(r"[,，、;；\n]", interests) if item.strip()]
    return _normalise_preferences(interests)


def explicit_preferences_from_orm(workspace_id: str) -> list[dict[str, str]]:
    """Read auditable explicit preferences without touching chat history."""

    try:
        from .orm import LearnerPreferenceORM

        with SessionLocal() as session:
            rows = session.query(LearnerPreferenceORM).filter(
                LearnerPreferenceORM.workspace_id == workspace_id,
                LearnerPreferenceORM.status == "active",
            ).all()
            return _normalise_preferences(
                [
                    {
                        "preference_key": row.preference_key,
                        "value_json": row.value_json,
                        "source": row.source,
                        "status": row.status,
                    }
                    for row in rows
                    if str(row.source or "explicit") == "explicit"
                ]
            )
    except Exception:
        # The legacy active-teaching database can be used before the ORM
        # migration is installed.  The safe fallback is no preference, never
        # a guessed preference.
        return []


def build_learner_preference_profile(
    *,
    workspace_id: str,
    explicit_preferences: Any,
    revision: int = 0,
) -> LearnerPreferenceProfileV1:
    """Validate and deduplicate the only preferences teaching may consume."""

    return LearnerPreferenceProfileV1(
        workspace_id=_text(
            workspace_id,
            limit=TEXT_LIMITS_POLICY.identifier_characters,
        ),
        revision=max(0, int(revision or 0)),
        preferences=[
            LearnerPreferenceV1.model_validate(item)
            for item in _normalise_preferences(explicit_preferences)
        ],
    )


def choose_analogy(
    *,
    concept: Mapping[str, Any],
    explicit_preferences: Any = None,
) -> dict[str, Any]:
    """Choose a preference-aware or neutral structural analogy.

    The analogy makes no source claim.  Its mapping is structural (object,
    condition, action, observable result), which lets callers reject it when
    the concept has no usable mapping instead of emitting a decorative story.
    """

    name = _text(
        concept.get("name") or concept.get("title") or "当前概念",
        limit=TEXT_LIMITS_POLICY.concept_name_characters,
    )
    explanation = _text(
        concept.get("explanation") or concept.get("summary") or "",
        limit=TEXT_LIMITS_POLICY.medium_text_characters,
    )
    prefs = _normalise_preferences(explicit_preferences)
    chosen = prefs[0] if prefs else None
    domain = chosen["value"] if chosen else PROMPT_POLICY.teacher_neutral_domain
    source = "explicit_learner_preference" if chosen else "neutral_structural_fallback"
    return {
        "type": "analogy",
        "domain": domain,
        "label": PROMPT_POLICY.teacher_analogy_label_template.format(
            concept=name,
            domain=domain,
        ),
        "content": PROMPT_POLICY.teacher_analogy_content_template.format(
            concept=name,
            domain=domain,
        ),
        "mapping": [dict(item) for item in PROMPT_POLICY.teacher_analogy_mapping],
        "limitation": PROMPT_POLICY.teacher_analogy_limitation,
        "source": source,
        "preference_key": chosen["key"] if chosen else "",
        "concept_hint": explanation[: TEXT_LIMITS_POLICY.preference_value_characters],
    }


def _opening_text(turn: Mapping[str, Any]) -> str:
    parts: list[str] = []
    concept = turn.get("concept") or {}
    if concept:
        parts.extend(_plain_parts(concept))
    for block in turn.get("blocks") or []:
        if isinstance(block, Mapping):
            parts.extend(_plain_parts(block.get("content") or block.get("text")))
    parts.extend(_plain_parts(turn.get("check")))
    parts.extend(_plain_parts(turn.get("expected_learner_action")))
    return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class TeacherTurnV1:
    state: dict[str, Any]
    concept: dict[str, Any]
    evidence: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    check: dict[str, Any]
    expected_learner_action: str
    plain_text: str
    schema_version: str = SCHEMA_VERSION
    presentation_state: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "state": self.state,
            "concept": self.concept,
            "evidence": self.evidence,
            "blocks": self.blocks,
            "check": self.check,
            "expected_learner_action": self.expected_learner_action,
            "plain_text": self.plain_text,
        }
        if self.presentation_state:
            payload["presentation_state"] = self.presentation_state
        return payload

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return self.to_dict()

    def json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def build_teacher_turn(
    *,
    concept: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]] | None = None,
    explicit_preferences: Any = None,
    phase: str = "waiting_for_answer",
    remediation_attempt: int = 0,
    correction: str = "",
) -> TeacherTurnV1:
    """Build the deterministic five-block opening for one knowledge point."""

    name = _text(
        concept.get("name") or concept.get("title") or "当前知识点",
        limit=TEXT_LIMITS_POLICY.concept_name_characters,
    )
    explanation = _text(
        concept.get("explanation") or concept.get("summary") or "",
        limit=TEXT_LIMITS_POLICY.explanation_characters,
    )
    objective = _text(
        concept.get("objective")
        or concept.get("goal")
        or f"能用自己的话说明“{name}”的核心结构",
        limit=TEXT_LIMITS_POLICY.objective_characters,
    )
    acceptance = _text(
        concept.get("acceptance_method")
        or concept.get("acceptance")
        or "给出一个具体应用，并说明可观察的结果",
        limit=TEXT_LIMITS_POLICY.medium_text_characters,
    )
    analogy = choose_analogy(concept=concept, explicit_preferences=explicit_preferences)
    example = _text(
        concept.get("worked_example") or concept.get("example") or "",
        limit=TEXT_LIMITS_POLICY.explanation_characters,
    )
    if not example:
        example = PROMPT_POLICY.teacher_worked_example_template.format(
            concept=name,
        )
    if correction:
        explanation = (
            f"先修正刚才最容易混淆的一点："
            f"{_text(correction, limit=TEXT_LIMITS_POLICY.medium_text_characters)}"
            f"\n\n{explanation}"
        ).strip()
    phase_label = {
        "waiting_for_answer": "讲解完成，等待你的检查回答",
        "targeted_correction": "针对性补救，等待重新检查",
        "alternate_example_or_prerequisite": "换一个结构例子或补前置，等待重新检查",
        "blocked": "已暂停，等待教师或学习者重新安排",
    }.get(phase, phase)
    opening_by_type = {
        "objective": _block("objective", objective),
        "explanation": _block("explanation", explanation),
        "analogy": analogy,
        "worked_example": _block("worked_example", example, steps=[
            "指出对象或输入",
            "按一个最小步骤推进",
            "说明可观察结果和验收方式",
        ]),
    }
    blocks = [
        opening_by_type[block_type]
        for block_type in REQUIRED_OPENING_BLOCKS
        if block_type != "check"
    ]
    check = {
        "prompt": PROMPT_POLICY.teacher_check_template.format(
            concept=name,
            acceptance=acceptance,
        ),
        "response_type": "short_explanation_with_example",
        "success_criteria": ["包含核心结构", "给出具体应用或例子", "说明可观察结果"],
    }
    state = {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "status": "waiting_for_answer" if phase != "blocked" else "blocked",
        "waiting_for_answer": phase != "blocked",
        "remediation_attempt": max(0, int(remediation_attempt)),
        "max_remediation_attempts": MAX_REMEDIATION_ATTEMPTS,
        "provider_controls_state": False,
        "mastery_changed": False,
    }
    turn_payload = {
        "state": state,
        "concept": {
            "id": _text(
                concept.get("id"),
                limit=TEXT_LIMITS_POLICY.identifier_characters,
            ),
            "name": name,
            "objective": objective,
        },
        "evidence": [dict(item) for item in (evidence or [])],
        "blocks": blocks,
        "check": check,
        "expected_learner_action": PROMPT_POLICY.teacher_expected_action,
    }
    plain_text = _opening_text(turn_payload)
    return TeacherTurnV1(
        state=state,
        concept=turn_payload["concept"],
        evidence=turn_payload["evidence"],
        blocks=blocks,
        check=check,
        expected_learner_action=turn_payload["expected_learner_action"],
        plain_text=plain_text,
    )


def merge_provider_expression(turn: TeacherTurnV1, provider_payload: Any) -> TeacherTurnV1:
    """Apply Provider wording and a visual-only pose without changing learning state."""

    if isinstance(provider_payload, TeacherExpressionV1):
        expression = provider_payload.model_dump()
    elif isinstance(provider_payload, Mapping) and any(
        key in provider_payload
        for key in ("explanation", "analogy", "worked_example", "check_prompt")
    ):
        expression = dict(provider_payload)
    elif isinstance(provider_payload, Mapping):
        expression = {
            "explanation": provider_payload.get(
                "content",
                provider_payload.get("text", provider_payload.get("message", "")),
            )
        }
    else:
        expression = {"explanation": provider_payload}
    explanation = _text(
        expression.get("explanation"),
        limit=TEXT_LIMITS_POLICY.long_text_characters,
    )
    analogy = _text(
        expression.get("analogy"),
        limit=TEXT_LIMITS_POLICY.explanation_characters,
    )
    worked_example = _text(
        expression.get("worked_example"),
        limit=TEXT_LIMITS_POLICY.long_text_characters,
    )
    code_example = _text(
        expression.get("code_example"),
        limit=TEXT_LIMITS_POLICY.code_characters,
    )
    code_language = re.sub(
        r"[^A-Za-z0-9_+.#-]",
        "",
        _text(
            expression.get("code_language"),
            limit=TEXT_LIMITS_POLICY.short_label_characters,
        ),
    )
    check_prompt = _text(
        expression.get("check_prompt"),
        limit=TEXT_LIMITS_POLICY.explanation_characters,
    )
    presentation_state = _text(
        expression.get("presentation_state"),
        limit=TEXT_LIMITS_POLICY.short_label_characters,
    ).lower()
    if presentation_state not in {
        "idle", "greeting", "talking", "explaining", "questioning",
        "praising", "encouraging", "confused", "shy",
        "playful_annoyed", "ending",
    }:
        presentation_state = turn.presentation_state
    if not any((explanation, analogy, worked_example, check_prompt)):
        return turn
    blocks = [dict(item) for item in turn.blocks]
    for item in blocks:
        block_type = item.get("type")
        if block_type == "explanation" and explanation:
            item["provider_expression"] = explanation
            item["content"] = explanation
        elif block_type == "analogy" and analogy:
            item["provider_expression"] = analogy
            item["content"] = analogy
        elif block_type == "worked_example" and worked_example:
            item["provider_expression"] = worked_example
            item["content"] = worked_example
            if code_example:
                item["code"] = code_example
                item["language"] = code_language
    check = dict(turn.check)
    if check_prompt:
        check["provider_expression"] = check_prompt
        check["prompt"] = check_prompt
    return TeacherTurnV1(
        state=dict(turn.state),
        concept=dict(turn.concept),
        evidence=[dict(item) for item in turn.evidence],
        blocks=blocks,
        check=check,
        expected_learner_action=turn.expected_learner_action,
        plain_text=_opening_text(
            {
                "concept": turn.concept,
                "blocks": blocks,
                "check": check,
                "expected_learner_action": turn.expected_learner_action,
            }
        ),
        schema_version=turn.schema_version,
        presentation_state=presentation_state,
    )


def transition_after_answer(
    state: Mapping[str, Any],
    *,
    correct: bool,
    score: float = 0.0,
) -> dict[str, Any]:
    """Deterministically transition after one learner check.

    Two failed checks are the hard bound.  A third failure is not another
    generated turn: it is ``blocked`` and requires an explicit later action.
    """

    next_state = dict(state)
    attempts = max(0, int(next_state.get("remediation_attempt") or 0))
    next_state["last_score"] = max(0.0, min(1.0, float(score or 0)))
    next_state["mastery_changed"] = False
    if correct:
        next_state.update({
            "phase": "resume_check" if attempts else "completed_check",
            "status": "ready_to_progress",
            "waiting_for_answer": False,
            "last_answer_passed": True,
        })
        return next_state
    attempts += 1
    next_state["remediation_attempt"] = attempts
    next_state["last_answer_passed"] = False
    if attempts == 1:
        phase = "targeted_correction"
        status = "waiting_for_answer"
    elif attempts == MAX_REMEDIATION_ATTEMPTS:
        phase = "alternate_example_or_prerequisite"
        status = "waiting_for_answer"
    else:
        phase = "blocked"
        status = "blocked"
    # If the policy is configured below two, never create an unbounded loop.
    if attempts > MAX_REMEDIATION_ATTEMPTS:
        phase, status = "blocked", "blocked"
    next_state.update({
        "phase": phase,
        "status": status,
        "waiting_for_answer": status != "blocked",
    })
    return next_state


def manual_skip_transition(state: Mapping[str, Any]) -> dict[str, Any]:
    """Record a user skip without turning it into mastery or completion."""

    next_state = dict(state)
    next_state.update({
        "phase": "manual_skip",
        "status": "manual_skipped",
        "waiting_for_answer": False,
        "manual_skip": True,
        "mastery_changed": False,
        "completed_as_mastered": False,
        "last_answer_passed": False,
    })
    return next_state


def self_attested_completion_transition(state: Mapping[str, Any]) -> dict[str, Any]:
    """Complete the current teaching point by learner attestation.

    This is deliberately separate from :func:`manual_skip_transition`:
    an explicit learner assertion completes and masters the point immediately.
    The origin remains auditable without creating a second UI workflow.
    """

    next_state = dict(state)
    next_state.update(
        {
            "phase": "self_attested_completion",
            "status": "completed",
            "waiting_for_answer": False,
            "self_attested_completion": True,
            "completion_origin": "self_attested",
            "provider_verified": False,
            "mastery_changed": True,
            "completed_as_mastered": True,
            "last_answer_passed": True,
        }
    )
    return next_state


def record_actual_elapsed(state: Mapping[str, Any], elapsed_minutes: Any) -> dict[str, Any]:
    """Accumulate actual activity only; estimated task minutes are immutable."""

    try:
        elapsed = max(
            0.0,
            min(
                float(TEACHING_POLICY.maximum_elapsed_event_minutes),
                float(elapsed_minutes or 0),
            ),
        )
    except (TypeError, ValueError):
        elapsed = 0.0
    next_state = dict(state)
    next_state["actual_elapsed_minutes"] = round(float(state.get("actual_elapsed_minutes") or 0) + elapsed, 3)
    if "estimated_task_minutes" in state:
        next_state["estimated_task_minutes"] = state["estimated_task_minutes"]
    return next_state


def persist_teacher_turn(
    *,
    workspace_id: str,
    turn: TeacherTurnV1 | Mapping[str, Any],
    turn_key: str,
    session_id: str | None = None,
    turn_number: int = 0,
    event_type: str = "teacher_turn_emitted",
    idempotency_key: str | None = None,
) -> dict[str, str]:
    """Persist a turn and append an audit event in the v1 ORM tables.

    Active-teaching's legacy SQLite session id is intentionally not written to
    the ORM foreign-key column.  It is kept in ``state_json`` instead, because
    it is not an ``orm_sessions`` id.
    """

    payload = turn.to_dict() if isinstance(turn, TeacherTurnV1) else dict(turn)
    turn_id = f"teacher_turn_{uuid.uuid4().hex[:24]}"
    with SessionLocal() as session:
        row = TeacherTurnORM(
            id=turn_id,
            workspace_id=workspace_id,
            session_id=None,
            turn_key=turn_key,
            turn_number=int(turn_number),
            role="teacher",
            content=_text(
                payload.get("plain_text"),
                limit=TEXT_LIMITS_POLICY.plain_text_characters,
            ),
            phase=_text(
                (payload.get("state") or {}).get("phase"),
                limit=TEXT_LIMITS_POLICY.short_label_characters,
            ),
            action_json={"action": event_type, "schema_version": payload.get("schema_version", SCHEMA_VERSION)},
            grounding_json={"evidence": payload.get("evidence") or []},
            state_json=payload,
            idempotency_key=idempotency_key,
        )
        session.add(row)
        session.flush()
        event_id = f"teacher_event_{uuid.uuid4().hex[:24]}"
        session.add(
            TeacherEventORM(
                id=event_id,
                workspace_id=workspace_id,
                teacher_turn_id=turn_id,
                event_key=f"{turn_key}:event",
                event_type=event_type,
                status="recorded",
                payload={"phase": row.phase, "schema_version": SCHEMA_VERSION},
                idempotency_key=f"{idempotency_key}:event" if idempotency_key else None,
            )
        )
        session.commit()
    return {"teacher_turn_id": turn_id, "teacher_event_id": event_id}


def turn_from_dict(payload: Mapping[str, Any]) -> TeacherTurnV1:
    """Validate/normalise a persisted JSON payload before rendering."""

    state = dict(payload.get("state") or {})
    blocks = []
    for raw in payload.get("blocks") or []:
        if isinstance(raw, str):
            blocks.append(_block("markdown", raw))
        elif isinstance(raw, Mapping):
            item = dict(raw)
            item["type"] = _text(
                item.get("type") or item.get("block_type") or "markdown",
                limit=TEXT_LIMITS_POLICY.short_label_characters,
            )
            if item.get("type") == "code":
                item["content"] = _text(
                    item.get("code") or item.get("content") or "",
                    limit=TEXT_LIMITS_POLICY.structured_block_characters,
                )
            else:
                item["content"] = _text(
                    item.get("content")
                    or item.get("text")
                    or item.get("value")
                    or "",
                    limit=TEXT_LIMITS_POLICY.structured_block_characters,
                )
            blocks.append(item)
    raw_concept = payload.get("concept") or {}
    concept = {
        str(key): (
            _text(value, limit=TEXT_LIMITS_POLICY.long_text_characters)
            if isinstance(value, (str, int, float, bool))
            else value
        )
        for key, value in dict(raw_concept).items()
    }
    raw_check = payload.get("check") or {"prompt": "请回答当前检查"}
    check = {
        str(key): (
            _text(value, limit=TEXT_LIMITS_POLICY.long_text_characters)
            if isinstance(value, (str, int, float, bool))
            else value
        )
        for key, value in dict(raw_check).items()
    }
    turn = TeacherTurnV1(
        state=state,
        concept=concept,
        evidence=[dict(item) for item in (payload.get("evidence") or []) if isinstance(item, Mapping)],
        blocks=blocks,
        check=check,
        expected_learner_action=_text(
            payload.get("expected_learner_action") or "请回答当前检查",
            limit=TEXT_LIMITS_POLICY.medium_text_characters,
        ),
        plain_text=_text(
            payload.get("plain_text")
            or _opening_text({**dict(payload), "blocks": blocks}),
            limit=TEXT_LIMITS_POLICY.plain_text_characters,
        ),
        schema_version=_text(
            payload.get("schema_version") or SCHEMA_VERSION,
            limit=TEXT_LIMITS_POLICY.short_label_characters,
        ),
        presentation_state=_text(
            payload.get("presentation_state"),
            limit=TEXT_LIMITS_POLICY.short_label_characters,
        ).lower(),
    )
    if turn.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported teacher turn schema: {turn.schema_version}")
    return turn


__all__ = [
    "SCHEMA_VERSION",
    "MAX_REMEDIATION_ATTEMPTS",
    "PREFERENCE_PROFILE_SCHEMA_VERSION",
    "REQUIRED_OPENING_BLOCKS",
    "LearnerPreferenceProfileV1",
    "LearnerPreferenceV1",
    "TeacherTurnV1",
    "build_learner_preference_profile",
    "build_teacher_turn",
    "choose_analogy",
    "explicit_preferences_from_orm",
    "explicit_preferences_from_receipt",
    "manual_skip_transition",
    "self_attested_completion_transition",
    "merge_provider_expression",
    "persist_teacher_turn",
    "record_actual_elapsed",
    "transition_after_answer",
    "turn_from_dict",
]
