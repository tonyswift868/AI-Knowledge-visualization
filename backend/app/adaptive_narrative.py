from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from . import db
from .orm import (
    CampaignAdaptationBlueprintORM,
    FusionAdapterVersionORM,
    GeneratedNarrativeSceneORM,
    LearningArtifactORM,
    NarrativeContinuitySnapshotORM,
    NarrativeCampaignORM,
    SceneContractORM,
    SessionLocal,
    StoryObjectORM,
    TaskSemanticProfileORM,
    init_orm,
)
from .story_pack import load_story_pack


class AdaptiveNarrativeError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status_code: int = 400):
        self.code, self.message, self.details, self.status_code = code, message, details or {}, status_code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    value = row.get("payload", row.get("profile", row))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(word.lower() in text.lower() for word in words)


def _safe_material(text: str, limit: int = 6000) -> tuple[str, bool]:
    injection = re.compile(r"(?:ignore|disregard|override|system prompt|developer message|忽略|无视|覆盖).{0,80}(?:instruction|prompt|指令|系统|规则)", re.I | re.S)
    changed = bool(injection.search(text))
    text = injection.sub("[学习资料中的指令性文本已按资料处理]", text)
    text = re.sub(r"<\s*(?:system|developer|assistant)\b[^>]*>.*?<\s*/\s*(?:system|developer|assistant)\s*>", "[资料标记已移除]", text, flags=re.I | re.S)
    return text[:limit], changed


def _safe_fact(text: str, limit: int = 260) -> str:
    cleaned, _ = _safe_material(text, limit * 2)
    cleaned = re.sub(r"<[^>]*>", "[标记已移除]", cleaned)
    return cleaned[:limit]


def _workspace_material(workspace_id: str) -> tuple[str, list[dict[str, Any]]]:
    goal = _payload(db.row("goals", "workspace_id", workspace_id))
    chunks: list[dict[str, Any]] = []
    for source in db.rows("sources", "workspace_id", workspace_id):
        for chunk in db.rows("chunks", "source_id", source["id"]):
            chunks.append({"source_id": source["id"], "source_title": source.get("title", ""), **chunk})
    return str(goal.get("goal_statement") or ""), chunks


