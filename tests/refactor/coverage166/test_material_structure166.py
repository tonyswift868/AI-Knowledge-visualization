"""Offline regression tests for coverage of imported multi-chapter materials."""
import json
from pathlib import Path

import pytest

from refactor.learning.course import planner_agents as agents


def _imported_html_sources():
    """Run the real extractor/import projection on the 14 checked-in HTML guides."""
    from refactor.learning.course import extract, materials

    root = Path(__file__).resolve().parents[3]
    files = sorted((root / "docs" / "learn" / "import").glob("*.html"))
    sources, chunks_by_source, all_chunks = [], {}, []
    for index, path in enumerate(files, 1):
        source_id = f"html-{index:02d}"
        document = extract.extract_html(path.read_bytes(), path.name)
        source_chunks = []
        for position, stored in enumerate(materials._document_chunks(document, source_id)):
            chunk = dict(stored)
            chunk["id"] = chunk.pop("chunk_id")
            chunk["source_id"] = source_id
            chunk["position"] = position
            source_chunks.append(chunk)
        sources.append(dict(id=source_id, title=document.title, kind="file"))
        chunks_by_source[source_id] = source_chunks
        all_chunks.extend(source_chunks)
    return sources, chunks_by_source, all_chunks


def _offline_knowledge_classifier(stage, system, messages):
    """Deterministic fixture: classify ambiguous teaching sections as knowledge.

    This deliberately does not pretend to reproduce model decisions. Host-side
    navigation rules still run against the actual imported HTML.
    """
    request = json.loads(messages[-1]["content"])
    if stage != "classify":
        raise AssertionError(f"unexpected model stage in offline replay: {stage}")
    return json.dumps([
        dict(id=row["id"], knowledge=[p["id"] for p in row["paragraphs"]],
             navigation=[], reason="离线回放替身：未判为导航的段落按知识内容保留。")
        for row in request["sections"]
    ], ensure_ascii=False)


def section(section_id, title, position, *, chapter=None, chunk_id=None):
    value = agents.Section(
        id=section_id,
        source_id="merged-guide",
        source_title="合并指南",
        title=title,
        chunk_ids=(chunk_id or f"chunk-{section_id}",),
        chars=80,
        first=position,
        last=position,
    )
    # Keep the baseline reproduction runnable before the chapter field lands.
    if chapter is not None:
        object.__setattr__(value, "chapter", chapter)
    return value


def reply(modules):
    return json.dumps(
        dict(summary="按原文目录学习", modules=modules, missing=[], excluded=[]),
        ensure_ascii=False,
    )


def module(key, title, *refs):
    return dict(key=key, title=title, summary="掌握原文内容", refs=list(refs))


def test_166_selected_parent_covers_every_descendant():
    sections = [
        section("root", "指南 / 第一章", 0),
        section("intro", "指南 / 第一章 / 概述", 1),
        section("details", "指南 / 第一章 / 细节", 2),
        section("example", "指南 / 第一章 / 实例", 3),
    ]

    parsed = agents.parse_material_structure(reply([module("m1", "第一章", "root")]), sections)

    assert parsed["modules"][0]["refs"] == ["root", "intro", "details", "example"]


def test_166_folded_directory_alias_expands_to_all_canonical_descendants():
    sections = []
    for chapter in range(14):
        for child in range(16):
            position = len(sections)
            sections.append(section(
                f"s{position + 1}", f"第{chapter + 1:02d}章 / 小节{child + 1:02d}", position
            ))
    folded = agents.build_sections((), {}, directory_sections=sections)
    assert len(folded) == 14

    parsed = agents.parse_material_structure(
        reply([module(f"m{i}", row.title, row.id) for i, row in enumerate(folded)]), sections
    )
    assigned = [ref for row in parsed["modules"] for ref in row["refs"]]

    assert len(assigned) == len(sections)
    assert len(set(assigned)) == len(sections)
    assert parsed["coverage"] == dict(total=224, assigned=224, excluded=0, unassigned=0, duplicates=0)


def test_166_explicit_nested_selection_owns_descendant_once():
    sections = [
        section("root", "指南 / 第一章", 0),
        section("a", "指南 / 第一章 / 概述", 1),
        section("a1", "指南 / 第一章 / 概述 / 定义", 2),
        section("b", "指南 / 第一章 / 实践", 3),
    ]

    parsed = agents.parse_material_structure(
        reply([module("parent", "第一章", "root"), module("nested", "概述", "a")]),
        sections,
    )
    by_key = {row["key"]: row["refs"] for row in parsed["modules"]}
    ownership = [ref for refs in by_key.values() for ref in refs]

    assert by_key["parent"] == ["root", "b"]
    assert by_key["nested"] == ["a", "a1"]
    assert len(ownership) == len(set(ownership)) == len(sections)


def test_166_equal_depth_competing_owners_are_rejected():
    sections = [
        section("root", "指南 / 第一章", 0),
        section("child", "指南 / 第一章 / 核心", 1),
    ]

    with pytest.raises(agents.AgentFormatError):
        agents.parse_material_structure(
            reply([module("left", "左模块", "child"), module("right", "右模块", "child")]),
            sections,
        )


