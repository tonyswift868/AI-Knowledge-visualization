"""Pure, local MentorFlow video-collection persistence service.

This module owns state and invariants only.  It deliberately has no HTTP,
network or provider imports; a later API/worker layer can call these methods
with already-normalised manifest, subtitle and provider-summary payloads.

The service returns plain dictionaries rather than leaking SQLAlchemy rows.
That keeps the contract stable for the future API and makes request replay
return the exact durable projection that was originally committed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, func, select, update

from .orm import (
    SessionLocal,
    VideoCollectionJobORM,
    VideoCollectionORM,
    VideoEpisodeORM,
    VideoSubtitleSegmentORM,
    VideoSummaryORM,
)


# Policy values are intentionally module constants so a worker/API can expose
# them without importing a network/provider adapter.
MAX_EPISODES = 1000
DEFAULT_MANIFEST_PAGE_SIZE = 50
MAX_MANIFEST_PAGE_SIZE = 100
DEFAULT_MAX_PARALLEL = 1
HARD_MAX_PARALLEL = 2
MAX_SUBTITLE_BYTES_PER_EPISODE = 8 * 1024 * 1024
MAX_SUBTITLE_SEGMENTS_PER_EPISODE = 20_000
MAX_EPISODE_DURATION_SECONDS = 6 * 60 * 60
MAX_TOTAL_SUBTITLE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_DURATION_SECONDS = 2_000 * 60 * 60
MAX_TOTAL_SUBTITLE_SEGMENTS = 2_000_000
DEFAULT_SUMMARY_BATCH_SIZE = 20
MIN_SUMMARY_BATCH_SIZE = 10
MAX_SUMMARY_BATCH_SIZE = 20
MAX_METADATA_BYTES = 64 * 1024
CURSOR_VERSION = 1

# Short aliases used by downstream code and tests.
MAX_SUBTITLE_BYTES = MAX_SUBTITLE_BYTES_PER_EPISODE
MAX_SEGMENTS_PER_EPISODE = MAX_SUBTITLE_SEGMENTS_PER_EPISODE
MAX_TOTAL_SEGMENTS = MAX_TOTAL_SUBTITLE_SEGMENTS
MAX_DURATION_PER_EPISODE_SECONDS = MAX_EPISODE_DURATION_SECONDS
MAX_TOTAL_DURATION_SECONDS_POLICY = MAX_TOTAL_DURATION_SECONDS


class CollectionStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class EpisodeStatus(str, Enum):
    UNPROCESSED = "unprocessed"
    PROCESSING = "processing"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"


class SummaryScope(str, Enum):
    EPISODE = "episode"
    BATCH = "batch"
    COLLECTION = "collection"


class VideoCollectionError(Exception):
    """Stable, serialisable service error.

    Codes are uppercase wire values.  ``legacy_code`` offers the lowercase
    spelling used by a few older local service helpers without changing the
    durable/API-facing code.
    """

    def __init__(self, code: str, message: str = "video collection request rejected", *, status: int | None = None):
        self.code = str(code).upper()
        self.legacy_code = self.code.lower()
        self.message = message
        self.status = int(status if status is not None else (409 if self.code.startswith("STALE") or self.code == "IDEMPOTENCY_CONFLICT" else 422))
        super().__init__(message)


class StaleCursorError(VideoCollectionError):
    def __init__(self, message: str = "manifest cursor is stale"):
        super().__init__("STALE_CURSOR", message, status=409)


class IdempotencyConflictError(VideoCollectionError):
    def __init__(self, message: str = "idempotency key was already used for another request"):
        super().__init__("IDEMPOTENCY_CONFLICT", message, status=409)


class BudgetExceededError(VideoCollectionError):
    def __init__(self, message: str = "video collection budget exceeded"):
        super().__init__("BUDGET_EXCEEDED", message, status=422)


class EvidenceValidationError(VideoCollectionError):
    def __init__(self, message: str = "summary evidence is not valid"):
        super().__init__("EVIDENCE_INVALID", message, status=422)


# Stable error-code aliases for callers that prefer constants.
ERR_MANIFEST_LIMIT = "MANIFEST_LIMIT_EXCEEDED"
ERR_STALE_CURSOR = "STALE_CURSOR"
ERR_JOB_INTERRUPTED = "JOB_INTERRUPTED"
ERR_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
ERR_EVIDENCE_INVALID = "EVIDENCE_INVALID"
ERR_IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


_VIDEO_WRITE_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, limit: int = 800) -> str:
    return str(value or "").strip()[:limit]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_SENSITIVE_KEY_PARTS = (
    "api_key", "apikey", "access_token", "auth_token", "bearer",
    "credential", "password", "provider_session", "secret", "token",
)


def _safe_json_object(value: Any, *, label: str = "metadata") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).casefold().replace("-", "_")
                if any(part in key for part in _SENSITIVE_KEY_PARTS):
                    raise VideoCollectionError("SENSITIVE_DATA_REJECTED", f"{label} must not contain credentials")
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    result = dict(value)
    visit(result)
    if len(_canonical_json(result).encode("utf-8")) > MAX_METADATA_BYTES:
        raise VideoCollectionError("METADATA_TOO_LARGE", f"{label} exceeds the bounded JSON limit")
    return result


def _canonical_url(value: Any) -> str:
    raw = _text(value, 2400)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise VideoCollectionError("INVALID_URL", "video collection URLs must use HTTP or HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise VideoCollectionError("INVALID_URL", "video collection URLs must not contain user-info credentials")
        host = (parsed.hostname or "").casefold().strip(".")
        if not host:
            return ""
        port = ""
        try:
            if parsed.port:
                port = f":{parsed.port}"
        except ValueError as exc:
            raise VideoCollectionError("INVALID_URL", "canonical URL contains an invalid port") from exc
        query_items = sorted(parse_qsl(parsed.query, keep_blank_values=True))
        for key, _value in query_items:
            folded = key.casefold().replace("-", "_")
            if any(part in folded for part in _SENSITIVE_KEY_PARTS):
                raise VideoCollectionError("SENSITIVE_DATA_REJECTED", "video collection URLs must not contain credentials")
        query = urlencode(query_items, doseq=True)
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((parsed.scheme.casefold(), host + port, path, query, ""))
    except (TypeError, ValueError) as exc:
        raise VideoCollectionError("INVALID_URL", "canonical URL is malformed") from exc


def canonical_url_hash(value: Any) -> str:
    canonical = _canonical_url(value)
    return _hash_text(canonical) if canonical else ""


def _bounded_page_size(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = DEFAULT_MANIFEST_PAGE_SIZE
    return max(1, min(MAX_MANIFEST_PAGE_SIZE, parsed))


def _bounded_parallel(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = DEFAULT_MAX_PARALLEL
    return max(1, min(HARD_MAX_PARALLEL, parsed))


def _bounded_summary_batch(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = DEFAULT_SUMMARY_BATCH_SIZE
    return max(MIN_SUMMARY_BATCH_SIZE, min(MAX_SUMMARY_BATCH_SIZE, parsed))


def _cursor_encode(collection_id: str, revision: int, offset: int) -> str:
    payload = {"v": CURSOR_VERSION, "c": str(collection_id), "r": int(revision), "o": int(offset)}
    raw = _canonical_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_decode(cursor: Any) -> dict[str, Any]:
    try:
        value = str(cursor or "").strip()
        if not value:
            raise ValueError
        value += "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
        if not isinstance(payload, dict) or int(payload.get("v")) != CURSOR_VERSION:
            raise ValueError
        collection_id = _text(payload.get("c"), 96)
        revision = int(payload.get("r"))
        offset = int(payload.get("o"))
        if not collection_id or revision < 0 or offset < 0:
            raise ValueError
        return {"v": CURSOR_VERSION, "c": collection_id, "r": revision, "o": offset}
    except (ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError, UnicodeDecodeError, UnicodeEncodeError, binascii.Error):
        raise StaleCursorError() from None


def _collection_stable_id(workspace_id: str, provider: str, provider_collection_id: str, canonical_url: str) -> str:
    if provider_collection_id:
        identity = {"provider": provider.casefold(), "provider_collection_id": provider_collection_id}
    elif canonical_url:
        identity = {"canonical_url": canonical_url}
    else:
        raise VideoCollectionError("MISSING_COLLECTION_IDENTITY", "provider_collection_id or canonical_url is required")
    return "collection:" + _sha({"workspace_id": workspace_id, "identity": identity})[:48]


def _episode_stable_id(provider: str, provider_episode_id: str, canonical_url: str, supplied: Any = None) -> str:
    # Caller supplied ids are intentionally ignored.  Stable identity is
    # derived from the provider episode id or canonical URL so one manifest
    # item cannot alias another row and mutate already-processed evidence.
    if provider_episode_id:
        return "episode:" + _sha({"provider": provider.casefold(), "provider_episode_id": provider_episode_id})[:48]
    if canonical_url:
        return "episode:" + _hash_text(canonical_url)[:48]
    raise VideoCollectionError("MISSING_EPISODE_IDENTITY", "provider_episode_id or canonical_url is required")


def _duration_seconds(raw: Mapping[str, Any]) -> float:
    value = raw.get("duration_seconds", raw.get("duration", raw.get("duration_s", 0)))
    if value is None or value == "":
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise VideoCollectionError("INVALID_DURATION", "episode duration must be numeric") from None
    if result < 0 or result > MAX_EPISODE_DURATION_SECONDS:
        raise BudgetExceededError("episode duration exceeds the six-hour limit")
    return result


def _episode_request_fingerprint(entry: Mapping[str, Any], stable_id: str, canonical_url: str) -> str:
    supplied = _text(entry.get("input_fingerprint"), 128)
    if supplied:
        return supplied
    return _sha(
        {
            "stable_id": stable_id,
            "provider": _text(entry.get("provider"), 80).casefold(),
            "provider_episode_id": _text(entry.get("provider_episode_id", entry.get("episode_id")), 240),
            "canonical_url": canonical_url,
            "duration_seconds": _duration_seconds(entry),
        }
    )


def _normalise_manifest_entry(raw: Mapping[str, Any], collection_provider: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise VideoCollectionError("INVALID_MANIFEST_ENTRY", "manifest entries must be objects")
    provider = _text(raw.get("provider") or collection_provider, 80)
    provider_episode_id = _text(raw.get("provider_episode_id", raw.get("episode_id")), 240)
    canonical_url = _canonical_url(raw.get("canonical_url") or raw.get("url"))
    stable_id = _episode_stable_id(provider, provider_episode_id, canonical_url, raw.get("stable_id"))
    duration_seconds = _duration_seconds(raw)
    selected = raw.get("selected", raw.get("is_selected", True)) is not False
    ordinal_raw = raw.get("ordinal", raw.get("position", 0))
    try:
        ordinal = int(ordinal_raw or 0)
    except (TypeError, ValueError, OverflowError):
        raise VideoCollectionError("INVALID_MANIFEST_ENTRY", "episode ordinal must be an integer") from None
    if ordinal < 0:
        raise VideoCollectionError("INVALID_MANIFEST_ENTRY", "episode ordinal cannot be negative")
    return {
        "provider": provider,
        "provider_episode_id": provider_episode_id,
        "canonical_url": canonical_url,
        "canonical_url_hash": _hash_text(canonical_url) if canonical_url else "",
        "stable_id": stable_id,
        "input_fingerprint": _episode_request_fingerprint(raw, stable_id, canonical_url),
        "title": _text(raw.get("title") or raw.get("name"), 300),
        "ordinal": ordinal,
        "duration_seconds": duration_seconds,
        "selected": selected,
        "metadata_json": _safe_json_object(raw.get("metadata") or raw.get("metadata_json") or {}, label="episode metadata"),
    }


def _request_payload_fingerprint(payload: Mapping[str, Any]) -> str:
    return _sha(dict(payload))


def _collection_dict(row: VideoCollectionORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "collection_id": row.id,
        "workspace_id": row.workspace_id,
        "stable_id": row.stable_id,
        "provider": row.provider,
        "provider_collection_id": row.provider_collection_id,
        "canonical_url": row.canonical_url,
        "canonical_url_hash": row.canonical_url_hash,
        "input_fingerprint": row.input_fingerprint,
        "idempotency_key": row.idempotency_key,
        "title": row.title,
        "status": row.status,
        "source_manifest_revision": int(row.source_manifest_revision or 0),
        "manifest_revision": int(row.source_manifest_revision or 0),
        "reported_count": row.reported_count,
        "enumerated_count": int(row.enumerated_count or 0),
        "selected_count": int(row.selected_count or 0),
        "manifest_fingerprint": row.manifest_fingerprint,
        "metadata": dict(row.metadata_json or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _episode_dict(row: VideoEpisodeORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "episode_id": row.id,
        "collection_id": row.collection_id,
        "stable_id": row.stable_id,
        "provider": row.provider,
        "provider_episode_id": row.provider_episode_id,
        "canonical_url": row.canonical_url,
        "canonical_url_hash": row.canonical_url_hash,
        "input_fingerprint": row.input_fingerprint,
        "title": row.title,
        "ordinal": int(row.ordinal or 0),
        "duration_seconds": float(row.duration_seconds or 0),
        "selected": bool(row.selected),
        "active": bool(row.active),
        "status": row.status,
        "attempts": int(row.attempts or 0),
        "retryable": bool(row.retryable),
        "checkpoint": dict(row.checkpoint_json or {}),
        "failure": dict(row.failure_json or {}),
        "source_manifest_revision": int(row.source_manifest_revision or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _job_dict(row: VideoCollectionJobORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.id,
        "collection_id": row.collection_id,
        "job_type": row.job_type,
        "status": row.status,
        "idempotency_key": row.idempotency_key,
        "request_fingerprint": row.request_fingerprint,
        "request": dict(row.request_json or {}),
        "result": dict(row.result_json or {}),
        "failure": dict(row.failure_json or {}),
        "progress": dict(row.progress_json or {}),
        "checkpoint": dict(row.checkpoint_json or {}),
        "attempts": int(row.attempts or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _summary_dict(row: VideoSummaryORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "summary_id": row.id,
        "collection_id": row.collection_id,
        "episode_id": row.episode_id,
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "batch_key": row.batch_key,
        "input_fingerprint": row.input_fingerprint,
        "provider_fingerprint": row.provider_fingerprint,
        "summary_text": row.summary_text,
        "summary": row.summary_text,
        "summary_json": dict(row.summary_json or {}),
        "evidence": list(row.evidence_json or []),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class VideoCollectionService:
    """Transactional service for manifests, jobs, subtitle segments and summaries."""

    def __init__(self, session_factory: Any = SessionLocal):
        self.session_factory = session_factory

    def _collection(self, db: Any, collection_id: str) -> VideoCollectionORM:
        row = db.get(VideoCollectionORM, _text(collection_id, 96))
        if row is None:
            raise VideoCollectionError("COLLECTION_NOT_FOUND", "video collection was not found", status=404)
        return row

    def _job(self, db: Any, job_id: str) -> VideoCollectionJobORM:
        row = db.get(VideoCollectionJobORM, _text(job_id, 96))
        if row is None:
            raise VideoCollectionError("JOB_NOT_FOUND", "video collection job was not found", status=404)
        return row

    def _episode(self, db: Any, episode_id: str) -> VideoEpisodeORM:
        row = db.get(VideoEpisodeORM, _text(episode_id, 96))
        if row is None:
            raise VideoCollectionError("EPISODE_NOT_FOUND", "video episode was not found", status=404)
        return row

    def create_collection(
        self,
        *,
        workspace_id: str,
        provider: str = "",
        provider_collection_id: str | None = None,
        canonical_url: str | None = None,
        title: str = "",
        input_fingerprint: str | None = None,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace_id = _text(workspace_id, 64)
        if not workspace_id:
            raise VideoCollectionError("WORKSPACE_REQUIRED", "workspace_id is required")
        provider = _text(provider, 80)
        provider_collection_id = _text(provider_collection_id, 240)
        canonical = _canonical_url(canonical_url)
        stable_id = _collection_stable_id(workspace_id, provider, provider_collection_id, canonical)
        desired_input = _text(input_fingerprint, 128) or _sha({"stable_id": stable_id, "provider": provider, "canonical_url": canonical})
        desired_key = _text(idempotency_key, 160) or f"create:{stable_id}"
        safe_metadata = _safe_json_object(metadata or {}, label="collection metadata")
        desired_payload = {"workspace_id": workspace_id, "provider": provider, "provider_collection_id": provider_collection_id, "canonical_url": canonical, "title": _text(title, 300), "input_fingerprint": desired_input, "metadata": safe_metadata}
        request_fp = _request_payload_fingerprint(desired_payload)
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            existing_by_key = db.scalar(select(VideoCollectionORM).where(VideoCollectionORM.workspace_id == workspace_id, VideoCollectionORM.idempotency_key == desired_key))
            if existing_by_key is not None:
                existing_request_fp = (existing_by_key.metadata_json or {}).get("_create_request_fingerprint")
                if existing_request_fp and existing_request_fp != request_fp:
                    raise IdempotencyConflictError()
                return _collection_dict(existing_by_key)
            existing = db.scalar(select(VideoCollectionORM).where(VideoCollectionORM.workspace_id == workspace_id, VideoCollectionORM.stable_id == stable_id))
            if existing is not None:
                # Stable identity is authoritative; a second request with a
                # different idempotency key must not create a duplicate row.
                if existing.input_fingerprint != desired_input and existing.input_fingerprint:
                    raise VideoCollectionError("COLLECTION_INPUT_CONFLICT", "collection identity has a different input fingerprint", status=409)
                db.commit()
                return _collection_dict(existing)
            duplicate_input = db.scalar(select(VideoCollectionORM).where(VideoCollectionORM.workspace_id == workspace_id, VideoCollectionORM.input_fingerprint == desired_input))
            if duplicate_input is not None:
                raise IdempotencyConflictError("input fingerprint already belongs to another collection")
            row = VideoCollectionORM(
                id="vc-" + _hash_text(workspace_id + ":" + stable_id)[:40],
                workspace_id=workspace_id,
                stable_id=stable_id,
                provider=provider,
                provider_collection_id=provider_collection_id,
                canonical_url=canonical,
                canonical_url_hash=_hash_text(canonical) if canonical else "",
                input_fingerprint=desired_input,
                idempotency_key=desired_key,
                title=_text(title, 300),
                status=CollectionStatus.ACTIVE.value,
                source_manifest_revision=0,
                metadata_json={**safe_metadata, "_create_request_fingerprint": request_fp},
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(row)
            db.commit()
            return _collection_dict(row)

    create = create_collection

    def update_collection_title(self, collection_id: str, title: str) -> dict[str, Any]:
        """Apply extractor metadata without changing the collection identity."""

        value = _text(title, 300)
        if not value:
            return self.get_collection(collection_id) or {}
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            row = self._collection(db, collection_id)
            row.title = value
            row.updated_at = _now()
            db.commit()
            return _collection_dict(row)

    def get_collection(
        self,
        collection_id: str | None = None,
        *,
        workspace_id: str | None = None,
        stable_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | None:
        with self.session_factory() as db:
            row = None
            if collection_id:
                row = db.get(VideoCollectionORM, _text(collection_id, 96))
            elif stable_id:
                if not workspace_id:
                    raise VideoCollectionError("WORKSPACE_REQUIRED", "workspace_id is required for stable-id lookup")
                row = db.scalar(select(VideoCollectionORM).where(VideoCollectionORM.workspace_id == _text(workspace_id, 64), VideoCollectionORM.stable_id == _text(stable_id, 160)))
            elif idempotency_key:
                if not workspace_id:
                    raise VideoCollectionError("WORKSPACE_REQUIRED", "workspace_id is required for idempotency lookup")
                row = db.scalar(select(VideoCollectionORM).where(VideoCollectionORM.workspace_id == _text(workspace_id, 64), VideoCollectionORM.idempotency_key == _text(idempotency_key, 160)))
            if row is not None and workspace_id and row.workspace_id != _text(workspace_id, 64):
                row = None
            return _collection_dict(row) if row else None

    def upsert_manifest(
        self,
        collection_id: str,
        entries: Iterable[Mapping[str, Any]],
        *,
        reported_count: int | None = None,
        selected_count: int | None = None,
        source_manifest_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        # Materialise and validate every entry before opening a write path.
        # This is the atomic 1001-entry guard: no collection/episode row can
        # be changed when the oversized request is rejected.
        raw_entries = list(entries or [])
        if len(raw_entries) > MAX_EPISODES:
            raise VideoCollectionError(ERR_MANIFEST_LIMIT, "manifest contains more than 1000 episodes")
        if reported_count is not None:
            try:
                reported = int(reported_count)
            except (TypeError, ValueError, OverflowError):
                raise VideoCollectionError("INVALID_REPORTED_COUNT", "reported_count must be an integer") from None
            if reported < 0 or reported > MAX_EPISODES:
                raise VideoCollectionError(ERR_MANIFEST_LIMIT, "reported_count is outside the 1000-episode limit")
        else:
            reported = None
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            collection = self._collection(db, collection_id)
            normalised = [_normalise_manifest_entry(item, collection.provider) for item in raw_entries]
            seen: set[str] = set()
            seen_ordinals: set[int] = set()
            seen_fingerprints: set[str] = set()
            for item in normalised:
                if item["stable_id"] in seen:
                    raise VideoCollectionError("DUPLICATE_MANIFEST_IDENTITY", "manifest contains a duplicate episode identity")
                if item["ordinal"] in seen_ordinals:
                    raise VideoCollectionError("DUPLICATE_MANIFEST_ORDINAL", "manifest contains a duplicate episode ordinal")
                if item["input_fingerprint"] in seen_fingerprints:
                    raise VideoCollectionError("DUPLICATE_MANIFEST_FINGERPRINT", "manifest contains a duplicate episode fingerprint")
                seen.add(item["stable_id"])
                seen_ordinals.add(item["ordinal"])
                seen_fingerprints.add(item["input_fingerprint"])
            fingerprint = _sha(
                {
                    "reported_count": reported,
                    "entries": [
                        {key: item[key] for key in ("stable_id", "input_fingerprint", "ordinal", "selected", "duration_seconds", "title")}
                        for item in sorted(normalised, key=lambda value: (value["ordinal"], value["stable_id"]))
                    ],
                }
            )
            if collection.manifest_fingerprint == fingerprint:
                collection.reported_count = reported if reported is not None else collection.reported_count
                collection.enumerated_count = int(db.scalar(select(func.count(VideoEpisodeORM.id)).where(VideoEpisodeORM.collection_id == collection.id, VideoEpisodeORM.active.is_(True))) or 0)
                collection.selected_count = int(db.scalar(select(func.count(VideoEpisodeORM.id)).where(VideoEpisodeORM.collection_id == collection.id, VideoEpisodeORM.active.is_(True), VideoEpisodeORM.selected.is_(True))) or 0)
                collection.updated_at = _now()
                response = {"collection": _collection_dict(collection), "manifest_revision": int(collection.source_manifest_revision or 0), "revision": int(collection.source_manifest_revision or 0), "changed": False, "episodes": []}
                db.commit()
                return response
            previous_revision = int(collection.source_manifest_revision or 0)
            next_revision = previous_revision + 1
            if source_manifest_revision is not None:
                try:
                    expected = int(source_manifest_revision)
                except (TypeError, ValueError, OverflowError):
                    raise VideoCollectionError("INVALID_MANIFEST_REVISION", "source_manifest_revision must be an integer") from None
                if expected not in {previous_revision, next_revision}:
                    raise StaleCursorError("source manifest revision is stale")
            existing_rows = {row.stable_id: row for row in db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == collection.id))}
            incoming_ids = set()
            for item in normalised:
                incoming_ids.add(item["stable_id"])
                row = existing_rows.get(item["stable_id"])
                if row is None:
                    row = VideoEpisodeORM(
                        id="ve-" + _hash_text(collection.id + ":" + item["stable_id"])[:40],
                        collection_id=collection.id,
                        status=EpisodeStatus.UNPROCESSED.value,
                        attempts=0,
                        retryable=True,
                        checkpoint_json={},
                        failure_json={},
                        created_at=_now(),
                        updated_at=_now(),
                    )
                    db.add(row)
                    existing_rows[item["stable_id"]] = row
                old_fingerprint = row.input_fingerprint
                row.stable_id = item["stable_id"]
                row.provider = item["provider"]
                row.provider_episode_id = item["provider_episode_id"]
                row.canonical_url = item["canonical_url"]
                row.canonical_url_hash = item["canonical_url_hash"]
                row.input_fingerprint = item["input_fingerprint"]
                row.title = item["title"]
                row.ordinal = item["ordinal"]
                row.duration_seconds = item["duration_seconds"]
                row.selected = bool(item["selected"])
                row.active = True
                row.source_manifest_revision = next_revision
                if old_fingerprint and old_fingerprint != item["input_fingerprint"]:
                    # A changed source must never retain ready/checkpoint state
                    # from earlier bytes.  Stale summaries remain auditable but
                    # are excluded from future hierarchy inputs.
                    db.execute(delete(VideoSubtitleSegmentORM).where(VideoSubtitleSegmentORM.episode_id == row.id))
                    db.execute(update(VideoSummaryORM).where(VideoSummaryORM.collection_id == collection.id, VideoSummaryORM.episode_id == row.id).values(status="stale"))
                    db.execute(update(VideoSummaryORM).where(VideoSummaryORM.collection_id == collection.id, VideoSummaryORM.scope_type.in_([SummaryScope.BATCH.value, SummaryScope.COLLECTION.value])).values(status="stale"))
                    row.status = EpisodeStatus.UNPROCESSED.value
                    row.retryable = True
                    row.processing_token = ""
                    row.checkpoint_json = {}
                    row.failure_json = {}
                row.updated_at = _now()
            for stable_id, row in existing_rows.items():
                if stable_id not in incoming_ids:
                    row.active = False
                    row.selected = False
                    row.source_manifest_revision = next_revision
                    row.updated_at = _now()
            collection.source_manifest_revision = next_revision
            collection.manifest_fingerprint = fingerprint
            collection.reported_count = reported
            collection.enumerated_count = len(normalised)
            collection.selected_count = sum(1 for item in normalised if item["selected"])
            collection.updated_at = _now()
            # ``selected_count`` is authoritative from the submitted manifest;
            # an explicit argument is accepted only as a consistency check.
            if selected_count is not None:
                try:
                    explicit_selected = int(selected_count)
                except (TypeError, ValueError, OverflowError):
                    raise VideoCollectionError("INVALID_SELECTED_COUNT", "selected_count must be an integer") from None
                if explicit_selected != collection.selected_count:
                    raise VideoCollectionError("SELECTED_COUNT_MISMATCH", "selected_count does not match manifest entries")
            response = {
                "collection": _collection_dict(collection),
                "manifest_revision": next_revision,
                "revision": next_revision,
                "changed": True,
                "reported_count": collection.reported_count,
                "enumerated_count": collection.enumerated_count,
                "selected_count": collection.selected_count,
                "episodes": [_episode_dict(row) for row in sorted(existing_rows.values(), key=lambda value: (int(value.ordinal or 0), value.stable_id)) if row.active],
            }
            db.commit()
            return response

    upsert_manifest_entries = upsert_manifest
    upsert_episodes = upsert_manifest

    def manifest_page(
        self,
        collection_id: str,
        *,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        with self.session_factory() as db:
            collection = self._collection(db, collection_id)
            size = _bounded_page_size(page_size if page_size is not None else DEFAULT_MANIFEST_PAGE_SIZE)
            offset = 0
            if cursor:
                token = _cursor_decode(cursor)
                if token["c"] != collection.id or token["r"] != int(collection.source_manifest_revision or 0):
                    raise StaleCursorError()
                offset = token["o"]
            rows = list(db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == collection.id, VideoEpisodeORM.active.is_(True)).order_by(VideoEpisodeORM.ordinal, VideoEpisodeORM.stable_id)))
            items = rows[offset:offset + size]
            next_offset = offset + len(items)
            has_more = next_offset < len(rows)
            next_cursor = _cursor_encode(collection.id, int(collection.source_manifest_revision or 0), next_offset) if has_more else None
            response = {
                "collection_id": collection.id,
                "manifest_revision": int(collection.source_manifest_revision or 0),
                "revision": int(collection.source_manifest_revision or 0),
                "page_size": size,
                "offset": offset,
                "page_index": offset // size,
                "page": offset // size + 1,
                "page_number": offset // size + 1,
                "current_cursor": cursor or None,
                "previous_cursor": _cursor_encode(collection.id, int(collection.source_manifest_revision or 0), max(0, offset - size)) if offset > 0 else None,
                "items": [_episode_dict(row) for row in items],
                "episodes": [_episode_dict(row) for row in items],
                "next_cursor": next_cursor,
                "cursor": next_cursor,
                "has_more": has_more,
                "total_count": len(rows),
                "reported_count": collection.reported_count,
                "enumerated_count": collection.enumerated_count,
                "selected_count": collection.selected_count,
            }
            return response

    get_manifest_page = manifest_page
    page_manifest = manifest_page
    list_episodes = manifest_page

    def create_job(
        self,
        collection_id: str,
        *,
        job_type: str = "transcribe",
        idempotency_key: str | None = None,
        request: Mapping[str, Any] | None = None,
        max_parallel: int | None = None,
        summary_batch_size: int | None = None,
    ) -> dict[str, Any]:
        request_json = _safe_json_object(request or {}, label="job request")
        request_json.setdefault("max_parallel", _bounded_parallel(max_parallel))
        request_json.setdefault("summary_batch_size", _bounded_summary_batch(summary_batch_size))
        request_json["max_parallel"] = _bounded_parallel(request_json.get("max_parallel"))
        request_json["summary_batch_size"] = _bounded_summary_batch(request_json.get("summary_batch_size"))
        key = _text(idempotency_key, 160) or f"job:{_text(collection_id, 96)}:{_text(job_type, 48)}:{_request_payload_fingerprint(request_json)}"
        request_fp = _request_payload_fingerprint({"job_type": _text(job_type, 48), "request": request_json})
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            collection = self._collection(db, collection_id)
            existing = db.scalar(select(VideoCollectionJobORM).where(VideoCollectionJobORM.collection_id == collection.id, VideoCollectionJobORM.idempotency_key == key))
            if existing is not None:
                if existing.request_fingerprint != request_fp:
                    raise IdempotencyConflictError()
                return _job_dict(existing)
            active = db.scalar(
                select(VideoCollectionJobORM).where(
                    VideoCollectionJobORM.collection_id == collection.id,
                    VideoCollectionJobORM.job_type == _text(job_type, 48),
                    VideoCollectionJobORM.status.in_([
                        JobStatus.QUEUED.value,
                        JobStatus.RUNNING.value,
                        JobStatus.PAUSED.value,
                        JobStatus.CANCEL_REQUESTED.value,
                    ]),
                ).order_by(VideoCollectionJobORM.created_at.desc())
            )
            if active is not None:
                if active.request_fingerprint == request_fp:
                    return _job_dict(active)
                raise VideoCollectionError(
                    "VIDEO_COLLECTION_JOB_IN_PROGRESS",
                    "a job of this type is already active for the collection",
                    status=409,
                )
            selected_total = int(db.scalar(select(func.count(VideoEpisodeORM.id)).where(VideoEpisodeORM.collection_id == collection.id, VideoEpisodeORM.active.is_(True), VideoEpisodeORM.selected.is_(True))) or 0)
            row = VideoCollectionJobORM(
                id="vj-" + _hash_text(collection.id + ":" + key)[:40],
                collection_id=collection.id,
                job_type=_text(job_type, 48) or "transcribe",
                status=JobStatus.QUEUED.value,
                idempotency_key=key,
                request_fingerprint=request_fp,
                request_json=request_json,
                result_json={},
                failure_json={},
                progress_json={"selected_total": selected_total, "completed": 0, "failed": 0, "unprocessed": selected_total},
                checkpoint_json={},
                attempts=0,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(row)
            db.commit()
            return _job_dict(row)

    enqueue_job = create_job
    create_transcription_job = create_job

    def get_job(self, job_id: str, *, collection_id: str | None = None) -> dict[str, Any] | None:
        with self.session_factory() as db:
            row = db.get(VideoCollectionJobORM, _text(job_id, 96))
            if row is None or (collection_id and row.collection_id != _text(collection_id, 96)):
                return None
            return _job_dict(row)

    def job_page(
        self,
        collection_id: str,
        *,
        job_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        try:
            page_limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError, OverflowError):
            raise VideoCollectionError("INVALID_JOB_PAGE", "job limit must be numeric") from None
        with self.session_factory() as db:
            self._collection(db, collection_id)
            query = select(VideoCollectionJobORM).where(VideoCollectionJobORM.collection_id == _text(collection_id, 96))
            if job_type:
                query = query.where(VideoCollectionJobORM.job_type == _text(job_type, 48))
            rows = list(db.scalars(query.order_by(VideoCollectionJobORM.created_at.desc(), VideoCollectionJobORM.id.desc()).limit(page_limit)))
            return {"collection_id": _text(collection_id, 96), "items": [_job_dict(row) for row in rows]}

    list_jobs = job_page

    def update_job_projection(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: Mapping[str, Any] | None = None,
        checkpoint: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        failure: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one bounded worker projection without exposing ORM rows."""

        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            row = self._job(db, job_id)
            if status is not None:
                target = _text(status, 40).casefold().replace("-", "_")
                if target not in {item.value for item in JobStatus}:
                    raise VideoCollectionError("INVALID_JOB_STATUS", "unknown job status")
                if target != row.status and target not in self._JOB_TRANSITIONS.get(row.status, set()):
                    raise VideoCollectionError("INVALID_JOB_TRANSITION", f"cannot transition job from {row.status} to {target}")
                self._save_job_status(db, row, target, failure=failure)
            elif failure is not None:
                row.failure_json = _safe_json_object(failure, label="job failure")
            if progress is not None:
                row.progress_json = _safe_json_object(progress, label="job progress")
            if checkpoint is not None:
                row.checkpoint_json = _safe_json_object(checkpoint, label="job checkpoint")
            if result is not None:
                row.result_json = _safe_json_object(result, label="job result")
            row.updated_at = _now()
            db.commit()
            return _job_dict(row)

    def _save_job_status(self, db: Any, row: VideoCollectionJobORM, status: str, *, failure: Mapping[str, Any] | None = None) -> None:
        row.status = status
        row.updated_at = _now()
        if status == JobStatus.RUNNING.value and row.started_at is None:
            row.started_at = row.updated_at
            row.attempts = int(row.attempts or 0) + 1
        if status in {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_FAILURES.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
            row.finished_at = row.updated_at
        if failure is not None:
            row.failure_json = dict(failure)

    _JOB_TRANSITIONS: dict[str, set[str]] = {
        JobStatus.QUEUED.value: {JobStatus.RUNNING.value, JobStatus.PAUSED.value, JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELLED.value, JobStatus.FAILED.value},
        JobStatus.RUNNING.value: {JobStatus.PAUSED.value, JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELLED.value, JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_FAILURES.value, JobStatus.FAILED.value},
        JobStatus.PAUSED.value: {JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELLED.value},
        JobStatus.CANCEL_REQUESTED.value: {JobStatus.CANCELLED.value, JobStatus.QUEUED.value},
        JobStatus.CANCELLED.value: {JobStatus.QUEUED.value},
        JobStatus.COMPLETED.value: {JobStatus.QUEUED.value},
        JobStatus.COMPLETED_WITH_FAILURES.value: {JobStatus.QUEUED.value},
        JobStatus.FAILED.value: {JobStatus.QUEUED.value},
    }

    def transition_job(self, job_id: str, status: str) -> dict[str, Any]:
        target = _text(status, 40).casefold().replace("-", "_")
        if target not in {item.value for item in JobStatus}:
            raise VideoCollectionError("INVALID_JOB_STATUS", "unknown job status")
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            row = self._job(db, job_id)
            current = row.status
            if target == current:
                return _job_dict(row)
            if target not in self._JOB_TRANSITIONS.get(current, set()):
                raise VideoCollectionError("INVALID_JOB_TRANSITION", f"cannot transition job from {current} to {target}")
            self._save_job_status(db, row, target)
            if target == JobStatus.PAUSED.value:
                for episode in db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == row.collection_id, VideoEpisodeORM.status == EpisodeStatus.PROCESSING.value)):
                    episode.status = EpisodeStatus.UNPROCESSED.value
                    episode.retryable = True
                    episode.processing_token = ""
                    episode.failure_json = {"code": "JOB_PAUSED"}
                    episode.updated_at = _now()
            elif target == JobStatus.CANCELLED.value:
                for episode in db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == row.collection_id, VideoEpisodeORM.status == EpisodeStatus.PROCESSING.value)):
                    episode.status = EpisodeStatus.CANCELLED.value
                    episode.retryable = False
                    episode.processing_token = ""
                    episode.failure_json = {"code": "JOB_CANCELLED"}
                    episode.updated_at = _now()
            db.commit()
            return _job_dict(row)

    def pause_job(self, job_id: str) -> dict[str, Any]:
        return self.transition_job(job_id, JobStatus.PAUSED.value)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        return self.transition_job(job_id, JobStatus.QUEUED.value)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            row = self._job(db, job_id)
            if row.status == JobStatus.CANCELLED.value:
                return _job_dict(row)
            if row.status in {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_FAILURES.value, JobStatus.FAILED.value}:
                raise VideoCollectionError("INVALID_JOB_TRANSITION", "terminal job cannot be cancelled")
            # Cancellation is terminal; cancel_request is kept as an explicit
            # transition for callers that want to display a two-phase UI.
            self._save_job_status(db, row, JobStatus.CANCELLED.value, failure={"code": "JOB_CANCELLED"})
            for episode in db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == row.collection_id, VideoEpisodeORM.status == EpisodeStatus.PROCESSING.value)):
                episode.status = EpisodeStatus.CANCELLED.value
                episode.retryable = False
                episode.processing_token = ""
                episode.failure_json = {"code": "JOB_CANCELLED"}
                episode.updated_at = _now()
            db.commit()
            return _job_dict(row)

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        return self.transition_job(job_id, JobStatus.CANCEL_REQUESTED.value)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            row = self._job(db, job_id)
            if row.status not in {JobStatus.FAILED.value, JobStatus.COMPLETED_WITH_FAILURES.value, JobStatus.CANCELLED.value, JobStatus.PAUSED.value}:
                if row.status == JobStatus.QUEUED.value:
                    return _job_dict(row)
                raise VideoCollectionError("JOB_NOT_RETRYABLE", "job is not in a retryable state")
            self._save_job_status(db, row, JobStatus.QUEUED.value, failure={})
            row.finished_at = None
            for episode in db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == row.collection_id, VideoEpisodeORM.status.in_([EpisodeStatus.FAILED.value, EpisodeStatus.NEEDS_REVIEW.value, EpisodeStatus.SKIPPED.value, EpisodeStatus.CANCELLED.value]))):
                if episode.selected and episode.active:
                    episode.status = EpisodeStatus.UNPROCESSED.value
                    episode.retryable = True
                    episode.processing_token = ""
                    episode.failure_json = {}
                    episode.updated_at = _now()
            db.commit()
            return _job_dict(row)

    def claim_next_episode(self, job_id: str, *, worker_id: str | None = None) -> dict[str, Any] | None:
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            job = self._job(db, job_id)
            if job.status in {JobStatus.PAUSED.value, JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELLED.value, JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_FAILURES.value, JobStatus.FAILED.value}:
                return None
            # Respect the request's bounded concurrency without inheriting the
            # knowledge-map worker's larger pool.  The hard policy cap is two.
            max_parallel = _bounded_parallel((job.request_json or {}).get("max_parallel"))
            in_progress = int(db.scalar(select(func.count(VideoEpisodeORM.id)).where(VideoEpisodeORM.collection_id == job.collection_id, VideoEpisodeORM.status == EpisodeStatus.PROCESSING.value)) or 0)
            if in_progress >= max_parallel:
                return None
            if job.status == JobStatus.QUEUED.value:
                self._save_job_status(db, job, JobStatus.RUNNING.value)
            candidate = db.scalar(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == job.collection_id, VideoEpisodeORM.active.is_(True), VideoEpisodeORM.selected.is_(True), VideoEpisodeORM.status.in_([EpisodeStatus.UNPROCESSED.value, EpisodeStatus.FAILED.value]), VideoEpisodeORM.retryable.is_(True)).order_by(VideoEpisodeORM.ordinal, VideoEpisodeORM.id).limit(1))
            if candidate is None:
                db.commit()
                return None
            token = _text(worker_id, 96) or secrets.token_hex(20)
            result = db.execute(
                update(VideoEpisodeORM)
                .where(VideoEpisodeORM.id == candidate.id, VideoEpisodeORM.status.in_([EpisodeStatus.UNPROCESSED.value, EpisodeStatus.FAILED.value]), VideoEpisodeORM.retryable.is_(True), VideoEpisodeORM.processing_token == "")
                .values(status=EpisodeStatus.PROCESSING.value, processing_token=token, processing_started_at=_now(), attempts=int(candidate.attempts or 0) + 1, updated_at=_now())
            )
            if result.rowcount != 1:
                db.rollback()
                return None
            db.refresh(candidate)
            job.progress_json = {**dict(job.progress_json or {}), "current_episode_id": candidate.id, "current_episode_stable_id": candidate.stable_id}
            job.checkpoint_json = {"episode_id": candidate.id, "episode_checkpoint": dict(candidate.checkpoint_json or {})}
            job.updated_at = _now()
            db.commit()
            return _episode_dict(candidate)

    claim_episode = claim_next_episode
    claim = claim_next_episode

    def checkpoint_episode(
        self,
        job_id: str,
        episode_id: str,
        checkpoint: Mapping[str, Any] | None = None,
        *,
        status: str | None = None,
        failure: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_status = _text(status, 32).casefold() if status else EpisodeStatus.PROCESSING.value
        allowed = {item.value for item in EpisodeStatus}
        if target_status not in allowed:
            raise VideoCollectionError("INVALID_EPISODE_STATUS", "unknown episode status")
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            job = self._job(db, job_id)
            episode = self._episode(db, episode_id)
            if episode.collection_id != job.collection_id:
                raise VideoCollectionError("EPISODE_JOB_MISMATCH", "episode does not belong to job")
            if episode.status != EpisodeStatus.PROCESSING.value and target_status == EpisodeStatus.PROCESSING.value:
                raise VideoCollectionError("EPISODE_NOT_CLAIMED", "episode is not currently claimed")
            episode.checkpoint_json = dict(checkpoint or episode.checkpoint_json or {})
            if failure is not None:
                episode.failure_json = dict(failure)
            if target_status != EpisodeStatus.PROCESSING.value:
                episode.status = target_status
                episode.processing_token = ""
                episode.processing_started_at = None
                episode.retryable = target_status in {EpisodeStatus.UNPROCESSED.value, EpisodeStatus.FAILED.value, EpisodeStatus.NEEDS_REVIEW.value}
                if target_status == EpisodeStatus.READY.value:
                    episode.failure_json = {}
                    episode.retryable = False
            episode.updated_at = _now()
            job.checkpoint_json = {"episode_id": episode.id, "episode_checkpoint": dict(episode.checkpoint_json or {})}
            job.progress_json = self._progress_for_collection(db, job.collection_id, current_episode_id=(episode.id if episode.status == EpisodeStatus.PROCESSING.value else ""))
            job.updated_at = _now()
            db.commit()
            return _episode_dict(episode)

    checkpoint = checkpoint_episode

    def _progress_for_collection(self, db: Any, collection_id: str, *, current_episode_id: str = "") -> dict[str, Any]:
        rows = list(db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == collection_id, VideoEpisodeORM.active.is_(True), VideoEpisodeORM.selected.is_(True))))
        counts = {status.value: 0 for status in EpisodeStatus}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {
            "selected_total": len(rows),
            "ready": counts.get(EpisodeStatus.READY.value, 0),
            "needs_review": counts.get(EpisodeStatus.NEEDS_REVIEW.value, 0),
            "failed": counts.get(EpisodeStatus.FAILED.value, 0),
            "skipped": counts.get(EpisodeStatus.SKIPPED.value, 0),
            "cancelled": counts.get(EpisodeStatus.CANCELLED.value, 0),
            "processing": counts.get(EpisodeStatus.PROCESSING.value, 0),
            "unprocessed": counts.get(EpisodeStatus.UNPROCESSED.value, 0),
            "current_episode_id": current_episode_id,
        }

    def coverage(self, collection_id: str, *, job_id: str | None = None) -> dict[str, Any]:
        with self.session_factory() as db:
            collection = self._collection(db, collection_id)
            progress = self._progress_for_collection(db, collection.id)
            denominator = int(progress["selected_total"])
            ready = int(progress["ready"])
            result = {
                "collection_id": collection.id,
                "job_id": _text(job_id, 96) if job_id else None,
                "denominator": denominator,
                "selected_manifest_episodes": denominator,
                "ready": ready,
                "needs_review": int(progress["needs_review"]),
                "failed": int(progress["failed"]),
                "skipped": int(progress["skipped"]),
                "cancelled": int(progress["cancelled"]),
                "processing": int(progress["processing"]),
                "unprocessed": int(progress["unprocessed"]),
                "covered": ready,
                "coverage_ratio": (ready / denominator) if denominator else 0.0,
                "status_counts": {key: int(value) for key, value in progress.items() if key in {"ready", "needs_review", "failed", "skipped", "cancelled", "processing", "unprocessed"}},
            }
            result["complete"] = denominator > 0 and denominator == ready
            if denominator == 0:
                result["warning"] = "no_selected_episodes"
            return result

    get_coverage = coverage
    coverage_summary = coverage

    def finalize_job(self, job_id: str) -> dict[str, Any]:
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            job = self._job(db, job_id)
            if job.status in {JobStatus.PAUSED.value, JobStatus.CANCELLED.value}:
                return _job_dict(job)
            collection = self._collection(db, job.collection_id)
            progress = self._progress_for_collection(db, collection.id)
            all_ready = int(progress["selected_total"]) > 0 and int(progress["selected_total"]) == int(progress["ready"])
            target = JobStatus.COMPLETED.value if all_ready else JobStatus.COMPLETED_WITH_FAILURES.value
            if job.status not in {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_FAILURES.value}:
                self._save_job_status(db, job, target, failure={} if all_ready else {"code": "EPISODE_FAILURES", "coverage": progress})
            job.progress_json = progress
            job.result_json = {"coverage": progress, "status": target}
            db.commit()
            return _job_dict(job)

    complete_job = finalize_job

    def recover_interrupted(self) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            processing = list(db.scalars(select(VideoEpisodeORM).where(VideoEpisodeORM.status == EpisodeStatus.PROCESSING.value)))
            for episode in processing:
                episode.status = EpisodeStatus.UNPROCESSED.value
                episode.retryable = True
                episode.processing_token = ""
                episode.processing_started_at = None
                episode.failure_json = {"code": ERR_JOB_INTERRUPTED, "retryable": True}
                episode.updated_at = _now()
            jobs = list(db.scalars(select(VideoCollectionJobORM).where(VideoCollectionJobORM.status.in_([JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value]))))
            for job in jobs:
                self._save_job_status(db, job, JobStatus.QUEUED.value, failure={"code": ERR_JOB_INTERRUPTED, "retryable": True})
                job.finished_at = None
                job.progress_json = self._progress_for_collection(db, job.collection_id)
                recovered.append(_job_dict(job))
            db.commit()
        return recovered

    recover = recover_interrupted
    recover_interrupted_jobs = recover_interrupted

    def insert_segments(
        self,
        episode_id: str,
        segments: Iterable[Mapping[str, Any]],
        *,
        job_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        raw_segments = list(segments or [])
        if len(raw_segments) > MAX_SUBTITLE_SEGMENTS_PER_EPISODE:
            raise BudgetExceededError("episode subtitle segment limit exceeded")
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            episode = self._episode(db, episode_id)
            if job_id:
                job = self._job(db, job_id)
                if job.collection_id != episode.collection_id:
                    raise VideoCollectionError("EPISODE_JOB_MISMATCH", "episode does not belong to job")
            normalised: list[dict[str, Any]] = []
            seen_ordinals: set[int] = set()
            for raw in raw_segments:
                if not isinstance(raw, Mapping):
                    raise VideoCollectionError("INVALID_SEGMENT", "subtitle segments must be objects")
                ordinal_raw = raw.get("ordinal", raw.get("index"))
                try:
                    ordinal = int(ordinal_raw)
                except (TypeError, ValueError, OverflowError):
                    raise VideoCollectionError("INVALID_SEGMENT", "segment ordinal must be an integer") from None
                if ordinal < 0:
                    raise VideoCollectionError("INVALID_SEGMENT", "segment ordinal cannot be negative")
                if ordinal in seen_ordinals:
                    raise VideoCollectionError("DUPLICATE_SEGMENT_ORDINAL", "request contains duplicate segment ordinal")
                seen_ordinals.add(ordinal)
                start_raw = raw.get("start_ms", raw.get("start", raw.get("start_seconds", 0)))
                end_raw = raw.get("end_ms", raw.get("end", raw.get("end_seconds", 0)))
                try:
                    start_ms = int(round(float(start_raw) * (1000 if "start_ms" not in raw else 1)))
                    end_ms = int(round(float(end_raw) * (1000 if "end_ms" not in raw else 1)))
                except (TypeError, ValueError, OverflowError):
                    raise VideoCollectionError("INVALID_SEGMENT", "segment start/end must be numeric") from None
                if start_ms < 0 or end_ms < start_ms:
                    raise VideoCollectionError("INVALID_SEGMENT_TIME", "segment end must be >= start and non-negative")
                if end_ms > MAX_EPISODE_DURATION_SECONDS * 1000:
                    raise BudgetExceededError("episode subtitle duration limit exceeded")
                text_value = str(raw.get("text", raw.get("content", "")) or "")
                byte_size = len(text_value.encode("utf-8"))
                quote_hash = _text(raw.get("quote_hash"), 128) or _hash_text(text_value)
                input_fp = _text(raw.get("input_fingerprint"), 128) or _sha({"episode_id": episode.id, "ordinal": ordinal, "start_ms": start_ms, "end_ms": end_ms, "text": text_value, "quote_hash": quote_hash})
                normalised.append({"ordinal": ordinal, "start_ms": start_ms, "end_ms": end_ms, "text": text_value, "quote_hash": quote_hash, "input_fingerprint": input_fp, "byte_size": byte_size})
            existing_rows = {row.ordinal: row for row in db.scalars(select(VideoSubtitleSegmentORM).where(VideoSubtitleSegmentORM.episode_id == episode.id))}
            existing_fp = {row.input_fingerprint: row for row in existing_rows.values()}
            new_rows: list[dict[str, Any]] = []
            for item in normalised:
                existing = existing_rows.get(item["ordinal"])
                if existing is not None:
                    if existing.input_fingerprint != item["input_fingerprint"]:
                        raise IdempotencyConflictError("segment ordinal already contains a different fingerprint")
                    continue
                by_fp = existing_fp.get(item["input_fingerprint"])
                if by_fp is not None:
                    continue
                new_rows.append(item)
            existing_count = len(existing_rows)
            if existing_count + len(new_rows) > MAX_SUBTITLE_SEGMENTS_PER_EPISODE:
                raise BudgetExceededError("episode subtitle segment limit exceeded")
            existing_bytes = int(db.scalar(select(func.coalesce(func.sum(VideoSubtitleSegmentORM.byte_size), 0)).where(VideoSubtitleSegmentORM.episode_id == episode.id)) or 0)
            added_bytes = sum(item["byte_size"] for item in new_rows)
            if existing_bytes + added_bytes > MAX_SUBTITLE_BYTES_PER_EPISODE:
                raise BudgetExceededError("episode subtitle byte limit exceeded")
            if max([item["end_ms"] for item in normalised] or [0]) > MAX_EPISODE_DURATION_SECONDS * 1000:
                raise BudgetExceededError("episode subtitle duration limit exceeded")
            collection_episode_ids = select(VideoEpisodeORM.id).where(VideoEpisodeORM.collection_id == episode.collection_id)
            total_segments = int(db.scalar(select(func.count(VideoSubtitleSegmentORM.id)).where(VideoSubtitleSegmentORM.episode_id.in_(collection_episode_ids))) or 0)
            total_bytes = int(db.scalar(select(func.coalesce(func.sum(VideoSubtitleSegmentORM.byte_size), 0)).where(VideoSubtitleSegmentORM.episode_id.in_(collection_episode_ids))) or 0)
            if total_segments + len(new_rows) > MAX_TOTAL_SUBTITLE_SEGMENTS:
                raise BudgetExceededError("collection subtitle segment budget exceeded")
            if total_bytes + added_bytes > MAX_TOTAL_SUBTITLE_BYTES:
                raise BudgetExceededError("collection subtitle byte budget exceeded")
            all_durations = list(db.scalars(select(VideoEpisodeORM.duration_seconds).where(VideoEpisodeORM.collection_id == episode.collection_id, VideoEpisodeORM.active.is_(True))))
            total_duration = sum(float(value or 0) for value in all_durations)
            # A manifest with omitted duration still receives a truthful bound
            # from the last segment timestamp after insertion.
            if not episode.duration_seconds:
                total_duration += max([item["end_ms"] for item in new_rows] or [0]) / 1000
            if total_duration > MAX_TOTAL_DURATION_SECONDS:
                raise BudgetExceededError("collection subtitle duration budget exceeded")
            for item in new_rows:
                db.add(VideoSubtitleSegmentORM(id="vs-" + _hash_text(episode.id + ":" + item["input_fingerprint"])[:40], episode_id=episode.id, **item, created_at=_now()))
            if new_rows:
                episode.checkpoint_json = {**dict(episode.checkpoint_json or {}), "segment_count": existing_count + len(new_rows), "subtitle_bytes": existing_bytes + added_bytes}
                episode.updated_at = _now()
            db.commit()
            rows = list(db.scalars(select(VideoSubtitleSegmentORM).where(VideoSubtitleSegmentORM.episode_id == episode.id).order_by(VideoSubtitleSegmentORM.ordinal)))
            return {"episode_id": episode.id, "inserted_count": len(new_rows), "segment_count": len(rows), "subtitle_bytes": int(sum(row.byte_size or 0 for row in rows)), "segments": [_segment_dict(row) for row in rows]}

    add_segments = insert_segments
    upsert_segments = insert_segments
    insert_subtitle_segments = insert_segments

    def episode_detail(
        self,
        collection_id: str,
        episode_id: str,
        *,
        segment_offset: int = 0,
        segment_limit: int = 8,
    ) -> dict[str, Any]:
        try:
            offset = max(0, int(segment_offset))
            limit = max(1, min(100, int(segment_limit)))
        except (TypeError, ValueError, OverflowError):
            raise VideoCollectionError("INVALID_SEGMENT_PAGE", "segment page must be numeric") from None
        with self.session_factory() as db:
            episode = self._episode(db, episode_id)
            if episode.collection_id != _text(collection_id, 96):
                raise VideoCollectionError("EPISODE_COLLECTION_MISMATCH", "episode does not belong to collection", status=404)
            query = select(VideoSubtitleSegmentORM).where(VideoSubtitleSegmentORM.episode_id == episode.id).order_by(VideoSubtitleSegmentORM.ordinal)
            total = int(db.scalar(select(func.count(VideoSubtitleSegmentORM.id)).where(VideoSubtitleSegmentORM.episode_id == episode.id)) or 0)
            rows = list(db.scalars(query.offset(offset).limit(limit)))
            return {
                "episode": _episode_dict(episode),
                "segment_page": {
                    "offset": offset,
                    "limit": limit,
                    "total": total,
                    "items": [_segment_dict(row) for row in rows],
                    "has_more": offset + len(rows) < total,
                },
            }

    get_episode_detail = episode_detail

    def episode_segments_for_summary(self, collection_id: str, episode_id: str) -> list[dict[str, Any]]:
        """Internal bounded summary input; never returned by collection list APIs."""

        with self.session_factory() as db:
            episode = self._episode(db, episode_id)
            if episode.collection_id != _text(collection_id, 96):
                raise VideoCollectionError("EPISODE_COLLECTION_MISMATCH", "episode does not belong to collection", status=404)
            rows = list(db.scalars(select(VideoSubtitleSegmentORM).where(VideoSubtitleSegmentORM.episode_id == episode.id).order_by(VideoSubtitleSegmentORM.ordinal)))
            return [_segment_dict(row) for row in rows]

    def selected_episodes(self, collection_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        with self.session_factory() as db:
            self._collection(db, collection_id)
            query = select(VideoEpisodeORM).where(VideoEpisodeORM.collection_id == _text(collection_id, 96), VideoEpisodeORM.active.is_(True), VideoEpisodeORM.selected.is_(True))
            if status:
                query = query.where(VideoEpisodeORM.status == _text(status, 32))
            rows = list(db.scalars(query.order_by(VideoEpisodeORM.ordinal, VideoEpisodeORM.id)))
            return [_episode_dict(row) for row in rows]

    def store_summary(
        self,
        collection_id: str,
        *,
        scope_type: str = "episode",
        scope_id: str = "",
        episode_id: str | None = None,
        batch_key: str | None = None,
        input_fingerprint: str | None = None,
        provider_fingerprint: str = "",
        summary_text: str = "",
        summary_json: Mapping[str, Any] | None = None,
        evidence: Iterable[Mapping[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        scope = _text(scope_type, 24).casefold() or SummaryScope.EPISODE.value
        if scope not in {item.value for item in SummaryScope}:
            raise VideoCollectionError("INVALID_SUMMARY_SCOPE", "summary scope must be episode, batch or collection")
        scope_id = _text(scope_id or episode_id or batch_key, 180)
        provider_fp = _text(provider_fingerprint, 128)
        summary_payload = dict(summary_json or {})
        evidence_refs = [dict(item) for item in (evidence or []) if isinstance(item, Mapping)]
        input_fp = _text(input_fingerprint, 128) or _sha({"scope_type": scope, "scope_id": scope_id, "summary": summary_payload, "summary_text": _text(summary_text, 10000), "evidence": evidence_refs})
        with _VIDEO_WRITE_LOCK, self.session_factory() as db:
            collection = self._collection(db, collection_id)
            if scope == SummaryScope.EPISODE.value:
                if not episode_id:
                    episode_id = scope_id
                episode = self._episode(db, episode_id)
                if episode.collection_id != collection.id:
                    raise EvidenceValidationError("episode summary belongs to another collection")
                scope_id = scope_id or episode.id
            elif scope == SummaryScope.COLLECTION.value:
                episode_id = None
            identity_query = select(VideoSummaryORM).where(VideoSummaryORM.collection_id == collection.id, VideoSummaryORM.scope_type == scope, VideoSummaryORM.scope_id == scope_id, VideoSummaryORM.input_fingerprint == input_fp, VideoSummaryORM.provider_fingerprint == provider_fp)
            existing = db.scalar(identity_query)
            if existing is not None:
                return _summary_dict(existing)
            self._validate_evidence(db, collection.id, scope, scope_id, episode_id, evidence_refs)
            if scope == SummaryScope.COLLECTION.value and self._contains_raw_transcript(db, collection.id, _text(summary_text, 20000), summary_payload):
                raise EvidenceValidationError("collection summary must not contain raw transcript text")
            row = VideoSummaryORM(
                id="vsum-" + _hash_text(collection.id + ":" + scope + ":" + scope_id + ":" + input_fp + ":" + provider_fp)[:40],
                collection_id=collection.id,
                episode_id=episode_id,
                scope_type=scope,
                scope_id=scope_id,
                batch_key=_text(batch_key, 180),
                input_fingerprint=input_fp,
                provider_fingerprint=provider_fp,
                summary_text=_text(summary_text, 20000),
                summary_json=summary_payload,
                evidence_json=evidence_refs,
                status="ready",
                created_at=_now(),
            )
            db.add(row)
            db.commit()
            return _summary_dict(row)

    save_summary = store_summary
    upsert_summary = store_summary

    def summary_page(
        self,
        collection_id: str,
        *,
        scope_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
        include_stale: bool = False,
    ) -> dict[str, Any]:
        try:
            page_offset = max(0, int(offset))
            page_limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError, OverflowError):
            raise VideoCollectionError("INVALID_SUMMARY_PAGE", "summary page must be numeric") from None
        with self.session_factory() as db:
            self._collection(db, collection_id)
            filters = [VideoSummaryORM.collection_id == _text(collection_id, 96)]
            if scope_type:
                scope = _text(scope_type, 24).casefold()
                if scope not in {item.value for item in SummaryScope}:
                    raise VideoCollectionError("INVALID_SUMMARY_SCOPE", "summary scope must be episode, batch or collection")
                filters.append(VideoSummaryORM.scope_type == scope)
            if not include_stale:
                filters.append(VideoSummaryORM.status == "ready")
            total = int(db.scalar(select(func.count(VideoSummaryORM.id)).where(*filters)) or 0)
            rows = list(db.scalars(select(VideoSummaryORM).where(*filters).order_by(VideoSummaryORM.created_at, VideoSummaryORM.id).offset(page_offset).limit(page_limit)))
            return {
                "collection_id": _text(collection_id, 96),
                "scope_type": scope_type,
                "offset": page_offset,
                "limit": page_limit,
                "total": total,
                "items": [_summary_dict(row) for row in rows],
                "has_more": page_offset + len(rows) < total,
            }

    list_summaries = summary_page

    def _validate_evidence(self, db: Any, collection_id: str, scope: str, scope_id: str, episode_id: str | None, evidence: list[dict[str, Any]]) -> None:
        if not evidence:
            raise EvidenceValidationError("summary requires at least one evidence reference")
        for ref in evidence:
            segment_id = _text(ref.get("segment_id") or ref.get("subtitle_segment_id") or (ref.get("id") if ref.get("kind") == "segment" else ""), 96)
            summary_id = _text(ref.get("summary_id") or (ref.get("id") if ref.get("kind") == "summary" else ""), 96)
            if segment_id:
                segment = db.get(VideoSubtitleSegmentORM, segment_id)
                if segment is None:
                    raise EvidenceValidationError("unknown subtitle segment evidence reference")
                owner = db.get(VideoEpisodeORM, segment.episode_id)
                if owner is None or owner.collection_id != collection_id:
                    raise EvidenceValidationError("subtitle segment evidence belongs to another collection")
                if episode_id and owner.id != episode_id:
                    raise EvidenceValidationError("episode summary evidence belongs to another episode")
                for key, actual in (("start_ms", segment.start_ms), ("end_ms", segment.end_ms), ("quote_hash", segment.quote_hash)):
                    if key in ref and str(ref[key]) != str(actual):
                        raise EvidenceValidationError(f"subtitle segment {key} does not match stored evidence")
                continue
            if summary_id:
                lower = db.get(VideoSummaryORM, summary_id)
                if lower is None or lower.collection_id != collection_id:
                    raise EvidenceValidationError("unknown lower-summary evidence reference")
                if lower.id == scope_id:
                    raise EvidenceValidationError("summary cannot cite itself")
                if scope == SummaryScope.COLLECTION.value and lower.scope_type == SummaryScope.COLLECTION.value:
                    raise EvidenceValidationError("collection summary may cite only episode or batch summaries")
                if scope == SummaryScope.EPISODE.value and lower.episode_id and lower.episode_id != episode_id:
                    raise EvidenceValidationError("episode summary cannot cite another episode summary")
                continue
            raise EvidenceValidationError("evidence reference must identify a stored segment or lower summary")

    def _contains_raw_transcript(self, db: Any, collection_id: str, text: str, payload: Mapping[str, Any]) -> bool:
        if not text and not payload:
            return False

        def contains_raw_field(value: Any) -> bool:
            if isinstance(value, Mapping):
                for raw_key, child in value.items():
                    key = str(raw_key).casefold().replace("-", "_")
                    if key in {"transcript", "raw_transcript", "subtitle_text", "full_text"} and len(str(child or "")) > 256:
                        return True
                    if contains_raw_field(child):
                        return True
            elif isinstance(value, (list, tuple)):
                return any(contains_raw_field(child) for child in value)
            return False

        if contains_raw_field(payload):
            return True
        candidate = text + " " + _canonical_json(payload)
        episode_ids = select(VideoEpisodeORM.id).where(VideoEpisodeORM.collection_id == collection_id)
        matched = 0
        matched_chars = 0
        total_chars = 0
        for segment_text in db.scalars(select(VideoSubtitleSegmentORM.text).where(VideoSubtitleSegmentORM.episode_id.in_(episode_ids))):
            value = str(segment_text or "").strip()
            if not value:
                continue
            total_chars += len(value)
            if len(value) >= 32 and value in candidate:
                matched += 1
                matched_chars += len(value)
        # A few timestamped quotations are legitimate evidence.  Reject only
        # payloads that reproduce a material fraction of the stored corpus.
        return matched >= 8 and total_chars > 0 and matched_chars / total_chars >= 0.5


def _segment_dict(row: VideoSubtitleSegmentORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "segment_id": row.id,
        "episode_id": row.episode_id,
        "ordinal": int(row.ordinal or 0),
        "start_ms": int(row.start_ms or 0),
        "end_ms": int(row.end_ms or 0),
        "start": float(row.start_ms or 0) / 1000,
        "end": float(row.end_ms or 0) / 1000,
        "text": row.text,
        "quote_hash": row.quote_hash,
        "input_fingerprint": row.input_fingerprint,
        "byte_size": int(row.byte_size or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# Domain-noun aliases make the migration/service contract discoverable to
# later API code without requiring callers to know the historical table prefix.
CollectionORM = VideoCollectionORM
EpisodeORM = VideoEpisodeORM
SubtitleSegmentORM = VideoSubtitleSegmentORM
JobORM = VideoCollectionJobORM
SummaryORM = VideoSummaryORM
VideoJobORM = VideoCollectionJobORM


def get_video_collection_service() -> VideoCollectionService:
    return VideoCollectionService()


__all__ = [
    "MAX_EPISODES", "DEFAULT_MANIFEST_PAGE_SIZE", "MAX_MANIFEST_PAGE_SIZE",
    "DEFAULT_MAX_PARALLEL", "HARD_MAX_PARALLEL", "MAX_SUBTITLE_BYTES_PER_EPISODE",
    "MAX_SUBTITLE_SEGMENTS_PER_EPISODE", "MAX_EPISODE_DURATION_SECONDS",
    "MAX_TOTAL_SUBTITLE_BYTES", "MAX_TOTAL_DURATION_SECONDS", "MAX_TOTAL_SUBTITLE_SEGMENTS",
    "DEFAULT_SUMMARY_BATCH_SIZE", "MIN_SUMMARY_BATCH_SIZE", "MAX_SUMMARY_BATCH_SIZE",
    "MAX_SUBTITLE_BYTES", "MAX_SEGMENTS_PER_EPISODE", "MAX_TOTAL_SEGMENTS",
    "CollectionStatus", "EpisodeStatus", "JobStatus", "SummaryScope",
    "VideoCollectionError", "StaleCursorError", "IdempotencyConflictError", "BudgetExceededError", "EvidenceValidationError",
    "ERR_MANIFEST_LIMIT", "ERR_STALE_CURSOR", "ERR_JOB_INTERRUPTED", "ERR_BUDGET_EXCEEDED", "ERR_EVIDENCE_INVALID", "ERR_IDEMPOTENCY_CONFLICT",
    "VideoCollectionService", "get_video_collection_service", "canonical_url_hash",
    "CollectionORM", "EpisodeORM", "SubtitleSegmentORM", "JobORM", "SummaryORM", "VideoJobORM",
]
