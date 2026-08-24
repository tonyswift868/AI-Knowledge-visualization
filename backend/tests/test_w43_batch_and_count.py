from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterable

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import generic_knowledge_api as generic_api
from app.main import app
from app.orm import (
    KnowledgeEdgeORM,
    KnowledgeMapRevisionORM,
    KnowledgeNodeORM,
    ReviewCandidateORM,
    SessionLocal,
)


WORKSPACE_ID = "w43-batch"
REVISION_ID = "w43-batch-revision"


def _seed_batch_revision() -> list[str]:
    """Seed a small valid material graph and three pending edge candidates."""

    with SessionLocal() as session:
        session.add(
            KnowledgeMapRevisionORM(
                id=REVISION_ID,
                map_id="knowledge-map:w43-batch",
                workspace_id=WORKSPACE_ID,
                revision=1,
                is_current=True,
                status="active",
                generation_mode="test",
                provider_used=False,
                source_revisions=[],
                supporting_material_ids=[],
                scope_policy={},
                role_provenance={},
                source_fingerprints={},
                quality={},
                fingerprint="w43-batch-fingerprint",
                warnings=[],
            )
        )

        source_refs = [{"material_id": "w43-material", "quote": "evidence"}]
        nodes = [
            KnowledgeNodeORM(
                id=f"{REVISION_ID}:theme",
                map_revision_id=REVISION_ID,
                node_id="theme",
                source_id="w43-material",
                semantic_key="theme:w43",
                label="W43 theme",
                summary="A material theme",
                kind="theme",
                revision=1,
                status="confirmed",
                content_hash="theme-hash",
                source_refs=source_refs,
                history={"node_type": "theme", "layer": "material"},
            )
        ]
        nodes.extend(
            KnowledgeNodeORM(
                id=f"{REVISION_ID}:{node_id}",
                map_revision_id=REVISION_ID,
                node_id=node_id,
                source_id="w43-material",
                semantic_key=f"concept:{node_id}",
                label=f"Concept {node_id}",
                summary="A material concept",
                kind="concept",
                revision=1,
                status="confirmed",
                content_hash=f"{node_id}-hash",
                source_refs=source_refs,
                history={"node_type": "concept", "layer": "material"},
            )
            for node_id in ("a", "b", "c")
        )
        session.add_all(nodes)

        edges = [
            KnowledgeEdgeORM(
                id=f"{REVISION_ID}:contains-{node_id}",
                map_revision_id=REVISION_ID,
                edge_id=f"contains-{node_id}",
                source_node_id="theme",
                target_node_id=node_id,
                relation="contains",
                source_refs=[
                    {"relation": "contains", "status": "confirmed", "layer": "material"},
                    *source_refs,
                ],
            )
            for node_id in ("a", "b", "c")
        ]
        candidate_edges = [("edge-ab", "a", "b"), ("edge-bc", "b", "c"), ("edge-ac", "a", "c")]
        edges.extend(
            KnowledgeEdgeORM(
                id=f"{REVISION_ID}:{edge_id}",
                map_revision_id=REVISION_ID,
                edge_id=edge_id,
                source_node_id=source,
                target_node_id=target,
                relation="prerequisite",
                source_refs=[
                    {"relation": "prerequisite", "status": "pending", "layer": "material"},
                    *source_refs,
                ],
            )
            for edge_id, source, target in candidate_edges
        )
        session.add_all(edges)

        candidate_ids = []
        for edge_id, source, target in candidate_edges:
            candidate_id = f"candidate-{edge_id}"
            candidate_ids.append(candidate_id)
            session.add(
                ReviewCandidateORM(
                    id=candidate_id,
                    workspace_id=WORKSPACE_ID,
                    map_revision_id=REVISION_ID,
                    candidate_key=f"edge:{edge_id}",
                    candidate_type="edge",
                    edge_id=edge_id,
                    status="pending",
                    payload={
                        "relation": "prerequisite",
                        "source_id": source,
                        "target_id": target,
                    },
                    evidence=source_refs,
                )
            )
        session.commit()
    return candidate_ids


def _revision_snapshot() -> tuple[list[tuple[str, int, bool, str]], list[tuple[str, str, str]]]:
    with SessionLocal() as session:
        revisions = [
            (row.id, int(row.revision), bool(row.is_current), row.status)
            for row in session.scalars(
                select(KnowledgeMapRevisionORM)
                .where(KnowledgeMapRevisionORM.workspace_id == WORKSPACE_ID)
                .order_by(KnowledgeMapRevisionORM.revision)
            ).all()
        ]
        candidates = [
            (row.id, row.map_revision_id, row.status)
            for row in session.scalars(
                select(ReviewCandidateORM)
                .where(ReviewCandidateORM.workspace_id == WORKSPACE_ID)
                .order_by(ReviewCandidateORM.id)
            ).all()
        ]
    return revisions, candidates


