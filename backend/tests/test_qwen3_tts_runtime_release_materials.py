from __future__ import annotations

import base64
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import build_qwen3_tts_runtime_pack as builder
from scripts import prepare_qwen3_tts_runtime_release_materials as materials


def _write(path: Path, content: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(path: Path, *, sized: bool = False) -> dict:
    value = {"path": str(path), "sha256": _sha(path)}
    if sized:
        value["bytes"] = path.stat().st_size
    return value


def _json_pin(path: Path, schema: str, **extra) -> dict:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha(path),
        "schema": schema,
        **extra,
    }


def _relative_pin(root: Path, path: Path, *, schema: str | None = None) -> dict:
    value = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha(path),
    }
    if schema is not None:
        value["schema"] = schema
    return value


def _inventory_tree(root: Path, relative_root: str) -> dict:
    records = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha(path),
        }
        for path in root.rglob("*")
        if path.is_file()
    ]
    records.sort(key=lambda item: (item["path"].casefold(), item["path"]))
    digest = hashlib.sha256()
    for row in sorted(records, key=lambda item: item["path"]):
        digest.update(
            f"{row['path']}\0{row['bytes']}\0{row['sha256']}\n".encode("utf-8")
        )
    return {
        "root": relative_root,
        "file_count": len(records),
        "total_bytes": sum(row["bytes"] for row in records),
        "tree_sha256": digest.hexdigest(),
        "files": records,
    }


