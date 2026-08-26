from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "enterprise"
STAGING = ROOT / "deploy" / "enterprise" / "staging"
sys.path.insert(0, str(SCRIPTS))

from build_staging_release_package import build_package  # noqa: E402
from collect_staging_evidence import collect_bundle  # noqa: E402
from staging_contracts import (  # noqa: E402
    EVIDENCE_CATEGORIES,
    ContractError,
    canonical_sha256,
    load_json_object,
    validate_document_hook,
    validate_host_config,
    validate_resource_budget,
    validate_runtime_images,
)
from staging_host_preflight import evaluate_preflight  # noqa: E402
from staging_release_orchestrator import (  # noqa: E402
    CommandRunner,
    _run_load,
    deploy_release,
    load_package,
    rollback_drill,
)
from staging_security_runner import evaluate_security  # noqa: E402
from verify_staging_orchestrator import verify as verify_orchestrator  # noqa: E402


def _image(name: str, character: str) -> str:
    return f"registry.example/mentorflow/{name}@sha256:{character * 64}"


def _manifest(path: Path, *, commit: str = "1" * 40, migration: str = "0030_enterprise") -> dict[str, object]:
    context_lock = json.loads((ROOT / ".github" / "enterprise-context.lock.json").read_text(encoding="utf-8"))
    payload: dict[str, object] = {
        "schema": "mentorflow.enterprise-release-manifest.v2",
        "status": "OFFLINE_VERIFIED",
        "source_commit": commit,
        "source_tree": "2" * 40,
        "enterprise_contract_version": "enterprise-context.v1",
        "enterprise_contract_bundle_sha256": context_lock["contract_bundle_sha256"],
        "migration_version": [migration],
        "image_tags": {
            "backend": _image("backend", "a"),
            "frontend": _image("frontend", "b"),
            "document-worker": _image("document-worker", "c"),
        },
        "images": [],
        "sbom_refs": ["backend.cdx.json", "frontend.cdx.json", "worker.cdx.json"],
        "security_report_refs": ["security.json"],
        "asvs_matrix_version": "OWASP-ASVS-5.0.0",
        "load_report_refs": ["load.json"],
        "qa_report_refs": ["qa.json"],
        "known_blockers": [],
        "pending_staging_gates": [],
        "provider_mode": "fake",
        "real_provider_calls": 0,
    }
    payload["manifest_content_sha256"] = canonical_sha256(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _runtime_images(path: Path) -> dict[str, object]:
    names = (
        "postgres", "keycloak", "worker", "otel-collector", "k6",
        "object-store", "prometheus", "grafana", "testing-services",
    )
    payload = {
        "schema": "mentorflow.staging-runtime-images.v1",
        "status": "RESOLVED",
        "images": {name: _image(name, hex(index + 1)[-1]) for index, name in enumerate(names)},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _preflight(path: Path, *, mode: str = "OFFLINE_FIXTURE") -> None:
    payload = {
        "schema": "mentorflow.staging-host-preflight-receipt.v1",
        "status": "PASS",
        "profile": "minimal",
        "mode": mode,
        "hard_failures": [],
        "checks": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _build_package(tmp_path: Path, *, name: str = "package", commit: str = "1" * 40, migration: str = "0030_enterprise") -> Path:
    manifest = tmp_path / f"{name}-manifest.json"
    runtime = tmp_path / f"{name}-runtime.json"
    _manifest(manifest, commit=commit, migration=migration)
    _runtime_images(runtime)
    output = tmp_path / name
    build_package(
        manifest_path=manifest,
        host_config_path=STAGING / "host.racknerd.example.json",
        budget_path=STAGING / "resource-budget.initial.json",
        runtime_images_path=runtime,
        smoke_path=STAGING / "smoke.example.json",
        document_hook_path=STAGING / "document-hook.disabled.json",
        compose_path=STAGING / "compose.orchestrator.yml",
        profile="minimal",
        output_dir=output,
    )
    return output


def test_host_contract_is_vendor_neutral_and_matches_current_target() -> None:
    host = validate_host_config(load_json_object(STAGING / "host.racknerd.example.json"))
    assert host["target"] == {
        "provider_label": "RackNerd",
        "provider_api_binding": None,
        "os_id": "ubuntu",
        "os_version": "24.04",
        "vcpu": 3,
        "ram_mb": 4096,
        "disk_gb": 60,
    }
    assert host["deploy_user"] != "root"
    assert host["layout"]["current"] == "/opt/mentorflow/current"


def test_initial_budget_fits_minimal_and_refuses_full_on_four_gb() -> None:
    host = validate_host_config(load_json_object(STAGING / "host.racknerd.example.json"))
    budget = validate_resource_budget(load_json_object(STAGING / "resource-budget.initial.json"))
    observed = {
        "os_id": "ubuntu", "os_version": "24.04", "vcpu": 3,
        "memory_total_mb": 3900, "memory_available_mb": 3200,
        "disk_total_gb": 56, "disk_free_gb": 40, "disk_writable": True,
        "docker_available": True, "docker_detail": "28.0",
        "compose_available": True, "compose_detail": "2.39",
        "time_synchronized": True, "time_detail": "yes",
        "candidate_port_available": True, "dns_resolves": None, "ipv6_present": False,
    }
    assert evaluate_preflight(host, budget, "minimal", observed)["status"] == "PASS"
    occupied = {**observed, "candidate_port_available": False, "candidate_port_owner": "mentorflow_staging/frontend"}
    assert evaluate_preflight(host, budget, "minimal", occupied)["status"] == "PASS"
    foreign = {**occupied, "candidate_port_owner": "unrelated/process"}
    assert "candidate_port" in evaluate_preflight(host, budget, "minimal", foreign)["hard_failures"]
    full = evaluate_preflight(host, budget, "full", observed, allow_capacity_override=True)
    assert full["status"] == "FAIL" and "ram" in full["hard_failures"]
    assert budget["status"] == "INITIAL_STAGING_BUDGET"
    assert budget["profiles"]["full"]["minimum_ram_mb"] == 12288
    assert budget["profiles"]["full"]["services"]["docling-worker"]["memory_limit_mb"] == 2048


def test_runtime_template_and_document_hook_fail_closed() -> None:
    template = load_json_object(STAGING / "runtime-images.template.json")
    with pytest.raises(ContractError, match="not deployable"):
        validate_runtime_images(template, "minimal")
    hook = validate_document_hook(load_json_object(STAGING / "document-hook.disabled.json"))
    assert hook["status"] == "OPTIONAL_UNTIL_E1_04"
    assert hook["e1_04_dependency"] == "NO" and hook["required_for_release"] is False


def test_package_binds_exact_manifest_images_and_contains_no_secrets(tmp_path: Path) -> None:
    output = _build_package(tmp_path)
    root, package = load_package(output)
    assert package["profile"] == "minimal"
    assert package["e1_04_dependency"] == "NO"
    assert package["provider_mode"] == "fake" and package["real_provider_calls"] == 0
    assert package["document_hook"]["status"] == "OPTIONAL_UNTIL_E1_04"
    assert package["enterprise_contract_version"] == "enterprise-context.v1"
    assert package["enterprise_contract_bundle_sha256"] == "03e4f66505b6b98202a07a70a39925293876ecc7b7c72f617dbe3b76dcb8b628"
    assert all("@sha256:" in image for image in package["images"].values())
    environment = (root / "compose.env").read_text(encoding="utf-8")
    assert "MENTORFLOW_COMPOSE_PROJECT=mentorflow_staging" in environment
    assert "password=" not in environment.casefold() and "token=" not in environment.casefold()
    assert package["migration_policy"] == "BACKUP_BEFORE_MIGRATE_FAIL_CLOSED"


def test_package_rejects_manifest_with_non_frozen_enterprise_context(tmp_path: Path) -> None:
    manifest_path = tmp_path / "wrong-context-manifest.json"
    manifest = _manifest(manifest_path)
    manifest["enterprise_contract_bundle_sha256"] = "3" * 64
    manifest.pop("manifest_content_sha256")
    manifest["manifest_content_sha256"] = canonical_sha256(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime_path = tmp_path / "runtime.json"
    _runtime_images(runtime_path)
    with pytest.raises(ContractError, match="frozen enterprise-context.v1"):
        build_package(
            manifest_path=manifest_path,
            host_config_path=STAGING / "host.racknerd.example.json",
            budget_path=STAGING / "resource-budget.initial.json",
            runtime_images_path=runtime_path,
            smoke_path=STAGING / "smoke.example.json",
            document_hook_path=STAGING / "document-hook.disabled.json",
            compose_path=STAGING / "compose.orchestrator.yml",
            profile="minimal",
            output_dir=tmp_path / "must-not-build",
        )


def test_release_package_is_stable_across_manifest_format_and_eol(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    runtime_path = tmp_path / "runtime.json"
    manifest = _manifest(manifest_path)
    _runtime_images(runtime_path)

    def build(output: Path) -> dict[str, object]:
        return build_package(
            manifest_path=manifest_path,
            host_config_path=STAGING / "host.racknerd.example.json",
            budget_path=STAGING / "resource-budget.initial.json",
            runtime_images_path=runtime_path,
            smoke_path=STAGING / "smoke.example.json",
            document_hook_path=STAGING / "document-hook.disabled.json",
            compose_path=STAGING / "compose.orchestrator.yml",
            profile="minimal",
            output_dir=output,
        )

    lf_package = build(tmp_path / "lf-package")
    manifest_path.write_bytes((json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\r\n").encode("utf-8"))
    crlf_package = build(tmp_path / "crlf-package")
    assert crlf_package["package_content_sha256"] == lf_package["package_content_sha256"]
    assert crlf_package["artifact_sha256"] == lf_package["artifact_sha256"]


def test_package_artifact_tamper_is_rejected(tmp_path: Path) -> None:
    output = _build_package(tmp_path)
    (output / "smoke-config.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ContractError, match="artifact hash mismatch"):
        load_package(output)


def test_rehashed_package_metadata_cannot_diverge_from_manifest(tmp_path: Path) -> None:
    output = _build_package(tmp_path)
    metadata_path = output / "release-package.json"
    package = json.loads(metadata_path.read_text(encoding="utf-8"))
    package["source_tree"] = "9" * 40
    package.pop("package_content_sha256")
    package["package_content_sha256"] = canonical_sha256(package)
    metadata_path.write_text(json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="does not match its EnterpriseReleaseManifest"):
        load_package(output)


def test_deploy_dry_run_orders_backup_migration_health_smoke_then_switch(tmp_path: Path) -> None:
    package = _build_package(tmp_path)
    preflight = tmp_path / "preflight.json"
    _preflight(preflight)
    receipt_path = tmp_path / "evidence" / "deploy" / "receipt.json"
    receipt = deploy_release(
        package_dir=package,
        host_config=STAGING / "host.racknerd.example.json",
        preflight_receipt=preflight,
        receipt_path=receipt_path,
        execute=False,
    )
    phases = [item["phase"] for item in receipt["phases"]]
    assert phases.index("database_start") < phases.index("database_backup")
    assert phases.index("database_backup") < phases.index("identity_database_backup") < phases.index("migration") < phases.index("migration_verify")
    assert phases.index("migration_verify") < phases.index("candidate_health") < phases.index("k6_smoke")
    assert phases.index("k6_smoke") < phases.index("atomic_current_pointer")
    assert receipt["status"] == "DRY_RUN" and receipt["real_provider_calls"] == 0


def test_migration_failure_stops_before_candidate_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = _build_package(tmp_path)
    preflight = tmp_path / "preflight.json"
    _preflight(preflight)
    receipt_path = tmp_path / "evidence" / "deploy" / "receipt.json"

    def fail_migration(self: CommandRunner, phase: str, argv: list[str], **kwargs: object) -> None:
        self.records.append({"phase": phase, "argv": argv, "status": "FAIL" if phase == "migration" else "PLANNED"})
        if phase == "migration":
            raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(CommandRunner, "run", fail_migration)
    with pytest.raises(subprocess.CalledProcessError):
        deploy_release(
            package_dir=package,
            host_config=STAGING / "host.racknerd.example.json",
            preflight_receipt=preflight,
            receipt_path=receipt_path,
            execute=False,
        )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "MIGRATION_FAILED_STOPPED"
    assert "candidate_start" not in [item["phase"] for item in receipt["phases"]]
    assert "restore" in receipt["rollback_procedure"]


def test_load_defaults_small_and_expanded_profiles_require_approval(tmp_path: Path) -> None:
    root, package = load_package(_build_package(tmp_path))
    host = validate_host_config(load_json_object(STAGING / "host.racknerd.example.json"))
    runner = CommandRunner(execute=False)
    _run_load(runner, root, package, host, ["smoke"], "http://127.0.0.1:18080", tmp_path / "artifacts", approve_expanded_load=False)
    assert [item["phase"] for item in runner.records] == ["k6_smoke"]
    with pytest.raises(ContractError, match="expanded-load approval"):
        _run_load(runner, root, package, host, ["stress"], "http://127.0.0.1:18080", tmp_path / "artifacts", approve_expanded_load=False)
    _run_load(runner, root, package, host, ["average", "stress", "spike", "tenant-isolation"], "http://127.0.0.1:18080", tmp_path / "artifacts", approve_expanded_load=True)
    commands = "\n".join(" ".join(item["argv"]) for item in runner.records)
    assert "PROVIDER_ADAPTER=fake" in commands and "tenant-isolation/scenario.js" in commands


def test_security_runner_fails_exposed_admin_and_missing_headers() -> None:
    host = validate_host_config(load_json_object(STAGING / "host.racknerd.example.json"))
    secure_services = {
        name: {"read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]}
        for name in ("postgres", "keycloak", "backend", "frontend", "worker", "otel-collector")
    }
    passed = evaluate_security(host, {
        "public_ports": [22, 80, 443],
        "containers": secure_services,
        "tls_valid": True,
        "tls_detail": "verified",
        "headers": {
            "Strict-Transport-Security": "max-age=31536000",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "DENY",
        },
        "secret_scan_status": "PASS",
        "secret_scan_reports": ["gitleaks.json"],
        "default_credentials_safe": True,
    })
    assert passed["status"] == "PASS"
    failed = evaluate_security(host, {
        "public_ports": [22, 3000, 5432],
        "containers": secure_services,
        "tls_valid": False,
        "headers": {},
        "secret_scan_status": "FAIL",
        "default_credentials_safe": False,
    })
    assert failed["status"] == "FAIL"
    assert {"open_ports", "admin_ports_not_exposed", "tls", "security_headers", "secret_scan_artifacts", "default_credentials"}.issubset(failed["failures"])


def test_evidence_bundle_requires_all_categories_and_excludes_sensitive_files(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    items: dict[str, str] = {}
    for category in EVIDENCE_CATEGORIES:
        directory = evidence / category
        directory.mkdir(parents=True)
        (directory / "receipt.json").write_text(json.dumps({"category": category}) + "\n", encoding="utf-8")
        items[category] = category
    index_path = tmp_path / "bundle-index.json"
    archive_path = tmp_path / "bundle.zip"
    index = collect_bundle(
        evidence_root=evidence,
        items=items,
        release_id="release-test",
        source_commit="1" * 40,
        index_path=index_path,
        archive_path=archive_path,
    )
    assert index["status"] == "COLLECTED" and set(index["categories"]) == set(EVIDENCE_CATEGORIES)
    assert archive_path.is_file() and index_path.is_file()
    (evidence / "security" / "password.txt").write_text("not-a-real-secret", encoding="utf-8")
    with pytest.raises(ValueError, match="sensitive-looking"):
        collect_bundle(
            evidence_root=evidence,
            items=items,
            release_id="release-test",
            source_commit="1" * 40,
            index_path=tmp_path / "bad-index.json",
            archive_path=tmp_path / "bad.zip",
        )
    (evidence / "security" / "password.txt").unlink()
    (evidence / "security" / "runtime.env").write_text("not-a-real-environment", encoding="utf-8")
    with pytest.raises(ValueError, match="sensitive-looking"):
        collect_bundle(
            evidence_root=evidence,
            items=items,
            release_id="release-test",
            source_commit="1" * 40,
            index_path=tmp_path / "env-index.json",
            archive_path=tmp_path / "env.zip",
        )


def test_rollback_drill_requires_matching_migration_heads_and_plans_b_to_a(tmp_path: Path) -> None:
    candidate = _build_package(tmp_path, name="candidate", commit="4" * 40)
    rollback = _build_package(tmp_path, name="rollback", commit="5" * 40)
    preflight = tmp_path / "preflight.json"
    _preflight(preflight)
    receipt = rollback_drill(
        candidate_dir=candidate,
        rollback_dir=rollback,
        host_config=STAGING / "host.racknerd.example.json",
        preflight_receipt=preflight,
        receipt_path=tmp_path / "drill.json",
        execute=False,
    )
    phases = [item["phase"] for item in receipt["phases"]]
    assert phases.index("candidate_b_health") < phases.index("k6_smoke") < phases.index("rollback_a_health")
    incompatible = _build_package(tmp_path, name="incompatible", commit="6" * 40, migration="0031_breaking")
    with pytest.raises(ContractError, match="matching migration heads"):
        rollback_drill(
            candidate_dir=incompatible,
            rollback_dir=rollback,
            host_config=STAGING / "host.racknerd.example.json",
            preflight_receipt=preflight,
            receipt_path=tmp_path / "bad-drill.json",
            execute=False,
        )


def test_bootstrap_is_shell_valid_and_orchestrator_static_gate_passes() -> None:
    bootstrap = (SCRIPTS / "bootstrap_staging_host.sh").read_text(encoding="utf-8")
    assert "--authorized-key-file" in bootstrap
    assert "authorized SSH key before SSH hardening" in bootstrap
    bash = shutil.which("bash")
    if bash:
        subprocess.run([bash, "-n", str(SCRIPTS / "bootstrap_staging_host.sh")], check=True, cwd=ROOT)
    result = verify_orchestrator(ROOT)
    assert result["status"] == "PASS"
    assert result["e1_04_dependency"] == "NO"
    assert result["document_hook"] == "OPTIONAL_UNTIL_E1_04"
    assert result["live_staging"] == "NOT_RUN" and result["provider_calls"] == 0
