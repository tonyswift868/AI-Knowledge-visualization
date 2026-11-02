"""Run Graphify's local syntax extraction for a handout snapshot.

This adapter deliberately stops before semantic/model analysis, graph clustering,
or inferred-edge enrichment. It returns Graphify's AST nodes and edges intact so
the handout generator can cite the parser's original confidence and relation data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any


def extract_graph(source_root: Path, work_dir: Path) -> dict[str, Any]:
    """Return code-only Graphify extraction for ``source_root``.

    Graphify's detector determines supported source files. ``gitignore=False``
    is intentional: the caller passes a clean, allowlisted archive snapshot and
    that snapshot, rather than its ignore rules, is the authority for scope.
    The AST cache and logs remain under ``work_dir``.
    """
    from graphify.detect import detect
    from graphify.extract import collect_files, extract

    source_root = source_root.resolve()
    work_dir = work_dir.resolve()
    cache_root = work_dir / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    detection = detect(source_root, cache_root=cache_root, gitignore=False)
    source_paths: list[Path] = []
    for raw in detection.get("files", {}).get("code", []):
        path = Path(raw)
        source_paths.extend(collect_files(path, root=source_root) if path.is_dir() else [path])
    source_paths = sorted({p.resolve() for p in source_paths}, key=lambda p: p.as_posix().casefold())

    ast = extract(source_paths, cache_root=cache_root, root=source_root, max_workers=4) if source_paths else {"nodes": [], "edges": []}
    # Keep Graphify edge/node fields untouched. In particular, callers need the
    # original relation and confidence fields to exclude inferred relationships.
    return {
        "nodes": ast.get("nodes", []),
        "edges": ast.get("edges", []),
        "metrics": {
            "graphify_version": "0.9.100",
            "backend": "syntax-only",
            "model_calls": 0,
            "detected_code_files": len(source_paths),
            "nodes": len(ast.get("nodes", [])),
            "edges": len(ast.get("edges", [])),
            "detection_categories": {
                name: len(paths) for name, paths in detection.get("files", {}).items()
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    result = extract_graph(args.source, args.work)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")
    print(json.dumps(result["metrics"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
