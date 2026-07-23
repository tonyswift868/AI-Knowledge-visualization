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
from pathlib import Path
from typing import Any

try:
    import UnityPy  # type: ignore
except ImportError as exc:  # pragma: no cover - optional offline dependency
    raise SystemExit("UnityPy is required for KKS face texture extraction") from exc


def _environment(source: Path, main_ab: str) -> Any:
    if source.suffix.casefold() == ".zipmod" or zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            names = {name.casefold(): name for name in archive.namelist()}
            wanted = (main_ab, f"abdata/{main_ab}")
            member = next((names.get(value.replace("\\", "/").casefold()) for value in wanted if names.get(value.replace("\\", "/").casefold())), None)
            if not member:
                raise KeyError(f"bundle {main_ab!r} is not present in {source}")
            return UnityPy.load(io.BytesIO(archive.read(member)))
    bundle = source if source.is_file() else source / "abdata" / main_ab
    if not bundle.is_file():
        raise FileNotFoundError(bundle)
    return UnityPy.load(str(bundle))


def extract(source: Path, main_ab: str, output: Path, requests: list[dict[str, str]]) -> dict[str, str]:
    environment = _environment(source, main_ab)
    wanted = {str(item["texture"]).casefold(): item for item in requests}
    found: dict[str, Any] = {}
    for obj in environment.objects:
        if obj.type.name != "Texture2D":
            continue
        data = obj.read()
        name = str(getattr(data, "m_Name", "") or "")
        if name.casefold() in wanted:
            found[name.casefold()] = data
            if len(found) == len(wanted):
                break
    output.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for key, request in wanted.items():
        data = found.get(key)
        if data is None:
            continue
        filename = Path(str(request["filename"])).name
        target = output / filename
        temporary = target.with_suffix(".tmp.png")
        data.image.convert("RGBA").save(temporary, format="PNG", optimize=True)
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
