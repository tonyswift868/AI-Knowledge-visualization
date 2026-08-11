from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest
import numpy as np
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).parents[2]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ORCH = _load("qwen_training_orchestrator", ROOT / "scripts" / "orchestrate_qwen3_tts_training.py")
TRAINER = _load("qwen_safe_trainer", ROOT / "scripts" / "qwen3_tts_sft_safe.py")
ADMISSION = _load("qwen_local_private_admission", ROOT / "scripts" / "create_local_private_voice_admission.py")
CPU_PREPARE = _load(
    "qwen_cpu_prepare", ROOT / "scripts" / "orchestrate_qwen3_tts_cpu_prepare.py"
)
BOUNDED_PROBE = _load(
    "qwen_bounded_probe", ROOT / "scripts" / "orchestrate_qwen3_tts_bounded_probe.py"
)


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture_wav(path: Path, index: int) -> None:
    sample_rate = 24_000
    time = np.arange(sample_rate // 5 + index, dtype=np.float64) / sample_rate
    samples = np.rint(np.sin(2 * np.pi * (180 + index * 10) * time) * 5000).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def _sample(path: str, file_path: Path, text: str) -> dict[str, object]:
    digest = _sha(file_path)
    with wave.open(str(file_path), "rb") as audio:
        duration_ms = round(audio.getnframes() * 1000 / audio.getframerate())
    return {
        "sample_id": digest,
        "audio": path,
        "sha256": digest,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": file_path.stat().st_size,
        "duration_ms": duration_ms,
        "text": text,
    }


def _manifest(dataset: Path) -> dict[str, object]:
    audio = dataset / "audio"
    audio.mkdir(parents=True)
    files = {
        "train": audio / "train.wav",
        "validation": audio / "validation.wav",
        "heldout": audio / "heldout.wav",
        "reference": audio / "reference.wav",
    }
    for index, path in enumerate(files.values(), start=1):
        _write_fixture_wav(path, index)
    reference_sha = _sha(files["reference"])
    return {
        "schema": ORCH.STAGING_MANIFEST_SCHEMA,
        "candidate_id": "tsubaki-original",
        "speaker_id": "tsubaki-teacher",
        "upstream_contract": {
            "repository": ORCH.SOURCE_REPOSITORY,
            "commit": ORCH.SOURCE_COMMIT,
            "prepare_entry": "finetuning/prepare_data.py",
            "training_entry": "finetuning/sft_12hz.py",
            "this_manifest_is_upstream_schema": False,
        },
        "base_model": {"model_id": ORCH.BASE_MODEL_ID, "revision": ORCH.BASE_MODEL_REVISION},
        "rights": {
            "grants": {
                "commercial_synthesis": True,
                "reference_audio_distribution": True,
                "training": True,
                "weight_distribution": True,
            },
            "identity": {"kind": "original", "impersonates": False, "source_identity": None},
        },
        "speaker_qa": {
            "method": "speaker-embedding-cosine-v1",
            "assessed_samples": 3,
            "minimum_similarity_threshold": 0.75,
            "minimum_reference_similarity": 0.9,
            "maximum_outlier_fraction": 0.05,
            "outlier_fraction": 0.0,
        },
        "corpus": {"digest": "a" * 64},
        "train": [_sample("audio/train.wav", files["train"], "训练文本")],
        "validation": [_sample("audio/validation.wav", files["validation"], "验证文本")],
        "heldout": [_sample("audio/heldout.wav", files["heldout"], "留出文本")],
        "reference": {
            "audio": "audio/reference.wav",
            "sha256": reference_sha,
            "bytes": files["reference"].stat().st_size,
            "duration_ms": round((24_000 // 5 + 4) * 1000 / 24_000),
            "text": "参考文本",
            "text_sha256": hashlib.sha256("参考文本".encode()).hexdigest(),
        },
    }


def _approve(
    dataset: Path,
    generation_id: str,
    manifest: dict[str, object],
    *,
    purpose: str = "dataset_training_qa",
    decision: str = "GO",
    valid_from: str = "2020-01-01T00:00:00+00:00",
    valid_until: str = "2035-01-01T00:00:00+00:00",
    admission_scope: str | None = None,
) -> tuple[Path, Ed25519PrivateKey]:
    _json(dataset / ORCH.STAGING_MANIFEST_NAME, manifest)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    registry = dataset.parent / "qa-trust.json"
    _json(
        registry,
        {
            "schema": ORCH.QA_TRUST_SCHEMA,
            "signers": [
                {
                    "key_id": "qa-key-1",
                    "public_key_base64": base64.b64encode(public).decode("ascii"),
                    "purposes": [purpose],
                }
            ],
        },
    )
    files, tree = ORCH._inventory(dataset)
    manifest_path = dataset / ORCH.STAGING_MANIFEST_NAME
    payload = {
        "schema": ORCH.QA_APPROVAL_SCHEMA,
        "generation_id": generation_id,
        "decision": decision,
        "manifest": {
            "path": ORCH.STAGING_MANIFEST_NAME,
            "bytes": manifest_path.stat().st_size,
            "sha256": _sha(manifest_path),
        },
        "files": files,
        "tree_sha256": tree,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "qa_summary": {
            "human_review": "passed",
            "training_started": False,
            **({"admission_scope": admission_scope} if admission_scope is not None else {}),
        },
    }
    _json(
        dataset / ORCH.QA_APPROVAL_NAME,
        {
            "schema": ORCH.QA_ENVELOPE_SCHEMA,
            "payload": payload,
            "signature": {
                "algorithm": "Ed25519",
                "key_id": "qa-key-1",
                "value_base64": base64.b64encode(private.sign(ORCH._canonical_json(payload))).decode("ascii"),
            },
        },
    )
    return registry, private


@pytest.fixture
def approved_dataset(tmp_path: Path) -> dict[str, object]:
    generation_id = "dataset-001"
    dataset = tmp_path / "training" / "generations" / generation_id / "dataset"
    dataset.mkdir(parents=True)
    manifest = _manifest(dataset)
    registry, private = _approve(dataset, generation_id, manifest)
    return {
        "dataset": dataset,
        "generation_id": generation_id,
        "manifest": manifest,
        "registry": registry,
        "private": private,
        "layout": ORCH.Layout(
            tmp_path / "training",
            tmp_path / "training" / "generations",
            tmp_path / "training" / "runs",
            tmp_path / "training" / "cache",
        ),
    }


def test_accepts_qa_signed_exact_dataset_inventory(approved_dataset: dict[str, object]) -> None:
    result = ORCH.validate_dataset_qa(
        approved_dataset["dataset"],
        approved_dataset["generation_id"],
        approved_dataset["registry"],
        now=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert result["qa_signer_key_id"] == "qa-key-1"
    assert result["train_samples"] == 1
    assert result["tree_sha256"]


def test_rejects_dataset_byte_drift_after_qa_signature(approved_dataset: dict[str, object]) -> None:
    (approved_dataset["dataset"] / "audio" / "train.wav").write_bytes(b"changed")
    with pytest.raises(ORCH.TrainingGateError, match="signed exact inventory") as caught:
        ORCH.validate_dataset_qa(
            approved_dataset["dataset"], approved_dataset["generation_id"], approved_dataset["registry"]
        )
    assert caught.value.code == "QA_INVENTORY_MISMATCH"


def test_rejects_untrusted_qa_purpose(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = _manifest(dataset)
    registry, _ = _approve(dataset, "dataset-001", manifest, purpose="speaker_qa")
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH.validate_dataset_qa(dataset, "dataset-001", registry)
    assert caught.value.code == "QA_SIGNER_UNTRUSTED"


def test_rejects_expired_qa_approval(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = _manifest(dataset)
    registry, _ = _approve(dataset, "dataset-001", manifest, valid_until="2025-01-01T00:00:00+00:00")
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH.validate_dataset_qa(
            dataset, "dataset-001", registry, now=datetime(2026, 8, 8, tzinfo=timezone.utc)
        )
    assert caught.value.code == "QA_APPROVAL_EXPIRED"


def test_rejects_manifest_path_escape_even_when_qa_signed(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = _manifest(dataset)
    manifest["train"][0]["audio"] = "../escape.wav"
    registry, _ = _approve(dataset, "dataset-001", manifest)
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH.validate_dataset_qa(dataset, "dataset-001", registry)
    assert caught.value.code == "DATASET_PATH_INVALID"


def test_rejects_impersonating_rights_even_when_qa_signed(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = _manifest(dataset)
    manifest["rights"]["identity"]["impersonates"] = True
    registry, _ = _approve(dataset, "dataset-001", manifest)
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH.validate_dataset_qa(dataset, "dataset-001", registry)
    assert caught.value.code == "DATASET_RIGHTS_INVALID"


def test_accepts_explicitly_scoped_local_private_admission(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = _manifest(dataset)
    evidence = dataset / "evidence"
    evidence.mkdir()
    _json(
        evidence / "CURATION_RECEIPT.json",
        {"status": "verified_curated_trainer_neutral_private_local_only"},
    )
    _json(evidence / "CURATED_QA_REPORT.json", {"acceptance": {"audio_contract": True, "failure_count": 0}})
    (evidence / "trainer-neutral-manifest.jsonl").write_text("{}\n", encoding="utf-8")
    _json(
        evidence / "LOCAL_AUTHORIZATION_SCOPE.json",
        {
            "schema": "mentorflow.local-private-user-authorization-scope.v1",
            "basis": "explicit_user_instruction_in_current_project",
            "identity_or_consent_rights_established": False,
            "semantic_audio_text_alignment_verified": False,
            "biometric_speaker_identity_verified": False,
        },
    )
    _json(
        evidence / "REFERENCE_SELECTION.json",
        {
            "schema": ADMISSION.SELECTION_SCHEMA,
            "selected": {
                "eligible": True,
                "audio_sha256": manifest["reference"]["sha256"],
                "text_sha256": manifest["reference"]["text_sha256"],
            },
        },
    )

    def pin(name: str) -> dict[str, object]:
        path = dataset / name
        return {"path": name, "bytes": path.stat().st_size, "sha256": _sha(path)}

    manifest["schema"] = ORCH.LOCAL_PRIVATE_STAGING_MANIFEST_SCHEMA
    manifest["rights"] = {
        "scope": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
        "grants": {
            "training": True,
            "private_local_inference": True,
            "commercial_synthesis": False,
            "reference_audio_distribution": False,
            "weight_distribution": False,
            "public_release": False,
        },
        "identity": {
            "kind": "declared_third_party_character_voice",
            "source_identity": "椿",
            "impersonates": True,
            "original_voice_claim": False,
            "biometric_identity_verified": False,
        },
        "authorization": {
            "basis": "explicit_user_instruction_in_current_project",
            "restriction": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
            "public_rights_evidence_present": False,
            "receipt": pin("evidence/LOCAL_AUTHORIZATION_SCOPE.json"),
        },
    }
    manifest["speaker_qa"] = {
        "method": "curated-private-local-single-declared-speaker-v1",
        "assessed_samples": 4,
        "curation_qa_failure_count": 0,
        "source_speaker_label": "chun_declared_from_source_filenames",
        "biometric_verified": False,
        "public_release_eligible": False,
        "reference_selection": pin("evidence/REFERENCE_SELECTION.json"),
    }
    manifest["provenance"] = {
        "private_local_only": True,
        "curated_payload_tree_sha256": "d" * 64,
        "curation_receipt": pin("evidence/CURATION_RECEIPT.json"),
        "curation_qa_report": pin("evidence/CURATED_QA_REPORT.json"),
        "trainer_neutral_manifest": pin("evidence/trainer-neutral-manifest.jsonl"),
    }
    manifest["limitations"] = {
        "semantic_audio_text_alignment_verified": False,
        "biometric_speaker_identity_verified": False,
        "public_release_eligible": False,
    }
    all_items = manifest["train"] + manifest["validation"] + manifest["heldout"]
    for item in all_items:
        item["style"] = "neutral"
    manifest["reference"]["style"] = "neutral"
    manifest["reference"]["display_name"] = "椿"
    ranked = sorted(
        all_items,
        key=lambda item: (
            hashlib.sha256(
                f"mentorflow-local-private-split-v1\0{item['sha256']}\0{item['text_sha256']}".encode()
            ).hexdigest(),
            item["sha256"],
        ),
    )
    assignments = {
        "heldout": ranked[:1],
        "validation": ranked[1:2],
        "train": ranked[2:],
    }
    manifest.update(assignments)
    assignment_hashes = {
        name: [item["sha256"] for item in assignments[name]]
        for name in ("heldout", "validation", "train")
    }
    manifest["split_contract"] = {
        "algorithm": "mentorflow-local-private-split-v1",
        "counts": {name: len(values) for name, values in assignment_hashes.items()},
        "assignment_sha256": ORCH._sha256_bytes(ORCH._canonical_json(assignment_hashes)),
    }
    manifest["corpus"]["digest"] = ORCH._sha256_bytes(
        ORCH._canonical_json(
            {
                "curated_payload_tree_sha256": "d" * 64,
                "reference_sha256": manifest["reference"]["sha256"],
                "splits": {name: assignment_hashes[name] for name in sorted(assignment_hashes)},
            }
        )
    )
    registry, _ = _approve(
        dataset,
        "dataset-local",
        manifest,
        purpose=ORCH.LOCAL_PRIVATE_QA_PURPOSE,
        admission_scope=ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
    )
    result = ORCH.validate_dataset_qa(dataset, "dataset-local", registry)
    assert result["training_scope"] == ORCH.LOCAL_PRIVATE_TRAINING_SCOPE


def test_reference_pitch_estimator_is_deterministic_for_pcm16_sine(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    sample_rate = 24_000
    time = np.arange(sample_rate * 6, dtype=np.float64) / sample_rate
    samples = np.rint(np.sin(2 * np.pi * 220.0 * time) * 8000).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())
    first = ADMISSION._pitch_summary(path)
    second = ADMISSION._pitch_summary(path)
    assert first == second
    assert abs(first["median_f0_hz"] - 220.0) < 2.0


def test_cpu_prepare_output_validator_binds_rows_codes_and_cpu_receipt(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    samples = np.zeros(1920, dtype="<i2")
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(samples.tobytes())
    raw_row = {"audio": str(audio), "text": "测试文本", "ref_audio": str(audio)}
    audio_sha = _sha(audio)
    duration_ms = 80
    pin = {
        "absolute_path": str(audio),
        "bytes": audio.stat().st_size,
        "sha256": audio_sha,
        "text_sha256": hashlib.sha256(raw_row["text"].encode("utf-8")).hexdigest(),
        "duration_ms": duration_ms,
    }
    reference_pin = {**pin, "text_sha256": "b" * 64}
    contract = {
        "model_type": "qwen3_tts_tokenizer_12hz",
        "input_sample_rate": 24_000,
        "encode_downsample_rate": 1920,
        "quantizers": 16,
        "codebook_size": 2048,
    }
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps(raw_row, ensure_ascii=False) + "\n", encoding="utf-8")
    prepared = tmp_path / "prepared.jsonl"
    prepared.write_text(
        json.dumps({**raw_row, "audio_codes": [[index for index in range(16)]]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    worker = tmp_path / "WORKER_RESULT.json"
    _json(
        worker,
        {
            "schema": CPU_PREPARE.WORKER_RESULT_SCHEMA,
            "status": "completed",
            "plan_sha256": "a" * 64,
            "rows": 1,
            "minimum_free_ram_bytes": 8 * 1024**3,
            "minimum_commit_headroom_bytes": 12 * 1024**3,
            "maximum_consecutive_low_physical_samples": 0,
            "maximum_worker_rss_bytes": 2 * 1024**3,
            "code_frames_total": 1,
            "code_frames_minimum": 1,
            "code_frames_maximum": 1,
            "code_value_minimum": 0,
            "code_value_maximum": 15,
            "empirical_code_frame_offsets": [0],
            "prepared_bytes": prepared.stat().st_size,
            "prepared_sha256": _sha(prepared),
            "sample_relation_sha256": hashlib.sha256(
                f"{audio_sha}\0{1920}\0{1}\0{0}\n".encode("ascii")
            ).hexdigest(),
            "tokenizer_contract": contract,
            "torch_cuda_available": False,
            "torch_cuda_device_count": 0,
            "floating_dtypes": ["torch.float32"],
            "batch_size": 1,
            "network_socket_connections_blocked": True,
            "network_urlopen_blocked": True,
            "network_audit_hook_enabled": True,
            "training_model_loaded": False,
            "training_started": False,
            "network_used": False,
        },
    )
    plan = {
        "plan_sha256": "a" * 64,
        "paths": {"dataset_root": str(tmp_path)},
        "input": {"train_rows": 1, "train_audio": [pin], "reference": reference_pin},
        "tokenizer": {"contract": contract},
    }
    result = CPU_PREPARE.validate_prepared_output(
        raw_jsonl=raw,
        prepared_jsonl=prepared,
        worker_result_path=worker,
        plan=plan,
    )
    assert result["rows"] == 1
    assert result["code_frames_total"] == 1
    bad = {**raw_row, "audio_codes": [[0] * 15]}
    prepared.write_text(json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
        CPU_PREPARE.validate_prepared_output(
            raw_jsonl=raw,
            prepared_jsonl=prepared,
            worker_result_path=worker,
            plan=plan,
        )
    assert caught.value.code == "PREPARED_INVALID"


def test_cpu_prepare_stream_limits_and_local_wav_guards(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    raw.write_bytes(b'{"value":1}\n{"value":2}\n')
    iterator = CPU_PREPARE._iter_bounded_jsonl(
        raw,
        "fixture",
        maximum_total_bytes=1024,
        maximum_line_bytes=32,
    )
    assert next(iterator) == (1, {"value": 1})
    assert next(iterator) == (2, {"value": 2})
    with pytest.raises(StopIteration):
        next(iterator)

    oversized = tmp_path / "oversized.jsonl"
    oversized.write_bytes(b'{"value":"' + b"x" * 40 + b'"}\n')
    with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
        list(
            CPU_PREPARE._iter_bounded_jsonl(
                oversized,
                "oversized fixture",
                maximum_total_bytes=1024,
                maximum_line_bytes=32,
            )
        )
    assert caught.value.code == "JSONL_TOO_LARGE"

    for invalid in ("https://example.invalid/a.wav", "data:audio/wav;base64,AAAA", r"\\server\share\a.wav"):
        with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
            CPU_PREPARE._local_dataset_wav(
                invalid,
                dataset_root=tmp_path,
                expected={},
                label="invalid fixture",
            )
        assert caught.value.code == "INPUT_INVALID"


def test_cpu_prepare_rejects_over_30_second_wav_and_hardlink(tmp_path: Path) -> None:
    audio = tmp_path / "too-long.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(np.zeros(CPU_PREPARE.MAXIMUM_AUDIO_FRAMES + 1, dtype="<i2").tobytes())
    pin = {
        "absolute_path": str(audio),
        "bytes": audio.stat().st_size,
        "sha256": _sha(audio),
        "duration_ms": round((CPU_PREPARE.MAXIMUM_AUDIO_FRAMES + 1) * 1000 / 24_000),
    }
    with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
        CPU_PREPARE._inspect_pcm16_wav(audio, expected=pin, label="long fixture")
    assert caught.value.code == "INPUT_INVALID"

    source = tmp_path / "plain.wav"
    _write_fixture_wav(source, 0)
    alias = tmp_path / "alias.wav"
    os.link(source, alias)
    alias_pin = {
        "absolute_path": str(alias),
        "bytes": alias.stat().st_size,
        "sha256": _sha(alias),
    }
    with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
        CPU_PREPARE._local_dataset_wav(
            str(alias), dataset_root=tmp_path, expected=alias_pin, label="hardlink fixture"
        )
    assert caught.value.code == "FILE_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="Windows share-mode lock contract")
def test_cpu_prepare_dataset_read_locks_deny_write_and_delete(tmp_path: Path) -> None:
    payload = tmp_path / "payload.bin"
    approval = tmp_path / ORCH.QA_APPROVAL_NAME
    payload.write_bytes(b"immutable payload")
    approval.write_bytes(b"immutable approval")
    locks = CPU_PREPARE._acquire_dataset_read_locks(tmp_path, [{"path": "payload.bin"}])
    try:
        assert locks.count == 2
        with pytest.raises(OSError):
            payload.write_bytes(b"changed")
        with pytest.raises(OSError):
            payload.unlink()
        assert payload.read_bytes() == b"immutable payload"
    finally:
        locks.close()
    payload.write_bytes(b"after release")
    assert payload.read_bytes() == b"after release"


def test_cpu_tokenizer_loader_passes_strict_offline_cpu_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeTokenizer:
        pass

    class FakeRegistry:
        @classmethod
        def register(cls, *args: object, **kwargs: object) -> None:
            captured.setdefault("registrations", []).append((args, kwargs))

    class FakeFeatureExtractor:
        @classmethod
        def from_pretrained(cls, path: str, **kwargs: object) -> object:
            captured["feature"] = (path, kwargs)
            return object()

    class FakeModelRegistry(FakeRegistry):
        @classmethod
        def from_pretrained(cls, path: str, **kwargs: object) -> object:
            captured["model"] = (path, kwargs)
            return type("FakeModel", (), {"config": object()})()

    qwen = ModuleType("qwen_tts")
    qwen.Qwen3TTSTokenizer = FakeTokenizer
    core = ModuleType("qwen_tts.core")
    for name in (
        "Qwen3TTSTokenizerV1Config",
        "Qwen3TTSTokenizerV1Model",
        "Qwen3TTSTokenizerV2Config",
        "Qwen3TTSTokenizerV2Model",
    ):
        setattr(core, name, type(name, (), {}))
    transformers = ModuleType("transformers")
    transformers.AutoConfig = FakeRegistry
    transformers.AutoFeatureExtractor = FakeFeatureExtractor
    transformers.AutoModel = FakeModelRegistry
    monkeypatch.setitem(sys.modules, "qwen_tts", qwen)
    monkeypatch.setitem(sys.modules, "qwen_tts.core", core)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    fake_torch = type("FakeTorch", (), {"float32": "float32", "device": staticmethod(lambda value: value)})()
    CPU_PREPARE._load_cpu_tokenizer(fake_torch, Path("tokenizer-root"))
    _, feature_kwargs = captured["feature"]
    _, model_kwargs = captured["model"]
    assert feature_kwargs == {"local_files_only": True, "trust_remote_code": False}
    assert model_kwargs == {
        "device_map": {"": "cpu"},
        "dtype": "float32",
        "attn_implementation": "sdpa",
        "use_kernels": False,
        "local_files_only": True,
        "trust_remote_code": False,
        "use_safetensors": True,
        "weights_only": True,
    }


def test_cpu_prepare_network_blocker_fails_before_socket_or_urlopen() -> None:
    script = f"""
import importlib.util
import socket
import urllib.request
native_sendmsg = hasattr(socket.socket, 'sendmsg')
spec = importlib.util.spec_from_file_location('cpu_network_fixture', {str(ROOT / 'scripts' / 'orchestrate_qwen3_tts_cpu_prepare.py')!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module._block_python_network()
if hasattr(socket.socket, 'sendmsg') != native_sendmsg:
    raise SystemExit(5)
import asyncio
blocked = 0
for action in (
    lambda: socket.create_connection(('127.0.0.1', 9), timeout=0.01),
    lambda: urllib.request.urlopen('http://127.0.0.1:9/', timeout=0.01),
):
    try:
        action()
    except OSError:
        blocked += 1
if blocked != 2:
    raise SystemExit(4)
print('blocked-before-traffic')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked-before-traffic"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_cpu_prepare_job_bootstrap_assigns_before_release_and_reports_limits() -> None:
    memory_limit = 256 * 1024**2
    job = CPU_PREPARE._WindowsJob(memory_limit)
    gate = CPU_PREPARE._WindowsBootstrapGate()
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts" / "orchestrate_qwen3_tts_cpu_prepare.py"),
                "--job-bootstrap",
                "--job-event",
                gate.name,
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        job.assign(process)
        before = job.query_memory()
        assert before["job_memory_limit_bytes"] == memory_limit
        assert before["process_memory_limit_bytes"] == memory_limit
        gate.release()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 2
        assert stdout == ""
        assert "ARGUMENT_MISSING" in stderr
        after = job.query_memory()
        assert 0 < after["peak_job_memory_used_bytes"] <= memory_limit
    finally:
        gate.close()
        if process is not None and process.poll() is None:
            job.terminate()
            process.wait(timeout=15)
        job.close()


def test_cpu_prepare_rejects_nonproduction_training_root_before_io(tmp_path: Path) -> None:
    with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
        CPU_PREPARE.prepare_cpu_only(
            dataset_generation_id="dataset-fixture",
            prepare_generation_id="prepare-fixture",
            training_root=tmp_path,
        )
    assert caught.value.code == "TRAINING_ROOT_INVALID"


def _cpu_memory_sample(*, physical: int, commit_headroom: int) -> dict[str, int]:
    commit_limit = 64 * 1024**3
    return {
        "physical_available_bytes": physical,
        "physical_total_bytes": 32 * 1024**3,
        "commit_total_bytes": commit_limit - commit_headroom,
        "commit_limit_bytes": commit_limit,
        "commit_headroom_bytes": commit_headroom,
    }


def test_cpu_prepare_adaptive_launch_memory_gate_boundaries() -> None:
    boundary = _cpu_memory_sample(
        physical=CPU_PREPARE.MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES,
        commit_headroom=CPU_PREPARE.MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES,
    )
    result = CPU_PREPARE._stable_launch_memory_gate(probe=lambda: boundary, sleep=lambda _: None)
    assert result["decision"] == "GO"
    assert len(result["samples"]) == 2

    for sample in (
        _cpu_memory_sample(
            physical=CPU_PREPARE.MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES - 1,
            commit_headroom=CPU_PREPARE.MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES,
        ),
        _cpu_memory_sample(
            physical=CPU_PREPARE.MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES,
            commit_headroom=CPU_PREPARE.MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES - 1,
        ),
    ):
        with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
            CPU_PREPARE._stable_launch_memory_gate(probe=lambda sample=sample: sample, sleep=lambda _: None)
        assert caught.value.code == "CPU_PREPARE_RESOURCE_GATE"


def test_cpu_prepare_adaptive_memory_probe_and_runtime_fail_closed() -> None:
    incomplete = _cpu_memory_sample(physical=8 * 1024**3, commit_headroom=12 * 1024**3)
    incomplete.pop("commit_headroom_bytes")
    with pytest.raises(CPU_PREPARE.CpuPrepareError) as caught:
        CPU_PREPARE._stable_launch_memory_gate(probe=lambda: incomplete, sleep=lambda _: None)
    assert caught.value.code == "CPU_MEMORY_OBSERVATION_FAILED"

    low_physical = _cpu_memory_sample(
        physical=CPU_PREPARE.MINIMUM_RUNTIME_PHYSICAL_AVAILABLE_BYTES - 1,
        commit_headroom=CPU_PREPARE.MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES,
    )
    count, reason = CPU_PREPARE._runtime_memory_violation(low_physical, 0)
    assert count == 1 and reason is None
    count, reason = CPU_PREPARE._runtime_memory_violation(low_physical, count)
    assert count == 2 and "physical" in str(reason)

    low_commit = _cpu_memory_sample(
        physical=CPU_PREPARE.MINIMUM_RUNTIME_PHYSICAL_AVAILABLE_BYTES,
        commit_headroom=CPU_PREPARE.MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES - 1,
    )
    _, reason = CPU_PREPARE._runtime_memory_violation(low_commit, 0)
    assert "commit" in str(reason)


def _bounded_sample(
    *,
    free_vram: int = 8192,
    temperature: int = 65,
    free_ram: int = 8 * 1024**3,
    commit_headroom: int = 15 * 1024**3,
) -> dict[str, int]:
    commit_limit = 64 * 1024**3
    return {
        "gpu_index": 0,
        "start_total_vram_mib": 12227,
        "start_used_vram_mib": 2500,
        "start_free_vram_mib": free_vram,
        "temperature_c": temperature,
        "free_ram_bytes": free_ram,
        "physical_available_bytes": free_ram,
        "physical_total_bytes": 32 * 1024**3,
        "commit_total_bytes": commit_limit - commit_headroom,
        "commit_limit_bytes": commit_limit,
        "commit_headroom_bytes": commit_headroom,
    }


def test_safe_trainer_projects_0_6b_text_embeddings_before_codec_addition() -> None:
    source = (ROOT / "scripts" / "qwen3_tts_sft_safe.py").read_text(encoding="utf-8")
    raw = "raw_text_embedding = model.talker.get_text_embeddings()(input_text_ids)"
    projected = "input_text_embedding = model.talker.text_projection(raw_text_embedding) * text_embedding_mask"
    codec = "input_codec_embedding = model.talker.get_input_embeddings()(input_codec_ids) * codec_embedding_mask"
    combined = "input_embeddings = input_text_embedding + input_codec_embedding"
    assert raw in source
    assert projected in source
    assert codec in source
    assert source.index(raw) < source.index(projected) < source.index(codec) < source.index(combined)
    assert "model.talker.model.text_embedding(input_text_ids) * text_embedding_mask" not in source


def test_cloud_trainer_template_uses_same_0_6b_text_projection() -> None:
    source = (ROOT / "scripts" / "build_qwen3_tts_autodl_bundle.py").read_text(encoding="utf-8")
    assert "model.talker.text_projection(model.talker.get_text_embeddings()(ids[:,:,0]))" in source
    assert "model.talker.model.text_embedding(ids[:,:,0])" not in source


def test_safe_trainer_releases_accelerate_references_before_worker_gpu_sample() -> None:
    source = (ROOT / "scripts" / "qwen3_tts_sft_safe.py").read_text(encoding="utf-8")
    release = "model, optimizer, loader = accelerator.free_memory(model, optimizer, loader)"
    exit_sample = 'exit_gpu = _gpu_runtime_sample()'
    assert release in source
    assert source.index(release) < source.index(exit_sample)
    assert 'if mode != "bounded-probe" and exit_delta > allowed_exit_delta:' in source
    assert '"external_post_exit_gpu_check_required": mode == "bounded-probe"' in source


def test_bounded_probe_allocator_cap_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary = BOUNDED_PROBE.bounded_policy(_bounded_sample())
    assert boundary["decision"] == "GO"
    assert boundary["allocator_cap_mib"] == 7168
    maximum = BOUNDED_PROBE.bounded_policy(_bounded_sample(free_vram=12_000))
    assert maximum["allocator_cap_mib"] == 8704
    assert BOUNDED_PROBE.bounded_policy(_bounded_sample(free_vram=8191))["decision"] == "NO_GO"
    assert BOUNDED_PROBE.bounded_policy(_bounded_sample(temperature=66))["decision"] == "NO_GO"
    assert BOUNDED_PROBE.bounded_policy(_bounded_sample(free_ram=8 * 1024**3 - 1))["decision"] == "NO_GO"
    assert BOUNDED_PROBE.bounded_policy(
        _bounded_sample(commit_headroom=BOUNDED_PROBE.START_MINIMUM_COMMIT_HEADROOM_BYTES - 1)
    )["decision"] == "NO_GO"

    calls: list[tuple[float, int]] = []

    class FakeCuda:
        @staticmethod
        def set_per_process_memory_fraction(fraction: float, device: int) -> None:
            calls.append((fraction, device))

    class FakeTorch:
        cuda = FakeCuda()

    policy = {
        "mode": "bounded-probe",
        "bounded_probe_policy": {
            "allocator_cap_mib": 7168,
            "start_free_vram_mib": 8192,
            "start_total_vram_mib": 12227,
            "system_free_vram_headroom_mib": 1024,
        },
    }
    result = TRAINER._bounded_allocator_policy(policy, FakeTorch())
    assert result and result["allocator_cap_mib"] == 7168
    assert calls == [(7168 / 12227, 0)]
    upper_policy = {
        "mode": "bounded-probe",
        "bounded_probe_policy": {
            "allocator_cap_mib": 7297,
            "start_free_vram_mib": 8321,
            "start_total_vram_mib": 12227,
            "system_free_vram_headroom_mib": 1024,
        },
    }
    monkeypatch.setenv("MENTORFLOW_BOUNDED_ALLOCATOR_CAP_MIB", "7186")
    monkeypatch.setenv("MENTORFLOW_BOUNDED_START_FREE_VRAM_MIB", "8210")
    monkeypatch.setenv("MENTORFLOW_BOUNDED_START_TOTAL_VRAM_MIB", "12227")
    effective = TRAINER._bounded_allocator_policy(upper_policy, FakeTorch())
    assert effective and effective["allocator_cap_mib"] == 7186
    assert effective["plan_allocator_cap_upper_bound_mib"] == 7297
    capped_plan = {
        "mode": "bounded-probe",
        "bounded_probe_policy": {
            "allocator_cap_mib": 7178,
            "start_free_vram_mib": 8202,
            "start_total_vram_mib": 12227,
            "system_free_vram_headroom_mib": 1024,
        },
    }
    monkeypatch.setenv("MENTORFLOW_BOUNDED_ALLOCATOR_CAP_MIB", "7178")
    monkeypatch.setenv("MENTORFLOW_BOUNDED_START_FREE_VRAM_MIB", "8219")
    capped = TRAINER._bounded_allocator_policy(capped_plan, FakeTorch())
    assert capped and capped["allocator_cap_mib"] == 7178


def test_bounded_probe_external_watcher_boundaries_fail_closed() -> None:
    policy = BOUNDED_PROBE.bounded_policy(_bounded_sample(free_vram=9500))
    sample = _bounded_sample(free_vram=512, temperature=80)
    sample["start_used_vram_mib"] = policy["thresholds"]["runtime_maximum_total_gpu_used_mib"]
    assert BOUNDED_PROBE.watcher_violation(policy, sample) is None
    for key, value in (
        ("start_used_vram_mib", sample["start_used_vram_mib"] + 1),
        ("start_free_vram_mib", 511),
        ("temperature_c", 81),
    ):
        bad = dict(sample)
        bad[key] = value
        assert BOUNDED_PROBE.watcher_violation(policy, bad)
    assert BOUNDED_PROBE.watcher_violation(policy, {}) == "resource observation missing or invalid"
    low = dict(sample)
    low["physical_available_bytes"] = BOUNDED_PROBE.RUNTIME_MINIMUM_RAM_BYTES - 1
    count, reason = BOUNDED_PROBE.memory_watcher_violation(low, 0)
    assert count == 1 and reason is None
    count, reason = BOUNDED_PROBE.memory_watcher_violation(low, count)
    assert count == 2 and "two consecutive" in str(reason)
    recovered = dict(sample)
    count, reason = BOUNDED_PROBE.memory_watcher_violation(recovered, 1)
    assert count == 0 and reason is None
    low_commit = dict(sample)
    low_commit["commit_headroom_bytes"] = BOUNDED_PROBE.RUNTIME_MINIMUM_COMMIT_HEADROOM_BYTES - 1
    _, reason = BOUNDED_PROBE.memory_watcher_violation(low_commit, 0)
    assert "commit" in str(reason)
    _, reason = BOUNDED_PROBE.memory_watcher_violation({}, 0)
    assert "missing" in str(reason)
    group_011 = BOUNDED_PROBE.bind_curriculum_runtime_margin(
        policy, {"curriculum_group": {"group_id": "group-011"}}
    )
    assert group_011["runtime_total_used_jitter_margin_mib"] == 512
    assert group_011["watcher_margin_mib"] == 256
    assert group_011["thresholds"]["maximum_exit_gpu_baseline_delta_mib"] == 256
    assert group_011["thresholds"]["runtime_maximum_total_gpu_used_mib"] == (
        group_011["start_used_vram_mib"] + group_011["allocator_cap_mib"] + 512
    )
    hash_value = group_011["policy_sha256"]
    for_hash = dict(group_011)
    for_hash.pop("policy_sha256")
    assert hash_value == hashlib.sha256(BOUNDED_PROBE._canonical(for_hash)).hexdigest()
    group_010 = BOUNDED_PROBE.bind_curriculum_runtime_margin(
        policy, {"curriculum_group": {"group_id": "group-010"}}
    )
    assert group_010["runtime_total_used_jitter_margin_mib"] == 256


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object policy")
def test_bounded_probe_gpu_job_is_gated_capped_and_kill_on_close() -> None:
    gate = BOUNDED_PROBE._WindowsGate()
    job = BOUNDED_PROBE._WindowsGpuJob()
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            BOUNDED_PROBE._GATED_BOOTSTRAP,
            gate.name,
            sys.executable,
            "-I",
            "-c",
            "import sys; raise SystemExit(0 if sys.argv[1] == 'argument with spaces' else 3)",
            "argument with spaces",
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    try:
        job.assign(process)
        limits = job.query()
        assert limits["job_memory_limit_bytes"] == 0
        assert limits["job_memory_limit_enabled"] is False
        assert not limits["limit_flags"] & 0x00000200
        assert limits["limit_flags"] & 0x00002000
        gate.release()
        assert process.wait(timeout=15) == 0
        assert job.query()["peak_job_memory_used_bytes"] > 0
    finally:
        if process.poll() is None:
            job.terminate()
            process.wait(timeout=15)
        gate.close()
        job.close()


def test_rejects_hardlinked_dataset_file(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    source = dataset / "source.bin"
    alias = dataset / "alias.bin"
    source.write_bytes(b"same inode")
    os.link(source, alias)
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH._inventory(dataset)
    assert caught.value.code == "HARDLINK_FORBIDDEN"


def test_resource_probe_reports_go_for_synthetic_safe_resources(tmp_path: Path) -> None:
    usage = shutil._ntuple_diskusage(200 * 1024**3, 20 * 1024**3, 180 * 1024**3)
    value = ORCH.resource_probe(
        mode="dry-run",
        epochs=1,
        model_bytes=1024,
        layout=ORCH.Layout(tmp_path, tmp_path / "g", tmp_path / "r", tmp_path / "c"),
        gpu={"memory_free_mib": 12000, "temperature_c": 50},
        ram={"total_bytes": 32 * 1024**3, "free_bytes": 16 * 1024**3},
        disk=usage,
    )
    assert value["decision"] == "GO"
    assert value["training_started"] is False


def test_resource_probe_reports_all_synthetic_blockers(tmp_path: Path) -> None:
    usage = shutil._ntuple_diskusage(20 * 1024**3, 19 * 1024**3, 1024**3)
    value = ORCH.resource_probe(
        mode="formal",
        epochs=3,
        model_bytes=1024**3,
        layout=ORCH.Layout(tmp_path, tmp_path / "g", tmp_path / "r", tmp_path / "c"),
        gpu={"memory_free_mib": 1000, "temperature_c": 90},
        ram={"total_bytes": 16 * 1024**3, "free_bytes": 1024**3},
        disk=usage,
    )
    assert value["decision"] == "NO_GO"
    assert len(value["reasons"]) == 4


def _pin_fixture(approved_dataset: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    (source / "finetuning").mkdir(parents=True)
    source_files = {
        "LICENSE": b"fixture license",
        "finetuning/prepare_data.py": b"print('fixture')\n",
    }
    for relative, content in source_files.items():
        path = source.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    source_pins = {name: (len(content), hashlib.sha256(content).hexdigest()) for name, content in source_files.items()}
    monkeypatch.setattr(ORCH, "SOURCE_FILES", source_pins)
    source_receipt = source / "SOURCE_RECEIPT.json"
    _json(
        source_receipt,
        {
            "schema": "mentorflow.pinned-source-receipt.v1",
            "repository": ORCH.SOURCE_REPOSITORY,
            "commit": ORCH.SOURCE_COMMIT,
            "fetch_mode": "official-raw-files-only",
            "files": [
                {"path": name, "bytes": size, "sha256": digest}
                for name, (size, digest) in source_pins.items()
            ],
        },
    )
    model = tmp_path / "model"
    (model / "speech_tokenizer").mkdir(parents=True)
    model_files = {"config.json": b"{}", "speech_tokenizer/config.json": b"{}"}
    for relative, content in model_files.items():
        model.joinpath(*relative.split("/")).write_bytes(content)
    model_pins = {name: (len(content), hashlib.sha256(content).hexdigest()) for name, content in model_files.items()}
    tree = hashlib.sha256(
        b"".join(f"{name}\0{size}\0{digest}\n".encode() for name, (size, digest) in model_pins.items())
    ).hexdigest()
    monkeypatch.setattr(ORCH, "MODEL_FILES", model_pins)
    monkeypatch.setattr(ORCH, "MODEL_TREE_SHA256", tree)
    model_receipt = tmp_path / "MODEL_RECEIPT.json"
    _json(
        model_receipt,
        {
            "schema": "mentorflow.trusted-huggingface-model-receipt.v1",
            "repository": ORCH.BASE_MODEL_ID,
            "revision": ORCH.BASE_MODEL_REVISION,
            "license": "Apache-2.0",
            "tree_sha256": tree,
            "files": [
                {"path": name, "bytes": size, "sha256": digest}
                for name, (size, digest) in model_pins.items()
            ],
        },
    )
    wheel = tmp_path / "wheelhouse.json"
    wheel.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        ORCH,
        "_runtime_probe",
        lambda path: {
            "python_executable": str(path),
            "python_executable_sha256": "b" * 64,
            "python": "fixture",
            "packages": ORCH.REQUIRED_PACKAGES,
        },
    )
    resource = {
        "schema": ORCH.RESOURCE_PROBE_SCHEMA,
        "decision": "GO",
        "reasons": [],
        "thresholds": {"runtime_maximum_temperature_c": 85},
    }
    return source, source_receipt, model, model_receipt, wheel, resource


def _passed_probe(contract_sha256: str) -> dict[str, object]:
    return {
        "schema": ORCH.PROBE_RESULT_SCHEMA,
        "status": "passed",
        "probe_contract_sha256": contract_sha256,
        "optimizer_steps_completed": 20,
        "longest_sample_covered": True,
        "loss_and_grad_finite": True,
        "peak_reserved_mib": 8_000,
        "maximum_total_gpu_used_mib": 10_000,
        "maximum_temperature_c": 75,
        "minimum_free_ram_bytes": 8 * 1024**3,
        "exit_gpu_baseline_delta_mib": 256,
        "formal_checkpoint_written": False,
        "network_used": False,
        "offline_environment_verified": True,
    }


def test_builds_hash_locked_offline_plan_and_never_writes_dataset(
    approved_dataset: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, source_receipt, model, model_receipt, wheel, resource = _pin_fixture(
        approved_dataset, tmp_path, monkeypatch
    )
    before = ORCH._inventory(approved_dataset["dataset"], excluded=frozenset({ORCH.QA_APPROVAL_NAME.casefold()}))
    plan = ORCH.build_run_plan(
        dataset_generation_id=approved_dataset["generation_id"],
        run_id="run-001",
        qa_trust_registry=approved_dataset["registry"],
        source_root=source,
        source_receipt=source_receipt,
        model_root=model,
        model_receipt=model_receipt,
        python_executable=Path(sys.executable),
        wheelhouse_evidence=wheel,
        mode="dry-run",
        seed=1234,
        batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-6,
        epochs=9,
        max_optimizer_steps=None,
        speaker_name="tsubaki",
        layout=approved_dataset["layout"],
        now=datetime(2026, 8, 8, tzinfo=timezone.utc),
        resource_value=resource,
    )
    after = ORCH._inventory(approved_dataset["dataset"], excluded=frozenset({ORCH.QA_APPROVAL_NAME.casefold()}))
    assert before == after
    assert plan["training"]["batch_size"] == 1
    assert plan["training"]["gradient_accumulation_steps"] == 8
    assert plan["training"]["max_optimizer_steps"] == 20
    assert plan["training"]["gradient_checkpointing"] == {"enabled": True, "use_reentrant": False}
    assert plan["training"]["use_cache"] is False
    assert plan["training"]["optimizer"] == {
        "name": "ChunkedAdamW",
        "base_math": "AdamW",
        "chunk_bytes": 32 * 1024 * 1024,
        "foreach": False,
        "fused": False,
        "amsgrad": False,
        "capturable": False,
        "differentiable": False,
        "implementation_sha256": _sha(ROOT / "scripts" / "chunked_adamw.py"),
    }
    assert plan["training"]["lora"] is False
    assert plan["training"]["eight_bit_optimizer"] is False
    assert plan["training"]["cpu_offload"] is False
    assert plan["probe_contract"]["probe_gates"]["optimizer_steps"] == 20
    assert plan["offline_environment"]["PIP_NO_INDEX"] == "1"
    assert plan["network_policy"]["downloads_allowed"] is False


def test_run_generation_is_non_overwriting(
    approved_dataset: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, source_receipt, model, model_receipt, wheel, resource = _pin_fixture(
        approved_dataset, tmp_path, monkeypatch
    )
    plan = ORCH.build_run_plan(
        dataset_generation_id=approved_dataset["generation_id"],
        run_id="run-immutable",
        qa_trust_registry=approved_dataset["registry"],
        source_root=source,
        source_receipt=source_receipt,
        model_root=model,
        model_receipt=model_receipt,
        python_executable=Path(sys.executable),
        wheelhouse_evidence=wheel,
        mode="dry-run",
        seed=1,
        batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-6,
        epochs=1,
        max_optimizer_steps=None,
        speaker_name="tsubaki",
        layout=approved_dataset["layout"],
        resource_value=resource,
    )
    run = ORCH._create_run(plan, approved_dataset["manifest"], layout=approved_dataset["layout"])
    assert (run / "RUN_PLAN.json").is_file()
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH._create_run(plan, approved_dataset["manifest"], layout=approved_dataset["layout"])
    assert caught.value.code == "RUN_EXISTS"


def test_rejects_unvalidated_training_profile(
    approved_dataset: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, source_receipt, model, model_receipt, wheel, resource = _pin_fixture(
        approved_dataset, tmp_path, monkeypatch
    )
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH.build_run_plan(
            dataset_generation_id=approved_dataset["generation_id"],
            run_id="run-bad-profile",
            qa_trust_registry=approved_dataset["registry"],
            source_root=source,
            source_receipt=source_receipt,
            model_root=model,
            model_receipt=model_receipt,
            python_executable=Path(sys.executable),
            wheelhouse_evidence=wheel,
            mode="dry-run",
            seed=1,
            batch_size=1,
            gradient_accumulation_steps=4,
            learning_rate=2e-6,
            epochs=1,
            max_optimizer_steps=None,
            speaker_name="tsubaki",
            layout=approved_dataset["layout"],
            resource_value=resource,
        )
    assert caught.value.code == "UNVALIDATED_TRAINING_PROFILE"


def test_formal_plan_requires_matching_passed_probe(
    approved_dataset: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, source_receipt, model, model_receipt, wheel, resource = _pin_fixture(
        approved_dataset, tmp_path, monkeypatch
    )
    common = {
        "dataset_generation_id": approved_dataset["generation_id"],
        "qa_trust_registry": approved_dataset["registry"],
        "source_root": source,
        "source_receipt": source_receipt,
        "model_root": model,
        "model_receipt": model_receipt,
        "python_executable": Path(sys.executable),
        "wheelhouse_evidence": wheel,
        "seed": 1,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "learning_rate": 2e-6,
        "epochs": 1,
        "max_optimizer_steps": None,
        "speaker_name": "tsubaki",
        "layout": approved_dataset["layout"],
        "now": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "resource_value": resource,
    }
    dry_plan = ORCH.build_run_plan(run_id="probe-run", mode="dry-run", **common)
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH.build_run_plan(run_id="formal-no-probe", mode="formal", **common)
    assert caught.value.code == "PROBE_RECEIPT_MISSING"
    receipt = tmp_path / "PROBE_RESULT.json"
    _json(receipt, _passed_probe(dry_plan["probe_contract_sha256"]))
    formal_plan = ORCH.build_run_plan(
        run_id="formal-with-probe",
        mode="formal",
        probe_receipt=receipt,
        **common,
    )
    assert formal_plan["formal_probe_receipt"]["sha256"] == _sha(receipt)
    assert formal_plan["probe_contract_sha256"] == dry_plan["probe_contract_sha256"]


def test_probe_receipt_rejects_bad_metric_type(tmp_path: Path) -> None:
    receipt = tmp_path / "PROBE_RESULT.json"
    value = _passed_probe("c" * 64)
    value["peak_reserved_mib"] = "8000"
    _json(receipt, value)
    with pytest.raises(ORCH.TrainingGateError) as caught:
        ORCH._validate_probe_receipt(receipt, "c" * 64)
    assert caught.value.code == "PROBE_RECEIPT_INVALID"


def test_safe_trainer_requires_every_offline_variable(tmp_path: Path) -> None:
    environment = dict(TRAINER.OFFLINE_VALUES)
    for name in ("HF_HOME", "HF_DATASETS_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME", "PIP_CACHE_DIR", "TMP", "TEMP"):
        environment[name] = str(Path("E:/fixture") / name.lower())
    TRAINER._assert_offline_environment(environment)
    environment["HF_HUB_OFFLINE"] = "0"
    with pytest.raises(TRAINER.SafeTrainerError) as caught:
        TRAINER._assert_offline_environment(environment)
    assert caught.value.code == "OFFLINE_ENV_INVALID"


def test_safe_trainer_runtime_gate_enforces_peak_and_ram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {
        "paths": {"run_root": str(tmp_path)},
        "resource_admission": {
            "thresholds": {
                "runtime_maximum_temperature_c": 80,
                "runtime_maximum_total_gpu_used_mib": 10_691,
                "runtime_maximum_peak_reserved_mib": 8_704,
                "runtime_minimum_free_ram_bytes": 4 * 1024**3,
                "formal_checkpoint_minimum_free_ram_bytes": 12 * 1024**3,
            }
        },
    }
    monkeypatch.setattr(TRAINER, "_gpu_runtime_sample", lambda: {"temperature_c": 70, "total_gpu_used_mib": 9_000})
    monkeypatch.setattr(TRAINER, "_free_ram_bytes", lambda: 8 * 1024**3)
    assert TRAINER._runtime_gate(plan, peak_reserved_mib=8_000)["peak_reserved_mib"] == 8_000
    with pytest.raises(TRAINER.SafeTrainerError) as caught:
        TRAINER._runtime_gate(plan, peak_reserved_mib=8_705)
    assert caught.value.code == "RUNTIME_RESOURCE_GATE"
    monkeypatch.setattr(TRAINER, "_free_ram_bytes", lambda: 11 * 1024**3)
    with pytest.raises(TRAINER.SafeTrainerError) as caught:
        TRAINER._checkpoint_resource_gate(plan, peak_reserved_mib=8_000)
    assert caught.value.code == "CHECKPOINT_RESOURCE_GATE"


def test_checkpoint_commit_is_atomic_verifiable_and_resumable(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "checkpoints"
    checkpoint_root.mkdir()
    stage = tmp_path / "attempt" / "checkpoint-stage"
    stage.mkdir(parents=True)
    (stage / "state.bin").write_bytes(b"state")
    final = TRAINER._commit_checkpoint(
        stage=stage,
        checkpoint_root=checkpoint_root,
        plan_sha256="a" * 64,
        global_step=1,
        state={"next_epoch": 0, "next_batch": 1},
    )
    assert not stage.exists()
    latest, receipt = TRAINER._latest_checkpoint(checkpoint_root, "a" * 64)
    assert latest == final
    assert receipt["global_optimizer_step"] == 1


def test_checkpoint_tamper_is_rejected(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "checkpoints"
    checkpoint_root.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "state.bin").write_bytes(b"state")
    final = TRAINER._commit_checkpoint(
        stage=stage,
        checkpoint_root=checkpoint_root,
        plan_sha256="a" * 64,
        global_step=1,
        state={"next_epoch": 0, "next_batch": 1},
    )
    (final / "state.bin").write_bytes(b"tampered")
    with pytest.raises(TRAINER.SafeTrainerError) as caught:
        TRAINER._latest_checkpoint(checkpoint_root, "a" * 64)
    assert caught.value.code == "CHECKPOINT_INVALID"
