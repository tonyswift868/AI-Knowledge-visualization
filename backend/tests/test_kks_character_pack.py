from __future__ import annotations

import io
import hashlib
import json
import struct
import zipfile

from app.koikatsu_card import native_card_fingerprint
from app import teacher_avatar_registry as registry


def _kks_glb() -> bytes:
    names = [
        "cf_s_head",
        "cf_s_arm01_L",
        "cf_s_forearm01_L",
        "cf_s_arm01_R",
        "cf_s_forearm01_R",
    ] + [f"cf_test_{index}" for index in range(800)]
    document = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["EXT_meshopt_compression", "EXT_texture_webp"],
        "nodes": [{"name": name} for name in names],
        "skins": [{"joints": list(range(len(names)))}],
        "meshes": [{"primitives": [{"targets": [{}]}]}],
        "materials": [{
            "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
            "emissiveTexture": {"index": 0},
        }],
    }
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 20 + len(payload)
    return b"glTF" + struct.pack("<II", 2, total) + struct.pack("<II", len(payload), 0x4E4F534A) + payload


def _pack(
    fingerprint: str,
    *,
    include_receipt: bool = True,
    receipt_status: str = "ready",
) -> bytes:
    model = _kks_glb()
    model_sha = hashlib.sha256(model).hexdigest()
    source_card_sha = "c" * 64
    provenance = {
        "schema": registry.KKS_MODEL_PROVENANCE_SCHEMA,
        "card_id": "0123456789abcdef",
        "identity_verified": True,
        "source_card_sha256": source_card_sha,
        "model_sha256": model_sha,
    }
    provenance_content = json.dumps(provenance, separators=(",", ":")).encode()
    provenance_sha = hashlib.sha256(provenance_content).hexdigest()
    receipt = {
        "schema": registry.KKS_APPEARANCE_RECEIPT_SCHEMA,
        "status": receipt_status,
        "decision": "accepted",
        "card_fingerprint": fingerprint,
        "model_kind": "kkbp-web",
        "source_card_sha256": source_card_sha,
        "model_sha256": model_sha,
        "provenance_sha256": provenance_sha,
        "reviewed_at": "2026-07-25T00:00:00Z",
        "reviewer": {"kind": "test", "id": "pytest"},
        "checks": {
            "identity_verified": True,
            "face_features_visible": True,
            "materials_separated": True,
            "full_body_visible": True,
            "browser_visual_review": True,
        },
        "evidence": {"browser_screenshot_sha256": "d" * 64},
    }
    receipt_content = json.dumps(receipt, separators=(",", ":")).encode()

    manifest = {
        "schema": registry.KKS_CHARACTER_PACK_SCHEMA,
        "display_name": "Test KKS",
        "card_fingerprint": fingerprint,
        "model": {
            "path": "model.glb",
            "sha256": model_sha,
        },
        "provenance": {
            "path": registry.KKS_MODEL_PROVENANCE_PATH,
            "sha256": provenance_sha,
        },
        "appearance_contract": {
            "status": "ready",
            "model_kind": "kkbp-web",
            "card_fingerprint": fingerprint,
            "source_card_sha256": source_card_sha,
            "model_sha256": model_sha,
            "receipt_path": registry.KKS_APPEARANCE_RECEIPT_PATH,
            "receipt_hash": hashlib.sha256(receipt_content).hexdigest(),
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(registry.KKS_CHARACTER_PACK_MANIFEST, json.dumps(manifest))
        archive.writestr("model.glb", model)
        archive.writestr(registry.KKS_MODEL_PROVENANCE_PATH, provenance_content)
        if include_receipt:
            archive.writestr(registry.KKS_APPEARANCE_RECEIPT_PATH, receipt_content)
    return buffer.getvalue()


def _runtime_pack(fingerprint: str) -> bytes:
    card = b"fake-card-png"
    skin = b"{}"
    manifest = {
        "schema": registry.KKS_RUNTIME_PACK_SCHEMA,
        "pack_id": "runtime-test-pack",
        "display_name": "Runtime Test",
        "card_fingerprint": fingerprint,
        "assets": [
            {"path": "card.png", "sha256": hashlib.sha256(card).hexdigest(), "size": len(card)},
            {"path": "assets/runtime-shared/test.json", "sha256": hashlib.sha256(skin).hexdigest(), "size": len(skin)},
        ],
    }
    runtime_manifest = {"card_id": "test", "status": "ready", "items": []}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(registry.KKS_RUNTIME_PACK_MANIFEST, json.dumps(manifest))
        archive.writestr("runtime/manifest.json", json.dumps(runtime_manifest))
        archive.writestr("card.png", card)
        archive.writestr("assets/runtime-shared/test.json", skin)
    return buffer.getvalue()


def test_native_card_fingerprint_is_path_and_name_independent() -> None:
    base = {
        "character": {"display_name": "A"},
        "body": [{"key": "height", "value": 0.5}],
        "face": [{"index": 0, "value": 0.5}],
        "native_summary": {"head_id": 0},
        "abmx_modifiers": [],
        "appearance": {"skin_main_color": [1, 1, 1, 1]},
    }
    renamed = {**base, "character": {"display_name": "B"}}
    assert native_card_fingerprint(base) == native_card_fingerprint(renamed)


def test_imported_kks_pack_is_card_matchable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path)
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")
    pack_root = tmp_path / "packs"
    monkeypatch.setattr(registry, "KKS_CHARACTER_PACK_ROOT", pack_root)
    fingerprint = "a" * 64
    content = _pack(fingerprint)
    item = registry.import_model(
        "teacher.zip",
        content,
        display_name="",
        creator="tester",
        source_url="local://test",
        license_name="test-only",
        license_url="",
        rights_confirmed=True,
        adult_character_confirmed=True,
    )
    assert item["asset_profile"] == "kks-character-pack"
    assert item["card_fingerprint"] == fingerprint
    assert item["appearance_contract"]["status"] == "ready"
    assert item["appearance_contract"]["model_kind"] == "kkbp-web"
    assert len(item["appearance_contract"]["receipt_hash"]) == 64
    assert item["kks_profile"]["joint_count"] == 805
    assert (tmp_path / item["model_id"] / "model.glb").is_file()
    pack_root.mkdir()
    published = pack_root / "test-pack.zip"
    published.write_bytes(content)
    catalog = registry.list_kks_character_packs()
    assert catalog["count"] == 1
    assert catalog["items"][0]["installed"] is True
    assert registry.resolve_kks_character_pack("test-pack") == published


def test_imported_kks_pack_rejects_missing_appearance_receipt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path)
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")
    fingerprint = "a" * 64

    try:
        registry.import_model(
            "teacher.zip",
            _pack(fingerprint, include_receipt=False),
            display_name="",
            creator="tester",
            source_url="local://test",
            license_name="test-only",
            license_url="",
            rights_confirmed=True,
            adult_character_confirmed=True,
        )
    except registry.TeacherAvatarError as exc:
        assert exc.code == "KKS_APPEARANCE_UNVERIFIED"
    else:
        raise AssertionError("pack without appearance receipt was accepted")
    assert not (tmp_path / "registry.json").exists()


