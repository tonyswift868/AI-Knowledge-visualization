from __future__ import annotations

import io
import wave

import pytest
from sqlalchemy import select

from app import active_teaching, asr_supervisor, conversion_adapters
from app.conversion_adapters import ConversionRegistry, ConversionSource
from app.orm import ConversionManifestORM, SessionLocal, SourceRevisionORM


def _wav_bytes(duration_ms: int = 250) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * (16_000 * duration_ms // 1000))
    return output.getvalue()


def test_pretty_json_upload_persists_revision_manifest_and_path_chunks(client):
    response = client.post(
        "/api/active-teaching/workspaces/json-domain/materials",
        files={
            "file": (
                "pretty.json",
                b'{\n  "unfamiliar_domain": {\n    "sentinel": "QUARTZ-391"\n  }\n}',
                "application/json",
            )
        },
    )

    assert response.status_code == 200, response.text
    material = response.json()
    assert any("$/unfamiliar_domain/sentinel" in item["text"] for item in material["chunks"])
    assert any("QUARTZ-391" in item["text"] for item in material["chunks"])
    with SessionLocal() as session:
        revision = session.scalar(select(SourceRevisionORM).where(SourceRevisionORM.source_id == material["id"]))
        manifest = session.scalar(
            select(ConversionManifestORM).where(ConversionManifestORM.source_revision_id == revision.id)
        )
    assert revision.status == "ready"
    assert manifest.adapter_name == "native-structured-data"
    assert manifest.metadata_json["part_count"] >= 1


def test_audio_upload_is_disabled_without_database_or_filesystem_mutation(client):
    workspace_id = "audio-disabled-boundary"
    target_dir = active_teaching.UPLOAD_ROOT / workspace_id
    before_files = (
        {str(item.relative_to(target_dir)) for item in target_dir.rglob("*")}
        if target_dir.exists()
        else set()
    )
    with active_teaching._connect() as connection:
        before_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_materials WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/materials",
        files={"file": ("spoken.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "AUDIO_MATERIAL_DISABLED"
    with active_teaching._connect() as connection:
        after_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_materials WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
    after_files = (
        {str(item.relative_to(target_dir)) for item in target_dir.rglob("*")}
        if target_dir.exists()
        else set()
    )
    assert after_rows == before_rows
    assert after_files == before_files


def test_video_rejection_and_provider_precondition_are_explicit(client):
    target_dir = active_teaching.UPLOAD_ROOT / "boundary"
    before_files = (
        {str(item.relative_to(target_dir)) for item in target_dir.rglob("*")}
        if target_dir.exists()
        else set()
    )
    with active_teaching._connect() as connection:
        before_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_materials WHERE workspace_id='boundary'"
            ).fetchone()[0]
        )
    rejected = client.post(
        "/api/active-teaching/workspaces/boundary/materials",
        files={"file": ("lesson.mp4", b"video", "video/mp4")},
    )
    assert rejected.status_code == 415
    assert rejected.json()["error"]["code"] == "VIDEO_MATERIAL_DISABLED"
    with active_teaching._connect() as connection:
        after_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_materials WHERE workspace_id='boundary'"
            ).fetchone()[0]
        )
    after_files = (
        {str(item.relative_to(target_dir)) for item in target_dir.rglob("*")}
        if target_dir.exists()
        else set()
    )
    assert after_rows == before_rows
    assert after_files == before_files

    uploaded = client.post(
        "/api/active-teaching/workspaces/boundary/materials",
        files={"file": ("source.txt", b"PROVIDER-GATE-881", "text/plain")},
    )
    assert uploaded.status_code == 200
    receipt = client.put(
        "/api/active-teaching/workspaces/boundary/receipt",
        json={
            "learning_goal": "test provider boundary",
            "current_level": "beginner",
            "current_foundation": "beginner",
            "start_date": "2099-01-01",
            "deadline": "2099-01-10",
            "study_weekdays": [0, 1, 2, 3, 4],
            "daily_task_minutes": 30,
            "preferred_learning_style": "neutral",
            "material_ids": [uploaded.json()["id"]],
        },
    )
    assert receipt.status_code == 200, receipt.text
    generation = client.post(
        "/api/active-teaching/workspaces/boundary/knowledge-map/generate",
        json={},
    )
    assert generation.status_code == 428
    assert generation.json()["error"]["code"] == "PROVIDER_VERIFICATION_REQUIRED"


def test_unknown_format_is_explicitly_rejected(client):
    rejected = client.post(
        "/api/active-teaching/workspaces/unknown-boundary/materials",
        files={"file": ("unknown.blob", b"unregistered material", "application/x-unknown")},
    )

    assert rejected.status_code == 415
    assert rejected.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_empty_corrupt_encrypted_and_oversized_materials_fail_explicitly(client, monkeypatch):
    empty = client.post(
        "/api/active-teaching/workspaces/empty-boundary/materials",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "MATERIAL_PARSE_FAILED"
    assert "no usable text" in empty.json()["error"]["message"].lower()

    corrupt = ConversionRegistry().convert_safe(
        ConversionSource(name="corrupt.pdf", data=b"%PDF-1.7\nnot-a-document", mime_type="application/pdf")
    )
    assert corrupt.status == "failed"
    assert corrupt.failure is not None
    assert corrupt.failure.code == "PDF_PARSE_FAILED"

    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    document.new_page().insert_text((72, 72), "protected material")
    encrypted = document.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="reader-password",
    )
    document.close()
    protected = ConversionRegistry().convert_safe(
        ConversionSource(name="protected.pdf", data=encrypted, mime_type="application/pdf")
    )
    assert protected.status == "failed"
    assert protected.failure is not None
    assert protected.failure.code == "PDF_ENCRYPTED"
    assert "password" in protected.failure.message.lower()

    monkeypatch.setattr(active_teaching, "MAX_MATERIAL_BYTES", 8)
    oversized = client.post(
        "/api/active-teaching/workspaces/oversized-boundary/materials",
        files={"file": ("large.txt", b"x" * 9, "text/plain")},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "MATERIAL_TOO_LARGE"
    assert oversized.json()["error"]["details"]["max_size_bytes"] == 8


def test_low_quality_ocr_and_no_speech_have_stable_retryable_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversion_adapters,
        "_ocr_parts",
        lambda *_args, **_kwargs: [],
    )
    no_text = ConversionRegistry().convert_safe(
        ConversionSource(
            name="unreadable.png",
            data=b"\x89PNG\r\n\x1a\nlow-quality-fixture",
            mime_type="image/png",
        )
    )
    assert no_text.status == "failed"
    assert no_text.failure is not None
    assert no_text.failure.code == "OCR_NO_TEXT"
    assert no_text.failure.retryable is True

    monkeypatch.setattr(asr_supervisor, "assets_ready", lambda: True)
    monkeypatch.setattr(asr_supervisor, "upload_root", lambda: tmp_path / "asr")
    monkeypatch.setattr(
        asr_supervisor,
        "transcribe",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": "",
            "blank_audio": True,
            "error": "No speech detected",
        },
    )
    no_speech = ConversionRegistry().convert_safe(
        ConversionSource(name="silence.wav", data=_wav_bytes(), mime_type="audio/wav")
    )
    assert no_speech.status == "failed"
    assert no_speech.failure is not None
    assert no_speech.failure.code == "AUDIO_NO_SPEECH"
    assert no_speech.failure.retryable is True
