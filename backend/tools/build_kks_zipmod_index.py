"""Build the SQLite KKS zipmod index consumed by cache-only web requests.

This is an explicit offline maintenance command.  It reads only manifest.xml
and ListInfo CSV members; it never starts Koikatsu, Unity, or a converter.  A
failed build leaves the previous database untouched.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.koikatsu_mod_assets import (  # noqa: E402
    MOD_INDEX_SCHEMA_VERSION as SCHEMA_VERSION,
    canonical_index_field,
    mod_root_identity,
    sanitize_index_row_values,
    semantic_definition_signature,
)


MAX_LIST_CSV_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ROWS_PER_CSV = 250_000

# A filename is only a fallback when the ListInfo CSV omitted its numeric
# category line.  Full-name matching prevents a substring such as
# ``not_bo_hair_b_00.csv`` from being accepted as category 101.
CATEGORY_FILENAME_PREFIXES = {
    100: "bo_head_",
    101: "bo_hair_b_",
    102: "bo_hair_f_",
    103: "bo_hair_s_",
    104: "bo_hair_o_",
    105: "co_top_",
    106: "co_bot_",
    107: "co_bra_",
    108: "co_shorts_",
    109: "co_gloves_",
    110: "co_panst_",
    111: "co_socks_",
    112: "co_shoes_",
    121: "ao_hair_",
    122: "ao_head_",
    123: "ao_face_",
    124: "ao_neck_",
    125: "ao_body_",
    126: "ao_waist_",
    127: "ao_leg_",
    128: "ao_arm_",
    129: "ao_hand_",
    200: "cpo_sailor_a_",
    201: "cpo_sailor_b_",
    202: "cpo_sailor_c_",
    210: "cpo_jacket_a_",
    211: "cpo_jacket_b_",
    212: "cpo_jacket_c_",
    400: "mt_face_detail_",
    401: "mt_eyeshadow_",
    402: "mt_cheek_",
    403: "mt_lip_",
    404: "mt_lipline_",
    405: "mt_face_paint_",
    406: "mt_eyebrow_",
    407: "mt_eye_white_",
    408: "mt_eye_",
    409: "mt_eye_gradation_",
    410: "mt_eye_hi_up_",
    411: "mt_eye_hi_down_",
    412: "mt_eyeline_up_",
    413: "mt_eyeline_down_",
    414: "mt_nose_",
    415: "mt_mole_",
    430: "mt_pattern_",
}


class IndexBuildError(RuntimeError):
    """The new snapshot was rejected and the prior index was preserved."""


def _safe_member_name(raw_name: str) -> str | None:
    normalized = str(raw_name or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or ".." in path.parts:
        return None
    if path.parts and ":" in path.parts[0]:
        return None
    return path.as_posix()


def _is_list_csv(member_name: str) -> bool:
    normalized = _safe_member_name(member_name)
    if normalized is None or not normalized.casefold().endswith(".csv"):
        return False
    return "/abdata/list/characustom/" in f"/{normalized.casefold()}"


def _inferred_category(member_name: str) -> int | None:
    normalized = _safe_member_name(member_name)
    if normalized is None:
        return None
    filename = PurePosixPath(normalized).name.casefold()
    for category, prefix in CATEGORY_FILENAME_PREFIXES.items():
        if re.fullmatch(rf"{re.escape(prefix)}\d+\.csv", filename):
            return category
    return None


def _decode_csv(data: bytes) -> str:
    if len(data) > MAX_LIST_CSV_BYTES:
        raise ValueError(f"ListInfo CSV exceeds {MAX_LIST_CSV_BYTES} bytes")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Older Japanese ListInfo files occasionally use Windows-932.  This is
        # an explicit compatibility fallback, not errors='ignore'.
        return data.decode("cp932")


def _list_rows(text: str, member_name: str) -> list[tuple[int, dict[str, str]]]:
    reader = csv.reader(io.StringIO(text, newline=""))
    inferred = _inferred_category(member_name)
    explicit: int | None = None
    header: list[tuple[int, str]] | None = None
    result: list[tuple[int, dict[str, str]]] = []
    for row in reader:
        if not row:
            continue
        stripped = [cell.strip() for cell in row]
        if (
            header is None
            and stripped[0].isdigit()
            and all(not cell for cell in stripped[1:])
        ):
            candidate = int(stripped[0])
            # KKS ListInfo files put the resource category on the first
            # numeric-only preamble row.  Later numeric-only rows are other
            # list metadata (commonly 0 or 2), not additional categories.
            # Treating every such row as a category rejected most real
            # Sideloader archives as a false conflict.
            if explicit is None:
                explicit = candidate
            continue
        if stripped[0].casefold() == "id":
            mapped = [
                (index, field)
                for index, raw_field in enumerate(stripped)
                if (field := canonical_index_field(raw_field)) is not None
            ]
            fields = [field for _index, field in mapped]
            # Mesh rows identify a prefab through MainAB; texture-only
            # Character Maker rows such as category 409 eye gradients and
            # category 430 patterns identify the payload through ColorMaskAB
            # or MainTexAB instead.
            if "ID" in fields and (
                "MainAB" in fields
                or "ColorMaskAB" in fields
                or "MainTexAB" in fields
            ):
                if len(fields) != len(set(fields)):
                    raise ValueError("ListInfo contains duplicate canonical fields")
                header = mapped
            continue
        if header is None or not stripped[0].lstrip("-").isdigit():
            continue
        category = explicit if explicit is not None else inferred
        if category is None:
            raise ValueError("ListInfo category is missing and filename is not canonical")
        if category not in CATEGORY_FILENAME_PREFIXES:
            # This cache is deliberately scoped to the Character Maker
            # categories that runtime_card_manifest can request.  Other KKS
            # list types may share the directory but must not enter lookups.
            continue
        if inferred is not None and explicit is not None and inferred != explicit:
            raise ValueError(
                f"ListInfo category {explicit} conflicts with filename category {inferred}"
            )
        values = sanitize_index_row_values({
            field: row[index] if index < len(row) else ""
            for index, field in header
        })
        raw_asset_id = values.get("ID", "")
        if not raw_asset_id or not raw_asset_id.lstrip("-").isdigit():
            continue
        values["ID"] = str(int(raw_asset_id))
        result.append((category, values))
        if len(result) > MAX_ROWS_PER_CSV:
            raise ValueError(f"ListInfo exceeds {MAX_ROWS_PER_CSV} rows")
    return result


def _manifest_guid(bundle: zipfile.ZipFile, entries: list[zipfile.ZipInfo]) -> str:
    candidates: list[tuple[int, str, zipfile.ZipInfo]] = []
    for entry in entries:
        normalized = _safe_member_name(entry.filename)
        if normalized is None:
            continue
        folded = normalized.casefold()
        if folded == "manifest.xml" or folded.endswith("/manifest.xml"):
            candidates.append((0 if folded == "manifest.xml" else 1, folded, entry))
    if not candidates:
        return ""
    entry = min(candidates, key=lambda item: (item[0], item[1]))[2]
    if entry.file_size > MAX_MANIFEST_BYTES:
        raise ValueError(f"manifest.xml exceeds {MAX_MANIFEST_BYTES} bytes")
    root = ElementTree.fromstring(bundle.read(entry))
    for element in root.iter():
        local_name = str(element.tag).rsplit("}", 1)[-1].casefold()
        if local_name == "guid" and str(element.text or "").strip():
            return str(element.text).strip()
    return ""


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version TEXT NOT NULL,
            status TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            archive_count INTEGER NOT NULL,
            invalid_archive_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            mod_root TEXT NOT NULL
        );
        CREATE TABLE archives (
            archive_id INTEGER PRIMARY KEY,
            archive_relpath TEXT NOT NULL UNIQUE,
            archive_size INTEGER NOT NULL,
            archive_mtime_ns INTEGER NOT NULL,
            mod_id TEXT NOT NULL
        );
        CREATE TABLE assets (
            asset_row_id INTEGER PRIMARY KEY,
            archive_id INTEGER NOT NULL REFERENCES archives(archive_id),
            asset_id INTEGER NOT NULL,
            category INTEGER NOT NULL,
            mod_id TEXT NOT NULL,
            list_csv TEXT NOT NULL,
            row_json TEXT NOT NULL,
            definition_key TEXT NOT NULL
        );
        CREATE INDEX assets_lookup
            ON assets(asset_id, category, mod_id);
        """
    )


