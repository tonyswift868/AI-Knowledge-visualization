from __future__ import annotations

import hashlib
from pathlib import Path
import json
import shutil
import zipfile

import pytest

from app import koikatsu_card as card_parser
from app import koikatsu_teacher_registry as registry


def test_runtime_pack_head_contract_rejects_incompatible_face_uv(tmp_path: Path) -> None:
    pack_root = tmp_path / "pack"
    face = pack_root / "assets" / "runtime-shared" / "face" / "093-cf_O_face.json"
    face.parent.mkdir(parents=True)
    source = registry.KKS_RUNTIME_ROOT / "shared-base" / "neutral-head" / "093-cf_O_face.json"
    shutil.copyfile(source, face)
    manifest = {
        "status": "ready",
        "items": [{
            "id": "ChaFileFace.headId:cf_O_face",
            "source_kind": "head",
            "runtimeSkin": "/api/koikatsu-runtime-packs/test/asset/runtime-shared/face/093-cf_O_face.json",
        }],
        "headRuntime": {"status": "ready", "geometryResolved": True},
    }
    checked = registry.validate_runtime_pack_head_contract(manifest, pack_root)
    assert checked["headRuntime"]["faceUvCompatible"] is True
    assert checked["headRuntime"]["faceCompositeCompatible"] is True

    # The browser replaces the source shader with a deterministic unlit
    # material when applying the baked face atlas. Exact face UVs, not the
    # Unity shader label, are therefore the compatibility contract.
    payload = json.loads(face.read_text(encoding="utf-8"))
    payload["materials"][0]["shaderName"] = "Shader Forge/main_skin"
    face.write_text(json.dumps(payload), encoding="utf-8")
    checked = registry.validate_runtime_pack_head_contract(manifest, pack_root)
    assert checked["headRuntime"]["faceUvCompatible"] is True
    assert checked["headRuntime"]["faceMaterialShader"] == "Shader Forge/main_skin"
    assert checked["headRuntime"]["faceCompositeCompatible"] is True

    payload = json.loads(face.read_text(encoding="utf-8"))
    payload["uvs"][0] = float(payload["uvs"][0]) + 0.125
    face.write_text(json.dumps(payload), encoding="utf-8")
    checked = registry.validate_runtime_pack_head_contract(manifest, pack_root)
    assert checked["status"] == "partial"
    assert checked["headRuntime"]["status"] == "invalid"
    assert checked["headRuntime"]["reason"] == "head-face-uv-contract-mismatch"


def test_export_match_requires_native_card_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    card = tmp_path / "KoikatsuSun_F_20260331054307896_早坂 愛.png"
    card.write_bytes(b"card")
    matching = tmp_path / "20260717072749_早坂愛"
    different_revision = tmp_path / "20260718153724_早坂愛"
    for folder in (matching, different_revision):
        folder.mkdir()
        (folder / "model.pmx").write_bytes(b"pmx")
        (folder / "Outfit 00").mkdir()
        (folder / "Outfit 00" / "model.pmx").write_bytes(b"outfit-pmx")
        (folder / "KK_MaterialDataComplete.json").write_text("[]", encoding="utf-8")
        (folder / "card_data.png").write_bytes(b"export-card")

    monkeypatch.setattr(
        registry,
        "_export_matches_card",
        lambda folder, _card: folder == matching,
    )

    resolved = registry._export_folder_for_card(card, {
        matching.name.casefold(): matching,
        different_revision.name.casefold(): different_revision,
    })

    assert resolved == matching


