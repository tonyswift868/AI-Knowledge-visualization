from __future__ import annotations

import hashlib
import copy
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from . import db
from .orm import NarrativeCampaignORM, NarrativeDialogueORM, NarrativeEventORM, SessionLocal, init_orm
from .persona_renderer import render_verified_content
from .story_pack import StoryPackError, load_story_pack, public_scene
from .services import tutor


PASS_EVENTS = {"INDEPENDENT_PROBLEM_PASSED", "DAILY_VARIANT_TEST_PASSED", "FIRST_PASS_MILESTONE_REACHED", "REVIEW_PASSED", "FINAL_ASSESSMENT_PASSED"}
FAIL_EVENTS = {"INDEPENDENT_PROBLEM_FAILED", "DAILY_VARIANT_TEST_FAILED", "REVIEW_FAILED", "FINAL_ASSESSMENT_FAILED"}
DELAY_EVENTS = {"PLAN_DELAY_DETECTED", "USER_RETURNED_AFTER_ABSENCE"}
EVENT_TYPES = PASS_EVENTS | FAIL_EVENTS | DELAY_EVENTS | {"LEARNING_SESSION_STARTED", "LEARNING_SESSION_COMPLETED", "EXPLANATION_COMPLETED", "GUIDED_PROBLEM_COMPLETED", "FIRST_PASS_MILESTONE_REACHED", "REVIEW_DUE", "FINAL_ASSESSMENT_UNLOCKED", "PLAN_REPLANNED"}
TRUSTED_SOURCES = {"evaluator", "task_completion", "learning_run", "system", "test"}


class NarrativeRuntimeError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status_code: int = 400):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _require_workspace(workspace_id: str) -> None:
    if not db.row("workspaces", "id", workspace_id):
        raise NarrativeRuntimeError("WORKSPACE_NOT_FOUND", "工作空间不存在", {"workspace_id": workspace_id}, 404)


def _campaign(campaign_id: str) -> NarrativeCampaignORM:
    init_orm()
    with SessionLocal() as session:
        item = session.get(NarrativeCampaignORM, campaign_id)
        if item is None:
            raise NarrativeRuntimeError("CAMPAIGN_NOT_FOUND", "Narrative Campaign 不存在", {"campaign_id": campaign_id}, 404)
        session.expunge(item)
        return item


def _safe_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key not in {"active_secrets", "revealed_facts_internal"}}


def _locked_pack(item: NarrativeCampaignORM) -> dict[str, Any]:
    pack = load_story_pack(item.story_pack_id)
    locked_checksum = (item.state or {}).get("story_pack_checksum")
    if str(pack["manifest"].get("version")) != str(item.story_pack_version) or (locked_checksum and pack["manifest"].get("checksum") != locked_checksum):
        raise NarrativeRuntimeError("STORY_PACK_VERSION_CONFLICT", "进行中的 Campaign 绑定的 Story Pack 版本或 checksum 已变化", {"story_pack_id": item.story_pack_id, "locked_version": item.story_pack_version, "current_version": pack["manifest"].get("version"), "locked_checksum": locked_checksum, "current_checksum": pack["manifest"].get("checksum")}, 409)
    return pack


def _public_campaign(item: NarrativeCampaignORM) -> dict[str, Any]:
    pack = _locked_pack(item)
    state = dict(item.state or {})
    scene = public_scene(pack, item.current_scene_id, _story_stage_for_scene(item.current_scene_id, item.current_story_stage))
    state = _safe_state(state)
    lines = scene.get("speaker_lines", [])
    line_index = int(state.get("current_line_index", 0))
    scene["current_line_index"] = line_index
    scene["line_count"] = len(lines)
    scene["lines_complete"] = line_index >= len(lines)
    scene["current_line"] = lines[line_index] if line_index < len(lines) else None
    scene["speaker_lines"] = lines[:line_index]
    return {
        "campaign_id": item.id,
        "workspace_id": item.workspace_id,
        "story_pack": {key: value for key, value in pack["manifest"].items() if key not in {"checksum", "provenance_notes"}},
        "current_arc_id": item.current_arc_id,
        "current_scene_id": item.current_scene_id,
        "current_story_stage": item.current_story_stage,
        "current_scene": scene,
        "state": state,
        "revision": item.revision,
    }


def _event_public(event: NarrativeEventORM) -> dict[str, Any]:
    return {"event_id": event.event_id, "workspace_id": event.workspace_id, "campaign_id": event.campaign_id, "source_type": event.source_type, "source_id": event.source_id, "event_type": event.event_type, "occurred_at": event.occurred_at.isoformat(), "payload": event.event_payload, "idempotency_key": event.idempotency_key, "generation_meta": event.generation_meta}


