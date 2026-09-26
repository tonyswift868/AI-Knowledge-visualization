"""Host-side evaluator boundary for classroom attempts.

Tutor text is deliberately not treated as a grade.  This module accepts a
small structured evaluator result, validates every identifier against the
active :class:`LearningContract`, and only then turns the result into durable
Evidence/Error facts.  A provider failure or malformed response raises before
any classroom state is written.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import threading
from typing import Any, Mapping

from .domain import (
    BlockResolution,
    BlockResolver,
    ErrorEpisode,
    EvaluationResult,
    EvidenceEvent,
    LearningContract,
    ObjectiveEvaluation,
    ObservedError,
    TutorCheckpoint,
    build_checkpoint,
    make_evidence,
    make_error_episode,
    validate_evaluation,
)
from .verification import VerificationScheduler
from ..observability import complete_with_metadata


class EvaluationError(ValueError):
    """A public, non-sensitive evaluator contract error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class EvaluationCommit:
    result: EvaluationResult
    evidence: tuple[EvidenceEvent, ...]
    errors: tuple[ErrorEpisode, ...]
    resolution: BlockResolution
    checkpoint: TutorCheckpoint


_RESULT_KEYS = frozenset({"verdict", "objective_results", "objectiveResults", "observed_errors", "observedErrors", "recommended_action", "recommendedAction"})
_OBJECTIVE_KEYS = frozenset({"objective_id", "objectiveId", "status", "evidence", "evidence_drafts", "evidenceDrafts"})
_EVIDENCE_KEYS = frozenset({"type", "summary", "independence", "artifact_refs", "artifactRefs"})
_ERROR_KEYS = frozenset({"competency_id", "competencyId", "error_class", "errorClass", "description", "severity", "misconception_candidate", "misconceptionCandidate"})


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError("EVALUATOR_SCHEMA_INVALID", f"{name} 必须是对象。")
    return value


def _strict_keys(value: Mapping[str, Any], allowed: frozenset[str], name: str, *, required: frozenset[str] | None = None) -> None:
    unknown = set(value) - set(allowed)
    if unknown:
        raise EvaluationError("EVALUATOR_SCHEMA_INVALID", f"{name} 含有未知字段。")
    if required and not required.issubset(value):
        raise EvaluationError("EVALUATOR_SCHEMA_INVALID", f"{name} 字段不完整。")


def _json_payload(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EvaluationError("EVALUATOR_JSON_INVALID", "模型评估结果不是有效 JSON。") from exc
    return _object(payload, "evaluation")


def parse_evaluation(payload: Any, contract: LearningContract, *, allowed_artifact_refs: set[str] | None = None) -> EvaluationResult:
    """Parse and validate one evaluator payload without writing anything."""
    if isinstance(payload, EvaluationResult):
        result = payload
    else:
        value = _json_payload(payload)
        _strict_keys(value, _RESULT_KEYS, "evaluation")
        if any(not (snake in value or camel in value) for snake, camel in (
                ("objective_results", "objectiveResults"),
                ("observed_errors", "observedErrors"),
                ("recommended_action", "recommendedAction"))):
            raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "evaluation 字段不完整。")
        value = {
            "verdict": value.get("verdict"),
            "objective_results": value.get("objective_results", value.get("objectiveResults")),
            "observed_errors": value.get("observed_errors", value.get("observedErrors")),
            "recommended_action": value.get("recommended_action", value.get("recommendedAction")),
        }
        objective_results: list[ObjectiveEvaluation] = []
        raw_objectives = value["objective_results"]
        if not isinstance(raw_objectives, (list, tuple)):
            raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "objective_results 必须是数组。")
        for raw in raw_objectives:
            item = _object(raw, "objective_result")
            _strict_keys(item, _OBJECTIVE_KEYS, "objective_result", required=frozenset({"status"}))
            objective_id = item.get("objective_id", item.get("objectiveId"))
            if not isinstance(objective_id, str) or not objective_id.strip():
                raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "objective_id 字段不完整。")
            draft_values = item.get("evidence_drafts", item.get("evidenceDrafts", item.get("evidence", ())))
            drafts = draft_values
            if not isinstance(drafts, (list, tuple)):
                raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "evidence_drafts 必须是数组。")
            evidence: list[dict[str, Any]] = []
            for raw_draft in drafts:
                draft = _object(raw_draft, "evidence_draft")
                _strict_keys(draft, _EVIDENCE_KEYS, "evidence_draft", required=frozenset({"type", "summary", "independence"}))
                refs = draft.get("artifact_refs", draft.get("artifactRefs", ()))
                if not isinstance(refs, (list, tuple)) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
                    raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "artifact_refs 必须是字符串数组。")
                evidence.append({
                    "type": draft.get("type"),
                    "summary": draft.get("summary"),
                    "independence": draft.get("independence"),
                    "artifact_refs": list(refs),
                })
            objective_results.append(ObjectiveEvaluation(
                str(objective_id), str(item.get("status")), tuple(evidence)))
        observed_errors: list[ObservedError] = []
        raw_errors = value["observed_errors"]
        if not isinstance(raw_errors, (list, tuple)):
            raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "observed_errors 必须是数组。")
        for raw in raw_errors:
            item = _object(raw, "observed_error")
            _strict_keys(item, _ERROR_KEYS, "observed_error", required=frozenset({"description"}))
            competency_id = item.get("competency_id", item.get("competencyId"))
            error_class = item.get("error_class", item.get("errorClass"))
            description = item.get("description")
            if (not isinstance(competency_id, str) or not competency_id.strip()
                    or not isinstance(error_class, str) or not error_class.strip()
                    or not isinstance(description, str) or not description.strip()):
                raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "observed_error 标识字段不完整。")
            candidate = item.get("misconception_candidate", item.get("misconceptionCandidate"))
            if candidate is not None and not isinstance(candidate, str):
                raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "misconception_candidate 必须是字符串或 null。")
            observed_errors.append(ObservedError(
                str(competency_id), str(error_class),
                description, str(item.get("severity", "medium")), candidate))
        try:
            result = EvaluationResult(
                str(value["verdict"]), tuple(objective_results), tuple(observed_errors),
                str(value["recommended_action"]),
            )
        except ValueError as exc:
            raise EvaluationError("EVALUATOR_SCHEMA_INVALID", "评估结果枚举或字段无效。") from exc
    try:
        return validate_evaluation(contract, result, allowed_artifact_refs=allowed_artifact_refs)
    except ValueError as exc:
        raise EvaluationError("EVALUATOR_SCOPE_INVALID", "评估结果引用了当前学习块之外的内容。") from exc


