from __future__ import annotations

from .plan_dialogue_batch8 import batch8_cases


def test_batch8_cases_cover_old_b_and_ambiguity_sets():
    cases = batch8_cases()
    ids = [case["id"] for case in cases]
    assert len(cases) == 30
    assert len(set(ids)) == len(ids)
    assert sum(case["id"].startswith("B-") for case in cases) == 18
    assert sum(case["area"] == "问与改" for case in cases) == 6
    assert sum(case["area"] == "行的上下文判断" for case in cases) == 3
    assert sum(case["area"] == "一句话多意图" for case in cases) == 3
    assert all("follow_up" not in case for case in cases)


def test_batch8_old_scope_cases_check_durable_scope_directly():
    cases = {case["id"]: case for case in batch8_cases()}
    assert cases["B-R01"]["expected"]["scope_include_chapter_max"] == 4
    assert cases["B-R02"]["expected"]["scope_exclude_chapter"] == 8
    assert "outline_chapters_max" not in cases["B-R01"]["expected"]
    assert "outline_excluded_chapter" not in cases["B-R02"]["expected"]
