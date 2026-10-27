"""One-off, auditable mastery grants for the BUG-131 recovery.

This is an operator tool, not a classroom feature.  Preview is the default and
opens the course database with SQLite ``mode=ro``; it does not instantiate
CourseStore (whose constructor may migrate the database).  A confirmed run
backs up the account's course.sqlite3 before opening CourseStore and writes
only through its public mastery/report APIs.

Examples (close MentorFlow before a confirmed run):
  python scripts/grant_mastery204.py --data-root COPY --account-id ACCOUNT --plan-id PLAN --module-id MODULE --question-id Q1 --question-id Q2
  python scripts/grant_mastery204.py --data-root COPY --account-id ACCOUNT --plan-id PLAN --module-id MODULE --question-id Q1 --confirm
  python scripts/grant_mastery204.py --data-root COPY --account-id ACCOUNT --plan-id PLAN --final-check-passed --confirm
  python scripts/grant_mastery204.py --data-root COPY --account-id ACCOUNT --plan-id PLAN --keep-question-id QID

The plan/module/question IDs are explicit operator choices.  In particular,
this tool never auto-selects or silently grants the Module 8 question.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.preferences.storage import account_directory
from refactor.learning.course.mastery import MasteryBook
from refactor.learning.course.question_bank import assessment_questions


def _db_path(data_root: Path, account_id: str) -> tuple[Path, Path]:
    account = account_directory(account_id, data_root)
    db = account / "course.sqlite3"
    for path in (account, db):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("课程库路径不能是链接。")
    if not db.is_file():
        raise ValueError(f"找不到课程库：{db}")
    return account, db.resolve()


def _readonly(path: Path):
    # With the app closed and no WAL payload, immutable mode guarantees SQLite
    # will not create even empty -wal/-shm sidecars during a preview. A live
    # WAL must be read in normal read-only mode so committed frames are visible.
    wal = Path(str(path) + "-wal")
    immutable = not wal.exists() or wal.stat().st_size == 0
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    return closing(sqlite3.connect(path.as_uri() + suffix, uri=True))


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _payloads(db: sqlite3.Connection, table: str, plan_id: str) -> list[dict]:
    if table not in _tables(db):
        return []
    return [json.loads(row[0]) for row in db.execute(
        f"SELECT payload FROM {table} WHERE plan_key=? ORDER BY id", (plan_id,))]


def _readonly_mastery(db: sqlite3.Connection, plan_id: str) -> MasteryBook:
    """Build the shared pure mastery projection from a read-only DB snapshot."""
    modules = _payloads(db, "modules", plan_id)
    points = _payloads(db, "points", plan_id)
    bank = _payloads(db, "question_bank", plan_id)
    answers = _payloads(db, "question_answers", plan_id)
    event_rows = _payloads(db, "judgements", plan_id) + _payloads(db, "attempt_closed", plan_id)
    credits = _payloads(db, "credits", plan_id)
    taught = {row["credit"]["unit_id"] for row in credits
              if row["id"].startswith(("learn:", "relearn:"))}
    subpoint_minutes: dict[str, int] = {}
    for row in credits:
        credit = row["credit"]
        if row["id"].startswith("subpoint:"):
            unit = credit["unit_id"]
            subpoint_minutes[unit] = subpoint_minutes.get(unit, 0) + credit["minutes"]
    for point in points:
        if (point.get("structure") == "concept_v1" and point.get("subpoints") and
                subpoint_minutes.get(point["id"], 0) >= point["minutes"]):
            taught.add(point["id"])
    return MasteryBook.from_course_records(points, modules, bank, taught, event_rows, answers)


def _grant_id(mode: str, plan_id: str, module_id: str | None, question_ids: list[str]) -> str:
    canonical = json.dumps([mode, plan_id, module_id, sorted(question_ids)], ensure_ascii=False,
                           separators=(",", ":"))
    return "manual-204-" + mode + "-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def preview(data_root: Path, account_id: str, plan_id: str, module_id: str | None,
            question_ids: list[str], final_check: bool) -> dict:
    """Read-only preflight. This intentionally does not call CourseStore."""
    account, path = _db_path(data_root, account_id)
    with _readonly(path) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("课程库完整性检查失败，未继续。")
        tables = _tables(db)
        if not {"plans", "modules", "question_bank", "judgements", "attempt_closed"} <= tables:
            raise ValueError("课程库结构不完整，无法预演。")
        plan_row = db.execute("SELECT payload FROM plans WHERE id=?", (plan_id,)).fetchone()
        if plan_row is None:
            raise ValueError(f"找不到计划：{plan_id}")
        plan = json.loads(plan_row[0])
        modules = _payloads(db, "modules", plan_id)
        module_map = {row["id"]: row for row in modules}
        questions = _payloads(db, "question_bank", plan_id)
        event_rows = _payloads(db, "judgements", plan_id)
        grant_rows = [row for row in event_rows
                      if (row.get("details") or {}).get("user_override") is True]
        grants_by_id = {(row.get("details") or {}).get("grant_id"): row for row in grant_rows}
        book = _readonly_mastery(db, plan_id)
        mode = "final" if final_check else "questions"
        grant = _grant_id(mode, plan_id, module_id, question_ids)
        result = dict(account_id=account_id, account_directory=str(account), plan_id=plan_id,
                      plan_title=plan.get("title", ""), plan_state=plan.get("state"), mode=mode,
                      grant_id=grant, already_applied=grant in grants_by_id)
        if final_check:
            questions_by_id = {row["id"]: row for row in assessment_questions(questions)}
            final_ids = sorted(questions_by_id)
            projection = (book.preview_question_override(final_ids, kind="final_check_passed")
                          if final_ids else {"questions": {}, "modules": {}})
            result["modules"] = [dict(module_id=m["id"], title=m.get("title"),
                                      state=book.module(m["id"]).state) for m in modules]
            result["final_questions_to_grant"] = [dict(question_id=qid,
                stem=questions_by_id[qid].get("stem", ""), current_passed=book.question(qid).passed,
                projected_passed=projection["questions"][qid]["passed"])
                for qid in final_ids]
            result["check_ready"] = book.check_ready()
            result["report_exists"] = any(row.get("id") == plan_id
                                           for row in _payloads(db, "reports", plan_id))
            result["will_complete_plan_and_save_report"] = book.check_ready()
            result["note"] = "确认执行时由产品掌握度 API 检查模块通过/跳过；不会替未通过模块放行。"
        else:
            if not module_id or module_id not in module_map:
                raise ValueError("指定模块不属于该计划。")
            bank = {row["id"]: row for row in questions}
            admitted = {row["id"] for row in assessment_questions(questions, module_id)}
            if any(qid not in admitted for qid in question_ids):
                raise ValueError("所选题目不在当前模块检测/检验题集中。")
            selected = []
            projection = book.preview_question_override(question_ids, kind="question_passed")
            for qid in question_ids:
                question = bank.get(qid)
                if question is None:
                    raise ValueError(f"指定题目不存在：{qid}")
                if question.get("module_id") != module_id:
                    raise ValueError(f"题目不属于指定模块：{qid}")
                selected.append(dict(question_id=qid, stem=question.get("stem", ""),
                                     current_passed=book.question(qid).passed,
                                     current_module_correct=book.question(qid).module_correct,
                                     projected_passed=projection["questions"][qid]["passed"],
                                     projected_module_correct=projection["questions"][qid]["module_correct"],
                                     existing_override=any(row["event"].get("question_id") == qid
                                         for row in grant_rows)))
            result["module"] = dict(module_id=module_id, title=module_map[module_id].get("title"),
                                     current_state=book.module(module_id).state,
                                     projected_state=projection["modules"][module_id]["state"])
            result["questions"] = selected
            result["will_preserve_existing_answers_and_credits"] = True
        return result


def preview_keep_question(data_root: Path, account_id: str, plan_id: str,
                          keep_question_id: str) -> dict:
    """Read-only preview for granting every unpassed final item except keep."""
    account, path = _db_path(data_root, account_id)
    with _readonly(path) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("课程库完整性检查失败，未继续。")
        tables = _tables(db)
        if not {"plans", "modules", "question_bank", "judgements", "attempt_closed"} <= tables:
            raise ValueError("课程库结构不完整，无法预演。")
        plan_row = db.execute("SELECT payload FROM plans WHERE id=?", (plan_id,)).fetchone()
        if plan_row is None:
            raise ValueError(f"找不到计划：{plan_id}")
        plan = json.loads(plan_row[0])
        completed = plan.get("state") == "completed"
        questions = assessment_questions(_payloads(db, "question_bank", plan_id))
        questions_by_id = {row["id"]: row for row in questions}
        keep = questions_by_id.get(keep_question_id)
        if keep is None:
            raise ValueError("保留题不属于当前最终检验题库。")
        book = _readonly_mastery(db, plan_id)
        keep_state = book.question(keep_question_id)
        stats = book.stats().get("questions", {})
        final_ids = set(questions_by_id)
        grant_id = _grant_id("keep", plan_id, None, [keep_question_id])
        matching = [row for row in _payloads(db, "judgements", plan_id)
                    if (row.get("details") or {}).get("user_override") is True and
                    (row.get("details") or {}).get("grant_id") == grant_id]
        if any((row.get("details") or {}).get("override_kind") != "final_check_passed" or
               (row.get("details") or {}).get("grant_scope") != "except_question" or
               (row.get("details") or {}).get("keep_question_id") != keep_question_id
               for row in matching):
            raise ValueError("授权记录与本次操作不匹配。")
        previously_granted = {row.get("event", {}).get("question_id") for row in matching}
        if not previously_granted <= final_ids - {keep_question_id}:
            raise ValueError("已有授权与本次保留题不一致。")
        if matching:
            changed_targets = [qid for qid in final_ids - {keep_question_id}
                               if not book.question(qid).passed]
            if changed_targets:
                raise ValueError("授权后出现了新的未通过题，或已有授权题再次变成未通过；请先核对进度，不能静默重复或扩大原授权。")
        elif completed:
            raise ValueError("计划已经完成；没有可验证的本次授权记录。")
        elif not book.check_ready():
            raise ValueError("仍有模块未通过或跳过，无法授权最终检验。")
        elif keep_state.passed:
            raise ValueError("保留题已经通过；不会撤销或降级既有成绩。")
        candidates = []
        for qid, question in questions_by_id.items():
            state = book.question(qid)
            if qid == keep_question_id or state.passed:
                continue
            candidates.append(dict(question_id=qid, stem=question.get("stem", ""),
                ever_wrong=stats.get(qid, {}).get("ever_wrong", False),
                module_correct=state.module_correct,
                projected_passed=True, already_granted=qid in previously_granted))
        keep_stats = stats.get(keep_question_id, {})
        correct_answers_required = (0 if keep_state.passed else
            max(0, 2 - keep_stats.get("streak", 0)) if keep_stats.get("ever_wrong") else 1)
        return dict(account_id=account_id, account_directory=str(account), plan_id=plan_id,
            plan_title=plan.get("title", ""), plan_state=plan.get("state"), mode="keep-question",
            grant_id=grant_id, keep_question=dict(question_id=keep_question_id,
                stem=keep.get("stem", ""), current_passed=keep_state.passed,
                module_correct=keep_state.module_correct,
                ever_wrong=keep_stats.get("ever_wrong", False),
                streak=keep_stats.get("streak", 0),
                correct_answers_required=correct_answers_required),
            final_question_count=len(final_ids), currently_unpassed_count=sum(
                not book.question(qid).passed for qid in final_ids),
            questions_to_grant=candidates, grant_count=len(candidates),
            already_applied=bool(matching),
            no_write_needed=not candidates,
            check_ready=book.check_ready(), will_complete_plan=False, will_save_report=False)


def _source_fingerprint(db: Path) -> str:
    digest = hashlib.sha256()
    for candidate in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        digest.update(candidate.name.encode("utf-8"))
        try:
            with candidate.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        except FileNotFoundError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:16]


def make_backup(account: Path, db_path: Path, grant_id: str) -> Path:
    """Create a consistent SQLite backup, without ever replacing a prior one."""
    fingerprint = _source_fingerprint(db_path)
    backup = account / f"course.sqlite3.before-{grant_id}-{fingerprint}.bak"
    if backup.exists():
        with _readonly(backup) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"已有恢复点损坏，拒绝覆盖：{backup}")
        return backup

    # Reserve the destination exclusively. A crash may leave a partial file;
    # it is never overwritten automatically and must be inspected by an owner.
    try:
        with backup.open("xb"):
            pass
    except FileExistsError:
        raise ValueError(f"恢复点已被并发创建，未覆盖：{backup}") from None
    try:
        with _readonly(db_path) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            target.commit()
        with _readonly(backup) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("自动恢复点完整性检查失败。")
    except BaseException:
        # Keep a failed partial restore point visible; do not hide/remove evidence.
        raise
    return backup


def execute(data_root: Path, account_id: str, plan_id: str, module_id: str | None,
            question_ids: list[str], final_check: bool) -> dict:
    account, db_path = _db_path(data_root, account_id)
    mode = "final" if final_check else "questions"
    grant_id = _grant_id(mode, plan_id, module_id, question_ids)
    before = preview(data_root, account_id, plan_id, module_id, question_ids, final_check)
    if final_check and not before["check_ready"]:
        raise StoreError("CHECK_NOT_READY", "仍有模块未通过或跳过，未执行最终检验授权。")
    if not final_check and before["already_applied"]:
        return dict(mode=mode, grant_id=grant_id, already_applied=True, backup=None,
                    module_id=module_id, question_ids=question_ids, newly_granted=[])
    if (final_check and before["already_applied"] and before["plan_state"] == "completed"
            and before["report_exists"]):
        return dict(mode=mode, grant_id=grant_id, already_applied=True, backup=None,
                    plan_state="completed", report_saved=True)
    # A committed question grant is immutable and complete in one CourseStore
    # transaction. Avoid creating another restore point on repeat invocations.
    backup = make_backup(account, db_path, grant_id)
    result = {"backup": str(backup) if backup else None, "grant_id": grant_id,
              "already_applied": before["already_applied"]}
    with CourseStore(account_id, data_root) as store:
        if final_check:
            store.grant_final_check_passed(plan_id, grant_id=grant_id, today=date.today())
            from refactor.learning.course.report import build_report
            report = build_report(store, plan_id, today=date.today())
            result.update(mode=mode, plan_state=store.get_plan(plan_id)["state"],
                          report_schema=report.get("schema"), report_saved=True)
        else:
            grant = store.grant_questions_as_passed(plan_id, module_id, question_ids, grant_id=grant_id)
            result.update(mode=mode, **grant)
    return result


def execute_keep_question(data_root: Path, account_id: str, plan_id: str,
                          keep_question_id: str) -> dict:
    """Back up, then grant final-check mastery except one learner-owned item."""
    account, db_path = _db_path(data_root, account_id)
    before = preview_keep_question(data_root, account_id, plan_id, keep_question_id)
    grant_id = before["grant_id"]
    if before["already_applied"] or before["no_write_needed"]:
        result = dict(before)
        result.update(backup=None, already_applied=True, report_saved=False)
        return result
    backup = make_backup(account, db_path, grant_id)
    with CourseStore(account_id, data_root) as store:
        granted = store.grant_final_check_questions_except(
            plan_id, keep_question_id, grant_id=grant_id)
        plan = store.get_plan(plan_id)
    if granted.get("already_applied"):
        return dict(mode="keep-question", grant_id=grant_id,
            keep_question_id=keep_question_id, newly_granted=[],
            already_granted=granted["already_granted"],
            preserved_passed=granted["preserved_passed"], check_ready=granted["check_ready"],
            keep_passed=granted["keep_passed"], plan_state=plan["state"],
            report_saved=False, backup=str(backup), already_applied=True)
    return dict(mode="keep-question", grant_id=grant_id,
        keep_question_id=keep_question_id, newly_granted=granted["newly_granted"],
        already_granted=granted["already_granted"],
        preserved_passed=granted["preserved_passed"], check_ready=granted["check_ready"],
        keep_passed=granted["keep_passed"], plan_state=plan["state"],
        report_saved=False, backup=str(backup), already_applied=False)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-root", required=True, type=Path,
                     help="MentorFlow data root; preview reads the account database read-only")
    cli.add_argument("--account-id", required=True)
    cli.add_argument("--plan-id", required=True)
    cli.add_argument("--module-id")
    cli.add_argument("--question-id", action="append", default=[], dest="question_ids")
    cli.add_argument("--final-check-passed", action="store_true",
                     help="select the separate final-check completion operation")
    cli.add_argument("--keep-question-id",
                     help="grant all unpassed final-check questions except this learner-owned question")
    cli.add_argument("--confirm", action="store_true",
                     help="write the selected user override after creating an account-local SQLite backup")
    return cli


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.keep_question_id:
        if args.module_id or args.question_ids or args.final_check_passed:
            parser().error("--keep-question-id cannot be combined with module/question/final-completion modes")
    elif args.final_check_passed:
        if args.module_id or args.question_ids:
            parser().error("--final-check-passed cannot be combined with module/question selection")
    elif not args.module_id or not args.question_ids:
        parser().error("question mode requires --module-id and one or more --question-id values")
    elif len(args.question_ids) != len(set(args.question_ids)):
        parser().error("question IDs must be unique")

    try:
        if args.keep_question_id:
            if not args.confirm:
                result = preview_keep_question(args.data_root, args.account_id, args.plan_id,
                                               args.keep_question_id)
                print(json.dumps({"dry_run": True, **result}, ensure_ascii=False, indent=2))
                print("预演未写数据库。确认执行前先退出 MentorFlow，再添加 --confirm。")
                return 0
            result = execute_keep_question(args.data_root, args.account_id, args.plan_id,
                                           args.keep_question_id)
            print(json.dumps({"dry_run": False, **result}, ensure_ascii=False, indent=2))
            return 0
        if not args.confirm:
            result = preview(args.data_root, args.account_id, args.plan_id, args.module_id,
                             args.question_ids, args.final_check_passed)
            print(json.dumps({"dry_run": True, **result}, ensure_ascii=False, indent=2))
            print("预演未写数据库。确认执行前先退出 MentorFlow，再添加 --confirm。")
            return 0
        result = execute(args.data_root, args.account_id, args.plan_id, args.module_id,
                         args.question_ids, args.final_check_passed)
        print(json.dumps({"dry_run": False, **result}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, sqlite3.Error, ValueError, StoreError) as exc:
        print(f"未执行：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
