from __future__ import annotations

from app import catalog_repository


def _workspace(client, title: str, goal: str, material: str):
    workspace = client.post("/api/workspaces", json={"title": title}).json()
    assert client.post(f"/api/workspaces/{workspace['id']}/sources/text", json={"title": "matching material", "text": material}).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/goal", json={"goal_statement": goal, "motivation": "catalog matching", "deadline": "2030-08-30", "minutes_per_day": 45, "days_per_week": 5, "preferred_weekdays": [1, 2, 3, 4, 5], "current_level": "beginner", "expected_output": "verifiable result", "unavailable_dates": [], "success_criteria": ["explain and apply"]}).status_code == 200
    return workspace


def test_match_keeps_required_candidates_and_returns_three_for_fake_mode(client):
    workspace = _workspace(client, "Algorithm catalog workspace", "LeetCode graph traversal and code repair", "LeetCode binary trees, graph traversal, code complexity and verification")
    response = client.post("/api/story-catalog/match", json={"workspace_id": workspace["id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert len(body["results"]) == 3
    assert sum(item["playable"] for item in body["results"]) == 1
    assert sum(item["pack_status"] == "PACK_REQUIRED" for item in body["results"]) == 2
    required = {"catalog_id", "title", "record_tier", "match_score", "match_reasons", "task_shape_fit", "duration_fit", "coupling_fit", "narrative_structure", "tags", "pack_status", "story_pack_id", "playable", "curation_status"}
    assert required.issubset(body["results"][0])
    assert body["retrieval_budget"]["scanned_records"] == 10000


def test_different_profiles_change_candidates_and_reasons(client):
    algorithms = _workspace(client, "Algorithm profile", "LeetCode graph traversal and code repair", "LeetCode binary trees, graph traversal, code complexity and verification")
    law = _workspace(client, "Law profile", "Law exam case analysis and testimony evidence", "case analysis, evidence, testimony conflict, precedent and legal argument")
    algorithm_match = client.post("/api/story-catalog/match", json={"workspace_id": algorithms["id"]}).json()
    law_match = client.post("/api/story-catalog/match", json={"workspace_id": law["id"]}).json()
    assert algorithm_match["task_profile"]["task_shape"] != law_match["task_profile"]["task_shape"]
    assert [item["catalog_id"] for item in algorithm_match["results"]] != [item["catalog_id"] for item in law_match["results"]]
    assert algorithm_match["results"][1]["match_reasons"] != law_match["results"][1]["match_reasons"]


def test_three_candidates_are_narratively_diverse_and_rematch_reads_workspace(client):
    workspace = _workspace(client, "Rematch workspace", "LeetCode coding practice", "LeetCode algorithm coding practice")
    first = client.post("/api/story-catalog/match", json={"workspace_id": workspace["id"]}).json()
    first_ids = [item["catalog_id"] for item in first["results"]]
    dimensions = ["narrative_structure", "relationship_engine", "interaction_style", "emotional_tone", "time_mechanic"]
    assert sum(len({item[field] for item in first["results"]}) > 1 for field in dimensions) >= 2
    law_workspace = _workspace(client, "Law rematch workspace", "Law case analysis and evidence argument", "law evidence testimony precedent argument")
    second = client.post("/api/story-catalog/match", json={"workspace_id": law_workspace["id"]}).json()
    assert second["task_profile"]["domain"] == "law"
    assert [item["catalog_id"] for item in second["results"]] != first_ids


def test_direct_fake_profile_matching_does_not_depend_on_provider():
    result = catalog_repository.match({"task_profile": {"domain": "language", "task_shape": "language_acquisition", "duration_days": 14, "coupling_recommendation": {"mode": "strong"}, "goal": "English vocabulary translation"}})
    assert result["count"] == 3
    assert len(result["results"]) == 3
