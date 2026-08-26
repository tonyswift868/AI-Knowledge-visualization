from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from staging_contracts import (
    ContractError,
    canonical_sha256,
    file_sha256,
    load_json_object,
    validate_host_config,
    validate_release_manifest,
    validate_release_package,
    write_atomic_json,
)


CORE_SERVICES = ("postgres", "keycloak", "backend", "frontend", "worker", "otel-collector")
FULL_SERVICES = CORE_SERVICES + ("object-store", "prometheus", "grafana", "testing-services")
EXPANDED_LOAD_PROFILES = ("average", "stress", "spike", "tenant-isolation")
MIGRATION_POLICY = "BACKUP_BEFORE_MIGRATE_FAIL_CLOSED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_directory(path: Path) -> str:
    index = [
        {"path": item.relative_to(path).as_posix(), "sha256": file_sha256(item)}
        for item in sorted(path.rglob("*"))
        if item.is_file()
    ]
    return canonical_sha256(index)


def load_package(package_dir: Path) -> tuple[Path, dict[str, Any]]:
    root = package_dir.resolve()
    metadata_path = root / "release-package.json"
    if not root.is_dir() or not metadata_path.is_file():
        raise ContractError("release package directory or metadata is missing")
    package = validate_release_package(load_json_object(metadata_path))
    if package["migration_policy"] != MIGRATION_POLICY:
        raise ContractError("staging release package must require backup before a fail-closed migration")
    for name, relative in package["artifacts"].items():
        path = (root / str(relative)).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ContractError(f"package artifact escaped or is missing: {name}")
        observed = file_sha256(path) if path.is_file() else _hash_directory(path)
        if package["artifact_sha256"].get(name) != observed:
            raise ContractError(f"package artifact hash mismatch: {name}")
    manifest = root / str(package["manifest_ref"])
    if file_sha256(manifest) != package["manifest_sha256"]:
        raise ContractError("packaged EnterpriseReleaseManifest hash mismatch")
    manifest_payload = load_json_object(manifest)
    manifest_images = validate_release_manifest(manifest_payload)
    expected_release_id = f"{manifest_payload['source_commit'][:12]}-{manifest_payload['manifest_content_sha256'][:12]}"
    identity_pairs = (
        ("release_id", expected_release_id),
        ("source_commit", manifest_payload["source_commit"]),
        ("source_tree", manifest_payload["source_tree"]),
        ("enterprise_contract_version", manifest_payload["enterprise_contract_version"]),
        ("enterprise_contract_bundle_sha256", manifest_payload["enterprise_contract_bundle_sha256"]),
        ("migration_version", manifest_payload["migration_version"]),
    )
    mismatched = [name for name, expected in identity_pairs if package.get(name) != expected]
    if mismatched or any(package["images"].get(name) != image for name, image in manifest_images.items()):
        raise ContractError(f"release package identity does not match its EnterpriseReleaseManifest: {mismatched or ['images']}")
    return root, package


class CommandRunner:
    def __init__(self, *, execute: bool) -> None:
        self.execute = execute
        self.records: list[dict[str, Any]] = []

    def run(
        self,
        phase: str,
        argv: list[str],
        *,
        cwd: Path,
        stdout_path: Path | None = None,
    ) -> None:
        record: dict[str, Any] = {"phase": phase, "argv": argv, "status": "PLANNED" if not self.execute else "RUNNING"}
        self.records.append(record)
        if not self.execute:
            return
        try:
            if stdout_path:
                stdout_path.parent.mkdir(parents=True, exist_ok=True)
                with stdout_path.open("wb") as output:
                    subprocess.run(argv, cwd=cwd, check=True, stdout=output, stderr=subprocess.PIPE, timeout=1800)
                record["stdout_ref"] = str(stdout_path)
                record["stdout_sha256"] = file_sha256(stdout_path)
            else:
                completed = subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True, timeout=1800)
                record["stdout_characters"] = len(completed.stdout)
                record["stderr_characters"] = len(completed.stderr)
            record["status"] = "PASS"
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            record["status"] = "FAIL"
            record["error_type"] = type(exc).__name__
            if stdout_path:
                stdout_path.unlink(missing_ok=True)
                record["partial_output_removed"] = True
            if isinstance(exc, subprocess.CalledProcessError):
                record["stderr_characters"] = len(exc.stderr or b"")
            raise


