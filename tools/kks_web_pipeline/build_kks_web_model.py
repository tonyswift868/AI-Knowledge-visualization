#!/usr/bin/env python3
"""Build one browser-ready KKBP character without touching the source export."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = Path(__file__).resolve().parent
DEFAULT_BLENDER = Path(
    r"E:\CodexData\temp\blender-5.2.0-portable"
    r"\blender-5.2.0-windows-x64\blender.exe"
)
DEFAULT_PROFILE = Path(r"E:\CodexData\temp\blender-kks-profile")
REQUIRED_NODES = {
    "cf_J_FaceBase",
    "cf_s_head",
    "cf_s_arm01_L",
    "cf_s_arm01_R",
    "cf_s_forearm01_L",
    "cf_s_forearm01_R",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert one complete KKBP Export_PMX directory into a bounded "
            "2K WebP/Meshopt GLB and publish it atomically by card id."
        )
    )
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--card-id", required=True)
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "web-models",
    )
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def validated_card_id(value: str) -> str:
    value = value.strip().lower()
    if len(value) != 16 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("card id must be exactly 16 lowercase hexadecimal characters")
    return value


def validate_export(export_dir: Path) -> Path:
    resolved = export_dir.resolve(strict=True)
    required = (
        resolved / "model.pmx",
        resolved / "Outfit 00" / "model.pmx",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("incomplete KKBP export; missing: " + ", ".join(missing))
    return resolved


def command_environment(profile: Path) -> dict[str, str]:
    profile = profile.resolve()
    environment = os.environ.copy()
    environment.update(
        {
            "BLENDER_USER_CONFIG": str(profile / "config"),
            "BLENDER_USER_SCRIPTS": str(profile / "scripts"),
            "BLENDER_USER_EXTENSIONS": str(profile / "extensions"),
        }
    )
    return environment


def run_stage(name: str, command: list[str], environment: dict[str, str], dry_run: bool) -> None:
    print(json.dumps({"stage": name, "command": command}, ensure_ascii=False))
    if dry_run:
        return
    completed = subprocess.run(command, env=environment, check=False)
    if completed.returncode:
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}")


def read_glb_json(path: Path) -> dict:
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("GLB header is truncated")
        magic, version, total_length, json_length, json_type = struct.unpack("<IIIII", header)
        if magic != 0x46546C67 or version != 2 or total_length != path.stat().st_size:
            raise ValueError("invalid GLB header")
        if json_type != 0x4E4F534A:
            raise ValueError("GLB first chunk is not JSON")
        return json.loads(stream.read(json_length).rstrip(b"\x00 ").decode("utf-8"))


def validate_glb(path: Path) -> dict[str, object]:
    payload = read_glb_json(path)
    extensions = set(payload.get("extensionsUsed") or ())
    missing_extensions = {"EXT_meshopt_compression", "EXT_texture_webp"} - extensions
    if missing_extensions:
        raise ValueError(f"GLB is missing extensions: {sorted(missing_extensions)}")
    node_names = {str(node.get("name") or "") for node in payload.get("nodes") or ()}
    missing_nodes = REQUIRED_NODES - node_names
    if missing_nodes:
        raise ValueError(f"GLB is missing required KKS nodes: {sorted(missing_nodes)}")
    node_count = len(payload.get("nodes") or ())
    mesh_count = len(payload.get("meshes") or ())
    material_count = len(payload.get("materials") or ())
    image_count = len(payload.get("images") or ())
    if node_count < 800:
        raise ValueError(f"GLB retained only {node_count} nodes; expected at least 800")
    if mesh_count < 3 or material_count < 5 or image_count < 3:
        raise ValueError("GLB does not contain the expected body/outfit/hair material groups")
    return {
        "bytes": path.stat().st_size,
        "nodes": node_count,
        "meshes": mesh_count,
        "materials": material_count,
        "images": image_count,
        "extensions": sorted(extensions),
    }


def publish_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def main() -> int:
    args = parse_args()
    card_id = validated_card_id(args.card_id)
    export_dir = validate_export(args.export_dir)
    blender = args.blender.resolve(strict=not args.dry_run)
    profile = args.profile.resolve()
    output_root = args.output_root.resolve()
    destination_dir = (output_root / card_id).resolve()
    destination_dir.relative_to(output_root)
    destination = destination_dir / "model.glb"

    if destination.is_file() and not args.force:
        receipt = validate_glb(destination)
        print(json.dumps({"status": "skipped", "output": str(destination), **receipt}, ensure_ascii=False))
        return 0

    owned_temporary = args.work_root is None
    work_root = (
        Path(tempfile.mkdtemp(prefix=f"kks-web-{card_id}-"))
        if owned_temporary
        else args.work_root.resolve()
    )
    work_root.mkdir(parents=True, exist_ok=True)
    imported_blend = work_root / "imported.blend"
    atlas_blend = work_root / "atlas.blend"
    generated_glb = work_root / "model.glb"
    environment = command_environment(profile)
    common = [str(blender), "-b"]

    try:
        run_stage(
            "import",
            common + ["-P", str(SCRIPT_ROOT / "import_kkbp.py"), "--", str(export_dir), str(imported_blend)],
            environment,
            args.dry_run,
        )
        run_stage(
            "bake",
            common + [str(imported_blend), "-P", str(SCRIPT_ROOT / "bake_kkbp.py"), "--", str(work_root), str(atlas_blend)],
            environment,
            args.dry_run,
        )
        run_stage(
            "export",
            common + [str(atlas_blend), "-P", str(SCRIPT_ROOT / "export_web_glb.py"), "--", str(generated_glb)],
            environment,
            args.dry_run,
        )
        if args.dry_run:
            print(json.dumps({"status": "dry-run", "output": str(destination)}, ensure_ascii=False))
            return 0

        receipt = validate_glb(generated_glb)
        publish_atomic(generated_glb, destination)
        sidecar = generated_glb.with_suffix(".materials.json")
        if sidecar.is_file():
            publish_atomic(sidecar, destination_dir / "model.materials.json")
        print(json.dumps({"status": "published", "output": str(destination), **receipt}, ensure_ascii=False))
        return 0
    finally:
        if owned_temporary and not args.keep_work and not args.dry_run:
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
