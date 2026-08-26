from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from staging_contracts import (
    PACKAGE_SCHEMA,
    ContractError,
    canonical_sha256,
    file_sha256,
    load_json_object,
    validate_document_hook,
    validate_host_config,
    validate_release_manifest,
    validate_release_package,
    validate_resource_budget,
    validate_runtime_images,
    validate_smoke_config,
    write_atomic_json,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPOSE = ROOT / "deploy" / "enterprise" / "staging" / "compose.orchestrator.yml"
DEFAULT_OTEL = ROOT / "deploy" / "enterprise" / "staging" / "otel-collector.minimal.yml"
DEFAULT_PROMETHEUS = ROOT / "deploy" / "enterprise" / "staging" / "prometheus.staging.yml"
DEFAULT_POSTGRES_INIT = ROOT / "deploy" / "enterprise" / "staging" / "postgres-init.sh"
CONTEXT_LOCK = ROOT / ".github" / "enterprise-context.lock.json"
LOAD_SUITE = ROOT / "tests" / "load"
APP_IMAGE_ENV = {
    "backend": "MENTORFLOW_BACKEND_IMAGE",
    "frontend": "MENTORFLOW_FRONTEND_IMAGE",
    "document-worker": "MENTORFLOW_DOCUMENT_WORKER_IMAGE",
}
RUNTIME_IMAGE_ENV = {
    "postgres": "POSTGRES_IMAGE",
    "keycloak": "KEYCLOAK_IMAGE",
    "worker": "MENTORFLOW_WORKER_IMAGE",
    "otel-collector": "OTEL_COLLECTOR_IMAGE",
    "k6": "K6_IMAGE",
    "object-store": "OBJECT_STORE_IMAGE",
    "prometheus": "PROMETHEUS_IMAGE",
    "grafana": "GRAFANA_IMAGE",
    "testing-services": "TESTING_SERVICES_IMAGE",
}
E1_04_DEPENDENCY = "NO"


def _repo_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) or not resolved.is_file():
        raise ContractError(f"{label} must be an existing repository file")
    return resolved


