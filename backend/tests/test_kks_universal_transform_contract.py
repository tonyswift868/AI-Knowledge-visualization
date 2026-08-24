from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools" / "verify_kks_universal_transform_contract.py"
SPEC = importlib.util.spec_from_file_location("verify_kks_universal_transform_contract", TOOL_PATH)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _require_external_kks_inputs(paths: dict[str, Path]) -> None:
    """Skip a real-asset check only when one immutable input is unavailable."""

    for label, path in paths.items():
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            with path.open("rb") as stream:
                stream.read(1)
        except OSError as exc:
            pytest.skip(
                f"external KKS prerequisite unavailable: {label} ({path}; {exc.__class__.__name__})"
            )


def _matrix(x: float = 0.0) -> list[float]:
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        x, 0.0, 0.0, 1.0,
    ]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_glb(path: Path, nodes: list[dict[str, object]]) -> None:
    payload = json.dumps({"asset": {"version": "2.0"}, "nodes": nodes}).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(payload), 0x4E4F534A)
        + payload
    )


def _write_fixture(
    tmp_path: Path,
    *,
    missing_exact: str | None = None,
    degenerate_shared: str | None = None,
) -> dict[str, Path]:
    nodes: list[dict[str, object]] = []
    for scope_targets in (VERIFY.HEAD_TARGETS, VERIFY.BODY_TARGETS):
        retained = [name for name in scope_targets if name != missing_exact]
        start = len(nodes)
        for offset, name in enumerate(retained):
            nodes.append(
                {
                    "name": name,
                    "matrix": _matrix(),
                    "children": [start + offset + 1] if offset + 1 < len(retained) else [],
                }
            )
    exact_glb = tmp_path / "exact.glb"
    _write_glb(exact_glb, nodes)

    def hierarchy(scope_targets: tuple[str, ...]) -> dict[str, object]:
        matrices = []
        for name in scope_targets:
            matrices.extend(
                [0.0] * 16 if name == degenerate_shared else _matrix()
            )
        return {
            "version": 1,
            "names": list(scope_targets),
            "parentIndices": [-1] + list(range(len(scope_targets) - 1)),
            "localMatrices": matrices,
        }

    head_hierarchy = tmp_path / "head-hierarchy.json"
    body_hierarchy = tmp_path / "body-hierarchy.json"
    head_driver = tmp_path / "head-driver.json"
    body_driver = tmp_path / "body-driver.json"
    _write_json(head_hierarchy, hierarchy(VERIFY.HEAD_TARGETS))
    _write_json(body_hierarchy, hierarchy(VERIFY.BODY_TARGETS))
    _write_json(
        head_driver,
        {
            "headId": 0,
            "sliderCount": 52,
            "categories": [
                {
                    "slider": 0,
                    "source": name,
                    "position": [False, False, False],
                    "rotation": [False, False, False],
                    "scale": [False, False, False],
                }
                for name in VERIFY.HEAD_TARGETS
            ],
            "animation": {name: [] for name in VERIFY.HEAD_TARGETS},
        },
    )
    _write_json(
        body_driver,
        {
            "sliderCount": 44,
            "categories": [
                {
                    "slider": 0,
                    "source": name,
                    "position": [False, False, False],
                    "rotation": [False, False, False],
                    "scale": [False, False, False],
                }
                for name in VERIFY.BODY_SOURCE_NAMES
            ],
            "animation": {name: [] for name in VERIFY.BODY_SOURCE_NAMES},
            "corrections": [],
        },
    )
    return {
        "exact_glb": exact_glb,
        "head_hierarchy": head_hierarchy,
        "body_hierarchy": body_hierarchy,
        "head_driver": head_driver,
        "body_driver": body_driver,
    }


def _write_runtime_head_skin(
    tmp_path: Path,
    *,
    bind_pose: list[float] | None = None,
    skin_index: int = 0,
) -> Path:
    path = tmp_path / "runtime-head-skin.json"
    _write_json(
        path,
        {
            "version": 2,
            "vertices": [0.0, 0.0, 0.0],
            "normals": [0.0, 1.0, 0.0],
            "uvs": [0.0, 0.0],
            "indices": [0, 0, 0],
            "boneNames": [VERIFY.HEAD_TARGETS[0]],
            "bonePaths": [VERIFY.HEAD_TARGETS[0]],
            "boneParentIndices": [-1],
            "boneLocalMatrices": _matrix(),
            "skinBoneCount": 1,
            "skinIndices": [skin_index, 0, 0, 0],
            "skinWeights": [1.0, 0.0, 0.0, 0.0],
            "bindPoses": bind_pose or _matrix(),
            "rendererMatrix": _matrix(),
            "materials": [],
        },
    )
    return path


