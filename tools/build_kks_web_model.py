#!/usr/bin/env python3
"""Build one browser-ready KKS character with the verified KKBP pipeline.

This is deliberately a single-card command.  It never scans the character
library and never writes into the source KKBP export directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.koikatsu_teacher_registry import (  # noqa: E402
    WEB_MODEL_PROVENANCE_SCHEMA,
    _export_matches_card,
    resolve_local_card,
)

PIPELINE_ROOT = Path(__file__).resolve().parent / "kks_web_pipeline"
DEFAULT_BLENDER = Path(
    r"E:\CodexData\temp\blender-5.2.0-portable"
    r"\blender-5.2.0-windows-x64\blender.exe"
)
CARD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
STAGES = ("import", "bake", "export", "publish")


class PipelineError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_glb_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            magic, version, total_length = struct.unpack("<4sII", stream.read(12))
            chunk_length, chunk_type = struct.unpack("<II", stream.read(8))
            payload = stream.read(chunk_length)
        if magic != b"glTF" or version != 2 or total_length != path.stat().st_size:
            raise ValueError("invalid GLB header")
        if chunk_type != 0x4E4F534A:
            raise ValueError("first GLB chunk is not JSON")
        value = json.loads(payload.rstrip(b"\x00 ").decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("GLB JSON is not an object")
        return value
    except (OSError, ValueError, json.JSONDecodeError, struct.error) as exc:
        raise PipelineError(f"invalid GLB {path}: {exc}") from exc


def ts_string_array(path: Path, constant: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"export const {re.escape(constant)}\s*=\s*\[(.*?)\]\s*as const",
        text,
        re.DOTALL,
    )
    if match is None:
        raise PipelineError(f"cannot locate {constant} in {path}")
    return re.findall(r"""["']([^"']+)["']""", match.group(1))


def required_driver_bones() -> set[str]:
    feature_root = PROJECT_ROOT / "frontend" / "src" / "features" / "teachers"
    body = feature_root / "kksBodyShapeDriver.ts"
    head = feature_root / "kksHeadShapeDriver.ts"
    return set(
        ts_string_array(body, "KKS_BODY_TARGET_BONES")
        + ts_string_array(body, "KKS_BODY_ALWAYS_TARGET_BONES")
        + ts_string_array(head, "KKS_HEAD_TARGET_BONES")
    )


def validate_glb(path: Path) -> dict[str, Any]:
    gltf = read_glb_json(path)
    extensions = set(gltf.get("extensionsUsed") or [])
    missing_extensions = {
        "EXT_meshopt_compression",
        "EXT_texture_webp",
    } - extensions
    if missing_extensions:
        raise PipelineError(
            f"{path} lacks required extensions: {sorted(missing_extensions)}"
        )

    nodes = gltf.get("nodes") or []
    joint_indexes = {
        int(index)
        for skin in gltf.get("skins") or []
        for index in skin.get("joints") or []
    }
    joint_names = {
        str(nodes[index].get("name") or "")
        for index in joint_indexes
        if 0 <= index < len(nodes) and isinstance(nodes[index], dict)
    }
    if len(joint_names) < 800:
        raise PipelineError(f"{path} has only {len(joint_names)} named skin joints")

    # KKBP exports cf_n_height as the armature/scene root rather than a skin
    # joint.  The browser applies slider 0 to the model root; every other body
    # and face driver destination must remain a real named joint.
    required = required_driver_bones() - {"cf_n_height"}
    missing_bones = sorted(required - joint_names)
    if missing_bones:
        preview = ", ".join(missing_bones[:12])
        raise PipelineError(
            f"{path} is missing {len(missing_bones)} KKS driver bones: {preview}"
        )

    morph_targets = sum(
        len(primitive.get("targets") or [])
        for mesh in gltf.get("meshes") or []
        for primitive in mesh.get("primitives") or []
    )
    if morph_targets < 1:
        raise PipelineError(f"{path} contains no morph targets")
    morph_names = {
        re.sub(r"(?:[\u180e\s]+|\.\d+)+$", "", str(name)).strip()
        for mesh in gltf.get("meshes") or []
        for name in (mesh.get("extras") or {}).get("targetNames") or []
    }
    required_expression_morphs = {
        "Eye 默认",
        "Eye 笑顔",
        "Eye 闭眼",
        "Mouth Default",
        "Mouth 笑顔",
        "Eyebrow Default",
    }
    missing_expression_morphs = sorted(required_expression_morphs - morph_names)
    if missing_expression_morphs:
        raise PipelineError(
            f"{path} lacks required KKS expression morphs: "
            f"{missing_expression_morphs}"
        )
    materials = gltf.get("materials") or []
    dark_map_count = sum(
        1 for material in materials if material.get("emissiveTexture")
    )
    normal_map_count = sum(
        1 for material in materials if material.get("normalTexture")
    )
    base_map_count = sum(
        1
        for material in materials
        if (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
    )
    if base_map_count < 7 or dark_map_count < 7 or normal_map_count < 1:
        raise PipelineError(
            f"{path} lacks the KKS light/dark/normal material profile "
            f"(light={base_map_count}, dark={dark_map_count}, normal={normal_map_count})"
        )
    return {
        "size": path.stat().st_size,
        "extensions": sorted(extensions),
        "joint_count": len(joint_names),
        "required_driver_bones": len(required),
        "morph_target_count": morph_targets,
        "required_expression_morphs": len(required_expression_morphs),
        "material_profile": "kks-light-dark-toon",
        "light_map_count": base_map_count,
        "dark_map_count": dark_map_count,
        "normal_map_count": normal_map_count,
    }


def valid_blend(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 1024:
            return False
        with path.open("rb") as stream:
            prefix = stream.read(12)
        return (
            prefix.startswith(b"BLENDER")
            # Blender 5.0+ can save .blend files with Zstandard compression.
            or prefix.startswith(b"\x28\xb5\x2f\xfd")
            # Older Blender releases also support gzip-compressed .blend.
            or prefix.startswith(b"\x1f\x8b")
        )
    except OSError:
        return False


def blender_environment(profile: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "BLENDER_USER_CONFIG": str(profile / "config"),
            "BLENDER_USER_SCRIPTS": str(profile / "scripts"),
            "BLENDER_USER_EXTENSIONS": str(profile / "extensions"),
        }
    )
    return environment


def display_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def run_stage(
    stage: str,
    command: list[str],
    *,
    environment: dict[str, str],
    log_path: Path,
    state_path: Path,
    state: dict[str, Any],
) -> None:
    print(f"[{stage}] {display_command(command)}", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        startupinfo = None
        if sys.platform == "win32":
            # KKBP's import operator requires a window-manager context and
            # crashes Blender 5.2 when invoked with `-b`. Run that one stage in
            # GUI mode, but keep the window hidden for unattended conversion.
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        return_code = process.wait()
    state["stages"][stage] = {
        "status": "complete" if return_code == 0 else "failed",
        "finished_at": utc_now(),
        "return_code": return_code,
        "log": str(log_path),
    }
    atomic_json(state_path, state)
    if return_code != 0:
        raise PipelineError(f"{stage} failed with exit code {return_code}; see {log_path}")


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def contained_publish_target(card_id: str) -> Path:
    root = (PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "web-models").resolve()
    target = (root / card_id / "model.glb").resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PipelineError("publish target escaped web-models root") from exc
    return target


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Convert one complete KKBP export into a validated Meshopt/WebP GLB. "
            "This command never scans or batch-converts the card library."
        )
    )
    value.add_argument("--export-dir", required=True, type=Path, help="Complete KKBP export directory for one card")
    value.add_argument("--card-id", required=True, help="16-character lowercase hexadecimal local card id")
    value.add_argument(
        "--output",
        type=Path,
        help="Final GLB path; defaults to legacy/koikatsu-3d/resources/runtime/web-models/<card-id>/model.glb",
    )
    value.add_argument(
        "--work-dir",
        type=Path,
        help="Resumable work directory outside the source export (default: OS temp/mentorflow-kks-web/<card-id>)",
    )
    value.add_argument(
        "--blender",
        type=Path,
        default=Path(os.getenv("MENTORFLOW_BLENDER", str(DEFAULT_BLENDER))),
        help="Official Blender 5.2 executable (or set MENTORFLOW_BLENDER)",
    )
    value.add_argument(
        "--blender-profile",
        type=Path,
        default=Path(os.getenv("MENTORFLOW_BLENDER_PROFILE", r"E:\CodexData\temp\blender-kks-profile")),
        help="Profile containing enabled KKBP Importer/mmd_tools/Pillow (or set MENTORFLOW_BLENDER_PROFILE)",
    )
    value.add_argument("--texture-size", type=int, default=2048, choices=(1024, 2048, 4096))
    value.add_argument("--dry-run", action="store_true", help="Print resumable stage commands without launching Blender or writing files")
    value.add_argument("--force", action="store_true", help="Rebuild valid work-stage outputs and replace an existing valid published GLB")
    value.add_argument("--stop-after", choices=STAGES, default="publish", help="Stop cleanly after this stage")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    card_id = str(args.card_id).strip().casefold()
    if not CARD_ID_RE.fullmatch(card_id):
        raise PipelineError("card id must be exactly 16 lowercase hexadecimal characters")

    export_dir = args.export_dir.resolve()
    work_dir = (
        args.work_dir.resolve()
        if args.work_dir
        else Path(tempfile.gettempdir(), "mentorflow-kks-web", card_id).resolve()
    )
    output = args.output.resolve() if args.output else contained_publish_target(card_id)
    default_target = contained_publish_target(card_id)
    if output != default_target:
        print(f"[notice] custom output selected: {output}")
    if export_dir == work_dir or export_dir in work_dir.parents or work_dir in export_dir.parents:
        raise PipelineError("work directory must be separate from the source export directory")

    imported = work_dir / "01-import" / "web-master.blend"
    atlas_dir = work_dir / "02-atlas"
    atlas_blend = atlas_dir / "atlas-master.blend"
    draft = work_dir / "03-export" / "model.glb"
    state_path = work_dir / "pipeline-state.json"
    helpers = {
        "import": PIPELINE_ROOT / "blender_import.py",
        "bake": PIPELINE_ROOT / "blender_bake.py",
        "export": PIPELINE_ROOT / "blender_export.py",
    }
    commands = {
        "import": [
            # KKBP Importer toggles Blender's console during this operator.
            # `-b` leaves no window-manager console and crashes Blender 5.2.
            # run_stage hides this GUI process on Windows.
            str(args.blender), "-P", str(helpers["import"]), "--",
            str(export_dir), str(imported),
        ],
        "bake": [
            # KKBP's bake/export-prep operators share the same console toggle
            # requirement as import. Keep a hidden GUI context for this stage.
            str(args.blender), str(imported), "-P", str(helpers["bake"]), "--",
            str(atlas_dir), str(atlas_blend),
        ],
        "export": [
            str(args.blender), "-b", str(atlas_blend), "-P", str(helpers["export"]), "--",
            str(draft), str(args.texture_size),
        ],
    }

    if args.dry_run:
        print(f"export_dir={export_dir}")
        print(f"work_dir={work_dir}")
        print(f"output={output}")
        for stage in ("import", "bake", "export"):
            print(f"[{stage}] {display_command(commands[stage])}")
        print(f"[publish] validate {draft}; atomic copy -> {output}")
        return 0

    if not export_dir.is_dir():
        raise PipelineError(f"export directory does not exist: {export_dir}")
    required_export_files = (
        export_dir / "model.pmx",
        export_dir / "Outfit 00" / "model.pmx",
        export_dir / "KK_MaterialDataComplete.json",
        export_dir / "card_data.png",
    )
    missing_export_files = [str(path) for path in required_export_files if not path.is_file()]
    if missing_export_files:
        raise PipelineError(f"incomplete KKBP export; missing: {missing_export_files}")
    try:
        source_card = resolve_local_card(card_id).resolve()
    except Exception as exc:
        raise PipelineError(f"cannot resolve local card {card_id}: {exc}") from exc
    if not _export_matches_card(export_dir, source_card):
        raise PipelineError(
            "KKBP export identity does not match the selected local card; "
            f"refusing to publish {export_dir} as {card_id}"
        )
    source_card_sha256 = sha256_file(source_card)
    export_card_sha256 = sha256_file(export_dir / "card_data.png")
    if not args.blender.is_file():
        raise PipelineError(f"Blender executable does not exist: {args.blender}")
    if not args.blender_profile.is_dir():
        raise PipelineError(f"Blender profile does not exist: {args.blender_profile}")
    if not all(path.is_file() for path in helpers.values()):
        raise PipelineError("pipeline Blender helper scripts are missing")

    provenance_path = output.with_name("model.provenance.json")
    if output.is_file() and provenance_path.is_file() and not args.force:
        try:
            report = validate_glb(output)
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except PipelineError:
            pass
        except (OSError, json.JSONDecodeError):
            pass
        else:
            if (
                isinstance(provenance, dict)
                and provenance.get("schema") == WEB_MODEL_PROVENANCE_SCHEMA
                and provenance.get("card_id") == card_id
                and provenance.get("identity_verified") is True
                and provenance.get("source_card_sha256") == source_card_sha256
                and provenance.get("export_card_sha256") == export_card_sha256
                and provenance.get("model_sha256") == sha256_file(output)
            ):
                print(f"[skip] existing identity-verified model.glb: {output}")
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0

    state: dict[str, Any] = {
        "schema": "mentorflow.kks-web-pipeline.v1",
        "card_id": card_id,
        "source_export": str(export_dir),
        "work_dir": str(work_dir),
        "output": str(output),
        "updated_at": utc_now(),
        "stages": {},
    }
    resume_same_source = False
    if state_path.is_file():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                isinstance(previous, dict)
                and previous.get("card_id") == card_id
                and previous.get("source_export") == str(export_dir)
            ):
                state["stages"] = dict(previous.get("stages") or {})
                resume_same_source = True
        except (OSError, json.JSONDecodeError):
            pass
    work_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(state_path, state)
    environment = blender_environment(args.blender_profile.resolve())

    if args.force or not resume_same_source or not valid_blend(imported):
        run_stage(
            "import", commands["import"], environment=environment,
            log_path=work_dir / "logs" / "import.log",
            state_path=state_path, state=state,
        )
        if not valid_blend(imported):
            raise PipelineError(f"import did not produce a valid blend: {imported}")
    else:
        print(f"[skip] valid import stage: {imported}")
    if args.stop_after == "import":
        return 0

    if args.force or not resume_same_source or not valid_blend(atlas_blend):
        run_stage(
            "bake", commands["bake"], environment=environment,
            log_path=work_dir / "logs" / "bake.log",
            state_path=state_path, state=state,
        )
        if not valid_blend(atlas_blend):
            raise PipelineError(f"bake did not produce a valid blend: {atlas_blend}")
    else:
        print(f"[skip] valid bake stage: {atlas_blend}")
    if args.stop_after == "bake":
        return 0

    draft_report: dict[str, Any] | None = None
    if not args.force and resume_same_source and draft.is_file():
        try:
            draft_report = validate_glb(draft)
        except PipelineError:
            draft_report = None
    if draft_report is None:
        run_stage(
            "export", commands["export"], environment=environment,
            log_path=work_dir / "logs" / "export.log",
            state_path=state_path, state=state,
        )
        draft_report = validate_glb(draft)
    else:
        print(f"[skip] valid export stage: {draft}")
    state["stages"]["export_validation"] = {
        "status": "complete",
        "finished_at": utc_now(),
        **draft_report,
    }
    atomic_json(state_path, state)
    if args.stop_after == "export":
        print(json.dumps(draft_report, ensure_ascii=False, indent=2))
        return 0

    atomic_copy(draft, output)
    published_report = validate_glb(output)
    provenance = {
        "schema": WEB_MODEL_PROVENANCE_SCHEMA,
        "card_id": card_id,
        "identity_verified": True,
        "source_card_sha256": source_card_sha256,
        "source_card_size": source_card.stat().st_size,
        "export_card_sha256": export_card_sha256,
        "model_sha256": sha256_file(output),
        "model_size": output.stat().st_size,
        "published_at": utc_now(),
    }
    atomic_json(provenance_path, provenance)
    state["stages"]["publish"] = {
        "status": "complete",
        "finished_at": utc_now(),
        "path": str(output),
        "provenance": str(provenance_path),
        **published_report,
    }
    atomic_json(state_path, state)
    print(f"[publish] {output}")
    print(json.dumps(published_report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
