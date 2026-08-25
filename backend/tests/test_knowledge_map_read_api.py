from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.knowledge.map_read_api import (
    CYCLE_USER_MESSAGE,
    InMemoryKnowledgeMapSnapshotPort,
    PARTIAL_USER_MESSAGE,
    create_knowledge_map_read_router,
)
from app.knowledge.map_read_demo import build_java_database_demo_snapshot
from app.knowledge.map_v2.compiler import KnowledgeMapCompiler
from app.knowledge.pipeline_v2.handoff import KnowledgeMapHandoffCandidate
from app.knowledge.pipeline_v2.progress import PipelineStatus


def _client(port: InMemoryKnowledgeMapSnapshotPort) -> TestClient:
    app = FastAPI()
    app.include_router(create_knowledge_map_read_router(port))
    return TestClient(app)


def _empty_snapshot():
    handoff = KnowledgeMapHandoffCandidate(
        handoff_id="empty-handoff",
        source_identity="fixture://empty",
        source_revision="empty-v1",
        verification_status=PipelineStatus.COMPLETE,
        concepts=(),
        pipeline_receipt_id="empty-receipt",
    )
    return KnowledgeMapCompiler(clock=lambda: 0.0).compile_handoff(
        handoff,
        known_evidence_ids=(),
    ).snapshot


def test_read_api_exposes_snapshot_queries_and_realistic_demo_detail() -> None:
    snapshot = build_java_database_demo_snapshot()
    port = InMemoryKnowledgeMapSnapshotPort(
        snapshots=(snapshot,),
        aliases=(("demo", snapshot.snapshot_id),),
    )

    with _client(port) as client:
        response = client.get("/api/verified-knowledge-map/maps/demo")
        assert response.status_code == 200
        payload = response.json()
        assert payload["display"] == {
            "renderable": True,
            "display_state": "READY",
            "user_message": "知识图已完成验证",
            "is_partial": False,
            "is_demo": True,
            "demo_label": "DEMO DATA",
        }
        assert len(payload["snapshot"]["nodes"]) == 13
        assert {
            item["edge_type"] for item in payload["snapshot"]["edges"]
        } == {
            "PREREQUISITE",
            "SUPPORTS",
            "RELATED",
            "POSSIBLE_PREREQUISITE",
        }

        roots = client.get(
            "/api/verified-knowledge-map/maps/demo/roots"
        ).json()["items"]
        leaves = client.get(
            "/api/verified-knowledge-map/maps/demo/leaves"
        ).json()["items"]
        critical = client.get(
            "/api/verified-knowledge-map/maps/demo/critical-concepts"
        ).json()["items"]
        orphans = client.get(
            "/api/verified-knowledge-map/maps/demo/orphans"
        ).json()["items"]
        uncertain = client.get(
            "/api/verified-knowledge-map/maps/demo/uncertain-edges"
        ).json()["items"]

        assert roots
        assert leaves
        assert len(critical) == 3
        assert [item["canonical_name"] for item in orphans] == [
            "数据库迁移工具"
        ]
        assert len(uncertain) == 1
        assert uncertain[0]["edge_type"] == "POSSIBLE_PREREQUISITE"

        isolation = next(
            item
            for item in snapshot.nodes
            if item.canonical_name == "事务隔离级别"
        )
        detail = client.get(
            f"/api/verified-knowledge-map/maps/demo/concepts/{isolation.concept_id}"
        ).json()
        assert detail["concept"]["canonical_name"] == "事务隔离级别"
        assert detail["evidence_count"] == 1
        assert detail["source_references"][0]["href"] is None
        assert detail["source_references"][0]["link_status"] == (
            "PENDING_SOURCE_ROUTE"
        )
        assert detail["repair_lineage"][0]["classification"] == (
            "TERMINOLOGY_ALIGNMENT"
        )
        assert detail["prerequisites"]

        connection = next(
            item
            for item in snapshot.nodes
            if item.canonical_name == "数据库连接 Connection"
        )
        dependents = client.get(
            "/api/verified-knowledge-map/maps/demo/concepts/"
            f"{connection.concept_id}/dependents"
        ).json()["items"]
        assert {item["canonical_name"] for item in dependents} >= {
            "JDBC 事务",
            "事务隔离级别",
        }

        diagnostics = client.get(
            "/api/verified-knowledge-map/maps/demo/diagnostics"
        ).json()
        assert diagnostics["status"] == "COMPLETE"
        assert diagnostics["diagnostics"]["orphan_concept_ids"] == [
            orphans[0]["concept_id"]
        ]


def test_cycle_is_blocked_with_user_message_and_no_stack_trace() -> None:
    snapshot = build_java_database_demo_snapshot(include_cycle=True)
    port = InMemoryKnowledgeMapSnapshotPort(
        snapshots=(snapshot,),
        aliases=(("cycle", snapshot.snapshot_id),),
    )

    with _client(port) as client:
        snapshot_response = client.get(
            "/api/verified-knowledge-map/maps/cycle"
        )
        roots_response = client.get(
            "/api/verified-knowledge-map/maps/cycle/roots"
        )

    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["snapshot"]["status"] == "FAILED"
    assert snapshot_response.json()["display"]["renderable"] is False
    assert snapshot_response.json()["display"]["user_message"] == (
        CYCLE_USER_MESSAGE
    )
    assert roots_response.status_code == 409
    assert roots_response.json()["detail"]["message"] == CYCLE_USER_MESSAGE
    assert "Traceback" not in roots_response.text


def test_partial_empty_and_missing_states_are_explicit() -> None:
    partial = build_java_database_demo_snapshot(
        pipeline_status=PipelineStatus.PARTIAL
    )
    empty = _empty_snapshot()
    port = InMemoryKnowledgeMapSnapshotPort(
        snapshots=(partial, empty),
        aliases=(
            ("partial", partial.snapshot_id),
            ("empty", empty.snapshot_id),
        ),
    )

    with _client(port) as client:
        partial_payload = client.get(
            "/api/verified-knowledge-map/maps/partial"
        ).json()
        empty_payload = client.get(
            "/api/verified-knowledge-map/maps/empty"
        ).json()
        missing = client.get("/api/verified-knowledge-map/maps/missing")

    assert partial_payload["snapshot"]["status"] == "PARTIAL"
    assert partial_payload["display"]["renderable"] is True
    assert partial_payload["display"]["user_message"] == PARTIAL_USER_MESSAGE
    assert empty_payload["display"]["display_state"] == "EMPTY"
    assert empty_payload["display"]["renderable"] is True
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "KNOWLEDGE_MAP_NOT_FOUND"
