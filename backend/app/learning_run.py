from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update

from . import db
from .learning_evaluator import evaluate_submission
from .narrative_runtime import publish_learning_event
from .orm import LearningRunMessageORM, LearningRunORM, SessionLocal, init_orm
from .services import tutor
from .adaptive_narrative import create_learning_artifact


PHASES = ["explanation", "worked_example", "guided_practice", "faded_guidance", "independent_attempt", "daily_variant_test", "passed"]
REVIEW_PHASES = ["spaced_recall", "mixed_review", "transfer_test", "review_passed"]
FINAL_PHASES = ["final_assessment", "final_passed"]


class LearningRunError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status_code: int = 400):
        self.code, self.message, self.details, self.status_code = code, message, details or {}, status_code
        super().__init__(message)


def _now(): return datetime.now(timezone.utc)
def _id(prefix): return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _request_key(body: dict[str, Any], prefix: str) -> str:
    supplied = str(body.get("idempotency_key") or "").strip()
    return supplied[:160] if supplied else _id(prefix)


def _task_context(task_id: str) -> tuple[str, dict[str, Any]]:
    for workspace in db.rows("workspaces", "status", "active"):
        plans = db.rows("plans", "workspace_id", workspace["id"])
        for plan_row in reversed(plans):
            payload = plan_row.get("payload")
            if isinstance(payload, str):
                import json
                payload = json.loads(payload)
            for day in (payload or {}).get("study_days", []):
                for task in day.get("tasks", []):
                    if task.get("id") == task_id:
                        return workspace["id"], task
    raise LearningRunError("TASK_NOT_FOUND", "任务不存在", {"task_id": task_id}, 404)


def _messages(session, run_id: str) -> list[dict[str, Any]]:
    return [{"id": item.id, "role": item.role, "phase": item.phase, "content": item.content, "grounding": item.grounding, "created_at": item.created_at.isoformat()} for item in session.query(LearningRunMessageORM).filter_by(run_id=run_id).order_by(LearningRunMessageORM.created_at.asc()).all()]


