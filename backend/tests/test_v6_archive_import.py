from __future__ import annotations

import io
import json
import struct
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import archive_import, db


def make_zip(files: dict[str, str | bytes], *, encrypted: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            if encrypted == name:
                info.flag_bits |= 0x1
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content)
    raw = bytearray(output.getvalue())
    if encrypted:
        cursor = 0
        while True:
            local = raw.find(b"PK\x03\x04", cursor)
            if local < 0:
                break
            struct.pack_into("<H", raw, local + 6, 1)
            cursor = local + 4
        cursor = 0
        while True:
            central = raw.find(b"PK\x01\x02", cursor)
            if central < 0:
                break
            struct.pack_into("<H", raw, central + 8, 1)
            cursor = central + 4
    return bytes(raw)


def workspace(client):
    return client.post("/api/workspaces", json={"title": "Archive workspace", "execution_mode": "demo"}).json()["id"]


def preview(client, workspace_id, payload, filename="study_bundle.zip", encrypted=None):
    return client.post(f"/api/workspaces/{workspace_id}/sources/archive/preview", files={"file": (filename, make_zip(payload, encrypted=encrypted), "application/zip")})


def test_normal_preview_and_import(client):
    ws = workspace(client)
    response = preview(client, ws, {"notes/java/jvm.md": "# JVM\n\nEvidence", "ignored.exe": "MZ"})
    assert response.status_code == 200
    assert "storage_path" not in response.text
    assert str(Path(db.DATA_DIR).resolve()) not in response.text
    body = response.json()
    assert body["supported_count"] == 1 and body["rejected_count"] == 1
    item = next(x for x in body["tree"] if x["status"] == "supported")
    imported = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": body["preview_id"], "selected_file_ids": [item["id"]]}).json()
    assert len(imported["imported"]) == 1
    assert imported["imported"][0]["source"]["title"] == "study_bundle.zip / notes/java/jvm.md"
    assert not (Path(db.DATA_DIR) / "archive_previews" / body["preview_id"]).exists()


def test_document_and_code_are_supported_with_language(client):
    ws = workspace(client)
    body = preview(client, ws, {"src/main.py": "print('safe')", "guide.markdown": "# Guide"}).json()
    assert {item["kind"] for item in body["tree"]} == {"code", "document"}
    assert next(item for item in body["tree"] if item["kind"] == "code")["language"] == "python"


def test_relative_path_and_language_are_persisted(client):
    ws = workspace(client)
    body = preview(client, ws, {"src/main.ts": "const answer = 42"}).json()
    item = body["tree"][0]
    result = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": body["preview_id"], "selected_file_ids": [item["id"]]}).json()
    source = result["imported"][0]["source"]
    assert source["archive_relative_path"] == "src/main.ts"
    assert source["language"] == "typescript"


