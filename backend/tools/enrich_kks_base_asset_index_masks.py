"""Add native clothing-mask ListInfo fields to the existing base index.

The base asset index predates the browser's shared-body assembly path and
therefore retained mesh identity but discarded ``OverBodyMask`` and
``OverBraMask``.  This bounded maintenance command reads only the small
``abdata/list/characustom`` bundles, merges four scalar fields into category
105 rows, and atomically replaces the generated JSON.  It never starts KKS or
walks character cards/mod archives.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import msgpack

try:
    import UnityPy  # type: ignore
except ImportError as exc:  # pragma: no cover - optional maintenance dependency
    raise SystemExit("UnityPy is required to enrich the KKS base asset index") from exc


MASK_FIELDS = {
    "OverBodyMaskAB": "over_body_mask_ab",
    "OverBodyMask": "over_body_mask",
    "OverBraMaskAB": "over_bra_mask_ab",
    "OverBraMask": "over_bra_mask",
}
MAX_LIST_BUNDLE_BYTES = 32 * 1024 * 1024


def _text_asset_payload(value: Any) -> bytes:
    script = getattr(value, "m_Script", b"")
    if isinstance(script, str):
        # UnityPy decodes arbitrary TextAsset bytes with surrogateescape.
        return script.encode("utf-8", "surrogateescape")
    return bytes(script)


def read_top_mask_rows(list_root: Path) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for bundle in sorted(list_root.glob("*.unity3d"), key=lambda path: path.name.casefold()):
        size = bundle.stat().st_size
        if size < 1 or size > MAX_LIST_BUNDLE_BYTES:
            raise ValueError(f"unsafe list bundle size: {bundle}")
        environment = UnityPy.load(str(bundle))
        for obj in environment.objects:
            if obj.type.name != "TextAsset":
                continue
            data = obj.read()
            list_name = str(getattr(data, "m_Name", "") or "")
            if not list_name.startswith("co_top_"):
                continue
            payload = msgpack.unpackb(
                _text_asset_payload(data), raw=False, strict_map_key=False
            )
            if not isinstance(payload, dict) or int(payload.get("categoryNo", -1)) != 105:
                continue
            keys = payload.get("lstKey")
            rows = payload.get("dictList")
            if not isinstance(keys, list) or not isinstance(rows, dict):
                continue
            field_indices = {
                output: keys.index(source)
                for source, output in MASK_FIELDS.items()
                if source in keys
            }
            id_index = keys.index("ID") if "ID" in keys else -1
            if id_index < 0:
                continue
            for raw_row in rows.values():
                if not isinstance(raw_row, list) or id_index >= len(raw_row):
                    continue
                row_id = str(raw_row[id_index])
                values = {
                    output: str(raw_row[index])
                    for output, index in field_indices.items()
                    if index < len(raw_row)
                }
                result[(list_name, row_id)] = values
    return result


def enrich_index(payload: dict[str, Any], mask_rows: dict[tuple[str, str], dict[str, str]]) -> int:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("base index rows are missing")
    changed = 0
    for row in rows:
        if not isinstance(row, dict) or str(row.get("category")) != "105":
            continue
        values = mask_rows.get((str(row.get("list") or ""), str(row.get("id") or "")))
        if values is None:
            continue
        if any(str(row.get(key) or "") != value for key, value in values.items()):
            row.update(values)
            changed += 1
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_root", type=Path)
    parser.add_argument("index", type=Path)
    args = parser.parse_args()
    list_root = args.game_root / "abdata" / "list" / "characustom"
    payload = json.loads(args.index.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("base index root must be an object")
    mask_rows = read_top_mask_rows(list_root)
    changed = enrich_index(payload, mask_rows)
    temporary = args.index.with_name(f".{args.index.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, args.index)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"mask_rows": len(mask_rows), "changed_rows": changed}))


if __name__ == "__main__":
    main()
