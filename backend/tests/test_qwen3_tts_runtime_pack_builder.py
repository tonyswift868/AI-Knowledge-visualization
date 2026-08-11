from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import teacher_voice_pack as voice_pack
from scripts import build_qwen3_tts_runtime_pack as builder


def _write(path: Path, content: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(content)
    return path


def _pinned(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _pinned_binary(path: Path) -> dict[str, str | int]:
    return {**_pinned(path), "bytes": path.stat().st_size}


def _record_token(content: bytes) -> str:
    digest = hashlib.sha256(content).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _make_tiny_wheel(
    root: Path,
    *,
    name: str,
    version: str,
    package_dir: str,
    dist_info: str,
    license_text: str | None,
    project_urls: tuple[str, ...] = (),
    extra_files: dict[str, bytes | str] | None = None,
) -> dict:
    package_path = _write(root / package_dir / "__init__.py", f"__version__ = {version!r}\n")
    metadata_lines = [
        "Metadata-Version: 2.4",
        f"Name: {name}",
        f"Version: {version}",
        "License-Expression: Apache-2.0",
        *(f"Project-URL: {value}" for value in project_urls),
        "",
    ]
    metadata_path = _write(
        root / dist_info / "METADATA",
        "\n".join(metadata_lines),
    )
    license_path = (
        _write(root / dist_info / "licenses" / "LICENSE", license_text)
        if license_text is not None
        else None
    )
    record_path = root / dist_info / "RECORD"
    record_rows = []
    wheel_members = [package_path, metadata_path]
    if license_path is not None:
        wheel_members.append(license_path)
    for relative, content in sorted((extra_files or {}).items()):
        wheel_members.append(_write(root / Path(*relative.split("/")), content))
    for path in wheel_members:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        record_rows.append(f"{relative},{_record_token(content)},{len(content)}")
    record_rows.append(f"{record_path.relative_to(root).as_posix()},,")
    _write(record_path, "\n".join(record_rows) + "\n")
    wheel_path = root.parent / "wheelhouse" / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    wheel_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in (*wheel_members, record_path):
            archive.write(path, path.relative_to(root).as_posix())
    return {
        "wheel": wheel_path,
        "license": license_path,
        "metadata": metadata_path,
        "record": record_path,
        "include": [package_dir, dist_info],
    }


def _add_supplement_dependency(
    build_fixture: dict,
    tmp_path: Path,
    *,
    with_excluded_test: bool = False,
) -> tuple[dict, dict]:
    spec = copy.deepcopy(build_fixture["spec"])
    evidence = tmp_path / "supplement-evidence"
    site_root = Path(build_fixture["site_root"])
    name = "missing-license"
    version = "1.2.3"
    component_id = "missing-license"
    repository = "https://github.com/fixture-owner/missing-license"
    project_urls = (f"Source, {repository}",)
    wheel_info = _make_tiny_wheel(
        site_root,
        name=name,
        version=version,
        package_dir="missing_license",
        dist_info="missing_license-1.2.3.dist-info",
        license_text=None,
        project_urls=project_urls,
        extra_files=(
            {"missing_license/tests/test_fixture.py": "raise RuntimeError('must not ship')\n"}
            if with_excluded_test
            else None
        ),
    )
    external_license = _write(evidence / "LICENSE", "Fixture external Apache-2.0 license.\n")
    license_sha = hashlib.sha256(external_license.read_bytes()).hexdigest()
    wheel_sha = hashlib.sha256(wheel_info["wheel"].read_bytes()).hexdigest()
    metadata_sha = hashlib.sha256(wheel_info["metadata"].read_bytes()).hexdigest()
    purl = "pkg:pypi/missing-license@1.2.3"
    commit = "1" * 40
    supplement_payload = {
        "schema": builder.LICENSE_SUPPLEMENT_SCHEMA,
        "package": {
            "name": name,
            "version": version,
            "purl": purl,
            "wheel_filename": wheel_info["wheel"].name,
            "wheel_sha256": wheel_sha,
            "metadata_sha256": metadata_sha,
        },
        "pypi_metadata_binding": {
            "project_urls": list(project_urls),
            "repository": repository,
        },
        "upstream_release": {
            "repository": repository,
            "tag": "v1.2.3",
            "tag_api_url": "https://api.github.com/repos/fixture-owner/missing-license/commits/v1.2.3",
            "resolved_commit": commit,
            "commit_url": f"https://github.com/fixture-owner/missing-license/commit/{commit}",
        },
        "license_file": {
            "repository_path": "LICENSE",
            "immutable_url": f"https://raw.githubusercontent.com/fixture-owner/missing-license/{commit}/LICENSE",
            "bytes": external_license.stat().st_size,
            "sha256": license_sha,
            "saved_relative_path": "license-supplements/missing-license==1.2.3/LICENSE",
        },
    }
    supplement_receipt = _write(
        evidence / "license-supplement-receipt.json",
        json.dumps(supplement_payload, sort_keys=True) + "\n",
    )
    wheel_receipt = evidence / "wheel-receipt.json"
    component_include = (
        ["missing_license/__init__.py", "missing_license-1.2.3.dist-info"]
        if with_excluded_test
        else wheel_info["include"]
    )
    test_path = site_root / "missing_license" / "tests" / "test_fixture.py"
    wheel_exclusions = (
        [
            {
                "path": test_path.relative_to(site_root).as_posix(),
                "bytes": test_path.stat().st_size,
                "sha256": hashlib.sha256(test_path.read_bytes()).hexdigest(),
                "reason": "non-runtime-test",
            }
        ]
        if with_excluded_test
        else []
    )
    component = _component(
        component_id=component_id,
        kind="dependency",
        name=name,
        version=version,
        root=site_root,
        include=component_include,
        license_path=external_license,
        receipt_path=_write(wheel_receipt, '{"schema":"placeholder"}\n'),
        purl=purl,
        wheel=_pinned_binary(wheel_info["wheel"]),
        license_expression="Apache-2.0",
        license_supplement=_pinned(supplement_receipt),
        **({"wheel_exclusions": wheel_exclusions} if wheel_exclusions else {}),
    )
    record_relative = wheel_info["record"].relative_to(site_root).as_posix()
    wheel_receipt_payload = {
        "schema": "mentorflow.python-wheel-receipt.v1",
        "name": name,
        "version": version,
        "purl": purl,
        "wheel_filename": wheel_info["wheel"].name,
        "wheel_bytes": wheel_info["wheel"].stat().st_size,
        "wheel_sha256": wheel_sha,
        "record_path": record_relative,
        "record_sha256": hashlib.sha256(wheel_info["record"].read_bytes()).hexdigest(),
        "tree_sha256": component["expected_tree_sha256"],
        "license_sha256": [license_sha],
        "source_url": f"https://files.pythonhosted.org/packages/{wheel_info['wheel'].name}",
    }
    if wheel_exclusions:
        wheel_receipt_payload["wheel_exclusions"] = wheel_exclusions
    _write(wheel_receipt, json.dumps(wheel_receipt_payload, sort_keys=True) + "\n")
    component["receipt"] = _pinned(wheel_receipt)
    spec["components"].append(component)
    lock = {
            "component_id": component_id,
            "name": name,
            "version": version,
            "purl": purl,
            "tree_sha256": component["expected_tree_sha256"],
            "record_path": record_relative,
            "record_sha256": wheel_receipt_payload["record_sha256"],
            "wheel_sha256": wheel_sha,
            "license_sha256": [license_sha],
        }
    if wheel_exclusions:
        lock["wheel_exclusions"] = wheel_exclusions
    spec["dependency_lock"].append(lock)
    return spec, {
        "component": component,
        "license": external_license,
        "supplement_receipt": supplement_receipt,
        "supplement_payload": supplement_payload,
        "wheel_exclusions": wheel_exclusions,
        "test_path": test_path if with_excluded_test else None,
    }


def _add_legal_inventory_dependency(
    build_fixture: dict,
    tmp_path: Path,
    *,
    declare_all: bool,
) -> tuple[dict, dict]:
    spec = copy.deepcopy(build_fixture["spec"])
    site_root = Path(build_fixture["site_root"])
    evidence = tmp_path / "legal-inventory-evidence"
    name = "legal-inventory"
    version = "1.0.0"
    wheel_info = _make_tiny_wheel(
        site_root,
        name=name,
        version=version,
        package_dir="legal_inventory",
        dist_info="legal_inventory-1.0.0.dist-info",
        license_text="Primary legal inventory license.\n",
        extra_files={
            "legal_inventory/ThirdPartyNotices.txt": "Third party notice fixture.\n",
            "legal_inventory/vendor/liblinear/COPYRIGHT": "Liblinear copyright fixture.\n",
        },
    )
    legal_paths = [
        wheel_info["license"],
        site_root / "legal_inventory" / "ThirdPartyNotices.txt",
        site_root / "legal_inventory" / "vendor" / "liblinear" / "COPYRIGHT",
    ]
    declared_paths = legal_paths if declare_all else legal_paths[:1]
    placeholder = _write(evidence / "wheel-receipt.json", '{"schema":"placeholder"}\n')
    purl = "pkg:pypi/legal-inventory@1.0.0"
    component = _component(
        component_id=name,
        kind="dependency",
        name=name,
        version=version,
        root=site_root,
        include=wheel_info["include"],
        license_path=declared_paths[0],
        receipt_path=placeholder,
        purl=purl,
        wheel=_pinned_binary(wheel_info["wheel"]),
        license_expression="Apache-2.0 AND LicenseRef-Bundled-Notices",
    )
    component["license_files"] = [_pinned(path) for path in declared_paths]
    license_hashes = sorted(
        {hashlib.sha256(path.read_bytes()).hexdigest() for path in declared_paths}
    )
    wheel_sha = hashlib.sha256(wheel_info["wheel"].read_bytes()).hexdigest()
    record_relative = wheel_info["record"].relative_to(site_root).as_posix()
    receipt_payload = {
        "schema": "mentorflow.python-wheel-receipt.v1",
        "name": name,
        "version": version,
        "purl": purl,
        "wheel_filename": wheel_info["wheel"].name,
        "wheel_bytes": wheel_info["wheel"].stat().st_size,
        "wheel_sha256": wheel_sha,
        "record_path": record_relative,
        "record_sha256": hashlib.sha256(wheel_info["record"].read_bytes()).hexdigest(),
        "tree_sha256": component["expected_tree_sha256"],
        "license_sha256": license_hashes,
        "source_url": f"https://files.pythonhosted.org/packages/{wheel_info['wheel'].name}",
    }
    _write(placeholder, json.dumps(receipt_payload, sort_keys=True) + "\n")
    component["receipt"] = _pinned(placeholder)
    spec["components"].append(component)
    spec["dependency_lock"].append(
        {
            "component_id": name,
            "name": name,
            "version": version,
            "purl": purl,
            "tree_sha256": component["expected_tree_sha256"],
            "record_path": record_relative,
            "record_sha256": receipt_payload["record_sha256"],
            "wheel_sha256": wheel_sha,
            "license_sha256": license_hashes,
        }
    )
    return spec, {
        "component": component,
        "legal_paths": legal_paths,
        "texts": [path.read_text(encoding="utf-8") for path in legal_paths],
    }


def _component(
    *,
    component_id: str,
    kind: str,
    name: str,
    version: str,
    root: Path,
    include: list[str],
    license_path: Path,
    receipt_path: Path,
    **extra,
) -> dict:
    value = {
        "id": component_id,
        "kind": kind,
        "name": name,
        "version": version,
        "supplier": "Fixture Supplier",
        "root": str(root),
        "target_root": builder.TARGET_ROOTS[kind],
        "include": include,
        "expected_tree_sha256": builder.calculate_allowlist_tree_sha256(root, include),
        "license_expression": "Apache-2.0",
        "license_files": [_pinned(license_path)],
        "receipt": _pinned(receipt_path),
        "homepage": "https://example.invalid/component",
    }
    value.update(extra)
    return value


@pytest.fixture()
def build_fixture(tmp_path: Path, monkeypatch) -> dict:
    evidence = tmp_path / "evidence"
    license_path = _write(evidence / "LICENSE.txt", "Fixture redistributable license.\n")
    monkeypatch.setattr(
        builder,
        "TRUSTED_QWEN_LICENSE_SHA256",
        hashlib.sha256(license_path.read_bytes()).hexdigest(),
    )

    trusted_source_receipt = _write(
        evidence / "trusted-qwen-source-receipt.json",
        json.dumps(
            {
                "schema": "mentorflow.pinned-source-receipt.v1",
                "repository": builder.TRUSTED_QWEN_SOURCE_REPOSITORY,
                "commit": builder.TRUSTED_QWEN_SOURCE_REVISION,
                "files": [
                    {"path": path, "bytes": size, "sha256": digest}
                    for path, (size, digest) in sorted(builder.TRUSTED_QWEN_SOURCE_FILES.items())
                ],
            },
            sort_keys=True,
        )
        + "\n",
    )

    portable_material = tmp_path / "portable-python"
    python_root = portable_material / "builder-input"
    portable_license = _write(portable_material / "PSF-LICENSE.txt", "Fixture PSF license.\n")
    portable_notice = _write(portable_material / "NOTICE.txt", "Fixture CPython notice.\n")
    _write(python_root / "LICENSE.txt", portable_license.read_bytes())
    _write(python_root / "python.exe", b"MZ-portable-python")
    _write(python_root / "python312.dll", b"MZ-python-dll")
    _write(python_root / "python312.zip", b"portable-stdlib")
    _write(python_root / "python312._pth", "python312.zip\n.\nLib/site-packages\nimport site\n")
    official_python_root = portable_material / "runtime"
    for path in python_root.iterdir():
        _write(official_python_root / path.name, path.read_bytes())
    _write(official_python_root / "python312._pth", "python312.zip\n.\n")
    source_artifacts_root = portable_material / "receipts"
    archive_path = _write(
        source_artifacts_root / "python-3.12.10-embed-amd64.zip",
        b"fixture portable Python archive",
    )
    for suffix in (".asc", ".crt", ".sig", ".sigstore", ".spdx.json"):
        _write(source_artifacts_root / f"{archive_path.name}{suffix}", f"fixture {suffix}\n")

    runner_root = tmp_path / "sources" / "runner"
    _write(
        runner_root / "runner.py",
        """import os
ROOT = os.environ.get("MENTORFLOW_VOICE_RUNTIME_ROOT")
HOST = os.environ.get("MENTORFLOW_VOICE_HOST")
PORT = os.environ.get("MENTORFLOW_VOICE_PORT")
DEVICE = os.environ.get("MENTORFLOW_VOICE_DEVICE")
MIN_DRIVER = os.environ.get("MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER")
""",
    )
    runner_license = _write(
        evidence / "MENTORFLOW-RUNTIME-LICENSE.txt",
        "Fixture MentorFlow runtime release license.\n",
    )
    runner_notice = _write(evidence / "runner" / "NOTICE.txt", "Fixture runner notice.\n")

    model_root = tmp_path / "sources" / "model"
    model_contents = {
        "config.json": b"{}\n",
        "generation_config.json": b'{"fixture":true}\n',
        "merges.txt": b"a b\n",
        "model.safetensors": b"fixture-main-model",
        "preprocessor_config.json": b"{}\n",
        "tokenizer_config.json": b"{}\n",
        "vocab.json": b"{}\n",
        "speech_tokenizer/config.json": b"{}\n",
        "speech_tokenizer/configuration.json": b"{}\n",
        "speech_tokenizer/model.safetensors": b"fixture-speech-tokenizer",
        "speech_tokenizer/preprocessor_config.json": b"{}\n",
    }
    for relative, content in model_contents.items():
        _write(model_root / Path(*relative.split("/")), content)
    model_include = [
        "config.json",
        "generation_config.json",
        "merges.txt",
        "model.safetensors",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
        "speech_tokenizer",
    ]
    trusted_model_files = {
        relative: (len(content), hashlib.sha256(content).hexdigest())
        for relative, content in model_contents.items()
    }
    trusted_model_tree = builder.calculate_allowlist_tree_sha256(model_root, model_include)
    monkeypatch.setattr(builder, "TRUSTED_MODEL_FILES", trusted_model_files)
    monkeypatch.setattr(builder, "TRUSTED_MODEL_TREE_SHA256", trusted_model_tree)
    model_receipt = _write(
        evidence / "model-receipt.json",
        json.dumps(
            {
                "schema": "mentorflow.trusted-huggingface-model-receipt.v1",
                "repository": builder.TRUSTED_MODEL_REPOSITORY,
                "revision": builder.TRUSTED_MODEL_REVISION,
                "license": "Apache-2.0",
                "tree_sha256": trusted_model_tree,
                "cache_metadata_excluded": True,
                "files": [
                    {"path": path, "bytes": size, "sha256": digest}
                    for path, (size, digest) in sorted(trusted_model_files.items())
                ],
            },
            sort_keys=True,
        )
        + "\n",
    )

    site_root = tmp_path / "sources" / "site-packages"
    qwen_wheel = _make_tiny_wheel(
        site_root,
        name="qwen-tts",
        version="0.1.1",
        package_dir="qwen_tts",
        dist_info="qwen_tts-0.1.1.dist-info",
        license_text="Qwen fixture license.\n",
    )
    torch_wheel = _make_tiny_wheel(
        site_root,
        name="torch",
        version="2.11.0+cu128",
        package_dir="torch",
        dist_info="torch-2.11.0+cu128.dist-info",
        license_text="Torch fixture license.\n",
    )

    python_include = [
        "LICENSE.txt",
        "python.exe",
        "python312.dll",
        "python312.zip",
        "python312._pth",
    ]
    python_tree = builder.calculate_allowlist_tree_sha256(python_root, python_include)
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_TREE_SHA256", python_tree)
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_LICENSE_SHA256", _sha(portable_license))
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_NOTICE_SHA256", _sha(portable_notice))
    monkeypatch.setattr(builder, "TRUSTED_PORTABLE_PYTHON_ARCHIVE_SHA256", _sha(archive_path))
    python_receipt_value = {
        "schema": builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
        "component": {
            "distribution": "python.org-embeddable",
            "name": "CPython Windows embeddable package (64-bit)",
            "platform": "win-x64",
            "version": builder.TRUSTED_PORTABLE_PYTHON_VERSION,
            "abi": "cp312",
            "supplier": "Python Software Foundation",
            "license": "PSF-2.0",
        },
        "selection": {
            "checked_on": "2026-08-08",
            "current_3_12_security_release": "3.12.13",
            "current_3_12_security_release_binary_installers": False,
            "selected_binary_release": builder.TRUSTED_PORTABLE_PYTHON_VERSION,
            "reason": "fixture pins the last binary CPython 3.12 release",
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
            "bytes": archive_path.stat().st_size,
            "sha256": _sha(archive_path),
            "official_spdx_sha256": _sha(archive_path),
            "official_release_page_md5": "8" * 32,
            "http_status": 200,
            "final_https_host": "www.python.org",
            "content_length": archive_path.stat().st_size,
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
            "zip_entries": len(python_include),
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
            "official_sha256": _sha(official_python_root / "python312._pth"),
            "builder_sha256": _sha(python_root / "python312._pth"),
            "change_summary": "fixture enables relative site-packages",
            "expected_tree_sha256": python_tree,
        },
        "authenticode": {
            "schema": builder.PORTABLE_PYTHON_AUTHENTICODE_SCHEMA,
            "status": "passed",
            "verifier": "PowerShell 7 Get-AuthenticodeSignature",
            "files": [
                {
                    "path": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": _sha(path),
                    "status": "Valid",
                    "publisher": "Python Software Foundation",
                    "publisher_subject": "CN=Fixture, O=Python Software Foundation, C=US",
                    "certificate_thumbprint": "A" * 40,
                }
                for path in (python_root / "python.exe", python_root / "python312.dll")
            ],
        },
    }
    python_receipt = _write(
        portable_material / "SOURCE_RECEIPT.json",
        json.dumps(python_receipt_value, sort_keys=True) + "\n",
    )
    python_inventory_value = {
        "schema": builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
        "component": {
            "id": "python-3.12.10-embed-amd64",
            "version": "3.12.10",
            "distribution": "python.org-embeddable",
            "platform": "win-x64",
            "abi": "cp312",
        },
        "trees": {
            "official_runtime": _inventory_tree(official_python_root, "runtime"),
            "builder_input": _inventory_tree(python_root, "builder-input"),
            "source_artifacts": _inventory_tree(source_artifacts_root, "receipts"),
        },
        "evidence": {
            "source_receipt": _relative_pin(
                portable_material,
                python_receipt,
                schema=builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
            ),
            "psf_license": _relative_pin(portable_material, portable_license),
            "notice": _relative_pin(portable_material, portable_notice),
        },
    }
    python_inventory = _write(
        portable_material / "MATERIAL_INVENTORY.v2.json",
        json.dumps(python_inventory_value, sort_keys=True) + "\n",
    )
    runner_file = runner_root / "runner.py"
    runner_tree = builder.calculate_allowlist_tree_sha256(runner_root, ["runner.py"])
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
                    "path": "runner.py",
                    "bytes": runner_file.stat().st_size,
                    "sha256": _sha(runner_file),
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

    components = [
        _component(
            component_id="python-3.12.10-embed-amd64",
            kind="portable_python",
            name="CPython Windows embeddable package (64-bit)",
            version="3.12.10",
            root=python_root,
            include=python_include,
            license_path=portable_license,
            receipt_path=python_receipt,
            distribution="python.org-embeddable",
            supplier="Python Software Foundation",
            license_expression="PSF-2.0",
        ),
        _component(
            component_id="mentorflow-qwen-runner",
            kind="runner",
            name="MentorFlow Qwen Base Clone runner",
            version="1.0.0",
            root=runner_root,
            include=["runner.py"],
            license_path=runner_license,
            receipt_path=runner_receipt,
            supplier="MentorFlow",
            license_expression="MIT",
        ),
        _component(
            component_id="qwen3-tts-0.6b-base",
            kind="model",
            name="Qwen3-TTS-12Hz-0.6B-Base",
            version=builder.TRUSTED_MODEL_REVISION,
            root=model_root,
            include=model_include,
            license_path=license_path,
            receipt_path=model_receipt,
            model={
                "repository": builder.TRUSTED_MODEL_REPOSITORY,
                "revision": builder.TRUSTED_MODEL_REVISION,
            },
        ),
    ]
    components[0]["license_files"] = [
        _pinned_binary(portable_license),
        _pinned_binary(portable_notice),
    ]
    components[0]["receipt"] = _pinned_binary(python_receipt)
    components[0]["schema"] = builder.PORTABLE_PYTHON_COMPONENT_SCHEMA
    components[0]["material_inventory"] = {
        **_pinned_binary(python_inventory),
        "schema": builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
    }
    components[1]["license_files"] = [_pinned(runner_license), _pinned(runner_notice)]

    dependency_lock = []
    dependency_inputs = (
        ("qwen-tts", "qwen-tts", "0.1.1", "pkg:pypi/qwen-tts@0.1.1", qwen_wheel, "Apache-2.0"),
        ("torch", "torch", "2.11.0+cu128", "pkg:pypi/torch@2.11.0%2Bcu128", torch_wheel, "BSD-3-Clause"),
    )
    for component_id, name, version, purl, wheel_info, license_expression in dependency_inputs:
        placeholder = _write(evidence / f"{component_id}-receipt.json", '{"schema":"placeholder"}\n')
        component = _component(
            component_id=component_id,
            kind="dependency",
            name=name,
            version=version,
            root=site_root,
            include=wheel_info["include"],
            license_path=wheel_info["license"],
            receipt_path=placeholder,
            purl=purl,
            wheel=_pinned_binary(wheel_info["wheel"]),
            license_expression=license_expression,
        )
        record_relative = wheel_info["record"].relative_to(site_root).as_posix()
        license_sha = hashlib.sha256(wheel_info["license"].read_bytes()).hexdigest()
        wheel_sha = hashlib.sha256(wheel_info["wheel"].read_bytes()).hexdigest()
        receipt_payload = {
            "schema": "mentorflow.python-wheel-receipt.v1",
            "name": name,
            "version": version,
            "purl": purl,
            "wheel_filename": wheel_info["wheel"].name,
            "wheel_bytes": wheel_info["wheel"].stat().st_size,
            "wheel_sha256": wheel_sha,
            "record_path": record_relative,
            "record_sha256": hashlib.sha256(wheel_info["record"].read_bytes()).hexdigest(),
            "tree_sha256": component["expected_tree_sha256"],
            "license_sha256": [license_sha],
            "source_url": f"https://files.pythonhosted.org/packages/{wheel_info['wheel'].name}",
        }
        _write(placeholder, json.dumps(receipt_payload, sort_keys=True) + "\n")
        component["receipt"] = _pinned(placeholder)
        components.append(component)
        dependency_lock.append(
            {
                "component_id": component_id,
                "name": name,
                "version": version,
                "purl": purl,
                "tree_sha256": component["expected_tree_sha256"],
                "record_path": record_relative,
                "record_sha256": receipt_payload["record_sha256"],
                "wheel_sha256": wheel_sha,
                "license_sha256": [license_sha],
            }
        )

    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    private_path = _write(
        evidence / "release-private.pem",
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    public_path = _write(
        evidence / "release-public.pem",
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    spec = {
        "schema": builder.SPEC_SCHEMA,
        "pack": {
            "id": "mentorflow-qwen3-tts-runtime",
            "version": "0.1.0",
            "platform": "win-x64",
            "architecture": "x86_64",
            "created_at_utc": "2026-08-07T12:00:00Z",
        },
        "trusted_qwen_source_receipt": _pinned(trusted_source_receipt),
        "components": components,
        "dependency_lock": dependency_lock,
        "runtime_gates": {
            "root_resolution": "pack-relative",
            "entrypoint": "runtime/runner/runner.py",
            "root_env": "MENTORFLOW_VOICE_RUNTIME_ROOT",
            "host_env": "MENTORFLOW_VOICE_HOST",
            "port_env": "MENTORFLOW_VOICE_PORT",
            "device_env": "MENTORFLOW_VOICE_DEVICE",
            "driver_minimum_env": "MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER",
            "default_host": "127.0.0.1",
            "default_port": 8892,
            "default_device": "cuda:0",
            "default_minimum_driver": "555.00",
            "driver_vendor": "nvidia",
            "on_driver_unsupported": "fail",
        },
        "signing": {
            "algorithm": "ed25519",
            "key_id": "fixture-release-key",
            "private_key": _pinned(private_path),
            "trusted_public_key": {
                **_pinned(public_path),
                "public_key_sha256": hashlib.sha256(public_raw).hexdigest(),
            },
        },
    }
    spec_path = tmp_path / "build-spec.json"

    def save(value: dict | None = None) -> Path:
        spec_path.write_text(
            json.dumps(value if value is not None else spec, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return spec_path

    return {
        "spec": spec,
        "spec_path": save(),
        "save": save,
        "public_key": private_key.public_key(),
        "public_raw": public_raw,
        "public_path": public_path,
        "public_sha256": hashlib.sha256(public_raw).hexdigest(),
        "model_root": model_root,
        "site_root": site_root,
        "model_receipt": model_receipt,
        "qwen_license": license_path,
        "python_root": python_root,
        "official_python_root": official_python_root,
        "python_inventory": python_inventory,
        "source_artifacts_root": source_artifacts_root,
        "python_receipt": python_receipt,
        "runner_license": runner_license,
        "runner_receipt": runner_receipt,
    }


def _expect_code(code: str, callable_) -> None:
    with pytest.raises(builder.BuildError) as caught:
        callable_()
    assert caught.value.code == code


@pytest.mark.parametrize("mutation", ["missing-schema", "extra-field", "duplicate-license"])
def test_portable_python_v2_component_contract_is_exact(build_fixture, mutation: str):
    spec = copy.deepcopy(build_fixture["spec"])
    component = next(item for item in spec["components"] if item["kind"] == "portable_python")
    if mutation == "missing-schema":
        component.pop("schema")
    elif mutation == "extra-field":
        component["unexpected"] = True
    else:
        component["license_files"].append(dict(component["license_files"][0]))
    path = build_fixture["save"](spec)
    _expect_code("PORTABLE_PYTHON_COMPONENT_INVALID", lambda: builder.plan_from_spec(path))


def test_portable_python_inventory_rejects_unallowlisted_builder_file(build_fixture):
    _write(build_fixture["python_root"] / "unexpected.bin", b"not in the component allowlist")
    _expect_code(
        "PORTABLE_PYTHON_INVENTORY_MISMATCH",
        lambda: builder.plan_from_spec(build_fixture["spec_path"]),
    )


def test_portable_python_inventory_requires_all_five_archive_sidecars(build_fixture):
    (build_fixture["source_artifacts_root"] / "python-3.12.10-embed-amd64.zip.asc").unlink()
    _expect_code(
        "PORTABLE_PYTHON_INVENTORY_MISMATCH",
        lambda: builder.plan_from_spec(build_fixture["spec_path"]),
    )


def test_portable_python_inventory_rejects_unknown_or_cyclic_field(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    component = next(item for item in spec["components"] if item["kind"] == "portable_python")
    inventory_path = build_fixture["python_inventory"]
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["component_fragment"] = {"path": "BUILDER_COMPONENT.v2.fragment.json"}
    inventory_path.write_text(json.dumps(inventory, sort_keys=True) + "\n", encoding="utf-8")
    component["material_inventory"] = {
        **_pinned_binary(inventory_path),
        "schema": builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
    }
    path = build_fixture["save"](spec)
    _expect_code("PORTABLE_PYTHON_INVENTORY_INVALID", lambda: builder.plan_from_spec(path))


def _build_pack(build_fixture: dict, output_root: Path) -> tuple[dict, Path]:
    plan = builder.plan_from_spec(build_fixture["spec_path"])
    result = builder.build_from_plan(plan, output_root)
    return result, Path(result["pack_root"])


@pytest.mark.parametrize(
    "relative",
    [
        "runtime/model:ads.bin",
        "C:/runtime/model.bin",
        "runtime/CON.txt",
        "runtime/lpt1.json",
        "runtime/COM¹.log",
        "runtime/name.",
        "runtime/name ",
        "runtime/control\x1f.json",
        "//server/share/file.bin",
        "runtime//file.bin",
        "runtime/<invalid>.json",
        " runtime/model.bin",
        "runtime/model?.bin",
        "runtime/model|pipe.bin",
        "runtime\\model.bin",
        "runtime/" + ("a" * 256),
    ],
)
def test_windows_relative_path_ambiguities_are_rejected(relative):
    with pytest.raises(builder.BuildError) as caught:
        builder._safe_relative(relative, field="fixture.path")
    assert caught.value.code in {
        "UNSAFE_PATH",
        "UNSAFE_WINDOWS_PATH",
        "WINDOWS_DEVICE_NAME",
    }


def test_default_plan_is_non_mutating_and_reports_exact_inputs(build_fixture, tmp_path, capsys):
    output = tmp_path / "must-not-exist"
    result = builder.main(["--spec", str(build_fixture["spec_path"])])
    assert result == 0
    body = json.loads(capsys.readouterr().out)
    assert body["mode"] == "plan"
    assert body["copies_files"] is False
    assert body["platform"] == "win-x64"
    assert body["file_count"] == 25
    assert body["path_budget"]["schema"] == "mentorflow.windows-install-path-budget.v1"
    assert body["path_budget"]["relative_path_count"] > body["file_count"]
    assert body["path_budget"]["max_relative_path_chars"] == len(
        body["path_budget"]["max_relative_path"]
    )
    assert not output.exists()


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "PORTABLE_PYTHON_RECEIPT_INVALID"),
        ("digest", "PORTABLE_PYTHON_AUTHENTICODE_INVALID"),
        ("status", "PORTABLE_PYTHON_AUTHENTICODE_INVALID"),
    ],
)
def test_portable_python_authenticode_receipt_is_exact_and_fail_closed(
    build_fixture, mutation: str, code: str
):
    spec = copy.deepcopy(build_fixture["spec"])
    component = next(item for item in spec["components"] if item["kind"] == "portable_python")
    receipt_path = build_fixture["python_receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        receipt.pop("authenticode")
    elif mutation == "digest":
        receipt["authenticode"]["files"][0]["sha256"] = "0" * 64
    else:
        receipt["authenticode"]["files"][0]["status"] = "NotSigned"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    component["receipt"] = _pinned_binary(receipt_path)
    inventory_path = Path(component["material_inventory"]["path"])
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["evidence"]["source_receipt"] = _relative_pin(
        inventory_path.parent,
        receipt_path,
        schema=builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
    )
    inventory_path.write_text(
        json.dumps(inventory, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    component["material_inventory"] = {
        **_pinned_binary(inventory_path),
        "schema": builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
    }
    path = build_fixture["save"](spec)
    _expect_code(code, lambda: builder.plan_from_spec(path))


def test_runner_cannot_reuse_qwen_model_license(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    component = next(item for item in spec["components"] if item["kind"] == "runner")
    runner_license = build_fixture["runner_license"]
    runner_license.write_bytes(build_fixture["qwen_license"].read_bytes())
    component["license_files"][0] = _pinned(runner_license)
    receipt_path = build_fixture["runner_receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["license"] = {
        "path": runner_license.name,
        "bytes": runner_license.stat().st_size,
        "sha256": _sha(runner_license),
    }
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    component["receipt"] = _pinned(receipt_path)
    path = build_fixture["save"](spec)
    _expect_code("RUNNER_LICENSE_INVALID", lambda: builder.plan_from_spec(path))


def test_runner_without_owner_resolved_license_expression_is_no_go(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    component = next(item for item in spec["components"] if item["kind"] == "runner")
    component["license_expression"] = "NOASSERTION"
    path = build_fixture["save"](spec)
    _expect_code("LICENSE_UNRESOLVED", lambda: builder.plan_from_spec(path))


def test_allowlist_path_escape_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    runner = next(item for item in spec["components"] if item["kind"] == "runner")
    runner["include"] = ["../escape.py"]
    path = build_fixture["save"](spec)
    _expect_code("UNSAFE_PATH", lambda: builder.plan_from_spec(path))


def test_reparse_point_is_rejected(build_fixture, monkeypatch):
    original = builder._is_reparse_point

    def fake_reparse(path: Path) -> bool:
        return path.name == "model.safetensors" or original(path)

    monkeypatch.setattr(builder, "_is_reparse_point", fake_reparse)
    _expect_code(
        "REPARSE_POINT_FORBIDDEN",
        lambda: builder.plan_from_spec(build_fixture["spec_path"]),
    )


def test_component_hash_mismatch_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    model = next(item for item in spec["components"] if item["kind"] == "model")
    model["expected_tree_sha256"] = "0" * 64
    path = build_fixture["save"](spec)
    _expect_code("COMPONENT_HASH_MISMATCH", lambda: builder.plan_from_spec(path))


def test_incomplete_model_is_rejected_even_with_recomputed_component_hash(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    model = next(item for item in spec["components"] if item["kind"] == "model")
    model["include"].remove("generation_config.json")
    model["expected_tree_sha256"] = builder.calculate_allowlist_tree_sha256(
        Path(model["root"]),
        model["include"],
    )
    path = build_fixture["save"](spec)
    _expect_code("MODEL_TRUST_SET_MISMATCH", lambda: builder.plan_from_spec(path))


def test_fake_model_receipt_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    receipt = json.loads(build_fixture["model_receipt"].read_text(encoding="utf-8"))
    receipt["repository"] = "Untrusted/Fake-Model"
    fake = _write(
        build_fixture["model_receipt"].with_name("fake-model-receipt.json"),
        json.dumps(receipt, sort_keys=True) + "\n",
    )
    model = next(item for item in spec["components"] if item["kind"] == "model")
    model["receipt"] = _pinned(fake)
    path = build_fixture["save"](spec)
    _expect_code("MODEL_RECEIPT_UNTRUSTED", lambda: builder.plan_from_spec(path))


def test_missing_license_evidence_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    model = next(item for item in spec["components"] if item["kind"] == "model")
    model["license_files"] = []
    path = build_fixture["save"](spec)
    _expect_code("LICENSE_EVIDENCE_MISSING", lambda: builder.plan_from_spec(path))


def test_missing_dependency_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    spec["dependency_lock"].append(
        {
            "component_id": "soundfile",
            "name": "soundfile",
            "version": "0.14.0",
            "purl": "pkg:pypi/soundfile@0.14.0",
            "tree_sha256": "0" * 64,
            "record_path": "soundfile-0.14.0.dist-info/RECORD",
            "record_sha256": "0" * 64,
            "wheel_sha256": "0" * 64,
            "license_sha256": ["0" * 64],
        }
    )
    path = build_fixture["save"](spec)
    _expect_code("DEPENDENCY_LOCK_MISMATCH", lambda: builder.plan_from_spec(path))


def test_wrong_dependency_lock_version_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    spec["dependency_lock"][0]["version"] = "9.9.9"
    path = build_fixture["save"](spec)
    _expect_code("DEPENDENCY_LOCK_MISMATCH", lambda: builder.plan_from_spec(path))


def test_fake_dependency_wheel_receipt_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    dependency = next(item for item in spec["components"] if item["id"] == "qwen-tts")
    receipt_path = Path(dependency["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["wheel_sha256"] = "0" * 64
    fake = _write(receipt_path.with_name("fake-qwen-wheel-receipt.json"), json.dumps(receipt, sort_keys=True) + "\n")
    dependency["receipt"] = _pinned(fake)
    path = build_fixture["save"](spec)
    _expect_code("DEPENDENCY_RECEIPT_MISMATCH", lambda: builder.plan_from_spec(path))


def test_dependency_license_supplement_is_bound_and_packaged(
    build_fixture,
    tmp_path,
):
    spec, fixture = _add_supplement_dependency(build_fixture, tmp_path)
    path = build_fixture["save"](spec)
    plan = builder.plan_from_spec(path)
    supplement_plan = next(item for item in plan["plans"] if item.component["id"] == "missing-license")
    assert supplement_plan.license_supplement_record is not None

    result = builder.build_from_plan(plan, tmp_path / "supplement-output")
    pack_root = Path(result["pack_root"])
    notice_index = json.loads(
        (pack_root / "THIRD_PARTY_NOTICES_INDEX.json").read_text(encoding="utf-8")
    )
    component_notice = next(
        item for item in notice_index["components"] if item["component_id"] == "missing-license"
    )
    supplement_notice = component_notice["license_supplement_receipt"]
    assert supplement_notice["commit"] == "1" * 40
    assert supplement_notice["license_sha256"] == hashlib.sha256(
        fixture["license"].read_bytes()
    ).hexdigest()
    assert (pack_root / Path(*supplement_notice["path"].split("/"))).read_bytes() == fixture[
        "supplement_receipt"
    ].read_bytes()
    copied_license = pack_root / Path(*component_notice["license_files"][0]["path"].split("/"))
    assert copied_license.read_bytes() == fixture["license"].read_bytes()
    sbom = json.loads((pack_root / "SBOM.spdx.json").read_text(encoding="utf-8"))
    package = next(item for item in sbom["packages"] if item["name"] == "missing-license")
    assert "License supplement provenance:" in package["comment"]
    manifest = json.loads((pack_root / "engine-manifest.json").read_text(encoding="utf-8"))
    manifest_paths = {item["path"] for item in manifest["files"]}
    assert supplement_notice["path"] in manifest_paths
    assert component_notice["license_files"][0]["path"] in manifest_paths


def test_explicit_test_member_exclusion_is_bound_signed_and_not_copied(
    build_fixture,
    tmp_path,
):
    spec, fixture = _add_supplement_dependency(
        build_fixture,
        tmp_path,
        with_excluded_test=True,
    )
    plan = builder.plan_from_spec(build_fixture["save"](spec))
    result = builder.build_from_plan(plan, tmp_path / "excluded-test-output")
    pack_root = Path(result["pack_root"])
    excluded_relative = fixture["wheel_exclusions"][0]["path"]
    assert not (
        pack_root / "runtime" / "python" / "Lib" / "site-packages" / Path(*excluded_relative.split("/"))
    ).exists()
    notice = json.loads((pack_root / "THIRD_PARTY_NOTICES_INDEX.json").read_text(encoding="utf-8"))
    component = next(item for item in notice["components"] if item["component_id"] == "missing-license")
    assert component["wheel_exclusions"]["entries"] == fixture["wheel_exclusions"]
    exclusion_inventory = pack_root / Path(*component["wheel_exclusions"]["path"].split("/"))
    assert exclusion_inventory.is_file()
    lock = json.loads((pack_root / "metadata" / "dependency-lock.json").read_text(encoding="utf-8"))
    locked = next(item for item in lock["packages"] if item["component_id"] == "missing-license")
    assert locked["wheel_exclusions"] == fixture["wheel_exclusions"]
    sbom = json.loads((pack_root / "SBOM.spdx.json").read_text(encoding="utf-8"))
    package = next(item for item in sbom["packages"] if item["name"] == "missing-license")
    assert excluded_relative in package["comment"]
    manifest = json.loads((pack_root / "engine-manifest.json").read_text(encoding="utf-8"))
    assert component["wheel_exclusions"]["path"] in {item["path"] for item in manifest["files"]}


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda exclusion: exclusion.__setitem__("path", "missing_license/runtime.py"),
            "WHEEL_EXCLUSION_NOT_ALLOWED",
        ),
        (
            lambda exclusion: exclusion.__setitem__(
                "path", "missing_license-1.2.3.dist-info/METADATA"
            ),
            "WHEEL_EXCLUSION_NOT_ALLOWED",
        ),
        (
            lambda exclusion: exclusion.__setitem__(
                "path", "missing_license-1.2.3.dist-info/licenses/LICENSE"
            ),
            "WHEEL_EXCLUSION_NOT_ALLOWED",
        ),
        (
            lambda exclusion: exclusion.__setitem__("sha256", "0" * 64),
            "DEPENDENCY_WHEEL_EXCLUSION_MISMATCH",
        ),
        (
            lambda exclusion: exclusion.__setitem__("bytes", exclusion["bytes"] + 1),
            "DEPENDENCY_WHEEL_EXCLUSION_MISMATCH",
        ),
        (
            lambda exclusion: exclusion.__setitem__("reason", "all-tests"),
            "WHEEL_EXCLUSION_NOT_ALLOWED",
        ),
    ],
    ids=[
        "production-source",
        "metadata",
        "license",
        "wrong-hash",
        "wrong-size",
        "overbroad-reason",
    ],
)
def test_wheel_exclusion_tampering_or_overbroad_scope_is_rejected(
    build_fixture,
    tmp_path,
    mutate,
    code,
):
    spec, fixture = _add_supplement_dependency(
        build_fixture,
        tmp_path,
        with_excluded_test=True,
    )
    exclusion = fixture["component"]["wheel_exclusions"][0]
    mutate(exclusion)
    dependency_receipt = Path(fixture["component"]["receipt"]["path"])
    receipt = json.loads(dependency_receipt.read_text(encoding="utf-8"))
    receipt["wheel_exclusions"] = fixture["component"]["wheel_exclusions"]
    _write(dependency_receipt, json.dumps(receipt, sort_keys=True) + "\n")
    fixture["component"]["receipt"] = _pinned(dependency_receipt)
    lock = next(item for item in spec["dependency_lock"] if item["component_id"] == "missing-license")
    lock["wheel_exclusions"] = fixture["component"]["wheel_exclusions"]
    path = build_fixture["save"](spec)
    _expect_code(code, lambda: builder.plan_from_spec(path))


def test_dependency_missing_in_wheel_license_without_supplement_is_rejected(
    build_fixture,
    tmp_path,
):
    spec, fixture = _add_supplement_dependency(build_fixture, tmp_path)
    fixture["component"].pop("license_supplement")
    path = build_fixture["save"](spec)
    _expect_code("DEPENDENCY_LICENSE_NOT_FROM_WHEEL", lambda: builder.plan_from_spec(path))


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("onnxruntime/ThirdPartyNotices.txt", True),
        ("sklearn/svm/src/liblinear/COPYRIGHT", True),
        ("example.dist-info/licenses/NOTICE.md", True),
        ("packaging/licenses/_spdx.py", False),
    ],
)
def test_license_like_member_recognizes_real_legal_files_only(relative: str, expected: bool):
    assert builder._is_license_like_member(Path(relative)) is expected


def test_dependency_omitted_third_party_notices_and_copyright_are_rejected(
    build_fixture,
    tmp_path,
):
    spec, _fixture = _add_legal_inventory_dependency(
        build_fixture,
        tmp_path,
        declare_all=False,
    )
    path = build_fixture["save"](spec)
    _expect_code(
        "DEPENDENCY_LICENSE_INVENTORY_MISMATCH",
        lambda: builder.plan_from_spec(path),
    )


def test_dependency_all_legal_files_flow_to_notices_manifest_and_sbom(
    build_fixture,
    tmp_path,
):
    spec, fixture = _add_legal_inventory_dependency(
        build_fixture,
        tmp_path,
        declare_all=True,
    )
    plan = builder.plan_from_spec(build_fixture["save"](spec))
    result = builder.build_from_plan(plan, tmp_path / "legal-inventory-output")
    pack_root = Path(result["pack_root"])
    notice_index = json.loads(
        (pack_root / "THIRD_PARTY_NOTICES_INDEX.json").read_text(encoding="utf-8")
    )
    component_notice = next(
        item
        for item in notice_index["components"]
        if item["component_id"] == "legal-inventory"
    )
    assert len(component_notice["license_files"]) == 3
    assert {
        item["sha256"] for item in component_notice["license_files"]
    } == {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture["legal_paths"]
    }
    aggregate = (pack_root / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    assert all(text.strip() in aggregate for text in fixture["texts"])

    manifest = json.loads((pack_root / "engine-manifest.json").read_text(encoding="utf-8"))
    manifest_paths = {item["path"] for item in manifest["files"]}
    assert {
        item["path"] for item in component_notice["license_files"]
    }.issubset(manifest_paths)
    sbom = json.loads((pack_root / "SBOM.spdx.json").read_text(encoding="utf-8"))
    package = next(item for item in sbom["packages"] if item["name"] == "legal-inventory")
    assert package["licenseDeclared"] == "Apache-2.0 AND LicenseRef-Bundled-Notices"
    legal_runtime_suffixes = {
        "runtime/python/Lib/site-packages/" + path.relative_to(build_fixture["site_root"]).as_posix()
        for path in fixture["legal_paths"]
    }
    sbom_paths = {item["fileName"].removeprefix("./") for item in sbom["files"]}
    assert legal_runtime_suffixes.issubset(sbom_paths)


def test_dependency_supplement_cannot_replace_in_wheel_license(
    build_fixture,
    tmp_path,
):
    spec, fixture = _add_supplement_dependency(build_fixture, tmp_path)
    qwen = next(item for item in spec["components"] if item["id"] == "qwen-tts")
    qwen["license_supplement"] = _pinned(fixture["supplement_receipt"])
    path = build_fixture["save"](spec)
    _expect_code("LICENSE_SUPPLEMENT_REPLACES_WHEEL_LICENSE", lambda: builder.plan_from_spec(path))


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda payload: payload["package"].__setitem__("wheel_sha256", "0" * 64),
            "LICENSE_SUPPLEMENT_PACKAGE_MISMATCH",
        ),
        (
            lambda payload: payload["package"].__setitem__("metadata_sha256", "0" * 64),
            "LICENSE_SUPPLEMENT_PACKAGE_MISMATCH",
        ),
        (
            lambda payload: payload["pypi_metadata_binding"].__setitem__(
                "repository", "https://github.com/attacker/wrong"
            ),
            "LICENSE_SUPPLEMENT_SOURCE_MISMATCH",
        ),
        (
            lambda payload: payload["upstream_release"].__setitem__("tag", "main"),
            "LICENSE_SUPPLEMENT_TAG_INVALID",
        ),
        (
            lambda payload: payload["upstream_release"].__setitem__("resolved_commit", "ABC"),
            "LICENSE_SUPPLEMENT_COMMIT_INVALID",
        ),
        (
            lambda payload: payload["license_file"].__setitem__(
                "immutable_url", "https://raw.githubusercontent.com/fixture-owner/missing-license/main/LICENSE"
            ),
            "LICENSE_SUPPLEMENT_URL_INVALID",
        ),
        (
            lambda payload: payload["license_file"].__setitem__(
                "immutable_url", "https://evil.invalid/fixture-owner/missing-license/LICENSE"
            ),
            "LICENSE_SUPPLEMENT_URL_INVALID",
        ),
        (
            lambda payload: payload["license_file"].__setitem__("sha256", "0" * 64),
            "LICENSE_SUPPLEMENT_HASH_MISMATCH",
        ),
        (
            lambda payload: payload.__setitem__("unknown", True),
            "LICENSE_SUPPLEMENT_SCHEMA_INVALID",
        ),
    ],
    ids=[
        "wrong-wheel-hash",
        "wrong-metadata-hash",
        "wrong-source-repository",
        "wrong-tag",
        "wrong-commit",
        "mutable-branch-url",
        "unknown-host",
        "wrong-license-hash",
        "unknown-field",
    ],
)
def test_dependency_license_supplement_tampering_is_rejected(
    build_fixture,
    tmp_path,
    mutation,
    code,
):
    spec, fixture = _add_supplement_dependency(build_fixture, tmp_path)
    payload = copy.deepcopy(fixture["supplement_payload"])
    mutation(payload)
    _write(fixture["supplement_receipt"], json.dumps(payload, sort_keys=True) + "\n")
    fixture["component"]["license_supplement"] = _pinned(fixture["supplement_receipt"])
    path = build_fixture["save"](spec)
    _expect_code(code, lambda: builder.plan_from_spec(path))


def test_dependency_rejects_deleted_code_even_with_resigned_tree_receipt_and_lock(
    build_fixture,
):
    spec = copy.deepcopy(build_fixture["spec"])
    dependency = next(item for item in spec["components"] if item["id"] == "qwen-tts")
    deleted = Path(dependency["root"]) / "qwen_tts" / "__init__.py"
    deleted.unlink()
    new_tree = builder.calculate_allowlist_tree_sha256(
        Path(dependency["root"]),
        dependency["include"],
    )
    dependency["expected_tree_sha256"] = new_tree

    old_receipt = Path(dependency["receipt"]["path"])
    receipt = json.loads(old_receipt.read_text(encoding="utf-8"))
    receipt["tree_sha256"] = new_tree
    resigned_receipt = _write(
        old_receipt.with_name("resigned-qwen-wheel-receipt.json"),
        json.dumps(receipt, sort_keys=True) + "\n",
    )
    dependency["receipt"] = _pinned(resigned_receipt)
    lock = next(item for item in spec["dependency_lock"] if item["component_id"] == "qwen-tts")
    lock["tree_sha256"] = new_tree

    path = build_fixture["save"](spec)
    _expect_code(
        "DEPENDENCY_WHEEL_CLOSURE_MISMATCH",
        lambda: builder.plan_from_spec(path),
    )


def _source_overlay_payload() -> dict:
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
    for index, (relative, input_sha256) in enumerate(sorted(builder.TRUSTED_QWEN_OVERLAY_INPUTS.items()), start=1):
        transforms.append(
            {
                "module": relative.removesuffix("/__init__.py").removesuffix(".py").replace("/", "."),
                "path": relative,
                "input_sha256": input_sha256,
                "output_sha256": str(index) * 64,
                "patch_sha256": str(index + 2) * 64,
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


@pytest.mark.parametrize("mutation", ["input-drift", "missing-transform", "extra-transform"])
def test_source_overlay_rejects_input_drift_and_non_exact_transform_set(tmp_path: Path, mutation: str):
    payload = _source_overlay_payload()
    if mutation == "input-drift":
        payload["transforms"][0]["input_sha256"] = "0" * 64
    elif mutation == "missing-transform":
        payload["transforms"].pop()
    else:
        payload["transforms"].append(copy.deepcopy(payload["transforms"][0]))
    receipt = _write(tmp_path / "overlay.json", json.dumps(payload, sort_keys=True) + "\n")
    _expect_code(
        "SOURCE_OVERLAY_INVALID",
        lambda: builder._read_source_overlay_contract(
            tmp_path,
            _pinned(receipt),
            component_id="mentorflow-qwen-runner",
        ),
    )


def test_runtime_testlike_rejects_unlisted_member():
    _expect_code(
        "RUNTIME_TESTLIKE_NOT_ALLOWED",
        lambda: builder._parse_runtime_testlike_entries(
            [
                {
                    "path": "torch/testing/not-runtime-required.py",
                    "bytes": 1,
                    "sha256": "0" * 64,
                    "reason": "runtime-import-trace",
                }
            ],
            component_id="torch",
        ),
    )


def test_runtime_testlike_rejects_missing_declaration(tmp_path: Path):
    root = tmp_path / "site-packages"
    _write(root / "torch" / "testing" / "__init__.py", b"runtime-required\n")
    _expect_code(
        "RUNTIME_TESTLIKE_UNDECLARED",
        lambda: builder.calculate_allowlist_tree_sha256(root, ["torch"]),
    )


def test_runtime_testlike_rejects_wrong_member_hash(tmp_path: Path):
    root = tmp_path / "site-packages"
    content = b"runtime-required\n"
    _write(root / "torch" / "testing" / "__init__.py", content)
    declaration = [
        {
            "path": "torch/testing/__init__.py",
            "bytes": len(content),
            "sha256": "0" * 64,
            "reason": "runtime-import-trace",
        }
    ]
    _expect_code(
        "RUNTIME_TESTLIKE_HASH_MISMATCH",
        lambda: builder.calculate_allowlist_tree_sha256(
            root,
            ["torch"],
            runtime_required_testlike=declaration,
        ),
    )


def test_runtime_testlike_rejects_declared_member_not_selected(tmp_path: Path):
    root = tmp_path / "site-packages"
    _write(root / "torch" / "__init__.py", b"runtime\n")
    declaration = [
        {
            "path": "torch/testing/__init__.py",
            "bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "reason": "runtime-import-trace",
        }
    ]
    _expect_code(
        "RUNTIME_TESTLIKE_FILE_SET_MISMATCH",
        lambda: builder.calculate_allowlist_tree_sha256(
            root,
            ["torch"],
            runtime_required_testlike=declaration,
        ),
    )
def test_dev_pyvenv_file_is_forbidden(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    python_component = next(
        item for item in spec["components"] if item["kind"] == "portable_python"
    )
    python_root = Path(python_component["root"])
    _write(python_root / "pyvenv.cfg", "home = C:\\Users\\developer\\Python312\n")
    python_component["include"].append("pyvenv.cfg")
    python_component["expected_tree_sha256"] = "0" * 64
    path = build_fixture["save"](spec)
    _expect_code("FORBIDDEN_PAYLOAD_PATH", lambda: builder.plan_from_spec(path))


def test_build_emits_signed_manifest_sbom_notices_and_verify_detects_tamper(
    build_fixture,
    tmp_path,
):
    result, pack_root = _build_pack(build_fixture, tmp_path / "output")
    assert result["verify"]["status"] == "verified"
    assert (pack_root / "engine-manifest.json").is_file()
    assert (pack_root / "SBOM.spdx.json").is_file()
    assert (pack_root / "THIRD_PARTY_NOTICES_INDEX.json").is_file()
    assert (pack_root / "THIRD_PARTY_NOTICES.txt").is_file()
    assert (pack_root / "FILES.sha256").is_file()
    assert (pack_root / "engine-manifest.sig").is_file()
    assert Path(result["archive_path"]).is_file()
    assert Path(result["catalog_path"]).is_file()
    assert not (pack_root / "pyvenv.cfg").exists()
    manifest_text = (pack_root / "engine-manifest.json").read_text(encoding="utf-8")
    assert str(build_fixture["model_root"]) not in manifest_text
    manifest = json.loads(manifest_text)
    assert manifest["schema"] == "mentorflow.qwen-engine-manifest.v1"
    assert set(manifest) == {
        "schema",
        "kind",
        "package_id",
        "version",
        "install_path_budget",
        "files",
    }
    assert manifest["install_path_budget"] == result["install_path_budget"]
    assert {item["path"] for item in manifest["files"]} >= {
        "FILES.sha256",
        "SBOM.spdx.json",
        "THIRD_PARTY_NOTICES.txt",
        "THIRD_PARTY_NOTICES_INDEX.json",
    }
    runtime_config = json.loads((pack_root / "runtime-config.json").read_text(encoding="utf-8"))
    assert runtime_config["requires_tts_api_key"] is False

    model = pack_root / "runtime" / "models" / "qwen3-tts-12hz-0.6b-base" / "model.safetensors"
    model.write_bytes(model.read_bytes() + b"tampered")
    _expect_code(
        "PACK_HASH_MISMATCH",
        lambda: builder.verify_pack(
            pack_root,
            build_fixture["public_key"],
            build_fixture["public_sha256"],
            expected_key_id="fixture-release-key",
        ),
    )


def test_repeated_build_is_byte_deterministic(build_fixture, tmp_path):
    first, first_root = _build_pack(build_fixture, tmp_path / "deterministic-a")
    second, second_root = _build_pack(build_fixture, tmp_path / "deterministic-b")

    assert Path(first["archive_path"]).read_bytes() == Path(second["archive_path"]).read_bytes()
    assert Path(first["catalog_path"]).read_bytes() == Path(second["catalog_path"]).read_bytes()
    assert (first_root / "engine-manifest.json").read_bytes() == (
        second_root / "engine-manifest.json"
    ).read_bytes()
    assert first["archive_sha256"] == second["archive_sha256"]
    assert first["manifest_sha256"] == second["manifest_sha256"]


def test_verify_only_cli_checks_catalog_archive_manifest_and_external_key(
    build_fixture,
    tmp_path,
    capsys,
):
    result, pack_root = _build_pack(build_fixture, tmp_path / "verify-cli-output")
    exit_code = builder.main(
        [
            "--verify-only",
            "--pack-root",
            str(pack_root),
            "--catalog",
            result["catalog_path"],
            "--archive",
            result["archive_path"],
            "--package-id",
            "mentorflow-qwen3-tts-runtime",
            "--version",
            "0.1.0",
            "--trusted-public-key",
            str(build_fixture["public_path"]),
            "--trusted-public-key-sha256",
            build_fixture["public_sha256"],
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "verified"
    assert report["archive_sha256"] == result["archive_sha256"]
    assert report["manifest_sha256"] == result["manifest_sha256"]
    assert report["signing_key_id"] == "fixture-release-key"


def test_verify_rejects_undeclared_file_and_empty_directory(build_fixture, tmp_path):
    _result, pack_root = _build_pack(build_fixture, tmp_path / "extra-output")
    _write(pack_root / "runtime" / "undeclared.bin", b"undeclared")
    _expect_code(
        "PACK_FILE_SET_MISMATCH",
        lambda: builder.verify_pack(
            pack_root,
            build_fixture["public_key"],
            build_fixture["public_sha256"],
            expected_key_id="fixture-release-key",
        ),
    )

    (pack_root / "runtime" / "undeclared.bin").unlink()
    (pack_root / "runtime" / "empty-extra").mkdir()
    _expect_code(
        "PACK_DIRECTORY_SET_MISMATCH",
        lambda: builder.verify_pack(
            pack_root,
            build_fixture["public_key"],
            build_fixture["public_sha256"],
            expected_key_id="fixture-release-key",
        ),
    )


def test_verify_rejects_hardlinked_material(build_fixture, tmp_path):
    _result, pack_root = _build_pack(build_fixture, tmp_path / "hardlink-output")
    material = pack_root / "runtime-config.json"
    outside = pack_root.parent / "runtime-config-hardlink-source.json"
    outside.write_bytes(material.read_bytes())
    material.unlink()
    try:
        os.link(outside, material)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable on this filesystem: {exc}")
    _expect_code(
        "PACK_FILE_INVALID",
        lambda: builder.verify_pack(
            pack_root,
            build_fixture["public_key"],
            build_fixture["public_sha256"],
            expected_key_id="fixture-release-key",
        ),
    )


def test_verify_rejects_reparse_material(build_fixture, tmp_path, monkeypatch):
    _result, pack_root = _build_pack(build_fixture, tmp_path / "reparse-output")
    material = (pack_root / "runtime-config.json").absolute()
    original = builder._is_reparse_point

    def fake_reparse(path: Path) -> bool:
        return path.absolute() == material or original(path)

    monkeypatch.setattr(builder, "_is_reparse_point", fake_reparse)
    _expect_code(
        "PACK_FILE_INVALID",
        lambda: builder.verify_pack(
            pack_root,
            build_fixture["public_key"],
            build_fixture["public_sha256"],
            expected_key_id="fixture-release-key",
        ),
    )


def test_builder_output_is_accepted_and_registered_by_canonical_adapter(
    build_fixture,
    tmp_path,
    monkeypatch,
):
    result, pack_root = _build_pack(build_fixture, tmp_path / "adapter-output")
    catalog = json.loads(Path(result["catalog_path"]).read_text(encoding="utf-8"))
    builder_report = builder.verify_catalog_release(
        catalog_path=Path(result["catalog_path"]),
        package_id="mentorflow-qwen3-tts-runtime",
        version="0.1.0",
        archive_path=Path(result["archive_path"]),
        pack_root=pack_root,
        public_key=build_fixture["public_key"],
        public_key_sha256=build_fixture["public_sha256"],
    )
    assert builder_report["manifest_sha256"] == result["manifest_sha256"]
    capability = voice_pack.verify_qwen_base_clone_engine_release(
        catalog=catalog,
        package_id="mentorflow-qwen3-tts-runtime",
        version="0.1.0",
        archive_path=Path(result["archive_path"]),
        package_root=pack_root,
        trusted_public_keys={"fixture-release-key": build_fixture["public_raw"]},
    )
    assert capability.is_valid() is True
    assert capability.dependency.manifest_sha256 == result["manifest_sha256"]

    class FixtureRuntime:
        def prepare(self, request):
            raise AssertionError("registration must not prepare")

        def synthesize(self, request):
            raise AssertionError("registration must not synthesize")

    monkeypatch.setattr(voice_pack, "_QWEN_BASE_CLONE_RUNTIMES", {})
    runtime = FixtureRuntime()
    voice_pack.register_qwen_base_clone_runtime(capability=capability, runtime=runtime)
    registration = voice_pack._qwen_runtime_registration(capability.dependency)
    assert registration is not None
    assert registration.capability is capability
    assert registration.runtime is runtime


def test_missing_trusted_signing_config_fails_closed(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    del spec["signing"]["trusted_public_key"]
    path = build_fixture["save"](spec)
    _expect_code("MISSING_EVIDENCE", lambda: builder.plan_from_spec(path))


def test_receipt_with_developer_absolute_path_is_rejected(build_fixture):
    spec = copy.deepcopy(build_fixture["spec"])
    receipt = _write(
        build_fixture["spec_path"].parent / "evidence" / "unsafe-receipt.json",
        json.dumps(
            {
                "schema": "fixture.source-receipt.v1",
                "source_path": "C:\\Users\\developer\\Downloads\\wheel.whl",
            }
        )
        + "\n",
    )
    model = next(item for item in spec["components"] if item["kind"] == "model")
    model["receipt"] = _pinned(receipt)
    path = build_fixture["save"](spec)
    _expect_code("ABSOLUTE_RECEIPT_PATH", lambda: builder.plan_from_spec(path))
