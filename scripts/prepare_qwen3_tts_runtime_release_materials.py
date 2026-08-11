#!/usr/bin/env python3
"""Prepare deterministic, offline-only inputs for the Qwen3-TTS pack builder.

This command does not download, install, sign, or start anything.  It combines
already audited release inputs into the exact
``mentorflow.qwen3-tts-runtime-build-spec.v1`` contract consumed by
``build_qwen3_tts_runtime_pack.py`` and emits the builder's existing trusted
Hugging Face model receipt schema.  A caller must supply an existing signing
key pair; this tool never creates release keys.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import build_qwen3_tts_runtime_pack as builder
except ModuleNotFoundError:  # Direct ``python scripts/prepare_*.py`` execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import build_qwen3_tts_runtime_pack as builder  # type: ignore[no-redef]

from backend.app.immutable_evidence import ImmutableEvidenceError, immutable_write


WHEELHOUSE_FRAGMENT_SCHEMA = "mentorflow.qwen-runtime-builder-dependency-fragment.v2"
WHEELHOUSE_VALIDATION_SCHEMA = "mentorflow.qwen-runtime-builder-dependency-validation.v2"
WHEELHOUSE_PLAN_SCHEMA = "mentorflow.qwen-runtime-wheelhouse-plan.v2"
MODEL_RECEIPT_SCHEMA = "mentorflow.trusted-huggingface-model-receipt.v1"
PROFILE = "12hz-only"
FORBIDDEN_DISTRIBUTIONS = frozenset({"onnxruntime", "sox"})
EXPECTED_REMOVED_DISTRIBUTIONS = ["flatbuffers", "onnxruntime", "protobuf", "sox"]
EXPECTED_V2_PACKAGE_COUNT = 48
WHEELHOUSE_LOCK_SCHEMA = "mentorflow.qwen-runtime-dependency-lock.v1"
WHEELHOUSE_INVENTORY_SCHEMA = "mentorflow.qwen-runtime-third-party-builder-inventory.v2"
PORTABLE_PE_SCHEMA = "mentorflow.qwen-runtime-portable-pe-dependencies.v2"
PORTABLE_PRODUCT_ENTRY_SCHEMA = "mentorflow.qwen-runtime-portable-import-smoke.v2"
LICENSE_EVIDENCE_SCHEMA = "mentorflow.qwen-runtime-license-evidence.v1"
EXPECTED_WHEEL_LICENSE_FILES = 103
EXPECTED_EXTERNAL_LICENSE_SUPPLEMENTS = 1
EXPECTED_TOTAL_LICENSE_FILES = 104
EXPECTED_RUNTIME_POLICY = {
    "product_entry": [
        "install_qwen_12hz_only_runtime(pack_root)",
        "from qwen_tts import Qwen3TTSModel",
    ],
    "numba_threading_layer": "workqueue",
    "tbb": "optional-not-shipped",
    "forbidden_distributions": ["onnxruntime", "sox"],
}
FRAGMENT_FIELDS = {
    "schema",
    "profile",
    "generation_id",
    "components",
    "dependency_lock",
    "runner_source_overlay",
    "license_evidence",
    "resolution_plan",
    "runtime_policy",
}
VALIDATION_CORE_FIELDS = {
    "schema",
    "status",
    "profile",
    "generation_id",
    "fragment",
    "dependency_lock",
    "inventory",
    "resolution_plan",
    "package_count",
    "selected_files",
    "selected_bytes",
    "license_files",
    "wheel_license_files",
    "external_license_supplement_files",
    "license_evidence",
    "excluded_test_files",
    "excluded_test_bytes",
    "runtime_required_testlike_members",
    "runtime_import_trace",
    "source_overlay",
    "site_packages",
    "removed_packages",
    "numba_threading_layer",
    "tbb",
}
VALIDATION_FINAL_FIELDS = VALIDATION_CORE_FIELDS | {
    "pre_product_entry_validation_sha256",
    "pe_dependency_audit",
    "portable_product_entry_receipt",
}
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class MaterialError(RuntimeError):
    """Stable fail-closed error for release-material preparation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return builder.sha256_file(path)


def _authenticode_signature(path: Path) -> dict[str, Any]:
    """Run the same PowerShell 7 Authenticode verifier used by portable smoke."""

    from scripts import prepare_qwen_runtime_wheelhouse as wheelhouse

    try:
        return wheelhouse._authenticode_signature(path)
    except wheelhouse.WheelhouseError as exc:
        raise MaterialError(exc.code, exc.message) from exc


def _validate_live_portable_authenticode(component: dict[str, Any], plan: builder.ComponentPlan) -> None:
    receipt = plan.receipt_record["payload"]
    rows = receipt["authenticode"]["files"]
    root = Path(str(component["root"]))
    for row in rows:
        relative = builder._safe_relative(row["path"], field="portable.authenticode.path")
        path = root.joinpath(*Path(relative).parts)
        actual = _authenticode_signature(path)
        expected = {
            "status": row["status"],
            "publisher_subject": row["publisher_subject"],
            "certificate_thumbprint": row["certificate_thumbprint"],
        }
        observed = {
            "status": actual.get("status"),
            "publisher_subject": actual.get("publisher_subject"),
            "certificate_thumbprint": actual.get("certificate_thumbprint"),
        }
        if observed != expected:
            raise MaterialError(
                "PORTABLE_PYTHON_AUTHENTICODE_INVALID",
                f"live Authenticode result differs from the pinned receipt for {relative}",
            )


def _plain_file(path: Path, *, code: str) -> Path:
    path = path.absolute()
    try:
        builder._ensure_plain_file(path, code=code)
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    return path


def _plain_directory(path: Path, *, field: str) -> Path:
    path = path.absolute()
    if not path.is_dir():
        raise MaterialError("DIRECTORY_MISSING", f"{field} is missing: {path}")
    if path.parent == path:
        raise MaterialError("FILESYSTEM_ROOT_FORBIDDEN", f"{field} cannot be a filesystem root")
    try:
        if builder._is_reparse_point(path):
            raise MaterialError("REPARSE_POINT_FORBIDDEN", f"{field} is a reparse point: {path}")
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    return path


