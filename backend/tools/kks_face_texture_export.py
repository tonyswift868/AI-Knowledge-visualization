"""Extract selected KKS face Texture2D assets into browser-ready PNG files.

The tool intentionally accepts a batch of texture requests for one bundle so
opening a card does not parse the same large zipmod once per eyebrow/eye line.
It runs outside the web process and writes only the requested immutable PNGs.
"""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

try:
    import UnityPy  # type: ignore
except ImportError as exc:  # pragma: no cover - optional offline dependency
    raise SystemExit("UnityPy is required for KKS face texture extraction") from exc

try:
    # Reuse the bounded UnityFS reader used by the mesh converter.  Running
    # this module as a script puts ``backend/tools`` on sys.path, so the plain
    # import works both from the API prewarm worker and from direct CLI use.
    from kks_runtime_skin_export import _BundleRangeReader, _large_bare_bundle_cab
except ImportError:  # pragma: no cover - test loaders may stub UnityPy
    _BundleRangeReader = None  # type: ignore[assignment]
    _large_bare_bundle_cab = None  # type: ignore[assignment]


MAX_ZIPMOD_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
MAX_BARE_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
MAX_LOOSE_TEXTURE_BYTES = 64 * 1024 * 1024
MAX_TEXTURE_EDGE = 8192
MAX_TEXTURE_REQUESTS = 16
LARGE_TOP_BUNDLE_BYTES = 1024 * 1024 * 1024


def _zip_member(archive: zipfile.ZipFile, main_ab: str) -> zipfile.ZipInfo:
    """Select one exact bundle member without relying on archive order."""

    normalized = str(main_ab or "").replace("\\", "/")
    for candidate in (normalized, f"abdata/{normalized}"):
        matches = [
            entry
            for entry in archive.infolist()
            if not entry.is_dir()
            and str(entry.filename).replace("\\", "/").casefold() == candidate.casefold()
        ]
        if len(matches) > 1:
            raise ValueError(f"bundle {main_ab!r} is ambiguous in the zipmod")
        if matches:
            return matches[0]

    # A few Character Maker ListInfo CSVs shipped by older mods contain a
    # stale ``MainAB`` (for example ``mt_eye_hi_up_00.unity3d``), while the
    # archive contains the requested texture in one shared CMS bundle.  Do
    # not guess across several bundles: only accept this compatibility path
    # when the package has exactly one Unity bundle, which makes the fallback
    # deterministic and prevents unrelated geometry/material substitution.
    bundles = [
        entry
        for entry in archive.infolist()
        if not entry.is_dir()
        and str(entry.filename).replace("\\", "/").casefold().endswith(".unity3d")
    ]
    if len(bundles) == 1:
        return bundles[0]
    raise KeyError(f"bundle {main_ab!r} is not present in the zipmod")


