from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(r"E:\Codex\Temp")
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from fastapi.testclient import TestClient

from app import active_teaching, db, orm
from app.main import app


def main() -> None:
    """Exercise security boundaries only against disposable local state."""

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    previous_db = db.DB_PATH
    previous_data = db.DATA_DIR
    previous_upload_root = active_teaching.UPLOAD_ROOT
    previous_orm_db = orm.ORM_DB
    with tempfile.TemporaryDirectory(prefix="mentorflow-security-smoke-", dir=TEMP_ROOT) as temporary:
        isolated = Path(temporary)
        db.DB_PATH = isolated / "app.db"
        db.DATA_DIR = isolated
        active_teaching.UPLOAD_ROOT = isolated / "active-teaching-uploads"
        orm.configure_orm(isolated / "mentorflow_v2.db")
        try:
            with TestClient(app) as client:
                assert client.post("/api/providers/test", json={"provider": "fake"}).status_code == 200
                assert client.post(
                    "/api/providers/test",
                    json={"provider": "fake", "api_key": "secret"},
                ).status_code == 422
                assert client.post(
                    "/api/providers/test",
                    json={
                        "provider": "openai-compatible",
                        "base_url": "https://example.invalid/v1",
                        "model": "m",
                        "api_key": "secret",
                    },
                ).status_code == 422
                source_root = isolated / "local-source"
                source_root.mkdir()
                (source_root / "x.md").write_text("x", encoding="utf-8")
                assert client.post(
                    "/api/local-sources/preview",
                    json={"path": "C:\\", "allowed_root": "C:\\"},
                ).status_code == 400
                preview = client.post(
                    "/api/local-sources/preview",
                    json={"path": str(source_root), "allowed_root": str(source_root)},
                )
                assert preview.status_code == 200
                workspace_id = client.post(
                    "/api/workspaces",
                    json={"title": "security-smoke-isolated"},
                ).json()["id"]
                assert client.post(
                    f"/api/workspaces/{workspace_id}/sources/local-path",
                    json={
                        "preview_id": preview.json()["preview_id"],
                        "selected_file_ids": [],
                    },
                ).status_code == 400
                assert "secret" not in preview.text
        finally:
            orm.engine.dispose()
            orm.configure_orm(previous_orm_db)
            db.DB_PATH = previous_db
            db.DATA_DIR = previous_data
            active_teaching.UPLOAD_ROOT = previous_upload_root
    print("SECURITY SMOKE PASSED: isolated DBs, raw-key rejection, root rejection, preview confirmation")


if __name__ == "__main__":
    main()
