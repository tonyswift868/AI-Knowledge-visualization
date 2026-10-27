"""206: concept structure sync selects one coherent parent/child snapshot."""
from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from refactor.learning.course.store import CourseStore
from tests.refactor.point_materials.test_subpoint114_questions import store as seeded_store


_GROUP = ("structure", "summary", "goal", "minutes", "chunk_ids", "subpoints")


def _put(store, row, modified_ns):
    with store.transaction():
        store._put("points", "p", row, modified_ns=modified_ns)


def _point(store):
    return store._get("points", "p", "point")


def _point_record(store):
    return next(record for record in store.pending_records(10000)
                if record.entity == "points" and record.payload["id"] == "point")


def _set_device_id(store, identifier):
    with store.transaction():
        store._db.execute(
            "UPDATE _meta SET value=? WHERE key='device_id'", (identifier,))


def _assert_parent_child_union(row):
    child_union = list(dict.fromkeys(
        chunk_id for child in row["subpoints"] for chunk_id in child["chunk_ids"]
    ))
    assert row["chunk_ids"] == child_union
    assert row["minutes"] == sum(child["minutes"] for child in row["subpoints"])


def _diverged_devices(seeded_store, tmp_path, note_stamp=100):
    base = seeded_store
    base.add_sources("p", [dict(id="added-source", title="追加资料", adopted=True,
        url="https://example.invalid/added", chunks=[dict(chunk_id="added-c",
        text="新增资料内容。", section=["新增章节"])])])

    a = CourseStore(base.account_id, tmp_path / "device-a")
    b = CourseStore(base.account_id, tmp_path / "device-b")
    records = base.pending_records(10000)
    a.apply_remote([(record, index + 1) for index, record in enumerate(records)])
    b.apply_remote([(record, index + 1) for index, record in enumerate(records)])

    # A adds source provenance to the existing concept. B only edits a note
    # and an independent title field. Fixed stamps make the expected winners
    # explicit without relying on wall-clock timing.
    added = _point(a)
    added["subpoints"][0]["chunk_ids"].append("added-c")
    added["chunk_ids"] = list(dict.fromkeys(
        cid for child in added["subpoints"] for cid in child["chunk_ids"]
    ))
    added["subpoints"][0]["notes"].append(
        dict(text="新增资料内容。", chunk_id="added-c"))
    _put(a, added, modified_ns=200)

    noted = _point(b)
    noted["subpoints"][1]["notes"].append(
        dict(text="另一设备的讲义补充。", chunk_id="c2"))
    _put(b, noted, modified_ns=note_stamp)
    titled = _point(b)
    titled["title"] = "另一设备改过的标题"
    _put(b, titled, modified_ns=400)
    return a, b


@pytest.mark.parametrize(("note_stamp", "winner"), [(100, "source"), (300, "note")])
def test_206_concurrent_source_and_note_edits_keep_a_coherent_structure(
        seeded_store, tmp_path, note_stamp, winner):
    a, b = _diverged_devices(seeded_store, tmp_path, note_stamp=note_stamp)
    try:
        record_a = _point_record(a)
        record_b = _point_record(b)

        # Exercise both delivery directions. The later structure group wins
        # as a whole; the independently versioned title still uses field LWW.
        a.apply_remote([(record_b, 1000)])
        b.apply_remote([(record_a, 1000)])

        for device in (a, b):
            row = _point(device)
            _assert_parent_child_union(row)
            if winner == "source":
                assert "added-c" in row["chunk_ids"]
            else:
                assert "added-c" not in row["chunk_ids"]
                assert any(note.get("text") == "另一设备的讲义补充。"
                           for note in row["subpoints"][1]["notes"])
            assert row["title"] == "另一设备改过的标题"
            sources = {source["id"] for source in device.records("sources", "p")}
            assert {"src", "added-source"} <= sources
            chunks = {chunk["id"] for chunk in device.records("source_chunks", "p")}
            assert "added-c" in chunks

        winner_a, winner_b = _point(a), _point(b)
        for field in _GROUP:
            assert winner_a[field] == winner_b[field]
        expected_record = record_a if winner == "source" else record_b
        for field in _GROUP:
            assert winner_a[field] == expected_record.payload[field]
        assert a.records("ledger_events", "p") == seeded_store.records("ledger_events", "p")
        assert b.records("judgements", "p") == seeded_store.records("judgements", "p")
    finally:
        a.close()
        b.close()