def _compose_base(package_dir: Path, package: dict[str, Any]) -> list[str]:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(package_dir / package["artifacts"]["environment"]),
        "-f",
        str(package_dir / package["artifacts"]["compose"]),
    ]
    if package["document_hook"]["enabled"]:
        command.extend(["-f", str(package_dir / package["artifacts"]["document_compose_overlay"])])
    command.extend(["--profile", package["profile"]])
    if package["document_hook"]["enabled"]:
        command.extend(["--profile", "document-hook"])
    return command


def _verify_live_boundaries(package_dir: Path, host: dict[str, Any], package: dict[str, Any], receipt_path: Path) -> None:
    if platform.system() != "Linux":
        raise RuntimeError("live release orchestration requires Linux")
    releases = Path(host["layout"]["releases"]).resolve()
    artifacts = Path(host["layout"]["artifacts"]).resolve()
    if not package_dir.is_relative_to(releases):
        raise RuntimeError("live package must be staged under /opt/mentorflow/releases")
    if not receipt_path.resolve().is_relative_to(artifacts):
        raise RuntimeError("live receipt must remain under /opt/mentorflow/artifacts")
    for secret in package["required_secret_files"]:
        path = Path(secret)
        if not path.is_file():
            raise RuntimeError(f"required secret file is missing: {path.name}")
        if path.stat().st_mode & 0o077:
            raise RuntimeError(f"secret file permissions are too broad: {path.name}")


def _load_preflight(
    path: Path,
    *,
    execute: bool,
    expected_profile: str | None = None,
    expected_host_config_sha256: str | None = None,
    expected_resource_budget_sha256: str | None = None,
) -> dict[str, Any]:
    receipt = load_json_object(path.resolve())
    if receipt.get("schema") != "mentorflow.staging-host-preflight-receipt.v1" or receipt.get("status") != "PASS":
        raise ContractError("a PASS host preflight receipt is required")
    if execute and receipt.get("mode") != "LIVE_HOST":
        raise ContractError("live deployment requires a LIVE_HOST preflight receipt")
    if execute:
        expected = {
            "profile": expected_profile,
            "host_config_sha256": expected_host_config_sha256,
            "resource_budget_sha256": expected_resource_budget_sha256,
            "host_node": platform.node(),
        }
        mismatched = [name for name, value in expected.items() if value is not None and receipt.get(name) != value]
        if mismatched:
            raise ContractError(f"live preflight receipt is stale or belongs to another deployment boundary: {mismatched}")
    return receipt


def _health(base_url: str, *, execute: bool, attempts: int = 20) -> None:
    if not execute:
        return
    endpoint = f"{base_url.rstrip('/')}/api/health"
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(endpoint, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("status") == "ok":
                    return
        except Exception as exc:  # noqa: BLE001 - only the type is retained
            last_error = exc
        time.sleep(3)
    raise RuntimeError(f"health gate failed: {type(last_error).__name__}")


def _load_command(
    package_dir: Path,
    package: dict[str, Any],
    host: dict[str, Any],
    profile: str,
    base_url: str,
    artifact_root: Path,
) -> list[str]:
    load_env = Path(host["layout"]["data"]) / "secrets" / "load.env"
    document_enabled = "true" if package["document_hook"]["enabled"] else "false"
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "--env-file",
        str(load_env),
        "-e",
        "PROVIDER_ADAPTER=fake",
        "-e",
        f"BASE_URL={base_url.rstrip('/')}",
        "-e",
        f"DOCUMENT_HOOK_ENABLED={document_enabled}",
        "-e",
        "TENANT_CORPUS_PATH=/work/tests/load/fixtures/tenant-corpus.v1.json",
        "-v",
        f"{package_dir}:/work:ro",
        "-v",
        f"{artifact_root}:/work/artifacts",
        "-w",
        "/work",
        package["images"]["k6"],
        "run",
        f"tests/load/{profile}/scenario.js",
    ]