def test_zip_slip_is_rejected(client):
    ws = workspace(client)
    response = preview(client, ws, {"../escape.md": "bad"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_PATH_TRAVERSAL"


@pytest.mark.parametrize("name", ["/etc/passwd", "C:\\Windows\\x.md", "\\\\server\\share\\x.md"])
def test_absolute_drive_and_unc_paths_are_rejected(client, name):
    ws = workspace(client)
    response = preview(client, ws, {name: "bad"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_PATH_TRAVERSAL"


def test_symlink_is_rejected(client):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        info = zipfile.ZipInfo("link.md")
        info.create_system = 3
        info.external_attr = (0o120777 << 16) | 0xA0000000
        zf.writestr(info, "target.md")
    ws = workspace(client)
    response = client.post(f"/api/workspaces/{ws}/sources/archive/preview", files={"file": ("link.zip", output.getvalue(), "application/zip")})
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_LINK_NOT_ALLOWED"


def test_too_many_files_is_rejected(client):
    ws = workspace(client)
    archive_import.MAX_FILES = 3
    try:
        response = preview(client, ws, {f"f{i}.md": "x" for i in range(4)})
    finally:
        archive_import.MAX_FILES = 2000
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_TOO_MANY_FILES"


def test_expanded_size_is_rejected(client):
    ws = workspace(client)
    old = archive_import.MAX_EXPANDED_BYTES
    archive_import.MAX_EXPANDED_BYTES = 4
    try:
        response = preview(client, ws, {"large.txt": "12345"})
    finally:
        archive_import.MAX_EXPANDED_BYTES = old
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_EXPANDED_SIZE_TOO_LARGE"


def test_single_and_overall_compression_ratios_are_rejected(client):
    ws = workspace(client)
    old_single, old_overall = archive_import.MAX_SINGLE_RATIO, archive_import.MAX_OVERALL_RATIO
    archive_import.MAX_SINGLE_RATIO = 1
    archive_import.MAX_OVERALL_RATIO = 1
    try:
        response = preview(client, ws, {"compressed.txt": "a" * 5000})
    finally:
        archive_import.MAX_SINGLE_RATIO, archive_import.MAX_OVERALL_RATIO = old_single, old_overall
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_SUSPICIOUS_COMPRESSION_RATIO"


def test_encrypted_zip_is_explicitly_rejected(client):
    ws = workspace(client)
    response = preview(client, ws, {"secret.md": "secret"}, filename="encrypted.zip", encrypted="secret.md")
    # The stdlib writes the encryption flag in the central directory for this fixture.
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_ENCRYPTED_NOT_SUPPORTED"


def test_nested_archive_is_ignored_and_not_recursive(client):
    ws = workspace(client)
    body = preview(client, ws, {"nested.zip": b"PK\x03\x04", "readme.md": "hello"}).json()
    nested = next(item for item in body["tree"] if item["filename"] == "nested.zip")
    assert nested["status"] == "ignored" and "嵌套" in nested["reason"]


def test_expired_preview_is_rejected(client):
    ws = workspace(client)
    body = preview(client, ws, {"notes.md": "hello"}).json()
    with db.connection() as conn:
        conn.execute("UPDATE archive_previews SET expires_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), body["preview_id"]))
    response = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": body["preview_id"], "selected_file_ids": [body["tree"][0]["id"]]})
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_PREVIEW_EXPIRED"


def test_empty_selection_is_rejected(client):
    ws = workspace(client)
    body = preview(client, ws, {"notes.md": "hello"}).json()
    response = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": body["preview_id"], "selected_file_ids": []})
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_SELECTION_EMPTY"


def test_item_id_must_belong_to_preview(client):
    ws = workspace(client)
    body = preview(client, ws, {"notes.md": "hello"}).json()
    response = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": body["preview_id"], "selected_file_ids": ["af_not_this_preview"]})
    assert response.status_code == 400 and response.json()["error"]["code"] == "ARCHIVE_ITEM_NOT_IN_PREVIEW"


def test_hash_deduplication_is_workspace_scoped(client):
    ws = workspace(client)
    first = preview(client, ws, {"one.md": "same content"}).json()
    item = first["tree"][0]
    client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": first["preview_id"], "selected_file_ids": [item["id"]]})
    second = preview(client, ws, {"two.md": "same content"}).json()
    result = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": second["preview_id"], "selected_file_ids": [second["tree"][0]["id"]]}).json()
    assert len(result["duplicates"]) == 1 and not result["imported"]


def test_one_bad_file_does_not_abort_other_files(client):
    ws = workspace(client)
    body = preview(client, ws, {"good.md": "valid", "bad.docx": b"PK\x03\x04not a real docx"}).json()
    selected = [item["id"] for item in body["tree"] if item["status"] == "supported"]
    result = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": body["preview_id"], "selected_file_ids": selected}).json()
    assert any(item["relative_path"] == "good.md" for item in result["imported"])
    assert any(item["relative_path"] == "bad.docx" for item in result["failed"])


def test_partial_archive_can_retry_only_failed_items(client, monkeypatch):
    ws = workspace(client)
    body = preview(client, ws, {"good.md": "valid", "bad.docx": b"PK\x03\x04not a real docx"}).json()
    selected = [item["id"] for item in body["tree"] if item["status"] == "supported"]
    first = client.post(
        f"/api/workspaces/{ws}/sources/archive/import",
        json={"preview_id": body["preview_id"], "selected_file_ids": selected},
    ).json()
    assert first["status"] == "partial"
    assert first["retryable_file_ids"]
    assert (Path(db.DATA_DIR) / "archive_previews" / body["preview_id"]).exists()

    from app import ingestion_secure

    original_parse = ingestion_secure.parse_file

    def repaired_parse(path):
        if Path(path).name == "bad.docx":
            return ([{"text": "repaired document", "page": 1, "heading": ""}], "ready", 1)
        return original_parse(path)

    monkeypatch.setattr(ingestion_secure, "parse_file", repaired_parse)
    second = client.post(
        f"/api/workspaces/{ws}/sources/archive/import",
        json={"preview_id": body["preview_id"], "selected_file_ids": first["retryable_file_ids"]},
    )
    assert second.status_code == 200
    retry = second.json()
    assert retry["status"] == "imported"
    assert retry["archive_source_id"] == first["archive_source_id"]
    assert [item["relative_path"] for item in retry["imported"]] == ["bad.docx"]
    assert not retry["failed"]
    assert not (Path(db.DATA_DIR) / "archive_previews" / body["preview_id"]).exists()


def test_ipynb_extracts_cells_without_execution(client, tmp_path):
    ws = workspace(client)
    marker = tmp_path / "should_not_exist.txt"
    notebook = {"cells": [{"cell_type": "markdown", "source": ["# Notes"]}, {"cell_type": "code", "source": [f"from pathlib import Path\nPath(r'{marker}').write_text('x')"]}]}
    body = preview(client, ws, {"lesson.ipynb": json.dumps(notebook)}).json()
    result = client.post(f"/api/workspaces/{ws}/sources/archive/import", json={"preview_id": body["preview_id"], "selected_file_ids": [body["tree"][0]["id"]]}).json()
    assert result["imported"] and not marker.exists()


def test_preview_and_import_endpoints_are_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/workspaces/{workspace_id}/sources/archive/preview" in paths
    assert "/api/workspaces/{workspace_id}/sources/archive/import" in paths


def test_expired_previews_are_cleaned_on_startup(client):
    ws = workspace(client)
    body = preview(client, ws, {"notes.md": "hello"}).json()
    with db.connection() as conn:
        conn.execute("UPDATE archive_previews SET expires_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), body["preview_id"]))
    archive_import.cleanup_expired_previews()
    assert not (Path(db.DATA_DIR) / "archive_previews" / body["preview_id"]).exists()


def test_cancel_preview_cleans_temp_directory(client):
    ws = workspace(client)
    body = preview(client, ws, {"notes.md": "hello"}).json()
    response = client.delete(f"/api/workspaces/{ws}/sources/archive/preview/{body['preview_id']}")
    assert response.status_code == 200 and response.json()["cancelled"] is True
    assert not (Path(db.DATA_DIR) / "archive_previews" / body["preview_id"]).exists()
