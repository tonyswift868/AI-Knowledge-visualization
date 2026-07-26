from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from PIL import Image, ImageChops


@pytest.fixture()
def compositor():
    path = Path(__file__).parents[1] / "tools" / "kks_face_compositor.py"
    spec = importlib.util.spec_from_file_location("test_kks_face_compositor_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_color_mask_matches_native_skin_channel_equation(compositor) -> None:
    base = Image.new("RGBA", (4, 1), (255, 255, 255, 255))
    mask = Image.new("RGBA", (4, 1))
    mask.putdata([
        (255, 0, 0, 0),
        (0, 255, 0, 0),
        (0, 0, 255, 0),
        (0, 0, 0, 0),
    ])

    result = compositor._base_skin(
        base,
        mask,
        (0.2, 0.4, 0.6, 1.0),
        (0.8, 0.6, 0.4, 1.0),
    )

    assert list(result.getdata()) == [
        (51, 102, 153, 255),
        (204, 153, 102, 255),
        (255, 255, 255, 255),
        (255, 255, 255, 255),
    ]


def test_neutral_preview_covers_only_blue_dominant_mouth_cavity(compositor) -> None:
    current = Image.new("RGBA", (4, 1))
    current.putdata([
        (80, 20, 20, 255),
        (70, 30, 20, 255),
        (60, 40, 20, 255),
        (50, 50, 20, 255),
    ])
    mask = Image.new("RGBA", (4, 1))
    mask.putdata([
        (0, 0, 255, 255),
        (0, 255, 0, 255),
        (255, 0, 0, 255),
        (0, 0, 100, 255),
    ])

    result = compositor._neutralize_bind_pose_mouth_cavity(
        current,
        mask,
        (0.9, 0.8, 0.7, 1.0),
    )

    assert list(result.getdata()) == [
        (230, 204, 178, 255),
        (70, 30, 20, 255),
        (60, 40, 20, 255),
        (50, 50, 20, 255),
    ]


def test_required_texture_dimension_limit_is_enforced(compositor, tmp_path: Path) -> None:
    source = tmp_path / "oversized.png"
    Image.new("RGBA", (5, 1), (255, 255, 255, 255)).save(source)
    compositor.MAX_EDGE = 4

    with pytest.raises(ValueError, match="dimensions"):
        compositor._open_texture(str(source), required=True)


def test_transparent_sentinel_does_not_open_a_file(compositor) -> None:
    assert compositor._open_texture(compositor.TRANSPARENT) is None


def test_lip_line_is_not_shrunk_to_a_single_pixel(compositor, tmp_path: Path) -> None:
    base_path = tmp_path / "base.png"
    mask_path = tmp_path / "mask.png"
    lip_path = tmp_path / "lip.png"
    base = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    mask = Image.new("RGBA", (1024, 1024), (0, 0, 255, 255))
    lip = Image.new("RGBA", (128, 58), (0, 0, 0, 0))
    for x in range(48, 80):
        for y in range(24, 29):
            lip.putpixel((x, y), (255, 255, 255, 255))
    base.save(base_path)
    mask.save(mask_path)
    lip.save(lip_path)

    result = compositor.compose(
        {
            "face_base": str(base_path),
            "face_color_mask": str(mask_path),
            "lip_line": str(lip_path),
        },
        {
            "skin_main_color": [1, 1, 1, 1],
            "skin_sub_color": [1, 1, 1, 1],
            "lip_line_color": [0.7, 0.2, 0.2, 1],
        },
    )
    bbox = ImageChops.difference(base, result).getbbox()
    assert bbox is not None
    assert bbox[2] - bbox[0] >= 20
    assert bbox[3] - bbox[1] >= 5


def test_missing_nose_uses_shared_neutral_face_cue(compositor, tmp_path: Path) -> None:
    base_path = tmp_path / "base.png"
    mask_path = tmp_path / "mask.png"
    base = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    mask = Image.new("RGBA", (1024, 1024), (0, 0, 255, 255))
    base.save(base_path)
    mask.save(mask_path)

    result = compositor.compose(
        {"face_base": str(base_path), "face_color_mask": str(mask_path)},
        {"skin_main_color": [1, 1, 1, 1], "skin_sub_color": [1, 1, 1, 1]},
    )

    nose_crop = result.crop((450, 360, 580, 500)).convert("RGB")
    assert any(pixel != (255, 255, 255) for pixel in nose_crop.getdata())
    assert result.getpixel((512, 780))[:3] == (255, 255, 255)


def test_lip_colour_keeps_neutral_mouth_when_lip_atlas_is_absent(compositor, tmp_path: Path) -> None:
    base_path = tmp_path / "base.png"
    mask_path = tmp_path / "mask.png"
    base = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    mask = Image.new("RGBA", (1024, 1024), (0, 0, 255, 255))
    base.save(base_path)
    mask.save(mask_path)

    result = compositor.compose(
        {"face_base": str(base_path), "face_color_mask": str(mask_path)},
        {
            "skin_main_color": [1, 1, 1, 1],
            "skin_sub_color": [1, 1, 1, 1],
            "lip_line_color": [0.7, 0.2, 0.2, 1],
        },
    )

    mouth_crop = result.crop((450, 500, 580, 610)).convert("RGB")
    assert any(pixel != (255, 255, 255) for pixel in mouth_crop.getdata())


def test_neutral_face_cues_remain_legible_at_browser_portrait_scale(compositor) -> None:
    mouth = compositor._neutral_mouth_support((1024, 1024), (0.45, 0.2, 0.2, 1.0))
    nose = compositor._neutral_nose_layer((1024, 1024))

    mouth_bbox = mouth.getchannel("A").getbbox()
    nose_bbox = nose.getchannel("A").getbbox()

    assert mouth_bbox is not None and mouth_bbox[2] - mouth_bbox[0] >= 100
    assert nose_bbox is not None and nose_bbox[2] - nose_bbox[0] >= 24
