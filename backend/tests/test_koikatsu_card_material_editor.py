from __future__ import annotations

from pathlib import Path

import msgpack
import pytest

from app import koikatsu_card as card


def _payload(**fields: object) -> dict[str, object]:
    encoded = {
        name: msgpack.packb(value, use_bin_type=True)
        for name, value in fields.items()
    }
    return {
        card.MATERIAL_EDITOR_PLUGIN_ID: [1, encoded],
    }


def test_material_shader_list_is_coordinate_aware_and_normalized():
    payload = _payload(
        MaterialShaderList=[
            {
                "ObjectType": 2,
                "CoordinateIndex": 3,
                "Slot": 3,
                "MaterialName": "Anne Material Cara",
                "ShaderName": "Shader Forge/main_item_ditherd",
                "ShaderNameOriginal": "Shader Forge/main_opaque",
                "RenderQueue": None,
                "RenderQueueOriginal": None,
            },
            {
                "ObjectType": 2,
                "CoordinateIndex": 2,
                "Slot": 3,
                "MaterialName": "wrong outfit",
                "ShaderName": "ignored",
            },
            {
                "ObjectType": 3,
                "CoordinateIndex": 0,
                "Slot": 1,
                "MaterialName": "hair",
                "ShaderName": "hair shader",
                "ShaderNameOriginal": "hair original",
            },
            {
                "ObjectType": 4,
                "CoordinateIndex": 0,
                "Slot": 0,
                "MaterialName": "face",
                "ShaderName": None,
                "ShaderNameOriginal": None,
                "RenderQueue": 2450,
                "RenderQueueOriginal": 2000,
            },
            {
                "ObjectType": 4,
                "CoordinateIndex": 0,
                "Slot": 0,
                "MaterialName": "empty",
                "ShaderName": None,
                "RenderQueue": None,
            },
        ],
    )

    assert card._material_editor_shader_overrides(payload, 3) == [
        {
            "object_type": 2,
            "slot": 3,
            "material": "Anne Material Cara",
            "shader": "Shader Forge/main_item_ditherd",
            "shader_original": "Shader Forge/main_opaque",
            "render_queue": None,
            "render_queue_original": None,
        },
        {
            "object_type": 3,
            "slot": 1,
            "material": "hair",
            "shader": "hair shader",
            "shader_original": "hair original",
            "render_queue": None,
            "render_queue_original": None,
        },
        {
            "object_type": 4,
            "slot": 0,
            "material": "face",
            "shader": None,
            "shader_original": None,
            "render_queue": 2450,
            "render_queue_original": 2000,
        },
    ]


def test_material_copy_list_is_coordinate_aware_and_bounded():
    payload = _payload(
        MaterialCopyList=[
            {
                "ObjectType": 1,
                "CoordinateIndex": 6,
                "Slot": 2,
                "MaterialName": "source",
                "MaterialCopyName": "source-copy",
            },
            {
                "ObjectType": 1,
                "CoordinateIndex": 5,
                "Slot": 2,
                "MaterialName": "wrong outfit",
                "MaterialCopyName": "ignored",
            },
            {
                "ObjectType": 4,
                "CoordinateIndex": 0,
                "Slot": 0,
                "MaterialName": "face",
                "MaterialCopyName": "face-copy",
            },
            {
                "ObjectType": 9,
                "CoordinateIndex": 6,
                "Slot": 0,
                "MaterialName": "invalid object",
                "MaterialCopyName": "ignored",
            },
            {
                "ObjectType": 2,
                "CoordinateIndex": 6,
                "Slot": 0,
                "MaterialName": "x" * 1_025,
                "MaterialCopyName": "ignored",
            },
        ],
    )

    assert card._material_editor_copy_overrides(payload, 6) == [
        {
            "object_type": 1,
            "slot": 2,
            "material": "source",
            "copy_name": "source-copy",
        },
        {
            "object_type": 4,
            "slot": 0,
            "material": "face",
            "copy_name": "face-copy",
        },
    ]


def test_material_shader_and_copy_lists_are_selected_from_kkex_projection():
    nested_shader = msgpack.packb(
        [{
            "ObjectType": 2,
            "CoordinateIndex": 0,
            "Slot": 0,
            "MaterialName": "cloth",
            "ShaderName": "shader",
        }],
        use_bin_type=True,
    )
    nested_copy = msgpack.packb(
        [{
            "ObjectType": 2,
            "CoordinateIndex": 0,
            "Slot": 0,
            "MaterialName": "cloth",
            "MaterialCopyName": "cloth-copy",
        }],
        use_bin_type=True,
    )
    kkex = msgpack.packb(
        {
            card.MATERIAL_EDITOR_PLUGIN_ID: [
                1,
                {
                    "MaterialShaderList": nested_shader,
                    "MaterialCopyList": nested_copy,
                    "RendererPropertyList": None,
                    "TextureDictionary": b"must not be projected",
                },
            ],
        },
        use_bin_type=True,
    )

    projected, texture_dictionary = card._project_kkex_payload(kkex)
    plugin_data = projected[card.MATERIAL_EDITOR_PLUGIN_ID][1]
    assert plugin_data == {
        "MaterialShaderList": nested_shader,
        "MaterialCopyList": nested_copy,
    }
    assert texture_dictionary is None


@pytest.mark.parametrize("field,parser", [
    ("MaterialShaderList", card._material_editor_shader_overrides),
    ("MaterialCopyList", card._material_editor_copy_overrides),
])
def test_material_editor_new_lists_reject_excessive_record_counts(field, parser):
    payload = _payload(
        **{
            field: [
                {
                    "ObjectType": 2,
                    "CoordinateIndex": 0,
                    "Slot": 0,
                }
            ]
            * (card.MAX_MATERIAL_EDITOR_RENDERER_OVERRIDES + 1)
        },
    )
    assert parser(payload, 0) == []


KKS_CARD_ROOT = Path(
    r"E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\UserData\chara\female"
)


def _real_card(pattern: str) -> Path:
    if not KKS_CARD_ROOT.is_dir():
        pytest.skip("local read-only KKS fixture library is unavailable")
    match = next(KKS_CARD_ROOT.rglob(pattern), None)
    if match is None:
        pytest.skip(f"local read-only KKS fixture is unavailable: {pattern}")
    return match


@pytest.mark.parametrize(
    ("pattern", "expected_shader_count"),
    [
        ("*20260331054307896*.png", 0),  # 早坂愛
        ("*20260327162521998*.png", 1),  # Takamaki Ann, active coordinate 0
        ("*アマリル*.png", 0),
    ],
)
def test_real_acceptance_cards_publish_bounded_material_editor_lists(
    pattern: str, expected_shader_count: int
):
    parsed = card.inspect_koikatsu_sunshine_card(_real_card(pattern).read_bytes())
    native = parsed["native_summary"]
    shader_overrides = native["material_editor_shader_overrides"]
    copy_overrides = native["material_editor_copy_overrides"]

    assert len(shader_overrides) == expected_shader_count
    assert copy_overrides == []
    if shader_overrides:
        assert shader_overrides[0] == {
            "object_type": 2,
            "slot": 3,
            "material": "Anne Material Cara",
            "shader": "Shader Forge/main_item_ditherd",
            "shader_original": "Shader Forge/main_opaque",
            "render_queue": None,
            "render_queue_original": None,
        }
