"""Machine-readable Enterprise E1 staging operations verification CLI."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from ..recovery_cli import load_backup_plan
from .adapters import (
    Clock,
    CommandRunner,
    HttpClient,
    SocketTcpProber,
    SystemClock,
    SystemCommandRunner,
    TcpProber,
    UrllibHttpClient,
)
from .incident import IncidentDrillError, load_incident_plan, run_incident_drills
from .models import CheckResult, EnterpriseStagingEvidenceBundle, VerificationStatus, utc_now
from .preflight import run_preflight
from .probes import (
    run_alert_harness,
    run_dashboard_gate,
    run_health_probe,
    run_otel_probe,
)
from .restore_drill import RestoreDrillError, load_restore_drill_plan, run_backup_automation, run_restore_drill
from .settings import VerificationSettings


CLI_RECEIPT_SCHEMA = "mentorflow.enterprise-verify-cli-receipt.v1"
_COMMIT = re.compile(r"[0-9a-f]{40}$")


def _default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mentorflow-enterprise-verify")
    parser.add_argument("--repo-root", type=Path, default=_default_root())
    parser.add_argument("--evidence-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "health", "otel"):
        commands.add_parser(name)
    backup = commands.add_parser("backup")
    backup.add_argument("--plan", type=Path, required=True)
    for name in ("restore", "restore-test"):
        restore = commands.add_parser(name)
        restore.add_argument("--plan", type=Path, required=True)
        restore.add_argument("--disposable-only", action="store_true")
    incident = commands.add_parser("incident")
    incident.add_argument("--plan", type=Path, required=True)
    incident.add_argument("--allow-disruption", action="store_true")
    all_command = commands.add_parser("all")
    all_command.add_argument("--backup-plan", type=Path)
    all_command.add_argument("--restore-plan", type=Path)
    all_command.add_argument("--incident-plan", type=Path)
    all_command.add_argument("--disposable-only", action="store_true")
    all_command.add_argument("--allow-disruption", action="store_true")
    return parser


def _source_commit(root: Path, runner: CommandRunner) -> tuple[str, bool]:
    execution = runner.run(("git", "-C", str(root), "rev-parse", "HEAD"), timeout_seconds=15)
    commit = execution.stdout.strip().casefold()
    return (commit, True) if execution.exit_code == 0 and _COMMIT.fullmatch(commit) else ("UNKNOWN", False)


def _check(
    check_id: str,
    category: str,
    status: VerificationStatus,
    summary: str,
    details: dict[str, object] | None = None,
) -> CheckResult:
    now = utc_now()
    return CheckResult(
        check_id=check_id,
        category=category,
        status=status,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        summary=summary,
        details=details or {},
    )


def _add(bundle: EnterpriseStagingEvidenceBundle, results: Sequence[CheckResult]) -> None:
    for result in results:
        bundle.add_check(result)


def _backup(
    plan_path: Path,
    bundle: EnterpriseStagingEvidenceBundle,
    runner: CommandRunner,
) -> list[CheckResult]:
    try:
        plan = load_backup_plan(plan_path)
    except (OSError, TypeError, ValueError):
        return [_check("backup.plan", "backup", VerificationStatus.FAIL, "backup plan is invalid")]
    return [run_backup_automation(plan, bundle, runner=runner)]


def _restore(
    plan_path: Path,
    bundle: EnterpriseStagingEvidenceBundle,
    runner: CommandRunner,
    *,
    disposable_only: bool,
) -> list[CheckResult]:
    try:
        plan = load_restore_drill_plan(plan_path)
    except RestoreDrillError as exc:
        return [
            _check(
                "restore.plan",
                "restore",
                VerificationStatus.FAIL,
                "restore drill plan is invalid",
                {"error_code": exc.code},
            )
        ]
    return run_restore_drill(plan, bundle, runner=runner, disposable_only=disposable_only)


def _incident(
    plan_path: Path,
    settings: VerificationSettings,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
    http: HttpClient,
    clock: Clock,
    allow_disruption: bool,
) -> list[CheckResult]:
    try:
        plan = load_incident_plan(plan_path)
    except (IncidentDrillError, RestoreDrillError) as exc:
        return [
            _check(
                "incident.plan",
                "incident",
                VerificationStatus.FAIL,
                "incident drill plan is invalid",
                {"error_code": getattr(exc, "code", type(exc).__name__)},
            )
        ]
    return run_incident_drills(
        plan,
        settings,
        bundle,
        runner=runner,
        http=http,
        clock=clock,
        allow_disruption=allow_disruption,
    )


def _missing_plan(kind: str) -> CheckResult:
    return _check(
        f"{kind}.plan",
        kind,
        VerificationStatus.BLOCKED,
        f"{kind} plan is required for the all command",
    )


def _receipt(bundle: EnterpriseStagingEvidenceBundle, evidence_path: Path) -> dict[str, object]:
    counts = Counter(item.status.value for item in bundle.checks)
    return {
        "schema_version": CLI_RECEIPT_SCHEMA,
        "run_id": bundle.run_id,
        "command": bundle.command,
        "status": bundle.status.value,
        "source_commit": bundle.source_commit,
        "evidence_path": str(evidence_path),
        "check_counts": {
            status.value: counts.get(status.value, 0)
            for status in VerificationStatus
        },
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner | None = None,
    http: HttpClient | None = None,
    tcp: TcpProber | None = None,
    clock: Clock | None = None,
) -> int:
    args = _parser().parse_args(argv)
    runner = runner or SystemCommandRunner()
    http = http or UrllibHttpClient()
    tcp = tcp or SocketTcpProber()
    clock = clock or SystemClock()
    root = args.repo_root.resolve()
    commit, commit_ready = _source_commit(root, runner)
    try:
        settings = VerificationSettings.from_environment(
            repo_root=root,
            source_commit=commit,
            evidence_directory=args.evidence_dir,
        )
    except (OSError, TypeError, ValueError):
        bundle = EnterpriseStagingEvidenceBundle.start(args.command, "invalid", commit)
        bundle.add_check(
            _check(
                "settings.validation",
                "settings",
                VerificationStatus.FAIL,
                "verification settings are invalid",
                {"error_code": "SETTINGS_INVALID"},
            )
        )
        evidence_directory = (
            args.evidence_dir.resolve()
            if args.evidence_dir is not None
            else (root / "reports" / "enterprise" / "e1-06b" / "runtime").resolve()
        )
        evidence_path = bundle.write_atomic(evidence_directory)
        print(json.dumps(_receipt(bundle, evidence_path), sort_keys=True, separators=(",", ":")))
        return 1
    bundle = EnterpriseStagingEvidenceBundle.start(args.command, settings.environment, commit)
    bundle.add_check(
        _check(
            "source.exact_commit",
            "source",
            VerificationStatus.PASS if commit_ready else VerificationStatus.BLOCKED,
            "exact source commit captured" if commit_ready else "exact source commit is unavailable",
        )
    )

    if args.command in {"preflight", "all"}:
        _add(bundle, run_preflight(settings, bundle, runner=runner, http=http, tcp=tcp))
    if args.command in {"health", "all"}:
        _add(bundle, run_health_probe(settings, http=http))
    if args.command in {"otel", "all"}:
        _add(bundle, run_otel_probe(settings, http=http, clock=clock))
        _add(bundle, run_dashboard_gate(settings, http=http))
        _add(bundle, run_alert_harness(settings, bundle, runner=runner))
    if args.command == "backup":
        _add(bundle, _backup(args.plan, bundle, runner))
    elif args.command == "all":
        _add(
            bundle,
            _backup(args.backup_plan, bundle, runner) if args.backup_plan else [_missing_plan("backup")],
        )
    if args.command in {"restore", "restore-test"}:
        _add(bundle, _restore(args.plan, bundle, runner, disposable_only=args.disposable_only))
    elif args.command == "all":
        _add(
            bundle,
            _restore(
                args.restore_plan,
                bundle,
                runner,
                disposable_only=args.disposable_only,
            )
            if args.restore_plan
            else [_missing_plan("restore")],
        )
    if args.command == "incident":
        _add(
            bundle,
            _incident(
                args.plan,
                settings,
                bundle,
                runner=runner,
                http=http,
                clock=clock,
                allow_disruption=args.allow_disruption,
            ),
        )
    elif args.command == "all":
        _add(
            bundle,
            _incident(
                args.incident_plan,
                settings,
                bundle,
                runner=runner,
                http=http,
                clock=clock,
                allow_disruption=args.allow_disruption,
            )
            if args.incident_plan
            else [_missing_plan("incident")],
        )
    evidence_path = bundle.write_atomic(settings.evidence_directory)
    print(json.dumps(_receipt(bundle, evidence_path), sort_keys=True, separators=(",", ":")))
    return 0 if bundle.status is VerificationStatus.PASS else 1 if bundle.status is VerificationStatus.FAIL else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CLI_RECEIPT_SCHEMA", "main"]
