from __future__ import annotations

import hashlib
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
from verify_rp05_staging_assets import verify as verify_rp05_assets  # noqa: E402
import build_rp05_staging_release as rp05_builder  # noqa: E402
import generate_release_manifest as manifest_module  # noqa: E402
import release_rollback as release_module  # noqa: E402


IMAGE_ROLES = ("backend", "frontend", "document-worker", "worker")


def test_rp05_oidc_loopback_bridge_is_lifecycle_and_dns_bound() -> None:
    result = verify_rp05_assets(ROOT)

    assert result["status"] == "PASS", result["failures"]
    for check in (
        "OIDC_PUBLIC_ISSUER_PRESERVED",
        "OIDC_BACKEND_TLS_TRUST_CONTRACT",
        "OIDC_LOOPBACK_PROXY_LIFECYCLE_CONTRACT",
        "OIDC_LOOPBACK_PROXY_TLS_CONTRACT",
        "OIDC_KEYCLOAK_DYNAMIC_RESOLUTION_CONTRACT",
        "EDGE_BACKEND_DYNAMIC_RESOLUTION_CONTRACT",
        "direct_access_grants_disabled",
        "only_edge_publishes_ports",
        "public_surface_http_https_only",
        "oidc_loopback_proxy_release_asset",
    ):
        assert result["checks"][check] == "PASS"


