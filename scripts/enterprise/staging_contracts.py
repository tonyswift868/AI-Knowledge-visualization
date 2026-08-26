from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


HOST_SCHEMA = "mentorflow.staging-host-config.v1"
BUDGET_SCHEMA = "mentorflow.container-resource-budget.v1"
RUNTIME_IMAGES_SCHEMA = "mentorflow.staging-runtime-images.v1"
DOCUMENT_HOOK_SCHEMA = "mentorflow.document-staging-verification-hook.v1"
SMOKE_SCHEMA = "mentorflow.staging-smoke-config.v1"
PACKAGE_SCHEMA = "mentorflow.staging-release-package.v1"
MANIFEST_SCHEMA = "mentorflow.enterprise-release-manifest.v2"
IMMUTABLE_IMAGE = re.compile(r"(?:@sha256:[0-9a-f]{64}|:[0-9a-f]{40})$")
LOWER_HEX_40 = re.compile(r"[0-9a-f]{40}")
LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}")
PROFILES = ("minimal", "full")
APP_IMAGE_ROLES = ("backend", "frontend", "document-worker")
MINIMAL_SERVICES = ("postgres", "keycloak", "backend", "frontend", "worker", "otel-collector")
FULL_ONLY_SERVICES = ("object-store", "prometheus", "grafana", "testing-services")
DOCUMENT_HOOK_SERVICES = ("docling-worker",)
MINIMAL_RUNTIME_IMAGES = ("postgres", "keycloak", "worker", "otel-collector", "k6")
FULL_RUNTIME_IMAGES = MINIMAL_RUNTIME_IMAGES + ("object-store", "prometheus", "grafana", "testing-services")
REQUIRED_SMOKE_CHECKS = ("health", "login", "workspace", "knowledge", "plan", "progress", "cost")
EVIDENCE_CATEGORIES = (
    "deploy",
    "versions",
    "containers",
    "migration",
    "tests",
    "k6",
    "security",
    "tenant_isolation",
    "backup_refs",
    "otel_refs",
)


class ContractError(ValueError):
    pass


