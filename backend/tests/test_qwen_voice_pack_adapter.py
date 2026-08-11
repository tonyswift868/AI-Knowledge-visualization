from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import threading
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import teacher_voice_pack as voice_pack
from scripts import build_qwen3_tts_runtime_pack as runtime_pack_builder


PACK_ID = "mentorflow-original-qwen-teacher"
ENGINE_ID = "mentorflow-qwen3-tts-base"
ENGINE_VERSION = "0.6b-12hz-v1"
KEY_ID = "mentorflow-qwen-test-key"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(
    bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
)
PUBLIC_KEY = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)


def _pcm_wav(*, frames: int, sample: int = 1800, rate: int = 24_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        frame = int(sample).to_bytes(2, "little", signed=True)
        wav_file.writeframes(frame * frames)
    return output.getvalue()


def _write_payload(root: Path, relative: str, content: bytes) -> dict:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    role = {
        "references": "reference_audio",
        "rights": "rights_evidence",
        "qa": "qa_evidence",
    }[relative.split("/", 1)[0]]
    return {
        "path": relative,
        "role": role,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _qwen_pack(
    root: Path,
    *,
    engine_manifest_sha256: str,
    version: str = "1.0.0",
) -> dict:
    files = [
        _write_payload(
            root,
            "references/lively.wav",
            _pcm_wav(frames=2_400, sample=2100),
        ),
        _write_payload(
            root,
            "references/relaxed.wav",
            _pcm_wav(frames=3_000, sample=1200),
        ),
        _write_payload(
            root,
            "rights/release.json",
            b'{"scope":"voice-pack-distribution"}',
        ),
        _write_payload(
            root,
            "qa/release.json",
            b'{"transcript_alignment":"verified"}',
        ),
    ]
    return {
        "schema": voice_pack.PACK_SCHEMA,
        "pack_id": PACK_ID,
        "version": version,
        "identity": {
            "kind": "original",
            "display_name": "MentorFlow 原创 Qwen 教师声线",
            "impersonates": False,
            "source_identity": None,
        },
        "rights": {
            "status": "verified",
            "covers": sorted(voice_pack.REQUIRED_RIGHTS),
            "evidence": ["rights/release.json"],
        },
        "qa": {
            "status": "passed",
            "transcript_alignment_verified": True,
            "reference_audio_reviewed": True,
            "original_identity_reviewed": True,
            "evidence": ["qa/release.json"],
        },
        "runtime": {
            "adapter": voice_pack.QWEN_BASE_CLONE_ADAPTER_ID,
            "engine_dependency": {
                "kind": voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
                "catalog_id": ENGINE_ID,
                "version": ENGINE_VERSION,
                "manifest_sha256": engine_manifest_sha256,
            },
        },
        "styles": {
            "lively": {
                "reference_audio": "references/lively.wav",
                "language": "zh",
                "transcript": "太好了，我们把这个关键点一起拿下。",
            },
            "relaxed": {
                "reference_audio": "references/relaxed.wav",
                "language": "zh",
                "transcript": "先别着急，我们慢慢理清这里的关系。",
            },
        },
        "files": files,
    }


def _sign(raw: bytes, *, invalid: bool = False) -> bytes:
    signature = PRIVATE_KEY.sign(raw)
    if invalid:
        signature = bytes([signature[0] ^ 1]) + signature[1:]
    return json.dumps(
        {
            "algorithm": "ed25519",
            "key_id": KEY_ID,
            "signature": base64.b64encode(signature).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _engine_release(
    tmp_path: Path,
    *,
    name: str = "engine",
    manifest_extra: dict | None = None,
    file_extra: dict | None = None,
    invalid_signature: bool = False,
    tamper_payload: bool = False,
    hardlink_model: bool = False,
    extra_empty_directory: bool = False,
    duplicate_case_path: bool = False,
) -> tuple[voice_pack.VerifiedQwenEngineRegistrationCapability, dict, Path, Path]:
    package_root = tmp_path / f"{name}-package"
    model_path = package_root / "model" / "model.safetensors"
    metadata_path = package_root / "metadata" / "release.json"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    model_payload = b"qwen-base-model-fixture"
    metadata_payload = b'{"model":"Qwen3-TTS-0.6B-Base"}'
    model_path.write_bytes(model_payload)
    metadata_path.write_bytes(metadata_payload)
    file_item = {
        "path": "model/model.safetensors",
        "role": "model_asset",
        "bytes": len(model_payload),
        "sha256": hashlib.sha256(model_payload).hexdigest(),
    }
    if file_extra:
        file_item.update(file_extra)
    manifest = {
        "schema": voice_pack.QWEN_ENGINE_MANIFEST_SCHEMA,
        "kind": voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
        "package_id": ENGINE_ID,
        "version": ENGINE_VERSION,
        "files": [
            file_item,
            {
                "path": "metadata/release.json",
                "role": "metadata",
                "bytes": len(metadata_payload),
                "sha256": hashlib.sha256(metadata_payload).hexdigest(),
            },
        ],
    }
    if duplicate_case_path:
        manifest["files"].append({**file_item, "path": "Model/model.safetensors"})
    manifest["install_path_budget"] = runtime_pack_builder.calculate_install_path_budget(
        [
            *(item["path"] for item in manifest["files"]),
            "engine-manifest.json",
            "engine-manifest.sig",
        ]
    )
    if manifest_extra:
        manifest.update(manifest_extra)
    raw_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    (package_root / "engine-manifest.json").write_bytes(raw_manifest)
    (package_root / "engine-manifest.sig").write_bytes(
        _sign(raw_manifest, invalid=invalid_signature)
    )
    if tamper_payload:
        model_path.write_bytes(model_payload + b"tampered")
    if hardlink_model:
        outside = tmp_path / f"{name}-outside-model.safetensors"
        outside.write_bytes(model_path.read_bytes())
        model_path.unlink()
        try:
            os.link(outside, model_path)
        except OSError as exc:
            pytest.skip(f"hardlinks unavailable on this filesystem: {exc}")
    if extra_empty_directory:
        (package_root / "undeclared-empty").mkdir()

    archive_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_root).as_posix())
    catalog = {
        "schema": voice_pack.QWEN_ENGINE_CATALOG_SCHEMA,
        "engines": [
            {
                "kind": voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
                "package_id": ENGINE_ID,
                "version": ENGINE_VERSION,
                "archive": {
                    "bytes": archive_path.stat().st_size,
                    "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                },
                "manifest": {
                    "bytes": len(raw_manifest),
                    "sha256": hashlib.sha256(raw_manifest).hexdigest(),
                },
                "signing_key_id": KEY_ID,
            }
        ],
    }
    capability = voice_pack.verify_qwen_base_clone_engine_release(
        catalog=catalog,
        package_id=ENGINE_ID,
        version=ENGINE_VERSION,
        archive_path=archive_path,
        package_root=package_root,
        trusted_public_keys={KEY_ID: PUBLIC_KEY},
    )
    return capability, catalog, archive_path, package_root


def _prepare_receipt(
    request: voice_pack.QwenBaseClonePrepareRequest,
) -> voice_pack.QwenBaseClonePrepareReceipt:
    binding = request.binding
    return voice_pack.QwenBaseClonePrepareReceipt(
        schema=voice_pack.QWEN_BASE_CLONE_PREPARE_SCHEMA,
        ready=True,
        engine_kind=binding.engine.kind,
        engine_catalog_id=binding.engine.catalog_id,
        engine_version=binding.engine.version,
        engine_manifest_sha256=binding.engine.manifest_sha256,
        pack_id=binding.pack_id,
        pack_version=binding.version,
        pack_manifest_sha256=binding.manifest_sha256,
        binding_sha256=request.binding_sha256,
        references_sha256=request.references_sha256,
    )


class FakeQwenRuntime:
    def __init__(
        self,
        *,
        fail_prepare: bool = False,
        prepare_mismatch: tuple[str, object] | None = None,
        synthesis_mismatch: tuple[str, object] | None = None,
        invalid_audio: bool = False,
        bad_audio_receipt: bool = False,
    ) -> None:
        self.fail_prepare = fail_prepare
        self.prepare_mismatch = prepare_mismatch
        self.synthesis_mismatch = synthesis_mismatch
        self.invalid_audio = invalid_audio
        self.bad_audio_receipt = bad_audio_receipt
        self.prepare_calls: list[voice_pack.QwenBaseClonePrepareRequest] = []
        self.synthesis_calls: list[voice_pack.QwenBaseCloneSynthesisRequest] = []

    async def prepare(self, request: voice_pack.QwenBaseClonePrepareRequest):
        self.prepare_calls.append(request)
        if self.fail_prepare:
            raise RuntimeError("fixture prepare failure")
        receipt = _prepare_receipt(request)
        if self.prepare_mismatch:
            receipt = replace(receipt, **{self.prepare_mismatch[0]: self.prepare_mismatch[1]})
        return receipt

    async def synthesize(self, request: voice_pack.QwenBaseCloneSynthesisRequest):
        self.synthesis_calls.append(request)
        audio = b"RIFF" + b"invalid" * 8 if self.invalid_audio else _pcm_wav(frames=2_700)
        result = voice_pack.QwenBaseCloneRuntimeAudio(
            schema=voice_pack.QWEN_BASE_CLONE_SYNTHESIS_SCHEMA,
            audio=audio,
            media_type="audio/wav",
            engine_kind=request.engine_kind,
            engine_catalog_id=request.engine_catalog_id,
            engine_version=request.engine_version,
            engine_manifest_sha256=request.engine_manifest_sha256,
            pack_id=request.pack_id,
            pack_version=request.pack_version,
            pack_manifest_sha256=request.pack_manifest_sha256,
            binding_sha256=request.binding_sha256,
            style=request.style,
            reference_audio_sha256=request.reference_audio_sha256,
            reference_transcript_sha256=request.reference_transcript_sha256,
            request_text_sha256=request.text_sha256,
            reply_kind=request.reply_kind,
            request_sha256=request.request_sha256,
            audio_sha256=hashlib.sha256(audio).hexdigest(),
            audio_bytes=len(audio),
            profile=request.style,
            cache="miss",
        )
        if self.bad_audio_receipt:
            result = replace(result, audio_sha256="0" * 64)
        if self.synthesis_mismatch:
            result = replace(
                result,
                **{self.synthesis_mismatch[0]: self.synthesis_mismatch[1]},
            )
        return result


def _register_runtime(monkeypatch, capability, runtime) -> None:
    monkeypatch.setattr(voice_pack, "_QWEN_BASE_CLONE_RUNTIMES", {})
    voice_pack.register_qwen_base_clone_runtime(
        capability=capability,
        runtime=runtime,
    )


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("runtime", "weights_path"),
        ("dependency", "argv"),
        ("qa", "cwd"),
        ("lively", "binary"),
        ("relaxed", "module"),
        ("file", "checkpoint"),
        ("top", "download"),
        ("rights", "unknown_control"),
    ],
)
def test_qwen_voice_manifest_uses_recursive_exact_allowlists(
    tmp_path: Path, location: str, field: str
):
    pack = _qwen_pack(tmp_path, engine_manifest_sha256="1" * 64)
    target = {
        "runtime": pack["runtime"],
        "dependency": pack["runtime"]["engine_dependency"],
        "qa": pack["qa"],
        "lively": pack["styles"]["lively"],
        "relaxed": pack["styles"]["relaxed"],
        "file": pack["files"][0],
        "top": pack,
        "rights": pack["rights"],
    }[location]
    target[field] = "untrusted-control"
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack.validate_voice_pack_manifest(pack)
    assert raised.value.code in {
        "VOICE_PACK_QWEN_SCHEMA_INVALID",
        "VOICE_PACK_ENGINE_DEPENDENCY_INVALID",
    }


def test_qwen_references_require_distinct_path_digest_and_declared_bytes(tmp_path: Path):
    pack = _qwen_pack(tmp_path, engine_manifest_sha256="1" * 64)
    lively = (tmp_path / "references" / "lively.wav").read_bytes()
    relaxed_path = tmp_path / "references" / "relaxed.wav"
    relaxed_path.write_bytes(lively)
    relaxed_item = next(
        item for item in pack["files"] if item["path"] == "references/relaxed.wav"
    )
    relaxed_item["bytes"] = len(lively)
    relaxed_item["sha256"] = hashlib.sha256(lively).hexdigest()
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack.validate_voice_pack_manifest(
            pack,
            payload_root=tmp_path,
            verify_files=True,
        )
    assert raised.value.code == "VOICE_PACK_STYLES_INVALID"


def test_qwen_reference_must_be_parseable_pcm_not_riff_prefix(tmp_path: Path):
    pack = _qwen_pack(tmp_path, engine_manifest_sha256="1" * 64)
    invalid = b"RIFF" + b"not-a-wave" * 20
    path = tmp_path / "references" / "lively.wav"
    path.write_bytes(invalid)
    item = next(item for item in pack["files"] if item["path"] == "references/lively.wav")
    item["bytes"] = len(invalid)
    item["sha256"] = hashlib.sha256(invalid).hexdigest()
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack.validate_voice_pack_manifest(
            pack,
            payload_root=tmp_path,
            verify_files=True,
        )
    assert raised.value.code == "VOICE_PACK_FILE_INVALID"


def test_engine_release_verifier_returns_opaque_capability(tmp_path: Path):
    capability, _catalog, _archive, package_root = _engine_release(tmp_path)
    assert capability.is_valid() is True
    assert capability.dependency.catalog_id == ENGINE_ID
    assert capability.package_root == package_root.resolve()
    with pytest.raises(TypeError):
        voice_pack.VerifiedQwenEngineRegistrationCapability(
            object(),
            capability.dependency,
            package_root,
            capability.archive_sha256,
        )


@pytest.mark.parametrize("field", ["command", "url", "key", "argv", "module"])
def test_engine_manifest_forbids_unknown_control_fields(tmp_path: Path, field: str):
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        _engine_release(tmp_path, manifest_extra={field: "forbidden"})
    assert raised.value.code == "VOICE_ENGINE_MANIFEST_INVALID"


def test_engine_release_rejects_bad_signature_and_file_hash(tmp_path: Path):
    with pytest.raises(voice_pack.TeacherVoicePackError) as signature_error:
        _engine_release(tmp_path, name="bad-signature", invalid_signature=True)
    assert signature_error.value.code == "VOICE_ENGINE_SIGNATURE_INVALID"
    with pytest.raises(voice_pack.TeacherVoicePackError) as file_error:
        _engine_release(tmp_path, name="bad-file", tamper_payload=True)
    assert file_error.value.code == "VOICE_ENGINE_FILE_MISMATCH"


def test_engine_release_rejects_hardlinked_package_file(tmp_path: Path):
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        _engine_release(tmp_path, name="hardlink", hardlink_model=True)
    assert raised.value.code == "VOICE_ENGINE_HARDLINK_FORBIDDEN"


def test_engine_release_rejects_undeclared_empty_directory(tmp_path: Path):
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        _engine_release(tmp_path, name="empty-directory", extra_empty_directory=True)
    assert raised.value.code == "VOICE_ENGINE_PACKAGE_INVALID"
    assert raised.value.details["undeclared"] == ["undeclared-empty"]


def test_engine_manifest_rejects_case_insensitive_path_collision(tmp_path: Path):
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        _engine_release(tmp_path, name="case-collision", duplicate_case_path=True)
    assert raised.value.code == "VOICE_ENGINE_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "relative",
    [
        " model/model.bin",
        "model/model.bin ",
        "model/model.bin.",
        "model/model:ads.bin",
        "model/<bad>.bin",
        "model/quoted\".bin",
        "model/pipe|.bin",
        "model/query?.bin",
        "model/star*.bin",
        "model/control\x1f.bin",
        "model/COM1.bin",
        "model/lpt¹.json",
        "model/CONOUT$.txt",
        "model\\backslash.bin",
        "model//double.bin",
        "../escape.bin",
    ],
)
def test_engine_paths_use_shared_windows_safe_validator(relative: str):
    with pytest.raises(voice_pack.TeacherVoicePackError) as adapter_error:
        voice_pack._safe_relative(relative, "fixture.path")
    assert adapter_error.value.code == "VOICE_PACK_PATH_INVALID"
    with pytest.raises(runtime_pack_builder.BuildError) as builder_error:
        runtime_pack_builder._safe_relative(relative, field="fixture.path")
    assert getattr(builder_error.value, "code", "") in {
        "UNSAFE_PATH",
        "UNSAFE_WINDOWS_PATH",
    }


