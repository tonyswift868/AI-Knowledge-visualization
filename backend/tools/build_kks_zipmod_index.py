"""Build the persistent KKS zipmod ListInfo index used by web requests.

This is an explicit offline maintenance command. It reads only manifest.xml
and CSV members from zipmods; it never starts Koikatsu, Unity, or a mesh/
texture converter. The completed CSV atomically replaces the prior snapshot.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1"
FIELDNAMES = (
    "schema_version",
    "archive_relpath",
    "archive_size",
    "archive_mtime_ns",
    "mod_id",
    "list_csv",
    "category",
    "asset_id",
    "row_json",
)


def _manifest_guid(bundle: zipfile.ZipFile) -> str:
    manifest_name = next(
        (name for name in bundle.namelist() if name.replace("\\", "/").casefold().endswith("/manifest.xml") or name.casefold() == "manifest.xml"),
        "",
    )
    if not manifest_name:
        return ""
    try:
        manifest = bundle.read(manifest_name).decode("utf-8", errors="ignore")
    except (KeyError, OSError):
        return ""
    match = re.search(r"<guid>\s*([^<]+?)\s*</guid>", manifest, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _list_rows(text: str) -> list[tuple[str, dict[str, str]]]:
    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    category = ""
    result: list[tuple[str, dict[str, str]]] = []
    for row in reader:
        if not row:
            continue
        if (
            header is None
            and row[0].strip().isdigit()
            and not category
            and all(not cell.strip() for cell in row[1:])
        ):
            category = str(int(row[0].strip()))
            continue
        if row[0].strip().upper() == "ID" and "MainAB" in row:
            header = [cell.strip() for cell in row]
            continue
        if header is None or not row[0].strip().lstrip("-").isdigit():
            continue
        values = {
            header[index]: row[index].strip() if index < len(row) else ""
            for index in range(len(header))
        }
        raw_asset_id = values.get("ID", "")
        if not raw_asset_id or not raw_asset_id.lstrip("-").isdigit():
            continue
        # Slot zero is a real selection in some categories (notably back hair).
        # Empty/p_dummy semantics are filtered later by the card/resource layer.
        asset_id = str(int(raw_asset_id))
        values["ID"] = asset_id
        result.append((category, values))
    return result


def build_index(mods_root: Path, output: Path) -> dict[str, Any]:
    root = mods_root.resolve()
    rows: list[dict[str, str]] = []
    archives = 0
    invalid_archives = 0
    for archive in sorted(root.rglob("*.zipmod"), key=lambda item: item.as_posix().casefold()):
        try:
            archive_stat = archive.stat()
            relative = archive.resolve().relative_to(root).as_posix()
            with zipfile.ZipFile(archive) as bundle:
                mod_id = _manifest_guid(bundle)
                for entry in sorted(
                    (item for item in bundle.infolist() if item.filename.casefold().endswith(".csv")),
                    key=lambda item: item.filename.casefold(),
                ):
                    try:
                        text = bundle.read(entry).decode("utf-8-sig")
                        parsed_rows = _list_rows(text)
                    except (OSError, UnicodeDecodeError, csv.Error, zipfile.BadZipFile):
                        continue
                    for category, values in parsed_rows:
                        rows.append({
                            "schema_version": SCHEMA_VERSION,
                            "archive_relpath": relative,
                            "archive_size": str(archive_stat.st_size),
                            "archive_mtime_ns": str(archive_stat.st_mtime_ns),
                            "mod_id": mod_id,
                            "list_csv": entry.filename.replace("\\", "/"),
                            "category": category,
                            "asset_id": values["ID"],
                            "row_json": json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                        })
            archives += 1
        except (OSError, ValueError, zipfile.BadZipFile):
            invalid_archives += 1
    rows.sort(key=lambda item: (
        item["mod_id"].casefold(),
        int(item["category"] or -1),
        int(item["asset_id"]),
        item["archive_relpath"].casefold(),
        item["list_csv"].casefold(),
        item["row_json"],
    ))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=FIELDNAMES, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, output)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {
        "status": "ready",
        "archives": archives,
        "invalid_archives": invalid_archives,
        "rows": len(rows),
        "output": str(output),
    }


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Build the cache-only KKS zipmod CSV index")
    parser.add_argument("--mods-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "data" / "kks-assets" / "zipmod-index-v1.csv",
    )
    args = parser.parse_args()
    print(json.dumps(build_index(args.mods_root, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
