#!/usr/bin/env python3
"""Static and tool-aware acceptance gate for RP-05 staging assets."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import yaml


RESIDENT = {"postgres", "keycloak", "otel-collector", "backend", "worker", "frontend", "edge"}
ONESHOT = {"postgres-provision", "keycloak-ready", "migrate", "migration-ready", "otel-ready"}
BANNED = {"seaweedfs", "docling", "prometheus", "grafana", "tempo", "loki", "k6", "object-store"}
EXPECTED_SECRETS = {
    "postgres_admin_password", "postgres_migrator_password", "postgres_app_password",
    "keycloak_db_password", "keycloak_admin_password", "bff_client_secret",
    "worker_client_secret", "tls_certificate", "tls_private_key",
}


def _yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    document = yaml.compose(text)

    def reject_duplicates(node: yaml.Node) -> None:
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key, value in node.value:
                identity = str(getattr(key, "value", ""))
                if identity in seen:
                    raise ValueError(f"duplicate YAML key in {path}: {identity}")
                seen.add(identity)
                reject_duplicates(key)
                reject_duplicates(value)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                reject_duplicates(value)

    if document is None:
        raise ValueError(f"empty YAML document: {path}")
    reject_duplicates(document)
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return value


def _condition(compose: dict, service: str, dependency: str) -> str:
    value = compose["services"][service].get("depends_on", {}).get(dependency)
    return value.get("condition") if isinstance(value, dict) else ""


def verify(root: Path) -> dict:
    asset_root = root / "deploy" / "enterprise" / "staging" / "rp05"
    compose_path = asset_root / "compose.minimal.yml"
    compose_text = compose_path.read_text(encoding="utf-8")
    compose = _yaml(compose_path)
    services = compose.get("services", {})
    failures: list[str] = []
    checks: dict[str, str] = {}

    def gate(name: str, condition: bool, detail: str) -> None:
        checks[name] = "PASS" if condition else "FAIL"
        if not condition:
            failures.append(f"{name}: {detail}")

    gate("default_minimal_service_set", set(services) == RESIDENT | ONESHOT, "service set drifted")
    gate("no_optional_profiles", all("profiles" not in value for value in services.values()), "profiles make default graph ambiguous")
    gate("heavy_services_absent", not (set(services) & BANNED), "heavy service entered minimal graph")
    gate("only_edge_publishes_ports", all(name == "edge" or "ports" not in value for name, value in services.items()), "non-edge service publishes host ports")
    edge_ports = services.get("edge", {}).get("ports", [])
    gate("public_surface_http_https_only", set(edge_ports) == {"80:8080", "443:8443"}, "public ports are not exactly HTTP/HTTPS")
    gate("internal_network_isolated", compose.get("networks", {}).get("internal", {}).get("internal") is True, "internal network is not isolated")
    gate("external_file_secrets", set(compose.get("secrets", {})) == EXPECTED_SECRETS and all("file" in value for value in compose["secrets"].values()), "external secret declarations are incomplete")
    gate("postgres_persistent", "/postgres:/var/lib/postgresql/data" in json.dumps(services["postgres"].get("volumes", [])), "PostgreSQL data is not host-persistent")
    gate("postgres_healthy", "healthcheck" in services["postgres"], "PostgreSQL healthcheck missing")

    provision = (asset_root / "postgres-provision.sh").read_text(encoding="utf-8")
    gate("postgres_role_separation", all(token in provision for token in ("mentorflow_migrator", "mentorflow_app", "mentorflow_keycloak", "NOSUPERUSER", "NOBYPASSRLS", "ALTER DATABASE mentorflow OWNER TO mentorflow_migrator")), "role separation is incomplete")
    runtime = (asset_root / "runtime-entrypoint.py").read_text(encoding="utf-8")
    gate("canonical_migration_head", "0031_enterprise_tenancy_completion" in runtime and "--check-heads" in runtime, "canonical migration check missing")
    gate("app_role_fail_closed", "rolsuper, rolbypassrls" in runtime and "application role owns tables" in runtime, "application-role checks missing")

    keycloak_command = str(services["keycloak"].get("command", ""))
    gate("keycloak_production_start", "kc.sh start --import-realm" in keycloak_command and "start-dev" not in compose_text, "Keycloak is not production/import mode")
    realm = json.loads((root / "deploy" / "enterprise" / "config" / "keycloak-realm.json").read_text(encoding="utf-8"))
    clients = {item["clientId"]: item for item in realm.get("clients", [])}
    gate("keycloak_realm_clients", {"mentorflow-api", "mentorflow-web", "mentorflow-bff", "mentorflow-worker"} <= clients.keys() and clients["mentorflow-web"].get("publicClient") is True, "realm/client contract incomplete")

    gate("migration_dependency_graph", _condition(compose, "migrate", "postgres-provision") == "service_completed_successfully" and _condition(compose, "migration-ready", "migrate") == "service_completed_successfully", "migration gates are unordered")
    for service in ("backend", "worker"):
        gate(f"{service}_dependency_graph", all(_condition(compose, service, dependency) == "service_completed_successfully" for dependency in ("migration-ready", "keycloak-ready", "otel-ready")), f"{service} does not wait for all gates")
    gate("edge_dependency_graph", _condition(compose, "edge", "worker") == "service_healthy" and _condition(compose, "edge", "frontend") == "service_healthy", "edge can start before runtime health")
    gate("worker_current_entrypoint", "app.enterprise.worker_entry" in runtime and "MENTORFLOW_WORKER_RUNTIME_FACTORY" in compose_text and "worker-health" in runtime, "durable worker contract incomplete")

    otel = _yaml(asset_root / "otel-collector.yml")
    gate("otel_three_pipelines", set(otel.get("service", {}).get("pipelines", {})) == {"traces", "metrics", "logs"}, "OTel pipelines incomplete")
    gate("otel_health_extension", "health_check" in otel.get("service", {}).get("extensions", []) and "4317" in compose_text and "4318" in compose_text and "13133" in compose_text, "OTel ingress/health contract incomplete")

    edge = (asset_root / "edge-https.conf").read_text(encoding="utf-8")
    gate("tls_ingress", all(token in edge for token in ("listen 8443 ssl", "tls_certificate", "tls_private_key", "Strict-Transport-Security", "proxy_buffering off")), "TLS/reverse-proxy seam incomplete")
    orchestrator = (root / "scripts" / "enterprise" / "rp05_staging.py").read_text(encoding="utf-8")
    gate("host_layout", all(f'"{name}"' in orchestrator for name in ("releases", "config", "secrets", "data", "backups", "evidence", "logs")), "host layout incomplete")
    gate("atomic_activation_rollback", all(token in orchestrator for token in ("symlink_to", "os.replace", "rollback", '"stop"')), "activation/rollback contract missing")
    gate("non_destructive_orchestrator", not any(token in orchestrator for token in ("down -v", "rm -rf", "volume rm", "ufw", "iptables", "ssh ")), "destructive or remote command found")

    builder = (root / "scripts" / "enterprise" / "build_rp05_staging_release.py").read_text(encoding="utf-8")
    gate("immutable_images", "@sha256:" in builder and "DIGEST.fullmatch" in builder and "source checkout must be clean" in builder, "image/source immutability checks missing")
    workflow = (root / ".github" / "workflows" / "container-release.yml").read_text(encoding="utf-8")
    gate("container_release_digest_input", "rp05-application-images.json" in workflow and "all(.images[]; test" in workflow and all(key in workflow for key in ("MENTORFLOW_BACKEND_IMAGE", "MENTORFLOW_FRONTEND_IMAGE", "MENTORFLOW_WORKER_IMAGE")), "container release does not emit the RP-05 application digest input")
    scan_paths = [path for path in asset_root.rglob("*") if path.is_file()] + [
        root / "scripts" / "enterprise" / "build_rp05_staging_release.py",
        root / "scripts" / "enterprise" / "rp05_staging.py",
        root / "scripts" / "enterprise" / "verify_rp05_staging_assets.py",
        root / ".github" / "workflows" / "container-release.yml",
    ]
    tracked = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in scan_paths)
    plaintext_patterns = (
        r"(?im)^\s*(?:password|secret|token|private_key)\s*[:=]\s*['\"]?[A-Za-z0-9+/]{12,}",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    )
    gate("secret_scan", not any(re.search(pattern, tracked) for pattern in plaintext_patterns), "plaintext credential material detected")

    docker = shutil.which("docker")
    if docker:
        checks["docker_compose_config"] = "AVAILABLE_NOT_RUN_NEEDS_OPERATOR_DIGESTS_AND_SECRETS"
    else:
        checks["docker_compose_config"] = "NOT_RUN_TOOL_UNAVAILABLE"
    checks["real_tls_staging_gate"] = "PENDING"
    checks["real_minimal_stack_staging_gate"] = "PENDING"
    return {
        "schema_version": "mentorflow.rp05-static-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "checks": checks,
        "failures": failures,
        "secrets_exposed": False if checks.get("secret_scan") == "PASS" else None,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    payload = verify(root)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(json.dumps({"schema_version": "mentorflow.rp05-static-verification.v1", "status": "FAIL", "failures": [str(exc)[:300]]}, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(1)