def _append_event(session, campaign: NarrativeCampaignORM, *, event_type: str, source_type: str, source_id: str, payload: dict[str, Any], idempotency_key: str, generation_meta: dict[str, Any] | None = None) -> tuple[NarrativeEventORM, bool]:
    existing = session.query(NarrativeEventORM).filter_by(campaign_id=campaign.id, idempotency_key=idempotency_key).first()
    if existing:
        return existing, True
    event = NarrativeEventORM(id=_new_id("narrative_event"), event_id=_new_id("evt"), workspace_id=campaign.workspace_id, campaign_id=campaign.id, source_type=source_type, source_id=source_id or "", event_type=event_type, occurred_at=_now(), event_payload=payload or {}, idempotency_key=idempotency_key, generation_meta=generation_meta or {})
    session.add(event)
    return event, False


def _scene_stage(scene: dict[str, Any]) -> str:
    return {"learning_session": "learning", "review_session": "review", "assessment": "assessment", "ending": "ending", "postgame": "postgame"}.get(scene.get("scene_type"), "story")


def _story_stage_for_scene(scene_id: str, runtime_stage: str) -> str:
    if scene_id in {"echo_review"}: return "review"
    if scene_id in {"echo_truth", "echo_low_point", "echo_final_prep", "echo_final_assessment", "echo_ending_complete", "echo_ending_archive"}: return "truth"
    if scene_id in {"echo_postgame"}: return "postgame"
    if scene_id in {"echo_first_pass", "echo_rupture"}: return "first_pass"
    return "intro" if runtime_stage in {"story", "intro"} else runtime_stage


def _gate_for_scene(pack: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any] | None:
    binding_id = scene.get("learning_gate_ref")
    if not binding_id:
        return None
    binding = next((item for item in pack["learning_bindings"].get("bindings", []) if item.get("binding_id") == binding_id), None)
    if binding is None:
        raise NarrativeRuntimeError("STORY_PACK_LEARNING_GATE_INVALID", "当前场景门禁无效")
    return {"binding_id": binding_id, "status": "pending", "required_event": str(binding.get("assessment_requirement", "")).upper(), "required_learning_phase": binding.get("required_learning_phase"), "required_mastery_status": binding.get("required_mastery_status"), "required_review_status": binding.get("required_review_status"), "required_plan_item_ids": binding.get("required_plan_item_ids", []), "minimum_independent_passes": binding.get("minimum_independent_passes", 0), "allowed_hint_level": binding.get("allowed_hint_level")}


def create_campaign(workspace_id: str, story_pack_id: str = "dev_fixture_minimal") -> dict[str, Any]:
    _require_workspace(workspace_id)
    try:
        pack = load_story_pack(story_pack_id)
    except StoryPackError as exc:
        raise NarrativeRuntimeError(exc.code, exc.message, exc.details, 400) from exc
    scene = pack["scenes"][pack["manifest"]["entry_scene_id"]]
    campaign_id = _new_id("campaign")
    state = {"campaign_id": campaign_id, "workspace_id": workspace_id, "story_pack_id": story_pack_id, "story_pack_version": str(pack["manifest"]["version"]), "story_pack_checksum": pack["manifest"]["checksum"], "current_arc_id": scene.get("arc_id", ""), "current_scene_id": scene["scene_id"], "current_story_stage": _scene_stage(scene), "current_line_index": 0, "read_line_ids": [], "dialogue_log_cursor": 0, "unlocked_scene_ids": [scene["scene_id"]], "completed_scene_ids": [], "pending_learning_gate": _gate_for_scene(pack, scene), "relationship_state": {}, "character_states": {}, "revealed_facts": [], "active_secrets": [], "player_choices": [], "promises": [], "missed_sessions": [], "story_flags": [], "ending_state": None, "postgame_state": None}
    init_orm()
    with SessionLocal() as session:
        item = NarrativeCampaignORM(id=campaign_id, workspace_id=workspace_id, story_pack_id=story_pack_id, story_pack_version=str(pack["manifest"]["version"]), current_arc_id=scene.get("arc_id", ""), current_scene_id=scene["scene_id"], current_story_stage=_scene_stage(scene), state=state, revision=0, created_at=_now(), updated_at=_now())
        session.add(item)
        _append_event(session, item, event_type="CAMPAIGN_STARTED", source_type="system", source_id=campaign_id, payload={"entry_scene_id": scene["scene_id"]}, idempotency_key="campaign:start")
        session.commit()
        session.refresh(item)
        return _public_campaign(item)


