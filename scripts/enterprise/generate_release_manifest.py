from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
COMMIT = re.compile(r"[0-9a-f]{40}")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
LEGACY_IMMUTABLE_IMAGE = re.compile(r"(?:@sha256:[0-9a-f]{64}|:[0-9a-f]{40})$")
STRICT_DIGEST_REFERENCE = re.compile(
    r"(?P<repository>[a-z0-9][a-z0-9._:/-]*[a-z0-9])@sha256:(?P<digest>[0-9a-f]{64})"
)
SHA256 = re.compile(r"[0-9a-f]{64}")
IMAGE_ROLES = ("backend", "frontend", "document-worker", "worker")
BASE_IMAGE_ROLES = ("node", "python", "nginx")
SOURCE_LOCK_PATHS = ("backend/requirements.lock.txt", "frontend/package-lock.json")
GIT_BLOB_HASH_ALGORITHM = "sha256:git-blob-bytes-v1"
ARTIFACT_HASH_ALGORITHM = "sha256:artifact-bytes-v1"
CANONICAL_JSON_HASH_ALGORITHM = "sha256:canonical-json-v1"
FINAL_DIGEST_SOURCE = "docker buildx imagetools inspect"


def canonical_json_sha256(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"{path.name} does not declare {name}")


def _migration_heads(root: Path = ROOT) -> list[str]:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted((root / "backend" / "alembic" / "versions").glob("*.py")):
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


def _git_output(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="strict"
    ).strip()


def _source_tree(commit: str, root: Path = ROOT) -> str:
    value = _git_output(root, "rev-parse", f"{commit}^{{tree}}")
    if not COMMIT.fullmatch(value):
        raise RuntimeError("git returned an invalid source tree")
    return value


def _git_blob_bytes(root: Path, commit: str, relative_path: str) -> bytes:
    if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise ValueError(f"Git blob path must be repository-relative: {relative_path}")
    return subprocess.check_output(["git", "cat-file", "blob", f"{commit}:{relative_path}"], cwd=root)


def _git_blob_sha256(root: Path, commit: str, relative_path: str) -> str:
    return hashlib.sha256(_git_blob_bytes(root, commit, relative_path)).hexdigest()


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def _required_refs(values: list[str], field: str, minimum: int = 1) -> list[str]:
    result = sorted({value.strip() for value in values if value.strip()})
    if len(result) < minimum:
        raise ValueError(f"{field} requires at least {minimum} non-empty reference(s)")
    return result


def _validate_source_identity(root: Path, commit: str, source_tree: str) -> None:
    if not COMMIT.fullmatch(commit):
        raise ValueError("commit must be exactly 40 lowercase hexadecimal characters")
    if not COMMIT.fullmatch(source_tree):
        raise ValueError("source-tree must be exactly 40 lowercase hexadecimal characters")
    if _git_output(root, "rev-parse", "HEAD") != commit:
        raise ValueError("final release source commit must equal the exact checked-out HEAD")
    if _source_tree(commit, root) != source_tree:
        raise ValueError("final release source tree does not match the source commit")
    tracked_contract_paths = (
        ".github/enterprise-context.lock.json",
        "backend/alembic",
        *SOURCE_LOCK_PATHS,
    )
    changed = subprocess.run(
        ["git", "diff", "--quiet", commit, "--", *tracked_contract_paths], cwd=root, check=False
    )
    if changed.returncode != 0:
        raise ValueError("final release contract inputs differ from the exact source commit")


