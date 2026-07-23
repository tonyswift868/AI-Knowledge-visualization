from __future__ import annotations

from pathlib import Path

from PIL import Image

from backend.tools import kks_eye_compositor as compositor


def _save(path: Path, pixels: list[tuple[int, int, int, int]], size: tuple[int, int]) -> str:
    image = Image.new("RGBA", size)
    image.putdata(pixels)
    image.save(path)
    return str(path)


def test_native_multiply_branch_applies_gradient_sub_color(tmp_path: Path) -> None:
    main = _save(tmp_path / "main.png", [(128, 128, 128, 255)], (1, 1))
    mask = _save(tmp_path / "mask.png", [(255, 0, 0, 255)], (1, 1))
    result = compositor.compose(main, mask, {
        "base_color": [1, 1, 1, 1],
        "sub_color": [1, 0, 0, 1],
        "gradient_blend": 0,
        "gradient_offset_y": 0.5,
        "gradient_scale": 0.5,
    })

    red, green, blue, alpha = result.getpixel((0, 0))
    assert red in range(127, 130)
    assert green == 0
    assert blue == 0
    assert alpha == 255


def test_black_main_pixel_uses_native_alpha_gate(tmp_path: Path) -> None:
    main = _save(tmp_path / "main.png", [(0, 255, 255, 77)], (1, 1))
    mask = _save(tmp_path / "mask.png", [(255, 0, 0, 255)], (1, 1))
    result = compositor.compose(main, mask, {
        "base_color": [1, 1, 1, 1],
        "sub_color": [1, 0, 0, 1],
        "gradient_blend": 1,
        "gradient_offset_y": 0.5,
        "gradient_scale": 0.5,
    })

    assert result.getpixel((0, 0)) == (0, 0, 0, 77)