def test_engine_payload_is_rehashed_after_secure_enumeration(
    tmp_path: Path,
    monkeypatch,
):
    original = voice_pack._enumerate_secure_package_files
    changed = False

    def enumerate_then_change(root: Path):
        nonlocal changed
        snapshot = original(root)
        if not changed:
            changed = True
            (root / "model" / "model.safetensors").write_bytes(b"changed-after-enumeration")
        return snapshot

    monkeypatch.setattr(voice_pack, "_enumerate_secure_package_files", enumerate_then_change)
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        _engine_release(tmp_path, name="rehash-after-enumeration")
    assert raised.value.code == "VOICE_ENGINE_FILE_MISMATCH"


def test_engine_registration_rehashes_verified_root_before_accepting_runtime(
    tmp_path: Path,
    monkeypatch,
):
    capability, _catalog, _archive, package_root = _engine_release(
        tmp_path,
        name="stale-before-registration",
    )
    (package_root / "model" / "model.safetensors").write_bytes(b"changed-before-registration")
    monkeypatch.setattr(voice_pack, "_QWEN_BASE_CLONE_RUNTIMES", {})
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack.register_qwen_base_clone_runtime(
            capability=capability,
            runtime=FakeQwenRuntime(),
        )
    assert raised.value.code == "VOICE_ENGINE_FILE_MISMATCH"


