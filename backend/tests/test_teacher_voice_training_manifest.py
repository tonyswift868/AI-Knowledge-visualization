from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import sys
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


SCRIPT = Path(__file__).parents[2] / "scripts" / "build_teacher_voice_training_manifest.py"
SPEC = importlib.util.spec_from_file_location("teacher_voice_training_manifest", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wav(path: Path, *, frames: int = 24_000, rate: int = 24_000, amplitude: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = array("h", (amplitude if index % 2 else -amplitude for index in range(frames)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _entry(path: Path, relative: str) -> dict[str, object]:
    return {"path": relative, "bytes": path.stat().st_size, "sha256": _sha(path)}


def _signed_envelope(schema: str, payload: dict, private_key: Ed25519PrivateKey, key_id: str = "release-root-1") -> dict:
    signature = private_key.sign(MODULE._canonical_json_bytes(payload))
    return {
        "schema": schema,
        "payload": payload,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": key_id,
            "value_base64": base64.b64encode(signature).decode("ascii"),
        },
    }


def _replace_signed_payload(path: Path, private_key: Ed25519PrivateKey, mutate) -> None:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    mutate(envelope["payload"])
    _write_json(path, _signed_envelope(envelope["schema"], envelope["payload"], private_key))


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    corpus = tmp_path / "corpus"
    rows = []
    for style_number, style in enumerate(MODULE.REQUIRED_STYLES):
        for index in range(5):
            relative = f"{style}/{index}.wav"
            _wav(
                corpus / relative,
                frames=24_000 + style_number * 100 + index,
                amplitude=700 + style_number * 100 + index * 25,
            )
            rows.append(
                {
                    "audio": relative,
                    "text": f"{style} unique training sentence {index}",
                    "language": "zh",
                    "style": style,
                    "speaker_id": "original_mentorflow_teacher",
                    "authorization_id": "original-corpus-001",
                }
            )
    reference_relative = "reference/original-reference.wav"
    reference = corpus / reference_relative
    _wav(reference, frames=25_000, amplitude=1300)
    reference_text = "这是原创教师声音的固定参考文本。"
    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    source_root = tmp_path / "qwen-source"
    source_contents = {
        "LICENSE": b"fixture-license",
        "README.md": b"fixture-readme",
        "pyproject.toml": b"fixture-project",
        "finetuning/README.md": b"fixture-finetune-readme",
        "finetuning/dataset.py": b"fixture-dataset",
        "finetuning/prepare_data.py": b"fixture-prepare",
        "finetuning/sft_12hz.py": b"fixture-train",
    }
    source_pins: dict[str, tuple[int, str]] = {}
    for relative, content in source_contents.items():
        path = source_root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        source_pins[relative] = (len(content), hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(MODULE, "PINNED_SOURCE_FILES", source_pins)
    source_receipt = source_root / "SOURCE_RECEIPT.json"
    _write_json(
        source_receipt,
        {
            "schema": MODULE.SOURCE_RECEIPT_SCHEMA,
            "repository": MODULE.UPSTREAM_REPOSITORY,
            "commit": MODULE.UPSTREAM_COMMIT,
            "fetch_mode": "official-raw-files-only",
            "executed": False,
            "files": [
                {"path": path, "bytes": facts[0], "sha256": facts[1]}
                for path, facts in sorted(source_pins.items())
            ],
        },
    )

    model = tmp_path / "model"
    model_revision = "a" * 40
    model_contents: dict[str, bytes] = {}
    model_config = {
        "model_type": "qwen3_tts",
        "tts_model_type": "base",
        "tts_model_size": "0b6",
        "tokenizer_type": "qwen3_tts_tokenizer_12hz",
    }
    model_contents["config.json"] = json.dumps(model_config).encode("utf-8")
    for relative in (
        "generation_config.json",
        "merges.txt",
        "model.safetensors",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
        "speech_tokenizer/config.json",
        "speech_tokenizer/configuration.json",
        "speech_tokenizer/model.safetensors",
        "speech_tokenizer/preprocessor_config.json",
    ):
        model_contents[relative] = f"fixture:{relative}".encode("utf-8")
    model_pins: dict[str, tuple[int, str]] = {}
    for relative, content in model_contents.items():
        path = model.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        model_pins[relative] = (len(content), hashlib.sha256(content).hexdigest())
        metadata = model / ".cache" / "huggingface" / "download" / f"{relative}.metadata"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(f"{model_revision}\nfixture-etag\n0\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "PINNED_MODEL_FILES", model_pins)
    monkeypatch.setattr(MODULE, "BASE_MODEL_REVISION", model_revision)
    model_receipt = model / "MODEL_RECEIPT.json"
    _write_json(
        model_receipt,
        {
            "schema": MODULE.MODEL_RECEIPT_SCHEMA,
            "model_id": MODULE.BASE_MODEL_ID,
            "revision": model_revision,
            "files": [
                {"path": path, "bytes": facts[0], "sha256": facts[1]}
                for path, facts in sorted(model_pins.items())
            ],
        },
    )

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trusted_signers = tmp_path / "trusted-signers.json"
    _write_json(
        trusted_signers,
        {
            "schema": MODULE.TRUSTED_SIGNERS_SCHEMA,
            "signers": [
                {
                    "key_id": "release-root-1",
                    "public_key_base64": base64.b64encode(public_key).decode("ascii"),
                    "purposes": ["rights", "speaker_qa"],
                }
            ],
        },
    )

    loaded = MODULE._load_samples(
        source,
        corpus,
        "original_mentorflow_teacher",
        "original-corpus-001",
    )
    corpus_digest = MODULE._corpus_digest(loaded)
    reference_sha = _sha(reference)
    _, reference_text_sha = MODULE._normalize_text(reference_text, "reference_text")

    receipts = tmp_path / "receipts"
    rights_evidence = receipts / "rights-evidence.txt"
    rights_evidence.parent.mkdir(parents=True, exist_ok=True)
    rights_evidence.write_text("signed original voice rights evidence", encoding="utf-8")
    rights_payload = {
        "authorization_id": "original-corpus-001",
        "status": "granted",
        "valid_from": "2029-01-01T00:00:00Z",
        "valid_until": "2031-01-01T00:00:00Z",
        "grants": {name: True for name in sorted(MODULE.REQUIRED_RIGHTS)},
        "subject": {
            "candidate_id": "candidate-a",
            "speaker_id": "original_mentorflow_teacher",
            "voice_identity": {"kind": "original", "impersonates": False, "source_identity": None},
            "reference_audio_sha256": reference_sha,
            "reference_text_sha256": reference_text_sha,
            "corpus_digest": corpus_digest,
        },
        "evidence_files": [_entry(rights_evidence, rights_evidence.name)],
    }
    rights = receipts / "rights.json"
    _write_json(rights, _signed_envelope(MODULE.RIGHTS_SCHEMA, rights_payload, private_key))

    speaker_evidence = receipts / "speaker-qa.json"
    speaker_evidence.write_text('{"all_samples_assessed":true}', encoding="utf-8")
    speaker_payload = {
        "status": "passed",
        "method": MODULE.SPEAKER_QA_METHOD,
        "valid_from": "2029-01-01T00:00:00Z",
        "valid_until": "2031-01-01T00:00:00Z",
        "subject": {
            "candidate_id": "candidate-a",
            "speaker_id": "original_mentorflow_teacher",
            "reference_audio_sha256": reference_sha,
            "corpus_digest": corpus_digest,
        },
        "result": {
            "passed": True,
            "assessed_samples": len(loaded),
            "minimum_similarity_threshold": 0.80,
            "minimum_reference_similarity": 0.92,
            "maximum_outlier_fraction": 0.05,
            "outlier_fraction": 0.0,
        },
        "evidence_files": [_entry(speaker_evidence, speaker_evidence.name)],
    }
    speaker_qa = receipts / "speaker-qa-receipt.json"
    _write_json(speaker_qa, _signed_envelope(MODULE.SPEAKER_QA_SCHEMA, speaker_payload, private_key))

    return {
        "corpus": corpus,
        "source": source,
        "source_receipt": source_receipt,
        "model": model,
        "model_receipt": model_receipt,
        "trusted_signers": trusted_signers,
        "rights": rights,
        "speaker_qa": speaker_qa,
        "private_key": private_key,
        "reference_relative": reference_relative,
        "reference": reference,
        "reference_text": reference_text,
    }


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return _fixture(tmp_path, monkeypatch)


def _build(paths: dict[str, object]) -> dict:
    return MODULE.build_manifest(
        source_jsonl=paths["source"],
        corpus_root=paths["corpus"],
        rights_path=paths["rights"],
        speaker_qa_path=paths["speaker_qa"],
        trusted_signers_path=paths["trusted_signers"],
        source_receipt_path=paths["source_receipt"],
        base_model_root=paths["model"],
        base_model_receipt_path=paths["model_receipt"],
        reference_audio=paths["reference_relative"],
        reference_text=paths["reference_text"],
        candidate_id="candidate-a",
        speaker_id="original_mentorflow_teacher",
        validation_per_style=1,
        heldout_per_style=1,
        now=NOW,
    )


def test_build_is_deterministic_three_way_signed_and_truthful(case: dict[str, object]) -> None:
    first = _build(case)
    second = _build(case)

    assert first == second
    assert first["schema"] == MODULE.SCHEMA
    assert first["summary"]["train_samples"] == 6
    assert first["summary"]["validation_samples"] == 2
    assert first["summary"]["heldout_samples"] == 2
    assert {item["style"] for item in first["train"]} == {"lively", "relaxed"}
    assert {item["style"] for item in first["validation"]} == {"lively", "relaxed"}
    assert {item["style"] for item in first["heldout"]} == {"lively", "relaxed"}
    assert first["upstream_contract"]["this_manifest_is_upstream_schema"] is False
    assert first["upstream_contract"]["status"].endswith("training_not_started")
    assert first["rights"]["signer_key_id"] == "release-root-1"
    assert first["speaker_qa"]["assessed_samples"] == 10
    MODULE._assert_split_isolation(first["train"], first["validation"], first["heldout"])


def test_export_is_exactly_train_only_and_audited(case: dict[str, object], tmp_path: Path) -> None:
    manifest = _build(case)
    output = tmp_path / "export" / "train_raw.jsonl"

    audit = MODULE.export_upstream_train_jsonl(manifest, case["corpus"], output)

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == manifest["summary"]["train_samples"]
    assert all(set(row) == {"audio", "text", "ref_audio"} for row in rows)
    assert {Path(row["audio"]).resolve() for row in rows} == {
        (case["corpus"] / item["audio"]).resolve() for item in manifest["train"]
    }
    excluded = {
        (case["corpus"] / item["audio"]).resolve()
        for item in manifest["validation"] + manifest["heldout"]
    }
    assert not excluded.intersection(Path(row["audio"]).resolve() for row in rows)
    assert {Path(row["ref_audio"]).resolve() for row in rows} == {case["reference"].resolve()}
    assert audit["exported_train_samples"] == 6
    assert audit["excluded_validation_samples"] == 2
    assert audit["excluded_heldout_samples"] == 2
    assert audit["raw_jsonl_sha256"] == _sha(output)
    assert audit["status"].endswith("training_not_started")


def test_rejects_normalized_transcript_duplicate_before_split(case: dict[str, object]) -> None:
    rows = [json.loads(line) for line in case["source"].read_text(encoding="utf-8").splitlines()]
    rows[0]["text"] = "ＡＢＣ　教师"
    rows[1]["text"] = "abc 教师"
    case["source"].write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(MODULE.ManifestValidationError, match="normalized transcript") as exc_info:
        _build(case)

    assert exc_info.value.code == "DUPLICATE_TRANSCRIPT"


def test_rejects_duplicate_audio_bytes_globally(case: dict[str, object]) -> None:
    duplicate = case["corpus"] / "lively" / "1.wav"
    duplicate.write_bytes((case["corpus"] / "lively" / "0.wav").read_bytes())

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "DUPLICATE_SAMPLE"


def test_rejects_signed_denied_rights(case: dict[str, object]) -> None:
    _replace_signed_payload(case["rights"], case["private_key"], lambda payload: payload.update(status="denied"))

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "RIGHTS_INCOMPLETE"


def test_rejects_tampered_rights_signature(case: dict[str, object]) -> None:
    envelope = json.loads(case["rights"].read_text(encoding="utf-8"))
    envelope["payload"]["status"] = "denied"
    _write_json(case["rights"], envelope)

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "SIGNATURE_INVALID"


def test_rejects_signed_speaker_qa_failure(case: dict[str, object]) -> None:
    def fail(payload: dict) -> None:
        payload["status"] = "failed"
        payload["result"]["passed"] = False

    _replace_signed_payload(case["speaker_qa"], case["private_key"], fail)

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "SPEAKER_QA_FAILED"


def test_rejects_truncated_wav_even_when_header_claims_full_frames(case: dict[str, object]) -> None:
    audio = case["corpus"] / "lively" / "0.wav"
    with audio.open("r+b") as handle:
        handle.truncate(100)

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "INVALID_AUDIO"


@pytest.mark.parametrize("amplitude", [0, 32767])
def test_rejects_silence_and_clipping(case: dict[str, object], amplitude: int) -> None:
    _wav(case["corpus"] / "lively" / "0.wav", amplitude=amplitude)

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "INVALID_AUDIO"


@pytest.mark.parametrize(
    "unsafe",
    [
        "carrier:sample.wav",
        "CON.wav",
        "folder/name.wav. ",
        "folder/name.wav ",
        " folder/name.wav",
        "folder/control\x01.wav",
        "../escape.wav",
        "folder//name.wav",
        "folder/./name.wav",
        "folder\\name.wav",
    ],
)
def test_rejects_ads_reserved_trailing_control_and_traversal_paths(unsafe: str) -> None:
    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        MODULE._safe_relative(unsafe, "audit")

    assert exc_info.value.code == "UNSAFE_PATH"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_similarity_threshold", "NaN"),
        ("minimum_reference_similarity", "Infinity"),
        ("maximum_outlier_fraction", "-Infinity"),
        ("outlier_fraction", "NaN"),
    ],
)
def test_rejects_signed_non_finite_speaker_qa_metrics(
    case: dict[str, object], field: str, value: str
) -> None:
    def make_non_finite(payload: dict) -> None:
        payload["result"][field] = value

    _replace_signed_payload(case["speaker_qa"], case["private_key"], make_non_finite)

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "SPEAKER_QA_FAILED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_similarity_threshold", 1.01),
        ("minimum_reference_similarity", -0.01),
        ("maximum_outlier_fraction", -0.01),
        ("outlier_fraction", 1.01),
    ],
)
def test_rejects_signed_out_of_range_speaker_qa_metrics(
    case: dict[str, object], field: str, value: float
) -> None:
    def make_out_of_range(payload: dict) -> None:
        payload["result"][field] = value

    _replace_signed_payload(case["speaker_qa"], case["private_key"], make_out_of_range)

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "SPEAKER_QA_FAILED"


