from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import struct

import pytest

from scripts import prepare_qwen_runtime_wheelhouse as prepare
from scripts import build_qwen3_tts_runtime_pack as builder
from backend.app.plain_file_copy import PlainFileCopyError, copy_plain_file_exact


def _artifact(name: str, version: str, digest: str) -> dict:
    return {
        "name": name,
        "version": version,
        "filename": f"{name}-{version}-py3-none-any.whl",
        "bytes": 10,
        "sha256": digest,
        "url": f"https://files.pythonhosted.org/packages/{name}.whl",
    }


def _receipt(name: str, version: str, digest: str, requires: list[str]) -> dict:
    return {
        "schema": prepare.RECEIPT_SCHEMA,
        "name": name,
        "version": version,
        "wheel": {"sha256": digest},
        "metadata": {"requires_dist": requires},
    }


def _strict_closure_fixture() -> tuple[dict, dict[str, dict]]:
    versions = {
        "qwen-tts": "0.1.1",
        "core-runtime": "1.5.0",
        "numpy": "2.4.6",
        "onnxruntime": "1.28.0",
        "flatbuffers": "25.12.19",
        "protobuf": "7.35.1",
        "sox": "1.4.1",
    }
    digests = {
        name: f"{index:x}" * 64 for index, name in enumerate(versions, start=1)
    }
    requirements = {
        "qwen-tts": [
            "core-runtime>=1.0",
            "onnxruntime",
            "sox",
            'developer-helper; extra == "dev"',
        ],
        "core-runtime": ["numpy>=2"],
        "onnxruntime": ["flatbuffers", "protobuf"],
        "numpy": [],
        "flatbuffers": [],
        "protobuf": [],
        "sox": [],
    }
    plan = {
        "artifacts": [
            _artifact(name, version, digests[name])
            for name, version in versions.items()
        ]
    }
    receipts = {
        name: _receipt(name, version, digests[name], requirements[name])
        for name, version in versions.items()
    }
    return plan, receipts


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("onnxruntime/ThirdPartyNotices.txt", True),
        ("sklearn/svm/src/liblinear/COPYRIGHT", True),
        ("sample.dist-info/licenses/LICENSE", True),
        ("packaging/licenses/_spdx.py", False),
    ],
)
def test_audit_license_member_recognizes_real_legal_files_only(relative: str, expected: bool):
    assert prepare._license_member(PurePosixPath(relative)) is expected


def test_strict_12hz_closure_removes_25hz_roots_and_only_their_transitives():
    plan, receipts = _strict_closure_fixture()
    closure = prepare._strict_12hz_closure(plan, receipts)
    assert closure["selected_names"] == ["core-runtime", "numpy", "qwen-tts"]
    assert closure["removed_names"] == [
        "flatbuffers",
        "onnxruntime",
        "protobuf",
        "sox",
    ]
    assert [item["dependency"] for item in closure["policy_skips"]] == [
        "onnxruntime",
        "sox",
    ]
    assert all(
        item["requires_dist_source"] == "audited-wheel-METADATA"
        for item in closure["artifacts"]
    )


def test_strict_12hz_closure_rejects_audited_version_outside_metadata_requirement():
    plan, receipts = _strict_closure_fixture()
    receipts["qwen-tts"]["metadata"]["requires_dist"][0] = "core-runtime>=2"
    with pytest.raises(prepare.WheelhouseError) as caught:
        prepare._strict_12hz_closure(plan, receipts)
    assert caught.value.code == "STRICT_12HZ_VERSION_MISMATCH"


def test_strict_license_evidence_counts_103_wheel_plus_one_external_without_dedup():
    repeated = "a" * 64
    wheel_files = [
        {
            "component_id": f"package-{index:03d}",
            "path": f"package-{index:03d}.dist-info/licenses/LICENSE",
            "sha256": repeated if index in {0, 1} else f"{index + 1:064x}",
        }
        for index in range(103)
    ]
    external = [
        {
            "component_id": "tokenizers",
            "path": "license-supplements/tokenizers==0.22.2/LICENSE",
            "sha256": "b" * 64,
            "receipt_sha256": "c" * 64,
        }
    ]
    evidence = prepare._license_evidence_summary(wheel_files, external)

    assert evidence["schema"] == prepare.LICENSE_EVIDENCE_SCHEMA
    assert evidence["wheel_file_count"] == 103
    assert evidence["external_supplement_file_count"] == 1
    assert evidence["total_file_count"] == 104
    assert len(evidence["wheel_files"]) == 103
    assert len(evidence["total_sha256"]) == 104
    assert evidence["total_sha256"].count(repeated) == 2


def test_install_path_budget_records_longest_member_and_rejects_an_overlong_root(tmp_path):
    longest = (
        "runtime/python/Lib/site-packages/torch/include/ATen/native/transformers/"
        "cuda/mem_eff_attention/epilogue/epilogue_rescale_output.h"
    )
    budget = builder.calculate_install_path_budget(["runtime/python/python.exe", longest])
    assert budget["max_relative_path"] == longest
    assert budget["max_relative_path_chars"] == len(longest)
    assert budget["minimum_safe_install_root_budget"] == 258 - len(longest)
    accepted_root = Path(os.path.abspath(tmp_path / "ok"))
    if len(str(accepted_root)) <= budget["minimum_safe_install_root_budget"]:
        accepted = builder.validate_install_root_budget(
            accepted_root,
            budget,
            relative_paths=["runtime/python/python.exe", longest],
        )
        assert accepted["max_full_path_chars"] <= 259
    overlong_root = Path(os.path.abspath(tmp_path / ("r" * 220)))
    with pytest.raises(builder.BuildError) as caught:
        builder.validate_install_root_budget(
            overlong_root,
            budget,
            relative_paths=["runtime/python/python.exe", longest],
        )
    assert caught.value.code == "INSTALL_ROOT_PATH_BUDGET_EXCEEDED"


