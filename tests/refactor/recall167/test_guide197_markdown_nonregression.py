"""Offline compiler comparison on the current 14-chapter Markdown guide."""
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

from refactor.learning.course import extract, materials, point_materials
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from scripts.learn.export_guide import _strip_provenance


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+\S.*$")


def _expected_questions(markdown):
    lines = markdown.splitlines()
    headings = [(index, len(match.group(1)), match.group(2).strip())
                for index, line in enumerate(lines)
                if (match := _HEADING.match(line))]
    expected = {}
    for position, (start, level, stem) in enumerate(headings):
        stem = re.sub(r"[*`#]", "", stem).strip()
        if level != 3 or not stem.endswith(("?", "？")):
            continue
        end = next((next_start for next_start, next_level, _ in headings[position + 1:]
                    if next_level <= level), len(lines))
        points = [line.strip() for line in lines[start + 1:end] if _BULLET.match(line)]
        if points:
            assert stem not in expected, f"原题标题重复：{stem}"
            expected[stem] = points
    return expected


def _fixture_sources():
    root = Path(__file__).resolve().parents[3] / "docs" / "learn"
    paths = sorted((path for path in root.glob("*.md") if path.name != "README.md"), key=lambda path: path.name)
    assert len(paths) == 14, f"预期14篇Markdown课文，实际找到{len(paths)}篇"

    rows, expected = [], {}
    for index, path in enumerate(paths, 1):
        cleaned = _strip_provenance(path.read_text(encoding="utf-8"))
        for stem, points in _expected_questions(cleaned).items():
            assert stem not in expected, f"原题标题重复：{stem}"
            expected[stem] = points

        source_id = f"guide197-{index:02d}"
        document = extract.extract_bytes(cleaned.encode("utf-8"), path.name)
        chunks = []
        for position, row in enumerate(materials._document_chunks(document, source_id)):
            chunk = dict(row)
            chunk["source_id"] = source_id
            chunk["position"] = position
            chunks.append(chunk)
        file_url = path.as_uri()
        rows.append(dict(id=source_id, kind="file", title=document.title or path.stem, url=file_url,
                         final_url=file_url, sha256=document.sha256, adopted=True, chunks=chunks))

    assert len(expected) == 20, f"预期20道有参考要点的原题，实际找到{len(expected)}道"
    assert all(len(points) == 3 for points in expected.values()), "每道原题必须恰有3条参考要点"
    return rows, expected


def _compile_guide(module_impl, sources, expected, root):
    chunks = [chunk for source in sources for chunk in source["chunks"]]
    store = CourseStore("guide197-markdown", root)
    today = date(2026, 10, 18)
    try:
        store.create_plan("plan", goal="MentorFlow 指南", window=PlanWindow(today, today + timedelta(days=30), 600))
        store.add_sources("plan", sources)
        store.save_outline("plan", [dict(id="stage", title="指南")],
                           [dict(id="module", stage_id="stage", title="14篇Markdown学习资料", prerequisites=[])])
        store.save_generated_module("plan", "module", [dict(
            id="guide-point", title="全部原文", summary="按原文练习", minutes=1,
            chunk_ids=[chunk["chunk_id"] for chunk in chunks],
        )], check_minutes=0, today=today)

        # Each sibling source bullet is an independent answer idea; direct
        # recall compilation still needs no model splitter.
        result = module_impl.prepare_module(store, "plan", "module", {}, lambda *args: None)
        bank = store.records("question_bank", "plan")
        assert result["candidates"] == 20
        assert result["questions"] == 20
        assert result["recall"] == 20 and result["objective"] == 0
        assert len({row["id"] for row in bank}) == 20
        assert all(row["origin"] == "found" for row in bank)
        assert {row["stem"] for row in bank} == set(expected)
        for row in bank:
            assert row["key_points"] == expected[row["stem"]]
            assert len(row["key_points"]) == 3
            assert all(point in row["reference"] for point in row["key_points"])

        fields = ("id", "kind", "stem", "options", "answer", "reference",
                  "source", "answer_source", "origin")
        return result, [tuple(row.get(field) for field in fields) for row in bank]
    finally:
        store.close()


def test_guide197_markdown_original_question_compiler_matches_baseline_on_14_chapters(tmp_path, monkeypatch):
    """Compare historical and current compilers on the same clean Markdown sources."""
    sources, expected = _fixture_sources()
    assert len(sources) == 14
    def historical_module(name):
        baseline_file = tmp_path / f"{name}_baseline197.py"
        with baseline_file.open("wb") as output:
            subprocess.run(
                ["git", "show", f"2c2c809f:refactor/learning/course/{name}.py"],
                cwd=Path(__file__).resolve().parents[3], check=True, stdout=output,
            )
        spec = importlib.util.spec_from_file_location(
            f"refactor.learning.course._{name}_baseline197", baseline_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # The historical compiler imports cloze helpers retired in 223. Load its
    # matching question-bank snapshot, without restoring obsolete product APIs.
    # Scope runtime imports too; restore the current module before comparison.
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, "refactor.learning.course.question_bank",
                        historical_module("question_bank"))
        baseline = historical_module("point_materials")
        baseline_result, baseline_bank = _compile_guide(baseline, sources, expected, tmp_path / "baseline")
    current_result, current_bank = _compile_guide(point_materials, sources, expected, tmp_path / "current")
    # Retired cloze counters are not part of the original-question contract.
    assert baseline_result.pop("cloze_points") == 0
    assert baseline_result.pop("cloze_heavy") is False
    assert not {"cloze_points", "cloze_heavy"}.intersection(current_result)
    assert {key: current_result[key] for key in baseline_result} == baseline_result
    assert current_result["supplemental_questions"] == 0
    assert baseline_bank == current_bank
