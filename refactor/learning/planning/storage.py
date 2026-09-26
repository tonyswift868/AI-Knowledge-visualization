"""Local account-owned plans; no legacy writes, cloud tokens or activation gate."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from ..file_io import PlainFileCopyError, _native_path, assert_no_reparse_ancestors, canonical_io_path
from ..plan_models import CurrentPlan, PlanError, PlanResource


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def decode_plan(raw):
    try:
        data = json.loads(raw)
        data["tasks"] = tuple(data["tasks"])
        data["resources"] = tuple(PlanResource(**v) for v in data["resources"])
        data["issues"] = tuple(data["issues"])
        plan = CurrentPlan(**data)
        if (not plan.plan_id or not plan.key or not plan.tasks
                or len({t["id"] for t in plan.tasks}) != len(plan.tasks)
                or any(not t["title"] or t["status"] not in {"pending", "in_progress", "completed"} for t in plan.tasks)):
            raise ValueError()
        return plan
    except (ValueError, TypeError, KeyError):
        raise PlanError("PLAN_STORAGE_INVALID", "计划记录损坏或格式不兼容；未清空已有记录。") from None


class PlanRepository:
    def __init__(self, data_root, scope, check):
        self.scope, self.check = scope, check
        self.root = canonical_io_path(data_root) / "learning" / "accounts" / scope.account_id

    @contextmanager
    def connection(self):
        self.check()
        connection = None
        try:
            assert_no_reparse_ancestors(self.root, Path(self.root.anchor))
            _native_path(self.root).mkdir(parents=True, exist_ok=True)
            database = self.root / "plans.sqlite3"
            for path in (database, Path(str(database) + "-journal")):
                assert_no_reparse_ancestors(path, Path(path.anchor))
                if _native_path(path).exists() and _native_path(path).stat().st_nlink != 1:
                    raise PlanError("PLAN_STORAGE_INVALID", "计划记录不是独立文件，已停止读取。")
            connection = sqlite3.connect(str(_native_path(database)), timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise PlanError("PLAN_STORAGE_VERSION", "计划记录版本不兼容，请使用对应版本程序。")
            with connection:
                if version == 0:
                    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                        raise PlanError("PLAN_STORAGE_INVALID", "计划文件已有未知内容，未覆盖。")
                    for statement in (
                        "CREATE TABLE owner(account_id TEXT, issuer TEXT, subject_id TEXT)",
                        "CREATE TABLE plans(plan_key TEXT PRIMARY KEY, plan_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1)",
                        "CREATE TABLE selection(singleton INTEGER PRIMARY KEY CHECK(singleton=1), plan_key TEXT NOT NULL)",
                        "CREATE TABLE progress(plan_key TEXT, task_id TEXT, payload TEXT NOT NULL, PRIMARY KEY(plan_key,task_id))",
                        "CREATE TABLE daily_missions(mission_id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, day_date TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                        "PRAGMA user_version=1",
                    ):
                        connection.execute(statement)
                    connection.execute("INSERT INTO owner VALUES (?,?,?)", (self.scope.account_id, self.scope.issuer, self.scope.subject_id))
                owners = connection.execute("SELECT account_id,issuer,subject_id FROM owner").fetchall()
                if [tuple(r) for r in owners] != [(self.scope.account_id, self.scope.issuer, self.scope.subject_id)]:
                    raise PlanError("PLAN_OWNER_MISMATCH", "计划文件归属不匹配，已停止读取。")
                # Additive schema extension for persisted Daily Mission snapshots.
                # Existing account databases remain readable without a version bump.
                connection.execute("CREATE TABLE IF NOT EXISTS daily_missions(mission_id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, day_date TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
                # Plan revisions are an additive account-local CAS fence.  Do
                # not rewrite existing bodies here: their first read/write
                # below upgrades the embedded detail revision atomically.
                plan_columns = {row[1] for row in connection.execute("PRAGMA table_info(plans)")}
                if "revision" not in plan_columns:
                    connection.execute("ALTER TABLE plans ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
                self.check()
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                self.check()
        except PlanError:
            raise
        except (sqlite3.Error, OSError, ValueError, PlainFileCopyError) as exc:
            raise PlanError("PLAN_STORAGE_FAILED", "计划存取失败，请检查磁盘或记录文件；未报告保存成功。") from exc
        finally:
            if connection is not None:
                connection.close()

    def list(self):
        with self.connection() as c:
            return tuple(decode_plan(r[0]) for r in c.execute("SELECT body FROM plans ORDER BY rowid DESC"))

    @staticmethod
    def _body_revision(data, column_revision):
        details = data.get("details") if isinstance(data, dict) else None
        embedded = details.get("plan_revision") if isinstance(details, dict) else None
        try:
            column = int(column_revision)
        except (TypeError, ValueError):
            column = 1
        # During migration older rows may contain an embedded revision that
        # predates the SQLite revision column (or vice versa).  Use the
        # monotonic maximum so a stale snapshot can never roll the CAS fence
        # backwards on the next edit.
        embedded_value = embedded if type(embedded) is int and embedded >= 1 else 1
        return max(1, embedded_value, column)

    @classmethod
    def _with_revision(cls, plan, revision):
        data = asdict(plan)
        details = dict(data.get("details") or {})
        details["plan_revision"] = int(revision)
        data["details"] = details
        return decode_plan(encode(data))

    @classmethod
    def _check_expected_revision(cls, data, column_revision, expected_revision):
        current = cls._body_revision(data, column_revision)
        expected = expected_revision
        if expected is None:
            # Existing callers carry the revision in the plan/details.  For
            # old records without it, use the database value as a compatible
            # first write fence rather than silently accepting a stale value.
            expected = current
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            raise PlanError("PLAN_REVISION_CONFLICT", "计划版本无效，请刷新后重试。") from None
        if expected != current:
            raise PlanError("PLAN_REVISION_CONFLICT", "计划已在其他设备更新，请刷新后重试。")
        return current

    @staticmethod
    def _write_plan_revision(connection, plan):
        """Persist an immutable curriculum snapshot for structured sync."""
        details = dict(plan.details or {})
        revision = int(details.get("plan_revision") or 1)
        values = {
            "plan_key": plan.key, "plan_id": plan.plan_id, "revision": revision,
            "status": plan.status if plan.status in {"draft", "active", "archived"} else "active",
            "snapshot": json.loads(encode(asdict(plan))),
            "created_at": details.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        }
        record_id = json.dumps([plan.key, plan.plan_id], ensure_ascii=False, separators=(",", ":"))
        connection.execute(
            "INSERT OR REPLACE INTO learning_records(record_type,record_id,values_json) VALUES(?,?,?)",
            ("plan_revision", record_id, encode(values)),
        )

    def save(self, plan, *, expected_revision=None, idempotency_key=None):
        if not isinstance(plan, CurrentPlan):
            raise PlanError("PLAN_INVALID", "计划内容无效，未保存。")
        with self.connection() as c:
            existing = c.execute("SELECT body,revision FROM plans WHERE plan_key=?", (plan.key,)).fetchone()
            if existing is None:
                saved = self._with_revision(plan, 1)
                c.execute("INSERT INTO plans(plan_key,plan_id,body,revision) VALUES (?,?,?,?)",
                          (saved.key, saved.plan_id, encode(asdict(saved)), 1))
            else:
                current_data = json.loads(existing[0])
                current = self._check_expected_revision(current_data, existing[1], expected_revision)
                saved = self._with_revision(plan, current + 1)
                c.execute("UPDATE plans SET plan_id=?,body=?,revision=? WHERE plan_key=?",
                          (saved.plan_id, encode(asdict(saved)), current + 1, saved.key))
            c.execute("INSERT OR REPLACE INTO selection VALUES (1,?)", (plan.key,))
            # LocalSyncAdapter creates this table lazily on old databases.
            c.execute("CREATE TABLE IF NOT EXISTS learning_records(record_type TEXT NOT NULL, record_id TEXT NOT NULL, values_json TEXT NOT NULL, PRIMARY KEY(record_type,record_id))")
            self._write_plan_revision(c, saved)
            return saved

    def selected(self):
        with self.connection() as c:
            row = c.execute("SELECT plan_key FROM selection WHERE singleton=1").fetchone()
            return row[0] if row else None

    def select(self, key):
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO selection VALUES (1,?)", (key,))

    def rename(self, key, title, *, expected_revision=None):
        """Rename a plan with an account-local optimistic-concurrency fence."""
        with self.connection() as c:
            row = c.execute("SELECT body,revision FROM plans WHERE plan_key=?", (key,)).fetchone()
            if row is None:
                raise PlanError("PLAN_NOT_FOUND", "本机计划不存在或已被删除。")
            data = json.loads(row[0])
            if isinstance(data.get("details"), dict) and data["details"].get("_sync_tombstone"):
                raise PlanError("PLAN_DELETED", "计划已删除，不能继续编辑。")
            current = self._check_expected_revision(data, row[1], expected_revision)
            data["title"] = title
            updated = self._with_revision(decode_plan(encode(data)), current + 1)
            c.execute("UPDATE plans SET body=?,revision=? WHERE plan_key=?", (encode(asdict(updated)), current + 1, key))
            c.execute("CREATE TABLE IF NOT EXISTS learning_records(record_type TEXT NOT NULL, record_id TEXT NOT NULL, values_json TEXT NOT NULL, PRIMARY KEY(record_type,record_id))")
            self._write_plan_revision(c, updated)
            return updated

    def tombstone(self, key, *, expected_revision=None):
        """Keep a syncable account-scoped tombstone in the plan header."""
        with self.connection() as c:
            row = c.execute("SELECT body,revision FROM plans WHERE plan_key=?", (key,)).fetchone()
            if row is None:
                raise PlanError("PLAN_NOT_FOUND", "本机计划不存在或已被删除。")
            data = json.loads(row[0])
            details = dict(data.get("details") or {})
            if details.get("_sync_tombstone"):
                return decode_plan(row[0])
            current = self._check_expected_revision(data, row[1], expected_revision)
            details["_sync_tombstone"] = {
                "schema": "mentorflow.plan-tombstone.v1",
                "deleted_at": datetime.now(timezone.utc).isoformat(),
            }
            data["details"] = details
            updated = self._with_revision(decode_plan(encode(data)), current + 1)
            c.execute("UPDATE plans SET body=?,revision=? WHERE plan_key=?", (encode(asdict(updated)), current + 1, key))
            c.execute("CREATE TABLE IF NOT EXISTS learning_records(record_type TEXT NOT NULL, record_id TEXT NOT NULL, values_json TEXT NOT NULL, PRIMARY KEY(record_type,record_id))")
            self._write_plan_revision(c, updated)
            selected = c.execute("SELECT plan_key FROM selection WHERE singleton=1").fetchone()
            if selected and selected[0] == key:
                replacement = None
                for candidate in c.execute("SELECT plan_key,body FROM plans WHERE plan_key<>? ORDER BY rowid DESC", (key,)):
                    candidate_data = json.loads(candidate[1])
                    candidate_details = candidate_data.get("details") or {}
                    if not candidate_details.get("_sync_tombstone"):
                        replacement = candidate[0]
                        break
                if replacement:
                    c.execute("UPDATE selection SET plan_key=? WHERE singleton=1", (replacement,))
                else:
                    c.execute("DELETE FROM selection WHERE singleton=1")
            return updated

    def progress(self, key):
        with self.connection() as c:
            return {r[0]: json.loads(r[1]) for r in c.execute("SELECT task_id,payload FROM progress WHERE plan_key=?", (key,))}

    def daily_mission(self, plan_key, mission_id):
        with self.connection() as c:
            row = c.execute("SELECT payload,revision FROM daily_missions WHERE plan_key=? AND mission_id=?",
                            (plan_key, mission_id)).fetchone()
            return None if row is None else {"payload": json.loads(row[0]), "revision": int(row[1])}

    def save_daily_mission(self, plan_key, payload, *, expected_revision=None, idempotency_key=None):
        mission_id = str(payload.get("mission_id") or "")
        idem = str(idempotency_key or payload.get("idempotency_key") or "")
        if not mission_id or not idem:
            raise PlanError("DAILY_MISSION_INVALID", "每日任务快照缺少幂等标识。")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as c:
            row = c.execute("SELECT payload,revision,idempotency_key FROM daily_missions WHERE plan_key=? AND mission_id=?",
                            (plan_key, mission_id)).fetchone()
            if row is not None and row[2] == idem:
                return {"payload": json.loads(row[0]), "revision": int(row[1]), "replayed": True}
            current = int(row[1]) if row else 0
            if expected_revision is not None and int(expected_revision) != current:
                raise PlanError("DAILY_MISSION_CONFLICT", "每日任务已在其他设备更新，请刷新后重试。")
            revision = current + 1
            body = dict(payload, idempotency_key=idem, updated_at=now)
            if row is None:
                c.execute("INSERT INTO daily_missions(mission_id,plan_key,day_date,payload,revision,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (mission_id, plan_key, body["day_date"], encode(body), revision, idem, body["started_at"], now))
            else:
                c.execute("UPDATE daily_missions SET day_date=?,payload=?,revision=?,idempotency_key=?,updated_at=? WHERE plan_key=? AND mission_id=?",
                          (body["day_date"], encode(body), revision, idem, now, plan_key, mission_id))
            return {"payload": body, "revision": revision, "replayed": False}

    def record(self, key, task, payload):
        self.record_many(key, ((task, payload),))

    def record_many(self, key, entries, *, expected=None):
        """Merge several progress rows atomically with an optional CAS fence."""
        entries = tuple(entries)
        expected = dict(expected or {})
        with self.connection() as c:
            # Check every member, including rows that the caller intends to
            # skip because they are already completed.  A concurrent sync
            # write therefore fails the whole operation before the first
            # INSERT can become visible.
            for task, expected_payload in expected.items():
                row = c.execute("SELECT payload FROM progress WHERE plan_key=? AND task_id=?", (key, task)).fetchone()
                current = json.loads(row[0]) if row else None
                if current != expected_payload:
                    raise PlanError("PROGRESS_CONFLICT", "任务进度在批量确认期间已变化，请刷新后重试。")
            for task, payload in entries:
                previous = c.execute("SELECT payload FROM progress WHERE plan_key=? AND task_id=?", (key, task)).fetchone()
                merged = {**(json.loads(previous[0]) if previous else {}), **payload}
                try:
                    encoded = encode(merged)
                except (TypeError, ValueError):
                    raise PlanError("PROGRESS_INVALID", "任务结果过大或不能保存为 JSON。") from None
                if len(encoded) > 100_000:
                    raise PlanError("PROGRESS_INVALID", "任务结果过大或不能保存为 JSON。")
                c.execute("INSERT OR REPLACE INTO progress VALUES (?,?,?)", (key, task, encoded))
