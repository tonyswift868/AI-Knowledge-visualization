from __future__ import annotations

from datetime import date

from app.persona_renderer import render_verified_content
from app.story_pack import StoryPackError, load_story_pack, validate_story_pack


def make_workspace(client):
    response = client.post("/api/workspaces", json={"title": "Narrative Runtime Test", "description": "fixture"})
    assert response.status_code == 200
    return response.json()


def test_dev_story_pack_is_versioned_and_valid():
    pack = load_story_pack("dev_fixture_minimal")
    report = validate_story_pack(pack, pack_root := __import__("pathlib").Path("story_packs/dev_fixture_minimal"))
    assert report["valid"] is True
    assert report["scene_count"] == 5
    assert pack["manifest"]["entry_scene_id"] == "dev_intro"


def test_story_pack_rejects_invalid_references(tmp_path):
    pack = {
        "story_pack_id": "fixture",
        "manifest": {"story_pack_id": "fixture", "version": "1", "title": "x", "source_work": "x", "source_version": "1", "language": "zh-CN", "minimum_runtime_version": "1", "supported_duration_days": [1], "content_rating": "dev", "entry_scene_id": "missing", "ending_scene_ids": [], "checksum": "", "created_at": "now", "provenance_notes": []},
        "characters": {"a": {"character_id": "a", "can_teach": False}},
        "scenes": {},
        "learning_bindings": {},
        "asset_manifest": {},
    }
    try:
        validate_story_pack(pack)
    except StoryPackError as exc:
        assert exc.code == "STORY_PACK_SCHEMA_INVALID"
    else:
        raise AssertionError("invalid pack must be rejected")


def test_story_pack_rejects_asset_path_traversal_and_future_character_knowledge():
    from pathlib import Path
    from copy import deepcopy

    pack = load_story_pack("dev_fixture_minimal")
    unsafe = deepcopy(pack)
    unsafe["asset_manifest"] = {"assets": [{"asset_id": "bad", "kind": "background", "path": "../../data/mentorflow_v2.db", "status": "ready"}]}
    try:
        validate_story_pack(unsafe, Path("story_packs/dev_fixture_minimal"))
    except StoryPackError as exc:
        assert exc.code == "STORY_PACK_PATH_UNSAFE"
    else:
        raise AssertionError("asset path traversal must be rejected")

    reference = load_story_pack("reference_echo_before_rain")
    public = __import__("app.story_pack", fromlist=["public_character"]).public_character(reference["characters"]["lan"], "intro")
    assert "truth" not in public["knowledge_by_story_stage"]