def test_166_same_title_in_two_repository_chapters_stays_separate():
    sections = [
        section("intro-a", "引言", 0, chapter="第一章"),
        section("details-a", "引言 / 概述", 1, chapter="第一章"),
        section("intro-b", "引言", 2, chapter="第二章"),
        section("details-b", "引言 / 概述", 3, chapter="第二章"),
    ]

    parsed = agents.parse_material_structure(
        reply([module("a", "第一章", "intro-a"), module("b", "第二章", "intro-b")]),
        sections,
    )

    assert [row["title"] for row in parsed["modules"]] == ["第一章", "第二章"]
    assert [row["refs"] for row in parsed["modules"]] == [
        ["intro-a", "details-a"], ["intro-b", "details-b"]
    ]


def test_166_uncovered_root_without_selected_ancestor_fails_loudly():
    sections = [
        section("root", "指南 / 第一章", 0),
        section("child", "指南 / 第一章 / 核心", 1),
        section("other", "指南 / 第二章", 2),
    ]

    # Selecting the first chapter cannot silently absorb an unrelated chapter.
    with pytest.raises(agents.AgentFormatError):
        agents.parse_material_structure(reply([module("m1", "第一章", "root")]), sections)


def test_166_excluded_parent_removes_its_whole_subtree_from_coverage():
    sections = [
        section("keep", "指南 / 保留章节", 0),
        section("keep-child", "指南 / 保留章节 / 正文", 1),
        section("skip", "指南 / 排除章节", 2),
        section("skip-child", "指南 / 排除章节 / 正文", 3),
    ]
    value = dict(
        summary="按资料学习",
        modules=[module("m1", "保留章节", "keep")],
        missing=[],
        excluded=[dict(name="排除章节", reason="与目标无关", refs=["skip"])],
    )

    parsed = agents.parse_material_structure(json.dumps(value, ensure_ascii=False), sections)

    assert parsed["modules"][0]["refs"] == ["keep", "keep-child"]
    assert parsed["excluded"][0]["refs"] == ["skip", "skip-child"]
    assert parsed["coverage"] == dict(total=4, assigned=2, excluded=2, unassigned=0, duplicates=0)


def test_166_module_fully_removed_by_exclusion_is_dropped_cleanly():
    sections = [
        section("a", "指南 / 排除章节", 0),
        section("a-child", "指南 / 排除章节 / 正文", 1),
        section("b", "指南 / 保留章节", 2),
    ]
    value = dict(
        summary="按资料学习",
        modules=[module("empty-after-exclusion", "被排除的模块", "a"),
                 module("kept", "保留模块", "b")],
        missing=[],
        excluded=[dict(name="排除章节", reason="用户排除此内容", refs=["a"])],
    )

    parsed = agents.parse_material_structure(json.dumps(value, ensure_ascii=False), sections)
    assert [row["key"] for row in parsed["modules"]] == ["kept"]
    assert parsed["coverage"] == dict(total=3, assigned=1, excluded=2, unassigned=0, duplicates=0)


def test_166_empty_excluded_refs_do_not_expand_to_parent_branch():
    sections = [
        section("parent", "指南 / 保留章节", 0),
        section("child", "指南 / 保留章节 / 正文", 1),
    ]
    value = dict(
        summary="按资料学习",
        modules=[module("m1", "保留模块", "parent")],
        missing=[],
        excluded=[dict(name="空排除项", reason="没有明确小节编号", refs=[])],
    )

    parsed = agents.parse_material_structure(json.dumps(value, ensure_ascii=False), sections)

    assert parsed["modules"][0]["refs"] == ["parent", "child"]
    assert parsed["excluded"][0]["refs"] == []
    assert parsed["coverage"] == dict(total=2, assigned=2, excluded=0, unassigned=0, duplicates=0)


def test_166_normalizing_twice_is_idempotent():
    sections = [
        section("parent", "指南 / 第一章", 0),
        section("child", "指南 / 第一章 / 核心", 1),
        section("other", "指南 / 第二章", 2),
    ]
    parsed = agents.parse_material_structure(
        reply([module("m1", "第一章", "parent"), module("m2", "第二章", "other")]), sections
    )

    assert agents.normalize_material_structure(parsed, sections) == parsed


def test_166_removed_chapter_is_not_restored_by_parent_selection():
    sections = [
        section("keep", "指南 / 保留章节", 0),
        section("keep-child", "指南 / 保留章节 / 正文", 1),
        section("removed", "指南 / 移除章节", 2),
        section("removed-child", "指南 / 移除章节 / 正文", 3),
    ]
    removed = [dict(kind="chapter", source_id="merged-guide", title="指南 / 移除章节")]

    parsed = agents.parse_material_structure(
        reply([module("m1", "保留章节", "keep")]), sections, removed_content=removed
    )

    assert parsed["modules"][0]["refs"] == ["keep", "keep-child"]
    assert parsed["coverage"] == dict(total=2, assigned=2, excluded=0, unassigned=0, duplicates=0)


