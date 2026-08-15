from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import credential_store, db
from app.main import app
from app.provider_runtime import get_provider_session
from app import provider_service


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "v5.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def install_mock_provider(monkeypatch, status=200, content=None, model="deepseek-chat"):
    requests = []
    payload = content or {"message": "real provider response", "title": "Real route", "status": "grounded"}

    def handler(request: httpx.Request):
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "mock provider failure"})
        return httpx.Response(200, json={"id": "mock-response", "model": model, "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    real_client = provider_service.httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(provider_service.httpx, "AsyncClient", factory)
    return requests


def make_real_session(client):
    credential_store.store_secret("v5-secret", "mfcred_v5_test")
    created = client.post("/api/provider-sessions", json={"provider": "openai-compatible", "base_url": "https://mock.deepseek.test/v1", "model": "deepseek-chat", "credential_ref": "mfcred_v5_test"})
    assert created.status_code == 200, created.text
    tested = client.post(f"/api/provider-sessions/{created.json()['provider_session_id']}/test")
    assert tested.status_code == 200, tested.text
    return tested.json()


def make_goal(client, workspace_id):
    return client.post(f"/api/workspaces/{workspace_id}/goal", json={"goal_statement": "完成真实 Provider 验证成果", "motivation": "v5", "deadline": (date.today() + timedelta(days=7)).isoformat(), "minutes_per_day": 90, "days_per_week": 5, "current_level": "beginner", "expected_output": "可核验成果", "success_criteria": ["完成最终任务"]})


def prepare_real_workspace(client, session):
    workspace = client.post("/api/workspaces", json={"title": "V5 Real Workspace", "execution_mode": "real", "provider_session_id": session["provider_session_id"]}).json()
    assert client.post(f"/api/workspaces/{workspace['id']}/sources/text", json={"title": "算法资料", "text": "# 两数之和\n\n使用哈希表记录已经看到的数字，并解释复杂度与边界。", "trust_level": "reference"}).status_code == 200
    assert make_goal(client, workspace["id"]).status_code == 200
    return workspace


def test_provider_session_real_request_payload_and_response_metadata(client, monkeypatch):
    requests = install_mock_provider(monkeypatch)
    result = make_real_session(client)
    assert result["provider"] == "openai_compatible"
    assert result["base_url_host"] == "mock.deepseek.test"
    assert result["model_requested"] == "deepseek-chat"
    assert result["model_reported"] == "deepseek-chat"
    assert result["verified"] is True and result["request_id"].startswith("req_")
    request = requests[0]
    assert request.url.path.endswith("/chat/completions")
    payload = json.loads(request.content)
    assert payload["model"] == "deepseek-chat" and payload["messages"][0]["content"] == "Respond with exactly OK."
    assert request.headers["authorization"] == "Bearer v5-secret"


def test_provider_timeout_allows_long_generation_but_keeps_connect_failures_fast():
    timeout = provider_service.PROVIDER_TIMEOUT
    assert timeout.read == 90.0
    assert timeout.connect == 10.0
    assert timeout.write == 30.0
    assert timeout.pool == 10.0


def test_api_key_not_in_legacy_db_or_provider_session_response(client, monkeypatch, tmp_path):
    install_mock_provider(monkeypatch)
    result = make_real_session(client)
    assert "v5-secret" not in json.dumps(result)
    assert "v5-secret" not in db.DB_PATH.read_bytes().decode("utf-8", errors="ignore")
    orm_db = Path(__file__).resolve().parents[2] / "data" / "mentorflow_v2.db"
    if orm_db.exists():
        assert "v5-secret" not in orm_db.read_bytes().decode("utf-8", errors="ignore")


def test_real_workspace_without_verified_session_is_rejected(client):
    workspace = client.post("/api/workspaces", json={"title": "Needs provider", "execution_mode": "real"}).json()
    assert make_goal(client, workspace["id"]).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/sources/text", json={"title": "资料", "text": "# 证据\n\n真实资料"}).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={}).status_code == 428


def test_demo_workspace_explicitly_uses_fake_and_never_real_session(client, monkeypatch):
    install_mock_provider(monkeypatch)
    workspace = client.post("/api/workspaces", json={"title": "Explicit Demo", "execution_mode": "demo"}).json()
    assert workspace["execution_mode"] == "demo" and workspace["provider_name"] == "fake"
    assert client.post(f"/api/workspaces/{workspace['id']}/sources/text", json={"title": "资料", "text": "# 证据\n\nDemo evidence"}).status_code == 200
    assert make_goal(client, workspace["id"]).status_code == 200
    analysis = client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={})
    assert analysis.status_code == 200 and analysis.json()["generation_meta"]["provider"] == "fake"


def test_plan_generation_binds_session_and_exposes_meta(client, monkeypatch):
    requests = install_mock_provider(monkeypatch, content={"title": "Real plan", "message": "Plan JSON"})
    session = make_real_session(client)
    workspace = prepare_real_workspace(client, session)
    analysis = client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={"provider_session_id": session["provider_session_id"]})
    assert analysis.status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/feasibility/confirm", json={"confirmed": True}).status_code == 200
    plan = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={"provider_session_id": session["provider_session_id"]})
    assert plan.status_code == 200, plan.text
    meta = plan.json()["generation_meta"]
    assert meta["mode"] == "real" and meta["provider"] == "openai_compatible" and meta["model"] == "deepseek-chat" and meta["fallback_used"] is False
    assert any(request.url.path.endswith("/chat/completions") for request in requests)


