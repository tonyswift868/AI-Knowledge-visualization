"""Preview and install an upstream-approved Expedition art ZIP.

The default command only previews.  Installation and activation are separate
explicit actions so a human can inspect the report and approve every asset.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="MentorFlow Expedition art-pack installer")
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--install", action="store_true", help="install the validated preview")
    parser.add_argument("--preview-id", default="")
    parser.add_argument("--confirm", action="store_true", help="explicitly confirm installation")
    parser.add_argument("--activate", action="store_true", help="activate after installation; only fully approved versions can activate")
    args = parser.parse_args()
    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "backend"))
    from app.expedition_art_pack import ArtPackError, activate_pack, install_preview, preview_bytes

    try:
        if not args.install:
            result = preview_bytes(args.zip.read_bytes(), args.zip.name)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("valid") else 2
        if not args.preview_id:
            parser.error("--install requires --preview-id from the preview result")
        result = install_preview(args.preview_id, confirm=args.confirm)
        if args.activate:
            result = activate_pack(result["pack_id"], result["version"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ArtPackError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message, "details": exc.details}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
