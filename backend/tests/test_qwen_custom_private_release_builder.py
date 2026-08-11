from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_qwen_custom_private_voice_release.py"
SPEC = importlib.util.spec_from_file_location("private_voice_release_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def valid_admission():
    return {
        "schema": "mentorflow.local-private-training-admission.v1",
        "scope": "private_local_only", "display_name": "椿", "style": "neutral",
        "public_or_commercial_release": False, "training_started": False,
        "model_loaded": False, "network_used": False,
    }


def valid_training_manifest():
    return {
        "schema": "mentorflow.local-private-teacher-voice-training-staging.v1",
        "rights": {
            "scope": "private_local_only",
            "grants": {
                "commercial_synthesis": False, "private_local_inference": True,
                "public_release": False, "reference_audio_distribution": False,
                "training": True, "weight_distribution": False,
            },
        },
    }


@pytest.mark.parametrize(
    ("document", "path", "value"),
    [
        ("admission", ("public_or_commercial_release",), True),
        ("manifest", ("rights", "grants", "weight_distribution"), True),
        ("manifest", ("rights", "grants", "reference_audio_distribution"), True),
    ],
)
def test_builder_rejects_private_rights_escalation(document, path, value):
    admission, manifest = valid_admission(), valid_training_manifest()
    target = admission if document == "admission" else manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(builder.BuildError):
        builder.validate_admission_evidence(admission, manifest)


def valid_inference():
    return {
        "schema": "mentorflow.qwen3-tts-offline-audition-receipt.v1",
        "status": "passed", "network_used": False, "port_8000_touched": False,
        "perceptual_quality_human_review_pending": True,
        "checkpoint_verification": {
            "receipt_sha256": builder.CHECKPOINT_RECEIPT_SHA256,
            "tree_sha256": builder.CHECKPOINT_TREE_SHA256,
            "loaded_speaker_slot_3000_exact": True,
            "all_checkpoint_tensors_hash_verified": True,
            "loaded_tensor_schema_count": 402,
        },
        "output": {"qa": {"passed": True, "finite": True, "silence": False,
            "channels": 1, "sample_rate": 24000, "subtype": "PCM_16", "format": "WAV"}},
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("checkpoint_verification", "receipt_sha256"), "0" * 64),
        (("checkpoint_verification", "loaded_speaker_slot_3000_exact"), False),
        (("perceptual_quality_human_review_pending",), False),
        (("output", "qa", "sample_rate"), 16000),
    ],
)
def test_builder_rejects_inference_binding_or_qa_tamper(path, value):
    inference = valid_inference()
    target = inference
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(builder.BuildError):
        builder.validate_inference_evidence(inference)


def test_builder_rejects_unknown_checkpoint_receipt_before_packaging(tmp_path: Path):
    receipt = tmp_path / "CHECKPOINT_RECEIPT.json"
    receipt.write_text(json.dumps({"schema": "mentorflow.qwen3-tts-training-checkpoint.v1"}), encoding="utf-8")
    with pytest.raises(builder.BuildError, match="accepted final group011"):
        builder.validate_checkpoint_evidence(tmp_path)