def list_campaigns(workspace_id: str) -> list[dict[str, Any]]:
    _require_workspace(workspace_id)
    init_orm()
    with SessionLocal() as session:
        items = session.query(NarrativeCampaignORM).filter_by(workspace_id=workspace_id).order_by(NarrativeCampaignORM.created_at.desc()).all()
        return [{"campaign_id": item.id, "workspace_id": item.workspace_id, "story_pack_id": item.story_pack_id, "story_pack_version": item.story_pack_version, "current_scene_id": item.current_scene_id, "revision": item.revision, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()} for item in items]


def get_campaign(campaign_id: str) -> dict[str, Any]:
    return _public_campaign(_campaign(campaign_id))


def _find_target(pack: dict[str, Any], current_scene: dict[str, Any], action: str, choice_id: str | None) -> str:
    if choice_id:
        choice = next((item for item in current_scene.get("choices", []) if item.get("choice_id") == choice_id), None)
        if choice is None:
            raise NarrativeRuntimeError("CHOICE_NOT_AVAILABLE", "当前场景没有这个选项")
        return str(choice["next_scene_id"])
    if current_scene.get("choices"):
        raise NarrativeRuntimeError("CHOICE_REQUIRED", "当前场景必须先完成固定选择")
    aliases = {action, "continue"}
    if action == "continue":
        aliases.update({"learning_gate_passed", "assessment_passed"})
    rule = next((item for item in current_scene.get("next_scene_rules", []) if item.get("when") in aliases), None)
    if not rule:
        raise NarrativeRuntimeError("SCENE_CANNOT_ADVANCE", "当前场景没有可用的继续动作")
    return str(rule["target_scene_id"])


def _assert_main_anchor_order(pack: dict[str, Any], current_id: str, target_id: str) -> None:
    anchors = pack["story_bible"].get("main_plot_anchors", []) or []
    if current_id in anchors and target_id in anchors and anchors.index(target_id) > anchors.index(current_id) + 1:
        raise NarrativeRuntimeError("MAIN_ANCHOR_SKIPPED", "固定主线锚点不能跳过")


def _condition_satisfied(condition: Any, state: dict[str, Any]) -> bool:
    token = str(condition or "").strip().lower()
    if not token:
        return True
    flags = {str(item).strip().lower() for item in state.get("story_flags", [])}
    if token in flags:
        return True
    gate = state.get("pending_learning_gate") or {}
    if token == "learning_gate_passed":
        return gate.get("status") == "passed"
    if token == "choice_made":
        return bool(state.get("player_choices"))
    if token.endswith("_completed"):
        completed_scene = token[:-10]
        if completed_scene in {str(item).lower() for item in state.get("completed_scene_ids", [])}:
            return True
    if token == "assessment_passed":
        return any(flag.endswith("assessment_passed") or flag == "final_assessment_passed" for flag in flags)
    if token == "final_assessment_passed":
        return "final_assessment_passed" in flags or "assessment_passed" in flags
    if token == "dev_learning_completed":
        return gate.get("status") == "passed" or any(flag.endswith("problem_passed") for flag in flags)
    return False


def _assert_entry_conditions(pack: dict[str, Any], scene: dict[str, Any], state: dict[str, Any]) -> None:
    conditions = scene.get("entry_conditions", []) or []
    binding = next((item for item in pack.get("learning_bindings", {}).get("bindings", []) if item.get("binding_id") == scene.get("learning_gate_ref")), None)
    gate_event = str((binding or {}).get("assessment_requirement") or "").lower()
    if gate_event == "daily_variant_test":
        gate_event = "daily_variant_test_passed"
    missing = [condition for condition in conditions if str(condition).strip().lower() != gate_event and not _condition_satisfied(condition, state)]
    if missing:
        raise NarrativeRuntimeError("SCENE_ENTRY_CONDITION_BLOCKED", "当前剧情节点的前置条件尚未满足", {"scene_id": scene.get("scene_id"), "missing_conditions": missing}, 409)


def _assert_ending_conditions(pack: dict[str, Any], target_id: str, choice_id: str | None, state: dict[str, Any]) -> None:
    conditions = (pack.get("story_bible", {}).get("ending_conditions", {}) or {}).get(target_id)
    if not isinstance(conditions, dict):
        return
    required_event = str(conditions.get("required_event") or "").strip().lower()
    flags = {str(item).strip().lower() for item in state.get("story_flags", [])}
    if required_event and required_event not in flags:
        raise NarrativeRuntimeError("ENDING_CONDITION_BLOCKED", "结局仍在等待最终考核事件", {"scene_id": target_id, "required_event": conditions.get("required_event")}, 409)
    required_choice = str(conditions.get("required_choice") or "").strip()
    if required_choice and required_choice != str(choice_id or ""):
        raise NarrativeRuntimeError("ENDING_CHOICE_REQUIRED", "必须选择与该结局对应的发送方式", {"scene_id": target_id, "required_choice": required_choice}, 409)


