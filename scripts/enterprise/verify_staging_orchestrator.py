from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from staging_contracts import (
    ContractError,
    DOCUMENT_HOOK_SERVICES,
    FULL_ONLY_SERVICES,
    MINIMAL_SERVICES,
    load_json_object,
    validate_document_hook,
    validate_host_config,
    validate_resource_budget,
    validate_runtime_images,
    validate_smoke_config,
)


ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "deploy" / "enterprise" / "staging"
REQUIRED_FILES = (
    ROOT / "contracts" / "enterprise" / "staging-orchestrator.schema.json",
    STAGING / "host.racknerd.example.json",
    STAGING / "resource-budget.initial.json",
    STAGING / "runtime-images.template.json",
    STAGING / "document-hook.disabled.json",
    STAGING / "smoke.example.json",
    STAGING / "compose.orchestrator.yml",
    STAGING / "compose.document-hook.yml",
    STAGING / "otel-collector.minimal.yml",
    STAGING / "prometheus.staging.yml",
    ROOT / "scripts" / "enterprise" / "bootstrap_staging_host.sh",
    ROOT / "scripts" / "enterprise" / "staging_contracts.py",
    ROOT / "scripts" / "enterprise" / "staging_host_preflight.py",
    ROOT / "scripts" / "enterprise" / "build_staging_release_package.py",
    ROOT / "scripts" / "enterprise" / "staging_release_orchestrator.py",
    ROOT / "scripts" / "enterprise" / "staging_security_runner.py",
    ROOT / "scripts" / "enterprise" / "collect_staging_evidence.py",
    ROOT / ".github" / "workflows" / "staging-orchestrator.yml",
    ROOT / "docs" / "enterprise" / "staging-orchestrator.md",
    ROOT / "reports" / "enterprise" / "machine" / "staging-orchestrator.pending.json",
)


def _require(text: str, values: tuple[str, ...], source: str) -> None:
    missing = [value for value in values if value not in text]
    if missing:
        raise RuntimeError(f"{source} missing required tokens: {missing}")


