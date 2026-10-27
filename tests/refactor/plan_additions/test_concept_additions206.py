from datetime import date, datetime, timedelta, timezone

import pytest

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore, StoreError


TODAY = date(2026, 10, 27)


@pytest.fixture
def store(tmp_path):
    instance = CourseStore("concept-additions206", tmp_path)
    instance.create_plan(
        "plan206", goal="理解资料", window=PlanWindow(TODAY, TODAY + timedelta(days=6), 60)
    )
    instance.add_sources("plan206", [
        dict(id="old-source", url="https://example.invalid/old", title="原资料", adopted=True, chunks=[
            dict(chunk_id="old-c", text="原资料中的概念说明。", section=["原章节"]),
        ]),
    ])
    instance.save_outline(
        "plan206", [dict(id="stage", title="阶段")],
        [dict(id="module", stage_id="stage", title="模块", prerequisites=[])],
    )
    concept = dict(
        id="concept", title="原有概念", summary="理解原有概念", minutes=15,
        chunk_ids=["old-c"], structure="concept_v1", goal="理解原有概念",
        subpoints=[
            dict(id=f"concept-s{i}", title=f"原小要点{i}", minutes=5,
                 chunk_ids=["old-c"], notes=[])
            for i in range(1, 4)
        ],
    )
    legacy = dict(id="legacy", title="旧式知识点", summary="沿用旧结构", minutes=5,
                  chunk_ids=["old-c"])
    instance.save_generated_module(
        "plan206", "module", [concept, legacy], check_minutes=5, today=TODAY
    )
    instance.start_plan("plan206", today=TODAY)
    instance.append_judgement(
        "plan206",
        Judgement("j206", 1, datetime(2026, 10, 27, tzinfo=timezone.utc),
                  "module_test", "module", "attempt206", (("concept", True),)),
    )
    instance.append_attempt_closed(
        "plan206", AttemptClosed("a206", 2, "module", "attempt206", "module_test")
    )
    instance.add_credit("plan206", "credit206", "concept", 7, TODAY, "confirmed")
    instance.add_sources("plan206", [
        dict(id="added-source", url="https://example.invalid/added", title="新增资料", adopted=True, chunks=[
            dict(chunk_id="added-c", text="新增资料补充的第一条概念说明。", section=["原小要点1"]),
            dict(chunk_id="added-d", text="新增资料补充的第二条概念说明。", section=["原小要点2"]),
        ]),
    ])
    try:
        yield instance
    finally:
        instance.close()


def test_206_append_source_to_concept_keeps_parent_child_provenance_and_existing_state(store):
    plan_before = store.get_plan("plan206")
    ledger_before = store.load_ledger("plan206").to_dict()
    judgements_before = store.records("judgements", "plan206")
    attempts_before = store.records("attempt_closed", "plan206")
    credits_before = store.records("credits", "plan206")
    modules_before = store.records("modules", "plan206")

    store.append_material_additions(
        "plan206", [], [dict(point_id="concept", chunk_ids=["added-c", "added-d"])],
        ["module"], today=TODAY,
    )

    point = next(row for row in store.records("points", "plan206") if row["id"] == "concept")
    child_union = list(dict.fromkeys(
        chunk_id for subpoint in point["subpoints"] for chunk_id in subpoint["chunk_ids"]
    ))
    placements = {
        chunk_id: next(subpoint["id"] for subpoint in point["subpoints"]
                       if chunk_id in subpoint["chunk_ids"])
        for chunk_id in ("added-c", "added-d")
    }
    assert point["chunk_ids"] == child_union == ["old-c", "added-c", "added-d"]
    assert len({subpoint_id for subpoint_id in placements.values()}) == 2
    assert len(point["subpoints"]) >= 3
    assert store.get_plan("plan206") == plan_before
    assert store.load_ledger("plan206").to_dict() == ledger_before
    assert store.records("judgements", "plan206") == judgements_before
    assert store.records("attempt_closed", "plan206") == attempts_before
    assert store.records("credits", "plan206") == credits_before
    assert store.records("modules", "plan206") == modules_before


def test_206_invalid_added_source_rolls_back_prior_supplement_write(store):
    point_before = next(row for row in store.records("points", "plan206") if row["id"] == "concept")
    records_before = store.records("points", "plan206")

    with pytest.raises(StoreError):
        store.append_material_additions(
            "plan206", [], [
                dict(point_id="concept", chunk_ids=["added-c"]),
                dict(point_id="legacy", chunk_ids=["missing-c"]),
            ], ["module"], today=TODAY,
        )

    assert next(row for row in store.records("points", "plan206") if row["id"] == "concept") == point_before
    assert store.records("points", "plan206") == records_before


def test_206_reappending_same_chunk_is_idempotent(store):
    args = ("plan206", [], [dict(point_id="concept", chunk_ids=["added-c"])], ["module"])
    store.append_material_additions(*args, today=TODAY)
    after_first = next(row for row in store.records("points", "plan206") if row["id"] == "concept")

    store.append_material_additions(*args, today=TODAY)
    after_retry = next(row for row in store.records("points", "plan206") if row["id"] == "concept")

    assert after_retry == after_first
    assert after_retry["chunk_ids"].count("added-c") == 1
    assert sum(sub["chunk_ids"].count("added-c") for sub in after_retry["subpoints"]) == 1


def test_206_legacy_point_supplement_and_new_legacy_module_remain_supported(store):
    store.append_material_additions(
        "plan206",
        [dict(id="module-new", stage_id="stage", title="新增模块", summary="新增内容",
              prerequisites=[], check_minutes=0, points=[
                  dict(id="point-new", title="新增旧式点", summary="旧式结构仍可写入",
                       minutes=10, chunk_ids=["added-c"]),
              ])],
        [dict(point_id="legacy", chunk_ids=["added-c"])],
        ["module", "module-new"], today=TODAY,
    )

    points = {row["id"]: row for row in store.records("points", "plan206")}
    assert set(points["legacy"]["chunk_ids"]) == {"old-c", "added-c"}
    assert points["point-new"]["chunk_ids"] == ["added-c"]
    assert "structure" not in points["point-new"]
    assert [row["id"] for row in sorted(store.records("modules", "plan206"),
                                        key=lambda row: row["position"])] == ["module", "module-new"]


def test_206_new_concept_module_persists_parent_child_provenance(store):
    store.append_material_additions(
        "plan206",
        [dict(id="module-concept", stage_id="stage", title="新增概念模块", summary="新增概念内容",
              prerequisites=[], check_minutes=0, points=[dict(
                  id="point-concept-new", title="新式新增点", summary="掌握新资料中的概念",
                  goal="掌握新资料中的概念", structure="concept_v1", minutes=10,
                  chunk_ids=["added-c", "added-d"], subpoints=[
                      dict(id="point-concept-new-s1", title="新资料一", minutes=5,
                           chunk_ids=["added-c"], notes=[]),
                      dict(id="point-concept-new-s2", title="新资料二", minutes=5,
                           chunk_ids=["added-d"], notes=[]),
                  ],
              )])],
        [], ["module", "module-concept"], today=TODAY,
    )

    point = next(row for row in store.records("points", "plan206") if row["id"] == "point-concept-new")
    child_union = list(dict.fromkeys(
        chunk_id for subpoint in point["subpoints"] for chunk_id in subpoint["chunk_ids"]
    ))
    assert point["structure"] == "concept_v1"
    assert point["chunk_ids"] == child_union == ["added-c", "added-d"]