def test_w43_batch_confirm_is_one_revision_and_updates_all_selected_candidates():
    candidate_ids = _seed_batch_revision()

    with TestClient(app) as client:
        listed = client.get(
            f"/api/active-teaching/workspaces/{WORKSPACE_ID}/knowledge-map/candidates"
        )
        assert listed.status_code == 200, listed.text
        listed_candidates = listed.json()["candidates"]
        assert {item["id"] for item in listed_candidates} == set(candidate_ids)

        selected = candidate_ids[:2]
        response = client.post(
            f"/api/active-teaching/workspaces/{WORKSPACE_ID}/knowledge-map/candidates/decisions",
            json={"candidate_ids": selected, "decision": "confirmed"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["decided_count"] == 2
        assert len(body["candidates"]) == 2
        assert body["revision"] == 2

        current = client.get(
            f"/api/active-teaching/workspaces/{WORKSPACE_ID}/knowledge-map/candidates"
        )
        assert current.status_code == 200, current.text
        current_candidates = current.json()["candidates"]
        assert {item["status"] for item in current_candidates if item["candidate_key"] in {"edge:edge-ab", "edge:edge-bc"}} == {"confirmed"}
        assert next(item for item in current_candidates if item["candidate_key"] == "edge:edge-ac")["status"] == "pending"
        assert all(item["revision_id"] == body["revision_id"] for item in current_candidates)

    revisions, _ = _revision_snapshot()
    assert len(revisions) == 2
    assert revisions[0][2] is False
    assert revisions[0][3] == "superseded"
    assert revisions[1][2] is True
    assert revisions[1][3] == "active"

    with SessionLocal() as session:
        edge_rows = session.scalars(
            select(KnowledgeEdgeORM).where(KnowledgeEdgeORM.map_revision_id == body["revision_id"])
        ).all()
        edge_status = {
            row.edge_id: (row.source_refs[0] or {}).get("status")
            for row in edge_rows
            if row.edge_id in {"edge-ab", "edge-bc", "edge-ac"}
        }
    assert edge_status == {"edge-ab": "confirmed", "edge-bc": "confirmed", "edge-ac": "pending"}


def test_w43_invalid_batch_is_rejected_without_any_revision_or_candidate_write():
    candidate_ids = _seed_batch_revision()
    before = _revision_snapshot()

    with TestClient(app) as client:
        response = client.post(
            f"/api/active-teaching/workspaces/{WORKSPACE_ID}/knowledge-map/candidates/decisions",
            json={
                "candidate_ids": [candidate_ids[0], "candidate-does-not-exist"],
                "decision": "confirmed",
            },
        )
        assert response.status_code >= 400

    assert _revision_snapshot() == before


def test_w43_concurrent_same_revision_has_one_commit_and_one_stale_conflict():
    candidate_ids = _seed_batch_revision()

    def decide_once():
        try:
            return generic_api._v1_update_candidates_orm(
                WORKSPACE_ID,
                [{"candidate_id": candidate_ids[0], "decision": "confirmed", "reason": "race"}],
            )
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: decide_once(), range(2)))

    successes = [item for item in outcomes if isinstance(item, dict)]
    conflicts = [item for item in outcomes if isinstance(item, HTTPException)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert conflicts[0].detail["error"]["code"] == "KNOWLEDGE_MAP_CANDIDATE_STALE"
    revisions, candidates = _revision_snapshot()
    assert [(revision[1], revision[2], revision[3]) for revision in revisions] == [
        (1, False, "superseded"),
        (2, True, "active"),
    ]
    assert len(candidates) == 6


def _count_material_and_model_common(nodes: Iterable[dict]) -> tuple[int, int]:
    material = [
        item
        for item in nodes
        if str(item.get("layer") or "material") == "material"
        and str(item.get("node_type") or "concept") == "concept"
    ]
    model_common = [
        item
        for item in nodes
        if str(item.get("layer") or "material") == "model_common"
        and str(item.get("node_type") or "concept") == "concept"
    ]
    return len(material), len(model_common)


def test_w43_model_common_is_not_in_default_plan_count_without_explicit_opt_in():
    workspace_id = "w43-plan-count"
    revision_id = "w43-plan-count-revision"
    material_nodes = [f"material-{index}" for index in range(150)]
    common_nodes = [f"common-{index}" for index in range(5)]

    with SessionLocal() as session:
        session.add(
            KnowledgeMapRevisionORM(
                id=revision_id,
                map_id="knowledge-map:w43-plan-count",
                workspace_id=workspace_id,
                revision=1,
                is_current=True,
                status="active",
                generation_mode="test",
                provider_used=False,
                source_revisions=[],
                supporting_material_ids=[],
                scope_policy={},
                role_provenance={},
                source_fingerprints={},
                quality={},
                fingerprint="w43-plan-count-fingerprint",
                warnings=[],
            )
        )
        session.add_all(
            [
                KnowledgeNodeORM(
                    id=f"{revision_id}:{node_id}",
                    map_revision_id=revision_id,
                    node_id=node_id,
                    source_id="w43-material" if node_id.startswith("material-") else "w43-model-common",
                    semantic_key=node_id,
                    label=node_id,
                    summary=node_id,
                    kind="concept",
                    revision=1,
                    status="confirmed",
                    content_hash=f"{node_id}-hash",
                    source_refs=[],
                    history={
                        "node_type": "concept",
                        "layer": "material" if node_id.startswith("material-") else "model_common",
                    },
                )
                for node_id in [*material_nodes, *common_nodes]
            ]
        )
        session.commit()

    with TestClient(app) as client:
        graph_response = client.get(
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map",
            params={"revision": "active"},
        )
        assert graph_response.status_code == 200, graph_response.text
        graph = graph_response.json()
        workspaces_response = client.get("/api/active-teaching/workspaces")
        assert workspaces_response.status_code == 200, workspaces_response.text
        workspace = next(
            item
            for item in workspaces_response.json()["items"]
            if item["workspace_id"] == workspace_id
        )

    assert graph["material_concept_count"] == 150
    assert graph["model_common_concept_count"] == 5
    assert graph["plan_concept_count"] == 150
    assert workspace["knowledge_point_count"] == 150
    assert _count_material_and_model_common(graph["nodes"]) == (150, 5)
    assert len(generic_api._v1_plan_order(graph["nodes"], graph["edges"])) == 150
    assert len(
        generic_api._v1_plan_order(
            graph["nodes"], graph["edges"], include_model_common=True
        )
    ) == 155
