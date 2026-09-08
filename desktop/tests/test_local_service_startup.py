from __future__ import annotations

import os
import socket
import time
import zipfile
from pathlib import Path

import httpx
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("uvicorn")

from desktop.runtime import LocalService  # noqa: E402
from desktop.main import (  # noqa: E402
    _bind_local_teacher_asset_vault,
    _prepare_desktop_frontend,
)


class _HealthResponse:
    def __init__(self, payload: object) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_constructor_reserves_selected_port_until_stop() -> None:
    service = LocalService()
    challenger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            challenger.bind(("127.0.0.1", service.port))
    finally:
        challenger.close()
        service.stop()

    assert service._listener is None


def test_health_requires_mentorflow_identity_not_any_http_200() -> None:
    assert not LocalService._is_expected_health(_HealthResponse({"status": "ok"}))
    assert not LocalService._is_expected_health(_HealthResponse({"status": "ok", "service": "Other"}))
    assert LocalService._is_expected_health(
        _HealthResponse({"status": "ok", "service": "MentorFlow"})
    )


def test_start_and_stop_are_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MENTORFLOW_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("MENTORFLOW_ORM_DB", str(tmp_path / "mentorflow_v2.db"))
    monkeypatch.setenv("MENTORFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MENTORFLOW_ACCOUNT_VAULT_ROOT", str(tmp_path))
    # Other collected tests may already have imported the canonical backend.
    # Environment changes alone cannot rebind its cached ORM engine/legacy path.
    from app import db, orm
    previous_orm = orm.ORM_DB
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    orm.configure_orm(tmp_path / "mentorflow_v2.db")
    service = LocalService()
    try:
        service.start()
        deadline = time.monotonic() + 20
        response: httpx.Response | None = None
        while time.monotonic() < deadline:
            try:
                candidate = httpx.get(f"{service.origin}/api/health", timeout=1)
            except httpx.HTTPError:
                time.sleep(0.1)
                continue
            if candidate.status_code == 200:
                response = candidate
                break
            time.sleep(0.1)
        assert response is not None
        assert LocalService._is_expected_health(response)
        assert service.thread is not None and service.thread.is_alive()
    finally:
        service.stop()
        orm.configure_orm(previous_orm)

    assert service.thread is not None and not service.thread.is_alive()
    assert service.health_thread is not None and not service.health_thread.is_alive()
    assert service._listener is None


def _write_frontend_bundle(path: Path, *, traversal: bool = False) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "index.html",
            '<html><head><link rel="stylesheet" href="/assets/app.css"></head>'
            '<body><script type="module" src="/assets/app.js"></script></body></html>',
        )
        archive.writestr("assets/app.css", "body{margin:0}")
        archive.writestr("assets/app.js", "document.body.dataset.ready='true'")
        archive.writestr("teacher2d/catalog.json", "{}")
        archive.writestr("vendor/live2d/live2dcubismcore.min.js", "void 0")
        if traversal:
            archive.writestr("../escaped.txt", "forbidden")


def test_source_desktop_materializes_frontend_bundle_before_backend_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "runtime-source"
    bundle = tmp_path / "frontend-dist.zip"
    _write_frontend_bundle(bundle)
    monkeypatch.setenv("MENTORFLOW_DESKTOP_FRONTEND_BUNDLE", str(bundle))

    dist = _prepare_desktop_frontend(project_root)

    assert dist == project_root / "frontend" / "dist"
    assert (dist / "index.html").is_file()
    assert (dist / "assets" / "app.js").is_file()
    assert _prepare_desktop_frontend(project_root) == dist


def test_source_desktop_fails_with_human_diagnostic_when_bundle_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MENTORFLOW_DESKTOP_FRONTEND_BUNDLE", raising=False)

    with pytest.raises(RuntimeError, match="DESKTOP_FRONTEND_ASSETS_MISSING") as error:
        _prepare_desktop_frontend(tmp_path / "runtime-source")

    assert "创建计划、教师舞台和教师资源页无法启动" in str(error.value)
    assert '{"detail":"Not Found"}' not in str(error.value)


def test_source_desktop_rejects_frontend_bundle_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "runtime-source"
    bundle = tmp_path / "frontend-dist.zip"
    _write_frontend_bundle(bundle, traversal=True)
    monkeypatch.setenv("MENTORFLOW_DESKTOP_FRONTEND_BUNDLE", str(bundle))

    with pytest.raises(RuntimeError, match="不安全路径"):
        _prepare_desktop_frontend(project_root)

    assert not (tmp_path / "escaped.txt").exists()
    assert not (project_root / "frontend" / "dist").exists()


def test_local_teacher_assets_are_bound_to_current_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_account = tmp_path / "accounts" / "other"
    current_account = tmp_path / "accounts" / "current"
    monkeypatch.setenv(
        "MENTORFLOW_VOICE_PACK_ROOT",
        str(other_account / "teacher-assets" / "voice-packs"),
    )
    monkeypatch.setenv(
        "MENTORFLOW_PRIVATE_TEACHER_ASSET_ROOT",
        str(other_account / "teacher-assets" / "tsubaki-live2d"),
    )

    bindings = _bind_local_teacher_asset_vault(current_account)

    expected_vault = current_account / "teacher-assets"
    assert bindings == {
        "MENTORFLOW_VOICE_PACK_ROOT": expected_vault / "voice-packs",
        "MENTORFLOW_PRIVATE_TEACHER_ASSET_ROOT": expected_vault / "tsubaki-live2d",
    }
    for name, expected in bindings.items():
        actual = Path(os.environ[name])
        assert actual == expected
        assert other_account not in actual.parents
