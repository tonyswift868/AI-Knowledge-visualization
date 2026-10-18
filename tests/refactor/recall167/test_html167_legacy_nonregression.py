"""Offline comparison fixture for the unchanged legacy HTML question route."""
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import subprocess

from refactor.learning.course import extract, materials, point_materials
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore


def _fixture_sources():
    root = Path(__file__).resolve().parents[3] / "docs" / "learn" / "import"
    rows = []
    for index, path in enumerate(sorted(root.glob("*.html")), 1):
        source_id = f"html167-{index:02d}"
        document = extract.extract_html(path.read_bytes(), path.name)
        chunks = []
        for position, row in enumerate(materials._document_chunks(document, source_id)):
            chunk = dict(row)
            chunk["source_id"] = source_id
            chunk["position"] = position
            chunks.append(chunk)
        rows.append(dict(id=source_id, kind="file", title=document.title, url=path.as_uri(),
                         final_url=path.as_uri(), sha256=document.sha256, adopted=True, chunks=chunks))
    return rows


def _compile_legacy(module_impl, sources, root):
    chunks = [chunk for source in sources for chunk in source["chunks"]]
    store = CourseStore("html167-legacy", root)
    today = date(2026, 10, 18)
    try:
        store.create_plan("plan", goal="MentorFlow 指南", window=PlanWindow(today, today + timedelta(days=30), 600))
        store.add_sources("plan", sources)
        store.save_outline("plan", [dict(id="stage", title="指南")],
                           [dict(id="module", stage_id="stage", title="14 份 HTML", prerequisites=[])])
        store.save_generated_module("plan", "module", [dict(
            id="legacy-point", title="全部原文", summary="按原文练习", minutes=1,
            chunk_ids=[chunk["chunk_id"] for chunk in chunks],
        )], check_minutes=0, today=today)

        def select_literal_keys(packet, cancel=None):
            from refactor.learning.course.material_questions import answer_units
            questions = []
            for candidate in packet["candidates"]:
                if candidate["candidate_type"] == "recall":
                    units = answer_units(candidate["reference"])
                    questions.append(dict(candidate_id=candidate["id"], kind="recall", stem=candidate["stem"],
                                          key_point_ids=list(range(1, min(5, len(units)) + 1))))
                else:
                    stem, answer, options = candidate["stem"], candidate["answer"], candidate["options"]
                    subtype = "choice" if options else "fill" if "_" in stem or "□" in stem else "calc"
                    questions.append(dict(candidate_id=candidate["id"], kind="objective", stem=stem,
                                          options=options, answer=answer, objective_kind=subtype))
            return dict(questions=questions)

        result = module_impl.prepare_module(
            store, "plan", "module", {"question_splitter": select_literal_keys}, lambda *args: None,
        )
        bank = store.records("question_bank", "plan")
        assert result["candidates"] == 95
        assert result["questions"] == 95
        assert result["recall"] + result["objective"] == 95
        assert len({row["id"] for row in bank}) == 95
        assert all(row["origin"] == "found" for row in bank)
        fields = ("id", "kind", "stem", "options", "answer", "reference", "key_points",
                  "source", "answer_source", "origin")
        return result, [tuple(row.get(field) for field in fields) for row in bank]
    finally:
        store.close()


def test_html167_legacy_original_question_compiler_matches_baseline_on_14_html(tmp_path):
    """Run baseline and current non-concept compilers on identical checked-in HTML."""
    sources = _fixture_sources()
    assert len(sources) == 14
    baseline_file = tmp_path / "point_materials_baseline167.py"
    baseline_bytes = subprocess.run(
        ["git", "show", "2c2c809f:refactor/learning/course/point_materials.py"],
        cwd=Path(__file__).resolve().parents[3], check=True, capture_output=True,
    ).stdout
    baseline_file.write_bytes(baseline_bytes)
    spec = importlib.util.spec_from_file_location(
        "refactor.learning.course._point_materials_baseline167", baseline_file)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)

    baseline_result, baseline_bank = _compile_legacy(baseline, sources, tmp_path / "baseline")
    current_result, current_bank = _compile_legacy(point_materials, sources, tmp_path / "current")
    assert baseline_result == current_result
    assert baseline_bank == current_bank
