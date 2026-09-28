"""Prepare optional, pinned voice downloads from explicitly selected local assets.

Inputs are read-only. Output is separate from the client installer. No downloads,
model training, or resource discovery outside the supplied directories occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def bundle(root, output, label, origin):
    root = Path(root).resolve(strict=True)
    members = []
    archive = Path(output) / (label + ".zip")
    # Never overwrite a prepared distribution; a new output directory identifies
    # a new candidate. Compression is intentionally fast for large CUDA binaries.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as writer:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(p in {"__pycache__", ".git", "tests", "test"} for p in relative.parts):
                continue
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
                raise ValueError("linked asset refused: " + str(path))
            if label == "engine":
                # The inference service supplies all model paths explicitly.
                # Exclude training checkpoints and unused pretrained variants.
                if relative.parts[0] not in {"runtime", "tools", "GPT_SoVITS", "LICENSE", "config.py"}:
                    continue
                if "pretrained_models" in relative.parts:
                    continue
            if label == "base" and path.name in {"installed.json", "G2PWModel.zip"}:
                continue
            name = label + "/" + relative.as_posix()
            try:
                writer.write(path, name)
                members.append({"path": name, "size": path.stat().st_size, "sha256": sha256(path)})
            except OSError as exc:
                raise OSError("Cannot read input asset: " + str(path)) from exc
    return {"name": archive.name, "urls": [origin.rstrip("/") + "/" + archive.name],
            "size": archive.stat().st_size, "sha256": sha256(archive), "members": members}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine", "base", "voice", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for source in (args.engine, args.base, args.voice):
        if args.output.resolve().is_relative_to(source.resolve()):
            raise ValueError("output overlaps a read-only input")
    rows = []
    for label in ("engine", "base", "voice"):
        row = bundle(getattr(args, label), args.output, label, args.origin)
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "members"}), flush=True)
    catalog = {"schema": "mentorflow.tts-components.v1", "assets": rows}
    (args.output / "component-catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