def test_imported_kks_pack_rejects_unapproved_appearance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path)
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")

    try:
        registry.import_model(
            "teacher.zip",
            _pack("a" * 64, receipt_status="candidate"),
            display_name="",
            creator="tester",
            source_url="local://test",
            license_name="test-only",
            license_url="",
            rights_confirmed=True,
            adult_character_confirmed=True,
        )
    except registry.TeacherAvatarError as exc:
        assert exc.code == "KKS_APPEARANCE_UNVERIFIED"
    else:
        raise AssertionError("candidate appearance receipt was accepted")


def test_imported_runtime_pack_is_card_matchable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path / "registry")
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(registry, "KKS_RUNTIME_PACK_ROOT", tmp_path / "runtime-packs")
    fingerprint = "b" * 64
    item = registry.import_model(
        "runtime.zip",
        _runtime_pack(fingerprint),
        display_name="",
        creator="tester",
        source_url="local://test",
        license_name="test-only",
        license_url="",
        rights_confirmed=True,
        adult_character_confirmed=True,
    )
    assert item["asset_profile"] == "kks-runtime-pack"
    assert item["card_fingerprint"] == fingerprint
    assert registry.resolve_runtime_pack_manifest("runtime-test-pack").is_file()


def test_categorized_runtime_pack_is_downloadable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry, "KKS_CHARACTER_PACK_ROOT", tmp_path / "packs")
    pack_root = tmp_path / "packs" / "赌牌游戏" / "FATE"
    pack_root.mkdir(parents=True)
    pack_path = pack_root / "runtime-test-pack.zip"
    pack_path.write_bytes(_runtime_pack("c" * 64))

    assert registry.resolve_kks_character_pack("runtime-test-pack") == pack_path
