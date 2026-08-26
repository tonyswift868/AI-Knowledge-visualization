from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IMMUTABLE_IMAGE = re.compile(r"(?:@sha256:[0-9a-f]{64}|:[0-9a-f]{40})$")
COMMIT = re.compile(r"[0-9a-f]{40}")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
IMAGE_ROLES = ("backend", "frontend", "document-worker")


def _assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"{path.name} does not declare {name}")


def _migration_heads() -> list[str]:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted((ROOT / "backend" / "alembic" / "versions").glob("*.py")):
        revision = str(_assignment(path, "revision"))
        down = _assignment(path, "down_revision")
        revisions.add(revision)
        if isinstance(down, tuple):
            parents.update(str(value) for value in down if value)
        elif down:
            parents.add(str(down))
    heads = sorted(revisions - parents)
    if not heads:
        raise RuntimeError("no Alembic migration head found")
    return heads


def _source_tree(commit: str) -> str:
    value = subprocess.check_output(
        ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()
    if not COMMIT.fullmatch(value):
        raise RuntimeError("git returned an invalid source tree")
    return value


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _required_refs(values: list[str], field: str, minimum: int = 1) -> list[str]:
    result = sorted({value.strip() for value in values if value.strip()})
    if len(result) < minimum:
        raise ValueError(f"{field} requires at least {minimum} non-empty reference(s)")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the MentorFlow EnterpriseReleaseManifest v2.")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-tree")
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--status", choices=("IMPLEMENTED", "OFFLINE_VERIFIED", "CI_CONFIGURED", "STAGING_EXECUTION_PENDING", "STAGING_VERIFIED"), required=True)
    for role in IMAGE_ROLES:
        parser.add_argument(f"--{role}-image", required=True)
        parser.add_argument(f"--{role}-semantic-tag")
        parser.add_argument(f"--{role}-sbom-ref", required=True)
        parser.add_argument(f"--{role}-security-report-ref", required=True)
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--sbom-ref", action="append", default=[])
    parser.add_argument("--security-report-ref", action="append", default=[])
    parser.add_argument("--load-report-ref", action="append", required=True)
    parser.add_argument("--qa-evidence", action="append", required=True)
    parser.add_argument("--known-blocker", action="append", default=[])
    parser.add_argument("--pending-gate", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not COMMIT.fullmatch(args.commit):
        raise SystemExit("commit must be exactly 40 lowercase hexadecimal characters")
    source_tree = args.source_tree or _source_tree(args.commit)
    if not COMMIT.fullmatch(source_tree):
        raise SystemExit("source-tree must be exactly 40 lowercase hexadecimal characters")
    if not SEMVER.fullmatch(args.app_version):
        raise SystemExit("app-version must be semantic version syntax")

    lock = json.loads((ROOT / ".github" / "enterprise-context.lock.json").read_text(encoding="utf-8"))
    images: list[dict[str, object]] = []
    image_tags: dict[str, str] = {}
    per_image_sbom: list[str] = []
    per_image_security: list[str] = []
    for role in IMAGE_ROLES:
        option = role.replace("-", "_")
        image = str(getattr(args, f"{option}_image"))
        semantic_tag = getattr(args, f"{option}_semantic_tag")
        if not IMMUTABLE_IMAGE.search(image):
            raise SystemExit(f"{role} image must use a commit tag or sha256 digest")
        if semantic_tag and (semantic_tag == "latest" or semantic_tag.endswith(":latest")):
            raise SystemExit(f"{role} semantic tag must not be latest")
        sbom_ref = str(getattr(args, f"{option}_sbom_ref"))
        security_ref = str(getattr(args, f"{option}_security_report_ref"))
        per_image_sbom.append(sbom_ref)
        per_image_security.append(security_ref)
        image_tags[role] = image
        images.append(
            {
                "name": role,
                "image_version": {
                    "source_commit": args.commit,
                    "source_tree": source_tree,
                    "semantic_version": args.app_version,
                },
                "immutable_ref": image,
                "semantic_tag": semantic_tag,
                "published": bool(args.published),
                "build_status": (
                    "STAGING_VERIFIED"
                    if args.status == "STAGING_VERIFIED"
                    else "STAGING_EXECUTION_PENDING"
                    if args.status == "STAGING_EXECUTION_PENDING"
                    else "CI_CONFIGURED"
                ),
                "sbom_ref": sbom_ref,
                "security_report_ref": security_ref,
            }
        )

    payload: dict[str, object] = {
        "schema": "mentorflow.enterprise-release-manifest.v2",
        "status": args.status,
        "source_commit": args.commit,
        "source_tree": source_tree,
        "enterprise_contract_version": lock["contract_version"],
        "enterprise_contract_bundle_sha256": lock["contract_bundle_sha256"],
        "migration_version": _migration_heads(),
        "image_tags": image_tags,
        "images": images,
        "sbom_refs": _required_refs(args.sbom_ref + per_image_sbom, "sbom_refs", minimum=3),
        "security_report_refs": _required_refs(args.security_report_ref + per_image_security, "security_report_refs"),
        "asvs_matrix_version": "OWASP-ASVS-5.0.0",
        "load_report_refs": _required_refs(args.load_report_ref, "load_report_refs"),
        "qa_report_refs": _required_refs(args.qa_evidence, "qa_report_refs"),
        "known_blockers": sorted(set(args.known_blocker)),
        "pending_staging_gates": sorted(set(args.pending_gate)),
        "provider_mode": "fake",
        "real_provider_calls": 0,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["manifest_content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    _write_atomic(args.output.resolve(), payload)
    print(json.dumps({"status": "PASS", "output": str(args.output.resolve()), "migration_heads": payload["migration_version"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
