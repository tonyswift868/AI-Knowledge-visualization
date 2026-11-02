"""Generate the read-only data and office appendix for the project handout.

The existing project-map scripts are loaded from the pinned snapshot.  The
data-map walk is reused, with its optional SQLite/JSON metadata inspection
disabled: this appendix records directory and file metadata only.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load project-map script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def generate_annex(snapshot_root: Path, output: Path, config: dict[str, Any]) -> list[dict[str, str]]:
    """Write the user-data directory map and office status under *output*.

    Config keys: ``app_root``, ``data_root``, ``office_root`` and
    ``main_repo``.  All roots are explicit so this module is reusable outside
    MentorFlow.  No SQLite, JSON, log, or other file contents are opened.
    """
    if config.get("enabled") is False:
        return []
    snapshot_root = Path(snapshot_root)
    output = Path(output)
    project_map = snapshot_root / "scripts" / "project_map"
    data_map = _load("handout_data_map", project_map / "data_map.py")
    office_status = _load("handout_office_status", project_map / "office_status.py")

    # Reuse the project's traversal, masking, aggregation, and Markdown
    # renderer while suppressing its opt-in metadata readers.  The scan only
    # uses scandir/stat calls after this override.
    data_map._inspect_metadata_file = lambda *_args, **_kwargs: None
    data_map.MAIN_REPO = Path(config.get("main_repo", snapshot_root))
    data_map.LOCAL_ROOT = snapshot_root / ".local"  # archive has no .local; never inspect live .local
    roots = [Path(config[key]) for key in ("app_root", "data_root")]
    report = data_map.scan(roots, data_map.LOCAL_ROOT)
    report["main_head"] = str(config.get("snapshot_commit", "pinned snapshot"))
    report["privacy"] = {
        "scope": "目录、文件名过滤后的类型与大小；本附录未打开任何文件内容",
        "sqlite": "只记录文件存在、类型和大小；不连接数据库、不读取 schema 或数据行",
        "json": "只记录目录与文件元数据；不解析 JSON",
        "logs_models_binaries": "只记录过滤后的文件名、类型和大小",
        "reparse_points": "跳过，不跟随链接",
    }
    output.mkdir(parents=True, exist_ok=True)
    data_json = output / "data-directory-map.json"
    data_md = output / "data-directory-map.md"
    data_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    data_md.write_text(data_map._markdown(report), encoding="utf-8")

    office = office_status.collect(Path(config["main_repo"]), Path(config["office_root"]))
    office_json = output / "office-status.json"
    office_md = output / "office-status.md"
    office_json.write_text(json.dumps(office, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    office_md.write_text(office_status.render_markdown(office), encoding="utf-8")
    return [
        {"path": p.name, "sha256": _sha256(p)}
        for p in (data_json, data_md, office_json, office_md)
    ]


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
