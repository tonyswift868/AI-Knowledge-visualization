"""Runtime contracts extracted from refactor/learning/plan_export.py."""
from __future__ import annotations

from dataclasses import dataclass

from enum import Enum

from pathlib import Path

from threading import Event, RLock

class ExportStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    MISSING_ORIGINALS = "MISSING_ORIGINALS"
    NO_CURRENT_PLAN = "NO_CURRENT_PLAN"

@dataclass(frozen=True)
class ExportResult:
    status: ExportStatus
    code: str
    message: str
    output_path: Path | None = None
    plan_id: str = ""
    account_id: str = ""
    missing: tuple[str, ...] = ()

    @property
    def succeeded(self):
        return self.status is ExportStatus.SUCCEEDED

class ExportCancellation(Event):
    """Serialize UI cancellation with atomic publication, preserving its result.

    The lock covers only the final rename and result recording, never copying,
    hashing or network requests. A close arriving after publication reports the
    saved artifact instead of incorrectly calling it cancelled.
    """
    def __init__(self):
        super().__init__()
        self.publication_lock = RLock()
        self.published_result = None

    def set(self):
        with self.publication_lock:
            super().set()