def test_campaign_gate_idempotency_revision_and_story_progress(client):
    workspace = make_workspace(client)
    packs = client.get("/api/story-packs")
    assert packs.status_code == 200 and packs.json()["story_packs"][0]["validation"]["valid"] is True
    created = client.post(f"/api/workspaces/{workspace['id']}/campaigns", json={"story_pack_id": "dev_fixture_minimal"})
    assert created.status_code == 200, created.text
    campaign = created.json()
    assert campaign["current_scene_id"] == "dev_intro"
    assert campaign["revision"] == 0
    line = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "continue", "expected_revision": 0, "idempotency_key": "dev-line-0"})
    assert line.status_code == 200 and line.json()["current_scene_id"] == "dev_intro"
    chosen = client.post(f"/api/campaigns/{campaign['campaign_id']}/choices", json={"choice_id": "inspect", "expected_revision": line.json()["revision"]})
    assert chosen.status_code == 200 and chosen.json()["current_scene_id"] == "dev_learning"
    campaign = chosen.json()
    learning_line = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "continue", "expected_revision": campaign["revision"], "idempotency_key": "dev-learning-line-0"})
    assert learning_line.status_code == 200 and learning_line.json()["current_scene_id"] == "dev_learning"
    campaign = learning_line.json()
    blocked = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "continue", "expected_revision": campaign["revision"]})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "LEARNING_GATE_BLOCKED"
    untrusted = client.post(f"/api/campaigns/{campaign['campaign_id']}/learning-events", json={"event_type": "INDEPENDENT_PROBLEM_PASSED", "source_type": "player", "source_id": "fake", "idempotency_key": "fake-pass"})
    assert untrusted.status_code in {403, 404}
    forged = client.post(f"/api/campaigns/{campaign['campaign_id']}/learning-events", json={"event_type": "INDEPENDENT_PROBLEM_PASSED", "source_type": "evaluator", "source_id": "lesson-1", "idempotency_key": "eval-1", "payload": {"score": 0.9}})
    assert forged.status_code in {403, 404}
    from app.narrative_runtime import publish_learning_event
    internal = publish_learning_event(workspace["id"], "INDEPENDENT_PROBLEM_PASSED", "learning_run", "lesson-1", {"learning_phase": "independent_completion", "mastery_status": "verified", "review_status": "any", "independent_passes": 1, "assessment_passed": True})
    assert internal and internal[0]["campaign"]["state"]["pending_learning_gate"]["status"] == "passed"
    passed = internal[0]
    advanced = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "continue", "expected_revision": passed["campaign"]["revision"]})
    assert advanced.status_code == 200 and advanced.json()["current_scene_id"] == "dev_choice"
    choice_line = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "continue", "expected_revision": advanced.json()["revision"], "idempotency_key": "dev-choice-line-0"})
    assert choice_line.status_code == 200 and choice_line.json()["current_scene_id"] == "dev_choice"
    chosen_again = client.post(f"/api/campaigns/{campaign['campaign_id']}/choices", json={"choice_id": "submit", "expected_revision": choice_line.json()["revision"]})
    assert chosen_again.status_code == 200 and chosen_again.json()["current_scene_id"] == "dev_assessment"
    assessment = publish_learning_event(workspace["id"], "DAILY_VARIANT_TEST_PASSED", "learning_run", "variant-1", {"learning_phase": "daily_variant_test", "mastery_status": "verified", "review_status": "any", "independent_passes": 1, "assessment_passed": True})
    assert assessment
    assessment_line = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "continue", "expected_revision": assessment[0]["campaign"]["revision"], "idempotency_key": "dev-assessment-line-0"})
    assert assessment_line.status_code == 200 and assessment_line.json()["current_scene_id"] == "dev_assessment"
    postgame = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "assessment_passed", "expected_revision": assessment_line.json()["revision"]})
    assert postgame.status_code == 200 and postgame.json()["current_scene_id"] == "dev_postgame"
    history = client.get(f"/api/campaigns/{campaign['campaign_id']}/history")
    assert history.status_code == 200 and len(history.json()["events"]) >= 6
    conflict = client.post(f"/api/campaigns/{campaign['campaign_id']}/advance", json={"action": "continue", "expected_revision": 0})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "CAMPAIGN_REVISION_CONFLICT"


def test_dialogue_routes_and_voice_fallback(client):
    workspace = make_workspace(client)
    campaign = client.post(f"/api/workspaces/{workspace['id']}/campaigns", json={}).json()
    story = client.post(f"/api/campaigns/{campaign['campaign_id']}/messages", json={"message": "我先看看这里"})
    assert story.status_code == 200 and story.json()["route"] == "story" and story.json()["public_facts_only"] is True
    planning = client.post(f"/api/campaigns/{campaign['campaign_id']}/messages", json={"message": "今天没时间，怎么调整计划"})
    assert planning.status_code == 200 and planning.json()["route"] == "planning"
    assert planning.json()["planning_preview"]["requires_confirmation"] is True
    assert planning.json()["next_action"]["type"] == "plan_replan_preview"
    voice = client.post(f"/api/campaigns/{campaign['campaign_id']}/voice/synthesize", json={"text": "fixture line", "voice_profile_id": "dev_voice_none"})
    assert voice.status_code == 200 and voice.json()["fallback"] == "text"


def test_campaign_locks_story_pack_version_and_checksum(client):
    from app.orm import NarrativeCampaignORM, SessionLocal
    workspace = make_workspace(client)
    campaign = client.post(f"/api/workspaces/{workspace['id']}/campaigns", json={}).json()
    with SessionLocal() as session:
        record = session.get(NarrativeCampaignORM, campaign["campaign_id"])
        record.story_pack_version = "0.0.0"
        session.commit()
    response = client.get(f"/api/campaigns/{campaign['campaign_id']}")
    assert response.status_code == 409 and response.json()["error"]["code"] == "STORY_PACK_VERSION_CONFLICT"


def test_persona_renderer_falls_back_on_semantic_change():
    result = render_verified_content("事实 A；引用 [1]。", "guide_a", "learning", candidate_text="事实 B；引用 [1]。")
    assert result["semantic_preservation_result"] == "failed_fallback"
    assert result["rendered_text"] == "事实 A；引用 [1]。"
