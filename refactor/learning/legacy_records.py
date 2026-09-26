"""Preserve authenticated historical records without loading the old runtime.

The archive is a cache of the existing server protocol. Every source revision
is retained, while the current snapshot controls which records are displayed.
Missing original files are never reconstructed from excerpts and called whole.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3

from .file_io import _native_path, assert_no_reparse_ancestors, canonical_io_path
from .plan_models import PlanError, project_snapshot, _record
from .plan_source import validate_snapshot


PLAN_PROJECTION_VERSION = 2


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class LegacyArchive:
    def __init__(self, data_root, scope, check):
        self.scope, self.check = scope, check
        self.path = canonical_io_path(data_root) / "learning" / "accounts" / scope.account_id / "history.sqlite3"

    @contextmanager
    def connection(self):
        self.check()
        assert_no_reparse_ancestors(self.path, Path(self.path.anchor))
        _native_path(self.path.parent).mkdir(parents=True, exist_ok=True)
        if _native_path(self.path).exists() and _native_path(self.path).stat().st_nlink != 1:
            raise PlanError("HISTORY_PATH_INVALID", "历史记录文件不是独立文件。")
        db = sqlite3.connect(str(_native_path(self.path)), timeout=10)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS owner(scope TEXT NOT NULL)")
                owners = db.execute("SELECT scope FROM owner").fetchall()
                expected = encode(self.scope.snapshot_scope())
                if not owners:
                    db.execute("INSERT INTO owner VALUES (?)", (expected,))
                elif len(owners) != 1 or json.loads(owners[0][0]) != self.scope.snapshot_scope():
                    raise PlanError("HISTORY_OWNER_MISMATCH", "历史记录属于另一个账号或空间，未合并。")
                db.execute("CREATE TABLE IF NOT EXISTS versions(kind TEXT, entity_id TEXT, revision INTEGER, record_type TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,entity_id,revision))")
                db.execute("CREATE TABLE IF NOT EXISTS current_entities(kind TEXT, entity_id TEXT, revision INTEGER, PRIMARY KEY(kind,entity_id))")
                db.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                yield db
                self.check()
        finally:
            db.close()

    def restore(self, snapshot):
        validate_snapshot(snapshot, self.scope)
        catalog = project_snapshot(snapshot, self.scope)
        with self.connection() as db:
            db.execute("DELETE FROM current_entities")
            for entity in snapshot["entities"]:
                table, _ = _record(entity)
                if not table:
                    continue
                key = (entity["kind"], entity["entity_id"], entity["revision"])
                db.execute("INSERT OR IGNORE INTO versions VALUES (?,?,?,?,?)", (*key, table, encode(entity)))
                db.execute("INSERT INTO current_entities VALUES (?,?,?)", key)
            header = {k: v for k, v in snapshot.items() if k not in {"entities", "resources", "progress_provenance"}}
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('snapshot',?)", (encode(header),))
            # Plan projections are small and avoid decoding large historical
            # activation snapshots each time the UI refreshes.
            projection = asdict(catalog)
            projection["projection_version"] = PLAN_PROJECTION_VERSION
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('plans',?)", (encode(projection),))
        return catalog

    def _reproject_cached(self, db):
        """Upgrade a cached projection from its authenticated current rows."""
        snapshot_row = db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()
        if not snapshot_row:
            return None
        header = json.loads(snapshot_row[0])
        entities = [json.loads(row[0]) for row in db.execute(
            "SELECT v.body FROM versions v JOIN current_entities c "
            "ON v.kind=c.kind AND v.entity_id=c.entity_id AND v.revision=c.revision")]
        header["entities"] = entities
        catalog = project_snapshot(header, self.scope)
        projection = asdict(catalog)
        projection["projection_version"] = PLAN_PROJECTION_VERSION
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('plans',?)", (encode(projection),))
        return catalog

    def catalog(self):
        if not _native_path(self.path).exists():
            return None
        from .plan_models import CurrentPlan, PlanCatalog, PlanResource
        with self.connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='plans'").fetchone()
            data = json.loads(row[0]) if row else None
            if data is None or data.get("projection_version") != PLAN_PROJECTION_VERSION:
                upgraded = self._reproject_cached(db)
                if upgraded is not None:
                    return replace(upgraded, authority="SERVER_SNAPSHOT_CACHE")
        if data is None:
            return None
        plans = tuple(CurrentPlan(**{**p, "tasks": tuple(p["tasks"]),
            "resources": tuple(PlanResource(**r) for r in p["resources"]), "issues": tuple(p["issues"])}) for p in data["plans"])
        return PlanCatalog(self.scope, data["snapshot_revision"], data["observed_at"], plans,
                           tuple(data["notices"]), "SERVER_SNAPSHOT_CACHE")

    def records(self, record_type):
        if not _native_path(self.path).exists():
            return []
        with self.connection() as db:
            rows = db.execute("SELECT v.body FROM versions v JOIN current_entities c USING(kind,entity_id,revision) WHERE v.record_type=?", (record_type,)).fetchall()
        return [e["payload"]["product_record"]["values"] for (raw,) in rows if not (e := json.loads(raw))["deleted"]]

    def material_content(self, material_id):
        chunks = [r for r in self.records("active_chunks") if str(r.get("material_id")) == material_id]
        chunks.sort(key=lambda r: (int(r.get("chunk_index") or r.get("ordinal") or 0), str(r.get("id") or "")))
        return "\n\n".join(str(r.get("text") or r.get("content") or "") for r in chunks)

    def restore_materials(self, store):
        resources = {}
        with self.connection() as db:
            for (raw,) in db.execute("SELECT v.body FROM versions v JOIN current_entities c USING(kind,entity_id,revision) WHERE v.record_type='active_materials'"):
                entity = json.loads(raw)
                if not entity["deleted"]:
                    resources[entity["entity_id"]] = entity["payload"]
        rows = []
        for rid, payload in resources.items():
            values = payload["product_record"]["values"]
            rows.append(dict(material_id=rid, filename=payload["filename"], size_bytes=payload["size_bytes"],
                sha256=payload["sha256"], created_at=values.get("created_at") or values.get("uploaded_at") or "", text=self.material_content(rid)))
        store.restore_metadata(rows)
        return len(rows)

    def history(self, plan):
        """Return messages by their session binding, without guessing task IDs."""
        sessions = {str(r["id"]) for r in self.records("active_teaching_sessions")
                    if str(r.get("plan_id")) == plan.plan_id and str(r.get("workspace_id")) == plan.workspace_id}
        messages = [r for r in self.records("active_teaching_messages") if str(r.get("session_id")) in sessions]
        messages.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("id") or "")))
        knowledge = [r for r in self.records("active_knowledge_points") if str(r.get("workspace_id")) == plan.workspace_id]
        evidence = [r for r in self.records("active_mastery_evidence") if str(r.get("session_id")) in sessions]
        review = [r for r in self.records("active_review_items") if str(r.get("plan_id")) == plan.plan_id]
        progress = [r for r in self.records("active_knowledge_progress_snapshots") if str(r.get("workspace_id")) == plan.workspace_id]
        return dict(messages=messages, knowledge=knowledge, evidence=evidence, review=review, progress=progress)
