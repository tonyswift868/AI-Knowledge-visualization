#!/usr/bin/env python3
"""Static and tool-aware acceptance gate for RP-05 staging assets."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


RESIDENT = {
    "postgres",
    "keycloak",
    "otel-collector",
    "backend",
    "oidc-loopback-proxy",
    "worker",
    "frontend",
    "edge",
}
ONESHOT = {"postgres-provision", "keycloak-ready", "migrate", "migration-ready", "otel-ready"}
BANNED = {"seaweedfs", "docling", "prometheus", "grafana", "tempo", "loki", "k6", "object-store"}
EXPECTED_SECRETS = {
    "postgres_admin_password", "postgres_migrator_password", "postgres_app_password",
    "keycloak_db_password", "keycloak_admin_password", "bff_client_secret",
    "worker_client_secret", "tls_certificate", "tls_private_key",
}
OIDC_NGINX_REQUIRED_WRITABLE_PATHS = ("/tmp", "/var/cache/nginx")
OIDC_NGINX_EXPECTED_TMPFS = {
    "/tmp": "/tmp:size=16m,mode=1777",
    "/var/cache/nginx": "/var/cache/nginx:size=8m,mode=0750,uid=101,gid=101",
}


_RUNTIME_BOOTSTRAP_HARNESS = r"""
import importlib.util
import json
import sys
import types

runtime_path, mode, repetitions = sys.argv[1], sys.argv[2], int(sys.argv[3])
events = []
spec = importlib.util.spec_from_file_location("rp05_runtime_entrypoint", runtime_path)
if spec is None or spec.loader is None:
    raise SystemExit("runtime entrypoint could not be loaded")
runtime_entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_entrypoint)
runtime_entrypoint._secret = lambda _name: "offline-test-secret"


def run_command(argv, env, *, quiet=False):
    del quiet
    app_url = env.get("MENTORFLOW_DATABASE_URL", "")
    migration_url = env.get("MENTORFLOW_MIGRATION_DATABASE_URL", "")
    if not app_url.startswith("postgresql+psycopg://mentorflow_app:"):
        return 81
    step = "schema-apply" if argv[-1] == "schema-apply" else "alembic-current"
    if step == "schema-apply" and not migration_url.startswith(
        "postgresql+psycopg://mentorflow_migrator:"
    ):
        return 82
    events.append(step)
    if mode == "apply-failure" and step == "schema-apply":
        return 23
    return 0


runtime_entrypoint._run_command = run_command
runtime_entrypoint._exec = lambda _argv, _env: events.append("runtime-exec") or 97

app_module = types.ModuleType("app")
app_module.__path__ = []
orm_module = types.ModuleType("app.orm")


class Engine:
    def dispose(self):
        events.append("engine-dispose")


def configure_enterprise_orm(env):
    if not env.get("MENTORFLOW_DATABASE_URL", "").startswith(
        "postgresql+psycopg://mentorflow_app:"
    ) or "MENTORFLOW_MIGRATION_DATABASE_URL" in env:
        raise RuntimeError("application role binding failed")
    events.append("app-engine")
    return Engine()


orm_module.configure_enterprise_orm = configure_enterprise_orm
app_module.orm = orm_module
enterprise_module = types.ModuleType("app.enterprise")
enterprise_module.__path__ = []
jobs_module = types.ModuleType("app.enterprise.jobs_v1")
jobs_module.__path__ = []
worker_runtime_module = types.ModuleType("app.enterprise.jobs_v1.runtime")


def verify_worker_database(_engine):
    events.append("schema-verify")
    if mode == "verify-failure":
        raise RuntimeError("offline verify failure")


worker_runtime_module.verify_worker_database = verify_worker_database
sys.modules.update(
    {
        "app": app_module,
        "app.orm": orm_module,
        "app.enterprise": enterprise_module,
        "app.enterprise.jobs_v1": jobs_module,
        "app.enterprise.jobs_v1.runtime": worker_runtime_module,
    }
)

codes = []
sys.argv = [runtime_path, "verify"]
for _ in range(repetitions):
    try:
        codes.append(runtime_entrypoint.main())
    except Exception as exc:
        events.append(f"exception:{type(exc).__name__}")
        codes.append(31)
        break