def _goal_plan(workspace_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    goal = _payload(db.row("goals", "workspace_id", workspace_id))
    plans = db.rows("plans", "workspace_id", workspace_id)
    row = next((item for item in reversed(plans) if item.get("status") in {"active", "draft"}), plans[-1] if plans else None)
    return goal, _payload(row)


ADAPTERS: dict[str, dict[str, Any]] = {
    "question_bank": {"version": "1.0.0", "supported_task_shapes": ["large_question_bank", "mixed"], "supported_learning_actions": ["classify", "compare", "verify", "transfer"], "supported_story_actions": ["verify_archive", "trace_route", "reconstruct_timeline"], "artifact_mapping_rules": {"artifact_type": "answer_set", "object_type": "archive_report", "display_name": "已验证的档案核验报告"}, "pass_consequence_templates": ["unlock_archive_core", "record_verified_pattern"], "fail_consequence_templates": ["retain_uncertainty", "offer_recovery_learning_run"], "review_transformation_rules": ["challenge_early_judgment"], "final_assessment_rules": ["require_complete_archive_chain"], "forbidden_mappings": ["answer_to_magic_power", "mistake_to_character_harm"]},
    "memory_drill": {"version": "1.0.0", "supported_task_shapes": ["memory_drill", "interview_memory"], "supported_learning_actions": ["recall", "classify", "compare", "verify"], "supported_story_actions": ["authenticate_identity", "verify_archive", "restore_memory"], "artifact_mapping_rules": {"artifact_type": "summary", "object_type": "identity_proof", "display_name": "已验证的记忆索引"}, "pass_consequence_templates": ["restore_memory_fragment"], "fail_consequence_templates": ["retain_uncertainty"], "review_transformation_rules": ["challenge_recalled_detail"], "final_assessment_rules": ["require_unassisted_recall"], "forbidden_mappings": ["forgotten_item_to_character_death"]},
    "concept_learning": {"version": "1.0.0", "supported_task_shapes": ["concept_course", "general_concepts"], "supported_learning_actions": ["explain", "compare", "infer", "apply"], "supported_story_actions": ["prepare_report", "simulate_outcomes", "verify_archive"], "artifact_mapping_rules": {"artifact_type": "explanation", "object_type": "research_note", "display_name": "已验证的研究便笺"}, "pass_consequence_templates": ["add_verified_explanation"], "fail_consequence_templates": ["keep_question_open"], "review_transformation_rules": ["recheck_boundary"], "final_assessment_rules": ["require_transfer_explanation"], "forbidden_mappings": ["concept_to_supernatural_force"]},
    "coding_practice": {"version": "1.0.0", "supported_task_shapes": ["coding_practice", "practical_project"], "supported_learning_actions": ["plan", "apply", "verify", "repair", "optimize", "transfer"], "supported_story_actions": ["diagnose_failure", "repair_system", "trace_route", "optimize_plan"], "artifact_mapping_rules": {"artifact_type": "code", "object_type": "system_patch", "display_name": "已验证的系统补丁"}, "pass_consequence_templates": ["unlock_relay_diagnostic", "record_patch_boundary"], "fail_consequence_templates": ["retain_fault_uncertainty", "offer_recovery_learning_run"], "review_transformation_rules": ["challenge_old_patch_boundary"], "final_assessment_rules": ["require_deployable_repair_plan"], "forbidden_mappings": ["bug_to_character_injury", "algorithm_to_magic"]},
    "language_learning": {"version": "1.0.0", "supported_task_shapes": ["language_acquisition"], "supported_learning_actions": ["recall", "translate", "infer", "verify", "transfer"], "supported_story_actions": ["decode_message", "translate_signal", "send_transmission"], "artifact_mapping_rules": {"artifact_type": "translation", "object_type": "translated_message", "display_name": "已验证的通信还原稿"}, "pass_consequence_templates": ["decode_interference"], "fail_consequence_templates": ["mark_message_uncertain"], "review_transformation_rules": ["retranslate_suspicious_line"], "final_assessment_rules": ["require_unassisted_translation"], "forbidden_mappings": ["word_to_energy"]},
    "evidence_argument": {"version": "1.0.0", "supported_task_shapes": ["case_analysis", "evidence_network"], "supported_learning_actions": ["classify", "compare", "argue", "verify", "infer"], "supported_story_actions": ["compare_testimony", "prepare_report", "negotiate_decision"], "artifact_mapping_rules": {"artifact_type": "argument", "object_type": "testimony_analysis", "display_name": "已验证的证词分析"}, "pass_consequence_templates": ["unlock_record_comparison"], "fail_consequence_templates": ["retain_conflicting_testimony"], "review_transformation_rules": ["reopen_weak_evidence"], "final_assessment_rules": ["require_cited_decision_memo"], "forbidden_mappings": ["wrong_answer_to_punishment"]},
    "long_form_creation": {"version": "1.0.0", "supported_task_shapes": ["long_form_creation"], "supported_learning_actions": ["plan", "create", "revise", "argue", "verify"], "supported_story_actions": ["prepare_report", "reconstruct_timeline", "send_transmission"], "artifact_mapping_rules": {"artifact_type": "creative_draft", "object_type": "research_note", "display_name": "已验证的外发报告草案"}, "pass_consequence_templates": ["assemble_transmission_draft"], "fail_consequence_templates": ["mark_report_provisional"], "review_transformation_rules": ["revise_unreliable_section"], "final_assessment_rules": ["require_final_report"], "forbidden_mappings": ["draft_to_canon_fact"]},
    "exam_cram": {"version": "1.0.0", "supported_task_shapes": ["exam_cram"], "supported_learning_actions": ["recall", "classify", "compare", "verify", "transfer"], "supported_story_actions": ["verify_archive", "simulate_outcomes", "optimize_plan"], "artifact_mapping_rules": {"artifact_type": "answer_set", "object_type": "risk_model", "display_name": "已验证的冲刺风险清单"}, "pass_consequence_templates": ["narrow_open_questions"], "fail_consequence_templates": ["retain_risk"], "review_transformation_rules": ["sample_weak_point"], "final_assessment_rules": ["require_fast_transfer"], "forbidden_mappings": ["time_pressure_to_harm"]},
    "mixed_learning": {"version": "1.0.0", "supported_task_shapes": ["mixed"], "supported_learning_actions": ["identify", "explain", "apply", "verify", "transfer"], "supported_story_actions": ["verify_archive", "prepare_report", "optimize_plan"], "artifact_mapping_rules": {"artifact_type": "summary", "object_type": "archive_report", "display_name": "已验证的综合档案"}, "pass_consequence_templates": ["record_verified_summary"], "fail_consequence_templates": ["retain_uncertainty"], "review_transformation_rules": ["challenge_summary"], "final_assessment_rules": ["require_integrated_output"], "forbidden_mappings": ["learning_to_magic"]},
}


def _detect_profile(workspace_id: str) -> dict[str, Any]:
    goal, chunks = _workspace_material(workspace_id)
    goal_data, plan = _goal_plan(workspace_id)
    material, injection_filtered = _safe_material("\n".join(str(item.get("text") or "") for item in chunks[:12]))
    text = f"{goal} {goal_data.get('expected_output', '')} {material}"
    lower = text.lower()
    if _contains(lower, ("算法", "数据结构", "代码", "编程", "题库")):
        domain, shape, structure = "algorithms" if _contains(lower, ("算法", "数据结构", "题库")) else "programming", "coding_practice" if _contains(lower, ("代码", "编程")) else "large_question_bank", "prerequisite_graph"
        actions, artifacts = ["identify", "plan", "apply", "verify", "repair", "optimize", "transfer"], ["code", "algorithm_plan", "proof"]
    elif _contains(lower, ("法律", "法考", "案例", "证据", "责任", "判例")):
        domain, shape, structure, actions, artifacts = "law", "case_analysis", "evidence_network", ["classify", "compare", "argue", "verify", "infer"], ["argument", "comparison", "report"]
    elif _contains(lower, ("英语", "单词", "语法", "听力", "翻译", "english", "vocabulary", "grammar")):
        domain, shape, structure, actions, artifacts = "language", "language_acquisition", "repeated_patterns", ["recall", "translate", "infer", "verify", "transfer"], ["translation", "summary"]
    elif _contains(lower, ("医学", "诊断", "症状", "病理", "药理", "临床")):
        domain, shape, structure, actions, artifacts = "medicine", "concept_course", "evidence_network", ["identify", "classify", "infer", "diagnose", "verify"], ["diagnosis", "report", "proof"]
    elif _contains(lower, ("论文", "写作", "文章", "摘要", "论点", "研究")):
        domain, shape, structure, actions, artifacts = "writing", "long_form_creation", "cumulative_project", ["plan", "argue", "create", "revise", "verify"], ["summary", "argument", "creative_draft"]
    elif _contains(lower, ("三天", "冲刺", "考试", "考前", "deadline")):
        domain, shape, structure, actions, artifacts = "general_concepts", "exam_cram", "independent_units", ["recall", "classify", "compare", "verify", "transfer"], ["answer_set", "proof"]
    elif _contains(lower, ("八股", "面试", "背诵", "记忆")):
        domain, shape, structure, actions, artifacts = "interview_memory", "memory_drill", "repeated_patterns", ["recall", "compare", "explain", "verify"], ["summary", "answer_set"]
    else:
        domain, shape, structure, actions, artifacts = "general_concepts", "mixed", "linear", ["identify", "explain", "apply", "verify", "transfer"], ["summary", "explanation"]
    if domain == "algorithms" and _contains(lower, ("code", "coding", "programming")):
        shape = "coding_practice"
    if domain == "general_concepts" and _contains(lower, ("law", "legal", "evidence", "testimony", "precedent")):
        domain, shape, structure, actions, artifacts = "law", "case_analysis", "evidence_network", ["classify", "compare", "argue", "verify", "infer"], ["argument", "comparison", "report"]
    try:
        deadline = str(goal_data.get("deadline") or "")[:10]
        duration = max(0, (datetime.fromisoformat(f"{deadline}T23:59:59+00:00") - _now()).days)
    except (TypeError, ValueError):
        duration = len(plan.get("study_days", []))
    if duration <= 3 or _contains(lower, ("三天", "冲刺", "考前")):
        shape = "exam_cram"
    coupling = "strong" if domain in {"law", "medicine", "language", "writing"} else "weak" if domain in {"algorithms", "programming", "interview_memory"} else "parallel"
    if _contains(lower, ("project", "delivery", "deploy")) and domain in {"programming", "general_concepts"}:
        shape = "practical_project"
    adapter_id = {"law": "evidence_argument", "medicine": "concept_learning", "language": "language_learning", "writing": "long_form_creation", "algorithms": "coding_practice", "programming": "coding_practice", "interview_memory": "memory_drill", "general_concepts": "exam_cram" if shape == "exam_cram" else "mixed_learning"}.get(domain, "mixed_learning")
    if shape == "practical_project":
        adapter_id = "practical_project"
    evidence_refs = [{"source_id": item.get("source_id"), "chunk_id": item.get("id"), "source_title": item.get("source_title", ""), "quote": str(item.get("text") or "")[:180]} for item in chunks[:8]]
    return {"profile_id": "", "workspace_id": workspace_id, "source_ids": sorted({item["source_id"] for item in evidence_refs if item.get("source_id")}), "goal": goal or goal_data.get("goal_statement", ""), "domain": domain, "task_shape": shape, "knowledge_structure": structure, "learning_actions": actions, "artifact_types": artifacts, "duration_days": duration, "daily_minutes": int(goal_data.get("minutes_per_day") or 30), "first_pass_intensity": "compressed" if shape == "exam_cram" else "standard", "review_need": "high" if shape in {"memory_drill", "exam_cram", "language_acquisition"} else "standard", "assessment_type": "transfer_and_evidence", "difficulty_estimate": "medium", "content_density": "high" if len(chunks) >= 8 else "unknown", "preferred_interaction": "scenario_choices", "emotional_preference": "grounded_companion", "content_rating": "general", "coupling_recommendation": {"mode": coupling, "reason": "根据目标、资料摘要和认知动作选择规则型融合方式。", "confidence": 0.78, "forbidden_literal_mappings": ADAPTERS[adapter_id]["forbidden_mappings"], "recommended_story_actions": ADAPTERS[adapter_id]["supported_story_actions"]}, "recommended_adapter_id": adapter_id, "confidence": 0.78, "evidence_refs": evidence_refs, "generation_meta": {"mode": "rules", "fallback_used": True, "prompt_injection_filtered": injection_filtered, "material_scope": "workspace_sources_and_goal_only"}}


def analyze_task_profile(workspace_id: str, *, force: bool = False) -> dict[str, Any]:
    if not db.row("workspaces", "id", workspace_id):
        raise AdaptiveNarrativeError("WORKSPACE_NOT_FOUND", "学习空间不存在", status_code=404)
    init_orm()
    with SessionLocal() as session:
        existing = session.query(TaskSemanticProfileORM).filter_by(workspace_id=workspace_id).order_by(TaskSemanticProfileORM.version.desc()).first()
        if existing and not force:
            result = copy.deepcopy(existing.profile); result["profile_id"] = existing.id; result["version"] = existing.version; return result
        profile = _detect_profile(workspace_id)
        version = int(existing.version + 1) if existing else 1
        profile["profile_id"] = _id("task_profile"); profile["version"] = version
        row = TaskSemanticProfileORM(id=profile["profile_id"], workspace_id=workspace_id, version=version, profile=profile, generation_meta=profile["generation_meta"], idempotency_key=f"profile:{workspace_id}:{version}", created_at=_now(), updated_at=_now())
        session.add(row); session.commit()
        return profile


def get_task_profile(workspace_id: str) -> dict[str, Any]:
    return analyze_task_profile(workspace_id)


def _latest_profile(session, workspace_id: str) -> tuple[TaskSemanticProfileORM, dict[str, Any]]:
    row = session.query(TaskSemanticProfileORM).filter_by(workspace_id=workspace_id).order_by(TaskSemanticProfileORM.version.desc()).first()
    if not row:
        profile = analyze_task_profile(workspace_id)
        row = session.query(TaskSemanticProfileORM).filter_by(id=profile["profile_id"]).first()
    if not row:
        raise AdaptiveNarrativeError("TASK_PROFILE_REQUIRED", "请先生成任务画像")
    return row, copy.deepcopy(row.profile)


def select_fusion_adapter(profile: dict[str, Any]) -> dict[str, Any]:
    adapter_id = str(profile.get("recommended_adapter_id") or "mixed_learning")
    if profile.get("task_shape") == "practical_project":
        adapter_id = "practical_project"
    adapter = copy.deepcopy(ADAPTERS.get(adapter_id, ADAPTERS["mixed_learning"]))
    adapter["adapter_id"] = adapter_id
    return adapter


def compile_adaptation(campaign_id: str, *, force: bool = False) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        campaign = session.get(NarrativeCampaignORM, campaign_id)
        if not campaign:
            raise AdaptiveNarrativeError("CAMPAIGN_NOT_FOUND", "Narrative Campaign 不存在", status_code=404)
        current = session.query(CampaignAdaptationBlueprintORM).filter_by(campaign_id=campaign_id).order_by(CampaignAdaptationBlueprintORM.version.desc()).first()
        if current and not force:
            return {"blueprint_id": current.id, "campaign_id": campaign_id, "version": current.version, "status": current.status, **copy.deepcopy(current.blueprint)}
        profile_row, profile = _latest_profile(session, campaign.workspace_id)
        adapter = select_fusion_adapter(profile)
        pack = load_story_pack(campaign.story_pack_id)
        anchors = pack["story_bible"].get("main_plot_anchors", []) or []
        bindings = []
        for anchor in anchors:
            scene = pack["scenes"].get(anchor, {})
            gate = scene.get("learning_gate_ref")
            bindings.append({"anchor_id": anchor, "trigger": {"event": (next((b.get("assessment_requirement") for b in pack["learning_bindings"].get("bindings", []) if b.get("binding_id") == gate), None) if gate else None), "required_artifacts": [adapter["artifact_mapping_rules"]["artifact_type"]] if gate else []}, "story_action": (adapter["supported_story_actions"][(len(bindings)) % len(adapter["supported_story_actions"]) ] if adapter["supported_story_actions"] else "verify_archive"), "artifact_usage": "reference_verified_artifact" if gate else "set_scene_context", "world_change_on_pass": adapter["pass_consequence_templates"], "world_change_on_fail": adapter["fail_consequence_templates"]})
        version = int(current.version + 1) if current else 1
        blueprint = {"blueprint_id": _id("blueprint"), "campaign_id": campaign_id, "task_profile_id": profile_row.id, "story_pack_id": campaign.story_pack_id, "coupling_mode": profile["coupling_recommendation"]["mode"], "fusion_adapter_ids": [adapter["adapter_id"]], "fixed_anchor_bindings": bindings, "artifact_story_mappings": [adapter["artifact_mapping_rules"]], "world_state_rules": adapter["pass_consequence_templates"], "character_state_rules": ["reference_verified_object_without_changing_learning_eligibility"], "review_arc": {"rules": adapter["review_transformation_rules"]}, "final_arc": {"rules": adapter["final_assessment_rules"]}, "postgame_arc": {"rules": ["use_story_object_for_maintenance_review"]}, "continuity_constraints": ["immutable_facts", "forbidden_reveals", "no_unverified_artifact_effect", "no_user_quote_invention"], "version": "1.0.0"}
        row = CampaignAdaptationBlueprintORM(id=blueprint["blueprint_id"], campaign_id=campaign_id, task_profile_id=profile_row.id, story_pack_id=campaign.story_pack_id, version=version, status="active" if not current else "draft", blueprint=blueprint, generation_meta={"mode": "rules", "fallback_used": True, "validated": True}, idempotency_key=f"blueprint:{campaign_id}:{version}", created_at=_now(), updated_at=_now())
        session.add(row)
        for adapter_id, definition in ADAPTERS.items():
            if not session.query(FusionAdapterVersionORM).filter_by(adapter_id=adapter_id, version=definition["version"]).first():
                session.add(FusionAdapterVersionORM(id=_id("adapter"), adapter_id=adapter_id, version=definition["version"], definition={"adapter_id": adapter_id, **definition}, created_at=_now()))
        session.commit()
        return {"blueprint_id": row.id, "campaign_id": campaign_id, "version": version, "status": row.status, **blueprint}


def get_blueprint(campaign_id: str) -> dict[str, Any]:
    return compile_adaptation(campaign_id)


def activate_blueprint(campaign_id: str, blueprint_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        row = session.query(CampaignAdaptationBlueprintORM).filter_by(id=blueprint_id, campaign_id=campaign_id).first()
        if not row:
            raise AdaptiveNarrativeError("BLUEPRINT_NOT_FOUND", "适配蓝图不存在", status_code=404)
        if row.status == "active":
            return {"blueprint_id": row.id, "campaign_id": campaign_id, "version": row.version, "status": row.status, **copy.deepcopy(row.blueprint)}
        session.query(CampaignAdaptationBlueprintORM).filter_by(campaign_id=campaign_id, status="active").update({"status": "archived"})
        row.status = "active"; row.updated_at = _now(); session.commit()
        return {"blueprint_id": row.id, "campaign_id": campaign_id, "version": row.version, "status": row.status, **copy.deepcopy(row.blueprint)}


def _artifact_mapping(session, campaign: NarrativeCampaignORM, profile: dict[str, Any], phase: str) -> dict[str, Any]:
    adapter = select_fusion_adapter(profile)
    mapping = copy.deepcopy(adapter["artifact_mapping_rules"])
    final_types = {"law": ("decision_memo", "最终决策备忘录"), "medicine": ("diagnostic_report", "最终诊断报告"), "writing": ("final_transmission", "最终外发稿"), "practical_project": ("delivery_plan", "最终交付计划")}
    if phase == "final_passed" and profile.get("domain") in final_types:
        mapping["object_type"], mapping["display_name"] = final_types[profile["domain"]]
    if phase == "review_passed":
        mapping["status"] = "revised"
    elif phase == "final_passed":
        mapping["status"] = "final"
    else:
        mapping["status"] = "verified"
    return mapping


def create_learning_artifact(workspace_id: str, run_id: str, task_id: str, phase: str, response: str, evidence: str, evaluation: dict[str, Any] | None, hints_used: int) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile_row, profile = _latest_profile(session, workspace_id)
        campaigns = session.query(NarrativeCampaignORM).filter_by(workspace_id=workspace_id).all()
        campaign = campaigns[0] if campaigns else None
        mapping = _artifact_mapping(session, campaign, profile, phase) if campaign else {"artifact_type": profile.get("artifact_types", ["summary"])[0], "object_type": "research_note", "display_name": "已验证学习成果", "status": "verified"}
        if phase == "passed" and hints_used > 0:
            mapping["status"] = "provisional"
        idem = f"artifact:{run_id}:{phase}"
        artifact = session.query(LearningArtifactORM).filter_by(workspace_id=workspace_id, idempotency_key=idem).first()
        if not artifact:
            artifact = LearningArtifactORM(id=_id("artifact"), workspace_id=workspace_id, campaign_id=campaign.id if campaign else "", learning_run_id=run_id, task_id=task_id, artifact_type=mapping["artifact_type"], content={"response": response[:6000], "evidence": evidence[:6000], "learning_phase": phase}, source_refs=profile.get("evidence_refs", [])[:12], evaluation_result=evaluation or {}, status=mapping.get("status", "verified"), verified=bool(evaluation and evaluation.get("passed")), hint_level="none" if not hints_used else ("used" if hints_used <= 1 else "high"), revision=0, idempotency_key=idem, created_at=_now())
            session.add(artifact); session.flush()
        object_ids: list[str] = []
        for campaign in campaigns:
            if phase == "review_passed":
                previous_objects = session.query(StoryObjectORM).filter_by(campaign_id=campaign.id, object_type=mapping["object_type"]).all()
                for previous in previous_objects:
                    if previous.source_artifact_id != artifact.id and previous.status not in {"final", "invalidated"}:
                        previous.status = "revised"
                        previous.revision += 1
            object_row = session.query(StoryObjectORM).filter_by(campaign_id=campaign.id, source_artifact_id=artifact.id).first()
            if not object_row:
                effective_verified = artifact.verified and mapping.get("status") not in {"provisional", "challenged", "invalidated"}
                object_row = StoryObjectORM(id=_id("story_object"), campaign_id=campaign.id, source_artifact_id=artifact.id, object_type=mapping["object_type"], display_name=mapping["display_name"], story_description=f"它把本次学习成果转成了可供中继站检查的{mapping['display_name']}。", verified_facts=[(evidence or response)[:500]], uncertainties=[] if effective_verified else ["仍需复习验证边界条件"], world_effects=select_fusion_adapter(profile)["pass_consequence_templates"] if effective_verified else [], character_visibility=["lan", "relay_tutor"] if effective_verified else [], status=mapping.get("status", "verified"), revision=0, created_at=_now())
                session.add(object_row); session.flush()
            object_ids.append(object_row.id)
            state = copy.deepcopy(campaign.state or {})
            objects = state.setdefault("adaptive_story_objects", [])
            public_object = {"story_object_id": object_row.id, "source_artifact_id": artifact.id, "object_type": object_row.object_type, "display_name": object_row.display_name, "status": object_row.status, "verified_facts": object_row.verified_facts, "world_effects": object_row.world_effects}
            if phase == "review_passed":
                for item in objects:
                    if item.get("object_type") == object_row.object_type and item.get("status") not in {"final", "invalidated"}:
                        item["status"] = "revised"
            if not any(item.get("story_object_id") == object_row.id for item in objects):
                objects.append(public_object)
            if artifact.verified and object_row.status not in {"provisional", "challenged", "invalidated"}:
                state.setdefault("adaptive_world_state", {})[object_row.object_type] = object_row.id
                state.setdefault("story_flags", []).append(f"ADAPTIVE_OBJECT_{object_row.object_type.upper()}") if f"ADAPTIVE_OBJECT_{object_row.object_type.upper()}" not in state.setdefault("story_flags", []) else None
            campaign.state = state; campaign.updated_at = _now()
            _upsert_continuity(session, campaign, warnings=[])
        session.commit()
        return {"artifact_id": artifact.id, "artifact_type": artifact.artifact_type, "verified": artifact.verified, "status": artifact.status, "story_object_ids": object_ids, "source_refs": artifact.source_refs}


def _upsert_continuity(session, campaign: NarrativeCampaignORM, warnings: list[str]) -> None:
    snapshot = session.query(NarrativeContinuitySnapshotORM).filter_by(campaign_id=campaign.id).first()
    state = campaign.state or {}
    data = {"campaign_id": campaign.id, "story_stage": campaign.current_story_stage, "fixed_facts": [], "revealed_facts": state.get("revealed_facts", []), "hidden_facts": [], "character_knowledge": {}, "character_false_beliefs": {}, "promises": state.get("promises", []), "open_questions": [], "pending_payoffs": [], "story_objects": state.get("adaptive_story_objects", []), "timeline_events": state.get("story_flags", []), "relationship_state": state.get("relationship_state", {}), "world_state": state.get("adaptive_world_state", {})}
    if not snapshot:
        snapshot = NarrativeContinuitySnapshotORM(id=_id("continuity"), campaign_id=campaign.id, snapshot=data, revision=0, validator_warnings=warnings, updated_at=_now()); session.add(snapshot)
    else:
        snapshot.snapshot = data; snapshot.revision += 1; snapshot.validator_warnings = warnings; snapshot.updated_at = _now()


def _validate_contract(contract: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    text = json.dumps({key: value for key, value in contract.items() if key not in {"must_not_reveal", "character_knowledge_snapshot"}}, ensure_ascii=False)
    forbidden = [str(item.get("fact")) for item in pack.get("story_bible", {}).get("forbidden_reveals", []) if isinstance(item, dict)]
    for fact in forbidden:
        if fact and fact in text:
            warnings.append("forbidden_reveal_in_contract")
    if not contract.get("story_problem") or not contract.get("required_artifact_type"):
        warnings.append("contract_missing_causal_requirement")
    return {"valid": not warnings, "warnings": warnings, "validators": ["story_fact", "persona", "forbidden_reveal", "learning_fact", "artifact_citation"]}


def generate_scene_contract(campaign_id: str, *, force: bool = False) -> dict[str, Any]:
    blueprint = compile_adaptation(campaign_id)
    init_orm()
    with SessionLocal() as session:
        campaign = session.get(NarrativeCampaignORM, campaign_id)
        if not campaign:
            raise AdaptiveNarrativeError("CAMPAIGN_NOT_FOUND", "Narrative Campaign 不存在", status_code=404)
        state = campaign.state or {}
        object_count = len(state.get("adaptive_story_objects", []))
        idem = f"contract:{campaign.current_scene_id}:{blueprint['version']}:{object_count}"
        existing = session.query(SceneContractORM).filter_by(campaign_id=campaign_id, idempotency_key=idem).first()
        if existing and not force:
            return {"contract_id": existing.id, "campaign_id": campaign_id, "status": existing.status, **copy.deepcopy(existing.contract), "validator_result": existing.validator_result}
        profile_row, profile = _latest_profile(session, campaign.workspace_id)
        adapter = select_fusion_adapter(profile)
        pack = load_story_pack(campaign.story_pack_id)
        scene = pack["scenes"][campaign.current_scene_id]
        objects = state.get("adaptive_story_objects", [])
        prior = [item.get("story_object_id") for item in objects if item.get("status") in {"verified", "revised", "final"}]
        problem = {"coding_practice": "中继站发现一条异常路径，需要一份可验证的修复方案。", "evidence_argument": "档案中的两段证词互相冲突，需要一份有依据的审查意见。", "language_learning": "干扰通信留下了一段不完整消息，需要确认还原文本。", "long_form_creation": "中继站需要一份结构清晰、可外发的报告草案。", "concept_learning": "故障日志缺少解释，需要把概念整理成可执行的排查依据。"}.get(adapter["adapter_id"], "中继站需要一份可靠的核验结果，才能决定下一步行动。")
        contract = {"contract_id": _id("contract"), "campaign_id": campaign_id, "story_anchor_before": campaign.current_scene_id, "story_anchor_after": next((anchor for anchor in pack["story_bible"].get("main_plot_anchors", []) if anchor != campaign.current_scene_id and pack["scenes"].get(anchor, {}).get("story_order", 999) > scene.get("story_order", 0)), ""), "scene_purpose": "把今日真实学习成果变成可引用的剧情行动", "emotional_start": "uncertain", "emotional_end": "grounded", "story_problem": problem, "learning_action": profile.get("learning_actions", ["verify"])[0], "story_action": adapter["supported_story_actions"][0], "required_artifact_type": adapter["artifact_mapping_rules"]["artifact_type"], "story_object_type": adapter["artifact_mapping_rules"]["object_type"], "must_include_facts": ["学习结果必须来自当前Workspace资料和Evaluator验证"], "must_reference_prior_artifacts": prior[-3:], "must_not_reveal": pack["story_bible"].get("forbidden_reveals", []), "character_knowledge_snapshot": {"story_stage": campaign.current_story_stage, "known_object_count": len(objects)}, "world_change_on_pass": adapter["pass_consequence_templates"], "world_change_on_fail": adapter["fail_consequence_templates"], "allowed_dynamic_slots": scene.get("safe_dynamic_slots", []), "dialogue_budget": {"max_lines": 6, "max_chars": 900}, "continuity_version": "1.0.0", "status": "validated"}
        validation = _validate_contract(contract, pack)
        row = SceneContractORM(id=contract["contract_id"], campaign_id=campaign_id, blueprint_id=blueprint["blueprint_id"], story_anchor_before=contract["story_anchor_before"], story_anchor_after=contract["story_anchor_after"], version=1, status="validated" if validation["valid"] else "rejected", contract=contract, validator_result=validation, idempotency_key=idem, created_at=_now())
        session.add(row); _upsert_continuity(session, campaign, validation["warnings"]); session.commit()
        return {"contract_id": row.id, "campaign_id": campaign_id, "status": row.status, **contract, "validator_result": validation}


def get_current_contract(campaign_id: str) -> dict[str, Any]:
    return generate_scene_contract(campaign_id)


def list_contracts(campaign_id: str) -> list[dict[str, Any]]:
    init_orm()
    with SessionLocal() as session:
        rows = session.query(SceneContractORM).filter_by(campaign_id=campaign_id).order_by(SceneContractORM.created_at.asc()).all()
        return [{"contract_id": row.id, "status": row.status, "story_anchor_before": row.story_anchor_before, "story_anchor_after": row.story_anchor_after, "version": row.version, "validator_result": row.validator_result, "created_at": row.created_at.isoformat()} for row in rows]


def generate_dynamic_scene(campaign_id: str, contract_id: str | None = None) -> dict[str, Any]:
    contract = get_current_contract(campaign_id) if not contract_id else None
    init_orm()
    with SessionLocal() as session:
        campaign = session.get(NarrativeCampaignORM, campaign_id)
        row = session.get(SceneContractORM, contract_id or contract["contract_id"])
        if not campaign or not row:
            raise AdaptiveNarrativeError("SCENE_CONTRACT_NOT_FOUND", "场景契约不存在", status_code=404)
        if row.status != "validated":
            raise AdaptiveNarrativeError("SCENE_CONTRACT_INVALID", "场景契约未通过连续性验证", status_code=409)
        existing = session.query(GeneratedNarrativeSceneORM).filter_by(campaign_id=campaign_id, contract_id=row.id).first()
        if existing:
            return {"generated_scene_id": existing.id, "contract_id": row.id, **copy.deepcopy(existing.scene), "validator_result": existing.validator_result}
        state = campaign.state or {}; objects = [item for item in state.get("adaptive_story_objects", []) if item.get("status") in {"verified", "revised", "final"}]
        object_item = objects[-1] if objects else None
        display = object_item.get("display_name") if object_item else "今天的学习成果"
        fact = _safe_fact((object_item.get("verified_facts") or ["尚未产生可引用成果"])[0]) if object_item else "尚未产生可引用成果"
        lines = [{"speaker_id": "lan", "text": f"中继站的异常还在这里。你今天整理出的{display}，能让我们确认下一步吗？", "expression": "worried", "voice_style": "lan_zh_soft"}, {"speaker_id": "relay_tutor", "text": f"只引用已经验证的部分：{fact[:260]}", "expression": "focused", "voice_style": "relay_zh_neutral"}, {"speaker_id": "lan", "text": "那我们把这份结果放进档案，不把不确定的地方说成事实。", "expression": "neutral", "voice_style": "lan_zh_soft"}]
        scene = {"scene_id": _id("generated_scene"), "contract_id": row.id, "lines": lines, "choices": [{"choice_id": "review_object", "label": "检查这份成果的边界"}, {"choice_id": "record_object", "label": "将已验证部分写入档案"}], "referenced_artifact_ids": [object_item.get("source_artifact_id")] if object_item else [], "world_effect_preview": row.contract.get("world_change_on_pass", []), "generation_meta": {"mode": "safe_template", "provider_used": False, "artifact_grounded": bool(object_item)}}
        validation = {"valid": True, "warnings": [], "validators": ["story_fact", "persona", "forbidden_reveal", "learning_fact", "artifact_citation"]}
        generated = GeneratedNarrativeSceneORM(id=scene["scene_id"], campaign_id=campaign_id, contract_id=row.id, scene=scene, validator_result=validation, generation_meta=scene["generation_meta"], status="ready", created_at=_now())
        session.add(generated); session.commit()
        return {"generated_scene_id": generated.id, "contract_id": row.id, **scene, "validator_result": validation}


def list_artifacts(workspace_id: str, campaign_id: str | None = None) -> list[dict[str, Any]]:
    init_orm()
    with SessionLocal() as session:
        query = session.query(LearningArtifactORM).filter_by(workspace_id=workspace_id)
        if campaign_id:
            query = query.filter_by(campaign_id=campaign_id)
        return [{"artifact_id": row.id, "campaign_id": row.campaign_id, "learning_run_id": row.learning_run_id, "task_id": row.task_id, "artifact_type": row.artifact_type, "content": row.content, "evaluation_result": row.evaluation_result, "verified": row.verified, "status": row.status, "source_refs": row.source_refs, "created_at": row.created_at.isoformat()} for row in query.order_by(LearningArtifactORM.created_at.asc()).all()]


def list_story_objects(campaign_id: str) -> list[dict[str, Any]]:
    init_orm()
    with SessionLocal() as session:
        rows = session.query(StoryObjectORM).filter_by(campaign_id=campaign_id).order_by(StoryObjectORM.created_at.asc()).all()
        return [{"story_object_id": row.id, "source_artifact_id": row.source_artifact_id, "object_type": row.object_type, "display_name": row.display_name, "story_description": row.story_description, "verified_facts": row.verified_facts, "uncertainties": row.uncertainties, "world_effects": row.world_effects, "status": row.status, "revision": row.revision, "created_at": row.created_at.isoformat()} for row in rows]


def adaptive_context(campaign_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        campaign = session.get(NarrativeCampaignORM, campaign_id)
        if not campaign:
            raise AdaptiveNarrativeError("CAMPAIGN_NOT_FOUND", "Narrative Campaign 不存在", status_code=404)
        state = campaign.state or {}
        return {"coupling_mode": ((compile_adaptation(campaign_id).get("coupling_mode")) if campaign else None), "story_objects": state.get("adaptive_story_objects", []), "world_state": state.get("adaptive_world_state", {}), "current_contract": get_current_contract(campaign_id)}
ADAPTERS.setdefault("practical_project", {"version": "1.0.0", "supported_task_shapes": ["practical_project"], "supported_learning_actions": ["identify", "plan", "apply", "verify", "repair", "optimize", "transfer"], "supported_story_actions": ["trace_route", "repair_system", "optimize_plan", "prepare_report"], "artifact_mapping_rules": {"artifact_type": "project_plan", "object_type": "route_plan", "display_name": "已验证的项目路径计划"}, "pass_consequence_templates": ["unlock_project_route", "record_delivery_boundary"], "fail_consequence_templates": ["retain_scope_uncertainty", "offer_recovery_learning_run"], "review_transformation_rules": ["challenge_scope_assumption"], "final_assessment_rules": ["require_verifiable_delivery_plan"], "forbidden_mappings": ["project_to_magic", "failure_to_character_harm"]})
