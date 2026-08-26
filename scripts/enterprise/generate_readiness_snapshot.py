from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOMAINS = (
    "IDENTITY", "TENANCY", "AUTHORIZATION", "DATABASE", "WORKFLOWS", "STAGING",
    "OBSERVABILITY", "BACKUP_RESTORE", "SECURITY", "LOAD", "RELEASE", "AI_QUALITY",
)
STATUSES = {"PASS", "PARTIAL", "FAIL", "PENDING"}
EVIDENCE_STATUSES = {
    "IMPLEMENTED", "OFFLINE_VERIFIED", "CI_CONFIGURED", "PENDING_OWNER_EVIDENCE",
    "STAGING_EXECUTION_PENDING", "NOT_RUN",
}
COMMIT = re.compile(r"[0-9a-f]{40}")


def generate(source: Path, *, source_commit: str, source_tree: str) -> dict[str, object]:
    if not COMMIT.fullmatch(source_commit) or not COMMIT.fullmatch(source_tree):
        raise RuntimeError("readiness source commit and tree must be exact 40-character hashes")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema") != "mentorflow.enterprise-readiness-evidence.v1":
        raise RuntimeError("unsupported readiness evidence schema")
    if "score" in payload:
        raise RuntimeError("readiness evidence must not contain a synthetic score")
    domains = payload.get("domains", {})
    if set(domains) != set(DOMAINS):
        raise RuntimeError(f"readiness evidence must contain exactly {list(DOMAINS)}")
    for name in DOMAINS:
        domain = domains[name]
        if domain.get("status") not in STATUSES:
            raise RuntimeError(f"{name} has invalid status")
        evidence = domain.get("evidence") or []
        if not evidence:
            raise RuntimeError(f"{name} has no evidence")
        for item in evidence:
            if item.get("status") not in EVIDENCE_STATUSES:
                raise RuntimeError(f"{name} has invalid evidence status")
            ref = str(item.get("ref", ""))
            if item.get("kind") == "LOCAL_FILE":
                if not ref.startswith("repo:"):
                    raise RuntimeError(f"{name} local evidence must use repo:<path>")
                path = (ROOT / ref.removeprefix("repo:")).resolve()
                if not path.is_relative_to(ROOT) or not path.is_file():
                    raise RuntimeError(f"{name} local evidence is missing: {ref}")
        if domain["status"] == "PASS":
            if domain.get("blockers") or domain.get("owner_evidence_status") != "PRESENT":
                raise RuntimeError(f"{name} cannot PASS with blockers or pending owner evidence")
            if any(item["status"] in {"PENDING_OWNER_EVIDENCE", "STAGING_EXECUTION_PENDING", "NOT_RUN"} for item in evidence):
                raise RuntimeError(f"{name} cannot PASS with pending evidence")
    states = {domains[name]["status"] for name in DOMAINS}
    overall = "FAIL" if "FAIL" in states else "PASS" if states == {"PASS"} else "PENDING" if states == {"PENDING"} else "PARTIAL"
    return {
        "schema": "mentorflow.enterprise-readiness-snapshot.v1",
        "source_commit": source_commit,
        "source_tree": source_tree,
        "overall_status": overall,
        "score": None,
        "domains": {name: domains[name] for name in DOMAINS},
    }


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate evidence-backed EnterpriseReadinessSnapshot.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = generate(args.source.resolve(), source_commit=args.source_commit, source_tree=args.source_tree)
    _write_atomic(args.output.resolve(), snapshot)
    print(json.dumps({"status": "PASS", "overall_status": snapshot["overall_status"], "domains": len(snapshot["domains"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
