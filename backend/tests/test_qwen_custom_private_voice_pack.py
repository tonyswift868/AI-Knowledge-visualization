from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.app import teacher_voice_pack as voice_pack
from backend.app import qwen_custom_private_runtime as private_runtime


def _write(root: Path, relative: str, data: bytes) -> dict:
    path = root / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def private_manifest(root: Path) -> dict:
    model_paths = (
        "model/config.json", "model/generation_config.json", "model/merges.txt",
        "model/model.safetensors", "model/preprocessor_config.json",
        "model/tokenizer_config.json", "model/vocab.json",
        "model/speech_tokenizer/config.json", "model/speech_tokenizer/configuration.json",
        "model/speech_tokenizer/model.safetensors", "model/speech_tokenizer/preprocessor_config.json",
    )
    files = []
    config = json.dumps({
        "tts_model_type": "custom_voice",
        "talker_config": {"spk_id": {"tsubaki_teacher": 3000}, "spk_is_dialect": {"tsubaki_teacher": False}},
    }).encode()
    for relative in model_paths:
        item = _write(root, relative, config if relative == "model/config.json" else relative.encode())
        files.append({**item, "role": "model_asset"})
    for relative in ("evidence/CHECKPOINT_RECEIPT.json", "evidence/TRAINER_STATE.json", "evidence/target_speaker.safetensors"):
        files.append({**_write(root, relative, relative.encode()), "role": "checkpoint_receipt"})
    files.append({**_write(root, "evidence/LOCAL_PRIVATE_ADMISSION_RECEIPT.json", b"rights"), "role": "rights_evidence"})
    files.append({**_write(root, "evidence/INFERENCE_RECEIPT.json", b"qa"), "role": "qa_evidence"})
    return {
        "schema": voice_pack.PACK_SCHEMA,
        "pack_id": "tsubaki-private-local",
        "version": "0.1.0-prerelease",
        "identity": {"kind": "declared_third_party_character_voice", "display_name": "椿（本机私有预发布）", "impersonates": True, "source_identity": "椿"},
        "rights": {
            "status": "restricted", "scope": "private_local_only",
            "grants": {"training": True, "private_local_inference": True, "weight_distribution": False, "reference_audio_distribution": False, "commercial_synthesis": False, "public_release": False},
            "evidence": ["evidence/LOCAL_PRIVATE_ADMISSION_RECEIPT.json"],
        },
        "qa": {"status": "technical_pass_human_review_pending", "checkpoint_integrity_verified": True, "audio_format_verified": True, "human_listening_review": "pending", "evidence": ["evidence/INFERENCE_RECEIPT.json"]},
        "runtime": {"adapter": voice_pack.QWEN_CUSTOM_PRIVATE_ADAPTER_ID, "dependency": {"kind": "qwen3-tts-12hz-portable-private", "registration_id": "tsubaki-private-runtime", "portable_runtime_receipt_sha256": "a" * 64}},
        "styles": {name: {"speaker": "tsubaki_teacher", "language": "Chinese", "behavior": "neutral_same_voice"} for name in ("lively", "relaxed")},
        "files": files,
        "private_prerelease": {"scope": "private_local_only_nonexportable", "exportable": False, "public_release": False, "commercial_use": False, "default_eligible": True},
    }


def test_private_manifest_accepts_exact_nonexportable_model_and_files(tmp_path: Path):
    manifest = private_manifest(tmp_path)
    validated = voice_pack.validate_voice_pack_manifest(manifest, payload_root=tmp_path, verify_files=True)
    assert validated["private_prerelease"]["exportable"] is False
    assert validated["rights"]["grants"]["weight_distribution"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("private_prerelease", "public_release"), True),
        (("rights", "grants", "commercial_synthesis"), True),
        (("rights", "grants", "weight_distribution"), True),
        (("identity", "impersonates"), False),
    ],
)
def test_private_manifest_rejects_rights_or_identity_escalation(tmp_path: Path, path: tuple[str, ...], value):
    manifest = private_manifest(tmp_path)
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(voice_pack.TeacherVoicePackError):
        voice_pack.validate_voice_pack_manifest(manifest, payload_root=tmp_path, verify_files=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [("release_channel", "stable"), ("usage_scope", "public"), ("default", False)],
)
def test_private_catalog_rejects_public_or_nondefault_labels(monkeypatch, tmp_path: Path, field: str, value):
    entry = {
        "pack_id": "tsubaki-private-local", "version": "0.1.0-prerelease",
        "runtime_adapter": voice_pack.QWEN_CUSTOM_PRIVATE_ADAPTER_ID,
        "scope": "private_local_only_nonexportable", "usage_scope": "local_private",
        "release_channel": "prerelease", "default": True,
        "source": {"kind": "bundled", "archive_path": "bundled/pack.zip"},
        "archive": {"bytes": 1, "sha256": "a" * 64}, "signing_key_id": "private-key",
    }
    entry[field] = value
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema": voice_pack.CATALOG_SCHEMA, "https_allowed_hosts": [], "trusted_keys": [], "packs": [entry]}), encoding="utf-8")
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_CATALOG", str(catalog))
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack._load_catalog()
    assert raised.value.code == "VOICE_PACK_PRIVATE_CATALOG_INVALID"