def test_rejects_tampered_model_bytes_and_fake_receipt(case: dict[str, object]) -> None:
    model_weight = case["model"] / "model.safetensors"
    model_weight.write_bytes(b"fake model")

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "PROVENANCE_MISMATCH"

    model_weight.write_bytes(b"fixture:model.safetensors")
    receipt = json.loads(case["model_receipt"].read_text(encoding="utf-8"))
    receipt["files"][0]["sha256"] = "0" * 64
    _write_json(case["model_receipt"], receipt)
    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)
    assert exc_info.value.code == "PROVENANCE_MISMATCH"


def test_rejects_tampered_pinned_source(case: dict[str, object]) -> None:
    (case["source_receipt"].parent / "finetuning" / "sft_12hz.py").write_text("tampered", encoding="utf-8")

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code == "PROVENANCE_MISMATCH"


def test_missing_model_release_receipt_fails_closed(case: dict[str, object]) -> None:
    case["model_receipt"].unlink()

    with pytest.raises(FileNotFoundError):
        _build(case)


def test_export_rejects_cross_split_leakage(case: dict[str, object], tmp_path: Path) -> None:
    manifest = _build(case)
    leaked = copy.deepcopy(manifest)
    leaked["validation"][0] = copy.deepcopy(leaked["train"][0])

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        MODULE.export_upstream_train_jsonl(leaked, case["corpus"], tmp_path / "leaked.jsonl")

    assert exc_info.value.code == "SPLIT_LEAKAGE"


def test_reference_audio_cannot_be_a_dataset_sample(case: dict[str, object]) -> None:
    case["reference"].write_bytes((case["corpus"] / "lively" / "0.wav").read_bytes())

    with pytest.raises(MODULE.ManifestValidationError) as exc_info:
        _build(case)

    assert exc_info.value.code in {"RIGHTS_INCOMPLETE", "SPLIT_LEAKAGE"}
