from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app import active_teaching, credential_store, db, orm
from app import generic_knowledge_api as generic_api
from app.main import app
from app.orm import KnowledgeMapJobORM, KnowledgeMapRevisionORM, SessionLocal
from app.provider_runtime import create_provider_session, get_provider_session


def _hierarchical_payload(payload: dict) -> dict:
    """Make contract providers emit an explicit evidence-backed theme hierarchy."""

    result = {**payload}
    nodes = [dict(item) for item in payload.get("knowledge_points") or []]
    material_nodes = [
        item for item in nodes if str(item.get("layer") or "material") == "material"
    ]
    for item in nodes:
        item.setdefault("node_type", "concept")
    source_chunk_ids = sorted(
        {
            str(chunk_id)
            for item in material_nodes
            for chunk_id in item.get("source_chunk_ids") or []
        }
    )
    theme_id = "contract-theme"
    theme = {
        "id": theme_id,
        "name": "Material theme",
        "explanation": "Theme grounded by the same test material.",
        "node_type": "theme",
        "layer": "material",
        "source_chunk_ids": source_chunk_ids,
    }
    contains = [
        {
            "prerequisite_id": theme_id,
            "dependent_id": str(item["id"]),
            "relation": "contains",
            "layer": "material",
            "source_chunk_ids": list(item.get("source_chunk_ids") or []),
        }
        for item in material_nodes
        if item.get("node_type") == "concept"
    ]
    result["knowledge_points"] = [theme, *nodes]
    result["dependencies"] = [*contains, *(payload.get("dependencies") or [])]
    return result


class _HierarchicalProvider:
    def __init__(self, provider):
        self.provider = provider

    async def json(self, messages):
        payload = await self.provider.json(messages)
        return _hierarchical_payload(payload)


@pytest.fixture()
def migrated_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def _receipt(material_id: str) -> dict[str, object]:
    return {
        "learning_goal": "掌握资料中的概念",
        "current_level": "beginner",
        "target_level": "complete_mastery",
        "start_date": date.today().isoformat(),
        "deadline": (date.today() + timedelta(days=7)).isoformat(),
        "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "preferred_learning_style": "explain-and-practice",
        "daily_task_minutes": 60,
        "material_ids": [material_id],
    }


def _material(client: TestClient) -> str:
    response = client.post(
        "/api/active-teaching/workspaces/v1/materials",
        files={"file": ("source.md", b"# Alpha\nGrounded evidence.", "text/markdown")},
    )
    assert response.status_code == 200, response.text
    material_id = response.json()["id"]
    assert client.put(
        "/api/active-teaching/workspaces/v1/receipt",
        json=_receipt(material_id),
    ).status_code == 200
    return material_id


def test_v1_requires_real_provider_and_reports_deferred_media(migrated_api):
    with TestClient(app) as client:
        _material(client)
        retired = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-graph/generate"
        )
        assert retired.status_code == 410
        assert retired.json()["error"]["code"] == "LEGACY_KNOWLEDGE_GRAPH_ENDPOINT"
        assert retired.json()["error"]["details"]["canonical_path"].endswith(
            "/knowledge-map/jobs"
        )
        compatibility = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/generate",
            json={},
        )
        assert compatibility.status_code == 428
        assert compatibility.json()["error"]["code"] == "PROVIDER_VERIFICATION_REQUIRED"
        rejected = client.post("/api/active-teaching/workspaces/v1/knowledge-map/jobs", json={})
        assert rejected.status_code == 428
        assert rejected.json()["error"]["code"] == "PROVIDER_VERIFICATION_REQUIRED"
        capabilities = client.get("/api/active-teaching/material-capabilities")
        assert capabilities.status_code == 200
        media = [
            item
            for item in capabilities.json()["registry"]["capabilities"]
            if any(token in item["adapter"] for token in ("image", "audio", "ocr", "transcri"))
        ]
        assert media
        assert all("available" in item and "adapter_version" in item for item in media)


