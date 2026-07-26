#!/usr/bin/env python3
"""Package one cache-ready native KKS card for customer-side import.

The pack contains the PNG card, a self-contained runtime manifest, and the
offline-converted shared skin/hierarchy/texture files referenced by that
manifest.  It does not launch KKS or Blender.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (PROJECT_ROOT, BACKEND_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.koikatsu_card import inspect_koikatsu_sunshine_card  # noqa: E402
from app.koikatsu_teacher_registry import (  # noqa: E402
    LOCAL_CARD_ROOT,
    resolve_local_card,
    resolve_runtime_asset,
    resolve_shared_base_asset,
    resolve_shared_runtime_asset,
    runtime_card_manifest,
)

RUNTIME_PACK_SCHEMA = "mentorflow.kks-runtime-pack.v1"
RUNTIME_PACK_MANIFEST = "mentorflow-kks-runtime-pack.json"
_RUNTIME_ASSET_KEYS = {"texture", "colorMask", "alphaMask", "patternTexture", "detailMask"}
_RUNTIME_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".json"}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _slug(value: str, fallback: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value.strip(), flags=re.UNICODE)
    return re.sub(r"-{2,}", "-", value).strip("-._")[:80] or fallback


def _category(card_path: Path) -> tuple[str, str, Path]:
    relative = card_path.resolve().relative_to(LOCAL_CARD_ROOT.resolve())
    parts = list(relative.parts)
    hierarchy = [part for part in parts[1:-1] if part]
    if not hierarchy:
        return "默认角色", "默认角色", Path("默认角色")
    return hierarchy[-1], " / ".join(hierarchy), Path(*(_slug(part, "未分类") for part in hierarchy))


def _asset_source(url: str, card_id: str) -> tuple[str, Path] | None:
    value = str(url or "")
    shared_runtime = "/api/koikatsu-cards/runtime-shared/"
    shared_base = "/api/koikatsu-cards/shared-base/"
    local_runtime = f"/api/koikatsu-cards/local/{card_id}/runtime/"
    if shared_runtime in value:
        relative = unquote(value.split(shared_runtime, 1)[1])
        return f"assets/runtime-shared/{relative}", resolve_shared_runtime_asset(relative)
    if shared_base in value:
        relative = unquote(value.split(shared_base, 1)[1])
        return f"assets/shared-base/{relative}", resolve_shared_base_asset(relative)
    if local_runtime in value:
        relative = unquote(value.split(local_runtime, 1)[1])
        return f"assets/local-runtime/{relative}", resolve_runtime_asset(card_id, relative)
    return None


def _rewrite(value: Any, card_id: str, files: dict[str, Path], pack_id: str) -> Any:
    if isinstance(value, str):
        source = _asset_source(value, card_id)
        if source is None:
            return value
        archive_name, path = source
        files[archive_name] = path
        return f"/api/koikatsu-runtime-packs/{pack_id}/asset/{archive_name.removeprefix('assets/')}"
    if isinstance(value, list):
        return [_rewrite(item, card_id, files, pack_id) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, card_id, files, pack_id) for key, item in value.items()}
    return value


def _rewrite_embedded_runtime_json(
    files: dict[str, Path],
    generated: dict[str, bytes],
    pack_id: str,
) -> None:
    """Rewrite relative texture references inside copied runtime skin JSON.

    The top-level runtime manifest uses API URLs, but each skin payload stores
    material maps as sibling filenames (for example ``foo.png`` and
    ``foo-color-mask.png``). A ZIP containing the JSON without rewriting those
    references makes the browser request the files from the server's cache,
    producing avoidable 404s for every missing optional mask. Only asset-like
    keys are considered; arbitrary character metadata remains untouched.
    """

    index = 0
    while index < len(files):
        archive_name = list(files)[index]
        index += 1
        source = files[archive_name]
        if source.suffix.casefold() != ".json" or not source.is_file():
            continue
        # These payloads are integrity-addressed by headRuntime.digest or are
        # referenced by size in the manifest. Re-serializing them would change
        # otherwise valid bytes and make the browser reject the exact driver.
        if source.name.casefold() in {"head-shape-driver.json", "hierarchy.json"}:
            continue
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        archive_dir = Path(archive_name).parent
        source_dir = source.parent

        def visit(value: Any, key: str = "") -> Any:
            if isinstance(value, list):
                return [visit(item, key) for item in value]
            if isinstance(value, dict):
                return {name: visit(item, str(name)) for name, item in value.items()}
            if not isinstance(value, str) or key not in _RUNTIME_ASSET_KEYS:
                return value
            relative = Path(value.replace("\\", "/"))
            if relative.is_absolute() or relative.suffix.casefold() not in _RUNTIME_ASSET_SUFFIXES:
                return value
            candidate = (source_dir / relative).resolve()
            try:
                candidate.relative_to(source_dir.resolve())
            except ValueError:
                return None
            if not candidate.is_file():
                # Missing optional maps must not survive as a browser URL. The
                # renderer already treats an absent map as a material fallback.
                return None
            archive_relative = (archive_dir / relative).as_posix()
            files.setdefault(archive_relative, candidate)
            return f"/api/koikatsu-runtime-packs/{pack_id}/asset/{archive_relative.removeprefix('assets/')}"

        rewritten = visit(payload)
        generated[archive_name] = (json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a customer-downloadable runtime pack for one cache-ready KKS card.")
    parser.add_argument("--card-id", required=True)
    parser.add_argument("--display-name", default="", help="optional curated catalog name from the offline queue")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "character-packs")
    parser.add_argument("--creator", default="本地恋活资源库")
    parser.add_argument("--source-url", default="local://user-kks-library")
    parser.add_argument("--license-name", default="用户提供的非商用本地资源")
    parser.add_argument("--license-url", default="")
    parser.add_argument("--force", action="store_true")
    return parser


def _update_catalog(root: Path, item: dict[str, Any]) -> None:
    catalog_path = root / "catalog.json"
    payload: dict[str, Any] = {"schema": "mentorflow.kks-character-pack-batch.v1", "items": []}
    if catalog_path.is_file():
        try:
            loaded = json.loads(catalog_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    rows = [row for row in payload.get("items") or [] if isinstance(row, dict) and row.get("card_fingerprint") != item.get("card_fingerprint")]
    rows.append(item)
    payload["items"] = rows
    payload["count"] = len(rows)
    root.mkdir(parents=True, exist_ok=True)
    temporary = catalog_path.with_name(f".{catalog_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, catalog_path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    card_id = str(args.card_id).strip().casefold()
    card_path = resolve_local_card(card_id)
    inspected = inspect_koikatsu_sunshine_card(card_path.read_bytes())
    fingerprint = str(inspected["native_fingerprint"])
    # Packaging is an explicit offline prewarm operation.  Allow the shared
    # runtime cache to upgrade legacy material contracts here; normal browser
    # card selection remains cache-only and never launches the game.
    runtime = runtime_card_manifest(card_id, allow_conversion=True)
    if runtime.get("status") != "ready":
        raise SystemExit(f"card {card_id} is not runtime-ready: {runtime.get('status')}")
    display_name = str(
        args.display_name.strip()
        or (runtime.get("card") or {}).get("character", {}).get("display_name")
        or card_path.stem
    )
    category, category_path, category_dir = _category(card_path)
    pack_id = f"{card_id}-{fingerprint[:12]}-runtime"
    output = args.output_root.resolve() / category_dir / f"{_slug(display_name, card_id)}-{pack_id}.zip"
    files: dict[str, Path] = {"card.png": card_path.resolve()}
    rewritten = _rewrite(runtime, card_id, files, pack_id)
    generated_json: dict[str, bytes] = {}
    _rewrite_embedded_runtime_json(files, generated_json, pack_id)
    asset_entries = []
    for name, path in sorted(files.items()):
        content = generated_json.get(name) or path.read_bytes()
        asset_entries.append({"path": name, "sha256": _sha256(content), "size": len(content)})
    manifest = {
        "schema": RUNTIME_PACK_SCHEMA,
        "pack_id": pack_id,
        "display_name": display_name,
        "creator": str(args.creator).strip(),
        "source_url": str(args.source_url).strip(),
        "license_name": str(args.license_name).strip(),
        "license_url": str(args.license_url).strip(),
        "category": category,
        "category_path": category_path,
        "card_id": card_id,
        "card_fingerprint": fingerprint,
        "runtime_manifest": "runtime/manifest.json",
        "assets": asset_entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not args.force:
        print(json.dumps({"status": "existing", "pack": str(output), "pack_id": pack_id}, ensure_ascii=False, indent=2))
        return 0
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(RUNTIME_PACK_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("runtime/manifest.json", json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n")
            for name, path in sorted(files.items()):
                if name in generated_json:
                    archive.writestr(name, generated_json[name])
                else:
                    archive.write(path, name)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    _update_catalog(args.output_root.resolve(), {
        "card_id": card_id,
        "pack_id": pack_id,
        "display_name": display_name,
        "category": category,
        "category_path": category_path,
        "creator": str(args.creator).strip(),
        "card_fingerprint": fingerprint,
        "size_bytes": output.stat().st_size,
        "pack_relpath": output.relative_to(args.output_root.resolve()).as_posix(),
        "asset_profile": "kks-runtime-pack",
        "runtime_manifest_url": f"/api/koikatsu-runtime-packs/{pack_id}/manifest",
    })
    print(json.dumps({"status": "built", "pack": str(output), "pack_id": pack_id, "display_name": display_name, "category_path": category_path, "asset_count": len(asset_entries), "size_bytes": output.stat().st_size}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