def _read_zip_member(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> bytes:
    """Read one untrusted member with a hard decompressed-size ceiling."""

    if entry.file_size < 1 or entry.file_size > MAX_ZIP_MEMBER_BYTES:
        raise ValueError(f"decompressed bundle exceeds {MAX_ZIP_MEMBER_BYTES} bytes")
    with archive.open(entry, "r") as stream:
        payload = stream.read(MAX_ZIP_MEMBER_BYTES + 1)
    if len(payload) != entry.file_size or len(payload) > MAX_ZIP_MEMBER_BYTES:
        raise ValueError("decompressed bundle size is invalid")
    return payload


def _read_loose_texture(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> Image.Image:
    """Decode one exact Sideloader loose PNG under a strict byte/pixel cap."""

    if entry.file_size < 20 or entry.file_size > MAX_LOOSE_TEXTURE_BYTES:
        raise ValueError(f"loose texture exceeds {MAX_LOOSE_TEXTURE_BYTES} bytes")
    with archive.open(entry, "r") as stream:
        payload = stream.read(MAX_LOOSE_TEXTURE_BYTES + 1)
    if len(payload) != entry.file_size or len(payload) > MAX_LOOSE_TEXTURE_BYTES:
        raise ValueError("loose texture size is invalid")
    with Image.open(io.BytesIO(payload)) as source:
        source.load()
        if source.width < 1 or source.height < 1 or source.width > MAX_TEXTURE_EDGE or source.height > MAX_TEXTURE_EDGE:
            raise ValueError("loose texture dimensions are invalid")
        return source.convert("RGBA")


def _loose_zip_textures(
    source: Path,
    main_ab: str,
    wanted: dict[str, dict[str, str]],
) -> dict[str, Image.Image]:
    """Resolve the standard Sideloader loose-file mirror of one bundle path."""

    if not (source.suffix.casefold() == ".zipmod" or zipfile.is_zipfile(source)):
        return {}
    package_size = source.stat().st_size
    if package_size < 1 or package_size > MAX_ZIPMOD_BYTES:
        raise ValueError(f"zipmod package exceeds {MAX_ZIPMOD_BYTES} bytes")
    normalized_ab = PurePosixPath(str(main_ab or "").replace("\\", "/"))
    directory = normalized_ab.with_suffix("")
    result: dict[str, Image.Image] = {}
    with zipfile.ZipFile(source) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        by_name: dict[str, list[zipfile.ZipInfo]] = {}
        for entry in entries:
            by_name.setdefault(str(entry.filename).replace("\\", "/").casefold(), []).append(entry)
        for key, request in wanted.items():
            texture = PurePosixPath(str(request["texture"])).name
            filename = texture if PurePosixPath(texture).suffix else f"{texture}.png"
            candidate = str(PurePosixPath("abdata") / directory / filename).casefold()
            matches = by_name.get(candidate, [])
            if len(matches) > 1:
                raise ValueError(f"loose texture {texture!r} is ambiguous in the zipmod")
            if matches:
                result[key] = _read_loose_texture(archive, matches[0])
    return result


def _validated_requests(requests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(requests, list):
        raise ValueError("texture requests must be a list")
    if len(requests) > MAX_TEXTURE_REQUESTS:
        raise ValueError(f"texture request count exceeds {MAX_TEXTURE_REQUESTS}")
    wanted: dict[str, dict[str, str]] = {}
    for request in requests:
        if not isinstance(request, dict) or "texture" not in request or "filename" not in request:
            raise ValueError("each texture request must contain texture and filename")
        texture = str(request["texture"])
        filename = Path(str(request["filename"])).name
        if not texture or not filename:
            raise ValueError("texture request fields must not be empty")
        color_key_alpha = request.get("color_key_alpha", False)
        if not isinstance(color_key_alpha, bool):
            raise ValueError("color_key_alpha must be a boolean")
        wanted[texture.casefold()] = {
            "texture": texture,
            "filename": filename,
            "color_key_alpha": color_key_alpha,
        }
    return wanted


def _bake_color_key_alpha(image: Image.Image) -> int:
    """Bake KKS eye-white background colour into PNG alpha offline."""

    rgba = image.convert("RGBA")
    width, height = rgba.size
    if width < 1 or height < 1:
        return 0
    pixels = list(rgba.getdata())
    if len(pixels) != width * height or any(int(pixel[3]) != 255 for pixel in pixels):
        return 0
    corners = (pixels[0], pixels[width - 1], pixels[(height - 1) * width], pixels[-1])
    if any(
        max(int(pixel[channel]) for pixel in corners)
        - min(int(pixel[channel]) for pixel in corners) > 4
        for channel in range(3)
    ):
        return 0
    background = tuple(
        sorted(int(pixel[channel]) for pixel in corners)[1]
        for channel in range(3)
    )
    baked: list[tuple[int, int, int, int]] = []
    changed = 0
    for pixel in pixels:
        red, green, blue, alpha = (int(value) for value in pixel[:4])
        difference = max(
            abs(red - background[0]),
            abs(green - background[1]),
            abs(blue - background[2]),
        )
        coverage = max(0.0, min((difference - 5.0) / 28.0, 1.0))
        next_alpha = int(alpha * coverage + 0.5)
        changed += next_alpha != alpha
        baked.append((red, green, blue, next_alpha))
    foreground = sum(pixel[3] > 0 for pixel in baked)
    if foreground < max(1, int(len(baked) * 0.01)) or foreground > int(len(baked) * 0.9):
        return 0
    rgba.putdata(baked)
    image.paste(rgba)
    return changed


def _environment(source: Path, main_ab: str) -> Any:
    if source.suffix.casefold() == ".zipmod" or zipfile.is_zipfile(source):
        if not source.is_file():
            raise FileNotFoundError(source)
        package_size = source.stat().st_size
        if package_size < 1 or package_size > MAX_ZIPMOD_BYTES:
            raise ValueError(f"zipmod package exceeds {MAX_ZIPMOD_BYTES} bytes")
        with zipfile.ZipFile(source) as archive:
            entry = _zip_member(archive, main_ab)
            payload = _read_zip_member(archive, entry)
        return UnityPy.load(io.BytesIO(payload))
    bundle = source if source.is_file() else source / "abdata" / main_ab
    if not bundle.is_file():
        raise FileNotFoundError(bundle)
    if bundle.stat().st_size > MAX_BARE_BUNDLE_BYTES:
        raise ValueError(f"trusted bare bundle exceeds {MAX_BARE_BUNDLE_BYTES} bytes")
    if (
        bundle.name.casefold() == "co_top_00.unity3d"
        and bundle.stat().st_size >= LARGE_TOP_BUNDLE_BYTES
        and _large_bare_bundle_cab is not None
        and _BundleRangeReader is not None
    ):
        # The stock top bundle is a 1.2GiB raw UnityFS block. Read only its
        # serialized CAB and register the .resS node as a lazy range reader;
        # Texture2D extraction then seeks to the requested bytes without
        # allocating the whole clothing bundle.
        cab_payload, resource_nodes = _large_bare_bundle_cab(bundle)
        environment = UnityPy.load(io.BytesIO(cab_payload))
        for resource_name, (absolute_offset, resource_size) in resource_nodes.items():
            environment.register_cab(
                resource_name,
                _BundleRangeReader(bundle, absolute_offset, resource_size),
            )
        return environment
    return UnityPy.load(str(bundle))


def extract(source: Path, main_ab: str, output: Path, requests: list[dict[str, Any]]) -> dict[str, str]:
    wanted = _validated_requests(requests)
    found: dict[str, Any] = dict(_loose_zip_textures(source, main_ab, wanted))
    remaining = set(wanted) - set(found)
    if remaining:
        environment = _environment(source, main_ab)
        for obj in environment.objects:
            if obj.type.name != "Texture2D":
                continue
            data = obj.read()
            name = str(getattr(data, "m_Name", "") or "")
            if name.casefold() in remaining:
                found[name.casefold()] = data
                if len(found) == len(wanted):
                    break
    output.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for key, request in wanted.items():
        data = found.get(key)
        if data is None:
            continue
        filename = request["filename"]
        target = output / filename
        temporary = target.with_suffix(".tmp.png")
        image = data if isinstance(data, Image.Image) else data.image.convert("RGBA")
        if request["color_key_alpha"]:
            _bake_color_key_alpha(image)
        image.save(temporary, format="PNG", optimize=True)
        temporary.replace(target)
        written[str(request["texture"])] = filename
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract selected Texture2D assets from one KKS bundle")
    parser.add_argument("source", type=Path)
    parser.add_argument("main_ab")
    parser.add_argument("output", type=Path)
    parser.add_argument("requests_json", help='JSON list of {"texture": ..., "filename": ...}')
    args = parser.parse_args()
    requests = json.loads(args.requests_json)
    if not isinstance(requests, list):
        parser.error("requests_json must be a list")
    written = extract(args.source, args.main_ab, args.output, requests)
    print(json.dumps({"written": written}, ensure_ascii=False))


if __name__ == "__main__":
    main()