def test_same_generation_adapter_prepare_does_not_repeat_full_engine_rehash(
    tmp_path: Path,
    monkeypatch,
):
    capability, _catalog, _archive, _package_root = _engine_release(
        tmp_path,
        name="same-generation-prepare",
    )
    runtime = FakeQwenRuntime()
    _register_runtime(monkeypatch, capability, runtime)
    monkeypatch.setattr(
        voice_pack,
        "_reverify_qwen_engine_capability",
        lambda _capability: pytest.fail("adapter must not rehash the full engine per click"),
    )
    voice_root = tmp_path / "voice-pack-same-generation"
    pack = _qwen_pack(
        voice_root,
        engine_manifest_sha256=capability.dependency.manifest_sha256,
    )
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()
    for _index in range(2):
        asyncio.run(
            adapter.prepare(pack, installed_root=voice_root, manifest_sha256="a" * 64)
        )
    assert len(runtime.prepare_calls) == 2


def test_engine_catalog_entry_is_strict_and_cannot_supply_url_or_command(tmp_path: Path):
    capability, catalog, archive_path, package_root = _engine_release(tmp_path)
    assert capability.is_valid()
    for field in ("url", "command", "public_key"):
        modified = deepcopy(catalog)
        modified["engines"][0][field] = "forbidden"
        with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
            voice_pack.verify_qwen_base_clone_engine_release(
                catalog=modified,
                package_id=ENGINE_ID,
                version=ENGINE_VERSION,
                archive_path=archive_path,
                package_root=package_root,
                trusted_public_keys={KEY_ID: PUBLIC_KEY},
            )
        assert raised.value.code == "VOICE_ENGINE_CATALOG_INVALID"


