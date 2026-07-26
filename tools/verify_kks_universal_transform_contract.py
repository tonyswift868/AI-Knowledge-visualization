#!/usr/bin/env python3
"""Offline structural and optional numerical KKS universal-runtime contract.

Without a same-card post-shape receipt this tool intentionally leaves numerical
equivalence unknown.  With KK_EditBoneInfo, the card's 52/44 slider vectors,
headId/typeBone metadata, optional same-card ABMX data, and the verified KKBP
basis conversion, it evaluates the shared hierarchies offline and compares all
59 head plus 88 body local matrices. Unknown evidence fails closed as
``status=incomplete``/exit 2; comparable out-of-tolerance evidence is
``status=failed``/exit 1.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


RUNTIME_MATRIX_TOLERANCE = 1e-5
RUNTIME_WEIGHT_EPSILON = 1e-8
RUNTIME_SCALE_EPSILON = 1e-8


HEAD_TARGETS = (
    "cf_J_FaceBase", "cf_J_FaceUp_tz", "cf_J_NoseBridge_ty", "cf_J_FaceUp_ty",
    "cf_J_FaceLow_tz", "cf_J_FaceLow_sx", "cf_J_CheekUp2_L", "cf_J_CheekUp2_R",
    "cf_J_ChinLow", "cf_J_CheekLow_s_L", "cf_J_CheekLow_s_R", "cf_J_Chin_Base",
    "cf_J_ChinTip_Base", "cf_J_Nose_tip", "cf_J_NoseBase_rx", "cf_J_NoseBridge_rx",
    "cf_J_CheekUpBase", "cf_J_CheekUp_s_L", "cf_J_CheekUp_s_R", "cf_J_Eye_tx_L",
    "cf_J_Eye_tx_R", "cf_J_megane_rx_ear", "cf_J_Eye_rz_L", "cf_J_Eye_rz_R",
    "cf_J_Eye_tz", "cf_J_Eye01_s_L", "cf_J_Eye01_s_R", "cf_J_Eye05_s_L",
    "cf_J_Eye05_s_R", "cf_J_Eye02_s_L", "cf_J_Eye02_s_R", "cf_J_Eye03_s_L",
    "cf_J_Eye03_s_R", "cf_J_Eye04_s_L", "cf_J_Eye04_s_R", "cf_J_Eye08_s_L",
    "cf_J_Eye08_s_R", "cf_J_Eye07_s_L", "cf_J_Eye07_s_R", "cf_J_Eye06_s_L",
    "cf_J_Eye06_s_R", "cf_J_Mayu_L", "cf_J_Mayu_R", "cf_J_MayuMid_s_L",
    "cf_J_MayuMid_s_R", "cf_J_MayuTip_s_L", "cf_J_MayuTip_s_R", "cf_J_EarBase_ry_L",
    "cf_J_EarBase_ry_R", "cf_J_EarUp_L", "cf_J_EarUp_R", "cf_J_EarLow_L",
    "cf_J_EarLow_R", "cf_J_MouthBase_ty", "cf_J_Mouth_L", "cf_J_Mouth_R",
    "cf_J_MouthBase_rx", "cf_J_Mouthup", "cf_J_MouthLow",
)

BODY_TARGETS = (
    "cf_n_height", "cf_s_hand_L", "cf_s_hand_R", "cf_s_head", "cf_s_neck",
    "cf_s_spine03", "cf_s_shoulder02_L", "cf_s_shoulder02_R", "cf_s_arm01_L",
    "cf_s_arm01_R", "cf_s_arm02_L", "cf_s_arm02_R", "cf_s_arm03_L",
    "cf_s_arm03_R", "cf_s_forearm01_L", "cf_s_forearm01_R", "cf_s_forearm02_L",
    "cf_s_forearm02_R", "cf_s_wrist_L", "cf_s_wrist_R", "cf_s_spine02",
    "cf_s_spine01", "cf_s_waist01", "cf_s_waist02", "cf_s_siri_L", "cf_s_siri_R",
    "cf_s_thigh01_L", "cf_s_thigh01_R", "cf_s_thigh02_L", "cf_s_thigh02_R",
    "cf_s_thigh03_L", "cf_s_thigh03_R", "cf_s_leg01_L", "cf_s_leg01_R",
    "cf_s_leg02_L", "cf_s_leg02_R", "cf_s_leg03_L", "cf_s_leg03_R", "cf_d_kokan",
    "cf_s_bust00_L", "cf_d_bust01_L", "cf_d_bust02_L", "cf_d_bust03_L",
    "cf_s_bust01_L", "cf_s_bust02_L", "cf_s_bust03_L", "cf_hit_bust02_L",
    "cf_d_bnip01_L", "cf_s_bnip01_L", "cf_s_bnip025_L", "cf_s_bnip015_L",
    "cf_s_bnip02_L", "cf_s_bnipacc_L", "cf_s_bust00_R", "cf_d_bust01_R",
    "cf_d_bust02_R", "cf_d_bust03_R", "cf_s_bust01_R", "cf_s_bust02_R",
    "cf_s_bust03_R", "cf_hit_bust02_R", "cf_d_bnip01_R", "cf_s_bnip01_R",
    "cf_s_bnip025_R", "cf_s_bnip015_R", "cf_s_bnip02_R", "cf_s_bnipacc_R",
    "cf_hit_siri_L", "cf_hit_siri_R", "cf_hit_waist_L", "cf_hit_berry",
    "cf_hit_spine02_L", "cf_hit_shoulder_L", "cf_hit_shoulder_R", "cf_hit_arm_L",
    "cf_hit_arm_R", "cf_hit_spine01", "cf_d_sk_top", "cf_d_sk_00_00",
    "cf_d_sk_01_00", "cf_d_sk_02_00", "cf_d_sk_03_00", "cf_d_sk_04_00",
    "cf_d_sk_05_00", "cf_d_sk_06_00", "cf_d_sk_07_00",
    "cf_d_shoulder_L", "cf_d_shoulder_R",
)

BODY_SOURCE_NAMES = (
    "cf_a_height", "cf_a_height_aid", "cf_a_head", "cf_a_neck", "cf_a_spine03",
    "cf_a_shoulder", "cf_a_shoulder_L_aid03", "cf_a_shoulder_R_aid03",
    "cf_a_arm_L_aid03", "cf_a_arm_R_aid03", "cf_a_arm02", "cf_a_arm03_blend01",
    "cf_a_arm03_blend02", "cf_a_farm01", "cf_a_farm02_blend01",
    "cf_a_farm02_blend03", "cf_a_farm03", "cf_a_spine02",
    "cf_a_spine02_aid_berry", "cf_a_spine01", "cf_a_berry", "cf_a_waist01",
    "cf_a_waist02", "cf_a_siri", "cf_a_thigh01_L", "cf_a_thigh01_L_aid",
    "cf_a_thigh01_R", "cf_a_thigh01_R_aid", "cf_a_thigh02_L_blend01",
    "cf_a_thigh02_L_blend03", "cf_a_thigh02_R_blend01",
    "cf_a_thigh02_R_blend03", "cf_a_thigh03_L", "cf_a_thigh03_R",
    "cf_a_leg01_L", "cf_a_leg01_R", "cf_a_leg02_L", "cf_a_leg02_R",
    "cf_a_leg03", "cf_a_dan", "cf_a_bust_ty", "cf_a_bust00_aid03_sz",
    "cf_a_bust00_aid02_sz", "cf_a_bust_L_ry", "cf_a_bust_rx",
    "cf_a_bust01_size", "cf_a_bust_L_tx", "cf_a_bust02_size", "cf_a_bust_tz",
    "cf_a_bust03_size", "cf_a_bust01_shape1", "cf_a_bust02_shape1",
    "cf_a_bust03_shape1", "cf_a_hit_bust_shape1", "cf_a_hit_bust_shape2",
    "cf_a_bnip01", "cf_a_bnip01_size", "cf_a_d_bnip01_size",
    "cf_a_bnip02_size", "cf_a_bnip015_size", "cf_a_bnip02",
    "cf_a_bnipacc_stand", "cf_a_bnipacc_size", "cf_a_bust_R_ry",
    "cf_a_bust_R_tx", "cf_a_hit_siri_shape1", "cf_a_hit_siri_shape2",
    "cf_a_hit_siri_shape3", "cf_a_hit_siri_shape4", "cf_a_hit_siri_shape5",
    "cf_a_hit_siri_shape6", "cf_a_hit_waist_shape1", "cf_a_hit_waist_shape2",
    "cf_a_hit_waist_shape3", "cf_a_hit_spinety_shape", "cf_a_hit_waist_shape4",
    "cf_a_hit_waist_shape5", "cf_a_hit_berry_shape", "cf_a_hit_berry_shape2",
    "cf_a_hit_berry_shape3", "cf_a_hit_spine02_shape1", "cf_a_hit_spine02_shape2",
    "cf_a_hit_spine02_shape3", "cf_a_hit_shoulder_shape1",
    "cf_a_hit_shoulder_shape2", "cf_a_hit_shoulder_shape3", "cf_a_hit_arm_shape2",
    "cf_a_hit_arm_shape3", "cf_a_hit_arm_shape4", "cf_a_hit_spine01_shape1",
    "cf_a_hit_spine01_shape2", "cf_a_sk_00_00", "cf_a_sk_00_01", "cf_a_sk_berry",
    "cf_a_sk_thigh01_sz", "cf_a_sk_01_00", "cf_a_sk_01_01",
    "cf_a_sk_thigh01_sx", "cf_a_sk_02_00", "cf_a_sk_02_01", "cf_a_sk_siri",
    "cf_a_sk_03_00", "cf_a_sk_03_01", "cf_a_sk_04_00", "cf_a_sk_04_01",
    "cf_a_sk_05_00", "cf_a_sk_05_01", "cf_a_sk_06_00", "cf_a_sk_06_01",
    "cf_a_sk_07_00", "cf_a_sk_07_01",
)

# KksGltfRig applies cf_n_height as a root-scale control.  KKBP does not
# export it as a physical node, so its exact-side contract is explicitly
# virtual rather than a missing-bone exception.
VIRTUAL_EXACT_TARGETS = {
    "body": {"cf_n_height": "root_scale_control"},
}

# KKBP/Blender exports standard humanoid display names for several KKS parent
# bones.  Values are the canonical KKS hierarchy names used by the universal
# runtime.  This table is intentionally closed: an unlisted alias remains a
# topology failure and is surfaced in the report.
EXACT_PARENT_ALIASES = {
    "Pelvis": "cf_j_waist01",
    "Spine": "cf_j_spine01",
    "Chest": "cf_j_spine02",
    "Upper Chest": "cf_j_spine03",
    "Neck": "cf_j_neck",
    "Head": "cf_j_head",
    "Left shoulder": "cf_j_shoulder_L",
    "Right shoulder": "cf_j_shoulder_R",
    "Left arm": "cf_j_arm00_L",
    "Right arm": "cf_j_arm00_R",
    "Left elbow": "cf_j_forearm01_L",
    "Right elbow": "cf_j_forearm01_R",
    "Left wrist": "cf_j_hand_L",
    "Right wrist": "cf_j_hand_R",
    "Left knee": "cf_j_leg01_L",
    "Right knee": "cf_j_leg01_R",
}


class ContractInputError(ValueError):
    pass


def _load_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractInputError(f"cannot read JSON {path}: {exc}") from exc


def _load_json(path: Path) -> dict[str, Any]:
    value = _load_json_value(path)
    if not isinstance(value, dict):
        raise ContractInputError(f"JSON root must be an object: {path}")
    return value


def _load_glb_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12:
                raise ContractInputError(f"truncated GLB header: {path}")
            magic, version, declared_length = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2:
                raise ContractInputError(f"expected GLB 2.0: {path}")
            json_length_raw = stream.read(8)
            if len(json_length_raw) != 8:
                raise ContractInputError(f"missing GLB JSON chunk: {path}")
            json_length, chunk_type = struct.unpack("<II", json_length_raw)
            if chunk_type != 0x4E4F534A:
                raise ContractInputError(f"first GLB chunk is not JSON: {path}")
            payload = stream.read(json_length)
            if len(payload) != json_length:
                raise ContractInputError(f"truncated GLB JSON chunk: {path}")
            actual_length = path.stat().st_size
            if declared_length != actual_length:
                raise ContractInputError(
                    f"GLB declared length {declared_length} != file size {actual_length}: {path}"
                )
        value = json.loads(payload.decode("utf-8").rstrip("\x00 \t\r\n"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractInputError(f"cannot read GLB {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
        raise ContractInputError(f"GLB has no nodes array: {path}")
    return value


def _finite_vector(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(isinstance(item, (int, float)) and math.isfinite(item) for item in value)
    )


def _matrix_scale(matrix: list[float]) -> tuple[float, float, float]:
    # glTF and extracted Unity matrices are column-major.
    return tuple(
        math.sqrt(sum(matrix[column * 4 + row] ** 2 for row in range(3)))
        for column in range(3)
    )


def _matrix_multiply(left: list[float], right: list[float]) -> list[float]:
    """Multiply two column-major 4x4 matrices."""
    return [
        sum(left[column_index * 4 + row] * right[column * 4 + column_index] for column_index in range(4))
        for column in range(4)
        for row in range(4)
    ]


def _matrix_max_abs_delta(left: list[float], right: list[float]) -> float:
    return max(abs(float(a) - float(b)) for a, b in zip(left, right))


IDENTITY_MATRIX = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _world_matrices(
    local_matrices: list[Any],
    parent_indices: list[Any],
    *,
    label: str,
) -> list[list[float]]:
    count = len(parent_indices)
    worlds: list[list[float] | None] = [None] * count
    active: set[int] = set()

    def resolve(index: int) -> list[float]:
        cached = worlds[index]
        if cached is not None:
            return cached
        if index in active:
            raise ContractInputError(f"{label}: parent cycle includes node {index}")
        active.add(index)
        local = local_matrices[index * 16:(index + 1) * 16]
        if not _finite_vector(local, 16):
            raise ContractInputError(f"{label}: local matrix {index} is not finite 4x4")
        parent = parent_indices[index]
        if not isinstance(parent, int) or parent < -1 or parent >= count or parent == index:
            raise ContractInputError(f"{label}: invalid parent {parent!r} for node {index}")
        world = list(local) if parent < 0 else _matrix_multiply(resolve(parent), list(local))
        worlds[index] = world
        active.remove(index)
        return world

    return [resolve(index) for index in range(count)]


def _hierarchy_paths(names: list[str], parents: list[int], label: str) -> list[str]:
    paths: list[str | None] = [None] * len(names)
    active: set[int] = set()

    def resolve(index: int) -> str:
        cached = paths[index]
        if cached is not None:
            return cached
        if index in active:
            raise ContractInputError(f"{label}: parent cycle includes node {index}")
        active.add(index)
        parent = parents[index]
        if not isinstance(parent, int) or parent < -1 or parent >= len(names) or parent == index:
            raise ContractInputError(f"{label}: invalid parent {parent!r} for node {index}")
        path = names[index] if parent < 0 else f"{resolve(parent)}/{names[index]}"
        paths[index] = path
        active.remove(index)
        return path

    return [resolve(index) for index in range(len(names))]


def _runtime_array_issue(
    payload: dict[str, Any],
    key: str,
    *,
    multiple: int | None = None,
    exact_length: int | None = None,
    finite: bool = True,
) -> str | None:
    value = payload.get(key)
    if not isinstance(value, list):
        return "not_an_array"
    if exact_length is not None and len(value) != exact_length:
        return f"length_{len(value)}_expected_{exact_length}"
    if multiple is not None and (not value or len(value) % multiple):
        return f"length_{len(value)}_not_positive_multiple_of_{multiple}"
    if finite and not all(isinstance(item, (int, float)) and math.isfinite(item) for item in value):
        return "contains_non_finite_or_non_numeric_value"
    return None


def _sample_driver_curve(
    frames: Any,
    value: float = 0.5,
    *,
    source_name: str = "",
) -> dict[str, list[float]]:
    if not isinstance(frames, list) or not frames:
        return {
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
        }
    parsed: list[dict[str, list[float]]] = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ContractInputError("head driver: animation frame must be an object")
        transform: dict[str, list[float]] = {}
        for key, length in (("position", 3), ("rotation", 3), ("scale", 3)):
            vector = frame.get(key)
            if not _finite_vector(vector, length):
                raise ContractInputError(f"head driver: frame {key} must be finite vec3")
            transform[key] = [float(axis) for axis in vector]
        parsed.append(transform)
    if len(parsed) == 1:
        return parsed[0]
    if value < 0 or value > 1:
        first = parsed[0]
        second = parsed[1]
        last = parsed[-1]
        sampled = {
            "position": [
                start + (last["position"][axis] - start) * value
                for axis, start in enumerate(first["position"])
            ],
            "scale": [
                start + (last["scale"][axis] - start) * value
                for axis, start in enumerate(first["scale"])
            ],
            "rotation": [],
        }
        keep_rotation_clamped = (
            ("thigh" in source_name.lower() and "01" in source_name)
            or (source_name.lower().startswith("cf_a_bust") and source_name.lower().endswith("_size"))
        )
        for axis, start in enumerate(first["rotation"]):
            if keep_rotation_clamped:
                sampled["rotation"].append(start if value < 0 else last["rotation"][axis])
                continue
            first_step = second["rotation"][axis] - start
            span = last["rotation"][axis] - start
            if span > 0 and first_step < 0:
                span -= 360
            elif span < 0 and first_step >= 0:
                span += 360
            sampled["rotation"].append(
                start - span * abs(value)
                if value < 0
                else last["rotation"][axis] + span * abs(value - 1)
            )
        return sampled
    key = value * (len(parsed) - 1)
    low = math.floor(key)
    high = min(len(parsed) - 1, low + 1)
    amount = key - low
    sampled: dict[str, list[float]] = {}
    for field in ("position", "rotation", "scale"):
        sampled[field] = []
        for axis in range(3):
            start = parsed[low][field][axis]
            delta = parsed[high][field][axis] - start
            if field == "rotation":
                delta %= 360
                if delta > 180:
                    delta -= 360
                if delta < -180:
                    delta += 360
            sampled[field].append(start + delta * amount)
    return sampled


def _evaluate_head_driver_patches(
    driver: dict[str, Any],
    sliders: list[float],
) -> dict[str, dict[str, list[float | None]]]:
    """Evaluate the browser's 59-target head mapping."""
    if driver.get("sliderCount") != 52:
        raise ContractInputError("head driver: sliderCount must be 52 for runtime application check")
    if len(sliders) < 52 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in sliders[:52]):
        raise ContractInputError("head driver: expected 52 finite slider values")
    categories = driver.get("categories")
    animation = driver.get("animation")
    if not isinstance(categories, list) or not isinstance(animation, dict):
        raise ContractInputError("head driver: expected categories array and animation object")
    sources = {
        name: {
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
        }
        for name in HEAD_TARGETS
    }
    for category in categories:
        if not isinstance(category, dict):
            raise ContractInputError("head driver: invalid category entry")
        name = category.get("source")
        if name not in sources:
            continue
        frames = animation.get(name)
        slider = category.get("slider")
        if not isinstance(slider, int) or slider < 0 or slider >= 52:
            raise ContractInputError(f"head driver: invalid slider for {name}")
        sampled = _sample_driver_curve(frames, float(sliders[slider]), source_name=name)
        for field in ("position", "rotation", "scale"):
            mask = category.get(field)
            if not isinstance(mask, list) or len(mask) != 3 or not all(isinstance(flag, bool) for flag in mask):
                raise ContractInputError(f"head driver: {name} {field} mask must be bool[3]")
            for axis, enabled in enumerate(mask):
                if enabled:
                    sources[name][field][axis] = sampled[field][axis]

    patches = {
        name: {
            "position": [None, None, None],
            "rotation": [None, None, None],
            "scale": [None, None, None],
        }
        for name in HEAD_TARGETS
    }

    def assign(target: int, field: str, *values: float | None) -> None:
        for axis, value in enumerate(values):
            if value is not None:
                patches[HEAD_TARGETS[target]][field][axis] = value

    source = [sources[name] for name in HEAD_TARGETS]
    assign(0, "scale", source[0]["scale"][0], 1.0, 1.0)
    assign(1, "position", None, None, source[1]["position"][2])
    assign(2, "position", None, source[2]["position"][1], source[2]["position"][2])
    assign(3, "position", None, source[3]["position"][1], None)
    assign(3, "scale", *source[3]["scale"])
    assign(4, "position", None, None, source[4]["position"][2])
    assign(5, "scale", source[5]["scale"][0], 1.0, 1.0)
    for index in (6, 7):
        assign(index, "position", source[index]["position"][0], None, source[index]["position"][2])
        assign(index, "scale", source[index]["scale"][0], 1.0, 1.0)
    assign(8, "position", None, source[8]["position"][1], None)
    assign(8, "scale", *source[8]["scale"])
    for index in (9, 10):
        assign(index, "position", None, None, source[index]["position"][2])
        assign(index, "scale", 1.0, 1.0, source[index]["scale"][2])
    for index in (11, 12):
        assign(index, "position", None, source[index]["position"][1], source[index]["position"][2])
        assign(index, "scale", source[index]["scale"][0], 1.0, 1.0)
    assign(13, "position", None, None, source[13]["position"][2])
    assign(14, "position", None, source[14]["position"][1], source[14]["position"][2])
    assign(15, "position", None, None, source[15]["position"][2])
    assign(16, "position", None, source[16]["position"][1], None)
    for index in (17, 18):
        assign(index, "position", *source[index]["position"])
    for index in (19, 20):
        assign(index, "position", None, source[index]["position"][1], None)
        assign(index, "rotation", 0.0, 0.0, source[index]["rotation"][2])
    assign(21, "position", None, source[21]["position"][1], None)
    assign(21, "rotation", source[21]["rotation"][0], 0.0, 0.0)
    for index in (22, 23):
        assign(index, "position", source[index]["position"][0], None, None)
        assign(index, "scale", source[index]["scale"][0], source[index]["scale"][1], 1.0)
    assign(24, "position", None, None, source[24]["position"][2])
    for index in (25, 26):
        assign(index, "position", source[index]["position"][0], None, None)
    for index in range(27, 41):
        assign(index, "position", None, source[index]["position"][1], None)
    for index in (41, 42):
        assign(index, "position", *source[index]["position"])
        assign(index, "rotation", 0.0, 0.0, source[index]["rotation"][2])
    for index in range(43, 47):
        assign(index, "rotation", 0.0, 0.0, source[index]["rotation"][2])
    for index in (47, 48):
        assign(index, "rotation", 0.0, source[index]["rotation"][1], source[index]["rotation"][2])
        assign(index, "scale", *source[index]["scale"])
    for index in (49, 50):
        assign(index, "position", source[index]["position"][0], source[index]["position"][1], None)
        assign(index, "scale", *source[index]["scale"])
    for index in (51, 52):
        assign(index, "position", None, source[index]["position"][1], None)
    assign(53, "position", None, source[53]["position"][1], source[53]["position"][2])
    for index in (54, 55):
        assign(index, "position", source[index]["position"][0], source[index]["position"][1], None)
    for index in range(56, 59):
        assign(index, "position", None, None, source[index]["position"][2])
    return patches


