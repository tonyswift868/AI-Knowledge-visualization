from __future__ import annotations

import importlib.util
import sqlite3

from app import db, orm


def test_single_user_runtime_has_no_account_routes_and_legacy_switch_is_inert(client, monkeypatch):
    route_paths = {getattr(route, "path", "") for route in client.app.routes}
    assert not any(path.startswith("/api/auth") for path in route_paths)

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert not any(path.startswith("/api/auth") for path in openapi.json()["paths"])

    for value in ("0", "1", "true", "false"):
        monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", value)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/status").status_code == 404
        assert client.post("/api/auth/register", json={"username": "ignored"}).status_code in {404, 405}


def test_old_schema_workspace_rows_remain_visible_without_account_claim_or_filter(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    with db.connection() as connection:
        connection.execute(
            "INSERT INTO workspaces "
            "(id,title,description,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            ("old-schema-workspace", "历史空间", "保留", "active", db.now(), db.now()),
        )

    response = client.get("/api/workspaces")
    assert response.status_code == 200
    assert any(item["id"] == "old-schema-workspace" for item in response.json())
    assert client.get("/api/workspaces/old-schema-workspace").status_code == 200


def test_historical_account_tables_remain_for_schema_and_data_compatibility():
    with sqlite3.connect(orm.ORM_DB) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "orm_local_users",
        "orm_local_sessions",
        "orm_workspace_ownership",
        "orm_local_phone_challenges",
    } <= tables


def test_provider_session_handle_is_local_and_owner_nullable(client):
    created = client.post("/api/provider-sessions", json={"provider": "fake"})
    assert created.status_code == 200, created.text
    session_id = created.json()["provider_session_id"]

    fetched = client.get(f"/api/provider-sessions/{session_id}")
    assert fetched.status_code == 200
    assert fetched.json()["provider_session_id"] == session_id
    assert fetched.json().get("owner_user_id") is None

    from app.provider_runtime import get_provider_session

    session = get_provider_session(session_id)
    assert session is not None
    assert session.owner_user_id is None


def test_retired_auth_modules_are_not_importable():
    assert importlib.util.find_spec("app.local_auth") is None
    assert importlib.util.find_spec("app.local_auth_api") is None
    assert importlib.util.find_spec("desktop.auth_window") is None