def test_adapter_binds_complete_engine_pack_reference_and_request_identity(
    monkeypatch, tmp_path: Path
):
    capability, *_ = _engine_release(tmp_path)
    runtime = FakeQwenRuntime()
    _register_runtime(monkeypatch, capability, runtime)
    pack = _qwen_pack(
        tmp_path / "voice-pack",
        engine_manifest_sha256=capability.dependency.manifest_sha256,
    )
    root = tmp_path / "voice-pack"
    voice_pack.validate_voice_pack_manifest(pack, payload_root=root, verify_files=True)
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()

    first = asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    repeated = asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    assert first["cached"] is False and repeated["cached"] is False
    assert len(runtime.prepare_calls) == 2
    prepare_request = runtime.prepare_calls[0]
    assert prepare_request.binding.engine == capability.dependency
    assert prepare_request.binding_sha256 == prepare_request.binding.binding_sha256
    assert prepare_request.references_sha256 == prepare_request.binding.references_sha256

    asyncio.run(
        adapter.synthesize(
            pack,
            text="请解释这个边界条件。",
            style="relaxed",
            reply_kind="teaching.explain",
            installed_root=root,
        )
    )
    request = runtime.synthesis_calls[-1]
    assert request.engine_kind == capability.dependency.kind
    assert request.engine_catalog_id == capability.dependency.catalog_id
    assert request.engine_version == capability.dependency.version
    assert request.engine_manifest_sha256 == capability.dependency.manifest_sha256
    assert request.pack_id == PACK_ID
    assert request.pack_version == "1.0.0"
    assert request.pack_manifest_sha256 == "a" * 64
    assert request.reference_audio_sha256 == request.reference.audio_sha256
    assert request.reference_transcript_sha256 == hashlib.sha256(
        request.reference.transcript.encode("utf-8")
    ).hexdigest()
    assert request.text_sha256 == hashlib.sha256(request.text.encode("utf-8")).hexdigest()
    assert request.reply_kind == "teaching.explain"

    asyncio.run(
        adapter.synthesize(
            pack,
            text="换一个问题。",
            style="lively",
            reply_kind="teaching.encourage",
            installed_root=root,
        )
    )
    assert runtime.synthesis_calls[-1].request_sha256 != request.request_sha256


