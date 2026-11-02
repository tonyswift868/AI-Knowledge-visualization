"""Graphify is isolated behind a syntax-only adapter for DOC-02."""
import json
from pathlib import Path
import sys
import types

from scripts.handout.graph_adapter import extract_graph, main


def install_fake_graphify(monkeypatch, code_file: Path):
    calls = {}
    package = types.ModuleType("graphify")
    package.__path__ = []
    detect_module = types.ModuleType("graphify.detect")
    extract_module = types.ModuleType("graphify.extract")

    def detect(root, **kwargs):
        calls["detect"] = (root, kwargs)
        return {"files": {"code": [str(code_file)], "document": [str(root / "README.md")]}}

    def collect_files(path, **kwargs):
        calls["collect_files"] = (path, kwargs)
        return [path]

    def extract(paths, **kwargs):
        calls["extract"] = (paths, kwargs)
        return {
            "nodes": [{"id": "a", "source_file": code_file.name}],
            "edges": [
                {"source": "a", "target": "b", "relation": "calls", "confidence": "EXTRACTED"},
                {"source": "a", "target": "c", "relation": "calls", "confidence": "INFERRED"},
            ],
        }

    detect_module.detect = detect
    extract_module.collect_files = collect_files
    extract_module.extract = extract
    monkeypatch.setitem(sys.modules, "graphify", package)
    monkeypatch.setitem(sys.modules, "graphify.detect", detect_module)
    monkeypatch.setitem(sys.modules, "graphify.extract", extract_module)
    return calls


def test_adapter_extracts_only_detector_code_and_keeps_edge_evidence(tmp_path, monkeypatch):
    root = tmp_path / "snapshot"
    root.mkdir()
    source = root / "app.py"
    source.write_text("def run(): pass\n", encoding="utf-8")
    calls = install_fake_graphify(monkeypatch, source)

    graph = extract_graph(root, tmp_path / "work")

    assert calls["detect"][1]["gitignore"] is False
    assert calls["detect"][1]["cache_root"] == (tmp_path / "work" / "cache").resolve()
    assert calls["extract"][0] == [source.resolve()]
    assert calls["extract"][1]["root"] == root.resolve()
    assert graph["edges"][0]["confidence"] == "EXTRACTED"
    assert graph["edges"][1]["confidence"] == "INFERRED"
    assert graph["metrics"]["backend"] == "syntax-only"
    assert graph["metrics"]["model_calls"] == 0
    assert graph["metrics"]["detected_code_files"] == 1


def test_adapter_cli_writes_graph_json(tmp_path, monkeypatch):
    root = tmp_path / "snapshot"
    root.mkdir()
    source = root / "app.py"
    source.write_text("def run(): pass\n", encoding="utf-8")
    install_fake_graphify(monkeypatch, source)
    out = tmp_path / "out" / "graph.json"

    assert main(["--source", str(root), "--work", str(tmp_path / "work"), "--out", str(out)]) == 0
    graph = json.loads(out.read_text(encoding="utf-8"))
    assert graph["metrics"]["model_calls"] == 0
    assert [edge["confidence"] for edge in graph["edges"]] == ["EXTRACTED", "INFERRED"]
