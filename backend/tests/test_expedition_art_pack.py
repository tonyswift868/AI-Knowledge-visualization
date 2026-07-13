from __future__ import annotations

import hashlib
import io
import json
import struct
import zlib
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import expedition_art_pack as art
from app.expedition_data import HEROES
from app.orm import Base


def _png(width: int, height: int, *, alpha: bool = True) -> bytes:
    color_type = 6 if alpha else 2
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    row = b"\x00" + (b"\x00" * width * (4 if alpha else 3))
    raw = row * height

    def chunk(name: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _fixture_zip(*, pack_id: str = "mentorflow_expedition_art_v1", version: str = "1.0.0", missing: str | None = None, bad_hash: str | None = None, extra: bool = False, traversal: bool = False) -> bytes:
    root = "expedition_art_pack_v1/"
    files: dict[str, bytes] = {}
    assets: list[dict[str, str]] = []
    checksums: dict[str, str] = {}
    dimensions = {"portrait": (256, 256), "avatar": (128, 128), "skill_cutin": (256, 128), "skill_icon": (64, 64)}
    for hero in HEROES:
        hero_id = hero["hero_id"]
        hero_folder = hero_id
        metadata_path = f"heroes/{hero_folder}/metadata.json"
        metadata = {
            "hero_id": hero_id,
            "display_name": hero["name"],
            "visual_archetype": "DEV ART PACK FIXTURE",
            "age_presentation": "adult",
            "portrait_focal_point": {"x": 0.5, "y": 0.4},
            "avatar_crop": {},
            "cutin_crop": {},
            "dominant_colors": ["#5ec8a1"],
            "battle_scale": 1.0,
            "battle_offset": {"x": 0, "y": 0},
            "facing": "right",
            "skill_fx_tags": ["fire"],
            "source": "DEV ART PACK FIXTURE",
            "author": "test fixture",
            "license": "test-only",
            "sha256": {},
        }
        files[root + metadata_path] = json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode()
        assets.append({"hero_id": hero_id, "asset_kind": "metadata", "path": metadata_path})
        for kind, (width, height) in dimensions.items():
            path = f"heroes/{hero_folder}/{kind}.png"
            if missing == f"{hero_id}:{kind}":
                continue
            files[root + path] = _png(width, height)
            assets.append({"hero_id": hero_id, "asset_kind": kind, "path": path})
    if traversal:
        assets.append({"hero_id": HEROES[0]["hero_id"], "asset_kind": "portrait", "path": "../escape.png"})
    manifest = {
        "pack_id": pack_id,
        "version": version,
        "minimum_runtime_version": "1.0.0",
        "created_at": "2026-07-13T00:00:00Z",
        "hero_count": len(HEROES),
        "required_hero_ids": [hero["hero_id"] for hero in HEROES],
        "assets": assets,
        "style_name": "DEV ART PACK FIXTURE",
        "source": "upstream_approved_art",
        "checksum": "",
        "license_notes": ["DEV ART PACK FIXTURE; not official art"],
    }
    files[root + "manifest.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    files[root + "attribution.json"] = json.dumps({"source": "DEV ART PACK FIXTURE", "author": "test fixture", "license": "test-only"}, sort_keys=True).encode()
    for path, value in files.items():
        if path.endswith("SHA256SUMS.json"):
            continue
        checksums[path[len(root):]] = hashlib.sha256(value).hexdigest()
    if bad_hash:
        checksums[bad_hash] = "0" * 64
    files[root + "SHA256SUMS.json"] = json.dumps({"files": checksums}, ensure_ascii=False, sort_keys=True).encode()
    if extra:
        files[root + "unexpected.bin"] = b"unexpected"
    if traversal:
        files[root + "../escape.png"] = _png(256, 256)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, value in files.items():
            archive.writestr(path, value)
    return output.getvalue()


@pytest.fixture()
def art_runtime(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'art.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(art, "SessionLocal", factory)
    monkeypatch.setattr(art, "init_orm", lambda: Base.metadata.create_all(engine))
    monkeypatch.setattr(art, "PACK_ROOT", tmp_path / "packs")
    monkeypatch.setattr(art, "PREVIEW_ROOT", tmp_path / "previews")
    return tmp_path


def test_preview_requires_complete_coverage_and_provenance():
    report = art.validate_art_pack(_fixture_zip())
    assert report["valid"] is True
    assert report["hero_count"] == 24
    assert report["asset_count"] == 96
    assert report["fallback"]["available"] is True

    with pytest.raises(art.ArtPackError) as missing:
        art.validate_art_pack(_fixture_zip(missing=f"{HEROES[0]['hero_id']}:portrait"))
    assert missing.value.code in {"ART_PACK_ASSET_MISSING", "ART_PACK_CHECKSUM_MISSING"}


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"bad_hash": f"heroes/{HEROES[0]['hero_id']}/portrait.png"}, "ART_PACK_HASH_MISMATCH"),
        ({"extra": True}, "ART_PACK_EXTRA_FILE"),
        ({"traversal": True}, "ART_PACK_UNSAFE_PATH"),
    ],
)
def test_preview_rejects_untrusted_archive(kwargs, code):
    if "bad_hash" in kwargs:
        report = art.validate_art_pack(_fixture_zip(**kwargs))
        assert report["valid"] is False
        assert any(item["code"] == code for item in report["errors"])
    else:
        with pytest.raises(art.ArtPackError) as exc:
            art.validate_art_pack(_fixture_zip(**kwargs))
        assert exc.value.code == code