def test_idle_or_natural_child_restart_is_prepared_and_synthesizes_on_first_click(
    monkeypatch, tmp_path: Path
):
    capability, *_ = _engine_release(tmp_path, name="generation-restart")

    class GenerationRuntime(FakeQwenRuntime):
        def __init__(self):
            super().__init__()
            self.child_generation = 1
            self.prepared_generation: int | None = None

        async def prepare(self, request):
            result = await super().prepare(request)
            self.prepared_generation = self.child_generation
            return result

        async def synthesize(self, request):
            if self.prepared_generation != self.child_generation:
                raise RuntimeError("fresh child generation has no prompts")
            return await super().synthesize(request)

        def natural_exit_and_restart(self):
            self.child_generation += 1
            self.prepared_generation = None

    runtime = GenerationRuntime()
    _register_runtime(monkeypatch, capability, runtime)
    root = tmp_path / "voice-pack-generation-restart"
    pack = _qwen_pack(
        root,
        engine_manifest_sha256=capability.dependency.manifest_sha256,
    )
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()

    async def click(text: str):
        await adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64)
        return await adapter.synthesize(
            pack,
            text=text,
            style="relaxed",
            reply_kind="teaching.explain",
            installed_root=root,
        )

    first = asyncio.run(click("第一代子进程。"))
    runtime.natural_exit_and_restart()
    after_restart = asyncio.run(click("空闲退出后第一次点击。"))

    assert first.audio.startswith(b"RIFF")
    assert after_restart.audio.startswith(b"RIFF")
    assert len(runtime.prepare_calls) == 2
    assert len(runtime.synthesis_calls) == 2


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("engine_kind", "wrong-engine"),
        ("engine_catalog_id", "wrong-package"),
        ("engine_version", "wrong-version"),
        ("engine_manifest_sha256", "f" * 64),
        ("pack_id", "wrong-pack"),
        ("pack_version", "9.9.9"),
        ("pack_manifest_sha256", "e" * 64),
        ("binding_sha256", "d" * 64),
        ("references_sha256", "c" * 64),
    ],
)
def test_prepare_receipt_requires_full_binding(
    monkeypatch, tmp_path: Path, field: str, wrong: object
):
    capability, *_ = _engine_release(tmp_path)
    runtime = FakeQwenRuntime(prepare_mismatch=(field, wrong))
    _register_runtime(monkeypatch, capability, runtime)
    root = tmp_path / "voice"
    pack = _qwen_pack(root, engine_manifest_sha256=capability.dependency.manifest_sha256)
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    assert raised.value.code == "VOICE_PACK_RUNTIME_BINDING_MISMATCH"
    assert runtime.synthesis_calls == []


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("engine_kind", "wrong-engine"),
        ("engine_catalog_id", "wrong-package"),
        ("engine_version", "wrong-version"),
        ("engine_manifest_sha256", "f" * 64),
        ("pack_id", "wrong-pack"),
        ("pack_version", "9.9.9"),
        ("pack_manifest_sha256", "e" * 64),
        ("binding_sha256", "d" * 64),
        ("style", "wrong-style"),
        ("reference_audio_sha256", "c" * 64),
        ("reference_transcript_sha256", "b" * 64),
        ("request_text_sha256", "a" * 64),
        ("reply_kind", "wrong.reply"),
        ("request_sha256", "9" * 64),
    ],
)
def test_synthesis_receipt_requires_full_request_binding(
    monkeypatch, tmp_path: Path, field: str, wrong: object
):
    capability, *_ = _engine_release(tmp_path)
    runtime = FakeQwenRuntime(synthesis_mismatch=(field, wrong))
    _register_runtime(monkeypatch, capability, runtime)
    root = tmp_path / "voice"
    pack = _qwen_pack(root, engine_manifest_sha256=capability.dependency.manifest_sha256)
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()
    asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        asyncio.run(
            adapter.synthesize(
                pack,
                text="不得返回错误绑定音频",
                style="relaxed",
                reply_kind="teaching.explain",
                installed_root=root,
            )
        )
    assert raised.value.code == "VOICE_PACK_RUNTIME_BINDING_MISMATCH"


