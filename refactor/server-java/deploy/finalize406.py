"""Run the local 406 account E2E, then collect server storage and clean Docker."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
E2E_401 = ROOT / "tests" / "refactor" / "server_java" / "e2e_sync_cleanup401.py"
E2E_406 = ROOT / "tests" / "refactor" / "server_java" / "e2e_sync_cleanup406.py"
SCRATCH = Path(r"D:\MentorFlow\.local\test-tmp\406-MF01\deploy")
SSH = (
    "ssh", "-i", r"C:\Users\21533\.ssh\mentorflow_server",
    "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "mentorflow-admin@89.45.45.28", "sudo", "-n", "sh", "-s",
)


def remote(script: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(SSH, input=script.encode("ascii"), capture_output=True, timeout=1800)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="run the gated E2E and server cleanup")
    parser.add_argument("--account-index", type=int, choices=(0, 1), default=0)
    args = parser.parse_args(argv)
    if not args.execute:
        print("DRY PLAN: check job406/deploy406 completion, run local E2E, collect storage report, then clean Docker")
        return 0
    if not E2E_401.is_file() or not E2E_406.is_file():
        print("E2E tool is missing; no server checks or cleanup were run.", file=sys.stderr)
        return 2

    gate = """set -eu
test "$(sh /opt/mentorflow/releases/java-406/refactor/server-java/deploy/job.sh status /opt/mentorflow/state/job406)" = 'SUCCEEDED 0'
test -f /opt/mentorflow/state/deploy406/complete
test ! -e /opt/mentorflow/state/deploy406/docker-cleanup-started
printf '%s\\n' FINALIZE406_DEPLOYMENT_GATE_OK
"""
    try:
        checked = remote(gate)
    except (OSError, subprocess.TimeoutExpired):
        print("Remote deployment completion check failed; Docker cleanup was skipped.", file=sys.stderr)
        return 2
    if checked.returncode != 0 or b"FINALIZE406_DEPLOYMENT_GATE_OK" not in checked.stdout:
        print("Remote job406 or deploy406 completion was not confirmed; Docker cleanup was skipped.", file=sys.stderr)
        return 2

    SCRATCH.mkdir(parents=True, exist_ok=True)
    for label, tool in (("sync-cleanup401", E2E_401), ("plan-cleanup406", E2E_406)):
        log = SCRATCH / f"{label}.log"
        try:
            e2e = subprocess.run(
                [sys.executable, str(tool), "--execute", "--account-index", str(args.account_index)],
                capture_output=True, timeout=1800,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            output = getattr(error, "stdout", b"") or b""
            details = getattr(error, "stderr", b"") or b""
            log.write_bytes(b"stdout:\n" + output + b"\nstderr:\n" + details)
            print(f"{label} E2E did not complete; Docker cleanup was skipped. Inspect {log}.", file=sys.stderr)
            return 2
        log.write_bytes(b"stdout:\n" + e2e.stdout + b"\nstderr:\n" + e2e.stderr)
        if e2e.returncode != 0:
            print(f"{label} E2E failed (exit {e2e.returncode}); Docker cleanup was skipped. Inspect {log}.", file=sys.stderr)
            return e2e.returncode or 2

    finish = """set -eu
state=/opt/mentorflow/state/deploy406
deploy=/opt/mentorflow/releases/java-406/refactor/server-java/deploy
test -f "$state/complete"
test ! -e "$state/docker-cleanup-started"
bash "$deploy/storage-report.sh" </dev/null > "$state/storage-after.txt"
test -s "$state/storage-after.txt"
touch "$state/docker-cleanup-started"
if STATE="$state" JAVA_IMAGE=mentorflow-java-server:406 MIGRATION_IMAGE=mentorflow-slim-backend:269-python-fallback MIGRATION_TOOL_IMAGE=mentorflow-migration-tool:MF-275 DOCKER_BIN=docker sh "$deploy/cleanup-docker406.sh" > "$state/cleanup-output.tmp" 2>&1; then
  cat "$state/cleanup-output.tmp" | tee -a "$state/deploy.log"
  rm -f "$state/cleanup-output.tmp"
else
  code=$?
  cat "$state/cleanup-output.tmp" | tee -a "$state/deploy.log" >&2
  rm -f "$state/cleanup-output.tmp"
  exit "$code"
fi
touch "$state/docker-cleanup-complete"
printf '%s\\n' FINALIZE406_OK
"""
    try:
        completed = remote(finish)
    except (OSError, subprocess.TimeoutExpired):
        print("Post-E2E storage or Docker cleanup did not complete; inspect server state before retrying.", file=sys.stderr)
        return 2
    if completed.returncode != 0 or b"FINALIZE406_OK" not in completed.stdout:
        print("Post-E2E storage or Docker cleanup failed; inspect server state before retrying.", file=sys.stderr)
        return completed.returncode or 2
    print("FINALIZE406_OK: E2E passed, storage report saved, Docker cleanup completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