def _resource_environment(profile: str, budget: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for service, allocation in budget["profiles"][profile]["services"].items():
        prefix = service.replace("-", "_").upper()
        values[f"{prefix}_MEMORY_RESERVATION"] = f"{allocation['memory_reservation_mb']}m"
        values[f"{prefix}_MEMORY_LIMIT"] = f"{allocation['memory_limit_mb']}m"
        values[f"{prefix}_CPUS"] = str(allocation["cpus"])
    return values


def _write_environment(path: Path, values: dict[str, str]) -> None:
    if any("\n" in key or "\n" in value or "\r" in key or "\r" in value for key, value in values.items()):
        raise ContractError("compose environment values must be single-line")
    path.write_text("".join(f"{key}={values[key]}\n" for key in sorted(values)), encoding="utf-8", newline="\n")


def _copy_text_lf(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")


def _copy_tree_text_lf(source: Path, destination: Path) -> None:
    for item in sorted(source.rglob("*")):
        if item.is_file():
            _copy_text_lf(item, destination / item.relative_to(source))


def build_package(
    *,
    manifest_path: Path,
    host_config_path: Path,
    budget_path: Path,
    runtime_images_path: Path,
    smoke_path: Path,
    document_hook_path: Path,
    compose_path: Path,
    profile: str,
    output_dir: Path,
    allow_capacity_override: bool = False,
    capacity_override_reason: str | None = None,
) -> dict[str, Any]:
    manifest = load_json_object(manifest_path.resolve())
    app_images = validate_release_manifest(manifest)
    context_lock = load_json_object(CONTEXT_LOCK)
    if context_lock.get("contract_version") != "enterprise-context.v1":
        raise ContractError("the repository enterprise context lock is not enterprise-context.v1")
    if (
        manifest.get("enterprise_contract_version") != context_lock.get("contract_version")
        or manifest.get("enterprise_contract_bundle_sha256") != context_lock.get("contract_bundle_sha256")
    ):
        raise ContractError("EnterpriseReleaseManifest does not consume the frozen enterprise-context.v1 bundle")
    host = validate_host_config(load_json_object(host_config_path.resolve()))
    budget = validate_resource_budget(load_json_object(budget_path.resolve()))
    runtime_images = validate_runtime_images(load_json_object(runtime_images_path.resolve()), profile)
    smoke = validate_smoke_config(load_json_object(smoke_path.resolve()))
    document_hook = validate_document_hook(load_json_object(document_hook_path.resolve()))
    if document_hook["e1_04_dependency"] != E1_04_DEPENDENCY:
        raise ContractError("DocumentStagingVerificationHook must remain OPTIONAL_UNTIL_E1_04 with E1_04_DEPENDENCY=NO")
    if document_hook["enabled"] and profile != "full":
        raise ContractError("a registered DocumentStagingVerificationHook requires the full profile and its bounded Docling budget")
    compose = _repo_file(compose_path, "compose file")
    _repo_file(DEFAULT_OTEL, "OTel config")
    _repo_file(DEFAULT_PROMETHEUS, "Prometheus config")
    _repo_file(DEFAULT_POSTGRES_INIT, "Postgres initialization script")
    if not LOAD_SUITE.is_dir():
        raise ContractError("tests/load is required for staging smoke and load gates")
    profile_budget = budget["profiles"][profile]
    target_ram = int(host["target"]["ram_mb"])
    capacity_override_used = target_ram < int(profile_budget["minimum_ram_mb"])
    if capacity_override_used:
        if not allow_capacity_override or not capacity_override_reason or len(capacity_override_reason.strip()) < 12:
            raise ContractError("the selected profile exceeds host RAM; an explicit reviewed capacity override reason is required")
    source_commit = str(manifest["source_commit"])
    release_id = f"{source_commit[:12]}-{str(manifest['manifest_content_sha256'])[:12]}"
    output = output_dir.resolve()
    if output.exists():
        raise ContractError("staging release package output must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent))
    try:
        (temporary / "enterprise-release-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _copy_text_lf(compose, temporary / "compose.orchestrator.yml")
        _copy_text_lf(DEFAULT_OTEL, temporary / "otel-collector.minimal.yml")
        _copy_text_lf(DEFAULT_PROMETHEUS, temporary / "prometheus.staging.yml")
        _copy_text_lf(DEFAULT_POSTGRES_INIT, temporary / "postgres-init.sh")
        _copy_text_lf(budget_path.resolve(), temporary / "resource-budget.json")
        _copy_text_lf(smoke_path.resolve(), temporary / "smoke-config.json")
        _copy_text_lf(document_hook_path.resolve(), temporary / "document-hook.json")
        _copy_tree_text_lf(LOAD_SUITE, temporary / "tests" / "load")
        artifacts: dict[str, str] = {
            "manifest": "enterprise-release-manifest.json",
            "compose": "compose.orchestrator.yml",
            "environment": "compose.env",
            "otel": "otel-collector.minimal.yml",
            "prometheus": "prometheus.staging.yml",
            "postgres_init": "postgres-init.sh",
            "resource_budget": "resource-budget.json",
            "smoke": "smoke-config.json",
            "document_hook": "document-hook.json",
            "load_suite": "tests/load",
            "proxy_upstream": "proxy-upstream.conf",
        }
        if document_hook["enabled"]:
            overlay = _repo_file(ROOT / str(document_hook["compose_overlay"]), "document hook compose overlay")
            _copy_text_lf(overlay, temporary / "compose.document-hook.yml")
            artifacts["document_compose_overlay"] = "compose.document-hook.yml"
        images = {**app_images, **runtime_images}
        environment = {
            "MENTORFLOW_COMPOSE_PROJECT": "mentorflow_staging",
            "MENTORFLOW_CANDIDATE_PORT": str(host["candidate_loopback_port"]),
            "MENTORFLOW_RUNTIME_ENV_FILE": f"{host['layout']['data']}/secrets/runtime.env",
            "MENTORFLOW_SECRETS_DIR": f"{host['layout']['data']}/secrets",
            "MENTORFLOW_PROVIDER_MODE": "fake",
            "DOCUMENT_HOOK_ENABLED": "true" if document_hook["enabled"] else "false",
            **{APP_IMAGE_ENV[name]: value for name, value in app_images.items()},
            **{RUNTIME_IMAGE_ENV[name]: value for name, value in runtime_images.items()},
            **_resource_environment(profile, budget),
        }
        _write_environment(temporary / "compose.env", environment)
        (temporary / "proxy-upstream.conf").write_text(
            "# Included by the host reverse proxy after atomic current-pointer switch.\n"
            "proxy_pass http://127.0.0.1:" + str(host["candidate_loopback_port"]) + ";\n",
            encoding="utf-8",
            newline="\n",
        )
        artifact_hashes: dict[str, str] = {}
        for name, relative in artifacts.items():
            path = temporary / relative
            if path.is_file():
                artifact_hashes[name] = file_sha256(path)
            elif path.is_dir():
                directory_index = [
                    {"path": item.relative_to(path).as_posix(), "sha256": file_sha256(item)}
                    for item in sorted(path.rglob("*"))
                    if item.is_file()
                ]
                artifact_hashes[name] = canonical_sha256(directory_index)
            else:
                raise ContractError(f"package artifact missing: {relative}")
        package: dict[str, Any] = {
            "schema": PACKAGE_SCHEMA,
            "release_id": release_id,
            "source_commit": source_commit,
            "source_tree": manifest["source_tree"],
            "enterprise_contract_version": context_lock["contract_version"],
            "enterprise_contract_bundle_sha256": context_lock["contract_bundle_sha256"],
            "manifest_ref": "enterprise-release-manifest.json",
            "manifest_sha256": file_sha256(temporary / "enterprise-release-manifest.json"),
            "migration_version": manifest["migration_version"],
            "migration_policy": "BACKUP_BEFORE_MIGRATE_FAIL_CLOSED",
            "profile": profile,
            "images": dict(sorted(images.items())),
            "resource_budget_status": budget["status"],
            "capacity_override": {
                "used": capacity_override_used,
                "reason": capacity_override_reason if capacity_override_used else None,
            },
            "provider_mode": "fake",
            "real_provider_calls": 0,
            "e1_04_dependency": E1_04_DEPENDENCY,
            "document_hook": document_hook,
            "required_secret_files": [
                f"{host['layout']['data']}/secrets/runtime.env",
                f"{host['layout']['data']}/secrets/postgres_password",
                f"{host['layout']['data']}/secrets/keycloak_admin_password",
            ],
            "artifacts": artifacts,
            "artifact_sha256": artifact_hashes,
        }
        package["package_content_sha256"] = canonical_sha256(package)
        validate_release_package(package)
        write_atomic_json(temporary / "release-package.json", package)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an exact-version MentorFlow staging release package.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--host-config", type=Path, required=True)
    parser.add_argument("--resource-budget", type=Path, required=True)
    parser.add_argument("--runtime-images", type=Path, required=True)
    parser.add_argument("--smoke-config", type=Path, required=True)
    parser.add_argument("--document-hook", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--profile", choices=("minimal", "full"), default="minimal")
    parser.add_argument("--allow-capacity-override", action="store_true")
    parser.add_argument("--capacity-override-reason")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    package = build_package(
        manifest_path=args.manifest,
        host_config_path=args.host_config,
        budget_path=args.resource_budget,
        runtime_images_path=args.runtime_images,
        smoke_path=args.smoke_config,
        document_hook_path=args.document_hook,
        compose_path=args.compose_file,
        profile=args.profile,
        output_dir=args.output_dir,
        allow_capacity_override=args.allow_capacity_override,
        capacity_override_reason=args.capacity_override_reason,
    )
    print(json.dumps({"status": "PASS", "release_id": package["release_id"], "output": str(args.output_dir.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