def _validate_digest_reference(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a repository@sha256 reference")
    match = STRICT_DIGEST_REFERENCE.fullmatch(value)
    if not match:
        raise ValueError(f"{field} must be a lowercase digest-only repository@sha256 reference")
    repository = match.group("repository")
    if ":" in repository.rsplit("/", 1)[-1]:
        raise ValueError(f"{field} must not combine a mutable tag with its digest")
    digest = match.group("digest")
    if len(set(digest)) < 8:
        raise ValueError(f"{field} uses an obviously synthetic digest")
    return value


def _artifact_path(root: Path, value: object, field: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}.path must be a non-empty repository-relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{field}.path must stay within the release workspace")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"{field}.path does not name a retained release artifact")
    return path, relative.as_posix()


def _validate_artifact(root: Path, value: object, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError(f"{field} must contain exactly path and sha256")
    path, relative = _artifact_path(root, value["path"], field)
    expected = value["sha256"]
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise ValueError(f"{field}.sha256 must be 64 lowercase hexadecimal characters")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"{field} artifact hash mismatch")
    return {
        "path": relative,
        "sha256": actual,
        "hash_algorithm": ARTIFACT_HASH_ALGORITHM,
    }


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field} JSON root must be an object")
    return payload


def _validate_final_evidence(
    root: Path,
    evidence_path: Path,
    *,
    commit: str,
    source_tree: str,
) -> tuple[dict[str, object], dict[str, str]]:
    evidence = _load_json(evidence_path, "final evidence")
    expected_top_level = {
        "schema",
        "source_commit",
        "source_tree",
        "digest_source",
        "base_images",
        "base_provenance",
        "images",
    }
    if set(evidence) != expected_top_level:
        raise ValueError("final evidence fields are incomplete or ambiguous")
    if evidence["schema"] != "mentorflow.enterprise-final-image-evidence.v1":
        raise ValueError("final evidence schema mismatch")
    if evidence["source_commit"] != commit or evidence["source_tree"] != source_tree:
        raise ValueError("final evidence source commit/tree mismatch")
    if evidence["digest_source"] != FINAL_DIGEST_SOURCE:
        raise ValueError("final evidence digest source is not the registry resolver")

    raw_base_images = evidence["base_images"]
    if not isinstance(raw_base_images, dict) or set(raw_base_images) != set(BASE_IMAGE_ROLES):
        raise ValueError("final evidence must bind exactly node, python, and nginx base images")
    base_images = {
        role: _validate_digest_reference(raw_base_images[role], f"base_images.{role}")
        for role in BASE_IMAGE_ROLES
    }
    base_provenance = _validate_artifact(root, evidence["base_provenance"], "base_provenance")
    base_payload = _load_json(root / base_provenance["path"], "base_provenance")
    if base_payload != {
        "schema": "mentorflow.base-image-provenance.v1",
        "resolver": FINAL_DIGEST_SOURCE,
        "images": base_images,
    }:
        raise ValueError("base provenance artifact does not exactly bind the resolved base images")

    raw_images = evidence["images"]
    if not isinstance(raw_images, list) or len(raw_images) != len(IMAGE_ROLES):
        raise ValueError("final evidence must contain exactly four application images")
    images: list[dict[str, object]] = []
    seen_roles: set[str] = set()
    seen_refs: set[str] = set()
    for index, raw_image in enumerate(raw_images):
        field = f"images[{index}]"
        if not isinstance(raw_image, dict) or set(raw_image) != {
            "role", "repository_digest", "digest_source", "artifacts"
        }:
            raise ValueError(f"{field} fields are incomplete or ambiguous")
        role = raw_image["role"]
        if role not in IMAGE_ROLES or role in seen_roles:
            raise ValueError(f"{field}.role is missing, unknown, or duplicated")
        if raw_image["digest_source"] != FINAL_DIGEST_SOURCE:
            raise ValueError(f"{field}.digest_source is not the registry resolver")
        repository_digest = _validate_digest_reference(raw_image["repository_digest"], f"{field}.repository_digest")
        if repository_digest in seen_refs:
            raise ValueError("final evidence must bind four distinct repository digests")
        artifacts = raw_image["artifacts"]
        expected_artifacts = {"build_metadata", "image_archive", "sbom", "security_report"}
        if not isinstance(artifacts, dict) or set(artifacts) != expected_artifacts:
            raise ValueError(f"{field}.artifacts must retain build, archive, SBOM, and security evidence")
        validated_artifacts = {
            name: _validate_artifact(root, artifacts[name], f"{field}.artifacts.{name}")
            for name in sorted(expected_artifacts)
        }
        build_payload = _load_json(
            root / validated_artifacts["build_metadata"]["path"], f"{field}.artifacts.build_metadata"
        )
        resolved_digest = repository_digest.rsplit("@", 1)[1]
        if build_payload.get("containerimage.digest") != resolved_digest:
            raise ValueError(f"{field} registry digest is not bound to retained Buildx metadata")
        sbom_payload = _load_json(root / validated_artifacts["sbom"]["path"], f"{field}.artifacts.sbom")
        if (
            sbom_payload.get("bomFormat") != "CycloneDX"
            or not isinstance(sbom_payload.get("specVersion"), str)
            or not isinstance(sbom_payload.get("components"), list)
            or not sbom_payload["components"]
        ):
            raise ValueError(f"{field} SBOM is not CycloneDX evidence")
        security_payload = _load_json(
            root / validated_artifacts["security_report"]["path"], f"{field}.artifacts.security_report"
        )
        if (
            security_payload.get("version") != "2.1.0"
            or not isinstance(security_payload.get("runs"), list)
            or not security_payload["runs"]
        ):
            raise ValueError(f"{field} security report is not SARIF 2.1.0 evidence")
        seen_roles.add(str(role))
        seen_refs.add(repository_digest)
        images.append(
            {
                "role": role,
                "repository_digest": repository_digest,
                "digest_source": FINAL_DIGEST_SOURCE,
                "artifacts": validated_artifacts,
            }
        )
    if seen_roles != set(IMAGE_ROLES):
        raise ValueError("final evidence does not cover every required application image role")
    images.sort(key=lambda item: IMAGE_ROLES.index(str(item["role"])))
    return {
        "base_images": base_images,
        "base_provenance": base_provenance,
        "images": images,
    }, base_images