def _public(session, run: LearningRunORM) -> dict[str, Any]:
    state = copy.deepcopy(run.state or {})
    return {"run_id": run.id, "workspace_id": run.workspace_id, "task_id": run.task_id, "lesson_id": run.lesson_id, "phase": run.phase, "status": run.status, "revision": run.revision, "phase_order": state.get("phase_order", PHASES), "state": state, "messages": _messages(session, run.id), "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat()}


def create_learning_run(task_id: str) -> dict[str, Any]:
    workspace_id, task = _task_context(task_id); init_orm()
    with SessionLocal() as session:
        existing = session.query(LearningRunORM).filter_by(task_id=task_id).order_by(LearningRunORM.created_at.desc()).first()
        if existing and existing.status != "archived":
            return _public(session, existing)
        task_type = str(task.get("task_type") or ""); initial_phase = "final_assessment" if task_type in {"final", "final_assessment", "final_task"} else ("spaced_recall" if task_type == "review" else "explanation")
        phase_order = FINAL_PHASES if initial_phase == "final_assessment" else (REVIEW_PHASES if initial_phase == "spaced_recall" else PHASES)
        run = LearningRunORM(id=_id("learning_run"), workspace_id=workspace_id, task_id=task_id, lesson_id=str(task.get("lesson_id") or ""), phase=initial_phase, status="active", state={"run_type": "final" if initial_phase == "final_assessment" else ("review" if initial_phase == "spaced_recall" else "first_pass"), "hints_used": 0, "phase_hint_counts": {initial_phase: 0}, "independent_passes": 0, "attempts": [], "evidence": [], "phase_history": [initial_phase], "external_execution_status": "not_executed", "phase_order": phase_order, "standard_task_minutes": max(1, int(task.get("standard_task_minutes") or task.get("estimated_minutes") or 30))}, revision=0, created_at=_now(), updated_at=_now())
        session.add(run); session.commit(); session.refresh(run)
        return _public(session, run)


def get_learning_run(run_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        run = session.get(LearningRunORM, run_id)
        if not run: raise LearningRunError("LEARNING_RUN_NOT_FOUND", "Learning Run 不存在", {"run_id": run_id}, 404)
        return _public(session, run)


def _run(session, run_id: str) -> LearningRunORM:
    run = session.get(LearningRunORM, run_id)
    if not run: raise LearningRunError("LEARNING_RUN_NOT_FOUND", "Learning Run 不存在", {"run_id": run_id}, 404)
    return run


def add_run_message(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    text = str(body.get("message") or "").strip()
    if not text: raise LearningRunError("MESSAGE_REQUIRED", "消息不能为空")
    idem = _request_key(body, "learning_message_request")
    init_orm()
    with SessionLocal() as session:
        run = _run(session, run_id)
        existing = session.query(LearningRunMessageORM).filter_by(run_id=run_id, idempotency_key=idem).first()
        if existing:
            return _public(session, run)
        if run.status == "passed": raise LearningRunError("LEARNING_RUN_COMPLETED", "Learning Run 已完成", status_code=409)
        session.add(LearningRunMessageORM(id=_id("learning_message"), run_id=run_id, role="user", phase=run.phase, content=text, grounding={}, idempotency_key=idem, created_at=_now()))
        result = tutor(run.workspace_id, run.lesson_id, text, body.get("session_id"))
        tutor_key = f"{idem}:tutor"[:160]
        session.add(LearningRunMessageORM(id=_id("learning_message"), run_id=run_id, role="tutor", phase=run.phase, content=result.get("message", ""), grounding=result, idempotency_key=tutor_key, created_at=_now()))
        session.commit(); session.refresh(run)
        return _public(session, run)


def request_hint(run_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        run = _run(session, run_id)
        if run.phase == "daily_variant_test": raise LearningRunError("HINT_NOT_ALLOWED", "变式测试不允许提示", status_code=409)
        state = copy.deepcopy(run.state or {}); state["hints_used"] = int(state.get("hints_used", 0)) + 1
        phase_hint_counts = dict(state.get("phase_hint_counts") or {})
        phase_hint_counts[run.phase] = int(phase_hint_counts.get(run.phase, 0)) + 1
        state["phase_hint_counts"] = phase_hint_counts
        run.state = state; run.revision += 1; run.updated_at = _now()
        session.add(LearningRunMessageORM(id=_id("learning_message"), run_id=run_id, role="hint", phase=run.phase, content="提示：先写出你能确认的条件、步骤和一个检查方法。", grounding={"hint_level": state["hints_used"]}, created_at=_now()))
        session.commit(); session.refresh(run)
        return _public(session, run)


def _update_task(task_id: str, *, passed: bool, evidence: str) -> tuple[str, dict[str, Any], list[str], list[str]]:
    import json
    workspace_id, task = _task_context(task_id)
    for plan_row in db.rows("plans", "workspace_id", workspace_id):
        payload = json.loads(plan_row["payload"]) if isinstance(plan_row.get("payload"), str) else plan_row.get("payload")
        changed = False
        for day in payload.get("study_days", []):
            for item in day.get("tasks", []):
                if item.get("id") == task_id:
                    item["learning_run_passed"] = passed; item["scene_completed"] = passed; item["completion_evidence"] = evidence; item["status"] = "completed" if passed else item.get("status", "in_progress"); changed = True
        if changed:
            with db.connection() as connection: connection.execute("UPDATE plans SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), plan_row["id"]))
            core = [item.get("id") for day in payload.get("study_days", []) for item in day.get("tasks", []) if item.get("task_type") not in {"review", "final", "final_assessment", "final_task", "postgame"}]
            done = [item.get("id") for day in payload.get("study_days", []) for item in day.get("tasks", []) if item.get("learning_run_passed") is True]
            return workspace_id, task, [item for item in core if item in done], core
    return workspace_id, task, [], []


def submit_learning_run(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    response = str(body.get("response") or body.get("answer") or "").strip()
    if not response: raise LearningRunError("RESPONSE_REQUIRED", "必须提交本阶段回答或产出")
    idem = _request_key(body, "learning_submit_request")
    init_orm()
    with SessionLocal() as session:
        run = _run(session, run_id)
        if run.last_idempotency_key == idem and run.last_response:
            return copy.deepcopy(run.last_response)
        if body.get("expected_revision") is not None and int(body["expected_revision"]) != run.revision: raise LearningRunError("LEARNING_RUN_REVISION_CONFLICT", "Learning Run 已更新，请刷新", status_code=409)
        state = copy.deepcopy(run.state or {}); phase = run.phase; evidence = str(body.get("evidence") or ""); phase_hint_counts = state.get("phase_hint_counts") or {}; used_hint = bool(body.get("used_hint", False)) or int(phase_hint_counts.get(phase, 0)) > 0
        next_phase = {"explanation": "worked_example", "worked_example": "guided_practice", "guided_practice": "faded_guidance", "faded_guidance": "independent_attempt"}.get(phase)
        evaluation = None
        if phase in {"independent_attempt", "daily_variant_test", "transfer_test", "final_assessment"}:
            evaluation = evaluate_submission(response, evidence, independent=True, used_hint=used_hint, phase=phase)
            state.setdefault("attempts", []).append({"phase": phase, "score": evaluation["score"], "passed": evaluation["passed"], "used_hint": used_hint})
            if not evaluation["passed"]:
                run.status = "failed"; run.state = state; run.revision += 1; run.updated_at = _now(); session.add(LearningRunMessageORM(id=_id("learning_message"), run_id=run_id, role="evaluator", phase=phase, content=evaluation["feedback"], grounding=evaluation, idempotency_key=f"{idem}:evaluator"[:160], created_at=_now())); result = {**_public(session, run), "evaluation": evaluation}; run.last_idempotency_key = idem; run.last_response = result; session.commit(); session.refresh(run); return result
            if phase == "independent_attempt":
                state["independent_passes"] = int(state.get("independent_passes", 0)) + 1; next_phase = "daily_variant_test"
            elif phase == "transfer_test":
                state["independent_passes"] = int(state.get("independent_passes", 0)) + 1; next_phase = "review_passed" if state["independent_passes"] >= 3 else "transfer_test"; run.status = "passed" if next_phase == "review_passed" else "active"
            elif phase == "final_assessment":
                state["independent_passes"] = max(int(state.get("independent_passes", 0)), 1); next_phase = "final_passed"; run.status = "passed"
            else:
                next_phase = "passed"; run.status = "passed"; state["passed_score"] = evaluation["score"]; state["evidence"] = [*state.get("evidence", []), evidence or response]
        if phase == "spaced_recall": next_phase = "mixed_review"
        if phase == "mixed_review": next_phase = "transfer_test"
        run.phase = next_phase or phase; run.state = state; state.setdefault("phase_history", []).append(run.phase); run.revision += 1; run.updated_at = _now(); run.status = "passed" if run.phase in {"passed", "review_passed", "final_passed"} else "active"
        session.add(LearningRunMessageORM(id=_id("learning_message"), run_id=run_id, role="system", phase=run.phase, content=f"阶段已完成，进入 {run.phase}。" if run.phase != "passed" else "全部学习阶段已由 Evaluator 确认。", grounding=evaluation or {}, idempotency_key=f"{idem}:system"[:160], created_at=_now()))
        session.flush(); result = _public(session, run); run.last_idempotency_key = idem; run.last_response = {**result, "evaluation": evaluation}; session.commit(); session.refresh(run)
    if run.phase in {"passed", "review_passed", "final_passed"}:
        workspace_id, task, done_core, core = _update_task(run.task_id, passed=True, evidence=evidence or response)
        artifact_result = create_learning_artifact(workspace_id, run_id, run.task_id, run.phase, response, evidence or response, evaluation, int((run.state or {}).get("hints_used", 0)))
        payload = {"learning_run_id": run_id, "task_id": run.task_id, "lesson_id": run.lesson_id, "learning_phase": "daily_variant_test", "mastery_status": "first_pass_verified", "review_status": "any", "independent_passes": int((run.state or {}).get("independent_passes", 1)), "hints_used": int((run.state or {}).get("hints_used", 0)), "assessment_passed": True, "completed_plan_item_ids": done_core}
        payload["artifact_ids"] = [artifact_result["artifact_id"]]
        payload["story_object_ids"] = artifact_result["story_object_ids"]
        payload["artifact_types"] = [artifact_result["artifact_type"]]
        if run.phase == "review_passed":
            payload["learning_phase"] = "mixed_review"; payload["mastery_status"] = "review_verified"; payload["review_status"] = "due_items_cleared"; publish_learning_event(workspace_id, "REVIEW_PASSED", "learning_run", run_id, payload)
        elif run.phase == "final_passed":
            payload["learning_phase"] = "final_assessment"; payload["mastery_status"] = "final_verified"; payload["review_status"] = "complete"; publish_learning_event(workspace_id, "FINAL_ASSESSMENT_PASSED", "learning_run", run_id, payload)
        else:
            publish_learning_event(workspace_id, "DAILY_VARIANT_TEST_PASSED", "learning_run", run_id, payload)
        if run.phase == "passed" and core and len(done_core) >= len(core):
            payload["learning_phase"] = "first_pass_complete"; payload["assessment_passed"] = True; publish_learning_event(workspace_id, "FIRST_PASS_MILESTONE_REACHED", "learning_run", run_id, payload)
        from .expedition_service import publish_verified_learning_reward
        reward = publish_verified_learning_reward(workspace_id, run_id, int((run.state or {}).get("standard_task_minutes", 30)), phase=run.phase, released=True)
        result = {**result, "evaluation": evaluation, "artifact": artifact_result, "expedition_reward": reward}
        with SessionLocal() as session:
            stored = session.get(LearningRunORM, run_id)
            if stored:
                stored.last_response = result
                session.commit()
    return {**result, "evaluation": evaluation}
