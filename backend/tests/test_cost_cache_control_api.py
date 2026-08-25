from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.cost.cache_observatory import CacheMetricsStore
from app.cost.durable_execution import SQLiteExecutionStore
from app.cost_cache_control_api import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _live_databases(tmp_path: Path) -> tuple[Path, Path]:
    execution_path = tmp_path / "execution.db"
    cache_path = tmp_path / "cache.db"
    execution = SQLiteExecutionStore(execution_path)
    execution.create_session(
        session_id="session-api",
        run_id="run-api",
        currency="USD",
        user_max_budget=Decimal("2.00"),
        warning_threshold=Decimal("1.20"),
        hard_stop_threshold=Decimal("1.80"),
        job_start_estimate=None,
    )
    execution.close()
    cache = CacheMetricsStore(cache_path)
    cache.close()
    return execution_path, cache_path


def test_demo_endpoint_is_explicit_synthetic_and_provider_free(monkeypatch) -> None:
    monkeypatch.delenv("MENTORFLOW_DURABLE_EXECUTION_DB", raising=False)
    monkeypatch.delenv("MENTORFLOW_CACHE_METRICS_DB", raising=False)

    with _client() as client:
        response = client.get("/api/cost-cache-control-center?demo=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "DEMO"
    assert payload["demo_fixture_id"] == "cost-cache-control-center-demo-v1"
    assert payload["demo_metadata"] == {
        "synthetic": True,
        "real_provider_calls": 0,
    }
    assert len(payload["cache"]["layers"]) == 6
    assert payload["cache"]["combined_cache_hit_percent"] is None
    assert payload["budget"]["execution_authority"] is False


def test_live_endpoint_requires_explicit_existing_databases(monkeypatch) -> None:
    secret_path = "E:/private/sk-secret-do-not-return.db"
    monkeypatch.setenv("MENTORFLOW_DURABLE_EXECUTION_DB", secret_path)
    monkeypatch.delenv("MENTORFLOW_CACHE_METRICS_DB", raising=False)

    with _client() as client:
        response = client.get("/api/cost-cache-control-center")

    assert response.status_code == 503
    body = response.json()
    assert body["detail"]["code"] == "COST_CACHE_METRICS_UNAVAILABLE"
    assert secret_path.casefold() not in response.text.casefold()


def test_live_endpoint_reads_persisted_stores_without_secret_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    execution_path, cache_path = _live_databases(tmp_path)
    monkeypatch.setenv("MENTORFLOW_DURABLE_EXECUTION_DB", str(execution_path))
    monkeypatch.setenv("MENTORFLOW_CACHE_METRICS_DB", str(cache_path))

    with _client() as client:
        response = client.get("/api/cost-cache-control-center")
        manifest_response = client.get(
            "/api/cost-cache-control-center/integration-manifest"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "LIVE"
    assert payload["current_session"]["session_id"] == "session-api"
    assert payload["read_only"] is True
    assert payload["budget"]["execution_authority"] is False
    serialized = json.dumps(payload).casefold()
    for forbidden in (
        "raw_prompt",
        "api_key",
        "secret_headers",
        "private_provider_credentials",
    ):
        assert forbidden not in serialized

    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["delivery_id"] == (
        "mentorflow-live-demo-03-cost-cache-control-center-v1"
    )
    assert manifest["demo_real_provider_calls"] == 0
    assert manifest["budget_execution_authority"] is False


def test_live_endpoint_returns_sanitized_not_found(monkeypatch, tmp_path: Path) -> None:
    execution_path, cache_path = _live_databases(tmp_path)
    monkeypatch.setenv("MENTORFLOW_DURABLE_EXECUTION_DB", str(execution_path))
    monkeypatch.setenv("MENTORFLOW_CACHE_METRICS_DB", str(cache_path))

    with _client() as client:
        response = client.get(
            "/api/cost-cache-control-center?session_id=missing-session"
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "COST_CACHE_SESSION_NOT_FOUND"
    assert str(execution_path).casefold() not in response.text.casefold()
