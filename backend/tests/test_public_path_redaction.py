from __future__ import annotations

import json

from app import db
import app.main as main_module
from app.services import create_source


def _payload_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_legacy_source_and_local_preview_payloads_redact_absolute_paths(
    client, tmp_path
):
    root = tmp_path / "SentinelUser" / "local-materials"
    root.mkdir(parents=True)
    (root / "notes.md").write_text("# Notes\n\nprivate material", encoding="utf-8")
    (root / "ignored.bin").write_bytes(b"unsupported")

    preview_response = client.post(
        "/api/local-sources/preview",
        json={"path": str(root), "allowed_root": str(root)},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert "root" not in preview
    assert all("path" not in item for item in preview["files"])
    assert all("path" not in item for item in preview["rejected"])
    assert all("SentinelUser" not in _payload_text(item) for item in preview["files"])
    assert all("SentinelUser" not in _payload_text(item) for item in preview["rejected"])
    assert all("/" not in item["relative_path"][:1] for item in preview["files"])

    workspace = client.post(
        "/api/workspaces", json={"title": "path redaction", "description": "test"}
    ).json()
    imported_response = client.post(
        f"/api/workspaces/{workspace['id']}/sources/local-path",
        json={
            "preview_id": preview["preview_id"],
            "selected_file_ids": [preview["files"][0]["id"]],
        },
    )
    assert imported_response.status_code == 200, imported_response.text
    imported = imported_response.json()
    assert "SentinelUser" not in _payload_text(imported)
    source_id = imported["sources"][0]["id"]

    detail = client.get(f"/api/sources/{source_id}")
    assert detail.status_code == 200
    assert "local_path" not in detail.json()
    assert "SentinelUser" not in _payload_text(detail.json())

    listed = client.get(f"/api/workspaces/{workspace['id']}/sources")
    assert listed.status_code == 200
    assert "local_path" not in listed.json()[0]
    assert "SentinelUser" not in _payload_text(listed.json())

    workspace_detail = client.get(f"/api/workspaces/{workspace['id']}")
    assert workspace_detail.status_code == 200
    assert "local_path" not in workspace_detail.json()["sources"][0]
    assert "SentinelUser" not in _payload_text(workspace_detail.json())

    stored = db.row("sources", "id", source_id)
    assert stored and "SentinelUser" in stored["local_path"]
    assert client.delete(f"/api/sources/{source_id}").json() == {"deleted": True}
    # Local-source files are user-owned and remain on disk; the stored path is
    # used for validation/read and is never sent back over this API.
    assert (root / "notes.md").exists()


def test_source_summary_schema_ignores_internal_extra_fields(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]["SourceSummaryOut"]
    assert "local_path" not in schema["properties"]
    from app.schemas import SourceSummaryOut

    assert SourceSummaryOut.model_config["extra"] == "ignore"


def test_legacy_source_filename_and_parse_error_are_safe_on_public_routes(client, tmp_path):
    workspace = client.post(
        "/api/workspaces", json={"title": "legacy source", "description": "test"}
    ).json()
    private_root = tmp_path / "SentinelUser" / "legacy"
    private_root.mkdir(parents=True)
    source = create_source(
        workspace["id"],
        str(private_root / "legacy.md"),
        "upload",
        "legacy source body",
        original_filename=str(private_root / "legacy.md"),
        local_path=str(private_root / "legacy.md"),
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE sources SET parse_error=? WHERE id=?",
            (str(private_root / "parse-error.txt"), source["id"]),
        )

    detail = client.get(f"/api/sources/{source['id']}")
    listed = client.get(f"/api/workspaces/{workspace['id']}/sources")
    workspace_detail = client.get(f"/api/workspaces/{workspace['id']}")
    for response in (detail, listed, workspace_detail):
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "SentinelUser" not in _payload_text(payload)
        item = payload if response is detail else (
            payload[0] if response is listed else payload["sources"][0]
        )
        assert item["original_filename"] == "legacy.md"
        assert item["parse_error"] == "本地文件解析失败"


def test_local_preview_and_consume_oserrors_use_stable_public_errors(
    client, monkeypatch, tmp_path
):
    private_root = tmp_path / "SentinelUser" / "local"
    private_root.mkdir(parents=True)
    sentinel_path = private_root / "notes.md"

    def fail_preview(*_args, **_kwargs):
        raise OSError(f"cannot read {sentinel_path}")

    monkeypatch.setattr(main_module, "create_preview", fail_preview)
    preview_error = client.post(
        "/api/local-sources/preview",
        json={"path": str(private_root), "allowed_root": str(private_root)},
    )
    assert preview_error.status_code == 400
    assert preview_error.json()["error"]["code"] == "LOCAL_PATH_REJECTED"
    assert "SentinelUser" not in _payload_text(preview_error.json())

    workspace = client.post(
        "/api/workspaces", json={"title": "local import error", "description": "test"}
    ).json()

    def fail_consume(*_args, **_kwargs):
        raise OSError(f"cannot read {sentinel_path}")

    monkeypatch.setattr(main_module, "consume_preview", fail_consume)
    import_error = client.post(
        f"/api/workspaces/{workspace['id']}/sources/local-path",
        json={"preview_id": "preview_private", "selected_file_ids": ["file_private"]},
    )
    assert import_error.status_code == 400
    assert import_error.json()["error"]["code"] == "LOCAL_IMPORT_REJECTED"
    assert "SentinelUser" not in _payload_text(import_error.json())


def test_local_parse_oserror_returns_generic_parse_error(client, monkeypatch, tmp_path):
    private_root = tmp_path / "SentinelUser" / "local"
    private_root.mkdir(parents=True)
    (private_root / "notes.md").write_text("# Notes\n\nprivate", encoding="utf-8")
    preview = client.post(
        "/api/local-sources/preview",
        json={"path": str(private_root), "allowed_root": str(private_root)},
    ).json()

    def fail_parse(path):
        raise OSError(f"cannot parse {path}")

    monkeypatch.setattr(main_module, "parse_file", fail_parse)
    workspace = client.post(
        "/api/workspaces", json={"title": "local parse error", "description": "test"}
    ).json()
    response = client.post(
        f"/api/workspaces/{workspace['id']}/sources/local-path",
        json={
            "preview_id": preview["preview_id"],
            "selected_file_ids": [preview["files"][0]["id"]],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["sources"] == [
        {
            "title": "notes.md",
            "status": "error",
            "parse_error": "本地文件解析失败",
        }
    ]
    assert "SentinelUser" not in _payload_text(response.json())