def _run_load(
    runner: CommandRunner,
    package_dir: Path,
    package: dict[str, Any],
    host: dict[str, Any],
    profiles: list[str],
    base_url: str,
    artifact_root: Path,
    *,
    approve_expanded_load: bool,
) -> None:
    invalid = [profile for profile in profiles if profile not in ("smoke", *EXPANDED_LOAD_PROFILES)]
    if invalid:
        raise ContractError(f"unsupported staging load profiles: {invalid}")
    if any(profile in EXPANDED_LOAD_PROFILES for profile in profiles) and not approve_expanded_load:
        raise ContractError("average/stress/spike/tenant load requires explicit expanded-load approval")
    if runner.execute:
        load_env = Path(host["layout"]["data"]) / "secrets" / "load.env"
        if not load_env.is_file() or load_env.stat().st_mode & 0o077:
            raise RuntimeError("load.env is missing or has unsafe permissions")
        artifact_root.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        runner.run(
            f"k6_{profile}",
            _load_command(package_dir, package, host, profile, base_url, artifact_root),
            cwd=package_dir,
        )


def _atomic_current_pointer(host: dict[str, Any], package_dir: Path, *, execute: bool) -> str | None:
    current = Path(host["layout"]["current"])
    previous: str | None = None
    if execute and current.is_symlink():
        previous = str(current.resolve())
    if not execute:
        return previous
    temporary = current.with_name(f".current.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    os.symlink(package_dir, temporary, target_is_directory=True)
    os.replace(temporary, current)
    return previous


def _restore_current_pointer(host: dict[str, Any], previous: str | None, *, execute: bool) -> None:
    if not execute or previous is None:
        return
    _atomic_current_pointer(host, Path(previous), execute=True)


def _reload_proxy(runner: CommandRunner, host: dict[str, Any], *, cwd: Path) -> None:
    command = host.get("proxy_reload_command") or []
    if command:
        runner.run("proxy_reload", [str(value) for value in command], cwd=cwd)
    else:
        runner.records.append({"phase": "proxy_reload", "status": "NOT_CONFIGURED", "evidence": "atomic current pointer updated; DNS/TLS proxy reload seam remains operator-configured"})


def deploy_release(
    *,
    package_dir: Path,
    host_config: Path,
    preflight_receipt: Path,
    receipt_path: Path,
    execute: bool,
) -> dict[str, Any]:
    package_root, package = load_package(package_dir)
    host = validate_host_config(load_json_object(host_config.resolve()))
    preflight = _load_preflight(
        preflight_receipt,
        execute=execute,
        expected_profile=package["profile"],
        expected_host_config_sha256=file_sha256(host_config.resolve()),
        expected_resource_budget_sha256=package["artifact_sha256"]["resource_budget"],
    )
    if execute:
        _verify_live_boundaries(package_root, host, package, receipt_path)
    runner = CommandRunner(execute=execute)
    compose = _compose_base(package_root, package)
    local_url = f"http://127.0.0.1:{host['candidate_loopback_port']}"
    artifact_root = receipt_path.resolve().parent.parent
    backup_run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_paths = {
        "application": Path(host["layout"]["backups"]) / f"pre-{package['release_id']}-{backup_run_id}-application.dump",
        "identity": Path(host["layout"]["backups"]) / f"pre-{package['release_id']}-{backup_run_id}-identity.dump",
    }
    if execute:
        artifact_root.mkdir(parents=True, exist_ok=True)
        next(iter(backup_paths.values())).parent.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "schema": "mentorflow.staging-deploy-receipt.v1",
        "status": "RUNNING" if execute else "DRY_RUN",
        "started_at_utc": _now(),
        "release_id": package["release_id"],
        "source_commit": package["source_commit"],
        "profile": package["profile"],
        "provider_mode": "fake",
        "real_provider_calls": 0,
        "e1_04_dependency": "NO",
        "document_hook": package["document_hook"]["status"],
        "preflight_receipt_sha256": file_sha256(preflight_receipt.resolve()),
        "preflight_mode": preflight.get("mode"),
        "backup_refs": {name: str(path) for name, path in backup_paths.items()},
        "migration_policy": package["migration_policy"],
        "phases": runner.records,
    }
    previous: str | None = None
    migration_started = False
    migration_verified = False
    switched = False
    try:
        runner.run("compose_config", compose + ["config", "--quiet"], cwd=package_root)
        runner.run("pull_exact_images", compose + ["pull"], cwd=package_root)
        runner.run("database_start", compose + ["up", "--detach", "--wait", "postgres"], cwd=package_root)
        runner.run(
            "database_backup",
            compose + ["exec", "-T", "postgres", "pg_dump", "--format=custom", "--username", "mentorflow", "mentorflow"],
            cwd=package_root,
            stdout_path=backup_paths["application"],
        )
        runner.run(
            "identity_database_backup",
            compose + ["exec", "-T", "postgres", "pg_dump", "--format=custom", "--username", "mentorflow", "mentorflow_keycloak"],
            cwd=package_root,
            stdout_path=backup_paths["identity"],
        )
        if execute:
            write_atomic_json(
                artifact_root / "backup-refs" / "pre-migration-backup.json",
                {
                    "schema": "mentorflow.staging-backup-ref.v1",
                    "status": "CREATED_NOT_RESTORE_VERIFIED",
                    "backups": [
                        {"role": name, "path": str(path), "sha256": file_sha256(path)}
                        for name, path in sorted(backup_paths.items())
                    ],
                    "release_id": package["release_id"],
                },
            )
        migration_started = True
        runner.run("migration", compose + ["run", "--rm", "migrate"], cwd=package_root)
        runner.run("migration_verify", compose + ["run", "--rm", "migration-verify"], cwd=package_root)
        migration_verified = True
        if execute:
            write_atomic_json(
                artifact_root / "migration" / "migration-receipt.json",
                {
                    "schema": "mentorflow.staging-migration-receipt.v1",
                    "status": "PASS",
                    "migration_version": package["migration_version"],
                    "backup_ref": "../backup-refs/pre-migration-backup.json",
                },
            )
        services = list(FULL_SERVICES if package["profile"] == "full" else CORE_SERVICES)
        if package["document_hook"]["enabled"]:
            services.append("docling-worker")
        runner.run("candidate_start", compose + ["up", "--detach", "--wait", *services], cwd=package_root)
        _health(local_url, execute=execute)
        runner.records.append({"phase": "candidate_health", "status": "PASS" if execute else "PLANNED", "url": f"{local_url}/api/health"})
        _run_load(runner, package_root, package, host, ["smoke"], local_url, artifact_root / "k6", approve_expanded_load=False)
        if execute:
            write_atomic_json(
                artifact_root / "tests" / "smoke-receipt.json",
                {
                    "schema": "mentorflow.staging-smoke-receipt.v1",
                    "status": "PASS",
                    "profile": "smoke",
                    "provider_mode": "fake",
                    "real_provider_calls": 0,
                    "document_hook": package["document_hook"]["status"],
                },
            )
            write_atomic_json(
                artifact_root / "tenant-isolation" / "tenant-isolation.pending.json",
                {
                    "schema": "mentorflow.staging-tenant-isolation-receipt.v1",
                    "status": "NOT_RUN",
                    "reason": "expanded tenant-isolation load was not part of the default small smoke",
                    "unauthorized_success_target": 0,
                },
            )
        previous = _atomic_current_pointer(host, package_root, execute=execute)
        switched = True
        runner.records.append({"phase": "atomic_current_pointer", "status": "PASS" if execute else "PLANNED", "previous": previous, "current": str(package_root)})
        _reload_proxy(runner, host, cwd=package_root)
        runner.run("container_versions", ["docker", "version", "--format", "{{json .}}"], cwd=package_root, stdout_path=artifact_root / "versions" / "docker-version.json")
        runner.run("compose_versions", ["docker", "compose", "version", "--format", "json"], cwd=package_root, stdout_path=artifact_root / "versions" / "compose-version.json")
        runner.run("container_inventory", compose + ["ps", "--format", "json"], cwd=package_root, stdout_path=artifact_root / "containers" / "compose-ps.json")
        if execute:
            write_atomic_json(
                artifact_root / "otel-refs" / "otel-collector-ref.json",
                {
                    "schema": "mentorflow.staging-otel-ref.v1",
                    "status": "CONTAINER_STARTED_TELEMETRY_BACKEND_NOT_VERIFIED",
                    "service": "otel-collector",
                    "endpoint": "http://otel-collector:4318",
                },
            )
        receipt["status"] = "RELEASED" if execute else "DRY_RUN"
    except Exception as exc:
        receipt["failure_type"] = type(exc).__name__
        if migration_started and not migration_verified:
            receipt["status"] = "MIGRATION_FAILED_STOPPED"
            receipt["rollback_procedure"] = "STOP; inspect migration receipt; restore the retained pre-migration backup into an isolated target; verify schema and tenant data; do not continue release automatically"
        elif switched:
            try:
                _restore_current_pointer(host, previous, execute=execute)
                _reload_proxy(runner, host, cwd=package_root)
                receipt["status"] = "ROLLED_BACK_POINTER"
            except Exception as rollback_error:  # noqa: BLE001
                receipt["status"] = "ROLLBACK_FAILED"
                receipt["rollback_failure_type"] = type(rollback_error).__name__
        else:
            receipt["status"] = "CANDIDATE_FAILED_BEFORE_SWITCH"
        raise
    finally:
        receipt["finished_at_utc"] = _now()
        receipt["phases"] = runner.records
        write_atomic_json(receipt_path.resolve(), receipt)
    return receipt


