from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "enterprise"
sys.path.insert(0, str(SCRIPTS))

from verify_enterprise_context import verify as verify_context  # noqa: E402
from verify_release_assets import verify as verify_assets  # noqa: E402
import release_rollback as release_module  # noqa: E402


def test_release_assets_are_static_provider_zero_and_lease_gated() -> None:
    result = verify_assets(ROOT)
    sbom = json.loads(
        (ROOT / "reports" / "enterprise" / "machine" / "lockfile-sbom.cdx.json").read_text(
            encoding="utf-8"
        )
    )
    component_count = len(sbom["components"])
    assert component_count > 100
    assert result["sbom_components"] == component_count
    assert result["dependency_risk_components"] == component_count
    stable_result = {
        key: value
        for key, value in result.items()
        if key not in {"sbom_components", "dependency_risk_components"}
    }
    assert stable_result == {
        "status": "PASS",
        "workflows": 7,
        "ci_lanes": 2,
        "container_images": 3,
        "evidence_documents": 18,
        "asvs_controls": 345,
        "threat_classes": 16,
        "load_profiles": 6,
        "tenant_organizations": 3,
        "tenant_users": 21,
        "tenant_resource_types": 7,
        "readiness_domains": 12,
        "readiness_snapshot": 1,
        "environment_not_run_facts": 6,
        "pending_machine_reports": 6,
        "security_waivers": 0,
        "provider_calls": 0,
        "shared_contract_paths_owned": 0,
        "scanner_installations": 0,
    }


def test_frozen_context_gate_fails_closed_then_accepts_only_exact_bundle(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    shutil.copy2(ROOT / ".github" / "enterprise-context.lock.json", tmp_path / ".github")
    with pytest.raises(RuntimeError, match="UPSTREAM_CONTEXT_MISSING"):
        verify_context(tmp_path)

    manifest = tmp_path / "backend" / "app" / "enterprise" / "contracts_v1" / "contract_hash_manifest.json"
    manifest.parent.mkdir(parents=True)
    lock = json.loads((tmp_path / ".github" / "enterprise-context.lock.json").read_text(encoding="utf-8"))
    manifest.write_text(
        json.dumps({"contract_version": lock["contract_version"], "hashes": {"bundle": lock["contract_bundle_sha256"]}}),
        encoding="utf-8",
    )
    test_path = tmp_path / lock["contract_test"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("# frozen upstream test marker\n", encoding="utf-8")
    assert verify_context(tmp_path)["bundle"] == lock["contract_bundle_sha256"]

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["hashes"]["bundle"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="CONTEXT_BUNDLE_HASH_MISMATCH"):
        verify_context(tmp_path)


def test_deterministic_sbom_and_release_dry_run(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.cdx.json"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_sbom.py"), "--output", str(sbom)],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_sbom.py"), "--check", "--output", str(sbom)],
        cwd=ROOT,
        check=True,
    )
    payload = json.loads(sbom.read_text(encoding="utf-8"))
    assert payload["bomFormat"] == "CycloneDX"
    assert payload["specVersion"] == "1.6"
    assert len(payload["components"]) > 100

    receipt = tmp_path / "release-receipt.json"
    command = [
        sys.executable,
        str(SCRIPTS / "release_rollback.py"),
        "--compose-file",
        "deploy/enterprise/compose.staging.yml",
        "--backend-image",
        "registry.example/mentorflow:" + "1" * 40,
        "--frontend-image",
        "registry.example/mentorflow-frontend:" + "1" * 40,
        "--document-worker-image",
        "registry.example/mentorflow-worker:" + "1" * 40,
        "--backend-rollback-image",
        "registry.example/mentorflow@sha256:" + "2" * 64,
        "--frontend-rollback-image",
        "registry.example/mentorflow-frontend@sha256:" + "2" * 64,
        "--document-worker-rollback-image",
        "registry.example/mentorflow-worker@sha256:" + "2" * 64,
        "--backend-promotion-image",
        "registry.example/mentorflow:staging",
        "--frontend-promotion-image",
        "registry.example/mentorflow-frontend:staging",
        "--document-worker-promotion-image",
        "registry.example/mentorflow-worker:staging",
        "--base-url",
        "http://127.0.0.1:18107",
        "--receipt",
        str(receipt),
        "--dry-run",
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    release_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert release_payload["status"] == "released"
    assert release_payload["schema"] == "mentorflow.enterprise-release-receipt.v2"
    assert release_payload["phases"][-1] == {"name": "promote_three_images", "status": "passed"}

    manifest = tmp_path / "release-manifest.json"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_release_manifest.py"),
            "--commit",
            "3" * 40,
            "--source-tree",
            "4" * 40,
            "--app-version",
            "0.1.0",
            "--status",
            "STAGING_EXECUTION_PENDING",
            "--backend-image",
            "registry.example/mentorflow:" + "3" * 40,
            "--backend-semantic-tag",
            "registry.example/mentorflow:0.1.0",
            "--backend-sbom-ref",
            "backend-sbom.cdx.json",
            "--backend-security-report-ref",
            "backend-trivy.sarif",
            "--frontend-image",
            "registry.example/mentorflow-frontend:" + "3" * 40,
            "--frontend-semantic-tag",
            "registry.example/mentorflow-frontend:0.1.0",
            "--frontend-sbom-ref",
            "frontend-sbom.cdx.json",
            "--frontend-security-report-ref",
            "frontend-trivy.sarif",
            "--document-worker-image",
            "registry.example/mentorflow-worker:" + "3" * 40,
            "--document-worker-semantic-tag",
            "registry.example/mentorflow-worker:0.1.0",
            "--document-worker-sbom-ref",
            "worker-sbom.cdx.json",
            "--document-worker-security-report-ref",
            "worker-trivy.sarif",
            "--sbom-ref",
            "lockfile-sbom.cdx.json",
            "--security-report-ref",
            "security-gate.json",
            "--load-report-ref",
            "load-test.pending.json",
            "--qa-evidence",
            "focused-pytest",
            "--known-blocker",
            "Docker unavailable",
            "--pending-gate",
            "REAL_CONTAINER",
            "--output",
            str(manifest),
        ],
        cwd=ROOT,
        check=True,
    )
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["source_commit"] == "3" * 40
    assert manifest_payload["source_tree"] == "4" * 40
    assert manifest_payload["migration_version"]
    assert manifest_payload["schema"] == "mentorflow.enterprise-release-manifest.v2"
    assert len(manifest_payload["images"]) == 3
    assert manifest_payload["asvs_matrix_version"] == "OWASP-ASVS-5.0.0"
    assert manifest_payload["pending_staging_gates"] == ["REAL_CONTAINER"]
    assert manifest_payload["real_provider_calls"] == 0


