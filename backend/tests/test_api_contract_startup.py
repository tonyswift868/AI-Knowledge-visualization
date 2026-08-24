from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "startup-contract.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "0")
    monkeypatch.setenv("MENTORFLOW_LOCAL_DATA_MODE", "1")
    with TestClient(app) as test_client:
        yield test_client


def _goal_payload() -> dict:
    return {
        "goal_statement": "在截止日期前完成可验证学习成果",
        "motivation": "持续成长",
        "deadline": (date.today() + timedelta(days=14)).isoformat(),
        "minutes_per_day": 30,
        "days_per_week": 5,
        "current_level": "beginner",
        "expected_output": "一份成果清单",
        "success_criteria": ["完成最终任务"],
    }


def test_startup_success_bodies_retain_legacy_shapes(client):
    created_response = client.post(
        "/api/workspaces",
        json={"title": "启动契约", "description": "characterization"},
    )
    assert created_response.status_code == 200
    workspace = created_response.json()
    workspace_keys = {
        "id",
        "title",
        "description",
        "status",
        "character_id",
        "allow_external_knowledge",
        "persona_prompt",
        "execution_mode",
        "provider_name",
        "provider_model",
        "created_at",
        "updated_at",
    }
    assert set(workspace) == workspace_keys

    listed_response = client.get("/api/workspaces")
    assert listed_response.status_code == 200
    assert listed_response.json() == [workspace]

    detail_response = client.get(f"/api/workspaces/{workspace['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert set(detail) == workspace_keys | {"sources", "goal", "feasibility", "plans"}
    assert detail["sources"] == []
    assert detail["goal"] is None
    assert detail["feasibility"] is None
    assert detail["plans"] == []

    source_response = client.post(
        f"/api/workspaces/{workspace['id']}/sources/text",
        json={"title": "契约资料", "text": "足够长的资料内容，用于生成一个可枚举的 chunk。"},
    )
    assert source_response.status_code == 200
    sources_response = client.get(f"/api/workspaces/{workspace['id']}/sources")
    assert sources_response.status_code == 200
    source = sources_response.json()[0]
    assert "text" not in source
    assert source["chunk_count"] >= 1
    assert set(source) == {
        "id",
        "workspace_id",
        "title",
        "source_type",
        "original_filename",
        "original_url",
        "trust_level",
        "content_hash",
        "status",
        "parse_error",
        "page_count",
        "archive_preview_id",
        "archive_relative_path",
        "parent_archive_name",
        "language",
        "created_at",
        "updated_at",
        "chunk_count",
    }

    goal_response = client.post(
        f"/api/workspaces/{workspace['id']}/goal", json=_goal_payload()
    )
    assert goal_response.status_code == 200
    goal = goal_response.json()
    assert set(goal) == {"id", "workspace_id", "payload", "confirmed", "created_at", "updated_at"}
    assert goal["payload"]["goal_statement"] == _goal_payload()["goal_statement"]
    assert goal["confirmed"] == 0


def test_startup_error_status_and_envelopes_are_unchanged(client):
    missing = client.get("/api/workspaces/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"

    invalid_body = client.post("/api/workspaces", json={"title": ""})
    assert invalid_body.status_code == 422
    assert invalid_body.json()["error"]["code"] == "VALIDATION_ERROR"

    provider_failure = client.post(
        "/api/workspaces",
        json={
            "title": "真实空间",
            "execution_mode": "real",
            "provider_session_id": "missing-session",
        },
    )
    assert provider_failure.status_code == 428
    assert provider_failure.json()["error"]["code"] == "PROVIDER_SESSION_REQUIRED"


def test_startup_response_models_keep_unknown_fields(client, monkeypatch):
    from app import main as main_module

    legacy_row = {
        "id": "ws-legacy",
        "title": "旧字段",
        "legacy_flag": {"source": "old-runtime"},
    }
    original_rows = main_module.db.rows

    def rows(table, *args, **kwargs):
        if table == "workspaces":
            return [legacy_row]
        return original_rows(table, *args, **kwargs)

    monkeypatch.setattr(main_module.db, "rows", rows)
    response = client.get("/api/workspaces")
    assert response.status_code == 200
    assert response.json() == [legacy_row]


def test_startup_openapi_exposes_response_schemas_without_route_changes(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    expected = {
        ("/api/workspaces", "get"),
        ("/api/workspaces", "post"),
        ("/api/workspaces/{workspace_id}", "get"),
        ("/api/workspaces/{workspace_id}/sources", "get"),
        ("/api/workspaces/{workspace_id}/goal", "post"),
    }
    assert all(method in paths[path] for path, method in expected)

    assert paths["/api/workspaces"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["items"]["$ref"].endswith("/WorkspaceOut")
    assert paths["/api/workspaces"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/WorkspaceOut")
    assert paths["/api/workspaces/{workspace_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/WorkspaceDetailOut")
    assert paths["/api/workspaces/{workspace_id}/sources"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["items"]["$ref"].endswith("/SourceSummaryOut")
    assert paths["/api/workspaces/{workspace_id}/goal"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/GoalOut")