def _archive_rows(
    archive: Path,
) -> tuple[str, int, int, list[tuple[int, dict[str, str], str]]]:
    before = archive.stat()
    parsed: list[tuple[int, dict[str, str], str]] = []
    with zipfile.ZipFile(archive) as bundle:
        entries = list(bundle.infolist())
        list_entries = sorted(
            (entry for entry in entries if _is_list_csv(entry.filename)),
            key=lambda item: str(item.filename).casefold(),
        )
        # An unrelated zipmod does not need its manifest parsed at all.
        # Keeping this narrow avoids rejecting a package that cannot affect
        # Character Maker resources.
        if not list_entries:
            after = archive.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("archive changed while it was being indexed")
            return "", int(after.st_size), int(after.st_mtime_ns), []
        mod_id = _manifest_guid(bundle, entries)
        for entry in list_entries:
            member_name = _safe_member_name(entry.filename)
            if member_name is None:
                continue
            if entry.file_size > MAX_LIST_CSV_BYTES:
                raise ValueError(
                    f"ListInfo CSV exceeds {MAX_LIST_CSV_BYTES} bytes: {member_name}"
                )
            text = _decode_csv(bundle.read(entry))
            for category, values in _list_rows(text, member_name):
                parsed.append((category, values, member_name))
    after = archive.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("archive changed while it was being indexed")
    if parsed and not mod_id:
        raise ValueError("zipmod has ListInfo rows but no manifest GUID")
    return mod_id, int(after.st_size), int(after.st_mtime_ns), parsed