def _assert_exit_conditions(scene: dict[str, Any], state: dict[str, Any], action: str, choice_id: str | None) -> None:
    missing: list[Any] = []
    for condition in scene.get("exit_conditions", []) or []:
        token = str(condition or "").strip().lower()
        if token == "choice_made":
            satisfied = action == "choice_made" and bool(choice_id)
        elif token == "assessment_passed":
            satisfied = action == "assessment_passed" or _condition_satisfied(condition, state)
        else:
            satisfied = _condition_satisfied(condition, state)
        if not satisfied:
            missing.append(condition)
    if missing:
        raise NarrativeRuntimeError("SCENE_EXIT_CONDITION_BLOCKED", "当前剧情节点的退出条件尚未满足", {"scene_id": scene.get("scene_id"), "missing_conditions": missing}, 409)


def _transition(campaign_id: str, *, action: str, choice_id: str | None, expected_revision: int | None, idempotency_key: str, source_type: str = "player") -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        item = session.get(NarrativeCampaignORM, campaign_id)
        if item is None:
            raise NarrativeRuntimeError("CAMPAIGN_NOT_FOUND", "Narrative Campaign 不存在", {"campaign_id": campaign_id}, 404)
        existing_event = session.query(NarrativeEventORM).filter_by(campaign_id=campaign_id, idempotency_key=idempotency_key).first()
        if existing_event:
            stored = (existing_event.event_payload or {}).get("result")
            if stored:
                return stored
            return _public_campaign(item)
        if expected_revision is not None and item.revision != expected_revision:
            raise NarrativeRuntimeError("CAMPAIGN_REVISION_CONFLICT", "Campaign 状态已更新，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": item.revision}, 409)
        pack = _locked_pack(item)
        current_scene = pack["scenes"][item.current_scene_id]
        state = copy.deepcopy(item.state or {})
        line_index = int(state.get("current_line_index", 0))
        lines = current_scene.get("speaker_lines", []) or []
        if choice_id and line_index < len(lines):
            raise NarrativeRuntimeError("LINE_SEQUENCE_INCOMPLETE", "当前场景台词尚未播放完")
        if not choice_id and line_index < len(lines):
            line = lines[line_index]
            state["current_line_index"] = line_index + 1
            state.setdefault("read_line_ids", []).append(line.get("line_id") or f"{current_scene['scene_id']}:{line_index}")
            state["dialogue_log_cursor"] = int(state.get("dialogue_log_cursor", 0)) + 1
            expected = item.revision; next_revision = expected + 1
            updated_at = _now()
            cas = session.execute(update(NarrativeCampaignORM).where(NarrativeCampaignORM.id == campaign_id, NarrativeCampaignORM.revision == expected).values(state=state, revision=next_revision, updated_at=updated_at))
            if cas.rowcount != 1:
                session.rollback(); raise NarrativeRuntimeError("CAMPAIGN_REVISION_CONFLICT", "Campaign 状态已更新，请刷新后重试", {"expected_revision": expected}, 409)
            item.state = state; item.revision = next_revision; item.updated_at = updated_at
            payload = {"scene_id": current_scene["scene_id"], "line_id": line.get("line_id") or f"{current_scene['scene_id']}:{line_index}", "line_index": line_index, "revision": next_revision}
            event, _ = _append_event(session, item, event_type="LINE_READ", source_type=source_type, source_id=campaign_id, payload=payload, idempotency_key=idempotency_key)
            result = _public_campaign(item); event.event_payload = {**payload, "result": result}; session.commit(); return result
        gate = (item.state or {}).get("pending_learning_gate")
        if gate and gate.get("status") != "passed":
            raise NarrativeRuntimeError("LEARNING_GATE_BLOCKED", "学习结果尚未通过，剧情节点保持锁定", {"binding_id": gate.get("binding_id"), "required_event": gate.get("required_event")}, 409)
        target_id = _find_target(pack, current_scene, action, choice_id)
        _assert_exit_conditions(current_scene, item.state or {}, action, choice_id)
        _assert_main_anchor_order(pack, item.current_scene_id, target_id)
        target = pack["scenes"].get(target_id)
        if target is None:
            raise NarrativeRuntimeError("SCENE_NOT_FOUND", "目标场景不存在", {"scene_id": target_id}, 404)
        state = copy.deepcopy(item.state or {})
        state.setdefault("completed_scene_ids", []).append(item.current_scene_id) if item.current_scene_id not in state.setdefault("completed_scene_ids", []) else None
        if choice_id:
            state.setdefault("player_choices", []).append({"scene_id": item.current_scene_id, "choice_id": choice_id})
        if action == "assessment_passed":
            state.setdefault("story_flags", []).append("assessment_passed") if "assessment_passed" not in state.setdefault("story_flags", []) else None
        _assert_entry_conditions(pack, target, state)
        if target.get("scene_type") == "ending":
            _assert_ending_conditions(pack, target_id, choice_id, state)
        state["current_scene_id"] = target_id
        state["current_arc_id"] = target.get("arc_id", "")
        state["current_story_stage"] = _scene_stage(target)
        state["current_line_index"] = 0
        state["read_line_ids"] = []
        state["dialogue_log_cursor"] = int(state.get("dialogue_log_cursor", 0))
        state["pending_learning_gate"] = _gate_for_scene(pack, target)
        if target_id not in state.setdefault("unlocked_scene_ids", []):
            state["unlocked_scene_ids"].append(target_id)
        if target.get("scene_type") in {"ending", "postgame"}:
            state["ending_state"] = target_id if target.get("scene_type") == "ending" else state.get("ending_state")
            state["postgame_state"] = target_id if target.get("scene_type") == "postgame" else state.get("postgame_state")
        expected = item.revision
        next_revision = expected + 1
        updated_at = _now()
        cas = session.execute(update(NarrativeCampaignORM).where(NarrativeCampaignORM.id == campaign_id, NarrativeCampaignORM.revision == expected).values(state=state, current_scene_id=target_id, current_arc_id=target.get("arc_id", ""), current_story_stage=_scene_stage(target), revision=next_revision, updated_at=updated_at))
        if cas.rowcount != 1:
            session.rollback()
            raise NarrativeRuntimeError("CAMPAIGN_REVISION_CONFLICT", "Campaign 状态已更新，请刷新后重试", {"expected_revision": expected}, 409)
        item.state = state; item.current_scene_id = target_id; item.current_arc_id = target.get("arc_id", ""); item.current_story_stage = _scene_stage(target); item.revision = next_revision; item.updated_at = updated_at
        payload = {"from_scene_id": current_scene["scene_id"], "to_scene_id": target_id, "choice_id": choice_id, "action": action, "revision": next_revision}
        event, _ = _append_event(session, item, event_type="SCENE_ADVANCED", source_type=source_type, source_id=campaign_id, payload=payload, idempotency_key=idempotency_key)
        result = _public_campaign(item)
        event.event_payload = {**payload, "result": result}
        session.commit()
        return result


