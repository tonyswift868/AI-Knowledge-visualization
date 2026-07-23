"""Export the vanilla Koikatsu shared body meshes from ``oo_base.unity3d``.

The character PNG does not contain a body mesh.  The game instantiates these
shared skinned meshes and drives them with the native ``cf_*`` skeleton.  This
small offline exporter keeps that boundary intact: it emits only the vanilla
body geometry and bind data, never starting the game or baking a character's
shape into the shared asset.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import UnityPy  # type: ignore
from UnityPy.helpers.MeshHelper import MeshHandler  # type: ignore


def matrix_values(matrix: Any) -> list[float]:
    # Unity exposes row fields; Three.js Matrix4.fromArray consumes
    # column-major values.
    return [float(getattr(matrix, f"e{row}{column}")) for column in range(4) for row in range(4)]


def export_renderer(renderer: Any) -> dict[str, Any] | None:
    mesh_ptr = getattr(renderer, "m_Mesh", None)
    if mesh_ptr is None:
        return None
    mesh = mesh_ptr.read()
    handler = MeshHandler(mesh)
    handler.process()
    vertices = [value for vertex in handler.m_Vertices for value in vertex]
    uvs = [value for uv in handler.m_UV0 for value in uv]
    indices = [int(value) for value in handler.m_IndexBuffer]
    skin_indices = [int(value) for row in handler.m_BoneIndices for value in row]
    skin_weights = [float(value) for row in handler.m_BoneWeights for value in row]
    bone_names: list[str] = []
    for bone_ptr in getattr(renderer, "m_Bones", ()):
        try:
            bone_names.append(str(bone_ptr.read().m_GameObject.read().m_Name))
        except Exception:
            bone_names.append("")
    bind_poses: list[float] = []
    for matrix in getattr(mesh, "m_BindPose", ()):
        bind_poses.extend(matrix_values(matrix))
    vertex_count = len(vertices) // 3
    if (
        vertex_count < 1
        or len(uvs) != vertex_count * 2
        or len(skin_indices) != vertex_count * 4
        or len(skin_weights) != vertex_count * 4
        or not indices
        or len(bind_poses) != len(bone_names) * 16
    ):
        return None
    return {
        "version": 1,
        "vertices": vertices,
        "uvs": uvs,
        "indices": indices,
        "boneNames": bone_names,
        "skinIndices": skin_indices,
        "skinWeights": skin_weights,
        "bindPoses": bind_poses,
        "source": "Koikatu Sunshine oo_base.unity3d:o_body_a",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    env = UnityPy.load(str(args.bundle))
    args.output.mkdir(parents=True, exist_ok=True)
    count = 0
    for obj in env.objects:
        if obj.type.name != "SkinnedMeshRenderer":
            continue
        try:
            renderer = obj.read()
            game_object = renderer.m_GameObject.read()
            if str(getattr(game_object, "m_Name", "")) != "o_body_a":
                continue
            payload = export_renderer(renderer)
            if payload is None:
                continue
            target = args.output / f"body-{count:02d}.json"
            target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            count += 1
        except Exception as exc:
            print(f"skip renderer {getattr(obj, 'path_id', '?')}: {exc}")
    manifest = {"version": 1, "items": [{"id": f"neutral-body-{i:02d}", "runtimeSkin": f"body-{i:02d}.json", "skinned": True} for i in range(count)]}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "items": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