def load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def canonical_sha256(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def require_exact_image(value: object, field: str) -> str:
    image = str(value)
    if not IMMUTABLE_IMAGE.search(image) or image.endswith(":latest"):
        raise ContractError(f"{field} must use a sha256 digest or exact 40-character commit tag")
    return image


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value.strip()


def validate_host_config(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != HOST_SCHEMA:
        raise ContractError("unsupported StagingHostConfig schema")
    target = payload.get("target")
    if not isinstance(target, dict):
        raise ContractError("target must be an object")
    if target.get("os_id") != "ubuntu" or target.get("os_version") != "24.04":
        raise ContractError("the current host contract requires Ubuntu 24.04")
    if target.get("provider_api_binding") is not None:
        raise ContractError("the staging contract must remain vendor-neutral and cannot bind a provider API")
    for field, minimum in (("vcpu", 1), ("ram_mb", 2048), ("disk_gb", 20)):
        value = target.get(field)
        if not isinstance(value, int) or value < minimum:
            raise ContractError(f"target.{field} must be an integer >= {minimum}")
    deploy_user = _required_string(payload, "deploy_user")
    if deploy_user in {"root", "admin"}:
        raise ContractError("deploy_user must be a dedicated non-root account")
    forbidden_credential_fields = {"password", "passwd", "secret", "token", "private_key", "ssh_private_key"}
    stack: list[object] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if any(str(key).casefold() in forbidden_credential_fields for key in current):
                raise ContractError("host config must not contain credential fields")
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    ssh = payload.get("ssh")
    if not isinstance(ssh, dict) or ssh.get("password_authentication") is not False or ssh.get("root_login") is not False:
        raise ContractError("SSH password authentication and root login must be disabled")
    if not isinstance(ssh.get("port"), int) or not 1 <= ssh["port"] <= 65535:
        raise ContractError("ssh.port must be a valid TCP port")
    network = payload.get("network")
    if not isinstance(network, dict) or network.get("ipv6_policy") not in {"REVIEW_BEFORE_ENABLE", "ENABLED_REVIEWED", "DISABLED_REVIEWED"}:
        raise ContractError("network.ipv6_policy must require an explicit review")
    ports = network.get("allowed_tcp_ports")
    if (
        not isinstance(ports, list)
        or ssh["port"] not in ports
        or any(not isinstance(port, int) or not 1 <= port <= 65535 for port in ports)
        or len(ports) != len(set(ports))
    ):
        raise ContractError("network.allowed_tcp_ports must include the SSH port")
    layout = payload.get("layout")
    if not isinstance(layout, dict) or layout.get("root") != "/opt/mentorflow":
        raise ContractError("layout.root must be /opt/mentorflow")
    expected_layout = {
        "releases": "/opt/mentorflow/releases",
        "current": "/opt/mentorflow/current",
        "data": "/opt/mentorflow/data",
        "backups": "/opt/mentorflow/backups",
        "artifacts": "/opt/mentorflow/artifacts",
        "logs": "/opt/mentorflow/logs",
    }
    if any(layout.get(name) != path for name, path in expected_layout.items()):
        raise ContractError("staging directory layout is incomplete or outside /opt/mentorflow")
    swap = payload.get("swap")
    if not isinstance(swap, dict) or swap.get("policy") not in {"CONFIGURE_IF_ABSENT", "REQUIRE_EXISTING", "DISABLED_REVIEWED"}:
        raise ContractError("swap policy seam is missing")
    if swap["policy"] == "CONFIGURE_IF_ABSENT" and (not isinstance(swap.get("size_mb"), int) or swap["size_mb"] < 512):
        raise ContractError("configured swap must reserve at least 512 MB")
    if payload.get("default_profile") not in PROFILES:
        raise ContractError("default_profile must be minimal or full")
    candidate_port = payload.get("candidate_loopback_port")
    if not isinstance(candidate_port, int) or not 1024 <= candidate_port <= 65535 or candidate_port in ports:
        raise ContractError("candidate_loopback_port must be a non-public high port")
    proxy_reload = payload.get("proxy_reload_command")
    if not isinstance(proxy_reload, list) or any(not isinstance(item, str) or not item.strip() for item in proxy_reload):
        raise ContractError("proxy_reload_command must be an argv list")
    return payload


def validate_resource_budget(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != BUDGET_SCHEMA or payload.get("status") != "INITIAL_STAGING_BUDGET":
        raise ContractError("resource budget must be INITIAL_STAGING_BUDGET")
    host_ram = payload.get("host_ram_mb")
    reserve = payload.get("host_reserve_mb")
    if not isinstance(host_ram, int) or host_ram < 2048 or not isinstance(reserve, int) or reserve < 512:
        raise ContractError("resource budget requires explicit host RAM and >=512 MB host reserve")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(PROFILES):
        raise ContractError("resource budget must define minimal and full profiles")
    expected_services = {
        "minimal": set(MINIMAL_SERVICES),
        "full": set(MINIMAL_SERVICES + FULL_ONLY_SERVICES + DOCUMENT_HOOK_SERVICES),
    }
    for profile_name in PROFILES:
        profile = profiles[profile_name]
        if not isinstance(profile, dict) or set(profile.get("services", {})) != expected_services[profile_name]:
            raise ContractError(f"{profile_name} profile service budget is incomplete")
        minimum_ram = profile.get("minimum_ram_mb")
        if not isinstance(minimum_ram, int) or minimum_ram < 2048:
            raise ContractError(f"{profile_name}.minimum_ram_mb is invalid")
        minimum_observed_ram = profile.get("minimum_observed_ram_mb")
        if (
            not isinstance(minimum_observed_ram, int)
            or minimum_observed_ram > minimum_ram
            or minimum_observed_ram < int(minimum_ram * 0.90)
        ):
            raise ContractError(f"{profile_name}.minimum_observed_ram_mb must be a bounded advertised-capacity tolerance")
        limits = 0
        for service, budget in profile["services"].items():
            if not isinstance(budget, dict):
                raise ContractError(f"{profile_name}.{service} budget must be an object")
            reservation = budget.get("memory_reservation_mb")
            limit = budget.get("memory_limit_mb")
            cpus = budget.get("cpus")
            if not isinstance(reservation, int) or not isinstance(limit, int) or not 0 < reservation <= limit:
                raise ContractError(f"{profile_name}.{service} memory budget is invalid")
            if not isinstance(cpus, (int, float)) or not 0 < float(cpus) <= 4:
                raise ContractError(f"{profile_name}.{service} CPU budget is invalid")
            limits += limit
        if limits + reserve > minimum_ram:
            raise ContractError(f"{profile_name} service limits plus host reserve exceed its minimum RAM")
    if profiles["minimal"]["minimum_ram_mb"] > host_ram:
        raise ContractError("minimal profile must fit the target 4 GB host")
    if profiles["full"].get("requires_capacity_override") is not True or profiles["full"]["minimum_ram_mb"] <= host_ram:
        raise ContractError("full profile must truthfully require a larger host or explicit capacity override")
    return payload


def validate_runtime_images(payload: dict[str, Any], profile: str) -> dict[str, str]:
    if profile not in PROFILES:
        raise ContractError("unsupported compose profile")
    if payload.get("schema") != RUNTIME_IMAGES_SCHEMA or payload.get("status") != "RESOLVED":
        raise ContractError("runtime images must be RESOLVED; the committed template is not deployable")
    images = payload.get("images")
    if not isinstance(images, dict):
        raise ContractError("runtime images must be an object")
    required = MINIMAL_RUNTIME_IMAGES if profile == "minimal" else FULL_RUNTIME_IMAGES
    missing = [name for name in required if name not in images]
    if missing:
        raise ContractError(f"runtime image refs missing: {missing}")
    return {name: require_exact_image(images[name], f"runtime image {name}") for name in required}


def validate_document_hook(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != DOCUMENT_HOOK_SCHEMA:
        raise ContractError("unsupported DocumentStagingVerificationHook schema")
    if payload.get("e1_04_dependency") != "NO" or payload.get("required_for_release") is not False:
        raise ContractError("E1-07C must not depend on E1-04")
    enabled = payload.get("enabled")
    status = payload.get("status")
    smoke_names = payload.get("smoke_check_names")
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(smoke_names, list) or any(not isinstance(item, str) or not item for item in smoke_names) or len(smoke_names) != len(set(smoke_names)):
        raise ContractError("document hook smoke check names are invalid")
    if not isinstance(evidence_refs, list) or any(not isinstance(item, str) or not item for item in evidence_refs) or len(evidence_refs) != len(set(evidence_refs)):
        raise ContractError("document hook evidence refs are invalid")
    if enabled is False:
        if status != "OPTIONAL_UNTIL_E1_04" or payload.get("compose_overlay") is not None or smoke_names or evidence_refs:
            raise ContractError("disabled document hook must remain OPTIONAL_UNTIL_E1_04")
    elif enabled is True:
        required_smoke = {"document_submit", "document_status", "foreign_document"}
        if status != "REGISTERED" or not _required_string(payload, "compose_overlay") or not required_smoke.issubset(smoke_names) or not evidence_refs:
            raise ContractError("enabled document hook requires a registered compose overlay")
    else:
        raise ContractError("document hook enabled must be boolean")
    return payload


def validate_smoke_config(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != SMOKE_SCHEMA or payload.get("provider_mode") != "fake":
        raise ContractError("smoke config must be provider-zero")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise ContractError("smoke checks must be a list")
    names = [item.get("name") for item in checks if isinstance(item, dict)]
    if any(name not in names for name in REQUIRED_SMOKE_CHECKS):
        raise ContractError("smoke config lacks a required core check")
    if len(names) != len(set(names)):
        raise ContractError("smoke check names must be unique")
    for item in checks:
        if not isinstance(item, dict) or item.get("method") not in {"GET", "POST"}:
            raise ContractError("smoke check method must be GET or POST")
        if item.get("name") == "health":
            if item.get("path") != "/api/health":
                raise ContractError("health smoke path must be /api/health")
        elif not isinstance(item.get("path_env"), str) or not item["path_env"].endswith("_PATH"):
            raise ContractError("non-health smoke checks must resolve paths from environment")
    document = payload.get("document_hook")
    if not isinstance(document, dict) or document.get("required") is not False or document.get("status") != "OPTIONAL_UNTIL_E1_04":
        raise ContractError("document smoke hook must be optional until E1-04")
    return payload


def validate_release_manifest(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise ContractError("unsupported EnterpriseReleaseManifest")
    if not LOWER_HEX_40.fullmatch(str(payload.get("source_commit", ""))) or not LOWER_HEX_40.fullmatch(str(payload.get("source_tree", ""))):
        raise ContractError("release manifest source identity must be exact")
    claimed_hash = str(payload.get("manifest_content_sha256", ""))
    if not LOWER_HEX_64.fullmatch(claimed_hash):
        raise ContractError("release manifest content hash is missing")
    unhashed = dict(payload)
    unhashed.pop("manifest_content_sha256", None)
    if canonical_sha256(unhashed) != claimed_hash:
        raise ContractError("release manifest content hash does not match its canonical content")
    if payload.get("provider_mode") != "fake" or payload.get("real_provider_calls") != 0:
        raise ContractError("staging package must default to fake provider with zero real calls")
    if payload.get("enterprise_contract_version") != "enterprise-context.v1" or not LOWER_HEX_64.fullmatch(str(payload.get("enterprise_contract_bundle_sha256", ""))):
        raise ContractError("release manifest must identify an enterprise-context.v1 bundle")
    images = payload.get("image_tags")
    if not isinstance(images, dict) or set(images) != set(APP_IMAGE_ROLES):
        raise ContractError("release manifest must contain exactly the three application image roles")
    return {name: require_exact_image(images[name], f"manifest image {name}") for name in images}


def validate_release_package(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != PACKAGE_SCHEMA:
        raise ContractError("unsupported staging release package")
    if payload.get("e1_04_dependency") != "NO" or payload.get("provider_mode") != "fake" or payload.get("real_provider_calls") != 0:
        raise ContractError("staging package dependency/provider boundary is invalid")
    if payload.get("enterprise_contract_version") != "enterprise-context.v1" or not LOWER_HEX_64.fullmatch(str(payload.get("enterprise_contract_bundle_sha256", ""))):
        raise ContractError("staging package enterprise context identity is invalid")
    if payload.get("profile") not in PROFILES:
        raise ContractError("staging package profile is invalid")
    if not LOWER_HEX_40.fullmatch(str(payload.get("source_commit", ""))):
        raise ContractError("staging package source commit is invalid")
    if not LOWER_HEX_40.fullmatch(str(payload.get("source_tree", ""))):
        raise ContractError("staging package source tree is invalid")
    if not re.fullmatch(r"[0-9a-f]{12}-[0-9a-f]{12}", str(payload.get("release_id", ""))):
        raise ContractError("staging package release ID is invalid")
    if not LOWER_HEX_64.fullmatch(str(payload.get("package_content_sha256", ""))):
        raise ContractError("staging package content hash is invalid")
    migration_version = payload.get("migration_version")
    if (
        not isinstance(migration_version, list)
        or not migration_version
        or any(not isinstance(item, str) or not item.strip() for item in migration_version)
        or len(set(migration_version)) != len(migration_version)
    ):
        raise ContractError("staging package migration version is invalid")
    if payload.get("migration_policy") != "BACKUP_BEFORE_MIGRATE_FAIL_CLOSED":
        raise ContractError("staging package migration policy must fail closed")
    unhashed = dict(payload)
    claimed_hash = str(unhashed.pop("package_content_sha256"))
    if canonical_sha256(unhashed) != claimed_hash:
        raise ContractError("staging package content hash does not match its canonical content")
    images = payload.get("images")
    if not isinstance(images, dict) or not images:
        raise ContractError("staging package images are missing")
    required_images = {"backend", "frontend", "document-worker", *(MINIMAL_RUNTIME_IMAGES if payload["profile"] == "minimal" else FULL_RUNTIME_IMAGES)}
    if not required_images.issubset(images):
        raise ContractError("staging package lacks required exact image roles")
    for name, value in images.items():
        require_exact_image(value, f"package image {name}")
    validate_document_hook(payload.get("document_hook") if isinstance(payload.get("document_hook"), dict) else {})
    if payload.get("resource_budget_status") != "INITIAL_STAGING_BUDGET":
        raise ContractError("staging package resource budget identity is invalid")
    capacity_override = payload.get("capacity_override")
    if not isinstance(capacity_override, dict) or not isinstance(capacity_override.get("used"), bool) or set(capacity_override) != {"used", "reason"}:
        raise ContractError("staging package capacity override is invalid")
    if capacity_override["used"] and (not isinstance(capacity_override.get("reason"), str) or len(capacity_override["reason"].strip()) < 12):
        raise ContractError("used capacity override requires a reviewed reason")
    if not capacity_override["used"] and capacity_override.get("reason") is not None:
        raise ContractError("unused capacity override cannot retain a reason")
    artifacts = payload.get("artifacts")
    hashes = payload.get("artifact_sha256")
    required_artifacts = {"manifest", "compose", "environment", "otel", "prometheus", "postgres_init", "resource_budget", "smoke", "document_hook", "load_suite", "proxy_upstream"}
    if not isinstance(artifacts, dict) or not required_artifacts.issubset(artifacts):
        raise ContractError("staging package artifact map is incomplete")
    if payload.get("manifest_ref") != artifacts.get("manifest"):
        raise ContractError("staging package manifest ref must use its hashed manifest artifact")
    if not isinstance(hashes, dict) or set(hashes) != set(artifacts) or any(not LOWER_HEX_64.fullmatch(str(value)) for value in hashes.values()):
        raise ContractError("staging package artifact hashes are incomplete")
    secrets = payload.get("required_secret_files")
    if not isinstance(secrets, list) or len(secrets) < 3 or any(not isinstance(item, str) or not item.startswith("/opt/mentorflow/data/secrets/") for item in secrets) or len(secrets) != len(set(secrets)):
        raise ContractError("staging package required secret paths are invalid")
    if not LOWER_HEX_64.fullmatch(str(payload.get("manifest_sha256", ""))):
        raise ContractError("staging package manifest file hash is invalid")
    return payload