@pytest.mark.parametrize("invalid_kind", ["invalid_pcm", "bad_audio_receipt"])
def test_invalid_runtime_wav_or_audio_receipt_clears_prepare_cache(
    monkeypatch, tmp_path: Path, invalid_kind: str
):
    capability, *_ = _engine_release(tmp_path)
    runtime = FakeQwenRuntime(
        invalid_audio=invalid_kind == "invalid_pcm",
        bad_audio_receipt=invalid_kind == "bad_audio_receipt",
    )
    _register_runtime(monkeypatch, capability, runtime)
    root = tmp_path / "voice"
    pack = _qwen_pack(root, engine_manifest_sha256=capability.dependency.manifest_sha256)
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()
    asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        asyncio.run(
            adapter.synthesize(
                pack,
                text="无效音频",
                style="lively",
                reply_kind="",
                installed_root=root,
            )
        )
    assert raised.value.code == "VOICE_PACK_RUNTIME_INVALID_AUDIO"
    runtime.invalid_audio = False
    runtime.bad_audio_receipt = False
    with pytest.raises(voice_pack.TeacherVoicePackError) as not_prepared:
        asyncio.run(
            adapter.synthesize(
                pack,
                text="必须重新prepare",
                style="lively",
                reply_kind="",
                installed_root=root,
            )
        )
    assert not_prepared.value.code == "VOICE_PACK_RUNTIME_NOT_PREPARED"
    asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    assert len(runtime.prepare_calls) == 2


