from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
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
SHA256 = re.compile(r"[0-9a-f]{64}")
REMOTE_REF = re.compile(r"refs/(?:heads|tags)/[^\s~^:?*\[\\]+(?:\^\{\})?")
CANONICAL_JSON_HASH_ALGORITHM = "sha256-canonical-json-v1"


def canonical_json_sha256(payload: object) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _load_payload(data: str, *, source: str) -> dict[str, object]:
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise RuntimeError(f"readiness evidence root must be an object: {source}")
    return payload


def _generate_payload(
    payload: dict[str, object],
    *,
    source_commit: str,
    source_tree: str,
    local_evidence_exists: Callable[[str], bool],
) -> dict[str, object]:
    if not COMMIT.fullmatch(source_commit) or not COMMIT.fullmatch(source_tree):
        raise RuntimeError("readiness source commit and tree must be exact 40-character hashes")
    if payload.get("schema") != "mentorflow.enterprise-readiness-evidence.v1":
        raise RuntimeError("unsupported readiness evidence schema")
    if "score" in payload:
        raise RuntimeError("readiness evidence must not contain a synthetic score")
    domains = payload.get("domains", {})
    if not isinstance(domains, dict) or set(domains) != set(DOMAINS):
        raise RuntimeError(f"readiness evidence must contain exactly {list(DOMAINS)}")
    for name in DOMAINS:
        domain = domains[name]
        if not isinstance(domain, dict) or domain.get("status") not in STATUSES:
            raise RuntimeError(f"{name} has invalid status")
        evidence = domain.get("evidence") or []
        if not isinstance(evidence, list) or not evidence:
            raise RuntimeError(f"{name} has no evidence")
        for item in evidence:
            if not isinstance(item, dict) or item.get("status") not in EVIDENCE_STATUSES:
                raise RuntimeError(f"{name} has invalid evidence status")
            ref = str(item.get("ref", ""))
            if item.get("kind") == "LOCAL_FILE":
                if not ref.startswith("repo:"):
                    raise RuntimeError(f"{name} local evidence must use repo:<path>")
                repo_path = ref.removeprefix("repo:")
                if not local_evidence_exists(repo_path):
                    raise RuntimeError(f"{name} local evidence is missing from the source commit: {ref}")
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


def generate(source: Path, *, source_commit: str, source_tree: str) -> dict[str, object]:
    payload = _load_payload(source.read_text(encoding="utf-8"), source=str(source))

    def current_file_exists(repo_path: str) -> bool:
        path = (ROOT / repo_path).resolve()
        return path.is_relative_to(ROOT) and path.is_file()

    return _generate_payload(
        payload,
        source_commit=source_commit,
        source_tree=source_tree,
        local_evidence_exists=current_file_exists,
    )


def _git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, encoding="utf-8", stderr=subprocess.STDOUT
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.output.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.output else str(exc)
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}") from exc


def resolve_source_identity(root: Path, *, source_commit: str, source_tree: str) -> None:
    if not COMMIT.fullmatch(source_commit) or not COMMIT.fullmatch(source_tree):
        raise RuntimeError("readiness source commit and tree must be exact 40-character hashes")
    _git_output(root, "cat-file", "-e", f"{source_commit}^{{commit}}")
    resolved_tree = _git_output(root, "rev-parse", f"{source_commit}^{{tree}}")
    if resolved_tree != source_tree:
        raise RuntimeError(
            f"readiness source tree mismatch: expected {source_tree}, resolved {resolved_tree}"
        )
    lineage = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_commit, "HEAD"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if lineage.returncode != 0:
        raise RuntimeError("readiness source commit is not an ancestor of the current delivery HEAD")


def _repo_relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"readiness evidence source is outside the repository: {path}") from exc


