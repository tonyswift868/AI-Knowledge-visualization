from __future__ import annotations

import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture()
def exporter(monkeypatch: pytest.MonkeyPatch):
    """Load the offline exporter without requiring UnityPy in the API venv."""

    unitypy = types.ModuleType("UnityPy")
    unitypy.load = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "UnityPy", unitypy)
    path = Path(__file__).parents[1] / "tools" / "kks_face_texture_export.py"
    spec = importlib.util.spec_from_file_location("test_kks_face_texture_export_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zipmod_package_size_is_bounded_before_archive_open(
    exporter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "oversized.zipmod"
    source.write_bytes(b"12345")
    monkeypatch.setattr(exporter, "MAX_ZIPMOD_BYTES", 4)

    with pytest.raises(ValueError, match="zipmod package"):
        exporter._environment(source, "face.unity3d")


def test_zipmod_member_read_is_bounded_and_does_not_use_archive_read(
    exporter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "face.zipmod"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("abdata/face.unity3d", b"bundle")

    loaded: list[object] = []
    monkeypatch.setattr(exporter.UnityPy, "load", lambda value: loaded.append(value) or "environment")
    monkeypatch.setattr(
        zipfile.ZipFile,
        "read",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unbounded ZipFile.read used")),
    )

    assert exporter._environment(source, "face.unity3d") == "environment"
    assert len(loaded) == 1
    assert isinstance(loaded[0], io.BytesIO)
    assert loaded[0].getvalue() == b"bundle"


def test_missing_listinfo_bundle_falls_back_only_to_a_unique_unity_bundle(
    exporter,
    tmp_path: Path,
) -> None:
    source = tmp_path / "charamaker.zipmod"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("abdata/chara/_kero/CMS_NT_00.unity3d", b"bundle")

    with zipfile.ZipFile(source) as archive:
        entry = exporter._zip_member(archive, "chara/mt_eye_hi_up_00.unity3d")
        assert entry.filename == "abdata/chara/_kero/CMS_NT_00.unity3d"


def test_missing_listinfo_bundle_does_not_guess_between_multiple_bundles(
    exporter,
    tmp_path: Path,
) -> None:
    source = tmp_path / "ambiguous.zipmod"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("abdata/one.unity3d", b"one")
        archive.writestr("abdata/two.unity3d", b"two")

    with zipfile.ZipFile(source) as archive:
        with pytest.raises(KeyError, match="not present"):
            exporter._zip_member(archive, "chara/mt_eye_hi_up_00.unity3d")


def test_zipmod_member_decompressed_size_is_bounded(
    exporter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "face.zipmod"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("face.unity3d", b"12345")
    monkeypatch.setattr(exporter, "MAX_ZIP_MEMBER_BYTES", 4)

    with pytest.raises(ValueError, match="decompressed bundle"):
        exporter._environment(source, "face.unity3d")


def test_zip_member_stream_reads_only_limit_plus_one(exporter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exporter, "MAX_ZIP_MEMBER_BYTES", 4)
    sizes: list[int] = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int) -> bytes:
            sizes.append(size)
            return b"12345"

    archive = types.SimpleNamespace(open=lambda *_args, **_kwargs: Stream())
    entry = types.SimpleNamespace(file_size=4)

    with pytest.raises(ValueError, match="size is invalid"):
        exporter._read_zip_member(archive, entry)
    assert sizes == [5]


def test_bare_bundle_uses_a_separate_finite_limit(
    exporter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "abdata" / "face.unity3d"
    bundle.parent.mkdir()
    bundle.write_bytes(b"12345")
    monkeypatch.setattr(exporter, "MAX_BARE_BUNDLE_BYTES", 4)

    with pytest.raises(ValueError, match="trusted bare bundle"):
        exporter._environment(tmp_path, "face.unity3d")


def test_texture_request_count_is_checked_before_loading_bundle(
    exporter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(exporter, "MAX_TEXTURE_REQUESTS", 1)
    monkeypatch.setattr(
        exporter,
        "_environment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("bundle loaded first")),
    )
    requests = [
        {"texture": "eye", "filename": "eye.png"},
        {"texture": "nose", "filename": "nose.png"},
    ]

    with pytest.raises(ValueError, match="request count"):
        exporter.extract(tmp_path, "face.unity3d", tmp_path / "out", requests)


def test_eye_white_color_key_is_baked_into_png_alpha(exporter) -> None:
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
    for y in range(3, 7):
        for x in range(2, 8):
            image.putpixel((x, y), (255, 255, 255, 255))

    changed = exporter._bake_color_key_alpha(image)

    assert changed > 0
    assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    assert image.getpixel((5, 5)) == (255, 255, 255, 255)


def test_sideloader_loose_png_uses_bundle_mirror_path_without_unity_bundle(
    exporter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "face.zipmod"
    image_bytes = io.BytesIO()
    Image.new("RGBA", (2, 3), (12, 34, 56, 255)).save(image_bytes, format="PNG")
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("abdata/chara/mt_eyeline_up_00/reika.png", image_bytes.getvalue())
    monkeypatch.setattr(
        exporter,
        "_environment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("loose texture loaded as bundle")),
    )

    result = exporter.extract(
        source,
        "chara/mt_eyeline_up_00.unity3d",
        tmp_path / "out",
        [{"texture": "reika", "filename": "line.png"}],
    )

    assert result == {"reika": "line.png"}
    with Image.open(tmp_path / "out" / "line.png") as written:
        assert written.size == (2, 3)
        assert written.convert("RGBA").getpixel((0, 0)) == (12, 34, 56, 255)


def test_loose_png_path_is_exact_and_does_not_search_unrelated_directories(
    exporter,
    tmp_path: Path,
) -> None:
    source = tmp_path / "face.zipmod"
    image_bytes = io.BytesIO()
    Image.new("RGBA", (1, 1), (255, 255, 255, 255)).save(image_bytes, format="PNG")
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("abdata/chara/some_other_folder/reika.png", image_bytes.getvalue())

    assert exporter._loose_zip_textures(
        source,
        "chara/mt_eyeline_up_00.unity3d",
        {"reika": {"texture": "reika", "filename": "line.png"}},
    ) == {}