def _read_json(path: Path, *, code: str) -> tuple[dict[str, Any], bytes]:
    path = _plain_file(path, code=code)
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialError(code, f"{path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MaterialError(code, f"{path} must contain a JSON object")
    return payload, raw


def _resolve_input(base: Path, value: Any, *, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise MaterialError("MISSING_PATH", f"{field} is required")
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    return path.absolute()


def _rewrite_declaration(value: Any, *, base: Path, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MaterialError("PINNED_INPUT_INVALID", f"{field} must be an object")
    result = copy.deepcopy(dict(value))
    path = _plain_file(
        _resolve_input(base, result.get("path"), field=f"{field}.path"),
        code="PINNED_INPUT_MISSING",
    )
    digest = str(result.get("sha256") or "").strip().casefold()
    if not builder.HEX64.fullmatch(digest):
        raise MaterialError("PINNED_INPUT_INVALID", f"{field}.sha256 must be lowercase SHA256")
    actual = _sha256_file(path)
    if actual != digest:
        raise MaterialError(
            "PINNED_INPUT_HASH_MISMATCH",
            f"{field} hash mismatch: expected {digest}, got {actual}",
        )
    if "bytes" in result and result["bytes"] != path.stat().st_size:
        raise MaterialError("PINNED_INPUT_SIZE_MISMATCH", f"{field} byte count does not match")
    result["path"] = str(path)
    return result


def _rewrite_component(value: Any, *, base: Path, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MaterialError("COMPONENT_INVALID", f"{field} must be an object")
    component = copy.deepcopy(dict(value))
    component["root"] = str(
        _plain_directory(
            _resolve_input(base, component.get("root"), field=f"{field}.root"),
            field=f"{field}.root",
        )
    )
    licenses = component.get("license_files")
    if not isinstance(licenses, list) or not licenses:
        raise MaterialError("LICENSE_EVIDENCE_MISSING", f"{field}.license_files is required")
    component["license_files"] = [
        _rewrite_declaration(item, base=base, field=f"{field}.license_files[{index}]")
        for index, item in enumerate(licenses)
    ]
    for name in (
        "receipt",
        "wheel",
        "license_supplement",
        "runtime_import_trace",
        "source_overlay",
        "material_inventory",
    ):
        if name in component:
            component[name] = _rewrite_declaration(
                component[name],
                base=base,
                field=f"{field}.{name}",
            )
    return component


def _walk_plain_files(root: Path) -> dict[str, Path]:
    root = _plain_directory(root, field="component root")
    files: dict[str, Path] = {}
    windows_paths: dict[str, str] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise MaterialError("SOURCE_ENUMERATION_FAILED", f"cannot enumerate {directory}: {exc}") from exc
        for child in children:
            try:
                if builder._is_reparse_point(child):
                    raise MaterialError("REPARSE_POINT_FORBIDDEN", f"reparse point is forbidden: {child}")
            except builder.BuildError as exc:
                raise MaterialError(exc.code, str(exc)) from exc
            if child.is_dir():
                pending.append(child)
                continue
            if not child.is_file():
                raise MaterialError("SOURCE_ENTRY_INVALID", f"non-file entry is forbidden: {child}")
            try:
                if builder._is_hardlink(child):
                    raise MaterialError("HARDLINK_FORBIDDEN", f"hardlink is forbidden: {child}")
                relative = builder._safe_relative(
                    child.relative_to(root).as_posix(),
                    field="component.file",
                )
            except builder.BuildError as exc:
                raise MaterialError(exc.code, str(exc)) from exc
            folded = builder.windows_casefold_key(relative)
            previous = windows_paths.get(folded)
            if previous is not None and previous != relative:
                raise MaterialError(
                    "WINDOWS_PATH_COLLISION",
                    f"case-insensitive path collision: {previous!r}, {relative!r}",
                )
            windows_paths[folded] = relative
            files[relative] = child
    return files


def _validate_exact_roots(plans: Iterable[builder.ComponentPlan], *, kinds: set[str]) -> None:
    grouped: dict[Path, list[builder.ComponentPlan]] = defaultdict(list)
    for plan in plans:
        if str(plan.component.get("kind")) in kinds:
            grouped[Path(str(plan.component["root"])).absolute()].append(plan)
    for root, root_plans in grouped.items():
        actual = set(_walk_plain_files(root))
        selected = {
            record.source_relative
            for plan in root_plans
            for record in plan.files
        }
        if actual != selected:
            raise MaterialError(
                "SOURCE_FILE_SET_MISMATCH",
                f"source root has undeclared or missing files: {root}; "
                f"extra={sorted(actual - selected)[:20]}, missing={sorted(selected - actual)[:20]}",
            )


def _strict_json_pin(
    value: Any,
    *,
    base: Path,
    field: str,
    schema: str,
    extra_fields: set[str] | None = None,
) -> tuple[dict[str, Any], Path, bytes]:
    expected_fields = {"path", "bytes", "sha256", "schema"} | set(extra_fields or ())
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise MaterialError(
            "WHEELHOUSE_VALIDATION_INVALID",
            f"{field} must have exact fields {sorted(expected_fields)}",
        )
    path = _plain_file(
        _resolve_input(base, value.get("path"), field=f"{field}.path"),
        code="WHEELHOUSE_VALIDATION_INVALID",
    )
    raw = path.read_bytes()
    digest = _sha256_bytes(raw)
    if (
        value.get("bytes") != len(raw)
        or value.get("sha256") != digest
        or value.get("schema") != schema
    ):
        raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", f"{field} pin does not match its exact file")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", f"{field} is not UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", f"{field} schema does not match its pin")
    return payload, path, raw


def _site_tree_stats(root: Path) -> dict[str, Any]:
    files = _walk_plain_files(root)
    digest = hashlib.sha256()
    total_bytes = 0
    for relative, path in sorted(files.items()):
        size = path.stat().st_size
        file_sha = _sha256_file(path)
        total_bytes += size
        digest.update(f"{relative}\0{size}\0{file_sha}\n".encode("utf-8"))
    return {
        "path": str(root),
        "files": len(files),
        "bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }


def _license_evidence_from_plans(
    component_plans: list[builder.ComponentPlan],
    *,
    site_root: Path,
) -> dict[str, Any]:
    wheel_files: list[dict[str, Any]] = []
    external_supplements: list[dict[str, Any]] = []
    for component_plan in component_plans:
        component_id = str(component_plan.component["id"])
        supplement = component_plan.license_supplement_record
        if supplement is None:
            for record in component_plan.license_records:
                try:
                    relative = Path(record["source"]).absolute().relative_to(site_root).as_posix()
                except ValueError as exc:
                    raise MaterialError(
                        "WHEELHOUSE_LICENSE_EVIDENCE_INVALID",
                        f"in-wheel license escaped site-packages: {record['source']}",
                    ) from exc
                wheel_files.append(
                    {
                        "component_id": component_id,
                        "path": relative,
                        "sha256": str(record["sha256"]),
                    }
                )
        else:
            payload = supplement.get("payload")
            license_file = payload.get("license_file") if isinstance(payload, dict) else None
            if (
                len(component_plan.license_records) != 1
                or not isinstance(license_file, dict)
                or not str(license_file.get("saved_relative_path") or "")
            ):
                raise MaterialError(
                    "WHEELHOUSE_LICENSE_EVIDENCE_INVALID",
                    f"external license supplement is incomplete for {component_id}",
                )
            external_supplements.append(
                {
                    "component_id": component_id,
                    "path": str(license_file["saved_relative_path"]),
                    "sha256": str(component_plan.license_records[0]["sha256"]),
                    "receipt_sha256": str(supplement["sha256"]),
                }
            )
    wheel_files.sort(key=lambda item: (item["component_id"], item["path"], item["sha256"]))
    external_supplements.sort(
        key=lambda item: (item["component_id"], item["path"], item["sha256"])
    )
    total_hashes = sorted(
        [item["sha256"] for item in wheel_files]
        + [item["sha256"] for item in external_supplements]
    )
    return {
        "schema": LICENSE_EVIDENCE_SCHEMA,
        "wheel_file_count": len(wheel_files),
        "external_supplement_file_count": len(external_supplements),
        "total_file_count": len(total_hashes),
        "wheel_files": wheel_files,
        "external_supplements": external_supplements,
        "total_sha256": total_hashes,
    }


def _require_exact_license_evidence(
    declared: Any,
    actual: Mapping[str, Any],
    *,
    component_license_file_count: int,
) -> None:
    if (
        actual.get("wheel_file_count") != EXPECTED_WHEEL_LICENSE_FILES
        or actual.get("external_supplement_file_count")
        != EXPECTED_EXTERNAL_LICENSE_SUPPLEMENTS
        or actual.get("total_file_count") != EXPECTED_TOTAL_LICENSE_FILES
        or component_license_file_count != EXPECTED_TOTAL_LICENSE_FILES
        or declared != dict(actual)
    ):
        raise MaterialError(
            "WHEELHOUSE_LICENSE_EVIDENCE_INVALID",
            "v2 license evidence must be exactly 103 in-wheel plus 1 external supplement",
        )


def _validate_portable_product_entry(
    receipt: dict[str, Any],
    *,
    validation_path: Path,
    validation_core: Mapping[str, Any],
    core_sha256: str,
    pins: Mapping[str, Mapping[str, Any]],
    selected_files: int,
    selected_bytes: int,
    excluded_test_files: int,
    excluded_test_bytes: int,
    package_count: int,
    generation_id: str,
    component_plans: Iterable[builder.ComponentPlan],
) -> None:
    expected_top = {
        "schema",
        "status",
        "profile",
        "generation_id",
        "scope",
        "not_proven",
        "safety",
        "timing",
        "inputs",
        "staging",
        "probe",
        "native_host_injection",
        "pe_dependency_audit",
    }
    if (
        set(receipt) != expected_top
        or receipt.get("schema") != PORTABLE_PRODUCT_ENTRY_SCHEMA
        or receipt.get("status") != "passed"
        or receipt.get("profile") != PROFILE
        or receipt.get("generation_id") != generation_id
    ):
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable product-entry receipt is skeletal or has invalid exact fields")
    inputs = receipt.get("inputs")
    expected_input_fields = {
        "python_source_receipt",
        "python_component_fragment",
        "dependency_lock",
        "builder_validation",
        "builder_fragment",
        "builder_inventory",
        "resolution_plan",
        "runner",
        "source_overlay",
        "model_identity_configs",
    }
    if not isinstance(inputs, dict) or set(inputs) != expected_input_fields:
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable receipt input field set is not exact")
    expected_input_hashes = {
        "builder_fragment": pins["fragment"]["sha256"],
        "dependency_lock": pins["dependency_lock"]["sha256"],
        "builder_inventory": pins["inventory"]["sha256"],
        "resolution_plan": pins["resolution_plan"]["sha256"],
        "source_overlay": pins["source_overlay"]["sha256"],
    }
    for field, digest in expected_input_hashes.items():
        declaration = inputs.get(field)
        if not isinstance(declaration, dict) or declaration.get("sha256") != digest:
            raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", f"portable receipt does not bind {field}")
    builder_validation = inputs.get("builder_validation")
    expected_core_path = validation_path.with_name(
        "builder-dependency-validation.core.json"
    )
    declared_core_path = Path(
        str(builder_validation.get("path") or "")
    ).absolute() if isinstance(builder_validation, dict) else Path()
    if (
        not isinstance(builder_validation, dict)
        or declared_core_path != expected_core_path.absolute()
        or builder_validation.get("sha256") != core_sha256
        or builder_validation.get("selected_files") != selected_files
        or builder_validation.get("excluded_test_files") != excluded_test_files
        or builder_validation.get("excluded_test_bytes") != excluded_test_bytes
    ):
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable receipt does not bind the pre-product-entry validation")
    declared_core_path = _plain_file(
        declared_core_path,
        code="PORTABLE_PRODUCT_ENTRY_INVALID",
    )
    declared_core, declared_core_raw = _read_json(
        declared_core_path,
        code="PORTABLE_PRODUCT_ENTRY_INVALID",
    )
    if (
        declared_core != dict(validation_core)
        or declared_core_raw != _json_bytes(dict(validation_core))
        or _sha256_bytes(declared_core_raw) != core_sha256
    ):
        raise MaterialError(
            "PORTABLE_PRODUCT_ENTRY_INVALID",
            "pre-product-entry validation file is not the exact canonical final-validation core",
        )
    staging = receipt.get("staging")
    if (
        not isinstance(staging, dict)
        or staging.get("dependency_files") != selected_files
        or staging.get("dependency_bytes") != selected_bytes
        or not str(staging.get("site_packages") or "").strip()
        or not isinstance(staging.get("path_budget"), dict)
        or not isinstance(staging.get("install_root_path_acceptance"), dict)
    ):
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable staging counts do not match the v2 tree")
    python_fragment_declaration = inputs["python_component_fragment"]
    python_fragment_path = _plain_file(
        Path(str(python_fragment_declaration.get("path") or "")),
        code="PORTABLE_PRODUCT_ENTRY_INVALID",
    )
    python_fragment, _python_fragment_raw = _read_json(
        python_fragment_path,
        code="PORTABLE_PRODUCT_ENTRY_INVALID",
    )
    try:
        python_component = _rewrite_component(
            python_fragment,
            base=python_fragment_path.parent,
            field="portable_receipt.python_component_fragment",
        )
        python_plan = builder._plan_component(
            python_fragment_path.parent,
            python_component,
        )
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    model_identity_configs = inputs.get("model_identity_configs")
    expected_model_identity = {"config.json", "speech_tokenizer/config.json"}
    if not isinstance(model_identity_configs, dict) or set(model_identity_configs) != expected_model_identity:
        raise MaterialError(
            "PORTABLE_PRODUCT_ENTRY_INVALID",
            "portable product-entry model identity config set is not exact",
        )
    smoke_relative_paths = {
        *(record.target for record in python_plan.files),
        *(record.target for plan in component_plans for record in plan.files),
        "runtime/runner/qwen_base_clone_server.py",
        *(
            f"runtime/models/qwen3-tts-12hz-0.6b-base/{relative}"
            for relative in expected_model_identity
        ),
        "metadata/source-overlays/qwen-12hz-only.json",
        "metadata/dependency-lock.json",
        "evidence/builder-dependency-lock.json",
    }
    expected_path_budget = builder.calculate_install_path_budget(smoke_relative_paths)
    try:
        root_acceptance = builder.validate_install_root_budget(
            Path(str(staging.get("root") or "")),
            staging["path_budget"],
            relative_paths=smoke_relative_paths,
        )
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    if (
        staging["path_budget"] != expected_path_budget
        or staging["install_root_path_acceptance"] != root_acceptance
    ):
        raise MaterialError(
            "PORTABLE_PRODUCT_ENTRY_INVALID",
            "portable staging path budget or root acceptance is not exact",
        )
    safety = receipt.get("safety")
    required_safety = {
        "network_connect_bind_and_listener_guards": True,
        "subprocess_guards": True,
        "offline_environment_flags": True,
        "all_cache_temp_profile_targets_on_e_drive": True,
        "model_execution": False,
        "cuda_api_called_by_probe": False,
        "service_started": False,
        "synthesis_called": False,
        "hardlinks_or_symlinks_used": False,
        "expected_capability_bind": "('::1', 0)",
        "expected_capability_bind_attempts": 1,
        "connect_attempts": 0,
        "listen_attempts": 0,
        "subprocess_attempts": 0,
        "host_external_native_dependency_edges": 0,
        "host_injected_observer_modules_recorded": True,
        "windows_system_boundary_recorded": True,
        "nvidia_driver_boundary_recorded": True,
        "numba_threading_layer": "workqueue",
        "tbb": "optional-not-shipped",
    }
    if not isinstance(safety, dict) or set(safety) != set(required_safety) or safety != required_safety:
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable safety result is not the exact guarded contract")
    probe = receipt.get("probe")
    result = probe.get("result") if isinstance(probe, dict) else None
    if (
        not isinstance(probe, dict)
        or set(probe) != {"path", "sha256", "command", "cwd", "environment", "returncode", "stdout", "stderr", "result"}
        or probe.get("returncode") != 0
        or not isinstance(result, dict)
        or result.get("status") != "passed"
        or result.get("package_count") != package_count
        or result.get("product_entry") != EXPECTED_RUNTIME_POLICY["product_entry"]
        or result.get("guarded_attempts")
        != {"bind": ["('::1', 0)"], "connect": [], "listen": [], "subprocess": [], "model_load": []}
        or result.get("numba")
        != {"configured": "workqueue", "active_config": "workqueue", "tbbpool_loaded": False, "tbb": "optional-not-shipped"}
    ):
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable probe did not execute the exact guarded product entry")
    probe_path = _plain_file(Path(str(probe["path"])), code="PORTABLE_PRODUCT_ENTRY_INVALID")
    if _sha256_file(probe_path) != probe["sha256"]:
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable probe source hash changed")
    for field in ("stdout", "stderr"):
        declaration = probe[field]
        if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256"}:
            raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", f"portable probe {field} pin is invalid")
        path = _plain_file(Path(str(declaration["path"])), code="PORTABLE_PRODUCT_ENTRY_INVALID")
        if _sha256_file(path) != declaration["sha256"]:
            raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", f"portable probe {field} hash changed")


def _validate_portable_pe(
    receipt: dict[str, Any],
    *,
    portable_receipt: dict[str, Any],
    generation_id: str,
) -> None:
    expected_top = {
        "schema",
        "status",
        "profile",
        "generation_id",
        "scope",
        "binary_count",
        "dependency_edge_count",
        "unresolved",
        "windows_system_boundary",
        "nvidia_driver_boundary",
        "tbb",
        "binaries",
    }
    binaries = receipt.get("binaries")
    if (
        set(receipt) != expected_top
        or receipt.get("schema") != PORTABLE_PE_SCHEMA
        or receipt.get("status") != "passed"
        or receipt.get("profile") != PROFILE
        or receipt.get("generation_id") != generation_id
        or receipt.get("unresolved") != []
        or not isinstance(binaries, list)
        or receipt.get("binary_count") != len(binaries)
        or receipt.get("dependency_edge_count")
        != sum(len(item.get("dependencies", [])) for item in binaries if isinstance(item, dict))
        or receipt.get("tbb", {}).get("status") != "optional-not-shipped"
        or receipt.get("tbb", {}).get("selected_numba_threading_layer") != "workqueue"
    ):
        raise MaterialError("PORTABLE_PE_RECEIPT_INVALID", "portable PE receipt is skeletal or internally inconsistent")
    smoke_root = _plain_directory(Path(str(portable_receipt["staging"]["root"])), field="portable smoke root")
    seen: set[str] = set()
    for index, row in enumerate(binaries):
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256", "dependencies"}:
            raise MaterialError("PORTABLE_PE_RECEIPT_INVALID", f"portable PE binary row {index} fields are invalid")
        relative = builder._safe_relative(row.get("path"), field=f"pe.binaries[{index}].path")
        path = _plain_file(smoke_root.joinpath(*Path(relative).parts), code="PORTABLE_PE_RECEIPT_INVALID")
        if relative in seen or path.stat().st_size != row.get("bytes") or _sha256_file(path) != row.get("sha256"):
            raise MaterialError("PORTABLE_PE_RECEIPT_INVALID", f"portable PE binary row {index} changed")
        if not isinstance(row.get("dependencies"), list):
            raise MaterialError("PORTABLE_PE_RECEIPT_INVALID", f"portable PE dependency row {index} is invalid")
        seen.add(relative)


def _validate_wheelhouse_v2(
    fragment_path: Path,
    validation_path: Path,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    fragment_path = _plain_file(fragment_path, code="WHEELHOUSE_FRAGMENT_MISSING")
    fragment, fragment_raw = _read_json(fragment_path, code="WHEELHOUSE_FRAGMENT_INVALID")
    if set(fragment) != FRAGMENT_FIELDS:
        raise MaterialError(
            "WHEELHOUSE_FRAGMENT_INVALID",
            f"wheelhouse fragment fields differ: {sorted(set(fragment) ^ FRAGMENT_FIELDS)}",
        )
    if fragment.get("schema") != WHEELHOUSE_FRAGMENT_SCHEMA or fragment.get("profile") != PROFILE:
        raise MaterialError(
            "WHEELHOUSE_PROFILE_REQUIRED",
            f"wheelhouse fragment must be {WHEELHOUSE_FRAGMENT_SCHEMA} profile={PROFILE}",
        )
    generation_id = fragment.get("generation_id")
    if not isinstance(generation_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{7,79}", generation_id
    ):
        raise MaterialError("WHEELHOUSE_GENERATION_INVALID", "v2 fragment generation id is invalid")
    if fragment.get("runtime_policy") != EXPECTED_RUNTIME_POLICY:
        raise MaterialError("WHEELHOUSE_POLICY_INVALID", "12Hz runtime policy is not the locked release policy")
    components = fragment.get("components")
    locks = fragment.get("dependency_lock")
    if not isinstance(components, list) or not components or not isinstance(locks, list):
        raise MaterialError("WHEELHOUSE_FRAGMENT_INVALID", "components/dependency_lock must be non-empty lists")
    if len(components) != EXPECTED_V2_PACKAGE_COUNT or len(locks) != EXPECTED_V2_PACKAGE_COUNT:
        raise MaterialError(
            "WHEELHOUSE_FRAGMENT_INVALID",
            f"strict v2 requires exactly {EXPECTED_V2_PACKAGE_COUNT} dependency components",
        )
    names = {str(item.get("name") or "").strip().casefold() for item in components if isinstance(item, Mapping)}
    forbidden = sorted(names & FORBIDDEN_DISTRIBUTIONS)
    if forbidden:
        raise MaterialError("WHEELHOUSE_POLICY_INVALID", f"25Hz-only distributions are present: {forbidden}")

    resolution = _rewrite_declaration(
        fragment.get("resolution_plan"),
        base=fragment_path.parent,
        field="fragment.resolution_plan",
    )
    resolution_path = Path(resolution["path"])
    plan, _plan_raw = _read_json(resolution_path, code="WHEELHOUSE_PLAN_INVALID")
    if (
        plan.get("schema") != WHEELHOUSE_PLAN_SCHEMA
        or plan.get("profile") != PROFILE
        or plan.get("generation_id") != generation_id
        or plan.get("blockers") != []
    ):
        raise MaterialError("WHEELHOUSE_PLAN_INVALID", "12Hz resolution plan schema/profile/blockers are invalid")
    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, list):
        raise MaterialError("WHEELHOUSE_PLAN_INVALID", "resolution plan artifacts must be a list")
    artifact_names = {
        str(item.get("name") or "").strip().casefold()
        for item in artifacts
        if isinstance(item, Mapping)
    }
    if artifact_names != names:
        raise MaterialError(
            "WHEELHOUSE_PLAN_MISMATCH",
            f"resolution/component sets differ: plan_only={sorted(artifact_names - names)}, "
            f"fragment_only={sorted(names - artifact_names)}",
        )
    removed = sorted(
        str(item.get("name") or "").strip().casefold()
        for item in plan.get("removed_artifacts", [])
        if isinstance(item, Mapping)
    )
    if removed != EXPECTED_REMOVED_DISTRIBUTIONS:
        raise MaterialError("WHEELHOUSE_PLAN_INVALID", "resolution plan removed-package set is not exact")

    generation_root = fragment_path.parent.parent
    expected_validation_path = (
        generation_root / "evidence" / "builder-dependency-validation.json"
    )
    if (
        fragment_path.parent.name != "builder-inputs"
        or validation_path.absolute() != expected_validation_path.absolute()
    ):
        raise MaterialError(
            "WHEELHOUSE_EVIDENCE_PATH_INVALID",
            "strict v2 final validation must be inside its generation evidence directory",
        )
    validation_path = _plain_file(validation_path, code="WHEELHOUSE_VALIDATION_INVALID")
    validation, _validation_raw = _read_json(
        validation_path,
        code="WHEELHOUSE_VALIDATION_INVALID",
    )
    if set(validation) != VALIDATION_FINAL_FIELDS:
        raise MaterialError(
            "WHEELHOUSE_VALIDATION_INVALID",
            f"v2 validation fields differ: {sorted(set(validation) ^ VALIDATION_FINAL_FIELDS)}",
        )
    core = {field: copy.deepcopy(validation[field]) for field in VALIDATION_CORE_FIELDS}
    core_sha256 = _sha256_bytes(_json_bytes(core))
    if (
        validation.get("schema") != WHEELHOUSE_VALIDATION_SCHEMA
        or validation.get("profile") != PROFILE
        or validation.get("generation_id") != generation_id
        or validation.get("status") != "passed"
        or validation.get("pre_product_entry_validation_sha256") != core_sha256
        or validation.get("package_count") != len(components)
        or validation.get("numba_threading_layer") != "workqueue"
        or validation.get("tbb") != "optional-not-shipped"
    ):
        raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", "v2 validation core hash or release policy is invalid")

    base = validation_path.parent
    fragment_pin = validation["fragment"]
    pinned_fragment, pinned_fragment_path, pinned_fragment_raw = _strict_json_pin(
        fragment_pin,
        base=base,
        field="validation.fragment",
        schema=WHEELHOUSE_FRAGMENT_SCHEMA,
    )
    if (
        pinned_fragment_path != fragment_path
        or pinned_fragment_raw != fragment_raw
        or pinned_fragment != fragment
    ):
        raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", "validation does not bind the exact raw fragment")
    lock_payload, lock_path, _lock_raw = _strict_json_pin(
        validation["dependency_lock"],
        base=base,
        field="validation.dependency_lock",
        schema=WHEELHOUSE_LOCK_SCHEMA,
        extra_fields={"package_count"},
    )
    inventory, inventory_path, _inventory_raw = _strict_json_pin(
        validation["inventory"],
        base=base,
        field="validation.inventory",
        schema=WHEELHOUSE_INVENTORY_SCHEMA,
        extra_fields={"package_count"},
    )
    pinned_plan, pinned_plan_path, _pinned_plan_raw = _strict_json_pin(
        validation["resolution_plan"],
        base=base,
        field="validation.resolution_plan",
        schema=WHEELHOUSE_PLAN_SCHEMA,
        extra_fields={"profile"},
    )
    if (
        pinned_plan_path != resolution_path
        or pinned_plan != plan
        or validation["resolution_plan"]["sha256"] != resolution["sha256"]
        or validation["resolution_plan"]["profile"] != PROFILE
    ):
        raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", "validation resolution plan pin is inconsistent")
    if (
        set(lock_payload) != {"schema", "target", "package_count", "packages"}
        or lock_payload.get("package_count") != len(locks)
        or validation["dependency_lock"]["package_count"] != len(locks)
        or lock_payload.get("packages") != locks
    ):
        raise MaterialError("WHEELHOUSE_LOCK_INVALID", "v2 dependency lock does not exactly equal fragment.dependency_lock")

    rewritten_components = [
        _rewrite_component(item, base=fragment_path.parent, field=f"fragment.components[{index}]")
        for index, item in enumerate(components)
    ]
    lock_by_id = {
        str(item.get("component_id") or ""): item
        for item in locks
        if isinstance(item, dict)
    }
    if len(lock_by_id) != len(locks):
        raise MaterialError("WHEELHOUSE_LOCK_INVALID", "dependency lock IDs are missing or duplicated")
    component_plans: list[builder.ComponentPlan] = []
    try:
        for component in rewritten_components:
            component_plan = builder._plan_component(fragment_path.parent, component)
            component_id = str(component.get("id") or "")
            if component_id not in lock_by_id:
                raise MaterialError("WHEELHOUSE_LOCK_INVALID", f"missing lock for {component_id}")
            builder._validate_dependency_component(fragment_path.parent, component_plan, lock_by_id[component_id])
            component_plans.append(component_plan)
        _validate_exact_roots(component_plans, kinds={"dependency"})
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc

    selected_records: dict[str, builder.FileRecord] = {}
    for component_plan in component_plans:
        for record in component_plan.files:
            previous = selected_records.setdefault(record.source_relative, record)
            if previous.sha256 != record.sha256 or previous.size != record.size:
                raise MaterialError("WHEELHOUSE_TREE_INVALID", f"dependency path collision: {record.source_relative}")
    selected_files = len(selected_records)
    selected_bytes = sum(record.size for record in selected_records.values())
    license_files = sum(len(component.license_records) for component in component_plans)
    excluded_test_files = sum(len(component.wheel_exclusions) for component in component_plans)
    excluded_test_bytes = sum(
        item["bytes"] for component in component_plans for item in component.wheel_exclusions
    )
    site_roots = {Path(str(component.component["root"])).absolute() for component in component_plans}
    if len(site_roots) != 1:
        raise MaterialError("WHEELHOUSE_TREE_INVALID", "v2 dependency components must share one exact site-packages tree")
    site_stats = _site_tree_stats(next(iter(site_roots)))
    license_evidence = _license_evidence_from_plans(
        component_plans,
        site_root=next(iter(site_roots)),
    )
    _require_exact_license_evidence(
        fragment.get("license_evidence"),
        license_evidence,
        component_license_file_count=license_files,
    )
    if validation.get("site_packages") != site_stats:
        raise MaterialError("WHEELHOUSE_TREE_INVALID", "validation site-packages file/byte/tree closure differs")

    expected_runtime_testlike = sorted(builder.TRUSTED_RUNTIME_TESTLIKE_MEMBERS)
    declared_runtime_testlike = sorted(
        item["path"]
        for component in component_plans
        for item in component.runtime_required_testlike
    )
    trace_records = [
        component.runtime_import_trace_record
        for component in component_plans
        if component.runtime_import_trace_record is not None
    ]
    trace_hashes = {record["sha256"] for record in trace_records}
    source_trace_hashes = {
        record["payload"]["source_trace"]["sha256"] for record in trace_records
    }
    expected_trace = {
        "contract_sha256": next(iter(trace_hashes)) if len(trace_hashes) == 1 else "",
        "source_trace_sha256": next(iter(source_trace_hashes)) if len(source_trace_hashes) == 1 else "",
    }
    if (
        declared_runtime_testlike != expected_runtime_testlike
        or validation.get("runtime_required_testlike_members") != expected_runtime_testlike
        or validation.get("runtime_import_trace") != expected_trace
    ):
        raise MaterialError("WHEELHOUSE_RUNTIME_TRACE_INVALID", "runtime test-like/source trace closure is not exact")

    overlay = _rewrite_declaration(
        fragment["runner_source_overlay"],
        base=fragment_path.parent,
        field="fragment.runner_source_overlay",
    )
    try:
        overlay_record = builder._read_source_overlay_contract(
            fragment_path.parent,
            overlay,
            component_id="mentorflow-qwen-runner",
        )
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    expected_overlay = {
        "path": overlay["path"],
        "sha256": overlay["sha256"],
        "policy": overlay_record["payload"]["policy"],
        "upstream_wheel_sha256": overlay_record["payload"]["upstream_wheel"]["sha256"],
        "transforms": overlay_record["payload"]["transforms"],
    }
    aggregate_expectations = {
        "package_count": len(component_plans),
        "selected_files": selected_files,
        "selected_bytes": selected_bytes,
        "license_files": license_files,
        "wheel_license_files": license_evidence["wheel_file_count"],
        "external_license_supplement_files": license_evidence[
            "external_supplement_file_count"
        ],
        "license_evidence": license_evidence,
        "excluded_test_files": excluded_test_files,
        "excluded_test_bytes": excluded_test_bytes,
        "removed_packages": EXPECTED_REMOVED_DISTRIBUTIONS,
        "runtime_required_testlike_members": expected_runtime_testlike,
        "runtime_import_trace": expected_trace,
        "source_overlay": expected_overlay,
    }
    if any(validation.get(field) != value for field, value in aggregate_expectations.items()):
        raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", "validation aggregate/trace/overlay fields do not match the exact tree")
    expected_inventory_fields = {
        "schema",
        "profile",
        "generation_id",
        "package_count",
        "selected_files",
        "selected_bytes",
        "license_files",
        "wheel_license_files",
        "external_license_supplement_files",
        "license_evidence",
        "excluded_test_files",
        "excluded_test_bytes",
        "runtime_required_testlike_members",
        "runtime_required_reason",
        "runtime_import_trace",
        "source_overlay",
        "components",
        "removed_packages",
        "resolution_plan_sha256",
        "tbb",
    }
    inventory_overlay = {
        "contract_sha256": expected_overlay["sha256"],
        "policy": expected_overlay["policy"],
        "upstream_wheel_sha256": expected_overlay["upstream_wheel_sha256"],
        "transforms": expected_overlay["transforms"],
    }
    expected_inventory_components = []
    for component_plan in component_plans:
        component = component_plan.component
        lock = lock_by_id[str(component["id"])]
        expected_inventory_components.append(
            {
                "name": component["name"],
                "version": component["version"],
                "purl": component["purl"],
                "license_expression": component["license_expression"],
                "license_sha256": sorted(
                    {str(record["sha256"]) for record in component_plan.license_records}
                ),
                "wheel_sha256": lock["wheel_sha256"],
                "selected_files": len(component_plan.files),
                "excluded_test_files": len(component_plan.wheel_exclusions),
                "excluded_test_bytes": sum(
                    item["bytes"] for item in component_plan.wheel_exclusions
                ),
                "wheel_exclusions": list(component_plan.wheel_exclusions),
                "runtime_required_testlike": list(
                    component_plan.runtime_required_testlike
                ),
                "runtime_import_trace_sha256": (
                    component_plan.runtime_import_trace_record["sha256"]
                    if component_plan.runtime_import_trace_record is not None
                    else None
                ),
            }
        )
    expected_inventory_components.sort(key=lambda item: (item["name"], item["version"]))
    actual_inventory_components = inventory.get("components")
    if isinstance(actual_inventory_components, list):
        actual_inventory_components = sorted(
            actual_inventory_components,
            key=lambda item: (str(item.get("name") or ""), str(item.get("version") or ""))
            if isinstance(item, dict)
            else ("", ""),
        )
    if (
        set(inventory) != expected_inventory_fields
        or validation["inventory"]["package_count"] != len(component_plans)
        or inventory.get("profile") != PROFILE
        or inventory.get("generation_id") != generation_id
        or inventory.get("package_count") != len(component_plans)
        or inventory.get("selected_files") != selected_files
        or inventory.get("selected_bytes") != selected_bytes
        or inventory.get("license_files") != license_files
        or inventory.get("wheel_license_files") != license_evidence["wheel_file_count"]
        or inventory.get("external_license_supplement_files")
        != license_evidence["external_supplement_file_count"]
        or inventory.get("license_evidence") != license_evidence
        or inventory.get("excluded_test_files") != excluded_test_files
        or inventory.get("excluded_test_bytes") != excluded_test_bytes
        or inventory.get("runtime_required_testlike_members") != expected_runtime_testlike
        or inventory.get("runtime_import_trace") != expected_trace
        or inventory.get("source_overlay") != inventory_overlay
        or inventory.get("removed_packages") != EXPECTED_REMOVED_DISTRIBUTIONS
        or inventory.get("resolution_plan_sha256") != resolution["sha256"]
        or inventory.get("tbb") != {"status": "optional-not-shipped", "selected_numba_threading_layer": "workqueue"}
        or actual_inventory_components != expected_inventory_components
    ):
        raise MaterialError("WHEELHOUSE_INVENTORY_INVALID", "inventory does not close over fragment/lock/v2 tree")

    pe_receipt, pe_path, _pe_raw = _strict_json_pin(
        validation["pe_dependency_audit"],
        base=base,
        field="validation.pe_dependency_audit",
        schema=PORTABLE_PE_SCHEMA,
        extra_fields={"status", "profile"},
    )
    portable_receipt, portable_path, _portable_raw = _strict_json_pin(
        validation["portable_product_entry_receipt"],
        base=base,
        field="validation.portable_product_entry_receipt",
        schema=PORTABLE_PRODUCT_ENTRY_SCHEMA,
        extra_fields={"status", "profile"},
    )
    expected_portable_path = generation_root / "evidence" / "portable-python-import-smoke.json"
    smoke_root = Path(str(portable_receipt.get("staging", {}).get("root") or ""))
    expected_pe_path = smoke_root / "evidence" / "portable-pe-dependency-audit.json"
    if (
        portable_path.absolute() != expected_portable_path.absolute()
        or pe_path.absolute() != expected_pe_path.absolute()
    ):
        raise MaterialError(
            "WHEELHOUSE_EVIDENCE_PATH_INVALID",
            "portable and PE receipts must stay inside their unique generation/smoke evidence roots",
        )
    for declaration in (validation["pe_dependency_audit"], validation["portable_product_entry_receipt"]):
        if declaration["status"] != "passed" or declaration["profile"] != PROFILE:
            raise MaterialError("WHEELHOUSE_VALIDATION_INVALID", "portable receipt declaration status/profile is invalid")
    pins = {
        "fragment": validation["fragment"],
        "dependency_lock": validation["dependency_lock"],
        "inventory": validation["inventory"],
        "resolution_plan": validation["resolution_plan"],
        "source_overlay": expected_overlay,
    }
    _validate_portable_product_entry(
        portable_receipt,
        validation_path=validation_path,
        validation_core=core,
        core_sha256=core_sha256,
        pins=pins,
        selected_files=selected_files,
        selected_bytes=selected_bytes,
        excluded_test_files=excluded_test_files,
        excluded_test_bytes=excluded_test_bytes,
        package_count=len(component_plans),
        generation_id=generation_id,
        component_plans=component_plans,
    )
    portable_pe_pin = portable_receipt.get("pe_dependency_audit")
    if (
        not isinstance(portable_pe_pin, dict)
        or portable_pe_pin.get("path") != str(pe_path)
        or portable_pe_pin.get("sha256") != validation["pe_dependency_audit"]["sha256"]
        or portable_pe_pin.get("status") != "passed"
    ):
        raise MaterialError("PORTABLE_PRODUCT_ENTRY_INVALID", "portable receipt does not bind the exact PE audit")
    _validate_portable_pe(
        pe_receipt,
        portable_receipt=portable_receipt,
        generation_id=generation_id,
    )
    return fragment, fragment_path.parent, {
        "validation": validation,
        "validation_path": validation_path,
        "core_sha256": core_sha256,
        "portable_receipt": portable_receipt,
        "portable_receipt_path": portable_path,
        "pe_receipt": pe_receipt,
        "lock_path": lock_path,
        "inventory_path": inventory_path,
        "component_plans": tuple(component_plans),
    }


def _model_receipt(model_root: Path) -> dict[str, Any]:
    model_root = _plain_directory(model_root, field="model root")
    actual = _walk_plain_files(model_root)
    expected = set(builder.TRUSTED_MODEL_FILES)
    if set(actual) != expected:
        raise MaterialError(
            "MODEL_FILE_SET_MISMATCH",
            f"model must contain the exact trusted 11-file set; "
            f"extra={sorted(set(actual) - expected)}, missing={sorted(expected - set(actual))}",
        )
    files = []
    for relative, (expected_bytes, expected_sha256) in sorted(builder.TRUSTED_MODEL_FILES.items()):
        path = actual[relative]
        size = path.stat().st_size
        digest = _sha256_file(path)
        if size != expected_bytes or digest != expected_sha256:
            raise MaterialError(
                "MODEL_FILE_MISMATCH",
                f"trusted model file mismatch: {relative}",
            )
        files.append({"path": relative, "bytes": size, "sha256": digest})
    include = sorted(builder.TRUSTED_MODEL_FILES)
    try:
        tree = builder.calculate_allowlist_tree_sha256(model_root, include)
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    if tree != builder.TRUSTED_MODEL_TREE_SHA256:
        raise MaterialError("MODEL_TREE_MISMATCH", "trusted model tree digest does not match")
    return {
        "schema": MODEL_RECEIPT_SCHEMA,
        "repository": builder.TRUSTED_MODEL_REPOSITORY,
        "revision": builder.TRUSTED_MODEL_REVISION,
        "license": "Apache-2.0",
        "tree_sha256": tree,
        "cache_metadata_excluded": True,
        "files": files,
    }


def _pin(path: Path, *, relative_to: Path | None = None, raw_public_sha256: str = "") -> dict[str, Any]:
    path = _plain_file(path, code="PINNED_INPUT_MISSING")
    rendered = (
        path.relative_to(relative_to).as_posix()
        if relative_to is not None and path.is_relative_to(relative_to)
        else str(path)
    )
    value: dict[str, Any] = {"path": rendered, "sha256": _sha256_file(path)}
    if raw_public_sha256:
        value["public_key_sha256"] = raw_public_sha256
    return value


def _write_immutable(path: Path, content: bytes, *, verify_only: bool) -> None:
    path = path.absolute()
    if path.parent == path:
        raise MaterialError("OUTPUT_INVALID", "output cannot be a filesystem root")
    if verify_only:
        if not path.exists() and not path.is_symlink():
            raise MaterialError("OUTPUT_MISSING", f"verified output is missing: {path}")
    try:
        immutable_write(path, content)
    except ImmutableEvidenceError as exc:
        codes = {
            "IMMUTABLE_TARGET_EXISTS": "OUTPUT_EXISTS",
            "IMMUTABLE_TARGET_INVALID": "OUTPUT_INVALID",
            "IMMUTABLE_REPARSE_FORBIDDEN": "REPARSE_POINT_FORBIDDEN",
        }
        raise MaterialError(codes.get(exc.code, exc.code), str(exc)) from exc


def prepare_release_materials(args: argparse.Namespace) -> dict[str, Any]:
    if not UTC_SECOND.fullmatch(args.created_at_utc):
        raise MaterialError("SOURCE_DATE_INVALID", "--created-at-utc must be a pinned UTC second")
    output_root = Path(args.output_root).absolute()
    if output_root.parent == output_root:
        raise MaterialError("OUTPUT_INVALID", "--output-root cannot be a filesystem root")

    fragment, fragment_base, wheelhouse_closure = _validate_wheelhouse_v2(
        Path(args.wheelhouse_fragment),
        Path(args.wheelhouse_validation),
    )
    dependency_components = [
        _rewrite_component(item, base=fragment_base, field=f"fragment.components[{index}]")
        for index, item in enumerate(fragment["components"])
    ]

    portable_path = _plain_file(Path(args.portable_component), code="PORTABLE_COMPONENT_MISSING")
    portable, _raw_portable = _read_json(portable_path, code="PORTABLE_COMPONENT_INVALID")
    portable_component = _rewrite_component(
        portable,
        base=portable_path.parent,
        field="portable_component",
    )
    try:
        portable_plan = builder._plan_component(portable_path.parent, portable_component)
        builder._validate_portable_python_component(
            portable_plan,
            spec_dir=portable_path.parent,
            require_material_inventory=True,
        )
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    _validate_live_portable_authenticode(portable_component, portable_plan)
    portable_inputs = wheelhouse_closure["portable_receipt"]["inputs"]
    if (
        portable_inputs["python_component_fragment"].get("sha256") != _sha256_file(portable_path)
        or portable_inputs["python_component_fragment"].get("tree_sha256")
        != portable_plan.tree_sha256
        or portable_inputs["python_source_receipt"].get("sha256")
        != portable_component["receipt"]["sha256"]
    ):
        raise MaterialError(
            "PORTABLE_PYTHON_RECEIPT_INVALID",
            "portable product-entry receipt does not bind the supplied CPython fragment/source receipt/tree",
        )

    trusted_source_path = _plain_file(
        Path(args.trusted_qwen_source_receipt),
        code="TRUSTED_SOURCE_RECEIPT_MISSING",
    )
    try:
        builder._validate_trusted_source_receipt(
            trusted_source_path.parent,
            _pin(trusted_source_path),
        )
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc

    model_root = _plain_directory(Path(args.model_root), field="model root")
    model_license = _plain_file(Path(args.model_license), code="MODEL_LICENSE_MISSING")
    if _sha256_file(model_license) != builder.TRUSTED_QWEN_LICENSE_SHA256:
        raise MaterialError("MODEL_LICENSE_UNTRUSTED", "model license is not the locked Qwen Apache-2.0 license")
    receipt_value = _model_receipt(model_root)
    receipt_path = output_root / "trusted-huggingface-model-receipt.v1.json"
    _write_immutable(receipt_path, _json_bytes(receipt_value), verify_only=args.verify_only)

    runner_root = _plain_directory(Path(args.runner_root), field="runner root")
    runner_relative = builder._safe_relative(args.runner_entrypoint, field="runner.entrypoint")
    runner_receipt = _plain_file(Path(args.runner_receipt), code="RUNNER_RECEIPT_MISSING")
    runner_license = _plain_file(Path(args.runner_license), code="RUNNER_LICENSE_MISSING")
    runner_notice = _plain_file(Path(args.runner_notice), code="RUNNER_NOTICE_MISSING")
    try:
        runner_tree = builder.calculate_allowlist_tree_sha256(runner_root, [runner_relative])
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    overlay = _rewrite_declaration(
        fragment["runner_source_overlay"],
        base=fragment_base,
        field="fragment.runner_source_overlay",
    )
    runner_component = {
        "id": "mentorflow-qwen-runner",
        "kind": "runner",
        "name": "MentorFlow Qwen Base Clone runner",
        "version": args.runner_version,
        "supplier": "MentorFlow",
        "root": str(runner_root),
        "target_root": builder.TARGET_ROOTS["runner"],
        "include": [runner_relative],
        "expected_tree_sha256": runner_tree,
        "license_expression": args.runner_license_expression,
        "license_files": [_pin(runner_license), _pin(runner_notice)],
        "receipt": _pin(runner_receipt),
        "source_overlay": overlay,
        "homepage": args.runner_homepage,
    }
    try:
        runner_plan = builder._plan_component(output_root, runner_component)
        builder._validate_runner_component(runner_plan)
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    portable_runner = portable_inputs["runner"]
    if (
        not isinstance(portable_runner, dict)
        or portable_runner.get("sha256") != runner_plan.files[0].sha256
    ):
        raise MaterialError(
            "RUNNER_RECEIPT_INVALID",
            "portable product-entry receipt did not execute the exact runner source selected for release",
        )
    portable_receipt = wheelhouse_closure["portable_receipt"]
    model_identity_configs = portable_inputs.get("model_identity_configs")
    expected_model_identity = {"config.json", "speech_tokenizer/config.json"}
    if not isinstance(model_identity_configs, dict) or set(model_identity_configs) != expected_model_identity:
        raise MaterialError(
            "PORTABLE_PRODUCT_ENTRY_INVALID",
            "portable product-entry model identity config set is not exact",
        )
    smoke_relative_paths = {
        *(record.target for record in portable_plan.files),
        *(
            record.target
            for component_plan in wheelhouse_closure["component_plans"]
            for record in component_plan.files
        ),
        *(record.target for record in runner_plan.files),
        *(
            f"runtime/models/qwen3-tts-12hz-0.6b-base/{relative}"
            for relative in expected_model_identity
        ),
        "metadata/source-overlays/qwen-12hz-only.json",
        "metadata/dependency-lock.json",
        "evidence/builder-dependency-lock.json",
    }
    expected_smoke_budget = builder.calculate_install_path_budget(smoke_relative_paths)
    staging = portable_receipt["staging"]
    smoke_root = Path(str(staging.get("root") or ""))
    try:
        smoke_acceptance = builder.validate_install_root_budget(
            smoke_root,
            staging.get("path_budget"),
            relative_paths=smoke_relative_paths,
        )
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    if (
        staging.get("path_budget") != expected_smoke_budget
        or staging.get("install_root_path_acceptance") != smoke_acceptance
    ):
        raise MaterialError(
            "PORTABLE_PRODUCT_ENTRY_INVALID",
            "portable product-entry path budget or root acceptance is not exact",
        )
    model_component = {
        "id": "qwen3-tts-0.6b-base",
        "kind": "model",
        "name": "Qwen3-TTS-12Hz-0.6B-Base",
        "version": builder.TRUSTED_MODEL_REVISION,
        "supplier": "Qwen",
        "root": str(model_root),
        "target_root": builder.TARGET_ROOTS["model"],
        "include": sorted(builder.TRUSTED_MODEL_FILES),
        "expected_tree_sha256": builder.TRUSTED_MODEL_TREE_SHA256,
        "license_expression": "Apache-2.0",
        "license_files": [_pin(model_license)],
        "receipt": _pin(receipt_path, relative_to=output_root),
        "homepage": f"https://huggingface.co/{builder.TRUSTED_MODEL_REPOSITORY}",
        "model": {
            "repository": builder.TRUSTED_MODEL_REPOSITORY,
            "revision": builder.TRUSTED_MODEL_REVISION,
        },
    }

    private_key = _plain_file(Path(args.signing_private_key), code="SIGNING_KEY_MISSING")
    public_key = _plain_file(Path(args.trusted_public_key), code="SIGNING_KEY_MISSING")
    try:
        loaded_public = builder._load_public_key(public_key)
        public_fingerprint = _sha256_bytes(builder._public_key_raw(loaded_public))
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    if args.trusted_public_key_sha256 and args.trusted_public_key_sha256 != public_fingerprint:
        raise MaterialError("TRUSTED_KEY_HASH_MISMATCH", "raw trusted public-key fingerprint differs")

    spec = {
        "schema": builder.SPEC_SCHEMA,
        "pack": {
            "id": args.package_id,
            "version": args.version,
            "platform": builder.PLATFORM,
            "architecture": "x86_64",
            "created_at_utc": args.created_at_utc,
        },
        "trusted_qwen_source_receipt": _pin(trusted_source_path),
        "components": [
            portable_component,
            runner_component,
            model_component,
            *dependency_components,
        ],
        "dependency_lock": copy.deepcopy(fragment["dependency_lock"]),
        "runtime_gates": {
            "root_resolution": "pack-relative",
            "entrypoint": f"runtime/runner/{runner_relative}",
            "root_env": "MENTORFLOW_VOICE_RUNTIME_ROOT",
            "host_env": "MENTORFLOW_VOICE_HOST",
            "port_env": "MENTORFLOW_VOICE_PORT",
            "device_env": "MENTORFLOW_VOICE_DEVICE",
            "driver_minimum_env": "MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER",
            "default_host": "127.0.0.1",
            "default_port": args.default_port,
            "default_device": "cuda:0",
            "default_minimum_driver": args.minimum_nvidia_driver,
            "driver_vendor": "nvidia",
            "on_driver_unsupported": "fail",
        },
        "signing": {
            "algorithm": "ed25519",
            "key_id": args.signing_key_id,
            "private_key": _pin(private_key),
            "trusted_public_key": _pin(
                public_key,
                raw_public_sha256=public_fingerprint,
            ),
        },
    }
    spec_path = output_root / "qwen3-tts-runtime-build-spec.v1.json"
    _write_immutable(spec_path, _json_bytes(spec), verify_only=args.verify_only)
    try:
        plan = builder.plan_from_spec(spec_path)
        _validate_exact_roots(plan["plans"], kinds={"portable_python", "model", "dependency"})
    except builder.BuildError as exc:
        raise MaterialError(exc.code, str(exc)) from exc
    return {
        "schema": "mentorflow.qwen3-tts-runtime-release-materials-report.v1",
        "status": "verified" if args.verify_only else "prepared",
        "copies_runtime_payload": False,
        "downloads": False,
        "starts_service": False,
        "generates_keys": False,
        "profile": PROFILE,
        "package_id": plan["pack_id"],
        "version": plan["version"],
        "component_count": len(plan["plans"]),
        "dependency_count": len(dependency_components),
        "file_count": plan["file_count"],
        "bytes": plan["bytes"],
        "install_path_budget": plan["path_budget"],
        "model_receipt": {
            "path": str(receipt_path),
            "sha256": _sha256_file(receipt_path),
            "tree_sha256": receipt_value["tree_sha256"],
            "files": len(receipt_value["files"]),
        },
        "build_spec": {
            "path": str(spec_path),
            "sha256": _sha256_file(spec_path),
            "schema": builder.SPEC_SCHEMA,
        },
        "signing_key_id": args.signing_key_id,
        "trusted_public_key_sha256": public_fingerprint,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare deterministic offline Qwen3-TTS builder inputs without signing or starting a service."
    )
    parser.add_argument("--wheelhouse-fragment", type=Path, required=True)
    parser.add_argument("--wheelhouse-validation", type=Path, required=True)
    parser.add_argument("--portable-component", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--model-license", type=Path, required=True)
    parser.add_argument("--trusted-qwen-source-receipt", type=Path, required=True)
    parser.add_argument("--runner-root", type=Path, required=True)
    parser.add_argument("--runner-entrypoint", default="qwen_base_clone_server.py")
    parser.add_argument("--runner-receipt", type=Path, required=True)
    parser.add_argument("--runner-license", type=Path, required=True)
    parser.add_argument("--runner-notice", type=Path, required=True)
    parser.add_argument(
        "--runner-license-expression",
        required=True,
        help="owner-confirmed SPDX expression matching the independent runner license",
    )
    parser.add_argument("--runner-version", default="1.0.0")
    parser.add_argument("--runner-homepage", default="https://mentorflow.invalid/local-voice-runtime")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--package-id", default="mentorflow-qwen3-tts-runtime")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--created-at-utc", required=True)
    parser.add_argument("--default-port", type=int, default=8892)
    parser.add_argument("--minimum-nvidia-driver", default="555.00")
    parser.add_argument("--signing-key-id", required=True)
    parser.add_argument("--signing-private-key", type=Path, required=True)
    parser.add_argument("--trusted-public-key", type=Path, required=True)
    parser.add_argument("--trusted-public-key-sha256", default="")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="recompute and compare existing outputs; never write missing files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = prepare_release_materials(args)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (MaterialError, builder.BuildError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": getattr(exc, "code", "RELEASE_MATERIALS_FAILED"),
                    "message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