def build_final_release_package(
    *,
    root: Path,
    commit: str,
    source_tree: str,
    app_version: str,
    evidence_path: Path,
    output: Path,
    digest_lock_output: Path,
    published: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    root = root.resolve()
    if not published:
        raise ValueError("FINAL_RELEASE_VERIFIED requires published registry digests")
    if not SEMVER.fullmatch(app_version):
        raise ValueError("app-version must use semantic version syntax")
    output = output.resolve()
    digest_lock_output = digest_lock_output.resolve()
    if (
        not output.is_relative_to(root)
        or not digest_lock_output.is_relative_to(root)
        or output == digest_lock_output
    ):
        raise ValueError("final manifest and digest lock must be distinct retained paths inside the release workspace")
    _validate_source_identity(root, commit, source_tree)
    evidence_path = evidence_path.resolve()
    if not evidence_path.is_relative_to(root) or not evidence_path.is_file():
        raise ValueError("final-evidence must be a retained file inside the release workspace")
    validated, base_images = _validate_final_evidence(
        root, evidence_path, commit=commit, source_tree=source_tree
    )
    context_lock = json.loads(
        _git_blob_bytes(root, commit, ".github/enterprise-context.lock.json").decode("utf-8")
    )
    source_locks = {
        path: {
            "sha256": _git_blob_sha256(root, commit, path),
            "hash_algorithm": GIT_BLOB_HASH_ALGORITHM,
        }
        for path in SOURCE_LOCK_PATHS
    }
    evidence_relative = evidence_path.relative_to(root).as_posix()
    evidence_payload = _load_json(evidence_path, "final evidence")
    payload: dict[str, object] = {
        "schema": "mentorflow.enterprise-final-release-manifest.v1",
        "status": "FINAL_RELEASE_VERIFIED",
        "published": True,
        "source_commit": commit,
        "source_tree": source_tree,
        "semantic_version": app_version,
        "enterprise_contract_version": context_lock["contract_version"],
        "enterprise_contract_bundle_sha256": context_lock["contract_bundle_sha256"],
        "migration_version": _migration_heads(root),
        "hash_byte_domains": {
            "source_locks": GIT_BLOB_HASH_ALGORITHM,
            "retained_artifacts": ARTIFACT_HASH_ALGORITHM,
            "structured_content": CANONICAL_JSON_HASH_ALGORITHM,
        },
        "source_locks": source_locks,
        "final_evidence": {
            "path": evidence_relative,
            "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            "hash_algorithm": ARTIFACT_HASH_ALGORITHM,
            "canonical_sha256": canonical_json_sha256(evidence_payload),
            "canonical_hash_algorithm": CANONICAL_JSON_HASH_ALGORITHM,
        },
        "base_images": base_images,
        "base_provenance": validated["base_provenance"],
        "images": validated["images"],
        "provider_mode": "fake",
        "real_provider_calls": 0,
    }
    payload["manifest_content_sha256"] = canonical_json_sha256(payload)
    _write_atomic(output, payload)

    digest_lock: dict[str, object] = {
        "schema": "mentorflow.enterprise-final-digest-lock.v1",
        "source_commit": commit,
        "source_tree": source_tree,
        "manifest": {
            "path": output.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "hash_algorithm": ARTIFACT_HASH_ALGORITHM,
            "content_sha256": payload["manifest_content_sha256"],
            "content_hash_algorithm": CANONICAL_JSON_HASH_ALGORITHM,
        },
        "source_locks": source_locks,
        "base_images": base_images,
        "images": {
            str(item["role"]): str(item["repository_digest"])
            for item in validated["images"]  # type: ignore[index]
        },
        "real_provider_calls": 0,
    }
    digest_lock["digest_lock_content_sha256"] = canonical_json_sha256(digest_lock)
    _write_atomic(digest_lock_output, digest_lock)
    return payload, digest_lock


def _add_legacy_image_arguments(parser: argparse.ArgumentParser) -> None:
    for role in IMAGE_ROLES:
        parser.add_argument(f"--{role}-image")
        parser.add_argument(f"--{role}-semantic-tag")
        parser.add_argument(f"--{role}-sbom-ref")
        parser.add_argument(f"--{role}-security-report-ref")


def _build_legacy_manifest(args: argparse.Namespace, root: Path = ROOT) -> dict[str, object]:
    lock = json.loads((root / ".github" / "enterprise-context.lock.json").read_text(encoding="utf-8"))
    images: list[dict[str, object]] = []
    image_tags: dict[str, str] = {}
    per_image_sbom: list[str] = []
    per_image_security: list[str] = []
    for role in IMAGE_ROLES:
        option = role.replace("-", "_")
        image = getattr(args, f"{option}_image")
        semantic_tag = getattr(args, f"{option}_semantic_tag")
        sbom_ref = getattr(args, f"{option}_sbom_ref")
        security_ref = getattr(args, f"{option}_security_report_ref")
        if not all(isinstance(value, str) and value.strip() for value in (image, sbom_ref, security_ref)):
            raise ValueError(f"legacy manifest requires image, SBOM, and security refs for {role}")
        if not LEGACY_IMMUTABLE_IMAGE.search(image):
            raise ValueError(f"{role} image must use a commit tag or sha256 digest")
        if semantic_tag and (semantic_tag == "latest" or semantic_tag.endswith(":latest")):
            raise ValueError(f"{role} semantic tag must not be latest")
        per_image_sbom.append(sbom_ref)
        per_image_security.append(security_ref)
        image_tags[role] = image
        images.append(
            {
                "name": role,
                "image_version": {
                    "source_commit": args.commit,
                    "source_tree": args.source_tree,
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
        "source_tree": args.source_tree,
        "enterprise_contract_version": lock["contract_version"],
        "enterprise_contract_bundle_sha256": lock["contract_bundle_sha256"],
        "migration_version": _migration_heads(root),
        "image_tags": image_tags,
        "images": images,
        "sbom_refs": _required_refs(args.sbom_ref + per_image_sbom, "sbom_refs", minimum=4),
        "security_report_refs": _required_refs(
            args.security_report_ref + per_image_security, "security_report_refs", minimum=4
        ),
        "asvs_matrix_version": "OWASP-ASVS-5.0.0",
        "load_report_refs": _required_refs(args.load_report_ref, "load_report_refs"),
        "qa_report_refs": _required_refs(args.qa_evidence, "qa_report_refs"),
        "known_blockers": sorted(set(args.known_blocker)),
        "pending_staging_gates": sorted(set(args.pending_gate)),
        "provider_mode": "fake",
        "real_provider_calls": 0,
    }
    payload["manifest_content_sha256"] = canonical_json_sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a MentorFlow Enterprise release manifest.")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-tree")
    parser.add_argument("--app-version", required=True)
    parser.add_argument(
        "--status",
        choices=(
            "IMPLEMENTED",
            "OFFLINE_VERIFIED",
            "CI_CONFIGURED",
            "STAGING_EXECUTION_PENDING",
            "STAGING_VERIFIED",
            "FINAL_RELEASE_VERIFIED",
        ),
        required=True,
    )
    _add_legacy_image_arguments(parser)
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--sbom-ref", action="append", default=[])
    parser.add_argument("--security-report-ref", action="append", default=[])
    parser.add_argument("--load-report-ref", action="append", default=[])
    parser.add_argument("--qa-evidence", action="append", default=[])
    parser.add_argument("--known-blocker", action="append", default=[])
    parser.add_argument("--pending-gate", action="append", default=[])
    parser.add_argument("--final-evidence", type=Path)
    parser.add_argument("--digest-lock-output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not COMMIT.fullmatch(args.commit):
        raise SystemExit("commit must be exactly 40 lowercase hexadecimal characters")
    args.source_tree = args.source_tree or _source_tree(args.commit)
    if not COMMIT.fullmatch(args.source_tree):
        raise SystemExit("source-tree must be exactly 40 lowercase hexadecimal characters")
    if not SEMVER.fullmatch(args.app_version):
        raise SystemExit("app-version must use semantic version syntax")

    try:
        if args.status == "FINAL_RELEASE_VERIFIED":
            legacy_values = [
                getattr(args, f"{role.replace('-', '_')}_{suffix}")
                for role in IMAGE_ROLES
                for suffix in ("image", "semantic_tag", "sbom_ref", "security_report_ref")
            ]
            if any(legacy_values) or any(
                (args.sbom_ref, args.security_report_ref, args.load_report_ref, args.qa_evidence,
                 args.known_blocker, args.pending_gate)
            ):
                raise ValueError("FINAL_RELEASE_VERIFIED rejects legacy staging evidence arguments")
            if args.final_evidence is None or args.digest_lock_output is None:
                raise ValueError("FINAL_RELEASE_VERIFIED requires final-evidence and digest-lock-output")
            payload, _ = build_final_release_package(
                root=ROOT,
                commit=args.commit,
                source_tree=args.source_tree,
                app_version=args.app_version,
                evidence_path=(ROOT / args.final_evidence).resolve()
                if not args.final_evidence.is_absolute()
                else args.final_evidence.resolve(),
                output=(ROOT / args.output).resolve() if not args.output.is_absolute() else args.output.resolve(),
                digest_lock_output=(ROOT / args.digest_lock_output).resolve()
                if not args.digest_lock_output.is_absolute()
                else args.digest_lock_output.resolve(),
                published=args.published,
            )
        else:
            if args.final_evidence is not None or args.digest_lock_output is not None:
                raise ValueError("legacy staging manifests reject final-release evidence arguments")
            payload = _build_legacy_manifest(args)
            output = (ROOT / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
            _write_atomic(output, payload)
    except (OSError, subprocess.CalledProcessError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"E1_FINAL_RELEASE_MANIFEST_FAILED: {exc}") from exc

    print(
        json.dumps(
            {
                "status": "PASS",
                "output": str(args.output.resolve()),
                "migration_heads": payload["migration_version"],
                "images": len(payload["images"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
