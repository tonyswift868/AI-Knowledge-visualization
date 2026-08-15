from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import datetime
from pathlib import Path


DATABASES = (Path("data/app.db"), Path("data/mentorflow_v2.db"))
DIRECTORIES = (Path("data/active_teaching"), Path("backend/config"))
OPTIONAL_FILES = (Path("weight.json"),)
EXCLUDED_SECRET = Path("MINIMAX_API_KEY.txt")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(source: Path, destination: Path) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        tables = [
            row[0]
            for row in destination_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        return {
            "integrity_check": destination_connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0],
            "tables": {
                table: destination_connection.execute(
                    f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
                for table in tables
            },
        }
    finally:
        destination_connection.close()
        source_connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a fail-if-present MentorFlow pre-change backup."
    )
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument(
        "--destination-root",
        type=Path,
        default=Path(r"E:\Codex\Data\backups\MentorFlow"),
    )
    parser.add_argument("--label", default="knowledge-map-state")
    args = parser.parse_args()

    project_root = args.project_root.expanduser().resolve(strict=True)
    destination_root = args.destination_root.expanduser().resolve()
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    destination = (destination_root / f"{timestamp}-{args.label}").resolve()
    if destination.exists():
        raise FileExistsError(f"backup target already exists: {destination}")
    destination.mkdir(parents=True)

    database_report: dict[str, object] = {}
    for relative in DATABASES:
        source = project_root / relative
        if not source.is_file():
            raise FileNotFoundError(source)
        database_report[relative.as_posix()] = backup_database(
            source, destination / relative
        )
    for relative in DIRECTORIES:
        source = project_root / relative
        if source.exists():
            shutil.copytree(source, destination / relative)
    for relative in OPTIONAL_FILES:
        source = project_root / relative
        if source.is_file():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    (destination / "sqlite-integrity-and-row-counts.json").write_text(
        json.dumps(database_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = []
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        manifest.append(
            {
                "relative_path": path.relative_to(destination).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    (destination / "manifest-sha256.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / "RESTORE.txt").write_text(
        "\n".join(
            (
                "Stop MentorFlow before restoring.",
                "Preserve the then-current state separately.",
                "Copy each backed-up relative path to the same project-relative path.",
                "Verify every SHA-256 and rerun PRAGMA integrity_check before startup.",
                f"Explicitly excluded and never read: {EXCLUDED_SECRET.as_posix()}",
            )
        ),
        encoding="utf-8",
    )
    for path in (item for item in destination.rglob("*") if item.is_file()):
        os.chmod(path, stat.S_IREAD)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
