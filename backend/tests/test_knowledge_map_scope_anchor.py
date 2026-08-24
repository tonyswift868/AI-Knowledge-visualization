from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app import active_teaching, db, credential_store, orm
from app import generic_knowledge_api as generic_api
from app.main import app
from app.provider_runtime import create_provider_session, get_provider_session
from app.knowledge_map_scope import (
    ScopeValidationError,
    canonicalize_graph_scope,
    normalize_scope,
)


@pytest.fixture()
def migrated_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_dir = tmp_path / "mentorflow"
    database_dir.mkdir()
    monkeypatch.setattr(db, "DB_PATH", database_dir / "legacy.db")
    monkeypatch.setattr(db, "DATA_DIR", database_dir)
    monkeypatch.setattr(active_teaching, "UPLOAD_ROOT", database_dir / "uploads")
    db.init_db()
    orm.configure_orm(database_dir / "mentorflow_v2.db")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{(database_dir / 'mentorflow_v2.db').as_posix()}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    orm.configure_orm(database_dir / "mentorflow_v2.db")
    yield
    orm.engine.dispose()


def _receipt(material_ids: list[str], *, anchor: str | None = None, support: list[str] | None = None) -> dict:
    return {
        "learning_goal": "掌握资料中的概念",
        "current_level": "beginner",
        "target_level": "complete_mastery",
        "start_date": date.today().isoformat(),
        "deadline": (date.today() + timedelta(days=7)).isoformat(),
        "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "preferred_learning_style": "explain-and-practice",
        "material_ids": material_ids,
        **({"anchor_material_id": anchor} if anchor else {}),
        **({"supporting_material_ids": support} if support is not None else {}),
    }


def test_scope_normalizes_legacy_and_rejects_conflicts():
    normalized = normalize_scope({"material_ids": ["anchor", "support"]})
    assert normalized["anchor_material_id"] == "anchor"
    assert normalized["supporting_material_ids"] == ["support"]
    assert normalized["material_ids"] == ["anchor", "support"]
    assert normalized["role_provenance"]["anchor"]["selected_by"] == "legacy_default"
    with pytest.raises(ScopeValidationError):
        normalize_scope(
            {
                "material_ids": ["anchor", "support"],
                "anchor_material_id": "support",
                "supporting_material_ids": ["anchor"],
            }
        )
    with pytest.raises(ScopeValidationError):
        normalize_scope(
            {
                "anchor_material_id": "anchor",
                "supporting_material_ids": ["anchor"],
            }
        )


def test_scope_fingerprint_is_support_order_independent_but_role_sensitive():
    chunks = [
        {"material_id": "a", "id": "ca", "content_hash": "ha", "ordinal": 0, "char_start": 0, "char_end": 2},
        {"material_id": "b", "id": "cb", "content_hash": "hb", "ordinal": 0, "char_start": 0, "char_end": 2},
    ]
    first = {"anchor_material_id": "a", "supporting_material_ids": ["b"], "source_fingerprints": {}}
    reordered = {"anchor_material_id": "a", "supporting_material_ids": ["b"], "source_fingerprints": {}}
    swapped = {"anchor_material_id": "b", "supporting_material_ids": ["a"], "source_fingerprints": {}}
    assert generic_api._v1_source_fingerprint(chunks, first) == generic_api._v1_source_fingerprint(chunks, reordered)
    assert generic_api._v1_source_fingerprint(chunks, first) != generic_api._v1_source_fingerprint(chunks, swapped)


def test_scope_canonicalization_unions_evidence_and_excludes_support_only():
    scope = normalize_scope({"anchor_material_id": "a", "supporting_material_ids": ["b"], "selected_by": "user"})
    result = canonicalize_graph_scope(
        [
            {"id": "anchor", "name": "Shared", "node_type": "concept", "semantic_key": "shared", "explanation": "The anchor is true", "source_evidence": [{"material_id": "a", "chunk_id": "ca"}]},
            {"id": "support", "name": "Shared", "node_type": "concept", "semantic_key": "shared", "explanation": "The anchor is true", "source_evidence": [{"material_id": "b", "chunk_id": "cb"}]},
            {"id": "novel", "name": "Novel", "node_type": "concept", "semantic_key": "novel", "source_evidence": [{"material_id": "b", "chunk_id": "cx"}]},
        ],
        [{"id": "edge", "source_id": "support", "target_id": "novel", "relation": "prerequisite"}],
        scope,
    )
    assert [item["id"] for item in result["nodes"]] == ["anchor"]
    assert {item["material_id"] for item in result["nodes"][0]["source_evidence"]} == {"a", "b"}
    assert any(item["candidate_type"] == "scope_conflict" for item in result["scope_candidates"])