def test_catalog_advertises_only_verified_complete_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    card_root = tmp_path / "chara"
    card = card_root / "female" / "KKS_Test.png"
    card.parent.mkdir(parents=True)
    card.write_bytes(b"card")
    export = tmp_path / "Export_PMX" / "20260724_Test"
    export.mkdir(parents=True)
    (export / "model.pmx").write_bytes(b"pmx")
    (export / "Outfit 00").mkdir()
    (export / "Outfit 00" / "model.pmx").write_bytes(b"outfit-pmx")
    (export / "KK_MaterialDataComplete.json").write_text("[]", encoding="utf-8")
    (export / "card_data.png").write_bytes(b"export-card")
    monkeypatch.setattr(registry, "LOCAL_CARD_ROOT", card_root)
    monkeypatch.setattr(registry, "resolve_local_card", lambda _card_id: card)
    monkeypatch.setattr(registry, "_export_folder_for_card", lambda *_args, **_kwargs: export)

    item = registry._catalog_item_with_native_export({
        "card_id": "0123456789abcdef",
        "relative_path": "female/KKS_Test.png",
        "asset_profile": None,
        "asset_status": "card-defined",
        "model_url": None,
    }, {export.name.casefold(): export})

    assert item["asset_profile"] == "kkbp-export"
    assert item["asset_status"] == "ready"
    assert item["model_format"] == "pmx"
    assert item["model_url"].endswith("/0123456789abcdef/model/model.pmx")


def test_catalog_prefers_verified_web_glb_over_native_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    card_root = tmp_path / "chara"
    card = card_root / "female" / "KKS_Test.png"
    card.parent.mkdir(parents=True)
    card.write_bytes(b"card")
    web_root = tmp_path / "web-models"
    model = web_root / "0123456789abcdef" / "model.glb"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"glTF")
    provenance = model.with_name("model.provenance.json")
    provenance.write_text(json.dumps({
        "schema": registry.WEB_MODEL_PROVENANCE_SCHEMA,
        "card_id": "0123456789abcdef",
        "identity_verified": True,
        "source_card_sha256": hashlib.sha256(card.read_bytes()).hexdigest(),
        "source_card_size": card.stat().st_size,
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "model_size": model.stat().st_size,
    }), encoding="utf-8")
    export = tmp_path / "Export_PMX" / "20260724_Test"
    export.mkdir(parents=True)
    monkeypatch.setattr(registry, "LOCAL_CARD_ROOT", card_root)
    monkeypatch.setattr(registry, "WEB_MODEL_ROOT", web_root)
    monkeypatch.setattr(registry, "resolve_local_card", lambda _card_id: card)
    monkeypatch.setattr(registry, "_export_folder_for_card", lambda *_args, **_kwargs: export)

    item = registry._catalog_item_with_native_export({
        "card_id": "0123456789abcdef",
        "relative_path": "female/KKS_Test.png",
        "asset_profile": None,
        "asset_status": "card-defined",
        "model_url": None,
    }, {export.name.casefold(): export})

    assert item["asset_profile"] == "kkbp-web"
    assert item["asset_status"] == "ready"
    assert item["model_format"] == "glb"
    assert item["model_url"].endswith("/0123456789abcdef/web/model.glb")


def test_catalog_rejects_web_glb_without_identity_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    card_root = tmp_path / "chara"
    card = card_root / "female" / "KKS_Test.png"
    card.parent.mkdir(parents=True)
    card.write_bytes(b"card")
    web_root = tmp_path / "web-models"
    model = web_root / "0123456789abcdef" / "model.glb"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"glTF")
    monkeypatch.setattr(registry, "LOCAL_CARD_ROOT", card_root)
    monkeypatch.setattr(registry, "WEB_MODEL_ROOT", web_root)
    monkeypatch.setattr(registry, "resolve_local_card", lambda _card_id: card)
    monkeypatch.setattr(registry, "_export_folder_for_card", lambda *_args, **_kwargs: None)

    item = registry._catalog_item_with_native_export({
        "card_id": "0123456789abcdef",
        "relative_path": "female/KKS_Test.png",
        "asset_profile": None,
        "asset_status": "card-defined",
        "model_url": None,
    }, {})

    assert item["asset_profile"] is None
    assert item["asset_status"] == "card-defined"
    assert item["model_url"] is None


