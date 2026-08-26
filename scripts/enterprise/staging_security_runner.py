from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from staging_contracts import ContractError, file_sha256, load_json_object, validate_host_config, write_atomic_json
from staging_release_orchestrator import _compose_base, load_package


LISTEN = re.compile(r"^(?P<address>\[[^]]+\]|[^:]+):(?P<port>\d+)$")
PLACEHOLDERS = {"admin", "password", "changeme", "change-me", "mentorflow", "default", "secret"}
REQUIRED_HEADERS = {
    "strict-transport-security",
    "x-content-type-options",
    "referrer-policy",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_secret_scan_manifest(payload: dict[str, Any], root: Path) -> list[dict[str, str]]:
    if payload.get("schema") != "mentorflow.secret-scan-evidence.v1" or payload.get("status") != "PASS":
        raise ContractError("a PASS machine-readable secret scan manifest is required")
    if payload.get("scanner") not in {"gitleaks", "github-secret-scanning"}:
        raise ContractError("secret scan evidence must come from a mature scanner")
    if payload.get("default_credentials_checked") is not True:
        raise ContractError("secret evidence must include the default-credential check")
    reports = payload.get("reports")
    if not isinstance(reports, list) or not reports:
        raise ContractError("secret scan evidence lacks report refs")
    verified: list[dict[str, str]] = []
    for item in reports:
        if not isinstance(item, dict):
            raise ContractError("secret report ref must be an object")
        path = (root / str(item.get("path", ""))).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ContractError("secret report path escaped the evidence root")
        observed = file_sha256(path)
        if observed != item.get("sha256"):
            raise ContractError("secret report hash mismatch")
        verified.append({"path": str(path), "sha256": observed})
    return verified


def evaluate_security(host: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    validate_host_config(host)
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, evidence: object) -> None:
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence})

    public_ports = set(int(port) for port in observations.get("public_ports", []))
    allowed_ports = set(int(port) for port in host["network"]["allowed_tcp_ports"])
    check("open_ports", public_ports.issubset(allowed_ports), {"public": sorted(public_ports), "allowed": sorted(allowed_ports)})
    check("admin_ports_not_exposed", not public_ports.intersection({3000, 5432, 8080, 9000, 9001, 9090}), sorted(public_ports))
    containers = observations.get("containers", {})
    required_services = {"postgres", "keycloak", "backend", "frontend", "worker", "otel-collector"}
    check("container_inventory", isinstance(containers, dict) and required_services.issubset(containers), sorted(containers) if isinstance(containers, dict) else type(containers).__name__)
    insecure: list[str] = []
    if isinstance(containers, dict):
        for name, config in containers.items():
            if not isinstance(config, dict):
                insecure.append(str(name))
                continue
            if config.get("read_only") is not True or "ALL" not in config.get("cap_drop", []) or "no-new-privileges:true" not in config.get("security_opt", []):
                insecure.append(str(name))
    check("container_hardening", not insecure, insecure)
    check("tls", observations.get("tls_valid") is True, observations.get("tls_detail"))
    headers = {str(name).casefold(): str(value) for name, value in observations.get("headers", {}).items()}
    missing_headers = sorted(REQUIRED_HEADERS - set(headers))
    frame_protection = "x-frame-options" in headers or "frame-ancestors" in headers.get("content-security-policy", "").casefold()
    check("security_headers", not missing_headers and frame_protection, {"missing": missing_headers, "frame_protection": frame_protection})
    check("secret_scan_artifacts", observations.get("secret_scan_status") == "PASS", observations.get("secret_scan_reports"))
    check("default_credentials", observations.get("default_credentials_safe") is True, observations.get("default_credentials_safe"))
    failures = [item["name"] for item in checks if item["status"] != "PASS"]
    return {
        "schema": "mentorflow.staging-security-receipt.v1",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "checks": checks,
    }


def _public_ports() -> list[int]:
    completed = subprocess.run(["ss", "-H", "-lnt"], check=True, capture_output=True, text=True, timeout=20)
    ports: set[int] = set()
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        endpoint = fields[3]
        match = LISTEN.match(endpoint)
        if not match:
            continue
        address = match.group("address").strip("[]")
        if address not in {"127.0.0.1", "::1"}:
            ports.add(int(match.group("port")))
    return sorted(ports)


def _container_security(package_root: Path, package: dict[str, Any]) -> dict[str, Any]:
    completed = subprocess.run(
        _compose_base(package_root, package) + ["config", "--format", "json"],
        cwd=package_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    config = json.loads(completed.stdout)
    result: dict[str, Any] = {}
    for name, service in config.get("services", {}).items():
        result[name] = {
            "read_only": service.get("read_only") is True,
            "cap_drop": service.get("cap_drop", []),
            "security_opt": service.get("security_opt", []),
        }
    return result


def _tls_and_headers(base_url: str) -> tuple[bool, str, dict[str, str]]:
    if not base_url.startswith("https://"):
        return False, "base URL is not HTTPS", {}
    request = urllib.request.Request(base_url.rstrip("/") + "/api/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10, context=ssl.create_default_context()) as response:
            headers = {name.casefold(): value for name, value in response.headers.items()}
            return response.status == 200, "verified certificate and health response", headers
    except Exception as exc:  # noqa: BLE001 - do not retain secrets or response bodies
        return False, type(exc).__name__, {}


def _default_credentials_safe(host: dict[str, Any]) -> bool:
    secret_root = Path(host["layout"]["data"]) / "secrets"
    for name in ("postgres_password", "keycloak_admin_password"):
        path = secret_root / name
        if not path.is_file() or path.stat().st_mode & 0o077:
            return False
        value = path.read_text(encoding="utf-8").strip().casefold()
        if len(value) < 16 or value in PLACEHOLDERS:
            return False
    return True


def collect_live(
    host: dict[str, Any],
    package_root: Path,
    package: dict[str, Any],
    base_url: str,
    secret_scan_manifest: Path,
    secret_evidence_root: Path,
) -> dict[str, Any]:
    secret_manifest = load_json_object(secret_scan_manifest.resolve())
    reports = validate_secret_scan_manifest(secret_manifest, secret_evidence_root.resolve())
    tls_valid, tls_detail, headers = _tls_and_headers(base_url)
    return {
        "public_ports": _public_ports(),
        "containers": _container_security(package_root, package),
        "tls_valid": tls_valid,
        "tls_detail": tls_detail,
        "headers": headers,
        "secret_scan_status": "PASS",
        "secret_scan_reports": reports,
        "default_credentials_safe": _default_credentials_safe(host),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run staging configuration, port, TLS, credential, and header security gates.")
    parser.add_argument("--host-config", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--secret-scan-manifest", type=Path, required=True)
    parser.add_argument("--secret-evidence-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    host = validate_host_config(load_json_object(args.host_config.resolve()))
    package_root, package = load_package(args.package_dir)
    if args.observations:
        observations = load_json_object(args.observations.resolve())
        mode = "OFFLINE_FIXTURE"
    elif args.execute:
        observations = collect_live(host, package_root, package, args.base_url, args.secret_scan_manifest, args.secret_evidence_root)
        mode = "LIVE_HOST"
    else:
        raise SystemExit("provide --observations for offline evaluation or --execute for a live host")
    receipt = evaluate_security(host, observations)
    receipt["mode"] = mode
    receipt["observed_at_utc"] = _now()
    write_atomic_json(args.receipt.resolve(), receipt)
    print(json.dumps({"status": receipt["status"], "failures": receipt["failures"]}, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
