from __future__ import annotations

import json
from pathlib import Path

from app.governance.evidence import generate_policy_evidence


APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def test_governance_evidence_generator_emits_required_reports_and_qa_templates(tmp_path) -> None:
    output = tmp_path / "evidence"
    paths = generate_policy_evidence(
        output,
        app_root=APP_ROOT,
        temp_root=tmp_path / "runtime",
    )
    assert {path.name for path in paths} == {
        "audit-coverage-report.json",
        "authorization-negative-report.json",
        "governance-e2e-report.json",
        "policy-integration-manifest.json",
        "protected-surface-manifest.json",
        "secret-access-report.json",
        "staging-qa-pack.json",
    }
    payloads = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    assert payloads["protected-surface-manifest.json"]["operation_count"] == 80
    assert payloads["audit-coverage-report.json"]["verification"]["complete"] is True
    assert payloads["authorization-negative-report.json"]["case_count"] == 404
    assert payloads["secret-access-report.json"]["complete"] is True
    assert payloads["secret-access-report.json"]["delivery_blocking"] is False
    assert payloads["secret-access-report.json"]["scope_resolution"] == "NONE"
    assert payloads["secret-access-report.json"]["violations"] == []
    assert payloads["secret-access-report.json"]["compatibility_boundaries"] == [
        {
            "class_name": "LegacyLocalVroidHubCompatibilityAdapter",
            "function_name": "credential_snapshot",
            "line": 109,
            "path": "teacher_avatar_registry.py",
            "symbol": "VROID_HUB_CLIENT_SECRET",
        }
    ]
    assert payloads["governance-e2e-report.json"]["complete"] is True
    assert payloads["staging-qa-pack.json"]["status"] == "TEMPLATES_ONLY_NOT_PUBLISHED"
    assert payloads["staging-qa-pack.json"]["published_requests"] == 0