def test_206_structure_group_equal_stamp_uses_stable_json_tie(seeded_store, tmp_path):
    a, b = _diverged_devices(seeded_store, tmp_path)
    try:
        row_a, row_b = _point(a), _point(b)
        # Keep two valid but different complete structures at the same group
        # stamp. The stable canonical-JSON tiebreak must converge either way.
        row_a["subpoints"][0]["title"] = "设备A子点"
        row_a["summary"] = "设备A摘要"
        row_a["goal"] = row_a["summary"]
        row_b["subpoints"][0]["title"] = "设备B子点"
        row_b["summary"] = "设备B摘要"
        row_b["goal"] = row_b["summary"]
        # modified_ns alone is not a tie: stamps also include device_id.
        _set_device_id(a, "same-stamp-device")
        _set_device_id(b, "same-stamp-device")
        _put(a, row_a, modified_ns=500)
        _put(b, row_b, modified_ns=500)
        record_a, record_b = _point_record(a), _point_record(b)

        a.apply_remote([(record_b, 2000)])
        b.apply_remote([(record_a, 2000)])
        final_a, final_b = _point(a), _point(b)
        for field in _GROUP:
            assert final_a[field] == final_b[field]
        _assert_parent_child_union(final_a)
        _assert_parent_child_union(final_b)

        # Canonical serialization defines the same tie result independently
        # of object insertion order. Exclude metadata and out-of-group fields.
        snapshots = [
            {field: record.payload[field] for field in _GROUP}
            for record in (record_a, record_b)
        ]
        expected = max(snapshots, key=lambda value: json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        assert {field: final_a[field] for field in _GROUP} == expected
        assert deepcopy(final_a["title"]) == "另一设备改过的标题"
    finally:
        a.close()
        b.close()


def test_206_current_and_819538eb_course_stores_accept_each_others_concept_rows(
        seeded_store, tmp_path):
    repo = Path(__file__).resolve().parents[3]
    baseline_path = tmp_path / "store_baseline819538eb.py"
    baseline_source = subprocess.run(
        ["git", "show", "819538eb:refactor/learning/course/store.py"],
        cwd=repo, check=True, capture_output=True,
    ).stdout
    baseline_path.write_bytes(baseline_source)
    module_name = "refactor.learning.course._store_baseline819538eb_206"
    spec = importlib.util.spec_from_file_location(module_name, baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = baseline
    spec.loader.exec_module(baseline)

    current = CourseStore(seeded_store.account_id, tmp_path / "current")
    old = baseline.CourseStore(seeded_store.account_id, tmp_path / "baseline")
    try:
        seeded_store.start_plan("p", today=date(2026, 10, 14))
        initial = seeded_store.pending_records(10000)
        current.apply_remote([(record, index + 1) for index, record in enumerate(initial)])
        old.apply_remote([(record, index + 1) for index, record in enumerate(initial)])
        progress_before = {
            entity: old.records(entity, "p")
            for entity in ("judgements", "attempt_closed", "credits", "ledger_events")
        }

        current.add_sources("p", [dict(id="compat-source", title="兼容资料", adopted=True,
            url="https://example.invalid/compat", chunks=[dict(chunk_id="compat-c",
            text="兼容性测试新增资料。", section=["兼容章节"])])])
        current.append_material_additions(
            "p", [], [dict(point_id="point", chunk_ids=["compat-c"])], ["m"],
            today=date(2026, 10, 14),
        )

        # The pre-change implementation must accept a real current payload,
        # including its updated parent/child union.
        old.apply_remote([(record, 1000 + index) for index, record in enumerate(
            current.pending_records(10000))])
        old_point = old._get("points", "p", "point")
        _assert_parent_child_union(old_point)

        # Then make a valid old-version note-only edit and send that wire row
        # back to the current implementation.
        old_point["subpoints"][0]["notes"].append(
            dict(text="旧版本追加的讲义摘录。", chunk_id="c1"))
        with old.transaction():
            old._put("points", "p", old_point)
        old_point_record = next(record for record in old.pending_records(10000)
                                if record.entity == "points"
                                and record.payload["id"] == "point")
        current.apply_remote([(old_point_record, 2000)])
        _assert_parent_child_union(current._get("points", "p", "point"))

        for entity, expected in progress_before.items():
            assert old.records(entity, "p") == expected
            assert current.records(entity, "p") == expected
    finally:
        current.close()
        old.close()
        sys.modules.pop(module_name, None)