def _neutral_head_driver_patches(driver: dict[str, Any]) -> dict[str, dict[str, list[float | None]]]:
    return _evaluate_head_driver_patches(driver, [0.5] * 52)


def _build_driver_sources(
    driver: dict[str, Any],
    source_names: tuple[str, ...],
    sliders: list[float],
) -> list[dict[str, list[float]]]:
    by_name = {
        name: {
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
        }
        for name in source_names
    }
    categories = driver.get("categories")
    animation = driver.get("animation")
    if not isinstance(categories, list) or not isinstance(animation, dict):
        raise ContractInputError("body driver: expected categories array and animation object")
    for category in categories:
        if not isinstance(category, dict):
            raise ContractInputError("body driver: invalid category entry")
        name = category.get("source")
        if name not in by_name:
            continue
        slider = category.get("slider")
        if not isinstance(slider, int) or slider < 0 or slider >= len(sliders):
            raise ContractInputError(f"body driver: invalid slider for {name}")
        sampled = _sample_driver_curve(
            animation.get(name),
            float(sliders[slider]),
            source_name=name,
        )
        for field in ("position", "rotation", "scale"):
            mask = category.get(field)
            if not isinstance(mask, list) or len(mask) != 3 or not all(isinstance(flag, bool) for flag in mask):
                raise ContractInputError(f"body driver: {name} {field} mask must be bool[3]")
            for axis, enabled in enumerate(mask):
                if enabled:
                    by_name[name][field][axis] = sampled[field][axis]
    return [by_name[name] for name in source_names]