def test_scope_canonicalization_support_before_anchor_is_order_independent():
    scope = normalize_scope({"anchor_material_id": "a", "supporting_material_ids": ["b"], "selected_by": "user"})
    result = canonicalize_graph_scope(
        [
            {"id": "support", "name": "Shared", "node_type": "concept", "semantic_key": "shared", "explanation": "same", "source_evidence": [{"material_id": "b", "chunk_id": "cb"}]},
            {"id": "anchor", "name": "Shared", "node_type": "concept", "semantic_key": "shared", "explanation": "same", "source_evidence": [{"material_id": "a", "chunk_id": "ca"}]},
        ],
        [],
        scope,
    )
    assert [item["id"] for item in result["nodes"]] == ["anchor"]
    assert {item["material_id"] for item in result["nodes"][0]["source_evidence"]} == {"a", "b"}


def test_second_revision_is_staged_and_explicit_activation_preserves_last_good(migrated_scope, monkeypatch):
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "scope-contract-model",
            "credential_ref": credential_store.store_secret("test-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True

    class Provider:
        async def json(self, messages):
            prompt = json.loads(messages[-1]["content"])
            chunks = prompt["chunks"]
            anchor = next(item for item in chunks if item["material_role"] == "anchor")
            support = next(item for item in chunks if item["material_role"] == "support")
            return {
                "knowledge_points": [
                    {"id": "theme", "name": "Theme", "node_type": "theme", "source_chunk_ids": [anchor["chunk_id"], support["chunk_id"]]},
                    {"id": "shared", "name": "矩阵乘法", "semantic_key": "matrix-multiplication", "explanation": "same", "source_chunk_ids": [anchor["chunk_id"]]},
                    {"id": "shared-support", "name": "两个矩阵做乘法", "semantic_key": "matrix-multiplication", "explanation": "same", "source_chunk_ids": [support["chunk_id"]]},
                ],
                "dependencies": [{"prerequisite_id": "theme", "dependent_id": "shared", "relation": "contains", "source_chunk_ids": [anchor["chunk_id"]]}],
            }

    monkeypatch.setattr(generic_api, "provider_for_session", lambda _session: Provider())
    with TestClient(app) as client:
        materials = []
        for name, content in (("anchor.md", b"# Anchor\nAnchor evidence."), ("support.md", b"# Support\nSupport evidence.")):
            uploaded = client.post(
                "/api/active-teaching/workspaces/scope/materials",
                files={"file": (name, content, "text/markdown")},
            )
            assert uploaded.status_code == 200, uploaded.text
            materials.append(uploaded.json()["id"])
        receipt = _receipt(materials, anchor=materials[0], support=[materials[1]])
        saved = client.put("/api/active-teaching/workspaces/scope/receipt", json=receipt)
        assert saved.status_code == 200, saved.text
        first = client.post(
            "/api/active-teaching/workspaces/scope/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert first.status_code == 202, first.text
        first_job = client.get(f"/api/active-teaching/workspaces/scope/knowledge-map/jobs/{first.json()['id']}").json()
        assert first_job["activation_required"] is False
        current = client.get("/api/active-teaching/workspaces/scope/knowledge-map", params={"revision": "active"}).json()
        assert current["scope"]["anchor_material_id"] == materials[0]
        concepts = [item for item in current["nodes"] if item["node_type"] == "concept"]
        assert len(concepts) == 1
        assert {item["material_id"] for item in concepts[0]["evidence"]} == set(materials)
        preview = client.post("/api/active-teaching/workspaces/scope/mastery-plans/preview", json={})
        assert preview.status_code == 200, preview.text
        plan_activation = client.post(
            f"/api/active-teaching/workspaces/scope/mastery-plans/{preview.json()['preview_id']}/activate",
            json={},
        )
        assert plan_activation.status_code == 200, plan_activation.text
        classroom = client.post("/api/active-teaching/workspaces/scope/sessions/start", json={})
        assert classroom.status_code == 200, classroom.text
        old_session = classroom.json()

        swapped = _receipt([materials[1], materials[0]], anchor=materials[1], support=[materials[0]])
        assert client.put("/api/active-teaching/workspaces/scope/receipt", json=swapped).status_code == 200
        second = client.post(
            "/api/active-teaching/workspaces/scope/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert second.status_code == 202, second.text
        second_job = client.get(f"/api/active-teaching/workspaces/scope/knowledge-map/jobs/{second.json()['id']}").json()
        assert second_job["activation_required"] is True
        assert second_job["current_revision_id"] == current["revision_id"]
        staged_graph = client.get(
            "/api/active-teaching/workspaces/scope/knowledge-map",
            params={"revision": second_job["revision_id"]},
        )
        assert staged_graph.status_code == 200, staged_graph.text
        assert staged_graph.json()["activation_required"] is True
        assert client.get("/api/active-teaching/workspaces/scope/knowledge-map", params={"revision": "active"}).json()["revision_id"] == current["revision_id"]
        with active_teaching._connect() as connection:
            original_chunk = connection.execute(
                "SELECT id,text,content_hash FROM active_chunks WHERE material_id=? ORDER BY ordinal LIMIT 1",
                (materials[0],),
            ).fetchone()
            assert original_chunk is not None
            connection.execute(
                "UPDATE active_chunks SET text=text || ?,content_hash=? WHERE id=?",
                (" changed", "scope-source-changed", original_chunk["id"]),
            )
            connection.commit()
        stale_activation = client.post(
            f"/api/active-teaching/workspaces/scope/knowledge-map/revisions/{second_job['revision_id']}/activate",
            json={"expected_current_revision_id": current["revision_id"]},
        )
        assert stale_activation.status_code == 409, stale_activation.text
        assert stale_activation.json()["error"]["code"] == "KNOWLEDGE_SOURCE_CHANGED"
        with active_teaching._connect() as connection:
            connection.execute(
                "UPDATE active_chunks SET text=?,content_hash=? WHERE id=?",
                (original_chunk["text"], original_chunk["content_hash"], original_chunk["id"]),
            )
            connection.commit()
        activated = client.post(
            f"/api/active-teaching/workspaces/scope/knowledge-map/revisions/{second_job['revision_id']}/activate",
            json={"expected_current_revision_id": current["revision_id"]},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["activation_required"] is False
        old_session_after = client.get(f"/api/active-teaching/sessions/{old_session['session_id']}")
        assert old_session_after.status_code == 200, old_session_after.text
        assert old_session_after.json()["map_revision_id"] == current["revision_id"]
        current_plan = client.get("/api/active-teaching/workspaces/scope/mastery-plans/current")
        assert current_plan.status_code == 200, current_plan.text
        assert current_plan.json()["status"] == "stale"
        repeat = client.post(
            f"/api/active-teaching/workspaces/scope/knowledge-map/revisions/{second_job['revision_id']}/activate",
            json={"expected_current_revision_id": second_job["revision_id"]},
        )
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["activation"]["no_op"] is True


def test_staged_scope_candidate_decision_creates_staged_child_without_switching_current(migrated_scope):
    from app.orm import KnowledgeEdgeORM, KnowledgeMapRevisionORM, KnowledgeNodeORM, ReviewCandidateORM, SessionLocal

    with TestClient(app) as client:
        uploaded = client.post(
            "/api/active-teaching/workspaces/candidate/materials",
            files={"file": ("anchor.md", b"# Candidate\nGrounded evidence.", "text/markdown")},
        )
        assert uploaded.status_code == 200, uploaded.text
        anchor_material_id = uploaded.json()["id"]

    with SessionLocal() as session:
        current = KnowledgeMapRevisionORM(
            id="current-map",
            map_id="knowledge-map:candidate",
            workspace_id="candidate",
            revision=1,
            is_current=True,
            status="active",
            generation_mode="provider_grounded",
            provider_used=True,
            fingerprint="current",
        )
        staged = KnowledgeMapRevisionORM(
            id="staged-map",
            map_id="knowledge-map:candidate",
            workspace_id="candidate",
            revision=2,
            is_current=False,
            status="ready_with_review",
            generation_mode="provider_grounded",
            provider_used=True,
            anchor_material_id=anchor_material_id,
            supporting_material_ids=[],
            scope_policy={"mode": "single_anchor"},
            role_provenance={anchor_material_id: {"role": "anchor", "selected_by": "user"}},
            fingerprint="",
        )
        session.add_all([current, staged])
        session.add_all(
            [
                KnowledgeNodeORM(id="current-map:theme", map_revision_id="current-map", node_id="current-theme", source_id=anchor_material_id, semantic_key="theme:current", label="Current theme", summary="Current theme", kind="theme", revision=1, status="confirmed", content_hash="current-h", history={"node_type": "theme", "layer": "material", "mastery_estimate": {"understand_minutes": 1, "recall_minutes": 1, "guided_practice_minutes": 1, "independent_application_minutes": 1, "review_minutes": 1}}, source_refs=[{"material_id": anchor_material_id}]),
                KnowledgeNodeORM(id="current-map:concept", map_revision_id="current-map", node_id="current-concept", source_id=anchor_material_id, semantic_key="concept:current", label="Current concept", summary="Current concept", kind="concept", revision=1, status="confirmed", content_hash="current-h", history={"node_type": "concept", "layer": "material", "mastery_estimate": {"understand_minutes": 1, "recall_minutes": 1, "guided_practice_minutes": 1, "independent_application_minutes": 1, "review_minutes": 1}}, source_refs=[{"material_id": anchor_material_id}]),
                KnowledgeNodeORM(id="staged-map:theme", map_revision_id="staged-map", node_id="theme", source_id="anchor", semantic_key="theme:theme", label="Theme", summary="Theme", kind="theme", revision=2, status="confirmed", content_hash="h", history={"node_type": "theme", "layer": "material"}, source_refs=[{"material_id": "anchor"}]),
                KnowledgeNodeORM(id="staged-map:concept", map_revision_id="staged-map", node_id="concept", source_id="anchor", semantic_key="concept:concept", label="Concept", summary="Concept", kind="concept", revision=2, status="confirmed", content_hash="h", history={"node_type": "concept", "layer": "material"}, source_refs=[{"material_id": "anchor"}]),
            ]
        )
        session.add(
            KnowledgeEdgeORM(id="current-map:contains", map_revision_id="current-map", edge_id="current-contains", source_node_id="current-theme", target_node_id="current-concept", relation="contains", source_refs=[{"relation": "contains", "status": "confirmed", "layer": "material"}, {"material_id": anchor_material_id}])
        )
        session.add(
            KnowledgeEdgeORM(id="staged-map:contains", map_revision_id="staged-map", edge_id="contains", source_node_id="theme", target_node_id="concept", relation="contains", source_refs=[{"relation": "contains", "status": "confirmed", "layer": "material"}, {"material_id": "anchor"}])
        )
        candidate = ReviewCandidateORM(
            id="scope-candidate",
            workspace_id="candidate",
            map_revision_id="staged-map",
            candidate_key="scope:novel",
            candidate_type="scope_conflict",
            node_id="novel",
            status="pending",
            payload={"reason": "support_only_novel_concept", "node_id": "novel"},
            evidence=[{"material_id": anchor_material_id}],
        )
        current_candidate = ReviewCandidateORM(
            id="current-scope-candidate",
            workspace_id="candidate",
            map_revision_id="current-map",
            candidate_key="scope:current-novel",
            candidate_type="contradiction",
            node_id="current-novel",
            status="pending",
            payload={"reason": "anchor_support_contradiction", "node_id": "current-novel"},
            evidence=[{"material_id": anchor_material_id}],
        )
        session.add_all([candidate, current_candidate])
        session.commit()

    with active_teaching._connect() as connection:
        chunks = generic_api._evidence_chunks(connection, "candidate", [anchor_material_id])
    with SessionLocal() as session:
        staged = session.get(KnowledgeMapRevisionORM, "staged-map")
        assert staged is not None
        staged.fingerprint = generic_api._v1_source_fingerprint(
            chunks,
            {
                "anchor_material_id": anchor_material_id,
                "supporting_material_ids": [],
                "scope_policy": {"mode": "single_anchor"},
                "role_provenance": {anchor_material_id: {"role": "anchor", "selected_by": "user"}},
            },
        )
        session.commit()

    with TestClient(app) as client:
        saved_receipt = client.put(
            "/api/active-teaching/workspaces/candidate/receipt",
            json=_receipt([anchor_material_id], anchor=anchor_material_id, support=[]),
        )
        assert saved_receipt.status_code == 200, saved_receipt.text
        preview = client.post(
            "/api/active-teaching/workspaces/candidate/mastery-plans/preview",
            json={},
        )
        assert preview.status_code == 200, preview.text
        assert any(
            item.get("candidate_type") == "contradiction"
            for item in preview.json().get("activation_blockers") or []
        )
        blocked_activation = client.post(
            f"/api/active-teaching/workspaces/candidate/mastery-plans/{preview.json()['preview_id']}/activate",
            json={},
        )
        assert blocked_activation.status_code == 409, blocked_activation.text
        assert blocked_activation.json()["error"]["code"] == "MATERIAL_REVIEW_REQUIRED"
        decision = client.post(
            "/api/active-teaching/workspaces/candidate/knowledge-map/candidates/scope-candidate/decision",
            json={"decision": "confirmed", "reason": "reviewed"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["map_status"] == "staged"
        with SessionLocal() as session:
            parent = session.get(KnowledgeMapRevisionORM, "staged-map")
            child = session.get(KnowledgeMapRevisionORM, decision.json()["revision_id"])
            assert parent is not None and parent.status == "ready_with_review" and parent.is_current is False
            assert child is not None and child.status == "staged" and child.is_current is False
            assert session.get(KnowledgeMapRevisionORM, "current-map").is_current is True
        stale = client.post(
            "/api/active-teaching/workspaces/candidate/knowledge-map/candidates/scope-candidate/decision",
            json={"decision": "rejected"},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "KNOWLEDGE_MAP_CANDIDATE_STALE"
        activated = client.post(
            f"/api/active-teaching/workspaces/candidate/knowledge-map/revisions/{decision.json()['revision_id']}/activate",
            json={"expected_current_revision_id": "current-map"},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["revision_id"] == decision.json()["revision_id"]
