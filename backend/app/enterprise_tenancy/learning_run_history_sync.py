"""Account-scoped, side-effect-free projection for LearningRun rehydration.

This module is deliberately independent of generic sync: it projects durable
ORM rows and applies only those rows, never replaying business commands.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
from datetime import datetime

from ..orm import LearningRunORM, LearningRunMessageORM

SUPPORTED_KINDS = frozenset({"learning_run", "history"})
_SECRET_NAMES = {"password", "token", "credential", "api_key", "private_key", "signing_key"}

def _assert_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if any(secret in str(key).lower() for secret in _SECRET_NAMES):
                raise ValueError("SECRET_FIELD_REJECTED")
            _assert_safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value: _assert_safe(child)

@dataclass(frozen=True)
class TrustedSyncScope:
    issuer: str
    subject_id: str
    organization_id: str
    workspace_id: str

def project_run(run: LearningRunORM, scope: TrustedSyncScope) -> dict[str, Any]:
    if run.workspace_id != scope.workspace_id:
        raise ValueError("LEARNING_RUN_WORKSPACE_SCOPE_MISMATCH")
    _assert_safe(run.state or {})
    return {"kind":"learning_run","entity_id":run.id,"workspace_id":run.workspace_id,
            "task_id":run.task_id,"lesson_id":run.lesson_id,"phase":run.phase,
            "status":run.status,"revision":run.revision,"state":dict(run.state or {}),
            "created_at":run.created_at.isoformat(),"updated_at":run.updated_at.isoformat(),
            "history_empty":False}

def project_history(messages: list[LearningRunMessageORM], scope: TrustedSyncScope, run_id: str) -> dict[str, Any]:
    if any(m.run_id != run_id for m in messages): raise ValueError("HISTORY_RUN_MISMATCH")
    rows=sorted(messages,key=lambda m:(m.created_at,m.id))
    for message in rows: _assert_safe(message.grounding or {})
    return {"kind":"history","entity_id":run_id,"workspace_id":scope.workspace_id,
            "run_id":run_id,"entries":[{"message_id":m.id,"run_id":m.run_id,"role":m.role,
            "phase":m.phase,"content":m.content,"grounding":dict(m.grounding or {}),
            "created_at":m.created_at.isoformat()} for m in rows],
            "history_empty":not rows}

def apply_snapshot(session, snapshot: Mapping[str, Any], scope: TrustedSyncScope) -> str:
    if snapshot.get("kind") not in SUPPORTED_KINDS or snapshot.get("workspace_id") != scope.workspace_id:
        raise ValueError("SYNC_SCOPE_OR_KIND_REJECTED")
    if snapshot["kind"] == "learning_run":
        run=session.get(LearningRunORM,str(snapshot["entity_id"]))
        if run and run.revision > int(snapshot["revision"]): return "STALE_REJECTED"
        if run and run.revision == int(snapshot["revision"]): return "DUPLICATE_IGNORED"
        if run is None: run=LearningRunORM(id=str(snapshot["entity_id"]),workspace_id=scope.workspace_id,task_id=str(snapshot.get("task_id") or ""),lesson_id=str(snapshot.get("lesson_id") or "")) ; session.add(run)
        for key in ("task_id","lesson_id","phase","status","state","revision"): setattr(run,key,snapshot.get(key))
        return "APPLIED"
    run=session.get(LearningRunORM,str(snapshot["run_id"]))
    if run is None or run.workspace_id != scope.workspace_id: raise ValueError("HISTORY_RUN_NOT_IN_SCOPE")
    for row in snapshot.get("entries",[]):
        if row.get("run_id") != run.id: raise ValueError("HISTORY_RUN_MISMATCH")
        if session.get(LearningRunMessageORM,str(row["message_id"])) is None:
            created = row["created_at"]
            if isinstance(created, str): created = datetime.fromisoformat(created)
            session.add(LearningRunMessageORM(id=str(row["message_id"]),run_id=run.id,role=str(row["role"]),phase=str(row.get("phase") or ""),content=str(row.get("content") or ""),grounding=dict(row.get("grounding") or {}),created_at=created,idempotency_key="rehydrate:"+str(row["message_id"])))
    return "EMPTY_SUPPORTED_HISTORY" if not snapshot.get("entries") else "APPLIED"

def reject_unsupported(kind: str) -> None:
    if kind not in SUPPORTED_KINDS: raise ValueError("UNSUPPORTED_HISTORY_KIND")