def test_install_review_activate_resolve_and_rollback(art_runtime):
    first = art.preview_bytes(_fixture_zip(version="1.0.0"), "DEV_ART_PACK_FIXTURE.zip")
    installed = art.install_preview(first["preview_id"], confirm=True)
    assert installed["status"] == "INSTALLED"
    assert installed["reviewed_assets"] == 0
    assert installed["required_reviews"] == 96
    with pytest.raises(art.ArtPackError) as blocked:
        art.activate_pack(installed["pack_id"], installed["version"])
    assert blocked.value.code == "ART_PACK_REVIEW_INCOMPLETE"

    for hero in HEROES:
        for kind in art.ART_KINDS:
            art.review_asset(installed["pack_id"], installed["version"], hero["hero_id"], kind, "APPROVED", "fixture review")
    active = art.activate_pack(installed["pack_id"], installed["version"])
    assert active["status"] == "ACTIVE"
    resolved = art.hero_assets(HEROES[0]["hero_id"])
    assert resolved["fallback_used"] is False
    assert resolved["assets"]["portrait"]["version"] == "1.0.0"
    path = art.asset_file(resolved["assets"]["portrait"]["asset_id"])
    assert path.is_file()
    assert path.suffix == ".png"

    second = art.preview_bytes(_fixture_zip(version="2.0.0"), "DEV_ART_PACK_FIXTURE_V2.zip")
    installed_second = art.install_preview(second["preview_id"], confirm=True)
    for hero in HEROES:
        for kind in art.ART_KINDS:
            art.review_asset(installed_second["pack_id"], installed_second["version"], hero["hero_id"], kind, "APPROVED")
    art.activate_pack(installed_second["pack_id"], installed_second["version"])
    rolled_back = art.rollback_pack(installed_second["pack_id"], installed_second["version"])
    assert rolled_back["active"]["version"] == "1.0.0"
    assert art.hero_assets(HEROES[0]["hero_id"])["assets"]["portrait"]["version"] == "1.0.0"


def test_fallback_is_explicit_when_no_official_pack(art_runtime):
    resolved = art.hero_assets(HEROES[0]["hero_id"])
    assert resolved["fallback_used"] is True
    assert resolved["assets"]["portrait"]["url"].endswith(".svg")
    assert resolved["assets"]["portrait"]["version"] == "fallback"
