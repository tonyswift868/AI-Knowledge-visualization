"""Avatar pixels after the real desktop preparation path (229)."""
import io

import pytest
from PIL import Image, ImageDraw

pytest_plugins = ["support_profile229"]

from support_profile229 import new_account229  # noqa: E402
from refactor.desktop.account_profile import encode_crop, image_preview  # noqa: E402
from refactor.desktop.account_protocol import AccountError  # noqa: E402
from refactor.server.enterprise.account_profiles import (  # noqa: E402
    AccountProfileError,
    _normalize_image,
)


def _oriented_phone_photo(path):
    """Landscape sensor pixels tagged by the phone for upright portrait display."""
    image = Image.new("RGB", (120, 80), (30, 90, 210))
    draw = ImageDraw.Draw(image)
    # EXIF 6 (90 degrees clockwise) maps raw (x=50..70,y=0..20) to
    # oriented (x=60..80,y=50..70). The centered 80x80 crop removes y=0..20,
    # so this independent expected red marker lands at output (x=60..80,y=30..50).
    draw.rectangle((50, 0, 70, 20), fill=(230, 20, 20))
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private location note"
    image.save(path, "JPEG", quality=95, exif=exif)
    return path


def _client_avatar_payload(path):
    preview = image_preview(path)
    side = min(preview.size)
    x = (preview.width - side) // 2
    y = (preview.height - side) // 2
    return preview, encode_crop(preview, {"x": x, "y": y, "size": side})


def _near(actual, expected, tolerance=55):
    return sum((int(actual[i]) - expected[i]) ** 2 for i in range(3)) <= tolerance**2 * 3


def test_phone_exif_orientation_and_backend_pixels_after_desktop_pipeline229(profile_env229, tmp_path):
    """The supported UI pipeline corrects EXIF and both HTTP backends show it."""
    env = profile_env229
    url = "/api/enterprise/account/profile"
    avatar_url = url + "/avatar"

    source_path = _oriented_phone_photo(tmp_path / "phone.jpg")
    with Image.open(source_path) as source:
        assert source.size == (120, 80) and source.getexif().get(274) == 6
    preview, payload = _client_avatar_payload(source_path)
    assert preview.size == (80, 120)
    with Image.open(io.BytesIO(payload)) as prepared:
        assert prepared.format == "JPEG" and prepared.size == (80, 80)
        assert not prepared.getexif()

    observed = {}
    for backend in ("python", "java"):
        client = env["clients"][backend]
        actor = new_account229(env, "phone-orientation-" + backend)
        initial = client.get(url, headers=actor["headers"])
        assert initial.status_code == 200 and initial.json()["version"] == 1
        saved = client.put(
            avatar_url,
            headers={**actor["headers"], "If-Match": '"profile-1"', "Content-Type": "image/jpeg"},
            content=payload,
        )
        assert saved.status_code == 200, saved.text
        fetched = client.get(avatar_url, headers=actor["headers"])
        assert fetched.status_code == 200
        assert fetched.headers["etag"] == saved.json()["avatar"]["etag"]
        with Image.open(io.BytesIO(fetched.content)) as result:
            result.load()
            assert result.size == (80, 80) and not result.getexif()
            rgb = result.convert("RGB")
            # A server that interpreted raw EXIF=6 again would rotate the red
            # marker away from this independently derived output coordinate.
            assert _near(rgb.getpixel((70, 40)), (230, 20, 20))
            assert _near(rgb.getpixel((10, 10)), (30, 90, 210))
            assert _near(rgb.getpixel((10, 60)), (30, 90, 210))
            observed[backend] = rgb.copy()
    for point in ((70, 40), (10, 10), (10, 60)):
        assert sum(abs(observed["python"].getpixel(point)[i]
                       - observed["java"].getpixel(point)[i]) for i in range(3)) <= 55


def test_animated_and_metadata_heavy_png_are_rejected_before_upload229(tmp_path):
    """Unsupported PNGs stop in desktop preparation; there is no UI upload path."""
    frame1 = Image.new("RGB", (32, 32), "red")
    frame2 = Image.new("RGB", (32, 32), "blue")
    apng_path = tmp_path / "animated.png"
    frame1.save(apng_path, "PNG", save_all=True, append_images=[frame2], duration=100, loop=0)
    with pytest.raises(AccountError, match="有效的 JPEG 或 PNG 静态图片"):
        image_preview(apng_path)

    from PIL.PngImagePlugin import PngInfo
    metadata_path = tmp_path / "metadata.png"
    metadata = PngInfo()
    metadata.add_text("bounded-metadata", "m" * (1536 * 1024), zip=True)
    Image.new("RGB", (32, 32), "purple").save(metadata_path, "PNG", pnginfo=metadata)
    with pytest.raises(AccountError, match="有效的 JPEG 或 PNG 静态图片"):
        image_preview(metadata_path)

    # An attacker can bypass the desktop, so also assert the Python backend's
    # expected rejection of decompressed text. Java's ImageIO may ignore
    # ancillary zTXt and take frame zero; it is not reachable from this UI path.
    with metadata_path.open("rb") as image_file:
        with pytest.raises(AccountProfileError):
            _normalize_image(image_file.read(), "image/png")
