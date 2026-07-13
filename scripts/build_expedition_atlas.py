"""Build optional deterministic JSON atlases for small Expedition icons.

This dependency-free V1 emits a layout manifest and keeps original files.  It
does not pack large portraits or skill cut-ins, and automatically creates
multiple atlas pages when the requested GPU-safe width is exceeded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(source: Path, output: Path, max_width: int = 2048) -> dict:
    icons = sorted(source.rglob("skill_icon.webp")) + sorted(source.rglob("skill_icon.png")) + sorted(source.rglob("*.json"))
    icons = [path for path in icons if path.name != "metadata.json"]
    cell = 256
    per_page = max(1, max_width // cell)
    pages: list[dict] = []
    for index, path in enumerate(icons):
        page_index = index // per_page
        while len(pages) <= page_index:
            pages.append({"page": len(pages), "width": max_width, "height": 0, "frames": {}})
        x = (index % per_page) * cell
        y = 0
        pages[page_index]["height"] = max(pages[page_index]["height"], cell)
        pages[page_index]["frames"][path.as_posix()] = {"x": x, "y": y, "w": cell, "h": cell}
    output.mkdir(parents=True, exist_ok=True)
    for page in pages:
        (output / f"expedition_icons_{page['page'] + 1}.json").write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {"schema": "mentorflow.expedition.atlas.v1", "source": str(source), "max_width": max_width, "pages": pages, "originals_preserved": True, "large_assets_excluded": True}
    (output / "atlas_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-width", type=int, default=2048)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.max_width), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
