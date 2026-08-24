from __future__ import annotations

import json
from pathlib import Path

from app import teacher_avatar_card_presets as card_presets


def test_source_card_preset_route_is_truthful_and_non_500(client):
    response = client.get("/api/teacher-avatar-card-presets")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ready", "partial", "unavailable"}
    assert body["count"] == len(body["items"]) == body["total"]
    if body["status"] == "unavailable":
        assert body["available"] is False
        assert body["reason"]


def test_missing_card_manifest_returns_actionable_unavailable_status(client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(card_presets, "MANIFEST", tmp_path / "missing" / "manifest.json")

    response = client.get("/api/teacher-avatar-card-presets")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["available"] is False
    assert body["items"] == []
    assert body["count"] == body["total"] == 0
    assert body["reason_code"] == "CARD_PRESET_MANIFEST_INVALID"
    assert body["reason"]


def test_packaged_card_manifest_and_files_can_be_listed_without_game_assets(
    client, tmp_path: Path, monkeypatch
):
    package_root = tmp_path / "legacy" / "koikatsu-3d" / "resources" / "card-presets" / "1.0.0"
    package_root.mkdir(parents=True)
    (package_root / "packaged-card.json").write_text("{}", encoding="utf-8")
    (package_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cards": [
                    {
                        "card_id": "packaged-card",
                        "file": "packaged-card.json",
                        "display_name": "Packaged card",
                        "creator": "MentorFlow",
                        "description": "tiny packaged parameter card",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(card_presets, "CARD_ROOT", package_root)
    monkeypatch.setattr(card_presets, "MANIFEST", package_root / "manifest.json")
    monkeypatch.setattr(
        card_presets,
        "validate_card",
        lambda _name, _payload: {
            "source_format": "json",
            "body_template_id": "template-id",
            "morph": {},
            "assets": {},
            "dependencies": [],
            "missing_dependencies": [],
            "native_summary": None,
            "character": {"display_name": "Packaged card", "creator": "MentorFlow"},
        },
    )

    response = client.get("/api/teacher-avatar-card-presets")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["available"] is True
    assert body["count"] == body["total"] == body["declared_count"] == 1
    assert [item["card_id"] for item in body["items"]] == ["packaged-card"]
