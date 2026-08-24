from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import active_teaching, db
import app.main as main_module
from app.services import create_source
from app.upload_limits import UploadLimitExceeded, active_upload_root, read_upload_limited


class _Stream:
    def __init__(self, payload: bytes, *, chunk_size: int = 3, failure: BaseException | None = None):
        self.payload = payload
        self.chunk_size = chunk_size
        self.failure = failure
        self.offset = 0
        self.calls: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.calls.append(size)
        if self.failure is not None:
            raise self.failure
        if self.offset >= len(self.payload):
            return b""
        end = min(len(self.payload), self.offset + min(size, self.chunk_size))
        chunk = self.payload[self.offset:end]
        self.offset = end
        return chunk


def test_small_and_exact_limit_streams_are_read_in_chunks():
    small = _Stream(b"abc", chunk_size=2)
    assert asyncio.run(read_upload_limited(small, 8, chunk_size=4)) == b"abc"
    assert max(small.calls) <= 4

    exact = _Stream(b"12345", chunk_size=2)
    assert asyncio.run(read_upload_limited(exact, 5, chunk_size=4)) == b"12345"
    assert max(exact.calls) <= 4


def test_limit_plus_one_fails_before_reading_the_rest_of_the_stream():
    stream = _Stream(b"x" * 6, chunk_size=6)
    with pytest.raises(UploadLimitExceeded) as caught:
        asyncio.run(read_upload_limited(stream, 5, chunk_size=64))
    assert caught.value.limit_bytes == 5
    assert caught.value.received_bytes == 6
    assert sum(stream.calls) <= 6


def test_stream_exception_is_not_hidden_by_the_reader():
    failure = RuntimeError("upstream stream failed")
    with pytest.raises(RuntimeError, match="upstream stream failed"):
        asyncio.run(read_upload_limited(_Stream(b"", failure=failure), 10))


def test_aggregate_budget_is_checked_while_reading_each_file():
    first = _Stream(b"1234", chunk_size=2)
    second = _Stream(b"567", chunk_size=2)
    first_payload = asyncio.run(read_upload_limited(first, 10, aggregate_limit=6))
    assert first_payload == b"1234"
    with pytest.raises(UploadLimitExceeded) as caught:
        asyncio.run(read_upload_limited(second, 10, aggregate_limit=6, aggregate_used=len(first_payload)))
    assert caught.value.aggregate is True
    assert caught.value.code == "UPLOAD_AGGREGATE_TOO_LARGE"
    assert caught.value.received_bytes == 7


def test_active_upload_root_honors_configured_user_data_root(monkeypatch, tmp_path):
    configured = tmp_path / "user-data" / "uploads"
    monkeypatch.setenv("MENTORFLOW_ACTIVE_UPLOAD_ROOT", str(configured))
    assert active_upload_root() == configured.resolve()


def test_legacy_source_upload_uses_active_user_data_root(client, monkeypatch, tmp_path):
    monkeypatch.delenv("MENTORFLOW_ACTIVE_UPLOAD_ROOT", raising=False)
    root = tmp_path / "active-uploads"
    monkeypatch.setattr(active_teaching, "UPLOAD_ROOT", root)
    workspace = client.post("/api/workspaces", json={"title": "upload root", "description": "test"}).json()
    response = client.post(
        f"/api/workspaces/{workspace['id']}/sources/upload",
        files={"file": ("root-check.txt", b"root-check", "text/plain")},
    )
    assert response.status_code == 200, response.text
    source_id = response.json()["id"]
    # Storage paths remain an internal deletion/read concern; the public
    # source detail intentionally omits the local_path field.
    assert "local_path" not in client.get(f"/api/sources/{source_id}").json()
    local_path = Path(db.row("sources", "id", source_id)["local_path"])
    assert local_path.is_file()
    assert local_path.is_relative_to(root.resolve())
    assert not local_path.is_relative_to(Path(__file__).resolve().parents[2] / "data" / "uploads")


def _source_for_delete(client, local_path: Path) -> str:
    workspace = client.post(
        "/api/workspaces", json={"title": "delete source", "description": "test"}
    ).json()
    source = create_source(
        workspace["id"],
        local_path.name,
        "upload",
        "source content",
        original_filename=local_path.name,
        local_path=str(local_path),
    )
    return source["id"]


def test_delete_source_removes_file_from_active_upload_root(client, monkeypatch, tmp_path):
    active_root = tmp_path / "active-uploads"
    monkeypatch.delenv("MENTORFLOW_ACTIVE_UPLOAD_ROOT", raising=False)
    monkeypatch.setattr(active_teaching, "UPLOAD_ROOT", active_root)
    path = active_root / "legacy-sources" / "new.txt"
    path.parent.mkdir(parents=True)
    path.write_text("new", encoding="utf-8")

    source_id = _source_for_delete(client, path)
    response = client.delete(f"/api/sources/{source_id}")

    assert response.status_code == 200, response.text
    assert not path.exists()


