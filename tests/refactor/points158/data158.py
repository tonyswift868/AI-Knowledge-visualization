"""Create a read-only SQLite backup and an input snapshot for 158-MF06."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from refactor.learning.course.store import CourseStore

ACCOUNT = "mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932"
PLAN = "029b076315034c00bbd6842a5c9c31d9"
ROOT = Path(r"D:\MentorFlow\.local\test-tmp\points158")
DATA_ROOT = ROOT / "data-snapshot"
SOURCE_DIR = Path(r"D:\MentorFlowApp\MentorFlow\Data\learning\accounts") / ACCOUNT
TARGET_DIR = DATA_ROOT / "learning" / "accounts" / ACCOUNT


def dump(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def backup_read_only(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise FileNotFoundError(src)
    if dst.exists():
        # Keep the original consistent backup on reruns; serialization may be
        # refreshed from it without touching the installed user's database.
        if dst.is_file():
            return
        raise FileExistsError(f"Snapshot target is not a file: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Source connection is explicitly URI read-only; backup API provides a
    # consistent copy even if SQLite uses WAL mode.
    with sqlite3.connect(src.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(dst) as target:
            source.backup(target)


def main() -> None:
    manifest = []
    for filename in ("course.sqlite3", "classroom.sqlite3"):
        source, target = SOURCE_DIR / filename, TARGET_DIR / filename
        if source.exists():
            backup_read_only(source, target)
            manifest.append({
                "filename": filename,
                "source": str(source),
                "copy": str(target),
                "bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            })

    if not any(row["filename"] == "course.sqlite3" for row in manifest):
        raise FileNotFoundError(SOURCE_DIR / "course.sqlite3")

    with CourseStore(ACCOUNT, DATA_ROOT) as store:
        plan = store.get_plan(PLAN)
        modules = sorted(store.records("modules", PLAN), key=lambda row: (row.get("position", 0), row["id"]))
        all_points = store.records("points", PLAN)
        all_questions = store.records("question_bank", PLAN)
        all_chunks = store.records("source_chunks", PLAN)
        credits = store.records("credits", PLAN)
        position = store.classroom_position(PLAN)
        mastery = store.load_mastery(PLAN)

    selected = [m for m in modules if int(m.get("position", -1)) >= 5]
    selected_ids = {m["id"] for m in selected}
    points_by_module: dict[str, list[dict]] = {m["id"]: [] for m in selected}
    for point in all_points:
        if point.get("module_id") in selected_ids:
            points_by_module[point["module_id"]].append(point)
    for rows in points_by_module.values():
        rows.sort(key=lambda p: (p.get("position", 0), p["id"]))

    selected_point_ids = {p["id"] for rows in points_by_module.values() for p in rows}
    questions_by_point: dict[str, list[dict]] = {pid: [] for pid in selected_point_ids}
    selected_questions = []
    for q in all_questions:
        linked = [pid for pid in q.get("point_ids", []) if pid in selected_point_ids]
        if linked:
            selected_questions.append(q)
            for pid in linked:
                questions_by_point[pid].append(q)
    for rows in questions_by_point.values():
        rows.sort(key=lambda q: (q.get("position", 0), q["id"]))

    selected_chunk_ids = {
        chunk_id
        for rows in points_by_module.values()
        for point in rows
        for chunk_id in point.get("chunk_ids", [])
    }
    for rows in points_by_module.values():
        for point in rows:
            for sub in point.get("subpoints", []) or []:
                selected_chunk_ids.update(sub.get("chunk_ids", []))
                for note in sub.get("notes", []) or []:
                    if note.get("chunk_id"):
                        selected_chunk_ids.add(note["chunk_id"])
    selected_chunks = [c for c in all_chunks if c["id"] in selected_chunk_ids]
    chunk_map = {c["id"]: c for c in selected_chunks}

    current_state = (position or {}).get("state", {})
    current_point_id = (current_state.get("concept") or {}).get("point_id") or current_state.get("point_id")
    learned_units = {
        row.get("credit", {}).get("unit_id")
        for row in credits
        if row.get("id", "").startswith(("learn:", "relearn:"))
    }
    protected: dict[str, list[str]] = {}
    for point in all_points:
        if point.get("module_id") not in selected_ids:
            continue
        module = next(m for m in selected if m["id"] == point["module_id"])
        before_cursor = module["position"] < current_state.get("module_position", module["position"])
        if module["id"] == current_state.get("module_id"):
            before_cursor = int(point.get("position", 0)) < int(current_state.get("index", 0))
        cursor_point = point.get("id") == current_point_id
        learned = point["id"] in learned_units
        reasons = []
        if learned: reasons.append("learned_credit")
        if before_cursor: reasons.append("before_cursor")
        if cursor_point: reasons.append("current_cursor")
        if reasons: protected[point["id"]] = reasons

    module_payload = []
    for module in selected:
        module_points = points_by_module[module["id"]]
        module_payload.append({
            "module_id": module["id"], "position": module.get("position"),
            "title": module.get("title"), "navigation": module.get("navigation", []),
            "points": module_points,
            "old_points": module_points,
            "regroup_points": [p for p in module_points if p["id"] not in protected],
            "questions": [q for q in selected_questions if q.get("module_id") == module["id"]],
            "stats": {
                "point_count": len(module_points),
                "subpoint_count": sum(len(p.get("subpoints", []) or []) for p in module_points),
                "minutes": sum(int(p.get("minutes", 0) or 0) for p in module_points),
                "question_count": len([q for q in selected_questions if q.get("module_id") == module["id"]]),
                "protected_point_count": sum(p["id"] in protected for p in module_points),
                "regroupable_point_count": sum(p["id"] not in protected for p in module_points),
            },
        })

    snapshot = {
        "plan_id": PLAN,
        "account_id": ACCOUNT,
        "plan": plan,
        "snapshot_manifest": manifest,
        "current_position": position,
        "current_state": current_state,
        "mastery_modules": {m["id"]: mastery.module(m["id"]).state for m in modules},
        "protection": protected,
        "protected_ids": list(protected),
        "credits": credits,
        "modules": module_payload,
        "question_bank": selected_questions,
        "chunks": selected_chunks,
        "chunk_map": chunk_map,
    }
    dump(ROOT / "snapshot.json", snapshot)
    print(json.dumps({
        "current_position": current_state,
        "manifest": manifest,
        "modules": [{"position": m["position"], "title": m["title"], **m["stats"]} for m in module_payload],
        "protected_points": len(protected),
        "snapshot_bytes": (ROOT / "snapshot.json").stat().st_size,
        "chunk_count": len(selected_chunks),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