def _write_exact_post_shape_fixture(
    tmp_path: Path,
    *,
    mismatch: str | None = None,
    semantic_mismatch: bool = False,
    abmx_modifiers: list[dict[str, object]] | None = None,
) -> dict[str, Path]:
    head_hierarchy = json.loads((tmp_path / "head-hierarchy.json").read_text(encoding="utf-8"))
    body_hierarchy = json.loads((tmp_path / "body-hierarchy.json").read_text(encoding="utf-8"))
    head_driver = json.loads((tmp_path / "head-driver.json").read_text(encoding="utf-8"))
    body_driver = json.loads((tmp_path / "body-driver.json").read_text(encoding="utf-8"))
    head_patches = VERIFY._evaluate_head_driver_patches(head_driver, [0.5] * 52)
    body_patches = VERIFY._evaluate_body_driver_patches(body_driver, [0.5] * 44, type_bone=0)
    head_locals, head_index = VERIFY._apply_patches_to_hierarchy(
        head_hierarchy, head_patches, label="fixture head",
    )
    body_locals, body_index = VERIFY._apply_patches_to_hierarchy(
        body_hierarchy, body_patches, label="fixture body",
    )
    if abmx_modifiers is not None:
        VERIFY._apply_abmx_to_hierarchy_locals(
            body_locals,
            body_index,
            abmx_modifiers,
            label="fixture body ABMX",
        )
        VERIFY._apply_abmx_to_hierarchy_locals(
            head_locals,
            head_index,
            abmx_modifiers,
            label="fixture head ABMX",
        )
    receipt = []
    for name in (*VERIFY.HEAD_TARGETS, *VERIFY.BODY_TARGETS):
        source_matrix = (
            head_locals[head_index[name]]
            if name in head_index
            else body_locals[body_index[name]]
        )
        matrix = VERIFY._basis_convert_to_row_major(source_matrix)
        position = [matrix[index] for index in (3, 7, 11)]
        scale = [
            sum(matrix[row * 4 + column] ** 2 for row in range(3)) ** 0.5
            for column in range(3)
        ]
        if name == mismatch:
            matrix[3] += 0.25
            position[0] += 0.25
        if semantic_mismatch and name == VERIFY.HEAD_TARGETS[0]:
            position[0] += 0.5
        receipt.append({
            "boneName": name,
            "transform": matrix,
            "position": position,
            "rotation": [1.0, 0.0, 0.0, 0.0],
            "scale": scale,
            "worldTransform": matrix,
            "worldPosition": position,
            "worldRotation": [1.0, 0.0, 0.0, 0.0],
            "worldScale": [1.0, 1.0, 1.0],
        })
    bones = tmp_path / "KK_EditBoneInfo.json"
    character = tmp_path / "KK_CharacterInfoData.json"
    custom_face = tmp_path / "KK_ChaFileCustomFace.json"
    custom_body = tmp_path / "KK_ChaFileCustomBody.json"
    card_data = tmp_path / "card_data.png"
    _write_json(bones, receipt)
    _write_json(
        character,
        [{
            "Name": "fixture",
            "ShapeInfoFace": [0.5] * 52,
            "ShapeInfoBody": [0.5] * 44,
        }],
    )
    _write_json(custom_face, [{"Name": "headId", "Value": "0"}])
    _write_json(custom_body, [{"Name": "typeBone", "Value": "0"}])
    result = {
        "exact_post_shape_bones": bones,
        "exact_character_info": character,
        "exact_custom_face": custom_face,
        "exact_custom_body": custom_body,
    }
    if abmx_modifiers is not None:
        card_data.write_bytes(b"fixture")
        result["exact_card_data"] = card_data
    return result