def rollback_drill(
    *,
    candidate_dir: Path,
    rollback_dir: Path,
    host_config: Path,
    preflight_receipt: Path,
    receipt_path: Path,
    execute: bool,
) -> dict[str, Any]:
    candidate_root, candidate = load_package(candidate_dir)
    rollback_root, rollback = load_package(rollback_dir)
    if candidate["migration_version"] != rollback["migration_version"]:
        raise ContractError("rollback drill requires matching migration heads; otherwise use backup/restore drill")
    if candidate["profile"] != rollback["profile"] or candidate["artifact_sha256"]["resource_budget"] != rollback["artifact_sha256"]["resource_budget"]:
        raise ContractError("rollback drill requires matching profile and resource budget")
    host = validate_host_config(load_json_object(host_config.resolve()))
    _load_preflight(
        preflight_receipt,
        execute=execute,
        expected_profile=candidate["profile"],
        expected_host_config_sha256=file_sha256(host_config.resolve()),
        expected_resource_budget_sha256=candidate["artifact_sha256"]["resource_budget"],
    )
    if execute:
        _verify_live_boundaries(candidate_root, host, candidate, receipt_path)
        releases = Path(host["layout"]["releases"]).resolve()
        if not rollback_root.is_relative_to(releases):
            raise RuntimeError("rollback package must remain under releases")
    runner = CommandRunner(execute=execute)
    receipt: dict[str, Any] = {
        "schema": "mentorflow.staging-rollback-drill-receipt.v1",
        "status": "RUNNING" if execute else "DRY_RUN",
        "started_at_utc": _now(),
        "candidate_b": candidate["release_id"],
        "rollback_a": rollback["release_id"],
        "migration_version": candidate["migration_version"],
        "provider_mode": "fake",
        "real_provider_calls": 0,
        "phases": runner.records,
    }
    try:
        for label, root, package in (("candidate_b", candidate_root, candidate), ("rollback_a", rollback_root, rollback)):
            compose = _compose_base(root, package)
            runner.run(f"{label}_pull", compose + ["pull"], cwd=root)
            runner.run(f"{label}_migration_verify", compose + ["run", "--rm", "migration-verify"], cwd=root)
            services = list(FULL_SERVICES if package["profile"] == "full" else CORE_SERVICES)
            if package["document_hook"]["enabled"]:
                services.append("docling-worker")
            runner.run(f"{label}_start", compose + ["up", "--detach", "--wait", *services], cwd=root)
            local_url = f"http://127.0.0.1:{host['candidate_loopback_port']}"
            _health(local_url, execute=execute)
            runner.records.append({"phase": f"{label}_health", "status": "PASS" if execute else "PLANNED"})
            artifact_root = Path(host["layout"]["artifacts"]) / "rollback-drill" / label
            _run_load(runner, root, package, host, ["smoke"], local_url, artifact_root, approve_expanded_load=False)
            _atomic_current_pointer(host, root, execute=execute)
            runner.records.append({"phase": f"{label}_current_pointer", "status": "PASS" if execute else "PLANNED", "current": str(root)})
            _reload_proxy(runner, host, cwd=root)
        receipt["status"] = "PASS" if execute else "DRY_RUN"
    except Exception as exc:
        receipt["status"] = "FAIL"
        receipt["failure_type"] = type(exc).__name__
        raise
    finally:
        receipt["finished_at_utc"] = _now()
        receipt["phases"] = runner.records
        write_atomic_json(receipt_path.resolve(), receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy, load-test, and rollback exact MentorFlow staging packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--package-dir", type=Path, required=True)
    deploy.add_argument("--host-config", type=Path, required=True)
    deploy.add_argument("--preflight-receipt", type=Path, required=True)
    deploy.add_argument("--receipt", type=Path, required=True)
    deploy.add_argument("--execute", action="store_true")
    load = subparsers.add_parser("load")
    load.add_argument("--package-dir", type=Path, required=True)
    load.add_argument("--host-config", type=Path, required=True)
    load.add_argument("--profile", action="append", choices=("smoke", *EXPANDED_LOAD_PROFILES), default=[])
    load.add_argument("--base-url", required=True)
    load.add_argument("--artifact-root", type=Path, required=True)
    load.add_argument("--approve-expanded-load", action="store_true")
    load.add_argument("--execute", action="store_true")
    drill = subparsers.add_parser("rollback-drill")
    drill.add_argument("--candidate-package", type=Path, required=True)
    drill.add_argument("--rollback-package", type=Path, required=True)
    drill.add_argument("--host-config", type=Path, required=True)
    drill.add_argument("--preflight-receipt", type=Path, required=True)
    drill.add_argument("--receipt", type=Path, required=True)
    drill.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "deploy":
        receipt = deploy_release(
            package_dir=args.package_dir,
            host_config=args.host_config,
            preflight_receipt=args.preflight_receipt,
            receipt_path=args.receipt,
            execute=args.execute,
        )
    elif args.command == "load":
        root, package = load_package(args.package_dir)
        host = validate_host_config(load_json_object(args.host_config.resolve()))
        runner = CommandRunner(execute=args.execute)
        profiles = args.profile or ["smoke"]
        _run_load(runner, root, package, host, profiles, args.base_url, args.artifact_root.resolve(), approve_expanded_load=args.approve_expanded_load)
        receipt = {"status": "PASS" if args.execute else "DRY_RUN", "profiles": profiles, "phases": runner.records}
        write_atomic_json(args.artifact_root.resolve() / "load-runner-receipt.json", receipt)
        if "tenant-isolation" in profiles:
            write_atomic_json(
                args.artifact_root.resolve().parent / "tenant-isolation" / "tenant-isolation-receipt.json",
                {
                    "schema": "mentorflow.staging-tenant-isolation-receipt.v1",
                    "status": "PASS" if args.execute else "DRY_RUN",
                    "unauthorized_success_target": 0,
                    "provider_mode": "fake",
                    "load_receipt": str((args.artifact_root.resolve() / "load-runner-receipt.json")),
                },
            )
    else:
        receipt = rollback_drill(
            candidate_dir=args.candidate_package,
            rollback_dir=args.rollback_package,
            host_config=args.host_config,
            preflight_receipt=args.preflight_receipt,
            receipt_path=args.receipt,
            execute=args.execute,
        )
    print(json.dumps({"status": receipt["status"], "command": args.command}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