def test_166_merged_repository_module_splits_by_source_chapter():
    source = dict(id="merged-guide", title="合并指南", category="repository", reasons=["github_docs"])
    chunks = [
        dict(id="chunk-1", source_id=source["id"], position=0, text="概念甲正文。", section=["第一章", "概念甲"]),
        dict(id="chunk-2", source_id=source["id"], position=1, text="概念乙正文。", section=["第一章", "概念乙"]),
        dict(id="chunk-3", source_id=source["id"], position=2, text="概念丙正文。", section=["第二章", "概念丙"]),
        dict(id="chunk-4", source_id=source["id"], position=3, text="概念丁正文。", section=["第二章", "概念丁"]),
    ]
    sections = agents.build_sections(
        [source], {source["id"]: chunks}, max_per_source=20, max_chars=10000, max_depth=None
    )
    assert [item.chapter for item in sections] == ["第一章", "第一章", "第二章", "第二章"]

    parsed = agents.parse_material_structure(
        reply([module("whole-book", "全书总览", *(item.id for item in sections))]),
        sections,
    )

    assert [row["title"] for row in parsed["modules"]] == ["第一章", "第二章"]
    assert [row["refs"] for row in parsed["modules"]] == [
        ["s1.1", "s1.2"], ["s1.3", "s1.4"]
    ]
    assert len({row["key"] for row in parsed["modules"]}) == 2


def test_166_html_sections_without_chapter_metadata_keep_original_module():
    sections = [
        section("html-1", "第一部分 / 小节甲", 0),
        section("html-2", "第二部分 / 小节乙", 1),
    ]
    source = reply([module("html-module", "原来的 HTML 模块", "html-1", "html-2")])

    parsed = agents.parse_material_structure(source, sections)

    assert len(parsed["modules"]) == 1
    assert parsed["modules"][0]["key"] == "html-module"
    assert parsed["modules"][0]["title"] == "原来的 HTML 模块"
    assert parsed["modules"][0]["refs"] == ["html-1", "html-2"]
    assert parsed["modules"][0]["chapters"] == ["第一部分 / 小节甲", "第二部分 / 小节乙"]


def test_166_real_import_html_replay_preserves_module_point_and_question_counts():
    """Static regression over the repository's 14 HTML import fixtures; no model/network."""
    from refactor.learning.course import planner_agents as agents
    from refactor.learning.course import material_questions

    sources, chunks_by_source, chunks = _imported_html_sources()
    assert len(sources) == 14

    # Exercise both directory projection and parser expansion. One model module
    # selects each guide's request aliases; parse_material_structure must expand
    # those aliases and assign every canonical section exactly once before the
    # normal module renderer runs.
    sections = agents.build_sections(
        sources, chunks_by_source, max_per_source=1000, max_chars=9000, max_depth=None
    )
    aliases = agents.build_sections((), {}, directory_sections=sections)
    selected_modules = []
    for index, source in enumerate(sources, 1):
        refs = [row.id for row in aliases if row.source_id == source["id"]]
        selected_modules.append(module(f"m{index}", source["title"], *refs))
    parsed = agents.parse_material_structure(reply(selected_modules), sections)
    assert len(parsed["modules"]) == 14
    assert parsed["coverage"]["unassigned"] == 0
    section_by_id = {row.id: row for row in sections}

    modules = []
    for index, planned_module in enumerate(parsed["modules"], 1):
        source_id = section_by_id[planned_module["refs"][0]].source_id
        selected_chunk_ids = {
            chunk_id for ref in planned_module["refs"]
            for chunk_id in section_by_id[ref].chunk_ids
        }
        source_chunks = [chunk for chunk in chunks_by_source[source_id] if chunk["id"] in selected_chunk_ids]
        projected = agents.directory_plan_module(
            dict(id=f"m{index}", title=planned_module["title"]), source_chunks,
            _offline_knowledge_classifier,
        )
        modules.append(projected)

    big_points = sum(len(module["points"]) for module in modules)
    subpoints = sum(len(point["subpoints"]) for module in modules for point in module["points"])
    question_candidates = len(material_questions.candidates(chunks, sources))

    # These totals are the checked-in fixture replay under explicit offline
    # knowledge classification. Historical user-model replay was 87/95/95;
    # the two-point difference reflects ambiguous sections deliberately assigned
    # to knowledge here instead of preserving historical model classifications.
    assert len(modules) == 14
    per_module = [
        (len(module["points"]), sum(len(point["subpoints"]) for point in module["points"]))
        for module in modules
    ]
    assert per_module == [
        (9, 9), (4, 4), (6, 12), (6, 6), (4, 6), (6, 6), (5, 5),
        (5, 5), (5, 5), (5, 5), (3, 3), (5, 5), (10, 10), (16, 16),
    ]
    assert (big_points, subpoints, question_candidates) == (89, 97, 95), (
        f"sections={len(sections)}, points={big_points}, subpoints={subpoints}, "
        f"question_candidates={question_candidates}; per_module="
        f"{[(len(m['points']), sum(len(p['subpoints']) for p in m['points'])) for m in modules]}"
    )