def advance_campaign(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return _transition(campaign_id, action=str(body.get("action", "continue")), choice_id=None, expected_revision=body.get("expected_revision"), idempotency_key=str(body.get("idempotency_key") or f"advance:{body.get('expected_revision')}:{body.get('action', 'continue')}"))


def choose_campaign(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    choice_id = str(body.get("choice_id") or "")
    if not choice_id:
        raise NarrativeRuntimeError("CHOICE_REQUIRED", "必须提供 choice_id")
    return _transition(campaign_id, action="choice_made", choice_id=choice_id, expected_revision=body.get("expected_revision"), idempotency_key=str(body.get("idempotency_key") or f"choice:{body.get('expected_revision')}:{choice_id}"))


def available_actions(campaign_id: str) -> dict[str, Any]:
    item = _campaign(campaign_id); pack = _locked_pack(item); scene = pack["scenes"][item.current_scene_id]; gate = (item.state or {}).get("pending_learning_gate")
    choices = [{"choice_id": choice.get("choice_id"), "label": choice.get("label"), "enabled": not gate or gate.get("status") == "passed", "blocked_reason": "LEARNING_GATE_BLOCKED" if gate and gate.get("status") != "passed" else None} for choice in scene.get("choices", [])]
    return {"campaign_id": campaign_id, "revision": item.revision, "learning_gate": gate, "actions": [{"id": "continue", "enabled": not gate or gate.get("status") == "passed"}, {"id": "enter_learning", "enabled": bool(gate)}, {"id": "request_hint", "enabled": bool(gate)}, {"id": "view_task", "enabled": True}], "choices": choices}


def campaign_history(campaign_id: str) -> list[dict[str, Any]]:
    item = _campaign(campaign_id); _locked_pack(item); init_orm()
    with SessionLocal() as session:
        events = session.query(NarrativeEventORM).filter_by(campaign_id=item.id).order_by(NarrativeEventORM.occurred_at.asc()).all()
        return [_event_public(event) for event in events]


def _binding_passes(campaign: NarrativeCampaignORM, event_type: str, payload: dict[str, Any]) -> tuple[bool, str]:
    gate = (campaign.state or {}).get("pending_learning_gate")
    if not gate or gate.get("status") == "passed":
        return False, "no_pending_gate"
    pack = _locked_pack(campaign)
    binding = next((item for item in pack["learning_bindings"].get("bindings", []) if item.get("binding_id") == gate.get("binding_id")), None)
    if not binding:
        return False, "binding_missing"
    expected_event = str(binding.get("assessment_requirement", "")).upper()
    if expected_event == "DAILY_VARIANT_TEST":
        expected_event = "DAILY_VARIANT_TEST_PASSED"
    if event_type.upper() != expected_event:
        return False, "event_type_mismatch"
    required_phase = str(binding.get("required_learning_phase", "")).lower()
    if required_phase and str(payload.get("learning_phase", "")).lower() != required_phase:
        return False, "learning_phase_mismatch"
    required_mastery = str(binding.get("required_mastery_status", "")).lower()
    if required_mastery and required_mastery != "any" and str(payload.get("mastery_status", "")).lower() != required_mastery:
        return False, "mastery_status_mismatch"
    required_review = str(binding.get("required_review_status", "")).lower()
    if required_review and required_review != "any" and str(payload.get("review_status", "")).lower() != required_review:
        return False, "review_status_mismatch"
    required_items = binding.get("required_plan_item_ids", []) or []
    if required_items and not set(required_items).issubset(set(payload.get("completed_plan_item_ids", []))):
        return False, "plan_items_incomplete"
    required_artifacts = binding.get("required_artifacts", []) or []
    if required_artifacts and not set(required_artifacts).issubset(set(payload.get("artifact_types", []))):
        return False, "artifacts_incomplete"
    if int(payload.get("independent_passes", 0)) < int(binding.get("minimum_independent_passes", 0) or 0):
        return False, "independent_passes_insufficient"
    allowed_hint = str(binding.get("allowed_hint_level", "any")).lower()
    hints = int(payload.get("hints_used", 0) or 0)
    if allowed_hint in {"none", "none_on_variant"} and hints > 0:
        return False, "hint_level_exceeded"
    if expected_event in {"FIRST_PASS_MILESTONE_REACHED", "FINAL_ASSESSMENT_PASSED"} and not bool(payload.get("assessment_passed", True)):
        return False, "assessment_not_passed"
    return True, "passed"


def _apply_learning_event(session, campaign: NarrativeCampaignORM, event_type: str, payload: dict[str, Any]) -> bool:
    state = copy.deepcopy(campaign.state or {})
    state.setdefault("story_flags", []).append(event_type) if event_type not in state.setdefault("story_flags", []) else None
    gate = state.get("pending_learning_gate")
    if gate:
        if event_type in PASS_EVENTS:
            passed, reason = _binding_passes(campaign, event_type, payload)
            if not passed:
                gate["last_rejected_event"] = {"event_type": event_type, "reason": reason}
                state["pending_learning_gate"] = gate
                campaign.state = state; campaign.updated_at = _now()
                return False
            gate["status"] = "passed"; gate["passed_event_type"] = event_type; gate["passed_source_id"] = payload.get("source_id", "")
            gate["resolved_by"] = next((item.get("on_pass_event") for item in _locked_pack(campaign)["learning_bindings"].get("bindings", []) if item.get("binding_id") == gate.get("binding_id")), event_type)
            state["pending_learning_gate"] = gate
        elif event_type in FAIL_EVENTS:
            gate["status"] = "failed"; gate["resolved_by"] = next((item.get("on_fail_event") for item in _locked_pack(campaign)["learning_bindings"].get("bindings", []) if item.get("binding_id") == gate.get("binding_id")), event_type); state["pending_learning_gate"] = gate
        elif event_type in DELAY_EVENTS:
            gate["status"] = "pending"; gate["resolved_by"] = next((item.get("on_delay_event") for item in _locked_pack(campaign)["learning_bindings"].get("bindings", []) if item.get("binding_id") == gate.get("binding_id")), event_type); state["missed_sessions"] = [*state.get("missed_sessions", []), payload.get("date") or _now().date().isoformat()]
    campaign.state = state; campaign.updated_at = _now()
    return True


def ingest_learning_event(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    event_type = str(body.get("event_type") or "")
    source_type = str(body.get("source_type") or "")
    if event_type not in EVENT_TYPES:
        raise NarrativeRuntimeError("LEARNING_EVENT_TYPE_INVALID", "学习事件类型不受支持")
    if not source_type:
        raise NarrativeRuntimeError("LEARNING_EVENT_SOURCE_REQUIRED", "必须提供学习事件来源")
    if event_type in PASS_EVENTS and source_type not in TRUSTED_SOURCES:
        raise NarrativeRuntimeError("LEARNING_EVENT_SOURCE_UNTRUSTED", "通过事件必须来自 Tutor/Evaluator 或系统", status_code=403)
    init_orm()
    with SessionLocal() as session:
        campaign = session.get(NarrativeCampaignORM, campaign_id)
        if campaign is None:
            raise NarrativeRuntimeError("CAMPAIGN_NOT_FOUND", "Narrative Campaign 不存在", status_code=404)
        idem = str(body.get("idempotency_key") or f"{source_type}:{body.get('source_id', '')}:{event_type}")
        existing = session.query(NarrativeEventORM).filter_by(campaign_id=campaign_id, idempotency_key=idem).first()
        if existing:
            stored = (existing.event_payload or {}).get("result")
            return {"duplicate": True, "event": _event_public(existing), "campaign": stored or _public_campaign(campaign)}
        if body.get("workspace_id") and body["workspace_id"] != campaign.workspace_id:
            raise NarrativeRuntimeError("WORKSPACE_SCOPE_VIOLATION", "Campaign 不属于该 Workspace", status_code=403)
        if body.get("expected_revision") is not None and int(body["expected_revision"]) != campaign.revision:
            raise NarrativeRuntimeError("CAMPAIGN_REVISION_CONFLICT", "Campaign 状态已更新，请刷新后重试", status_code=409)
        payload = dict(body.get("payload") or {}); payload["source_id"] = body.get("source_id", "")
        event, duplicate = _append_event(session, campaign, event_type=event_type, source_type=source_type, source_id=str(body.get("source_id") or ""), payload=payload, idempotency_key=idem, generation_meta=dict(body.get("generation_meta") or {}))
        if not duplicate:
            _apply_learning_event(session, campaign, event_type, payload)
            expected = campaign.revision
            next_revision = expected + 1
            cas = session.execute(update(NarrativeCampaignORM).where(NarrativeCampaignORM.id == campaign_id, NarrativeCampaignORM.revision == expected).values(state=campaign.state, revision=next_revision, updated_at=campaign.updated_at))
            if cas.rowcount != 1:
                session.rollback()
                raise NarrativeRuntimeError("CAMPAIGN_REVISION_CONFLICT", "Campaign 状态已更新，请刷新后重试", {"expected_revision": expected}, 409)
            campaign.revision = next_revision
            result = _public_campaign(campaign)
            event.event_payload = {**payload, "result": result}
            session.commit()
        return {"duplicate": duplicate, "event": _event_public(event), "campaign": _public_campaign(campaign)}


def publish_learning_event(workspace_id: str, event_type: str, source_type: str, source_id: str, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    init_orm(); results = []
    with SessionLocal() as session:
        campaigns = session.query(NarrativeCampaignORM).filter_by(workspace_id=workspace_id).all()
        ids = [item.id for item in campaigns]
    for campaign_id in ids:
        try:
            results.append(ingest_learning_event(campaign_id, {"event_type": event_type, "source_type": source_type, "source_id": source_id, "payload": payload or {}, "idempotency_key": f"{source_type}:{source_id}:{event_type}"}))
        except NarrativeRuntimeError:
            continue
    return results


def _route_dialogue(text: str, requested: str | None = None) -> str:
    if requested in {"story", "tutor", "planning"}:
        return requested
    lower = text.lower()
    if any(token in text for token in ("计划", "排期", "截止", "今天没时间", "缺席")) or any(token in lower for token in ("plan", "schedule")):
        return "planning"
    if any(token in text for token in ("怎么做", "解释", "题目", "代码", "答案", "提示", "为什么", "不会")) or any(token in lower for token in ("code", "hint", "explain")):
        return "tutor"
    return "story"


def _planning_preview(workspace_id: str, reason: str) -> dict[str, Any]:
    plans = db.rows("plans", "workspace_id", workspace_id)
    current_row = next((item for item in reversed(plans) if item.get("status") in {"active", "draft"}), plans[-1] if plans else None)
    if not current_row:
        return {"plan_id": None, "current_version": None, "changes": [], "requires_confirmation": True}
    plan = json.loads(current_row.get("payload") or "{}") if isinstance(current_row.get("payload"), str) else current_row.get("payload", {})
    pending = [task for day in plan.get("study_days", []) for task in day.get("tasks", []) if task.get("status") != "completed"]
    first_pending = pending[0] if pending else None
    return {
        "plan_id": plan.get("id") or current_row.get("id"),
        "current_version": plan.get("version"),
        "reason": reason,
        "preserve_completed": True,
        "changes": ([{"type": "protect_completed_work", "count": sum(1 for day in plan.get("study_days", []) for task in day.get("tasks", []) if task.get("status") == "completed")}, {"type": "replan_pending_queue", "task_id": first_pending.get("id"), "task_title": first_pending.get("title")} ] if first_pending else [{"type": "no_pending_tasks"}]),
        "requires_confirmation": True,
        "confirmation_endpoint": f"/api/plans/{plan.get('id') or current_row.get('id')}/replan",
    }


def campaign_message(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    item = _campaign(campaign_id); pack = _locked_pack(item); text = str(body.get("message") or "").strip(); idem = str(body.get("idempotency_key") or f"message:{hashlib.sha256(text.encode('utf-8')).hexdigest()}")
    if not text:
        raise NarrativeRuntimeError("MESSAGE_REQUIRED", "消息不能为空")
    init_orm()
    with SessionLocal() as session:
        existing = session.query(NarrativeDialogueORM).filter_by(campaign_id=campaign_id, idempotency_key=idem).first()
        if existing:
            return (existing.generation_meta or {}).get("response", {"route": existing.route, "message": existing.content, "revision": item.revision})

    def persist(response: dict[str, Any], character_id: str, route: str) -> dict[str, Any]:
        init_orm()
        with SessionLocal() as session:
            dialogue = NarrativeDialogueORM(id=_new_id("dialogue"), campaign_id=campaign_id, scene_id=item.current_scene_id, line_id="", role="user_and_system", character_id=character_id, route=route, content=response.get("message", ""), generation_meta={"response": response})
            dialogue.idempotency_key = idem
            session.add(dialogue)
            session.commit()
        return response
    route = _route_dialogue(text, body.get("intent"))
    scene = pack["scenes"][item.current_scene_id]
    character_id = (scene.get("character_layers") or [{"character_id": ""}])[0].get("character_id", "")
    character = pack["characters"].get(character_id, {})
    if route == "tutor":
        lesson_id = str(body.get("lesson_id") or "")
        result = tutor(item.workspace_id, lesson_id, text, body.get("session_id"))
        rendered = render_verified_content(result.get("message", ""), character_id=character_id or "system_tutor", story_stage=item.current_story_stage, emotion="focused", speech_rules=character.get("speech_rules"), forbidden_speech=character.get("forbidden_speech"))
        return persist({"route": route, "message": rendered["rendered_text"], "grounding": result, "persona": rendered, "revision": item.revision}, character_id, route)
    if route == "planning":
        preview = _planning_preview(item.workspace_id, text)
        return persist({"route": route, "message": "我先生成了一份调整预览：已完成的学习不会被重置，待完成队列会重新评估日期、容量和风险。确认后才会激活新计划。", "planning_preview": preview, "next_action": {"type": "plan_replan_preview", "plan_id": preview.get("plan_id"), "requires_confirmation": True, "workspace_id": item.workspace_id}, "revision": item.revision}, character_id, route)
    line = (scene.get("speaker_lines") or [{"speaker_id": character_id, "text": "我在等一份可以核验的结果。"}])[0]
    adaptive_objects = [obj for obj in (item.state or {}).get("adaptive_story_objects", []) if obj.get("status") in {"verified", "revised", "final"}]
    if adaptive_objects:
        latest = adaptive_objects[-1]
        fact = str((latest.get("verified_facts") or [""])[0])
        fact = re.sub(r"<[^>]*>", "[标记已移除]", fact)[:260]
        message = f"{character.get('display_name', character_id)}：我还记得你留下的{latest.get('display_name', '学习成果')}。你说过：{fact[:260]} 这份结果现在正用于当前档案。"
        return persist({"route": route, "message": message, "scene_id": item.current_scene_id, "public_facts_only": True, "referenced_story_object_ids": [latest.get("story_object_id")], "revision": item.revision}, character_id, route)
    return persist({"route": route, "message": f"{character.get('display_name', character_id)}：{line.get('text', '')}", "scene_id": item.current_scene_id, "public_facts_only": True, "revision": item.revision}, character_id, route)