def _evaluate_body_driver_patches(
    driver: dict[str, Any],
    sliders: list[float],
    *,
    type_bone: int,
) -> dict[str, dict[str, list[float | None]]]:
    """Python port of kksBodyShapeDriver.ts, including UpdateAlways."""
    if driver.get("sliderCount") != 44:
        raise ContractInputError("body driver: sliderCount must be 44")
    if len(sliders) < 44 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in sliders[:44]):
        raise ContractInputError("body driver: expected 44 finite slider values")
    if type_bone not in (0, 1):
        raise ContractInputError("character metadata: typeBone must be 0 or 1")
    source = _build_driver_sources(driver, BODY_SOURCE_NAMES, sliders)
    patches = {
        name: {
            "position": [None, None, None],
            "rotation": [None, None, None],
            "scale": [None, None, None],
        }
        for name in BODY_TARGETS
    }

    def assign(target: int, field: str, *values: float | None) -> None:
        for axis, value in enumerate(values):
            if value is not None:
                patches[BODY_TARGETS[target]][field][axis] = value

    def v(index: int, field: str) -> list[float]:
        return source[index][field]

    zero = {"position": [0.0] * 3, "rotation": [0.0] * 3, "scale": [0.0] * 3}
    corrections = driver.get("corrections")
    if type_bone == 1 and (
        not isinstance(corrections, list)
        or len(corrections) < 32
    ):
        raise ContractInputError("body driver: typeBone=1 requires correction table")

    def correction(index: int) -> dict[str, list[float]]:
        if type_bone == 0:
            return zero
        value = corrections[index]
        if not isinstance(value, dict):
            raise ContractInputError(f"body driver: invalid correction {index}")
        for field in ("position", "rotation", "scale"):
            if not _finite_vector(value.get(field), 3):
                raise ContractInputError(f"body driver: invalid correction {index} {field}")
        return value

    def cv(index: int, field: str, axis: int) -> float:
        return float(correction(index)[field][axis])

    height = v(0, "scale")
    aid = v(1, "scale")
    assign(0, "scale", *height)
    assign(1, "scale", *aid)
    assign(2, "scale", *aid)
    assign(3, "position", None, v(2, "position")[1] + cv(2, "position", 1), None)
    assign(3, "scale", *[
        v(2, "scale")[axis] * aid[axis] + cv(2, "scale", axis)
        for axis in range(3)
    ])
    assign(4, "position", None, cv(1, "position", 1), v(3, "position")[2])
    assign(4, "rotation", cv(1, "rotation", 0), 0.0, 0.0)
    assign(4, "scale",
           v(3, "scale")[0] * aid[0] + cv(1, "scale", 0),
           1.0,
           v(3, "scale")[2] * aid[2] + cv(1, "scale", 2))
    assign(5, "position", None, None, v(4, "position")[2] + cv(5, "position", 2))
    assign(5, "rotation", cv(5, "rotation", 0), 0.0, 0.0)
    assign(5, "scale",
           v(4, "scale")[0] * aid[0] + cv(5, "scale", 0),
           1.0,
           v(4, "scale")[2] * aid[2] + cv(5, "scale", 2))
    assign(6, "position",
           v(6, "position")[0] + v(5, "position")[0] + cv(14, "position", 0),
           v(5, "position")[1] + cv(14, "position", 1),
           cv(14, "position", 2))
    assign(6, "scale",
           v(5, "scale")[0],
           v(5, "scale")[1] * aid[1] + cv(14, "scale", 1),
           v(5, "scale")[2] * v(6, "scale")[2] * aid[2] + cv(14, "scale", 2))
    assign(7, "position",
           v(7, "position")[0] - v(5, "position")[0] + cv(15, "position", 0),
           v(5, "position")[1] + cv(15, "position", 1),
           cv(15, "position", 2))
    assign(7, "scale",
           v(5, "scale")[0],
           v(5, "scale")[1] * aid[1] + cv(15, "scale", 1),
           v(5, "scale")[2] * v(7, "scale")[2] * aid[2] + cv(15, "scale", 2))
    for index, source_index, correction_index in ((8, 8, 16), (9, 9, 17)):
        assign(index, "position", v(source_index, "position")[0], v(5, "position")[1] + cv(correction_index, "position", 1), None)
        assign(index, "scale",
               1 + cv(correction_index, "scale", 0),
               v(5, "scale")[1] * aid[1] + cv(correction_index, "scale", 1),
               v(5, "scale")[2] * v(source_index, "scale")[2] * aid[2] + cv(correction_index, "scale", 2))
    for index in (10, 11):
        ci = index + 8
        assign(index, "position", None, v(10, "position")[1] + cv(ci, "position", 1), v(10, "position")[2] + cv(ci, "position", 2))
        assign(index, "scale", 1.0, v(10, "scale")[1] * aid[1] + cv(ci, "scale", 1), v(10, "scale")[2] * aid[2] + cv(ci, "scale", 2))
    for index in (12, 13):
        ci = index + 8
        assign(index, "position", None, cv(ci, "position", 1), None)
        assign(index, "scale", 1.0, v(11, "scale")[1] * v(12, "scale")[1] * aid[1] + cv(ci, "scale", 1), v(11, "scale")[2] * v(12, "scale")[2] * aid[2] + cv(ci, "scale", 2))
    for index in (14, 15):
        ci = index + 8
        assign(index, "position", None, cv(ci, "position", 1), None)
        assign(index, "scale", 1.0, v(13, "scale")[1] * aid[1], v(13, "scale")[2] * aid[2])
    for index in (16, 17):
        ci = index + 8
        assign(index, "rotation", 0.0, cv(ci, "rotation", 1), cv(ci, "rotation", 2))
        assign(index, "scale", 1.0, v(14, "scale")[1] * v(15, "scale")[1] * aid[1] + cv(ci, "scale", 1), v(14, "scale")[2] * v(15, "scale")[2] * aid[2] + cv(ci, "scale", 2))
    for index in (18, 19):
        ci = index + 8
        assign(index, "scale", 1 + cv(ci, "scale", 0), v(16, "scale")[1] * aid[1] + cv(ci, "scale", 1), v(16, "scale")[2] * aid[2])
    assign(20, "position", None, None, v(17, "position")[2] + v(18, "position")[2] + cv(4, "position", 2))
    assign(20, "scale", v(17, "scale")[0] * aid[0] + cv(4, "scale", 0), 1.0, v(17, "scale")[2] * aid[2] * v(18, "scale")[2] + cv(4, "scale", 2))
    assign(21, "position", None, v(19, "position")[1] + v(20, "position")[1], v(19, "position")[2] + v(20, "position")[2] + cv(3, "position", 2))
    assign(21, "rotation", v(20, "rotation")[0], 0.0, 0.0)
    assign(21, "scale", v(19, "scale")[0] * aid[0] * v(20, "scale")[0] + cv(3, "scale", 0), v(20, "scale")[1], v(19, "scale")[2] * aid[2] * v(20, "scale")[2] + cv(3, "scale", 2))
    assign(22, "position", None, v(20, "position")[1], v(21, "position")[2] + v(20, "position")[2])
    assign(22, "rotation", v(20, "rotation")[0], 0.0, 0.0)
    assign(22, "scale", v(21, "scale")[0] * aid[0] * v(20, "scale")[0], v(20, "scale")[1], v(21, "scale")[2] * aid[2] * v(20, "scale")[2])
    assign(23, "position", None, None, v(22, "position")[2])
    assign(23, "scale", v(22, "scale")[0] * aid[0] + cv(0, "scale", 0), 1.0, v(22, "scale")[2] * aid[2])
    for index, ci in ((24, 28), (25, 29)):
        assign(index, "position", cv(ci, "position", 0), v(23, "position")[1], v(23, "position")[2] + v(22, "position")[2] * 0.3)
        assign(index, "rotation", v(23, "rotation")[0] + cv(ci, "rotation", 0), 0.0, cv(ci, "rotation", 2))
        assign(index, "scale", v(23, "scale")[0] + (v(22, "scale")[0] - 1) * 0.5 + cv(ci, "scale", 0), v(23, "scale")[1], v(23, "scale")[2] + (v(22, "scale")[2] - 1) * 0.5 + (v(21, "scale")[2] - 1) * 0.5)

    def upper_leg(target: int, primary: int, helper: int, ci: int) -> None:
        assign(target, "position", v(primary, "position")[0] + v(helper, "position")[0] + cv(ci, "position", 0), None, v(primary, "position")[2] + cv(ci, "position", 2))
        assign(target, "rotation", v(primary, "rotation")[0] + cv(ci, "rotation", 0), cv(ci, "rotation", 1), v(primary, "rotation")[2] + cv(ci, "rotation", 2))
        assign(target, "scale", v(primary, "scale")[0] * v(helper, "scale")[0] * aid[0] + cv(ci, "scale", 0), 1.0, v(primary, "scale")[2] * v(helper, "scale")[2] * aid[2] + cv(ci, "scale", 2))

    upper_leg(26, 24, 25, 6)
    upper_leg(27, 26, 27, 7)
    for target, primary, helper, ci in ((28, 28, 29, 8), (29, 30, 31, 9)):
        assign(target, "position", v(primary, "position")[0] + v(helper, "position")[0] + cv(ci, "position", 0), None, v(primary, "position")[2] + v(helper, "position")[2])
        assign(target, "scale", v(primary, "scale")[0] * v(helper, "scale")[0] * aid[0] + cv(ci, "scale", 0), 1.0, v(primary, "scale")[2] * v(helper, "scale")[2] * aid[2] + cv(ci, "scale", 2))
    for target, primary, ci in ((30, 32, 10), (31, 33, 11)):
        assign(target, "position", v(primary, "position")[0], None, v(primary, "position")[2] + cv(ci, "position", 2))
        assign(target, "rotation", v(primary, "rotation")[0], 0.0, v(primary, "rotation")[2])
        assign(target, "scale", v(primary, "scale")[0] * aid[0] + cv(ci, "scale", 0), 1.0, v(primary, "scale")[2] * aid[2])
    for target, primary, ci in ((32, 34, 12), (33, 35, 13)):
        assign(target, "position", v(primary, "position")[0], None, v(primary, "position")[2])
        assign(target, "scale", v(primary, "scale")[0] * aid[0] + cv(ci, "scale", 0), 1.0, v(primary, "scale")[2] * aid[2] + cv(ci, "scale", 2))
    for target, primary in ((34, 36), (35, 37)):
        assign(target, "position", None, v(primary, "position")[1], v(primary, "position")[2])
        assign(target, "scale", v(primary, "scale")[0] * aid[0], 1.0, v(primary, "scale")[2] * aid[2])
    assign(36, "scale", v(38, "scale")[0], 1.0, v(38, "scale")[2])
    assign(37, "scale", v(38, "scale")[0], 1.0, v(38, "scale")[2])
    assign(38, "position", None, None, v(39, "position")[2])

    def safe_divide(numerator: float, denominator: float) -> float:
        return numerator / denominator if math.isfinite(numerator) and math.isfinite(denominator) and abs(denominator) > 1e-8 else 1.0

    for right in (False, True):
        offset = 14 if right else 0
        ry = 63 if right else 43
        tx = 64 if right else 46
        mirror = -1.0 if right else 1.0
        assign(39 + offset, "position", None, v(40, "position")[1], sum(v(i, "position")[2] for i in (41, 42, 43, 40, 44)))
        assign(39 + offset, "rotation", v(44, "rotation")[0], 0.0, 0.0)
        assign(40 + offset, "position", v(tx, "position")[0], v(45, "position")[1], v(45, "position")[2])
        assign(40 + offset, "rotation", v(45, "rotation")[0], v(ry, "rotation")[1] - v(45, "rotation")[1] if right else v(ry, "rotation")[1] + v(45, "rotation")[1], 0.0)
        assign(40 + offset, "scale", *v(45, "scale"))
        assign(41 + offset, "position", None, v(47, "position")[1], v(47, "position")[2] + v(48, "position")[2])
        assign(41 + offset, "rotation", v(47, "rotation")[0], 0.0, 0.0)
        assign(41 + offset, "scale", *v(47, "scale"))
        assign(42 + offset, "position", None, v(49, "position")[1], v(49, "position")[2] + v(48, "position")[2] / 2)
        assign(42 + offset, "rotation", v(49, "rotation")[0], 0.0, 0.0)
        assign(42 + offset, "scale", *v(49, "scale"))
        assign(43 + offset, "position", v(50, "position")[0] * mirror, v(50, "position")[1], v(50, "position")[2])
        assign(43 + offset, "rotation", v(50, "rotation")[0], v(50, "rotation")[1] * mirror, 0.0)
        assign(43 + offset, "scale", v(50, "scale")[0], v(50, "scale")[1], v(48, "scale")[2])
        assign(44 + offset, "position", v(51, "position")[0] * mirror, v(51, "position")[1], v(51, "position")[2])
        assign(44 + offset, "rotation", v(51, "rotation")[0], v(51, "rotation")[1] * mirror, 0.0)
        assign(44 + offset, "scale", v(51, "scale")[0] * v(48, "scale")[0], v(51, "scale")[1] * v(48, "scale")[1], v(51, "scale")[2])
        assign(45 + offset, "position", None, v(52, "position")[1], v(52, "position")[2])
        assign(45 + offset, "rotation", v(52, "rotation")[0], 0.0, 0.0)
        assign(45 + offset, "scale", v(52, "scale")[0] * v(48, "scale")[0], v(52, "scale")[1] * v(48, "scale")[1], 1.0)
        assign(46 + offset, "position", v(53, "position")[0] * mirror, v(53, "position")[1], v(53, "position")[2] + v(54, "position")[2])
        assign(46 + offset, "scale", *(v(53, "scale")[axis] * v(54, "scale")[0] for axis in range(3)))
        assign(47 + offset, "position", None, None, v(55, "position")[2] + v(56, "position")[2])
        assign(47 + offset, "scale", v(57, "scale")[0], v(57, "scale")[0], v(57, "scale")[2])
        assign(48 + offset, "position", None, None, -(v(55, "position")[2] - 0.01) * 1.2)
        assign(48 + offset, "scale", *(v(55, "scale")[axis] * v(56, "scale")[axis] for axis in range(3)))
        assign(49 + offset, "scale", safe_divide(v(58, "scale")[0] * 1.2, v(55, "scale")[0]), safe_divide(v(58, "scale")[1] * 1.2, v(55, "scale")[1]), safe_divide(1.0, v(55, "scale")[2]))
        assign(50 + offset, "position", None, None, 0.0025 + v(59, "position")[2] - (v(55, "position")[2] - 0.01))
        assign(50 + offset, "scale", 0.1 + v(59, "scale")[1] * v(59, "scale")[0], 0.1 + v(59, "scale")[1] * v(59, "scale")[0], 0.1 + v(59, "scale")[2] * v(59, "scale")[0])
        assign(51 + offset, "position", None, None, 0.004 + v(60, "position")[2] + v(55, "position")[0] + v(58, "position")[2])
        assign(51 + offset, "scale", *(v(60, "scale")[axis] * v(58, "scale")[axis] for axis in range(3)))
        assign(52 + offset, "position", None, None, v(61, "position")[2])
        assign(52 + offset, "scale", *(safe_divide(safe_divide(v(61, "scale")[axis], v(60, "scale")[axis]), v(58, "scale")[axis]) * v(62, "scale")[axis] for axis in range(3)))

    hit_scale = [
        v(65, "scale")[axis] * v(67, "scale")[axis] * v(68, "scale")[axis] * aid[axis] * v(70, "scale")[0]
        for axis in range(3)
    ]
    siri_x = sum(v(i, "position")[0] for i in (65, 66, 67, 68, 69, 70))
    siri_y = sum(v(i, "position")[1] for i in (67, 68, 70))
    siri_z = sum(v(i, "position")[2] for i in (65, 67, 70))
    assign(67, "position", siri_x, siri_y, siri_z)
    assign(67, "scale", *hit_scale)
    assign(68, "position", -siri_x, siri_y, siri_z)
    assign(68, "scale", *hit_scale)
    assign(69, "position", None, sum(v(i, "position")[1] for i in (71, 72, 73, 74, 75, 76)), v(72, "position")[2] + v(73, "position")[2])
    assign(69, "scale", *(v(71, "scale")[0] * v(72, "scale")[0] * aid[axis] * v(73, "scale")[axis] * v(76, "scale")[axis] * v(75, "scale")[axis] for axis in range(3)))
    assign(70, "position", v(77, "position")[0], v(77, "position")[1] + v(74, "position")[1] * 2 + v(78, "position")[1] + v(79, "position")[1], v(77, "position")[2] + v(78, "position")[2] + v(79, "position")[2])
    assign(70, "scale", *(v(77, "scale")[axis] * v(78, "scale")[axis] * aid[axis] * v(79, "scale")[axis] for axis in range(3)))
    assign(71, "position", None, v(80, "position")[1] + v(81, "position")[1] + v(82, "position")[1], v(82, "position")[2])
    assign(71, "scale", *(v(81, "scale")[axis] * v(82, "scale")[axis] * aid[axis] for axis in range(3)))
    shoulder_position = [sum(v(i, "position")[axis] for i in (83, 84, 85)) for axis in range(3)]
    shoulder_scale = [v(83, "scale")[axis] * v(84, "scale")[axis] * v(85, "scale")[axis] * aid[axis] for axis in range(3)]
    assign(72, "position", *shoulder_position)
    assign(72, "scale", *shoulder_scale)
    assign(73, "position", -shoulder_position[0], shoulder_position[1], shoulder_position[2])
    assign(73, "scale", *shoulder_scale)
    arm_y = v(86, "scale")[1] * v(87, "scale")[1] * v(88, "scale")[1] * aid[0]
    arm_z = v(86, "scale")[2] * v(87, "scale")[2] * v(88, "scale")[2] * aid[0]
    assign(74, "scale", 1.0, arm_y, arm_z)
    assign(75, "scale", 1.0, arm_y, arm_z)
    assign(76, "position", None, None, v(89, "position")[2] + v(90, "position")[2])
    assign(76, "scale", *(v(89, "scale")[axis] * v(90, "scale")[axis] * aid[axis] for axis in range(3)))
    assign(77, "scale", *aid)
    r94 = v(94, "rotation")[0] - 360 if v(94, "rotation")[0] > 180 else v(94, "rotation")[0]
    r97 = v(97, "rotation")[0] - 360 if v(97, "rotation")[0] > 180 else v(97, "rotation")[0]
    skirt_specs = (
        (78, (91, 92), v(93, "position")[2], v(91, "rotation")[0] + v(92, "rotation")[0] + v(92, "rotation")[2] + r94, 91),
        (79, (95, 96), v(93, "position")[2], v(95, "rotation")[0] + v(96, "rotation")[0] + v(96, "rotation")[2] + r94 * .6 + r97 * .6, 95),
        (80, (98, 99), 0.0, v(98, "rotation")[0] + v(99, "rotation")[0] + v(99, "rotation")[2] + r97, 98),
        (81, (101, 102), v(100, "position")[2], v(101, "rotation")[0] + v(102, "rotation")[0] + v(102, "rotation")[2] + r94 * .6 + r97 * .6 + v(100, "rotation")[0], 101),
        (82, (103, 104), v(100, "position")[2], v(103, "rotation")[0] + v(104, "rotation")[0] + v(104, "rotation")[2] + r94 + v(100, "rotation")[0], 103),
        (83, (105, 106), v(100, "position")[2], v(105, "rotation")[0] + v(106, "rotation")[0] + v(106, "rotation")[2] + r94 * .6 + r97 * .6 + v(100, "rotation")[0], 105),
        (84, (107, 108), 0.0, v(107, "rotation")[0] + v(108, "rotation")[0] + v(108, "rotation")[2] + r97, 107),
        (85, (109, 110), v(93, "position")[2], v(109, "rotation")[0] + v(110, "rotation")[0] + v(110, "rotation")[2] + r94 * .6 + r97 * .6, 109),
    )
    # X/Z position formulas differ by skirt segment; keep the native expressions explicit.
    skirt_positions = (
        (v(91, "position")[0] + v(92, "position")[0], v(91, "position")[2] + v(92, "position")[2] + v(93, "position")[2]),
        (v(95, "position")[0] + v(96, "position")[0] - v(93, "position")[0] * .5, v(95, "position")[2] + v(96, "position")[2] + v(93, "position")[2]),
        (v(98, "position")[0] + v(99, "position")[0] + v(100, "position")[0] * .5 - v(93, "position")[0], v(98, "position")[2] + v(99, "position")[2]),
        (v(101, "position")[0] + v(102, "position")[0] + v(100, "position")[0], v(101, "position")[2] + v(102, "position")[2] + v(100, "position")[2]),
        (v(103, "position")[0] + v(104, "position")[0], v(103, "position")[2] + v(104, "position")[2] + v(100, "position")[2]),
        (v(105, "position")[0] + v(106, "position")[0] - v(100, "position")[0], v(105, "position")[2] + v(106, "position")[2] + v(100, "position")[2]),
        (v(107, "position")[0] + v(108, "position")[0] - v(100, "position")[0] * .5 + v(93, "position")[0], v(107, "position")[2] + v(108, "position")[2]),
        (v(109, "position")[0] + v(110, "position")[0] + v(93, "position")[0] * .5, v(109, "position")[2] + v(110, "position")[2] + v(93, "position")[2]),
    )
    for spec, (position_x, position_z) in zip(skirt_specs, skirt_positions):
        target, _, _, rotation_x, rotation_source = spec
        assign(target, "position", position_x, None, position_z)
        assign(target, "rotation", rotation_x, v(rotation_source, "rotation")[1], v(rotation_source, "rotation")[2])

    assign(86, "position", (-0.01563369 if type_bone == 0 else cv(30, "position", 0)), None, None)
    assign(87, "position", (0.01560147 if type_bone == 0 else cv(31, "position", 0)), None, None)
    return patches