def test_candidate_failure_restores_tags_then_health_and_smoke_verifies_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = tmp_path / "rollback-receipt.json"
    health_calls: list[str] = []
    smoke_calls: list[str] = []
    promotion_sources: list[dict[str, str]] = []

    def fake_health(base_url: str, **_kwargs: object) -> None:
        health_calls.append(base_url)
        if len(health_calls) == 1:
            raise RuntimeError("candidate health failed")

    def fake_smoke(base_url: str, _env: dict[str, str], **_kwargs: object) -> None:
        smoke_calls.append(base_url)

    def fake_promote(
        candidates: dict[str, str], _promotions: dict[str, str], _env: dict[str, str], **_kwargs: object
    ) -> None:
        promotion_sources.append(candidates)

    monkeypatch.setattr(release_module, "_health", fake_health)
    monkeypatch.setattr(release_module, "_smoke", fake_smoke)
    monkeypatch.setattr(release_module, "_promote", fake_promote)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_rollback.py",
            "--compose-file", "deploy/enterprise/compose.staging.yml",
            "--backend-image", "registry.example/backend:" + "1" * 40,
            "--frontend-image", "registry.example/frontend:" + "1" * 40,
            "--document-worker-image", "registry.example/worker:" + "1" * 40,
            "--backend-rollback-image", "registry.example/backend:" + "2" * 40,
            "--frontend-rollback-image", "registry.example/frontend:" + "2" * 40,
            "--document-worker-rollback-image", "registry.example/worker:" + "2" * 40,
            "--backend-promotion-image", "registry.example/backend:staging",
            "--frontend-promotion-image", "registry.example/frontend:staging",
            "--document-worker-promotion-image", "registry.example/worker:staging",
            "--base-url", "http://127.0.0.1:18107",
            "--receipt", str(receipt),
            "--dry-run",
        ],
    )
    with pytest.raises(RuntimeError, match="candidate health failed"):
        release_module.main()

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "rolled_back"
    assert [phase["name"] for phase in payload["phases"]][-4:] == [
        "rollback_promotion_tags",
        "rollback_deploy",
        "rollback_health",
        "post_rollback_smoke",
    ]
    assert health_calls == ["http://127.0.0.1:18107", "http://127.0.0.1:18107"]
    assert smoke_calls == ["http://127.0.0.1:18107"]
    assert promotion_sources == [payload["rollback_images"]]