@pytest.mark.skipif(os.name != "nt", reason="exercises Win32 CopyFileW extended paths")
def test_plain_copy_uses_extended_win32_path_for_over_260_character_target(tmp_path):
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    source = source_root / "payload.bin"
    payload = b"long-path-copy-fixture\n"
    source.write_bytes(payload)
    relative = Path(*(["nested-segment-" + ("x" * 36)] * 5), "payload.bin")
    destination = destination_root / relative
    assert len(str(destination.absolute())) >= 260
    digest = hashlib.sha256(payload).hexdigest()
    result = copy_plain_file_exact(
        source,
        destination,
        expected_bytes=len(payload),
        expected_sha256=digest,
        source_root=source_root,
        destination_root=destination_root,
    )
    assert result.bytes == len(payload)
    assert result.sha256 == digest
    extended_destination = Path("\\\\?\\" + str(destination.absolute()))
    assert extended_destination.read_bytes() == payload
    with pytest.raises(PlainFileCopyError) as caught:
        copy_plain_file_exact(
            source,
            destination,
            expected_bytes=len(payload),
            expected_sha256=digest,
            source_root=source_root,
            destination_root=destination_root,
        )
    assert caught.value.code == "COPY_TARGET_EXISTS"


def test_v2_probe_is_real_product_entry_with_explicit_bind_and_no_package_import_list():
    source = prepare._portable_probe_source_v2()
    compile(source, "portable_import_probe_v2.py", "exec")
    assert "from qwen_tts import Qwen3TTSModel" in source
    assert "socket.socket.bind = _deny_bind" in source
    assert '"bind": ["(\'::1\', 0)"]' in source
    assert "\nimport onnxruntime\n" not in source
    assert "\nimport torch\n" not in source
    assert "numba.np.ufunc.tbbpool" in source


def _tiny_pe_with_import(dll_name: str) -> bytes:
    data = bytearray(0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0, 0, 0, 0xF0, 0x2022)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<Q", data, optional + 24, 0x140000000)
    struct.pack_into("<I", data, optional + 60, 0x200)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<II", data, optional + 112 + 8, 0x1000, 40)
    section = optional + 0xF0
    data[section : section + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, section + 8, 0x200, 0x1000, 0x200, 0x200)
    struct.pack_into("<IIIII", data, 0x200, 0, 0, 0, 0x1050, 0)
    encoded = dll_name.encode("ascii") + b"\0"
    data[0x250 : 0x250 + len(encoded)] = encoded
    return bytes(data)


def test_pe_parser_reads_imports_without_executing_binary(tmp_path):
    path = tmp_path / "fixture.pyd"
    path.write_bytes(_tiny_pe_with_import("KERNEL32.dll"))
    assert prepare._pe_imports(path) == {
        "imports": ["kernel32.dll"],
        "delay_imports": [],
    }


def test_pe_parser_fails_closed_on_non_pe_input(tmp_path):
    path = tmp_path / "not-a-pe.dll"
    path.write_bytes(b"not a portable executable")
    with pytest.raises(prepare.WheelhouseError) as caught:
        prepare._pe_imports(path)
    assert caught.value.code == "PE_PARSE_FAILED"


def test_host_observer_fails_if_named_by_any_portable_pe_edge(tmp_path):
    path = tmp_path / "AudioDevProps2.dll"
    path.write_bytes(b"fixture")
    with pytest.raises(prepare.WheelhouseError) as caught:
        prepare._host_injected_observer_record(
            str(path),
            pe_declared_import_names={"audiodevprops2.dll"},
        )
    assert caught.value.code == "HOST_EXTERNAL_NATIVE_DEPENDENCY"


def test_host_observer_requires_exact_valid_authenticode_publisher(tmp_path, monkeypatch):
    path = tmp_path / "AudioDevProps2.dll"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(
        prepare,
        "_authenticode_signature",
        lambda _path: {"status": "NotSigned", "publisher_subject": None},
    )
    with pytest.raises(prepare.WheelhouseError) as caught:
        prepare._host_injected_observer_record(str(path), pe_declared_import_names=set())
    assert caught.value.code == "HOST_INJECTED_MODULE_UNAPPROVED"


def test_known_signed_host_observer_is_recorded_as_non_prerequisite(tmp_path, monkeypatch):
    path = tmp_path / "AudioDevProps2.dll"
    path.write_bytes(b"signed fixture")
    monkeypatch.setattr(
        prepare,
        "_authenticode_signature",
        lambda _path: {
            "status": "Valid",
            "publisher_subject": "CN=Fixture, O=SteelSeries France SASU, C=FR",
        },
    )
    record = prepare._host_injected_observer_record(
        str(path),
        pe_declared_import_names=set(),
    )
    assert record["runtime_prerequisite"] is False
    assert record["pe_import_or_delay_import_edge"] is False
    assert record["approved_publisher"] == "SteelSeries France SASU"
