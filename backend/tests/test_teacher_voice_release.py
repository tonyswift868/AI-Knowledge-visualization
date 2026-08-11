from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.teacher_voice_release import (
    AWAITING_STATUS,
    RUNTIME_URL_ENV,
    TeacherVoiceReleaseError,
    default_manifest_path,
    load_teacher_voice_release_slot,
    validate_new_training_output_path,
    validate_teacher_voice_release_slot,
)


def _awaiting_manifest() -> dict:
    return json.loads(default_manifest_path().read_text(encoding="utf-8"))


def _write(path: Path, data: bytes = b"fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _ready_manifest(tmp_path: Path) -> tuple[dict, Path, dict[str, Path]]:
    manifest = _awaiting_manifest()
    manifest_path = tmp_path / "config" / "voice" / "release.json"
    release_root = tmp_path / "voice-release"
    manifest["status"] = "ready"
    manifest["runtime"]["default_url"] = "http://127.0.0.1:19980"
    manifest["authorization"] = {
        "status": "explicit_permission",
        "evidence": [
            {
                "authorization_id": "permission-001",
                "evidence_path": "rights/permission.txt",
                "covers": ["training", "synthesis"],
            }
        ],
    }
    manifest["storage"]["release_root"] = "../../voice-release"
    manifest["models"] = {
        "gpt_weight": "models/teacher.ckpt",
        "sovits_weight": "models/teacher.pth",
    }
    paths = {
        "evidence": release_root / "rights" / "permission.txt",
        "gpt": release_root / "models" / "teacher.ckpt",
        "sovits": release_root / "models" / "teacher.pth",
        "lively": release_root / "references" / "lively.wav",
        "relaxed": release_root / "references" / "relaxed.wav",
    }
    for path in paths.values():
        _write(path)
    for style_id in ("lively", "relaxed"):
        manifest["styles"][style_id]["reference_audio"] = [
            {
                "path": f"references/{style_id}.wav",
                "transcript": f"{style_id} reference",
                "language": "zh",
                "authorization_id": "permission-001",
            }
        ]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, manifest_path, paths


def _assert_error(code: str, callback) -> None:
    with pytest.raises(TeacherVoiceReleaseError) as exc_info:
        callback()
    assert exc_info.value.code == code


def test_default_slot_is_truthfully_awaiting_authorized_samples() -> None:
    slot = load_teacher_voice_release_slot(environ={})

    assert slot.status == AWAITING_STATUS
    assert slot.authorization_status == AWAITING_STATUS
    assert slot.ready is False
    assert slot.gpt_weight is None
    assert slot.sovits_weight is None
    assert slot.runtime_url == ""
    assert set(slot.styles) == {"lively", "relaxed"}
    assert all(not style.references for style in slot.styles.values())

    _assert_error(
        "release_not_ready",
        lambda: load_teacher_voice_release_slot(environ={}, require_ready=True),
    )


def test_ready_slot_resolves_portable_assets_and_loopback_env_override(tmp_path: Path) -> None:
    _, manifest_path, paths = _ready_manifest(tmp_path)

    slot = load_teacher_voice_release_slot(
        manifest_path,
        environ={RUNTIME_URL_ENV: "http://localhost:19980/"},
        require_ready=True,
    )

    assert slot.ready is True
    assert slot.runtime_url == "http://localhost:19980"
    assert slot.gpt_weight == paths["gpt"].resolve()
    assert slot.sovits_weight == paths["sovits"].resolve()
    assert slot.styles["lively"].references[0].path == paths["lively"].resolve()
    assert slot.styles["relaxed"].references[0].path == paths["relaxed"].resolve()


def test_ready_slot_requires_an_explicit_runtime_endpoint(tmp_path: Path) -> None:
    manifest, manifest_path, _ = _ready_manifest(tmp_path)
    manifest["runtime"]["default_url"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _assert_error(
        "missing_runtime_url",
        lambda: load_teacher_voice_release_slot(
            manifest_path,
            environ={},
            require_ready=True,
        ),
    )


def test_missing_authorization_is_rejected(tmp_path: Path) -> None:
    manifest = _awaiting_manifest()
    manifest.pop("authorization")

    _assert_error(
        "missing_authorization",
        lambda: validate_teacher_voice_release_slot(manifest, tmp_path / "release.json"),
    )

    ready, manifest_path, _ = _ready_manifest(tmp_path / "ready")
    ready["authorization"]["evidence"] = []
    _assert_error(
        "missing_authorization",
        lambda: validate_teacher_voice_release_slot(ready, manifest_path),
    )


@pytest.mark.parametrize("missing_key", ["gpt", "sovits", "lively", "relaxed"])
def test_ready_slot_rejects_missing_model_or_reference_file(
    tmp_path: Path, missing_key: str
) -> None:
    manifest, manifest_path, paths = _ready_manifest(tmp_path)
    paths[missing_key].unlink()

    _assert_error(
        "missing_release_file",
        lambda: validate_teacher_voice_release_slot(manifest, manifest_path),
    )


@pytest.mark.parametrize(
    "runtime_url",
    [
        "https://127.0.0.1:9880",
        "http://192.168.1.20:9880",
        "http://example.com:9880",
        "http://127.0.0.1:9880/tts",
    ],
)
def test_non_loopback_or_non_base_runtime_is_rejected(
    tmp_path: Path, runtime_url: str
) -> None:
    manifest = _awaiting_manifest()
    _assert_error(
        "non_loopback_runtime" if "192.168" in runtime_url or "example" in runtime_url or runtime_url.startswith("https") else "invalid_runtime_url",
        lambda: validate_teacher_voice_release_slot(
            manifest,
            tmp_path / "release.json",
            environ={RUNTIME_URL_ENV: runtime_url},
        ),
    )


def test_style_cannot_masquerade_as_an_identity(tmp_path: Path) -> None:
    manifest = _awaiting_manifest()
    manifest["styles"]["lively"]["identity_id"] = "another_character"
    _assert_error(
        "style_identity_override",
        lambda: validate_teacher_voice_release_slot(manifest, tmp_path / "release.json"),
    )

    manifest = _awaiting_manifest()
    manifest["styles"]["lively"]["speaker_name"] = "named_character"
    _assert_error(
        "style_identity_override",
        lambda: validate_teacher_voice_release_slot(manifest, tmp_path / "release.json"),
    )


def test_existing_and_protected_training_outputs_are_never_overwritten(tmp_path: Path) -> None:
    manifest = _awaiting_manifest()
    manifest["storage"]["release_root"] = "voice-release"
    manifest_path = tmp_path / "release.json"
    manifest["storage"]["overwrite_existing"] = True
    _assert_error(
        "overwrite_forbidden",
        lambda: validate_teacher_voice_release_slot(manifest, manifest_path),
    )

    manifest["storage"]["overwrite_existing"] = False
    slot = validate_teacher_voice_release_slot(manifest, manifest_path)
    existing = slot.training_output_root / "new-run" / "weights.pth"
    _write(existing)
    _assert_error(
        "existing_output_collision",
        lambda: validate_new_training_output_path(slot, "new-run/weights.pth"),
    )
    protected = slot.training_output_root / "protected-model"
    _assert_error(
        "protected_asset_collision",
        lambda: validate_new_training_output_path(
            slot,
            "protected-model/new.pth",
            protected_paths=[protected],
        ),
    )


def test_ready_slot_rejects_reuse_of_a_protected_model(tmp_path: Path) -> None:
    manifest, manifest_path, paths = _ready_manifest(tmp_path)
    _assert_error(
        "protected_asset_collision",
        lambda: validate_teacher_voice_release_slot(
            copy.deepcopy(manifest),
            manifest_path,
            protected_paths=[paths["gpt"]],
        ),
    )
