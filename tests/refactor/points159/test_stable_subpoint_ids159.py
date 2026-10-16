"""The regroup contract keeps Store's parent-scoped subpoint ids valid."""

from refactor.learning.course.restructure import _regroup_points
from refactor.learning.course.restructure import apply_structure
from tests.refactor.points158.test_regroup_tool158 import _proposal


def _point(point_id, subpoints):
    return {
        "id": point_id,
        "module_id": "m1",
        "position": 1,
        "title": point_id,
        "minutes": sum(s["minutes"] for s in subpoints),
        "chunk_ids": ["c1"],
        "structure": "concept_v1",
        "goal": point_id,
        "subpoints": subpoints,
    }


def test_regroup_renumbers_moved_subpoints_to_the_new_parent():
    old = [
        _point("old-a", [{"id": "old-a-s1", "title": "A", "minutes": 5, "chunk_ids": ["c1"], "notes": []}]),
        _point("old-b", [{"id": "old-b-s1", "title": "B", "minutes": 7, "chunk_ids": ["c1"], "notes": []}]),
    ]
    incoming = [_point("theme", [
        {"id": "old-b-s1", "title": "B2", "minutes": 7, "chunk_ids": ["c1"], "notes": []},
        {"id": "old-a-s1", "title": "A2", "minutes": 5, "chunk_ids": ["c1"], "notes": []},
    ])]

    rows, protected, empty, renames = _regroup_points(
        None, "plan", old, {"old-a", "old-b"}, incoming, set(), {"c1"}, [], "m1"
    )

    assert not protected and not empty
    assert [s["id"] for s in rows[0]["subpoints"]] == ["theme-s1", "theme-s2"]
    assert renames == {"old-a-s1": "theme-s1", "old-b-s1": "theme-s2"}


def test_regroup_ids_are_unique_even_when_old_ids_share_suffixes():
    old = [
        _point("old-a", [{"id": "old-a-s1", "title": "A", "minutes": 5, "chunk_ids": ["c1"], "notes": []}]),
        _point("old-b", [{"id": "old-b-s1", "title": "B", "minutes": 7, "chunk_ids": ["c1"], "notes": []}]),
    ]
    rows, _, _, renames = _regroup_points(
        None, "plan", old, {"old-a", "old-b"},
        [_point("theme-1", [{"id": "old-a-s1", "title": "A", "minutes": 5, "chunk_ids": ["c1"], "notes": []}]),
         _point("theme-2", [{"id": "old-b-s1", "title": "B", "minutes": 7, "chunk_ids": ["c1"], "notes": []}])],
        set(), {"c1"}, [], "m1"
    )
    assert [s["id"] for p in rows for s in p["subpoints"]] == ["theme-1-s1", "theme-2-s1"]
    assert len(set(renames.values())) == 2


def test_regroup_carries_terminal_preparation_markers_to_new_parent():
    old = [_point("old-a", [
        {"id": "old-a-s1", "title": "A", "minutes": 5, "chunk_ids": ["c1"], "notes": []},
    ])]
    old[0]["subpoint_preparation"] = {
        "state": "done", "fingerprint": "old", "result": {},
        "processed_subpoints": {"old-a-s1": {"subpoint_id": "old-a-s1", "status": "rejected"}},
        "rejections": [{"subpoint_id": "old-a-s1", "reason": "无合格题"}],
    }
    rows, _, _, _ = _regroup_points(
        None, "plan", old, {"old-a"},
        [_point("theme", [{"id": "old-a-s1", "title": "A", "minutes": 5, "chunk_ids": ["c1"], "notes": []}])],
        set(), {"c1"}, [], "m1"
    )
    sub = rows[0]["subpoints"][0]
    marker = rows[0]["subpoint_preparation"]
    assert sub["id"] == "theme-s1" and "old-a-s1" in sub["legacy_subpoint_ids"]
    assert marker["processed_subpoints"]["theme-s1"]["status"] == "rejected"
    assert marker["rejections"][0]["subpoint_id"] == "theme-s1"


def test_real_store_migrates_question_subpoint_id_and_parent(tmp_path):
    # Use the same seeded Store contract as the 158 acceptance fixture, but
    # exercise the real validator without its test-only adapter.
    from refactor.learning.course.store import CourseStore
    from tests.refactor.restructure114.test_restructure114_contract import seed
    from tests.refactor.points158.test_regroup_tool158 import _as_concept_points

    with CourseStore("account", tmp_path) as store:
        seed(store)
        _as_concept_points(store)
        preview = apply_structure(store, "p", _proposal(store), dry_run=True)
        assert preview["can_apply"], preview["issues"]
        apply_structure(store, "p", _proposal(store), dry_run=False)
        rows = [q for q in store.records("question_bank", "p") if q.get("subpoint_id", "").startswith("new-group-")]
        assert rows
        assert all(q["subpoint_id"].startswith("new-group-s") for q in rows)
        assert all(q["point_ids"] == ["new-group"] for q in rows)