def test_missing_verified_engine_or_prepare_failure_never_synthesizes(
    monkeypatch, tmp_path: Path
):
    capability, *_ = _engine_release(tmp_path)
    root = tmp_path / "voice"
    pack = _qwen_pack(root, engine_manifest_sha256=capability.dependency.manifest_sha256)
    monkeypatch.setattr(voice_pack, "_QWEN_BASE_CLONE_RUNTIMES", {})
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()
    with pytest.raises(voice_pack.TeacherVoicePackError) as missing:
        asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    assert missing.value.code == "VOICE_PACK_RUNTIME_UNAVAILABLE"

    runtime = FakeQwenRuntime(fail_prepare=True)
    voice_pack.register_qwen_base_clone_runtime(capability=capability, runtime=runtime)
    with pytest.raises(voice_pack.TeacherVoicePackError) as failed:
        asyncio.run(adapter.prepare(pack, installed_root=root, manifest_sha256="a" * 64))
    assert failed.value.code == "VOICE_PACK_RUNTIME_PREPARE_FAILED"
    assert runtime.synthesis_calls == []


def test_registry_rejects_concurrent_same_identity_overwrite(monkeypatch, tmp_path: Path):
    capability, *_ = _engine_release(tmp_path)
    monkeypatch.setattr(voice_pack, "_QWEN_BASE_CLONE_RUNTIMES", {})
    runtimes = [FakeQwenRuntime(), FakeQwenRuntime()]
    barrier = threading.Barrier(2)

    def register(runtime):
        barrier.wait(timeout=5)
        try:
            voice_pack.register_qwen_base_clone_runtime(
                capability=capability,
                runtime=runtime,
            )
            return "ok"
        except voice_pack.TeacherVoicePackError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(register, runtimes))
    assert sorted(outcomes) == ["VOICE_ENGINE_REGISTRATION_CONFLICT", "ok"]
    registration = voice_pack._qwen_runtime_registration(capability.dependency)
    assert registration is not None
    assert registration.runtime in runtimes