def verify(root: Path = ROOT) -> dict[str, object]:
    missing = [path.relative_to(root).as_posix() for path in REQUIRED_FILES if not path.is_file()]
    if missing:
        raise RuntimeError(f"E1-07C assets missing: {missing}")
    host = validate_host_config(load_json_object(STAGING / "host.racknerd.example.json"))
    budget = validate_resource_budget(load_json_object(STAGING / "resource-budget.initial.json"))
    hook = validate_document_hook(load_json_object(STAGING / "document-hook.disabled.json"))
    smoke = validate_smoke_config(load_json_object(STAGING / "smoke.example.json"))
    runtime_template = load_json_object(STAGING / "runtime-images.template.json")
    try:
        validate_runtime_images(runtime_template, "minimal")
    except ContractError as exc:
        if "not deployable" not in str(exc):
            raise
    else:
        raise RuntimeError("the committed unresolved runtime image template must fail closed")

    compose = yaml.safe_load((STAGING / "compose.orchestrator.yml").read_text(encoding="utf-8"))
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise RuntimeError("staging compose is not a YAML object with services")
    services = compose["services"]
    minimal = {name for name, value in services.items() if "minimal" in value.get("profiles", [])}
    full = {name for name, value in services.items() if "full" in value.get("profiles", [])}
    if not set(MINIMAL_SERVICES).issubset(minimal) or not {"migrate", "migration-verify"}.issubset(minimal):
        raise RuntimeError("minimal compose profile lacks core services or migration gates")
    if not set(MINIMAL_SERVICES + FULL_ONLY_SERVICES).issubset(full):
        raise RuntimeError("full compose profile lacks its expanded services")
    if any("ports" in services[name] for name in services if name != "frontend"):
        raise RuntimeError("only the candidate frontend may publish a host port")
    frontend_ports = services["frontend"].get("ports", [])
    if not frontend_ports or not all(str(value).startswith("127.0.0.1:") for value in frontend_ports):
        raise RuntimeError("candidate frontend port must remain loopback-only")
    hardened = [
        name
        for name, value in services.items()
        if value.get("read_only") is not True
        or "ALL" not in value.get("cap_drop", [])
        or "no-new-privileges:true" not in value.get("security_opt", [])
    ]
    if hardened:
        raise RuntimeError(f"compose services lack hardening: {hardened}")
    overlay = yaml.safe_load((STAGING / "compose.document-hook.yml").read_text(encoding="utf-8"))
    if set(overlay.get("services", {})) != set(DOCUMENT_HOOK_SERVICES) or "document-hook" not in overlay["services"]["docling-worker"].get("profiles", []):
        raise RuntimeError("document hook compose overlay is invalid")
    if not set(DOCUMENT_HOOK_SERVICES).issubset(budget["profiles"]["full"]["services"]):
        raise RuntimeError("full resource budget must bound the optional document hook")

    bootstrap = (ROOT / "scripts" / "enterprise" / "bootstrap_staging_host.sh").read_text(encoding="utf-8")
    _require(
        bootstrap,
        (
            "--apply", "--authorized-key-file", "authorized SSH key before SSH hardening", "useradd", "passwd --lock", "PasswordAuthentication no", "PermitRootLogin no",
            "docker-compose-plugin", "ufw default deny incoming", "timedatectl set-ntp true",
            "/opt/mentorflow", "CONFIGURE_IF_ABSENT", "provider_api_calls", "credentials_written",
        ),
        "bootstrap_staging_host.sh",
    )
    if "racknerd" in bootstrap.casefold() or "curl |" in bootstrap:
        raise RuntimeError("bootstrap must remain vendor-neutral and cannot pipe remote scripts into a shell")

    preflight = (ROOT / "scripts" / "enterprise" / "staging_host_preflight.py").read_text(encoding="utf-8")
    _require(preflight, ("vcpu", "memory_headroom", "disk_free", "candidate_port", "docker", "compose", "dns_seam", "time_sync", "ipv6_review", "disk_writable", "LIVE_HOST"), "staging_host_preflight.py")
    builder = (ROOT / "scripts" / "enterprise" / "build_staging_release_package.py").read_text(encoding="utf-8")
    _require(builder, ("validate_release_manifest", "validate_runtime_images", "package_content_sha256", "E1_04", "provider_mode", "real_provider_calls", "compose.env"), "build_staging_release_package.py")
    orchestrator = (ROOT / "scripts" / "enterprise" / "staging_release_orchestrator.py").read_text(encoding="utf-8")
    deploy_body = orchestrator[orchestrator.index("def deploy_release"):orchestrator.index("def rollback_drill")]
    order = [deploy_body.index(token) for token in ("database_backup", 'runner.run("migration"', "migration_verify", "candidate_health", "_run_load(runner", "atomic_current_pointer")]
    if order != sorted(order):
        raise RuntimeError("deploy phase order must be backup -> migration -> verify -> health -> smoke -> switch")
    _require(orchestrator, ("MIGRATION_FAILED_STOPPED", "BACKUP_BEFORE_MIGRATE_FAIL_CLOSED", "approve_expanded_load", "tenant-isolation", "rollback-drill", "matching migration heads"), "staging_release_orchestrator.py")

    security = (ROOT / "scripts" / "enterprise" / "staging_security_runner.py").read_text(encoding="utf-8")
    _require(security, ("gitleaks", "github-secret-scanning", "open_ports", "admin_ports_not_exposed", "container_hardening", "tls", "security_headers", "default_credentials"), "staging_security_runner.py")
    evidence = (ROOT / "scripts" / "enterprise" / "collect_staging_evidence.py").read_text(encoding="utf-8")
    _require(evidence, ("EVIDENCE_CATEGORIES", "sensitive-looking file", "backup_refs", "otel_refs", "bundle_content_sha256", "release_package_sha256", "ZIP"), "collect_staging_evidence.py")

    workflow_text = (ROOT / ".github" / "workflows" / "staging-orchestrator.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    if not isinstance(workflow, dict) or "approved-staging" not in workflow.get("jobs", {}):
        raise RuntimeError("staging orchestrator workflow is invalid")
    approved = workflow["jobs"]["approved-staging"]
    if approved.get("environment") != "staging" or "self-hosted" not in approved.get("runs-on", []):
        raise RuntimeError("staging workflow must use the protected staging environment and self-hosted runner")
    _require(workflow_text, ("Host preflight", "Build exact release package", "Backup, migrate, deploy, smoke", "Expanded load and tenant isolation", "Staging security gates", "Collect release evidence bundle"), "staging-orchestrator.yml")
    if re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(password|token)\s*:\s*[^$\s{]", workflow_text, re.IGNORECASE):
        raise RuntimeError("workflow appears to contain a literal credential")

    docs = (ROOT / "docs" / "enterprise" / "staging-orchestrator.md").read_text(encoding="utf-8")
    _require(
        docs,
        (
            "RackNerd", "Ubuntu 24.04", "3 vCPU", "4 GB", "60 GB", "vendor-neutral",
            "minimal", "full", "INITIAL_STAGING_BUDGET", "atomic", "migration",
            "smoke", "average", "stress", "spike", "unauthorized_success_count = 0",
            "evidence", "rollback-drill", "E1_04_DEPENDENCY=NO", "OPTIONAL_UNTIL_E1_04",
            "LOCAL_WINDOWS_DOCKER = NOT_RUN", "REAL_LINUX_STAGING = NOT_RUN",
        ),
        "staging-orchestrator.md",
    )
    pending = load_json_object(ROOT / "reports" / "enterprise" / "machine" / "staging-orchestrator.pending.json")
    if pending.get("status") != "STAGING_EXECUTION_PENDING" or pending.get("e1_04_dependency") != "NO" or pending.get("real_provider_calls") != 0:
        raise RuntimeError("staging pending execution truth is invalid")
    if set(pending.get("gates", {}).values()) != {"NOT_RUN"}:
        raise RuntimeError("unexecuted staging gates must all remain NOT_RUN")
    load_core = (ROOT / "tests" / "load" / "lib" / "core.js").read_text(encoding="utf-8")
    _require(load_core, ("DOCUMENT_HOOK_ENABLED", "PROVIDER_ADAPTER=fake", "unauthorized_success_count"), "tests/load/lib/core.js")
    return {
        "status": "PASS",
        "host_contract": host["schema"],
        "bootstrap": "IMPLEMENTED",
        "compose_profiles": 2,
        "minimal_services": len(minimal),
        "full_services": len(full | set(overlay["services"])),
        "resource_budget": budget["status"],
        "smoke_checks": len(smoke["checks"]),
        "document_hook": hook["status"],
        "e1_04_dependency": "NO",
        "evidence_categories": 10,
        "live_staging": "NOT_RUN",
        "provider_calls": 0,
    }


def main() -> int:
    print(json.dumps(verify(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