def build_index(
    mods_root: Path,
    output: Path,
    *,
    allow_partial: bool = False,
    allow_empty: bool = False,
) -> dict[str, Any]:
    root = mods_root.resolve()
    if not root.is_dir():
        raise IndexBuildError(f"mods root is not a directory; old index preserved: {root}")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(
        f".{output.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    connection: sqlite3.Connection | None = None
    archives = 0
    invalid_archives = 0
    row_count = 0
    errors: list[str] = []
    try:
        connection = sqlite3.connect(str(temporary))
        _create_schema(connection)
        archive_paths = sorted(
            root.rglob("*.zipmod"),
            key=lambda item: item.as_posix().casefold(),
        )
        for archive in archive_paths:
            try:
                resolved_archive = archive.resolve()
                relative = resolved_archive.relative_to(root).as_posix()
                if not resolved_archive.is_file() or resolved_archive.suffix.casefold() != ".zipmod":
                    raise ValueError("archive is not a contained .zipmod file")
                mod_id, archive_size, archive_mtime_ns, parsed_rows = _archive_rows(resolved_archive)
                connection.execute("SAVEPOINT one_archive")
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO archives(
                            archive_relpath, archive_size, archive_mtime_ns, mod_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (relative, archive_size, archive_mtime_ns, mod_id),
                    )
                    archive_id = int(cursor.lastrowid)
                    connection.executemany(
                        """
                        INSERT INTO assets(
                            archive_id, asset_id, category, mod_id, list_csv,
                            row_json, definition_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                archive_id,
                                int(values["ID"]),
                                category,
                                mod_id,
                                member_name,
                                json.dumps(
                                    sanitize_index_row_values(values),
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                    sort_keys=True,
                                ),
                                semantic_definition_signature(values),
                            )
                            for category, values, member_name in parsed_rows
                        ],
                    )
                except Exception:
                    connection.execute("ROLLBACK TO SAVEPOINT one_archive")
                    connection.execute("RELEASE SAVEPOINT one_archive")
                    raise
                else:
                    connection.execute("RELEASE SAVEPOINT one_archive")
                archives += 1
                row_count += len(parsed_rows)
            except (
                OSError,
                ValueError,
                UnicodeError,
                csv.Error,
                sqlite3.Error,
                zipfile.BadZipFile,
                ElementTree.ParseError,
            ) as exc:
                invalid_archives += 1
                errors.append(f"{archive}: {exc}")

        empty_errors: list[str] = []
        if archives == 0 and not allow_empty:
            empty_errors.append("no valid .zipmod archives were found")
        if row_count == 0 and not allow_empty:
            empty_errors.append("no KKS ListInfo rows were found")
        if empty_errors:
            errors.extend(empty_errors)
            raise IndexBuildError(
                "index build rejected; old index preserved: " + "; ".join(empty_errors)
            )
        if errors and not allow_partial:
            preview = "; ".join(errors[:3])
            raise IndexBuildError(
                f"index build rejected ({len(errors)} error(s)); old index preserved: {preview}"
            )

        status = "partial" if errors else "ready"
        connection.execute(
            """
            INSERT INTO metadata(
                singleton, schema_version, status, row_count, archive_count,
                invalid_archive_count, error_count, mod_root
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                status,
                row_count,
                archives,
                invalid_archives,
                len(errors),
                mod_root_identity(root),
            ),
        )
        connection.commit()
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).casefold() != "ok":
            raise IndexBuildError("SQLite quick_check failed; old index preserved")
        connection.close()
        connection = None
        # Windows' _commit rejects read-only descriptors (EBADF).
        with temporary.open("r+b") as built:
            os.fsync(built.fileno())
        os.replace(temporary, output)
        return {
            "status": status,
            "archives": archives,
            "invalid_archives": invalid_archives,
            "rows": row_count,
            "errors": len(errors),
            "output": str(output),
        }
    finally:
        if connection is not None:
            connection.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Build the cache-only KKS zipmod SQLite index")
    parser.add_argument("--mods-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "zipmod-index-v1.sqlite3",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Replace the old snapshot even when one or more archives fail validation.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow a deliberately empty mods root to replace the old snapshot.",
    )
    args = parser.parse_args()
    print(json.dumps(
        build_index(
            args.mods_root,
            args.output,
            allow_partial=args.allow_partial,
            allow_empty=args.allow_empty,
        ),
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