def test_structural_success_still_fails_closed_as_numerically_incomplete(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    report = VERIFY.verify_contract(**paths)

    assert report["structural_status"] == "pass"
    assert report["status"] == "incomplete"
    assert report["numerical_equivalence"]["status"] == "unknown"
    assert "verified_KKBP_basis_and_matrix_layout" in (
        report["numerical_equivalence"]["required_evidence"]
    )


def test_missing_exact_target_is_reported_not_silently_accepted(tmp_path: Path) -> None:
    missing = "cf_J_Eye01_s_L"
    paths = _write_fixture(tmp_path, missing_exact=missing)
    report = VERIFY.verify_contract(**paths)

    assert report["status"] == "failed"
    assert missing in report["head"]["coverage"]["missing_in_exact_glb"]
    assert "node_coverage" in report["head"]["failures"]


def test_virtual_height_root_does_not_require_an_exact_node(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path, missing_exact="cf_n_height")
    report = VERIFY.verify_contract(**paths)

    assert report["structural_status"] == "pass"
    assert report["status"] == "incomplete"
    assert report["body"]["coverage"]["missing_in_exact_glb"] == []
    assert report["body"]["coverage"]["virtual_exact_contracts"] == [
        {"name": "cf_n_height", "contract": "root_scale_control"}
    ]


def test_unknown_exact_parent_alias_remains_a_topology_failure(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    glb = VERIFY._load_glb_json(paths["exact_glb"])
    nodes = glb["nodes"]
    target_index = next(
        index for index, node in enumerate(nodes)
        if node.get("name") == "cf_s_spine03"
    )
    for node in nodes:
        if target_index in node.get("children", []):
            node["children"].remove(target_index)
    mystery_index = len(nodes)
    nodes.append({"name": "Mystery Parent", "matrix": _matrix(), "children": [target_index]})
    assert mystery_index == len(nodes) - 1
    _write_glb(paths["exact_glb"], nodes)

    report = VERIFY.verify_contract(**paths)

    mismatch = next(
        item for item in report["body"]["topology"]["parent_mismatches"]
        if item["name"] == "cf_s_spine03"
    )
    assert report["status"] == "failed"
    assert mismatch["exact_parent_raw"] == "Mystery Parent"
    assert mismatch["exact_parent_canonical"] == "Mystery Parent"
    assert mismatch["alias_applied"] is False
    assert "parent_topology_mismatch" in report["body"]["failures"]


def test_degenerate_shared_scale_is_reported(tmp_path: Path) -> None:
    target = "cf_s_bust00_L"
    paths = _write_fixture(tmp_path, degenerate_shared=target)
    report = VERIFY.verify_contract(**paths)

    assert report["status"] == "failed"
    assert report["body"]["local_transform_issues"]["shared"][target] == "degenerate_scale"
    assert "invalid_local_transform" in report["body"]["failures"]


def test_runtime_head_skin_rest_contract_passes_without_claiming_equivalence(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths["runtime_head_skin"] = _write_runtime_head_skin(tmp_path)

    report = VERIFY.verify_contract(**paths)

    runtime = report["runtime_head_skin"]
    assert report["status"] == "incomplete"
    assert report["numerical_equivalence"]["status"] == "unknown"
    assert runtime["status"] == "pass"
    assert runtime["weighted_bone_count"] == 1
    assert runtime["mapped_bone_count"] == 1
    assert runtime["rest_bind_pose_max_abs_error"] == 0.0
    assert runtime["hierarchy_world_max_abs_error"] == 0.0
    assert runtime["post_driver_sample"]["trs_issues"] == []


def test_runtime_head_skin_rejects_weighted_bone_without_bind_pose(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths["runtime_head_skin"] = _write_runtime_head_skin(tmp_path, skin_index=1)

    report = VERIFY.verify_contract(**paths)

    assert report["status"] == "failed"
    assert report["runtime_head_skin"]["status"] == "fail"
    assert "weighted_bind_pose_coverage" in report["runtime_head_skin"]["failures"]


def test_runtime_head_skin_rejects_rest_bind_mismatch(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths["runtime_head_skin"] = _write_runtime_head_skin(tmp_path, bind_pose=_matrix(0.25))

    report = VERIFY.verify_contract(**paths)

    runtime = report["runtime_head_skin"]
    assert report["status"] == "failed"
    assert runtime["rest_bind_pose_max_abs_error"] == 0.25
    assert "rest_bind_pose_mismatch" in runtime["failures"]


def test_runtime_head_skin_rejects_degenerate_post_driver_scale(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    driver = json.loads(paths["head_driver"].read_text(encoding="utf-8"))
    driver["categories"][0]["scale"] = [True, False, False]
    driver["animation"][VERIFY.HEAD_TARGETS[0]] = [
        {
            "key": 0,
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [0.0, 1.0, 1.0],
        }
    ]
    _write_json(paths["head_driver"], driver)
    paths["runtime_head_skin"] = _write_runtime_head_skin(tmp_path)

    report = VERIFY.verify_contract(**paths)

    runtime = report["runtime_head_skin"]
    assert report["status"] == "failed"
    assert "post_driver_invalid_trs" in runtime["failures"]
    assert runtime["post_driver_sample"]["trs_issues"][0]["name"] == "cf_J_FaceBase"


def test_exact_post_shape_receipt_can_prove_numerical_equivalence(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths.update(_write_exact_post_shape_fixture(tmp_path))

    report = VERIFY.verify_contract(**paths)

    assert report["status"] == "passed"
    assert report["numerical_equivalence"]["status"] == "pass"
    assert report["numerical_equivalence"]["comparable"] is True
    assert report["numerical_equivalence"]["group_statistics"]["all"]["bone_count"] == 147


def test_exact_post_shape_receipt_applies_same_card_abmx_before_basis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _write_fixture(tmp_path)
    modifiers = [{
        "bone_name": "cf_s_shoulder02_L",
        "scale": [1.0, 1.05, 1.0],
        "length": 1.0,
        "position": [0.0, 0.0, 0.0],
        "rotation": [0.0, 0.0, 0.0],
    }]
    paths.update(_write_exact_post_shape_fixture(tmp_path, abmx_modifiers=modifiers))
    monkeypatch.setattr(VERIFY, "_load_card_abmx_modifiers", lambda _path: modifiers)

    report = VERIFY.verify_contract(**paths)

    equivalence = report["numerical_equivalence"]
    assert report["status"] == "passed"
    assert equivalence["status"] == "pass"
    assert equivalence["abmx"]["declared_modifier_count"] == 1
    assert equivalence["abmx"]["applied_modifier_count"] == 1
    assert equivalence["abmx"]["body"]["applied_target_bones"] == [
        "cf_s_shoulder02_L"
    ]
    assert equivalence["abmx"]["body"]["coordinate_space"] == (
        "raw_unity_local_before_KKBP_basis_conversion"
    )


def test_exact_post_shape_receipt_reports_real_mismatch_not_unknown(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths.update(_write_exact_post_shape_fixture(tmp_path, mismatch="cf_J_Eye01_s_L"))

    report = VERIFY.verify_contract(**paths)

    assert report["status"] == "failed"
    assert report["numerical_equivalence"]["status"] == "fail"
    assert report["numerical_equivalence"]["worst_bones"][0]["name"] == "cf_J_Eye01_s_L"
    assert report["numerical_equivalence"]["worst_bones"][0]["max_abs_error"] == 0.25


def test_exact_post_shape_receipt_fails_closed_when_matrix_semantics_disagree(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    paths.update(_write_exact_post_shape_fixture(tmp_path, semantic_mismatch=True))

    report = VERIFY.verify_contract(**paths)

    assert report["status"] == "incomplete"
    assert report["numerical_equivalence"]["status"] == "unknown"
    assert report["numerical_equivalence"]["comparable"] is False
    assert report["numerical_equivalence"]["reason"] == "receipt_matrix_semantics_not_confirmed"


def test_real_hayasaka_inputs_never_claim_numerical_equivalence() -> None:
    # Exact Hayasaka's selected vanilla head resolves to this immutable shared
    # runtime digest. Do not borrow a path from another card's portable pack,
    # even when the driver bytes happen to be identical.
    runtime = (
        ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "runtime-skins"
        / "_shared" / "e45d6a3f0ac9e782"
    )
    inputs = {
        "Hayasaka exact GLB": ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "web-models" / "01413f1d1ea31292" / "model.glb",
        "neutral-head hierarchy": ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "shared-base" / "neutral-head" / "hierarchy.json",
        "neutral-body hierarchy": ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "shared-base" / "neutral-body" / "hierarchy.json",
        "Hayasaka head driver": runtime / "head-shape-driver.json",
        "neutral body driver": ROOT / "legacy" / "koikatsu-3d" / "resources" / "web" / "kks-body-shape-driver.json",
    }
    _require_external_kks_inputs(inputs)
    report = VERIFY.verify_contract(
        exact_glb=inputs["Hayasaka exact GLB"],
        head_hierarchy=inputs["neutral-head hierarchy"],
        body_hierarchy=inputs["neutral-body hierarchy"],
        head_driver=inputs["Hayasaka head driver"],
        body_driver=inputs["neutral body driver"],
    )

    assert report["numerical_equivalence"]["status"] == "unknown"
    assert report["status"] in {"failed", "incomplete"}
    assert report["head"]["target_count"] == 59
    assert report["body"]["target_count"] == 88
