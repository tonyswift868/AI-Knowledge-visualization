from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app import spine_teacher_registry as registry
from app.main import app


def _package(*, licensed: bool = True, unsafe: bool = False) -> bytes:
    manifest = {
        "format": "mentorflow.spine-teacher",
        "runtime_version": "4.1",
        "license": {
            "spine_editor_license_confirmed": licensed,
            "character_rights_confirmed": licensed,
        },
        "skeleton": {
            "json": "teacher.json",
            "atlas": "teacher.atlas",
        },
        "animations": {
            "idle": "idle",
            "listening": "listen",
            "thinking": "think",
            "explaining": "talk",
            "encouraging": "happy",
        },
        "skin": "default",
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("teacher.manifest.json", json.dumps(manifest))
        archive.writestr("teacher.json", json.dumps({"skeleton": {"spine": "4.1.00"}}))
        archive.writestr("teacher.atlas", "teacher.png\nsize: 1,1\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\n")
        archive.writestr("teacher.png", b"\x89PNG\r\n\x1a\n")
        if unsafe:
            archive.writestr("../escape.png", b"\x89PNG\r\n\x1a\n")
    return output.getvalue()


def _import(content: bytes):
    return registry.import_package(
        "teacher.zip",
        content,
        display_name="艾莉丝",
        creator="测试作者",
        spine_editor_license_confirmed=True,
        character_rights_confirmed=True,
        adult_character_confirmed=True,
    )


def test_imports_a_licensed_spine_teacher_and_rewrites_public_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path)
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")

    item = _import(_package())
    manifest = registry.public_manifest(item["teacher_id"])

    assert item["format"] == "spine-4.1"
    assert item["manifest_url"] == f"/api/spine-teachers/{item['teacher_id']}/manifest"
    assert manifest["skeleton"]["json_url"].endswith("/assets/teacher.json")
    assert manifest["skeleton"]["atlas_url"].endswith("/assets/teacher.atlas")
    assert manifest["animations"]["explaining"] == "talk"
    assert registry.active_teacher()["teacher_id"] == item["teacher_id"]
    assert (tmp_path / item["teacher_id"] / "teacher.png").is_file()


def test_rejects_zip_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path)
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")

    with pytest.raises(registry.SpineTeacherError, match="不安全路径"):
        _import(_package(unsafe=True))
    assert not (tmp_path.parent / "escape.png").exists()


def test_rejects_missing_import_confirmations(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path)
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")

    with pytest.raises(registry.SpineTeacherError, match="必须确认"):
        registry.import_package(
            "teacher.zip",
            _package(),
            display_name="艾莉丝",
            creator="测试作者",
            spine_editor_license_confirmed=False,
            character_rights_confirmed=True,
            adult_character_confirmed=True,
        )


def test_rejects_manifest_without_complete_animation_mapping():
    manifest = json.loads(zipfile.ZipFile(BytesIO(_package())).read("teacher.manifest.json"))
    del manifest["animations"]["thinking"]
    with pytest.raises(registry.SpineTeacherError, match="五种教学动画"):
        registry.validate_manifest(manifest)


def test_spine_teacher_http_import_manifest_and_asset_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path)
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")

    with TestClient(app) as client:
        response = client.post(
            "/api/spine-teachers/import",
            files={"file": ("teacher.zip", _package(), "application/zip")},
            data={
                "display_name": "艾莉丝",
                "creator": "测试作者",
                "spine_editor_license_confirmed": "true",
                "character_rights_confirmed": "true",
                "adult_character_confirmed": "true",
            },
        )
        assert response.status_code == 200, response.text
        item = response.json()["item"]

        active = client.get("/api/spine-teachers/active")
        assert active.status_code == 200
        assert active.json()["item"]["teacher_id"] == item["teacher_id"]

        manifest = client.get(item["manifest_url"])
        assert manifest.status_code == 200
        skeleton_url = manifest.json()["skeleton"]["json_url"]
        asset = client.get(skeleton_url)
        assert asset.status_code == 200
        assert asset.json()["skeleton"]["spine"] == "4.1.00"
