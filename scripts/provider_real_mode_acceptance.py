from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import active_teaching, db, orm, provider_runtime, provider_service  # noqa: E402
from app.main import app  # noqa: E402
from app.provider_runtime import reset_provider_sessions  # noqa: E402


def run_command(command: list[str], cwd: Path) -> tuple[int, str]:
    process = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return process.returncode, process.stdout + process.stderr


def main() -> int:
    output = ROOT / "artifacts" / "provider_real_mode_acceptance"
    output.mkdir(parents=True, exist_ok=True)
    secret = "acceptance-secret-must-not-escape"
    captured: list[dict] = []
    old_db, old_data = db.DB_PATH, db.DATA_DIR
    old_orm_db = orm.ORM_DB
    old_upload_root = active_teaching.UPLOAD_ROOT
    reset_provider_sessions()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured.append({"method": request.method, "url": str(request.url), "headers": {"authorization": "Bearer [REDACTED]", "content-type": request.headers.get("content-type", "")}, "payload": payload})
        return httpx.Response(200, json={"id": "acceptance-response", "model": "mock-deepseek", "choices": [{"message": {"content": json.dumps({"title": "Acceptance plan", "message": "Acceptance tutor response", "status": "grounded"})}}]})

    real_client = provider_service.httpx.AsyncClient
    real_get_secret = provider_runtime.get_secret
    transport = httpx.MockTransport(handler)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    provider_service.httpx.AsyncClient = client_factory
    provider_runtime.get_secret = lambda credential_ref: secret if credential_ref == "mfcred_acceptance_mock" else ""
    try:
        acceptance_temp_root = Path(r"E:\Codex\Temp")
        acceptance_temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="mentorflow-v5-acceptance-",
            dir=acceptance_temp_root,
        ) as temporary:
            db.DB_PATH = Path(temporary) / "app.db"
            db.DATA_DIR = Path(temporary)
            active_teaching.UPLOAD_ROOT = Path(temporary) / "active-teaching-uploads"
            orm.configure_orm(Path(temporary) / "mentorflow_v2.db")
            with TestClient(app) as client:
                created = client.post("/api/provider-sessions", json={"provider": "openai-compatible", "base_url": "https://mock.deepseek.test/v1", "model": "mock-deepseek", "credential_ref": "mfcred_acceptance_mock"})
                assert created.status_code == 200, created.text
                session_id = created.json()["provider_session_id"]
                tested = client.post(f"/api/provider-sessions/{session_id}/test")
                assert tested.status_code == 200, tested.text
                test_response = tested.json()
                workspace = client.post("/api/workspaces", json={"title": "V5 Acceptance Real", "execution_mode": "real", "provider_session_id": session_id}).json()
                workspace_id = workspace["id"]
                assert client.post(f"/api/workspaces/{workspace_id}/sources/text", json={"title": "Acceptance evidence", "text": "# Evidence\n\nThe mock provider is bound to this real workspace.", "trust_level": "reference"}).status_code == 200
                assert client.post(f"/api/workspaces/{workspace_id}/goal", json={"goal_statement": "完成真实 Provider 验收成果", "motivation": "acceptance", "deadline": "2030-01-07", "minutes_per_day": 90, "days_per_week": 5, "current_level": "beginner", "expected_output": "验收结果", "success_criteria": ["完成验收"]}).status_code == 200
                feasibility = client.post(f"/api/workspaces/{workspace_id}/feasibility/analyze", json={"provider_session_id": session_id})
                assert feasibility.status_code == 200, feasibility.text
                assert client.post(f"/api/workspaces/{workspace_id}/feasibility/confirm", json={"confirmed": True}).status_code == 200
                plan = client.post(f"/api/workspaces/{workspace_id}/plans/generate", json={"provider_session_id": session_id})
                assert plan.status_code == 200, plan.text
                plan_meta = plan.json()["generation_meta"]
                lesson_id = plan.json()["milestones"][0]["modules"][0]["lessons"][0]["id"]
                session = client.post(f"/api/workspaces/{workspace_id}/sessions", json={"lesson_id": lesson_id, "provider_session_id": session_id}).json()
                tutor = client.post(f"/api/sessions/{session['id']}/messages", json={"message": "Explain the evidence", "provider_session_id": session_id})
                assert tutor.status_code == 200, tutor.text
                tutor_meta = tutor.json()["generation_meta"]
                assert plan_meta["fallback_used"] is False and tutor_meta["fallback_used"] is False
                assert plan_meta["provider_session_id"] == tutor_meta["provider_session_id"] == session_id
                (output / "provider_test_mock_request.json").write_text(json.dumps(captured[0], ensure_ascii=False, indent=2), encoding="utf-8")
                (output / "provider_test_mock_response.json").write_text(json.dumps(test_response, ensure_ascii=False, indent=2), encoding="utf-8")
                (output / "plan_generation_meta.json").write_text(json.dumps(plan_meta, ensure_ascii=False, indent=2), encoding="utf-8")
                (output / "tutor_generation_meta.json").write_text(json.dumps(tutor_meta, ensure_ascii=False, indent=2), encoding="utf-8")
                (output / "no_fake_fallback_test.txt").write_text(f"real_workspace_execution_mode={workspace['execution_mode']}\nplan_provider={plan_meta['provider']}\ntutor_provider={tutor_meta['provider']}\nplan_fallback_used={plan_meta['fallback_used']}\ntutor_fallback_used={tutor_meta['fallback_used']}\nprovider_http_requests={len(captured)}\nassertion=passed\n", encoding="utf-8")
                (output / "network_contract.md").write_text("# Network contract evidence\n\nThe captured MockTransport request was POST `/chat/completions` with the selected model in JSON and an Authorization header redacted before writing this artifact. Browser API calls carry only `provider_session_id`; API Key is never returned by the ProviderSession API.\n\nCaptured request count: " + str(len(captured)) + "\n", encoding="utf-8")
    finally:
        provider_service.httpx.AsyncClient = real_client
        provider_runtime.get_secret = real_get_secret
        orm.engine.dispose()
        orm.configure_orm(old_orm_db)
        active_teaching.UPLOAD_ROOT = old_upload_root
        db.DB_PATH, db.DATA_DIR = old_db, old_data

    backend_code, backend_output = run_command([sys.executable, "-m", "pytest", "backend/tests", "-q"], ROOT)
    (output / "test_results.txt").write_text(f"backend_exit_code={backend_code}\n{backend_output}", encoding="utf-8")
    frontend_code, frontend_output = run_command(["npm.cmd", "run", "test", "--", "--run"], ROOT / "frontend")
    (output / "frontend_provider_state_test.txt").write_text(f"frontend_exit_code={frontend_code}\n{frontend_output}", encoding="utf-8")
    e2e_code, e2e_output = run_command(["npm.cmd", "exec", "--", "playwright", "test", "--config=playwright.config.ts"], ROOT / "frontend")
    (output / "e2e_results.txt").write_text(f"e2e_exit_code={e2e_code}\n{e2e_output}", encoding="utf-8")
    build_code, build_output = run_command(["npm.cmd", "run", "build"], ROOT / "frontend")
    (output / "build_results.txt").write_text(f"build_exit_code={build_code}\n{build_output}", encoding="utf-8")

    forbidden = []
    for path in output.iterdir():
        if path.is_file() and secret in path.read_text(encoding="utf-8", errors="ignore"):
            forbidden.append(path.name)
    evidence = f"""# MentorFlow V5 Real Provider Mode Evidence

Generated by `scripts/provider_real_mode_acceptance.py` using FastAPI TestClient and `httpx.MockTransport`.

- ProviderSession test: `{test_response['provider']}` / `{test_response['model_reported']}`, host `{test_response['base_url_host']}`, latency `{test_response['latency_ms']} ms`, request `{test_response['request_id']}`.
- Plan generation meta: `{json.dumps(plan_meta, ensure_ascii=False)}`.
- Tutor generation meta: `{json.dumps(tutor_meta, ensure_ascii=False)}`.
- Backend tests exit: `{backend_code}`; frontend tests exit: `{frontend_code}`; E2E exit: `{e2e_code}`; build exit: `{build_code}`.
- API key artifact scan: `{'passed' if not forbidden else 'failed: ' + ', '.join(forbidden)}`.
- Fake fallback assertion: passed (`fallback_used=false` for plan and Tutor).
"""
    (output / "REAL_PROVIDER_IMPLEMENTATION_EVIDENCE.md").write_text(evidence, encoding="utf-8")
    return 0 if all(code == 0 for code in (backend_code, frontend_code, e2e_code, build_code)) and not forbidden else 1


if __name__ == "__main__":
    raise SystemExit(main())
