"""Durable, workspace-scoped planning material candidate pools.

The planning assistant is intentionally a read-only discovery surface.  This
module owns the small amount of server state needed to retain candidates over
multiple discovery turns and to apply explicit learner decisions.  Search or
Provider output is never allowed to select an item implicitly.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from sqlalchemy import select

from .orm import (
    PlanningResourceCandidateORM,
    PlanningResourcePoolORM,
    PlanningResourcePoolRequestORM,
    PlanningResourcePoolEventORM,
    SessionLocal,
)


DEFAULT_PAGE_SIZE = 3
MAX_PAGE_SIZE = 50
_CURSOR_VERSION = 1
_POOL_WRITE_LOCK = threading.RLock()
_IDEMPOTENCY_METADATA_KEY = "__planning_pool_idempotency"


class PlanningPoolError(Exception):
    """A safe, serialisable planning-pool conflict or input error."""

    def __init__(self, code: str, message: str = "planning resource pool request rejected"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = (
            415
            if code in {"video_material_disabled", "audio_material_disabled", "subtitle_material_disabled"}
            else 409
            if code.startswith("stale_") or code == "idempotency_conflict"
            else 422
        )


class StaleRevisionError(PlanningPoolError):
    def __init__(self) -> None:
        super().__init__("stale_revision", "planning resource pool revision is stale")


class StaleCursorError(PlanningPoolError):
    def __init__(self) -> None:
        super().__init__("stale_cursor", "planning resource pool cursor is stale")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_page_size(value: Any) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return max(1, min(MAX_PAGE_SIZE, value))


def _text(value: Any, limit: int = 800) -> str:
    return str(value or "").strip()[:limit]


def _video_candidate(candidate: Mapping[str, Any]) -> bool:
    """Fail closed for video rows before they can become selectable."""

    raw_kind = _text(
        candidate.get("media_kind")
        or candidate.get("kind")
        or candidate.get("content_type")
        or candidate.get("resource_type"),
        80,
    ).casefold().replace("-", "_").replace(" ", "_")
    if raw_kind in {"video", "video_collection", "playlist", "collection", "course", "series", "single_video", "audio", "subtitle", "caption"}:
        return True
    mime = _text(candidate.get("media_type"), 120).casefold()
    if mime.startswith("video/") or mime.startswith("audio/"):
        return True
    url = _text(candidate.get("url") or candidate.get("href"), 1200).casefold()
    try:
        parsed = urlparse(url)
        path = parsed.path
        query = parsed.query
        host = (parsed.hostname or "").strip(".")
    except (TypeError, ValueError):
        return False
    return bool(
        host in {
            "youtube.com", "www.youtube.com", "youtu.be", "bilibili.com",
            "www.bilibili.com", "vimeo.com", "www.vimeo.com", "youku.com",
            "www.youku.com", "douyin.com", "www.douyin.com",
        }
        or host.endswith(".bilibili.com")
        or any(token in path for token in ("/video", "/watch", "/playlist", "/series", "/collection"))
        or any(token in path for token in (".mp4", ".m3u8", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".m4a", ".srt", ".vtt"))
        or "list=" in query
    )


def _url_identity(value: Any) -> str:
    raw = _text(value, 1200)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.hostname:
            return ""
        host = parsed.hostname.casefold().strip(".")
        # Accessing ``port`` may raise ValueError for malformed values such
        # as ``https://example.test:bad``.  Let the caller return a stable,
        # serialisable PlanningPoolError rather than leaking a 500.
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme.casefold()}://{host}{port}{path}{('?' + parsed.query) if parsed.query else ''}"
    except (TypeError, ValueError) as exc:
        raise PlanningPoolError("invalid_url", "candidate URL is malformed") from exc


def stable_candidate_identity(candidate: Mapping[str, Any]) -> str:
    """Return a stable identity without using a title as the sole key.

    Exact URLs, upload/content hashes and source identifiers are preferred.
    For an otherwise anonymous search result, the complete bounded candidate
    payload (not only its display title) is hashed, preventing same-title
    resources from collapsing into one item.
    """

    # A canonical URL is authoritative.  Provider/client supplied IDs must
    # never make two different URLs share one durable identity.
    url = _url_identity(candidate.get("url") or candidate.get("href"))
    if url:
        return f"url:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:32]}"
    direct_id = candidate.get("direct_identifier")
    if isinstance(direct_id, Mapping):
        kind = _text(direct_id.get("kind"), 80)
        value = _text(direct_id.get("value"), 180)
        if kind and value:
            return f"direct:{kind}:{value.casefold()}"
    for key in ("upload_id", "file_id", "source_id", "content_hash", "sha256", "resource_id"):
        value = _text(candidate.get(key), 180)
        if value:
            return f"{key}:{value.casefold()}"
    explicit = _text(candidate.get("stable_id") or candidate.get("candidate_id") or candidate.get("identity"), 180)
    if explicit:
        return explicit
    payload = {
        key: candidate.get(key)
        for key in sorted(candidate)
        if key not in {"title", "label", "display_title", "selected", "rejected", "role", "selection_state"}
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return "anon:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _normalise_candidate(raw: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    candidate = dict(raw)
    stable_id = stable_candidate_identity(candidate)
    url = _text(candidate.get("url") or candidate.get("href"), 1200)
    candidate["id"] = _text(candidate.get("id") or stable_id, 180)
    candidate["candidate_id"] = stable_id
    candidate["stable_id"] = stable_id
    candidate["identity"] = stable_id
    if url:
        candidate["url"] = url
    candidate["title"] = _text(candidate.get("title") or candidate.get("name") or urlparse(url).hostname or "未命名资料", 240)
    candidate["snippet"] = _text(candidate.get("snippet") or candidate.get("content"), 800)
    candidate["media_kind"] = _text(candidate.get("media_kind") or candidate.get("kind") or "unknown", 80) or "unknown"
    candidate["coverage_role"] = _text(candidate.get("coverage_role") or candidate.get("role"), 80)
    candidate["direct_user_reference"] = bool(candidate.get("direct_user_reference") is True)
    if _video_candidate(candidate):
        # Keep a durable audit row, but make it impossible for ordinary page
        # or decision paths to expose/select/retain it.  The API layer adds a
        # user-facing warning; the pool owns this lower-level invariant for
        # direct service callers and legacy rows.
        candidate["planning_media_contract"] = "document-only"
        candidate["planning_blocked"] = True
    # ``retained`` is server-owned.  Only an exact user reference may be
    # retained at insertion time; ordinary discovery cards become retained
    # only after an explicit select/anchor/support decision.
    candidate["retained"] = bool(candidate["direct_user_reference"])
    candidate["planning_selection_intent"] = candidate.get("planning_selection_intent") == "selected"
    # Selection is server-owned; ordinary incoming flags are metadata only.
    # ``planning_selection_intent`` is emitted solely by the explicit
    # selected_resources compatibility contract.
    candidate.pop("selected", None)
    candidate.pop("rejected", None)
    candidate.pop("selection_state", None)
    return stable_id, candidate


def _cursor_encode(session_id: str, revision: int, offset: int) -> str:
    payload = {"v": _CURSOR_VERSION, "s": session_id, "r": int(revision), "o": int(offset), "n": secrets.token_hex(4)}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _cursor_decode(cursor: str) -> dict[str, Any]:
    try:
        padded = str(cursor).strip() + "=" * (-len(str(cursor).strip()) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(payload, dict) or int(payload.get("v")) != _CURSOR_VERSION:
            raise ValueError
        if not _text(payload.get("s"), 128):
            raise ValueError
        int(payload.get("r"))
        if int(payload.get("o")) < 0:
            raise ValueError
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, UnicodeEncodeError, binascii.Error):
        raise StaleCursorError() from None


def _request_fingerprint(operation: str, payload: Mapping[str, Any]) -> str:
    """Hash the canonical mutation request used by the idempotency ledger.

    The request table predates explicit request binding and only stores the
    response.  Keeping the binding as private metadata in that JSON column
    lets us reject reuse of a key for a different mutation without changing
    the persisted schema or public response contract.
    """

    encoded = json.dumps(
        {"operation": operation, **dict(payload)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event_payload(row: PlanningResourceCandidateORM) -> dict[str, Any]:
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    return {
        **payload,
        "id": row.candidate_id,
        "candidate_id": row.candidate_id,
        "stable_id": row.stable_id,
        "identity": row.stable_id,
        "selection_state": row.selection_state,
        "selected": row.selection_state in {"selected", "anchor", "support"},
        "rejected": row.selection_state == "rejected",
        "anchor": row.selection_state == "anchor",
        "support": row.selection_state == "support",
        "role": "anchor" if row.selection_state == "anchor" else ("support" if row.selection_state == "support" else ""),
        "retained": bool(row.retained),
    }


class PlanningResourcePool:
    """Small transactional service around the three pool tables."""

    def __init__(self, session_factory: Any = SessionLocal):
        self.session_factory = session_factory

    def _get_pool(self, db: Any, workspace_id: str, planning_session_id: str | None, *, create: bool = True) -> PlanningResourcePoolORM:
        sid = _text(planning_session_id, 128) or f"plan-{secrets.token_urlsafe(18)}"
        pool = db.scalar(
            select(PlanningResourcePoolORM).where(
                PlanningResourcePoolORM.workspace_id == _text(workspace_id, 64),
                PlanningResourcePoolORM.planning_session_id == sid,
            )
        )
        if pool is None and create:
            pool = PlanningResourcePoolORM(
                id=secrets.token_hex(16), workspace_id=_text(workspace_id, 64),
                planning_session_id=sid, revision=0, page_size=DEFAULT_PAGE_SIZE,
                selected_count=0, anchor_candidate_id="", created_at=_now(), updated_at=_now(),
            )
            db.add(pool)
            db.flush()
        if pool is None:
            raise PlanningPoolError("session_not_found", "planning session was not found")
        return pool

    def _pool_for_request(self, db: Any, workspace_id: str, client_request_id: Any) -> PlanningResourcePoolORM | None:
        key = _text(client_request_id, 160)
        if not key:
            return None
        return db.scalar(
            select(PlanningResourcePoolORM)
            .join(PlanningResourcePoolRequestORM, PlanningResourcePoolRequestORM.pool_id == PlanningResourcePoolORM.id)
            .where(
                PlanningResourcePoolORM.workspace_id == _text(workspace_id, 64),
                PlanningResourcePoolRequestORM.client_request_id == key,
            )
        )

    def _check_revision(self, pool: PlanningResourcePoolORM, expected_revision: Any) -> None:
        if expected_revision is None:
            return
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError, OverflowError):
            raise PlanningPoolError("invalid_revision", "revision must be an integer") from None
        if expected != int(pool.revision):
            raise StaleRevisionError()

    def _idempotent(
        self,
        db: Any,
        pool: PlanningResourcePoolORM,
        client_request_id: Any,
        request_fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        key = _text(client_request_id, 160)
        if not key:
            return None
        row = db.scalar(select(PlanningResourcePoolRequestORM).where(
            PlanningResourcePoolRequestORM.pool_id == pool.id,
            PlanningResourcePoolRequestORM.client_request_id == key,
        ))
        if row is None:
            return None
        if not isinstance(row.response_json, dict):
            raise PlanningPoolError("idempotency_conflict")
        response = dict(row.response_json)
        metadata = response.pop(_IDEMPOTENCY_METADATA_KEY, None)
        # Rows written before request binding was introduced have no metadata;
        # preserve their replay behavior for backwards compatibility.  New
        # rows must bind a key to the complete canonical mutation request.
        if request_fingerprint and metadata is not None:
            if not isinstance(metadata, dict) or metadata.get("fingerprint") != request_fingerprint:
                raise PlanningPoolError("idempotency_conflict")
        return response

    def _save_idempotent(
        self,
        db: Any,
        pool: PlanningResourcePoolORM,
        client_request_id: Any,
        response: dict[str, Any],
        request_fingerprint: str | None = None,
    ) -> None:
        key = _text(client_request_id, 160)
        if not key:
            return
        stored_response = dict(response)
        if request_fingerprint:
            stored_response[_IDEMPOTENCY_METADATA_KEY] = {"fingerprint": request_fingerprint}
        db.add(PlanningResourcePoolRequestORM(
            id=secrets.token_hex(16), pool_id=pool.id, client_request_id=key,
            revision=int(pool.revision), response_json=stored_response, created_at=_now(),
        ))

    def _mutate_event(self, db: Any, pool: PlanningResourcePoolORM, action: str, candidate_id: str, before: str, after: str, request_id: str = "") -> None:
        db.add(PlanningResourcePoolEventORM(
            id=secrets.token_hex(16), pool_id=pool.id, revision=int(pool.revision),
            action=action, candidate_id=candidate_id, before_state=before,
            after_state=after, client_request_id=request_id, created_at=_now(),
        ))

    @staticmethod
    def _sync_anchor_pointer(pool: PlanningResourcePoolORM, rows: Mapping[str, PlanningResourceCandidateORM]) -> bool:
        """Keep the durable anchor id aligned with the candidate state.

        Candidate state is the source of truth for the page projection.  A
        stale ``anchor_candidate_id`` (for example, from an interrupted or
        older decision) must not make an unrelated selection look like an
        anchor or survive a restart.  Return whether the pool pointer changed
        so write paths can include the repair in their revision.
        """
        anchors = [row for row in rows.values() if row.active and row.selection_state == "anchor"]
        preferred = next(
            (row for row in anchors if row.candidate_id == pool.anchor_candidate_id),
            anchors[0] if anchors else None,
        )
        anchor_id = preferred.candidate_id if preferred is not None else ""
        if pool.anchor_candidate_id == anchor_id:
            return False
        pool.anchor_candidate_id = anchor_id
        return True

    def _page_db(
        self,
        db: Any,
        workspace_id: str,
        planning_session_id: str | None = None,
        *,
        page_size: Any = None,
        cursor: str | None = None,
        create: bool = True,
        excluded_candidate_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
            pool = self._get_pool(db, workspace_id, planning_session_id, create=create)
            size = _bounded_page_size(page_size if page_size is not None else pool.page_size)
            offset = 0
            if cursor:
                token = _cursor_decode(cursor)
                if token.get("s") != pool.planning_session_id or int(token.get("r", -1)) != int(pool.revision):
                    raise StaleCursorError()
                offset = max(0, int(token.get("o", 0)))
            all_rows = list(db.scalars(select(PlanningResourceCandidateORM).where(
                PlanningResourceCandidateORM.pool_id == pool.id,
            )))
            # Enforce the document-only invariant even for callers that read
            # the durable pool directly (bypassing the generic API policy
            # recheck).  This also repairs legacy selected/anchor videos on
            # the first post-upgrade page read.
            revoked_unsupported = False
            for row in all_rows:
                payload = row.payload_json if isinstance(row.payload_json, Mapping) else {}
                if not row.active or not _video_candidate(payload):
                    continue
                before = row.selection_state
                row.selection_state = "rejected"
                row.active = False
                row.retained = False
                row.updated_at = _now()
                row.payload_json = {**payload, "planning_blocked": True, "planning_media_contract": "document-only"}
                self._mutate_event(db, pool, "revoke_unsupported", row.candidate_id, before, "rejected")
                revoked_unsupported = True
            if pool.anchor_candidate_id and any(
                row.candidate_id == pool.anchor_candidate_id and row.selection_state == "rejected"
                for row in all_rows
            ):
                pool.anchor_candidate_id = ""
                revoked_unsupported = True
            if revoked_unsupported:
                pool.revision = int(pool.revision) + 1
                pool.updated_at = _now()
            excluded = {
                _text(value, 180)
                for value in (excluded_candidate_ids or [])
                if _text(value, 180)
            }
            active_rows = [
                row for row in all_rows
                if row.active
                and row.candidate_id not in excluded
                and row.stable_id not in excluded
            ]
            active_rows.sort(key=lambda row: (row.ordinal, row.created_at))
            strict_batches = any(
                isinstance(row.payload_json, dict)
                and row.payload_json.get("planning_batch_contract") == "complete-page-v1"
                for row in active_rows
            )
            if strict_batches:
                # Every candidate card consumes a page slot.  Exact learner
                # references stay first (their ordinals are stable) and retain
                # provenance, but they are not hidden in an out-of-band lane.
                # Pinned selections are additionally projected above the grid
                # without being removed from the paged result set.
                protected = [
                    row for row in active_rows
                    if row.selection_state in {"selected", "anchor", "support"}
                ]
                rows = list(active_rows)
                # Keep incomplete qualified rows durable but do not advertise
                # them as a complete recommendation page.
                publishable_count = (len(rows) // size) * size
                # Exact learner references are never hidden merely because a
                # search engine failed to supply enough qualified companions.
                # In that fail-closed case expose the truthful partial final
                # page instead of making the user's own link disappear.
                if any(row.direct_user_reference for row in rows[publishable_count:]):
                    publishable_count = len(rows)
                publishable_rows = rows[:publishable_count]
            else:
                # Compatibility for direct service callers and historical
                # pools that predate the complete-page planning contract.
                protected = [
                    row for row in active_rows
                    if row.selection_state in {"selected", "anchor", "support"} or row.direct_user_reference
                ]
                rows = sorted(
                    active_rows,
                    key=lambda row: (
                        0 if row.selection_state in {"anchor", "support", "selected"} or row.direct_user_reference else 1,
                        row.ordinal,
                    ),
                )
                publishable_count = len(rows)
                publishable_rows = rows
            items = publishable_rows[offset:offset + size]
            next_offset = offset + len(items)
            has_more = next_offset < len(publishable_rows)
            # ``page_index`` follows the frontend's zero-based cursor model;
            # ``page``/``page_number`` remain human-facing one-based aliases.
            page_index = offset // size
            next_cursor = _cursor_encode(pool.planning_session_id, int(pool.revision), next_offset) if has_more else None
            previous_offset = max(0, offset - size)
            previous_cursor = (
                _cursor_encode(pool.planning_session_id, int(pool.revision), previous_offset)
                if offset > 0 else None
            )
            selected = [row for row in active_rows if row.selection_state in {"selected", "anchor", "support"}]
            anchor = next((row for row in active_rows if row.selection_state == "anchor"), None)
            support = [row for row in active_rows if row.selection_state == "support"]
            direct_count = len([row for row in active_rows if row.direct_user_reference])
            fresh_count = len([row for row in publishable_rows if not row.direct_user_reference])
            page_capacity = max(size, ((len(rows) + size - 1) // size) * size)
            slots_free = max(0, page_capacity - len(protected)) if not strict_batches else (size if len(rows) % size == 0 else size - (len(rows) % size))
            held_incomplete_count = max(0, len(rows) - publishable_count)
            page_shortfall_count = (
                max(0, size - (len(rows) % size))
                if strict_batches and len(rows) % size
                else 0
            )
            protected_payloads = [_event_payload(row) for row in protected]
            response = {
                "planning_session_id": pool.planning_session_id,
                "revision": int(pool.revision),
                "page_size": size,
                "page": page_index + 1,
                "page_index": page_index,
                "page_number": page_index + 1,
                "offset": offset,
                "current_cursor": cursor,
                "previous_cursor": previous_cursor,
                "mode": "server_paged",
                "server_paged": True,
                "items": [_event_payload(row) for row in items],
                "candidates": [_event_payload(row) for row in items],
                "protected_items": protected_payloads,
                "pinned_items": [_event_payload(row) for row in selected],
                "provided_items": [_event_payload(row) for row in active_rows if row.direct_user_reference],
                "next_cursor": next_cursor,
                "cursor": next_cursor,
                "has_more": has_more,
                "total_count": publishable_count,
                "qualified_candidate_count": len(rows),
                "held_incomplete_count": held_incomplete_count,
                # Machine-readable aliases for clients that need to explain a
                # partial final page without counting blocked rows as cards.
                "shortfall_count": page_shortfall_count,
                "page_shortfall_count": page_shortfall_count,
                "page_complete": page_shortfall_count == 0,
                "new_candidate_count": fresh_count,
                "direct_reference_count": direct_count,
                "all_active_count": len(active_rows),
                "selected_count": len(selected),
                "protected_count": len(protected),
                "page_capacity": page_capacity,
                "page_count": max(1, (publishable_count + size - 1) // size),
                "rejected_count": len([row for row in all_rows if row.selection_state == "rejected"]),
                "slots_free": slots_free,
                "available_slots": slots_free,
                "companion_slots_free": max(0, size - direct_count),
                "anchor_candidate_id": anchor.candidate_id if anchor else "",
                "anchor_id": anchor.candidate_id if anchor else "",
                "supporting_candidate_ids": [row.candidate_id for row in support],
                "support_ids": [row.candidate_id for row in support],
                "selection": {
                    "anchor": anchor.candidate_id if anchor else None,
                    "support": [row.candidate_id for row in support],
                    "selected": [row.candidate_id for row in selected],
                    "rejected": [row.candidate_id for row in all_rows if row.selection_state == "rejected"],
                },
            }
            return response

    def page(
        self,
        workspace_id: str,
        planning_session_id: str | None = None,
        *,
        page_size: Any = None,
        cursor: str | None = None,
        excluded_candidate_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        with self.session_factory() as db:
            response = self._page_db(
                db,
                workspace_id,
                planning_session_id,
                page_size=page_size,
                cursor=cursor,
                excluded_candidate_ids=excluded_candidate_ids,
            )
            db.commit()
            return response

    def history(self, workspace_id: str, planning_session_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as db:
            pool = self._get_pool(db, workspace_id, planning_session_id, create=False)
            rows = list(db.scalars(select(PlanningResourcePoolEventORM).where(
                PlanningResourcePoolEventORM.pool_id == pool.id,
            ).order_by(PlanningResourcePoolEventORM.created_at, PlanningResourcePoolEventORM.id)))
            return [
                {
                    "id": row.id, "revision": int(row.revision), "action": row.action,
                    "candidate_id": row.candidate_id, "before": row.before_state,
                    "after": row.after_state, "client_request_id": row.client_request_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

    events = history

    def candidates(
        self,
        workspace_id: str,
        planning_session_id: str,
        *,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Return candidate payloads for a server-side policy recheck.

        The normal page projection intentionally contains only one page.  A
        replenishment pass must inspect every active row, however, otherwise a
        blocked row on a later page can continue consuming durable capacity.
        This read-only helper keeps that internal audit lane separate from the
        learner-facing page contract.
        """

        with self.session_factory() as db:
            pool = self._get_pool(db, workspace_id, planning_session_id, create=False)
            query = select(PlanningResourceCandidateORM).where(
                PlanningResourceCandidateORM.pool_id == pool.id,
            )
            if not include_inactive:
                query = query.where(PlanningResourceCandidateORM.active.is_(True))
            rows = list(db.scalars(query))
            rows.sort(key=lambda row: (row.ordinal, row.created_at))
            return [_event_payload(row) for row in rows]

    def supersede_blocked(
        self,
        workspace_id: str,
        *,
        planning_session_id: str,
        candidate_ids: Iterable[str] | str,
        expected_revision: Any = None,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Deactivate only unselected, non-direct rows rejected by policy.

        Current-policy checks live in the API layer, while this method owns the
        durable invariant: direct references and learner selections can never
        be silently removed.  Superseded identities remain in the candidate
        table and event history so a later discovery round cannot recycle the
        same rejected result.
        """

        ids = [
            _text(value, 180)
            for value in ([candidate_ids] if isinstance(candidate_ids, str) else (candidate_ids or []))
            if _text(value, 180)
        ]
        request_fingerprint = _request_fingerprint(
            "supersede_blocked",
            {"candidate_ids": list(dict.fromkeys(ids))},
        )
        with _POOL_WRITE_LOCK:
            with self.session_factory() as db:
                pool = self._get_pool(db, workspace_id, planning_session_id, create=False)
                replay = self._idempotent(db, pool, client_request_id, request_fingerprint)
                if replay is not None:
                    db.commit()
                    return replay
                self._check_revision(pool, expected_revision)
                rows = {
                    row.candidate_id: row
                    for row in db.scalars(
                        select(PlanningResourceCandidateORM).where(
                            PlanningResourceCandidateORM.pool_id == pool.id,
                        )
                    )
                }
                changed = self._sync_anchor_pointer(pool, rows)
                request_id = _text(client_request_id, 160)
                for candidate_id in dict.fromkeys(ids):
                    row = rows.get(candidate_id)
                    if row is None:
                        continue
                    # The policy recheck must fail closed, but the mutation
                    # path also guards these invariants independently.
                    if (
                        not row.active
                        or row.selection_state != "unselected"
                        or row.direct_user_reference
                    ):
                        continue
                    before = row.selection_state
                    row.active = False
                    row.selection_state = "superseded"
                    row.retained = False
                    row.updated_at = _now()
                    self._mutate_event(
                        db,
                        pool,
                        "supersede_blocked",
                        row.candidate_id,
                        before,
                        "superseded",
                        request_id,
                    )
                    changed = True
                if changed:
                    pool.revision = int(pool.revision) + 1
                    pool.updated_at = _now()
                pool.selected_count = len(
                    [
                        row
                        for row in rows.values()
                        if row.selection_state in {"selected", "anchor", "support"}
                    ]
                )
                response = self._page_db(
                    db,
                    workspace_id,
                    pool.planning_session_id,
                    page_size=pool.page_size,
                )
                self._save_idempotent(db, pool, client_request_id, response, request_fingerprint)
                db.commit()
                return response

    # Descriptive aliases for callers that use the replenishment vocabulary.
    deactivate_blocked = supersede_blocked

    def revoke_unsupported(
        self,
        workspace_id: str,
        *,
        planning_session_id: str,
        candidate_ids: Iterable[str] | str,
    ) -> dict[str, Any]:
        """Durably revoke legacy media rows that violate document-only policy.

        Unlike ordinary quality superseding, this boundary applies even to a
        previously selected/anchored row: video/audio/subtitle material must
        not remain a handoff anchor after a policy upgrade.
        """

        ids = [
            _text(value, 180)
            for value in ([candidate_ids] if isinstance(candidate_ids, str) else (candidate_ids or []))
            if _text(value, 180)
        ]
        with _POOL_WRITE_LOCK:
            with self.session_factory() as db:
                pool = self._get_pool(db, workspace_id, planning_session_id, create=False)
                rows = {
                    row.candidate_id: row
                    for row in db.scalars(
                        select(PlanningResourceCandidateORM).where(
                            PlanningResourceCandidateORM.pool_id == pool.id,
                        )
                    )
                }
                changed = False
                for candidate_id in dict.fromkeys(ids):
                    row = rows.get(candidate_id)
                    if row is None or not row.active:
                        continue
                    if not (
                        bool((row.payload_json or {}).get("planning_blocked"))
                        or _video_candidate(row.payload_json if isinstance(row.payload_json, Mapping) else {})
                    ):
                        continue
                    before = row.selection_state
                    row.selection_state = "rejected"
                    row.active = False
                    row.retained = False
                    row.updated_at = _now()
                    self._mutate_event(db, pool, "revoke_unsupported", row.candidate_id, before, "rejected")
                    changed = True
                if pool.anchor_candidate_id and pool.anchor_candidate_id in ids:
                    pool.anchor_candidate_id = ""
                    changed = True
                if changed:
                    pool.revision = int(pool.revision) + 1
                    pool.updated_at = _now()
                pool.selected_count = len(
                    [row for row in rows.values() if row.active and row.selection_state in {"selected", "anchor", "support"}]
                )
                response = self._page_db(db, workspace_id, pool.planning_session_id, page_size=pool.page_size)
                db.commit()
                return response

    # Explicit aliases keep the service easy to consume from tests and later
    # handoff code without exposing ORM rows as part of the public contract.
    snapshot = page
    get_page = page

    def _upsert_unlocked(self, workspace_id: str, candidates: Iterable[Mapping[str, Any]], *, planning_session_id: str | None = None, mode: str = "append", page_size: Any = None, expected_revision: Any = None, client_request_id: str | None = None) -> dict[str, Any]:
        mode = _text(mode, 40).casefold() or "append"
        if mode not in {"append", "replace_unselected", "replace"}:
            raise PlanningPoolError("invalid_mode", "mode must be append, replace_unselected, or replace")
        incoming: list[tuple[str, dict[str, Any]]] = []
        for raw in candidates or []:
            if isinstance(raw, Mapping):
                incoming.append(_normalise_candidate(raw))
        with self.session_factory() as db:
            pool = (
                self._pool_for_request(db, workspace_id, client_request_id)
                if not planning_session_id
                else None
            ) or self._get_pool(db, workspace_id, planning_session_id)
            # Bind an idempotency key to the server-resolved session.  The
            # first create request legitimately omits a session id, while its
            # retry normally includes the id returned by that first response;
            # both forms must fingerprint as the same mutation.
            request_fingerprint = _request_fingerprint(
                "upsert",
                {
                    "planning_session_id": pool.planning_session_id,
                    "mode": mode,
                    "page_size": _bounded_page_size(page_size) if page_size is not None else None,
                    "candidates": [payload for _, payload in incoming],
                },
            )
            replay = self._idempotent(db, pool, client_request_id, request_fingerprint)
            if replay is not None:
                db.commit()
                return replay
            self._check_revision(pool, expected_revision)
            requested_page_size = _bounded_page_size(page_size) if page_size is not None else int(pool.page_size)
            page_size_changed = int(pool.page_size) != requested_page_size
            if page_size is not None:
                pool.page_size = requested_page_size
            if mode in {"replace", "replace_unselected"}:
                for row in db.scalars(select(PlanningResourceCandidateORM).where(
                    PlanningResourceCandidateORM.pool_id == pool.id,
                    PlanningResourceCandidateORM.active.is_(True),
                )):
                    if row.selection_state == "unselected" and not row.direct_user_reference:
                        row.active = False
                        self._mutate_event(db, pool, "replace_unselected", row.candidate_id, row.selection_state, "superseded", _text(client_request_id, 160))
                        row.selection_state = "superseded"
            by_id = {row.stable_id: row for row in db.scalars(select(PlanningResourceCandidateORM).where(PlanningResourceCandidateORM.pool_id == pool.id))}
            changed = page_size_changed or self._sync_anchor_pointer(pool, by_id)
            ordinal = max([int(row.ordinal or 0) for row in by_id.values()] or [0])
            for stable_id, payload in incoming:
                blocked_media = bool(payload.get("planning_blocked") is True or _video_candidate(payload))
                row = by_id.get(stable_id)
                if row is None:
                    ordinal += 1
                    row = PlanningResourceCandidateORM(
                        id=secrets.token_hex(16), pool_id=pool.id, candidate_id=stable_id,
                        stable_id=stable_id, ordinal=ordinal, payload_json=payload,
                        selection_state=("rejected" if blocked_media else "selected" if payload.get("planning_selection_intent") else "unselected"), active=not blocked_media,
                        retained=bool(payload.get("direct_user_reference") or payload.get("planning_selection_intent")) and not blocked_media, direct_user_reference=bool(payload.get("direct_user_reference")),
                        created_at=_now(), updated_at=_now(),
                    )
                    db.add(row)
                    by_id[stable_id] = row
                    self._mutate_event(db, pool, "append", stable_id, "", row.selection_state, _text(client_request_id, 160))
                    changed = True
                else:
                    before_payload = dict(row.payload_json or {})
                    before_state = row.selection_state
                    before_active = row.active
                    before_retained = bool(row.retained)
                    if blocked_media:
                        # A legacy selected/anchor video is revoked at the
                        # durable boundary; it must not survive into a later
                        # planning handoff merely because its URL was retained.
                        row.payload_json = {**(row.payload_json or {}), **payload}
                        row.selection_state = "rejected"
                        row.active = False
                        row.retained = False
                    elif row.selection_state in {"selected", "anchor", "support"} or row.direct_user_reference:
                        # Pinned metadata can be enriched, but its state and
                        # active visibility are immutable under discovery.
                        row.payload_json = {**(row.payload_json or {}), **payload}
                        row.active = True
                    elif payload.get("planning_selection_intent") and row.selection_state not in {"selected", "anchor", "support"}:
                        before = row.selection_state
                        row.selection_state = "selected"
                        row.retained = True
                        row.active = True
                        self._mutate_event(db, pool, "select", stable_id, before, "selected", _text(client_request_id, 160))
                    elif row.selection_state == "rejected":
                        # A fresh discovery does not undo an explicit reject.
                        row.payload_json = {**(row.payload_json or {}), **payload}
                    elif row.selection_state == "superseded":
                        # A policy-rejected discovery identity remains in the
                        # audit/exclusion lane.  Replaying the same provider
                        # URL must not silently recycle it into active page
                        # capacity.  An exact user reference is the one
                        # intentional exception: the learner's direct link
                        # remains preservable even if an older discovery row
                        # carried the same identity.
                        row.payload_json = {**(row.payload_json or {}), **payload}
                        if payload.get("direct_user_reference") is True:
                            row.direct_user_reference = True
                            row.selection_state = "unselected"
                            row.active = True
                            row.retained = True
                        else:
                            row.active = False
                            row.retained = False
                    else:
                        row.payload_json = {**(row.payload_json or {}), **payload}
                        row.active = True
                    changed = changed or (
                        before_payload != dict(row.payload_json or {})
                        or before_state != row.selection_state
                        or before_active != row.active
                        or before_retained != bool(row.retained)
                    )
                    row.updated_at = _now()
            if changed or mode in {"replace", "replace_unselected"}:
                pool.revision = int(pool.revision) + 1
                pool.updated_at = _now()
            pool.selected_count = len([row for row in by_id.values() if row.selection_state in {"selected", "anchor", "support"}])
            response = self._page_db(db, workspace_id, pool.planning_session_id, page_size=pool.page_size)
            self._save_idempotent(db, pool, client_request_id, response, request_fingerprint)
            db.commit()
            return response

    def upsert(self, workspace_id: str, candidates: Iterable[Mapping[str, Any]], **kwargs: Any) -> dict[str, Any]:
        # SQLite and the local single-user runtime do not provide a process
        # wide row lock.  Serialize write paths in-process so two callers
        # cannot both pass the same revision/idempotency checks.
        with _POOL_WRITE_LOCK:
            return self._upsert_unlocked(workspace_id, candidates, **kwargs)

    add_candidates = upsert
    replenish = upsert

    def _decide_unlocked(self, workspace_id: str, *, planning_session_id: str, action: str, candidate_ids: Iterable[str] | str | None = None, expected_revision: Any = None, client_request_id: str | None = None, anchor_candidate_id: str | None = None, supporting_candidate_ids: Iterable[str] | None = None) -> dict[str, Any]:
        ids = [_text(value, 180) for value in ([candidate_ids] if isinstance(candidate_ids, str) else (candidate_ids or [])) if _text(value, 180)]
        action = _text(action, 40).casefold().replace("-", "_")
        support_mode = action in {"select_support", "support"}
        original_supporting_ids = (
            [_text(value, 180) for value in supporting_candidate_ids if _text(value, 180)]
            if supporting_candidate_ids is not None
            else None
        )
        if action in {"select_anchor", "anchor"}:
            ids = [_text(anchor_candidate_id or (ids[0] if ids else ""), 180)]
            action = "set_anchor"
        if action in {"select_support", "support"} and original_supporting_ids is not None:
            ids = original_supporting_ids
            action = "select"
        elif action in {"select_support", "support"}:
            action = "select"
        if action in {"undo_reject", "restore", "unreject"}:
            action = "undo"
        if action in {"unselect", "unselect_candidate", "deselect_candidate", "clear_selection"}:
            action = "deselect"
        if action not in {"select", "deselect", "reject", "undo", "set_anchor", "clear_anchor"}:
            raise PlanningPoolError("invalid_action", "action must be select, deselect, reject, undo, or set_anchor")
        request_fingerprint = _request_fingerprint(
            "decide",
            {
                "action": action,
                "candidate_ids": ids,
                "anchor_candidate_id": _text(anchor_candidate_id, 180),
                "supporting_candidate_ids": original_supporting_ids,
                "support_mode": support_mode,
            },
        )
        with self.session_factory() as db:
            pool = self._get_pool(db, workspace_id, planning_session_id, create=False)
            replay = self._idempotent(db, pool, client_request_id, request_fingerprint)
            if replay is not None:
                db.commit()
                return replay
            self._check_revision(pool, expected_revision)
            rows = {row.candidate_id: row for row in db.scalars(select(PlanningResourceCandidateORM).where(PlanningResourceCandidateORM.pool_id == pool.id))}
            if action == "set_anchor":
                ids = [_text(anchor_candidate_id or (ids[0] if ids else ""), 180)]
            changed = self._sync_anchor_pointer(pool, rows)
            if action == "clear_anchor":
                ids = [row.candidate_id for row in rows.values() if row.selection_state == "anchor"]
            if action in {"select", "set_anchor", "undo"}:
                for candidate_id in ids:
                    row = rows.get(candidate_id)
                    if row is not None and (
                        bool((row.payload_json or {}).get("planning_blocked"))
                        or _video_candidate(row.payload_json if isinstance(row.payload_json, Mapping) else {})
                    ):
                        raise PlanningPoolError(
                            "video_material_disabled",
                            "video candidates cannot enter the document-only planning pool",
                        )
            if action in {"set_anchor", "select"} and action == "set_anchor":
                target = rows.get(ids[0]) if ids else None
                if target is None:
                    raise PlanningPoolError("candidate_not_found", "anchor candidate was not found")
                for row in rows.values():
                    if row.selection_state == "anchor" and row.candidate_id != target.candidate_id:
                        self._mutate_event(db, pool, "change_anchor", row.candidate_id, "anchor", "support", _text(client_request_id, 160))
                        row.selection_state = "support"
                        changed = True
                    elif row.candidate_id != target.candidate_id and row.selection_state == "selected":
                        self._mutate_event(db, pool, "normalise_support", row.candidate_id, "selected", "support", _text(client_request_id, 160))
                        row.selection_state = "support"
                        changed = True
                before = target.selection_state
                target.selection_state = "anchor"
                target.active = True
                target.retained = True
                self._mutate_event(db, pool, "set_anchor", target.candidate_id, before, "anchor", _text(client_request_id, 160))
                pool.anchor_candidate_id = target.candidate_id
                changed = changed or before != "anchor"
            else:
                for candidate_id in ids:
                    row = rows.get(candidate_id)
                    if row is None:
                        raise PlanningPoolError("candidate_not_found", f"candidate {candidate_id} was not found")
                    before = row.selection_state
                    if action == "select":
                        if before == "rejected":
                            row.active = True
                        # Selecting the current anchor is idempotent.  An
                        # explicit anchor must only be changed through
                        # set_anchor, clear_anchor, or deselect; otherwise a
                        # repeated click would demote it to support while the
                        # pool still carried its id.
                        if before == "anchor" and row.candidate_id == pool.anchor_candidate_id:
                            row.selection_state = "anchor"
                        else:
                            row.selection_state = "support" if (support_mode or pool.anchor_candidate_id) else "selected"
                        row.active = True
                        row.retained = True
                    elif action == "deselect":
                        # Deselect is the only path out of a selected/anchor/
                        # support state.  Rejection must remain explicit.
                        if before not in {"selected", "anchor", "support"}:
                            raise PlanningPoolError("candidate_not_selected", "candidate is not selected")
                        row.selection_state = "unselected"
                        row.active = True
                        row.retained = bool(row.direct_user_reference)
                        if before == "anchor":
                            pool.anchor_candidate_id = ""
                    elif action == "reject":
                        if before in {"selected", "anchor", "support"}:
                            raise PlanningPoolError("selected_candidate_pinned", "selected candidates must be explicitly deselected first")
                        row.selection_state = "rejected"
                        row.active = False
                        row.retained = bool(row.direct_user_reference)
                    elif action == "undo":
                        if before != "rejected":
                            raise PlanningPoolError("candidate_not_rejected", "only rejected candidates can be undone")
                        row.selection_state = "unselected"
                        row.active = True
                        row.retained = bool(row.direct_user_reference)
                    elif action == "clear_anchor":
                        row.selection_state = "support"
                        row.active = True
                    if before != row.selection_state:
                        self._mutate_event(db, pool, action, row.candidate_id, before, row.selection_state, _text(client_request_id, 160))
                        changed = True
            if changed:
                pool.revision = int(pool.revision) + 1
                pool.updated_at = _now()
            pool.selected_count = len([row for row in rows.values() if row.selection_state in {"selected", "anchor", "support"}])
            if action in {"clear_anchor", "deselect"} and not any(row.selection_state == "anchor" for row in rows.values()):
                pool.anchor_candidate_id = ""
            response = self._page_db(db, workspace_id, pool.planning_session_id, page_size=pool.page_size)
            self._save_idempotent(db, pool, client_request_id, response, request_fingerprint)
            db.commit()
            return response

    def decide(self, workspace_id: str, **kwargs: Any) -> dict[str, Any]:
        with _POOL_WRITE_LOCK:
            return self._decide_unlocked(workspace_id, **kwargs)

    def select(self, workspace_id: str, *, planning_session_id: str, candidate_ids: Iterable[str] | str, **kwargs: Any) -> dict[str, Any]:
        return self.decide(workspace_id, planning_session_id=planning_session_id, action="select", candidate_ids=candidate_ids, **kwargs)

    def reject(self, workspace_id: str, *, planning_session_id: str, candidate_ids: Iterable[str] | str, **kwargs: Any) -> dict[str, Any]:
        return self.decide(workspace_id, planning_session_id=planning_session_id, action="reject", candidate_ids=candidate_ids, **kwargs)

    def deselect(self, workspace_id: str, *, planning_session_id: str, candidate_ids: Iterable[str] | str, **kwargs: Any) -> dict[str, Any]:
        return self.decide(workspace_id, planning_session_id=planning_session_id, action="deselect", candidate_ids=candidate_ids, **kwargs)

    def undo(self, workspace_id: str, *, planning_session_id: str, candidate_ids: Iterable[str] | str, **kwargs: Any) -> dict[str, Any]:
        return self.decide(workspace_id, planning_session_id=planning_session_id, action="undo", candidate_ids=candidate_ids, **kwargs)


def get_planning_pool() -> PlanningResourcePool:
    return PlanningResourcePool()


PlanningResourcePoolService = PlanningResourcePool


def create_pool(workspace_id: str, *, planning_session_id: str | None = None, page_size: Any = None) -> dict[str, Any]:
    return get_planning_pool().page(workspace_id, planning_session_id, page_size=page_size)


def get_page(workspace_id: str, planning_session_id: str, *, page_size: Any = None, cursor: str | None = None) -> dict[str, Any]:
    return get_planning_pool().page(workspace_id, planning_session_id, page_size=page_size, cursor=cursor)


__all__ = [
    "DEFAULT_PAGE_SIZE", "PlanningPoolError", "StaleRevisionError", "StaleCursorError",
    "PlanningResourcePool", "PlanningResourcePoolService", "get_planning_pool", "create_pool", "get_page", "stable_candidate_identity",
]
