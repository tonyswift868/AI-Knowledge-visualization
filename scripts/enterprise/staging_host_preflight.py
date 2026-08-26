from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from staging_contracts import (
    ContractError,
    file_sha256,
    load_json_object,
    validate_host_config,
    validate_resource_budget,
    write_atomic_json,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command_ok(argv: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    output = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, output[-500:]


def _memory() -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, raw = line.split(":", 1)
        values[name] = int(raw.strip().split()[0]) // 1024
    return values["MemTotal"], values["MemAvailable"]


def _os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"')
    return result


def collect_observations(host: dict[str, Any]) -> dict[str, Any]:
    if platform.system() != "Linux":
        raise RuntimeError("live staging preflight requires Linux")
    layout_root = Path(host["layout"]["root"])
    if not layout_root.is_dir():
        raise RuntimeError("bootstrap must create /opt/mentorflow before preflight")
    release = _os_release()
    memory_total_mb, memory_available_mb = _memory()
    disk = shutil.disk_usage(layout_root)
    docker_ok, docker_detail = _command_ok(["docker", "version", "--format", "{{.Server.Version}}"])
    compose_ok, compose_detail = _command_ok(["docker", "compose", "version", "--short"])
    time_ok, time_detail = _command_ok(["timedatectl", "show", "--property=NTPSynchronized", "--value"])
    writable = False
    try:
        with tempfile.NamedTemporaryFile(prefix="preflight-", dir=layout_root, delete=True) as handle:
            handle.write(b"mentorflow-preflight")
            handle.flush()
        writable = True
    except OSError:
        writable = False
    candidate_port = int(host["candidate_loopback_port"])
    port_available = False
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", candidate_port))
        port_available = True
    except OSError:
        port_available = False
    finally:
        probe.close()
    candidate_port_owner: str | None = None
    if not port_available and docker_ok:
        owner_ok, owner_detail = _command_ok([
            "docker", "ps", "--filter", f"publish={candidate_port}",
            "--format", '{{.Label "com.docker.compose.project"}}/{{.Label "com.docker.compose.service"}}',
        ])
        if owner_ok:
            owners = {line.strip() for line in owner_detail.splitlines() if line.strip()}
            if owners == {"mentorflow_staging/frontend"}:
                candidate_port_owner = "mentorflow_staging/frontend"
    dns_name = host["network"].get("dns_name")
    dns_resolves: bool | None = None
    if dns_name:
        try:
            socket.getaddrinfo(str(dns_name), 443, type=socket.SOCK_STREAM)
            dns_resolves = True
        except socket.gaierror:
            dns_resolves = False
    return {
        "host_node": platform.node(),
        "os_id": release.get("ID"),
        "os_version": release.get("VERSION_ID"),
        "vcpu": os.cpu_count() or 0,
        "memory_total_mb": memory_total_mb,
        "memory_available_mb": memory_available_mb,
        "disk_total_gb": int(disk.total / 1_000_000_000),
        "disk_free_gb": int(disk.free / 1_000_000_000),
        "docker_available": docker_ok,
        "docker_detail": docker_detail,
        "compose_available": compose_ok,
        "compose_detail": compose_detail,
        "time_synchronized": time_ok and time_detail.casefold() == "yes",
        "time_detail": time_detail,
        "ipv6_present": Path("/proc/net/if_inet6").is_file() and bool(Path("/proc/net/if_inet6").read_text(encoding="utf-8").strip()),
        "candidate_port_available": port_available,
        "candidate_port_owner": candidate_port_owner,
        "disk_writable": writable,
        "dns_name": dns_name,
        "dns_resolves": dns_resolves,
    }


def evaluate_preflight(
    host: dict[str, Any],
    budget: dict[str, Any],
    profile: str,
    observed: dict[str, Any],
    *,
    allow_capacity_override: bool = False,
) -> dict[str, Any]:
    validate_host_config(host)
    validate_resource_budget(budget)
    if profile not in budget["profiles"]:
        raise ContractError("preflight profile is not budgeted")
    profile_budget = budget["profiles"][profile]
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, evidence: object, *, hard: bool = True) -> None:
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "hard": hard, "evidence": evidence})

    target = host["target"]
    check("ubuntu_24_04", observed.get("os_id") == "ubuntu" and observed.get("os_version") == "24.04", {"id": observed.get("os_id"), "version": observed.get("os_version")})
    check("vcpu", int(observed.get("vcpu", 0)) >= int(target["vcpu"]), {"observed": observed.get("vcpu"), "required": target["vcpu"]})
    required_ram = int(profile_budget["minimum_observed_ram_mb"])
    ram_ok = int(observed.get("memory_total_mb", 0)) >= required_ram
    check("ram", ram_ok, {"observed_mb": observed.get("memory_total_mb"), "required_observed_mb": required_ram, "advertised_profile_mb": profile_budget["minimum_ram_mb"], "capacity_override": allow_capacity_override})
    check("memory_headroom", int(observed.get("memory_available_mb", 0)) >= int(budget["host_reserve_mb"]), {"available_mb": observed.get("memory_available_mb"), "host_reserve_mb": budget["host_reserve_mb"]})
    minimum_observed_disk_gb = max(20, int(int(target["disk_gb"]) * 0.90))
    check("disk_total", int(observed.get("disk_total_gb", 0)) >= minimum_observed_disk_gb, {"observed_gb": observed.get("disk_total_gb"), "required_observed_gb": minimum_observed_disk_gb, "advertised_gb": target["disk_gb"]})
    check("disk_free", int(observed.get("disk_free_gb", 0)) >= 15, {"observed_gb": observed.get("disk_free_gb"), "required_gb": 15})
    check("disk_writable", observed.get("disk_writable") is True, observed.get("disk_writable"))
    check("docker", observed.get("docker_available") is True, observed.get("docker_detail"))
    check("compose", observed.get("compose_available") is True, observed.get("compose_detail"))
    check("time_sync", observed.get("time_synchronized") is True, observed.get("time_detail"))
    candidate_port_ok = observed.get("candidate_port_available") is True or observed.get("candidate_port_owner") == "mentorflow_staging/frontend"
    check("candidate_port", candidate_port_ok, {"port": host["candidate_loopback_port"], "available": observed.get("candidate_port_available"), "approved_owner": observed.get("candidate_port_owner")})
    dns_name = host["network"].get("dns_name")
    checks.append({
        "name": "dns_seam",
        "status": "PASS" if dns_name and observed.get("dns_resolves") is True else "PENDING_CONFIGURATION" if not dns_name else "FAIL",
        "hard": bool(dns_name),
        "evidence": {"dns_name": dns_name, "resolves": observed.get("dns_resolves")},
    })
    ipv6_policy = host["network"]["ipv6_policy"]
    ipv6_ok = ipv6_policy != "REVIEW_BEFORE_ENABLE" or observed.get("ipv6_present") is False
    checks.append({
        "name": "ipv6_review",
        "status": "PASS" if ipv6_ok else "REVIEW_REQUIRED",
        "hard": observed.get("ipv6_present") is True,
        "evidence": {"policy": ipv6_policy, "present": observed.get("ipv6_present")},
    })
    hard_failures = [item["name"] for item in checks if item["hard"] and item["status"] not in {"PASS"}]
    return {
        "schema": "mentorflow.staging-host-preflight-receipt.v1",
        "status": "PASS" if not hard_failures else "FAIL",
        "profile": profile,
        "resource_budget_status": budget["status"],
        "hard_failures": hard_failures,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a vendor-neutral Ubuntu staging host before deployment.")
    parser.add_argument("--host-config", type=Path, required=True)
    parser.add_argument("--resource-budget", type=Path, required=True)
    parser.add_argument("--profile", choices=("minimal", "full"), default="minimal")
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--allow-capacity-override", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    host = validate_host_config(load_json_object(args.host_config.resolve()))
    budget = validate_resource_budget(load_json_object(args.resource_budget.resolve()))
    if args.observations:
        observed = load_json_object(args.observations.resolve())
        mode = "OFFLINE_FIXTURE"
    elif args.execute:
        observed = collect_observations(host)
        mode = "LIVE_HOST"
    else:
        payload = {
            "schema": "mentorflow.staging-host-preflight-plan.v1",
            "status": "DRY_RUN",
            "profile": args.profile,
            "checks": ["CPU", "RAM", "disk", "ports", "Docker", "Compose", "DNS", "time_sync", "IPv6", "disk_writable", "memory_headroom"],
        }
        write_atomic_json(args.receipt.resolve(), payload)
        print(json.dumps(payload, sort_keys=True))
        return 0
    receipt = evaluate_preflight(host, budget, args.profile, observed, allow_capacity_override=args.allow_capacity_override)
    receipt["mode"] = mode
    receipt["host_node"] = observed.get("host_node")
    receipt["host_config_sha256"] = file_sha256(args.host_config.resolve())
    receipt["resource_budget_sha256"] = file_sha256(args.resource_budget.resolve())
    receipt["observed_at_utc"] = _now()
    write_atomic_json(args.receipt.resolve(), receipt)
    print(json.dumps({"status": receipt["status"], "receipt": str(args.receipt.resolve()), "hard_failures": receipt["hard_failures"]}, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