def test_private_catalog_rejects_https_before_download(monkeypatch, tmp_path: Path):
    entry = {
        "pack_id": "tsubaki-private-local", "version": "0.1.0-prerelease",
        "runtime_adapter": voice_pack.QWEN_CUSTOM_PRIVATE_ADAPTER_ID,
        "scope": "private_local_only_nonexportable", "usage_scope": "local_private",
        "release_channel": "prerelease", "default": True,
        "source": {"kind": "https", "url": "https://example.invalid/pack.zip"},
        "archive": {"bytes": 1, "sha256": "a" * 64}, "signing_key_id": "private-key",
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema": voice_pack.CATALOG_SCHEMA, "https_allowed_hosts": ["example.invalid"], "trusted_keys": [], "packs": [entry]}), encoding="utf-8")
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_CATALOG", str(catalog))
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack._load_catalog()
    assert raised.value.code == "VOICE_PACK_PRIVATE_CATALOG_INVALID"


def test_public_pack_exposes_private_scope(tmp_path: Path):
    public = voice_pack._public_pack(private_manifest(tmp_path), state="active")
    assert public["scope"] == "private_local_only_nonexportable"
    assert public["release_channel"] == "prerelease"
    assert public["usage_scope"] == "local_private"


def _private_runtime_registration(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "component"
    for relative in (private_runtime.PYTHON, private_runtime.RUNNER, private_runtime.BASE_RUNNER, private_runtime.DEPENDENCY_LOCK, private_runtime.OVERLAY_CONTRACT):
        _write(root, relative, relative.encode())
    portable_receipt_path = root / "metadata" / "PRIVATE_PORTABLE_RUNTIME_RECEIPT.json"
    portable_receipt_path.write_text(json.dumps({
        "schema": private_runtime.PORTABLE_RECEIPT_SCHEMA,
        "runtime_kind": "qwen3-tts-12hz-portable-private",
        "python_version": "3.12.10",
        "python_sha256": hashlib.sha256(private_runtime.PYTHON.encode()).hexdigest(),
        "dependency_lock_sha256": hashlib.sha256(private_runtime.DEPENDENCY_LOCK.encode()).hexdigest(),
        "overlay_contract_sha256": hashlib.sha256(private_runtime.OVERLAY_CONTRACT.encode()).hexdigest(),
        "offline_only": True,
    }), encoding="utf-8")
    tree = private_runtime._runtime_tree(root)
    component = {
        "schema": private_runtime.COMPONENT_SCHEMA,
        "registration_id": "test-private-runtime",
        "scope": "private_local_only_nonexportable",
        "portable_receipt_path": "metadata/PRIVATE_PORTABLE_RUNTIME_RECEIPT.json",
        "portable_receipt_sha256": hashlib.sha256(portable_receipt_path.read_bytes()).hexdigest(),
        "python_sha256": hashlib.sha256(private_runtime.PYTHON.encode()).hexdigest(),
        "runner_sha256": hashlib.sha256(private_runtime.RUNNER.encode()).hexdigest(),
        "base_runner_sha256": hashlib.sha256(private_runtime.BASE_RUNNER.encode()).hexdigest(),
        "tree": tree,
    }
    component_path = root / "PRIVATE_RUNTIME_COMPONENT.json"
    component_path.write_text(json.dumps(component), encoding="utf-8")
    signing_key = Ed25519PrivateKey.generate()
    public_key = signing_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    registration = tmp_path / "private-runtime-registration.json"
    registration.write_text(json.dumps({
        "schema": private_runtime.REGISTRATION_SCHEMA,
        "enabled": True,
        "registration_id": "test-private-runtime",
        "runtime_root": str(root),
        "component_sha256": hashlib.sha256(component_path.read_bytes()).hexdigest(),
        "component_signature": __import__("base64").b64encode(signing_key.sign(component_path.read_bytes())).decode("ascii"),
        "signing_key_id": "tsubaki-private-local-prerelease-key",
        "public_key": __import__("base64").b64encode(public_key).decode("ascii"),
        "cache_root": str(tmp_path / "cache"),
        "port": 18893,
    }), encoding="utf-8")
    return registration, root


def test_private_registration_defaults_next_to_catalog_and_full_tree_is_cached(monkeypatch, tmp_path: Path):
    registration, _root = _private_runtime_registration(tmp_path)
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    monkeypatch.delenv(private_runtime.REGISTRATION_ENV, raising=False)
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_CATALOG", str(catalog))
    monkeypatch.setattr(private_runtime, "_REGISTRATION_CACHE_KEY", None)
    monkeypatch.setattr(private_runtime, "_REGISTRATION_CACHE", None)
    original = private_runtime._runtime_tree
    calls = 0

    def counted(root: Path):
        nonlocal calls
        calls += 1
        return original(root)

    monkeypatch.setattr(private_runtime, "_runtime_tree", counted)
    first = private_runtime.load_registration()
    second = private_runtime.load_registration()
    assert first["runtime_root"] == second["runtime_root"]
    assert private_runtime._registration_path() == registration
    assert calls == 1


def test_persistent_server_reuses_one_loaded_backend_for_two_texts():
    server_path = Path(__file__).resolve().parents[1] / "runtime" / "qwen_custom_private_server.py"
    spec = importlib.util.spec_from_file_location("test_qwen_custom_private_server", server_path)
    assert spec is not None and spec.loader is not None
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)

    class FakeBackend:
        def __init__(self):
            self.model = None
            self.model_load_count = 0

        def synthesize(self, text: str):
            if self.model is None:
                self.model = object()
                self.model_load_count += 1
            raw = (b"RIFF" + text.encode("utf-8"))
            return raw, {
                "loaded_model_sha256": "c" * 64,
                "speaker_slot_exact": True,
                "model_load_count": self.model_load_count,
                "sample_rate": 24000,
            }

    model_root = Path(__file__).resolve().parent
    backend = FakeBackend()
    state = server.CustomRuntimeState(backend, model_root, "c" * 64)
    binding = {
        "pack_id": "tsubaki-private-local", "pack_version": "0.1.0-prerelease",
        "pack_manifest_sha256": "d" * 64, "model_root": str(model_root),
        "model_sha256": "c" * 64, "speaker": "tsubaki_teacher",
        "speaker_slot": 3000, "scope": "private_local_only_nonexportable",
    }
    state.prepare({"schema": server.PREPARE_SCHEMA, "binding": binding})
    receipts = []
    for text in ("第一条。", "第二条不同文本。"):
        result = state.synthesize({
            "schema": server.SYNTHESIS_SCHEMA, "binding": binding,
            "text": text, "style": "lively", "reply_kind": "teacher",
        })
        receipts.append(result["receipt"])
    assert [item["model_load_count"] for item in receipts] == [1, 1]
    assert receipts[0]["text_sha256"] != receipts[1]["text_sha256"]
    assert all(item["service_scope"] == "owned_loopback_internal" for item in receipts)


def test_active_private_pack_warm_click_reuses_identity_cache_and_tamper_invalidates(monkeypatch, tmp_path: Path):
    root = tmp_path / "packs"
    installed = root / "tsubaki-private-local" / "0.1.0-prerelease"
    installed.mkdir(parents=True)
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text("{}", encoding="utf-8")
    active_file = root / ".state" / "active.json"
    active_file.parent.mkdir(parents=True)
    active_file.write_text("{}", encoding="utf-8")
    manifest_file = installed / "voice-pack.json"
    manifest_file.write_text("manifest", encoding="utf-8")
    (installed / "voice-pack.sig").write_text("signature", encoding="utf-8")
    (installed / ".mentorflow-install.json").write_text("receipt", encoding="utf-8")
    payload = installed / "model" / "model.safetensors"
    payload.parent.mkdir()
    payload.write_bytes(b"trained-model")
    manifest_hash = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
    active = {"pack_id": "tsubaki-private-local", "version": "0.1.0-prerelease", "manifest_sha256": manifest_hash}
    manifest = {"pack_id": active["pack_id"], "version": active["version"], "files": [{"path": "model/model.safetensors"}]}
    entry = {"pack_id": active["pack_id"], "version": active["version"]}
    calls = 0

    def load_installed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise voice_pack.TeacherVoicePackError("VOICE_PACK_FILE_HASH_MISMATCH", "tampered")
        return manifest

    monkeypatch.setattr(voice_pack, "_ACTIVE_VERIFIED_CACHE", None)
    monkeypatch.setattr(voice_pack, "_load_catalog", lambda: (catalog_file, {"packs": [entry]}))
    monkeypatch.setattr(voice_pack, "_pack_root", lambda: root)
    monkeypatch.setattr(voice_pack, "_active_reference", lambda _root: active)
    monkeypatch.setattr(voice_pack, "_active_path", lambda _root: active_file)
    monkeypatch.setattr(voice_pack, "_catalog_entry", lambda *_args: entry)
    monkeypatch.setattr(voice_pack, "_load_installed", load_installed)
    assert voice_pack._active_manifest(verify_files=True)[0] is manifest
    assert voice_pack._active_manifest(verify_files=True)[0] is manifest
    assert calls == 1
    payload.write_bytes(b"tampered-model")
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack._active_manifest(verify_files=True)
    assert raised.value.code == "VOICE_PACK_FILE_HASH_MISMATCH"
    assert calls == 2
