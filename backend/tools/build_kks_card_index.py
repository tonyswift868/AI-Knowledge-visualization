"""Build the SQLite snapshot used for low-load local KKS card browsing.

This command is deliberately offline: recursive filesystem discovery happens
only here, never in an HTTP request.  A rejected or interrupted build leaves
the previous database untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.koikatsu_card_index import (  # noqa: E402
    CARD_FOLDERS,
    CARD_INDEX_SCHEMA_VERSION as SCHEMA_VERSION,
    card_id_for_relative_path,
    card_root_identity,
)


class IndexBuildError(RuntimeError):
    """The new snapshot was rejected and the previous index was preserved."""


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version TEXT NOT NULL,
            status TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            card_root TEXT NOT NULL,
            built_at_utc TEXT NOT NULL
        );
        CREATE TABLE cards (
            card_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            folder TEXT NOT NULL,
            filename TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL
        );
        CREATE INDEX cards_folder_path
            ON cards(folder COLLATE NOCASE, relative_path COLLATE NOCASE);
        CREATE INDEX cards_filename
            ON cards(filename COLLATE NOCASE);
        """
    )


def _card_rows(root: Path) -> tuple[list[tuple[str, str, str, str, int, int]], list[str]]:
    """Scan only the two native KKS character folders."""

    rows: list[tuple[str, str, str, str, int, int]] = []
    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    seen_paths: set[str] = set()
    for folder in sorted(CARD_FOLDERS):
        folder_root = root / folder
        if not folder_root.is_dir():
            continue
        try:
            folder_value = folder_root.lstat()
            folder_is_link = stat.S_ISLNK(folder_value.st_mode)
            folder_is_junction = bool(
                getattr(folder_root, "is_junction", lambda: False)()
            )
        except OSError as exc:
            errors.append(f"{folder_root}: directory validation failed: {exc}")
            continue
        if folder_is_link or folder_is_junction:
            errors.append(
                f"{folder_root}: symbolic-link/junction card roots are not indexed"
            )
            continue

        candidates: list[Path] = []

        def record_walk_error(error: OSError) -> None:
            location = str(getattr(error, "filename", "") or folder_root)
            errors.append(f"{location}: directory scan failed: {error}")

        try:
            for current_value, directory_names, filenames in os.walk(
                folder_root,
                topdown=True,
                onerror=record_walk_error,
                followlinks=False,
            ):
                current = Path(current_value)
                safe_directories: list[str] = []
                for directory_name in sorted(directory_names, key=str.casefold):
                    directory = current / directory_name
                    try:
                        directory_value = directory.lstat()
                        is_link = stat.S_ISLNK(directory_value.st_mode)
                        is_junction = bool(
                            getattr(directory, "is_junction", lambda: False)()
                        )
                    except OSError as exc:
                        errors.append(f"{directory}: directory validation failed: {exc}")
                        continue
                    if is_link or is_junction:
                        errors.append(
                            f"{directory}: symbolic-link/junction directories are not indexed"
                        )
                        continue
                    safe_directories.append(directory_name)
                directory_names[:] = safe_directories
                candidates.extend(
                    current / filename
                    for filename in sorted(filenames, key=str.casefold)
                    if Path(filename).suffix.casefold() == ".png"
                )
        except OSError as exc:
            errors.append(f"{folder_root}: directory scan failed: {exc}")
            continue
        candidates.sort(key=lambda item: (item.as_posix().casefold(), item.as_posix()))
        for candidate in candidates:
            try:
                if candidate.is_symlink():
                    raise ValueError("symbolic-link cards are not indexed")
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
                if not resolved.is_file() or resolved.suffix.casefold() != ".png":
                    raise ValueError("entry is not a contained PNG file")
                relative = candidate.relative_to(root).as_posix()
                parts = Path(relative).parts
                if (
                    len(parts) < 2
                    or parts[0].casefold() != folder
                    or ".." in parts
                    or "\\" in relative
                    or any(":" in part for part in parts)
                    or "\x00" in relative
                ):
                    raise ValueError("entry has an invalid relative path")
                # Preserve the path's spelling: it is both the stable identity
                # input and the path used by the read-side boundary checks.
                token = card_id_for_relative_path(relative)
                previous = seen_ids.get(token)
                if previous is not None and previous != relative:
                    raise ValueError(
                        f"SHA-256 card id collision with {previous!r}"
                    )
                if relative in seen_paths:
                    raise ValueError("duplicate relative card path")
                value = resolved.stat()
                rows.append((
                    token,
                    relative,
                    folder,
                    candidate.name,
                    int(value.st_mtime_ns),
                    int(value.st_size),
                ))
                seen_ids[token] = relative
                seen_paths.add(relative)
            except (OSError, ValueError) as exc:
                errors.append(f"{candidate}: {exc}")
    rows.sort(key=lambda row: (row[1].casefold(), row[1]))
    return rows, errors


