"""Classroom-owned dialogue and notes only. PlanService owns task progress.

Each operation uses its own SQLite connection, allowing account/page disposal
without closing or sharing a database handle with another module.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .domain import (
    ErrorEpisode,
    EvaluationResult,
    EvidenceEvent,
    LearningContract,
    TutorCheckpoint,
    VerificationItem,
    error_episode_from_dict,
    evaluation_result_from_dict,
    evidence_event_from_dict,
    learning_contract_from_dict,
    to_dict,
    verification_item_from_dict,
    tutor_checkpoint_from_dict,
)
from ..plan_models import PlanError


def account_directory(data_root, account_id: str) -> Path:
    if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", account_id):
        raise ValueError("课堂账号标识无效")
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", account_id):
        raise ValueError("课堂账号标识无效")
    root = Path(data_root).resolve() / "learning" / "accounts"
    path = root / account_id
    # Do not follow a junction/symlink into a different account or external root.
    if path.resolve() != path or (path / "classroom.sqlite3").resolve() != path / "classroom.sqlite3":
        raise ValueError("课堂记录路径无效")
    return path


class ClassroomRecords:
    def __init__(self, data_root, account_id: str):
        self.directory = account_directory(data_root, account_id)
        self.path = self.directory / "classroom.sqlite3"
        self.account_id = account_id

    @contextmanager
    def connection(self):
        account_directory(self.directory.parents[2], self.account_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=3)
        try:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS turns (
                        id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, task_id TEXT NOT NULL,
                        action TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL, created_at TEXT NOT NULL,
                        decision_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS turn_scope ON turns(plan_key, task_id);
                    CREATE TABLE IF NOT EXISTS notes (
                        plan_key TEXT NOT NULL, task_id TEXT NOT NULL, text TEXT NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 1,
                        idempotency_key TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY(plan_key, task_id)
                    );
                    CREATE TABLE IF NOT EXISTS receipts (
                        id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, task_id TEXT NOT NULL,
                        payload TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS session_states (
                        plan_key TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL,
                        updated_at TEXT NOT NULL, PRIMARY KEY(plan_key, task_id)
                    );
                    CREATE TABLE IF NOT EXISTS learning_contracts (
                        contract_id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS contract_scope ON learning_contracts(plan_key);
                    CREATE TABLE IF NOT EXISTS evidence_events (
                        evidence_id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, contract_id TEXT NOT NULL,
                        payload TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS evidence_scope ON evidence_events(plan_key, contract_id);
                    CREATE TABLE IF NOT EXISTS error_episodes (
                        error_episode_id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, contract_id TEXT NOT NULL,
                        payload TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS error_scope ON error_episodes(plan_key, contract_id);
                    CREATE TABLE IF NOT EXISTS verification_items (
                        verification_id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, contract_id TEXT NOT NULL,
                        payload TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS verification_scope ON verification_items(plan_key, contract_id, status);
                    CREATE TABLE IF NOT EXISTS evaluation_results (
                        plan_key TEXT NOT NULL, contract_id TEXT NOT NULL, payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL, PRIMARY KEY(plan_key, contract_id)
                    );
                    CREATE TABLE IF NOT EXISTS tutor_checkpoints (
                        checkpoint_id TEXT PRIMARY KEY, plan_key TEXT NOT NULL, contract_id TEXT NOT NULL,
                        payload TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS checkpoint_scope ON tutor_checkpoints(plan_key, contract_id, created_at);
                """)
                columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
                if "decision_json" not in columns:
                    db.execute("ALTER TABLE turns ADD COLUMN decision_json TEXT NOT NULL DEFAULT '{}'" )
                note_columns = {row[1] for row in db.execute("PRAGMA table_info(notes)")}
                if "revision" not in note_columns:
                    db.execute("ALTER TABLE notes ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
                if "idempotency_key" not in note_columns:
                    db.execute("ALTER TABLE notes ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
                yield db
        finally:
            db.close()

    def load(self, plan_key: str, task_id: str):
        with self.connection() as db:
            db.row_factory = sqlite3.Row
            turns = [dict(row) for row in db.execute(
                "SELECT * FROM turns WHERE plan_key=? AND task_id=? ORDER BY rowid", (plan_key, task_id))]
            for turn in turns:
                try:
                    value = json.loads(turn.pop("decision_json", "{}"))
                except (TypeError, json.JSONDecodeError):
                    value = {}
                turn["decision"] = value if isinstance(value, dict) else {}
            row = db.execute("SELECT text,revision FROM notes WHERE plan_key=? AND task_id=?", (plan_key, task_id)).fetchone()
            theme = db.execute("SELECT value FROM preferences WHERE key='theme'").fetchone()
            receipts = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM receipts WHERE plan_key=? AND task_id=? ORDER BY rowid", (plan_key, task_id))]
            checkpoint_rows = db.execute(
                "SELECT payload FROM tutor_checkpoints WHERE plan_key=? "
                "ORDER BY created_at DESC, checkpoint_id DESC",
                (plan_key,),
            ).fetchall()
        checkpoint = None
        # Contracts created from a formal plan use ``contract:<plan_id>:<task>``;
        # older classroom rows used ``task:<task>``.  Match the task suffix in
        # the decoded fact so reopening a plan whose key differs from plan_id
        # still restores the latest bounded Tutor context.
        for checkpoint_row in checkpoint_rows:
            try:
                candidate = tutor_checkpoint_from_dict(json.loads(checkpoint_row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if candidate.contract_id == f"task:{task_id}" or candidate.contract_id.endswith(f":{task_id}"):
                checkpoint = candidate
                break
        return {"turns": turns, "note": row[0] if row else "", "note_revision": int(row[1]) if row else 0,
                "theme": theme[0] if theme else "sakura",
                "receipts": receipts, "checkpoint": checkpoint}

    def begin_turn(self, plan_key, task_id, action, question, decision=None):
        turn_id = uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO turns(id,plan_key,task_id,action,question,state,created_at,decision_json) VALUES(?,?,?,?,?,'pending',?,?)",
                       (turn_id, plan_key, task_id, action, question, datetime.now(timezone.utc).isoformat(),
                        json.dumps(decision or {}, ensure_ascii=False)))
        return turn_id

    def finish_turn(self, turn_id, answer, state):
        if state not in {"completed", "failed"}:
            raise ValueError("invalid turn outcome")
        with self.connection() as db:
            db.execute("UPDATE turns SET answer=?,state=? WHERE id=? AND state='pending'", (answer, state, turn_id))

    def fail_pending(self, plan_key: str, task_id: str) -> int:
        """Close requests whose worker was cancelled before its result arrived."""
        with self.connection() as db:
            cursor = db.execute(
                "UPDATE turns SET answer='',state='failed' "
                "WHERE plan_key=? AND task_id=? AND state='pending'",
                (plan_key, task_id),
            )
            return int(cursor.rowcount)

    def save_note(self, plan_key, task_id, text, *, expected_revision=None, idempotency_key=None):
        if not isinstance(plan_key, str) or not plan_key or not isinstance(task_id, str) or not task_id:
            raise PlanError("NOTE_INVALID", "笔记归属无效。")
        if not isinstance(text, str):
            raise PlanError("NOTE_INVALID", "笔记内容无效。")
        idem = str(idempotency_key or "")
        with self.connection() as db:
            row = db.execute("SELECT revision,idempotency_key FROM notes WHERE plan_key=? AND task_id=?",
                             (plan_key, task_id)).fetchone()
            current = int(row[0]) if row else 0
            if row and idem and row[1] == idem:
                return current
            expected = current if expected_revision is None else expected_revision
            try:
                expected = int(expected)
            except (TypeError, ValueError):
                raise PlanError("NOTE_REVISION_CONFLICT", "笔记版本无效，请刷新后重试。") from None
            if expected != current:
                raise PlanError("NOTE_REVISION_CONFLICT", "笔记已在其他设备更新，请刷新后重试。")
            revision = current + 1
            db.execute("INSERT OR REPLACE INTO notes(plan_key,task_id,text,revision,idempotency_key) VALUES(?,?,?,?,?)",
                       (plan_key, task_id, text, revision, idem))
            return revision

    def save_theme(self, theme):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES('theme',?)", (theme,))

    def session_state(self, plan_key: str, task_id: str) -> str:
        """Return the host-owned session state for one plan/task."""
        with self.connection() as db:
            row = db.execute("SELECT status FROM session_states WHERE plan_key=? AND task_id=?",
                             (plan_key, task_id)).fetchone()
        return str(row[0]) if row else "active"

    def set_session_state(self, plan_key: str, task_id: str, status: str) -> str:
        if status not in {"active", "paused"}:
            raise ValueError("invalid classroom session state")
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO session_states(plan_key,task_id,status,updated_at) VALUES(?,?,?,?)",
                       (plan_key, task_id, status, datetime.now(timezone.utc).isoformat()))
        return status

    def record_receipt(self, plan_key, task_id, payload):
        with self.connection() as db:
            db.execute("INSERT INTO receipts VALUES(?,?,?,?,?)", (uuid4().hex, plan_key, task_id,
                       json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    # Domain records are additive to the original classroom schema.  These
    # methods intentionally use the caller's plan_key as a second scope key;
    # a valid account directory alone must never make one plan's evidence
    # visible to another plan.
    def save_contract(self, plan_key: str, contract: LearningContract) -> None:
        if not isinstance(contract, LearningContract) or not isinstance(plan_key, str) or not plan_key:
            raise ValueError("invalid learning contract")
        payload = json.dumps(to_dict(contract), ensure_ascii=False)
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO learning_contracts(contract_id,plan_key,payload,created_at) VALUES(?,?,?,?)",
                (contract.contract_id, plan_key, payload, contract.created_at),
            )

    def load_contract(self, plan_key: str, contract_id: str) -> LearningContract | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM learning_contracts WHERE plan_key=? AND contract_id=?",
                             (plan_key, contract_id)).fetchone()
        return learning_contract_from_dict(json.loads(row[0])) if row else None

    def list_contracts(self, plan_key: str) -> list[LearningContract]:
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM learning_contracts WHERE plan_key=? ORDER BY created_at",
                              (plan_key,)).fetchall()
        return [learning_contract_from_dict(json.loads(row[0])) for row in rows]

    def record_evidence(self, plan_key: str, event: EvidenceEvent) -> None:
        if not isinstance(event, EvidenceEvent) or not isinstance(plan_key, str) or not plan_key:
            raise ValueError("invalid evidence event")
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO evidence_events(evidence_id,plan_key,contract_id,payload,created_at) VALUES(?,?,?,?,?)",
                (event.evidence_id, plan_key, event.contract_id, json.dumps(to_dict(event), ensure_ascii=False), event.created_at),
            )

    def list_evidence(self, plan_key: str, contract_id: str | None = None) -> list[EvidenceEvent]:
        query = "SELECT payload FROM evidence_events WHERE plan_key=?"
        params: tuple[object, ...] = (plan_key,)
        if contract_id is not None:
            query += " AND contract_id=?"
            params += (contract_id,)
        query += " ORDER BY created_at, evidence_id"
        with self.connection() as db:
            rows = db.execute(query, params).fetchall()
        return [evidence_event_from_dict(json.loads(row[0])) for row in rows]

    def record_error(self, plan_key: str, episode: ErrorEpisode) -> None:
        if not isinstance(episode, ErrorEpisode) or not isinstance(plan_key, str) or not plan_key:
            raise ValueError("invalid error episode")
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO error_episodes(error_episode_id,plan_key,contract_id,payload,created_at) VALUES(?,?,?,?,?)",
                (episode.error_episode_id, plan_key, episode.contract_id, json.dumps(to_dict(episode), ensure_ascii=False), episode.created_at),
            )

    def list_errors(self, plan_key: str, contract_id: str | None = None) -> list[ErrorEpisode]:
        query = "SELECT payload FROM error_episodes WHERE plan_key=?"
        params: tuple[object, ...] = (plan_key,)
        if contract_id is not None:
            query += " AND contract_id=?"
            params += (contract_id,)
        query += " ORDER BY created_at, error_episode_id"
        with self.connection() as db:
            rows = db.execute(query, params).fetchall()
        return [error_episode_from_dict(json.loads(row[0])) for row in rows]

    def upsert_verification(self, plan_key: str, item: VerificationItem) -> None:
        if not isinstance(item, VerificationItem) or not isinstance(plan_key, str) or not plan_key:
            raise ValueError("invalid verification item")
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO verification_items(verification_id,plan_key,contract_id,payload,status,created_at) VALUES(?,?,?,?,?,?)",
                (item.verification_id, plan_key, item.contract_id, json.dumps(to_dict(item), ensure_ascii=False), item.status, item.created_at),
            )

    def list_verification(self, plan_key: str, contract_id: str | None = None, *, status: str | None = None) -> list[VerificationItem]:
        query = "SELECT payload FROM verification_items WHERE plan_key=?"
        params: tuple[object, ...] = (plan_key,)
        if contract_id is not None:
            query += " AND contract_id=?"
            params += (contract_id,)
        if status is not None:
            query += " AND status=?"
            params += (status,)
        query += " ORDER BY created_at, verification_id"
        with self.connection() as db:
            rows = db.execute(query, params).fetchall()
        return [verification_item_from_dict(json.loads(row[0])) for row in rows]

    def get_verification(self, plan_key: str, verification_id: str) -> VerificationItem | None:
        """Read one queue item inside the account/plan scope."""
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM verification_items WHERE plan_key=? AND verification_id=?",
                (plan_key, verification_id),
            ).fetchone()
        return verification_item_from_dict(json.loads(row[0])) if row else None

    def start_verification(self, plan_key: str, verification_id: str) -> VerificationItem:
        """Move a queued item into an active verification attempt."""
        item = self.get_verification(plan_key, verification_id)
        if item is None:
            raise ValueError("verification item not found")
        if item.status in {"passed", "resolved"}:
            return item
        if item.status not in {"queued", "scheduled", "uncertain", "remediation_required",
                               "pending", "failed", "in_progress"}:
            raise ValueError("verification item cannot be started from its current state")
        updated = replace(item, status="verifying")
        self.upsert_verification(plan_key, updated)
        return updated

    def resolve_verification(self, plan_key: str, verification_id: str, verdict: str | EvaluationResult) -> VerificationItem:
        """Persist a host-decided verification result.

        The value is intentionally a small state transition; a model verdict
        must be parsed/validated by ``EvaluationService`` before this method
        is called.
        """
        item = self.get_verification(plan_key, verification_id)
        if item is None:
            raise ValueError("verification item not found")
        if item.status in {"passed", "resolved"}:
            return item
        if item.status != "verifying":
            raise ValueError("verification item must be verifying before resolution")
        value = verdict.verdict if isinstance(verdict, EvaluationResult) else str(verdict).upper()
        status = {"PASS": "passed", "UNCERTAIN": "uncertain", "FAIL": "remediation_required"}.get(value)
        if status is None:
            raise ValueError("unknown verification verdict")
        updated = replace(item, status=status)
        self.upsert_verification(plan_key, updated)
        return updated

    def save_evaluation(self, plan_key: str, contract_id: str, result: EvaluationResult) -> None:
        if not isinstance(result, EvaluationResult) or not isinstance(plan_key, str) or not plan_key:
            raise ValueError("invalid evaluation result")
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO evaluation_results(plan_key,contract_id,payload,updated_at) VALUES(?,?,?,?)",
                (plan_key, contract_id, json.dumps(to_dict(result), ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )

    def load_evaluation(self, plan_key: str, contract_id: str) -> EvaluationResult | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM evaluation_results WHERE plan_key=? AND contract_id=?",
                             (plan_key, contract_id)).fetchone()
        return evaluation_result_from_dict(json.loads(row[0])) if row else None

    def save_checkpoint(self, plan_key: str, checkpoint: TutorCheckpoint) -> None:
        if not isinstance(checkpoint, TutorCheckpoint) or not isinstance(plan_key, str) or not plan_key:
            raise ValueError("invalid tutor checkpoint")
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO tutor_checkpoints(checkpoint_id,plan_key,contract_id,payload,created_at) VALUES(?,?,?,?,?)",
                (checkpoint.checkpoint_id, plan_key, checkpoint.contract_id,
                 json.dumps(to_dict(checkpoint), ensure_ascii=False), checkpoint.created_at),
            )

    def list_checkpoints(self, plan_key: str, contract_id: str | None = None) -> list[TutorCheckpoint]:
        query = "SELECT payload FROM tutor_checkpoints WHERE plan_key=?"
        params: tuple[object, ...] = (plan_key,)
        if contract_id is not None:
            query += " AND contract_id=?"
            params += (contract_id,)
        query += " ORDER BY created_at, checkpoint_id"
        with self.connection() as db:
            rows = db.execute(query, params).fetchall()
        return [tutor_checkpoint_from_dict(json.loads(row[0])) for row in rows]

    def latest_checkpoint(self, plan_key: str, contract_id: str) -> TutorCheckpoint | None:
        values = self.list_checkpoints(plan_key, contract_id)
        return values[-1] if values else None
