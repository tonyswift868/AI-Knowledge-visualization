from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from staging_contracts import (
    EVIDENCE_CATEGORIES,
    canonical_sha256,
    file_sha256,
    write_atomic_json,
)
from staging_release_orchestrator import load_package


SENSITIVE_NAME = re.compile(r"(?:^|[._-])(secret|password|credential|private[-_]?key|token|env)(?:$|[._-])", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_items(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("evidence item must be CATEGORY=RELATIVE_PATH")
        category, relative = value.split("=", 1)
        if category not in EVIDENCE_CATEGORIES or category in result or not relative.strip():
            raise ValueError(f"invalid or duplicate evidence category: {category}")
        result[category] = relative.strip()
    missing = sorted(set(EVIDENCE_CATEGORIES) - set(result))
    if missing:
        raise ValueError(f"evidence bundle is missing categories: {missing}")
    return result


def _safe_files(root: Path, relative: str, category: str) -> list[Path]:
    candidate = root / relative
    path = candidate.resolve()
    current = candidate
    contains_symlink = False
    while current != root and root in current.parents:
        if current.is_symlink():
            contains_symlink = True
            break
        current = current.parent
    if not path.is_relative_to(root) or not path.exists() or contains_symlink:
        raise ValueError(f"evidence path escaped, is missing, or is a symlink: {relative}")
    files = [path] if path.is_file() else [item for item in sorted(path.rglob("*")) if item.is_file()]
    if not files:
        raise ValueError(f"evidence path contains no files: {relative}")
    for item in files:
        current = item
        while current != path:
            if current.is_symlink():
                raise ValueError(f"evidence path contains a symlink: {item.relative_to(root).as_posix()}")
            current = current.parent
        if SENSITIVE_NAME.search(item.name):
            raise ValueError(f"sensitive-looking file cannot enter the evidence bundle: {item.name}")
    if category in {"backup_refs", "otel_refs"} and any(item.suffix.casefold() != ".json" for item in files):
        raise ValueError(f"{category} must contain JSON references, never raw backup or telemetry payloads")
    return files


def collect_bundle(
    *,
    evidence_root: Path,
    items: dict[str, str],
    release_id: str,
    source_commit: str,
    index_path: Path,
    archive_path: Path,
    release_package_sha256: str | None = None,
    release_package_content_sha256: str | None = None,
) -> dict[str, Any]:
    root = evidence_root.resolve()
    if not root.is_dir():
        raise ValueError("evidence root must be an existing directory")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("source commit must be exact")
    entries: list[dict[str, Any]] = []
    archive_members: list[tuple[Path, str]] = []
    for category in EVIDENCE_CATEGORIES:
        files = _safe_files(root, items[category], category)
        for file in files:
            relative = file.relative_to(root).as_posix()
            member = f"evidence/{category}/{relative}"
            entries.append({
                "category": category,
                "source_path": relative,
                "archive_path": member,
                "sha256": file_sha256(file),
                "size_bytes": file.stat().st_size,
            })
            archive_members.append((file, member))
    index: dict[str, Any] = {
        "schema": "mentorflow.staging-evidence-bundle.v1",
        "status": "COLLECTED",
        "created_at_utc": _now(),
        "release_id": release_id,
        "source_commit": source_commit,
        "release_package_sha256": release_package_sha256,
        "release_package_content_sha256": release_package_content_sha256,
        "categories": list(EVIDENCE_CATEGORIES),
        "entries": entries,
    }
    index["bundle_content_sha256"] = canonical_sha256({key: value for key, value in index.items() if key != "created_at_utc"})
    write_atomic_json(index_path.resolve(), index)
    archive_path = archive_path.resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=archive_path.name + ".", suffix=".tmp", dir=archive_path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(index_path.resolve(), arcname="bundle-index.json")
            for file, member in archive_members:
                archive.write(file, arcname=member)
        os.replace(temporary, archive_path)
    finally:
        temporary.unlink(missing_ok=True)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect hashed staging evidence without secrets or raw backups.")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--item", action="append", default=[], help="CATEGORY=RELATIVE_PATH; all categories are required")
    parser.add_argument("--release-package", type=Path, help="Exact release-package.json; preferred for immutable identity")
    parser.add_argument("--release-id")
    parser.add_argument("--source-commit")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    release_package_sha256: str | None = None
    release_package_content_sha256: str | None = None
    if args.release_package:
        package_path = args.release_package.resolve()
        package_root, package = load_package(package_path.parent)
        if package_path != package_root / "release-package.json":
            parser.error("--release-package must name the verified release-package.json at package root")
        release_id = str(package["release_id"])
        source_commit = str(package["source_commit"])
        release_package_sha256 = file_sha256(package_path)
        release_package_content_sha256 = str(package["package_content_sha256"])
        if args.release_id and args.release_id != release_id:
            parser.error("--release-id does not match --release-package")
        if args.source_commit and args.source_commit != source_commit:
            parser.error("--source-commit does not match --release-package")
    else:
        if not args.release_id or not args.source_commit:
            parser.error("provide --release-package or both --release-id and --source-commit")
        release_id = args.release_id
        source_commit = args.source_commit
    index = collect_bundle(
        evidence_root=args.evidence_root,
        items=_parse_items(args.item),
        release_id=release_id,
        source_commit=source_commit,
        index_path=args.index,
        archive_path=args.archive,
        release_package_sha256=release_package_sha256,
        release_package_content_sha256=release_package_content_sha256,
    )
    print(json.dumps({"status": index["status"], "entries": len(index["entries"]), "archive": str(args.archive.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
