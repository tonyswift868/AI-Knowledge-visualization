"""Atomic, caller-rooted JSON persistence for review memory state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import REVIEW_MEMORY_SCHEMA_VERSION, ReviewMemoryState


class ReviewMemoryStoreError(RuntimeError):
    pass


class ReviewMemoryStoreCorruptionError(ReviewMemoryStoreError):
    pass


class ReviewMemoryVersionConflictError(ReviewMemoryStoreError):
    pass


_LOCK_GUARD = threading.Lock()
_LOCKS: dict[tuple[str, str], threading.RLock] = {}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class AtomicJsonReviewMemoryStore:
    def __init__(self, root: str | Path) -> None:
        candidate = Path(root)
        if not candidate.is_absolute():
            raise ValueError("store root must be an absolute caller-provided path")
        self.root = candidate.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _lock(self, student_id: str) -> threading.RLock:
        digest = hashlib.sha256(student_id.strip().encode("utf-8")).hexdigest()
        key = (str(self.root).casefold(), digest)
        with _LOCK_GUARD:
            return _LOCKS.setdefault(key, threading.RLock())

    def state_path(self, student_id: str) -> Path:
        normalized = student_id.strip()
        if not normalized:
            raise ValueError("student_id must be non-empty")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self.root / "students" / digest[:2] / f"{digest}.json"

    def _before_replace(self, temporary_path: Path, target_path: Path) -> None:
        """Crash injection seam for tests."""

    def _write(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_canonical_bytes(payload) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._before_replace(temporary, path)
            os.replace(temporary, path)
            try:
                directory_descriptor = os.open(path.parent, os.O_RDONLY)
            except OSError:
                directory_descriptor = None
            if directory_descriptor is not None:
                try:
                    os.fsync(directory_descriptor)
                except OSError:
                    pass
                finally:
                    os.close(directory_descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, student_id: str) -> ReviewMemoryState | None:
        normalized = student_id.strip()
        path = self.state_path(normalized)
        with self._lock(normalized):
            if not path.exists():
                return None
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(envelope, dict):
                    raise TypeError("review memory envelope must be an object")
                state_data = envelope["state"]
                expected_checksum = envelope["checksum"]
                actual_checksum = hashlib.sha256(_canonical_bytes(state_data)).hexdigest()
                if actual_checksum != expected_checksum:
                    raise ValueError("checksum mismatch")
                state = ReviewMemoryState.model_validate(state_data)
                if state.schema_version != REVIEW_MEMORY_SCHEMA_VERSION:
                    raise ValueError("unsupported review memory schema")
                if state.student_id != normalized:
                    raise ValueError("stored student identity mismatch")
                return state
            except Exception as exc:
                raise ReviewMemoryStoreCorruptionError(
                    f"cannot load review memory {path}: {exc}"
                ) from exc

    def save(
        self,
        state: ReviewMemoryState,
        *,
        expected_revision: int | None = None,
    ) -> None:
        path = self.state_path(state.student_id)
        with self._lock(state.student_id):
            current = self.load(state.student_id)
            current_revision = 0 if current is None else current.revision
            if expected_revision is not None and current_revision != expected_revision:
                raise ReviewMemoryVersionConflictError(
                    f"expected revision {expected_revision}, found {current_revision}"
                )
            state_data = state.model_dump(mode="json")
            payload = {
                "schema_version": REVIEW_MEMORY_SCHEMA_VERSION,
                "state": state_data,
                "checksum": hashlib.sha256(_canonical_bytes(state_data)).hexdigest(),
                "written_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(path, payload)


__all__ = [
    "AtomicJsonReviewMemoryStore",
    "ReviewMemoryStoreCorruptionError",
    "ReviewMemoryStoreError",
    "ReviewMemoryVersionConflictError",
]