def test_rp05_release_includes_the_oidc_loopback_proxy_asset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    images = tmp_path / "images.json"
    images.write_text(
        json.dumps(
            {
                "schema_version": "mentorflow.rp05-runtime-images.v1",
                "images": {
                    key: f"registry.example/{key.casefold()}@sha256:{'1' * 64}"
                    for key in rp05_builder.IMAGE_KEYS
                },
            }
        ),
        encoding="utf-8",
    )
    public_env = tmp_path / "public.env"
    public_env.write_text(
        "\n".join(
            (
                "MENTORFLOW_COMPOSE_PROJECT=mentorflow-r52",
                "MENTORFLOW_PUBLIC_ORIGIN=https://mentorflow.example.test",
                "MENTORFLOW_PUBLIC_HOSTNAME=mentorflow.example.test",
                f"MENTORFLOW_WORKER_RUNTIME_FACTORY={rp05_builder.PRODUCTION_FACTORY}",
                *(
                    f"{key}={value}"
                    for key, value in sorted(rp05_builder.PRODUCTION_HANDLERS.items())
                ),
                "MENTORFLOW_WORKER_CONCURRENCY=4",
                "",
            )
        ),
        encoding="utf-8",
    )

    git_values = iter(("", "codex/e1-r52-fixture", "a" * 40, "b" * 40))
    monkeypatch.setattr(rp05_builder, "_git", lambda *_args: next(git_values))
    output = tmp_path / "release"
    manifest = rp05_builder.build(ROOT, images, public_env, output)

    proxy = output / "oidc-loopback-proxy.conf"
    assert proxy.is_file()
    assert manifest["files"][proxy.name] == _sha256(proxy)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _final_release_fixture(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "release-repo"
    (root / ".github").mkdir(parents=True)
    (root / "backend" / "alembic" / "versions").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    _write_json(
        root / ".github" / "enterprise-context.lock.json",
        {"contract_version": "enterprise-context.v1", "contract_bundle_sha256": _digest("contracts")},
    )
    (root / "backend" / "alembic" / "versions" / "0001_initial.py").write_text(
        'revision = "0001"\ndown_revision = None\n', encoding="utf-8"
    )
    (root / "backend" / "requirements.lock.txt").write_text("fastapi==1.0.0\n", encoding="utf-8")
    _write_json(root / "frontend" / "package-lock.json", {"lockfileVersion": 3, "packages": {}})
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "release-tests@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Release Tests"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=root, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    source_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()

    artifacts = root / "artifacts" / "enterprise" / "container"
    base_images = {
        role: f"registry.example/base/{role}@sha256:{_digest('base-' + role)}"
        for role in ("node", "python", "nginx")
    }
    base_path = artifacts / "base-image-digests.json"
    _write_json(
        base_path,
        {
            "schema": "mentorflow.base-image-provenance.v1",
            "resolver": manifest_module.FINAL_DIGEST_SOURCE,
            "images": base_images,
        },
    )
    images = []
    for role in IMAGE_ROLES:
        digest = _digest("application-" + role)
        build = artifacts / f"{role}-build-metadata.json"
        archive = artifacts / f"{role}-image.tar.gz"
        sbom = artifacts / f"{role}-image-sbom.cdx.json"
        security = artifacts / f"{role}-trivy.sarif"
        _write_json(build, {"containerimage.digest": "sha256:" + digest})
        archive.write_bytes(("archive:" + role).encode("utf-8"))
        _write_json(
            sbom,
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [{"type": "library", "name": "fixture", "version": "1"}],
            },
        )
        _write_json(
            security,
            {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Trivy"}}, "results": []}]},
        )
        images.append(
            {
                "role": role,
                "repository_digest": f"registry.example/mentorflow-{role}@sha256:{digest}",
                "digest_source": manifest_module.FINAL_DIGEST_SOURCE,
                "artifacts": {
                    name: {"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)}
                    for name, path in {
                        "build_metadata": build,
                        "image_archive": archive,
                        "sbom": sbom,
                        "security_report": security,
                    }.items()
                },
            }
        )
    evidence_path = artifacts / "final-image-evidence.json"
    evidence = {
        "schema": "mentorflow.enterprise-final-image-evidence.v1",
        "source_commit": commit,
        "source_tree": source_tree,
        "digest_source": manifest_module.FINAL_DIGEST_SOURCE,
        "base_images": base_images,
        "base_provenance": {"path": base_path.relative_to(root).as_posix(), "sha256": _sha256(base_path)},
        "images": images,
    }
    _write_json(evidence_path, evidence)
    return {
        "root": root,
        "commit": commit,
        "source_tree": source_tree,
        "evidence": evidence,
        "evidence_path": evidence_path,
        "manifest": artifacts / "final-release-manifest.json",
        "digest_lock": artifacts / "final-image-digest-lock.json",
    }


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
        "container_images": 4,
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


@pytest.mark.parametrize(
    ("relative_path", "before", "after", "message"),
    [
        (".github/workflows/enterprise-main-full.yml", "  workflow_dispatch:",
         "  workflow_dispatch:\n  push:", "manual-only"),
        (".github/dependabot.yml", "updates: []",
         "updates: [{package-ecosystem: pip}]", "disabled remote update"),
        (".github/workflows/container-release.yml",
         "runs-on: [self-hosted, Linux, X64, mentorflow-e1, turkey-ci]",
         "runs-on: ubuntu-latest", "approved self-hosted runner"),
    ],
)
def test_release_checks_reject_remote_execution_contract_drift(
    monkeypatch: pytest.MonkeyPatch, relative_path: str, before: str,
    after: str, message: str,
) -> None:
    target = ROOT / relative_path
    original = Path.read_text
    assert before in original(target, encoding="utf-8")

    def read_text(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        return text.replace(before, after) if path == target else text

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(RuntimeError, match=message):
        verify_assets(ROOT)


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
            "--worker-image",
            "registry.example/mentorflow-durable-worker:" + "3" * 40,
            "--worker-semantic-tag",
            "registry.example/mentorflow-durable-worker:0.1.0",
            "--worker-sbom-ref",
            "durable-worker-sbom.cdx.json",
            "--worker-security-report-ref",
            "durable-worker-trivy.sarif",
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
    assert len(manifest_payload["images"]) == 4
    assert manifest_payload["asvs_matrix_version"] == "OWASP-ASVS-5.0.0"
    assert manifest_payload["pending_staging_gates"] == ["REAL_CONTAINER"]
    assert manifest_payload["real_provider_calls"] == 0


def test_final_release_manifest_and_digest_lock_bind_four_real_digest_evidence(tmp_path: Path) -> None:
    fixture = _final_release_fixture(tmp_path)
    manifest, digest_lock = manifest_module.build_final_release_package(
        root=fixture["root"],
        commit=fixture["commit"],
        source_tree=fixture["source_tree"],
        app_version="0.1.0",
        evidence_path=fixture["evidence_path"],
        output=fixture["manifest"],
        digest_lock_output=fixture["digest_lock"],
        published=True,
    )
    assert manifest["schema"] == "mentorflow.enterprise-final-release-manifest.v1"
    assert manifest["status"] == "FINAL_RELEASE_VERIFIED"
    assert manifest["source_commit"] == fixture["commit"]
    assert manifest["source_tree"] == fixture["source_tree"]
    assert manifest["hash_byte_domains"] == {
        "source_locks": "sha256:git-blob-bytes-v1",
        "retained_artifacts": "sha256:artifact-bytes-v1",
        "structured_content": "sha256:canonical-json-v1",
    }
    assert [item["role"] for item in manifest["images"]] == list(IMAGE_ROLES)
    assert set(digest_lock["images"]) == set(IMAGE_ROLES)
    assert digest_lock["source_commit"] == fixture["commit"]
    assert digest_lock["source_tree"] == fixture["source_tree"]
    assert fixture["manifest"].is_file() and fixture["digest_lock"].is_file()
    manifest_content_hash = manifest.pop("manifest_content_sha256")
    assert manifest_module.canonical_json_sha256(manifest) == manifest_content_hash
    manifest["manifest_content_sha256"] = manifest_content_hash
    digest_lock_content_hash = digest_lock.pop("digest_lock_content_sha256")
    assert manifest_module.canonical_json_sha256(digest_lock) == digest_lock_content_hash
    digest_lock["digest_lock_content_sha256"] = digest_lock_content_hash


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("artifact-hash", "artifact hash mismatch"),
        ("mutable-tag", "digest-only repository@sha256"),
        ("synthetic-digest", "obviously synthetic digest"),
        ("build-digest", "not bound to retained Buildx metadata"),
        ("digest-source", "digest source is not the registry resolver"),
        ("base-provenance", "does not exactly bind the resolved base images"),
        ("missing-role", "exactly four application images"),
        ("source-tree", "source commit/tree mismatch"),
    ],
)
def test_final_release_manifest_fails_closed_on_ambiguous_or_tampered_evidence(
    tmp_path: Path, case: str, message: str
) -> None:
    fixture = _final_release_fixture(tmp_path)
    evidence = fixture["evidence"]
    if case == "artifact-hash":
        evidence["images"][0]["artifacts"]["sbom"]["sha256"] = "0" * 64
    elif case == "mutable-tag":
        evidence["images"][0]["repository_digest"] = "registry.example/mentorflow-backend:" + "a" * 40
    elif case == "synthetic-digest":
        evidence["images"][0]["repository_digest"] = "registry.example/mentorflow-backend@sha256:" + "a" * 64
    elif case == "build-digest":
        evidence["images"][0]["repository_digest"] = (
            "registry.example/mentorflow-backend@sha256:" + _digest("different-build")
        )
    elif case == "digest-source":
        evidence["digest_source"] = "docker inspect local tag"
    elif case == "base-provenance":
        evidence["base_images"]["node"] = (
            "registry.example/base/node@sha256:" + _digest("different-node-base")
        )
    elif case == "missing-role":
        evidence["images"].pop()
    else:
        evidence["source_tree"] = "0" * 40
    _write_json(fixture["evidence_path"], evidence)
    with pytest.raises(ValueError, match=message):
        manifest_module.build_final_release_package(
            root=fixture["root"],
            commit=fixture["commit"],
            source_tree=fixture["source_tree"],
            app_version="0.1.0",
            evidence_path=fixture["evidence_path"],
            output=fixture["manifest"],
            digest_lock_output=fixture["digest_lock"],
            published=True,
        )


def test_source_lock_hash_uses_git_blob_bytes_not_checkout_line_endings(tmp_path: Path) -> None:
    fixture = _final_release_fixture(tmp_path)
    root = fixture["root"]
    commit = fixture["commit"]
    lock_path = root / "backend" / "requirements.lock.txt"
    expected = manifest_module._git_blob_sha256(root, commit, "backend/requirements.lock.txt")
    lock_path.write_bytes(lock_path.read_bytes().replace(b"\n", b"\r\n"))
    assert manifest_module._git_blob_sha256(root, commit, "backend/requirements.lock.txt") == expected
    assert hashlib.sha256(lock_path.read_bytes()).hexdigest() != expected


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
