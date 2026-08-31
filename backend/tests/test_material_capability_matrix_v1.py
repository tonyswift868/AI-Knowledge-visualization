from __future__ import annotations

import io
import json
import mimetypes
import wave
import zipfile
from datetime import date, timedelta
from pathlib import PurePosixPath

from sqlalchemy import select

from app import active_teaching, conversion_adapters, credential_store
from app import generic_knowledge_api as generic_api
from app.conversion_adapters import ConversionRegistry, ConvertedPart
from app.orm import (
    ConversionManifestORM,
    MaterialDispositionORM,
    SessionLocal,
    SourceRevisionORM,
)
from app.provider_runtime import create_provider_session, get_provider_session


WORKSPACE_ID = "capability-matrix-v1"


def _text_payload(suffix: str, sentinel: str) -> bytes:
    if suffix == ".json":
        return json.dumps(
            {"matrix": {"suffix": suffix, "sentinel": sentinel}},
            ensure_ascii=False,
            indent=2,
        ).encode()
    if suffix == ".jsonl":
        return (json.dumps({"suffix": suffix, "sentinel": sentinel}) + "\n").encode()
    if suffix in {".yaml", ".yml"}:
        return f"matrix:\n  suffix: {suffix}\n  sentinel: {sentinel}\n".encode()
    if suffix == ".xml":
        return f"<matrix suffix=\"{suffix}\"><sentinel>{sentinel}</sentinel></matrix>".encode()
    if suffix == ".csv":
        return f"suffix,sentinel\n{suffix},{sentinel}\n".encode()
    if suffix == ".tsv":
        return f"suffix\tsentinel\n{suffix}\t{sentinel}\n".encode()
    if suffix in {".md", ".markdown"}:
        return f"# Capability {suffix}\n\n{sentinel}\n".encode()
    if suffix in {".html", ".htm"}:
        return (
            f"<!doctype html><html><body><h1>Capability {suffix}</h1>"
            f"<p>{sentinel}</p></body></html>"
        ).encode()
    return f"Capability material {suffix}\nSentinel {sentinel}\n".encode()


def _pdf_payload(sentinel: str) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), f"Capability PDF {sentinel}")
    payload = document.tobytes()
    document.close()
    return payload


def _docx_payload(sentinel: str) -> bytes:
    from docx import Document

    output = io.BytesIO()
    document = Document()
    document.add_heading("Capability DOCX", level=1)
    document.add_paragraph(sentinel)
    document.save(output)
    return output.getvalue()


def _pptx_payload(sentinel: str) -> bytes:
    try:
        from pptx import Presentation
    except ImportError:
        Presentation = None
    if Presentation is not None:
        output = io.BytesIO()
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = f"Capability PPTX {sentinel}"
        presentation.save(output)
        return output.getvalue()

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            f"""<?xml version="1.0"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Capability PPTX {sentinel}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>""",
        )
    return output.getvalue()