def test_concurrent_pack_prepare_cannot_restore_superseded_old_binding(
    monkeypatch, tmp_path: Path
):
    capability, *_ = _engine_release(tmp_path)

    class DelayedRuntime(FakeQwenRuntime):
        def __init__(self):
            super().__init__()
            self.old_started = asyncio.Event()
            self.release_old = asyncio.Event()

        async def prepare(self, request):
            self.prepare_calls.append(request)
            if request.binding.version == "1.0.0":
                self.old_started.set()
                await self.release_old.wait()
            return _prepare_receipt(request)

    runtime = DelayedRuntime()
    _register_runtime(monkeypatch, capability, runtime)
    root_v1 = tmp_path / "voice-v1"
    root_v2 = tmp_path / "voice-v2"
    pack_v1 = _qwen_pack(
        root_v1,
        engine_manifest_sha256=capability.dependency.manifest_sha256,
        version="1.0.0",
    )
    pack_v2 = _qwen_pack(
        root_v2,
        engine_manifest_sha256=capability.dependency.manifest_sha256,
        version="1.1.0",
    )
    adapter = voice_pack.QwenBaseCloneVoicePackAdapter()

    async def exercise_switch():
        old_task = asyncio.create_task(
            adapter.prepare(pack_v1, installed_root=root_v1, manifest_sha256="a" * 64)
        )
        await runtime.old_started.wait()
        new_result = await adapter.prepare(
            pack_v2,
            installed_root=root_v2,
            manifest_sha256="b" * 64,
        )
        runtime.release_old.set()
        old_result = await asyncio.gather(old_task, return_exceptions=True)
        return new_result, old_result[0]

    new_result, old_result = asyncio.run(exercise_switch())
    assert new_result["schema"] == voice_pack.QWEN_BASE_CLONE_PREPARE_RESULT_SCHEMA
    assert new_result["binding"]["pack"]["version"] == "1.1.0"
    assert isinstance(old_result, voice_pack.TeacherVoicePackError)
    assert old_result.code == "VOICE_PACK_RUNTIME_PREPARE_SUPERSEDED"
    assert adapter._prepared_binding is not None
    assert adapter._prepared_binding.version == "1.1.0"


def _configure_signed_voice_pack(
    monkeypatch,
    tmp_path: Path,
    *,
    engine_manifest_sha256: str,
) -> Path:
    payload_root = tmp_path / "voice-source"
    manifest = _qwen_pack(
        payload_root,
        engine_manifest_sha256=engine_manifest_sha256,
    )
    raw_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    archive_path = tmp_path / "bundled" / "voice-pack.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("voice-pack.json", raw_manifest)
        archive.writestr("voice-pack.sig", _sign(raw_manifest))
        for item in manifest["files"]:
            archive.write(payload_root / Path(item["path"]), item["path"])
    catalog_path = tmp_path / "voice-catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema": voice_pack.CATALOG_SCHEMA,
                "https_allowed_hosts": [],
                "trusted_keys": [
                    {
                        "key_id": KEY_ID,
                        "public_key": base64.b64encode(PUBLIC_KEY).decode("ascii"),
                    }
                ],
                "packs": [
                    {
                        "pack_id": PACK_ID,
                        "version": "1.0.0",
                        "display_name": "Qwen fixture",
                        "default": True,
                        "source": {
                            "kind": "bundled",
                            "archive_path": "bundled/voice-pack.zip",
                        },
                        "archive": {
                            "bytes": archive_path.stat().st_size,
                            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                        },
                        "signing_key_id": KEY_ID,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    installed_root = tmp_path / "installed"
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_CATALOG", str(catalog_path))
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_ROOT", str(installed_root))
    return installed_root


def test_active_synthesis_rehashes_rights_and_qa_before_runtime(
    client, monkeypatch, tmp_path: Path
):
    capability, *_ = _engine_release(tmp_path, name="active-engine")
    runtime = FakeQwenRuntime()
    _register_runtime(monkeypatch, capability, runtime)
    monkeypatch.setitem(
        voice_pack._RUNTIME_ADAPTERS,
        voice_pack.QWEN_BASE_CLONE_ADAPTER_ID,
        voice_pack.QwenBaseCloneVoicePackAdapter(),
    )
    monkeypatch.setitem(
        voice_pack._RUNTIME_ADAPTER_LOCKS,
        voice_pack.QWEN_BASE_CLONE_ADAPTER_ID,
        asyncio.Lock(),
    )
    installed_root = _configure_signed_voice_pack(
        monkeypatch,
        tmp_path,
        engine_manifest_sha256=capability.dependency.manifest_sha256,
    )
    install_response = client.post(
        f"/api/teacher-voice/packs/{PACK_ID}/install",
        json={"version": "1.0.0"},
    )
    assert install_response.status_code == 200, install_response.json()
    assert client.post(
        f"/api/teacher-voice/packs/{PACK_ID}/activate",
        json={"version": "1.0.0"},
    ).status_code == 200
    rights_path = installed_root / PACK_ID / "1.0.0" / "rights" / "release.json"
    rights_path.write_bytes(rights_path.read_bytes() + b"tampered")
    monkeypatch.delenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", raising=False)
    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "不得合成", "reply_kind": "teaching.explain"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {
        "VOICE_PACK_FILE_SIZE_MISMATCH",
        "VOICE_PACK_FILE_HASH_MISMATCH",
    }
    assert runtime.prepare_calls == []
    assert runtime.synthesis_calls == []