def test_receipt_persists_explicit_learner_preference_profile(migrated_api):
    with TestClient(app) as client:
        material_id = _material(client)
        receipt = _receipt(material_id)
        receipt["learner_interests"] = "桌游、做饭"
        saved = client.put(
            "/api/active-teaching/workspaces/v1/receipt",
            json=receipt,
        )
        assert saved.status_code == 200, saved.text

        profile = client.get(
            "/api/active-teaching/workspaces/v1/learner-preference-profile"
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["schema_version"] == "learner-preference-profile.v1"
        assert profile.json()["revision"] >= 1
        assert profile.json()["preferences"] == [
            {"key": "interest", "value": "桌游", "source": "explicit"},
            {"key": "interest", "value": "做饭", "source": "explicit"},
        ]

        receipt["learner_interests"] = ""
        cleared = client.put(
            "/api/active-teaching/workspaces/v1/receipt",
            json=receipt,
        )
        assert cleared.status_code == 200, cleared.text
        empty_profile = client.get(
            "/api/active-teaching/workspaces/v1/learner-preference-profile"
        )
        assert empty_profile.json()["preferences"] == []


def test_v1_orm_job_candidate_plan_and_last_good(migrated_api, monkeypatch):
    material_id: str
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "test-model",
            "credential_ref": credential_store.store_secret("test-key"),
        }
    )
    session = get_provider_session(provider["provider_session_id"])
    assert session is not None
    session.verified = True

    class Provider:
        fail = False

        async def json(self, messages):
            if self.fail:
                raise generic_api.ProviderError("PROVIDER_TIMEOUT", "test failure", 504)
            prompt = json.loads(messages[-1]["content"])
            chunk_id = prompt["chunks"][0]["chunk_id"]
            return {
                "knowledge_points": [
                    {"id": "a", "name": "Alpha", "explanation": "A", "source_chunk_ids": [chunk_id]},
                    {"id": "b", "name": "Beta", "explanation": "B", "source_chunk_ids": [chunk_id]},
                ],
                "dependencies": [
                    {"prerequisite_id": "a", "dependent_id": "b", "source_chunk_ids": [chunk_id]}
                ],
            }

    test_provider = Provider()
    monkeypatch.setattr(
        generic_api,
        "provider_for_session",
        lambda _session: _HierarchicalProvider(test_provider),
    )
    with TestClient(app) as client:
        material_id = _material(client)
        discovery_receipt = _receipt(material_id)
        discovery_receipt["discover_external_resources"] = True
        assert client.put(
            "/api/active-teaching/workspaces/v1/receipt",
            json=discovery_receipt,
        ).status_code == 200
        queued = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["id"]
        job = client.get(f"/api/active-teaching/workspaces/v1/knowledge-map/jobs/{job_id}")
        assert job.json()["status"] == "ready", job.text
        graph = client.get("/api/active-teaching/workspaces/v1/knowledge-map", params={"revision": "active"})
        assert graph.status_code == 200
        revision = graph.json()["revision"]

        cached = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert cached.status_code == 202, cached.text
        assert cached.json()["cache_hit"] is True
        assert cached.json()["revision_id"] == graph.json()["revision_id"]
        assert {item["disposition"] for item in cached.json()["dispositions"]} == {
            "included"
        }

        candidates = client.get("/api/active-teaching/workspaces/v1/knowledge-map/candidates")
        assert candidates.status_code == 200
        candidate = candidates.json()["candidates"][0]
        assert candidate["id"] != candidate["edge_id"]
        decision = client.post(
            f"/api/active-teaching/workspaces/v1/knowledge-map/candidates/{candidate['id']}/decision",
            json={"decision": "confirmed"},
        )
        assert decision.status_code == 200, decision.text
        revision = decision.json()["revision"]
        preview = client.post("/api/active-teaching/workspaces/v1/mastery-plans/preview", json={})
        assert preview.status_code == 200, preview.text
        assert all(
            item["relation"] in {"contains", "prerequisite"}
            for item in preview.json()["excluded_unconfirmed_relations"]
        )
        activated = client.post(
            f"/api/active-teaching/workspaces/v1/mastery-plans/{preview.json()['preview_id']}/activate",
            json={},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["execution_task_count"] > 0
        current_plan = client.get(
            "/api/active-teaching/workspaces/v1/mastery-plans/current"
        )
        assert current_plan.status_code == 200, current_plan.text
        assert current_plan.json()["is_current"] is True
        assert len(current_plan.json()["execution_tasks"]) == activated.json()["execution_task_count"]
        classroom = client.post("/api/active-teaching/workspaces/v1/sessions/start", json={})
        assert classroom.status_code == 200, classroom.text
        turn = classroom.json()["state"]["teacher_turn_v1"]
        assert [item["type"] for item in turn["blocks"]] == [
            "objective",
            "explanation",
            "analogy",
            "worked_example",
        ]
        assert turn["check"]["prompt"]
        assert turn["state"]["waiting_for_answer"] is True
        session_id = classroom.json()["session_id"]
        for expected_phase in (
            "targeted_correction",
            "alternate_example_or_prerequisite",
            "blocked",
        ):
            response = client.post(
                f"/api/active-teaching/sessions/{session_id}/advance",
                json={"message": "我不懂这里的对象是什么，需要再解释一下。"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["state"]["teacher_turn_v1"]["state"]["phase"] == expected_phase
        point_before_skip = response.json()["state"]["current_knowledge_point_id"]
        skipped = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"force_complete": True},
        )
        assert skipped.status_code == 200, skipped.text
        skipped_payload = skipped.json()
        assert skipped_payload["state"]["current_knowledge_point_id"] != point_before_skip
        assert skipped_payload["state"]["teacher_turn_v1"]["state"]["waiting_for_answer"] is True
        assert skipped_payload["state"]["mastery_changed"] is True
        assert skipped_payload["state"]["completion_origin"] == "self_attested"
        assert skipped_payload["state"]["provider_verified"] is False
        assert skipped_payload["state"]["self_attested_completion_evidence_id"]
        assert "下面进入" in skipped_payload["messages"][-1]["content"]
        assert "并非教师验证掌握" not in skipped_payload["messages"][-1]["content"]

        graph_before_discovery = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()
        with active_teaching._connect() as connection:
            plan_before_discovery = connection.execute(
                """SELECT plan_snapshot FROM orm_plan_activation_records
                   WHERE plan_id=?""",
                (preview.json()["preview_id"],),
            ).fetchone()[0]

        async def canonical_resource_stub(
            connection,
            *,
            workspace_id,
            points,
            provider_session_id,
            map_revision_id=None,
            task_label="",
            force=False,
        ):
            assert map_revision_id == graph_before_discovery["revision_id"]
            assert {point["id"] for point in points} == {
                node["id"] for node in graph_before_discovery["nodes"]
            }
            connection.execute(
                """INSERT INTO orm_learning_resources
                   (id,workspace_id,map_revision_id,knowledge_point_id,kind,title,
                    description,url,host,provider_label,authority,source,search_query,
                    discovery_meta,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "canonical-resource-sentinel",
                    workspace_id,
                    map_revision_id,
                    points[0]["id"],
                    "official_documentation",
                    "规范资源哨兵",
                    "仅用于验证资源发现与地图解耦。",
                    "https://example.org/canonical-resource-sentinel",
                    "example.org",
                    "example",
                    "official",
                    "test_grounded",
                    "规范资源哨兵",
                    json.dumps({"scope": "task"}, ensure_ascii=False),
                    "active",
                    "2026-08-13T00:00:00+00:00",
                ),
            )
            connection.commit()
            return {
                "status": "completed",
                "resource_count": 1,
                "searched_point_count": 1,
                "warnings": [],
                "provider_meta": {
                    "mode": "contract_stub",
                    "scope": "task",
                    "map_revision_id": map_revision_id,
                },
            }

        monkeypatch.setattr(
            generic_api,
            "discover_task_resources",
            canonical_resource_stub,
        )
        discovery = client.post(
            "/api/active-teaching/workspaces/v1/resource-discovery/start",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert discovery.status_code == 202, discovery.text
        discovery_status = client.get(
            "/api/active-teaching/workspaces/v1/resource-discovery/status",
            params={"run_id": discovery.json()["run_id"]},
        )
        assert discovery_status.status_code == 200, discovery_status.text
        assert [item["id"] for item in discovery_status.json()["resources"]] == [
            "canonical-resource-sentinel"
        ]
        graph_after_discovery = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()

        with active_teaching._connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM active_knowledge_points WHERE workspace_id=?",
                ("v1",),
            ).fetchone()[0] == 0
            self_attested = connection.execute(
                """SELECT event_type,passed FROM orm_plan_mastery_events
                   WHERE workspace_id=? ORDER BY occurred_at,id""",
                ("v1",),
            ).fetchall()
            assert [(row["event_type"], row["passed"]) for row in self_attested] == [
                ("self_attested_completion", 1)
            ]
            independent_task = connection.execute(
                """SELECT * FROM orm_plan_execution_tasks
                   WHERE workspace_id=? AND learning_phase='independent_application'
                   ORDER BY day_index,task_order,id LIMIT 1""",
                ("v1",),
            ).fetchone()
            assert independent_task is not None
            independent_point = active_teaching._point_for_task(
                connection,
                independent_task,
            )
            mastery_state = {"completed_tasks": [], "completed_task_minutes": 0}
            active_teaching._v1_successful_task(
                connection,
                task=independent_task,
                point=independent_point,
                state=mastery_state,
                score=0.93,
                evaluation={"mode": "verified_contract", "criteria_met": True},
            )
            connection.commit()
            mastery_event = connection.execute(
                """SELECT event_type,passed,evidence_hash
                   FROM orm_plan_mastery_events
                   WHERE task_id=?""",
                (independent_task["id"],),
            ).fetchone()
            assert mastery_event["event_type"] == "mastery_confirmed"
            assert mastery_event["passed"] == 1
            assert mastery_event["evidence_hash"]
            assert connection.execute(
                "SELECT COUNT(*) FROM active_knowledge_points WHERE workspace_id=?",
                ("v1",),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM active_external_resources WHERE workspace_id=?",
                ("v1",),
            ).fetchone()[0] == 0
            assert connection.execute(
                """SELECT plan_snapshot FROM orm_plan_activation_records
                   WHERE plan_id=?""",
                (preview.json()["preview_id"],),
            ).fetchone()[0] == plan_before_discovery
            assert graph_after_discovery["revision_id"] == graph_before_discovery["revision_id"]
            assert graph_after_discovery["source_fingerprint"] == graph_before_discovery["source_fingerprint"]
            assert graph_after_discovery["nodes"] == graph_before_discovery["nodes"]
            assert graph_after_discovery["edges"] == graph_before_discovery["edges"]
            connection.execute(
                "UPDATE active_chunks SET text=text || ?, content_hash=? WHERE material_id=?",
                (" changed", "changed-hash", material_id),
            )
            connection.commit()
        test_provider.fail = True
        failed = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert failed.status_code == 202
        failed_job = client.get(
            f"/api/active-teaching/workspaces/v1/knowledge-map/jobs/{failed.json()['id']}"
        )
        assert failed_job.json()["status"] == "failed"
        assert failed_job.json()["dispositions"]
        assert all(
            item["disposition"] == "needs_review"
            and item["reason"].startswith("generation_failed:")
            for item in failed_job.json()["dispositions"]
        )
        failed_revision_id = failed_job.json()["revision_id"]
        assert failed_revision_id
        with SessionLocal() as session_store:
            failed_revision = session_store.get(KnowledgeMapRevisionORM, failed_revision_id)
            assert failed_revision is not None
            assert failed_revision.status == "failed"
            assert failed_revision.is_current is False
            assert failed_revision.failure_json["code"] == "PROVIDER_TIMEOUT"
        assert client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map", params={"revision": "active"}
        ).json()["revision"] == revision


def test_generation_cache_key_includes_verified_provider_configuration():
    source = "source-fingerprint"
    base = SimpleNamespace(
        provider="openai_compatible",
        base_url="https://provider.invalid/v1",
        model="model-a",
        credential_ref="mfcred_a",
    )
    same = SimpleNamespace(**vars(base))
    changed_model = SimpleNamespace(**{**vars(base), "model": "model-b"})
    changed_credential = SimpleNamespace(
        **{**vars(base), "credential_ref": "mfcred_b"}
    )

    fingerprint = generic_api._v1_generation_fingerprint(source, base)
    assert fingerprint == generic_api._v1_generation_fingerprint(source, same)
    assert fingerprint != generic_api._v1_generation_fingerprint(source, changed_model)
    assert fingerprint != generic_api._v1_generation_fingerprint(
        source,
        changed_credential,
    )


def test_public_runtime_policy_exposes_ui_bounds_without_provider_secrets(migrated_api):
    with TestClient(app) as client:
        response = client.get("/api/active-teaching/runtime-policy")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scheduling"]["minimum_daily_minutes"] > 0
    assert payload["scheduling"]["maximum_daily_minutes"] >= payload["scheduling"]["minimum_daily_minutes"]
    assert "credential" not in json.dumps(payload).casefold()


def test_concurrent_generation_deduplicates_exact_request_and_rejects_changed_inputs(
    migrated_api,
):
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "concurrency-contract-model",
            "credential_ref": credential_store.store_secret("test-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True

    with TestClient(app) as client:
        material_id = _material(client)
        with active_teaching._connect() as connection:
            chunks = generic_api._evidence_chunks(connection, "v1", [material_id])
        source_fingerprint = generic_api._v1_source_fingerprint(chunks)
        generation_fingerprint = generic_api._v1_generation_fingerprint(
            source_fingerprint,
            provider_session,
        )
        with SessionLocal() as session:
            session.add(
                KnowledgeMapJobORM(
                    id="concurrent-job",
                    workspace_id="v1",
                    map_id="knowledge-map:v1",
                    job_type="generate",
                    status="extracting",
                    request_json={
                        "material_ids": [material_id],
                        "source_fingerprint": source_fingerprint,
                        "generation_fingerprint": generation_fingerprint,
                    },
                )
            )
            session.commit()

        duplicate = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["id"] == "concurrent-job"

        with SessionLocal() as session:
            running = session.get(KnowledgeMapJobORM, "concurrent-job")
            running.request_json = {
                **dict(running.request_json or {}),
                "generation_fingerprint": "different-inputs",
            }
            session.commit()
        conflict = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "KNOWLEDGE_MAP_JOB_ALREADY_RUNNING"
        assert conflict.json()["error"]["details"]["running_job_id"] == "concurrent-job"


def test_startup_recovery_fails_orphaned_jobs_for_safe_retry(migrated_api):
    with SessionLocal() as session:
        session.add(
            KnowledgeMapJobORM(
                id="orphaned-job",
                workspace_id="restart-workspace",
                map_id="knowledge-map:restart-workspace",
                job_type="generate",
                status="extracting",
                request_json={},
            )
        )
        session.commit()

    assert generic_api.recover_interrupted_knowledge_map_jobs() == 1
    with SessionLocal() as session:
        job = session.get(KnowledgeMapJobORM, "orphaned-job")
        assert job is not None
        assert job.status == "failed"
        assert job.failure_json["code"] == "KNOWLEDGE_MAP_JOB_INTERRUPTED"
        assert job.failure_json["details"]["retryable"] is True


def test_same_name_across_materials_requires_reviewed_merge(migrated_api, monkeypatch):
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "contract-model",
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
            assert len(chunks) == 2
            return {
                "knowledge_points": [
                    {
                        "id": "left",
                        "name": "Shared display name",
                        "explanation": "Grounded by the first source.",
                        "source_chunk_ids": [chunks[0]["chunk_id"]],
                    },
                    {
                        "id": "right",
                        "name": "Shared display name",
                        "explanation": "Grounded by the second source.",
                        "source_chunk_ids": [chunks[1]["chunk_id"]],
                    },
                ],
                "dependencies": [
                    {
                        "prerequisite_id": "left",
                        "dependent_id": "right",
                        "source_chunk_ids": [
                            chunks[0]["chunk_id"],
                            chunks[1]["chunk_id"],
                        ],
                    }
                ],
            }

    monkeypatch.setattr(
        generic_api,
        "provider_for_session",
        lambda _session: _HierarchicalProvider(Provider()),
    )
    with TestClient(app) as client:
        material_ids: list[str] = []
        for name, body in (
            ("first.md", b"# First\nSentinel one."),
            ("second.md", b"# Second\nSentinel two."),
        ):
            uploaded = client.post(
                "/api/active-teaching/workspaces/v1/materials",
                files={"file": (name, body, "text/markdown")},
            )
            assert uploaded.status_code == 200, uploaded.text
            material_ids.append(uploaded.json()["id"])
        receipt = _receipt(material_ids[0])
        receipt["material_ids"] = material_ids
        assert client.put(
            "/api/active-teaching/workspaces/v1/receipt", json=receipt
        ).status_code == 200

        queued = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert queued.status_code == 202, queued.text
        job = client.get(
            f"/api/active-teaching/workspaces/v1/knowledge-map/jobs/{queued.json()['id']}"
        ).json()
        assert job["status"] == "ready", job

        graph_before = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()
        assert len(
            [node for node in graph_before["nodes"] if node["node_type"] == "concept"]
        ) == 2
        assert all(node["status"] == "confirmed" for node in graph_before["nodes"])
        candidates = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map/candidates"
        ).json()["candidates"]
        merge = next(item for item in candidates if item["candidate_type"] == "merge_suggestion")

        decision = client.post(
            f"/api/active-teaching/workspaces/v1/knowledge-map/candidates/{merge['id']}/decision",
            json={"decision": "confirmed", "reason": "reviewed evidence"},
        )
        assert decision.status_code == 200, decision.text
        graph_after = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()
        source_id = merge["payload"]["source_node_id"]
        target_id = merge["payload"]["target_node_id"]
        by_id = {node["id"]: node for node in graph_after["nodes"]}
        assert by_id[target_id]["status"] == "merged"
        assert {
            evidence["material_id"] for evidence in by_id[source_id]["evidence"]
        } == set(material_ids)
        collapsed = next(
            edge
            for edge in graph_after["edges"]
            if edge["source_id"] == source_id and edge["target_id"] == source_id
        )
        assert collapsed["status"] == "rejected"
        next_candidates = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map/candidates"
        ).json()["candidates"]
        edge_candidate = next(
            item
            for item in next_candidates
            if item["candidate_type"] == "edge"
            and item["payload"].get("source_id")
            == item["payload"].get("target_id")
        )
        assert edge_candidate["status"] == "rejected"

        preview = client.post(
            "/api/active-teaching/workspaces/v1/mastery-plans/preview", json={}
        )
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["tasks"]) >= 1
        assert all(task["knowledge_point_id"] != target_id for task in preview.json()["tasks"])


def test_rejecting_only_material_contains_edge_fails_hierarchy_gate(
    migrated_api,
    monkeypatch,
):
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "hierarchy-contract-model",
            "credential_ref": credential_store.store_secret("test-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True

    class Provider:
        async def json(self, messages):
            prompt = json.loads(messages[-1]["content"])
            chunk_id = prompt["chunks"][0]["chunk_id"]
            return {
                "knowledge_points": [
                    {
                        "id": "only-concept",
                        "name": "Only material concept",
                        "explanation": "One evidence-grounded concept.",
                        "source_chunk_ids": [chunk_id],
                    }
                ],
                "dependencies": [],
            }

    monkeypatch.setattr(
        generic_api,
        "provider_for_session",
        lambda _session: _HierarchicalProvider(Provider()),
    )
    with TestClient(app) as client:
        _material(client)
        queued = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert queued.status_code == 202, queued.text
        assert client.get(
            f"/api/active-teaching/knowledge-map/jobs/{queued.json()['id']}"
        ).json()["status"] == "ready"
        graph_before = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()
        contains_candidate = next(
            item
            for item in client.get(
                "/api/active-teaching/workspaces/v1/knowledge-map/candidates"
            ).json()["candidates"]
            if item["candidate_type"] == "edge"
            and item["payload"]["relation"] == "contains"
        )

        rejected = client.post(
            f"/api/active-teaching/workspaces/v1/knowledge-map/candidates/{contains_candidate['id']}/decision",
            json={"decision": "rejected", "reason": "test hierarchy gate"},
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "KNOWLEDGE_MAP_GRAPH_INVALID"
        graph_after = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()
        assert graph_after["revision_id"] == graph_before["revision_id"]


def test_confirming_last_edge_of_cycle_is_rejected_atomically(migrated_api, monkeypatch):
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "contract-model",
            "credential_ref": credential_store.store_secret("test-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True

    class Provider:
        async def json(self, messages):
            prompt = json.loads(messages[-1]["content"])
            chunk_id = prompt["chunks"][0]["chunk_id"]
            return {
                "knowledge_points": [
                    {
                        "id": node_id,
                        "name": f"Concept {node_id}",
                        "explanation": f"Evidence for {node_id}.",
                        "source_chunk_ids": [chunk_id],
                    }
                    for node_id in ("a", "b", "c")
                ],
                "dependencies": [
                    {
                        "prerequisite_id": source,
                        "dependent_id": target,
                        "source_chunk_ids": [chunk_id],
                    }
                    for source, target in (("a", "b"), ("b", "c"), ("c", "a"))
                ],
            }

    monkeypatch.setattr(
        generic_api,
        "provider_for_session",
        lambda _session: _HierarchicalProvider(Provider()),
    )
    with TestClient(app) as client:
        _material(client)
        queued = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert queued.status_code == 202, queued.text
        job = client.get(
            f"/api/active-teaching/workspaces/v1/knowledge-map/jobs/{queued.json()['id']}"
        ).json()
        assert job["status"] == "ready", job
        graph = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()
        assert graph["quality"]["proposed_graph_acyclic"] is False, graph
        assert graph["quality"]["proposed_cycle_count"] >= 1

        for _ in range(2):
            pending = [
                item
                for item in client.get(
                    "/api/active-teaching/workspaces/v1/knowledge-map/candidates"
                ).json()["candidates"]
                if item["candidate_type"] == "edge"
                and item["status"] == "pending"
                and item["payload"].get("relation", "prerequisite") == "prerequisite"
            ]
            accepted = client.post(
                f"/api/active-teaching/workspaces/v1/knowledge-map/candidates/{pending[0]['id']}/decision",
                json={"decision": "confirmed"},
            )
            assert accepted.status_code == 200, accepted.text

        before_rejected_decision = client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()["revision"]
        last_pending = next(
            item
            for item in client.get(
                "/api/active-teaching/workspaces/v1/knowledge-map/candidates"
            ).json()["candidates"]
            if item["candidate_type"] == "edge"
            and item["status"] == "pending"
            and item["payload"].get("relation", "prerequisite") == "prerequisite"
        )
        rejected = client.post(
            f"/api/active-teaching/workspaces/v1/knowledge-map/candidates/{last_pending['id']}/decision",
            json={"decision": "confirmed"},
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "KNOWLEDGE_MAP_GRAPH_INVALID"
        assert client.get(
            "/api/active-teaching/workspaces/v1/knowledge-map",
            params={"revision": "active"},
        ).json()["revision"] == before_rejected_decision


def test_extension_requires_confirmation_and_unconfirmed_common_is_not_projected(
    migrated_api, monkeypatch
):
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "contract-model",
            "credential_ref": credential_store.store_secret("test-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True

    class Provider:
        async def json(self, messages):
            prompt = json.loads(messages[-1]["content"])
            chunk_id = prompt["chunks"][0]["chunk_id"]
            return {
                "knowledge_points": [
                    {
                        "id": "material",
                        "name": "Grounded sentinel",
                        "explanation": "Grounded by the selected file.",
                        "estimated_mastery_minutes": 180,
                        "source_chunk_ids": [chunk_id],
                    },
                    {
                        "id": "common",
                        "name": "Unconfirmed model common sentinel",
                        "explanation": "A separately reviewable model suggestion.",
                        "layer": "model_common",
                        "source_chunk_ids": [],
                    },
                ],
                "dependencies": [],
            }

    monkeypatch.setattr(
        generic_api,
        "provider_for_session",
        lambda _session: _HierarchicalProvider(Provider()),
    )
    with TestClient(app) as client:
        material_id = _material(client)
        receipt = _receipt(material_id)
        receipt.update(
            {
                "deadline": receipt["start_date"],
                "daily_task_minutes": 10,
                "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            }
        )
        assert client.put(
            "/api/active-teaching/workspaces/v1/receipt", json=receipt
        ).status_code == 200
        queued = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        assert queued.status_code == 202, queued.text
        assert client.get(
            f"/api/active-teaching/workspaces/v1/knowledge-map/jobs/{queued.json()['id']}"
        ).json()["status"] == "ready"

        preview = client.post(
            "/api/active-teaching/workspaces/v1/mastery-plans/preview", json={}
        )
        assert preview.status_code == 200, preview.text
        preview_body = preview.json()
        assert preview_body["extension_draft"]["required"] is True
        assert preview_body["extension_draft"]["requires_confirmation"] is True
        assert preview_body["extension_draft"]["added_dates"]
        assert {
            task["knowledge_point_id"] for task in preview_body["tasks"]
        } == {
            next(
                node["id"]
                for node in client.get(
                    "/api/active-teaching/workspaces/v1/knowledge-map",
                    params={"revision": "active"},
                ).json()["nodes"]
                if node["layer"] == "material" and node["node_type"] == "concept"
            )
        }
        blocked = client.post(
            f"/api/active-teaching/workspaces/v1/mastery-plans/{preview_body['preview_id']}/activate",
            json={},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "PLAN_EXTENSION_CONFIRMATION_REQUIRED"
        activated = client.post(
            f"/api/active-teaching/workspaces/v1/mastery-plans/{preview_body['preview_id']}/activate",
            json={"confirm_extension": True},
        )
        assert activated.status_code == 200, activated.text
        with active_teaching._connect() as connection:
            legacy_points = connection.execute(
                "SELECT id FROM active_knowledge_points WHERE workspace_id=?",
                ("v1",),
            ).fetchall()
            execution_tasks = connection.execute(
                """SELECT title,knowledge_point_id,concept_snapshot
                   FROM orm_plan_execution_tasks WHERE workspace_id=?""",
                ("v1",),
            ).fetchall()
        assert legacy_points == []
        assert execution_tasks
        concept_snapshots = [
            json.loads(row["concept_snapshot"]) for row in execution_tasks
        ]
        assert {item["name"] for item in concept_snapshots} == {"Grounded sentinel"}
        assert all(
            item["knowledge_point_id"] == row["knowledge_point_id"]
            for item, row in zip(concept_snapshots, execution_tasks, strict=True)
        )
        assert all(item["evidence_hash"] for item in concept_snapshots)

        opened = client.post("/api/active-teaching/workspaces/v1/sessions/start", json={})
        assert opened.status_code == 200, opened.text
        assert opened.json()["state"]["teacher_turn_v1"]["concept"]["name"] == "Grounded sentinel"
        with active_teaching._connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM active_knowledge_points WHERE workspace_id=?",
                ("v1",),
            ).fetchone()[0] == 0


def test_unincluded_material_blocks_activation_until_explicit_decision(
    migrated_api, monkeypatch
):
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "contract-model",
            "credential_ref": credential_store.store_secret("test-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True

    class Provider:
        async def json(self, messages):
            prompt = json.loads(messages[-1]["content"])
            first_chunk = prompt["chunks"][0]["chunk_id"]
            return {
                "knowledge_points": [
                    {
                        "id": "cited-only",
                        "name": "Only cited material",
                        "explanation": "The provider deliberately cites one selected source.",
                        "source_chunk_ids": [first_chunk],
                    }
                ],
                "dependencies": [],
            }

    monkeypatch.setattr(
        generic_api,
        "provider_for_session",
        lambda _session: _HierarchicalProvider(Provider()),
    )
    with TestClient(app) as client:
        material_ids: list[str] = []
        for filename, content in (
            ("cited.md", b"# Cited\nProvider evidence."),
            ("uncited.md", b"# Uncited\nReview sentinel."),
        ):
            response = client.post(
                "/api/active-teaching/workspaces/v1/materials",
                files={"file": (filename, content, "text/markdown")},
            )
            assert response.status_code == 200, response.text
            material_ids.append(response.json()["id"])
        receipt = _receipt(material_ids[0])
        receipt["material_ids"] = material_ids
        assert client.put(
            "/api/active-teaching/workspaces/v1/receipt", json=receipt
        ).status_code == 200
        queued = client.post(
            "/api/active-teaching/workspaces/v1/knowledge-map/jobs",
            json={"provider_session_id": provider["provider_session_id"]},
        )
        job = client.get(
            f"/api/active-teaching/workspaces/v1/knowledge-map/jobs/{queued.json()['id']}"
        ).json()
        assert job["status"] == "ready_with_review", job
        assert {item["disposition"] for item in job["dispositions"]} == {
            "included",
            "needs_review",
        }

        preview = client.post(
            "/api/active-teaching/workspaces/v1/mastery-plans/preview", json={}
        ).json()
        assert len(preview["activation_blockers"]) == 1
        blocked = client.post(
            f"/api/active-teaching/workspaces/v1/mastery-plans/{preview['preview_id']}/activate",
            json={},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "MATERIAL_REVIEW_REQUIRED"

        material_candidate = next(
            item
            for item in client.get(
                "/api/active-teaching/workspaces/v1/knowledge-map/candidates"
            ).json()["candidates"]
            if item["candidate_type"] == "material_disposition"
        )
        decided = client.post(
            f"/api/active-teaching/workspaces/v1/knowledge-map/candidates/{material_candidate['id']}/decision",
            json={"decision": "confirmed", "reason": "exclude uncited material from this revision"},
        )
        assert decided.status_code == 200, decided.text
        next_preview = client.post(
            "/api/active-teaching/workspaces/v1/mastery-plans/preview", json={}
        )
        assert next_preview.status_code == 200, next_preview.text
        assert next_preview.json()["activation_blockers"] == []
        activated = client.post(
            f"/api/active-teaching/workspaces/v1/mastery-plans/{next_preview.json()['preview_id']}/activate",
            json={},
        )
        assert activated.status_code == 200, activated.text