print(json.dumps({"codes": codes, "events": events}, sort_keys=True))
raise SystemExit(next((code for code in codes if code), 0))
"""


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


def _tmpfs_entries(value: object) -> list[dict[str, object]]:
    entries = value if isinstance(value, list) else []
    parsed_entries: list[dict[str, object]] = []
    for raw in entries:
        if not isinstance(raw, str):
            parsed_entries.append(
                {"raw": raw, "target": None, "options": {}, "flags": []}
            )
            continue
        target, separator, option_text = raw.partition(":")
        options: dict[str, str] = {}
        flags: list[str] = []
        if separator and target.startswith("/"):
            for token in option_text.split(","):
                key, equals, option_value = token.partition("=")
                if equals:
                    options[key] = option_value
                elif token:
                    flags.append(token)
        else:
            target = ""
        parsed_entries.append(
            {
                "raw": raw,
                "target": target or None,
                "options": options,
                "flags": flags,
            }
        )
    return parsed_entries


def _oidc_loopback_tmpfs_contract(
    value: object, *, target: str = "/tmp"
) -> dict[str, object]:
    entries = _tmpfs_entries(value)
    matching = [entry for entry in entries if entry["target"] == target]
    selected = matching[0] if len(matching) == 1 else {}
    options = selected.get("options", {})
    if not isinstance(options, dict):
        options = {}
    return {
        "entry_count": len(matching),
        "total_entry_count": len(entries),
        "target": selected.get("target"),
        "size": options.get("size"),
        "mode": options.get("mode"),
        "uid": options.get("uid"),
        "gid": options.get("gid"),
        "raw": selected.get("raw"),
        "targets": sorted(
            entry["target"] for entry in entries if isinstance(entry["target"], str)
        ),
        "split_mode_entry_absent": all(
            entry["raw"] != "mode=1777" for entry in entries
        ),
    }


def _volume_binding(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        mount_type = str(value.get("type") or "volume")
        target = str(value.get("target") or "")
        read_only = bool(value.get("read_only"))
        return {"type": mount_type, "target": target, "read_only": read_only}
    if not isinstance(value, str):
        return {"type": "unknown", "target": "", "read_only": False}
    parts = value.split(":")
    target_index = next(
        (index for index, part in enumerate(parts[1:], start=1) if part.startswith("/")),
        -1,
    )
    if target_index < 0:
        return {"type": "unknown", "target": "", "read_only": False}
    source = ":".join(parts[:target_index])
    target = parts[target_index]
    options = parts[target_index + 1 :]
    mount_type = (
        "bind"
        if source.startswith((".", "/", "${")) or re.match(r"^[A-Za-z]:", source)
        else "volume"
    )
    return {"type": mount_type, "target": target, "read_only": "ro" in options}


def _mount_covers_path(mount_target: str, required_path: str) -> bool:
    normalized = mount_target.rstrip("/") or "/"
    return normalized == "/" or required_path == normalized or required_path.startswith(
        normalized + "/"
    )


def _oidc_loopback_runtime_storage_contract(service: object) -> dict[str, object]:
    service_map = service if isinstance(service, dict) else {}
    entries = _tmpfs_entries(service_map.get("tmpfs"))
    tmpfs_paths = {
        entry["target"] for entry in entries if isinstance(entry["target"], str)
    }
    volume_bindings = [_volume_binding(value) for value in service_map.get("volumes", [])]
    runtime_bindings = [
        binding
        for binding in volume_bindings
        if isinstance(binding["target"], str)
        and any(
            _mount_covers_path(binding["target"], required)
            for required in OIDC_NGINX_REQUIRED_WRITABLE_PATHS
        )
    ]
    persistent_runtime_volume_used = any(
        binding["type"] == "volume" for binding in runtime_bindings
    )
    host_bind_runtime_path_used = any(
        binding["type"] == "bind" for binding in runtime_bindings
    )
    writable_etc_bind = any(
        isinstance(binding["target"], str)
        and (
            binding["target"] == "/etc/nginx"
            or binding["target"].startswith("/etc/nginx/")
        )
        and binding["read_only"] is not True
        for binding in volume_bindings
    )
    writable_etc_tmpfs = any(
        _mount_covers_path(path, "/etc/nginx")
        or path == "/etc/nginx"
        or path.startswith("/etc/nginx/")
        for path in tmpfs_paths
    )
    missing = sorted(set(OIDC_NGINX_REQUIRED_WRITABLE_PATHS) - tmpfs_paths)
    unexpected = sorted(tmpfs_paths - set(OIDC_NGINX_REQUIRED_WRITABLE_PATHS))
    raw_entries = {entry["raw"] for entry in entries if isinstance(entry["raw"], str)}
    expected_raw = set(OIDC_NGINX_EXPECTED_TMPFS.values())
    exact_tmpfs_contract = len(entries) == len(expected_raw) and raw_entries == expected_raw
    return {
        "read_only_rootfs": service_map.get("read_only") is True,
        "privileged": service_map.get("privileged") is True,
        "required_writable_paths": list(OIDC_NGINX_REQUIRED_WRITABLE_PATHS),
        "runtime_tmpfs_paths": sorted(tmpfs_paths),
        "missing_required_writable_paths": missing,
        "unexpected_runtime_tmpfs_paths": unexpected,
        "exact_tmpfs_contract": exact_tmpfs_contract,
        "persistent_runtime_volume_used": persistent_runtime_volume_used,
        "host_bind_runtime_path_used": host_bind_runtime_path_used,
        "unauthorized_writable_etc_nginx": writable_etc_bind or writable_etc_tmpfs,
        "run_tmpfs_present": "/run" in tmpfs_paths,
        "runtime_storage_contract_pass": all(
            (
                service_map.get("read_only") is True,
                service_map.get("privileged") is not True,
                not missing,
                not unexpected,
                exact_tmpfs_contract,
                not persistent_runtime_volume_used,
                not host_bind_runtime_path_used,
                not writable_etc_bind,
                not writable_etc_tmpfs,
                "/run" not in tmpfs_paths,
            )
        ),
    }


def _runtime_bootstrap_probe(
    runtime_path: Path,
    mode: str,
    *,
    repetitions: int = 1,
) -> tuple[int, dict[str, object], str]:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                _RUNTIME_BOOTSTRAP_HARNESS,
                str(runtime_path),
                mode,
                str(repetitions),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, {}, f"{type(exc).__name__}: {exc}"
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        payload = {}
    detail = completed.stderr.strip() or completed.stdout.strip()
    return completed.returncode, payload, detail[-800:]


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
    runtime_path = asset_root / "runtime-entrypoint.py"
    runtime = runtime_path.read_text(encoding="utf-8")
    gate("canonical_migration_head", "0031_enterprise_tenancy_completion" in runtime and "--check-heads" in runtime, "canonical migration check missing")
    gate("worker_schema_bootstrap_wired", all(token in runtime for token in ("schema-apply", "verify_worker_database", "mentorflow_migrator", "mentorflow_app")), "Procrastinate apply/verify is not wired to the migration-ready gate")
    gate("app_role_fail_closed", "verify_worker_database(engine)" in runtime and "orm.configure_enterprise_orm(app_env)" in runtime, "application-role verification contract missing")

    worker_entry = (root / "backend" / "app" / "enterprise" / "worker_entry.py").read_text(encoding="utf-8")
    worker_schema = (root / "backend" / "app" / "enterprise" / "jobs_v1" / "postgres_schema.py").read_text(encoding="utf-8")
    gate("existing_idempotent_schema_apply_contract", "schema-apply" in worker_entry and "apply_worker_schema" in worker_entry and ".schema_manager.apply_schema()" in worker_schema, "existing idempotent worker schema contract is not reused")

    success_code, success_payload, success_detail = _runtime_bootstrap_probe(
        runtime_path, "success", repetitions=2
    )
    expected_success_events = [
        "alembic-current",
        "schema-apply",
        "app-engine",
        "schema-verify",
        "engine-dispose",
    ] * 2
    gate("schema_bootstrap_order", success_code == 0 and success_payload.get("events") == expected_success_events, f"bootstrap order mismatch: {success_detail}")
    gate("schema_apply_idempotent_repeated_startup", success_payload.get("codes") == [0, 0] and success_payload.get("events", []).count("schema-apply") == 2, f"repeated startup was not safe: {success_detail}")

    apply_code, apply_payload, apply_detail = _runtime_bootstrap_probe(
        runtime_path, "apply-failure"
    )
    gate("schema_apply_failure_fail_closed", apply_code == 23 and apply_payload.get("events") == ["alembic-current", "schema-apply"], f"schema apply failure did not stop bootstrap: {apply_detail}")

    verify_code, verify_payload, verify_detail = _runtime_bootstrap_probe(
        runtime_path, "verify-failure"
    )
    verify_events = verify_payload.get("events", [])
    expected_verify_failure_events = expected_success_events[:5] + [
        "exception:RuntimeError"
    ]
    gate("schema_verify_failure_fail_closed", verify_code == 31 and verify_events == expected_verify_failure_events and "runtime-exec" not in verify_events, f"schema verify failure did not stop bootstrap: {verify_detail}")

    keycloak_command = str(services["keycloak"].get("command", ""))
    gate("keycloak_production_start", "kc.sh start --import-realm" in keycloak_command and "start-dev" not in compose_text, "Keycloak is not production/import mode")
    realm = json.loads((root / "deploy" / "enterprise" / "config" / "keycloak-realm.json").read_text(encoding="utf-8"))
    clients = {item["clientId"]: item for item in realm.get("clients", [])}
    gate("keycloak_realm_clients", {"mentorflow-api", "mentorflow-web", "mentorflow-bff", "mentorflow-worker"} <= clients.keys() and clients["mentorflow-web"].get("publicClient") is True, "realm/client contract incomplete")
    gate(
        "direct_access_grants_disabled",
        all(client.get("directAccessGrantsEnabled") is False for client in clients.values()),
        "Direct Access Grants must remain disabled for every Keycloak client",
    )

    gate("database_reachable_before_migration", _condition(compose, "postgres-provision", "postgres") == "service_healthy", "database health does not gate provisioning")
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
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    nginx_version_match = re.search(
        r"^ARG NGINX_VERSION=(\d+)\.(\d+)\.(\d+)\s*$", dockerfile, re.MULTILINE
    )
    nginx_version = (
        tuple(int(value) for value in nginx_version_match.groups())
        if nginx_version_match
        else ()
    )
    nginx_dynamic_resolver_capable = nginx_version >= (1, 27, 3)
    gate(
        "edge_nginx_dynamic_resolver_capability",
        nginx_dynamic_resolver_capable,
        "frontend/edge NGINX must be 1.27.3 or newer for upstream resolve support",
    )
    backend_upstream = re.search(
        r"upstream\s+mentorflow_backend\s*\{(?P<body>.*?)\}", edge, re.DOTALL
    )
    api_location = re.search(r"location\s+/api/\s*\{(?P<body>.*?)\}", edge, re.DOTALL)
    upstream_body = backend_upstream.group("body") if backend_upstream else ""
    api_body = api_location.group("body") if api_location else ""
    hardcoded_proxy_ip = re.search(
        r"proxy_pass\s+https?://(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", edge
    )
    dynamic_backend_resolution = all(
        (
            backend_upstream is not None,
            re.search(r"\bzone\s+mentorflow_backend\s+\d+[kKmM]?\s*;", upstream_body)
            is not None,
            re.search(
                r"\bresolver\s+127\.0\.0\.11\s+valid=\d+[smh]\s+ipv6=off\s*;",
                upstream_body,
            )
            is not None,
            re.search(r"\bresolver_timeout\s+\d+[smh]\s*;", upstream_body)
            is not None,
            re.search(r"\bserver\s+backend:8000\s+resolve\s*;", upstream_body)
            is not None,
            re.search(r"\bproxy_pass\s+http://mentorflow_backend\s*;", api_body)
            is not None,
            "proxy_pass http://backend:8000" not in edge,
            hardcoded_proxy_ip is None,
            nginx_dynamic_resolver_capable,
        )
    )
    gate(
        "EDGE_BACKEND_DYNAMIC_RESOLUTION_CONTRACT",
        dynamic_backend_resolution,
        "edge must use Docker DNS and an upstream resolve server for backend without a fixed container IP",
    )
    oidc_proxy_path = asset_root / "oidc-loopback-proxy.conf"
    oidc_proxy = oidc_proxy_path.read_text(encoding="utf-8")
    oidc_service = services.get("oidc-loopback-proxy", {})
    oidc_tmpfs = _oidc_loopback_tmpfs_contract(oidc_service.get("tmpfs"))
    oidc_cache_tmpfs = _oidc_loopback_tmpfs_contract(
        oidc_service.get("tmpfs"), target="/var/cache/nginx"
    )
    oidc_runtime_storage = _oidc_loopback_runtime_storage_contract(oidc_service)
    frontend_stage = dockerfile.split(
        "FROM ${NGINX_BASE_IMAGE} AS frontend-image", 1
    )[-1]
    nginx_image_contract = all(
        (
            nginx_version == (1, 28, 0),
            "ARG NGINX_BASE_IMAGE=nginx:${NGINX_VERSION}-alpine" in dockerfile,
            "FROM ${NGINX_BASE_IMAGE} AS frontend-image" in dockerfile,
            re.search(r"^USER 101:101\s*$", frontend_stage, re.MULTILINE)
            is not None,
            oidc_service.get("image")
            == "${MENTORFLOW_FRONTEND_IMAGE:?MENTORFLOW_FRONTEND_IMAGE is required}",
            oidc_service.get("command") == ["nginx", "-g", "daemon off;"],
            "entrypoint" not in oidc_service,
        )
    )
    nginx_writable_path_audit_complete = all(
        (
            "pid /tmp/nginx.pid;" in oidc_proxy,
            "client_body_temp_path /tmp/client_body;" in oidc_proxy,
            "proxy_temp_path /tmp/proxy;" in oidc_proxy,
            "fastcgi_temp_path" not in oidc_proxy,
            "uwsgi_temp_path" not in oidc_proxy,
            "scgi_temp_path" not in oidc_proxy,
            "access_log off;" in oidc_proxy,
            "error_log /dev/stderr warn;" in oidc_proxy,
            "lock_file" not in oidc_proxy,
            oidc_service.get("volumes")
            == ["./oidc-loopback-proxy.conf:/etc/nginx/nginx.conf:ro"],
            nginx_image_contract,
        )
    )
    nginx_path_audit = {
        "/tmp": {
            "used_by_exact_service": True,
            "write_required": True,
            "already_writable": True,
            "fix_required": False,
            "evidence": "pid, client_body_temp_path, and proxy_temp_path are explicitly under /tmp",
        },
        "/var/cache/nginx": {
            "used_by_exact_service": True,
            "write_required": True,
            "already_writable": False,
            "fix_required": True,
            "evidence": "unoverridden FastCGI, uWSGI, and SCGI module temp directories share the image cache root; exact R57 startup failed at fastcgi_temp",
        },
        "/run": {
            "used_by_exact_service": True,
            "write_required": False,
            "already_writable": False,
            "fix_required": False,
            "evidence": "TLS certificate and key are read-only secrets; PID is explicitly /tmp/nginx.pid",
        },
        "/var/run": {
            "used_by_exact_service": False,
            "write_required": False,
            "already_writable": False,
            "fix_required": False,
            "evidence": "PID is explicitly under /tmp and Linux atomic accept locking requires no lock file",
        },
        "/etc/nginx": {
            "used_by_exact_service": True,
            "write_required": False,
            "already_writable": False,
            "fix_required": False,
            "evidence": "the exact nginx.conf is mounted read-only and the inherited entrypoint reached nginx initialization under the R57 runtime",
        },
    }
    gate(
        "OIDC_NGINX_IMAGE_RUNTIME_CONTRACT",
        nginx_image_contract,
        "OIDC bridge must retain the nginx:1.28.0-alpine frontend image running as UID/GID 101",
    )
    gate(
        "READ_ONLY_ROOTFS",
        oidc_runtime_storage["read_only_rootfs"] is True,
        "oidc-loopback-proxy read_only root filesystem must remain enabled",
    )
    gate(
        "NGINX_WRITABLE_PATH_AUDIT",
        nginx_writable_path_audit_complete,
        "exact PID, temp-directory, log, image-user, entrypoint, and config-mount paths are not fully classified",
    )
    gate(
        "OIDC_LOOPBACK_TMPFS_ENTRY_COUNT",
        oidc_tmpfs["entry_count"] == 1,
        "oidc-loopback-proxy must contain exactly one /tmp scalar entry",
    )
    gate(
        "OIDC_LOOPBACK_TMPFS_TARGET",
        oidc_tmpfs["target"] == "/tmp",
        "oidc-loopback-proxy tmpfs target must remain /tmp",
    )
    gate(
        "OIDC_LOOPBACK_TMPFS_SIZE",
        oidc_tmpfs["size"] == "16m",
        "oidc-loopback-proxy tmpfs size must remain 16m",
    )
    gate(
        "OIDC_LOOPBACK_TMPFS_MODE",
        oidc_tmpfs["mode"] == "1777",
        "oidc-loopback-proxy tmpfs mode must remain 1777",
    )
    gate(
        "FORBIDDEN_SPLIT_ENTRY_MODE_1777",
        oidc_tmpfs["split_mode_entry_absent"] is True,
        "mode=1777 must not parse as a separate tmpfs mount entry",
    )
    gate(
        "OIDC_LOOPBACK_TMPFS_SINGLE_SCALAR",
        oidc_tmpfs["raw"] == OIDC_NGINX_EXPECTED_TMPFS["/tmp"]
        and oidc_tmpfs["entry_count"] == 1,
        "oidc-loopback-proxy tmpfs must preserve the exact one-scalar contract",
    )
    gate(
        "NGINX_RUNTIME_REQUIRED_WRITABLE_PATHS",
        oidc_runtime_storage["required_writable_paths"]
        == list(OIDC_NGINX_REQUIRED_WRITABLE_PATHS),
        "required writable paths must remain the exact audited /tmp and /var/cache/nginx set",
    )
    gate(
        "NGINX_RUNTIME_TMPFS_PATHS",
        oidc_runtime_storage["runtime_tmpfs_paths"]
        == list(OIDC_NGINX_REQUIRED_WRITABLE_PATHS),
        "runtime tmpfs paths must exactly cover the audited writable-path set",
    )
    gate(
        "NGINX_CACHE_TMPFS_TARGET",
        oidc_cache_tmpfs["entry_count"] == 1
        and oidc_cache_tmpfs["target"] == "/var/cache/nginx",
        "one dedicated /var/cache/nginx tmpfs is required",
    )
    gate(
        "NGINX_CACHE_TMPFS_SIZE",
        oidc_cache_tmpfs["size"] == "8m",
        "OIDC Nginx cache tmpfs must remain bounded to 8m",
    )
    gate(
        "NGINX_CACHE_TMPFS_MODE_OWNERSHIP",
        oidc_cache_tmpfs["mode"] == "0750"
        and oidc_cache_tmpfs["uid"] == "101"
        and oidc_cache_tmpfs["gid"] == "101",
        "cache tmpfs must be owned by the exact Nginx UID/GID 101 with mode 0750",
    )
    gate(
        "MISSING_REQUIRED_WRITABLE_PATHS",
        oidc_runtime_storage["missing_required_writable_paths"] == [],
        "one or more exact Nginx runtime write paths are not backed by tmpfs",
    )
    gate(
        "PERSISTENT_RUNTIME_VOLUME_USED",
        oidc_runtime_storage["persistent_runtime_volume_used"] is False,
        "persistent named volumes cannot provide temporary Nginx runtime state",
    )
    gate(
        "HOST_BIND_RUNTIME_PATH_USED",
        oidc_runtime_storage["host_bind_runtime_path_used"] is False,
        "host bind mounts cannot provide temporary Nginx runtime state",
    )
    gate(
        "UNAUTHORIZED_WRITABLE_ETC_NGINX",
        oidc_runtime_storage["unauthorized_writable_etc_nginx"] is False,
        "/etc/nginx must remain read-only",
    )
    gate(
        "NGINX_RUNTIME_TMPFS_EXACT_CONTRACT",
        oidc_runtime_storage["runtime_storage_contract_pass"] is True,
        "OIDC Nginx runtime storage must be exact, bounded, ephemeral, and read-only-rootfs compatible",
    )
    gate(
        "TLS_SECRETS_REMAIN_VISIBLE_WITHOUT_RUN_TMPFS",
        oidc_runtime_storage["run_tmpfs_present"] is False,
        "a broad /run tmpfs is not authorized because TLS secrets live below /run/secrets",
    )
    malformed_tmpfs = yaml.safe_load(
        "tmpfs: [/tmp:size=16m,mode=1777]\n"
    )["tmpfs"]
    malformed_contract = _oidc_loopback_tmpfs_contract(malformed_tmpfs)
    gate(
        "OIDC_LOOPBACK_TMPFS_OLD_FORM_NEGATIVE_CONTROL",
        malformed_contract["total_entry_count"] == 2
        and malformed_contract["entry_count"] == 1
        and malformed_contract["mode"] is None
        and malformed_contract["split_mode_entry_absent"] is False,
        "old flow-sequence form must remain a demonstrated split-entry negative control",
    )
    only_tmp_contract = _oidc_loopback_runtime_storage_contract(
        {
            "read_only": True,
            "tmpfs": [OIDC_NGINX_EXPECTED_TMPFS["/tmp"]],
            "volumes": ["./oidc-loopback-proxy.conf:/etc/nginx/nginx.conf:ro"],
        }
    )
    read_only_false_contract = _oidc_loopback_runtime_storage_contract(
        {
            "read_only": False,
            "tmpfs": list(OIDC_NGINX_EXPECTED_TMPFS.values()),
            "volumes": ["./oidc-loopback-proxy.conf:/etc/nginx/nginx.conf:ro"],
        }
    )
    persistent_volume_contract = _oidc_loopback_runtime_storage_contract(
        {
            "read_only": True,
            "tmpfs": list(OIDC_NGINX_EXPECTED_TMPFS.values()),
            "volumes": [
                "./oidc-loopback-proxy.conf:/etc/nginx/nginx.conf:ro",
                "oidc-nginx-runtime:/var/cache/nginx",
            ],
        }
    )
    host_bind_contract = _oidc_loopback_runtime_storage_contract(
        {
            "read_only": True,
            "tmpfs": list(OIDC_NGINX_EXPECTED_TMPFS.values()),
            "volumes": [
                "./oidc-loopback-proxy.conf:/etc/nginx/nginx.conf:ro",
                "./oidc-nginx-runtime:/var/cache/nginx",
            ],
        }
    )
    gate(
        "R55_ONLY_TMP_NEGATIVE_CONTROL",
        only_tmp_contract["runtime_storage_contract_pass"] is False
        and only_tmp_contract["missing_required_writable_paths"]
        == ["/var/cache/nginx"],
        "the previous read-only R55 state with only /tmp must fail path coverage",
    )
    gate(
        "READ_ONLY_FALSE_NEGATIVE_CONTROL",
        read_only_false_contract["runtime_storage_contract_pass"] is False
        and read_only_false_contract["read_only_rootfs"] is False,
        "read_only:false must fail the runtime storage contract",
    )
    gate(
        "PERSISTENT_VOLUME_NEGATIVE_CONTROL",
        persistent_volume_contract["runtime_storage_contract_pass"] is False
        and persistent_volume_contract["persistent_runtime_volume_used"] is True,
        "a named runtime volume must fail the runtime storage contract",
    )
    gate(
        "HOST_BIND_NEGATIVE_CONTROL",
        host_bind_contract["runtime_storage_contract_pass"] is False
        and host_bind_contract["host_bind_runtime_path_used"] is True,
        "a host bind for runtime state must fail the runtime storage contract",
    )
    oidc_upstream = re.search(
        r"upstream\s+mentorflow_keycloak\s*\{(?P<body>.*?)\}",
        oidc_proxy,
        re.DOTALL,
    )
    oidc_upstream_body = oidc_upstream.group("body") if oidc_upstream else ""
    hardcoded_oidc_ip = re.search(
        r"(?:proxy_pass\s+https?://|server\s+)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?",
        oidc_proxy,
    )
    backend_dependency = oidc_service.get("depends_on", {}).get("backend", {})
    oidc_lifecycle_bound = (
        isinstance(backend_dependency, dict)
        and backend_dependency.get("condition") == "service_healthy"
        and backend_dependency.get("restart") is True
        and oidc_service.get("network_mode") == "service:backend"
        and "networks" not in oidc_service
        and "ports" not in oidc_service
        and _condition(compose, "edge", "oidc-loopback-proxy") == "service_healthy"
    )
    gate(
        "OIDC_PUBLIC_ISSUER_PRESERVED",
        services.get("backend", {}).get("environment", {}).get(
            "MENTORFLOW_KEYCLOAK_ISSUER"
        )
        == "${MENTORFLOW_PUBLIC_ORIGIN:?MENTORFLOW_PUBLIC_ORIGIN is required}/realms/mentorflow",
        "backend public issuer must remain derived from the operator-approved public origin",
    )
    gate(
        "OIDC_LOOPBACK_PROXY_LIFECYCLE_CONTRACT",
        oidc_lifecycle_bound,
        "OIDC bridge must share the backend namespace, restart after backend replacement, and gate edge startup",
    )
    gate(
        "OIDC_LOOPBACK_PROXY_TLS_CONTRACT",
        all(
            token in oidc_proxy
            for token in (
                "listen 127.0.0.1:18080 ssl",
                "ssl_certificate /run/secrets/tls_certificate",
                "ssl_certificate_key /run/secrets/tls_private_key",
                "ssl_protocols TLSv1.2 TLSv1.3",
            )
        )
        and set(oidc_service.get("secrets", []))
        == {"tls_certificate", "tls_private_key"},
        "OIDC bridge must expose only the verified TLS loopback listener",
    )
    gate(
        "OIDC_KEYCLOAK_DYNAMIC_RESOLUTION_CONTRACT",
        all(
            (
                nginx_dynamic_resolver_capable,
                oidc_upstream is not None,
                re.search(
                    r"\bzone\s+mentorflow_keycloak\s+\d+[kKmM]?\s*;",
                    oidc_upstream_body,
                )
                is not None,
                re.search(
                    r"\bresolver\s+127\.0\.0\.11\s+valid=\d+[smh]\s+ipv6=off\s*;",
                    oidc_upstream_body,
                )
                is not None,
                re.search(r"\bresolver_timeout\s+\d+[smh]\s*;", oidc_upstream_body)
                is not None,
                re.search(r"\bserver\s+keycloak:8080\s+resolve\s*;", oidc_upstream_body)
                is not None,
                "proxy_pass http://mentorflow_keycloak;" in oidc_proxy,
                hardcoded_oidc_ip is None,
            )
        ),
        "OIDC bridge must re-resolve the Keycloak service name without a fixed container IP",
    )
    orchestrator = (root / "scripts" / "enterprise" / "rp05_staging.py").read_text(encoding="utf-8")
    gate("host_layout", all(f'"{name}"' in orchestrator for name in ("releases", "config", "secrets", "data", "backups", "evidence", "logs")), "host layout incomplete")
    gate("atomic_activation_rollback", all(token in orchestrator for token in ("symlink_to", "os.replace", "rollback", '"stop"')), "activation/rollback contract missing")
    gate("non_destructive_orchestrator", not any(token in orchestrator for token in ("down -v", "rm -rf", "volume rm", "ufw", "iptables", "ssh ")), "destructive or remote command found")

    builder = (root / "scripts" / "enterprise" / "build_rp05_staging_release.py").read_text(encoding="utf-8")
    gate("immutable_images", "@sha256:" in builder and "DIGEST.fullmatch" in builder and "source checkout must be clean" in builder, "image/source immutability checks missing")
    gate(
        "oidc_loopback_proxy_release_asset",
        'source_assets / "oidc-loopback-proxy.conf": "oidc-loopback-proxy.conf"'
        in builder,
        "OIDC loopback proxy configuration is missing from immutable release assets",
    )
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
        "oidc_loopback_nginx_runtime": {
            "image": "nginx:1.28.0-alpine via frontend-image",
            "image_user": "101:101",
            "pid_path": "/tmp/nginx.pid",
            "client_body_temp_path": "/tmp/client_body",
            "proxy_temp_path": "/tmp/proxy",
            "fastcgi_temp_path": "/var/cache/nginx/fastcgi_temp",
            "uwsgi_temp_path": "/var/cache/nginx/uwsgi_temp",
            "scgi_temp_path": "/var/cache/nginx/scgi_temp",
            "access_log_path": "off",
            "error_log_path": "/dev/stderr",
            "path_audit": nginx_path_audit,
            **oidc_runtime_storage,
        },
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
