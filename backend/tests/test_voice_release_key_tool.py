from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import uuid
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.app import teacher_voice_pack as voice_pack
from scripts import manage_voice_release_keys as key_tool


PURPOSE = "mentorflow-voice-and-engine-release"
STARTS_ON = "2026-08-01"
ROTATE_AFTER = "2026-09-01"
OVERLAP_UNTIL = "2026-09-15"
TEST_ROOT_KEY_ID = "test-mentorflow-release-policy-root-v1"
TEST_ROOT_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(
    bytes.fromhex("4d656e746f72466c6f772d746573742d72656c656173652d726f6f742d763121")
)
ALL_MANIFEST_SCHEMAS = sorted({value["schema"] for value in key_tool.MANIFEST_CONTRACTS.values()})


@pytest.fixture(scope="module")
def e_key_workspace() -> Path:
    if os.name != "nt" or not Path("E:/").is_dir():
        pytest.skip("real Windows E: ACL/FileId tests require an E: volume")
    parent = Path("E:/Kristina_Runtime/cache")
    if not parent.is_dir():
        pytest.skip("the designated E: test parent is unavailable")
    workspace = parent / f"mentorflow-release-key-test-{uuid.uuid4().hex}"
    workspace.mkdir()
    try:
        yield workspace
    finally:
        assert workspace.parent == parent
        assert workspace.name.startswith("mentorflow-release-key-test-")
        if workspace.exists():
            shutil.rmtree(workspace)
        assert not workspace.exists()


def _request(
    output_root: Path,
    *,
    key_id: str = "test-release-key-20260808",
    purpose: str = PURPOSE,
    test_only: bool = True,
) -> dict[str, Any]:
    return {
        "output_root": output_root,
        "key_id": key_id,
        "purpose": purpose,
        "starts_on": STARTS_ON,
        "rotate_after": ROTATE_AFTER,
        "overlap_days": 14,
        "predecessor_key_id": "",
        "test_only": test_only,
    }


def _manifest_value(scope: str, *, production: bool = False) -> dict[str, Any]:
    suffix = "1.0.0" if production else "0.0.0-test"
    prefix = "production" if production else "test-fixture"
    if scope == "engine":
        return {
            "schema": "mentorflow.qwen-engine-manifest.v1",
            "kind": "qwen3-tts-0.6b-base",
            "package_id": f"{prefix}-engine",
            "version": suffix,
            "files": [],
        }
    if scope == "voice":
        return {
            "schema": "mentorflow.voice-pack.v1",
            "pack_id": f"{prefix}-voice",
            "version": suffix,
            "files": [],
        }
    if scope == "engine-catalog":
        return {
            "schema": "mentorflow.qwen-engine-catalog.v1",
            "engines": [
                {
                    "package_id": f"{prefix}-engine",
                    "version": suffix,
                }
            ],
        }
    if scope == "voice-catalog":
        return {
            "schema": "mentorflow.voice-pack-catalog.v1",
            "packs": [
                {
                    "pack_id": f"{prefix}-voice",
                    "version": suffix,
                }
            ],
        }
    raise AssertionError(f"unknown fixture scope: {scope}")