def test_face_composite_cache_miss_does_not_masquerade_raw_face_as_baked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A pale face_base atlas must never be advertised as the final composite."""

    source = tmp_path / "face.png"
    source.write_bytes(b"png")
    monkeypatch.setattr(registry, "_shared_runtime_file", lambda _relative: source)
    monkeypatch.setattr(registry, "_looks_like_png", lambda path, *_args: Path(path).name == "face.png")

    face_url = "/api/koikatsu-cards/runtime-shared/face-textures/bundle/face.png"
    public, failure = registry._face_composite_manifest(
        {"face_base": face_url, "face_color_mask": face_url},
        {"skin_main_color": [1, 1, 1, 1], "skin_sub_color": [1, 1, 1, 1]},
    )

    assert public is None
    assert failure == {"slot": "face_composite", "reason": "composite-cache-miss"}
    assert registry._blocking_face_texture_failures([failure]) == []


def test_eye_composite_cache_miss_keeps_raw_pupil_atlas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "eye.png"
    source.write_bytes(b"png")
    monkeypatch.setattr(registry, "_shared_runtime_file", lambda _relative: source)
    monkeypatch.setattr(registry, "_looks_like_png", lambda path, *_args: Path(path).name == "eye.png")

    eye_url = "/api/koikatsu-cards/runtime-shared/face-textures/bundle/eye.png"
    result, failures = registry._eye_composite_manifest(
        {
            "pupil_left": eye_url,
            "pupil_right": eye_url,
            "pupil_gradient": eye_url,
        },
        {"pupils": [{}, {}]},
    )

    assert result == {"pupil_left_composite": eye_url, "pupil_right_composite": eye_url}
    assert {item["slot"] for item in failures} == {"pupil_left_composite", "pupil_right_composite"}
    assert registry._blocking_face_texture_failures(failures) == []


def test_web_model_asset_requires_existing_card(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(registry, "WEB_MODEL_ROOT", tmp_path)
    checked: list[str] = []

    def reject(card_id: str) -> Path:
        checked.append(card_id)
        raise registry.KoikatsuTeacherError("LOCAL_CARD_NOT_FOUND", "missing", 404)

    monkeypatch.setattr(registry, "resolve_local_card", reject)

    with pytest.raises(registry.KoikatsuTeacherError) as caught:
        registry.resolve_web_model_asset("not-a-card", "model.glb")

    assert checked == ["not-a-card"]
    assert caught.value.code == "LOCAL_CARD_NOT_FOUND"


@pytest.mark.parametrize(
    "relative",
    (
        "../secret.glb",
        "textures/../../secret.png",
        r"textures\secret.png",
        "C:/secret.glb",
    ),
)
def test_web_model_asset_rejects_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative: str,
) -> None:
    monkeypatch.setattr(registry, "WEB_MODEL_ROOT", tmp_path)
    monkeypatch.setattr(registry, "resolve_local_card", lambda _card_id: tmp_path / "card.png")

    with pytest.raises(registry.KoikatsuTeacherError) as caught:
        registry.resolve_web_model_asset("0123456789abcdef", relative)

    assert caught.value.code == "WEB_MODEL_ASSET_PATH_INVALID"


def test_incomplete_export_without_outfit_model_is_rejected(tmp_path: Path) -> None:
    export = tmp_path / "20260717_Test"
    export.mkdir()
    (export / "model.pmx").write_bytes(b"body-only")
    (export / "Outfit 00").mkdir()
    (export / "Outfit 00" / "hair_DM.png").write_bytes(b"texture-only")
    (export / "KK_MaterialDataComplete.json").write_text("[]", encoding="utf-8")
    (export / "card_data.png").write_bytes(b"export-card")

    assert registry._complete_export(export) is False


def _resources(*properties: str) -> dict:
    references = [
        {"property": property_name, "kind": "accessory", "parent_key": "a_n_head"}
        for property_name in properties
    ]
    row = {
        "Name": "shared prefab",
        "MainAB": "asset.unity3d",
        "MainData": "p_asset",
    }
    return {
        "card": {},
        "coordinate_type": 0,
        "references": references,
        "resolved_assets": {
            property_name: [dict(row)]
            for property_name in properties
        },
        "resolved_count": len(properties),
        "unresolved_references": [],
    }


def _converted() -> dict:
    return {
        "items": [{
            "id": "renderer",
            "runtimeSkin": "renderer.json",
            "runtimeSkinSize": 10,
            "rigidAttachment": True,
            "runtimeFiles": [],
        }],
    }


def _patch_lightweight_manifest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    properties: tuple[str, ...] = ("slot-a",),
) -> None:
    monkeypatch.setattr(registry, "local_card_resources", lambda _card_id: _resources(*properties))
    monkeypatch.setattr(registry, "_shared_native_manifest_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        registry,
        "_runtime_cache_identity",
        lambda _row, _reference: (
            ("runtime-v19", "source", "fingerprint", "ab", "data", ""),
            "0123456789abcdef",
            "",
        ),
    )


def test_cache_only_manifest_never_launches_converter(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lightweight_manifest(monkeypatch)
    monkeypatch.setattr(
        registry,
        "_read_runtime_conversion",
        lambda _target: (None, "conversion-cache-miss"),
    )
    launches: list[Path] = []
    monkeypatch.setattr(
        registry,
        "_run_runtime_conversion",
        lambda _row, target, **_kwargs: launches.append(target),
    )

    manifest = registry.runtime_card_manifest("0123456789abcdef")

    assert manifest["conversion_mode"] == "cache-only"
    assert launches == []
    assert {"property": "slot-a", "reason": "conversion-cache-miss"} in manifest["conversion_failures"]


def test_optional_face_decal_failure_does_not_block_pack() -> None:
    failures = [
        {"slot": "mole", "reason": "extraction-cache-miss"},
        {"slot": "face_base", "reason": "extraction-cache-miss"},
    ]

    assert registry._blocking_face_texture_failures(failures) == [failures[1]]


def test_explicit_prewarm_converts_one_missing_prefab(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lightweight_manifest(monkeypatch)
    reads = iter([
        (None, "conversion-cache-miss"),
        (None, "conversion-cache-miss"),
        (_converted(), None),
    ])
    monkeypatch.setattr(registry, "_read_runtime_conversion", lambda _target: next(reads))
    launches: list[Path] = []

    def convert(_row: dict, target: Path, **_kwargs: object) -> None:
        launches.append(target)
        return None

    monkeypatch.setattr(registry, "_run_runtime_conversion", convert)

    manifest = registry.runtime_card_manifest(
        "0123456789abcdef",
        allow_conversion=True,
    )

    assert manifest["conversion_mode"] == "prewarm"
    assert len(launches) == 1
    assert manifest["items"][0]["runtimeSkin"].startswith(
        "/api/koikatsu-cards/runtime-shared/0123456789abcdef/"
    )


def test_two_semantic_slots_share_one_physical_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lightweight_manifest(monkeypatch, properties=("left-ear", "right-ear"))
    reads: list[Path] = []

    def read(target: Path) -> tuple[dict, None]:
        reads.append(target)
        return _converted(), None

    monkeypatch.setattr(registry, "_read_runtime_conversion", read)

    manifest = registry.runtime_card_manifest("0123456789abcdef")
    slot_items = [item for item in manifest["items"] if item.get("source_property")]

    assert len(reads) == 1
    assert [item["source_property"] for item in slot_items] == ["left-ear", "right-ear"]
    assert len({item["runtimeSkin"] for item in slot_items}) == 1


def test_manifest_attaches_native_clothing_and_pattern_textures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _resources("outfit0.ChaFileClothes.ClothesBot")
    resources["references"][0].update({
        "kind": "clothes",
        "appearance": {
            "colorInfo": [
                {"pattern": 3, "baseColor": [1, 1, 1, 1]},
            ],
        },
    })
    monkeypatch.setattr(registry, "local_card_resources", lambda _card_id: resources)
    monkeypatch.setattr(registry, "_shared_native_manifest_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        registry,
        "_clothing_texture_manifest",
        lambda *_args, **_kwargs: ({
            "outfit0.ChaFileClothes.ClothesBot": [{
                "textureSet": 1,
                "mainTexture": "/api/main.png",
                "colorMask": "/api/mask.png",
            }],
        }, []),
    )
    monkeypatch.setattr(
        registry,
        "_pattern_texture_manifest",
        lambda *_args, **_kwargs: ({
            "outfit0.ChaFileClothes.ClothesBot:pattern:0": "/api/pattern03.png",
        }, []),
    )
    monkeypatch.setattr(
        registry,
        "_runtime_cache_identity",
        lambda _row, _reference: (
            ("runtime-v19", "source", "fingerprint", "ab", "data", ""),
            "0123456789abcdef",
            "",
        ),
    )
    converted = _converted()
    converted["items"][0]["rendererGroups"] = [{
        "component": "clothes",
        "role": "normal",
        "textureSet": 1,
    }]
    monkeypatch.setattr(registry, "_read_runtime_conversion", lambda _target: (converted, None))

    manifest = registry.runtime_card_manifest("0123456789abcdef")
    item = next(value for value in manifest["items"] if value.get("source_property"))

    assert item["clothingTextureSets"][0]["colorMask"] == "/api/mask.png"
    assert item["appearance"]["colorInfo"][0]["patternTexture"] == "/api/pattern03.png"


def test_embedded_color_mask_bundle_sentinel_is_not_extracted() -> None:
    row = {
        "ColorMaskAB": "1",
        "ColorMaskTex": "chara/mt_mask_p5.unity3d",
    }

    assert registry._is_embedded_texture_bundle_sentinel(
        row,
        "ColorMaskAB",
        "ColorMaskTex",
    ) is True
    assert registry._is_embedded_texture_bundle_sentinel(
        {"ColorMaskAB": "chara/mask.unity3d", "ColorMaskTex": "annBM"},
        "ColorMaskAB",
        "ColorMaskTex",
    ) is False


def test_nothing_asset_with_zero_textures_is_skipped() -> None:
    assert registry._is_intentionally_empty_native_asset({
        "Name": "Nothing",
        "MainData": "p_o_top_sailor03_02",
        "MainTex": "0",
        "MainTexAB": "0",
        "ColorMaskTex": "0",
        "ColorMaskAB": "0",
    }) is True
    assert registry._is_intentionally_empty_native_asset({
        "Name": "Nothing",
        "MainData": "real_prefab",
        "MainTex": "real_texture",
        "MainTexAB": "chara/real.unity3d",
    }) is False


def test_reserved_empty_clothing_subpart_is_not_a_missing_dependency() -> None:
    references = card_parser._asset_references(
        {},
        [{"id": 1, "_sub_parts_id": [card_parser.EMPTY_NATIVE_ASSET_ID, 3]}],
        [],
        0,
        [],
    )

    assert [(item["category"], item["id"]) for item in references] == [(201, 3)]


def test_headmod_face_renderer_is_recognized_as_main_head() -> None:
    assert registry._is_runtime_head_main_face_id("cf_O_face") is True
    assert registry._is_runtime_head_main_face_id("face_026") is True
    assert registry._is_runtime_head_main_face_id("cf_O_noseline") is False


def test_texture_source_falls_back_to_vanilla_bundle_for_zipmod_cross_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mods = tmp_path / "mods"
    game = tmp_path / "game"
    mods.mkdir()
    vanilla_bundle = game / "abdata" / "chara" / "oo_base.unity3d"
    vanilla_bundle.parent.mkdir(parents=True)
    vanilla_bundle.write_bytes(b"vanilla")
    zipmod = mods / "cross-source.zipmod"
    with zipfile.ZipFile(zipmod, "w") as archive:
        archive.writestr("abdata/chara/mod.unity3d", b"prefab")
    stat = zipmod.stat()
    monkeypatch.setattr(registry, "MOD_ROOT", mods)
    monkeypatch.setattr(registry, "DEFAULT_GAME_ROOT", game)

    resolved = registry._trusted_texture_source(
        {
            "zipmod": str(zipmod),
            "source_type": "zipmod",
            "source_fingerprint": f"{stat.st_size}:{stat.st_mtime_ns}",
        },
        "chara/oo_base.unity3d",
    )

    assert resolved is not None
    assert resolved[0] == vanilla_bundle.resolve()


def test_empty_face_texture_field_does_not_create_extraction_failure() -> None:
    resources = {
        "face_texture_references": [{
            "resource_key": "detail",
            "property": "ChaFileFace.detailId",
            "slot": "face_detail_normal",
            "bundle_field": "MainAB",
            "texture_field": "NormallMapDetail",
        }],
        "resolved_face_textures": {
            "detail": [{
                "MainAB": "chara/nia_t.unity3d",
                "NormallMapDetail": "",
                "LineMask": "Nia_face_line",
            }],
        },
        "unresolved_face_textures": [],
    }

    _textures, failures = registry._face_texture_manifest(resources)

    assert failures == []


def test_native_hair_duplicate_uses_later_list_definition() -> None:
    rows = (
        {"MainAB": "chara/bo_hair_o_00.unity3d", "MainData": "old", "load_order": 10},
        {"MainAB": "chara/bo_hair_o_40.unity3d", "MainData": "new", "load_order": 20},
    )

    assert registry._native_base_row(rows, 104, ("MainAB", "MainData"))[0]["MainData"] == "new"


def test_native_non_hair_duplicate_keeps_first_list_definition() -> None:
    rows = (
        {"MainAB": "chara/co_top_00.unity3d", "MainData": "first", "load_order": 10},
        {"MainAB": "chara/co_top_40.unity3d", "MainData": "later", "load_order": 20},
    )

    assert registry._native_base_row(rows, 105, ("MainAB", "MainData"))[0]["MainData"] == "first"


@pytest.mark.parametrize(
    "relative",
    (
        "../secret.json",
        "0123456789abcdef/../../secret.json",
        "namespace/bad name.json",
        "0123456789abcdef\\file.json",
        "0123456789abcdef/C:/file.json",
    ),
)
def test_shared_runtime_path_rejects_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative: str,
) -> None:
    monkeypatch.setattr(registry, "RUNTIME_ASSET_ROOT", tmp_path)

    with pytest.raises(registry.KoikatsuTeacherError) as caught:
        registry._shared_runtime_file(relative)

    assert caught.value.code == "RUNTIME_ASSET_PATH_INVALID"


def test_material_editor_official_object_types_bind_exact_reference_slots() -> None:
    items = [
        {
            "id": "outfit0.accessory3.ChaFileAccessory.PartsInfo.id:Anne Hair Long",
            "source_kind": "accessory",
            "source_property": "outfit0.accessory3.ChaFileAccessory.PartsInfo.id",
        },
        {
            "id": "outfit0.ChaFileClothes.ClothesShorts:o_shorts_a",
            "source_kind": "clothes",
            "source_property": "outfit0.ChaFileClothes.ClothesShorts",
        },
        {
            "id": "ChaFileFace.headId:cf_O_face",
            "source_kind": "head",
            "source_property": "ChaFileFace.headId",
        },
        {
            "id": "__neutral_body__:neutral-body-00",
            "source_kind": "neutral_body",
        },
    ]
    resources = {
        "references": [
            {
                "kind": "accessory",
                "slot": 3,
                "property": "outfit0.accessory3.ChaFileAccessory.PartsInfo.id",
            },
            {
                "kind": "clothes",
                "slot": 3,
                "property": "outfit0.ChaFileClothes.ClothesShorts",
            },
        ],
        "material_editor_renderer_overrides": [
            {
                "object_type": 2,
                "slot": 3,
                "renderer": "Anne Hair Long",
                "enabled": False,
            },
            {
                "object_type": 2,
                "slot": 3,
                "renderer": "already omitted disabled renderer",
                "enabled": False,
            },
        ],
        "material_editor_property_overrides": [
            {
                "object_type": 2,
                "slot": 3,
                "material": "Anne Material Cara",
                "property": "Cutoff",
                "kind": "float",
                "value": 0.3,
            },
            {
                "object_type": 1,
                "slot": 3,
                "material": "shorts",
                "property": "Color",
                "kind": "color",
                "value": [1, 1, 1, 1],
            },
            {
                "object_type": 4,
                "slot": 0,
                "material": "cf_m_face_00",
                "property": "Cutoff",
                "kind": "float",
                "value": 0.1,
            },
        ],
        "material_editor_shader_overrides": [{
            "object_type": 2,
            "slot": 3,
            "material": "Anne Material Cara",
            "shader": "Shader Forge/main_item_ditherd",
            "shader_original": "Shader Forge/main_opaque",
            "render_queue": None,
            "render_queue_original": None,
        }],
        "material_editor_texture_overrides": [{
            "object_type": 2,
            "slot": 3,
            "material": "Anne HairMaterial1",
            "property": "MainTex",
            "texture_id": 2,
        }],
        "material_editor_copy_overrides": [{
            "object_type": 2,
            "slot": 3,
            "material": "Anne HairMaterial1",
            "copy_name": "Anne HairMaterial1 Copy",
        }],
    }

    failures = registry._apply_material_editor_contract(
        items,
        resources,
        {2: "/api/koikatsu-cards/local/card/runtime/material-editor/hash/2.png"},
    )

    accessory, clothing, head, body = items
    assert accessory["rendererEnabled"] is False
    assert accessory["materialShaderOverrides"][0]["shader"].endswith("ditherd")
    assert accessory["materialTextureOverrides"][0]["texture_id"] == 2
    assert accessory["materialPropertyOverrides"][0]["material"] == "Anne Material Cara"
    assert accessory["materialCopyOverrides"][0]["copy_name"].endswith(" Copy")
    assert clothing["materialPropertyOverrides"][0]["material"] == "shorts"
    assert "materialShaderOverrides" not in clothing
    assert head["materialPropertyOverrides"][0]["object_type"] == 4
    assert body["materialPropertyOverrides"][0]["object_type"] == 4
    assert failures == [{
        "reason": "material-copy-unsupported",
        "object_type": 2,
        "slot": 3,
        "material": "Anne HairMaterial1",
        "copy_name": "Anne HairMaterial1 Copy",
    }]


def test_material_editor_missing_enabled_renderer_remains_blocking() -> None:
    failures = registry._apply_material_editor_contract(
        [{
            "id": "outfit0.accessory3.ChaFileAccessory.PartsInfo.id:present",
            "source_kind": "accessory",
            "source_property": "outfit0.accessory3.ChaFileAccessory.PartsInfo.id",
        }],
        {
            "references": [{
                "kind": "accessory",
                "slot": 3,
                "property": "outfit0.accessory3.ChaFileAccessory.PartsInfo.id",
            }],
            "material_editor_renderer_overrides": [{
                "object_type": 2,
                "slot": 3,
                "renderer": "missing but required",
                "enabled": True,
            }],
        },
        {},
    )

    assert failures == [{
        "reason": "target-renderer-not-found",
        "field": "material_editor_renderer_overrides",
        "object_type": 2,
        "slot": 3,
        "renderer": "missing but required",
    }]


def _complete_test_png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def test_material_editor_texture_cache_is_cache_only_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(registry, "RUNTIME_ASSET_ROOT", tmp_path)
    monkeypatch.setattr(
        registry,
        "resolve_local_card",
        lambda _card_id: pytest.fail("cache-only path read the source card"),
    )
    monkeypatch.setattr(
        registry,
        "extract_material_editor_texture_blobs",
        lambda *_args, **_kwargs: pytest.fail("cache-only path expanded KKEx"),
    )
    resources = {
        "card": {"native_fingerprint": "a" * 64},
        "coordinate_type": 0,
        "material_editor_texture_overrides": [{"texture_id": 2}],
    }

    urls, failures = registry._material_editor_texture_cache(
        "0123456789abcdef",
        resources,
        allow_conversion=False,
    )

    assert urls == {}
    assert failures == [{
        "reason": "embedded-texture-cache-miss",
        "texture_id": 2,
    }]
    assert list(tmp_path.rglob("*")) == []


def test_material_editor_texture_prewarm_writes_valid_png_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "card.png"
    source.write_bytes(b"card")
    cache_root = tmp_path / "runtime"
    monkeypatch.setattr(registry, "RUNTIME_ASSET_ROOT", cache_root)
    monkeypatch.setattr(registry, "resolve_local_card", lambda _card_id: source)
    monkeypatch.setattr(
        registry,
        "extract_material_editor_texture_blobs",
        lambda content, coordinate: {2: _complete_test_png()}
        if content == b"card" and coordinate == 3 else {},
    )
    resources = {
        "card": {"native_fingerprint": "b" * 64},
        "coordinate_type": 3,
        "material_editor_texture_overrides": [{"texture_id": 2}],
    }

    urls, failures = registry._material_editor_texture_cache(
        "0123456789abcdef",
        resources,
        allow_conversion=True,
    )

    assert failures == []
    assert urls == {
        2: (
            "/api/koikatsu-cards/local/0123456789abcdef/runtime/"
            f"material-editor/{'b' * 24}/2.png"
        ),
    }
    target = cache_root / "0123456789abcdef" / "material-editor" / ("b" * 24) / "2.png"
    assert registry._looks_like_png(target)
    assert list(target.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    ("card_id", "expected_properties"),
    [
        ("eb8136372690f12d", 1),  # Ann: accessory slot 3
        ("dd9f692c6a679dd0", 15),  # Takagi: accessory slots 0..8
    ],
)
def test_real_ann_and_takagi_material_editor_contract_is_attached_offline(
    card_id: str,
    expected_properties: int,
) -> None:
    if not registry.LOCAL_CARD_ROOT.is_dir():
        pytest.skip("local read-only KKS card fixtures are unavailable")
    try:
        resources = registry.local_card_resources(card_id)
        manifest = registry.runtime_card_manifest(card_id, allow_conversion=False)
    except registry.KoikatsuTeacherError:
        pytest.skip("local read-only KKS runtime cache fixture is unavailable")
    runtime_items = [
        item for item in manifest["items"] if item.get("source_property")
    ]
    if not runtime_items:
        pytest.skip("local read-only KKS runtime cache fixture is unavailable")

    if card_id == "eb8136372690f12d":
        assert len(resources["material_editor_renderer_overrides"]) == 3
        assert len(resources["material_editor_texture_overrides"]) == 4
        assert len(resources["material_editor_shader_overrides"]) == 1
        accessory_items = [
            item
            for item in runtime_items
            if item.get("source_property")
            == "outfit0.accessory3.ChaFileAccessory.PartsInfo.id"
        ]
        assert len(accessory_items) == 4
        long_hair = next(item for item in accessory_items if item["id"].endswith(":Anne Hair Long"))
        assert long_hair["rendererEnabled"] is False
        assert all(
            item["materialShaderOverrides"][0]["object_type"] == 2
            for item in accessory_items
        )
        material_failures = manifest["materialEditorFailures"]
        if material_failures:
            assert manifest["status"] == "partial"
            assert {
                failure["reason"] for failure in material_failures
            } == {"embedded-texture-cache-miss"}
        else:
            assert manifest["status"] == "ready"
            assert all(
                {
                    override["texture_id"]
                    for override in item.get("materialTextureOverrides") or []
                } == {2, 3}
                for item in accessory_items
            )
            character_items = [
                item
                for item in manifest["items"]
                if item.get("source_kind") in {"head", "neutral_body"}
            ]
            assert character_items
            assert all(
                {
                    override["texture_id"]
                    for override in item.get("materialTextureOverrides") or []
                } == {1, 5}
                for item in character_items
            )
    else:
        assert len(resources["material_editor_property_overrides"]) == expected_properties
        assert manifest["materialEditorFailures"] == []
        assert manifest["status"] == "ready"
        bound_slots = {
            int(item["source_property"].split("accessory", 1)[1].split(".", 1)[0])
            for item in runtime_items
            if item.get("materialPropertyOverrides")
            and "accessory" in str(item.get("source_property"))
        }
        assert bound_slots == set(range(9))
