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
import os
import threading


def sha256(path):
    from refactor.learning.voice.download import filesystem_path
    with filesystem_path(path).open("rb") as source:
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


def engine_members(archive, metadata, work):
    """Map the pinned upstream layout to the client's engine directory."""
    from refactor.learning.voice.seven_zip import inventory, unpack
    from refactor.learning.voice.download import ArchiveMember, DownloadAsset
    if archive.stat().st_size != metadata["size"] or sha256(archive) != metadata["sha256"]:
        raise ValueError("official engine archive hash mismatch")
    rows = []
    for item in inventory(archive, work, threading.Event()):
        name = item["Path"]
        parts = Path(name).parts
        if item.get("Folder") != "-" or len(parts) < 2:
            continue
        relative = Path(*parts[1:])
        if relative.parts[0] not in {"runtime", "tools", "GPT_SoVITS", "LICENSE", "config.py", "ffmpeg.exe", "ffprobe.exe"}:
            continue
        if any(p in {"__pycache__", ".git", "tests", "test", "pretrained_models", "G2PWModel"} for p in relative.parts):
            continue
        if relative.suffix.lower() in {".pyc", ".pyo"}:
            continue
        rows.append(ArchiveMember(name, int(item["Size"]), "0" * 64, "engine/" + relative.as_posix()))
    if not any(m.install_path == "engine/runtime/python.exe" for m in rows):
        raise ValueError("unsupported official engine archive layout")
    prepared = work / "prepared"
    prepared.mkdir()
    asset = DownloadAsset("engine.7z", tuple(metadata["urls"]), metadata["size"], metadata["sha256"],
                          "engine.7z", "7z", tuple(rows))
    unpack(asset, archive, prepared, threading.Event())
    return [{"path": row.path, "install_path": row.install_path, "size": row.size,
             "sha256": sha256(prepared / row.install_path)} for row in rows]


def official_catalog(args):
    from refactor.learning.voice.tts.config import BASE_MODEL_DOWNLOADS
    from refactor.learning.voice.tts.model_manager import G2PW_MEMBERS
    from refactor.learning.voice.download import asset_urls
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    os.environ["MF_ASSET_7ZIP"] = str(args.seven_zip.resolve())
    metadata = json.loads(args.engine_metadata.read_text(encoding="utf-8"))
    rows = [{**metadata, "members": engine_members(args.engine_archive.resolve(), metadata, args.work.resolve())}]
    for model in BASE_MODEL_DOWNLOADS:
        urls = [model["url"]]
        if model.get("archive"):
            urls.append("https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/G2PWModel.zip")
        row = {"name": model["path"].replace("/", "--"), "urls": list(asset_urls(tuple(urls))),
               "size": model["size"], "sha256": model["sha256"], "archive": model.get("archive"),
               "install_path": "base/" + model["path"], "source_page": model["url"].replace("/resolve/", "/blob/")}
        if model.get("archive"):
            row["members"] = [{"path": m.path, "install_path": "base/" + m.path, "size": m.size, "sha256": m.sha256} for m in G2PW_MEMBERS]
        rows.append(row)
    voice = bundle(args.voice, args.output, "voice", args.origin)
    voice["archive"] = "zip"
    rows.append(voice)
    catalog = {"schema": "mentorflow.tts-components.v1", "assets": rows}
    target = args.output / "component-catalog.json"
    target.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"catalog": str(target), "download_bytes": sum(r["size"] for r in rows),
                      "engine_files": len(rows[0]["members"]), "voice_bytes": voice["size"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine-archive", "engine-metadata", "seven-zip", "voice", "output", "work"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    for source in (args.voice, args.engine_archive.parent):
        if args.output.resolve().is_relative_to(source.resolve()):
            raise ValueError("output overlaps a read-only input")
    official_catalog(args)


if __name__ == "__main__":
    main()