def test_provider_failure_never_falls_back_to_fake(client, monkeypatch):
    install_mock_provider(monkeypatch, status=401)
    credential_store.store_secret("bad-key", "mfcred_bad_test")
    created = client.post("/api/provider-sessions", json={"provider": "openai-compatible", "base_url": "https://mock.deepseek.test/v1", "model": "deepseek-chat", "credential_ref": "mfcred_bad_test"}).json()
    failed = client.post(f"/api/provider-sessions/{created['provider_session_id']}/test")
    assert failed.status_code == 401 and failed.json()["error"]["code"] == "PROVIDER_AUTH_FAILED"
    workspace = client.post("/api/workspaces", json={"title": "No fallback", "execution_mode": "real"}).json()
    assert client.post(f"/api/workspaces/{workspace['id']}/sources/text", json={"title": "资料", "text": "# 证据\n\nReal"}).status_code == 200
    assert make_goal(client, workspace["id"]).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={}).status_code == 428


def test_provider_session_api_rejects_raw_keys(client):
    response = client.post(
        "/api/provider-sessions",
        json={
            "provider": "openai-compatible",
            "base_url": "https://mock.deepseek.test/v1",
            "model": "deepseek-chat",
            "api_key": "must-not-cross-api",
        },
    )
    assert response.status_code == 422
    assert "must-not-cross-api" not in response.text


def test_provider_session_has_no_time_limit_and_can_be_deleted(client, monkeypatch):
    install_mock_provider(monkeypatch)
    result = make_real_session(client)
    session = get_provider_session(result["provider_session_id"])
    assert session.expires_at is None
    current = client.get(f"/api/provider-sessions/{result['provider_session_id']}")
    assert current.status_code == 200
    assert current.json()["verified"] is True
    assert current.json()["expires_at"] is None
    assert client.delete(f"/api/provider-sessions/{result['provider_session_id']}").status_code == 200


def test_tutor_uses_same_verified_provider_session(client, monkeypatch):
    install_mock_provider(monkeypatch, content={"message": "Tutor from real model", "status": "grounded"})
    session = make_real_session(client)
    workspace = prepare_real_workspace(client, session)
    assert client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={"provider_session_id": session["provider_session_id"]}).status_code == 200
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/confirm", json={"confirmed": True})
    plan = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={"provider_session_id": session["provider_session_id"]}).json()
    lesson_id = plan["milestones"][0]["modules"][0]["lessons"][0]["id"]
    created = client.post(f"/api/workspaces/{workspace['id']}/sessions", json={"lesson_id": lesson_id, "provider_session_id": session["provider_session_id"]}).json()
    message = client.post(f"/api/sessions/{created['id']}/messages", json={"message": "解释证据", "provider_session_id": session["provider_session_id"]})
    assert message.status_code == 200
    assert message.json()["message"] == "Tutor from real model"
    assert message.json()["generation_meta"]["provider_session_id"] == session["provider_session_id"]