def _record_token(content: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")


def _tiny_wheel(site_root: Path, wheelhouse: Path, license_path: Path) -> dict:
    package = _write(site_root / "qwen_tts" / "__init__.py", "__version__ = '0.1.1'\n")
    metadata = _write(
        site_root / "qwen_tts-0.1.1.dist-info" / "METADATA",
        "Metadata-Version: 2.4\nName: qwen-tts\nVersion: 0.1.1\nLicense-Expression: Apache-2.0\n\n",
    )
    wheel_license = _write(
        site_root / "qwen_tts-0.1.1.dist-info" / "licenses" / "LICENSE",
        license_path.read_bytes(),
    )
    record = site_root / "qwen_tts-0.1.1.dist-info" / "RECORD"
    rows = []
    for path in (package, metadata, wheel_license):
        relative = path.relative_to(site_root).as_posix()
        content = path.read_bytes()
        rows.append(f"{relative},{_record_token(content)},{len(content)}")
    rows.append("qwen_tts-0.1.1.dist-info/RECORD,,")
    _write(record, "\n".join(rows) + "\n")
    wheel = wheelhouse / "qwen_tts-0.1.1-py3-none-any.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in (package, metadata, wheel_license, record):
            archive.write(path, path.relative_to(site_root).as_posix())
    return {
        "include": ["qwen_tts", "qwen_tts-0.1.1.dist-info"],
        "metadata": metadata,
        "record": record,
        "license": wheel_license,
        "wheel": wheel,
    }


def _overlay_payload() -> dict:
    operations = {
        "qwen_tts/core/__init__.py": ["remove-v1-imports"],
        "qwen_tts/inference/qwen3_tts_tokenizer.py": [
            "remove-v1-imports",
            "remove-v1-registrations",
            "require-12hz-config-before-load",
            "require-12hz-model-after-load",
            "require-12hz-encode-decode",
        ],
    }
    transforms = []
    for index, (relative, input_sha256) in enumerate(
        sorted(builder.TRUSTED_QWEN_OVERLAY_INPUTS.items()),
        start=1,
    ):
        transforms.append(
            {
                "module": relative.removesuffix("/__init__.py").removesuffix(".py").replace("/", "."),
                "path": relative,
                "input_sha256": input_sha256,
                "output_sha256": f"{index:x}" * 64,
                "patch_sha256": f"{index + 2:x}" * 64,
                "operations": operations[relative],
            }
        )
    return {
        "schema": builder.SOURCE_OVERLAY_SCHEMA,
        "policy": "qwen3-tts-12hz-only-no-sox",
        "application": "verified-in-memory-import-loader",
        "upstream_wheel": {
            "name": "qwen-tts",
            "version": "0.1.1",
            "sha256": builder.TRUSTED_QWEN_WHEEL_SHA256,
        },
        "transforms": transforms,
        "model_identity": {
            "main_config": {
                "path": "config.json",
                "sha256": builder.TRUSTED_MODEL_FILES["config.json"][1],
                "required_fields": {
                    "model_type": "qwen3_tts",
                    "tokenizer_type": "qwen3_tts_tokenizer_12hz",
                    "tts_model_size": "0b6",
                    "tts_model_type": "base",
                },
            },
            "speech_tokenizer_config": {
                "path": "speech_tokenizer/config.json",
                "sha256": builder.TRUSTED_MODEL_FILES["speech_tokenizer/config.json"][1],
                "required_fields": {"model_type": "qwen3_tts_tokenizer_12hz"},
            },
        },
        "forbidden_imports": ["qwen_tts.core.tokenizer_25hz", "sox"],
        "upstream_wheel_bytes_modified": False,
    }


def test_license_evidence_consumer_preserves_103_plus_one_and_rejects_drift(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "site-packages"
    shared_sha = "a" * 64
    wheel_records = tuple(
        {
            "source": site_root / f"pkg-{index}.dist-info" / "LICENSE",
            "sha256": shared_sha if index < 2 else f"{index:064x}",
        }
        for index in range(103)
    )
    wheel_plan = builder.ComponentPlan(
        component={"id": "wheel-licenses"},
        files=(),
        tree_sha256="b" * 64,
        license_records=wheel_records,
        receipt_record={},
        license_supplement_record=None,
        wheel_exclusions=(),
        runtime_required_testlike=(),
        runtime_import_trace_record=None,
        source_overlay_record=None,
    )
    supplement_plan = builder.ComponentPlan(
        component={"id": "tokenizers==0.22.2"},
        files=(),
        tree_sha256="c" * 64,
        license_records=(
            {"source": tmp_path / "license-supplements" / "LICENSE", "sha256": shared_sha},
        ),
        receipt_record={},
        license_supplement_record={
            "sha256": "d" * 64,
            "payload": {
                "license_file": {
                    "saved_relative_path": (
                        "license-supplements/tokenizers==0.22.2/LICENSE"
                    )
                }
            },
        },
        wheel_exclusions=(),
        runtime_required_testlike=(),
        runtime_import_trace_record=None,
        source_overlay_record=None,
    )
    evidence = materials._license_evidence_from_plans(
        [wheel_plan, supplement_plan],
        site_root=site_root.absolute(),
    )

    assert evidence["wheel_file_count"] == 103
    assert evidence["external_supplement_file_count"] == 1
    assert evidence["total_file_count"] == 104
    assert len(evidence["wheel_files"]) == 103
    assert evidence["external_supplements"] == [
        {
            "component_id": "tokenizers==0.22.2",
            "path": "license-supplements/tokenizers==0.22.2/LICENSE",
            "sha256": shared_sha,
            "receipt_sha256": "d" * 64,
        }
    ]
    assert evidence["total_sha256"].count(shared_sha) == 3
    materials._require_exact_license_evidence(
        evidence,
        evidence,
        component_license_file_count=104,
    )

    drifted_values = []
    missing_wheel = json.loads(json.dumps(evidence))
    missing_wheel["wheel_files"].pop()
    drifted_values.append(missing_wheel)
    wheel_sha_drift = json.loads(json.dumps(evidence))
    wheel_sha_drift["wheel_files"][0]["sha256"] = "e" * 64
    drifted_values.append(wheel_sha_drift)
    supplement_receipt_drift = json.loads(json.dumps(evidence))
    supplement_receipt_drift["external_supplements"][0]["receipt_sha256"] = "f" * 64
    drifted_values.append(supplement_receipt_drift)
    extra_row = json.loads(json.dumps(evidence))
    extra_row["wheel_files"].append(dict(extra_row["wheel_files"][0]))
    drifted_values.append(extra_row)
    duplicate_removed = json.loads(json.dumps(evidence))
    duplicate_removed["total_sha256"][0] = "0" * 64
    drifted_values.append(duplicate_removed)

    for declared in drifted_values:
        with pytest.raises(materials.MaterialError) as exc_info:
            materials._require_exact_license_evidence(
                declared,
                evidence,
                component_license_file_count=104,
            )
        assert exc_info.value.code == "WHEELHOUSE_LICENSE_EVIDENCE_INVALID"


@pytest.fixture()
def release_fixture(tmp_path: Path, monkeypatch) -> dict:
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(materials, "EXPECTED_V2_PACKAGE_COUNT", 1)
    monkeypatch.setattr(materials, "EXPECTED_WHEEL_LICENSE_FILES", 1)
    monkeypatch.setattr(materials, "EXPECTED_EXTERNAL_LICENSE_SUPPLEMENTS", 0)
    monkeypatch.setattr(materials, "EXPECTED_TOTAL_LICENSE_FILES", 1)
    generation_id = "testgen-20260808"
    monkeypatch.setattr(builder, "TRUSTED_RUNTIME_TESTLIKE_MEMBERS", frozenset())
    license_path = _write(evidence / "QWEN-LICENSE.txt", "Fixture Apache-2.0 license.\n")
    license_sha = _sha(license_path)
    monkeypatch.setattr(builder, "TRUSTED_QWEN_LICENSE_SHA256", license_sha)

    source_file_map = {"LICENSE": (license_path.stat().st_size, license_sha)}
    monkeypatch.setattr(builder, "TRUSTED_QWEN_SOURCE_FILES", source_file_map)
    source_receipt = _write(
        evidence / "trusted-qwen-source.json",
        json.dumps(
            {
                "schema": "mentorflow.pinned-source-receipt.v1",
                "repository": builder.TRUSTED_QWEN_SOURCE_REPOSITORY,
                "commit": builder.TRUSTED_QWEN_SOURCE_REVISION,
                "files": [
                    {"path": "LICENSE", "bytes": license_path.stat().st_size, "sha256": license_sha}
                ],
            },
            sort_keys=True,
        )
        + "\n",
    )

    model_root = tmp_path / "model"
    model_contents = {
        "config.json": b"{}\n",
        "generation_config.json": b"{}\n",
        "merges.txt": b"a b\n",
        "model.safetensors": b"tiny-main-model",
        "preprocessor_config.json": b"{}\n",
        "tokenizer_config.json": b"{}\n",
        "vocab.json": b"{}\n",
        "speech_tokenizer/config.json": b"{}\n",
        "speech_tokenizer/configuration.json": b"{}\n",
        "speech_tokenizer/model.safetensors": b"tiny-tokenizer",
        "speech_tokenizer/preprocessor_config.json": b"{}\n",
    }
    for relative, content in model_contents.items():
        _write(model_root / Path(*relative.split("/")), content)
    model_files = {
        relative: (len(content), hashlib.sha256(content).hexdigest())
        for relative, content in model_contents.items()
    }
    monkeypatch.setattr(builder, "TRUSTED_MODEL_FILES", model_files)
    model_tree = builder.calculate_allowlist_tree_sha256(model_root, sorted(model_files))
    monkeypatch.setattr(builder, "TRUSTED_MODEL_TREE_SHA256", model_tree)

    portable_material = tmp_path / "portable"
    portable_root = portable_material / "builder-input"
    portable_files = {
        "LICENSE.txt": b"Fixture PSF license.\n",
        "python.exe": b"MZ-tiny-python",
        "python312.dll": b"MZ-tiny-dll",
        "python312.zip": b"tiny-stdlib",
        "python312._pth": b"python312.zip\n.\nLib/site-packages\nimport site\n",
    }
    for relative, content in portable_files.items():
        _write(portable_root / relative, content)
    portable_runtime = portable_material / "runtime"
    for relative, content in portable_files.items():
        _write(portable_runtime / relative, content)
    _write(portable_runtime / "python312._pth", b"python312.zip\n.\n")
    portable_receipts = portable_material / "receipts"
    portable_archive = _write(
        portable_receipts / "python-3.12.10-embed-amd64.zip",
        b"fixture archive",
    )
    for suffix in (".asc", ".crt", ".sig", ".sigstore", ".spdx.json"):
        _write(portable_receipts / f"{portable_archive.name}{suffix}", f"fixture {suffix}\n")
    portable_license = _write(portable_material / "PSF-LICENSE.txt", "Fixture PSF license.\n")
    portable_notice = _write(portable_material / "NOTICE.txt", "Fixture CPython notice.\n")
    portable_tree = builder.calculate_allowlist_tree_sha256(portable_root, sorted(portable_files))
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_TREE_SHA256", portable_tree)
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_LICENSE_SHA256", _sha(portable_license))
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_NOTICE_SHA256", _sha(portable_notice))
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_ARCHIVE_SHA256", _sha(portable_archive))
    authenticode_rows = [
        {
            "path": relative,
            "bytes": (portable_root / relative).stat().st_size,
            "sha256": _sha(portable_root / relative),
            "status": "Valid",
            "publisher": "Python Software Foundation",
            "publisher_subject": "CN=Fixture, O=Python Software Foundation, C=US",
            "certificate_thumbprint": "A" * 40,
        }
        for relative in ("python.exe", "python312.dll")
    ]
    portable_receipt_value = {
        "schema": builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
        "component": {
            "distribution": "python.org-embeddable",
            "name": "CPython Windows embeddable package (64-bit)",
            "platform": "win-x64",
            "version": "3.12.10",
            "abi": "cp312",
            "supplier": "Python Software Foundation",
            "license": "PSF-2.0",
        },
        "selection": {
            "checked_on": "2026-08-08",
            "current_3_12_security_release": "3.12.13",
            "current_3_12_security_release_binary_installers": False,
            "selected_binary_release": "3.12.10",
            "reason": "fixture pins the last binary release",
        },
        "upstream": {
            "release_page": "https://www.python.org/downloads/release/python-31210/",
            "latest_3_12_release_page": "https://www.python.org/downloads/release/python-31213/",
            "archive_url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
            "spdx_url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.spdx.json",
            "sigstore_bundle_url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.sigstore",
            "detached_signature_url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.sig",
            "signing_certificate_url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.crt",
            "openpgp_signature_url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.asc",
        },
        "archive": {
            "filename": "python-3.12.10-embed-amd64.zip",
            "bytes": portable_archive.stat().st_size,
            "sha256": _sha(portable_archive),
            "official_spdx_sha256": _sha(portable_archive),
            "official_release_page_md5": "8" * 32,
            "http_status": 200,
            "final_https_host": "www.python.org",
            "content_length": portable_archive.stat().st_size,
            "last_modified_utc": "2025-04-08T13:57:57Z",
            "etag": "fixture",
        },
        "verification": {
            "official_spdx_sha256_matches": True,
            "official_release_page_md5_matches": True,
            "sigstore_bundle_digest_matches": True,
            "sigstore_rekor_body_digest_matches": True,
            "detached_signature_matches_bundle": True,
            "detached_certificate_matches_bundle": True,
            "detached_ecdsa_signature_valid": True,
            "signer_certificate_san": "fixture@python.org",
            "full_sigstore_trust_root_and_transparency_log_verification": False,
            "openpgp_signature_verified": False,
            "verification_limit": "fixture limitation",
        },
        "extraction": {
            "zip_entries": len(portable_files),
            "unsafe_entries": 0,
            "official_tree": "runtime",
            "license_path": "runtime/LICENSE.txt",
            "license_bytes": portable_license.stat().st_size,
            "license_sha256": _sha(portable_license),
        },
        "builder_derivative": {
            "root": "builder-input",
            "changes_from_official_tree": 1,
            "changed_path": "python312._pth",
            "official_sha256": _sha(portable_runtime / "python312._pth"),
            "builder_sha256": _sha(portable_root / "python312._pth"),
            "change_summary": "fixture enables relative site-packages",
            "expected_tree_sha256": portable_tree,
        },
        "authenticode": {
            "schema": builder.PORTABLE_PYTHON_AUTHENTICODE_SCHEMA,
            "status": "passed",
            "verifier": "PowerShell 7 Get-AuthenticodeSignature",
            "files": authenticode_rows,
        },
    }
    portable_receipt = _write(
        portable_material / "SOURCE_RECEIPT.json",
        json.dumps(portable_receipt_value, sort_keys=True) + "\n",
    )
    portable_inventory_value = {
        "schema": builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
        "component": {
            "id": "python-3.12.10-embed-amd64",
            "version": "3.12.10",
            "distribution": "python.org-embeddable",
            "platform": "win-x64",
            "abi": "cp312",
        },
        "trees": {
            "official_runtime": _inventory_tree(portable_runtime, "runtime"),
            "builder_input": _inventory_tree(portable_root, "builder-input"),
            "source_artifacts": _inventory_tree(portable_receipts, "receipts"),
        },
        "evidence": {
            "source_receipt": _relative_pin(
                portable_material,
                portable_receipt,
                schema=builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
            ),
            "psf_license": _relative_pin(portable_material, portable_license),
            "notice": _relative_pin(portable_material, portable_notice),
        },
    }
    portable_inventory = _write(
        portable_material / "MATERIAL_INVENTORY.v2.json",
        json.dumps(portable_inventory_value, sort_keys=True) + "\n",
    )
    signature_by_name = {item["path"]: item for item in authenticode_rows}
    monkeypatch.setattr(
        materials,
        "_authenticode_signature",
        lambda path: {
            "status": signature_by_name[path.name]["status"],
            "publisher_subject": signature_by_name[path.name]["publisher_subject"],
            "certificate_thumbprint": signature_by_name[path.name]["certificate_thumbprint"],
        },
    )
    portable_component = {
        "schema": builder.PORTABLE_PYTHON_COMPONENT_SCHEMA,
        "id": "python-3.12.10-embed-amd64",
        "kind": "portable_python",
        "name": "CPython Windows embeddable package (64-bit)",
        "version": "3.12.10",
        "supplier": "Python Software Foundation",
        "root": "builder-input",
        "target_root": builder.TARGET_ROOTS["portable_python"],
        "include": sorted(portable_files),
        "expected_tree_sha256": portable_tree,
        "license_expression": "PSF-2.0",
        "license_files": [
            _relative_pin(portable_material, portable_license),
            _relative_pin(portable_material, portable_notice),
        ],
        "receipt": _relative_pin(portable_material, portable_receipt),
        "distribution": "python.org-embeddable",
        "homepage": "https://www.python.org/",
        "material_inventory": _relative_pin(
            portable_material,
            portable_inventory,
            schema=builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
        ),
    }
    portable_component_path = _write(
        portable_material / "BUILDER_COMPONENT.v2.fragment.json",
        json.dumps(portable_component, sort_keys=True) + "\n",
    )

    site_root = tmp_path / "wheelhouse-v2" / "builder-inputs" / "site-packages"
    wheel = _tiny_wheel(site_root, tmp_path / "wheelhouse-v2" / "wheels", license_path)
    monkeypatch.setattr(builder, "TRUSTED_QWEN_WHEEL_SHA256", _sha(wheel["wheel"]))
    purl = "pkg:pypi/qwen-tts@0.1.1"
    component = {
        "id": "qwen-tts",
        "kind": "dependency",
        "name": "qwen-tts",
        "version": "0.1.1",
        "supplier": "Qwen",
        "root": str(site_root),
        "target_root": builder.TARGET_ROOTS["dependency"],
        "include": wheel["include"],
        "expected_tree_sha256": builder.calculate_allowlist_tree_sha256(site_root, wheel["include"]),
        "license_expression": "Apache-2.0",
        "license_files": [_pin(wheel["license"])],
        "receipt": {},
        "homepage": "https://pypi.org/project/qwen-tts/0.1.1/",
        "purl": purl,
        "wheel": _pin(wheel["wheel"], sized=True),
    }
    receipt = {
        "schema": "mentorflow.python-wheel-receipt.v1",
        "name": "qwen-tts",
        "version": "0.1.1",
        "purl": purl,
        "wheel_filename": wheel["wheel"].name,
        "wheel_bytes": wheel["wheel"].stat().st_size,
        "wheel_sha256": _sha(wheel["wheel"]),
        "record_path": wheel["record"].relative_to(site_root).as_posix(),
        "record_sha256": _sha(wheel["record"]),
        "tree_sha256": component["expected_tree_sha256"],
        "license_sha256": [_sha(wheel["license"])],
        "source_url": f"https://files.pythonhosted.org/packages/{wheel['wheel'].name}",
    }
    receipt_path = _write(
        tmp_path / "wheelhouse-v2" / "builder-inputs" / "receipts" / "qwen-tts.json",
        json.dumps(receipt, sort_keys=True) + "\n",
    )
    component["receipt"] = _pin(receipt_path)
    lock = {
        "component_id": "qwen-tts",
        "name": "qwen-tts",
        "version": "0.1.1",
        "purl": purl,
        "tree_sha256": component["expected_tree_sha256"],
        "record_path": receipt["record_path"],
        "record_sha256": receipt["record_sha256"],
        "wheel_sha256": receipt["wheel_sha256"],
        "license_sha256": receipt["license_sha256"],
    }
    overlay_path = _write(
        tmp_path / "wheelhouse-v2" / "builder-inputs" / "receipts" / "overlay.json",
        json.dumps(_overlay_payload(), sort_keys=True) + "\n",
    )
    resolution = {
        "schema": materials.WHEELHOUSE_PLAN_SCHEMA,
        "profile": materials.PROFILE,
        "generation_id": generation_id,
        "blockers": [],
        "artifacts": [{"name": "qwen-tts", "version": "0.1.1"}],
        "removed_artifacts": [
            {"name": "flatbuffers", "version": "fixture", "reason": "unreachable"},
            {"name": "onnxruntime", "version": "fixture", "reason": "25hz-only"},
            {"name": "protobuf", "version": "fixture", "reason": "unreachable"},
            {"name": "sox", "version": "fixture", "reason": "25hz-only"},
        ],
    }
    resolution_path = _write(
        tmp_path / "wheelhouse-v2" / "resolution-plan.json",
        json.dumps(resolution, sort_keys=True) + "\n",
    )
    fragment = {
        "schema": materials.WHEELHOUSE_FRAGMENT_SCHEMA,
        "profile": materials.PROFILE,
        "generation_id": generation_id,
        "components": [component],
        "dependency_lock": [lock],
        "runner_source_overlay": _pin(overlay_path),
        "license_evidence": {
            "schema": materials.LICENSE_EVIDENCE_SCHEMA,
            "wheel_file_count": 1,
            "external_supplement_file_count": 0,
            "total_file_count": 1,
            "wheel_files": [
                {
                    "component_id": "qwen-tts",
                    "path": wheel["license"].relative_to(site_root).as_posix(),
                    "sha256": _sha(wheel["license"]),
                }
            ],
            "external_supplements": [],
            "total_sha256": [_sha(wheel["license"])],
        },
        "resolution_plan": _pin(resolution_path),
        "runtime_policy": materials.EXPECTED_RUNTIME_POLICY,
    }
    fragment_path = _write(
        tmp_path / "wheelhouse-v2" / "builder-inputs" / "builder-dependency-fragment.json",
        json.dumps(fragment, sort_keys=True) + "\n",
    )
    wheelhouse_root = tmp_path / "wheelhouse-v2"
    lock_payload_path = _write(
        wheelhouse_root / "builder-dependency-lock.json",
        json.dumps(
            {
                "schema": materials.WHEELHOUSE_LOCK_SCHEMA,
                "target": {"python": "3.12.10", "platform": "win_amd64"},
                "package_count": 1,
                "packages": [lock],
            },
            sort_keys=True,
        )
        + "\n",
    )
    site_files = materials._walk_plain_files(site_root)
    selected_files = len(site_files)
    selected_bytes = sum(path.stat().st_size for path in site_files.values())
    overlay_payload = json.loads(overlay_path.read_text(encoding="utf-8"))
    inventory_value = {
        "schema": materials.WHEELHOUSE_INVENTORY_SCHEMA,
        "profile": materials.PROFILE,
        "generation_id": generation_id,
        "package_count": 1,
        "selected_files": selected_files,
        "selected_bytes": selected_bytes,
        "license_files": 1,
        "wheel_license_files": 1,
        "external_license_supplement_files": 0,
        "license_evidence": fragment["license_evidence"],
        "excluded_test_files": 0,
        "excluded_test_bytes": 0,
        "runtime_required_testlike_members": [],
        "runtime_required_reason": "guarded official-wheel runtime import trace",
        "runtime_import_trace": {"contract_sha256": "", "source_trace_sha256": ""},
        "source_overlay": {
            "contract_sha256": _sha(overlay_path),
            "policy": overlay_payload["policy"],
            "upstream_wheel_sha256": overlay_payload["upstream_wheel"]["sha256"],
            "transforms": overlay_payload["transforms"],
        },
        "components": [
            {
                "name": "qwen-tts",
                "version": "0.1.1",
                "purl": purl,
                "license_expression": "Apache-2.0",
                "license_sha256": [_sha(wheel["license"])],
                "wheel_sha256": _sha(wheel["wheel"]),
                "selected_files": selected_files,
                "excluded_test_files": 0,
                "excluded_test_bytes": 0,
                "wheel_exclusions": [],
                "runtime_required_testlike": [],
                "runtime_import_trace_sha256": None,
            }
        ],
        "removed_packages": materials.EXPECTED_REMOVED_DISTRIBUTIONS,
        "resolution_plan_sha256": _sha(resolution_path),
        "tbb": {
            "status": "optional-not-shipped",
            "selected_numba_threading_layer": "workqueue",
        },
    }
    inventory_path = _write(
        wheelhouse_root / "THIRD_PARTY_BUILDER_INVENTORY.json",
        json.dumps(inventory_value, sort_keys=True) + "\n",
    )
    validation_core = {
        "schema": materials.WHEELHOUSE_VALIDATION_SCHEMA,
        "status": "passed",
        "profile": materials.PROFILE,
        "generation_id": generation_id,
        "fragment": _json_pin(fragment_path, materials.WHEELHOUSE_FRAGMENT_SCHEMA),
        "dependency_lock": _json_pin(
            lock_payload_path,
            materials.WHEELHOUSE_LOCK_SCHEMA,
            package_count=1,
        ),
        "inventory": _json_pin(
            inventory_path,
            materials.WHEELHOUSE_INVENTORY_SCHEMA,
            package_count=1,
        ),
        "resolution_plan": _json_pin(
            resolution_path,
            materials.WHEELHOUSE_PLAN_SCHEMA,
            profile=materials.PROFILE,
        ),
        "package_count": 1,
        "selected_files": selected_files,
        "selected_bytes": selected_bytes,
        "license_files": 1,
        "wheel_license_files": 1,
        "external_license_supplement_files": 0,
        "license_evidence": fragment["license_evidence"],
        "excluded_test_files": 0,
        "excluded_test_bytes": 0,
        "runtime_required_testlike_members": [],
        "runtime_import_trace": {"contract_sha256": "", "source_trace_sha256": ""},
        "source_overlay": {
            "path": str(overlay_path),
            "sha256": _sha(overlay_path),
            "policy": overlay_payload["policy"],
            "upstream_wheel_sha256": overlay_payload["upstream_wheel"]["sha256"],
            "transforms": overlay_payload["transforms"],
        },
        "site_packages": materials._site_tree_stats(site_root),
        "removed_packages": materials.EXPECTED_REMOVED_DISTRIBUTIONS,
        "numba_threading_layer": "workqueue",
        "tbb": "optional-not-shipped",
    }
    core_validation_path = _write(
        wheelhouse_root / "evidence" / "builder-dependency-validation.core.json",
        json.dumps(validation_core, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    validation_core_sha256 = _sha(core_validation_path)

    runner_root = tmp_path / "runner"
    runner = _write(
        runner_root / "qwen_base_clone_server.py",
        "\n".join(
            [
                "import os",
                'ROOT = os.environ.get("MENTORFLOW_VOICE_RUNTIME_ROOT")',
                'HOST = os.environ.get("MENTORFLOW_VOICE_HOST")',
                'PORT = os.environ.get("MENTORFLOW_VOICE_PORT")',
                'DEVICE = os.environ.get("MENTORFLOW_VOICE_DEVICE")',
                'MIN_DRIVER = os.environ.get("MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER")',
                "",
            ]
        ),
    )
    runner_license = _write(
        evidence / "MENTORFLOW-RUNTIME-LICENSE.txt",
        "Fixture MentorFlow runtime release license.\n",
    )
    runner_notice = _write(evidence / "runner" / "NOTICE.txt", "Fixture runner notice.\n")
    runner_tree = builder.calculate_allowlist_tree_sha256(
        runner_root,
        ["qwen_base_clone_server.py"],
    )
    runner_receipt = _write(
        evidence / "runner-receipt.json",
        json.dumps(
            {
                "schema": builder.RUNNER_SOURCE_RECEIPT_SCHEMA,
                "component": {
                    "id": "mentorflow-qwen-runner",
                    "name": "MentorFlow Qwen Base Clone runner",
                    "version": "1.0.0",
                    "supplier": "MentorFlow",
                    "license_expression": "MIT",
                },
                "source": {
                    "path": "qwen_base_clone_server.py",
                    "bytes": runner.stat().st_size,
                    "sha256": _sha(runner),
                    "tree_sha256": runner_tree,
                },
                "license": {
                    "path": runner_license.name,
                    "bytes": runner_license.stat().st_size,
                    "sha256": _sha(runner_license),
                },
                "notice": {
                    "path": runner_notice.name,
                    "bytes": runner_notice.stat().st_size,
                    "sha256": _sha(runner_notice),
                },
            },
            sort_keys=True,
        )
        + "\n",
    )

    smoke_root = wheelhouse_root / "portable-import-smoke"
    pe_binary = _write(smoke_root / "runtime" / "python" / "python.exe", b"MZ-fixture-smoke-python")
    pe_value = {
        "schema": materials.PORTABLE_PE_SCHEMA,
        "status": "passed",
        "profile": materials.PROFILE,
        "generation_id": generation_id,
        "scope": "all EXE/DLL/PYD files in the copied portable runtime tree",
        "binary_count": 1,
        "dependency_edge_count": 0,
        "unresolved": [],
        "windows_system_boundary": [],
        "nvidia_driver_boundary": [],
        "tbb": {
            "status": "optional-not-shipped",
            "selected_numba_threading_layer": "workqueue",
            "edges": [],
        },
        "binaries": [
            {
                "path": "runtime/python/python.exe",
                "bytes": pe_binary.stat().st_size,
                "sha256": _sha(pe_binary),
                "dependencies": [],
            }
        ],
    }
    pe_path = _write(
        smoke_root / "evidence" / "portable-pe-dependency-audit.json",
        json.dumps(pe_value, sort_keys=True) + "\n",
    )
    probe_path = _write(smoke_root / "evidence" / "probe.py", "print('fixture')\n")
    stdout_path = _write(smoke_root / "evidence" / "stdout.txt", "fixture passed\n")
    stderr_path = _write(smoke_root / "evidence" / "stderr.txt", "")
    smoke_relative_paths = {
        *(f"runtime/python/{relative}" for relative in portable_files),
        *(
            f"runtime/python/Lib/site-packages/{path.relative_to(site_root).as_posix()}"
            for path in site_root.rglob("*")
            if path.is_file()
        ),
        "runtime/runner/qwen_base_clone_server.py",
        "runtime/models/qwen3-tts-12hz-0.6b-base/config.json",
        "runtime/models/qwen3-tts-12hz-0.6b-base/speech_tokenizer/config.json",
        "metadata/source-overlays/qwen-12hz-only.json",
        "metadata/dependency-lock.json",
        "evidence/builder-dependency-lock.json",
    }
    smoke_path_budget = builder.calculate_install_path_budget(smoke_relative_paths)
    smoke_path_acceptance = builder.validate_install_root_budget(
        smoke_root,
        smoke_path_budget,
        relative_paths=smoke_relative_paths,
    )
    portable_product_value = {
        "schema": materials.PORTABLE_PRODUCT_ENTRY_SCHEMA,
        "status": "passed",
        "profile": materials.PROFILE,
        "generation_id": generation_id,
        "scope": "fresh copied E:-only portable Python dependency and production-runner import",
        "not_proven": ["model execution"],
        "safety": {
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
        },
        "timing": {"started_unix_ns": 1, "finished_unix_ns": 2, "duration_seconds": 0.1},
        "inputs": {
            "python_source_receipt": _pin(portable_receipt),
            "python_component_fragment": {
                **_pin(portable_component_path),
                "tree_sha256": portable_tree,
            },
            "dependency_lock": {**_pin(lock_payload_path), "package_count": 1},
            "builder_validation": {
                "path": str(core_validation_path),
                "sha256": validation_core_sha256,
                "selected_files": selected_files,
                "excluded_test_files": 0,
                "excluded_test_bytes": 0,
            },
            "builder_fragment": _pin(fragment_path),
            "builder_inventory": _pin(inventory_path),
            "resolution_plan": _pin(resolution_path),
            "runner": {
                "source_path": str(runner),
                "copied_path": str(runner),
                "sha256": _sha(runner),
            },
            "source_overlay": {
                "source_path": str(overlay_path),
                "copied_path": str(overlay_path),
                "sha256": _sha(overlay_path),
            },
            "model_identity_configs": {
                relative: {
                    "source_sha256": _sha(model_root / Path(*relative.split("/"))),
                    "copied_sha256": _sha(model_root / Path(*relative.split("/"))),
                }
                for relative in ("config.json", "speech_tokenizer/config.json")
            },
        },
        "staging": {
            "root": str(smoke_root),
            "path_budget": smoke_path_budget,
            "install_root_path_acceptance": smoke_path_acceptance,
            "free_bytes_before": 1,
            "required_copy_bytes": selected_bytes,
            "python": str(smoke_root / "runtime" / "python"),
            "python_executable": str(pe_binary),
            "python_executable_sha256": _sha(pe_binary),
            "site_packages": str(smoke_root / "runtime" / "python" / "Lib" / "site-packages"),
            "dependency_files": selected_files,
            "dependency_bytes": selected_bytes,
        },
        "probe": {
            "path": str(probe_path),
            "sha256": _sha(probe_path),
            "command": [str(probe_path)],
            "cwd": str(smoke_root),
            "environment": {},
            "returncode": 0,
            "stdout": _pin(stdout_path),
            "stderr": _pin(stderr_path),
            "result": {
                "status": "passed",
                "package_count": 1,
                "product_entry": materials.EXPECTED_RUNTIME_POLICY["product_entry"],
                "guarded_attempts": {
                    "bind": ["('::1', 0)"],
                    "connect": [],
                    "listen": [],
                    "subprocess": [],
                    "model_load": [],
                },
                "numba": {
                    "configured": "workqueue",
                    "active_config": "workqueue",
                    "tbbpool_loaded": False,
                    "tbb": "optional-not-shipped",
                },
            },
        },
        "native_host_injection": {"observers": []},
        "pe_dependency_audit": {
            "path": str(pe_path),
            "sha256": _sha(pe_path),
            "status": "passed",
        },
    }
    portable_product_path = _write(
        wheelhouse_root / "evidence" / "portable-python-import-smoke.json",
        json.dumps(portable_product_value, sort_keys=True) + "\n",
    )
    validation = {
        **validation_core,
        "pre_product_entry_validation_sha256": validation_core_sha256,
        "pe_dependency_audit": _json_pin(
            pe_path,
            materials.PORTABLE_PE_SCHEMA,
            status="passed",
            profile=materials.PROFILE,
        ),
        "portable_product_entry_receipt": _json_pin(
            portable_product_path,
            materials.PORTABLE_PRODUCT_ENTRY_SCHEMA,
            status="passed",
            profile=materials.PROFILE,
        ),
    }
    validation_path = _write(
        wheelhouse_root / "evidence" / "builder-dependency-validation.json",
        json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )

    private = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    private_path = _write(
        evidence / "test-private.pem",
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    public_path = _write(
        evidence / "test-public.pem",
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )
    output_root = tmp_path / "outputs"
    argv = [
        "--wheelhouse-fragment", str(fragment_path),
        "--wheelhouse-validation", str(validation_path),
        "--portable-component", str(portable_component_path),
        "--model-root", str(model_root),
        "--model-license", str(license_path),
        "--trusted-qwen-source-receipt", str(source_receipt),
        "--runner-root", str(runner_root),
        "--runner-receipt", str(runner_receipt),
        "--runner-license", str(runner_license),
        "--runner-notice", str(runner_notice),
        "--runner-license-expression", "MIT",
        "--output-root", str(output_root),
        "--created-at-utc", "2026-08-08T00:00:00Z",
        "--signing-key-id", "fixture-release-key",
        "--signing-private-key", str(private_path),
        "--trusted-public-key", str(public_path),
    ]
    return {
        "args": materials._parser().parse_args(argv),
        "argv": argv,
        "output_root": output_root,
        "model_root": model_root,
        "fragment_path": fragment_path,
        "core_validation_path": core_validation_path,
        "validation_path": validation_path,
        "portable_product_path": portable_product_path,
        "portable_receipt": portable_receipt,
        "portable_component_path": portable_component_path,
        "private_path": private_path,
        "public_path": public_path,
        "runner": runner,
    }


def test_prepare_and_verify_are_deterministic_and_builder_compatible(release_fixture):
    first = materials.prepare_release_materials(release_fixture["args"])
    second = materials.prepare_release_materials(release_fixture["args"])
    verify_args = materials._parser().parse_args([*release_fixture["argv"], "--verify-only"])
    verified = materials.prepare_release_materials(verify_args)

    assert first == second
    assert verified["status"] == "verified"
    assert first["model_receipt"]["files"] == 11
    assert first["generates_keys"] is False
    assert first["starts_service"] is False
    spec_path = Path(first["build_spec"]["path"])
    assert builder.plan_from_spec(spec_path)["status"] == "ready"
    assert release_fixture["private_path"].is_file()
    assert release_fixture["public_path"].is_file()


def test_model_extra_file_fails_before_spec_is_emitted(release_fixture):
    _write(release_fixture["model_root"] / "unexpected.bin", b"extra")
    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "MODEL_FILE_SET_MISMATCH"
    assert not (release_fixture["output_root"] / "qwen3-tts-runtime-build-spec.v1.json").exists()


def test_model_hardlink_is_rejected(release_fixture, tmp_path):
    target = release_fixture["model_root"] / "model.safetensors"
    original = target.read_bytes()
    outside = tmp_path / "outside-model.bin"
    outside.write_bytes(original)
    target.unlink()
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "HARDLINK_FORBIDDEN"


def test_v1_or_non_12hz_wheelhouse_is_rejected(release_fixture):
    payload = json.loads(release_fixture["fragment_path"].read_text(encoding="utf-8"))
    payload["schema"] = "mentorflow.qwen-runtime-builder-dependency-fragment.v1"
    release_fixture["fragment_path"].write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "WHEELHOUSE_PROFILE_REQUIRED"


def _rewrite_final_validation(path: Path, payload: dict) -> None:
    core = {field: payload[field] for field in materials.VALIDATION_CORE_FIELDS}
    payload["pre_product_entry_validation_sha256"] = hashlib.sha256(
        materials._json_bytes(core)
    ).hexdigest()
    path.write_bytes(materials._json_bytes(payload))


def test_skeletal_v2_validation_is_rejected(release_fixture):
    path = release_fixture["validation_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("portable_product_entry_receipt")
    path.write_bytes(materials._json_bytes(payload))

    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "WHEELHOUSE_VALIDATION_INVALID"


def test_validation_fragment_raw_sha_must_match_even_with_rehashed_core(release_fixture):
    path = release_fixture["validation_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fragment"]["sha256"] = "0" * 64
    _rewrite_final_validation(path, payload)

    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "WHEELHOUSE_VALIDATION_INVALID"


def test_validation_selected_counts_must_close_over_site_tree(release_fixture):
    path = release_fixture["validation_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["selected_files"] += 1
    _rewrite_final_validation(path, payload)

    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "WHEELHOUSE_VALIDATION_INVALID"


def test_fake_passed_portable_product_entry_is_rejected(release_fixture):
    product_path = release_fixture["portable_product_path"]
    product = json.loads(product_path.read_text(encoding="utf-8"))
    product["probe"]["result"].pop("product_entry")
    product_path.write_text(json.dumps(product, sort_keys=True) + "\n", encoding="utf-8")
    validation_path = release_fixture["validation_path"]
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["portable_product_entry_receipt"] = _json_pin(
        product_path,
        materials.PORTABLE_PRODUCT_ENTRY_SCHEMA,
        status="passed",
        profile=materials.PROFILE,
    )
    validation_path.write_bytes(materials._json_bytes(validation))

    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "PORTABLE_PRODUCT_ENTRY_INVALID"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "NotSigned"),
        ("certificate_thumbprint", "B" * 40),
    ],
)
def test_live_portable_authenticode_must_match_pinned_receipt(
    release_fixture, monkeypatch, field: str, value: str
):
    original = materials._authenticode_signature

    def drift(path: Path):
        result = dict(original(path))
        result[field] = value
        return result

    monkeypatch.setattr(materials, "_authenticode_signature", drift)
    with pytest.raises(materials.MaterialError) as caught:
        materials.prepare_release_materials(release_fixture["args"])
    assert caught.value.code == "PORTABLE_PYTHON_AUTHENTICODE_INVALID"