def _xlsx_payload(sentinel: str) -> bytes:
    try:
        from openpyxl import Workbook
    except ImportError:
        Workbook = None
    if Workbook is not None:
        output = io.BytesIO()
        workbook = Workbook()
        workbook.active["A1"] = f"Capability XLSX {sentinel}"
        workbook.save(output)
        return output.getvalue()

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Capability" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Capability XLSX {sentinel}</t></is></c></row></sheetData>
</worksheet>""",
        )
    return output.getvalue()


def _epub_payload(sentinel: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            f"<html><head><title>Capability EPUB</title></head><body><h1>Capability EPUB</h1><p>{sentinel}</p></body></html>",
        )
    return output.getvalue()


def _image_payload(suffix: str, sentinel: str) -> bytes:
    from PIL import Image, ImageDraw

    output = io.BytesIO()
    image = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(image).text((10, 30), sentinel, fill="black")
    image.save(
        output,
        format={".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[suffix],
    )
    return output.getvalue()


def _wav_payload(sentinel: str) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        sample = max(1, sum(sentinel.encode()) % 127)
        audio.writeframes(bytes((sample, 0)) * 1_600)
    return output.getvalue()


def _audio_payload(suffix: str, sentinel: str) -> bytes:
    if suffix == ".wav":
        return _wav_payload(sentinel)
    marker = sentinel.encode()
    if suffix == ".mp3":
        return b"ID3\x04\x00\x00\x00\x00\x00\x00" + marker
    if suffix == ".m4a":
        return b"\x00\x00\x00\x18ftypM4A " + marker
    if suffix == ".ogg":
        return b"OggS\x00\x02" + marker
    return b"\x1aE\xdf\xa3" + marker


def _zip_payload(sentinel: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("materials/sentinel.txt", sentinel)
    return output.getvalue()


def _material_payload(suffix: str, sentinel: str) -> bytes:
    if suffix == ".pdf":
        return _pdf_payload(sentinel)
    if suffix == ".docx":
        return _docx_payload(sentinel)
    if suffix == ".pptx":
        return _pptx_payload(sentinel)
    if suffix == ".xlsx":
        return _xlsx_payload(sentinel)
    if suffix == ".epub":
        return _epub_payload(sentinel)
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return _image_payload(suffix, sentinel)
    if suffix in {".wav", ".mp3", ".m4a", ".ogg", ".webm"}:
        return _audio_payload(suffix, sentinel)
    if suffix == ".zip":
        return _zip_payload(sentinel)
    return _text_payload(suffix, sentinel)


def _media_type(suffix: str) -> str:
    explicit = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".jsonl": "application/x-ndjson",
        ".tsv": "text/tab-separated-values",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
    }
    return explicit.get(suffix) or mimetypes.guess_type(f"material{suffix}")[0] or "application/octet-stream"


def _active_material_count(workspace_id: str) -> int:
    with active_teaching._connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM active_materials WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
    return int(row[0])


def _tree_snapshot(path) -> set[str]:
    if not path.exists():
        return set()
    return {str(item.relative_to(path)) for item in path.rglob("*")}


def test_every_runtime_registered_planning_format_uploads_converts_and_reaches_the_map(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        conversion_adapters,
        "_ocr_parts",
        lambda raw, suffix, **_kwargs: [
            ConvertedPart(
                text=f"Image evidence {suffix} {len(raw)}",
                ordinal=1,
                kind="ocr-line",
                bbox=(1.0, 2.0, 3.0, 4.0),
            )
        ],
    )

    capability_response = client.get("/api/active-teaching/material-capabilities")
    assert capability_response.status_code == 200, capability_response.text
    capabilities = capability_response.json()["registry"]["capabilities"]
    registry_available_suffixes = sorted(
        {
            suffix
            for capability in capabilities
            if capability["available"] and not capability["rejected"]
            for suffix in capability["suffixes"]
        }
    )
    assert registry_available_suffixes
    registry_suffixes = sorted(
        {
            suffix
            for capability in ConversionRegistry().capabilities()
            if capability.available and not capability.rejected
            for suffix in capability.suffixes
        }
    )
    assert registry_available_suffixes == registry_suffixes
    audio_suffixes = {
        suffix
        for capability in ConversionRegistry().capabilities()
        if capability.adapter == "sensevoice-audio"
        for suffix in capability.suffixes
    }
    planning_suffixes = [
        suffix
        for suffix in registry_available_suffixes
        if suffix not in audio_suffixes
        and not active_teaching._video_upload_hint(
            f"material{suffix}",
            _media_type(suffix),
            b"",
        )
        and not active_teaching._audio_or_subtitle_upload_hint(
            f"material{suffix}",
            _media_type(suffix),
        )
    ]
    assert planning_suffixes

    material_ids: list[str] = []
    uploaded_by_suffix: dict[str, dict] = {}
    for index, suffix in enumerate(planning_suffixes, start=1):
        sentinel = f"MATRIX-{index:03d}-{suffix.removeprefix('.').upper()}"
        response = client.post(
            f"/api/active-teaching/workspaces/{WORKSPACE_ID}/materials",
            files={
                "file": (
                    f"capability-{index:03d}{suffix}",
                    _material_payload(suffix, sentinel),
                    _media_type(suffix),
                )
            },
        )
        assert response.status_code == 200, f"{suffix}: {response.text}"
        material = response.json()
        assert material["chunks"], suffix
        assert material["deduplicated"] is False, suffix
        material_ids.append(material["id"])
        uploaded_by_suffix[suffix] = material

    assert len(material_ids) == len(planning_suffixes)
    assert len(material_ids) == len(set(material_ids))
    with SessionLocal() as session:
        revisions = session.scalars(
            select(SourceRevisionORM).where(SourceRevisionORM.source_id.in_(material_ids))
        ).all()
        manifests = session.scalars(
            select(ConversionManifestORM).where(
                ConversionManifestORM.source_revision_id.in_([row.id for row in revisions])
            )
        ).all()
    assert {row.source_id for row in revisions} == set(material_ids)
    assert len(manifests) == len(material_ids)
    assert all(row.status == "ready" for row in revisions)
    assert all(row.status == "ready" and row.adapter_name for row in manifests)

    receipt = client.put(
        f"/api/active-teaching/workspaces/{WORKSPACE_ID}/receipt",
        json={
            "learning_goal": "validate every runtime-registered material capability",
            "current_level": "beginner",
            "current_foundation": "beginner",
            "target_level": "complete_mastery",
            "start_date": date.today().isoformat(),
            "deadline": (date.today() + timedelta(days=120)).isoformat(),
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "daily_task_minutes": 60,
            "preferred_learning_style": "neutral",
            "discover_external_resources": False,
            "material_ids": material_ids,
        },
    )
    assert receipt.status_code == 200, receipt.text

    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "capability-contract-model",
            "credential_ref": credential_store.store_secret("test-only-memory-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True

    class GroundedProvider:
        async def json(self, messages):
            prompt = json.loads(messages[-1]["content"])
            chunks = prompt["chunks"]
            theme_id = f"theme-{chunks[0]['chunk_id']}"
            return {
                "knowledge_points": [
                    {
                        "id": theme_id,
                        "name": "Batch material theme",
                        "explanation": "Theme for the supplied capability batch.",
                        "node_type": "theme",
                        "layer": "material",
                        "source_chunk_ids": [chunk["chunk_id"] for chunk in chunks],
                    },
                    *[
                    {
                        "id": f"node-{chunk['chunk_id']}",
                        "name": f"Evidence {PurePosixPath(chunk['chunk_id']).name}",
                        "explanation": chunk["text"][:200],
                        "difficulty": "medium",
                        "acceptance_method": "cite the supplied evidence",
                        "node_type": "concept",
                        "layer": "material",
                        "source_chunk_ids": [chunk["chunk_id"]],
                        "confidence": 0.9,
                    }
                    for chunk in chunks
                    ],
                ],
                "dependencies": [
                    {
                        "prerequisite_id": theme_id,
                        "dependent_id": f"node-{chunk['chunk_id']}",
                        "relation": "contains",
                        "layer": "material",
                        "source_chunk_ids": [chunk["chunk_id"]],
                    }
                    for chunk in chunks
                ],
            }

    monkeypatch.setattr(generic_api, "provider_for_session", lambda _session: GroundedProvider())
    queued = client.post(
        f"/api/active-teaching/workspaces/{WORKSPACE_ID}/knowledge-map/jobs",
        json={"provider_session_id": provider["provider_session_id"]},
    )
    assert queued.status_code == 202, queued.text
    job = client.get(
        f"/api/active-teaching/knowledge-map/jobs/{queued.json()['id']}"
    )
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "ready", job.text

    graph = client.get(
        f"/api/active-teaching/workspaces/{WORKSPACE_ID}/knowledge-map",
        params={"revision": "active"},
    )
    assert graph.status_code == 200, graph.text
    graph_payload = graph.json()
    evidence_material_ids = {
        evidence["material_id"]
        for node in graph_payload["nodes"]
        if node["layer"] == "material"
        for evidence in node["evidence"]
    }
    assert evidence_material_ids == set(material_ids)
    assert all(node["evidence"] for node in graph_payload["nodes"] if node["layer"] == "material")
    assert all(edge["evidence"] for edge in graph_payload["edges"] if edge["layer"] == "material")
    with SessionLocal() as session:
        dispositions = session.scalars(
            select(MaterialDispositionORM).where(
                MaterialDispositionORM.map_revision_id == graph_payload["revision_id"]
            )
        ).all()
    assert {row.source_id for row in dispositions} == set(material_ids)
    assert {row.disposition for row in dispositions} == {"included"}


def test_every_registered_video_suffix_is_explicitly_rejected(client):
    capability_response = client.get("/api/active-teaching/material-capabilities")
    assert capability_response.status_code == 200
    capabilities = capability_response.json()["registry"]["capabilities"]
    all_suffixes = {
        suffix
        for capability in capabilities
        for suffix in capability["suffixes"]
    }
    rejected_suffixes = sorted({
        suffix
        for suffix in all_suffixes
        if active_teaching._video_upload_hint(
            f"material{suffix}",
            _media_type(suffix),
            b"",
        )
    })
    assert rejected_suffixes
    for index, suffix in enumerate(rejected_suffixes, start=1):
        workspace_id = f"rejected-video-matrix-{index}"
        target_dir = active_teaching.UPLOAD_ROOT / workspace_id
        before_files = _tree_snapshot(target_dir)
        before_rows = _active_material_count(workspace_id)
        response = client.post(
            f"/api/active-teaching/workspaces/{workspace_id}/materials",
            files={
                "file": (
                    f"unsupported-{index}{suffix}",
                    f"video-{index}".encode(),
                    _media_type(suffix),
                )
            },
        )
        assert response.status_code == 415, f"{suffix}: {response.text}"
        assert response.json()["error"]["code"] == "VIDEO_MATERIAL_DISABLED"
        assert _active_material_count(workspace_id) == before_rows
        assert _tree_snapshot(target_dir) == before_files


def test_every_registered_audio_suffix_is_explicitly_rejected_without_mutation(client):
    audio_suffixes = sorted(
        {
            suffix
            for capability in ConversionRegistry().capabilities()
            if capability.adapter == "sensevoice-audio"
            for suffix in capability.suffixes
            if not active_teaching._video_upload_hint(
                f"material{suffix}",
                _media_type(suffix),
                b"",
            )
        }
    )
    assert audio_suffixes
    for index, suffix in enumerate(audio_suffixes, start=1):
        workspace_id = f"rejected-audio-matrix-{index}"
        target_dir = active_teaching.UPLOAD_ROOT / workspace_id
        before_files = _tree_snapshot(target_dir)
        before_rows = _active_material_count(workspace_id)
        filename = f"unsupported-{index}{suffix}"
        payload = _audio_payload(suffix, f"audio-{index}")
        media_type = _media_type(suffix)
        response = client.post(
            f"/api/active-teaching/workspaces/{workspace_id}/materials",
            files={
                "file": (
                    filename,
                    payload,
                    media_type,
                )
            },
        )
        assert response.status_code == 415, f"{suffix}: {response.text}"
        expected_code = (
            "VIDEO_MATERIAL_DISABLED"
            if active_teaching._video_upload_hint(filename, media_type, payload)
            else "AUDIO_MATERIAL_DISABLED"
        )
        assert response.json()["error"]["code"] == expected_code, suffix
        assert _active_material_count(workspace_id) == before_rows
        assert _tree_snapshot(target_dir) == before_files