def test_delete_source_removes_file_from_historical_upload_root(
    client, monkeypatch, tmp_path
):
    active_root = tmp_path / "active-uploads"
    historical_root = tmp_path / "historical-uploads"
    monkeypatch.setenv("MENTORFLOW_ACTIVE_UPLOAD_ROOT", str(active_root))
    monkeypatch.setattr(main_module, "_historical_upload_root", lambda: historical_root)
    path = historical_root / "legacy-sources" / "old.txt"
    path.parent.mkdir(parents=True)
    path.write_text("old", encoding="utf-8")

    source_id = _source_for_delete(client, path)
    response = client.delete(f"/api/sources/{source_id}")

    assert response.status_code == 200, response.text
    assert not path.exists()


def test_delete_source_does_not_remove_file_outside_upload_roots(
    client, monkeypatch, tmp_path
):
    active_root = tmp_path / "active-uploads"
    historical_root = tmp_path / "historical-uploads"
    external_root = tmp_path / "external"
    monkeypatch.setenv("MENTORFLOW_ACTIVE_UPLOAD_ROOT", str(active_root))
    monkeypatch.setattr(main_module, "_historical_upload_root", lambda: historical_root)
    path = external_root / "outside.txt"
    path.parent.mkdir(parents=True)
    path.write_text("outside", encoding="utf-8")

    source_id = _source_for_delete(client, path)
    response = client.delete(f"/api/sources/{source_id}")

    assert response.status_code == 200, response.text
    assert path.read_text(encoding="utf-8") == "outside"


def test_teacher_avatar_batch_zip_limit_reports_runtime_pack_contract(client, monkeypatch):
    async def oversized_reader(*_args, **_kwargs):
        raise UploadLimitExceeded(
            limit_bytes=main_module.MAX_KOIKATSU_EXPORT_BYTES,
            received_bytes=main_module.MAX_KOIKATSU_EXPORT_BYTES + 1,
        )

    monkeypatch.setattr(main_module, "read_upload_limited", oversized_reader)
    response = client.post(
        "/api/teacher-avatars/import-batch",
        files=[("file", ("pack.zip", b"mock", "application/zip"))],
    )

    assert response.status_code == 413, response.text
    error = response.json()["error"]
    assert error["code"] == "RUNTIME_PACK_SIZE_INVALID"
    assert "500MB" in error["message"]
    assert error["details"]["max_size_bytes"] == main_module.MAX_KOIKATSU_EXPORT_BYTES
    assert error["details"]["aggregate"] is False


def test_teacher_avatar_batch_non_zip_and_aggregate_limits_keep_their_contract(
    client, monkeypatch
):
    async def oversized_reader(*_args, **_kwargs):
        raise UploadLimitExceeded(
            limit_bytes=main_module.MAX_MODEL_BYTES,
            received_bytes=main_module.MAX_MODEL_BYTES + 1,
        )

    monkeypatch.setattr(main_module, "read_upload_limited", oversized_reader)
    response = client.post(
        "/api/teacher-avatars/import-batch",
        files=[("file", ("avatar.vrm", b"mock", "application/octet-stream"))],
    )
    assert response.status_code == 413, response.text
    error = response.json()["error"]
    assert error["code"] == "MODEL_SIZE_INVALID"
    assert "100MB" in error["message"]
    assert error["details"]["max_size_bytes"] == main_module.MAX_MODEL_BYTES

    async def aggregate_reader(*_args, **_kwargs):
        raise UploadLimitExceeded(
            limit_bytes=main_module.MAX_BATCH_BYTES,
            received_bytes=main_module.MAX_BATCH_BYTES + 1,
            aggregate=True,
        )

    monkeypatch.setattr(main_module, "read_upload_limited", aggregate_reader)
    aggregate = client.post(
        "/api/teacher-avatars/import-batch",
        files=[("file", ("avatar.vrm", b"mock", "application/octet-stream"))],
    )
    assert aggregate.status_code == 413, aggregate.text
    aggregate_error = aggregate.json()["error"]
    assert aggregate_error["code"] == "BATCH_SIZE_LIMIT"
    assert "500MB" in aggregate_error["message"]
    assert aggregate_error["details"]["max_size_bytes"] == main_module.MAX_BATCH_BYTES
    assert aggregate_error["details"]["aggregate"] is True