def build_index(
    cards_root: Path,
    output: Path,
    *,
    allow_partial: bool = False,
    allow_empty: bool = False,
    allow_nonstandard_root: bool = False,
) -> dict[str, Any]:
    """Build, integrity-check, fsync, then atomically replace the snapshot."""

    try:
        root = cards_root.resolve()
        output = output.resolve()
    except OSError as exc:
        raise IndexBuildError(
            f"card root or output path is invalid; old index preserved: {exc}"
        ) from exc
    if not root.is_dir():
        raise IndexBuildError(
            f"card root is not a directory; old index preserved: {root}"
        )
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise IndexBuildError(
            "index output must be outside the KKS card root; old data preserved: "
            f"{output}"
        )
    native_layout = (
        root.name.casefold() == "chara"
        and root.parent.name.casefold() == "userdata"
    )
    native_folders = [folder for folder in CARD_FOLDERS if (root / folder).is_dir()]
    if not native_folders:
        raise IndexBuildError(
            "card root has no female or male folder; old index preserved: "
            f"{root}"
        )
    if not native_layout and not allow_nonstandard_root:
        raise IndexBuildError(
            "card root is not a KKS UserData/chara directory; "
            f"old index preserved: {root}"
        )

    # Complete the scan before creating a temporary database.  This both
    # minimizes leftover files after read failures and prevents an output
    # located under the card root from influencing discovery.
    try:
        rows, errors = _card_rows(root)
    except OSError as exc:
        raise IndexBuildError(
            f"card scan failed; old index preserved: {exc}"
        ) from exc
    if not rows and not allow_empty:
        raise IndexBuildError(
            "no KKS PNG cards were found; old index preserved"
        )
    if errors and not allow_partial:
        preview = "; ".join(errors[:3])
        raise IndexBuildError(
            f"index build rejected ({len(errors)} error(s)); "
            f"old index preserved: {preview}"
        )

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IndexBuildError(
            f"index output directory is unavailable; old index preserved: {exc}"
        ) from exc
    temporary = output.with_name(
        f".{output.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(temporary))
        _create_schema(connection)
        connection.executemany(
            """
            INSERT INTO cards(
                card_id, relative_path, folder, filename, mtime_ns, size
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        status = "partial" if errors else "ready"
        connection.execute(
            """
            INSERT INTO metadata(
                singleton, schema_version, status, row_count, error_count,
                card_root, built_at_utc
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                status,
                len(rows),
                len(errors),
                card_root_identity(root),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).casefold() != "ok":
            raise IndexBuildError(
                "SQLite quick_check failed; old index preserved"
            )
        connection.close()
        connection = None
        # Windows rejects fsync on a read-only descriptor (EBADF). Reopen the
        # fully checked SQLite snapshot writable solely for the durability
        # barrier, then atomically replace the prior index.
        with temporary.open("r+b") as built:
            os.fsync(built.fileno())
        os.replace(temporary, output)
        return {
            "status": status,
            "cards": len(rows),
            "errors": len(errors),
            "card_root": str(root),
            "output": str(output),
        }
    except IndexBuildError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise IndexBuildError(
            f"index build failed; old index preserved: {exc}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Build the cache-only KKS character-card SQLite index"
    )
    parser.add_argument("--cards-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            project_root
            / "legacy"
            / "koikatsu-3d"
            / "resources"
            / "runtime"
            / "card-index-v1.sqlite3"
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Replace the snapshot even if one or more card entries are invalid.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow an intentionally empty native card root to replace the snapshot.",
    )
    parser.add_argument(
        "--allow-nonstandard-root",
        action="store_true",
        help="Explicitly index a copied female/male layout outside UserData/chara.",
    )
    args = parser.parse_args()
    print(json.dumps(
        build_index(
            args.cards_root,
            args.output,
            allow_partial=args.allow_partial,
            allow_empty=args.allow_empty,
            allow_nonstandard_root=args.allow_nonstandard_root,
        ),
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