def evaluator_messages(contract: LearningContract, attempt: str, *, context: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """Build a bounded, role-specific evaluator prompt."""
    if not isinstance(attempt, str) or not attempt.strip() or len(attempt) > 12000:
        raise EvaluationError("EVALUATOR_INPUT_INVALID", "学习者作答为空或超出长度限制。")
    bounded_context: dict[str, Any] = {}
    for key, value in list((context or {}).items())[:32]:
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            bounded_context[key[:80]] = value[:2000]
        elif isinstance(value, (list, tuple)):
            bounded_context[key[:80]] = [str(item)[:500] for item in value[:20]]
        elif isinstance(value, Mapping):
            try:
                bounded_context[key[:80]] = json.loads(json.dumps(dict(value), ensure_ascii=False))
            except (TypeError, ValueError):
                bounded_context[key[:80]] = str(value)[:2000]
        else:
            bounded_context[key[:80]] = str(value)[:500]
    context_text = json.dumps(bounded_context, ensure_ascii=False)
    if len(context_text) > 10000:
        context_text = context_text[:10000]
        bounded_context = {"bounded_context": context_text}
    payload = {
        "contract": {
            "contract_id": contract.contract_id,
            "target_depth": contract.target_depth,
            "visible_objectives": [{"id": item.id, "text": item.text, "blocking": item.blocking}
                                   for item in contract.visible_objectives],
            "required_evidence": [{"objective_id": item.objective_id,
                                    "acceptable_types": list(item.acceptable_types),
                                    "independence_required": item.independence_required}
                                   for item in contract.required_evidence],
            "competency_ids": list(contract.primary_competency_ids),
        },
        "attempt": attempt,
        "context": bounded_context,
        "protocol": "只输出 JSON；只能引用当前 contract 的 objective/competency；不要直接写入完成或掌握状态。",
    }
    return [
        {"role": "system", "content": "你是 MentorFlow 的隐藏 Evaluator。你只判断学习者本次作答的证据，不负责教学，也不能修改状态。输出字段必须为 verdict、objective_results、observed_errors、recommended_action。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class EvaluationService:
    """Evaluate an attempt and optionally commit facts through ClassroomRecords."""

    def parse(self, payload: Any, contract: LearningContract, *, allowed_artifact_refs: set[str] | None = None) -> EvaluationResult:
        return parse_evaluation(payload, contract, allowed_artifact_refs=allowed_artifact_refs)

    def evaluate_attempt(self, contract: LearningContract, attempt: str, *, evaluator_client: Any = None,
                         result: Any = None, records: Any = None, plan_key: str | None = None,
                         session_id: str = "evaluation", context: Mapping[str, Any] | None = None,
                         allowed_artifact_refs: set[str] | None = None, cancel: Any = None) -> EvaluationCommit:
        if cancel is None:
            cancel = threading.Event()
        if result is None:
            if evaluator_client is None or not callable(getattr(evaluator_client, "complete", None)):
                raise EvaluationError("EVALUATOR_UNAVAILABLE", "当前没有可用的 Evaluator。")
            try:
                result = complete_with_metadata(
                    evaluator_client,
                    evaluator_messages(contract, attempt, context=context),
                    cancel=cancel,
                    metadata={"plan_key": str(plan_key or contract.plan_id),
                              "session_id": str(session_id), "role": "EVALUATOR"},
                )
            except Exception as exc:
                # Do not expose provider details; importantly, no persistence has happened yet.
                raise EvaluationError("EVALUATOR_PROVIDER_FAILED", "评估服务暂时不可用，本次作答未改变学习状态。") from exc
        parsed = self.parse(result, contract, allowed_artifact_refs=allowed_artifact_refs)
        return self.commit(contract, parsed, attempt=attempt, records=records, plan_key=plan_key,
                           session_id=session_id, context=context)

    # Short alias used by adapters that call the role simply ``evaluate``.
    evaluate = evaluate_attempt

    def commit(self, contract: LearningContract, result: EvaluationResult, *, attempt: str = "",
               records: Any = None, plan_key: str | None = None, session_id: str = "evaluation",
               context: Mapping[str, Any] | None = None) -> EvaluationCommit:
        parsed = self.parse(result, contract)
        evidence: list[EvidenceEvent] = []
        errors: list[ErrorEpisode] = []
        source_text = attempt.strip()[:12000] if isinstance(attempt, str) else ""
        for objective in parsed.objective_results:
            for draft in objective.evidence:
                evidence.append(make_evidence(
                    plan_id=contract.plan_id, session_id=session_id, contract_id=contract.contract_id,
                    objective_ids=(objective.objective_id,), competency_ids=contract.primary_competency_ids,
                    type=draft["type"], source="student_attempt", summary=draft["summary"],
                    artifact_refs=tuple(draft.get("artifact_refs", ())),
                    independence=draft["independence"], evaluator_result=objective.status,
                    confidence="high" if objective.status == "supported" else "medium" if objective.status == "supported_with_help" else "low",
                ))
        error_context = str((context or {}).get("error_context") or "independent_application")
        hint_level = str((context or {}).get("hint_level") or "none")
        for observed in parsed.observed_errors:
            errors.append(make_error_episode(
                plan_id=contract.plan_id, contract_id=contract.contract_id,
                competency_id=observed.competency_id, error_class=observed.error_class,
                description=observed.description, context=error_context, hint_level=hint_level,
                resolved_in_session=False, misconception_candidate=observed.misconception_candidate,
                evidence_id=evidence[-1].evidence_id if evidence else None,
            ))
        all_evidence = list(evidence)
        all_errors = list(errors)
        if records is not None and plan_key:
            for event in evidence:
                records.record_evidence(plan_key, event)
            for error in errors:
                records.record_error(plan_key, error)
            records.save_evaluation(plan_key, contract.contract_id, parsed)
            all_evidence = records.list_evidence(plan_key, contract.contract_id)
            all_errors = records.list_errors(plan_key, contract.contract_id)
        resolution = BlockResolver.resolve(contract, all_evidence, evaluation=parsed)
        checkpoint = build_checkpoint(contract, all_evidence, all_errors)
        if records is not None and plan_key:
            existing_items = records.list_verification(plan_key, contract.contract_id)
            existing_by_objective = {item.objective_id: item for item in existing_items}
            for candidate in VerificationScheduler.build_queue(contract, all_evidence, all_errors):
                previous = existing_by_objective.get(candidate.objective_id)
                if previous is not None:
                    if previous.status in {"passed", "resolved"}:
                        continue
                    candidate = replace(candidate, verification_id=previous.verification_id,
                                        status=previous.status)
                records.upsert_verification(plan_key, candidate)
            records.save_checkpoint(plan_key, checkpoint)
        return EvaluationCommit(parsed, tuple(evidence), tuple(errors), resolution, checkpoint)


__all__ = ["EvaluationError", "EvaluationCommit", "EvaluationService", "evaluator_messages", "parse_evaluation"]
