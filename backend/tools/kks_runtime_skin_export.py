"""Convert a Koikatsu zipmod GameObject into browser-ready skinned meshes.

This is deliberately an offline conversion tool.  It reads the Unity asset
bundle embedded in a local ``.zipmod`` and emits the vertex/UV/index/skin/bind
data consumed by ``KoikatsuPmxRig``.  The converter never starts the game and
does not apply body sliders; the browser applies the card's native sliders to
the shared skeleton exactly once.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable

try:
    import UnityPy  # type: ignore
    from UnityPy.helpers.MeshHelper import MeshHandler  # type: ignore
except ImportError as exc:  # pragma: no cover - optional offline dependency
    raise SystemExit(
        "UnityPy is required for offline conversion. Install it with "
        "`py -3.13 -m pip install UnityPy`."
    ) from exc


def _matrix_values(matrix: Any) -> list[float]:
    return [float(getattr(matrix, f"e{row}{column}")) for row in range(4) for column in range(4)]


def _game_object_components(game_object: Any) -> Iterable[Any]:
    for pair in getattr(game_object, "m_Component", ()):
        component = getattr(pair, "component", None)
        if component is None:
            continue
        yield component.read()


def _walk_game_objects(root: Any) -> Iterable[Any]:
    yield root
    transform = next((component for component in _game_object_components(root) if component.__class__.__name__ == "Transform"), None)
    if transform is None:
        return
    for child_ptr in getattr(transform, "m_Children", ()):
        child_transform = child_ptr.read()
        child_game_object = child_transform.m_GameObject.read()
        yield from _walk_game_objects(child_game_object)


def _renderer_payload(renderer: Any) -> dict[str, Any] | None:
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
    bone_names = []
    for bone_ptr in getattr(renderer, "m_Bones", ()):
        try:
            bone_names.append(str(bone_ptr.read().m_GameObject.read().m_Name))
        except Exception:
            bone_names.append("")
    bind_poses = []
    for matrix in getattr(mesh, "m_BindPose", ()):
        bind_poses.extend(_matrix_values(matrix))
    vertex_count = len(vertices) // 3
    if vertex_count < 1 or len(uvs) != vertex_count * 2 or len(skin_indices) != vertex_count * 4 or len(skin_weights) != vertex_count * 4:
        return None
    return {
        "vertices": vertices,
        "uvs": uvs,
        "indices": indices,
        "boneNames": bone_names,
        "skinIndices": skin_indices,
        "skinWeights": skin_weights,
        "bindPoses": bind_poses,
    }


def _find_root(environment: Any, main_data: str) -> Any:
    bundle = next((obj.read() for obj in environment.objects if obj.type.name == "AssetBundle"), None)
    if bundle is None:
        raise ValueError("asset bundle metadata is missing")
    candidates = [(name, info.asset) for name, info in bundle.m_Container if name == main_data]
    if not candidates:
        # Some lists use the display name while the bundle uses a suffixed
        # variant. Prefer an exact prefix match before failing.
        candidates = [(name, info.asset) for name, info in bundle.m_Container if name.startswith(main_data)]
    for _, pointer in candidates:
        try:
            asset = pointer.read()
            if asset.__class__.__name__ == "GameObject":
                return asset
        except Exception:
            continue
    raise ValueError(f"GameObject {main_data!r} was not found in the bundle")


def convert_zipmod(zipmod: Path, main_ab: str, main_data: str, output: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(zipmod) as archive:
        bundle_bytes = archive.read(main_ab)
    environment = UnityPy.load(io.BytesIO(bundle_bytes))
    root = _find_root(environment, main_data)
    output.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for index, game_object in enumerate(_walk_game_objects(root)):
        for renderer in _game_object_components(game_object):
            if renderer.__class__.__name__ != "SkinnedMeshRenderer":
                continue
            payload = _renderer_payload(renderer)
            if payload is None:
                continue
            mesh_name = str(getattr(renderer.m_Mesh.read(), "m_Name", "mesh") or "mesh")
            safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", mesh_name).strip("._") or f"mesh-{index}"
            filename = f"{index:03d}-{safe_name}.json"
            (output / filename).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            items.append({"id": mesh_name, "runtimeSkin": filename, "skinned": True})
    (output / "manifest.json").write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zipmod", type=Path)
    parser.add_argument("main_ab")
    parser.add_argument("main_data")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    items = convert_zipmod(args.zipmod, args.main_ab, args.main_data, args.output)
    print(json.dumps({"count": len(items), "items": items}, ensure_ascii=False))


if __name__ == "__main__":
    main()
