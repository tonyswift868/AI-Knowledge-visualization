"""Contract tests for the thematic regroup proposal without model calls."""
from copy import deepcopy
import json

from tests.refactor.points158.regroup158 import build, catalog, export_module, parse


def fixture():
    snapshot = {
        "protected_ids": ["old-a"],
        "chunk_map": {
            "chunk-a": {"id": "chunk-a", "text": "先理解锁的目标。", "section": ["锁基础"]},
            "chunk-b": {"id": "chunk-b", "text": "再看 Redis 的实现。", "section": ["Redis"]},
        },
    }
    module = {
        # Match data158.py's actual snapshot contract: module_id, not id.
        "module_id": "module-a", "position": 5, "title": "分布式系统",
        "navigation": [{"id": "nav-a", "title": "资料导航", "chunk_ids": [], "reason": "资料"}],
        "points": [
            {"id": "old-a", "module_id": "module-a", "position": 20, "title": "已学旧点",
             "subpoints": [{"id": "old-a-s1", "title": "已学标题", "minutes": 8,
                            "chunk_ids": ["chunk-a"], "notes": [{"text": "先理解锁的目标。", "chunk_id": "chunk-a"}]}]},
            {"id": "old-b", "module_id": "module-a", "position": 21, "title": "未学旧点",
             "subpoints": [{"id": "old-b-s1", "title": "Zuul 是 Netfl", "minutes": 9,
                            "chunk_ids": ["chunk-b"], "notes": [{"text": "再看 Redis 的实现。", "chunk_id": "chunk-b"}]}]},
        ],
    }
    value = {
        "groups": [{"title": "分布式锁：Redis 实现", "goal": "理解锁的目标和一种实现。",
                    "reason": "实现建立在锁的用途上。", "subpoint_ids": ["S2", "S1"]}],
        "renames": [
            {"id": "S1", "title": "保护点新标题不应应用", "reason": "测试保护规则"},
            {"id": "S2", "title": "Redis 锁的实现", "reason": "旧标题截断"},
        ],
        "ordering_reason": "先概念后实现",
    }
    return snapshot, module, value


def test_model_grouping_covers_all_subpoints_exactly_once_and_keeps_order_inside_group():
    snapshot, module, value = fixture()
    aliases, rows = catalog(module, snapshot)
    parsed = parse(json.dumps(value, ensure_ascii=False), aliases)
    assert len(rows) == 2
    assert set(aliases) == {"S1", "S2"}
    exported, audit = export_module(module, snapshot, parsed)
    subs = exported["points"][0]["subpoints"]
    assert [sub["id"] for sub in subs] == ["old-a-s1", "old-b-s1"]
    assert exported["replace_point_ids"] == ["old-a", "old-b"]
    assert len({sub["id"] for point in exported["points"] for sub in point["subpoints"]}) == 2
    assert audit == [{"id": "old-b-s1", "old": "Zuul 是 Netfl", "new": "Redis 锁的实现"}]


def test_export_preserves_notes_chunks_minutes_and_changes_only_unprotected_titles():
    snapshot, module, value = fixture()
    before = {s["id"]: deepcopy(s) for p in module["points"] for s in p["subpoints"]}
    exported, _ = export_module(module, snapshot, value)
    after = {s["id"]: s for p in exported["points"] for s in p["subpoints"]}
    assert set(before) == set(after)
    for sid in before:
        old = before[sid]
        new = dict(after[sid])
        assert new.pop("title") == (old["title"] if sid == "old-a-s1" else "Redis 锁的实现")
        old.pop("title")
        assert new == old
    assert exported["points"][0]["minutes"] == 17
    assert exported["points"][0]["chunk_ids"] == ["chunk-a", "chunk-b"]


def test_grouping_size_has_no_fixed_lower_or_upper_limit():
    snapshot, module, value = fixture()
    # A single subpoint is still a valid topic when it is all the material has.
    module["points"] = [module["points"][1]]
    snapshot["protected_ids"] = []
    catalog(module, snapshot)
    one = {"groups": [{"title": "Redis 锁", "goal": "理解 Redis 锁。", "reason": "主题完整。",
                       "subpoint_ids": ["S1"]}]}
    proposal, _ = export_module(module, snapshot, one)
    assert len(proposal["points"]) == 1
    assert len(proposal["points"][0]["subpoints"]) == 1

    # The allowed input is not capped at eight: a genuinely broad theme can
    # carry many short subpoints without splitting for balance.
    wide = deepcopy(module)
    wide["points"] = [deepcopy(module["points"][0])]
    wide_subs = []
    for i in range(1, 82):
        wide_subs.append({"id": f"old-b-s{i}", "title": f"原文要点 {i}", "minutes": 1,
                          "chunk_ids": ["chunk-b"], "notes": [{"text": "再看 Redis 的实现。", "chunk_id": "chunk-b"}]})
    wide["points"][0]["subpoints"] = wide_subs
    wide_snapshot = deepcopy(snapshot)
    wide_aliases, _ = catalog(wide, wide_snapshot)
    wide_value = {"groups": [{"title": "Redis 锁完整机制", "goal": "理解全部实现细节。", "reason": "同主题前后相连。",
                              "subpoint_ids": list(reversed(list(wide_aliases)))}]}
    wide_proposal, _ = export_module(wide, wide_snapshot, wide_value)
    assert len(wide_proposal["points"]) == 1
    assert len(wide_proposal["points"][0]["subpoints"]) == 81
    assert [s["id"] for s in wide_proposal["points"][0]["subpoints"]] == [s["id"] for s in wide_subs]


def test_regroup158_complete_files_do_not_claim_human_review_passed(tmp_path):
    snapshot, module, value = fixture()
    snapshot.update(plan_id='p', modules=[])
    for position in (5, 6, 7):
        row = deepcopy(module)
        row['position'] = position
        snapshot['modules'].append(row)
        (tmp_path / f'group-{position}.json').write_text(
            json.dumps(value, ensure_ascii=False), encoding='utf-8')
    artifact = build(snapshot, tmp_path)
    assert artifact['complete'] is True
    assert artifact['quality_review']['passed'] is False
    assert '人工' in artifact['quality_review']['reason']