def test_generation_meta_is_persisted_without_key(client, monkeypatch):
    install_mock_provider(monkeypatch)
    session = make_real_session(client)
    workspace = prepare_real_workspace(client, session)
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={"provider_session_id": session["provider_session_id"]})
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/confirm", json={"confirmed": True})
    plan = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={"provider_session_id": session["provider_session_id"]}).json()
    fetched = client.get(f"/api/plans/{plan['id']}").json()
    assert fetched["generation_meta"]["provider"] == "openai_compatible"
    assert "v5-secret" not in json.dumps(fetched)


def test_error_mapping_for_rate_limit(monkeypatch):
    from app.provider_service import OpenAICompatibleProvider, ProviderError
    async def request(*args, **kwargs): return httpx.Response(429, json={"message": "do not expose"})
    provider = OpenAICompatibleProvider("https://mock.deepseek.test/v1", "deepseek-chat", "secret")
    provider.request = request
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.text([]))
    assert error.value.code == "PROVIDER_RATE_LIMITED" and error.value.status == 429 and "secret" not in str(error.value)


def test_provider_session_get_is_safe(client, monkeypatch):
    install_mock_provider(monkeypatch)
    result = make_real_session(client)
    fetched = client.get(f"/api/provider-sessions/{result['provider_session_id']}")
    assert fetched.status_code == 200 and "api_key" not in fetched.text and "v5-secret" not in fetched.text


def test_authenticated_accounts_cannot_reuse_each_others_provider_session(client, monkeypatch):
    """A guessed process-local session id must not cross the account boundary."""

    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    install_mock_provider(monkeypatch)
    credential_store.store_secret("owner-secret", "mfcred_owner_test")

    first = client.post(
        "/api/auth/register",
        json={
            "username": "OwnerAlpha",
            "display_name": "Owner Alpha",
            "password": "Correct-Horse-42",
            "claim_existing_data": True,
        },
    )
    assert first.status_code == 201, first.text
    created = client.post(
        "/api/provider-sessions",
        json={
            "provider": "openai-compatible",
            "base_url": "https://mock.deepseek.test/v1",
            "model": "deepseek-chat",
            "credential_ref": "mfcred_owner_test",
        },
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["provider_session_id"]

    client.post("/api/auth/logout")
    second = client.post(
        "/api/auth/register",
        json={
            "username": "OwnerBeta",
            "display_name": "Owner Beta",
            "password": "Correct-Horse-43",
            "claim_existing_data": False,
        },
    )
    assert second.status_code == 201, second.text

    assert client.get(f"/api/provider-sessions/{session_id}").status_code == 404
    assert client.post(f"/api/provider-sessions/{session_id}/test").status_code == 404
    assert client.delete(f"/api/provider-sessions/{session_id}").json() == {"deleted": False}
    assert client.delete("/api/provider-credentials/mfcred_owner_test").status_code == 404


def test_evaluator_requires_and_reports_real_provider(client, monkeypatch):
    install_mock_provider(monkeypatch, content={"feedback": "checked by real provider"})
    session = make_real_session(client)
    workspace = prepare_real_workspace(client, session)
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={"provider_session_id": session["provider_session_id"]})
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/confirm", json={"confirmed": True})
    plan = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={"provider_session_id": session["provider_session_id"]}).json()
    lesson_id = plan["milestones"][0]["modules"][0]["lessons"][0]["id"]
    rejected = client.post(f"/api/lessons/{lesson_id}/evaluate", json={"response": "解释证据和步骤"})
    assert rejected.status_code == 428
    accepted = client.post(f"/api/lessons/{lesson_id}/evaluate", json={"response": "解释证据和步骤", "provider_session_id": session["provider_session_id"]})
    assert accepted.status_code == 200 and accepted.json()["generation_meta"]["provider_session_id"] == session["provider_session_id"]


def test_gemini_key_is_header_only():
    from app.provider_service import GeminiProvider
    captured = {}

    async def request(*args, **kwargs):
        captured["url"] = str(args[1]); captured["headers"] = kwargs["headers"]
        return httpx.Response(200, json={"modelVersion": "gemini-mock", "candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    provider = GeminiProvider("https://gemini.test", "gemini-mock", "secret-gemini")
    provider.request = request
    assert asyncio.run(provider.text([])) == "ok"
    assert "secret-gemini" not in captured["url"] and captured["headers"]["x-goog-api-key"] == "secret-gemini"