def _matrix_to_trs(matrix: list[float]) -> dict[str, list[float]]:
    scale = list(_matrix_scale(matrix))
    if any(axis <= RUNTIME_SCALE_EPSILON for axis in scale):
        raise ContractInputError("cannot decompose matrix with degenerate scale")
    rotation_matrix = list(matrix)
    for column in range(3):
        for row in range(3):
            rotation_matrix[column * 4 + row] /= scale[column]
    m11, m12, m13 = rotation_matrix[0], rotation_matrix[4], rotation_matrix[8]
    m21, m22, m23 = rotation_matrix[1], rotation_matrix[5], rotation_matrix[9]
    m31, _, m33 = rotation_matrix[2], rotation_matrix[6], rotation_matrix[10]
    x = math.asin(-max(-1.0, min(1.0, m23)))
    if abs(m23) < 0.9999999:
        y = math.atan2(m13, m33)
        z = math.atan2(m21, m22)
    else:
        y = math.atan2(-m31, m11)
        z = 0.0
    return {
        "position": [float(matrix[12]), float(matrix[13]), float(matrix[14])],
        "rotation": [math.degrees(x), math.degrees(y), math.degrees(z)],
        "scale": scale,
    }


def _quaternion_multiply(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def _unity_euler_matrix(
    position: list[float],
    rotation_degrees: list[float],
    scale: list[float],
) -> list[float]:
    x, y, z = (math.radians(value) / 2 for value in rotation_degrees)
    qx = (math.sin(x), 0.0, 0.0, math.cos(x))
    qy = (0.0, math.sin(y), 0.0, math.cos(y))
    qz = (0.0, 0.0, math.sin(z), math.cos(z))
    q = _quaternion_multiply(_quaternion_multiply(qy, qx), qz)
    qxv, qyv, qzv, qw = q
    x2, y2, z2 = qxv + qxv, qyv + qyv, qzv + qzv
    xx, xy, xz = qxv * x2, qxv * y2, qxv * z2
    yy, yz, zz = qyv * y2, qyv * z2, qzv * z2
    wx, wy, wz = qw * x2, qw * y2, qw * z2
    sx, sy, sz = scale
    return [
        (1 - (yy + zz)) * sx, (xy + wz) * sx, (xz - wy) * sx, 0.0,
        (xy - wz) * sy, (1 - (xx + zz)) * sy, (yz + wx) * sy, 0.0,
        (xz + wy) * sz, (yz - wx) * sz, (1 - (xx + yy)) * sz, 0.0,
        position[0], position[1], position[2], 1.0,
    ]


def _apply_patches_to_hierarchy(
    hierarchy: dict[str, Any],
    patches: dict[str, dict[str, list[float | None]]],
    *,
    label: str,
) -> tuple[list[list[float]], dict[str, int]]:
    names = hierarchy.get("names")
    matrices = hierarchy.get("localMatrices")
    if not isinstance(names, list) or not isinstance(matrices, list) or len(matrices) != len(names) * 16:
        raise ContractInputError(f"{label}: invalid hierarchy arrays")
    counts = Counter(names)
    unique = {name: index for index, name in enumerate(names) if counts[name] == 1}
    locals_out = [
        [float(value) for value in matrices[index * 16:(index + 1) * 16]]
        for index in range(len(names))
    ]
    for name, patch in patches.items():
        index = unique.get(name)
        if index is None:
            raise ContractInputError(f"{label}: target {name} is not unique in hierarchy")
        transform = _matrix_to_trs(locals_out[index])
        for field in ("position", "rotation", "scale"):
            for axis, value in enumerate(patch[field]):
                if value is not None:
                    transform[field][axis] = float(value)
        if not all(math.isfinite(value) for field in transform.values() for value in field):
            raise ContractInputError(f"{label}: post-driver transform is non-finite for {name}")
        if any(abs(value) <= RUNTIME_SCALE_EPSILON for value in transform["scale"]):
            raise ContractInputError(f"{label}: post-driver scale is degenerate for {name}")
        locals_out[index] = _unity_euler_matrix(
            transform["position"],
            transform["rotation"],
            transform["scale"],
        )
    return locals_out, unique


def _load_card_abmx_modifiers(card_path: Path) -> list[dict[str, Any]]:
    """Reuse the production card parser for the selected-coordinate ABMX rows."""

    backend_root = str(Path(__file__).resolve().parents[1] / "backend")
    inserted_backend_path = backend_root not in sys.path
    if inserted_backend_path:
        sys.path.insert(0, backend_root)
    try:
        from app.koikatsu_card import inspect_koikatsu_sunshine_card

        inspection = inspect_koikatsu_sunshine_card(card_path.read_bytes())
    except Exception as exc:
        raise ContractInputError(
            f"exact card data: cannot decode same-card ABMX metadata: {exc}"
        ) from exc
    finally:
        if inserted_backend_path:
            sys.path.remove(backend_root)
    modifiers = inspection.get("abmx_modifiers")
    if not isinstance(modifiers, list) or not all(isinstance(item, dict) for item in modifiers):
        raise ContractInputError("exact card data: abmx_modifiers must be an object array")
    return modifiers


def _apply_abmx_to_hierarchy_locals(
    locals_out: list[list[float]],
    index_by_name: dict[str, int],
    modifiers: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    """Apply ABMX's post-shape local-transform contract in raw Unity axes."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    ignored_names: set[str] = set()
    for modifier in modifiers:
        name = modifier.get("bone_name")
        if not isinstance(name, str) or not name:
            raise ContractInputError(f"{label}: ABMX modifier has no bone_name")
        if name not in index_by_name:
            ignored_names.add(name)
            continue
        grouped.setdefault(name, []).append(modifier)

    applied_count = 0
    for name, rows in grouped.items():
        index = index_by_name[name]
        baseline = locals_out[index]
        baseline_scale = list(_matrix_scale(baseline))
        if any(axis <= RUNTIME_SCALE_EPSILON for axis in baseline_scale):
            raise ContractInputError(f"{label}: ABMX baseline scale is degenerate for {name}")

        scale_multiplier = [1.0, 1.0, 1.0]
        position_offset = [0.0, 0.0, 0.0]
        rotation_offset = [0.0, 0.0, 0.0]
        length_multiplier = 1.0
        for row in rows:
            scale = row.get("scale")
            position = row.get("position")
            rotation = row.get("rotation")
            length = row.get("length")
            if (
                not _finite_vector(scale, 3)
                or not _finite_vector(position, 3)
                or not _finite_vector(rotation, 3)
                or isinstance(length, bool)
                or not isinstance(length, (int, float))
                or not math.isfinite(length)
            ):
                raise ContractInputError(f"{label}: invalid ABMX transform for {name}")
            for axis in range(3):
                scale_multiplier[axis] *= float(scale[axis])
                position_offset[axis] += float(position[axis])
                rotation_offset[axis] += float(rotation[axis])
            length_multiplier *= float(length)
            applied_count += 1

        baseline_rotation = list(baseline)
        for column in range(3):
            for row in range(3):
                baseline_rotation[column * 4 + row] /= baseline_scale[column]
        baseline_rotation[12:15] = [0.0, 0.0, 0.0]
        delta_rotation = _unity_euler_matrix(
            [0.0, 0.0, 0.0],
            rotation_offset,
            [1.0, 1.0, 1.0],
        )
        combined_rotation = _matrix_multiply(baseline_rotation, delta_rotation)
        combined_scale = [
            baseline_scale[axis] * scale_multiplier[axis]
            for axis in range(3)
        ]
        if any(abs(axis) <= RUNTIME_SCALE_EPSILON for axis in combined_scale):
            raise ContractInputError(f"{label}: ABMX result scale is degenerate for {name}")
        for column in range(3):
            for row in range(3):
                combined_rotation[column * 4 + row] *= combined_scale[column]
        combined_rotation[12:15] = [
            float(baseline[12 + axis]) * length_multiplier + position_offset[axis]
            for axis in range(3)
        ]
        locals_out[index] = combined_rotation

    return {
        "declared_modifier_count": len(modifiers),
        "applied_modifier_count": applied_count,
        "applied_target_bones": sorted(grouped),
        "ignored_non_target_bones": sorted(ignored_names),
        "coordinate_space": "raw_unity_local_before_KKBP_basis_conversion",
        "composition": "scale_and_length_multiply_position_and_rotation_add",
    }


BASIS_C = [
    -1.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, -1.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]
BASIS_C_INVERSE = [
    -1.0, 0.0, 0.0, 0.0,
    0.0, 0.0, -1.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _basis_convert_to_row_major(matrix: list[float]) -> list[float]:
    converted = _matrix_multiply(_matrix_multiply(BASIS_C, matrix), BASIS_C_INVERSE)
    return [
        converted[column * 4 + row]
        for row in range(4)
        for column in range(4)
    ]


def _metadata_value(items: Any, name: str) -> str | None:
    if not isinstance(items, list):
        return None
    matches = [
        item.get("Value")
        for item in items
        if isinstance(item, dict) and item.get("Name") == name
    ]
    return str(matches[0]) if len(matches) == 1 else None


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"max": None, "mean": None, "p95": None}
    ordered = sorted(values)
    return {
        "max": max(values),
        "mean": sum(values) / len(values),
        "p95": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
    }


def _verify_exact_post_shape_receipt(
    *,
    receipt_path: Path,
    character_info_path: Path,
    custom_face_path: Path,
    custom_body_path: Path,
    card_data_path: Path | None,
    head_hierarchy: dict[str, Any],
    body_hierarchy: dict[str, Any],
    head_driver: dict[str, Any],
    body_driver: dict[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    receipt = _load_json_value(receipt_path)
    character_info = _load_json_value(character_info_path)
    custom_face = _load_json_value(custom_face_path)
    custom_body = _load_json_value(custom_body_path)
    abmx_modifiers = (
        _load_card_abmx_modifiers(card_data_path)
        if card_data_path is not None
        else []
    )
    if not isinstance(receipt, list) or not all(isinstance(item, dict) for item in receipt):
        return {"status": "unknown", "comparable": False, "reason": "receipt_root_is_not_object_array"}
    if not isinstance(character_info, list) or len(character_info) != 1 or not isinstance(character_info[0], dict):
        return {"status": "unknown", "comparable": False, "reason": "character_info_must_have_one_object"}
    metadata = character_info[0]
    face_sliders = metadata.get("ShapeInfoFace")
    body_sliders = metadata.get("ShapeInfoBody")
    head_id_raw = _metadata_value(custom_face, "headId")
    type_bone_raw = _metadata_value(custom_body, "typeBone")
    try:
        head_id = int(head_id_raw) if head_id_raw is not None else None
        type_bone = int(type_bone_raw) if type_bone_raw is not None else None
    except ValueError:
        return {"status": "unknown", "comparable": False, "reason": "headId_or_typeBone_is_not_integer"}
    if not _finite_vector(face_sliders, 52) or not _finite_vector(body_sliders, 44):
        return {"status": "unknown", "comparable": False, "reason": "slider_vector_length_or_finiteness_mismatch"}
    if head_id is None or type_bone is None:
        return {"status": "unknown", "comparable": False, "reason": "headId_or_typeBone_missing"}
    if head_driver.get("headId") != head_id:
        return {
            "status": "unknown",
            "comparable": False,
            "reason": "head_driver_profile_does_not_match_receipt_headId",
            "head_id": head_id,
            "driver_head_id": head_driver.get("headId"),
        }

    receipt_by_name: dict[str, dict[str, Any]] = {}
    duplicate_names: list[str] = []
    for item in receipt:
        name = item.get("boneName")
        if not isinstance(name, str) or not name:
            continue
        if name in receipt_by_name:
            duplicate_names.append(name)
        else:
            receipt_by_name[name] = item
    target_names = list(HEAD_TARGETS) + list(BODY_TARGETS)
    target_duplicates = sorted(set(duplicate_names) & set(target_names))
    missing = sorted(set(target_names) - set(receipt_by_name))
    if target_duplicates or missing:
        return {
            "status": "unknown",
            "comparable": False,
            "reason": "receipt_target_coverage_or_uniqueness_failure",
            "missing_targets": missing,
            "duplicate_targets": target_duplicates,
        }

    semantic_issues: list[dict[str, Any]] = []
    for name in target_names:
        item = receipt_by_name[name]
        matrix = item.get("transform")
        position = item.get("position")
        scale = item.get("scale")
        if not _finite_vector(matrix, 16) or not _finite_vector(position, 3) or not _finite_vector(scale, 3):
            semantic_issues.append({"name": name, "issue": "non_finite_transform_position_or_scale"})
            continue
        translation_error = max(abs(float(matrix[index]) - float(position[axis])) for axis, index in enumerate((3, 7, 11)))
        # The receipt is row-major TRS; scale is the norm of each basis column.
        matrix_scale = [
            math.sqrt(sum(float(matrix[row * 4 + column]) ** 2 for row in range(3)))
            for column in range(3)
        ]
        scale_error = max(abs(matrix_scale[axis] - float(scale[axis])) for axis in range(3))
        if translation_error > tolerance or scale_error > tolerance:
            semantic_issues.append({
                "name": name,
                "issue": "receipt_matrix_flatten_semantics_mismatch",
                "translation_error": translation_error,
                "scale_error": scale_error,
            })
    if semantic_issues:
        return {
            "status": "unknown",
            "comparable": False,
            "reason": "receipt_matrix_semantics_not_confirmed",
            "semantic_issues": semantic_issues[:20],
        }

    try:
        body_patches = _evaluate_body_driver_patches(
            body_driver,
            [float(value) for value in body_sliders],
            type_bone=type_bone,
        )
        body_locals, body_index = _apply_patches_to_hierarchy(
            body_hierarchy,
            body_patches,
            label="body post-shape",
        )
        body_worlds = _world_matrices(
            [value for matrix in body_locals for value in matrix],
            body_hierarchy["parentIndices"],
            label="body post-shape",
        )
        head_patches = _evaluate_head_driver_patches(
            head_driver,
            [float(value) for value in face_sliders],
        )
        face_base_index = head_hierarchy["names"].index("cf_J_FaceBase")
        face_base_parent = head_hierarchy["parentIndices"][face_base_index]
        head_baseline_worlds = _world_matrices(
            head_hierarchy["localMatrices"],
            head_hierarchy["parentIndices"],
            label="head baseline",
        )
        parent_with_body = (
            body_worlds[body_index["cf_s_head"]]
            if face_base_parent < 0
            else _matrix_multiply(
                body_worlds[body_index["cf_s_head"]],
                head_baseline_worlds[face_base_parent],
            )
        )
        parent_scale = _matrix_scale(parent_with_body)
        source_x = head_patches["cf_J_FaceBase"]["scale"][0]
        if source_x is None:
            raise ContractInputError("head post-shape: FaceBase X scale missing")
        correction_y = (
            float(body_driver["corrections"][2]["scale"][1])
            if type_bone == 1
            else 0.0
        )
        head_correct = 1 / (1 + correction_y) if type_bone == 1 and abs(1 + correction_y) > 1e-8 else 1.0
        corrected_world_y = parent_scale[1] * head_correct
        head_patches["cf_J_FaceBase"]["scale"] = [
            corrected_world_y / parent_scale[0] + float(source_x) - 1,
            corrected_world_y / parent_scale[1],
            corrected_world_y / parent_scale[2],
        ]
        head_locals, head_index = _apply_patches_to_hierarchy(
            head_hierarchy,
            head_patches,
            label="head post-shape",
        )
        body_abmx = _apply_abmx_to_hierarchy_locals(
            body_locals,
            body_index,
            abmx_modifiers,
            label="body post-shape ABMX",
        )
        head_abmx = _apply_abmx_to_hierarchy_locals(
            head_locals,
            head_index,
            abmx_modifiers,
            label="head post-shape ABMX",
        )
    except (ContractInputError, IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return {
            "status": "unknown",
            "comparable": False,
            "reason": f"universal_post_driver_evaluation_failed: {exc}",
        }

    per_bone: list[dict[str, Any]] = []
    for group, names, locals_out, index_by_name in (
        ("head", HEAD_TARGETS, head_locals, head_index),
        ("body", BODY_TARGETS, body_locals, body_index),
    ):
        for name in names:
            predicted = _basis_convert_to_row_major(locals_out[index_by_name[name]])
            exact = [float(value) for value in receipt_by_name[name]["transform"]]
            element_error = [abs(predicted[index] - exact[index]) for index in range(16)]
            predicted_position = [predicted[index] for index in (3, 7, 11)]
            exact_position = [exact[index] for index in (3, 7, 11)]
            predicted_scale = [
                math.sqrt(sum(predicted[row * 4 + column] ** 2 for row in range(3)))
                for column in range(3)
            ]
            exact_scale = [
                math.sqrt(sum(exact[row * 4 + column] ** 2 for row in range(3)))
                for column in range(3)
            ]
            per_bone.append({
                "group": group,
                "name": name,
                "position_axis_abs_error": [
                    abs(predicted_position[axis] - exact_position[axis])
                    for axis in range(3)
                ],
                "scale_axis_abs_error": [
                    abs(predicted_scale[axis] - exact_scale[axis])
                    for axis in range(3)
                ],
                "matrix_element_abs_error": element_error,
                "max_abs_error": max(element_error),
                "within_tolerance": max(element_error) <= tolerance,
            })
    group_statistics: dict[str, Any] = {}
    for group in ("head", "body", "all"):
        entries = per_bone if group == "all" else [entry for entry in per_bone if entry["group"] == group]
        group_statistics[group] = {
            "bone_count": len(entries),
            "within_tolerance_count": sum(1 for entry in entries if entry["within_tolerance"]),
            "matrix_max_abs_error": _distribution([entry["max_abs_error"] for entry in entries]),
            "position_axis_abs_error": [
                _distribution([entry["position_axis_abs_error"][axis] for entry in entries])
                for axis in range(3)
            ],
            "scale_axis_abs_error": [
                _distribution([entry["scale_axis_abs_error"][axis] for entry in entries])
                for axis in range(3)
            ],
        }
    worst = sorted(per_bone, key=lambda item: item["max_abs_error"], reverse=True)
    all_pass = all(item["within_tolerance"] for item in per_bone)
    return {
        "status": "pass" if all_pass else "fail",
        "comparable": True,
        "tolerance": tolerance,
        "basis": {
            "formula": "C * unity_local * inverse(C)",
            "position": "[-x,-z,y]",
            "scale": "[x,z,y]",
            "receipt_matrix_layout": "row_major_translation_indices_3_7_11",
            "shared_matrix_layout": "column_major",
        },
        "metadata": {
            "character_name": metadata.get("Name"),
            "head_id": head_id,
            "type_bone": type_bone,
            "face_slider_count": len(face_sliders),
            "body_slider_count": len(body_sliders),
        },
        "abmx": {
            "card_data_supplied": card_data_path is not None,
            "declared_modifier_count": len(abmx_modifiers),
            "applied_modifier_count": (
                body_abmx["applied_modifier_count"]
                + head_abmx["applied_modifier_count"]
            ),
            "body": body_abmx,
            "head": head_abmx,
        },
        "group_statistics": group_statistics,
        "worst_bones": worst[:20],
        "per_bone": per_bone,
    }


def _verify_runtime_head_skin(
    payload: dict[str, Any],
    hierarchy: dict[str, Any],
    driver: dict[str, Any],
    *,
    tolerance: float = RUNTIME_MATRIX_TOLERANCE,
) -> dict[str, Any]:
    failures: list[str] = []
    array_issues: dict[str, str] = {}
    vertices = payload.get("vertices")
    vertex_count = len(vertices) // 3 if isinstance(vertices, list) and len(vertices) % 3 == 0 else 0
    checks = (
        ("vertices", {"multiple": 3}),
        ("indices", {"multiple": 3}),
        ("boneLocalMatrices", {"multiple": 16}),
        ("skinIndices", {"exact_length": vertex_count * 4}),
        ("skinWeights", {"exact_length": vertex_count * 4}),
    )
    for key, kwargs in checks:
        if issue := _runtime_array_issue(payload, key, **kwargs):
            array_issues[key] = issue
    for key, stride in (("normals", 3), ("uvs", 2)):
        if key in payload and (issue := _runtime_array_issue(payload, key, exact_length=vertex_count * stride)):
            array_issues[key] = issue
    if not array_issues.get("indices") and any(
        not isinstance(index, int) or index < 0 or index >= vertex_count
        for index in payload["indices"]
    ):
        array_issues["indices"] = "contains_non_integer_or_out_of_range_vertex"
    if not array_issues.get("skinIndices") and any(
        not isinstance(index, int) for index in payload["skinIndices"]
    ):
        array_issues["skinIndices"] = "contains_non_integer_index"
    if not array_issues.get("skinWeights") and any(
        float(weight) < 0 for weight in payload["skinWeights"]
    ):
        array_issues["skinWeights"] = "contains_negative_weight"
    if not array_issues.get("skinWeights") and any(
        sum(float(weight) for weight in payload["skinWeights"][offset:offset + 4])
        <= RUNTIME_WEIGHT_EPSILON
        for offset in range(0, len(payload["skinWeights"]), 4)
    ):
        array_issues["skinWeights"] = "vertex_has_no_positive_weight"
    if issue := _runtime_array_issue(payload, "rendererMatrix", exact_length=16):
        array_issues["rendererMatrix"] = issue

    names = payload.get("boneNames")
    paths = payload.get("bonePaths")
    parents = payload.get("boneParentIndices")
    skin_bone_count = payload.get("skinBoneCount")
    bone_count = len(names) if isinstance(names, list) else 0
    if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
        array_issues["boneNames"] = "must_be_non_empty_strings"
    if not isinstance(paths, list) or len(paths) != bone_count or not all(isinstance(path, str) for path in paths):
        array_issues["bonePaths"] = f"length_must_equal_bone_count_{bone_count}"
    if not isinstance(parents, list) or len(parents) != bone_count:
        array_issues["boneParentIndices"] = f"length_must_equal_bone_count_{bone_count}"
    if len(payload.get("boneLocalMatrices", [])) != bone_count * 16:
        array_issues["boneLocalMatrices"] = f"length_must_equal_{bone_count * 16}"
    if not isinstance(skin_bone_count, int) or skin_bone_count < 1 or skin_bone_count > bone_count:
        array_issues["skinBoneCount"] = f"must_be_between_1_and_{bone_count}"
        skin_bone_count = 0
    if len(payload.get("bindPoses", [])) != skin_bone_count * 16 or not _finite_vector(payload.get("bindPoses"), skin_bone_count * 16):
        array_issues["bindPoses"] = f"must_be_finite_length_{skin_bone_count * 16}"
    if array_issues:
        failures.append("array_contract")

    weighted_indices: set[int] = set()
    invalid_weighted_slots: list[dict[str, Any]] = []
    if not array_issues.get("skinIndices") and not array_issues.get("skinWeights"):
        for slot, (bone_index, weight) in enumerate(zip(payload["skinIndices"], payload["skinWeights"])):
            if float(weight) <= RUNTIME_WEIGHT_EPSILON:
                continue
            if not isinstance(bone_index, int) or bone_index < 0 or bone_index >= skin_bone_count:
                invalid_weighted_slots.append({"slot": slot, "bone_index": bone_index, "weight": weight})
            else:
                weighted_indices.add(bone_index)
    if invalid_weighted_slots or any(
        index * 16 + 15 >= len(payload.get("bindPoses", []))
        for index in weighted_indices
    ):
        failures.append("weighted_bind_pose_coverage")

    mapping_issues: list[dict[str, Any]] = []
    hierarchy_deltas: list[float] = []
    rest_deltas: list[float] = []
    matched_indices: dict[int, int] = {}
    post_driver_issues: list[dict[str, Any]] = []
    side_pair_issues: list[dict[str, Any]] = []
    side_pair_count = 0
    mirrored_x_pair_count = 0
    try:
        if array_issues:
            raise ContractInputError("runtime head skin: array contract prevents matrix checks")
        source_worlds = _world_matrices(payload["boneLocalMatrices"], parents, label="runtime head skin")
        hierarchy_names = hierarchy.get("names")
        hierarchy_parents = hierarchy.get("parentIndices")
        hierarchy_locals = hierarchy.get("localMatrices")
        if not isinstance(hierarchy_names, list) or not isinstance(hierarchy_parents, list) or not isinstance(hierarchy_locals, list):
            raise ContractInputError("head hierarchy: missing names/parentIndices/localMatrices")
        hierarchy_worlds = _world_matrices(hierarchy_locals, hierarchy_parents, label="head hierarchy")
        hierarchy_paths = _hierarchy_paths(hierarchy_names, hierarchy_parents, "head hierarchy")
        hierarchy_counts = Counter(hierarchy_names)
        for source_index, (name, source_path) in enumerate(zip(names, paths)):
            path_candidates = [
                index for index, hierarchy_path in enumerate(hierarchy_paths)
                if hierarchy_path.endswith(source_path) or source_path.endswith(hierarchy_path)
            ]
            candidates = path_candidates or (
                [hierarchy_names.index(name)] if hierarchy_counts.get(name) == 1 else []
            )
            if len(candidates) != 1:
                mapping_issues.append({
                    "bone_index": source_index,
                    "name": name,
                    "path": source_path,
                    "candidate_count": len(candidates),
                })
                continue
            hierarchy_index = candidates[0]
            matched_indices[source_index] = hierarchy_index
            hierarchy_deltas.append(
                _matrix_max_abs_delta(source_worlds[source_index], hierarchy_worlds[hierarchy_index])
            )
        for index in sorted(weighted_indices):
            bind = payload["bindPoses"][index * 16:(index + 1) * 16]
            rest_deltas.append(
                _matrix_max_abs_delta(
                    _matrix_multiply(source_worlds[index], bind),
                    IDENTITY_MATRIX,
                )
            )

        patches = _neutral_head_driver_patches(driver)
        resolved: dict[str, dict[str, list[float]]] = {}
        baseline_positions: dict[str, list[float]] = {}
        for name in HEAD_TARGETS:
            if hierarchy_counts.get(name) != 1:
                continue
            index = hierarchy_names.index(name)
            local = hierarchy_locals[index * 16:(index + 1) * 16]
            position = [float(local[12]), float(local[13]), float(local[14])]
            baseline_positions[name] = list(position)
            scale = list(_matrix_scale(local))
            rotation = [0.0, 0.0, 0.0]
            patch = patches[name]
            for field, base in (("position", position), ("rotation", rotation), ("scale", scale)):
                for axis, value in enumerate(patch[field]):
                    if value is not None:
                        base[axis] = float(value)
            resolved[name] = {"position": position, "rotation": rotation, "scale": scale}
            if not all(math.isfinite(axis) for field in resolved[name].values() for axis in field):
                post_driver_issues.append({"name": name, "issue": "non_finite_trs"})
            if any(abs(axis) <= RUNTIME_SCALE_EPSILON for axis in scale):
                post_driver_issues.append({"name": name, "issue": "degenerate_scale", "scale": scale})
        for left_name in HEAD_TARGETS:
            right_name = _paired_name(left_name)
            if not left_name.endswith("_L") or right_name not in resolved or left_name not in resolved:
                continue
            side_pair_count += 1
            left = resolved[left_name]
            right = resolved[right_name]
            left_x, right_x = left["position"][0], right["position"][0]
            baseline_left_x = baseline_positions[left_name][0]
            baseline_right_x = baseline_positions[right_name][0]
            # Local axes are not guaranteed to have a common handedness across
            # every mirrored child. Enforce sign preservation only where the
            # exported neutral pair itself establishes opposite X signs.
            if (
                baseline_left_x * baseline_right_x < -(tolerance * tolerance)
                and left_x * right_x >= 0
            ):
                side_pair_issues.append({
                    "left": left_name,
                    "right": right_name,
                    "issue": "x_position_same_sign",
                    "values": [left_x, right_x],
                })
            if baseline_left_x * baseline_right_x < -(tolerance * tolerance):
                mirrored_x_pair_count += 1
            for axis, (left_scale, right_scale) in enumerate(zip(left["scale"], right["scale"])):
                if left_scale * right_scale <= 0:
                    side_pair_issues.append({
                        "left": left_name,
                        "right": right_name,
                        "issue": f"scale_axis_{axis}_sign_or_zero",
                        "values": [left_scale, right_scale],
                    })
    except ContractInputError as exc:
        if not array_issues:
            failures.append("matrix_or_driver_contract")
        mapping_issues.append({"error": str(exc)})

    if mapping_issues:
        failures.append("bone_mapping")
    if rest_deltas and max(rest_deltas) > tolerance:
        failures.append("rest_bind_pose_mismatch")
    if hierarchy_deltas and max(hierarchy_deltas) > tolerance:
        failures.append("hierarchy_world_mismatch")
    if post_driver_issues:
        failures.append("post_driver_invalid_trs")
    if side_pair_issues:
        failures.append("post_driver_left_right_degenerate")
    failures = list(dict.fromkeys(failures))
    return {
        "status": "pass" if not failures else "fail",
        "tolerance": tolerance,
        "weight_epsilon": RUNTIME_WEIGHT_EPSILON,
        "vertex_count": vertex_count,
        "bone_count": bone_count,
        "skin_bone_count": skin_bone_count,
        "weighted_bone_count": len(weighted_indices),
        "array_issues": array_issues,
        "invalid_weighted_slots": invalid_weighted_slots[:20],
        "mapped_bone_count": len(matched_indices),
        "mapping_issues": mapping_issues[:20],
        "rest_bind_pose_max_abs_error": max(rest_deltas) if rest_deltas else None,
        "hierarchy_world_max_abs_error": max(hierarchy_deltas) if hierarchy_deltas else None,
        "post_driver_sample": {
            "slider_value": 0.5,
            "checked_target_count": len(HEAD_TARGETS) - sum(1 for name in HEAD_TARGETS if name not in hierarchy.get("names", [])),
            "checked_left_right_pair_count": side_pair_count,
            "mirrored_x_sign_pair_count": mirrored_x_pair_count,
            "trs_issues": post_driver_issues,
            "left_right_issues": side_pair_issues,
        },
        "failures": failures,
    }


def _node_transform_issue(node: dict[str, Any]) -> str | None:
    if "matrix" in node:
        matrix = node["matrix"]
        if not _finite_vector(matrix, 16):
            return "matrix_not_finite_16"
        scale = _matrix_scale(matrix)
    else:
        translation = node.get("translation", [0.0, 0.0, 0.0])
        rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
        scale = node.get("scale", [1.0, 1.0, 1.0])
        if not _finite_vector(translation, 3):
            return "translation_not_finite_3"
        if not _finite_vector(rotation, 4):
            return "rotation_not_finite_4"
        if not _finite_vector(scale, 3):
            return "scale_not_finite_3"
    if any(abs(float(axis)) <= 1e-8 for axis in scale):
        return "degenerate_scale"
    return None


def _glb_index(glb: dict[str, Any]) -> dict[str, Any]:
    nodes = glb["nodes"]
    names = [node.get("name") if isinstance(node, dict) else None for node in nodes]
    counts = Counter(name for name in names if isinstance(name, str) and name)
    parents: list[int] = [-1] * len(nodes)
    invalid_children: list[dict[str, int]] = []
    for parent_index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for child in node.get("children", []):
            if not isinstance(child, int) or child < 0 or child >= len(nodes):
                invalid_children.append({"parent": parent_index, "child": child})
            elif parents[child] != -1:
                invalid_children.append({"parent": parent_index, "child": child})
            else:
                parents[child] = parent_index
    unique = {name: index for index, name in enumerate(names) if name and counts[name] == 1}
    parent_names = {
        name: names[parents[index]] if parents[index] >= 0 else None
        for name, index in unique.items()
    }
    transform_issues = {
        name: issue
        for name, index in unique.items()
        if (issue := _node_transform_issue(nodes[index])) is not None
    }
    return {
        "counts": counts,
        "unique": unique,
        "parent_names": parent_names,
        "transform_issues": transform_issues,
        "invalid_children": invalid_children,
    }


def _hierarchy_index(hierarchy: dict[str, Any], label: str) -> dict[str, Any]:
    names = hierarchy.get("names")
    parents = hierarchy.get("parentIndices")
    matrices = hierarchy.get("localMatrices")
    if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
        raise ContractInputError(f"{label}: names must be non-empty strings")
    if not isinstance(parents, list) or len(parents) != len(names):
        raise ContractInputError(f"{label}: parentIndices length mismatch")
    if not isinstance(matrices, list) or len(matrices) != len(names) * 16:
        raise ContractInputError(f"{label}: localMatrices length mismatch")
    counts = Counter(names)
    unique = {name: index for index, name in enumerate(names) if counts[name] == 1}
    invalid_parents: list[dict[str, int]] = []
    for index, parent in enumerate(parents):
        if not isinstance(parent, int) or parent < -1 or parent >= len(names) or parent == index:
            invalid_parents.append({"node": index, "parent": parent})
    parent_names = {
        name: names[parents[index]] if isinstance(parents[index], int) and parents[index] >= 0 else None
        for name, index in unique.items()
    }
    transform_issues: dict[str, str] = {}
    for name, index in unique.items():
        matrix = matrices[index * 16:(index + 1) * 16]
        if not _finite_vector(matrix, 16):
            transform_issues[name] = "matrix_not_finite_16"
        elif any(axis <= 1e-8 for axis in _matrix_scale(matrix)):
            transform_issues[name] = "degenerate_scale"
    return {
        "counts": counts,
        "unique": unique,
        "parent_names": parent_names,
        "transform_issues": transform_issues,
        "invalid_parents": invalid_parents,
    }


def _driver_sources(driver: dict[str, Any], label: str) -> set[str]:
    categories = driver.get("categories")
    animation = driver.get("animation")
    if not isinstance(categories, list) or not isinstance(animation, dict):
        raise ContractInputError(f"{label}: expected categories array and animation object")
    sources: set[str] = set()
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("source"), str):
            raise ContractInputError(f"{label}: invalid category entry")
        sources.add(category["source"])
    return sources


def _paired_name(name: str) -> str | None:
    if name.endswith("_L"):
        return f"{name[:-2]}_R"
    if name.endswith("_R"):
        return f"{name[:-2]}_L"
    return None


def _side_pair_issues(
    targets: Iterable[str],
    sources: dict[str, set[str]],
) -> list[dict[str, str]]:
    target_set = set(targets)
    issues: list[dict[str, str]] = []
    # Some native KKS destination tables intentionally contain a unilateral
    # helper (for example cf_hit_waist_L).  Only names whose counterpart is
    # part of the declared runtime contract are an expected L/R pair.
    expected_pairs = {
        name for name in target_set
        if (counterpart := _paired_name(name)) and counterpart in target_set
    }
    for name in sorted(expected_pairs):
        counterpart = _paired_name(name)
        assert counterpart is not None
        for source_name, available in sources.items():
            if name in available and counterpart not in available:
                issues.append(
                    {
                        "source": source_name,
                        "name": name,
                        "missing_counterpart": counterpart,
                    }
                )
    return issues


def _scope_report(
    *,
    label: str,
    targets: tuple[str, ...],
    driver: dict[str, Any],
    hierarchy: dict[str, Any],
    glb_index: dict[str, Any],
    driver_declares_destination_targets: bool,
) -> dict[str, Any]:
    hierarchy_index = _hierarchy_index(hierarchy, f"{label} hierarchy")
    driver_sources = _driver_sources(driver, f"{label} driver")
    animation = driver["animation"]
    missing_driver_animation = sorted(driver_sources - set(animation))
    target_set = set(targets)
    virtual_exact = VIRTUAL_EXACT_TARGETS.get(label, {})

    def duplicate_targets(index: dict[str, Any]) -> list[str]:
        return sorted(name for name in targets if index["counts"].get(name, 0) > 1)

    missing_driver = (
        sorted(target_set - driver_sources)
        if driver_declares_destination_targets
        else []
    )
    missing_hierarchy = sorted(target_set - set(hierarchy_index["unique"]))
    missing_glb = sorted(
        target_set - set(glb_index["unique"]) - set(virtual_exact)
    )
    parent_mismatches = []
    for name in sorted(target_set & set(hierarchy_index["unique"]) & set(glb_index["unique"])):
        shared_parent = hierarchy_index["parent_names"].get(name)
        exact_parent_raw = glb_index["parent_names"].get(name)
        exact_parent_canonical = EXACT_PARENT_ALIASES.get(exact_parent_raw, exact_parent_raw)
        virtual_parent_bridge = (
            exact_parent_raw is None
            and shared_parent in virtual_exact
            and virtual_exact[shared_parent] == "root_scale_control"
        )
        if virtual_parent_bridge:
            exact_parent_canonical = shared_parent
        if shared_parent != exact_parent_canonical:
            parent_mismatches.append(
                {
                    "name": name,
                    "shared_parent": shared_parent,
                    "exact_parent_raw": exact_parent_raw,
                    "exact_parent_canonical": exact_parent_canonical,
                    "alias_applied": exact_parent_raw in EXACT_PARENT_ALIASES,
                    "virtual_parent_bridge": virtual_parent_bridge,
                }
            )
    target_transform_issues = {
        "shared": {
            name: hierarchy_index["transform_issues"][name]
            for name in targets if name in hierarchy_index["transform_issues"]
        },
        "exact": {
            name: glb_index["transform_issues"][name]
            for name in targets if name in glb_index["transform_issues"]
        },
    }
    pair_issues = _side_pair_issues(
        targets,
        {
            "driver": driver_sources,
            "shared_hierarchy": set(hierarchy_index["unique"]),
            "exact_glb": set(glb_index["unique"]),
        },
    )
    failures = []
    if missing_driver or missing_driver_animation:
        failures.append("driver_target_coverage")
    if missing_hierarchy or missing_glb:
        failures.append("node_coverage")
    if duplicate_targets(hierarchy_index) or duplicate_targets(glb_index):
        failures.append("target_uniqueness")
    if hierarchy_index["invalid_parents"] or glb_index["invalid_children"]:
        failures.append("invalid_topology")
    if parent_mismatches:
        failures.append("parent_topology_mismatch")
    if target_transform_issues["shared"] or target_transform_issues["exact"]:
        failures.append("invalid_local_transform")
    if pair_issues:
        failures.append("left_right_pair_incomplete")

    return {
        "status": "pass" if not failures else "fail",
        "target_count": len(targets),
        "driver_source_count": len(driver_sources),
        "driver_contract_mode": (
            "categories_are_destination_targets"
            if driver_declares_destination_targets
            else "destination_targets_are_runtime_code_contract"
        ),
        "coverage": {
            "missing_in_driver": missing_driver,
            "driver_sources_missing_animation": missing_driver_animation,
            "missing_in_shared_hierarchy": missing_hierarchy,
            "missing_in_exact_glb": missing_glb,
            "virtual_exact_contracts": [
                {"name": name, "contract": contract}
                for name, contract in sorted(virtual_exact.items())
                if name in target_set and name not in glb_index["unique"]
            ],
        },
        "duplicates": {
            "shared_hierarchy": duplicate_targets(hierarchy_index),
            "exact_glb": duplicate_targets(glb_index),
        },
        "topology": {
            "parent_mismatches": parent_mismatches,
            "exact_parent_aliases": dict(sorted(EXACT_PARENT_ALIASES.items())),
            "invalid_shared_parents": hierarchy_index["invalid_parents"],
            "invalid_exact_children": glb_index["invalid_children"],
        },
        "left_right_pair_issues": pair_issues,
        "local_transform_issues": target_transform_issues,
        "failures": failures,
    }


def verify_contract(
    exact_glb: Path,
    head_hierarchy: Path,
    body_hierarchy: Path,
    head_driver: Path,
    body_driver: Path,
    runtime_head_skin: Path | None = None,
    exact_post_shape_bones: Path | None = None,
    exact_character_info: Path | None = None,
    exact_custom_face: Path | None = None,
    exact_custom_body: Path | None = None,
    exact_card_data: Path | None = None,
    exact_tolerance: float = RUNTIME_MATRIX_TOLERANCE,
) -> dict[str, Any]:
    glb = _load_glb_json(exact_glb)
    glb_index = _glb_index(glb)
    head_hierarchy_payload = _load_json(head_hierarchy)
    head_driver_payload = _load_json(head_driver)
    head = _scope_report(
        label="head",
        targets=HEAD_TARGETS,
        driver=head_driver_payload,
        hierarchy=head_hierarchy_payload,
        glb_index=glb_index,
        driver_declares_destination_targets=True,
    )
    body_hierarchy_payload = _load_json(body_hierarchy)
    body_driver_payload = _load_json(body_driver)
    body = _scope_report(
        label="body",
        targets=BODY_TARGETS,
        driver=body_driver_payload,
        hierarchy=body_hierarchy_payload,
        glb_index=glb_index,
        driver_declares_destination_targets=False,
    )
    runtime_head_skin_report = (
        _verify_runtime_head_skin(
            _load_json(runtime_head_skin),
            head_hierarchy_payload,
            head_driver_payload,
        )
        if runtime_head_skin is not None
        else {
            "status": "not_requested",
            "reason": "Pass --runtime-head-skin to validate exported runtime skinning algebra.",
        }
    )
    structural_status = (
        "pass"
        if (
            head["status"] == body["status"] == "pass"
            and runtime_head_skin_report["status"] in {"pass", "not_requested"}
        )
        else "fail"
    )
    if exact_post_shape_bones is not None:
        if exact_character_info is None:
            equivalence = {
                "status": "unknown",
                "comparable": False,
                "reason": "--exact-character-info is required with --exact-post-shape-bones",
            }
        else:
            exact_custom_face = exact_custom_face or exact_post_shape_bones.parent / "KK_ChaFileCustomFace.json"
            exact_custom_body = exact_custom_body or exact_post_shape_bones.parent / "KK_ChaFileCustomBody.json"
            equivalence = _verify_exact_post_shape_receipt(
                receipt_path=exact_post_shape_bones,
                character_info_path=exact_character_info,
                custom_face_path=exact_custom_face,
                custom_body_path=exact_custom_body,
                card_data_path=exact_card_data,
                head_hierarchy=head_hierarchy_payload,
                body_hierarchy=body_hierarchy_payload,
                head_driver=head_driver_payload,
                body_driver=body_driver_payload,
                tolerance=exact_tolerance,
            )
    else:
        equivalence = {
            "status": "unknown",
            "reason": (
                "Exact GLB is a card-authored baseline while shared hierarchies are neutral "
                "baselines. Pass an exact post-shape bone receipt plus the same card's "
                "52/44 slider metadata to test numerical equivalence."
            ),
            "required_evidence": [
                "same_card_KK_EditBoneInfo",
                "same_card_KK_CharacterInfoData",
                "same_card_headId_and_typeBone",
                "verified_KKBP_basis_and_matrix_layout",
            ],
        }
    if structural_status == "fail" or equivalence["status"] == "fail":
        status = "failed"
    elif equivalence["status"] == "pass":
        status = "passed"
    else:
        status = "incomplete"
    return {
        "schema_version": 4,
        "status": status,
        "structural_status": structural_status,
        "numerical_equivalence": equivalence,
        "inputs": {
            "exact_glb": str(exact_glb.resolve()),
            "head_hierarchy": str(head_hierarchy.resolve()),
            "body_hierarchy": str(body_hierarchy.resolve()),
            "head_driver": str(head_driver.resolve()),
            "body_driver": str(body_driver.resolve()),
            "runtime_head_skin": (
                str(runtime_head_skin.resolve()) if runtime_head_skin is not None else None
            ),
            "exact_post_shape_bones": (
                str(exact_post_shape_bones.resolve()) if exact_post_shape_bones is not None else None
            ),
            "exact_character_info": (
                str(exact_character_info.resolve()) if exact_character_info is not None else None
            ),
            "exact_card_data": (
                str(exact_card_data.resolve()) if exact_card_data is not None else None
            ),
        },
        "exact_glb": {
            "node_count": len(glb["nodes"]),
            "unnamed_node_count": sum(
                1 for node in glb["nodes"]
                if not isinstance(node, dict) or not isinstance(node.get("name"), str)
            ),
        },
        "head": head,
        "body": body,
        "runtime_head_skin": runtime_head_skin_report,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-glb", type=Path, required=True)
    parser.add_argument("--head-hierarchy", type=Path, required=True)
    parser.add_argument("--body-hierarchy", type=Path, required=True)
    parser.add_argument("--head-driver", type=Path, required=True)
    parser.add_argument("--body-driver", type=Path, required=True)
    parser.add_argument(
        "--runtime-head-skin",
        type=Path,
        help=(
            "Optional RuntimeSkin JSON. Validates array/bone coverage, rest bind "
            "algebra, hierarchy-world agreement, and a neutral head-driver sample."
        ),
    )
    parser.add_argument(
        "--exact-post-shape-bones",
        type=Path,
        help="Optional same-card KK_EditBoneInfo.json post-shape local transform receipt.",
    )
    parser.add_argument(
        "--exact-character-info",
        type=Path,
        help="Same-card KK_CharacterInfoData.json containing ShapeInfoFace/Body.",
    )
    parser.add_argument(
        "--exact-custom-face",
        type=Path,
        help="Optional same-card custom-face metadata; defaults beside the bone receipt.",
    )
    parser.add_argument(
        "--exact-custom-body",
        type=Path,
        help="Optional same-card custom-body metadata; defaults beside the bone receipt.",
    )
    parser.add_argument(
        "--exact-card-data",
        type=Path,
        help=(
            "Optional same-card native PNG used to apply selected-coordinate "
            "ABMX after ShapeBody/ShapeHead and before the KKBP basis conversion."
        ),
    )
    parser.add_argument(
        "--exact-tolerance",
        type=float,
        default=RUNTIME_MATRIX_TOLERANCE,
        help="Maximum allowed absolute local-matrix element error (default: 1e-5).",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = verify_contract(
            args.exact_glb,
            args.head_hierarchy,
            args.body_hierarchy,
            args.head_driver,
            args.body_driver,
            args.runtime_head_skin,
            args.exact_post_shape_bones,
            args.exact_character_info,
            args.exact_custom_face,
            args.exact_custom_body,
            args.exact_card_data,
            args.exact_tolerance,
        )
    except ContractInputError as exc:
        report = {
            "schema_version": 4,
            "status": "failed",
            "structural_status": "fail",
            "numerical_equivalence": {"status": "unknown"},
            "input_error": str(exc),
        }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    if report["status"] == "failed":
        return 1
    if report["status"] == "incomplete":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