def _write_manifest(path: Path, scope: str, *, production: bool = False) -> bytes:
    raw = (
        json.dumps(_manifest_value(scope, production=production), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _index_paths(key_root: Path) -> dict[str, Any]:
    index_path = key_root / key_tool.KEY_INDEX_BASENAME
    index = json.loads(index_path.read_text(encoding="utf-8"))
    return {
        "index": index,
        "index_path": index_path,
        "private_path": key_root / index["private_key"]["basename"],
        "public_path": key_root / index["public_key"]["basename"],
        "receipt_path": key_root / index["trust_receipt"]["basename"],
    }


def _supplemental_constraints(generated_key: dict[str, Any]) -> dict[str, Any]:
    receipt = json.loads(generated_key["receipt_path"].read_text(encoding="utf-8"))
    return {
        "trusted_fingerprint": receipt["fingerprint"]["value"],
        "expected_key_id": receipt["key_id"],
    }


def _trust_chain(generated_key: dict[str, Any]) -> dict[str, Any]:
    return {
        "trust_policy": generated_key["policy_path"],
        "trust_policy_signature": generated_key["policy_signature_path"],
    }


def _policy_value(generated_key: dict[str, Any]) -> dict[str, Any]:
    receipt = json.loads(generated_key["receipt_path"].read_text(encoding="utf-8"))
    return {
        "schema": key_tool.TRUST_POLICY_SCHEMA,
        "policy_id": "test-mentorflow-release-policy-main-v1",
        "root_key_id": TEST_ROOT_KEY_ID,
        "test_only": True,
        "created_at_utc": "2026-08-07T00:00:00Z",
        "validity": {
            "not_before": "2026-08-01T00:00:00Z",
            "not_after": "2026-09-15T23:59:59Z",
        },
        "allowed_leaf_keys": [
            {
                "key_id": receipt["key_id"],
                "public_key_sha256": receipt["fingerprint"]["value"],
                "purposes": sorted(key_tool.ALLOWED_PURPOSES),
                "schemas": ALL_MANIFEST_SCHEMAS,
            }
        ],
        "revoked_key_ids": [],
    }


def _write_test_policy(
    case: Path,
    generated_key: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    signing_key: Ed25519PrivateKey = TEST_ROOT_PRIVATE_KEY,
    signature_root_key_id: str = TEST_ROOT_KEY_ID,
) -> dict[str, Any]:
    case.mkdir()
    value = deepcopy(policy if policy is not None else _policy_value(generated_key))
    raw = key_tool._json_bytes(value)
    policy_path = case / "release-trust-policy.json"
    signature_path = case / "release-trust-policy.sig"
    policy_path.write_bytes(raw)
    signature_path.write_bytes(
        key_tool._json_bytes(
            {
                "algorithm": "ed25519",
                "root_key_id": signature_root_key_id,
                "signature": base64.b64encode(signing_key.sign(raw)).decode("ascii"),
            }
        )
    )
    return {
        "policy": value,
        "policy_path": policy_path,
        "policy_signature_path": signature_path,
    }


def _load_fixture_private(path: Path) -> Ed25519PrivateKey:
    loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(loaded, Ed25519PrivateKey)
    return loaded


def _resign_receipt(receipt: dict[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    payload = deepcopy(receipt)
    payload.pop("self_signature", None)
    payload_bytes = key_tool._canonical_json_bytes(payload)
    payload["self_signature"] = {
        "algorithm": "ed25519",
        "key_id": payload["key_id"],
        "payload_sha256": key_tool._sha256(payload_bytes),
        "signature": base64.b64encode(private_key.sign(payload_bytes)).decode("ascii"),
    }
    return payload


def _write_signature(path: Path, private_key: Ed25519PrivateKey, raw: bytes, key_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "algorithm": "ed25519",
                "key_id": key_id,
                "signature": base64.b64encode(private_key.sign(raw)).decode("ascii"),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def generated_key(e_key_workspace: Path) -> dict[str, Any]:
    key_id = "test-release-key-20260808-main"
    key_root = e_key_workspace / "keys-main"
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = key_tool.main(
            [
                "--generate",
                "--output-root",
                str(key_root),
                "--key-id",
                key_id,
                "--purpose",
                PURPOSE,
                "--rotation-start",
                STARTS_ON,
                "--rotate-after",
                ROTATE_AFTER,
                "--overlap-days",
                "14",
                "--test-key",
            ]
        )
    assert exit_code == 0, stderr.getvalue()
    assert stderr.getvalue() == ""
    paths = _index_paths(key_root)
    generated = {
        "key_id": key_id,
        "key_root": key_root,
        "stdout": stdout.getvalue(),
        "result": json.loads(stdout.getvalue()),
        **paths,
    }
    generated.update(_write_test_policy(e_key_workspace / "policy-main", generated))
    return generated


@pytest.fixture(scope="module")
def generated_key_b(e_key_workspace: Path) -> dict[str, Any]:
    key_id = "test-release-key-20260808-substitute"
    key_root = e_key_workspace / "keys-substitute"
    result = key_tool.generate_release_key(
        **_request(key_root, key_id=key_id, purpose="mentorflow-engine-pack-release")
    )
    return {
        "key_id": key_id,
        "key_root": key_root,
        "result": result,
        **_index_paths(key_root),
    }


@pytest.fixture(scope="module")
def signed_engine(e_key_workspace: Path, generated_key: dict[str, Any]) -> dict[str, Any]:
    case = e_key_workspace / "signed-engine"
    case.mkdir()
    manifest_path = case / "engine-manifest.json"
    raw_manifest = _write_manifest(manifest_path, "engine")
    signature_path = case / "engine-manifest.sig"
    result = key_tool.sign_manifest(
        manifest=manifest_path,
        key_root=generated_key["key_root"],
        signature_out=signature_path,
    )
    return {
        "case": case,
        "manifest_path": manifest_path,
        "raw_manifest": raw_manifest,
        "signature_path": signature_path,
        "result": result,
    }


def _public_receipt_copy(
    e_key_workspace: Path,
    generated_key: dict[str, Any],
    name: str,
    receipt: dict[str, Any],
) -> tuple[Path, Path]:
    case = e_key_workspace / name
    case.mkdir()
    public_path = case / generated_key["public_path"].name
    receipt_path = case / generated_key["receipt_path"].name
    shutil.copyfile(generated_key["public_path"], public_path)
    receipt_path.write_bytes(key_tool._json_bytes(receipt))
    return public_path, receipt_path


def test_default_command_is_non_mutating_plan(capsys: pytest.CaptureFixture[str]):
    assert key_tool.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "plan"
    assert result["writes"] is False
    assert result["explicit_actions"] == ["--generate", "--sign-manifest", "--verify"]
    assert result["verify_requires"][-2:] == ["--trust-policy", "--trust-policy-signature"]


def test_default_rotation_start_uses_utc_at_shanghai_midnight(monkeypatch: pytest.MonkeyPatch):
    class ShanghaiLocalDate:
        @staticmethod
        def today():
            return datetime(2026, 8, 8, 0, 30, tzinfo=timezone.utc).date()

    monkeypatch.setattr(key_tool, "date", ShanghaiLocalDate)
    monkeypatch.setattr(
        key_tool,
        "_utc_now",
        lambda: datetime(2026, 8, 7, 16, 30, tzinfo=timezone.utc),
    )
    assert key_tool._parser().parse_args([]).rotation_start == "2026-08-07"


def test_generation_arguments_without_mode_are_an_implicit_plan(
    e_key_workspace: Path, capsys: pytest.CaptureFixture[str]
):
    output_root = e_key_workspace / "implicit-plan-only"
    assert (
        key_tool.main(
            [
                "--output-root",
                str(output_root),
                "--key-id",
                "test-implicit-plan-20260808",
                "--rotation-start",
                STARTS_ON,
                "--rotate-after",
                ROTATE_AFTER,
                "--test-key",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "plan"
    assert result["writes"] is False
    assert not output_root.exists()


@pytest.mark.parametrize(
    ("key_id", "test_only"),
    [
        ("test-looks-like-test-but-active", False),
        ("production-looking-test-key", True),
    ],
)
def test_test_key_prefix_and_status_are_bidirectionally_bound(
    e_key_workspace: Path, key_id: str, test_only: bool
):
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool.plan_generation(**_request(e_key_workspace / f"prefix-{uuid.uuid4().hex}", key_id=key_id, test_only=test_only))
    assert caught.value.code == "TEST_KEY_ID_INVALID"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (r"relative\key", "PATH_NOT_ABSOLUTE"),
        (r"C:\private\key.pem", "PRIVATE_KEY_DRIVE_FORBIDDEN"),
        (r"E:\keys\..\escape", "PATH_UNSAFE"),
        (r"E:\keys\private.pem:stream", "PATH_UNSAFE"),
        (r"\\server\share\key", "PATH_NOT_ABSOLUTE"),
    ],
)
def test_private_location_rejects_escape_ads_unc_and_c_drive(value: str, code: str):
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool._validate_private_location(value, must_exist=False)
    assert caught.value.code == code


def test_private_location_rejects_repository_and_e_drive_temp(e_key_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(key_tool.KeyToolError) as repository:
        key_tool._validate_private_location(key_tool.REPOSITORY_ROOT / "release-private-key", must_exist=False)
    assert repository.value.code == "PRIVATE_KEY_REPOSITORY_FORBIDDEN"

    monkeypatch.setenv("TEMP", str(e_key_workspace))
    with pytest.raises(key_tool.KeyToolError) as temporary:
        key_tool._validate_private_location(e_key_workspace / "release-private-key", must_exist=False)
    assert temporary.value.code == "PRIVATE_KEY_TEMP_FORBIDDEN"


def test_generation_has_random_private_name_exact_acl_fileids_and_path_free_stdout(generated_key: dict[str, Any]):
    key_root = generated_key["key_root"]
    index = generated_key["index"]
    private_path = generated_key["private_path"]
    public_path = generated_key["public_path"]
    receipt_path = generated_key["receipt_path"]
    result = generated_key["result"]
    stdout = generated_key["stdout"]

    assert result["status"] == "generated-test-key"
    assert result["key_id"] == generated_key["key_id"]
    assert result["public_key_basename"] == public_path.name
    assert result["public_trust_receipt_basename"] == receipt_path.name
    assert not re.search(r"(?i)[a-z]:[\\/]", stdout)
    assert str(key_root) not in stdout
    assert private_path.name not in stdout
    assert "BEGIN PRIVATE KEY" not in stdout

    assert re.fullmatch(r"\.private-[0-9a-f]{48}\.pem", private_path.name)
    assert {item.name for item in key_root.iterdir()} == {
        key_tool.KEY_INDEX_BASENAME,
        private_path.name,
        public_path.name,
        receipt_path.name,
    }

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_text = json.dumps(receipt, sort_keys=True)
    assert private_path.name not in receipt_text
    assert key_tool.KEY_INDEX_BASENAME not in receipt_text
    assert result["public_trust_receipt"] == receipt
    assert receipt["schema"] == key_tool.TRUST_RECEIPT_SCHEMA
    assert receipt["key_id"] == generated_key["key_id"]
    assert receipt["purpose"] == PURPOSE
    assert receipt["status"] == "test-only"
    assert receipt["test_only"] is True
    assert receipt["rotation"] == {
        "starts_on": STARTS_ON,
        "rotate_after": ROTATE_AFTER,
        "overlap_until": OVERLAP_UNTIL,
        "overlap_days": 14,
        "predecessor_key_id": None,
    }
    assert receipt["validity"] == {
        "signing": {"not_before": STARTS_ON, "not_after": ROTATE_AFTER},
        "verification": {"not_before": STARTS_ON, "not_after": OVERLAP_UNTIL},
    }
    assert receipt["application_trust"]["private_key_allowed"] is False
    assert result["fingerprint_sha256"] == receipt["fingerprint"]["value"]

    raw_public = base64.b64decode(receipt["public_key"]["value"], validate=True)
    public_key = serialization.load_pem_public_key(public_path.read_bytes())
    payload = deepcopy(receipt)
    self_signature = payload.pop("self_signature")
    payload_bytes = key_tool._canonical_json_bytes(payload)
    assert self_signature["payload_sha256"] == key_tool._sha256(payload_bytes)
    public_key.verify(base64.b64decode(self_signature["signature"], validate=True), payload_bytes)
    assert key_tool._sha256(raw_public) == receipt["fingerprint"]["value"]

    for label, path in (
        ("private_key", private_path),
        ("public_key", public_path),
        ("trust_receipt", receipt_path),
    ):
        expected = key_tool._identity_from_dict(index[label]["identity"], field=label)
        actual = key_tool._identity_from_path(path, directory=False, field=label)
        assert key_tool._same_identity(expected, actual)
        assert expected.size == actual.size

    for protected in (key_root, private_path, public_path, receipt_path, generated_key["index_path"]):
        acl = key_tool._apply_and_verify_acl(protected, apply=False)
        assert acl["ok"] is True
        assert acl["protected"] is True
        assert acl["rule_count"] == 3
        assert acl["expected_rights"] == 2032127
        assert acl["expected_propagation"] == "None"
        assert acl["expected_inheritance"] == (
            "ContainerInherit, ObjectInherit" if protected == key_root else "None"
        )
        assert acl["bad"] == []
        assert acl["missing"] == []
        assert set(acl["principals"]) == {
            acl["current_user_sid"],
            "S-1-5-18",
            "S-1-5-32-544",
        }


def test_sign_verify_signature_document_and_runtime_adapter_interoperate(
    generated_key: dict[str, Any], signed_engine: dict[str, Any]
):
    result = signed_engine["result"]
    signature_document = json.loads(signed_engine["signature_path"].read_text(encoding="utf-8"))
    receipt = json.loads(generated_key["receipt_path"].read_text(encoding="utf-8"))

    assert result["status"] == "signed"
    assert result["key_id"] == receipt["key_id"]
    assert result["signature_basename"] == "engine-manifest.sig"
    assert not re.search(r"(?i)[a-z]:[\\/]", json.dumps(result))
    assert signature_document == {
        "algorithm": "ed25519",
        "key_id": receipt["key_id"],
        "signature": signature_document["signature"],
    }
    verified = key_tool.verify_manifest(
        manifest=signed_engine["manifest_path"],
        public_key=generated_key["public_path"],
        trust_receipt=generated_key["receipt_path"],
        signature=signed_engine["signature_path"],
        **_trust_chain(generated_key),
    )
    assert verified["status"] == "verified"
    assert verified["integrity_verified"] is True
    assert verified["policy_signature_verified"] is True
    assert verified["trust_anchored"] is True
    assert verified["trust_policy"] == {
        "policy_id": "test-mentorflow-release-policy-main-v1",
        "root_key_id": TEST_ROOT_KEY_ID,
        "test_only": True,
    }
    assert verified["key_id"] == receipt["key_id"]
    assert verified["manifest_sha256"] == key_tool._sha256(signed_engine["raw_manifest"])

    raw_public = base64.b64decode(receipt["public_key"]["value"], validate=True)
    raw_signature = base64.b64decode(signature_document["signature"], validate=True)
    assert voice_pack.verify_ed25519(raw_public, signed_engine["raw_manifest"], raw_signature) is True


def test_signed_engine_is_accepted_by_full_runtime_catalog_adapter(
    e_key_workspace: Path, generated_key: dict[str, Any]
):
    package_id = "test-runtime-engine"
    version = "0.0.0-test"
    package_root = e_key_workspace / "runtime-adapter-package"
    payloads = {
        "model/model.safetensors": ("model_asset", b"qwen-test-model"),
        "metadata/release.json": ("metadata", b'{"fixture":"test-only"}'),
    }
    files = []
    for relative, (role, content) in payloads.items():
        path = package_root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append(
            {
                "path": relative,
                "role": role,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest = {
        "schema": voice_pack.QWEN_ENGINE_MANIFEST_SCHEMA,
        "kind": voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
        "package_id": package_id,
        "version": version,
        "files": files,
    }
    raw_manifest = key_tool._canonical_json_bytes(manifest)
    manifest_path = package_root / "engine-manifest.json"
    manifest_path.write_bytes(raw_manifest)
    signature_path = package_root / "engine-manifest.sig"
    key_tool.sign_manifest(
        manifest=manifest_path,
        key_root=generated_key["key_root"],
        signature_out=signature_path,
    )

    policy_verified = key_tool.verify_manifest(
        manifest=manifest_path,
        public_key=generated_key["public_path"],
        trust_receipt=generated_key["receipt_path"],
        signature=signature_path,
        **_trust_chain(generated_key),
    )
    assert policy_verified["policy_signature_verified"] is True
    assert policy_verified["trust_anchored"] is True

    archive_path = e_key_workspace / "runtime-adapter-package.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_root).as_posix())
    catalog = {
        "schema": voice_pack.QWEN_ENGINE_CATALOG_SCHEMA,
        "engines": [
            {
                "kind": voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
                "package_id": package_id,
                "version": version,
                "archive": {
                    "bytes": archive_path.stat().st_size,
                    "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                },
                "manifest": {
                    "bytes": len(raw_manifest),
                    "sha256": hashlib.sha256(raw_manifest).hexdigest(),
                },
                "signing_key_id": generated_key["key_id"],
            }
        ],
    }
    raw_public = base64.b64decode(
        generated_key["result"]["public_trust_receipt"]["public_key"]["value"],
        validate=True,
    )
    capability = voice_pack.verify_qwen_base_clone_engine_release(
        catalog=catalog,
        package_id=package_id,
        version=version,
        archive_path=archive_path,
        package_root=package_root,
        trusted_public_keys={generated_key["key_id"]: raw_public},
    )
    assert capability.dependency.catalog_id == package_id
    assert capability.dependency.version == version
    assert capability.dependency.manifest_sha256 == hashlib.sha256(raw_manifest).hexdigest()


def test_fixed_registry_policy_rejects_whole_bundle_and_candidate_fake_root(
    e_key_workspace: Path,
    generated_key: dict[str, Any],
    generated_key_b: dict[str, Any],
):
    case = e_key_workspace / "whole-bundle-substitution"
    case.mkdir()
    manifest_path = case / "engine-manifest.json"
    _write_manifest(manifest_path, "engine")
    signature_path = case / "engine-manifest.sig"
    key_tool.sign_manifest(
        manifest=manifest_path,
        key_root=generated_key_b["key_root"],
        signature_out=signature_path,
    )

    with pytest.raises(key_tool.KeyToolError) as substituted:
        key_tool.verify_manifest(
            manifest=manifest_path,
            public_key=generated_key_b["public_path"],
            trust_receipt=generated_key_b["receipt_path"],
            signature=signature_path,
            **_trust_chain(generated_key),
        )
    assert substituted.value.code == "TRUST_POLICY_LEAF_UNKNOWN"

    fake_root_key_id = "test-attacker-controlled-policy-root"
    fake_root_private = _load_fixture_private(generated_key_b["private_path"])
    fake_raw_public = base64.b64decode(
        generated_key_b["result"]["public_trust_receipt"]["public_key"]["value"],
        validate=True,
    )
    (case / "release_trust_roots.v1.json").write_bytes(
        key_tool._json_bytes(
            {
                "schema": key_tool.TRUST_ROOTS_SCHEMA,
                "production_roots": [],
                "test_roots": [
                    {
                        "algorithm": "ed25519",
                        "root_key_id": fake_root_key_id,
                        "public_key": base64.b64encode(fake_raw_public).decode("ascii"),
                        "public_key_sha256": hashlib.sha256(fake_raw_public).hexdigest(),
                        "status": "test-only",
                        "test_only": True,
                    }
                ],
            }
        )
    )
    fake_policy = _policy_value(generated_key_b)
    fake_policy["policy_id"] = "test-attacker-policy"
    fake_policy["root_key_id"] = fake_root_key_id
    fake = _write_test_policy(
        e_key_workspace / "fake-candidate-policy",
        generated_key_b,
        policy=fake_policy,
        signing_key=fake_root_private,
        signature_root_key_id=fake_root_key_id,
    )
    with pytest.raises(key_tool.KeyToolError) as fake_root:
        key_tool.verify_manifest(
            manifest=manifest_path,
            public_key=generated_key_b["public_path"],
            trust_receipt=generated_key_b["receipt_path"],
            signature=signature_path,
            trust_policy=fake["policy_path"],
            trust_policy_signature=fake["policy_signature_path"],
        )
    assert fake_root.value.code == "TRUST_ROOT_UNKNOWN"


def test_verify_supplemental_constraints_can_only_reduce_signed_policy_trust(
    generated_key: dict[str, Any], signed_engine: dict[str, Any]
):
    base = {
        "manifest": signed_engine["manifest_path"],
        "public_key": generated_key["public_path"],
        "trust_receipt": generated_key["receipt_path"],
        "signature": signed_engine["signature_path"],
        **_trust_chain(generated_key),
    }
    constraints = _supplemental_constraints(generated_key)
    cases = [
        (
            {"trusted_fingerprint": "0" * 64},
            "TRUST_CONSTRAINT_FINGERPRINT_MISMATCH",
        ),
        (
            {"trusted_fingerprint": "not-a-fingerprint"},
            "TRUST_CONSTRAINT_INVALID",
        ),
        (
            {"expected_key_id": "test-release-key-unexpected"},
            "TRUST_CONSTRAINT_KEY_ID_MISMATCH",
        ),
        (
            {"revoked_key_ids": [constraints["expected_key_id"]]},
            "TRUST_POLICY_REVOKED",
        ),
    ]
    for supplied_anchor, expected_code in cases:
        with pytest.raises(key_tool.KeyToolError) as caught:
            key_tool.verify_manifest(**base, **supplied_anchor)
        assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("name", "mutate", "expected_code"),
    [
        (
            "leaf-fingerprint",
            lambda value: value["allowed_leaf_keys"][0].__setitem__("public_key_sha256", "0" * 64),
            "TRUST_POLICY_LEAF_MISMATCH",
        ),
        (
            "revoked-leaf",
            lambda value: value["revoked_key_ids"].append("test-release-key-20260808-main"),
            "TRUST_POLICY_REVOKED",
        ),
        (
            "purpose-grant",
            lambda value: value["allowed_leaf_keys"][0].__setitem__(
                "purposes", ["mentorflow-engine-pack-release"]
            ),
            "TRUST_POLICY_PURPOSE_FORBIDDEN",
        ),
        (
            "schema-grant",
            lambda value: value["allowed_leaf_keys"][0].__setitem__(
                "schemas", ["mentorflow.voice-pack.v1"]
            ),
            "TRUST_POLICY_SCHEMA_FORBIDDEN",
        ),
        (
            "expired",
            lambda value: value["validity"].__setitem__("not_after", "2026-08-08T11:00:00Z"),
            "TRUST_POLICY_OUTSIDE_VALIDITY",
        ),
        (
            "not-yet-valid",
            lambda value: value["validity"].__setitem__("not_before", "2026-08-08T13:00:00Z"),
            "TRUST_POLICY_OUTSIDE_VALIDITY",
        ),
        (
            "future-created",
            lambda value: value.__setitem__("created_at_utc", "2026-08-08T12:06:00Z"),
            "TRUST_POLICY_INVALID",
        ),
        (
            "noncanonical-offset",
            lambda value: value.__setitem__("created_at_utc", "2026-08-07T08:00:00+08:00"),
            "TRUST_POLICY_INVALID",
        ),
    ],
)
def test_signed_policy_rejects_wrong_expired_future_revoked_and_scope_grants(
    e_key_workspace: Path,
    generated_key: dict[str, Any],
    signed_engine: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    expected_code: str,
):
    policy = _policy_value(generated_key)
    mutate(policy)
    artifact = _write_test_policy(e_key_workspace / f"policy-negative-{name}", generated_key, policy=policy)
    monkeypatch.setattr(
        key_tool,
        "_utc_now",
        lambda: datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool.verify_manifest(
            manifest=signed_engine["manifest_path"],
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signed_engine["signature_path"],
            trust_policy=artifact["policy_path"],
            trust_policy_signature=artifact["policy_signature_path"],
        )
    assert caught.value.code == expected_code


def test_policy_signature_test_root_boundary_and_empty_production_registry_fail_closed(
    e_key_workspace: Path,
    generated_key: dict[str, Any],
    signed_engine: dict[str, Any],
):
    registry = json.loads(key_tool.RELEASE_TRUST_ROOTS_PATH.read_text(encoding="utf-8"))
    assert registry["schema"] == key_tool.TRUST_ROOTS_SCHEMA
    assert registry["production_roots"] == []
    assert [root["root_key_id"] for root in registry["test_roots"]] == [TEST_ROOT_KEY_ID]
    installed_test_root = base64.b64decode(registry["test_roots"][0]["public_key"], validate=True)
    fixture_test_root = TEST_ROOT_PRIVATE_KEY.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    assert installed_test_root == fixture_test_root

    tampered = _write_test_policy(e_key_workspace / "policy-signature-tampered", generated_key)
    signature_document = json.loads(tampered["policy_signature_path"].read_text(encoding="utf-8"))
    raw_signature = bytearray(base64.b64decode(signature_document["signature"], validate=True))
    raw_signature[0] ^= 1
    signature_document["signature"] = base64.b64encode(raw_signature).decode("ascii")
    tampered["policy_signature_path"].write_bytes(key_tool._json_bytes(signature_document))
    with pytest.raises(key_tool.KeyToolError) as bad_signature:
        key_tool.verify_manifest(
            manifest=signed_engine["manifest_path"],
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signed_engine["signature_path"],
            trust_policy=tampered["policy_path"],
            trust_policy_signature=tampered["policy_signature_path"],
        )
    assert bad_signature.value.code == "TRUST_POLICY_SIGNATURE_INVALID"

    production_policy = _policy_value(generated_key)
    production_policy["policy_id"] = "mentorflow-production-policy"
    production_policy["test_only"] = False
    test_root_artifact = _write_test_policy(
        e_key_workspace / "test-root-production-policy",
        generated_key,
        policy=production_policy,
    )
    with pytest.raises(key_tool.KeyToolError) as test_boundary:
        key_tool.verify_manifest(
            manifest=signed_engine["manifest_path"],
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signed_engine["signature_path"],
            trust_policy=test_root_artifact["policy_path"],
            trust_policy_signature=test_root_artifact["policy_signature_path"],
        )
    assert test_boundary.value.code == "TRUST_POLICY_TEST_BOUNDARY"

    production_root_key_id = "mentorflow-production-policy-root-v1"
    production_policy["root_key_id"] = production_root_key_id
    no_production_root = _write_test_policy(
        e_key_workspace / "production-root-unconfigured",
        generated_key,
        policy=production_policy,
        signature_root_key_id=production_root_key_id,
    )
    with pytest.raises(key_tool.KeyToolError) as production_blocked:
        key_tool.verify_manifest(
            manifest=signed_engine["manifest_path"],
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signed_engine["signature_path"],
            trust_policy=no_production_root["policy_path"],
            trust_policy_signature=no_production_root["policy_signature_path"],
        )
    assert production_blocked.value.code == "TRUST_ROOT_UNKNOWN"


def test_verify_cli_requires_signed_policy_and_rejects_registry_override(
    generated_key: dict[str, Any], signed_engine: dict[str, Any], capsys: pytest.CaptureFixture[str]
):
    base = [
        "--verify",
        "--manifest",
        str(signed_engine["manifest_path"]),
        "--public-key",
        str(generated_key["public_path"]),
        "--trust-receipt",
        str(generated_key["receipt_path"]),
        "--signature",
        str(signed_engine["signature_path"]),
    ]
    assert key_tool.main(base) == 2
    missing_both = json.loads(capsys.readouterr().err)
    assert missing_both["code"] == "ARGUMENT_MISSING"
    assert "--trust-policy" in missing_both["message"]

    assert key_tool.main(base + ["--trust-policy", str(generated_key["policy_path"])]) == 2
    missing_signature = json.loads(capsys.readouterr().err)
    assert missing_signature["code"] == "ARGUMENT_MISSING"
    assert "--trust-policy-signature" in missing_signature["message"]

    complete_policy = [
        "--trust-policy",
        str(generated_key["policy_path"]),
        "--trust-policy-signature",
        str(generated_key["policy_signature_path"]),
    ]
    assert key_tool.main(base + complete_policy + ["--revoked-key-id", generated_key["key_id"]]) == 2
    revoked = json.loads(capsys.readouterr().err)
    assert revoked["code"] == "TRUST_POLICY_REVOKED"

    assert key_tool.main(base + complete_policy) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"
    assert verified["integrity_verified"] is True
    assert verified["policy_signature_verified"] is True
    assert verified["trust_anchored"] is True

    with pytest.raises(SystemExit) as registry_override:
        key_tool.main(
            base
            + complete_policy
            + ["--trust-root-registry", str(generated_key["policy_path"])]
        )
    assert registry_override.value.code == 2


def test_tampered_manifest_fails_and_existing_signature_is_never_overwritten(
    e_key_workspace: Path, generated_key: dict[str, Any], signed_engine: dict[str, Any]
):
    tampered_case = e_key_workspace / "tampered-manifest"
    tampered_case.mkdir()
    manifest_path = tampered_case / "engine-manifest.json"
    signature_path = tampered_case / "engine-manifest.sig"
    manifest_path.write_bytes(signed_engine["raw_manifest"] + b" ")
    shutil.copyfile(signed_engine["signature_path"], signature_path)
    with pytest.raises(key_tool.KeyToolError) as tampered:
        key_tool.verify_manifest(
            manifest=manifest_path,
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signature_path,
            **_trust_chain(generated_key),
        )
    assert tampered.value.code == "SIGNATURE_INVALID"

    original_signature = signed_engine["signature_path"].read_bytes()
    with pytest.raises(key_tool.KeyToolError) as overwrite:
        key_tool.sign_manifest(
            manifest=signed_engine["manifest_path"],
            key_root=generated_key["key_root"],
            signature_out=signed_engine["signature_path"],
        )
    assert overwrite.value.code == "OUTPUT_EXISTS"
    assert signed_engine["signature_path"].read_bytes() == original_signature


def test_signature_key_id_is_receipt_derived_and_cli_rejects_caller_override(
    generated_key: dict[str, Any], signed_engine: dict[str, Any], capsys: pytest.CaptureFixture[str]
):
    receipt = json.loads(generated_key["receipt_path"].read_text(encoding="utf-8"))
    signature_document = json.loads(signed_engine["signature_path"].read_text(encoding="utf-8"))
    assert signature_document["key_id"] == receipt["key_id"]

    assert (
        key_tool.main(
            [
                "--sign-manifest",
                "--manifest",
                str(signed_engine["manifest_path"]),
                "--key-root",
                str(generated_key["key_root"]),
                "--signature-out",
                str(signed_engine["case"] / "other.sig"),
                "--key-id",
                "attacker-selected-key-id",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["code"] == "KEY_ID_CALLER_FORBIDDEN"
    assert not re.search(r"(?i)[a-z]:[\\/]", captured.err)


@pytest.mark.parametrize(
    ("name", "mutate", "resign", "expected_code"),
    [
        (
            "receipt-self-signature",
            lambda value: value.__setitem__("created_at_utc", "2026-08-07T00:00:00Z"),
            False,
            "TRUST_RECEIPT_SIGNATURE_INVALID",
        ),
        (
            "receipt-fingerprint",
            lambda value: value["fingerprint"].__setitem__("value", "0" * 64),
            True,
            "PUBLIC_FINGERPRINT_MISMATCH",
        ),
        (
            "receipt-rotation",
            lambda value: value["rotation"].__setitem__("overlap_until", "2026-09-16"),
            True,
            "ROTATION_INVALID",
        ),
        (
            "receipt-validity",
            lambda value: value["validity"]["signing"].__setitem__("not_after", "2026-09-02"),
            True,
            "VALIDITY_INVALID",
        ),
        (
            "receipt-status",
            lambda value: value.__setitem__("status", "active"),
            True,
            "TRUST_STATUS_INVALID",
        ),
        (
            "receipt-key-id-test-binding",
            lambda value: value.__setitem__("key_id", "production-looking-key"),
            True,
            "TRUST_STATUS_INVALID",
        ),
        (
            "receipt-created-offset",
            lambda value: value.__setitem__("created_at_utc", "2026-08-07T08:00:00+08:00"),
            True,
            "TRUST_RECEIPT_INVALID",
        ),
        (
            "receipt-created-fractional",
            lambda value: value.__setitem__("created_at_utc", "2026-08-07T00:00:00.000Z"),
            True,
            "TRUST_RECEIPT_INVALID",
        ),
    ],
)
def test_receipt_self_signature_and_internal_consistency_fail_closed(
    e_key_workspace: Path,
    generated_key: dict[str, Any],
    signed_engine: dict[str, Any],
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    resign: bool,
    expected_code: str,
):
    receipt = json.loads(generated_key["receipt_path"].read_text(encoding="utf-8"))
    mutate(receipt)
    if resign:
        receipt = _resign_receipt(receipt, _load_fixture_private(generated_key["private_path"]))
    public_path, receipt_path = _public_receipt_copy(e_key_workspace, generated_key, name, receipt)
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool.verify_manifest(
            manifest=signed_engine["manifest_path"],
            public_key=public_path,
            trust_receipt=receipt_path,
            signature=signed_engine["signature_path"],
            **_trust_chain(generated_key),
        )
    assert caught.value.code == expected_code


def test_manifest_schema_and_key_purpose_scope_matrix_including_catalog(
    e_key_workspace: Path, generated_key: dict[str, Any]
):
    private_key = _load_fixture_private(generated_key["private_path"])
    receipt = json.loads(generated_key["receipt_path"].read_text(encoding="utf-8"))
    scopes = {
        "engine": "engine-manifest.json",
        "voice": "voice-pack.json",
        "engine-catalog": "engine-catalog.json",
        "voice-catalog": "voice-pack-catalog.json",
    }
    allowed = {
        "mentorflow-engine-pack-release": {"engine"},
        "mentorflow-voice-pack-release": {"voice"},
        "mentorflow-voice-and-engine-release": {"engine", "voice"},
        "mentorflow-release-catalog": {"engine-catalog", "voice-catalog"},
    }
    manifests: dict[str, tuple[Path, Path]] = {}
    for scope, filename in scopes.items():
        case = e_key_workspace / f"matrix-manifest-{scope}"
        case.mkdir()
        manifest_path = case / filename
        raw = _write_manifest(manifest_path, scope)
        signature_path = case / key_tool.MANIFEST_CONTRACTS[filename]["signature"]
        _write_signature(signature_path, private_key, raw, receipt["key_id"])
        manifests[scope] = manifest_path, signature_path

    for purpose, allowed_scopes in allowed.items():
        purpose_receipt = deepcopy(receipt)
        purpose_receipt["purpose"] = purpose
        purpose_receipt = _resign_receipt(purpose_receipt, private_key)
        public_path, receipt_path = _public_receipt_copy(
            e_key_workspace,
            generated_key,
            f"matrix-trust-{purpose}",
            purpose_receipt,
        )
        for scope, (manifest_path, signature_path) in manifests.items():
            arguments = {
                "manifest": manifest_path,
                "public_key": public_path,
                "trust_receipt": receipt_path,
                "signature": signature_path,
                **_trust_chain(generated_key),
            }
            if scope in allowed_scopes:
                verified = key_tool.verify_manifest(**arguments)
                assert verified["status"] == "verified", (purpose, scope)
                assert verified["trust_anchored"] is True
            else:
                with pytest.raises(key_tool.KeyToolError) as mismatch:
                    key_tool.verify_manifest(**arguments)
                assert mismatch.value.code == "KEY_PURPOSE_MISMATCH", (purpose, scope)


def test_manifest_filename_and_schema_must_match(
    e_key_workspace: Path, generated_key: dict[str, Any], signed_engine: dict[str, Any]
):
    invalid_schema_case = e_key_workspace / "schema-mismatch"
    invalid_schema_case.mkdir()
    invalid_manifest = invalid_schema_case / "voice-pack.json"
    invalid_manifest.write_bytes(signed_engine["raw_manifest"])
    invalid_signature = invalid_schema_case / "voice-pack.sig"
    shutil.copyfile(signed_engine["signature_path"], invalid_signature)
    with pytest.raises(key_tool.KeyToolError) as schema_mismatch:
        key_tool.verify_manifest(
            manifest=invalid_manifest,
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=invalid_signature,
            **_trust_chain(generated_key),
        )
    assert schema_mismatch.value.code == "MANIFEST_SCHEMA_INVALID"


def test_test_only_key_cannot_sign_production_manifest(
    e_key_workspace: Path, generated_key: dict[str, Any]
):
    case = e_key_workspace / "production-manifest"
    case.mkdir()
    manifest_path = case / "engine-manifest.json"
    raw_manifest = _write_manifest(manifest_path, "engine", production=True)
    signature_path = case / "engine-manifest.sig"
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool.sign_manifest(
            manifest=manifest_path,
            key_root=generated_key["key_root"],
            signature_out=signature_path,
        )
    assert caught.value.code == "TEST_KEY_PRODUCTION_FORBIDDEN"
    assert not signature_path.exists()

    _write_signature(
        signature_path,
        _load_fixture_private(generated_key["private_path"]),
        raw_manifest,
        generated_key["key_id"],
    )
    with pytest.raises(key_tool.KeyToolError) as verify_caught:
        key_tool.verify_manifest(
            manifest=manifest_path,
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signature_path,
            **_trust_chain(generated_key),
        )
    assert verify_caught.value.code == "TEST_KEY_PRODUCTION_FORBIDDEN"


def test_sign_and_verify_reject_outside_rotation_windows(
    e_key_workspace: Path,
    generated_key: dict[str, Any],
    signed_engine: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    policy = _policy_value(generated_key)
    policy["created_at_utc"] = "2026-07-01T00:00:00Z"
    policy["validity"] = {
        "not_before": "2026-07-01T00:00:00Z",
        "not_after": "2026-10-01T00:00:00Z",
    }
    policy_artifact = _write_test_policy(
        e_key_workspace / "leaf-rotation-window-policy",
        generated_key,
        policy=policy,
    )
    trust_chain = {
        "trust_policy": policy_artifact["policy_path"],
        "trust_policy_signature": policy_artifact["policy_signature_path"],
    }
    monkeypatch.setattr(
        key_tool,
        "_utc_now",
        lambda: datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(key_tool.KeyToolError) as too_early:
        key_tool.verify_manifest(
            manifest=signed_engine["manifest_path"],
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signed_engine["signature_path"],
            **trust_chain,
        )
    assert too_early.value.code == "KEY_OUTSIDE_VALIDITY"

    late_sign_case = e_key_workspace / "late-sign"
    late_sign_case.mkdir()
    late_manifest = late_sign_case / "engine-manifest.json"
    _write_manifest(late_manifest, "engine")
    late_signature = late_sign_case / "engine-manifest.sig"
    monkeypatch.setattr(
        key_tool,
        "_utc_now",
        lambda: datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(key_tool.KeyToolError) as late_sign:
        key_tool.sign_manifest(
            manifest=late_manifest,
            key_root=generated_key["key_root"],
            signature_out=late_signature,
        )
    assert late_sign.value.code == "KEY_OUTSIDE_VALIDITY"
    assert not late_signature.exists()

    monkeypatch.setattr(
        key_tool,
        "_utc_now",
        lambda: datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(key_tool.KeyToolError) as late_verify:
        key_tool.verify_manifest(
            manifest=signed_engine["manifest_path"],
            public_key=generated_key["public_path"],
            trust_receipt=generated_key["receipt_path"],
            signature=signed_engine["signature_path"],
            **trust_chain,
        )
    assert late_verify.value.code == "KEY_OUTSIDE_VALIDITY"


def test_sign_rejects_indexed_private_key_public_mismatch(e_key_workspace: Path):
    key_root = e_key_workspace / "keys-private-mismatch"
    key_tool.generate_release_key(
        **_request(key_root, key_id="test-release-key-20260808-private-mismatch")
    )
    paths = _index_paths(key_root)
    original = paths["private_path"].read_bytes()
    replacement_key = Ed25519PrivateKey.generate()
    replacement = replacement_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    assert len(replacement) == len(original)
    paths["private_path"].write_bytes(replacement)
    key_tool._apply_and_verify_acl(paths["private_path"], apply=False)

    case = e_key_workspace / "private-mismatch-sign"
    case.mkdir()
    manifest_path = case / "engine-manifest.json"
    _write_manifest(manifest_path, "engine")
    signature_path = case / "engine-manifest.sig"
    with pytest.raises(key_tool.KeyToolError) as mismatch:
        key_tool.sign_manifest(manifest=manifest_path, key_root=key_root, signature_out=signature_path)
    assert mismatch.value.code == "KEY_PAIR_MISMATCH"
    assert not signature_path.exists()


def test_hardlinks_and_simulated_path_replacement_are_rejected(
    e_key_workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    case = e_key_workspace / "fileid-attacks"
    case.mkdir()
    hardlink_source = case / "source.bin"
    hardlink_path = case / "hardlink.bin"
    hardlink_source.write_bytes(b"fixture")
    os.link(hardlink_source, hardlink_path)
    with pytest.raises(key_tool.KeyToolError) as hardlink:
        key_tool._secure_read(hardlink_path, field="hardlink fixture", maximum_bytes=64)
    assert hardlink.value.code == "HARDLINK_FORBIDDEN"

    rebound_path = case / "rebound.bin"
    rebound_path.write_bytes(b"fixed bytes")
    actual = key_tool._identity_from_path(rebound_path, directory=False, field="rebound fixture")
    fake_file_id = "f" * 16 if actual.file_id != "f" * 16 else "e" * 16
    fake = replace(actual, file_id=fake_file_id)
    with pytest.raises(key_tool.KeyToolError) as indexed_replacement:
        key_tool._secure_read(
            rebound_path,
            field="indexed rebound fixture",
            maximum_bytes=64,
            expected_identity=fake,
        )
    assert indexed_replacement.value.code == "KEY_INDEX_IDENTITY_MISMATCH"

    real_identity_from_path = key_tool._identity_from_path

    def simulated_rebound(path: Path, *, directory: bool, field: str) -> key_tool.FileIdentity:
        if path == rebound_path:
            return fake
        return real_identity_from_path(path, directory=directory, field=field)

    monkeypatch.setattr(key_tool, "_identity_from_path", simulated_rebound)
    with pytest.raises(key_tool.KeyToolError) as rebound:
        key_tool._secure_read(rebound_path, field="rebound fixture", maximum_bytes=64)
    assert rebound.value.code == "FILE_IDENTITY_CHANGED"


def test_cleanup_deletes_only_the_exact_recorded_fileid(e_key_workspace: Path):
    case = e_key_workspace / "cleanup-fileid"
    case.mkdir()
    target = case / "created.bin"
    identity = key_tool._exclusive_write(target, b"original", protect_acl=False)
    moved_original = case / "moved-original.bin"
    os.replace(target, moved_original)
    target.write_bytes(b"replacement")

    with pytest.raises(key_tool.KeyToolError) as cleanup:
        key_tool._cleanup_created([key_tool.CreatedObject(target, identity, False)])
    assert cleanup.value.code == "CLEANUP_FAILED"
    assert target.read_bytes() == b"replacement"
    assert moved_original.read_bytes() == b"original"

    exact_target = case / "exact.bin"
    exact_identity = key_tool._exclusive_write(exact_target, b"exact", protect_acl=False)
    key_tool._cleanup_created([key_tool.CreatedObject(exact_target, exact_identity, False)])
    assert not exact_target.exists()


def test_acl_failure_cleans_empty_generation_root(e_key_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    output_root = e_key_workspace / "keys-fail-closed"

    def fail_acl(_path: Path, *, apply: bool) -> dict[str, Any]:
        assert apply is True
        raise key_tool.KeyToolError("ACL_VERIFICATION_FAILED", "fixture ACL failure")

    monkeypatch.setattr(key_tool, "_apply_and_verify_acl", fail_acl)
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool.generate_release_key(
            **_request(output_root, key_id="test-release-key-20260808-acl-root")
        )
    assert caught.value.code == "ACL_VERIFICATION_FAILED"
    assert not output_root.exists()


def test_private_file_acl_failure_removes_key_bytes_and_root(
    e_key_workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    output_root = e_key_workspace / "keys-file-acl-fail-closed"
    real_acl = key_tool._apply_and_verify_acl

    def fail_private_file_acl(path: Path, *, apply: bool) -> dict[str, Any]:
        if path.is_file():
            raise key_tool.KeyToolError("ACL_VERIFICATION_FAILED", "fixture file ACL failure")
        return real_acl(path, apply=apply)

    monkeypatch.setattr(key_tool, "_apply_and_verify_acl", fail_private_file_acl)
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool.generate_release_key(
            **_request(output_root, key_id="test-release-key-20260808-acl-file")
        )
    assert caught.value.code == "ACL_VERIFICATION_FAILED"
    assert not output_root.exists()


def test_reparse_point_is_rejected_when_windows_allows_test_symlink(e_key_workspace: Path):
    target = e_key_workspace / "real"
    target.mkdir()
    link = e_key_workspace / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("test account cannot create a Windows symlink")
    candidate = link / "private.pem"
    with pytest.raises(key_tool.KeyToolError) as caught:
        key_tool._validate_private_location(candidate, must_exist=False)
    assert caught.value.code == "REPARSE_FORBIDDEN"