def generate_from_git(
    root: Path,
    source: Path,
    *,
    source_commit: str,
    source_tree: str,
) -> dict[str, object]:
    resolve_source_identity(root, source_commit=source_commit, source_tree=source_tree)
    source_path = _repo_relative_path(root, source)
    committed_text = _git_output(root, "show", f"{source_commit}:{source_path}")
    committed_payload = _load_payload(committed_text, source=f"{source_commit}:{source_path}")
    working_payload = _load_payload(source.read_text(encoding="utf-8"), source=source_path)
    if working_payload != committed_payload:
        raise RuntimeError("current readiness evidence differs from the declared source commit")

    def committed_file_exists(repo_path: str) -> bool:
        if not repo_path or Path(repo_path).is_absolute() or ".." in Path(repo_path).parts:
            return False
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{source_commit}:{Path(repo_path).as_posix()}"],
            cwd=root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    return _generate_payload(
        committed_payload,
        source_commit=source_commit,
        source_tree=source_tree,
        local_evidence_exists=committed_file_exists,
    )


def verify_remote_reachability(
    root: Path,
    *,
    source_commit: str,
    remote: str,
    refs: Sequence[str],
) -> dict[str, str]:
    if not remote.strip() or not refs:
        raise RuntimeError("remote reachability verification requires a remote and approved refs")
    invalid = [ref for ref in refs if not REMOTE_REF.fullmatch(ref)]
    if invalid:
        raise RuntimeError(f"invalid approved provenance refs: {invalid}")
    output = _git_output(root, "ls-remote", "--exit-code", remote, *refs)
    resolved: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2:
            resolved[fields[1]] = fields[0]
    missing = [ref for ref in refs if resolved.get(ref) != source_commit]
    if missing:
        raise RuntimeError(
            f"readiness source is not exact-reachable through every approved ref: {missing}"
        )
    return {ref: resolved[ref] for ref in refs}


def verify_snapshot_file(
    path: Path,
    expected: dict[str, object],
    *,
    expected_sha256: str | None = None,
) -> str:
    current = _load_payload(path.read_text(encoding="utf-8"), source=str(path))
    if current != expected:
        raise RuntimeError("readiness snapshot is stale or semantically tampered")
    actual_sha256 = canonical_json_sha256(current)
    if expected_sha256 is not None:
        if not SHA256.fullmatch(expected_sha256):
            raise RuntimeError("expected readiness snapshot SHA-256 is not exact")
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"readiness snapshot canonical hash mismatch: expected {expected_sha256}, actual {actual_sha256}"
            )
    return actual_sha256


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify an evidence-backed EnterpriseReadinessSnapshot.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--expected-snapshot-sha256")
    parser.add_argument("--provenance-remote")
    parser.add_argument("--provenance-ref", action="append", default=[])
    args = parser.parse_args()
    source = args.source if args.source.is_absolute() else ROOT / args.source
    output = args.output if args.output.is_absolute() else ROOT / args.output
    snapshot = generate_from_git(
        ROOT,
        source.resolve(),
        source_commit=args.source_commit,
        source_tree=args.source_tree,
    )
    if bool(args.provenance_remote) != bool(args.provenance_ref):
        raise RuntimeError("provenance remote and approved refs must be supplied together")
    remote_refs: dict[str, str] = {}
    if args.provenance_remote:
        remote_refs = verify_remote_reachability(
            ROOT,
            source_commit=args.source_commit,
            remote=args.provenance_remote,
            refs=args.provenance_ref,
        )
    if args.check:
        snapshot_sha256 = verify_snapshot_file(
            output.resolve(), snapshot, expected_sha256=args.expected_snapshot_sha256
        )
    else:
        if args.expected_snapshot_sha256 is not None:
            actual_sha256 = canonical_json_sha256(snapshot)
            if actual_sha256 != args.expected_snapshot_sha256:
                raise RuntimeError(
                    f"generated readiness snapshot canonical hash mismatch: expected {args.expected_snapshot_sha256}, actual {actual_sha256}"
                )
        _write_atomic(output.resolve(), snapshot)
        snapshot_sha256 = canonical_json_sha256(snapshot)
    print(json.dumps({
        "status": "PASS",
        "overall_status": snapshot["overall_status"],
        "domains": len(snapshot["domains"]),
        "source_commit": args.source_commit,
        "source_tree": args.source_tree,
        "snapshot_hash_algorithm": CANONICAL_JSON_HASH_ALGORITHM,
        "snapshot_sha256": snapshot_sha256,
        "remote_refs": remote_refs,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
